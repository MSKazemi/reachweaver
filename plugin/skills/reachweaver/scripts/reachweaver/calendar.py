"""Deterministic offline slot proposals; never approves or publishes posts.

Industry windows are uncertain starting points, not platform-ranking rules.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .policy import SUPPORTED_LIVE, ValidationError
from .storage import approval_binding_problem, approval_key_mode, binding_refusal, get_post, is_paused, transition

# Times represent the intended AUDIENCE's local clock, not the operator's clock.
# These are illustrative recurring candidate windows, not proven optimal slots.
# No fallback is invented for channels without usable population timing evidence.
_CANDIDATE_HOUR = {
    "linkedin": (16, (0, 1, 2, 3, 4)),
    "instagram": (19, (0, 1, 2, 3, 4)),
    "facebook": (9, (0, 1, 2, 3, 4)),
    "threads": (9, (0, 1, 2, 3, 4)),
    "x": (9, (0, 1, 2, 3, 4)),
    "tiktok": (9, (5, 6)),
    "bluesky": (18, (0, 1, 2, 3, 4, 5, 6)),
}
_WD = {name: number for number, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
_WD_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SLOT = re.compile(r"^(mon|tue|wed|thu|fri|sat|sun)@([01][0-9]|2[0-3]):([0-5][0-9])$", re.I)
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_EVIDENCE_URL = "https://buffer.com/resources/best-time-to-post-social-media/"
# Native queue caps are not API authorization; recheck provider UI before handoff.
_NATIVE_HORIZON_DAYS = {"tiktok": 10, "pinterest": 30}
# Issue #16 R3: at most this share of proposed posts may go to exploration slots, so most of a
# batch stays on the evidence-backed (exploit) slots.
EXPLORE_SHARE_CAP = 0.5


def _date(value: str) -> date:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        raise ValidationError("Dates must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("Invalid calendar date") from exc


def _zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Audience IANA timezone is required (e.g. Europe/Rome)")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Unknown IANA timezone; install the system timezone database if needed") from exc


def _slots(platform: str, custom: list[str] | None) -> tuple[list[tuple[int, time]], str, str]:
    if custom:
        if len(custom) > 28:
            raise ValidationError("At most 28 custom slots are supported")
        result = []
        for item in custom:
            match = _SLOT.fullmatch(item) if isinstance(item, str) else None
            if not match:
                raise ValidationError("Use --slot Wed@16:00 (repeat for each local weekday/time)")
            result.append((_WD[match[1].lower()], time(int(match[2]), int(match[3]))))
        if len(result) != len(set(result)):
            raise ValidationError("Duplicate slot in requested calendar")
        return result, "operator_supplied", "unverified"
    if platform not in _CANDIDATE_HOUR:
        raise ValidationError(f"No defensible general time prior for {platform}; supply one or more --slot Weekday@HH:MM from your own audience insights")
    hour, weekdays = _CANDIDATE_HOUR[platform]
    return [(wd, time(hour, 0)) for wd in weekdays], "industry_observational", "low"


def generic_baseline_slots(platform: str) -> dict | None:
    """The non-personalized slots ``calendar-plan`` proposes without ``--slot`` (read-only).

    Returns ``{"slots": [(weekday 0=Mon, audience-local time), ...], "evidence", "confidence",
    "evidence_url"}``, or None when the planner has no general prior for ``platform``. Used by
    ``baseline-eval`` (issue #16) as the comparison baseline; the planner itself is unchanged.
    """
    if platform not in _CANDIDATE_HOUR:
        return None
    windows, evidence, confidence = _slots(platform, None)
    return {"slots": windows, "evidence": evidence, "confidence": confidence, "evidence_url": _EVIDENCE_URL}


def _slot_name(weekday: int, clock: time) -> str:
    return f"{_WD_NAMES[weekday]}@{clock.isoformat(timespec='minutes')}"


def _explore_windows(share: float, explore_slots: list[str] | None, platform: str,
                     windows: list[tuple[int, time]]) -> tuple[Fraction, list[tuple[int, time]]]:
    """Validate ``--explore-share``/``--explore-slot`` (issue #16 R3); (0, []) when unused."""
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not math.isfinite(share):
        raise ValidationError("--explore-share must be a number")
    if share == 0:
        if explore_slots:
            raise ValidationError("--explore-slot needs --explore-share above 0")
        return Fraction(0), []
    if not 0 < share <= EXPLORE_SHARE_CAP:
        raise ValidationError(f"--explore-share must be above 0 and at most {EXPLORE_SHARE_CAP}")
    if not explore_slots:
        raise ValidationError("--explore-share needs at least one --explore-slot Weekday@HH:MM: exploration "
                              "times are your own hypotheses, and the planner invents none")
    chosen, _, _ = _slots(platform, explore_slots)
    if set(chosen) & set(windows):
        raise ValidationError("An --explore-slot repeats an exploit slot; exploration must try a different time")
    return Fraction(str(share)), chosen


def _aware(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Reject non-existent/ambiguous DST times instead of silently choosing a fold."""
    wall = datetime.combine(day, clock)
    early = wall.replace(tzinfo=zone, fold=0)
    late = wall.replace(tzinfo=zone, fold=1)
    if early.utcoffset() != late.utcoffset():
        raise ValidationError(f"DST ambiguous/nonexistent local slot {day} {clock} in {zone.key}; choose another hour")
    back = early.astimezone(timezone.utc).astimezone(zone)
    if back.replace(tzinfo=None) != wall:
        raise ValidationError(f"Nonexistent local slot {day} {clock} in {zone.key}; choose another hour")
    return early


def _snapshot(con, account_id: str) -> list[datetime]:
    reserved = []
    for row in con.execute("SELECT scheduled_at FROM posts WHERE account_id=? AND status='scheduled' AND scheduled_at IS NOT NULL", (account_id,)):
        try:
            at = datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                raise ValueError("naive schedule")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError("Invalid existing schedule; repair state before planning") from exc
        reserved.append(at.astimezone(timezone.utc))
    return reserved


def propose_calendar(
    con, *, account_id: str, platform: str, audience_timezone: str,
    start: str, days: int = 14, spacing_hours: int = 20,
    post_ids: list[str] | None = None, include_drafts: bool = False,
    slots: list[str] | None = None, blackout: list[str] | None = None,
    limit: int = 30, now: datetime | None = None,
    audience_activity: dict | None = None,
    explore_share: float = 0.0, explore_slots: list[str] | None = None,
) -> dict:
    """Plan without modifying approval or scheduling state; no network I/O.

    ``audience_activity`` is the result of ``audience.derive_slots`` (owner-exported activity,
    issue #16). When given, its top hours replace the generic prior, every entry carries the
    slot's provenance, and DST-gap/fold occurrences are skipped (listed) instead of failing.
    When it is None the output is unchanged. The planner stays advisory either way.

    ``explore_share`` (0 < F <= ``EXPLORE_SHARE_CAP``, issue #16 R3) sends floor(F x posts) of
    the posts, evenly interleaved by post order, to the operator's ``explore_slots``; the rest
    stay on the exploit windows above. Every entry then carries ``role`` and the result an
    ``exploration`` block. The split is deterministic, never random. With the default 0 the
    output is unchanged.
    """
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValidationError("Account ID required")
    if type(days) is not int or not 1 <= days <= 60:
        raise ValidationError("--days must be 1..60")
    if type(spacing_hours) is not int or not 1 <= spacing_hours <= 168:
        raise ValidationError("--spacing-hours must be 1..168")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValidationError("--limit must be 1..100")
    if post_ids is not None and (not post_ids or len(post_ids) > 100 or len(post_ids) != len(set(post_ids))):
        raise ValidationError("--post-id entries must be distinct and nonempty (max 100)")
    zone = _zone(audience_timezone)
    first = _date(start)
    end = first + timedelta(days=days - 1)
    blocked = {_date(value) for value in (blackout or [])}
    provenance: dict[tuple[int, time], dict] = {}
    if audience_activity is not None:
        if slots:
            raise ValidationError("--audience-activity and --slot are mutually exclusive; choose one evidence source")
        if audience_activity.get("account_id") != account_id:
            raise ValidationError("Audience activity belongs to a different account")
        for s in audience_activity["slots"]:  # first wins: slots arrive best-ranked first
            provenance.setdefault((s["weekday"], s["time"]), s["provenance"])
        if not provenance:
            raise ValidationError("Audience activity produced no candidate slots")
        windows, evidence, confidence = list(provenance), audience_activity["evidence"], "unverified"
    else:
        windows, evidence, confidence = _slots(platform, slots)
    share, explore_windows = _explore_windows(explore_share, explore_slots, platform, windows)
    dst_skipped: list[str] = []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValidationError("Current time must include a timezone")
    current = current.astimezone(timezone.utc)

    if post_ids is not None:
        posts = [get_post(con, post_id) for post_id in post_ids]
    else:
        statuses = "('draft','approved')" if include_drafts else "('approved')"
        posts = con.execute(
            f"SELECT * FROM posts WHERE account_id=? AND platform=? AND status IN {statuses} ORDER BY created_at,id LIMIT ?",
            (account_id, platform, limit + 1),
        ).fetchall()
        if len(posts) > limit:
            raise ValidationError("More eligible posts than --limit; choose explicit --post-id values or increase limit")
    if not posts:
        raise ValidationError("No eligible posts found; create and approve drafts or pass --include-drafts for preview")
    if len(posts) > limit:
        raise ValidationError("Too many selected posts for --limit")
    for post in posts:
        if post["account_id"] != account_id or post["platform"] != platform:
            raise ValidationError("Selected post does not belong to the specified account/platform")
        if post["status"] not in (("draft", "approved") if include_drafts else ("approved",)):
            raise ValidationError("Only draft/approved posts may be previewed; apply needs individually approved posts")

    candidates = []
    tagged = [(w, "exploit") for w in windows] + [(w, "explore") for w in explore_windows]
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day in blocked:
            continue
        for (weekday, clock), role in tagged:
            if weekday != day.weekday():
                continue
            if provenance and role == "exploit":
                try:
                    local = _aware(day, clock, zone)
                except ValidationError:
                    dst_skipped.append(f"{day.isoformat()}T{clock.isoformat(timespec='minutes')}")
                    continue
            else:
                local = _aware(day, clock, zone)
            utc = local.astimezone(timezone.utc)
            if utc >= current + timedelta(minutes=5):
                candidates.append((utc, local, role))
    candidates.sort(key=lambda x: x[0])
    booked = _snapshot(con, account_id)
    gap = timedelta(hours=spacing_hours)
    rows = []
    for index, post in enumerate(posts):
        # Evenly spaced and capped: post i explores when floor((i+1)F) > floor(iF), so exactly
        # floor(n x F) posts explore. Fixed by post order, not randomized.
        role = "explore" if share and math.floor((index + 1) * share) > math.floor(index * share) else "exploit"
        option = next(((utc, local) for utc, local, kind in candidates
                       if kind == role and all(abs(utc - t) >= gap for t in booked)), None)
        if option is None:
            raise ValidationError(f"Insufficient collision-free slots between {first} and {end}; add days/slots or adjust blackout and spacing")
        utc, local = option
        booked.append(utc)
        horizon = _NATIVE_HORIZON_DAYS.get(platform)
        within_native_horizon = (utc <= current + timedelta(days=horizon)) if horizon else None
        row = {
            "native_horizon_days": horizon, "within_native_horizon_now": within_native_horizon,
            "post_id": post["id"], "account_id": account_id, "platform": platform,
            "post_status": post["status"], "audience_timezone": zone.key,
            "scheduled_local": local.isoformat(timespec="minutes"),
            "scheduled_utc": utc.isoformat(timespec="seconds"),
            "evidence": evidence, "confidence": confidence,
            "evidence_url": _EVIDENCE_URL if evidence == "industry_observational" else None,
            "delivery": "reachweaver_local_queue" if platform in SUPPORTED_LIVE else "manual_native_handoff",
            "schedule_status": "proposed_only",
        }
        if share:
            row["role"] = role
        if role == "explore":  # the operator's hypothesis, not the exploit evidence
            row.update(evidence="operator_explore_hypothesis", confidence="unverified", evidence_url=None)
        elif provenance:
            row["provenance"] = provenance[(local.weekday(), local.time().replace(tzinfo=None))]
        rows.append(row)
    result = {
        "account_id": account_id, "platform": platform,
        "start_date": first.isoformat(), "end_date": end.isoformat(),
        "spacing_hours": spacing_hours,
        "delivery_note": "This is only a proposed local calendar; no credentials or platforms contacted. Native scheduling needs a separate confirmed platform receipt.",
        "entries": rows,
    }
    if audience_activity is not None:
        summary = {key: value for key, value in audience_activity.items() if key != "slots"}
        summary["candidate_slots"] = [f"{_WD_NAMES[wd]}@{clock.isoformat(timespec='minutes')}" for wd, clock in windows]
        summary["dst_skipped_local"] = dst_skipped
        summary["advisory_only"] = "Proposals only: the planner never approves, schedules or publishes on its own."
        result["audience_activity"] = summary
    if share:
        explore_count = sum(1 for row in rows if row["role"] == "explore")
        exploit_names = [_slot_name(wd, clock) for wd, clock in windows]
        explore_names = [_slot_name(wd, clock) for wd, clock in explore_windows]
        # The generic prior IS baseline-eval's baseline, so only non-generic exploit slots are arms.
        arms = [] if evidence == "industry_observational" else [f"exploit:{name}" for name in exploit_names]
        notes = [
            "Roles follow a fixed, evenly spaced order of the selected posts. Nothing is randomized, so "
            "explore-versus-exploit outcomes stay associational.",
            "Explore slots are the operator's own hypotheses (evidence=operator_explore_hypothesis, "
            "confidence=unverified), not measured better times.",
            "Proposals only: the planner never approves, schedules or publishes on its own; --apply "
            "gating, spacing and blackouts are unchanged.",
        ]
        if explore_count == 0:
            notes.append(f"Too few selected posts for an explore slot at this share: floor({len(rows)} x "
                         f"{float(share)}) = 0.")
        result["exploration"] = {
            "explore_share": float(share), "explore_share_cap": EXPLORE_SHARE_CAP,
            "explore_entries": explore_count, "exploit_entries": len(rows) - explore_count,
            "exploit_slots": exploit_names, "exploit_evidence": evidence, "explore_slots": explore_names,
            "assignment": "deterministic_interleave", "randomized": False,
            "baseline_eval_slots": arms + [f"explore:{name}" for name in explore_names],
            "notes": notes,
        }
    return result


def apply_calendar(con, proposal: dict, *, now: datetime | None = None) -> dict:
    """Atomically queue *already approved* posts only on supported local adapters.

    Do not approve or publish anything here; all-or-nothing recheck under lock. Every post
    must still match its approval hash (#7); ``transition`` then rebinds the new time.
    """
    entries = proposal["entries"]
    account = proposal["account_id"]
    platform = proposal["platform"]
    if platform not in SUPPORTED_LIVE:
        raise ValidationError("This platform is manual-only in ReachWeaver; use its native scheduler and verify the queue receipt")
    if not entries:
        raise ValidationError("No proposed slots to apply")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValidationError("Current time requires timezone")
    current = current.astimezone(timezone.utc)
    gap = timedelta(hours=proposal["spacing_hours"])
    con.execute("BEGIN IMMEDIATE")
    try:
        if is_paused(con):
            raise ValidationError("Publishing queue is paused; do not schedule while emergency stop is active")
        reserved = _snapshot(con, account)
        for entry in entries:
            post = get_post(con, entry["post_id"])
            if post["account_id"] != account or post["platform"] != platform or post["status"] != "approved":
                raise ValidationError("Posts changed or lack exact-content approval; nothing scheduled")
            problem = approval_binding_problem(post, approval_key_mode(con))  # #7: unbound/changed/unkeyed
            if problem:
                raise ValidationError(binding_refusal(problem, entry["post_id"]) + ". Nothing scheduled")
            at = datetime.fromisoformat(entry["scheduled_utc"])
            if at.tzinfo is None or at.astimezone(timezone.utc) < current + timedelta(minutes=5):
                raise ValidationError("Proposed time no longer safely in the future; rerun the planner")
            at = at.astimezone(timezone.utc)
            if any(abs(at - old) < gap for old in reserved):
                raise ValidationError("New/existing schedule collision detected; nothing scheduled")
            reserved.append(at)
        for entry in entries:
            transition(con, entry["post_id"], "schedule", entry["scheduled_utc"])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {**proposal, "entries": [{**row, "schedule_status": "locally_scheduled"} for row in entries],
            "delivery_note": "Approved posts queued in local SQLite only. A trusted live worker, verified credentials, exact approval and real external receipts are required for publication."}
