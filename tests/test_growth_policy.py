"""Growth recommendations stay under the existing approval and budget policy (public issue #46).

A recommendation, readout or baseline comparison is advice. It cannot approve, schedule or publish, and the
posts an owner then writes for an experiment's arms go through the same approval, dry-run default, live gate,
per-account caps and emergency stop as any other post. Offline; every network entry point is refused.
"""
from __future__ import annotations

import ast
import contextlib
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
from reachweaver.growth import next_hypotheses
from tests.test_e2e_offline import ACCOUNT_ID, EXAMPLE_ACCOUNTS, FAKE_ENV, LIVE_ENV, MockBluesky, NetworkAttempt

ROOT = Path(__file__).resolve().parents[1]
ADVICE_MODULES = ("growth", "growth_readout", "baseline_eval", "timing_analysis", "experiments", "audience")
# The only storage names advice code may import: read helpers. Nothing that claims, approves, schedules or writes a post.
READ_ONLY_STORAGE = frozenset({"METRIC_FIELDS", "get_post", "parse_utc", "published_posts", "snapshots_for"})
# Dotted prefixes: urllib.parse is pure string handling and allowed; the network-capable modules are not.
FORBIDDEN_MODULES = frozenset({"publishers", "postiz", "bluesky", "threads", "instagram", "facebook", "youtube",
                               "tiktok", "linkedin", "mastodon_media", "media_upload", "ratelimit", "socket",
                               "urllib.request", "urllib.error", "http.client", "http.server", "requests", "ssl"})


def _forbidden(module: str | None) -> bool:
    module = module or ""
    return any(module == bad or module.startswith(bad + ".") for bad in FORBIDDEN_MODULES)
FORBIDDEN_NAMES = frozenset({"claim_sending", "finish_send", "uncertain_send", "transition", "set_paused", "add_post",
                             "reconcile", "urlopen", "_send_http", "request_json"})


def _tree(module: str) -> ast.AST:
    return ast.parse((ROOT / "reachweaver" / f"{module}.py").read_text(encoding="utf-8"))


class AdviceCannotSendTests(unittest.TestCase):
    def test_no_advice_module_imports_a_send_path_or_the_network(self):
        for module in ADVICE_MODULES:
            with self.subTest(module=module):
                for node in ast.walk(_tree(module)):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertFalse(_forbidden(alias.name), alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        # Relative imports name the package module; absolute ones the dotted path.
                        self.assertFalse(_forbidden(node.module), node.module)
                        if node.level and node.module == "storage":
                            self.assertLessEqual({a.name for a in node.names}, READ_ONLY_STORAGE,
                                                 f"{module} imports a non-read storage helper")

    def test_no_advice_module_names_a_claim_approve_schedule_or_send_function(self):
        for module in ADVICE_MODULES:
            with self.subTest(module=module):
                names = {n.id for n in ast.walk(_tree(module)) if isinstance(n, ast.Name)}
                names |= {n.attr for n in ast.walk(_tree(module)) if isinstance(n, ast.Attribute)}
                self.assertEqual(names & FORBIDDEN_NAMES, set())

    def test_the_next_hypothesis_is_advice_that_needs_approval_and_is_not_a_draft(self):
        advice = next_hypotheses([])
        self.assertTrue(advice["requires_human_approval"])
        self.assertIn("not a draft, schedule or permission to publish", advice["note"])
        self.assertEqual(set(advice["next"]), {"dimension", "rule", "reason"})

    def test_experiment_next_cli_prints_the_same_flag_and_opens_no_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp) / "results.json"
            results.write_text("[]", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["experiment-next", "--results", str(results), "--json"])
            self.assertEqual(code, 0, err.getvalue())
            self.assertTrue(json.loads(out.getvalue())["requires_human_approval"])
            self.assertEqual(list(Path(tmp).iterdir()), [results])


class ExperimentArmsStayUnderPolicyTests(unittest.TestCase):
    """Two arms of a hook test are ordinary posts: the same approval, dry run, live gate, cap and pause apply."""

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.dir = Path(work.name)
        self.db = str(self.dir / "state" / "arms.sqlite")
        self.accounts = self.dir / "accounts.json"
        shutil.copyfile(EXAMPLE_ACCOUNTS, self.accounts)
        self.attempts: list[str] = []
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)

        def refuse(name):
            def inner(*args, **kwargs):
                self.attempts.append(name)
                raise NetworkAttempt(name)
            return inner
        for target, name in (("socket.socket.connect", "connect"), ("socket.socket.connect_ex", "connect_ex"),
                             ("socket.create_connection", "create_connection"), ("socket.getaddrinfo", "getaddrinfo")):
            stack.enter_context(patch(target, refuse(name)))
        clean = {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")}
        stack.enter_context(patch.dict(os.environ, clean, clear=True))
        self.mock = MockBluesky()
        stack.enter_context(patch("reachweaver.publishers.request_json", self.mock))

    def cli(self, *argv, live=False):
        env = {**FAKE_ENV, **LIVE_ENV} if live else {}
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue() + err.getvalue()

    def status(self, pid):
        with sqlite3.connect(self.db) as con:
            return con.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()[0]

    def arm(self, text):
        path = self.dir / f"{abs(hash(text))}.txt"
        path.write_text(text, encoding="utf-8")
        code, out = self.cli("draft", "--account", ACCOUNT_ID, "--file", str(path), "--campaign", "hook-test")
        self.assertEqual(code, 0, out)
        return out.split("Created draft ", 1)[1].split()[0]

    def test_unapproved_arms_are_never_sent_even_with_the_live_gate_open(self):
        a, b = self.arm("Arm A: the failure mode first."), self.arm("Arm B: the result first, then the failure mode.")
        self.cli("publish", "--live", live=True)
        self.assertEqual((self.status(a), self.status(b)), ("draft", "draft"))
        self.assertEqual(self.mock.calls, [])

    def test_approved_arms_are_a_dry_run_by_default_and_refused_without_the_opt_in(self):
        a, b = self.arm("Arm A: the failure mode first."), self.arm("Arm B: the result first, then the failure mode.")
        for pid in (a, b):
            self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        code, out = self.cli("publish")
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        code, out = self.cli("publish", "--live")  # --live alone: no environment opt-in, no credentials
        self.assertNotEqual(code, 0, out)
        self.assertEqual((self.status(a), self.status(b)), ("approved", "approved"))
        self.assertEqual(self.mock.calls, [])

    def test_the_account_cap_lets_only_one_arm_through_in_a_sweep_and_pause_stops_the_other(self):
        a, b = self.arm("Arm A: the failure mode first."), self.arm("Arm B: the result first, then the failure mode.")
        for pid in (a, b):
            self.assertEqual(self.cli("approve", pid, "--approver", "owner")[0], 0)
        code, out = self.cli("publish", "--live", "--limit", "10", live=True)
        sent = [pid for pid in (a, b) if self.status(pid) == "published"]
        held = [pid for pid in (a, b) if self.status(pid) == "approved"]
        self.assertEqual((len(sent), len(held)), (1, 1), out)  # the minimum gap per account holds the second arm
        self.assertEqual(self.mock.record_creations, 1)
        self.cli("pause")
        code, out = self.cli("publish", "--live", "--post-id", held[0], live=True)
        self.assertEqual(self.status(held[0]), "approved")
        self.assertEqual(self.mock.record_creations, 1)  # emergency stop: nothing more was sent
        self.assertEqual(self.attempts, [])

    def test_changing_an_arm_after_approval_blocks_it(self):
        a = self.arm("Arm A: the failure mode first.")
        self.assertEqual(self.cli("approve", a, "--approver", "owner")[0], 0)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE posts SET content=content || ' (edited)' WHERE id=?", (a,))
        self.cli("publish", "--live", "--post-id", a, live=True)
        self.assertNotEqual(self.status(a), "published")
        self.assertEqual(self.mock.calls, [])


if __name__ == "__main__":
    unittest.main()
