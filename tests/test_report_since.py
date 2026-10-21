"""`report --since`: posts sent in a window and their snapshots observed in it. Offline, read-only, synthetic."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import connect, report, report_since, reply_report, store_metrics

NOW = "2026-10-21T12:00:00+00:00"


def ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class ParseTests(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(report_since("7d", NOW), "2026-10-14T12:00:00+00:00")
        self.assertEqual(report_since("30d", NOW), "2026-09-21T12:00:00+00:00")
        self.assertEqual(report_since("24h", NOW), "2026-10-20T12:00:00+00:00")
        self.assertEqual(report_since(" 2026-10-01 ", NOW), "2026-10-01T00:00:00+00:00")
        self.assertEqual(report_since("2026-10-01T09:00:00+02:00", NOW), "2026-10-01T07:00:00+00:00")
        self.assertEqual(report_since("2026-10-01T09:00:00Z", NOW), "2026-10-01T09:00:00+00:00")

    def test_bad_values_fail_with_a_clear_message(self):
        for value in ("", "7", "7w", "0d", "-7d", "7 d", "seven days", "2026-13-01", "2026-02-30",
                      "2026-10-01T09:00:00", "01/10/2026", None, "9999999d"):
            with self.subTest(value=value), self.assertRaises(ValidationError) as caught:
                report_since(value, NOW)
            self.assertIn("--since", str(caught.exception))
        with self.assertRaisesRegex(ValidationError, "use a duration such as 24h, 7d or 30d"):
            report_since("last week", NOW)

    def test_future_and_too_old_windows_are_refused(self):
        with self.assertRaisesRegex(ValidationError, "future"):
            report_since("2026-10-22", NOW)
        with self.assertRaisesRegex(ValidationError, "within 3650 days"):
            report_since("3651d", NOW)
        with self.assertRaisesRegex(ValidationError, "within 3650 days"):
            report_since("2000-01-01", NOW)


class WindowBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        state = self.tmp / "state"
        state.mkdir(mode=0o700)
        self.db = state / "rw.sqlite"
        with connect(str(self.db)) as con:
            self.publish(con, "old", sent=ago(20))
            self.publish(con, "new", sent=ago(2))
            self.publish(con, "unsent", sent=None)
            self.publish(con, "reply-new", sent=ago(1), reply=True)
            self.publish(con, "reply-old", sent=ago(15), reply=True)
            store_metrics(con, "old", ago(19), "native-export", likes=100)
            store_metrics(con, "new", ago(1), "native-export", likes=7)
            store_metrics(con, "unsent", ago(1), "native-export", likes=50)

    def publish(self, con, post_id, *, sent, reply=False):
        con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at,send_started_at,remote_id,
                       reply_target) VALUES(?,?,?,?,'published','2026-09-01T00:00:00+00:00',?,?,?)""",
                    (post_id, "acct", "bluesky", "Hello " + post_id, sent, "remote-" + post_id,
                     '{"uri":"at://x"}' if reply else None))


class ReportWindowTests(WindowBase):
    def test_without_since_nothing_changes(self):
        with connect(str(self.db)) as con:
            whole = report(con)
            self.assertEqual(report(con, since=None), whole)
        self.assertEqual(whole["bluesky"]["published"], 3)
        self.assertEqual(whole["bluesky"]["likes"], 157)

    def test_since_keeps_posts_sent_in_the_window_and_their_snapshots_in_it(self):
        with connect(str(self.db)) as con:
            week = report(con, since=report_since("7d"))
            month = report(con, since=report_since("30d"))
            replies = reply_report(con, since=report_since("7d"))
            all_replies = reply_report(con)
        self.assertEqual(week["bluesky"]["published"], 1)  # "old" is outside, "unsent" has no send time
        self.assertEqual(week["bluesky"]["likes"], 7)
        self.assertEqual(month["bluesky"]["published"], 2)
        self.assertEqual(month["bluesky"]["likes"], 107)
        self.assertEqual(replies["bluesky"]["published"], 1)
        self.assertEqual(all_replies["bluesky"]["published"], 2)

    def test_a_snapshot_observed_before_the_window_is_not_used(self):
        with connect(str(self.db)) as con:
            self.publish(con, "fresh", sent=ago(0.5))
            store_metrics(con, "fresh", ago(3), "backfill", likes=999)  # observed before the window starts
            day = report(con, since=report_since("1d"))
            self.assertEqual(report(con)["bluesky"]["likes"], 157 + 999)
        self.assertEqual(day["bluesky"]["published"], 1)
        self.assertEqual(day["bluesky"]["metric_coverage"], 0)
        self.assertIsNone(day["bluesky"]["likes"])  # unknown, never a fabricated 0
        self.assertEqual(day["bluesky"]["metrics"]["likes"]["status_counts"]["unknown"], 1)


class ReportCliTests(WindowBase):
    def test_json_gets_a_window_key_only_with_since(self):
        code, out, err = run("--db", self.db, "report")
        self.assertEqual(code, 0, err)
        self.assertNotIn("window", json.loads(out))
        code, out, err = run("--db", self.db, "report", "--since", "7d")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc["platforms"]["bluesky"]["published"], 1)
        self.assertEqual(set(doc["window"]), {"since", "until", "basis"})
        self.assertEqual(doc["replies"]["bluesky"]["published"], 1)

    def test_markdown_names_the_window_and_csv_keeps_its_columns(self):
        code, out, _ = run("--db", self.db, "report", "--since", "30d", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("Window: posts sent since", out)
        code, plain, _ = run("--db", self.db, "report", "--format", "csv")
        code, windowed, _ = run("--db", self.db, "report", "--since", "30d", "--format", "csv")
        self.assertEqual(plain.splitlines()[0], windowed.splitlines()[0])

    def test_an_empty_window_says_so(self):
        code, out, _ = run("--db", self.db, "report", "--since", "1h", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertIn("No published posts in this window.", out)

    def test_report_since_is_read_only(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(run("--db", self.db, "report", "--since", "7d")[0], 0)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)

    def test_a_bad_value_fails_before_any_database_is_created(self):
        missing = self.tmp / "other" / "none.sqlite"
        for value in ("7w", "tomorrow", "2026-02-30"):
            with self.subTest(value=value):
                code, out, err = run("--db", missing, "report", "--since", value)
                self.assertEqual(code, 2)
                self.assertIn("--since", err)
                self.assertIn("24h, 7d or 30d", err)
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
