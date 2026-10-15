"""Mastodon authorization failures and the cross-server handle rule (private #13). Tests only.

Every HTTP exchange is scripted at ``publishers._send_http`` (``Wire`` from ``test_rate_limits``);
nothing reaches any host and no live post is made.

* ``MastodonAuthFixtureTests`` replays the synthetic 401 and 403 fixtures in
  ``tests/fixtures/providers/mastodon/`` through ``publish --live``. At the identity preflight
  (``verify_credentials``) they block the post before the claim, with one request and no retry; on
  the status create, after the claim, they end in ``needs_review`` (class ``ambiguous``) and a new
  process never resends. The fixtures are hand-written from docs.joinmastodon.org, not recordings.
* ``CrossServerHandleTests`` shows that a handle naming another server than the account's own
  ``server`` is refused by ``connections._mastodon`` before any request. The account file itself
  loads: the refusal happens at ``connect-check --online`` and at the live preflight.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from test_fault_injection import fixture_http, load
from test_no_network import NetworkGuard
from test_rate_limits import ACCOUNTS, ENV, MARKER, MASTO_ME, RateLimitBase, Wire, ok_routes

from reachweaver.accounts import load_accounts
from reachweaver.connections import check_connection
from reachweaver.policy import ValidationError
from reachweaver.storage import connect

STATUSES = ("POST", "/api/v1/statuses")
VERIFY = ("GET", "verify_credentials")
# fixture -> HTTP status; the provider's error text must never reach any output.
PREFLIGHT = {"mastodon/verify-credentials-401-invalid-token.json": 401,
             "mastodon/verify-credentials-403-login-disabled.json": 403}
CREATE = {"mastodon/statuses-401-invalid-token.json": 401,
          "mastodon/statuses-403-outside-scope.json": 403}
CROSS_SERVER = "mastodon_expected_handle_must_be_local_to_configured_server"


def provider_text(name: str) -> str:
    return load(name)["body"]["error"]


class MastodonAuthFixtureTests(RateLimitBase):
    def test_fixtures_are_synthetic_cited_and_carry_no_credential(self):
        for name, status in {**PREFLIGHT, **CREATE}.items():
            with self.subTest(fixture=name):
                fx = load(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                self.assertEqual(fx["status"], status)
                self.assertIn("https://docs.joinmastodon.org/methods/", fx["_fixture"]["source"])
                self.assertEqual(set(fx["body"]), {"error"})
                blob = json.dumps({k: v for k, v in fx.items() if k != "_fixture"}).lower()
                for word in ("authorization", "bearer", "access_token", "api_key"):
                    self.assertNotIn(word, blob)

    def test_401_or_403_at_identity_preflight_blocks_before_claim_without_retry(self):
        for name, status in PREFLIGHT.items():
            with self.subTest(fixture=name):
                pid = self.approved("rl-mastodon", text=f"Preflight {status} note")
                wire = Wire({VERIFY: [lambda n=name: fixture_http(n)], STATUSES: []})
                code, out, err = self.live(wire, "--post-id", pid)
                self.assertEqual(code, 2, out + err)
                self.assertIn("BLOCKED", out)
                self.assertIn(f"http_{status}", out)
                self.assertEqual(wire.count(*VERIFY), 1)  # a 401/403 is not a retryable preflight status
                self.assertEqual(wire.count(*STATUSES), 0)
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.cooldowns(), [])
                self.assertEqual(self.events("send_claimed", pid), [])
                row = self.row(pid)
                self.assertEqual((row["status"], row["remote_id"]), ("approved", None))
                for leak in (provider_text(name), MARKER, ENV["REACHWEAVER_RL_MASTO_TOKEN"]):
                    self.assertNotIn(leak, out + err)
                with connect(self.db) as db:  # leave nothing due for the next subtest
                    db.execute("UPDATE posts SET status='cancelled' WHERE id=?", (pid,))

    def test_401_or_403_on_status_create_goes_to_needs_review_and_is_never_resent(self):
        for name, status in CREATE.items():
            with self.subTest(fixture=name):
                with connect(self.db) as db:  # the per-account minimum gap would block the next claim
                    db.execute("DELETE FROM event_log WHERE action='send_claimed'")
                pid = self.approved("rl-mastodon", text=f"Create {status} note")
                wire = Wire(ok_routes("mastodon", [lambda n=name: fixture_http(n)]))
                code, out, err = self.live(wire, "--post-id", pid)
                self.assertEqual(code, 3, out + err)
                self.assertIn("[ambiguous]", out)
                self.assertNotIn("PUBLISHED", out)
                self.assertEqual(wire.count(*STATUSES), 1)
                row = self.row(pid)
                self.assertEqual((row["status"], row["remote_id"], row["remote_url"]), ("needs_review", None, None))
                self.assertTrue(row["last_error"].endswith(f"http_{status}"), row["last_error"])
                self.assertTrue(self.events("needs_review", pid)[-1]["detail"].endswith("class=ambiguous"))
                self.assertEqual(self.events("published", pid), [])
                self.assertEqual(self.cooldowns(), [])  # only a 429 records a cooldown
                for leak in (provider_text(name), MARKER, ENV["REACHWEAVER_RL_MASTO_TOKEN"]):
                    self.assertNotIn(leak, out + err)
                again = Wire({})  # a new process: the same post id and a full sweep send nothing
                with NetworkGuard() as guard:
                    self.assertEqual(self.live(again, "--post-id", pid)[0], 2)
                    self.live(again)
                self.assertEqual((again.calls, guard.attempts), ([], []))
                self.assertEqual(self.row(pid)["status"], "needs_review")
                with connect(self.db) as db:  # an unresolved post holds the account (#41); free it for the next subtest
                    db.execute("UPDATE posts SET status='cancelled' WHERE id=?", (pid,))
        self.assertEqual(self.sleeps, [])


class CrossServerHandleTests(RateLimitBase):
    """The configured server is https://mastodon.example.org (``rl-mastodon`` in test_rate_limits)."""

    def with_handle(self, handle):
        accounts = [dict(a, handle=handle) if a["id"] == "rl-mastodon" else a for a in ACCOUNTS]
        self.config.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
        return load_accounts(self.config)["rl-mastodon"]  # the account file itself loads

    def check(self, account, wire):
        """``check_connection`` with the token set; returns (result or ValidationError, guard)."""
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http", wire), \
                patch.dict(os.environ, ENV):
            try:
                return check_connection(account), guard
            except ValidationError as exc:
                return exc, guard

    def test_handle_on_another_server_is_refused_before_any_request(self):
        for handle in ("owner@other.example", "@owner@other.example", "owner@mastodon.example.org.evil",
                       "owner@mastodon.example.org@other.example", "owner@"):
            with self.subTest(handle=handle):
                wire = Wire({VERIFY: [MASTO_ME]})
                result, guard = self.check(self.with_handle(handle), wire)
                self.assertIsInstance(result, ValidationError)
                self.assertEqual(str(result), CROSS_SERVER)
                self.assertEqual((wire.calls, guard.attempts), ([], []))

    def test_local_handle_forms_reach_the_identity_check(self):
        for handle in ("owner", "@owner", "owner@mastodon.example.org", "Owner@Mastodon.Example.org"):
            with self.subTest(handle=handle):
                wire = Wire({VERIFY: [MASTO_ME]})
                result, _ = self.check(self.with_handle(handle), wire)
                self.assertNotIsInstance(result, Exception)
                self.assertEqual((result.platform, result.identity), ("mastodon", "verified"))
                self.assertEqual(wire.calls, [VERIFY])

    def test_cli_connect_check_and_live_publish_refuse_before_any_request(self):
        self.with_handle("owner@other.example")
        pid = self.approved("rl-mastodon", text="Cross-server note")
        wire = Wire({VERIFY: [MASTO_ME], STATUSES: []})
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http", wire):
            code, out, err = self.cli("connect-check", "--account", "rl-mastodon", "--online")
            self.assertEqual(code, 2, out + err)
            self.assertIn(CROSS_SERVER, out + err)
            code, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2, out + err)
        self.assertIn("BLOCKED", out)
        self.assertIn(CROSS_SERVER, out)
        self.assertEqual((wire.calls, guard.attempts), ([], []))
        self.assertEqual(self.events("send_claimed", pid), [])
        self.assertEqual(self.row(pid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
