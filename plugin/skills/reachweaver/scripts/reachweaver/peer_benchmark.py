"""Offline peer benchmark from public counters the owner copied by hand (public issue #11).

No fetch, no scraping, no login, no platform API, no private analytics, nothing stored. The command reads one
local JSON file in which the owner records, for each account, the public counters visible on its public page,
the page URL and the date seen. It reports where the owner's own counters sit among the listed peers.

Descriptive only. Public counters differ in meaning between platforms, can be edited or deleted, and say nothing
about causes, quality or private performance.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

from .policy import ValidationError
from .profile_audit import SECRET_KEY, SECRET_VALUE, load_registry

SCHEMA = "reachweaver.peer-benchmark.v1"
COUNTERS = frozenset({"followers", "following", "posts", "likes", "reposts", "replies", "views"})
KINDS = ("organization", "project", "community", "person")
DEFAULT_KINDS = frozenset({"organization", "project", "community"})
ENTRY_REQUIRED = frozenset({"label", "platform", "kind", "own", "public_data_only", "source_url", "observed_at", "counters"})
ENTRY_ALLOWED = ENTRY_REQUIRED
ROOT_ALLOWED = frozenset({"schema_version", "entries", "note"})
MIN_PEERS = 3
MAX_ENTRIES = 200
MAX_BYTES = 1_000_000
STALE_DAYS = 90
SPREAD_DAYS = 30
LABEL = re.compile(r"^[^\x00-\x1f]{1,100}$")


def _when(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValidationError("observed_at must be an ISO 8601 timestamp with a time zone")
    return parsed.astimezone(timezone.utc)


def _public_url(value: object) -> str:
    parts = urlsplit(value) if isinstance(value, str) else None
    if (parts is None or parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or len(value) > 300):  # type: ignore[arg-type]
        raise ValidationError("source_url must be a plain https page URL with no credentials, query or fragment")
    return value  # type: ignore[return-value]


def load_peers(path: Path, *, include_individuals: bool = False) -> dict:
    """Validate the owner's file. Errors say what is wrong, never echo the file's content."""
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValidationError("Peer benchmark input must be <=1 MB")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValidationError("Could not read the peer benchmark file as JSON (no network requests were made)") from exc
    _no_secrets(data)
    if not isinstance(data, dict) or "schema_version" not in data or "entries" not in data or set(data) - ROOT_ALLOWED:
        raise ValidationError("Peer benchmark file needs schema_version and entries, and nothing else but note")
    if data["schema_version"] != SCHEMA:
        raise ValidationError("Unsupported peer benchmark schema_version")
    entries = data["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValidationError(f"entries must list 1..{MAX_ENTRIES} accounts")
    platforms = {p["id"] for p in load_registry()["platforms"]}
    allowed_kinds = set(KINDS) if include_individuals else set(DEFAULT_KINDS)
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not ENTRY_REQUIRED <= entry.keys() or set(entry) - ENTRY_ALLOWED:
            raise ValidationError("Each entry needs exactly: " + ", ".join(sorted(ENTRY_REQUIRED)))
        if entry["platform"] not in platforms:
            raise ValidationError("Unsupported platform in an entry")
        if not isinstance(entry["label"], str) or not LABEL.fullmatch(entry["label"]):
            raise ValidationError("label must be a short single-line string")
        if entry["kind"] not in KINDS:
            raise ValidationError("kind must be one of " + ", ".join(KINDS))
        if entry["kind"] not in allowed_kinds:
            raise ValidationError("Individual people are excluded by default; pass --include-individuals only for "
                                  "accounts whose owners made the numbers public and after a privacy check")
        if type(entry["own"]) is not bool:
            raise ValidationError("own must be true or false")
        if entry["public_data_only"] is not True:
            raise ValidationError("public_data_only must be true: this command accepts only counters shown on a public page")
        _public_url(entry["source_url"])
        entry["_observed"] = _when(entry["observed_at"])
        counters = entry["counters"]
        if not isinstance(counters, dict) or not counters or set(counters) - COUNTERS:
            raise ValidationError("counters must name at least one of: " + ", ".join(sorted(COUNTERS)))
        for value in counters.values():
            if type(value) is not int or value < 0 or value > 10**12:
                raise ValidationError("Counter values must be non-negative whole numbers")
        key = (entry["platform"], entry["label"].casefold())
        if key in seen:
            raise ValidationError("Duplicate account entry for the same platform and label")
        seen.add(key)
    return data


def _no_secrets(obj, depth: int = 0) -> None:
    if depth > 6:
        raise ValidationError("Peer benchmark input nesting is too deep")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str) or SECRET_KEY.search(key):
                raise ValidationError("Peer benchmark input contains a forbidden credential-like field name")
            _no_secrets(value, depth + 1)
    elif isinstance(obj, list):
        for value in obj:
            _no_secrets(value, depth + 1)
    elif isinstance(obj, str) and (len(obj) > 2000 or SECRET_VALUE.search(obj)):
        raise ValidationError("Peer benchmark input may contain sensitive data; remove it before proceeding")


def benchmark(data: dict, *, platform: str | None = None, now: datetime | None = None) -> dict:
    """Own counters against the listed peers, per platform and counter. ``now`` makes staleness testable."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    entries = [e for e in data["entries"] if platform is None or e["platform"] == platform]
    if platform is not None and not entries:
        raise ValidationError("No entries for that platform")
    by_platform: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_platform[entry["platform"]].append(entry)
    reports = []
    for name, group in sorted(by_platform.items()):
        own = [e for e in group if e["own"]]
        peers = [e for e in group if not e["own"]]
        warnings: list[str] = []
        if len(own) != 1:
            warnings.append("Exactly one entry must have own: true for a comparison" if not own else
                            "More than one own entry; compare one account at a time")
        dates = [e["_observed"] for e in group]
        if (max(dates) - min(dates)).days > SPREAD_DAYS:
            warnings.append(f"Observation dates are more than {SPREAD_DAYS} days apart; counters are not from the same period")
        oldest = (moment - min(dates)).days
        if oldest > STALE_DAYS:
            warnings.append(f"The oldest observation is {oldest} days old (over {STALE_DAYS}); re-read the public pages")
        counters = []
        for counter in sorted(COUNTERS):
            mine = own[0]["counters"].get(counter) if len(own) == 1 else None
            values = sorted(p["counters"][counter] for p in peers if counter in p["counters"])
            row: dict = {"counter": counter, "own": mine, "peer_count": len(values)}
            if mine is None or not values:
                row["status"] = "unknown" if mine is None else "no_peer_values"
            elif len(values) < MIN_PEERS:
                row["status"] = "insufficient_peers"
                row["minimum_peers"] = MIN_PEERS
            else:
                med = median(values)
                row.update({
                    "status": "compared", "peer_min": values[0], "peer_median": med, "peer_max": values[-1],
                    "peers_at_or_below_own": sum(1 for v in values if v <= mine),
                    "ratio_to_peer_median": round(mine / med, 3) if med else None})
            counters.append(row)
        reports.append({"platform": name, "own_label": own[0]["label"] if len(own) == 1 else None,
                        "peer_count": len(peers), "counters": [c for c in counters if c["own"] is not None or c["peer_count"]],
                        "warnings": warnings,
                        "sources": [{"label": e["label"], "source_url": e["source_url"],
                                     "observed_at": e["_observed"].isoformat(timespec="seconds")} for e in group]})
    return {"schema_version": SCHEMA, "kind": "peer_benchmark", "status": "offline_descriptive_not_a_ranking",
            "platforms_covered": len(reports), "reports": reports,
            "limits": ["Public counters only, typed in by the owner from public pages. Nothing was fetched and nothing is stored.",
                       "Counters do not mean the same thing on every platform and can be edited, hidden or deleted by their owners.",
                       "A position among a few chosen peers is not a market ranking and not evidence of cause, quality or private performance.",
                       f"Fewer than {MIN_PEERS} peers with a value gives no comparison."]}


def render_summary(result: dict) -> str:
    lines = ["OFFLINE peer benchmark — public counters you supplied, NOT a ranking or a measure of private performance"]
    for report in result["reports"]:
        lines.append(f"{report['platform']}: {report['own_label'] or 'no single own entry'} vs {report['peer_count']} peers")
        for row in report["counters"]:
            if row["status"] == "compared":
                lines.append(f"  {row['counter']}: own {row['own']} | peer median {row['peer_median']:g} "
                             f"(min {row['peer_min']}, max {row['peer_max']}) | own is at or above {row['peers_at_or_below_own']} of {row['peer_count']} peers")
            else:
                lines.append(f"  {row['counter']}: {row['status'].replace('_', ' ')} (own {row['own'] if row['own'] is not None else 'unknown'}, {row['peer_count']} peer values)")
        for warning in report["warnings"]:
            lines.append(f"  WARNING: {warning}")
    lines.append("Nothing was fetched, changed or published; unknown is not zero.")
    return "\n".join(lines)
