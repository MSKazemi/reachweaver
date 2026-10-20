"""Robustness regressions (audit 2026-10-20): bad local input ends in a clean ERROR line, never a traceback.

Synthetic data only; offline. The share-browser test uses a loopback test server, nothing else.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver import persona_guard, storage
from reachweaver import profile as profile_store
from reachweaver.calendar_gaps import find_calendar_gaps
from reachweaver.cli import main
from reachweaver.inputs import MAX_JSON_DEPTH, loads_text, nesting_depth, read_json_file, split_list_args
from reachweaver.policy import ValidationError
from reachweaver.web_analytics import load_ga4_csv, load_gsc_csv
from test_profile import sample
from test_share_browser import request, running_server

ROOT = Path(__file__).resolve().parents[1]
# Valid JSON nested deeper than inputs.MAX_JSON_DEPTH. The explicit limit is checked before parsing, so the
# result no longer depends on the interpreter: Python 3.14 bounds its C decoder by the thread stack, and CI's
# runner parsed depths that raised RecursionError on a laptop.
DEEP = "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        os.chmod(self.tmp, 0o700)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write(self, name, text, encoding="utf-8"):
        path = self.tmp / name
        path.write_bytes(text.encode(encoding))
        return path

    def assertCleanError(self, code, err, *needles):
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("ERROR: "), err)
        self.assertNotIn("Traceback", err)
        for needle in needles:
            self.assertIn(needle, err)


class DatabaseErrorTests(CliCase):  # B1
    def test_a_file_that_is_not_a_database_is_a_clean_exit_2(self):
        secret = "do-not-echo-this-line " * 8
        bad = self.write("bad.sqlite", secret)
        os.chmod(bad, 0o600)
        code, out, err = self.cli("--db", str(bad), "list")
        self.assertCleanError(code, err, "unreadable or corrupt", "another running reachweaver process")
        self.assertNotIn("do-not-echo", err + out)

    def test_a_locked_database_says_busy_and_names_the_likely_cause(self):
        with patch("reachweaver.cli.execute", side_effect=sqlite3.OperationalError("database is locked")), \
                patch("reachweaver.cli.write_started", return_value=False):  # other tests may leave the marker set
            code, _, err = self.cli("--db", str(self.tmp / "x.sqlite"), "list")
        self.assertCleanError(code, err, "locked (busy)", "another running reachweaver process")
        self.assertNotIn("reconcile", err)  # no live send was in progress

    def test_a_database_error_after_a_live_write_started_still_says_reconcile(self):
        with patch("reachweaver.cli.execute", side_effect=sqlite3.DatabaseError("disk I/O error")), \
                patch("reachweaver.cli.write_started", return_value=True):
            code, _, err = self.cli("--db", str(self.tmp / "x.sqlite"), "list")
        self.assertCleanError(code, err, "unreadable or corrupt", "reconcile manually")


class DeepJsonTests(CliCase):  # B2
    def test_every_json_reading_command_refuses_deep_nesting_with_exit_2(self):
        deep = self.write("deep.json", DEEP)
        db = str(self.tmp / "never.sqlite")
        for argv in (("--db", db, "--accounts", str(deep), "doctor"),
                     ("profile-audit", "--file", str(deep)),
                     ("post-audit", "--file", str(deep)),
                     ("campaign-check", "--file", str(deep)),
                     ("discovery-plan", "--file", str(deep)),
                     ("peer-benchmark", "--file", str(deep)),
                     ("--profile", str(deep), "profile", "--check"),
                     ("creative-preflight", "--file", str(deep))):
            with self.subTest(command=argv[-3:]):
                code, _, err = self.cli(*argv)
                self.assertCleanError(code, err, "nested too deeply")
        self.assertFalse(Path(db).exists())

    def test_health_reports_the_deep_accounts_file_as_a_blocker(self):
        deep = self.write("deep.json", DEEP)
        code, out, err = self.cli("--db", str(self.tmp / "h.sqlite"), "--accounts", str(deep), "health")
        self.assertEqual(code, 1, err)
        self.assertIn("nested too deeply", out)

    def test_main_is_the_last_resort_for_any_recursion_error(self):
        with patch("reachweaver.cli.execute", side_effect=RecursionError("maximum recursion depth exceeded")):
            code, _, err = self.cli("--db", str(self.tmp / "x.sqlite"), "list")
        self.assertCleanError(code, err, "nested too deeply")

    def test_helpers(self):
        with self.assertRaisesRegex(ValidationError, "nested too deeply"):
            loads_text(DEEP, "Snapshot")
        with self.assertRaises(json.JSONDecodeError):  # callers keep their own "not JSON" message
            loads_text("{nope")
        at_limit = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH
        self.assertEqual(nesting_depth(at_limit), MAX_JSON_DEPTH)
        self.assertIsInstance(loads_text(at_limit), list)  # the limit itself still parses
        self.assertEqual(nesting_depth('{"a": "[[[{{{", "b": "\\"[["}'), 1)  # brackets in strings do not count
        with patch("reachweaver.inputs.json.loads", side_effect=RecursionError):  # decoder's own limit
            with self.assertRaisesRegex(ValidationError, "nested too deeply"):
                loads_text("[1]")


class DateRangeTests(CliCase):  # B3
    def test_distribution_plan_date_out_of_range(self):
        code, _, err = self.cli("distribution-plan", "--region", "global", "--goal", "awareness",
                                "--date", "0001-01-01", "--timezone", "Etc/GMT-14")
        self.assertCleanError(code, err, "out of range")

    def test_peer_benchmark_observed_at_out_of_range(self):
        data = json.loads((ROOT / "examples/peer-benchmark.example.json").read_text(encoding="utf-8"))
        data["entries"][0]["observed_at"] = "0001-01-01T00:00:00+01:00"
        path = self.write("peers.json", json.dumps(data))
        code, _, err = self.cli("peer-benchmark", "--file", str(path))
        self.assertCleanError(code, err, "observed_at is out of range")

    def test_creator_coverage_published_at_out_of_range(self):
        data = json.loads((ROOT / "examples/creator-coverage.example.json").read_text(encoding="utf-8"))
        data["published_at"] = "0001-01-01T00:00:00+01:00"
        path = self.write("coverage.json", json.dumps(data))
        code, _, err = self.cli("creator-coverage", "--file", str(path))
        self.assertCleanError(code, err, "published_at is out of range")

    def test_csv_and_snapshot_dates_out_of_range(self):
        from reachweaver.profile_audit import _at
        from reachweaver.profile_metrics_csv import _iso_aware
        with self.assertRaisesRegex(ValidationError, "out of range"):
            _iso_aware("0001-01-01T00:00:00+01:00", "observed_at")
        self.assertIsNone(_at("0001-01-01T00:00:00+01:00"))  # unknown date, excluded like any unparsable one

    def test_calendar_gaps_window_out_of_range(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.row_factory = sqlite3.Row
        con.executescript(storage.SCHEMA)
        self.addCleanup(con.close)
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        for start, zone in (("9999-12-31", "Europe/Rome"), ("0001-01-01", "Asia/Tokyo")):
            with self.subTest(start=start), self.assertRaisesRegex(ValidationError, "out of range"):
                find_calendar_gaps(con, account_id="personal", platform="bluesky", audience_timezone=zone,
                                   start=start, days=14, now=now)


LINKEDIN = {"id": "li-work", "platform": "linkedin", "persona": "work", "env_prefix": "REACHWEAVER_LIW",
            "person_id": "examplePersonId", "linkedin_version": "202609"}


class PersonaRouteTests(CliCase):  # B4
    def setUp(self):
        super().setUp()
        self.profile_data = sample()
        self.profile_data["handles"].append({"platform": "linkedin", "persona": "work", "username": "alex-example",
                                             "status": "claimed"})
        self.profile = self.tmp / "profile.json"
        profile_store.save_profile(self.profile_data, self.profile)

    def test_account_without_a_username_is_not_a_conflict(self):
        accounts = self.write("accounts.json", json.dumps({"accounts": [LINKEDIN]}))
        code, out, err = self.cli("--accounts", str(accounts), "--profile", str(self.profile),
                                  "account-route", "--platform", "linkedin", "--persona", "work")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("@None", out)
        self.assertIn("work on linkedin: ok. Use account li-work.", out)
        self.assertIn("not compared with @alex-example", out)

    def test_profile_check_has_no_false_finding(self):
        accounts = self.write("accounts.json", json.dumps({"accounts": [LINKEDIN]}))
        code, out, err = self.cli("--accounts", str(accounts), "--profile", str(self.profile), "profile", "--check")
        self.assertNotIn("@None", out)
        self.assertNotIn("FINDING", out)
        self.assertEqual(code, 0, out + err)

    def test_a_real_username_mismatch_is_still_a_conflict(self):
        named = dict(LINKEDIN, handle="someone-else")
        route = persona_guard.route("linkedin", "work", {"li-work": named}, self.profile_data)
        self.assertEqual(route["status"], "conflict")
        self.assertIn("@someone-else", route["advice"])

    def test_unknown_persona_still_refuses(self):
        no_persona = {k: v for k, v in LINKEDIN.items() if k != "persona"}
        message = persona_guard.check_account(no_persona, "work", {"li-work": no_persona}, self.profile_data)
        self.assertIn("cannot tell which persona", message)
        self.assertNotIn("@None", message)


class ShareBrowserInputTests(unittest.TestCase):  # B6
    def setUp(self):
        from reachweaver import home
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = home.root()
        home.set_root(Path(tmp.name))
        self.addCleanup(lambda: home.set_root(old))

    def post(self, srv, body: bytes):
        return request(srv, "POST", "/api/preview", body, token=srv.token, origin=srv.origin,
                       content_type="application/json")

    def test_lone_surrogate_is_a_clean_400(self):
        with running_server() as srv:
            for body in (b'{"\\ud800": 1}', b'{"platforms": ["threads"], "text": "a\\udc00b"}'):
                with self.subTest(body=body):
                    code, _, raw = self.post(srv, body)
                    self.assertEqual(code, 400, raw)
                    self.assertIn("lone surrogate", json.loads(raw)["errors"][0])

    def test_null_note_means_no_note(self):
        with running_server() as srv:
            code, _, raw = self.post(srv, b'{"platforms": ["threads"], "text": "Hello", "note": null}')
            self.assertEqual(code, 200, raw)
            code, _, raw = self.post(srv, b'{"platforms": ["threads"], "text": "Hello", "note": 5}')
            self.assertEqual(code, 422, raw)
            self.assertIn("plain text", raw.decode())


class ListStatusTests(CliCase):  # B7
    def test_status_choices_are_the_schema_statuses(self):
        allowed = re.search(r"CHECK\(status IN \(([^)]*)\)\)", storage.SCHEMA).group(1)
        self.assertEqual(tuple(item.strip("'") for item in allowed.split(",")), storage.POST_STATUSES)

    def test_unknown_status_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            main(["--db", str(self.tmp / "s.sqlite"), "list", "--status", "bogus"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_no_match_leaves_stdout_empty_and_says_so_on_stderr(self):
        code, out, err = self.cli("--db", str(self.tmp / "s.sqlite"), "list", "--status", "published")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("No posts with status published", err)


class CsvLineTests(CliCase):  # B8
    HEADER = "Session source / medium,Session campaign,Session manual ad content,Sessions,Engaged sessions,Key events\n"

    def test_form_feed_and_line_separator_do_not_end_the_table(self):
        path = self.write("ga4.csv", self.HEADER
                          + "a / b,camp,x,1,1,0\x0c\n"
                          + "c / d,camp aign,x,2,1,0\n"
                          + "e / f,camp,x,3,1,0\n")
        export = load_ga4_csv(path)
        self.assertEqual([r["sessions"] for r in export["rows"]], [1, 2, 3])
        self.assertEqual([r["line"] for r in export["rows"]], [2, 3, 4])
        self.assertEqual(export["trailing_lines_ignored"], 0)

    def test_quoted_cell_with_blank_and_hash_lines_inside(self):
        path = self.write("ga4.csv", self.HEADER
                          + 'a / b,"multi\n\n# not a comment",x,1,1,0\n'
                          + "c / d,camp,x,2,1,0\n")
        export = load_ga4_csv(path)
        self.assertEqual([r["sessions"] for r in export["rows"]], [1, 2])
        self.assertEqual([r["line"] for r in export["rows"]], [4, 5])  # a record spanning lines reports its last (csv line_num)

    def test_search_console_cell_with_line_separator(self):
        path = self.write("gsc.csv", "Top pages,Clicks,Impressions,CTR,Position\n"
                          "https://example.org/a b,1,10,10%,2.5\n"
                          "https://example.org/c,2,20,10%,3\n")
        rows = load_gsc_csv(path)["rows"]
        self.assertEqual([r["clicks"] for r in rows], [1, 2])
        self.assertEqual(rows[0]["key"], "https://example.org/a b")


class BrokenPipeTests(unittest.TestCase):  # B10
    def test_reader_closing_early_is_quiet_exit_141(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        # bufsize=0: an unbuffered pipe, so read(10) takes 10 bytes. Python 3.14 raised the default buffer to
        # 128 KiB, and a buffered read(10) could drain the whole ~80 KB output and let the child exit 0.
        proc = subprocess.Popen([sys.executable, "-m", "reachweaver", "delivery-options", "--json"], cwd=ROOT,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        proc.stdout.read(10)  # the output is far larger than a pipe buffer
        proc.stdout.close()
        err = proc.stderr.read().decode()
        proc.stderr.close()
        self.assertEqual(proc.wait(timeout=60), 141, err)
        self.assertNotIn("Exception ignored", err)
        self.assertNotIn("ERROR", err)

    def test_a_broken_pipe_that_is_not_stdout_stays_an_error(self):
        with patch("reachweaver.cli.execute", side_effect=BrokenPipeError(32, "Broken pipe")):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--db", "unused.sqlite", "list"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR: ", err.getvalue())


class BomAndListTests(CliCase):  # B11
    def test_json_with_a_byte_order_mark_is_read(self):
        accounts = json.loads((ROOT / "config/accounts.example.json").read_text(encoding="utf-8"))
        path = self.write("accounts.json", "﻿" + json.dumps(accounts))
        self.assertEqual(read_json_file(path), accounts)
        code, out, err = self.cli("--db", str(self.tmp / "d.sqlite"), "--accounts", str(path), "doctor")
        self.assertNotIn("unreadable or not JSON", err)
        self.assertIn("my-linkedin", out + err)
        snapshot = json.loads((ROOT / "examples/profile-post-audit.example.json").read_text(encoding="utf-8"))
        path = self.write("snapshot.json", "﻿" + json.dumps(snapshot))
        code, _, err = self.cli("profile-audit", "--file", str(path))
        self.assertNotEqual(code, 2, err)

    def test_account_kit_accepts_comma_lists_like_share(self):
        profile = self.tmp / "profile.json"
        profile_store.save_profile(sample(), profile)
        base = ("--profile", str(profile), "account-kit", "--persona", "work", "--json")
        repeated = self.cli(*base, "--platform", "instagram", "--platform", "threads")
        commas = self.cli(*base, "--platform", "instagram, threads")
        self.assertIn("plan_sha256", repeated[1])
        self.assertEqual(commas, repeated)

    def test_split_list_args(self):
        self.assertEqual(split_list_args(["a,b", " c ", ",,", "d,"]), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
