"""CLI dispatch integrity: offline commands must return before the DB is opened."""
from __future__ import annotations

import ast
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]


class CliDispatchTests(unittest.TestCase):
    def test_every_early_offline_branch_ends_with_return(self):
        tree = ast.parse((ROOT / "reachweaver/cli.py").read_text(encoding="utf-8"))
        execute = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute")
        branches = [n for n in execute.body if isinstance(n, ast.If) and "args.command" in ast.unparse(n.test)]
        self.assertGreater(len(branches), 10)
        for branch in branches:
            with self.subTest(command=ast.unparse(branch.test)):
                self.assertIsInstance(branch.body[-1], (ast.Return, ast.Raise),
                                      "offline command falls through into the database block")

    def test_storage_report_is_not_shadowed_by_a_local_name(self):
        tree = ast.parse((ROOT / "reachweaver/cli.py").read_text(encoding="utf-8"))
        execute = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "execute")
        assigned = {t.id for n in ast.walk(execute) if isinstance(n, (ast.Assign, ast.For))
                    for t in ast.walk(n.targets[0] if isinstance(n, ast.Assign) else n.target)
                    if isinstance(t, ast.Name)}
        self.assertNotIn("report", assigned)

    def test_engagement_triage_never_creates_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "never.sqlite"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["--db", str(db), "engagement-triage", str(ROOT / "tests/fixtures/engagement/export.json")])
            self.assertEqual(code, 0)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
