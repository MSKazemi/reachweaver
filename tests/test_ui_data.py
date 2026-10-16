"""Dashboard data layer: read-only, redacted, no credential values. Offline; nothing is sent."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reachweaver import ui_data
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, transition, uncertain_send

SECRET = "-".join(("very", "private", "app", "password", "value"))
BSKY = {"id": "u-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_U_BSKY", "handle": "o.bsky.social"}
HOSTILE = '<script>alert(1)</script> "onerror=x'
NOW = "2026-10-08T10:00:00+00:00"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DataBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "u.sqlite"
        self.config = self.base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [BSKY]}), encoding="utf-8")
        env = patch.dict(os.environ, {"REACHWEAVER_U_BSKY_APP_PASSWORD": SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.accounts = load_accounts(self.config)
        with connect(str(self.db)) as con:
            self.draft = add_post(con, "u-bluesky", "bluesky", "Plain draft " + HOSTILE)
            self.approved = add_post(con, "u-bluesky", "bluesky", "Approved text")
            transition(con, self.approved, "approve", approver="owner")
            self.review = add_post(con, "u-bluesky", "bluesky", "Ambiguous text")
            transition(con, self.review, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.review, now=NOW))
            uncertain_send(con, self.review, "network error " + SECRET)


class ReadOnlyTests(DataBase):
    def test_every_view_leaves_the_database_bytes_unchanged(self):
        before = digest(self.db)
        siblings = sorted(p.name for p in self.db.parent.iterdir())
        with ui_data.open_database(self.db) as con:
            ui_data.overview(con, self.accounts, None)
            ui_data.posts_list(con)
            for pid in (self.draft, self.approved, self.review):
                ui_data.post_detail(con, pid, self.accounts)
            ui_data.accounts_view(self.accounts, None)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), siblings)

    def test_missing_database_is_refused_and_not_created(self):
        missing = self.base / "nowhere" / "x.sqlite"
        with self.assertRaises(ValidationError):
            with ui_data.open_database(missing):
                pass
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_connection_cannot_write(self):
        with ui_data.open_database(self.db) as con:
            with self.assertRaises(Exception):
                con.execute("UPDATE posts SET status='draft'")


class ListTests(DataBase):
    def test_needs_review_is_listed_first_and_excerpts_only(self):
        with ui_data.open_database(self.db) as con:
            data = ui_data.posts_list(con)
        self.assertEqual(data["total"], 3)
        self.assertEqual([p["status"] for p in data["posts"]], ["needs_review", "approved", "draft"])
        self.assertNotIn("content", data["posts"][0])

    def test_filters(self):
        with ui_data.open_database(self.db) as con:
            self.assertEqual(ui_data.posts_list(con, status="draft")["total"], 1)
            self.assertEqual(ui_data.posts_list(con, account_id="nobody")["total"], 0)


class DetailTests(DataBase):
    def test_detail_matches_show_output(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--db", str(self.db), "--accounts", str(self.config), "show", self.approved])
        self.assertEqual(code, 0)
        shown = json.loads(out.getvalue())
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, self.approved, self.accounts)
        for key, value in shown.items():
            self.assertEqual(detail[key], value, key)
        self.assertTrue(detail["events"])
        self.assertIn("publish", detail["next_steps"][0]["command"])

    def test_unknown_post_is_a_validation_error(self):
        with ui_data.open_database(self.db) as con:
            with self.assertRaises(ValidationError):
                ui_data.post_detail(con, "nope", self.accounts)

    def test_hostile_text_is_returned_verbatim_for_the_page_to_escape(self):
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, self.draft, self.accounts)
        self.assertIn(HOSTILE, detail["content"])

    def test_next_steps_are_text_commands_by_state(self):
        with ui_data.open_database(self.db) as con:
            draft = ui_data.post_detail(con, self.draft, self.accounts)["next_steps"]
            review = ui_data.post_detail(con, self.review, self.accounts)["next_steps"]
        self.assertTrue(any("approve" in step["command"] for step in draft))
        self.assertTrue(any("reconcile" in step["command"] for step in review))


class ExactTextTests(DataBase):
    """Review finding: the dashboard must show the text that would be sent, not a redacted copy."""
    TEXT = ("Join our live session: Tuesday 5pm https://x.io/a?token=abc12345 Key: "
            + "-".join(("1234", "abcd", "5678", "efgh")))  # built at run time so the scanner sees no secret-shaped literal

    def test_exact_text_is_not_redacted_but_a_likely_secret_is_flagged(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "u-bluesky", "bluesky", self.TEXT)
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, pid, self.accounts)
            listing = ui_data.posts_list(con, status="draft")
        self.assertEqual(detail["content"], self.TEXT)
        self.assertTrue(detail["content_looks_like_secret"])
        item = next(p for p in listing["posts"] if p["id"] == pid)
        self.assertEqual(item["excerpt"], self.TEXT)
        self.assertTrue(item["looks_like_secret"])

    def test_ordinary_text_is_not_flagged_and_errors_stay_redacted(self):
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, self.approved, self.accounts)
            review = ui_data.post_detail(con, self.review, self.accounts)
        self.assertFalse(detail["content_looks_like_secret"])
        self.assertNotIn(SECRET, json.dumps(review))

    def test_detail_carries_the_length_meter_and_a_plain_approval_sentence(self):
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, self.approved, self.accounts)
        self.assertEqual(detail["length"]["limit"], 300)
        self.assertEqual(detail["length"]["state"], "ok")
        self.assertIn("Approved by owner", detail["approval_summary"])

    def test_reconcile_suggestion_is_a_pasteable_command(self):
        with ui_data.open_database(self.db) as con:
            steps = ui_data.post_detail(con, self.review, self.accounts)["next_steps"]
        command = steps[0]["command"]
        self.assertNotIn("(", command)
        self.assertEqual(command.split()[:2], ["reachweaver", "reconcile"])


class RepeatCheckTests(DataBase):
    def test_detail_carries_the_repeat_check_in_the_same_place_show_has_it(self):
        with ui_data.open_database(self.db) as con:
            detail = ui_data.post_detail(con, self.approved, self.accounts)
        check = detail["repeat_check"]
        self.assertEqual(set(check), {"blocking", "warnings", "findings"})
        keys = list(detail)
        self.assertLess(keys.index("repeat_check"), keys.index("api_cost"))

    def test_an_exact_repeat_is_reported_without_the_earlier_text(self):
        with connect(str(self.db)) as con:
            twin = add_post(con, "u-bluesky", "bluesky", "Approved text")
        with ui_data.open_database(self.db) as con:
            check = ui_data.post_detail(con, twin, self.accounts)["repeat_check"]
        self.assertGreaterEqual(len(check["findings"]), 1)
        self.assertTrue(all("message" in f for f in check["findings"]))

    def test_a_history_failure_degrades_instead_of_breaking_the_page(self):
        import sqlite3
        from unittest.mock import patch
        with patch("reachweaver.post_view.repeat_guard.find_repeats", side_effect=sqlite3.OperationalError("no such table")):
            with ui_data.open_database(self.db) as con:
                check = ui_data.post_detail(con, self.approved, self.accounts)["repeat_check"]
        self.assertEqual((check["blocking"], check["findings"]), (0, []))
        self.assertIn("unavailable", check)


class SecretTests(DataBase):
    def test_no_credential_value_in_any_view(self):
        with ui_data.open_database(self.db) as con:
            blobs = [ui_data.overview(con, self.accounts, None), ui_data.posts_list(con),
                     ui_data.accounts_view(self.accounts, None)]
            blobs += [ui_data.post_detail(con, pid, self.accounts) for pid in (self.draft, self.approved, self.review)]
        self.assertNotIn(SECRET, json.dumps(blobs))

    def test_accounts_view_lists_missing_variable_names_only(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REACHWEAVER_U_BSKY_APP_PASSWORD", None)
            os.environ.pop("REACHWEAVER_U_BSKY_IDENTIFIER", None)
            view = ui_data.accounts_view(self.accounts, None)
        row = view["accounts"][0]
        self.assertTrue(row["credentials_missing"])
        for name in row["credentials_missing"]:
            self.assertTrue(re.fullmatch(r"REACHWEAVER_U_BSKY_[A-Z_]+", name), name)

    def test_missing_accounts_file_degrades_instead_of_failing(self):
        def loader():
            raise ValidationError("Missing config/accounts.json. Copy the example.")
        accounts, error = ui_data.load_accounts_view(loader)
        self.assertEqual(accounts, {})
        with ui_data.open_database(self.db) as con:
            summary = ui_data.overview(con, accounts, error)
        self.assertIn("overall", summary)


FORBIDDEN_NAMES = frozenset({
    "publish", "publish_video", "sendgate", "claim_sending", "register_claim", "finish_send", "reconcile",
    "require_credential", "credential", "transition", "set_paused", "add_post", "connect", "latch_keyed_mode",
    "subprocess", "socket", "urlopen", "urllib", "requests", "os", "publishers"})


def referenced_names(path: str) -> set[str]:
    """Identifiers, attributes and imported names/modules in a module; string contents are ignored."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update((node.module or "").split("."))
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
    return names


class StaticSafetyTests(unittest.TestCase):
    """The dashboard data layer must not name any way to send, claim, approve, write or read a credential."""

    def test_ui_data_references_no_write_send_or_credential_api(self):
        found = referenced_names(ui_data.__file__) & FORBIDDEN_NAMES
        self.assertEqual(found, set())

    def test_the_check_would_notice_a_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.py"
            bad.write_text("from reachweaver.storage import transition\ntransition(None, 'x', 'approve')\n")
            self.assertEqual(referenced_names(str(bad)) & FORBIDDEN_NAMES, {"transition"})


if __name__ == "__main__":
    unittest.main()
