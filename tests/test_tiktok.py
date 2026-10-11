"""TikTok Content Posting API adapter (issue #23, owner-approved 2026-10-09; disabled until a live receipt). Every HTTP exchange is mocked: no network, no live post.

Two halves:
* ``Unreachable*``: with the bundled registry (both video switches "off") and an empty
  ``policy.SUPPORTED_LIVE_VIDEO`` every entry point refuses TikTok before a credential read or request.
* ``Flipped*``: a TEMPORARY registry copy with the switches "live" (and ``SUPPORTED_LIVE_VIDEO``
  patched to match) shows the mocked contract: creator-inbox delivery recorded as needs_review (never
  published), chunked upload, status polling, identity and privacy refusals, post-write needs_review.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_media import av_mp4, png_bytes  # noqa: E402

from reachweaver import capabilities, tiktok  # noqa: E402
from reachweaver.accounts import CREDENTIAL_SUFFIXES, env_prefix_conflicts, load_accounts  # noqa: E402
from reachweaver.capabilities import load_registry, require_video_mode, validate_registry  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.connections import check_connection  # noqa: E402
from reachweaver.policy import SUPPORTED_LIVE_VIDEO, ValidationError, validate_post  # noqa: E402
from reachweaver.publishers import reset_write_marker, write_started
from gate_support import publish  # noqa: E402
from reachweaver.storage import (  # noqa: E402
    APPROVAL_BINDING_TAG, APPROVAL_BINDING_TAG_VIDEO, add_post, approval_payload, approval_payload_sha256,
    claim_sending, connect, get_post, published_posts, report, set_post_video, transition,
)

FAKE_TOKEN = "fake-tiktok-user-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_TT"
ENV = {PREFIX + "_TIKTOK_ACCESS_TOKEN": FAKE_TOKEN}
LIVE_ENV = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
OPEN_ID, NAME, HANDLE = "-000OPENid.test_1", "Test Creator", "testcreator"
PUBLISH_ID = "v_inbox_file~v2.123456789"
UPLOAD_URL = "https://open-upload.tiktokapis.com/video/?upload_id=12345&upload_token=Xza123"
CAPTION = "How ReachWeaver checks captions #devtools"


def account(**overrides):
    base = {"id": "tt-me", "platform": "tiktok", "env_prefix": PREFIX, "handle": HANDLE,
            "open_id": OPEN_ID, "display_name": NAME}
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self.headers = Message()
        self._body = body

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code):
    # The message and body carry the token: they must never reach an error string.
    return HTTPError(url, code, "Bearer " + FAKE_TOKEN, Message(), io.BytesIO(FAKE_TOKEN.encode()))


def ok(data):
    return {"data": data, "error": {"code": "ok", "message": "", "log_id": "202610090000"}}


class FakeTikTok:
    """Routes mocked requests by (method, host, path). ``errors`` maps a route name to an HTTP code."""

    ROUTES = {
        ("GET", "open.tiktokapis.com", "/v2/user/info/"): "user_info",
        ("POST", "open.tiktokapis.com", "/v2/post/publish/creator_info/query/"): "creator_info",
        ("POST", "open.tiktokapis.com", "/v2/post/publish/inbox/video/init/"): "inbox_init",
        ("POST", "open.tiktokapis.com", "/v2/post/publish/video/init/"): "direct_init",
        ("POST", "open.tiktokapis.com", "/v2/post/publish/status/fetch/"): "status",
        ("PUT", "open-upload.tiktokapis.com", "/video/"): "upload",
    }

    def __init__(self, *, user=None, creator=None, statuses=("PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"),
                 status_extra=None, errors=None, init=None, upload_codes=None):
        self.requests, self.routes, self.write_flags = [], [], []
        self.user = user or {"open_id": OPEN_ID, "display_name": NAME}
        self.creator = creator or {"creator_username": HANDLE, "creator_nickname": NAME,
                                   "privacy_level_options": ["FOLLOWER_OF_CREATOR", "SELF_ONLY"],
                                   "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False,
                                   "max_video_post_duration_sec": 600}
        self.statuses = list(statuses)
        self.status_extra = status_extra or {}
        self.errors = errors or {}
        self.init = init or {"publish_id": PUBLISH_ID, "upload_url": UPLOAD_URL}
        self.upload_codes = upload_codes

    def __call__(self, req):
        parts = urlsplit(req.full_url)
        route = self.ROUTES.get((req.get_method(), parts.hostname, parts.path))
        if parts.scheme != "https" or route is None:
            raise AssertionError("unexpected request")
        self.requests.append(req)
        self.routes.append(route)
        self.write_flags.append(write_started())
        if route in self.errors:
            raise http_error(req.full_url, self.errors[route])
        if route == "upload":
            uploads = self.routes.count("upload")
            if self.upload_codes is not None:
                return FakeResponse(self.upload_codes[uploads - 1], b"")
            last = req.get_header("Content-range").split("-")[1].split("/")
            return FakeResponse(201 if int(last[0]) + 1 == int(last[1]) else 206, b"")
        body = {
            "user_info": lambda: ok({"user": self.user}),
            "creator_info": lambda: ok(self.creator),
            "inbox_init": lambda: ok(self.init),
            "direct_init": lambda: ok(self.init),
            "status": lambda: ok({"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0],
                                  **self.status_extra}),
        }[route]()
        return FakeResponse(200, json.dumps(body).encode())

    def json_body(self, route):
        return json.loads(self.requests[self.routes.index(route)].data.decode())

    def all(self, route):
        return [r for r, name in zip(self.requests, self.routes) if name == route]


_BUNDLED = load_registry()  # read once, before any test patches SUPPORTED_LIVE_VIDEO


def flipped_registry(*, direct: bool = False) -> dict:
    """The registry change the owner would make AFTER review and a live receipt (temp copy only)."""
    registry = copy.deepcopy(_BUNDLED)
    entry = next(e for e in registry["platforms"] if e["id"] == "tiktok")
    entry["reachweaver_video_inbox"] = "live"
    entry["reachweaver_video_direct_post"] = "live" if direct else "off"
    return registry


class VideoFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.video = self.tmp / "clip.mp4"
        self.video.write_bytes(av_mp4(mdat_size=4096))


class Flipped(VideoFile):
    """Patches a temp registry copy and every SUPPORTED_LIVE_VIDEO binding; the bundled file is untouched."""

    DIRECT = False

    def setUp(self):
        super().setUp()
        path = self.tmp / "platform_registry.json"
        path.write_text(json.dumps(flipped_registry(direct=self.DIRECT)), encoding="utf-8")
        live = frozenset({"tiktok"})
        for target, value in (("reachweaver.capabilities.REGISTRY_FILE", path),
                              ("reachweaver.policy.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.capabilities.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.connections.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.cli.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.tiktok._sleep", self.slept),
                              ("reachweaver.ratelimit._sleep", lambda s: None)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sleeps: list[float] = []
        reset_write_marker()
        self.db = self.tmp / "state" / "outbox.sqlite"
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")

    def slept(self, seconds):
        self.sleeps.append(seconds)

    def fresh_db(self):
        """A new outbox per subtest, so the per-account rolling budget of one case cannot block the next."""
        self.db = self.tmp / f"state-{len(list(self.tmp.glob('state*')))}" / "outbox.sqlite"

    def cli(self, *args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or LIVE_ENV), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def approved(self, *attach_args, text=CAPTION):
        with connect(str(self.db)) as con:
            pid = add_post(con, "tt-me", "tiktok", text)
        status, out, err = self.cli("video-attach", pid, "--file", str(self.video), *attach_args, env={})
        self.assertEqual(status, 0, out + err)
        status, out, err = self.cli("approve", pid, "--approver", "owner", env={})
        self.assertEqual(status, 0, out + err)
        return pid

    def send(self, fake, pid):
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            return self.cli("publish", "--live", "--post-id", pid)

    def row(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)

    def events(self, pid, action):
        with connect(str(self.db)) as con:
            return [r["detail"] for r in con.execute("SELECT detail FROM event_log WHERE post_id=? AND action=?",
                                                     (pid, action))]

    def assert_no_secret(self, *texts):
        for text in texts:
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn("upload_token", text)


# --------------------------------------------------------------------------- unreachable today

class UnreachableTests(VideoFile):
    def test_bundled_registry_keeps_both_switches_off(self):
        entry = next(e for e in load_registry()["platforms"] if e["id"] == "tiktok")
        self.assertEqual((entry["reachweaver_adapter"], entry["reachweaver_video_inbox"],
                          entry["reachweaver_video_direct_post"], entry["status"]),
                         ("none", "off", "off", "requires-review"))
        self.assertEqual(SUPPORTED_LIVE_VIDEO, frozenset())
        for mode in tiktok.MODES:
            with self.subTest(mode=mode), self.assertRaisesRegex(ValidationError, "No ReachWeaver live video"):
                require_video_mode("tiktok", mode)

    def test_library_entry_points_refuse_before_any_request_or_credential_read(self):
        record = tiktok.build_video_record(self.video)
        data = self.video.read_bytes()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.tiktok.credential") as cred:
            with self.assertRaisesRegex(ValidationError, "not yet implemented for tiktok"):
                publish(account(), CAPTION, "p1")
            with self.assertRaisesRegex(ValidationError, "platform_has_no_authorized_live_connector"):
                check_connection(account())
            with self.assertRaisesRegex(ValidationError, "No ReachWeaver live video"):
                tiktok.check_connection(account(), record)
            with self.assertRaisesRegex(ValidationError, "No ReachWeaver live video"):
                tiktok.deliver(account(), CAPTION, record, data,
                               verified=tiktok.VerifiedTikTok("tt-me", "creator_inbox"))
        send.assert_not_called()
        cred.assert_not_called()

    def test_runner_refuses_at_the_registry_gate_first(self):
        db = self.tmp / "outbox.sqlite"
        with connect(str(db)) as con:
            pid = add_post(con, "tt-me", "tiktok", CAPTION)
            set_post_video(con, pid, tiktok.build_video_record(self.video))
            transition(con, pid, "approve")
            post = get_post(con, pid)
            out = io.StringIO()
            with patch("reachweaver.publishers._send_http") as send, patch("reachweaver.tiktok.credential") as cred, \
                    patch("reachweaver.storage.check_approval_binding") as binding, contextlib.redirect_stdout(out):
                code = tiktok.run_live_send(con, post, account(), max_per_24h=2, min_gap_minutes=60, optout=[],
                                            wlog=_NullLog(), cooldown_floor_seconds=60, preflight_max_retries=0)
            self.assertEqual(code, 2)
            self.assertIn("No ReachWeaver live video adapter", out.getvalue())
            send.assert_not_called()
            cred.assert_not_called()
            binding.assert_not_called()
            self.assertEqual(get_post(con, pid)["status"], "approved")

    def test_cli_live_publish_keeps_tiktok_manual_and_approved(self):
        db, config = self.tmp / "outbox.sqlite", self.tmp / "accounts.json"
        config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
        with connect(str(db)) as con:
            pid = add_post(con, "tt-me", "tiktok", CAPTION)
            set_post_video(con, pid, tiktok.build_video_record(self.video))
            transition(con, pid, "approve")
        out = io.StringIO()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            status = main(["--db", str(db), "--accounts", str(config), "publish", "--live", "--post-id", pid])
            check = main(["--db", str(db), "--accounts", str(config), "connect-check", "--account", "tt-me",
                          "--online"])
        self.assertEqual((status, check), (2, 2))
        self.assertIn("requires manual publishing", out.getvalue())
        send.assert_not_called()
        with connect(str(db)) as con:
            self.assertEqual(get_post(con, pid)["status"], "approved")

    def test_registry_switch_rules(self):
        registry = flipped_registry()
        direct_only = flipped_registry(direct=True)
        next(e for e in direct_only["platforms"] if e["id"] == "tiktok")["reachweaver_video_inbox"] = "off"
        self.assertIn("must equal policy.SUPPORTED_LIVE_VIDEO", "; ".join(validate_registry(registry)))
        self.assertIn("needs reachweaver_video_inbox 'live'", "; ".join(validate_registry(direct_only)))
        with patch("reachweaver.capabilities.SUPPORTED_LIVE_VIDEO", frozenset({"tiktok"})):
            self.assertEqual(validate_registry(registry), [])
            self.assertEqual(validate_registry(flipped_registry(direct=True)), [])
        bad = copy.deepcopy(load_registry())
        next(e for e in bad["platforms"] if e["id"] == "tiktok")["reachweaver_video_inbox"] = "on"
        next(e for e in bad["platforms"] if e["id"] == "youtube")["reachweaver_video_inbox"] = "off"
        errors = "; ".join(validate_registry(bad))
        self.assertIn("tiktok: reachweaver_video_inbox must be one of off, live", errors)
        self.assertIn("youtube: reachweaver_video_inbox only apply to tiktok", errors)
        self.assertEqual(json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8")), load_registry())


class _NullLog:
    def __getattr__(self, name):
        return lambda *a, **k: None


# --------------------------------------------------------------------------- offline pieces

class ConfigAndCredentialTests(unittest.TestCase):
    def write(self, *accounts):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "accounts.json"
        path.write_text(json.dumps({"accounts": list(accounts)}), encoding="utf-8")
        return path

    def test_adapter_shaped_account_is_strict_and_manual_account_unchanged(self):
        self.assertIn("tt-me", load_accounts(self.write(account())))
        manual = {"id": "tt-manual", "platform": "tiktok", "env_prefix": "REACHWEAVER_TTM", "handle": "x",
                  "audience_note": "kept as before"}
        self.assertIn("tt-manual", load_accounts(self.write(manual)))
        for bad, needle in ((account(privacy_level="PUBLIC_TO_EVERYONE"), "unsupported fields privacy_level"),
                            (account(open_id="has space"), "open_id is required"),
                            (account(display_name=""), "display_name is required")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                load_accounts(self.write(bad))

    def test_credential_suffix_reserved_for_overlap_rule(self):
        self.assertEqual(CREDENTIAL_SUFFIXES["tiktok"], ("_TIKTOK_ACCESS_TOKEN",))
        # REACHWEAVER_A + _TIKTOK_ACCESS_TOKEN equals REACHWEAVER_A_TIKTOK + _ACCESS_TOKEN (LinkedIn).
        clash = [account(env_prefix="REACHWEAVER_A"),
                 {"id": "li", "platform": "linkedin", "env_prefix": "REACHWEAVER_A_TIKTOK"}]
        self.assertTrue(env_prefix_conflicts(clash))

    def test_caption_utf16_limit_tightens_validate_post(self):
        self.assertEqual(validate_post("tiktok", "\U0001F600" * 1100), [])
        self.assertTrue(any("UTF-16" in p for p in validate_post("tiktok", "\U0001F600" * 1101)))


class VideoRecordTests(VideoFile):
    def test_inbox_record_binds_sha256_and_carries_no_settings(self):
        record = tiktok.build_video_record(self.video)
        self.assertEqual(record["sha256"], hashlib.sha256(self.video.read_bytes()).hexdigest())
        self.assertEqual((tiktok.record_mode(record), tiktok.post_settings(record), record["mime"]),
                         ("creator_inbox", None, "video/mp4"))
        self.assertEqual((record["kind"], record["platform"]), ("reachweaver_video", "tiktok"))  # shared video record
        self.assertEqual(record["platform_options"], {"mode": "creator_inbox", "privacy_level": None, "commercial": None,
                                                      "disable_comment": None, "disable_duet": None,
                                                      "disable_stitch": None, "is_aigc": None})
        self.assertEqual((record["width"], record["height"], record["video_codec"]), (1080, 1920, "h264"))
        self.assertIs(tiktok.check_record(record), record)
        with self.assertRaisesRegex(ValidationError, "direct_post only"):
            tiktok.build_video_record(self.video, privacy_level="SELF_ONLY")

    def test_direct_post_needs_explicit_privacy_and_commercial_and_defaults_interactions_off(self):
        with self.assertRaisesRegex(ValidationError, "explicit --privacy-level"):
            tiktok.build_video_record(self.video, mode="direct_post", commercial="none")
        with self.assertRaisesRegex(ValidationError, "explicit --commercial"):
            tiktok.build_video_record(self.video, mode="direct_post", privacy_level="SELF_ONLY")
        with self.assertRaisesRegex(ValidationError, "branded content cannot be private"):
            tiktok.build_video_record(self.video, mode="direct_post", privacy_level="SELF_ONLY",
                                      commercial="branded_content")
        record = tiktok.build_video_record(self.video, mode="direct_post", privacy_level="SELF_ONLY",
                                           commercial="none")
        self.assertEqual(tiktok.post_settings(record), {
            "privacy_level": "SELF_ONLY", "disable_comment": True, "disable_duet": True, "disable_stitch": True,
            "brand_content_toggle": False, "brand_organic_toggle": False, "is_aigc": False})
        self.assertEqual(record["platform_options"], {"mode": "direct_post", "privacy_level": "SELF_ONLY",
                                                      "commercial": "none", "disable_comment": True,
                                                      "disable_duet": True, "disable_stitch": True, "is_aigc": False})

    def test_refuses_unusable_files(self):
        link = self.tmp / "link.mp4"
        link.symlink_to(self.video)
        image = self.tmp / "image.mp4"
        image.write_bytes(png_bytes(1080, 1920))
        slow = self.tmp / "slow.mp4"
        slow.write_bytes(av_mp4(fps_ticks=[(150, 2000)]))  # 15 fps
        for path, needle in ((link, "symlink"), (self.tmp / "missing.mp4", "unreadable"),
                             (image, "MP4 only"), (slow, "frame rate 15.0 outside 23..60")):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValidationError, needle):
                tiktok.build_video_record(path)
        with patch("reachweaver.tiktok.MAX_VIDEO_BYTES", 100), self.assertRaisesRegex(ValidationError, "too_large"):
            tiktok.build_video_record(self.video)

    def test_changed_file_is_detected(self):
        record = tiktok.build_video_record(self.video)
        self.assertEqual(tiktok.load_bound_video(record), self.video.read_bytes())
        self.video.write_bytes(av_mp4(mdat_size=4097))
        with self.assertRaisesRegex(tiktok.VideoChanged, "approval_video_changed_reapproval_required"):
            tiktok.load_bound_video(record)

    def test_chunk_plan_follows_the_media_transfer_guide(self):
        self.assertEqual(tiktok.chunk_plan(4_000_000), (4_000_000, 1))       # under 5 MB: whole file
        self.assertEqual(tiktok.chunk_plan(64_000_000), (64_000_000, 1))     # one chunk up to 64 MB
        size = 100_000_000
        chunk, count = tiktok.chunk_plan(size)
        self.assertEqual((chunk, count), (10 * 1024 * 1024, size // (10 * 1024 * 1024)))
        ranges = tiktok.chunk_ranges(size, chunk, count)
        self.assertEqual((ranges[0][0], ranges[-1][1], len(ranges)), (0, size - 1, count))
        self.assertTrue(all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:])))
        final = ranges[-1][1] - ranges[-1][0] + 1
        self.assertTrue(5 * 1024 * 1024 <= chunk <= 64_000_000 and chunk <= final < 128_000_000)


class ApprovalBindingTests(VideoFile):
    def test_text_only_payload_unchanged_and_video_payload_binds_record(self):
        db = self.tmp / "outbox.sqlite"
        with connect(str(db)) as con:
            pid = add_post(con, "tt-me", "tiktok", CAPTION)
            payload = approval_payload(get_post(con, pid))
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG)
            self.assertNotIn("video", payload)
            set_post_video(con, pid, tiktok.build_video_record(self.video))
            payload = approval_payload(get_post(con, pid))
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG_VIDEO)
            self.assertEqual(payload["video"]["sha256"], hashlib.sha256(self.video.read_bytes()).hexdigest())
            self.assertNotIn("path", payload["video"])  # the local path is never sent, so never bound
            transition(con, pid, "approve")
            self.assertEqual(get_post(con, pid)["approved_payload_sha256"], approval_payload_sha256(get_post(con, pid)))
            with self.assertRaisesRegex(ValidationError, "unapprove it first"):
                set_post_video(con, pid, None)
            record = json.loads(get_post(con, pid)["video_json"])
            record["sha256"] = "0" * 64
            con.execute("UPDATE posts SET video_json=? WHERE id=?", (json.dumps(record), pid))  # behind approval's back
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch_reapproval_required"):
                claim_sending(con, pid)
            self.assertEqual(get_post(con, pid)["status"], "approved")


class OfflineCliTests(Flipped):
    def test_attach_show_check_approve_and_detach(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "tt-me", "tiktok", CAPTION)
            other = add_post(con, "tt-me", "tiktok", "second")
            text_post = add_post(con, "bsky", "bluesky", "text only")
        status, _, err = self.cli("video-attach", text_post, "--file", str(self.video), env={})
        self.assertEqual(status, 2)
        self.assertIn("TikTok drafts only", err)
        status, out, _ = self.cli("video-attach", pid, "--file", str(self.video), env={})
        self.assertEqual(status, 0)
        self.assertIn("nothing becomes public", out)
        status, out, _ = self.cli("show", pid, env={})
        shown = json.loads(out)["video"]
        self.assertTrue(shown["file_matches_record"])
        self.assertIn("no: in creator_inbox mode", shown["caption_sent"])
        self.video.write_bytes(av_mp4(mdat_size=5000))  # the file changes before approval
        status, out, _ = self.cli("check", pid, env={})
        self.assertEqual(status, 1)
        self.assertIn("approval_video_changed_reapproval_required", out)
        status, _, err = self.cli("approve", pid, env={})
        self.assertEqual(status, 2)
        self.assertIn("Refusing to approve", err)
        self.assertEqual(self.row(pid)["status"], "draft")
        self.assertEqual(self.cli("video-detach", pid, env={})[0], 0)
        self.assertIsNone(self.row(pid)["video_json"])
        status, _, err = self.cli("video-attach", other, "--file", str(self.video), "--mode", "direct_post", env={})
        self.assertEqual(status, 2)
        self.assertIn("explicit --privacy-level", err)


# --------------------------------------------------------------------------- flipped: creator inbox

class FlippedInboxTests(Flipped):
    def test_inbox_delivery_is_needs_review_never_published(self):
        pid = self.approved()
        fake = FakeTikTok()
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertEqual(fake.routes, ["user_info", "inbox_init", "upload", "status", "status"])
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"], row["remote_id"], row["remote_url"]),
                         ("needs_review", tiktok.INBOX_REASON, tiktok.REMOTE_PREFIX + PUBLISH_ID, None))
        self.assertIn(f"delivery=creator_inbox publish_id={PUBLISH_ID} final_status=SEND_TO_USER_INBOX",
                      self.events(pid, "needs_review")[0])
        self.assertIn("NOT public", out)
        with connect(str(self.db)) as con:  # nothing counts as a public post in reports or metrics
            self.assertEqual(published_posts(con), [])
            self.assertNotIn("tiktok", report(con))
        # Only the video goes to the inbox: no caption, privacy or interaction settings.
        self.assertEqual(fake.json_body("inbox_init"), {"source_info": {
            "source": "FILE_UPLOAD", "video_size": self.video.stat().st_size,
            "chunk_size": self.video.stat().st_size, "total_chunk_count": 1}})
        put = fake.all("upload")[0]
        self.assertEqual(put.data, self.video.read_bytes())
        self.assertEqual(put.get_header("Content-range"), f"bytes 0-{self.video.stat().st_size - 1}/"
                                                          f"{self.video.stat().st_size}")
        self.assertEqual(put.get_header("Content-type"), "video/mp4")
        self.assertIsNone(put.get_header("Authorization"))  # the token never goes to the upload host
        self.assertEqual(fake.requests[0].get_header("Authorization"), "Bearer " + FAKE_TOKEN)
        self.assertEqual(parse_qs(urlsplit(fake.requests[0].full_url).query)["fields"], ["open_id,display_name"])
        # Write marker: unset through the init, set before the first chunk PUT.
        self.assertEqual(fake.write_flags[:3], [False, False, True])
        self.assertEqual(self.sleeps, [10, 10])
        self.assert_no_secret(out, err)
        # A later run never resends it.
        again = FakeTikTok()
        with patch("reachweaver.publishers._send_http", side_effect=again):
            status, _, _ = self.cli("publish", "--live")
        self.assertEqual((status, again.routes), (0, []))

    def test_multi_chunk_upload_uses_206_then_201(self):
        pid = self.approved()
        size = self.video.stat().st_size
        fake = FakeTikTok()
        with patch("reachweaver.tiktok.SINGLE_CHUNK_MAX", 1000), patch("reachweaver.tiktok.CHUNK_SIZE", 1024):
            status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        count = size // 1024
        self.assertEqual(fake.json_body("inbox_init")["source_info"]["total_chunk_count"], count)
        puts = fake.all("upload")
        self.assertEqual(len(puts), count)
        self.assertEqual(b"".join(p.data for p in puts), self.video.read_bytes())
        self.assertEqual(puts[-1].get_header("Content-range"), f"bytes {(count - 1) * 1024}-{size - 1}/{size}")

    def test_wrong_creator_or_401_blocks_before_the_claim(self):
        for fake, needle in ((FakeTikTok(user={"open_id": "someone-else", "display_name": NAME}), "identity_mismatch"),
                             (FakeTikTok(user={"open_id": OPEN_ID, "display_name": "Other"}), "identity_mismatch"),
                             (FakeTikTok(errors={"user_info": 401}), "tiktok_user_info_http_401")):
            with self.subTest(needle=needle):
                self.fresh_db()
                pid = self.approved()
                status, out, err = self.send(fake, pid)
                self.assertEqual(status, 2)
                self.assertIn(needle, out)
                self.assertEqual(fake.routes, ["user_info"])
                self.assertEqual(self.row(pid)["status"], "approved")
                self.assertEqual(self.events(pid, "send_claimed"), [])
                self.assert_no_secret(out, err)

    def test_changed_file_blocks_before_any_request(self):
        pid = self.approved()
        self.video.write_bytes(av_mp4(mdat_size=4097))
        fake = FakeTikTok()
        status, out, _ = self.send(fake, pid)
        self.assertEqual((status, fake.routes), (2, []))
        self.assertIn("approval_video_changed_reapproval_required", out)
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertTrue(self.events(pid, "approval_binding_failed"))

    def test_optout_and_pause_block_before_any_request(self):
        pid = self.approved(text="Thanks @blockeduser for the review")
        optout = self.tmp / "optout.json"
        optout.write_text(json.dumps({"version": 1, "entries": [
            {"handle": "blockeduser", "platform": "tiktok", "recorded_on": "2026-10-09"}]}), encoding="utf-8")
        fake = FakeTikTok()
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid, "--optout", str(optout))
            self.assertEqual(status, 2)
            self.assertIn("BLOCKED", out)
            self.assertEqual(self.cli("pause", env={})[0], 0)
            status, _, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("Emergency pause", err)
        self.assertEqual(fake.routes, [])

    def test_init_refusal_after_claim_is_certain_not_sent_and_never_uploads(self):
        for fake, needle in ((FakeTikTok(errors={"inbox_init": 403}), "tiktok_init_http_403"),
                             (FakeTikTok(init={"publish_id": PUBLISH_ID, "upload_url": "https://evil.example/u"}),
                              "tiktok_init_upload_url_refused")):
            with self.subTest(needle=needle):
                self.fresh_db()
                pid = self.approved()
                status, out, err = self.send(fake, pid)
                self.assertEqual(status, 3)
                self.assertNotIn("upload", fake.routes)
                row = self.row(pid)
                self.assertEqual(row["status"], "needs_review")
                self.assertTrue(row["last_error"].startswith(needle))
                self.assertIn("class=certain_not_sent", self.events(pid, "needs_review")[0])
                self.assert_no_secret(out, err)

    def test_post_write_failures_are_ambiguous_and_never_retried(self):
        cases = ((FakeTikTok(errors={"upload": 500}), "tiktok_upload_http_500_uncertain", 1),
                 (FakeTikTok(upload_codes=[200]), "tiktok_upload_unexpected_status_200_uncertain", 1),
                 (FakeTikTok(statuses=("FAILED",), status_extra={"fail_reason": "file_format_check_failed"}),
                  "tiktok_status_failed_file_format_check_failed_not_posted", 1),
                 (FakeTikTok(statuses=("PROCESSING_UPLOAD",)), "tiktok_status_unconfirmed_uncertain", 1),
                 (FakeTikTok(errors={"status": 429}), "tiktok_status_http_429_uncertain", 1))
        for fake, code, uploads in cases:
            with self.subTest(code=code), patch("reachweaver.tiktok.STATUS_MAX_CHECKS", 3):
                self.fresh_db()
                pid = self.approved()
                status, out, err = self.send(fake, pid)
                self.assertEqual(status, 3, out + err)
                self.assertEqual(fake.routes.count("upload"), uploads)
                row = self.row(pid)
                self.assertEqual((row["status"], row["last_error"]), ("needs_review", code))
                self.assertIn("class=ambiguous", self.events(pid, "needs_review")[0])
                self.assert_no_secret(out, err)

    def test_direct_post_mode_refused_while_its_switch_is_off(self):
        pid = self.approved("--mode", "direct_post", "--privacy-level", "SELF_ONLY", "--commercial", "none")
        fake = FakeTikTok()
        status, out, _ = self.send(fake, pid)
        self.assertEqual((status, fake.routes), (2, []))
        self.assertIn("direct_post is off in the capability registry", out)
        self.assertEqual(self.row(pid)["status"], "approved")


# --------------------------------------------------------------------------- flipped: direct post

class FlippedDirectPostTests(Flipped):
    DIRECT = True

    def direct(self, level="FOLLOWER_OF_CREATOR", *extra):
        return self.approved("--mode", "direct_post", "--privacy-level", level, "--commercial", "own_brand", *extra)

    def test_public_id_is_the_only_published_outcome(self):
        pid = self.direct("FOLLOWER_OF_CREATOR", "--allow-comment")
        fake = FakeTikTok(statuses=("PROCESSING_UPLOAD", "PUBLISH_COMPLETE"),
                          status_extra={"publicaly_available_post_id": [7300000000000000001]})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 0, out + err)
        self.assertEqual(fake.routes, ["user_info", "creator_info", "direct_init", "upload", "status", "status"])
        body = fake.json_body("direct_init")
        self.assertEqual(body["post_info"], {
            "title": CAPTION, "privacy_level": "FOLLOWER_OF_CREATOR", "disable_comment": False,
            "disable_duet": True, "disable_stitch": True, "brand_content_toggle": False,
            "brand_organic_toggle": True, "is_aigc": False})
        row = self.row(pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]),
                         ("published", "7300000000000000001", None))
        self.assertIn("delivery=direct_post", self.events(pid, "published")[0])
        self.assert_no_secret(out, err)

    def test_complete_without_public_id_stays_needs_review(self):
        pid = self.direct("SELF_ONLY")
        fake = FakeTikTok(statuses=("PUBLISH_COMPLETE",))
        with patch("reachweaver.tiktok.STATUS_MAX_CHECKS", 2):
            status, _, _ = self.send(fake, pid)
        self.assertEqual(status, 3)
        self.assertEqual(self.row(pid)["last_error"], "tiktok_direct_post_complete_no_public_id_uncertain")

    def test_privacy_level_not_offered_or_locked_interaction_blocks_before_claim(self):
        cases = (("PUBLIC_TO_EVERYONE", (), {}, "tiktok_privacy_level_not_offered"),
                 ("SELF_ONLY", ("--allow-duet",), {"duet_disabled": True}, "tiktok_duet_disabled_by_creator"),
                 ("SELF_ONLY", (), {"creator_nickname": "Someone"}, "identity_mismatch"),
                 ("SELF_ONLY", (), {"max_video_post_duration_sec": 5}, "tiktok_video_too_long_for_creator"))
        for level, extra, creator, needle in cases:
            with self.subTest(needle=needle):
                self.fresh_db()
                pid = self.direct(level, *extra)
                info = {**FakeTikTok().creator, **creator}
                fake = FakeTikTok(creator=info)
                status, out, _ = self.send(fake, pid)
                self.assertEqual(status, 2)
                self.assertIn(needle, out)
                self.assertEqual(fake.routes, ["user_info", "creator_info"])
                self.assertEqual(self.row(pid)["status"], "approved")
                self.assertEqual(self.events(pid, "send_claimed"), [])


TT_FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "tiktok"


def tt_fixture(name: str) -> dict:
    return json.loads((TT_FIXTURES / name).read_text(encoding="utf-8"))


class ChunkFault(FakeTikTok):
    """FakeTikTok whose Nth chunk PUT replays a fixture: an error status raises, a 2xx is returned."""

    def __init__(self, *, fixture, at, **kwargs):
        super().__init__(**kwargs)
        self.fault, self.at = tt_fixture(fixture), at

    def __call__(self, req):
        parts = urlsplit(req.full_url)
        if self.ROUTES.get((req.get_method(), parts.hostname, parts.path)) == "upload" \
                and self.routes.count("upload") + 1 == self.at:
            self.requests.append(req)
            self.routes.append("upload")
            self.write_flags.append(write_started())
            if self.fault["status"] >= 400:
                raise http_error(req.full_url, self.fault["status"])
            return FakeResponse(self.fault["status"], b"")
        return super().__call__(req)


class ChunkFixtureTests(Flipped):
    """Issue #23: partial chunk-upload failures. Past the first byte nothing is retried or polled."""

    def test_fixtures_are_valid_synthetic_and_carry_no_credential(self):
        names = sorted(p.name for p in TT_FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(names), 12)
        for name in names:
            with self.subTest(fixture=name):
                fx = tt_fixture(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                self.assertNotIn("upload_token", json.dumps(fx))

    def test_partial_chunk_failures_are_ambiguous_and_never_retried_or_polled(self):
        cases = (("upload-chunk-2-http-500.json", 2, "tiktok_upload_http_500_uncertain"),
                 ("upload-chunk-2-http-416.json", 2, "tiktok_upload_http_416_uncertain"),
                 ("upload-chunk-1-status-200.json", 1, "tiktok_upload_unexpected_status_200_uncertain"),
                 ("upload-final-status-206.json", None, "tiktok_upload_unexpected_status_206_uncertain"))
        for fixture, at, code in cases:
            with self.subTest(fixture=fixture):
                self.fresh_db()
                pid = self.approved()
                size = self.video.stat().st_size
                at = at or size // 1024  # None: the final chunk of this plan
                fake = ChunkFault(fixture=fixture, at=at)
                with patch("reachweaver.tiktok.SINGLE_CHUNK_MAX", 1000), patch("reachweaver.tiktok.CHUNK_SIZE", 1024):
                    status, out, err = self.send(fake, pid)
                self.assertEqual(status, 3, out + err)
                self.assertGreater(size // 1024, at - 1)  # the failing chunk exists in this plan
                self.assertEqual(fake.routes, ["user_info", "inbox_init"] + ["upload"] * at)  # no retry, no poll
                self.assertEqual(fake.write_flags[2:], [True] * at)
                row = self.row(pid)
                self.assertEqual((row["status"], row["last_error"]), ("needs_review", code))
                self.assertIn("class=ambiguous", self.events(pid, "needs_review")[0])
                self.assertEqual(self.events(pid, "published"), [])
                self.assert_no_secret(out, err)


class CreatorOptionFixtureTests(Flipped):
    """Issue #23: invalid creator_info shapes and an unaudited (SELF_ONLY-only) creator, Direct Post."""

    DIRECT = True

    def direct(self, level="SELF_ONLY"):
        return self.approved("--mode", "direct_post", "--privacy-level", level, "--commercial", "own_brand")

    def creator(self, fixture):
        return tt_fixture(fixture)["body"]["data"]

    def test_invalid_creator_options_block_before_the_claim(self):
        cases = (("creator-info-invalid-empty-options.json", "tiktok_privacy_level_not_offered"),
                 ("creator-info-invalid-missing-options.json", "tiktok_creator_info_missing_privacy_options"),
                 ("creator-info-invalid-options-not-strings.json", "tiktok_creator_info_missing_privacy_options"),
                 ("creator-info-invalid-options-not-list.json", "tiktok_creator_info_missing_privacy_options"),
                 ("creator-info-invalid-missing-max-duration.json", "tiktok_creator_info_missing_max_duration"),
                 ("creator-info-invalid-max-duration-string.json", "tiktok_creator_info_missing_max_duration"),
                 ("creator-info-invalid-max-duration-zero.json", "tiktok_creator_info_missing_max_duration"))
        for fixture, needle in cases:
            with self.subTest(fixture=fixture):
                self.fresh_db()
                pid = self.direct()
                fake = FakeTikTok(creator=self.creator(fixture))
                status, out, err = self.send(fake, pid)
                self.assertNotEqual(status, 0, out + err)
                self.assertIn(needle, out + err + (self.row(pid)["last_error"] or ""))
                self.assertEqual(fake.routes, ["user_info", "creator_info"])  # no init, no upload
                self.assertEqual(self.events(pid, "send_claimed"), [])
                self.assertEqual(self.events(pid, "published"), [])
                self.assert_no_secret(out, err)

    def test_unaudited_creator_offers_only_private_so_other_levels_are_blocked(self):
        for level in ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"):
            with self.subTest(level=level):
                self.fresh_db()
                pid = self.direct(level)
                fake = FakeTikTok(creator=self.creator("creator-info-unaudited-private-only.json"))
                status, out, _ = self.send(fake, pid)
                self.assertEqual(status, 2, out)
                self.assertIn("tiktok_privacy_level_not_offered", out)
                self.assertIn("SELF_ONLY", out)
                self.assertEqual(fake.routes, ["user_info", "creator_info"])
                self.assertEqual(self.row(pid)["status"], "approved")

    def test_unaudited_creator_self_only_is_sent_private_and_never_counted_as_published(self):
        pid = self.direct("SELF_ONLY")
        fake = FakeTikTok(creator=self.creator("creator-info-unaudited-private-only.json"),
                          statuses=("PUBLISH_COMPLETE",))  # private: TikTok returns no public post id
        with patch("reachweaver.tiktok.STATUS_MAX_CHECKS", 2):
            status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertEqual(fake.json_body("direct_init")["post_info"]["privacy_level"], "SELF_ONLY")
        row = self.row(pid)
        self.assertEqual((row["status"], row["remote_id"]), ("needs_review", None))
        self.assertEqual(row["last_error"], "tiktok_direct_post_complete_no_public_id_uncertain")
        self.assertEqual(self.events(pid, "published"), [])


if __name__ == "__main__":
    unittest.main()
