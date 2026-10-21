"""Learning loops: commands that recommend show the owner's OWN results next to their fixed rules.

discovery-plan (per-tag results), followup-suggest (thresholds derived from your median), experiment-next (saved
readouts), suggest ("What works for you" + research freshness), calendar-plan (your measured blocks next to the
fixed hours) and report (read-only database). Synthetic data only; every database read is checked to change
nothing.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.calendar import propose_calendar, your_results_for
from reachweaver.cli import main
from reachweaver.discovery import add_your_results, build_discovery_plan
from reachweaver.followups import DERIVED_MIN_POSTS, derive_rules
from reachweaver.insights import STRENGTH_OBSERVATION, STRENGTH_TOO_LITTLE, tag_evidence, time_evidence
from reachweaver.storage import SCHEMA, add_post, connect, store_metrics, transition
from reachweaver.suggest import build_suggestions
from test_insights import Seeder

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
START = datetime(2026, 8, 3, 9, tzinfo=timezone.utc)  # a Monday


def memory_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_rust(seed: Seeder, n: int = 6, **kwargs) -> None:
    """n posts tagged #rust with clearly higher interactions and n untagged posts."""
    for i in range(n):
        when = START + timedelta(days=i)
        seed.post(when, f"Notes on build caching #rust part {i}", likes=30 + i, **kwargs)
        seed.post(when + timedelta(hours=3), f"Notes on build caching part {i}", likes=5 + (i % 2), **kwargs)


PLAN = {"schema_version": 1, "platform": "bluesky", "text": "Notes on Rust build caching", "audience": "developers",
        "keywords": ["Rust", "build caching"], "as_of": "2026-10-21"}


class DiscoveryYourResultsTests(unittest.TestCase):
    def setUp(self):
        self.con = memory_db()
        self.addCleanup(self.con.close)
        self.seed = Seeder(self.con)

    def test_tag_evidence_is_shown_and_the_ranking_is_never_changed(self):
        seed_rust(self.seed)
        before = self.con.total_changes
        plain = build_discovery_plan(PLAN)
        plan = add_your_results(build_discovery_plan(PLAN), self.con, now=NOW)
        self.assertEqual(self.con.total_changes, before, "discovery must never write")
        self.assertEqual(plan["recommended"], plain["recommended"])  # same tags, order and scores
        mine = plan["your_results"]
        self.assertEqual((mine["status"], mine["score_adjustment"]), ("ok", None))
        self.assertIn("never folded into editorial_relevance_score", mine["policy"])
        (cohort,) = mine["cohorts"]
        rust = next(t for t in cohort["tags"] if t["tag"] == "#Rust")  # #Rust matches the lowercased #rust
        self.assertEqual((rust["n"], rust["rest_n"], rust["strength"]), (6, 6, STRENGTH_OBSERVATION))
        self.assertGreater(rust["difference"], 0)
        self.assertIn("n=6", rust["text"])
        unused = next(t for t in cohort["tags"] if t["tag"] == "#BuildCaching")
        self.assertEqual((unused["n"], unused["strength"]), (0, STRENGTH_TOO_LITTLE))
        from reachweaver.discovery import render_text
        text = render_text(plan)
        self.assertIn("your results (my-bluesky): n=6", text)
        self.assertIn("Order and scores above are unchanged", text)

    def test_small_groups_say_too_little_data_and_accounts_stay_apart(self):
        seed_rust(self.seed, n=3)
        seed_rust(self.seed, n=6, account="other-bluesky")
        evidence = tag_evidence(self.con, ["#Rust"], platform="bluesky", now=NOW)
        by_account = {c["account_id"]: c["tags"][0] for c in evidence["cohorts"]}
        self.assertEqual(by_account["my-bluesky"]["strength"], STRENGTH_TOO_LITTLE)
        self.assertIn("too little data", by_account["my-bluesky"]["text"])
        self.assertEqual(by_account["other-bluesky"]["strength"], STRENGTH_OBSERVATION)
        only = tag_evidence(self.con, ["#Rust"], platform="bluesky", account_id="other-bluesky", now=NOW)
        self.assertEqual([c["account_id"] for c in only["cohorts"]], ["other-bluesky"])

    def test_no_database_no_results_and_topic_label_surfaces(self):
        self.assertEqual(add_your_results(build_discovery_plan(PLAN), None)["your_results"]["status"], "no_database")
        self.assertEqual(add_your_results(build_discovery_plan(PLAN), self.con)["your_results"]["status"],
                         "no_results")
        threads = add_your_results(build_discovery_plan({**PLAN, "platform": "threads"}), self.con)
        self.assertEqual(threads["your_results"]["status"], "not_applicable")

    def test_cli_reads_an_existing_database_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            db_path = folder / "rw.sqlite"
            with connect(str(db_path)) as con:
                seed_rust(Seeder(con))
            file = folder / "plan.json"
            file.write_text(json.dumps(PLAN), encoding="utf-8")
            before = digest(db_path)
            code, out, err = run("--db", str(db_path), "discovery-plan", "--file", str(file), "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["your_results"]["status"], "ok")
            code, out, _ = run("--db", str(db_path), "discovery-plan", "--file", str(file), "--account", "nobody")
            self.assertEqual(code, 0)
            self.assertNotIn("your results (", out)
            self.assertEqual(digest(db_path), before, "the database bytes must not change")


class FollowupDerivedThresholdTests(unittest.TestCase):
    def setUp(self):
        self.con = memory_db()
        self.addCleanup(self.con.close)
        self.seed = Seeder(self.con)

    def seed_typical(self, n: int = 11, account: str = "my-bluesky", platform: str = "bluesky") -> str:
        """n ordinary posts at 10 interactions (8 + 1 + 1) and one at 40; returns the outlier's id."""
        for i in range(n):
            self.seed.post(START + timedelta(days=i), f"Ordinary update {i}", likes=8, account=account,
                           platform=platform)
        return self.seed.post(START + timedelta(days=n), "The one that took off", likes=38, account=account,
                              platform=platform)

    def test_threshold_is_twice_your_median_and_labelled(self):
        hot = self.seed_typical()
        before = self.con.total_changes
        rules, info = derive_rules(self.con, now=NOW)
        self.assertEqual(self.con.total_changes, before)
        (rule,) = rules
        self.assertEqual((rule["id"], rule["metric"], rule["threshold"], rule["within_hours"], rule["source"]),
                         ("took-off-my-bluesky", "interactions", 20, 192, "bluesky-public-api"))
        (account,) = info["accounts"]
        self.assertEqual((account["status"], account["posts_with_result"], account["median"]), ("derived", 12, 10))
        self.assertIn("derived from your 12 posts", account["text"])
        from reachweaver.followups import build_suggestions as followup_suggestions
        report = followup_suggestions(self.con, rules, now=NOW)
        self.assertEqual([s["post_id"] for s in report["suggestions"]], [hot])
        self.assertEqual(report["suggestions"][0]["value"], 40)
        self.assertIn("Threshold derived from your 12 posts", report["suggestions"][0]["suggestion"])

    def test_other_sources_and_late_snapshots_are_not_compared(self):
        self.seed_typical()
        quiet = self.seed.post(START + timedelta(days=30), "Quiet post", likes=8)
        store_metrics(self.con, quiet, (START + timedelta(days=30, hours=100)).isoformat(), "native-export",
                      likes=500, replies=0, reposts=0)  # a different source never meets this rule
        store_metrics(self.con, quiet, (START + timedelta(days=60)).isoformat(), "bluesky-public-api",
                      likes=500, replies=0, reposts=0)  # far past 192 h: not compared
        rules, _ = derive_rules(self.con, now=NOW)
        from reachweaver.followups import build_suggestions as followup_suggestions
        report = followup_suggestions(self.con, rules, now=NOW)
        self.assertNotIn(quiet, [s["post_id"] for s in report["suggestions"]])

    def test_too_little_data_zero_median_and_human_only(self):
        for i in range(DERIVED_MIN_POSTS - 1):
            self.seed.post(START + timedelta(days=i), f"Early post {i}", likes=8)
        for i in range(12):
            self.seed.post(START + timedelta(days=i), f"Silent {i}", likes=0, replies=0, reposts=0,
                           account="quiet-bluesky")
            self.seed.post(START + timedelta(days=i), f"Thread {i}", likes=9, account="my-reddit", platform="reddit")
        rules, info = derive_rules(self.con, now=NOW)
        self.assertEqual(rules, [])
        status = {a["account_id"]: a["status"] for a in info["accounts"]}
        self.assertEqual(status, {"my-bluesky": "too_little_data", "quiet-bluesky": "median_zero",
                                  "my-reddit": "human_only_platform"})

    def test_cli_without_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "rw.sqlite"
            with connect(str(db_path)) as con:
                for i in range(4):
                    Seeder(con).post(START + timedelta(days=i), f"Early {i}", likes=8)
            code, out, err = run("--db", str(db_path), "followup-suggest")
            self.assertEqual(code, 0, err)
            self.assertIn("Not enough of your own results yet", out)
            self.assertIn("--rules FILE", out)
            code, out, _ = run("--db", str(db_path), "followup-suggest", "--json")
            self.assertEqual(json.loads(out)["status"], "too_little_data")
            with connect(str(db_path)) as con:
                seed = Seeder(con)
                for i in range(10):
                    seed.post(START + timedelta(days=10 + i), f"Ordinary {i}", likes=8)
                hot = seed.post(START + timedelta(days=25), "Took off", likes=60)
            before = digest(db_path)
            state = Path(temp) / "state.json"
            code, out, err = run("--db", str(db_path), "followup-suggest", "--state", str(state), "--record")
            self.assertEqual(code, 0, err)
            self.assertIn("Thresholds derived from your own results", out)
            self.assertIn(hot, out)
            self.assertEqual(digest(db_path), before)
            recorded = json.loads(state.read_text(encoding="utf-8"))["suggested"]
            self.assertEqual([(e["post_id"], e["rule_id"]) for e in recorded], [(hot, "took-off-my-bluesky")])
            code, out, _ = run("--db", str(db_path), "followup-suggest", "--state", str(state), "--json")
            self.assertEqual(json.loads(out)["counts"]["already_suggested"], 1)


def readout(experiment_id: str, dimension: str, label: str, direction: str | None, pairs: int) -> dict:
    return {"kind": "reachweaver.growth-readout", "experiment_id": experiment_id, "dimension": dimension,
            "plan_hash": "sha256:" + "0" * 64, "label": label, "direction": direction, "pairs_usable": pairs}


class ExperimentNextReadoutTests(unittest.TestCase):
    def test_readout_folder_explicit_files_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "readouts"
            folder.mkdir()
            (folder / "a.json").write_text(json.dumps(readout("hook-1", "hook", "directional", "positive", 8)))
            (folder / "b.json").write_text(json.dumps(readout("hook-1", "hook", "inconclusive", None, 3)))
            (folder / "notes.json").write_text(json.dumps({"kind": "something-else"}))
            code, out, err = run("experiment-next", "--readout-dir", str(folder), "--json")
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual(result["next"]["rule"], "replicate")
            self.assertEqual(result["next"]["dimension"], "hook")
            self.assertEqual([r["readout_file"] for r in result["sources"]["readouts_used"]], ["a.json"])
            ignored = {r["file"] for r in result["sources"]["readouts_ignored"]}
            self.assertEqual(ignored, {"b.json", "notes.json"})
            code, text, _ = run("experiment-next", "--readout-dir", str(folder))
            self.assertIn("READOUT a.json: hook-1 (hook, directional, positive, 8 usable pairs)", text)
            # an explicit --readout that is not a readout is an error, never silently skipped
            code, _, err = run("experiment-next", "--readout", str(folder / "notes.json"))
            self.assertEqual(code, 2)
            self.assertIn("not saved 'experiment-readout --json' output", err)

    def test_results_list_still_works_and_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            results = Path(temp) / "results.json"
            results.write_text(json.dumps([{"experiment_id": "hook-1", "dimension": "hook", "label": "inconclusive"}]))
            code, old, _ = run("experiment-next", "--results", str(results), "--json")
            self.assertEqual(code, 0)
            self.assertNotIn("sources", json.loads(old))  # the --results-only output is unchanged
            saved = Path(temp) / "r.json"
            saved.write_text(json.dumps(readout("hook-1", "hook", "directional", "positive", 9)))
            code, out, _ = run("experiment-next", "--results", str(results), "--readout", str(saved), "--json")
            data = json.loads(out)
            self.assertEqual(len(data["results"]), 1)
            self.assertEqual(data["results"][0]["label"], "inconclusive")
            self.assertIn("already in --results", data["sources"]["readouts_ignored"][0]["reason"])
            code, _, err = run("experiment-next")
            self.assertEqual(code, 2)
            self.assertIn("--readout-dir", err)


class SuggestWhatWorksTests(unittest.TestCase):
    def test_what_works_items_and_the_freshness_line(self):
        con = memory_db()
        self.addCleanup(con.close)
        Seeder(con).link_effect(8)
        before = con.total_changes
        result = build_suggestions(con=con, limit=50, registry_warning="WARNING: Research facts as of 2027-03-01: "
                                                                       "3 stale; detail: reachweaver registry-age")
        self.assertEqual(con.total_changes, before)
        works = [a for a in result["actions"] if a["kind"] == "what_works_for_you"]
        self.assertTrue(works)
        first = works[0]
        self.assertEqual((first["priority"], first["confidence"], first["account_id"]),
                         ("P3", "observational_only", "my-bluesky"))
        self.assertEqual(first["evidence"]["source"], "insights_readonly")
        self.assertGreaterEqual(first["evidence"]["n"], 5)
        self.assertTrue(first["title"].startswith("What works for you: try "))
        fresh = [a for a in result["actions"] if a["kind"] == "research_freshness"]
        self.assertEqual(len(fresh), 1)
        self.assertIn("registry-age", fresh[0]["evidence"]["line"])
        quiet = build_suggestions(con=memory_db(), limit=50)
        self.assertFalse([a for a in quiet["actions"] if a["kind"] in ("what_works_for_you", "research_freshness")])

    def test_bare_suggest_cli_shows_what_works(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "rw.sqlite"
            with connect(str(db_path)) as con:
                Seeder(con).link_effect(8)
            before = digest(db_path)
            code, out, err = run("--db", str(db_path), "suggest", "--limit", "50")
            self.assertEqual(code, 0, err)
            self.assertIn("What works for you: try more of posts with a link", out)
            self.assertEqual(digest(db_path), before)


class CalendarYourResultsTests(unittest.TestCase):
    def setUp(self):
        self.con = memory_db()
        self.addCleanup(self.con.close)
        self.seed = Seeder(self.con)

    def seed_evenings(self):
        for i in range(6):
            day = START + timedelta(days=i)
            self.seed.post(day.replace(hour=19), f"Evening note {i}", likes=40 + i)
            self.seed.post(day.replace(hour=8), f"Morning note {i}", likes=4 + (i % 2))

    def proposal(self):
        draft = add_post(self.con, "my-bluesky", "bluesky", "A factual tip about caching", "synthetic")
        return propose_calendar(self.con, account_id="my-bluesky", platform="bluesky", audience_timezone="UTC",
                                start="2026-11-02", days=3, post_ids=[draft], include_drafts=True, now=NOW)

    def test_measured_blocks_are_shown_next_to_unchanged_slots(self):
        self.seed_evenings()
        proposal = self.proposal()
        entries = json.loads(json.dumps(proposal["entries"]))
        before = self.con.total_changes
        mine = your_results_for(self.con, proposal, account_id="my-bluesky", platform="bluesky",
                                audience_timezone="UTC", now=NOW)
        self.assertEqual(self.con.total_changes, before)
        self.assertEqual(proposal["entries"], entries)
        self.assertEqual(mine["status"], "ok")
        self.assertEqual(mine["better_blocks"], ["18:00-24:00"])
        self.assertEqual(mine["fixed_slots"][0]["slot"], "Mon@18:00")  # bluesky's illustrative 18:00 slot
        self.assertEqual(mine["fixed_slots"][0]["block"], "18:00-24:00")
        self.assertIn("n=6", mine["fixed_slots"][0]["your_result"])
        self.assertIn("never used to pick them", mine["slots_unchanged"])
        evening = next(b for b in mine["blocks"] if b["block"] == "18:00-24:00")
        self.assertEqual((evening["n"], evening["strength"]), (6, STRENGTH_OBSERVATION))

    def test_no_results_returns_none_and_small_data_is_labelled(self):
        self.assertIsNone(your_results_for(self.con, self.proposal(), account_id="my-bluesky", platform="bluesky",
                                           audience_timezone="UTC", now=NOW))
        for i in range(3):
            self.seed.post(START + timedelta(days=i), f"Note {i}", likes=8)
        small = time_evidence(self.con, account_id="my-bluesky", platform="bluesky", timezone_name="UTC", now=NOW)
        self.assertEqual((small["status"], small["posts_with_result"]), ("too_little_data", 3))
        self.assertEqual(small["better_blocks"], [])

    def test_cli_adds_your_results_only_to_proposals(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "rw.sqlite")
            config = Path(temp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [{"id": "my-bluesky", "platform": "bluesky",
                                                        "env_prefix": "REACHWEAVER_TEST_PLAN",
                                                        "handle": "test.bsky.social"}]}), encoding="utf-8")
            with connect(db_path) as con:
                self.seed = Seeder(con)
                self.seed_evenings()
                pid = add_post(con, "my-bluesky", "bluesky", "A factual tip")
                transition(con, pid, "approve")
            start = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
            argv = ["--db", db_path, "--accounts", str(config), "calendar-plan", "--account", "my-bluesky",
                    "--timezone", "UTC", "--start", start, "--days", "5", "--post-id", pid]
            code, out, err = run(*argv)
            self.assertEqual(code, 0, err)
            proposal = json.loads(out)
            self.assertEqual(proposal["your_results"]["better_blocks"], ["18:00-24:00"])
            self.assertEqual(proposal["entries"][0]["schedule_status"], "proposed_only")
            code, out, err = run(*argv, "--apply")
            self.assertEqual(code, 0, err)
            self.assertNotIn("your_results", json.loads(out))  # the --apply path is unchanged


class ReportReadOnlyTests(unittest.TestCase):
    def test_missing_database_is_never_created(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "state" / "missing.sqlite"
            code, out, err = run("--db", str(db_path), "report")
            self.assertEqual(code, 0)
            self.assertEqual(set(json.loads(out)), {"generated_at", "platforms", "note"})
            self.assertEqual(json.loads(out)["platforms"], {})
            self.assertIn("nothing to report", err)
            self.assertIn("It was not created", err)
            code, out, _ = run("--db", str(db_path), "report", "--format", "markdown")
            self.assertIn("No published posts.", out)
            self.assertFalse(db_path.exists())
            self.assertFalse(db_path.parent.exists())

    def test_existing_database_is_read_only_and_output_unchanged(self):
        from reachweaver.report_format import render_report
        from reachweaver.storage import report as storage_report
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "rw.sqlite"
            with connect(str(db_path)) as con:
                Seeder(con).link_effect(3)
                expected = storage_report(con)
            before = digest(db_path)
            code, out, err = run("--db", str(db_path), "report")
            self.assertEqual(code, 0, err)
            data = json.loads(out)
            self.assertEqual(data["platforms"], json.loads(json.dumps(expected)))
            self.assertEqual(digest(db_path), before)
            self.assertEqual(out, render_report(data, "json"))

    def test_older_schema_is_refused_not_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "old.sqlite"
            raw = sqlite3.connect(db_path)
            raw.execute("CREATE TABLE posts(id TEXT PRIMARY KEY)")
            raw.commit()
            raw.close()
            before = digest(db_path)
            code, _, err = run("--db", str(db_path), "report")
            self.assertEqual(code, 2)
            self.assertIn("never migrates", err)
            self.assertEqual(digest(db_path), before)


if __name__ == "__main__":
    unittest.main()
