"""Delete only verified, already-merged GitHub PR branches, with a SHA lease.

This is maintainer automation, not part of the ReachWeaver package. Every
candidate is validated using live GitHub metadata before a remote ref is changed.
The expected SHA is also enforced *atomically* by git's --force-with-lease.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

REPOSITORY = "MSKazemi/reachweaver"
DEFAULT_BRANCH = "main"
HISTORICAL_MERGED_PRS = (57, 58, 59)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BRANCH_RE = re.compile(r"[A-Za-z0-9_.\-/]+\Z")


class PruneError(RuntimeError):
    """Failed to verify a candidate or delete it safely."""


class GitHubAPI:
    def __init__(self, token: str) -> None:
        if not token:
            raise PruneError("GITHUB_TOKEN must be provided")
        self._token = token

    def get(self, endpoint: str) -> Any:
        if not endpoint.startswith(f"/repos/{REPOSITORY}/"):
            raise PruneError("unapproved API path")
        request = urllib.request.Request(
            "https://api.github.com" + endpoint,
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "reachweaver-branch-prune",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise PruneError(f"GitHub API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise PruneError("GitHub API verification failed") from exc


def branch_path(name: str) -> str:
    """Encode the full branch name as one REST path parameter."""
    if (not name or not BRANCH_RE.fullmatch(name) or name == DEFAULT_BRANCH
            or name.startswith("-") or name.startswith("/") or name.endswith("/")
            or ".." in name or "//" in name or "@{" in name):
        raise PruneError("unsafe branch name")
    return urllib.parse.quote(name, safe="")


def verified_candidate(api: GitHubAPI, pr_number: int) -> tuple[str, str] | None:
    """Return (name, exact SHA) only when PR history and remote ref agree."""
    if pr_number <= 0:
        raise PruneError("invalid PR number")
    pr = api.get(f"/repos/{REPOSITORY}/pulls/{pr_number}")
    if not isinstance(pr, dict):
        raise PruneError(f"PR #{pr_number} cannot be inspected")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    source_repo = head.get("repo") or {}
    if not (pr.get("merged") is True and pr.get("merged_at")
            and pr.get("state") == "closed" and base.get("ref") == DEFAULT_BRANCH
            and source_repo.get("full_name") == REPOSITORY):
        return None
    name, sha, merge_sha = head.get("ref"), head.get("sha"), pr.get("merge_commit_sha")
    if not isinstance(name, str):
        return None
    encoded = branch_path(name)
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        raise PruneError("invalid head SHA")
    if not isinstance(merge_sha, str) or not SHA_RE.fullmatch(merge_sha):
        raise PruneError("missing merge commit SHA")

    # A merge may have been reverted or main rewritten. Do not delete a
    # potential recovery branch unless its merge commit is still on main.
    comparison = api.get(f"/repos/{REPOSITORY}/compare/{merge_sha}...{DEFAULT_BRANCH}")
    if not isinstance(comparison, dict) or comparison.get("status") not in ("ahead", "identical"):
        return None
    if comparison.get("behind_by") != 0:
        return None

    branch = api.get(f"/repos/{REPOSITORY}/branches/{encoded}")
    if branch is None:  # Already deleted by GitHub / a simultaneous cleanup run.
        return None
    if not isinstance(branch, dict) or branch.get("protected") is not False:
        return None
    if (branch.get("commit") or {}).get("sha") != sha:
        return None  # New commits were pushed since merge.

    query = urllib.parse.urlencode({"state": "open", "head": f"MSKazemi:{name}", "per_page": "100"})
    open_prs = api.get(f"/repos/{REPOSITORY}/pulls?{query}")
    if not isinstance(open_prs, list):
        raise PruneError("unable to verify currently open PRs")
    if open_prs:
        return None
    return name, sha


def delete_with_lease(name: str, expected_sha: str, token: str) -> None:
    """Delete only if the server still has exactly the vetted commit SHA."""
    # The token is supplied through git's environment config and not exposed
    # in command arguments, remote URL, command output, or checkout config.
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: bearer " + token,
        "GIT_TERMINAL_PROMPT": "0",
    })
    remote_ref = f"refs/heads/{name}"
    command = ["git", "push", f"--force-with-lease={remote_ref}:{expected_sha}",
               "origin", f":{remote_ref}"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Deliberately do not echo stderr or anything containing credentials.
        raise PruneError("lease-protected remote branch deletion failed")


def prune(api: GitHubAPI, pr_number: int, *, apply: bool, token: str) -> str:
    candidate = verified_candidate(api, pr_number)
    if candidate is None:
        return f"PR #{pr_number}: kept (unmerged, changed, protected, in use or absent)"
    name, sha = candidate
    if not apply:
        return f"PR #{pr_number}: eligible (dry run): {name} @ {sha}"
    # Recheck the ref immediately before an atomic, lease-protected delete.
    fresh = verified_candidate(api, pr_number)
    if fresh != candidate:
        raise PruneError(f"PR #{pr_number}: branch changed during verification")
    delete_with_lease(name, sha, token)
    return f"PR #{pr_number}: removed verified merged branch {name} @ {sha}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, action="append", default=[], help="one merged PR number")
    parser.add_argument("--historical", action="store_true", help="review the known merged PRs #57-#59")
    parser.add_argument("--apply", action="store_true", help="delete eligible remote branches with SHA lease")
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        parser.error("refusing to operate outside the designated public repository")
    numbers = sorted(set(args.pr + (list(HISTORICAL_MERGED_PRS) if args.historical else [])))
    if not numbers:
        parser.error("choose --pr NUMBER or --historical")
    try:
        token = os.environ.get("GITHUB_TOKEN", "")
        api = GitHubAPI(token)
        for number in numbers:
            print(prune(api, number, apply=args.apply, token=token))
    except PruneError as exc:
        print(f"Branch cleanup blocked: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
