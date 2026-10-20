"""Offline UTM links, metric definitions, conversion attribution and SEO destinations.

Everything here reads LOCAL files and the local SQLite database only. It never
fetches analytics, landing pages or the SEO repository, never publishes and never
writes to the database. Missing values stay ``None`` (unknown), never zero, and
attribution is a tagging/referrer join, never a causal claim.
"""
from __future__ import annotations

import csv
import ipaddress
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import PLATFORMS, ValidationError
from .private_sources import refuse_private_link
from .privacy import redact
from .storage import list_posts

# ---------------------------------------------------------------------------
# 1. Canonical UTM link builder
# ---------------------------------------------------------------------------

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
UTM_MEDIUM = "social"
# Click identifiers and email/ads trackers: they identify a click or a person,
# not a campaign, and make otherwise-identical destinations look different.
TRACKING_PARAMS = frozenset({
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "twclid", "ttclid", "li_fat_id", "igshid", "mc_cid", "mc_eid",
    "_hsenc", "_hsmi", "mkt_tok", "srsltid", "ref_src",
})
_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    slug = _SLUG.sub("-", value.strip().lower()).strip("-")
    if not slug or len(slug) > 100:
        raise ValidationError(f"{label} must contain 1..100 letters/digits after normalization")
    return slug


def _is_tracking(key: str) -> bool:
    key = key.lower()
    return key.startswith("utm_") or key in TRACKING_PARAMS


def normalize_destination(url: str) -> str:
    """Return a deterministic public HTTPS destination without fragment/trackers.

    Lowercases the host, drops the default port, strips the fragment, removes all
    ``utm_*`` and known click-tracking parameters and sorts the remaining query.
    """
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url.strip()):
        raise ValidationError("Destination must be a non-empty URL without whitespace")
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise ValidationError("Destination must use https")
    if parsed.username or parsed.password:
        raise ValidationError("Destination must not contain credentials")
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Destination has an invalid port") from exc
    if not host or "." not in host or host.endswith((".local", ".localhost")):
        raise ValidationError("Destination must be a public host name")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValidationError("IP-address destinations are not permitted")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                   if not _is_tracking(k))
    return urlunsplit(("https", netloc, parsed.path or "/", urlencode(query), ""))


def _origin(url: str) -> str:
    parts = urlsplit(normalize_destination(url))
    return f"{parts.scheme}://{parts.netloc}"


def normalize_origin(value: str) -> str:
    """Validate one allowlist entry such as ``https://github.com``."""
    parsed = urlsplit(value.strip() if isinstance(value, str) else "")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValidationError("Allowed origin must be scheme://host[:port] without path/query")
    return _origin(value)


def build_utm_link(url: str, *, platform: str, campaign: str, content: str | None = None,
                   term: str | None = None, allow_origins=None, private_repos=()) -> str:
    """Build a canonical, idempotent UTM-tagged destination.

    Applying the function to its own output with the same arguments returns the
    same string. With a non-empty ``allow_origins`` the destination origin must be
    listed (exact scheme+host+port). A destination inside any ``private_repos``
    (``owner/repo`` of a private source, issue #9) is refused: never a public link.
    """
    if platform not in PLATFORMS:
        raise ValidationError(f"Unknown platform for utm_source: {platform}")
    destination = normalize_destination(url)
    refuse_private_link(destination, private_repos)
    if allow_origins:
        allowed = {normalize_origin(item) for item in allow_origins}
        if _origin(destination) not in allowed:
            raise ValidationError(f"Destination origin {_origin(destination)} is not in the allowlist")
    params = [("utm_source", platform), ("utm_medium", UTM_MEDIUM),
              ("utm_campaign", _slug(campaign, "campaign"))]
    if content is not None:
        params.append(("utm_content", _slug(content, "content")))
    if term is not None:
        params.append(("utm_term", _slug(term, "term")))
    parts = urlsplit(destination)
    query = "&".join(item for item in (parts.query, urlencode(params)) if item)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def parse_utm(url: str) -> dict:
    """Return the ``utm_*`` parameters of a URL (lowercased keys, last value wins)."""
    return {k.lower(): v for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)
            if k.lower() in UTM_KEYS}


def validate_utm_link(url: str, *, allow_origins=None) -> list[str]:
    """Problems that would make a tagged social link unattributable or untidy."""
    issues: list[str] = []
    try:
        destination = normalize_destination(url)
    except ValidationError as exc:
        return [str(exc)]
    utm = parse_utm(url)
    for key in ("utm_source", "utm_medium", "utm_campaign"):
        if not utm.get(key):
            issues.append(f"missing {key}")
    if utm.get("utm_source") and utm["utm_source"] not in PLATFORMS:
        issues.append("utm_source is not a known platform")
    if utm.get("utm_medium") and utm["utm_medium"] != UTM_MEDIUM:
        issues.append(f"utm_medium must be {UTM_MEDIUM!r}")
    if urlsplit(url).fragment:
        issues.append("fragment present")
    if any(k.lower() in TRACKING_PARAMS for k, _ in parse_qsl(urlsplit(url).query)):
        issues.append("click-tracking parameter present")
    if allow_origins and _origin(destination) not in {normalize_origin(o) for o in allow_origins}:
        issues.append("destination origin not allowlisted")
    if not issues:
        try:
            canonical = build_utm_link(url, platform=utm["utm_source"], campaign=utm["utm_campaign"],
                                       content=utm.get("utm_content") or None,
                                       term=utm.get("utm_term") or None)
        except ValidationError as exc:
            issues.append(str(exc))
        else:
            if canonical != url:
                issues.append("not in canonical form; rebuild with utm-link")
    return issues


# ---------------------------------------------------------------------------
# 2. Versioned per-platform metric definitions
# ---------------------------------------------------------------------------

METRIC_DEFINITIONS_VERSION = "2026-10-08.2"
CANONICAL_METRICS = {
    "views": "Platform-counted plays/opens of the content; each platform defines a view differently.",
    "impressions": "Times the content was displayed on screen; not unique people.",
    "reach": "Estimated unique accounts that saw the content at least once.",
    "engaged_views": "Views meeting a platform-defined engagement threshold.",
    "watch_time": "Total time spent watching video content.",
    "replies": "Comments or replies directly on the content.",
    "shares": "Reshares, reposts or forwards of the content.",
    "saves": "Saves/bookmarks of the content by viewers.",
    "clicks": "Clicks on the outbound link (definition varies: some include any click).",
    "follower_change": "Net follower/subscriber change attributed to a window or content.",
    "profile_actions": "Profile visits or actions originating from the content.",
}
AVAILABILITY = ("api", "native_export", "unavailable", "unknown")
# Date on which every non-unknown entry below was fetched and checked against its source_url.
METRIC_DEFINITIONS_VERIFIED = "2026-10-08"
_UNKNOWN_DELAY = "Not verified; check current provider documentation."
_COUNTER_DELAY = "Public cumulative counter read at observation time; no reporting window."


def _m(field, availability, definition, denominator=None, delay=_UNKNOWN_DELAY, source=None, also=()):
    """One table entry; ``also`` lists further fetched sources quoted in ``definition``."""
    return {"native_field": field, "availability": availability, "definition": definition,
            "denominator": denominator, "reporting_delay": delay, "source_url": source,
            "additional_sources": list(also),
            "verified_date": None if availability == "unknown" else METRIC_DEFINITIONS_VERIFIED}


# Every recorded entry was fetched and checked on METRIC_DEFINITIONS_VERIFIED against its
# own source_url (quoted where marked). Any metric not listed for a platform reports
# availability ``unknown`` with no field, definition or verified_date: it is never guessed.
# For unknown entries the platform URL only says where to look; it is not evidence.
# Denominators are ReachWeaver's suggested rate bases, not provider statements.
_YT = "https://developers.google.com/youtube/analytics/metrics"
_YT_DELAY = "Analytics API report over the queried startDate..endDate; the metrics page states no latency."
_BSKY_POST = "https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/defs.json"
_BSKY_ACTOR = "https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/actor/defs.json"
_MASTODON = "https://docs.joinmastodon.org/entities/Status/"
_TG_MSG = "https://core.telegram.org/constructor/message"
_TG_BOT = "https://core.telegram.org/bots/api#message"
_LI_MEMBER = "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/post-statistics"
_LI_ORG = "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations/share-statistics"
_LI_DELAY = ("memberCreatorPostAnalytics: lifetime by default, or a dateRange (start inclusive, end exclusive); "
             "'best-effort accurate'. Needs r_member_postAnalytics consent (Community Management API).")
_LI_NO_DAILY = _LI_DELAY + " DAILY aggregation is not supported for this metric."
_X = "https://docs.x.com/x-api/fundamentals/metrics"
_X_PRIVATE = ("non_public_metrics: owner user context only (OAuth 1.0a or OAuth 2.0 user context) and only "
              "for posts created within the last 30 days.")
_THREADS = "https://developers.facebook.com/docs/threads/insights"
_IG = "https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights"
_IG_DELAY = ("Meta: data can be delayed up to 48 hours; unavailable insights return an empty data set, "
             "not 0; metrics are stored for up to 2 years.")
_FB = "https://developers.facebook.com/docs/graph-api/reference/insights"
_FB_DELAY = ("Lifetime post metric. The page says a number of Page Insights metrics are deprecated by "
             "June 15, 2026 without listing them; re-check before use.")
_TIKTOK = "https://developers.tiktok.com/doc/tiktok-api-v2-video-object"
_PIN = "https://raw.githubusercontent.com/pinterest/api-description/main/v5/openapi.yaml"
_PIN_DELAY = ("GET /pins/{pin_id}/analytics (OpenAPI 5.28.0): UTC start_date at most 90 days back, end_date "
              "at most 90 days after start_date; scopes boards:read and pins:read.")
_PIN_NOTE = "Pinterest metric_types enum value; the API spec gives the name only, no definition text."

_PLATFORM_METRICS = {
    "youtube": (_YT, {
        "views": _m("views", "api", "'This metric represents different numbers in different types of "
                    "reports.' Compare only within one report type.", None, _YT_DELAY, _YT),
        "engaged_views": _m("engagedViews", "api", "Times the channel's videos were viewed past the first "
                            "frame, or the user clicked/tapped to play.", "views", _YT_DELAY, _YT),
        "watch_time": _m("estimatedMinutesWatched", "api", "Minutes users watched videos for the channel, "
                         "content owner, video or playlist.", "views", _YT_DELAY, _YT),
        "replies": _m("comments", "api", "Times users commented on a video.", "views", _YT_DELAY, _YT),
        "shares": _m("shares", "api", "Times users shared a video through the Share button.", "views",
                     _YT_DELAY, _YT),
        "follower_change": _m("subscribersGained - subscribersLost", "api",
                              "Derived: times users subscribed minus times users unsubscribed in the window.",
                              None, _YT_DELAY, _YT),
        "impressions": _m("Impressions (YouTube Studio)", "native_export",
                          "Thumbnail shown for more than 1 second with at least 50% visible, on YouTube "
                          "surfaces only; Studio Reach tab (video) or Content tab (channel). The Analytics API "
                          "metrics page lists no thumbnail-impression metric.", None,
                          "YouTube Studio date range chosen by the owner.",
                          "https://support.google.com/youtube/answer/9314486?hl=en", (_YT,)),
    }),
    "bluesky": (_BSKY_POST, {
        "views": _m(None, "unavailable", "postView has no view count (only reply, repost, like, quote and "
                    "bookmark counts).", None, _COUNTER_DELAY, _BSKY_POST),
        "impressions": _m(None, "unavailable", "postView has no impression count.", None, _COUNTER_DELAY,
                          _BSKY_POST),
        "reach": _m(None, "unavailable", "postView has no reach estimate.", None, _COUNTER_DELAY, _BSKY_POST),
        "replies": _m("replyCount", "api", "Public reply counter on app.bsky.feed.defs#postView.", None,
                      _COUNTER_DELAY, _BSKY_POST),
        "shares": _m("repostCount", "api", "Public repost counter on postView (quotes are quoteCount).", None,
                     _COUNTER_DELAY, _BSKY_POST),
        "saves": _m("bookmarkCount", "api", "Bookmark counter on postView.", None, _COUNTER_DELAY, _BSKY_POST),
        "follower_change": _m("followersCount", "api",
                              "Derive by differencing two profileViewDetailed follower totals.", None,
                              _COUNTER_DELAY, _BSKY_ACTOR),
    }),
    "mastodon": (_MASTODON, {
        "views": _m(None, "unavailable", "The Status entity has no view count.", None, _COUNTER_DELAY,
                    _MASTODON),
        "impressions": _m(None, "unavailable", "The Status entity has no impression count.", None,
                          _COUNTER_DELAY, _MASTODON),
        "reach": _m(None, "unavailable", "The Status entity has no reach estimate.", None, _COUNTER_DELAY,
                    _MASTODON),
        "replies": _m("replies_count", "api", "'How many replies this status has received.'", None,
                      _COUNTER_DELAY, _MASTODON),
        "shares": _m("reblogs_count", "api", "'How many boosts this status has received.'", None,
                     _COUNTER_DELAY, _MASTODON),
        "follower_change": _m("followers_count", "api", "'The reported followers of this profile.' Derive by "
                              "differencing two account totals.", None, _COUNTER_DELAY,
                              "https://docs.joinmastodon.org/entities/Account/"),
    }),
    "telegram": (_TG_MSG, {
        "views": _m("views", "api", "'View count for channel posts' on the MTProto client API message "
                    "constructor. The Bot API Message object has no views field.", None, _COUNTER_DELAY,
                    _TG_MSG, (_TG_BOT,)),
        "shares": _m("forwards", "api", "'Forward counter' on the MTProto client API message constructor; not "
                     "in the Bot API Message object.", "views", _COUNTER_DELAY, _TG_MSG, (_TG_BOT,)),
    }),
    # Member posts (what the LinkedIn member adapter publishes). Organization pages use
    # organizationalEntityShareStatistics; its equivalent field is named in the definition.
    "linkedin": (_LI_MEMBER, {
        "impressions": _m("IMPRESSION", "api", "Number of impressions of the requested entity. Organization "
                          "shares: impressionCount.", None, _LI_DELAY, _LI_MEMBER, (_LI_ORG,)),
        "reach": _m("MEMBERS_REACHED", "api", "Number of members reached, post viewers or unique impressions "
                    "count. Organization shares: uniqueImpressionsCount ('Number of unique impressions').",
                    None, _LI_NO_DAILY, _LI_MEMBER, (_LI_ORG,)),
        "replies": _m("COMMENT", "api", "Number of comments of the requested entity (aggregated q=me values "
                      "are documented as not consistent with the UI). Organization shares: commentCount.",
                      "impressions", _LI_DELAY, _LI_MEMBER, (_LI_ORG,)),
        "shares": _m("RESHARE", "api", "Number of reshares/reposts of the requested entity. Organization "
                     "shares: shareCount.", "impressions", _LI_DELAY, _LI_MEMBER, (_LI_ORG,)),
        "saves": _m("POST_SAVE", "api", "Times the requested entity was saved (API version 202604 and later).",
                    "impressions", _LI_DELAY, _LI_MEMBER),
        "clicks": _m("LINK_CLICKS", "api", "Number of link clicks on the requested entity (202604+). "
                     "Organization clickCount is 'Number of clicks', not documented as link-only, so the two "
                     "are not comparable.", "impressions", _LI_NO_DAILY, _LI_MEMBER, (_LI_ORG,)),
        "follower_change": _m("FOLLOWER_GAINED_FROM_CONTENT", "api", "Followers gained from the requested "
                              "entity (gains only, not net; 202604+).", None, _LI_NO_DAILY, _LI_MEMBER),
        "profile_actions": _m("PROFILE_VIEW_FROM_CONTENT", "api", "Profile views driven by the requested "
                              "entity (202604+).", "impressions", _LI_NO_DAILY, _LI_MEMBER),
    }),
    "x": (_X, {
        "impressions": _m("public_metrics.impression_count", "api", "Count of times the post appeared on a "
                          "user's screen; not unique.", None, _COUNTER_DELAY, _X),
        "replies": _m("public_metrics.reply_count", "api", "Replies to the post.", "impressions",
                      _COUNTER_DELAY, _X),
        "shares": _m("public_metrics.retweet_count", "api", "Reposts; does not include quote posts "
                     "(quote_count).", "impressions", _COUNTER_DELAY, _X),
        "saves": _m("public_metrics.bookmark_count", "api", "Times the post has been bookmarked.",
                    "impressions", _COUNTER_DELAY, _X),
        "clicks": _m("non_public_metrics.url_link_clicks", "api", "URL clicks on the post.", "impressions",
                     _X_PRIVATE, _X),
        "profile_actions": _m("non_public_metrics.user_profile_clicks", "api", "Profile clicks from the post.",
                              "impressions", _X_PRIVATE, _X),
    }),
    "threads": (_THREADS, {
        "views": _m("views", "api", "'The number of times your post was played or displayed.' Meta marks it "
                    "in development.", None, _UNKNOWN_DELAY, _THREADS),
        "replies": _m("replies", "api", "Replies on the post: all replies for a root post, direct replies only "
                      "for a reply; nested replies are excluded.", "views", _UNKNOWN_DELAY, _THREADS),
        "shares": _m("reposts", "api", "Times the post was reposted (quotes and shares are separate fields).",
                     "views", _UNKNOWN_DELAY, _THREADS),
    }),
    "instagram": (_IG, {
        "views": _m("views", "api", "'Total number of times IG Media has been played on Instagram.' Meta marks "
                    "it in development. FEED, REELS, STORY.", None, _IG_DELAY, _IG),
        "reach": _m("reach", "api", "Unique Instagram users that saw the media at least once (estimated). "
                    "FEED, REELS, STORY.", None, _IG_DELAY, _IG),
        "saves": _m("saved", "api", "Times the media was saved by an Instagram user. FEED, REELS.", "reach",
                    _IG_DELAY, _IG),
        "shares": _m("shares", "api", "Shares of the media. FEED, REELS, STORY.", "reach", _IG_DELAY, _IG),
        "replies": _m("comments", "api", "Comments on the media object. FEED, REELS.", "reach", _IG_DELAY, _IG),
        "profile_actions": _m("profile_visits", "api", "Times the profile was visited. FEED, STORY.", "reach",
                              _IG_DELAY, _IG),
        "impressions": _m("impressions", "unavailable", "Deprecated for media created after July 2, 2024 "
                          "(still returned for older media); use views.", None, _IG_DELAY, _IG),
    }),
    "facebook": (_FB, {
        "views": _m("post_media_view", "api", "'The number of times your content was played or displayed.' "
                    "post_impressions is deprecated from Graph API v25.", None, _FB_DELAY, _FB),
        "reach": _m("post_total_media_view_unique", "api", "'The number of total unique media viewers for FB "
                    "post.' post_impressions_unique is deprecated from Graph API v25.", None, _FB_DELAY, _FB),
    }),
    "tiktok": (_TIKTOK, {
        "views": _m("view_count", "api", "'Number of views of the video' (Display API video object).", None,
                    _COUNTER_DELAY, _TIKTOK),
        "replies": _m("comment_count", "api", "'Number of comments on the video'.", "views", _COUNTER_DELAY,
                      _TIKTOK),
        "shares": _m("share_count", "api", "'Number of shares of the video'.", "views", _COUNTER_DELAY, _TIKTOK),
    }),
    # The fetched page documents no num_comments field on submissions (only a preference/sort option).
    "reddit": ("https://www.reddit.com/dev/api/", {}),
    "pinterest": (_PIN, {
        "impressions": _m("IMPRESSION", "api", _PIN_NOTE, None, _PIN_DELAY, _PIN),
        "saves": _m("SAVE", "api", _PIN_NOTE, "impressions", _PIN_DELAY, _PIN),
        "clicks": _m("OUTBOUND_CLICK", "api", _PIN_NOTE, "impressions", _PIN_DELAY, _PIN),
        "replies": _m("TOTAL_COMMENTS", "api", _PIN_NOTE, "impressions", _PIN_DELAY, _PIN),
        "profile_actions": _m("PROFILE_VISIT", "api", _PIN_NOTE + " Pins created before 2023-03-20: Idea Pins "
                              "only.", "impressions", _PIN_DELAY, _PIN),
        "views": _m("VIDEO_MRC_VIEW", "api", "'Video views' (video Pins only).", None, _PIN_DELAY, _PIN),
        "watch_time": _m("VIDEO_V50_WATCH_TIME", "api", "'Total play time' (video Pins only).", "views",
                         _PIN_DELAY, _PIN),
    }),
    # The Forem v1 reference shows no article-level page_views_count or comments_count field.
    "devto": ("https://developers.forem.com/api/v1", {}),
    "hashnode": ("https://apidocs.hashnode.com/", {}),
}


def metric_definition(platform: str, metric: str) -> dict:
    """Definition, native field, denominator and availability; ``unknown`` if unverified."""
    if platform not in PLATFORMS:
        raise ValidationError(f"Unknown platform: {platform}")
    if metric not in CANONICAL_METRICS:
        raise ValidationError(f"Unknown metric {metric}; use one of {', '.join(CANONICAL_METRICS)}")
    source, metrics = _PLATFORM_METRICS.get(platform, (None, {}))
    entry = metrics.get(metric) or _m(None, "unknown", None)
    return {"version": METRIC_DEFINITIONS_VERSION, "platform": platform, "metric": metric,
            "canonical_definition": CANONICAL_METRICS[metric], **entry,
            "source_url": entry["source_url"] or source}


def metric_definitions(platform: str) -> list[dict]:
    return [metric_definition(platform, metric) for metric in CANONICAL_METRICS]


# ---------------------------------------------------------------------------
# 3. Conversion attribution from local analytics and GitHub traffic files
# ---------------------------------------------------------------------------

ANALYTICS_COLUMNS = ("date", "utm_source", "utm_medium", "utm_campaign", "utm_content", "sessions")
SOCIAL_REFERRERS = {
    "t.co": "x", "x.com": "x", "twitter.com": "x", "linkedin.com": "linkedin", "lnkd.in": "linkedin",
    "bsky.app": "bluesky", "mastodon.social": "mastodon", "reddit.com": "reddit",
    "news.ycombinator.com": "hackernews", "youtube.com": "youtube", "youtu.be": "youtube",
    "facebook.com": "facebook", "instagram.com": "instagram", "threads.net": "threads",
    "threads.com": "threads", "dev.to": "devto", "hashnode.com": "hashnode", "t.me": "telegram",
    "tiktok.com": "tiktok", "pinterest.com": "pinterest", "pin.it": "pinterest",
}
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


def referrer_platform(value: str | None) -> str | None:
    """Map a referrer (domain or URL) to a social platform; ``None`` if not social."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().lower()
    host = (urlsplit(text).hostname if "://" in text else text.split("/")[0]) or ""
    host = host.removeprefix("www.")
    for domain, platform in SOCIAL_REFERRERS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return None


def _count(value, label: str, line: int):
    if value is None or str(value).strip() == "":
        return None  # missing stays unknown, never zero
    text = str(value).strip()
    if not (text.isascii() and text.isdecimal()):
        raise ValidationError(f"Analytics line {line}: {label} must be a nonnegative integer or blank")
    return int(text)


def report_landing_page(value: str | None) -> str:
    """Return a landing URL/path that is safe to print in a report.

    Analytics exports can carry magic-link tokens, password-reset codes or subscriber
    e-mail addresses in the query string (``?token=``, ``?email=``) or the fragment
    (``#access_token=``). Only ``scheme://host[:port]/path`` (or a bare path) and the
    five canonical ``utm_*`` parameters are kept. User info, every other query
    parameter and the fragment are dropped, and the remainder goes through
    ``privacy.redact`` (secret env values, token shapes, e-mail addresses) in case a
    token or address sits in the path itself. Idempotent.
    """
    text = (value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:  # malformed authority (bad port or IPv6 literal): keep nothing from it
        return ""
    netloc = ""
    if host:
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc = f"{netloc}:{port}"
    utm = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() in UTM_KEYS]
    safe = urlunsplit((parsed.scheme.lower(), netloc, parsed.path, urlencode(utm), ""))
    return redact(safe, emails=True)


def load_analytics_csv(path) -> list[dict]:
    """Parse an owner-exported website-analytics CSV (aggregated rows, no personal data)."""
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fields = {name.strip().lower() for name in reader.fieldnames or []}
        missing = [c for c in ANALYTICS_COLUMNS if c not in fields]
        if missing or not fields & {"landing_url", "page"}:
            raise ValidationError("Analytics CSV needs date, landing_url or page, utm_source, utm_medium, "
                                  "utm_campaign, utm_content, sessions (conversions, referrer optional); "
                                  f"missing: {', '.join(missing) or 'landing_url/page'}")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            try:
                day = date.fromisoformat(row["date"])
            except ValueError as exc:
                raise ValidationError(f"Analytics line {line}: date must be YYYY-MM-DD") from exc
            rows.append({
                "line": line, "date": day.isoformat(),
                "landing": report_landing_page(row.get("landing_url") or row.get("page") or ""),
                **{key: row.get(key, "") for key in UTM_KEYS[:4]},
                "referrer": row.get("referrer", ""),
                "sessions": _count(row.get("sessions"), "sessions", line),
                "conversions": _count(row.get("conversions"), "conversions", line),
            })
    return rows


def _nonneg(value, label):
    if type(value) is not int or value < 0:
        raise ValidationError(f"GitHub traffic {label} must be a nonnegative integer")
    return value


def load_github_traffic(path) -> dict:
    """Parse a saved ``traffic/views`` object or ``traffic/popular/referrers`` list."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        referrers = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("referrer"), str):
                raise ValidationError("GitHub referrers must be a list of {referrer,count,uniques}")
            referrers.append({"referrer": item["referrer"], "count": _nonneg(item.get("count"), "count"),
                              "uniques": _nonneg(item.get("uniques"), "uniques")})
        return {"kind": "referrers", "referrers": referrers}
    if isinstance(data, dict) and isinstance(data.get("views"), list):
        days = []
        for item in data["views"]:
            if not isinstance(item, dict) or not isinstance(item.get("timestamp"), str):
                raise ValidationError("GitHub views entries need timestamp, count, uniques")
            days.append({"timestamp": item["timestamp"], "count": _nonneg(item.get("count"), "count"),
                         "uniques": _nonneg(item.get("uniques"), "uniques")})
        return {"kind": "views", "count": _nonneg(data.get("count"), "count"),
                "uniques": _nonneg(data.get("uniques"), "uniques"), "views": days}
    raise ValidationError("Unrecognized GitHub traffic JSON: expected traffic/views or popular/referrers output")


def _sum_known(values) -> tuple[int | None, int]:
    known = [v for v in values if v is not None]
    return (sum(known) if known else None), len(known)


def _bucket(rows: list[dict], *, fields: tuple[str, ...] = ("sessions", "conversions"), **extra) -> dict:
    bucket: dict[str, Any] = {**extra, "rows": len(rows)}
    for field in fields:
        bucket[field], bucket[f"{field}_known_rows"] = _sum_known(r.get(field) for r in rows)
    # Re-sanitized here too: callers may pass rows that did not come from load_analytics_csv.
    bucket["landing_pages"] = sorted({page for page in (report_landing_page(r.get("landing")) for r in rows) if page})
    return bucket


def _safe_slug(value) -> str | None:
    try:
        return _slug(value, "value")
    except ValidationError:
        return None


def _join_context(con) -> dict:
    """Posts, campaign variants and published campaigns, read with SELECT only."""
    posts = {row["id"]: row for row in list_posts(con)}
    variants = {}
    for row in con.execute("SELECT campaign_id,variant_id,post_id FROM campaign_variants"):
        variants[(_safe_slug(row["campaign_id"]), _safe_slug(row["variant_id"]))] = row["post_id"]
    published = {pid: p for pid, p in posts.items() if p["status"] == "published"}
    campaign_platforms: dict[str, set] = {}
    for post in published.values():
        if _safe_slug(post["campaign"]):
            campaign_platforms.setdefault(cast(str, _safe_slug(post["campaign"])), set()).add(post["platform"])
    return {"posts": posts, "variants": variants, "published": published, "campaign_platforms": campaign_platforms}


def _classify(rows: list[dict], ctx: dict) -> tuple[dict, dict, dict, dict]:
    """Split rows into post, campaign, referrer-only and unattributed groups (UTM join, not causal)."""
    posts, variants = ctx["posts"], ctx["variants"]
    campaign_platforms: dict[str, set] = ctx["campaign_platforms"]
    by_post: dict[Any, list[dict]]
    by_campaign: dict[str, list[dict]]
    by_referrer: dict[str, list[dict]]
    unattributed: dict[str, list[dict]]
    by_post, by_campaign, by_referrer, unattributed = {}, {}, {}, {}
    for row in rows:
        source, medium = row["utm_source"].lower(), row["utm_medium"].lower()
        camp, content = _safe_slug(row["utm_campaign"]), _safe_slug(row["utm_content"])
        if not any((source, medium, camp, content)):
            platform = referrer_platform(row["referrer"])
            if platform:
                by_referrer.setdefault(platform, []).append(row)
            else:
                unattributed.setdefault("no_utm", []).append(row)
            continue
        post_id = None
        if content:
            post_id = variants.get((camp, content)) or (content if content in posts else None)
        if post_id:
            post = posts[post_id]
            if post["status"] != "published":
                reason = "utm_matches_unpublished_post"
            elif source != post["platform"]:
                reason = "utm_conflict:utm_source"
            elif medium != UTM_MEDIUM:
                reason = "utm_conflict:utm_medium"
            elif _safe_slug(post["campaign"]) and camp != _safe_slug(post["campaign"]):
                reason = "utm_conflict:utm_campaign"
            else:
                by_post.setdefault(post_id, []).append(row)
                continue
            unattributed.setdefault(reason, []).append(row)
        elif camp in campaign_platforms:
            if source in campaign_platforms[camp] and medium == UTM_MEDIUM:
                by_campaign.setdefault(camp, []).append(row)
            else:
                unattributed.setdefault("utm_conflict:campaign_source_or_medium", []).append(row)
        else:
            unattributed.setdefault("unknown_utm", []).append(row)
    return by_post, by_campaign, by_referrer, unattributed


def _joined(ctx: dict, groups: tuple[dict, dict, dict, dict], fields: tuple[str, ...], referrer_level: str) -> dict:
    by_post, by_campaign, by_referrer, unattributed = groups
    posts = ctx["posts"]
    all_unattributed = [r for rows in unattributed.values() for r in rows]
    summary = {**_bucket(all_unattributed, fields=fields),
               "by_reason": {k: len(v) for k, v in sorted(unattributed.items())}}
    summary.pop("landing_pages")
    return {
        "posts": [_bucket(rows, fields=fields, post_id=pid, platform=posts[pid]["platform"],
                          account_id=posts[pid]["account_id"], campaign=posts[pid]["campaign"])
                  for pid, rows in sorted(by_post.items())],
        "campaigns": [_bucket(rows, fields=fields, campaign=c, level="campaign_only_no_post_id")
                      for c, rows in sorted(by_campaign.items())],
        "referrer": [_bucket(rows, fields=fields, platform=p, level=referrer_level)
                     for p, rows in sorted(by_referrer.items())],
        "unattributed": summary,
    }


# ---------------------------------------------------------------------------
# 3a. Owner-exported GA4 and Search Console files (parsed by web_analytics.py)
# ---------------------------------------------------------------------------

GA4_FIELDS = ("sessions", "engaged_sessions", "key_events")


def _ga4_section(ctx: dict, exports: list[dict]) -> dict:
    """GA4 export rows joined to posts exactly like analytics rows, kept in their own section."""
    rows = [row for export in exports for row in export["rows"]]
    groups = _classify(rows, ctx)
    joined = _joined(ctx, groups, GA4_FIELDS, "session_source_domain_not_post")
    ranges = [export["date_range"] for export in exports]
    window = None
    if ranges and all(ranges):
        window = [min(r[0] for r in ranges if r), max(r[1] for r in ranges if r)]
    published = ctx["published"]
    gaps: list[dict] = []
    no_rows = sorted(set(published) - set(groups[0]))
    if no_rows:
        gaps.append({"gap": "published_posts_without_matching_ga4_rows", "post_ids": no_rows,
                     "meaning": "No GA4 row carried this post's id: unknown traffic, untagged link, a GA4 "
                                "threshold or outside the export. NOT zero."})
    if window is None:
        gaps.append({"gap": "ga4_date_range_unknown",
                     "meaning": "The export has no # Start/End date lines and no Date column; the period is unknown."})
    else:
        late = sorted(pid for pid, p in published.items()
                      if p["send_started_at"] and p["send_started_at"][:10] > window[1])
        if late:
            gaps.append({"gap": "published_after_ga4_window", "post_ids": late, "ga4_last_date": window[1]})
    dimensions = set.intersection(*(set(export["dimensions"]) for export in exports))
    if "content" not in dimensions:
        gaps.append({"gap": "ga4_manual_ad_content_column_missing",
                     "meaning": "Without Session manual ad content (utm_content) no row can be credited to a post; "
                                "campaign-level matches only."})
    if "campaign" not in dimensions:
        gaps.append({"gap": "ga4_campaign_column_missing"})
    for field in GA4_FIELDS[1:]:
        if any(field not in export["metrics"] for export in exports):
            gaps.append({"gap": f"ga4_{field}_column_missing", "meaning": f"{field} is unknown, not zero."})
    for field in GA4_FIELDS:
        unknown = sum(1 for r in rows if r[field] is None)
        if unknown:
            gaps.append({"gap": f"ga4_{field}_unknown_rows", "rows": unknown})
    return {
        "source": "owner_supplied_ga4_csv_export",
        "date_range": window,
        "rows": len(rows),
        "files": [{"date_range": list(export["date_range"]) if export["date_range"] else None,
                   "date_range_source": export["date_range_source"], "rows": len(export["rows"]),
                   "dimensions": export["dimensions"], "metrics": export["metrics"],
                   "ignored_columns": export["ignored_columns"], "export_totals_row": export["totals_row"],
                   "trailing_lines_ignored": export["trailing_lines_ignored"]} for export in exports],
        "attributed": {"posts": joined["posts"], "campaigns": joined["campaigns"]},
        "referrer_only": joined["referrer"],
        "unattributed": joined["unattributed"],
        "coverage_gaps": gaps,
        "warnings": [
            "Owner-supplied GA4 export data for the date range above; ReachWeaver did not fetch or verify it.",
            "Attribution is a session source/medium/campaign/ad-content join, NOT causation, incrementality or reach.",
            "GA4 sessions are not comparable with sessions from --analytics or another tool; never add them up.",
            "Untagged GA4 rows such as t.co / referral are domain-level; they are never credited to a post.",
            "Blank cells are unknown and excluded from sums, never zero. The export's totals row is never joined.",
        ],
    }


_TRAILING = ".,;:!?"
GSC_TOP_ROWS = 25


def _page_key(url: str) -> tuple[str, str] | None:
    """``(host, path)`` for matching a Search Console page to a linked destination; query dropped."""
    try:
        parts = urlsplit(url.strip().rstrip(_TRAILING))
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return host, (parts.path.rstrip("/") or "/")


def _gsc_row(row: dict, name: str, value: str) -> dict:
    return {name: value, "clicks": row["clicks"], "impressions": row["impressions"], "ctr": row["ctr"],
            "position": row["position"]}


def _by_clicks(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-(r["clicks"] if r["clicks"] is not None else -1), r["key"]))


def _gsc_section(ctx: dict, tables: dict) -> dict:
    """Search-side context for the pages posts link to; never credited to a post."""
    pages, queries = tables.get("pages"), tables.get("queries")
    dates, filters = tables.get("dates"), tables.get("filters")
    filter_values = {" ".join(r["filter"].lower().split()): r["value"] for r in (filters or {}).get("rows", [])}
    days = sorted(r["key"] for r in (dates or {}).get("rows", []))
    window = [days[0], days[-1]] if days else None
    linked: dict[tuple[str, str], set] = {}
    for pid, post in ctx["published"].items():
        for url in _URL.findall(post["content"] or ""):
            key = _page_key(url)
            if key:
                linked.setdefault(key, set()).add(pid)
    matched, gsc_keys = [], set()
    for row in (pages or {}).get("rows", []):
        key = _page_key(row["key"])
        if key is None:
            continue
        gsc_keys.add(key)
        if key in linked:
            matched.append({**_gsc_row(row, "page", report_landing_page(row["key"])),
                            "linked_from_post_ids": sorted(linked[key]), "level": "page_search_context_not_post"})
    gaps: list[dict] = []
    if pages is None:
        gaps.append({"gap": "gsc_pages_table_not_supplied"})
    else:
        hosts = {host for host, _ in gsc_keys}
        missing = sorted((key, ids) for key, ids in linked.items() if key[0] in hosts and key not in gsc_keys)
        if missing:
            gaps.append({"gap": "post_destinations_not_in_gsc_pages",
                         "pages": [{"page": f"{host}{path}", "post_ids": sorted(ids)} for (host, path), ids in missing],
                         "meaning": "Not in the exported Pages rows (exports keep at most 1,000 rows): unknown, "
                                    "NOT zero search clicks."})
    if queries is None:
        gaps.append({"gap": "gsc_queries_table_not_supplied"})
    if window is None:
        gaps.append({"gap": "gsc_date_range_unknown",
                     "meaning": "No Dates table: the period is only the Filters date label, if one was supplied."})
    dates_total = None
    if dates is not None:
        clicks, clicks_known = _sum_known(r["clicks"] for r in dates["rows"])
        impressions, impressions_known = _sum_known(r["impressions"] for r in dates["rows"])
        dates_total = {"clicks": clicks, "clicks_known_days": clicks_known, "impressions": impressions,
                       "impressions_known_days": impressions_known, "days": len(dates["rows"]),
                       "level": "property_by_date_not_post"}
    top_pages = _by_clicks(pages["rows"])[:GSC_TOP_ROWS] if pages else []
    top_queries = _by_clicks(queries["rows"])[:GSC_TOP_ROWS] if queries else []
    return {
        "source": "owner_supplied_search_console_performance_export",
        "date_range": window, "date_range_source": "dates_table" if window else None,
        "date_filter_label": filter_values.get("date"), "search_type": filter_values.get("search type"),
        "tables": {kind: len(tables[kind]["rows"]) if kind in tables else None
                   for kind in ("pages", "queries", "dates", "filters")},
        "dates_total": dates_total,
        "pages_linked_from_posts": sorted(matched, key=lambda r: r["page"]),
        "top_pages": [_gsc_row(r, "page", report_landing_page(r["key"])) for r in top_pages],
        "top_queries": [_gsc_row(r, "query", redact(r["key"], emails=True)[:200]) for r in top_queries],
        "coverage_gaps": gaps,
        "warnings": [
            "Owner-supplied Search Console export data for the date range above; ReachWeaver did not fetch or "
            "verify it.",
            "Search clicks and impressions are Google Search activity, not social traffic: never credited to a "
            "post and NOT evidence that a post caused search visits or ranking.",
            "Exports keep at most 1,000 rows per table; a page or query missing from the export is unknown, not zero.",
            "Search Console exports values shown as ~ or - as 0, so a 0 can mean not available; Position 0 is null.",
            "Pages rows are aggregated by page and the Dates table by property; do not add page rows up to a "
            "site total.",
        ],
    }


def attribution_report(con, analytics_rows: list[dict] | None, github: list[dict] | None = None, *,
                       ga4: list[dict] | None = None, gsc: dict | None = None) -> dict:
    """Join tagged analytics rows to local posts (read-only); never claim causation.

    ``analytics_rows`` is ``None`` when no generic analytics CSV was supplied. ``ga4`` is a
    list of parsed GA4 exports and ``gsc`` the combined Search Console tables
    (``web_analytics.combine_ga4`` / ``combine_gsc``); each gets its own section.
    """
    ctx = _join_context(con)
    published = ctx["published"]
    rows_in = analytics_rows or []
    groups = _classify(rows_in, ctx)
    joined = _joined(ctx, groups, ("sessions", "conversions"), "referrer_domain_not_post")

    social_gh: list[dict]
    other_gh: list[dict]
    github_views, github_referrers, social_gh, other_gh = None, None, [], []
    for item in github or []:
        if item["kind"] == "views":
            stamps = sorted(day["timestamp"] for day in item["views"])
            github_views = {"count": item["count"], "uniques": item["uniques"], "days": len(stamps),
                            "first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None,
                            "level": "repository_total_not_post"}
        else:
            github_referrers = True
            for ref in item["referrers"]:
                platform = referrer_platform(ref["referrer"])
                (social_gh if platform else other_gh).append({**ref, "platform": platform})

    gaps: list[dict] = []
    dates = sorted(r["date"] for r in rows_in)
    if analytics_rows is None:
        gaps.append({"gap": "analytics_csv_not_supplied"})
    else:
        no_rows = sorted(set(published) - set(groups[0]))
        if no_rows:
            gaps.append({"gap": "published_posts_without_matching_utm_rows", "post_ids": no_rows,
                         "meaning": "No tagged rows found: unknown traffic, untagged link or outside window. NOT zero."})
    untagged = sorted(pid for pid, p in published.items()
                      if any("utm_content" not in parse_utm(u) for u in _URL.findall(p["content"] or "")))
    if untagged:
        gaps.append({"gap": "published_posts_with_untagged_links", "post_ids": untagged})
    if dates:
        late = sorted(pid for pid, p in published.items()
                      if p["send_started_at"] and p["send_started_at"][:10] > dates[-1])
        if late:
            gaps.append({"gap": "published_after_analytics_window", "post_ids": late,
                         "analytics_last_date": dates[-1]})
    unknown_conv = sum(1 for r in rows_in if r["conversions"] is None)
    if unknown_conv:
        gaps.append({"gap": "conversions_unknown_rows", "rows": unknown_conv})
    unknown_sessions = sum(1 for r in rows_in if r["sessions"] is None)
    if unknown_sessions:
        gaps.append({"gap": "sessions_unknown_rows", "rows": unknown_sessions})
    if github_views is None:
        gaps.append({"gap": "github_views_not_supplied"})
    if github_referrers is None:
        gaps.append({"gap": "github_referrers_not_supplied"})

    ga4_section = _ga4_section(ctx, ga4) if ga4 else None
    gsc_section = _gsc_section(ctx, gsc) if gsc else None
    return {
        "inputs": {"analytics_rows": len(rows_in),
                   "analytics_date_range": [dates[0], dates[-1]] if dates else None,
                   "github_files": len(github or []),
                   "ga4_files": len(ga4 or []),
                   "ga4_date_range": ga4_section["date_range"] if ga4_section else None,
                   "gsc_files": len(gsc or {}),
                   "gsc_date_range": gsc_section["date_range"] if gsc_section else None},
        "attributed": {"posts": joined["posts"], "campaigns": joined["campaigns"]},
        "referrer_only": {"analytics": joined["referrer"], "github": social_gh},
        "unattributed": joined["unattributed"],
        "github": {"views": github_views, "non_social_referrers": other_gh},
        "ga4": ga4_section,
        "search_console": gsc_section,
        "coverage_gaps": gaps,
        "warnings": [
            "Attribution is a UTM/referrer join, NOT causation or incrementality.",
            "Referrer-only rows are domain-level; they cannot be credited to a specific post.",
            "GitHub traffic API data covers only the most recent 14 days and is repository-level.",
            "Blank sessions/conversions are unknown and excluded from sums, never zero.",
            "Analytics tools differ in session and conversion definitions; do not mix exports.",
        ],
    }


# ---------------------------------------------------------------------------
# 3b. Per-post outcomes for growth readouts (issue #16 R4)
# ---------------------------------------------------------------------------

# Qualified growth signals read from a saved ``attribution-report --json`` file instead of a
# native snapshot: the per-post field each one takes.
ATTRIBUTION_OUTCOMES = {"docs_visits": "sessions", "repo_stars_utm": "conversions"}
ATTRIBUTION_MAX_BYTES = 8 * 1024 * 1024


def _optional_count(value, where: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValidationError(f"{where} must be a nonnegative integer or null")
    return value


def load_attribution_outcomes(path) -> dict:
    """Per-post attributed sessions/conversions from a saved ``attribution-report --json`` file.

    Only ``attributed.posts`` (exact UTM matches with a post id) and
    ``inputs.analytics_date_range`` are read. Campaign-level and referrer-only rows cannot be
    credited to a post and are ignored. A null count stays None (unknown), never 0. Parsing
    only: no database, no network. Errors never echo the file's content.
    """
    file = Path(path)
    try:
        if file.stat().st_size > ATTRIBUTION_MAX_BYTES:
            raise ValidationError("--attribution file is larger than 8 MiB")
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError("--attribution file cannot be read") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"--attribution is not valid JSON: {exc.msg}") from None
    attributed = data.get("attributed") if isinstance(data, dict) else None
    posts_in = attributed.get("posts") if isinstance(attributed, dict) else None
    if not isinstance(posts_in, list):
        raise ValidationError("--attribution must be saved 'attribution-report --json' output (attributed.posts)")
    posts: dict[str, dict] = {}
    for index, item in enumerate(posts_in):
        where = f"attributed.posts[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("post_id"), str) or not item["post_id"].strip():
            raise ValidationError(f"{where} needs a post_id")
        if item["post_id"] in posts:
            raise ValidationError(f"{where}: post {item['post_id']} is listed twice")
        if not isinstance(item.get("platform"), str) or not isinstance(item.get("account_id"), str):
            raise ValidationError(f"{where} needs platform and account_id")
        posts[item["post_id"]] = {"platform": item["platform"], "account_id": item["account_id"],
                                  "sessions": _optional_count(item.get("sessions"), f"{where}.sessions"),
                                  "conversions": _optional_count(item.get("conversions"), f"{where}.conversions")}
    inputs = data.get("inputs")
    window = inputs.get("analytics_date_range") if isinstance(inputs, dict) else None
    date_range = None
    if window is not None:
        if not isinstance(window, list) or len(window) != 2:
            raise ValidationError("inputs.analytics_date_range must be [first, last] dates or null")
        first, last = (_day(value, "inputs.analytics_date_range") for value in window)
        if last < first:
            raise ValidationError("inputs.analytics_date_range ends before it starts")
        date_range = (first, last)
    return {"posts": posts, "analytics_date_range": date_range}


def attributed_outcome(outcomes: dict, post, signal: str, *, age_hours: int) -> tuple[int | None, str | None]:
    """``(count, None)`` for one published post, or ``(None, reason)`` when it is unknown.

    The count is the post's attributed ``sessions`` (``docs_visits``) or ``conversions``
    (``repo_stars_utm``) over the analytics export window. The post must have been sent on
    or after the window's first date, and the window must run at least ``age_hours`` (rounded
    up to whole days) past the send date, so every counted post had at least that long to
    collect visits. A post with no exact UTM row is unknown, not 0: no tagged row can also
    mean an untagged link or a gap in the export.
    """
    field = ATTRIBUTION_OUTCOMES.get(signal)
    if field is None:
        raise ValidationError(f"{signal} is not read from attribution-report; use one of "
                              f"{', '.join(ATTRIBUTION_OUTCOMES)}")
    window = outcomes["analytics_date_range"]
    if window is None:
        return None, "no_analytics_window"
    try:
        sent = date.fromisoformat(str(post["send_started_at"])[:10])
    except ValueError:
        return None, "invalid_send_timestamp"
    if sent < window[0]:
        return None, "sent_before_analytics_window"
    if (window[1] - sent).days < -(-age_hours // 24):
        return None, "analytics_window_shorter_than_post_age"
    entry = outcomes["posts"].get(post["id"])
    if entry is None:
        return None, "no_attributed_rows"
    if entry["platform"] != post["platform"] or entry["account_id"] != post["account_id"]:
        return None, "attribution_cohort_mismatch"
    if entry[field] is None:
        return None, f"{field}_unknown"
    return entry[field], None


# ---------------------------------------------------------------------------
# 4. SEO repository integration contract (issue #25)
# ---------------------------------------------------------------------------

SEO_SCHEMA_VERSION = 1
CAMPAIGN_TYPES = ("release", "paper", "product", "docs")
_BRIEF_FIELDS = ("page_id", "canonical_url", "title", "primary_keyword", "audience", "campaign_type", "priority")
_SECRET_KEY = re.compile(r"token|secret|password|passwd|api[_-]?key|cookie|credential|authorization", re.I)
_SECRET_VALUE = re.compile(r"^(ghp_|gho_|github_pat_|sk-|xox[bp]-)")


def _reject_secrets(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            if _SECRET_KEY.search(str(key)):
                raise ValidationError(f"SEO export must not contain credentials ({path}.{key})")
            _reject_secrets(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_secrets(value, f"{path}[{index}]")
    elif isinstance(node, str) and _SECRET_VALUE.match(node):
        raise ValidationError(f"SEO export contains a token-like value at {path}")


def _keyword(value: str) -> str:
    return " ".join(value.lower().split())


def _day(value, label):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{label} must be YYYY-MM-DD") from exc


def load_seo_briefs(path) -> dict:
    """Load and validate a local SEO export (no network, no SEO-repo import)."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != SEO_SCHEMA_VERSION:
        raise ValidationError("SEO briefs must be a schema_version 1 JSON object")
    _reject_secrets(doc)
    briefs = doc.get("briefs")
    if not isinstance(briefs, list) or not briefs:
        raise ValidationError("SEO briefs file needs a non-empty 'briefs' list")
    seen, clean = set(), []
    for index, brief in enumerate(briefs):
        label = f"briefs[{index}]"
        if not isinstance(brief, dict):
            raise ValidationError(f"{label} must be an object")
        for field in _BRIEF_FIELDS[:-1]:
            if not isinstance(brief.get(field), str) or not brief[field].strip():
                raise ValidationError(f"{label}.{field} is required")
        if brief["page_id"] in seen:
            raise ValidationError(f"Duplicate page_id {brief['page_id']}")
        seen.add(brief["page_id"])
        if brief["campaign_type"] not in CAMPAIGN_TYPES:
            raise ValidationError(f"{label}.campaign_type must be one of {', '.join(CAMPAIGN_TYPES)}")
        if type(brief.get("priority")) is not int or not 1 <= brief["priority"] <= 5:
            raise ValidationError(f"{label}.priority must be an integer 1 (highest)..5")
        if normalize_destination(brief["canonical_url"]) != brief["canonical_url"]:
            raise ValidationError(f"{label}.canonical_url must be canonical: https, lowercase host, "
                                  "no fragment/utm/tracker parameters")
        status = brief.get("status", "active")
        if status not in ("active", "draft", "retired"):
            raise ValidationError(f"{label}.status must be active, draft or retired")
        project = brief.get("project")
        if project is not None and (not isinstance(project, str) or not project.strip()):
            raise ValidationError(f"{label}.project must be a non-empty string when present")
        clean.append({**{k: brief[k] for k in _BRIEF_FIELDS}, "project": project, "status": status})
    pages = {b["page_id"]: b for b in clean}
    campaigns = []
    for index, item in enumerate(doc.get("campaigns") or []):
        label = f"campaigns[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("campaign_id"), str) or not item["campaign_id"]:
            raise ValidationError(f"{label}.campaign_id is required")
        if item.get("page_id") not in pages:
            raise ValidationError(f"{label}.page_id must reference a brief")
        start, end = _day(item.get("start"), f"{label}.start"), _day(item.get("end"), f"{label}.end")
        if end < start:
            raise ValidationError(f"{label}.end must not precede start")
        if item.get("status", "planned") not in ("planned", "active", "ended"):
            raise ValidationError(f"{label}.status must be planned, active or ended")
        campaigns.append({"campaign_id": item["campaign_id"], "page_id": item["page_id"],
                          "start": start.isoformat(), "end": end.isoformat(),
                          "status": item.get("status", "planned")})
    if len({c["campaign_id"] for c in campaigns}) != len(campaigns):
        raise ValidationError("Duplicate campaign_id in SEO campaigns")
    return {"schema_version": SEO_SCHEMA_VERSION, "briefs": clean, "campaigns": campaigns}


def seo_conflicts(doc: dict) -> list[dict]:
    """Competing messages: overlapping live campaigns on one canonical URL or keyword."""
    pages = {b["page_id"]: b for b in doc["briefs"]}
    conflicts = []
    by_url: dict[str, list[str]] = {}
    for brief in doc["briefs"]:
        if brief["status"] != "retired":
            by_url.setdefault(brief["canonical_url"], []).append(brief["page_id"])
    for url, ids in sorted(by_url.items()):
        if len(ids) > 1:
            conflicts.append({"type": "duplicate_canonical_url_across_briefs", "value": url, "page_ids": sorted(ids)})
    live = sorted((c for c in doc["campaigns"] if c["status"] != "ended"), key=lambda c: c["campaign_id"])
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a["start"] > b["end"] or b["start"] > a["end"]:
                continue
            pa, pb = pages[a["page_id"]], pages[b["page_id"]]
            window = [max(a["start"], b["start"]), min(a["end"], b["end"])]
            pair = [a["campaign_id"], b["campaign_id"]]
            if pa["canonical_url"] == pb["canonical_url"]:
                conflicts.append({"type": "same_canonical_url", "value": pa["canonical_url"],
                                  "campaigns": pair, "overlap": window})
            if _keyword(pa["primary_keyword"]) == _keyword(pb["primary_keyword"]):
                conflicts.append({"type": "same_primary_keyword", "value": _keyword(pa["primary_keyword"]),
                                  "campaigns": pair, "overlap": window})
    return conflicts


def campaign_destinations(briefs: dict, campaign_type: str, project: str | None = None, *,
                          platform: str | None = None, campaign: str | None = None) -> dict:
    """Pick the highest-priority canonical landing page and flag competing messages.

    ``briefs`` is the validated document returned by :func:`load_seo_briefs`.
    """
    if campaign_type not in CAMPAIGN_TYPES:
        raise ValidationError(f"campaign type must be one of {', '.join(CAMPAIGN_TYPES)}")
    candidates = sorted((b for b in briefs["briefs"] if b["campaign_type"] == campaign_type
                         and b["status"] == "active" and (project is None or b["project"] == project)),
                        key=lambda b: (b["priority"], b["page_id"]))
    destination = candidates[0] if candidates else None
    conflicts = seo_conflicts(briefs)
    relevant = []
    if destination:
        page_ids = {c["campaign_id"]: c["page_id"] for c in briefs["campaigns"]}
        for conflict in conflicts:
            ids = conflict.get("page_ids") or [page_ids[c] for c in conflict["campaigns"]]
            if destination["page_id"] in ids:
                relevant.append(conflict)
    result = {"campaign_type": campaign_type, "project": project,
              "status": "no_destination" if not destination else
                        "review_conflicts" if relevant else "ok",
              "destination": destination, "alternatives": candidates[1:],
              "destination_conflicts": relevant, "all_conflicts": conflicts,
              "note": "Read from a local SEO export only; landing page liveness and claim consistency "
                      "need human review. Social shares do not guarantee search ranking."}
    if destination and platform and campaign:
        result["utm_link"] = build_utm_link(destination["canonical_url"], platform=platform, campaign=campaign)
    return result
