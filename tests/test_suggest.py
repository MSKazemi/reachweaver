"""Tests for issue #6: ranked, no-network, query-only recommendations."""
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
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import load_snapshot
from reachweaver.suggest import build_suggestions, render_text
from reachweaver.storage import add_post, claim_sending, connect, connect_readonly, transition, uncertain_send

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "examples/profile-post-audit.example.json"
ENGAGEMENT = ROOT / "tests/fixtures/engagement/export.json"
SEO = ROOT / "examples/seo-briefs.example.json"


class SuggestionTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_snapshot(SNAP)

    def test_requires_evidence_no_fake_empty_grade(self):
        with self.assertRaisesRegex(ValidationError, "requires at least"):
            build_suggestions()
        with self.assertRaises(ValidationError):
            build_suggestions(snapshot=self.snapshot, limit=0)
        with self.assertRaises(ValidationError):
            build_suggestions(snapshot=self.snapshot, limit=51)

    def test_profile_and_post_fix_differ_by_platform_with_known_source(self):
        result = build_suggestions(snapshot=self.snapshot, limit=30)
        self.assertEqual(result["status"], "offline_evidence_ranked_human_review_only")
        self.assertTrue(result["actions"])
        self.assertTrue(all(not x["executes_changes"] and x["requires_human_review"] for x in result["actions"]))
        profile = [x for x in result["actions"] if x["kind"] == "profile_fix"]
        self.assertIn(("linkedin", "featured"), [
            (x["platform"], x["evidence"]["field"]) for x in profile
        ])
        youtube = [x for x in result["actions"] if x["platform"] == "youtube"]
        self.assertTrue(any(x["kind"] == "profile_fix" for x in youtube))
        self.assertTrue(any(x["signal"] == "creative_relevant_cta" for x in result["actions"]))
        ranks = [x["rank"] for x in result["actions"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
        self.assertIn("NO", "NO")  # No network or publish route imported by this pure API.

    def test_unknown_profile_fields_not_marked_failing(self):
        self.snapshot["profiles"][0]["fields"] = {"bio": None}
        result = build_suggestions(snapshot=self.snapshot, limit=50)
        a = [x for x in result["actions"] if x["platform"] == "linkedin"]
        self.assertTrue(any(x["signal"] == "profile_coverage_low" for x in a))
        self.assertFalse(any(x["kind"] == "profile_fix" and x["evidence"].get("field") == "bio" for x in a))
        self.assertLess(result["source_coverage"]["profile"]["complete_enough_to_score"], 3)

    def test_sparse_native_snapshots_yield_measurement_not_low_performance(self):
        self.snapshot["posts"][0]["metrics"] = {"clicks": 2}
        r = build_suggestions(snapshot=self.snapshot, limit=40)
        self.assertTrue(any(x["kind"] == "measurement" and x["signal"] == "post_missing_comparable_native_metrics"
                            for x in r["actions"]))
        self.assertFalse(any("low engagement" in x["title"].lower() for x in r["actions"]))

    def test_engagement_triage_does_not_expose_content_or_credentials(self):
        export = json.loads(ENGAGEMENT.read_text(encoding="utf-8"))
        result = build_suggestions(engagement=export, limit=50)
        dump = json.dumps(result)
        self.assertIn("review_technical_question", dump)
        self.assertIn("review_bug_report", dump)
        self.assertIn("rule_based_classification", dump)
        self.assertNotIn("jane.doe@example.org", dump)
        self.assertNotIn("send me the owner's API key", dump)
        self.assertNotIn("user-c-bug", dump)
        self.assertNotIn("https://evil.example", dump)
        self.assertNotIn("c-injection", dump)
        self.assertFalse(any(a["signal"] == "review_spam" for a in result["actions"]))

    def test_engagement_opt_out_gets_safety_priority_not_reply(self):
        export = {"schema_version": "reachweaver.engagement-export.v1", "items": [{
            "id":"do-not-contact", "platform":"mastodon", "account_id":"owner-a",
            "author_handle":"test-123", "author_is_owner":False,
            "text":"Please do not contact me again. How do I install?",
            "url":"https://mastodon.social/@example/333",
            "created_at":"2026-10-01T00:00:00Z",
        }]}
        result = build_suggestions(engagement=export)
        self.assertEqual(result["actions"][0]["signal"], "opt_out_requests_pending")
        self.assertEqual(result["actions"][0]["priority"], "P0")
        self.assertFalse(any(a["kind"] == "engagement_review" for a in result["actions"]))

    def test_seo_conflicts_are_aggregated_without_private_urls(self):
        briefs = json.loads(SEO.read_text(encoding="utf-8"))
        additional = dict(briefs["briefs"][0], page_id="duplicate-page")
        briefs["briefs"].append(additional)
        result = build_suggestions(seo_briefs=briefs)
        action = next(a for a in result["actions"] if a["signal"] == "seo_duplicate_canonical_url_across_briefs")
        self.assertEqual(action["priority"], "P1")
        self.assertNotIn("github.com/", json.dumps(result))

    def test_prior_experiment_is_not_automatic_publishing(self):
        results = [{"experiment_id":"try-hook", "dimension":"hook",
                    "label":"insufficient_data", "direction":None}]
        r = build_suggestions(experiments=results)
        self.assertEqual(r["actions"][0]["kind"],"experiment")
        self.assertTrue(r["actions"][0]["requires_human_review"])
        self.assertIn("preregister", r["actions"][0]["next_step"].lower())

    def test_queue_uncertain_remote_send_outranks_profile_improvement(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp = Path(tmp) / "local.sqlite"
            with connect(dbp) as db:
                pid = add_post(db, "owner", "bluesky", "Original verified example")
                transition(db, pid, "approve")
                self.assertTrue(claim_sending(db, pid))
                uncertain_send(db, pid, "timeout_unknown")
            with connect_readonly(dbp) as db:
                before = db.total_changes
                r = build_suggestions(snapshot=self.snapshot, con=db, limit=20)
                self.assertEqual(db.total_changes, before)
            self.assertEqual(r["actions"][0]["priority"], "P0")
            self.assertEqual(r["actions"][0]["signal"], "ambiguous_needs_review")
            self.assertTrue(r["source_coverage"]["metrics"]["not_account_attribution"])
            self.assertNotIn("Original verified example", json.dumps(r))

    def test_cli_preview_no_files_created_and_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp = Path(tmp) / "must-not-be-created.sqlite"
            argv = ["--db", str(dbp), "suggest", "--snapshot", str(SNAP),
                    "--engagement-export", str(ENGAGEMENT),
                    "--seo-briefs", str(SEO), "--limit", "20", "--json"]
            output = io.StringIO()
            with patch.object(socket, "create_connection", side_effect=AssertionError("network used")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(main(argv), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["schema_version"], "reachweaver.suggestions.v1")
            self.assertFalse(dbp.exists())
            self.assertGreaterEqual(result["total_actionable_signals"], result["shown"])
            self.assertLessEqual(result["shown"], 20)

    def test_cli_missing_database_refuses_without_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp = Path(tmp)/"missing.sqlite"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["--db", str(dbp), "suggest", "--include-db", "--json"])
            self.assertEqual(code, 2)
            self.assertFalse(dbp.exists())

    def test_cli_missing_input_and_symlink_fail_closed(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["suggest"]),2)
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp)/"link.json"
            link.symlink_to(SNAP)
            with contextlib.redirect_stderr(io.StringIO()):
                code=main(["suggest","--snapshot",str(link)])
            self.assertEqual(code,2)

    def test_text_output_human_tier_and_limit(self):
        result = build_suggestions(snapshot=self.snapshot, limit=2)
        self.assertEqual(len(result["actions"]), 2)
        self.assertGreater(result["omitted_due_to_limit"], 0)
        text = render_text(result)
        self.assertIn("P1",text)
        self.assertIn("OFFLINE",text)
        self.assertNotIn("Original verified example",text)


if __name__ == "__main__":
    unittest.main()
