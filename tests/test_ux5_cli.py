"""Terminal UX round 5 (2026-10-22): fewer commands in three measured journeys. Offline; synthetic data only.

1. ``posted`` at a terminal ends with "This week so far: ..." (the first line of ``report --week``, from the same
   functions) and ``More: reachweaver report --week``; piped output keeps ``Next: reachweaver report --week``.
2. ``idea "a" "b"`` saves several ideas in one write (all or none; an empty one is refused); at a terminal it then
   prints the top suggestion of ``next``; piped output and the single-idea and ``idea add`` forms are unchanged.
3. ``account-add`` at a terminal asks "Connect it now ...? [y/N]" (default No) and on y runs the unchanged
   ``connect --guide --account ID`` flow in the same process; elsewhere it only prints that command.
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

from reachweaver import account_add, ideas, results, whats_next
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.storage import add_post, connect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_no_network import NetworkGuard  # noqa: E402

LINK_1 = "https://bsky.app/profile/maker.bsky.social/post/3kux5aaa1"
LINK_2 = "https://bsky.app/profile/maker.bsky.social/post/3kux5aaa2"
LINK_3 = "https://www.instagram.com/p/SyntheticUx5/"
CREDENTIAL_LINES = ("      read -r -s -p 'REACHWEAVER_BSKY_IDENTIFIER: ' REACHWEAVER_BSKY_IDENTIFIER; "
                    "export REACHWEAVER_BSKY_IDENTIFIER; printf '\\n'",
                    "      read -r -s -p 'REACHWEAVER_BSKY_APP_PASSWORD: ' REACHWEAVER_BSKY_APP_PASSWORD; "
                    "export REACHWEAVER_BSKY_APP_PASSWORD; printf '\\n'")


class TTY(io.StringIO):
    def isatty(self):
        return True


class Sandbox(unittest.TestCase):
    """A fresh folder as the working directory, no REACHWEAVER_* settings, no network."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state" / "db.sqlite")
        self.accounts = self.root / "config" / "accounts.json"
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv, tty: bool = False, answers: str | None = None) -> tuple[int, str, str]:
        """``tty``: stdout and stderr are terminals. ``answers``: stdin is a terminal holding these lines."""
        out, err = (TTY() if tty else io.StringIO()), (TTY() if tty else io.StringIO())
        stdin = TTY(answers) if answers is not None else io.StringIO("")
        with NetworkGuard() as guard, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch("sys.stdin", stdin):
            try:
                code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
            except SystemExit as exc:  # argparse errors
                code = exc.code
            self.assertEqual(guard.attempts, [])
        return code, out.getvalue(), err.getvalue()


class PostedWeekTests(Sandbox):
    def record_three(self, tty: bool) -> list[str]:
        outs = []
        for link, likes in ((LINK_1, "12"), (LINK_2, "30"), (LINK_3, "7")):
            code, out, err = self.cli("posted", link, "--likes", likes, "--replies", "1", tty=tty)
            self.assertEqual(code, 0, out + err)
            outs.append(out)
        return outs

    def test_a_terminal_gets_the_week_so_far_from_the_report_functions(self):
        outs = self.record_three(tty=True)
        last = outs[-1].strip().splitlines()
        self.assertEqual(last[-1], "More: reachweaver report --week")
        self.assertTrue(last[-2].startswith("This week so far: 3 posts · 49 likes · 3 replies · best: "), last[-2])
        self.assertIn("(30 likes, 1 reply)", last[-2])
        self.assertNotIn("Next: reachweaver report --week", outs[-1])
        self.assertTrue(outs[0].strip().splitlines()[-2].startswith("This week so far: 1 post · 12 likes"))
        # The same numbers as the first line of `report --week` (one set of functions, not a second calculation).
        _, report, _ = self.cli("report", "--week", tty=True)
        head, _, rest = report.splitlines()[0].partition(": ")
        self.assertTrue(head.startswith("This week (since "), head)
        self.assertEqual(last[-2], f"{results.WEEK_SO_FAR}: {rest}")

    def test_piped_output_is_unchanged(self):
        outs = self.record_three(tty=False)
        for out in outs:
            self.assertEqual(out.strip().splitlines()[-1], "Next: reachweaver report --week")
            self.assertNotIn("This week so far", out)
            self.assertNotIn("More:", out)

    def test_updating_numbers_at_a_terminal_shows_the_week_too(self):
        self.record_three(tty=False)
        code, out, err = self.cli("posted", LINK_1, "--likes", "50", tty=True)
        self.assertEqual(code, 0, out + err)
        lines = out.strip().splitlines()
        self.assertTrue(lines[0].startswith("Updated numbers for"), out)
        self.assertTrue(lines[-2].startswith("This week so far: 3 posts · 87 likes"), lines[-2])
        self.assertEqual(lines[-1], "More: reachweaver report --week")
        code, out, _ = self.cli("posted", LINK_1, "--likes", "51")
        self.assertEqual(out.strip().splitlines()[-1], "Next: reachweaver report --week")

    def test_a_post_older_than_a_week_says_so_plainly(self):
        code, out, err = self.cli("posted", LINK_1, "--likes", "4", "--at", "2026-01-05", tty=True)
        self.assertEqual(code, 0, out + err)
        self.assertIn("This week so far: no published posts recorded.", out)

    def test_week_summary_only_reads(self):
        self.record_three(tty=False)
        with connect(self.db) as db:
            before = db.total_changes
            line = results.week_summary(db)
            self.assertEqual(db.total_changes, before)
        self.assertTrue(line.startswith("This week so far: 3 posts"), line)


class IdeaManyTests(Sandbox):
    def inbox(self) -> list[dict]:
        return ideas.listing(ideas.inbox_path(self.db))

    def test_several_ideas_in_one_command(self):
        code, out, err = self.cli("idea", "Behind the scenes", "Three tools I use")
        self.assertEqual(code, 0, out + err)
        saved = self.inbox()
        self.assertEqual([i["text"] for i in saved], ["Behind the scenes", "Three tools I use"])
        self.assertEqual({i["status"] for i in saved}, {"open"})
        self.assertEqual(len({i["id"] for i in saved}), 2)
        self.assertIn("Added 2 ideas to the ideas inbox", out)
        self.assertIn(", ".join(i["id"] for i in saved), out)
        self.assertEqual(out.strip().splitlines()[-1], ideas.SAVED_NEXT)  # piped: no suggestion, as before

    def test_idea_add_takes_several_too_and_json_is_a_list(self):
        code, out, err = self.cli("idea", "add", "One", "Two", "--tag", "studio", "--json")
        self.assertEqual(code, 0, out + err)
        records = json.loads(out)
        self.assertEqual([(r["text"], r["tags"]) for r in records], [("One", ["studio"]), ("Two", ["studio"])])

    def test_one_idea_is_unchanged(self):
        code, out, err = self.cli("idea", "Only one")
        self.assertEqual(code, 0, out + err)
        [saved] = self.inbox()
        self.assertEqual(out, f"Added {saved['id']} to the ideas inbox ({ideas.inbox_path(self.db)}). Not a draft: "
                              f"nothing was queued or sent.\n{ideas.SAVED_NEXT}\n")
        code, out, _ = self.cli("idea", "add", "Second one", "--json")
        self.assertEqual(json.loads(out)["text"], "Second one")  # one idea: one record, not a list

    def test_an_empty_idea_saves_nothing(self):
        for argv in (("idea", "Good one", ""), ("idea", "Good one", "   "), ("idea", "")):
            code, _, err = self.cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("empty", err)
            self.assertEqual(self.inbox(), [], argv)
        code, _, err = self.cli("idea", "First", "")
        self.assertIn("Idea 2", err)
        self.assertIn("Nothing saved", err)

    def test_a_source_belongs_to_one_idea(self):
        code, _, err = self.cli("idea", "One", "Two", "--source", "https://example.org/a")
        self.assertEqual(code, 2)
        self.assertIn("ONE idea", err)
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.cli("idea", "One", "--source", "https://example.org/a")[0], 0)

    def test_a_terminal_gets_the_top_suggestion_of_next(self):
        code, out, err = self.cli("idea", "Behind the scenes of my studio", "Three tools", tty=True)
        self.assertEqual(code, 0, out + err)
        first = self.inbox()[0]
        top = whats_next.build_next(self.db)["actions"][0]
        self.assertEqual(top["evidence"]["idea_id"], first["id"])
        self.assertIn("Top of your list: Write a post from your idea \"Behind the scenes of my studio\"", out)
        self.assertIn("Write it now: reachweaver share --guide --text 'Behind the scenes of my studio'  or in the "
                      f"browser: reachweaver share --web --open --idea {first['id']}", out)
        self.assertEqual(out.strip().splitlines()[-1], "All suggestions: reachweaver next")
        self.assertNotIn(ideas.SAVED_NEXT, out)
        self.assertFalse(Path(self.db).exists(), "idea and its suggestion never create the database")

    def test_a_waiting_draft_comes_first_at_a_terminal(self):
        self.accounts.parent.mkdir(parents=True)
        self.accounts.write_text(json.dumps({"accounts": [{"id": "my-bsky", "platform": "bluesky",
                                                           "env_prefix": "REACHWEAVER_UX5", "handle": "maker.bsky.social"}]}),
                                 encoding="utf-8")
        with connect(self.db) as db:
            add_post(db, "my-bsky", "bluesky", "A synthetic draft")
        code, out, err = self.cli("idea", "Later", tty=True)
        self.assertEqual(code, 0, out + err)
        self.assertIn("First, before writing: 1 draft(s) waiting for you", out)
        self.assertIn("  reachweaver review", out)
        self.assertEqual(out.strip().splitlines()[-1], "Saved ideas and everything else: reachweaver next")

    def test_all_or_nothing_in_one_write(self):
        path = ideas.inbox_path(self.db)
        with self.assertRaises(Exception):
            ideas.add_many(path, ["fine", "x" * (ideas.TEXT_MAX + 1)])
        self.assertEqual(ideas.listing(path), [])
        with patch.object(ideas, "_save", wraps=ideas._save) as save:
            ideas.add_many(path, ["a", "b", "c"])
        self.assertEqual(save.call_count, 1)


class AccountAddConnectTests(Sandbox):
    def test_yes_continues_into_the_unchanged_connect_guide(self):
        code, out, err = self.cli("account-add", "bluesky", tty=True, answers="maker.bsky.social\n\ny\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("my-bluesky", load_accounts(self.accounts))
        self.assertIn(account_add.CONNECT_NOW, out)
        guide = out[out.index("Continuing with the connection steps for my-bluesky."):]
        self.assertIn("ReachWeaver connection guide.", guide)
        self.assertIn("[1/1] my-bluesky (Bluesky)", guide)
        for line in CREDENTIAL_LINES:
            self.assertIn(line + "\n", guide)
        self.assertIn("(not inside an AI chat)", guide)
        self.assertIn("Never paste a password, token or code into Claude, a chat or an issue.", guide)
        # The same text a separate `connect --guide --account my-bluesky` prints.
        code, alone, _ = self.cli("connect", "--guide", "--account", "my-bluesky", tty=True, answers="")
        self.assertEqual(code, 0)
        self.assertEqual(guide.split("\n", 1)[1], alone)
        self.assertNotIn("Or post by hand now", out)

    def test_enter_means_no_and_prints_the_usual_next_steps(self):
        code, out, err = self.cli("account-add", "bluesky", tty=True, answers="maker.bsky.social\n\n\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn(account_add.CONNECT_NOW, out)
        self.assertNotIn("connection guide", out)
        self.assertIn("Next: reachweaver", out)
        self.assertIn("connect --guide --account my-bluesky", out)
        self.assertIn("Or post by hand now, no sign-in needed", out)

    def test_end_of_input_means_no(self):
        code, out, _ = self.cli("account-add", "bluesky", "--handle", "maker.bsky.social", tty=True, answers="")
        self.assertEqual(code, 0)
        self.assertIn(account_add.CONNECT_NOW, out)
        self.assertNotIn("connection guide", out)

    def test_not_a_terminal_never_asks(self):
        code, out, err = self.cli("account-add", "bluesky", "--handle", "maker.bsky.social")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("Connect it now", out)
        self.assertIn("connect --guide --account my-bluesky", out)
        # stdin a terminal but output piped: still no question (connect --guide needs both).
        self.accounts.unlink()
        code, out, _ = self.cli("account-add", "bluesky", "--handle", "maker.bsky.social", answers="y\n")
        self.assertEqual(code, 0)
        self.assertNotIn("Connect it now", out)

    def test_no_question_for_a_network_without_an_adapter_or_a_dry_run(self):
        code, out, _ = self.cli("account-add", "instagram", "--handle", "maker", tty=True, answers="y\n")
        self.assertEqual(code, 0)
        self.assertNotIn("Connect it now", out)
        code, out, _ = self.cli("account-add", "bluesky", "--handle", "maker.bsky.social", "--dry-run", tty=True,
                                answers="y\n")
        self.assertEqual(code, 0)
        self.assertNotIn("Connect it now", out)

    def test_connect_adding_a_first_account_does_not_ask_twice(self):
        code, out, err = self.cli("connect", "--guide", tty=True, answers="bluesky\nmaker.bsky.social\n\n")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("Connect it now", out)
        self.assertEqual(out.count("ReachWeaver connection guide."), 1)


if __name__ == "__main__":
    unittest.main()
