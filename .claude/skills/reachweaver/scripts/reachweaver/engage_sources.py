"""Read-only public sources for ``engage-scout --online``: GET only, no credential, bounded, no retry.

Three searches, each checked on 2026-10-21 against the official docs and with a real unauthenticated GET
(details and quotes in ``docs/ENGAGE-SCOUT.md``):

* Bluesky ``app.bsky.feed.searchPosts`` (XRPC query, HTTP GET) on ``https://api.bsky.app``. The API hosts guide
  says public endpoints "can be made directly against the Bluesky API, preferably via the
  https://public.api.bsky.app hostname"; on 2026-10-21 that cached host answered searchPosts with HTTP 403 while
  ``api.bsky.app`` answered 200 without auth, so the scout uses ``api.bsky.app``. The rate-limits page says these
  direct hosts "do not support authentication" and have "generous rate-limits" (no number). The lexicon warns the
  endpoint "may require authentication (eg, not public) for some service providers". Follower counts come from one
  ``app.bsky.actor.getProfiles`` call per 25 authors (``actors`` maxLength 25). Authors carrying the
  ``!no-unauthenticated`` label (they asked apps not to show them to logged-out viewers) are dropped.
* Mastodon ``GET /api/v1/timelines/tag/:hashtag`` on the owner's configured server. The docs say "Requires app
  token + read:statuses if the instance has disabled public preview" and that a server can be "configured to
  disable unauthenticated access to hashtag timelines" (401); ``limit`` max 40; 300 requests per 5 minutes.
  Accounts with ``discoverable: false`` or ``noindex: true`` are dropped, boosts and non-public posts skipped.
* GitHub ``GET /search/issues`` on ``https://api.github.com``: "For unauthenticated requests, the rate limit allows
  you to make up to 10 requests per minute." Pull requests are skipped; bot accounts are flagged.

Every request: HTTPS to an allow-listed host, method GET, no Authorization/Cookie header, no redirect followed,
15 s timeout, a 2 MB body cap, JSON nesting limit (``inputs.loads_text``). A 429 (or GitHub's 403 with
``x-ratelimit-remaining: 0``) stops that source at once with no retry. Fetched text is returned as untrusted data
for ``engage_scout`` to clean; nothing here interprets it. Nothing here posts, replies, likes or follows.
"""
from __future__ import annotations

import http.client
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .inputs import loads_text
from .policy import ValidationError

BLUESKY_HOST = "https://api.bsky.app"
SEARCH_POSTS = "/xrpc/app.bsky.feed.searchPosts"
GET_PROFILES = "/xrpc/app.bsky.actor.getProfiles"
GITHUB_HOST = "https://api.github.com"
SEARCH_ISSUES = "/search/issues"
MASTODON_TAG = "/api/v1/timelines/tag/"
USER_AGENT = "ReachWeaver-engage-scout (read-only)"
TIMEOUT_SECONDS = 15
MAX_BODY_BYTES = 2_000_000
MAX_QUERIES = {"bluesky": 5, "mastodon": 5, "github": 3}  # GitHub: 10 unauthenticated searches per minute
PROFILES_PER_REQUEST = 25  # getProfiles ``actors`` maxLength
MAX_PROFILE_REQUESTS = 2
NO_UNAUTHENTICATED = "!no-unauthenticated"
CHECKED_ON = "2026-10-21"
ONLINE_ORDER = ("bluesky", "mastodon", "github")
SOURCE_URLS = {
    "bluesky": ("https://docs.bsky.app/docs/api-directory", "https://docs.bsky.app/docs/rate-limits",
                "https://docs.bsky.app/docs/developer-guidelines"),
    "mastodon": ("https://docs.joinmastodon.org/methods/timelines/#tag",
                 "https://docs.joinmastodon.org/api/rate-limits/"),
    "github": ("https://docs.github.com/en/rest/search/search",
               "https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies"),
}


class SourceError(RuntimeError):
    """A sanitised per-request failure code (never a header or response body)."""


class RateLimited(SourceError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None  # a 3xx surfaces as HTTPError and is refused


def _open(req: Request):
    """The only place a connection is opened (tests replace this with recorded fixtures)."""
    return build_opener(_NoRedirects()).open(req, timeout=TIMEOUT_SECONDS)


def get_json(url: str, allowed_hosts: frozenset[str]) -> Any:
    """One unauthenticated, redirect-free, bounded GET returning parsed JSON."""
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in allowed_hosts or parts.username or parts.password:
        raise SourceError("host_not_allowed")
    req = Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with _open(req) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        remaining = exc.headers.get("x-ratelimit-remaining") if exc.headers is not None else None
        try:
            exc.close()  # best effort; never replaces the sanitised error
        except Exception:  # noqa: BLE001
            pass
        if code == 429 or (code == 403 and remaining == "0"):
            raise RateLimited("rate_limited") from None
        raise SourceError(f"http_{code}") from None
    except (URLError, TimeoutError, OSError, http.client.HTTPException):
        raise SourceError("network_or_timeout") from None
    if len(raw) > MAX_BODY_BYTES:
        raise SourceError("response_too_large")
    try:
        return loads_text(raw.decode("utf-8"), "provider response")
    except (UnicodeError, ValueError, ValidationError):
        raise SourceError("invalid_json") from None


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "br" or (tag == "p" and self.parts):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value[:100_000])
    parser.close()
    return "".join(parser.parts).strip()


# --------------------------------------------------------------------- request plans

def _terms(config: dict) -> list[str]:
    if config["search_terms"]:
        return config["search_terms"]
    keywords = [k for p in config["projects"] for k in p["keywords"]]
    return list(dict.fromkeys(keywords))[:3]


def _phrase(term: str) -> str:
    return f'"{term}"' if " " in term else term


def _since(config: dict, platform: str, now: datetime) -> datetime:
    hours = config["max_age_hours"].get(platform, config["max_age_hours"]["default"])
    return now - timedelta(hours=hours)


def plan(config: dict, platforms: list[str], now: datetime) -> list[dict]:
    """Every GET the online run would send, in order (follower lookups are added after the search)."""
    limit = config["per_source_limit"]
    out: list[dict] = []
    if "bluesky" in platforms:
        since = _since(config, "bluesky", now).strftime("%Y-%m-%dT%H:%M:%SZ")
        for term in _terms(config)[:MAX_QUERIES["bluesky"]]:
            params = {"q": _phrase(term), "sort": "latest", "limit": limit, "since": since}
            out.append({"source": "bluesky", "method": "GET", "auth": "none",
                        "url": BLUESKY_HOST + SEARCH_POSTS + "?" + urlencode(params)})
    if "mastodon" in platforms and config["mastodon_server"]:
        for tag in config["hashtags"][:MAX_QUERIES["mastodon"]]:
            out.append({"source": "mastodon", "method": "GET", "auth": "none",
                        "url": f"{config['mastodon_server']}{MASTODON_TAG}{quote(tag, safe='')}?limit={limit}"})
    if "github" in platforms:
        day = _since(config, "github", now).date().isoformat()
        for term in _terms(config)[:MAX_QUERIES["github"]]:
            params = {"q": f"{_phrase(term)} is:issue is:open created:>={day}", "sort": "created", "order": "desc",
                      "per_page": limit}
            out.append({"source": "github", "method": "GET", "auth": "none",
                        "url": GITHUB_HOST + SEARCH_ISSUES + "?" + urlencode(params)})
    return out


# --------------------------------------------------------------------- normalisers (to the owner-file item shape)

def _bluesky_item(post: object) -> dict | None:
    if not isinstance(post, dict):
        return None
    uri, author, record = post.get("uri"), post.get("author"), post.get("record")
    if not (isinstance(uri, str) and isinstance(author, dict) and isinstance(record, dict)):
        return None
    parts = uri.split("/")
    if len(parts) != 5 or parts[0] != "at:" or parts[3] != "app.bsky.feed.post" or not author.get("did"):
        return None
    handle = author.get("handle") or author.get("did")
    raw_labels = [x for value in (author.get("labels"), post.get("labels")) if isinstance(value, list) for x in value]
    labels = [x.get("val") for x in raw_labels if isinstance(x, dict)]
    viewer_value = post.get("viewer")
    viewer: dict = viewer_value if isinstance(viewer_value, dict) else {}
    return {"platform": "bluesky", "url": f"https://bsky.app/profile/{parts[2]}/post/{parts[4]}",
            "author_handle": str(handle), "text": str(record.get("text") or ""),
            "created_at": str(record.get("createdAt") or post.get("indexedAt") or ""),
            "reply_count": post.get("replyCount") if type(post.get("replyCount")) is int else None,
            "reply_restricted": "threadgate" in post or viewer.get("replyDisabled") is True,
            "author_not_discoverable": NO_UNAUTHENTICATED in labels, "_did": str(author["did"]),
            "_source": "bluesky_search"}


def _mastodon_item(status: object, server: str) -> dict | None:
    if not isinstance(status, dict) or status.get("reblog") is not None or status.get("visibility") != "public":
        return None
    account = status.get("account")
    url = status.get("url")
    if not isinstance(account, dict) or not isinstance(url, str) or not isinstance(account.get("acct"), str):
        return None
    acct = account["acct"].lstrip("@")
    host = urlsplit(server).hostname or ""
    text = html_to_text(status.get("content"))
    spoiler = status.get("spoiler_text")
    if isinstance(spoiler, str) and spoiler.strip():
        text = f"[CW: {spoiler.strip()}]\n{text}"
    followers = account.get("followers_count")
    replies = status.get("replies_count")
    return {"platform": "mastodon", "url": url, "author_handle": acct if "@" in acct else f"{acct}@{host}",
            "text": text, "created_at": str(status.get("created_at") or ""),
            "author_followers": followers if type(followers) is int and followers >= 0 else None,
            "reply_count": replies if type(replies) is int and replies >= 0 else None,
            "author_is_bot": account.get("bot") is True,
            "author_not_discoverable": account.get("discoverable") is False or account.get("noindex") is True,
            "_source": "mastodon_hashtag"}


def _github_item(issue: object) -> dict | None:
    if not isinstance(issue, dict) or "pull_request" in issue:
        return None
    user = issue.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("login"), str) or not isinstance(issue.get("html_url"), str):
        return None
    labels = [x.get("name") for x in issue.get("labels") or [] if isinstance(x, dict) and isinstance(x.get("name"), str)]
    comments = issue.get("comments")
    return {"platform": "github", "url": issue["html_url"], "author_handle": user["login"],
            "title": str(issue.get("title") or ""), "text": str(issue.get("body") or "")[:20_000],
            "created_at": str(issue.get("created_at") or ""), "labels": labels[:50],
            "reply_count": comments if type(comments) is int and comments >= 0 else None,
            "locked": issue.get("locked") is True, "author_is_bot": user.get("type") == "Bot",
            "_source": "github_issue_search"}


# --------------------------------------------------------------------- fetch

def _bluesky_followers(items: list[dict], report: dict) -> None:
    dids = list(dict.fromkeys(i["_did"] for i in items))[:PROFILES_PER_REQUEST * MAX_PROFILE_REQUESTS]
    counts: dict[str, int] = {}
    for start in range(0, len(dids), PROFILES_PER_REQUEST):
        url = BLUESKY_HOST + GET_PROFILES + "?" + urlencode([("actors", d) for d in dids[start:start +
                                                                                       PROFILES_PER_REQUEST]])
        try:
            data = get_json(url, frozenset({"api.bsky.app"}))
        except SourceError as exc:
            report["followers"] = f"unknown ({exc})"
            break
        report["requests"] += 1
        for profile in data.get("profiles", []) if isinstance(data, dict) else []:
            if isinstance(profile, dict) and type(profile.get("followersCount")) is int:
                counts[str(profile.get("did"))] = profile["followersCount"]
    for item in items:
        did = item.pop("_did")
        if did in counts:
            item["author_followers"] = counts[did]


def fetch(config: dict, platforms: list[str], now: datetime) -> tuple[list[dict], list[dict]]:
    """Run the planned GETs sequentially. Returns (items in the owner-file shape, one report per source)."""
    planned = plan(config, platforms, now)
    items: list[dict] = []
    reports: list[dict] = []
    if "mastodon" in platforms and not (config["mastodon_server"] and config["hashtags"]):
        reports.append({"source": "mastodon", "status": "skipped: the config needs mastodon_server and hashtags"})
    hosts = {"bluesky": frozenset({"api.bsky.app"}), "github": frozenset({"api.github.com"}),
             "mastodon": frozenset({urlsplit(config["mastodon_server"] or "").hostname or ""})}
    for source in ONLINE_ORDER:
        requests = [r for r in planned if r["source"] == source]
        if not requests:
            continue
        report: dict[str, Any] = {"source": source, "status": "ok", "requests": 0, "received": 0,
                                  "sources_checked_on": CHECKED_ON, "source_urls": list(SOURCE_URLS[source])}
        found: list[dict] = []
        for request in requests:
            try:
                data = get_json(request["url"], hosts[source])
            except RateLimited:
                report["status"] = "stopped: rate limited (HTTP 429); no retry, try later"
                break
            except SourceError as exc:
                report["status"] = (f"stopped: {exc} (this server requires a token for hashtag timelines; the scout "
                                    "never sends one)" if source == "mastodon" and str(exc) == "http_401"
                                    else f"stopped: {exc}")
                break
            report["requests"] += 1
            if source == "bluesky":
                rows = data.get("posts") if isinstance(data, dict) else None
                found += [x for x in map(_bluesky_item, rows or []) if x]
            elif source == "mastodon":
                found += [x for x in (_mastodon_item(s, config["mastodon_server"]) for s in
                                      (data if isinstance(data, list) else [])) if x]
            else:
                rows = data.get("items") if isinstance(data, dict) else None
                found += [x for x in map(_github_item, rows or []) if x]
        if source == "bluesky" and found and report["status"] == "ok":
            _bluesky_followers(found, report)  # never after a stop: no request follows a 429
        for item in found:
            item.pop("_did", None)
        report["received"] = len(found)
        reports.append(report)
        items += found
    return items, reports

