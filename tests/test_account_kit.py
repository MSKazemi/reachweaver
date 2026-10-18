"""Account kit: one reviewable plan, one approval, local sign-up packets. It signs nobody up.

Synthetic data only. No browser, form, credential, database or network. The owner decision of 2026-10-16/17
(no browser or agent driving of platform sites) is pinned: nothing here recommends or enables it.
"""
from __future__ import annotations

import json
import re
import stat
import tempfile
import unittest
from pathlib import Path

from reachweaver import account_kit, distribution
from reachweaver.account_advisor import CHANNELS
from reachweaver.policy import ValidationError
from test_profile import sample

ROOT = Path(__file__).resolve().parents[1]
RAW_PII = ("alex@example.org", "+390000000000", "1990-02-03")
CHROME = re.compile(r"(?i)chrome|playwright|selenium|puppeteer|headless")


class RegistryTests(unittest.TestCase):
    def test_registry_is_valid(self):
        self.assertEqual(account_kit.validate_registry(account_kit.load_registry()), [])

    def test_every_advisor_platform_has_a_spec_and_a_url(self):
        for platform in CHANNELS:
            with self.subTest(platform=platform):
                spec = account_kit.platform_spec(platform)
                self.assertTrue(spec["starting_url"].startswith("https://"))
                self.assertIn("display_name", spec["may_ask"])

    def test_every_locale_with_a_spec_has_a_glossary_or_is_english(self):
        registry = account_kit.load_registry()
        for platform in CHANNELS:
            locale = account_kit.platform_spec(platform)["ui_locale"]
            with self.subTest(platform=platform):
                self.assertTrue(locale == "en" or locale in registry["glossary"])

    def test_minimisation_phone_and_birth_date_only_where_listed(self):
        self.assertNotIn("phone", account_kit.platform_spec("bluesky")["may_ask"])
        self.assertNotIn("birth_date", account_kit.platform_spec("github")["may_ask"])
        self.assertIn("birth_date", account_kit.platform_spec("instagram")["may_ask"])
        self.assertIn("phone", account_kit.platform_spec("weibo")["may_ask"])

    def test_chinese_platforms_use_chinese_labels_and_nicknames(self):
        for platform in ("weibo", "xiaohongshu", "douyin", "bilibili", "wechat", "zhihu"):
            spec = account_kit.platform_spec(platform)
            with self.subTest(platform=platform):
                self.assertEqual(spec["ui_locale"], "zh-Hans")
                self.assertEqual(spec["username_kind"], "nickname")
                self.assertTrue(spec["glossary"])
                self.assertIn("chinese", spec["region_note"].casefold())

    def test_alias_ids_resolve(self):
        self.assertEqual(account_kit.advisor_id("facebook"), "facebook_pages")
        self.assertEqual(account_kit.advisor_id("devto"), "dev")
        self.assertEqual(account_kit.advisor_id("weibo"), "weibo")

    def test_no_browser_agent_wording_anywhere_in_the_feature(self):
        for path in (ROOT / "reachweaver/account_kit.py", ROOT / "reachweaver/signup_registry.json",
                     ROOT / "reachweaver/persona_guard.py", ROOT / "reachweaver/profile.py"):
            with self.subTest(path=path.name):
                self.assertIsNone(CHROME.search(path.read_text(encoding="utf-8")))

    def test_module_cannot_send(self):
        self.assertIsNone(account_kit.SEND_PATH)


class PacketTests(unittest.TestCase):
    def test_handle_platform_packet(self):
        packet = account_kit.build_packet(sample(), "work", "instagram")
        fields = packet["fields"]
        self.assertEqual(fields["display_name"], "Alex Example")
        self.assertEqual(fields["email"], "alex@example.org")
        self.assertEqual(fields["phone"], "+390000000000")
        self.assertEqual(fields["birth_date"], "1990-02-03")
        self.assertEqual(fields["country"], "IT")
        self.assertEqual(fields["username_candidates"][0], "alex.work")  # the persona's claimed handle elsewhere
        self.assertEqual(packet["missing"], [])
        for name in fields["username_candidates"]:
            self.assertRegex(name, r"^[a-z0-9._]{3,30}$")

    def test_only_fields_the_platform_may_ask_are_prepared(self):
        fields = account_kit.build_packet(sample(), "work", "bluesky")["fields"]
        self.assertNotIn("phone", fields)
        self.assertNotIn("birth_date", fields)
        self.assertIn("email", fields)

    def test_chinese_packet_uses_the_local_name_and_asks_for_a_local_bio(self):
        packet = account_kit.build_packet(sample(), "work", "weibo")
        self.assertEqual(packet["fields"]["display_name"], "示例")
        self.assertEqual(packet["fields"]["nickname"], "示例")
        self.assertNotIn("username_candidates", packet["fields"])
        self.assertEqual(packet["fields"]["bio"], "Builds tools.")
        self.assertEqual(packet["fields"]["bio_language"], "en")
        keys = [q["key"] for q in packet["optional_missing"]]
        self.assertIn("personas.work.bio.zh-Hans", keys)
        self.assertTrue(packet["glossary"])
        self.assertTrue(packet["region_note"])

    def test_candidates_skip_unavailable_names_and_dedupe(self):
        data = sample()
        data["handles"].append({"platform": "x", "persona": "work", "username": "alexexample", "status": "unavailable"})
        names = account_kit.username_candidates(data, "work", "x")
        self.assertNotIn("alexexample", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(names)

    def test_candidates_come_from_names_when_there_are_no_stems(self):
        data = sample()
        del data["personas"]["family"]["username_stems"]
        data["handles"] = []
        data["preferences"] = {}
        names = account_kit.username_candidates(data, "family", "tiktok")
        self.assertIn("alexexample", names)

    def test_missing_required_facts_become_questions(self):
        data = sample()
        del data["person"]["email"]
        del data["personas"]["work"]["display_name"]
        packet = account_kit.build_packet(data, "work", "instagram")
        keys = [q["key"] for q in packet["missing"]]
        self.assertIn("person.email", keys)
        self.assertIn("personas.work.display_name", keys)

    def test_persona_email_beats_person_email(self):
        data = sample()
        data["personas"]["work"]["email"] = "work@example.org"
        self.assertEqual(account_kit.build_packet(data, "work", "instagram")["fields"]["email"], "work@example.org")

    def test_human_only_venues_get_no_generated_text(self):
        for platform in ("reddit", "discord", "band"):
            fields = account_kit.build_packet(sample(), "work", platform)["fields"]
            with self.subTest(platform=platform):
                self.assertNotIn("bio", fields)
                self.assertNotIn("website", fields)

    def test_human_only_set_matches_the_distribution_registry(self):
        registry = distribution.load_registry()
        expected = {p["id"] for p in registry["platforms"] if p.get("human_only") is True} & set(CHANNELS)
        self.assertEqual(account_kit.human_only_platforms(), expected)

    def test_unknown_inputs_are_refused(self):
        for platform in ("notanetwork", "hacker_news", ""):
            with self.subTest(platform=platform), self.assertRaises(ValidationError):
                account_kit.build_packet(sample(), "work", platform)
        with self.assertRaises(ValidationError):
            account_kit.build_packet(sample(), "ghost", "instagram")

    def test_packet_has_the_fixed_human_steps_and_no_password_field(self):
        packet = account_kit.build_packet(sample(), "work", "instagram")
        text = json.dumps(packet, ensure_ascii=False).casefold()
        self.assertIn("captcha", text)
        self.assertIn("password manager", text)
        self.assertNotIn('"password"', text)


class PlanTests(unittest.TestCase):
    def plan(self, data=None, platforms=("instagram", "weibo"), persona="work"):
        return account_kit.plan(data or sample(), persona, list(platforms))

    def test_plan_is_ready_and_hashes_the_exact_content(self):
        plan = self.plan()
        self.assertTrue(plan["ready"])
        self.assertRegex(plan["plan_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(plan["plan_sha256"], self.plan()["plan_sha256"])
        changed = sample()
        changed["person"]["phone"] = "+390000000001"
        self.assertNotEqual(plan["plan_sha256"], self.plan(changed)["plan_sha256"])

    def test_plan_not_ready_while_required_facts_are_missing(self):
        data = sample()
        del data["person"]["email"]
        plan = self.plan(data)
        self.assertFalse(plan["ready"])
        self.assertEqual([q["key"] for q in plan["questions"]], ["person.email"])  # deduped across platforms
        self.assertEqual(sorted(plan["questions"][0]["platforms"]), ["instagram", "weibo"])

    def test_rendered_plan_shows_masked_values_only(self):
        text = account_kit.render_plan(self.plan())
        for raw in RAW_PII:
            self.assertNotIn(raw, text)
        self.assertIn("a***@example.org", text)
        self.assertIn(self.plan()["plan_sha256"][:12], text)
        self.assertIn("--approve-plan", text)

    def test_platform_list_is_validated_deduped_and_bounded(self):
        self.assertEqual([p["platform"] for p in self.plan(platforms=("instagram", "instagram"))["packets"]],
                         ["instagram"])
        with self.assertRaises(ValidationError):
            self.plan(platforms=())
        with self.assertRaises(ValidationError):
            account_kit.plan(sample(), "work", ["instagram"] * 1 + [f"p{i}" for i in range(30)])


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "kit"
        self.plan = account_kit.plan(sample(), "work", ["instagram", "weibo"])

    def test_requires_the_matching_hash(self):
        for bad in ("", "0" * 64, self.plan["plan_sha256"][:8], "zz"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                account_kit.write_plan(self.plan, self.out, bad)
        self.assertFalse(self.out.exists())

    def test_writes_private_files_with_the_prepared_values(self):
        files = account_kit.write_plan(self.plan, self.out, self.plan["plan_sha256"][:16])
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        for name in files:
            self.assertEqual(stat.S_IMODE((self.out / name).stat().st_mode), 0o600, name)
        self.assertEqual(sorted(files), sorted(["README.md", "plan.json", "instagram/SIGNUP.md",
                                                "instagram/fields.json", "weibo/SIGNUP.md", "weibo/fields.json"]))
        signup = (self.out / "instagram/SIGNUP.md").read_text(encoding="utf-8")
        self.assertIn("alex@example.org", signup)
        self.assertIn("captcha", signup.casefold())
        self.assertIn("reachweaver profile --handle instagram:work=", signup)
        self.assertIn("contains personal data", signup.casefold())
        weibo = (self.out / "weibo/SIGNUP.md").read_text(encoding="utf-8")
        self.assertIn("手机号", weibo)
        self.assertIn("示例", weibo)
        fields = json.loads((self.out / "weibo/fields.json").read_text(encoding="utf-8"))
        self.assertEqual(fields["fields"]["nickname"], "示例")

    def test_refuses_an_existing_folder_and_a_plan_with_open_questions(self):
        self.out.mkdir()
        with self.assertRaises(ValidationError):
            account_kit.write_plan(self.plan, self.out, self.plan["plan_sha256"])
        data = sample()
        del data["person"]["email"]
        open_plan = account_kit.plan(data, "work", ["instagram"])
        with self.assertRaises(ValidationError):
            account_kit.write_plan(open_plan, Path(self.tmp.name) / "kit2", open_plan["plan_sha256"])

    def test_a_tampered_plan_is_refused(self):
        self.plan["packets"][0]["fields"]["email"] = "someone@else.example"
        with self.assertRaises(ValidationError):
            account_kit.write_plan(self.plan, self.out, self.plan["plan_sha256"])
        self.assertFalse(self.out.exists())

    def test_parent_must_exist_and_nothing_is_left_behind_on_failure(self):
        with self.assertRaises(ValidationError):
            account_kit.write_plan(self.plan, Path(self.tmp.name) / "nope" / "kit", self.plan["plan_sha256"])


if __name__ == "__main__":
    unittest.main()
