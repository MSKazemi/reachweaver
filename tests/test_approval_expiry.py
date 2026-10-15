"""Opt-in approval expiry (public issue #39): a tighten-only time-to-live on approvals. Offline; no provider calls.

Draft for owner security review (docs/SECURITY-REVIEW-PROPOSAL-39-approval-expiry.md). Default is OFF, so every
existing flow is unchanged until the owner sets ``approval-mode --max-age-hours``.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    APPROVAL_EXPIRED, APPROVAL_EXPIRES_BEFORE_DUE, add_post, approval_binding_problem, approval_expiry_problem,
    approval_max_age_hours, approval_rebind_required, check_approval_binding, claim_sending, connect, get_post,
    offline_key_mode, set_approval_max_age_hours, transition,
)


def after(post, hours: float) -> str:
    return (datetime.fromisoformat(post["approved_at"]) + timedelta(hours=hours)).isoformat(timespec="seconds")


def age(db, pid, hours: float) -> None:
    """Make an approval ``hours`` older by moving ``approved_at`` back (the ledger and hash do not cover it)."""
    moved = datetime.fromisoformat(get_post(db, pid)["approved_at"]) - timedelta(hours=hours)
    db.execute("UPDATE posts SET approved_at=? WHERE id=?", (moved.isoformat(timespec="seconds"), pid))


class ApprovalExpiryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [{"id": "my-bluesky", "platform": "bluesky",
                                                           "env_prefix": "REACHWEAVER_EX_BSKY",
                                                           "handle": "owner.bsky.social"}]}), encoding="utf-8")

    def approved(self, db, text="A verified research note"):
        pid = add_post(db, "my-bluesky", "bluesky", text, "launch", "https://example.org/r")
        transition(db, pid, "approve")
        return pid

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------------ default off

    def test_off_by_default_so_an_old_approval_still_sends(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertIsNone(approval_max_age_hours(db))
            far = after(get_post(db, pid), 24 * 400)
            self.assertTrue(claim_sending(db, pid, far))
            self.assertEqual(get_post(db, pid)["status"], "sending")

    # ------------------------------------------------------------------ the limit

    def test_inside_the_window_sends_and_outside_it_is_refused_with_nothing_claimed(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 24)
            fresh, stale = self.approved(db, "fresh note"), self.approved(db, "stale note")
            self.assertTrue(claim_sending(db, fresh, after(get_post(db, fresh), 23.9)))
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, stale, after(get_post(db, stale), 24.1))
            self.assertIn(APPROVAL_EXPIRED, str(caught.exception))
            self.assertIn("reachweaver unapprove", str(caught.exception))  # the exact re-approval steps
            row = get_post(db, stale)
            self.assertEqual((row["status"], row["last_error"]), ("approved", APPROVAL_EXPIRED))
            claims = db.execute("SELECT count(*) FROM event_log WHERE action='send_claimed' AND post_id=?",
                                (stale,)).fetchone()[0]
            self.assertEqual(claims, 0)
            failed = db.execute("SELECT detail FROM event_log WHERE action='approval_binding_failed' AND post_id=?",
                                (stale,)).fetchall()
            self.assertEqual(len(failed), 1)
            self.assertIn(APPROVAL_EXPIRED, failed[0][0])

    def test_re_approving_starts_a_new_window(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 24)
            pid = self.approved(db)
            with self.assertRaises(ValidationError):
                claim_sending(db, pid, after(get_post(db, pid), 30))
            transition(db, pid, "unapprove")
            transition(db, pid, "approve")
            self.assertTrue(claim_sending(db, pid, after(get_post(db, pid), 1)))

    def test_tampering_keeps_its_own_reason_even_when_the_approval_is_also_old(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 24)
            pid = self.approved(db)
            db.execute("UPDATE posts SET content='Changed after approval' WHERE id=?", (pid,))
            row = get_post(db, pid)
            problem = approval_binding_problem(row, offline_key_mode(db), now=after(row, 48))
            self.assertEqual(problem, "approval_hash_mismatch_reapproval_required")

    def test_an_unreadable_stored_limit_fails_closed(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("INSERT INTO settings(name,value) VALUES('approval_max_age_hours','soon')")
            self.assertEqual(approval_max_age_hours(db), 0)
            self.assertEqual(approval_expiry_problem(get_post(db, pid), 0), APPROVAL_EXPIRED)

    def test_a_row_without_a_readable_approval_time_counts_as_expired(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            row = dict(get_post(db, pid))

            class Row(dict):
                def keys(self):
                    return list(super().keys())
            for bad in (None, "", "not-a-time"):
                self.assertEqual(approval_expiry_problem(Row({**row, "approved_at": bad}), 24), APPROVAL_EXPIRED, bad)
            self.assertIsNone(approval_expiry_problem(Row(row), None))

    # ------------------------------------------------------------------ tighten-only

    def test_the_limit_can_only_be_lowered_by_a_command(self):
        with connect(self.db) as db:
            self.assertEqual(set_approval_max_age_hours(db, 72), 72)
            for same_or_higher in (72, 73, 8760):
                with self.assertRaisesRegex(ValidationError, "only a lower value"):
                    set_approval_max_age_hours(db, same_or_higher)
            self.assertEqual(set_approval_max_age_hours(db, 24), 24)
            self.assertEqual(approval_max_age_hours(db), 24)

    def test_invalid_values_are_refused(self):
        with connect(self.db) as db:
            for bad in (0, -1, 8761, True, "5", 1.5, None):
                with self.subTest(value=bad), self.assertRaises(ValidationError):
                    set_approval_max_age_hours(db, bad)  # type: ignore[arg-type]
            self.assertIsNone(approval_max_age_hours(db))

    # ------------------------------------------------------------------ schedule, health, show, CLI

    def test_scheduling_beyond_the_expiry_is_refused_and_changes_nothing(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 24)
            pid = self.approved(db)
            row = get_post(db, pid)
            with self.assertRaises(ValidationError) as caught:
                transition(db, pid, "schedule", after(row, 25))
            self.assertIn(APPROVAL_EXPIRES_BEFORE_DUE, str(caught.exception))
            self.assertIn("Nothing scheduled", str(caught.exception))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            transition(db, pid, "schedule", after(row, 20))
            self.assertEqual(get_post(db, pid)["status"], "scheduled")

    def test_a_scheduled_post_whose_approval_lapsed_is_not_sent_and_is_listed_for_attention(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 24)
            pid = self.approved(db)
            transition(db, pid, "schedule", after(get_post(db, pid), 20))
            late = after(get_post(db, pid), 26)
            with self.assertRaises(ValidationError):
                claim_sending(db, pid, late)
            self.assertEqual(check_approval_binding(db, pid, late), APPROVAL_EXPIRED)
            self.assertEqual(approval_rebind_required(db), [])  # still inside the window by the real clock
            age(db, pid, 48)
            listed = approval_rebind_required(db)
            self.assertEqual([(p["post_id"], p["reason"]) for p in listed], [(pid, APPROVAL_EXPIRED)])

    def test_show_displays_when_the_approval_lapses(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 48)
            pid = self.approved(db)
            expected = after(get_post(db, pid), 48)
        code, out, err = self.cli("show", pid)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["approval"]["approval_expires_at"], expected)

    def test_the_cli_sets_shows_and_tightens_the_limit(self):
        code, out, _ = self.cli("approval-mode")
        self.assertIn("Approval time-to-live (#39): off", out)
        code, out, _ = self.cli("approval-mode", "--max-age-hours", "72")
        self.assertEqual(code, 0)
        self.assertIn("Approval time-to-live (#39): 72 hours", out)
        code, out, err = self.cli("approval-mode", "--max-age-hours", "96")
        self.assertEqual(code, 2)
        self.assertIn("only a lower value", err)
        code, out, _ = self.cli("approval-mode", "--max-age-hours", "12")
        self.assertIn("12 hours", out)

    def test_a_live_publish_of_an_expired_approval_sends_nothing(self):
        with connect(self.db) as db:
            set_approval_max_age_hours(db, 1)
            pid = self.approved(db)
            age(db, pid, 5)
        env = {"REACHWEAVER_EX_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_EX_BSKY_APP_PASSWORD": "fake-value",
               "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with patch.dict(os.environ, env), patch("reachweaver.publishers.request_json") as request, \
                patch("reachweaver.publishers._send_http") as network:
            code, out, err = self.cli("publish", "--live", "--post-id", pid)
        request.assert_not_called()
        network.assert_not_called()
        self.assertNotEqual(code, 0)
        self.assertIn(APPROVAL_EXPIRED, out + err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
