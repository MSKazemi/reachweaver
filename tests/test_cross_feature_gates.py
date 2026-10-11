"""Cross-feature gate ordering after integrating #7 D6/D7, #11 shared cooldowns and #14 scope check.

Each feature has its own suite (``test_approval_confirmation_keyed``, ``test_shared_cooldown``,
``test_linkedin_scope``). These tests pin what only the combination can break: a failed human
confirmation (#7 D6) must stop before the Bluesky ``createSession`` reservation (#11) and before
the LinkedIn introspection request (#14), and ``claim_sending`` must enforce the keyed binding
(#7 D7) and a shared Postiz cooldown scope (#11) together. Offline: every terminal is a fake, the
connection check is mocked and no request leaves the process.
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

from test_approval_confirmation_keyed import FakeTty, NoReadStdin, Terminal, forge

from reachweaver import confirmation
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.ratelimit import POSTIZ_WORKSPACE_SCOPE
from reachweaver.storage import (
    APPROVAL_KEY_CREDENTIAL, MAC_MISMATCH, SESSION_EVENT, add_post, approval_payload_sha256, claim_sending,
    connect, get_post, record_provider_cooldown, transition,
)

KEY = "FAKE-offline-cross-feature-approval-key-0123456789"
ACME = POSTIZ_WORKSPACE_SCOPE + "acme"


def _count(db, action):
    return db.execute("SELECT COUNT(*) FROM event_log WHERE action=?", (action,)).fetchone()[0]


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class FailedConfirmationStopsBeforeNewPreflightWork(unittest.TestCase):
    ACCOUNTS = [
        {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_XF_BSKY",
         "handle": "example.bsky.social"},
        {"id": "li-scope", "platform": "linkedin", "env_prefix": "REACHWEAVER_XF_LI", "person_id": "782bbtaQ",
         "linkedin_version": "202609", "authorization_expires_at": "2026-12-01T00:00:00+00:00",
         "scope_check": "introspect"},
    ]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.db = str(root / "outbox.sqlite")
        self.config = root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": self.ACCOUNTS}), encoding="utf-8")
        with connect(self.db) as db:
            confirmation.enable(db)

    def cli(self, *argv, env, tty):
        out, err = Terminal(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch("sys.stdin", NoReadStdin()))
            stack.enter_context(patch("reachweaver.confirmation._DevTty", tty))
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def approved(self, account_id, platform):
        with connect(self.db) as db:
            pid = add_post(db, account_id, platform, "A useful verified research note")
            prefix = approval_payload_sha256(get_post(db, pid), scheduled_at=None, use_row_schedule=False)[:8]
        code, out, err = self.cli("approve", pid, env={}, tty=FakeTty([prefix + "\n"]))
        self.assertEqual(code, 0, out + err)
        return pid

    def test_wrong_prefix_spends_no_session_budget_and_sends_no_introspection(self):
        live = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
                "REACHWEAVER_XF_BSKY_IDENTIFIER": "name", "REACHWEAVER_XF_BSKY_APP_PASSWORD": "app-pw",
                "REACHWEAVER_XF_LI_ACCESS_TOKEN": "FAKE-li-token", "REACHWEAVER_XF_LI_CLIENT_ID": "FAKE-id",
                "REACHWEAVER_XF_LI_CLIENT_SECRET": "FAKE-li-client-value"}
        for account_id, platform in (("my-bluesky", "bluesky"), ("li-scope", "linkedin")):
            with self.subTest(platform=platform):
                pid = self.approved(account_id, platform)
                with patch("reachweaver.cli.check_connection") as preflight, \
                        patch("reachweaver.cli.reserve_bluesky_sessions") as reserve, \
                        patch("reachweaver.cli.publish") as sent:
                    code, _, err = self.cli("publish", "--live", "--post-id", pid, env=live,
                                            tty=FakeTty(["00000000\n"] * 3))
                self.assertEqual(code, 2)
                self.assertIn(confirmation.FAILED, err)
                preflight.assert_not_called()  # so no createSession and no introspectToken request
                reserve.assert_not_called()
                sent.assert_not_called()
                with connect(self.db) as db:
                    self.assertEqual(get_post(db, pid)["status"], "approved")
                    self.assertEqual(_count(db, "send_claimed"), 0)
                    self.assertEqual(_count(db, SESSION_EVENT), 0)


class ClaimEnforcesKeyedBindingAndSharedScope(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "outbox.sqlite")

    def test_both_gates_hold_under_the_claim_lock(self):
        with patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: KEY}, clear=True), connect(self.db) as db:
            ids = []
            for text in ("Note one", "Note two", "Note three"):
                pid = add_post(db, "pz-b", "mastodon", text)
                transition(db, pid, "approve")  # keyed: stores the MAC and latches keyed mode
                ids.append(pid)
            cooled, forged, clean = ids
            record_provider_cooldown(db, "pz-a", _later(600), "default", stage="send", scope=ACME)
            # A valid MAC does not get past a shared-scope cooldown recorded by another account.
            with self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active"):
                claim_sending(db, cooled, cooldown_scopes=(ACME,))
            # A forged row is refused on the binding before the cooldown is even consulted.
            forge(db, forged)
            with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                claim_sending(db, forged, cooldown_scopes=(ACME,))
            self.assertEqual(get_post(db, forged)["last_error"], MAC_MISMATCH)
            self.assertEqual(_count(db, "approval_binding_failed"), 1)
            self.assertEqual(_count(db, "send_claimed"), 0)
            # Outside the scope and correctly signed, the claim proceeds exactly as before.
            self.assertTrue(claim_sending(db, clean, expected_sha256=approval_payload_sha256(get_post(db, clean))))
            self.assertEqual([get_post(db, p)["status"] for p in ids], ["approved", "approved", "sending"])


if __name__ == "__main__":
    unittest.main()
