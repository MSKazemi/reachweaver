"""Versioned canonical contracts (schema_version "1.2") and the publish state machine.

Stdlib-only declarative validation: no jsonschema dependency, no network, no storage
writes. Connector-specific fields live only in ``PlatformVariant.platform_options``;
everything upstream of a connector is vendor-neutral. See docs/SCHEMAS.md.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from .accounts import ACCOUNT_ID, ENV_PREFIX, SECRET_FIELD
from .campaigns import OBJECTIVES, public_url
from .policy import PLATFORMS, ValidationError, require_https_public_server, validate_post
from .storage import METRIC_FIELDS, METRIC_STATUSES

# One version for the whole contract set, not one per type. 1.1 is a MINOR bump: it only
# adds the optional AnalyticsSnapshot window_start/window_end, so every 1.0 document stays valid.
# 1.2 (#15) is another MINOR bump: it only adds the optional AnalyticsSnapshot metric_status.
SCHEMA_VERSION = "1.2"
SUPPORTED_VERSIONS = ("1.0", "1.1", "1.2")

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
REMOTE_ID = re.compile(r"^[^\s\x00-\x1f]{1,512}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DOI = re.compile(r"^10\.\d{4,9}/[^\s]+$")
LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|\d{3}))?$")  # BCP-47 subset
RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
MIME = re.compile(r"^(?:image|video|application|text)/[a-z0-9.+-]{1,64}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
UTM_VALUE = re.compile(r"^[A-Za-z0-9._~-]{1,100}$")

STATES = ("draft", "reviewed", "approved", "scheduled", "publishing",
          "published", "failed", "unknown", "cancelled")
TRANSITIONS = {
    "draft": {"reviewed", "cancelled"},
    "reviewed": {"approved", "draft", "cancelled"},
    "approved": {"scheduled", "publishing", "draft", "cancelled"},
    "scheduled": {"publishing", "approved", "draft", "cancelled"},
    "publishing": {"published", "failed", "unknown"},
    "unknown": {"published", "failed"},  # human/receipt reconciliation only; never re-send
    "failed": {"draft"},                 # confirmed not sent: re-review AND re-approve
    "published": set(),
    "cancelled": set(),
}
APPROVAL_GATED = frozenset({"approved", "scheduled", "publishing"})
# Documentation-only mapping of current storage.posts.status values; storage is unchanged.
STORAGE_STATUS_MAP = {
    "draft": "draft", "approved": "approved", "scheduled": "scheduled",
    "sending": "publishing", "published": "published", "needs_review": "unknown",
    "cancelled": "cancelled",
}


@dataclass(frozen=True)
class F:
    """One declarative field rule."""
    kind: str                      # str int num bool ts url dict list obj
    required: bool = True
    enum: frozenset | None = None
    pattern: re.Pattern | None = None
    items: "F | None" = None
    schema: str | None = None
    min: float | None = None
    max: float | None = None
    nullable: bool = False
    unique: bool = False
    since: str = "1.0"             # schema_version that introduced the field (minor bumps only)


def _opt(kind: str, **kw) -> F:
    return F(kind, required=False, **kw)


def _ids(min_items: int = 0) -> F:
    return F("list", items=F("str", pattern=ID), min=min_items, max=100, unique=True,
             required=min_items > 0)


_PLATFORM = F("str", enum=PLATFORMS)
_ACCOUNT = F("str", pattern=ACCOUNT_ID)
_METRICS = frozenset(METRIC_FIELDS)

SCHEMAS: dict[str, dict[str, F]] = {
    "UTM": {
        "source": F("str", pattern=UTM_VALUE), "medium": F("str", pattern=UTM_VALUE),
        "campaign": F("str", pattern=UTM_VALUE), "term": _opt("str", pattern=UTM_VALUE),
        "content": _opt("str", pattern=UTM_VALUE),
    },
    "Cost": {
        "amount": F("num", min=0), "currency": F("str", pattern=CURRENCY),
        "kind": F("str", enum=frozenset({"api", "media_generation", "ad_spend", "tooling", "labor", "other"})),
    },
    "Consent": {
        "subject": F("str", enum=frozenset({"account_owner", "people_depicted", "contributor", "rights_holder"})),
        "obtained": F("bool"), "recorded_at": F("ts"), "reference": _opt("str", max=300),
    },
    "Metrics": {key: _opt("int", min=0, nullable=True) for key in METRIC_FIELDS},
    # 1.2: why a metric has no exact count (mirrors storage.metric_status). An exact count stays
    # in Metrics; a metric listed here must be null/absent there. Absent from both = unknown.
    "MetricStatus": {"status": F("str", enum=frozenset(METRIC_STATUSES)), "value": _opt("int", min=0)},
    "MetricStatuses": {key: _opt("obj", schema="MetricStatus") for key in METRIC_FIELDS},
    "SourceSignal": {
        "id": F("str", pattern=ID), "brand_id": F("str", pattern=SLUG),
        "kind": F("str", enum=frozenset({"github_release", "paper", "repository", "blog", "demo",
                                         "dataset", "talk", "other"})),
        "title": F("str", max=300), "url": _opt("url"), "doi": _opt("str", pattern=DOI),
        "published_at": _opt("ts"), "observed_at": F("ts"), "summary": _opt("str", max=2000),
    },
    "Evidence": {
        "id": F("str", pattern=ID), "source_signal_id": _opt("str", pattern=ID),
        "claim": F("str", max=1000), "url": _opt("url"), "doi": _opt("str", pattern=DOI),
        "excerpt": _opt("str", max=500),
        "verification": F("str", enum=frozenset({"primary_source_checked", "owner_attested"})),
        "checked_by": F("str", max=120), "checked_at": F("ts"),
    },
    "ContentBrief": {
        "id": F("str", pattern=ID), "brand_id": F("str", pattern=SLUG),
        "campaign_id": F("str", pattern=SLUG), "audience_id": F("str", pattern=SLUG),
        "objective": F("str", enum=OBJECTIVES), "locale": F("str", pattern=LOCALE),
        "key_message": F("str", max=1000), "cta": _opt("str", max=300),
        "evidence_ids": _ids(1), "utm": _opt("obj", schema="UTM"), "created_at": F("ts"),
    },
    "Draft": {
        "id": F("str", pattern=ID), "brief_id": F("str", pattern=ID),
        "brand_id": F("str", pattern=SLUG), "campaign_id": F("str", pattern=SLUG),
        "locale": F("str", pattern=LOCALE), "body": F("str", max=30000),
        "evidence_ids": _ids(1),
        "authorship": F("str", enum=frozenset({"human", "ai_assisted", "ai_generated"})),
        "created_at": F("ts"),
    },
    "MediaAsset": {
        "id": F("str", pattern=ID),
        "kind": F("str", enum=frozenset({"image", "video", "gif", "document"})),
        "mime_type": F("str", pattern=MIME), "uri": F("str", max=2000),
        "sha256": F("str", pattern=SHA256), "bytes": F("int", min=1),
        "width": _opt("int", min=1), "height": _opt("int", min=1),
        "duration_seconds": _opt("num", min=0.1, max=86400),
        "alt_text": _opt("str", max=1500), "has_captions": _opt("bool"),
        "transcript_uri": _opt("str", max=2000), "license": F("str", max=200),
        "people_depicted": F("bool"),
        "consent": _opt("list", items=F("obj", schema="Consent"), max=20),
    },
    "PlatformVariant": {
        "id": F("str", pattern=ID), "draft_id": F("str", pattern=ID),
        "brand_id": F("str", pattern=SLUG), "campaign_id": F("str", pattern=SLUG),
        "platform": _PLATFORM, "account_id": _ACCOUNT, "locale": F("str", pattern=LOCALE),
        "format": F("str", enum=frozenset({"text", "image", "video", "carousel", "link", "article"})),
        "text": F("str", max=30000),
        "media": _opt("list", items=F("obj", schema="MediaAsset"), max=20),
        "link_url": _opt("url"), "utm": _opt("obj", schema="UTM"),
        "platform_options": _opt("dict"), "created_at": F("ts"),
    },
    "Account": {
        "id": _ACCOUNT, "platform": _PLATFORM, "brand_id": F("str", pattern=SLUG),
        "display_name": F("str", max=200), "handle": _opt("str", max=200),
        "server": _opt("str", max=300), "chat_id": _opt("str", max=100),
        "env_prefix": F("str", pattern=ENV_PREFIX),
        "audience_ids": _opt("list", items=F("str", pattern=SLUG), max=50, unique=True),
        "locale": _opt("str", pattern=LOCALE),
        "consent": F("list", items=F("obj", schema="Consent"), min=1, max=20),
    },
    "Approval": {
        "id": F("str", pattern=ID), "variant_id": F("str", pattern=ID),
        "account_id": _ACCOUNT, "platform": _PLATFORM,
        "approved_payload_sha256": F("str", pattern=SHA256),
        "decision": F("str", enum=frozenset({"approved", "revoked"})),
        "reviewer_kind": F("str", enum=frozenset({"human"})),
        "approved_by": F("str", max=120), "approved_at": F("ts"), "expires_at": _opt("ts"),
        "notes": _opt("str", max=2000),
    },
    "PublishJob": {
        "id": F("str", pattern=ID), "variant_id": F("str", pattern=ID),
        "approval_id": _opt("str", pattern=ID), "brand_id": F("str", pattern=SLUG),
        "campaign_id": F("str", pattern=SLUG), "account_id": _ACCOUNT, "platform": _PLATFORM,
        "state": F("str", enum=frozenset(STATES)),
        "idempotency_key": F("str", pattern=IDEMPOTENCY_KEY),
        "payload": F("obj", schema="PlatformVariant"), "payload_sha256": F("str", pattern=SHA256),
        "scheduled_at": _opt("ts"), "attempts": F("int", min=0, max=1000),
        "remote_post_id": _opt("str", pattern=REMOTE_ID), "remote_url": _opt("url"),
        "error_code": _opt("str", pattern=ERROR_CODE),
        "cost": _opt("list", items=F("obj", schema="Cost"), max=20),
        "created_at": F("ts"), "updated_at": F("ts"),
    },
    "PublishReceipt": {
        "id": F("str", pattern=ID), "job_id": F("str", pattern=ID),
        "account_id": _ACCOUNT, "platform": _PLATFORM,
        "idempotency_key": F("str", pattern=IDEMPOTENCY_KEY),
        "outcome": F("str", enum=frozenset({"published", "failed", "unknown"})),
        "remote_post_id": _opt("str", pattern=REMOTE_ID), "remote_url": _opt("url"),
        "error_code": _opt("str", pattern=ERROR_CODE),
        "evidence": F("str", enum=frozenset({"api_response", "owner_verified_native_account", "none"})),
        "observed_at": F("ts"), "cost": _opt("list", items=F("obj", schema="Cost"), max=20),
    },
    "AnalyticsSnapshot": {
        "id": F("str", pattern=ID), "job_id": F("str", pattern=ID),
        "account_id": _ACCOUNT, "platform": _PLATFORM,
        "remote_post_id": F("str", pattern=REMOTE_ID), "campaign_id": _opt("str", pattern=SLUG),
        "observed_at": F("ts"), "post_age_hours": _opt("num", min=0),
        "source": F("str", pattern=SLUG), "metrics": F("obj", schema="Metrics"),
        # 1.1: the period the counts cover. Both or neither; absent means an unknown window
        # (lifetime cumulative or a provider-defined period), never a guessed one.
        "window_start": _opt("ts", since="1.1"), "window_end": _opt("ts", since="1.1"),
        # 1.2: per-metric status for values that are not exact counts (suppressed,
        # not_available, delayed, estimated + value). Estimates are never exact metrics.
        "metric_status": _opt("obj", schema="MetricStatuses", since="1.2"),
    },
    "Experiment": {
        "id": F("str", pattern=ID), "campaign_id": F("str", pattern=SLUG),
        "hypothesis": F("str", max=2000), "primary_metric": F("str", enum=_METRICS),
        "denominator": F("str", enum=frozenset({"views", "impressions"})),
        "guardrail_metric": _opt("str", enum=_METRICS),
        "variant_ids": F("list", items=F("str", pattern=ID), min=2, max=50, unique=True),
        "age_hours": F("int", min=24, max=720), "tolerance_hours": F("int", min=1, max=168),
        "min_pairs": F("int", min=1, max=1000),
        "status": F("str", enum=frozenset({"planned", "running", "completed", "abandoned"})),
        "declared_at": F("ts"),
    },
}
# Public, top-level document types (the rest are embedded helper objects).
TYPES = ("SourceSignal", "Evidence", "ContentBrief", "Draft", "PlatformVariant", "MediaAsset",
         "Account", "Approval", "PublishJob", "PublishReceipt", "AnalyticsSnapshot", "Experiment")


def canonical_json(obj) -> bytes:
    """Deterministic UTF-8 JSON: sorted keys, no whitespace, no NaN/Infinity."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _version_key(version) -> tuple[int, int] | None:
    """A supported ``"MAJOR.MINOR"`` as a comparable tuple; ``None`` for anything else."""
    if not isinstance(version, str) or version not in SUPPORTED_VERSIONS:
        return None
    major, minor = version.split(".")
    return int(major), int(minor)


def _ts(value) -> datetime | None:
    if not isinstance(value, str) or not RFC3339_UTC.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _secret_keys(obj, path: str, errors: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if SECRET_FIELD.search(str(key)):
                errors.append(f"{path}.{key}: secret-like field names are forbidden; use environment variables")
            _secret_keys(value, f"{path}.{key}", errors)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _secret_keys(value, f"{path}[{index}]", errors)


def _value(spec: F, value, path: str, errors: list[str], version: str = SCHEMA_VERSION) -> None:
    if value is None:
        if not spec.nullable:
            errors.append(f"{path}: must not be null")
        return
    kind, before = spec.kind, len(errors)
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: must be a nonempty string")
        elif len(value) > (spec.max or 5000):
            errors.append(f"{path}: longer than {int(spec.max or 5000)} characters")
        elif any(ord(ch) < 32 and ch not in "\n\t\r" for ch in value):
            errors.append(f"{path}: contains control characters")
        elif spec.pattern and not spec.pattern.fullmatch(value):
            errors.append(f"{path}: invalid format")
    elif kind in ("int", "num"):
        ok = isinstance(value, int) if kind == "int" else isinstance(value, (int, float))
        if isinstance(value, bool) or not ok or (isinstance(value, float) and not math.isfinite(value)):
            errors.append(f"{path}: must be {'an integer' if kind == 'int' else 'a finite number'}")
        elif (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
            errors.append(f"{path}: out of range [{spec.min}, {spec.max}]")
    elif kind == "bool":
        if not isinstance(value, bool):
            errors.append(f"{path}: must be true or false")
    elif kind == "ts":
        if _ts(value) is None:
            errors.append(f"{path}: must be an RFC 3339 UTC timestamp, e.g. 2026-10-15T07:00:00Z")
    elif kind == "url":
        if not public_url(value):
            errors.append(f"{path}: must be a public https URL without credentials or secret query keys")
    elif kind == "dict":
        if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
            errors.append(f"{path}: must be an object with string keys")
        else:
            try:
                if len(canonical_json(value)) > 20000:
                    errors.append(f"{path}: larger than 20000 bytes")
            except (TypeError, ValueError):
                errors.append(f"{path}: must contain JSON values only")
    elif kind == "list":
        if not isinstance(value, list):
            errors.append(f"{path}: must be a list")
        elif len(value) < (spec.min or 0) or (spec.max is not None and len(value) > spec.max):
            errors.append(f"{path}: must contain {int(spec.min or 0)}..{spec.max} items")
        else:
            for index, item in enumerate(value):
                _value(cast(F, spec.items), item, f"{path}[{index}]", errors, version)
            if spec.unique and len(errors) == before and len({canonical_json(i) for i in value}) != len(value):
                errors.append(f"{path}: items must be unique")
    elif kind == "obj":
        _object(cast(str, spec.schema), value, path, errors, top=False, version=version)
    if spec.enum is not None and len(errors) == before and value not in spec.enum:
        errors.append(f"{path}: must be one of {', '.join(sorted(spec.enum))}")


def _object(kind: str, obj, path: str, errors: list[str], *, top: bool,
            version: str = SCHEMA_VERSION) -> None:
    """Check ``obj`` against the fields ``kind`` had in schema ``version``.

    ``version`` is the enclosing document's version. An envelope type may declare its own:
    it must be supported and, when embedded, not newer than its container. A migrated 1.1
    job keeps its approved 1.0 payload byte for byte, so the approval hash still binds it.
    """
    if not isinstance(obj, dict):
        errors.append(f"{path}: must be a JSON object")
        return
    fields, before = SCHEMAS[kind], len(errors)
    envelope = kind in TYPES
    if envelope:
        declared_version = obj.get("schema_version")
        declared_key = _version_key(declared_version)
        if declared_version is None and top:
            errors.append(f"{path}.schema_version: required")
        elif declared_version is not None and declared_key is None:
            errors.append(f"{path}.schema_version: {declared_version!r} is not one of "
                          f"{', '.join(SUPPORTED_VERSIONS)}; run migrate() first")
        elif declared_key is not None and not top and declared_key > (_version_key(version) or declared_key):
            errors.append(f"{path}.schema_version: {declared_version!r} is newer than its container ({version})")
        elif declared_key is not None:
            version = cast(str, declared_version)
    current = _version_key(version) or _version_key(SCHEMA_VERSION)
    for key in obj:
        since = _version_key(fields[key].since) if key in fields else None
        if since is not None and current is not None and since > current:
            errors.append(f"{path}.{key}: added in schema {fields[key].since}; "
                          f"declare schema_version {fields[key].since} or later")
        elif key not in fields and not (envelope and key in ("schema_version", "type")):
            errors.append(f"{path}.{key}: unknown field" + (" (put connector fields in platform_options)" if kind == "PlatformVariant" else ""))
    if envelope:
        declared = obj.get("type")
        if declared is None and top:
            errors.append(f"{path}.type: required")
        elif declared is not None and declared != kind:
            errors.append(f"{path}.type: expected {kind}, got {declared!r}")
    for name, spec in fields.items():
        if name not in obj:
            if spec.required:
                errors.append(f"{path}.{name}: required")
            continue
        since = _version_key(spec.since)
        if since is None or current is None or since <= current:  # newer fields were reported above
            _value(spec, obj[name], f"{path}.{name}", errors, version)
    if len(errors) == before and kind in _RULES:
        _RULES[kind](obj, path, errors)


def _need_url_or_doi(obj, path, errors):
    if "url" not in obj and "doi" not in obj:
        errors.append(f"{path}: needs a traceable url or doi")


def _media_rules(obj, path, errors):
    kind, mime = obj["kind"], obj["mime_type"]
    for name in ("uri", "transcript_uri"):
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", obj.get(name, "")) and not public_url(obj[name]):
            errors.append(f"{path}.{name}: remote URIs must be public https; otherwise use a relative path")
    if kind in ("image", "gif") and not mime.startswith("image/"):
        errors.append(f"{path}.mime_type: {kind} needs an image/* type")
    if kind == "video" and not mime.startswith("video/"):
        errors.append(f"{path}.mime_type: video needs a video/* type")
    if kind in ("image", "gif") and "alt_text" not in obj:
        errors.append(f"{path}.alt_text: required for {kind} accessibility")
    if kind == "video":
        for name in ("duration_seconds", "has_captions"):
            if name not in obj:
                errors.append(f"{path}.{name}: required for video")
        if obj.get("has_captions") is False and "transcript_uri" not in obj:
            errors.append(f"{path}: uncaptioned video needs transcript_uri")
    if obj["people_depicted"] and not any(c["subject"] == "people_depicted" and c["obtained"]
                                          for c in obj.get("consent", [])):
        errors.append(f"{path}.consent: depicted people need recorded obtained consent")


def _variant_rules(obj, path, errors):
    for issue in validate_post(obj["platform"], obj["text"]):
        errors.append(f"{path}.text: {issue}")
    media, fmt = obj.get("media", []), obj["format"]
    kinds = [m["kind"] for m in media]
    expected = {
        "text": not media, "article": not media, "link": not media and "link_url" in obj,
        "image": bool(kinds) and all(k in ("image", "gif") for k in kinds),
        "video": kinds == ["video"],
        "carousel": len(kinds) >= 2 and all(k in ("image", "video") for k in kinds),
    }[fmt]
    if not expected:
        errors.append(f"{path}.media: does not match format {fmt!r}")
    if "utm" in obj and "link_url" not in obj:
        errors.append(f"{path}.utm: requires link_url")


def _account_rules(obj, path, errors):
    if not any(c["subject"] == "account_owner" and c["obtained"] for c in obj["consent"]):
        errors.append(f"{path}.consent: owner authorization (account_owner, obtained=true) required")
    platform = obj["platform"]
    if platform == "mastodon":
        try:
            require_https_public_server(str(obj.get("server", "")))
        except ValidationError as exc:
            errors.append(f"{path}.server: {exc}")
    elif platform == "bluesky" and "handle" not in obj:
        errors.append(f"{path}.handle: required for bluesky")
    elif platform == "telegram" and "chat_id" not in obj:
        errors.append(f"{path}.chat_id: required for telegram")


def _approval_rules(obj, path, errors):
    if "expires_at" in obj and _ts(obj["expires_at"]) <= _ts(obj["approved_at"]):
        errors.append(f"{path}.expires_at: must be after approved_at")


def _outcome_rules(obj, path, errors, outcome):
    if outcome == "published" and "remote_post_id" not in obj:
        errors.append(f"{path}.remote_post_id: required when published")
    if outcome != "published" and ("remote_post_id" in obj or "remote_url" in obj):
        errors.append(f"{path}.remote_post_id: only allowed when published")
    if outcome in ("failed", "unknown") and "error_code" not in obj:
        errors.append(f"{path}.error_code: required when {outcome}")


def _job_rules(obj, path, errors):
    payload = obj["payload"]
    for name, other in (("variant_id", "id"), ("account_id", "account_id"), ("platform", "platform"),
                        ("brand_id", "brand_id"), ("campaign_id", "campaign_id")):
        if obj[name] != payload[other]:
            errors.append(f"{path}.{name}: {obj[name]!r} does not match payload.{other} {payload[other]!r}")
    if obj["payload_sha256"] != payload_sha256(payload):
        errors.append(f"{path}.payload_sha256: does not match canonical payload hash")
    state = obj["state"]
    if state in APPROVAL_GATED | {"published", "failed", "unknown"} and "approval_id" not in obj:
        errors.append(f"{path}.approval_id: required in state {state}")
    if state == "scheduled" and "scheduled_at" not in obj:
        errors.append(f"{path}.scheduled_at: required in state scheduled")
    _outcome_rules(obj, path, errors, state if state in ("published", "failed", "unknown") else "pending")
    if state not in ("failed", "unknown") and "error_code" in obj and state != "draft":
        errors.append(f"{path}.error_code: only allowed for failed/unknown (or a returned draft)")


def _receipt_rules(obj, path, errors):
    _outcome_rules(obj, path, errors, obj["outcome"])
    if obj["outcome"] == "published" and obj["evidence"] == "none":
        errors.append(f"{path}.evidence: a published receipt needs first-party evidence")


def _metric_status_rules(obj, path, errors):
    if obj["status"] == "estimated" and "value" not in obj:
        errors.append(f"{path}.value: an estimated status needs the platform's estimate")
    elif obj["status"] != "estimated" and "value" in obj:
        errors.append(f"{path}.value: only an estimated status carries a value")


def _snapshot_rules(obj, path, errors):
    statuses = obj.get("metric_status") or {}
    if all(obj["metrics"].get(k) is None for k in METRIC_FIELDS) and not statuses:
        errors.append(f"{path}.metrics: record at least one observed metric or metric_status; "
                      "missing is unknown, not zero")
    for key in statuses:
        if obj["metrics"].get(key) is not None:
            errors.append(f"{path}.metric_status.{key}: a metric is either an exact count in metrics "
                          "or a status here, not both")
    # Mirrors storage.observation_window: both bounds or neither, start <= end <= observed_at.
    if ("window_start" in obj) != ("window_end" in obj):
        errors.append(f"{path}.window_start: give both window_start and window_end, or neither (unknown window)")
    elif "window_start" in obj:
        start, end, observed = _ts(obj["window_start"]), _ts(obj["window_end"]), _ts(obj["observed_at"])
        if start is None or end is None or observed is None:  # unreachable: fields validated first
            return
        if start > end:
            errors.append(f"{path}.window_start: must not be after window_end")
        if end > observed:
            errors.append(f"{path}.window_end: must not be after observed_at")


def _experiment_rules(obj, path, errors):
    if obj.get("guardrail_metric") == obj["primary_metric"]:
        errors.append(f"{path}.guardrail_metric: must differ from primary_metric")


_RULES = {
    "SourceSignal": _need_url_or_doi, "Evidence": _need_url_or_doi, "MediaAsset": _media_rules,
    "PlatformVariant": _variant_rules, "Account": _account_rules, "Approval": _approval_rules,
    "PublishJob": _job_rules, "PublishReceipt": _receipt_rules,
    "AnalyticsSnapshot": _snapshot_rules, "Experiment": _experiment_rules,
    "MetricStatus": _metric_status_rules,
}


def validate(kind: str, obj) -> list[str]:
    """Return every contract error for a top-level document; empty means valid."""
    if kind not in TYPES:
        raise ValidationError(f"Unknown schema type {kind!r}; expected one of {', '.join(TYPES)}")
    errors: list[str] = []
    _secret_keys(obj, kind, errors)
    _object(kind, obj, kind, errors, top=True)
    return errors


def require_valid(kind: str, obj) -> dict:
    errors = validate(kind, obj)
    if errors:
        raise ValidationError("; ".join(errors))
    return obj


def _minor_step(doc: dict) -> dict:
    """A minor step only adds optional fields, so content is unchanged (migration rule 2)."""
    return doc


# Version steps: {from_version: (to_version, pure function)}. migrate() sets the top-level
# schema_version after each step. Embedded documents keep theirs: rewriting an approved
# payload would change its hash and void the approval (migration rule 6).
MIGRATIONS: dict[str, tuple[str, Callable[[dict], dict]]] = {"1.0": ("1.1", _minor_step),
                                                              "1.1": ("1.2", _minor_step)}


def migrate(doc: dict) -> dict:
    """Return a copy upgraded to SCHEMA_VERSION; never mutates input; unknown versions fail."""
    if not isinstance(doc, dict):
        raise ValidationError("Only JSON objects can be migrated")
    result, seen = copy.deepcopy(doc), set()
    while result.get("schema_version") != SCHEMA_VERSION:
        version = result.get("schema_version")
        if not isinstance(version, str) or version not in MIGRATIONS or version in seen:
            raise ValidationError(f"Unsupported schema_version {version!r}; supported: {', '.join(SUPPORTED_VERSIONS)}")
        seen.add(version)
        target, step = MIGRATIONS[version]
        result = step(result)
        result["schema_version"] = target
    return result


def _bind_approval(job: dict, approval: dict | None, now: datetime) -> None:
    if approval is None:
        raise ValidationError(f"Job {job['id']} is not approved: a human Approval is required")
    require_valid("Approval", approval)
    if approval["decision"] != "approved":
        raise ValidationError("Approval was revoked")
    if job.get("approval_id") != approval["id"] or approval["variant_id"] != job["variant_id"]:
        raise ValidationError("Approval does not belong to this job/variant")
    if approval["account_id"] != job["account_id"]:
        raise ValidationError(f"Wrong account: approved for {approval['account_id']}, job targets {job['account_id']}")
    if approval["platform"] != job["platform"]:
        raise ValidationError(f"Cross-platform mismatch: approved for {approval['platform']}, job targets {job['platform']}")
    if approval["approved_payload_sha256"] != payload_sha256(job["payload"]):
        raise ValidationError("Payload changed after approval (sha256 mismatch); re-review and re-approve")
    if "expires_at" in approval and now >= cast(datetime, _ts(approval["expires_at"])):
        raise ValidationError("Approval expired")


def _bind_receipt(job: dict, receipt: dict | None, target: str) -> None:
    if receipt is None:
        raise ValidationError(f"Moving to {target} requires a PublishReceipt")
    require_valid("PublishReceipt", receipt)
    for name, theirs in (("id", "job_id"), ("account_id", "account_id"), ("platform", "platform"),
                         ("idempotency_key", "idempotency_key")):
        if job[name] != receipt[theirs]:
            raise ValidationError(f"Receipt {theirs} does not match job {name}")
    if receipt["outcome"] != target:
        raise ValidationError(f"Receipt outcome {receipt['outcome']} cannot move job to {target}")


def transition_job(job: dict, target: str, *, approval: dict | None = None,
                   receipt: dict | None = None, account: dict | None = None,
                   prior_receipts=(), paused: bool = False, now: str | None = None) -> dict:
    """Pure canonical lifecycle step. Returns a new job dict; never sends or persists anything."""
    require_valid("PublishJob", job)
    if target not in STATES:
        raise ValidationError(f"Unknown state {target!r}")
    state = job["state"]
    if state == "published" and target in ("publishing", "published"):
        raise ValidationError("Replay rejected: job already published")
    if target not in TRANSITIONS[state]:
        raise ValidationError(f"Illegal transition {state} -> {target}")
    when = _ts(now) if now else datetime.now(timezone.utc).replace(microsecond=0)
    if when is None:
        raise ValidationError("now must be an RFC 3339 UTC timestamp")
    if account is not None:
        require_valid("Account", account)
        if account["id"] != job["account_id"] or account["platform"] != job["platform"]:
            raise ValidationError("Wrong account: Account document does not match job account/platform")
    if target in APPROVAL_GATED:
        _bind_approval(job, approval, when)
    if target == "scheduled" and "scheduled_at" not in job:
        raise ValidationError("Set scheduled_at before scheduling")
    if target == "publishing":
        if paused:
            raise ValidationError("Emergency pause is active")
        if state == "scheduled" and cast(datetime, _ts(job["scheduled_at"])) > when:
            raise ValidationError("Job is not due yet")
        for prior in prior_receipts:
            if prior.get("idempotency_key") == job["idempotency_key"] and prior.get("outcome") in ("published", "unknown"):
                raise ValidationError("Replay rejected: idempotency key already has a published/unknown receipt")
    new = copy.deepcopy(job)
    new["state"], new["updated_at"] = target, when.isoformat().replace("+00:00", "Z")
    if target == "publishing":
        new["attempts"] += 1
        new.pop("error_code", None)
    if target in ("published", "failed", "unknown"):
        _bind_receipt(job, receipt, target)
        for name in ("remote_post_id", "remote_url", "error_code"):
            new.pop(name, None)
            if name in cast(dict, receipt):
                new[name] = cast(dict, receipt)[name]
    if target == "draft":
        new.pop("approval_id", None)
    if target in ("draft", "approved", "cancelled") and state == "scheduled":
        new.pop("scheduled_at", None)
    return require_valid("PublishJob", new)
