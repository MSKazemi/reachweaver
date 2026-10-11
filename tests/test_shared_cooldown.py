"""Issue #11 follow-up (owner-approved 2026-10-09): shared cooldown scopes and the Bluesky createSession budget.

docs/SECURITY-REVIEW-PROPOSAL-11-shared-cooldown.md. Every HTTP exchange is scripted at
``publishers._send_http`` (or refused by ``NetworkGuard``); nothing reaches a host and no live
publishing happens. The rule under test throughout: both additions are tighten-only. They can
make an unclaimed post wait longer; they never release, retry or resend anything.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from test_no_network import NetworkGuard
from test_rate_limits import MARKER, SESSION, Wire, http

from reachweaver import ratelimit
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.health import build_health
from reachweaver.policy import ValidationError
from reachweaver.privacy import apply_purge, plan_purge
from reachweaver.ratelimit import (
    POSTIZ_WORKSPACE_SCOPE, SESSIONS_PER_PUBLISH, cooldown_scopes, reserve_bluesky_sessions,
    session_scope, session_windows,
)
from reachweaver.storage import (
    SESSION_EVENT, SESSION_REFUSAL, SESSION_REFUSED_EVENT, active_provider_cooldown, add_post,
    claim_sending, connect, connect_readonly, cooldown_refusal, event, get_post, provider_cooldowns,
    record_provider_cooldown, recorded_cooldown_scopes, schema_gaps, sync_cooldown_scopes, transition,
)

KEY = "-".join(("pz", "shared", "workspace", "key"))  # one Postiz key, stored once per prefix (#5)
OTHER_KEY = "-".join(("pz", "other", "workspace", "key"))
BSKY_PW = "-".join(("bsky", "app", "pw"))
ENV = {
    "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
    "REACHWEAVER_SC_A_POSTIZ_API_KEY": KEY, "REACHWEAVER_SC_B_POSTIZ_API_KEY": KEY,
    "REACHWEAVER_SC_C_POSTIZ_API_KEY": OTHER_KEY,
    "REACHWEAVER_SC_D_POSTIZ_API_KEY": KEY, "REACHWEAVER_SC_E_POSTIZ_API_KEY": KEY,
    "REACHWEAVER_SC_F_POSTIZ_API_KEY": KEY,
    "REACHWEAVER_SC_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_SC_BSKY_APP_PASSWORD": BSKY_PW,
    "REACHWEAVER_SC_BSKY2_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_SC_BSKY2_APP_PASSWORD": BSKY_PW,
}


def postiz(account_id, prefix, integration, workspace=None):
    entry = {"id": account_id, "platform": "mastodon", "env_prefix": prefix, "handle": "owner",
             "backend": "postiz", "postiz_base_url": "https://api.postiz.com",
             "postiz_integration_id": integration}
    if workspace:
        entry["postiz_workspace"] = workspace
    return entry


ACCOUNTS = [
    postiz("pz-a", "REACHWEAVER_SC_A", "int-a", "acme"),
    postiz("pz-b", "REACHWEAVER_SC_B", "int-b", "acme"),
    postiz("pz-c", "REACHWEAVER_SC_C", "int-c", "other-org"),
    postiz("pz-d", "REACHWEAVER_SC_D", "int-d"),  # no workspace: per-account, as before
    postiz("pz-e", "REACHWEAVER_SC_E", "int-e"),
    dict(postiz("pz-bsky", "REACHWEAVER_SC_F", "int-f"), platform="bluesky"),
    {"id": "sc-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_SC_BSKY", "handle": "owner.bsky.social"},
    # A second entry for the same Bluesky account shares its createSession budget.
    {"id": "sc-bluesky-two", "platform": "bluesky", "env_prefix": "REACHWEAVER_SC_BSKY2",
     "handle": "@Owner.bsky.social"},
]
INTEGRATIONS = [{"id": f"int-{x}", "identifier": "mastodon", "profile": "owner", "disabled": False}
                for x in "abcde"] + [{"id": "int-f", "identifier": "bluesky", "profile": "owner", "disabled": False}]
ACME = POSTIZ_WORKSPACE_SCOPE + "acme"


def now_s(delta_seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat(timespec="seconds")


class SharedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.db = str(self.base / "state" / "sc.sqlite")
        self.config = self.base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": ACCOUNTS}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        sleeper = patch("reachweaver.ratelimit._sleep", side_effect=lambda _s: None)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def approved(self, account, text="Evidence-backed release note"):
        platform = next(a["platform"] for a in ACCOUNTS if a["id"] == account)
        with connect(self.db) as db:
            pid = add_post(db, account, platform, text)
            transition(db, pid, "approve")
        return pid

    def cli(self, *argv, env=ENV):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live(self, wire, *argv):
        with patch("reachweaver.publishers._send_http", wire):
            return self.cli("publish", "--live", *argv)

    def status(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)["status"]

    def events(self, action):
        with connect(self.db) as db:
            return [dict(r) for r in db.execute("SELECT at, post_id, detail FROM event_log WHERE action=? ORDER BY id",
                                                (action,))]

    def postiz_429_on(self, pid):
        wire = Wire({("GET", "/integrations"): [INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [lambda: http(429, {"Retry-After": "900"}, MARKER.encode())]})
        code, out, err = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.status(pid), "needs_review")  # D16: the claimed post is never resent
        return out + err


# --------------------------------------------------------------------------- config validation

class WorkspaceConfigTests(SharedBase):
    def write(self, accounts):
        self.config.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")

    def test_postiz_workspace_validated_slug_on_backend_accounts_only(self):
        accounts = load_accounts(self.config)
        self.assertEqual(cooldown_scopes(accounts["pz-a"]), (ACME,))
        self.assertEqual(cooldown_scopes(accounts["pz-d"]), ())
        self.assertEqual(cooldown_scopes(accounts["sc-bluesky"]), ())
        for bad in ("Acme", "-acme", "acme_x", "a" * 64, "", 7, "acme corp", KEY + "!"):
            with self.subTest(value=bad):
                self.write([postiz("pz-a", "REACHWEAVER_SC_A", "int-a", None) | {"postiz_workspace": bad}])
                with self.assertRaisesRegex(ValidationError, "postiz_workspace"):
                    load_accounts(self.config)
        self.write([{"id": "plain", "platform": "mastodon", "env_prefix": "REACHWEAVER_SC_M",
                     "server": "https://mastodon.example.org", "postiz_workspace": "acme"}])
        with self.assertRaisesRegex(ValidationError, "only valid on an account with"):
            load_accounts(self.config)

    def test_cooldown_scopes_strict_and_lenient(self):
        bad = {"id": "x", "backend": "postiz", "postiz_workspace": "NOT OK"}
        with self.assertRaisesRegex(ValidationError, "postiz_workspace_invalid"):
            cooldown_scopes(bad)
        self.assertEqual(cooldown_scopes(bad, strict=False), ())
        self.assertEqual(cooldown_scopes({"id": "x", "postiz_workspace": "acme"}, strict=False), ())

    def test_scope_never_contains_or_derives_from_the_key(self):
        accounts = load_accounts(self.config)
        for account in accounts.values():
            for scope in cooldown_scopes(account):
                self.assertNotIn(KEY, scope)
                self.assertNotIn("REACHWEAVER_", scope)


# --------------------------------------------------------------------------- shared Postiz cooldowns

class SharedPostizCooldownTests(SharedBase):
    def test_429_on_a_blocks_b_pre_claim_without_network(self):
        a, b = self.approved("pz-a", "Note A"), self.approved("pz-b", "Note B")
        out = self.postiz_429_on(a)
        self.assertNotIn(MARKER, out)
        self.assertNotIn(KEY, out)
        [recorded] = self.events("provider_cooldown")
        self.assertIn("account=pz-a", recorded["detail"])
        self.assertIn(f"scope={ACME}", recorded["detail"])
        self.assertNotIn(KEY, recorded["detail"])
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--live", "--post-id", b)
        self.assertEqual((code, guard.attempts), (2, []))
        network.assert_not_called()
        self.assertIn(f"SKIPPED {b}: account_provider_cooldown_active", out)
        self.assertIn("account=pz-b", out)
        self.assertIn(f"scope={ACME}", out)
        self.assertIn("recorded_by=pz-a", out)
        self.assertEqual(self.status(b), "approved")
        self.assertEqual([e for e in self.events("send_claimed") if e["post_id"] == b], [])

    def test_claim_sending_under_lock_consults_group_scope(self):
        """Pre-claim gate passed (or a library caller skipped it); the claim itself refuses."""
        b, b2 = self.approved("pz-b", "Note B"), self.approved("pz-b", "Note B two")
        with connect(self.db) as db:
            record_provider_cooldown(db, "pz-a", now_s(600), "default", stage="send", scope=ACME)
            # Caller-supplied scopes, no recorded membership.
            with self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active.*recorded_by=pz-a"):
                claim_sending(db, b, cooldown_scopes=(ACME,))
            # A library caller that omits the scopes is covered by the recorded membership.
            sync_cooldown_scopes(db, {"pz-a": (ACME,), "pz-b": (ACME,)})
            with self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active"):
                claim_sending(db, b2)
            for pid in (b, b2):
                self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)
            # Without scope or membership the claim is per-account, exactly as before.
            sync_cooldown_scopes(db, {"pz-b": ()})
            self.assertTrue(claim_sending(db, b))
            with self.assertRaises(ValidationError):
                claim_sending(db, b2, cooldown_scopes="postiz-workspace:acme")  # a str is not a tuple

    def test_claim_lock_refuses_when_cooldown_lands_after_pre_claim_gate(self):
        """A 429 recorded by another worker between the pre-claim check and the claim."""
        b = self.approved("pz-b", "Note B")

        real_claim = claim_sending

        def racing_claim(con, post_id, *args, **kwargs):
            record_provider_cooldown(con, "pz-a", now_s(900), "retry_after", stage="send", scope=ACME)
            return real_claim(con, post_id, *args, **kwargs)

        wire = Wire({("GET", "/integrations"): [INTEGRATIONS], ("POST", "/public/v1/posts"): []})
        with patch("reachweaver.storage.claim_sending", racing_claim):
            code, out, _ = self.live(wire, "--post-id", b)
        self.assertEqual(code, 2, out)
        self.assertIn(f"BLOCKED {b}: account_provider_cooldown_active", out)
        self.assertEqual(wire.count("POST", "/public/v1/posts"), 0)
        self.assertEqual(self.status(b), "approved")

    def test_different_workspace_unaffected(self):
        a, c = self.approved("pz-a", "Note A"), self.approved("pz-c", "Note C")
        self.postiz_429_on(a)
        wire = Wire({("GET", "/integrations"): [INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [[{"postId": "job-c", "integration": "int-c"}]]})
        code, out, _ = self.live(wire, "--post-id", c)
        self.assertEqual(code, 3, out)  # Postiz acceptance -> needs_review, as before
        self.assertIn("accepted by Postiz backend (postiz:job-c)", out)
        self.assertEqual(wire.count("POST", "/public/v1/posts"), 1)

    def test_no_scope_is_per_account_as_today(self):
        d, e = self.approved("pz-d", "Note D"), self.approved("pz-e", "Note E")
        self.postiz_429_on(d)
        [recorded] = self.events("provider_cooldown")
        self.assertNotIn("scope=", recorded["detail"])
        wire = Wire({("GET", "/integrations"): [INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [[{"postId": "job-e", "integration": "int-e"}]]})
        code, out, _ = self.live(wire, "--post-id", e)
        self.assertEqual(code, 3, out)
        self.assertEqual(wire.count("POST", "/public/v1/posts"), 1)
        d2 = self.approved("pz-d", "Note D two")
        with NetworkGuard():
            code, out, _ = self.cli("publish", "--live", "--post-id", d2)
        self.assertEqual(code, 2)
        self.assertIn("account=pz-d", out)
        self.assertNotIn("recorded_by", out)

    def test_preflight_429_also_writes_group_scope(self):
        a, b = self.approved("pz-a", "Note A"), self.approved("pz-b", "Note B")
        wire = Wire({("GET", "/integrations"): [lambda: http(429)] * 3, ("POST", "/public/v1/posts"): []})
        code, out, _ = self.live(wire, "--post-id", a)
        self.assertEqual(code, 2, out)
        self.assertEqual(self.status(a), "approved")
        [recorded] = self.events("provider_cooldown")
        self.assertIn("stage=preflight", recorded["detail"])
        self.assertIn(f"scope={ACME}", recorded["detail"])
        with NetworkGuard() as guard:
            code, out, _ = self.cli("publish", "--live", "--post-id", b)
        self.assertEqual((code, guard.attempts), (2, []))
        self.assertIn("recorded_by=pz-a", out)

    def test_tighten_only_latest_deadline_wins_across_account_and_scope(self):
        with connect(self.db) as db:
            own = record_provider_cooldown(db, "pz-b", now_s(7200), "retry_after", stage="send")
            record_provider_cooldown(db, "pz-a", now_s(120), "default", stage="send", scope=ACME)
            hit = active_provider_cooldown(db, "pz-b", scopes=(ACME,))
            self.assertEqual(hit["until"], own["until"])  # the shorter scope deadline never shortens it
            self.assertEqual(hit["recorded_by"], "pz-b")
            later = record_provider_cooldown(db, "pz-a", now_s(9000), "retry_after", stage="send", scope=ACME)
            hit = active_provider_cooldown(db, "pz-b", scopes=(ACME,))
            self.assertEqual((hit["until"], hit["recorded_by"]), (later["until"], "pz-a"))
            self.assertIn("scope=" + ACME, cooldown_refusal(hit))
            self.assertIsNone(active_provider_cooldown(db, "pz-c", scopes=(POSTIZ_WORKSPACE_SCOPE + "other-org",)))
            # The listing stays one entry per recording account.
            self.assertEqual([c["account_id"] for c in provider_cooldowns(db)], ["pz-a", "pz-b"])
            with self.assertRaisesRegex(ValidationError, "scope"):
                active_provider_cooldown(db, "pz-b", scopes=("bad scope",))
            with self.assertRaisesRegex(ValidationError, "scope"):
                record_provider_cooldown(db, "pz-a", now_s(60), "default", stage="send", scope="UPPER:x")

    def test_malformed_scope_token_never_widens(self):
        with connect(self.db) as db:
            event(db, "provider_cooldown", None, f"account=pz-a until={now_s(600)} source=default "
                  "reason=http_429 stage=send scope=NOT_A_SCOPE")
            self.assertIsNotNone(active_provider_cooldown(db, "pz-a"))  # the account itself still waits
            self.assertIsNone(active_provider_cooldown(db, "pz-b", scopes=(ACME,)))

    def test_membership_sync_replaces_only_loaded_accounts(self):
        with connect(self.db) as db:
            sync_cooldown_scopes(db, {"pz-a": (ACME,), "pz-b": (ACME,)})
            sync_cooldown_scopes(db, {"pz-a": ()})  # pz-a left the workspace; pz-b not loaded
            self.assertEqual(recorded_cooldown_scopes(db, "pz-a"), ())
            self.assertEqual(recorded_cooldown_scopes(db, "pz-b"), (ACME,))
            with self.assertRaises(ValidationError):
                sync_cooldown_scopes(db, {"pz-a": ("not a scope",)})
            self.assertEqual(recorded_cooldown_scopes(db, "pz-b"), (ACME,))

    def test_dry_run_shows_shared_cooldown_without_loading_accounts(self):
        a, b = self.approved("pz-a", "Note A"), self.approved("pz-b", "Note B")
        self.postiz_429_on(a)  # the live run recorded the membership
        offline = {k: v for k, v in ENV.items() if k not in ("REACHWEAVER_ALLOW_LIVE", "REACHWEAVER_ENV")}
        boom = AssertionError("dry run loaded accounts")
        with NetworkGuard() as guard, patch("reachweaver.cli.load_accounts", side_effect=boom) as loader:
            code, out, _ = self.cli("publish", env=offline)
        self.assertEqual(code, 0, out)
        loader.assert_not_called()
        self.assertEqual(guard.attempts, [])
        self.assertIn(f"shared scope {ACME}", out)
        self.assertIn(f"SKIPPED {b}: account_provider_cooldown_active", out)
        self.assertIn("recorded_by=pz-a", out)
        self.assertEqual(self.status(b), "approved")

    def test_health_reports_shared_cooldown(self):
        a, _b = self.approved("pz-a", "Note A"), self.approved("pz-b", "Note B")
        self.postiz_429_on(a)
        with NetworkGuard(), connect(self.db) as db:
            report = build_health(db, load_accounts(self.config), env=ENV)
        row = next(r for r in report["accounts"] if r["account_id"] == "pz-b")
        self.assertEqual(row["provider_cooldown"]["recorded_by"], "pz-a")
        self.assertEqual(row["rate_limits"]["postiz_workspace"], "acme")
        self.assertIn("provider_cooldown_active", {r["code"] for r in row["reasons"]})
        other = next(r for r in report["accounts"] if r["account_id"] == "pz-c")
        self.assertIsNone(other["provider_cooldown"])


# --------------------------------------------------------------------------- migration

OLD_SCHEMA = """
CREATE TABLE posts (id TEXT PRIMARY KEY, account_id TEXT NOT NULL, platform TEXT NOT NULL,
  content TEXT NOT NULL, campaign TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, approved_at TEXT, scheduled_at TEXT,
  remote_id TEXT, remote_url TEXT, last_error TEXT, send_started_at TEXT);
CREATE TABLE event_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, post_id TEXT,
  action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class MigrationTests(SharedBase):
    def make_old_db(self):
        Path(self.db).parent.mkdir(mode=0o700)
        con = sqlite3.connect(self.db)
        con.executescript(OLD_SCHEMA)
        # A cooldown row written before this change: no scope= token.
        con.execute("INSERT INTO event_log(at,post_id,action,detail) VALUES(?,?,?,?)",
                    (now_s(), None, "provider_cooldown",
                     f"account=pz-a until={now_s(600)} source=default reason=http_429 stage=send"))
        con.commit()
        con.close()
        os.chmod(self.db, 0o600)

    def test_old_db_gains_membership_table_idempotently_and_legacy_rows_stay_per_account(self):
        self.make_old_db()
        with connect_readonly(self.db) as ro:  # read-only path on the old file: no table, no error
            self.assertEqual(recorded_cooldown_scopes(ro, "pz-a"), ())
            self.assertIsNotNone(active_provider_cooldown(ro, "pz-a"))
            self.assertIn("cooldown_scope_members", schema_gaps(ro))
        for _ in range(2):  # idempotent
            with connect(self.db) as db:
                self.assertEqual(schema_gaps(db), [])
                tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name='cooldown_scope_members'")]
                self.assertEqual(tables, ["cooldown_scope_members"])
                legacy = active_provider_cooldown(db, "pz-a")
                self.assertIsNone(legacy["scope"])
                self.assertIsNone(active_provider_cooldown(db, "pz-b", scopes=(ACME,)))  # never widened
        with connect(self.db) as db:
            sync_cooldown_scopes(db, {"pz-a": (ACME,), "pz-b": (ACME,)})
            record_provider_cooldown(db, "pz-a", now_s(900), "default", stage="send", scope=ACME)
            self.assertEqual(active_provider_cooldown(db, "pz-b")["recorded_by"], "pz-a")


# --------------------------------------------------------------------------- Bluesky createSession budget

class CreateSessionBudgetTests(SharedBase):
    def seed(self, account_id, count, *, age_seconds=30):
        account = load_accounts(self.config)[account_id]
        scope = session_scope(account)
        with connect(self.db) as db:
            for _ in range(count):
                event(db, SESSION_EVENT, None, f"account={account_id} scope={scope} stage=preflight",
                      at=now_s(-age_seconds))
        return scope

    def test_windows_follow_registry_facts_and_only_tighten(self):
        windows = {w["window_seconds"]: w for w in session_windows()}
        self.assertEqual({k: (w["documented_limit"], w["local_limit"]) for k, w in windows.items()},
                         {300: (30, 15), 86400: (300, 150)})
        lower = {"platforms": [{"id": "bluesky", "rate_limits": {"facts": [
            {"scope": "account", "action": "com.atproto.server.createSession", "limit": 10,
             "unit": "requests", "window_seconds": 300},
            {"scope": "account", "action": "com.atproto.server.createSession", "limit": 9000,
             "unit": "requests", "window_seconds": 86400},
            {"scope": "account", "action": "com.atproto.server.createSession", "limit": 4,
             "unit": "requests", "window_seconds": 60}]}}]}
        got = {w["window_seconds"]: w["local_limit"] for w in session_windows(lower)}
        self.assertEqual(got, {60: 2, 300: 5, 86400: 150})  # 9000/day never loosens the 300/day fact
        self.assertEqual({w["window_seconds"] for w in session_windows({"platforms": []})}, {300, 86400})

    def test_scope_is_a_digest_shared_by_entries_for_one_account(self):
        accounts = load_accounts(self.config)
        one, two = session_scope(accounts["sc-bluesky"]), session_scope(accounts["sc-bluesky-two"])
        self.assertEqual(one, two)
        self.assertRegex(one, r"^bsky-account:[0-9a-f]{16}$")
        self.assertNotIn("owner", one)
        other = dict(accounts["sc-bluesky"], service="https://pds.example.org")
        self.assertNotEqual(session_scope(other), one)

    def test_publish_refused_before_any_request_when_budget_exhausted(self):
        self.seed("sc-bluesky", 14)  # 14 + 2 > 15 in the 5-minute window
        pid = self.approved("sc-bluesky", "Bluesky note")
        log = self.base / "worker.jsonl"
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http") as network, \
                patch("reachweaver.cli.check_connection") as identity:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, "--log-json", "--log-file", str(log))
        self.assertEqual(code, 2, out)
        self.assertEqual(guard.attempts, [])
        network.assert_not_called()
        identity.assert_not_called()
        self.assertIn(f"SKIPPED {pid}: {SESSION_REFUSAL}", out)
        self.assertIn("window=300s used=14 needed=2 local_limit=15", out)
        self.assertEqual(self.status(pid), "approved")
        self.assertEqual(self.events("send_claimed"), [])
        [refused] = self.events(SESSION_REFUSED_EVENT)
        self.assertIn("account=sc-bluesky", refused["detail"])
        self.assertNotIn("owner", refused["detail"])
        self.assertEqual(len(self.events(SESSION_EVENT)), 14)  # a refusal reserves nothing
        reasons = [json.loads(line).get("reason") for line in log.read_text().splitlines()]
        self.assertIn("session_budget", reasons)

    def test_daily_window_and_shared_account_entries(self):
        self.seed("sc-bluesky", 149, age_seconds=3600)  # outside 5 min, inside 24 h
        pid = self.approved("sc-bluesky-two", "Bluesky note")  # other entry, same Bluesky account
        with NetworkGuard() as guard:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual((code, guard.attempts), (2, []))
        self.assertIn("window=86400s used=149", out)

    def test_reservation_counts_both_sessions_even_when_preflight_fails(self):
        pid = self.approved("sc-bluesky", "Bluesky note")
        wire = Wire({("POST", "createSession"): [lambda: http(503)], ("POST", "repo.createRecord"): []})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertEqual(wire.count("POST", "createSession"), 1)
        stages = [e["detail"].split("stage=")[1] for e in self.events(SESSION_EVENT)]
        self.assertEqual(stages, ["preflight", "send_reserved"])
        self.assertEqual(SESSIONS_PER_PUBLISH, 2)

    def test_reserved_publish_reaches_claim_and_is_never_resent(self):
        pid = self.approved("sc-bluesky", "Bluesky note")
        wire = Wire({("POST", "createSession"): [SESSION, SESSION], ("POST", "repo.createRecord"): [TimeoutError()]})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        self.assertEqual(self.status(pid), "needs_review")
        self.assertEqual(wire.count("POST", "createSession"), 2)
        self.assertEqual(len(self.events(SESSION_EVENT)), 2)

    def test_connect_check_refused_before_request_and_metered(self):
        self.seed("sc-bluesky", 15)
        with NetworkGuard() as guard, patch("reachweaver.cli.check_connection") as identity:
            code, _, err = self.cli("connect-check", "--account", "sc-bluesky", "--online")
        self.assertEqual((code, guard.attempts), (2, []))
        identity.assert_not_called()
        self.assertIn(SESSION_REFUSAL, err)
        with connect(self.db) as db:
            db.execute("DELETE FROM event_log WHERE action=?", (SESSION_EVENT,))
        from reachweaver.connections import VerifiedConnection
        with patch("reachweaver.cli.check_connection",
                   return_value=VerifiedConnection("sc-bluesky", "bluesky", "verified", "unverified")):
            code, out, _ = self.cli("connect-check", "--account", "sc-bluesky", "--online")
        self.assertEqual(code, 0, out)
        [one] = self.events(SESSION_EVENT)
        self.assertIn("stage=connect_check", one["detail"])

    def test_reserve_is_atomic_and_validates(self):
        account = load_accounts(self.config)["sc-bluesky"]
        with connect(self.db) as db:
            for _ in range(7):
                reserve_bluesky_sessions(db, account, needed=2, stage="preflight")
            with self.assertRaisesRegex(ValidationError, SESSION_REFUSAL):
                reserve_bluesky_sessions(db, account, needed=2, stage="preflight")
            reserve_bluesky_sessions(db, account, needed=1, stage="connect_check")  # exactly 15
            with self.assertRaisesRegex(ValidationError, SESSION_REFUSAL):
                reserve_bluesky_sessions(db, account, needed=1, stage="connect_check")
            for bad in ({"needed": 0, "stage": "preflight"}, {"needed": 5, "stage": "preflight"},
                        {"needed": 1, "stage": "send"}):
                with self.assertRaises(ValidationError):
                    reserve_bluesky_sessions(db, account, **bad)
            self.assertFalse(db.in_transaction)

    def test_dry_run_previews_budget_without_accounts(self):
        self.seed("sc-bluesky", 14)
        pid = self.approved("sc-bluesky", "Bluesky note")
        offline = {k: v for k, v in ENV.items() if k not in ("REACHWEAVER_ALLOW_LIVE", "REACHWEAVER_ENV")}
        with NetworkGuard() as guard, patch("reachweaver.cli.load_accounts", side_effect=AssertionError) as loader:
            code, out, _ = self.cli("publish", env=offline)
        self.assertEqual((code, guard.attempts), (0, []))
        loader.assert_not_called()
        self.assertIn(f"SKIPPED {pid}: {SESSION_REFUSAL}", out)
        self.assertEqual(len(self.events(SESSION_EVENT)), 14)  # a preview reserves nothing

    def test_health_shows_budget_and_purge_keeps_recent_session_events(self):
        self.seed("sc-bluesky", 3)
        with connect(self.db) as db:
            report = build_health(db, load_accounts(self.config), env=ENV)
            row = next(r for r in report["accounts"] if r["account_id"] == "sc-bluesky")
            used = {w["window_seconds"]: w["used"] for w in row["rate_limits"]["create_session_budget"]}
            self.assertEqual(used, {300: 3, 86400: 3})
            plan = plan_purge(db, {"event_log_days": 1}, now=now_s(3 * 3600))
            self.assertEqual(plan["protected"]["recent_create_session_events"], 3)
            forged = dict(plan, actions=dict(plan["actions"], event_log_delete=[
                r[0] for r in db.execute("SELECT id FROM event_log WHERE action=?", (SESSION_EVENT,))]))
            apply_purge(db, forged, now=now_s(3 * 3600))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action=?",
                                        (SESSION_EVENT,)).fetchone()[0], 3)

    def test_postiz_backed_bluesky_is_not_metered(self):
        """Postiz, not ReachWeaver, opens the Bluesky session for a backend account."""
        pid = self.approved("pz-bsky", "Postiz note")
        wire = Wire({("GET", "/integrations"): [INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [[{"postId": "job-f", "integration": "int-f"}]]})
        code, out, _ = self.live(wire, "--post-id", pid)
        self.assertEqual(code, 3, out)
        self.assertEqual(self.events(SESSION_EVENT), [])
        self.assertEqual(ratelimit.SESSIONS_PER_PUBLISH, 2)


if __name__ == "__main__":
    unittest.main()
