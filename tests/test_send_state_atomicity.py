"""Send-state bookkeeping atomicity (draft pending owner security review).

Regression tests for docs/SECURITY-REVIEW-PROPOSAL-send-state-atomicity.md, findings F1-F5.
Everything is local or mocked: no network and no live publishing. Each test is written so that it
fails when its fix (P1-P5) is reverted.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver import cli, storage
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    add_post, claim_sending, connect, finish_send, get_post, is_paused, reconcile, set_paused,
    transition, uncertain_send,
)

ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
       "REACHWEAVER_TEST_ATOM_IDENTIFIER": "owner.bsky.social",
       "REACHWEAVER_TEST_ATOM_APP_PASSWORD": "mock-app-secret",
       "REACHWEAVER_TEST_ATOM2_IDENTIFIER": "owner.bsky.social",
       "REACHWEAVER_TEST_ATOM2_APP_PASSWORD": "mock-app-secret"}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "outbox.sqlite")
        self.config = str(Path(self.temp.name) / "accounts.json")
        Path(self.config).write_text(json.dumps({"accounts": [
            {"id": "atom-a", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_ATOM", "handle": "owner.bsky.social"},
            {"id": "atom-b", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_ATOM2", "handle": "owner.bsky.social"},
        ]}), encoding="utf-8")

    def approved(self, db, account="atom-a"):
        pid = add_post(db, account, "bluesky", "Useful evidence-backed technical note")
        transition(db, pid, "approve")
        return pid

    def sending(self, db, account="atom-a"):
        pid = self.approved(db, account)
        self.assertTrue(claim_sending(db, pid))
        return pid

    def actions(self, db, pid):
        return [r["action"] for r in db.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id", (pid,))]

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, ENV), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", self.config, *argv])
        return code, out.getvalue(), err.getvalue()


class StateConflictTests(Base):
    """F1 / P1."""

    def test_reconcile_then_finish_send_is_a_logged_conflict_not_a_publish(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            reconcile(db, pid, False, force=True)
            self.assertIs(finish_send(db, pid, "r1", "https://example.com/r1"), False)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["remote_id"]), ("draft", None))
            actions = self.actions(db, pid)
            self.assertNotIn("published", actions)
            self.assertEqual(actions[-1], "state_conflict")
            detail = db.execute("SELECT detail FROM event_log WHERE action='state_conflict'").fetchone()["detail"]
            self.assertIn("remote_id=r1", detail)
            self.assertIn("current_status=draft", detail)

    def test_uncertain_send_conflict_carries_error_code(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            reconcile(db, pid, False, force=True)
            self.assertIs(uncertain_send(db, pid, "timeout_uncertain"), False)
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertNotIn("needs_review", self.actions(db, pid))
            detail = db.execute("SELECT detail FROM event_log WHERE action='state_conflict'").fetchone()["detail"]
            self.assertIn("error_code=timeout_uncertain", detail)

    def test_normal_paths_return_true(self):
        with connect(self.db) as db:
            a, b = self.sending(db), self.sending(db, "atom-b")
            self.assertIs(finish_send(db, a, "r1", None), True)
            self.assertIs(uncertain_send(db, b, "x_uncertain"), True)
            self.assertEqual((get_post(db, a)["status"], get_post(db, b)["status"]), ("published", "needs_review"))

    def test_cli_prints_review_never_published_and_logs_conflict(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        log = str(Path(self.temp.name) / "worker.jsonl")

        def send_then_human_reconciles(*args, **kwargs):
            with connect(self.db) as other:  # a human reconciles while the send is in flight
                reconcile(other, pid, False, force=True)
            return ("r-race", "https://bsky.app/profile/t/post/1")

        with patch("reachweaver.cli.check_connection", return_value=object()), \
             patch("reachweaver.cli.publish", side_effect=send_then_human_reconciles):
            code, out, err = self.run_cli(["publish", "--live", "--post-id", pid,
                                           "--log-json", "--log-file", log])
        self.assertEqual(code, 3)
        self.assertIn("REVIEW", out)
        self.assertNotIn("PUBLISHED", out)
        self.assertIn("r-race", out + err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertEqual(self.actions(db, pid)[-1], "state_conflict")
        lines = [json.loads(x) for x in Path(log).read_text(encoding="utf-8").splitlines()]
        self.assertFalse([x for x in lines if x["event"] == "post_published"])
        review = [x for x in lines if x["event"] == "post_needs_review"]
        self.assertEqual(review[0]["error_code"], "state_conflict")


class ReconcileGuardTests(Base):
    """F2 / P2."""

    def test_fresh_sending_post_is_refused_and_untouched(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            with self.assertRaises(ValidationError) as ctx:
                reconcile(db, pid, False)
            self.assertIn("--force", str(ctx.exception))
            with self.assertRaises(ValidationError):
                reconcile(db, pid, True, "r1")
            self.assertEqual(get_post(db, pid)["status"], "sending")
            self.assertNotIn("reconciled_not_published", self.actions(db, pid))

    def test_allowed_after_threshold_and_with_force(self):
        with connect(self.db) as db:
            old, forced = self.sending(db), self.sending(db, "atom-b")
            later = (datetime.now(timezone.utc) + timedelta(minutes=16)).isoformat(timespec="seconds")
            reconcile(db, old, False, now=later)
            self.assertEqual(get_post(db, old)["status"], "draft")
            reconcile(db, forced, True, "r2", force=True)
            self.assertEqual(get_post(db, forced)["status"], "published")
            detail = db.execute("SELECT detail FROM event_log WHERE post_id=? AND action='reconciled_published'",
                                (forced,)).fetchone()["detail"]
            self.assertIn("forced=1", detail)

    def test_just_inside_threshold_still_refused(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            inside = (datetime.now(timezone.utc) + timedelta(minutes=14)).isoformat(timespec="seconds")
            with self.assertRaises(ValidationError):
                reconcile(db, pid, False, now=inside)

    def test_unreadable_claim_time_fails_closed(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            db.execute("UPDATE posts SET send_started_at=NULL WHERE id=?", (pid,))
            with self.assertRaises(ValidationError):
                reconcile(db, pid, False, now="2099-01-01T00:00:00+00:00")
            reconcile(db, pid, False, force=True)
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_needs_review_is_never_age_checked(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            uncertain_send(db, pid, "x_uncertain")
            reconcile(db, pid, False)
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_status_guarded_update_refuses_a_row_that_moved(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            stale = get_post(db, pid)
            db.execute("UPDATE posts SET status='published', remote_id='r9' WHERE id=?", (pid,))
            with patch("reachweaver.storage.get_post", return_value=stale):
                with self.assertRaises(ValidationError):
                    reconcile(db, pid, False, force=True)
            self.assertEqual(get_post(db, pid)["status"], "published")

    def test_cli_refuses_then_force_flag_works(self):
        with connect(self.db) as db:
            pid = self.sending(db)
        code, out, err = self.run_cli(["reconcile", pid, "--not-published"])
        self.assertNotEqual(code, 0)
        self.assertIn("--force", out + err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "sending")
        code, out, err = self.run_cli(["reconcile", pid, "--not-published", "--force"])
        self.assertEqual(code, 0, out + err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_existing_flags_still_parse(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            uncertain_send(db, pid, "x_uncertain")
        code, out, err = self.run_cli(["reconcile", pid, "--published", "--remote-id", "n1",
                                       "--remote-url", "https://example.com/n1"])
        self.assertEqual(code, 0, out + err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["remote_url"], "https://example.com/n1")


class TransitionAtomicityTests(Base):
    """F3 / P3."""

    def test_stale_read_cannot_overwrite_a_sending_post(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            stale = get_post(db, pid)  # what a racing cancel read before the claim
            self.assertTrue(claim_sending(db, pid))
            for command in ("cancel", "unapprove"):
                with patch("reachweaver.storage.get_post", return_value=stale):
                    with self.assertRaises(ValidationError):
                        transition(db, pid, command)
                self.assertEqual(get_post(db, pid)["status"], "sending")
            with patch("reachweaver.storage.get_post", return_value=stale):
                with self.assertRaises(ValidationError):
                    transition(db, pid, "schedule", "2099-01-01T00:00:00+00:00")
            row = get_post(db, pid)
            self.assertEqual(row["status"], "sending")
            self.assertIsNone(row["scheduled_at"])

    def test_transition_on_sending_post_is_rejected(self):
        with connect(self.db) as db:
            pid = self.sending(db)
            for command in ("cancel", "unapprove", "approve"):
                with self.assertRaises(ValidationError):
                    transition(db, pid, command)
            self.assertEqual(get_post(db, pid)["status"], "sending")

    def test_transition_holds_the_write_lock_while_it_reads(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            real_get = storage.get_post
            seen = []

            def spy(con, post_id):
                row = real_get(con, post_id)
                rival = sqlite3.connect(self.db, timeout=0.1)
                try:
                    rival.execute("BEGIN IMMEDIATE")
                    seen.append("rival got the lock")
                    rival.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    seen.append("locked")
                finally:
                    rival.close()
                return row

            with patch("reachweaver.storage.get_post", side_effect=spy):
                transition(db, pid, "cancel")
            self.assertEqual(seen, ["locked"])
            self.assertEqual(get_post(db, pid)["status"], "cancelled")

    def test_failed_transition_rolls_back_and_joins_an_outer_transaction(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            with self.assertRaises(ValidationError):
                transition(db, pid, "schedule")  # no --at
            self.assertFalse(db.in_transaction)
            self.assertEqual(get_post(db, pid)["status"], "approved")
            db.execute("BEGIN IMMEDIATE")  # calendar apply style: caller owns the transaction
            transition(db, pid, "cancel")
            self.assertTrue(db.in_transaction)
            db.execute("ROLLBACK")
            self.assertEqual(get_post(db, pid)["status"], "approved")


class BookkeepingFailureTests(Base):
    """F4 / P4."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(cli, "BOOKKEEPING_DELAY_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_operational_error_still_surfaces_remote_id_and_sweep_continues(self):
        with connect(self.db) as db:
            first, second = self.approved(db, "atom-a"), self.approved(db, "atom-b")
        real = cli.finish_send
        calls = []

        def flaky(con, post_id, *args, **kwargs):
            calls.append(post_id)
            if post_id == first:
                raise sqlite3.OperationalError("database is locked")
            return real(con, post_id, *args, **kwargs)

        sent = []

        def publisher(account, content, post_id, **kwargs):
            sent.append(post_id)
            return (f"remote-{len(sent)}", f"https://bsky.app/profile/t/post/{len(sent)}")

        log = str(Path(self.temp.name) / "worker.jsonl")
        with patch("reachweaver.cli.check_connection", return_value=object()), \
             patch("reachweaver.cli.publish", side_effect=publisher), \
             patch("reachweaver.cli.finish_send", side_effect=flaky):
            code, out, err = self.run_cli(["publish", "--live", "--limit", "5", "--log-json", "--log-file", log])
        self.assertEqual(sorted(sent), sorted([first, second]))     # the send itself ran once per post
        self.assertEqual(calls.count(first), cli.BOOKKEEPING_ATTEMPTS)  # bounded retry, then give up
        self.assertIn("remote-", err)                               # remote id on stderr
        self.assertNotIn(f"PUBLISHED {first}", out)
        self.assertIn(f"PUBLISHED {second}", out)                   # the sweep was not aborted
        lines = [json.loads(x) for x in Path(log).read_text(encoding="utf-8").splitlines()]
        codes = [x["error_code"] for x in lines if x["event"] == "post_needs_review"]
        self.assertEqual(codes, ["bookkeeping_failed"])
        end = [x for x in lines if x["event"] == "run_end"][0]
        self.assertEqual(end["counts"]["needs_review"], 1)
        self.assertEqual(end["counts"]["published"], 1)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, first)["status"], "sending")  # fails safe, never resent

    def test_transient_error_recovers_on_retry_without_resending(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        real = cli.finish_send
        attempts = []

        def once_flaky(con, *args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(con, *args, **kwargs)

        with patch("reachweaver.cli.check_connection", return_value=object()), \
             patch("reachweaver.cli.publish", return_value=("r1", "https://bsky.app/profile/t/post/1")) as pub, \
             patch("reachweaver.cli.finish_send", side_effect=once_flaky):
            code, out, err = self.run_cli(["publish", "--live", "--post-id", pid])
        self.assertEqual(code, 0)
        self.assertEqual(pub.call_count, 1)
        self.assertIn("PUBLISHED", out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "published")

    def test_uncertain_send_bookkeeping_failure_does_not_abort_the_run(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        with patch("reachweaver.cli.check_connection", return_value=object()), \
             patch("reachweaver.cli.publish", side_effect=ValidationError("boom_uncertain")), \
             patch("reachweaver.cli.uncertain_send", side_effect=sqlite3.OperationalError("locked")):
            code, out, err = self.run_cli(["publish", "--live", "--post-id", pid])
        self.assertEqual(code, 3)
        self.assertIn("REVIEW", out)
        self.assertIn("bookkeeping FAILED", out)


class PauseAtomicityTests(Base):
    """F5 / P5."""

    def test_pause_and_its_event_commit_together(self):
        with connect(self.db) as db:
            with patch("reachweaver.storage.event", side_effect=sqlite3.OperationalError("disk I/O error")):
                with self.assertRaises(sqlite3.OperationalError):
                    set_paused(db, True)
            self.assertFalse(is_paused(db))
            self.assertFalse(db.in_transaction)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='emergency_paused'").fetchone()[0], 0)
            set_paused(db, True)
            self.assertTrue(is_paused(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='emergency_paused'").fetchone()[0], 1)
            set_paused(db, False)
            self.assertFalse(is_paused(db))


if __name__ == "__main__":
    unittest.main()
