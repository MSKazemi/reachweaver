"""``publish --log-json`` structured worker logs (issue #26, slice S11). Every test is offline.

The log is observational: these tests pin the event schema and fixed vocabulary, prove
that post text, third-party handles, secrets, provider error text and URL query strings
never reach a log line, that ``--log-file`` is created 0600 and refused before any work
when it cannot be used, and that ``publish`` changes nothing (database rows, provider
requests, stdout, exit code) when logging is switched on, in a dry run and in a mocked
live run. Mocked live runs patch the environment with the live opt-in and the production
marker through ``patch.dict`` like ``tests/test_hardening.py``; every HTTP request goes to
a scripted fake at ``publishers._send_http`` and ``NetworkGuard`` refuses sockets.
"""
from __future__ import annotations

import contextlib
import email.message
import io
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from test_no_network import NetworkGuard
from gate_support import bound_send_options

from reachweaver import accounts as accounts_module
from reachweaver.cli import main
from reachweaver.publishers import PublishError
from reachweaver.ratelimit import record_rate_limit
from reachweaver.storage import add_post, connect, transition
from reachweaver.worker_log import (
    COUNT_KEYS, EVENTS, GATE_REASONS, SEND_CLASSES, SKIP_REASONS, WorkerLog, claim_refusal_reason,
    new_run_id, open_worker_log, safe_remote_url,
)

POSIX = os.name == "posix"
# Built at runtime so the tracked-file secret scan never sees a credential-shaped literal. Never real.
BSKY_PASSWORD = "-".join(("q7sl", "26fk", "x9zz", "m3tq"))
MASTO_TOKEN = "-".join(("FAKE", "s11", "mastodon", "token", "0001"))
MASTO2_TOKEN = "-".join(("FAKE", "s11", "mastodon", "token", "0002"))
GH_TOKEN = "ghp_" + "FAKEs11githubTOKENnotREAL0005abcdefgh"
JWT = ".".join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJzMTEifQ", "c2lnbmF0dXJl"))
EMAIL = "alice.s11@example.com"
HANDLE = "@mallory-s11.bsky.social"
QUERY_SECRET = "qs-" + "s11-secret-value"
DID = "did:plc:structuredlog11"
CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"
COMMON_KEYS = {"schema_version", "run_id", "seq", "ts", "event", "dry_run"}
POST_KEYS = {"post_id", "account_id", "platform"}
EVENT_KEYS = {
    "run_start": COMMON_KEYS | {"live_requested", "scheduled_only", "limit", "post_id"},
    "gate_refused": COMMON_KEYS | {"reason"},
    "post_skipped": COMMON_KEYS | POST_KEYS | {"reason"},
    "post_claimed": COMMON_KEYS | POST_KEYS | {"backend"},
    "post_published": COMMON_KEYS | POST_KEYS | {"backend", "remote_id", "remote_url"},
    "post_needs_review": COMMON_KEYS | POST_KEYS | {"backend", "send_class", "error_code", "rate_limited"},
    "run_end": COMMON_KEYS | {"outcome", "exit_code", "error_code", "counts", "dropped_lines"},
}
CREDENTIALS = {
    "REACHWEAVER_SLA_IDENTIFIER": "owner.bsky.social",
    "REACHWEAVER_SLA_APP_PASSWORD": BSKY_PASSWORD,
    "REACHWEAVER_SLB_TOKEN": MASTO_TOKEN,
    "REACHWEAVER_SLC_TOKEN": MASTO2_TOKEN,
    "REACHWEAVER_GITHUB_TOKEN": GH_TOKEN,
}
SECRETS = (BSKY_PASSWORD, MASTO_TOKEN, MASTO2_TOKEN, GH_TOKEN, JWT, QUERY_SECRET)


def prod_env(**extra):
    """The live opt-in and the production marker, as in tests/test_hardening.py."""
    return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", **extra}


class FakeResponse:
    def __init__(self, data):
        self.raw = json.dumps(data).encode("utf-8")
        self.status = 200
        self.headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self.raw[:limit] if limit and limit > 0 else self.raw


class FakeWire:
    """Scripted Bluesky/Mastodon replies; records every request, opens no socket.

    Account SLB publishes with a receipt URL carrying a query string; account SLC's create
    request fails with HTTP 503 whose body holds secret-shaped text (never read)."""

    def __init__(self):
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        url = req.full_url
        auth = req.get_header("Authorization") or ""
        if url.endswith("/xrpc/com.atproto.server.createSession"):
            return FakeResponse({"did": DID, "handle": "owner.bsky.social", "accessJwt": "s11-access"})
        if url.endswith("/xrpc/com.atproto.repo.createRecord"):
            rkey = json.loads(req.data)["rkey"]
            return FakeResponse({"uri": f"at://{DID}/app.bsky.feed.post/{rkey}", "cid": CID})
        username = "owner2" if MASTO2_TOKEN in auth else "owner"
        if url.endswith("/api/v1/accounts/verify_credentials"):
            return FakeResponse({"id": "7", "username": username})
        if url.endswith("/api/v1/statuses"):
            if username == "owner2":
                body = io.BytesIO(f"upstream error token={GH_TOKEN} contact {EMAIL} {HANDLE}".encode())
                raise HTTPError(url, 503, "Service Unavailable", email.message.Message(), body)
            return FakeResponse({"id": "1001", "account": {"username": "owner"}, "visibility": "public",
                                 "url": f"https://mastodon.example.org/@owner/1001?token={QUERY_SECRET}"
                                        f"&email={EMAIL}#frag"})
        raise AssertionError("unexpected request")


def wire_calls(wire: FakeWire) -> list:
    """Every request as (method, URL, body, headers); Bluesky's wall-clock createdAt is normalized."""
    calls = []
    for req in wire.requests:
        body = req.data
        if req.full_url.endswith("/xrpc/com.atproto.repo.createRecord"):
            data = json.loads(body)
            data["record"]["createdAt"] = "<time>"
            body = json.dumps(data, sort_keys=True).encode()
        calls.append((req.get_method(), req.full_url, body, sorted(req.header_items())))
    return calls


def db_dump(path: Path) -> dict:
    """Every row of every table, with wall-clock columns normalized (the two runs differ in time)."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        out = {}
        for table in ("posts", "event_log", "settings", "metric_snapshots", "campaign_variants"):
            rows = []
            for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                item = dict(row)
                for column in ("at", "send_started_at"):
                    if item.get(column) is not None:
                        item[column] = "<time>"
                rows.append(item)
            out[table] = rows
        return out
    finally:
        con.close()


class _Workspace(unittest.TestCase):
    """A private temp dir as CWD (no checkout config is read) and a REACHWEAVER_*-free environment."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        os.chmod(self.base, 0o700)
        cwd = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(accounts_module._LOADED_FILE_CREDENTIALS.clear)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        self.db = self.state / "rw.sqlite"
        self.config = self.base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "sl-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_SLA", "handle": "owner.bsky.social"},
            {"id": "sl-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_SLB",
             "server": "https://mastodon.example.org", "handle": "owner"},
            {"id": "sl-masto-two", "platform": "mastodon", "env_prefix": "REACHWEAVER_SLC",
             "server": "https://mastodon.example.org", "handle": "owner2"},
        ]}), encoding="utf-8")
        self.optout = self.base / "optout.json"
        self.optout.write_text(json.dumps({"version": 1, "entries": [{"handle": HANDLE}]}), encoding="utf-8")
        self.base_env = {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}

    def snapshot(self, name: str) -> Path:
        """A copy of the seeded database in its own private directory, WITH its approval ledger (wave 9
        item 1: the ledger sits next to the database; a copy without it would fail closed)."""
        folder = self.state / name
        folder.mkdir(mode=0o700)
        target = folder / "rw.sqlite"
        shutil.copy(self.db, target)
        ledger_file = self.db.parent / "approval-ledger.jsonl"
        if ledger_file.exists():
            shutil.copy(ledger_file, folder / ledger_file.name)
        return target

    def seed(self) -> dict:
        """Due approved posts covering every live decision; content carries secrets and personal data."""
        ids = {}
        with connect(str(self.db)) as con:
            def approved(key, account, platform, text):
                ids[key] = add_post(con, account, platform, text, "s11")
                # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
                transition(con, ids[key], "approve", approver="test-owner",
                           send_options=bound_send_options(self.config, account))
                # created_at is second-resolution; spread it so the due order is fixed.
                con.execute("UPDATE posts SET created_at=? WHERE id=?",
                            (f"2026-10-01T00:00:{len(ids):02d}+00:00", ids[key]))
            approved("published_bsky", "sl-bsky", "bluesky",
                     f"Release notes {EMAIL} https://example.org/r?token={QUERY_SECRET} {GH_TOKEN}")
            approved("published_masto", "sl-masto", "mastodon", f"Mastodon note {JWT}")
            approved("review", "sl-masto-two", "mastodon", f"Second account note password={BSKY_PASSWORD}")
            approved("optout", "sl-bsky", "bluesky", f"Thanks {HANDLE} for the report")
            approved("rebind", "sl-bsky", "bluesky", "Text that will change after approval")
            con.execute("UPDATE posts SET content=? WHERE id=?", (f"Changed {EMAIL}", ids["rebind"]))
            approved("budget", "sl-bsky", "bluesky", "A second Bluesky post inside the minimum gap")
            approved("missing", "sl-ghost", "bluesky", "Account no longer configured")
        return ids

    def cli(self, *argv, env=None, stderr=None, db=None, config=True):
        out, err = io.StringIO(), stderr if stderr is not None else io.StringIO()
        base = ["--db", str(db or self.db)] + (["--accounts", str(self.config)] if config else [])
        with patch.dict(os.environ, {**self.base_env, **(env or {})}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*base, *map(str, argv)])
        return code, out.getvalue(), err.getvalue() if stderr is None else ""

    def live(self, *argv, db=None, stderr=None):
        """A mocked live run: every request goes to a fresh FakeWire; sockets are refused."""
        wire = FakeWire()
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http", side_effect=wire):
            code, out, err = self.cli("publish", "--live", "--optout", self.optout, *argv,
                                      env=prod_env(**CREDENTIALS), db=db, stderr=stderr)
        self.assertEqual(guard.attempts, [])
        return code, out, err, wire

    @staticmethod
    def records(text: str) -> list[dict]:
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def assert_schema(self, records: list[dict]):
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["event"], "run_start")
        self.assertEqual(records[-1]["event"], "run_end")
        self.assertEqual([r["seq"] for r in records], list(range(1, len(records) + 1)))
        self.assertEqual(len({r["run_id"] for r in records}), 1)
        for record in records:
            self.assertIn(record["event"], EVENTS)
            self.assertEqual(set(record), EVENT_KEYS[record["event"]] | (
                {"emergency_paused"} if record["event"] == "run_end" and record["dry_run"] else set()) | (
                {"rate_limited"} if record["event"] == "post_skipped" and "rate_limited" in record else set()),
                record["event"])
            self.assertEqual(record["schema_version"], 1)
            self.assertIsInstance(record["dry_run"], bool)
            self.assertIsNotNone(datetime.fromisoformat(record["ts"]).tzinfo)
            if record["event"] == "post_skipped":
                self.assertIn(record["reason"], SKIP_REASONS)
            if record["event"] == "gate_refused":
                self.assertIn(record["reason"], GATE_REASONS)
            if record["event"] == "post_needs_review":
                self.assertIn(record["send_class"], SEND_CLASSES)
                self.assertRegex(record["error_code"], r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?$")
            if record["event"] == "run_end":
                self.assertEqual(set(record["counts"]), set(COUNT_KEYS))

    def assert_no_leak(self, text: str):
        for needle in (*SECRETS, EMAIL, HANDLE, HANDLE.lstrip("@"), "Release notes", "Mastodon note", "Thanks",
                       "password=", "token=", "?", "#frag", "upstream error", "REACHWEAVER_SL"):
            self.assertNotIn(needle, text, f"log leaked {needle[:8]}…")


# --------------------------------------------------------------------------- vocabulary and helpers

class VocabularyTests(unittest.TestCase):
    def test_fixed_event_and_reason_vocabulary(self):
        self.assertEqual(EVENTS, ("run_start", "gate_refused", "post_skipped", "post_claimed", "post_published",
                                  "post_needs_review", "run_end"))
        self.assertEqual(GATE_REASONS, ("live_gate", "emergency_pause", "human_confirmation"))
        for code in ("dry_run", "not_due", "paused", "optout_blocked", "budget", "cooldown", "rebind_required",
                     "account_missing", "identity_preflight_failed", "not_eligible", "backend_refused"):
            self.assertIn(code, SKIP_REASONS)
        for code in (*EVENTS, *SKIP_REASONS, *GATE_REASONS):
            self.assertRegex(code, r"^[a-z][a-z_]+$")

    def test_unknown_codes_are_mapped_never_echoed(self):
        stream = io.StringIO()
        log = WorkerLog(stream, dry_run=True)
        log.start(live_requested=False, scheduled_only=False, limit=10, post_id=None)
        log.skipped({"id": "p1", "account_id": "sl-bsky", "platform": "bluesky"}, f"free text {EMAIL}")
        log.gate_refused("something else")
        log.needs_review({"id": "p1", "account_id": "sl-bsky", "platform": "bluesky"},
                         send_class="weird", code=f"Free {EMAIL}")
        log._write("not_an_event", {"x": 1})  # a programming error is dropped and counted, not raised
        log.end(exit_code=0)
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([r["event"] for r in records],
                         ["run_start", "post_skipped", "gate_refused", "post_needs_review", "run_end"])
        self.assertEqual(records[1]["reason"], "claim_refused")
        self.assertEqual(records[2]["reason"], "live_gate")
        self.assertEqual((records[3]["send_class"], records[3]["error_code"]), ("unknown", "unclassified"))
        self.assertEqual(records[-1]["dropped_lines"], 1)
        self.assertNotIn(EMAIL, stream.getvalue())

    def test_hostile_identifiers_are_replaced(self):
        stream = io.StringIO()
        log = WorkerLog(stream, dry_run=False)
        log.start(live_requested=True, scheduled_only=False, limit=1, post_id=f"x {EMAIL}")
        log.published({"id": "p 1?x", "account_id": f"https://evil.example/?token={GH_TOKEN}", "platform": HANDLE},
                      f"id with {GH_TOKEN} spaces", f"https://user:{QUERY_SECRET}@evil.example/p?token={QUERY_SECRET}")
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(lines[0]["post_id"], "invalid")
        self.assertEqual((lines[1]["post_id"], lines[1]["account_id"], lines[1]["platform"]),
                         ("invalid", "invalid", "other"))
        self.assertEqual(lines[1]["remote_id"], "invalid")
        self.assertEqual(lines[1]["remote_url"], "https://evil.example/p")
        for needle in (GH_TOKEN, QUERY_SECRET, EMAIL, "evil.example/?"):
            self.assertNotIn(needle, stream.getvalue())

    def test_remote_url_keeps_scheme_host_path_only(self):
        self.assertEqual(safe_remote_url("https://bsky.app/profile/did:plc:x/post/3k?ref=a#c"),
                         "https://bsky.app/profile/did:plc:x/post/3k")
        self.assertEqual(safe_remote_url("https://mastodon.example.org:8443/@owner/1"),
                         "https://mastodon.example.org:8443/@owner/1")
        self.assertEqual(safe_remote_url("javascript:alert(1)"), "invalid")
        self.assertIsNone(safe_remote_url(None))

    def test_claim_refusals_map_to_codes(self):
        from reachweaver.storage import BINDING_MISMATCH, COOLDOWN_REFUSAL
        from reachweaver.policy import ValidationError
        cases = {"account_rolling_24h_send_attempt_cap_reached": "budget",
                 "account_minimum_send_attempt_gap_not_elapsed": "budget",
                 COOLDOWN_REFUSAL + " until=x account=y source=z; post stays approved/scheduled": "cooldown",
                 BINDING_MISMATCH + ": text": "rebind_required",
                 "Global emergency pause is active. Run 'resume' deliberately.": "paused",
                 "anything else": "claim_refused"}
        for message, code in cases.items():
            self.assertEqual(claim_refusal_reason(ValidationError(message)), code)

    def test_run_ids_strictly_increase(self):
        ids = [new_run_id() for _ in range(200)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))

    def test_disabled_log_writes_nothing_and_reads_nothing(self):
        log = open_worker_log(False, None, dry_run=True)
        self.assertFalse(log.enabled)
        log.start(live_requested=False, scheduled_only=False, limit=1, post_id=None)
        boom = Mock(side_effect=AssertionError("disabled log must not read"))
        self.assertIsNone(log.probe(boom))
        log.backend_result(boom, {"id": "p"}, 3, backend="postiz")
        boom.assert_not_called()


# --------------------------------------------------------------------------- dry run

class DryRunLogTests(_Workspace):
    def test_dry_run_logs_one_decision_per_post_in_due_order(self):
        ids = self.seed()
        with connect(str(self.db)) as con:
            record_rate_limit(con, {"id": "sl-masto-two", "platform": "mastodon"},
                              PublishError("http_429", status=429), stage="preflight")
        code, out, err = self.cli("publish", "--log-json", "--optout", self.optout)
        self.assertEqual(code, 0, err)
        records = self.records(err)
        self.assert_schema(records)
        self.assertTrue(all(r["dry_run"] for r in records))
        skipped = [(r["post_id"], r["reason"]) for r in records if r["event"] == "post_skipped"]
        self.assertEqual(skipped, [
            (ids["published_bsky"], "dry_run"), (ids["published_masto"], "dry_run"), (ids["review"], "cooldown"),
            (ids["optout"], "optout_blocked"), (ids["rebind"], "rebind_required"), (ids["budget"], "dry_run"),
            (ids["missing"], "dry_run")])
        end = records[-1]
        self.assertEqual((end["outcome"], end["exit_code"], end["emergency_paused"]), ("completed", 0, False))
        self.assertEqual(end["counts"], {"due": 7, "skipped": 7, "claimed": 0, "published": 0, "needs_review": 0})
        self.assert_no_leak(err)

    def test_dry_run_while_paused_marks_every_post_paused(self):
        self.seed()
        self.assertEqual(self.cli("pause")[0], 0)
        code, _, err = self.cli("publish", "--log-json", "--optout", self.optout)
        self.assertEqual(code, 0)
        records = self.records(err)
        self.assertEqual({r["reason"] for r in records if r["event"] == "post_skipped"}, {"paused"})
        self.assertTrue(records[-1]["emergency_paused"])

    def test_not_due_post_id_is_logged_then_refused(self):
        code, _, err = self.cli("publish", "--log-json", "--post-id", "deadbeefdeadbeef")
        self.assertEqual(code, 2)
        lines = [line for line in err.splitlines() if line.startswith("{")]
        records = self.records("\n".join(lines))
        self.assertEqual([r["event"] for r in records], ["run_start", "post_skipped", "run_end"])
        self.assertEqual((records[1]["post_id"], records[1]["reason"]), ("deadbeefdeadbeef", "not_due"))
        self.assertEqual((records[2]["outcome"], records[2]["exit_code"], records[2]["error_code"]),
                         ("error", 2, "validation_error"))
        self.assertIn("ERROR: Selected post is not approved and due", err)

    def test_without_flag_nothing_is_logged(self):
        self.seed()
        code, _, err = self.cli("publish", "--optout", self.optout)
        self.assertEqual((code, err), (0, ""))


# --------------------------------------------------------------------------- mocked live run

class LiveLogTests(_Workspace):
    def test_live_run_schema_ordering_and_codes(self):
        ids = self.seed()
        code, out, err, wire = self.live("--log-json")
        self.assertEqual(code, 0, out + err)
        records = self.records(err)
        self.assert_schema(records)
        self.assertFalse(any(r["dry_run"] for r in records))
        decisions = [(r["event"], r.get("post_id"), r.get("reason")) for r in records[1:-1]]
        self.assertEqual(decisions, [
            ("post_claimed", ids["published_bsky"], None), ("post_published", ids["published_bsky"], None),
            ("post_claimed", ids["published_masto"], None), ("post_published", ids["published_masto"], None),
            ("post_claimed", ids["review"], None), ("post_needs_review", ids["review"], None),
            ("post_skipped", ids["optout"], "optout_blocked"), ("post_skipped", ids["rebind"], "rebind_required"),
            ("post_skipped", ids["budget"], "budget"), ("post_skipped", ids["missing"], "account_missing")])
        by_event = {(r["event"], r["post_id"]): r for r in records if "post_id" in r and r["event"] != "run_start"}
        bsky = by_event[("post_published", ids["published_bsky"])]
        self.assertEqual((bsky["account_id"], bsky["platform"], bsky["backend"]), ("sl-bsky", "bluesky", None))
        self.assertTrue(bsky["remote_id"].startswith(f"at://{DID}/app.bsky.feed.post/"))
        self.assertTrue(bsky["remote_url"].startswith(f"https://bsky.app/profile/{DID}/post/"))
        masto = by_event[("post_published", ids["published_masto"])]
        self.assertEqual((masto["remote_id"], masto["remote_url"]), ("1001", "https://mastodon.example.org/@owner/1001"))
        review = by_event[("post_needs_review", ids["review"])]
        self.assertEqual((review["send_class"], review["error_code"], review["rate_limited"]),
                         ("ambiguous", "http_503", False))
        end = records[-1]
        self.assertEqual((end["outcome"], end["exit_code"], end["error_code"]), ("completed", 0, None))
        self.assertEqual(end["counts"], {"due": 7, "skipped": 4, "claimed": 3, "published": 2, "needs_review": 1})
        # The stored receipt keeps the query string; only the log line drops it.
        with connect(str(self.db)) as con:
            stored = con.execute("SELECT remote_url FROM posts WHERE id=?", (ids["published_masto"],)).fetchone()[0]
        self.assertIn("?token=", stored)

    def test_live_log_never_leaks_content_handles_secrets_or_queries(self):
        self.seed()
        code, out, err, _ = self.live("--log-json")
        self.assertEqual(code, 0, out + err)
        self.assert_no_leak(err)

    def test_adapter_error_text_reduces_to_fixed_codes(self):
        self.seed()
        failures = [PublishError(f"provider said token={GH_TOKEN} {EMAIL}"), RuntimeError(f"boom {MASTO_TOKEN} {HANDLE}")]
        with patch("reachweaver.cli.publish", side_effect=failures):
            code, out, err, _ = self.live("--log-json", "--limit", "2")
        self.assertEqual(code, 0, out + err)
        reviews = [r for r in self.records(err) if r["event"] == "post_needs_review"]
        self.assertEqual([(r["error_code"], r["send_class"]) for r in reviews],
                         [("publish_error", "certain_not_sent"), ("unexpected_error_uncertain", "ambiguous")])
        self.assert_no_leak(err)

    def test_live_gate_refusal_is_logged_before_any_account(self):
        self.seed()
        boom = AssertionError("gate let work through")
        for env, expected in (({"REACHWEAVER_ENV": "prod"}, "live_gate"),
                              ({"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "dev"}, "live_gate")):
            with self.subTest(env=env), NetworkGuard() as guard, \
                    patch("reachweaver.cli.load_accounts", side_effect=boom) as loader:
                code, _, err = self.cli("publish", "--live", "--log-json", env={**env, **CREDENTIALS})
                loader.assert_not_called()
                self.assertEqual(guard.attempts, [])
            self.assertEqual(code, 2)
            events = [r for r in self.records("\n".join(line for line in err.splitlines() if line.startswith("{")))]
            self.assertEqual([(r["event"], r.get("reason")) for r in events],
                             [("run_start", None), ("gate_refused", expected), ("run_end", None)])
            self.assertEqual((events[-1]["outcome"], events[-1]["exit_code"]), ("error", 2))
            self.assertNotIn(BSKY_PASSWORD, err)

    def test_emergency_pause_refusal_is_logged(self):
        self.seed()
        self.assertEqual(self.cli("pause")[0], 0)
        with patch("reachweaver.cli.load_accounts", side_effect=AssertionError("paused run loaded accounts")):
            code, _, err, wire = self.live("--log-json")
        self.assertEqual(code, 2)
        self.assertEqual(wire.requests, [])
        events = self.records("\n".join(line for line in err.splitlines() if line.startswith("{")))
        self.assertEqual([(r["event"], r.get("reason")) for r in events],
                         [("run_start", None), ("gate_refused", "emergency_pause"), ("run_end", None)])

    def test_postiz_backend_outcome_is_read_back(self):
        config = json.loads(self.config.read_text())
        config["accounts"].append({"id": "sl-postiz", "platform": "bluesky", "env_prefix": "REACHWEAVER_SLD",
                                   "handle": "owner.bsky.social", "backend": "postiz",
                                   "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-1"})
        self.config.write_text(json.dumps(config))
        with connect(str(self.db)) as con:
            pid = add_post(con, "sl-postiz", "bluesky", f"Backend note {EMAIL}")
            transition(con, pid, "approve", approver="test-owner")

        def accepted(con, post, account, **_):
            from reachweaver.postiz import record_backend_acceptance
            con.execute("UPDATE posts SET status='sending' WHERE id=?", (post["id"],))
            record_backend_acceptance(con, post["id"], "postiz:pz-1")
            return 3

        with patch("reachweaver.postiz.run_live_send", side_effect=accepted):
            code, out, err, _ = self.live("--log-json")
        self.assertEqual(code, 0, out + err)
        records = self.records(err)
        self.assert_schema(records)
        self.assertEqual([(r["event"], r.get("backend")) for r in records[1:-1]],
                         [("post_claimed", "postiz"), ("post_needs_review", "postiz")])
        self.assertEqual(records[2]["error_code"], "postiz_accepted_platform_receipt_unverified")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE posts SET status='approved' WHERE id=?", (pid,))
        con.commit()
        con.close()
        with patch("reachweaver.postiz.run_live_send", return_value=2):
            code, out, err, _ = self.live("--log-json")
        self.assertEqual([(r["event"], r.get("reason")) for r in self.records(err)[1:-1]],
                         [("post_skipped", "backend_refused")])


# --------------------------------------------------------------------------- --log-file

@unittest.skipUnless(POSIX, "file modes are POSIX-only")
class LogFileTests(_Workspace):
    def test_file_created_0600_and_appended(self):
        self.seed()
        target = self.base / "logs" / "worker.jsonl"
        target.parent.mkdir(mode=0o700)
        previous = os.umask(0)  # whatever the umask, the file is owner-only
        try:
            code, _, err = self.cli("publish", "--log-json", "--log-file", target, "--optout", self.optout)
        finally:
            os.umask(previous)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        first = self.records(target.read_text())
        self.assert_schema(first)
        self.assertEqual(self.cli("publish", "--log-json", "--log-file", target, "--optout", self.optout)[0], 0)
        both = self.records(target.read_text())
        self.assertEqual(both[:len(first)], first)  # appended, never truncated
        run_ids = [both[0]["run_id"], both[len(first)]["run_id"]]
        self.assertLess(run_ids[0], run_ids[1])
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_unusable_paths_refused_before_any_work(self):
        self.seed()
        before = db_dump(self.db)
        readonly = self.base / "ro"
        readonly.mkdir(mode=0o500)
        self.addCleanup(readonly.chmod, 0o700)
        wide = self.base / "wide.jsonl"
        wide.write_text("")
        wide.chmod(0o644)
        link = self.base / "link.jsonl"
        link.symlink_to(self.base / "target.jsonl")
        fifo = self.base / "fifo.jsonl"
        os.mkfifo(fifo)
        directory = self.base / "adir"
        directory.mkdir()
        cases = {"missing directory": self.base / "nope" / "x.jsonl", "read-only directory": readonly / "x.jsonl",
                 "group/world readable": wide, "symlink": link, "fifo": fifo, "directory": directory}
        boom = AssertionError("work started despite an unusable log file")
        for name, path in cases.items():
            for live in (False, True):
                with self.subTest(case=name, live=live), NetworkGuard() as guard, \
                        patch("reachweaver.cli.pending", side_effect=boom) as due, \
                        patch("reachweaver.cli.load_accounts", side_effect=boom) as loader, \
                        patch("reachweaver.cli.claim_sending", side_effect=boom) as claim, \
                        patch("reachweaver.publishers._send_http", side_effect=boom) as wire:
                    argv = ["publish", "--log-json", "--log-file", path] + (["--live"] if live else [])
                    code, out, err = self.cli(*argv, env=prod_env(**CREDENTIALS) if live else None)
                    self.assertEqual(code, 2, out + err)
                    self.assertIn("ERROR:", err)
                    self.assertIn("--log-file", err)
                    for mock in (due, loader, claim, wire):
                        mock.assert_not_called()
                    self.assertEqual(guard.attempts, [])
                    self.assertNotIn(BSKY_PASSWORD, out + err)
        self.assertFalse((self.base / "target.jsonl").exists())
        self.assertEqual(db_dump(self.db), before)

    def test_log_file_requires_log_json(self):
        code, _, err = self.cli("publish", "--log-file", self.base / "x.jsonl")
        self.assertEqual(code, 2)
        self.assertIn("--log-file requires --log-json", err)
        self.assertFalse((self.base / "x.jsonl").exists())


# --------------------------------------------------------------------------- write failures

class FailingStream(io.StringIO):
    """stderr whose JSON lines fail after ``ok`` successful lines (plain text still works)."""

    def __init__(self, ok: int):
        super().__init__()
        self.ok = ok

    def write(self, text):
        if text.startswith("{"):
            if self.ok <= 0:
                raise OSError(28, "No space left on device")
            self.ok -= 1
        return super().write(text)


class WriteFailureTests(_Workspace):
    def test_first_line_failure_refuses_before_any_work(self):
        self.seed()
        before = db_dump(self.db)
        boom = AssertionError("work started despite an unwritable log")
        stream = FailingStream(ok=0)
        with patch("reachweaver.cli.pending", side_effect=boom) as due, \
                patch("reachweaver.cli.load_accounts", side_effect=boom) as loader:
            code, out, err, wire = self.live("--log-json", stderr=stream)
        self.assertEqual(code, 2)
        due.assert_not_called()
        loader.assert_not_called()
        self.assertEqual(wire.requests, [])
        self.assertIn("Structured log is not writable", stream.getvalue())
        self.assertEqual(db_dump(self.db), before)

    def test_mid_run_failure_never_changes_the_run(self):
        self.seed()
        copy = self.snapshot("copy")
        base_code, base_out, _, base_wire = self.live(db=copy)
        stream = FailingStream(ok=1)  # run_start succeeds, every later line fails
        code, out, _, wire = self.live("--log-json", stderr=stream)
        self.assertEqual((code, out), (base_code, base_out))
        self.assertEqual(wire_calls(wire), wire_calls(base_wire))
        self.assertEqual(db_dump(self.db), db_dump(copy))
        self.assertIn("structured log line(s) could not be written", stream.getvalue())


# --------------------------------------------------------------------------- behaviour equivalence

class BehaviourEquivalenceTests(_Workspace):
    """Same seeded database, same scripted provider: with and without --log-json, nothing differs."""

    def pair(self):
        self.seed()
        return self.snapshot("plain"), self.snapshot("logged")

    def test_dry_run_is_identical(self):
        plain, logged = self.pair()
        for extra in ((), ("--scheduled-only",), ("--post-id", "deadbeefdeadbeef")):
            with self.subTest(extra=extra):
                a = self.cli("publish", "--optout", self.optout, *extra, db=plain)
                b = self.cli("publish", "--optout", self.optout, "--log-json", *extra, db=logged)
                self.assertEqual(a[:2], b[:2])
                self.assertEqual(a[2], "\n".join(line for line in b[2].splitlines() if not line.startswith("{"))
                                 + ("\n" if a[2] else ""))
                self.assertEqual(db_dump(plain), db_dump(logged))

    def test_mocked_live_run_is_identical(self):
        plain, logged = self.pair()
        log_file = self.base / "worker.jsonl"
        for argv in ((), ("--limit", "3"), ("--max-per-24h", "1")):
            with self.subTest(argv=argv):
                a_code, a_out, a_err, a_wire = self.live(*argv, db=plain)
                b_code, b_out, b_err, b_wire = self.live(*argv, "--log-json", "--log-file", log_file, db=logged)
                self.assertEqual((a_code, a_out, a_err), (b_code, b_out, b_err))
                self.assertEqual(wire_calls(a_wire), wire_calls(b_wire))
                self.assertGreater(len(a_wire.requests), 0)
                self.assertEqual(db_dump(plain), db_dump(logged))
        self.assertTrue(any(r["event"] == "post_published" for r in self.records(log_file.read_text())))

    def test_single_post_live_run_exit_codes_are_identical(self):
        plain, logged = self.pair()
        with connect(str(plain)) as con:
            ids = [row[0] for row in con.execute("SELECT id FROM posts ORDER BY created_at,id")]
        for pid in ids:
            with self.subTest(post=pid):
                a = self.live("--post-id", pid, db=plain)
                b = self.live("--post-id", pid, "--log-json", db=logged)
                self.assertEqual((a[0], a[1]), (b[0], b[1]))
                self.assertEqual(a[2], "\n".join(line for line in b[2].splitlines() if not line.startswith("{"))
                                 + ("\n" if a[2] else ""))
                self.assertEqual(wire_calls(a[3]), wire_calls(b[3]))
                self.assertEqual(db_dump(plain), db_dump(logged))


if __name__ == "__main__":
    unittest.main()


class WorkerLogNeverRaisesTests(unittest.TestCase):
    """Event methods run between claim_sending and the send; they must never raise."""

    MALFORMED = ({}, {"id": None, "account_id": 7, "platform": ["x"]}, None)

    def _events(self, log, post):
        log.claimed(post)
        log.published(post, object(), "https://exa mple.invalid/\x00?token=1")
        log.needs_review(post, send_class=object(), code="")
        log.skipped(post, "not-a-reason")

    def test_disabled_log_ignores_malformed_rows(self):
        from reachweaver.worker_log import open_worker_log
        log = open_worker_log(False, None, dry_run=False)
        for post in self.MALFORMED:
            with self.subTest(post=post):
                self._events(log, post)

    def test_enabled_log_ignores_malformed_rows(self):
        from reachweaver.worker_log import open_worker_log
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            log = open_worker_log(True, None, dry_run=False)
            log.start(live_requested=True, scheduled_only=False, limit=1, post_id=None)
            for post in self.MALFORMED:
                with self.subTest(post=post):
                    self._events(log, post)
        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.startswith("{")]
        self.assertTrue(lines)
        self.assertNotIn("token=1", stream.getvalue())
