"""Stable, conservative guardrails; remote limits can change without notice."""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from .richtext import BLUESKY_MAX_GRAPHEMES, BLUESKY_MAX_UTF8_BYTES, grapheme_count, mastodon_length, utf8_len

SUPPORTED_LIVE = frozenset({"bluesky", "mastodon", "telegram", "linkedin"})
# Platforms whose video adapter may run (#22 YouTube, #23 TikTok; owner-approved 2026-10-09,
# disabled until a live receipt). Empty: every video adapter is unreachable. Enabling one takes
# this code change AND the platform's registry video switch (``capabilities.VIDEO_MODES``) set to
# "live"; capabilities.validate_registry keeps the two equal.
SUPPORTED_LIVE_VIDEO: frozenset[str] = frozenset()
# TikTok captions: "The maximum length is 2200 in UTF-16 runes" (Direct Post reference,
# https://developers.tiktok.com/doc/content-posting-api-reference-direct-post, accessed 2026-10-09).
TIKTOK_CAPTION_MAX_UTF16 = 2200
LINKEDIN_ESCAPED_MAX = 3000  # documented Posts commentary maximum; checked on the ESCAPED text actually sent (not independently verified, see docs/LINKEDIN.md)
PLATFORMS = frozenset({
    "bluesky", "mastodon", "telegram", "linkedin", "x", "threads",
    "instagram", "facebook", "tiktok", "youtube", "reddit", "pinterest",
    "devto", "hashnode",
})

# Lower than some provider limits: do not mistake these for exact API specs.
CONSERVATIVE_TEXT_LIMITS = {
    "bluesky": 300,  # Lexicon maxGraphemes; validate_post counts graphemes (never under-counted) + 3000 UTF-8 bytes.
    "mastodon": 450, # Instance settings differ: an account may pin a lower max_characters (never raises this).
    "telegram": 4000,
    "x": 260,
    "threads": 450,
    "linkedin": 2800,
    "facebook": 4500,
    "instagram": 2100,
    "tiktok": 2000,
    "youtube": 4500,
    "reddit": 9000,
    "pinterest": 450,
    "devto": 30000,   # Markdown articles; manual-only, not an official provider cap.
    "hashnode": 30000, # Markdown articles; manual-only, not an official provider cap.
}

# Mastodon visibilities ReachWeaver may request. "direct" (a DM) is forbidden.
MASTODON_VISIBILITIES = ("public", "unlisted", "private")

# Mastodon image attachments (issue #13, owner-approved 2026-10-09; disabled until a live receipt). Conservative
# defaults; an account may pin LOWER values (``max_media_attachments``, ``image_size_limit``,
# ``media_description_limit``), never higher. Sources, accessed 2026-10-09:
# - https://docs.joinmastodon.org/user/posting/ : "Up to 4 images can be attached, per post";
#   images "up to 16MB" (counted here as 16,000,000 bytes, below the 16 MiB the server uses).
# - https://docs.joinmastodon.org/entities/Instance/ : configuration.statuses.max_media_attachments
#   (example 4), configuration.media_attachments.description_limit (example 1500, added 4.4.0).
#   The server's own model constant is higher (10,000 on main), so 1500 is the low documented value.
# Only PNG, JPEG and WebP are allowed: of MediaAttachment::IMAGE_MIME_TYPES (jpeg, png, gif,
# webp; github.com/mastodon/mastodon app/models/media_attachment.rb on main, accessed
# 2026-10-09) the formats ReachWeaver's header parser recognizes, minus GIF, which Mastodon
# converts to soundless video (gifv, "Only one animated GIF can be attached, per post"), a
# different, asynchronous flow. Video is not supported by this draft.
MASTODON_MEDIA_DEFAULTS = {"max_media_attachments": 4, "image_size_limit": 16_000_000,
                           "media_description_limit": 1500}
MASTODON_IMAGE_MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}

# Trojan Source (CVE-2021-42574): explicit bidi embeddings, overrides and isolates make the
# text a reviewer sees differ from the bytes being approved. No real post needs them.
BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
# Other invisible format characters with no legitimate place in a social post: word joiner and
# invisible math operators, deprecated format controls, a BOM inside text, the Mongolian vowel
# separator, interlinear annotation and the deprecated language tag. Deliberately NOT listed,
# because ordinary multilingual or emoji text uses them: ZWSP U+200B (Thai/Khmer word breaks),
# ZWNJ U+200C (Persian, Indic), ZWJ U+200D (emoji sequences), the LRM/RLM/ALM marks U+200E,
# U+200F, U+061C (mixed RTL text; they cannot reverse a run), soft hyphen U+00AD and the tag
# characters U+E0020-E007F (subdivision flag emoji).
INVISIBLE_FORMAT_CHARS = frozenset(
    "᠎⁠⁡⁢⁣⁤⁪⁫⁬⁭⁮⁯﻿￹￺￻\U000e0001")

PLACEHOLDER = re.compile(r"(?:\bTODO\b|\bTBD\b|\[insert[^\]]*\]|\{\{[^}]+\}\}|<placeholder>)", re.I)


class ValidationError(ValueError):
    """Bad user-owned draft or configuration (not an API response)."""


# Live gate (issue #5, owner-approved 2026-10-09): publish --live also requires REACHWEAVER_ENV=prod.
LIVE_OPT_IN_ENV = "REACHWEAVER_ALLOW_LIVE"
RUNTIME_ENV = "REACHWEAVER_ENV"
RUNTIME_ENVIRONMENTS = ("dev", "staging", "prod")
LIVE_ENVIRONMENT = "prod"


def runtime_environment(env=None) -> str:
    """REACHWEAVER_ENV for display: an allowlisted value, ``unset`` or ``unrecognized``.

    Only ``dev``, ``staging`` and ``prod`` (exact, lowercase) are echoed; any other value
    is reported as ``unrecognized`` and never printed.
    """
    env = os.environ if env is None else env
    value = env.get(RUNTIME_ENV)
    if value is None or value == "":
        return "unset"
    return value if value in RUNTIME_ENVIRONMENTS else "unrecognized"


def live_opt_in(env=None) -> bool:
    """True only when REACHWEAVER_ALLOW_LIVE is exactly ``1``. Never a value echo."""
    env = os.environ if env is None else env
    return env.get(LIVE_OPT_IN_ENV) == "1"


def require_live_gate(env=None) -> None:
    """Raise unless REACHWEAVER_ALLOW_LIVE=1 and REACHWEAVER_ENV=prod (``--live`` is checked by the caller).

    The CLI calls this right after ``--live`` is seen and before it loads any account,
    reads any credential or makes any request, for native adapters and the Postiz backend
    alike. The message never echoes an unrecognized REACHWEAVER_ENV value.
    """
    env = os.environ if env is None else env
    if not live_opt_in(env):
        raise ValidationError("Live publishing requires both --live and REACHWEAVER_ALLOW_LIVE=1 "
                              "(and REACHWEAVER_ENV=prod on the production host)")
    current = runtime_environment(env)
    if current != LIVE_ENVIRONMENT:
        raise ValidationError(f"Live publishing requires REACHWEAVER_ENV=prod as well as --live and "
                              f"REACHWEAVER_ALLOW_LIVE=1 (REACHWEAVER_ENV is {current}). Set it only on the "
                              "production host; no account was loaded and nothing was sent")


def mastodon_text_limit(account: dict | None) -> int:
    """min(conservative default, owner-pinned instance max_characters); never higher."""
    limit = CONSERVATIVE_TEXT_LIMITS["mastodon"]
    configured = (account or {}).get("max_characters")
    if configured is None:
        return limit
    if type(configured) is not int or configured < 1:
        raise ValidationError("mastodon max_characters must be a positive integer")
    return min(limit, configured)


def mastodon_media_limits(account: dict | None) -> dict[str, int]:
    """Effective image-attachment limits: min(conservative default, owner-pinned value)."""
    limits = dict(MASTODON_MEDIA_DEFAULTS)
    for name, default in MASTODON_MEDIA_DEFAULTS.items():
        configured = (account or {}).get(name)
        if configured is None:
            continue
        if type(configured) is not int or configured < 1:
            raise ValidationError(f"mastodon {name} must be a positive integer")
        limits[name] = min(default, configured)
    return limits


def _length_issue(platform: str, content: str, account: dict | None) -> list[str]:
    if platform == "bluesky":
        issues = []
        graphemes = grapheme_count(content)
        if graphemes > BLUESKY_MAX_GRAPHEMES:
            issues.append(f"Post has {graphemes} grapheme clusters (conservative count); bluesky limit is {BLUESKY_MAX_GRAPHEMES}")
        if utf8_len(content) > BLUESKY_MAX_UTF8_BYTES:
            issues.append(f"Post has {utf8_len(content)} UTF-8 bytes; bluesky limit is {BLUESKY_MAX_UTF8_BYTES}")
        return issues
    if platform == "mastodon":
        try:
            limit = mastodon_text_limit(account)
        except ValidationError as exc:
            return [str(exc)]
        counted = mastodon_length(content)
        if counted > limit:
            return [f"Post counts as {counted} characters (URLs as at least 23); conservative mastodon limit is {limit}"]
        return []
    if platform == "instagram":  # tighten-only: official 2200 chars, 30 hashtags, 20 @ tags (issue #20)
        from .instagram import content_problems
        issues = content_problems(content)
        if len(content) > CONSERVATIVE_TEXT_LIMITS[platform]:
            issues.append(f"Post has {len(content)} Unicode code points; conservative instagram limit is "
                          f"{CONSERVATIVE_TEXT_LIMITS[platform]}")
        return issues
    if platform == "tiktok":  # tighten-only: the official UTF-16 cap on top of the code-point cap (#23)
        issues = []
        units = len(content.encode("utf-16-le")) // 2
        if units > TIKTOK_CAPTION_MAX_UTF16:
            issues.append(f"Caption has {units} UTF-16 units; tiktok limit is {TIKTOK_CAPTION_MAX_UTF16}")
        if len(content) > CONSERVATIVE_TEXT_LIMITS[platform]:
            issues.append(f"Post has {len(content)} Unicode code points; conservative tiktok limit is "
                          f"{CONSERVATIVE_TEXT_LIMITS[platform]}")
        return issues
    if platform == "youtube":  # tighten-only: official 5000-byte, no '<'/'>' description rule (#22)
        from .youtube import description_problems
        issues = description_problems(content)
        if len(content) > CONSERVATIVE_TEXT_LIMITS[platform]:
            issues.append(f"Post has {len(content)} Unicode code points; conservative youtube limit is "
                          f"{CONSERVATIVE_TEXT_LIMITS[platform]}")
        return issues
    if platform == "threads":  # tighten-only: official 500 (emoji by UTF-8 bytes) and 5 links, issue #19
        from .threads import content_problems as threads_content_problems
        issues = threads_content_problems(content)
        if len(content) > CONSERVATIVE_TEXT_LIMITS[platform]:
            issues.append(f"Post has {len(content)} Unicode code points; conservative threads limit is "
                          f"{CONSERVATIVE_TEXT_LIMITS[platform]}")
        return issues
    if len(content) > CONSERVATIVE_TEXT_LIMITS[platform]:
        return [f"Post has {len(content)} Unicode code points; conservative {platform} limit is {CONSERVATIVE_TEXT_LIMITS[platform]}"]
    return []


def validate_post(platform: str, content: str, account: dict | None = None) -> list[str]:
    """Return blocking lint problems for a draft without changing it.

    ``account`` (public metadata) lets a Mastodon account pin a lower
    per-instance ``max_characters``; it can never raise the conservative cap.
    """
    issues: list[str] = []
    if not isinstance(platform, str) or platform not in PLATFORMS:
        issues.append(f"Unknown platform: {platform}")
        return issues
    if not content or not content.strip():
        issues.append("Post must contain substantive text")
        return issues
    if PLACEHOLDER.search(content):
        issues.append("Unresolved placeholder/TODO in the post")
    issues.extend(_length_issue(platform, content, account))
    if platform == "linkedin":
        from .linkedin import escape_commentary  # lazy: linkedin imports this module
        escaped = len(escape_commentary(content))
        if escaped > LINKEDIN_ESCAPED_MAX:
            issues.append(f"LinkedIn commentary is {escaped} characters after required escaping; "
                          f"the conservative limit is {LINKEDIN_ESCAPED_MAX}")
    if any(ord(ch) < 32 and ch not in "\n\t\r" for ch in content):
        issues.append("Post includes control characters")
    if any(ch in BIDI_CONTROLS for ch in content):
        issues.append("Post includes bidirectional override/embedding/isolate characters (U+202A-U+202E, "
                      "U+2066-U+2069), which can make the reviewed text differ from what is sent")
    if any(ch in INVISIBLE_FORMAT_CHARS for ch in content):
        issues.append("Post includes invisible format characters (for example U+2060 word joiner or U+FEFF)")
    if "\x00" in content:
        issues.append("Post includes NUL")
    return issues


def require_https_public_server(value: str) -> str:
    """Allow a configured HTTPS social server, not localhost, IP or credentials."""
    import ipaddress
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValidationError("Server must be an HTTPS origin with no credentials/path/query")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local") or "." not in host:
        raise ValidationError("Local network hosts are not permitted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValidationError("IP-address social servers are not permitted")
    if parsed.port not in (None, 443):
        raise ValidationError("Only standard HTTPS port 443 is permitted")
    return f"https://{host}"
