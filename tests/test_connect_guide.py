"""The guided connection flow: status table, walkthrough, and what it must never do (offline; no network).

``connect`` never takes a secret, never opens a browser, never posts and never touches the database unless the
owner confirms a Bluesky check. The online identity check itself is injected here, so no socket is ever opened.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import connect_guide as cg
from reachweaver.accounts import load_accounts
from reachweaver.capabilities import load_registry
from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sekret-value-that-must-never-be-printed-1234"
ACCOUNTS = [
    {"id": "blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_CG_BSKY", "handle": "me.bsky.social"},
    {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_CG_MASTO", "server": "https://mastodon.social",
     "handle": "alice@mastodon.social"},
    {"id": "tg", "platform": "telegram", "env_prefix": "REACHWEAVER_CG_TG", "chat_id": "@examplechannel",
     "channel_username": "examplechannel", "bot_username": "example_bot"},
    {"id": "x-acct", "platform": "x", "env_prefix": "REACHWEAVER_CG_X"},
]


class TTY(io.StringIO):
    """A stand-in terminal: the walkthrough requires both stdin and stdout to report isatty()."""

    def isatty(self):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "accounts.json"
        self.config.write_text(json.dumps({"accounts": ACCOUNTS}), encoding="utf-8")
        self.db = Path(self.tmp.name) / "state" / "r.sqlite"
        self.registry = load_registry()

    def accounts(self):
        return load_accounts(self.config)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()


class StatusTableTests(Base):
    def test_states_cover_missing_ready_manual_and_disconnected(self):
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET}
        with patch.dict(os.environ, env, clear=True):
            rows = {r["account"]: r for r in cg.account_rows(self.accounts(), self.registry, disconnected=["tg"])}
        self.assertEqual(rows["blue"]["state"], cg.NEEDS)
        self.assertEqual(rows["blue"]["missing"], ["REACHWEAVER_CG_BSKY_IDENTIFIER", "REACHWEAVER_CG_BSKY_APP_PASSWORD"])
        self.assertEqual(rows["masto"]["state"], cg.READY)
        self.assertEqual(rows["tg"]["state"], cg.DISCONNECTED)
        self.assertEqual(rows["x-acct"]["state"], cg.MANUAL)
        self.assertIn("manual-export", rows["x-acct"]["next"])

    def test_the_table_and_json_show_variable_names_only(self):
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET}
        with patch.dict(os.environ, env, clear=True):
            rows = cg.account_rows(self.accounts(), self.registry)
        rendered = cg.render_rows(rows) + json.dumps(rows)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("REACHWEAVER_CG_BSKY_APP_PASSWORD", rendered)

    def test_empty_account_list_explains_what_to_do(self):
        text = cg.render_rows([])
        self.assertIn("No accounts yet", text)
        self.assertIn("reachweaver account-add bluesky", text)
        self.assertIn("reachweaver workspace --init .", text)
        self.assertNotIn("accounts.example.json", text)


class WalkthroughTests(Base):
    def walk(self, answers, *, check=None, only=None, env=None, disconnected=()):
        said, asked = [], []
        queue = list(answers)

        def ask(prompt):
            asked.append(prompt)
            return queue.pop(0) if queue else "q"

        with patch.dict(os.environ, env or {}, clear=True):
            results = cg.run_guided(self.accounts(), self.registry, only=only, ask=ask, say=said.append,
                                    check=check or (lambda account: None), disconnected=disconnected)
        return results, "\n".join(said), asked

    def test_a_ready_account_is_checked_only_after_the_owner_says_yes(self):
        calls = []
        results, text, asked = self.walk(["y"], check=lambda a: calls.append(a["id"]),
                                         only="masto", env={"REACHWEAVER_CG_MASTO_TOKEN": SECRET})
        self.assertEqual(calls, ["masto"])
        self.assertEqual(results[0]["state"], cg.VERIFIED)
        self.assertEqual(len(asked), 1)
        self.assertIn("Contact Mastodon now", asked[0])
        self.assertIn("Verified", text)

    def test_no_or_enter_skips_without_any_request(self):
        for answer in ("n", "", "maybe"):
            with self.subTest(answer=answer):
                calls = []
                results, _, _ = self.walk([answer], check=lambda a: calls.append(a), only="masto",
                                          env={"REACHWEAVER_CG_MASTO_TOKEN": SECRET})
                self.assertEqual(calls, [])
                self.assertEqual(results[0]["state"], cg.SKIPPED)

    def test_q_stops_before_the_remaining_accounts(self):
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET, "REACHWEAVER_CG_TG_BOT_TOKEN": SECRET + "2"}
        calls = []
        results, text, _ = self.walk(["q"], check=lambda a: calls.append(a["id"]), env=env)
        # blue needs credentials (no prompt), masto is ready and the owner stops there
        self.assertEqual(calls, [])
        self.assertEqual([r["account"] for r in results], ["blue", "masto"])
        self.assertIn("Stopped", text)

    def test_a_failed_check_is_explained_in_plain_words(self):
        results, text, _ = self.walk(["y"], check=lambda a: "http_401", only="masto",
                                     env={"REACHWEAVER_CG_MASTO_TOKEN": SECRET})
        self.assertEqual(results[0]["state"], cg.FAILED)
        self.assertIn("expired, revoked or wrong", text)
        self.assertIn("http_401", cg.render_summary(results) + str(results))

    def test_missing_credentials_get_steps_names_and_a_safe_way_to_set_them(self):
        results, text, asked = self.walk([], only="blue")
        self.assertEqual(results[0]["state"], cg.NEEDS)
        self.assertEqual(asked, [])  # nothing is asked: no secret is ever requested
        self.assertIn("https://bsky.app/settings/app-passwords", text)
        self.assertIn("App Password", text)
        self.assertIn("read -r -s", text)  # hidden input, nothing lands in shell history
        self.assertIn("REACHWEAVER_CG_BSKY_APP_PASSWORD", text)
        self.assertIn("Never paste a password, token or code into Claude", text)

    def test_mastodon_page_uses_the_configured_server(self):
        _, text, _ = self.walk([], only="masto")
        self.assertIn("https://mastodon.social/settings/applications", text)

    def test_manual_and_disconnected_accounts_need_no_connection_and_no_prompt(self):
        _, text, asked = self.walk([], only="x-acct")
        self.assertEqual(asked, [])
        self.assertIn("No connection needed", text)
        _, text, asked = self.walk([], only="tg", disconnected=["tg"], env={"REACHWEAVER_CG_TG_BOT_TOKEN": SECRET})
        self.assertEqual(asked, [])
        self.assertIn("reconnect --account tg", text)

    def test_only_one_question_is_ever_asked_and_it_is_never_for_a_secret(self):
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET, "REACHWEAVER_CG_TG_BOT_TOKEN": SECRET + "2"}
        _, text, asked = self.walk(["y", "n"], env=env)
        self.assertEqual(len(asked), 2)
        for prompt in asked:
            self.assertIn("[y = yes, n = skip, q = stop]", prompt)
            self.assertNotRegex(prompt.lower(), r"password|token|secret|enter your")
        self.assertNotIn(SECRET, text)

    def test_summary_points_to_the_approval_step_and_never_skips_it(self):
        results, _, _ = self.walk(["y"], only="masto", env={"REACHWEAVER_CG_MASTO_TOKEN": SECRET})
        summary = cg.render_summary(results)
        self.assertIn("approve POST_ID", summary)
        self.assertIn("still needs your approval", summary)


class CommandTests(Base):
    def test_status_table_is_offline_and_creates_nothing(self):
        code, out, err = self.run_cli("connect")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("credentials missing: REACHWEAVER_CG_BSKY_IDENTIFIER", out)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())

    def test_json_status_and_account_filter(self):
        code, out, _ = self.run_cli("connect", "--json", "--account", "masto")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([r["account"] for r in data["accounts"]], ["masto"])
        self.assertIn("no request was made", data["note"])

    def test_guide_needs_a_person_at_a_terminal(self):
        code, out, err = self.run_cli("connect", "--guide")
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", err)
        self.assertEqual(out, "")

    def test_guide_and_json_cannot_be_combined_and_unknown_account_is_refused(self):
        self.assertEqual(self.run_cli("connect", "--guide", "--json")[0], 2)
        code, _, err = self.run_cli("connect", "--account", "nope")
        self.assertEqual(code, 2)
        self.assertIn("Unknown account", err)

    def test_the_walkthrough_runs_with_a_terminal_and_makes_no_request_without_a_yes(self):
        answers = iter(["n"])
        out = TTY()
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET}
        with patch.dict(os.environ, env, clear=True), patch("sys.stdin", TTY()), contextlib.redirect_stdout(out), \
                patch("builtins.input", lambda prompt="": next(answers, "q")), \
                patch("reachweaver.cli.check_connection", side_effect=AssertionError("no request without y")):
            code = main(["--db", str(self.db), "--accounts", str(self.config), "connect", "--guide", "--account", "masto"])
        self.assertEqual(code, 0)
        self.assertIn("Skipped", out.getvalue())
        self.assertNotIn(SECRET, out.getvalue())
        self.assertFalse(self.db.exists())

    def test_a_yes_runs_the_same_read_only_check_as_connect_check(self):
        out = TTY()
        env = {"REACHWEAVER_CG_MASTO_TOKEN": SECRET}
        with patch.dict(os.environ, env, clear=True), patch("sys.stdin", TTY()), contextlib.redirect_stdout(out), \
                patch("builtins.input", lambda prompt="": "y"), \
                patch("reachweaver.cli.check_connection") as checked:
            code = main(["--db", str(self.db), "--accounts", str(self.config), "connect", "--guide", "--account", "masto"])
        self.assertEqual(code, 0)
        checked.assert_called_once()
        self.assertIn("Verified", out.getvalue())


class NoAccountsTests(unittest.TestCase):
    """The natural first command, before any account exists: next steps (exit 0), and in a terminal an offer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "config" / "accounts.json"
        self.db = Path(self.tmp.name) / "state" / "r.sqlite"

    def run_cli(self, *argv, answers=None):
        out, err = TTY(), io.StringIO()
        queue = list(answers or [])

        def fake_input(prompt=""):
            out.write(prompt)
            if not queue:
                raise EOFError
            return queue.pop(0)

        terminal = patch("sys.stdin", TTY()) if answers is not None else patch("sys.stdin", io.StringIO(""))
        with patch.dict(os.environ, {}, clear=True), terminal, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), patch("builtins.input", fake_input), \
                patch("reachweaver.cli.check_connection", side_effect=AssertionError("no request")):
            code = main(["--db", str(self.db), "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_no_accounts_file_is_a_status_not_an_error(self):
        code, out, err = self.run_cli("connect")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("No accounts yet, so there is nothing to connect.", out)
        # The run's own --db/--accounts are repeated, so following the step fills the same file (UX 2026-10-21).
        self.assertIn(f"reachweaver --db {self.db} --accounts {self.config} account-add bluesky", out)
        self.assertIn(f"--accounts {self.config} account-add mastodon (or telegram, linkedin)", out)
        self.assertNotIn("workspace --init", out, "a workspace would not create the named accounts file")
        self.assertIn("Posting by hand needs no account: reachweaver share --guide", out)
        self.assertIn(cg.DOCS_BASE + "docs/ACCOUNT-ONBOARDING.md", out)
        self.assertNotIn("Add one now?", out, "no question without a terminal")
        self.assertFalse(self.config.exists())
        self.assertFalse(self.db.exists())

    def test_an_empty_accounts_list_and_json_and_guide_without_a_terminal(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{"accounts": []}', encoding="utf-8")
        code, out, _ = self.run_cli("connect")
        self.assertEqual(code, 0)
        self.assertIn("No accounts yet", out)
        code, out, _ = self.run_cli("connect", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["accounts"], [])
        code, out, _ = self.run_cli("connect", "--guide")
        self.assertEqual(code, 0)
        self.assertIn("No accounts yet", out)

    def test_a_broken_accounts_file_still_stops(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("not json", encoding="utf-8")
        self.assertEqual(self.run_cli("connect")[0], 2)

    def test_in_a_terminal_it_offers_the_account_add_flow_then_the_guide(self):
        code, out, err = self.run_cli("connect", "--guide", answers=["blusky", "bluesky", "", "me.bsky.social", ""])
        self.assertEqual(code, 0, err)
        self.assertIn("Did you mean bluesky?", out)
        self.assertIn("Bluesky needs your handle", out)  # a blank answer asks again
        self.assertEqual(load_accounts(self.config)["my-bluesky"]["handle"], "me.bsky.social")
        self.assertIn("Continuing with the connection steps for my-bluesky.", out)
        self.assertIn("read -r -s -p 'REACHWEAVER_BSKY_APP_PASSWORD: '", out)
        self.assertIn("How to keep them safely: " + cg.DOCS_BASE + "docs/ACCOUNT-ONBOARDING.md", out)
        self.assertNotIn("More detail (online): " + cg.DOCS_BASE + "docs/ACCOUNT-ONBOARDING.md", out)  # once is enough
        for prompt in re.findall(r"[^\n]*\? [^\n]*", out):
            self.assertNotRegex(prompt.lower(), r"password:|token:|secret:")
        self.assertFalse(self.db.exists())

    def test_enter_skips_the_offer_and_writes_nothing(self):
        code, out, _ = self.run_cli("connect", answers=[""])
        self.assertEqual(code, 0)
        self.assertIn("Add one now? Which network?", out)
        self.assertFalse(self.config.exists())

    def test_offer_reasks_then_gives_up(self):
        said = []
        self.assertIsNone(cg.offer_first_account(["bluesky"], ask=lambda p: "nope", say=said.append))
        self.assertEqual(len(said), cg.FIRST_ACCOUNT_ATTEMPTS + 1)
        self.assertEqual(cg.offer_first_account(["bluesky"], ask=lambda p: "BSKY", say=said.append), "bluesky")


class BoundaryTests(unittest.TestCase):
    def test_module_has_no_way_to_read_a_secret_open_a_browser_or_touch_the_network(self):
        text = (ROOT / "reachweaver/connect_guide.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("getpass", "os.environ", "getenv", "webbrowser", "subprocess", "socket", "urllib",
                          "selenium", "playwright", "keyring", "sqlite3", "open("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)

    def test_guide_text_for_every_platform_names_an_official_page_and_a_doc_that_exists(self):
        for platform, guide in cg.GUIDES.items():
            with self.subTest(platform=platform):
                self.assertTrue(guide["page"].startswith(("https://", "{server}")))
                self.assertTrue((ROOT / guide["doc"]).is_file(), guide["doc"])
                self.assertGreaterEqual(len(guide["steps"]), 3)


if __name__ == "__main__":
    unittest.main()
