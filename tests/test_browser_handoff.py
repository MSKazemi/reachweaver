"""No-network acceptance checks for distribution-based browser handoff."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from reachweaver.browser_handoff import SEND_PATH, build_handoff, list_delivery_routes, write_handoff
from reachweaver.cli import main
from reachweaver.policy import ValidationError


class BrowserHandoffTests(unittest.TestCase):
    def test_routes_cover_regions_without_new_api_access(self):
        d = list_delivery_routes()
        self.assertEqual(d["platform_count"], 56)
        rows = {p["id"]: p for p in d["channels"]}
        for platform in ("xiaohongshu", "wechat_channels", "line_official",
                         "zalo_oa", "naver_blog", "kakao_channel", "vk", "moj"):
            self.assertEqual(rows[platform]["method"], "browser_assisted_manual_review")
            self.assertEqual(rows[platform]["api_automation"], "not_enabled")
        self.assertEqual(rows["bluesky"]["method"], "existing_mock_tested_api_with_owner_gates")
        self.assertEqual(rows["instagram"]["method"], "creator_export_and_browser_review")
        self.assertEqual(rows["hacker_news"]["method"], "human_only_no_generated_draft")
        self.assertEqual(rows["gitee"]["method"], "not_social_publishing")
        self.assertIsNone(SEND_PATH)

    def test_multilingual_exact_text_and_non_published_status(self):
        text = "真实的技术分享 — نگاهی به مستندات"
        p = build_handoff("xiaohongshu",
                          {"platform": "xiaohongshu", "format": "image", "text": text},
                          account="@demo", at="2026-11-02T19:00:00+08:00")
        self.assertEqual(p["content"], text)
        self.assertEqual(p["status"], "browser_handoff_not_published")
        self.assertFalse(p["approval_to_publish"])
        self.assertFalse(p["scheduled"])
        self.assertFalse(p["published"])
        self.assertEqual(p["content_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(p["requested_local_time"], "2026-11-02T19:00:00+08:00")
        self.assertIsNone(p["privacy_setting"])

    def test_human_only_and_non_publishing_blocked(self):
        for platform in ("hacker_news", "reddit", "douban", "naver_cafe", "pantip", "github", "gitee"):
            with self.subTest(platform=platform), self.assertRaises(ValidationError):
                build_handoff(platform, {"platform": platform, "text": "A post"}, account="@owner")

    def test_untrusted_and_invalid_inputs_rejected(self):
        variants = [
            {"platform": "wrong", "text": "Post"},
            {"platform": "xiaohongshu", "text": "TODO pending"},
            {"platform": "xiaohongshu", "text": "Some\u202etext"},
            {"platform": "xiaohongshu", "text": "Safe", ("access_" + "token"): "not-used"},
            {"platform": "xiaohongshu", "text": "Safe", "link_url": "http://example.com"},
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(ValidationError):
                build_handoff("xiaohongshu", variant, account="@owner")
        with self.assertRaises(ValidationError):
            build_handoff("xiaohongshu", {"platform": "xiaohongshu", "text": "Post"},
                          account="@owner", at="2026-11-02T19:00:00")
        with self.assertRaises(ValidationError):
            build_handoff("instagram", {"platform": "instagram", "format": "text", "text": "Post"},
                          account="@owner")

    def test_private_output_and_no_overwrite(self):
        p = build_handoff("weibo", {"platform": "weibo", "text": "中文更新"}, account="@owner")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "handoff"
            written = write_handoff(p, folder)
            self.assertEqual(len(written), 4)
            self.assertEqual((folder / "content.txt").read_text(), "中文更新")
            text = (folder / "BROWSER-TASK.md").read_text()
            self.assertIn("NEVER click the final", text)
            self.assertNotIn("中文更新", text)
            if os.name == "posix":
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in folder.iterdir()))
            with self.assertRaises(ValidationError):
                write_handoff(p, folder)

    def test_cli_end_to_end_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            variant = folder / "variant.json"
            variant.write_text(json.dumps({"platform": "naver_blog", "text": "Useful article"}))
            out = folder / "package"
            result = io.StringIO()
            with contextlib.redirect_stdout(result):
                self.assertEqual(main(["delivery-options", "--platform", "naver_blog", "--json"]), 0)
            self.assertIn("browser_assisted_manual_review", result.getvalue())
            result = io.StringIO()
            with contextlib.redirect_stdout(result):
                self.assertEqual(main(["browser-handoff", "--platform", "naver_blog",
                                       "--account", "@creator", "--variant", str(variant),
                                       "--out", str(out), "--json"]), 0)
            self.assertFalse(json.loads(result.getvalue())["published"])
            self.assertTrue((out / "BROWSER-TASK.md").is_file())


class BrowserBatchTests(unittest.TestCase):
    def test_batch_preflights_all_items_before_creating_directory(self):
        from reachweaver.browser_handoff import build_batch, write_batch
        base = {"schema_version": 1, "items": [
            {"platform": "weibo", "account": "@owner", "variant": {"platform": "weibo", "text": "第一条"}},
            {"platform": "naver_blog", "account": "@writer",
             "variant": {"platform": "naver_blog", "text": "A technical note"}},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "batch"
            batch = build_batch(base)
            self.assertEqual(batch["item_count"], 2)
            self.assertFalse(batch["published"])
            names = write_batch(batch, folder)
            self.assertEqual(len(names), 9)
            self.assertEqual((folder / "01-weibo" / "content.txt").read_text(), "第一条")
            manifest = json.loads((folder / "batch-manifest.json").read_text())
            self.assertFalse(manifest["scheduled"])
            self.assertEqual(len(manifest["items"]), 2)
            self.assertEqual(manifest["items"][1]["directory"], "02-naver_blog")
            if os.name == "posix":
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValidationError):
                write_batch(batch, folder)
            invalid = json.loads(json.dumps(base))
            invalid["items"][1]["platform"] = "reddit"
            invalid["items"][1]["variant"]["platform"] = "reddit"
            with self.assertRaises(ValidationError):
                build_batch(invalid)
            self.assertFalse((Path(tmp) / "invalid-batch").exists())

    def test_batch_cli_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"schema_version": 1, "items": [
                {"platform": "xiaohongshu", "account": "@owner",
                 "variant": {"platform": "xiaohongshu", "text": "A verified technical demo"}},
            ]}
            file = root / "batch.json"
            file.write_text(json.dumps(payload), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["browser-batch", "--file", str(file),
                             "--out", str(root / "packages"), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["item_count"], 1)
            self.assertTrue((root / "packages" / "01-xiaohongshu" / "content.txt").exists())
