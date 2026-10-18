"""CLI wiring: --persona on draft/check/approve, account-route, profile, account-kit.

Synthetic data; temporary folders; no network and no live send.
"""
from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main, make_parser
from reachweaver.storage import connect
from test_no_network import NetworkGuard
from test_profile import sample

ACCOUNTS = [
    {"id": "work-bsky", "platform": "bluesky", "persona": "work", "env_prefix": "REACHWEAVER_PC_WB",
     "handle": "alex.work.example.org"},
    {"id": "fam-bsky", "platform": "bluesky", "persona": "family", "env_prefix": "REACHWEAVER_PC_FB",
     "handle": "alex.fam.example.org"},
    {"id": "work-masto", "platform": "mastodon", "persona": "work", "env_prefix": "REACHWEAVER_PC_WM",
     "server": "https://social.example.org", "handle": "alexwork"},
    {"id": "fam-masto", "platform": "mastodon", "persona": "family", "env_prefix": "REACHWEAVER_PC_FM",
     "server": "https://social.example.org", "handle": "alexfam"},
]
RAW_PII = ("alex@example.org", "+390000000000", "1990-02-03")


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state" / "pc.sqlite")
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": ACCOUNTS}), encoding="utf-8")
        self.profile = self.root / "profile.json"

    def run_cli(self, *argv, profile=True):
        out, err = io.StringIO(), io.StringIO()
        base = ["--db", self.db, "--accounts", str(self.accounts)]
        if profile:
            base += ["--profile", str(self.profile)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*base, *argv])
        return code, out.getvalue(), err.getvalue()

    def rows(self):
        with connect(self.db) as con:
            return [dict(r) for r in con.execute("SELECT id, account_id, status FROM posts")]

    def write_profile(self, data=None):
        self.profile.write_text(json.dumps(data or sample()), encoding="utf-8")


class PersonaOnPostCommandsTests(CliCase):
    def test_draft_to_the_wrong_persona_is_refused_with_the_account_to_switch_to(self):
        code, _, err = self.run_cli("draft", "--account", "fam-bsky", "--persona", "work", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("Wrong account for persona 'work'", err)
        self.assertIn("Switch to account work-bsky", err)
        self.assertIn("nothing created", err.casefold())
        self.assertEqual(self.rows(), [])

    def test_draft_to_the_right_persona_works(self):
        code, out, err = self.run_cli("draft", "--account", "work-bsky", "--persona", "work", "--text", "Hi")
        self.assertEqual(code, 0, err)
        self.assertIn("Created draft", out)

    def test_multi_draft_creates_nothing_when_one_account_is_the_wrong_persona(self):
        code, _, err = self.run_cli("draft", "--account", "work-bsky", "--account", "fam-masto",
                                    "--persona", "work", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("fam-masto", err)
        self.assertIn("Switch to account work-masto", err)
        self.assertEqual(self.rows(), [])

    def test_check_blocks_and_approve_refuses_a_draft_on_the_wrong_persona(self):
        code, out, _ = self.run_cli("draft", "--account", "fam-bsky", "--text", "Hi")
        self.assertEqual(code, 0)
        post_id = self.rows()[0]["id"]
        code, out, _ = self.run_cli("check", post_id, "--persona", "work")
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)
        self.assertIn("Wrong account for persona 'work'", out)
        code, _, err = self.run_cli("approve", post_id, "--persona", "work")
        self.assertEqual(code, 2)
        self.assertIn("Refusing to approve", err)
        self.assertEqual(self.rows()[0]["status"], "draft")
        code, out, _ = self.run_cli("check", post_id, "--persona", "family")
        self.assertEqual(code, 0)
        code, _, err = self.run_cli("approve", post_id, "--persona", "family")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.rows()[0]["status"], "approved")

    def test_default_persona_from_the_profile_applies_and_can_be_overridden(self):
        self.write_profile()  # preferences.default_persona = work
        code, _, err = self.run_cli("draft", "--account", "fam-bsky", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("persona 'work'", err)
        code, _, err = self.run_cli("draft", "--account", "fam-bsky", "--persona", "family", "--text", "Hi")
        self.assertEqual(code, 0, err)

    def test_nothing_changes_without_a_profile_or_a_flag(self):
        code, _, err = self.run_cli("draft", "--account", "fam-bsky", "--text", "Hi")
        self.assertEqual(code, 0, err)

    def test_an_invalid_profile_fails_closed_with_the_fix(self):
        self.profile.write_text("{not json", encoding="utf-8")
        code, _, err = self.run_cli("draft", "--account", "work-bsky", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("profile", err.casefold())
        self.assertEqual(self.rows(), [])

    def test_persona_flag_with_no_profile_uses_account_personas_only(self):
        code, _, err = self.run_cli("draft", "--account", "fam-bsky", "--persona", "work", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("Switch to account work-bsky", err)

    def test_an_account_whose_persona_is_unknown_is_refused_when_a_persona_is_asserted(self):
        data = [dict(a) for a in ACCOUNTS]
        del data[0]["persona"]
        self.accounts.write_text(json.dumps({"accounts": data}), encoding="utf-8")
        code, _, err = self.run_cli("draft", "--account", "work-bsky", "--persona", "work", "--text", "Hi")
        self.assertEqual(code, 2)
        self.assertIn("cannot tell", err)


class AccountRouteTests(CliCase):
    def test_route_text_and_exit_codes(self):
        code, out, _ = self.run_cli("account-route", "--platform", "bluesky", "--persona", "work")
        self.assertEqual(code, 0)
        self.assertIn("work-bsky", out)
        code, out, _ = self.run_cli("account-route", "--platform", "weibo", "--persona", "work")
        self.assertEqual(code, 1)
        self.assertIn("account-kit --platform weibo --persona work", out)

    def test_route_json_and_default_persona(self):
        self.write_profile()
        code, out, _ = self.run_cli("account-route", "--platform", "instagram", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual((data["persona"], data["status"]), ("work", "ok"))
        for raw in RAW_PII:
            self.assertNotIn(raw, out)

    def test_route_needs_a_persona(self):
        code, _, err = self.run_cli("account-route", "--platform", "bluesky")
        self.assertEqual(code, 2)
        self.assertIn("--persona", err)


class ProfileCommandTests(CliCase):
    def test_init_show_set_handle_check_flow(self):
        code, out, err = self.run_cli("profile", "--init", "--persona", "work", "--persona", "family")
        self.assertEqual(code, 0, err)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o600)
        code, _, err = self.run_cli("profile", "--init")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        code, out, err = self.run_cli("profile", "--set", "person.given_name=Alex", "--set",
                                      "person.email=alex@example.org", "--set", "person.phone=+390000000000",
                                      "--handle", "bluesky:work=alex.work.example.org")
        self.assertEqual(code, 0, err)
        self.assertNotIn("alex@example.org", out)  # saved values are never echoed
        code, out, _ = self.run_cli("profile", "--show")
        self.assertEqual(code, 0)
        self.assertNotIn("alex@example.org", out)
        self.assertNotIn("+390000000000", out)
        self.assertIn("a***@example.org", out)
        self.assertIn("alex.work.example.org", out)
        code, out, _ = self.run_cli("profile", "--check")
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)

    def test_bad_value_is_rejected_without_changing_the_file(self):
        self.write_profile()
        before = self.profile.read_text(encoding="utf-8")
        code, _, err = self.run_cli("profile", "--set", "person.email=not-an-email")
        self.assertEqual(code, 2)
        self.assertNotIn("not-an-email", err)
        self.assertEqual(self.profile.read_text(encoding="utf-8"), before)

    def test_check_reports_a_profile_that_disagrees_with_accounts(self):
        data = sample()
        data["handles"] = [{"platform": "bluesky", "persona": "work", "username": "someone.else", "status": "claimed"}]
        self.write_profile(data)
        self.profile.chmod(0o644)
        code, out, _ = self.run_cli("profile", "--check")
        self.assertEqual(code, 1)
        self.assertIn("someone.else", out)
        self.assertIn("chmod 600", out)

    def test_check_flags_an_account_persona_missing_from_the_profile(self):
        data = sample()
        del data["personas"]["family"]
        data["handles"] = [h for h in data["handles"] if h["persona"] != "family"]
        self.write_profile(data)
        code, out, _ = self.run_cli("profile", "--check")
        self.assertEqual(code, 1)
        self.assertIn("family", out)

    def test_one_mode_at_a_time(self):
        code, _, err = self.run_cli("profile")
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("profile", "--show", "--check")
        self.assertEqual(code, 2)

    def test_missing_profile_has_a_clear_message(self):
        code, _, err = self.run_cli("profile", "--show")
        self.assertEqual(code, 2)
        self.assertIn("profile --init", err)


class AccountKitCommandTests(CliCase):
    def test_plan_is_printed_masked_and_writes_nothing(self):
        self.write_profile()
        code, out, err = self.run_cli("account-kit", "--platform", "instagram", "--platform", "weibo")
        self.assertEqual(code, 0, err)
        for raw in RAW_PII:
            self.assertNotIn(raw, out)
        self.assertIn("Plan sha256", out)
        self.assertIn("weibo", out)
        self.assertIn("interface language zh-Hans", out)
        self.assertEqual(list(self.root.glob("kit*")), [])

    def test_one_approval_writes_all_packets(self):
        self.write_profile()
        code, out, _ = self.run_cli("account-kit", "--platform", "instagram", "--platform", "weibo", "--json")
        plan = json.loads(out)
        for raw in RAW_PII:
            self.assertNotIn(raw, out)
        sha = plan["plan_sha256"]
        target = self.root / "kit"
        code, out, err = self.run_cli("account-kit", "--platform", "instagram", "--platform", "weibo",
                                      "--out", str(target), "--approve-plan", sha[:12])
        self.assertEqual(code, 0, err)
        self.assertTrue((target / "weibo" / "SIGNUP.md").is_file())
        self.assertIn("created no account", out.casefold())

    def test_out_requires_the_approval_and_a_wrong_hash_writes_nothing(self):
        self.write_profile()
        target = self.root / "kit"
        code, _, err = self.run_cli("account-kit", "--platform", "instagram", "--out", str(target))
        self.assertEqual(code, 2)
        self.assertIn("--approve-plan", err)
        code, _, err = self.run_cli("account-kit", "--platform", "instagram", "--out", str(target),
                                    "--approve-plan", "0" * 12)
        self.assertEqual(code, 2)
        self.assertFalse(target.exists())
        code, _, err = self.run_cli("account-kit", "--platform", "instagram", "--approve-plan", "0" * 12)
        self.assertEqual(code, 2)

    def test_questions_for_a_thin_profile(self):
        data = sample()
        del data["person"]["email"]
        self.write_profile(data)
        code, out, _ = self.run_cli("account-kit", "--platform", "instagram", "--questions")
        self.assertEqual(code, 0)
        self.assertIn("person.email", out)
        code, out, _ = self.run_cli("account-kit", "--platform", "instagram")
        self.assertEqual(code, 1)
        self.assertIn("Answer 1 question", out)

    def test_persona_comes_from_the_default_and_errors_are_plain(self):
        self.write_profile()
        code, out, _ = self.run_cli("account-kit", "--platform", "bluesky")
        self.assertIn("persona 'work'", out)
        code, _, err = self.run_cli("account-kit", "--platform", "notanetwork")
        self.assertEqual(code, 2)
        code, _, err = self.run_cli("account-kit", "--platform", "bluesky", "--persona", "ghost")
        self.assertEqual(code, 2)

    def test_requires_a_profile(self):
        code, _, err = self.run_cli("account-kit", "--platform", "instagram", "--persona", "work")
        self.assertEqual(code, 2)
        self.assertIn("profile --init", err)


class NoNetworkTests(CliCase):
    def test_every_new_command_and_the_guard_work_with_sockets_refused(self):
        with NetworkGuard() as guard:
            self.assertEqual(self.run_cli("profile", "--init", "--persona", "work", "--persona", "family")[0], 0)
            self.assertEqual(self.run_cli("profile", "--set", "person.given_name=Alex", "--set",
                                          "person.email=alex@example.org", "--set", "personas.work.display_name=Alex",
                                          "--set", "personas.work.username_stems=alexw",
                                          "--handle", "bluesky:work=alex.work.example.org")[0], 0)
            for argv in (("profile", "--show"), ("profile", "--check"),
                         ("account-route", "--platform", "bluesky", "--persona", "work"),
                         ("account-kit", "--platform", "weibo", "--persona", "work"),
                         ("account-kit", "--platform", "weibo", "--persona", "work", "--questions"),
                         ("draft", "--account", "work-bsky", "--persona", "work", "--text", "hi")):
                with self.subTest(argv=argv):
                    code, _, err = self.run_cli(*argv)
                    self.assertIn(code, (0, 1), err)
        self.assertEqual(guard.attempts, [])


SETTINGS = Path(__file__).resolve().parents[1] / ".claude/settings.json"


@unittest.skipUnless(SETTINGS.is_file(), ".claude/settings.json is private-only and absent from the public checkout")
class ApprovalStaysHumanTests(unittest.TestCase):
    """The single plan approval is the person's: the project's Claude Code deny rule for `approve` covers it."""

    def test_the_plan_approval_flag_matches_the_agent_deny_rule(self):
        import fnmatch
        settings = json.loads(SETTINGS.read_text("utf-8"))
        rules = [r[len("Bash("):-1] for r in settings["permissions"]["deny"] if r.startswith("Bash(")]
        command = "reachweaver account-kit --persona work --platform weibo --out kit --approve-plan 0123456789ab"
        self.assertTrue(any(fnmatch.fnmatchcase(command, rule) for rule in rules))

    def test_plan_without_the_approval_flag_does_not_match_it(self):
        import fnmatch
        settings = json.loads(SETTINGS.read_text("utf-8"))
        rules = [r[len("Bash("):-1] for r in settings["permissions"]["deny"] if r.startswith("Bash(")]
        command = "reachweaver account-kit --persona work --platform weibo"
        self.assertFalse(any(fnmatch.fnmatchcase(command, rule) for rule in rules))


class HelpWiringTests(unittest.TestCase):
    def test_new_commands_are_registered_and_grouped(self):
        from reachweaver.cli_help import GROUPS
        grouped = {name for _, names in GROUPS for name in names}
        parser = make_parser()
        for name in ("profile", "account-route", "account-kit"):
            self.assertIn(name, grouped)
        text = parser.format_help()
        self.assertIn("--profile", text)


if __name__ == "__main__":
    unittest.main()
