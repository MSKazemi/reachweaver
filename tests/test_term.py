"""Terminal helpers and the CLI presentation they drive. Offline; plain output must stay unchanged."""
from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import term
from reachweaver.cli import main
from reachweaver.storage import add_post, connect

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Tty(io.StringIO):
    def isatty(self):
        return True


class TermUnitTests(unittest.TestCase):
    def setUp(self):
        term.set_plain(False)
        self.addCleanup(term.set_plain, False)

    def test_plain_when_not_a_terminal_or_with_plain(self):
        self.assertFalse(term.enabled(io.StringIO()))
        self.assertTrue(term.enabled(Tty()))
        term.set_plain(True)
        self.assertFalse(term.enabled(Tty()))
        self.assertEqual(term.style("x", "red", Tty()), "x")

    def test_style_only_on_a_terminal(self):
        self.assertEqual(term.style("OK", "green", io.StringIO()), "OK")
        self.assertEqual(term.style("OK", "green", Tty()), "\x1b[32mOK\x1b[0m")
        self.assertEqual(term.style("OK", "nonsense", Tty()), "OK")

    def test_table_aligns_and_fits_the_width_and_colour_does_not_shift_columns(self):
        rows = [["a1", "draft", "x" * 80], ["b22", "needs_review", "short"]]
        plain = term.table(["ID", "STATUS", "NOTE"], rows, io.StringIO(), columns=40)
        coloured = term.table(["ID", "STATUS", "NOTE"], rows, Tty(), columns=40,
                              paint=lambda col, text: "red" if col == 1 and text == "needs_review" else None)
        self.assertEqual(ANSI.sub("", coloured), plain)
        self.assertTrue(all(len(line) <= 40 for line in plain.splitlines()), plain)
        self.assertIn("…", plain)
        self.assertIn("\x1b[31mneeds_review\x1b[0m", coloured)

    def test_wide_characters_are_measured_as_two_columns(self):
        text = term.table(["N"], [["日本語"]], io.StringIO(), columns=80)
        self.assertEqual(text.splitlines()[1], "日本語")

    def test_fixed_columns_are_never_clipped_so_ids_stay_copyable(self):
        ident = "0c82441be134aaaa"
        rows = [[ident, "my-bluesky", "bluesky", "draft", "2026-10-16T10:00:00+00:00", "https://example.org/" + "x" * 60]]
        text = term.table(["ID", "ACCOUNT", "PLATFORM", "STATUS", "SCHEDULED", "URL"], rows, io.StringIO(),
                          columns=80, fixed=(0,))
        self.assertIn(ident, text)
        self.assertIn("…", text)

    def test_control_characters_cannot_steer_the_terminal(self):
        text = term.table(["URL"], [["https://x/\x1b[2J\x07bell"]], io.StringIO(), columns=80)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertEqual(term.clean("a\x1bb\tc\n"), "a\ufffdb\tc\n")

    def test_terminal_width_comes_from_the_os_with_a_default(self):
        self.assertEqual(term.terminal_columns(io.StringIO()), 100)

    def test_term_reads_no_environment(self):
        source = Path(term.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|\benv\.get")


class CliPresentationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "state" / "t.sqlite")
        self.config = Path(tmp.name) / "accounts.json"
        self.config.write_text('{"accounts":[{"id":"t-bsky","platform":"bluesky","env_prefix":"REACHWEAVER_T_BSKY",'
                               '"handle":"t.bsky.social"}]}', encoding="utf-8")
        with connect(self.db) as con:
            self.post = add_post(con, "t-bsky", "bluesky", "hello")
        term.set_plain(False)
        self.addCleanup(term.set_plain, False)

    def run_cli(self, *argv, tty=False):
        out, err = (Tty(), Tty()) if tty else (io.StringIO(), io.StringIO())
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_list_output_is_unchanged_when_piped(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertEqual(out, f"{self.post} | t-bsky | bluesky | draft |  | \n")

    def test_list_is_a_table_on_a_terminal_and_plain_flag_restores_the_old_format(self):
        code, out, _ = self.run_cli("list", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("STATUS", ANSI.sub("", out))
        self.assertIn("1 post(s)", out)
        self.assertNotIn(" | ", out)
        out.index("\x1b[")  # coloured
        code, out, _ = self.run_cli("--plain", "list", tty=True)
        self.assertEqual(out, f"{self.post} | t-bsky | bluesky | draft |  | \n")

    def test_empty_list_on_a_terminal_says_what_to_do(self):
        with connect(self.db) as con:
            con.execute("DELETE FROM posts")
        _, out, _ = self.run_cli("list", tty=True)
        self.assertIn("No posts yet", out)

    def test_show_is_json_when_piped_and_a_readable_sheet_on_a_terminal(self):
        import json as _json
        code, out, _ = self.run_cli("show", self.post)
        self.assertEqual(_json.loads(out)["id"], self.post)
        code, out, _ = self.run_cli("show", self.post, tty=True)
        plain = ANSI.sub("", out)
        for needle in ("EXACT TEXT", "hello", "Not approved yet", "--plain show " + self.post):
            self.assertIn(needle, plain)
        code, out, _ = self.run_cli("--plain", "show", self.post, tty=True)
        self.assertEqual(_json.loads(out)["id"], self.post)

    def test_check_text_is_plain_when_piped(self):
        code, out, _ = self.run_cli("check", self.post)
        self.assertEqual((code, out.splitlines()[0]), (0, "OK"))

    def test_error_hint_only_for_a_person_at_a_terminal(self):
        _, _, err = self.run_cli("show", "nope")
        self.assertEqual(err.strip(), "ERROR: Unknown post id: nope")
        _, _, err = self.run_cli("show", "nope", tty=True)
        self.assertIn("ERROR: Unknown post id: nope", err)
        self.assertIn("reachweaver list", err)

    def test_missing_accounts_file_hint(self):
        out, err = io.StringIO(), Tty()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(Path(self.db).parent / "absent.json"), "doctor"])
        self.assertEqual(code, 2)
        self.assertIn("accounts.example.json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
