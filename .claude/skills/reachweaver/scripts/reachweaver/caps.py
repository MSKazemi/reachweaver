"""Per-account posting caps file that can only TIGHTEN live send budgets (issues #26 and #11).

``config/caps.json`` (ignored; see ``config/caps.example.json``) holds optional
``defaults`` and per-account ``max_per_24h`` / ``min_gap_minutes`` values.
``publish --live`` passes the strictest of the CLI value, the file defaults and
the account entry into the unchanged ``storage.claim_sending`` parameters:

* ``max_per_24h``  = min(CLI, defaults, account)   (fewer attempts is stricter)
* ``min_gap_minutes`` = max(CLI, defaults, account) (a longer gap is stricter)

Version 2 (issue #11, owner decision D14, docs/SECURITY-REVIEW-PROPOSAL-7-11.md 2.4) adds an
optional ``providers`` block keyed by provider (``bluesky``, ``mastodon``, ``telegram``,
``linkedin``, ``threads``, ``facebook``, ``postiz``). A provider entry may carry the two budget keys above plus:

* ``cooldown_floor_seconds`` = max(built-in 60, provider entries)  (a longer minimum cooldown)
* ``preflight_max_retries`` = min(built-in 2, provider entries)    (fewer read-only retries)

A direct account uses its platform's entry; a Postiz-backed account uses both ``postiz`` and
its platform's entry. Version 1 files stay valid unchanged. A looser file value is ignored,
never applied. A malformed file fails closed: the live command refuses to run rather than
guessing a budget. Offline only.

``BUILTIN_PROVIDER_CAPS`` (owner decisions D19-4 and D21-5, 2026-10-09) holds pilot blocks that
apply with or without a file, tighten-only like a file entry: Threads at most 2 sends per 24 h;
Facebook Pages at most 2 per 24 h and at least 60 minutes apart.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import home
from .policy import ValidationError
from .inputs import read_json_file

CAPS_SCHEMA_VERSIONS = (1, 2)
CAPS_SCHEMA_VERSION = 2  # newest accepted; version 1 files remain valid
CAPS_ENV = "REACHWEAVER_CAPS"
DEFAULT_CAPS_PATH = Path("config/caps.json")
# Same ranges the CLI accepts, so a file value can never be outside a valid claim.
LIMITS = {"max_per_24h": (1, 10), "min_gap_minutes": (15, 1440)}
CLI_DEFAULTS = {"max_per_24h": 2, "min_gap_minutes": 60}
PROVIDERS = ("bluesky", "mastodon", "telegram", "linkedin", "threads", "facebook", "postiz")
# Built-in, tighten-only provider blocks for adapters whose pilot budget the owner fixed in review.
# They apply with or without a caps file, exactly like a file's provider entry (min max_per_24h,
# max min_gap_minutes), so neither the CLI flags nor a looser file can raise them.
BUILTIN_PROVIDER_CAPS: dict[str, dict[str, int]] = {
    # Facebook Page pilot (owner decision D21-5, 2026-10-09): 2 per 24 h and at least 60 minutes apart.
    "facebook": {"max_per_24h": 2, "min_gap_minutes": 60},
    # Threads pilot (owner decision D19-4, 2026-10-09): far below the documented 250 API posts per 24 h.
    "threads": {"max_per_24h": 2},
}
# Version 2 provider-only keys and their built-in values (ratelimit.FLOOR_SECONDS / PREFLIGHT_MAX_RETRIES).
RATE_LIMITS = {"cooldown_floor_seconds": (60, 86400), "preflight_max_retries": (0, 2)}
RATE_DEFAULTS = {"cooldown_floor_seconds": 60, "preflight_max_retries": 2}
PROVIDER_LIMITS = {**LIMITS, **RATE_LIMITS}


def _validate_block(block: object, where: str, limits: Mapping[str, tuple[int, int]] = LIMITS) -> dict:
    if not isinstance(block, dict):
        raise ValidationError(f"{where} must be an object")
    extra = sorted(set(block) - set(limits))
    if extra:
        raise ValidationError(f"{where}: unknown keys {', '.join(extra)}")
    out = {}
    for key, (low, high) in limits.items():
        if key in block:
            value = block[key]
            if type(value) is not int or not low <= value <= high:
                raise ValidationError(f"{where}.{key} must be an integer {low}..{high}")
            out[key] = value
    return out


def validate_caps(raw: object) -> dict:
    """Normalized {"version", "defaults", "accounts", "providers"}; any defect raises."""
    if not isinstance(raw, dict) or raw.get("version") not in CAPS_SCHEMA_VERSIONS \
            or type(raw.get("version")) is not int:
        raise ValidationError("Caps file must be an object with \"version\": 1 or 2")
    version = raw["version"]
    allowed = {"version", "defaults", "accounts"} | ({"providers"} if version >= 2 else set())
    unknown = sorted(set(raw) - allowed)
    if unknown:
        hint = " (the providers block needs \"version\": 2)" if "providers" in unknown else ""
        raise ValidationError(f"Caps file has unknown top-level keys: {', '.join(unknown)}{hint}")
    defaults = _validate_block(raw.get("defaults", {}), "caps.defaults")
    accounts_raw = raw.get("accounts", {})
    if not isinstance(accounts_raw, dict):
        raise ValidationError("caps.accounts must be an object keyed by account id")
    accounts = {str(k): _validate_block(v, f"caps.accounts.{k}") for k, v in accounts_raw.items()}
    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict):
        raise ValidationError("caps.providers must be an object keyed by provider")
    bad = sorted(str(k) for k in providers_raw if k not in PROVIDERS)
    if bad:
        raise ValidationError(f"caps.providers: unknown providers {', '.join(bad)} "
                              f"(known: {', '.join(PROVIDERS)})")
    providers = {str(k): _validate_block(v, f"caps.providers.{k}", PROVIDER_LIMITS)
                 for k, v in providers_raw.items()}
    return {"version": version, "defaults": defaults, "accounts": accounts, "providers": providers}


def caps_source(path: str | Path | None = None) -> Path | None:
    """Explicit path, else $REACHWEAVER_CAPS, else config/caps.json if present, else None."""
    if path:
        return Path(path)
    env = os.environ.get(CAPS_ENV)
    if env:
        return Path(env)
    default = home.default_path(DEFAULT_CAPS_PATH)
    return default if default.is_file() else None


def load_caps(path: str | Path | None = None) -> tuple[dict | None, Path | None]:
    """(caps, source). No configured file returns (None, None); a named missing file is an error."""
    source = caps_source(path)
    if source is None:
        return None, None
    if not source.is_file():
        raise ValidationError(f"Caps file not found: {source}")
    try:
        raw = read_json_file(source)
    except (OSError, ValueError) as exc:
        raise ValidationError(f"Caps file is unreadable or not JSON: {source} ({exc.__class__.__name__})") from None
    return validate_caps(raw), source


def providers_for(account: Mapping[str, Any]) -> tuple[str, ...]:
    """Provider entries that apply to an account: its platform, plus ``postiz`` when backed by it."""
    platform = str(account.get("platform", ""))
    names = (["postiz"] if account.get("backend") == "postiz" else []) + [platform]
    return tuple(name for name in names if name in PROVIDERS)


def _provider_blocks(caps: dict | None, providers: Iterable[str]) -> list[dict]:
    table = (caps or {}).get("providers", {})
    return [table[name] for name in providers if name in table]


def effective_caps(caps: dict | None, account_id: str, cli_max_per_24h: int,
                   cli_min_gap_minutes: int, *, providers: Iterable[str] = ()) -> tuple[int, int]:
    """Strictest (max_per_24h, min_gap_minutes) of CLI, file defaults, provider entries and the
    account entry.

    Tighten-only by construction: the result is never looser than the CLI values. Built-in pilot
    blocks (``BUILTIN_PROVIDER_CAPS``) apply even without a caps file.
    """
    max_per_24h, min_gap = cli_max_per_24h, cli_min_gap_minutes
    for name in providers:  # built-in pilot caps (BUILTIN_PROVIDER_CAPS), always applied
        block = BUILTIN_PROVIDER_CAPS.get(name, {})
        max_per_24h = min(max_per_24h, block.get("max_per_24h", max_per_24h))
        min_gap = max(min_gap, block.get("min_gap_minutes", min_gap))
    if caps:
        blocks = [caps.get("defaults", {}), *_provider_blocks(caps, providers),
                  caps.get("accounts", {}).get(account_id, {})]
        for block in blocks:
            if "max_per_24h" in block:
                max_per_24h = min(max_per_24h, block["max_per_24h"])
            if "min_gap_minutes" in block:
                min_gap = max(min_gap, block["min_gap_minutes"])
    return max_per_24h, min_gap


def effective_rate_limits(caps: dict | None, providers: Iterable[str] = ()) -> dict:
    """{"cooldown_floor_seconds", "preflight_max_retries"}: built-ins, tightened by provider entries."""
    floor = RATE_DEFAULTS["cooldown_floor_seconds"]
    retries = RATE_DEFAULTS["preflight_max_retries"]
    for block in _provider_blocks(caps, providers):
        floor = max(floor, block.get("cooldown_floor_seconds", floor))
        retries = min(retries, block.get("preflight_max_retries", retries))
    return {"cooldown_floor_seconds": floor, "preflight_max_retries": retries}
