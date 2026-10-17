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
from reachweaver.storage import (add_post, claim_sending, connect, finish_send, record_shared,
                                 set_approval_max_age_hours, store_metrics, transition, uncertain_send)

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


class JournalTests(DataBase):
    def setUp(self):
        super().setUp()
        with connect(str(self.db)) as con:  # a second account: the first is held by its needs_review post
            self.sent = add_post(con, "u-bluesky-2", "bluesky", "Sent text " + HOSTILE)
            transition(con, self.sent, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.sent, now=NOW))
            finish_send(con, self.sent, "at://did:x/post/1", "https://bsky.app/profile/o.bsky.social/post/1")
            self.by_hand = record_shared(con, platform="youtube", account_id=None, content="Shared by hand",
                                         images=[], video=None, links=[], posted_at=NOW,
                                         url="https://youtube.com/watch?v=x", note="note " + SECRET)

    def test_posted_and_hand_shared_entries_newest_first_without_secrets(self):
        with ui_data.open_database(self.db) as con:
            view = ui_data.journal_view(con)
        ids = [e["id"] for e in view["entries"]]
        # posted or possibly posted (needs_review), plus by hand; drafts and approved posts are not in the journal
        self.assertEqual(set(ids), {self.sent, self.review, self.by_hand})
        self.assertEqual(ids[0], self.sent)  # sent now, after the hand-shared post dated NOW
        sent = view["entries"][0]
        self.assertIn(HOSTILE, sent["excerpt"])  # returned as data; the page inserts it as text
        self.assertEqual((sent["source"], sent["approved_by"], sent["status"]), ("reachweaver", "owner", "published"))
        self.assertNotIn("text", sent)
        by_id = {e["id"]: e for e in view["entries"]}
        self.assertEqual(by_id[self.by_hand]["source"], "by_hand")
        self.assertEqual(by_id[self.review]["status"], "needs_review")
        self.assertNotIn(SECRET, json.dumps(view))
        self.assertFalse(view["truncated"])
        self.assertIn("Local record", view["note"])

    def test_account_filter_keeps_hand_shared_entries_without_an_account(self):
        with ui_data.open_database(self.db) as con:
            self.assertEqual(len(ui_data.journal_view(con, account="u-bluesky-2")["entries"]), 2)
            self.assertEqual(len(ui_data.journal_view(con, account="u-bluesky")["entries"]), 2)  # needs_review + by hand
            self.assertEqual(len(ui_data.journal_view(con, platform="youtube")["entries"]), 1)


class ScheduleTests(DataBase):
    def schedule(self, text, at):
        with connect(str(self.db)) as con:
            pid = add_post(con, "u-bluesky", "bluesky", text)
            transition(con, pid, "approve", approver="owner")
            transition(con, pid, "schedule", scheduled_at=at)
        return pid

    def test_overdue_upcoming_later_and_unscheduled_groups(self):
        overdue = self.schedule("Missed", "2026-10-08T09:59:00+00:00")
        soon = self.schedule("Soon", "2026-10-08T23:30:00-02:00")  # 01:30 UTC on the 9th: a day-boundary case
        edge = self.schedule("Day 13", "2026-10-21T09:00:00+00:00")
        far = self.schedule("Day 15", "2026-10-23T10:00:00+00:00")
        with ui_data.open_database(self.db) as con:
            view = ui_data.schedule_view(con, now=NOW)
        self.assertEqual([p["id"] for p in view["overdue"]], [overdue])
        self.assertEqual([p["id"] for p in view["upcoming"]], [soon, edge])
        self.assertEqual(view["upcoming"][0]["scheduled_at"], "2026-10-09T01:30:00+00:00")  # normalised to UTC
        self.assertEqual(view["later"], 1)
        self.assertNotIn(far, json.dumps(view))
        self.assertEqual([p["id"] for p in view["unscheduled"]], [self.approved])
        self.assertEqual(view["sending"], [])  # needs_review is the Queue's business, not the schedule's
        self.assertIsNone(view["approval_max_age_hours"])

    def test_an_approval_that_expires_before_its_time_is_flagged(self):
        pid = self.schedule("Late", "2026-10-30T10:00:00+00:00")
        with connect(str(self.db)) as con:
            set_approval_max_age_hours(con, 24)
        with ui_data.open_database(self.db) as con:
            view = ui_data.schedule_view(con, now=NOW, days=30)
            expired = ui_data.schedule_view(con, now="2030-01-01T00:00:00+00:00")
        item = next(p for p in view["upcoming"] if p["id"] == pid)
        self.assertTrue(item["expires_before_due"])
        self.assertTrue(item["approval_expires_at"])
        self.assertEqual(view["approval_max_age_hours"], 24)
        self.assertTrue(all(p["approval_expired"] for p in expired["overdue"] + expired["unscheduled"]))

    def test_errors_are_redacted_and_text_is_flagged_not_changed(self):
        with ui_data.open_database(self.db) as con:
            self.assertNotIn(SECRET, json.dumps(ui_data.schedule_view(con, now=NOW)))


class SafetyTests(DataBase):
    def view(self, root=None):
        with ui_data.open_database(self.db) as con:
            return ui_data.safety_view(con, self.db, self.accounts, self.config, self.base, root)

    def test_plain_language_ledger_state_and_no_marker_is_only_a_note(self):
        view = self.view()
        self.assertIn(view["ledger"]["status"], ui_data.LEDGER_SENTENCE)
        self.assertEqual(view["ledger"]["sentence"], ui_data.LEDGER_SENTENCE[view["ledger"]["status"]])
        self.assertEqual(view["integrity"], "ok")
        marker = next(c for c in view["checks"] if c["code"] == "not_a_marked_workspace")
        self.assertEqual(marker["level"], "info")
        self.assertNotIn(SECRET, json.dumps(view))

    @unittest.skipUnless(os.name == "posix", "permission bits")
    def test_a_database_other_users_can_read_is_reported(self):
        self.db.chmod(0o600)
        self.assertFalse(any(c["code"] == "readable_by_other_users" for c in self.view()["checks"]))
        self.db.chmod(0o644)  # review: the folder check knew, but the card dropped it
        finding = next(c for c in self.view()["checks"] if c["code"] == "readable_by_other_users")
        self.assertEqual(finding["level"], "warning")
        self.assertIn("chmod 600", finding["advice"])

    def test_ledger_problems_name_the_post_and_say_what_to_do(self):
        audit = {"status": "broken", "problems": [{"post_id": self.review, "reason": "approval_ledger_broken"},
                                                  {"post_id": self.approved,
                                                   "reason": "approval_replay_reapproval_required"}]}
        with patch("reachweaver.ui_data.ledger.audit", return_value=audit):
            view = self.view()
        self.assertEqual(view["ledger"]["status"], "broken")
        self.assertEqual([p["post_id"] for p in view["ledger"]["problems"]], [self.review, self.approved])
        self.assertIn("Approve it again", view["ledger"]["problems"][1]["sentence"])
        self.assertFalse(any(c["code"].startswith("approval_ledger_") for c in view["checks"]))

    def test_every_ledger_state_has_a_sentence(self):
        for state in ("ok", "absent", "not_checked", "broken", "unavailable"):
            self.assertTrue(ui_data.LEDGER_SENTENCE[state])

    def test_a_missing_attachment_of_an_unsent_post_is_reported(self):
        image = self.base / "pic.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        with connect(str(self.db)) as con:
            con.execute("UPDATE posts SET media_json=? WHERE id=?", (json.dumps(
                [{"source": "local", "path": str(image), "sha256": "0" * 64, "mime": "image/png", "bytes": 72,
                  "width": 1, "height": 1, "alt": "x", "focus": None}]), self.draft))
        image.unlink()
        codes = [c["code"] for c in self.view()["checks"]]
        self.assertIn("attachment_missing", codes)


class ResultsTests(DataBase):
    def setUp(self):
        super().setUp()
        with connect(str(self.db)) as con:  # a second account: the first is held by its needs_review post
            self.sent = add_post(con, "u-bluesky-2", "bluesky", "Sent " + HOSTILE, "launch")
            transition(con, self.sent, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.sent, now=NOW))
            finish_send(con, self.sent, "at://did:x/post/9", "https://bsky.app/profile/o/post/9")
            self.quiet = add_post(con, "u-bluesky-3", "bluesky", "Sent, never measured", "launch")
            transition(con, self.quiet, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.quiet, now=NOW))
            finish_send(con, self.quiet, "at://did:x/post/10", "https://bsky.app/profile/o/post/10")
            store_metrics(con, self.sent, "2030-01-01T00:00:00+00:00", "native_export", views=120, likes=4,
                          reposts="suppressed")
            store_metrics(con, self.sent, "2030-01-02T00:00:00+00:00", "native_export", views=150, likes=6)

    def test_latest_exact_values_and_unknown_is_never_zero(self):
        with ui_data.open_database(self.db) as con:
            view = ui_data.results_view(con)
        by_id = {p["id"]: p for p in view["posts"]}
        latest = by_id[self.sent]["latest"]
        self.assertEqual(latest["metrics"]["views"], {"state": "exact", "value": 150})
        self.assertEqual(latest["metrics"]["clicks"], {"state": "unknown", "value": None})
        self.assertIsNone(by_id[self.quiet]["latest"])  # not recorded, not zero
        self.assertEqual((view["shown"], view["with_snapshot"]), (2, 1))
        bluesky = next(p for p in view["platforms"] if p["platform"] == "bluesky")
        self.assertEqual(bluesky["metrics"]["views"]["value"], 150)
        self.assertEqual(bluesky["metrics"]["views"]["coverage"], "1/2")
        self.assertIsNone(bluesky["metrics"]["clicks"]["value"])
        self.assertIn(HOSTILE, by_id[self.sent]["excerpt"])  # data, escaped by the page
        self.assertNotIn(SECRET, json.dumps(view))

    def test_post_page_lists_every_snapshot_with_its_status(self):
        with ui_data.open_database(self.db) as con:
            results = ui_data.post_detail(con, self.sent, self.accounts)["results"]
            none = ui_data.post_detail(con, self.quiet, self.accounts)["results"]
        self.assertEqual([r["metrics"]["views"]["value"] for r in results], [120, 150])
        self.assertEqual(results[0]["metrics"]["reposts"]["state"], "suppressed")
        self.assertIsNotNone(results[0]["age_hours"])
        self.assertEqual(none, [])

    def test_filters(self):
        with ui_data.open_database(self.db) as con:
            self.assertEqual([p["id"] for p in ui_data.results_view(con, account="u-bluesky-3")["posts"]], [self.quiet])
            self.assertEqual(ui_data.results_view(con, platform="mastodon")["platforms"], [])


class CampaignTests(ResultsTests):
    def test_campaigns_are_grouped_with_status_counts(self):
        with ui_data.open_database(self.db) as con:
            view = ui_data.campaigns_view(con)
        launch = next(c for c in view["campaigns"] if c["campaign"] == "launch")
        self.assertEqual(launch["posts"], 2)
        self.assertEqual(launch["status_counts"], {"published": 2})
        self.assertEqual(launch["accounts"], ["u-bluesky-2", "u-bluesky-3"])
        self.assertEqual(view["posts_without_campaign"], 3)

    def test_campaign_detail_shows_each_post_and_its_exact_results(self):
        with ui_data.open_database(self.db) as con:
            detail = ui_data.campaign_detail(con, "launch")
            with self.assertRaises(ValidationError):
                ui_data.campaign_detail(con, "nope")
        by_id = {p["id"]: p for p in detail["posts"]}
        self.assertEqual(by_id[self.sent]["results"]["views"], 150)
        self.assertIsNone(by_id[self.sent]["results"]["reposts"])  # suppressed is not an exact value
        self.assertIsNone(by_id[self.quiet]["results"])
        self.assertIn("approved, scheduled and sent on its own", detail["note"])


class SuggestionTests(DataBase):
    def test_suggestions_leave_out_what_the_overview_already_lists(self):
        with ui_data.open_database(self.db) as con:
            view = ui_data.suggestions_view(con)
        self.assertTrue(all(a["kind"] != "publishing_safety" for a in view["actions"]))
        self.assertLessEqual(len(view["actions"]), ui_data.SUGGESTION_LIMIT)
        self.assertTrue(view["limitations"])
        self.assertNotIn(SECRET, json.dumps(view))


class NewViewsReadOnlyTests(JournalTests):
    def test_new_views_leave_the_database_and_folder_unchanged(self):
        before = digest(self.db)
        siblings = sorted(p.name for p in self.db.parent.iterdir())
        with ui_data.open_database(self.db) as con:
            ui_data.journal_view(con)
            ui_data.schedule_view(con, now=NOW)
            ui_data.safety_view(con, self.db, self.accounts, self.config, self.base, None)
            ui_data.suggestions_view(con)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), siblings)


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
