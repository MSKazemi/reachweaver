"""YouTube video upload (issue #22, owner-approved 2026-10-09; disabled until a live receipt). Every HTTP exchange is mocked: no network, no upload.

Two halves:
* ``UnreachableTests``: with the bundled registry (YouTube adapter ``none``, video ``api_gated``)
  and an empty ``policy.SUPPORTED_LIVE_VIDEO`` every entry point refuses before a credential read.
* ``Flipped*`` tests: a TEMPORARY registry copy with YouTube set to ``live_video`` (and
  ``SUPPORTED_LIVE_VIDEO`` patched to match) runs the mocked resumable flow: happy path,
  308 Resume Incomplete, 5xx, quotaExceeded 403, wrong channel, file changes and pause.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_media import mp4_bytes  # noqa: E402

from reachweaver import capabilities, youtube  # noqa: E402
from reachweaver.accounts import env_prefix_conflicts, load_accounts, missing_credentials  # noqa: E402
from reachweaver.capabilities import load_registry, require_capability, validate_registry  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.connections import check_connection  # noqa: E402
from reachweaver.policy import SUPPORTED_LIVE_VIDEO, ValidationError, validate_post  # noqa: E402
from reachweaver.publishers import PublishError, reset_write_marker, write_started
from gate_support import publish, publish_video  # noqa: E402
from reachweaver.storage import (APPROVAL_BINDING_TAG, APPROVAL_BINDING_TAG_VIDEO, add_post,  # noqa: E402
                                 approval_payload, claim_sending, connect, get_post, set_paused,
                                 set_post_video, transition)

FAKE_TOKEN = "fake-youtube-access-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_YT"
ENV = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}
LIVE_ENV = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
CHANNEL = "UCabcdefghijklmnopqrstuv"
OTHER_CHANNEL = "UCzzzzzzzzzzzzzzzzzzzzzz"
VIDEO_ID = "dQw4w9WgXcQ"
SESSION_SECRET = "SESSIONCAPABILITY0123456789"
SESSION_URI = f"https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id={SESSION_SECRET}"
DESCRIPTION = "Release walkthrough: https://example.com/release"
CHUNK = 256 * 1024


def account(**overrides):
    base = {"id": "yt-me", "platform": "youtube", "env_prefix": PREFIX, "handle": "MyChannel",
            "channel_id": CHANNEL}
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self._body = body

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url, code, *, reason=None, headers=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    body = {"error": {"code": code, "message": "Bearer " + FAKE_TOKEN + " " + url,
                      "errors": [{"reason": reason or "backendError", "message": FAKE_TOKEN}]}}
    # The message and body carry the token and URL: they must never reach an error string.
    return HTTPError(url, code, "Bearer " + FAKE_TOKEN, message, io.BytesIO(json.dumps(body).encode()))


class FakeYouTube:
    """Routes mocked requests. ``errors`` maps a route ("channels", "init", "put1", "put2", ...)
    to (code, reason); ``ranges`` overrides the Range a 308 reports; ``on_put`` runs a hook."""

    def __init__(self, *, size, channels=None, errors=None, ranges=None, final=None, location=SESSION_URI,
                 on_put=None):
        self.size = size
        self.requests: list = []
        self.routes: list[str] = []
        self.channels = channels if channels is not None else [{"id": CHANNEL}]
        self.errors = errors or {}
        self.ranges = ranges or {}
        self.final = final
        self.location = location
        self.on_put = on_put
        self.received = b""

    def route(self, req) -> str:
        parts = urlsplit(req.full_url)
        if (parts.scheme, parts.hostname) != ("https", "www.googleapis.com"):
            raise AssertionError("request to an unexpected host")
        method = req.get_method()
        if method == "GET" and parts.path == "/youtube/v3/channels":
            return "channels"
        if method == "POST" and parts.path == "/upload/youtube/v3/videos" and "upload_id" not in parts.query:
            return "init"
        if method == "PUT" and req.full_url == SESSION_URI:
            return f"put{sum(1 for r in self.routes if r.startswith('put')) + 1}"
        raise AssertionError("unexpected request")

    def __call__(self, req):
        name = self.route(req)
        self.requests.append(req)
        self.routes.append(name)
        if name in self.errors:
            code, reason = self.errors[name]
            raise http_error(req.full_url, code, reason=reason)
        if name == "channels":
            return FakeResponse(200, json.dumps({"kind": "youtube#channelListResponse", "items": self.channels}).encode())
        if name == "init":
            return FakeResponse(200, b"", {"Location": self.location} if self.location else {})
        if self.on_put:
            self.on_put(name)
        self.received += req.data
        last = int(req.headers["Content-range"].split(" ")[1].split("/")[0].split("-")[1])
        if last + 1 < self.size:
            upper = self.ranges.get(name, last)
            raise http_error(req.full_url, 308, headers={"Range": f"bytes=0-{upper}"})
        body = self.final or {"id": VIDEO_ID, "snippet": {"channelId": CHANNEL, "title": "Demo"},
                              "status": {"privacyStatus": "private", "uploadStatus": "uploaded",
                                         "selfDeclaredMadeForKids": False}}
        return FakeResponse(201, json.dumps(body).encode())

    def headers(self, name):
        return dict(self.requests[self.routes.index(name)].header_items())

    def init_body(self):
        return json.loads(self.requests[self.routes.index("init")].data.decode())


def flipped_registry() -> dict:
    """The registry change the owner would make AFTER review and a live receipt (temp copy only)."""
    registry = copy.deepcopy(load_registry())
    entry = next(e for e in registry["platforms"] if e["id"] == "youtube")
    entry.update({
        "reachweaver_video_upload": "live",  # the unified video switch (integration, 2026-10-09)
        "rate_limits": {
            "summary": "videos.insert: own quota bucket, default 100 calls per day at 1 unit each",
            "source": "https://developers.google.com/youtube/v3/determine_quota_cost",
            "accessed": "2026-10-09",
            "facts": [{"scope": "API project", "action": "videos.insert calls (default bucket)",
                       "limit": 100, "unit": "calls", "window_seconds": 86400}],
        },
    })
    entry["formats"]["video"] = "api_direct"
    return registry


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.db = str(self.tmp / "outbox.sqlite")
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
        reset_write_marker()

    def video_file(self, name="clip.mp4", *, size_hint=600_000, width=1080, height=1920, seconds=30):
        data = mp4_bytes(width, height, seconds, mdat_size=size_hint)
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["--db", self.db, "--accounts", str(self.config), *argv])
            except SystemExit as exc:  # argparse usage errors
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def draft(self, *, path=None, privacy="private", kids="no", tags=("release", "open source"), approve=True,
              text=DESCRIPTION):
        with connect(self.db) as con:
            pid = add_post(con, "yt-me", "youtube", text)
        path = path or self.video_file()
        argv = ["youtube-attach", pid, "--file", str(path), "--title", "Release 1.0 walkthrough",
                "--privacy", privacy, "--made-for-kids", kids, "--category-id", "28"]
        for tag in tags:
            argv += ["--tag", tag]
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 0, out + err)
        if approve:
            with connect(self.db) as con:
                transition(con, pid, "approve")
        return pid, path

    def post(self, pid):
        with connect(self.db) as con:
            return get_post(con, pid)

    def events(self, pid):
        with connect(self.db) as con:
            return [(r["action"], r["detail"]) for r in con.execute(
                "SELECT action, detail FROM event_log WHERE post_id=? ORDER BY id", (pid,))]

    def db_dump(self) -> str:
        con = sqlite3.connect(self.db)
        try:
            return "\n".join(str(row) for table in ("posts", "event_log", "settings")
                             for row in con.execute(f"SELECT * FROM {table}"))
        finally:
            con.close()


class UnreachableTests(Workspace):
    """Under the bundled registry and policy the adapter cannot be reached."""

    def test_bundled_registry_keeps_youtube_gated(self):
        entry = next(e for e in load_registry()["platforms"] if e["id"] == "youtube")
        self.assertEqual((entry["reachweaver_adapter"], entry["formats"]["video"], entry["status"]),
                         ("none", "api_gated", "requires-review"))
        self.assertEqual(SUPPORTED_LIVE_VIDEO, frozenset())
        with self.assertRaisesRegex(ValidationError, "api_gated"):
            require_capability("youtube", "video", "publish")

    def test_library_entry_points_refused_before_any_request_or_credential(self):
        spec, _ = youtube.build_video_spec(self.video_file(), title="T", tags=[], privacy_status="private",
                                           made_for_kids=False, category_id="28")
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.youtube._token") as token:
            with self.assertRaisesRegex(ValidationError, "not enabled for youtube"):
                publish_video(account(), DESCRIPTION, "p1", spec)
            with self.assertRaisesRegex(ValidationError, "not yet implemented for youtube"):
                publish(account(), DESCRIPTION, "p1")
            with self.assertRaisesRegex(ValidationError, "platform_has_no_authorized_live_connector"):
                check_connection(account())
        send.assert_not_called()
        token.assert_not_called()

    def test_cli_live_publish_skips_video_and_keeps_post_approved(self):
        pid, _ = self.draft()
        with patch("reachweaver.publishers._send_http") as send:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=LIVE_ENV)
        self.assertEqual(code, 2)
        self.assertIn("requires manual publishing", out)
        send.assert_not_called()
        self.assertEqual(self.post(pid)["status"], "approved")

    def test_registry_rejects_live_video_elsewhere_and_needs_matching_policy(self):
        registry = flipped_registry()
        self.assertIn("'live' video switch must equal policy.SUPPORTED_LIVE_VIDEO", "; ".join(validate_registry(registry)))
        with patch("reachweaver.capabilities.SUPPORTED_LIVE_VIDEO", frozenset({"youtube"})):
            self.assertEqual(validate_registry(registry), [])
            bad = copy.deepcopy(registry)
            entry = next(e for e in bad["platforms"] if e["id"] == "youtube")
            entry["formats"]["video"] = "api_gated"  # #22 rule kept: a live upload needs video api_direct
            self.assertIn("youtube: a live video mode needs video api_direct", "; ".join(validate_registry(bad)))
            no_facts = copy.deepcopy(registry)
            next(e for e in no_facts["platforms"] if e["id"] == "youtube")["rate_limits"].pop("facts")
            self.assertIn("needs sourced rate_limits.facts", "; ".join(validate_registry(no_facts)))
        other = copy.deepcopy(load_registry())
        tiktok = next(e for e in other["platforms"] if e["id"] == "tiktok")
        tiktok.update(reachweaver_video_upload="off")
        self.assertIn("tiktok: reachweaver_video_upload only apply to youtube", "; ".join(validate_registry(other)))
        legacy = copy.deepcopy(load_registry())  # the draft's adapter value was folded into the switch
        next(e for e in legacy["platforms"] if e["id"] == "youtube").update(reachweaver_adapter="live_video")
        self.assertIn("invalid reachweaver_adapter 'live_video'", "; ".join(validate_registry(legacy)))
        self.assertEqual(json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8")), load_registry())


class ConfigTests(unittest.TestCase):
    def load(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
            return load_accounts(path)

    def test_adapter_and_manual_accounts(self):
        loaded = self.load(account(channel_id=f" {CHANNEL} "))["yt-me"]
        self.assertEqual(loaded["channel_id"], CHANNEL)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(loaded), [PREFIX + "_ACCESS_TOKEN"])
            manual = self.load({"id": "yt-manual", "platform": "youtube", "env_prefix": PREFIX})["yt-manual"]
            self.assertEqual(missing_credentials(manual), [])  # manual-export metadata needs no token

    def test_invalid_configs_rejected(self):
        for bad, needle in ((account(channel_id="UC123"), "channel_id must be"),
                            (account(channel_id=7), "channel_id must be"),
                            (account(playlist_id="PL1"), "unsupported fields playlist_id"),
                            (account(client_secret="x"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)

    def test_env_prefix_overlap_covers_the_youtube_suffix(self):
        found = env_prefix_conflicts([account(), {"id": "li", "platform": "linkedin", "env_prefix": PREFIX + "_ACCESS"}])
        self.assertTrue(found)


class SpecAndBindingTests(Workspace):
    def test_attach_defaults_private_and_requires_made_for_kids(self):
        with connect(self.db) as con:
            pid = add_post(con, "yt-me", "youtube", DESCRIPTION)
        path = self.video_file()
        code, _, err = self.cli("youtube-attach", pid, "--file", str(path), "--title", "T", "--category-id", "28")
        self.assertEqual(code, 2)
        self.assertIn("--made-for-kids", err)
        code, out, _ = self.cli("youtube-attach", pid, "--file", str(path), "--title", "T", "--category-id", "28",
                                "--made-for-kids", "no")
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual(shown["video"]["privacy_status"], "private")
        self.assertIs(shown["video"]["made_for_kids"], False)
        self.assertIn("likely_short", shown["shorts"])
        self.assertIn("no API flag", shown["shorts"])
        self.assertIn("Nothing was uploaded", shown["note"])

    def test_attach_refuses_bad_files_metadata_and_non_drafts(self):
        pid, path = self.draft(approve=False)
        mov = self.tmp / "clip.mov"
        mov.write_bytes(mp4_bytes(1080, 1920, 5, brand=b"qt  "))
        link = self.tmp / "link.mp4"
        link.symlink_to(path)
        text = self.tmp / "notes.mp4"
        text.write_text("not a video")
        base = ["youtube-attach", pid, "--made-for-kids", "no", "--category-id", "28"]
        cases = (([*base, "--file", str(mov), "--title", "T"], "MP4"),
                 ([*base, "--file", str(link), "--title", "T"], "symlink"),
                 ([*base, "--file", str(text), "--title", "T"], "MP4"),
                 ([*base, "--file", str(self.tmp / "missing.mp4"), "--title", "T"], "missing"),
                 ([*base, "--file", str(path), "--title", "a <b> title"], "may not contain"),
                 ([*base, "--file", str(path), "--title", "x" * 101], "limit is 100"),
                 ([*base, "--file", str(path), "--title", "T", "--tag", "a,b"], "free of ','"),
                 ([*base, "--file", str(path), "--title", "T", *sum((["--tag", f"tag {i:03d}xxxxxx"] for i in range(40)), [])],
                  "limit is 500"),
                 (["youtube-attach", pid, "--file", str(path), "--title", "T", "--made-for-kids", "no",
                   "--category-id", "music"], "numeric category"))
        for argv, needle in cases:
            with self.subTest(needle=needle):
                code, out, err = self.cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn(needle, out + err)
        with connect(self.db) as con:
            transition(con, pid, "approve")
            with self.assertRaisesRegex(ValidationError, "only change on a draft"):
                set_post_video(con, pid, None)
            other = add_post(con, "yt-me", "bluesky", "hello")
            spec = youtube.decode_video(get_post(con, pid)["video_json"])
            with self.assertRaisesRegex(ValidationError, "only supported for TikTok and YouTube drafts, not bluesky"):
                set_post_video(con, other, spec)

    def test_tag_counting_follows_the_documented_rule(self):
        self.assertEqual(youtube.tags_length(["release", "open source"]), 7 + 13 + 1)
        self.assertEqual(youtube.tags_length([]), 0)

    def test_description_rules_tighten_validate_post(self):
        self.assertTrue(any("'<' or '>'" in p for p in validate_post("youtube", "See <here>")))
        self.assertTrue(any("5000" in p for p in validate_post("youtube", "é" * 2600)))
        self.assertEqual(validate_post("youtube", DESCRIPTION), [])

    def test_video1_payload_binds_every_sent_field_and_text_posts_stay_v1(self):
        pid, _ = self.draft()
        with connect(self.db) as con:
            row = get_post(con, pid)
            payload = approval_payload(row)
            text_pid = add_post(con, "yt-me", "youtube", "Text only")
            self.assertEqual(approval_payload(get_post(con, text_pid))["binding"], APPROVAL_BINDING_TAG)
            self.assertNotIn("video", approval_payload(get_post(con, text_pid)))
        self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG_VIDEO)
        # The shared video record (video.py): everything except the local path is bound.
        self.assertEqual(set(payload["video"]), {"kind", "version", "platform", "sha256", "bytes", "mime",
                                                 "duration_seconds", "width", "height", "video_codec",
                                                 "frame_rate_fps", "platform_options"})
        self.assertEqual(set(payload["video"]["platform_options"]),
                         {"title", "tags", "category_id", "privacy_status", "self_declared_made_for_kids"})
        self.assertEqual(payload["content"], DESCRIPTION)  # the description is bound as content
        for field, value in (("privacy_status", "public"), ("self_declared_made_for_kids", True), ("title", "Other"),
                             ("tags", ["x"]), ("category_id", "22"), ("sha256", "0" * 64)):
            with self.subTest(field=field), connect(self.db) as con:
                spec = json.loads(get_post(con, pid)["video_json"])
                original = json.loads(get_post(con, pid)["video_json"])
                (spec if field == "sha256" else spec["platform_options"])[field] = value
                con.execute("UPDATE posts SET video_json=? WHERE id=?", (json.dumps(spec), pid))
                with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                    claim_sending(con, pid)
                con.execute("UPDATE posts SET video_json=? WHERE id=?", (json.dumps(original), pid))
        with connect(self.db) as con:
            con.execute("UPDATE posts SET video_json='not json' WHERE id=?", (pid,))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(con, pid)

    def test_approve_refuses_a_changed_file_and_show_reports_it(self):
        pid, path = self.draft(approve=False)
        path.write_bytes(path.read_bytes() + b"x")
        with connect(self.db) as con, self.assertRaisesRegex(ValidationError, "approval_video_changed"):
            transition(con, pid, "approve")
        code, out, _ = self.cli("show", pid)
        self.assertEqual(json.loads(out)["video"]["file_status"], "youtube_video_file_changed")
        code, out, _ = self.cli("check", pid)
        self.assertEqual(code, 1)
        self.assertIn("youtube_video_file_changed", out)

    def test_optout_handle_in_title_or_tag_is_refused(self):
        optout = self.tmp / "optout.json"
        optout.write_text(json.dumps({"version": 1, "entries": [{"handle": "@alice.example.com"}]}), encoding="utf-8")
        with connect(self.db) as con:
            pid = add_post(con, "yt-me", "youtube", DESCRIPTION)
        code, out, err = self.cli("youtube-attach", pid, "--file", str(self.video_file()), "--title",
                                  "Thanks @alice.example.com", "--made-for-kids", "no", "--category-id", "28")
        self.assertEqual(code, 0, out + err)
        code, out, _ = self.cli("check", pid, "--optout", str(optout))
        self.assertEqual(code, 1)
        code, out, err = self.cli("approve", pid, "--optout", str(optout))
        self.assertEqual(code, 2)
        self.assertEqual(self.post(pid)["status"], "draft")

    def test_purge_minimization_clears_the_video_spec(self):
        from reachweaver.privacy import apply_purge
        pid, _ = self.draft(approve=False)
        with connect(self.db) as con:
            transition(con, pid, "cancel")
            apply_purge(con, {"actions": {"cancelled_posts_minimize": [pid]}})
            self.assertIsNone(get_post(con, pid)["video_json"])


class FlippedRegistry(Workspace):
    """Patches a temp registry copy and every SUPPORTED_LIVE_VIDEO binding; the bundled file is untouched."""

    def setUp(self):
        super().setUp()
        path = self.tmp / "platform_registry.json"
        path.write_text(json.dumps(flipped_registry()), encoding="utf-8")
        live = frozenset({"youtube"})
        for target, value in (("reachweaver.capabilities.REGISTRY_FILE", path),
                              ("reachweaver.policy.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.capabilities.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.publishers.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.connections.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.cli.SUPPORTED_LIVE_VIDEO", live),
                              ("reachweaver.youtube.CHUNK_SIZE", CHUNK),
                              ("reachweaver.ratelimit._sleep", lambda seconds: None)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_live(self, pid, fake):
        with patch("reachweaver.publishers._send_http", fake):
            return self.cli("publish", "--live", "--post-id", pid, env=LIVE_ENV)

    def assert_no_secret(self, *texts):
        for text in (*texts, self.db_dump()):
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn(SESSION_SECRET, text)


class FlippedHappyPathTests(FlippedRegistry):
    def test_resumable_upload_in_chunks_then_receipt(self):
        pid, path = self.draft()
        data = path.read_bytes()
        fake = FakeYouTube(size=len(data))
        code, out, err = self.run_live(pid, fake)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(fake.routes, ["channels", "init", "put1", "put2", "put3"])
        self.assertEqual(fake.received, data)  # exactly the approved bytes, once, in order
        init = fake.headers("init")
        self.assertEqual(init["Authorization"], "Bearer " + FAKE_TOKEN)
        self.assertEqual((init["X-upload-content-length"], init["X-upload-content-type"]), (str(len(data)), "video/mp4"))
        self.assertEqual(init["Content-type"], "application/json; charset=UTF-8")
        query = parse_qs(urlsplit(fake.requests[1].full_url).query)
        self.assertEqual(query, {"uploadType": ["resumable"], "part": ["snippet,status"]})
        self.assertEqual(fake.init_body(), {
            "snippet": {"title": "Release 1.0 walkthrough", "description": DESCRIPTION, "categoryId": "28",
                        "tags": ["release", "open source"]},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}})
        ranges = [fake.headers(f"put{i}")["Content-range"] for i in (1, 2, 3)]
        self.assertEqual(ranges, [f"bytes 0-{CHUNK - 1}/{len(data)}", f"bytes {CHUNK}-{2 * CHUNK - 1}/{len(data)}",
                                  f"bytes {2 * CHUNK}-{len(data) - 1}/{len(data)}"])
        self.assertEqual(parse_qs(urlsplit(fake.requests[0].full_url).query), {"part": ["id"], "mine": ["true"]})
        row = self.post(pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]),
                         ("published", VIDEO_ID, f"https://www.youtube.com/watch?v={VIDEO_ID}"))
        published = [d for a, d in self.events(pid) if a == "published"][0]
        self.assertIn("privacy_status=private", published)
        self.assertIn(f"channel_id={CHANNEL}", published)
        self.assertTrue(write_started())
        self.assert_no_secret(out, err)

    def test_library_publish_video_runs_its_own_identity_check(self):
        pid, _ = self.draft()
        spec = youtube.decode_video(self.post(pid)["video_json"])
        fake = FakeYouTube(size=spec["bytes"])
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", fake):
            receipt = publish_video(account(), DESCRIPTION, pid, spec)
        self.assertEqual(receipt[0], VIDEO_ID)
        self.assertEqual(fake.routes[0], "channels")


class DuplicateAvoidanceTests(FlippedRegistry):
    """Issue #22 duplicate avoidance: a published post is never uploaded again, and two runs racing
    on one approved post upload it at most once. Both rest on the durable ``claim_sending`` claim
    (approved -> sending), exercised here through the public CLI and storage functions."""

    def test_rerun_after_a_successful_publish_sends_nothing(self):
        pid, path = self.draft()
        first = FakeYouTube(size=path.stat().st_size)
        code, out, err = self.run_live(pid, first)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(first.routes, ["channels", "init", "put1", "put2", "put3"])
        before = (self.post(pid), self.events(pid))
        for argv in (("--post-id", pid), ()):  # by id, and as part of a whole-queue run
            with self.subTest(argv=argv):
                again = FakeYouTube(size=path.stat().st_size)
                with patch("reachweaver.publishers._send_http", again):
                    code, out, err = self.cli("publish", "--live", *argv, env=LIVE_ENV)
                self.assertEqual(again.routes, [])  # no channel check, no session, no byte
                self.assertEqual(code, 2 if argv else 0, out + err)  # not due: nothing to send
                self.assertNotIn("PUBLISHED", out)
        row = self.post(pid)
        self.assertEqual((row["status"], row["remote_id"]), ("published", VIDEO_ID))
        self.assertEqual((dict(row), self.events(pid)), (dict(before[0]), before[1]))  # nothing recorded either

    def test_a_second_run_started_mid_upload_sends_nothing(self):
        pid, path = self.draft()
        racer = FakeYouTube(size=path.stat().st_size)
        racer_result = []

        def second_run(name):
            if name == "put1":  # the first run holds the claim and is uploading
                racer_result.append(self.run_live(pid, racer))
        first = FakeYouTube(size=path.stat().st_size, on_put=second_run)
        code, out, err = self.run_live(pid, first)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(first.routes, ["channels", "init", "put1", "put2", "put3"])
        [(racer_code, racer_out, _)] = racer_result
        self.assertEqual((racer_code, racer.routes), (2, []))  # the post was not due: it was 'sending'
        self.assertNotIn("PUBLISHED", racer_out)
        self.assertEqual(self.post(pid)["status"], "published")
        self.assertEqual([a for a, _ in self.events(pid)].count("send_claimed"), 1)

    def test_two_runs_racing_for_the_claim_upload_once(self):
        """Both runs pass the pre-claim checks; the second wins the claim first. The loser's claim
        returns False, so it never opens an upload session."""
        pid, path = self.draft()
        winner = FakeYouTube(size=path.stat().st_size)
        loser = FakeYouTube(size=path.stat().st_size)
        real_claim, winner_result = claim_sending, []

        def racing_claim(con, post_id, *args, **kwargs):
            if not winner_result:  # the other process claims between our preflight and our claim
                with patch("reachweaver.cli.claim_sending", real_claim):
                    winner_result.append(self.run_live(pid, winner))
            return real_claim(con, post_id, *args, **kwargs)

        with patch("reachweaver.cli.claim_sending", racing_claim):
            code, out, err = self.run_live(pid, loser)
        self.assertEqual(code, 2, out + err)
        self.assertIn(f"SKIPPED {pid}: no longer eligible", out)
        self.assertEqual(loser.routes, ["channels"])  # read-only preflight only, no session, no byte
        [(winner_code, winner_out, winner_err)] = winner_result
        self.assertEqual(winner_code, 0, winner_out + winner_err)
        self.assertEqual(winner.routes, ["channels", "init", "put1", "put2", "put3"])
        uploads = [r for r in winner.routes + loser.routes if r == "init"]
        self.assertEqual(len(uploads), 1)  # at most one upload across both runs
        self.assertEqual(winner.received, path.read_bytes())
        self.assertEqual((self.post(pid)["status"], self.post(pid)["remote_id"]), ("published", VIDEO_ID))
        self.assertEqual([a for a, _ in self.events(pid)].count("send_claimed"), 1)
        self.assert_no_secret(out, err, winner_out, winner_err)


class FlippedFailureTests(FlippedRegistry):
    def assert_review(self, pid, code, out, *, needle, send_class):
        self.assertEqual(code, 3, out)
        row = self.post(pid)
        self.assertEqual(row["status"], "needs_review")
        self.assertIn(needle, row["last_error"])
        review = [d for a, d in self.events(pid) if a == "needs_review"][0]
        self.assertIn(f"class={send_class}", review)

    def test_wrong_channel_blocks_before_claim(self):
        pid, path = self.draft()
        for channels in ([{"id": OTHER_CHANNEL}], [{"id": CHANNEL}, {"id": OTHER_CHANNEL}], []):
            with self.subTest(channels=channels):
                fake = FakeYouTube(size=path.stat().st_size, channels=channels)
                code, out, err = self.run_live(pid, fake)
                self.assertEqual(code, 2)
                self.assertIn("identity", out)
                self.assertEqual(fake.routes, ["channels"])
                self.assertEqual(self.post(pid)["status"], "approved")
                self.assert_no_secret(out, err)

    def test_quota_exceeded_at_session_init_is_certain_not_sent(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size, errors={"init": (403, "quotaExceeded")})
        code, out, err = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_init_http_403_quotaExceeded", send_class="certain_not_sent")
        self.assertEqual(fake.routes, ["channels", "init"])
        self.assertFalse(write_started())
        self.assert_no_secret(out, err)

    def test_session_uri_off_endpoint_is_refused_before_any_byte(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size, location="https://evil.example.com/upload?upload_id=x")
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_init_session_uri_invalid", send_class="certain_not_sent")
        self.assertEqual(fake.routes, ["channels", "init"])

    def test_server_error_mid_upload_goes_to_review_and_is_never_resumed(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size, errors={"put2": (503, None)})
        code, out, err = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_upload_http_503", send_class="ambiguous")
        self.assertTrue(self.post(pid)["last_error"].endswith("_uncertain"))
        self.assertEqual(fake.routes, ["channels", "init", "put1", "put2"])  # no status query, no resume
        self.assert_no_secret(out, err)
        fake2 = FakeYouTube(size=path.stat().st_size)
        code, out, _ = self.run_live(pid, fake2)  # a second run sends nothing
        self.assertEqual(code, 2)
        self.assertEqual(fake2.routes, [])

    def test_network_error_mid_upload_is_ambiguous(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size)

        def drop(name):
            if name == "put2":
                raise TimeoutError("connection dropped " + SESSION_URI)
        fake.on_put = drop
        code, out, err = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_upload_network_or_timeout_uncertain", send_class="ambiguous")
        self.assert_no_secret(out, err)

    def test_partial_range_on_308_stops_instead_of_resending(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size, ranges={"put1": CHUNK // 2})
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_upload_range_mismatch_uncertain", send_class="ambiguous")
        self.assertEqual(fake.routes, ["channels", "init", "put1"])

    def test_file_changed_during_upload_never_sends_the_final_chunk(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size)

        def tamper(name):
            if name == "put1":
                data = bytearray(path.read_bytes())
                data[-10] ^= 0xFF
                path.write_bytes(bytes(data))
        fake.on_put = tamper
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_upload_file_changed_incomplete_uncertain",
                           send_class="ambiguous")
        self.assertEqual(fake.routes, ["channels", "init", "put1", "put2"])  # put3 (final) never sent

    def test_file_changed_after_approval_is_blocked_before_claim_and_restorable(self):
        pid, path = self.draft()
        original = path.read_bytes()
        path.write_bytes(original[:-1] + b"\x01")
        fake = FakeYouTube(size=len(original))
        code, out, _ = self.run_live(pid, fake)
        self.assertEqual(code, 2)
        self.assertIn("approval_video_changed_reapproval_required", out)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.post(pid)["status"], "approved")
        self.assertIn("approval_binding_failed", [a for a, _ in self.events(pid)])
        path.write_bytes(original)  # the approved bytes again: the send may proceed
        code, out, _ = self.run_live(pid, FakeYouTube(size=len(original)))
        self.assertEqual(code, 0, out)

    def test_adapter_rehash_refuses_before_any_request(self):
        pid, path = self.draft()
        spec = youtube.decode_video(self.post(pid)["video_json"])
        path.write_bytes(path.read_bytes() + b"tail")
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            with self.assertRaisesRegex(ValidationError, "youtube_video_file_changed"):
                youtube.upload_video(account(), DESCRIPTION, pid, spec)
        send.assert_not_called()
        self.assertFalse(write_started())

    def test_receipt_mismatches_are_uncertain(self):
        cases = (({"id": VIDEO_ID, "snippet": {"channelId": OTHER_CHANNEL},
                   "status": {"privacyStatus": "private", "uploadStatus": "uploaded"}}, "channel_mismatch"),
                 ({"id": VIDEO_ID, "snippet": {"channelId": CHANNEL},
                   "status": {"privacyStatus": "private", "uploadStatus": "rejected"}}, "upload_status"),
                 ({"snippet": {"channelId": CHANNEL}, "status": {}}, "missing_video_id"))
        for index, (body, needle) in enumerate(cases):
            with self.subTest(needle=needle):
                self.db = str(self.tmp / f"outbox-{index}.sqlite")  # fresh budget per case
                pid, path = self.draft()
                code, out, _ = self.run_live(pid, FakeYouTube(size=path.stat().st_size, final=body))
                self.assert_review(pid, code, out, needle=needle, send_class="ambiguous")

    def test_public_request_locked_private_is_a_mismatch_for_review(self):
        pid, path = self.draft(privacy="public")
        fake = FakeYouTube(size=path.stat().st_size)  # returns privacyStatus private
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_receipt_privacy_mismatch_uncertain", send_class="ambiguous")
        self.assertEqual(fake.init_body()["status"]["privacyStatus"], "public")

    def test_pause_during_upload_stops_and_never_resends(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size)

        def pause(name):
            if name == "put1":
                with connect(self.db) as con:
                    set_paused(con, True)
        fake.on_put = pause
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_upload_paused_incomplete_uncertain", send_class="ambiguous")
        self.assertEqual(fake.routes, ["channels", "init", "put1"])

    def test_rate_limited_init_records_a_cooldown(self):
        pid, path = self.draft()
        fake = FakeYouTube(size=path.stat().st_size, errors={"init": (429, "rateLimitExceeded")})
        code, out, _ = self.run_live(pid, fake)
        self.assert_review(pid, code, out, needle="youtube_init_http_429_rateLimitExceeded",
                           send_class="certain_not_sent")
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM event_log WHERE action='provider_cooldown'")
                             .fetchone()[0], 1)

    def test_dry_run_lists_the_video_and_sends_nothing(self):
        pid, _ = self.draft()
        with patch("reachweaver.publishers._send_http") as send:
            code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn("matches_recorded_sha256", out)
        send.assert_not_called()

    def test_pre_marker_failures_never_mark_write_started(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            with self.assertRaises(PublishError):
                youtube._session_uri("https://www.googleapis.com/upload/youtube/v3/videos")  # no query
        send.assert_not_called()
        self.assertFalse(write_started())


YT_FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "youtube"


def yt_fixture(name: str) -> dict:
    return json.loads((YT_FIXTURES / name).read_text(encoding="utf-8"))


def yt_fixture_error(url: str, name: str) -> HTTPError:
    """The fixture as an HTTPError; the message also carries the token and URL, which must never surface."""
    fx = yt_fixture(name)
    message = Message()
    for key, value in fx["headers"].items():
        message[key] = value
    body = json.dumps(fx["body"]).replace("Forbidden", "Forbidden Bearer " + FAKE_TOKEN) if fx["body"] else ""
    return HTTPError(url, fx["status"], "Bearer " + FAKE_TOKEN + " " + url, message, io.BytesIO(body.encode()))


class ChunkUploadFixtureTests(FlippedRegistry):
    """Issue #22: synthetic partial-failure and unaudited-private fixtures replayed through the chunk loop.

    Every failure after the first byte is ambiguous, ends in ``_uncertain`` and is never resumed."""

    def fail_on(self, fake, put_name, fixture):
        def hook(name):
            if name == put_name:
                raise yt_fixture_error(SESSION_URI, fixture)
        fake.on_put = hook

    def test_fixtures_are_valid_synthetic_and_carry_no_credential(self):
        names = sorted(p.name for p in YT_FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(names), 6)
        for name in names:
            with self.subTest(fixture=name):
                fx = yt_fixture(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                text = json.dumps(fx)
                self.assertNotIn("Bearer", text)
                self.assertNotIn("upload_id", text)

    def test_partial_failures_after_the_first_byte_stop_and_never_resume(self):
        cases = (("upload-chunk-403-forbidden.json", "put2", "youtube_upload_http_403_forbidden_uncertain",
                  ["channels", "init", "put1", "put2"]),
                 ("upload-chunk-404-session-expired.json", "put2", "youtube_upload_http_404_uncertain",
                  ["channels", "init", "put1", "put2"]),
                 ("upload-chunk-308-no-range.json", "put2", "youtube_upload_range_mismatch_uncertain",
                  ["channels", "init", "put1", "put2"]),
                 ("upload-final-chunk-500.json", "put3", "youtube_upload_http_500_uncertain",
                  ["channels", "init", "put1", "put2", "put3"]),
                 ("upload-final-chunk-308-resume-incomplete.json", "put3",
                  "youtube_upload_incomplete_after_final_chunk_uncertain",
                  ["channels", "init", "put1", "put2", "put3"]))
        for index, (fixture, put, needle, routes) in enumerate(cases):
            with self.subTest(fixture=fixture):
                self.db = str(self.tmp / f"outbox-chunk-{index}.sqlite")  # fresh budget per case
                reset_write_marker()
                pid, path = self.draft()
                fake = FakeYouTube(size=path.stat().st_size)
                self.fail_on(fake, put, fixture)
                code, out, err = self.run_live(pid, fake)
                self.assertEqual(code, 3, out + err)
                row = self.post(pid)
                self.assertEqual((row["status"], row["last_error"]), ("needs_review", needle))
                review = [d for a, d in self.events(pid) if a == "needs_review"][0]
                self.assertIn("class=ambiguous", review)
                self.assertEqual(fake.routes, routes)  # no status query, no retry, no resume
                self.assertTrue(write_started())
                self.assert_no_secret(out, err)
                # A second run sends nothing: the session is not resumable from ReachWeaver.
                again = FakeYouTube(size=path.stat().st_size)
                code, _, _ = self.run_live(pid, again)
                self.assertEqual((code, again.routes), (2, []))

    def test_unaudited_project_private_receipt_for_a_public_request_is_not_published(self):
        pid, path = self.draft(privacy="public")
        fake = FakeYouTube(size=path.stat().st_size, final=yt_fixture("receipt-unaudited-private.json")["body"])
        code, out, err = self.run_live(pid, fake)
        self.assertEqual(code, 3, out + err)
        row = self.post(pid)
        self.assertEqual((row["status"], row["last_error"], row["remote_id"]),
                         ("needs_review", "youtube_receipt_privacy_mismatch_uncertain", None))
        self.assertEqual(fake.init_body()["status"]["privacyStatus"], "public")  # requested exactly what was approved
        self.assertEqual(fake.routes, ["channels", "init", "put1", "put2", "put3"])

    def test_unaudited_project_private_receipt_for_a_private_request_is_published_private(self):
        pid, path = self.draft(privacy="private")
        fake = FakeYouTube(size=path.stat().st_size, final=yt_fixture("receipt-unaudited-private.json")["body"])
        code, out, err = self.run_live(pid, fake)
        self.assertEqual(code, 0, out + err)
        self.assertIn("privacy_status=private", [d for a, d in self.events(pid) if a == "published"][0])


if __name__ == "__main__":
    unittest.main()
