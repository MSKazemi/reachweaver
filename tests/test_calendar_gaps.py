"""Read-only calendar gap finder (public issue #9): no network, no writes, no scheduling."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from reachweaver.calendar_gaps import ADVISORY, find_calendar_gaps, render_text
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, connect

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def insert(db, pid, status="scheduled", at=None, account="personal", platform="bluesky"):
    db.execute("INSERT INTO posts(id,account_id,platform,content,status,created_at,scheduled_at) VALUES(?,?,?,?,?,?,?)",
               (pid, account, platform, "Useful release note.", status, "2026-10-01T00:00:00+00:00", at))


class GapFixture(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)

    def gaps(self, **kw):
        args = dict(account_id="personal", platform="bluesky", audience_timezone="Europe/Rome",
                    start="2026-10-12", days=7, now=NOW)
        args.update(kw)
        return find_calendar_gaps(self.db, **args)

    def dump(self):
        return list(self.db.iterdump())


class SlotStatusTests(GapFixture):
    def test_empty_calendar_lists_every_baseline_slot_as_empty(self):
        insert(self.db, "ready", status="approved")
        report = self.gaps()
        self.assertEqual(report["summary"]["candidate_slots"], 7)
        self.assertEqual((report["summary"]["empty"], report["summary"]["filled"]), (7, 0))
        self.assertEqual(report["summary"]["approved_unscheduled"], 1)
        self.assertEqual(report["slots"][0]["local"], "2026-10-12T18:00+02:00")
        self.assertEqual(report["evidence"], "industry_observational")

    def test_queued_post_near_a_slot_fills_it_within_tolerance_only(self):
        insert(self.db, "exact", at="2026-10-13T16:00:00+00:00")
        insert(self.db, "edge", at="2026-10-14T17:30:00+00:00")   # 90 min after the slot
        insert(self.db, "outside", at="2026-10-15T17:31:00+00:00")  # 91 min after the slot
        report = self.gaps()
        by_day = {row["local"][:10]: row for row in report["slots"]}
        self.assertEqual((by_day["2026-10-13"]["status"], by_day["2026-10-13"]["post_ids"]), ("filled", ["exact"]))
        self.assertEqual(by_day["2026-10-14"]["status"], "filled")
        self.assertEqual(by_day["2026-10-15"]["status"], "empty")
        self.assertEqual(report["off_slot_post_ids"], ["outside"])
        self.assertEqual(self.gaps(tolerance_minutes=120)["slots"][3]["status"], "filled")

    def test_slots_already_in_the_past_are_not_reported_as_empty(self):
        report = self.gaps(now=datetime(2026, 10, 14, 0, tzinfo=timezone.utc))
        statuses = [row["status"] for row in report["slots"]]
        self.assertEqual(statuses[:2], ["past", "past"])
        self.assertEqual(report["summary"]["past"], 2)
        self.assertEqual(report["summary"]["empty"], 5)

    def test_blackout_days_and_custom_slots(self):
        report = self.gaps(blackout=["2026-10-13"])
        self.assertEqual(report["summary"]["candidate_slots"], 6)
        custom = self.gaps(slots=["Wed@16:00"])
        self.assertEqual([row["local"] for row in custom["slots"]], ["2026-10-14T16:00+02:00"])
        self.assertEqual((custom["evidence"], custom["confidence"]), ("operator_supplied", "unverified"))

    def test_dst_gap_slot_is_skipped_and_named(self):
        report = self.gaps(slots=["Sun@02:30"], start="2026-03-29", days=1, now=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(report["slots"], [])
        self.assertEqual(report["dst_skipped_local"], ["2026-03-29T02:30"])

    def test_other_accounts_posts_are_ignored(self):
        insert(self.db, "theirs", at="2026-10-13T16:00:00+00:00", account="someone-else")
        self.assertEqual(self.gaps()["summary"]["filled"], 0)


class OverloadTests(GapFixture):
    def test_more_than_max_per_day_is_overloaded_and_threshold_is_configurable(self):
        for pid, at in (("a", "2026-10-13T07:00:00+00:00"), ("b", "2026-10-13T12:00:00+00:00"),
                        ("c", "2026-10-13T20:00:00+00:00")):
            insert(self.db, pid, at=at)
        report = self.gaps()
        self.assertEqual(report["overloaded_days"], [{"date": "2026-10-13", "count": 3, "post_ids": ["a", "b", "c"]}])
        self.assertEqual(self.gaps(max_per_day=3)["overloaded_days"], [])

    def test_posts_closer_than_the_spacing_are_flagged(self):
        insert(self.db, "a", at="2026-10-13T06:00:00+00:00")
        insert(self.db, "b", at="2026-10-13T16:00:00+00:00")   # 10 h later
        insert(self.db, "c", at="2026-10-14T17:00:00+00:00")   # 25 h after b
        report = self.gaps()
        self.assertEqual(report["close_pairs"], [{"post_ids": ["a", "b"], "hours_apart": 10.0}])
        self.assertEqual(self.gaps(min_spacing_hours=26)["summary"]["close_pairs"], 2)

    def test_posts_outside_the_window_do_not_count(self):
        for n, pid in enumerate("abc"):
            insert(self.db, pid, at=f"2026-11-0{n + 1}T10:00:00+00:00")
        report = self.gaps()
        self.assertEqual((report["summary"]["scheduled_in_window"], report["overloaded_days"]), (0, []))


class TimingAnnotationTests(GapFixture):
    def test_empty_slots_carry_matching_timing_evidence_but_filled_ones_do_not(self):
        insert(self.db, "a", at="2026-10-13T16:00:00+00:00")
        timing = {"cohort": {"account_id": "personal"}, "decision": "no_clear_separation",
                  "slots": [{"slot": "weekday_18_24", "posts": 4, "eligible_for_comparison": True, "median_rate": 0.031}]}
        report = self.gaps(timing=timing)
        by_day = {row["local"][:10]: row for row in report["slots"]}
        self.assertNotIn("timing_evidence", by_day["2026-10-13"])
        self.assertEqual(by_day["2026-10-12"]["timing_evidence"],
                         {"posts": 4, "eligible_for_comparison": True, "median_rate": 0.031,
                          "kind": "observational_association_only"})
        self.assertNotIn("timing_evidence", by_day["2026-10-17"])  # weekend: no weekend evidence supplied
        self.assertIn("not a best-time proof", report["timing"]["note"])


class ValidationTests(GapFixture):
    def test_bad_arguments_are_refused(self):
        for kw in ({"days": 0}, {"days": 61}, {"tolerance_minutes": 0}, {"max_per_day": 21},
                   {"min_spacing_hours": 169}, {"audience_timezone": "Mars/Base"}, {"start": "12-10-2026"}):
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                self.gaps(**kw)
        with self.assertRaises(ValidationError):
            self.gaps(account_id=" ")

    def test_platform_without_a_time_prior_needs_explicit_slots(self):
        with self.assertRaisesRegex(ValidationError, "No defensible general time prior"):
            self.gaps(platform="youtube")
        self.assertEqual(self.gaps(platform="youtube", slots=["Mon@10:00"])["evidence"], "operator_supplied")

    def test_audience_activity_rules(self):
        activity = {"account_id": "personal", "evidence": "owner_export", "slots": [
            {"weekday": 1, "time": datetime(2026, 1, 1, 9, 0).time(), "provenance": {"rank": 1}}]}
        report = self.gaps(audience_activity=activity)
        self.assertEqual(report["slots"][0]["provenance"], {"rank": 1})
        self.assertEqual(report["confidence"], "unverified")
        with self.assertRaises(ValidationError):
            self.gaps(audience_activity=activity, slots=["Mon@10:00"])
        with self.assertRaises(ValidationError):
            self.gaps(audience_activity={**activity, "account_id": "other"})

    def test_corrupt_schedule_in_state_is_refused_not_guessed(self):
        insert(self.db, "naive", at="2026-10-13T16:00:00")
        with self.assertRaisesRegex(ValidationError, "Invalid existing schedule"):
            self.gaps()


class ReadOnlyTests(GapFixture):
    def test_nothing_is_written_or_changed(self):
        insert(self.db, "a", at="2026-10-13T16:00:00+00:00")
        insert(self.db, "ready", status="approved")
        before = self.dump()
        self.gaps()
        self.assertEqual(self.dump(), before)

    def test_report_states_it_is_advisory(self):
        text = render_text(self.gaps())
        for line in ADVISORY:
            self.assertIn(line, text)
        self.assertIn("EMPTY", text)


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db_path = str(self.dir / "outbox.sqlite")
        self.config = self.dir / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_GAPS",
             "handle": "test.bsky.social"}]}), encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db_path, "--accounts", str(self.config), "calendar-gaps", *argv])
        return code, out.getvalue(), err.getvalue()

    def seed(self):
        with connect(self.db_path) as db:
            insert(db, "p1", at="2026-10-13T16:00:00+00:00", account="my-bluesky")
            insert(db, "p2", status="approved", account="my-bluesky")

    def test_json_report_and_database_untouched(self):
        self.seed()
        raw = Path(self.db_path).read_bytes()
        code, out, _ = self.run_cli("--account", "my-bluesky", "--timezone", "Europe/Rome", "--start", "2026-10-12",
                                    "--days", "7", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["summary"]["scheduled_in_window"], 1)
        self.assertEqual(report["summary"]["approved_unscheduled"], 1)
        self.assertEqual(Path(self.db_path).read_bytes(), raw)

    def test_text_output_is_default(self):
        self.seed()
        code, out, _ = self.run_cli("--account", "my-bluesky", "--timezone", "UTC", "--start", "2026-10-12")
        self.assertEqual(code, 0)
        self.assertIn("Calendar gaps for my-bluesky", out)

    def test_missing_database_is_an_error_and_is_not_created(self):
        code, _, err = self.run_cli("--account", "my-bluesky", "--timezone", "UTC")
        self.assertEqual(code, 2)
        self.assertFalse(Path(self.db_path).exists())
        self.assertTrue(err)

    def test_unknown_account_and_partial_timing_flags_are_refused(self):
        self.seed()
        code, _, err = self.run_cli("--account", "nobody", "--timezone", "UTC")
        self.assertEqual(code, 2)
        self.assertIn("Unknown calendar account", err)
        code, _, err = self.run_cli("--account", "my-bluesky", "--timezone", "UTC", "--timing-source", "native-export")
        self.assertEqual(code, 2)
        self.assertIn("go together", err)

    def test_timing_annotation_runs_through_the_cli(self):
        self.seed()
        code, out, _ = self.run_cli("--account", "my-bluesky", "--timezone", "UTC", "--start", "2026-10-12", "--json",
                                    "--timing-source", "native-export", "--timing-metric", "clicks",
                                    "--timing-denominator", "impressions")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["timing"]["decision"], "insufficient_comparable_slots")


if __name__ == "__main__":
    unittest.main()
