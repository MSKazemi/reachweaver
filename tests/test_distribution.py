"""Offline contracts for the global research registry and conservative planner."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reachweaver.distribution import (
    GOALS, build_distribution_plan, load_registry, validate_registry,
)

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"


class GlobalDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_registry()

    def test_version_and_complete_channel_coverage(self):
        self.assertEqual(self.data["schema_version"], 1)
        self.assertEqual(self.data["reviewed_on"], "2026-10-15")
        ids = {p["id"] for p in self.data["platforms"]}
        self.assertGreaterEqual(len(ids), 30)
        self.assertTrue({
            "reddit", "hacker_news", "linkedin", "youtube", "x", "bluesky",
            "mastodon", "product_hunt", "instagram", "tiktok", "threads",
            "facebook_groups", "discord", "telegram", "indie_hackers",
            "peerlist", "hashnode", "bilibili", "zhihu", "oschina",
            "csdn", "juejin", "gitee", "qiita", "zenn", "dev",
            "github", "snapcraft", "pinterest", "wechat",
        }.issubset(ids))
        market_ids = {m["id"] for m in self.data["markets"]}
        self.assertTrue({
            "global", "india", "china", "japan", "indonesia", "nigeria",
            "kenya", "south_africa", "ghana", "egypt", "morocco",
        }.issubset(market_ids))
        self.assertEqual(validate_registry(self.data), [])

    def test_added_regional_channels_remain_research_only(self):
        added = ["xiaohongshu","wechat_channels","toutiao","baidu_tieba","douban","maimai","qzone","note_jp","naver_blog","naver_cafe","kakao_openchat","dcard","ptt","pantip","line_openchat","zalo_official_account","kaskus","sharechat","moj","vk","odnoklassniki","dzen","habr","medium","substack","snapchat","kwai"]
        self.assertEqual(len(added), 27)
        self.assertEqual(len(self.data["platforms"]), 72)
        self.assertEqual(len(self.data["markets"]), 18)
        by_id = {p["id"]: p for p in self.data["platforms"]}
        for platform_id in added:
            platform = by_id[platform_id]
            self.assertEqual(platform["reachweaver_delivery"], "manual_only")
            self.assertEqual(platform["scheduling"]["mode"], "unverified")
            self.assertEqual(platform["scheduling"]["official_source"], "")
            self.assertEqual(platform["checked_on"], "2026-10-14")
            self.assertTrue(platform["research_source"].startswith("https://"))
        self.assertEqual(by_id["bilibili"]["checked_on"], "2026-10-08")
        for platform_id in ("baidu_tieba", "douban", "maimai", "qzone", "naver_cafe",
                            "kakao_openchat", "dcard", "ptt", "pantip", "line_openchat",
                            "zalo_official_account", "kaskus", "habr"):
            self.assertTrue(by_id[platform_id]["human_only"], platform_id)
        for region in ("south_korea", "taiwan", "thailand", "russia"):
            for goal in GOALS:
                plan = build_distribution_plan(region, goal)
                self.assertEqual(plan["status"], "OFFLINE_RESEARCH_PROPOSAL_NOT_SCHEDULED")
                self.assertFalse(plan["network_requests"])
                self.assertFalse(plan["account_access"])
                self.assertEqual(plan["posts_created"], 0)
                self.assertEqual(plan["posts_scheduled"], 0)
        self.assertEqual(build_distribution_plan("china", "awareness", limit=8)["channels"][2]["platform_id"], "xiaohongshu")
        self.assertNotIn("tiktok", next(m for m in self.data["markets"] if m["id"] == "india")["rankings"]["awareness"])

    def test_india_regional_networks_do_not_gain_live_send_capabilities(self):
        india_ids = ["josh","chingari","hipi","public_app","kutumb","arattai_channels","localcircles","pratilipi","rooter"]
        self.assertEqual(len(india_ids), 9)
        by_id = {p["id"]: p for p in self.data["platforms"]}
        self.assertNotIn("koo", by_id)  # Service announced closure in July 2024.
        self.assertEqual(by_id["sharechat"]["checked_on"], "2026-10-14")
        for platform_id in india_ids:
            platform = by_id[platform_id]
            self.assertEqual(platform["reachweaver_delivery"], "manual_only")
            self.assertEqual(platform["scheduling"]["mode"], "unverified")
            self.assertEqual(platform["scheduling"]["official_source"], "")
            self.assertEqual(platform["checked_on"], "2026-10-15")
            self.assertTrue(platform["research_source"].startswith("https://"))
        for platform_id in ("kutumb", "arattai_channels", "localcircles"):
            self.assertTrue(by_id[platform_id]["human_only"], platform_id)
        for platform_id in ("josh", "chingari", "hipi", "public_app", "pratilipi", "rooter"):
            self.assertFalse(by_id[platform_id]["human_only"], platform_id)
        for goal in GOALS:
            plan = build_distribution_plan("india", goal, limit=20)
            self.assertEqual(plan["status"], "OFFLINE_RESEARCH_PROPOSAL_NOT_SCHEDULED")
            self.assertFalse(plan["network_requests"])
            self.assertFalse(plan["account_access"])
            self.assertFalse(plan["publishing_approval_granted"])
            self.assertEqual(plan["posts_created"], 0)
            self.assertEqual(plan["posts_scheduled"], 0)
            self.assertTrue(all(p["account_connected"] == "unknown_not_verified" for p in plan["channels"]))
        awareness = [p["platform_id"] for p in build_distribution_plan("india", "awareness", limit=20)["channels"]]
        self.assertTrue({"sharechat", "moj", "josh", "chingari", "hipi"}.issubset(awareness))
        self.assertNotIn("tiktok", awareness)
        self.assertEqual(build_distribution_plan("india", "awareness")["channels"][-1]["platform_id"], "josh")

    def test_per_platform_evidence_dates_do_not_backfill_false_reviews(self):
        data = deepcopy(self.data)
        self.assertEqual(validate_registry(data), [])
        data["platforms"][0]["checked_on"] = "2026-10-16"
        self.assertTrue(any("cannot be later" in e for e in validate_registry(data)))
        data["platforms"][0]["checked_on"] = "not-a-date"
        self.assertTrue(any("ISO 8601" in e for e in validate_registry(data)))
        data["platforms"][0]["checked_on"] = "2026-10-08"
        data["platforms"][0]["research_source"] = "http://example.com"
        self.assertTrue(any("research_source" in e for e in validate_registry(data)))

    def test_missing_evidence_invalidates_native_schedule(self):
        d = deepcopy(self.data)
        p = next(x for x in d["platforms"] if x["id"] == "youtube")
        p["scheduling"]["official_source"] = ""
        self.assertTrue(any("youtube" in e for e in validate_registry(d)))

    def test_duplicate_unknown_and_invalid_market_fails(self):
        d = deepcopy(self.data)
        d["platforms"].append(deepcopy(d["platforms"][0]))
        d["markets"][0]["rankings"]["contributors"].append("not_a_platform")
        d["markets"][1]["timezone"] = "MadeUp/Zone"
        errors = validate_registry(d)
        self.assertTrue(any("duplicate platform" in e for e in errors))
        self.assertTrue(any("unknown channel" in e for e in errors))
        self.assertTrue(any("timezone" in e for e in errors))

    def test_documentation_matrix_is_deduplicated_and_scheduling_precise(self):
        matrix = (ROOT / "docs/PLATFORM-MATRIX.md").read_text(encoding="utf-8")
        rows = [line for line in matrix.splitlines() if line.startswith("| DEV Community |")]
        self.assertEqual(len(rows), 1, "DEV Community must have one capability row")
        self.assertNotIn("| DEV Community (DEV.to) |", matrix)
        registry = {item["id"]: item for item in self.data["platforms"]}
        self.assertEqual(registry["hashnode"]["scheduling"]["mode"], "native_conditional")
        self.assertIn("hashnode.com/changelog/", registry["hashnode"]["scheduling"]["official_source"])
        self.assertEqual(registry["hashnode"]["reachweaver_delivery"], "manual_only")

    def test_threads_native_scheduler_is_not_reachweaver_api(self):
        platforms = {p["id"]: p for p in self.data["platforms"]}
        threads = platforms["threads"]
        self.assertEqual(threads["scheduling"]["mode"], "native_conditional")
        self.assertIn("about.fb.com/news/2024/08/new-threads-features", threads["scheduling"]["official_source"])
        self.assertEqual(threads["reachweaver_delivery"], "manual_only")
        self.assertFalse(threads["human_only"])

    def test_no_false_remote_scheduler_or_connected_accounts(self):
        p = {x["id"]: x for x in self.data["platforms"]}
        self.assertEqual(p["hacker_news"]["scheduling"]["mode"], "prohibited")
        self.assertTrue(p["hacker_news"]["human_only"])
        self.assertEqual(p["reddit"]["scheduling"]["mode"], "moderator_only")
        self.assertEqual(
            {x["id"] for x in self.data["platforms"] if x["reachweaver_delivery"] == "mock_tested_text"},
            {"bluesky", "mastodon", "telegram", "linkedin"},
        )
        self.assertEqual(p["youtube"]["reachweaver_delivery"], "manual_only")
        self.assertEqual(p["bilibili"]["reachweaver_delivery"], "manual_only")
        plan = build_distribution_plan("china", "adoption")
        self.assertEqual(plan["status"], "OFFLINE_RESEARCH_PROPOSAL_NOT_SCHEDULED")
        self.assertFalse(plan["network_requests"])
        self.assertFalse(plan["account_access"])
        self.assertFalse(plan["publishing_approval_granted"])
        self.assertEqual(plan["posts_created"], 0)
        self.assertEqual(plan["posts_scheduled"], 0)
        self.assertTrue(all(x["account_connected"] == "unknown_not_verified" for x in plan["channels"]))

    def test_region_specific_priorities(self):
        self.assertEqual(build_distribution_plan("india", "adoption")["channels"][0]["platform_id"], "youtube")
        self.assertEqual(build_distribution_plan("china", "adoption")["channels"][0]["platform_id"], "bilibili")
        self.assertEqual(build_distribution_plan("japan", "contributors")["channels"][1]["platform_id"], "qiita")
        self.assertEqual(build_distribution_plan("nigeria", "collaboration")["channels"][0]["platform_id"], "linkedin")
        for region in ("india", "china", "japan", "indonesia", "nigeria", "kenya"):
            for goal in GOALS:
                self.assertGreater(len(build_distribution_plan(region, goal)["channels"]), 0)

    def test_local_timezone_conversion_and_daylight_saving(self):
        india = build_distribution_plan("india", "adoption", local_date="2026-10-14")
        self.assertEqual(india["illustrative_time_tests"][0]["local"], "2026-10-14T12:30:00+05:30")
        self.assertEqual(india["illustrative_time_tests"][0]["utc"], "2026-10-14T07:00:00+00:00")
        china = build_distribution_plan("china", "adoption", local_date="2026-10-14")
        self.assertEqual(china["illustrative_time_tests"][0]["local"], "2026-10-14T12:30:00+08:00")
        kenya = build_distribution_plan("kenya", "contributors", local_date="2026-10-14")
        self.assertEqual(kenya["illustrative_time_tests"][0]["utc"], "2026-10-14T09:30:00+00:00")
        rome_summer = build_distribution_plan("global", "adoption", local_date="2026-10-14", audience_timezone="Europe/Rome")
        rome_winter = build_distribution_plan("global", "adoption", local_date="2026-11-04", audience_timezone="Europe/Rome")
        self.assertEqual(rome_summer["illustrative_time_tests"][0]["utc"], "2026-10-14T10:30:00+00:00")
        self.assertEqual(rome_winter["illustrative_time_tests"][0]["utc"], "2026-11-04T11:30:00+00:00")
        self.assertTrue(all(not x["scheduled"] for x in india["illustrative_time_tests"]))

    def test_invalid_inputs_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Unknown region"):
            build_distribution_plan("everywhere", "adoption")
        with self.assertRaisesRegex(ValueError, "Unknown goal"):
            build_distribution_plan("india", "virality")
        with self.assertRaisesRegex(ValueError, "limit"):
            build_distribution_plan("india", "adoption", limit=99)
        with self.assertRaisesRegex(ValueError, "timezone"):
            build_distribution_plan("global", "adoption", local_date="2026-10-14")
        with self.assertRaisesRegex(ValueError, "IANA"):
            build_distribution_plan("global", "adoption", local_date="2026-10-14", audience_timezone="UTC+6")
        with self.assertRaisesRegex(ValueError, "date"):
            build_distribution_plan("india", "adoption", local_date="14/10/2026")

    def test_standalone_skill_has_same_data_and_runtime(self):
        for name in ("distribution.py", "distribution_registry.json", "cli.py"):
            self.assertEqual(
                (ROOT / "reachweaver" / name).read_bytes(),
                (SKILL / "scripts/reachweaver" / name).read_bytes(),
            )
        self.assertEqual((SKILL / "references/global-distribution.md").read_bytes(),
                         (ROOT / "plugin/skills/reachweaver/references/global-distribution.md").read_bytes())
        self.assertIn("references/global-distribution.md", (SKILL / "SKILL.md").read_text())
        self.assertIn("Hacker News", (SKILL / "references/global-distribution.md").read_text())

    def test_cli_planner_runs_without_creating_state_or_using_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "should-not-exist.sqlite"
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            cmd = [
                sys.executable, "-m", "reachweaver.cli", "--db", str(db),
                "distribution-plan", "--region", "india", "--goal", "contributors",
                "--date", "2026-10-14", "--json",
            ]
            result = subprocess.run(
                cmd, cwd=root, env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            content = json.loads(result.stdout)
            self.assertEqual(content["region"], "india")
            self.assertTrue(content["illustrative_time_tests"])
            self.assertFalse(db.exists())
            self.assertEqual(content["posts_scheduled"], 0)


if __name__ == "__main__":
    unittest.main()
