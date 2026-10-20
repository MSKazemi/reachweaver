"""Read-only calendar gap finder (public issue #9): empty and overloaded slots per account.

It compares the slots the planner would use (``calendar.propose_calendar``'s candidate windows:
operator ``--slot`` values, an owner-exported audience-activity file, or the generic baseline)
with the posts already queued locally, and with ``timing_analysis`` when the owner supplies
matched-age native metrics. It never approves, schedules or publishes, makes no network
request and writes nothing; a gap is a prompt for the owner, not a recommendation to post.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any

from .calendar import _WD_NAMES, _aware, _date, _slots, _zone
from .policy import ValidationError
from .timing_analysis import _slot as daypart_slot

DEFAULT_TOLERANCE_MINUTES = 90
DEFAULT_MAX_PER_DAY = 2
DEFAULT_MIN_SPACING_HOURS = 20
ADVISORY = (
    "Read-only: gaps and overload flags are prompts for the owner, never instructions to post and never "
    "consent to publish. Nothing was approved, scheduled or sent, and no platform was contacted.",
    "Empty means no locally scheduled post near the slot. Posts queued in a platform's native scheduler "
    "or created outside ReachWeaver are invisible here.",
    "Candidate slots are low-confidence starting hypotheses (or owner-supplied times), not proven best times.",
)


def _utc(value: object) -> datetime:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("Invalid existing schedule; repair state before analysing the calendar") from exc
    if at.tzinfo is None:
        raise ValidationError("Invalid existing schedule; repair state before analysing the calendar")
    return at.astimezone(timezone.utc)


def _scheduled(con, account_id: str) -> list[tuple[str, datetime]]:
    rows = con.execute("SELECT id, scheduled_at FROM posts WHERE account_id=? AND status='scheduled' "
                       "AND scheduled_at IS NOT NULL ORDER BY scheduled_at, id", (account_id,)).fetchall()
    return [(row["id"], _utc(row["scheduled_at"])) for row in rows]


def _check_int(name: str, value: object, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValidationError(f"{name} must be an integer {low}..{high}")
    return value


def find_calendar_gaps(
    con, *, account_id: str, platform: str, audience_timezone: str, start: str | None = None,
    days: int = 14, slots: list[str] | None = None, blackout: list[str] | None = None,
    audience_activity: dict | None = None, timing: dict | None = None,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES, max_per_day: int = DEFAULT_MAX_PER_DAY,
    min_spacing_hours: int = DEFAULT_MIN_SPACING_HOURS, now: datetime | None = None,
) -> dict:
    """Classify every candidate slot in the window and flag overloaded days and crowded posts.

    ``audience_activity`` is ``audience.derive_slots`` output and ``timing`` is
    ``timing_analysis.analyze_posting_times`` output; both are optional and only annotate.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValidationError("Account ID required")
    _check_int("--days", days, 1, 60)
    _check_int("--tolerance-minutes", tolerance_minutes, 1, 720)
    _check_int("--max-per-day", max_per_day, 1, 20)
    _check_int("--min-spacing-hours", min_spacing_hours, 1, 168)
    zone = _zone(audience_timezone)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValidationError("Current time must include a timezone")
    current = current.astimezone(timezone.utc)
    first = _date(start) if start else current.astimezone(zone).date()
    try:  # the whole window, both ends in UTC, must exist: 0001-01-01 or 9999-12-31 may not
        last = first + timedelta(days=days - 1)
        datetime.combine(first, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
        datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    except OverflowError:
        raise ValidationError("--start is out of range for the calendar window") from None
    blocked = {_date(value) for value in (blackout or [])}

    provenance: dict[tuple[int, time], dict] = {}
    if audience_activity is not None:
        if slots:
            raise ValidationError("--audience-activity and --slot are mutually exclusive; choose one evidence source")
        if audience_activity.get("account_id") != account_id:
            raise ValidationError("Audience activity belongs to a different account")
        for item in audience_activity["slots"]:
            provenance.setdefault((item["weekday"], item["time"]), item["provenance"])
        if not provenance:
            raise ValidationError("Audience activity produced no candidate slots")
        windows, evidence, confidence = list(provenance), audience_activity["evidence"], "unverified"
    else:
        windows, evidence, confidence = _slots(platform, slots)

    queued = _scheduled(con, account_id)
    tolerance = timedelta(minutes=tolerance_minutes)
    timing_by_label = {row["slot"]: row for row in (timing or {}).get("slots", [])}

    rows: list[dict] = []
    dst_skipped: list[str] = []
    matched: set[str] = set()
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day in blocked:
            continue
        for weekday, clock in windows:
            if weekday != day.weekday():
                continue
            try:
                local = _aware(day, clock, zone)
            except ValidationError:
                dst_skipped.append(f"{day.isoformat()}T{clock.isoformat(timespec='minutes')}")
                continue
            utc = local.astimezone(timezone.utc)
            near = [pid for pid, at in queued if abs(at - utc) <= tolerance]
            matched.update(near)
            status = "past" if utc < current else ("filled" if near else "empty")
            _, _, label = daypart_slot(local)
            row: dict[str, Any] = {
                "local": local.isoformat(timespec="minutes"), "utc": utc.isoformat(timespec="seconds"),
                "weekday": _WD_NAMES[weekday], "daypart": label, "status": status, "post_ids": near}
            if status == "empty" and label in timing_by_label:
                seen = timing_by_label[label]
                row["timing_evidence"] = {"posts": seen["posts"], "eligible_for_comparison": seen["eligible_for_comparison"],
                                          "median_rate": seen["median_rate"], "kind": "observational_association_only"}
            if (weekday, clock) in provenance:
                row["provenance"] = provenance[(weekday, clock)]
            rows.append(row)
    rows.sort(key=lambda r: r["utc"])

    window_start = datetime.combine(first, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    window_end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=zone).astimezone(timezone.utc)
    in_window = [(pid, at) for pid, at in queued if window_start <= at < window_end]
    per_day: dict[str, list[str]] = defaultdict(list)
    for pid, at in in_window:
        per_day[at.astimezone(zone).date().isoformat()].append(pid)
    overloaded_days = [{"date": day, "count": len(ids), "post_ids": ids}
                       for day, ids in sorted(per_day.items()) if len(ids) > max_per_day]
    spacing = timedelta(hours=min_spacing_hours)
    close_pairs = []
    for (pid_a, at_a), (pid_b, at_b) in zip(in_window, in_window[1:]):
        if at_b - at_a < spacing:
            close_pairs.append({"post_ids": [pid_a, pid_b],
                                "hours_apart": round((at_b - at_a).total_seconds() / 3600, 2)})
    off_slot = [pid for pid, _ in in_window if pid not in matched]
    ready = con.execute("SELECT count(*) FROM posts WHERE account_id=? AND status='approved'",
                        (account_id,)).fetchone()[0]

    counts = {name: sum(1 for row in rows if row["status"] == name) for name in ("filled", "empty", "past")}
    return {
        "account_id": account_id, "platform": platform, "audience_timezone": zone.key,
        "window": {"start": first.isoformat(), "end": last.isoformat(), "days": days},
        "evidence": evidence, "confidence": confidence,
        "thresholds": {"tolerance_minutes": tolerance_minutes, "max_per_day": max_per_day,
                       "min_spacing_hours": min_spacing_hours},
        "slots": rows,
        "overloaded_days": overloaded_days, "close_pairs": close_pairs, "off_slot_post_ids": off_slot,
        "summary": {"candidate_slots": len(rows), **counts, "overloaded_days": len(overloaded_days),
                    "close_pairs": len(close_pairs), "scheduled_in_window": len(in_window),
                    "approved_unscheduled": ready},
        "timing": None if timing is None else {"cohort": timing.get("cohort"), "decision": timing.get("decision"),
                                               "note": "Annotation only; observational, not a best-time proof."},
        "dst_skipped_local": dst_skipped,
        "advisory": list(ADVISORY),
    }


def render_text(report: dict) -> str:
    """Human-readable gap report (the JSON form carries every field)."""
    summary = report["summary"]
    lines = [f"Calendar gaps for {report['account_id']} ({report['platform']}), "
             f"{report['window']['start']} to {report['window']['end']}, audience zone {report['audience_timezone']}",
             f"Slots: {summary['candidate_slots']} candidate, {summary['filled']} filled, {summary['empty']} empty, "
             f"{summary['past']} past. Scheduled in window: {summary['scheduled_in_window']}. "
             f"Approved but unscheduled: {summary['approved_unscheduled']}."]
    for row in report["slots"]:
        if row["status"] == "empty":
            note = ""
            if "timing_evidence" in row:
                seen = row["timing_evidence"]
                note = f"  [own history: {seen['posts']} posts, median {seen['median_rate']}, association only]"
            lines.append(f"  EMPTY   {row['weekday']} {row['local']}{note}")
    for item in report["overloaded_days"]:
        lines.append(f"  OVERLOADED day {item['date']}: {item['count']} scheduled ({', '.join(item['post_ids'])})")
    for pair in report["close_pairs"]:
        lines.append(f"  TOO CLOSE {pair['post_ids'][0]} / {pair['post_ids'][1]}: {pair['hours_apart']} h apart")
    if report["off_slot_post_ids"]:
        lines.append(f"  Off-slot scheduled posts: {', '.join(report['off_slot_post_ids'])}")
    if report["dst_skipped_local"]:
        lines.append(f"  Skipped (DST gap or fold): {', '.join(report['dst_skipped_local'])}")
    lines.extend(f"Note: {item}" for item in report["advisory"])
    return "\n".join(lines)
