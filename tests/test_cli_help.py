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

    def _help(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            make_parser().parse_args(argv)
        self.assertEqual(raised.exception.code, 0)
        return out.getvalue()

    def test_help_all_lists_every_command_grouped(self):
        text = self._help(["--help", "all"])
        for name in self.names:
            self.assertRegex(text, rf"(?m)^  {re.escape(name)}\s", name)
        self.assertLess(text.index("Get started"), text.index("Draft and approve"))
        self.assertNotRegex(text, r"\{init,", "the flat argparse command list must not come back")
        self.assertIn("--home DIR", text)  # the global options stay in the full view
        self.assertEqual(text, self._help(["-h", "all"]))
        self.assertEqual(text, self._help(["--help=all"]))
        self.assertEqual(text, cli_help.full_help(make_parser()))


class EverydayHelpTests(unittest.TestCase):
    def setUp(self):
        self.parser = make_parser()
        self.action, self.names = subcommands(self.parser)

    def _help(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            make_parser().parse_args(argv)
        self.assertEqual(raised.exception.code, 0)
        return out.getvalue()

    def _main(self, argv):
        from reachweaver.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_everyday_names_are_real_grouped_and_unique(self):
        names = [entry[0] for entry in cli_help.EVERYDAY]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(sorted(set(names) - set(self.names)), [], "EVERYDAY names a command that does not exist")
        grouped = {name for _, group in cli_help.GROUPS for name in group}
        self.assertTrue(set(names) <= grouped)
        self.assertTrue(12 <= len(names) <= 16, len(names))
        self.assertEqual(cli_help.EVERYDAY_NAMES, frozenset(names))

    def test_plain_help_is_the_short_everyday_view(self):
        text = self._help(["--help"])
        self.assertEqual(text, self._help(["-h"]))
        rows = re.findall(r"(?m)^  ([a-z][a-z-]*)  +\S", text)
        self.assertEqual(rows, [entry[0] for entry in cli_help.EVERYDAY])
        self.assertIn("share --web", text)
        self.assertIn(f"to see all {len(self.names)} commands by group", text)
        self.assertIn("`reachweaver --help all` (or `reachweaver help all`)", text)
        self.assertLess(len(text.splitlines()), 40)
        for line in text.splitlines():
            self.assertLessEqual(len(line), 100, line)
            self.assertNotRegex(line, r"#\d+|issue \d+", line)
        for name in ("approve", "approval-mode", "reconcile", "context", "mcp-serve"):
            self.assertNotRegex(text, rf"(?m)^  {re.escape(name)}\s", name)

    def test_everyday_lines_never_promise_a_send(self):
        lines = {entry[0]: " ".join(entry[1:]) for entry in cli_help.EVERYDAY}
        self.assertIn("dry run", lines["publish"])
        self.assertIn("you only", lines["review"])
        self.assertIn("nothing is sent", lines["draft"])
        self.assertIn("nothing goes online", lines["share"])

    def test_help_command_matches_the_help_flag(self):
        code, out, _ = self._main(["help"])
        self.assertEqual((code, out), (0, self._help(["--help"])))
        code, out, _ = self._main(["help", "all"])
        self.assertEqual((code, out), (0, self._help(["--help", "all"])))

    def test_help_for_one_command_is_that_commands_own_help(self):
        own = self._help(["list", "--help"])
        self.assertTrue(own.startswith("usage: reachweaver list"))
        self.assertEqual(self._help(["--help", "list"]), own)
        code, out, _ = self._main(["help", "list"])
        self.assertEqual((code, out), (0, own))

    def test_unknown_help_topic_is_a_short_error_with_a_suggestion(self):
        code, out, err = self._main(["help", "lst"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unknown help topic 'lst'", err)
        self.assertIn("Did you mean: list?", err)
        err_io = io.StringIO()
        with contextlib.redirect_stderr(err_io), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as raised:
            make_parser().parse_args(["--help", "lst"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Did you mean: list?", err_io.getvalue())
        self.assertLess(len(err_io.getvalue()), 400)

    def test_every_command_stays_callable(self):
        for name in self.names:
            with self.subTest(command=name):
                self.assertIn(name, self.action.choices)
                self.assertTrue(self.action.choices[name].format_help().startswith(f"usage: reachweaver {name}"))

    def test_describe_marks_everyday_commands_and_still_lists_all(self):
        import json
        code, out, _ = self._main(["describe", "--json"])
        self.assertEqual(code, 0)
        commands = json.loads(out)["commands"]
        self.assertEqual(sorted(c["name"] for c in commands), self.names)
        self.assertTrue(all(isinstance(c["everyday"], bool) for c in commands))
        self.assertEqual({c["name"] for c in commands if c["everyday"]}, set(cli_help.EVERYDAY_NAMES))
        code, out, _ = self._main(["describe", "help", "--json"])
        entry = json.loads(out)["command"]
        self.assertEqual((entry["everyday"], entry["tags"]), (True, ["read_only"]))

    def test_help_flag_is_not_a_value_flag_for_completion_or_a_global_option(self):
        from reachweaver import catalog, completion
        script = completion.render("bash", make_parser())
        self.assertNotRegex(script, r"\|--help\)|'--help'\)|--help\|", "completion must not treat --help as taking a value")
        names = [flag["name"] for flag in catalog.describe(make_parser())["global_options"]]
        self.assertNotIn("--help", names)

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

    def test_bare_command_prints_the_start_guide_and_creates_nothing(self):
        import tempfile
        from pathlib import Path
        from reachweaver.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "x.sqlite"
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["--db", str(db), "--accounts", str(Path(tmp) / "none.json")])
            self.assertEqual(code, 0)
            self.assertIn("reachweaver --help", out.getvalue())
            self.assertIn("only reads local state", out.getvalue())
            self.assertFalse(db.parent.exists())

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
