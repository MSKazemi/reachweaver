"""Read-only, evidence-labelled worldwide channel planner.

This module never accesses social networks, credentials, a database or an outbox.
It proposes audience-specific channels and *hypothetical* local-time experiment
windows. It cannot establish recommendation algorithms or confirm account access.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GOALS = ("contributors", "adoption", "awareness", "collaboration")
SCHEDULE_MODES = frozenset({
    "native", "native_conditional", "third_party", "api_or_client",
    "moderator_only", "launch_only", "unverified", "not_applicable", "prohibited",
})
DELIVERY_MODES = frozenset({"manual_only", "mock_tested_text", "not_applicable"})
SOURCE_REQUIRED = frozenset({
    "native", "native_conditional", "api_or_client",
    "moderator_only", "launch_only", "prohibited",
})
REGISTRY_FILE = Path(__file__).with_name("distribution_registry.json")


def load_registry(path: Path | None = None) -> dict:
    """Load a versioned, bundled, auditable read-only research snapshot."""
    registry = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    errors = validate_registry(registry)
    if errors:
        raise ValueError("Invalid distribution registry: " + "; ".join(errors))
    return registry


def _https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and not (
        parts.username or parts.password
    )


def validate_registry(registry: object) -> list[str]:
    """Validate data contracts, provenance, and conservative safety invariants."""
    errors: list[str] = []
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return ["schema_version must be 1"]
    try:
        date.fromisoformat(registry["reviewed_on"])
    except (ValueError, KeyError, TypeError):
        errors.append("reviewed_on must be ISO 8601 date")
    if registry.get("decision_type") != "editorial_research_hypotheses_not_predictions":
        errors.append("decision_type must explicitly disclaim forecasts")
    platforms = registry.get("platforms")
    markets = registry.get("markets")
    if not isinstance(platforms, list) or not platforms:
        return errors + ["platforms must be a non-empty list"]
    if not isinstance(markets, list) or not markets:
        return errors + ["markets must be a non-empty list"]
    ids: set[str] = set()
    mapping: dict[str, dict] = {}
    for p in platforms:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str):
            errors.append("platform entry missing string id")
            continue
        key = p["id"]
        if key in ids:
            errors.append(f"duplicate platform: {key}")
        ids.add(key)
        mapping[key] = p
        sched = p.get("scheduling", {})
        mode = sched.get("mode") if isinstance(sched, dict) else None
        if mode not in SCHEDULE_MODES:
            errors.append(f"{key}: invalid scheduling mode")
        if mode in SOURCE_REQUIRED and not _https_url(sched.get("official_source")):
            errors.append(f"{key}: documented scheduling claim needs HTTPS official source")
        if p.get("reachweaver_delivery") not in DELIVERY_MODES:
            errors.append(f"{key}: invalid ReachWeaver delivery status")
        if p.get("human_only") not in (True, False):
            errors.append(f"{key}: human_only must be boolean")
        try:
            checked = date.fromisoformat(p["checked_on"])
            snapshot = date.fromisoformat(registry["reviewed_on"])
            if checked > snapshot:
                errors.append(f"{key}: checked_on cannot be later than registry snapshot")
        except (ValueError, KeyError, TypeError):
            errors.append(f"{key}: checked_on must be an ISO 8601 date")
        if p.get("research_source") is not None and not _https_url(p["research_source"]):
            errors.append(f"{key}: research_source must be HTTPS when present")
    for key in ("bluesky", "mastodon", "telegram", "linkedin"):
        if mapping.get(key, {}).get("reachweaver_delivery") != "mock_tested_text":
            errors.append(f"{key}: current integration must remain labelled mock-tested")
    if {p["id"] for p in platforms if p.get("reachweaver_delivery") == "mock_tested_text" and isinstance(p.get("id"), str)} != {"bluesky", "mastodon", "telegram", "linkedin"}:
        errors.append("mock-tested adapters list must match actual implementation")
    hn = mapping.get("hacker_news", {})
    if hn.get("human_only") is not True or hn.get("scheduling", {}).get("mode") != "prohibited" or hn.get("reachweaver_delivery") != "manual_only":
        errors.append("Hacker News must remain human-authored, manual and unscheduled")
    reddit = mapping.get("reddit", {})
    if reddit.get("human_only") is not True or reddit.get("scheduling", {}).get("mode") != "moderator_only":
        errors.append("Reddit schedule must be marked moderator-only")
    market_ids: set[str] = set()
    for market in markets:
        if not isinstance(market, dict) or not isinstance(market.get("id"), str):
            errors.append("market missing id")
            continue
        key = market["id"]
        if key in market_ids:
            errors.append(f"duplicate market: {key}")
        market_ids.add(key)
        tz = market.get("timezone")
        if not tz and key != "global":
            errors.append(f"{key}: non-global market requires IANA timezone")
        elif tz:
            try:
                ZoneInfo(tz)
            except (ValueError, ZoneInfoNotFoundError, TypeError):
                errors.append(f"{key}: invalid IANA timezone")
        ranks = market.get("rankings", {})
        for goal in GOALS:
            choice = ranks.get(goal)
            if not isinstance(choice, list) or not choice:
                errors.append(f"{key}: missing ranking for {goal}")
                continue
            if len(choice) != len(set(choice)):
                errors.append(f"{key}/{goal}: duplicate channel")
            for channel in choice:
                if channel not in ids:
                    errors.append(f"{key}/{goal}: unknown channel {channel}")
    times = registry.get("time_slot_policy", {})
    if times.get("status") != "hypotheses_only":
        errors.append("times must be labelled hypotheses_only")
    for entry in times.get("local_windows", []):
        try:
            time.fromisoformat(entry["local_time"])
        except (TypeError, KeyError, ValueError):
            errors.append("invalid local-time window")
    return errors


def build_distribution_plan(
    region: str,
    goal: str,
    *,
    limit: int = 5,
    local_date: str | None = None,
    audience_timezone: str | None = None,
    registry: dict | None = None,
) -> dict:
    """Build only an offline draft research plan. Never queue or post content."""
    data = registry if registry is not None else load_registry()
    issues = validate_registry(data)
    if issues:
        raise ValueError("Invalid registry: " + "; ".join(issues))
    if goal not in GOALS:
        raise ValueError(f"Unknown goal: {goal}; choose one of {', '.join(GOALS)}")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    markets = {m["id"]: m for m in data["markets"]}
    if region not in markets:
        raise ValueError(f"Unknown region: {region}; choose one of {', '.join(sorted(markets))}")
    market = markets[region]
    platforms = {p["id"]: p for p in data["platforms"]}
    selected: list[dict] = []
    for channel in market["rankings"][goal][:limit]:
        item = platforms[channel]
        selected.append({
            "priority": len(selected) + 1,
            "platform_id": item["id"],
            "platform": item["label"],
            "content_category": item["category"],
            "native_schedule": item["scheduling"]["mode"],
            "native_schedule_scope": item["scheduling"]["scope"],
            "official_source": item["scheduling"]["official_source"],
            "reachweaver_delivery": item["reachweaver_delivery"],
            "requires_human_only": item["human_only"],
            "account_connected": "unknown_not_verified",
        })
    slots = []
    tz_name = audience_timezone or market.get("timezone")
    if audience_timezone:
        try:
            ZoneInfo(audience_timezone)
        except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid IANA audience timezone") from exc
    if local_date is not None:
        if not tz_name:
            raise ValueError("Global date experiments require --timezone for a specific audience")
        try:
            day = date.fromisoformat(local_date)
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc
        zone = ZoneInfo(tz_name)
        for window in data["time_slot_policy"]["local_windows"]:
            clock = time.fromisoformat(window["local_time"])
            stamp = datetime.combine(day, clock, tzinfo=zone)
            slots.append({
                "label": window["id"],
                "local": stamp.isoformat(),
                "utc": stamp.astimezone(timezone.utc).isoformat(),
                "evidence": "untested_time_hypothesis",
                "scheduled": False,
            })
    return {
        "status": "OFFLINE_RESEARCH_PROPOSAL_NOT_SCHEDULED",
        "region": region,
        "market": market["label"],
        "country": market["country"],
        "goal": goal,
        "reviewed_on": data["reviewed_on"],
        "confidence": "editorial_hypothesis_not_reach_forecast",
        "audience_timezone": tz_name,
        "language_options_not_product_capabilities": market["languages"],
        "localization_warning": market["localization_note"],
        "channels": selected,
        "illustrative_time_tests": slots,
        "network_requests": False,
        "account_access": False,
        "posts_created": 0,
        "posts_scheduled": 0,
        "publishing_approval_granted": False,
        "guardrails": [
            "Inspect current platform/community policies and actual owned accounts before acting.",
            "Only human-authored/manual work on Hacker News; no automated or AI-edited HN text.",
            "Native UI scheduling is not a ReachWeaver integration or delivery receipt.",
            "Do not imply translation of content is a supported dictation language.",
            "Test native metrics at matched post ages; unknown outcomes are not zero.",
        ],
    }
