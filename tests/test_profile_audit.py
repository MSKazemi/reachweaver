"""25-network profile audit and matched-age post review: local, safe, descriptive."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import (
    audit_posts, audit_profiles, load_registry, load_snapshot, render_summary,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/profile-post-audit.example.json"


class PlatformAuditTests(unittest.TestCase):
    def setUp(self):
        self.data = load_snapshot(EXAMPLE)

    def write(self, obj: dict, dest: Path) -> None:
        dest.write_text(json.dumps(obj), encoding="utf-8")

    def test_registry_has_individual_playbooks_for_priority_channels(self):
        rows = load_registry()["platforms"]
        by_name = {p["id"]: p for p in rows}
        self.assertGreaterEqual(len(rows), 23)
        for platform in ("linkedin", "devto", "youtube", "x", "bluesky", "mastodon",
                         "instagram", "facebook", "threads", "tiktok", "reddit",
                         "hackernews", "telegram", "discord", "hashnode", "pinterest",
                         "producthunt", "indiehackers", "peerlist", "bilibili", "zhihu", "oschina"):
            self.assertIn(platform, by_name)
            self.assertGreaterEqual(len(by_name[platform]["profile_checks"]), 4)
            self.assertTrue(by_name[platform]["post_strategy"]["test_hypothesis"])
            self.assertTrue(by_name[platform]["evidence"]["tier"])
        self.assertIn("headline", by_name["linkedin"]["profile_checks"])
        self.assertIn("playlists", by_name["youtube"]["profile_checks"])
        self.assertIn("claimed_domain", by_name["pinterest"]["profile_checks"])
        self.assertIn("profile_metadata", by_name["mastodon"]["profile_checks"])
        self.assertTrue(by_name["hackernews"]["human_only"])
        self.assertTrue(by_name["reddit"]["human_only"])
        self.assertIsNone(by_name["zhihu"]["evidence"]["official_source"])

    def test_profile_is_scored_by_observed_fields_not_missing_assumptions(self):
        r = audit_profiles(self.data, platform="linkedin")
        self.assertEqual(r["kind"], "profile_audit")
        self.assertEqual(r["status"], "offline_descriptive_not_a_platform_rank")
        item = r["reports"][0]
        self.assertEqual(item["account_id"], "sample-linkedin")
        self.assertEqual(item["status"], "observed_only")
        self.assertLess(item["readiness_heuristic_score"], 100)
        self.assertEqual(next(x for x in item["checklist"] if x["field"]=="banner")["status"], "unknown")
        self.assertIn("featured", [x["field"] for x in item["fixes"]])
        self.assertTrue(item["coverage"] > 0.6)

    def test_no_data_means_no_fake_profile_score(self):
        sample = self.data["profiles"][0]
        sample["fields"] = {"bio": None}
        item = audit_profiles(self.data, platform="linkedin")["reports"][0]
        self.assertIsNone(item["readiness_heuristic_score"])
        self.assertEqual(item["status"], "insufficient_profile_data")
        self.assertEqual(item["observed_checks"],0)
        self.assertEqual(item["fixes"],[])

    def test_real_missing_profile_link_is_fix_and_tracking_is_not_clean(self):
        p = self.data["profiles"][0]
        p["fields"]["website"] = "https://example.org/?utm_source=linkedin"
        item = audit_profiles(self.data, platform="linkedin")["reports"][0]
        self.assertIn("website", [f["field"] for f in item["fixes"]])
        p["fields"]["website"] = "http://example.org"
        item = audit_profiles(self.data, platform="linkedin")["reports"][0]
        self.assertIn("website", [f["field"] for f in item["fixes"]])

    def test_only_sourced_bio_limit_flags_and_unverified_limits_not_invented(self):
        self.data["profiles"].append({
            "platform": "instagram", "account_id": "sample-ig", "handle": "@sampleig",
            "fields": {"bio": "b" * 151}})
        item = audit_profiles(self.data, platform="instagram")["reports"][0]
        alert = next(a for a in item["alerts"] if a["kind"] == "length_review")
        self.assertEqual((alert["field"], alert["limit"], alert["length"]), ("bio", 150, 151))
        self.assertTrue(alert["source"].startswith("https://help.instagram.com/"))
        # X's bio page could not be curl-verified on 2026-10-15, so a long X bio is not flagged.
        self.data["profiles"].append({
            "platform": "x", "account_id": "sample-x", "handle": "@samplex",
            "fields": {"bio": "b" * 400}})
        item = audit_profiles(self.data, platform="x")["reports"][0]
        self.assertNotIn("length_review", [a["kind"] for a in item["alerts"]])
        self.assertEqual(item["field_limits"]["bio"], "unknown")
        item = audit_profiles(self.data, platform="youtube")["reports"][0]
        self.assertNotIn("length_review", [a["kind"] for a in item["alerts"]])

    def test_every_sourced_field_limit_has_url_date_and_quote_naming_the_number(self):
        rows = load_registry()["platforms"]
        sourced = [(p["id"], e) for p in rows for e in p.get("profile_field_limits", [])]
        self.assertGreaterEqual(len({pid for pid, _ in sourced}), 9)
        for pid, entry in sourced:
            with self.subTest(platform=pid, field=entry["field"]):
                self.assertTrue(entry["source_url"].startswith("https://"))
                self.assertEqual(entry["checked_on"], "2026-10-15")
                self.assertIn(str(entry["max"]), entry["quote"])
                self.assertIn(entry["unit"], ("characters", "graphemes"))
        for p in rows:
            bio = next((e for e in p.get("profile_field_limits", []) if e["field"] == "bio"), None)
            self.assertEqual(p["profile_bio_limit"], bio["max"] if bio else None)
            if bio is None:
                self.assertEqual(p["profile_bio_limit_scope"], "unverified")

    def test_unsourced_fields_stay_unknown_and_are_never_length_checked(self):
        rows = {p["id"]: p for p in load_registry()["platforms"]}
        for pid in ("linkedin", "threads", "tiktok", "reddit", "telegram", "discord", "hashnode",
                    "producthunt", "snapchat", "whatsapp", "hackernews"):
            self.assertEqual(rows[pid]["profile_field_limits"], [], pid)
            self.assertIsNone(rows[pid]["profile_bio_limit"], pid)
        self.data["profiles"].append({
            "platform": "linkedin", "account_id": "sample-li", "handle": "sample-li",
            "fields": {"headline": "h" * 3900, "bio": "b" * 3900}})
        item = audit_profiles(self.data, platform="linkedin")["reports"][1]
        self.assertNotIn("length_review", [a["kind"] for a in item["alerts"]])
        self.assertEqual(item["field_limits"], {"display_name": "unknown", "headline": "unknown",
                                                "bio": "unknown", "handle": "unknown"})

    def test_handle_and_grapheme_limits(self):
        self.data["profiles"].append({
            "platform": "mastodon", "account_id": "sample-m", "handle": "@" + "u" * 31 + "@example.social",
            "fields": {"display_name": "Sample"}})
        item = audit_profiles(self.data, platform="mastodon")["reports"][0]
        self.assertEqual([(a["field"], a["limit"]) for a in item["alerts"] if a["kind"] == "length_review"],
                         [("handle", 30)])
        family = "\U0001f468‍\U0001f469‍\U0001f467"  # one grapheme, five code points
        self.data["profiles"].append({
            "platform": "bluesky", "account_id": "sample-b", "handle": "sample.example.org",
            "fields": {"bio": family * 256}})
        item = audit_profiles(self.data, platform="bluesky")["reports"][0]
        self.assertNotIn("length_review", [a["kind"] for a in item["alerts"]])
        self.data["profiles"][-1]["fields"]["bio"] = "é" * 257  # combining accent
        item = audit_profiles(self.data, platform="bluesky")["reports"][0]
        self.assertEqual(item["alerts"][0]["length"], 257)
        self.assertIn("Length: bio is 257 graphemes", render_summary(audit_profiles(self.data, platform="bluesky")))

    def test_registry_refuses_a_limit_without_evidence(self):
        from reachweaver import profile_audit
        registry = load_registry()
        x = next(p for p in registry["platforms"] if p["id"] == "x")
        bad = [dict(x["profile_field_limits"][0], quote="no number here"),
               {k: v for k, v in x["profile_field_limits"][0].items() if k != "source_url"},
               dict(x["profile_field_limits"][0], checked_on="recently"),
               dict(x["profile_field_limits"][0], field="headline")]
        for entry in bad:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                x["profile_field_limits"] = [entry]
                path = Path(tmp) / "registry.json"
                path.write_text(json.dumps(registry), encoding="utf-8")
                with patch.object(profile_audit, "REGISTRY", path), self.assertRaises(ValidationError):
                    load_registry()
        x["profile_field_limits"] = []
        x["profile_bio_limit"] = 160
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with patch.object(profile_audit, "REGISTRY", path), self.assertRaises(ValidationError):
                load_registry()

    def test_professional_brand_mismatch_is_advisory_not_algorithm_penalty(self):
        self.data["profiles"][1]["fields"]["display_name"] = "Different Organization"
        item = audit_profiles(self.data, platform="youtube")["reports"][0]
        self.assertIn("brand_consistency", [x["kind"] for x in item["alerts"]])
        self.assertTrue(item["readiness_heuristic_score"] is not None)

    def test_human_only_commmunities_not_advised_to_autopost(self):
        self.data["profiles"].append({
            "platform":"hackernews","account_id":"sample-hn","handle":"samplehn",
            "fields":{"about":"I study distributed systems","website":"https://example.org",
                      "rules_reviewed":True,"community_intro":True},
        })
        p = audit_profiles(self.data, platform="hackernews")["reports"][0]
        self.assertIn("human_only",[x["kind"] for x in p["alerts"]])
        self.assertIn("human",p["post_hypothesis"].lower())

    def test_post_audit_nearest_age_same_format_source_and_no_causal_claim(self):
        r = audit_posts(self.data, platform="linkedin")
        self.assertEqual(r["post_count"],3)
        group = r["format_cohorts"][0]
        self.assertEqual(group["sample_size"],3)
        self.assertEqual(group["source"],"owner-native-export")
        self.assertEqual(group["median_action_per_exposure"],0.06)
        self.assertEqual(group["status"],"exploratory_descriptive_only")
        self.assertIn("randomization",str(r["warnings"]))
        self.assertIn("relevant_cta",r["post_reports"][0]["suggestions"][0]["signal"])

    def test_post_missing_denominator_or_age_not_zero(self):
        self.data["posts"][0]["metrics"].pop("impressions")
        self.data["posts"][1]["observed_at"]="2026-10-09T10:00:00+02:00"
        r = audit_posts(self.data, platform="linkedin")
        self.assertEqual(r["excluded"]["missing_comparable_native_metrics"],1)
        self.assertEqual(r["excluded"]["unmatched_post_age"],1)
        self.assertEqual(r["format_cohorts"][0]["sample_size"],1)
        self.assertIsNone(r["format_cohorts"][0]["median_action_per_exposure"])

    def test_format_and_source_never_cross_pooled(self):
        self.data["posts"][1]["format"]="video"
        self.data["posts"][2]["source"]="other-native-source"
        r = audit_posts(self.data, platform="linkedin")
        self.assertEqual([g["sample_size"] for g in r["format_cohorts"]],[1,1,1])
        self.assertTrue(all(g["status"]=="insufficient_data" for g in r["format_cohorts"]))

    def test_post_review_flags_are_not_inferred_from_views(self):
        self.data["posts"][0]["review_flags"]={}
        r = audit_posts(self.data, platform="linkedin")
        self.assertEqual(r["post_reports"][0]["creative_review"]["hook_clear"],"unknown")
        self.assertIsNone(audit_posts(self.data, platform="youtube")["format_cohorts"][0]["median_action_per_exposure"])

    def test_invalid_input_and_secret_key_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"snap.json"
            bads = [
                {**self.data, "auth_token":"private"},
                {**self.data, "profiles":[{**self.data["profiles"][0], "fields":{"avatar":"yes"}}]},
                {**self.data, "profiles":[{**self.data["profiles"][0], "fields":{"unverified_field":True}}]},
                {**self.data, "profiles":[{**self.data["profiles"][0], "platform":"unknownnetwork"}]},
                {**self.data, "posts":[{**self.data["posts"][0], "metrics":{"views": -1}}]},
                {**self.data, "posts":[{**self.data["posts"][0], "review_flags":{"hook_clear":"maybe"}}]},
                {**self.data, "posts":[{**self.data["posts"][0], "account_id":"other-account"}]},
            ]
            for data in bads:
                with self.subTest(bad=list(data.keys())):
                    self.write(data,path)
                    with self.assertRaises(ValidationError):
                        load_snapshot(path)

    def test_validates_age_and_account_filter(self):
        with self.assertRaisesRegex(ValidationError,"age_hours"):
            audit_posts(self.data,age_hours=12)
        with self.assertRaisesRegex(ValidationError,"min_posts"):
            audit_posts(self.data,min_posts=0)
        with self.assertRaisesRegex(ValidationError,"No matching"):
            audit_profiles(self.data,platform="pinterest")

    def test_cli_stays_offline_and_creates_no_database_or_network_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp)/"should-not-exist.sqlite"
            for cmd in ("profile-audit","post-audit"):
                output = io.StringIO()
                with patch.object(socket,"create_connection",side_effect=AssertionError("network used")),\
                     contextlib.redirect_stdout(output):
                    code = main(["--db",str(db),cmd,"--file",str(EXAMPLE),
                                 "--platform","linkedin","--json"])
                self.assertEqual(code,0)
                r = json.loads(output.getvalue())
                self.assertEqual(r["kind"],"profile_audit" if cmd=="profile-audit" else "post_audit")
                self.assertFalse(db.exists())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["profile-audit","--file",str(EXAMPLE)]),0)
            self.assertIn("OFFLINE",output.getvalue())
            self.assertIn("linkedin",output.getvalue())


if __name__ == "__main__":
    unittest.main()
