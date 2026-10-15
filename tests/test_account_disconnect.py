"""Local account disconnect and human-only reconnect (public issue #38). Offline; the provider is mocked.

Draft for owner security review (docs/SECURITY-REVIEW-PROPOSAL-38-account-disconnect.md).
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

from reachweaver import disconnect
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.confirmation import ConfirmationError
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, get_post, transition
from reachweaver.worker_log import SKIP_REASONS

SECRET_VALUE = "value-that-must-never-be-printed"
ENV = {"REACHWEAVER_DC_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_DC_BSKY_APP_PASSWORD": SECRET_VALUE,
       "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}


def approved(db, text="a note", account="my-bluesky", platform="bluesky"):
    pid = add_post(db, account, platform, text, "launch", "https://example.org/r")
    transition(db, pid, "approve")
    return pid


class DisconnectStorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "outbox.sqlite")

    def test_disconnect_records_once_and_keeps_the_first_time(self):
        with connect(self.db) as db:
            self.assertTrue(disconnect.disconnect(db, "my-bluesky", "2026-10-15T10:00:00+00:00"))
            self.assertFalse(disconnect.disconnect(db, "my-bluesky", "2026-10-16T10:00:00+00:00"))
            self.assertEqual(disconnect.disconnected_since(db, "my-bluesky"), "2026-10-15T10:00:00+00:00")
            self.assertIsNone(disconnect.disconnected_since(db, "my-mastodon"))
            events = db.execute("SELECT action FROM event_log WHERE action LIKE 'account_%'").fetchall()
            self.assertEqual([e[0] for e in events], ["account_disconnected"])

    def test_invalid_account_ids_are_refused(self):
        with connect(self.db) as db:
            for bad in ("", None, "x" * 81, 5):
                with self.subTest(value=bad), self.assertRaises(ValidationError):
                    disconnect.disconnect(db, bad)  # type: ignore[arg-type]

    def test_a_disconnected_account_cannot_be_claimed_and_others_can(self):
        with connect(self.db) as db:
            mine = approved(db)
            other = approved(db, "other", account="my-mastodon", platform="mastodon")
            disconnect.disconnect(db, "my-bluesky")
            with self.assertRaises(ValidationError) as caught:
                claim_sending(db, mine)
            self.assertIn(disconnect.DISCONNECTED, str(caught.exception))
            self.assertIn("reachweaver reconnect --account my-bluesky", str(caught.exception))
            self.assertEqual(get_post(db, mine)["status"], "approved")
            claimed = db.execute("SELECT count(*) FROM event_log WHERE action='send_claimed'").fetchone()[0]
            self.assertEqual(claimed, 0)
            self.assertTrue(claim_sending(db, other))

    def test_an_unreadable_stored_value_still_counts_as_disconnected(self):
        with connect(self.db) as db:
            pid = approved(db)
            db.execute("INSERT INTO settings(name,value) VALUES('account_disconnected:my-bluesky','???')")
            with self.assertRaises(ValidationError):
                claim_sending(db, pid)

    def test_reconnect_needs_the_ritual_and_a_failed_ritual_changes_nothing(self):
        with connect(self.db) as db:
            disconnect.disconnect(db, "my-bluesky")
            seen = []

            def refuse(challenge, **kwargs):
                seen.append((challenge, kwargs["prompt"]))
                raise ConfirmationError("human_confirmation_failed: no")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ConfirmationError):
                disconnect.reconnect(db, "my-bluesky", confirm=refuse)
            self.assertIsNotNone(disconnect.disconnected_since(db, "my-bluesky"))
            self.assertEqual(len(seen[0][0]), 64)  # a fresh 32-byte challenge
            self.assertIn("reconnect account my-bluesky", seen[0][1])

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(disconnect.reconnect(db, "my-bluesky", confirm=lambda challenge, **kw: None))
            self.assertIsNone(disconnect.disconnected_since(db, "my-bluesky"))
            actions = [r[0] for r in db.execute("SELECT action FROM event_log WHERE action LIKE 'account_%' ORDER BY id")]
            self.assertEqual(actions, ["account_disconnected", "account_reconnected"])
            pid = approved(db)
            self.assertTrue(claim_sending(db, pid))

    def test_each_challenge_is_new(self):
        challenges = []
        with connect(self.db) as db, contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                disconnect.disconnect(db, "my-bluesky")
                disconnect.reconnect(db, "my-bluesky", confirm=lambda challenge, **kw: challenges.append(challenge))
        self.assertEqual(len(set(challenges)), 2)

    def test_reconnecting_a_connected_account_is_a_no_op_without_a_prompt(self):
        with connect(self.db) as db:
            self.assertFalse(disconnect.reconnect(db, "my-bluesky", confirm=lambda *a, **k: self.fail("prompted")))

    def test_listing_handles_underscores_and_ignores_other_settings(self):
        with connect(self.db) as db:
            for account in ("a_b", "axb", "plain"):
                disconnect.disconnect(db, account)
            db.execute("INSERT INTO settings(name,value) VALUES('account_disconnectedX','1')")
            self.assertEqual(disconnect.disconnected_accounts(db), ["a_b", "axb", "plain"])

    def test_disconnect_and_the_unresolved_send_hold_work_together(self):
        from reachweaver.storage import HOLD_REFUSAL, uncertain_send
        with connect(self.db) as db:
            first = approved(db, "first note")
            self.assertTrue(claim_sending(db, first))
            uncertain_send(db, first, "network_or_timeout_uncertain", send_class="ambiguous")
            second = approved(db, "second note")
            with self.assertRaisesRegex(ValidationError, HOLD_REFUSAL):
                claim_sending(db, second)  # held by the unresolved send
            disconnect.disconnect(db, "my-bluesky")
            with self.assertRaisesRegex(ValidationError, disconnect.DISCONNECTED):
                claim_sending(db, second)  # the disconnect is reported first
            with contextlib.redirect_stdout(io.StringIO()):
                disconnect.reconnect(db, "my-bluesky", confirm=lambda challenge, **kw: None)
            with self.assertRaisesRegex(ValidationError, HOLD_REFUSAL):
                claim_sending(db, second)  # reconnecting does not clear an unresolved send

    def test_worker_log_has_a_reason_for_it(self):
        self.assertIn("account_disconnected", SKIP_REASONS)


class DisconnectCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_DC_BSKY", "handle": "owner.bsky.social"},
            {"id": "my-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_DC_TG", "chat_id": "@examplechannel",
             "channel_username": "examplechannel", "bot_username": "example_bot"}]}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_disconnect_lists_names_and_steps_and_never_a_value(self):
        with patch.dict(os.environ, ENV):
            code, out, err = self.cli("disconnect", "--account", "my-bluesky")
        self.assertEqual(code, 0, err)
        self.assertIn("Disconnected my-bluesky", out)
        self.assertIn("This revoked nothing at the provider", out)
        self.assertIn("REACHWEAVER_DC_BSKY_IDENTIFIER", out)
        self.assertIn("REACHWEAVER_DC_BSKY_APP_PASSWORD", out)
        self.assertNotIn(SECRET_VALUE, out + err)
        self.assertIn("reachweaver cancel POST_ID", out)
        code, out, _ = self.cli("disconnect", "--account", "my-bluesky")
        self.assertIn("Already disconnected", out)

    def test_unknown_account_is_refused(self):
        code, out, err = self.cli("disconnect", "--account", "nobody")
        self.assertEqual(code, 2)
        self.assertIn("Unknown account", err)
        with connect(self.db) as db:
            self.assertEqual(disconnect.disconnected_accounts(db), [])

    def test_reconnect_without_a_terminal_is_refused_and_the_account_stays_disconnected(self):
        self.cli("disconnect", "--account", "my-bluesky")
        code, out, err = self.cli("reconnect", "--account", "my-bluesky")
        self.assertNotEqual(code, 0)
        self.assertIn("human_confirmation_requires_tty", out + err)
        with connect(self.db) as db:
            self.assertIsNotNone(disconnect.disconnected_since(db, "my-bluesky"))

    def test_reconnect_of_a_connected_account_says_so(self):
        code, out, _ = self.cli("reconnect", "--account", "my-bluesky")
        self.assertEqual(code, 0)
        self.assertIn("is not disconnected", out)

    def test_a_live_publish_is_skipped_before_any_credential_or_request(self):
        with connect(self.db) as db:
            pid = approved(db)
        self.cli("disconnect", "--account", "my-bluesky")
        # Credentials are not even set: the disconnect must be reported first, and nothing is requested.
        with patch.dict(os.environ, {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers.request_json") as request, patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        request.assert_not_called()
        network.assert_not_called()
        self.assertEqual(code, 2)
        self.assertIn(f"SKIPPED {pid}: {disconnect.DISCONNECTED}", out)
        self.assertNotIn("configure environment variables", out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_the_dry_run_and_connect_check_report_the_state(self):
        with connect(self.db) as db:
            approved(db)
        self.cli("disconnect", "--account", "my-bluesky")
        with patch("reachweaver.publishers.request_json") as request:
            code, out, _ = self.cli("publish")
        request.assert_not_called()
        self.assertIn(disconnect.DISCONNECTED, out)
        self.assertIn("--live would skip it before any claim or request", out)
        from reachweaver.connections import VerifiedConnection
        verified = VerifiedConnection("my-bluesky", "bluesky", "verified", "unverified")
        with patch("reachweaver.cli.check_connection", return_value=verified):
            code, out, err = self.cli("connect-check", "--account", "my-bluesky", "--online")
        self.assertEqual(json.loads(out)["local_state"], "disconnected")
        self.assertIn("disconnected_since", json.loads(out))


class CredentialNamesTests(unittest.TestCase):
    def test_names_come_from_the_account_config_and_never_include_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.json"
            path.write_text(json.dumps({"accounts": [
                {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_CN_BSKY", "handle": "owner.bsky.social"},
                {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_CN_MASTO",
                 "server": "https://social.example.org", "handle": "alice"}]}), encoding="utf-8")
            accounts = load_accounts(path)
        with patch.dict(os.environ, {"REACHWEAVER_CN_BSKY_APP_PASSWORD": SECRET_VALUE}):
            self.assertEqual(disconnect.credential_names(accounts["my-bluesky"]),
                             ["REACHWEAVER_CN_BSKY_IDENTIFIER", "REACHWEAVER_CN_BSKY_APP_PASSWORD"])
            self.assertEqual(disconnect.credential_names(accounts["my-mastodon"]), ["REACHWEAVER_CN_MASTO_TOKEN"])
            text = "\n".join(disconnect.revocation_steps(accounts["my-bluesky"]))
        self.assertNotIn(SECRET_VALUE, text)
        self.assertIn("never held your password", text)


if __name__ == "__main__":
    unittest.main()
