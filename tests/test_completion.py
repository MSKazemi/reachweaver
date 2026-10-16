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

    def test_unsupported_shell_is_refused(self):
        with self.assertRaises(ValueError):
            completion.render("powershell", make_parser())

    def test_generation_runs_nothing_and_reads_no_environment(self):
        source = Path(completion.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|\benv\.get|subprocess|sqlite|connect\(")


if __name__ == "__main__":
    unittest.main()
