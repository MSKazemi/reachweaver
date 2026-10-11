"""Issue #11 follow-up: provider 429 fixtures and fault injection per adapter (tests only).

Two groups, both offline (every HTTP exchange is scripted at ``publishers._send_http`` and the
no-network guard is on during the second runs):

* ``ProviderQuotaFixtureTests``: the hand-written 429 fixtures of all five send paths
  (``tests/fixtures/providers/<provider>/``) are replayed through the full ``publish --live``
  path. They prove the delay hint comes from ``ratelimit.py`` data (``Retry-After``,
  ``X-RateLimit-Reset``, Telegram ``parameters.retry_after``, the LinkedIn midnight default),
  that the post goes to ``needs_review`` with the right send class, and that a fresh process
  sends nothing for that account. The fixtures are synthetic, not recordings, and are not
  evidence of any live account.
* ``FaultInjectionTests``: a stateful fake provider records every create it receives.
  (a) timeout after success: the provider created the post, the response was lost; (b) process
  restart: the process dies inside the create call. In both, exactly one create request is ever
  made, and a new process, a ``--post-id`` run and a full worker sweep add none (D16).
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from test_no_network import NetworkGuard
from test_rate_limits import (
    ACCOUNT_OF, MARKER, RateLimitBase, Wire, create_route, ok_routes,
)

from reachweaver.ratelimit import AMBIGUOUS, LIKELY_NOT_CREATED, hint_from_http_error
from reachweaver.storage import connect, queue_health

PROVIDERS = Path(__file__).parent / "fixtures" / "providers"
# adapter -> (fixture, cooldown source, send class, ratelimit hint source or None)
CASES = {
    "bluesky": ("bluesky/create-record-429-retry-after.json", "retry_after", LIKELY_NOT_CREATED),
    "mastodon": ("mastodon/statuses-429-x-ratelimit-reset.json", "x_ratelimit_reset", LIKELY_NOT_CREATED),
    "telegram": ("telegram/send-message-429-retry-after.json", "telegram_retry_after", LIKELY_NOT_CREATED),
    "linkedin": ("linkedin/posts-429-daily-throttle.json", "daily_reset", AMBIGUOUS),
    "postiz": ("postiz/posts-429-retry-after.json", "retry_after", AMBIGUOUS),
}


def load(name: str) -> dict:
    return json.loads((PROVIDERS / name).read_text(encoding="utf-8"))


def fixture_http(name: str) -> HTTPError:
    """The fixture as the HTTPError urllib raises; the URL holds a fake secret that must not leak."""
    fx = load(name)
    reset = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="milliseconds")
    headers = Message()
    for key, value in fx["headers"].items():
        headers[key] = reset if value == "$RESET_ISO" else value
    body = json.dumps(fx["body"]).encode("utf-8")
    return HTTPError(f"https://provider.invalid/{MARKER}", fx["status"], MARKER, headers, BytesIO(body))


class ProviderQuotaFixtureTests(RateLimitBase):
    def test_fixtures_are_synthetic_and_carry_no_credential(self):
        for adapter, (name, _, _) in CASES.items():
            with self.subTest(adapter=adapter):
                fx = load(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                self.assertEqual(fx["status"], 429)
                blob = json.dumps(fx).lower()
                for word in ("authorization", "bearer", "access_token", "accessjwt", "api_key"):
                    self.assertNotIn(word, blob)

    def test_hint_comes_from_ratelimit_data_for_each_fixture(self):
        expected = {"bluesky": (120, "retry_after"), "telegram": (35, "telegram_retry_after"),
                    "postiz": (900, "retry_after"), "linkedin": None}
        for adapter, (name, _, _) in CASES.items():
            with self.subTest(adapter=adapter):
                hint = hint_from_http_error(fixture_http(name), telegram_body=adapter == "telegram")
                if adapter == "mastodon":
                    self.assertEqual(hint.source, "x_ratelimit_reset")
                    self.assertTrue(1790 <= hint.delay_seconds <= 1801, hint)
                elif expected[adapter] is None:
                    self.assertIsNone(hint)  # no header, no Telegram body: the documented default applies
                else:
                    self.assertEqual((hint.delay_seconds, hint.source), expected[adapter])

    def test_each_fixture_through_the_cli_goes_to_needs_review_and_is_never_resent(self):
        for adapter, (name, source, send_class) in CASES.items():
            with self.subTest(adapter=adapter):
                with connect(self.db) as db:
                    db.execute("DELETE FROM event_log WHERE action IN ('provider_cooldown','send_claimed',"
                               "'bluesky_create_session')")
                account = ACCOUNT_OF[adapter]
                pid = self.approved(account, text=f"Quota fixture note for {adapter}")
                sibling = self.approved(account, text=f"Sibling note for {adapter}")
                wire = Wire(ok_routes(adapter, [lambda n=name: fixture_http(n)]))
                code, out, err = self.live(wire, "--post-id", pid)
                self.assertEqual(code, 3, out + err)
                self.assertIn(f"[{send_class}]", out)
                self.assertNotIn(MARKER, out + err)
                row = self.row(pid)
                self.assertEqual(row["status"], "needs_review")
                self.assertTrue(row["last_error"].endswith("http_429") or "429" in row["last_error"], row)
                self.assertTrue(self.events("needs_review", pid)[-1]["detail"].endswith(f"class={send_class}"))
                [cooldown] = self.cooldowns()
                self.assertEqual((cooldown["account_id"], cooldown["source"], cooldown["send_class"]),
                                 (account, source, send_class))
                self.assertEqual(wire.count(*create_route(adapter)), 1)
                # A new process (fresh wire, fresh connection): nothing is sent for this account.
                again = Wire({})
                with NetworkGuard() as guard:
                    code, _, _ = self.live(again, "--post-id", pid)
                    self.assertEqual(code, 2)
                    code, out, _ = self.live(again, "--post-id", sibling)
                    self.assertEqual(code, 2)
                    self.assertIn("account_provider_cooldown_active", out)
                    self.live(again)  # a full worker sweep
                self.assertEqual((again.calls, guard.attempts), ([], []))
                self.assertEqual(self.row(pid)["status"], "needs_review")
                self.assertEqual(self.row(sibling)["status"], "approved")
                with connect(self.db) as db:  # leave the sibling out of the next subtest's sweep
                    db.execute("UPDATE posts SET status='cancelled' WHERE id=?", (sibling,))
        self.assertEqual(self.sleeps, [])


class FakeProvider:
    """Stateful stand-in for a provider: counts the posts it actually created."""

    def __init__(self, outcome):
        self.created = 0
        self.outcome = outcome

    def create(self):
        self.created += 1  # the provider has made the post before the failure below
        raise self.outcome()


class FaultInjectionTests(RateLimitBase):
    def assert_no_resend(self, adapter, pid, *, status):
        """A new process, the same post id and a full sweep: zero requests, state unchanged."""
        again = Wire({})
        with NetworkGuard() as guard:
            self.assertEqual(self.live(again, "--post-id", pid)[0], 2)
            self.live(again)
        self.assertEqual((again.calls, guard.attempts), ([], []))
        self.assertEqual(self.row(pid)["status"], status)

    def test_timeout_after_success_is_never_resent(self):
        for adapter, account in ACCOUNT_OF.items():
            with self.subTest(adapter=adapter):
                with connect(self.db) as db:
                    db.execute("DELETE FROM event_log WHERE action IN ('send_claimed','bluesky_create_session')")
                pid = self.approved(account, text=f"Created remotely, response lost: {adapter}")
                provider = FakeProvider(lambda: TimeoutError("timed out"))
                wire = Wire(ok_routes(adapter, [provider.create]))
                code, out, err = self.live(wire, "--post-id", pid)
                self.assertEqual(code, 3, out + err)
                self.assertEqual(self.row(pid)["status"], "needs_review")
                self.assertTrue(self.events("needs_review", pid)[-1]["detail"].endswith("class=ambiguous"))
                self.assertEqual(self.cooldowns(), [])  # a timeout is not a rate limit
                self.assert_no_resend(adapter, pid, status="needs_review")
                self.assertEqual(provider.created, 1)  # exactly one remote post, no duplicate
        self.assertEqual(self.sleeps, [])

    def test_process_restart_inside_the_create_call_is_never_resent(self):
        """The process dies after the claim (KeyboardInterrupt is not caught by the send handlers).

        Nothing can classify the outcome, so the post stays ``sending`` (it is not moved to
        ``needs_review`` by anyone). It is never eligible again: a restarted process, a
        ``--post-id`` run and a sweep send nothing, and ``queue_health`` reports it as stale
        sending so a human reconciles it.
        """
        for adapter, account in ACCOUNT_OF.items():
            with self.subTest(adapter=adapter):
                with connect(self.db) as db:
                    db.execute("DELETE FROM event_log WHERE action IN ('send_claimed','bluesky_create_session')")
                pid = self.approved(account, text=f"Process dies mid-send: {adapter}")
                provider = FakeProvider(KeyboardInterrupt)
                wire = Wire(ok_routes(adapter, [provider.create]))
                code, out, err = self.live(wire, "--post-id", pid)
                self.assertEqual(code, 130, out + err)
                self.assertIn("reconcile manually", err)
                self.assertEqual(self.row(pid)["status"], "sending")
                self.assert_no_resend(adapter, pid, status="sending")
                self.assertEqual(provider.created, 1)
                later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
                with connect(self.db) as db:
                    health = queue_health(db, now=later)
                self.assertIn(pid, [p["post_id"] for p in health["stale_sending"]])
                self.assertTrue(health["attention_required"])
                with connect(self.db) as db:  # leave it out of the next subtest's sweep
                    db.execute("UPDATE posts SET status='cancelled' WHERE id=?", (pid,))
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
