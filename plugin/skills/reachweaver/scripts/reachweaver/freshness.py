"""Offline age report for the bundled research registries (registry freshness).

ReachWeaver ships eight dated research snapshots. Each states platform facts that drift: API
capabilities, native scheduling, discovery guidance, profile fields and limits, sign-up
expectations, AI-search crawler controls, AI-disclosure rules and open-social-web protocol facts.
This module reads only those bundled files and reports how old each dated fact is, so no command
silently relies on a stale one.

One threshold policy for every registry (days between the checked date and ``today``):

* ``fresh``   -- 0..60 days, or a checked date after ``today`` (a snapshot newer than the evaluation date).
* ``aging``   -- more than 60 days: still shown, re-verify against the cited source soon.
* ``stale``   -- more than 120 days: re-verify before relying on it. 120 is the capability registry's
  ``DEFAULT_MAX_AGE_DAYS`` and its ``capabilities``/``health`` warning, so the two agree.
* ``undated`` -- no checked date is recorded. A date is never inferred from another field.

A fact's date is its own (``verified_date``, ``checked_on``, ``accessed``) when the entry records
one. The sign-up registry records only a snapshot ``reviewed_on``, so its entries use that and say
so (``date_basis: registry_reviewed_on``).

Nothing here contacts a network, reads a credential, opens the database or writes a file. A
``fresh`` label is not proof a fact is still true; it only says when someone last checked it.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

from .capabilities import DEFAULT_MAX_AGE_DAYS

AGING_AFTER_DAYS = 60
STALE_AFTER_DAYS = DEFAULT_MAX_AGE_DAYS  # 120: one stale threshold across capabilities, health and this report
STATUSES = ("fresh", "aging", "stale", "undated")
LABELS = {"fresh": "", "aging": "aging - re-verify", "stale": "stale - re-verify", "undated": "undated - unverified"}
REPORT = "reachweaver_registry_age"
POLICY = {
    "aging_after_days": AGING_AFTER_DAYS, "stale_after_days": STALE_AFTER_DAYS,
    "rule": (f"fresh <= {AGING_AFTER_DAYS} days < aging <= {STALE_AFTER_DAYS} days < stale; "
             "undated = no checked date recorded (never inferred)"),
}
# Registries by the CLI name, with the bundled file each one reads.
REGISTRY_FILES = {
    "capabilities": "platform_registry.json",
    "distribution": "distribution_registry.json",
    "discovery": "discovery_registry.json",
    "profile-audit": "profile_audit_registry.json",
    "signup": "signup_registry.json",
    "ai-search": "ai_search_registry.json",
    "ai-disclosure": "ai_disclosure_registry.json",
    "social-web": "social_web_registry.json",
}
# Registries name some platforms differently; facts are grouped per platform under one key.
ALIASES = {"facebook_pages": "facebook", "dev": "devto", "hacker_news": "hackernews",
           "indie_hackers": "indiehackers", "product_hunt": "producthunt"}
NOTE = ("OFFLINE: read only the bundled registry files; no network, account, credential or database was used. "
        "A fresh date says when a fact was last checked, not that it is still true; re-verify against the "
        "cited official source before relying on an aging, stale or undated fact.")


def _parse(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def classify(checked_on: object, today: date) -> tuple[int | None, str]:
    """``(age_days, status)`` for one checked date; ``(None, "undated")`` when it is absent or unreadable."""
    checked = _parse(checked_on)
    if checked is None:
        return None, "undated"
    age = (today - checked).days
    if age > STALE_AFTER_DAYS:
        return age, "stale"
    if age > AGING_AFTER_DAYS:
        return age, "aging"
    return age, "fresh"


def annotate(checked_on: object, today: date | None = None) -> dict:
    """Additive fields for an output that presents a platform fact: checked date, age and label."""
    current = today or date.today()
    age, status = classify(checked_on, current)
    return {"checked_on": checked_on if age is not None else None, "age_days": age,
            "freshness": status, "freshness_label": LABELS[status]}


def suffix(item: dict) -> str:
    """Text tail for a rendered fact: ``checked 2026-10-08`` plus the label when it is not fresh."""
    if item.get("freshness") == "undated" or not item.get("checked_on"):
        return "undated - unverified"
    label = LABELS.get(str(item.get("freshness")), "")
    return f"checked {item['checked_on']}" + (f", {label}" if label else "")


def _fact(registry: str, platform: str, fact: str, checked_on: object, source: object,
          today: date, basis: str = "entry") -> dict:
    age, status = classify(checked_on, today)
    return {"registry": registry, "platform": platform, "platform_key": ALIASES.get(platform, platform),
            "fact": fact, "checked_on": checked_on if age is not None else None, "date_basis": basis,
            "age_days": age, "status": status,
            "source": source if isinstance(source, str) and source.startswith("https://") else None}


def _capability_facts(data: dict, today: date) -> list[dict]:
    out = []
    for entry in data["platforms"]:
        out.append(_fact("capabilities", entry["id"], "capability entry", entry.get("verified_date"),
                         entry.get("official_docs_url"), today))
        limits = entry.get("rate_limits")
        if isinstance(limits, dict) and "accessed" in limits:
            out.append(_fact("capabilities", entry["id"], "rate limits", limits["accessed"], limits.get("source"), today))
    return out


def _distribution_facts(data: dict, today: date) -> list[dict]:
    out = []
    for entry in data["platforms"]:
        sched = entry.get("scheduling") if isinstance(entry.get("scheduling"), dict) else {}
        source = sched.get("official_source") or entry.get("research_source")
        out.append(_fact("distribution", entry["id"], "channel and native scheduling", entry.get("checked_on"),
                         source, today))
    return out


def _discovery_facts(data: dict, today: date) -> list[dict]:
    out = []
    groups = [("default", data["default"])] if isinstance(data.get("default"), dict) else []
    for key, entry in groups + sorted(data["platforms"].items()):
        for source in entry.get("sources", []):
            out.append(_fact("discovery", key, f"guidance source: {source.get('title', 'untitled')}",
                             source.get("checked_on"), source.get("url"), today))
    return out


def _profile_facts(data: dict, today: date) -> list[dict]:
    out = []
    for entry in data["platforms"]:
        evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
        out.append(_fact("profile-audit", entry["id"], f"profile evidence ({evidence.get('tier', 'unknown tier')})",
                         evidence.get("checked_on"), evidence.get("official_source"), today))
        for limit in entry.get("profile_field_limits", []):
            out.append(_fact("profile-audit", entry["id"], f"{limit['field']} length limit", limit.get("checked_on"),
                             limit.get("source_url"), today))
    return out


def _signup_facts(data: dict, today: date) -> list[dict]:
    return [_fact("signup", key, "sign-up form expectations", data.get("reviewed_on"), None, today,
                  basis="registry_reviewed_on")
            for key in sorted(data.get("platforms", {}))]


def _evidence_list(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ai_search_facts(data: dict, today: date) -> list[dict]:
    """Crawler controls per crawler token (lowercased); page signals, measurement and the limitation as ``web``."""
    out = []
    limitation = data.get("limitation")
    if isinstance(limitation, dict):
        out.append(_fact("ai-search", "web", "limitation", limitation.get("checked_on"),
                         limitation.get("source_url"), today))
    for crawler in data.get("crawlers", []):
        for evidence in _evidence_list(crawler.get("evidence")):
            out.append(_fact("ai-search", str(crawler.get("token", "unknown")).lower(),
                             f"crawler {crawler.get('token')} ({crawler.get('operator')})", evidence.get("checked_on"),
                             evidence.get("source_url"), today))
    for group, label in (("page_signals", "page signal"), ("measurement", "measurement")):
        for entry in data.get(group, []):
            for evidence in _evidence_list(entry.get("evidence")):
                out.append(_fact("ai-search", "web", f"{label}: {entry.get('id')}", evidence.get("checked_on"),
                                 evidence.get("source_url"), today))
    return out


def _ai_disclosure_facts(data: dict, today: date) -> list[dict]:
    """One fact per platform (or jurisdiction) and each source it cites; uncited sources as ``general``."""
    sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
    out, cited = [], set()
    owners = [*sorted((data.get("platforms") or {}).items()), *sorted((data.get("jurisdictions") or {}).items())]
    for key, entry in owners:
        ids = sorted({sid for field, value in entry.items() if field.endswith("_source") and isinstance(value, list)
                      for sid in value if isinstance(sid, str)})
        for sid in ids:
            cited.add(sid)
            source = sources.get(sid) if isinstance(sources.get(sid), dict) else {}
            out.append(_fact("ai-disclosure", key, f"AI disclosure source: {sid}", source.get("checked_on"),
                             source.get("url"), today))
    for sid in sorted(set(sources) - cited):
        source = sources[sid] if isinstance(sources[sid], dict) else {}
        out.append(_fact("ai-disclosure", "general", f"AI disclosure source: {sid}", source.get("checked_on"),
                         source.get("url"), today))
    return out


def _social_web_facts(data: dict, today: date) -> list[dict]:
    """Every quoted source of protocols, platforms, bridges, identity checks and home options, keyed by its id."""
    out = []
    for group, label in (("protocols", "protocol"), ("platforms", "open social web entry"), ("bridges", "bridge"),
                         ("identity_checks", "identity check"), ("home_options", "home option")):
        for entry in data.get(group, []):
            for source in _evidence_list(entry.get("sources")):
                out.append(_fact("social-web", str(entry.get("id", "unknown")), f"{label}: {entry.get('id')}",
                                 source.get("checked_on"), source.get("url"), today))
    return out


EXTRACTORS: dict[str, Callable[[dict, date], list[dict]]] = {
    "capabilities": _capability_facts, "distribution": _distribution_facts, "discovery": _discovery_facts,
    "profile-audit": _profile_facts, "signup": _signup_facts, "ai-search": _ai_search_facts,
    "ai-disclosure": _ai_disclosure_facts, "social-web": _social_web_facts,
}


def _load(name: str) -> dict:
    """The registry through its own validating loader, so this report sees exactly what the commands use."""
    if name == "capabilities":
        from .capabilities import load_registry as load_capabilities
        return load_capabilities()
    if name == "distribution":
        from .distribution import load_registry as load_distribution
        return load_distribution()
    if name == "discovery":
        from .discovery import load_discovery_registry
        return load_discovery_registry()
    if name == "profile-audit":
        from .profile_audit import load_registry as load_profile
        return load_profile()
    if name == "ai-search":
        from .ai_visibility import load_registry as load_ai_search
        return load_ai_search()
    if name == "ai-disclosure":
        from .ai_labels import load_registry as load_ai_disclosure
        return load_ai_disclosure()
    if name == "social-web":
        from .social_web import load_registry as load_social_web
        return load_social_web()
    from .account_kit import load_registry as load_signup, validate_registry as validate_signup
    data = load_signup()
    errors = validate_signup(data)
    if errors:
        raise ValueError("Invalid signup registry: " + "; ".join(errors))
    return data


def _counts(facts: list[dict]) -> dict[str, int]:
    return {status: sum(1 for f in facts if f["status"] == status) for status in STATUSES}


def _oldest(facts: list[dict]) -> str | None:
    dated = [f["checked_on"] for f in facts if f["checked_on"]]
    return min(dated) if dated else None


def _worst(facts: list[dict]) -> str:
    order = ("fresh", "undated", "aging", "stale")  # undated sits below aging: it is unverified, not old
    return max((f["status"] for f in facts), key=order.index, default="fresh")


def build_report(today: date | None = None, *, registry: str | None = None, platform: str | None = None,
                 include_fresh: bool = False,
                 loaders: dict[str, Callable[[], dict]] | None = None) -> dict:
    """Per-fact age, per-registry counts and the oldest checked date per platform, offline.

    ``today`` is injectable for reproducible output and tests. ``registry`` narrows to one registry name,
    ``platform`` to one platform id (aliases such as ``facebook_pages``/``facebook`` match each other).
    ``facts`` lists aging, stale and undated facts, plus fresh ones with ``include_fresh``. A registry that
    fails its own validation is reported with its error, never silently skipped.
    """
    current = today or date.today()
    names = list(REGISTRY_FILES)
    if registry is not None:
        if registry not in REGISTRY_FILES:
            raise ValueError(f"Unknown registry {registry!r}; choose one of {', '.join(names)}")
        names = [registry]
    wanted = ALIASES.get(platform, platform) if platform else None
    registries: list[dict] = []
    every: list[dict] = []
    for name in names:
        row: dict = {"registry": name, "file": f"reachweaver/{REGISTRY_FILES[name]}", "reviewed_on": None,
                     "error": None}
        try:
            loader = (loaders or {}).get(name)
            data = loader() if loader else _load(name)
            facts = EXTRACTORS[name](data, current)
            row["reviewed_on"] = data.get("reviewed_on") or data.get("checked_on")  # ai-disclosure: checked_on
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            facts = []
            row["error"] = str(exc) or type(exc).__name__
        if wanted:
            facts = [f for f in facts if f["platform_key"] == wanted]
        row.update(facts=len(facts), counts=_counts(facts), oldest_checked_on=_oldest(facts))
        registries.append(row)
        every.extend(facts)
    platforms: dict[str, dict] = {}
    for key in sorted({f["platform_key"] for f in every}):
        mine = [f for f in every if f["platform_key"] == key]
        platforms[key] = {"oldest_checked_on": _oldest(mine), "status": _worst(mine), "counts": _counts(mine),
                          "registries": sorted({f["registry"] for f in mine})}
    shown = [f for f in every if include_fresh or f["status"] != "fresh"]
    order = {"stale": 0, "aging": 1, "undated": 2, "fresh": 3}
    shown.sort(key=lambda f: (order[f["status"]], f["checked_on"] or "", f["registry"], f["platform"], f["fact"]))
    return {
        "report": REPORT, "as_of": current.isoformat(), "policy": dict(POLICY),
        "filters": {"registry": registry, "platform": platform, "include_fresh": include_fresh},
        "counts": _counts(every), "total_facts": len(every), "oldest_checked_on": _oldest(every),
        "registries": registries, "platforms": platforms, "facts": shown,
        "network_requests": False, "note": NOTE,
    }


def summary_line(report: dict) -> str:
    """One line: counts, the oldest checked date and the command for detail."""
    c = report["counts"]
    errors = [r["registry"] for r in report["registries"] if r["error"]]
    text = (f"Research facts as of {report['as_of']}: {c['stale']} stale, {c['aging']} aging, {c['undated']} undated, "
            f"{c['fresh']} fresh of {report['total_facts']} (oldest checked {report['oldest_checked_on'] or 'none'})")
    if errors:
        text += f"; INVALID registry: {', '.join(errors)}"
    return text + "; detail: reachweaver registry-age"


def warning_line(today: date | None = None, *, report: dict | None = None) -> str | None:
    """A one-line warning for session summaries (``context``/``start``), or None when nothing is aging or stale.

    Undated facts alone do not warn: they are permanently labelled unverified hypotheses in their registry
    and in ``registry-age``. An invalid registry always warns.
    """
    data = report or build_report(today)
    c = data["counts"]
    if not (c["stale"] or c["aging"] or any(r["error"] for r in data["registries"])):
        return None
    return "WARNING: " + summary_line(data)


def platform_summary(report: dict, platform: str, *, exclude: tuple[str, ...] = ()) -> dict:
    """Freshness of one platform's facts across registries (``exclude`` names registries to leave out).

    Needs a report built with ``include_fresh=True`` so that every fact is present.
    """
    if not report["filters"]["include_fresh"]:
        raise ValueError("platform_summary needs a report built with include_fresh=True")
    key = ALIASES.get(platform, platform)
    facts = [f for f in report["facts"] if f["platform_key"] == key and f["registry"] not in exclude]
    return {"oldest_checked_on": _oldest(facts), "status": _worst(facts) if facts else "no_facts",
            "counts": _counts(facts), "registries": sorted({f["registry"] for f in facts})}


def compact(report: dict) -> dict:
    """The small form embedded in ``health`` and ``doctor``: counts, policy and per-registry dates."""
    return {"as_of": report["as_of"], "policy": report["policy"], "counts": report["counts"],
            "total_facts": report["total_facts"], "oldest_checked_on": report["oldest_checked_on"],
            "registries": {r["registry"]: {"reviewed_on": r["reviewed_on"], "oldest_checked_on": r["oldest_checked_on"],
                                           "counts": r["counts"], "error": r["error"]}
                           for r in report["registries"]},
            "line": summary_line(report), "command": "reachweaver registry-age"}


def render_text(report: dict) -> str:
    lines = [summary_line(report), f"Policy: {report['policy']['rule']}"]
    for row in report["registries"]:
        c = row["counts"]
        state = f"INVALID: {row['error']}" if row["error"] else (
            f"{c['stale']} stale, {c['aging']} aging, {c['undated']} undated, {c['fresh']} fresh; "
            f"oldest {row['oldest_checked_on'] or 'none'}")
        lines.append(f"  {row['registry']} (reviewed {row['reviewed_on'] or 'unknown'}): {state}")
    if report["facts"]:
        lines.append("Facts to re-verify (stale first):" if not report["filters"]["include_fresh"] else "Facts:")
        for f in report["facts"]:
            age = f"{f['age_days']} days" if f["age_days"] is not None else "no date"
            lines.append(f"  {f['status'].upper():8} {f['registry']}/{f['platform']}: {f['fact']} "
                         f"({f['checked_on'] or 'undated'}, {age})" + (f" {f['source']}" if f["source"] else ""))
    else:
        lines.append("Nothing aging, stale or undated in this selection.")
    lines.append(report["note"])
    return "\n".join(lines)
