"""The one parser for typed recording times (reachweaver/when.py). Offline, deterministic: ``now`` and ``tz`` are pinned."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reachweaver.policy import ValidationError
from reachweaver.storage import parse_utc
from reachweaver.when import parse_when

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 21, 17, 30, 15, tzinfo=timezone.utc)  # 19:30 in Rome (CEST, +02:00)


def when(text: str, **kw) -> str:
    return parse_when(text, now=NOW, tz=ROME, **kw)


class WordsTests(unittest.TestCase):
    def test_now_and_today_are_the_current_time(self):
        for word in ("now", "NOW", " today "):
            self.assertEqual(when(word), "2026-10-21T17:30:15+00:00")

    def test_yesterday_keeps_the_local_clock_time_across_a_daylight_saving_change(self):
        self.assertEqual(when("yesterday"), "2026-10-20T17:30:15+00:00")
        after_switch = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # Rome switched to +01:00 on 2026-10-25
        self.assertEqual(parse_when("yesterday", now=after_switch, tz=ROME), "2026-10-25T09:00:00+00:00")
        before = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)  # 12:00 CET on the switch day
        self.assertEqual(parse_when("yesterday", now=before, tz=ROME), "2026-10-24T10:00:00+00:00")


class LocalTimeTests(unittest.TestCase):
    def test_date_and_time_are_read_in_the_local_zone(self):
        self.assertEqual(when("2026-10-20 18:30"), "2026-10-20T16:30:00+00:00")
        self.assertEqual(when("2026-10-20T18:30"), "2026-10-20T16:30:00+00:00")
        self.assertEqual(when("2026-10-20 08:05:09"), "2026-10-20T06:05:09+00:00")
        self.assertEqual(when("2026-01-15 18:30"), "2026-01-15T17:30:00+00:00")  # winter time, +01:00

    def test_a_date_alone_is_midday_local_but_never_in_the_future(self):
        self.assertEqual(when("2026-10-19"), "2026-10-19T10:00:00+00:00")
        morning = datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc)  # 09:00 in Rome: midday has not come yet
        self.assertEqual(parse_when("2026-10-21", now=morning, tz=ROME), "2026-10-21T07:00:00+00:00")

    def test_an_explicit_offset_is_kept_exactly_as_before(self):
        for text in ("2026-10-20T18:30:00+02:00", "2026-10-20T16:30:00Z", "2026-10-20T16:30:00+00:00"):
            self.assertEqual(when(text), parse_utc(text))
            self.assertEqual(when(text), "2026-10-20T16:30:00+00:00")

    def test_without_tz_the_operating_system_zone_is_used(self):
        expected = datetime(2026, 10, 20, 18, 30).astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")
        self.assertEqual(parse_when("2026-10-20 18:30", now=NOW), expected)

    def test_output_is_always_utc_iso_with_offset(self):
        for text in ("now", "yesterday", "2026-10-19", "2026-10-19 07:00", "2026-10-19T07:00:00-05:00"):
            out = when(text)
            self.assertTrue(out.endswith("+00:00"), out)
            self.assertEqual(parse_utc(out), out)


class RefusalTests(unittest.TestCase):
    def test_unclear_input_is_refused_with_the_accepted_forms(self):
        for bad in ("", "   ", "tomorrow", "last week", "2026-13-01", "2026-02-30 10:00", "2026-10-20 25:00",
                    "20/10/2026", "x" * 100, None, 5):
            with self.subTest(bad=bad), self.assertRaises(ValidationError) as ctx:
                when(bad, what="--observed-at")  # type: ignore[arg-type]
            if isinstance(bad, str) and bad.strip() and len(bad) < 60:
                self.assertIn("--observed-at", str(ctx.exception))

    def test_now_must_carry_a_timezone(self):
        with self.assertRaises(ValidationError):
            parse_when("now", now=datetime(2026, 10, 21, 12, 0))


if __name__ == "__main__":
    unittest.main()
