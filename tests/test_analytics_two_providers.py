"""Two mocked providers plus mockstats, end to end through ``report`` (issue #15). Offline only.

Bluesky and Mastodon public counters are fetched through ``metrics_fetch.fetch_metrics`` behind the
mocked ``publishers._send_http`` seam (no live request), the invented ``mockstats`` provider is
normalized from its fixture, and then the ``report`` CLI renders all three in JSON, Markdown and CSV.
The provider metric definitions differ, so the report must keep one block per platform, never add a
cross-platform total, and print an unknown value as ``null``/``unknown``, never as 0.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import test_metrics_fetch as mf
from test_metrics_fetch import FIXED_NOW, MASTO_ENV, FakeNetwork, at_uri, bsky_view, masto_status

from reachweaver.attribution import metric_definition
from reachweaver.metrics_fetch import MOCKSTATS_SOURCE, normalize_mockstats
from reachweaver.report_format import CSV_COLUMNS
from reachweaver.storage import METRIC_FIELDS, connect, store_metrics

PLATFORMS = ["bluesky", "mastodon", "mockstats"]
CROSS_PLATFORM_LABELS = {"total", "all", "overall", "combined", "sum", "all platforms", "all_platforms"}
MOCKSTATS_CASES = Path(__file__).parent / "fixtures" / "providers" / "mockstats" / "threshold_cases.json"


class TwoProvidersAndMockstatsReportTests(unittest.TestCase):
    """Borrows the mocked-network fixture of tests/test_metrics_fetch.py without re-running its tests."""

    setUp = mf.MetricsFetchTests.setUp
    post = mf.MetricsFetchTests.post
    run_fetch = mf.MetricsFetchTests.run_fetch
    cli = mf.MetricsFetchTests.cli
    assert_only_get = mf.MetricsFetchTests.assert_only_get

    def seed(self):
        self.bsky = self.post("mf-bluesky", remote_id=at_uri("3kccccccccc22"))
        self.masto = self.post("mf-mastodon", remote_id="113000000000000010")
        network = FakeNetwork(
            {"posts": [bsky_view("3kccccccccc22", likeCount=7, repostCount=2, replyCount=1, bookmarkCount=3)]},
            masto_status("113000000000000010", favourites_count=9, reblogs_count=4, replies_count=2),
        )
        with patch.dict("os.environ", MASTO_ENV):
            summary = self.run_fetch(network)
        self.assertEqual(sorted(e["post_id"] for e in summary["fetched"]), sorted([self.bsky, self.masto]))
        self.assert_only_get(network)
        cases = json.loads(MOCKSTATS_CASES.read_text(encoding="utf-8"))
        with connect(self.db) as con:
            con.execute("""INSERT INTO posts(id,account_id,platform,content,campaign,status,created_at,remote_id)
                           VALUES('ms1','mf-mockstats','mockstats','Hello','','published',?,'remote-ms1')""",
                        (FIXED_NOW,))
            store_metrics(con, "ms1", FIXED_NOW, MOCKSTATS_SOURCE,
                          **normalize_mockstats(cases["above_threshold"]["metrics"]))

    def report(self, fmt: str) -> str:
        code, out, err = self.cli("report", "--format", fmt)
        self.assertEqual(code, 0, err)
        return out

    def test_json_keeps_one_block_per_platform_and_unknown_is_null(self):
        self.seed()
        data = json.loads(self.report("json"))
        platforms = data["platforms"]
        self.assertEqual(list(platforms), PLATFORMS)  # three blocks, no cross-platform total
        self.assertFalse(CROSS_PLATFORM_LABELS & {key.lower() for key in data})
        self.assertIn("not unique people or comparable cross-platform views", data["note"])
        blue, masto, mock = (platforms[p] for p in PLATFORMS)
        # Each block holds its own provider's counts, never another platform's.
        self.assertEqual((blue["likes"], blue["reposts"], blue["replies"], blue["saves"]), (7, 2, 1, 3))
        self.assertEqual((masto["likes"], masto["reposts"], masto["replies"]), (9, 4, 2))
        self.assertEqual((mock["views"], mock["clicks"], mock["saves"]), (120, 14, 10))
        # Bluesky saves come from bookmarkCount; Mastodon has no saves counter, so unknown, never 0.
        self.assertIsNone(masto["saves"])
        self.assertEqual(masto["metrics"]["saves"]["status_counts"]["unknown"], 1)
        # Public counters have no views/impressions/clicks: null for both, never 0.
        for item in (blue, masto):
            for field in ("views", "impressions", "clicks", "signups"):
                self.assertIsNone(item[field], field)
                self.assertIsNone(item["metrics"][field]["value"], field)
                self.assertEqual(item["metrics"][field]["exact"], 0, field)
        # mockstats reactions_all is a different definition from likes: not mapped, so unknown.
        self.assertIsNone(mock["likes"])
        self.assertIsNone(mock["impressions"])
        # No value is ever the sum of two platforms.
        for field in METRIC_FIELDS:
            values = [platforms[p][field] for p in PLATFORMS if platforms[p][field] is not None]
            if len(values) > 1:
                for item in platforms.values():
                    self.assertNotEqual(item[field], sum(values), field)

    def test_metric_definitions_differ_between_the_two_providers(self):
        bsky, masto = metric_definition("bluesky", "saves"), metric_definition("mastodon", "saves")
        self.assertEqual((bsky["native_field"], bsky["availability"]), ("bookmarkCount", "api"))
        self.assertEqual((masto["native_field"], masto["availability"]), (None, "unknown"))
        shares = [metric_definition(p, "shares")["native_field"] for p in ("bluesky", "mastodon")]
        self.assertEqual(shares, ["repostCount", "reblogs_count"])
        for platform in ("bluesky", "mastodon"):
            self.assertEqual(metric_definition(platform, "views")["availability"], "unavailable")

    def test_markdown_has_no_total_row_and_prints_unknown_not_zero(self):
        self.seed()
        markdown = self.report("markdown")
        table = [line for line in markdown.splitlines()
                 if line.startswith("| ") and not line.startswith("| Platform")]
        exact_rows = {cells[0]: cells for cells in
                      ([c.strip() for c in line.strip("|").split("|")] for line in table)
                      if len(cells) == 4 + len(METRIC_FIELDS)}
        self.assertEqual(sorted(exact_rows), PLATFORMS)
        for line in table:
            first = line.strip("|").split("|")[0].strip().lower()
            self.assertNotIn(first, CROSS_PLATFORM_LABELS, line)
        column = {field: 3 + i for i, field in enumerate(METRIC_FIELDS)}
        self.assertEqual(exact_rows["bluesky"][column["saves"]], "3")
        self.assertEqual(exact_rows["mastodon"][column["saves"]], "unknown")
        for platform in ("bluesky", "mastodon"):
            for field in ("views", "impressions", "clicks", "signups"):
                self.assertEqual(exact_rows[platform][column[field]], "unknown", (platform, field))
        self.assertEqual(exact_rows["mockstats"][column["likes"]], "unknown")
        # A 0 appears only where a post really reported 0; none of these posts did.
        for cells in exact_rows.values():
            for field in METRIC_FIELDS:
                self.assertIsNone(re.fullmatch(r"0( \(.*\))?", cells[column[field]]), (cells[0], field))

    def test_csv_has_one_row_per_platform_metric_and_unknown_is_never_zero(self):
        self.seed()
        rows = list(csv.DictReader(io.StringIO(self.report("csv"))))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(len(rows), len(PLATFORMS) * len(METRIC_FIELDS))
        self.assertEqual(sorted({r["platform"] for r in rows}), PLATFORMS)
        by_key = {(r["platform"], r["metric"]): r for r in rows}
        self.assertEqual(by_key[("bluesky", "saves")]["value"], "3")
        self.assertEqual((by_key[("mastodon", "saves")]["value"], by_key[("mastodon", "saves")]["unknown_posts"]),
                         ("unknown", "1"))
        for platform in ("bluesky", "mastodon"):
            for field in ("views", "impressions", "clicks", "signups"):
                row = by_key[(platform, field)]
                self.assertEqual((row["value"], row["exact_posts"]), ("unknown", "0"), (platform, field))
        for row in rows:
            if row["exact_posts"] == "0":
                self.assertEqual(row["value"], "unknown", (row["platform"], row["metric"]))
                self.assertIn(row["estimated_sum"], ("", "unknown", "0"))  # no estimate leaks into value


if __name__ == "__main__":
    unittest.main()
