"""Refuse a tagged GitHub Release unless the repository owner tagged the current ``main`` commit.

Both jobs of ``.github/workflows/release.yml`` run this before anything is built or published::

    python scripts/release_ref_guard.py

It reads only GitHub's default ``GITHUB_*`` variables and the job's own checkout, which must be
made with ``fetch-depth: 0`` so ``refs/remotes/origin/main`` and the tags exist locally. It needs
no token, makes no network call, writes nothing and contacts no social platform. Every check fails
closed: a missing variable, an unresolvable ref or any mismatch exits 1 and names the reason.

Checks (all must hold):

* the run is a ``push`` of a tag (``GITHUB_EVENT_NAME``, ``GITHUB_REF_TYPE``, ``GITHUB_REF``);
* the tag is exactly ``vMAJOR.MINOR.PATCH`` (no leading zeros, no suffix);
* the actor who pushed, and whoever (re-)ran the workflow, is the repository owner, by login
  and by numeric account id;
* the checked-out commit, the tag, ``GITHUB_SHA`` and ``origin/main`` are the same commit.

It cannot turn on branch protection, prove that the commit was reviewed, or clear rights,
privacy or legal questions: those stay separate owner decisions (docs/RELEASE-PLAYBOOK.md).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping

TAG_RE = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = re.compile(r"[1-9][0-9]*\Z")
MAIN_REF = "refs/remotes/origin/main"


def git_commit(reference: str, cwd: str | None = None) -> str:
    """Resolve a local ref to a commit SHA without a shell or network. Raises on any failure."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", reference + "^{commit}"],
        cwd=cwd, capture_output=True, text=True, timeout=30, check=False,
    )
    sha = result.stdout.strip().lower()
    if result.returncode != 0 or not SHA_RE.fullmatch(sha):
        raise ValueError(f"cannot resolve {reference}")
    return sha


def release_errors(env: Mapping[str, str], resolve: Callable[[str], str] = git_commit) -> list[str]:
    """Every reason this run may not publish a release; an empty list means all checks passed."""
    errors: list[str] = []
    if env.get("GITHUB_EVENT_NAME") != "push":
        errors.append("the run was not triggered by a push")
    if env.get("GITHUB_REF_TYPE") != "tag":
        errors.append("the run was not triggered by a tag")
    tag = env.get("GITHUB_REF_NAME", "")
    if not TAG_RE.fullmatch(tag):
        # Nothing below is safe to resolve with an unvalidated name.
        errors.append("the tag is not exactly vMAJOR.MINOR.PATCH")
        return errors
    if env.get("GITHUB_REF") != f"refs/tags/{tag}":
        errors.append("GITHUB_REF is not refs/tags/<tag>")

    owner = env.get("GITHUB_REPOSITORY_OWNER", "")
    for name in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"):
        login = env.get(name, "")
        # GitHub logins are case-insensitive, as are string comparisons in workflow expressions.
        if not login or not owner or login.casefold() != owner.casefold():
            errors.append(f"{name} is not the repository owner")
    actor_id, owner_id = env.get("GITHUB_ACTOR_ID", ""), env.get("GITHUB_REPOSITORY_OWNER_ID", "")
    if not (ID_RE.fullmatch(actor_id) and ID_RE.fullmatch(owner_id) and actor_id == owner_id):
        errors.append("GITHUB_ACTOR_ID is not the repository owner's account id")

    github_sha = env.get("GITHUB_SHA", "").lower()
    refs = {"checkout": "HEAD", "tag": f"refs/tags/{tag}", "origin/main": MAIN_REF}
    if SHA_RE.fullmatch(github_sha):
        refs["GITHUB_SHA"] = github_sha
    else:
        errors.append("GITHUB_SHA is not a commit SHA")
    commits: dict[str, str] = {}
    for label, reference in refs.items():
        try:
            commit = resolve(reference)
        except (OSError, ValueError, subprocess.SubprocessError):
            errors.append(f"cannot resolve the {label} commit locally (checkout needs fetch-depth: 0)")
            continue
        if not SHA_RE.fullmatch(commit):
            errors.append(f"the {label} commit is not a SHA")
            continue
        commits[label] = commit
    checkout = commits.get("checkout")
    if checkout:
        for label in ("tag", "GITHUB_SHA"):
            if label in commits and commits[label] != checkout:
                errors.append(f"the {label} commit is not the checked-out commit")
        if "origin/main" in commits and commits["origin/main"] != checkout:
            errors.append("the tagged commit is not the current origin/main commit")
    return errors


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Fail closed unless the repository owner pushed a vX.Y.Z tag at the current "
                    "origin/main commit. Reads GITHUB_* variables and the local checkout only.",
    ).parse_args(argv)
    problems = release_errors(os.environ)
    for problem in problems:
        print(f"release-ref: blocked: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"release-ref: OK: {os.environ['GITHUB_REF_NAME']} is the owner's tag at the current origin/main commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
