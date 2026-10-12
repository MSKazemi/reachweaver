"""Load an owner-exported, first-party audience-activity CSV for advisory timing (issue #16).

The CSV layout is **ReachWeaver's own interchange format** (``reachweaver.audience-activity.v1``).
No real platform export has been verified against it: the owner copies hour-of-week "most active
times" / "followers online" numbers from a native analytics dashboard they are entitled to see
into this layout themselves. ReachWeaver never fetches, scrapes or verifies those numbers, and
"first party" here means *owner-reported from the owner's own dashboard*, not *checked*.

Rules: one row per (account_id, weekday, hour); one IANA timezone, one ``source`` and one
``as_of`` per account; ``activity`` is a non-negative number or empty/``unknown`` (kept as
missing, never zero-filled); hours with no row stay missing; text cells that a spreadsheet
would run as a formula are rejected (leading ``=``, ``+``, ``-``, ``@``, tab, CR, LF and their
full-width forms, per OWASP "CSV Injection",
https://community.owasp.org/attacks/CSV_Injection, accessed 2026-10-09).

UTC normalisation uses the UTC offsets of the calendar week (Monday to Sunday) that contains
``as_of``. That is an approximation: in a week with a DST change the weekly pattern is assumed
to follow the wall clock, a nonexistent local hour (spring-forward gap) has no UTC hour and is
not ranked, and an ambiguous local hour (fall-back fold) maps to its first occurrence
(``fold=0``, PEP 495). Nothing here schedules, approves or publishes.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .accounts import ACCOUNT_ID
from .policy import ValidationError
from .privacy import redact

FORMAT = "reachweaver.audience-activity.v1"
EVIDENCE = "first_party_audience_activity"
INTERCHANGE_NOTE = ("ReachWeaver's own interchange format; no platform export has been verified against it. "
                    "Values are owner-reported and not verified by ReachWeaver.")
REQUIRED_COLUMNS = ("account_id", "timezone", "weekday", "hour", "activity", "source", "as_of")
OPTIONAL_COLUMNS = ("consent", "notes")
MAX_BYTES = 1_048_576
MAX_ROWS = 10_000
MAX_SOURCE_CHARS = 200
MAX_NOTE_CHARS = 500
DEFAULT_TOP_HOURS = 5
DEFAULT_MAX_AGE_DAYS = 90
HOURS_PER_WEEK = 168
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WEEKDAYS = {name.lower(): i for i, name in enumerate(WEEKDAY_NAMES)}
_WEEKDAYS.update({name: i for i, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))})
_MISSING = {"", "unknown", "n/a", "na"}
# OWASP CSV Injection trigger characters (ASCII and full-width forms).
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_INT = re.compile(r"^[0-9]{1,2}$")
_NUMBER = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _text(value: str, column: str, line: int, *, limit: int, required: bool) -> str | None:
    if not value:
        if required:
            raise ValidationError(f"Audience activity line {line}: {column} is required")
        return None
    if value.startswith(_FORMULA_PREFIX):
        raise ValidationError(f"Audience activity line {line}: {column} starts with a spreadsheet formula "
                              "character (= + - @ tab CR LF); remove it (CSV injection guard)")
    if _CONTROL.search(value):
        raise ValidationError(f"Audience activity line {line}: {column} contains control characters")
    if len(value) > limit:
        raise ValidationError(f"Audience activity line {line}: {column} exceeds {limit} characters")
    return value


def _zone(value: str, line: int) -> ZoneInfo:
    if not value or value.startswith(_FORMULA_PREFIX):
        raise ValidationError(f"Audience activity line {line}: timezone must be an IANA name such as Europe/Rome")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError(f"Audience activity line {line}: unknown IANA timezone") from exc


def _weekday(value: str, line: int) -> int:
    if _INT.fullmatch(value) and int(value) <= 6:
        return int(value)
    if value.lower() in _WEEKDAYS:
        return _WEEKDAYS[value.lower()]
    raise ValidationError(f"Audience activity line {line}: weekday must be 0-6 (0=Mon) or Mon..Sun")


def _hour(value: str, line: int) -> int:
    if _INT.fullmatch(value) and int(value) <= 23:
        return int(value)
    raise ValidationError(f"Audience activity line {line}: hour must be an integer 0-23")


def _activity(value: str, line: int) -> float | None:
    if value.lower() in _MISSING:
        return None
    if value.startswith("-"):
        raise ValidationError(f"Audience activity line {line}: activity must not be negative")
    if not _NUMBER.fullmatch(value):
        raise ValidationError(f"Audience activity line {line}: activity must be a non-negative number, "
                              "empty or 'unknown'")
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"Audience activity line {line}: activity must be finite")
    return number


def _as_of(value: str, line: int, today: date) -> date:
    if not _DAY.fullmatch(value):
        raise ValidationError(f"Audience activity line {line}: as_of must be an ISO date YYYY-MM-DD")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Audience activity line {line}: as_of is not a valid date") from exc
    if day > today + timedelta(days=1):
        raise ValidationError(f"Audience activity line {line}: as_of is in the future")
    return day


def _read(path: Path, max_bytes: int) -> str:
    if path.stat().st_size > max_bytes:
        raise ValidationError(f"Audience activity file exceeds {max_bytes} bytes")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValidationError(f"Audience activity file exceeds {max_bytes} bytes")
    if b"\x00" in raw:
        raise ValidationError("Audience activity file contains NUL bytes; export it as UTF-8 CSV")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("Audience activity file must be UTF-8") from exc


def utc_hour_of_week(zone: ZoneInfo, as_of: date, weekday: int, hour: int) -> tuple[int | None, str]:
    """Map a local (weekday, hour) to a UTC hour-of-week (0 = Monday 00:00 UTC) for ``as_of``'s week.

    Returns ``(hour_of_week, dst)`` where ``dst`` is ``ok``, ``ambiguous_first_occurrence`` (fold=0
    was used) or ``nonexistent`` (spring-forward gap; hour_of_week is None).
    """
    day = as_of - timedelta(days=as_of.weekday()) + timedelta(days=weekday)
    wall = datetime.combine(day, time(hour))
    early = wall.replace(tzinfo=zone, fold=0)
    late = wall.replace(tzinfo=zone, fold=1)
    if early.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
        return None, "nonexistent"
    dst = "ok" if early.utcoffset() == late.utcoffset() else "ambiguous_first_occurrence"
    utc = early.astimezone(timezone.utc)
    return utc.weekday() * 24 + utc.hour, dst


def load_audience_activity(path, *, today: date | None = None, max_bytes: int = MAX_BYTES) -> dict:
    """Validate an owner-exported audience-activity CSV and normalise it per account (read-only).

    Raises ValidationError on the first problem, naming the CSV line. Returns ``{"format",
    "interchange_note", "accounts": {account_id: {...}}}``. Notes are only scanned for the
    ``synthetic`` marker and never echoed; ``source``/``consent`` are echoed after secret and
    e-mail redaction.
    """
    current = today or datetime.now(timezone.utc).date()
    reader = csv.reader(io.StringIO(_read(Path(path), max_bytes), newline=""))
    header = next(reader, None)
    if not header:
        raise ValidationError("Audience activity CSV is empty")
    columns = [name.strip().lower() for name in header]
    if len(columns) != len(set(columns)):
        raise ValidationError("Audience activity CSV has duplicate column names")
    unknown = [name for name in columns if name not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    missing = [name for name in REQUIRED_COLUMNS if name not in columns]
    if missing or unknown:
        raise ValidationError(f"Audience activity CSV ({FORMAT}) needs columns {', '.join(REQUIRED_COLUMNS)} "
                              f"(optional: {', '.join(OPTIONAL_COLUMNS)}); missing: {', '.join(missing) or 'none'}; "
                              f"unknown: {', '.join(unknown) or 'none'}")
    accounts: dict[str, dict] = {}
    seen: set[tuple[str, int, int]] = set()
    count = 0
    for line, cells in enumerate(reader, start=2):
        if not cells or all(not cell.strip() for cell in cells):
            continue
        count += 1
        if count > MAX_ROWS:
            raise ValidationError(f"Audience activity CSV exceeds {MAX_ROWS} rows")
        if len(cells) != len(columns):
            raise ValidationError(f"Audience activity line {line}: expected {len(columns)} cells, got {len(cells)}")
        row = {name: cell.strip() for name, cell in zip(columns, cells)}
        account_id = _text(row["account_id"], "account_id", line, limit=63, required=True) or ""
        if not ACCOUNT_ID.fullmatch(account_id):
            raise ValidationError(f"Audience activity line {line}: account_id must match {ACCOUNT_ID.pattern}")
        zone = _zone(row["timezone"], line)
        weekday, hour = _weekday(row["weekday"], line), _hour(row["hour"], line)
        activity = _activity(row["activity"], line)
        source = _text(row["source"], "source", line, limit=MAX_SOURCE_CHARS, required=True) or ""
        as_of = _as_of(row["as_of"], line, current)
        consent = _text(row.get("consent", ""), "consent", line, limit=MAX_NOTE_CHARS, required=False)
        notes = _text(row.get("notes", ""), "notes", line, limit=MAX_NOTE_CHARS, required=False)
        key = (account_id, weekday, hour)
        if key in seen:
            raise ValidationError(f"Audience activity line {line}: duplicate row for {account_id} "
                                  f"{WEEKDAY_NAMES[weekday]} {hour:02d}:00")
        seen.add(key)
        entry = accounts.setdefault(account_id, {
            "account_id": account_id, "timezone": zone.key, "source": source, "as_of": as_of,
            "consent": consent, "synthetic": False, "rows": [],
        })
        for label, value, expected in (("timezone", zone.key, entry["timezone"]), ("source", source, entry["source"]),
                                       ("as_of", as_of, entry["as_of"])):
            if value != expected:
                raise ValidationError(f"Audience activity line {line}: mixed {label} values for {account_id}; "
                                      "use one export (one timezone, source and as_of) per account")
        if consent and entry["consent"] is None:
            entry["consent"] = consent
        elif consent and consent != entry["consent"]:
            raise ValidationError(f"Audience activity line {line}: mixed consent values for {account_id}")
        if any("synthetic" in (text or "").lower() for text in (source, consent, notes)):
            entry["synthetic"] = True
        utc_how, dst = utc_hour_of_week(zone, as_of, weekday, hour)
        entry["rows"].append({"weekday": weekday, "hour": hour, "activity": activity,
                              "utc_hour_of_week": utc_how, "dst": dst})
    if not accounts:
        raise ValidationError("Audience activity CSV has no data rows")
    for entry in accounts.values():
        entry["rows"].sort(key=lambda r: (r["weekday"], r["hour"]))
        known = sum(r["activity"] is not None for r in entry["rows"])
        entry["coverage"] = {"rows": len(entry["rows"]), "known_hours": known,
                             "unknown_hours": len(entry["rows"]) - known,
                             "missing_hours": HOURS_PER_WEEK - len(entry["rows"])}
        entry["source"] = redact(entry["source"], emails=True)
        entry["consent"] = redact(entry["consent"], emails=True) if entry["consent"] else None
        entry["as_of"] = entry["as_of"].isoformat()
    return {"format": FORMAT, "interchange_note": INTERCHANGE_NOTE, "accounts": accounts}


def derive_slots(data: dict, account_id: str, *, target_timezone: str, top_hours: int = DEFAULT_TOP_HOURS,
                 today: date | None = None, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Top-``top_hours`` known, positive activity hours for one account as weekly planner slots.

    Slots are expressed in ``target_timezone`` (the planner's audience zone). When it differs from
    the CSV's zone they are converted through the ``as_of`` week's UTC offsets (an approximation
    across DST changes, reported in ``warnings``). Ties break by weekday then hour. When a DST
    fall-back maps two activity hours onto one local slot, the higher-ranked hour keeps it, the
    other is named in ``warnings`` and the next-ranked hour fills in; ``provenance.rank`` stays
    the hour's activity rank, so it can skip a number. Advisory only.
    """
    if type(top_hours) is not int or not 1 <= top_hours <= 24:
        raise ValidationError("--audience-top-hours must be 1..24")
    if type(max_age_days) is not int or not 1 <= max_age_days <= 3650:
        raise ValidationError("--audience-max-age-days must be 1..3650")
    entry = data["accounts"].get(account_id)
    if entry is None:
        raise ValidationError(f"Audience activity CSV has no rows for account {account_id}")
    try:
        target = ZoneInfo(target_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Unknown IANA timezone; install the system timezone database if needed") from exc
    current = today or datetime.now(timezone.utc).date()
    as_of = date.fromisoformat(entry["as_of"])
    age_days = (current - as_of).days
    stale = age_days > max_age_days
    warnings = [INTERCHANGE_NOTE, "Activity (audience presence) is not an outcome; it does not prove a time "
                "performs better. Validate with timing-evaluate/baseline-eval."]
    if stale:
        warnings.append(f"Stale audience activity: as_of {entry['as_of']} is {age_days} days old "
                        f"(threshold {max_age_days}); export a fresh snapshot before relying on it")
    if entry["synthetic"]:
        warnings.append("Audience activity is marked synthetic; never use it for a real account")
    skipped = [r for r in entry["rows"] if r["dst"] == "nonexistent"]
    if skipped:
        warnings.append(f"{len(skipped)} hour(s) do not exist in {entry['timezone']} during the as_of week "
                        "(DST gap) and were not ranked")
    if target.key != entry["timezone"]:
        warnings.append(f"CSV timezone {entry['timezone']} differs from --timezone {target.key}; slots were "
                        "converted with the as_of week's UTC offsets and may shift by an hour across DST changes")
    ranked = sorted((r for r in entry["rows"] if r["activity"] is not None and r["activity"] > 0
                     and r["utc_hour_of_week"] is not None),
                    key=lambda r: (-r["activity"], r["weekday"], r["hour"]))
    if not ranked:
        raise ValidationError(f"Audience activity for {account_id} has no known positive activity hours")
    zone = ZoneInfo(entry["timezone"])
    week = as_of - timedelta(days=as_of.weekday())
    slots: list[dict] = []
    kept: dict[tuple[int, time], dict] = {}

    def hour(r: dict) -> str:
        return f"{WEEKDAY_NAMES[r['weekday']]} {r['hour']:02d}:00 {entry['timezone']}"

    for rank, row in enumerate(ranked, start=1):
        if len(slots) == top_hours:
            break
        if target.key == entry["timezone"]:
            weekday, clock = row["weekday"], time(row["hour"])
        else:
            local = datetime.combine(week + timedelta(days=row["weekday"]), time(row["hour"]), tzinfo=zone)
            moved = local.astimezone(target)
            weekday, clock = moved.weekday(), time(moved.hour, moved.minute)
        winner = kept.get((weekday, clock))
        if winner is not None:
            # Across a DST fall-back two activity hours can land on one local wall-clock slot. The
            # first (higher-ranked) keeps it; the collision is reported and the next hour refills.
            warnings.append(f"Activity hour {hour(row)} (rank {rank}, activity {row['activity']:g}) maps to the "
                            f"same {target.key} slot {WEEKDAY_NAMES[weekday]} {clock.strftime('%H:%M')} as the "
                            f"higher-ranked {hour(winner)} (DST fall-back); it was dropped and the next-ranked "
                            "hour fills its place")
            continue
        kept[(weekday, clock)] = row
        slots.append({"weekday": weekday, "time": clock, "provenance": {
            "evidence": EVIDENCE, "format": FORMAT, "account_id": account_id,
            "source": entry["source"], "as_of": entry["as_of"], "age_days": age_days, "stale": stale,
            "activity_timezone": entry["timezone"], "activity_weekday": WEEKDAY_NAMES[row["weekday"]],
            "activity_hour": row["hour"], "activity": row["activity"], "rank": rank,
            "utc_hour_of_week": row["utc_hour_of_week"], "dst": row["dst"],
            "synthetic": entry["synthetic"], "consent": entry["consent"], "verified_by_reachweaver": False,
        }})
    return {
        "evidence": EVIDENCE, "format": FORMAT, "account_id": account_id, "source": entry["source"],
        "as_of": entry["as_of"], "age_days": age_days, "stale": stale, "max_age_days": max_age_days,
        "activity_timezone": entry["timezone"], "synthetic": entry["synthetic"], "consent": entry["consent"],
        "coverage": entry["coverage"], "top_hours": top_hours, "slots": slots, "warnings": warnings,
    }
