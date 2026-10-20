"""Shell completion generated from the parser. Runs real bash; offline, reads no database."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reachweaver import completion
from reachweaver.cli import make_parser


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class BashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls.tmp.name) / "rw.bash"
        cls.script.write_text(completion.render("bash", make_parser()), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def complete(self, *words, current=""):
        quoted = " ".join(f"'{w}'" for w in (*words, current))
        code = (f"source '{self.script}'; COMP_WORDS=({quoted}); COMP_CWORD={len(words)}; _reachweaver; "
                'printf "%s\\n" "${COMPREPLY[@]}"')
        out = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=20, check=True)
        return out.stdout.split()

    def test_script_is_valid_bash(self):
        subprocess.run(["bash", "-n", str(self.script)], check=True, timeout=20)

    def test_completes_subcommands(self):
        self.assertEqual(self.complete("reachweaver", current="ap"), ["approval-mode", "approve"])
        self.assertIn("ui", self.complete("reachweaver", current="u"))
        everything = self.complete("reachweaver")
        names = sorted(completion._subparsers(make_parser()))
        self.assertTrue(set(names) <= set(everything))

    def test_completes_options_of_the_chosen_command_after_global_options(self):
        self.assertIn("--account", self.complete("reachweaver", "--db", "x.sqlite", "draft", current="--a"))
        self.assertIn("--port", self.complete("reachweaver", "ui", current="--p"))

    def test_fixed_choices_complete_and_free_values_do_not_offer_flags(self):
        self.assertEqual(self.complete("reachweaver", "youtube-attach", "--privacy"), ["private", "unlisted", "public"])
        self.assertEqual(self.complete("reachweaver", "draft", "--account"), [])
        self.assertEqual(self.complete("reachweaver", "--db"), [])

    def test_unknown_command_completes_nothing_harmful(self):
        self.assertEqual(self.complete("reachweaver", "nonsense", current="--x"), [])

    def test_completes_the_actions_of_a_command_with_its_own_subcommands(self):
        self.assertEqual(self.complete("reachweaver", "idea"), ["add", "drop", "list", "promote", "--help"])
        self.assertEqual(self.complete("reachweaver", "idea", current="p"), ["promote"])
        self.assertEqual(self.complete("reachweaver", "--db", "x.sqlite", "idea", current="l"), ["list"])
        actions = sorted(completion._nested(completion._subparsers(make_parser())["idea"]))
        self.assertEqual(actions, ["add", "drop", "list", "promote"])

    def test_completes_options_and_choices_of_a_nested_action(self):
        self.assertEqual(self.complete("reachweaver", "idea", "list", current="--s"), ["--status"])
        self.assertEqual(self.complete("reachweaver", "idea", "list", "--status"), ["open", "promoted", "dropped"])
        self.assertEqual(self.complete("reachweaver", "idea", "list", "--status", current="pr"), ["promoted"])
        self.assertIn("--objective", self.complete("reachweaver", "idea", "promote", "idea-1a2b3c4d", current="--o"))
        self.assertIn("awareness", self.complete("reachweaver", "--db", "x.sqlite", "idea", "promote", "--objective"))
        self.assertEqual(self.complete("reachweaver", "idea", "add", current="--t"), ["--tag"])
        self.assertEqual(self.complete("reachweaver", "idea", "add", "--tag"), [])  # a free value: nothing offered
        self.assertEqual(self.complete("reachweaver", "idea", "promote", "--out"), [])
        self.assertEqual(self.complete("reachweaver", "idea", "nonsense", current="--"), [])
        # A flat command is unchanged: its options, not the nested actions.
        self.assertNotIn("add", self.complete("reachweaver", "list", current=""))


class CommandTests(unittest.TestCase):
    def test_command_prints_the_script_and_creates_nothing(self):
        import contextlib
        import io
        from reachweaver.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "x.sqlite"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--db", str(db), "completion", "bash"])
            self.assertEqual(code, 0)
            self.assertIn("complete -o default -F _reachweaver reachweaver", out.getvalue())
            self.assertFalse(db.parent.exists())


class RenderTests(unittest.TestCase):
    def test_zsh_wraps_the_same_script_with_bashcompinit(self):
        script = completion.render("zsh", make_parser())
        self.assertIn("bashcompinit", script)
        self.assertIn("complete -o default -F _reachweaver reachweaver", script)

    def test_zsh_carries_the_same_nested_completion_as_bash(self):
        parser = make_parser()
        bash, zsh = completion.render("bash", parser), completion.render("zsh", parser)
        self.assertTrue(zsh.endswith(bash), "zsh must run the exact bash function (through bashcompinit)")
        for key in ("'idea list:--status'", "'idea promote'", "COMPREPLY=($(compgen -W 'add drop list promote --help'"):
            self.assertIn(key, zsh)

    def test_unsupported_shell_is_refused(self):
        with self.assertRaises(ValueError):
            completion.render("powershell", make_parser())

    def test_generation_runs_nothing_and_reads_no_environment(self):
        source = Path(completion.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|\benv\.get|subprocess|sqlite|connect\(")


if __name__ == "__main__":
    unittest.main()
