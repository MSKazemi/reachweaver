"""Offline tests for preregistered growth experiments (reachweaver/growth.py, issue #16)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.cli import main
from reachweaver.experiments import evaluate_experiment
from reachweaver.growth import (
    DIMENSIONS, LABELS, MIN_RECOMMENDED_PAIRS, canonical_json, check_plan, check_variant_set,
    classify_result, next_hypotheses, plan_hash, preregister, verify_preregistration,
    write_preregistration,
)
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, finish_send, store_metrics, transition
from test_no_network import NetworkGuard

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "growth-plan.example.json"


def load_plan() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def third_arm(plan: dict) -> dict:
    arm = deepcopy(plan["variants"][1])
    arm.update(id="treatment-question", hook="question-first",
               text="What should happen when a social post send times out halfway? ReachWeaver parks it in "
                    "needs_review and waits for a person to check the account. Review the design: "
                    "https://github.com/MSKazemi/reachweaver")
    return arm


def summary(rates, *, lift=None, guard=0.0, skipped=()):
    """Synthetic evaluate_experiment output with (control_rate, treatment_rate) pairs."""
    pairs = [{"pair": f"pair[{i}]", "control_rate": c, "treatment_rate": t} for i, (c, t) in enumerate(rates)]
    if lift is None and rates:
        c, t = sum(r[0] for r in rates), sum(r[1] for r in rates)
        lift = (t - c) / c if c else None
    return {"pair_results": pairs, "pairs_usable": len(pairs), "pairs_skipped": list(skipped),
            "relative_lift": lift, "guardrail_relative_change": guard, "verdict": "inconclusive"}


class VariantSetTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()

    def errors(self, variants=None, dimension=None):
        return check_variant_set(self.plan["variants"] if variants is None else variants,
                                 dimension or self.plan["dimension"])[0]

    def test_example_plan_is_valid(self):
        self.assertEqual(check_plan(self.plan), ([], []))
        self.assertEqual(self.errors(self.plan["variants"] + [third_arm(self.plan)]), [])

    def test_more_than_three_variants_rejected_as_mass_blasting(self):
        arms = self.plan["variants"] + [third_arm(self.plan)]
        fourth = deepcopy(arms[2])
        fourth.update(id="treatment-four", hook="number-first", text="Four arms is too many. " * 3)
        errors = self.errors(arms + [fourth])
        self.assertTrue(any("mass blasting" in e for e in errors), errors)

    def test_single_variant_rejected(self):
        self.assertTrue(any("at least 2 arms" in e for e in self.errors(self.plan["variants"][:1])))

    def test_undeclared_label_difference_rejected(self):
        self.plan["variants"][1]["framing"] = "founder-story"
        errors = self.errors()
        self.assertTrue(any("framing differs from control" in e for e in errors), errors)

    def test_different_cta_url_when_dimension_is_hook_rejected(self):
        arm = self.plan["variants"][1]
        arm["cta_url"] = "https://example.org/landing"
        arm["text"] = arm["text"].replace("https://github.com/MSKazemi/reachweaver", "https://example.org/landing")
        errors = self.errors()
        self.assertTrue(any("CTA (cta_url/cta_phrase) differs" in e for e in errors), errors)
        self.assertTrue(any("links in the text differ" in e for e in errors), errors)

    def test_link_drift_in_text_detected_even_with_matching_labels(self):
        self.plan["variants"][1]["text"] += " Docs: https://example.org/docs"
        self.assertTrue(any("links in the text differ" in e for e in self.errors()))

    def test_utm_query_differences_are_allowed(self):
        arm = self.plan["variants"][1]
        arm["text"] = arm["text"].replace("reachweaver", "reachweaver?utm_content=treatment-proof-first")
        self.assertEqual(self.errors(), [])

    def test_multi_dimension_declaration_rejected(self):
        self.plan["variants"][1]["differs_in"] = ["hook", "cta"]
        self.assertTrue(any("ONE dimension" in e for e in self.errors()))
        self.plan["variants"][1]["differs_in"] = "framing"
        self.assertTrue(any("does not match the plan dimension" in e for e in self.errors()))
        self.assertTrue(any("exactly one of" in e for e in check_variant_set(self.plan["variants"], "length")[0]))

    def test_near_duplicate_variants_rejected(self):
        arm = self.plan["variants"][1]
        arm["text"] = self.plan["variants"][0]["text"].replace("New post", "Fresh post")
        errors = self.errors()
        self.assertTrue(any("near-duplicate" in e for e in errors), errors)

    def test_tested_dimension_must_actually_differ(self):
        self.plan["variants"][1]["hook"] = "generic-announcement"
        self.assertTrue(any("nothing is tested" in e for e in self.errors()))

    def test_exactly_one_control(self):
        self.plan["variants"][1]["role"] = "control"
        self.assertTrue(any("exactly one arm" in e for e in self.errors()))


class PreregistrationTests(unittest.TestCase):
    def setUp(self):
        self.plan = load_plan()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def assert_blocked(self, pattern):
        with self.assertRaisesRegex(ValidationError, pattern):
            preregister(self.plan)

    def test_vanity_primary_metric_rejected_but_allowed_as_guardrail(self):
        self.assertTrue(any(g["signal"] == "likes" for g in self.plan["guardrail_metrics"]))
        for vanity in ("likes", "views", "impressions", "reposts"):
            with self.subTest(vanity=vanity):
                self.plan["primary_metric"] = {"signal": vanity, "denominator": "none"}
                self.assert_blocked("vanity metric; allowed only as a guardrail")

    def test_link_clicks_require_impressions_denominator(self):
        self.plan["primary_metric"] = {"signal": "link_clicks", "denominator": "views"}
        self.assert_blocked("denominator for link_clicks")
        self.plan["primary_metric"] = {"signal": "follower_magic"}
        self.assert_blocked("qualified signal")

    def test_attributed_signal_warns_it_is_not_a_snapshot_field(self):
        self.plan["primary_metric"] = {"signal": "repo_stars_utm", "denominator": "none"}
        record = preregister(self.plan)
        self.assertTrue(any("attribution-report" in w for w in record["warnings"]))

    def test_confounders_must_all_be_acknowledged(self):
        del self.plan["confounders_acknowledged"]["follower_count"]
        self.assert_blocked("follower_count")
        self.plan["confounders_acknowledged"] = ["account_baseline", "time"]
        self.assert_blocked("confounders_acknowledged must map")

    def test_hypothesis_window_cohort_and_stop_rules_validated(self):
        cases = (
            ("hypothesis", "This will go viral and is guaranteed to win", "overclaims"),
            ("hypothesis", "short", "falsifiable"),
            ("cohort", {"platform": "bluesky"}, "ONE platform"),
            ("observation_window", {"metric_age_hours": 168, "age_tolerance_hours": 24,
                                    "start": "2026-10-12", "end": "2026-10-01"}, "end after start"),
            ("stopping_rule", {"type": "fixed_pairs", "max_pairs": 8, "stop_when_winning": True},
             "early stopping on a favourable result"),
            ("stopping_rule", {"type": "fixed_pairs", "max_pairs": 3}, "max_pairs must be"),
            ("minimum_pairs", 1, "minimum_pairs must be"),
        )
        for field, value, pattern in cases:
            with self.subTest(field=field, pattern=pattern):
                plan = deepcopy(self.plan)
                plan[field] = value
                errors, _ = check_plan(plan)
                self.assertTrue(any(pattern in e for e in errors), errors)

    def test_secret_like_keys_and_unknown_fields_rejected(self):
        self.plan["api_token"] = "not-a-real-value"
        self.assert_blocked("secret-like")

    def test_small_minimum_pairs_warns_with_power_heuristic(self):
        self.plan["minimum_pairs"] = 3
        self.plan["stopping_rule"]["max_pairs"] = 4
        record = preregister(self.plan)
        self.assertTrue(any(f"{MIN_RECOMMENDED_PAIRS}-pair heuristic" in w for w in record["warnings"]))
        self.assertEqual(record["power_heuristic"]["best_case_sign_test_p"], 0.25)

    def test_record_shape_and_canonical_hash(self):
        now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        record = preregister(self.plan, now=now)
        self.assertEqual(record["registered_at"], "2026-10-09T12:00:00+00:00")
        self.assertRegex(record["plan_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(record["plan_hash"], plan_hash(self.plan))
        self.assertEqual(record["status"], "registered_not_approved")
        reordered = json.loads(json.dumps(self.plan, indent=7, sort_keys=False))
        reordered = dict(reversed(list(reordered.items())))
        self.assertEqual(plan_hash(reordered), record["plan_hash"])
        self.assertEqual(canonical_json(reordered), canonical_json(self.plan))

    def test_hash_detects_post_hoc_edits(self):
        registered = preregister(self.plan)["plan_hash"]
        self.assertTrue(verify_preregistration(self.plan, registered)["match"])
        self.assertTrue(verify_preregistration(self.plan, registered.removeprefix("sha256:").upper())["match"])
        for edit in (lambda p: p.update(minimum_pairs=7),
                     lambda p: p["primary_metric"].update(signal="replies"),
                     lambda p: p["observation_window"].update(end="2026-12-08"),
                     lambda p: p["variants"][1].update(text=p["variants"][1]["text"] + " ")):
            plan = deepcopy(self.plan)
            edit(plan)
            result = verify_preregistration(plan, registered)
            self.assertFalse(result["match"])
            self.assertEqual(result["verdict"], "edited_after_registration_or_wrong_hash")
        with self.assertRaises(ValidationError):
            verify_preregistration(self.plan, "not-a-hash")

    def test_verify_accepts_record_and_detects_tampered_record(self):
        record = preregister(self.plan)
        self.assertTrue(verify_preregistration(record, record["plan_hash"])["match"])
        record["plan"]["minimum_relative_lift"] = 0.05
        self.assertFalse(verify_preregistration(record, record["plan_hash"])["match"])

    def test_write_refuses_overwrite_and_is_owner_only(self):
        out = self.dir / "prereg.json"
        write_preregistration(preregister(self.plan), out)
        first = out.read_bytes()
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        with self.assertRaisesRegex(ValidationError, "Refusing to overwrite"):
            write_preregistration(preregister(self.plan), out)
        self.assertEqual(out.read_bytes(), first)
        link = self.dir / "link.json"
        link.symlink_to(self.dir / "missing-target.json")
        with self.assertRaisesRegex(ValidationError, "Refusing to overwrite"):
            write_preregistration(preregister(self.plan), link)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.dir / "state" / "never.sqlite"), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_cli_preregister_verify_offline_and_no_database(self):
        out = self.dir / "record.json"
        with NetworkGuard() as guard:
            code, stdout, stderr = self.cli("experiment-preregister", str(EXAMPLE), "--out", str(out))
            self.assertEqual(code, 0, stderr)
            registered = json.loads(stdout)["plan_hash"]
            self.assertIn("Not approval to publish", stderr)
            self.assertEqual(self.cli("experiment-verify", str(EXAMPLE), "--hash", registered)[0], 0)
            self.assertEqual(self.cli("experiment-verify", str(out), "--hash", registered)[0], 0)
            edited = self.dir / "edited.json"
            plan = load_plan()
            plan["minimum_pairs"] = 7
            edited.write_text(json.dumps(plan), encoding="utf-8")
            code, stdout, _ = self.cli("experiment-verify", str(edited), "--hash", registered)
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(stdout)["match"])
            code, _, stderr = self.cli("experiment-preregister", str(EXAMPLE), "--out", str(out))
            self.assertEqual(code, 2)
            self.assertIn("Refusing to overwrite", stderr)
            bad = self.dir / "vanity.json"
            plan = load_plan()
            plan["primary_metric"] = {"signal": "likes"}
            bad.write_text(json.dumps(plan), encoding="utf-8")
            code, _, stderr = self.cli("experiment-preregister", str(bad), "--out", str(self.dir / "x.json"))
            self.assertEqual(code, 2)
            self.assertIn("vanity", stderr)
            self.assertFalse((self.dir / "x.json").exists())
        self.assertEqual(guard.attempts, [])
        self.assertFalse((self.dir / "state").exists())


class ClassificationTests(unittest.TestCase):
    PLAN = {"minimum_pairs": 6, "minimum_relative_lift": 0.2, "maximum_guardrail_drop": 0.1}

    def test_small_sample_is_insufficient_data(self):
        result = classify_result(summary([(0.01, 0.02)] * 2), self.PLAN)
        self.assertEqual(result["label"], "insufficient_data")
        self.assertIn("below the preregistered minimum", result["explanations"][0])
        empty = classify_result({"pair_results": [], "pairs_skipped": [{"reason": "missing_comparable_snapshot"}]})
        self.assertEqual(empty["label"], "insufficient_data")
        self.assertIsNone(empty["direction"])

    def test_consistent_majority_is_directional_never_proven(self):
        rates = [(0.010, 0.015), (0.012, 0.016), (0.011, 0.015), (0.010, 0.014), (0.013, 0.016), (0.012, 0.0125)]
        result = classify_result(summary(rates), self.PLAN)
        self.assertEqual(result["label"], "directional")
        self.assertEqual(result["direction"], "positive")
        self.assertEqual(result["direction_counts"], {"treatment_higher": 6, "control_higher": 0, "tied": 0})
        self.assertNotIn("proven", result["allowed_labels"])
        self.assertIn("not a proven or causal effect", result["explanations"][-1])

    def test_sign_test_gate_keeps_five_of_six_inconclusive(self):
        # Calibrated by simulate_readout: a strict majority alone labelled ~30% of zero-lift runs directional.
        rates = [(0.010, 0.015), (0.012, 0.016), (0.011, 0.015), (0.010, 0.014), (0.013, 0.016), (0.012, 0.011)]
        result = classify_result(summary(rates), self.PLAN)
        self.assertEqual(result["label"], "inconclusive")
        self.assertEqual(result["sign_test_two_sided_p"], 0.21875)
        self.assertTrue(any("sign-test" in e for e in result["explanations"]), result["explanations"])
        ten_of_twelve = rates[:5] * 2 + [(0.012, 0.011), (0.011, 0.0105)]
        self.assertEqual(classify_result(summary(ten_of_twelve), self.PLAN)["label"], "directional")

    def test_label_space_never_contains_proven(self):
        self.assertEqual(LABELS, ("insufficient_data", "inconclusive", "directional"))
        cases = [[], [(0.01, 0.02)], [(0.01, 0.5)] * 6, [(0.01, 0.0)] * 8, [(0.01, 0.01)] * 6,
                 [(0.01, 0.02), (0.02, 0.01)] * 4, [(0.0, 0.0)] * 6]
        for rates in cases:
            for plan in (None, self.PLAN, {"minimum_pairs": 2}):
                label = classify_result(summary(rates), plan)["label"]
                self.assertIn(label, LABELS)
                self.assertNotIn("proven", label)

    def test_no_majority_is_inconclusive(self):
        # 3 of 7 pairs favour treatment, 2 favour control, 2 tie: no strict majority.
        rates = [(0.01, 0.03)] * 3 + [(0.02, 0.01)] * 2 + [(0.01, 0.01)] * 2
        result = classify_result(summary(rates), self.PLAN)
        self.assertEqual(result["label"], "inconclusive")
        self.assertTrue(any("strict majority" in e for e in result["explanations"]))

    def test_tiny_lift_and_guardrail_breach_are_inconclusive(self):
        rates = [(0.100, 0.101)] * 6
        self.assertEqual(classify_result(summary(rates), self.PLAN)["label"], "inconclusive")
        rates = [(0.01, 0.02)] * 6
        result = classify_result(summary(rates, guard=-0.4), self.PLAN)
        self.assertEqual(result["label"], "inconclusive")
        self.assertTrue(any("guardrail" in e for e in result["explanations"]))

    def test_below_power_heuristic_is_inconclusive_even_if_unanimous(self):
        result = classify_result(summary([(0.01, 0.02)] * 3), {"minimum_pairs": 3, "minimum_relative_lift": 0.2})
        self.assertEqual(result["label"], "inconclusive")
        self.assertEqual(result["sign_test_two_sided_p"], 0.25)

    def test_novelty_first_pair_dominance_flagged(self):
        rates = [(0.01, 0.20)] + [(0.010, 0.0105), (0.010, 0.0102), (0.010, 0.0101), (0.010, 0.0103),
                                  (0.010, 0.0104), (0.010, 0.0099)]
        result = classify_result(summary(rates), self.PLAN)
        self.assertTrue(result["flags"]["novelty_effect"])
        self.assertEqual(result["label"], "inconclusive")
        self.assertTrue(any("novelty" in e for e in result["explanations"]))

    def test_single_outlier_dominance_flagged(self):
        rates = [(0.010, 0.0105), (0.010, 0.0102), (0.010, 0.0101), (0.010, 0.20), (0.010, 0.0103),
                 (0.010, 0.0104), (0.010, 0.0099)]
        result = classify_result(summary(rates), self.PLAN)
        self.assertFalse(result["flags"]["novelty_effect"])
        self.assertTrue(result["flags"]["single_outlier"])
        self.assertEqual(result["label"], "inconclusive")

    def test_maps_real_evaluate_experiment_output(self):
        with tempfile.TemporaryDirectory() as tmp, connect(str(Path(tmp) / "t.sqlite")) as con:
            plan = {"schema_version": 1, "experiment_id": "hook-test", "hypothesis": "Proof-first hooks earn clicks",
                    "changed_variable": "hook only", "pre_registered_at":
                        (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                    "primary_metric": "clicks", "guardrail_metric": "saves", "denominator": "impressions",
                    "minimum_pairs": 3, "minimum_relative_lift": 0.2, "maximum_guardrail_drop": 0.1,
                    "metric_age_hours": 168, "age_tolerance_hours": 24, "pairs": []}
            for index in range(3):
                ids = []
                for arm, clicks in ((0, 10), (1, 18)):
                    post = add_post(con, "my-bluesky", "bluesky", f"Distinct observation {index} arm {arm}")
                    transition(con, post, "approve")
                    claim_sending(con, post)
                    finish_send(con, post, "remote-id", "https://example.com/post")
                    sent = con.execute("SELECT send_started_at FROM posts WHERE id=?", (post,)).fetchone()[0]
                    store_metrics(con, post, (datetime.fromisoformat(sent) + timedelta(hours=168)).isoformat(),
                                  "native-export", clicks=clicks, saves=20, impressions=1000)
                    ids.append(post)
                plan["pairs"].append({"control": ids[0], "treatment": ids[1]})
            readout = evaluate_experiment(con, plan)
        # evaluate_experiment shares classify_result's gate: 3 pairs is below the heuristic minimum.
        self.assertEqual(readout["verdict"], "no_consistent_win")
        result = classify_result(readout, plan)
        self.assertEqual(result["label"], "inconclusive")
        self.assertEqual(result["source_verdict"], "no_consistent_win")


class BacklogTests(unittest.TestCase):
    def test_empty_backlog_starts_with_first_dimension(self):
        result = next_hypotheses([])
        self.assertEqual(result["next"]["dimension"], DIMENSIONS[0])
        self.assertEqual(result["next"]["rule"], "next_untested")
        self.assertTrue(result["requires_human_approval"])
        self.assertIn("no learned recommender", result["method"])

    def test_negative_results_recorded_and_next_dimension_follows_fixed_order(self):
        results = [{"experiment_id": "hook-1", "dimension": "hook", "label": "inconclusive", "direction": "positive"},
                   {"experiment_id": "framing-1", "dimension": "framing", "label": "directional",
                    "direction": "negative"}]
        result = next_hypotheses(results)
        self.assertEqual([n["experiment_id"] for n in result["negative_results"]], ["hook-1", "framing-1"])
        self.assertEqual(result["next"]["dimension"], "format")
        self.assertEqual(result, next_hypotheses(deepcopy(results)))

    def test_pending_then_replication_rules(self):
        pending = [{"experiment_id": "hook-1", "dimension": "hook", "label": "insufficient_data"}]
        self.assertEqual(next_hypotheses(pending)["next"]["rule"], "finish_pending")
        self.assertEqual(next_hypotheses(pending)["negative_results"], [])
        win = [{"experiment_id": "hook-1", "dimension": "hook", "label": "directional", "direction": "positive"}]
        self.assertEqual(next_hypotheses(win)["next"], {**next_hypotheses(win)["next"], "rule": "replicate",
                                                        "dimension": "hook"})
        failed_replication = win + [{"experiment_id": "hook-2", "dimension": "hook", "label": "inconclusive"}]
        result = next_hypotheses(failed_replication)
        self.assertEqual(result["next"]["dimension"], "framing")
        self.assertEqual([n["experiment_id"] for n in result["negative_results"]], ["hook-2"])

    def test_all_dimensions_tested_picks_least_tested(self):
        results = [{"experiment_id": f"{d}-1", "dimension": d, "label": "inconclusive"} for d in DIMENSIONS]
        results.append({"experiment_id": "hook-2", "dimension": "hook", "label": "inconclusive"})
        self.assertEqual(next_hypotheses(results)["next"], {**next_hypotheses(results)["next"],
                                                            "rule": "least_tested", "dimension": "framing"})

    def test_malformed_results_rejected(self):
        for bad in ("nope", [{"experiment_id": "x", "dimension": "length", "label": "inconclusive"}],
                    [{"experiment_id": "x", "dimension": "hook", "label": "proven"}]):
            with self.assertRaises(ValidationError):
                next_hypotheses(bad)


if __name__ == "__main__":
    unittest.main()
