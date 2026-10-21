"""`platform-risk` (multi-homing / dependency) and the market notices of calendar-plan and distribution-plan.

Offline, synthetic data only: a read-only report over the local database plus a sourced registry.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.platform_risk import (
    BASIS_MEASURED, BASIS_POSTS, analyze, concentration, load_registry, market_notices, parse_market, render_text,
    validate_registry,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, connect, record_shared, store_metrics

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"
SENT = datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def published(con, account: str, platform: str, *, views: int | None, sent: datetime = SENT,
              age_hours: int = 168, source: str = "synthetic") -> str:
    pid = add_post(con, account, platform, f"Synthetic note for {account} at {sent.isoformat()}", "risk")
    con.execute("UPDATE posts SET status='published', send_started_at=? WHERE id=?", (sent.isoformat(), pid))
    if views is not None:
        store_metrics(con, pid, (sent + timedelta(hours=age_hours)).isoformat(), source, views=views)
    return pid


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_registry()

    def test_bundled_registry_is_valid_and_every_flag_is_sourced(self):
        self.assertEqual(validate_registry(self.data), [])
        for flag in self.data["flags"]:
            for source in flag["sources"]:
                self.assertTrue(source["url"].startswith("https://"), flag["id"])
                self.assertEqual(source["checked_on"], "2026-10-21", flag["id"])
                self.assertEqual(source["kind"], "official", flag["id"])

    def test_the_owner_requested_flags_quote_their_pages(self):
        by_platform = {f["platform"]: f for f in self.data["flags"]}
        quotes = {p: " ".join(s["quote"] for s in f["sources"]) for p, f in by_platform.items()}
        self.assertIn("$0.200 per request", quotes["x"])
        self.assertIn("automate posting on the LinkedIn Services", quotes["linkedin"])
        self.assertIn("separate agreement with Reddit", quotes["reddit"])
        self.assertIn("retrain, test, and update the content recommendation algorithm", quotes["tiktok"])
        self.assertIn("2026-01-23", by_platform["tiktok"]["label"])

    def test_validator_rejects_unsourced_or_unlabelled_material(self):
        broken = deepcopy(self.data)
        broken["flags"][0]["sources"] = []
        self.assertTrue(any("sources" in e for e in validate_registry(broken)))
        broken = deepcopy(self.data)
        broken["flags"][0]["sources"][0]["checked_on"] = "2027-01-01"
        self.assertTrue(any("checked_on" in e for e in validate_registry(broken)))
        broken = deepcopy(self.data)
        broken["concentration"]["status"] = "industry_standard"
        self.assertTrue(any("editorial_thresholds" in e for e in validate_registry(broken)))
        broken = deepcopy(self.data)
        broken["market_notices"][0]["notice"] = "TikTok is different in the US."
        self.assertTrue(any("re-verify" in e for e in validate_registry(broken)))
        broken = deepcopy(self.data)
        broken["owned_channels"]["status"] = "fact"
        self.assertTrue(any("owned_channels" in e for e in validate_registry(broken)))

    def test_registry_is_packaged_and_mirrored_byte_identical(self):
        self.assertIn('"platform_risk_registry.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn('"platform_risk_registry.json"', (ROOT / "scripts/sync_skill.py").read_text(encoding="utf-8"))
        source = (ROOT / "reachweaver/platform_risk_registry.json").read_bytes()
        self.assertEqual((SKILL / "scripts/reachweaver/platform_risk_registry.json").read_bytes(), source)
        self.assertEqual((ROOT / "plugin/skills/reachweaver/scripts/reachweaver/platform_risk_registry.json")
                         .read_bytes(), source)


class MarketTests(unittest.TestCase):
    def test_parse_market_forms(self):
        self.assertEqual(parse_market("US"), (None, "US"))
        self.assertEqual(parse_market("us"), (None, "US"))
        self.assertEqual(parse_market("global"), (None, "global"))
        self.assertEqual(parse_market("tiktok@US"), ("tiktok", "US"))
        self.assertEqual(parse_market("tiktok@Global"), ("tiktok", "global"))
        for bad in ("mars", "tiktok@", "@US", "tiktok@US@x", "", "TikTok@US"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_market(bad)

    def test_tiktok_notice_flags_us_cites_and_asks_to_reverify(self):
        us = market_notices(["tiktok"], [("tiktok", "US")])
        self.assertEqual(len(us), 1)
        self.assertIs(us[0]["applies"], True)
        self.assertEqual(us[0]["since"], "2026-01-23")
        self.assertIn("re-verify", us[0]["notice"])
        self.assertTrue(us[0]["sources"][0]["url"].startswith("https://newsroom.tiktok.com/"))
        self.assertIs(market_notices(["tiktok"], [(None, "global")])[0]["applies"], False)
        unstated = market_notices(["tiktok"])[0]
        self.assertIsNone(unstated["applies"])
        self.assertIn("--market tiktok@US", unstated["notice"])
        self.assertIs(market_notices(["tiktok"], [(None, "global"), ("tiktok", "US")])[0]["applies"], True)

    def test_no_notice_for_platforms_without_one(self):
        self.assertEqual(market_notices(["bluesky", "linkedin"], [(None, "US")]), [])

    def test_distribution_plan_adds_the_notice_only_when_tiktok_is_listed(self):
        code, out = run("distribution-plan", "--region", "global", "--goal", "awareness", "--limit", "8",
                        "--market", "US", "--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertIn("tiktok", [c["platform_id"] for c in plan["channels"]])
        self.assertIs(plan["market_notices"][0]["applies"], True)
        code, out = run("distribution-plan", "--region", "india", "--goal", "awareness", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("market_notices", json.loads(out))
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = run("distribution-plan", "--region", "global", "--goal", "awareness", "--market", "mars")
        self.assertEqual(code, 2)

    def test_calendar_plan_flags_tiktok_us_without_changing_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "outbox.sqlite")
            config = Path(temp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [
                {"id": "tt-manual", "platform": "tiktok", "env_prefix": "REACHWEAVER_TEST_TTR", "handle": "x"}]}),
                encoding="utf-8")
            with connect(db_path) as db:
                add_post(db, "tt-manual", "tiktok", "A synthetic short video caption")
            start = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
            base = ["--db", db_path, "--accounts", str(config), "calendar-plan", "--account", "tt-manual",
                    "--timezone", "America/New_York", "--start", start, "--days", "8", "--include-drafts"]
            code, plain = run(*base)
            self.assertEqual(code, 0)
            code, flagged = run(*base, "--market", "tiktok@US")
            self.assertEqual(code, 0)
            plain_doc, flagged_doc = json.loads(plain), json.loads(flagged)
            self.assertEqual(plain_doc["entries"], flagged_doc["entries"])
            self.assertIsNone(plain_doc["market_notices"][0]["applies"])
            self.assertIs(flagged_doc["market_notices"][0]["applies"], True)


class ConcentrationTests(unittest.TestCase):
    def test_bands_and_hhi(self):
        one = concentration({"x": 10})
        self.assertEqual((one["hhi"], one["effective_platforms"], one["label"]), (1.0, 1.0, "concentrated"))
        two = concentration({"a": 5, "b": 5})
        self.assertEqual((two["hhi"], two["effective_platforms"], two["label"]), (0.5, 2.0, "few"))
        four = concentration({"a": 1, "b": 1, "c": 1, "d": 1})
        self.assertEqual((four["hhi_points"], four["label"]), (2500, "spread"))
        skewed = concentration({"a": 90, "b": 10})
        self.assertEqual((skewed["top_platform"], skewed["top_share"], skewed["label"]), ("a", 0.9, "concentrated"))
        self.assertEqual(concentration({})["label"], "no_data")
        self.assertEqual(concentration({"a": 0})["label"], "no_data")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.addCleanup(self.con.close)

    def test_measured_basis_with_matched_age_and_latest_fallback_labels(self):
        published(self.con, "my-linkedin", "linkedin", views=600)
        published(self.con, "my-linkedin", "linkedin", views=200, age_hours=400)  # far from 168 h: latest value
        published(self.con, "my-bluesky", "bluesky", views=200)
        report = analyze(self.con, now=NOW)
        overall = report["overall"]
        self.assertEqual(overall["basis"], BASIS_MEASURED)
        self.assertEqual(overall["shares"], {"linkedin": 0.8, "bluesky": 0.2})
        self.assertEqual(overall["top_platform"], "linkedin")
        self.assertEqual(overall["hhi"], 0.68)
        self.assertEqual(report["measurement"]["posts_by_method"], {"latest_not_age_matched": 1, "matched_age": 2})
        linkedin = next(p for p in report["platforms"] if p["platform"] == "linkedin")
        self.assertEqual([f["id"] for f in linkedin["flags"]], ["automated_posting_prohibited"])
        self.assertEqual(linkedin["dependency"], "flagged")
        self.assertEqual(overall["owned_share"], 0)
        self.assertIn("no_owned_share", [a["id"] for a in report["advice"]])
        self.assertFalse(report["database_written"])
        self.assertFalse(report["network_requests"])

    def test_windowed_snapshots_are_never_used(self):
        pid = published(self.con, "my-bluesky", "bluesky", views=None)
        store_metrics(self.con, pid, (SENT + timedelta(hours=168)).isoformat(), "synthetic", views=999,
                      window_start=SENT.isoformat(), window_end=(SENT + timedelta(days=1)).isoformat())
        report = analyze(self.con, now=NOW)
        self.assertEqual(report["overall"]["basis"], BASIS_POSTS)
        self.assertEqual(report["measurement"]["posts_by_method"], {"no_exact_value": 1})

    def test_falls_back_to_post_counts_when_a_platform_has_no_measurement(self):
        published(self.con, "my-tiktok", "tiktok", views=5000)
        published(self.con, "my-mastodon", "mastodon", views=None)
        published(self.con, "my-mastodon", "mastodon", views=None)
        record_shared(self.con, platform="x", account_id=None, content="Shared by hand", images=[], video=None,
                      links=[], posted_at=SENT.isoformat(), url="", note="")
        report = analyze(self.con, now=NOW)
        overall = report["overall"]
        self.assertEqual(overall["basis"], BASIS_POSTS)
        self.assertIn("FALLBACK", overall["basis_note"])
        self.assertIn("mastodon", overall["basis_note"])
        self.assertEqual(overall["shares"], {"mastodon": 0.5, "tiktok": 0.25, "x": 0.25})
        x_row = next(r for r in report["accounts"] if r["platform"] == "x")
        self.assertEqual((x_row["manual_shares"], x_row["account_id"]), (1, "manual:x"))
        self.assertIn("measure_more", [a["id"] for a in report["advice"]])
        self.assertIn("post-count fallback", render_text(report))

    def test_owned_reach_personas_and_markets(self):
        published(self.con, "work-linkedin", "linkedin", views=300)
        published(self.con, "family-bluesky", "bluesky", views=100)
        accounts = {"work-linkedin": {"platform": "linkedin", "persona": "work"},
                    "family-bluesky": {"platform": "bluesky", "persona": "family"}}
        report = analyze(self.con, accounts=accounts, owned_reach={"newsletter": 100}, markets=[("tiktok", "US")],
                         now=NOW)
        self.assertEqual(report["overall"]["owned_share"], 0.2)
        self.assertIn("owned_share", [a["id"] for a in report["advice"]])
        self.assertEqual([s["scope"] for s in report["personas"]], ["persona:family", "persona:work"])
        self.assertEqual(report["personas"][1]["top_share"], 1.0)
        self.assertEqual(report["market_notices"], [])  # no TikTok activity, so no TikTok notice
        newsletter = next(p for p in report["platforms"] if p["platform"] == "newsletter")
        self.assertEqual(newsletter["dependency"], "low_owned_hypothesis")

    def test_filters_and_input_validation(self):
        published(self.con, "my-linkedin", "linkedin", views=100, sent=SENT - timedelta(days=60))
        published(self.con, "my-bluesky", "bluesky", views=100)
        self.assertEqual(analyze(self.con, since=SENT - timedelta(days=1), now=NOW)["overall"]["platforms"], ["bluesky"])
        self.assertEqual(analyze(self.con, account="my-linkedin", now=NOW)["overall"]["platforms"], ["linkedin"])
        self.assertEqual(analyze(self.con, account="nobody", now=NOW)["status"], "no_posts")
        for kwargs in ({"metric": "likes"}, {"age_hours": 10}, {"tolerance_hours": 0},
                       {"age_hours": 48, "tolerance_hours": 48}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                analyze(self.con, now=NOW, **kwargs)


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        os.chmod(self.root, 0o700)
        self.db = str(self.root / "outbox.sqlite")

    def test_read_only_json_and_text(self):
        with connect(self.db) as db:
            published(db, "my-tiktok", "tiktok", views=900)
            published(db, "my-x", "x", views=100)
        before = hashlib.sha256(Path(self.db).read_bytes()).hexdigest()
        files_before = sorted(p.name for p in self.root.iterdir())
        code, out = run("--db", self.db, "--accounts", str(self.root / "missing.json"), "platform-risk", "--json",
                        "--market", "tiktok@US", "--owned-reach", "site=250", "--now", NOW.isoformat())
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["kind"], "reachweaver.platform-risk")
        self.assertEqual(report["overall"]["top_platform"], "tiktok")
        self.assertIs(report["market_notices"][0]["applies"], True)
        x = next(p for p in report["platforms"] if p["platform"] == "x")
        self.assertEqual([f["id"] for f in x["flags"]], ["pay_per_use_api"])
        code, text = run("--db", self.db, "platform-risk", "--now", NOW.isoformat())
        self.assertEqual(code, 0)
        self.assertIn("Hypotheses to check", text)
        self.assertEqual(hashlib.sha256(Path(self.db).read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), files_before)

    def test_missing_database_is_reported_not_created(self):
        code, out = run("--db", self.db, "platform-risk", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "no_database")
        self.assertFalse(Path(self.db).exists())

    def test_bad_owned_value_is_an_input_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = run("--db", self.db, "platform-risk", "--owned-reach", "Newsletter=lots")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
