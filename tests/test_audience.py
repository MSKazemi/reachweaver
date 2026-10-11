"""Offline tests for the first-party audience-activity import and its calendar-plan integration (#16)."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock

from reachweaver import audience
from reachweaver.audience import derive_slots, load_audience_activity, utc_hour_of_week
from reachweaver.calendar import propose_calendar
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, approval_payload_sha256, connect, transition
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/audience-activity.example.csv"
GOLDEN = ROOT / "tests/fixtures/calendar_no_audience.golden.json"
TODAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
HEADER = "account_id,timezone,weekday,hour,activity,source,as_of"


def row(weekday="Mon", hour=19, activity="10", *, account="my-bluesky", tz="Europe/Rome",
        source="Owner dashboard export", as_of="2026-10-05"):
    return f"{account},{tz},{weekday},{hour},{activity},{source},{as_of}"


class CsvCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, *lines: str, header: str = HEADER) -> Path:
        path = Path(self.tmp.name) / "activity.csv"
        path.write_text("\n".join((header, *lines)) + "\n", encoding="utf-8")
        return path

    def load(self, *lines: str, header: str = HEADER, **kw) -> dict:
        return load_audience_activity(self.write(*lines, header=header), today=TODAY, **kw)


class LoaderTests(CsvCase):
    def test_example_is_synthetic_and_keeps_missing_hours_missing(self):
        data = load_audience_activity(EXAMPLE, today=TODAY)
        entry = data["accounts"]["my-bluesky"]
        self.assertTrue(entry["synthetic"])
        self.assertIn("SYNTHETIC", entry["source"])
        self.assertEqual(entry["coverage"], {"rows": 71, "known_hours": 70, "unknown_hours": 1, "missing_hours": 97})
        unknown = [r for r in entry["rows"] if r["activity"] is None]
        self.assertEqual([(r["weekday"], r["hour"]) for r in unknown], [(2, 23)])
        self.assertNotIn((0, 3), {(r["weekday"], r["hour"]) for r in entry["rows"]})  # absent, not zero
        self.assertIn("interchange format", data["interchange_note"])

    def test_weekday_forms_and_missing_activity_tokens(self):
        data = self.load(row("0", 1, ""), row("tuesday", 2, "unknown"), row("WED", 3, "N/A"), row("6", 4, "2.5"))
        rows = data["accounts"]["my-bluesky"]["rows"]
        self.assertEqual([(r["weekday"], r["activity"]) for r in rows], [(0, None), (1, None), (2, None), (6, 2.5)])
        self.assertEqual(data["accounts"]["my-bluesky"]["coverage"]["known_hours"], 1)

    def test_validation_errors(self):
        cases = {
            "duplicate row": (row("Mon", 19), row("0", 19)),
            "mixed timezone": (row("Mon", 19), row("Tue", 19, tz="Europe/Berlin")),
            "mixed as_of": (row("Mon", 19), row("Tue", 19, as_of="2026-10-01")),
            "mixed source": (row("Mon", 19), row("Tue", 19, source="Other export")),
            "not be negative": (row(activity="-1"),),
            "non-negative number": (row(activity="nan"),),
            "non-negative number ": (row(activity="1e3"),),
            "weekday must be": (row(weekday="7"),),
            "weekday must be ": (row(weekday="Funday"),),
            "hour must be": (row(hour=24),),
            "hour must be ": (row(hour="9.5"),),
            "unknown IANA": (row(tz="Mars/Olympus"),),
            "in the future": (row(as_of="2026-12-01"),),
            "ISO date": (row(as_of="05/10/2026"),),
            "not a valid date": (row(as_of="2026-02-30"),),
            "account_id must match": (row(account="Bad_ID"),),
            "source is required": (row(source=""),),
            "expected 7 cells": ("my-bluesky,Europe/Rome,Mon,19,10,src",),
            "no data rows": (),
        }
        for message, lines in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message.strip()):
                self.load(*lines)

    def test_header_problems(self):
        with self.assertRaisesRegex(ValidationError, "missing: as_of"):
            self.load(header="account_id,timezone,weekday,hour,activity,source")
        with self.assertRaisesRegex(ValidationError, "unknown: followers"):
            self.load(header=HEADER + ",followers")
        with self.assertRaisesRegex(ValidationError, "duplicate column"):
            self.load(header=HEADER + ",hour")
        path = Path(self.tmp.name) / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "empty"):
            load_audience_activity(path, today=TODAY)

    def test_formula_injection_in_text_cells_is_rejected(self):
        header = HEADER + ",consent,notes"
        for payload in ("=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "＝cmd", "\t=1"):
            for column in ("source", "consent", "notes"):
                values = {"source": "Owner export", "consent": "owner", "notes": "ok", column: payload}
                line = row(source=values["source"]) + f",{values['consent']},{values['notes']}"
                with self.subTest(payload=payload, column=column), \
                        self.assertRaisesRegex(ValidationError, f"{column} starts with a spreadsheet formula"):
                    self.load(line, header=header)
        with self.assertRaisesRegex(ValidationError, "account_id starts with a spreadsheet formula"):
            self.load(row(account="=cmd"))
        with self.assertRaisesRegex(ValidationError, "timezone must be an IANA"):
            self.load(row(tz="=Europe/Rome"))
        with self.assertRaisesRegex(ValidationError, "control characters"):
            self.load(row(source="Owner\x07export"))

    def test_size_row_and_encoding_limits(self):
        path = self.write(row())
        with self.assertRaisesRegex(ValidationError, "exceeds 32 bytes"):
            load_audience_activity(path, today=TODAY, max_bytes=32)
        with mock.patch.object(audience, "MAX_ROWS", 2), self.assertRaisesRegex(ValidationError, "exceeds 2 rows"):
            self.load(row("Mon"), row("Tue"), row("Wed"))
        path.write_bytes(HEADER.encode() + b"\nmy-bluesky,Europe/Rome,Mon,1,1,src\x00,2026-10-05\n")
        with self.assertRaisesRegex(ValidationError, "NUL"):
            load_audience_activity(path, today=TODAY)
        path.write_bytes(HEADER.encode() + b"\nmy-bluesky,Europe/Rome,Mon,1,1,src\xff,2026-10-05\n")
        with self.assertRaisesRegex(ValidationError, "UTF-8"):
            load_audience_activity(path, today=TODAY)

    def test_notes_never_echoed_and_source_redacted(self):
        header = HEADER + ",notes"
        data = self.load(row(source="Export by owner@example.com") + ",private remark about a follower",
                         header=header)
        text = json.dumps(data)
        self.assertNotIn("owner@example.com", text)
        self.assertNotIn("private remark", text)
        self.assertFalse(data["accounts"]["my-bluesky"]["synthetic"])


class DstNormalisationTests(unittest.TestCase):
    rome = ZoneInfo("Europe/Rome")

    def test_ordinary_week_offsets(self):
        # 2026-10-05 is a Monday in CEST (+02:00): Mon 19:00 local = Mon 17:00 UTC.
        self.assertEqual(utc_hour_of_week(self.rome, date(2026, 10, 5), 0, 19), (17, "ok"))
        # Mon 01:00 CEST is Sunday 23:00 UTC: the hour of week wraps to the end of the week.
        self.assertEqual(utc_hour_of_week(self.rome, date(2026, 10, 8), 0, 1), (6 * 24 + 23, "ok"))

    def test_spring_forward_gap_and_offset_change_in_the_as_of_week(self):
        as_of = date(2026, 3, 29)  # Sunday; Rome moves 02:00 CET -> 03:00 CEST
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 0, 9), (8, "ok"))            # Monday, CET
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 6, 1), (6 * 24 + 0, "ok"))    # Sunday CET
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 6, 2), (None, "nonexistent"))
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 6, 3), (6 * 24 + 1, "ok"))    # Sunday CEST

    def test_fall_back_fold_uses_first_occurrence(self):
        as_of = date(2026, 10, 25)  # Sunday; 03:00 CEST -> 02:00 CET, so 02:00 happens twice
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 6, 2), (6 * 24 + 0, "ambiguous_first_occurrence"))
        self.assertEqual(utc_hour_of_week(self.rome, as_of, 6, 3), (6 * 24 + 2, "ok"))

    def test_new_york_uses_its_own_transition_week(self):
        ny = ZoneInfo("America/New_York")
        self.assertEqual(utc_hour_of_week(ny, date(2026, 3, 8), 6, 2), (None, "nonexistent"))
        self.assertEqual(utc_hour_of_week(ny, date(2026, 3, 29), 6, 2), (6 * 24 + 6, "ok"))  # EDT


class DeriveSlotsTests(CsvCase):
    def test_top_hours_ranked_with_deterministic_ties_and_full_provenance(self):
        data = self.load(row("Mon", 19, "50"), row("Tue", 19, "50"), row("Wed", 8, "70"), row("Thu", 9, "0"),
                         row("Fri", 10, ""), row("Sat", 11, "5"))
        result = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", top_hours=3, today=TODAY)
        self.assertEqual([(s["weekday"], s["time"]) for s in result["slots"]],
                         [(2, time(8)), (0, time(19)), (1, time(19))])
        for rank, slot in enumerate(result["slots"], start=1):
            p = slot["provenance"]
            self.assertEqual((p["evidence"], p["source"], p["as_of"], p["rank"]),
                             ("first_party_audience_activity", "Owner dashboard export", "2026-10-05", rank))
            self.assertFalse(p["verified_by_reachweaver"])
        # Zero and unknown hours are never candidates, even when fewer known hours exist than requested.
        many = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", top_hours=24, today=TODAY)
        self.assertEqual(len(many["slots"]), 4)

    def test_staleness_threshold(self):
        data = self.load(row(as_of="2026-06-01"))
        stale = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["age_days"], 130)
        self.assertTrue(stale["slots"][0]["provenance"]["stale"])
        self.assertTrue(any(w.startswith("Stale audience activity") for w in stale["warnings"]))
        fresh = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY, max_age_days=200)
        self.assertFalse(fresh["stale"])
        self.assertFalse(any(w.startswith("Stale") for w in fresh["warnings"]))

    def test_nonexistent_dst_hour_is_not_ranked(self):
        data = self.load(row("Sun", 2, "999", as_of="2026-03-29"), row("Sun", 3, "5", as_of="2026-03-29"))
        result = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY, max_age_days=365)
        self.assertEqual([(s["weekday"], s["time"]) for s in result["slots"]], [(6, time(3))])
        self.assertTrue(any("DST gap" in w for w in result["warnings"]))

    def test_conversion_to_a_different_planner_zone(self):
        data = self.load(row("Fri", 19, "9"), row("Mon", 1, "8"))
        result = derive_slots(data, "my-bluesky", target_timezone="America/New_York", today=TODAY)
        # Rome is UTC+2 and New York UTC-4 in the as_of week: 6 hours apart, crossing midnight back.
        self.assertEqual([(s["weekday"], s["time"]) for s in result["slots"]], [(4, time(13)), (6, time(19))])
        self.assertTrue(any("differs from --timezone" in w for w in result["warnings"]))

    def test_fall_back_collision_keeps_the_higher_ranked_hour_and_refills(self):
        # Review finding (2026-10-09): in the Europe/Rome fall-back week (Sun 2026-10-25), UTC Sun 00:00
        # (02:00 CEST) and UTC Sun 01:00 (02:00 CET) both land on local Sun 02:00; the lower-ranked hour's
        # provenance overwrote the top hour's and only N-1 distinct windows remained.
        rows = (row("Sun", 0, "100", tz="UTC", as_of="2026-10-21"), row("Sun", 1, "90", tz="UTC", as_of="2026-10-21"),
                row("Wed", 9, "10", tz="UTC", as_of="2026-10-21"), row("Thu", 9, "5", tz="UTC", as_of="2026-10-21"))
        data = load_audience_activity(self.write(*rows), today=date(2026, 10, 22))
        result = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=date(2026, 10, 22), top_hours=3)
        self.assertEqual([(s["weekday"], s["time"], s["provenance"]["rank"], s["provenance"]["activity"])
                          for s in result["slots"]],
                         [(6, time(2), 1, 100.0), (2, time(11), 3, 10.0), (3, time(11), 4, 5.0)])
        dropped = [w for w in result["warnings"] if "DST fall-back" in w]
        self.assertEqual(dropped, ["Activity hour Sun 01:00 UTC (rank 2, activity 90) maps to the same Europe/Rome "
                                   "slot Sun 02:00 as the higher-ranked Sun 00:00 UTC (DST fall-back); it was dropped "
                                   "and the next-ranked hour fills its place"])
        # With nothing left to refill, the plan has fewer slots rather than a silently wrong one.
        data = load_audience_activity(self.write(*rows[:2]), today=date(2026, 10, 22))
        result = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=date(2026, 10, 22), top_hours=2)
        self.assertEqual([(s["weekday"], s["time"], s["provenance"]["rank"]) for s in result["slots"]],
                         [(6, time(2), 1)])
        self.assertTrue(any("DST fall-back" in w for w in result["warnings"]))

    def test_refusals(self):
        data = self.load(row(activity="0"), row("Tue", 1, "", account="other-account"))
        with self.assertRaisesRegex(ValidationError, "no known positive activity"):
            derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY)
        with self.assertRaisesRegex(ValidationError, "no rows for account"):
            derive_slots(data, "missing-account", target_timezone="Europe/Rome", today=TODAY)
        for top in (0, 25, True):
            with self.subTest(top=top), self.assertRaisesRegex(ValidationError, "top-hours"):
                derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY, top_hours=top)
        with self.assertRaisesRegex(ValidationError, "max-age-days"):
            derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY, max_age_days=0)


class PlannerIntegrationTests(CsvCase):
    def setUp(self):
        super().setUp()
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)
        for pid in ("a", "b", "c"):
            self.db.execute("INSERT INTO posts(id,account_id,platform,content,status,created_at) VALUES(?,?,?,?,?,?)",
                            (pid, "my-bluesky", "bluesky", "Useful note.", "approved", "2026-10-01T00:00:00+00:00"))
            post = self.db.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
            self.db.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?", (approval_payload_sha256(post), pid))

    def state(self):
        return [tuple(r) for r in self.db.execute("SELECT id,status,scheduled_at,approved_payload_sha256 FROM posts")]

    def test_every_entry_carries_provenance_and_planner_stays_advisory(self):
        before = self.state()
        data = load_audience_activity(EXAMPLE, today=TODAY)
        slots = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=TODAY, top_hours=3)
        plan = propose_calendar(self.db, account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
                                start="2026-10-12", days=14, now=NOW, audience_activity=slots)
        self.assertEqual(len(plan["entries"]), 3)
        for entry in plan["entries"]:
            self.assertEqual(entry["schedule_status"], "proposed_only")
            self.assertEqual((entry["evidence"], entry["confidence"], entry["evidence_url"]),
                             ("first_party_audience_activity", "unverified", None))
            self.assertEqual(entry["provenance"]["as_of"], "2026-10-05")
            self.assertIn("SYNTHETIC", entry["provenance"]["source"])
            local = datetime.fromisoformat(entry["scheduled_local"])
            self.assertEqual((local.weekday(), local.hour),
                             (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun").index(
                                 entry["provenance"]["activity_weekday"]), entry["provenance"]["activity_hour"]))
        summary = plan["audience_activity"]
        self.assertEqual(summary["candidate_slots"], ["Fri@19:00", "Thu@19:00", "Fri@20:00"])
        self.assertNotIn("slots", summary)
        self.assertIn("never approves, schedules or publishes", summary["advisory_only"])
        self.assertEqual(self.state(), before)

    def test_dst_gap_occurrence_is_skipped_not_fatal(self):
        data = self.load(row("Sun", 2, "90", as_of="2026-03-16"), row("Mon", 9, "10", as_of="2026-03-16"))
        slots = derive_slots(data, "my-bluesky", target_timezone="Europe/Rome", today=date(2026, 3, 20))
        plan = propose_calendar(self.db, account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
                                start="2026-03-27", days=10, post_ids=["a", "b"],
                                now=datetime(2026, 3, 20, tzinfo=timezone.utc), audience_activity=slots)
        self.assertEqual(plan["audience_activity"]["dst_skipped_local"], ["2026-03-29T02:00"])
        self.assertEqual([e["scheduled_local"] for e in plan["entries"]], ["2026-03-30T09:00+02:00",
                                                                             "2026-04-05T02:00+02:00"])

    def test_duplicate_window_keeps_the_first_higher_ranked_provenance(self):
        # Defence in depth for the fall-back collision: the planner never lets a later slot overwrite an
        # earlier (higher-ranked) one's provenance for the same local window.
        slots = derive_slots(self.load(row("Mon", 9, "50"), row("Tue", 9, "10")), "my-bluesky",
                             target_timezone="Europe/Rome", today=TODAY)
        first = slots["slots"][0]
        slots["slots"].append({**first, "provenance": {**first["provenance"], "rank": 9, "activity": 1.0}})
        plan = propose_calendar(self.db, account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
                                start="2026-10-12", days=7, post_ids=["a", "b"], now=NOW, audience_activity=slots)
        monday = [e for e in plan["entries"] if datetime.fromisoformat(e["scheduled_local"]).weekday() == 0]
        self.assertEqual([(e["provenance"]["rank"], e["provenance"]["activity"]) for e in monday], [(1, 50.0)])
        self.assertEqual(plan["audience_activity"]["candidate_slots"], ["Mon@09:00", "Tue@09:00"])

    def test_audience_and_slot_are_exclusive_and_account_bound(self):
        slots = derive_slots(self.load(row()), "my-bluesky", target_timezone="Europe/Rome", today=TODAY)
        with self.assertRaisesRegex(ValidationError, "mutually exclusive"):
            propose_calendar(self.db, account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
                             start="2026-10-12", now=NOW, slots=["Mon@10:00"], audience_activity=slots)
        with self.assertRaisesRegex(ValidationError, "different account"):
            propose_calendar(self.db, account_id="other", platform="bluesky", audience_timezone="Europe/Rome",
                             start="2026-10-12", now=NOW, audience_activity=slots)

    def test_without_audience_output_is_byte_identical_to_golden(self):
        # The golden file was generated by the planner BEFORE --audience-activity existed.
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        cases = {
            "bluesky_default_rome": dict(platform="bluesky", audience_timezone="Europe/Rome", start="2026-10-20", days=14),
            "tiktok_dst_ny": dict(platform="tiktok", audience_timezone="America/New_York", start="2026-10-29", days=10),
            "mastodon_custom_slots": dict(platform="mastodon", audience_timezone="Asia/Tehran", start="2026-10-12",
                                          days=14, slots=["Mon@10:00", "Thu@16:30"], blackout=["2026-10-15"],
                                          spacing_hours=24),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                con = sqlite3.connect(":memory:", isolation_level=None)
                con.row_factory = sqlite3.Row
                con.executescript(SCHEMA)
                for i in range(3):
                    con.execute("INSERT INTO posts(id,account_id,platform,content,status,created_at) VALUES(?,?,?,?,?,?)",
                                (f"p{i}", "personal", kw["platform"], "Useful release note.", "approved",
                                 f"2026-10-01T00:00:0{i}+00:00"))
                    post = con.execute("SELECT * FROM posts WHERE id=?", (f"p{i}",)).fetchone()
                    con.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?",
                                (approval_payload_sha256(post), f"p{i}"))
                out = json.dumps(propose_calendar(con, account_id="personal", now=NOW, **kw), indent=2)
                con.close()
                self.assertEqual(out, golden[name])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = str(root / "outbox.sqlite")
        self.config = root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [{"id": "my-bluesky", "platform": "bluesky",
                                                         "env_prefix": "REACHWEAVER_TEST_AUD",
                                                         "handle": "test.bsky.social"}]}), encoding="utf-8")
        with connect(self.db) as db:
            self.pid = add_post(db, "my-bluesky", "bluesky", "A factual and helpful open-source tip")
            transition(db, self.pid, "approve")
        today = datetime.now(timezone.utc).date()
        self.csv = root / "activity.csv"
        self.csv.write_text(HEADER + "\n" + row("Wed", 16, "40", as_of=(today - timedelta(days=1)).isoformat())
                            + "\n", encoding="utf-8")
        self.start = (today + timedelta(days=2)).isoformat()

    def run_cli(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["--db", self.db, "--accounts", str(self.config), "calendar-plan", "--account", "my-bluesky",
                "--timezone", "Europe/Rome", "--start", self.start, "--days", "14", *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_no_flag_cli_output_equals_unmodified_planner_call(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        with connect(self.db) as db:
            expected = propose_calendar(db, account_id="my-bluesky", platform="bluesky",
                                        audience_timezone="Europe/Rome", start=self.start, days=14,
                                        spacing_hours=20, post_ids=None, include_drafts=False, slots=None,
                                        blackout=None, limit=30)
        self.assertEqual(out, json.dumps(expected, indent=2) + "\n")
        self.assertNotIn("audience_activity", out)
        self.assertNotIn("provenance", out)

    def test_flag_adds_provenance_without_changing_queue(self):
        code, out, _ = self.run_cli("--audience-activity", str(self.csv), "--audience-top-hours", "2")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        entry = plan["entries"][0]
        self.assertEqual(entry["evidence"], "first_party_audience_activity")
        self.assertEqual(entry["provenance"]["source"], "Owner dashboard export")
        self.assertTrue(entry["scheduled_local"].endswith("T16:00+02:00") or entry["scheduled_local"].endswith("T16:00+01:00"))
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT status FROM posts WHERE id=?", (self.pid,)).fetchone()[0], "approved")

    def test_cli_refusals_are_errors(self):
        for extra, needle in ((("--audience-top-hours", "3"), "need --audience-activity"),
                              (("--audience-activity", str(self.csv), "--slot", "Mon@10:00"), "mutually exclusive"),
                              (("--audience-activity", str(self.csv), "--audience-top-hours", "30"), "1..24")):
            with self.subTest(extra=extra):
                code, out, err = self.run_cli(*extra)
                self.assertNotEqual(code, 0)
                self.assertIn(needle, err)
                self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
