"""The release-to-draft workflow TEMPLATE stays credential-free, read-only and post-free (offline, stdlib only).

``examples/github-workflows/release-to-draft.yml`` is shipped for users to copy into their own repository; it is
deliberately NOT under ``.github/workflows/``, so it never runs here. No YAML parser: structural text checks in the
style of ``tests/test_release_workflow.py``.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "examples/github-workflows/release-to-draft.yml"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
HEADER, JOBS = TEMPLATE.split("\njobs:\n", 1)
REPO_WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def uses_lines(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"(?m)^\s+(?:- )?uses:\s*(\S+)", text)]


def run_lines(text: str) -> list[str]:
    lines, block = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run: |"):
            block = True
            continue
        if stripped.startswith("run: "):
            lines.append(stripped[len("run: "):])
            block = False
        elif block and line.startswith("          ") and stripped:
            lines.append(stripped)
        elif stripped:
            block = False
    return lines


class ReleaseToDraftTemplateTests(unittest.TestCase):
    def test_it_is_a_template_not_an_active_workflow_of_this_repository(self):
        self.assertFalse((ROOT / ".github/workflows/release-to-draft.yml").exists())
        for workflow in REPO_WORKFLOWS:
            self.assertNotIn("release-brief", workflow.read_text(encoding="utf-8"), workflow.name)

    def test_it_triggers_only_on_a_published_release(self):
        self.assertIn("on:\n  release:\n    types: [published]\n", HEADER)
        for trigger in ("push:", "pull_request", "pull_request_target", "workflow_run", "schedule:"):
            self.assertNotIn(trigger, TEMPLATE)

    def test_no_secret_other_than_the_default_github_token(self):
        secrets = set(re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)", TEMPLATE))
        self.assertLessEqual(secrets, {"GITHUB_TOKEN"})
        self.assertEqual(set(re.findall(r"\$\{\{\s*github\.token\s*\}\}", TEMPLATE)), {"${{ github.token }}"})
        for name in ("REACHWEAVER_", "BSKY", "MASTODON", "LINKEDIN", "TELEGRAM", "PYPI", "id-token"):
            self.assertNotIn(name, TEMPLATE)

    def test_permissions_are_read_only(self):
        self.assertIn("\npermissions:\n  contents: read\n", HEADER + "\n")
        self.assertEqual(re.findall(r"(?m)^\s*permissions:", TEMPLATE), ["permissions:"])  # no job-level widening
        self.assertNotRegex(TEMPLATE, r"(?m):\s*write\b")
        self.assertNotIn("write-all", TEMPLATE)

    def test_no_publish_approve_or_live_strings(self):
        body = TEMPLATE.replace("types: [published]", "")
        for word in ("publish", "approve", "--live", "campaign-stage", "schedule", "reconcile"):
            self.assertNotIn(word, body.casefold(), word)

    def test_every_action_is_pinned_by_full_sha_reusing_the_repository_pins(self):
        used = uses_lines(TEMPLATE)
        self.assertEqual({u.split("@", 1)[0] for u in used},
                         {"actions/checkout", "actions/setup-python", "actions/upload-artifact"})
        repo_pins = {u for w in REPO_WORKFLOWS for u in uses_lines(w.read_text(encoding="utf-8"))}
        for ref in used:
            with self.subTest(ref=ref):
                self.assertRegex(ref.split("@", 1)[1], FULL_SHA)
                self.assertIn(ref, repo_pins)
        self.assertIn("persist-credentials: false", TEMPLATE)

    def test_steps_read_the_release_write_the_scaffold_check_it_and_upload_it(self):
        runs = run_lines(JOBS)
        joined = "\n".join(runs)
        self.assertIn('gh api "repos/$REPO/releases/$RELEASE_ID" > "$RUNNER_TEMP/release.json"', runs)
        self.assertIn('reachweaver release-brief --release "$RUNNER_TEMP/release.json" --repo "$REPO" '
                      '--out release-draft', runs)
        self.assertIn("reachweaver campaign-check --file release-draft/campaign.reachweaver.json", runs)
        # BLOCKED (exit 2) is the expected result; any other failure still fails the job.
        self.assertIn('if [ "$code" -ne 0 ] && [ "$code" -ne 2 ]; then exit "$code"; fi', runs)
        self.assertNotIn("|| true", joined)
        self.assertNotIn("continue-on-error", TEMPLATE)
        self.assertIn("path: release-draft/", TEMPLATE)
        self.assertLess(joined.index("gh api"), joined.index("release-brief"))
        self.assertLess(joined.index("release-brief"), joined.index("campaign-check"))

    def test_event_values_reach_the_shell_through_env_never_inline(self):
        for line in run_lines(JOBS):
            with self.subTest(line=line):
                self.assertNotIn("${{", line)

    def test_header_says_no_credentials_no_posting_and_how_to_continue(self):
        header = TEMPLATE.split("\nname:", 1)[0]
        for phrase in ("no credentials", "no posting", "never makes a post", "download", "docs/RELEASE-TO-DRAFT.md"):
            self.assertIn(phrase, header.casefold() if phrase.islower() else header)


if __name__ == "__main__":
    unittest.main()
