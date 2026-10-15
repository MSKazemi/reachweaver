"""License, attribution, provenance and alt translations on media records (public issue #48). Offline.

Draft for owner security review (docs/SECURITY-REVIEW-PROPOSAL-48-media-rights.md): the optional ``rights`` record is
bound by the approval hash under ``approval/media-2``; images without it keep ``approval/media-1`` unchanged.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from test_mastodon_media import png

from reachweaver.attachments import (
    bound_view, build_attachment, build_rights, decode_media, describe, encode_media, has_rights,
)
from reachweaver.cli import main
from reachweaver.confirmation import show_payload
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    APPROVAL_BINDING_TAG_MEDIA, APPROVAL_BINDING_TAG_MEDIA_RIGHTS, add_post, approval_binding_problem, approval_payload,
    connect, get_post, offline_key_mode, post_media, published_text, set_post_media, transition,
)

OWNER = dict(license_id="owner", created_by="owner", rights_confirmed=True)


class BuildRightsTests(unittest.TestCase):
    def test_an_owner_image_needs_only_the_confirmation_license_and_creator(self):
        record, warnings = build_rights(**OWNER)
        self.assertEqual(record, {"rights_confirmed": True, "license": "owner", "provenance": {"created_by": "owner"}})
        self.assertEqual(warnings, [])

    def test_the_owner_must_confirm_rights(self):
        for bad in (None, False, "yes"):
            with self.subTest(value=bad), self.assertRaisesRegex(ValidationError, "rights_confirmed must be true"):
                build_rights(license_id="owner", created_by="owner", rights_confirmed=bad)

    def test_third_party_media_needs_a_real_license_attribution_and_gets_a_source_warning(self):
        with self.assertRaisesRegex(ValidationError, "needs its actual license"):
            build_rights(license_id="owner", created_by="third_party", rights_confirmed=True, attribution="Photo: A")
        with self.assertRaisesRegex(ValidationError, "requires attribution text"):
            build_rights(license_id="CC-BY-4.0", created_by="third_party", rights_confirmed=True)
        record, warnings = build_rights(license_id="CC-BY-4.0", created_by="third_party", rights_confirmed=True,
                                        attribution="Photo: A. Photographer", source_url="https://example.org/photo")
        self.assertEqual(record["attribution"], "Photo: A. Photographer")
        self.assertEqual(warnings, [])
        _, warnings = build_rights(license_id="licensed", created_by="third_party", rights_confirmed=True, attribution="Photo: A")
        self.assertTrue(any("no provenance.source_url" in w for w in warnings))

    def test_cc_by_needs_attribution_even_for_the_owner(self):
        with self.assertRaisesRegex(ValidationError, "requires attribution text"):
            build_rights(license_id="CC-BY-SA-4.0", created_by="owner", rights_confirmed=True)

    def test_tool_made_images_name_the_tool_and_carry_a_disclosure_warning(self):
        with self.assertRaisesRegex(ValidationError, "provenance.tool must name"):
            build_rights(license_id="owner", created_by="tool", rights_confirmed=True)
        _, warnings = build_rights(license_id="owner", created_by="tool", rights_confirmed=True, tool="an image editor")
        self.assertTrue(any("disclose per platform policy" in w for w in warnings))

    def test_invalid_license_creator_and_source_url(self):
        with self.assertRaisesRegex(ValidationError, "not an SPDX identifier"):
            build_rights(license_id="my own terms!", created_by="owner", rights_confirmed=True)
        with self.assertRaisesRegex(ValidationError, "created_by must be one of"):
            build_rights(license_id="owner", created_by="someone", rights_confirmed=True)

    def test_alt_translations_are_validated_like_alt_text(self):
        record, _ = build_rights(**OWNER, alt_translations={"it": "Un gatto su un divano", "fa": "یک گربه روی مبل"})
        self.assertEqual(list(record["alt_translations"]), ["fa", "it"])  # sorted, canonical
        for locale, text, message in (("english", "A cat", "BCP-47"), ("it", "", "alt text is required"),
                                      ("it", "image", "placeholder"), ("it", "a‮b", "bidirectional"),
                                      ("it", "x" * 1501, "characters")):
            with self.subTest(locale=locale, text=text[:10]), self.assertRaisesRegex(ValidationError, message):
                build_rights(**OWNER, alt_translations={locale: text})
        with self.assertRaisesRegex(ValidationError, "at most"):
            build_rights(**OWNER, alt_translations={f"a{chr(98 + i)}": "text here" for i in range(13)})


class RecordAndBindingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.image = self.root / "cat.png"
        self.image.write_bytes(png())
        self.account = {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_MR_MASTO",
                        "server": "https://social.example.org", "handle": "alice"}

    def attachment(self, **rights):
        item = build_attachment(self.image, "A cat on a sofa", None, account=self.account)
        if rights:
            item["rights"] = build_rights(**rights)[0]
        return item

    def draft(self, db, **rights):
        pid = add_post(db, "my-mastodon", "mastodon", "A photo of the office cat", "launch", "https://example.org/r")
        set_post_media(db, pid, [self.attachment(**rights)], account=self.account)
        return pid

    def test_a_record_without_rights_is_stored_and_bound_exactly_as_before(self):
        item = self.attachment()
        self.assertNotIn("rights", json.loads(encode_media([item]))[0])
        self.assertNotIn("rights", bound_view([item])[0])
        self.assertFalse(has_rights([item]))
        with connect(self.db) as db:
            pid = self.draft(db)
            self.assertEqual(approval_payload(get_post(db, pid))["binding"], APPROVAL_BINDING_TAG_MEDIA)

    def test_rights_round_trip_and_move_the_binding_to_media_2(self):
        rights = dict(OWNER, alt_translations={"it": "Un gatto su un divano"})
        item = self.attachment(**rights)
        [decoded] = decode_media(encode_media([item]))
        self.assertEqual(decoded["rights"], item["rights"])
        with connect(self.db) as db:
            pid = self.draft(db, **rights)
            payload = approval_payload(get_post(db, pid))
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG_MEDIA_RIGHTS)
            self.assertEqual(payload["media"][0]["rights"]["license"], "owner")
            self.assertEqual(payload["media"][0]["rights"]["alt_translations"], {"it": "Un gatto su un divano"})
            self.assertEqual(describe(post_media(get_post(db, pid)))[0]["rights"]["provenance"], {"created_by": "owner"})

    def test_changing_any_rights_field_after_approval_breaks_the_approval(self):
        rights = dict(license_id="CC-BY-4.0", created_by="third_party", rights_confirmed=True, attribution="Photo: A",
                      source_url="https://example.org/p", alt_translations={"it": "Un gatto"})
        edits = {
            "license": lambda r: r.update(license="CC-BY-SA-4.0"),
            "attribution": lambda r: r.update(attribution="Photo: B"),
            "creator": lambda r: r["provenance"].update(created_by="owner"),
            "source": lambda r: r["provenance"].update(source_url="https://example.org/other"),
            "translation": lambda r: r["alt_translations"].update(it="Un cane"),
            "removed": lambda r: r.pop("alt_translations"),
        }
        for name, edit in edits.items():
            with self.subTest(change=name), connect(self.db) as db:
                pid = self.draft(db, **rights)
                transition(db, pid, "approve")
                row = get_post(db, pid)
                self.assertIsNone(approval_binding_problem(row, offline_key_mode(db)))
                media = json.loads(row["media_json"])
                edit(media[0]["rights"])
                db.execute("UPDATE posts SET media_json=? WHERE id=?",
                           (json.dumps(media, sort_keys=True, separators=(",", ":"), ensure_ascii=False), pid))
                self.assertEqual(approval_binding_problem(get_post(db, pid), offline_key_mode(db)),
                                 "approval_hash_mismatch_reapproval_required")

    def test_adding_rights_after_approval_also_breaks_it_and_old_approvals_still_verify(self):
        with connect(self.db) as db:
            pid = self.draft(db)  # no rights
            transition(db, pid, "approve")
            self.assertIsNone(approval_binding_problem(get_post(db, pid), offline_key_mode(db)))  # media-1 still verifies
            media = json.loads(get_post(db, pid)["media_json"])
            media[0]["rights"] = build_rights(**OWNER)[0]
            db.execute("UPDATE posts SET media_json=? WHERE id=?", (json.dumps(media), pid))
            self.assertEqual(approval_binding_problem(get_post(db, pid), offline_key_mode(db)),
                             "approval_hash_mismatch_reapproval_required")

    def test_a_stored_record_with_bad_rights_is_refused_on_read(self):
        item = self.attachment(**OWNER)
        good = json.loads(encode_media([item]))
        mutations = {
            "extra key": lambda r: r.update(note="x"),
            "unconfirmed": lambda r: r.update(rights_confirmed=False),
            "bad license": lambda r: r.update(license="no spaces allowed"),
            "bad creator": lambda r: r["provenance"].update(created_by="robot"),
            "extra provenance key": lambda r: r["provenance"].update(email="a@b.example"),
            "translation not text": lambda r: r.update(alt_translations={"it": 5}),
            "translation bad tag": lambda r: r.update(alt_translations={"xx_yy": "text here"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                data = json.loads(json.dumps(good))
                mutate(data[0]["rights"])
                with self.assertRaises(ValidationError):
                    decode_media(json.dumps(data))
        data = json.loads(json.dumps(good))
        data[0]["unexpected"] = 1
        with self.assertRaises(ValidationError):
            decode_media(json.dumps(data))

    def test_rights_text_is_never_published(self):
        rights = dict(OWNER, attribution="Photo: Somebody Unique", alt_translations={"it": "Un gatto speciale"})
        with connect(self.db) as db:
            pid = self.draft(db, **rights)
            text = published_text(get_post(db, pid))
        self.assertIn("A cat on a sofa", text)  # the alt text that is sent
        self.assertNotIn("Somebody Unique", text)
        self.assertNotIn("Un gatto speciale", text)

    def test_the_confirmation_display_shows_what_the_digest_binds(self):
        with connect(self.db) as db:
            pid = self.draft(db, **dict(OWNER, alt_translations={"it": "Un gatto"}))
            row = get_post(db, pid)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            show_payload(row, "0" * 64, heading="CONFIRM")
        self.assertIn("rights: license owner, made by owner, 1 alt translation(s)", out.getvalue())


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.image = self.root / "cat.png"
        self.image.write_bytes(png())
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [{"id": "my-mastodon", "platform": "mastodon",
                                                           "env_prefix": "REACHWEAVER_MR_MASTO",
                                                           "server": "https://social.example.org", "handle": "alice"}]}),
                                 encoding="utf-8")
        with connect(self.db) as db:
            self.pid = add_post(db, "my-mastodon", "mastodon", "A photo of the office cat", "launch", "https://example.org/r")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def attach(self, *extra):
        return self.cli("media-attach", self.pid, "--file", str(self.image), "--alt", "A cat on a sofa", *extra)

    def test_without_rights_options_nothing_changes(self):
        code, out, err = self.attach()
        self.assertEqual(code, 0, err)
        self.assertNotIn("Rights recorded", out)
        with connect(self.db) as db:
            self.assertFalse(has_rights(post_media(get_post(db, self.pid))))

    def test_rights_are_stored_shown_and_bound(self):
        code, out, err = self.attach("--rights-confirmed", "--license", "CC-BY-4.0", "--created-by", "third_party",
                                     "--attribution", "Photo: A. Photographer", "--rights-source-url", "https://example.org/p",
                                     "--alt-translation", "it=Un gatto su un divano", "--alt-translation", "fa=یک گربه")
        self.assertEqual(code, 0, err)
        self.assertIn("Rights recorded (#48): license CC-BY-4.0, made by third_party; bound at approval (approval/media-2)", out)
        code, out, _ = self.cli("show", self.pid)
        shown = json.loads(out)
        rights = shown["media"]["attachments"][0]["rights"]
        self.assertIn("approval/media-2", shown["media"]["bound_by_approval"])
        self.assertIn("never published", shown["media"]["rights_not_sent"])
        self.assertEqual((rights["license"], rights["attribution"]), ("CC-BY-4.0", "Photo: A. Photographer"))
        self.assertEqual(sorted(rights["alt_translations"]), ["fa", "it"])
        with connect(self.db) as db:
            self.assertEqual(approval_payload(get_post(db, self.pid))["binding"], APPROVAL_BINDING_TAG_MEDIA_RIGHTS)

    def test_incomplete_or_unconfirmed_rights_refuse_the_attachment_and_change_nothing(self):
        cases = (
            (("--license", "owner", "--created-by", "owner"), "rights_confirmed must be true"),
            (("--rights-confirmed", "--created-by", "owner"), "license is required"),
            (("--rights-confirmed", "--license", "owner"), "created_by must be one of"),
            (("--rights-confirmed", "--license", "owner", "--created-by", "third_party", "--attribution", "A"), "actual license"),
            (("--rights-confirmed", "--license", "owner", "--created-by", "owner", "--alt-translation", "italian"), "LANG=TEXT"),
            (("--rights-confirmed", "--license", "owner", "--created-by", "owner", "--alt-translation", "it=a",
              "--alt-translation", "it=b"), "once per language"),
        )
        for options, message in cases:
            with self.subTest(options=options):
                code, out, err = self.attach(*options)
                self.assertEqual(code, 2)
                self.assertIn(message, err)
                with connect(self.db) as db:
                    self.assertEqual(post_media(get_post(db, self.pid)), [])

    def test_the_tool_note_is_shown_when_a_tool_made_the_image(self):
        code, out, err = self.attach("--rights-confirmed", "--license", "owner", "--created-by", "tool", "--tool", "an editor")
        self.assertEqual(code, 0, err)
        self.assertIn("NOTE: attachment: tool-generated or tool-edited media", out)


if __name__ == "__main__":
    unittest.main()
