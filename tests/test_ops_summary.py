"""ops-summary (issue #26): one redacted, read-only report. Offline; nothing is sent."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.ops_summary import build_summary, exit_code
from reachweaver.storage import (
    add_post, claim_sending, connect, set_paused, transition, uncertain_send,
)

NOW = "2026-10-08T12:00:00+00:00"
T0 = "2026-10-08T10:00:00+00:00"
SECRET = "-".join(("very", "private", "app", "password", "value"))
PRIVATE_TEXT = "Private launch wording that must never be echoed"
BSKY = {"id": "s-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_S_BSKY",
        "handle": "owner.bsky.social"}
BSKY_ENV = {"REACHWEAVER_S_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_S_BSKY_APP_PASSWORD": SECRET}


class SummaryBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = str(self.base / "state" / "s.sqlite")
        self.config = self.base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [BSKY]}), encoding="utf-8")
        self.accounts = load_accounts(self.config)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in list(os.environ):
            if name.startswith("REACHWEAVER_"):
                del os.environ[name]

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def seed(self):
        with connect(self.db) as con:
            ok = add_post(con, "s-bluesky", "bluesky", PRIVATE_TEXT)
            transition(con, ok, "approve", approver="test-owner")
            amb = add_post(con, "s-bluesky", "bluesky", PRIVATE_TEXT + " two")
            transition(con, amb, "approve", approver="test-owner")
            self.assertTrue(claim_sending(con, amb, now=T0))
            uncertain_send(con, amb, "network error " + SECRET)
        return ok, amb


class SummaryContentTests(SummaryBase):
    def test_combines_health_queue_and_metrics(self):
        self.seed()
        with connect(self.db) as con:
            summary = build_summary(con, self.accounts, now=NOW, env=BSKY_ENV)
        self.assertEqual(summary["report"], "reachweaver_ops_summary")
        self.assertEqual(summary["overall"], "attention")
        row = summary["accounts"][0]
        self.assertEqual((row["account_id"], row["platform"], row["adapter"]),
                         ("s-bluesky", "bluesky", "live_text"))
        self.assertEqual(row["credentials_missing"], [])
        self.assertEqual(row["needs_review"], 1)
        self.assertEqual(row["queue"]["approved"], 1)
        self.assertEqual(row["budget"]["attempts_rolling_24h"], 1)
        self.assertEqual(summary["dead_letter"]["total"], 1)
        self.assertEqual(sum(summary["dead_letter"]["by_send_class"].values()), 1)
        self.assertEqual(summary["metric_totals"]["send_claims_total"], 1)
        self.assertIn("needs_review", {r["code"] for r in summary["reasons"]})
        self.assertEqual(exit_code(summary), 0)

    def test_blocked_exit_code_when_paused(self):
        with connect(self.db) as con:
            set_paused(con, True)
            summary = build_summary(con, self.accounts, now=NOW, env=BSKY_ENV)
        self.assertEqual(summary["overall"], "blocked")
        self.assertTrue(summary["global"]["emergency_paused"])
        self.assertEqual(exit_code(summary), 1)

    def test_missing_credentials_report_names_only(self):
        with connect(self.db) as con:
            summary = build_summary(con, self.accounts, now=NOW, env={})
        self.assertEqual(summary["accounts"][0]["credentials_missing"],
                         ["REACHWEAVER_S_BSKY_APP_PASSWORD", "REACHWEAVER_S_BSKY_IDENTIFIER"])

    def test_unloadable_accounts_reason_has_no_free_text(self):
        with connect(self.db) as con:
            summary = build_summary(con, {}, accounts_error=f"bad file /home/x/{SECRET}.json", now=NOW, env={})
        self.assertEqual(summary["overall"], "blocked")
        self.assertNotIn(SECRET, json.dumps(summary))
        self.assertTrue(all(set(r) == {"level", "code", "account_id"} for r in summary["reasons"]))


class SummaryCliTests(SummaryBase):
    def test_json_and_text_are_redacted_and_deterministic(self):
        self.seed()
        env = {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1"}
        with patch.dict(os.environ, env):
            a = self.cli("ops-summary", "--json", "--now", NOW)
            b = self.cli("ops-summary", "--json", "--now", NOW)
            text = self.cli("ops-summary", "--now", NOW)
        self.assertEqual(a, b)
        self.assertEqual(a[0], 0, a[2])
        self.assertEqual(json.loads(a[1])["schema_version"], 1)
        self.assertEqual(text[0], 0, text[2])
        self.assertIn("OPS SUMMARY: ATTENTION", text[1])
        for blob in (a[1] + a[2], text[1] + text[2]):
            for needle in (SECRET, PRIVATE_TEXT, "owner.bsky.social", "network error"):
                self.assertNotIn(needle, blob)

    def test_read_only_database_unchanged_and_no_network(self):
        self.seed()
        digest = hashlib.sha256(Path(self.db).read_bytes()).hexdigest()

        def refuse(*_a, **_k):
            raise AssertionError("network access attempted")
        with patch.object(socket.socket, "connect", refuse), patch.object(socket, "getaddrinfo", refuse):
            code, _, err = self.cli("ops-summary", "--json", "--since", "7d")
        self.assertIn(code, (0, 1), err)
        self.assertEqual(hashlib.sha256(Path(self.db).read_bytes()).hexdigest(), digest)

    def test_missing_database_is_exit_2_and_not_created(self):
        code, _, err = self.cli("ops-summary")
        self.assertEqual(code, 2)
        self.assertFalse(Path(self.db).exists())
        self.assertTrue(err)

    def test_paused_exits_1(self):
        with connect(self.db) as con:
            set_paused(con, True)
        code, out, _ = self.cli("ops-summary", "--now", NOW)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)

    def test_bad_accounts_file_still_reports(self):
        with connect(self.db):
            pass
        self.config.write_text("not json", encoding="utf-8")
        code, out, _ = self.cli("ops-summary", "--json", "--now", NOW)
        self.assertEqual(code, 1)
        self.assertIn("accounts_config_unloadable", out)


if __name__ == "__main__":
    unittest.main()
