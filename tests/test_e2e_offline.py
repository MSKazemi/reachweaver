"""Reproducible offline end-to-end flow: clone files to preview, approve and mock publish (issue #27).

tests/test_onboarding.py already proves that the QUICKSTART commands run from a fresh copy and end
in a dry run. This module goes one step further, still entirely offline: it takes the shipped
example files (config/accounts.example.json, examples/example-post.txt, the example campaign and
brand), walks draft -> show -> check -> approve -> dry-run -> live-gate refusal -> mock send, and
checks the benchmark-harness skeleton (scripts/pilot_benchmark.py) and the pilot-report readout.

Safety properties of this module:
* every socket entry point is patched to refuse, so a real request cannot happen;
* the Bluesky HTTP layer (``reachweaver.publishers.request_json``) is replaced by a recording mock;
* credentials are fake strings injected through a patched environment;
* nothing here loosens, bypasses or reimplements the approval, dry-run, emergency-stop, credential
  isolation or reconciliation rules; the tests only observe them through the public CLI.

A passing run is evidence about the offline code path only. It is not a pilot result and not a
platform receipt.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ACCOUNTS = ROOT / "config" / "accounts.example.json"
EXAMPLE_POST = ROOT / "examples" / "example-post.txt"
EXAMPLE_CAMPAIGN = ROOT / "examples" / "campaign.reachweaver.json"
EXAMPLE_BRAND = ROOT / "examples" / "brand.reachweaver.json"
SYNTHETIC_LOG = ROOT / "tests" / "fixtures" / "pilot" / "synthetic-pilot-log.csv"

ACCOUNT_ID = "my-bluesky"
FAKE_DID = "did:plc:e2emock000"
FAKE_CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"  # docs example shape
FAKE_ENV = {"REACHWEAVER_BSKY_IDENTIFIER": "example.bsky.social",
            "REACHWEAVER_BSKY_APP_PASSWORD": "mock-not-a-secret"}
LIVE_ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NetworkAttempt(RuntimeError):
    """Deliberately not an OSError, so no caller can swallow it as an ordinary network failure."""


class MockBluesky:
    """Recording stand-in for ``publishers.request_json``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url, body=None, *args, **kwargs):
        self.calls.append((url, body))
        if url.endswith("createSession"):
            return {"did": FAKE_DID, "handle": "example.bsky.social", "accessJwt": "mock-access"}
        return {"uri": f"at://{FAKE_DID}/app.bsky.feed.post/{body['rkey']}", "cid": FAKE_CID}

    @property
    def record_creations(self) -> int:
        return sum(1 for url, _ in self.calls if url.endswith("createRecord"))


class OfflineEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.dir = Path(self.work.name)
        self.db = str(self.dir / "state" / "e2e.sqlite")
        self.accounts = self.dir / "accounts.json"
        shutil.copyfile(EXAMPLE_ACCOUNTS, self.accounts)
        self.attempts: list[str] = []

        def refuse(name):
            def inner(*args, **kwargs):
                self.attempts.append(name)
                raise NetworkAttempt(name)
            return inner
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target, name in (("socket.socket.connect", "connect"), ("socket.socket.connect_ex", "connect_ex"),
                             ("socket.create_connection", "create_connection"),
                             ("socket.getaddrinfo", "getaddrinfo"), ("socket.gethostbyname", "gethostbyname")):
            stack.enter_context(patch(target, refuse(name)))
        # Start from an empty environment so no real credential or live opt-in can leak in.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}
        stack.enter_context(patch.dict(os.environ, clean, clear=True))
        self.mock = MockBluesky()
        stack.enter_context(patch("reachweaver.publishers.request_json", self.mock))

    def cli(self, *argv: str, with_db: bool = True) -> tuple[int, str]:
        prefix = ["--db", self.db, "--accounts", str(self.accounts)] if with_db else []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*prefix, *argv])
        return code, out.getvalue() + err.getvalue()

    def status(self, post_id: str) -> str:
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()[0]

    def draft(self) -> str:
        code, out = self.cli("draft", "--account", ACCOUNT_ID, "--file", str(EXAMPLE_POST), "--campaign", "e2e")
        self.assertEqual(code, 0, out)
        self.assertIn("not published", out)
        return out.split("Created draft ", 1)[1].split()[0]

    def test_clone_to_mock_publish(self) -> None:
        # 1. Static checks that need no database.
        code, out = self.cli("campaign-check", "--file", str(EXAMPLE_CAMPAIGN), "--brand", str(EXAMPLE_BRAND),
                             with_db=False)
        self.assertEqual(code, 0, out)
        self.assertIn("VALID", out)
        code, out = self.cli("capabilities", with_db=False)
        self.assertEqual(code, 0, out)

        # 2. Init and draft: a draft is never sent.
        code, out = self.cli("init")
        self.assertEqual(code, 0, out)
        post_id = self.draft()
        self.assertEqual(self.status(post_id), "draft")
        code, out = self.cli("show", post_id)
        self.assertEqual(code, 0, out)
        code, out = self.cli("check", post_id)
        self.assertEqual(code, 0, out)

        # 3. An unapproved post cannot be published, even with the live gate open.
        with patch.dict(os.environ, {**FAKE_ENV, **LIVE_ENV}):
            self.cli("publish", "--live", "--post-id", post_id)
        self.assertEqual(self.status(post_id), "draft")
        self.assertEqual(self.mock.calls, [])

        # 4. Approve, then the default publish is a dry run: no request, status unchanged.
        code, out = self.cli("approve", post_id, "--approver", "e2e-reader")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status(post_id), "approved")
        code, out = self.cli("publish", "--post-id", post_id)
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(self.status(post_id), "approved")
        self.assertEqual(self.mock.calls, [])

        # 5. --live alone is refused without the environment opt-in (and without credentials).
        code, out = self.cli("publish", "--live", "--post-id", post_id)
        self.assertNotEqual(code, 0, out)
        self.assertEqual(self.status(post_id), "approved")
        self.assertEqual(self.mock.calls, [])

        # 6. With the full opt-in, the mock receives exactly one record creation and the post is published.
        with patch.dict(os.environ, {**FAKE_ENV, **LIVE_ENV}):
            code, out = self.cli("publish", "--live", "--post-id", post_id)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status(post_id), "published")
        self.assertEqual(self.mock.record_creations, 1)
        self.assertNotIn("mock-not-a-secret", out)
        with sqlite3.connect(self.db) as conn:
            actions = [r[0] for r in conn.execute(
                "SELECT action FROM event_log WHERE post_id=? ORDER BY id", (post_id,))]
        for expected in ("approve", "published"):
            self.assertIn(expected, actions)

        # 7. Running the same publish again sends nothing more.
        with patch.dict(os.environ, {**FAKE_ENV, **LIVE_ENV}):
            self.cli("publish", "--live", "--post-id", post_id)
        self.assertEqual(self.mock.record_creations, 1)

        # 8. The mock "published" status is not a platform receipt; the run opened no socket.
        self.assertEqual(self.attempts, [])
        code, out = self.cli("queue-health")
        self.assertEqual(code, 0, out)

    def test_edit_after_approval_blocks_send(self) -> None:
        self.cli("init")
        post_id = self.draft()
        code, out = self.cli("approve", post_id, "--approver", "e2e-reader")
        self.assertEqual(code, 0, out)
        with sqlite3.connect(self.db) as conn:  # simulate a change made behind the CLI's back
            conn.execute("UPDATE posts SET content=content || ' tampered' WHERE id=?", (post_id,))
        with patch.dict(os.environ, {**FAKE_ENV, **LIVE_ENV}):
            self.cli("publish", "--live", "--post-id", post_id)
        self.assertNotEqual(self.status(post_id), "published")
        self.assertEqual(self.mock.record_creations, 0)
        self.assertEqual(self.attempts, [])


class BenchmarkHarnessSkeleton(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bench = _load_script("pilot_benchmark")

    def test_harness_runs_offline_and_labels_itself_synthetic(self) -> None:
        attempts: list[str] = []

        def refuse(*args, **kwargs):
            attempts.append("network")
            raise NetworkAttempt("network")
        with tempfile.TemporaryDirectory() as tmp, \
                patch("socket.socket.connect", refuse), patch("socket.create_connection", refuse), \
                patch("socket.getaddrinfo", refuse):
            result = self.bench.run_benchmark(Path(tmp), posts=3)
        self.assertEqual(attempts, [])
        self.assertTrue(result["synthetic"])
        self.assertTrue(result["not_pilot_evidence"])
        self.assertEqual(result["posts"], 3)
        self.assertEqual(result["published_through_mock"], 3)
        self.assertEqual(set(result["step_seconds"]), set(self.bench.STEPS))
        for step in result["step_seconds"].values():
            self.assertEqual(step["n"], 3)
        # The harness must not emit any claim-shaped field.
        for forbidden in ("lift", "reach", "winner", "significance", "operator_minutes"):
            self.assertNotIn(forbidden, json.dumps(sorted(result)))

    def test_harness_cli_exit_codes(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.bench.main(["--posts", "1"]), 0)
        self.assertIn("NOT pilot evidence", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.bench.main(["--posts", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_synthetic_pilot_log_readout_stays_descriptive(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["pilot-report", "--log", str(SYNTHETIC_LOG), "--json"])
        self.assertIn(code, (0, 1), out.getvalue())
        report = json.loads(out.getvalue())
        self.assertIn(report["comparison"]["status"], ("refused", "descriptive"))

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)
        # Field names only: explanatory prose may legitimately say "no significance test".
        for forbidden in ("winner", "significance", "lift", "p_value"):
            self.assertNotIn(forbidden, set(keys(report)))

    def test_protocol_documents_the_harness_without_claiming_results(self) -> None:
        doc = (ROOT / "docs" / "FIELD-PILOT-PROTOCOL.md").read_text(encoding="utf-8")
        self.assertIn("pilot_benchmark.py", doc)
        self.assertIn("No pilot has been run", doc)
        self.assertTrue(socket.socket)  # socket import is used by the guards above


if __name__ == "__main__":
    unittest.main()
