"""The pilot report states its sample size, metric definitions, evidence sources and limits, and invents no lift (public issue #55).

Offline: the committed synthetic log and the protocol document. Nothing is fetched or written.
"""
from __future__ import annotations

import io
import json
import re
import unittest
from pathlib import Path

from reachweaver import pilot
from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "tests" / "fixtures" / "pilot" / "synthetic-pilot-log.csv"
PROTOCOL = (ROOT / "docs" / "FIELD-PILOT-PROTOCOL.md").read_text(encoding="utf-8")
FORBIDDEN_KEYS = {"winner", "significance", "significant", "lift", "p_value", "pvalue", "confidence_interval",
                  "uplift", "causal_effect", "improvement_percent"}
FORBIDDEN_WORDS = re.compile(r"guarantee|viral|proven|always works|will increase", re.I)


def keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from keys(value)


class DefinitionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = pilot.pilot_report(pilot.load_pilot_log(LOG))
        cls.defs = cls.report["metric_definitions"]

    def test_every_logged_measure_has_a_unit_a_source_and_a_definition_and_nothing_extra(self):
        self.assertEqual(set(self.defs["measures"]), set(pilot.MEASURES))
        for name, entry in self.defs["measures"].items():
            with self.subTest(measure=name):
                self.assertEqual(set(entry), {"unit", "source", "definition"})
                self.assertTrue(entry["unit"].strip())
                self.assertGreater(len(entry["source"]), 8)
                self.assertGreater(len(entry["definition"]), 40)

    def test_every_rate_names_known_measures_and_says_how_it_is_computed(self):
        self.assertEqual(set(self.defs["rates"]), set(pilot.RATES))
        for name, entry in self.defs["rates"].items():
            with self.subTest(rate=name):
                self.assertIn(entry["numerator"], pilot.MEASURES)
                self.assertIn(entry["denominator"], pilot.MEASURES)
                self.assertIn("where both are known", entry["meaning"])
                self.assertEqual((entry["numerator"], entry["denominator"]), pilot.RATES[name])

    def test_the_definitions_match_the_protocol_table_column_for_column(self):
        section = PROTOCOL.split("## 4. Measures and how each is recorded", 1)[1].split("\n## ", 1)[0]
        for name in pilot.MEASURES:
            with self.subTest(measure=name):
                self.assertRegex(section, rf"\| `{name}` \|")
        self.assertEqual(self.defs["protocol"], pilot.PROTOCOL_SECTION)
        self.assertIn("4-measures-and-how-each-is-recorded", self.defs["protocol"])

    def test_the_impressions_definition_says_blank_where_the_platform_exposes_none(self):
        text = self.defs["measures"]["impressions"]["definition"]
        self.assertIn("Bluesky and Mastodon", text)
        self.assertIn("unknown, not zero", text)
        self.assertIn("Bluesky and Mastodon do not expose view or impression counts", PROTOCOL)

    def test_clicks_means_attributed_sessions_not_platform_link_clicks(self):
        self.assertIn("Not platform 'link clicks'", self.defs["measures"]["clicks"]["definition"])
        self.assertIn("attribution-report", self.defs["measures"]["clicks"]["source"])

    def test_a_blank_cell_is_defined_as_unknown_everywhere(self):
        self.assertIn("unknown", self.defs["blank_cell"])
        self.assertIn("only when the value is known to be zero", self.defs["blank_cell"])


class SampleSizeAndLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = pilot.pilot_report(pilot.load_pilot_log(LOG))

    def test_every_arm_reports_its_sample_size_and_each_measure_its_coverage(self):
        self.assertTrue(self.report["arms"])
        for arm, summary in self.report["arms"].items():
            with self.subTest(arm=arm):
                self.assertGreater(summary["rows"], 0)
                self.assertGreater(summary["weeks"], 0)
                self.assertTrue(summary["first_date"] and summary["last_date"])
                for name, total in summary["totals"].items():
                    self.assertEqual(total["known_rows"] + total["unknown_rows"], summary["rows"], name)
                    self.assertTrue(0 <= total["coverage"] <= 1, name)

    def test_the_limits_are_stated_and_say_descriptive_only(self):
        limits = " ".join(self.report["limitations"])
        for needle in ("Descriptive only", "Blank cells are unknown", "owner-reported", "not an outcome"):
            self.assertIn(needle.lower(), limits.lower())
        self.assertEqual(self.report["comparison"].get("label", pilot.COMPARISON_LABEL), "descriptive_not_causal")

    def test_a_missing_value_is_unknown_never_zero(self):
        for summary in self.report["arms"].values():
            for name, total in summary["totals"].items():
                if total["known_rows"] == 0:
                    self.assertIsNone(total["sum"], name)

    def test_no_invented_lift_or_guarantee_anywhere_in_the_report(self):
        self.assertEqual(set(keys(self.report)) & FORBIDDEN_KEYS, set())
        text = json.dumps(self.report)
        self.assertIsNone(FORBIDDEN_WORDS.search(text), FORBIDDEN_WORDS.search(text))

    def test_a_comparison_is_refused_when_an_arm_is_too_short_rather_than_zeroed(self):
        short = pilot.pilot_report(pilot.load_pilot_log(LOG), min_weeks=52)
        self.assertEqual(short["comparison"]["status"], "refused")
        self.assertIn("metric_definitions", short)  # the definitions travel with every report, refused or not


class TextAndCliTests(unittest.TestCase):
    def test_the_text_report_points_at_the_definitions(self):
        text = pilot.render_report(pilot.pilot_report(pilot.load_pilot_log(LOG)))
        self.assertIn("metric_definitions", text)
        self.assertIn("docs/FIELD-PILOT-PROTOCOL.md", text)
        self.assertIn("Nothing was published", text)

    def test_the_cli_json_carries_the_definitions(self):
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["pilot-report", "--log", str(LOG), "--json"])
        data = json.loads(out.getvalue())
        self.assertEqual(code, 1 if data["rows_malformed"] else 0)  # the synthetic log has malformed rows on purpose
        self.assertEqual(set(data["metric_definitions"]["measures"]), set(pilot.MEASURES))


if __name__ == "__main__":
    unittest.main()
