"""Offline, evidence-labelled discovery and hashtag planning.

No network, cookies, social accounts, hidden algorithms, publishing, scheduling or
claims of predicted impressions. This is an editorial ranking of *relevance* only.
A dated owner observation of a trend is NOT a first-party API verification.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .campaigns import _sensitive_keys, public_url
from .distribution import load_registry as load_distribution
from .policy import ValidationError

REGISTRY = Path(__file__).with_name("discovery_registry.json")
STATUS = "offline_discovery_proposal_not_published"
MAX_INPUT_BYTES = 250_000
MAX_TEXT = 12000
MAX_CANDIDATES = 60
MAX_KEYWORDS = 12
TREND_MAX_AGE_DAYS = 14
TAG_RE = re.compile(r"^#?[\w]{2,50}$", re.UNICODE)
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")
REGION_RE = re.compile(r"^(?:global|[A-Z]{2})$")
ALLOWED_ROOT = frozenset({
    "schema_version", "platform", "text", "audience", "region", "language",
    "keywords", "candidates", "as_of", "max_suggestions",
})
ALLOWED_CANDIDATE = frozenset({
    "tag", "kind", "source_type", "source_url", "observed_on",
    "region", "evidence_note",
})
KINDS = frozenset({"core", "niche", "broad", "trend"})
OFFICIAL_TREND_HOSTS = {"tiktok": frozenset({"ads.tiktok.com"})}
SOURCE_TYPES = frozenset({"editorial", "owner_observation", "official_tool_recorded"})


def load_discovery_registry(path: Path | None = None) -> dict:
    """Return independently authored, dated signal-family guidance."""
    source = path or REGISTRY
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Discovery guidance registry cannot be read") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("platforms"), dict):
        raise ValidationError("Invalid discovery guidance registry")
    for key, value in data["platforms"].items():
        if (not isinstance(value, dict) or not 0 <= value.get("editorial_tag_budget", -1) <= 6
                or value.get("surface") not in ("hashtags", "topic_label", "keywords")
                or not isinstance(value.get("sources"), list) or not value["sources"]
                or any(not public_url(src.get("url")) or not isinstance(src.get("checked_on"), str)
                       for src in value["sources"])):
            raise ValidationError(f"Invalid discovery guidance for {key}")
    return data


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return "".join(c if c.isalnum() else " " for c in folded).strip()


def _compact(value: str) -> str:
    return "".join(c for c in _normalize(value) if c.isalnum())


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"{field} must be a YYYY-MM-DD date")
    return parsed


def _string(value: object, name: str, max_len: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= max_len:
        raise ValidationError(f"{name} must be a nonempty string (maximum {max_len} characters)")
    return value.strip()


def _local_tag(term: str) -> str | None:
    """Construct only a literal tag derived from an explicit supplied topic phrase."""
    tokens = re.findall(r"\w+", unicodedata.normalize("NFKC", term), re.UNICODE)
    name = "".join(token[:1].upper() + token[1:] for token in tokens)
    return "#" + name if TAG_RE.fullmatch("#" + name) else None


def _owner_candidate(raw: object, n: int, surface: str) -> dict:
    if not isinstance(raw, dict) or set(raw) - ALLOWED_CANDIDATE:
        raise ValidationError(f"candidate {n}: unsupported or missing tag data")
    tag = _string(raw.get("tag"), f"candidate {n} tag", 60)
    if surface == "topic_label":
        # Threads exposes a topic label, not a bundle of space-free hashtags.
        tag = tag.lstrip("#").replace("_", " ")
        if not re.fullmatch(r"[\w ]{2,50}", tag, re.UNICODE):
            raise ValidationError(f"candidate {n}: invalid topic label")
    elif not TAG_RE.fullmatch(tag):
        raise ValidationError(f"candidate {n}: invalid hashtag; one # and letters/digits only")
    else:
        tag = "#" + tag.lstrip("#")
    kind = raw.get("kind", "niche")
    if kind not in KINDS:
        raise ValidationError(f"candidate {n}: kind must be core/niche/broad/trend")
    source_type = raw.get("source_type", "editorial")
    if source_type not in SOURCE_TYPES:
        raise ValidationError(f"candidate {n}: unknown source type")
    url = raw.get("source_url")
    if url is not None and not public_url(url):
        raise ValidationError(f"candidate {n}: source_url must be a public HTTPS URL without secrets")
    observed = raw.get("observed_on")
    if observed is not None:
        _date(observed, f"candidate {n} observed_on")
    region = raw.get("region", "global")
    if not isinstance(region, str) or not REGION_RE.fullmatch(region):
        raise ValidationError(f"candidate {n}: region must be global or an ISO country code")
    note = raw.get("evidence_note")
    if note is not None:
        _string(note, f"candidate {n} evidence_note", 240)
    return {"tag": tag, "kind": kind, "source_type": source_type,
            "source_url": url, "observed_on": observed, "region": region,
            "evidence_note": note or ""}


def _evidence_issue(candidate: dict, platform: str, region: str, today: date) -> str | None:
    if candidate["kind"] != "trend":
        return None
    if not candidate["source_url"] or not candidate["observed_on"] or not candidate["evidence_note"]:
        return "trend_requires_dated_source_and_observation"
    checked = _date(candidate["observed_on"], "observed_on")
    days = (today - checked).days
    if days < 0:
        return "trend_observation_in_future"
    if days > TREND_MAX_AGE_DAYS:
        return "trend_evidence_expired"
    if region != "global" and candidate["region"] != region:
        return "trend_region_mismatch"
    if candidate["source_type"] == "editorial":
        return "trend_requires_owner_or_official_source"
    if candidate["source_type"] == "official_tool_recorded":
        host = (urlsplit(candidate["source_url"]).hostname or "").lower()
        if host not in OFFICIAL_TREND_HOSTS.get(platform, frozenset()):
            return "official_trend_source_not_recognized"
    return None


def _is_about_tag(tag: str, content: str) -> bool:
    """Strict lexical gate: trending popularity cannot override off-topic labels.

    This deliberately refuses unproven semantic synonyms. Claude/human can rewrite
    the actual content to make a *truthful* topic explicit, then run the check again.
    """
    compact = _compact(tag)
    body = _compact(content)
    if not compact or len(compact) < 2:
        return False
    # Avoid #AI matching the middle of 'said' / similar: short tags need word boundaries.
    if len(compact) <= 3:
        words = _normalize(content).split()
        return compact in words
    return compact in body


def _suggestion(candidate: dict, *, generated: bool, in_text: bool) -> dict:
    # Editorial points for ordering, NEVER platform ranking weights or reach prediction.
    kind = candidate["kind"]
    score = {"core": 40, "niche": 35, "broad": 18, "trend": 15}[kind]
    score += 8 if in_text else 3
    if kind == "trend":
        score += 2
    return {
        "tag": candidate["tag"],
        "category": kind,
        "editorial_relevance_score": score,
        "basis": "keyword_derived" if generated else "owner_supplied_candidate",
        "trend_status": ("owner_reported_unverified" if kind == "trend" else "not_a_trend_claim"),
        "evidence_url": candidate["source_url"],
        "observed_on": candidate["observed_on"],
        "region": candidate["region"],
        "reason": ("Present in the post's actual text" if in_text
                   else "Anchored to the author's explicitly supplied keywords"),
    }


def build_discovery_plan(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - ALLOWED_ROOT or raw.get("schema_version") != 1:
        raise ValidationError("Discovery input must have schema_version=1 and only documented fields")
    if _sensitive_keys(raw):
        raise ValidationError("Discovery input contains secret-like fields; use public editorial text only")
    platform = _string(raw.get("platform"), "platform", 80).lower()
    distribution = {entry["id"]: entry for entry in load_distribution()["platforms"]}
    if platform not in distribution:
        raise ValidationError("Unknown distribution platform")
    target = distribution[platform]
    if target["human_only"] or target["reachweaver_delivery"] == "not_applicable":
        raise ValidationError("Human-only or non-publishing destination: no generated tag plan")
    text = _string(raw.get("text"), "text", MAX_TEXT)
    audience = _string(raw.get("audience"), "audience", 200)
    region = raw.get("region", "global")
    if not isinstance(region, str) or not REGION_RE.fullmatch(region):
        raise ValidationError("region must be global or uppercase 2-letter ISO country")
    lang = raw.get("language", "en")
    if not isinstance(lang, str) or not LANG_RE.fullmatch(lang):
        raise ValidationError("language must be a BCP-47-like language tag")
    keywords = raw.get("keywords")
    if (not isinstance(keywords, list) or not 1 <= len(keywords) <= MAX_KEYWORDS
            or any(not isinstance(k, str) or not 2 <= len(k.strip()) <= 80 for k in keywords)):
        raise ValidationError("Supply 1..12 verified topic keyword phrases (2..80 characters)")
    candidates = raw.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise ValidationError("candidates must be a list of at most 60 proposed tags")
    now = _date(raw["as_of"], "as_of") if "as_of" in raw else date.today()
    guide = load_discovery_registry()
    specific = guide["platforms"].get(platform)
    policy = specific or guide["default"]
    surface = policy["surface"]
    budget = policy["editorial_tag_budget"]
    maximum = raw.get("max_suggestions", budget)
    if type(maximum) is not int or not 0 <= maximum <= 6:
        raise ValidationError("max_suggestions must be an integer 0..6")
    maximum = min(maximum, budget)
    warnings: list[str] = []
    if not specific:
        warnings.append("No reviewed platform-specific ranking guidance exists; conservative editorial defaults only.")
    if surface == "keywords":
        warnings.append("Title, description, thumbnail and topic relevance outrank hashtag additions here; no hashtags proposed.")
    for keyword in keywords:
        if not _is_about_tag(keyword, text):
            warnings.append(f"Topic keyword '{keyword}' is not present in the submitted text; use it only if genuinely accurate.")
    source_content = text
    seen: set[str] = set()
    valid: list[dict] = []
    skipped: list[dict] = []
    parsed = [_owner_candidate(candidate, index + 1, surface) for index, candidate in enumerate(candidates)]
    if surface == "hashtags" and maximum:
        # Not trending: these are obvious lexical topic labels, not current popularity claims.
        for keyword in keywords:
            label = _local_tag(keyword)
            if label:
                parsed.append({"tag": label, "kind": "core", "source_type": "editorial",
                               "source_url": None, "observed_on": None, "region": "global",
                               "evidence_note": "", "_generated": True})
    for candidate in parsed:
        name = candidate["tag"]
        key = _compact(name)
        if key in seen:
            continue
        if surface == "keywords":
            skipped.append({"tag": name, "reason": "keyword_surface_no_hashtag_plan"})
            continue
        if not _is_about_tag(name, source_content):
            skipped.append({"tag": name, "reason": "tag_not_lexically_related_to_post_or_keywords"})
            continue
        issue = _evidence_issue(candidate, platform, region, now)
        if issue:
            skipped.append({"tag": name, "reason": issue})
            continue
        generated = bool(candidate.get("_generated"))
        seen.add(key)
        valid.append(_suggestion(candidate, generated=generated,
                                 in_text=_is_about_tag(name, text)))
    # Diversity quotas: reserve limited room for relevant broad and dated trends.
    # They never displace the last core tag and cannot bypass the relevance gate.
    selected: list[dict] = []
    has_broad = any(v["category"] == "broad" for v in valid)
    has_trend = any(v["category"] == "trend" for v in valid)
    reserve = min(max(0, maximum - 1), int(has_broad) + int(has_trend))
    for group, cap in ((("core", "niche"), maximum - reserve), (("broad",), 1), (("trend",), 1)):
        subset = sorted((v for v in valid if v["category"] in group),
                        key=lambda v: (-v["editorial_relevance_score"], v["tag"].casefold()))
        for value in subset[:min(cap, maximum - len(selected))]:
            selected.append(value)
    # If broad/trend slots were unused, fill remaining space with honest niche tags.
    if len(selected) < maximum:
        remaining = sorted((v for v in valid if v not in selected and v["category"] in ("core", "niche")),
                           key=lambda v: (-v["editorial_relevance_score"], v["tag"].casefold()))
        selected.extend(remaining[:maximum - len(selected)])
    excluded = {v["tag"] for v in selected}
    skipped.extend({"tag": v["tag"], "reason": "editorial_budget_or_category_quota"}
                   for v in valid if v["tag"] not in excluded)
    return {
        "schema_version": 1, "status": STATUS, "platform": platform, "audience": audience,
        "region": region, "language": lang, "as_of": now.isoformat(),
        "surface": surface, "editorial_tag_budget": budget,
        "recommended": selected, "skipped": skipped, "warnings": warnings,
        "search_keywords": list(dict.fromkeys(keywords)),
        "platform_guidance": {
            "description": policy["description"], "creative_checks": policy["creative_checks"],
            "measure": policy["measure"], "sources": policy["sources"],
            "evidence_level": specific.get("evidence_level", "source_linked_editorial") if specific else "editorial_only",
        },
        "experiment": {
            "hypothesis": "A platform-native, audience-relevant creative variant may outperform the current baseline; test before inferring improvement.",
            "primary_metric": policy["measure"][0],
            "window_hours": 168,
            "baseline_observed": False,
            "method": "Compare similar formats/topics at matched ages; pre-register hook or metadata changes separately.",
            "claim": "not_a_reach_forecast_or_causal_estimate",
        },
        "safety": {
            "network": False, "credentials": False, "publishes": False, "schedules": False,
            "predicted_views": None, "algorithm_weights_known": False,
            "popularity_verified": False,
            "note": ("Scores rank lexical topical relevance for editing, not a social algorithm. "
                     "Official tool URLs and owner-observed trends need human verification; "
                     "unrelated or expired trends are excluded. No guarantee of impressions."),
        },
    }


def check_file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValidationError("discovery-plan input must be a regular JSON file <=250 KB; no symlinks")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("discovery-plan file must contain valid JSON") from exc
    return build_discovery_plan(obj)


def render_text(plan: dict) -> str:
    lines = [f"{plan['status']}: {plan['platform']} ({plan['surface']})",
             f"Audience: {plan['audience']}; region {plan['region']}; as of {plan['as_of']}",
             f"Editorial tags: {len(plan['recommended'])} (not a reach prediction)"]
    for s in plan["recommended"]:
        lines.append(f"  {s['tag']} [{s['category']}]: {s['reason']} ({s['basis']})")
    for skip in plan["skipped"]:
        lines.append(f"  SKIP {skip['tag']}: {skip['reason']}")
    for warn in plan["warnings"]:
        lines.append(f"  NOTE {warn}")
    lines.append("Creative checklist (platform-dependent):")
    lines.extend("  - " + s for s in plan["platform_guidance"]["creative_checks"])
    lines.append("No accounts, predictions, schedules or posts created. Review claims, trends and eligibility yourself.")
    return "\n".join(lines)
