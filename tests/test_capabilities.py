"""Offline contracts for the fail-closed platform capability registry (issue #6/#24)."""
from __future__ import annotations

import contextlib
from copy import deepcopy
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reachweaver import capabilities
from reachweaver.capabilities import (
    FORMATS, load_registry, require_capability, stale_entries, validate_registry,
)
from reachweaver.cli import main
from reachweaver.policy import PLATFORMS, SUPPORTED_LIVE, ValidationError
from gate_support import publish  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILL_RUNTIME = ROOT / ".claude/skills/reachweaver/scripts/reachweaver"


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CapabilityRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_registry()

    def _entry(self, data, key):
        return next(e for e in data["platforms"] if e["id"] == key)

    def test_registry_complete_for_every_platform(self):
        self.assertEqual(validate_registry(self.data), [])
        self.assertEqual({e["id"] for e in self.data["platforms"]}, set(PLATFORMS))
        for entry in self.data["platforms"]:
            for field in capabilities.REQUIRED_FIELDS:
                self.assertIn(field, entry, entry["id"])
            self.assertEqual(set(entry["formats"]), set(FORMATS))
            self.assertTrue(all(s.startswith("https://") for s in entry["sources"]))
            date.fromisoformat(entry["verified_date"])

    def test_live_text_adapters_match_supported_live(self):
        live = {e["id"] for e in self.data["platforms"] if e["reachweaver_adapter"] == "live_text"}
        self.assertEqual(live, set(SUPPORTED_LIVE))
        d = deepcopy(self.data)
        self._entry(d, "x")["reachweaver_adapter"] = "live_text"
        self._entry(d, "x")["status"] = "supported"
        self.assertTrue(any("SUPPORTED_LIVE" in e for e in validate_registry(d)))

    def test_missing_field_bad_enum_date_and_source_rejected(self):
        d = deepcopy(self.data)
        del self._entry(d, "linkedin")["fees"]
        self._entry(d, "threads")["formats"]["video"] = "probably"
        self._entry(d, "tiktok")["verified_date"] = "08/10/2026"
        self._entry(d, "youtube")["sources"] = ["http://insecure.example.com/doc"]
        d["platforms"] = [e for e in d["platforms"] if e["id"] != "hashnode"]
        errors = validate_registry(d)
        for needle in ("linkedin: missing fields", "threads: invalid video", "tiktok: verified_date",
                       "youtube: sources", "missing platforms: hashnode"):
            self.assertTrue(any(needle in e for e in errors), needle)

    def test_stated_rate_limit_requires_source(self):
        d = deepcopy(self.data)
        self._entry(d, "x")["rate_limits"]["source"] = ""
        self.assertTrue(any("x: a stated rate limit" in e for e in validate_registry(d)))

    def test_fail_closed_unknown_platform_format_and_action(self):
        with self.assertRaisesRegex(ValidationError, "Unknown platform"):
            require_capability("myspace")
        with self.assertRaisesRegex(ValidationError, "Unknown content format"):
            require_capability("bluesky", "hologram")
        with self.assertRaisesRegex(ValidationError, "Unknown capability action"):
            require_capability("bluesky", "text", "delete")
        with self.assertRaisesRegex(ValidationError, "unknown"):
            require_capability("bluesky", "image", "api")

    def test_gated_manual_unavailable_formats_refused(self):
        cases = [("pinterest", "image", "api_gated"), ("reddit", "text", "api_gated"),
                 ("devto", "text", "manual_export"), ("instagram", "text", "unavailable"),
                 ("threads", "text", "api_gated"), ("linkedin", "carousel", "unavailable")]
        for platform, fmt, status in cases:
            with self.subTest(platform=platform):
                with self.assertRaisesRegex(ValidationError, status):
                    require_capability(platform, fmt, "api")

    def test_api_direct_without_adapter_cannot_publish(self):
        self.assertEqual(require_capability("x", "text", "api")["id"], "x")
        with self.assertRaisesRegex(ValidationError, "No ReachWeaver live adapter"):
            require_capability("x", "text", "publish")
        with self.assertRaisesRegex(ValidationError, "No ReachWeaver live adapter"):
            require_capability("mastodon", "image", "publish")
        for platform in SUPPORTED_LIVE:
            self.assertEqual(require_capability(platform)["reachweaver_adapter"], "live_text")

    def test_invalid_registry_argument_fails_closed(self):
        d = deepcopy(self.data)
        self._entry(d, "bluesky")["formats"]["text"] = "unknown"
        with self.assertRaisesRegex(ValidationError, "Invalid capability registry"):
            require_capability("bluesky", registry=d)

    def test_stale_detection(self):
        d = deepcopy(self.data)
        self._entry(d, "x")["verified_date"] = "2026-01-01"
        self.assertEqual(stale_entries(d, max_age_days=120, today=date(2026, 10, 8)), ["x"])
        self.assertEqual(stale_entries(self.data, max_age_days=120, today=date(2026, 10, 8)), [])
        with self.assertRaises(ValidationError):
            stale_entries(d, max_age_days=0)

    def test_safety_rules_preserved_for_reddit_and_gated_platforms(self):
        reddit = self._entry(self.data, "reddit")
        self.assertEqual(reddit["status"], "blocked")
        joined = " ".join(reddit["compliance_constraints"])
        for phrase in ("moderator-only", "unsolicited DMs", "scraping", "vote"):
            self.assertIn(phrase, joined)
        d = deepcopy(self.data)
        self._entry(d, "reddit")["compliance_constraints"] = ["anything goes"]
        self.assertTrue(any("reddit" in e for e in validate_registry(d)))
        self.assertIn("cost ceiling", " ".join(self._entry(self.data, "x")["compliance_constraints"]))

    def test_publish_guard_blocks_before_network_when_registry_invalid(self):
        account = {"id": "bsky", "platform": "bluesky", "handle": "a.bsky.social", "env_prefix": "RW_T"}
        with tempfile.TemporaryDirectory() as root:
            bad = Path(root) / "platform_registry.json"
            bad.write_text("{}", encoding="utf-8")
            with patch.object(capabilities, "REGISTRY_FILE", bad), \
                    patch("reachweaver.publishers._send_http") as network:
                with self.assertRaisesRegex(ValidationError, "capability registry"):
                    publish(account, "Hello", "post-1")
            network.assert_not_called()


class SourcedRateLimitTests(unittest.TestCase):
    """Issue #11: provider rate limits are exact, sourced and dated, or explicitly unpublished."""

    ACCESSED = "2026-10-09"

    @classmethod
    def setUpClass(cls):
        cls.data = load_registry()

    def _limits(self, data, key):
        return next(e for e in data["platforms"] if e["id"] == key)["rate_limits"]

    def _facts(self, key):
        return {(f["scope"], f["action"], f["window_seconds"]): (f["limit"], f["unit"])
                for f in self._limits(self.data, key)["facts"]}

    def test_bluesky_official_numbers_source_and_access_date(self):
        limits = self._limits(self.data, "bluesky")
        self.assertEqual(limits["source"], "https://bsky.network/docs/rate-limits")
        self.assertEqual(limits["accessed"], self.ACCESSED)
        self.assertFalse(limits["summary"].startswith("unknown"))
        facts = self._facts("bluesky")
        writes = "repo record writes (CREATE 3, UPDATE 2, DELETE 1 point)"
        self.assertEqual(facts[("account", writes, 3600)], (5000, "points"))
        self.assertEqual(facts[("account", writes, 86400)], (35000, "points"))
        self.assertEqual(facts[("account", "com.atproto.server.createSession", 300)], (30, "requests"))
        self.assertEqual(facts[("account", "com.atproto.server.createSession", 86400)], (300, "requests"))
        self.assertEqual(facts[("ip", "all PDS API requests", 300)], (3000, "requests"))
        self.assertIn("not operated by Bluesky", " ".join(limits["not_published"]))

    def test_telegram_official_numbers_source_and_channel_gap(self):
        limits = self._limits(self.data, "telegram")
        self.assertEqual(limits["source"],
                         "https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this")
        self.assertEqual(limits["accessed"], self.ACCESSED)
        by_scope = {(f["scope"], f["window_seconds"]): f["limit"] for f in limits["facts"]}
        self.assertEqual(by_scope, {("chat", 1): 1, ("group", 60): 20, ("bot", 1): 30})
        self.assertTrue(all(f["unit"] == "messages" for f in limits["facts"]))
        # ReachWeaver posts only to channels; the FAQ publishes no channel limit, so it is recorded
        # as unpublished instead of being guessed from the chat or group numbers.
        self.assertIn("channel-specific", " ".join(limits["not_published"]))
        self.assertIn("No channel-specific limit is published", limits["summary"])

    def test_mastodon_and_linkedin_reverified(self):
        mastodon = self._facts("mastodon")
        self.assertEqual(mastodon[("account", "all endpoints and methods", 300)], (300, "requests"))
        self.assertEqual(mastodon[("account", "POST /api/v1/media", 1800)], (30, "requests"))
        linkedin = self._facts("linkedin")
        self.assertEqual(linkedin[("member", "Share on LinkedIn requests (daily limit, UTC day)", 86400)],
                         (150, "requests"))
        self.assertEqual(linkedin[("application", "Share on LinkedIn requests (daily limit, UTC day)", 86400)],
                         (100000, "requests"))
        for key in ("mastodon", "linkedin"):
            self.assertEqual(self._limits(self.data, key)["accessed"], self.ACCESSED)

    def test_every_live_adapter_has_sourced_facts(self):
        for platform in SUPPORTED_LIVE:
            limits = self._limits(self.data, platform)
            self.assertTrue(limits["facts"], platform)
            self.assertTrue(limits["source"].startswith("https://"), platform)
            self.assertIn(limits["source"], next(e for e in self.data["platforms"] if e["id"] == platform)["sources"])
        d = deepcopy(self.data)
        del self._limits(d, "telegram")["facts"]
        self.assertTrue(any("telegram: a live_text adapter needs sourced rate_limits.facts" in e
                            for e in validate_registry(d)))

    def test_malformed_rate_limit_detail_rejected(self):
        good = {"scope": "account", "action": "x", "limit": 1, "unit": "requests", "window_seconds": 1}
        cases = {
            "accessed must be an ISO date": {"accessed": "09/10/2026"},
            "unknown keys guess": {"guess": "1000/day"},
            "not_published must be": {"not_published": []},
            "facts must be a non-empty list": {"facts": []},
            "facts[0]": {"facts": [{**good, "limit": True}]},
        }
        for needle, change in cases.items():
            with self.subTest(needle=needle):
                d = deepcopy(self.data)
                self._limits(d, "bluesky").update(change)
                self.assertTrue(any(needle in e for e in validate_registry(d)), validate_registry(d))
        for bad in ({**good, "window_seconds": 0}, {**good, "limit": -5}, {**good, "unit": " "},
                    {**good, "extra": 1}, {k: v for k, v in good.items() if k != "scope"}, "5/s"):
            with self.subTest(fact=bad):
                d = deepcopy(self.data)
                self._limits(d, "mastodon")["facts"] = [bad]
                self.assertTrue(any("mastodon: rate_limits.facts[0]" in e for e in validate_registry(d)))

    def test_facts_need_stated_summary_source_and_access_date(self):
        fact = {"scope": "account", "action": "x", "limit": 1, "unit": "requests", "window_seconds": 1}
        for change, needle in (({"summary": "unknown (guess)", "source": "https://example.com/doc"},
                                "threads: rate_limits.facts need"),
                               ({"summary": "unknown (guess)", "source": ""}, "threads: rate_limits.accessed needs"),
                               ({"summary": "1 per second", "source": ""}, "threads: a stated rate limit")):
            with self.subTest(change=change):
                d = deepcopy(self.data)
                self._limits(d, "threads").update({"accessed": self.ACCESSED, "facts": [fact], **change})
                self.assertTrue(any(needle in e for e in validate_registry(d)), validate_registry(d))
        d = deepcopy(self.data)
        self._limits(d, "x")["facts"] = [fact]  # stated + HTTPS source, but no access date
        self.assertTrue(any("x: rate_limits.facts need" in e for e in validate_registry(d)))
        d = deepcopy(self.data)
        self._limits(d, "threads")["not_published"] = ["Threads publishing quota"]  # allowed on unknown
        self.assertEqual(validate_registry(d), [])

    def test_loosest_live_budget_within_published_per_account_limits(self):
        """The loosest budget the CLI/caps allow (caps.LIMITS) stays inside every published
        per-account, per-member and per-chat limit. Read-only cross-check; it changes no budget.

        Worst-case provider units per claimed attempt, from the current send path:
        Bluesky createSession preflight (never retried) + createSession + createRecord (3 points);
        Mastodon and LinkedIn identity GET with up to 2 retries + 1 create; Telegram 1 sendMessage.
        IP-, bot- and application-wide limits are shared with other tools and are not bounded by
        a per-account budget, so they are excluded here.
        """
        from reachweaver.caps import LIMITS
        max_attempts, min_gap_seconds = LIMITS["max_per_24h"][1], LIMITS["min_gap_minutes"][0] * 60
        per_attempt = {
            ("bluesky", "repo record writes (CREATE 3, UPDATE 2, DELETE 1 point)"): 3,
            ("bluesky", "com.atproto.server.createSession"): 2,
            ("mastodon", "all endpoints and methods"): 4,
            ("mastodon", "POST /api/v1/media"): 0,
            ("mastodon", "DELETE /api/v1/statuses/:id or POST /api/v1/statuses/:id/unreblog"): 0,
            ("linkedin", "Share on LinkedIn requests (daily limit, UTC day)"): 4,
            ("telegram", "messages sent in a single chat (guidance: avoid exceeding; short bursts may be "
                         "allowed before 429)"): 1,
            ("telegram", "messages sent in a group"): 1,
        }
        checked = 0
        for platform in SUPPORTED_LIVE:
            for fact in self._limits(self.data, platform)["facts"]:
                if fact["scope"] not in ("account", "member", "chat", "group"):
                    continue
                units = per_attempt[(platform, fact["action"])]  # KeyError: classify a new fact
                attempts = min(max_attempts, fact["window_seconds"] // min_gap_seconds + 1)
                with self.subTest(platform=platform, action=fact["action"], window=fact["window_seconds"]):
                    self.assertLessEqual(attempts * units, fact["limit"])
                checked += 1
        self.assertGreaterEqual(checked, 10)


class CapabilityCliTests(unittest.TestCase):
    def test_cli_table_and_json_are_offline(self):
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = _run(["capabilities"])
            self.assertEqual(code, 0)
            self.assertIn("pinterest | requires-review | none", out)
            self.assertIn("not permission", out)
            code, out, _ = _run(["capabilities", "--platform", "reddit", "--json"])
        network.assert_not_called()
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["valid"])
        self.assertEqual([p["id"] for p in payload["platforms"]], ["reddit"])

    def test_cli_unknown_platform_and_invalid_registry_exit_nonzero(self):
        code, _, err = _run(["capabilities", "--platform", "myspace"])
        self.assertEqual(code, 2)
        self.assertIn("Unknown platform", err)
        with tempfile.TemporaryDirectory() as root:
            bad = Path(root) / "platform_registry.json"
            data = json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8"))
            data["platforms"] = data["platforms"][:-1]
            bad.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(capabilities, "REGISTRY_FILE", bad):
                code, _, err = _run(["capabilities"])
        self.assertEqual(code, 2)
        self.assertIn("missing platforms", err)

    def test_cli_warns_on_stale_entries(self):
        with tempfile.TemporaryDirectory() as root:
            old = Path(root) / "platform_registry.json"
            data = json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8"))
            next(e for e in data["platforms"] if e["id"] == "x")["verified_date"] = "2020-01-01"
            old.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(capabilities, "REGISTRY_FILE", old):
                code, out, _ = _run(["capabilities", "--max-age-days", "120"])
        self.assertEqual(code, 0)
        self.assertIn("WARNING: x verified_date older than 120 days", out)

    def test_skill_bundle_and_docs_mirror_registry(self):
        for name in ("capabilities.py", "platform_registry.json"):
            self.assertEqual((ROOT / "reachweaver" / name).read_bytes(),
                             (SKILL_RUNTIME / name).read_bytes())
        doc = (ROOT / "docs/PLATFORMS.md").read_text(encoding="utf-8")
        for platform in PLATFORMS:
            self.assertIn(f"`{platform}`", doc)
        for phrase in ("Periodic update process", "Go/no-go", "cost ceiling", "moderator-only", "Hacker News"):
            self.assertIn(phrase, doc)


if __name__ == "__main__":
    unittest.main()
