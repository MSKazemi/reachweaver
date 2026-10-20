"""Read-only evaluation against calendar-plan's non-personalized baseline (issue #16).

Question answered: over an explicit audience-local period, did posts sent at the account's
own (personalized) slots, or in named variant campaigns, show different outcome rates than
posts sent at ``calendar-plan``'s generic population-prior slot? Optionally, saved
``experiment-readout --json`` files are added so several experiments are reported together.

What it adds is reporting only:

* matched-age outcome rates from the user's own imported snapshots (exact source, nearest
  snapshot to the target post age, never summed; missing is unknown, never zero);
* a percentile bootstrap interval with a fixed seed (deterministic output);
* a two-sided permutation p-value for baseline comparisons (exact when small);
* Holm-Bonferroni adjustment across every comparison tested in one run;
* explicit explore/exploit roles and a correlational/randomized split;
* ``insufficient_data`` with ``None`` estimates instead of a zero.

It never changes a readout label: the strict sign-test gate in ``growth.classify_result``
stays the only source of ``directional``. The database is only read (``SELECT`` and
``storage.get_post``). Nothing here drafts, approves, schedules, publishes, changes
``calendar-plan`` or contacts a network. No result is causal or proven.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
from collections import Counter
from datetime import date, datetime
from itertools import combinations
from math import comb
from pathlib import Path
from statistics import median
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .attribution import ATTRIBUTION_OUTCOMES, attributed_outcome  # parsing only; no network
from .calendar import generic_baseline_slots
from .growth import MIN_RECOMMENDED_PAIRS, is_windowed_snapshot
from .growth_readout import describe_design
from .policy import PLATFORMS, ValidationError
from .storage import METRIC_FIELDS, get_post, parse_utc, published_posts, snapshots_for
from .inputs import read_json_file

KIND = "reachweaver.baseline-evaluation"
READOUT_KIND = "reachweaver.growth-readout"
ROLES = ("explore", "exploit")
STATUSES = ("insufficient_data", "no_clear_difference", "separated_after_holm")
EXACT_PERMUTATION_LIMIT = 20000
MAX_COMPARISONS = 12
WEEK_MINUTES = 7 * 24 * 60
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SLOT = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)@([01][0-9]|2[0-3]):([0-5][0-9])$", re.I)
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

WARNINGS = (
    "Correlational: posting times and campaigns were chosen by the operator, not randomly assigned. A difference "
    "can come from topic, format, follower growth, feed changes, holidays or promotion.",
    "The baseline is calendar-plan's generic industry prior (low confidence), not a proven default. Posts at that "
    "slot form the comparison group only because of when they were sent.",
    "Percentile bootstrap intervals are approximate and tend to be too narrow in small samples; medians of a few "
    "posts move in coarse steps.",
    "Holm-Bonferroni controls the family-wise error only across the comparisons in this run. Re-running over other "
    "periods or arms after seeing results is itself multiple testing.",
    "Readout labels are copied unchanged from experiment-readout's strict sign-test gate; this report never "
    "upgrades a label.",
    "Missing snapshots are unknown, not zero: excluded posts are counted, and insufficient_data is reported "
    "instead of an estimate.",
    "Choose --since/--until before looking at results; choosing the period after seeing the data inflates false "
    "positives.",
    "Read-only: this command cannot approve, schedule or publish anything and does not change calendar-plan.",
)
ATTRIBUTION_WARNINGS = (
    "Outcome is the attributed count per post from a saved attribution-report (a UTM join over the analytics "
    "export window), not a matched-age snapshot rate: posts sent earlier in the window had longer to collect "
    "visits. Only posts with at least --age-hours inside the window are used.",
    "A post without an exact UTM row is unknown and excluded, never 0: an untagged link and a real absence look "
    "the same. Repository-level signals (stars, issues, pull requests, new contributors) cannot be credited to "
    "a post and are not read.",
)
DISCLAIMER = ("Associational evaluation of one account/platform cohort. Not causal, not proof of a best time, "
              "not a promise of future reach.")


# --- Statistics (pure, deterministic) -------------------------------------------------------

def quantile(values: list[float], q: float) -> float:
    """Linear-interpolation (type 7) quantile of a nonempty list."""
    if not values:
        raise ValidationError("quantile of an empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def holm_bonferroni(p_values: list[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in input order (monotone, capped at 1).

    Sorted ascending, the k-th smallest (k from 0) is multiplied by (m - k); each adjusted
    value is the running maximum, so a larger raw p never gets a smaller adjusted p.
    """
    for p in p_values:
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            raise ValidationError("p-values must be numbers in [0, 1]")
    m = len(p_values)
    order = sorted(range(m), key=lambda i: (p_values[i], i))
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def _median_difference(a: list[float], b: list[float]) -> float:
    return median(a) - median(b)


def _relative_median_difference(a: list[float], b: list[float]) -> float | None:
    base = median(b)
    return median(a) / base - 1 if base > 0 else None


def bootstrap_independent(a: list[float], b: list[float], *, resamples: int, confidence: float,
                          rng: random.Random) -> dict[str, Any]:
    """Percentile bootstrap of median(a) - median(b) and median(a)/median(b) - 1.

    Each group is resampled with replacement independently. The relative interval is None
    when any resample has a zero baseline median (undefined ratio), never a made-up number.
    """
    diffs: list[float] = []
    relatives: list[float] = []
    relative_defined = True
    for _ in range(resamples):
        boot_a, boot_b = rng.choices(a, k=len(a)), rng.choices(b, k=len(b))
        diffs.append(_median_difference(boot_a, boot_b))
        rel = _relative_median_difference(boot_a, boot_b)
        if rel is None:
            relative_defined = False
        elif relative_defined:
            relatives.append(rel)
    tail = (1 - confidence) / 2
    return {"difference": [quantile(diffs, tail), quantile(diffs, 1 - tail)],
            "relative": [quantile(relatives, tail), quantile(relatives, 1 - tail)] if relative_defined else None}


def bootstrap_paired(differences: list[float], *, resamples: int, confidence: float,
                     rng: random.Random) -> list[float]:
    """Percentile bootstrap interval of the median of paired differences (pairs resampled)."""
    draws = [median(rng.choices(differences, k=len(differences))) for _ in range(resamples)]
    tail = (1 - confidence) / 2
    return [quantile(draws, tail), quantile(draws, 1 - tail)]


def permutation_p(a: list[float], b: list[float], *, resamples: int, rng: random.Random) -> tuple[float, str]:
    """Two-sided permutation p-value for |median(a) - median(b)|.

    Exact over every relabelling when there are at most ``EXACT_PERMUTATION_LIMIT`` of them
    (the observed split counts, so p >= 1/total). Otherwise Monte Carlo with the add-one
    correction (count + 1) / (resamples + 1), so p is never 0.
    """
    observed = abs(_median_difference(a, b))
    eps = 1e-12 + 1e-9 * observed
    pooled = list(a) + list(b)
    n = len(a)
    total = comb(len(pooled), n)
    if total <= EXACT_PERMUTATION_LIMIT:
        hits = 0
        indices = range(len(pooled))
        for chosen in combinations(indices, n):
            picked = set(chosen)
            first = [pooled[i] for i in chosen]
            second = [pooled[i] for i in indices if i not in picked]
            if abs(_median_difference(first, second)) >= observed - eps:
                hits += 1
        return hits / total, "exact_permutation"
    hits = 0
    work = list(pooled)
    for _ in range(resamples):
        rng.shuffle(work)
        if abs(_median_difference(work[:n], work[n:])) >= observed - eps:
            hits += 1
    return (hits + 1) / (resamples + 1), "monte_carlo_permutation"


def _rng(seed: int, comparison_id: str, purpose: str) -> random.Random:
    # String seeds are hashed with SHA-512 by random.seed (version 2): stable across runs and
    # independent of PYTHONHASHSEED and of the order in which arms were given.
    return random.Random(f"reachweaver-baseline-eval:{seed}:{comparison_id}:{purpose}")


# --- Input validation -------------------------------------------------------------------------

def _zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Audience IANA timezone required (e.g. Europe/Rome)")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Unknown audience IANA timezone") from exc


def _day(value: object, flag: str) -> date:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise ValidationError(f"{flag} is required as YYYY-MM-DD: the evaluation period must be explicit")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{flag} is not a valid calendar date") from exc


def _int_in(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _role(spec: object, what: str) -> tuple[str, str]:
    if not isinstance(spec, str) or not spec.strip():
        raise ValidationError(f"{what} must be a nonempty string")
    prefix, sep, rest = spec.partition(":")
    if sep and prefix.lower() in ROLES:
        if not rest.strip():
            raise ValidationError(f"{what} {spec!r} names a role but no arm")
        return prefix.lower(), rest.strip()
    return "explore", spec.strip()  # an undeclared arm is exploration until shown otherwise


def _slot_minute(text: str) -> int:
    match = _SLOT.fullmatch(text)
    if not match:
        raise ValidationError(f"Slot {text!r}: use [explore:|exploit:]Wed@16:00 (audience-local weekday and time)")
    weekday = _WEEKDAYS.index(match[1].capitalize())
    return weekday * 1440 + int(match[2]) * 60 + int(match[3])


def _slot_label(minute: int) -> str:
    return f"{_WEEKDAYS[minute // 1440]}@{(minute % 1440) // 60:02d}:{minute % 60:02d}"


def _distance(a: int, b: int) -> int:
    """Minutes between two points of the week, wrapping Sunday night to Monday morning."""
    gap = abs(a - b) % WEEK_MINUTES
    return min(gap, WEEK_MINUTES - gap)


def _week_minute(local: datetime) -> int:
    return local.weekday() * 1440 + local.hour * 60 + local.minute


def load_readouts(paths: list[Path] | list[str]) -> list[Any]:
    """Read saved ``experiment-readout --json`` files (validated in ``evaluate_against_baseline``)."""
    loaded = []
    for path in paths:
        try:
            loaded.append(read_json_file(Path(path)))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid readout JSON in {path}: {exc.msg}") from exc
    return loaded


# --- Data collection (read-only) --------------------------------------------------------------

def _sent_at(row: sqlite3.Row) -> datetime | None:
    try:
        return datetime.fromisoformat(parse_utc(row["send_started_at"]))
    except (ValidationError, ValueError, TypeError, AttributeError):
        return None


def _matched_rate(con: sqlite3.Connection, post_id: str, sent: datetime, *, source: str, metric: str,
                  denominator: str, age_hours: int, tolerance_hours: int,
                  skipped: Counter[str] | None = None) -> float | None:
    """metric/denominator from the ONE source-matched snapshot nearest the target age, else None."""
    target_seconds = sent.timestamp() + age_hours * 3600
    choices = []
    for row in snapshots_for(con, post_id, source=source):  # exact counts only (#15)
        if is_windowed_snapshot(row):  # windowed counts are not cumulative at post age
            if skipped is not None:
                skipped["windowed_snapshots_skipped"] += 1
            continue
        if row[metric] is None or row[denominator] is None or row[denominator] <= 0:
            continue
        try:
            observed = datetime.fromisoformat(parse_utc(row["observed_at"]))
        except (ValidationError, ValueError, TypeError):
            continue
        distance = abs(observed.timestamp() - target_seconds) / 3600
        if distance <= tolerance_hours:
            choices.append((distance, observed, row[metric] / row[denominator]))
    if not choices:
        return None
    return min(choices, key=lambda item: (item[0], item[1]))[2]


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"posts_with_rate": 0, "median_rate": None, "p25_rate": None, "p75_rate": None}
    return {"posts_with_rate": len(values), "median_rate": round(median(values), 6),
            "p25_rate": round(quantile(values, 0.25), 6), "p75_rate": round(quantile(values, 0.75), 6)}


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _interval(values: list[float] | None, digits: int = 6) -> list[float] | None:
    return None if values is None else [round(v, digits) for v in values]


# --- Evaluation -------------------------------------------------------------------------------

def evaluate_against_baseline(
    con: sqlite3.Connection, *, account_id: str, platform: str, audience_timezone: str, source: str,
    metric: str, denominator: str, since: str, until: str, slots: list[str] | None = None,
    campaign_arms: list[str] | None = None, readouts: list[Any] | None = None, age_hours: int = 168,
    tolerance_hours: int = 24, slot_tolerance_minutes: int = 60, min_posts: int = 5, resamples: int = 2000,
    confidence: float = 0.95, seed: int = 16, attribution: dict | None = None, outcome: str | None = None,
) -> dict[str, Any]:
    """Compare personalized slots / variant campaigns / readouts against the generic baseline.

    Only published posts of ONE account and platform whose audience-local send date falls in
    ``since..until`` (inclusive) are used. Reads the database; never writes it.

    With ``attribution`` (``attribution.load_attribution_outcomes``) and ``outcome``
    (``docs_visits`` or ``repo_stars_utm``, issue #16 R4) each post's value is its attributed
    count instead of a snapshot rate; ``source``, ``metric`` and ``denominator`` are then not
    used and must be None. An unknown count excludes the post; it is never 0.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValidationError("Explicit account ID required")
    if platform not in PLATFORMS:
        raise ValidationError("Unknown platform")
    zone = _zone(audience_timezone)
    if attribution is not None:
        if outcome not in ATTRIBUTION_OUTCOMES:
            raise ValidationError(f"--attribution needs --outcome {' or '.join(ATTRIBUTION_OUTCOMES)}")
        if source is not None or metric is not None or denominator is not None:
            raise ValidationError("--attribution replaces --source/--metric/--denominator; give one or the other")
    elif outcome is not None:
        raise ValidationError("--outcome needs --attribution FILE (saved attribution-report --json output)")
    else:
        if not isinstance(source, str) or not 1 <= len(source.strip()) <= 100:
            raise ValidationError("Specify the exact native metric snapshot --source; sources are never pooled")
        if metric not in METRIC_FIELDS or denominator not in ("views", "impressions") or metric == denominator:
            raise ValidationError("Choose a supported metric and a distinct views/impressions denominator")
    first, last = _day(since, "--since"), _day(until, "--until")
    if last < first:
        raise ValidationError("--until must not be earlier than --since")
    if (last - first).days > 366:
        raise ValidationError("The evaluation period may span at most 367 days")
    _int_in(age_hours, "age_hours", 24, 720)
    _int_in(tolerance_hours, "tolerance_hours", 1, 168)
    if tolerance_hours >= age_hours:
        raise ValidationError("tolerance_hours must be smaller than age_hours")
    _int_in(slot_tolerance_minutes, "slot_tolerance_minutes", 0, 120)
    _int_in(min_posts, "min_posts", 3, 100)
    _int_in(resamples, "resamples", 200, 20000)
    if type(seed) is not int:
        raise ValidationError("seed must be an integer")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.5 <= confidence < 1:
        raise ValidationError("confidence must be in [0.5, 1)")
    alpha = round(1 - confidence, 10)

    # Arms: each personalized slot and each variant campaign is ONE comparison against baseline.
    arms: list[dict[str, Any]] = []
    for spec in slots or []:
        role, text = _role(spec, "--slot")
        minute = _slot_minute(text)
        arms.append({"id": f"slot:{_slot_label(minute)}", "kind": "timing", "role": role,
                     "slot": _slot_label(minute), "minute": minute})
    for spec in campaign_arms or []:
        role, name = _role(spec, "--campaign-arm")
        if len(name) > 200:
            raise ValidationError("--campaign-arm name is too long")
        arms.append({"id": f"campaign:{name}", "kind": "campaign", "role": role, "campaign": name})
    readouts = list(readouts or [])
    if not arms and not readouts:
        raise ValidationError("Nothing to evaluate: give at least one --slot, --campaign-arm or --readout")
    if len(arms) + len(readouts) > MAX_COMPARISONS:
        raise ValidationError(f"At most {MAX_COMPARISONS} comparisons per run (each one costs Holm power)")
    ids = [arm["id"] for arm in arms]
    if len(ids) != len(set(ids)):
        raise ValidationError("Duplicate arm: each --slot and --campaign-arm may appear once")

    baseline_spec = generic_baseline_slots(platform)
    if baseline_spec is None and arms:
        raise ValidationError(f"calendar-plan has no generic non-personalized slot for {platform}, so there is no "
                              "baseline to compare --slot/--campaign-arm against; use --readout only")
    tolerance = slot_tolerance_minutes
    baseline_minutes = ([wd * 1440 + clock.hour * 60 + clock.minute for wd, clock in baseline_spec["slots"]]
                        if baseline_spec else [])
    timing_arms = [arm for arm in arms if arm["kind"] == "timing"]
    for arm in timing_arms:
        for minute in baseline_minutes:
            if _distance(arm["minute"], minute) <= 2 * tolerance:
                raise ValidationError(f"--slot {arm['slot']} overlaps calendar-plan's generic baseline slot "
                                      f"{_slot_label(minute)} within +/-{tolerance} min; a post could belong to "
                                      "both. Choose a different slot or a smaller --slot-tolerance-minutes")
    for left, right in combinations(timing_arms, 2):
        if _distance(left["minute"], right["minute"]) <= 2 * tolerance:
            raise ValidationError(f"--slot {left['slot']} and {right['slot']} overlap within +/-{tolerance} min")

    # Posts: one account/platform cohort, published, audience-local send date in the period.
    rows = published_posts(con, account_id=account_id, platform=platform)
    excluded: Counter[str] = Counter()
    outside = 0
    in_period = 0
    groups: dict[str, list[float]] = {"baseline": [], **{arm["id"]: [] for arm in arms}}
    assigned: Counter[str] = Counter()
    unassigned = 0
    for row in rows:
        sent = _sent_at(row)
        if sent is None:
            excluded["missing_or_invalid_send_timestamp"] += 1
            continue
        local = sent.astimezone(zone)
        if not first <= local.date() <= last:
            outside += 1
            continue
        in_period += 1
        minute = _week_minute(local)
        matched = [arm["id"] for arm in arms
                   if (arm["kind"] == "timing" and _distance(minute, arm["minute"]) <= tolerance)
                   or (arm["kind"] == "campaign" and row["campaign"] == arm["campaign"])]
        if len(matched) > 1:
            excluded["ambiguous_arm"] += 1
            continue
        if matched:
            group = matched[0]
        elif any(_distance(minute, b) <= tolerance for b in baseline_minutes):
            group = "baseline"
        else:
            unassigned += 1
            continue
        assigned[group] += 1
        if attribution is not None:
            count, why = attributed_outcome(attribution, row, cast(str, outcome), age_hours=age_hours)
            if why:
                excluded[f"{why}:{group}"] += 1
                continue
            groups[group].append(float(cast(int, count)))
            continue
        rate = _matched_rate(con, row["id"], sent, source=cast(str, source), metric=cast(str, metric),
                             denominator=cast(str, denominator), age_hours=age_hours,
                             tolerance_hours=tolerance_hours, skipped=excluded)
        if rate is None:
            excluded[f"no_matched_snapshot:{group}"] += 1
            continue
        groups[group].append(rate)

    baseline_values = groups["baseline"]
    value_kind = "an attributed count" if attribution is not None else "a matched-age rate"
    baseline_block = None
    if baseline_spec is not None:
        baseline_block = {
            "definition": "calendar-plan generic, non-personalized slots for this platform (no --slot)",
            "slots": [_slot_label(m) for m in baseline_minutes], "evidence": baseline_spec["evidence"],
            "confidence": baseline_spec["confidence"], "evidence_url": baseline_spec["evidence_url"],
            "slot_tolerance_minutes": tolerance, "posts_in_period": assigned["baseline"],
            **_summary(baseline_values)}

    comparisons: list[dict[str, Any]] = []
    for arm in arms:
        values = groups[arm["id"]]
        item: dict[str, Any] = {
            "id": arm["id"], "kind": "baseline_comparison", "arm_kind": arm["kind"], "role": arm["role"],
            "evidence": "correlational",
            "design": "operator-chosen posting slot or campaign versus posts at the generic slot; not randomized",
            "arm": {k: arm[k] for k in ("slot", "campaign") if k in arm},
            "arm_posts_in_period": assigned[arm["id"]], "arm_summary": _summary(values),
            "baseline_posts_with_rate": len(baseline_values),
            "estimate": None, "relative_estimate": None, "interval": None, "relative_interval": None,
            "p_value": None, "p_value_method": None, "holm_adjusted_p": None, "status": "insufficient_data",
            "direction": None, "explanations": []}
        if len(values) < min_posts or len(baseline_values) < min_posts:
            item["explanations"].append(
                f"{len(values)} arm and {len(baseline_values)} baseline posts with {value_kind}; at least "
                f"{min_posts} each are required. No estimate is reported (unknown, not zero)")
        else:
            boot = bootstrap_independent(values, baseline_values, resamples=resamples, confidence=confidence,
                                         rng=_rng(seed, arm["id"], "bootstrap"))
            p, method = permutation_p(values, baseline_values, resamples=resamples,
                                      rng=_rng(seed, arm["id"], "permutation"))
            item.update(estimate=_round(_median_difference(values, baseline_values)),
                        relative_estimate=_round(_relative_median_difference(values, baseline_values), 4),
                        interval=_interval(boot["difference"]), relative_interval=_interval(boot["relative"], 4),
                        p_value=round(p, 6), p_value_method=method)
            if boot["relative"] is None:
                item["explanations"].append("relative interval undefined: a resampled baseline median was zero")
        comparisons.append(item)

    experiments, excluded_readouts = _readout_items(con, readouts, account_id=account_id, platform=platform,
                                                    zone=zone, first=first, last=last, resamples=resamples,
                                                    confidence=confidence, seed=seed)
    everything = comparisons + experiments
    tested = [item for item in everything if item["p_value"] is not None]
    for item, adjusted in zip(tested, holm_bonferroni([item["p_value"] for item in tested])):
        item["holm_adjusted_p"] = round(adjusted, 6)
    for item in everything:
        _decide(item, alpha=alpha, family_size=len(tested))

    if attribution is not None:
        window = attribution["analytics_date_range"]
        measure: dict[str, Any] = {
            "metric": outcome, "denominator": "none", "primary_source": "attribution_report",
            "attribution_field": ATTRIBUTION_OUTCOMES[cast(str, outcome)],
            "attribution_source": attribution.get("source", "analytics"),
            "analytics_date_range": [d.isoformat() for d in window] if window else None,
            "post_age_hours": age_hours, "age_tolerance_hours": None,
            "statistic": "median attributed count per post (arm minus baseline)"}
        source_label = "attribution-report"
    else:
        measure = {"metric": metric, "denominator": denominator, "post_age_hours": age_hours,
                   "age_tolerance_hours": tolerance_hours, "statistic": "median rate (arm minus baseline)"}
        source_label = cast(str, source)
    explore_posts = sum(assigned[a["id"]] for a in arms if a["role"] == "explore")
    exploit_posts = sum(assigned[a["id"]] for a in arms if a["role"] == "exploit")
    personalized = explore_posts + exploit_posts
    return {
        "kind": KIND, "schema_version": 1,
        "cohort": {"account_id": account_id, "platform": platform, "audience_timezone": zone.key,
                   "source": source_label},
        "period": {"since": first.isoformat(), "until": last.isoformat(),
                   "basis": "audience-local date of send_started_at, inclusive"},
        "measure": measure,
        "baseline": baseline_block,
        "posts": {"published_in_period": in_period, "published_outside_period": outside,
                  "unassigned_in_period": unassigned, "assigned": dict(sorted(assigned.items())),
                  "excluded": dict(sorted(excluded.items()))},
        "correlational": [item for item in everything if item["evidence"] == "correlational"],
        "randomized": [item for item in everything if item["evidence"] == "randomized"],
        "excluded_readouts": excluded_readouts,
        "uncertainty": {"interval": "percentile bootstrap", "confidence": confidence, "resamples": resamples,
                        "seed": seed, "minimum_posts_per_group": min_posts,
                        "minimum_pairs_per_readout": MIN_RECOMMENDED_PAIRS,
                        "baseline_p_value": "two-sided permutation test of the median difference (exact when "
                                            f"at most {EXACT_PERMUTATION_LIMIT} relabellings)",
                        "readout_p_value": "the readout's own two-sided sign test, unchanged"},
        "multiple_testing": {"method": "holm-bonferroni", "alpha": alpha, "family_size": len(tested),
                             "family": "every comparison with a p-value in this run (baseline comparisons and "
                                       "readouts together)"},
        "explore_exploit": {"explore_posts": explore_posts, "exploit_posts": exploit_posts,
                            "baseline_posts": assigned["baseline"],
                            "exploration_share": round(explore_posts / personalized, 4) if personalized else None,
                            "note": "Roles are the operator's declaration (default explore). Preregistered "
                                    "experiments are exploration."},
        "status": "evaluated" if tested else "insufficient_data",
        "warnings": list(WARNINGS) + (list(ATTRIBUTION_WARNINGS) if attribution is not None else []),
        "disclaimer": DISCLAIMER,
    }


def _decide(item: dict[str, Any], *, alpha: float, family_size: int) -> None:
    """Set ``status`` and ``direction`` from the interval and the Holm-adjusted p. Labels untouched."""
    explanations = item["explanations"]
    if item["p_value"] is None or item["interval"] is None:
        item["status"] = "insufficient_data"
        return
    low, high = item["interval"]
    excludes_zero = low > 0 or high < 0
    # A readout can only separate when the strict sign-test gate already said 'directional'.
    gate_ok = item["kind"] == "baseline_comparison" or item["readout_label"] == "directional"
    if excludes_zero and item["holm_adjusted_p"] <= alpha and gate_ok:
        item["status"] = "separated_after_holm"
        higher = low > 0
        if item["kind"] == "baseline_comparison":
            item["direction"] = "arm_higher" if higher else "arm_lower"
        else:
            item["direction"] = "treatment_higher" if higher else "treatment_lower"
        explanations.append(f"interval excludes zero and Holm-adjusted p {item['holm_adjusted_p']} <= {alpha} "
                            f"across {family_size} comparison(s): an association to replicate, not proof")
        return
    item["status"] = "no_clear_difference"
    if not excludes_zero:
        explanations.append("the interval includes zero")
    if item["holm_adjusted_p"] > alpha:
        explanations.append(f"Holm-adjusted p {item['holm_adjusted_p']} > {alpha} across {family_size} "
                            "comparison(s)")
    if not gate_ok:
        explanations.append(f"the readout label is {item['readout_label']!r}; only the strict sign-test gate can "
                            "give 'directional', and this report never upgrades it")
    elif item.get("readout_label") == "directional":
        explanations.append("directional under the single-experiment gate but not after correction for the other "
                            "comparisons in this run: replicate before acting")
    if item["role"] == "exploit":
        explanations.append("exploit arm without a clear difference from the generic baseline in this period")


def _readout_items(con: sqlite3.Connection, readouts: list[Any], *, account_id: str, platform: str, zone: ZoneInfo,
                   first: date, last: date, resamples: int, confidence: float,
                   seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, readout in enumerate(readouts):
        where = f"readout[{index}]"
        if not isinstance(readout, dict) or readout.get("kind") != READOUT_KIND:
            raise ValidationError(f"{where} must be saved 'experiment-readout --json' output ({READOUT_KIND})")
        experiment_id = readout.get("experiment_id")
        classification, evaluation = readout.get("classification"), readout.get("evaluation")
        design_in, plan = readout.get("design"), readout.get("experiment_plan")
        if not isinstance(experiment_id, str) or not experiment_id or not all(
                isinstance(x, dict) for x in (classification, evaluation, design_in, plan)):
            raise ValidationError(f"{where} is missing experiment_id, classification, evaluation, design or "
                                  "experiment_plan")
        classification, evaluation = cast(dict, classification), cast(dict, evaluation)
        design_in, plan = cast(dict, design_in), cast(dict, plan)
        if experiment_id in seen:
            raise ValidationError(f"{where}: experiment {experiment_id} is listed twice")
        seen.add(experiment_id)
        raw_cohort = plan.get("cohort")
        cohort: dict = raw_cohort if isinstance(raw_cohort, dict) else {}
        if cohort.get("account_id") != account_id or cohort.get("platform") != platform:
            raise ValidationError(f"{where}: experiment {experiment_id} is on {cohort.get('account_id')}/"
                                  f"{cohort.get('platform')}, not {account_id}/{platform}; cohorts are never pooled")
        label = readout.get("label")
        if label not in ("insufficient_data", "inconclusive", "directional"):
            raise ValidationError(f"{where}: unknown readout label {label!r}")
        pairs = evaluation.get("pair_results")
        if not isinstance(pairs, list):
            raise ValidationError(f"{where}: evaluation.pair_results must be a list")
        diffs: list[float] = []
        outside: list[str] = []
        for pair in pairs:
            if not isinstance(pair, dict) or not all(isinstance(pair.get(k), (int, float))
                                                     and not isinstance(pair.get(k), bool)
                                                     for k in ("control_rate", "treatment_rate")):
                raise ValidationError(f"{where}: every pair result needs numeric control_rate and treatment_rate")
            for key in ("control", "treatment"):
                post_id = pair.get(key)
                try:
                    sent = _sent_at(get_post(con, str(post_id)))
                except ValidationError:
                    sent = None
                if sent is None or not first <= sent.astimezone(zone).date() <= last:
                    outside.append(str(post_id))
            diffs.append(pair["treatment_rate"] - pair["control_rate"])
        design = describe_design(design_in.get("assignment"))
        if outside:
            excluded.append({"experiment_id": experiment_id, "reason": "pairs_outside_period_or_not_in_database",
                             "posts": sorted(set(outside)),
                             "note": "A readout is used whole or not at all: its label covers all its pairs."})
            continue
        sign_p = classification.get("sign_test_two_sided_p")
        item: dict[str, Any] = {
            "id": f"readout:{experiment_id}", "kind": "preregistered_experiment", "role": "explore",
            "evidence": "randomized" if design["assignment"] == "randomized" else "correlational",
            "design": design, "experiment_id": experiment_id, "dimension": readout.get("dimension"),
            "readout_label": label, "readout_direction": readout.get("direction"),
            "label_source": "growth.classify_result strict sign-test gate (unchanged)",
            "pairs": len(diffs), "estimate": None, "interval": None,
            "statistic": "median paired difference (treatment rate minus control rate)",
            "p_value": None, "p_value_method": None, "holm_adjusted_p": None,
            "status": "insufficient_data", "direction": None, "explanations": []}
        if label == "insufficient_data" or isinstance(sign_p, bool) or not isinstance(sign_p, (int, float)):
            item["explanations"].append("the readout itself reports insufficient data; it is not tested")
        elif len(diffs) < MIN_RECOMMENDED_PAIRS:
            item["explanations"].append(f"{len(diffs)} pair(s) is below the {MIN_RECOMMENDED_PAIRS}-pair minimum "
                                        "for an interval; no estimate is reported (unknown, not zero)")
        else:
            item.update(estimate=_round(median(diffs)), p_value=round(float(sign_p), 6),
                        p_value_method="readout_sign_test",
                        interval=_interval(bootstrap_paired(diffs, resamples=resamples, confidence=confidence,
                                                            rng=_rng(seed, f"readout:{experiment_id}",
                                                                     "bootstrap"))))
        if item["evidence"] == "randomized":
            item["explanations"].append("randomized assignment is the owner's declaration; ReachWeaver did not "
                                        "randomize or verify it")
        items.append(item)
    return items, excluded


# --- Rendering --------------------------------------------------------------------------------

def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.6f}"


def _render_item(item: dict[str, Any]) -> str:
    interval = item["interval"]
    ci = "n/a" if interval is None else f"[{interval[0]:+.6f}, {interval[1]:+.6f}]"
    if item["kind"] == "baseline_comparison":
        head = (f"  {item['id']} [{item['role']}] n={item['arm_summary']['posts_with_rate']} vs baseline "
                f"n={item['baseline_posts_with_rate']}: median diff {_fmt(item['estimate'])}")
        if item["relative_estimate"] is not None:
            head += f" ({item['relative_estimate']:+.1%})"
    else:
        head = (f"  {item['id']} [{item['role']}] label={item['readout_label']} design={item['design']['assignment']}"
                f" pairs={item['pairs']}: median paired diff {_fmt(item['estimate'])}")
    p = item["p_value"]
    tail = (f" CI {ci}; p={'n/a' if p is None else p} ({item['p_value_method'] or 'not tested'}) "
            f"holm={'n/a' if item['holm_adjusted_p'] is None else item['holm_adjusted_p']} -> {item['status']}")
    if item["direction"]:
        tail += f" ({item['direction']})"
    lines = [head + tail]
    lines += [f"    - {text}" for text in item["explanations"]]
    return "\n".join(lines)


def render_text(result: dict[str, Any]) -> str:
    cohort, period, measure = result["cohort"], result["period"], result["measure"]
    lines = [f"BASELINE-EVAL {cohort['account_id']}/{cohort['platform']} {period['since']}..{period['until']} "
             f"({cohort['audience_timezone']}) {measure['metric']}/{measure['denominator']} at "
             f"{measure['post_age_hours']}h, source {cohort['source']}: {result['status']}"]
    base = result["baseline"]
    if base is None:
        lines.append("BASELINE: calendar-plan has no generic slot for this platform (readouts only)")
    else:
        median_text = "n/a" if base["median_rate"] is None else f"{base['median_rate']:.6f}"
        lines.append(f"BASELINE calendar-plan generic {', '.join(base['slots'])} (+/-{base['slot_tolerance_minutes']} "
                     f"min; {base['evidence']}, confidence {base['confidence']}): {base['posts_with_rate']} posts "
                     f"with rate, median {median_text}")
    unc, mt = result["uncertainty"], result["multiple_testing"]
    lines.append(f"UNCERTAINTY: {unc['confidence']:.0%} percentile bootstrap, {unc['resamples']} resamples, seed "
                 f"{unc['seed']}; Holm-Bonferroni across {mt['family_size']} tested comparison(s), alpha {mt['alpha']}")
    lines.append("CORRELATIONAL (not randomized):")
    lines += [_render_item(item) for item in result["correlational"]] or ["  (none)"]
    lines.append("RANDOMIZED (owner-declared, unverified):")
    lines += [_render_item(item) for item in result["randomized"]] or ["  (none)"]
    for item in result["excluded_readouts"]:
        lines.append(f"EXCLUDED readout {item['experiment_id']}: {item['reason']} ({len(item['posts'])} post(s))")
    posts = result["posts"]
    lines.append(f"POSTS: {posts['published_in_period']} published in period, {posts['unassigned_in_period']} "
                 f"unassigned, excluded {posts['excluded'] or 'none'}")
    ee = result["explore_exploit"]
    share = "n/a" if ee["exploration_share"] is None else f"{ee['exploration_share']:.0%}"
    lines.append(f"EXPLORE/EXPLOIT: explore {ee['explore_posts']}, exploit {ee['exploit_posts']}, baseline "
                 f"{ee['baseline_posts']} posts; exploration share {share}")
    lines += [f"WARNING: {text}" for text in result["warnings"]]
    lines.append(f"DISCLAIMER: {result['disclaimer']}")
    return "\n".join(lines)

