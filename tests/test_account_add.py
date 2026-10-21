"""account-add: one public account entry, validated by the normal loader; never a secret. Synthetic data only."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import account_add
from reachweaver import profile as profile_store
from reachweaver.accounts import load_accounts
from reachweaver.cli import main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_no_network import NetworkGuard  # noqa: E402


class TerminalText(io.StringIO):
    def isatty(self):
        return True


class AccountAddTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.accounts = self.root / "config" / "accounts.json"
        self.profile = self.root / "config" / "profile.json"
        self.db = self.root / "absent.sqlite"

    def cli(self, *args, stdin=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("sys.stdin", stdin or io.StringIO("")):
            code = main(["--db", str(self.db), "--accounts", str(self.accounts), "--profile", str(self.profile),
                         "account-add", *args])
            self.assertEqual(guard.attempts, [])
        self.assertFalse(self.db.exists(), "account-add must not create a database")
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_send_path(self):
        self.assertIsNone(account_add.SEND_PATH)

    def test_adds_bluesky_entry_that_the_loader_accepts_and_names_the_variables(self):
        code, out, err = self.cli("bluesky", "--handle", "@maker.bsky.social")
        self.assertEqual(code, 0, err)
        loaded = load_accounts(self.accounts)
        self.assertEqual(loaded["my-bluesky"], {"id": "my-bluesky", "platform": "bluesky",
                                                "env_prefix": "REACHWEAVER_BSKY", "handle": "maker.bsky.social"})
        self.assertIn("REACHWEAVER_BSKY_IDENTIFIER, REACHWEAVER_BSKY_APP_PASSWORD", out)
        self.assertIn("connect --guide --account my-bluesky", out)
        self.assertFalse(self.accounts.with_name("accounts.json.bak").exists(), "no previous file, no backup")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.accounts.stat().st_mode), 0o600)

    def test_second_account_keeps_a_backup_and_refuses_duplicates(self):
        self.assertEqual(self.cli("bluesky", "--handle", "maker.bsky.social")[0], 0)
        first = self.accounts.read_bytes()
        code, out, err = self.cli("mastodon", "--server", "https://mastodon.example", "--handle", "maker")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.accounts.with_name("accounts.json.bak").read_bytes(), first)
        self.assertEqual(set(load_accounts(self.accounts)), {"my-bluesky", "my-mastodon"})
        self.assertIn("REACHWEAVER_MASTODON_TOKEN", out)
        current = self.accounts.read_bytes()
        for args, message in (
                (("bluesky", "--handle", "MAKER.bsky.social", "--id", "other"), "already uses this identity"),
                (("bluesky", "--handle", "new.bsky.social", "--id", "my-bluesky"), "already exists"),
                (("bluesky", "--handle", "example.bsky.social", "--id", "x2"), "example placeholder"),
                (("bluesky", "--handle", "new.bsky.social", "--field", "app_password=abc"), "Never put tokens"),
                (("bluesky", "--handle", "new.bsky.social", "--field", "chat_id=1"), "take these fields"),
                (("bluesky", "--handle", "new.bsky.social", "--id", "b2", "--env-prefix", "REACHWEAVER_BSKY"),
                 "env_prefix"),
                (("mastodon", "--server", "http://mastodon.example", "--handle", "other"), "HTTPS"),
                (("telegram", "--field", "chat_id=not a chat", "--field", "bot_username=mybot"), "chat_id"),
                (("linkedin", "--field", "person_id=abc123"), "linkedin_version"),
        ):
            code, _, err = self.cli(*args)
            self.assertEqual(code, 2, args)
            self.assertIn(message, err, args)
            self.assertEqual(self.accounts.read_bytes(), current, f"{args} changed the file")

    def test_dry_run_writes_nothing_and_ids_do_not_collide(self):
        self.assertEqual(self.cli("bluesky", "--handle", "maker.bsky.social")[0], 0)
        before = self.accounts.read_bytes()
        code, out, err = self.cli("bluesky", "--handle", "second.bsky.social", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", out)
        self.assertIn("Account name:      my-bluesky-2 (what you type after --account)", out)
        self.assertIn("Credential prefix: REACHWEAVER_MY_BLUESKY_2", out)
        self.assertIn("Network:           Bluesky", out)
        self.assertIn("Handle:            second.bsky.social", out)
        self.assertNotIn('"id":', out, "plain lines, not raw JSON")
        self.assertIn("persona", out, "two accounts on one network: suggest a persona")
        self.assertEqual(self.accounts.read_bytes(), before)
        self.assertFalse(self.accounts.with_name("accounts.json.bak").exists())

    def test_missing_details_without_a_terminal_name_the_flags(self):
        code, _, err = self.cli("telegram", "--field", "chat_id=@mychannel")
        self.assertEqual(code, 2)
        self.assertIn("Telegram needs your bot's username (like your_bot). Add --field bot_username=your_bot.", err)
        self.assertFalse(self.accounts.exists())
        code, _, err = self.cli("bluesky")
        self.assertEqual(code, 2)
        self.assertIn("Bluesky needs your handle (like name.bsky.social). Add --handle name.bsky.social.", err)

    def test_a_copied_example_shape_is_refused_like_a_placeholder(self):
        code, _, err = self.cli("bluesky", "--handle", "name.bsky.social")
        self.assertEqual(code, 2)
        self.assertIn("example placeholder", err)
        self.assertFalse(self.accounts.exists())

    def test_a_blank_answer_in_a_terminal_asks_again(self):
        prompts: list[str] = []
        answers = TerminalText("\n\nmaker.bsky.social\n\n")
        with patch("builtins.input", side_effect=lambda prompt: (prompts.append(prompt), answers.readline().strip())[1]):
            code, out, err = self.cli("bluesky", stdin=answers)
        self.assertEqual(code, 0, err)
        self.assertEqual(sum("Your public username" in p for p in prompts), 3)
        self.assertIn("Bluesky needs your handle (like name.bsky.social). Type it, or press Ctrl-D to stop.", out)
        self.assertEqual(load_accounts(self.accounts)["my-bluesky"]["handle"], "maker.bsky.social")

    def test_three_blank_answers_stop_with_the_plain_message(self):
        with patch("builtins.input", return_value=""):
            code, _, err = self.cli("bluesky", stdin=TerminalText(""))
        self.assertEqual(code, 2)
        self.assertIn("Bluesky needs your handle", err)
        self.assertFalse(self.accounts.exists())

    def test_interactive_questions_never_ask_for_a_secret(self):
        answers = TerminalText("@mychannel\nmy_bot\ny\n")
        with patch("builtins.input", side_effect=lambda prompt: (prompts.append(prompt), answers.readline().strip())[1]):
            prompts: list[str] = []
            code, out, err = self.cli("telegram", stdin=answers)
        self.assertEqual(code, 0, err)
        self.assertEqual(load_accounts(self.accounts)["my-telegram"]["chat_id"], "@mychannel")
        for prompt in prompts:
            self.assertNotRegex(prompt.casefold(), r"password|token|secret|key")
        self.assertIn("REACHWEAVER_TELEGRAM_BOT_TOKEN", out)

    def test_interactive_cancel_writes_nothing(self):
        answers = TerminalText("maker.bsky.social\nn\n")
        with patch("builtins.input", side_effect=lambda prompt: answers.readline().strip()):
            code, out, _ = self.cli("bluesky", stdin=answers)
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", out)
        self.assertFalse(self.accounts.exists())

    def test_broken_existing_file_is_never_rewritten(self):
        self.accounts.parent.mkdir(parents=True)
        self.accounts.write_text('{"accounts": [{"id": "x", "platform": "bluesky"}]}', encoding="utf-8")
        before = self.accounts.read_bytes()
        code, _, err = self.cli("bluesky", "--handle", "maker.bsky.social")
        self.assertEqual(code, 2)
        self.assertEqual(self.accounts.read_bytes(), before)
        self.assertFalse(self.accounts.with_name("accounts.json.bak").exists())

    def test_wrong_account_guard_with_a_profile(self):
        data = profile_store.template(["work", "family"])
        profile_store.set_handle(data, "bluesky", "family", "fam.bsky.social", "claimed")
        profile_store.set_handle(data, "bluesky", "work", "work.bsky.social", "claimed")
        profile_store.save_profile(data, self.profile)
        code, _, err = self.cli("bluesky", "--handle", "fam.bsky.social", "--persona", "work")
        self.assertEqual(code, 2)
        self.assertIn("'family' persona", err)
        code, _, err = self.cli("bluesky", "--handle", "fam.bsky.social", "--persona", "school")
        self.assertEqual(code, 2)
        self.assertIn("not a persona in your profile", err)
        code, _, err = self.cli("bluesky", "--handle", "other.bsky.social", "--persona", "work")
        self.assertEqual(code, 2, "profile says the work username is work.bsky.social")
        self.assertFalse(self.accounts.exists())
        code, out, err = self.cli("bluesky", "--handle", "fam.bsky.social", "--persona", "family")
        self.assertEqual(code, 0, err)
        self.assertEqual(load_accounts(self.accounts)["my-bluesky"]["persona"], "family")
        code, out, err = self.cli("bluesky", "--handle", "work.bsky.social", "--id", "bsky-work")
        self.assertEqual(code, 0, err)
        self.assertIn("'work' persona", out)

    def test_works_inside_a_workspace_and_for_manual_metadata_networks(self):
        workspace = self.root / "space"
        (workspace / "config").mkdir(parents=True)
        (workspace / ".reachweaver-workspace").write_text("{}", encoding="utf-8")
        stdout = io.StringIO()
        with NetworkGuard() as guard, contextlib.redirect_stdout(stdout), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REACHWEAVER_ACCOUNTS", None)
            code = main(["--home", str(workspace), "account-add", "instagram", "--handle", "maker_demo",
                         "--persona", "work"])
            self.assertEqual(guard.attempts, [])
        self.assertEqual(code, 0)
        entry = load_accounts(workspace / "config" / "accounts.json")["my-instagram"]
        self.assertEqual(entry["handle"], "maker_demo")
        self.assertIn("no enabled Instagram adapter", stdout.getvalue())

    def test_fills_the_unedited_example_entry_in_place(self):
        example = Path(account_add.__file__).with_name("accounts.example.json")
        self.accounts.parent.mkdir(parents=True)
        self.accounts.write_bytes(example.read_bytes())
        code, out, err = self.cli("bluesky", "--handle", "maker.bsky.social")
        self.assertEqual(code, 0, err)
        self.assertIn("Filling in the example entry my-bluesky", out)
        loaded = load_accounts(self.accounts)
        self.assertEqual(list(loaded), ["my-bluesky", "my-mastodon", "my-telegram-channel", "my-linkedin"])
        self.assertEqual(loaded["my-bluesky"]["handle"], "maker.bsky.social")
        self.assertEqual(loaded["my-bluesky"]["env_prefix"], "REACHWEAVER_BSKY")
        self.assertEqual(self.accounts.with_name("accounts.json.bak").read_bytes(), example.read_bytes())
        code, out, err = self.cli("bluesky", "--handle", "second.bsky.social")
        self.assertEqual(code, 0, err)
        self.assertNotIn("Filling in", out, "an edited entry is never replaced")
        self.assertIn("my-bluesky-2", out)

    def test_interactive_confirmation_defaults_to_yes_and_names_the_next_step(self):
        prompts: list[str] = []
        answers = TerminalText("maker.bsky.social\n\n")
        with patch("builtins.input", side_effect=lambda prompt: (prompts.append(prompt), answers.readline().strip())[1]):
            code, out, err = self.cli("bluesky", stdin=answers)
        self.assertEqual(code, 0, err)
        self.assertTrue(prompts[-1].endswith("[Y/n]: "), prompts)
        self.assertEqual(load_accounts(self.accounts)["my-bluesky"]["handle"], "maker.bsky.social")
        # The run's --db/--accounts/--profile are repeated so the next step uses the same files (UX 2026-10-21).
        flags = f"--db {self.db} --accounts {self.accounts} --profile {self.profile}"
        self.assertIn(f"Next: reachweaver {flags} connect --guide --account my-bluesky", out)
        self.assertIn(f"reachweaver {flags} share --platform bluesky", out)
        self.assertIn(f"After that, write your first post: reachweaver {flags} draft --account my-bluesky", out)
        self.assertNotIn("Then draft and review as usual", out)
        # Wording only: the credential names are still printed, never a value (credential isolation unchanged).
        self.assertIn(f"Set these yourself with reachweaver {flags} connect --guide (it shows the exact lines), "
                      "never in accounts.json: REACHWEAVER_BSKY_IDENTIFIER, REACHWEAVER_BSKY_APP_PASSWORD", out)
        self.assertNotIn("systemd credentials", out)

    def test_default_paths_are_not_repeated_and_a_named_file_is(self):
        """--accounts equal to the default adds nothing; a named file is carried into every next step."""
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        out = io.StringIO()
        with NetworkGuard(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--accounts", "config/accounts.json", "account-add", "bluesky", "--handle",
                                   "maker.bsky.social"]), 0)
        self.assertIn("Next: reachweaver connect --guide --account my-bluesky", out.getvalue())
        out = io.StringIO()
        with NetworkGuard(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--accounts", "my accounts.json", "account-add", "mastodon", "--handle", "maker",
                                   "--server", "https://mastodon.example"]), 0)
        self.assertTrue((self.root / "my accounts.json").is_file(), "account-add honours the global --accounts")
        self.assertIn("Next: reachweaver --accounts 'my accounts.json' connect --guide --account my-mastodon",
                      out.getvalue())

    def test_output_and_file_never_hold_a_secret_shaped_field(self):
        self.assertEqual(self.cli("linkedin", "--field", "person_id=abc123", "--field", "linkedin_version=202610")[0], 0)
        stored = json.loads(self.accounts.read_text(encoding="utf-8"))["accounts"][0]
        self.assertEqual(sorted(stored), ["env_prefix", "id", "linkedin_version", "person_id", "platform"])


class AccountRemoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.accounts = self.root / "config" / "accounts.json"
        self.db = self.root / "state.sqlite"
        self.accounts.parent.mkdir(parents=True)
        self.accounts.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_RM_BSKY", "handle": "maker.bsky.social"},
            {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_RM_MASTODON",
             "server": "https://mastodon.example", "handle": "maker"}]}, indent=2), encoding="utf-8")

    def cli(self, *args, stdin=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with NetworkGuard() as guard, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch("sys.stdin", stdin or io.StringIO("")):
            code = main(["--db", str(self.db), "--accounts", str(self.accounts), "account-remove", *args])
            self.assertEqual(guard.attempts, [])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_removes_one_entry_keeps_a_backup_and_touches_nothing_else(self):
        before = self.accounts.read_bytes()
        env = {"REACHWEAVER_RM_BSKY_IDENTIFIER": "maker.bsky.social"}
        with patch.dict(os.environ, env):
            code, out, err = self.cli("my-bluesky", "--yes")
            self.assertEqual(os.environ["REACHWEAVER_RM_BSKY_IDENTIFIER"], "maker.bsky.social")
        self.assertEqual(code, 0, err)
        self.assertEqual(list(load_accounts(self.accounts)), ["my-mastodon"])
        self.assertEqual(self.accounts.with_name("accounts.json.bak").read_bytes(), before)
        self.assertIn("Removed account my-bluesky (Bluesky)", out)
        self.assertIn("Not touched: credentials, the database, approvals and disconnect state", out)
        self.assertIn("REACHWEAVER_RM_BSKY_IDENTIFIER", out)
        self.assertFalse(self.db.exists(), "account-remove never creates a database")

    def test_unknown_id_dry_run_and_missing_confirmation_write_nothing(self):
        before = self.accounts.read_bytes()
        code, _, err = self.cli("my-blusky", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("Unknown account 'my-blusky'", err)
        self.assertIn("my-bluesky, my-mastodon", err)
        code, out, _ = self.cli("my-bluesky", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        code, _, err = self.cli("my-bluesky")
        self.assertEqual(code, 2)
        self.assertIn("add --yes", err)
        self.assertEqual(self.accounts.read_bytes(), before)
        self.assertFalse(self.accounts.with_name("accounts.json.bak").exists())

    def test_terminal_confirmation_defaults_to_no(self):
        before = self.accounts.read_bytes()
        with patch("builtins.input", return_value=""):
            code, out, _ = self.cli("my-bluesky", stdin=TerminalText(""))
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", out)
        self.assertEqual(self.accounts.read_bytes(), before)
        with patch("builtins.input", return_value="y"):
            code, out, _ = self.cli("my-bluesky", stdin=TerminalText(""))
        self.assertEqual(code, 0)
        self.assertEqual(list(load_accounts(self.accounts)), ["my-mastodon"])

    def test_posts_of_the_account_are_kept_and_counted(self):
        from reachweaver.storage import add_post, connect
        with connect(str(self.db)) as db:
            add_post(db, "my-bluesky", "bluesky", "A draft that stays")
        size = self.db.stat().st_size
        code, out, err = self.cli("my-bluesky", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("1 post(s) in the local database belong to my-bluesky. They are kept", out)
        self.assertEqual(self.db.stat().st_size, size)
        with connect(str(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts WHERE account_id='my-bluesky'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
