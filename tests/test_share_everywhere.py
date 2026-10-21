"""Share everywhere (owner decision 2026-10-21): copy-only packages for the text networks ReachWeaver can also send
to, deterministic per-network fitting, and offline media header checks. Synthetic data; no network, no database.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.manual_export import platform_preflight
from reachweaver.policy import SUPPORTED_LIVE, ValidationError, validate_post
from reachweaver.richtext import grapheme_count
from reachweaver.share import API_NOTE, api_text_platforms, destinations, make_packages
from reachweaver.share_tailor import ELLIPSIS, RULES, fit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_media import mp4_bytes, png_bytes  # noqa: E402
from test_no_network import NetworkGuard  # noqa: E402

API = ("bluesky", "linkedin", "mastodon", "telegram")
WORDS = ("Small steady releases help new creators learn the tool without fear of breaking anything "
         "important along the way, and every note in the changelog explains one change in plain words. ")
LONG = WORDS * 40  # about 4,500 characters of ordinary words


class ShareCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "packages"
        self.db = self.root / "absent.sqlite"

    def cli(self, *args, db=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--db", str(db or self.db), "--accounts", str(self.root / "no-accounts.json"), *args])
            self.assertEqual(guard.attempts, [])
        if db is None:
            self.assertFalse(self.db.exists(), "share must not create a database")
        return code, stdout.getvalue(), stderr.getvalue()


class ApiCapableNetworksTests(ShareCase):
    def test_the_four_api_text_networks_are_offered_as_copy_only(self):
        self.assertEqual(set(api_text_platforms()), set(SUPPORTED_LIVE))
        self.assertEqual(destinations(["Bluesky", "Mastodon", "telegram", "LinkedIn"]),
                         ["bluesky", "mastodon", "telegram", "linkedin"])
        self.assertEqual(destinations(["bsky"]), ["bluesky"])

    def test_package_per_network_with_the_send_note_and_no_database(self):
        code, output, errors = self.cli("share", "--platform", ",".join(API), "--text",
                                        "A small update for new creators #opensource https://example.org/notes",
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("NOT PUBLISHED", output)
        readme = (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8")
        self.assertIn(API_NOTE, readme)
        for platform in API:
            folder = self.out / platform
            self.assertEqual((folder / "post.txt").read_text(encoding="utf-8"),
                             "A small update for new creators #opensource https://example.org/notes")
            package = json.loads((folder / "manual-export.json").read_text(encoding="utf-8"))
            self.assertEqual(package["status"], "manual_export_not_published")
            self.assertEqual(package["share"]["route"], "api_capable_copy")
            self.assertEqual(package["share"]["send_from_reachweaver"], API_NOTE)
            self.assertIn(API_NOTE, package["notice"])
            self.assertEqual(package["limits"]["text"]["display"], RULES[platform][0])
            self.assertIn(API_NOTE, (folder / "CHECKLIST.md").read_text(encoding="utf-8"))
        bluesky = json.loads((self.out / "bluesky" / "manual-export.json").read_text(encoding="utf-8"))
        kinds = {facet["type"] for facet in bluesky["content"]["bluesky_preview"]["facets"]}
        self.assertEqual(kinds, {"link", "tag"})

    def test_an_existing_database_is_not_touched(self):
        db = self.root / "state.sqlite"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", str(db), "init"]), 0)
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        code, _, errors = self.cli("share", "--platform", "bluesky,mastodon", "--text", "A copy-only update",
                                   "--out", str(self.out), db=db)
        self.assertEqual(code, 0, errors)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before, "share wrote to the database")
        with sqlite3.connect(db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_human_only_and_moderator_only_rules_are_unchanged(self):
        for venue in ("reddit", "hacker_news"):
            code, _, errors = self.cli("share", "--platform", f"bluesky,{venue}", "--text", "A ready post",
                                       "--out", str(self.root / venue))
            self.assertEqual(code, 2)
            self.assertIn("Human-only", errors)
            self.assertFalse((self.root / venue).exists())
        code, _, errors = self.cli("share", "--platform", "hacker_news", "--text", "A ready post",
                                   "--out", str(self.root / "hn"))
        self.assertEqual(code, 2)
        code, _, errors = self.cli("share", "--platform", "bluesky,reddit", "--text", "A ready post",
                                   "--note", "Read the rules first", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertFalse((self.out / "reddit" / "post.txt").exists())
        self.assertTrue((self.out / "reddit" / "NOTES.md").exists())

    def test_draft_check_rule_blocks_with_no_shorten(self):
        code, _, errors = self.cli("share", "--platform", "bluesky", "--text", LONG[:900], "--no-shorten",
                                   "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("bluesky limit is 300", errors)
        self.assertFalse(self.out.exists())


class TailoringTests(ShareCase):
    def test_long_caption_is_shortened_per_network_and_flagged(self):
        code, output, errors = self.cli("share", "--platform", "bluesky,mastodon,threads,x", "--text", LONG[:1200],
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("SHORTENED", output)
        bluesky = (self.out / "bluesky" / "post.txt").read_text(encoding="utf-8")
        self.assertTrue(bluesky.endswith(ELLIPSIS))
        self.assertLessEqual(grapheme_count(bluesky), 300)
        self.assertEqual(validate_post("bluesky", bluesky), [])
        self.assertTrue(LONG.startswith(bluesky[:-1]), "only the end is cut, at a word boundary")
        package = json.loads((self.out / "bluesky" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertTrue(package["share"]["tailoring"]["shortened"])
        self.assertIn("SHORTENED", package["preflight"]["warnings"][0])
        # X has no known limit in ReachWeaver: left exactly as written, with a guardrail note.
        self.assertEqual((self.out / "x" / "post.txt").read_text(encoding="utf-8"), LONG[:1200])
        x_package = json.loads((self.out / "x" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertFalse(x_package["share"]["tailoring"]["shortened"])
        self.assertIn("not a platform rule", x_package["preflight"]["warnings"][0])

    def test_every_fitted_text_passes_the_rule_draft_check_or_manual_export_applies(self):
        for platform in RULES:
            result = fit(platform, LONG + LONG)
            self.assertTrue(result["shortened"], platform)
            if platform in SUPPORTED_LIVE:
                self.assertEqual(validate_post(platform, result["text"]), [], platform)
            else:
                errors, _ = platform_preflight(platform, {"text": result["text"], "format": "video",
                                                          "platform_options": {"title": "T", "target_type": "page"}})
                self.assertEqual(errors, [], platform)

    def test_text_within_limits_and_hashtag_order_are_never_changed(self):
        text = "#launch Our #python tool is out, read more at https://example.org #opensource"
        packages, errors = make_packages(["bluesky", "threads", "instagram", "x", "linkedin"], text, [], None,
                                         None, [], None)
        self.assertEqual(errors, [])
        for platform, package in packages:
            self.assertEqual(package["content"]["text"], text, platform)
            self.assertFalse(package["share"]["tailoring"]["shortened"], platform)

    def test_one_unbreakable_word_still_blocks(self):
        packages, errors = make_packages(["bluesky"], "x" * 400, [], None, None, [], None)
        self.assertTrue(any("bluesky limit" in e for e in errors), errors)

    def test_removed_hashtags_are_named(self):
        result = fit("bluesky", WORDS * 3 + " #tail #end")
        self.assertTrue(result["shortened"])
        self.assertEqual(result["removed_hashtags"], ["#tail", "#end"])
        self.assertIn("#tail", result["note"])


class MediaCheckTests(ShareCase):
    def test_attached_video_is_checked_against_sourced_limits(self):
        video = self.root / "clip.mp4"
        video.write_bytes(mp4_bytes(1080, 1920, 700))
        code, output, errors = self.cli("share", "--platform", "bluesky,instagram,threads", "--text",
                                        "Watch the demo", "--attach", str(video), "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("MEDIA PROBLEM", output)
        bluesky = json.loads((self.out / "bluesky" / "manual-export.json").read_text(encoding="utf-8"))
        warnings = "\n".join(bluesky["preflight"]["warnings"])
        self.assertIn("duration 700.0s exceeds 600s", warnings)
        self.assertNotIn("no media manifest supplied", warnings)
        self.assertEqual(bluesky["share"]["media_checks"][0]["table"], "bluesky")
        instagram = json.loads((self.out / "instagram" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertNotIn("exceeds", "\n".join(instagram["preflight"]["warnings"]))
        threads = json.loads((self.out / "threads" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertIn("no sourced video limits", "\n".join(threads["preflight"]["warnings"]))
        self.assertFalse((self.out / "bluesky" / "clip.mp4").exists(), "media is never copied")

    def test_image_aspect_ratio_problem_and_unreadable_file(self):
        wide = self.root / "wide.png"
        wide.write_bytes(png_bytes(1000, 300))
        broken = self.root / "broken.mp4"
        broken.write_bytes(b"not really a video")
        code, output, errors = self.cli("share", "--platform", "instagram", "--text", "Our banner",
                                        "--attach", str(wide), "--attach", str(broken), "--preview")
        self.assertEqual(code, 0, errors)
        self.assertIn("aspect ratio", output)
        self.assertIn("unrecognized file header", output)

    def test_media_manifest_path_is_unchanged(self):
        packages, _ = make_packages(["instagram"], "caption", [], None, "image", [], None)
        self.assertIn("no media manifest supplied", "\n".join(packages[0][1]["preflight"]["warnings"]))


class BrowserTests(unittest.TestCase):
    def test_catalog_and_incoming_accept_api_networks_as_copy_only(self):
        from reachweaver.share_browser import _incoming, _safe_preview, catalog
        items = {p["id"]: p for p in catalog()}
        self.assertTrue(items["bluesky"]["api_capable"])
        self.assertFalse(items["instagram"]["api_capable"])
        self.assertTrue(items["reddit"]["human_only"])
        packages = _incoming({"platforms": ["bluesky"], "text": LONG[:800]})
        preview = _safe_preview(packages)[0]
        self.assertEqual(preview["note"], API_NOTE)
        self.assertTrue(preview["shortened"])
        self.assertFalse(preview["published"])
        with self.assertRaises(ValidationError):
            _incoming({"platforms": ["hacker_news"], "text": "A ready post"})


if __name__ == "__main__":
    unittest.main()
