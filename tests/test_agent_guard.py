"""The plugin's PreToolUse agent guard (reachweaver/agent_guard.py) and argparse abbreviations.

Offline; nothing here runs a ReachWeaver command: the guard only reads command text.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from reachweaver import agent_guard
from reachweaver.cli import make_parser

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugin/hooks/hooks.json"
PLUGIN_GUARD = ROOT / "plugin/skills/reachweaver/scripts/reachweaver/agent_guard.py"
SETTINGS = ROOT / ".claude/settings.json"

# (command, refused?) -- the table the guard must satisfy.
CASES = [
    # Owner-only actions in every invocation form.
    ("reachweaver approve 12", True),
    ("reachweaver --db state/x.sqlite approve 12", True),
    ("reachweaver --db=state/x.sqlite --plain approve 12 --approver me", True),
    ("python3 -m reachweaver approve 12", True),
    ("python3 -m reachweaver.cli approve 12", True),
    ("python3 -I -mreachweaver.cli approve 12", True),
    ("python -m reachweaver.__main__ approve 12", True),
    ("python3 reachweaver/__main__.py approve 12", True),
    ("python3 ~/.claude/skills/reachweaver/scripts/run.py approve 12", True),
    ('python3 "/opt/plugins/reachweaver/skills/reachweaver/scripts/run.py" approve 12', True),
    ("uvx reachweaver approve 12", True),
    ("pipx run reachweaver resume", True),
    ("reachweaver resume", True),
    ("reachweaver reconcile 12 --published --remote-id abc", True),
    ("reachweaver reconnect --account work-bsky", True),
    ("reachweaver review", True),
    ("reachweaver --db state/x.sqlite review --campaign launch --approver me", True),
    ("python3 -m reachweaver.cli review --account work-bsky", True),
    ("python3 ~/.claude/skills/reachweaver/scripts/run.py review", True),
    ("python3 -c \"from reachweaver.cli import main; main(['review'])\"", True),
    ("reachweaver publish --live --limit 1", True),
    ("python3 -m reachweaver.cli publish --limit 1 --live", True),
    ("reachweaver publish --live=1", True),
    ("reachweaver account-kit --persona work --platform weibo --approve-plan 0123456789ab", True),
    ("reachweaver account-kit --persona work --platform weibo --approve-plan=0123456789ab", True),
    ("reachweaver approval-mode --confirmation off", True),
    ("reachweaver approval-mode --confirmation=off", True),
    # Abbreviations an older argparse accepted.
    ("reachweaver publish --liv", True),
    ("reachweaver publish --li", True),
    ("reachweaver approval-mode --conf off", True),
    ("reachweaver account-kit --platform weibo --approve abc", True),
    ("reachweaver --d x.sqlite approve 12", True),
    # Environment switches and the approval secret.
    ("REACHWEAVER_ALLOW_LIVE=1 reachweaver publish", True),
    ("export REACHWEAVER_ALLOW_LIVE=1", True),
    ("env REACHWEAVER_ENV=prod reachweaver doctor", True),
    ('REACHWEAVER_ENV="prod" reachweaver doctor', True),
    ("echo $REACHWEAVER_APPROVAL_SECRET", True),
    ('$env:REACHWEAVER_ALLOW_LIVE = "1"', True),
    # Chains, wrappers, nesting and substitution.
    ("reachweaver show 12 && reachweaver approve 12", True),
    ("reachweaver check 12; reachweaver approve 12", True),
    ("reachweaver show 12\nreachweaver approve 12", True),
    ("echo 12 | xargs reachweaver approve", True),
    ("sudo -u me reachweaver approve 12", True),
    ("timeout 30 python3 -m reachweaver publish --live", True),
    ("bash -c 'reachweaver approve 12'", True),
    ("sh -lc \"cd /work && python3 -m reachweaver.cli approve 12\"", True),
    ("bash -c \"bash -c 'reachweaver approve 12'\"", True),
    ('eval "reachweaver approve 12"', True),
    ('echo "$(reachweaver approve 12)"', True),
    ("echo `reachweaver resume`", True),
    ("(cd /work && reachweaver approve 12)", True),
    ('RW="python3 scripts/run.py"; $RW approve 12', True),
    ("reachweaver $CMD 12", True),
    ("reachweaver publish $FLAGS", True),
    # Fail closed: unreadable command or inline Python naming ReachWeaver and an owner-only word.
    ("reachweaver approve 'unterminated", True),
    ("python3 -c \"from reachweaver.cli import main; main(['approve', '12'])\"", True),
    ("python3 - <<'EOF'\nfrom reachweaver.cli import main\nmain(['resume'])\nEOF", True),
    # Accepted trade-off: words, not a quoted string, that read like an owner-only run.
    ("echo reachweaver approve", True),
    # Read-only and dry-run commands pass.
    ("reachweaver show 12", False),
    ("reachweaver check 12", False),
    ("reachweaver list --status approved", False),
    ("python3 -m reachweaver.cli doctor", False),
    ("python3 scripts/run.py queue-health --json", False),
    ("reachweaver publish", False),
    ("reachweaver publish --limit 1 --log-json", False),
    ("reachweaver publish --log-file run.log", False),
    ("reachweaver approval-mode", False),
    ("reachweaver approval-mode --confirmation tty_hash_prefix", False),
    ("reachweaver approval-mode --max-age-hours 168", False),
    ("reachweaver account-kit --persona work --platform weibo --out $HOME/kit", False),
    ("reachweaver unapprove 12", False),
    ("reachweaver pause", False),
    ("reachweaver disconnect --account work-bsky", False),
    # The co-reviewer packet commands are not the review loop.
    ("reachweaver review-packet --post-id 12 --out packet", False),
    ("reachweaver review-status --packet packet", False),
    ("python3 -c \"from reachweaver.cli import main; main(['review-status', '--packet', 'p'])\"", False),
    ("reachweaver --plain journal", False),
    # Draft text that mentions an owner-only word inside one quoted argument passes.
    ('reachweaver draft --account work-bsky --text "Please approve and resume the live --live run"', False),
    ("reachweaver draft --account work-bsky --text 'run `reachweaver approve 12` yourself'", False),
    ("reachweaver draft --account work-bsky --text approve", False),
    # Development commands that only name the files or the variable.
    ("grep -n approve reachweaver/cli.py", False),
    ("grep -rn REACHWEAVER_ALLOW_LIVE docs/", False),
    ("unset REACHWEAVER_ALLOW_LIVE", False),
    ("REACHWEAVER_ENV=dev reachweaver doctor", False),
    ("python3 -m unittest tests.test_agent_guard", False),
    ("git commit -F msg.txt", False),
    ("ls reachweaver && cat reachweaver/cli.py", False),
    ("npm test", False),
    ("", False),
]


class AgentGuardTableTests(unittest.TestCase):
    def test_table(self):
        for command, refused in CASES:
            with self.subTest(command=command):
                reason = agent_guard.check_command(command)
                self.assertEqual(reason is not None, refused, reason)

    def test_table_is_large_and_mixed(self):
        self.assertGreaterEqual(len(CASES), 40)
        self.assertGreaterEqual(sum(1 for _, refused in CASES if not refused), 15)


class AgentGuardHookProtocolTests(unittest.TestCase):
    """Exit 2 + stderr reason blocks a PreToolUse call; exit 0 passes (code.claude.com/docs/en/hooks)."""

    def run_main(self, payload: str) -> tuple[int, str]:
        err = io.StringIO()
        code = agent_guard.main(stdin=io.StringIO(payload), stderr=err)
        return code, err.getvalue()

    def payload(self, command: str, tool: str = "Bash") -> str:
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                           "tool_input": {"command": command, "description": "x"}})

    def test_refusal_exits_2_and_tells_claude_to_ask_the_owner(self):
        code, err = self.run_main(self.payload("python3 -m reachweaver.cli approve 12"))
        self.assertEqual(code, 2)
        self.assertIn("Owner-only: ask the owner to run this in their own terminal.", err)

    def test_allowed_command_exits_0_silently(self):
        self.assertEqual(self.run_main(self.payload("reachweaver show 12")), (0, ""))

    def test_powershell_and_monitor_commands_are_checked(self):
        for tool in ("PowerShell", "Monitor"):
            with self.subTest(tool=tool):
                self.assertEqual(self.run_main(self.payload("reachweaver.exe resume", tool))[0], 2)

    def test_tool_without_a_command_passes(self):
        payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}})
        self.assertEqual(self.run_main(payload), (0, ""))

    def test_unreadable_input_fails_closed_only_when_it_names_reachweaver(self):
        self.assertEqual(self.run_main("{not json reachweaver approve")[0], 2)
        self.assertEqual(self.run_main("{not json")[0], 0)
        self.assertEqual(self.run_main('["reachweaver"]')[0], 2)

    def test_script_runs_isolated_as_the_plugin_does(self):
        # `python3 -I <file>`: no package import, the reachweaver/ directory never shadows stdlib.
        for command, expected in (("reachweaver approve 12", 2), ("reachweaver review", 2),
                                  ("reachweaver show 12", 0), ("reachweaver review-packet --post-id 12 --out p", 0)):
            with self.subTest(command=command):
                done = subprocess.run([sys.executable, "-I", str(PLUGIN_GUARD)], input=self.payload(command),
                                      capture_output=True, text=True, timeout=60, check=False)
                self.assertEqual(done.returncode, expected, done.stderr)


class PluginHookWiringTests(unittest.TestCase):
    def test_hooks_json_registers_the_guard_and_the_script_exists(self):
        data = json.loads(HOOKS.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["matcher"].split("|")), {"Bash", "PowerShell", "Monitor"})
        hook = entries[0]["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertEqual(hook["onFailure"], "block")
        prefix = 'python3 -I "${CLAUDE_PLUGIN_ROOT}/'
        self.assertTrue(hook["command"].startswith(prefix) and hook["command"].endswith('"'), hook["command"])
        relative = hook["command"][len(prefix):-1]
        self.assertTrue((ROOT / "plugin" / relative).is_file(), relative)
        self.assertEqual((ROOT / "plugin" / relative).read_bytes(),
                         (ROOT / "reachweaver/agent_guard.py").read_bytes())

    def test_session_start_hook_is_unchanged(self):
        data = json.loads(HOOKS.read_text(encoding="utf-8"))
        self.assertEqual([h["matcher"] for h in data["hooks"]["SessionStart"]], ["startup"])

    def test_guard_imports_only_the_standard_library_and_nothing_relative(self):
        text = (ROOT / "reachweaver/agent_guard.py").read_text(encoding="utf-8")
        self.assertNotIn("from .", text)
        self.assertNotIn("import reachweaver", text)


class NoAbbreviatedOptionsTests(unittest.TestCase):
    """argparse's allow_abbrev would let `publish --liv` mean `--live` past a deny rule (P2)."""

    def parse(self, *argv: str):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            make_parser().parse_args(list(argv))
        return caught.exception.code

    def test_abbreviated_owner_only_options_are_rejected(self):
        for argv in (("publish", "--liv"), ("publish", "--lim", "1"), ("approval-mode", "--conf", "off"),
                     ("account-kit", "--platform", "weibo", "--approve", "abc"),
                     ("--d", "x.sqlite", "show", "1")):
            with self.subTest(argv=argv):
                self.assertEqual(self.parse(*argv), 2)

    def test_full_spellings_still_parse(self):
        args = make_parser().parse_args(["publish", "--live", "--limit", "1"])
        self.assertTrue(args.live)
        args = make_parser().parse_args(["approval-mode", "--confirmation", "off"])
        self.assertEqual(args.confirmation, "off")

    def test_every_parser_refuses_abbreviations(self):
        import argparse
        parser = make_parser()
        self.assertFalse(parser.allow_abbrev)
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertGreater(len(action.choices), 50)
        for name, sub in action.choices.items():
            with self.subTest(command=name):
                self.assertFalse(sub.allow_abbrev)


@unittest.skipUnless(SETTINGS.is_file(), ".claude/settings.json is private-only and absent from the public checkout")
class RepositoryDenyRulesCoverModuleFormTests(unittest.TestCase):
    """The repository's own deny rules also cover `python3 -m reachweaver[.cli]` (P1)."""

    def test_module_form_is_denied(self):
        import fnmatch
        rules = [r[len("Bash("):-1] for r in json.loads(SETTINGS.read_text("utf-8"))["permissions"]["deny"]
                 if r.startswith("Bash(")]
        for command in ("python3 -m reachweaver.cli approve 12", "python3 -m reachweaver approve 12",
                        "python3 -m reachweaver.cli publish --live", "python3 -m reachweaver publish --limit 1 --live",
                        "python3 -m reachweaver.cli resume", "python3 -m reachweaver reconcile 12 --published",
                        "reachweaver reconnect --account a", "python3 -m reachweaver.cli reconnect --account a",
                        "reachweaver review", "reachweaver review --campaign launch", "python3 -m reachweaver review",
                        "python3 -m reachweaver.cli review --account a", "python3 scripts/run.py review"):
            with self.subTest(command=command):
                self.assertTrue(any(fnmatch.fnmatchcase(command, rule) for rule in rules))
        for command in ("reachweaver review-packet --post-id 12 --out packet", "reachweaver review-status --packet p"):
            with self.subTest(command=command):  # agent-safe packet commands stay allowed
                self.assertFalse(any(fnmatch.fnmatchcase(command, rule) for rule in rules))


if __name__ == "__main__":
    unittest.main()
