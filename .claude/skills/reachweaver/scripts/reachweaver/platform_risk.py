"""``platform-risk``: read-only multi-homing / platform dependency report.

Question answered: how much of your recorded reach sits on each platform, how concentrated is it, and which
sourced platform risks apply to the platforms you depend on most?

Method (docs/PLATFORM-RISK.md):

* Basis. The measured basis is the sum, per platform, of one value per published post: the ``--metric``
  (default ``views``) at a matched post age (``baseline_eval.matched_snapshot``: exact counts only, windowed
  snapshots skipped, the nearest snapshot within ``--tolerance-hours`` of ``--age-hours``), else the latest
  exact non-windowed snapshot of that post, which is labelled "not age-matched". A post's snapshots are never
  summed. The measured basis is used for a scope only when every platform in it has at least one measured
  post; otherwise the scope falls back to post counts (published posts plus manual shares recorded with
  ``mark-shared``/``share``), and the output says so. Platforms count views differently, so a measured share
  compares unlike counters.
* Concentration. Top-platform share and the Herfindahl-Hirschman index (sum of squared shares, 0..1) with
  "effective platforms" = 1 / HHI. The plain-language bands are editorial thresholds from the registry.
* Flags. ``platform_risk_registry.json`` quotes official pages (checked dates) for: X pay-per-use API pricing,
  LinkedIn API terms on automated posting, Reddit's separate agreement for commercial API use and the
  TikTok US joint venture (separate US recommendation algorithm since 2026-01-23). Owned channels (site, blog,
  newsletter, e-mail list, or anything you declare with ``--owned``) count as low dependency: an editorial
  hypothesis.
* Scopes. Overall, per account and per persona (the optional ``persona`` field of accounts.json).
* Advice is phrased as hypotheses to check, never as a forecast.

The database is opened ``mode=ro`` (``storage.connect_readonly``): nothing here drafts, approves, schedules,
publishes, writes a file, touches the send queue or contacts a network. ``market_notices`` is a pure helper the
planners (``calendar-plan``, ``distribution-plan``) call to flag market-specific advice.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .policy import ValidationError

KIND = "reachweaver.platform-risk"
SCHEMA_VERSION = 1
REGISTRY_FILE = Path(__file__).with_name("platform_risk_registry.json")
METRICS = ("views", "impressions")
BASIS_MEASURED = "measured_reach"
BASIS_POSTS = "post_count"
_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_OWNED_VALUE = re.compile(r"^([a-z][a-z0-9_.-]{0,62})=([0-9]{1,15})$")
_MARKET = re.compile(r"^(?:([a-z][a-z0-9_-]{0,30})@)?([A-Za-z]{2,10})$")
__all__ = ("load_registry", "validate_registry", "parse_market", "market_notices", "concentration", "analyze",
           "render_text", "add_parser", "run_cli")


# ---- registry ------------------------------------------------------------------------------------------------

def _source_errors(owner: str, sources: object, reviewed: str) -> list[str]:
    if not isinstance(sources, list) or not sources:
        return [f"{owner}: needs a non-empty sources list"]
    errors = []
    for source in sources:
        if not isinstance(source, dict) or not str(source.get("url", "")).startswith("https://"):
            errors.append(f"{owner}: every source needs an https url")
            continue
        if not isinstance(source.get("quote"), str) or len(source["quote"].strip()) < 10:
            errors.append(f"{owner}: every source needs the verified quote")
        if source.get("kind") not in ("official", "secondary"):
            errors.append(f"{owner}: source kind must be official or secondary")
        checked = source.get("checked_on")
        if not isinstance(checked, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", checked) or checked > reviewed:
            errors.append(f"{owner}: checked_on must be an ISO date not later than reviewed_on")
    return errors


def validate_registry(data: object) -> list[str]:
    """Data contract: every flag is sourced and quoted; hypotheses and thresholds are labelled as such."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return ["schema_version must be 1"]
    reviewed = data.get("reviewed_on")
    if not isinstance(reviewed, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", reviewed):
        return ["reviewed_on must be an ISO date"]
    errors: list[str] = []
    if data.get("decision_type") != "sourced_platform_facts_and_editorial_hypotheses":
        errors.append("decision_type must separate sourced facts from hypotheses")
    flags = data.get("flags")
    flag_ids: set[str] = set()
    if not isinstance(flags, list) or not flags:
        return errors + ["flags must be a non-empty list"]
    for flag in flags:
        key = flag.get("id") if isinstance(flag, dict) else None
        if not isinstance(key, str) or not _ID.fullmatch(key) or key in flag_ids:
            errors.append(f"flag id missing, invalid or duplicate: {key}")
            continue
        flag_ids.add(key)
        for field in ("platform", "label", "kind", "fact", "hypothesis"):
            if not isinstance(flag.get(field), str) or not flag[field].strip():
                errors.append(f"{key}: {field} is required")
        errors += _source_errors(key, flag.get("sources"), reviewed)
    markets = data.get("markets")
    if not isinstance(markets, list) or "global" not in markets:
        errors.append("markets must list the known markets, including global")
        markets = []
    for notice in data.get("market_notices", []):
        if not isinstance(notice, dict) or notice.get("flag") not in flag_ids or notice.get("market") not in markets:
            errors.append("each market notice needs a known flag and market")
        elif not isinstance(notice.get("notice"), str) or "re-verify" not in notice["notice"]:
            errors.append(f"{notice.get('flag')}: a market notice must tell the reader to re-verify")
    owned = data.get("owned_channels")
    if not isinstance(owned, dict) or owned.get("status") != "editorial_hypothesis" or not owned.get("ids"):
        errors.append("owned_channels must be labelled editorial_hypothesis and list ids")
    conc = data.get("concentration")
    if not isinstance(conc, dict) or conc.get("status") != "editorial_thresholds" or not conc.get("bands"):
        errors.append("concentration bands must be labelled editorial_thresholds")
    return errors


def load_registry(path: Path | None = None) -> dict:
    data = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    errors = validate_registry(data)
    if errors:
        raise ValidationError("Platform risk registry is invalid: " + "; ".join(errors[:5]))
    return data


def _flag_view(flag: Mapping) -> dict:
    return {"id": flag["id"], "label": flag["label"], "kind": flag["kind"], "fact": flag["fact"],
            "hypothesis": flag["hypothesis"], "status": "sourced_fact_plus_hypothesis",
            "sources": [dict(s) for s in flag["sources"]]}


# ---- markets (calendar-plan, distribution-plan, platform-risk) -------------------------------------------------

def parse_market(value: str, registry: dict | None = None) -> tuple[str | None, str]:
    """``US``, ``global`` or ``PLATFORM@MARKET`` (e.g. ``tiktok@US``) -> (platform or None, market)."""
    data = registry if registry is not None else load_registry()
    text = value.strip() if isinstance(value, str) else ""
    match = _MARKET.fullmatch(text)
    known = {m.lower(): m for m in data["markets"]}
    if not match or match.group(2).lower() not in known:
        raise ValidationError(f"--market {text[:40]!r}: use one of {', '.join(data['markets'])}, optionally as "
                              "PLATFORM@MARKET such as tiktok@US")
    return match.group(1), known[match.group(2).lower()]


def _market_for(platform: str, markets: Iterable[tuple[str | None, str]]) -> str | None:
    chosen = None
    for scope, market in markets:
        if scope == platform:
            return market
        if scope is None:
            chosen = market
    return chosen


def market_notices(platforms: Iterable[str], markets: Iterable[tuple[str | None, str]] = (),
                   registry: dict | None = None) -> list[dict]:
    """Market-specific notices for ``platforms`` (pure, offline). Empty when no notice applies to any platform.

    ``applies`` is True when the stated market is the notice's market, False when another market was stated
    and None when no market was stated. The notice only flags and cites; it never changes the advice itself.
    """
    data = registry if registry is not None else load_registry()
    flags = {f["id"]: f for f in data["flags"]}
    wanted = list(dict.fromkeys(platforms))
    markets = list(markets)
    out = []
    for notice in data.get("market_notices", []):
        if notice["platform"] not in wanted:
            continue
        stated = _market_for(notice["platform"], markets)
        applies = None if stated is None else stated == notice["market"]
        if applies:
            text = notice["notice"]
        elif applies is None:
            text = (f"Market not stated. {notice['notice']} If your audience is in the {notice['market']}, pass "
                    f"--market {notice['platform']}@{notice['market']}.")
        else:
            text = (f"Stated market {stated}: this advice is not {notice['market']}-specific. {notice['notice']}")
        flag = flags[notice["flag"]]
        out.append({"platform": notice["platform"], "market": notice["market"], "stated_market": stated,
                    "applies": applies, "since": notice.get("since"), "flag": notice["flag"], "notice": text,
                    "sources": [{"url": s["url"], "quote": s["quote"], "checked_on": s["checked_on"]}
                                for s in flag["sources"][:1]],
                    "status": "sourced_flag_reverify"})
    return out


# ---- concentration -----------------------------------------------------------------------------------------

def concentration(values: Mapping[str, float], registry: dict | None = None) -> dict:
    """Top share, HHI (0..1), effective platforms and the editorial band for one scope's basis values."""
    data = registry if registry is not None else load_registry()
    total = float(sum(v for v in values.values() if v > 0))
    if total <= 0:
        return {"total": 0, "top_platform": None, "top_share": None, "hhi": None, "hhi_points": None,
                "effective_platforms": None, "label": "no_data", "text": "Nothing recorded to compare yet."}
    shares = {k: v / total for k, v in values.items() if v > 0}
    hhi = sum(s * s for s in shares.values())
    effective = 1 / hhi
    top = max(sorted(shares), key=lambda k: shares[k])
    band = next(b for b in data["concentration"]["bands"]
                if b["max_effective_platforms"] is None or effective < b["max_effective_platforms"])
    return {"total": round(total, 6) if total != int(total) else int(total), "top_platform": top,
            "top_share": round(shares[top], 4), "hhi": round(hhi, 4), "hhi_points": round(hhi * 10000),
            "effective_platforms": round(effective, 2), "label": band["label"], "text": band["text"],
            "shares": {k: round(v, 4) for k, v in sorted(shares.items(), key=lambda kv: (-kv[1], kv[0]))}}


# ---- data collection (read-only) ---------------------------------------------------------------------------

def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}  # table name is a fixed literal


def _tables(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _parse_time(value: object) -> datetime | None:
    from .storage import parse_utc
    try:
        return datetime.fromisoformat(parse_utc(str(value)))
    except (ValidationError, ValueError, TypeError):
        return None


def _post_value(con: sqlite3.Connection, post_id: str, sent: datetime | None, metric: str, *, age_hours: int,
                tolerance_hours: int, skipped: Counter) -> tuple[int | None, str]:
    """(value, method): the matched-age exact value, else the latest exact non-windowed value, else (None, ...)."""
    from .baseline_eval import matched_snapshot
    from .growth import is_windowed_snapshot
    from .storage import snapshots_for
    rows = snapshots_for(con, post_id)
    if not rows:
        return None, "no_snapshot"
    if sent is not None:
        best = None
        for source in sorted({r["source"] for r in rows}):
            row = matched_snapshot(con, post_id, sent, source=source, age_hours=age_hours,
                                   tolerance_hours=tolerance_hours, usable=lambda r: r[metric] is not None,
                                   skipped=skipped)
            if row is None:
                continue
            observed = _parse_time(row["observed_at"])
            distance = abs(observed.timestamp() - sent.timestamp() - age_hours * 3600) if observed else float("inf")
            if best is None or distance < best[0]:
                best = (distance, row)
        if best is not None:
            return int(best[1][metric]), "matched_age"
    usable = [r for r in rows if r[metric] is not None and not is_windowed_snapshot(r)]
    if not usable:
        return None, "no_exact_value"
    latest = max(usable, key=lambda r: (str(r["observed_at"]), r["id"]))
    return int(latest[metric]), "latest_not_age_matched"


def _published(con: sqlite3.Connection, *, account: str | None, since: datetime | None) -> list[dict]:
    have = _columns(con, "posts")
    sent = "send_started_at" if "send_started_at" in have else "NULL"
    query = f"SELECT id, account_id, platform, {sent} AS sent_at, created_at FROM posts WHERE status='published'"
    params: list[Any] = []
    if account:
        query += " AND account_id=?"
        params.append(account)
    out = []
    for row in con.execute(query + " ORDER BY id", params):
        when = _parse_time(row["sent_at"]) or _parse_time(row["created_at"])
        if since is not None and (when is None or when < since):
            continue
        out.append({"id": row["id"], "account_id": row["account_id"], "platform": row["platform"],
                    "sent": _parse_time(row["sent_at"])})
    return out


def _manual_shares(con: sqlite3.Connection, *, account: str | None, since: datetime | None) -> list[dict]:
    if "shared_material" not in _tables(con):
        return []
    out = []
    for row in con.execute("SELECT platform, account_id, posted_at, recorded_at FROM shared_material ORDER BY id"):
        if account and row["account_id"] != account:
            continue
        when = _parse_time(row["posted_at"]) or _parse_time(row["recorded_at"])
        if since is not None and (when is None or when < since):
            continue
        out.append({"platform": row["platform"], "account_id": row["account_id"] or f"manual:{row['platform']}"})
    return out


def _owned_pairs(values: Iterable[str], flag: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in values:
        match = _OWNED_VALUE.fullmatch(item.strip()) if isinstance(item, str) else None
        if not match:
            raise ValidationError(f"{flag} {str(item)[:40]!r}: use NAME=N, e.g. newsletter=1200 (a lowercase name, "
                                  "a whole number)")
        if match.group(1) in out:
            raise ValidationError(f"{flag} names {match.group(1)} twice")
        out[match.group(1)] = int(match.group(2))
    return out


# ---- analysis ------------------------------------------------------------------------------------------------

def _scope(name: str, platforms: dict[str, dict], registry: dict, owned: set[str]) -> dict:
    measured_ok = bool(platforms) and all(p["measured_posts"] > 0 or p.get("owner_stated_reach") is not None
                                          for p in platforms.values())
    basis = BASIS_MEASURED if measured_ok else BASIS_POSTS
    key = "measured_reach" if measured_ok else "posts"
    values = {pid: float(p[key] or 0) for pid, p in platforms.items()}
    result = concentration(values, registry)
    owned_share = round(sum(result.get("shares", {}).get(pid, 0.0) for pid in platforms if pid in owned), 4) \
        if result["total"] else None
    unmeasured = sorted(pid for pid, p in platforms.items()
                        if p["measured_posts"] == 0 and p.get("owner_stated_reach") is None)
    note = ("Shares of measured reach (one value per post; platforms count views differently)." if measured_ok else
            "FALLBACK: shares of post counts, not reach: " + (", ".join(unmeasured) or "no platform")
            + " has no measured value, so measured reach cannot be compared across this scope.")
    return {"scope": name, "basis": basis, "basis_note": note, **result, "owned_share": owned_share,
            "platforms": sorted(platforms)}


def analyze(con: sqlite3.Connection, *, accounts: Mapping[str, Mapping] | None = None, account: str | None = None,
            since: datetime | None = None, metric: str = "views", age_hours: int = 168, tolerance_hours: int = 48,
            owned: Iterable[str] = (), owned_reach: Mapping[str, int] | None = None,
            owned_posts: Mapping[str, int] | None = None, markets: Iterable[tuple[str | None, str]] = (),
            canonical_home: dict | None = None, now: datetime | None = None, registry: dict | None = None) -> dict:
    """Read-only platform dependency report (SELECT only)."""
    data = registry if registry is not None else load_registry()
    if metric not in METRICS:
        raise ValidationError(f"--metric must be one of {', '.join(METRICS)}")
    if type(age_hours) is not int or not 24 <= age_hours <= 720:
        raise ValidationError("--age-hours must be 24..720")
    if type(tolerance_hours) is not int or not 1 <= tolerance_hours <= 168 or tolerance_hours >= age_hours:
        raise ValidationError("--tolerance-hours must be 1..168 and smaller than --age-hours")
    accounts = dict(accounts or {})
    owned_ids = set(data["owned_channels"]["ids"]) | {o.strip().lower() for o in owned}
    owned_reach = dict(owned_reach or {})
    owned_posts = dict(owned_posts or {})
    markets = list(markets)
    skipped: Counter = Counter()
    methods: Counter = Counter()
    per_account: dict[str, dict] = {}

    def slot(account_id: str, platform: str) -> dict:
        item = per_account.get(account_id)
        if item is None:
            meta = accounts.get(account_id) or {}
            item = per_account[account_id] = {
                "account_id": account_id, "platform": platform,
                "persona": meta.get("persona") if isinstance(meta.get("persona"), str) else None,
                "published_posts": 0, "manual_shares": 0, "posts": 0, "measured_posts": 0, "measured_reach": 0,
                "owner_stated_reach": None, "owned": platform in owned_ids or account_id in owned_ids,
                "configured": account_id in accounts}
        return item

    for post in _published(con, account=account, since=since):
        item = slot(post["account_id"], post["platform"])
        item["published_posts"] += 1
        item["posts"] += 1
        value, method = _post_value(con, post["id"], post["sent"], metric, age_hours=age_hours,
                                    tolerance_hours=tolerance_hours, skipped=skipped)
        methods[method] += 1
        if value is not None:
            item["measured_posts"] += 1
            item["measured_reach"] += value
    for share in _manual_shares(con, account=account, since=since):
        item = slot(share["account_id"], share["platform"])
        item["manual_shares"] += 1
        item["posts"] += 1
    if account is None:
        for name in sorted(set(owned_reach) | set(owned_posts)):
            item = slot(f"owned:{name}", name)
            item["owned"] = True
            item["configured"] = False
            item["owner_stated_reach"] = owned_reach.get(name)
            if name in owned_reach:
                item["measured_reach"] = owned_reach[name]
            item["posts"] += owned_posts.get(name, 0)
            owned_ids.add(name)

    def by_platform(rows: Iterable[dict]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in rows:
            p = out.setdefault(row["platform"], {"posts": 0, "measured_posts": 0, "measured_reach": 0,
                                                 "owner_stated_reach": None})
            p["posts"] += row["posts"]
            p["measured_posts"] += row["measured_posts"]
            p["measured_reach"] += row["measured_reach"]
            if row["owner_stated_reach"] is not None:
                p["owner_stated_reach"] = (p["owner_stated_reach"] or 0) + row["owner_stated_reach"]
        return out

    rows = sorted(per_account.values(), key=lambda r: r["account_id"])
    overall_platforms = by_platform(rows)
    overall = _scope("overall", overall_platforms, data, owned_ids)
    personas = sorted({r["persona"] for r in rows if r["persona"]})
    persona_scopes = [_scope(f"persona:{name}", by_platform(r for r in rows if r["persona"] == name), data, owned_ids)
                      for name in personas]
    flags_by_platform: dict[str, list[dict]] = {}
    for flag in data["flags"]:
        flags_by_platform.setdefault(flag["platform"], []).append(_flag_view(flag))
    basis_values = overall.get("shares", {})
    for row in rows:
        platform_total = overall_platforms[row["platform"]]
        key = "measured_reach" if overall["basis"] == BASIS_MEASURED else "posts"
        part = row[key] / platform_total[key] if platform_total[key] else 0.0
        row["share_of_overall"] = round(basis_values.get(row["platform"], 0.0) * part, 4) if basis_values else None
        row["flags"] = [f["id"] for f in flags_by_platform.get(row["platform"], [])]
    platforms_out = []
    for pid, p in sorted(overall_platforms.items(), key=lambda kv: (-basis_values.get(kv[0], 0.0), kv[0])):
        flags = flags_by_platform.get(pid, [])
        platforms_out.append({"platform": pid, **p, "share": basis_values.get(pid), "owned": pid in owned_ids,
                              "dependency": "low_owned_hypothesis" if pid in owned_ids else
                              ("flagged" if flags else "no_flag_recorded"),
                              "flags": flags})
    notices = market_notices(list(overall_platforms), markets, data)
    for item in platforms_out:
        for flag in item["flags"]:
            if flag["id"] in {n["flag"] for n in notices if n["platform"] == item["platform"]}:
                flag["market"] = next(n for n in notices if n["platform"] == item["platform"])["stated_market"]
    advice = _advice(overall, platforms_out, canonical_home)
    current = now or datetime.now(timezone.utc)
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "status": "ok" if rows else "no_posts",
        "generated_at": current.isoformat(timespec="seconds"), "registry_reviewed_on": data["reviewed_on"],
        "filters": {"account": account, "since": since.isoformat(timespec="seconds") if since else None,
                    "metric": metric, "age_hours": age_hours, "tolerance_hours": tolerance_hours,
                    "markets": [f"{p}@{m}" if p else m for p, m in markets]},
        "measurement": {"posts_by_method": dict(sorted(methods.items())),
                        "windowed_snapshots_skipped": skipped.get("windowed_snapshots_skipped", 0),
                        "note": ("matched_age = the exact value nearest the post age; latest_not_age_matched = the "
                                 "latest exact value at whatever age it was recorded; posts without an exact value "
                                 "count only in the post-count fallback.")},
        "overall": overall, "personas": persona_scopes, "accounts": rows, "platforms": platforms_out,
        "market_notices": notices, "canonical_home": canonical_home,
        "owned_channels": {"ids": sorted(owned_ids), "status": data["owned_channels"]["status"],
                           "rationale": data["owned_channels"]["rationale"]},
        "concentration_method": data["concentration"]["method"],
        "concentration_thresholds": "editorial (registry), not an industry or legal standard",
        "advice": advice,
        "limits": [
            "Only what ReachWeaver recorded: posts published through it, manual shares you recorded and numbers you "
            "imported or fetched. Reach elsewhere is invisible here.",
            "Views and impressions are counted differently on every platform; a measured share compares unlike "
            "counters.",
            "Flags quote official pages on their checked date; terms and prices change. Re-read the source.",
            "Advice lines are hypotheses to check, never forecasts.",
        ],
        "database_written": False, "network_requests": False, "queue_touched": False,
        "publishing_approval_granted": False,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{round(value * 100)}%"


def _advice(overall: dict, platforms: list[dict], canonical_home: dict | None) -> list[dict]:
    if not overall["total"]:
        return [{"id": "no_data", "hypothesis": "Nothing recorded yet, so dependency cannot be estimated.",
                 "check": "Publish or record a few posts (mark-shared for manual ones), then import or fetch metrics."}]
    basis = "reach" if overall["basis"] == BASIS_MEASURED else "posts (post-count fallback, not reach)"
    advice = []
    top = next(p for p in platforms if p["platform"] == overall["top_platform"])
    top_flags = ", ".join(f["label"] for f in top["flags"]) or "no flag recorded here"
    advice.append({"id": "top_platform",
                   "hypothesis": (f"{top['platform']} holds {_pct(overall['top_share'])} of your recorded {basis} "
                                  f"({overall['label']}: about {overall['effective_platforms']} effective platforms). "
                                  f"A rule, price or algorithm change there would touch that share. Flags: {top_flags}."),
                   "check": "Compare with your own analytics before acting; the share covers recorded activity only."})
    owned = overall["owned_share"] or 0.0
    if owned > 0:
        advice.append({"id": "owned_share",
                       "hypothesis": f"An owned channel holds {_pct(owned)} of your recorded {basis}.",
                       "check": "Owned figures you stated (--owned-reach) are your own numbers, not measured here."})
    else:
        home = (canonical_home or {}).get("target")
        advice.append({"id": "no_owned_share",
                       "hypothesis": (f"No owned channel holds any of your recorded {basis}; all of it depends on "
                                      "platforms you do not control."),
                       "check": ("Try publishing the next piece first on " + (f"your canonical home ({home})"
                                 if home else "an address you control (social-web-plan --site URL)")
                                 + ", then record its reach with --owned-reach NAME=N next time.")})
    for item in platforms:
        for flag in item["flags"]:
            if (item["share"] or 0) >= 0.2:
                advice.append({"id": f"flag:{flag['id']}",
                               "hypothesis": (f"{_pct(item['share'])} of your recorded {basis} is on "
                                              f"{item['platform']}: {flag['hypothesis']}"),
                               "check": f"Re-read {flag['sources'][0]['url']} (checked {flag['sources'][0]['checked_on']})."})
    if overall["basis"] == BASIS_POSTS:
        advice.append({"id": "measure_more", "hypothesis": overall["basis_note"],
                       "check": "metrics-fetch or import-metrics for the unmeasured platforms gives a reach basis."})
    return advice


# ---- rendering -----------------------------------------------------------------------------------------------

def render_text(report: Mapping) -> str:
    if report["status"] == "no_database":
        return f"No ReachWeaver database at {report['database']} yet: nothing to measure."
    overall = report["overall"]
    lines = [f"Platform dependency ({report['filters']['metric']}, matched age {report['filters']['age_hours']}h; "
             f"registry reviewed {report['registry_reviewed_on']})", "",
             f"Basis: {overall['basis']}. {overall['basis_note']}"]
    if overall["total"]:
        lines.append(f"Concentration: {overall['label']} - {overall['text']} Top: {overall['top_platform']} "
                     f"{_pct(overall['top_share'])}; HHI {overall['hhi']} ({overall['hhi_points']} points); "
                     f"effective platforms {overall['effective_platforms']}; owned share {_pct(overall['owned_share'])}.")
    lines += ["", "Platforms:"]
    for p in report["platforms"]:
        flags = "; ".join(f["label"] for f in p["flags"]) or "-"
        lines.append(f"  {p['platform']}: share {_pct(p['share'])} | posts {p['posts']} | measured "
                     f"{p['measured_posts']} ({p['measured_reach']}) | {p['dependency']} | flags: {flags}")
    if report["personas"]:
        lines += ["", "Personas:"]
        for scope in report["personas"]:
            lines.append(f"  {scope['scope']}: {scope['label']}, top {scope['top_platform']} "
                         f"{_pct(scope['top_share'])} ({scope['basis']})")
    lines += ["", "Accounts:"]
    for row in report["accounts"]:
        lines.append(f"  {row['account_id']} ({row['platform']}): {_pct(row['share_of_overall'])} of overall; "
                     f"posts {row['posts']}, measured {row['measured_posts']}")
    for notice in report["market_notices"]:
        lines += ["", f"Market notice ({notice['platform']}): {notice['notice']} Source: {notice['sources'][0]['url']}"]
    lines += ["", "Hypotheses to check:"]
    lines += [f"  - {a['hypothesis']} Check: {a['check']}" for a in report["advice"]]
    lines += ["", "Read-only: database opened read-only; no network, queue or publishing operations."]
    return "\n".join(lines)


# ---- CLI -----------------------------------------------------------------------------------------------------

def add_parser(sub) -> None:
    p = sub.add_parser("platform-risk", help="Read-only multi-homing report: share of your recorded reach per "
                       "platform, concentration (HHI), sourced platform risk flags and hypotheses; never sends")
    p.add_argument("--account", metavar="ID", help="Only this account")
    p.add_argument("--since", metavar="WHEN", help="Only posts sent since 30d (days ago), YYYY-MM-DD or an ISO time")
    p.add_argument("--metric", choices=METRICS, default="views", help="Reach counter (default views)")
    p.add_argument("--age-hours", type=int, default=168, help="Matched post age (24..720 hours, default 168)")
    p.add_argument("--tolerance-hours", type=int, default=48, help="Snapshot age tolerance (1..168 hours, default 48)")
    p.add_argument("--owned", action="append", default=[], metavar="ID",
                   help="Treat this platform or account id as an owned channel (your statement); repeat")
    p.add_argument("--owned-reach", action="append", default=[], metavar="NAME=N",
                   help="Reach of an owned channel in the same window, e.g. newsletter=1200 (your own number); repeat")
    p.add_argument("--owned-posts", action="append", default=[], metavar="NAME=N",
                   help="Items published on an owned channel in the window, e.g. site=4; repeat")
    p.add_argument("--market", action="append", default=[], metavar="MARKET",
                   help="Audience market for market-specific flags: US, global or PLATFORM@MARKET (tiktok@US); repeat")
    p.add_argument("--site", metavar="URL", help="Your owned site (https), for the canonical-home hint (social-web-plan)")
    p.add_argument("--setup", type=Path, metavar="FILE", help="social-web-plan setup JSON, for the canonical-home hint")
    p.add_argument("--now", help="Reference time for --since 30d (ISO 8601 with timezone; default now)")
    p.add_argument("--json", action="store_true", help="Machine-readable report (kind reachweaver.platform-risk v1)")


def run_cli(args, accounts: Mapping[str, Mapping] | None) -> int:
    """Dispatch for ``platform-risk``: database mode=ro only; never creates, migrates or writes anything."""
    from . import social_web
    from .insights import parse_since
    from .storage import connect_readonly, database_path, parse_utc
    registry = load_registry()
    now = datetime.fromisoformat(parse_utc(args.now)) if args.now else datetime.now(timezone.utc)
    since = parse_since(args.since, now) if args.since else None
    markets = [parse_market(m, registry) for m in args.market]
    owned_reach = _owned_pairs(args.owned_reach, "--owned-reach")
    owned_posts = _owned_pairs(args.owned_posts, "--owned-posts")
    home = None
    if args.site or args.setup:
        plan = social_web.build_plan(accounts or {}, setup=social_web.load_setup(args.setup), site_url=args.site)
        home = plan["canonical_home"]
    database = database_path(args.db)
    if not database.exists():
        report: dict = {"kind": KIND, "schema_version": SCHEMA_VERSION, "status": "no_database",
                        "database": str(database), "database_written": False}
    else:
        with connect_readonly(database) as db:
            report = analyze(db, accounts=accounts, account=args.account, since=since, metric=args.metric,
                             age_hours=args.age_hours, tolerance_hours=args.tolerance_hours, owned=args.owned,
                             owned_reach=owned_reach, owned_posts=owned_posts, markets=markets,
                             canonical_home=home, now=now, registry=registry)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return 0
