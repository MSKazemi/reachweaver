"""Terminal review sheet and length facts: derived from the show view only. Offline."""
from __future__ import annotations

import io
import re
import unittest

from reachweaver import show_view, term

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Tty(io.StringIO):
    def isatty(self):
        return True


def view(**over):
    base = {"id": "abc123", "account_id": "my-bsky", "platform": "bluesky", "status": "approved",
            "content": "Hello world " * 3, "campaign": "launch", "source_url": "https://example.org/x",
            "scheduled_at": None, "approved_at": "2026-10-16T10:00:00+00:00",
            "approval": {"binding": "bound", "approved_by": "owner", "payload_sha256": "ab" * 32},
            "platform_preview": {"graphemes": 36, "grapheme_limit": 300, "handle": "me.bsky.social"}}
    base.update(over)
    return base


class LengthTests(unittest.TestCase):
    def test_bluesky_mastodon_linkedin_use_their_own_counts(self):
        self.assertEqual(show_view.length_of(view())["state"], "ok")
        mast = view(platform="mastodon", platform_preview={"counted_length": 480, "text_limit": 500})
        self.assertEqual(show_view.length_of(mast), {"used": 480, "limit": 500, "unit": "characters",
                                                     "ratio": 0.96, "state": "tight"})
        li = view(platform="linkedin", platform_preview={"counted_length": 3001, "text_limit": 3000})
        self.assertEqual(show_view.length_of(li)["state"], "over")

    def test_unknown_platform_or_missing_preview_has_no_meter_never_a_guess(self):
        self.assertIsNone(show_view.length_of(view(platform="threads")))
        self.assertIsNone(show_view.length_of(view(platform_preview=None)))
        self.assertIsNone(show_view.length_of(view(platform_preview={"graphemes": 4})))


class SheetTests(unittest.TestCase):
    def sheet(self, v=None, steps=None, stream=None):
        return show_view.render_sheet(v or view(), steps if steps is not None else [
            {"label": "Preview", "command": "reachweaver publish --post-id abc123"}], stream or io.StringIO(), columns=80)

    def test_sheet_has_exact_text_length_approval_and_next_commands(self):
        text = self.sheet()
        for needle in ("POST abc123", "[approved]", "my-bsky on bluesky", "campaign launch", "EXACT TEXT",
                       "Hello world Hello world Hello world", "36/300 graphemes", "Approved by owner",
                       "hash abababababab", "Posts as: me.bsky.social", "reachweaver publish --post-id abc123",
                       "reachweaver --plain show abc123"):
            self.assertIn(needle, text)
        self.assertNotIn("\x1b", text)

    def test_every_status_has_a_plain_sentence(self):
        for status in ("draft", "approved", "scheduled", "sending", "needs_review", "published", "cancelled"):
            v = view(status=status)
            if status in ("approved", "scheduled"):
                v["approval"]["binding"] = "bound"
            self.assertTrue(show_view.approval_sentence(v), status)
        self.assertIn("approve it again", show_view.approval_sentence(
            view(approval={"binding": "payload_changed"})))
        self.assertIn("Not approved yet", show_view.approval_sentence(view(status="draft")))

    def test_over_the_limit_is_called_out(self):
        text = self.sheet(view(platform_preview={"graphemes": 301, "grapheme_limit": 300}))
        self.assertIn("OVER THE LIMIT", text)

    def test_colour_only_on_a_terminal_and_never_changes_the_text(self):
        plain = self.sheet()
        coloured = self.sheet(stream=Tty())
        self.assertIn("\x1b[", coloured)
        self.assertEqual(ANSI.sub("", coloured), plain)

    def test_long_text_wraps_inside_the_box(self):
        text = self.sheet(view(content="word " * 80))
        self.assertTrue(all(len(line) <= 80 for line in text.splitlines()), max(map(len, text.splitlines())))

    def test_repeat_check_findings_are_listed_without_stray_control_characters(self):
        v = view(repeat_check={"blocking": 1, "warnings": 0, "findings": [{"message": "same wording as post ab12\x1b[31m"}]})
        text = self.sheet(v)
        self.assertIn("Repeat check: 1 blocking, 0 warning(s)", text)
        self.assertIn("same wording as post ab12", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("Repeat check", self.sheet(view(repeat_check={"blocking": 0, "warnings": 0, "findings": []})))

    def test_hostile_text_is_just_text(self):
        text = self.sheet(view(content="<script>x</script> \x1b[31m"))
        self.assertIn("<script>x</script>", text)

    def test_attachments_are_one_line_each(self):
        v = view(media={"count": 2, "attachments": []}, reply={"kind": "reply", "invalid": "bad target"})
        text = self.sheet(v)
        self.assertIn("Images: 2 attached", text)
        self.assertIn("Reply: problem: bad target", text)


class NoEnvironmentTests(unittest.TestCase):
    def test_module_reads_no_environment(self):
        from pathlib import Path
        self.assertNotRegex(Path(show_view.__file__).read_text(encoding="utf-8"), r"os\.environ|getenv|\benv\.get")
        self.assertTrue(hasattr(term, "style"))


if __name__ == "__main__":
    unittest.main()
