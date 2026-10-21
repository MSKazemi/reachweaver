"""Agent interface: `context`, `describe`, the new --json outputs and stable error codes. Offline, synthetic."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver import catalog, context, error_codes, publish_preview
from reachweaver.cli import main, make_parser
from reachweaver.policy import ValidationError
from reachweaver.publishers import PublishError
from reachweaver.storage import add_post, connect, set_paused, transition
from test_ui_data import FORBIDDEN_NAMES, referenced_names

ACCOUNTS = ('{"accounts":[{"id":"a-bsky","platform":"bluesky","env_prefix":"REACHWEAVER_A_BSKY",'
            '"handle":"a.bsky.social"},{"id":"a-masto","platform":"mastodon","env_prefix":"REACHWEAVER_A_MASTO",'
            '"server":"https://mastodon.example","handle":"a"}]}')
CLEAN_ENV = {"REACHWEAVER_ALLOW_LIVE": "", "REACHWEAVER_ENV": ""}


def subcommands() -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in make_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "a.sqlite"
        self.accounts = self.base / "accounts.json"
        self.profile = self.base / "profile.json"
        patcher = mock.patch.dict("os.environ", CLEAN_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.accounts), "--profile", str(self.profile),
                         *argv])
        return code, out.getvalue(), err.getvalue()

    def post(self, text="hello agent", status="draft") -> str:
        with connect(str(self.db)) as con:
            pid = add_post(con, "a-bsky", "bluesky", text)
            if status in ("approved", "scheduled"):
                transition(con, pid, "approve", approver="owner")
            if status not in ("draft", "approved"):
                con.execute("UPDATE posts SET status=? WHERE id=?", (status, pid))
        return pid


class CatalogTests(unittest.TestCase):
    def test_every_subcommand_is_classified_and_nothing_is_stale(self):
        self.assertEqual(catalog.classification_problems(subcommands()), [],
                         "classify the new command in reachweaver/catalog.py COMMANDS")

    def test_owner_only_core(self):
        owner = catalog.owner_only_commands()
        for item in ("approve", "review", "reconcile", "resume", "reconnect", "publish --live", "connect --guide",
                     "account-kit --approve-plan", "approval-mode --confirmation"):
            self.assertIn(item, owner)
        self.assertNotIn("pause", owner)
        self.assertTrue(catalog.is_owner_only("reachweaver approve P1 --approver me"))
        self.assertTrue(catalog.is_owner_only("reachweaver publish --live --post-id P1"))
        self.assertFalse(catalog.is_owner_only("reachweaver publish --post-id P1"))
        self.assertTrue(catalog.owner_runs("reachweaver ui"))
        self.assertFalse(catalog.owner_runs("reachweaver list --status draft"))
        self.assertFalse(catalog.is_owner_only(None))

    def test_owner_only_flags_exist_on_their_commands(self):
        subs = subcommands()
        for name, meta in catalog.COMMANDS.items():
            options = {o for a in subs[name]._actions for o in a.option_strings}
            for flag in meta["owner_only_flags"]:
                self.assertIn(flag, options, f"{name} {flag}")

    def test_network_tag_covers_every_online_or_live_flag(self):
        for name, sub in subcommands().items():
            options = {o for a in sub._actions for o in a.option_strings}
            if options & {"--online", "--live"}:
                self.assertIn("network", catalog.COMMANDS[name]["tags"], name)

    def test_describe_covers_every_subcommand_and_matches_the_parser(self):
        code_out = io.StringIO()
        with contextlib.redirect_stdout(code_out):
            self.assertEqual(main(["describe", "--json"]), 0)
        document = json.loads(code_out.getvalue())
        names = [entry["name"] for entry in document["commands"]]
        self.assertEqual(sorted(names), sorted(subcommands()))
        by_name = {entry["name"]: entry for entry in document["commands"]}
        self.assertEqual(by_name["version"]["json"], "--json")
        self.assertEqual(by_name["journal"]["json"], "--format json")
        self.assertIsNone(by_name["pause"]["json"])
        self.assertEqual(by_name["publish"]["owner_only_flags"], ["--live"])
        for entry in document["commands"]:
            self.assertEqual(len({"read_only", "writes"} & set(entry["tags"])), 1, entry["name"])

    def test_describe_one_command_has_typed_flags_with_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["describe", "draft", "--json"]), 0)
        entry = json.loads(out.getvalue())["command"]
        flags = {f["name"]: f for f in entry["flags"]}
        self.assertTrue(flags["--account"]["required"] and flags["--account"]["repeatable"])
        self.assertEqual(flags["--file"]["type"], "path")
        self.assertEqual(flags["--online"]["type"], "flag")
        self.assertTrue(flags["--text"]["help"] is None or isinstance(flags["--text"]["help"], str))
        self.assertIn({"required": True, "flags": ["--text", "--file"]}, entry["exclusive_groups"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["describe", "calendar-plan", "--json"])
        flags = {f["name"]: f for f in json.loads(out.getvalue())["command"]["flags"]}
        self.assertEqual(flags["--days"]["default"], 14)
        self.assertEqual(flags["--days"]["type"], "int")

    def test_describe_unknown_command_is_not_found(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(["describe", "nope", "--json"]), 2)
        self.assertTrue(err.getvalue().startswith("ERROR: Unknown command"))
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "not_found")

    def test_describe_text_lists_every_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["describe"])
        for name in subcommands():
            self.assertRegex(out.getvalue(), rf"(?m)^{name}\s")

    def test_the_agent_modules_have_no_send_or_write_path(self):
        for module in (context, catalog, publish_preview):
            self.assertEqual(referenced_names(module.__file__) & (FORBIDDEN_NAMES - {"publish"}), set(),
                             module.__name__)


class ContextTests(Base):
    def test_empty_folder_creates_nothing(self):
        code, out, _ = self.run_cli("context", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())
        self.assertEqual(doc["state"], "no_accounts_file")
        self.assertIsNone(doc["posts"])
        self.assertIsNone(doc["safety"]["emergency_paused"])
        self.assertIn("publish --live", doc["owner_only_commands"]["commands"])
        self.assertIn("review", doc["owner_only_commands"]["commands"])  # #11 guided approval loop
        self.assertLess(len(out.encode()), 3000)

    def test_populated_state_is_summarised_compactly_without_secret_values(self):
        self.accounts.write_text(ACCOUNTS, encoding="utf-8")
        draft = self.post("a draft")
        approved = self.post("approved text", "approved")
        review = self.post("ambiguous", "needs_review")
        with connect(str(self.db)) as con:
            set_paused(con, True)
        before = self.db.read_bytes()
        secret = "s3cr3t-value-never-printed-0123456789"
        with mock.patch.dict("os.environ", {"REACHWEAVER_A_BSKY_IDENTIFIER": "a.bsky.social",
                                            "REACHWEAVER_A_BSKY_APP_PASSWORD": secret,
                                            "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "staging"}):
            code, out, _ = self.run_cli("context", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(self.db.read_bytes(), before, "context must not write the database")
        self.assertNotIn(secret, out)
        self.assertNotIn("staging", out)
        doc = json.loads(out)
        self.assertLess(len(out.encode()), 3000)
        self.assertTrue(doc["safety"]["emergency_paused"])
        self.assertEqual(doc["safety"]["live_gate"], {"allow_live_set": True, "env_is_prod": False,
                                                      "live_possible_in_this_shell": False})
        self.assertEqual(doc["posts"]["total"], 3)
        self.assertEqual(doc["needs_owner"]["approved_not_sent"]["ids"], [approved])
        self.assertEqual(doc["needs_owner"]["needs_review"]["ids"], [review])
        self.assertEqual(doc["needs_owner"]["drafts"], 1)
        accounts = {a["id"]: a for a in doc["accounts"]["items"]}
        self.assertEqual(accounts["a-bsky"]["missing"], [])
        self.assertEqual(accounts["a-masto"]["state"], "needs_credentials")
        self.assertFalse(accounts["a-bsky"]["placeholder"])
        self.assertEqual(doc["approval"]["confirmation"], "off")
        resume = [s for s in doc["next_actions"] if s["command"] == "reachweaver resume"]
        self.assertTrue(resume and resume[0]["owner_runs"])
        self.assertNotIn(draft, json.dumps(doc["owner_only_commands"]))

    def test_example_placeholder_accounts_are_flagged_without_changing_existing_keys(self):
        example = Path(__file__).resolve().parents[1] / "config" / "accounts.example.json"
        self.accounts.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        code, out, _ = self.run_cli("context", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["state"], "no_database")
        for item in doc["accounts"]["items"]:
            self.assertTrue(item["placeholder"], item["id"])
            self.assertLessEqual({"id", "platform", "adapter", "state", "persona", "missing"}, set(item))
        self.assertTrue(any(step["command"] == "reachweaver studio --open" and step["owner_runs"]
                            for step in doc["next_actions"]))
        code, text, _ = self.run_cli("context")
        self.assertIn("still an example placeholder", text)
        self.assertFalse(self.db.exists())

    def test_text_mode_is_a_short_summary(self):
        self.accounts.write_text(ACCOUNTS, encoding="utf-8")
        code, out, _ = self.run_cli("context")
        self.assertEqual(code, 0)
        self.assertIn("not created yet", out)
        self.assertIn("Owner only, never run by an agent:", out)
        self.assertFalse(self.db.exists())

    def test_plugin_hook_is_reported(self):
        with mock.patch("reachweaver.updater.install_kind", return_value="plugin"):
            update = context._update_notice()
        self.assertTrue(update["session_hook"])
        self.assertIn("do not run it again", update["advice"])


class JsonOutputTests(Base):
    def setUp(self):
        super().setUp()
        self.accounts.write_text(ACCOUNTS, encoding="utf-8")

    def test_draft_list_check_schedule_cancel(self):
        code, out, _ = self.run_cli("draft", "--account", "a-bsky", "--text", "json draft", "--json")
        self.assertEqual(code, 0)
        created = json.loads(out)["created"]
        self.assertEqual(created[0]["account"], "a-bsky")
        self.assertEqual(created[0]["status"], "draft")
        pid = created[0]["post_id"]
        code, out, _ = self.run_cli("draft", "--account", "a-bsky", "--account", "a-masto", "--text", "two", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([c["platform"] for c in json.loads(out)["created"]], ["bluesky", "mastodon"])
        code, out, _ = self.run_cli("list", "--json")
        rows = json.loads(out)
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), {"id", "account_id", "platform", "status", "scheduled_at", "remote_url"})
        code, out, _ = self.run_cli("check", pid, "--json")
        result = json.loads(out)
        self.assertEqual(code, 0 if result["ok"] else 1)
        self.assertEqual(result["post_id"], pid)
        self.assertIsInstance(result["findings"], list)
        code, out, _ = self.run_cli("unapprove", pid, "--json")
        self.assertEqual(code, 2)  # a draft cannot be unapproved; the error document is on stdout
        self.assertIn(json.loads(out)["error"]["code"], error_codes.CODES)
        with connect(str(self.db)) as con:
            transition(con, pid, "approve", approver="owner")
        code, out, _ = self.run_cli("schedule", pid, "--at", "2030-01-01T09:00:00+00:00", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "scheduled")
        code, out, _ = self.run_cli("cancel", pid, "--json")
        self.assertEqual(json.loads(out), {"post_id": pid, "status": "cancelled"})

    def test_text_output_without_json_is_unchanged(self):
        code, out, _ = self.run_cli("draft", "--account", "a-bsky", "--text", "plain")
        self.assertRegex(out, r"^Created draft \w+ for a-bsky; not published\n$")
        pid = out.split()[2]
        code, out, _ = self.run_cli("list")
        self.assertEqual(out, f"{pid} | a-bsky | bluesky | draft |  | \n")
        code, out, _ = self.run_cli("cancel", pid)
        self.assertEqual(out, f"Post {pid} -> cancelled\n")

    def test_draft_json_is_refused_with_reply_to_before_anything(self):
        code, out, err = self.run_cli("draft", "--account", "a-bsky", "--text", "x", "--reply-to",
                                      "bluesky:at://did:plc:x/app.bsky.feed.post/y", "--json")
        self.assertEqual(code, 2)
        self.assertIn("not available with --reply-to", err)
        self.assertEqual(json.loads(self.run_cli("list", "--json")[1]), [], "nothing may be created")

    def test_doctor_json_names_only(self):
        secret = "another-secret-value-0123456789abcdef"
        with mock.patch.dict("os.environ", {"REACHWEAVER_A_MASTO_TOKEN": secret}):
            code, out, _ = self.run_cli("doctor", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn(secret, out)
        doc = json.loads(out)
        self.assertEqual({c["name"] for c in doc["checks"]}, {"a-bsky", "a-masto"})
        self.assertEqual(doc["ready"], 1)

    def test_approval_mode_json_displays_and_refuses_a_change(self):
        code, out, _ = self.run_cli("approval-mode", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["confirmation"], "off")
        code, out, err = self.run_cli("approval-mode", "--json", "--max-age-hours", "5")
        self.assertEqual(code, 2)
        self.assertIn("ERROR: approval-mode --json only displays", err)
        code, out, _ = self.run_cli("approval-mode", "--json")
        self.assertIsNone(json.loads(out)["approval_max_age_hours"], "the refused change must not apply")

    def test_publish_json_is_a_dry_run_and_refused_with_live(self):
        pid = self.post("due text", "approved")
        code, out, _ = self.run_cli("publish", "--json")
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertTrue(doc["dry_run"])
        self.assertEqual(doc["due"], 1)
        self.assertEqual(doc["posts"][0]["post_id"], pid)
        self.assertTrue(doc["lines"][0].startswith("DRY RUN: 1 due"))
        code, out, err = self.run_cli("publish", "--json", "--live")
        self.assertEqual(code, 2)
        self.assertIn("dry run only", err)
        self.assertEqual(json.loads(out)["error"]["code"], "validation_error")

    def test_publish_preview_flags_problem_lines(self):
        doc = publish_preview.structure(["DRY RUN: 2 due approved posts; no requests sent",
                                         "  p1 bluesky a: 'x'", "    BLOCKED p1: opt-out", "  p2 mastodon b: 'y'",
                                         "Emergency pause is active"], 0)
        self.assertEqual([p["flagged"] for p in doc["posts"]], [True, False])
        self.assertTrue(doc["paused"])

    def test_import_metrics_json(self):
        pid = self.post("published", "published")
        csv_file = self.base / "m.csv"
        csv_file.write_text(f"post_id,observed_at,source,views\n{pid},2026-10-01T00:00:00+00:00,native,5\n",
                            encoding="utf-8")
        code, out, _ = self.run_cli("import-metrics", "--csv", str(csv_file), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["imported"], 1)


class ErrorCodeTests(Base):
    def test_codes_from_exception_classes_and_messages(self):
        cases = [(ValidationError("Unknown post id: x"), "not_found"),
                 (ValidationError("Missing config/accounts.json."), "not_found"),
                 (ValidationError("x already exists; never overwrites"), "already_exists"),
                 (ValidationError("Refusing to approve P: reason"), "refused"),
                 (ValidationError("Global emergency pause is active. Run 'resume' deliberately."), "paused"),
                 (ValidationError("Live publishing requires both --live and REACHWEAVER_ALLOW_LIVE=1"), "live_gate"),
                 (ValidationError("State database is not private; restrict it"), "permission"),
                 (ValidationError("db predates this version's schema (missing x)"), "database"),
                 (ValidationError("--limit must be 1..100"), "validation_error"),
                 (PublishError("boom"), "publish_error"), (PermissionError("no"), "permission"),
                 (FileNotFoundError("no"), "not_found"), (OSError("io"), "io_error"),
                 (json.JSONDecodeError("bad", "x", 0), "invalid_json"), (ValueError("v"), "invalid_value"),
                 (KeyError("k"), "invalid_input"), (RuntimeError("r"), "error")]
        for exc, code in cases:
            with self.subTest(exc=repr(exc)):
                self.assertEqual(error_codes.error_code(exc), code)
                self.assertIn(code, error_codes.CODES)

    def test_without_json_stdout_stays_empty_and_stderr_unchanged(self):
        code, out, err = self.run_cli("show", "missing-id")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("ERROR: Unknown post id: missing-id"))

    def test_with_json_stdout_gets_the_error_document(self):
        self.accounts.write_text(ACCOUNTS, encoding="utf-8")
        code, out, err = self.run_cli("check", "missing-id", "--json")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("ERROR: Unknown post id: missing-id"))
        self.assertEqual(json.loads(out), {"error": {"code": "not_found", "message": "Unknown post id: missing-id"}})


if __name__ == "__main__":
    unittest.main()
