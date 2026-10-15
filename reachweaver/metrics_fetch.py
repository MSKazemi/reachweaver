"""Read-only polling of PUBLIC Bluesky and Mastodon post counters (issue #15 slice).

This is not private analytics. It reads the same public counters any visitor can
see and stores them as metric snapshots, so ``report`` and the experiment
readouts can use first-party numbers instead of hand-copied ones.

* Bluesky: ``app.bsky.feed.getPosts`` (an XRPC *query*, HTTP GET) on the public,
  cached AppView ``https://public.api.bsky.app``. No credential is ever sent; the
  docs say these direct AppView endpoints "do not support authentication". Up to
  25 AT-URIs per request (lexicon ``uris`` maxLength 25). Every returned post's
  ``author.did`` must equal the DID in its AT-URI.
* Mastodon: ``GET /api/v1/statuses/:id`` on the account's configured server, first
  WITHOUT a token ("Public for public statuses" since 2.7.0). Only on 401
  (authorized-fetch mode) or 404 ("does not exist or is private"), and only when
  ``{env_prefix}_TOKEN`` is set, it is retried exactly once with that bearer token
  (a token with ``read:statuses`` is sufficient). The returned ``account`` must be
  the configured handle on the configured server.

Field mapping uses only fields the cited docs define. Anything else stays NULL
(unknown), never zero: neither API exposes views, impressions or link clicks.
Requests are sequential, capped per run, never redirected, and a provider 429
stops the run at once with no retry. Nothing here writes to any platform.

Sources (fetched and checked 2026-10-08):
  https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/getPosts.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/defs.json
  https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/api-directory.mdx
  https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/rate-limits.md
  https://docs.joinmastodon.org/methods/statuses/#get
  https://docs.joinmastodon.org/entities/Status/
  https://docs.joinmastodon.org/entities/Account/
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from . import publishers
from .accounts import credential
from .policy import ValidationError, require_https_public_server
from .storage import METRIC_FIELDS, store_metrics, utc_now

BLUESKY_APPVIEW = "https://public.api.bsky.app"
GET_POSTS = "/xrpc/app.bsky.feed.getPosts"
GET_POSTS_MAX_URIS = 25
DEFAULT_MAX_POSTS = 50
MAX_POSTS_LIMIT = 200
SOURCES = {"bluesky": "bluesky-public-api", "mastodon": "mastodon-api"}
# (storage column, native field). Only fields the cited docs define; saves only when present.
FIELD_MAP: dict[str, tuple[tuple[str, str], ...]] = {
    "bluesky": (("likes", "likeCount"), ("reposts", "repostCount"),
                ("replies", "replyCount"), ("saves", "bookmarkCount")),
    "mastodon": (("likes", "favourites_count"), ("reposts", "reblogs_count"),
                 ("replies", "replies_count")),
}
SOURCE_URLS = {
    "bluesky": ("https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/getPosts.json",
                "https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/defs.json"),
    "mastodon": ("https://docs.joinmastodon.org/methods/statuses/#get",
                 "https://docs.joinmastodon.org/entities/Status/"),
}
NOTE = ("READ-ONLY public counters (Bluesky public AppView, Mastodon public status API), not private "
        "analytics. views, impressions and clicks are not exposed by these APIs and stay unknown (NULL), "
        "never zero. Nothing was posted, liked or changed on any platform.")
_POST_URI = re.compile(r"at://(did:[a-z]+:[A-Za-z0-9._:%-]+)/app\.bsky\.feed\.post/([A-Za-z0-9._:~-]{1,512})")
_MASTODON_ID = re.compile(r"[A-Za-z0-9._~-]{1,128}")


class RateLimited(publishers.PublishError):
    """The provider answered HTTP 429. The run stops; nothing is retried."""


def _http_code(exc: publishers.PublishError) -> int | None:
    match = re.fullmatch(r"http_(\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _get(url: str, headers: dict[str, str] | None = None) -> dict:
    """Bounded, redirect-free, sanitized GET; a 429 becomes RateLimited."""
    try:
        return publishers.request_get_json(url, headers)
    except publishers.PublishError as exc:
        if _http_code(exc) == 429:
            raise RateLimited("http_429") from None
        raise


def _count(value: object) -> int | None:
    """A documented counter is a nonnegative int; anything else (missing, bool, str) is unknown."""
    return value if type(value) is int and value >= 0 else None


def map_counts(platform: str, data: dict) -> dict[str, int | None]:
    """All storage metric columns; only documented native fields are filled, the rest are None."""
    values: dict[str, int | None] = {field: None for field in METRIC_FIELDS}
    for column, native in FIELD_MAP[platform]:
        values[column] = _count(data.get(native))
    return values


def _mastodon_local_handle(account: dict) -> str:
    handle = str(account.get("handle") or "").strip().lstrip("@").casefold()
    instance = (urlparse(account["server"]).hostname or "").casefold()
    parts = handle.split("@")
    if not handle or len(parts) > 2 or not parts[0] or (len(parts) == 2 and parts[1] != instance):
        raise ValidationError("mastodon_expected_handle_must_be_local_to_configured_server")
    return parts[0]


def _request_plan(account: dict, remote_id: str) -> dict:
    """What a fetch would send (no credential values, only the env variable name)."""
    if account["platform"] == "bluesky":
        return {"method": "GET", "endpoint": BLUESKY_APPVIEW + GET_POSTS,
                "params": {"uris": remote_id}, "auth": "none (public AppView; never authenticated)"}
    server = require_https_public_server(str(account.get("server", "")))
    return {"method": "GET", "endpoint": f"{server}/api/v1/statuses/{quote(remote_id, safe='')}",
            "auth": f"none; one retry with bearer {account['env_prefix']}_TOKEN only on 401/404 and only if set"}


def select_posts(con: sqlite3.Connection, accounts: dict[str, dict], *, account_id: str | None = None,
                 post_id: str | None = None, max_posts: int = DEFAULT_MAX_POSTS) -> tuple[list[dict], list[dict]]:
    """Eligible published posts (newest first, at most ``max_posts``) and skipped posts with reasons."""
    if type(max_posts) is not int or not 1 <= max_posts <= MAX_POSTS_LIMIT:
        raise ValidationError(f"max_posts must be 1..{MAX_POSTS_LIMIT}")
    if account_id is not None:
        account = accounts.get(account_id)
        if account is None:
            raise ValidationError("Unknown account. Run 'doctor' for account IDs")
        if account["platform"] not in SOURCES:
            raise ValidationError("metrics-fetch supports only bluesky and mastodon accounts")
    query, params = "SELECT id,account_id,platform,status,remote_id FROM posts", []
    clauses = []
    if post_id is not None:
        clauses.append("id=?")
        params.append(post_id)
    if account_id is not None:
        clauses.append("account_id=?")
        params.append(account_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    rows = con.execute(query + " ORDER BY created_at DESC, id", params).fetchall()
    if post_id is not None and not rows:
        raise ValidationError(f"Unknown post id: {post_id}" + (" for this account" if account_id else ""))
    eligible: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        base = {"post_id": row["id"], "account_id": row["account_id"], "platform": row["platform"]}
        account = accounts.get(row["account_id"])
        remote_id = str(row["remote_id"] or "").strip()
        reason = None
        if row["platform"] not in SOURCES:
            reason = "platform_not_supported_for_public_counters"
        elif row["status"] != "published":
            reason = f"not_published (status={row['status']})"
        elif not remote_id:
            reason = "published_without_remote_id"
        elif account is None or account["platform"] != row["platform"]:
            reason = "account_not_in_config"
        elif account.get("backend") is not None:
            reason = "backend_account_remote_id_is_not_a_platform_id"
        elif row["platform"] == "bluesky" and not _POST_URI.fullmatch(remote_id):
            reason = "remote_id_is_not_a_bluesky_post_at_uri"
        elif row["platform"] == "mastodon" and not _MASTODON_ID.fullmatch(remote_id):
            reason = "remote_id_is_not_a_mastodon_status_id"
        if reason is not None:
            # Without a filter, unrelated platforms are silently out of scope rather than noise.
            if post_id is not None or account_id is not None or row["platform"] in SOURCES:
                skipped.append({**base, "reason": reason})
            continue
        if len(eligible) >= max_posts:
            skipped.append({**base, "reason": f"deferred_max_posts_{max_posts}"})
            continue
        eligible.append({**base, "remote_id": remote_id, "account": account})
    return eligible, skipped


def _result(item: dict, data: dict, observed_at: str, auth: str) -> tuple[dict, dict[str, int | None]]:
    platform = item["platform"]
    values = map_counts(platform, data)
    fetched = {k: v for k, v in values.items() if v is not None}
    return ({"post_id": item["post_id"], "account_id": item["account_id"], "platform": platform,
             "source": SOURCES[platform], "observed_at": observed_at, "auth": auth,
             "fields_fetched": fetched,
             "fields_unknown": [k for k in METRIC_FIELDS if values[k] is None],
             "source_urls": list(SOURCE_URLS[platform])}, values)


def _fetch_bluesky(batch: list[dict]) -> tuple[dict[str, dict], str]:
    """One getPosts query for up to 25 posts; returns validated posts keyed by URI."""
    url = BLUESKY_APPVIEW + GET_POSTS + "?" + urlencode([("uris", item["remote_id"]) for item in batch])
    data = _get(url)  # no Authorization header: the public AppView does not take one
    observed_at = utc_now()
    posts = data.get("posts")
    if not isinstance(posts, list):
        raise publishers.PublishError("bluesky_getposts_missing_posts")
    found: dict[str, dict] = {}
    for post in posts:
        if isinstance(post, dict) and isinstance(post.get("uri"), str):
            found[post["uri"]] = post
    return found, observed_at


def _bluesky_author_ok(uri: str, post: dict) -> bool:
    match = _POST_URI.fullmatch(uri)
    author = post.get("author")
    return bool(match and isinstance(author, dict) and author.get("did") == match.group(1))


def _fetch_mastodon(item: dict) -> tuple[dict, str, str]:
    """GET one status unauthenticated, then at most one bearer retry on 401/404."""
    account = item["account"]
    url = _request_plan(account, item["remote_id"])["endpoint"]
    try:
        return _get(url), utc_now(), "none"
    except RateLimited:
        raise
    except publishers.PublishError as exc:
        token = credential(account["env_prefix"] + "_TOKEN")
        if _http_code(exc) not in (401, 404) or not token:
            raise
    data = _get(url, {"Authorization": f"Bearer {token}"})  # exactly one retry; GET only
    return data, utc_now(), "bearer_token_retry"


def _mastodon_identity_error(item: dict, data: dict) -> str | None:
    account = item["account"]
    if str(data.get("id", "")) != item["remote_id"]:
        return "mastodon_status_id_mismatch"
    author = data.get("account")
    if not isinstance(author, dict):
        return "mastodon_status_missing_account"
    expected = _mastodon_local_handle(account)
    instance = (urlparse(account["server"]).hostname or "").casefold()
    username = str(author.get("username", "")).casefold()
    acct = str(author.get("acct", username)).casefold()
    if username != expected or acct not in (expected, f"{expected}@{instance}"):
        return "author_mismatch: status account is not the configured handle"
    return None


def fetch_metrics(con: sqlite3.Connection, accounts: dict[str, dict], *, account_id: str | None = None,
                  post_id: str | None = None, max_posts: int = DEFAULT_MAX_POSTS,
                  dry_run: bool = False) -> dict[str, Any]:
    """Poll public counters for eligible published posts and store one snapshot per post.

    ``dry_run`` lists the planned GET requests and touches neither the network nor the DB.
    Re-running within the same UTC second upserts the same (post_id, observed_at, source) row.
    """
    eligible, skipped = select_posts(con, accounts, account_id=account_id, post_id=post_id,
                                     max_posts=max_posts)
    summary: dict[str, Any] = {"mode": "dry_run" if dry_run else "online", "max_posts": max_posts,
                               "fetched": [], "failed": [], "skipped": skipped, "stopped": None,
                               "note": NOTE}
    if dry_run:
        summary["would_fetch"] = [{"post_id": i["post_id"], "account_id": i["account_id"],
                                   "platform": i["platform"], "source": SOURCES[i["platform"]],
                                   "request": _request_plan(i["account"], i["remote_id"]),
                                   "fields": [c for c, _ in FIELD_MAP[i["platform"]]]} for i in eligible]
        summary["note"] = "DRY RUN: no network request and no database write. " + NOTE
        return summary
    for item in eligible:  # fail closed on a bad handle before any request is sent
        if item["platform"] == "mastodon":
            _mastodon_local_handle(item["account"])

    def record(item: dict, data: dict, observed_at: str, auth: str) -> None:
        entry, values = _result(item, data, observed_at, auth)
        if not entry["fields_fetched"]:
            summary["failed"].append({**_ref(item), "reason": "no_documented_counts_in_response"})
            return
        store_metrics(con, item["post_id"], observed_at, SOURCES[item["platform"]], **values)
        summary["fetched"].append(entry)

    bluesky = [i for i in eligible if i["platform"] == "bluesky"]
    mastodon = [i for i in eligible if i["platform"] == "mastodon"]
    try:
        for start in range(0, len(bluesky), GET_POSTS_MAX_URIS):
            batch = bluesky[start:start + GET_POSTS_MAX_URIS]
            try:
                found, observed_at = _fetch_bluesky(batch)
            except RateLimited:
                raise
            except publishers.PublishError as exc:
                summary["failed"].extend({**_ref(i), "reason": str(exc)} for i in batch)
                continue
            for item in batch:
                post = found.get(item["remote_id"])
                if post is None:
                    summary["failed"].append({**_ref(item), "reason": "not_returned (deleted, hidden or not indexed)"})
                elif not _bluesky_author_ok(item["remote_id"], post):
                    summary["failed"].append({**_ref(item), "reason": "author_mismatch: author DID differs from URI DID"})
                else:
                    record(item, post, observed_at, "none")
        for item in mastodon:
            try:
                data, observed_at, auth = _fetch_mastodon(item)
            except RateLimited:
                raise
            except publishers.PublishError as exc:
                summary["failed"].append({**_ref(item), "reason": str(exc)})
                continue
            problem = _mastodon_identity_error(item, data)
            if problem:
                summary["failed"].append({**_ref(item), "reason": problem})
            else:
                record(item, data, observed_at, auth)
    except RateLimited:
        done = {e["post_id"] for e in summary["fetched"]} | {f["post_id"] for f in summary["failed"]}
        summary["not_attempted"] = [i["post_id"] for i in eligible if i["post_id"] not in done]
        summary["stopped"] = ("rate_limited: the provider returned HTTP 429. The run stopped without retrying; "
                              "snapshots already stored are kept. Try again later with a smaller --max-posts.")
    return summary


def _ref(item: dict) -> dict:
    return {"post_id": item["post_id"], "account_id": item["account_id"], "platform": item["platform"]}


# --- Third mocked provider: "mockstats" (issue #15, offline design slice) -----------------------------
# An INVENTED provider used only to prove the pipeline copes with metric definitions that differ from
# Bluesky/Mastodon public counters and with privacy thresholds. It has no endpoint, no credential and no
# network code: ``normalize_mockstats`` is a pure function over an already-parsed JSON object. A real
# provider would need its own documented fields and its own review before any fetcher exists.
MOCKSTATS_SOURCE = "mockstats-mock"
MOCKSTATS_DEFAULT_THRESHOLD = 10
# native field -> (snapshot column, uses privacy threshold). Definitions deliberately differ:
#   plays_3s       plays of at least 3 seconds   -> views       (a play is not an impression)
#   link_taps      taps on the post link          -> clicks
#   bookmarks      saves                          -> saves
#   unique_viewers distinct people, deduplicated  -> NOT mapped: it is not "impressions" (which counts
#                  repeat displays) and not "views"; mapping it would mix two definitions.
#   reactions_all  every reaction type combined   -> NOT mapped: it is not "likes".
MOCKSTATS_FIELD_MAP: dict[str, tuple[str, bool]] = {
    "plays_3s": ("views", True), "link_taps": ("clicks", True), "bookmarks": ("saves", True),
}
MOCKSTATS_UNMAPPED = ("unique_viewers", "reactions_all")


def normalize_mockstats(payload: dict, *, threshold: int = MOCKSTATS_DEFAULT_THRESHOLD) -> dict[str, int | str | None]:
    """Map one mockstats metrics object to ``store_metrics`` values (offline, pure).

    * A documented count at or above ``threshold`` is exact.
    * A count BELOW ``threshold`` is stored as ``suppressed`` and the small number is dropped, so it
      can neither be summed as exact nor re-exposed (it is below the provider's privacy minimum).
    * ``{"withheld": "privacy_threshold"}`` is ``suppressed``, ``{"withheld": "not_collected"}`` is
      ``not_available`` and ``{"pending": true}`` is ``delayed``.
    * Missing, boolean, negative, float or string values stay unknown (None): never zero.
    * Fields whose definition differs from a snapshot column are not mapped at all.
    """
    if type(threshold) is not int or threshold < 1:
        raise ValidationError("threshold must be a positive integer")
    if not isinstance(payload, dict):
        raise ValidationError("mockstats payload must be an object")
    values: dict[str, int | str | None] = {field: None for field in METRIC_FIELDS}
    for native, (column, thresholded) in MOCKSTATS_FIELD_MAP.items():
        raw = payload.get(native)
        if isinstance(raw, dict):
            if raw.get("withheld") == "privacy_threshold":
                values[column] = "suppressed"
            elif raw.get("withheld") == "not_collected":
                values[column] = "not_available"
            elif raw.get("pending") is True:
                values[column] = "delayed"
            continue
        count = _count(raw)
        if count is None:
            continue
        values[column] = "suppressed" if thresholded and count < threshold else count
    return values
