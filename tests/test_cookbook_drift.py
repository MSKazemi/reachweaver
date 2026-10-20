"""The skill's CLI cookbook stays in step with the real argparse parser (offline).

`references/cli-cookbook.md` promises Claude Code "every command with its key flags". These tests fail when a new
subcommand has no row there, when a row names a command that no longer exists, or when a flag written next to a
command is not accepted by that command, and the failure message says exactly what to add or fix. They also keep
the skill on the `RW` / `python3` command convention (a bare `python` is not on PATH everywhere).
"""
from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from reachweaver.cli import make_parser

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"
COOKBOOK = SKILL / "references/cli-cookbook.md"
COOKBOOK_REL = COOKBOOK.relative_to(ROOT).as_posix()

FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
ROW = re.compile(r"^\|\s*`([a-z][a-z0-9-]*)`\s*\|")
SPAN = re.compile(r"`([^`\n]+)`")
LAUNCHERS = {"RW", "reachweaver", "python3", "-m", "reachweaver.cli", "run.py"}
ENV_ASSIGNMENT = re.compile(r"^[A-Z][A-Z0-9_]*=\S*$")
BARE_PYTHON_MODULE = re.compile(r"(?<![\w./-])python -m reachweaver")
BARE_PYTHON_COMMAND = re.compile(r"(?<![\w./-])python (?=[-<~\w])")


def _options(parser) -> set[str]:
    """A command's flags, including those of nested actions such as `idea add` / `idea promote`."""
    found = {s for a in parser._actions for s in a.option_strings}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for nested in action.choices.values():
                found |= _options(nested)
    return found


def _parser_tables():
    parser = make_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {choice.dest: choice.help or "" for choice in sub._choices_actions}
    flags = {name: _options(sp) for name, sp in sub.choices.items()}
    global_flags = {s for a in parser._actions for s in a.option_strings}
    return flags, global_flags, helps


FLAGS, GLOBAL_FLAGS, HELPS = _parser_tables()
ALL_FLAGS = GLOBAL_FLAGS.union(*FLAGS.values())


def _span_command(text: str) -> str | None:
    """The subcommand a code span or code line is about: `RW draft --x`, `draft --x`, `ENV=1 reachweaver publish`."""
    for token in text.split():
        if token in LAUNCHERS or ENV_ASSIGNMENT.match(token) or token.endswith("/run.py"):
            continue
        return token if token in FLAGS else None
    return None


def _flag_claims(text: str):
    """Yield (line_no, command or None, flag, context) for every flag the cookbook writes.

    A flag belongs to the command its code span or code line starts with; otherwise, in a table row, to the row's
    command; otherwise (prose) to no command, and then it only has to exist somewhere in the CLI.
    """
    in_code = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            code, _, comment = line.partition("#")
            owner = _span_command(code)
            for flag in FLAG.findall(code):
                yield number, owner, flag, line.strip()
            line = comment
        row = ROW.match(line)
        row_command = row.group(1) if row else None
        rest = line
        for span in SPAN.findall(line):
            owner = _span_command(span) or row_command
            for flag in FLAG.findall(span):
                yield number, owner, flag, span
            rest = rest.replace(f"`{span}`", " ", 1)
        for flag in FLAG.findall(rest):
            yield number, row_command, flag, line.strip()[:80]


class CookbookDriftTests(unittest.TestCase):
    def setUp(self):
        self.text = COOKBOOK.read_text(encoding="utf-8")
        self.rows = [m.group(1) for m in map(ROW.match, self.text.splitlines()) if m]

    def test_every_subcommand_has_a_cookbook_row(self):
        missing = [name for name in sorted(FLAGS) if name not in self.rows]
        hints = []
        for name in missing:
            flags = sorted(f for f in FLAGS[name] if f not in {"-h", "--help"})
            hints.append(f"  | `{name}` | {HELPS.get(name, '').strip()} | {', '.join(f'`{f}`' for f in flags) or 'none'} |")
        self.assertEqual(missing, [], f"Add a row for each new subcommand to {COOKBOOK_REL} (under its task heading, "
                                      "trimmed to one line and its key flags; mark owner-only steps **OWNER**), "
                                      "then run python3 scripts/sync_skill.py:\n" + "\n".join(hints))

    def test_every_cookbook_row_is_a_real_subcommand_listed_once(self):
        unknown = sorted(set(self.rows) - set(FLAGS))
        self.assertEqual(unknown, [], f"{COOKBOOK_REL} has rows for commands the CLI no longer has; remove or rename them")
        duplicated = sorted({name for name in self.rows if self.rows.count(name) > 1})
        self.assertEqual(duplicated, [], f"{COOKBOOK_REL} lists these commands in more than one row")

    def test_every_flag_written_next_to_a_command_exists(self):
        wrong = []
        for number, command, flag, context in _flag_claims(self.text):
            known = FLAGS[command] | GLOBAL_FLAGS if command else ALL_FLAGS
            if flag not in known:
                where = f"`{command}`" if command else "any command"
                wrong.append(f"  line {number}: {flag} is not an option of {where} ({context})")
        self.assertEqual(wrong, [], f"Fix these flags in {COOKBOOK_REL} (check `python3 -m reachweaver COMMAND --help`):\n"
                                    + "\n".join(wrong))

    def test_flag_checker_attributes_flags_to_the_right_command(self):
        sample = ("| `draft` | text | `--account ID`, `RW approve POST_ID --allow-repeat` |\n"
                  "```bash\nRW publish --post-id X  # then --live\n```\n"
                  "Then `REACHWEAVER_ENV=prod reachweaver publish --live`; see `RW COMMAND --help`.\n")
        claims = [(command, flag) for _, command, flag, _ in _flag_claims(sample)]
        self.assertEqual(claims, [("draft", "--account"), ("approve", "--allow-repeat"), ("publish", "--post-id"),
                                  (None, "--live"), ("publish", "--live"), (None, "--help")])

    def test_cookbook_uses_the_rw_prefix_and_marks_owner_only_steps(self):
        self.assertIn("`RW` means `python3 <skill-dir>/scripts/run.py`", self.text)
        self.assertNotRegex(self.text, r"python3? -m reachweaver\.cli \w", "write `RW COMMAND`, as SKILL.md does")
        for owner_only in ("`approve`", "`resume`", "`reconcile`", "`reconnect`", "`ui`"):
            row = next(line for line in self.text.splitlines() if line.startswith(f"| {owner_only} |"))
            self.assertIn("**OWNER**", row, owner_only)
        for flag in ("--live", "--approve-plan", "--guide"):
            self.assertRegex(self.text, r"\*\*OWNER\*\* `" + re.escape(flag), flag)
        self.assertIn("**Claude never runs it**", self.text)

    def test_skill_never_tells_claude_to_run_bare_python(self):
        files = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
        found = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if BARE_PYTHON_MODULE.search(line) or BARE_PYTHON_COMMAND.search(line):
                    found.append(f"  {path.relative_to(ROOT).as_posix()}:{number}")
        self.assertEqual(found, [], "Use `RW COMMAND` (or python3) instead of a bare `python`:\n" + "\n".join(found))

    def test_public_docs_do_not_run_the_cli_with_bare_python(self):
        found = []
        for path in sorted((ROOT / "docs").glob("*.md")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if BARE_PYTHON_MODULE.search(line):
                    found.append(f"  {path.relative_to(ROOT).as_posix()}:{number}")
        self.assertEqual(found, [], "Use `reachweaver COMMAND` or `python3 -m reachweaver COMMAND`:\n" + "\n".join(found))


if __name__ == "__main__":
    unittest.main()
