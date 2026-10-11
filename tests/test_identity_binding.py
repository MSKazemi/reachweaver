"""Wave 9 item 2: verified-identity binding at approve time (owner-approved 2026-10-10).

docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md section 2. Offline: every identity check is a mock and a
socket guard turns any real connection attempt into a test failure.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import identity
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection
from reachweaver.identity import (
    IDENTITY_MISMATCH, IDENTITY_MISSING, IDENTITY_REQUIRED, IDENTITY_UNAVAILABLE, current_fingerprint,
    identity_fingerprint,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    SCHEMA, add_post, approval_payload, approval_payload_sha256, check_approval_binding, claim_sending,
    connect, get_post, migrate, schema_gaps, transition,
)

TOKEN = "tok-SECRET-identity-test-value"
ACCOUNT = {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_IDB",
           "handle": "example.bsky.social"}
CHECK = "reachweaver.connections.check_connection"
RECEIPT = ("at://did:plc:x/app.bsky.feed.post/1", None)


def verified(*_args, **_kw):
    return VerifiedConnection("my-bluesky", "bluesky", "verified", "unverified")


def events(db, action):
    return [r["detail"] for r in db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id", (action,))]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.config = self.root / "accounts.json"
        self.write_config(ACCOUNT)

        def refuse(*_a, **_k):
            raise AssertionError("network attempted in an offline test")
        for target, attr in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
            patcher = patch.object(target, attr, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_config(self, account):
        self.config.write_text(json.dumps({"accounts": [account]}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live_env(self):
        return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
                "REACHWEAVER_TEST_IDB_IDENTIFIER": "name", "REACHWEAVER_TEST_IDB_APP_PASSWORD": TOKEN}

    def draft(self, text="A useful verified research note"):
        with connect(self.db) as db:
            return add_post(db, "my-bluesky", "bluesky", text)

    def approve_verified(self, pid):
        with patch(CHECK, side_effect=verified) as check:
            code, out, err = self.cli("approve", pid, "--verify-identity", "--online", "--approver", "owner",
                                      env=self.live_env())
        self.assertEqual(code, 0, out + err)
        check.assert_called_once()
        return out

    def post(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)

    def live(self, pid):
        with patch("reachweaver.cli.check_connection", side_effect=verified) as preflight, \
                patch("reachweaver.cli.publish", return_value=RECEIPT) as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        return code, out + err, preflight, sent


class FingerprintTests(unittest.TestCase):
    def test_pure_normalized_and_holds_no_value(self):
        a = identity_fingerprint(ACCOUNT)
        self.assertRegex(a, r"^[0-9a-f]{64}$")
        self.assertEqual(a, identity_fingerprint({**ACCOUNT, "handle": "@Example.Bsky.Social "}))
        self.assertNotIn("example", a)
        for change in ({"handle": "other.bsky.social"}, {"id": "other-account"}, {"service": "https://pds.example"}):
            self.assertNotEqual(a, identity_fingerprint({**ACCOUNT, **change}), change)

    def test_mastodon_server_and_handle_are_bound(self):
        masto = {"id": "m1", "platform": "mastodon", "env_prefix": "X_M", "handle": "alice", "server": "https://a.example"}
        base = identity_fingerprint(masto)
        self.assertNotEqual(base, identity_fingerprint({**masto, "server": "https://b.example"}))
        self.assertNotEqual(base, identity_fingerprint({**masto, "handle": "bob"}))
        self.assertEqual(base, identity_fingerprint({**masto, "server": "https://A.example/"}))

    def test_unbindable_accounts_fail_with_a_fixed_code(self):
        for account in ({**ACCOUNT, "backend": "postiz"}, {**ACCOUNT, "handle": ""}, {**ACCOUNT, "platform": "nope"}):
            with self.assertRaises(ValidationError) as caught:
                identity_fingerprint(account)
            self.assertEqual(str(caught.exception), IDENTITY_UNAVAILABLE)
            self.assertIsNone(current_fingerprint(account))
        self.assertIsNone(current_fingerprint(None))

    def test_codes_are_distinct_fixed_strings(self):
        self.assertEqual(identity.IDENTITY_TAG, "reachweaver.storage.approval/id-1")
        self.assertEqual(len(set(identity.IDENTITY_CODES)), 4)
        for code in identity.IDENTITY_CODES:
            self.assertRegex(code, r"^[a-z_]+$")


class PayloadTests(Base):
    def test_identity_changes_hash_under_its_own_tag_and_absent_changes_nothing(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "text")
            row = get_post(db, pid)
            plain = approval_payload_sha256(row)
            self.assertNotIn("identity", approval_payload(row))
            fp = identity_fingerprint(ACCOUNT)
            bound = approval_payload(row, identity=fp)
            self.assertEqual((bound["binding"], bound["identity_binding"], bound["identity"]),
                             ("reachweaver.storage.approval/1", "reachweaver.storage.approval/id-1", fp))
            self.assertNotEqual(plain, approval_payload_sha256(row, identity=fp))

    def test_new_column_migrates_onto_an_old_database(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA.replace("  approved_identity TEXT,  -- wave 9 item 2; placed here so the legacy-DDL guard test stays untouched\n", ""))
        self.assertIn("posts.approved_identity", schema_gaps(con))
        self.assertEqual(migrate(con), ["approved_identity"])
        self.assertEqual(schema_gaps(con), [])


class ApproveTests(Base):
    def test_binding_stored_at_approve_and_nothing_secret_is_stored(self):
        pid = self.draft()
        plain = self.draft()
        out = self.approve_verified(pid)
        self.assertIn("identity bound (approval/id-1)", out)
        with connect(self.db) as db:
            row = get_post(db, pid)
            self.assertEqual(row["approved_identity"], identity_fingerprint(ACCOUNT))
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertEqual(approval_payload(row)["identity_binding"], identity.IDENTITY_TAG)
            self.assertIn("identity=bound", events(db, "approve")[-1])
            # same text approved without the flag keeps the plain approval/1 hash
            transition(db, plain, "approve")
            base = get_post(db, plain)
            self.assertIsNone(base["approved_identity"])
            self.assertNotEqual(base["approved_payload_sha256"], row["approved_payload_sha256"])
            dump = json.dumps([dict(r) for r in db.execute("SELECT * FROM posts")]
                              + [dict(r) for r in db.execute("SELECT * FROM event_log")]
                              + [dict(r) for r in db.execute("SELECT * FROM settings")])
        for secret in (TOKEN, "example.bsky.social", "REACHWEAVER_TEST_IDB"):
            self.assertNotIn(secret, dump)
        from reachweaver.privacy import redact
        self.assertEqual(redact(dump), redact(redact(dump)))

    def test_failed_check_at_approve_writes_nothing(self):
        pid = self.draft()
        with patch(CHECK, side_effect=ValidationError("identity_mismatch: Bluesky session handle does not match")):
            code, out, err = self.cli("approve", pid, "--verify-identity", "--online", env=self.live_env())
        self.assertEqual(code, 2)
        with connect(self.db) as db:
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["approved_identity"], row["approved_payload_sha256"]),
                             ("draft", None, None))
            self.assertEqual(events(db, "approve"), [])

    def test_verify_identity_needs_online_and_makes_no_call_without_it(self):
        pid = self.draft()
        with patch(CHECK) as check:
            code, _, err = self.cli("approve", pid, "--verify-identity", env=self.live_env())
        self.assertEqual(code, 2)
        self.assertIn("--online", err)
        check.assert_not_called()
        self.assertEqual(self.post(pid)["status"], "draft")

    def test_plain_approve_is_unchanged_offline_and_makes_no_call(self):
        pid = self.draft()
        with patch(CHECK) as check:
            code, out, err = self.cli("approve", pid)
        self.assertEqual(code, 0, err)
        check.assert_not_called()
        self.assertNotIn("identity bound", out)
        self.assertIsNone(self.post(pid)["approved_identity"])

    def test_non_draft_is_refused_before_any_call(self):
        pid = self.draft()
        self.cli("approve", pid)
        with patch(CHECK) as check:
            code, _, err = self.cli("approve", pid, "--verify-identity", "--online", env=self.live_env())
        self.assertEqual(code, 2)
        check.assert_not_called()

    def test_postiz_backed_account_cannot_be_identity_bound(self):
        self.write_config({"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_IDB",
                           "backend": "postiz", "postiz_base_url": "https://postiz.example.org",
                           "postiz_integration_id": "int-1", "handle": "example.bsky.social"})
        pid = self.draft()
        code, _, err = self.cli("approve", pid, "--verify-identity", "--online", env=self.live_env())
        self.assertEqual(code, 2)
        self.assertIn("backend_account", err)  # check_connection refuses a backend account before any request
        self.assertEqual(self.post(pid)["status"], "draft")

    def test_unapprove_clears_the_identity_and_schedule_keeps_it(self):
        pid = self.draft()
        self.approve_verified(pid)
        with connect(self.db) as db:
            transition(db, pid, "schedule", "2030-01-01T00:00:00+00:00")
            row = get_post(db, pid)
            self.assertEqual(row["approved_identity"], identity_fingerprint(ACCOUNT))
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            transition(db, pid, "unapprove")
            self.assertIsNone(get_post(db, pid)["approved_identity"])


class SendTests(Base):
    def test_unchanged_identity_still_sends(self):
        pid = self.draft()
        self.approve_verified(pid)
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 0, text)
        preflight.assert_called_once()
        sent.assert_called_once()
        self.assertEqual(self.post(pid)["status"], "published")

    def test_repointed_handle_is_blocked_before_any_network_call(self):
        pid = self.draft()
        self.approve_verified(pid)
        self.write_config({**ACCOUNT, "handle": "other.bsky.social"})  # env prefix now points at @other
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 2)
        self.assertIn(IDENTITY_MISMATCH, text)
        preflight.assert_not_called()
        sent.assert_not_called()
        with connect(self.db) as db:
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]), ("approved", IDENTITY_MISMATCH))
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)
            self.assertEqual(events(db, "send_claimed"), [])
        for leaked in ("other.bsky.social", "example.bsky.social", identity_fingerprint(ACCOUNT), TOKEN):
            self.assertNotIn(leaked, text)

    def test_repointed_service_is_blocked(self):
        pid = self.draft()
        self.approve_verified(pid)
        self.write_config({**ACCOUNT, "service": "https://pds.example.org"})
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 2)
        self.assertIn(IDENTITY_MISMATCH, text)
        sent.assert_not_called()

    def test_account_id_repoint_on_the_row_is_blocked_by_the_bound_hash(self):
        pid = self.draft()
        self.approve_verified(pid)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET account_id='other-account' WHERE id=?", (pid,))
            problem = check_approval_binding(db, pid, identity_fingerprint=identity_fingerprint(ACCOUNT))
        self.assertEqual(problem, "approval_hash_mismatch_reapproval_required")

    def test_tampered_identity_column_is_a_hash_mismatch(self):
        pid = self.draft()
        self.approve_verified(pid)
        forged = identity_fingerprint({**ACCOUNT, "handle": "other.bsky.social"})
        for value in (None, forged):
            with connect(self.db) as db:
                db.execute("UPDATE posts SET approved_identity=? WHERE id=?", (value, pid))
                problem = check_approval_binding(db, pid, identity_fingerprint=forged)
            self.assertEqual(problem, "approval_hash_mismatch_reapproval_required", value)

    def test_claim_refuses_a_wrong_or_missing_fingerprint_and_accepts_the_right_one(self):
        pid = self.draft()
        self.approve_verified(pid)
        good = identity_fingerprint(ACCOUNT)
        with connect(self.db) as db:
            for fp in (None, "0" * 64, identity_fingerprint({**ACCOUNT, "handle": "x.bsky.social"})):
                with self.assertRaises(ValidationError) as caught:
                    claim_sending(db, pid, identity_fingerprint=fp)
                self.assertTrue(str(caught.exception).startswith(IDENTITY_MISMATCH))
                self.assertEqual(get_post(db, pid)["status"], "approved")
            with self.assertRaises(ValidationError):
                claim_sending(db, pid)  # a sender that never passes one (e.g. a backend) fails closed
            self.assertTrue(claim_sending(db, pid, identity_fingerprint=good))
            self.assertEqual(get_post(db, pid)["status"], "sending")


class UpgradePathTests(Base):
    def test_old_approval_still_sends_until_the_owner_requires_identity(self):
        pid = self.draft()
        self.cli("approve", pid)  # exactly what a pre-change approval looks like: no identity
        code, text, _, sent = self.live(pid)
        self.assertEqual(code, 0, text)
        sent.assert_called_once()

    def test_require_identity_blocks_old_approvals_until_reapproved_with_verification(self):
        old, fresh = self.draft("An older approved post"), self.draft("A draft not yet approved")
        self.cli("approve", old)
        code, out, err = self.cli("approval-mode", "--require-identity")
        self.assertEqual(code, 0, err)
        self.assertIn("Identity-bound approval (wave 9 item 2): required", out)
        self.assertIn("need attention", out)  # the old approval is listed as needing re-approval
        # 1. the old approval is not grandfathered: refused before any network call
        code, text, preflight, sent = self.live(old)
        self.assertEqual(code, 2)
        self.assertIn(IDENTITY_MISSING, text)
        preflight.assert_not_called()
        sent.assert_not_called()
        self.assertEqual(self.post(old)["status"], "approved")
        # 2. plain approve is refused offline with a clear message
        with patch(CHECK) as check:
            code, _, err = self.cli("approve", fresh)
        self.assertEqual(code, 2)
        self.assertIn(IDENTITY_REQUIRED, err)
        check.assert_not_called()
        self.assertEqual(self.post(fresh)["status"], "draft")
        # 3. the documented upgrade path works
        self.assertEqual(self.cli("unapprove", old)[0], 0)
        self.approve_verified(old)
        code, text, _, sent = self.live(old)
        self.assertEqual(code, 0, text)
        sent.assert_called_once()

    def test_scheduling_an_unbound_approval_is_refused_when_required(self):
        pid = self.draft()
        self.cli("approve", pid)
        self.cli("approval-mode", "--require-identity")
        code, _, err = self.cli("schedule", pid, "--at", "2030-01-01T00:00:00+00:00")
        self.assertEqual(code, 2)
        self.assertIn(IDENTITY_MISSING, err)

    def test_library_approve_is_refused_when_required_and_the_latch_cannot_be_removed(self):
        pid = self.draft()
        with connect(self.db) as db:
            from reachweaver.storage import latch_identity_mode
            latch_identity_mode(db)
            with self.assertRaises(ValidationError) as caught:
                transition(db, pid, "approve")
            self.assertIn(IDENTITY_REQUIRED, str(caught.exception))
            with self.assertRaises(ValidationError):
                transition(db, pid, "approve", identity="not-a-fingerprint")
            transition(db, pid, "approve", identity=identity_fingerprint(ACCOUNT))
        with self.assertRaises(SystemExit):  # tighten-only: there is no "off" form
            self.cli("approval-mode", "--require-identity", "off")
        code, out, _ = self.cli("approval-mode")
        self.assertIn("required", out.splitlines()[0])

    def test_dry_run_lists_a_required_but_unbound_approval_as_needing_reapproval(self):
        pid = self.draft()
        self.cli("approve", pid)
        self.cli("approval-mode", "--require-identity")
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn("re-approved", out)


class SanitizedErrorTests(Base):
    def test_refusal_text_is_fixed_and_leaks_nothing(self):
        for code in identity.IDENTITY_CODES:
            text = identity.refusal_text(code, "post-1")
            self.assertTrue(text.startswith(code))
            self.assertNotIn("bsky", text)
            self.assertNotRegex(text, r"[0-9a-f]{64}")


if __name__ == "__main__":
    unittest.main()
