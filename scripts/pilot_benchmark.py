#!/usr/bin/env python3
"""Offline benchmark-harness skeleton for the field-pilot report (issue #27).

Runs the full ReachWeaver workflow (init, draft, check, approve, publish) for N synthetic posts
against a throwaway database, with the Bluesky HTTP layer replaced by an in-process mock. It
measures only local CPU wall time per workflow step and verifies end states. It never opens a
socket, reads a credential or publishes anything.

This is a SKELETON for the harness, not pilot evidence: the output is labelled ``synthetic`` and
``not_pilot_evidence``. It is NOT an operator-time measurement (no human is involved) and says
nothing about reach, accuracy or any real account. Pilot numbers come only from the owner's
operations log read by ``reachweaver pilot-report`` (docs/FIELD-PILOT-PROTOCOL.md).

Usage:  python3 scripts/pilot_benchmark.py [--posts N] [--json]
Exit codes: 0 all posts reached ``published`` through the mock; 1 otherwise; 2 bad arguments.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reachweaver.cli import main as cli_main  # noqa: E402
from reachweaver.storage import connect, get_post  # noqa: E402

STEPS = ("draft", "check", "approve", "publish")
FAKE_CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"  # docs example shape
FAKE_DID = "did:plc:mockowner000"
LIVE_GATE_ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}


def _prefix(n: int) -> str:
    return f"REACHWEAVER_BENCH_A{n}_BSKY"


def _mock_env(posts: int) -> dict:
    env = dict(LIVE_GATE_ENV)
    for n in range(posts):
        env[f"{_prefix(n)}_IDENTIFIER"] = f"bench{n}.example.invalid"
        env[f"{_prefix(n)}_APP_PASSWORD"] = "mock-not-a-secret"
    return env


class MockBluesky:
    """Stands in for ``publishers.request_json``; records calls, returns a valid createRecord receipt."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url, body=None, *args, **kwargs):
        self.calls.append(url)
        if url.endswith("createSession"):
            # The adapter's identity preflight requires the session handle to equal the configured handle.
            return {"did": FAKE_DID, "handle": (body or {}).get("identifier", ""), "accessJwt": "mock-access"}
        return {"uri": f"at://{FAKE_DID}/app.bsky.feed.post/{body['rkey']}", "cid": FAKE_CID}


def _cli(db: str, accounts: Path, *argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(["--db", db, "--accounts", str(accounts), *argv])
    return code, out.getvalue() + err.getvalue()


def run_benchmark(workdir: Path, posts: int = 3) -> dict:
    """Run the mocked workflow for ``posts`` synthetic posts; return a labelled, self-describing result."""
    if posts < 1:
        raise ValueError("posts must be at least 1")
    accounts = workdir / "accounts.json"
    # One account per post: the built-in per-account cap (2 per 24 h, 60 min gap) is a safety limit
    # and is never loosened here, so a single account could not publish more than two posts.
    accounts.write_text(json.dumps({"accounts": [
        {"id": f"bench-bsky-{n}", "platform": "bluesky", "env_prefix": _prefix(n),
         "handle": f"bench{n}.example.invalid"} for n in range(posts)]}), encoding="utf-8")
    db = str(workdir / "bench.sqlite")
    timings: dict[str, list[float]] = {s: [] for s in STEPS}
    rows: list[dict] = []
    mock = MockBluesky()
    with patch.dict(os.environ, _mock_env(posts)), patch("reachweaver.publishers.request_json", mock):
        code, out = _cli(db, accounts, "init")
        if code != 0:
            raise RuntimeError(f"init failed: {out}")
        for n in range(posts):
            text = workdir / f"post-{n}.txt"
            text.write_text(f"Synthetic benchmark post {n}. https://example.com/bench/{n}\n", encoding="utf-8")
            exit_codes: dict[str, int] = {}
            post_id = ""
            for step in STEPS:
                if step == "draft":
                    argv = ("draft", "--account", f"bench-bsky-{n}", "--file", str(text), "--campaign", "bench")
                elif step == "check":
                    argv = ("check", post_id)
                elif step == "approve":
                    argv = ("approve", post_id, "--approver", "bench-harness")
                else:
                    argv = ("publish", "--live", "--post-id", post_id)
                start = time.perf_counter()
                code, out = _cli(db, accounts, *argv)
                timings[step].append(time.perf_counter() - start)
                exit_codes[step] = code
                if step == "draft":
                    post_id = out.split("Created draft ", 1)[1].split()[0] if "Created draft " in out else ""
                if code != 0 or not post_id:
                    break
            with connect(db) as conn:
                status = get_post(conn, post_id)["status"] if post_id else "missing"
            rows.append({"post_index": n, "exit_codes": exit_codes, "final_status": status})
    return {
        "label": "synthetic offline harness run",
        "synthetic": True,
        "not_pilot_evidence": True,
        "measures": "local CPU wall time per step with a mocked HTTP layer; not operator minutes",
        "posts": posts,
        "published_through_mock": sum(1 for r in rows if r["final_status"] == "published"),
        "mock_http_calls": len(mock.calls),
        "step_seconds": {s: {"n": len(v), "total": round(sum(v), 6),
                             "mean": round(sum(v) / len(v), 6) if v else None} for s, v in timings.items()},
        "rows": rows,
    }


def render(result: dict) -> str:
    lines = [f"{result['label']} (NOT pilot evidence; no account, network or credential involved)",
             f"posts: {result['posts']}, published through mock: {result['published_through_mock']}, "
             f"mock http calls: {result['mock_http_calls']}"]
    for step, s in result["step_seconds"].items():
        lines.append(f"  {step}: n={s['n']} total={s['total']}s mean={s['mean']}s")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--posts", type=int, default=3, help="synthetic posts to run (default 3)")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    if args.posts < 1:
        parser.error("--posts must be at least 1")
    with tempfile.TemporaryDirectory(prefix="rw-bench-") as tmp:
        result = run_benchmark(Path(tmp), args.posts)
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0 if result["published_through_mock"] == args.posts else 1


if __name__ == "__main__":
    sys.exit(main())
