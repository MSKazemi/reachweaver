"""Offline documentation contract tests for ReachWeaver channel research."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"


class GrowthReferencesTests(unittest.TestCase):
    def test_channel_and_playbook_references_are_bundled(self):
        for filename in ("channel-selection.md", "platform-playbooks.md"):
            content = (SKILL / "references" / filename).read_text(encoding="utf-8")
            self.assertGreater(len(content), 1000)
            self.assertIn("2026", content)

    def test_router_references_both_documents(self):
        content = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for filename in ("channel-selection.md", "platform-playbooks.md"):
            self.assertIn("references/" + filename, content)

    @unittest.skipUnless((ROOT / "docs/ECOSYSTEM-AUDIT-2026-10-08.md").exists(), "private-only design record")
    def test_audit_sources_and_community_safety(self):
        guide = (SKILL / "references/platform-playbooks.md").read_text(encoding="utf-8").lower()
        strategy = (SKILL / "references/channel-selection.md").read_text(encoding="utf-8").lower()
        audit = (ROOT / "docs/ECOSYSTEM-AUDIT-2026-10-08.md").read_text(encoding="utf-8")
        self.assertIn("do not automate posting", guide)
        self.assertIn("human-only", strategy)
        self.assertIn("not monthly active users", strategy)
        for name in ("coreyhaines31/marketingskills", "gitroomhq/postiz-app", "langchain-ai/social-media-agent"):
            self.assertIn(name, audit)


if __name__ == "__main__":
    unittest.main()
