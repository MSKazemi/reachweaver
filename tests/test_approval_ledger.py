"""Wave 9 item 1: approval replay ledger and per-approval nonce (owner-approved 2026-10-10).

docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md section 1. Offline: every network-capable call is a mock and
a socket guard turns any real connection attempt into a test failure.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import ledger
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection
from reachweaver.health import build_health
from reachweaver.ledger import (
    BROKEN, LEDGER_FILENAME, NONCE_MISSING, NONCE_TAG, REPLAY, UNAVAILABLE, is_nonce,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    SCHEMA, add_post, approval_payload, approval_payload_sha256, check_approval_binding, claim_sending, connect,
    finish_send, get_post, migrate, queue_health, schema_gaps, transition,
)

TOKEN = "tok-SECRET-ledger-test-value"
ACCOUNT = {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_LDG",
           "handle": "example.bsky.social"}
CHECK_ENV = {"REACHWEAVER_TEST_LDG_IDENTIFIER": "name", "REACHWEAVER_TEST_LDG_APP_PASSWORD": TOKEN}
RECEIPT = ("at://did:plc:x/app.bsky.feed.post/1", None)
NOW = "2026-10-08T12:00:00+00:00"
COLUMN_NAME = re.compile(r"^[a-z_0-9]+$")


def verified(*_args, **_kw):
    return VerifiedConnection("my-bluesky", "bluesky", "verified", "unverified")


def events(db, action):
    return [r["detail"] for r in db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id", (action,))]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        os.chmod(self.root, 0o700)
        self.db = str(self.root / "outbox.sqlite")
        self.ledger = self.root / LEDGER_FILENAME
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [ACCOUNT]}), encoding="utf-8")

        def refuse(*_a, **_k):
            raise AssertionError("network attempted in an offline test")
        for target, attr in ((socket.socket, "connect"), (socket, "create_connection"), (socket, "getaddrinfo")):
            patcher = patch.object(target, attr, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def approved(self, db, content="A useful verified research note"):
        pid = add_post(db, "my-bluesky", "bluesky", content, "launch", "https://example.org/r")
        transition(db, pid, "approve", approver="owner")
        return pid

    def ledger_lines(self):
        text = self.ledger.read_text(encoding="utf-8") if self.ledger.exists() else ""
        return [json.loads(line) for line in text.splitlines()]

    @staticmethod
    def snapshot(db, pid):
        return dict(get_post(db, pid))

    @staticmethod
    def restore(db, pid, snap):
        columns = [c for c in snap if c != "id"]
        assert all(COLUMN_NAME.match(c) for c in columns)  # fixed schema names, never input
        db.execute("UPDATE posts SET " + ",".join(f"{c}=?" for c in columns) + " WHERE id=?",
                   (*[snap[c] for c in columns], pid))

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live_env(self):
        return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", **CHECK_ENV}

    def live(self, pid):
        with patch("reachweaver.cli.check_connection", side_effect=verified) as preflight, \
                patch("reachweaver.cli.publish", return_value=RECEIPT) as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        return code, out + err, preflight, sent


class NonceAndPayloadTests(Base):
    def test_approve_draws_a_nonce_binds_it_and_creates_a_private_ledger(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            row = get_post(db, pid)
            self.assertTrue(is_nonce(row["approval_nonce"]))
            payload = approval_payload(row)
            self.assertEqual((payload["nonce_binding"], payload["approval_nonce"]), (NONCE_TAG, row["approval_nonce"]))
            self.assertEqual(NONCE_TAG, "reachweaver.storage.approval/2")
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            second = self.approved(db)
            self.assertNotEqual(get_post(db, second)["approval_nonce"], row["approval_nonce"])
        self.assertTrue(self.ledger.is_file())
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.ledger_lines(), [])  # nothing is consumed by an approval

    def test_payload_without_a_nonce_is_unchanged_golden(self):
        """A row without a nonce hashes exactly like before (the main-branch reference hash)."""
        reference = {"id": "p1", "platform": "mastodon", "account_id": "m1", "content": "Hello world https://example.org",
                     "scheduled_at": None, "source_url": "https://example.org/r", "campaign": "c1",
                     "media_json": None, "video_json": None, "approval_nonce": None}
        self.assertEqual(approval_payload_sha256(reference),
                         "b05c9191238bf9129932dfe6826bdb074a5f64da8d835cf1751650182f2d49e1")
        self.assertNotIn("approval_nonce", approval_payload(reference))
        self.assertNotEqual(approval_payload_sha256({**reference, "approval_nonce": "ab" * 16}),
                            approval_payload_sha256(reference))

    def test_nonce_is_part_of_the_hash_so_editing_it_is_a_mismatch(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET approval_nonce=? WHERE id=?", (ledger.new_nonce(), pid))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch_reapproval_required"):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(self.ledger_lines(), [])

    def test_unapprove_clears_the_nonce_and_schedule_keeps_it(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            nonce = get_post(db, pid)["approval_nonce"]
            transition(db, pid, "schedule", "2026-10-20T09:00:00+00:00")
            self.assertEqual(get_post(db, pid)["approval_nonce"], nonce)
            transition(db, pid, "unapprove")
            self.assertIsNone(get_post(db, pid)["approval_nonce"])


class ReplayTests(Base):
    def test_normal_approve_claim_still_sends_once_and_consumes_once(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            nonce = get_post(db, pid)["approval_nonce"]
            self.assertTrue(claim_sending(db, pid, now=NOW, max_per_24h=2, min_gap_minutes=60))
            self.assertFalse(claim_sending(db, pid, now=NOW))  # no longer eligible: not claimable twice
            self.assertEqual(get_post(db, pid)["status"], "sending")
            self.assertEqual(len(events(db, "send_claimed")), 1)
        (line,) = self.ledger_lines()
        self.assertEqual((line["post_id"], line["approval_nonce"], line["event"], line["v"]), (pid, nonce, "consumed", 1))
        self.assertEqual(line["prev"], "0" * 64)
        self.assertEqual(set(line), {"v", "post_id", "approval_nonce", "payload_sha256", "event", "ts", "prev"})

    def test_restored_older_approval_row_is_blocked_before_any_network_call(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            before = self.snapshot(db, pid)  # the valid approval, exactly as an attacker or a backup holds it
            self.assertTrue(claim_sending(db, pid, now=NOW))
            finish_send(db, pid, "remote-1", None)
            self.restore(db, pid, before)  # same post id, same text, hash, tag, nonce
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["approved_payload_sha256"]), ("approved", before["approved_payload_sha256"]))
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))  # the hash alone is fooled
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, pid, now="2026-10-08T13:00:00+00:00")
            self.assertTrue(str(caught.exception).startswith(REPLAY + ":"))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(len(events(db, "send_claimed")), 1)  # still only the first claim
            self.assertEqual(len(events(db, "approval_replay_blocked")), 1)
            self.assertEqual(get_post(db, pid)["last_error"], REPLAY)
            self.assertEqual(check_approval_binding(db, pid, now=NOW), REPLAY)
            self.assertEqual(len(events(db, "approval_replay_blocked")), 1)  # deduplicated, as binding failures are
        self.assertEqual(len(self.ledger_lines()), 1)

    def test_published_row_set_back_to_approved_is_blocked(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, now=NOW))
            finish_send(db, pid, "remote-1", None)
            db.execute("UPDATE posts SET status='approved', remote_id=NULL WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, REPLAY):
                claim_sending(db, pid, now="2026-10-08T13:00:00+00:00")
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_cli_live_run_blocks_a_restored_approval_with_no_preflight_and_no_send(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "A useful verified research note")
        self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        with connect(self.db) as db:
            before = self.snapshot(db, pid)
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 0, text)
        preflight.assert_called_once()
        sent.assert_called_once()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "published")
            self.restore(db, pid, before)
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 2)
        self.assertIn(REPLAY, text)
        preflight.assert_not_called()  # blocked before the identity preflight, which is a network call
        sent.assert_not_called()
        self.assertEqual(len(self.ledger_lines()), 1)

    def test_reapproval_after_unapprove_works_and_the_old_nonce_stays_refused(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            old = self.snapshot(db, pid)
            self.assertTrue(claim_sending(db, pid, now=NOW))
            finish_send(db, pid, "remote-1", None)
            self.restore(db, pid, old)
            with self.assertRaisesRegex(ValidationError, REPLAY):
                claim_sending(db, pid, now="2026-10-08T13:00:00+00:00")
            transition(db, pid, "unapprove")  # a human withdraws the restored approval, then re-approves
            transition(db, pid, "approve", approver="owner")
            fresh = get_post(db, pid)["approval_nonce"]
            self.assertNotEqual(fresh, old["approval_nonce"])
            self.assertTrue(claim_sending(db, pid, now="2026-10-08T14:00:00+00:00"))
            self.restore(db, pid, old)  # the old nonce is still consumed
            with self.assertRaisesRegex(ValidationError, REPLAY):
                claim_sending(db, pid, now="2026-10-08T15:00:00+00:00")
        self.assertEqual([line["approval_nonce"] for line in self.ledger_lines()], [old["approval_nonce"], fresh])

    def test_crash_between_the_ledger_append_and_the_status_flip_fails_closed(self):
        real = ledger.consume

        def append_then_crash(*args, **kw):
            real(*args, **kw)
            raise RuntimeError("simulated crash")
        with connect(self.db) as db:
            pid = self.approved(db)
            with patch("reachweaver.ledger.consume", side_effect=append_then_crash), \
                    self.assertRaises(RuntimeError):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(len(self.ledger_lines()), 1)
            with self.assertRaisesRegex(ValidationError, REPLAY):  # safe direction: nothing is sent
                claim_sending(db, pid, now=NOW)
            audit = queue_health(db, now=NOW)["approval_ledger"]
            self.assertEqual(audit["problems"], [{"post_id": pid, "reason": REPLAY}])
            self.assertTrue(queue_health(db, now=NOW)["attention_required"])
            transition(db, pid, "unapprove")  # documented recovery: a human unapprove + approve
            transition(db, pid, "approve", approver="owner")
            self.assertTrue(claim_sending(db, pid, now=NOW))

    def test_two_concurrent_claimers_produce_one_ledger_line_and_one_winner(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        barrier, results = threading.Barrier(2), []

        def worker():
            with connect(self.db) as con:
                barrier.wait()
                try:
                    results.append(claim_sending(con, pid, now=NOW))
                except ValidationError as exc:
                    results.append(str(exc))
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(sorted(map(str, results)), ["False", "True"])
        self.assertEqual(len(self.ledger_lines()), 1)


class UpgradeTests(Base):
    def old_database(self):
        """A database exactly as before this change: no approval_nonce column, one approved row."""
        old = SCHEMA.replace("  approval_nonce TEXT,  -- wave 9 item 1 (same placement rule): per-approval random id, "
                             "see reachweaver/ledger.py\n", "")
        self.assertNotEqual(old, SCHEMA, "posts DDL moved; update this fixture")
        con = sqlite3.connect(self.db, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.executescript(old)
        con.execute("""INSERT INTO posts(id,account_id,platform,content,campaign,source_url,status,created_at,approved_at)
                       VALUES('legacy','my-bluesky','bluesky','Approved before nonces','','','approved',
                              '2026-09-01T00:00:00+00:00','2026-09-02T00:00:00+00:00')""")
        row = con.execute("SELECT * FROM posts WHERE id='legacy'").fetchone()
        con.execute("UPDATE posts SET approved_payload_sha256=? WHERE id='legacy'", (approval_payload_sha256(row),))
        con.close()
        os.chmod(self.db, 0o600)

    def test_migration_adds_a_nullable_column_idempotently_and_keeps_rows(self):
        self.old_database()
        with connect(self.db) as db:
            self.assertEqual(schema_gaps(db), [])
            self.assertEqual([r[1] for r in db.execute("PRAGMA table_info(posts)")].count("approval_nonce"), 1)
            row = get_post(db, "legacy")
            self.assertEqual((row["status"], row["content"], row["approval_nonce"]), ("approved", "Approved before nonces", None))
            self.assertEqual(migrate(db), [])
        with connect(self.db) as db:  # reopening changes nothing
            self.assertEqual([r[1] for r in db.execute("PRAGMA table_info(posts)")].count("approval_nonce"), 1)
            self.assertEqual(migrate(db), [])

    def test_migrate_reports_the_added_column_once(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA.replace("  approval_nonce TEXT,  -- wave 9 item 1 (same placement rule): per-approval "
                                         "random id, see reachweaver/ledger.py\n", ""))
        self.assertIn("posts.approval_nonce", schema_gaps(con))
        self.assertEqual(migrate(con), ["approval_nonce"])
        self.assertEqual(migrate(con), [])

    def test_pre_upgrade_approval_is_not_grandfathered_and_the_upgrade_path_works(self):
        self.old_database()
        with connect(self.db) as db:
            before = approval_payload_sha256(get_post(db, "legacy"))
            self.assertEqual(before, get_post(db, "legacy")["approved_payload_sha256"])  # golden: unchanged hash
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, "legacy", now=NOW)
            self.assertTrue(str(caught.exception).startswith(NONCE_MISSING + ":"))
            self.assertEqual(check_approval_binding(db, "legacy", now=NOW), NONCE_MISSING)
            self.assertEqual(get_post(db, "legacy")["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])
            health = queue_health(db, now=NOW)
            self.assertEqual(health["approval_rebind_required"],
                             [{"post_id": "legacy", "account_id": "my-bluesky", "platform": "bluesky",
                               "status": "approved", "reason": NONCE_MISSING}])
            self.assertTrue(health["attention_required"])
            with self.assertRaisesRegex(ValidationError, NONCE_MISSING):
                transition(db, "legacy", "schedule", "2026-10-20T09:00:00+00:00")
            transition(db, "legacy", "unapprove")  # the documented path: unapprove, review, approve
            transition(db, "legacy", "approve", approver="owner")
            self.assertTrue(is_nonce(get_post(db, "legacy")["approval_nonce"]))
            self.assertIsNone(check_approval_binding(db, "legacy", now=NOW))
            self.assertTrue(claim_sending(db, "legacy", now=NOW))
        self.assertEqual(len(self.ledger_lines()), 1)

    def test_empty_ledger_and_published_rows_after_upgrade_is_normal(self):
        with connect(self.db) as db:
            db.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                          VALUES('old','my-bluesky','bluesky','Published long ago','published','2026-09-01T00:00:00+00:00')""")
            self.assertEqual(queue_health(db, now=NOW)["approval_ledger"], {"status": "absent", "problems": []})
            pid = self.approved(db)  # the first approve creates the ledger lazily
            self.assertTrue(claim_sending(db, pid, now=NOW))
            self.assertEqual(queue_health(db, now=NOW)["approval_ledger"], {"status": "ok", "problems": []})


class LedgerFailureTests(Base):
    def two_claims(self):
        with connect(self.db) as db:
            ids = [self.approved(db, content=f"Note number {i} for the ledger") for i in range(3)]
            for pid in ids[:2]:
                self.assertTrue(claim_sending(db, pid, now=NOW))
        return ids

    def assert_claim_refused(self, code, pid):
        with connect(self.db) as db:
            before = len(events(db, "send_claimed")), len(events(db, "approval_binding_failed"))
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, pid, now=NOW)
            self.assertTrue(str(caught.exception).startswith(code + ":"), str(caught.exception))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual((len(events(db, "send_claimed")), len(events(db, "approval_binding_failed"))), before)
            self.assertEqual(check_approval_binding(db, pid, now=NOW), code)
        return str(caught.exception)

    def test_missing_ledger_is_unavailable_and_nothing_is_claimed(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        self.ledger.unlink()
        self.assert_claim_refused(UNAVAILABLE, pid)
        with connect(self.db) as db:
            self.assertEqual(queue_health(db, now=NOW)["approval_ledger"]["status"], "unavailable")

    def test_deleted_ledger_blocks_new_approvals_instead_of_being_recreated(self):
        with connect(self.db) as db:
            self.approved(db)
            self.ledger.unlink()
            fresh = add_post(db, "my-bluesky", "bluesky", "Another note that needs approval")
            with self.assertRaisesRegex(ValidationError, UNAVAILABLE + ".*Nothing approved"):
                transition(db, fresh, "approve")
            self.assertEqual(get_post(db, fresh)["status"], "draft")
        self.assertFalse(self.ledger.exists())

    def test_wrong_mode_and_symlink_are_unavailable(self):
        with connect(self.db) as db:
            pid = self.approved(db)
        self.ledger.chmod(0o644)
        self.assert_claim_refused(UNAVAILABLE, pid)
        self.ledger.chmod(0o600)
        target = self.root / "elsewhere.jsonl"
        target.write_text("", encoding="utf-8")
        target.chmod(0o600)
        self.ledger.unlink()
        self.ledger.symlink_to(target)
        self.assert_claim_refused(UNAVAILABLE, pid)

    def test_in_memory_database_cannot_approve_or_claim(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO posts(id,account_id,platform,content,created_at) VALUES('d','a','bluesky','x','2026-10-01T00:00:00+00:00')")
        with self.assertRaisesRegex(ValidationError, UNAVAILABLE + ".*Nothing approved"):
            transition(con, "d", "approve")
        self.assertEqual(con.execute("SELECT status FROM posts").fetchone()[0], "draft")

    def test_edited_or_truncated_chain_is_broken_in_health_and_refuses_claims(self):
        ids = self.two_claims()
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        # Removing the first line breaks the second line's prev pointer.
        self.ledger.write_text(lines[1] + "\n", encoding="utf-8")
        with connect(self.db) as db:
            audit = queue_health(db, now=NOW)["approval_ledger"]
            self.assertEqual((audit["status"], audit["problems"]), ("broken", [{"post_id": None, "reason": BROKEN}]))
            self.assertTrue(queue_health(db, now=NOW)["attention_required"])
        self.assert_claim_refused(UNAVAILABLE, ids[2])
        # Changing a field of the first line breaks the chain as well.
        first = json.loads(lines[0])
        first["post_id"] = "forged"
        self.ledger.write_text(json.dumps(first, sort_keys=True, separators=(",", ":")) + "\n" + lines[1] + "\n",
                               encoding="utf-8")
        self.assert_claim_refused(UNAVAILABLE, ids[2])
        # A torn last line is broken too.
        self.ledger.write_text("\n".join(lines) + "\n{\"v\":1", encoding="utf-8")
        self.assert_claim_refused(UNAVAILABLE, ids[2])
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")  # restored: claims work again
        with connect(self.db) as db:
            self.assertTrue(claim_sending(db, ids[2], now=NOW))

    def test_a_removed_tail_line_is_caught_by_health_while_the_row_exists(self):
        ids = self.two_claims()
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.ledger.write_text(lines[0] + "\n", encoding="utf-8")  # chain still valid, tail consumption erased
        with connect(self.db) as db:
            audit = queue_health(db, now=NOW)["approval_ledger"]
            self.assertEqual(audit["problems"], [{"post_id": ids[1], "reason": BROKEN}])
            report = build_health(db, {"my-bluesky": ACCOUNT}, now=NOW, env=CHECK_ENV)
        self.assertIn(BROKEN, {r["code"] for r in report["reasons"]})
        self.assertEqual(report["overall"], "attention")
        self.assertEqual(report["global"]["approval_ledger"]["problems"], audit["problems"])


class SanitizedErrorTests(Base):
    def test_every_ledger_refusal_is_a_fixed_code_without_paths_nonces_hashes_or_secrets(self):
        with connect(self.db) as db:
            pid = self.approved(db, content=f"Note with {TOKEN} inside")
            row = self.snapshot(db, pid)
            nonce, digest = row["approval_nonce"], row["approved_payload_sha256"]
            messages = [ledger.refusal_text(code, pid) for code in ledger.LEDGER_CODES]
            self.ledger.chmod(0o644)  # a real refusal through the claim path
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, pid, now=NOW)
            messages.append(str(caught.exception))
            self.ledger.chmod(0o600)
            self.assertTrue(claim_sending(db, pid, now=NOW))
            self.restore(db, pid, row)
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, pid, now="2026-10-08T13:00:00+00:00")
            messages.append(str(caught.exception))
            details = [d for action in ("approval_replay_blocked", "approval_binding_failed") for d in events(db, action)]
            line = self.ledger.read_text(encoding="utf-8")
        for text in messages + details:
            for leaked in (str(self.root), LEDGER_FILENAME + "/", nonce, digest, TOKEN, "Errno", "Traceback", "sqlite"):
                self.assertNotIn(leaked, text)
            self.assertNotRegex(text, r"[0-9a-f]{32}")
        for code in ledger.LEDGER_CODES:
            self.assertRegex(code, r"^[a-z_]+$")
        self.assertNotIn(TOKEN, line)
        self.assertNotIn("research", line)  # no content in the ledger

    def test_cli_output_for_a_replay_is_the_fixed_text(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "A useful verified research note")
        self.cli("approve", pid, "--approver", "owner")
        with connect(self.db) as db:
            snap = self.snapshot(db, pid)
        self.live(pid)
        with connect(self.db) as db:
            self.restore(db, pid, snap)
        _, text, _, _ = self.live(pid)
        for leaked in (str(self.root), snap["approval_nonce"], snap["approved_payload_sha256"], TOKEN):
            self.assertNotIn(leaked, text)
        self.assertIn(f"reachweaver unapprove {pid}", text)


class WithdrawalTests(Base):
    """Security decision item 12 (owner-approved 2026-10-21): unapprove/cancel close the nonce in the ledger."""

    def withdrawn_copy(self, db, command, *, schedule=False):
        """Approve (and optionally schedule), keep a backup copy of the row, then withdraw it with ``command``."""
        pid = self.approved(db)
        if schedule:
            transition(db, pid, "schedule", "2026-10-08T11:00:00+00:00")
        backup = self.snapshot(db, pid)
        transition(db, pid, command)
        return pid, backup

    def test_unapprove_appends_one_withdrawn_line_and_a_restored_backup_is_refused(self):
        with connect(self.db) as db:
            pid, backup = self.withdrawn_copy(db, "unapprove")
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertEqual(events(db, "unapprove"), ["status=draft ledger=withdrawn"])
            self.restore(db, pid, backup)  # a database backup from before the unapprove
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["approved_payload_sha256"]), ("approved", approval_payload_sha256(row)))
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, pid, now=NOW)
            self.assertTrue(str(caught.exception).startswith(ledger.WITHDRAWN + ":"), str(caught.exception))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual((get_post(db, pid)["last_error"], events(db, "send_claimed")), (ledger.WITHDRAWN, []))
            self.assertEqual(len(events(db, "approval_replay_blocked")), 1)
            self.assertIn(f"reason={ledger.WITHDRAWN}", events(db, "approval_replay_blocked")[0])
            self.assertEqual(check_approval_binding(db, pid, now=NOW), ledger.WITHDRAWN)
            audit = queue_health(db, now=NOW)["approval_ledger"]
            self.assertEqual(audit, {"status": "broken", "problems": [{"post_id": pid, "reason": ledger.WITHDRAWN}]})
        (line,) = self.ledger_lines()
        self.assertEqual((line["post_id"], line["approval_nonce"], line["event"], line["v"], line["prev"]),
                         (pid, backup["approval_nonce"], "withdrawn", 1, "0" * 64))
        self.assertEqual(line["payload_sha256"], backup["approved_payload_sha256"])
        self.assertEqual(set(line), {"v", "post_id", "approval_nonce", "payload_sha256", "event", "ts", "prev"})

    def test_cancel_of_an_approved_or_scheduled_post_is_recorded_and_a_restore_is_refused(self):
        for schedule in (False, True):
            with self.subTest(schedule=schedule), connect(self.db) as db:
                pid, backup = self.withdrawn_copy(db, "cancel", schedule=schedule)
                self.assertEqual(get_post(db, pid)["status"], "cancelled")
                self.assertEqual(events(db, "cancel")[-1], "status=cancelled ledger=withdrawn")
                self.restore(db, pid, backup)
                with self.assertRaisesRegex(ValidationError, "^" + ledger.WITHDRAWN):
                    claim_sending(db, pid, now="2026-10-08T12:30:00+00:00")
                self.assertEqual(get_post(db, pid)["status"], "scheduled" if schedule else "approved")
                self.assertEqual(events(db, "send_claimed"), [])
        self.assertEqual([line["event"] for line in self.ledger_lines()], ["withdrawn", "withdrawn"])

    def test_unapprove_of_a_scheduled_post_is_recorded(self):
        with connect(self.db) as db:
            pid, backup = self.withdrawn_copy(db, "unapprove", schedule=True)
            self.restore(db, pid, backup)
            self.assertEqual(check_approval_binding(db, pid, now=NOW), ledger.WITHDRAWN)
            with self.assertRaisesRegex(ValidationError, "^" + ledger.WITHDRAWN):
                claim_sending(db, pid, now=NOW)

    def test_cli_live_run_refuses_a_restored_withdrawn_approval_before_any_request(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "A useful verified research note")
        self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        with connect(self.db) as db:
            backup = self.snapshot(db, pid)
        code, out, err = self.cli("unapprove", pid)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Post {pid} -> draft", out)
        with connect(self.db) as db:
            self.restore(db, pid, backup)
        code, text, preflight, sent = self.live(pid)
        self.assertEqual(code, 2)
        self.assertIn(ledger.WITHDRAWN, text)
        self.assertIn(f"reachweaver unapprove {pid}", text)
        for leaked in (str(self.root), backup["approval_nonce"], backup["approved_payload_sha256"], TOKEN):
            self.assertNotIn(leaked, text)
        preflight.assert_not_called()  # refused before the identity preflight, which is a network call
        sent.assert_not_called()
        self.assertEqual([line["event"] for line in self.ledger_lines()], ["withdrawn"])

    def test_reapproval_after_unapprove_works_and_the_withdrawn_nonce_stays_refused(self):
        with connect(self.db) as db:
            pid, backup = self.withdrawn_copy(db, "unapprove")
            transition(db, pid, "approve", approver="owner")  # normal use: re-approve draws a fresh nonce
            fresh = get_post(db, pid)["approval_nonce"]
            self.assertNotEqual(fresh, backup["approval_nonce"])
            self.assertIsNone(check_approval_binding(db, pid, now=NOW))
            self.assertTrue(claim_sending(db, pid, now=NOW))
            finish_send(db, pid, "remote-1", None)
            self.restore(db, pid, backup)
            with self.assertRaisesRegex(ValidationError, "^" + ledger.WITHDRAWN):
                claim_sending(db, pid, now="2026-10-08T13:00:00+00:00")
        self.assertEqual([(line["approval_nonce"], line["event"]) for line in self.ledger_lines()],
                         [(backup["approval_nonce"], "withdrawn"), (fresh, "consumed")])

    def test_documented_recovery_after_a_refused_restore_adds_no_second_line(self):
        with connect(self.db) as db:
            pid, backup = self.withdrawn_copy(db, "unapprove")
            self.restore(db, pid, backup)
            transition(db, pid, "unapprove")  # the refusal text's steps: unapprove, show, approve
            self.assertEqual(events(db, "unapprove")[-1], "status=draft ledger=withdrawn")
            transition(db, pid, "approve", approver="owner")
            self.assertTrue(claim_sending(db, pid, now=NOW))
        self.assertEqual([line["event"] for line in self.ledger_lines()], ["withdrawn", "consumed"])

    def test_draft_cancel_and_pre_nonce_rows_write_nothing(self):
        with connect(self.db) as db:
            self.approved(db)  # creates the ledger
            draft = add_post(db, "my-bluesky", "bluesky", "Never approved")
            transition(db, draft, "cancel")
            self.assertEqual(events(db, "cancel"), ["status=cancelled"])
            legacy = self.approved(db, content="Approved before nonces existed")
            db.execute("UPDATE posts SET approval_nonce=NULL WHERE id=?", (legacy,))
            transition(db, legacy, "unapprove")
            self.assertEqual(events(db, "unapprove"), ["status=draft"])
        self.assertEqual(self.ledger_lines(), [])

    def test_unusable_ledger_never_blocks_a_withdrawal_and_the_cli_warns(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "A useful verified research note")
        self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        self.ledger.chmod(0o644)  # not private: unusable, fail closed for sends
        code, out, err = self.cli("unapprove", pid)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"Post {pid} -> draft", out)
        self.assertIn("WARNING: the withdrawal of post", err)
        self.assertIn(UNAVAILABLE, err)
        self.assertNotIn(str(self.root), err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertEqual(events(db, "unapprove"), [f"status=draft ledger=unrecorded reason={UNAVAILABLE}"])
        self.ledger.chmod(0o600)
        self.assertEqual(self.ledger_lines(), [])
        self.ledger.unlink()  # a deleted ledger: cancel still works, with the same warning
        with connect(self.db) as db:
            transition(db, pid, "cancel")
            self.assertEqual(get_post(db, pid)["status"], "cancelled")
        # --json keeps its document on stdout; the warning goes to stderr only.
        with connect(self.db) as db:
            other = add_post(db, "my-bluesky", "bluesky", "Second note")
            db.execute("UPDATE posts SET status='approved', approval_nonce=? WHERE id=?", (ledger.new_nonce(), other))
        code, out, err = self.cli("cancel", other, "--json")
        self.assertEqual((code, json.loads(out)), (0, {"post_id": other, "status": "cancelled"}))
        self.assertIn("WARNING", err)

    def test_crash_after_the_withdrawn_line_fails_closed(self):
        real = ledger.withdraw

        def append_then_crash(*args, **kw):
            real(*args, **kw)
            raise RuntimeError("simulated crash")
        with connect(self.db) as db:
            pid = self.approved(db)
            with patch("reachweaver.ledger.withdraw", side_effect=append_then_crash), self.assertRaises(RuntimeError):
                transition(db, pid, "unapprove")
            self.assertEqual(get_post(db, pid)["status"], "approved")  # the row change was rolled back
            with self.assertRaisesRegex(ValidationError, "^" + ledger.WITHDRAWN):  # safe direction: nothing sent
                claim_sending(db, pid, now=NOW)
            transition(db, pid, "unapprove")
            transition(db, pid, "approve", approver="owner")
            self.assertTrue(claim_sending(db, pid, now=NOW))

    def test_ledger_format_compatibility(self):
        with connect(self.db) as db:
            consumed = self.approved(db)
            self.assertTrue(claim_sending(db, consumed, now=NOW))  # an old-style ledger: consumed lines only
            self.assertEqual(queue_health(db, now=NOW)["approval_ledger"], {"status": "ok", "problems": []})
            pid, _ = self.withdrawn_copy(db, "unapprove")
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["event"] for line in lines], ["consumed", "withdrawn"])
        # Unknown event names and a second line for the same approval are a broken chain (fail closed).
        for edit in ({"event": "revoked"}, {"event": "consumed", "post_id": consumed}):
            with self.subTest(edit=edit):
                first = json.loads(lines[0])
                second = {**json.loads(lines[1]), **edit}
                if second["post_id"] == consumed:
                    second["approval_nonce"] = first["approval_nonce"]
                second["prev"] = hashlib.sha256(lines[0].encode("utf-8")).hexdigest()
                self.ledger.write_text(lines[0] + "\n" + json.dumps(second, sort_keys=True, separators=(",", ":"))
                                       + "\n", encoding="utf-8")
                with connect(self.db) as db:
                    self.assertEqual(queue_health(db, now=NOW)["approval_ledger"]["status"], "broken")
                    self.assertEqual(ledger.check(db, pid, "ab" * 16), UNAVAILABLE)
        self.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with connect(self.db) as db:
            self.assertEqual(queue_health(db, now=NOW)["approval_ledger"], {"status": "ok", "problems": []})

    def test_health_reports_a_restored_withdrawn_approval(self):
        with connect(self.db) as db:
            pid, backup = self.withdrawn_copy(db, "cancel")
            self.restore(db, pid, backup)
            report = build_health(db, {"my-bluesky": ACCOUNT}, now=NOW, env=CHECK_ENV)
        self.assertIn(ledger.WITHDRAWN, {r["code"] for r in report["reasons"]})
        self.assertEqual(report["overall"], "attention")


if __name__ == "__main__":
    unittest.main()
