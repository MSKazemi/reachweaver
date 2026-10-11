"""Offline health, dead-letter view and tighten-only caps file. No provider is ever contacted."""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver.caps import effective_caps, load_caps, validate_caps
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection
from reachweaver.health import (
    RECONCILE_STEPS, build_health, dead_letter, exit_code, sanitize_error_code,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    add_post, claim_sending, connect, finish_send, get_post, set_paused, transition, uncertain_send,
)

NOW = "2026-10-08T12:00:00+00:00"
# Built at runtime so the tracked-file secret scan never sees a credential-shaped literal.
SECRET = "-".join(("very", "private", "app", "password", "value"))
LI_VALUE = "-".join(("li", "private", "value"))
PZ_VALUE = "-".join(("pz", "private", "value"))
BSKY = {"id": "h-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_H_BSKY",
        "handle": "owner.bsky.social"}
BSKY_ENV = {"REACHWEAVER_H_BSKY_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_H_BSKY_APP_PASSWORD": SECRET}
LINKEDIN = {"id": "h-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_H_LI",
            "person_id": "abc123", "linkedin_version": "202609",
            "authorization_expires_at": "2026-12-01T00:00:00+00:00"}
POSTIZ = {"id": "h-postiz", "platform": "mastodon", "env_prefix": "REACHWEAVER_H_PZ", "backend": "postiz",
          "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "integ-1", "handle": "owner"}
YOUTUBE = {"id": "h-youtube", "platform": "youtube", "env_prefix": "REACHWEAVER_H_YT"}


class _NoSockets:
    """Refuse every socket connection or DNS lookup for the duration of a block."""

    def __enter__(self):
        def refuse(*_a, **_k):
            raise AssertionError("network access attempted")
        self._patches = [patch.object(socket.socket, "connect", refuse),
                         patch.object(socket, "create_connection", refuse),
                         patch.object(socket, "getaddrinfo", refuse)]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self._patches:
            item.stop()


class HealthTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.base = base
        self.db = str(base / "state" / "h.sqlite")
        self.config = base / "accounts.json"
        self.write_accounts(BSKY)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in list(os.environ):
            if name.startswith("REACHWEAVER_"):
                del os.environ[name]

    def write_accounts(self, *accounts):
        self.config.write_text(json.dumps({"accounts": list(accounts)}), encoding="utf-8")
        from reachweaver.accounts import load_accounts
        self.accounts = load_accounts(self.config)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def health(self, env=None, **kwargs):
        with connect(self.db) as db:
            return build_health(db, self.accounts, now=kwargs.pop("now", NOW),
                                env=BSKY_ENV if env is None else env, **kwargs)

    def approved(self, db, account="h-bluesky", platform="bluesky", text="Evidence-backed release note"):
        pid = add_post(db, account, platform, text)
        transition(db, pid, "approve")
        return pid

    @staticmethod
    def codes(report):
        return {r["code"] for r in report["reasons"]}


class HealthReportTests(HealthTestBase):
    def test_ready_account_is_ok_and_never_shows_values(self):
        report = self.health()
        self.assertEqual(report["overall"], "ok", report["reasons"])
        self.assertEqual(exit_code(report), 0)
        row = report["accounts"][0]
        self.assertEqual(row["adapter"], "live_text")
        self.assertEqual(row["registry"]["reachweaver_adapter"], "live_text")
        self.assertFalse(row["registry"]["stale"])
        self.assertEqual(sorted(row["credentials"]["present"]), sorted(BSKY_ENV))
        self.assertEqual(row["credentials"]["missing"], [])
        self.assertFalse(row["postiz"]["configured"])
        self.assertEqual(row["budget"]["max_per_24h"], 2)
        self.assertEqual(row["budget"]["min_gap_minutes"], 60)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertFalse(report["global"]["emergency_paused"])
        self.assertFalse(report["global"]["live_opt_in_env"])

    def test_cli_health_offline_ok_and_json(self):
        with patch.dict(os.environ, BSKY_ENV), _NoSockets():
            code, out, _ = self.cli("health")
            # Real clock: the registry entry may age into 'attention' later; never 'blocked'.
            self.assertEqual(code, 0, out)
            self.assertRegex(out, r"OVERALL: (OK|ATTENTION)")
            code, out, _ = self.cli("health", "--json", "--stale-minutes", "45")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn(data["overall"], ("ok", "attention"))
        self.assertNotIn("credentials_missing", {r["code"] for r in data["reasons"]})
        self.assertEqual(data["global"]["stale_sending_minutes"], 45)
        self.assertIn("dead_letter", data)
        self.assertNotIn(SECRET, out)

    def test_pause_blocks_with_nonzero_exit(self):
        with connect(self.db) as db:
            set_paused(db, True)
        report = self.health()
        self.assertEqual(report["overall"], "blocked")
        self.assertIn("emergency_pause_active", self.codes(report))
        self.assertTrue(report["global"]["emergency_paused"])
        with patch.dict(os.environ, BSKY_ENV):
            code, out, _ = self.cli("health")
        self.assertEqual(code, 1)
        self.assertIn("OVERALL: BLOCKED", out)

    def test_missing_credentials_attention_then_blocked_with_queued_work(self):
        report = self.health(env={})
        self.assertEqual(report["overall"], "attention")
        row = report["accounts"][0]
        self.assertEqual(sorted(row["credentials"]["missing"]), sorted(BSKY_ENV))
        with connect(self.db) as db:
            self.approved(db)
        report = self.health(env={})
        self.assertEqual(report["overall"], "blocked")
        reason = next(r for r in report["reasons"] if r["code"] == "credentials_missing")
        self.assertEqual(reason["level"], "blocked")
        self.assertEqual(reason["account_id"], "h-bluesky")
        self.assertEqual(report["accounts"][0]["queue"]["approved"], 1)

    def test_live_opt_in_flag_reported(self):
        report = self.health(env={**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1"})
        self.assertTrue(report["global"]["live_opt_in_env"])

    def test_linkedin_version_and_authorization_lifecycle(self):
        self.write_accounts(LINKEDIN)
        env = {"REACHWEAVER_H_LI_ACCESS_TOKEN": LI_VALUE}
        report = self.health(env=env)
        self.assertEqual(report["overall"], "ok", report["reasons"])
        li = report["accounts"][0]["linkedin"]
        self.assertEqual(li["version"]["status"], "ok")
        self.assertEqual(li["authorization"]["status"], "ok")
        # Version 12+ months old and an expired grant: both block.
        report = self.health(env=env, now="2027-12-15T00:00:00+00:00")
        self.assertEqual(report["overall"], "blocked")
        self.assertTrue({"linkedin_version_unsupported", "linkedin_authorization_expired"} <= self.codes(report))
        # 10 months old: ageing warning only.
        self.write_accounts({**LINKEDIN, "authorization_expires_at": "2028-12-01T00:00:00+00:00"})
        report = self.health(env=env, now="2027-07-15T00:00:00+00:00")
        self.assertIn("linkedin_version_ageing", self.codes(report))
        self.assertNotIn(LI_VALUE, json.dumps(report))

    def test_postiz_backend_and_manual_adapters(self):
        self.write_accounts(BSKY, POSTIZ, YOUTUBE)
        with connect(self.db) as db:
            pid = add_post(db, "h-youtube", "youtube", "A demo video description")
            transition(db, pid, "approve")
        report = self.health(env={**BSKY_ENV, "REACHWEAVER_H_PZ_POSTIZ_API_KEY": PZ_VALUE})
        rows = {r["account_id"]: r for r in report["accounts"]}
        self.assertEqual(rows["h-postiz"]["adapter"], "backend")
        self.assertTrue(rows["h-postiz"]["postiz"]["configured"])
        self.assertEqual(rows["h-postiz"]["credentials"]["present"], ["REACHWEAVER_H_PZ_POSTIZ_API_KEY"])
        self.assertEqual(rows["h-youtube"]["adapter"], "manual")
        self.assertEqual(rows["h-youtube"]["credentials"], {"present": [], "missing": [], "source": "environment",
                                                            "note": rows["h-youtube"]["credentials"]["note"]})
        self.assertIn("manual_platform_has_queued_posts", self.codes(report))
        self.assertEqual(rows["h-youtube"]["status"], "attention")
        self.assertNotIn(PZ_VALUE, json.dumps(report))

    def test_registry_staleness_is_attention(self):
        report = self.health(now="2027-06-01T00:00:00+00:00")
        self.assertIn("bluesky", report["global"]["registry"]["stale_platforms"])
        self.assertTrue(report["accounts"][0]["registry"]["stale"])
        self.assertIn("registry_entry_stale", self.codes(report))
        self.assertEqual(report["overall"], "attention")

    def test_invalid_registry_blocks(self):
        with connect(self.db) as db, patch("reachweaver.health.load_registry",
                                           side_effect=ValidationError("Invalid capability registry")):
            report = build_health(db, self.accounts, now=NOW, env=BSKY_ENV)
        self.assertEqual(report["overall"], "blocked")
        self.assertIn("capability_registry_invalid", self.codes(report))

    def test_budget_counts_attempts_and_exhaustion(self):
        with connect(self.db) as db:
            first, second, queued = self.approved(db), self.approved(db), self.approved(db)
            claim_sending(db, first, now="2026-10-08T09:00:00+00:00", max_per_24h=2, min_gap_minutes=60)
            finish_send(db, first, "at://1", "https://bsky.app/1")
            claim_sending(db, second, now="2026-10-08T11:30:00+00:00", max_per_24h=2, min_gap_minutes=60)
            finish_send(db, second, "at://2", "https://bsky.app/2")
        report = self.health()
        budget = report["accounts"][0]["budget"]
        self.assertEqual(budget["attempts_rolling_24h"], 2)
        self.assertEqual(budget["remaining"], 0)
        self.assertEqual(budget["last_attempt_at"], "2026-10-08T11:30:00+00:00")
        self.assertEqual(budget["next_attempt_allowed_at"], "2026-10-08T12:30:00+00:00")
        self.assertIn("rolling_budget_exhausted", self.codes(report))
        self.assertEqual(report["accounts"][0]["queue"]["published"], 2)

    def test_needs_review_and_stale_sending_need_attention(self):
        with connect(self.db) as db:
            review, stuck = self.approved(db), self.approved(db)
            claim_sending(db, review, now="2026-10-08T08:00:00+00:00")
            uncertain_send(db, review, "network_or_timeout_uncertain")
            claim_sending(db, stuck, now="2026-10-08T10:00:00+00:00")
        report = self.health()
        row = report["accounts"][0]
        self.assertEqual((row["needs_review"], row["stale_sending"]), (1, 1))
        self.assertTrue({"needs_review", "stale_sending"} <= self.codes(report))
        self.assertEqual(report["overall"], "attention")
        self.assertEqual(report["dead_letter"]["total"], 1)

    def test_unloadable_accounts_config_blocks(self):
        self.config.unlink()
        code, out, _ = self.cli("health")
        self.assertEqual(code, 1)
        self.assertIn("accounts_config_unloadable", out)

    def test_orphan_queued_posts_reported(self):
        with connect(self.db) as db:
            self.approved(db, account="gone-account")
        report = self.health()
        self.assertEqual(report["unconfigured_accounts_with_active_posts"], ["gone-account"])
        self.assertIn("queued_posts_for_unconfigured_accounts", self.codes(report))

    def test_health_is_read_only(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            claim_sending(db, pid, now="2026-10-08T08:00:00+00:00")
            uncertain_send(db, pid, "http_401")
            before = (db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0],
                      [tuple(r) for r in db.execute("SELECT * FROM posts ORDER BY id")])
        with patch.dict(os.environ, BSKY_ENV), _NoSockets():
            self.cli("health", "--json")
            self.cli("dead-letter", "--json")
        with connect(self.db) as db:
            after = (db.execute("SELECT COUNT(*) FROM event_log").fetchone()[0],
                     [tuple(r) for r in db.execute("SELECT * FROM posts ORDER BY id")])
        self.assertEqual(before, after)

    def test_invalid_caps_file_blocks_health(self):
        bad = self.base / "caps.json"
        bad.write_text(json.dumps({"version": 1, "defaults": {"max_per_24h": 50}}), encoding="utf-8")
        with patch.dict(os.environ, {**BSKY_ENV, "REACHWEAVER_CAPS": str(bad)}):
            code, out, _ = self.cli("health")
        self.assertEqual(code, 1)
        self.assertIn("caps_file_invalid", out)


class DeadLetterTests(HealthTestBase):
    def test_sanitized_codes(self):
        self.assertEqual(sanitize_error_code("network_or_timeout_uncertain"), "network_or_timeout_uncertain")
        self.assertEqual(sanitize_error_code("postiz:postiz_http_500"), "postiz:postiz_http_500")
        self.assertEqual(sanitize_error_code(
            "linkedin_http_426_uncertain: Linkedin-Version is not active; pin a current version"),
            "linkedin_http_426_uncertain")
        self.assertEqual(sanitize_error_code("Bearer abcdefghijklmnopqrstuvwxyz failed"), "unclassified")
        self.assertEqual(sanitize_error_code(None), "unknown")
        self.assertEqual(sanitize_error_code(""), "unknown")

    def test_groups_by_code_with_age_and_steps_never_retries(self):
        with connect(self.db) as db:
            ids = [self.approved(db, text=f"Evidence-backed note number {i}") for i in range(4)]
            errors = ["network_or_timeout_uncertain", "network_or_timeout_uncertain",
                      "linkedin_http_426_uncertain: Linkedin-Version is not active",
                      "token=" + SECRET + " leaked into an error"]
            for i, (pid, error) in enumerate(zip(ids, errors)):
                claim_sending(db, pid, now=f"2026-10-08T0{i + 6}:00:00+00:00")
                uncertain_send(db, pid, error)
            # uncertain_send stamps the needs_review event at wall-clock time; pin ages for the test.
            for i, pid in enumerate(ids):
                db.execute("UPDATE event_log SET at=? WHERE post_id=? AND action='needs_review'",
                           (f"2026-10-08T0{i + 6}:05:00+00:00", pid))
            letters = dead_letter(db, now=NOW)
            statuses = {get_post(db, pid)["status"] for pid in ids}
        self.assertEqual(statuses, {"needs_review"})
        self.assertEqual(letters["total"], 4)
        groups = {g["code"]: g for g in letters["groups"]}
        self.assertEqual(set(groups), {"network_or_timeout_uncertain", "linkedin_http_426_uncertain", "unclassified"})
        timeout = groups["network_or_timeout_uncertain"]
        self.assertEqual(timeout["count"], 2)
        self.assertEqual(timeout["oldest_age_minutes"], 6 * 60 - 5)
        self.assertEqual(timeout["reconcile_steps"], list(RECONCILE_STEPS))
        self.assertIn("version", groups["linkedin_http_426_uncertain"]["suggested_action"].lower())
        self.assertEqual(letters["groups"][0]["code"], "network_or_timeout_uncertain")
        self.assertNotIn(SECRET, json.dumps(letters))
        self.assertIn("never automatic", letters["retry"])

    def test_cli_dead_letter_text_and_json(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            claim_sending(db, pid)
            uncertain_send(db, pid, "http_401")
        with _NoSockets():
            code, out, _ = self.cli("dead-letter")
            self.assertEqual(code, 0)
            self.assertIn("[http_401] 1 post(s)", out)
            self.assertIn("reconcile POST_ID --not-published", out)
            code, out, _ = self.cli("dead-letter", "--json")
        self.assertEqual(json.loads(out)["groups"][0]["posts"][0]["post_id"], pid)


class CapsFileTests(HealthTestBase):
    def test_effective_caps_tighten_only(self):
        caps = validate_caps({"version": 1, "defaults": {"max_per_24h": 1, "min_gap_minutes": 240},
                              "accounts": {"a": {"max_per_24h": 10, "min_gap_minutes": 15}}})
        # The account entry is looser than both CLI and defaults: ignored.
        self.assertEqual(effective_caps(caps, "a", 2, 60), (1, 240))
        self.assertEqual(effective_caps(caps, "unknown", 2, 60), (1, 240))
        looser = validate_caps({"version": 1, "defaults": {"max_per_24h": 10, "min_gap_minutes": 15}})
        self.assertEqual(effective_caps(looser, "a", 2, 60), (2, 60))
        self.assertEqual(effective_caps(None, "a", 3, 90), (3, 90))

    def test_invalid_caps_rejected(self):
        for raw in ({"version": 3}, {"version": 1, "extra": 1}, {"version": 1, "defaults": {"max_per_24h": 0}},
                    {"version": 1, "defaults": {"min_gap_minutes": 5}}, {"version": 1, "defaults": {"burst": 3}},
                    {"version": 1, "accounts": {"a": {"max_per_24h": True}}}, {"version": 1, "accounts": []}):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                validate_caps(raw)

    def test_load_caps_sources(self):
        self.assertEqual(load_caps(), (None, None))
        with self.assertRaisesRegex(ValidationError, "not found"):
            load_caps(self.base / "missing.json")
        example = Path(__file__).resolve().parents[1] / "config/caps.example.json"
        caps, source = load_caps(example)
        self.assertEqual(source, example)
        self.assertIn("my-linkedin", caps["accounts"])

    def _live(self, caps: dict | None, *extra):
        if caps is not None:
            path = self.base / "caps.json"
            path.write_text(json.dumps(caps), encoding="utf-8")
            env = {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_CAPS": str(path)}
        else:
            env = {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        verified = VerifiedConnection("h-bluesky", "bluesky", "verified", "unverified")
        with patch.dict(os.environ, env), _NoSockets(), \
                patch("reachweaver.cli.check_connection", return_value=verified), \
                patch("reachweaver.cli.publish", return_value=("at://new", "https://bsky.app/new")) as sender:
            code, out, _ = self.cli("publish", "--live", "--post-id", self.target, *extra)
        return code, out, sender

    def _seed(self, hours_ago: list[float]):
        now = datetime.now(timezone.utc)
        with connect(self.db) as db:
            for i, hours in enumerate(hours_ago):
                pid = self.approved(db, text=f"Earlier evidence-backed note {i}")
                at = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
                self.assertTrue(claim_sending(db, pid, now=at))
                finish_send(db, pid, f"at://{i}", f"https://bsky.app/{i}")
            self.target = self.approved(db, text="The post under test")

    def test_stricter_file_cap_blocks_where_cli_would_allow(self):
        self._seed([3])
        code, out, sender = self._live({"version": 1, "accounts": {"h-bluesky": {"max_per_24h": 1}}})
        self.assertEqual(code, 2, out)
        self.assertIn("rolling_24h_send_attempt_cap_reached", out)
        sender.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, self.target)["status"], "approved")
        code, out, sender = self._live(None)  # same state, CLI default 2/24h: allowed
        self.assertEqual(code, 0, out)
        sender.assert_called_once()

    def test_stricter_file_gap_blocks_where_cli_would_allow(self):
        self._seed([2])
        code, out, sender = self._live({"version": 1, "defaults": {"min_gap_minutes": 180}})
        self.assertEqual(code, 2, out)
        self.assertIn("minimum_send_attempt_gap_not_elapsed", out)
        sender.assert_not_called()

    def test_looser_file_values_are_ignored(self):
        self._seed([5, 3])  # CLI default budget (2/24h) already used
        loose = {"version": 1, "defaults": {"max_per_24h": 10, "min_gap_minutes": 15},
                 "accounts": {"h-bluesky": {"max_per_24h": 10, "min_gap_minutes": 15}}}
        code, out, sender = self._live(loose)
        self.assertEqual(code, 2, out)
        self.assertIn("rolling_24h_send_attempt_cap_reached", out)
        sender.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, self.target)["status"], "approved")

    def test_looser_file_gap_is_ignored(self):
        self._seed([0.5])  # 30 min ago; CLI gap is 60
        code, out, sender = self._live({"version": 1, "accounts": {"h-bluesky": {"min_gap_minutes": 15}}})
        self.assertEqual(code, 2, out)
        self.assertIn("minimum_send_attempt_gap_not_elapsed", out)
        sender.assert_not_called()

    def test_malformed_caps_file_refuses_live_run(self):
        self._seed([])
        code, out, sender = self._live({"version": 1, "defaults": {"max_per_24h": 99}})
        self.assertEqual(code, 2)
        sender.assert_not_called()

    def test_dry_run_mentions_caps_file(self):
        self._seed([])
        path = self.base / "caps.json"
        path.write_text(json.dumps({"version": 1, "defaults": {"max_per_24h": 1}}), encoding="utf-8")
        with patch.dict(os.environ, {"REACHWEAVER_CAPS": str(path)}):
            code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn("tighten-only", out)


if __name__ == "__main__":
    unittest.main()
