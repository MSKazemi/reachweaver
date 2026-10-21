"""AI-content label precondition for posts with media (owner-approved 2026-10-21, decisions D1-D4 and D6).

Offline and synthetic: every image and video is generated here, no request is made (socket connect is patched to
fail), nothing is sent. Covers the approve refusal and acceptance, the binding (approval/media-3), send-time
re-checks on bound values, an upgraded database, the audit record of the answer, text-only posts and health.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_instagram import record as ig_record  # noqa: E402
from test_mastodon_media import png  # noqa: E402
from test_media import av_mp4  # noqa: E402

from reachweaver import ai_label_gate, ai_labels, attachments, tiktok  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.health import build_health  # noqa: E402
from reachweaver.policy import ValidationError  # noqa: E402
from reachweaver.storage import (SCHEMA, add_post, approval_binding_problem, approval_payload,  # noqa: E402
                                 approval_payload_sha256, approval_rebind_required, check_approval_binding,
                                 claim_sending, connect, get_post, migrate, set_post_media, set_post_video,
                                 transition)

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-22T10:00:00+00:00"
UNANSWERED = "ai_label_unanswered"
MISMATCH = "approval_hash_mismatch_reapproval_required"
NEW_COLUMNS = ("  authorship TEXT,  -- 2026-10-21 AI-label precondition (same placement rule): see "
               "reachweaver/ai_label_gate.py\n",
               "  realistic_synthetic_json TEXT,  -- same: owner answers per attachment sha256 (ai-declare)\n",
               "  approved_ai_label TEXT,  -- same: applied|not_needed, written by approve --ai-label only\n")


def _no_network(*_args, **_kwargs):
    raise AssertionError("network access attempted")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state" / "outbox.sqlite")
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(av_mp4(mdat_size=4096))
        guard = patch.object(socket.socket, "connect", _no_network)
        guard.start()
        self.addCleanup(guard.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--plain", "--db", self.db, "--accounts", str(ROOT / "config" / "accounts.example.json"),
                         *argv])
        return code, out.getvalue(), err.getvalue()

    def ig_post(self, db):
        pid = add_post(db, "ig-me", "instagram", "A chart of the week")
        set_post_media(db, pid, [ig_record()])
        return pid

    def masto_post(self, db):
        pid = add_post(db, "my-mastodon", "mastodon", "A chart of the results")
        set_post_media(db, pid, [attachments.build_attachment(self.image, "A described chart")])
        return pid

    def tiktok_post(self, db):
        pid = add_post(db, "tt-me", "tiktok", "Demo clip")
        set_post_video(db, pid, tiktok.build_video_record(self.video))
        return pid

    def events(self, db, action):
        return [r[0] for r in db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id", (action,))]


class ApproveTests(Base):
    def test_media_post_with_unknown_decision_is_refused_without_an_answer(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)  # no provenance, no realism answer: unknown
            with self.assertRaisesRegex(ValidationError, UNANSWERED) as caught:
                transition(db, pid, "approve")
            self.assertIn("Nothing approved", str(caught.exception))
            self.assertIn("--ai-label applied", str(caught.exception))
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_required_decision_is_refused_even_with_a_declaration(self):
        with connect(self.db) as db:
            pid = self.tiktok_post(db)
            ai_label_gate.declare(db, pid, realistic="yes")
            self.assertEqual(ai_label_gate.decision(get_post(db, pid))["platform_decision"], "required")
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                transition(db, pid, "approve")
            self.assertEqual(transition(db, pid, "approve", ai_label="applied"), "approved")
            row = get_post(db, pid)
            self.assertEqual(row["approved_ai_label"], "applied")
            self.assertIsNone(approval_binding_problem(row))
            self.assertIsNone(ai_label_gate.problem(row))

    def test_answer_accepts_and_is_recorded_in_the_approve_event(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            self.assertEqual(transition(db, pid, "approve", ai_label="not_needed"), "approved")
            detail = self.events(db, "approve")[-1]
            self.assertIn("ai_label_decision=unknown ai_label=not_needed", detail)

    def test_a_declaration_that_settles_it_needs_no_answer(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            ai_label_gate.declare(db, pid, realistic="no")
            self.assertEqual(transition(db, pid, "approve"), "approved")
            row = get_post(db, pid)
            self.assertIsNone(row["approved_ai_label"])
            self.assertIn("ai_label_decision=not_required ai_label=none", self.events(db, "approve")[-1])
            payload = approval_payload(row)
            self.assertEqual(payload["ai_label_binding"], "reachweaver.storage.approval/media-3")
            self.assertEqual(payload["binding"], "reachweaver.storage.approval/media-1")  # base tag unchanged
            self.assertIsNone(ai_label_gate.problem(row))

    def test_platform_without_a_recorded_rule_always_needs_an_answer(self):
        with connect(self.db) as db:
            pid = self.masto_post(db)
            ai_label_gate.declare(db, pid, realistic="no")
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                transition(db, pid, "approve")
            self.assertEqual(transition(db, pid, "approve", ai_label="not_needed"), "approved")

    def test_invalid_answer_and_answer_on_text_only_post_are_refused(self):
        with connect(self.db) as db:
            media = self.ig_post(db)
            with self.assertRaisesRegex(ValidationError, "--ai-label must be one of"):
                transition(db, media, "approve", ai_label="yes")
            text = add_post(db, "my-bluesky", "bluesky", "Plain text")
            with self.assertRaisesRegex(ValidationError, "ai_label_text_only"):
                transition(db, text, "approve", ai_label="applied")
            with self.assertRaisesRegex(ValidationError, "only applies to approve"):
                transition(db, media, "cancel", ai_label="applied")
            self.assertEqual(get_post(db, text)["status"], "draft")

    def test_unapprove_clears_the_answer_and_schedule_keeps_it(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            transition(db, pid, "approve", ai_label="applied")
            transition(db, pid, "schedule", "2026-10-30T09:00:00+00:00")
            row = get_post(db, pid)
            self.assertEqual(row["approved_ai_label"], "applied")
            self.assertIsNone(approval_binding_problem(row))
            transition(db, pid, "unapprove")
            self.assertIsNone(get_post(db, pid)["approved_ai_label"])
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                transition(db, pid, "approve")


class BindingTests(Base):
    def test_changing_a_bound_value_after_approval_is_a_hash_mismatch(self):
        edits = {
            "answer": "UPDATE posts SET approved_ai_label='applied' WHERE id=?",
            "answer_removed": "UPDATE posts SET approved_ai_label=NULL WHERE id=?",
            "authorship": "UPDATE posts SET authorship='ai_generated' WHERE id=?",
            "realism": "UPDATE posts SET realistic_synthetic_json=NULL WHERE id=?",
        }
        for name, sql in edits.items():
            with self.subTest(edit=name), connect(self.db) as db:
                pid = self.ig_post(db)
                ai_label_gate.declare(db, pid, realistic="no")
                transition(db, pid, "approve", ai_label="not_needed")
                db.execute(sql, (pid,))  # behind the approval's back
                row = get_post(db, pid)
                self.assertEqual(approval_binding_problem(row), MISMATCH)
                with self.assertRaisesRegex(ValidationError, MISMATCH):
                    claim_sending(db, pid, now=NOW)
                self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_media_post_with_no_declaration_keeps_its_payload(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            payload = approval_payload(get_post(db, pid))
            self.assertNotIn("ai_label_binding", payload)
            self.assertNotIn("ai_label_declarations", payload)

    def test_text_only_payload_never_carries_the_declarations(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "We shipped version three")
            before = approval_payload_sha256(get_post(db, pid))
            ai_label_gate.declare(db, pid, authorship="ai_generated")
            row = get_post(db, pid)
            self.assertEqual(approval_payload_sha256(row), before)
            self.assertNotIn("ai_label_binding", approval_payload(row))
            self.assertEqual(transition(db, pid, "approve"), "approved")  # text-only: never blocked
            self.assertIsNone(ai_label_gate.problem(get_post(db, pid)))

    def test_declarations_only_change_on_a_draft(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            transition(db, pid, "approve", ai_label="applied")
            with self.assertRaisesRegex(ValidationError, "only change on a draft"):
                ai_label_gate.declare(db, pid, realistic="no")

    def test_declaration_is_keyed_by_the_attachment_hash(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            ai_label_gate.declare(db, pid, realistic="no")
            set_post_media(db, pid, [ig_record(sha256="a" * 64)])  # a different file: no answer for it
            self.assertEqual(ai_label_gate.decision(get_post(db, pid))["platform_decision"], "unknown")
            with self.assertRaisesRegex(ValidationError, "--attachment must be 1..1"):
                ai_label_gate.declare(db, pid, realistic="yes", attachment=2)
            ai_label_gate.declare(db, pid, realistic="no", attachment=1)
            self.assertEqual(ai_label_gate.decode_realism(get_post(db, pid)["realistic_synthetic_json"]),
                             {"a" * 64: False})  # the stale answer was dropped


class SendTimeTests(Base):
    def approve_as_before_the_change(self, db, pid):
        """An approval made by code without the precondition (the previous release)."""
        with patch.object(ai_label_gate, "approve_check", return_value=(None, None)):
            transition(db, pid, "approve")

    def test_missing_bound_answer_refuses_claim_schedule_and_is_listed(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            self.approve_as_before_the_change(db, pid)
            row = get_post(db, pid)
            self.assertIsNone(approval_binding_problem(row))  # the hash itself still verifies
            self.assertEqual(ai_label_gate.problem(row), UNANSWERED)
            self.assertEqual(check_approval_binding(db, pid, now=NOW), UNANSWERED)
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                claim_sending(db, pid, now=NOW)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]), ("approved", UNANSWERED))
            self.assertIn(f"reason={UNANSWERED}", self.events(db, "approval_binding_failed")[-1])
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                transition(db, pid, "schedule", "2026-10-30T09:00:00+00:00")
            listed = {item["post_id"]: item["reason"] for item in approval_rebind_required(db)}
            self.assertEqual(listed, {pid: UNANSWERED})
        code, out, _ = self.cli("publish")  # dry run
        self.assertEqual(code, 0)
        self.assertIn(f"BLOCKED {UNANSWERED}", out)
        self.assertIn("--ai-label applied|not_needed", out)

    def test_bound_answer_passes_the_send_time_check(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            transition(db, pid, "approve", ai_label="not_needed")
            self.assertIsNone(check_approval_binding(db, pid, now=NOW))
            self.assertTrue(claim_sending(db, pid, now=NOW))

    def test_registry_change_after_approval_fails_closed_without_rebinding(self):
        with connect(self.db) as db:
            pid = self.ig_post(db)
            ai_label_gate.declare(db, pid, realistic="no")
            transition(db, pid, "approve")  # not_required: no answer needed or bound
            registry = ai_labels.load_registry()
            registry["platforms"]["instagram"]["non_realistic_media"] = "required"
            with patch.object(ai_labels, "load_registry", return_value=registry):
                row = get_post(db, pid)
                self.assertIsNone(approval_binding_problem(row))  # the registry is not bound
                self.assertEqual(ai_label_gate.problem(row), UNANSWERED)

    def test_unreadable_registry_fails_closed_for_media_only(self):
        with connect(self.db) as db:
            media = self.ig_post(db)
            ai_label_gate.declare(db, media, realistic="no")
            text = add_post(db, "my-bluesky", "bluesky", "Plain text")
            with patch.object(ai_labels, "load_registry", side_effect=ValidationError("registry is invalid")):
                with self.assertRaisesRegex(ValidationError, UNANSWERED):
                    transition(db, media, "approve")
                self.assertEqual(transition(db, media, "approve", ai_label="applied"), "approved")
                self.assertEqual(transition(db, text, "approve"), "approved")


class UpgradeTests(Base):
    def old_database(self):
        """A database exactly as before this change, with an approved media post and an approved text post."""
        old = SCHEMA
        for line in NEW_COLUMNS:
            self.assertIn(line, old, "posts DDL moved; update this fixture")
            old = old.replace(line, "")
        Path(self.db).parent.mkdir(mode=0o700, exist_ok=True)
        con = sqlite3.connect(self.db, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.executescript(old)
            media = self.ig_post(con)
            text = add_post(con, "my-bluesky", "bluesky", "Plain text before the upgrade")
            with patch.object(ai_label_gate, "approve_check", return_value=(None, None)):  # the old approve
                transition(con, media, "approve")
            transition(con, text, "approve")
            hashes = {pid: con.execute("SELECT approved_payload_sha256 FROM posts WHERE id=?", (pid,)).fetchone()[0]
                      for pid in (media, text)}
        finally:
            con.close()
        os.chmod(self.db, 0o600)
        return media, text, hashes

    def test_old_media_approval_needs_one_reapproval_and_text_is_untouched(self):
        media, text, hashes = self.old_database()
        with connect(self.db) as db:
            self.assertEqual(migrate(db), [])  # connect() already added the columns
            for pid in (media, text):
                row = get_post(db, pid)
                self.assertIsNone(row["approved_ai_label"])
                self.assertEqual(row["approved_payload_sha256"], hashes[pid])
                self.assertIsNone(approval_binding_problem(row))  # golden: no new key in the payload
            self.assertEqual(approval_rebind_required(db), [{"post_id": media, "account_id": "ig-me",
                                                             "platform": "instagram", "status": "approved",
                                                             "reason": UNANSWERED}])
            with self.assertRaisesRegex(ValidationError, UNANSWERED):
                claim_sending(db, media, now=NOW)
            self.assertTrue(claim_sending(db, text, now=NOW))
            transition(db, media, "unapprove")
            transition(db, media, "approve", ai_label="not_needed")
            self.assertTrue(claim_sending(db, media, now=NOW))

    def test_migrate_adds_the_columns_once(self):
        Path(self.db).parent.mkdir(mode=0o700, exist_ok=True)
        con = sqlite3.connect(self.db, isolation_level=None)
        try:
            old = SCHEMA
            for line in NEW_COLUMNS:
                old = old.replace(line, "")
            con.executescript(old)
            self.assertEqual(migrate(con), ["authorship", "realistic_synthetic_json", "approved_ai_label"])
            self.assertEqual(migrate(con), [])
        finally:
            con.close()


class CliTests(Base):
    def test_approve_refuses_then_accepts_and_prints_the_binding(self):
        with connect(self.db) as db:
            pid = self.masto_post(db)
        code, _, err = self.cli("approve", pid)
        self.assertEqual(code, 2)
        self.assertIn(f"Refusing to approve {pid}: {UNANSWERED}", err)
        code, out, err = self.cli("approve", pid, "--ai-label", "not_needed")
        self.assertEqual(code, 0, err)
        self.assertIn("AI label unknown, --ai-label not_needed bound (approval/media-3)", out)
        code, out, _ = self.cli("show", pid)
        shown = json.loads(out)
        self.assertEqual(shown["approval"]["binding"], "bound")
        self.assertEqual(shown["approval"]["ai_label"], "bound (approval/media-3): --ai-label not_needed")
        self.assertIn("answered: --ai-label not_needed", shown["ai_label"]["approval"])

    def test_ai_declare_check_and_show_print_the_advice(self):
        with connect(self.db) as db:
            pid = self.masto_post(db)
        code, out, err = self.cli("ai-declare", pid, "--realistic-synthetic", "yes", "--authorship", "ai_assisted")
        self.assertEqual(code, 0, err)
        self.assertIn("authorship=ai_assisted; realistic_synthetic image 1=yes", out)
        self.assertIn("approve needs --ai-label applied|not_needed", out)
        code, out, _ = self.cli("check", pid)
        self.assertEqual(code, 0, out)  # advice, not a check failure
        self.assertIn("AI label (mastodon): unknown", out)
        self.assertIn("EU:", out)  # D4: the Art. 50 hint is printed, advisory only
        self.assertIn("never blocks", out)
        code, out, _ = self.cli("check", pid, "--json")
        self.assertEqual(json.loads(out)["ai_label"]["decision"], "unknown")
        code, out, _ = self.cli("ai-label-check", "--post-id", pid, "--json")
        target = json.loads(out)["targets"][0]
        self.assertEqual(target["authorship"], "ai_assisted")  # the stored declaration is used
        code, out, err = self.cli("ai-declare", pid, "--json", "--realistic-synthetic", "clear")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["realistic_synthetic"], {})
        code, _, err = self.cli("ai-declare", pid)
        self.assertEqual(code, 2)
        self.assertIn("nothing to declare", err)

    def test_text_only_post_check_is_quiet_and_approve_unchanged(self):
        code, out, _ = self.cli("draft", "--account", "my-bluesky", "--text", "Plain words, written by me")
        pid = out.split()[2]
        code, out, _ = self.cli("check", pid)
        self.assertEqual(code, 0)
        self.assertNotIn("AI label", out)
        code, _, err = self.cli("approve", pid, "--ai-label", "applied")
        self.assertEqual(code, 2)
        self.assertIn("ai_label_text_only", err)
        code, out, err = self.cli("approve", pid)
        self.assertEqual(code, 0, err)
        self.assertNotIn("AI label", out)


class HealthTests(Base):
    def test_registry_age_warns_after_ninety_days_and_never_blocks(self):
        age = ai_label_gate.registry_age(date(2026, 10, 22))
        self.assertEqual(age["status"], "ok")
        self.assertEqual(age["warn_after_days"], 90)
        late = ai_label_gate.registry_age(date(2027, 3, 1))
        self.assertEqual(late["status"], "aging")
        self.assertGreater(late["age_days"], 90)
        with connect(self.db) as db:
            report = build_health(db, {}, now="2027-03-01T00:00:00+00:00", env={})
        codes = {r["code"]: r["level"] for r in report["reasons"]}
        self.assertEqual(codes["ai_disclosure_registry_aging"], "attention")
        self.assertEqual(report["global"]["ai_disclosure_registry"]["status"], "aging")
        with connect(self.db) as db:
            fresh = build_health(db, {}, now="2026-10-22T00:00:00+00:00", env={})
        self.assertNotIn("ai_disclosure_registry_aging", {r["code"] for r in fresh["reasons"]})


if __name__ == "__main__":
    unittest.main()
