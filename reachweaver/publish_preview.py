"""``publish --json`` (dry run only): the unchanged dry-run preview, captured and returned as JSON.

The dry run itself is not touched: ``capture_dry_run`` runs the very same code path with standard output
redirected into a buffer, then structures the lines it printed. The original lines are kept in ``lines``
so nothing the text preview says can be lost. ``publish --live`` never comes here (the CLI refuses
``--json`` together with ``--live`` before anything runs).
"""
from __future__ import annotations

import contextlib
import io
import re
from typing import Callable

_DUE = re.compile(r"^DRY RUN: (\d+) due approved posts")
_POST = re.compile(r"^  (\S+) (\S+) (\S+): ")
_PROBLEM = re.compile(r"^    (BLOCKED|SKIPPED|HELD)\b|^    .*(--live would (skip|refuse)|CHANGED or unusable)")


def structure(lines: list[str], exit_code: int) -> dict:
    """Due posts with their BLOCKED/SKIPPED/HELD and would-skip lines; every line is also returned verbatim."""
    due = None
    posts: list[dict] = []
    for line in lines:
        found = _DUE.match(line)
        if found:
            due = int(found.group(1))
            continue
        found = _POST.match(line)
        if found:
            posts.append({"post_id": found.group(1), "platform": found.group(2), "account_id": found.group(3),
                          "flagged": False, "problems": []})
            continue
        if posts and _PROBLEM.match(line):
            posts[-1]["problems"].append(line.strip())
            posts[-1]["flagged"] = True
    return {"dry_run": True, "exit_code": exit_code, "due": due, "posts": posts,
            "paused": any(line.startswith("Emergency pause is active") for line in lines), "lines": lines,
            "note": "Dry run: no request was sent. flagged=true means --live would skip, hold or refuse that post "
                    "(see problems); false is not a promise that a live send succeeds."}


def capture_dry_run(run: Callable[[], int]) -> dict:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = run()
    return structure(buffer.getvalue().splitlines(), code)
