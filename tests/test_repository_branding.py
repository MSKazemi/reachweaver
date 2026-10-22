"""Offline contract tests for GitHub About updates and discovery metadata."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import repository_about as about

ROOT = Path(__file__).resolve().parents[1]


class RepositoryBrandingTests(unittest.TestCase):
    def manifest(self) -> dict:
        return about.load_manifest()

    def test_checked_in_metadata_is_scoped_and_matches_brand(self):
        m = self.manifest()
        self.assertEqual(m["repository"], "MSKazemi/reachweaver")
        self.assertEqual(m["product_name"], "ReachWeaver")
        self.assertEqual(m["homepage"], "https://mskazemi.com/reachweaver/")  # the docs site, verified live 2026-10-22
        self.assertEqual(m["metadata_scope"], "about_only_no_visibility_changes")
        self.assertIn("Claude Code skill", m["description"])
        self.assertIn("social media", m["description"])
        self.assertIn("claude-code", m["topics"])
        self.assertIn("social-media-scheduler", m["topics"])
        self.assertEqual(len(m["topics"]), len(set(m["topics"])))
        self.assertLessEqual(len(m["topics"]), 20)

    def test_preview_is_network_free_and_does_not_change_visibility(self):
        output = io.StringIO()
        with patch.object(about, "_gh_api") as api, contextlib.redirect_stdout(output):
            self.assertEqual(about.main([]), 0)
        api.assert_not_called()
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["status"], "offline_preview_only")
        self.assertEqual(preview["homepage"], "https://mskazemi.com/reachweaver/")

    def test_apply_requires_explicit_exact_repository_name_before_network(self):
        m = self.manifest()
        with patch.object(about, "_gh_api") as api:
            with self.assertRaisesRegex(about.MetadataError, "Explicit --confirm"):
                about.apply_metadata(m, confirmed="MSKazemi/another-repo")
        api.assert_not_called()

    def test_authorized_about_write_preserves_existing_topics_and_private_status(self):
        m = self.manifest()
        responses = [
            {"full_name": "MSKazemi/reachweaver", "private": True,
             "description": "Old description", "homepage": None},
            {"names": ["python"]},
            {"full_name": "MSKazemi/reachweaver"},
            {"names": sorted(set(m["topics"]))},
        ]
        with patch.object(about, "_gh_api", side_effect=responses) as api:
            outcome = about.apply_metadata(m, confirmed="MSKazemi/reachweaver")
        self.assertEqual(api.call_count, 4)
        operations = [(c.args[0], c.args[1]) for c in api.call_args_list]
        self.assertEqual(operations, [
            ("GET", "repos/MSKazemi/reachweaver"),
            ("GET", "repos/MSKazemi/reachweaver/topics"),
            ("PATCH", "repos/MSKazemi/reachweaver"),
            ("PUT", "repos/MSKazemi/reachweaver/topics"),
        ])
        patch_body = api.call_args_list[2].args[2]
        self.assertEqual(patch_body, {"description": m["description"], "homepage": m["homepage"]})
        topic_body = api.call_args_list[3].args[2]
        self.assertIn("python", topic_body["names"])
        self.assertEqual(len(topic_body["names"]), len(set(topic_body["names"])))
        self.assertFalse(outcome["visibility_changed"])
        self.assertTrue(outcome["homepage_updated"])

    def test_correct_metadata_is_noop(self):
        m = self.manifest()
        with patch.object(about, "_gh_api", side_effect=[
            {"full_name": m["repository"], "description": m["description"],
             "homepage": m["homepage"], "private": True},
            {"names": m["topics"]},
        ]) as api:
            result = about.apply_metadata(m, confirmed=m["repository"])
        self.assertEqual(api.call_count, 2)
        self.assertFalse(result["description_updated"])
        self.assertFalse(result["topics_updated"])

    def test_invalid_schema_and_attempted_visibility_mutation_rejected(self):
        m = self.manifest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repo-about.json"
            for tampered in (
                {**m, "private": False},
                {**m, "metadata_scope": "public"},
                {**m, "topics": ["Claude Code"]},
                {**m, "topics": [m["topics"][0]] * 2},
                {**m, "description": ""},
                {**m, "homepage": "http://example.com"},
            ):
                path.write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaises(about.MetadataError):
                    about.load_manifest(path)

    def test_merge_topics_preserves_unrelated_and_blocks_overflow(self):
        m = self.manifest()
        result = about.merge_topics(["social-media", "independent-topic"], m["topics"])
        self.assertIn("independent-topic", result)
        self.assertEqual(len(result), len(set(result)))
        with self.assertRaisesRegex(about.MetadataError, "exceed"):
            about.merge_topics([f"extra-topic-{i}" for i in range(20)], m["topics"])

    def test_readme_has_early_discoverability_and_honest_limits(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        first_screen = readme[:3000]
        self.assertTrue(readme.startswith("# ReachWeaver\n"))
        self.assertIn("Claude Code", first_screen)
        self.assertIn("social media", first_screen.lower())
        self.assertIn("[quickstart](docs/quickstart.md)", first_screen.lower())
        # honest limits: the only live sends are the two recorded receipts; everything else is mock-tested or manual
        self.assertIn("one approved text post each was sent through reachweaver to bluesky and mastodon", first_screen.lower())
        self.assertIn("telegram and linkedin have had no live send", first_screen.lower())
        self.assertIn("public-release safety checklist", first_screen)


if __name__ == "__main__":
    unittest.main()
