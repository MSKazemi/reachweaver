"""Terminal UX round-3 strict re-measure fixes (2026-10-21). Offline; synthetic data only.

1. Next steps repeat the run's own --accounts/--db (``home.command``), so following them does not loop.
2. ``--comments`` is ``--replies`` in ``posted``/``metrics``; "Did you mean" tries the apps' words first.
3. ``share --attach``: file findings print once, before the networks.
4. Plain words in share/account-add/connect.
5. ``report`` at a terminal: no post ids, plain advice instead of ``insights --age-hours`` (tests/test_results_ux.py).
6. ``show`` on a post made by hand (tests/test_results_ux.py).
7. ``draft`` names the next step at a terminal only.
8. ``connect --guide`` wording; the credential lines themselves are unchanged.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import connect_guide as cg
from reachweaver import home, results
from reachweaver.accounts import load_accounts
from reachweaver.capabilities import load_registry
from reachweaver.cli import main
from reachweaver.share import MEDIA_CHECK_NOTE
from reachweaver.storage import connect, snapshots_for

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_media import png_bytes  # noqa: E402
from test_no_network import NetworkGuard  # noqa: E402

BSKY_LINK = "https://bsky.app/profile/maker.bsky.social/post/3kexample1"


class TTY(io.StringIO):
    def isatty(self):
        return True


class Sandbox(unittest.TestCase):
    """A fresh folder as the working directory, no REACHWEAVER_* settings, no network."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv, tty: bool = False) -> tuple[int, str, str]:
        out, err = (TTY() if tty else io.StringIO()), (TTY() if tty else io.StringIO())
        with NetworkGuard() as guard, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                patch("sys.stdin", io.StringIO("")):
            try:
                code = main(list(argv))
            except SystemExit as exc:  # argparse errors
                code = exc.code
            self.assertEqual(guard.attempts, [])
        return code, out.getvalue(), err.getvalue()


class HomeCommandTests(unittest.TestCase):
    def tearDown(self):
        home.remember_flags()

    def test_no_typed_options_is_the_plain_command(self):
        home.remember_flags()
        self.assertEqual(home.command("account-add bluesky"), "reachweaver account-add bluesky")
        self.assertIsNone(home.typed_accounts())

    def test_typed_options_are_repeated_quoted_and_an_own_accounts_file_replaces_the_global_one(self):
        home.remember_flags(db="state/my db.sqlite", accounts="mine.json")
        self.assertEqual(home.command("review"), "reachweaver --db 'state/my db.sqlite' --accounts mine.json review")
        self.assertEqual(home.command("account-add bluesky", accounts="other.json"),
                         "reachweaver --db 'state/my db.sqlite' --accounts other.json account-add bluesky")
        self.assertEqual(home.typed_accounts(), "mine.json")

    def test_main_forgets_the_options_when_it_ends(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            main(["--accounts", str(Path(folder) / "none.json"), "--db", str(Path(folder) / "s" / "x.sqlite"),
                  "connect"])
        self.assertEqual(home.command("x"), "reachweaver x")


class FollowTheHintTests(Sandbox):
    """J1: `--accounts myaccounts.json draft ...` used to suggest a plain account-add, which wrote
    config/accounts.json, so the retry failed the same way."""

    def test_the_suggested_account_add_fills_the_named_file_and_the_retry_works(self):
        draft = ["--accounts", "myaccounts.json", "--db", "state/rw.sqlite", "draft", "--account", "my-bluesky",
                 "--text", "Hello from a synthetic test"]
        code, _, err = self.cli(*draft)
        self.assertEqual(code, 2)
        self.assertIn("Missing myaccounts.json: no accounts yet. Add one: reachweaver --db state/rw.sqlite "
                      "--accounts myaccounts.json account-add bluesky (public details only).", err)
        self.assertNotIn("workspace --init", err, "a workspace would create config/accounts.json instead")
        suggested = shlex.split(err.split("Add one: ", 1)[1].split(" (public", 1)[0])
        self.assertEqual(suggested[0], "reachweaver")
        code, out, err = self.cli(*suggested[1:], "--handle", "maker.bsky.social")
        self.assertEqual(code, 0, err)
        self.assertEqual(list(load_accounts(self.root / "myaccounts.json")), ["my-bluesky"])
        self.assertFalse((self.root / "config" / "accounts.json").exists())
        self.assertIn("Next: reachweaver --db state/rw.sqlite --accounts myaccounts.json connect --guide "
                      "--account my-bluesky", out)
        code, out, err = self.cli(*draft)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"^Created draft [0-9a-f]{16} for my-bluesky; not published\n$")

    def test_default_paths_keep_the_short_message_and_the_workspace_offer(self):
        code, _, err = self.cli("draft", "--account", "my-bluesky", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("Missing config/accounts.json: no accounts yet. Add one: reachweaver account-add bluesky "
                      "(public details only), or set up a folder: reachweaver workspace --init .", err)

    def test_draft_at_a_terminal_names_the_next_step_with_the_same_options(self):
        self.assertEqual(self.cli("--accounts", "mine.json", "account-add", "bluesky", "--handle",
                                  "maker.bsky.social")[0], 0)
        code, out, err = self.cli("--accounts", "mine.json", "draft", "--account", "my-bluesky", "--text", "Hi",
                                  tty=True)
        self.assertEqual(code, 0, err)
        post_id = out.split()[2]
        self.assertIn(f"Next: reachweaver --accounts mine.json review (go through your drafts) or "
                      f"reachweaver --accounts mine.json show {post_id}", out)
        code, out, _ = self.cli("--accounts", "mine.json", "draft", "--account", "my-bluesky", "--text", "Hi 2")
        self.assertEqual(out.count("\n"), 1, "a script still gets the one line")


class CommentsAliasTests(Sandbox):
    def test_posted_and_metrics_take_comments_as_replies(self):
        code, out, err = self.cli("posted", BSKY_LINK, "--likes", "3", "--comments", "4")
        self.assertEqual(code, 0, err)
        self.assertIn("3 likes, 4 replies", out)
        with connect("state/reachweaver.sqlite") as con:
            post_id = con.execute("SELECT id FROM posts").fetchone()["id"]
            self.assertEqual([row["replies"] for row in snapshots_for(con, post_id)], [4])
        code, _, err = self.cli("metrics", post_id, "--comments", "6")
        self.assertEqual(code, 0, err)
        with connect("state/reachweaver.sqlite") as con:
            self.assertIn(6, [row["replies"] for row in snapshots_for(con, post_id)])

    def test_did_you_mean_prefers_the_apps_words_before_spelling(self):
        cases = (("posted", BSKY_LINK, "--shares", "2", "--reposts"),
                 ("posted", BSKY_LINK, "--retweets", "2", "--reposts"),
                 ("posted", BSKY_LINK, "--impressions", "9", "--views"),
                 ("metrics", "abc", "--boosts", "1", "--reposts"))
        for command, target, flag, value, expected in cases:
            code, _, err = self.cli(command, target, flag, value)
            self.assertEqual(code, 2)
            self.assertIn(f"unrecognized arguments: {flag} {value}. Did you mean {expected}?", err)
        code, _, err = self.cli("draft", "--account", "a", "--text", "x", "--comments", "4")
        self.assertEqual(code, 2)
        self.assertNotIn("--replies", err, "only an option the command has is suggested")


class ShareWordsAndMediaTests(Sandbox):
    def test_a_file_finding_is_printed_once_for_three_networks(self):
        (self.root / "clip.mp4").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)  # a WebM header in a .mp4 name
        code, out, err = self.cli("share", "--platform", "bluesky,mastodon,linkedin", "--text", "Demo clip",
                                  "--attach", "clip.mp4", "--out", "pk")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("unrecognized file header"), 1, out)
        self.assertEqual(out.count(MEDIA_CHECK_NOTE), 1, out)
        self.assertLess(out.index("Attached media: 1 file"), out.index("Check what will be created:"))
        for network in ("bluesky", "mastodon", "linkedin"):  # each package still says it
            checklist = (self.root / "pk" / network / "CHECKLIST.md").read_text(encoding="utf-8")
            self.assertIn("unrecognized file header", checklist)

    def test_a_finding_every_network_has_is_printed_once(self):
        (self.root / "photo.png").write_bytes(png_bytes(1080, 1080))
        code, out, err = self.cli("share", "--platform", "bluesky,mastodon", "--text", "A photo",
                                  "--attach", "photo.png", "--out", "pk")
        self.assertEqual(code, 0, err)
        media, plan = out.split("Check what will be created:", 1)
        plan = plan.split("Your text", 1)[0]
        self.assertIn(" - Bluesky: Copy and post it yourself (image)", plan)
        self.assertEqual(media.count("Media photo.png (1080x1080): no problem found"), 1, out)
        self.assertNotIn("Media photo.png", plan)
        self.assertEqual(out.count(MEDIA_CHECK_NOTE), 1)

    def test_network_specific_media_lines_stay_with_their_network(self):
        (self.root / "photo.png").write_bytes(png_bytes(1080, 1080))
        code, out, err = self.cli("share", "--platform", "bluesky,threads", "--text", "A photo",
                                  "--attach", "photo.png", "--out", "pk")
        self.assertEqual(code, 0, err)
        plan = out.split("Check what will be created:", 1)[1].split("Your text", 1)[0]
        bluesky, threads = plan.split(" - Threads:", 1)
        self.assertIn("Media photo.png (1080x1080): no problem found", bluesky)
        self.assertNotIn("no sourced", bluesky)
        self.assertIn("has no sourced image limits for this network", threads)
        self.assertEqual(out.count(MEDIA_CHECK_NOTE), 1)

    def test_plain_words_and_plurals(self):
        code, out, err = self.cli("share", "--platform", "bluesky", "--text", "Hello there", "--out", "one")
        self.assertEqual(code, 0, err)
        self.assertIn("Check what will be created:\n - Bluesky: Copy and post it yourself (text)", out)
        self.assertIn("Prepared 1 private package to copy from, in one", out)
        for jargon in ("handoff plan", "MANUAL COPY", "format text", "manual packages"):
            self.assertNotIn(jargon, out)
        code, out, err = self.cli("share", "--platform", "bluesky,mastodon", "--text", "Hello there", "--out", "two")
        self.assertIn("Prepared 2 private packages to copy from, in two", out)
        code, out, err = self.cli("share", "--platform", "hacker_news", "--note", "Ask about the demo",
                                  "--out", "hn")
        self.assertEqual(code, 0, err)
        self.assertIn("You write the post yourself (notes only)", out)
        self.assertNotIn("YOU WRITE IT", out)


class ReportAdviceTests(unittest.TestCase):
    NOTE = {"posts": 1, "hours_after_posting": [5], "record_again_around": "2026-10-28",
            "suggested_age_hours": 25, "matched_age_hours": 168}

    def test_posts_recorded_by_hand_get_the_posted_command(self):
        self.assertEqual(results.early_note_advice(self.NOTE, by_hand=True),
                         "You recorded these numbers 5 hours after posting. Record numbers again around 2026-10-28 "
                         "to compare after 7 days: reachweaver posted LINK --likes N")

    def test_posts_reachweaver_sent_get_the_metrics_command(self):
        text = results.early_note_advice(dict(self.NOTE, hours_after_posting=[0, 1]), by_hand=False)
        self.assertTrue(text.startswith("You recorded these numbers between 0 and 1 hours after posting."), text)
        self.assertIn("reachweaver metrics POST_ID --likes N ('reachweaver list' shows the post ids)", text)

    def test_when_the_date_has_passed_it_says_how_to_compare_what_there_is(self):
        text = results.early_note_advice(dict(self.NOTE, hours_after_posting=[250], record_again_around=None,
                                              suggested_age_hours=250), by_hand=True)
        self.assertIn("to compare the ones you have, run: reachweaver insights --age-hours 250", text)


class ConnectGuideWordingTests(unittest.TestCase):
    """Wording only. The read/export lines and what is asked (nothing) are protected and unchanged."""

    def test_own_terminal_window_wording_and_how_long_the_variables_last(self):
        accounts = {"blue": {"id": "blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX4_BSKY",
                             "handle": "me.bsky.social"}}
        said, asked = [], []
        with patch.dict(os.environ, {}, clear=True):
            cg.run_guided(accounts, load_registry(), only=None, ask=lambda p: asked.append(p) or "q",
                          say=said.append, check=lambda account: None)
        text = "\n".join(said)
        self.assertEqual(asked, [], "nothing is asked: no secret is ever requested")
        self.assertIn("Then, in your own terminal window (not inside an AI chat), set these variables WITHOUT typing "
                      "the value into history", text)
        self.assertNotIn("the AI does not control", text)
        for name in ("REACHWEAVER_UX4_BSKY_IDENTIFIER", "REACHWEAVER_UX4_BSKY_APP_PASSWORD"):
            self.assertIn(f"      read -r -s -p '{name}: ' {name}; export {name}; printf '\\n'", said)
        self.assertIn("  These variables last only until you close that terminal window. How to keep them safely: "
                      + cg.DOCS_BASE + "docs/ACCOUNT-ONBOARDING.md", said)
        self.assertIn("  Never paste a password, token or code into Claude, a chat or an issue.", said)

    def test_no_accounts_lines_are_typeable(self):
        text = cg.render_no_accounts()
        self.assertIn("  Other networks: reachweaver account-add mastodon (or telegram, linkedin)", text)
        self.assertNotIn("mastodon, telegram or linkedin", text)


if __name__ == "__main__":
    unittest.main()
