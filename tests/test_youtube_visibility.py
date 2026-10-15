"""A finished YouTube upload is not a public video, and the receipt feeds the analytics report (public issue #52).

Mocked network only; reuses the harness of ``tests/test_youtube.py``.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from reachweaver import youtube
from reachweaver.storage import connect
from reachweaver.youtube_analytics import sampling_report
from tests.test_youtube import CHANNEL, VIDEO_ID, FakeYouTube, FlippedRegistry


def receipt(privacy):
    return {"id": VIDEO_ID, "snippet": {"channelId": CHANNEL, "title": "Demo"},
            "status": {"privacyStatus": privacy, "uploadStatus": "uploaded", "selfDeclaredMadeForKids": False}}


class VisibilityNoteTests(unittest.TestCase):
    def test_note_is_none_without_a_stored_spec_or_with_garbage(self):
        for value in (None, "", "not json", "{}", '{"privacy_status": "secret"}'):
            with self.subTest(value=value):
                self.assertIsNone(youtube.visibility_note(value))


class PublishOutputTests(FlippedRegistry):
    def publish(self, privacy):
        pid, path = self.draft(privacy=privacy)
        fake = FakeYouTube(size=path.stat().st_size, final=receipt(privacy))
        code, out, err = self.run_live(pid, fake)
        self.assertEqual(code, 0, out + err)
        return pid, out

    def test_private_upload_says_it_is_not_public(self):
        pid, out = self.publish("private")
        self.assertIn(f"PUBLISHED {pid}: https://www.youtube.com/watch?v={VIDEO_ID}", out)
        line = next(line for line in out.splitlines() if line.startswith(f"NOTE {pid}:"))
        self.assertIn("privacy_status=private", line)
        self.assertIn("NOT public", line)
        self.assertIn("YouTube Studio", line)

    def test_unlisted_upload_says_it_is_not_public(self):
        pid, out = self.publish("unlisted")
        line = next(line for line in out.splitlines() if line.startswith(f"NOTE {pid}:"))
        self.assertIn("privacy_status=unlisted", line)
        self.assertIn("NOT public", line)

    def test_an_approved_public_upload_is_reported_as_public_without_the_warning(self):
        pid, out = self.publish("public")
        line = next(line for line in out.splitlines() if line.startswith(f"NOTE {pid}:"))
        self.assertIn("privacy_status=public, as approved", line)
        self.assertNotIn("NOT public", line)

    def test_the_note_is_added_after_the_published_line_and_changes_no_state(self):
        pid, out = self.publish("private")
        lines = out.splitlines()
        self.assertLess(next(i for i, ln in enumerate(lines) if ln.startswith("PUBLISHED")),
                        next(i for i, ln in enumerate(lines) if ln.startswith("NOTE")))
        row = self.post(pid)
        self.assertEqual((row["status"], row["remote_id"]), ("published", VIDEO_ID))


class ReceiptToAnalyticsTests(FlippedRegistry):
    def test_an_adapter_receipt_flows_into_the_sampling_report_consistently(self):
        pid, path = self.draft(privacy="private")
        fake = FakeYouTube(size=path.stat().st_size, final=receipt("private"))
        code, out, err = self.run_live(pid, fake)
        self.assertEqual(code, 0, out + err)
        with connect(self.db) as con:
            report = sampling_report(con)
        (video,) = report["videos"]
        self.assertEqual((video["post_id"], video["video_id"], video["watch_url"]),
                         (pid, VIDEO_ID, f"https://www.youtube.com/watch?v={VIDEO_ID}"))
        self.assertEqual((video["channel_id"], video["privacy_status"], video["upload_status"]),
                         (CHANNEL, "private", "uploaded"))
        self.assertEqual(video["snapshots"], 0)
        for name, metric in video["metrics"].items():
            self.assertEqual(metric, {"label": "unknown"}, f"{name} must be unknown, never zero")

    def test_the_report_and_the_publish_output_agree_on_the_privacy(self):
        pid, path = self.draft(privacy="unlisted")
        fake = FakeYouTube(size=path.stat().st_size, final=receipt("unlisted"))
        _, out, _ = self.run_live(pid, fake)
        with connect(self.db) as con:
            (video,) = sampling_report(con, post_id=pid)["videos"]
        self.assertIn("privacy_status=unlisted", out)
        self.assertEqual(video["privacy_status"], "unlisted")


class StudioFallbackDocTests(unittest.TestCase):
    """Issue #22: docs/YOUTUBE.md keeps its documented manual YouTube Studio fallback."""

    @classmethod
    def setUpClass(cls):
        text = (Path(__file__).resolve().parents[1] / "docs" / "YOUTUBE.md").read_text(encoding="utf-8")
        parts = text.split("\n## Manual fallback: upload in YouTube Studio\n", 1)
        cls.section = parts[1].split("\n## ", 1)[0] if len(parts) == 2 else ""

    def test_the_section_exists_and_reachweaver_never_uploads_in_it(self):
        self.assertTrue(self.section, "docs/YOUTUBE.md lost its 'Manual fallback: upload in YouTube Studio' section")
        self.assertIn("ReachWeaver never uploads in this path; **you** upload in your own browser session.",
                      self.section)
        self.assertIn("reachweaver manual-export --platform youtube", self.section)

    def test_the_steps_stay_private_first_with_a_human_audience_answer(self):
        steps = re.findall(r"^(\d)\. ", self.section, flags=re.MULTILINE)
        self.assertEqual(steps, ["1", "2", "3", "4", "5", "6"])
        self.assertIn("**Answer the audience (\"made for kids\") question yourself.**", self.section)
        self.assertIn("The safe first choice is `Private`", self.section)

    def test_the_result_is_reconciled_and_a_half_processed_copy_is_checked_first(self):
        self.assertIn("`reachweaver reconcile` with `--published --remote-id VIDEO_ID`", self.section)
        self.assertIn("`--not-published`", self.section)
        self.assertIn("Never claim a view or reach number that Studio did not show you.", self.section)
        self.assertIn("first check Studio for a half-processed copy to avoid a duplicate", self.section)


if __name__ == "__main__":
    unittest.main()
