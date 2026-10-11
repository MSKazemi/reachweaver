"""YouTube analytics-sampling report (issue #22 follow-up). Offline: stored snapshots only, sockets blocked."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import youtube_analytics
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect, connect_readonly, finish_send, store_metrics

OBS = "2026-10-05T09:00:00+00:00"
CHANNEL = "UC" + "a" * 22


def _no_network(*_a, **_k):
    raise AssertionError("network access attempted")


class SamplingReportTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection"):
            patcher = patch(target, _no_network)
            patcher.start()
            self.addCleanup(patcher.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "outbox.sqlite")
        with connect(self.db):
            pass

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, *argv])
        return code, out.getvalue(), err.getvalue()

    def video(self, con, vid, *, account="yt-me", receipt=True):
        pid = add_post(con, account, "youtube", f"description {vid}")
        con.execute("UPDATE posts SET status='sending' WHERE id=?", (pid,))
        extra = ({"channel_id": CHANNEL, "privacy_status": "private", "upload_status": "uploaded",
                  "sha256": "0123456789abcdef"} if receipt else None)
        finish_send(con, pid, vid, f"https://www.youtube.com/watch?v={vid}", extra)
        return pid

    def test_no_snapshot_is_unknown_never_zero(self):
        with connect(self.db) as con:
            self.video(con, "vid00000001")
            report = youtube_analytics.sampling_report(con)
        (video,) = report["videos"]
        self.assertEqual(video["snapshots"], 0)
        self.assertIsNone(video["latest"])
        self.assertTrue(all(m == {"label": "unknown"} for m in video["metrics"].values()))
        self.assertIsNone(report["totals"]["views"]["value"])
        self.assertEqual(report["totals"]["views"]["coverage"], "0/1")

    def test_receipt_fields_match_the_video_receipt(self):
        with connect(self.db) as con:
            self.video(con, "vid00000001")
            self.video(con, "vid00000002", receipt=False)
            videos = {v["video_id"]: v for v in youtube_analytics.sampling_report(con)["videos"]}
        first = videos["vid00000001"]
        self.assertEqual((first["channel_id"], first["privacy_status"], first["upload_status"]),
                         (CHANNEL, "private", "uploaded"))
        self.assertEqual(first["watch_url"], "https://www.youtube.com/watch?v=vid00000001")
        second = videos["vid00000002"]  # settled without receipt fields: null, not guessed
        self.assertEqual([second[k] for k in ("channel_id", "privacy_status", "upload_status")], [None] * 3)

    def test_labels_exact_sampled_status_and_unknown(self):
        with connect(self.db) as con:
            pid = self.video(con, "vid00000001")
            store_metrics(con, pid, OBS, "native-export", views=120, likes="suppressed", impressions="estimated:900",
                          clicks="delayed", signups="not_available")
            video = youtube_analytics.sampling_report(con)["videos"][0]
        m = video["metrics"]
        self.assertEqual(m["views"], {"label": "exact", "value": 120})
        self.assertEqual(m["impressions"], {"label": "sampled", "value": 900})
        self.assertEqual(m["likes"], {"label": "suppressed"})
        self.assertEqual(m["clicks"], {"label": "delayed"})
        self.assertEqual(m["signups"], {"label": "not_available"})
        self.assertEqual(m["replies"], {"label": "unknown"})
        self.assertEqual(video["latest"]["source"], "native-export")
        self.assertFalse(video["latest"]["window"]["known"])

    def test_totals_sum_exact_only_and_keep_samples_apart(self):
        with connect(self.db) as con:
            a, b, c = (self.video(con, f"vid0000000{i}") for i in (1, 2, 3))
            store_metrics(con, a, OBS, "s", views=100, window_start="2026-10-01T00:00:00+00:00",
                          window_end="2026-10-05T00:00:00+00:00")
            store_metrics(con, b, OBS, "s", views="estimated:5000")
            store_metrics(con, c, OBS, "s", views=40)
            views = youtube_analytics.sampling_report(con)["totals"]["views"]
        self.assertEqual((views["value"], views["exact"], views["total"], views["coverage"]), (140, 2, 3, "2/3"))
        self.assertEqual(views["exact_without_window"], 1)
        self.assertEqual(views["labels"]["sampled"], 1)
        self.assertEqual(views["sampled"]["sum"], 5000)  # shown apart, not in value
        self.assertIn("never summed", views["sampled"]["note"])

    def test_uses_latest_snapshot_only(self):
        with connect(self.db) as con:
            pid = self.video(con, "vid00000001")
            store_metrics(con, pid, "2026-10-01T09:00:00+00:00", "s", views=10)
            store_metrics(con, pid, "2026-10-05T09:00:00+00:00", "s", views=30)
            video = youtube_analytics.sampling_report(con)["videos"][0]
        self.assertEqual((video["snapshots"], video["metrics"]["views"]["value"]), (2, 30))

    def test_filters_and_non_youtube_refusal(self):
        with connect(self.db) as con:
            keep = self.video(con, "vid00000001")
            self.video(con, "vid00000002", account="yt-other")
            draft = add_post(con, "yt-me", "youtube", "never published")
            other = add_post(con, "bsky", "bluesky", "hello")
            by_account = youtube_analytics.sampling_report(con, account_id="yt-me")
            self.assertEqual([v["post_id"] for v in by_account["videos"]], [keep])
            self.assertEqual(youtube_analytics.sampling_report(con, post_id=draft)["videos"], [])
            self.assertEqual(len(youtube_analytics.sampling_report(con)["videos"]), 2)
            with self.assertRaisesRegex(ValidationError, "only applies to YouTube"):
                youtube_analytics.sampling_report(con, post_id=other)
            with self.assertRaisesRegex(ValidationError, "does not belong"):
                youtube_analytics.sampling_report(con, account_id="yt-other", post_id=keep)

    def test_cli_is_read_only_and_json(self):
        with connect(self.db) as con:
            pid = self.video(con, "vid00000001")
            store_metrics(con, pid, OBS, "s", views=7)
        before = Path(self.db).read_bytes()
        code, out, err = self.cli("youtube-analytics")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["totals"]["views"]["value"], 7)
        self.assertEqual(data["videos"][0]["video_id"], "vid00000001")
        self.assertEqual(Path(self.db).read_bytes(), before)
        with connect_readonly(self.db) as con:  # the read-only connection accepts the report too
            self.assertEqual(len(youtube_analytics.sampling_report(con)["videos"]), 1)

    def test_cli_missing_database_fails_without_creating_it(self):
        missing = Path(self.db).parent / "absent.sqlite"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(missing), "youtube-analytics"])
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())

    def test_socket_guard_is_active(self):
        with self.assertRaises(AssertionError):
            socket.create_connection(("127.0.0.1", 9))


if __name__ == "__main__":
    unittest.main()
