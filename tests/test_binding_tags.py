"""Approval binding tags across text and media kinds (integration of #7, #13, #20, #22, #23; owner-approved 2026-10-09).

One tag per payload kind, never two meanings for one tag: a text-only row keeps the original
``approval/1`` payload byte for byte (the reference hash below was computed on ``main`` before any
media column existed), and every media kind binds under its own, distinct tag.
"""
import hashlib
import unittest

from reachweaver import storage
from reachweaver.storage import approval_payload, approval_payload_sha256

# Reference row: what a text-only post looked like on main, plus every nullable column a later
# schema added, all NULL. Its hash must never change.
REFERENCE_ROW = {
    "id": "p1", "platform": "mastodon", "account_id": "m1", "content": "Hello world https://example.org",
    "scheduled_at": None, "status": "approved", "created_at": "2026-10-09T00:00:00+00:00",
    "approval_sha256": None, "source_url": "https://example.org/r", "campaign": "c1",
    "approved_payload_sha256": None, "approved_by": None, "approval_channel": None,
    "approved_payload_mac": None, "media_json": None, "video_json": None,
}
REFERENCE_SHA256 = "b05c9191238bf9129932dfe6826bdb074a5f64da8d835cf1751650182f2d49e1"


class BindingTagTests(unittest.TestCase):
    def test_tag_values_are_pinned(self):
        self.assertEqual(storage.APPROVAL_BINDING_TAG, "reachweaver.storage.approval/1")
        self.assertEqual(storage.APPROVAL_BINDING_TAG_MEDIA, "reachweaver.storage.approval/media-1")
        self.assertEqual(storage.APPROVAL_BINDING_TAG_VIDEO, "reachweaver.storage.approval/video-1")
        self.assertEqual(storage.APPROVAL_MAC_TAG, "reachweaver.storage.approval-mac/1")

    def test_tags_are_pairwise_distinct(self):
        tags = [storage.APPROVAL_BINDING_TAG, storage.APPROVAL_BINDING_TAG_MEDIA, storage.APPROVAL_BINDING_TAG_VIDEO,
                storage.APPROVAL_MAC_TAG]
        self.assertEqual(len(set(tags)), len(tags), tags)

    def test_text_only_row_hash_is_byte_identical_to_main(self):
        self.assertEqual(approval_payload_sha256(REFERENCE_ROW), REFERENCE_SHA256)
        self.assertEqual(approval_payload(REFERENCE_ROW)["binding"], storage.APPROVAL_BINDING_TAG)
        empty = {**REFERENCE_ROW, "media_json": "", "video_json": ""}  # an empty column is text-only, too
        self.assertEqual(approval_payload_sha256(empty), REFERENCE_SHA256)

    def test_text_only_row_without_new_columns_hashes_the_same(self):
        legacy = {k: v for k, v in REFERENCE_ROW.items() if k not in ("approved_payload_mac", "media_json", "video_json")}
        self.assertEqual(approval_payload_sha256(legacy), REFERENCE_SHA256)

    def test_reference_payload_is_the_documented_v1_shape(self):
        v1 = {"binding": "reachweaver.storage.approval/1", "account_id": "m1", "platform": "mastodon",
              "content": "Hello world https://example.org", "scheduled_at": None,
              "source_url": "https://example.org/r", "campaign": "c1"}
        self.assertEqual(approval_payload(REFERENCE_ROW), v1)
        self.assertEqual(hashlib.sha256(storage._canonical_json(v1)).hexdigest(), REFERENCE_SHA256)


if __name__ == "__main__":
    unittest.main()
