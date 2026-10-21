"""Registry freshness: one offline age policy for every bundled research registry.

Every test injects the evaluation date (``today`` / ``--as-of`` / ``as_of``), derived from the registry data
itself where it matters, so nothing here depends on the real clock or on a specific snapshot date.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import socket
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from reachweaver import freshness
from reachweaver.account_advisor import advise, render_text as advisor_text
from reachweaver.capabilities import DEFAULT_MAX_AGE_DAYS
from reachweaver.cli import main
from reachweaver.discovery import build_discovery_plan, render_text as discovery_text
from reachweaver.distribution import build_distribution_plan, load_registry as load_distribution
from reachweaver.profile_audit import load_registry as load_profile

ROOT = Path(__file__).resolve().parents[1]


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class _NoSockets:
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


def _newest_bundled_date() -> date:
    report = freshness.build_report(date(2000, 1, 1), include_fresh=True)
    return max(date.fromisoformat(f["checked_on"]) for f in report["facts"] if f["checked_on"])


def _synthetic(today: date) -> dict:
    """Small registries in each real shape, with dates at fixed ages from ``today``."""
    def ago(days):
        return (today - timedelta(days=days)).isoformat()
    return {
        "capabilities": lambda: {"reviewed_on": ago(0), "platforms": [
            {"id": "x", "verified_date": ago(121), "official_docs_url": "https://docs.x.com/",
             "rate_limits": {"summary": "s", "source": "https://docs.x.com/limits", "accessed": ago(10)}},
            {"id": "bluesky", "verified_date": ago(60), "official_docs_url": "https://docs.bsky.app/"}]},
        "distribution": lambda: {"reviewed_on": ago(0), "platforms": [
            {"id": "facebook_pages", "checked_on": ago(61), "scheduling": {"official_source": "https://www.facebook.com/help"}},
            {"id": "x", "checked_on": ago(200), "scheduling": {"official_source": ""}, "research_source": "https://x.com/"}]},
        "discovery": lambda: {"reviewed_on": ago(0),
                              "default": {"sources": [{"title": "d", "url": "https://example.org/", "checked_on": ago(5)}]},
                              "platforms": {"x": {"sources": [{"title": "t", "url": "https://x.com/a", "checked_on": ago(120)}]}}},
        "profile-audit": lambda: {"reviewed_on": ago(0), "platforms": [
            {"id": "facebook", "evidence": {"tier": "official", "official_source": "https://www.facebook.com/help",
                                            "checked_on": ago(2)},
             "profile_field_limits": [{"field": "bio", "checked_on": ago(130), "source_url": "https://www.facebook.com/help/b"}]},
            {"id": "discord", "evidence": {"tier": "editorial_hypothesis_unverified", "official_source": None,
                                           "checked_on": None}}]},
        "signup": lambda: {"reviewed_on": ago(1), "platforms": {"x": {}, "instagram": {}}},
    }


class PolicyTests(unittest.TestCase):
    def test_thresholds_and_boundaries(self):
        today = date(2030, 6, 15)
        cases = {0: "fresh", 60: "fresh", 61: "aging", 120: "aging", 121: "stale", -3: "fresh"}
        for days, status in cases.items():
            with self.subTest(days=days):
                self.assertEqual(freshness.classify((today - timedelta(days=days)).isoformat(), today), (days, status))
        for value in (None, "", "2030-13-01", "15/06/2030", "2030-6-1", 20300615):
            with self.subTest(value=value):
                self.assertEqual(freshness.classify(value, today), (None, "undated"))

    def test_one_stale_threshold_with_the_capability_registry(self):
        self.assertEqual(freshness.STALE_AFTER_DAYS, DEFAULT_MAX_AGE_DAYS)
        self.assertEqual(freshness.AGING_AFTER_DAYS, 60)
        self.assertLess(freshness.AGING_AFTER_DAYS, freshness.STALE_AFTER_DAYS)

    def test_annotate_and_suffix(self):
        today = date(2030, 6, 15)
        fresh = freshness.annotate("2030-06-01", today)
        self.assertEqual(fresh, {"checked_on": "2030-06-01", "age_days": 14, "freshness": "fresh", "freshness_label": ""})
        self.assertEqual(freshness.suffix(fresh), "checked 2030-06-01")
        aging = freshness.annotate("2030-03-01", today)
        self.assertEqual(freshness.suffix(aging), "checked 2030-03-01, aging - re-verify")
        stale = freshness.annotate("2029-01-01", today)
        self.assertEqual(freshness.suffix(stale), "checked 2029-01-01, stale - re-verify")
        undated = freshness.annotate(None, today)
        self.assertEqual((undated["checked_on"], undated["age_days"], undated["freshness"]), (None, None, "undated"))
        self.assertEqual(freshness.suffix(undated), "undated - unverified")


class ReportTests(unittest.TestCase):
    def test_synthetic_registries_per_fact_per_registry_and_per_platform(self):
        today = date(2030, 6, 15)
        report = freshness.build_report(today, include_fresh=True, loaders=_synthetic(today))
        self.assertEqual(report["as_of"], "2030-06-15")
        self.assertFalse(report["network_requests"])
        rows = {r["registry"]: r for r in report["registries"]}
        self.assertEqual(rows["capabilities"]["counts"], {"fresh": 2, "aging": 0, "stale": 1, "undated": 0})
        self.assertEqual(rows["distribution"]["counts"], {"fresh": 0, "aging": 1, "stale": 1, "undated": 0})
        self.assertEqual(rows["discovery"]["counts"], {"fresh": 1, "aging": 1, "stale": 0, "undated": 0})
        self.assertEqual(rows["profile-audit"]["counts"], {"fresh": 1, "aging": 0, "stale": 1, "undated": 1})
        self.assertEqual(rows["signup"]["counts"], {"fresh": 2, "aging": 0, "stale": 0, "undated": 0})
        signup = [f for f in report["facts"] if f["registry"] == "signup"]
        self.assertTrue(all(f["date_basis"] == "registry_reviewed_on" for f in signup))
        # aliases group one platform across registries; the oldest checked date and worst status per platform
        x = report["platforms"]["x"]
        self.assertEqual((x["status"], x["oldest_checked_on"]), ("stale", (today - timedelta(days=200)).isoformat()))
        self.assertEqual(x["registries"], ["capabilities", "discovery", "distribution", "signup"])
        facebook = report["platforms"]["facebook"]
        self.assertEqual(facebook["registries"], ["distribution", "profile-audit"])
        self.assertEqual(facebook["status"], "stale")
        self.assertEqual(report["platforms"]["discord"]["status"], "undated")
        self.assertEqual(report["counts"]["undated"], 1)
        self.assertEqual(report["total_facts"], sum(report["counts"].values()))
        # stale first, then aging, then undated
        statuses = [f["status"] for f in report["facts"]]
        self.assertEqual(statuses[:3], ["stale", "stale", "stale"])
        self.assertIsNone(next(f for f in report["facts"] if f["platform"] == "discord")["checked_on"])

    def test_default_lists_only_facts_to_reverify_and_filters(self):
        today = date(2030, 6, 15)
        report = freshness.build_report(today, loaders=_synthetic(today))
        self.assertTrue(report["facts"])
        self.assertNotIn("fresh", {f["status"] for f in report["facts"]})
        only = freshness.build_report(today, registry="distribution", platform="facebook", loaders=_synthetic(today))
        self.assertEqual([r["registry"] for r in only["registries"]], ["distribution"])
        self.assertEqual([f["platform"] for f in only["facts"]], ["facebook_pages"])
        with self.assertRaisesRegex(ValueError, "Unknown registry"):
            freshness.build_report(today, registry="nope")

    def test_invalid_registry_is_reported_never_skipped_or_raised(self):
        today = date(2030, 6, 15)
        loaders = _synthetic(today)

        def broken():
            raise ValueError("Invalid distribution registry: platforms must be a non-empty list")
        loaders["distribution"] = broken
        report = freshness.build_report(today, loaders=loaders)
        row = next(r for r in report["registries"] if r["registry"] == "distribution")
        self.assertIn("non-empty list", row["error"])
        self.assertEqual(row["facts"], 0)
        self.assertIn("INVALID registry: distribution", freshness.summary_line(report))
        self.assertIsNotNone(freshness.warning_line(report=report))

    def test_warning_line_only_when_aging_or_stale(self):
        today = date(2030, 6, 15)
        loaders = {name: (lambda: {"reviewed_on": today.isoformat(), "platforms": []})
                   for name in ("capabilities", "distribution", "profile-audit")}
        loaders["discovery"] = lambda: {"platforms": {}}
        loaders["signup"] = lambda: {"reviewed_on": today.isoformat(), "platforms": {"x": {}}}
        self.assertIsNone(freshness.warning_line(report=freshness.build_report(today, loaders=loaders)))
        line = freshness.warning_line(report=freshness.build_report(today, loaders=_synthetic(today)))
        self.assertTrue(line.startswith("WARNING: Research facts as of 2030-06-15:"))
        self.assertIn("reachweaver registry-age", line)
        self.assertNotIn("\n", line)

    def test_platform_summary_needs_every_fact(self):
        today = date(2030, 6, 15)
        with self.assertRaisesRegex(ValueError, "include_fresh"):
            freshness.platform_summary(freshness.build_report(today, loaders=_synthetic(today)), "x")
        full = freshness.build_report(today, include_fresh=True, loaders=_synthetic(today))
        summary = freshness.platform_summary(full, "x", exclude=("capabilities",))
        self.assertEqual(summary["registries"], ["discovery", "distribution", "signup"])
        self.assertEqual(summary["counts"]["stale"], 1)
        self.assertEqual(freshness.platform_summary(full, "nowhere")["status"], "no_facts")

    def test_bundled_registries_all_load_and_undated_matches_the_data(self):
        newest = _newest_bundled_date()
        report = freshness.build_report(newest, include_fresh=True)
        self.assertEqual([r["error"] for r in report["registries"]], [None] * 5)
        self.assertEqual(report["counts"]["stale"] + report["counts"]["aging"], 0)
        # undated = exactly the profile-audit entries whose evidence records no date; nothing is inferred
        expected = sorted(p["id"] for p in load_profile()["platforms"] if not p["evidence"].get("checked_on"))
        undated = sorted(f["platform"] for f in report["facts"] if f["status"] == "undated")
        self.assertEqual(undated, expected)
        self.assertEqual({f["registry"] for f in report["facts"] if f["status"] == "undated"}, {"profile-audit"})
        # the same snapshot, evaluated one day past the stale threshold of its newest date, is all stale
        late = freshness.build_report(newest + timedelta(days=freshness.STALE_AFTER_DAYS + 1))
        self.assertEqual(late["counts"]["fresh"] + late["counts"]["aging"], 0)
        self.assertEqual(late["counts"]["stale"], report["total_facts"] - len(expected))

    def test_module_is_offline_and_read_only(self):
        tree = ast.parse((ROOT / "reachweaver/freshness.py").read_text(encoding="utf-8"))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse(imported & {"urllib", "http", "socket", "sqlite3", "subprocess", "requests"})
        self.assertNotIn("open(", (ROOT / "reachweaver/freshness.py").read_text(encoding="utf-8"))


class CliTests(unittest.TestCase):
    def test_registry_age_json_text_and_exit_codes(self):
        newest = _newest_bundled_date()
        late = (newest + timedelta(days=200)).isoformat()
        with tempfile.TemporaryDirectory() as root, _NoSockets():
            db = os.path.join(root, "never.sqlite")
            code, out, _ = _run("--db", db, "registry-age", "--as-of", late, "--json")
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertEqual(data["report"], "reachweaver_registry_age")
            self.assertEqual(data["as_of"], late)
            self.assertGreater(data["counts"]["stale"], 0)
            self.assertEqual(data["policy"]["stale_after_days"], 120)
            code, out, _ = _run("--db", db, "registry-age", "--as-of", late, "--registry", "distribution",
                                "--platform", "x")
            self.assertEqual(code, 0)
            self.assertIn("STALE    distribution/x: channel and native scheduling", out)
            self.assertIn("OFFLINE", out)
            code, out, _ = _run("--db", db, "registry-age", "--as-of", newest.isoformat(), "--registry", "capabilities")
            self.assertEqual(code, 0)
            self.assertIn("Nothing aging, stale or undated", out)
            code, _, err = _run("--db", db, "registry-age", "--as-of", "21/10/2026")
            self.assertEqual(code, 2)
            self.assertIn("--as-of must be a YYYY-MM-DD date", err)
            self.assertFalse(os.path.exists(db), "registry-age must never create the database")

    def test_doctor_carries_the_summary(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "accounts.json"
            config.write_text(json.dumps({"accounts": []}), encoding="utf-8")
            code, out, _ = _run("--accounts", str(config), "doctor", "--json")
            self.assertEqual(code, 0)
            data = json.loads(out)["registry_freshness"]
            self.assertEqual(set(data["registries"]), set(freshness.REGISTRY_FILES))
            self.assertIn("registry-age", data["line"])
            code, out, _ = _run("--accounts", str(config), "doctor")
            self.assertIn("Research facts as of", out)

    def test_catalog_classifies_registry_age_read_only(self):
        from reachweaver.catalog import COMMANDS
        self.assertEqual(COMMANDS["registry-age"]["tags"], ("read_only",))


class OutputLabelTests(unittest.TestCase):
    """distribution-plan, account-advisor and discovery-plan show each platform fact's checked date and label."""

    def test_distribution_plan_channels_are_dated_and_labelled(self):
        newest = _newest_bundled_date()
        fresh = build_distribution_plan("global", "contributors", today=newest)
        stale = build_distribution_plan("global", "contributors", today=newest + timedelta(days=365))
        checked = {p["id"]: p["checked_on"] for p in load_distribution()["platforms"]}
        for channel in fresh["channels"]:
            self.assertEqual(channel["checked_on"], checked[channel["platform_id"]])
            self.assertEqual(channel["freshness"], "fresh")
            self.assertEqual(channel["freshness_label"], "")
            self.assertIn("account_connected", channel)  # additive: existing keys stay
        self.assertTrue(all(c["freshness"] == "stale" for c in stale["channels"]))
        self.assertTrue(all(c["freshness_label"] == "stale - re-verify" for c in stale["channels"]))
        self.assertEqual(stale["freshness_policy"]["aging_after_days"], 60)
        self.assertEqual(fresh["freshness_as_of"], newest.isoformat())

    def test_distribution_plan_text_shows_checked_date(self):
        code, out, _ = _run("distribution-plan", "--region", "global", "--goal", "contributors", "--limit", "2")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"\| checked \d{4}-\d{2}-\d{2}")

    def test_account_advisor_recommendations_are_dated_and_labelled(self):
        newest = _newest_bundled_date()
        aging = advise(limit=3, setup="bluesky", today=newest + timedelta(days=freshness.AGING_AFTER_DAYS + 30))
        for item in aging["recommendations"] + [aging["setup"]]:
            self.assertIn(item["freshness"], ("aging", "stale"))
            self.assertTrue(item["freshness_label"].endswith("re-verify"))
        text = advisor_text(aging)
        self.assertRegex(text, r"publishing: \w+ \(checked \d{4}-\d{2}-\d{2}, (aging|stale) - re-verify\)")
        self.assertRegex(text, r"Research entry: checked \d{4}-\d{2}-\d{2}, (aging|stale) - re-verify")

    def test_discovery_plan_sources_are_dated_and_labelled_at_as_of(self):
        payload = {"schema_version": 1, "platform": "tiktok", "text": "Offline speech recognition demo on Linux.",
                   "audience": "Linux developers", "keywords": ["Linux"], "as_of": "2026-10-17"}
        plan = build_discovery_plan(payload)
        self.assertTrue(all(s["freshness"] == "fresh" and s["age_days"] == 0 for s in plan["platform_guidance"]["sources"]))
        later = (date(2026, 10, 17) + timedelta(days=400)).isoformat()
        plan = build_discovery_plan({**payload, "as_of": later})
        guidance = plan["platform_guidance"]
        self.assertTrue(all(s["freshness"] == "stale" for s in guidance["sources"]))
        self.assertEqual(guidance["freshness"], "stale")
        self.assertEqual(guidance["checked_on"], min(s["checked_on"] for s in guidance["sources"]))
        self.assertEqual(plan["distribution_entry"]["freshness"], "stale")
        self.assertIn("stale - re-verify", discovery_text(plan))


if __name__ == "__main__":
    unittest.main()
