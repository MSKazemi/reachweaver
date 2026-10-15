"""Offline routes per distribution channel (`delivery-options`, salvaged from draft PR #65)."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

from reachweaver import delivery_options
from reachweaver.cli import main
from reachweaver.delivery_options import ROUTES, list_delivery_routes
from reachweaver.distribution import load_registry
from reachweaver.manual_package import DEDICATED
from reachweaver.onboarding import blocker_audit
from reachweaver.policy import SUPPORTED_LIVE, ValidationError


class DeliveryOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = list_delivery_routes()
        cls.rows = {c["id"]: c for c in cls.result["channels"]}
        cls.registry = {p["id"]: p for p in load_registry()["platforms"]}

    def test_every_registry_channel_has_exactly_one_known_route(self):
        self.assertEqual(set(self.rows), set(self.registry))
        self.assertEqual(self.result["platform_count"], len(self.registry))
        self.assertTrue({c["route"] for c in self.result["channels"]} <= set(ROUTES))
        self.assertEqual(sum(self.result["route_counts"].values()), len(self.registry))
        self.assertIsNone(delivery_options.SEND_PATH)
        self.assertFalse(self.result["network_requests"])
        self.assertEqual((self.result["posts_created"], self.result["posts_scheduled"]), (0, 0))

    def test_routes_agree_with_the_onboarding_blocker_audit(self):
        audit = {r["id"]: r["kind"] for r in blocker_audit()["rows"] if r["registry"] == "distribution"}
        for platform_id, row in self.rows.items():
            with self.subTest(platform=platform_id):
                if row["reachweaver_delivery"] == "mock_tested_text":
                    self.assertEqual(row["route"], "official_api_adapter")
                else:
                    self.assertEqual(row["route"], audit[platform_id])

    def test_api_route_only_for_the_four_mock_tested_adapters(self):
        api = {pid for pid, row in self.rows.items() if row["route"] == "official_api_adapter"}
        self.assertEqual(api, {"bluesky", "mastodon", "telegram", "linkedin"})
        self.assertEqual(api, set(SUPPORTED_LIVE))

    def test_regional_channels_are_manual_and_never_api(self):
        for platform_id in ("xiaohongshu", "wechat_channels", "naver_blog", "kakao_talk_channel",
                            "line_official_account", "vk", "moj", "pixnet"):
            with self.subTest(platform=platform_id):
                row = self.rows[platform_id]
                self.assertEqual(row["route"], "native_manual")
                self.assertEqual(row["commands"], [f"reachweaver manual-export --platform {platform_id} "
                                                   "--variant VARIANT.json --out NEW_DIR"])
                self.assertEqual(row["account_connected"], "unknown_not_verified")

    def test_human_only_channels_get_only_the_notes_form(self):
        for platform_id, row in self.rows.items():
            if self.registry[platform_id]["human_only"]:
                with self.subTest(platform=platform_id):
                    self.assertEqual(row["route"], "human_only")
                    self.assertIn("writes no post text", row["note"])
        self.assertEqual(self.rows["hacker_news"]["native_scheduling"], "prohibited")
        self.assertEqual(self.rows["reddit"]["native_scheduling"], "moderator_only")

    def test_sourced_packages_use_the_manual_export_platform_name(self):
        self.assertEqual(self.rows["instagram"]["route"], "manual_export")
        self.assertIn("--platform facebook ", self.rows["facebook_pages"]["commands"][0])
        for platform_id in DEDICATED:
            self.assertEqual(self.rows[platform_id]["route"], "manual_export")
        self.assertEqual(self.rows["gitee"]["route"], "unsupported")
        self.assertEqual(self.rows["gitee"]["commands"], [])

    def test_no_route_or_wording_offers_browser_or_composer_automation(self):
        text = json.dumps(self.result).casefold()
        for needle in ("chrome", "browser_", "browser-", "composer", "handoff"):
            self.assertNotIn(needle, text)

    def test_unknown_platform_is_refused(self):
        with self.assertRaises(ValidationError):
            list_delivery_routes("not_a_platform")

    def test_cli_text_and_json_are_offline(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["delivery-options", "--platform", "naver_blog", "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["channels"][0]["route"], "native_manual")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["delivery-options", "--platform", "hacker_news"]), 0)
        self.assertIn("hacker_news: human_only (native scheduling: prohibited, human-only)", out.getvalue())
        self.assertIn("no API request, schedule or post was made", out.getvalue())


if __name__ == "__main__":
    unittest.main()
