"""Offline posting-history repetition checks (issue #10, slice S6). No network, no publishing.

The database is only ever opened read-only by the code under test; fixtures are written
with the normal storage helpers before the check runs.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from reachweaver import history as history_module
from reachweaver.campaigns import read_json, validate_campaign
from reachweaver.claims import check_claims, jaccard
from reachweaver.cli import main
from reachweaver.history import (
    DEFAULT_FREQUENCY_MAX_POSTS, DEFAULT_FREQUENCY_WINDOW_DAYS, HISTORY_DUPLICATE_THRESHOLD, load_history,
)
from reachweaver.preview import build_preview
from reachweaver.storage import add_post, connect, connect_readonly, event
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "examples/campaign.reachweaver.json"
BRAND = ROOT / "examples/brand.reachweaver.json"
PREVIEW_CAMPAIGN = ROOT / "examples/campaign-preview.reachweaver.json"
PREVIEW_BRAND = ROOT / "examples/brand-preview.reachweaver.json"


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def digest(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def history_codes(report: dict, index: int) -> list[str]:
    return [f["code"] for f in report["variants"][index]["findings"] if f["code"].startswith("history_")]


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "state.sqlite"
        self.campaign = read_json(CAMPAIGN)
        self.brand = read_json(BRAND)
        self.text0 = self.campaign["variants"][0]["text"]  # my-linkedin
        self.text1 = self.campaign["variants"][1]["text"]  # my-bluesky
        with connect(str(self.db)):
            pass

    def post(self, account: str, platform: str, content: str, *, status: str = "published",
             days_ago: float = 1, campaign: str = "", scheduled_days_ago: float | None = None) -> str:
        with connect(str(self.db)) as con:
            pid = add_post(con, account, platform, content, campaign)
            con.execute("UPDATE posts SET status=?, created_at=?, scheduled_at=? WHERE id=?",
                        (status, iso(days_ago + 1), iso(scheduled_days_ago) if scheduled_days_ago is not None
                         else None, pid))
            if status == "published":
                event(con, "published", pid, "remote_id=fixture", at=iso(days_ago))
        return pid

    def history(self, **kw) -> dict:
        return load_history(self.db, self.campaign, **kw)

    def report(self, **kw) -> dict:
        return check_claims(self.campaign, self.brand, history=self.history(**kw))


class NearDuplicateTests(HistoryTestCase):
    def test_exact_duplicate_names_post_status_and_date_but_never_prior_text(self):
        pid = self.post("my-linkedin", "linkedin", self.text0, days_ago=3)
        report = self.report()
        found = [f for f in report["variants"][0]["findings"] if f["code"] == "history_near_duplicate"]
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["severity"], f["matched_post_id"], f["matched_status"]), ("warning", pid, "published"))
        self.assertEqual(f["similarity"], 1.0)
        self.assertEqual(f["matched_at"][:10], iso(3)[:10])
        self.assertIn("identical wording", f["message"])
        self.assertIn(pid, f["message"])
        self.assertNotIn("production operation", json.dumps(f))  # prior text never emitted
        self.assertNotIn("production operation", json.dumps(report["history"]))
        self.assertTrue(report["valid"])  # a warning never blocks
        self.assertEqual(history_codes(report, 1), [])

    def test_near_duplicate_respin_is_flagged_with_its_score(self):
        respin = self.text0.replace("production operation", "production process").replace("most want", "want")
        score = jaccard(self.text0, respin)
        self.assertTrue(HISTORY_DUPLICATE_THRESHOLD <= score < 1.0, score)
        self.post("my-linkedin", "linkedin", respin, status="scheduled", days_ago=0, scheduled_days_ago=-2)
        f = next(f for f in self.report()["variants"][0]["findings"] if f["code"] == "history_near_duplicate")
        self.assertEqual(f["matched_status"], "scheduled")
        self.assertEqual(f["similarity"], round(score, 3))
        self.assertIn("word 3-gram Jaccard", f["message"])

    def test_unrelated_post_is_not_flagged(self):
        self.post("my-linkedin", "linkedin", "Release notes for a completely different project are out today.")
        self.assertEqual(history_codes(self.report(), 0), [])

    def test_other_account_is_ignored(self):
        self.post("other-linkedin", "linkedin", self.text0)
        self.post("my-bluesky", "bluesky", self.text0[:280])
        report = self.report()
        self.assertEqual(history_codes(report, 0), [])
        self.assertEqual(report["history"]["accounts"], ["my-bluesky", "my-linkedin"])

    def test_outside_window_is_ignored_until_the_window_grows(self):
        self.post("my-linkedin", "linkedin", self.text0, days_ago=40)
        self.assertEqual(history_codes(self.report(), 0), [])
        self.assertEqual(history_codes(self.report(days=60), 0), ["history_near_duplicate"])

    def test_cancelled_is_ignored_and_draft_or_needs_review_count(self):
        self.post("my-linkedin", "linkedin", self.text0, status="cancelled")
        self.assertEqual(history_codes(self.report(), 0), [])
        pid = self.post("my-linkedin", "linkedin", self.text0, status="needs_review")
        f = next(f for f in self.report()["variants"][0]["findings"] if f["code"] == "history_near_duplicate")
        self.assertEqual((f["matched_post_id"], f["matched_status"]), (pid, "needs_review"))

    def test_best_match_first_and_other_ids_listed(self):
        older = self.post("my-linkedin", "linkedin", self.text0, days_ago=10)
        newer = self.post("my-linkedin", "linkedin", self.text0, status="draft", days_ago=2)
        f = next(f for f in self.report()["variants"][0]["findings"] if f["code"] == "history_near_duplicate")
        self.assertEqual(f["matched_post_id"], newer)  # ties keep the newest
        self.assertEqual(f["also_matched_post_ids"], [older])

    def test_posts_of_this_same_campaign_are_skipped_and_counted(self):
        self.post("my-linkedin", "linkedin", self.text0, status="draft", campaign=self.campaign["campaign_id"])
        report = self.report()
        self.assertEqual(history_codes(report, 0), [])
        self.assertEqual(report["history"]["excluded_same_campaign"], 1)
        self.assertEqual(report["history"]["posts_considered"], 0)

    def test_truncation_is_reported(self):
        for n in range(3):
            self.post("my-linkedin", "linkedin", f"Distinct note number {n} about queue design.")
        with mock.patch.object(history_module, "HISTORY_MAX_ROWS", 2):
            report = self.report()
        self.assertTrue(report["history"]["truncated"])
        self.assertEqual(report["history"]["posts_considered"], 2)


class FrequencyTests(HistoryTestCase):
    def fill(self, count: int, account: str = "my-bluesky", platform: str = "bluesky", days_ago: float = 1):
        for n in range(count):
            self.post(account, platform, f"Unrelated update {n}: notes on queue item {n * 7}.", days_ago=days_ago)

    def test_frequency_warning_at_default_threshold(self):
        self.fill(DEFAULT_FREQUENCY_MAX_POSTS)
        report = self.report()
        found = [f for f in report["variants"][1]["findings"] if f["code"] == "history_frequency"]
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["severity"], f["existing_posts"], f["campaign_variants"], f["heuristic"]),
                         ("warning", DEFAULT_FREQUENCY_MAX_POSTS, 1, True))
        self.assertEqual(f["window_days"], DEFAULT_FREQUENCY_WINDOW_DAYS)
        self.assertIn("not a platform rule or limit", f["message"])
        self.assertIn("heuristic", report["history"]["note"])
        self.assertEqual(history_codes(report, 0), [])  # the other account is quiet
        self.assertTrue(report["valid"])

    def test_below_threshold_or_outside_window_is_quiet(self):
        self.fill(DEFAULT_FREQUENCY_MAX_POSTS - 1)
        self.fill(3, days_ago=DEFAULT_FREQUENCY_WINDOW_DAYS + 2)
        self.assertEqual(history_codes(self.report(), 1), [])

    def test_threshold_and_window_are_configurable_and_future_schedule_counts(self):
        self.post("my-bluesky", "bluesky", "Scheduled note on queues.", status="scheduled",
                  days_ago=0, scheduled_days_ago=-3)
        self.post("my-bluesky", "bluesky", "Older note on retries.", days_ago=20)
        self.assertEqual(history_codes(self.report(max_posts=2), 1), [])
        self.assertEqual(history_codes(self.report(max_posts=2, window_days=30), 1), ["history_frequency"])

    def test_frequency_is_reported_once_per_account(self):
        self.campaign["variants"].append(dict(self.campaign["variants"][1], id="second-bluesky",
                                              text="A second, unrelated Bluesky draft about receipts."))
        self.fill(DEFAULT_FREQUENCY_MAX_POSTS)
        report = self.report()
        self.assertEqual(history_codes(report, 1), ["history_frequency"])
        self.assertEqual(history_codes(report, 2), [])
        f = next(f for f in report["variants"][1]["findings"] if f["code"] == "history_frequency")
        self.assertEqual(f["campaign_variants"], 2)


class UnicodeTests(HistoryTestCase):
    def assert_flagged(self, prior: str, draft: str):
        self.campaign["variants"][0]["text"] = draft
        self.post("my-linkedin", "linkedin", prior)
        f = next(f for f in self.report()["variants"][0]["findings"] if f["code"] == "history_near_duplicate")
        self.assertEqual(f["similarity"], 1.0)

    def test_case_and_url_differences_are_ignored(self):
        self.assert_flagged("Queue receipts make every send auditable https://example.org/a",
                            "QUEUE RECEIPTS make every send auditable https://example.org/b?utm=x")

    def test_fullwidth_letters_fold_under_nfkc(self):
        self.assert_flagged("Ｑｕｅｕｅ receipts make every send auditable",
                            "Queue receipts make every send auditable")

    def test_decomposed_and_precomposed_accents_match(self):
        self.assert_flagged("Le café publie des reçus pour chaque envoi",
                            "Le café publie des reçus pour chaque envoi")

    def test_zero_width_characters_do_not_split_words(self):
        self.assert_flagged("Queue re​ceipts make every se­nd auditable",
                            "Queue receipts make every send auditable")

    def test_zero_width_space_still_separates_words_in_thai_lao_khmer_and_myanmar(self):
        # Review finding (2026-10-09): dropping every Cf character also removed U+200B where Thai, Lao,
        # Khmer and Myanmar text uses it as the (invisible) word separator, merging a whole line into one
        # token; a one-word edit then scored 0.00 instead of matching the space-separated score.
        zwsp = "​"
        samples = {
            "thai": "แมว เสือ ไก ปลา นก หมา ควาย ลา".split(),
            "lao": "ແມວ ເສືອ ໄກ່ ປາ ນົກ ໝາ ຄວາຍ ລາ".split(),
            "khmer": "ខ្ញុំ ចូលចិត្ត ការ អាន សៀវភៅ ថ្មី នៅ ពេល".split(),
            "myanmar": "ကြောင် ကျား ကြက် ငါး ငှက် ခွေး ကျွဲ မြည်း".split(),
        }
        for script, words in samples.items():
            with self.subTest(script=script):
                changed = words[:4] + ["xyz"] + words[5:]
                spaced = jaccard(" ".join(words), " ".join(changed))
                self.assertGreater(spaced, 0.0)
                self.assertEqual(jaccard(zwsp.join(words), zwsp.join(changed)), spaced)
                self.assertEqual(jaccard(zwsp.join(words), zwsp.join(words)), 1.0)
        # Other invisible format characters are still dropped, inside any script.
        self.assertEqual(jaccard("Queue re⁠ceipts make every se﻿nd audit‮able",
                                 "Queue receipts make every send auditable"), 1.0)

    def test_non_latin_scripts_compare(self):
        self.assert_flagged("صف انتشار هر ارسال را قابل بررسی می‌کند", "صف انتشار هر ارسال را قابل بررسی میکند")

    def test_in_campaign_check_shares_the_normalisation(self):
        self.assertEqual(jaccard("café au lait tonight", "café au lait tonight"), 1.0)


class ReadOnlyAndCliTests(HistoryTestCase):
    def write_campaign(self, campaign: dict | None = None) -> Path:
        path = self.dir / "campaign.json"
        path.write_text(json.dumps(campaign or self.campaign), encoding="utf-8")
        return path

    def test_without_history_behaviour_is_unchanged(self):
        self.post("my-linkedin", "linkedin", self.text0)
        report = check_claims(self.campaign, self.brand)
        self.assertNotIn("history", report)
        self.assertEqual(history_codes(report, 0), [])
        self.assertEqual(validate_campaign(self.campaign, brand=self.brand),
                         validate_campaign(self.campaign, brand=self.brand, history=None))
        path = self.write_campaign()
        code, out, _ = run_cli("campaign-check", "--file", str(path), "--brand", str(BRAND), "--json")
        self.assertNotIn("history", json.loads(out))
        self.assertNotIn("history_", out)
        # the global --db stays inert for campaign-check, as for campaign-preview
        code2, out2, _ = run_cli("--db", str(self.db), "campaign-check", "--file", str(path), "--brand", str(BRAND),
                                 "--json")
        self.assertEqual((code, out), (code2, out2))

    def test_campaign_check_cli_reports_history_and_leaves_db_untouched(self):
        pid = self.post("my-linkedin", "linkedin", self.text0)
        path = self.write_campaign()
        before, files_before = digest(self.db), sorted(os.listdir(self.dir))
        code, out, err = run_cli("campaign-check", "--file", str(path), "--brand", str(BRAND),
                                 "--history-db", str(self.db), "--json")
        data = json.loads(out)
        self.assertEqual(code, 0 if data["valid"] else 2, err)
        self.assertTrue(any("history_near_duplicate" in w and pid in w for w in data["warnings"]))
        self.assertFalse(any("history_" in e for e in data["errors"]))
        self.assertEqual(data["history"]["source"], "local_database_read_only")
        code_text, text, _ = run_cli("campaign-check", "--file", str(path), "--brand", str(BRAND),
                                     "--history-db", str(self.db), "--history-days", "10",
                                     "--history-window-days", "3", "--history-max-posts", "1")
        self.assertEqual(code_text, code)
        self.assertIn("history_frequency", text)
        self.assertIn("database unchanged", text)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(sorted(os.listdir(self.dir)), files_before)  # no journal or -wal left behind

    def test_campaign_preview_cli_adds_warnings_without_changing_confidence(self):
        campaign = read_json(PREVIEW_CAMPAIGN)
        brand = read_json(PREVIEW_BRAND)
        self.campaign = campaign
        self.post("my-mastodon", "mastodon", campaign["variants"][2]["text"])
        plain = build_preview(campaign, brand)
        with_history = build_preview(campaign, brand, history=self.history())
        self.assertEqual([v["confidence"]["level"] for v in plain["variants"]],
                         [v["confidence"]["level"] for v in with_history["variants"]])
        codes = [f["code"] for f in with_history["variants"][2]["findings"]]
        self.assertIn("history_near_duplicate", codes)
        self.assertNotIn("history", plain)
        path = self.write_campaign(campaign)
        before = digest(self.db)
        for shape in ((), ("--json",), ("--markdown",)):
            code, out, _ = run_cli("campaign-preview", "--file", str(path), "--brand", str(PREVIEW_BRAND),
                                   "--history-db", str(self.db), *shape)
            self.assertIn("history_near_duplicate", out)
            self.assertIn("local_database_read_only" if shape == ("--json",) else "Read-only; warnings only", out)
            self.assertIn(code, (0, 2))
        self.assertEqual(digest(self.db), before)

    def test_missing_database_fails_clearly_and_is_not_created(self):
        path = self.write_campaign()
        missing = self.dir / "nested" / "absent.sqlite"
        for command in ("campaign-check", "campaign-preview"):
            code, _, err = run_cli(command, "--file", str(path), "--brand", str(BRAND), "--history-db", str(missing))
            self.assertEqual(code, 2)
            self.assertIn("does not exist", err)
        self.assertFalse(missing.parent.exists())

    def test_history_tuning_without_database_is_an_error(self):
        path = self.write_campaign()
        code, _, err = run_cli("campaign-check", "--file", str(path), "--history-days", "10")
        self.assertEqual(code, 2)
        self.assertIn("--history-days requires --history-db", err)

    def test_out_of_range_values_are_rejected(self):
        for kw in ({"days": 0}, {"days": 366}, {"window_days": 0}, {"max_posts": 0}, {"days": True}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.history(**kw)
        path = self.write_campaign()
        code, _, err = run_cli("campaign-check", "--file", str(path), "--history-db", str(self.db),
                               "--history-days", "400")
        self.assertEqual(code, 2)
        self.assertIn("1..365", err)

    def test_non_reachweaver_files_are_rejected(self):
        empty = self.dir / "other.sqlite"
        con = sqlite3.connect(empty)
        con.execute("CREATE TABLE t (x)")
        con.close()
        garbage = self.dir / "garbage.sqlite"
        garbage.write_bytes(b"not a database at all" * 100)
        for path, needle in ((empty, "no posts table"), (garbage, "Cannot read")):
            with self.subTest(path=path.name), self.assertRaises(ValidationError) as ctx:
                load_history(path, self.campaign)
            self.assertIn(needle, str(ctx.exception))

    def test_symlinked_database_is_refused(self):
        link = self.dir / "link.sqlite"
        link.symlink_to(self.db)
        with self.assertRaises(ValidationError):
            load_history(link, self.campaign)

    def test_readonly_connection_cannot_write(self):
        with connect_readonly(self.db) as con:
            with self.assertRaises(sqlite3.Error):
                con.execute("DELETE FROM posts")


if __name__ == "__main__":
    unittest.main()
