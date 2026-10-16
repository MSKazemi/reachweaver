"""Local do-not-contact list: handles, accounts or domains never to reply to, mention or tag (issue #26).

The list lives in an ignored local file (default ``config/optout.json``). It is
owner data about third parties, so it holds the minimum needed to honour a
request: a handle or domain, an optional platform and an optional date. There is
no free-text reason field. Everything here is offline and side-effect free.

Matching is deliberately conservative (over-blocking is the safe failure):

* handles compare case-insensitively without a leading ``@``;
* a bare ``name`` entry also matches ``name@any.instance`` and vice versa, so a
  Mastodon opt-out is honoured even when a draft omits the instance;
* a ``domain`` entry matches a handle equal to the domain, any sub-domain handle
  (Bluesky domain handles) and any ``user@domain`` (a whole Mastodon instance);
* an entry with a ``platform`` applies only to that platform when the caller
  knows the platform; without a platform it applies everywhere.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

from . import home
from .policy import PLATFORMS, ValidationError

OPTOUT_SCHEMA_VERSION = 1
OPTOUT_ENV = "REACHWEAVER_OPTOUT"
DEFAULT_OPTOUT_PATH = Path("config/optout.json")
MAX_ENTRIES = 10000
ENTRY_KEYS = frozenset({"handle", "domain", "platform", "recorded_on"})
OPTOUT_PLATFORMS = PLATFORMS | {"github", "hackernews"}

_HANDLE = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]{0,98}[a-z0-9_])?(?:@[a-z0-9-]+(?:\.[a-z0-9-]+)+)?$")
_DOMAIN = re.compile(r"^(?=.{3,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
# An @-mention: not preceded by a word char, '@', '.' or '/', so e-mail addresses and
# URL paths are not mentions. Optional '@instance' suffix for fediverse handles.
MENTION = re.compile(
    r"(?<![\w@./])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?(?:@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)?)")


def normalize_handle(value: object) -> str:
    """Casefolded, NFKC-normalized handle without a leading '@'; '' when not a string."""
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().casefold().lstrip("@").rstrip(".")


def _split(handle: str) -> tuple[str, str]:
    """('local', 'host') for 'local@host'; ('name', '') for a bare handle."""
    local, sep, host = handle.partition("@")
    return (local, host) if sep else (handle, "")


def _iso_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_optout(raw: object) -> list[dict]:
    """Return normalized entries; any defect raises (an opt-out list must never half-load)."""
    if not isinstance(raw, dict) or raw.get("version") != OPTOUT_SCHEMA_VERSION:
        raise ValidationError(f"Opt-out file must be an object with \"version\": {OPTOUT_SCHEMA_VERSION}")
    unknown = sorted(set(raw) - {"version", "entries"})
    if unknown:
        raise ValidationError(f"Opt-out file has unknown top-level keys: {', '.join(unknown)}")
    entries = raw.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValidationError(f"Opt-out entries must be a list of at most {MAX_ENTRIES}")
    out = []
    for index, entry in enumerate(entries):
        where = f"optout entries[{index}]"
        if not isinstance(entry, dict):
            raise ValidationError(f"{where} must be an object")
        extra = sorted(set(entry) - ENTRY_KEYS)
        if extra:
            raise ValidationError(f"{where}: unknown keys {', '.join(extra)} (keep only handle/domain/platform/recorded_on)")
        if ("handle" in entry) == ("domain" in entry):
            raise ValidationError(f"{where}: needs exactly one of handle or domain")
        platform = entry.get("platform")
        if platform is not None and platform not in OPTOUT_PLATFORMS:
            raise ValidationError(f"{where}: unknown platform {platform!r}")
        recorded = entry.get("recorded_on")
        if recorded is not None and not _iso_date(recorded):
            raise ValidationError(f"{where}: recorded_on must be YYYY-MM-DD")
        if "handle" in entry:
            handle = normalize_handle(entry["handle"])
            if not _HANDLE.fullmatch(handle):
                raise ValidationError(f"{where}: handle must look like name, name.example.social or name@instance.example")
            out.append({"kind": "handle", "value": handle, "platform": platform})
        else:
            domain = normalize_handle(entry["domain"])
            if not _DOMAIN.fullmatch(domain):
                raise ValidationError(f"{where}: domain must be a DNS name such as example.org")
            out.append({"kind": "domain", "value": domain, "platform": platform})
    return out


def optout_source(path: str | Path | None = None) -> Path | None:
    """Explicit path, else $REACHWEAVER_OPTOUT, else config/optout.json if present, else None."""
    if path:
        return Path(path)
    env = os.environ.get(OPTOUT_ENV)
    if env:
        return Path(env)
    default = home.default_path(DEFAULT_OPTOUT_PATH)
    return default if default.is_file() else None


def load_optout(path: str | Path | None = None) -> list[dict]:
    """Load the opt-out list. No file configured means an empty list; a named but missing file is an error."""
    source = optout_source(path)
    if source is None:
        return []
    if not source.is_file():
        raise ValidationError(f"Opt-out file not found: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Opt-out file is unreadable or not JSON: {source} ({exc.__class__.__name__})") from None
    return validate_optout(raw)


def listed_entry(handle: object, entries: list[dict], platform: str | None = None) -> dict | None:
    """The first opt-out entry matching ``handle`` on ``platform`` (None when not listed)."""
    candidate = normalize_handle(handle)
    if not candidate or not entries:
        return None
    local, host = _split(candidate)
    for entry in entries:
        if entry["platform"] and platform and entry["platform"] != platform:
            continue
        value = entry["value"]
        if entry["kind"] == "handle":
            e_local, e_host = _split(value)
            if candidate == value or (local == e_local and (not host or not e_host)):
                return entry
        else:
            for name in (host, candidate if not host else ""):
                if name and (name == value or name.endswith("." + value)):
                    return entry
    return None


def mentioned_handles(text: object) -> list[tuple[str, int, int]]:
    """Every @-mention in ``text`` as (handle-without-@, start, end) of the '@...' span."""
    if not isinstance(text, str):
        return []
    return [(m.group(1), m.start(), m.end()) for m in MENTION.finditer(text)]


def optout_mentions(text: object, entries: list[dict], platform: str | None = None) -> list[dict]:
    """Mentions of opted-out handles/domains in ``text``; empty when the list is empty."""
    if not entries or not isinstance(text, str):
        return []
    found = []
    for handle, start, end in mentioned_handles(text):
        entry = listed_entry(handle, entries, platform)
        if entry is not None:
            found.append({"span": text[start:end], "start": start, "end": end,
                          "entry_kind": entry["kind"]})
    return found


def describe_mentions(hits: list[dict]) -> str:
    """Human-review wording for ``optout_mentions`` hits (names the spans so the owner can edit them)."""
    spans = ", ".join(f"{hit['span']!r} ({hit['entry_kind']})" for hit in hits)
    return f"optout_mention: {spans} on the local opt-out list; remove the mention/tag"


def publish_block_reason(text: object, entries: list[dict], platform: str | None = None) -> str | None:
    """Pre-claim publish gate wording, or None when ``text`` mentions nobody on the list.

    Publish output can land in unattended worker logs, so this names only a count,
    never the handle: the list itself is personal data about the people on it.
    """
    hits = optout_mentions(text, entries, platform)
    if not hits:
        return None
    noun = "mention" if len(hits) == 1 else "mentions"
    return f"optout_mention: {len(hits)} @-{noun} on the local opt-out list"
