"""Adversarial/offline contract tests for source-bound creative and experiments."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachweaver.accounts import load_accounts
from reachweaver.campaigns import export_campaign, read_json, stage_campaign, validate_brand, validate_campaign
from reachweaver.cli import main
from reachweaver.experiments import evaluate_experiment
from reachweaver.policy import ValidationError
from reachweaver.storage import (add_post,claim_sending,connect,finish_send,get_post,store_metrics,transition)

ROOT = Path(__file__).resolve().parents[1]


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "state.sqlite")
        self.campaign = read_json(ROOT / "examples/campaign.reachweaver.json")
        self.brand = read_json(ROOT / "examples/brand.reachweaver.json")
        self.accounts_path = Path(self.tmp.name) / "accounts.json"
        self.accounts_path.write_text(json.dumps({"accounts": [
            {"id":"my-bluesky", "platform":"bluesky", "env_prefix":"REACHWEAVER_TEST_BSKY", "handle":"example.bsky.social"},
            {"id":"my-linkedin", "platform":"linkedin", "env_prefix":"REACHWEAVER_TEST_LI", "person_id":"782bbtaQ", "linkedin_version":"202609"},
        ]}),encoding="utf-8")
        self.accounts = load_accounts(self.accounts_path)

    def test_valid_reference_campaign_and_brand(self):
        self.assertEqual(validate_brand(self.brand),[])
        errors,warnings = validate_campaign(self.campaign,brand=self.brand,accounts=self.accounts)
        self.assertEqual(errors,[])
        self.assertIsInstance(warnings,list)

    def test_no_primary_source_fails(self):
        missing=deepcopy(self.campaign)
        missing["evidence"] = []
        errors,_=validate_campaign(missing,brand=self.brand)
        self.assertTrue(any("evidence" in e for e in errors))
        self.assertTrue(any("unknown evidence" in e for e in errors))

    def test_forged_or_unverified_source_blocked(self):
        invalid=deepcopy(self.campaign)
        invalid["evidence"][0]["url"]="http://localhost:8888/fake-proof"
        invalid["evidence"][0]["verification"]="unknown"
        errors,_=validate_campaign(invalid,brand=self.brand)
        self.assertTrue(any("public HTTPS" in e for e in errors))
        self.assertTrue(any("verification" in e for e in errors))

    def test_brand_account_allowlist_blocks_cross_brand(self):
        wrong=deepcopy(self.brand)
        wrong["approved_accounts"]=["my-bluesky"]
        errors,_=validate_campaign(self.campaign,brand=wrong,accounts=self.accounts)
        self.assertTrue(any("not approved for brand" in e for e in errors))
        wrong["brand_id"]="other-project"
        errors,_=validate_campaign(self.campaign,brand=wrong,accounts=self.accounts)
        self.assertTrue(any("does not match" in e for e in errors))

    def test_wrong_platform_account_rejected(self):
        self.campaign["variants"][0]["account_id"]="my-bluesky"
        errors,_=validate_campaign(self.campaign,brand=self.brand,accounts=self.accounts)
        self.assertTrue(any("not configured" in e for e in errors))

    def test_copy_paste_warning_not_fake_algorithm_rule(self):
        self.campaign["variants"][0]["text"] = "Short proof-led post. Verified: https://github.com/MSKazemi/reachweaver"
        self.campaign["variants"][1]["text"] = self.campaign["variants"][0]["text"]
        errors,warnings=validate_campaign(self.campaign,brand=self.brand)
        self.assertEqual(errors,[])
        self.assertTrue(any("copy-paste" in w for w in warnings))

    def test_secret_fields_and_banned_claims_are_rejected(self):
        self.campaign["api_token"]="not-a-real-value"
        self.campaign["variants"][0]["text"]="Guaranteed virality! " + self.campaign["variants"][0]["text"]
        errors,_=validate_campaign(self.campaign,brand=self.brand)
        self.assertTrue(any("secret-like" in e for e in errors))
        self.assertTrue(any("prohibited claim" in e for e in errors))

    def test_evidence_checked_at_required_not_future(self):
        self.campaign["evidence"][0].pop("checked_at")
        errors,_=validate_campaign(self.campaign,brand=self.brand)
        self.assertTrue(any("checked_at" in e for e in errors))
        self.campaign["evidence"][0]["checked_at"]=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
        errors,_=validate_campaign(self.campaign,brand=self.brand)
        self.assertTrue(any("future" in e for e in errors))

    def test_public_source_url_ip_is_rejected(self):
        self.campaign["evidence"][0]["url"]="https://127.0.0.1/trick"
        errors,_=validate_campaign(self.campaign,brand=self.brand)
        self.assertTrue(any("public HTTPS" in e for e in errors))

    def test_credentials_in_evidence_url_are_rejected(self):
        self.campaign["evidence"][0]["url"]="https://example.org/paper?access_token=private"
        errors,_=validate_campaign(self.campaign,brand=self.brand)
        self.assertTrue(any("public HTTPS" in e for e in errors))

    def test_structural_check_is_not_claim_verification(self):
        self.campaign["evidence"][0]["claim"]="Unsupported claim not actually on the linked page"
        self.assertEqual(validate_campaign(self.campaign,brand=self.brand)[0],[])
        # Structural lint deliberately does not pretend to inspect external website semantics.

    def test_idempotent_stage_as_drafts_only(self):
        with connect(self.db) as db:
            first=stage_campaign(db,self.campaign,self.brand,self.accounts)
            self.assertEqual(len(first),2)
            self.assertTrue(all(x["created"] for x in first))
            second=stage_campaign(db,self.campaign,self.brand,self.accounts)
            self.assertFalse(any(x["created"] for x in second))
            self.assertEqual([x["post_id"] for x in first],[x["post_id"] for x in second])
            self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0],2)
            self.assertEqual({get_post(db,x["post_id"])["status"] for x in first},{"draft"})
            self.assertEqual(db.execute("SELECT count(*) FROM campaign_variants").fetchone()[0],2)

    def test_changed_campaign_after_stage_fails_without_partial_writes(self):
        with connect(self.db) as db:
            stage_campaign(db,self.campaign,self.brand,self.accounts)
            self.campaign["variants"].insert(0,{
                "id":"new-prepended", "platform":"bluesky","account_id":"my-bluesky","format":"text",
                "angle":"new angle","cta":"Read more","text":"Fresh example", "evidence_ids":["source-repo"]})
            with self.assertRaisesRegex(ValidationError,"changed after staging"):
                stage_campaign(db,self.campaign,self.brand,self.accounts)
            self.assertEqual(db.execute("SELECT count(*) FROM posts").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM campaign_variants").fetchone()[0],2)

    def test_export_is_offline_and_refuses_overwrite(self):
        out=Path(self.tmp.name)/"new-output"
        out.mkdir()
        files=export_campaign(self.campaign,self.brand,out)
        self.assertEqual(len(files),4)
        self.assertIn("DRAFTS ONLY",(out/"MANUAL-REVIEW.txt").read_text())
        self.assertEqual((out/"drafts/bluesky-developer-bluesky.txt").read_text().strip(),self.campaign["variants"][1]["text"])
        with self.assertRaises(ValidationError):
            export_campaign(self.campaign,self.brand,out)

    def test_cli_check_and_stage(self):
        manifest=Path(self.tmp.name)/"manifest.json"
        brand=Path(self.tmp.name)/"brand.json"
        manifest.write_text(json.dumps(self.campaign),encoding="utf-8")
        brand.write_text(json.dumps(self.brand),encoding="utf-8")
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["campaign-check","--file",str(manifest),"--brand",str(brand),"--json"]),0)
        self.assertTrue(json.loads(output.getvalue())["valid"])
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--db",self.db,"--accounts",str(self.accounts_path),"campaign-stage","--file",str(manifest),"--brand",str(brand)]),0)
        self.assertEqual(json.loads(output.getvalue())["status"],"draft_only")


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"test.sqlite")
        self.pre_registered=datetime.now(timezone.utc)-timedelta(days=1)
        self.plan={
            "schema_version":1,"experiment_id":"hook-test","hypothesis":"More proof leads to qualified clicks",
            "changed_variable":"hook text only","pre_registered_at":self.pre_registered.isoformat(),
            "primary_metric":"clicks","guardrail_metric":"saves","denominator":"impressions",
            "minimum_pairs":3,"minimum_relative_lift":0.2,"maximum_guardrail_drop":0.1,
            "metric_age_hours":168,"age_tolerance_hours":24,"pairs":[]}

    def add_pair(self, con, *, control_clicks=10,treatment_clicks=18, control_saves=20,treatment_saves=21,
                 exposure=1000, with_metric=True, mismatch_account=False, other_platform=False):
        entries=[]
        for idx in range(2):
            account="my-bluesky" if idx == 0 or not mismatch_account else "my-other-account"
            platform="bluesky" if idx == 0 or not other_platform else "linkedin"
            post=add_post(con,account,platform,f"A good original observation {idx} {len(self.plan['pairs'])}")
            transition(con,post,"approve")
            claim_sending(con,post)
            finish_send(con,post,"remote-id", "https://example.com/post")
            created=con.execute("SELECT send_started_at FROM posts WHERE id=?",(post,)).fetchone()[0]
            snapshot_at=(datetime.fromisoformat(created)+timedelta(hours=168)).isoformat()
            if with_metric:
                store_metrics(con,post,snapshot_at,"native-export",clicks=(control_clicks,treatment_clicks)[idx],
                              saves=(control_saves,treatment_saves)[idx],impressions=exposure)
            entries.append(post)
        self.plan["pairs"].append({"control":entries[0],"treatment":entries[1]})

    def test_success_requires_repeated_comparable_pairs(self):
        with connect(self.db) as con:
            for _ in range(6):self.add_pair(con)
            result=evaluate_experiment(con,self.plan)
            self.assertEqual(result["verdict"],"directional_positive")
            self.assertEqual(result["pairs_usable"],6)
            self.assertAlmostEqual(result["relative_lift"],0.8)
            self.assertIn("observational",result["warnings"][0])

    def test_one_pair_not_called_win(self):
        with connect(self.db) as con:
            self.add_pair(con)
            result=evaluate_experiment(con,self.plan)
            self.assertEqual(result["verdict"],"inconclusive")
            self.assertEqual(result["reason"],"insufficient_pairs_or_zero_baseline")

    def test_missing_metrics_are_not_zeroes(self):
        with connect(self.db) as con:
            self.add_pair(con,with_metric=False)
            result=evaluate_experiment(con,self.plan)
            self.assertEqual(result["pairs_usable"],0)
            self.assertEqual(result["pairs_skipped"][0]["reason"],"missing_comparable_snapshot")
            self.assertIsNone(result["relative_lift"])

    def test_wrong_account_skips_pair(self):
        with connect(self.db) as con:
            self.add_pair(con,mismatch_account=True)
            self.assertEqual(evaluate_experiment(con,self.plan)["pairs_skipped"][0]["reason"],"platform_or_account_mismatch")

    def test_plan_after_publish_rejected_in_readout(self):
        with connect(self.db) as con:
            self.add_pair(con)
            self.plan["pre_registered_at"]=(datetime.now(timezone.utc)+timedelta(hours=5)).isoformat()
            self.assertEqual(evaluate_experiment(con,self.plan)["pairs_skipped"][0]["reason"],"first_publication_predates_declared_plan")

    def test_guardrail_degradation_blocks_winner(self):
        with connect(self.db) as con:
            for _ in range(3): self.add_pair(con,treatment_saves=5)
            result=evaluate_experiment(con,self.plan)
            self.assertEqual(result["verdict"],"no_consistent_win")
            self.assertLess(result["guardrail_relative_change"],-0.5)

    def test_zero_impression_exposure_never_counts_as_valid_pair(self):
        with connect(self.db) as con:
            self.add_pair(con,exposure=0)
            self.assertEqual(evaluate_experiment(con,self.plan)["pairs_usable"],0)

    def test_sequential_account_cohorts_are_not_pooled(self):
        with connect(self.db) as con:
            self.add_pair(con)
            self.add_pair(con)
            pair=self.plan["pairs"][-1]
            for pid in (pair["control"],pair["treatment"]):
                con.execute("UPDATE posts SET account_id=? WHERE id=?",("other-account",pid))
            result=evaluate_experiment(con,self.plan)
            self.assertEqual(result["pairs_usable"],1)
            self.assertEqual(result["pairs_skipped"][0]["reason"],"different_platform_or_account_cohort")

    def test_tampered_plan_cannot_reuse_post(self):
        with connect(self.db) as con:
            self.add_pair(con)
            self.plan["pairs"].append(self.plan["pairs"][0])
            with self.assertRaises(ValidationError):evaluate_experiment(con,self.plan)

    def test_mismatched_platform_is_rejected_without_aggregating(self):
        with connect(self.db) as con:
            self.add_pair(con,other_platform=True)
            self.assertEqual(evaluate_experiment(con,self.plan)["pairs_usable"],0)


if __name__=="__main__":
    unittest.main()
