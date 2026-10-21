"""Issue #7: approval payload hash binding. Offline; every provider call is mocked.

Owner-approved 2026-10-08 (docs/SECURITY-REVIEW-PROPOSAL-7-11.md): legacy approvals are
not grandfathered (D2) and a mismatch fails closed with the post left approved/scheduled (D5).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver import postiz, send_options
from reachweaver.accounts import load_accounts
from reachweaver.calendar import apply_calendar, propose_calendar
from reachweaver.cli import main
from reachweaver.health import build_health, exit_code, render_health
from reachweaver.policy import ValidationError
from reachweaver.schemas import canonical_json
from reachweaver.storage import (
    APPROVAL_BINDING_FIELDS, APPROVAL_BINDING_TAG, SCHEMA, add_post, approval_payload,
    approval_payload_sha256, check_approval_binding, claim_sending, connect, get_post, migrate,
    queue_health, set_paused, transition,
)

NOW = "2026-10-08T12:00:00+00:00"
FUTURE = "2026-10-20T09:00:00+00:00"
LATER = "2026-10-21T09:00:00+00:00"
BOUND_COLUMNS = {"content": "Changed after approval", "account_id": "other-account",
                 "platform": "mastodon", "source_url": "https://attacker.example/x",
                 "campaign": "swapped-campaign"}


def events(db, action):
    return db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id", (action,)).fetchall()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")

    def approved(self, db, content="A useful verified research note", **kw):
        pid = add_post(db, "my-bluesky", "bluesky", content, "launch", "https://example.org/r")
        transition(db, pid, "approve", **kw)
        return pid

    def legacy(self, db, content="Approved before the binding existed", scheduled_at=None):
        """An approved (or scheduled) row exactly as an upgraded pre-#7 database holds it."""
        pid = self.approved(db, content=content)
        status = "scheduled" if scheduled_at else "approved"
        db.execute("UPDATE posts SET status=?, scheduled_at=?, approved_payload_sha256=NULL, approved_by=NULL, "
                   "approval_channel=NULL WHERE id=?", (status, scheduled_at, pid))
        return pid


class ApprovalBindingStorageTests(Base):
    def test_approve_stores_hash_and_attribution(self):
        with connect(self.db) as db:
            pid = self.approved(db, approver="mohsen", approval_channel="cli_flag")
            row = get_post(db, pid)
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertEqual((row["approved_by"], row["approval_channel"]), ("mohsen", "cli_flag"))
            detail = events(db, "approve")[-1]["detail"]
            self.assertIn(f"approval_sha256={row['approved_payload_sha256'][:12]}", detail)
            self.assertIn("approver=mohsen channel=cli_flag", detail)
            library = self.approved(db)
            self.assertEqual(get_post(db, library)["approval_channel"], "library")
            self.assertIsNone(get_post(db, library)["approved_by"])

    def test_hash_uses_schemas_canonical_json(self):
        with connect(self.db) as db:
            row = get_post(db, self.approved(db, content="Unicode é — kept byte-exact"))
            payload = approval_payload(row)
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG)
            # Wave 9 item 1: every approval also binds its random nonce under the distinct tag approval/2.
            self.assertEqual(set(payload), {"binding", "account_id", "platform", "content",
                                            "scheduled_at", "source_url", "campaign",
                                            "nonce_binding", "approval_nonce"})
            self.assertEqual(payload["nonce_binding"], "reachweaver.storage.approval/2")
            self.assertEqual(payload["approval_nonce"], row["approval_nonce"])
            self.assertIsNone(payload["scheduled_at"])
            expected = hashlib.sha256(canonical_json(payload)).hexdigest()
            self.assertEqual(approval_payload_sha256(row), expected)
            reordered = dict(reversed(list(payload.items())))
            self.assertEqual(hashlib.sha256(canonical_json(reordered)).hexdigest(), expected)

    def test_unchanged_payload_claims_normally(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, now=NOW, max_per_24h=2, min_gap_minutes=60))
            self.assertEqual(get_post(db, pid)["status"], "sending")
            self.assertEqual(len(events(db, "send_claimed")), 1)
            self.assertEqual(events(db, "approval_binding_failed"), [])

    def test_each_bound_field_tamper_blocks_claim(self):
        for column, value in BOUND_COLUMNS.items():
            with self.subTest(column=column), connect(self.db) as db:
                pid = self.approved(db)
                db.execute(f"UPDATE posts SET {column}=? WHERE id=?", (value, pid))
                with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch_reapproval_required"):
                    claim_sending(db, pid, now=NOW)
                self.assertEqual(get_post(db, pid)["status"], "approved")
        with connect(self.db) as db:  # scheduled_at is bound too (set behind schedule's back)
            pid = self.approved(db)
            transition(db, pid, "schedule", FUTURE)
            db.execute("UPDATE posts SET scheduled_at=? WHERE id=?", ("2026-10-08T11:00:00+00:00", pid))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "scheduled")

    def test_mismatch_keeps_post_approved_writes_one_event_no_send_claimed(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET content='Edited behind the approval' WHERE id=?", (pid,))
            for _ in range(3):  # a worker timer retrying must not flood the audit log
                with self.assertRaisesRegex(ValidationError, "No social post sent"):
                    claim_sending(db, pid, now=NOW, max_per_24h=1, min_gap_minutes=60)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]),
                             ("approved", "approval_hash_mismatch_reapproval_required"))
            failures = events(db, "approval_binding_failed")
            self.assertEqual(len(failures), 1)
            self.assertIn("reason=approval_hash_mismatch_reapproval_required", failures[0]["detail"])
            self.assertNotIn("Edited", failures[0]["detail"])  # hash prefixes only, never content
            self.assertEqual(events(db, "send_claimed"), [])
            other = self.approved(db)  # the refused post consumed no rolling budget
            self.assertTrue(claim_sending(db, other, now=NOW, max_per_24h=1, min_gap_minutes=60))

    def test_legacy_null_hash_requires_reapproval(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "approval_hash_missing_reapproval_required"):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertIn("stored=none", events(db, "approval_binding_failed")[0]["detail"])

    def test_unapprove_then_approve_restores_eligibility(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET content='Reviewed replacement text' WHERE id=?", (pid,))
            with self.assertRaises(ValidationError):
                claim_sending(db, pid, now=NOW)
            transition(db, pid, "unapprove")
            self.assertIsNone(get_post(db, pid)["last_error"])
            transition(db, pid, "approve", approver="owner", approval_channel="cli_flag")
            self.assertTrue(claim_sending(db, pid, now=NOW))

    def test_schedule_rebinds_hash_and_reschedule_rebinds_again(self):
        with connect(self.db) as db:
            pid = self.approved(db, approver="owner", approval_channel="cli_flag")
            first = get_post(db, pid)["approved_payload_sha256"]
            transition(db, pid, "schedule", FUTURE)
            row = get_post(db, pid)
            self.assertNotEqual(row["approved_payload_sha256"], first)
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertEqual((row["approved_by"], row["approval_channel"]), ("owner", "cli_flag"))
            self.assertIn("approval_sha256=", events(db, "schedule")[-1]["detail"])
            transition(db, pid, "schedule", LATER)
            row = get_post(db, pid)
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertTrue(claim_sending(db, pid, now="2026-10-21T09:05:00+00:00"))

    def test_schedule_refuses_tampered_post_without_laundering(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            before = get_post(db, pid)["approved_payload_sha256"]
            db.execute("UPDATE posts SET content='Tampered before scheduling' WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "Nothing scheduled"):
                transition(db, pid, "schedule", FUTURE)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["scheduled_at"], row["approved_payload_sha256"]),
                             ("approved", None, before))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, now=NOW)

    def test_schedule_refuses_legacy_null_hash(self):
        # Stricter than the draft (which left the hash NULL and let the claim refuse later):
        # scheduling an unbound approval is refused outright and changes nothing.
        with connect(self.db) as db:
            for scheduled_at in (None, FUTURE):
                with self.subTest(scheduled_at=scheduled_at):
                    pid = self.legacy(db, scheduled_at=scheduled_at)
                    before = tuple(get_post(db, pid))
                    with self.assertRaisesRegex(ValidationError, "approval_hash_missing.*Nothing scheduled"):
                        transition(db, pid, "schedule", LATER)
                    self.assertEqual(tuple(get_post(db, pid)), before)
                    with self.assertRaisesRegex(ValidationError, "approval_hash_missing"):
                        claim_sending(db, pid, now="2026-10-22T09:01:00+00:00")

    def test_legacy_scheduled_row_refused_stays_scheduled_no_claim(self):
        with connect(self.db) as db:
            pid = self.legacy(db, scheduled_at="2026-10-08T11:00:00+00:00")
            for _ in range(2):
                with self.assertRaisesRegex(ValidationError, "approval_hash_missing_reapproval_required.*"
                                                              "reachweaver unapprove"):
                    claim_sending(db, pid, now=NOW, max_per_24h=2, min_gap_minutes=60)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]),
                             ("scheduled", "approval_hash_missing_reapproval_required"))
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)
            self.assertEqual(events(db, "send_claimed"), [])

    def test_reapproval_of_legacy_row_restores_eligibility(self):
        with connect(self.db) as db:
            pid = self.legacy(db, scheduled_at="2026-10-08T11:00:00+00:00")
            with self.assertRaises(ValidationError):
                claim_sending(db, pid, now=NOW)
            with self.assertRaisesRegex(ValidationError, "Cannot approve post in state scheduled"):
                transition(db, pid, "approve")  # no in-place rebind: approve only accepts a draft
            self.assertEqual(transition(db, pid, "unapprove"), "draft")
            self.assertEqual(transition(db, pid, "approve", approver="owner", approval_channel="cli_flag"),
                             "approved")
            transition(db, pid, "schedule", FUTURE)
            row = get_post(db, pid)
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertTrue(claim_sending(db, pid, now="2026-10-20T09:01:00+00:00"))

    def test_expected_sha256_must_match_the_bound_row(self):
        # A sender's copy read before the lock must be the bound payload, not just "a" payload.
        with connect(self.db) as db:
            pid = self.approved(db)
            stale_copy = dict(get_post(db, pid))
            stale_copy["content"] = "Copy that differs from the bound row"
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, now=NOW, expected_sha256=approval_payload_sha256(stale_copy))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])
            transition(db, pid, "unapprove")
            transition(db, pid, "approve")
            self.assertTrue(claim_sending(db, pid, now=NOW,
                                          expected_sha256=approval_payload_sha256(get_post(db, pid))))

    def test_check_approval_binding_records_once_and_never_changes_status(self):
        with connect(self.db) as db:
            good, bad = self.approved(db), self.legacy(db)
            self.assertIsNone(check_approval_binding(db, good, now=NOW))
            draft = add_post(db, "my-bluesky", "bluesky", "A draft that is not approved")
            self.assertIsNone(check_approval_binding(db, draft, now=NOW))
            for _ in range(2):
                self.assertEqual(check_approval_binding(db, bad, now=NOW),
                                 "approval_hash_missing_reapproval_required")
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)
            self.assertEqual([get_post(db, p)["status"] for p in (good, bad)], ["approved", "approved"])

    def test_calendar_apply_rebinds_and_rolls_back_on_tamper(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
        kw = dict(account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
                  start="2026-10-12", days=14, now=now)
        with connect(self.db) as db:
            a, b = self.approved(db), self.approved(db, content="A second verified research note")
            apply_calendar(db, propose_calendar(db, **kw), now=now)
            for pid in (a, b):
                row = get_post(db, pid)
                self.assertEqual(row["status"], "scheduled")
                self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            c, d = self.approved(db, content="Third note"), self.approved(db, content="Fourth note")
            plan = propose_calendar(db, post_ids=[c, d], **kw)
            db.execute("UPDATE posts SET content='Tampered fourth' WHERE id=?", (d,))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                apply_calendar(db, plan, now=now)
            self.assertEqual([get_post(db, p)["status"] for p in (c, d)], ["approved", "approved"])
            self.assertIsNone(get_post(db, c)["scheduled_at"])

    def test_unapprove_and_cancel_clear_binding(self):
        with connect(self.db) as db:
            for command in ("unapprove", "cancel"):
                with self.subTest(command=command):
                    pid = self.approved(db, approver="owner", approval_channel="cli_flag")
                    transition(db, pid, command)
                    row = get_post(db, pid)
                    self.assertEqual([row[name] for name in APPROVAL_BINDING_FIELDS], [None, None, None])

    def test_attribution_only_on_approve_and_validated(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            with self.assertRaisesRegex(ValidationError, "only applies to approve"):
                transition(db, pid, "unapprove", approver="owner")
            draft = add_post(db, "my-bluesky", "bluesky", "Another useful note")
            for bad in ("", "has space", "x" * 121, "semi;colon", "-leading"):
                with self.subTest(bad=bad), self.assertRaises(ValidationError):
                    transition(db, draft, "approve", approver=bad, approval_channel="cli_flag")
            with self.assertRaisesRegex(ValidationError, "channel"):
                transition(db, draft, "approve", approval_channel="telepathy")
            self.assertEqual(get_post(db, draft)["status"], "draft")

    def test_pause_is_reported_before_binding_failure(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET content='Changed' WHERE id=?", (pid,))
            set_paused(db, True)
            with self.assertRaisesRegex(ValidationError, "emergency pause"):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(events(db, "approval_binding_failed"), [])

    def unmigrated(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        con.executescript("""
            CREATE TABLE posts(id TEXT PRIMARY KEY, account_id TEXT, platform TEXT, content TEXT,
              campaign TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
              status TEXT, created_at TEXT, approved_at TEXT, scheduled_at TEXT, last_error TEXT,
              send_started_at TEXT);
            CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
              post_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO posts(id,account_id,platform,content,status,created_at) VALUES
              ('d1','acct','bluesky','Hello','draft','2026-10-01T00:00:00+00:00'),
              ('a1','acct','bluesky','Hello','approved','2026-10-01T00:00:00+00:00');""")
        return con

    def test_unmigrated_table_cannot_grant_approval_but_can_revoke(self):
        con = self.unmigrated()
        with self.assertRaisesRegex(ValidationError, "approval_binding_unavailable.*Nothing changed by approve"):
            transition(con, "d1", "approve")
        with self.assertRaisesRegex(ValidationError, "approval_binding_unavailable"):
            transition(con, "a1", "schedule", FUTURE)
        self.assertEqual([r[0] for r in con.execute("SELECT status FROM posts ORDER BY id")], ["approved", "draft"])
        self.assertEqual(transition(con, "a1", "unapprove"), "draft")
        self.assertEqual(transition(con, "d1", "cancel"), "cancelled")

    def test_unmigrated_connection_fails_closed_in_claim(self):
        con = sqlite3.connect(":memory:", isolation_level=None)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        con.executescript("""
            CREATE TABLE posts(id TEXT PRIMARY KEY, account_id TEXT, platform TEXT, content TEXT,
              status TEXT, created_at TEXT, approved_at TEXT, scheduled_at TEXT, last_error TEXT,
              send_started_at TEXT);
            CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
              post_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO posts VALUES('p1','acct','bluesky','Hello','approved','2026-10-01T00:00:00+00:00',
              '2026-10-01T00:00:00+00:00',NULL,NULL,NULL);""")
        with self.assertRaisesRegex(ValidationError, "approval_binding_unavailable"):
            claim_sending(con, "p1", now=NOW)
        self.assertEqual(con.execute("SELECT status FROM posts").fetchone()[0], "approved")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], 0)


class MigrationTests(Base):
    OLD_POSTS = ("  send_started_at TEXT,\n  approved_payload_sha256 TEXT,\n  approved_by TEXT,\n  approval_channel TEXT,\n"
                 "  approved_payload_mac TEXT,\n  media_json TEXT,\n  video_json TEXT,\n  reply_target TEXT\n")

    def test_old_database_gains_nullable_columns_rows_unbound(self):
        self.assertIn(self.OLD_POSTS, SCHEMA, "posts DDL moved; update OLD_POSTS")
        old = SCHEMA.replace(self.OLD_POSTS, "  send_started_at TEXT\n")
        con = sqlite3.connect(self.db, isolation_level=None)
        con.executescript(old)
        con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at,approved_at)
                       VALUES('legacy','my-bluesky','bluesky','Approved long ago','approved',
                              '2026-09-01T00:00:00+00:00','2026-09-02T00:00:00+00:00')""")
        con.close()
        Path(self.db).chmod(0o600)
        with connect(self.db) as db:
            have = [row[1] for row in db.execute("PRAGMA table_info(posts)")]
            self.assertTrue(set(APPROVAL_BINDING_FIELDS) <= set(have))
            self.assertIn("reply_target", have)  # issue #17 draft column, NULL for every old row
            row = get_post(db, "legacy")
            self.assertIsNone(row["reply_target"])
            self.assertEqual((row["status"], row["content"], row["approved_payload_sha256"]),
                             ("approved", "Approved long ago", None))
            self.assertEqual(migrate(db), [])  # idempotent
            with self.assertRaisesRegex(ValidationError, "approval_hash_missing_reapproval_required"):
                claim_sending(db, "legacy", now=NOW)
        with connect(self.db) as db:  # reopening changes nothing
            self.assertEqual([row[1] for row in db.execute("PRAGMA table_info(posts)")].count("approved_by"), 1)


class ApprovalBindingCliTests(Base):
    def setUp(self):
        super().setUp()
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_BIND",
             "handle": "example.bsky.social"}]}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=env is not None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def draft(self):
        with connect(self.db) as db:
            return add_post(db, "my-bluesky", "bluesky", "A useful verified research note")

    def test_cli_approve_explicit_approver(self):
        pid = self.draft()
        code, out, _ = self.cli("approve", pid, "--approver", "mohsen")
        self.assertEqual(code, 0)
        self.assertIn(f"Post {pid} -> approved", out)
        self.assertIn("approver mohsen via cli_flag", out)
        with connect(self.db) as db:
            row = get_post(db, pid)
        self.assertIn(row["approved_payload_sha256"][:12], out)
        self.assertEqual((row["approved_by"], row["approval_channel"]), ("mohsen", "cli_flag"))

    def test_cli_approve_env_user(self):
        pid = self.draft()
        code, out, _ = self.cli("approve", pid, env={"USER": "owner"})
        self.assertEqual(code, 0)
        self.assertIn("via cli_env_user", out)
        other = self.draft()
        code, out, _ = self.cli("approve", other, env={})
        self.assertEqual(code, 0)
        self.assertIn("approver unattributed via cli_unattributed", out)

    def test_cli_approve_rejects_bad_approver(self):
        pid = self.draft()
        code, _, err = self.cli("approve", pid, "--approver", "two words")
        self.assertEqual(code, 2)
        self.assertIn("--approver", err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_cli_approve_prints_bound_fields(self):
        pid = self.draft()
        code, out, _ = self.cli("approve", pid, "--approver", "owner")
        self.assertEqual(code, 0)
        self.assertIn("Bound: account=my-bluesky platform=bluesky", out)

    def test_cli_show_reports_binding_state(self):
        pid = self.draft()
        code, out, _ = self.cli("show", pid)
        self.assertEqual(code, 0)
        shown = json.loads(out)["approval"]
        self.assertEqual(shown["binding"], "not_approved")
        self.assertEqual(len(shown["approve_would_bind_sha256"]), 64)
        self.cli("approve", pid, "--approver", "owner")
        shown = json.loads(self.cli("show", pid)[1])["approval"]
        self.assertEqual(shown["binding"], "bound")
        self.assertEqual(shown["approved_by"], "owner")
        with connect(self.db) as db:
            self.assertEqual(shown["payload_sha256"], get_post(db, pid)["approved_payload_sha256"])
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (pid,))
        shown = json.loads(self.cli("show", pid)[1])["approval"]
        self.assertEqual(shown["binding"], "approval_hash_missing_reapproval_required")
        self.assertIn(f"reachweaver unapprove {pid}", shown["reapprove"])

    def test_cli_dry_run_flags_unbound_post_without_writing(self):
        pid = self.draft()
        self.cli("approve", pid)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (pid,))
            before = db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn(f"BLOCKED approval_hash_missing_reapproval_required: post {pid}", out)
        self.assertIn("1 due post(s) would be BLOCKED until re-approved", out)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], before)

    def live_env(self):
        return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_TEST_BIND_IDENTIFIER": "name",
                "REACHWEAVER_TEST_BIND_APP_PASSWORD": "app-pw"}

    def test_cli_live_publish_blocks_legacy_unbound_before_identity_preflight(self):
        pid = self.draft()
        self.cli("approve", pid)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET approved_payload_sha256=NULL, approved_by=NULL, approval_channel=NULL "
                       "WHERE id=?", (pid,))
        with patch("reachweaver.cli.check_connection") as preflight, patch("reachweaver.cli.publish") as sent:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        self.assertEqual(code, 2)
        self.assertIn("approval_hash_missing_reapproval_required", out)
        preflight.assert_not_called()
        sent.assert_not_called()
        with connect(self.db) as db:
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]),
                             ("approved", "approval_hash_missing_reapproval_required"))
            self.assertEqual(events(db, "send_claimed"), [])
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)

    def test_cli_reapproval_path_then_live_send_claims(self):
        pid = self.draft()
        self.cli("approve", pid)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (pid,))
        self.assertEqual(self.cli("unapprove", pid)[0], 0)
        self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        receipt = ("at://did:plc:x/app.bsky.feed.post/1", None)
        with patch("reachweaver.cli.check_connection"), \
                patch("reachweaver.cli.publish", return_value=receipt) as sent:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        self.assertEqual(code, 0, out)
        sent.assert_called_once()
        self.assertEqual(sent.call_args.args[1], "A useful verified research note")
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "published")

    def test_cli_live_publish_refuses_row_rebound_during_preflight(self):
        # Between the pre-claim gate and the claim, the row is replaced by a self-consistent
        # (content + recomputed hash) payload. The loop still holds the old copy, so the claim's
        # expected_sha256 check must refuse rather than send a text that is no longer bound.
        pid = self.draft()
        self.cli("approve", pid)

        def swap(_account):
            with connect(self.db) as db:
                db.execute("UPDATE posts SET content='Replaced during preflight' WHERE id=?", (pid,))
                db.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?",
                           (approval_payload_sha256(get_post(db, pid)), pid))
        with patch("reachweaver.cli.check_connection", side_effect=swap), \
                patch("reachweaver.cli.publish") as sent:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        self.assertEqual(code, 2)
        self.assertIn("approval_hash_mismatch_reapproval_required", out)
        sent.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])

    def test_cli_schedule_refuses_unbound_and_tampered(self):
        for column, value in (("approved_payload_sha256", None), ("content", "Swapped text")):
            with self.subTest(column=column):
                pid = self.draft()
                self.cli("approve", pid)
                with connect(self.db) as db:
                    db.execute(f"UPDATE posts SET {column}=? WHERE id=?", (value, pid))
                code, _, err = self.cli("schedule", pid, "--at", "2026-12-01T09:00:00+00:00")
                self.assertEqual(code, 2)
                self.assertIn("Nothing scheduled", err)
                with connect(self.db) as db:
                    self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_cli_live_publish_blocks_tampered_post_before_network(self):
        pid = self.draft()
        self.assertEqual(self.cli("approve", pid)[0], 0)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET content='Swapped after approval' WHERE id=?", (pid,))
        env = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_TEST_BIND_IDENTIFIER": "name",
               "REACHWEAVER_TEST_BIND_APP_PASSWORD": "app-pw"}
        with patch("reachweaver.cli.check_connection"), patch("reachweaver.cli.publish") as sent:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=env)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", out)
        self.assertIn("approval_hash_mismatch_reapproval_required", out)
        sent.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")


class ApprovalBindingPostizTests(Base):
    def setUp(self):
        super().setUp()
        config = self.root / "accounts.json"
        config.write_text(json.dumps({"accounts": [
            {"id": "pz-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZB", "handle": "me.bsky.social",
             "backend": "postiz", "postiz_base_url": "https://api.postiz.com",
             "postiz_integration_id": "int-blue-1"}]}))
        self.account = load_accounts(config)["pz-blue"]
        self.integration = postiz.VerifiedIntegration("pz-blue", "bluesky", "int-blue-1", "bluesky")

    def send(self, db, post):
        out = io.StringIO()
        with patch("reachweaver.postiz.verify_integration", return_value=self.integration) as preflight, \
                patch("reachweaver.publishers.publish") as sent, contextlib.redirect_stdout(out):
            code = postiz.run_live_send(db, post, self.account, max_per_24h=2, min_gap_minutes=60, optout=[])
        return code, out.getvalue(), preflight, sent

    def pz_approved(self, db):
        pid = add_post(db, "pz-blue", "bluesky", "Release notes https://example.org/r")
        # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
        transition(db, pid, "approve", send_options=send_options.fingerprint(self.account))
        return pid

    def test_postiz_run_live_send_blocks_tampered_post(self):
        with connect(self.db) as db:
            pid = self.pz_approved(db)
            db.execute("UPDATE posts SET content='Release notes https://attacker.example/r' WHERE id=?", (pid,))
            code, out, preflight, sent = self.send(db, get_post(db, pid))
            self.assertEqual(code, 2)
            self.assertIn("approval_hash_mismatch", out)
            preflight.assert_not_called()  # refused before the identity preflight, too
            sent.assert_not_called()
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])

    def test_postiz_run_live_send_blocks_legacy_unbound_post(self):
        with connect(self.db) as db:
            pid = self.pz_approved(db)
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (pid,))
            code, out, preflight, sent = self.send(db, get_post(db, pid))
            self.assertEqual(code, 2)
            self.assertIn("BLOCKED", out)
            self.assertIn("approval_hash_missing_reapproval_required", out)
            preflight.assert_not_called()
            sent.assert_not_called()
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)

    def test_postiz_claim_refuses_stale_copy_that_differs_from_bound_row(self):
        # The pre-gate passes on the self-consistent DB row, but the copy about to be sent
        # differs: claim_sending's expected_sha256 check refuses it under the lock.
        with connect(self.db) as db:
            pid = self.pz_approved(db)
            stale = dict(get_post(db, pid))
            stale["content"] = "Release notes https://attacker.example/r"
            code, out, _, sent = self.send(db, stale)
            self.assertEqual(code, 2)
            self.assertIn("approval_hash_mismatch", out)
            sent.assert_not_called()
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])

    def test_postiz_bound_post_reaches_the_backend(self):
        with connect(self.db) as db:
            pid = self.pz_approved(db)
            code, _, preflight, sent = self.send(db, get_post(db, pid))
            self.assertEqual(code, 3)  # the mock returns: treated as uncertain, never retried
            preflight.assert_called_once()
            sent.assert_called_once()
            self.assertEqual(len(events(db, "send_claimed")), 1)


class ApprovalBindingCalendarTests(Base):
    KW = dict(account_id="my-bluesky", platform="bluesky", audience_timezone="Europe/Rome",
              start="2026-10-12", days=14, now=datetime(2026, 10, 8, 12, tzinfo=timezone.utc))

    def test_calendar_apply_refuses_legacy_unbound_and_rolls_back(self):
        with connect(self.db) as db:
            good, old = self.approved(db), self.legacy(db)
            plan = propose_calendar(db, post_ids=[good, old], **self.KW)
            with self.assertRaisesRegex(ValidationError, "approval_hash_missing.*Nothing scheduled"):
                apply_calendar(db, plan, now=self.KW["now"])
            for pid in (good, old):
                row = get_post(db, pid)
                self.assertEqual((row["status"], row["scheduled_at"]), ("approved", None))
            self.assertEqual(events(db, "schedule"), [])


class ApprovalBindingHealthTests(Base):
    ACCOUNTS = {"my-bluesky": {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_HB",
                               "handle": "example.bsky.social"}}
    ENV = {"REACHWEAVER_HB_IDENTIFIER": "example.bsky.social", "REACHWEAVER_HB_APP_PASSWORD": "pw"}

    def test_health_lists_posts_needing_reapproval_as_attention(self):
        with connect(self.db) as db:
            bound = self.approved(db)
            legacy = self.legacy(db, scheduled_at=FUTURE)
            tampered = self.approved(db, content="Original approved text")
            db.execute("UPDATE posts SET content='Tampered text' WHERE id=?", (tampered,))
            orphan = add_post(db, "gone-account", "bluesky", "Approved for an account no longer configured")
            transition(db, orphan, "approve")
            db.execute("UPDATE posts SET approved_payload_sha256=NULL WHERE id=?", (orphan,))
            before = db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
            queue = queue_health(db, now=NOW)
            report = build_health(db, self.ACCOUNTS, now=NOW, env=self.ENV)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], before)  # read-only
        listed = {p["post_id"]: p["reason"] for p in queue["approval_rebind_required"]}
        self.assertEqual(listed, {legacy: "approval_hash_missing_reapproval_required",
                                  tampered: "approval_hash_mismatch_reapproval_required",
                                  orphan: "approval_hash_missing_reapproval_required"})
        self.assertNotIn(bound, listed)
        self.assertTrue(queue["attention_required"])
        self.assertEqual(report["overall"], "attention")
        self.assertEqual(exit_code(report), 0)  # attention, not blocked
        self.assertEqual(report["approval_rebind_required"]["total"], 3)
        self.assertIn("reachweaver unapprove POST_ID", " ".join(report["approval_rebind_required"]["steps"]))
        self.assertEqual(report["accounts"][0]["approval_rebind_required"], 2)
        reasons = [r for r in report["reasons"] if r["code"] == "approval_rebind_required"]
        self.assertEqual([r["level"] for r in reasons], ["attention", "attention"])
        self.assertIn(orphan, reasons[0]["detail"] + reasons[1]["detail"])
        text = render_health(report)
        self.assertIn("Re-approval required: 3 post(s)", text)
        self.assertIn(f"{legacy} bluesky my-bluesky scheduled: approval_hash_missing_reapproval_required", text)
        self.assertNotIn("Tampered text", json.dumps(report))  # ids and reason codes only, never content

    def test_health_ok_when_every_approval_is_bound(self):
        with connect(self.db) as db:
            self.approved(db)
            report = build_health(db, self.ACCOUNTS, now=NOW, env=self.ENV)
        self.assertEqual(report["approval_rebind_required"]["total"], 0)
        self.assertNotIn("approval_rebind_required", {r["code"] for r in report["reasons"]})


if __name__ == "__main__":
    unittest.main()
