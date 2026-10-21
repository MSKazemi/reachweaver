"""`start`: read-only guidance by state. Offline; creates nothing and runs nothing."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path

from reachweaver import catalog, home, start
from reachweaver.accounts import (PLACEHOLDER_IGNORED_FIELDS, PLACEHOLDER_VALUES, load_accounts, placeholder_accounts,
                                  placeholder_fields)
from reachweaver.cli import main
from reachweaver.storage import add_post, connect, set_paused, store_metrics, transition
from test_ui_data import FORBIDDEN_NAMES, referenced_names

ACCOUNTS = '{"accounts":[{"id":"s-bsky","platform":"bluesky","env_prefix":"REACHWEAVER_S_BSKY","handle":"s.bsky.social"}]}'


class StartBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "s.sqlite"
        self.config = self.base / "accounts.json"

    def guide(self):
        return start.build_guide(str(self.db), str(self.config))

    def commands(self, guide):
        return [s["command"] for s in guide["steps"] if s["command"]]


class StateTests(StartBase):
    def in_folder(self, folder: Path, workspace: Path | None = None) -> None:
        """Run the guide from ``folder`` with ``workspace`` as the active workspace (None: not a workspace)."""
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.chdir(folder))
        old = home.root()
        home.set_root(workspace)
        stack.callback(home.set_root, old)

    def test_no_accounts_file_in_a_source_checkout_still_offers_the_example(self):
        guide = self.guide()  # the tests run from the repository root, where the example file exists
        self.assertEqual(guide["state"], "no_accounts_file")
        self.assertIn("cp config/accounts.example.json config/accounts.json", self.commands(guide))

    def test_no_accounts_file_outside_a_workspace_starts_with_the_browser_then_workspace_init(self):
        self.in_folder(self.base)
        guide = self.guide()
        self.assertEqual(guide["state"], "no_accounts_file")
        commands = self.commands(guide)
        self.assertEqual(commands[:3], [start.SHARE_WEB, start.SHARE_GUIDE, "reachweaver workspace --init ."])
        self.assertFalse(any(c.startswith("cp ") for c in commands), "no example file here to copy")
        self.assertIn("Easiest", guide["steps"][0]["label"])

    def test_no_accounts_file_inside_a_workspace_restores_it_with_workspace_init(self):
        root = self.base / "my space"
        root.mkdir()
        self.in_folder(self.base, workspace=root)
        commands = self.commands(self.guide())
        self.assertIn(f"reachweaver workspace --init '{root}'", commands)
        self.assertNotIn("reachweaver workspace --init .", commands)

    def test_a_fresh_workspace_reports_example_placeholders_instead_of_configured_accounts(self):
        from reachweaver.workspace import init_workspace
        init_workspace(self.base)
        self.in_folder(self.base, workspace=self.base)
        guide = start.build_guide(None, None)
        self.assertEqual(guide["state"], "no_database")
        self.assertIn("0 account(s) configured", guide["summary"])
        self.assertIn("4 account(s) still use example placeholders — edit config/accounts.json", guide["summary"])
        labels = [s["label"] for s in guide["steps"]]
        self.assertIn("4 account(s) still use example placeholders — edit config/accounts.json", labels)
        self.assertEqual(self.commands(guide)[:2], [start.SHARE_WEB, start.SHARE_GUIDE])
        self.assertFalse((self.base / "state" / "reachweaver.sqlite").exists())

    def test_placeholder_accounts_never_become_the_draft_suggestion(self):
        self.config.write_text((Path(__file__).resolve().parents[1] / "config/accounts.example.json")
                               .read_text(encoding="utf-8"), encoding="utf-8")
        with connect(str(self.db)):
            pass
        guide = self.guide()
        commands = self.commands(guide)
        self.assertFalse(any(c.startswith("reachweaver draft") for c in commands), commands)
        self.assertIn(start.SHARE_WEB, commands)

    def test_open_ideas_and_metric_snapshots_get_their_own_steps(self):
        from reachweaver import ideas
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        self.db.parent.mkdir(mode=0o700)
        ideas.add(ideas.inbox_path(self.db), "Write about the new release")
        guide = self.guide()  # no database yet: the idea still shows
        self.assertIn("reachweaver idea list --status open", self.commands(guide))
        with connect(str(self.db)) as con:
            post = add_post(con, "s-bsky", "bluesky", "hello")
            con.execute("UPDATE posts SET status='published', send_started_at='2026-10-20T09:00:00+00:00' WHERE id=?",
                        (post,))
        self.assertNotIn(start.REPORT, self.commands(self.guide()))
        with connect(str(self.db)) as con:
            store_metrics(con, post, "2026-10-21T09:00:00+00:00", "native-export", likes=3)
        guide = self.guide()
        self.assertIn(start.REPORT, self.commands(guide))
        self.assertIn("1 saved idea(s)", start.render(guide))

    def test_a_broken_accounts_file_is_reported_and_never_overwritten(self):
        self.config.write_text("{not json", encoding="utf-8")
        guide = self.guide()
        self.assertEqual(guide["state"], "accounts_invalid")
        self.assertNotIn("cp config/accounts.example.json config/accounts.json", self.commands(guide))
        self.assertIn("cannot be used", guide["summary"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--db", str(self.db), "--accounts", str(self.config), "start"])
        self.assertEqual(code, 1)

    def test_no_database_is_not_created(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        guide = self.guide()
        self.assertEqual(guide["state"], "no_database")
        self.assertIn("reachweaver init", self.commands(guide))
        self.assertFalse(self.db.parent.exists())

    def test_empty_database_suggests_a_first_draft(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        with connect(str(self.db)):
            pass
        guide = self.guide()
        self.assertEqual(guide["state"], "empty")
        self.assertTrue(any(c.startswith("reachweaver draft --account s-bsky") for c in self.commands(guide)))

    def test_drafts_then_queued_then_review_then_paused(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        with connect(str(self.db)) as con:
            post = add_post(con, "s-bsky", "bluesky", "hello")
        guide = self.guide()
        self.assertEqual(guide["state"], "drafts")
        self.assertIn(start.REVIEW, self.commands(guide))  # the guided owner-run loop, not list/show/check
        self.assertNotIn("reachweaver list --status draft", self.commands(guide))
        with connect(str(self.db)) as con:
            transition(con, post, "approve", approver="owner")
        guide = self.guide()
        self.assertEqual(guide["state"], "queued")
        self.assertIn("reachweaver publish", self.commands(guide))
        with connect(str(self.db)) as con:
            con.execute("UPDATE posts SET status='needs_review'")
        self.assertEqual(self.guide()["state"], "needs_review")
        with connect(str(self.db)) as con:
            set_paused(con, True)
        guide = self.guide()
        self.assertEqual(guide["state"], "needs_review")
        self.assertIn("reachweaver resume", self.commands(guide))
        self.assertIn("PAUSED", guide["summary"])

    def test_guidance_never_contains_an_approve_command(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        with connect(str(self.db)) as con:
            add_post(con, "s-bsky", "bluesky", "hello")
        text = start.render(self.guide())
        self.assertNotRegex(text, r"reachweaver approve|--live")
        self.assertIn("runs nothing", text)

    def test_the_database_is_not_modified(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        with connect(str(self.db)) as con:
            add_post(con, "s-bsky", "bluesky", "hello")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.guide()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)


class CliTests(StartBase):
    def test_command_prints_text_and_json(self):
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        for extra in ([], ["--json"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--db", str(self.db), "--accounts", str(self.config), "start", *extra])
            self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["state"], "no_database")
        self.assertFalse(self.db.exists())


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_COPIES = (ROOT / "config/accounts.example.json", ROOT / "reachweaver/accounts.example.json",
                  ROOT / ".claude/skills/reachweaver/assets/accounts.example.json")


class PlaceholderTests(unittest.TestCase):
    def test_the_placeholder_set_matches_every_identity_value_of_the_example_file(self):
        for path in EXAMPLE_COPIES:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                values = {value for account in json.loads(path.read_text(encoding="utf-8"))["accounts"]
                          for key, value in account.items()
                          if key not in PLACEHOLDER_IGNORED_FIELDS and isinstance(value, str)}
                self.assertEqual(values, set(PLACEHOLDER_VALUES))

    def test_every_example_account_is_a_placeholder_and_an_edited_one_is_not(self):
        accounts = load_accounts(EXAMPLE_COPIES[0])
        self.assertEqual(placeholder_accounts(accounts), list(accounts))
        edited = dict(accounts["my-bluesky"], handle="maria.bsky.social")
        self.assertEqual(placeholder_fields(edited), [])
        self.assertEqual(placeholder_fields(accounts["my-telegram-channel"]),
                         ["bot_username", "channel_username", "chat_id"])
        self.assertEqual(placeholder_fields({"handle": "  Example.BSKY.social "}), ["handle"])  # case/space-blind

    def test_settings_left_at_their_example_value_do_not_count(self):
        self.assertEqual(placeholder_fields({"id": "example", "platform": "mastodon", "visibility": "public",
                                             "server": "https://mastodon.social", "linkedin_version": "202609",
                                             "handle": "maria"}), [])


class CommandTests(StartBase):
    def test_every_suggested_command_parses_and_owner_steps_are_marked(self):
        from reachweaver.cli import make_parser
        from reachweaver import ideas
        self.config.write_text(ACCOUNTS, encoding="utf-8")
        self.db.parent.mkdir(mode=0o700)
        ideas.add(ideas.inbox_path(self.db), "An idea")
        with connect(str(self.db)) as con:
            add_post(con, "s-bsky", "bluesky", "hello")
            published = add_post(con, "s-bsky", "bluesky", "posted")
            con.execute("UPDATE posts SET status='published', send_started_at='2026-10-20T09:00:00+00:00' "
                        "WHERE id=?", (published,))
            store_metrics(con, published, "2026-10-21T09:00:00+00:00", "native-export", likes=1)
        parser = make_parser()
        seen = set()
        for guide in (self.guide(), start.build_guide(str(self.base / "none.sqlite"), str(self.base / "none.json"))):
            for command in self.commands(guide):
                if command.startswith("cp "):
                    continue
                parser.parse_args(shlex.split(command)[1:])
                seen.add(command)
        self.assertTrue(catalog.owner_runs(start.REVIEW))
        self.assertTrue(catalog.owner_runs(start.SHARE_WEB))
        self.assertFalse(catalog.owner_runs(start.REPORT))
        self.assertLessEqual({start.REVIEW, start.SHARE_WEB, start.REPORT, "reachweaver idea list --status open"}, seen)


class StaticTests(unittest.TestCase):
    def test_start_names_no_write_send_or_credential_api(self):
        self.assertEqual(referenced_names(start.__file__) & FORBIDDEN_NAMES, set())


if __name__ == "__main__":
    unittest.main()
