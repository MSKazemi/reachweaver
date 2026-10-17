"""Share command is a safe, one-step offline handoff, never a publishing shortcut."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.manual_export import MANUAL_PLATFORMS
from reachweaver.manual_package import generic_platforms
from reachweaver.share import SEND_PATH, destinations, make_packages
from reachweaver.policy import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_no_network import NetworkGuard  # noqa: E402


class TerminalText(io.StringIO):
    def isatty(self):
        return True


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "packages"
        self.db = self.root / "absent.sqlite"
        self.missing_accounts = self.root / "no-accounts.json"

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(["--db", str(self.db), "--accounts", str(self.missing_accounts), *args])
            self.assertEqual(guard.attempts, [])
        self.assertFalse(self.db.exists(), "Share must not create a database")
        return result, stdout.getvalue(), stderr.getvalue()

    def test_zero_extra_requirements_and_common_choices(self):
        self.assertIsNone(SEND_PATH)
        code, output, errors = self.cli("share", "--list")
        self.assertEqual(code, 0, errors)
        self.assertIn("instagram", output)
        self.assertIn("facebook", output)
        self.assertIn("offline", output.casefold())
        self.assertTrue(set(MANUAL_PLATFORMS).issubset(set(generic_platforms()) | set(MANUAL_PLATFORMS)))

    def test_prepare_two_destinations_from_plain_text(self):
        code, output, errors = self.cli("share", "--platform", "instagram,threads",
                                        "--text", "Our useful demo #makers", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("NOT PUBLISHED", output)
        self.assertEqual((self.out / "instagram" / "caption.txt").read_text(encoding="utf-8"),
                         "Our useful demo #makers")
        self.assertEqual((self.out / "threads" / "caption.txt").read_text(encoding="utf-8"),
                         "Our useful demo #makers")
        self.assertIn("not logged in", (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        for platform in ("instagram", "threads"):
            package = json.loads((self.out / platform / "manual-export.json").read_text(encoding="utf-8"))
            self.assertEqual(package["status"], "manual_export_not_published")
            self.assertEqual(package["preflight"]["errors"], [])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.out / "OPEN-FIRST.md").stat().st_mode), 0o600)

    def test_generic_network_and_alias(self):
        self.assertEqual(destinations(["Twitter", "dev.to"]), ["x", "dev"])
        code, output, errors = self.cli("share", "--platform", "Twitter",
                                        "--text", "A new useful release https://example.org",
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertTrue((self.out / "x" / "post.txt").exists())
        self.assertFalse((self.out / "x" / "caption.txt").exists())

    def test_facebook_is_explicitly_pages_only(self):
        code, output, errors = self.cli("share", "--platform", "facebook pages",
                                        "--text", "An update for our community",
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        package = json.loads((self.out / "facebook" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertEqual(package["account"]["target_type"], "page")

    def test_youtube_requires_title_before_any_files(self):
        code, _, errors = self.cli("share", "--platform", "youtube",
                                   "--text", "Video description", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("title", errors.casefold())
        self.assertFalse(self.out.exists())
        code, _, errors = self.cli("share", "--platform", "youtube",
                                   "--title", "My demo", "--text", "Video description",
                                   "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertEqual((self.out / "youtube" / "title.txt").read_text(encoding="utf-8"), "My demo")

    def test_preview_and_blocking_error_never_create_output(self):
        code, out, errors = self.cli("share", "--platform", "threads",
                                     "--text", "Preview content", "--preview",
                                     "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("PREVIEW ONLY", out)
        self.assertFalse(self.out.exists())
        code, _, errors = self.cli("share", "--platform", "instagram,threads",
                                   "--text", "x" * 600, "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("threads", errors)
        self.assertFalse(self.out.exists(), "One rejected destination must block all writes")

    def test_human_only_notes_and_refusal_of_ready_post(self):
        code, output, errors = self.cli("share", "--platform", "reddit",
                                        "--note", "Check subreddit rules",
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertTrue((self.out / "reddit" / "NOTES.md").exists())
        self.assertFalse((self.out / "reddit" / "post.txt").exists())
        code, _, errors = self.cli("share", "--platform", "reddit", "--text", "Promotional content",
                                   "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("human-only", errors)
        self.assertFalse((self.root / "bad").exists())

    def test_mixed_human_and_nonhuman_requires_separate_notes(self):
        code, output, errors = self.cli("share", "--platform", "threads,reddit",
                                        "--text", "A useful tutorial",
                                        "--note", "First read the rules",
                                        "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertFalse((self.out / "reddit" / "post.txt").exists())
        self.assertEqual((self.out / "threads" / "caption.txt").read_text(encoding="utf-8"),
                         "A useful tutorial")

    def test_attachment_reference_only_and_missing_file_blocked(self):
        video = self.root / "demo.mp4"
        video.write_bytes(b"sample-not-a-valid-video")
        code, _, errors = self.cli("share", "--platform", "instagram",
                                   "--text", "Watch our demo", "--attach", str(video),
                                   "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        package = json.loads((self.out / "instagram" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertEqual(package["content"]["format"], "video")
        self.assertFalse(package["media"]["validated"])
        self.assertIn(str(video), (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        self.assertFalse((self.out / "instagram" / "demo.mp4").exists())
        code, _, errors = self.cli("share", "--platform", "instagram",
                                   "--text", "demo", "--attach", str(self.root / "missing.mp4"),
                                   "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("missing", errors.casefold())
        self.assertFalse((self.root / "bad").exists())

    def test_refuses_existing_output_and_unapproved_live_network(self):
        self.out.mkdir()
        code, _, errors = self.cli("share", "--platform", "threads",
                                   "--text", "Draft only", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("exists", errors)
        code, _, errors = self.cli("share", "--platform", "bluesky",
                                   "--text", "Should be refused",
                                   "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("approve", errors.casefold())
        self.assertFalse((self.root / "bad").exists())

    def test_guided_terminal_flow_can_be_cancelled(self):
        with patch("sys.stdin", TerminalText("instagram\nA real caption\nn\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Cancelled", output)
        self.assertFalse(self.out.exists())
        with patch("sys.stdin", TerminalText("instagram\nA real caption\ny\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertTrue((self.out / "OPEN-FIRST.md").is_file())


if __name__ == "__main__":
    unittest.main()
