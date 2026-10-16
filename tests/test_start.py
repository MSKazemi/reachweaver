"""`start`: read-only guidance by state. Offline; creates nothing and runs nothing."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver import start
from reachweaver.cli import main
from reachweaver.storage import add_post, connect, set_paused, transition
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
    def test_no_accounts_file(self):
        guide = self.guide()
        self.assertEqual(guide["state"], "no_accounts_file")
        self.assertIn("cp config/accounts.example.json config/accounts.json", self.commands(guide))

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
        self.assertEqual(self.guide()["state"], "drafts")
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


class StaticTests(unittest.TestCase):
    def test_start_names_no_write_send_or_credential_api(self):
        self.assertEqual(referenced_names(start.__file__) & FORBIDDEN_NAMES, set())


if __name__ == "__main__":
    unittest.main()
