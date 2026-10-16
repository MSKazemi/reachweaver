"""The example alert rules in docs/WORKER-OPERATIONS.md only use metric families that `ops-metrics` emits.

A rule naming a metric that does not exist never fires, silently. The rules were also loaded into a running
Prometheus 3.15.0 on 2026-10-16 (promtool check rules: 9 rules; all healthy, the pause alert went pending); this
offline test keeps the names from drifting afterwards.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]


def example_rules() -> str:
    text = (ROOT / "docs" / "WORKER-OPERATIONS.md").read_text(encoding="utf-8")
    section = text.split("### Example alert rules", 1)[1]
    return section.split("```yaml", 1)[1].split("```", 1)[0]


class ExampleAlertRulesTests(unittest.TestCase):
    def test_every_metric_in_the_example_rules_is_emitted_by_ops_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            db = str(Path(tmp) / "state" / "rw.sqlite")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--db", db, "pause"]), 0)
                self.assertEqual(main(["--db", db, "ops-metrics", "--format", "prometheus"]), 0)
        families = set(re.findall(r"^# TYPE (reachweaver_[a-z_]+) ", out.getvalue(), flags=re.M))
        used = set(re.findall(r"\breachweaver_[a-z_]+", example_rules()))
        self.assertGreaterEqual(len(used), 8)
        self.assertEqual(used - families, set())

    def test_the_example_has_nine_named_alerts(self) -> None:
        self.assertEqual(len(re.findall(r"^\s+- alert: ReachWeaver[A-Za-z]+$", example_rules(), flags=re.M)), 9)


if __name__ == "__main__":
    unittest.main()
