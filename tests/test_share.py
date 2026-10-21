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
from test_media import png_bytes  # noqa: E402
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
        # One 600-character word cannot be shortened at a word boundary, so Threads still blocks. With
        # --strict that blocks every write (the old all-or-nothing behaviour).
        code, _, errors = self.cli("share", "--platform", "instagram,threads", "--strict",
                                   "--text", "x" * 600, "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("threads", errors)
        self.assertFalse(self.out.exists(), "--strict: one rejected destination must block all writes")

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
        self.assertFalse(package["media"]["validated"], "no manifest was validated; only headers were read")
        self.assertIn("unrecognized file header", "\n".join(package["preflight"]["warnings"]))
        self.assertIn(str(video), (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        self.assertFalse((self.out / "instagram" / "demo.mp4").exists())
        code, _, errors = self.cli("share", "--platform", "instagram",
                                   "--text", "demo", "--attach", str(self.root / "missing.mp4"),
                                   "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("missing", errors.casefold())
        self.assertFalse((self.root / "bad").exists())

    def test_refuses_existing_output_and_unknown_network(self):
        self.out.mkdir()
        code, _, errors = self.cli("share", "--platform", "threads",
                                   "--text", "Draft only", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("exists", errors)
        code, _, errors = self.cli("share", "--platform", "myspace",
                                   "--text", "Should be refused",
                                   "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("unknown network 'myspace'", errors.casefold())
        self.assertFalse((self.root / "bad").exists())
        code, _, errors = self.cli("share", "--platform", "blusky", "--text", "Typo", "--out", str(self.root / "bad"))
        self.assertEqual(code, 2)
        self.assertIn("Unknown network 'blusky'. Did you mean bluesky?", errors)
        code, _, errors = self.cli("share", "--platform", "twiter", "--text", "Typo", "--out", str(self.root / "bad"))
        self.assertIn("Did you mean x?", errors, "an alias suggests the id it stands for")
        self.assertFalse((self.root / "bad").exists())

    def test_api_capable_network_is_a_copy_only_package(self):
        # Owner decision 2026-10-21: Bluesky gets copy-ready text too; sending stays draft -> review -> approve.
        code, _, errors = self.cli("share", "--platform", "bluesky",
                                   "--text", "Copy this yourself", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        package = json.loads((self.out / "bluesky" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertEqual(package["status"], "manual_export_not_published")
        self.assertIn("draft → review", package["notice"])

    def test_default_output_goes_into_a_selected_workspace(self):
        workspace = self.root / "my-workspace"
        workspace.mkdir()
        (workspace / ".reachweaver-workspace").write_text("{}", encoding="utf-8")
        code, output, errors = self.cli(
            "--home", str(workspace), "share", "--platform", "threads",
            "--text", "A caption saved inside my workspace"
        )
        self.assertEqual(code, 0, errors)
        exports = list((workspace / "state").glob("share-*/OPEN-FIRST.md"))
        self.assertEqual(len(exports), 1)
        self.assertIn("my-workspace", output)
        self.assertFalse((self.root / "packages").exists())

    def test_guided_terminal_flow_can_be_cancelled(self):
        # network, caption, attachment (Enter skips), save?
        with patch("sys.stdin", TerminalText("instagram\nA real caption\n\n\nn\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Cancelled", output)
        self.assertFalse(self.out.exists())
        with patch("sys.stdin", TerminalText("instagram\nA real caption\n\n\ny\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertTrue((self.out / "OPEN-FIRST.md").is_file())

    def test_guide_saves_on_enter_because_saving_is_local(self):
        with patch("sys.stdin", TerminalText("instagram\nA real caption\n\n\n\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Save these files? [Y/n]", output)
        self.assertTrue((self.out / "OPEN-FIRST.md").is_file())

    def test_guide_reasks_an_empty_or_unknown_network_with_the_list(self):
        with patch("sys.stdin", TerminalText("\nblusky\nbluesky\nA real caption\n\n\n\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Type at least one network, for example: instagram", output)
        self.assertIn("Unknown network 'blusky'. Did you mean bluesky?", output)
        self.assertNotIn("--guide", errors)
        self.assertTrue((self.out / "bluesky" / "post.txt").is_file())

    def test_guide_takes_an_attachment_and_reasks_a_missing_one(self):
        photo = self.root / "photo one.png"
        photo.write_bytes(png_bytes(1080, 1080))
        answers = f"bluesky\nA real caption\n\nnope.jpg\n'{photo}'\n\n"
        with patch("sys.stdin", TerminalText(answers)):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Attach a photo or video? (path, or Enter to skip)", output)
        self.assertIn("File not found: nope.jpg", output)
        self.assertIn("Media: 1 file(s) checked offline", output)
        self.assertTrue((self.out / "bluesky" / "post.txt").is_file())

    def test_youtube_without_title_names_the_flag(self):
        code, _, errors = self.cli("share", "--platform", "youtube", "--text", "A description",
                                   "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn('YouTube needs a video title: add --title "…"', errors)
        self.assertNotIn("platform_options", errors)
        self.assertFalse(self.out.exists())

    def test_open_first_has_a_labelled_tip_and_plain_checklists(self):
        code, _, errors = self.cli("share", "--platform", "instagram,mastodon", "--text", "Our demo is out",
                                   "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        guide = (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8")
        self.assertIn("Tip (from ReachWeaver's sourced notes; a suggestion, not a promise of reach): Mastodon counts "
                      "a link as 23 characters", guide)
        checklist = (self.out / "instagram" / "CHECKLIST.md").read_text(encoding="utf-8")
        for internal in ("manual_export_not_published", "api_gated", "fail closed", "first-party evidence",
                         "no media manifest supplied"):
            self.assertNotIn(internal, checklist)
        self.assertIn("Status: Not posted yet.", checklist)
        self.assertIn("ReachWeaver cannot post to Instagram; you post it yourself", checklist)
        self.assertIn("Instagram needs a photo or video: attach it in the app", checklist)
        mastodon = (self.out / "mastodon" / "CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("This is a copy-only package", mastodon)
        self.assertNotIn("limit unknown", mastodon.split("## Checklist")[1].split("##")[0])


class ShareOnboardingTests(unittest.TestCase):
    """Partial packages, the text in the terminal, the multi-line guide caption and plain attachment errors."""

    setUp = ShareTests.setUp
    cli = ShareTests.cli

    def test_a_blocked_network_is_skipped_with_its_fix_and_the_others_are_written(self):
        code, out, errors = self.cli("share", "--platform", "instagram,tiktok,youtube",
                                     "--text", "New release is out", "--out", str(self.out))
        self.assertEqual(code, 1, errors)
        self.assertIn('YouTube skipped: add --title "…" and run again with --platform youtube', errors)
        self.assertIn("Partial: skipped youtube", errors)
        self.assertNotIn("BLOCKED", errors)
        self.assertTrue((self.out / "instagram" / "caption.txt").is_file())
        self.assertTrue((self.out / "tiktok" / "caption.txt").is_file())
        self.assertFalse((self.out / "youtube").exists())
        self.assertNotIn("YouTube", (self.out / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        self.assertIn("Prepared 2 private manual packages", out)

    def test_strict_keeps_all_or_nothing(self):
        code, _, errors = self.cli("share", "--platform", "instagram,youtube", "--strict",
                                   "--text", "New release is out", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED. No files were written", errors)
        self.assertFalse(self.out.exists())

    def test_errors_that_belong_to_no_single_network_still_block_everything(self):
        code, _, errors = self.cli("share", "--platform", "instagram,threads", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("Add a caption", errors)
        self.assertFalse(self.out.exists())
        # Every chosen network blocked: nothing is partial about it.
        code, _, errors = self.cli("share", "--platform", "youtube", "--text", "A description", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertFalse(self.out.exists())

    def test_the_final_text_is_shown_once_when_every_network_gets_the_same(self):
        code, out, errors = self.cli("share", "--platform", "instagram,threads,bluesky",
                                     "--text", "Our new release is out https://example.org/notes", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Same text for all 3: it fits every network, so nothing was changed", out)
        self.assertEqual(out.count("\nOur new release is out https://example.org/notes\n"), 1)
        self.assertIn(" - Instagram (photo post): 48 of 2200 characters allowed", out)
        self.assertIn(" - Threads: 48 of 500 characters allowed", out)
        self.assertIn(" - Bluesky: 48 of 300 characters allowed", out)

    def test_a_shortened_text_is_shown_per_network_with_a_pointer_to_the_full_text(self):
        long = " ".join(["This sentence keeps the post long enough."] * 12)
        code, out, errors = self.cli("share", "--platform", "instagram,bluesky", "--text", long,
                                     "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("Your text, ready to copy (exactly what is in each file):", out)
        self.assertIn("---- Bluesky: ", out)
        self.assertIn("SHORTENED to fit", out)
        self.assertIn("… full text in instagram/caption.txt", out)
        self.assertNotIn("Same text", out)
        self.assertLess(len(out.split("Your text, ready to copy")[1].splitlines()), 15)

    def test_guide_caption_takes_several_lines_until_an_empty_line(self):
        with patch("sys.stdin", TerminalText("bluesky\nFirst line\nSecond line\n\n\n\n")):
            code, output, errors = self.cli("share", "--guide", "--out", str(self.out))
        self.assertEqual(code, 0, errors)
        self.assertIn("press Enter on an empty line to finish (or Ctrl-D)", output)
        self.assertEqual((self.out / "bluesky" / "post.txt").read_text(encoding="utf-8"), "First line\nSecond line")

    def test_guide_caption_ends_at_ctrl_d_and_an_empty_first_line_asks_again(self):
        from reachweaver import share
        with patch("builtins.input", side_effect=["", "Only line", EOFError()]), \
                contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(share._ask_caption(), "Only line")
        self.assertIn("cannot be empty", shown.getvalue())
        with patch("builtins.input", side_effect=EOFError()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(ValidationError):
                share._ask_caption()

    def test_a_missing_attachment_says_where_it_looked_and_what_to_do(self):
        code, _, errors = self.cli("share", "--platform", "instagram", "--text", "hi",
                                   "--attach", str(self.root / "nothere.mp4"), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn(f"Attachment not found: {self.root / 'nothere.mp4'} (looked in {self.root}). "
                      "Check the name or leave it out.", errors)
        code, _, errors = self.cli("share", "--platform", "instagram", "--text", "hi",
                                   "--attach", str(self.root), "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("is a folder, not a file", errors)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
