"""One source -> per-platform variants -> media cut list (public issue #8), end to end and offline.

``sources-scan`` (offline fixtures) -> ``signal-brief`` -> ``media-storyboard`` -> ``media-check``.
No network, no database, no generated copy or media: every creative field stays a placeholder.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "examples/sources.reachweaver.json"
BRAND = ROOT / "examples/brand.reachweaver.json"
ACCOUNTS = ROOT / "config/accounts.example.json"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class RepurposingChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        guard = mock.patch.object(socket, "socket", side_effect=AssertionError("network used"))
        guard.start()
        cls.addClassCleanup(guard.stop)
        cls.addClassCleanup(cls._tmp.cleanup)
        code, out, _ = run("sources-scan", "--config", str(SOURCES), "--json")
        assert code == 0, out
        cls.scan = cls.dir / "scan.json"
        cls.scan.write_text(out, encoding="utf-8")
        cls.signal = json.loads(out)["signals"][0]["id"]
        cls.campaign = cls.dir / "campaign.json"
        cls.brief = run("--accounts", str(ACCOUNTS), "signal-brief", "--scan", str(cls.scan), "--signal-id", cls.signal,
                        "--brand", str(BRAND), "--out", str(cls.campaign))
        cls.board = cls.dir / "storyboard"
        cls.story = run("media-storyboard", "--campaign", str(cls.campaign), "--out", str(cls.board), "--duration", "30",
                        "--video-targets", "tiktok", "youtube_shorts", "--image-targets", "instagram", "linkedin")

    def test_one_signal_yields_a_placeholder_variant_per_approved_account(self):
        self.assertEqual(self.brief[0], 0, self.brief[2])
        campaign = json.loads(self.campaign.read_text(encoding="utf-8"))
        self.assertEqual({(v["platform"], v["account_id"]) for v in campaign["variants"]},
                         {("bluesky", "my-bluesky"), ("linkedin", "my-linkedin")})
        for variant in campaign["variants"]:
            self.assertIn("{{write:", variant["text"], "no copy may be generated")
        self.assertTrue(self.campaign.with_suffix(".brief.md").is_file())
        self.assertEqual(stat.S_IMODE(os.stat(self.campaign).st_mode), 0o600)

    def test_the_scaffold_cannot_be_staged_until_a_human_writes_it(self):
        code, out, _ = run("campaign-check", "--file", str(self.campaign), "--brand", str(BRAND), "--json")
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["valid"])

    def test_the_same_campaign_yields_a_video_and_image_cut_list(self):
        self.assertEqual(self.story[0], 0, self.story[2])
        board = json.loads((self.board / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(board["status"], "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT")
        self.assertEqual(board["video_targets"], ["tiktok", "youtube_shorts"])
        self.assertEqual(set(board["image_specs"]), {"instagram", "linkedin"})
        scenes = board["scenes"]
        self.assertGreaterEqual(len(scenes), 3)
        self.assertEqual((scenes[0]["start_ms"], scenes[-1]["end_ms"], board["duration_ms"]), (0, 30000, 30000))
        self.assertTrue(all(a["end_ms"] == b["start_ms"] for a, b in zip(scenes, scenes[1:])), "scenes must be contiguous")
        self.assertEqual([scenes[0]["role"], scenes[-1]["role"]], ["hook", "cta"])
        for scene in scenes:
            for field in ("on_screen_text", "voiceover", "visual"):
                self.assertIn("{{write:", scene[field], f"{scene['id']}.{field} must stay a placeholder")
        for name in ("storyboard.md", "media-manifest.json", "subtitles.en.srt"):
            self.assertTrue((self.board / name).is_file(), name)

    def test_nothing_is_generated_and_the_manifest_blocks_until_real_files_exist(self):
        text = (self.board / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("{{write:", text)
        self.assertIn("No copy, alt text, subtitle text", text)
        code, out, _ = run("media-check", str(self.board / "media-manifest.json"), "--json")
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(out)["valid"])

    def test_outputs_are_new_files_and_an_existing_target_is_refused(self):
        code, _, err = run("--accounts", str(ACCOUNTS), "signal-brief", "--scan", str(self.scan), "--signal-id",
                           self.signal, "--brand", str(BRAND), "--out", str(self.campaign))
        self.assertEqual(code, 2, err)
        code, _, err = run("media-storyboard", "--campaign", str(self.campaign), "--out", str(self.board))
        self.assertEqual(code, 2, err)


if __name__ == "__main__":
    unittest.main()
