"""Offline contracts for DEV's verified, manual-first Claude Code workflow."""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "reachweaver"
GUIDE = ROOT / "docs" / "DEV-AUTHORITY-PLAYBOOK-2026-10-08.md"


class DEVGuidanceTests(unittest.TestCase):
    def test_skill_router_and_full_reference(self):
        router = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        reference = (SKILL / "references" / "dev-community.md").read_text(encoding="utf-8")
        self.assertIn("references/dev-community.md", router)
        self.assertGreater(len(reference), 3000)
        self.assertIn("https://dev.to/guidelines-for-ai-assisted-articles-on-dev/", reference)
        self.assertIn("https://dev.to/help/writing-editing-scheduling", reference)

    def test_guardrails_not_fictional_adapters(self):
        reference = (SKILL / "references" / "dev-community.md").read_text(encoding="utf-8").lower()
        playbook = GUIDE.read_text(encoding="utf-8").lower()
        for text in (reference, playbook):
            self.assertIn("ai-generated", text)
            self.assertIn("comments", text)
            self.assertIn("human", text)
            self.assertIn("manual", text)
            self.assertIn("canonical_url", text)
            self.assertIn("unknown", text)
            self.assertIn("not", text)
        self.assertIn("reachweaver does not implement", reference)
        self.assertIn("no verified current ranking weights", reference)
        self.assertIn("no dev authentication", playbook)

    def test_public_and_portable_templates_match(self):
        filename = "dev-article-brief.md"
        self.assertEqual(
            (ROOT / "examples" / filename).read_bytes(),
            (SKILL / "assets" / filename).read_bytes(),
        )
        header = (ROOT / "examples" / "dev-performance-log.csv").read_text(encoding="utf-8")
        for field in ("article_url", "native_views", "meaningful_conversations", "metric_source", "missingness_notes"):
            self.assertIn(field, header)

    def test_dev_agent_evaluation_cases(self):
        cases = [json.loads(line) for line in (ROOT / "evals" / "prompts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        by_id = {case["id"]: case for case in cases}
        for name in (
            "dev-comment-generation-prohibited",
            "dev-mass-follow-growth",
            "dev-native-scheduling-not-integration",
            "dev-ai-promotional-article",
            "dev-timing-and-missing-metrics",
        ):
            self.assertIn(name, by_id)
            self.assertGreaterEqual(len(by_id[name]["must"]), 3)


if __name__ == "__main__":
    unittest.main()
