"""Timing analysis is descriptive, local, source-bound and cannot publish content."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, store_metrics
from reachweaver.timing_analysis import analyze_posting_times


class TimingOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)

    def post(self, when: datetime, clicks: int | None = 10,
             impressions: int | None = 100, source="native-export",
             account="my-bluesky", platform="bluesky", campaign="same-format",
             age_shift: int = 0) -> str:
        post_id = add_post(self.db, account, platform, "Genuine technical example", campaign)
        self.db.execute(
            "UPDATE posts SET status='published',send_started_at=? WHERE id=?",
            (when.isoformat(), post_id),
        )
        observed = when + timedelta(hours=168 + age_shift)
        if clicks is not None or impressions is not None:
            store_metrics(self.db, post_id, observed.isoformat(), source,
                          clicks=clicks, impressions=impressions)
        return post_id

    def readout(self, **kwargs):
        defaults = dict(account_id="my-bluesky", platform="bluesky",
                        audience_timezone="UTC", source="native-export",
                        metric="clicks", denominator="impressions",
                        age_hours=168, tolerance_hours=24, campaign="same-format")
        return analyze_posting_times(self.db, **(defaults | kwargs))

    def seed_two_groups(self):
        # Weekday mornings contain one huge outlier; the median remains robust.
        dates = (21, 22, 23)
        for day, c in zip(dates, (10, 11, 900)):
            self.post(datetime(2026, 9, day, 9, tzinfo=timezone.utc), c)
        for day, c in zip(dates, (20, 21, 22)):
            self.post(datetime(2026, 9, day, 19, tzinfo=timezone.utc), c)

    def test_account_cohort_robust_medians_and_candidate_is_noncausal(self):
        self.seed_two_groups()
        before = self.db.execute("SELECT count(*) FROM event_log").fetchone()[0]
        result = self.readout()
        self.assertEqual(result["posts_with_comparable_snapshots"], 6)
        self.assertEqual(result["decision"], "exploratory_candidate")
        self.assertEqual(result["candidate"]["slot"], "weekday_18_24")
        self.assertEqual(result["candidate"]["validity"], "observational_only_not_a_proven_best_time")
        morning = next(s for s in result["slots"] if s["slot"] == "weekday_06_12")
        self.assertEqual(morning["median_rate"], 0.11)
        self.assertEqual(morning["posts"], 3)
        self.assertEqual(result["candidate"]["median_rate"], 0.21)
        self.assertEqual(self.db.execute("SELECT count(*) FROM event_log").fetchone()[0], before)

    def test_sparse_and_single_group_stay_inconclusive(self):
        self.post(datetime(2026, 9, 21, 9, tzinfo=timezone.utc))
        r = self.readout()
        self.assertEqual(r["decision"], "insufficient_comparable_slots")
        self.assertIsNone(r["candidate"])
        self.assertFalse(r["slots"][0]["eligible_for_comparison"])
        for day in (22, 23):
            self.post(datetime(2026, 9, day, 9, tzinfo=timezone.utc))
        self.assertEqual(self.readout()["decision"], "insufficient_comparable_slots")

    def test_ties_do_not_manufacture_a_best_hour(self):
        for day in (21, 22, 23):
            self.post(datetime(2026, 9, day, 9, tzinfo=timezone.utc), 10)
            self.post(datetime(2026, 9, day, 19, tzinfo=timezone.utc), 10)
        r = self.readout()
        self.assertEqual(r["decision"], "no_clear_separation")
        self.assertIsNone(r["candidate"])

    def test_missing_and_wrong_source_denominators_are_not_zero(self):
        for day in (21, 22, 23):
            self.post(datetime(2026, 9, day, 9, tzinfo=timezone.utc), 10)
        self.post(datetime(2026, 9, 24, 9, tzinfo=timezone.utc), clicks=10,
                  impressions=None)
        self.post(datetime(2026, 9, 25, 9, tzinfo=timezone.utc), clicks=10,
                  impressions=100, source="other")
        self.post(datetime(2026, 9, 26, 9, tzinfo=timezone.utc), clicks=10,
                  impressions=0)
        r = self.readout()
        self.assertEqual(r["published_in_window_with_timestamp"], 6)
        self.assertEqual(r["posts_with_comparable_snapshots"], 3)
        self.assertEqual(r["excluded"]["no_source_matched_snapshot_or_denominator"], 3)

    def test_matched_post_age_nearest_snapshot_only(self):
        when = datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
        pid = self.post(when, clicks=10)
        store_metrics(self.db, pid, (when + timedelta(hours=168 - 12)).isoformat(),
                      "native-export", clicks=999, impressions=100)
        result = self.readout()
        self.assertEqual(result["slots"][0]["median_rate"], 0.1)
        self.assertEqual(result["posts_with_comparable_snapshots"], 1)

    def test_age_tolerance_excludes_stale_snapshots(self):
        self.post(datetime(2026, 9, 21, 9, tzinfo=timezone.utc), age_shift=-30)
        result = self.readout()
        self.assertEqual(result["posts_with_comparable_snapshots"], 0)
        self.assertEqual(result["excluded"]["no_source_matched_snapshot_or_denominator"], 1)

    def test_timezone_and_date_filters_are_audience_local(self):
        self.post(datetime(2026, 9, 21, 23, tzinfo=timezone.utc))
        r = self.readout(audience_timezone="Europe/Rome", since="2026-09-22", until="2026-09-22")
        self.assertEqual(r["posts_with_comparable_snapshots"], 1)
        self.assertEqual(r["slots"][0]["slot"], "weekday_00_06")
        r = self.readout(audience_timezone="UTC", since="2026-09-22", until="2026-09-22")
        self.assertEqual(r["posts_with_comparable_snapshots"], 0)

    def test_cross_account_platform_and_campaign_not_pooled(self):
        self.seed_two_groups()
        self.post(datetime(2026, 9, 24, 19, tzinfo=timezone.utc), 500,
                  account="not-owned")
        self.post(datetime(2026, 9, 25, 19, tzinfo=timezone.utc), 500,
                  platform="mastodon")
        self.post(datetime(2026, 9, 26, 19, tzinfo=timezone.utc), 500,
                  campaign="different-format")
        r = self.readout()
        self.assertEqual(r["posts_with_comparable_snapshots"], 6)
        r = self.readout(campaign="different-format")
        self.assertEqual(r["posts_with_comparable_snapshots"], 1)

    def test_validation_is_fail_closed(self):
        with self.assertRaises(ValidationError):
            self.readout(audience_timezone="Moon/Other")
        with self.assertRaises(ValidationError):
            self.readout(source="")
        with self.assertRaises(ValidationError):
            self.readout(metric="impressions", denominator="impressions")
        with self.assertRaises(ValidationError):
            self.readout(age_hours=12)
        with self.assertRaises(ValidationError):
            self.readout(tolerance_hours=168, age_hours=168)
        with self.assertRaises(ValidationError):
            self.readout(min_posts_per_slot=1)
        with self.assertRaises(ValidationError):
            self.readout(since="2026-09-30", until="2026-09-01")

    def test_invalid_published_send_timestamp_reported_not_crashed(self):
        pid=self.post(datetime(2026, 9, 21, 9, tzinfo=timezone.utc))
        self.db.execute("UPDATE posts SET send_started_at='wrong' WHERE id=?", (pid,))
        r=self.readout()
        self.assertEqual(r["excluded"]["invalid_send_timestamp"],1)
        self.assertEqual(r["posts_with_comparable_snapshots"],0)


class TimingCLITests(unittest.TestCase):
    def test_read_only_cli_from_user_supplied_metrics(self):
        from reachweaver.storage import connect
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "outbox.sqlite")
            config_path = Path(temp) / "accounts.json"
            config_path.write_text(json.dumps({"accounts": [
                {"id": "my-bluesky", "platform": "bluesky",
                 "env_prefix": "REACHWEAVER_TEST_TIMING", "handle": "test.bsky.social"}
            ]}))
            with connect(db_path) as db:
                pid=add_post(db,"my-bluesky","bluesky","Actual release information","same-format")
                when=datetime(2026, 9, 21, 9, tzinfo=timezone.utc)
                db.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?",
                           (when.isoformat(),pid))
                store_metrics(db,pid,(when+timedelta(hours=168)).isoformat(),
                              "native-export", clicks=12, impressions=100)
            args=["--db",db_path,"--accounts",str(config_path),
                  "timing-evaluate","--account","my-bluesky","--timezone","UTC",
                  "--source","native-export","--metric","clicks","--denominator","impressions",
                  "--campaign","same-format"]
            stdout=io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(args),0)
            result=json.loads(stdout.getvalue())
            self.assertEqual(result["posts_with_comparable_snapshots"],1)
            self.assertEqual(result["decision"],"insufficient_comparable_slots")
            with connect(db_path) as db:
                self.assertEqual(db.execute("SELECT status FROM posts WHERE id=?",(pid,)).fetchone()[0],
                                 "published")
                self.assertEqual(db.execute("SELECT count(*) FROM event_log WHERE action='schedule'").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
