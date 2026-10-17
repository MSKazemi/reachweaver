"""Guard tests for issue #44 (browser-assist delivery): pin today's state until an owner decision.

No browser-assist delivery mode exists. Neither registry contains one, both validators reject it,
the skill and the live-pilot guide keep their "no browser/cookie automation" sentences, and every
human-only channel stays manual. Read-only: nothing here edits a registry, and adding the mode
needs the security review and owner decisions recorded for #44, not a change to these tests.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from reachweaver import capabilities, distribution, postiz
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("browser_assist", "browser-assist", "browserassist")
AGENT_NAMES = ("claude in chrome", "claude-in-chrome", "claude_in_chrome", "claude_chrome")
# Human-only channels named in #44; each is checked to exist in the distribution registry.
HUMAN_ONLY = ("reddit", "hacker_news", "discord", "whatsapp", "facebook_groups", "indie_hackers")
SKILL_SENTENCE = "Never assume an unofficial browser login/cookie automation is permissible."
PILOT_SENTENCE = "Never use stored login cookies or browser automation to evade restrictions."


def entry(registry: dict, platform_id: str) -> dict:
    return next(p for p in registry["platforms"] if p["id"] == platform_id)


class NoBrowserAssistModeTests(unittest.TestCase):
    def test_neither_registry_nor_the_mode_sets_contain_browser_assist(self):
        for path in (distribution.REGISTRY_FILE, capabilities.REGISTRY_FILE):
            with self.subTest(registry=path.name):
                text = path.read_text(encoding="utf-8").casefold()
                for name in NAMES:
                    self.assertNotIn(name, text)
        for modes in (distribution.DELIVERY_MODES, capabilities.ADAPTERS, capabilities.LIVE_ADAPTERS,
                      capabilities.REPLY_ADAPTERS):
            self.assertTrue(modes.isdisjoint(NAMES), sorted(modes))
        self.assertEqual(distribution.DELIVERY_MODES, frozenset({"manual_only", "mock_tested_text", "not_applicable"}))

    def test_distribution_validator_rejects_a_browser_assist_delivery(self):
        bundled = distribution.load_registry()
        self.assertEqual(distribution.validate_registry(bundled), [])
        for platform_id in ("dev", "x", "hacker_news"):  # an ordinary channel, a manual one, a human-only one
            with self.subTest(platform=platform_id):
                changed = copy.deepcopy(bundled)
                entry(changed, platform_id)["reachweaver_delivery"] = "browser_assist"
                errors = "; ".join(distribution.validate_registry(changed))
                self.assertIn(f"{platform_id}: invalid ReachWeaver delivery status", errors)
        changed = copy.deepcopy(bundled)
        entry(changed, "hacker_news")["reachweaver_delivery"] = "browser_assist"
        self.assertIn("Hacker News must remain human-authored, manual and unscheduled",
                      distribution.validate_registry(changed))

    def test_capability_validator_rejects_a_browser_assist_adapter(self):
        bundled = capabilities.load_registry()
        for platform_id in ("x", "linkedin", "reddit"):
            with self.subTest(platform=platform_id):
                changed = copy.deepcopy(bundled)
                entry(changed, platform_id)["reachweaver_adapter"] = "browser_assist"
                self.assertIn(f"{platform_id}: invalid reachweaver_adapter 'browser_assist'",
                              capabilities.validate_registry(changed))

    def test_no_runtime_module_or_cli_choice_mentions_browser_assist(self):
        for path in sorted((ROOT / "reachweaver").glob("*.py")):
            with self.subTest(module=path.name):
                text = path.read_text(encoding="utf-8").casefold()
                for name in NAMES:
                    self.assertNotIn(name, text)

    def test_no_runtime_module_recommends_a_browser_agent_extension(self):
        # Owner decision 2026-10-17 (account-advisor): setup coaching is a normal chat, never an
        # agent in the user's browser. Runtime code, including the skill bundle copies, names none.
        roots = (ROOT / "reachweaver", ROOT / ".claude/skills/reachweaver/scripts/reachweaver")
        for path in sorted(p for root in roots for p in root.glob("*.py")):
            with self.subTest(module=str(path.relative_to(ROOT))):
                text = path.read_text(encoding="utf-8").casefold()
                for name in AGENT_NAMES:
                    self.assertNotIn(name, text)


class NoBrowserAutomationWordingTests(unittest.TestCase):
    def test_skill_bundles_keep_the_no_cookie_automation_rule(self):
        for skill in (ROOT / ".claude/skills/reachweaver/SKILL.md", ROOT / "plugin/skills/reachweaver/SKILL.md"):
            with self.subTest(skill=str(skill.relative_to(ROOT))):
                self.assertIn(SKILL_SENTENCE, skill.read_text(encoding="utf-8"))

    def test_live_pilot_guide_keeps_the_no_browser_automation_rule(self):
        self.assertIn(PILOT_SENTENCE, (ROOT / "docs" / "FIRST-LIVE-PILOT.md").read_text(encoding="utf-8"))


class HumanOnlyChannelsStayManualTests(unittest.TestCase):
    def setUp(self):
        self.registry = distribution.load_registry()

    def test_the_named_human_only_channels_exist_and_stay_manual(self):
        for platform_id in HUMAN_ONLY:
            with self.subTest(platform=platform_id):
                item = entry(self.registry, platform_id)
                self.assertIs(item["human_only"], True)
                self.assertEqual(item["reachweaver_delivery"], "manual_only")
        self.assertEqual(entry(self.registry, "hacker_news")["scheduling"]["mode"], "prohibited")
        self.assertEqual(entry(self.registry, "reddit")["scheduling"]["mode"], "moderator_only")

    def test_every_human_only_entry_is_manual_only(self):
        human = [p for p in self.registry["platforms"] if p["human_only"]]
        self.assertTrue(human)
        self.assertEqual({p["reachweaver_delivery"] for p in human}, {"manual_only"})

    def test_reddit_has_no_adapter_and_no_backend_route(self):
        reddit = entry(capabilities.load_registry(), "reddit")
        self.assertEqual((reddit["status"], reddit["reachweaver_adapter"]), ("blocked", "none"))
        with self.assertRaisesRegex(ValidationError, "human-only"):
            postiz.require_backend_capability("reddit")
        capability_ids = {p["id"] for p in capabilities.load_registry()["platforms"]}
        for platform_id in HUMAN_ONLY:
            if platform_id in capability_ids:  # only reddit today; the others have no capability entry
                with self.subTest(platform=platform_id):
                    self.assertNotIn(entry(capabilities.load_registry(), platform_id)["reachweaver_adapter"],
                                     capabilities.LIVE_ADAPTERS)

    def test_the_bundled_registry_file_is_what_the_loader_returns(self):
        raw = json.loads(distribution.REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(raw, self.registry)


if __name__ == "__main__":
    unittest.main()
