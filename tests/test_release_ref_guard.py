"""scripts/release_ref_guard.py against real temporary local Git repositories (private #50, #51).

Each test builds a bare ``origin`` repository, pushes ``main`` and tags to it, then clones it the
way ``actions/checkout`` with ``fetch-depth: 0`` leaves a tag build: every branch under
``refs/remotes/origin/`` and the tag checked out as a detached HEAD. Only the ``file`` transport on
local paths is used (no network), with the user's git config and hooks isolated, and the guard is
also run as a subprocess, exactly as the workflow runs it, with synthetic ``GITHUB_*`` variables.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_ref_guard.py"
spec = importlib.util.spec_from_file_location("release_ref_guard", SCRIPT)
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

OWNER, OWNER_ID = "repo-owner", "1001"
TAG = "v1.2.3"


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ReleaseRefGuardGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="rw-release-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        empty_config = self.tmp / "gitconfig"
        empty_config.write_text("", encoding="utf-8")
        # Isolate from the developer's and the CI runner's git config, hooks and GITHUB_* values.
        self.base_env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "GIT_"))}
        self.base_env.update({
            "GIT_CONFIG_GLOBAL": str(empty_config), "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.tmp),
            "GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_COMMITTER_NAME": "Test Author", "GIT_COMMITTER_EMAIL": "author@example.invalid",
        })
        self.origin, self.work, self.runner = self.tmp / "origin.git", self.tmp / "work", self.tmp / "runner"
        self.git(self.tmp, "init", "--quiet", "--bare", "--initial-branch=main", str(self.origin))
        self.git(self.tmp, "init", "--quiet", "--initial-branch=main", str(self.work))
        self.git(self.work, "remote", "add", "origin", str(self.origin))
        self.first = self.commit("first")
        self.git(self.work, "push", "--quiet", "origin", "main")

    # -- helpers ------------------------------------------------------------------------------
    def git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false", *args],
            cwd=cwd, env=self.base_env, capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        (self.work / "file.txt").write_text(message + "\n", encoding="utf-8")
        self.git(self.work, "add", "file.txt")
        self.git(self.work, "commit", "--quiet", "-m", message)
        return self.git(self.work, "rev-parse", "HEAD")

    def tag(self, name: str = TAG, *, annotated: bool = False) -> None:
        if annotated:
            self.git(self.work, "tag", "-a", "-m", name, name)
        else:
            self.git(self.work, "tag", name)
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/" + name)

    def checkout_tag(self, name: str = TAG) -> None:
        """What actions/checkout leaves for a tag push with fetch-depth: 0."""
        if not self.runner.exists():
            self.git(self.tmp, "clone", "--quiet", "--no-checkout", str(self.origin), str(self.runner))
        self.git(self.runner, "fetch", "--quiet", "--tags", "origin")
        self.git(self.runner, "checkout", "--quiet", "--detach", "refs/tags/" + name)

    def env(self, **overrides: str) -> dict[str, str]:
        tagged = self.git(self.runner, "rev-parse", f"refs/tags/{TAG}^{{commit}}") if self.runner.exists() else ""
        values = {
            "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": TAG,
            "GITHUB_REF": f"refs/tags/{TAG}", "GITHUB_SHA": tagged,
            "GITHUB_ACTOR": OWNER, "GITHUB_TRIGGERING_ACTOR": OWNER, "GITHUB_REPOSITORY_OWNER": OWNER,
            "GITHUB_ACTOR_ID": OWNER_ID, "GITHUB_REPOSITORY_OWNER_ID": OWNER_ID,
        }
        values.update(overrides)
        return values

    def errors(self, env: dict[str, str]) -> list[str]:
        return guard.release_errors(env, lambda ref: guard.git_commit(ref, cwd=str(self.runner)))

    def run_script(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT)], cwd=self.runner, env={**self.base_env, **env},
                              capture_output=True, text=True, timeout=60, check=False)

    def assertBlocked(self, env: dict[str, str], reason: str) -> None:
        problems = self.errors(env)
        self.assertTrue(any(reason in p for p in problems), problems)
        result = self.run_script(env)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("release-ref: blocked: ", result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertNotIn("OK", result.stdout)

    # -- accepted -----------------------------------------------------------------------------
    def test_owner_tag_at_current_main_is_accepted(self) -> None:
        self.tag()
        self.checkout_tag()
        self.assertEqual(self.errors(self.env()), [])
        result = self.run_script(self.env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"release-ref: OK: {TAG}", result.stdout)

    def test_annotated_owner_tag_and_case_insensitive_login_are_accepted(self) -> None:
        self.tag(annotated=True)
        self.checkout_tag()
        tag_object = self.git(self.runner, "rev-parse", f"refs/tags/{TAG}")
        self.assertNotEqual(tag_object, self.first)  # really an annotated tag object
        env = self.env(GITHUB_ACTOR=OWNER.upper(), GITHUB_SHA=tag_object)
        self.assertEqual(self.errors(env), [])
        self.assertEqual(self.run_script(env).returncode, 0)

    # -- refused: who -------------------------------------------------------------------------
    def test_a_different_actor_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        self.assertBlocked(self.env(GITHUB_ACTOR="someone-else", GITHUB_ACTOR_ID="2002"),
                           "GITHUB_ACTOR is not the repository owner")

    def test_a_rerun_by_someone_else_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        self.assertBlocked(self.env(GITHUB_TRIGGERING_ACTOR="someone-else"),
                           "GITHUB_TRIGGERING_ACTOR is not the repository owner")

    def test_a_matching_login_with_another_account_id_or_no_id_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        for actor_id in ("2002", "", "1001 "):
            with self.subTest(actor_id=actor_id):
                self.assertBlocked(self.env(GITHUB_ACTOR_ID=actor_id), "GITHUB_ACTOR_ID")

    def test_missing_owner_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        self.assertBlocked(self.env(GITHUB_REPOSITORY_OWNER=""), "GITHUB_ACTOR is not the repository owner")

    # -- refused: what ------------------------------------------------------------------------
    def test_a_tag_that_is_no_longer_the_current_main_commit_is_refused(self) -> None:
        self.tag()
        self.commit("second")  # main moves on after the tag was pushed
        self.git(self.work, "push", "--quiet", "origin", "main")
        self.checkout_tag()
        self.assertBlocked(self.env(), "not the current origin/main commit")

    def test_a_tag_on_an_older_commit_is_refused(self) -> None:
        self.commit("second")
        self.git(self.work, "push", "--quiet", "origin", "main")
        self.git(self.work, "tag", TAG, self.first)
        self.git(self.work, "push", "--quiet", "origin", "refs/tags/" + TAG)
        self.checkout_tag()
        self.assertBlocked(self.env(), "not the current origin/main commit")

    def test_a_checkout_other_than_the_tag_or_github_sha_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        second = self.commit("second")
        self.git(self.work, "push", "--quiet", "origin", "main")
        self.git(self.runner, "fetch", "--quiet", "origin")
        self.git(self.runner, "checkout", "--quiet", "--detach", "refs/remotes/origin/main")
        self.assertBlocked(self.env(GITHUB_SHA=self.first), "the tag commit is not the checked-out commit")
        self.assertBlocked(self.env(GITHUB_SHA=self.first), "the GITHUB_SHA commit is not the checked-out commit")
        self.assertEqual(self.git(self.runner, "rev-parse", "HEAD"), second)

    def test_non_semver_tags_are_refused_before_any_ref_is_resolved(self) -> None:
        self.tag()
        self.checkout_tag()
        for name in ("v1.2", "1.2.3", "v1.2.3-rc1", "v01.2.3", "v1.2.3\n", "v1.2.3;x", "--help", ""):
            with self.subTest(tag=name):
                env = self.env(GITHUB_REF_NAME=name, GITHUB_REF=f"refs/tags/{name}")
                resolved: list[str] = []
                problems = guard.release_errors(env, lambda ref: resolved.append(ref) or "a" * 40)
                self.assertIn("the tag is not exactly vMAJOR.MINOR.PATCH", problems)
                self.assertEqual(resolved, [])
        self.assertBlocked(self.env(GITHUB_REF_NAME="v1.2.3-rc1", GITHUB_REF="refs/tags/v1.2.3-rc1"),
                           "vMAJOR.MINOR.PATCH")

    def test_a_branch_ref_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        self.assertBlocked(self.env(GITHUB_REF_TYPE="branch", GITHUB_REF=f"refs/heads/{TAG}"),
                           "not triggered by a tag")
        self.assertBlocked(self.env(GITHUB_REF=f"refs/heads/{TAG}"), "GITHUB_REF is not refs/tags/<tag>")

    def test_a_non_push_event_is_refused(self) -> None:
        self.tag()
        self.checkout_tag()
        for event in ("workflow_dispatch", "pull_request", ""):
            with self.subTest(event=event):
                self.assertBlocked(self.env(GITHUB_EVENT_NAME=event), "not triggered by a push")

    def test_a_shallow_checkout_without_origin_main_fails_closed(self) -> None:
        self.tag()
        self.checkout_tag()
        self.git(self.runner, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertBlocked(self.env(), "cannot resolve the origin/main commit locally")

    def test_a_missing_or_malformed_github_sha_fails_closed(self) -> None:
        self.tag()
        self.checkout_tag()
        for value in ("", "HEAD", "z" * 40):
            with self.subTest(sha=value):
                self.assertBlocked(self.env(GITHUB_SHA=value), "GITHUB_SHA is not a commit SHA")

    def test_outside_a_git_repository_everything_fails_closed(self) -> None:
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        # The ceiling stops git from finding a repository that happens to enclose the temp directory.
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)}):
            problems = guard.release_errors(self.env(GITHUB_SHA="a" * 40),
                                            lambda ref: guard.git_commit(ref, cwd=str(outside)))
        self.assertEqual(sum("cannot resolve" in p for p in problems), 4, problems)


class ReleaseRefGuardStaticTests(unittest.TestCase):
    def test_the_guard_is_stdlib_only_and_never_reaches_the_network_or_a_token(self):
        source = SCRIPT.read_text(encoding="utf-8")
        imports = {line.split()[1].split(".")[0] for line in source.splitlines()
                   if line.startswith(("import ", "from "))}
        self.assertEqual(imports, {"__future__", "argparse", "os", "re", "subprocess", "sys", "collections"})
        for forbidden in ("urllib", "http.client", "socket", "TOKEN", "shell=True"):
            self.assertNotIn(forbidden, source)
        # The one subprocess it starts is a local, read-only `git rev-parse`.
        self.assertEqual(source.count("subprocess.run("), 1)
        self.assertIn('["git", "rev-parse", "--verify", "--quiet"', source)


if __name__ == "__main__":
    unittest.main()
