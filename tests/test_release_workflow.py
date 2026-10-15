"""The tag-triggered release workflow runs the full offline release gate before it builds or publishes anything
(public issue #55), in a read-only ``verify`` job separate from the only job that may write (private #50, #51).

Static checks on ``.github/workflows/release.yml`` (stdlib only, no YAML parser): the gate is what refuses a release
with a missing or undated changelog section, broken docs links, a stale skill bundle, a blocked platform without an
alternative, an invalid readiness scorecard or a failing test suite. ``tests/test_release_ref_guard.py`` covers the
owner/tag/current-main guard both jobs run.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
HEADER, JOBS = WORKFLOW.split("\njobs:\n", 1)
VERIFY = ("\n" + JOBS).split("\n  verify:\n", 1)[1].split("\n  publish:\n", 1)[0]
PUBLISH = ("\n" + JOBS).split("\n  publish:\n", 1)[1]


def step_names() -> list[str]:
    return re.findall(r"(?m)^      - name: (.+)$", WORKFLOW)


def step_block(prefix: str, text: str = WORKFLOW) -> str:
    parts = re.split(r"(?m)^      - ", text)
    return next(part for part in parts if part.startswith("name: " + prefix))


def job_steps(job: str) -> list[str]:
    return re.split(r"(?m)^      - ", job)[1:]


def run_lines(text: str) -> list[str]:
    return [line.strip()[len("run: "):] for line in text.splitlines() if line.strip().startswith("run: ")]


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
        # Workflow default is read-only; the single write permission belongs to the publish job.
        self.assertIn("\npermissions:\n  contents: read\n", HEADER + "\n")
        self.assertEqual(len(re.findall(r"(?m)^\s+[a-z-]+: write\b", WORKFLOW)), 1)
        self.assertIn("    permissions:\n      contents: write", PUBLISH)
        self.assertIn("    permissions:\n      contents: read\n", VERIFY)
        self.assertNotIn("write", VERIFY)
        self.assertEqual(set(re.findall(r"(?m)^\s+([a-z-]+): (?:read|write)\b", WORKFLOW)), {"contents"})

    def test_the_gate_script_the_workflow_calls_exists_and_accepts_the_tag_flag(self):
        script = (ROOT / "scripts" / "release_gate.py").read_text(encoding="utf-8")
        self.assertIn('"--tag"', script)
        self.assertIn("--skip-tests", script)
        self.assertIn("exits 3 (incomplete) at best, never 0", script)  # why the workflow must not pass it


class ReleaseWorkflowSplitTests(unittest.TestCase):
    """Private #50/#51: read-only verification, owner-only publication, the same checks as CI."""

    def test_there_are_exactly_two_jobs_and_publish_needs_verify_and_the_owner(self):
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):$", JOBS), ["verify", "publish"])
        self.assertIn("\n    needs: verify\n", "\n" + PUBLISH)
        self.assertIn("    if: github.actor == github.repository_owner && "
                      "github.triggering_actor == github.repository_owner\n", PUBLISH)
        self.assertNotIn("\n    if:", "\n" + VERIFY)

    def test_no_other_trigger_can_start_it(self):
        for trigger in ("workflow_dispatch", "pull_request", "workflow_run", "schedule", "repository_dispatch"):
            self.assertNotIn(trigger, WORKFLOW)

    def test_both_checkouts_fetch_everything_and_persist_no_credentials(self):
        for job in (VERIFY, PUBLISH):
            checkout = job_steps(job)[0]
            self.assertTrue(checkout.startswith("uses: actions/checkout@"))
            self.assertIn("fetch-depth: 0", checkout)
            self.assertIn("persist-credentials: false", checkout)
        self.assertEqual(WORKFLOW.count("persist-credentials: false"), 2)

    def test_both_jobs_run_the_ref_guard_first_in_verify_and_last_before_publishing(self):
        self.assertEqual(run_lines(VERIFY)[0], "python scripts/release_ref_guard.py")
        self.assertEqual(VERIFY.count("python scripts/release_ref_guard.py"), 1)
        self.assertEqual(PUBLISH.count("python scripts/release_ref_guard.py"), 1)
        publish_runs = run_lines(PUBLISH)
        self.assertEqual(publish_runs[-2], "python scripts/release_ref_guard.py")
        self.assertTrue(publish_runs[-1].startswith("gh release create "))
        self.assertTrue((ROOT / "scripts" / "release_ref_guard.py").is_file())

    def test_verify_runs_every_ci_hygiene_check_with_the_same_pins_and_flags(self):
        ci_runs = run_lines(CI)
        verify_runs = run_lines(VERIFY)
        for command in ("python scripts/secret_scan.py", "python scripts/lint.py",
                        "python -m compileall -q reachweaver scripts tests",
                        "python scripts/check_runtime_deps.py", "python scripts/sync_skill.py --check",
                        'python scripts/release_gate.py --tag "$GITHUB_REF_NAME"'):
            self.assertIn(command, verify_runs)
        for tool in ("ruff", "mypy"):
            ci = [r for r in ci_runs if r.startswith(f"pipx run --spec {tool}==")]
            self.assertEqual(len(ci), 1, tool)
            self.assertEqual([r for r in verify_runs if r.startswith(f"pipx run --spec {tool}==")], ci, tool)
        self.assertIn("python scripts/secret_scan.py", ci_runs)

    def test_verify_never_skips_or_softens_a_failure(self):
        self.assertNotIn("--skip-tests", "\n".join(run_lines(WORKFLOW)))
        for weakener in ("continue-on-error", "|| true", "|| :", "set +e"):
            self.assertNotIn(weakener, WORKFLOW)

    def test_the_release_gate_and_every_check_run_only_in_verify_and_before_publish(self):
        self.assertIn("release_gate.py", VERIFY)
        self.assertNotIn("release_gate.py", PUBLISH)
        self.assertNotIn("gh release", VERIFY)
        self.assertNotIn("github.token", VERIFY)
        self.assertNotIn("GH_TOKEN", VERIFY)

    def test_publish_tests_the_archive_and_checksum_and_only_publishes_an_existing_tag(self):
        build = step_block("Build bundle", PUBLISH)
        for command in ("python -m zipfile -t skill.zip", "sha256sum --check --strict skill.zip.sha256"):
            self.assertIn(command, build)
        self.assertLess(build.index("sha256sum skill.zip >"), build.index("sha256sum --check"))
        release = run_lines(PUBLISH)[-1]
        self.assertIn("--verify-tag", release)
        self.assertIn("skill.zip skill.zip.sha256", release)

    def test_every_action_is_pinned_to_a_full_commit_sha(self):
        actions = re.findall(r"(?m)^\s+- uses: (\S+)", WORKFLOW)
        self.assertEqual(len(actions), 4)
        for action in actions:
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
