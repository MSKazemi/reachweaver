"""Wave 9 item 3 (owner-approved security-reviewed change): the send gate inside publishers.publish().

docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md section 3. Offline: every transport and identity check is a
mock, the database is a temp file, nothing contacts a platform. A direct Python call of
``publish()`` / ``publish_video()`` must be refused (fixed ``send_*`` code, before any capability,
credential or network step) unless ``storage.claim_sending`` committed a claim of exactly that post
under live caps and the live env gate holds; the CLI path is unchanged.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from gate_support import LIVE_ENV, issued_connection
from reachweaver import publishers, sendgate
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, get_post, set_paused, transition

TEXT = "A useful verified research note"
ACCOUNT = {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_GATE",
           "handle": "example.bsky.social"}
RECEIPT = ("at://did:plc:x/app.bsky.feed.post/1", None)


class GateBase(unittest.TestCase):
    def setUp(self):
        sendgate.reset()
        self.addCleanup(sendgate.reset)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")

    def approved(self, db, content=TEXT):
        pid = add_post(db, "my-bluesky", "bluesky", content)
        transition(db, pid, "approve")
        return pid

    def direct(self, content, pid, *, verified=None, env=LIVE_ENV, account=ACCOUNT, **kw):
        """Call the public function directly, with the network and identity check mocked."""
        with patch.dict(os.environ, env), \
                patch("reachweaver.publishers._send_http") as http, \
                patch("reachweaver.connections.check_connection") as check, \
                patch("reachweaver.publishers.publish_bluesky", return_value=RECEIPT) as adapter:
            try:
                result = publishers.publish(account, content, pid, verified_connection=verified, **kw)
            except ValidationError as exc:
                result = exc
        return result, http, check, adapter


class DirectCallRefusedTests(GateBase):
    def assertRefused(self, outcome, code):
        result, http, check, adapter = outcome
        self.assertIsInstance(result, ValidationError)
        self.assertTrue(str(result).startswith(code + ":"), str(result))
        http.assert_not_called()
        check.assert_not_called()
        adapter.assert_not_called()

    def test_no_claim_is_refused_with_no_network_attempt(self):
        outcome = self.direct(TEXT, "never-claimed")
        self.assertRefused(outcome, "send_ticket_required")
        self.assertNotIn(TEXT, str(outcome[0]))  # sanitized: fixed code, never the content

    def test_forged_verified_connection_alone_does_not_open_the_gate(self):
        forged = VerifiedConnection("my-bluesky", "bluesky", "verified", "verified")
        self.assertRefused(self.direct(TEXT, "p-forged", verified=forged), "send_ticket_required")

    def test_pause_blocks_the_claim_so_no_ticket_exists(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            set_paused(db, True)
            with self.assertRaisesRegex(ValidationError, "Global emergency pause is active"):
                claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60)
            self.assertEqual(get_post(db, pid)["status"], "approved")
        self.assertRefused(self.direct(TEXT, pid), "send_ticket_required")

    def test_unbound_or_tampered_row_gets_no_ticket(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("UPDATE posts SET content='Swapped after approval' WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60)
        self.assertRefused(self.direct("Swapped after approval", pid), "send_ticket_required")

    def test_claim_without_live_caps_is_refused(self):
        with connect(self.db) as db:  # legacy direct callers may claim without caps; the gate does not
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid))
        self.assertRefused(self.direct(TEXT, pid), "send_caps_required")
        with connect(self.db) as db:
            pid = self.approved(db, "Another useful verified research note")
            self.assertTrue(claim_sending(db, pid, max_per_24h=2, min_gap_minutes=0))
        self.assertRefused(self.direct("Another useful verified research note", pid), "send_caps_required")

    def test_live_env_gate_must_hold(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60))
        for env in ({}, {"REACHWEAVER_ALLOW_LIVE": "1"}, {"REACHWEAVER_ENV": "prod"},
                    {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "staging"}):
            with self.subTest(env=env):
                self.assertRefused(self.direct(TEXT, pid, env=env), "send_live_gate_closed")

    def test_other_content_account_or_media_is_refused_and_does_not_burn_the_claim(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60))
        self.assertRefused(self.direct("Different text than approved", pid), "send_ticket_mismatch")
        self.assertRefused(self.direct(TEXT, pid, account={**ACCOUNT, "id": "other"}), "send_ticket_mismatch")
        self.assertRefused(self.direct(TEXT, pid, media=[{"sha256": "0" * 64}]), "send_ticket_mismatch")
        result, _, _, adapter = self.direct(TEXT, pid, verified=issued_connection(
            "my-bluesky", "bluesky", "verified", "unverified"))
        self.assertEqual(result, RECEIPT)  # the legitimate call still works
        adapter.assert_called_once()

    def test_expired_claim_is_refused(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60))
        with patch("reachweaver.sendgate.time.monotonic", return_value=10 ** 9):
            self.assertRefused(self.direct(TEXT, pid), "send_ticket_expired")


class ClaimedSendTests(GateBase):
    def claimed(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            self.assertTrue(claim_sending(db, pid, max_per_24h=2, min_gap_minutes=60))
        return pid

    def test_claim_then_publish_sends_exactly_once(self):
        pid = self.claimed()
        good = issued_connection("my-bluesky", "bluesky", "verified", "unverified")
        result, http, check, adapter = self.direct(TEXT, pid, verified=good)
        self.assertEqual(result, RECEIPT)
        adapter.assert_called_once()
        check.assert_not_called()  # a connection that check_connection issued is not re-checked
        again, http2, _, adapter2 = self.direct(TEXT, pid, verified=good)
        self.assertIsInstance(again, ValidationError)
        self.assertTrue(str(again).startswith("send_ticket_consumed:"))
        adapter2.assert_not_called()
        http2.assert_not_called()

    def test_hand_built_connection_with_a_claim_is_rechecked(self):
        pid = self.claimed()
        forged = VerifiedConnection("my-bluesky", "bluesky", "verified", "verified")
        result, _, check, adapter = self.direct(TEXT, pid, verified=forged)
        self.assertEqual(result, RECEIPT)
        check.assert_called_once()  # the forged object bought nothing: identity is verified again
        adapter.assert_called_once()

    def test_no_connection_with_a_claim_runs_the_check(self):
        pid = self.claimed()
        _, _, check, _ = self.direct(TEXT, pid)
        check.assert_called_once()

    def test_a_new_claim_is_needed_per_send(self):
        pid = self.claimed()
        self.direct(TEXT, pid)
        with connect(self.db) as db:
            second = self.approved(db, "Second useful verified research note")
            later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            self.assertTrue(claim_sending(db, second, now=later, max_per_24h=2, min_gap_minutes=60))
        outcome = self.direct(TEXT, second)  # post 2's claim does not authorise post 1's text
        self.assertTrue(str(outcome[0]).startswith("send_ticket_mismatch:"))


class VideoEntryPointTests(GateBase):
    def test_publish_video_is_gated_first(self):
        account = {"id": "yt", "platform": "youtube", "env_prefix": "REACHWEAVER_TEST_YT"}
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.youtube.upload_video") as upload, \
                patch("reachweaver.connections.check_connection") as check:
            with self.assertRaisesRegex(ValidationError, "^send_ticket_required"):
                publishers.publish_video(account, "Description", "v1", {"platform": "youtube"})
        upload.assert_not_called()
        check.assert_not_called()


class StructuralTests(unittest.TestCase):
    ENTRY_POINTS = ("publish", "publish_video")
    # Per-platform adapters, reached only through the gated entry points after a claim was consumed.
    ADAPTERS = {"publish_bluesky", "publish_mastodon", "publish_telegram", "publish_linkedin",
                "publish_instagram", "publish_threads", "publish_facebook"}

    def test_every_dispatch_entry_point_consumes_a_claim_before_anything_else(self):
        tree = ast.parse(inspect.getsource(publishers))
        found = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("publish")}
        self.assertEqual(set(found) - self.ADAPTERS, set(self.ENTRY_POINTS),
                         "a new public publish* function must either be gated or be listed as an adapter here")
        for name in self.ENTRY_POINTS:
            body = [s for s in found[name].body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            self.assertEqual(ast.unparse(body[0]), "from . import sendgate", name)
            self.assertIn("sendgate.consume(", ast.unparse(body[1]), name)

    def test_only_claim_sending_registers_claims(self):
        root = Path(publishers.__file__).parent
        users = sorted(p.name for p in root.glob("*.py") if "register_claim(" in p.read_text() and p.name != "sendgate.py")
        self.assertEqual(users, ["storage.py"])

    def test_refusal_codes_are_fixed_strings(self):
        for code, text in sendgate._CODES.items():
            self.assertRegex(code, r"^send_[a-z_]+$")
            self.assertNotRegex(text, r"[{}%]")


class CliPathUnchangedTests(GateBase):
    def setUp(self):
        super().setUp()
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_GATE",
             "handle": "example.bsky.social"}]}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=env is not None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live_env(self):
        return {**LIVE_ENV, "REACHWEAVER_TEST_GATE_IDENTIFIER": "name", "REACHWEAVER_TEST_GATE_APP_PASSWORD": "app-pw"}

    def test_cli_live_publish_still_sends_once_through_the_real_gate(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", TEXT)
        self.assertEqual(self.cli("approve", pid)[0], 0)
        verified = issued_connection("my-bluesky", "bluesky", "verified", "unverified")
        with patch("reachweaver.cli.check_connection", return_value=verified), \
                patch("reachweaver.connections.check_connection") as recheck, \
                patch("reachweaver.publishers._send_http") as http, \
                patch("reachweaver.publishers.publish_bluesky", return_value=RECEIPT) as adapter:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        self.assertEqual(code, 0, out)
        adapter.assert_called_once()
        recheck.assert_not_called()
        http.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "published")

    def test_cli_paused_run_sends_nothing(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", TEXT)
        self.cli("approve", pid)
        with connect(self.db) as db:
            set_paused(db, True)
        with patch("reachweaver.publishers.publish_bluesky") as adapter:
            code, _, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env())
        self.assertNotEqual(code, 0)
        adapter.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
