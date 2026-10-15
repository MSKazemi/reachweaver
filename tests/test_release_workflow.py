"""The tag-triggered release workflow runs the full offline release gate before it builds or publishes anything (public issue #55).

Static checks on ``.github/workflows/release.yml`` (stdlib only, no YAML parser): the gate is what refuses a release
with a missing or undated changelog section, broken docs links, a stale skill bundle, a blocked platform without an
alternative, an invalid readiness scorecard or a failing test suite.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def step_names() -> list[str]:
    return re.findall(r"(?m)^      - name: (.+)$", WORKFLOW)


def step_block(prefix: str) -> str:
    parts = re.split(r"(?m)^      - ", WORKFLOW)
    return next(part for part in parts if part.startswith("name: " + prefix))


class ReleaseWorkflowGateTests(unittest.TestCase):
    def test_it_runs_only_on_a_semver_tag(self):
        self.assertIn("tags: ['v[0-9]+.[0-9]+.[0-9]+']", WORKFLOW)
        self.assertNotIn("pull_request", WORKFLOW)
        self.assertNotIn("branches:", WORKFLOW)

    def test_the_gate_step_runs_the_gate_with_the_tag_and_never_skips_tests(self):
        block = step_block("Release gate")
        self.assertIn('python scripts/release_gate.py --tag "$GITHUB_REF_NAME"', block)
        self.assertNotIn("--skip-tests", block.split("run:", 1)[1])
        self.assertNotIn("continue-on-error", block)
        self.assertNotIn("|| true", block)

    def test_the_gate_runs_before_the_bundle_is_built_and_before_anything_is_published(self):
        names = step_names()
        gate = next(i for i, n in enumerate(names) if n.startswith("Release gate"))
        build = next(i for i, n in enumerate(names) if n.startswith("Build bundle"))
        publish = next(i for i, n in enumerate(names) if n.startswith("Publish GitHub Release"))
        self.assertLess(gate, build)
        self.assertLess(build, publish)

    def test_the_version_and_bundle_check_still_runs_first(self):
        names = step_names()
        self.assertLess(next(i for i, n in enumerate(names) if n.startswith("Tag must match")),
                        next(i for i, n in enumerate(names) if n.startswith("Release gate")))

    def test_the_workflow_uses_no_credential_other_than_the_github_token_and_contacts_no_platform(self):
        secrets = set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)", WORKFLOW))
        self.assertEqual(secrets, set())
        self.assertEqual(set(re.findall(r"\$\{\{\s*github\.token\s*\}\}", WORKFLOW)), {"${{ github.token }}"})
        for platform in ("bsky", "mastodon", "linkedin", "telegram", "REACHWEAVER_ALLOW_LIVE", "REACHWEAVER_ENV"):
            self.assertNotIn(platform, WORKFLOW)

    def test_permissions_are_limited_to_writing_the_release(self):
        self.assertIn("permissions:\n  contents: write", WORKFLOW)
        self.assertEqual(len(re.findall(r"(?m)^\s+[a-z-]+: write$", WORKFLOW)), 1)

    def test_the_gate_script_the_workflow_calls_exists_and_accepts_the_tag_flag(self):
        script = (ROOT / "scripts" / "release_gate.py").read_text(encoding="utf-8")
        self.assertIn('"--tag"', script)
        self.assertIn("--skip-tests", script)
        self.assertIn("exits 3 (incomplete) at best, never 0", script)  # why the workflow must not pass it


if __name__ == "__main__":
    unittest.main()
