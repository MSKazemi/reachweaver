"""The hand-written decisions in docs/WEEKLY-REPORT-EXAMPLE.md stay tied to the data they quote (public issue #45).

``tests/test_weekly_report_example.py`` regenerates the tables. This module pins the prose written around them:
every figure it quotes must come from the generated block or the synthetic fixtures, and the derived ones must
equal what the report commands compute. If a fixture changes, the decisions can no longer go stale silently.
Offline: a temp SQLite and the committed synthetic fixtures.
"""
from __future__ import annotations

import csv
import json
import re
import unittest

from tests.test_weekly_report_example import BEGIN, DOC, END, FIXTURES, WeeklyReportExampleTests

SUPPORTED, UNSUPPORTED, LIMITS = ("## Decisions this evidence supports", "## Decisions this evidence cannot support",
                                  "## Known limitations of this example")


def numbers(text: str) -> set[str]:
    """Standalone integers, ignoring list numbering and anything glued to a word, id, path or code span."""
    text = re.sub(r"(?m)^\s*\d+\.\s", "", text)
    return set(re.findall(r"(?<![\w.`/-])\d+(?![\w.`/-])", text))


class ProseFixture(unittest.TestCase):
    build = WeeklyReportExampleTests.build  # same temp database, imports and report commands

    @classmethod
    def setUpClass(cls):
        cls.doc = DOC.read_text(encoding="utf-8")
        cls.generated = cls.doc.split(BEGIN, 1)[1].split(END, 1)[0]
        after = cls.doc.split(END, 1)[1]
        cls.supported = after.split(SUPPORTED, 1)[1].split(UNSUPPORTED, 1)[0]
        cls.unsupported = after.split(UNSUPPORTED, 1)[1].split(LIMITS, 1)[0]
        cls.limits = after.split(LIMITS, 1)[1]


class ProseStructureTests(ProseFixture):
    def test_the_three_hand_written_sections_exist_in_order(self):
        positions = [self.doc.index(h) for h in (BEGIN, END, SUPPORTED, UNSUPPORTED, LIMITS)]
        self.assertEqual(positions, sorted(positions))

    def test_the_example_still_says_it_is_synthetic_in_its_limitations(self):
        self.assertIn("synthetic", self.limits.lower())
        self.assertIn("say nothing about real platform behaviour", self.limits)


class ProseFiguresTests(ProseFixture):
    def test_every_figure_in_the_decisions_comes_from_the_generated_block_or_the_data(self):
        report, attribution, markdown = self.build()
        backing = self.generated + markdown + json.dumps(report) + json.dumps(attribution)
        for path in sorted(FIXTURES.iterdir()):
            backing += path.read_text(encoding="utf-8")
        allowed = numbers(backing)
        for name, section in (("supported", self.supported), ("cannot support", self.unsupported)):
            unbacked = sorted(numbers(section) - allowed, key=int)
            self.assertEqual(unbacked, [], f"figures in the '{name}' decisions that no fixture or report contains")

    def test_the_derived_figures_equal_what_the_report_commands_compute(self):
        report, attribution, _ = self.build()
        sessions = {p["platform"]: p["sessions"] for p in attribution["attributed"]["posts"]}
        self.assertEqual((sessions["bluesky"], sessions["linkedin"], sessions["mastodon"]), (69, 52, 19))
        self.assertIn("69 and 52, against 19 for Mastodon", self.supported)
        self.assertEqual(attribution["unattributed"]["sessions"], 149)
        self.assertIn("149 unattributed sessions (140 of them from a search referrer)", self.unsupported)
        rows = list(csv.DictReader((FIXTURES / "analytics.csv").read_text(encoding="utf-8").splitlines()))
        search = sum(int(r["sessions"]) for r in rows if "google." in (r["referrer"] or ""))
        self.assertEqual(search, 140)
        referrer_only = {row["platform"]: row["sessions"] for row in attribution["referrer_only"]["analytics"]}
        self.assertEqual(referrer_only["telegram"], 11)
        self.assertIn("The 11 sessions in section C reach Telegram only at domain level", self.supported)
        campaign_only = [r for r in rows if r["utm_campaign"] == "rw-v0-3-release" and not r["utm_content"]]
        self.assertEqual([int(r["sessions"]) for r in campaign_only], [6])
        self.assertIn("campaign-only row (6 sessions, `rw-v0-3-release`, no `utm_content`)", self.supported)

    def test_the_linkedin_claims_match_the_snapshot_and_the_coverage_gaps(self):
        report, attribution, _ = self.build()
        linkedin = report["platforms"]["linkedin"]
        self.assertEqual(linkedin["clicks"], 37)
        self.assertIn("`LINK_CLICKS` (37)", self.unsupported)
        posts = {p["platform"]: p for p in attribution["attributed"]["posts"]}
        self.assertIsNone(posts["linkedin"]["conversions"])
        self.assertIn("conversions `unknown (0)`", self.supported)
        gaps = {g["gap"]: g for g in attribution["coverage_gaps"]}
        self.assertEqual(gaps["conversions_unknown_rows"]["rows"], 2)
        self.assertEqual(gaps["published_posts_with_untagged_links"]["post_ids"], ["5e0b000000000004"])
        self.assertEqual(gaps["published_posts_without_matching_utm_rows"]["post_ids"], ["5e0b000000000004"])
        self.assertIn("`conversions_unknown_rows` is 2", self.supported)
        self.assertEqual(linkedin["window_coverage"]["with_window"], 1)  # the only snapshot with a stated window
        for platform in ("bluesky", "mastodon"):
            self.assertEqual(report["platforms"][platform]["window_coverage"]["with_window"], 0)


if __name__ == "__main__":
    unittest.main()
