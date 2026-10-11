"""baseline-eval (issue #16): known-answer statistics, determinism, refusals, Holm, read-only CLI."""
from __future__ import annotations

import contextlib
import io
import json
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path
from statistics import median

from reachweaver.baseline_eval import (
    EXACT_PERMUTATION_LIMIT, STATUSES, bootstrap_independent, bootstrap_paired, evaluate_against_baseline,
    holm_bonferroni, permutation_p, quantile, render_text,
)
from reachweaver.calendar import generic_baseline_slots
from reachweaver.cli import main
from reachweaver.growth_readout import run_readout
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, connect, store_metrics
from test_growth_readout import growth_plan, post_map, record_for, seed_pairs
from test_no_network import NetworkGuard

# 2026-09-01 is a Tuesday. Bluesky's generic calendar-plan slot is 18:00 every day.
TUESDAYS = (1, 8, 15, 22, 29)


class StatisticsKnownAnswerTests(unittest.TestCase):
    def test_holm_known_answer_and_cap(self):
        # sorted 0.005*4=0.02, 0.01*3=0.03, 0.03*2=0.06, 0.04*1=0.04 -> running max 0.06
        self.assertEqual([round(p, 10) for p in holm_bonferroni([0.01, 0.04, 0.03, 0.005])],
                         [0.03, 0.06, 0.06, 0.02])
        self.assertEqual(holm_bonferroni([0.5, 0.6]), [1.0, 1.0])
        self.assertEqual(holm_bonferroni([0.2]), [0.2])
        self.assertEqual(holm_bonferroni([]), [])

    def test_holm_preserves_order_and_is_never_below_raw_or_above_bonferroni(self):
        rng = random.Random(7)
        for _ in range(200):
            raw = [rng.random() for _ in range(rng.randint(1, 8))]
            adjusted = holm_bonferroni(raw)
            for i, j in combinations(range(len(raw)), 2):
                if raw[i] <= raw[j]:
                    self.assertLessEqual(adjusted[i], adjusted[j])
            for p, adj in zip(raw, adjusted):
                self.assertGreaterEqual(adj, p)
                self.assertLessEqual(adj, min(1.0, len(raw) * p) + 1e-12)

    def test_holm_rejects_invalid_p_values(self):
        for bad in ([1.5], [-0.1], [True], ["0.1"]):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                holm_bonferroni(bad)

    def test_quantile_is_type_7(self):
        self.assertEqual(quantile([1, 2, 3, 4], 0.25), 1.75)
        self.assertEqual(quantile([4, 1, 3, 2], 0.5), 2.5)
        self.assertEqual(quantile([5], 0.9), 5)
        with self.assertRaises(ValidationError):
            quantile([], 0.5)

    def test_exact_permutation_known_answers(self):
        # 20 relabellings of 3 vs 3; |median diff| >= 3 only for {1,2,3}, {1,2,4} and their complements.
        self.assertEqual(permutation_p([1, 2, 3], [4, 5, 6], resamples=200, rng=random.Random(0)),
                         (0.2, "exact_permutation"))
        # Even perfectly separated 4 vs 4 groups give 4/70 > 0.05: small samples stay uncertain.
        p, method = permutation_p([1, 2, 3, 4], [5, 6, 7, 8], resamples=200, rng=random.Random(0))
        self.assertEqual((round(p, 10), method), (round(4 / 70, 10), "exact_permutation"))
        self.assertEqual(permutation_p([1, 1, 1], [1, 1, 1], resamples=200, rng=random.Random(0))[0], 1.0)

    def test_exact_permutation_matches_brute_force(self):
        rng = random.Random(3)
        for _ in range(20):
            a = [rng.randint(0, 9) / 100 for _ in range(rng.randint(3, 6))]
            b = [rng.randint(0, 9) / 100 for _ in range(rng.randint(3, 6))]
            pooled, observed, hits, total = a + b, abs(median(a) - median(b)), 0, 0
            for chosen in combinations(range(len(pooled)), len(a)):
                x = [pooled[i] for i in chosen]
                y = [pooled[i] for i in range(len(pooled)) if i not in chosen]
                total += 1
                hits += abs(median(x) - median(y)) >= observed - 1e-12
            self.assertAlmostEqual(permutation_p(a, b, resamples=200, rng=random.Random(0))[0], hits / total)

    def test_monte_carlo_permutation_is_seeded_and_never_zero(self):
        a, b = [0.01 * i for i in range(10)], [0.5 + 0.01 * i for i in range(10)]  # comb(20, 10) > limit
        first = permutation_p(a, b, resamples=500, rng=random.Random("s"))
        second = permutation_p(a, b, resamples=500, rng=random.Random("s"))
        self.assertEqual(first, second)
        self.assertEqual(first[1], "monte_carlo_permutation")
        self.assertEqual(first[0], 1 / 501)  # every shuffle is less extreme; add-one keeps p above 0
        self.assertGreater(184756, EXACT_PERMUTATION_LIMIT)

    def test_bootstrap_degenerate_and_zero_baseline(self):
        out = bootstrap_independent([0.2] * 5, [0.1] * 5, resamples=300, confidence=0.95, rng=random.Random(1))
        self.assertEqual([round(v, 12) for v in out["difference"]], [0.1, 0.1])
        self.assertEqual([round(v, 12) for v in out["relative"]], [1.0, 1.0])
        out = bootstrap_independent([0.2] * 5, [0.0] * 5, resamples=300, confidence=0.95, rng=random.Random(1))
        self.assertIsNone(out["relative"])
        self.assertEqual(bootstrap_paired([0.005] * 6, resamples=300, confidence=0.95, rng=random.Random(1)),
                         [0.005, 0.005])

    def test_bootstrap_interval_brackets_estimate_and_narrows_with_confidence(self):
        rng = random.Random(9)
        a = [0.02 + rng.random() / 100 for _ in range(12)]
        b = [0.015 + rng.random() / 100 for _ in range(12)]
        wide = bootstrap_independent(a, b, resamples=2000, confidence=0.95, rng=random.Random(2))["difference"]
        narrow = bootstrap_independent(a, b, resamples=2000, confidence=0.8, rng=random.Random(2))["difference"]
        self.assertLessEqual(wide[0], median(a) - median(b))
        self.assertGreaterEqual(wide[1], median(a) - median(b))
        self.assertLessEqual(wide[0], narrow[0])
        self.assertGreaterEqual(wide[1], narrow[1])


class BaselineDbCase(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)

    def post(self, when: datetime, clicks: int | None, *, impressions: int = 1000, campaign: str = "same-format",
             account: str = "my-bluesky", platform: str = "bluesky", source: str = "native-export") -> str:
        post_id = add_post(self.db, account, platform, f"Genuine note {when.isoformat()} {clicks}", campaign)
        self.db.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?", (when.isoformat(), post_id))
        if clicks is not None:
            store_metrics(self.db, post_id, (when + timedelta(hours=168)).isoformat(), source,
                          clicks=clicks, impressions=impressions)
        return post_id

    def seed_baseline(self, clicks=(10, 11, 12, 10, 11, 12, 10, 11)):
        for offset, value in enumerate(clicks):  # daily 18:00 UTC from Tue 2026-09-01
            self.post(datetime(2026, 9, 1 + offset, 18, tzinfo=timezone.utc), value)

    def seed_tuesday_mornings(self, clicks=(20, 21, 22, 23, 24), hour=9):
        for day, value in zip(TUESDAYS, clicks):
            self.post(datetime(2026, 9, day, hour, tzinfo=timezone.utc), value)

    def run_eval(self, **changes):
        params = dict(account_id="my-bluesky", platform="bluesky", audience_timezone="UTC", source="native-export",
                      metric="clicks", denominator="impressions", since="2026-09-01", until="2026-09-30",
                      slots=["exploit:Tue@09:00"])
        params.update(changes)
        return evaluate_against_baseline(self.db, **params)


class BaselineComparisonTests(BaselineDbCase):
    def test_known_answer_comparison_is_correlational_and_read_only(self):
        self.seed_baseline()
        self.seed_tuesday_mornings()
        before = (self.db.total_changes, self.db.execute("SELECT count(*) FROM event_log").fetchone()[0])
        result = self.run_eval()
        self.assertEqual((self.db.total_changes, self.db.execute("SELECT count(*) FROM event_log").fetchone()[0]),
                         before)
        self.assertEqual(result["baseline"]["slots"], [f"{d}@18:00" for d in
                                                       ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")])
        self.assertEqual(result["baseline"]["median_rate"], 0.011)
        self.assertEqual(result["baseline"]["evidence"], "industry_observational")
        self.assertEqual(result["randomized"], [])
        (item,) = result["correlational"]
        self.assertEqual((item["kind"], item["evidence"], item["role"]), ("baseline_comparison", "correlational", "exploit"))
        self.assertEqual(item["arm_summary"]["median_rate"], 0.022)
        self.assertEqual((item["estimate"], item["relative_estimate"]), (0.011, 1.0))
        # comb(13, 5) = 1287 relabellings, exact; 30 are at least as extreme.
        self.assertEqual((item["p_value"], item["p_value_method"]), (round(30 / 1287, 6), "exact_permutation"))
        self.assertEqual(item["holm_adjusted_p"], item["p_value"])
        self.assertLessEqual(item["interval"][0], item["estimate"])
        self.assertGreaterEqual(item["interval"][1], item["estimate"])
        self.assertGreater(item["interval"][0], 0)
        self.assertEqual((item["status"], item["direction"]), ("separated_after_holm", "arm_higher"))
        self.assertIn(item["status"], STATUSES)
        self.assertEqual(result["multiple_testing"]["family_size"], 1)
        self.assertEqual(result["posts"]["published_in_period"], 13)
        self.assertEqual(result["explore_exploit"]["exploit_posts"], 5)
        self.assertEqual(result["explore_exploit"]["exploration_share"], 0.0)
        text = render_text(result)
        self.assertIn("CORRELATIONAL (not randomized):", text)
        self.assertIn("separated_after_holm", text)
        self.assertNotIn("significant", json.dumps(result))
        for entry in result["correlational"]:
            self.assertFalse(any("proven" in e for e in entry["explanations"]))

    def test_fixed_seed_is_deterministic_and_independent_of_arm_order(self):
        self.seed_baseline(tuple(10 + (i * 7) % 5 for i in range(12)))
        tuesday, saturday = datetime(2026, 9, 1, 9, tzinfo=timezone.utc), datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        for week, value in enumerate((20, 18, 25, 19, 22, 21, 23)):
            self.post(tuesday + timedelta(days=7 * week), value)
        for week, value in enumerate((9, 14, 11, 13, 12)):
            self.post(saturday + timedelta(days=7 * week), value)
        kwargs = dict(until="2026-10-31", slots=["Tue@09:00", "Sat@12:00"], resamples=500)
        first, second = self.run_eval(**kwargs), self.run_eval(**kwargs)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        swapped = self.run_eval(**{**kwargs, "slots": ["Sat@12:00", "Tue@09:00"]})
        by_id = {item["id"]: item for item in swapped["correlational"]}
        for item in first["correlational"]:
            self.assertEqual(item, by_id[item["id"]])
        other_seed = self.run_eval(**kwargs, seed=99)
        self.assertEqual([i["estimate"] for i in other_seed["correlational"]],
                         [i["estimate"] for i in first["correlational"]])

    def test_monte_carlo_path_is_deterministic(self):
        self.seed_baseline(tuple(10 + i % 4 for i in range(12)))
        for week in range(10):
            when = datetime(2026, 9, 1, 9, tzinfo=timezone.utc) + timedelta(days=7 * week)
            self.post(when, 14 + week % 5)
        kwargs = dict(until="2026-11-30", resamples=400)
        first = self.run_eval(**kwargs)["correlational"][0]
        self.assertEqual(first["p_value_method"], "monte_carlo_permutation")  # comb(22, 10) > limit
        self.assertEqual(first, self.run_eval(**kwargs)["correlational"][0])

    def test_small_samples_are_refused_not_zeroed(self):
        self.seed_baseline()
        self.seed_tuesday_mornings(clicks=(20, 21, 22, 23))
        result = self.run_eval()
        (item,) = result["correlational"]
        self.assertEqual(item["status"], "insufficient_data")
        for key in ("estimate", "relative_estimate", "interval", "p_value", "holm_adjusted_p", "direction"):
            self.assertIsNone(item[key], key)
        self.assertIn("at least 5 each", item["explanations"][0])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["multiple_testing"]["family_size"], 0)
        # Lowering the minimum is explicit and bounded.
        self.assertEqual(self.run_eval(min_posts=4)["correlational"][0]["status"], "separated_after_holm")
        with self.assertRaises(ValidationError):
            self.run_eval(min_posts=2)

    def test_missing_snapshots_are_counted_never_treated_as_zero(self):
        self.seed_baseline()
        self.seed_tuesday_mornings()
        self.post(datetime(2026, 9, 1, 9, 10, tzinfo=timezone.utc), None)  # arm post without snapshot
        self.post(datetime(2026, 9, 2, 18, tzinfo=timezone.utc), 10, source="other-source")
        result = self.run_eval()
        self.assertEqual(result["posts"]["excluded"],
                         {"no_matched_snapshot:baseline": 1, "no_matched_snapshot:slot:Tue@09:00": 1})
        item = result["correlational"][0]
        self.assertEqual(item["arm_posts_in_period"], 6)
        self.assertEqual(item["arm_summary"]["posts_with_rate"], 5)
        self.assertEqual(item["arm_summary"]["median_rate"], 0.022)

    def test_period_is_explicit_and_filters_by_audience_local_date(self):
        self.seed_baseline()
        self.seed_tuesday_mornings()
        self.post(datetime(2026, 10, 6, 9, tzinfo=timezone.utc), 99)  # outside the period
        result = self.run_eval()
        self.assertEqual(result["posts"]["published_outside_period"], 1)
        self.assertEqual(result["correlational"][0]["arm_summary"]["posts_with_rate"], 5)
        # 2026-09-01 18:00 UTC is 2026-09-02 03:00 in Tokyo, so it leaves a period ending 09-01.
        # 2026-09-01 09:00 UTC is 18:00 local in Tokyo, the generic slot, so it is the only post in the period.
        tokyo = self.run_eval(audience_timezone="Asia/Tokyo", until="2026-09-01", slots=["Wed@03:00"])
        self.assertEqual(tokyo["posts"]["published_in_period"], 1)
        self.assertEqual(tokyo["posts"]["assigned"], {"baseline": 1})
        for bad in (dict(since=None), dict(until="2026-9-30"), dict(since="2026-10-01"),
                    dict(until="2026-02-30"), dict(until="2027-12-31")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.run_eval(**bad)

    def test_holm_corrects_several_comparisons_in_order(self):
        self.seed_baseline()
        self.seed_tuesday_mornings()
        thursday = datetime(2026, 9, 3, 9, tzinfo=timezone.utc)
        for week, value in enumerate((12, 11, 13, 10, 12)):  # Thursdays 09:00, close to the baseline
            self.post(thursday + timedelta(days=7 * week), value)
        saturday, sunday = datetime(2026, 9, 5, 12, tzinfo=timezone.utc), datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
        for week, value in enumerate((12, 13, 11, 14, 12)):  # Saturdays 12:00, overlapping the baseline
            self.post(saturday + timedelta(days=7 * week), value)
        for week, value in enumerate((16, 15, 17, 14)):  # Sundays 12:00, four posts only
            self.post(sunday + timedelta(days=7 * week), value)
        alone = self.run_eval()["correlational"][0]
        self.assertEqual(alone["status"], "separated_after_holm")
        result = self.run_eval(until="2026-10-03",
                               slots=["exploit:Tue@09:00", "Thu@09:00", "Sat@12:00", "Sun@12:00"])
        items = {item["id"]: item for item in result["correlational"]}
        tested = [items[k] for k in ("slot:Tue@09:00", "slot:Thu@09:00", "slot:Sat@12:00")]
        self.assertEqual(result["multiple_testing"]["family_size"], 3)
        self.assertEqual(items["slot:Sun@12:00"]["status"], "insufficient_data")
        self.assertIsNone(items["slot:Sun@12:00"]["holm_adjusted_p"])
        expected = holm_bonferroni([item["p_value"] for item in tested])
        self.assertEqual([item["holm_adjusted_p"] for item in tested], [round(p, 6) for p in expected])
        ordered = sorted(tested, key=lambda item: item["p_value"])
        self.assertIs(ordered[0], items["slot:Tue@09:00"])
        self.assertEqual([i["holm_adjusted_p"] for i in ordered], sorted(i["holm_adjusted_p"] for i in ordered))
        # The strongest arm's adjusted p triples: it is no longer separated once corrected.
        strongest = items["slot:Tue@09:00"]
        self.assertEqual(strongest["p_value"], alone["p_value"])
        self.assertEqual(strongest["holm_adjusted_p"], round(3 * strongest["p_value"], 6))
        self.assertGreater(strongest["holm_adjusted_p"], 0.05)
        self.assertEqual(strongest["status"], "no_clear_difference")
        self.assertTrue(any("Holm-adjusted p" in e for e in strongest["explanations"]))
        self.assertTrue(any("exploit arm" in e for e in strongest["explanations"]))
        self.assertEqual(result["explore_exploit"]["explore_posts"], 14)
        self.assertEqual(result["explore_exploit"]["exploration_share"], round(14 / 19, 4))

    def test_campaign_arms_and_ambiguous_posts(self):
        self.seed_baseline()
        for day, value in zip((2, 3, 4, 5, 6), (30, 31, 32, 33, 34)):  # variant campaign AT the generic slot
            self.post(datetime(2026, 9, day, 18, 5, tzinfo=timezone.utc), value, campaign="variant-proof")
        self.post(datetime(2026, 9, 8, 9, tzinfo=timezone.utc), 50, campaign="variant-proof")  # also Tue@09:00
        self.post(datetime(2026, 9, 9, 3, tzinfo=timezone.utc), 50)  # matches nothing
        result = self.run_eval(slots=["Tue@09:00"], campaign_arms=["variant-proof"])
        items = {item["id"]: item for item in result["correlational"]}
        campaign = items["campaign:variant-proof"]
        self.assertEqual((campaign["arm_kind"], campaign["role"]), ("campaign", "explore"))
        self.assertEqual(campaign["arm_summary"]["posts_with_rate"], 5)
        self.assertEqual(result["baseline"]["posts_with_rate"], 8)  # campaign posts never join the baseline
        self.assertEqual(result["posts"]["excluded"], {"ambiguous_arm": 1})
        self.assertEqual(result["posts"]["unassigned_in_period"], 1)

    def test_invalid_arms_and_missing_baseline_are_refused(self):
        cases = (
            dict(slots=["Tue@18:30"]),                    # overlaps the generic 18:00 slot
            dict(slots=["Tue@09:00", "Tue@10:30"]),       # two arms overlap
            dict(slots=["Tue@09:00", "explore:Tue@09:00"]),
            dict(slots=["exploit:"]), dict(slots=["Tuesday 9am"]), dict(slots=[]),
            dict(confidence=1.0), dict(resamples=10), dict(slot_tolerance_minutes=500),
            dict(source=""), dict(metric="impressions"),
            dict(slots=[f"{d}@{h:02d}:00" for d in ("Mon", "Tue") for h in (0, 3, 6, 9, 12, 15)] + ["Wed@03:00"]),
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValidationError):
                self.run_eval(**case)
        self.post(datetime(2026, 9, 1, 9, tzinfo=timezone.utc), 10, account="my-mastodon", platform="mastodon")
        self.assertIsNone(generic_baseline_slots("mastodon"))
        with self.assertRaisesRegex(ValidationError, "no generic non-personalized slot"):
            self.run_eval(account_id="my-mastodon", platform="mastodon")
        self.assertEqual(self.run_eval(slot_tolerance_minutes=0)["baseline"]["slot_tolerance_minutes"], 0)


class ReadoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = connect(str(Path(self.tmp.name) / "state" / "t.sqlite"))
        self.con = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def readout(self, clicks, **plan_changes) -> dict:
        plan = growth_plan(**plan_changes)
        pairs = seed_pairs(self.con, clicks)
        return json.loads(json.dumps(run_readout(self.con, record_for(plan),
                                                 post_map(pairs, experiment_id=plan["experiment_id"]))))

    def run_eval(self, readouts, **changes):
        params = dict(account_id="my-bluesky", platform="bluesky", audience_timezone="UTC", source="native-export",
                      metric="clicks", denominator="impressions", since="2026-09-01", until="2026-11-30",
                      readouts=readouts)
        params.update(changes)
        return evaluate_against_baseline(self.con, **params)

    def test_single_directional_readout_keeps_its_label_and_survives_holm(self):
        readout = self.readout([(10, 15)] * 6)
        self.assertEqual(readout["label"], "directional")
        before = self.con.total_changes
        result = self.run_eval([readout])
        self.assertEqual(self.con.total_changes, before)
        (item,) = result["correlational"]  # the example plan is unrandomized
        self.assertEqual(result["randomized"], [])
        self.assertEqual((item["kind"], item["role"], item["readout_label"]),
                         ("preregistered_experiment", "explore", "directional"))
        self.assertEqual(item["design"]["interpretation"], "associational")
        self.assertEqual((item["p_value"], item["p_value_method"]), (0.03125, "readout_sign_test"))
        self.assertEqual((item["estimate"], item["interval"]), (0.005, [0.005, 0.005]))
        self.assertEqual(item["status"], "separated_after_holm")

    def test_two_experiments_at_once_are_holm_corrected_and_labels_are_untouched(self):
        first = self.readout([(10, 15)] * 6)
        second = self.readout([(10, 16)] * 6, experiment_id="hook-proof-vs-generic-2026-11")
        result = self.run_eval([first, second])
        self.assertEqual(result["multiple_testing"]["family_size"], 2)
        for item in result["correlational"]:
            self.assertEqual(item["readout_label"], "directional")
            self.assertEqual(item["holm_adjusted_p"], 0.0625)
            self.assertEqual(item["status"], "no_clear_difference")
            self.assertTrue(any("not after correction" in e for e in item["explanations"]))

    def test_randomized_readouts_are_kept_apart_from_correlational(self):
        randomized = self.readout([(10, 15)] * 6, assignment="randomized")
        plain = self.readout([(10, 15)] * 6, experiment_id="hook-proof-vs-generic-2026-11")
        result = self.run_eval([randomized, plain])
        self.assertEqual([i["experiment_id"] for i in result["randomized"]], [randomized["experiment_id"]])
        self.assertEqual([i["experiment_id"] for i in result["correlational"]], [plain["experiment_id"]])
        self.assertFalse(result["randomized"][0]["design"]["verified"])
        self.assertTrue(any("owner's declaration" in e for e in result["randomized"][0]["explanations"]))
        self.assertIn("RANDOMIZED (owner-declared, unverified):", render_text(result))

    def test_gate_is_never_upgraded_and_small_readouts_are_not_tested(self):
        fake = self.readout([(10, 15)] * 6)
        fake["label"] = "inconclusive"  # the strict gate said no; strong numbers must not override it
        result = self.run_eval([fake])
        (item,) = result["correlational"]
        self.assertEqual(item["status"], "no_clear_difference")
        self.assertTrue(any("never upgrades" in e for e in item["explanations"]))
        small = self.readout([(10, 15)] * 3, minimum_pairs=3, experiment_id="hook-proof-vs-generic-2026-12")
        (item,) = self.run_eval([small])["correlational"]
        self.assertEqual((item["status"], item["p_value"], item["estimate"]), ("insufficient_data", None, None))

    def test_period_cohort_and_kind_checks(self):
        readout = self.readout([(10, 15)] * 6)
        result = self.run_eval([readout], until="2026-09-20")
        self.assertEqual(result["correlational"], [])
        self.assertEqual(result["excluded_readouts"][0]["reason"], "pairs_outside_period_or_not_in_database")
        self.assertEqual(result["status"], "insufficient_data")
        for bad in ({**readout, "kind": "other"}, [], {**readout, "label": "proven"}):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValidationError):
                self.run_eval([bad])
        with self.assertRaisesRegex(ValidationError, "cohorts are never pooled"):
            self.run_eval([readout], account_id="someone-else")
        with self.assertRaisesRegex(ValidationError, "listed twice"):
            self.run_eval([readout, readout])
        # A platform without a generic slot can still report readouts, with no baseline block.
        self.assertIsNone(generic_baseline_slots("telegram"))


class BaselineEvalCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.db = str(base / "state" / "outbox.sqlite")
        self.accounts = base / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_T_BSKY",
             "handle": "example.bsky.social"}]}), encoding="utf-8")
        with connect(self.db) as con:
            def post(when, clicks):
                post_id = add_post(con, "my-bluesky", "bluesky", f"Genuine note {when.isoformat()}", "c")
                con.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?",
                            (when.isoformat(), post_id))
                store_metrics(con, post_id, (when + timedelta(hours=168)).isoformat(), "native-export",
                              clicks=clicks, impressions=1000)
            for offset, value in enumerate((10, 11, 12, 10, 11, 12, 10, 11)):
                post(datetime(2026, 9, 1 + offset, 18, tzinfo=timezone.utc), value)
            for day, value in zip(TUESDAYS, (20, 21, 22, 23, 24)):
                post(datetime(2026, 9, day, 9, tzinfo=timezone.utc), value)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def base_args(self):
        return ["baseline-eval", "--account", "my-bluesky", "--timezone", "UTC", "--source", "native-export",
                "--metric", "clicks", "--denominator", "impressions", "--since", "2026-09-01",
                "--until", "2026-09-30", "--slot", "exploit:Tue@09:00"]

    def test_cli_json_and_text_are_offline_read_only_and_deterministic(self):
        with NetworkGuard() as guard:
            code, out, err = self.cli(*self.base_args(), "--json")
            again = self.cli(*self.base_args(), "--json")[1]
            text_code, text, _ = self.cli(*self.base_args())
        self.assertEqual(guard.attempts, [])
        self.assertEqual((code, text_code), (0, 0), err)
        result = json.loads(out)
        self.assertEqual(out, again)
        self.assertEqual(result["kind"], "reachweaver.baseline-evaluation")
        self.assertEqual(result["correlational"][0]["estimate"], 0.011)
        self.assertIn("BASELINE calendar-plan generic", text)
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM posts WHERE status!='published'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM event_log WHERE action NOT IN "
                                         "('draft_created','metrics_recorded')").fetchone()[0], 0)

    def test_cli_requires_explicit_period_and_known_account(self):
        args = self.base_args()
        without_since = args[:args.index("--since")] + args[args.index("--since") + 2:]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--db", self.db, "--accounts", str(self.accounts), *without_since])
        self.assertEqual(raised.exception.code, 2)
        args[args.index("--account") + 1] = "unknown"
        code, _, err = self.cli(*args)
        self.assertEqual(code, 2)
        self.assertIn("Unknown account", err)
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        code, _, err = self.cli(*self.base_args(), "--readout", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("Invalid readout JSON", err)


if __name__ == "__main__":
    unittest.main()
