"""`draft --account A --account B`: one separate draft per account, all or none; approval stays per post."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.storage import connect

ACCOUNTS = [
    {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_MD_BSKY", "handle": "alice.example.org"},
    {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_MD_MASTO",
     "server": "https://social.example.org", "handle": "alice"},
    {"id": "my-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_MD_TG", "chat_id": "@alice_channel"},
    {"id": "other-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_MD_BSKY2", "handle": "bob.example.org"},
]


class MultiDraftTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state" / "md.sqlite")
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": ACCOUNTS}), encoding="utf-8")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def rows(self):
        with connect(self.db) as con:
            return [dict(r) for r in con.execute("SELECT id, account_id, platform, status, content, campaign FROM posts")]

    def test_one_unapproved_draft_per_account(self):
        code, out, err = self.run_cli("draft", "--account", "my-bluesky", "--account", "my-mastodon",
                                      "--text", "Release notes are out", "--campaign", "launch")
        self.assertEqual(code, 0, err)
        rows = self.rows()
        self.assertEqual(sorted(r["account_id"] for r in rows), ["my-bluesky", "my-mastodon"])
        self.assertTrue(all(r["status"] == "draft" and r["campaign"] == "launch" for r in rows))
        self.assertEqual(len({r["id"] for r in rows}), 2)
        self.assertIn("Created 2 drafts, one per account; none is approved or published", out)
        for row in rows:
            self.assertIn(row["id"], out)
        self.assertIn("approve each one on its own", out.lower())

    def test_single_account_output_is_unchanged(self):
        code, out, _ = self.run_cli("draft", "--account", "my-bluesky", "--text", "Hello")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^Created draft [0-9a-f]{16} for my-bluesky; not published\n$")

    def test_one_invalid_account_creates_nothing(self):
        too_long = "x" * 400  # over Bluesky's 300 graphemes, under Mastodon's limit
        code, _, err = self.run_cli("draft", "--account", "my-mastodon", "--account", "my-bluesky", "--text", too_long)
        self.assertEqual(code, 2)
        self.assertIn("nothing created", err)
        self.assertIn("my-bluesky (bluesky)", err)
        self.assertEqual(self.rows(), [])

    def test_optout_on_one_platform_creates_nothing(self):
        optout = self.root / "optout.json"
        optout.write_text(json.dumps({"version": 1, "entries": [{"platform": "mastodon",
                                                                 "handle": "@blocked@social.example.org"}]}),
                          encoding="utf-8")
        code, _, err = self.run_cli("draft", "--account", "my-bluesky", "--account", "my-mastodon", "--text",
                                    "Thanks @blocked@social.example.org", "--optout", str(optout))
        self.assertEqual(code, 2)
        self.assertIn("my-mastodon", err)
        self.assertEqual(self.rows(), [])

    def test_refusals(self):
        cases = [
            (("--account", "my-bluesky", "--account", "other-bluesky"), "duplicate posting"),
            (("--account", "my-bluesky", "--account", "my-bluesky"), "named twice"),
            (("--account", "my-bluesky", "--account", "nobody"), "Unknown account(s) nobody"),
            (("--account", "my-bluesky", "--account", "my-mastodon", "--reply-to", "bluesky:at://x"), "exactly one"),
            (("--account", "my-bluesky", "--account", "my-mastodon", "--online"), "only with --reply-to"),
        ]
        for extra, message in cases:
            with self.subTest(message=message):
                code, _, err = self.run_cli("draft", *extra, "--text", "Hello")
                self.assertEqual(code, 2, err)
                self.assertIn(message, err)
        self.assertEqual(self.rows(), [])

    def test_a_database_error_midway_leaves_no_partial_set(self):
        from unittest.mock import patch
        from reachweaver import storage
        real, calls = storage.add_post, []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 2:
                raise storage.sqlite3.OperationalError("database is locked")
            return real(*a, **k)
        with patch("reachweaver.cli.add_post", side_effect=flaky):
            code, _, err = self.run_cli("draft", "--account", "my-bluesky", "--account", "my-mastodon", "--text", "Hi")
        self.assertEqual(code, 2)  # main() reports the locked database cleanly instead of a traceback
        self.assertIn("locked", err)
        self.assertEqual(self.rows(), [])  # review: the first draft is rolled back too

    def test_approval_is_still_one_post_at_a_time(self):
        self.run_cli("draft", "--account", "my-bluesky", "--account", "my-mastodon", "--text", "Two drafts")
        first, second = (r["id"] for r in self.rows())
        code, _, err = self.run_cli("approve", first, "--approver", "owner")
        self.assertEqual(code, 0, err)
        statuses = {r["id"]: r["status"] for r in self.rows()}
        self.assertEqual((statuses[first], statuses[second]), ("approved", "draft"))


if __name__ == "__main__":
    unittest.main()
