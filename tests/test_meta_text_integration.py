"""Threads (#19) and Facebook Pages (#21) under the phase 1 live gates (owner-approved 2026-10-09;
disabled until a live receipt).

``test_threads`` and ``test_facebook`` cover the adapters on temporary flipped registries. These tests pin what only the
combination can break, on the same flipped copy (the bundled registry is never changed):

* the #7 D6 confirmation runs before any Threads request, and a failure sends nothing;
* the #7 D7 keyed tag is checked before the claim: a forged row sends no write request;
* a #11 account cooldown holds the post before any request;
* owner decision D19-2: the emergency pause is re-read during the container wait and stops before
  ``threads_publish`` (needs_review, never resent);
* owner decision D19-4: a built-in Threads cap of 2 sends per 24 h that the CLI cannot loosen;
* owner decision D21-5: a built-in Facebook cap of 2 sends per 24 h and a 60-minute gap.
Facebook sends one write request, so there is no wait inside the adapter to pause.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_approval_confirmation_keyed import FakeTty, forge
import test_facebook as fb
import test_reply_send as rs
from test_threads import ENV, LIVE_ENV, FakeThreads, FlippedRegistry, account

from reachweaver import accounts as accounts_module, confirmation, threads
from reachweaver.policy import SUPPORTED_LIVE
from reachweaver.replies import canonical_target
from reachweaver.cli import main
from reachweaver.caps import BUILTIN_PROVIDER_CAPS, effective_caps, providers_for
from reachweaver.publishers import PublishError
from gate_support import publish  # noqa: E402
from reachweaver.storage import (
    APPROVAL_KEY_CREDENTIAL, MAC_MISMATCH, add_post, claim_sending, connect, get_post, record_provider_cooldown,
    set_paused, transition,
)

KEY = "FAKE-offline-meta-integration-approval-key-0123456789"


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


class ThreadsGateIntegration(FlippedRegistry):
    def setUp(self):
        super().setUp()
        self.db = self.tmp / "state" / "outbox.sqlite"
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def approved(self, text="Release notes are out: https://example.com"):
        with connect(str(self.db)) as con:
            pid = add_post(con, "th-me", "threads", text)
            transition(con, pid, "approve")
        return pid

    def status_of(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)["status"]

    def claims(self, pid):
        with connect(str(self.db)) as con:
            return con.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed' AND post_id=?",
                               (pid,)).fetchone()[0]

    def test_failed_confirmation_sends_no_threads_request(self):
        pid = self.approved()
        with connect(str(self.db)) as con:
            confirmation.enable(con)
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.confirmation.require_interactive"), \
                patch("reachweaver.confirmation._DevTty", FakeTty(["00000000\n"] * 3)):
            status, _, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn(confirmation.FAILED, err)
        self.assertEqual(fake.routes, [])  # not even the identity or quota GET
        self.assertEqual(self.status_of(pid), "approved")
        self.assertEqual(self.claims(pid), 0)

    def test_forged_keyed_row_sends_no_write_request(self):
        with patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: KEY}), connect(str(self.db)) as con:
            pid = add_post(con, "th-me", "threads", "Release notes are out: https://example.com")
            transition(con, pid, "approve")
            forge(con, pid, "Forged after approval: https://example.com")
        fake = FakeThreads()
        with patch.dict(os.environ, {**LIVE_ENV, APPROVAL_KEY_CREDENTIAL: KEY}), \
                patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn(MAC_MISMATCH, out)
        self.assertNotIn("create", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")
        self.assertEqual(self.claims(pid), 0)
        with connect(str(self.db)) as con, patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: KEY}):
            with self.assertRaisesRegex(Exception, MAC_MISMATCH):  # claim_sending re-checks under its lock
                claim_sending(con, pid)

    def test_account_cooldown_holds_before_any_request(self):
        pid = self.approved()
        with connect(str(self.db)) as con:
            record_provider_cooldown(con, "th-me", _later(600), "default", stage="send")
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("account_provider_cooldown_active", out)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.status_of(pid), "approved")

    def test_pause_during_container_wait_stops_before_publish_and_is_never_resent(self):
        pid = self.approved()
        fake = FakeThreads(statuses=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))

        def pause_while_waiting(seconds):
            with connect(str(self.db)) as con:
                set_paused(con, True)

        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.threads._sleep", side_effect=pause_while_waiting):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 3, out)
        self.assertIn(threads.PAUSED_CODE, out)
        self.assertIn("create", fake.routes)
        self.assertNotIn("publish", fake.routes)  # never made public
        self.assertEqual(self.status_of(pid), "needs_review")
        with connect(str(self.db)) as con:
            set_paused(con, False)
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            again, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(again, 2)
        self.assertEqual(fake.routes.count("create"), 1)

    def test_cli_cannot_loosen_the_built_in_threads_cap(self):
        pid = self.approved()
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.cli.claim_sending", wraps=claim_sending) as claim:
            status, out, err = self.cli("publish", "--live", "--post-id", pid, "--max-per-24h", "10")
        self.assertEqual(status, 0, out + err)
        self.assertEqual(claim.call_args.kwargs["max_per_24h"], 2)


class ThreadsPauseLibrary(FlippedRegistry):
    def test_pause_before_publish_raises_after_the_container_and_before_threads_publish(self):
        for flips in (1, 2, 3):  # paused before the wait, between polls, or right before publishing
            with self.subTest(after_checks=flips):
                calls = []

                def paused():
                    calls.append(1)
                    return len(calls) >= flips

                fake = FakeThreads(statuses=("IN_PROGRESS", "FINISHED"))
                with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
                    with self.assertRaisesRegex(PublishError, f"^{threads.PAUSED_CODE}$"):
                        publish(account(), "Release notes: https://example.com", "p1", paused=paused)
                self.assertIn("create", fake.routes)
                self.assertNotIn("publish", fake.routes)

    def test_no_pause_callback_keeps_the_original_flow(self):
        fake = FakeThreads()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            publish(account(), "Release notes: https://example.com", "p1", paused=lambda: False)
        self.assertEqual(fake.routes, ["me", "quota", "create", "status", "publish", "media"])


class BuiltInPilotCaps(unittest.TestCase):
    def test_threads_cap_is_two_per_day_and_tighten_only(self):
        self.assertEqual(BUILTIN_PROVIDER_CAPS["threads"], {"max_per_24h": 2})
        providers = providers_for(account())
        self.assertIn("threads", providers)
        self.assertEqual(effective_caps(None, "th-me", 10, 15, providers=providers), (2, 15))
        self.assertEqual(effective_caps(None, "th-me", 1, 120, providers=providers), (1, 120))  # stricter CLI wins
        tighter = {"version": 2, "defaults": {}, "accounts": {}, "providers": {"threads": {"max_per_24h": 1}}}
        self.assertEqual(effective_caps(tighter, "th-me", 10, 15, providers=providers), (1, 15))
        self.assertEqual(effective_caps(None, "m1", 10, 15, providers=("mastodon",)), (10, 15))  # others unchanged



class FacebookGateIntegration(fb.FlippedRegistry):
    def setUp(self):
        super().setUp()
        self.db = self.tmp / "state" / "outbox.sqlite"
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [fb.account()]}), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def approved(self, text=fb.TEXT):
        with connect(str(self.db)) as con:
            pid = add_post(con, "fb-page", "facebook", text)
            transition(con, pid, "approve")
        return pid

    def status_of(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)["status"]

    def run_live(self, pid, *extra, env=None):
        fake = fb.FakeGraph()
        with patch.dict(os.environ, env or fb.LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid, *extra)
        return status, out, err, fake

    def test_failed_confirmation_sends_no_graph_request(self):
        pid = self.approved()
        with connect(str(self.db)) as con:
            confirmation.enable(con)
        with patch("reachweaver.confirmation.require_interactive"), \
                patch("reachweaver.confirmation._DevTty", FakeTty(["00000000\n"] * 3)):
            status, _, err, fake = self.run_live(pid)
        self.assertEqual(status, 2)
        self.assertIn(confirmation.FAILED, err)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.status_of(pid), "approved")

    def test_forged_keyed_row_sends_no_feed_request(self):
        with patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: KEY}), connect(str(self.db)) as con:
            pid = add_post(con, "fb-page", "facebook", fb.TEXT)
            transition(con, pid, "approve")
            forge(con, pid, "Forged after approval: https://example.com")
        status, out, _, fake = self.run_live(pid, env={**fb.LIVE_ENV, APPROVAL_KEY_CREDENTIAL: KEY})
        self.assertEqual(status, 2)
        self.assertIn(MAC_MISMATCH, out)
        self.assertNotIn("feed", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")

    def test_account_cooldown_holds_before_any_request(self):
        pid = self.approved()
        with connect(str(self.db)) as con:
            record_provider_cooldown(con, "fb-page", _later(600), "default", stage="send")
        status, out, _, fake = self.run_live(pid)
        self.assertEqual(status, 2)
        self.assertIn("account_provider_cooldown_active", out)
        self.assertEqual(fake.routes, [])

    def test_cli_cannot_loosen_the_built_in_facebook_caps(self):
        pid = self.approved()
        with patch("reachweaver.cli.claim_sending", wraps=claim_sending) as claim:
            status, out, err, fake = self.run_live(pid, "--max-per-24h", "10", "--min-gap-minutes", "15")
        self.assertEqual(status, 0, out + err)
        self.assertEqual((claim.call_args.kwargs["max_per_24h"], claim.call_args.kwargs["min_gap_minutes"]), (2, 60))
        self.assertEqual(fake.routes.count("feed"), 1)

    def test_pause_before_the_run_sends_nothing(self):
        pid = self.approved()
        with connect(str(self.db)) as con:
            set_paused(con, True)
        status, _, err, fake = self.run_live(pid)
        self.assertEqual(status, 2)
        self.assertIn("Emergency pause", err)
        self.assertEqual(fake.routes, [])


class FacebookBuiltInCaps(unittest.TestCase):
    def test_facebook_cap_is_two_per_day_with_a_60_minute_gap(self):
        self.assertEqual(BUILTIN_PROVIDER_CAPS["facebook"], {"max_per_24h": 2, "min_gap_minutes": 60})
        providers = providers_for(fb.account())
        self.assertIn("facebook", providers)
        self.assertEqual(effective_caps(None, "fb-page", 10, 15, providers=providers), (2, 60))
        self.assertEqual(effective_caps(None, "fb-page", 1, 240, providers=providers), (1, 240))



class BundledRegistryRefusesThreadsFacebookAndReplies(rs.Base):
    """Under the bundled registry (never patched here) ``publish --live`` refuses a Threads post, a
    Facebook post and Bluesky/Mastodon replies before any platform credential is read and before
    any request (credential reader instrumented; Instagram, YouTube, TikTok and Mastodon images are
    covered by ``test_media_integration.BundledRegistryRefusesEveryMediaMode``)."""

    def test_publish_live_refuses_before_any_credential_read_or_request(self):
        self.assertEqual(SUPPORTED_LIVE, frozenset({"bluesky", "mastodon", "telegram", "linkedin"}))
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_RP_BSKY", "handle": "owner.bsky.social"},
            {"id": "my-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_RP_MASTO",
             "server": rs.SERVER, "handle": "owner"},
            account(env_prefix="REACHWEAVER_IT3_THREADS"), fb.account(env_prefix="REACHWEAVER_IT3_FB"),
        ]}), encoding="utf-8")
        with connect(self.db) as db:
            self.bsky_root(db)
            self.masto_root(db)
            posts = {
                "threads": add_post(db, "th-me", "threads", "Release notes: https://example.com"),
                "facebook": add_post(db, "fb-page", "facebook", "Release notes: https://example.com"),
                "bluesky reply": add_post(db, "my-bsky", "bluesky", "Thanks for asking!",
                                          reply_target=canonical_target(self.bsky_target(db))),
                "mastodon reply": add_post(db, "my-masto", "mastodon", "Thanks for asking!",
                                           reply_target=canonical_target(self.masto_target(db))),
            }
            for pid in posts.values():
                transition(db, pid, "approve")
        live = {**rs.LIVE, **rs.BSKY_ENV, **rs.MASTO_ENV, "REACHWEAVER_IT3_THREADS_ACCESS_TOKEN": "fake-value",
                "REACHWEAVER_IT3_FB_PAGE_ACCESS_TOKEN": "fake-value"}
        prefixes = ("REACHWEAVER_RP_", "REACHWEAVER_IT3_")
        real = accounts_module.credential
        expected = {"threads": "requires manual publishing", "facebook": "requires manual publishing",
                    "bluesky reply": "reply_capability_gated", "mastodon reply": "reply_capability_gated"}
        for kind, pid in posts.items():
            reads: list[str] = []

            def tracking(name, env=None, _reads=reads):
                _reads.append(name)
                return real(name, env)

            with self.subTest(kind=kind), patch("reachweaver.accounts.credential", side_effect=tracking), \
                    patch("reachweaver.threads.credential", side_effect=tracking), \
                    patch("reachweaver.facebook.credential", side_effect=tracking), \
                    patch("reachweaver.publishers._send_http") as send, \
                    patch("reachweaver.publishers.request_json") as post, \
                    patch("reachweaver.publishers.request_get_json") as get:
                code, out, err = self.cli("publish", "--live", "--post-id", pid, env=live)
                self.assertEqual(code, 2, out + err)
                self.assertIn(expected[kind], out)
                send.assert_not_called()
                post.assert_not_called()
                get.assert_not_called()
                self.assertEqual([n for n in reads if n.startswith(prefixes)], [])
                with connect(self.db) as db:
                    self.assertEqual(get_post(db, pid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
