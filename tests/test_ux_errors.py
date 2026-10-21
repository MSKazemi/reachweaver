"""Actionable command-line errors: every message names the fix (offline, synthetic data).

Covers the first-run failures a UX audit of v0.15.0 hit by running the CLI: a missing accounts file, a mistyped
flag, command or choice, a missing input file, and ``--version``. Nothing here opens a network connection.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.cli_help import file_error_message
from reachweaver.cli_parser import COMMAND_ALIASES, make_parser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_no_network import NetworkGuard  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, contextlib.chdir(self.root), patch.dict(os.environ, {}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:  # argparse errors exit from inside parse_args
                code = exc.code
            self.assertEqual(guard.attempts, [])
        return code, out.getvalue(), err.getvalue()


class MistypedInput(Base):
    def test_unknown_flag_suggests_the_close_one_without_the_usage_block(self):
        code, _, err = self.run_cli("share", "--text", "hi", "--platfrom", "bluesky")
        self.assertEqual(code, 2)
        self.assertNotIn("usage:", err)  # only the error and its hint (UX re-measure 2026-10-21)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("reachweaver share: error: unrecognized arguments: --platfrom bluesky. Did you mean --platform?",
                      err)
        self.assertIn("'reachweaver share --help' lists the options", err)

    def test_a_global_option_after_the_command_says_where_it_goes(self):
        code, _, err = self.run_cli("share", "--text", "hi", "--db", "x.sqlite")
        self.assertEqual(code, 2)
        self.assertIn("--db is a global option: put it before the command", err)

    def test_an_unknown_flag_with_no_close_match_still_points_at_help(self):
        code, _, err = self.run_cli("list", "--zzzzzz")
        self.assertEqual(code, 2)
        self.assertNotIn("Did you mean", err)
        self.assertIn("'reachweaver list --help' lists the options", err)

    def test_an_invalid_choice_suggests_the_close_one(self):
        code, _, err = self.run_cli("account-add", "blusky")
        self.assertEqual(code, 2)
        self.assertIn("invalid choice: 'blusky'", err)
        self.assertIn("Did you mean bluesky?", err)

    def test_command_aliases_suggest_existing_commands_only(self):
        commands = set(make_parser()._subparsers._group_actions[0].choices)  # type: ignore[union-attr]
        for word, targets in COMMAND_ALIASES.items():
            self.assertTrue(set(targets) <= commands, (word, targets))
            self.assertNotIn(word, commands, f"{word} is a real command now; drop the alias")
        self.assertNotIn("next", COMMAND_ALIASES)
        for word, expected in (("post", "share, draft"), ("write", "share, draft"), ("compose", "share, draft"),
                               ("undo", "cancel, unapprove"), ("results", "report"), ("stats", "report")):
            code, _, err = self.run_cli(word)
            self.assertEqual(code, 2)
            self.assertIn(f"unknown command '{word}'. Did you mean: {expected}", err)

    def test_version_flag_prints_what_the_version_command_prints(self):
        code, flag_out, _ = self.run_cli("--version")
        self.assertEqual(code, 0)
        code, command_out, _ = self.run_cli("version")
        self.assertEqual(code, 0)
        self.assertEqual(flag_out, command_out)
        self.assertTrue(flag_out.startswith("reachweaver "))


class MissingFiles(Base):
    def test_missing_caption_file_names_the_folder_and_the_alternative(self):
        code, _, err = self.run_cli("share", "--platform", "bluesky", "--file", "nope.txt")
        self.assertEqual(code, 2)
        self.assertIn(f"ERROR: File not found: nope.txt (looked in {self.root.resolve()})", err)
        self.assertIn('or use --text "..." instead', err)
        self.assertNotIn("Errno", err)

    def test_json_error_carries_the_same_plain_message(self):
        code, out, err = self.run_cli("media-check", "absent.json", "--json")
        self.assertEqual(code, 2)
        document = json.loads(out)
        self.assertEqual(document["error"]["code"], "not_found")
        self.assertIn("File not found: absent.json", document["error"]["message"])
        self.assertNotIn("--text", document["error"]["message"], "only commands with --text suggest it")

    def test_folder_and_permission_errors_are_plain(self):
        self.assertIn("is a folder, not a file", file_error_message(IsADirectoryError(21, "x", "media"), None))
        self.assertIn("Permission denied: secret.txt", file_error_message(PermissionError(13, "x", "secret.txt"), None))
        self.assertIsNone(file_error_message(OSError("no filename"), None))
        self.assertIsNone(file_error_message(ValueError("not an OS error"), None))


class MissingAccounts(Base):
    def test_missing_accounts_file_says_what_to_type(self):
        code, _, err = self.run_cli("doctor")
        self.assertEqual(code, 2)
        self.assertIn("no accounts yet. Add one: reachweaver account-add bluesky (public details only), "
                      "or set up a folder: reachweaver workspace --init .", err)
        self.assertNotIn("accounts.example.json", err)
        (self.root / "config").mkdir()
        (self.root / "config" / "accounts.example.json").write_text('{"accounts": []}', encoding="utf-8")
        code, _, err = self.run_cli("doctor")
        self.assertIn("Or copy config/accounts.example.json to config/accounts.json", err)

    def test_start_still_recognises_the_missing_file(self):
        from reachweaver import start
        with contextlib.chdir(self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(start.build_guide(None, None)["state"], "no_accounts_file")


if __name__ == "__main__":
    unittest.main()
