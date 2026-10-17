"""China-aware manual packages: sourced limits, counting, topics and declarations.

Every fixture is synthetic and local; no test contacts a network host, and the module under test
has no send path.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from reachweaver import china
from reachweaver.cli import main
from reachweaver.distribution import load_registry
from reachweaver.manual_batch import build_batch, write_batch
from reachweaver.manual_package import build_package, generic_platforms, render_markdown, write_package
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_no_network import NetworkGuard  # noqa: E402

REGISTRY = {p["id"]: p for p in load_registry()["platforms"]}
ZH = "ReachWeaver 是一个离线优先的开源工具，帮你准备社交媒体帖子，人工审核后自己发布。"


def variant(platform: str, text: str = ZH, **options) -> dict:
    out: dict = {"platform": platform, "text": text}
    if options:
        out["platform_options"] = options
    return out


class Registry(unittest.TestCase):
    def test_every_profile_is_a_manual_only_non_human_registry_destination(self):
        for platform in china.platforms():
            with self.subTest(platform=platform):
                self.assertIn(platform, generic_platforms())
                self.assertEqual(REGISTRY[platform]["reachweaver_delivery"], "manual_only")
                self.assertFalse(REGISTRY[platform]["human_only"])

    def test_human_only_chinese_spaces_keep_the_notes_form(self):
        for platform in ("douban", "maimai", "qzone"):
            with self.subTest(platform=platform):
                self.assertFalse(china.is_china(platform))
                package = build_package(platform, {"talking_points": ["离线优先"]})
                self.assertNotIn("china", package)
                self.assertFalse(package["content"]["ready_to_paste"])

    def test_no_send_path_and_no_network_import(self):
        self.assertIsNone(china.SEND_PATH)
        source = inspect.getsource(china)
        for name in ("urllib", "requests", "http.client", "socket", "webbrowser", "publishers"):
            self.assertNotIn(f"import {name}", source)

    def test_every_fact_has_a_status_and_a_source_unless_unknown(self):
        facts = list(china.LEGAL.values())
        for profile in china.PROFILES.values():
            facts += [f for k, f in profile.items() if isinstance(f, dict) and "status" in f]
            facts += profile.get("notes", [])
        for fact in facts:
            with self.subTest(fact=fact.get("note") or fact.get("value")):
                self.assertIn(fact["status"], china.STATUSES)
                if fact["status"] == "unknown":
                    self.assertIsNone(fact["value"])
                else:
                    self.assertTrue(fact["source"].startswith("https://"))
                if fact["status"] == "verified" and fact["value"] is not None:
                    self.assertTrue(fact["quote"], "a verified fact carries its official quote")


class Counting(unittest.TestCase):
    def test_xhs_title_counts_ascii_as_half_like_the_creator_page(self):
        self.assertEqual(china.xhs_title_length("开源"), 2)
        self.assertEqual(china.xhs_title_length("abc"), 2)  # ceil(3 / 2)
        self.assertEqual(china.xhs_title_length("ab开源"), 3)
        self.assertEqual(china.xhs_title_length("^"), 1)  # the page weighs ^ as 2 units
        self.assertEqual(china.xhs_title_length("😀"), 2)  # two UTF-16 units above U+007F

    def test_han_count(self):
        self.assertEqual(china.han_count("开源 tool 工具"), 4)

    def test_topics_render_in_each_platform_syntax(self):
        self.assertEqual(china.render_topics("weibo", ["开源工具", "开发者社区"]), "#开源工具#\n#开发者社区#")
        self.assertEqual(china.render_topics("xiaohongshu", ["开源"]), "#开源")
        self.assertEqual(china.render_topics("bilibili", ["开源", "教程"]), "开源,教程")


class Preflight(unittest.TestCase):
    def test_weibo_verified_limit_blocks(self):
        package = build_package("weibo", variant("weibo", "开" * 5001))
        self.assertTrue(any("limit is 5000" in e for e in package["preflight"]["errors"]))
        self.assertFalse(build_package("weibo", variant("weibo", "开" * 5000))["preflight"]["errors"])
        self.assertEqual(package["limits"]["text"]["value"], 5000)

    def test_client_and_api_limits_only_warn(self):
        long_title = "离线准备社交媒体帖子的开源工具真好用而且免费"  # 22 weighted
        package = build_package("xiaohongshu", variant("xiaohongshu", title=long_title))
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertTrue(any("title length is 22" in w for w in package["preflight"]["warnings"]))
        self.assertFalse(any("title length" in w for w in build_package(
            "xiaohongshu", variant("xiaohongshu", title="ReachWeaver离线准备帖子"))["preflight"]["warnings"]))
        bili = build_package("bilibili", variant("bilibili", "介" * 300, title="题" * 80))
        self.assertEqual(bili["preflight"]["errors"], [])
        warnings = " ".join(bili["preflight"]["warnings"])
        self.assertIn("post text length is 300; the limit is 249", warnings)
        self.assertIn("title length is 80; the limit is 79", warnings)

    def test_weibo_topic_syntax_warnings(self):
        warnings = build_package("weibo", variant("weibo", ZH + " #开源工具# #AI #短#"))["preflight"]["warnings"]
        self.assertTrue(any("#AI has no closing #" in w for w in warnings))
        self.assertTrue(any("topic #短# must be 4-32" in w for w in warnings))
        self.assertFalse(any("开源工具" in w for w in warnings))

    def test_topics_option_is_checked_and_never_changes_the_text(self):
        package = build_package("xiaohongshu", variant("xiaohongshu", topics=[f"话题{i}" for i in range(11)]))
        self.assertTrue(any("topic count is 11" in w for w in package["preflight"]["warnings"]))
        self.assertEqual(package["content"]["text"], ZH)
        bad = build_package("weibo", variant("weibo", topics=["#开源#"]))
        self.assertIn("platform_options.topics must be a list of topic words without #", bad["preflight"]["errors"])
        self.assertFalse(any("platform_options.topics is not used" in w for w in package["preflight"]["warnings"]))

    def test_text_without_chinese_gets_a_localization_warning(self):
        warnings = build_package("zhihu", variant("zhihu", "An English-only post."))["preflight"]["warnings"]
        self.assertTrue(any("no Chinese characters" in w for w in warnings))
        self.assertFalse(any("no Chinese characters" in w
                             for w in build_package("zhihu", variant("zhihu"))["preflight"]["warnings"]))

    def test_generic_safety_rules_still_apply(self):
        errors = build_package("weibo", variant("weibo", "TODO 待定"))["preflight"]["errors"]
        self.assertTrue(any("placeholder" in e for e in errors))
        errors = build_package("weibo", variant("weibo", "开源‮工具"))["preflight"]["errors"]
        self.assertTrue(any("bidirectional" in e for e in errors))

    def test_weibo_image_size_from_the_media_section(self):
        assets = [{"kind": "image", "path": "big.png", "detected": {"bytes": 21 * 1024 * 1024}},
                  {"kind": "image", "path": "ok.png", "detected": {"bytes": 1024}}]
        errors, _ = china.media_check("weibo", assets)
        self.assertEqual(len(errors), 1)
        self.assertIn("big.png", errors[0])
        self.assertEqual(china.media_check("xiaohongshu", assets), ([], []))


class Decisions(unittest.TestCase):
    def test_ai_and_ad_decisions_on_every_profile_and_nothing_preselected(self):
        for platform in china.platforms():
            with self.subTest(platform=platform):
                decisions = build_package(platform, variant(platform))["china"]["human_decisions"]
                ids = [d["id"] for d in decisions]
                self.assertEqual(ids[:2], ["ai_generated_label", "advertising_label"])
                for decision in decisions:
                    self.assertIsNone(decision["selected"])
                    self.assertTrue(decision["required"])
                self.assertEqual(decisions[0]["source"], china.CAC_LABELING)
                self.assertEqual(decisions[1]["source"], china.SAMR_ADS)

    def test_native_option_names_and_originality(self):
        xhs = build_package("xiaohongshu", variant("xiaohongshu"))["china"]["human_decisions"]
        self.assertIn("笔记含AI合成内容", xhs[0]["question"])
        self.assertIn("内容包含营销广告", xhs[1]["question"])
        douyin = build_package("douyin", variant("douyin"))["china"]["human_decisions"]
        self.assertIn("内容由AI生成", douyin[0]["question"])
        for platform in ("wechat", "bilibili"):
            ids = [d["id"] for d in build_package(platform, variant(platform))["china"]["human_decisions"]]
            self.assertIn("originality", ids)


class Output(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_written_package_has_topics_file_and_exact_text(self):
        package = build_package("weibo", variant("weibo", topics=["开源工具"]))
        files = write_package(package, self.dir / "weibo")
        names = sorted(Path(f).name for f in files)
        self.assertEqual(names, ["CHECKLIST.md", "manual-export.json", "post.txt", "topics.txt"])
        self.assertEqual((self.dir / "weibo" / "post.txt").read_text(encoding="utf-8"), ZH)
        self.assertEqual((self.dir / "weibo" / "topics.txt").read_text(encoding="utf-8"), "#开源工具#")
        markdown = render_markdown(package)
        self.assertIn("## China (微博)", markdown)
        self.assertIn("Decisions only you can make", markdown)
        self.assertIn("Asia/Shanghai", markdown)
        self.assertIn("manual_export_not_published", (self.dir / "weibo" / "manual-export.json").read_text())

    def test_batch_hashes_topics_file(self):
        batch = build_batch({"schema_version": 1, "items": [
            {"platform": "weibo", "variant": variant("weibo", topics=["开源工具"]), "at": "2026-11-02T20:00:00+08:00"},
            {"platform": "xiaohongshu", "variant": variant("xiaohongshu")}]})
        self.assertEqual(batch["errors"], [])
        write_batch(batch, self.dir / "batch")
        manifest = json.loads((self.dir / "batch" / "batch-manifest.json").read_text(encoding="utf-8"))
        hashed = {name for item in manifest["items"] for name in item["files_sha256"]}
        self.assertIn("topics.txt", hashed)

    def test_cli_prints_decisions_and_sends_nothing(self):
        path = self.dir / "v.json"
        path.write_text(json.dumps(variant("douyin")), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with NetworkGuard(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["manual-export", "--platform", "douyin", "--variant", str(path),
                         "--out", str(self.dir / "douyin")])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("China-aware manual package", out.getvalue())
        self.assertIn("DECIDE YOURSELF: ai_generated_label (nothing pre-selected)", out.getvalue())
        self.assertIn("Nothing was sent", err.getvalue())

    def test_non_china_generic_packages_have_no_china_block(self):
        for platform in ("x", "dev", "product_hunt"):
            with self.subTest(platform=platform):
                self.assertNotIn("china", build_package(platform, {"platform": platform, "text": "Post"}))


if __name__ == "__main__":
    unittest.main()
