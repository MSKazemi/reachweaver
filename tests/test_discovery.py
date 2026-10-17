"""Discovery plans: topical relevance, source freshness, platform-specific safety, no network."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.discovery import (
    STATUS, TREND_MAX_AGE_DAYS, build_discovery_plan, check_file,
    load_discovery_registry,
)
from reachweaver.policy import ValidationError


def basic(**kwargs):
    payload = {
        "schema_version": 1, "platform": "tiktok", "as_of": "2026-10-17",
        "text": ("Original demo of offline AI speech recognition on Linux. "
                 "See true transcription results and known limitations."),
        "audience": "Linux software developers", "region": "US", "language": "en",
        "keywords": ["Linux", "offline AI", "speech recognition"],
        "max_suggestions": 4,
        "candidates": [
            {"tag": "#Linux", "kind": "niche"},
            {"tag": "#AI", "kind": "broad"},
            {"tag": "#SpeechRecognition", "kind": "core"},
            {"tag": "#DancingCat", "kind": "trend", "region": "US"},
        ],
    }
    payload.update(kwargs)
    return payload


def tagnames(report):
    return [e["tag"] for e in report["recommended"]]


class DiscoveryTests(unittest.TestCase):
    def test_registry_sources_are_currently_reviewed_navigation_guidance(self):
        d = load_discovery_registry()
        self.assertGreaterEqual(len(d["platforms"]), 16)
        for platform in ("tiktok", "youtube", "linkedin", "instagram",
                         "threads", "xiaohongshu", "bilibili", "douyin"):
            item = d["platforms"][platform]
            self.assertTrue(item["sources"])
            self.assertTrue(all(s["checked_on"] == "2026-10-17" for s in item["sources"]))
            self.assertIn(item["surface"], ("hashtags", "topic_label", "keywords"))

    def test_related_niche_broad_and_core_recommended_with_no_view_prediction(self):
        p = build_discovery_plan(basic())
        tags = tagnames(p)
        self.assertIn("#AI", tags)
        self.assertIn("#SpeechRecognition", tags)
        self.assertIn("#Linux", tags)
        self.assertNotIn("#DancingCat", tags)
        self.assertLessEqual(len(tags), 4)
        self.assertEqual(p["status"], STATUS)
        self.assertEqual(p["platform_guidance"]["evidence_level"], "official_signal_families_only")
        self.assertIsNone(p["safety"]["predicted_views"])
        self.assertFalse(p["safety"]["network"])
        self.assertFalse(p["safety"]["publishes"])
        self.assertFalse(p["safety"]["schedules"])
        self.assertFalse(p["safety"]["popularity_verified"])
        self.assertTrue(all(x["editorial_relevance_score"] < 100 for x in p["recommended"]))
        self.assertIn("tag_not_lexically_related", str(p["skipped"]))

    def test_recent_owner_observed_topic_trend_can_be_included_but_not_verified(self):
        trend = {
            "tag": "#SpeechRecognition", "kind": "trend",
            "source_type": "official_tool_recorded",
            "source_url": "https://ads.tiktok.com/resources/help/article/how-to-use-trends",
            "observed_on": "2026-10-16", "region": "US",
            "evidence_note": "Synthetic test observation: user checks regional trend panel.",
        }
        item = basic(candidates=[
            {"tag": "#Linux", "kind": "niche"},
            {"tag": "#AI", "kind": "broad"},
            trend,
        ])
        report = build_discovery_plan(item)
        self.assertIn("#SpeechRecognition", tagnames(report))
        rec = next(x for x in report["recommended"] if x["tag"] == "#SpeechRecognition")
        self.assertEqual(rec["trend_status"], "owner_reported_unverified")
        self.assertEqual(rec["category"], "trend")
        self.assertEqual(rec["observed_on"], "2026-10-16")
        self.assertFalse(report["safety"]["popularity_verified"])

    def test_stale_future_wrong_region_and_unknown_official_tool_are_filtered(self):
        source = {
            "tag": "#SpeechRecognition", "kind": "trend", "source_type": "official_tool_recorded",
            "source_url": "https://ads.tiktok.com/resources/help/article/how-to-use-trends",
            "observed_on": "2026-10-01", "region": "US",
            "evidence_note": "Synthetic historical screenshot, not live trend data.",
        }
        scenarios = [
            ({"observed_on": "2026-10-01"}, "trend_evidence_expired"),
            ({"observed_on": "2026-10-18"}, "trend_observation_in_future"),
            ({"region": "GB", "observed_on": "2026-10-16"}, "trend_region_mismatch"),
            ({"source_url": "https://example.org/research",
              "observed_on": "2026-10-16"}, "official_trend_source_not_recognized"),
            ({"source_type": "editorial",
              "observed_on": "2026-10-16"}, "trend_requires_owner_or_official_source"),
            ({"source_url": None,
              "observed_on": None}, "trend_requires_dated_source_and_observation"),
        ]
        for change, code in scenarios:
            with self.subTest(code=code):
                payload = basic(candidates=[{**source, **change}])
                result = build_discovery_plan(payload)
                self.assertTrue(any(x["reason"] == code for x in result["skipped"]), result)
                self.assertNotIn("#SpeechRecognition", tag for tag in [])
                self.assertTrue(all(x["category"] != "trend" for x in result["recommended"]))
        self.assertEqual(TREND_MAX_AGE_DAYS, 14)

    def test_brand_and_broad_tags_do_not_overwrite_topic_truth(self):
        p = build_discovery_plan(basic(
            keywords=["Linux"],
            candidates=[
                {"tag": "#WorldCup", "kind": "trend",
                 "source_type": "official_tool_recorded",
                 "source_url": "https://ads.tiktok.com/resources/help/article/how-to-use-trends",
                 "observed_on": "2026-10-17", "region": "US",
                 "evidence_note": "Synthetic popular topic unrelated to a Linux demonstration."},
                {"tag": "#Travel", "kind": "broad"},
                {"tag": "#Linux", "kind": "niche"},
            ],
        ))
        self.assertEqual(tagnames(p), ["#Linux"])
        self.assertTrue(any(x["reason"] == "tag_not_lexically_related_to_post_or_keywords"
                            for x in p["skipped"]))

    def test_youtube_does_not_pretend_tags_drive_video_discovery(self):
        item = basic(platform="youtube", candidates=[{"tag": "#Linux", "kind": "niche"}])
        p = build_discovery_plan(item)
        self.assertEqual(p["surface"], "keywords")
        self.assertEqual(p["recommended"], [])
        self.assertEqual(p["editorial_tag_budget"], 0)
        self.assertIn("tag", " ".join(p["warnings"]).lower())
        self.assertIn("thumbnail", " ".join(p["platform_guidance"]["creative_checks"]).lower())

    def test_threads_one_native_topic_not_generic_hashtag_flood(self):
        item = basic(platform="threads", candidates=[
            {"tag": "SpeechRecognition", "kind": "core"},
            {"tag": "Linux", "kind": "niche"},
        ])
        report = build_discovery_plan(item)
        self.assertEqual(report["surface"], "topic_label")
        self.assertEqual(len(report["recommended"]), 1)
        self.assertTrue(all(not x["tag"].startswith("#") for x in report["recommended"]))

    def test_unknown_markets_use_conservative_fallback(self):
        result = build_discovery_plan(basic(platform="sharechat"))
        self.assertEqual(result["platform_guidance"]["evidence_level"], "editorial_only")
        self.assertTrue(result["warnings"])
        self.assertLessEqual(len(result["recommended"]), 2)

    def test_human_only_destinations_do_not_generate_tags(self):
        for platform in ("hacker_news", "reddit", "douban", "pantip", "naver_cafe", "github"):
            with self.subTest(platform=platform), self.assertRaises(ValidationError):
                build_discovery_plan(basic(platform=platform))

    def test_inputs_with_secrets_or_invalid_shapes_fail_closed(self):
        variants = [
            basic(schema_version=2), basic(platform="not-a-site"),
            basic(region="europe"), basic(as_of="2026-99-99"),
            basic(keywords=[]), basic(keywords=["x"] * 13),
            basic(candidates=[{"tag": "##notvalid"}]),
            basic(candidates=[{"tag": "#Linux", "kind": "unknown"}]),
            basic(candidates=[{"tag": "#Linux",
                               "source_url": "http://localhost/a"}]),
            basic(candidates=[{"tag": "#Linux",
                               "source_url": "https://site.org/?secret=test"}]),
            basic(max_suggestions=True),
            basic(**{("api_" + "key"): "not_a_credential"}),
        ]
        for payload in variants:
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(ValidationError):
                build_discovery_plan(payload)

    def test_cli_offline_and_input_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            file = folder / "plan.json"
            file.write_text(json.dumps(basic()), encoding="utf-8")
            db = folder / "never.db"
            out = io.StringIO()
            with patch.object(socket, "create_connection", side_effect=RuntimeError("network!")):
                with contextlib.redirect_stdout(out):
                    code = main(["--db", str(db), "discovery-plan", "--file", str(file), "--json"])
            self.assertEqual(code, 0)
            plan = json.loads(out.getvalue())
            self.assertIn("#Linux", tagnames(plan))
            self.assertFalse(db.exists())
            link = folder / "symlink.json"
            link.symlink_to(file)
            with self.assertRaises(ValidationError):
                check_file(link)
            file.write_text("{" * 260_000, encoding="utf-8")
            with self.assertRaises(ValidationError):
                check_file(file)


if __name__ == "__main__":
    unittest.main()
