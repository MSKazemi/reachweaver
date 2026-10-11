"""Network-boundary and secret-scan guards. These tests must never contact any host.

1. Every socket entry point is patched to record and refuse connections while all
   ``reachweaver`` modules are imported in a fresh interpreter and while the offline
   CLI paths run against a temporary config and database.
2. ``scripts/secret_scan.py`` must detect a planted fake credential in a throwaway
   git repository and must never echo the matched value.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "secret_scan.py"


class NetworkAttempt(RuntimeError):
    """Raised by the guard; deliberately not an OSError so callers cannot swallow it as a network error."""


class NetworkGuard:
    """Patch socket-level entry points so any connection or DNS lookup is recorded and refused."""

    def __init__(self):
        self.attempts: list[str] = []
        self._stack = contextlib.ExitStack()

    def _refuse(self, name):
        def refuse(*args, **kwargs):
            self.attempts.append(name)
            raise NetworkAttempt(f"network access attempted via {name}")
        return refuse

    def __enter__(self):
        for target, attr in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                             (socket, "create_connection"), (socket, "getaddrinfo"),
                             (socket, "gethostbyname")):
            self._stack.enter_context(patch.object(target, attr, self._refuse(f"{target.__name__}.{attr}")))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _offline_env():
    """Environment with no ReachWeaver credentials, live opt-in or systemd credentials directory."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith("REACHWEAVER_") and k != "CREDENTIALS_DIRECTORY"}


def _git_env():
    """Environment that cannot be redirected to another repository by inherited GIT_* variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


class NetworkGuardSelfTest(unittest.TestCase):
    def test_guard_refuses_and_records_without_connecting(self):
        with NetworkGuard() as guard:
            with self.assertRaises(NetworkAttempt):
                socket.create_connection(("127.0.0.1", 9), timeout=0.01)
            with self.assertRaises(NetworkAttempt):
                socket.getaddrinfo("example.invalid", 443)
            sock = socket.socket()
            try:
                with self.assertRaises(NetworkAttempt):
                    sock.connect(("127.0.0.1", 9))
            finally:
                sock.close()
        self.assertEqual(guard.attempts, ["socket.create_connection", "socket.getaddrinfo", "socket.connect"])


class NoNetworkImportTests(unittest.TestCase):
    def test_importing_every_module_in_fresh_interpreter_opens_no_socket(self):
        # A fresh interpreter is required: other tests have already imported the package here.
        code = textwrap.dedent("""
            import importlib, pkgutil, socket, sys
            attempts = []
            def refuse(name):
                def inner(*a, **k):
                    attempts.append(name)
                    raise RuntimeError("network access attempted via " + name)
                return inner
            socket.socket.connect = refuse("socket.connect")
            socket.socket.connect_ex = refuse("socket.connect_ex")
            socket.create_connection = refuse("create_connection")
            socket.getaddrinfo = refuse("getaddrinfo")
            socket.gethostbyname = refuse("gethostbyname")
            sys.path.insert(0, sys.argv[1])
            import reachweaver
            names = sorted(m.name for m in pkgutil.iter_modules(reachweaver.__path__) if m.name != "__main__")
            for name in names:
                importlib.import_module("reachweaver." + name)
            print(len(names), "modules", ",".join(attempts) or "no-network")
            sys.exit(1 if attempts else 0)
        """)
        result = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], capture_output=True,
                                text=True, env=_offline_env(), timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no-network", result.stdout)
        self.assertGreaterEqual(int(result.stdout.split()[0]), 10)


class NoNetworkCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.db = str(base / "state" / "outbox.sqlite")
        self.config = base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "nn-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_NN_BSKY",
             "handle": "example.bsky.social"},
            {"id": "nn-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_NN_MASTO",
             "server": "https://mastodon.social", "handle": "example"},
            {"id": "nn-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_NN_TG",
             "chat_id": "@examplechannel", "channel_username": "examplechannel", "bot_username": "example_bot"},
        ]}), encoding="utf-8")
        env = patch.dict(os.environ, _offline_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_offline_cli_paths_never_touch_the_network(self):
        examples = ROOT / "examples"
        with NetworkGuard() as guard:
            self.assertEqual(self.cli("init")[0], 0)
            code, out, _ = self.cli("doctor")
            self.assertEqual(code, 0)
            self.assertIn("Live opt-in: disabled", out)
            code, out, _ = self.cli("draft", "--account", "nn-bluesky", "--text", "A verified offline research note")
            self.assertEqual(code, 0)
            post_id = out.split()[2]
            for argv in (("check", post_id), ("show", post_id), ("approve", post_id), ("list",),
                         ("publish",), ("publish", "--scheduled-only"), ("queue-health",), ("report",),
                         ("engagement-fetch",), ("engagement-fetch", "--account", "nn-mastodon"),
                         ("pause",), ("resume",),
                         ("campaign-check", "--file", str(examples / "campaign.reachweaver.json"),
                          "--brand", str(examples / "brand.reachweaver.json"), "--json"),
                         ("distribution-plan", "--region", "india", "--goal", "adoption", "--json")):
                with self.subTest(argv=argv):
                    code, _, err = self.cli(*argv)
                    self.assertEqual(code, 0, err)
            # Refusal paths must fail closed before any socket is opened.
            code, _, err = self.cli("connect-check", "--account", "nn-bluesky")
            self.assertEqual(code, 2)
            self.assertIn("--online", err)
            code, _, err = self.cli("publish", "--live", "--post-id", post_id)
            self.assertEqual(code, 2)
            self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        self.assertEqual(guard.attempts, [])


@unittest.skipUnless(shutil.which("git"), "git is required for the secret-scan tests")
class SecretScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, env=_git_env(),
                       capture_output=True)
        # Built at runtime so this test file itself never contains a token-shaped literal.
        self.github_token = "ghp_" + "Zq7Xv2Lm9Pw4Rt6Ys1Ub3Nc8Kd5Hf0Jg2AeB"
        self.telegram_token = "7" * 9 + ":" + "AAH" + "x9Y8z7W6v5U4t3S2r1Q0p9O8n7M6l5k4"

    def _write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _scan(self):
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True, env=_git_env(),
                       capture_output=True)
        return subprocess.run([sys.executable, str(SCANNER), "--root", str(self.repo)],
                              capture_output=True, text=True, env=_git_env(), timeout=60, check=False)

    def test_detects_planted_tokens_without_printing_values(self):
        self._write("notes/leak.txt", "first line\nsecond line\nGH=" + self.github_token + "\n")
        self._write("bot.py", 'BOT = "' + self.telegram_token + '"\n')
        result = self._scan()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("notes/leak.txt:3:github-token", result.stdout.splitlines())
        self.assertIn("bot.py:1:telegram-bot-token", result.stdout.splitlines())
        combined = result.stdout + result.stderr
        for secret in (self.github_token, self.telegram_token, self.github_token[4:20]):
            self.assertNotIn(secret, combined)

    def test_untracked_files_are_not_scanned(self):
        self._write("clean.txt", "nothing to see\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "clean.txt"], check=True, env=_git_env(),
                       capture_output=True)
        (self.repo / "scratch.txt").write_text(self.github_token, encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCANNER), "--root", str(self.repo)],
                                capture_output=True, text=True, env=_git_env(), timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_allowlist_requires_reason_and_suppresses_only_named_pattern(self):
        self._write("tests/fixture.py", "GH=" + self.github_token + "\nBOT=" + self.telegram_token + "\n")
        self._write(".secret-scan-allow", "tests/fixture.py github-token  # synthetic fixture\n")
        result = self._scan()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines(), ["tests/fixture.py:2:telegram-bot-token"])
        self._write(".secret-scan-allow", "tests/fixture.py github-token\n")
        result = self._scan()
        self.assertEqual(result.returncode, 2)
        self.assertIn("reason", result.stderr)

    def test_example_files_skip_bluesky_shape_but_real_files_do_not(self):
        shaped = "-".join(("ab12", "cd34", "ef56", "gh78"))
        self._write("config/accounts.example.json", '{"note": "' + shaped + '"}\n')
        self._write("config/real.txt", "app pw " + shaped + "\n")
        result = self._scan()
        self.assertEqual(result.stdout.splitlines(), ["config/real.txt:1:bluesky-app-password"])
        self.assertNotIn(shaped, result.stdout + result.stderr)

    @unittest.skipUnless((ROOT / ".git").exists(), "repository checkout required")
    def test_repository_tree_is_clean(self):
        result = subprocess.run([sys.executable, str(SCANNER)], capture_output=True, text=True,
                                env=_git_env(), timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
