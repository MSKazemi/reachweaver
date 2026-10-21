"""One parser for the times a person types when recording something: ``now``, ``yesterday``, ``2026-10-20 18:30``.

Used by ``metrics --observed-at/--window-start/--window-end`` and ``mark-shared --at``. It only reads what the
person typed; the stored value is always what it was before: an ISO 8601 time in UTC with its offset
(``2026-10-20T16:30:00+00:00``, as ``storage.parse_utc`` returns), so nothing downstream changes.

Accepted (case-insensitive, surrounding spaces ignored):

* ``now`` and ``today``: the current time.
* ``yesterday``: the same clock time one calendar day earlier, in the computer's time zone.
* ``YYYY-MM-DD``: 12:00 (midday) that day in the computer's time zone, or the current time if that is still
  in the future (a date alone carries no clock time; give ``YYYY-MM-DD HH:MM`` when the hour matters).
* ``YYYY-MM-DD HH:MM`` (``T`` instead of the space and ``:SS`` also work): that wall-clock time in the
  computer's time zone, following its daylight-saving rules for that date.
* Any ISO 8601 time with an explicit offset or ``Z`` (``2026-10-20T18:30:00+02:00``): exactly as before.

"The computer's time zone" is the one the operating system uses for local time (Python's
``datetime.astimezone()`` on a naive time); this module reads no environment variable itself. Tests pass
``tz`` to pin a zone. Files (``import-metrics`` CSV) keep requiring an explicit offset: a relative word in a
file would change meaning with the day it is imported.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone, tzinfo

from .policy import ValidationError
from .storage import parse_utc

FORMS = ("now, today, yesterday, YYYY-MM-DD, 'YYYY-MM-DD HH:MM' (your computer's time zone) "
         "or an ISO 8601 time with a timezone such as 2026-10-15T09:00:00+02:00")
MIDDAY = time(12, 0)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_LOCAL = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})[ T]([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?")
_MAX_INPUT = 64


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValidationError("now must include a timezone")
    return current


def _localize(naive: datetime, tz: tzinfo | None) -> datetime:
    """A naive wall-clock time in ``tz``, or in the operating system's local zone when ``tz`` is None."""
    try:
        return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
    except (OverflowError, OSError, ValueError) as exc:
        raise ValidationError("Time is out of range") from exc


def _iso(moment: datetime) -> str:
    try:
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, ValueError) as exc:
        raise ValidationError("Time is out of range") from exc


def parse_when(value: object, *, now: datetime | None = None, tz: tzinfo | None = None,
               what: str = "Time") -> str:
    """The typed time as ``storage.parse_utc`` output (UTC ISO 8601 with offset); ValidationError otherwise.

    ``what`` names the option in the error (``--observed-at``), ``now`` and ``tz`` make tests reproducible."""
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_INPUT:
        raise ValidationError(f"{what} is not understood; use {FORMS}")
    text = value.strip()
    word = text.casefold()
    current = _now(now)
    if word in ("now", "today"):
        return _iso(current)
    if word == "yesterday":
        local_now = current.astimezone(tz) if tz is not None else current.astimezone()
        wall = local_now.replace(tzinfo=None) - timedelta(days=1)
        return _iso(_localize(wall, tz))
    if _DATE.fullmatch(text):
        try:
            day = datetime.fromisoformat(text).date()
        except ValueError:
            raise ValidationError(f"{what} {text!r} is not a calendar date") from None
        moment = _localize(datetime.combine(day, MIDDAY), tz)
        return _iso(min(moment, current))
    local = _LOCAL.fullmatch(text)
    if local:
        date_text, hour, minute = local.group(1), int(local.group(2)), int(local.group(3))
        second = int(local.group(4) or 0)
        try:
            naive = datetime.fromisoformat(date_text).replace(hour=hour, minute=minute, second=second)
        except ValueError:
            raise ValidationError(f"{what} {text!r} is not a valid date and time") from None
        return _iso(_localize(naive, tz))
    try:
        return parse_utc(text)
    except ValidationError as exc:
        if "out of range" in str(exc):
            raise
        raise ValidationError(f"{what} {text[:40]!r} is not understood; use {FORMS}") from None
