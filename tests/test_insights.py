"""`insights` ("What works for you"): read-only, per-account, matched-age observations (synthetic data only)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.insights import (STRENGTH_OBSERVATION, STRENGTH_TOO_LITTLE, analyze, hashtags, hook_type,
                                  length_bucket, parse_since, top_insights)
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, store_metrics

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
LINK = "Read the guide https://example.org/guide"
PLAIN = "A short note about build caching"


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class Seeder:
    """Synthetic published posts with one snapshot at the matched age (168 h)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def post(self, when: datetime, text: str, *, likes: int | None, replies: int = 1, reposts: int = 1,
             account: str = "my-bluesky", platform: str = "bluesky", source: str = "bluesky-public-api",
             age_shift: int = 0) -> str:
        post_id = add_post(self.con, account, platform, text, "synthetic")
        self.con.execute("UPDATE posts SET status='published', send_started_at=? WHERE id=?",
                         (when.isoformat(), post_id))
        if likes is not None:
            store_metrics(self.con, post_id, (when + timedelta(hours=168 + age_shift)).isoformat(), source,
                          likes=likes, replies=replies, reposts=reposts)
        return post_id

    def link_effect(self, n: int = 8, *, account: str = "my-bluesky", platform: str = "bluesky",
                    source: str = "bluesky-public-api", start: datetime = datetime(2026, 9, 1, 9, tzinfo=timezone.utc),
                    high_for_link: bool = True) -> None:
        """n posts with a link and n without; the link group has clearly higher interactions."""
        for i in range(n):
            when = start + timedelta(days=i, hours=(i % 3) * 5)
            hi, lo = 30 + i, 5 + (i % 3)
            self.post(when, f"{LINK} #python part {i}", likes=hi if high_for_link else lo,
                      account=account, platform=platform, source=source)
            self.post(when + timedelta(hours=2), f"{PLAIN} #python part {i}", likes=lo if high_for_link else hi,
                      account=account, platform=platform, source=source)


class FeatureRuleTests(unittest.TestCase):
    def test_hook_rules(self):
        self.assertEqual(hook_type("Why is my build slow? Here is how"), "question")
        self.assertEqual(hook_type("3 ways to cache builds. Number two is odd?"), "number")
        self.assertEqual(hook_type("Caching builds saves time."), "statement")
        self.assertEqual(hook_type(""), "statement")

    def test_hashtags_ignore_urls_and_numbers(self):
        self.assertEqual(hashtags("New #Python tip #python #2026 https://x.org/#anchor #dev-tools"),
                         ["#python", "#dev-tools"])

    def test_length_buckets(self):
        self.assertEqual([length_bucket("a" * n) for n in (0, 100, 101, 280, 281)],
                         ["short", "short", "medium", "medium", "long"])

    def test_since_forms(self):
        self.assertEqual(parse_since("30d", NOW), NOW - timedelta(days=30))
        self.assertEqual(parse_since("2026-10-01", NOW), datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-01T02:00:00+02:00", NOW), datetime(2026, 10, 1, tzinfo=timezone.utc))
        for bad in ("30", "0d", "yesterday", "2026-13-01"):
            with self.assertRaises(ValidationError):
                parse_since(bad, NOW)


class InsightsAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.addCleanup(self.con.close)
        self.seed = Seeder(self.con)

    def analyze(self, **kwargs):
        return analyze(self.con, **({"now": NOW, "resamples": 300} | kwargs))

    def test_clear_effect_is_an_observation_with_counts_and_a_hypothesis_line(self):
        self.seed.link_effect(8)
        before = self.con.total_changes
        report = self.analyze()
        self.assertEqual(self.con.total_changes, before, "insights must never write")
        self.assertEqual(report["status"], "ok")
        (cohort,) = report["cohorts"]
        self.assertEqual((cohort["account_id"], cohort["source"], cohort["posts_with_result"]),
                         ("my-bluesky", "bluesky-public-api", 16))
        link = next(f for f in cohort["features"] if f["feature"] == "link")
        with_link = next(g for g in link["groups"] if g["value"] == "with link")
        self.assertEqual((with_link["n"], with_link["rest_n"], with_link["strength"]), (8, 8, STRENGTH_OBSERVATION))
        self.assertGreater(with_link["difference"], 0)
        self.assertTrue(with_link["interval"]["excludes_zero"])
        first = cohort["suggestions"][0]
        self.assertEqual((first["feature"], first["direction"]), ("link", "more"))
        self.assertIn("n=8", first["text"])
        self.assertIn("hypothesis to test, not a cause", first["text"])
        self.assertIn("experiment-preregister", first["test_with"])
        self.assertLessEqual(len(cohort["suggestions"]), 3)
        self.assertEqual(len({s["feature"] for s in cohort["suggestions"]}), len(cohort["suggestions"]))
        joined = json.dumps(report).lower()
        self.assertNotIn('"cause"', joined)
        self.assertNotIn("proven", joined)

    def test_too_little_data(self):
        self.seed.link_effect(3)  # 6 posts: fewer than 2 x min_posts (5)
        report = self.analyze()
        self.assertEqual(report["status"], "not_enough_data")
        cohort = report["cohorts"][0]
        self.assertEqual(cohort["suggestions"], [])
        self.assertTrue(all(g["strength"] == STRENGTH_TOO_LITTLE for f in cohort["features"] for g in f["groups"]))
        self.assertTrue(any("metrics-fetch" in step for step in report["how_to_get_data"]))
        self.assertTrue(any("import-metrics" in step for step in report["how_to_get_data"]))

    def test_small_group_is_too_little_data_even_when_cohort_is_large(self):
        self.seed.link_effect(8)
        report = self.analyze()
        tag = next(f for f in report["cohorts"][0]["features"] if f["feature"] == "tag")
        python = next(g for g in tag["groups"] if g["value"] == "#python")
        self.assertEqual((python["n"], python["rest_n"], python["strength"]), (16, 0, STRENGTH_TOO_LITTLE))

    def test_accounts_never_pool_and_filters_apply(self):
        self.seed.link_effect(8)  # bluesky: links help
        self.seed.link_effect(8, account="my-mastodon", platform="mastodon", source="mastodon-api",
                              high_for_link=False)  # mastodon: links hurt
        report = self.analyze()
        by_account = {c["account_id"]: c for c in report["cohorts"]}
        self.assertEqual(set(by_account), {"my-bluesky", "my-mastodon"})
        self.assertEqual(by_account["my-bluesky"]["posts_with_result"], 16)
        self.assertEqual(by_account["my-mastodon"]["posts_with_result"], 16)
        self.assertEqual(by_account["my-mastodon"]["source"], "mastodon-api")
        link = {c: next(s for s in by_account[c]["suggestions"] if s["feature"] == "link") for c in by_account}
        self.assertEqual((link["my-bluesky"]["value"], link["my-bluesky"]["direction"]), ("with link", "more"))
        self.assertEqual(link["my-mastodon"]["value"] == "with link", link["my-mastodon"]["direction"] == "less")
        only = self.analyze(account_id="my-mastodon")
        self.assertEqual([c["account_id"] for c in only["cohorts"]], ["my-mastodon"])
        self.assertEqual([c["platform"] for c in self.analyze(platform="bluesky")["cohorts"]], ["bluesky"])

    def test_sources_are_never_mixed(self):
        self.seed.link_effect(8)
        for i in range(3):  # a second label covering fewer posts
            self.seed.post(datetime(2026, 9, 20 + i, 9, tzinfo=timezone.utc), PLAIN, likes=500,
                           source="native-export")
        cohort = self.analyze()["cohorts"][0]
        self.assertEqual(cohort["source"], "bluesky-public-api")
        self.assertEqual(cohort["other_sources"], {"native-export": 3})
        self.assertEqual(cohort["posts_with_result"], 16)
        forced = self.analyze(source="native-export")["cohorts"][0]
        self.assertEqual((forced["source"], forced["posts_with_result"], forced["status"]),
                         ("native-export", 3, "not_enough_data"))

    def test_since_filters_by_send_time(self):
        self.seed.link_effect(8, start=datetime(2026, 8, 1, 9, tzinfo=timezone.utc))  # old
        self.seed.link_effect(3, start=datetime(2026, 10, 1, 9, tzinfo=timezone.utc))  # recent
        self.assertEqual(self.analyze()["cohorts"][0]["published_in_window"], 22)
        recent = self.analyze(since="30d")
        self.assertEqual(recent["filters"]["since_utc"], "2026-09-21T12:00:00+00:00")
        self.assertEqual(recent["cohorts"][0]["published_in_window"], 6)
        self.assertEqual(recent["status"], "not_enough_data")

    def test_unmatched_age_missing_and_unknown_are_excluded_not_zero(self):
        self.seed.link_effect(8)
        self.seed.post(datetime(2026, 9, 25, 9, tzinfo=timezone.utc), PLAIN, likes=0, age_shift=72)  # too late
        self.seed.post(datetime(2026, 9, 26, 9, tzinfo=timezone.utc), PLAIN, likes=None)  # nothing recorded
        cohort = self.analyze()["cohorts"][0]
        self.assertEqual((cohort["published_in_window"], cohort["posts_with_result"]), (18, 16))
        self.assertEqual(cohort["excluded"], {"no_result_at_matched_age": 2})

    def test_deterministic_and_rate_metric(self):
        self.seed.link_effect(8)
        self.assertEqual(self.analyze(), self.analyze())
        report = self.analyze(metric="likes", denominator="views")
        self.assertEqual(report["status"], "not_enough_data")  # public counters carry no views: unknown, not 0
        with self.assertRaises(ValidationError):
            self.analyze(metric="views", denominator="views")
        with self.assertRaises(ValidationError):
            self.analyze(min_posts=2)

    def test_groups_of_mostly_the_same_posts_are_one_line(self):
        start = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
        for i in range(8):  # every link post is also tagged #guide: the data cannot tell them apart
            when = start + timedelta(days=i, hours=(i % 3) * 5)
            self.seed.post(when, f"{LINK} #guide part {i}", likes=30 + i)
            self.seed.post(when + timedelta(hours=2), f"{PLAIN} part {i}", likes=5 + (i % 3))
        lines = self.analyze()["cohorts"][0]["suggestions"]
        folded = [o for line in lines for o in line["overlaps_with"]]
        self.assertIn(("tag", "#guide", 8, 8), [(o["feature"], o["value"], o["n"], o["shared"]) for o in folded])
        self.assertNotIn("#guide", [line["value"] for line in lines])

    def test_top_insights_hook(self):
        self.assertEqual(top_insights(self.con, now=NOW), [])
        self.seed.link_effect(8)
        lines = top_insights(self.con, now=NOW)
        self.assertTrue(1 <= len(lines) <= 3)
        self.assertEqual(lines[0]["feature"], "link")
        self.assertIsNone(lines[0]["interval"])


class InsightsCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state"
        state.mkdir(mode=0o700)
        self.db = state / "rw.sqlite"
        con = sqlite3.connect(self.db, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        Seeder(con).link_effect(8)
        con.close()
        os.chmod(self.db, 0o600)

    def digest(self) -> str:
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def test_json_schema_is_stable_and_db_unchanged(self):
        before = self.digest()
        code, out = run("--db", str(self.db), "insights", "--json", "--now", NOW.isoformat(), "--resamples", "200")
        self.assertEqual(code, 0)
        self.assertEqual(self.digest(), before)
        report = json.loads(out)
        self.assertEqual(set(report), {"kind", "schema_version", "status", "filters", "method", "excluded", "cohorts",
                                       "suggestions", "how_to_get_data", "limits", "disclaimer"})
        self.assertEqual((report["kind"], report["schema_version"]), ("reachweaver.insights", 1))
        self.assertEqual(set(report["filters"]), {"account", "platform", "since", "since_utc", "now", "timezone",
                                                  "source"})
        cohort = report["cohorts"][0]
        self.assertEqual(set(cohort), {"account_id", "platform", "source", "other_sources", "published_in_window",
                                       "posted_by_hand", "posts_with_result", "excluded", "status", "overall", "features",
                                       "suggestions"})
        group = cohort["features"][0]["groups"][0]
        self.assertEqual(set(group), {"value", "label", "rest_label", "n", "median", "rest_n", "rest_median",
                                      "difference", "relative_difference", "interval", "strength"})
        self.assertEqual([f["feature"] for f in cohort["features"]],
                         ["time_of_day", "weekday", "day_type", "length", "link", "format", "hashtag_count",
                          "hook", "tag"])

    def test_text_output_and_filters(self):
        code, out = run("--db", str(self.db), "insights", "--now", NOW.isoformat(), "--account", "my-bluesky",
                        "--platform", "bluesky", "--since", "60d", "--resamples", "0")
        self.assertEqual(code, 0)
        self.assertIn("What works for you", out)
        self.assertIn("1. Try more of: posts with a link", out)
        self.assertIn("not a cause", out)
        code, out = run("--db", str(self.db), "insights", "--now", NOW.isoformat(), "--since", "7d")
        self.assertEqual(code, 0)
        self.assertIn("Not enough results yet", out)
        self.assertIn("metrics-fetch", out)

    def test_dashboard_results_panel_is_read_only(self):
        from reachweaver import ui_data
        before = self.digest()
        with ui_data.open_database(self.db) as con:
            panel = ui_data.results_view(con)["insights"]
            other = ui_data.results_view(con, platform="mastodon")["insights"]
        self.assertEqual(self.digest(), before)
        self.assertEqual(panel["lines"][0]["feature"], "link")
        self.assertIn("hypothesis to test", panel["lines"][0]["text"])
        self.assertIn("not causes", panel["note"])
        self.assertEqual(other["lines"], [])

    def test_missing_database_is_friendly_and_not_created(self):
        missing = Path(self.tmp.name) / "state" / "none.sqlite"
        code, out = run("--db", str(missing), "insights")
        self.assertEqual(code, 0)
        self.assertIn("No ReachWeaver database", out)
        self.assertFalse(missing.exists())
        code, out = run("--db", str(missing), "insights", "--json")
        self.assertEqual(json.loads(out)["status"], "no_database")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
