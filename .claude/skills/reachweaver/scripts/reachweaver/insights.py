"""``insights``: "What works for you" -- read-only comparisons over the owner's OWN published posts.

Question answered: among your published posts, which simple, observable features (time of day, weekday,
length, link, image/video, hashtag count, opening hook, individual hashtags) went together with a higher
or lower result at the same post age? Every answer is an observation about your past posts, never a cause
and never a promise: the lines it prints are hypotheses to test with the ``experiment-*`` commands.

Method (see docs/INSIGHTS.md):

* Unit of analysis: one cohort = one account x one platform x one exact snapshot ``source`` label.
  Accounts are never pooled, and snapshots of different sources are never mixed (each source counts
  differently). By default the source with the most matched posts is used; ``--source`` picks one.
* Outcome: the value at a matched post age, read with ``baseline_eval.matched_snapshot`` (the same
  nearest-snapshot rule as ``baseline-eval`` and ``timing-evaluate``: exact counts only, windowed
  snapshots skipped, never summed, missing is unknown and the post is excluded, never 0). The default
  metric is ``interactions`` = likes + replies + reposts (all three exact in the same snapshot), because
  those are the counters every metric path records, including the public Bluesky/Mastodon counters of
  ``metrics-fetch``, which expose no views. ``--metric``/``--denominator`` select any stored count or a
  rate such as clicks per impression.
* Effect: per feature value, the median of that group against the median of the rest of the cohort,
  with counts. ``too little data`` when either side has fewer than ``min_posts`` (default 5) posts;
  otherwise ``observation``. An optional percentile bootstrap interval (``baseline_eval
  .bootstrap_independent``, fixed seed, deterministic) is approximate and not corrected for the many
  comparisons made.

The database is only read (``SELECT``): nothing here drafts, approves, schedules, publishes, writes a
file or contacts a network.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import results
from .baseline_eval import bootstrap_independent, matched_snapshot, quantile
from .policy import PLATFORMS, ValidationError
from .storage import METRIC_FIELDS, is_manual, measured_posts, parse_utc
from .timing_analysis import DAYPARTS, post_format

KIND = "reachweaver.insights"
SCHEMA_VERSION = 1
INTERACTIONS = ("likes", "replies", "reposts")
METRICS = ("interactions",) + METRIC_FIELDS
DENOMINATORS = ("views", "impressions")
MIN_POSTS_DEFAULT = 5
STRENGTH_TOO_LITTLE = "too little data"
STRENGTH_OBSERVATION = "observation"
MAX_TAGS = 20
TOP_LINES = 3
OVERLAP_SHARE = 0.8  # a group with this share of its posts inside an earlier line's group is folded into it
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SINCE_DAYS = re.compile(r"^([1-9][0-9]{0,3})d$")
_SINCE_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_HASHTAG = re.compile(r"(?<![\w#&/])#([^\W\d_][\w-]{0,49})")
_SENTENCE_END = re.compile(r"[.!?\n]")
_NUMBER_START = re.compile(r"^[^\w]*\d", re.UNICODE)
_LENGTH_RANGES = {"short": "up to 100 characters", "medium": "101-280 characters", "long": "over 280 characters"}

# Feature id -> (label, one-line description of the groups). Order is the report order.
FEATURES: dict[str, tuple[str, str]] = {
    "time_of_day": ("Time of day", "6-hour block of the send time in --timezone"),
    "weekday": ("Weekday", "day of the week of the send time in --timezone"),
    "day_type": ("Weekday or weekend", "Mon-Fri vs Sat-Sun in --timezone"),
    "length": ("Text length", "short <= 100 characters, medium 101-280, long > 280 (whole post text)"),
    "link": ("Link", "the text contains an http(s) link or not"),
    "format": ("Format", "text, image or video, from the stored attachment columns"),
    "hashtag_count": ("Hashtag count", "0, 1-2 or 3+ hashtags in the text"),
    "hook": ("Opening hook", "first sentence ends with '?' = question; else starts with a digit = number; "
                             "else statement"),
    "tag": ("Hashtag", "each hashtag (lowercased) vs posts without it; the 20 most used are shown"),
}
LIMITS = (
    "Observational: you chose what to post and when, nothing was randomly assigned. A difference can come "
    "from topic, audience growth, promotion, news, platform changes or chance, not from the feature itself.",
    "Features are confounded with each other (for example long posts may also carry links); each comparison "
    "looks at one feature at a time and does not adjust for the others.",
    "Small samples: medians of a few posts move in coarse steps. Many comparisons are made, so some "
    "differences will look large by chance alone; bootstrap intervals are approximate and not corrected "
    "for that.",
    "Only posts with an exact result at the matched age from one source are used. Missing is unknown, "
    "never zero; excluded posts are counted.",
    "send_started_at is when the send started, not a guaranteed publication time. For a post you published by "
    "hand (mark-shared --url) it is the time you declared.",
    "Read-only: this command cannot approve, schedule or publish anything and changes no plan.",
)
HOW_TO_GET_DATA = (
    "Bluesky/Mastodon public counters: the owner runs 'reachweaver metrics-fetch --online' (read-only GETs) "
    "a few days and again about a week after posting.",
    "Native analytics export: 'reachweaver import-metrics --csv FILE' (columns post_id, observed_at, source, "
    "metric columns).",
    "One post by hand, from the app's insights: 'reachweaver metrics POST_ID --likes N --replies N --reposts N' "
    "(recorded now, source 'entered-by-hand').",
    "Record a result near the matched age (default 168 h = 7 days after posting, within 24 h) for at least "
    "10 posts per account; then run 'reachweaver insights' again.",
)
DISCLAIMER = ("What went together with better or worse results in your own past posts. Not causal, not a "
              "platform algorithm, not a promise of future reach.")


# --- Inputs -----------------------------------------------------------------------------------

def _zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("--timezone must be an IANA time zone such as Europe/Rome or UTC")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Unknown IANA time zone for --timezone") from exc


def parse_since(value: str | None, now: datetime) -> datetime | None:
    """``30d`` (days before ``now``), ``YYYY-MM-DD`` (00:00 UTC) or an ISO 8601 time with a timezone."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("--since must be like 30d, 2026-10-01 or 2026-10-01T00:00:00Z")
    text = value.strip()
    days = _SINCE_DAYS.fullmatch(text)
    if days:
        return now - timedelta(days=int(days.group(1)))
    if _SINCE_DATE.fullmatch(text):
        try:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValidationError("--since is not a valid calendar date") from exc
    try:
        return datetime.fromisoformat(parse_utc(text))
    except ValidationError as exc:
        raise ValidationError("--since must be like 30d, 2026-10-01 or 2026-10-01T00:00:00Z") from exc


def _int_in(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{name} must be an integer in [{low}, {high}]")
    return value


# --- Features (pure, deterministic) -------------------------------------------------------------

def hashtags(text: str) -> list[str]:
    """Distinct lowercased hashtags (``#name``) in first-seen order; URLs are removed first."""
    seen: list[str] = []
    for match in _HASHTAG.finditer(_URL.sub(" ", text or "")):
        tag = "#" + match.group(1).lower()
        if tag not in seen:
            seen.append(tag)
    return seen


def hook_type(text: str) -> str:
    """``question`` / ``number`` / ``statement`` from the first sentence (simple, documented rules)."""
    stripped = (text or "").strip()
    end = _SENTENCE_END.search(stripped)
    first = stripped[:end.end()] if end else stripped
    if first.rstrip().endswith("?"):
        return "question"
    if _NUMBER_START.match(first):
        return "number"
    return "statement"


def length_bucket(text: str) -> str:
    size = len(text or "")
    return "short" if size <= 100 else "medium" if size <= 280 else "long"


def hashtag_bucket(count: int) -> str:
    return "0" if count == 0 else "1-2" if count <= 2 else "3+"


def post_features(post: sqlite3.Row, local: datetime) -> dict[str, list[str]]:
    """Every feature value of one published post; ``tag`` may hold several values (or none)."""
    text = post["content"] or ""
    tags = hashtags(text)
    return {
        "time_of_day": [DAYPARTS[local.hour // 6]],
        "weekday": [_WEEKDAYS[local.weekday()]],
        "day_type": ["weekend" if local.weekday() >= 5 else "weekday"],
        "length": [length_bucket(text)],
        "link": ["with link" if _URL.search(text) else "no link"],
        "format": [post_format(post)],
        "hashtag_count": [hashtag_bucket(len(tags))],
        "hook": [hook_type(text)],
        "tag": tags,
    }


def describe_group(feature: str, value: str, timezone_name: str) -> tuple[str, str]:
    """(plain words for the group, plain words for the rest)."""
    if feature == "time_of_day":
        return f"posts sent {value} ({timezone_name})", "posts sent at other times"
    if feature == "weekday":
        return f"posts sent on {value}", "posts on other days"
    if feature == "day_type":
        return f"{value} posts", "weekend posts" if value == "weekday" else "weekday posts"
    if feature == "length":
        return f"{value} posts ({_LENGTH_RANGES.get(value, '?')})", "posts of other lengths"
    if feature == "link":
        return ("posts with a link", "posts without a link") if value == "with link" else (
            "posts without a link", "posts with a link")
    if feature == "format":
        return f"{value} posts", "posts in other formats"
    if feature == "hashtag_count":
        return f"posts with {value} hashtags", "posts with a different number of hashtags"
    if feature == "hook":
        return f"posts opening with a {value}", "posts with other openings"
    return f"posts tagged {value}", f"posts without {value}"


# --- Outcome ----------------------------------------------------------------------------------

def _measure(metric: str, denominator: str | None) -> tuple[Callable[[sqlite3.Row], bool], Callable[[sqlite3.Row], float]]:
    parts = INTERACTIONS if metric == "interactions" else (metric,)

    def usable(row: sqlite3.Row) -> bool:
        if any(row[p] is None for p in parts):
            return False
        return denominator is None or (row[denominator] is not None and row[denominator] > 0)

    def value(row: sqlite3.Row) -> float:
        total = float(sum(row[p] for p in parts))
        return total / row[denominator] if denominator else total

    return usable, value


def _sent(post: sqlite3.Row) -> datetime | None:
    try:
        return datetime.fromisoformat(parse_utc(post["send_started_at"]))
    except (ValidationError, ValueError, TypeError, AttributeError):
        return None


def _sources(con: sqlite3.Connection, post_ids: list[str]) -> list[str]:
    found: set[str] = set()
    for start in range(0, len(post_ids), 500):
        chunk = post_ids[start:start + 500]
        found.update(row[0] for row in con.execute(
            f"SELECT DISTINCT source FROM metric_snapshots WHERE post_id IN ({','.join('?' for _ in chunk)})",
            chunk))
    return sorted(found)


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "p25": None, "p75": None}
    return {"n": len(values), "median": _round(median(values)), "p25": _round(quantile(values, 0.25)),
            "p75": _round(quantile(values, 0.75))}


def _rng(seed: int, cohort: str, feature: str, value: str) -> random.Random:
    # String seeds are hashed with SHA-512 (random.seed version 2): stable across runs and PYTHONHASHSEED.
    return random.Random(f"reachweaver-insights:{seed}:{cohort}:{feature}:{value}")


def _compare(rows: list[dict], feature: str, value: str, *, min_posts: int, resamples: int, confidence: float,
             seed: int, cohort_key: str, timezone_name: str) -> dict[str, Any]:
    group = [r["value"] for r in rows if value in r["features"][feature]]
    rest = [r["value"] for r in rows if value not in r["features"][feature]]
    label, rest_label = describe_group(feature, value, timezone_name)
    item: dict[str, Any] = {
        "value": value, "label": label, "rest_label": rest_label,
        "n": len(group), "median": _round(median(group)) if group else None,
        "rest_n": len(rest), "rest_median": _round(median(rest)) if rest else None,
        "difference": None, "relative_difference": None, "interval": None,
        "strength": STRENGTH_TOO_LITTLE,
    }
    if len(group) < min_posts or len(rest) < min_posts:
        return item
    item["strength"] = STRENGTH_OBSERVATION
    base = median(rest)
    item["difference"] = _round(median(group) - base)
    item["relative_difference"] = _round(median(group) / base - 1, 4) if base > 0 else None
    if resamples:
        boot = bootstrap_independent(group, rest, resamples=resamples, confidence=confidence,
                                     rng=_rng(seed, cohort_key, feature, value))
        low, high = boot["difference"]
        item["interval"] = {"confidence": confidence, "difference": [_round(low), _round(high)],
                            "excludes_zero": low > 0 or high < 0, "method": "percentile_bootstrap"}
    return item


def _feature_values(rows: list[dict], feature: str) -> list[str]:
    counts: Counter[str] = Counter(v for r in rows for v in r["features"][feature])
    if feature == "time_of_day":
        return [d for d in DAYPARTS if d in counts]
    if feature == "weekday":
        return [d for d in _WEEKDAYS if d in counts]
    order = {"day_type": ("weekday", "weekend"), "length": ("short", "medium", "long"),
             "link": ("with link", "no link"), "format": ("text", "image", "video", "unknown"),
             "hashtag_count": ("0", "1-2", "3+"), "hook": ("question", "number", "statement")}
    if feature in order:
        return [v for v in order[feature] if v in counts]
    return [tag for tag, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TAGS]]


def _unit(metric: str, denominator: str | None) -> str:
    name = "interactions (likes + replies + reposts)" if metric == "interactions" else metric
    return f"{name} per {denominator[:-1] if denominator.endswith('s') else denominator}" if denominator else name


def _fmt(value: float | None, denominator: str | None) -> str:
    if value is None:
        return "unknown"
    if denominator:
        return f"{value:.2%}"
    return f"{value:g}" if value == int(value) else f"{value:.1f}"


def _members(rows: list[dict], feature: str, value: str) -> frozenset[str]:
    return frozenset(r["post_id"] for r in rows if value in r["features"][feature])


def _suggestions(cohort: dict[str, Any], rows: list[dict], *, denominator: str | None,
                 limit: int) -> list[dict[str, Any]]:
    candidates = []
    for feature in cohort["features"]:
        for group in feature["groups"]:
            if group["strength"] != STRENGTH_OBSERVATION or not group["difference"]:
                continue
            size = abs(group["relative_difference"]) if group["relative_difference"] is not None else float("inf")
            excludes = bool(group["interval"] and group["interval"]["excludes_zero"])
            candidates.append((not excludes, -size, -group["n"], feature["feature"], group["value"], feature, group))
    candidates.sort(key=lambda c: c[:5])
    lines: list[dict[str, Any]] = []
    chosen: list[frozenset[str]] = []
    used: set[str] = set()
    for *_, feature, group in candidates:
        if feature["feature"] in used:  # one line per feature: "with link" and "no link" are one finding
            continue
        members = _members(rows, feature["feature"], group["value"])
        overlap = next((i for i, other in enumerate(chosen)
                        if len(members & other) >= OVERLAP_SHARE * len(members)), None)
        if overlap is not None:  # mostly posts of an earlier line's group: one finding, not two
            lines[overlap]["overlaps_with"].append({"feature": feature["feature"], "value": group["value"],
                                                    "label": group["label"], "n": group["n"],
                                                    "shared": len(members & chosen[overlap])})
            continue
        shares = [{"line": i + 1, "shared": len(members & other)} for i, other in enumerate(chosen)
                  if len(members & other) * 2 >= len(members)]
        used.add(feature["feature"])
        chosen.append(members)
        direction = "more" if group["difference"] > 0 else "less"
        rel = group["relative_difference"]
        size = f" ({rel:+.0%})" if rel is not None else ""
        text = (f"Try {direction} of: {group['label']} -- median {_fmt(group['median'], denominator)} "
                f"(n={group['n']}) vs {_fmt(group['rest_median'], denominator)} for {group['rest_label']} "
                f"(n={group['rest_n']}){size}. A hypothesis to test, not a cause.")
        lines.append({
            "account_id": cohort["account_id"], "platform": cohort["platform"], "direction": direction,
            "feature": feature["feature"], "value": group["value"], "label": group["label"],
            "rest_label": group["rest_label"], "n": group["n"], "median": group["median"],
            "rest_n": group["rest_n"], "rest_median": group["rest_median"], "relative_difference": rel,
            "interval": group["interval"], "strength": group["strength"], "text": text, "overlaps_with": [],
            "shares_posts_with_lines": shares,
            "test_with": ("Write a growth plan that alternates the two variants (examples/growth-plan.example.json), "
                          "then 'experiment-preregister PLAN --out RECORD' before posting and "
                          "'experiment-readout' afterwards; 'experiment-simulate' shows how many pairs you need."),
        })
        if len(lines) >= limit:
            break
    return lines


# --- Analysis ---------------------------------------------------------------------------------

def analyze(con: sqlite3.Connection, *, account_id: str | None = None, platform: str | None = None,
            since: str | None = None, now: datetime | None = None, timezone_name: str = "UTC",
            source: str | None = None, metric: str = "interactions", denominator: str | None = None,
            age_hours: int = 168, tolerance_hours: int = 24, min_posts: int = MIN_POSTS_DEFAULT,
            resamples: int = 1000, confidence: float = 0.95, seed: int = 21,
            limit: int = TOP_LINES) -> dict[str, Any]:
    """The full ``insights`` report (schema ``reachweaver.insights`` v1). Reads ``con``; never writes it."""
    return _analyze(con, account_id=account_id, platform=platform, since=since, now=now,
                    timezone_name=timezone_name, source=source, metric=metric, denominator=denominator,
                    age_hours=age_hours, tolerance_hours=tolerance_hours, min_posts=min_posts,
                    resamples=resamples, confidence=confidence, seed=seed, limit=limit)[0]


def _analyze(con: sqlite3.Connection, *, account_id: str | None, platform: str | None, since: str | None,
             now: datetime | None, timezone_name: str, source: str | None, metric: str, denominator: str | None,
             age_hours: int, tolerance_hours: int, min_posts: int, resamples: int, confidence: float, seed: int,
             limit: int) -> tuple[dict[str, Any], dict[tuple[str, str], list[dict]]]:
    """``analyze`` plus each cohort's per-post rows (kept out of the report), for the learning loops below."""
    if account_id is not None and (not isinstance(account_id, str) or not account_id.strip()):
        raise ValidationError("--account must be a nonempty account id")
    if platform is not None and platform not in PLATFORMS:
        raise ValidationError("Unknown --platform")
    if metric not in METRICS:
        raise ValidationError(f"--metric must be one of {', '.join(METRICS)}")
    if denominator is not None and (denominator not in DENOMINATORS or denominator == metric):
        raise ValidationError("--denominator must be views or impressions and differ from --metric")
    if source is not None and (not isinstance(source, str) or not 1 <= len(source.strip()) <= 100):
        raise ValidationError("--source must be the exact snapshot source label (1..100 characters)")
    _int_in(age_hours, "--age-hours", 24, 720)
    if type(tolerance_hours) is not int or not 1 <= tolerance_hours < age_hours or tolerance_hours > 168:
        raise ValidationError("--tolerance-hours must be 1..168 and less than --age-hours")
    _int_in(min_posts, "--min-posts", 3, 100)
    if type(resamples) is not int or not (resamples == 0 or 200 <= resamples <= 20000):
        raise ValidationError("--resamples must be 0 (no interval) or 200..20000")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.5 <= confidence < 1:
        raise ValidationError("--confidence must be in [0.5, 1)")
    _int_in(limit, "limit", 1, 10)
    zone = _zone(timezone_name)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValidationError("now must include a timezone")
    start = parse_since(since, current)
    usable, value_of = _measure(metric, denominator)

    # Sent by ReachWeaver, plus posts the owner published by hand and recorded with mark-shared --url (labelled).
    posts = measured_posts(con, account_id=account_id, platform=platform, include_replies=False)
    by_cohort: dict[tuple[str, str], list[tuple[sqlite3.Row, datetime]]] = defaultdict(list)
    skipped_global: Counter[str] = Counter()
    for post in posts:
        sent = _sent(post)
        if sent is None:
            skipped_global["missing_or_invalid_send_time"] += 1
            continue
        if start is not None and sent < start:
            continue
        by_cohort[(post["account_id"], post["platform"])].append((post, sent))

    cohorts: list[dict[str, Any]] = []
    kept: dict[tuple[str, str], list[dict]] = {}
    for (account, plat) in sorted(by_cohort):
        members = by_cohort[(account, plat)]
        candidates = [source] if source is not None else _sources(con, [p["id"] for p, _ in members])
        best: tuple[str | None, list[dict], Counter[str]] = (None, [], Counter())
        coverage: dict[str, int] = {}
        for label in candidates:
            skipped: Counter[str] = Counter()
            rows = []
            for post, sent in members:
                snap = matched_snapshot(con, post["id"], sent, source=label, age_hours=age_hours,
                                        tolerance_hours=tolerance_hours, usable=usable, skipped=skipped)
                if snap is None:
                    skipped["no_result_at_matched_age"] += 1
                    continue
                rows.append({"post_id": post["id"], "value": value_of(snap),
                             "features": post_features(post, sent.astimezone(zone))})
            coverage[label] = len(rows)
            if best[0] is None or len(rows) > len(best[1]):
                best = (label, rows, skipped)
        chosen, rows, skipped = best
        if chosen is None:
            skipped = Counter({"no_result_at_matched_age": len(members)})
        cohort_key = f"{account}:{plat}:{chosen}"
        features = []
        for feature, (label, description) in FEATURES.items():
            groups = [_compare(rows, feature, v, min_posts=min_posts, resamples=resamples, confidence=confidence,
                               seed=seed, cohort_key=cohort_key, timezone_name=zone.key)
                      for v in _feature_values(rows, feature)]
            features.append({"feature": feature, "label": label, "groups_by": description, "groups": groups})
        comparable = len(rows) >= 2 * min_posts
        cohort: dict[str, Any] = {
            "account_id": account, "platform": plat, "source": chosen,
            "other_sources": {k: v for k, v in sorted(coverage.items()) if k != chosen},
            "published_in_window": len(members), "posted_by_hand": sum(1 for p, _ in members if is_manual(p)),
            "posts_with_result": len(rows),
            "excluded": dict(sorted(skipped.items())),
            "status": "ok" if comparable else "not_enough_data",
            "overall": _summary([r["value"] for r in rows]),
            "features": features,
        }
        cohort["suggestions"] = _suggestions(cohort, rows, denominator=denominator, limit=limit) if comparable else []
        matched_ids = {r["post_id"] for r in rows}
        early = results.early_note([(sent, results.snapshot_ages(con, post["id"], sent)) for post, sent in members
                                    if post["id"] not in matched_ids], current,
                                   age_hours=age_hours, tolerance_hours=tolerance_hours, tz=zone)
        if early is not None:  # key only when some posts have results, but all from before the matched age
            cohort["results_at_other_ages"] = early
        cohorts.append(cohort)
        kept[(account, plat)] = rows

    status = "ok" if any(c["status"] == "ok" for c in cohorts) else "not_enough_data"
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "status": status,
        "filters": {"account": account_id, "platform": platform, "since": since,
                    "since_utc": start.isoformat(timespec="seconds") if start else None,
                    "now": current.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "timezone": zone.key, "source": source},
        "method": {"metric": metric, "denominator": denominator, "unit": _unit(metric, denominator),
                   "post_age_hours": age_hours, "age_tolerance_hours": tolerance_hours,
                   "min_posts_per_group": min_posts, "comparison": "median of group vs median of the rest",
                   "strength_labels": [STRENGTH_TOO_LITTLE, STRENGTH_OBSERVATION],
                   "interval": ({"method": "percentile_bootstrap", "resamples": resamples, "confidence": confidence,
                                 "seed": seed} if resamples else None)},
        "excluded": dict(sorted(skipped_global.items())),
        "cohorts": cohorts,
        "suggestions": [line for c in cohorts for line in c["suggestions"]],
        "how_to_get_data": list(HOW_TO_GET_DATA) if status != "ok" else [],
        "limits": list(LIMITS),
        "disclaimer": DISCLAIMER,
    }, kept


def top_insights(con: sqlite3.Connection, *, account_id: str | None = None, platform: str | None = None,
                 since: str | None = None, now: datetime | None = None, timezone_name: str = "UTC",
                 limit: int = TOP_LINES) -> list[dict[str, Any]]:
    """Up to ``limit`` "try more / try less" lines per account, for other views (start, context, ui).

    Same defaults as ``insights`` but without bootstrap intervals (fast). Returns ``[]`` when there is not
    enough data; never raises for lack of data and never writes. Each line carries ``text``, ``n`` and the
    comparison, and is a hypothesis to test, not a cause.
    """
    report = analyze(con, account_id=account_id, platform=platform, since=since, now=now,
                     timezone_name=timezone_name, resamples=0, limit=limit)
    return report["suggestions"]


# --- Learning loops: the same matched-age rows, read by other commands -------------------------
#
# discovery-plan, calendar-plan and followup-suggest show the owner's own results next to their fixed
# rules with these helpers. Same defaults as ``insights`` (interactions at 168 h +/- 24 h, the source with
# the most matched posts, groups of at least 5), no bootstrap, SELECT only. They return evidence to show;
# none of them changes a score, a slot, an approval or a schedule (docs/INSIGHTS.md "Where your results
# are used").

LOOP_LABEL = "observation from your own published posts; not a cause, not a forecast"
LOOP_NOTE = ("Your own results at the same post age (about 7 days), one account at a time. An observation, "
             "not a cause and not a forecast: nothing was re-ranked, scheduled or changed because of it.")
_EVIDENCE_KEYS = ("n", "median", "rest_n", "rest_median", "difference", "relative_difference", "strength")


def _quick(con: sqlite3.Connection, *, account_id: str | None, platform: str | None, now: datetime | None,
           timezone_name: str = "UTC") -> tuple[dict[str, Any], dict[tuple[str, str], list[dict]]]:
    return _analyze(con, account_id=account_id, platform=platform, since=None, now=now,
                    timezone_name=timezone_name, source=None, metric="interactions", denominator=None,
                    age_hours=168, tolerance_hours=24, min_posts=MIN_POSTS_DEFAULT, resamples=0,
                    confidence=0.95, seed=21, limit=TOP_LINES)


def evidence_text(item: dict[str, Any]) -> str:
    """One short line for a group: ``n=6, median 12 vs 8 for the rest (+50%), observation``."""
    if not item.get("n"):
        return "not in your posts with a result yet"
    text = f"n={item['n']}, median {_fmt(item['median'], None)} vs {_fmt(item['rest_median'], None)} for the rest"
    if item["strength"] != STRENGTH_OBSERVATION:
        return text + f" ({STRENGTH_TOO_LITTLE})"
    rel = item.get("relative_difference")
    return text + (f" ({rel:+.0%})" if rel is not None else "") + f", {STRENGTH_OBSERVATION}"


def _cohort_head(cohort: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": cohort["account_id"], "platform": cohort["platform"], "source": cohort["source"],
            "posts_with_result": cohort["posts_with_result"], "median": cohort["overall"]["median"]}


def tag_evidence(con: sqlite3.Connection, tags: list[str], *, platform: str, account_id: str | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
    """For each tag, how this account's own posts carrying it did against its posts without it.

    Tags match the lowercased hashtags of the post text (``#OpenSource`` = ``#opensource``). Every account on
    ``platform`` (or only ``account_id``) is its own cohort. Reads only; never changes any score.
    """
    base = {"label": LOOP_LABEL, "unit": _unit("interactions", None), "post_age_hours": 168,
            "min_posts_per_group": MIN_POSTS_DEFAULT, "note": LOOP_NOTE}
    if platform not in PLATFORMS:
        return {**base, "status": "no_results", "cohorts": []}
    report, rows_by = _quick(con, account_id=account_id, platform=platform, now=now)
    keys = [(tag, "#" + tag.lstrip("#").lower()) for tag in tags]
    cohorts = []
    for cohort in report["cohorts"]:
        rows = rows_by.get((cohort["account_id"], cohort["platform"]), [])
        items = []
        for shown, key in keys:
            group = _compare(rows, "tag", key, min_posts=MIN_POSTS_DEFAULT, resamples=0, confidence=0.95, seed=21,
                             cohort_key="", timezone_name="UTC")
            item = {"tag": shown, **{k: group[k] for k in _EVIDENCE_KEYS}}
            item["text"] = evidence_text(item)
            items.append(item)
        cohorts.append({**_cohort_head(cohort), "tags": items})
    status = "ok" if any(c["posts_with_result"] for c in cohorts) else "no_results"
    return {**base, "status": status, "cohorts": cohorts}


def time_evidence(con: sqlite3.Connection, *, account_id: str, platform: str, timezone_name: str,
                  slots: list[tuple[int, Any]] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """This account's own results by 6-hour block and weekday (in ``timezone_name``), plus the block each
    ``(weekday, time)`` slot falls in. Reads only; the caller's slots are never changed."""
    base: dict[str, Any] = {"label": LOOP_LABEL, "account_id": account_id, "platform": platform,
                            "timezone": timezone_name, "unit": _unit("interactions", None), "post_age_hours": 168,
                            "min_posts_per_group": MIN_POSTS_DEFAULT, "note": LOOP_NOTE}
    if platform not in PLATFORMS:
        return {**base, "status": "no_results", "posts_with_result": 0}
    report, _ = _quick(con, account_id=account_id, platform=platform, now=now, timezone_name=timezone_name)
    cohort = next((c for c in report["cohorts"] if c["platform"] == platform), None)
    if cohort is None or not cohort["posts_with_result"]:
        return {**base, "status": "no_results", "posts_with_result": 0}
    groups = {f["feature"]: f["groups"] for f in cohort["features"]}

    def rows(feature: str, key: str) -> list[dict[str, Any]]:
        out = []
        for group in groups[feature]:
            item = {key: group["value"], **{k: group[k] for k in _EVIDENCE_KEYS}}
            item["text"] = evidence_text(item)
            out.append(item)
        return out

    dayparts, weekdays = rows("time_of_day", "block"), rows("weekday", "weekday")
    by_block = {d["block"]: d for d in dayparts}
    compared = []
    for weekday, clock in slots or []:
        block = DAYPARTS[clock.hour // 6]
        mine = by_block.get(block)
        compared.append({"slot": f"{_WEEKDAYS[weekday]}@{clock.strftime('%H:%M')}", "block": block,
                         "your_result": mine["text"] if mine else "no posts of yours in this block yet"})
    return {**base, "status": "ok" if cohort["status"] == "ok" else "too_little_data",
            "source": cohort["source"], "posts_with_result": cohort["posts_with_result"],
            "median": cohort["overall"]["median"], "blocks": dayparts, "weekdays": weekdays,
            "better_blocks": [d["block"] for d in dayparts
                              if d["strength"] == STRENGTH_OBSERVATION and (d["difference"] or 0) > 0],
            "better_weekdays": [d["weekday"] for d in weekdays
                                if d["strength"] == STRENGTH_OBSERVATION and (d["difference"] or 0) > 0],
            "fixed_slots": compared}


def typical_results(con: sqlite3.Connection, *, account_id: str | None = None,
                    now: datetime | None = None) -> list[dict[str, Any]]:
    """Per account x platform: posts with a matched-age result, their median and the source used. Reads only."""
    report, _ = _quick(con, account_id=account_id, platform=None, now=now)
    return [{**_cohort_head(c), "published": c["published_in_window"]} for c in report["cohorts"]]


# --- Output -----------------------------------------------------------------------------------

def render_text(report: dict[str, Any]) -> str:
    method = report["method"]
    denominator = method["denominator"]
    lines = ["What works for you (your own published posts; observations, not causes)",
             f"Result: {method['unit']} about {method['post_age_hours']} h after posting "
             f"(+/- {method['age_tolerance_hours']} h); times in {report['filters']['timezone']}."]
    if not report["cohorts"]:
        lines += ["", "No published posts match these filters yet."]
    for cohort in report["cohorts"]:
        lines += ["", f"{results.account_label(cohort['account_id'])} ({cohort['platform']}): {cohort['posts_with_result']} of "
                      f"{cohort['published_in_window']} published posts have a result"
                      + (f" ({cohort['posted_by_hand']} of them posted by hand)" if cohort.get("posted_by_hand") else "")
                      + (f" from source '{cohort['source']}'" if cohort["source"] else "")
                      + (f"; median {_fmt(cohort['overall']['median'], denominator)}" if cohort["overall"]["n"] else "")
                      + "."]
        if cohort.get("results_at_other_ages"):
            lines.append("  " + results.early_note_text(cohort["results_at_other_ages"]))
        if cohort["status"] != "ok":
            lines.append(f"  Too little data to compare: at least {2 * method['min_posts_per_group']} posts with a "
                         f"result are needed (groups of {method['min_posts_per_group']}).")
            continue
        if not cohort["suggestions"]:
            lines.append(f"  No group of {method['min_posts_per_group']}+ posts differs from the rest yet.")
        for number, line in enumerate(cohort["suggestions"], 1):
            lines.append(f"  {number}. {line['text']}")
            if line["overlaps_with"]:
                lines.append("     Not a separate finding (their posts are mostly in this group): " + ", ".join(
                    f"{o['label']} ({o['shared']} of {o['n']})" for o in line["overlaps_with"]))
            for share in line["shares_posts_with_lines"]:
                lines.append(f"     {share['shared']} of these {line['n']} posts are also in line {share['line']}.")
    if report["status"] != "ok":
        lines += ["", "Not enough results yet. How to get them:"] + [f"  - {item}" for item in report["how_to_get_data"]]
    else:
        lines += ["", "Test a line before acting on it: experiment-preregister, then experiment-readout "
                      "(experiment-simulate shows how many pairs you need). 'insights --json' has every group."]
    lines += ["", "Limits: correlation only; features overlap; small samples and many comparisons produce "
                  "chance differences. Read-only: nothing was changed or sent."]
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------------------------

def add_parser(sub) -> None:
    """``insights`` (called from ``cli_parser.make_parser``)."""
    p = sub.add_parser("insights", help="Read-only 'What works for you': compares your own published posts' "
                       "matched-age results by time, weekday, length, link, format, hashtags and opening hook; "
                       "observations to test, never causes")
    p.add_argument("--account", metavar="ID", help="Only this account (each account is always analysed apart)")
    p.add_argument("--platform", choices=PLATFORMS, metavar="PLATFORM", help="Only this platform id")
    p.add_argument("--since", metavar="WHEN", help="Only posts sent since 30d (days ago), YYYY-MM-DD or an ISO time")
    p.add_argument("--timezone", default="UTC", help="IANA time zone for time-of-day and weekday groups (default UTC)")
    p.add_argument("--source", help="Exact snapshot source label (default: the one with the most matched posts)")
    p.add_argument("--metric", choices=METRICS, default="interactions",
                   help="Result to compare (default interactions = likes + replies + reposts)")
    p.add_argument("--denominator", choices=DENOMINATORS, help="Compare a rate: --metric per views or impressions")
    p.add_argument("--age-hours", type=int, default=168, help="Matched post age (24..720 hours, default 168)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Snapshot age tolerance (1..168 hours)")
    p.add_argument("--min-posts", type=int, default=MIN_POSTS_DEFAULT,
                   help="Fewer posts than this in a group (or the rest) = 'too little data' (3..100, default 5)")
    p.add_argument("--resamples", type=int, default=1000,
                   help="Bootstrap resamples for the approximate interval; 0 = none (200..20000)")
    p.add_argument("--seed", type=int, default=21, help="Fixed seed: same inputs give identical output")
    p.add_argument("--now", help="Reference time for --since 30d (ISO 8601 with timezone; default now)")
    p.add_argument("--json", action="store_true", help="Machine-readable report (schema reachweaver.insights v1)")


def run_cli(args) -> int:
    """Dispatch for ``insights``: database mode=ro only; never creates, migrates or writes anything."""
    from .storage import connect_readonly, database_path, schema_gaps
    now = datetime.fromisoformat(parse_utc(args.now)) if args.now else datetime.now(timezone.utc)
    database = database_path(args.db)
    if not database.exists():
        if args.json:
            print(json.dumps({"kind": KIND, "schema_version": SCHEMA_VERSION, "status": "no_database",
                              "database": str(database), "how_to_get_data": list(HOW_TO_GET_DATA)}, indent=2))
        else:
            print(f"No ReachWeaver database at {database} yet, so there is nothing to learn from.\n"
                  "Publish a few posts, record their results, then run insights again:\n"
                  + "\n".join(f"  - {item}" for item in HOW_TO_GET_DATA))
        return 0
    with connect_readonly(database) as db:
        gaps = schema_gaps(db, ("posts", "metric_snapshots"))
        if gaps:
            raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                  "insights is read-only and never migrates. Run 'reachweaver init' once to "
                                  "upgrade it, then retry")
        report = analyze(db, account_id=args.account, platform=args.platform, since=args.since, now=now,
                         timezone_name=args.timezone, source=args.source, metric=args.metric,
                         denominator=args.denominator, age_hours=args.age_hours,
                         tolerance_hours=args.tolerance_hours, min_posts=args.min_posts,
                         resamples=args.resamples, seed=args.seed)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return 0
