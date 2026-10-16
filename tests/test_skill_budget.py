"""The skill stays cheap to load and free of paid model calls (offline).

ReachWeaver is meant to run inside the owner's Claude Code subscription: the always-loaded SKILL.md must stay a short
router, detail lives in references loaded on demand, and no source file may call a paid model API.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"
SKILL_MD_MAX_BYTES = 16_000  # was about 37,500 before the split into on-demand references
PAID_API = re.compile(r"api\.anthropic\.com|api\.openai\.com|openai\.azure\.com|cognitiveservices\.azure|"
                      r"ANTHROPIC_API_KEY|OPENAI_API_KEY|AZURE_OPENAI|import anthropic|from anthropic|"
                      r"import openai|from openai", re.I)


class SkillBudgetTests(unittest.TestCase):
    def test_skill_md_stays_a_short_router(self):
        size = len((SKILL / "SKILL.md").read_bytes())
        self.assertLessEqual(size, SKILL_MD_MAX_BYTES,
                             f"SKILL.md is {size} bytes; move detail into references/ (loaded on demand)")

    def test_the_subscription_statement_and_owner_only_steps_stay_in_the_router(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Runs on the owner's Claude Code subscription", text)
        self.assertIn("no API key", text)
        self.assertIn("Never add, suggest or run a paid LLM API call", text)
        self.assertRegex(text, r"Claude never runs")

    def test_the_detail_moved_out_of_the_router_is_still_reachable(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for name in ("routing-detail.md", "workflows.md", "cli-cookbook.md"):
            self.assertIn(f"references/{name}", text)
            self.assertGreater(len((SKILL / "references" / name).read_text(encoding="utf-8")), 2000)

    def test_no_source_file_calls_a_paid_model_api(self):
        sources = [*sorted((ROOT / "reachweaver").glob("*.py")), *sorted((ROOT / "scripts").glob("*.py")),
                   *sorted((ROOT / ".github").rglob("*.yml"))]
        self.assertGreater(len(sources), 40)
        for path in sources:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertIsNone(PAID_API.search(path.read_text(encoding="utf-8")))

    def test_the_claude_code_guide_exists_and_is_linked_from_the_front_page(self):
        guide = ROOT / "docs/CLAUDE-CODE.md"
        self.assertGreater(len(guide.read_text(encoding="utf-8")), 2000)
        self.assertIn("](docs/CLAUDE-CODE.md)", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("](CLAUDE-CODE.md)", (ROOT / "docs/README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
