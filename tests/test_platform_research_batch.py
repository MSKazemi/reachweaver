"""Registry entries for the community, creator and publishing surfaces researched on 2026-10-15 (public issues #15-#22, #27-#30, #32-#37).

Each entry is manual-only research metadata: no adapter, no scheduling claim without an official source, and a
``human_only`` flag where the surface's own rules make tool-drafted promotion inappropriate. Offline.
"""
from __future__ import annotations

import re
import unittest
from copy import deepcopy
from pathlib import Path

from reachweaver.distribution import SOURCE_REQUIRED, load_registry, validate_registry

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "COMMUNITY-AND-CREATOR-PLATFORMS-2026-10-15.md"
CHECKED = "2026-10-15"

# id -> (issue, scheduling mode, human_only)
EXPECTED = {
    "lobsters": (15, "unverified", True),
    "lemmy": (16, "native_conditional", True),
    "github_discussions": (17, "unverified", False),
    "huggingface": (18, "unverified", True),
    "quora": (19, "unverified", True),
    "stackexchange": (20, "unverified", True),
    "tumblr": (21, "native", True),
    "xing": (22, "unverified", True),
    "beehiiv": (27, "native_conditional", False),
    "ghost": (28, "native", False),
    "vimeo": (29, "unverified", False),
    "twitch": (30, "native_conditional", True),
    "flipboard": (32, "unverified", True),
    "researchgate": (33, "unverified", True),
    "behance": (34, "unverified", True),
    "dribbble": (35, "api_or_client", True),
    "viber": (36, "unverified", False),
    "slack_communities": (37, "native_conditional", True),
}
PARTLY_UNREADABLE = {"xing"}  # the developer site was read; the member terms are JavaScript-only and stay unverified


class BatchEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.entries = {p["id"]: p for p in cls.registry["platforms"]}

    def test_all_eighteen_entries_exist_and_the_registry_is_valid(self):
        self.assertEqual(set(EXPECTED) - set(self.entries), set())
        self.assertEqual(validate_registry(self.registry), [])

    def test_each_entry_is_manual_only_dated_today_and_never_an_adapter(self):
        for pid in EXPECTED:
            entry = self.entries[pid]
            with self.subTest(pid=pid):
                self.assertEqual(entry["reachweaver_delivery"], "manual_only")
                self.assertEqual(entry["checked_on"], CHECKED)
                self.assertLessEqual(entry["checked_on"], self.registry["reviewed_on"])
                self.assertTrue(entry["label"].strip() and entry["category"].strip())

    def test_scheduling_modes_and_human_only_flags_match_the_recorded_decisions(self):
        for pid, (_, mode, human) in EXPECTED.items():
            with self.subTest(pid=pid):
                self.assertEqual(self.entries[pid]["scheduling"]["mode"], mode)
                self.assertIs(self.entries[pid]["human_only"], human)

    def test_a_scheduling_claim_always_carries_an_https_official_source(self):
        for pid in EXPECTED:
            sched = self.entries[pid]["scheduling"]
            with self.subTest(pid=pid):
                if sched["mode"] in SOURCE_REQUIRED:
                    self.assertTrue(sched["official_source"].startswith("https://"))
                else:
                    self.assertEqual(sched["official_source"], "", "no source, so no claim: the mode must stay unverified")

    def test_every_entry_has_an_https_research_source_and_unread_parts_are_named(self):
        for pid in EXPECTED:
            with self.subTest(pid=pid):
                self.assertTrue(self.entries[pid]["research_source"].startswith("https://"))
                if pid in PARTLY_UNREADABLE:
                    self.assertRegex(self.entries[pid]["scheduling"]["scope"], r"could not be read|unverified")
                    self.assertEqual(self.entries[pid]["scheduling"]["mode"], "unverified")
                    self.assertTrue(self.entries[pid]["human_only"])

    def test_scope_text_is_specific_and_makes_no_reach_or_virality_promise(self):
        for pid in EXPECTED:
            scope = self.entries[pid]["scheduling"]["scope"]
            with self.subTest(pid=pid):
                self.assertGreater(len(scope), 60)
                self.assertNotRegex(scope.lower(), r"guarantee|viral|grow your|increase reach")

    def test_the_batch_does_not_change_the_protected_rules(self):
        self.assertTrue(self.entries["hacker_news"]["human_only"])
        self.assertEqual(self.entries["hacker_news"]["scheduling"]["mode"], "prohibited")
        self.assertEqual(self.entries["reddit"]["scheduling"]["mode"], "moderator_only")
        live = {p["id"] for p in self.registry["platforms"] if p["reachweaver_delivery"] == "mock_tested_text"}
        self.assertEqual(live, {"bluesky", "mastodon", "telegram", "linkedin"})

    def test_an_entry_with_a_native_mode_but_no_source_would_be_rejected(self):
        broken = deepcopy(self.registry)
        entry = next(p for p in broken["platforms"] if p["id"] == "ghost")
        entry["scheduling"]["official_source"] = ""
        self.assertTrue(any("ghost" in e for e in validate_registry(broken)))


class ResearchDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = DOC.read_text(encoding="utf-8")

    def test_the_doc_covers_every_platform_and_names_its_issue(self):
        for pid, (issue, _, _) in EXPECTED.items():
            with self.subTest(pid=pid):
                self.assertIn(f"`{pid}`", self.doc)
                self.assertIn(f"#{issue}", self.doc)

    def test_the_doc_states_the_read_date_the_method_and_what_was_not_verified(self):
        self.assertIn("2026-10-15", self.doc)
        self.assertIn("Not verified", self.doc)
        self.assertIn("summariz", self.doc.lower())  # the fetch tool summarizes; quotes need a live re-check
        self.assertIn("official", self.doc.lower())

    def test_every_source_link_is_https(self):
        links = re.findall(r"\]\((https?://[^)]+)\)", self.doc)
        self.assertTrue(links)
        self.assertTrue(all(link.startswith("https://") for link in links), links)

    def test_the_doc_keeps_hacker_news_and_reddit_out_of_this_batch(self):
        self.assertNotIn("| `hacker_news`", self.doc)
        self.assertNotIn("| `reddit`", self.doc)


REREAD = {"medium", "substack", "vk", "naver_blog", "naver_cafe", "line_openchat", "kakao_openchat", "snapchat"}


class RereadEntryTests(unittest.TestCase):
    """The eight entries first registered with only a landing page now say what was read and what was not (#13, #14, #23-#26, #31)."""

    @classmethod
    def setUpClass(cls):
        cls.entries = {p["id"]: p for p in load_registry()["platforms"]}
        cls.doc = DOC.read_text(encoding="utf-8")
        cls.section = cls.doc.split("## Re-read of eight earlier entries", 1)[1]

    def test_each_entry_names_its_source_date_and_what_is_unverified(self):
        for pid in REREAD:
            entry = self.entries[pid]
            with self.subTest(pid=pid):
                self.assertEqual(entry["checked_on"], CHECKED)
                self.assertTrue(entry["research_source"].startswith("https://"))
                self.assertEqual(entry["reachweaver_delivery"], "manual_only")
                self.assertEqual(entry["scheduling"]["mode"], "unverified")
                self.assertEqual(entry["scheduling"]["official_source"], "")
                self.assertRegex(entry["scheduling"]["scope"], r"unverified|nothing about|could be read|none is claimed")
                self.assertGreater(len(entry["scheduling"]["scope"]), 150)
                self.assertIn(entry["research_source"], self.doc)

    def test_the_quotes_in_the_entries_and_the_doc_match_each_other(self):
        medium = self.entries["medium"]["scheduling"]["scope"]
        self.assertIn("The Medium API is no longer supported. We do not recommend using it.", medium)
        self.assertIn("The Medium API is no longer supported. We do not recommend using it.", self.section)
        for text in ("6.3.6", "6.3.9", "5.13.4.1"):
            self.assertIn(text, self.entries["vk"]["scheduling"]["scope"])
            self.assertIn(text, self.section)
        self.assertIn("scrapes", self.section)
        self.assertIn("/v1/cafe/{clubid}/menu/{menuid}/articles", self.entries["naver_cafe"]["scheduling"]["scope"])

    def test_the_section_lists_what_was_not_verified_and_makes_no_reach_promise(self):
        self.assertIn("**Not verified:**", self.section)
        self.assertIn("means it is not in the pages named above", self.section)
        self.assertNotRegex(self.section.lower(), r"guarantee|viral|grow your|increase reach")

    def test_every_quote_in_the_section_is_25_words_or_fewer(self):
        for quote in re.findall(r'"([^"]+)"', self.section):
            with self.subTest(quote=quote[:40]):
                self.assertLessEqual(len(quote.split()), 25)


if __name__ == "__main__":
    unittest.main()
