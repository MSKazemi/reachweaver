"""Field-pilot operations log readout (issue #27 slice). All data here is SYNTHETIC.

The numbers in these tests and in tests/fixtures/pilot/ are invented test fixtures that
exercise arithmetic and refusal rules. They are not pilot results.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.pilot import COLUMNS, load_pilot_log, pilot_report, render_report
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/pilot/synthetic-pilot-log.csv"
HEADER = ",".join(COLUMNS)


def _row(day, arm, minutes="10", posts="1", dupes="0", errors="0", interventions="0",
         factual="0", clicks="", impressions="", conversions="", task="synthetic"):
    return ",".join((day, arm, task, minutes, posts, dupes, errors, interventions, factual,
                     clicks, impressions, conversions))


class PilotLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def write(self, *rows, header=HEADER):
        path = self.base / "log.csv"
        path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8")
        return path

    def report(self, *rows, **kwargs):
        return pilot_report(load_pilot_log(self.write(*rows)), **kwargs)

    def two_week_rows(self, arm, **values):
        return (_row("2026-01-05", arm, **values), _row("2026-01-12", arm, **values))

    # --- blanks are unknown, never zero -------------------------------------------------
    def test_blank_cells_are_unknown_not_zero(self):
        result = self.report(
            _row("2026-01-05", "manual", minutes="", clicks="5", impressions="100"),
            _row("2026-01-06", "manual", minutes="20", clicks="", impressions=""),
        )
        totals = result["arms"]["manual"]["totals"]
        self.assertEqual(totals["minutes_spent"], {"sum": 20.0, "known_rows": 1, "unknown_rows": 1,
                                                   "coverage": 0.5})
        self.assertEqual(totals["downstream_conversions"]["sum"], None)
        self.assertEqual(totals["downstream_conversions"]["unknown_rows"], 2)
        rates = result["arms"]["manual"]["rates"]
        # minutes_per_post uses only the row where BOTH minutes and posts are known.
        self.assertEqual(rates["minutes_per_post"]["value"], 20.0)
        self.assertEqual(rates["minutes_per_post"]["rows_used"], 1)
        self.assertEqual(rates["minutes_per_post"]["rows_excluded_unknown"], 1)
        self.assertEqual(rates["conversions_per_click"]["value"], None)
        self.assertEqual(rates["conversions_per_click"]["reason"], "no_rows_with_both_values_known")
        self.assertIsNone(rates["conversions_per_click"]["numerator"])

    def test_explicit_zero_is_known_and_zero_denominator_is_not_a_rate(self):
        result = self.report(_row("2026-01-05", "manual", clicks="0", impressions="0"))
        rate = result["arms"]["manual"]["rates"]["click_through_rate"]
        self.assertEqual((rate["numerator"], rate["denominator"], rate["rows_used"]), (0, 0, 1))
        self.assertIsNone(rate["value"])
        self.assertEqual(rate["reason"], "zero_denominator")
        self.assertEqual(result["arms"]["manual"]["totals"]["clicks"]["known_rows"], 1)

    # --- refusal on insufficient data ---------------------------------------------------
    def test_comparison_refused_when_an_arm_has_too_few_weeks(self):
        result = self.report(*self.two_week_rows("manual"), _row("2026-01-05", "reachweaver"),
                             _row("2026-01-06", "reachweaver"))
        comparison = result["comparison"]
        self.assertEqual(comparison["status"], "refused")
        self.assertIn("arm_reachweaver_has_1_of_2_required_weeks", comparison["reasons"])
        self.assertNotIn("rates", comparison)
        self.assertNotIn("difference_vs_baseline", comparison)
        # Per-arm totals are still reported.
        self.assertEqual(result["arms"]["reachweaver"]["rows"], 2)

    def test_comparison_refused_with_a_single_arm(self):
        result = self.report(*self.two_week_rows("manual"))
        self.assertEqual(result["comparison"]["status"], "refused")
        self.assertIn("fewer_than_two_arms_logged", result["comparison"]["reasons"])
        self.assertEqual(result["arms_not_logged"], ["scheduler", "reachweaver"])

    def test_min_weeks_is_configurable_and_validated(self):
        rows = (*self.two_week_rows("manual"), *self.two_week_rows("reachweaver"))
        self.assertEqual(self.report(*rows)["comparison"]["status"], "descriptive")
        self.assertEqual(self.report(*rows, min_weeks=3)["comparison"]["status"], "refused")
        for bad in (0, 53, "2", True):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.report(*rows, min_weeks=bad)

    def test_weeks_are_distinct_iso_weeks_not_row_counts(self):
        # Five rows inside one ISO week are still one week of data.
        rows = [_row(f"2026-01-0{d}", "scheduler") for d in range(5, 10)]
        result = self.report(*self.two_week_rows("manual"), *rows)
        self.assertEqual(result["arms"]["scheduler"]["weeks"], 1)
        self.assertEqual(result["arms"]["scheduler"]["iso_weeks"], ["2026-W02"])
        self.assertEqual(result["comparison"]["status"], "refused")

    # --- per-arm rates ------------------------------------------------------------------
    def test_per_arm_rates_carry_numerators_denominators_and_descriptive_label(self):
        result = self.report(
            _row("2026-01-05", "manual", minutes="30", posts="2", dupes="1", clicks="4", impressions="200"),
            _row("2026-01-12", "manual", minutes="10", posts="2", clicks="6", impressions="300"),
            _row("2026-01-05", "reachweaver", minutes="8", posts="1", interventions="2",
                 clicks="3", impressions="100", conversions="1"),
            _row("2026-01-13", "reachweaver", minutes="12", posts="3", errors="1", clicks="1",
                 impressions="100", conversions="0", task="reconcile"),
        )
        manual = result["arms"]["manual"]["rates"]
        self.assertEqual(manual["minutes_per_post"]["value"], 10.0)
        self.assertEqual(manual["duplicate_rate"], {
            "numerator_field": "duplicates", "denominator_field": "posts_published", "numerator": 1,
            "denominator": 4, "rows_used": 2, "rows_excluded_unknown": 0, "value": 0.25})
        self.assertEqual(manual["click_through_rate"]["value"], 0.02)
        rw = result["arms"]["reachweaver"]["rates"]
        self.assertEqual(rw["minutes_per_post"]["value"], 5.0)
        self.assertEqual(rw["unrecoverable_error_rate"]["value"], 0.25)
        self.assertEqual(rw["interventions_per_post"]["value"], 0.5)
        self.assertEqual(rw["conversions_per_click"]["value"], 0.25)
        self.assertEqual(result["arms"]["reachweaver"]["minutes_by_task"]["reconcile"]["minutes"], 12.0)
        comparison = result["comparison"]
        self.assertEqual(comparison["status"], "descriptive")
        self.assertEqual(comparison["label"], "descriptive_not_causal")
        self.assertEqual(comparison["baseline_arm"], "manual")
        self.assertEqual(comparison["difference_vs_baseline"]["minutes_per_post"]["reachweaver"], -5.0)
        self.assertEqual(comparison["rates"]["click_through_rate"]["manual"]["denominator"], 500)
        text = json.dumps(result).lower()
        for word in ("winner", "proven", "significant", "causes", "lift"):
            self.assertNotIn(word, text)

    def test_baseline_absent_gives_no_differences(self):
        result = self.report(*self.two_week_rows("scheduler"), *self.two_week_rows("reachweaver"))
        self.assertEqual(result["comparison"]["status"], "descriptive")
        self.assertIsNone(result["comparison"]["baseline_arm"])
        self.assertEqual(result["comparison"]["difference_vs_baseline"], {})

    # --- malformed rows reported, never repaired ----------------------------------------
    def test_malformed_rows_are_reported_with_line_numbers_and_excluded(self):
        result = pilot_report(load_pilot_log(FIXTURE))
        lines = {item["line"]: item["errors"] for item in result["malformed_rows"]}
        self.assertEqual(sorted(lines), [10, 11, 12, 13])
        self.assertIn("date must be YYYY-MM-DD", lines[10])
        self.assertTrue(any("arm must be one of" in e for e in lines[11]))
        self.assertTrue(any("minutes_spent" in e for e in lines[12]))
        self.assertTrue(any("duplicates cannot exceed" in e for e in lines[13]))
        self.assertEqual(result["rows_valid"], 8)
        self.assertEqual(result["arms"]["manual"]["rows"], 3)

    def test_non_numeric_extra_cells_and_bad_task_are_malformed(self):
        result = self.report(
            _row("2026-01-05", "manual", clicks="n/a"),
            _row("2026-01-05", "manual", posts="1.5"),
            _row("2026-01-05", "manual") + ",surplus",
            _row("2026-01-05", "manual", task=""),
            "2026-01-05,manual,truncated,10,1",
            _row("2026-01-05", "manual", minutes="7.5"),
        )
        self.assertEqual([m["line"] for m in result["malformed_rows"]], [2, 3, 4, 5, 6])
        self.assertIn("row has fewer cells than the header", result["malformed_rows"][4]["errors"])
        self.assertEqual(result["arms"]["manual"]["totals"]["minutes_spent"]["sum"], 7.5)

    def test_missing_column_rejects_the_file(self):
        header = ",".join(c for c in COLUMNS if c != "impressions")
        with self.assertRaisesRegex(ValidationError, "missing: impressions"):
            load_pilot_log(self.write(header=header))

    def test_clicks_above_impressions_is_a_warning(self):
        result = self.report(_row("2026-01-05", "manual", clicks="9", impressions="3"))
        self.assertEqual(result["rows_malformed"], 0)
        self.assertTrue(any("clicks exceed impressions" in w["warning"] for w in result["warnings"]))

    def test_template_is_header_only(self):
        template = ROOT / "examples/pilot-log.template.csv"
        self.assertEqual(template.read_text(encoding="utf-8"), HEADER + "\n")
        result = pilot_report(load_pilot_log(template))
        self.assertEqual((result["rows_valid"], result["arms"]), (0, {}))
        self.assertEqual(result["comparison"]["status"], "refused")
        self.assertIn("not causal", render_report(result))


class PilotCliIsolationTests(unittest.TestCase):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_uses_no_database_and_no_network(self):
        attempts = []

        def refuse(*args, **kwargs):
            attempts.append(args)
            raise RuntimeError("network access attempted")

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "never.sqlite"
            with patch.object(socket.socket, "connect", refuse), \
                    patch.object(socket, "create_connection", refuse), \
                    patch.object(socket, "getaddrinfo", refuse):
                code, out, _ = self.cli("--db", str(db), "pilot-report", "--log", str(FIXTURE), "--json")
                text_code, text, _ = self.cli("--db", str(db), "pilot-report", "--log",
                                              str(ROOT / "examples/pilot-log.template.csv"))
            self.assertFalse(db.exists())
            self.assertFalse(db.parent.exists())
        self.assertEqual(attempts, [])
        self.assertEqual(code, 1)  # the synthetic fixture deliberately contains malformed rows
        self.assertEqual(json.loads(out)["kind"], "reachweaver.pilot-report")
        self.assertEqual(text_code, 0)
        self.assertIn("COMPARISON REFUSED", text)

    def test_module_imports_no_storage_publisher_or_network_code(self):
        tree = ast.parse((ROOT / "reachweaver/pilot.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(("." * node.level) + (node.module or ""))
        self.assertEqual(imported - {"__future__", "csv", "math", "re", "datetime", "pathlib"},
                         {".policy"})


if __name__ == "__main__":
    unittest.main()
