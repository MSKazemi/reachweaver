"""Bluesky draft -> preview -> dry run -> approval -> mocked live send -> report, end to end through the CLI (issue #42).

Offline: the provider is mocked at ``publishers.request_json``; no network, no credential beyond a fake env value.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import publishers
from reachweaver.cli import main
from reachweaver.richtext import deterministic_tid
from reachweaver.storage import connect, get_post

CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"
ENV = {"REACHWEAVER_LC_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_LC_BSKY_APP_PASSWORD": "mock-app-pw",
       "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
TEXT = "Shipped a release note with proof: https://example.com/notes #research"


def session():
    return {"did": "did:plc:owner123", "handle": "owner.bsky.social", "accessJwt": "mock-access"}


class BlueskyLifecycleTests(unittest.TestCase):
    def setUp(self):
        publishers.reset_write_marker()
        self.addCleanup(publishers.reset_write_marker)
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.db = str(Path(self.work.name) / "state.sqlite")
        self.config = Path(self.work.name) / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [{"id": "lc-bsky", "platform": "bluesky",
                                                         "env_prefix": "REACHWEAVER_LC_BSKY",
                                                         "handle": "owner.bsky.social"}]}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def status(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)["status"]

    def test_the_whole_lifecycle_with_a_mocked_provider(self):
        code, out, _ = self.cli("draft", "--account", "lc-bsky", "--text", TEXT)
        self.assertEqual(code, 0, out)
        pid = out.split()[-1] if len(out.split()[-1]) == 16 else next(w for w in out.split() if len(w) == 16)
        self.assertEqual(self.status(pid), "draft")

        # Preview: offline, shows what would be sent (facets, grapheme count, record key, service).
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("show", pid)
        network.assert_not_called()
        preview = json.loads(out)["platform_preview"]
        self.assertEqual(preview["grapheme_limit"], 300)
        self.assertEqual([f["type"] for f in preview["facets"]], ["link", "tag"])
        self.assertEqual(preview["handle"], "owner.bsky.social")

        # An unapproved draft is never sent, live or not.
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers.request_json") as request:
            self.cli("publish", "--live", "--post-id", pid)
        request.assert_not_called()
        self.assertEqual(self.status(pid), "draft")

        # Approval, then the default dry run: still no request, and the post stays approved.
        code, out, _ = self.cli("approve", pid, "--approver", "owner")
        self.assertEqual(code, 0, out)
        with patch("reachweaver.publishers.request_json") as request, patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--post-id", pid)
        request.assert_not_called()
        network.assert_not_called()
        self.assertEqual(self.status(pid), "approved")

        # Live send with a mocked provider: identity session, publish session, createRecord.
        with connect(self.db) as db:
            created = get_post(db, pid)["created_at"]
        rkey = deterministic_tid(pid, created)
        receipt = {"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}", "cid": CID}
        with patch.dict(os.environ, ENV), \
                patch("reachweaver.publishers.request_json", side_effect=[session(), session(), receipt]) as request:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 0, out)
        self.assertIn("PUBLISHED", out)
        self.assertEqual(request.call_count, 3)
        with connect(self.db) as db:
            row = get_post(db, pid)
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["remote_id"], receipt["uri"])
        self.assertTrue(row["remote_url"].startswith("https://bsky.app/profile/"))
        self.assertTrue(row["remote_url"].endswith(rkey))

        # The same post is never sent twice.
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers.request_json") as request:
            self.cli("publish", "--live", "--post-id", pid)
        request.assert_not_called()

        # Report: the published post is counted, and metrics nobody imported stay unknown, never zero.
        code, out, _ = self.cli("report")
        self.assertEqual(code, 0, out)
        bluesky = json.loads(out)["platforms"]["bluesky"]
        self.assertEqual(bluesky["published"], 1)
        self.assertEqual(bluesky["metric_coverage"], 0)
        for metric in ("views", "impressions", "likes", "replies", "reposts", "clicks"):
            self.assertIsNone(bluesky[metric], metric)  # unknown, never a fabricated zero
        self.assertEqual(bluesky["metrics"]["views"]["coverage"], "0/1")
        self.assertNotIn("mock-app-pw", out)
        self.assertNotIn("mock-access", out)


if __name__ == "__main__":
    unittest.main()
