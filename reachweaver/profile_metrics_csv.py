"""Consent-gated, OFFLINE normalized CSV snapshots for individual profile/post audits.

Reads only a local owner-provided CSV and an already validated owned profile
snapshot. No provider connection, OAuth, SQLite mutation, file creation or send.
It is a *local export normalization*, NOT native-platform verification.
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .policy import ValidationError
from .profile_audit import (
    ID, METRICS, REVIEW_FLAGS, SCHEMA, _reject_sensitive,
)

REQUIRED = frozenset({
    "platform", "account_id", "post_id", "format", "source",
    "published_at", "observed_at",
})
COLUMNS = REQUIRED | METRICS | REVIEW_FLAGS
MAX_CSV_BYTES = 2_000_000
MAX_POSTS = 100
_INT = re.compile(r"^(?:0|[1-9][0-9]{0,12})$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,6})?$")
DURATION_METRICS = frozenset({"watch_time_seconds", "average_view_duration_seconds"})


def _iso_aware(value: str, label: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Invalid {label}: expected offset-aware ISO 8601 datetime") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValidationError(f"Invalid {label}: explicit timezone offset required")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _numeric(value: str, field: str) -> int | float:
    if field in DURATION_METRICS:
        if not _DECIMAL.fullmatch(value):
            raise ValidationError(f"{field} must be a finite nonnegative duration up to 6 decimals")
        number = float(value)
    else:
        if not _INT.fullmatch(value):
            raise ValidationError(f"{field} must be a nonnegative integer count")
        number = int(value)
    if not math.isfinite(number) or number > 1e12:
        raise ValidationError(f"{field} exceeds safe native-observation bounds")
    return number


def merge_authorized_csv(snapshot: dict, csv_path: Path, *, owner_authorized: bool) -> tuple[dict, dict]:
    """Return a NEW in-memory snapshot with one observation per unique owner post.

    Explicit owner attestation is a consent checkpoint, not proof of ownership,
    provenance, genuine engagement, metric scope, or permission for any API call.
    """
    if owner_authorized is not True:
        raise ValidationError("Reading private post metrics requires explicit --owner-authorized acknowledgement")
    if snapshot.get("schema_version") != SCHEMA or not isinstance(snapshot.get("profiles"), list):
        raise ValidationError("Only a validated ReachWeaver owner profile snapshot may be enriched")
    if csv_path.is_symlink() or not csv_path.is_file():
        raise ValidationError("Metrics CSV must be an existing regular file; symlinks are not allowed")
    try:
        size = csv_path.stat().st_size
        if size < 1 or size > MAX_CSV_BYTES:
            raise ValidationError("Metrics CSV must be nonempty and <=2 MB")
        data = csv_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValidationError("Could not read local UTF-8 metrics CSV; no network request was made") from exc
    try:
        reader = csv.DictReader(io.StringIO(data, newline=""))
        headers = reader.fieldnames
        if not headers or len(headers) != len(set(headers)) or not REQUIRED <= set(headers) or set(headers) - COLUMNS:
            raise ValidationError("Invalid CSV headers: provide required platform/account/post/time/source columns and only documented metrics or review flags")
        profiles = {(p["platform"], p["account_id"]) for p in snapshot["profiles"]}
        known = {(p["platform"], p["account_id"], p["post_id"]) for p in snapshot.get("posts", [])}
        records = []
        counts: Counter[str] = Counter()
        for index, row in enumerate(reader, 1):
            if index > MAX_POSTS or len(records) + len(snapshot.get("posts", [])) >= MAX_POSTS:
                raise ValidationError("Audit snapshot allows at most 100 post observations")
            if None in row or any(value is None for value in row.values()):
                raise ValidationError("CSV row has missing or extra cells; check separators and quoting")
            _reject_sensitive(row)
            canonical = {k: v.strip() for k, v in row.items()}
            if not all(canonical[field] for field in REQUIRED):
                raise ValidationError("Each CSV post needs platform, account, post ID, format, source and two timestamps")
            platform, account = canonical["platform"], canonical["account_id"]
            if (platform, account) not in profiles:
                raise ValidationError("CSV refers to an unknown or unapproved platform/account in the owner profile snapshot")
            if not all(ID.fullmatch(canonical[field]) for field in ("account_id", "post_id", "format", "source")):
                raise ValidationError("CSV identifiers, formats and source labels must be short safe tokens")
            key = (platform, account, canonical["post_id"])
            if key in known:
                raise ValidationError("Duplicate native post observation; do not double-count cumulative metrics")
            known.add(key)
            published = _iso_aware(canonical["published_at"], "published_at")
            observed = _iso_aware(canonical["observed_at"], "observed_at")
            if observed < published:
                raise ValidationError("Observed native metrics cannot precede publishing")
            metrics: dict[str, int | float] = {}
            flags: dict[str, bool] = {}
            for field in METRICS:
                value = canonical.get(field, "")
                if value:
                    metrics[field] = _numeric(value, field)
            for field in REVIEW_FLAGS:
                value = canonical.get(field, "")
                if value:
                    if value not in ("true", "false"):
                        raise ValidationError(f"{field} must be true, false or blank (unknown)")
                    flags[field] = value == "true"
            records.append({
                "platform": platform, "account_id": account,
                "post_id": canonical["post_id"], "format": canonical["format"],
                "source": canonical["source"],
                "published_at": published, "observed_at": observed,
                "metrics": metrics, "review_flags": flags,
            })
            counts[platform] += 1
    except csv.Error as exc:
        raise ValidationError("Invalid CSV quoting or encoding; no files were changed") from exc
    if not records:
        raise ValidationError("CSV contains no native post observations")
    result = {**snapshot, "posts": [*snapshot.get("posts", []), *records]}
    return result, {
        "kind": "owner_acknowledged_offline_metrics_csv",
        "acknowledgement_is_not_independent_ownership_verification": True,
        "rows_added": len(records),
        "total_posts": len(result["posts"]),
        "platforms": dict(sorted(counts.items())),
        "notes": [
            "The source label is supplied by the owner, not cryptographically verified.",
            "Only one cumulative metric observation per unique platform/account/post is accepted.",
            "Missing numeric cells remain unknown, never zero. Explicit zero is observed zero.",
            "The CSV was never transmitted or written; no account or provider was contacted.",
        ],
    }
