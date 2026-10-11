"""Read-only fetch of PUBLIC replies to your own published Bluesky/Mastodon posts (issue #17 slice).

``engagement-fetch`` turns the replies under posts that ReachWeaver itself recorded as
``published`` (with a platform ``remote_id``) into a ``reachweaver.engagement-export.v1``
file that ``engagement-triage`` reads unchanged. It never sends, replies, likes, follows
or writes a database row. Without ``online=True`` it only lists the planned GET requests.

* Bluesky: ``app.bsky.feed.getPostThread`` (XRPC query, HTTP GET) on the public AppView
  ``https://public.api.bsky.app`` with no credential. Parameters ``uri`` (AT-URI),
  ``depth`` (reply levels, lexicon default 6, 0..1000) and ``parentHeight`` (0..1000; sent
  as 0 because ancestors of your own post are not replies to it). ``thread`` is a
  ``threadViewPost`` (``post``, ``replies``) or ``notFoundPost`` / ``blockedPost``.
* Mastodon: ``GET /api/v1/statuses/:id`` (owner check, public URL) then
  ``GET /api/v1/statuses/:id/context`` on the account's configured HTTPS server, both
  WITHOUT a token. Unauthenticated context is "limited to 40 ancestors and 60 descendants
  with a maximum depth of 20"; a 404 means the status is private or does not exist.
  ``content`` is HTML and is reduced to plain text here.

Neither endpoint is paginated, so each post costs one Bluesky request or two Mastodon
requests. Runs are capped in posts, depth and replies; requests are sequential and never
redirected (``publishers.request_get_json``); a 429 stops the run at once with no retry and
records only the provider's retry hint. Reply text is untrusted: secrets and personal data
are redacted with ``engagement.redact_private_data`` before it reaches the file, which is a
NEW owner-only (0600) file that is never overwritten.

Sources (fetched and checked 2026-10-09):
  https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getPostThread.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/defs.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/post.json
  https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/actor/defs.json
  https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/api-directory.mdx
  https://docs.joinmastodon.org/methods/statuses/#context
  https://docs.joinmastodon.org/entities/Status/
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from . import publishers
from .campaigns import public_url
from .engagement import EXPORT_SCHEMA, MAX_ID_CHARS, MAX_ITEMS, MAX_TEXT_CHARS, load_export, redact_private_data
from .metrics_fetch import (BLUESKY_APPVIEW, DEFAULT_MAX_POSTS, MAX_POSTS_LIMIT, RateLimited, _POST_URI,
                            _bluesky_author_ok, _http_code, _mastodon_identity_error, _mastodon_local_handle,
                            select_posts)
from .policy import ValidationError, require_https_public_server
from .privacy import write_private_file
from .storage import parse_utc, utc_now

GET_POST_THREAD = "/xrpc/app.bsky.feed.getPostThread"
SUPPORTED = ("bluesky", "mastodon")
DEFAULT_DEPTH = 6        # getPostThread lexicon default
MAX_DEPTH = 20           # our cap; Mastodon's unauthenticated context depth limit is 20
DEFAULT_MAX_REPLIES = 100
MAX_REPLIES_LIMIT = 500
MASTODON_UNAUTH_DESCENDANTS = 60  # documented unauthenticated cap: more replies may exist
MAX_HTML_CHARS = 100_000
SOURCE_URLS = {
    "bluesky": ("https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getPostThread.json",),
    "mastodon": ("https://docs.joinmastodon.org/methods/statuses/#context",),
}
NOTE = ("READ-ONLY public replies (Bluesky public AppView, unauthenticated Mastodon API). No token was used, "
        "no database row was written and nothing was posted, liked, followed or sent. Reply text is untrusted "
        "data; secrets and personal data were redacted before writing.")


def _get(url: str) -> dict:
    """Bounded, redirect-free, sanitized, unauthenticated GET; a 429 becomes RateLimited.

    Unlike ``metrics_fetch._get`` the provider retry hint is kept, read-only, for the summary.
    """
    try:
        return publishers.request_get_json(url)  # no headers: never a credential
    except publishers.PublishError as exc:
        if _http_code(exc) == 429:
            raise RateLimited("http_429", status=429, retry_hint=getattr(exc, "retry_hint", None)) from None
        raise


def _reason(exc: publishers.PublishError, platform: str) -> str:
    code = _http_code(exc)
    if code == 404:
        return "not_found (deleted, private or not indexed)" if platform == "mastodon" else "not_found"
    if code == 400 and platform == "bluesky":
        return "http_400: XRPC error (getPostThread reports NotFound for deleted or unindexed posts)"
    if code == 401:
        return "http_401: server requires authorization (authorized fetch); engagement-fetch never sends a token"
    if code in (301, 302, 303, 307, 308):
        return f"http_{code}: redirect refused"
    return str(exc)


def _check_bounds(max_posts: int, depth: int, max_replies: int) -> None:
    if type(max_posts) is not int or not 1 <= max_posts <= MAX_POSTS_LIMIT:
        raise ValidationError(f"max_posts must be 1..{MAX_POSTS_LIMIT}")
    if type(depth) is not int or not 1 <= depth <= MAX_DEPTH:
        raise ValidationError(f"depth must be 1..{MAX_DEPTH}")
    if type(max_replies) is not int or not 1 <= max_replies <= MAX_REPLIES_LIMIT:
        raise ValidationError(f"max_replies must be 1..{MAX_REPLIES_LIMIT}")


def _request_plan(account: dict, remote_id: str, depth: int) -> list[dict]:
    """The GET requests a fetch would send; never any credential (none is ever used)."""
    if account["platform"] == "bluesky":
        params = {"uri": remote_id, "depth": depth, "parentHeight": 0}
        return [{"method": "GET", "endpoint": BLUESKY_APPVIEW + GET_POST_THREAD, "params": params,
                 "url": BLUESKY_APPVIEW + GET_POST_THREAD + "?" + urlencode(params), "auth": "none"}]
    server = require_https_public_server(str(account.get("server", "")))
    status = f"{server}/api/v1/statuses/{quote(remote_id, safe='')}"
    return [{"method": "GET", "endpoint": status, "url": status, "auth": "none"},
            {"method": "GET", "endpoint": status + "/context", "url": status + "/context", "auth": "none"}]


class _TextExtractor(HTMLParser):
    """Plain text from Mastodon status HTML: paragraphs and <br> become newlines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "p" and self.parts:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: object) -> str:
    """Text content of untrusted HTML; markup, links' targets and scripts are dropped."""
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(value[:MAX_HTML_CHARS])
    parser.close()
    return "".join(parser.parts).strip()


# Characters that remain when a cut lands inside a phone-number-like run (see engagement.PHONE).
_NUMBER_RUN = frozenset("0123456789 \t\n\r().+-")


def _drop_cut_fragment(text: str) -> str:
    """Remove what a hard length cut may have split: the trailing printable-ASCII run (the
    characters an e-mail address, URL or token consists of) and then a trailing digit run.

    A fragment of a secret no longer matches the redaction patterns, so it must not be kept.
    Written as backward scans rather than ``$``-anchored regexes to stay linear in length.
    """
    end = len(text)
    while end and "!" <= text[end - 1] <= "~":
        end -= 1
    start = end
    while start and text[start - 1] in _NUMBER_RUN:
        start -= 1
    if any(c.isdigit() for c in text[start:end]):
        end = start
    return text[:end]


def _clean_text(text: str) -> str:
    """Length-capped, secret- and PII-redacted text. Control characters are kept so that
    engagement-triage can still flag hidden bidi controls as suspicious.

    Redaction runs BEFORE the cap (review F1): on a window of twice the cap, so an address or
    token that crosses the cap is seen whole and replaced. A fragment split by either cut (the
    window edge, or the cap applied to the redacted text) is dropped, and the result is
    redacted once more.
    """
    window = text[:2 * MAX_TEXT_CHARS]
    if len(window) < len(text):
        window = _drop_cut_fragment(window)
    redacted = redact_private_data(window)[0]
    if len(redacted) > MAX_TEXT_CHARS:
        redacted = redact_private_data(_drop_cut_fragment(redacted[:MAX_TEXT_CHARS]))[0][:MAX_TEXT_CHARS]
    return redacted


def _item_id(platform: str, native: str) -> str:
    raw = f"{platform}:{native}"
    return raw if len(raw) <= MAX_ID_CHARS else f"{platform}:sha256:{hashlib.sha256(raw.encode()).hexdigest()}"


def _created_at(*candidates: object) -> str | None:
    for value in candidates:
        if isinstance(value, str):
            try:
                return parse_utc(value)
            except ValidationError:
                continue
    return None


def _bsky_url(uri: str) -> str | None:
    match = _POST_URI.fullmatch(uri)
    return f"https://bsky.app/profile/{match.group(1)}/post/{match.group(2)}" if match else None


def _bluesky_items(item: dict, data: dict, depth: int, max_replies: int, problems: list[dict]) -> list[dict]:
    """Walk a threadViewPost breadth-first; the root (your own post) is never an item."""
    thread = data.get("thread")
    if not isinstance(thread, dict):
        raise publishers.PublishError("malformed_response: thread missing")
    if thread.get("notFound") is True:
        raise publishers.PublishError("not_found (deleted or not indexed)")
    if thread.get("blocked") is True:
        raise publishers.PublishError("blocked")
    root = thread.get("post")
    if not isinstance(root, dict) or root.get("uri") != item["remote_id"]:
        raise publishers.PublishError("malformed_response: thread root is not the requested post")
    if not _bluesky_author_ok(item["remote_id"], root):
        raise publishers.PublishError("author_mismatch: author DID differs from URI DID")
    owner_did = _POST_URI.fullmatch(item["remote_id"]).group(1)  # type: ignore[union-attr]
    root_url = _bsky_url(item["remote_id"])
    out: list[dict] = []
    queue: list[tuple[object, str | None, int]] = [(r, root_url, 1) for r in _list(thread.get("replies"))]
    while queue:
        node, parent, level = queue.pop(0)
        if not isinstance(node, dict):
            continue
        if node.get("notFound") is True or node.get("blocked") is True:
            problems.append({"post_id": item["post_id"], "reason": "reply_not_found_or_blocked"})
            continue
        post = node.get("post")
        uri = post.get("uri") if isinstance(post, dict) else None
        url = _bsky_url(uri) if isinstance(uri, str) else None
        author = post.get("author") if isinstance(post, dict) else None
        record = post.get("record") if isinstance(post, dict) else None
        if (not url or not isinstance(author, dict) or not isinstance(author.get("did"), str)
                or not isinstance(record, dict) or not isinstance(record.get("text"), str)):
            problems.append({"post_id": item["post_id"], "reason": "malformed_reply_skipped"})
            continue
        created = _created_at(record.get("createdAt"), post.get("indexedAt"))  # type: ignore[union-attr]
        if created is None:
            problems.append({"post_id": item["post_id"], "reason": "reply_without_valid_timestamp_skipped"})
            continue
        if len(out) >= max_replies:
            problems.append({"post_id": item["post_id"], "reason": f"truncated_max_replies_{max_replies}"})
            break
        # An empty or missing handle falls back to the DID; a reply with neither is skipped
        # (one note each) so that it cannot make write_export reject the whole export.
        handle = next((value.strip() for value in (author.get("handle"), author["did"])
                       if isinstance(value, str) and value.strip()), None)
        if handle is None:
            problems.append({"post_id": item["post_id"], "reason": "reply_without_author_identity_skipped"})
            continue
        item_id = _item_id("bluesky", uri)  # type: ignore[arg-type]
        out.append({"id": item_id, "platform": "bluesky", "account_id": item["account_id"],
                    "author_handle": handle, "author_is_owner": author["did"] == owner_did,
                    "text": _clean_text(record["text"]), "url": url, "created_at": created,
                    "in_reply_to": parent, "thread_root_url": root_url})
        if level < depth:
            queue.extend((r, item_id, level + 1) for r in _list(node.get("replies")))
    return out


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _mastodon_handle(status: dict, instance: str) -> str | None:
    account = status.get("account")
    acct = account.get("acct") if isinstance(account, dict) else None
    if not isinstance(acct, str) or not acct.strip():
        return None
    acct = acct.strip().lstrip("@")
    return acct if "@" in acct else f"{acct}@{instance}"


def _mastodon_url(status: dict) -> str | None:
    for key in ("url", "uri"):
        value = status.get(key)
        if isinstance(value, str) and len(value) <= 2048 and public_url(value):
            return value
    return None


def _mastodon_items(item: dict, root: dict, context: dict, depth: int, max_replies: int,
                    problems: list[dict]) -> list[dict]:
    """Descendants of your own status, ordered as returned, within the depth/count caps."""
    descendants = context.get("descendants")
    if not isinstance(descendants, list):
        raise publishers.PublishError("malformed_response: descendants missing")
    account = item["account"]
    instance = (urlparse(account["server"]).hostname or "").casefold()
    expected = _mastodon_local_handle(account)
    root_url = _mastodon_url(root)
    if root_url is None:
        raise publishers.PublishError("malformed_response: status has no public https url")
    if len(descendants) >= MASTODON_UNAUTH_DESCENDANTS:
        problems.append({"post_id": item["post_id"],
                         "reason": f"possibly_truncated: unauthenticated context returns at most "
                                   f"{MASTODON_UNAUTH_DESCENDANTS} descendants"})
    levels: dict[str, int] = {item["remote_id"]: 0}
    ids: dict[str, str] = {}
    out: list[dict] = []
    for status in descendants:
        if not isinstance(status, dict) or not isinstance(status.get("id"), str):
            problems.append({"post_id": item["post_id"], "reason": "malformed_reply_skipped"})
            continue
        parent = status.get("in_reply_to_id")
        if parent not in levels:
            continue  # not connected to this post through the returned statuses
        level = levels[parent] + 1
        if level > depth:
            continue
        if status.get("visibility") not in (None, "public", "unlisted"):
            continue  # unauthenticated reads should never return these; never export them
        url, handle = _mastodon_url(status), _mastodon_handle(status, instance)
        created = _created_at(status.get("created_at"))
        if url is None or handle is None or created is None:
            problems.append({"post_id": item["post_id"], "reason": "malformed_reply_skipped"})
            continue
        if len(out) >= max_replies:
            problems.append({"post_id": item["post_id"], "reason": f"truncated_max_replies_{max_replies}"})
            break
        levels[status["id"]] = level
        item_id = _item_id("mastodon", f"{instance}:{status['id']}")
        ids[status["id"]] = item_id
        text = html_to_text(status.get("content"))
        spoiler = status.get("spoiler_text")
        if isinstance(spoiler, str) and spoiler.strip():
            text = f"[CW: {spoiler.strip()}]\n{text}"
        account_value = status.get("account")
        author: dict = account_value if isinstance(account_value, dict) else {}
        is_owner = (str(author.get("username", "")).casefold() == expected
                    and handle.casefold() == f"{expected}@{instance}")
        out.append({"id": item_id, "platform": "mastodon", "account_id": item["account_id"],
                    "author_handle": handle, "author_is_owner": is_owner, "text": _clean_text(text),
                    "url": url, "created_at": created,
                    "in_reply_to": root_url if parent == item["remote_id"] else ids[parent],
                    "thread_root_url": root_url})
    return out


def _ref(item: dict) -> dict:
    return {"post_id": item["post_id"], "account_id": item["account_id"], "platform": item["platform"]}


def fetch_engagement(con: sqlite3.Connection, accounts: dict[str, dict], *, account_id: str | None = None,
                     post_id: str | None = None, max_posts: int = DEFAULT_MAX_POSTS, depth: int = DEFAULT_DEPTH,
                     max_replies: int = DEFAULT_MAX_REPLIES, online: bool = False) -> dict[str, Any]:
    """Collect public replies to eligible published posts. Returns a summary with ``export``.

    ``online=False`` (the default) lists the planned GET requests and touches no network.
    The database is only read: no row, status or event is written.
    """
    _check_bounds(max_posts, depth, max_replies)
    if account_id is not None and account_id in accounts and accounts[account_id]["platform"] not in SUPPORTED:
        raise ValidationError("engagement-fetch supports only bluesky and mastodon accounts")
    eligible, skipped = select_posts(con, accounts, account_id=account_id, post_id=post_id, max_posts=max_posts)
    for entry in skipped:
        if entry["reason"] == "platform_not_supported_for_public_counters":
            entry["reason"] = "platform_not_supported_for_engagement_fetch"
    plans = {i["post_id"]: _request_plan(i["account"], i["remote_id"], depth) for i in eligible}  # fail closed
    for i in eligible:
        if i["platform"] == "mastodon":
            _mastodon_local_handle(i["account"])
    summary: dict[str, Any] = {"mode": "online" if online else "plan_only", "max_posts": max_posts,
                               "depth": depth, "max_replies": max_replies, "fetched": [], "failed": [],
                               "skipped": skipped, "notes": [], "stopped": None, "note": NOTE}
    if not online:
        summary["would_fetch"] = [{**_ref(i), "requests": [{k: v for k, v in r.items() if k != "url"}
                                                           for r in plans[i["post_id"]]],
                                   "source_urls": list(SOURCE_URLS[i["platform"]])} for i in eligible]
        summary["note"] = ("PLAN ONLY: no network request, no file and no database write. "
                           "Re-run with --online --out FILE to fetch. " + NOTE)
        return summary
    items: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    try:
        for item in eligible:
            problems: list[dict] = []
            plan = plans[item["post_id"]]
            try:
                if item["platform"] == "bluesky":
                    found = _bluesky_items(item, _get(plan[0]["url"]), depth, max_replies, problems)
                else:
                    root = _get(plan[0]["url"])
                    problem = _mastodon_identity_error(item, root)
                    if problem:
                        raise publishers.PublishError(problem)
                    found = _mastodon_items(item, root, _get(plan[1]["url"]), depth, max_replies, problems)
            except RateLimited:
                raise
            except publishers.PublishError as exc:
                summary["failed"].append({**_ref(item), "reason": _reason(exc, item["platform"])})
                continue
            new = 0
            for reply in found:
                if reply["id"] in seen:
                    duplicates += 1
                    continue
                if len(items) >= MAX_ITEMS:
                    problems.append({"post_id": item["post_id"], "reason": f"truncated_export_max_items_{MAX_ITEMS}"})
                    break
                seen.add(reply["id"])
                items.append(reply)
                new += 1
            summary["fetched"].append({**_ref(item), "replies": new, "observed_at": utc_now(),
                                       "source_urls": list(SOURCE_URLS[item["platform"]])})
            summary["notes"].extend(problems)
    except RateLimited as exc:
        done = {e["post_id"] for e in summary["fetched"]} | {f["post_id"] for f in summary["failed"]}
        summary["not_attempted"] = [i["post_id"] for i in eligible if i["post_id"] not in done]
        hint = getattr(exc, "retry_hint", None)
        summary["retry_after_seconds"] = hint.delay_seconds if hint is not None else None
        summary["stopped"] = ("rate_limited: the provider returned HTTP 429. The run stopped without retrying "
                              "and no cooldown state was written. Try again later with a smaller --max-posts.")
    summary["duplicates_dropped"] = duplicates
    summary["export"] = {"schema_version": EXPORT_SCHEMA,
                         "source": "reachweaver engagement-fetch: public replies, read-only, redacted",
                         "generated_at": utc_now(), "items": items}
    return summary


def write_export(path: Path, export: dict) -> int:
    """Validate against engagement-triage's own loader, then write a NEW 0600 file."""
    load_export(export)  # the exact contract engagement-triage enforces
    write_private_file(path, json.dumps(export, indent=2, ensure_ascii=False) + "\n")
    return len(export["items"])
