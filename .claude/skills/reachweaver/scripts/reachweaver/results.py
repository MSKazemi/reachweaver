"""Per-post results for people, read from the local database: shared by ``report`` (terminal summary), ``show``,
``next``, ``start`` and ``insights``. SELECT only; nothing here writes, sends or contacts a network.

Rules (the same as ``storage.report``): a post's numbers are its latest snapshot (inside the report window when
one is given); only EXACT counts are shown or added up; a missing count stays unknown and is never printed as 0.
"Interactions" here is likes + replies + reposts over the counts that were recorded for that post (the label
says so); ``insights`` keeps its stricter rule (all three exact at the matched age).

Post age: ``insights`` compares results about 7 days (168 h +/- 24 h) after posting. A post is *due* for its
7-day numbers once it is 6 days old and has no snapshot from that age on; a post whose snapshots are all earlier
than that has *early* results, and the plain-language note says when to record again.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, tzinfo

from .policy import ValidationError
from .storage import (MEASURED_STATUSES, _at_or_after, _has_table, _latest_snapshot_since, is_manual,
                      latest_snapshot, measured_posts, metric_states, parse_utc, snapshots_for)

INTERACTION_FIELDS = ("likes", "replies", "reposts")
# ``metrics`` without --source: the owner typed the numbers from the platform's own insights screen. An honest
# label for a hand-entered observation; it never claims an API or export source.
HAND_ENTERED_SOURCE = "entered-by-hand"
MATCH_AGE_HOURS = 168
MATCH_TOLERANCE_HOURS = 24
DUE_AFTER_HOURS = MATCH_AGE_HOURS - MATCH_TOLERANCE_HOURS  # 6 days
DUE_MAX_DAYS = 30  # older posts are not nagged about any more
USUAL_DAYS = 90
FIRST_WORDS = 40
NAMES = {"linkedin": "LinkedIn", "youtube": "YouTube", "tiktok": "TikTok", "x": "X", "dev": "DEV",
         "xiaohongshu": "Xiaohongshu", "sharechat": "ShareChat", "hackernews": "Hacker News"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")


def platform_name(platform: str) -> str:
    return NAMES.get(platform, platform[:1].upper() + platform[1:])


NO_ACCOUNT = "posted by you (no account)"


def account_label(account_id: object, handles: dict[str, str] | None = None) -> str:
    """``@handle`` when the accounts file gives one for this id, else the account id; a post recorded by hand
    without an account says so plainly (it was the owner's own post, just not tied to a configured account)."""
    if not account_id:
        return NO_ACCOUNT
    handle = (handles or {}).get(str(account_id))
    return f"@{handle}" if handle else str(account_id)


def account_handles(accounts: dict) -> dict[str, str]:
    """``account id -> public handle`` from loaded accounts (no '@', control characters removed); ids without one
    are left out."""
    out = {}
    for account_id, account in (accounts or {}).items():
        handle = str(account.get("handle") or account.get("channel_username") or "").strip().lstrip("@")
        if handle and not has_control(handle) and " " not in handle and len(handle) <= 100:
            out[str(account_id)] = handle
    return out


def has_control(text: str) -> bool:
    """True when ``text`` holds a control or bidi-override character (never put such text in a shell command)."""
    return bool(_CONTROL.search(text))


def first_words(text: object, limit: int = FIRST_WORDS) -> str:
    """The start of a post's text on one line: control and bidi characters removed, cut at a word with "…"."""
    flat = " ".join(_CONTROL.sub(" ", str(text or "")).split())
    if not flat:
        return "(no text)"
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0] if " " in flat[:limit] else flat[:limit]
    return cut.rstrip(",.;:-") + "…"


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")  # fixed: no locale


def has_text(post) -> bool:
    content = post["content"] if "content" in post.keys() else ""
    return bool(" ".join(str(content or "").split()))


def post_title(post) -> str:
    """How a post is named in summaries: its first words, or for a post without text (``reachweaver posted URL``
    without --text) its network and day, e.g. ``Instagram post of 18 Oct`` (2026-10-21: a link fragment such as
    ``instagram.com/…/DemoPost123`` did not tell the person which post it was)."""
    if has_text(post):
        return first_words(post["content"])
    platform = post["platform"] if "platform" in post.keys() else ""
    posted = post["send_started_at"] if "send_started_at" in post.keys() else None
    return untitled_label(str(platform or ""), posted)


def untitled_label(platform: str, posted_at: object, now: datetime | None = None) -> str:
    """``Instagram post of 18 Oct`` (the year is added when it is not this year); ``Instagram post`` without a
    time. The day is in the computer's time zone, as every other date shown to the person."""
    name = platform_name(platform) if platform else "Your"
    moment = to_time(posted_at)
    if moment is None:
        return f"{name} post"
    local = moment.astimezone()
    this_year = (now or datetime.now(local.tzinfo)).astimezone().year
    day = f"{local.day} {MONTHS[local.month - 1]}" + (f" {local.year}" if local.year != this_year else "")
    return f"{name} post of {day}"


def quoted_title(row: dict) -> str:
    """A row's name for a sentence: first words in quotes; a network-and-day label (no text) as it is."""
    return f"\"{row['first_words']}\"" if row.get("titled", True) else str(row["first_words"])


def to_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(parse_utc(value)) if isinstance(value, str) and value else None
    except (ValidationError, ValueError):
        return None


def hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def local_date(moment: datetime, tz: tzinfo | None = None) -> str:
    """``YYYY-MM-DD`` in ``tz`` (default: the computer's own time zone)."""
    return (moment.astimezone(tz) if tz is not None else moment.astimezone()).date().isoformat()


def metrics_command(post_id: str) -> str:
    return f"reachweaver metrics {post_id} --likes N --replies N --reposts N"


def _numbers(snapshot) -> tuple[dict[str, int], int | None]:
    exact = {key: int(value) for key, (label, value) in metric_states(snapshot).items()
             if label == "exact" and value is not None}
    recorded = [exact[key] for key in INTERACTION_FIELDS if key in exact]
    return exact, (sum(recorded) if recorded else None)


def post_results(con: sqlite3.Connection, *, since: str | None = None) -> list[dict]:
    """One row per measured post (replies left out, as in ``report``), with its latest exact numbers.

    ``since`` (UTC ISO, from ``report_since``) keeps posts sent at or after it and snapshots observed since."""
    window = to_time(since) if since is not None else None
    rows = []
    for post in measured_posts(con, include_replies=False):
        if window is not None and not _at_or_after(post["send_started_at"], window):
            continue
        snapshot = latest_snapshot(con, post["id"]) if window is None else _latest_snapshot_since(con, post["id"], window)
        exact, interactions = _numbers(snapshot)
        sent = to_time(post["send_started_at"])
        observed = to_time(snapshot["observed_at"]) if snapshot is not None else None
        rows.append({
            "post_id": post["id"], "platform": post["platform"], "account_id": post["account_id"] or None,
            "posted_by_hand": is_manual(post), "posted_at": post["send_started_at"],
            "first_words": post_title(post), "titled": has_text(post),
            "observed_at": snapshot["observed_at"] if snapshot is not None else None,
            "source": snapshot["source"] if snapshot is not None else None,
            "hours_after_posting": (round(hours_between(sent, observed)) if sent and observed else None),
            "numbers": exact, "interactions": interactions})
    return rows


WEEK_SO_FAR = "This week so far"


def week_summary(con: sqlite3.Connection, *, now: str | None = None, head: str = WEEK_SO_FAR) -> str:
    """The first line of ``report --week`` (same window, same ``storage.report`` totals, same ``post_results``
    ranking, same ``report_format.summary_line``) under another heading. ``posted`` prints it at a terminal so the
    person sees how the week is going without a second command. SELECT only."""
    from .report_format import summary_line
    from .storage import report, report_since
    since = report_since("7d", now)
    return summary_line({"platforms": report(con, since=since)}, post_results(con, since=since), head=head)


def ranked(rows: list[dict]) -> list[dict]:
    """Posts with recorded interactions, highest first (ties: newest first, then id)."""
    scored = [row for row in rows if row["interactions"] is not None]
    scored.sort(key=lambda row: row["post_id"])
    scored.sort(key=lambda row: row["posted_at"] or "", reverse=True)
    scored.sort(key=lambda row: row["interactions"], reverse=True)
    return scored


SINGULAR = {"likes": "like", "replies": "reply", "reposts": "repost", "views": "view"}  # "1 reply", not "1 replie"


def numbers_text(numbers: dict[str, int], keys=("likes", "replies", "reposts", "views")) -> str:
    parts = [f"{numbers[key]} {key if numbers[key] != 1 else SINGULAR.get(key, key)}" for key in keys if key in numbers]
    return ", ".join(parts) if parts else "no exact counts"


def snapshot_ages(con: sqlite3.Connection, post_id: str, sent: datetime) -> list[float]:
    ages = []
    for row in snapshots_for(con, post_id):
        observed = to_time(row["observed_at"])
        if observed is not None:
            ages.append(hours_between(sent, observed))
    return ages


def results_due(con: sqlite3.Connection, now: datetime) -> list[dict]:
    """Posts 6..30 days old with no snapshot from day 6 on: the ones to record the 7-day numbers for. Oldest first."""
    due = []
    for post in measured_posts(con, include_replies=False):
        sent = to_time(post["send_started_at"])
        if sent is None:
            continue
        age = hours_between(sent, now)
        if not DUE_AFTER_HOURS <= age <= DUE_MAX_DAYS * 24:
            continue
        if any(hours >= DUE_AFTER_HOURS for hours in snapshot_ages(con, post["id"], sent)):
            continue
        due.append({"post_id": post["id"], "platform": post["platform"], "account_id": post["account_id"] or None,
                    "first_words": post_title(post), "titled": has_text(post), "posted_at": post["send_started_at"],
                    "days_since_posting": int(age // 24), "command": metrics_command(post["id"])})
    due.sort(key=lambda item: (item["posted_at"], item["post_id"]))
    return due


def early_note(entries: list[tuple[datetime, list[float]]], now: datetime, *, age_hours: int = MATCH_AGE_HOURS,
               tolerance_hours: int = MATCH_TOLERANCE_HOURS, tz: tzinfo | None = None) -> dict | None:
    """For posts that have results, but none at the matched age (``age_hours`` +/- ``tolerance_hours``): how many
    hours after posting they were recorded, and when to record again.

    ``entries`` holds, per post, its send time and the post age (hours) of each snapshot. Posts without a
    snapshot, or with one inside the matched window, are left out. Returns None when no post is outside it.
    When some of those posts were recorded too early and can still be recorded at the matched age, the note is
    about those only (they are the ones the person can act on). ``record_again_around`` is the earliest matched date still ahead (or within its tolerance) for a post
    recorded too early; None when every such date has passed. ``suggested_age_hours`` is the middle recorded
    age, kept inside what ``insights --age-hours`` accepts (25..720 with the default 24 h tolerance)."""
    low, high = age_hours - tolerance_hours, age_hours + tolerance_hours
    outside: list[tuple[datetime, float, bool]] = []
    for sent, ages in entries:
        if ages and not any(low <= hours <= high for hours in ages):
            outside.append((sent, max(ages), all(hours < low for hours in ages)))
    if not outside:
        return None
    actionable = [item for item in outside if item[2] and item[0] + timedelta(hours=high) >= now]
    if actionable:  # posts that can still get a matched-age result: the note is about them
        outside = actionable
    hours = sorted(round(latest) for _, latest, _ in outside)
    again = min(sent + timedelta(hours=age_hours) for sent, _, _ in actionable) if actionable else None
    middle = hours[len(hours) // 2]
    return {"posts": len(outside), "hours_after_posting": hours,
            "record_again_around": local_date(max(again, now), tz) if again is not None else None,
            "suggested_age_hours": min(720, max(25, middle)),
            "matched_age_hours": age_hours}


def early_note_text(note: dict, *, age_hint: str = "use --age-hours {n}") -> str:
    """The plain sentence: when the results were recorded, when to record again, the --age-hours option."""
    hours = note["hours_after_posting"]
    when = f"{hours[0]} hours" if hours[0] == hours[-1] else f"between {hours[0]} and {hours[-1]} hours"
    age = note["matched_age_hours"]
    target = "about 7 days" if age == MATCH_AGE_HOURS else f"about {age} hours"
    text = f"You recorded results {when} after posting; insights compares results {target} after posting. "
    hint = age_hint.format(n=note["suggested_age_hours"])
    if note["record_again_around"]:
        return text + f"Record again around {note['record_again_around']} or {hint}."
    return text + f"To compare the results you have, {hint}."


def early_note_advice(note: dict, *, by_hand: bool) -> str:
    """``early_note`` as plain advice for the terminal ``report`` (UX 2026-10-21): when to record again and the
    command that records numbers, instead of an ``insights --age-hours`` option. ``by_hand``: every post the note
    is about was recorded with ``posted``, so ``posted LINK`` updates its numbers; otherwise ``metrics``."""
    hours = note["hours_after_posting"]
    if hours[-1] == 0:
        when = "right after posting"
    elif hours[0] == hours[-1]:
        when = f"{hours[0]} hour{'s' if hours[0] != 1 else ''} after posting"
    else:
        when = f"between {hours[0]} and {hours[-1]} hours after posting"
    days = "7 days" if note["matched_age_hours"] == MATCH_AGE_HOURS else f"{note['matched_age_hours']} hours"
    record = ("reachweaver posted LINK --likes N" if by_hand
              else "reachweaver metrics POST_ID --likes N ('reachweaver list' shows the post ids)")
    text = f"You recorded these numbers {when}. "
    if note["record_again_around"]:
        return text + f"Record numbers again around {note['record_again_around']} to compare after {days}: {record}"
    return (text + f"Comparing posts uses the numbers about {days} after posting; to compare the ones you have, "
            f"run: reachweaver insights --age-hours {note['suggested_age_hours']}")


def early_note_for(con: sqlite3.Connection, rows: list[dict], now: datetime, tz: tzinfo | None = None) -> dict | None:
    """``early_note`` for the posts in ``rows`` (``post_results`` output)."""
    entries = []
    for row in rows:
        sent = to_time(row["posted_at"])
        if sent is not None and row["observed_at"] is not None:
            entries.append((sent, snapshot_ages(con, row["post_id"], sent)))
    return early_note(entries, now, tz=tz)


def last_posted(con: sqlite3.Connection) -> dict[str, datetime]:
    """Latest time you posted per platform: posts sent or recorded as posted by hand, plus material recorded with
    ``mark-shared`` (its declared time, else when it was recorded)."""
    latest: dict[str, datetime] = {}

    def see(platform: str, moment: datetime | None) -> None:
        if moment is not None and (platform not in latest or moment > latest[platform]):
            latest[platform] = moment

    marks = ",".join("?" for _ in MEASURED_STATUSES)
    for row in con.execute(f"SELECT platform, send_started_at FROM posts WHERE status IN ({marks})",
                           MEASURED_STATUSES):
        see(row["platform"], to_time(row["send_started_at"]))
    if _has_table(con, "shared_material"):
        for row in con.execute("SELECT platform, posted_at, recorded_at FROM shared_material"):
            see(row["platform"], to_time(row["posted_at"]) or to_time(row["recorded_at"]))
    return latest


def usual_networks(con: sqlite3.Connection, now: datetime, *, limit: int = 3) -> list[str]:
    """The networks you posted on most in the last 90 days (ties: most recent first)."""
    counts: dict[str, int] = {}
    recent: dict[str, datetime] = {}
    start = now - timedelta(days=USUAL_DAYS)

    def see(platform: str, moment: datetime | None) -> None:
        if moment is not None and moment >= start:
            counts[platform] = counts.get(platform, 0) + 1
            recent[platform] = max(recent.get(platform, moment), moment)

    marks = ",".join("?" for _ in MEASURED_STATUSES)
    for row in con.execute(f"SELECT platform, send_started_at FROM posts WHERE status IN ({marks})",
                           MEASURED_STATUSES):
        see(row["platform"], to_time(row["send_started_at"]))
    if _has_table(con, "shared_material"):  # by-hand posts with --url are in both tables: count them once
        for row in con.execute("SELECT platform, posted_at, recorded_at FROM shared_material "
                               "WHERE post_id IS NULL OR post_id=''" if _has_column(con, "shared_material", "post_id")
                               else "SELECT platform, posted_at, recorded_at FROM shared_material"):
            see(row["platform"], to_time(row["posted_at"]) or to_time(row["recorded_at"]))
    order = sorted(counts, key=lambda p: (-counts[p], -recent[p].timestamp(), p))
    return order[:limit]


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in con.execute(f"PRAGMA table_info({table})"))


def latest_by_source(con: sqlite3.Connection, post) -> list[dict]:
    """``show``: the latest snapshot of one post per source label, newest first, exact counts only."""
    sent = to_time(post["send_started_at"])
    latest: dict[str, sqlite3.Row] = {}
    for row in snapshots_for(con, post["id"]):
        kept = latest.get(row["source"])
        if kept is None or (row["observed_at"], row["id"]) > (kept["observed_at"], kept["id"]):
            latest[row["source"]] = row
    out = []
    for row in sorted(latest.values(), key=lambda r: (r["observed_at"], r["id"]), reverse=True):
        exact, interactions = _numbers(row)
        observed = to_time(row["observed_at"])
        states = metric_states(row)
        out.append({"observed_at": row["observed_at"], "source": row["source"], "numbers": exact,
                    "other_states": {key: label for key, (label, _) in states.items()
                                     if label not in ("exact", "unknown")},
                    "interactions_recorded": interactions,
                    "hours_after_posting": (round(hours_between(sent, observed)) if sent and observed else None)})
    return out
