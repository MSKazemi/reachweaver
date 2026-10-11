"""Credential hygiene audit and missing/expired-authorization safety (issue #5 slice).

Everything here is offline. Socket entry points are patched by the same guard as
tests/test_no_network.py, so any connection or DNS lookup fails the test. Fake
credential strings exist only in patched environments and are asserted never printed.
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

from test_no_network import NetworkGuard, _offline_env

from reachweaver.accounts import load_accounts, missing_credentials
from reachweaver.cli import main
from reachweaver.credential_hygiene import (
    CREDENTIAL_SUFFIXES, audit_environment, expected_variables, looks_like_placeholder,
    render_audit, scan_shell_history,
)
from reachweaver.publishers import PublishError
from reachweaver.storage import connect, get_post

# Built at runtime so no token-shaped literal sits in the source; never real.
SHARED_SECRET = "shared-" + "fake-value-" + "41b7"
BSKY_SECRET = "bsky-" + "fake-app-pw-" + "9c2e"
ORPHAN_SECRET = "orphan-" + "fake-token-" + "77aa"
HISTORY_SECRET = "hist-" + "fake-literal-" + "5d10"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.db = str(base / "state" / "outbox.sqlite")
        self.config = base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "ch-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_CH_BSKY",
             "handle": "example.bsky.social"},
            {"id": "ch-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_CH_MASTO",
             "server": "https://mastodon.social", "handle": "example"},
            {"id": "ch-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_CH_TG",
             "chat_id": "@examplechannel", "channel_username": "examplechannel", "bot_username": "example_bot"},
            {"id": "ch-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_CH_LI",
             "person_id": "782bbtaQ", "linkedin_version": "202609"},
            {"id": "ch-x", "platform": "x", "env_prefix": "REACHWEAVER_CH_X"},
        ]}), encoding="utf-8")
        self.accounts = load_accounts(self.config)
        env = patch.dict(os.environ, _offline_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def codes(self, report):
        return [f["code"] for f in report["findings"]]


class AuditEnvironmentTests(Base):
    def test_suffixes_agree_with_accounts_missing_credentials(self):
        for account in self.accounts.values():
            with self.subTest(account=account["id"]), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(list(expected_variables(account)), missing_credentials(account))
        self.assertEqual(set(CREDENTIAL_SUFFIXES), {"bluesky", "mastodon", "telegram", "linkedin", "instagram",
                                                    "tiktok", "youtube", "threads", "facebook"})

    def test_reports_set_and_missing_names_only(self):
        env = {"REACHWEAVER_CH_BSKY_IDENTIFIER": "example.bsky.social",
               "REACHWEAVER_CH_BSKY_APP_PASSWORD": BSKY_SECRET}
        report = audit_environment(self.accounts, env)
        rows = {r["account_id"]: r for r in report["accounts"]}
        self.assertEqual(rows["ch-bluesky"]["set"], ["REACHWEAVER_CH_BSKY_IDENTIFIER", "REACHWEAVER_CH_BSKY_APP_PASSWORD"])
        self.assertEqual(rows["ch-bluesky"]["missing"], [])
        self.assertEqual(rows["ch-mastodon"]["missing"], ["REACHWEAVER_CH_MASTO_TOKEN"])
        self.assertEqual(rows["ch-linkedin"]["missing"], ["REACHWEAVER_CH_LI_ACCESS_TOKEN"])
        self.assertTrue(rows["ch-x"]["manual_only"])
        self.assertEqual(report["findings"], [])  # missing credentials are status, not findings
        dumped = json.dumps(report) + render_audit(report)
        self.assertNotIn(BSKY_SECRET, dumped)

    def test_reuse_detected_across_accounts_without_printing_value_or_hash(self):
        env = {"REACHWEAVER_CH_MASTO_TOKEN": SHARED_SECRET, "REACHWEAVER_CH_TG_BOT_TOKEN": SHARED_SECRET}
        report = audit_environment(self.accounts, env)
        reused = [f for f in report["findings"] if f["code"] == "credential_value_reused"]
        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0]["variables"], ["REACHWEAVER_CH_MASTO_TOKEN", "REACHWEAVER_CH_TG_BOT_TOKEN"])
        self.assertEqual(reused[0]["accounts"], ["ch-mastodon", "ch-telegram"])
        dumped = json.dumps(report) + render_audit(report)
        self.assertNotIn(SHARED_SECRET, dumped)
        import hashlib
        self.assertNotIn(hashlib.sha256(SHARED_SECRET.encode()).hexdigest(), dumped)

    def test_distinct_values_are_not_reuse(self):
        env = {"REACHWEAVER_CH_MASTO_TOKEN": SHARED_SECRET, "REACHWEAVER_CH_TG_BOT_TOKEN": BSKY_SECRET}
        self.assertNotIn("credential_value_reused", self.codes(audit_environment(self.accounts, env)))

    def test_orphans_detected_and_known_non_account_token_is_not_orphan(self):
        env = {"REACHWEAVER_OLDBRAND_TOKEN": ORPHAN_SECRET, "REACHWEAVER_GITHUB_TOKEN": BSKY_SECRET,
               "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_DB": "state/x.sqlite"}
        report = audit_environment(self.accounts, env)
        orphan = [f for f in report["findings"] if f["code"] == "orphan_credential"]
        self.assertEqual(orphan[0]["variables"], ["REACHWEAVER_OLDBRAND_TOKEN"])
        self.assertEqual(report["non_account_credentials_set"], ["REACHWEAVER_GITHUB_TOKEN"])
        self.assertTrue(report["live_opt_in"])
        self.assertNotIn(ORPHAN_SECRET, json.dumps(report) + render_audit(report))

    def test_placeholders_flagged(self):
        for value in ("", "   ", "changeme", "xxx", "XXXXXX", "<your-token>", "${TOKEN}", "your_token_here", "****"):
            with self.subTest(value=value):
                self.assertTrue(looks_like_placeholder(value))
                report = audit_environment(self.accounts, {"REACHWEAVER_CH_MASTO_TOKEN": value})
                self.assertIn("placeholder_value", self.codes(report))
        for value in (SHARED_SECRET, "123456:" + "Abc_def-ghi"):
            self.assertFalse(looks_like_placeholder(value))

    def test_shared_env_prefix_variable_collision_flagged_even_when_unset(self):
        # Mastodon prefix *_BOT reads REACHWEAVER_X_BOT_TOKEN: exactly the Telegram bot token.
        accounts = {
            "tg": {"id": "tg", "platform": "telegram", "env_prefix": "REACHWEAVER_X"},
            "masto": {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_X_BOT"},
        }
        report = audit_environment(accounts, {})
        shared = [f for f in report["findings"] if f["code"] == "shared_credential_variable"]
        self.assertEqual(shared[0]["variables"], ["REACHWEAVER_X_BOT_TOKEN"])
        self.assertEqual(shared[0]["accounts"], ["masto", "tg"])


class HistoryScanTests(Base):
    def write_history(self, lines, mode=0o600):
        path = Path(self.tmp.name) / "history"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(mode)
        return path

    def test_reports_line_numbers_only(self):
        path = self.write_history([
            "ls -la",
            "export REACHWEAVER_CH_MASTO_TOKEN=" + HISTORY_SECRET,
            ": 1700000000:0;REACHWEAVER_CH_BSKY_APP_PASSWORD='" + HISTORY_SECRET + "' reachweaver doctor",
            'export REACHWEAVER_CH_MASTO_TOKEN="$(pass show reachweaver/masto)"',
            "read -r -s REACHWEAVER_CH_TG_BOT_TOKEN",
            "unset REACHWEAVER_CH_MASTO_TOKEN",
            "REACHWEAVER_CH_TG_BOT_TOKEN= reachweaver doctor",
            "REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id abc",
            "docker run -e REACHWEAVER_X_BOT_TOKEN=" + HISTORY_SECRET + " image",
        ])
        self.assertEqual(scan_shell_history(path), [2, 3, 9])
        code, out, err = self.cli("credentials-audit", "--history", str(path))
        self.assertEqual(code, 1)
        self.assertIn("FINDING shell_history_literal:", out)
        self.assertIn("lines 2, 3, 9", out)
        self.assertNotIn(HISTORY_SECRET, out + err)
        self.assertNotIn("REACHWEAVER_CH_MASTO_TOKEN=", out + err)
        code, out, _ = self.cli("credentials-audit", "--history", str(path), "--json")
        self.assertEqual(code, 1)
        finding = next(f for f in json.loads(out)["findings"] if f["code"] == "shell_history_literal")
        self.assertEqual(finding["lines"], [2, 3, 9])
        self.assertNotIn(HISTORY_SECRET, out)

    def test_clean_history_and_world_readable_history(self):
        path = self.write_history(["ls", "reachweaver doctor"])
        self.assertEqual(self.cli("credentials-audit", "--history", str(path))[0], 0)
        if os.name == "posix":
            path.chmod(0o644)
            code, out, _ = self.cli("credentials-audit", "--history", str(path))
            self.assertEqual(code, 1)
            self.assertIn("history_file_not_private", out)

    def test_missing_history_file_is_an_error_not_a_crash(self):
        code, _, err = self.cli("credentials-audit", "--history", str(Path(self.tmp.name) / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("History file not found", err)


class CliAuditTests(Base):
    def test_cli_never_prints_values_and_exits_1_on_findings(self):
        env = {"REACHWEAVER_CH_MASTO_TOKEN": SHARED_SECRET, "REACHWEAVER_CH_TG_BOT_TOKEN": SHARED_SECRET,
               "REACHWEAVER_STALE_APP_PASSWORD": ORPHAN_SECRET}
        with NetworkGuard() as guard, patch.dict(os.environ, env):
            code, out, err = self.cli("credentials-audit")
            jcode, jout, _ = self.cli("credentials-audit", "--json")
        self.assertEqual(guard.attempts, [])
        self.assertEqual((code, jcode), (1, 1))
        self.assertIn("credential_value_reused", out)
        self.assertIn("orphan_credential", out)
        for secret in (SHARED_SECRET, ORPHAN_SECRET):
            self.assertNotIn(secret, out + err + jout)
        self.assertFalse(Path(self.db).exists(), "credentials-audit must not open the state database")

    def test_clean_environment_exits_0_and_doctor_points_to_audit(self):
        with NetworkGuard() as guard:
            code, out, _ = self.cli("credentials-audit")
            self.assertEqual(code, 0)
            self.assertIn("No findings.", out)
            code, out, _ = self.cli("doctor")
        self.assertEqual(guard.attempts, [])
        self.assertEqual(code, 0)
        self.assertIn("credentials-audit", out)


class MissingOrExpiredAuthorizationTests(Base):
    """Missing/expired authorization must never publish, claim, or disclose a credential."""

    def approved_post(self, account):
        code, out, err = self.cli("draft", "--account", account, "--text", "A verified offline research note")
        self.assertEqual(code, 0, err)
        post_id = out.split()[2]
        self.assertEqual(self.cli("approve", post_id)[0], 0)
        return post_id

    def assert_untouched(self, post_id):
        with connect(self.db) as db:
            self.assertEqual(get_post(db, post_id)["status"], "approved")
            claims = db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=? AND action='send_claimed'",
                                (post_id,)).fetchone()[0]
        self.assertEqual(claims, 0)

    def test_live_publish_without_credentials_skips_before_any_network(self):
        post_ids = [self.approved_post(a) for a in ("ch-bluesky", "ch-mastodon", "ch-telegram")]
        with NetworkGuard() as guard, patch.dict(os.environ, {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.cli.publish") as sender:
            for post_id in post_ids:
                with self.subTest(post_id=post_id):
                    code, out, _ = self.cli("publish", "--live", "--post-id", post_id)
                    self.assertEqual(code, 2)
                    self.assertIn("SKIPPED", out)
                    self.assertIn("configure environment variables", out)
            code, out, _ = self.cli("publish", "--live")  # batch mode: all skipped, nothing sent
            self.assertEqual(code, 0)
        self.assertEqual(guard.attempts, [])
        sender.assert_not_called()
        for post_id in post_ids:
            self.assert_untouched(post_id)

    def test_placeholder_telegram_token_blocked_before_network(self):
        post_id = self.approved_post("ch-telegram")
        env = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_CH_TG_BOT_TOKEN": "changeme"}
        with NetworkGuard() as guard, patch.dict(os.environ, env):
            code, out, err = self.cli("publish", "--live", "--post-id", post_id)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", out)
        self.assertEqual(guard.attempts, [])
        self.assertNotIn("changeme", out + err)
        self.assert_untouched(post_id)

    def test_expired_or_revoked_credential_blocks_without_claim_or_disclosure(self):
        post_id = self.approved_post("ch-mastodon")
        env = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_CH_MASTO_TOKEN": SHARED_SECRET}
        # A revoked/expired Mastodon token answers verify_credentials with HTTP 401.
        with NetworkGuard() as guard, patch.dict(os.environ, env), \
                patch("reachweaver.publishers.request_get_json", side_effect=PublishError("http_401")) as probe, \
                patch("reachweaver.publishers.request_json") as writer:
            code, out, err = self.cli("publish", "--live", "--post-id", post_id)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", out)
        self.assertIn("http_401", out)
        self.assertEqual(probe.call_count, 1)
        writer.assert_not_called()  # no status POST was ever attempted
        self.assertEqual(guard.attempts, [])
        self.assertNotIn(SHARED_SECRET, out + err)
        self.assert_untouched(post_id)


if __name__ == "__main__":
    unittest.main()
