"""The shared attachment model across #13 (Mastodon local files) and #20 (Instagram hosted URLs).

Owner-approved 2026-10-09 (D20-7 and the phase 2 media design): one ``media_json`` record shape
with ``source: "local" | "url"``, one tag ``approval/media-1``, platform-specific limits. Offline.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reachweaver import attachments, remote_media
from reachweaver.policy import ValidationError
from reachweaver.storage import (APPROVAL_BINDING_TAG_MEDIA, add_post, approval_payload, connect, get_post,
                                 post_media, set_post_media)
from test_instagram import record as url_record
from test_mastodon_media import png


class SharedImageModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = str(self.root / "state" / "rw.sqlite")
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())
        self.local = attachments.build_attachment(self.image, "A described image", "0.1,0.2")

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_sources_share_one_codec_and_one_tag(self):
        self.assertEqual(self.local["source"], "local")
        self.assertEqual(url_record()["source"], "url")
        mixed = attachments.encode_media([self.local, url_record()])
        self.assertEqual(attachments.decode_media(mixed), [self.local, url_record()])
        self.assertIs(remote_media.decode_media, attachments.decode_media)  # re-exported, not a second codec
        self.assertIs(remote_media.encode_media, attachments.encode_media)
        bound = attachments.bound_view([self.local, url_record()])
        self.assertEqual(set(bound[0]), {"sha256", "mime", "alt", "focus"})  # path never bound
        self.assertEqual(set(bound[1]), {"sha256", "mime", "alt", "focus", "url"})  # the URL is what is sent
        with connect(self.db) as db:
            for platform, item in (("mastodon", self.local), ("instagram", url_record())):
                pid = add_post(db, "acct", platform, "Caption")
                set_post_media(db, pid, [item])
                self.assertEqual(approval_payload(get_post(db, pid))["binding"], APPROVAL_BINDING_TAG_MEDIA)
                self.assertEqual(post_media(get_post(db, pid)), [item])

    def test_each_platform_accepts_only_its_source(self):
        with connect(self.db) as db:
            masto = add_post(db, "m", "mastodon", "Text")
            insta = add_post(db, "i", "instagram", "Caption")
            with self.assertRaisesRegex(ValidationError, "mastodon attachments must be local image files"):
                set_post_media(db, masto, [url_record()])
            with self.assertRaisesRegex(ValidationError, "instagram attachments must be owner-hosted image URLs"):
                set_post_media(db, insta, [self.local])
            for platform in ("bluesky", "telegram", "linkedin", "tiktok", "youtube"):
                pid = add_post(db, "x", platform, "Text")
                with self.subTest(platform=platform), self.assertRaisesRegex(ValidationError, "only supported for"):
                    set_post_media(db, pid, [self.local])
            self.assertEqual(post_media(get_post(db, masto)), [])

    def test_strict_decode_per_source(self):
        bad = [
            {k: v for k, v in self.local.items() if k != "source"},  # no source
            {**self.local, "source": "remote"},
            {**self.local, "url": "https://img.example.org/a.jpg"},  # local with a url
            {**url_record(), "path": "/tmp/x.jpg"},  # url with a path
            {k: v for k, v in url_record().items() if k != "url"},
            {**url_record(), "focus": "0,0"},  # Instagram has no focal point
            {**url_record(), "mime": "image/png"},
            {**self.local, "path": "relative.png"},
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValidationError):
                attachments.decode_media(json.dumps([item]))

    def test_offline_checks_never_download_a_hosted_image(self):
        self.assertIsNone(attachments.media_problem([url_record()]))  # skipped offline; the live path re-downloads
        view = attachments.describe([url_record(), self.local])
        self.assertIn("verify_yourself", view[0])
        self.assertTrue(view[1]["file_matches_record"])
        with self.assertRaisesRegex(ValidationError, attachments.MEDIA_CHANGED):
            attachments.read_approved_bytes(url_record())  # the local reader refuses a URL record
        with self.assertRaisesRegex(ValidationError, attachments.MEDIA_CHANGED):
            remote_media.fetch_approved_bytes(self.local, fetch=lambda url: b"")  # and vice versa


if __name__ == "__main__":
    unittest.main()
