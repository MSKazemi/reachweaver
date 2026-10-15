"""The regional official-API feasibility matrix stays sourced, dated and research-only (public issue #68).

docs/REGIONAL-API-FEASIBILITY-2026-10-15.md delivers the issue's first rule only. These tests pin that every provider
row exists with a reviewed date and an https official URL or an explicit "not verified" reason, that LINE is labelled
messaging rather than feed publishing, that no row claims a GO, an adapter or a receipt, and that the registries and the
live-send set are unchanged for these surfaces. Offline: nothing here contacts a provider.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from reachweaver import policy
from reachweaver.distribution import load_registry, validate_registry

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "REGIONAL-API-FEASIBILITY-2026-10-15.md"
COLUMNS = (
    "Provider", "Registry id", "Jurisdiction", "Account type", "Eligible posting surface", "Scopes",
    "Official docs/terms", "Owner eligibility", "Rate/cost", "Content types", "Scheduling", "Data retention",
    "Reviewed", "Confirmation action", "Verdict",
)
# provider -> (registry id, verdict prefix)
PROVIDERS = {
    "Douyin": ("douyin", "NO-GO"),
    "LINE Official Account": ("line_official_account", "NO-GO"),
    "WeChat Official Accounts": ("wechat", "CONDITIONAL"),
    "WeChat Channels": ("wechat_channels", "NO-GO"),
    "Bilibili": ("bilibili", "NO-GO"),
    "Weibo": ("weibo", "NO-GO"),
    "Xiaohongshu": ("xiaohongshu", "NO-GO"),
    "Kuaishou": ("kuaishou", "NO-GO"),
}
# registry values as of 2026-10-15; this research must not change any of them
REGISTRY_PIN = {
    "douyin": ("manual_only", False, "unverified"),
    "line_official_account": ("manual_only", False, "unverified"),
    "wechat": ("manual_only", False, "unverified"),
    "wechat_channels": ("manual_only", False, "unverified"),
    "bilibili": ("manual_only", False, "unverified"),
    "weibo": ("manual_only", False, "unverified"),
    "xiaohongshu": ("manual_only", False, "unverified"),
    "kuaishou": ("manual_only", False, "unverified"),
    "toutiao": ("manual_only", False, "unverified"),
}
NOT_VERIFIED = re.compile(r"not verified \([^)]+\)")
LINK = re.compile(r"\]\((https://[^)\s]+)\)")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_matrix(text: str) -> list[dict[str, str]]:
    start, end = "<!-- regional-feasibility:start -->", "<!-- regional-feasibility:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError("expected exactly one regional-feasibility block")
    lines = [ln.strip() for ln in text[text.index(start) + len(start):text.index(end)].splitlines() if ln.strip()]
    cells = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    if tuple(cells[0]) != COLUMNS:
        raise ValueError(f"header {cells[0]} != {list(COLUMNS)}")
    rows = []
    for number, row in enumerate(cells[2:], start=3):
        if len(row) != len(COLUMNS):
            raise ValueError(f"line {number} has {len(row)} cells, expected {len(COLUMNS)}")
        rows.append(dict(zip(COLUMNS, row)))
    return rows


class FeasibilityMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding="utf-8")
        cls.rows = {row["Provider"]: row for row in parse_matrix(cls.text)}

    def test_every_provider_row_exists_once_with_its_registry_id(self):
        self.assertEqual(set(self.rows), set(PROVIDERS))
        for provider, (pid, _) in PROVIDERS.items():
            with self.subTest(provider=provider):
                self.assertEqual(self.rows[provider]["Registry id"], f"`{pid}`")

    def test_every_row_has_a_reviewed_date_on_or_after_the_research_date(self):
        for provider, row in self.rows.items():
            with self.subTest(provider=provider):
                self.assertRegex(row["Reviewed"], DATE)
                self.assertGreaterEqual(row["Reviewed"], "2026-10-15")
                self.assertLessEqual(row["Reviewed"], "2026-10-16")

    def test_every_row_cites_an_https_official_page_or_says_why_it_is_not_verified(self):
        for provider, row in self.rows.items():
            docs = row["Official docs/terms"]
            with self.subTest(provider=provider):
                self.assertTrue(LINK.search(docs) or NOT_VERIFIED.search(docs), docs)
                self.assertNotIn("http://", docs)

    def test_a_bare_not_verified_always_carries_its_reason(self):
        for provider, row in self.rows.items():
            for column, cell in row.items():
                with self.subTest(provider=provider, column=column):
                    self.assertEqual(cell.count("not verified"), len(NOT_VERIFIED.findall(cell)), cell)

    def test_line_is_messaging_to_opted_in_friends_not_feed_publishing(self):
        line = self.rows["LINE Official Account"]
        self.assertTrue(line["Eligible posting surface"].startswith("Messaging, not a public feed"))
        for kind in ("reply", "push", "multicast", "narrowcast", "broadcast"):
            self.assertIn(kind, line["Eligible posting surface"])
        self.assertIn("NO-GO as a feed publisher", line["Verdict"])
        self.assertIn("reply messages not counted", line["Rate/cost"])
        self.assertIn("explicit consent", line["Data retention"])

    def test_verdicts_are_no_go_or_conditional_never_go(self):
        for provider, (_, verdict) in PROVIDERS.items():
            with self.subTest(provider=provider):
                self.assertTrue(self.rows[provider]["Verdict"].startswith(verdict))

    def test_no_row_claims_a_live_adapter_account_or_receipt(self):
        claims = re.compile(r"live_text|live_media|mock-tested|receipt|connected|SUPPORTED_LIVE|\bGO\b")
        for provider, row in self.rows.items():
            for column, cell in row.items():
                with self.subTest(provider=provider, column=column):
                    if column == "Verdict":
                        self.assertNotRegex(cell.replace("NO-GO", ""), r"\bGO\b")
                    else:
                        self.assertIsNone(claims.search(cell), cell)

    def test_douyin_records_the_official_scope_and_does_not_extend_to_toutiao(self):
        douyin = self.rows["Douyin"]
        self.assertIn("`video.create.bind`", douyin["Scopes"])
        self.assertIn("https://open.douyin.com/api/douyin/v1/video/create_video/", douyin["Eligible posting surface"])
        self.assertIn("redirects to 404", douyin["Official docs/terms"])
        self.assertIn("**Toutiao is not covered by the Douyin row.**", self.text)

    def test_wechat_official_accounts_and_channels_are_separate_rows(self):
        oa, channels = self.rows["WeChat Official Accounts"], self.rows["WeChat Channels"]
        self.assertIn("/cgi-bin/freepublish/submit", oa["Eligible posting surface"])
        self.assertIn("/cgi-bin/draft/add", oa["Eligible posting surface"])
        self.assertIn("No posting endpoint", channels["Eligible posting surface"])
        self.assertNotIn("freepublish", channels["Eligible posting surface"])

    def test_the_no_go_providers_keep_a_native_human_handoff(self):
        for provider in ("WeChat Channels", "Bilibili", "Xiaohongshu", "Kuaishou"):
            with self.subTest(provider=provider):
                self.assertTrue(self.rows[provider]["Confirmation action"].startswith("Native only"))

    def test_native_scheduling_is_separate_from_reachweaver_delivery_with_zero_reach_guarantee(self):
        section = self.text.split("## Native scheduling versus ReachWeaver delivery", 1)[1].split("\n## ", 1)[0]
        for needle in ("**Platform-native scheduling**", "**API-delivered ReachWeaver post**",
                       "**Locally queued ReachWeaver post**", "**Zero reach guarantee.**"):
            self.assertIn(needle, section)

    def test_the_remaining_issue_rules_are_owner_gated_and_marked_not_done(self):
        section = self.text.split("## Owner-gated next steps (not done)", 1)[1]
        self.assertIn("are **not done**", section)
        for needle in ("Developer account and app registration", "Scope and capability approval",
                       "Legal, IP and privacy review", "Mocked contract tests for the first authorized provider",
                       "First supervised receipt", "never accepts terms"):
            self.assertIn(needle, section)

    def test_the_matrix_is_linked_from_the_regional_catalog_and_the_platform_matrix(self):
        for name in ("REGIONAL-CHANNELS-2026-10-14.md", "PLATFORM-MATRIX.md"):
            with self.subTest(doc=name):
                self.assertIn("(REGIONAL-API-FEASIBILITY-2026-10-15.md)",
                              (ROOT / "docs" / name).read_text(encoding="utf-8"))


class RegistryUnchangedTests(unittest.TestCase):
    """The research changes no delivery, scheduling or live-send value."""

    @classmethod
    def setUpClass(cls):
        cls.registry = load_registry()
        cls.by_id = {p["id"]: p for p in cls.registry["platforms"]}

    def test_registry_is_valid_and_the_pinned_values_are_unchanged(self):
        self.assertEqual(validate_registry(self.registry), [])
        for pid, expected in REGISTRY_PIN.items():
            entry = self.by_id[pid]
            with self.subTest(platform=pid):
                self.assertEqual((entry["reachweaver_delivery"], entry["human_only"], entry["scheduling"]["mode"]),
                                 expected)

    def test_no_researched_surface_gains_an_adapter_or_a_live_send_path(self):
        capability = json.loads((ROOT / "reachweaver/platform_registry.json").read_text(encoding="utf-8"))
        adapters = {p["id"]: p.get("reachweaver_adapter") for p in capability["platforms"]}
        for pid in REGISTRY_PIN:
            with self.subTest(platform=pid):
                self.assertNotIn(pid, policy.SUPPORTED_LIVE)
                self.assertNotIn(pid, policy.SUPPORTED_LIVE_VIDEO)
                self.assertIn(adapters.get(pid, "none"), ("none", None))
        self.assertEqual(policy.SUPPORTED_LIVE, frozenset({"bluesky", "mastodon", "telegram", "linkedin"}))

    def test_hacker_news_stays_human_only_and_reddit_moderator_only(self):
        hn, reddit = self.by_id["hacker_news"], self.by_id["reddit"]
        self.assertEqual((hn["scheduling"]["mode"], hn["human_only"], hn["reachweaver_delivery"]),
                         ("prohibited", True, "manual_only"))
        self.assertEqual((reddit["scheduling"]["mode"], reddit["human_only"], reddit["reachweaver_delivery"]),
                         ("moderator_only", True, "manual_only"))


if __name__ == "__main__":
    unittest.main()
