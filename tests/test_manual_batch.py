"""Offline batch of manual packages (`manual-export-batch`, salvaged from draft PR #65)."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from reachweaver import manual_batch
from reachweaver.cli import main
from reachweaver.manual_batch import MAX_ITEMS, build_batch, write_batch
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/manual-batch.example.json"
GENERATED_AT = "2026-10-16T00:00:00Z"


def item(platform: str, text: str, **extra) -> dict:
    return {"platform": platform, "variant": {"platform": platform, "text": text}, **extra}


class BuildBatchTests(unittest.TestCase):
    def test_example_batch_is_ready_and_never_published(self):
        batch = build_batch(json.loads(EXAMPLE.read_text(encoding="utf-8")), generated_at=GENERATED_AT)
        self.assertEqual(batch["errors"], [])
        self.assertTrue(batch["ready_for_human_review"])
        self.assertEqual(batch["item_count"], 3)
        self.assertFalse(batch["published"])
        self.assertFalse(batch["scheduled"])
        self.assertIsNone(manual_batch.SEND_PATH)
        kinds = [i["package_kind"] for i in batch["items"]]
        self.assertEqual(kinds, ["reachweaver.manual-package.v1", "reachweaver.manual-package.v1",
                                 "reachweaver.manual-export.v1"])
        for summary in batch["items"]:
            self.assertEqual(summary["status"], "manual_export_not_published")

    def test_multilingual_text_is_hashed_exactly(self):
        text = "真实的技术分享 — نگاهی به مستندات"
        batch = build_batch({"schema_version": 1, "items": [item("weibo", text, at="2026-11-02T19:00:00+08:00")]})
        self.assertEqual(batch["errors"], [])
        summary = batch["items"][0]
        self.assertEqual(summary["files_sha256"], {"post.txt": hashlib.sha256(text.encode("utf-8")).hexdigest()})
        self.assertEqual(summary["planned_local_time"], "2026-11-02T19:00:00+08:00")
        self.assertTrue(any("advisory only" in w for w in batch["warnings"]))

    def test_per_platform_checks_are_the_manual_export_ones(self):
        batch = build_batch({"schema_version": 1, "items": [
            item("threads", "x" * 501),
            {"platform": "instagram", "variant": {"platform": "instagram", "format": "text", "text": "Hi"}},
            item("youtube", "Description only"),
        ]})
        joined = "\n".join(batch["errors"])
        self.assertIn("item 1 (threads):", joined)
        self.assertIn("item 2 (instagram):", joined)
        self.assertIn("item 3 (youtube):", joined)
        self.assertFalse(batch["ready_for_human_review"])

    def test_human_only_and_non_manual_destinations_are_refused(self):
        for platform in ("hacker_news", "reddit", "douban", "naver_cafe", "pantip", "discord"):
            with self.subTest(platform=platform):
                batch = build_batch({"schema_version": 1,
                                     "items": [{"platform": platform, "variant": {"talking_points": ["One note"]}}]})
                self.assertTrue(any("human-only" in e for e in batch["errors"]), batch["errors"])
        for platform in ("bluesky", "github", "gitee", "facebook_pages", "not_a_platform"):
            with self.subTest(platform=platform):
                batch = build_batch({"schema_version": 1, "items": [item(platform, "A post")]})
                self.assertTrue(any("not a manual-export destination" in e for e in batch["errors"]))

    def test_untrusted_and_invalid_inputs_block(self):
        bad_items = [
            {"platform": "xiaohongshu", "variant": {"platform": "wrong", "text": "Post"}},
            item("xiaohongshu", "TODO pending"),
            item("xiaohongshu", "Some‮text"),
            {"platform": "xiaohongshu", "variant": {"platform": "xiaohongshu", "text": "Safe",
                                                     "link_url": "http://example.com"}},
            item("xiaohongshu", "Post", at="2026-11-02T19:00:00"),
            item("xiaohongshu", "Post", extra_field=1),
            {"platform": "xiaohongshu", "variant": "not an object"},
        ]
        for bad in bad_items:
            with self.subTest(item=bad):
                self.assertTrue(build_batch({"schema_version": 1, "items": [bad]})["errors"])
        secret = {"platform": "xiaohongshu", "variant": {"platform": "xiaohongshu", "text": "Safe",
                                                          ("access_" + "token"): "not-used"}}
        with self.assertRaisesRegex(ValidationError, "secret-like"):
            build_batch({"schema_version": 1, "items": [secret]})

    def test_batch_shape_and_size_limits(self):
        for data in ({"items": [item("x", "Post")]}, {"schema_version": 1, "items": []},
                     {"schema_version": 1, "items": [item("x", "Post")], "extra": True},
                     {"schema_version": 1, "items": [item("x", f"Post {n}") for n in range(MAX_ITEMS + 1)]}):
            with self.subTest(data=str(data)[:60]), self.assertRaises(ValidationError):
                build_batch(data)
        full = build_batch({"schema_version": 1, "items": [item("x", f"Post number {n}") for n in range(MAX_ITEMS)]})
        self.assertEqual(full["errors"], [])
        self.assertEqual(full["item_count"], MAX_ITEMS)

    def test_repeated_platform_and_text_is_warned(self):
        batch = build_batch({"schema_version": 1, "items": [item("x", "Same post"), item("x", "Same post")]})
        self.assertEqual(batch["errors"], [])
        self.assertTrue(any("same platform and text as item 1" in w for w in batch["warnings"]))


class SharedTextCheckTests(unittest.TestCase):
    """The Trojan Source check from PR #65 now runs in every manual package, single or batched."""

    def test_bidi_invisible_and_del_characters_block_single_packages(self):
        from reachweaver.manual_export import platform_preflight
        from reachweaver.manual_package import package_preflight
        for text in ("Some‮text", "Join⁠ed", "BOM﻿inside", "Delete\x7fme"):
            with self.subTest(text=repr(text)):
                self.assertTrue(platform_preflight("threads", {"platform": "threads", "text": text})[0])
                self.assertTrue(package_preflight("x", {"platform": "x", "text": text})[0])
                self.assertTrue(package_preflight("reddit", {"talking_points": [text]})[0])

    def test_ordinary_multilingual_and_emoji_text_still_passes(self):
        from reachweaver.manual_package import package_preflight
        for text in ("می‌خواهم", "Family \U0001F468‍\U0001F469‍\U0001F467", "‏مرحبا abc"):
            with self.subTest(text=repr(text)):
                self.assertEqual(package_preflight("x", {"platform": "x", "text": text})[0], [])


class WriteBatchTests(unittest.TestCase):
    def test_writes_private_folders_with_matching_hashes_and_never_overwrites(self):
        batch = build_batch({"schema_version": 1, "items": [
            item("weibo", "第一条"),
            {"platform": "naver_blog", "variant": {"platform": "naver_blog", "text": "A technical note",
                                                   "platform_options": {"title": "Note"}}},
            {"platform": "tiktok", "variant": {"platform": "tiktok", "format": "video", "text": "Caption"},
             "account_meta": {"handle": "@creator"}},
        ]})
        self.assertEqual(batch["errors"], [])  # TikTok video without a manifest only warns
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "batch"
            written = write_batch(batch, folder)
            self.assertIn(str(folder / "batch-manifest.json"), written)
            self.assertEqual((folder / "01-weibo" / "post.txt").read_text(encoding="utf-8"), "第一条")
            self.assertEqual((folder / "02-naver_blog" / "title.txt").read_text(encoding="utf-8"), "Note")
            self.assertTrue((folder / "03-tiktok" / "caption.txt").is_file())
            manifest = json.loads((folder / "batch-manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("packages", manifest)
            self.assertFalse(manifest["published"])
            self.assertFalse(manifest["scheduled"])
            for summary in manifest["items"]:
                for name, digest in summary["files_sha256"].items():
                    data = (folder / summary["directory"] / name).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            names = {p.name for p in folder.rglob("*")}
            self.assertFalse({"BROWSER-TASK.md", "handoff.json", "content.txt"} & names)
            if os.name == "posix":
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
                for path in folder.rglob("*"):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600, path)
            with self.assertRaises(ValidationError):
                write_batch(batch, folder)

    def test_blocked_batch_writes_nothing(self):
        batch = build_batch({"schema_version": 1, "items": [item("x", "Fine"), item("reddit", "Not allowed")]})
        self.assertTrue(batch["errors"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                write_batch(batch, Path(tmp) / "out")
            self.assertFalse((Path(tmp) / "out").exists())

    def test_a_tampered_text_is_refused_by_the_hash_check(self):
        batch = build_batch({"schema_version": 1, "items": [item("x", "Reviewed text")]})
        tampered = copy.deepcopy(batch)
        tampered["packages"][0]["content"]["text"] = "Changed after review"
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValidationError, "SHA-256"):
            write_batch(tampered, Path(tmp) / "out")


class BatchCliTests(unittest.TestCase):
    def test_cli_writes_the_example_and_reports_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "packages"
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["manual-export-batch", "--file", str(EXAMPLE), "--out", str(out), "--json"])
            self.assertEqual(code, 0, stderr.getvalue())
            data = json.loads(stdout.getvalue())
            self.assertEqual(data["item_count"], 3)
            self.assertFalse(data["published"])
            self.assertTrue((out / "03-threads" / "caption.txt").is_file())
            self.assertIn("Nothing was sent, uploaded, scheduled or shared.", stderr.getvalue())

    def test_cli_blocked_batch_exits_2_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "batch.json"
            file.write_text(json.dumps({"schema_version": 1, "items": [item("hacker_news", "Show HN: text")]}),
                            encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["manual-export-batch", "--file", str(file), "--out", str(root / "out")])
            self.assertEqual(code, 2)
            self.assertIn("BLOCKED: nothing written", stdout.getvalue())
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
