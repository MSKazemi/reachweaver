"""Wrong-account guard: a post meant for one persona must not go to another persona's account.

Synthetic data only; pure offline functions.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reachweaver import persona_guard
from reachweaver.accounts import load_accounts
from reachweaver.policy import ValidationError
from test_profile import sample

ACCOUNTS = {
    "ig-work": {"id": "ig-work", "platform": "instagram", "persona": "work", "handle": "alex.work",
                "env_prefix": "REACHWEAVER_IGW"},
    "ig-family": {"id": "ig-family", "platform": "instagram", "persona": "family", "handle": "alex.fam",
                  "env_prefix": "REACHWEAVER_IGF"},
    "ig-plain": {"id": "ig-plain", "platform": "instagram", "handle": "alex.work", "env_prefix": "REACHWEAVER_IGP"},
    "ig-other": {"id": "ig-other", "platform": "instagram", "handle": "someone.else", "env_prefix": "REACHWEAVER_IGO"},
    "bsky-work": {"id": "bsky-work", "platform": "bluesky", "persona": "work", "handle": "alex.bsky.social",
                  "env_prefix": "REACHWEAVER_BSKY"},
}


class CheckAccountTests(unittest.TestCase):
    def check(self, account_id, persona, profile="sample"):
        return persona_guard.check_account(ACCOUNTS[account_id], persona, ACCOUNTS,
                                           sample() if profile == "sample" else profile)

    def test_matching_persona_passes(self):
        self.assertIsNone(self.check("ig-work", "work"))
        self.assertIsNone(self.check("ig-family", "family"))

    def test_wrong_persona_names_the_account_to_switch_to(self):
        message = self.check("ig-family", "work")
        self.assertIn("ig-family", message)
        self.assertIn("'family'", message)
        self.assertIn("Switch to account ig-work", message)
        self.assertIn("@alex.work", message)

    def test_wrong_persona_without_a_connected_account_names_the_profile_username(self):
        accounts = {"ig-family": ACCOUNTS["ig-family"]}
        message = persona_guard.check_account(accounts["ig-family"], "work", accounts, sample())
        self.assertIn("@alex.work", message)
        self.assertIn("not connected", message)
        self.assertIn("accounts.json", message)

    def test_no_account_and_no_handle_points_to_account_kit(self):
        accounts = {"ig-family": ACCOUNTS["ig-family"]}
        message = persona_guard.check_account(accounts["ig-family"], "work", accounts, None)
        self.assertIn("account-kit", message)
        self.assertIn("--platform instagram --persona work", message)

    def test_account_without_persona_is_matched_by_username_through_the_profile(self):
        self.assertIsNone(self.check("ig-plain", "work"))  # its handle is the work username
        message = self.check("ig-plain", "family")
        self.assertIn("work", message)  # its username belongs to the work persona

    def test_unknown_account_is_refused_with_the_fix(self):
        message = self.check("ig-other", "work")
        self.assertIn("cannot tell", message)
        self.assertIn('"persona": "work"', message)

    def test_without_a_profile_only_the_account_persona_counts(self):
        self.assertIsNone(self.check("ig-work", "work", profile=None))
        self.assertIn("cannot tell", self.check("ig-plain", "work", profile=None))

    def test_unknown_persona_is_refused(self):
        self.assertIn("not a persona", self.check("ig-work", "ghost"))
        with self.assertRaises(ValidationError):
            persona_guard.check_account(ACCOUNTS["ig-work"], "Bad!", ACCOUNTS, None)

    def test_comparison_ignores_case_and_leading_at(self):
        account = dict(ACCOUNTS["ig-plain"], handle="@Alex.Work")
        self.assertIsNone(persona_guard.check_account(account, "work", {"x": account}, sample()))

    def test_telegram_style_channel_username_counts_as_the_account_username(self):
        account = {"id": "tg", "platform": "telegram", "persona": "work", "channel_username": "alexchan"}
        self.assertEqual(persona_guard.account_username(account), "alexchan")
        self.assertIsNone(persona_guard.account_username({"id": "x", "platform": "mastodon"}))


class RouteTests(unittest.TestCase):
    def test_ok_with_account_and_profile(self):
        route = persona_guard.route("instagram", "work", ACCOUNTS, sample())
        self.assertEqual(route["status"], "ok")
        # ig-plain has no persona field but uses the work username, so the profile places it there too
        self.assertEqual([a["id"] for a in route["accounts"]], ["ig-work", "ig-plain"])
        self.assertEqual(route["profile_handle"]["username"], "alex.work")

    def test_profile_only_is_ok_but_says_not_connected(self):
        route = persona_guard.route("instagram", "work", {}, sample())
        self.assertEqual(route["status"], "ok")
        self.assertIn("not connected", route["advice"])

    def test_missing_points_to_account_kit(self):
        route = persona_guard.route("weibo", "work", ACCOUNTS, sample())
        self.assertEqual(route["status"], "missing")
        self.assertIn("account-kit --platform weibo --persona work", route["advice"])

    def test_planned_or_unavailable_handle_is_not_a_username_to_use(self):
        data = sample()
        data["handles"][0]["status"] = "planned"
        route = persona_guard.route("instagram", "work", {}, data)
        self.assertEqual(route["status"], "missing")
        self.assertIn("planned", route["advice"])

    def test_conflict_when_profile_and_account_disagree(self):
        data = sample()
        data["handles"][0]["username"] = "different.name"
        route = persona_guard.route("instagram", "work", ACCOUNTS, data)
        self.assertEqual(route["status"], "conflict")
        self.assertIn("different.name", route["advice"])
        self.assertIn("alex.work", route["advice"])

    def test_platform_alias_and_unknown_inputs(self):
        self.assertEqual(persona_guard.route("facebook_pages", "work", {}, None)["platform"], "facebook")
        with self.assertRaises(ValidationError):
            persona_guard.route("notanetwork", "work", {}, None)
        with self.assertRaises(ValidationError):
            persona_guard.route("instagram", "Bad!", {}, None)

    def test_route_output_has_no_pii(self):
        text = json.dumps(persona_guard.route("instagram", "work", ACCOUNTS, sample()))
        for raw in ("alex@example.org", "+390000000000", "1990-02-03"):
            self.assertNotIn(raw, text)


class AccountsFileTests(unittest.TestCase):
    def write(self, extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "accounts.json"
        entry = {"id": "bsky-work", "platform": "bluesky", "env_prefix": "REACHWEAVER_BSKY", "handle": "a.bsky.social"}
        entry.update(extra)
        path.write_text(json.dumps({"accounts": [entry]}), encoding="utf-8")
        return path

    def test_persona_is_accepted_on_an_account(self):
        self.assertEqual(load_accounts(self.write({"persona": "work"}))["bsky-work"]["persona"], "work")

    def test_persona_must_have_the_id_shape(self):
        for bad in ("Work", "w", "has space", 7, ""):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                load_accounts(self.write({"persona": bad}))


if __name__ == "__main__":
    unittest.main()
