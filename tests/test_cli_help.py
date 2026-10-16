"""Grouped `--help` index: complete, drift-proof and free of internal issue numbers. Offline."""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import unittest

from reachweaver import cli_help
from reachweaver.cli import make_parser


def subcommands(parser):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action, sorted(action.choices)


class GroupTests(unittest.TestCase):
    def setUp(self):
        self.parser = make_parser()
        self.action, self.names = subcommands(self.parser)

    def test_every_subcommand_is_in_exactly_one_group(self):
        seen: dict[str, str] = {}
        for title, names in cli_help.GROUPS:
            for name in names:
                self.assertNotIn(name, seen, f"{name} is in both {seen.get(name)!r} and {title!r}")
                seen[name] = title
        self.assertEqual(sorted(set(self.names) - set(seen)), [], "add new subcommands to cli_help.GROUPS")
        self.assertEqual(sorted(set(seen) - set(self.names)), [], "stale name in cli_help.GROUPS")

    def test_every_subcommand_has_help_text(self):
        helps = {choice.dest: choice.help for choice in self.action._choices_actions}
        self.assertEqual([n for n in self.names if not helps.get(n)], [])

    def test_top_level_help_lists_every_command_grouped(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            self.parser.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)
        text = out.getvalue()
        for name in self.names:
            self.assertRegex(text, rf"(?m)^  {re.escape(name)}\s", name)
        self.assertLess(text.index("Get started"), text.index("Draft and approve"))
        self.assertNotRegex(text, r"\{init,", "the flat argparse command list must not come back")

    def test_index_lines_are_short_and_without_issue_numbers(self):
        index = cli_help.grouped_index(self.parser)
        for line in index.splitlines():
            self.assertLessEqual(len(line), 110, line)
            self.assertNotRegex(line, r"#\d+|issue \d+", line)

    def test_per_command_help_is_unchanged_in_shape(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.parser.parse_args(["youtube-attach", "--help"])
        self.assertTrue(out.getvalue().startswith("usage: reachweaver youtube-attach"))

    def test_summary_cleaning(self):
        self.assertEqual(cli_help.summary("#22 (owner-approved; disabled until a live receipt): attach one MP4"),
                         "Attach one MP4")
        self.assertEqual(cli_help.summary("Approve the exact current text; binds an immutable hash (#7)"),
                         "Approve the exact current text")
        self.assertEqual(cli_help.summary(None), "")
        self.assertLessEqual(len(cli_help.summary("word " * 80)), cli_help.SUMMARY_WIDTH)

    def test_a_mistyped_command_gets_a_suggestion_not_a_wall_of_names(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            self.parser.parse_args(["aprove"])
        self.assertEqual(raised.exception.code, 2)
        text = err.getvalue()
        self.assertIn("unknown command 'aprove'", text)
        self.assertIn("Did you mean:", text)
        self.assertLess(len(text), 400)

    def test_option_errors_keep_the_standard_argparse_wording(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.parser.parse_args(["youtube-attach", "--privacy", "bogus"])
        self.assertIn("invalid choice", err.getvalue())

    def test_usage_names_the_command_slot_without_polluting_subcommand_usage(self):
        self.assertIn("COMMAND", self.parser.format_usage())
        sub = self.action.choices["list"]
        self.assertTrue(sub.format_usage().startswith("usage: reachweaver list "), sub.format_usage())

    def test_commands_added_by_the_repeat_guard_and_connect_work_are_grouped(self):
        grouped = {name: title for title, names in cli_help.GROUPS for name in names}
        self.assertTrue(grouped["connect"].startswith("Get started"))
        self.assertTrue(grouped["repeat-check"].startswith("Draft and approve"))
        self.assertTrue(grouped["mark-shared"].startswith("Draft and approve"))

    def test_error_hints(self):
        self.assertIn("accounts.example.json", cli_help.error_hint("Missing config/accounts.json. Copy it"))
        self.assertIn("list", cli_help.error_hint("Unknown post id: x"))
        self.assertIsNone(cli_help.error_hint("something unrelated"))


if __name__ == "__main__":
    unittest.main()
