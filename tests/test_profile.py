"""Profile knowledge base (config/profile.json): validation, masking, atomic private writes.

All values are synthetic. No network, no browser, no credential, no database.
"""
from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from reachweaver import profile
from reachweaver.policy import ValidationError


def sample() -> dict:
    return {
        "schema": profile.SCHEMA,
        "person": {"given_name": "Alex", "family_name": "Example", "email": "alex@example.org",
                   "phone": "+390000000000", "birth_date": "1990-02-03", "country": "IT",
                   "timezone": "Europe/Rome", "languages": ["en", "zh-Hans"]},
        "personas": {
            "work": {"display_name": {"en": "Alex Example", "zh-Hans": "示例"}, "bio": {"en": "Builds tools."},
                     "username_stems": ["alexexample"], "website": "https://example.org"},
            "family": {"display_name": "Alex", "username_stems": ["alex.fam"]}},
        "handles": [{"platform": "instagram", "persona": "work", "username": "@alex.work", "status": "claimed"},
                    {"platform": "instagram", "persona": "family", "username": "alex.fam", "status": "claimed"}],
        "preferences": {"default_persona": "work"},
    }


class ValidateTests(unittest.TestCase):
    def test_sample_is_valid(self):
        self.assertEqual(profile.validate_profile(sample()), [])

    def test_unknown_keys_are_refused_so_pii_cannot_creep_in(self):
        for where, key in (("person", "mothers_maiden_name"), ("personas.work", "nickname2"), ("", "extra")):
            data = sample()
            target = data
            for part in filter(None, where.split(".")):
                target = target[part]
            target[key] = "x"
            with self.subTest(where=where):
                self.assertTrue(profile.validate_profile(data))

    def test_secret_and_identity_document_keys_are_refused_anywhere(self):
        for key in ("password", "app_token", "api_key", "session_cookie", "otp", "totp_seed", "recovery_codes",
                    "passport_number", "national_id", "ssn", "iban", "card_number", "cvv", "tax_code"):
            data = sample()
            data["person"][key] = "x"
            with self.subTest(key=key):
                errors = " ".join(profile.validate_profile(data))
                self.assertIn("never store", errors)
                self.assertNotIn("x\"", errors)

    def test_field_shapes(self):
        cases = {
            ("person", "email"): "not-an-email",
            ("person", "phone"): "0039 123",
            ("person", "birth_date"): "2999-01-01",
            ("person", "country"): "Italy",
            ("person", "languages"): ["english please"],
            ("person", "given_name"): "a\x00b",
            ("person", "family_name"): "x" * 101,
        }
        for (section, key), value in cases.items():
            data = sample()
            data[section][key] = value
            with self.subTest(key=key):
                self.assertTrue(profile.validate_profile(data))

    def test_bidi_and_control_characters_are_refused_in_text(self):
        for bad in ("evil‮exe", "line\nbreak", "bell\x07"):
            data = sample()
            data["personas"]["work"]["display_name"] = bad
            with self.subTest(bad=bad):
                self.assertTrue(profile.validate_profile(data))

    def test_persona_ids_and_handle_rules(self):
        data = sample()
        data["personas"]["Bad Id"] = {}
        self.assertTrue(profile.validate_profile(data))
        data = sample()
        data["handles"].append({"platform": "instagram", "persona": "work", "username": "second", "status": "claimed"})
        self.assertIn("duplicate", " ".join(profile.validate_profile(data)))
        data = sample()
        data["handles"][0]["persona"] = "ghost"
        self.assertIn("unknown persona", " ".join(profile.validate_profile(data)))
        data = sample()
        data["handles"][0]["platform"] = "notanetwork"
        self.assertIn("unknown platform", " ".join(profile.validate_profile(data)))
        data = sample()
        data["handles"][0]["status"] = "verified"
        self.assertTrue(profile.validate_profile(data))
        data = sample()
        data["handles"][0]["username"] = "has space"
        self.assertTrue(profile.validate_profile(data))
        data = sample()
        data["preferences"]["default_persona"] = "ghost"
        self.assertTrue(profile.validate_profile(data))

    def test_platform_aliases_are_canonical(self):
        data = sample()
        data["handles"][0]["platform"] = "facebook_pages"
        self.assertEqual(profile.validate_profile(data), [])
        self.assertEqual(profile.canonical_platform("facebook_pages"), "facebook")
        self.assertEqual(profile.canonical_platform("dev"), "devto")
        self.assertEqual(profile.canonical_platform("weibo"), "weibo")
        self.assertIsNone(profile.canonical_platform("nope"))

    def test_chinese_and_other_scripts_are_valid_usernames(self):
        data = sample()
        data["handles"].append({"platform": "weibo", "persona": "work", "username": "示例用户", "status": "planned"})
        self.assertEqual(profile.validate_profile(data), [])

    def test_normalize_username(self):
        self.assertEqual(profile.normalize_username(" @Alex.Work "), "alex.work")
        self.assertEqual(profile.normalize_username("ＡＢＣ"), "abc")


class ExampleFileTests(unittest.TestCase):
    def test_shipped_example_is_valid_and_synthetic(self):
        path = Path(__file__).resolve().parents[1] / "config" / "profile.example.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(profile.validate_profile(data), [])
        self.assertTrue(data["person"]["email"].endswith("@example.org"))
        self.assertTrue(data["person"]["phone"].startswith("+39000000"))

    def test_the_real_profile_path_is_ignored_by_git(self):
        text = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config/profile.json", text.splitlines())


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config" / "profile.json"

    def test_save_is_private_atomic_and_round_trips(self):
        profile.save_profile(sample(), self.path)
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(profile.load_profile(self.path)["person"]["given_name"], "Alex")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["profile.json"])

    def test_save_refuses_an_invalid_document_and_leaves_the_old_file(self):
        profile.save_profile(sample(), self.path)
        bad = sample()
        bad["person"]["password"] = "x"
        with self.assertRaises(ValidationError):
            profile.save_profile(bad, self.path)
        self.assertEqual(profile.load_profile(self.path)["person"]["given_name"], "Alex")

    def test_load_errors_are_plain_and_do_not_echo_values(self):
        with self.assertRaises(ValidationError) as ctx:
            profile.load_profile(self.path)
        self.assertIn("profile --init", str(ctx.exception))
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValidationError):
            profile.load_profile(self.path)
        bad = sample()
        bad["person"]["email"] = "secret-looking@@"
        self.path.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(ValidationError) as ctx:
            profile.load_profile(self.path)
        self.assertNotIn("secret-looking", str(ctx.exception))

    def test_oversize_file_is_refused(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(" " * (profile.MAX_BYTES + 1), encoding="utf-8")
        with self.assertRaises(ValidationError):
            profile.load_profile(self.path)

    def test_permissions_finding(self):
        profile.save_profile(sample(), self.path)
        self.assertIsNone(profile.permissions_finding(self.path))
        os.chmod(self.path, 0o644)
        self.assertIn("chmod 600", profile.permissions_finding(self.path))

    def test_path_resolution(self):
        self.assertEqual(profile.profile_path("x.json"), Path("x.json"))
        self.assertEqual(profile.profile_path(None), Path("config/profile.json"))

    def test_no_environment_variable_is_read(self):
        text = Path(profile.__file__).read_text(encoding="utf-8")
        self.assertNotIn("os.environ", text)
        self.assertNotIn("getenv", text)

    def test_load_optional_returns_none_only_when_the_file_is_absent(self):
        self.assertIsNone(profile.load_optional(self.path))
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValidationError):
            profile.load_optional(self.path)


class EditTests(unittest.TestCase):
    def test_template_is_valid_and_has_requested_personas(self):
        template = profile.template(["work", "family"])
        self.assertEqual(profile.validate_profile(template), [])
        self.assertEqual(sorted(template["personas"]), ["family", "work"])
        with self.assertRaises(ValidationError):
            profile.template(["Bad Id"])

    def test_set_value_paths(self):
        data = profile.template(["work"])
        profile.set_value(data, "person.given_name", "Alex")
        profile.set_value(data, "person.languages", "en, zh-Hans")
        profile.set_value(data, "personas.work.bio.zh-Hans", "你好")
        profile.set_value(data, "personas.work.username_stems", '["alexexample"]')
        profile.set_value(data, "preferences.default_persona", "work")
        self.assertEqual(data["person"]["languages"], ["en", "zh-Hans"])
        self.assertEqual(data["personas"]["work"]["bio"], {"zh-Hans": "你好"})
        self.assertEqual(data["personas"]["work"]["username_stems"], ["alexexample"])
        self.assertEqual(profile.validate_profile(data), [])

    def test_set_value_creates_a_persona_but_rejects_other_paths(self):
        data = profile.template(["work"])
        profile.set_value(data, "personas.family.display_name", "Alex")
        self.assertIn("family", data["personas"])
        for path in ("handles", "schema", "person", "person.password", "personas", "other.x", "personas.work",
                     "personas.Bad.display_name", "personas.work.bio.not a lang"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                profile.set_value(data, path, "x")

    def test_set_value_empty_clears_the_field(self):
        data = sample()
        profile.set_value(data, "person.phone", "")
        self.assertNotIn("phone", data["person"])

    def test_set_handle_adds_updates_and_validates(self):
        data = sample()
        profile.set_handle(data, "instagram", "work", "@new.name", "claimed")
        self.assertEqual([h["username"] for h in data["handles"] if h["persona"] == "work"], ["new.name"])
        profile.set_handle(data, "weibo", "work", "示例", "planned")
        self.assertEqual(len(data["handles"]), 3)
        for args in (("nope", "work", "x", "claimed"), ("weibo", "ghost", "x", "claimed"),
                     ("weibo", "work", "has space", "claimed"), ("weibo", "work", "ok", "verified")):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                profile.set_handle(copy.deepcopy(data), *args)

    def test_parse_handle_spec(self):
        self.assertEqual(profile.parse_handle_spec("instagram:work=@alex.work"),
                         ("instagram", "work", "alex.work", "claimed"))
        self.assertEqual(profile.parse_handle_spec("weibo:work=示例:planned"), ("weibo", "work", "示例", "planned"))
        for bad in ("instagram", "instagram=x", "instagram:work", ":work=x"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                profile.parse_handle_spec(bad)


class MaskTests(unittest.TestCase):
    def test_masks_hide_the_middle(self):
        self.assertEqual(profile.mask_email("alex@example.org"), "a***@example.org")
        self.assertEqual(profile.mask_phone("+390000001234"), "+39******1234")
        self.assertEqual(profile.mask_date("1990-02-03"), "1990-**-**")
        self.assertEqual(profile.mask_email(None), "")

    def test_masked_view_never_contains_raw_pii(self):
        view = json.dumps(profile.masked_view(sample()), ensure_ascii=False)
        for raw in ("alex@example.org", "+390000000000", "1990-02-03"):
            self.assertNotIn(raw, view)
        self.assertIn("Alex", view)  # names and handles are not hidden: they are public-facing


if __name__ == "__main__":
    unittest.main()
