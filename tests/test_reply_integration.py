"""Replies (#17) integrated with the phase 1-2 gates (owner-approved 2026-10-09; disabled until a live receipt).

``test_reply_send`` covers the reply feature itself. These tests pin what only the combination can
break: the #7 D7 keyed tag covers the reply target (it MACs the payload hash, which binds the
target), the #7 D6 confirmation shows and binds the target, the #11 cooldown scopes and the
Bluesky ``createSession`` budget apply to replies, and a reply is text-only (no images, no video).
Offline: every request is mocked and the bundled registry is only ever patched in memory.
"""
from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_approval_confirmation_keyed import FakeTty
from test_mastodon_media import png
from test_reply_send import BSKY_ENV, LIVE, MASTO_ENV, Base, enabled_registry

from reachweaver import confirmation, storage
from reachweaver.attachments import build_attachment
from reachweaver.policy import ValidationError
from gate_support import publish  # noqa: E402
from reachweaver.ratelimit import POSTIZ_WORKSPACE_SCOPE
from reachweaver.replies import canonical_target
from reachweaver.storage import (
    APPROVAL_KEY_CREDENTIAL, MAC_MISMATCH, REPLY_MEDIA_REFUSED, add_post, approval_payload, approval_payload_sha256,
    claim_sending, connect, get_post, record_provider_cooldown, set_post_media, transition,
)

KEY = "FAKE-offline-reply-integration-approval-key-0123456789"


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _claims(db, pid):
    return db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed' AND post_id=?", (pid,)).fetchone()[0]


class KeyedBindingCoversReplyTarget(Base):
    def test_changing_the_target_breaks_the_keyed_tag_even_with_a_recomputed_hash(self):
        with patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: KEY}), connect(self.db) as db:
            self.bsky_root(db)
            target = self.bsky_target(db)
            pid = self.approved_reply(db, "my-bsky", "bluesky", target)
            row = get_post(db, pid)
            self.assertTrue(row["approved_payload_mac"])
            tag_before = storage.approval_mac(KEY.encode(), pid, row["approved_payload_sha256"])
            self.assertEqual(row["approved_payload_mac"], tag_before)
            # Attacker with DB write but no key: swap the parent, recompute the plain hash.
            moved = {**target, "parent": {"uri": target["parent"]["uri"] + "x", "cid": target["parent"]["cid"]}}
            db.execute("UPDATE posts SET reply_target=? WHERE id=?", (canonical_target(moved), pid))
            db.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?",
                       (approval_payload_sha256(get_post(db, pid)), pid))
            forged = get_post(db, pid)
            self.assertNotEqual(storage.approval_mac(KEY.encode(), pid, forged["approved_payload_sha256"]), tag_before)
            with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                claim_sending(db, pid, expected_sha256=approval_payload_sha256(forged))
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(_claims(db, pid), 0)

    def test_reply_payload_keeps_the_text_tag_and_adds_only_the_target(self):
        with connect(self.db) as db:
            self.bsky_root(db)
            target = self.bsky_target(db)
            pid = self.approved_reply(db, "my-bsky", "bluesky", target)
            payload = approval_payload(get_post(db, pid))
        self.assertEqual(payload["binding"], storage.APPROVAL_BINDING_TAG)
        self.assertEqual(payload["reply_target"], canonical_target(target))
        self.assertNotIn("media", payload)
        self.assertNotIn("video", payload)


class ConfirmationShowsAndBindsReplyTarget(Base):
    def test_live_reply_needs_the_typed_prefix_of_the_target_bound_hash(self):
        with connect(self.db) as db:
            self.bsky_root(db)
            pid = self.approved_reply(db, "my-bsky", "bluesky", self.bsky_target(db))
            digest = approval_payload_sha256(get_post(db, pid))
            confirmation.enable(db)
        tty = FakeTty(["00000000\n"] * 3)
        with patch("reachweaver.capabilities.load_registry", return_value=enabled_registry()), \
                patch("reachweaver.confirmation.require_interactive"), \
                patch("reachweaver.confirmation._DevTty", tty), \
                patch("reachweaver.cli.reserve_bluesky_sessions") as reserve, \
                patch("reachweaver.cli.check_connection") as preflight, \
                patch("reachweaver.publishers.request_json") as post:
            code, shown, err = self.cli("publish", "--live", "--post-id", pid, env={**BSKY_ENV, **LIVE})
        self.assertEqual(code, 2)
        self.assertIn(confirmation.FAILED, err)
        self.assertIn(f"payload sha256 {digest}", shown)
        self.assertIn("reply to: ", shown)  # the human sees where the reply lands
        self.assertIn("root at://", shown)
        reserve.assert_not_called()
        preflight.assert_not_called()
        post.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(_claims(db, pid), 0)


class CooldownAndSessionBudgetApplyToReplies(Base):
    def test_account_cooldown_holds_a_reply_before_any_request(self):
        with connect(self.db) as db:
            self.masto_root(db)
            pid = self.approved_reply(db, "my-masto", "mastodon", self.masto_target(db))
            record_provider_cooldown(db, "my-masto", _later(600), "default", stage="send")
        with patch("reachweaver.capabilities.load_registry", return_value=enabled_registry()), \
                patch("reachweaver.cli.check_connection") as preflight, \
                patch("reachweaver.publishers.request_json") as post:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env={**MASTO_ENV, **LIVE})
        self.assertEqual(code, 2, out)
        self.assertIn("account_provider_cooldown_active", out)
        preflight.assert_not_called()
        post.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(_claims(db, pid), 0)

    def test_claim_enforces_a_cooldown_scope_for_a_reply(self):
        with connect(self.db) as db:
            self.masto_root(db)
            pid = self.approved_reply(db, "my-masto", "mastodon", self.masto_target(db))
            record_provider_cooldown(db, "other", _later(600), "default", stage="send", scope=POSTIZ_WORKSPACE_SCOPE + "acme")
            with self.assertRaisesRegex(ValidationError, "account_provider_cooldown_active"):
                claim_sending(db, pid, cooldown_scopes=(POSTIZ_WORKSPACE_SCOPE + "acme",))
            self.assertEqual(_claims(db, pid), 0)

    def test_bluesky_reply_reserves_the_create_session_budget(self):
        with connect(self.db) as db:
            self.bsky_root(db)
            pid = self.approved_reply(db, "my-bsky", "bluesky", self.bsky_target(db))
        with patch("reachweaver.capabilities.load_registry", return_value=enabled_registry()), \
                patch("reachweaver.cli.reserve_bluesky_sessions",
                      side_effect=ValidationError("bluesky_create_session_budget_exhausted")) as reserve, \
                patch("reachweaver.cli.check_connection") as preflight, \
                patch("reachweaver.publishers.request_json") as post:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env={**BSKY_ENV, **LIVE})
        self.assertEqual(code, 2, out)
        self.assertIn("bluesky_create_session_budget_exhausted", out)
        reserve.assert_called_once()
        preflight.assert_not_called()
        post.assert_not_called()


class ReplyIsTextOnly(Base):
    def image(self):
        path = self.root / "a.png"
        path.write_bytes(png())
        return build_attachment(path, "A described image")

    def test_attach_and_approve_refuse_images_on_a_reply(self):
        with connect(self.db) as db:
            self.masto_root(db)
            target = self.masto_target(db)
            pid = add_post(db, "my-masto", "mastodon", "Thanks for asking!", reply_target=canonical_target(target))
            with self.assertRaisesRegex(ValidationError, "reply_media_unsupported"):
                set_post_media(db, pid, [self.image()])
            self.assertIsNone(get_post(db, pid)["media_json"])
            from reachweaver.attachments import encode_media
            db.execute("UPDATE posts SET media_json=? WHERE id=?", (encode_media([self.image()]), pid))  # forced
            with self.assertRaisesRegex(ValidationError, "reply_media_unsupported"):
                transition(db, pid, "approve")
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertTrue(approval_payload(get_post(db, pid)).get("reply_media_invalid"))
            video = {"platform": "tiktok"}
            with self.assertRaisesRegex(ValidationError, "TikTok and YouTube"):
                storage.set_post_video(db, pid, video)
        code, out, _ = self.cli("check", pid)
        self.assertEqual(code, 1)
        self.assertIn("reply_media_unsupported", out)

    def test_live_and_library_paths_refuse_a_reply_that_gained_media_after_approval(self):
        with connect(self.db) as db:
            self.masto_root(db)
            target = self.masto_target(db)
            pid = self.approved_reply(db, "my-masto", "mastodon", target)
            from reachweaver.attachments import encode_media
            db.execute("UPDATE posts SET media_json=? WHERE id=?", (encode_media([self.image()]), pid))
        with patch("reachweaver.capabilities.load_registry", return_value=enabled_registry()), \
                patch("reachweaver.cli.check_connection") as preflight, \
                patch("reachweaver.accounts.credential") as cred, \
                patch("reachweaver.publishers.request_json") as post:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env={**MASTO_ENV, **LIVE})
            with self.assertRaisesRegex(ValidationError, "reply_media_unsupported"):
                publish(self.account("my-masto"), "Thanks!", pid, reply_target=target, media=[self.image()])
        self.assertEqual(code, 2, out)
        self.assertIn(REPLY_MEDIA_REFUSED.split(":")[0], out)
        preflight.assert_not_called()
        cred.assert_not_called()
        post.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(_claims(db, pid), 0)

    def test_dry_run_holds_a_reply_with_media(self):
        with connect(self.db) as db:
            self.masto_root(db)
            pid = self.approved_reply(db, "my-masto", "mastodon", self.masto_target(db))
            from reachweaver.attachments import encode_media
            db.execute("UPDATE posts SET media_json=? WHERE id=?", (encode_media([self.image()]), pid))
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn(f"HELD {pid}: reply_media_unsupported", out)


if __name__ == "__main__":
    unittest.main()
