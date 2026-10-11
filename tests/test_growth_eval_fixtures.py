"""Verify that the Claude growth-evaluation prompt corpus is valid JSONL."""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]

class GrowthEvalFixtureTests(unittest.TestCase):
    def test_cases_have_unique_ids_and_actions(self):
        path = ROOT / "evals/prompts.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        ids = [x["id"] for x in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(rows), 19)
        for case in rows:
            self.assertTrue(case["prompt"].strip())
            self.assertIsInstance(case["must"], list)
            self.assertGreaterEqual(len(case["must"]), 3)

if __name__ == "__main__":
    unittest.main()
