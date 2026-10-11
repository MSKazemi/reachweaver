"""Read-only, privacy-preserving analysis of user-imported social outcome snapshots.

This is NOT a platform algorithm, a causal test, or permission to schedule posts.
No platform accounts, websites or user APIs are contacted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .growth import is_windowed_snapshot
from .policy import PLATFORMS, ValidationError
from .storage import METRIC_FIELDS, parse_utc, published_posts, snapshots_for

DAYPARTS = ("00:00-06:00", "06:00-12:00", "12:00-18:00", "18:00-24:00")


def _require_zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value:
        raise ValidationError("Audience IANA timezone required")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Unknown audience IANA timezone") from exc


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValidationError("Date filters must be YYYY-MM-DD")
    try:
        d = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid YYYY-MM-DD date filter") from exc
    if d.isoformat() != value:
        raise ValidationError("Date filters must be YYYY-MM-DD")
    return d


def _percentile(values: list[float], fraction: float) -> float:
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def _slot(local: datetime) -> tuple[str, int, str]:
    kind = "weekend" if local.weekday() >= 5 else "weekday"
    index = local.hour // 6
    return kind, index, f"{kind}_{index * 6:02d}_{(index + 1) * 6:02d}"


def analyze_posting_times(
    con, *, account_id: str, platform: str, audience_timezone: str, source: str,
    metric: str, denominator: str, age_hours: int = 168, tolerance_hours: int = 24,
    min_posts_per_slot: int = 3, campaign: str | None = None,
    since: str | None = None, until: str | None = None,
) -> dict:
    """Evaluate associations within ONE account and ONE metric-source cohort.

    The only data input is already user-imported native metric snapshots.
    All rates are sampled at comparable post ages, never summed over snapshots.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValidationError("Explicit account ID required")
    if platform not in PLATFORMS:
        raise ValidationError("Unknown platform")
    zone = _require_zone(audience_timezone)
    if not isinstance(source, str) or not 1 <= len(source.strip()) <= 100:
        raise ValidationError("Specify exact native metric snapshot --source, never pool different sources")
    if metric not in METRIC_FIELDS or denominator not in ("views", "impressions") or metric == denominator:
        raise ValidationError("Choose a supported metric and a distinct views/impressions denominator")
    if type(age_hours) is not int or not 24 <= age_hours <= 720:
        raise ValidationError("Age in hours must be 24..720")
    if type(tolerance_hours) is not int or not 1 <= tolerance_hours < age_hours or tolerance_hours > 168:
        raise ValidationError("Tolerance must be 1..168 hours and less than target age")
    if type(min_posts_per_slot) is not int or not 3 <= min_posts_per_slot <= 50:
        raise ValidationError("Minimum posts per slot must be 3..50")
    if campaign is not None and (not isinstance(campaign, str) or not campaign.strip()):
        raise ValidationError("Campaign filter must be a nonempty string")
    beginning, ending = _date(since), _date(until)
    if beginning and ending and ending < beginning:
        raise ValidationError("--until must not be earlier than --since")

    posts = published_posts(con, account_id=account_id, platform=platform, campaign=campaign)
    excluded: Counter[str] = Counter()
    rows = []
    total_in_window = 0
    for post in posts:
        if not post["send_started_at"]:
            excluded["missing_send_timestamp"] += 1
            continue
        try:
            sent = datetime.fromisoformat(parse_utc(post["send_started_at"]))
        except (ValidationError, ValueError, TypeError):
            excluded["invalid_send_timestamp"] += 1
            continue
        local = sent.astimezone(zone)
        if beginning and local.date() < beginning or ending and local.date() > ending:
            continue
        total_in_window += 1
        target = sent + timedelta(hours=age_hours)
        choices = []
        for observation in snapshots_for(con, post["id"], source=source):  # exact counts only (#15)
            if is_windowed_snapshot(observation):  # windowed counts are not cumulative at post age
                excluded["windowed_snapshots_skipped"] += 1
                continue
            if observation[metric] is None or observation[denominator] is None or observation[denominator] <= 0:
                continue
            try:
                observed = datetime.fromisoformat(parse_utc(observation["observed_at"]))
            except (ValidationError, ValueError, TypeError):
                continue
            distance = abs((observed - target).total_seconds()) / 3600
            if distance <= tolerance_hours:
                choices.append((distance, observed, observation))
        if not choices:
            excluded["no_source_matched_snapshot_or_denominator"] += 1
            continue
        # Pick one closest snapshot; cumulative snapshots are NEVER summed.
        _, _, selected = min(choices, key=lambda x: (x[0], x[1]))
        kind, index, label = _slot(local)
        rate = selected[metric] / selected[denominator]
        rows.append({"slot": label, "period": kind, "part_index": index, "rate": rate})

    groups = defaultdict(list)
    for item in rows:
        groups[item["slot"]].append(item["rate"])
    slots = []
    for key, values in groups.items():
        period, first_hour, end_hour = key.rsplit("_", 2)
        slots.append({
            "slot": key, "day_group": period,
            "local_time_range": f"{first_hour}:00-{end_hour}:00",
            "posts": len(values),
            "eligible_for_comparison": len(values) >= min_posts_per_slot,
            "median_rate": round(median(values), 6),
            "p25_rate": round(_percentile(values, 0.25), 6),
            "p75_rate": round(_percentile(values, 0.75), 6),
        })
    slots.sort(key=lambda x: (x["day_group"], x["local_time_range"]))
    eligible = sorted(
        (row for row in slots if row["eligible_for_comparison"]),
        key=lambda x: (-x["median_rate"], -x["posts"], x["slot"]),
    )

    candidate = None
    decision = "insufficient_comparable_slots"
    if len(eligible) >= 2:
        leader, runner_up = eligible[:2]
        if leader["median_rate"] > runner_up["median_rate"]:
            decision = "exploratory_candidate"
            candidate = {
                "slot": leader["slot"], "median_rate": leader["median_rate"],
                "runner_up_slot": runner_up["slot"],
                "runner_up_median_rate": runner_up["median_rate"],
                "requires_human_approval": True,
                "validity": "observational_only_not_a_proven_best_time",
            }
        else:
            decision = "no_clear_separation"

    return {
        "cohort": {"account_id": account_id, "platform": platform,
                   "campaign": campaign, "audience_timezone": zone.key, "source": source},
        "measure": {"metric": metric, "denominator": denominator, "post_age_hours": age_hours,
                    "age_tolerance_hours": tolerance_hours},
        "filters": {"since": since, "until": until, "minimum_posts_per_slot": min_posts_per_slot},
        "published_in_window_with_timestamp": total_in_window,
        "posts_with_comparable_snapshots": len(rows),
        "excluded": dict(sorted(excluded.items())),
        "slots": slots,
        "decision": decision,
        "candidate": candidate,
        "warnings": [
            "Observational post-time association, NOT a randomized experiment, algorithm reverse-engineering or proof of optimal times.",
            "Within-cohort formats, topics, promotions and follower growth are NOT controlled: stratify by campaign and inspect manually.",
            "Only user-imported snapshots with the exact requested source are used; missing is unknown, NOT zero.",
            "send_started_at is a send-attempt timestamp, not guaranteed precise publication time.",
            "A slot chosen on the same sample may reflect winner's curse; test it against another slot prospectively.",
            "This command cannot approve, schedule or publish anything.",
        ],
    }
