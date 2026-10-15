"""Offline profile and post-performance audits from owner-supplied snapshots.

No web fetch, platform API, account login, secret access, writes or publishing.
Scores describe *observed completeness*, not reach, ranking or causal effects.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from urllib.parse import parse_qsl, urlsplit

from .policy import ValidationError

REGISTRY = Path(__file__).with_name("profile_audit_registry.json")
SCHEMA = "reachweaver.profile-post-audit.v1"
SECRET_KEY = re.compile(r"(^|_)(token|api_key|secret|password|cookie|session|authorization|credential)(_|$)", re.I)
SECRET_VALUE = re.compile(r"(?:github_pat_|ghp_|gho_|sk-[a-zA-Z0-9]{10}|bearer\s+[a-zA-Z0-9]{8})", re.I)
VAGUE_CLAIM = re.compile(r"\b(?:guaranteed|best\s+ever|number\s+one|100%\s+guaranteed)\b|#\s*1\b", re.I)
ID = re.compile(r"^[a-zA-Z0-9_@.:-]{1,100}$")
METRICS = frozenset({
    "views", "impressions", "reach", "engaged_views", "likes", "comments",
    "shares", "reposts", "replies", "saves", "clicks", "upvotes", "profile_visits",
    "follows", "watch_time_seconds", "average_view_duration_seconds",
})
BOOL_FIELDS = frozenset({
    "avatar", "banner", "featured", "pinned", "highlights", "trailer", "playlists",
    "technical_articles", "topics", "original_samples", "rules_reviewed",
    "community_intro", "domain_verified", "profile_metadata", "directory_opt_in",
    "opt_in", "welcome_post", "business_details", "maker_story", "product_gallery",
    "claimed_domain", "boards", "portfolio", "project_page", "featured_projects",
    "contribution_guide", "starter_pack", "readme",
})
URL_FIELDS = frozenset({"website", "invite_link"})
TEXT_FIELDS = frozenset({"display_name", "bio", "headline", "cta", "category", "about"})
REVIEW_FLAGS = frozenset({
    "hook_clear", "proof_visible", "original_content", "captioned",
    "alt_text", "relevant_cta", "community_rules_followed", "human_written",
})
VIDEO_FORMATS = frozenset({"short_video", "shorts", "reel", "video", "live", "spotlight", "story"})
IMAGE_FORMATS = frozenset({"image", "carousel", "pin", "infographic"})
REQUIRED_PROFILE = frozenset({"platform", "account_id", "handle", "fields"})
REQUIRED_POST = frozenset({"platform", "account_id", "post_id", "format"})
PROFILE_ALLOWED = REQUIRED_PROFILE | {"profile_kind", "keywords"}
POST_ALLOWED = REQUIRED_POST | {"source", "published_at", "observed_at", "metrics", "review_flags"}
ROOT_ALLOWED = frozenset({"schema_version", "brand_name", "preferred_handle", "profiles", "posts"})
KNOWN_FIELDS = BOOL_FIELDS | URL_FIELDS | TEXT_FIELDS
# Sourced length limits: text fields plus the top-level handle. Every entry carries an official URL,
# a checked date and a verbatim quote; a field without an entry is unknown and is never length-checked.
LIMIT_FIELDS = TEXT_FIELDS | {"handle"}
LIMIT_UNITS = frozenset({"characters", "graphemes"})
LIMIT_SCOPES = frozenset({"official_help", "official_api_reference", "official_api_schema",
                          "official_open_source_code", "instance_default_verify_actual_server"})
LIMIT_KEYS = frozenset({"field", "max", "unit", "scope", "source_url", "checked_on", "quote"})
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Code points that join or modify the previous user-perceived character (approximate grapheme count).
_GRAPHEME_EXTEND = re.compile("[‍︀-️\U0001f3fb-\U0001f3ff\U000e0020-\U000e007f]")


def _reject_sensitive(obj, depth: int = 0) -> None:
    if depth > 6:
        raise ValidationError("Audit input nesting is too deep")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str) or SECRET_KEY.search(key):
                raise ValidationError("Audit input contains a forbidden credential-like field name")
            _reject_sensitive(value, depth + 1)
    elif isinstance(obj, list):
        if len(obj) > 100:
            raise ValidationError("Audit input array too large")
        for value in obj:
            _reject_sensitive(value, depth + 1)
    elif isinstance(obj, str):
        if len(obj) > 4000 or SECRET_VALUE.search(obj):
            raise ValidationError("Audit input may contain sensitive data; remove secrets before proceeding")


def _keys(obj, required: frozenset, allowed: frozenset) -> None:
    if not isinstance(obj, dict) or not required <= obj.keys() or set(obj) - allowed:
        raise ValidationError("Invalid profile/post audit structure or unsupported fields")


def load_registry() -> dict:
    try:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Profile-audit registry unavailable") from exc
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        raise ValidationError("Profile-audit registry schema mismatch")
    if not isinstance(registry.get("platforms"), list):
        raise ValidationError("Profile-audit platform list unavailable")
    seen = set()
    for p in registry["platforms"]:
        if (not isinstance(p, dict) or not isinstance(p.get("id"), str) or p["id"] in seen
                or not isinstance(p.get("profile_checks"), list) or not p["profile_checks"]
                or any(f not in KNOWN_FIELDS for f in p["profile_checks"])):
            raise ValidationError("Invalid or duplicate platform audit rules")
        strategy = p.get("post_strategy")
        if (not isinstance(strategy, dict)
                or strategy.get("action_metric") not in METRICS
                or strategy.get("exposure_metric") not in METRICS):
            raise ValidationError("Invalid platform outcome metric mapping")
        _check_field_limits(p)
        seen.add(p["id"])
    return registry


def _check_field_limits(p: dict) -> None:
    """Refuse a limit without official URL, checked date and a quote naming the number."""
    limits = p.get("profile_field_limits", [])
    if not isinstance(limits, list):
        raise ValidationError("Invalid profile field limits")
    fields = set()
    for entry in limits:
        if (not isinstance(entry, dict) or not LIMIT_KEYS <= entry.keys()
                or set(entry) - LIMIT_KEYS - {"note"}
                or entry["field"] not in LIMIT_FIELDS or entry["field"] in fields
                or (entry["field"] != "handle" and entry["field"] not in p["profile_checks"])
                or not isinstance(entry["max"], int) or isinstance(entry["max"], bool) or entry["max"] < 1
                or entry["unit"] not in LIMIT_UNITS or entry["scope"] not in LIMIT_SCOPES
                or not isinstance(entry["source_url"], str) or not entry["source_url"].startswith("https://")
                or not isinstance(entry["checked_on"], str) or not ISO_DATE.match(entry["checked_on"])
                or not isinstance(entry["quote"], str) or not 0 < len(entry["quote"]) <= 200
                or str(entry["max"]) not in entry["quote"]):
            raise ValidationError(f"Invalid sourced field limit for {p['id']}")
        fields.add(entry["field"])
    bio = next((e for e in limits if e["field"] == "bio"), None)
    if (p.get("profile_bio_limit") != (bio["max"] if bio else None)
            or p.get("profile_bio_limit_scope") != (bio["scope"] if bio else "unverified")):
        raise ValidationError(f"profile_bio_limit for {p['id']} must mirror its sourced bio limit")


def _measure(value: str, unit: str) -> int:
    """Code points, or an approximate grapheme count (combining marks, joiners, variation selectors,
    skin-tone modifiers and tag characters attach to the previous character). Never an exact counter."""
    if unit == "characters":
        return len(value)
    count, joined, flag_half = 0, False, False
    for ch in value:
        if unicodedata.combining(ch) or _GRAPHEME_EXTEND.match(ch):
            joined = joined or ch == "‍"
            continue
        if joined:  # the character after a zero-width joiner belongs to the same emoji sequence
            joined = False
            continue
        if "\U0001f1e6" <= ch <= "\U0001f1ff":  # two regional indicators form one flag
            flag_half = not flag_half
            if not flag_half:
                continue
        else:
            flag_half = False
        count += 1
    return count


def _length_alerts(rule: dict, profile: dict) -> list[dict]:
    alerts = []
    for entry in rule.get("profile_field_limits", []):
        field = entry["field"]
        value = profile.get("handle") if field == "handle" else profile["fields"].get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        if field == "handle":
            value = value.strip().lstrip("@").split("@")[0]
        size = _measure(value, entry["unit"])
        if size > entry["max"]:
            alerts.append({
                "kind": "length_review", "field": field, "length": size, "limit": entry["max"],
                "unit": entry["unit"], "scope": entry["scope"], "checked_on": entry["checked_on"],
                "message": (f"{field} is {size} {entry['unit']}, over the sourced {entry['max']}-{entry['unit']} "
                            f"limit ({entry['scope']}, checked {entry['checked_on']}). Counting rules and server "
                            "settings can differ; verify in the native editor."),
                "source": entry["source_url"]})
    return alerts


def field_limit_status(rule: dict) -> dict:
    """Each text field the platform audits (and the handle): its sourced limit, or ``unknown``."""
    sourced = {e["field"]: e for e in rule.get("profile_field_limits", [])}
    fields = [f for f in rule["profile_checks"] if f in TEXT_FIELDS] + ["handle"]
    return {f: ({k: sourced[f][k] for k in ("max", "unit", "scope", "source_url", "checked_on")}
                if f in sourced else "unknown") for f in fields}


CSV_COLUMNS = ("platform", "account_id", "handle", "profile_kind", "keywords", "field", "value")
CSV_OPTIONAL = frozenset({"brand_name", "preferred_handle"})
MAX_CSV_ROWS = 2000


def _csv_value(field: str, raw: str, row: int):
    """One CSV cell as a snapshot value: blank is unknown (None), never false or an empty string."""
    text = raw.strip()
    if not text:
        return None
    if field in BOOL_FIELDS:
        if text.casefold() not in ("true", "false"):
            raise ValidationError(f"Profile CSV row {row}: {field} must be true, false or blank")
        return text.casefold() == "true"
    return text


def snapshot_from_csv(text: str) -> dict:
    """A long-format profile CSV (one row per field) as the JSON snapshot ``load_snapshot`` validates.

    Columns ``platform,account_id,handle,profile_kind,keywords,field,value`` plus optional constant
    ``brand_name`` and ``preferred_handle``. Rows of one ``platform``+``account_id`` build one profile;
    ``handle``, ``profile_kind`` and ``keywords`` (``;`` separated) may be repeated or left blank on later
    rows but never contradict the first. A blank ``field`` declares a profile with no fields. A blank
    ``value`` is unknown. Posts are not read from CSV (use JSON, and ``--metrics-csv`` for metrics).
    Errors name a row number, never the row's content.
    """
    if "\x00" in text:
        raise ValidationError("Profile CSV must be plain UTF-8 text")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    names = reader.fieldnames or []
    if (len(set(names)) != len(names) or any(c not in names for c in CSV_COLUMNS)
            or set(names) - set(CSV_COLUMNS) - CSV_OPTIONAL):
        raise ValidationError("Profile CSV header must be platform,account_id,handle,profile_kind,keywords,field,value "
                              "(optional brand_name, preferred_handle)")
    profiles: dict[tuple[str, str], dict] = {}
    constants: dict[str, str] = {}
    seen_fields: set[tuple[str, str, str]] = set()
    for number, row in enumerate(reader, start=2):
        if number - 1 > MAX_CSV_ROWS:
            raise ValidationError(f"Profile CSV has more than {MAX_CSV_ROWS} rows")
        if None in row or any(v is None for v in row.values()):
            raise ValidationError(f"Profile CSV row {number} has the wrong number of columns")
        platform, account = row["platform"].strip(), row["account_id"].strip()
        if not platform or not account:
            raise ValidationError(f"Profile CSV row {number} needs platform and account_id")
        for name in CSV_OPTIONAL & set(names):
            value = row[name].strip()
            if value and constants.setdefault(name, value) != value:
                raise ValidationError(f"Profile CSV row {number}: {name} must be the same on every row")
        entry = profiles.setdefault((platform, account), {
            "platform": platform, "account_id": account, "fields": {}})
        for column in ("handle", "profile_kind", "keywords"):
            value = row[column].strip()
            if not value:
                continue
            parsed = [k.strip() for k in value.split(";") if k.strip()] if column == "keywords" else value
            key = column
            if key in entry and entry[key] != parsed:
                raise ValidationError(f"Profile CSV row {number}: {column} contradicts an earlier row of the same profile")
            entry[key] = parsed
        field = row["field"].strip()
        if field:
            if (platform, account, field) in seen_fields:
                raise ValidationError(f"Profile CSV row {number}: duplicate field for the same profile")
            seen_fields.add((platform, account, field))
            if field not in KNOWN_FIELDS:
                raise ValidationError(f"Profile CSV row {number}: unknown profile field")
            entry["fields"][field] = _csv_value(field, row["value"], number)
        elif row["value"].strip():
            raise ValidationError(f"Profile CSV row {number}: a value needs a field name")
    if not profiles:
        raise ValidationError("Profile CSV has no rows")
    for entry in profiles.values():
        entry.setdefault("handle", "")  # load_snapshot then refuses a profile that never declared one
    snapshot: dict = {"schema_version": SCHEMA, "profiles": list(profiles.values())}
    snapshot.update(constants)
    return snapshot


def load_snapshot(path: Path) -> dict:
    """The owner's snapshot from a ``.json`` file, or a profile ``.csv`` (see ``snapshot_from_csv``)."""
    try:
        if path.stat().st_size > 2_000_000:
            raise ValidationError("Audit input must be <=2 MB; keep sensitive exports local")
        raw = path.read_text(encoding="utf-8")
        data = snapshot_from_csv(raw) if path.suffix.casefold() == ".csv" else json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ValidationError("Could not read the audit snapshot as JSON or profile CSV (no network requests were made)") from exc
    _reject_sensitive(data)
    _keys(data, frozenset({"schema_version", "profiles"}), ROOT_ALLOWED)
    if data["schema_version"] != SCHEMA:
        raise ValidationError("Unsupported audit snapshot schema_version")
    profiles = data["profiles"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 50:
        raise ValidationError("Profiles must contain 1..50 owner-supplied records")
    if "posts" in data and (not isinstance(data["posts"], list) or len(data["posts"]) > 100):
        raise ValidationError("At most 100 post observations are supported")
    if "brand_name" in data and (not isinstance(data["brand_name"], str) or len(data["brand_name"]) > 100):
        raise ValidationError("brand_name must be a short string")
    if "preferred_handle" in data and (not isinstance(data["preferred_handle"], str) or not ID.fullmatch(data["preferred_handle"])):
        raise ValidationError("preferred_handle must be one short string; no credentials")
    registry = {p["id"]: p for p in load_registry()["platforms"]}
    identities = set()
    for p in profiles:
        _keys(p, REQUIRED_PROFILE, PROFILE_ALLOWED)
        if p["platform"] not in registry:
            raise ValidationError("Unsupported audit platform")
        if not isinstance(p["account_id"], str) or not ID.fullmatch(p["account_id"]):
            raise ValidationError("Invalid account_id")
        if not isinstance(p["handle"], str) or not 1 <= len(p["handle"]) <= 100:
            raise ValidationError("Invalid handle")
        key = (p["platform"], p["account_id"])
        if key in identities:
            raise ValidationError("Duplicate audit profile identity")
        identities.add(key)
        if p.get("profile_kind", "personal") not in ("personal", "organization", "community", "maker"):
            raise ValidationError("Unrecognized profile_kind")
        fields = p["fields"]
        if not isinstance(fields, dict) or set(fields) - KNOWN_FIELDS:
            raise ValidationError("Unknown or invalid profile fields")
        for field, value in fields.items():
            if value is None:
                continue
            if field in BOOL_FIELDS and type(value) is not bool:
                raise ValidationError("Profile boolean check must be true, false or null")
            if field in TEXT_FIELDS | URL_FIELDS and not isinstance(value, str):
                raise ValidationError("Profile text/link fields must be strings or null")
        keywords = p.get("keywords", [])
        if (not isinstance(keywords, list) or len(keywords) > 8
                or any(not isinstance(k, str) or not 1 <= len(k) <= 80 for k in keywords)):
            raise ValidationError("keywords must be <=8 nonempty phrases")
    post_keys: set[tuple[str, str, str]] = set()
    for post in data.get("posts", []):
        _keys(post, REQUIRED_POST, POST_ALLOWED)
        if (post["platform"], post["account_id"]) not in identities:
            raise ValidationError("Post must refer to a profile in this snapshot")
        if not isinstance(post["post_id"], str) or not ID.fullmatch(post["post_id"]):
            raise ValidationError("Invalid post_id")
        if not isinstance(post["format"], str) or not ID.fullmatch(post["format"]):
            raise ValidationError("Invalid post format")
        identity = (post["platform"], post["account_id"], post["post_id"])
        if identity in post_keys:
            raise ValidationError("Duplicate post observation; use one matched-age snapshot per unique post")
        post_keys.add(identity)
        for label in ("source", "published_at", "observed_at"):
            if label in post and post[label] is not None and not isinstance(post[label], str):
                raise ValidationError("Post source/timestamps must be strings or null")
        flags = post.get("review_flags", {})
        if (not isinstance(flags, dict) or set(flags) - REVIEW_FLAGS
                or any(type(v) is not bool and v is not None for v in flags.values())):
            raise ValidationError("Invalid manual creative-review flags")
        metric_values = post.get("metrics", {})
        if (not isinstance(metric_values, dict) or set(metric_values) - METRICS):
            raise ValidationError("Unknown metrics (never confuse different platform definitions)")
        for v in metric_values.values():
            if v is not None and (type(v) not in (float, int) or v < 0 or not math.isfinite(v) or v > 1e12):
                raise ValidationError("Metrics must be finite non-negative numeric observations or null")
    return data


def _url_state(value: str) -> tuple[bool, str]:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False, "Use an owner-verified HTTPS URL without embedded credentials"
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.lower().startswith("utm_") or key.lower() in ("gclid", "fbclid") for key, _ in params):
        return False, "Profile website link should be a clean, canonical destination, not a campaign tracking link"
    return True, "Canonical profile URL provided (HTTP reachability NOT verified)"


STOPWORDS = frozenset({
    "about", "after", "also", "and", "are", "for", "from", "have", "into", "more", "that", "the", "their", "them",
    "then", "this", "with", "your", "you", "our", "its", "who", "how", "what", "when", "where", "which", "will"})
WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


def _terms(fields: dict) -> set[str]:
    text = " ".join(str(fields.get(k) or "") for k in ("bio", "headline", "about"))
    return {w for w in (m.casefold() for m in WORD.findall(text)) if w not in STOPWORDS}


def _host(value) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").casefold()
    return host.removeprefix("www.") if parts.scheme == "https" and host else None


def cross_platform_consistency(profiles: list[dict]) -> dict:
    """Advisory name, handle, link and bio-wording comparison across the snapshot's profiles of one kind.

    Heuristic, never scored: handles and bios legitimately differ by platform, so a finding asks the owner
    to confirm intent. Fields the owner left unknown are skipped and reported, never guessed.
    """
    findings: list[dict] = []
    not_compared: list[dict] = []
    groups: dict[str, list[dict]] = defaultdict(list)
    for profile in profiles:
        groups[profile.get("profile_kind", "personal")].append(profile)
    compared = 0
    for kind, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        compared += len(members)
        label = lambda m: f"{m['platform']}:{m['account_id']}"  # noqa: E731
        names: dict[str, list[str]] = defaultdict(list)
        stems: dict[str, list[str]] = defaultdict(list)
        hosts: dict[str, list[str]] = defaultdict(list)
        for m in members:
            name = m["fields"].get("display_name")
            if isinstance(name, str) and name.strip():
                names[" ".join(name.casefold().split())].append(label(m))
            else:
                not_compared.append({"profile_kind": kind, "profile": label(m), "field": "display_name"})
            stems[m["handle"].casefold().lstrip("@").split("@")[0]].append(label(m))
            host = _host(m["fields"].get("website"))
            if host:
                hosts[host].append(label(m))
            else:
                not_compared.append({"profile_kind": kind, "profile": label(m), "field": "website"})
        for field, buckets, message in (
                ("display_name", names, "Display names differ across platforms; confirm one public identity is intended."),
                ("handle", stems, "Handles differ across platforms; this is often unavoidable, so confirm it is intentional."),
                ("website_host", hosts, "Profile links point at different sites; confirm each is the right destination.")):
            if len(buckets) > 1:
                findings.append({"kind": f"{field}_differs", "profile_kind": kind, "message": message,
                                 "groups": [{"value": value, "profiles": ids} for value, ids in sorted(buckets.items())]})
        terms = {label(m): _terms(m["fields"]) for m in members}
        measured = {k: v for k, v in terms.items() if v}
        for m in members:
            if label(m) not in measured:
                not_compared.append({"profile_kind": kind, "profile": label(m), "field": "bio"})
        ids = sorted(measured)
        pairs = [[a, b] for i, a in enumerate(ids) for b in ids[i + 1:] if not measured[a] & measured[b]]
        if pairs:
            findings.append({"kind": "bio_wording_unshared", "profile_kind": kind,
                             "message": "These bios, headlines or about texts share no significant word; confirm they describe the same work.",
                             "profile_pairs": pairs})
    return {"basis": "owner-supplied snapshot text; heuristic and advisory, never part of a score",
            "profiles_compared": compared, "findings": findings,
            "not_compared": not_compared,
            "scope": "all profiles in the snapshot, grouped by profile_kind, regardless of --platform"}



def audit_profiles(snapshot: dict, *, platform: str | None = None) -> dict:
    registry = load_registry()
    mapping = {p["id"]: p for p in registry["platforms"]}
    if platform and platform not in mapping:
        raise ValidationError("Unknown audit platform")
    profiles = [p for p in snapshot["profiles"] if platform is None or p["platform"] == platform]
    if not profiles:
        raise ValidationError("No matching owner-supplied profiles")
    reports = []
    for p in profiles:
        rule = mapping[p["platform"]]
        observed = passes = 0
        checklist = []
        fixes = []
        fields = p["fields"]
        for field in rule["profile_checks"]:
            value = fields.get(field)
            if value is None:
                status, reason = "unknown", "Not provided; cannot assume present or absent"
            else:
                observed += 1
                if field in BOOL_FIELDS:
                    good = value is True
                    reason = "Owner reported present" if good else "Owner reported missing"
                elif field in URL_FIELDS:
                    good, reason = _url_state(value)
                else:
                    good = bool(value.strip())
                    reason = "Owner-provided text is nonempty" if good else "Owner reported empty text"
                status = "present" if good else "needs_fix"
                if good:
                    passes += 1
                else:
                    fixes.append({"priority": "P1" if field in ("bio","headline","website","avatar","display_name") else "P2",
                                  "field":field,"action": registry["field_labels"][field][0],
                                  "reason": reason})
            checklist.append({"field": field, "description": registry["field_labels"][field][0],
                              "status": status, "evidence": "owner_supplied_snapshot" if status != "unknown" else "not_observed"})
        alerts = _length_alerts(rule, p)
        bio = fields.get("bio")
        if isinstance(bio, str) and bio:
            if VAGUE_CLAIM.search(bio):
                alerts.append({"kind":"claim_review","message":"Bio may contain an unsupported superlative/guarantee; verify proof manually before publication."})
        keywords = p.get("keywords", [])
        if keywords and any(isinstance(fields.get(key), str) for key in ("bio", "headline", "about")):
            text_fields = " ".join(str(fields.get(key) or "") for key in ("bio","headline","about")).casefold()
            if not any(k.casefold() in text_fields for k in keywords):
                alerts.append({"kind":"keyword_alignment","message":"Declared topic phrases were not found in about/headline/bio; consider one natural, accurate phrase, not keyword stuffing."})
        preferred = snapshot.get("preferred_handle")
        if preferred and p.get("profile_kind") in ("organization","community","maker"):
            # Handles differ legitimately across services; advisory, never a score penalty.
            actual_stem = p["handle"].casefold().lstrip("@").split("@")[0].split(".")[0]
            expected = preferred.casefold().lstrip("@")
            if actual_stem != expected:
                alerts.append({"kind":"handle_consistency",
                               "message":"Handle differs from the owner-declared preferred handle. Confirm this is intentional for the platform."})
        brand = snapshot.get("brand_name")
        name = fields.get("display_name")
        if (brand and p.get("profile_kind") in ("organization", "community")
                and isinstance(name, str) and name.strip() and brand.casefold() not in name.casefold()):
            alerts.append({"kind":"brand_consistency","message":"Organization/community display name differs from declared brand. Confirm intentional naming."})
        if rule["human_only"]:
            alerts.append({"kind":"human_only","message":"Community participation and submissions require human policy review; this command never writes posts/replies."})
        total = len(rule["profile_checks"])
        coverage = round(observed / total, 3)
        reports.append({
            "platform":p["platform"],"account_id":p["account_id"],
            "profile_method":rule["label"],
            "readiness_heuristic_score": round(100 * passes / observed) if observed and coverage >= 0.6 else None,
            "score_meaning":"Percentage of observed checklist fields present; NOT algorithm rank, projected reach or account quality.",
            "coverage":coverage,"observed_checks":observed,"total_checks":total,
            "checklist":checklist,"fixes":sorted(fixes,key=lambda x:x["priority"]),
            "alerts":alerts,"post_hypothesis":rule["post_strategy"]["test_hypothesis"],
            "supported_formats_hypotheses":rule["post_strategy"]["formats"],
            "source_tier":rule["evidence"]["tier"],
            "official_source":rule["evidence"].get("official_source"),
            "field_limits": field_limit_status(rule),
            "status":"observed_only" if coverage>=0.6 else "insufficient_profile_data",
        })
    return {"schema_version":SCHEMA,"kind":"profile_audit","status":"offline_descriptive_not_a_platform_rank",
            "platforms_covered":len(reports),"reports":reports,
            "cross_platform_consistency": cross_platform_consistency(snapshot["profiles"]),
            "note":"No profile was fetched and no edits or publishing occurred. Fields are owner assertions, NOT independent verification."}


def _at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z","+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def audit_posts(snapshot: dict, *, platform: str | None = None, age_hours: int = 168,
                tolerance_hours: int = 24, min_posts: int = 3) -> dict:
    if type(age_hours) is not int or not 24 <= age_hours <= 720:
        raise ValidationError("age_hours must be 24..720")
    if type(tolerance_hours) is not int or not 1 <= tolerance_hours < age_hours or tolerance_hours > 168:
        raise ValidationError("tolerance_hours must be 1..168 and less than age_hours")
    if type(min_posts) is not int or not 3 <= min_posts <= 30:
        raise ValidationError("min_posts must be 3..30")
    registry = {p["id"]:p for p in load_registry()["platforms"]}
    if platform and platform not in registry:
        raise ValidationError("Unknown audit platform")
    posts = [p for p in snapshot.get("posts", []) if platform is None or p["platform"] == platform]
    reports = []
    grouped = defaultdict(list)
    exclusions: defaultdict[str, int] = defaultdict(int)
    for p in posts:
        rules = registry[p["platform"]]
        suggestions = []
        flags = p.get("review_flags", {})
        must_check = ["hook_clear","proof_visible","original_content","relevant_cta"]
        if p["format"] in VIDEO_FORMATS:
            must_check.append("captioned")
        if p["format"] in IMAGE_FORMATS:
            must_check.append("alt_text")
        if rules["human_only"]:
            must_check.append("community_rules_followed")
        if p["platform"] == "hackernews":
            must_check.append("human_written")
        for flag in must_check:
            if flags.get(flag) is False:
                suggestions.append({"signal":flag,"action":f"Review and improve {flag.replace('_',' ')} before the next human-authorized post"})
        if p["format"] not in rules["post_strategy"]["formats"]:
            suggestions.append({"signal":"format_hypothesis","action":"Format is outside this network's currently documented examples; verify account/platform suitability before treating it as a bad format."})
        numerator = rules["post_strategy"]["action_metric"]
        denominator = rules["post_strategy"]["exposure_metric"]
        m = p.get("metrics", {})
        published = _at(p.get("published_at"))
        observed = _at(p.get("observed_at"))
        rate = None
        reason = None
        if not published or not observed or observed < published:
            reason = "missing_or_invalid_post_age"
        elif not p.get("source"):
            reason = "missing_source_label"
        elif abs((observed - published).total_seconds()/3600 - age_hours) > tolerance_hours:
            reason = "unmatched_post_age"
        elif m.get(numerator) is None or m.get(denominator) is None or m[denominator] <= 0:
            reason = "missing_comparable_native_metrics"
        else:
            rate = m[numerator] / m[denominator]
            grouped[(p["platform"],p["account_id"],p["format"],p["source"])].append(rate)
            if rate > 1:
                suggestions.append({"signal":"metric_definition_review","action":"Action count exceeds exposure count; confirm metric definitions and reporting period."})
        if reason:
            exclusions[reason] += 1
        reports.append({"platform":p["platform"],"account_id":p["account_id"],"post_id":p["post_id"],
                        "format":p["format"],"source":p.get("source"),"age_match":reason is None,
                        "metric":f"{numerator}/{denominator}", "descriptive_rate":round(rate,6) if rate is not None else None,
                        "missing_reason":reason,"creative_review":{f: ("unknown" if flags.get(f) is None else ("observed_ok" if flags[f] else "fix")) for f in must_check},
                        "suggestions":suggestions})
    groups = []
    for (platform_id, account_id, fmt, source), values in sorted(grouped.items()):
        groups.append({"platform":platform_id,"account_id":account_id,"format":fmt,"source":source,
                       "sample_size":len(values),"median_action_per_exposure":round(median(values),6) if len(values)>=min_posts else None,
                       "status":"exploratory_descriptive_only" if len(values)>=min_posts else "insufficient_data"})
    return {"schema_version":SCHEMA,"kind":"post_audit","status":"observational_only_no_platform_predictions",
            "age_hours":age_hours,"tolerance_hours":tolerance_hours,
            "post_count":len(reports),"post_reports":reports,"format_cohorts":groups,
            "excluded":dict(sorted(exclusions.items())),
            "warnings":["No posts or metrics are fetched; all entries are owner-supplied.",
                        "Views/impressions/reach are different and may be unavailable; compare only the same platform, account, format, native source and post age.",
                        "Creative-review flags are owner declarations and not machine-verified quality.",
                        "Format medians are observational; no randomization, statistical significance, guaranteed growth or optimization of hidden ranking weights.",
                        "Hacker News submissions must be independently human-written and manually submitted, never generated or auto-posted."]}


def render_summary(result: dict) -> str:
    if result["kind"] == "profile_audit":
        lines = ["OFFLINE profile audit — observed checklist completeness, NOT algorithm rankings"]
        for item in result["reports"]:
            score = "insufficient data" if item["readiness_heuristic_score"] is None else str(item["readiness_heuristic_score"])+"/100"
            lines.append(f"{item['platform']} [{item['account_id']}]: {score} | coverage {item['coverage']:.0%} | fixes {len(item['fixes'])}")
            for fix in item["fixes"][:6]:
                lines.append(f"  {fix['priority']}: {fix['field']} — {fix['action']}")
            for alert in item["alerts"]:
                if alert["kind"] == "length_review":
                    lines.append(f"  Length: {alert['message']}")
            lines.append(f"  Next hypothesis: {item['post_hypothesis']}")
        cross = result.get("cross_platform_consistency")
        if cross and cross["findings"]:
            lines.append(f"Cross-platform consistency (advisory, {cross['profiles_compared']} profiles compared):")
            for finding in cross["findings"]:
                lines.append(f"  {finding['kind']} [{finding['profile_kind']}]: {finding['message']}")
    else:
        lines = [f"OFFLINE post audit — {result['post_count']} supplied posts, matched age {result['age_hours']}h"]
        for item in result["format_cohorts"]:
            value = item["median_action_per_exposure"]
            lines.append(f"{item['platform']} {item['account_id']} {item['format']}: {item['sample_size']} matched; median {value if value is not None else 'unknown'} ({item['status']})")
        if not result["format_cohorts"]:
            lines.append("No comparable native metric cohorts. Upload better source-labeled data; missing is not zero.")
        lines.append("No external accounts read, changed, followed or published.")
    return "\n".join(lines)
