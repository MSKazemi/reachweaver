"""Offline, fail-closed platform capability and API-approval registry (issue #6).

The registry is research data, never consent, account authorization or proof of
delivery. Unknown, unavailable, gated and manual-only capabilities are refused.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from .policy import PLATFORMS, SUPPORTED_LIVE, SUPPORTED_LIVE_VIDEO, ValidationError

REGISTRY_FILE = Path(__file__).with_name("platform_registry.json")
FORMATS = ("text", "image", "video", "carousel", "link")
FORMAT_STATUSES = frozenset({"api_direct", "api_gated", "manual_export", "unavailable", "unknown"})
PLATFORM_STATUSES = frozenset({"supported", "requires-review", "blocked", "manual-export"})
ADAPTERS = frozenset({"live_text", "live_media", "none"})
# Adapters that can publish. ``live_media`` (issue #20) is for a media-only platform such as
# Instagram, whose text format is not a standalone post. The bundled registry has no such entry.
LIVE_ADAPTERS = frozenset({"live_text", "live_media"})
# Issue #17 (owner-approved 2026-10-09; disabled until a live receipt): optional per-entry reply
# adapter. Absent or "none" refuses every reply send. "live_reply" is valid only for the platforms
# with a mock-tested reply adapter and a live_text adapter. The bundled registry ships "none" for
# both: the reply path stays unreachable until the owner changes this after a supervised live
# reply receipt (docs/SECURITY-REVIEW-PROPOSAL-17-reply-send.md).
REPLY_ADAPTER_FIELD = "reachweaver_reply_adapter"
REPLY_ADAPTERS = frozenset({"live_reply", "none"})
REPLY_ADAPTER_PLATFORMS = frozenset({"bluesky", "mastodon"})
ACTIONS = frozenset({"publish", "api"})
# Image formats a ReachWeaver live adapter exists for (#13 Mastodon local files, #20 Instagram
# owner-hosted URLs; owner-approved 2026-10-09, disabled until a live receipt). Having the
# adapter does NOT enable it: ``require_capability(..., "publish")`` also needs the platform's
# registry entry to list the format in ``live_media_formats``, which the bundled registry does
# not do. The owner adds it only after a supervised first send with a first-party receipt.
LIVE_MEDIA_FORMATS = {"mastodon": frozenset({"image"}), "instagram": frozenset({"image"})}
# Video (#22 YouTube, #23 TikTok; owner-approved 2026-10-09, disabled until a live receipt) is
# gated apart from text and images, never through ``reachweaver_adapter`` or
# ``live_media_formats``. A platform's video adapter runs only when the platform is in
# ``policy.SUPPORTED_LIVE_VIDEO`` (code) AND the registry switch for the delivery mode is "live"
# (data). ``VIDEO_MODES`` maps each platform that has a video adapter to its modes and their
# optional registry fields (absent = "off"); ``VIDEO_MODE_REQUIRES`` lists modes that need
# another mode live first; ``VIDEO_LIVE_FORMATS`` the ``formats.video`` statuses a live mode
# accepts. TikTok: "creator_inbox" uploads a draft the creator finishes in the app (nothing
# public); "direct_post" publishes and needs creator_inbox live. YouTube: "upload" is one
# resumable videos.insert. The #22 draft's ``reachweaver_adapter: "live_video"`` value and #23's
# two switches were reconciled into this one schema at integration (2026-10-09); YouTube keeps
# #22's rule that a live upload needs ``video: api_direct``, TikTok keeps #23's api_direct or
# api_gated (TikTok's audit gate).
VIDEO_MODES: dict[str, dict[str, str]] = {
    "tiktok": {"creator_inbox": "reachweaver_video_inbox", "direct_post": "reachweaver_video_direct_post"},
    "youtube": {"upload": "reachweaver_video_upload"},
}
VIDEO_MODE_REQUIRES: dict[tuple[str, str], str] = {("tiktok", "direct_post"): "creator_inbox"}
VIDEO_LIVE_FORMATS: dict[str, frozenset[str]] = {"tiktok": frozenset({"api_direct", "api_gated"}),
                                                 "youtube": frozenset({"api_direct"})}
VIDEO_MODE_VALUES = ("off", "live")
DEFAULT_MAX_AGE_DAYS = 120
REQUIRED_FIELDS = (
    "id", "label", "status", "official_docs_url", "supported_account_types", "auth",
    "user_approval_requirements", "formats", "metrics_access", "edit_delete",
    "rate_limits", "fees", "compliance_constraints", "verified_date", "sources",
    "reachweaver_adapter",
)
RATE_LIMIT_KEYS = frozenset({"summary", "source", "accessed", "facts", "not_published"})
RATE_FACT_KEYS = frozenset({"scope", "action", "limit", "unit", "window_seconds"})
_TEXT_FIELDS = ("label", "user_approval_requirements", "metrics_access", "edit_delete", "fees")


def _https(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and not (parts.username or parts.password)


def _iso_date(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and len(value) == 10 else None
    except ValueError:
        return None


def _nonempty_str_list(value: object, *, allow_empty: bool = False) -> bool:
    return isinstance(value, list) and (allow_empty or bool(value)) and all(
        isinstance(v, str) and v.strip() for v in value)


def _rate_limit_detail_errors(key: str, limits: dict) -> list[str]:
    """Check the optional sourced rate-limit detail (issue #11 provenance).

    ``accessed`` is the ISO date the ``source`` page was read. ``facts`` are the exact published
    numbers (``limit`` per ``window_seconds`` in ``unit`` for a ``scope``); they need ``accessed``,
    an HTTPS ``source`` and a stated summary. ``not_published`` names limits the official docs do
    not state, so a gap is recorded instead of guessed. Research data only: nothing here changes
    ReachWeaver's own budgets or cooldowns (``caps.py``, ``ratelimit.py``).
    """
    errors: list[str] = []
    extra = sorted(set(limits) - RATE_LIMIT_KEYS)
    if extra:
        errors.append(f"{key}: rate_limits has unknown keys {', '.join(extra)}")
    stated = not limits["summary"].startswith("unknown")
    if "accessed" in limits:
        if _iso_date(limits["accessed"]) is None:
            errors.append(f"{key}: rate_limits.accessed must be an ISO date YYYY-MM-DD")
        elif not _https(limits["source"]):
            errors.append(f"{key}: rate_limits.accessed needs an HTTPS source")
    if "facts" in limits:
        facts = limits["facts"]
        if not stated or "accessed" not in limits or not _https(limits["source"]):
            errors.append(f"{key}: rate_limits.facts need a stated summary, HTTPS source and accessed date")
        if not isinstance(facts, list) or not facts:
            errors.append(f"{key}: rate_limits.facts must be a non-empty list")
        else:
            for index, fact in enumerate(facts):
                if not _valid_rate_fact(fact):
                    errors.append(f"{key}: rate_limits.facts[{index}] needs scope, action, unit text and "
                                  "positive integer limit and window_seconds")
    if "not_published" in limits and not _nonempty_str_list(limits["not_published"]):
        errors.append(f"{key}: rate_limits.not_published must be a non-empty list of text")
    return errors


def _valid_rate_fact(fact: object) -> bool:
    if not isinstance(fact, dict) or set(fact) != RATE_FACT_KEYS:
        return False
    texts_ok = all(isinstance(fact[k], str) and fact[k].strip() for k in ("scope", "action", "unit"))
    numbers_ok = all(type(fact[k]) is int and fact[k] > 0 for k in ("limit", "window_seconds"))
    return texts_ok and numbers_ok


def validate_registry(registry: object) -> list[str]:
    """Return completeness/enum/provenance errors; empty list means valid."""
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return ["schema_version must be 1"]
    errors: list[str] = []
    if _iso_date(registry.get("reviewed_on")) is None:
        errors.append("reviewed_on must be an ISO date YYYY-MM-DD")
    entries = registry.get("platforms")
    if not isinstance(entries, list) or not entries:
        return errors + ["platforms must be a non-empty list"]
    seen: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            errors.append("platform entry missing string id")
            continue
        key = entry["id"]
        if key in seen:
            errors.append(f"duplicate platform: {key}")
        seen[key] = entry
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            errors.append(f"{key}: missing fields {', '.join(missing)}")
            continue
        for field in _TEXT_FIELDS:
            if not isinstance(entry[field], str) or not entry[field].strip():
                errors.append(f"{key}: {field} must be non-empty text")
        if entry["status"] not in PLATFORM_STATUSES:
            errors.append(f"{key}: invalid status {entry['status']!r}")
        if not _https(entry["official_docs_url"]):
            errors.append(f"{key}: official_docs_url must be HTTPS")
        if not _nonempty_str_list(entry["supported_account_types"]):
            errors.append(f"{key}: supported_account_types must be a non-empty list")
        auth = entry["auth"]
        if not (isinstance(auth, dict) and isinstance(auth.get("method"), str) and auth["method"].strip()
                and _nonempty_str_list(auth.get("scopes"), allow_empty=True)):
            errors.append(f"{key}: auth needs method text and scopes list")
        formats = entry["formats"]
        if not isinstance(formats, dict) or set(formats) != set(FORMATS):
            errors.append(f"{key}: formats must define exactly {', '.join(FORMATS)}")
        else:
            for fmt, status in formats.items():
                if status not in FORMAT_STATUSES:
                    errors.append(f"{key}: invalid {fmt} format status {status!r}")
        limits = entry["rate_limits"]
        if not (isinstance(limits, dict) and isinstance(limits.get("summary"), str) and limits["summary"].strip()
                and isinstance(limits.get("source"), str)):
            errors.append(f"{key}: rate_limits needs summary and source")
        elif not limits["summary"].startswith("unknown") and not _https(limits["source"]):
            errors.append(f"{key}: a stated rate limit needs an HTTPS source")
        else:
            errors.extend(_rate_limit_detail_errors(key, limits))
        if not _nonempty_str_list(entry["compliance_constraints"]):
            errors.append(f"{key}: compliance_constraints must be a non-empty list")
        if _iso_date(entry["verified_date"]) is None:
            errors.append(f"{key}: verified_date must be an ISO date YYYY-MM-DD")
        sources = entry["sources"]
        if not isinstance(sources, list) or not sources or not all(_https(s) for s in sources):
            errors.append(f"{key}: sources must be a non-empty list of HTTPS URLs")
        adapter = entry["reachweaver_adapter"]
        if adapter not in ADAPTERS:
            errors.append(f"{key}: invalid reachweaver_adapter {adapter!r}")
        if adapter == "live_text" and (not isinstance(formats, dict) or formats.get("text") != "api_direct"):
            errors.append(f"{key}: live_text adapter requires text=api_direct")
        if adapter in LIVE_ADAPTERS and not (isinstance(limits, dict) and limits.get("facts")):
            errors.append(f"{key}: a {adapter} adapter needs sourced rate_limits.facts")
        if (adapter in LIVE_ADAPTERS) != (entry["status"] == "supported"):
            errors.append(f"{key}: status 'supported' must match a live adapter (live_text or live_media)")
        if "live_media_formats" in entry or adapter == "live_media":  # optional; absent = no live media
            media = entry.get("live_media_formats")
            allowed = LIVE_MEDIA_FORMATS.get(key, frozenset())
            if not (isinstance(media, list) and media and all(isinstance(m, str) for m in media)
                    and len(set(media)) == len(media) and set(media) <= allowed):
                errors.append(f"{key}: live_media_formats must list formats ReachWeaver has a live media "
                              f"adapter for ({', '.join(sorted(allowed)) or 'none'})")
            elif adapter not in LIVE_ADAPTERS or not all(isinstance(formats, dict) and formats.get(m) == "api_direct"
                                                         for m in media):
                errors.append(f"{key}: live_media_formats needs a live adapter and api_direct formats")
        reply = entry.get(REPLY_ADAPTER_FIELD, "none")
        if reply not in REPLY_ADAPTERS:
            errors.append(f"{key}: invalid {REPLY_ADAPTER_FIELD} {reply!r}")
        elif reply == "live_reply" and (adapter != "live_text" or key not in REPLY_ADAPTER_PLATFORMS):
            errors.append(f"{key}: live_reply needs a live_text adapter and a ReachWeaver reply adapter "
                          f"({', '.join(sorted(REPLY_ADAPTER_PLATFORMS))} only)")
    errors.extend(_video_mode_errors(seen))
    if set(seen) != set(PLATFORMS):
        absent, extra = sorted(set(PLATFORMS) - set(seen)), sorted(set(seen) - set(PLATFORMS))
        if absent:
            errors.append("registry missing platforms: " + ", ".join(absent))
        if extra:
            errors.append("registry has unknown platforms: " + ", ".join(extra))
    live = {k for k, e in seen.items() if e.get("reachweaver_adapter") in LIVE_ADAPTERS}
    if live != set(SUPPORTED_LIVE):
        errors.append("live_text adapters must equal policy.SUPPORTED_LIVE")
    reddit = seen.get("reddit", {})
    if reddit.get("reachweaver_adapter") != "none" or not any(
            "moderator-only" in c for c in reddit.get("compliance_constraints", [])):
        errors.append("reddit must stay adapter-free with a moderator-only/human-only constraint")
    return errors


def _video_mode(entry: dict, platform: str, mode: str) -> str:
    return entry.get(VIDEO_MODES[platform][mode], "off")


def _video_mode_errors(seen: dict[str, dict]) -> list[str]:
    """Video delivery switches (#22, #23): shape, platform allowlist, mode dependencies, sourced
    facts and the code/data two-key rule (live platforms must equal ``SUPPORTED_LIVE_VIDEO``)."""
    errors: list[str] = []
    owners: dict[str, list[str]] = {}
    for platform, modes in VIDEO_MODES.items():
        for field in modes.values():
            owners.setdefault(field, []).append(platform)
    live_platforms = set()
    for key, entry in seen.items():
        foreign = [field for field in owners if field in entry and key not in owners[field]]
        if foreign:
            errors.append(f"{key}: {', '.join(foreign)} only apply to "
                          f"{', '.join(sorted({p for field in foreign for p in owners[field]}))}")
            continue
        if key not in VIDEO_MODES:
            continue
        values = {mode: _video_mode(entry, key, mode) for mode in VIDEO_MODES[key]}
        bad = [VIDEO_MODES[key][m] for m, v in values.items() if v not in VIDEO_MODE_VALUES]
        if bad:
            errors.append(f"{key}: {', '.join(bad)} must be one of {', '.join(VIDEO_MODE_VALUES)}")
            continue
        for mode, value in values.items():
            needed = VIDEO_MODE_REQUIRES.get((key, mode))
            if value == "live" and needed is not None and values[needed] != "live":
                errors.append(f"{key}: {VIDEO_MODES[key][mode]} 'live' needs {VIDEO_MODES[key][needed]} 'live'")
        if "live" in values.values():
            live_platforms.add(key)
            formats, limits = entry.get("formats"), entry.get("rate_limits")
            allowed = VIDEO_LIVE_FORMATS[key]
            if not isinstance(formats, dict) or formats.get("video") not in allowed:
                errors.append(f"{key}: a live video mode needs video {' or '.join(sorted(allowed))}")
            if not (isinstance(limits, dict) and limits.get("facts")):
                errors.append(f"{key}: a live video mode needs sourced rate_limits.facts")
    if live_platforms != set(SUPPORTED_LIVE_VIDEO):
        errors.append("platforms with a 'live' video switch must equal policy.SUPPORTED_LIVE_VIDEO")
    return errors


def load_registry(path: Path | None = None) -> dict:
    """Load and validate the bundled registry; any defect is a ValidationError."""
    try:
        registry = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Capability registry unreadable: {exc.__class__.__name__}") from None
    errors = validate_registry(registry)
    if errors:
        raise ValidationError("Invalid capability registry: " + "; ".join(errors))
    return registry


def stale_entries(registry: dict, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                  today: date | None = None) -> list[str]:
    """Platform IDs whose verified_date is older than max_age_days (warning, not error)."""
    if not isinstance(max_age_days, int) or isinstance(max_age_days, bool) or not 1 <= max_age_days <= 3650:
        raise ValidationError("max_age_days must be 1..3650")
    now = today or date.today()
    return sorted(e["id"] for e in registry["platforms"]
                  if (now - date.fromisoformat(e["verified_date"])).days > max_age_days)


def require_capability(platform: str, fmt: str = "text", action: str = "publish", *,
                       registry: dict | None = None) -> dict:
    """Fail closed unless the registry positively permits this action.

    ``api``: provider documents a direct self-serve API for the format.
    ``publish``: additionally a ReachWeaver live adapter exists: text with a ``live_text``
    adapter, or an image format the entry lists in the optional ``live_media_formats`` with a
    live adapter (#13 Mastodon, #20 Instagram; absent in the bundled registry, so live images
    stay refused until the owner adds it after a first-party receipt).
    """
    data = registry if registry is not None else load_registry()
    if registry is not None:
        errors = validate_registry(data)
        if errors:
            raise ValidationError("Invalid capability registry: " + "; ".join(errors))
    if action not in ACTIONS:
        raise ValidationError(f"Unknown capability action: {action}")
    if fmt not in FORMATS:
        raise ValidationError(f"Unknown content format: {fmt}")
    entry = next((e for e in data["platforms"] if e["id"] == platform), None)
    if entry is None:
        raise ValidationError(f"Unknown platform capability: {platform}")
    status = entry["formats"][fmt]
    if status != "api_direct":
        raise ValidationError(f"{platform} {fmt} is {status}; {action} refused (fail closed)")
    if action == "publish":
        adapter = entry["reachweaver_adapter"]
        enabled = (fmt == "text" and adapter == "live_text") or (
            adapter in LIVE_ADAPTERS and fmt in LIVE_MEDIA_FORMATS.get(platform, ())
            and fmt in entry.get("live_media_formats", []))
        if not enabled:
            raise ValidationError(f"No ReachWeaver live adapter for {platform} {fmt}; use manual export")
    return entry


def is_live_entry(entry: dict) -> bool:
    """True when a registry entry is ``supported`` with a live (text or media) adapter."""
    return entry.get("status") == "supported" and entry.get("reachweaver_adapter") in LIVE_ADAPTERS


def require_video_mode(platform: str, mode: str, *, registry: dict | None = None) -> dict:
    """Fail closed unless ``platform``'s video adapter may run in ``mode`` (#22, #23).

    The single gate for every live video send. Needs the platform in
    ``policy.SUPPORTED_LIVE_VIDEO`` (code) AND the registry switch for the mode set to "live"
    (data), plus every mode it requires (TikTok ``direct_post`` needs ``creator_inbox``). The
    bundled registry keeps every switch "off" and the set is empty, so this always raises today.
    ``require_capability(..., "video", "publish")`` never enables video.
    """
    if mode not in VIDEO_MODES.get(platform, {}) and mode not in {m for modes in VIDEO_MODES.values() for m in modes}:
        raise ValidationError(f"Unknown video delivery mode: {mode}")
    data = registry if registry is not None else load_registry()
    if registry is not None:
        errors = validate_registry(data)
        if errors:
            raise ValidationError("Invalid capability registry: " + "; ".join(errors))
    entry = next((e for e in data["platforms"] if e["id"] == platform), None)
    if entry is None:
        raise ValidationError(f"Unknown platform capability: {platform}")
    if platform not in VIDEO_MODES or mode not in VIDEO_MODES[platform] or platform not in SUPPORTED_LIVE_VIDEO:
        raise ValidationError(f"No ReachWeaver live video adapter enabled for {platform}; use manual export")
    needed = [mode]
    while (platform, needed[0]) in VIDEO_MODE_REQUIRES:
        needed.insert(0, VIDEO_MODE_REQUIRES[(platform, needed[0])])
    for name in needed:
        if _video_mode(entry, platform, name) != "live":
            raise ValidationError(f"{platform} video {name} is off in the capability registry ("
                                  f"{VIDEO_MODES[platform][name]}); refused (fail closed)")
    return entry


def require_reply_capability(platform: str, *, registry: dict | None = None) -> dict:
    """Fail closed unless live text publish is allowed AND the entry's reply adapter is live_reply.

    Issue #17 (owner-approved 2026-10-09; disabled until a live receipt): the bundled registry sets ``reachweaver_reply_adapter`` to ``none``, so
    every reply send is refused here before any claim, credential read or request.
    """
    entry = require_capability(platform, "text", "publish", registry=registry)
    if entry.get(REPLY_ADAPTER_FIELD, "none") != "live_reply":
        raise ValidationError(f"reply_capability_gated: {platform} replies have no enabled ReachWeaver reply "
                              f"adapter ({REPLY_ADAPTER_FIELD}={entry.get(REPLY_ADAPTER_FIELD, 'none')}); "
                              "post the reply manually; the reply path stays disabled until the owner records "
                              "a supervised live reply receipt and enables it in the registry")
    return entry


def reply_send_support(platform: str) -> str:
    """``approved_reply_via_publish`` when the registry enables replies for ``platform``, else ``none``."""
    try:
        require_reply_capability(platform)
    except ValidationError:
        return "none"
    return "approved_reply_via_publish"


def summarize(registry: dict, platform: str | None = None) -> list[dict]:
    """Compact rows for CLI/docs output."""
    rows = []
    for e in sorted(registry["platforms"], key=lambda item: item["id"]):
        if platform and e["id"] != platform:
            continue
        rows.append({"platform": e["id"], "status": e["status"], "adapter": e["reachweaver_adapter"],
                     **{fmt: e["formats"][fmt] for fmt in FORMATS},
                     "verified_date": e["verified_date"]})
    if platform and not rows:
        raise ValidationError(f"Unknown platform capability: {platform}")
    return rows
