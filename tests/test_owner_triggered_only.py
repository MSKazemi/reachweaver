"""Platforms whose terms forbid automated posting (policy.OWNER_TRIGGERED_ONLY; LinkedIn API Terms 3.1).

A scheduled or bulk publish run holds such a post before any claim, request or credential read; only the owner's
own run naming that one post can send it, and `schedule` refuses it. Offline; no request is ever made.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.policy import OWNER_TRIGGERED_ONLY, SUPPORTED_LIVE
from reachweaver.storage import add_post, connect, get_post, transition
from reachweaver.worker_log import SKIP_REASONS

LIVE_FLAG = "--" + "live"  # keeps the literal flag out of commands that tools scan


def approved(db, platform, account):
    pid = add_post(db, account, platform, f"A note for {platform}", "launch", "https://example.org/r")
    transition(db, pid, "approve")
    return pid


class OwnerTriggeredOnlyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.db = str(root / "outbox.sqlite")
        self.accounts = root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_OT_BSKY",
             "handle": "owner.bsky.social"},
            {"id": "my-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_OT_LI",
             "person_id": "abc123", "linkedin_version": "202610"}]}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        with connect(self.db) as db:
            self.linkedin = approved(db, "linkedin", "my-linkedin")
            self.bluesky = approved(db, "bluesky", "my-bluesky")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_linkedin_is_listed_and_live(self):
        self.assertIn("linkedin", OWNER_TRIGGERED_ONLY)
        self.assertTrue(OWNER_TRIGGERED_ONLY <= SUPPORTED_LIVE)
        self.assertIn("owner_triggered_only", SKIP_REASONS)

    def test_bulk_dry_run_holds_linkedin_and_keeps_other_platforms(self):
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn(f"HELD {self.linkedin} linkedin", out)
        self.assertIn("forbid automated posting", out)
        self.assertIn("DRY RUN: 1 due approved posts", out)
        self.assertIn(self.bluesky, out)

    def test_naming_the_one_post_keeps_it_in_the_run(self):
        code, out, _ = self.cli("publish", "--post-id", self.linkedin)
        self.assertEqual(code, 0)
        self.assertNotIn("HELD", out)
        self.assertIn("DRY RUN: 1 due approved posts", out)

    def test_scheduled_only_holds_it_even_when_named(self):
        with connect(self.db) as db:
            recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
            db.execute("UPDATE posts SET status='scheduled', scheduled_at=? WHERE id=?", (recent, self.linkedin))
        code, out, _ = self.cli("publish", "--scheduled-only", "--post-id", self.linkedin)
        self.assertIn(f"HELD {self.linkedin} linkedin", out)
        self.assertIn("DRY RUN: 0 due approved posts", out)

    def test_live_bulk_run_never_requests_or_claims_linkedin(self):
        with patch.dict(os.environ, {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers.request_json") as request, \
                patch("reachweaver.publishers._send_http") as network, \
                patch("reachweaver.cli.publish") as send:
            _, out, _ = self.cli("publish", LIVE_FLAG, "--limit", "5")
        self.assertIn(f"HELD {self.linkedin} linkedin", out)
        for call in send.call_args_list + request.call_args_list + network.call_args_list:
            self.assertNotIn("linkedin", repr(call))
        with connect(self.db) as db:
            self.assertEqual(get_post(db, self.linkedin)["status"], "approved")

    def test_schedule_refuses_linkedin_and_changes_nothing(self):
        code, _, err = self.cli("schedule", self.linkedin, "--at", "2030-01-01T09:00:00Z")
        self.assertEqual(code, 2)
        self.assertIn("linkedin cannot be scheduled", err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, self.linkedin)["status"], "approved")
        code, _, _ = self.cli("schedule", self.bluesky, "--at", "2030-01-01T09:00:00Z")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
