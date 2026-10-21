"""Cross-feature gates for media posts: #7 D6/D7 with #13/#20 images and #22/#23 video.

Owner-approved 2026-10-09 (phase 2 media design). Offline: every HTTP call is mocked, and the
"flipped" registry is a temporary copy; the bundled registry is never changed.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_approval_confirmation_keyed import FakeTty, NoReadStdin, Terminal  # noqa: E402
from test_mastodon_media import png  # noqa: E402
from test_tiktok import (CAPTION, ENV as TT_ENV, FakeTikTok, Flipped as TikTokFlipped,  # noqa: E402
                         account as tt_account)
from test_media import av_mp4, mp4_bytes  # noqa: E402
from test_instagram import account as ig_account, record as ig_record  # noqa: E402
from test_youtube import account as yt_account  # noqa: E402

from reachweaver import accounts, attachments, confirmation, send_options, tiktok, youtube  # noqa: E402
from reachweaver.capabilities import VIDEO_MODES, load_registry, require_capability, require_video_mode  # noqa: E402
from reachweaver.policy import SUPPORTED_LIVE, SUPPORTED_LIVE_VIDEO  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.policy import ValidationError  # noqa: E402
from reachweaver.storage import (APPROVAL_MAC_PREFIX, MAC_MISMATCH, add_post, approval_binding_problem,  # noqa: E402
                                 approval_key_mode, approval_payload_sha256, claim_sending, connect, get_post,
                                 post_video, set_post_media, set_post_video, transition)
from reachweaver.video import BOTH_KINDS  # noqa: E402

KEY = "k" * 40  # a fake owner approval key for the keyed (#7 D7) tests; never a real secret
WITH_KEY = {"REACHWEAVER_APPROVAL_" + "SECRET": KEY}


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state" / "outbox.sqlite")
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(av_mp4(mdat_size=4096))


class KeyedBindingCoversMediaBytes(Workspace):
    """#7 D7 MACs the approval hash, so it covers image and video bytes exactly as it covers text."""

    def _approve_keyed(self, db, pid):
        with patch.dict(os.environ, WITH_KEY, clear=True):
            transition(db, pid, "approve")
            row = get_post(db, pid)
            self.assertTrue(row["approved_payload_mac"].startswith(APPROVAL_MAC_PREFIX))
            self.assertIsNone(approval_binding_problem(row, approval_key_mode(db)))

    def _swap_and_rehash(self, db, pid, column, record):
        """An attacker with DB write but no key: record other bytes and recompute the plain hash."""
        text = attachments.encode_media([record]) if column == "media_json" else json.dumps(record)
        db.execute(f"UPDATE posts SET {column}=? WHERE id=?", (text, pid))
        db.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?",
                   (approval_payload_sha256(get_post(db, pid)), pid))

    def test_changed_image_byte_breaks_the_keyed_tag(self):
        with connect(self.db) as db:
            pid = add_post(db, "masto", "mastodon", "With a chart")
            set_post_media(db, pid, [attachments.build_attachment(self.image, "A described chart")])
            self._approve_keyed(db, pid)
            self.image.write_bytes(png(note=b"one changed byte"))
            other = attachments.build_attachment(self.image, "A described chart")
            self._swap_and_rehash(db, pid, "media_json", other)
            with patch.dict(os.environ, WITH_KEY, clear=True):
                self.assertEqual(approval_binding_problem(get_post(db, pid), approval_key_mode(db)), MAC_MISMATCH)
                with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                    claim_sending(db, pid)
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_changed_video_byte_breaks_the_keyed_tag(self):
        with connect(self.db) as db:
            pid = add_post(db, "tt-me", "tiktok", CAPTION)
            set_post_video(db, pid, tiktok.build_video_record(self.video))
            self._approve_keyed(db, pid)
            self.video.write_bytes(av_mp4(mdat_size=4097))
            self._swap_and_rehash(db, pid, "video_json", tiktok.build_video_record(self.video))
            with patch.dict(os.environ, WITH_KEY, clear=True):
                self.assertEqual(approval_binding_problem(get_post(db, pid), approval_key_mode(db)), MAC_MISMATCH)
                with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                    claim_sending(db, pid)
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_the_mac_message_is_the_media_aware_payload_hash(self):
        with connect(self.db) as db:
            text_pid = add_post(db, "masto", "mastodon", "Same text")
            media_pid = add_post(db, "masto", "mastodon", "Same text")
            set_post_media(db, media_pid, [attachments.build_attachment(self.image, "A described chart")])
            self.assertNotEqual(approval_payload_sha256(get_post(db, text_pid), scheduled_at=None,
                                                        use_row_schedule=False),
                                approval_payload_sha256(get_post(db, media_pid), scheduled_at=None,
                                                        use_row_schedule=False))


class OneKindPerPost(Workspace):
    def test_images_and_a_video_are_never_both_attached(self):
        with connect(self.db) as db:
            pid = add_post(db, "tt-me", "tiktok", CAPTION)
            set_post_video(db, pid, tiktok.build_video_record(self.video))
            with self.assertRaisesRegex(ValidationError, "media_and_video_refused"):
                set_post_media(db, pid, [attachments.build_attachment(self.image, "A described chart")])
            masto = add_post(db, "masto", "mastodon", "Text")
            set_post_media(db, masto, [attachments.build_attachment(self.image, "A described chart")])
            record = tiktok.build_video_record(self.video)
            with self.assertRaisesRegex(ValidationError, "only supported for TikTok and YouTube drafts, not mastodon"):
                set_post_video(db, masto, record)
            # Both columns forced behind the API's back: never approvable, never claimable.
            db.execute("UPDATE posts SET video_json=? WHERE id=?", (json.dumps(record), masto))
            with self.assertRaisesRegex(ValidationError, "media_and_video_refused"):
                transition(db, masto, "approve")
            self.assertEqual(get_post(db, masto)["status"], "draft")

    def test_a_record_must_match_the_post_platform(self):
        yt_file = self.root / "yt.mp4"
        yt_file.write_bytes(mp4_bytes(1080, 1920, 30, mdat_size=4096))
        yt_record, _ = youtube.build_video_spec(yt_file, title="T", tags=[], privacy_status="private",
                                                made_for_kids=False, category_id="28")
        with connect(self.db) as db:
            yt = add_post(db, "yt", "youtube", "A description")
            tt = add_post(db, "tt-me", "tiktok", CAPTION)
            with self.assertRaisesRegex(ValidationError, "video record is for tiktok, not this youtube post"):
                set_post_video(db, yt, tiktok.build_video_record(self.video))
            with self.assertRaisesRegex(ValidationError, "video record is for youtube, not this tiktok post"):
                set_post_video(db, tt, yt_record)
            self.assertIsNone(post_video(get_post(db, yt)))
            self.assertIsNone(post_video(get_post(db, tt)))
            set_post_video(db, yt, yt_record)
            self.assertEqual(post_video(get_post(db, yt))["platform_options"]["privacy_status"], "private")
        self.assertIn("never both", BOTH_KINDS)


class TikTokLiveGatesFromPhaseOne(TikTokFlipped):
    """The TikTok runner enforces #7 D6 and #11 scopes like the native and Postiz loops."""

    def test_failed_human_confirmation_sends_nothing(self):
        pid = self.approved()
        with connect(str(self.db)) as db:
            confirmation.enable(db)
        out, err = Terminal(), io.StringIO()
        fake = FakeTikTok()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {**TT_ENV, "REACHWEAVER_ALLOW_LIVE": "1",
                                                        "REACHWEAVER_ENV": "prod"}, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch("sys.stdin", NoReadStdin()))
            stack.enter_context(patch("reachweaver.confirmation._DevTty", FakeTty(["00000000\n"] * 3)))
            stack.enter_context(patch("reachweaver.publishers._send_http", side_effect=fake))
            code = main(["--db", str(self.db), "--accounts", str(self.config), "publish", "--live",
                         "--post-id", pid])
        self.assertEqual(code, 2, out.getvalue() + err.getvalue())
        self.assertIn(confirmation.FAILED, err.getvalue())
        self.assertEqual(fake.routes, [])  # no identity GET, no init, no upload
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_confirmation_shows_the_video_aware_hash_prefix(self):
        pid = self.approved()
        with connect(str(self.db)) as db:
            confirmation.enable(db)
            digest = approval_payload_sha256(get_post(db, pid))
        tty = FakeTty([digest[:8] + "\n"])
        out, err = Terminal(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {**TT_ENV, "REACHWEAVER_ALLOW_LIVE": "1",
                                                        "REACHWEAVER_ENV": "prod"}, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch("sys.stdin", NoReadStdin()))
            stack.enter_context(patch("reachweaver.confirmation._DevTty", tty))
            stack.enter_context(patch("reachweaver.publishers._send_http", side_effect=FakeTikTok()))
            code = main(["--db", str(self.db), "--accounts", str(self.config), "publish", "--live",
                         "--post-id", pid])
        self.assertEqual(code, 3, out.getvalue() + err.getvalue())  # inbox delivery: needs_review, not public
        self.assertIn(f"payload sha256 {digest}", out.getvalue())
        self.assertIn("video:", out.getvalue())
        self.assertEqual(tt_account()["id"], "tt-me")


class BundledRegistryRefusesEveryMediaMode(Workspace):
    """Under the bundled registry no new adapter, image or video mode is reachable, and publish
    refuses each media kind before any platform credential is read (credential reader mocked)."""

    ACCOUNTS = [
        {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_IT_MASTO", "server": "https://social.example.org",
         "handle": "alice"},
        ig_account(env_prefix="REACHWEAVER_IT_IG"),
        yt_account(env_prefix="REACHWEAVER_IT_YT"),
        tt_account(env_prefix="REACHWEAVER_IT_TT"),
    ]

    def test_registry_enables_nothing_new(self):
        registry = load_registry()
        self.assertEqual(SUPPORTED_LIVE, frozenset({"bluesky", "mastodon", "telegram", "linkedin"}))
        self.assertEqual(SUPPORTED_LIVE_VIDEO, frozenset())
        for entry in registry["platforms"]:
            self.assertNotIn("live_media_formats", entry, entry["id"])
            self.assertIn(entry["reachweaver_adapter"], ("live_text", "none"), entry["id"])
            for field in VIDEO_MODES.get(entry["id"], {}).values():
                self.assertEqual(entry.get(field, "off"), "off", (entry["id"], field))
        for platform, fmt in (("mastodon", "image"), ("instagram", "image"), ("youtube", "video"),
                              ("tiktok", "video")):
            with self.subTest(platform=platform), self.assertRaises(ValidationError):
                require_capability(platform, fmt, "publish")
        for platform, modes in VIDEO_MODES.items():
            for mode in modes:
                with self.subTest(platform=platform, mode=mode), self.assertRaises(ValidationError):
                    require_video_mode(platform, mode)

    def _approved_posts(self):
        yt_file = self.root / "yt.mp4"
        yt_file.write_bytes(mp4_bytes(1080, 1920, 30, mdat_size=4096))
        with connect(self.db) as db:
            masto = add_post(db, "masto", "mastodon", "With a chart")
            set_post_media(db, masto, [attachments.build_attachment(self.image, "A described chart")])
            insta = add_post(db, "ig-me", "instagram", "A caption")
            set_post_media(db, insta, [ig_record()])
            yt = add_post(db, "yt-me", "youtube", "A description")
            record, _ = youtube.build_video_spec(yt_file, title="T", tags=[], privacy_status="private",
                                                 made_for_kids=False, category_id="28")
            set_post_video(db, yt, record)
            tt = add_post(db, "tt-me", "tiktok", CAPTION)
            set_post_video(db, tt, tiktok.build_video_record(self.video))
            masto_account = next(a for a in self.ACCOUNTS if a["id"] == "masto")
            for pid in (masto, insta, yt, tt):
                # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
                transition(db, pid, "approve",
                           send_options=send_options.fingerprint(masto_account) if pid == masto else None)
        return {"mastodon": masto, "instagram": insta, "youtube": yt, "tiktok": tt}

    def test_publish_live_refuses_before_any_platform_credential_read(self):
        posts = self._approved_posts()
        config = self.root / "accounts.json"
        config.write_text(json.dumps({"accounts": self.ACCOUNTS}), encoding="utf-8")
        live = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
                **{a["env_prefix"] + s: "fake-value" for a in self.ACCOUNTS
                   for s in ("_TOKEN", "_ACCESS_TOKEN", "_TIKTOK_ACCESS_TOKEN")}}
        real = accounts.credential
        prefixes = tuple(a["env_prefix"] for a in self.ACCOUNTS)
        for platform, pid in posts.items():
            reads: list[str] = []

            def tracking(name, env=None, _reads=reads):
                _reads.append(name)
                return real(name, env)

            out, err = io.StringIO(), io.StringIO()
            with self.subTest(platform=platform), patch.dict(os.environ, live, clear=True), \
                    patch("reachweaver.accounts.credential", side_effect=tracking), \
                    patch("reachweaver.tiktok.credential", side_effect=tracking), \
                    patch("reachweaver.publishers._send_http") as send, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--db", self.db, "--accounts", str(config), "publish", "--live", "--post-id", pid])
                self.assertEqual(code, 2, out.getvalue() + err.getvalue())
                self.assertIn("SKIPPED", out.getvalue())
                send.assert_not_called()
                self.assertEqual([n for n in reads if n.startswith(prefixes)], [])  # no platform credential read
                if platform == "mastodon":  # the tracker is wired: the binding gate read the approval-key name
                    self.assertIn("REACHWEAVER_APPROVAL_" + "SECRET", reads)
                    self.assertIn("mastodon image", out.getvalue())
                else:
                    self.assertIn("requires manual publishing", out.getvalue())
                with connect(self.db) as db:
                    self.assertEqual(get_post(db, pid)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
