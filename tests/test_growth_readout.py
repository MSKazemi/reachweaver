"""Offline tests for the growth readout, design labels and seeded simulation (issue #16)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.experiments import evaluate_experiment
from reachweaver.growth import ASSIGNMENTS, LABELS, check_plan, preregister
from reachweaver.growth_readout import (
    DISCLAIMER, POST_MAP_KIND, describe_design, growth_plan_to_experiment, run_readout, simulate_readout,
    summarize_pairs, synthetic_pairs,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, finish_send, store_metrics, transition
from test_no_network import NetworkGuard

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "growth-plan.example.json"
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
CONTROL, TREATMENT = "control-generic", "treatment-proof-first"


def growth_plan(**changes) -> dict:
    plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    plan["observation_window"].update(start="2026-09-01", end="2026-11-30")
    plan.update(changes)
    return plan


def record_for(plan: dict) -> dict:
    return preregister(plan, now=T0)


def publish(con, *, sent: datetime, clicks=None, saves=20, impressions=1000,
            account="my-bluesky", platform="bluesky", text="Distinct observation") -> str:
    post = add_post(con, account, platform, f"{text} {sent.isoformat()} {clicks}")
    transition(con, post, "approve")
    claim_sending(con, post, sent.isoformat())
    finish_send(con, post, "remote-id", "https://example.com/post")
    if clicks is not None:
        store_metrics(con, post, (sent + timedelta(hours=168)).isoformat(), "native-export",
                      clicks=clicks, saves=saves, impressions=impressions)
    return post


def seed_pairs(con, clicks, *, alternate=False) -> list[dict]:
    """Publish one pair per (control_clicks, treatment_clicks) a week apart; control first unless alternating."""
    pairs = []
    for index, (c_clicks, t_clicks) in enumerate(clicks):
        week = T0 + timedelta(days=7 * index + 1, hours=10)
        c_first = not alternate or index % 2 == 0
        c_at, t_at = (week, week + timedelta(days=1)) if c_first else (week + timedelta(days=1), week)
        pairs.append({CONTROL: publish(con, sent=c_at, clicks=c_clicks),
                      TREATMENT: publish(con, sent=t_at, clicks=t_clicks)})
    return pairs


def post_map(pairs, **changes) -> dict:
    data = {"kind": POST_MAP_KIND, "schema_version": 1, "experiment_id": "hook-proof-vs-generic-2026-10",
            "pairs": pairs}
    data.update(changes)
    return data


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db_path = self.dir / "state" / "t.sqlite"
        self.ctx = connect(str(self.db_path))
        self.con = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)


class ConversionTests(DbCase):
    def test_copies_preregistered_fields_exactly_and_carries_hash(self):
        plan = growth_plan()
        record = record_for(plan)
        pairs = seed_pairs(self.con, [(10, 15)] * 2)
        out = growth_plan_to_experiment(record, post_map(pairs), self.con)
        window = plan["observation_window"]
        self.assertEqual((out["primary_metric"], out["denominator"]), ("clicks", plan["primary_metric"]["denominator"]))
        self.assertEqual(out["guardrail_metric"], "saves")
        self.assertEqual((out["metric_age_hours"], out["age_tolerance_hours"]),
                         (window["metric_age_hours"], window["age_tolerance_hours"]))
        for field in ("minimum_pairs", "minimum_relative_lift", "maximum_guardrail_drop", "experiment_id", "hypothesis"):
            self.assertEqual(out[field], plan[field], field)
        self.assertEqual(out["plan_hash"], record["plan_hash"])
        self.assertEqual(out["pre_registered_at"], record["registered_at"])
        self.assertEqual(out["pairs"], [{"control": p[CONTROL], "treatment": p[TREATMENT]} for p in pairs])
        self.assertEqual([g["signal"] for g in out["conversion"]["guardrails_not_evaluated"]], ["likes"])
        self.assertEqual(out["assignment"], "unrandomized")
        # The converted plan is accepted by the existing evaluator unchanged.
        self.assertEqual(evaluate_experiment(self.con, out)["pairs_usable"], 2)

    def test_refuses_unverified_hash_raw_plan_and_wrong_external_hash(self):
        record = record_for(growth_plan())
        pairs = post_map(seed_pairs(self.con, [(10, 15)]))
        tampered = deepcopy(record)
        tampered["plan"]["minimum_relative_lift"] = 0.01
        with self.assertRaisesRegex(ValidationError, "does not verify"):
            growth_plan_to_experiment(tampered, pairs, self.con)
        with self.assertRaisesRegex(ValidationError, "record written by experiment-preregister"):
            growth_plan_to_experiment(record["plan"], pairs, self.con)
        with self.assertRaisesRegex(ValidationError, "does not verify"):
            growth_plan_to_experiment(record, pairs, self.con, expected_hash="sha256:" + "0" * 64)
        self.assertEqual(growth_plan_to_experiment(record, pairs, self.con,
                                                   expected_hash=record["plan_hash"])["plan_hash"], record["plan_hash"])

    def test_refuses_unpublished_wrong_account_or_platform_posts(self):
        record = record_for(growth_plan())
        good = seed_pairs(self.con, [(10, 15)])[0]
        draft = add_post(self.con, "my-bluesky", "bluesky", "Still a draft")
        other_account = publish(self.con, sent=T0 + timedelta(days=3), clicks=5, account="other-bluesky")
        other_platform = publish(self.con, sent=T0 + timedelta(days=3), clicks=5, platform="mastodon")
        cases = ((draft, "not published"), (other_account, "not the preregistered cohort"),
                 (other_platform, "not the preregistered cohort"), ("no-such-post", "Unknown post id"))
        for post_id, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValidationError, pattern):
                    growth_plan_to_experiment(record, post_map([{**good, TREATMENT: post_id}]), self.con)

    def test_refuses_malformed_maps(self):
        record = record_for(growth_plan())
        good = seed_pairs(self.con, [(10, 15)])[0]
        cases = (
            (post_map([good], experiment_id="other"), "experiment_id does not match"),
            (post_map([good], kind="something-else"), POST_MAP_KIND),
            (post_map([{**good, "mystery-arm": "x"}]), "only this plan's arm ids"),
            (post_map([good, dict(good)]), "reused"),
            (post_map([{CONTROL: good[CONTROL]}]), "must be a published post id"),
            (post_map([]), "nonempty list"),
            (post_map([good], treatment_arm=CONTROL), "treatment_arm"),
            (post_map([good], extra=True), "Unknown post map fields"),
            (post_map([good] * 9), "max_pairs"),
        )
        for mapping, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValidationError, pattern):
                    growth_plan_to_experiment(record, mapping, self.con)

    def test_three_arm_plan_needs_an_explicit_treatment_arm(self):
        plan = growth_plan()
        third = deepcopy(plan["variants"][1])
        third.update(id="treatment-question", hook="question-first",
                     text="What should happen when a social post send times out halfway? ReachWeaver parks it in "
                          "needs_review and waits for a person to check the account. Review the design: "
                          "https://github.com/MSKazemi/reachweaver")
        plan["variants"].append(third)
        record = record_for(plan)
        pair = seed_pairs(self.con, [(10, 15)])[0]
        pair["treatment-question"] = publish(self.con, sent=T0 + timedelta(days=4), clicks=12)
        with self.assertRaisesRegex(ValidationError, "treatment_arm"):
            growth_plan_to_experiment(record, post_map([pair]), self.con)
        out = growth_plan_to_experiment(record, post_map([pair], treatment_arm="treatment-question"), self.con)
        self.assertEqual(out["pairs"], [{"control": pair[CONTROL], "treatment": pair["treatment-question"]}])
        self.assertEqual(out["treatment_arm"], "treatment-question")
        with self.assertRaisesRegex(ValidationError, "must be a published post id"):
            growth_plan_to_experiment(record, post_map([{**pair, TREATMENT: ["not", "an", "id"]}],
                                                       treatment_arm="treatment-question"), self.con)

    def test_attribution_only_primary_is_refused(self):
        plan = growth_plan(primary_metric={"signal": "repo_stars_utm", "denominator": "none"})
        pairs = post_map(seed_pairs(self.con, [(10, 15)]))
        with self.assertRaisesRegex(ValidationError, "attribution-report"):
            growth_plan_to_experiment(record_for(plan), pairs, self.con)

    def test_database_is_only_read(self):
        record = record_for(growth_plan())
        pairs = post_map(seed_pairs(self.con, [(10, 15)] * 6))
        before = self.con.total_changes
        events = self.con.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
        run_readout(self.con, record, pairs)
        self.assertEqual(self.con.total_changes, before)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM event_log").fetchone()[0], events)

    def test_send_order_warnings(self):
        record = record_for(growth_plan())
        out = growth_plan_to_experiment(record, post_map(seed_pairs(self.con, [(10, 15)] * 3)), self.con)
        self.assertEqual(out["conversion"]["arm_order"], {"control_first": 3, "treatment_first": 0, "same_time": 0})
        self.assertTrue(any("always sent first" in w for w in out["conversion"]["warnings"]))
        alternating = record_for(growth_plan(assignment="alternating"))
        balanced = growth_plan_to_experiment(alternating, post_map(seed_pairs(self.con, [(10, 15)] * 4,
                                                                              alternate=True)), self.con)
        self.assertEqual(balanced["conversion"]["warnings"], [])
        unbalanced = growth_plan_to_experiment(alternating, post_map(seed_pairs(self.con, [(10, 15)] * 4)), self.con)
        self.assertTrue(any("alternating" in w for w in unbalanced["conversion"]["warnings"]))


class DesignTests(DbCase):
    def test_only_randomized_is_more_than_associational(self):
        for assignment in ("unrandomized", "alternating", None, "coin-flip-ish", 7):
            with self.subTest(assignment=assignment):
                design = describe_design(assignment)
                self.assertEqual(design["interpretation"], "associational")
                self.assertFalse(design["verified"])
                self.assertIn("not a randomized result", design["statement"])
        randomized = describe_design("randomized")
        self.assertEqual(randomized["interpretation"], "randomized_assignment_owner_declared")
        self.assertIn("cannot verify", randomized["statement"])
        self.assertIn("not a causal or proven effect", randomized["statement"])

    def test_plan_validates_assignment_and_hash_covers_it(self):
        self.assertEqual(check_plan(growth_plan())[0], [])
        for value in ASSIGNMENTS:
            self.assertEqual(check_plan(growth_plan(assignment=value))[0], [], value)
        self.assertTrue(any("assignment must be" in e for e in check_plan(growth_plan(assignment="random"))[0]))
        self.assertNotEqual(record_for(growth_plan(assignment="randomized"))["plan_hash"],
                            record_for(growth_plan(assignment="unrandomized"))["plan_hash"])
        self.assertIn("not verified",
                      record_for(growth_plan(assignment="randomized"))["power_heuristic"]["rule"])

    def test_readout_never_describes_unrandomized_as_more_than_associational(self):
        clicks = [(10, 16), (12, 17), (11, 16), (10, 15), (13, 17), (12, 14)]
        for assignment, interpretation in (("unrandomized", "associational"), ("alternating", "associational"),
                                           ("randomized", "randomized_assignment_owner_declared")):
            with self.subTest(assignment=assignment):
                record = record_for(growth_plan(assignment=assignment))
                result = run_readout(self.con, record, post_map(seed_pairs(self.con, clicks)))
                self.assertEqual(result["label"], "directional")
                self.assertEqual(result["design"]["assignment"], assignment)
                self.assertEqual(result["design"]["interpretation"], interpretation)
                associational = any("association only" in e for e in result["explanations"])
                self.assertEqual(associational, assignment != "randomized")
                self.assertEqual(result["disclaimer"], DISCLAIMER)


class ReadoutTests(DbCase):
    def test_end_to_end_labels_flags_and_missing_data(self):
        record = record_for(growth_plan())
        pairs = seed_pairs(self.con, [(10, 16), (12, 17), (11, 16), (10, 15), (13, 17), (12, 14)])
        result = run_readout(self.con, record, post_map(pairs))
        self.assertEqual((result["label"], result["direction"]), ("directional", "positive"))
        self.assertEqual(result["flags"], {"novelty_effect": False, "single_outlier": False})
        self.assertEqual(result["plan_hash"], record["plan_hash"])
        self.assertEqual((result["experiment_id"], result["dimension"]), (record["experiment_id"], "hook"))
        self.assertNotIn("proven", json.dumps(result["classification"]["allowed_labels"]))
        # One pair without snapshots: missing data is excluded, not zero, and drops below the minimum.
        pairs[0] = {CONTROL: publish(self.con, sent=T0 + timedelta(days=60)),
                    TREATMENT: publish(self.con, sent=T0 + timedelta(days=61))}
        missing = run_readout(self.con, record, post_map(pairs))
        self.assertEqual(missing["label"], "insufficient_data")
        self.assertEqual(missing["pairs_skipped"], [{"pair": "pair[0]", "reason": "missing_comparable_snapshot"}])

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_cli_readout_text_and_json_offline(self):
        record = record_for(growth_plan())
        prereg, mapping = self.dir / "prereg.json", self.dir / "posts.json"
        prereg.write_text(json.dumps(record), encoding="utf-8")
        mapping.write_text(json.dumps(post_map(seed_pairs(self.con, [(10, 15)] * 3))), encoding="utf-8")
        with NetworkGuard() as guard:
            code, stdout, stderr = self.cli("experiment-readout", "--prereg", str(prereg), "--posts", str(mapping))
            self.assertEqual(code, 0, stderr)
            self.assertIn("LABEL: insufficient_data", stdout)
            self.assertIn("DESIGN: unrandomized -> associational", stdout)
            self.assertIn("FLAGS: novelty_effect=no single_outlier=no", stdout)
            self.assertIn(f"DISCLAIMER: {DISCLAIMER}", stdout)
            code, stdout, _ = self.cli("experiment-readout", "--prereg", str(prereg), "--posts", str(mapping), "--json")
            self.assertEqual(code, 0)
            data = json.loads(stdout)
            self.assertEqual(data["design"]["interpretation"], "associational")
            self.assertEqual(data["experiment_plan"]["plan_hash"], record["plan_hash"])
            record["plan"]["minimum_pairs"] = 2
            prereg.write_text(json.dumps(record), encoding="utf-8")
            code, _, stderr = self.cli("experiment-readout", "--prereg", str(prereg), "--posts", str(mapping))
            self.assertEqual(code, 2)
            self.assertIn("does not verify", stderr)
        self.assertEqual(guard.attempts, [])


class NextAndSimulateCliTests(unittest.TestCase):
    def cli(self, tmp: Path, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(tmp / "state" / "never.sqlite"), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_experiment_next_reads_a_list_and_never_opens_the_database(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            results = tmp / "results.json"
            results.write_text(json.dumps([{"experiment_id": "hook-1", "dimension": "hook", "label": "inconclusive",
                                            "direction": "positive"}]), encoding="utf-8")
            code, stdout, _ = self.cli(tmp, "experiment-next", "--results", str(results), "--json")
            self.assertEqual(code, 0)
            data = json.loads(stdout)
            self.assertEqual(data["next"]["dimension"], "framing")
            self.assertEqual([n["experiment_id"] for n in data["negative_results"]], ["hook-1"])
            code, stdout, _ = self.cli(tmp, "experiment-next", "--results", str(results))
            self.assertIn("NEXT: framing (next_untested)", stdout)
            results.write_text('{"not": "a list"}', encoding="utf-8")
            self.assertEqual(self.cli(tmp, "experiment-next", "--results", str(results))[0], 2)
            results.write_text("[nope", encoding="utf-8")
            self.assertEqual(self.cli(tmp, "experiment-next", "--results", str(results))[0], 2)
            self.assertFalse((tmp / "state").exists())

    def test_experiment_simulate_cli_reports_counts_and_never_opens_the_database(self):
        with tempfile.TemporaryDirectory() as name, NetworkGuard() as guard:
            tmp = Path(name)
            argv = ("experiment-simulate", "--seed", "7", "--pairs", "6", "--lift", "0", "--noise", "0.3",
                    "--trials", "200")
            code, stdout, _ = self.cli(tmp, *argv, "--json")
            self.assertEqual(code, 0)
            data = json.loads(stdout)
            self.assertTrue(data["simulated"])
            self.assertEqual(sum(data["label_counts"].values()), 200)
            self.assertEqual(json.loads(self.cli(tmp, *argv, "--json")[1]), data)
            code, stdout, _ = self.cli(tmp, *argv)
            self.assertTrue(stdout.startswith("SIMULATED seed=7"))
            self.assertIn("directional", stdout)
            self.assertEqual(self.cli(tmp, *argv[:-2], "--trials", "0")[0], 2)
            self.assertFalse((tmp / "state").exists())
        self.assertEqual(guard.attempts, [])


class SimulationTests(unittest.TestCase):
    def test_fixed_seed_is_deterministic(self):
        first = simulate_readout(16, 6, 0.2, 0.3, trials=300)
        self.assertEqual(first, simulate_readout(16, 6, 0.2, 0.3, trials=300))
        self.assertNotEqual(first["label_counts"], simulate_readout(17, 6, 0.2, 0.3, trials=300)["label_counts"])

    def test_zero_true_lift_rarely_yields_directional(self):
        for pairs in (6, 8, 12):
            with self.subTest(pairs=pairs):
                result = simulate_readout(16, pairs, 0.0, 0.3, trials=500)
                self.assertLess(result["label_rates"]["directional"], 0.06, result["label_counts"])
                self.assertLessEqual(result["label_rate_95ci"]["directional"][0], 0.06)

    def test_below_six_pairs_is_never_directional(self):
        for pairs in (2, 3, 5):
            result = simulate_readout(16, pairs, 1.0, 0.1, trials=200)
            self.assertEqual(result["label_counts"]["directional"], 0, pairs)

    def test_large_true_lift_with_many_pairs_is_mostly_directional_positive(self):
        result = simulate_readout(16, 12, 1.0, 0.1, trials=300, impressions=10000)
        self.assertGreater(result["label_rates"]["directional"], 0.8, result["label_counts"])
        self.assertEqual(result["directional_by_direction"]["negative"], 0)

    def test_distribution_is_reported_with_counts_and_uncertainty(self):
        result = simulate_readout(3, 8, 0.2, 0.3, trials=250)
        self.assertEqual(set(result["label_counts"]), set(LABELS))
        self.assertEqual(sum(result["label_counts"].values()), 250)
        for label in LABELS:
            low, high = result["label_rate_95ci"][label]
            self.assertLessEqual(low, result["label_rates"][label])
            self.assertGreaterEqual(high, result["label_rates"][label])
        self.assertEqual(result["estimated_lift"]["n"], 250)
        self.assertLess(result["estimated_lift"]["p05"], result["estimated_lift"]["p95"])
        self.assertIn("SIMULATED", result["note"])
        self.assertEqual(result["kind"], "reachweaver.simulated-readout")

    def test_invalid_parameters_rejected(self):
        for args, kwargs in (((1, 1, 0.0, 0.3), {}), ((1, 6, -1.0, 0.3), {}), ((1, 6, 0.0, -0.1), {}),
                             ((1, 6, 0.0, 0.3), {"trials": 0}), ((1.5, 6, 0.0, 0.3), {}),
                             ((1, 6, 0.0, 0.3), {"minimum_pairs": 1})):
            with self.assertRaises(ValidationError):
                simulate_readout(*args, **kwargs)

    def test_in_memory_summary_matches_evaluate_experiment(self):
        import random
        pairs = synthetic_pairs(random.Random(5), 6, 0.3, 0.3)
        with tempfile.TemporaryDirectory() as tmp, connect(str(Path(tmp) / "s" / "t.sqlite")) as con:
            plan = {"schema_version": 1, "experiment_id": "sim-check", "hypothesis": "Synthetic consistency check",
                    "changed_variable": "hook", "pre_registered_at": T0.isoformat(), "primary_metric": "clicks",
                    "guardrail_metric": "saves", "denominator": "impressions", "minimum_pairs": 6,
                    "minimum_relative_lift": 0.1, "maximum_guardrail_drop": 0.1, "metric_age_hours": 168,
                    "age_tolerance_hours": 24, "pairs": []}
            for index, pair in enumerate(pairs):
                ids = {}
                for offset, arm in enumerate(("control", "treatment")):
                    snap = pair[arm]
                    sent = T0 + timedelta(days=2 * index + offset + 1)
                    ids[arm] = publish(con, sent=sent, clicks=snap["clicks"], saves=snap["saves"],
                                       impressions=snap["impressions"], text=f"Synthetic {index} {arm}")
                plan["pairs"].append(ids)
            real = evaluate_experiment(con, plan)
        synthetic = summarize_pairs(pairs, 6)
        self.assertEqual(synthetic["relative_lift"], real["relative_lift"])
        self.assertEqual(synthetic["guardrail_relative_change"], real["guardrail_relative_change"])
        self.assertEqual([(p["control_rate"], p["treatment_rate"]) for p in synthetic["pair_results"]],
                         [(p["control_rate"], p["treatment_rate"]) for p in real["pair_results"]])


if __name__ == "__main__":
    unittest.main()
