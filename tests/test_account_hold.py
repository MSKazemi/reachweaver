"""Account hold after an unresolved send (public issue #41): no further claim on an account whose last send is
unknown, until a person reconciles it. Offline; the provider is mocked.

Draft for owner security review (docs/SECURITY-REVIEW-PROPOSAL-41-account-hold.md).
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

from reachweaver import publishers
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.publishers import PublishError
from reachweaver.storage import (
    HOLD_REFUSAL, account_send_hold, add_post, claim_sending, connect, get_post, finish_send, reconcile,
    transition, uncertain_send,
)
from reachweaver.worker_log import SKIP_REASONS

ENV = {"REACHWEAVER_HD_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_HD_BSKY_APP_PASSWORD": "fake-value",
       "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"


def session():
    return {"did": "did:plc:owner123", "handle": "owner.bsky.social", "accessJwt": "mock-access"}


def approved(db, text, account="my-bluesky", platform="bluesky"):
    pid = add_post(db, account, platform, text, "launch", "https://example.org/r")
    transition(db, pid, "approve")
    return pid


def make_unresolved(db, text="first note", account="my-bluesky"):
    pid = approved(db, text, account)
    assert claim_sending(db, pid)
    uncertain_send(db, pid, "network_or_timeout_uncertain", send_class="ambiguous")
    return pid


class AccountHoldStorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "outbox.sqlite")

    def test_a_needs_review_post_holds_every_later_claim_on_that_account(self):
        with connect(self.db) as db:
            first = make_unresolved(db)
            second = approved(db, "second note")
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, second)
            message = str(caught.exception)
            self.assertIn(HOLD_REFUSAL, message)
            self.assertIn(first, message)
            self.assertIn("reachweaver reconcile", message)
            row = get_post(db, second)
            self.assertEqual(row["status"], "approved")
            claimed = db.execute("SELECT count(*) FROM event_log WHERE action='send_claimed' AND post_id=?",
                                 (second,)).fetchone()[0]
            self.assertEqual(claimed, 0)

    def test_the_hold_is_per_account(self):
        with connect(self.db) as db:
            make_unresolved(db, account="my-bluesky")
            other = approved(db, "other account", account="my-mastodon", platform="mastodon")
            self.assertTrue(claim_sending(db, other))

    def test_reconciling_either_way_lifts_the_hold(self):
        for published in (True, False):
            with self.subTest(published=published), tempfile.TemporaryDirectory() as tmp:
                with connect(str(Path(tmp) / "o.sqlite")) as db:
                    first = make_unresolved(db)
                    second = approved(db, "second note")
                    with self.assertRaises(ValidationError):
                        claim_sending(db, second)
                    if published:
                        reconcile(db, first, True, "at://did:plc:x/app.bsky.feed.post/abc",
                                  "https://bsky.app/profile/x/post/abc")
                    else:
                        reconcile(db, first, False)
                    self.assertIsNone(account_send_hold(db, "my-bluesky", second))
                    self.assertTrue(claim_sending(db, second))

    def test_only_unresolved_states_hold(self):
        with connect(self.db) as db:
            published = approved(db, "published")
            self.assertTrue(claim_sending(db, published))
            finish_send(db, published, "at://did:plc:x/app.bsky.feed.post/abc", "https://bsky.app/profile/x/post/abc")
            cancelled = approved(db, "cancelled")
            transition(db, cancelled, "cancel")
            approved(db, "still approved")
            add_post(db, "my-bluesky", "bluesky", "a draft", "launch", "https://example.org/r")
            self.assertIsNone(account_send_hold(db, "my-bluesky"))

    def test_a_live_send_does_not_hold_but_a_stale_one_does(self):
        with connect(self.db) as db:
            first = approved(db, "in flight")
            started = datetime.now(timezone.utc)
            self.assertTrue(claim_sending(db, first, started.isoformat(timespec="seconds")))
            fresh = (started + timedelta(minutes=5)).isoformat(timespec="seconds")
            late = (started + timedelta(minutes=16)).isoformat(timespec="seconds")
            self.assertIsNone(account_send_hold(db, "my-bluesky", None, fresh))
            hold = account_send_hold(db, "my-bluesky", None, late)
            self.assertEqual((hold["post_id"], hold["status"]), (first, "sending"))
            second = approved(db, "second")
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, second, late)
            self.assertIn("--not-published --force", str(caught.exception))

    def test_the_hold_is_derived_so_nothing_is_stored_to_forget_or_edit(self):
        with connect(self.db) as db:
            make_unresolved(db)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse([t for t in tables if "hold" in t.lower()])
            settings = {r[0] for r in db.execute("SELECT name FROM settings")}
            self.assertFalse([s for s in settings if "hold" in s.lower()])

    def test_a_refused_claim_uses_no_budget_and_the_hold_wins_over_nothing_else_being_written(self):
        with connect(self.db) as db:
            make_unresolved(db)
            second = approved(db, "second note")
            before = db.execute("SELECT count(*) FROM event_log").fetchone()[0]
            with self.assertRaises(ValidationError):
                claim_sending(db, second, max_per_24h=2, min_gap_minutes=0)
            self.assertEqual(db.execute("SELECT count(*) FROM event_log").fetchone()[0], before)

    def test_worker_log_has_a_reason_for_it(self):
        self.assertIn("unresolved_send_hold", SKIP_REASONS)


class AccountHoldCliTests(unittest.TestCase):
    def setUp(self):
        publishers.reset_write_marker()
        self.addCleanup(publishers.reset_write_marker)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [{"id": "my-bluesky", "platform": "bluesky",
                                                           "env_prefix": "REACHWEAVER_HD_BSKY",
                                                           "handle": "owner.bsky.social"}]}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_an_ambiguous_send_stops_the_next_post_before_any_request(self):
        with connect(self.db) as db:
            first = approved(db, "first note")
            second = approved(db, "second note")
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers.request_json",
                                                side_effect=[session(), session(), PublishError("network_or_timeout_uncertain")]):
            code, out, _ = self.cli("publish", "--live", "--post-id", first)
        self.assertEqual(code, 3, out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, first)["status"], "needs_review")
        # The second post: refused with the hold, exit 2, and not one request is made.
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers.request_json") as request, \
                patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--live", "--post-id", second)
        request.assert_not_called()
        network.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn(HOLD_REFUSAL, out)
        self.assertIn(first, out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, second)["status"], "approved")

    def test_a_sweep_skips_held_posts_and_keeps_going_for_other_accounts(self):
        with connect(self.db) as db:
            make_unresolved(db)
            held = approved(db, "held note")
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers.request_json") as request:
            code, out, _ = self.cli("publish", "--live")
        request.assert_not_called()
        self.assertIn(f"SKIPPED {held}: {HOLD_REFUSAL}", out)

    def test_the_dry_run_says_a_live_run_would_skip_it(self):
        with connect(self.db) as db:
            make_unresolved(db)
            approved(db, "held note")
        with patch("reachweaver.publishers.request_json") as request, patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish")
        request.assert_not_called()
        network.assert_not_called()
        self.assertIn(HOLD_REFUSAL, out)
        self.assertIn("--live would skip it before any claim or request", out)

    def test_reconcile_through_the_cli_releases_the_account(self):
        with connect(self.db) as db:
            first = make_unresolved(db)
            second = approved(db, "second note")
        code, out, err = self.cli("reconcile", first, "--not-published")
        self.assertEqual(code, 0, out + err)
        with connect(self.db) as db:
            self.assertIsNone(account_send_hold(db, "my-bluesky", second))


if __name__ == "__main__":
    unittest.main()
