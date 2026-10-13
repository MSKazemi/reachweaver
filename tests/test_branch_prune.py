"""Offline safety checks for the SHA-leased, allowlisted PR branch janitor."""
from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prune_merged_branches", ROOT / "scripts/prune_merged_branches.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

PR = 57
BRANCH = "feat/platform-profile-post-audit-issue-3"
HEAD = "a" * 40
MERGE = "b" * 40
PREFIX = f"/repos/{mod.REPOSITORY}"


class FakeAPI:
    """Explicit mocked GitHub endpoints; all unknown requests fail closed."""

    def __init__(self) -> None:
        self.routes = {
            f"{PREFIX}/pulls/{PR}": {
                "merged": True, "merged_at": "2026-10-11T12:00:00Z", "state": "closed",
                "base": {"ref": "main"}, "head": {
                    "ref": BRANCH, "sha": HEAD, "repo": {"full_name": mod.REPOSITORY}
                },
                "merge_commit_sha": MERGE,
            },
            f"{PREFIX}/compare/{MERGE}...main": {"status": "ahead", "behind_by": 0},
            f"{PREFIX}/branches/{mod.branch_path(BRANCH)}": {
                "protected": False, "commit": {"sha": HEAD},
            },
            f"{PREFIX}/pulls?state=open&head=MSKazemi%3A{BRANCH.replace('/', '%2F')}&per_page=100": [],
        }
        self.calls = []

    def get(self, endpoint: str):
        self.calls.append(endpoint)
        if endpoint not in self.routes:
            raise AssertionError("unexpected API request " + endpoint)
        return self.routes[endpoint]


class SafePruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = FakeAPI()

    def test_eligible_known_merged_branch(self) -> None:
        self.assertEqual(mod.verified_candidate(self.api, PR, HEAD), (BRANCH, HEAD))
        self.assertEqual(len(self.api.calls), 4)

    def test_dry_run_never_deletes(self) -> None:
        with mock.patch.object(mod, "delete_with_lease") as deleter:
            msg = mod.prune(self.api, PR, HEAD, apply=False, token="placeholder")
            self.assertIn("dry run", msg)
            deleter.assert_not_called()

    def test_apply_rechecks_before_using_exact_sha_lease(self) -> None:
        with mock.patch.object(mod, "delete_with_lease") as deleter:
            msg = mod.prune(self.api, PR, HEAD, apply=True, token="placeholder")
            self.assertIn("removed verified", msg)
            deleter.assert_called_once_with(BRANCH, HEAD, "placeholder")
            self.assertEqual(len(self.api.calls), 8)

    def test_unmerged_or_closed_without_merge_is_preserved(self) -> None:
        pr = self.api.routes[f"{PREFIX}/pulls/{PR}"]
        for change in ({"merged": False}, {"merged_at": None}, {"state": "open"}):
            with self.subTest(change=change):
                old = {k: pr[k] for k in change}
                pr.update(change)
                self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))
                pr.update(old)

    def test_only_immutable_merge_time_sha_is_eligible(self) -> None:
        self.assertIsNone(mod.verified_candidate(self.api, PR, "c" * 40))
        self.api.routes[f"{PREFIX}/pulls/{PR}"]["head"]["sha"] = "c" * 40
        self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))

    def test_fork_head_is_never_deleted(self) -> None:
        self.api.routes[f"{PREFIX}/pulls/{PR}"]["head"]["repo"]["full_name"] = "attacker/fork"
        self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))

    def test_merge_commit_must_be_ancestor_of_main(self) -> None:
        key = f"{PREFIX}/compare/{MERGE}...main"
        for status, behind in (("diverged", 1), ("behind", 1), ("ahead", 1), ("identical", 1)):
            with self.subTest(status=status, behind=behind):
                self.api.routes[key] = {"status": status, "behind_by": behind}
                self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))

    def test_changed_or_protected_branch_never_deleted(self) -> None:
        key = f"{PREFIX}/branches/{mod.branch_path(BRANCH)}"
        for value in (None, {"protected": True, "commit": {"sha": HEAD}},
                      {"protected": False, "commit": {"sha": "c" * 40}}):
            with self.subTest(branch=value):
                self.api.routes[key] = value
                self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))

    def test_open_followup_pr_prevents_cleanup(self) -> None:
        key = next(k for k in self.api.routes if "pulls?state=open" in k)
        self.api.routes[key] = [{"number": 9001}]
        self.assertIsNone(mod.verified_candidate(self.api, PR, HEAD))

    def test_unsafe_branch_rejected(self) -> None:
        for name in ("main", "", "../secrets", "-danger", "a//b", "a/", "/a", "a\\b", "@{bad}"):
            with self.subTest(name=name), self.assertRaises(mod.PruneError):
                mod.branch_path(name)

    def test_changed_between_checks_fails_without_deletion(self) -> None:
        original = self.api.get
        count = [0]
        def flaky(endpoint):
            result = original(endpoint)
            if "/branches/" in endpoint:
                count[0] += 1
                if count[0] == 2:
                    return {"protected": False, "commit": {"sha": "c" * 40}}
            return result
        self.api.get = flaky
        with mock.patch.object(mod, "delete_with_lease") as deleter:
            with self.assertRaises(mod.PruneError):
                mod.prune(self.api, PR, HEAD, apply=True, token="placeholder")
            deleter.assert_not_called()

    def test_git_command_enforces_sha_lease_and_hides_token(self) -> None:
        with mock.patch.object(mod.subprocess, "run") as runner:
            runner.return_value = subprocess.CompletedProcess([], 0)
            mod.delete_with_lease(BRANCH, HEAD, "secret-not-to-log")
            command = runner.call_args.args[0]
            self.assertEqual(command[0:3], ["git", "push", f"--force-with-lease=refs/heads/{BRANCH}:{HEAD}"])
            self.assertEqual(command[-1], f":refs/heads/{BRANCH}")
            self.assertNotIn("secret-not-to-log", str(command))
            header = runner.call_args.kwargs["env"]["GIT_CONFIG_VALUE_0"]
            self.assertTrue(header.startswith("AUTHORIZATION: basic "))
            self.assertEqual(base64.b64decode(header.split()[-1]).decode(), "x-access-token:secret-not-to-log")
            self.assertTrue(runner.call_args.kwargs["capture_output"])

    def test_historical_pinned_head_never_uses_new_branch_state(self) -> None:
        self.assertEqual(mod.HISTORICAL_MERGED_PRS[57], "6be2ff635d2950d124185c3b872bd50768bcce2d")
        self.assertEqual(mod.HISTORICAL_MERGED_PRS[62], "1d055bfc3d66b651273806cadffe5a1d6d36d883")

    def test_git_lease_failure_is_fatal(self) -> None:
        with mock.patch.object(mod.subprocess, "run") as runner:
            runner.return_value = subprocess.CompletedProcess([], 1, stderr="sensitive")
            with self.assertRaisesRegex(mod.PruneError, "lease-protected") as ctx:
                mod.delete_with_lease(BRANCH, HEAD, "placeholder")
            self.assertNotIn("sensitive", str(ctx.exception))
            self.assertIn("without a classified reason", str(ctx.exception))

    def test_requires_specific_repository(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/fork"}):
            with self.assertRaises(SystemExit):
                mod.main(["--historical"])


if __name__ == "__main__":
    unittest.main()
