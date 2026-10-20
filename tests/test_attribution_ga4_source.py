"""experiment-readout and baseline-eval can read the GA4 section of a saved attribution-report.

`--attribution-source ga4` reads `ga4.attributed.posts` (sessions; key events as conversions) and
`ga4.date_range` instead of the --analytics join. The two sources are never mixed. Offline.
"""
from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import date

from reachweaver.attribution import (
    attributed_outcome, attribution_report, load_analytics_csv, load_attribution_outcomes,
)
from reachweaver.cli import make_parser
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, get_post
from reachweaver.web_analytics import combine_ga4, load_ga4_csv
from test_web_analytics import GA4_HEADER, TempFiles


class Ga4AttributionSourceTests(TempFiles):
    def setUp(self):
        super().setUp()
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)
        self.p1 = self.published("2026-09-02T09:00:00+00:00")
        self.p2 = self.published("2026-09-03T09:00:00+00:00")

    def published(self, sent):
        pid = add_post(self.db, "my-bluesky", "bluesky", f"Note {sent}", "rw-launch")
        self.db.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?", (sent, pid))
        return pid

    def saved(self, with_ga4=True):
        preamble = "# Start date: 20260901\n# End date: 20261031\n"
        ga4 = None
        if with_ga4:
            ga4 = combine_ga4([load_ga4_csv(self.ga4(
                f"bluesky / social,rw-launch,{self.p1},40,30,0.75,4\n"
                f"bluesky / social,rw-launch,{self.p2},7,5,0.7,\n", preamble=preamble, header=GA4_HEADER))])
        analytics = self.write("a.csv", "date,landing_url,utm_source,utm_medium,utm_campaign,utm_content,"
                               "sessions,conversions\n"
                               f"2026-09-10,https://example.com/,bluesky,social,rw-launch,{self.p1},999,99\n")
        report = attribution_report(self.db, load_analytics_csv(analytics), ga4=ga4)
        path = self.tmp / "attribution.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_ga4_source_reads_the_ga4_section_only(self):
        outcomes = load_attribution_outcomes(self.saved(), "ga4")
        self.assertEqual(outcomes["source"], "ga4")
        self.assertEqual(outcomes["analytics_date_range"], (date(2026, 9, 1), date(2026, 10, 31)))
        self.assertEqual(outcomes["posts"][self.p1]["sessions"], 40)
        self.assertEqual(outcomes["posts"][self.p1]["conversions"], 4)  # key events
        self.assertIsNone(outcomes["posts"][self.p2]["conversions"])  # blank stays unknown, never 0
        row = get_post(self.db, self.p1)
        self.assertEqual(attributed_outcome(outcomes, row, "docs_visits", age_hours=168), (40, None))
        self.assertEqual(attributed_outcome(outcomes, get_post(self.db, self.p2), "repo_stars_utm", age_hours=168),
                         (None, "conversions_unknown"))

    def test_default_source_is_unchanged(self):
        outcomes = load_attribution_outcomes(self.saved())
        self.assertEqual(outcomes["source"], "analytics")
        self.assertEqual(outcomes["posts"][self.p1]["sessions"], 999)
        self.assertNotIn(self.p2, outcomes["posts"])

    def test_missing_ga4_section_and_bad_source_are_refused(self):
        path = self.saved(with_ga4=False)
        with self.assertRaisesRegex(ValidationError, "no ga4 section"):
            load_attribution_outcomes(path, "ga4")
        with self.assertRaisesRegex(ValidationError, "--attribution-source must be one of"):
            load_attribution_outcomes(path, "gsc")

    def test_bad_ga4_window_is_named(self):
        path = self.tmp / "bad.json"
        path.write_text(json.dumps({"ga4": {"attributed": {"posts": []}, "date_range": ["2026-10-02", "2026-10-01"]}}),
                        encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "ga4.date_range ends before it starts"):
            load_attribution_outcomes(path, "ga4")

    def test_both_commands_take_the_flag_with_analytics_default(self):
        parser = make_parser()
        for argv in (["experiment-readout", "--prereg", "p", "--posts", "m"],
                     ["baseline-eval", "--account", "a", "--timezone", "UTC", "--since", "2026-09-01",
                      "--until", "2026-09-30"]):
            with self.subTest(command=argv[0]):
                self.assertEqual(parser.parse_args(argv).attribution_source, "analytics")
                self.assertEqual(parser.parse_args([*argv, "--attribution-source", "ga4"]).attribution_source, "ga4")


if __name__ == "__main__":
    unittest.main()
