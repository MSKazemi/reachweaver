"""Issue #13 (owner-approved 2026-10-09; disabled until a live receipt): Mastodon image attachments.

Fully offline: the only network entry point (``publishers._send_http``) is replaced by an
in-memory fake Mastodon server, and ``publishers._sleep`` never sleeps. Live image publishing
is refused by the bundled capability registry; tests that exercise the send flow patch a
registry copy that lists ``live_media_formats: ["image"]`` for Mastodon.
"""
from __future__ import annotations

import contextlib
import email.message
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

from reachweaver import capabilities, publishers
from reachweaver.attachments import FIELDS, MEDIA_CHANGED, build_attachment, decode_media, encode_media, parse_focus
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection
from reachweaver.policy import ValidationError, mastodon_media_limits
from reachweaver.publishers import PublishError, multipart_body, publish_mastodon, write_started
from gate_support import issued_connection, publish  # noqa: E402
from reachweaver.schemas import canonical_json
from reachweaver.storage import (
    APPROVAL_BINDING_TAG, APPROVAL_BINDING_TAG_MEDIA, add_post, approval_payload, approval_payload_sha256,
    claim_sending, connect, get_post, post_media, published_text, set_paused, set_post_media, transition,
)

NOW = "2026-10-09T12:00:00+00:00"
PREFIX = "REACHWEAVER_MM_MASTO"
ENV = {PREFIX + "_TOKEN": "mock-media-token"}
LIVE = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
SERVER = "https://social.example.org"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int = 4, height: int = 3, note: bytes = b"") -> bytes:
    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    extra = _chunk(b"tEXt", b"Comment\x00" + note) if note else b""
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + extra + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def webp(width: int = 8, height: int = 6) -> bytes:
    vp8x = bytes([0, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(vp8x)) + vp8x
    return b"RIFF" + struct.pack("<I", len(body)) + body


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._raw = json.dumps(body).encode("utf-8")
        self.headers = email.message.Message()

    def getcode(self):
        return self.status

    def read(self, limit=-1):
        return self._raw if limit < 0 else self._raw[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeMastodon:
    """Queued responses per (method, path prefix); records every request it receives."""

    def __init__(self, routes: dict[tuple[str, str], list]):
        self.routes = {key: list(values) for key, values in routes.items()}
        self.requests: list[dict] = []

    def __call__(self, req):
        path = req.full_url[len(SERVER):]
        self.requests.append({"method": req.get_method(), "path": path, "headers": dict(req.header_items()),
                              "body": req.data or b""})
        for (method, prefix), queue in self.routes.items():
            if req.get_method() == method and path.startswith(prefix):
                if not queue:
                    raise AssertionError(f"unexpected extra request {method} {path}")
                item = queue.pop(0)
                if isinstance(item, dict):  # a provider fixture: status, headers and body
                    headers = email.message.Message()
                    for name, value in item.get("headers", {}).items():
                        headers[name] = value
                    if item["status"] >= 400:
                        raise HTTPError(req.full_url, item["status"], "error", headers,
                                        io.BytesIO(json.dumps(item["body"]).encode()))
                    response = FakeResponse(item["status"], item["body"])
                    response.headers = headers
                    return response
                if isinstance(item, int):  # an HTTP error status
                    headers = email.message.Message()
                    raise HTTPError(req.full_url, item, "error", headers, io.BytesIO(b'{"error":"x"}'))
                return FakeResponse(*item)
        raise AssertionError(f"no route for {req.get_method()} {path}")

    def paths(self):
        return [(r["method"], r["path"]) for r in self.requests]


FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "mastodon"


def load_fixture(name: str) -> dict:
    """A synthetic provider response (never a recording; see tests/fixtures/providers/README.md)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def jpeg_with_exif(tiff: bytes) -> bytes:
    exif = b"Exif\x00\x00" + tiff
    return (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
            + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 3, 4, 1) + b"\x01\x11\x00" + b"\xff\xd9")


def tiff_block(*, gps: bool, order: str = ">") -> bytes:
    """Minimal TIFF: IFD0 with one entry; GPSInfo (0x8825) -> a GPS IFD of one entry, or nothing."""
    e = order
    head = (b"MM" if e == ">" else b"II") + struct.pack(e + "H", 42) + struct.pack(e + "I", 8)
    if gps:
        ifd0 = struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x8825, 4, 1, 26) + struct.pack(e + "I", 0)
        gps_ifd = struct.pack(e + "H", 1) + struct.pack(e + "HHI4s", 1, 2, 2, b"N\x00\x00\x00") + struct.pack(e + "I", 0)
    else:
        ifd0 = struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x0112, 3, 1, 1) + struct.pack(e + "I", 0)
        gps_ifd = b""
    return head + ifd0 + gps_ifd


def media_entity(media_id: str, ready: bool) -> dict:
    return {"id": media_id, "type": "image", "url": f"https://files.example.org/{media_id}.png" if ready else None}


def status_entity(media_ids: list[str], status_id: str = "109") -> dict:
    return {"id": status_id, "url": f"{SERVER}/@alice/{status_id}", "visibility": "public",
            "account": {"username": "alice"}, "media_attachments": [{"id": m, "type": "image"} for m in media_ids]}


def media_registry() -> dict:
    registry = json.loads(json.dumps(capabilities.load_registry()))
    for entry in registry["platforms"]:
        if entry["id"] == "mastodon":
            entry["live_media_formats"] = ["image"]
    return registry


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "state.sqlite")
        self.config = self.root / "accounts.json"
        self.write_accounts()
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())

    def write_accounts(self, **options):
        accounts = [{"id": "masto", "platform": "mastodon", "env_prefix": PREFIX, "server": SERVER,
                     "handle": "alice", **options},
                    {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_MM_BSKY",
                     "handle": "alice.example.org"}]
        self.config.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def draft(self, text="New release notes with a chart"):
        code, out, err = self.cli("draft", "--account", "masto", "--text", text)
        self.assertEqual(code, 0, err)
        return out.split()[2]

    def attach(self, pid, path=None, alt="Bar chart of weekly downloads rising from 10 to 40", *extra):
        return self.cli("media-attach", pid, "--file", str(path or self.image), "--alt", alt, *extra)

    def approved_media_post(self, images=1):
        pid = self.draft()
        for i in range(images):
            path = self.root / f"img{i}.png"
            path.write_bytes(png(note=f"image {i}".encode()))
            code, _, err = self.attach(pid, path, f"Chart number {i + 1} of weekly downloads")
            self.assertEqual(code, 0, err)
        code, _, err = self.cli("approve", pid, "--approver", "mohsen")
        self.assertEqual(code, 0, err)
        return pid

    def live(self, pid, server: FakeMastodon, *, paused_after: int | None = None):
        verified = issued_connection("masto", "mastodon", "verified", "unverified")
        with patch.dict(os.environ, LIVE), patch("reachweaver.publishers._send_http", side_effect=server), \
                patch("reachweaver.cli.check_connection", return_value=verified), \
                patch("reachweaver.capabilities.load_registry", return_value=media_registry()), \
                patch("reachweaver.publishers._sleep") as sleeper:
            result = self.cli("publish", "--live", "--post-id", pid)
        return (*result, sleeper)

    def status(self, pid):
        with connect(self.db) as db:
            row = get_post(db, pid)
            return row["status"], row["last_error"]

    def events(self, action):
        with connect(self.db) as db:
            return [r["detail"] for r in db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id",
                                                    (action,))]


class AttachmentValidationTests(Base):
    def test_build_records_header_facts_and_hash(self):
        item = build_attachment(self.image, "  A bar chart  ", "0.5,-0.25")
        self.assertEqual(item["sha256"], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual((item["mime"], item["width"], item["height"], item["alt"], item["focus"]),
                         ("image/png", 4, 3, "A bar chart", "0.5,-0.25"))
        self.assertEqual(item["path"], str(self.image.resolve()))
        webp_path = self.root / "pic.webp"
        webp_path.write_bytes(webp())
        self.assertEqual(build_attachment(webp_path, "A photo of the team")["mime"], "image/webp")

    def test_alt_text_required_and_checked(self):
        limit = mastodon_media_limits(None)["media_description_limit"]
        self.assertEqual(limit, 1500)
        for bad in ("", "   ", None, "image", "Alt text", "TODO describe", "x" * (limit + 1), "bad\x07char",
                    "mixed‮direction"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                build_attachment(self.image, bad)
        self.assertEqual(build_attachment(self.image, "x" * limit)["alt"], "x" * limit)
        self.assertEqual(build_attachment(self.image, "line one\nline two")["alt"], "line one\nline two")

    def test_owner_pinned_limits_only_lower(self):
        self.write_accounts(media_description_limit=10, max_media_attachments=1, image_size_limit=100)
        from reachweaver.accounts import load_accounts
        account = load_accounts(self.config)["masto"]
        self.assertEqual(mastodon_media_limits(account),
                         {"max_media_attachments": 1, "image_size_limit": 100, "media_description_limit": 10})
        with self.assertRaisesRegex(ValidationError, "limit is 10"):
            build_attachment(self.image, "a description longer than ten", account=account)
        for name, bad in (("max_media_attachments", 5), ("image_size_limit", 16_000_001),
                          ("media_description_limit", 0), ("media_description_limit", True)):
            with self.subTest(name=name):
                self.write_accounts(**{name: bad})
                with self.assertRaises(ValidationError):
                    load_accounts(self.config)

    def test_unsupported_files_refused(self):
        gif = self.root / "anim.gif"
        gif.write_bytes(b"GIF89a\x04\x00\x03\x00" + b"\x00" * 20)
        text = self.root / "notes.png"
        text.write_bytes(b"not an image at all")
        empty = self.root / "empty.png"
        empty.write_bytes(b"")
        big = self.root / "big.png"
        big.write_bytes(png() + b"\x00" * 16_000_001)
        link = self.root / "link.png"
        link.symlink_to(self.image)
        for path, message in ((gif, "GIF"), (text, "PNG, JPEG or WebP"), (empty, "empty"), (big, "limit is"),
                              (link, "symbolic link"), (self.root / "missing.png", "missing"),
                              (self.root, "regular file")):
            with self.subTest(path=path.name), self.assertRaisesRegex(ValidationError, message):
                build_attachment(path, "A described image")

    def test_focus_parsing(self):
        self.assertIsNone(parse_focus(None))
        self.assertEqual(parse_focus("0,0"), "0,0")
        self.assertEqual(parse_focus("-1.0, 1"), "-1,1")
        self.assertEqual(parse_focus("-0.000,0.123456"), "0,0.123456")
        for bad in ("1.1,0", "0", "a,b", "0,0,0", "1e-3,0", "nan,0", "0.1234567,0"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_focus(bad)

    def test_media_json_round_trip_and_strict_decode(self):
        item = build_attachment(self.image, "A described image")
        self.assertEqual(decode_media(encode_media([item])), [item])
        self.assertIsNone(encode_media([]))
        self.assertEqual(decode_media(None), [])
        for bad in ("{", "[]", "{}", json.dumps([{**item, "extra": 1}]), json.dumps([{**item, "sha256": "x"}]),
                    json.dumps([{**item, "path": "relative.png"}]), json.dumps([{**item, "mime": "image/gif"}]),
                    json.dumps([{**item, "alt": " "}]), json.dumps([{**item, "focus": "2,0"}])):
            with self.subTest(bad=bad[:40]), self.assertRaises(ValidationError):
                decode_media(bad)

    def test_multipart_body_is_well_formed(self):
        data = png()
        body, content_type = multipart_body([("description", "Grafik — ünïcode"), ("focus", "0,0")],
                                            "file", "attachment-1.png", "image/png", data)
        boundary = content_type.split("boundary=")[1].encode()
        self.assertNotIn(boundary, data)
        self.assertTrue(body.startswith(b"--" + boundary + b"\r\n"))
        self.assertTrue(body.endswith(b"--" + boundary + b"--\r\n"))
        self.assertIn('Content-Disposition: form-data; name="description"\r\n\r\nGrafik — ünïcode\r\n'.encode(), body)
        self.assertIn(b'name="file"; filename="attachment-1.png"\r\nContent-Type: image/png\r\n\r\n' + data, body)
        with self.assertRaisesRegex(PublishError, "invalid_request"):
            multipart_body([('bad"name', "x")], "file", "a.png", "image/png", data)


class ApprovalBindingTests(Base):
    def setUp(self):
        super().setUp()
        self.item = build_attachment(self.image, "A described image", "0.1,0.2")

    def post_with(self, db, media):
        pid = add_post(db, "masto", "mastodon", "Release notes with a chart")
        set_post_media(db, pid, media)
        return get_post(db, pid)

    def test_text_only_payload_is_unchanged_v1(self):
        with connect(self.db) as db:
            pid = add_post(db, "masto", "mastodon", "Text only")
            row = get_post(db, pid)
            payload = approval_payload(row)
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG)
            self.assertEqual(set(payload), {"binding", "account_id", "platform", "content", "scheduled_at",
                                            "source_url", "campaign"})
            v1 = {"binding": "reachweaver.storage.approval/1", "account_id": "masto", "platform": "mastodon",
                  "content": "Text only", "scheduled_at": None, "source_url": "", "campaign": ""}
            self.assertEqual(approval_payload_sha256(row), hashlib.sha256(canonical_json(v1)).hexdigest())
            set_post_media(db, pid, [])  # detaching everything returns to the v1 payload
            self.assertEqual(approval_payload_sha256(get_post(db, pid)), hashlib.sha256(canonical_json(v1)).hexdigest())

    def test_media1_payload_covers_sha_mime_alt_focus_in_order(self):
        with connect(self.db) as db:
            row = self.post_with(db, [self.item])
            payload = approval_payload(row)
            self.assertEqual(payload["binding"], APPROVAL_BINDING_TAG_MEDIA)
            self.assertEqual(payload["media"], [{"sha256": self.item["sha256"], "mime": "image/png",
                                                 "alt": "A described image", "focus": "0.1,0.2"}])
            base = approval_payload_sha256(row)
            other = self.root / "other.png"
            other.write_bytes(png(note=b"different bytes"))
            second = build_attachment(other, "Second image")
            variants = {
                "bytes": [{**self.item, "sha256": hashlib.sha256(b"x").hexdigest()}],
                "alt": [{**self.item, "alt": "A different description"}],
                "focus": [{**self.item, "focus": None}],
                "mime": [{**self.item, "mime": "image/webp"}],
                "added": [self.item, second],
            }
            hashes = {base}
            for name, media in variants.items():
                with self.subTest(name=name):
                    digest = approval_payload_sha256(self.post_with(db, media))
                    self.assertNotIn(digest, hashes)
                    hashes.add(digest)
            reordered = approval_payload_sha256(self.post_with(db, [second, self.item]))
            self.assertNotIn(reordered, hashes)
            self.assertEqual(approval_payload_sha256(self.post_with(db, [{**self.item, "path": str(other)}])), base,
                             "the local path is not sent, so it is not bound; the bytes are")

    def test_media_tamper_after_approval_blocks_claim(self):
        with connect(self.db) as db:
            pid = self.post_with(db, [self.item])["id"]
            transition(db, pid, "approve")
            db.execute("UPDATE posts SET media_json=? WHERE id=?",
                       (encode_media([{**self.item, "alt": "Swapped description"}]), pid))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch_reapproval_required"):
                claim_sending(db, pid, now=NOW)
            db.execute("UPDATE posts SET media_json=? WHERE id=?", ("not json", pid))
            with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch"):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_approve_refuses_changed_file_and_attach_only_on_drafts(self):
        with connect(self.db) as db:
            pid = self.post_with(db, [self.item])["id"]
            self.image.write_bytes(png(note=b"edited after attach"))
            with self.assertRaisesRegex(ValidationError, MEDIA_CHANGED):
                transition(db, pid, "approve")
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.image.write_bytes(png())  # restored: the recorded bytes again
            transition(db, pid, "approve")
            with self.assertRaisesRegex(ValidationError, "only change on a draft"):
                set_post_media(db, pid, [])

    def test_storage_refuses_media_on_other_platforms_and_over_count(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bluesky", "bluesky", "A Bluesky post")
            with self.assertRaisesRegex(ValidationError, "only supported for Mastodon"):
                set_post_media(db, pid, [self.item])
            pid = add_post(db, "masto", "mastodon", "Too many")
            with self.assertRaisesRegex(ValidationError, "limit is 4"):
                set_post_media(db, pid, [self.item] * 5)
            self.assertEqual(post_media(get_post(db, pid)), [])

    def test_published_text_includes_alt_for_optout(self):
        with connect(self.db) as db:
            row = self.post_with(db, [{**self.item, "alt": "Photo with @blocked@social.example.org"}])
            self.assertIn("@blocked@social.example.org", published_text(row))


class GpsWarningTests(Base):
    """Issue #13 D7: a warning (never a block) when the attached image carries EXIF GPS data."""

    def attach_bytes(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return self.cli("media-attach", self.draft(), "--file", str(path), "--alt", "Photo of the conference stage")

    def test_jpeg_with_gps_warns_but_attaches(self):
        for order in (">", "<"):
            with self.subTest(order=order):
                code, out, err = self.attach_bytes("gps.jpg", jpeg_with_exif(tiff_block(gps=True, order=order)))
                self.assertEqual(code, 0, err)
                self.assertIn("Attached image 1", out)
                self.assertIn("WARNING: embedded EXIF metadata contains GPS location data", out)
                self.assertIn("does not strip it", out)

    def test_png_and_webp_gps_detected(self):
        from reachweaver.media import exif_has_gps
        base = png()
        end = base.index(_chunk(b"IDAT", b"")[4:8]) - 4
        with_exif = base[:end] + _chunk(b"eXIf", tiff_block(gps=True)) + base[end:]
        self.assertTrue(exif_has_gps(with_exif))
        exif_chunk = b"EXIF" + struct.pack("<I", len(tiff_block(gps=True))) + tiff_block(gps=True)
        body = b"WEBP" + exif_chunk
        self.assertTrue(exif_has_gps(b"RIFF" + struct.pack("<I", len(body)) + body))
        code, out, err = self.attach_bytes("gps.png", with_exif)
        self.assertEqual(code, 0, err)
        self.assertIn("WARNING:", out)

    def test_exif_without_gps_or_no_exif_does_not_warn(self):
        for name, data in (("plain.jpg", jpeg_with_exif(tiff_block(gps=False))), ("plain.png", png())):
            with self.subTest(name=name):
                code, out, err = self.attach_bytes(name, data)
                self.assertEqual(code, 0, err)
                self.assertNotIn("WARNING", out)

    def test_malformed_exif_never_raises(self):
        from reachweaver.media import exif_has_gps
        for tiff in (b"", b"MM", b"MM\x00*\xff\xff\xff\xff", b"II*\x00\x08\x00\x00\x00\xff\xff"):
            with self.subTest(tiff=tiff):
                self.assertFalse(exif_has_gps(jpeg_with_exif(tiff)))
        self.assertFalse(exif_has_gps(b"\xff\xd8\xff\xe1\xff"))

    def test_warning_does_not_change_record_or_approval_binding(self):
        data = jpeg_with_exif(tiff_block(gps=True))
        path = self.root / "gps.jpg"
        path.write_bytes(data)
        item = build_attachment(path, "Photo of the conference stage")
        self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(set(item), set(FIELDS))
        self.assertEqual(path.read_bytes(), data, "the file is never rewritten")


class MediaCliTests(Base):
    def test_attach_show_check_detach(self):
        pid = self.draft()
        code, out, err = self.attach(pid, None, "Bar chart of downloads", "--focus", "0,0.5")
        self.assertEqual(code, 0, err)
        self.assertIn("Attached image 1", out)
        self.assertIn("Not uploaded", out)
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("show", pid)
        network.assert_not_called()
        shown = json.loads(out)
        media = shown["media"]
        self.assertEqual(media["count"], 1)
        self.assertTrue(media["all_files_match_record"])
        self.assertEqual(media["attachments"][0]["alt"], "Bar chart of downloads")
        self.assertEqual(media["attachments"][0]["focus"], "0,0.5")
        self.assertIn("refused", media["live_image_publish"])
        self.assertEqual(self.cli("check", pid)[0], 0)
        self.image.write_bytes(png(note=b"changed"))
        code, out, _ = self.cli("check", pid)
        self.assertEqual(code, 1)
        self.assertIn(MEDIA_CHANGED, out)
        self.assertFalse(json.loads(self.cli("show", pid)[1])["media"]["all_files_match_record"])
        code, out, _ = self.cli("media-detach", pid)
        self.assertEqual(code, 0)
        self.assertIn("Removed 1", out)
        self.assertNotIn("media", json.loads(self.cli("show", pid)[1]))
        self.assertTrue(self.image.exists(), "detach never touches local files")

    def test_attach_requires_alt_and_refuses_bad_targets(self):
        pid = self.draft()
        err = io.StringIO()
        with self.assertRaises(SystemExit) as exit_, contextlib.redirect_stderr(err):
            main(["--db", self.db, "--accounts", str(self.config), "media-attach", pid, "--file", str(self.image)])
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("--alt", err.getvalue())
        self.assertEqual(self.attach(pid, None, "   ")[0], 2)
        code, out, err = self.cli("draft", "--account", "my-bluesky", "--text", "A Bluesky post")
        bsky = out.split()[2]
        code, _, err = self.attach(bsky)
        self.assertEqual(code, 2)
        self.assertIn("only supported for Mastodon", err)
        self.assertEqual(self.cli("approve", pid)[0], 0)
        code, _, err = self.attach(pid)
        self.assertEqual(code, 2)
        self.assertIn("only change on a draft", err)
        alt_file = self.root / "alt.txt"
        alt_file.write_text("Described in a file", encoding="utf-8")
        other = self.draft("Another post")
        code, _, err = self.cli("media-attach", other, "--file", str(self.image), "--alt-file", str(alt_file))
        self.assertEqual(code, 0, err)

    def test_optout_handle_in_alt_text_refused(self):
        optout = self.root / "optout.json"
        optout.write_text(json.dumps({"version": 1, "entries": [{"platform": "mastodon",
                                                                 "handle": "@blocked@social.example.org"}]}),
                          encoding="utf-8")
        pid = self.draft()
        code, _, err = self.attach(pid, None, "Photo of @blocked@social.example.org", "--optout", str(optout))
        self.assertEqual(code, 2, err)
        self.assertIn("refused", err)
        with connect(self.db) as db:
            self.assertEqual(post_media(get_post(db, pid)), [])

    def test_approve_binds_media_and_prints_it(self):
        pid = self.draft()
        self.attach(pid)
        code, out, err = self.cli("approve", pid)
        self.assertEqual(code, 0, err)
        self.assertIn("media=1", out)
        with connect(self.db) as db:
            row = get_post(db, pid)
            self.assertEqual(row["approved_payload_sha256"], approval_payload_sha256(row))
            self.assertEqual(approval_payload(row)["binding"], APPROVAL_BINDING_TAG_MEDIA)
        self.assertEqual(len(self.events("media_set")), 1)
        self.assertNotIn("Bar chart", self.events("media_set")[0], "audit events hold hashes, not alt text")

    def test_dry_run_lists_media_and_capability_refusal(self):
        pid = self.approved_media_post()
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--post-id", pid)
        network.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("media: 1 image(s) image/png", out)
        self.assertIn("No ReachWeaver live adapter for mastodon image", out)


class LiveGateTests(Base):
    def test_bundled_registry_refuses_live_image_publish(self):
        with self.assertRaisesRegex(ValidationError, "No ReachWeaver live adapter for mastodon image"):
            capabilities.require_capability("mastodon", "image", "publish")
        capabilities.require_capability("mastodon", "image", "api")  # research fact only
        capabilities.require_capability("mastodon", "image", "publish", registry=media_registry())
        self.assertEqual(capabilities.validate_registry(media_registry()), [])

    def test_registry_rejects_bad_live_media_formats(self):
        for platform, value in (("mastodon", ["video"]), ("mastodon", "image"), ("mastodon", ["image", "image"]),
                                ("bluesky", ["image"]), ("x", ["image"])):
            registry = json.loads(json.dumps(capabilities.load_registry()))
            for entry in registry["platforms"]:
                if entry["id"] == platform:
                    entry["live_media_formats"] = value
            with self.subTest(platform=platform, value=value):
                self.assertTrue(any("live_media_formats" in e for e in capabilities.validate_registry(registry)))

    def test_live_media_post_blocked_by_registry_before_any_claim(self):
        pid = self.approved_media_post()
        verified = issued_connection("masto", "mastodon", "verified", "unverified")
        with patch.dict(os.environ, LIVE), patch("reachweaver.publishers._send_http") as network, \
                patch("reachweaver.cli.check_connection", return_value=verified) as identity:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("No ReachWeaver live adapter for mastodon image", out)
        network.assert_not_called()
        identity.assert_not_called()
        self.assertEqual(self.status(pid)[0], "approved")
        self.assertEqual(self.events("send_claimed"), [])

    def test_library_publish_refuses_media_without_registry_or_on_other_targets(self):
        item = build_attachment(self.image, "A described image")
        account = {"id": "masto", "platform": "mastodon", "env_prefix": PREFIX, "server": SERVER, "handle": "alice"}
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as network:
            with self.assertRaisesRegex(ValidationError, "mastodon image"):
                publish(account, "Hello", "p1", media=[item])
            publishers.reset_write_marker()
            with self.assertRaisesRegex(ValidationError, "mastodon image"):  # adapter called directly
                publish_mastodon(account, "Hello", "p1", media=[item])
            self.assertFalse(write_started())
            with self.assertRaisesRegex(ValidationError, "media_attachments_unsupported"):
                publish({**account, "platform": "bluesky"}, "Hello", "p1", media=[item])
            with self.assertRaisesRegex(ValidationError, "media_attachments_unsupported"):
                publish({**account, "backend": "postiz"}, "Hello", "p1", media=[item])
        network.assert_not_called()

    def test_changed_file_blocked_before_claim_and_restorable(self):
        pid = self.approved_media_post()
        path = self.root / "img0.png"
        original = path.read_bytes()
        path.write_bytes(png(note=b"swapped after approval"))
        server = FakeMastodon({})
        code, out, _, _ = self.live(pid, server)
        self.assertEqual(code, 2)
        self.assertIn(MEDIA_CHANGED, out)
        self.assertEqual(server.requests, [])
        self.assertEqual(self.status(pid), ("approved", MEDIA_CHANGED))
        self.assertEqual(len(self.events("approval_binding_failed")), 1)
        self.assertEqual(self.events("send_claimed"), [])
        path.write_bytes(original)  # the approved bytes again: sending them is what was approved
        server = FakeMastodon({("POST", "/api/v2/media"): [(200, media_entity("m1", True))],
                               ("POST", "/api/v1/statuses"): [(200, status_entity(["m1"]))]})
        code, out, _, _ = self.live(pid, server)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.status(pid)[0], "published")

    def test_adapter_rechecks_bytes_before_write_marker(self):
        item = build_attachment(self.image, "A described image")
        self.image.write_bytes(png(note=b"changed between check and send"))
        account = {"id": "masto", "platform": "mastodon", "env_prefix": PREFIX, "server": SERVER, "handle": "alice"}
        publishers.reset_write_marker()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as network, \
                patch("reachweaver.capabilities.load_registry", return_value=media_registry()):
            with self.assertRaisesRegex(ValidationError, MEDIA_CHANGED):
                publish_mastodon(account, "Hello", "p1", media=[item])
        network.assert_not_called()
        self.assertFalse(write_started(), "a changed file is certain not sent (C1)")


class SendFlowTests(Base):
    def test_upload_poll_then_status_happy_path(self):
        pid = self.approved_media_post(images=2)
        server = FakeMastodon({
            ("POST", "/api/v2/media"): [(202, media_entity("m1", False)), (200, media_entity("m2", True))],
            ("GET", "/api/v1/media/m1"): [(206, media_entity("m1", False)), (200, media_entity("m1", True))],
            ("POST", "/api/v1/statuses"): [(200, status_entity(["m1", "m2"]))],
        })
        code, out, err, sleeper = self.live(pid, server)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(server.paths(), [("POST", "/api/v2/media"), ("GET", "/api/v1/media/m1"),
                                          ("GET", "/api/v1/media/m1"), ("POST", "/api/v2/media"),
                                          ("POST", "/api/v1/statuses")])
        self.assertEqual([c.args[0] for c in sleeper.call_args_list], [1, 2])
        upload = server.requests[0]
        self.assertTrue(upload["headers"]["Content-type"].startswith("multipart/form-data; boundary="))
        self.assertNotIn("Idempotency-key", upload["headers"])
        self.assertIn(b"Chart number 1 of weekly downloads", upload["body"])
        self.assertIn((self.root / "img0.png").read_bytes(), upload["body"])
        self.assertNotIn(str(self.root).encode(), upload["body"], "the local path never leaves the machine")
        self.assertIn(b'filename="attachment-1.png"', upload["body"])
        status = server.requests[-1]
        self.assertEqual(status["headers"]["Idempotency-key"], pid)
        form = parse_qs(status["body"].decode())
        self.assertEqual(form["media_ids[]"], ["m1", "m2"])
        self.assertEqual(form["status"], ["New release notes with a chart"])
        self.assertEqual(self.status(pid), ("published", None))
        self.assertIn("media=2", self.events("published")[0])

    def test_processing_poll_is_bounded(self):
        pid = self.approved_media_post()
        polls = publishers.MEDIA_POLL_ATTEMPTS
        server = FakeMastodon({("POST", "/api/v2/media"): [(202, media_entity("m1", False))],
                               ("GET", "/api/v1/media/m1"): [(206, media_entity("m1", False))] * polls})
        code, out, _, sleeper = self.live(pid, server)
        self.assertEqual(code, 3)
        self.assertEqual(len(server.requests), 1 + polls)
        self.assertEqual(sum(c.args[0] for c in sleeper.call_args_list), sum(publishers.MEDIA_POLL_DELAYS))
        self.assertNotIn(("POST", "/api/v1/statuses"), server.paths())
        self.assertEqual(self.status(pid), ("needs_review", "mastodon_media_processing_timeout_uncertain"))
        self.assertIn("class=ambiguous", self.events("needs_review")[0])

    def test_upload_errors_go_to_needs_review_and_are_never_resent(self):
        for http_status in (422, 413, 500):
            with self.subTest(status=http_status):
                self.db = str(self.root / f"state-{len(list(self.root.iterdir()))}.sqlite")  # fresh budget
                pid = self.approved_media_post()
                server = FakeMastodon({("POST", "/api/v2/media"): [http_status]})
                code, out, _, _ = self.live(pid, server)
                self.assertEqual(code, 3)
                self.assertEqual(self.status(pid), ("needs_review", f"mastodon_media_upload_http_{http_status}"))
                self.assertEqual(server.paths(), [("POST", "/api/v2/media")])
                again = FakeMastodon({})
                code, out, _, _ = self.live(pid, again)
                self.assertEqual(code, 2)
                self.assertEqual(again.requests, [], "a needs_review post is never resent")

    def test_rate_limited_upload_records_cooldown(self):
        pid = self.approved_media_post()
        server = FakeMastodon({("POST", "/api/v2/media"): [429]})
        code, out, _, _ = self.live(pid, server)
        self.assertEqual(code, 3)
        self.assertEqual(self.status(pid)[1], "mastodon_media_upload_http_429")
        self.assertEqual(len(self.events("provider_cooldown")), 1)

    def test_fixture_unprocessed_202_then_206_then_ready(self):
        pid = self.approved_media_post()
        up, partial, ready = (load_fixture(n) for n in ("media-upload-202-unprocessed.json",
                                                        "media-poll-206-partial.json", "media-poll-200-ready.json"))
        self.assertIsNone(up["body"]["url"])
        self.assertEqual((up["status"], partial["status"], ready["status"]), (202, 206, 200))
        mid = up["body"]["id"]
        server = FakeMastodon({("POST", "/api/v2/media"): [up],
                               ("GET", "/api/v1/media/" + mid): [partial, ready],
                               ("POST", "/api/v1/statuses"): [(200, status_entity([mid]))]})
        code, out, err, sleeper = self.live(pid, server)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(server.paths(), [("POST", "/api/v2/media"), ("GET", "/api/v1/media/" + mid),
                                          ("GET", "/api/v1/media/" + mid), ("POST", "/api/v1/statuses")])
        self.assertEqual(len(sleeper.call_args_list), 2)
        self.assertEqual(self.status(pid), ("published", None))

    def test_fixture_never_ready_stays_unpublished(self):
        pid = self.approved_media_post()
        up, partial = load_fixture("media-upload-202-unprocessed.json"), load_fixture("media-poll-206-partial.json")
        mid = up["body"]["id"]
        server = FakeMastodon({("POST", "/api/v2/media"): [up],
                               ("GET", "/api/v1/media/" + mid): [partial] * publishers.MEDIA_POLL_ATTEMPTS})
        code, _, _, _ = self.live(pid, server)
        self.assertEqual(code, 3)
        self.assertNotIn(("POST", "/api/v1/statuses"), server.paths())
        self.assertEqual(self.status(pid), ("needs_review", "mastodon_media_processing_timeout_uncertain"))

    def test_fixture_429_records_cooldown_and_is_never_resent(self):
        for name in ("media-upload-429-rate-limit.json", "media-upload-429-reset-only.json"):
            with self.subTest(fixture=name):
                self.db = str(self.root / f"state-{len(list(self.root.iterdir()))}.sqlite")  # fresh budget
                pid = self.approved_media_post()
                server = FakeMastodon({("POST", "/api/v2/media"): [load_fixture(name)]})
                code, out, _, _ = self.live(pid, server)
                self.assertEqual(code, 3)
                self.assertEqual(server.paths(), [("POST", "/api/v2/media")])
                self.assertEqual(self.status(pid), ("needs_review", "mastodon_media_upload_http_429"))
                [cooldown] = self.events("provider_cooldown")
                self.assertIn("account=masto", cooldown)
                self.assertNotIn("Too many requests", cooldown + out)
                again = FakeMastodon({})
                self.assertEqual(self.live(pid, again)[0], 2)
                self.assertEqual(again.requests, [])

    def test_failure_after_first_upload_needs_review_without_status(self):
        pid = self.approved_media_post(images=2)
        server = FakeMastodon({("POST", "/api/v2/media"): [(200, media_entity("m1", True)), 422]})
        code, _, _, _ = self.live(pid, server)
        self.assertEqual(code, 3)
        self.assertEqual(server.paths(), [("POST", "/api/v2/media"), ("POST", "/api/v2/media")])
        self.assertEqual(self.status(pid), ("needs_review", "mastodon_media_upload_http_422"))

    def test_poll_error_and_bad_entities_are_uncertain(self):
        cases = {
            "mastodon_media_poll_http_404": {("POST", "/api/v2/media"): [(202, media_entity("m1", False))],
                                              ("GET", "/api/v1/media/m1"): [404]},
            "mastodon_media_upload_mastodon_media_missing_id_uncertain": {
                ("POST", "/api/v2/media"): [(200, {"type": "image", "url": None})]},
            "mastodon_media_upload_mastodon_media_type_mismatch_uncertain": {
                ("POST", "/api/v2/media"): [(200, {"id": "m1", "type": "gifv", "url": "https://f.example.org/x"})]},
            "mastodon_media_poll_mastodon_media_id_mismatch_uncertain": {
                ("POST", "/api/v2/media"): [(202, media_entity("m1", False))],
                ("GET", "/api/v1/media/m1"): [(200, media_entity("m9", True))]},
            "mastodon_receipt_media_mismatch_uncertain": {
                ("POST", "/api/v2/media"): [(200, media_entity("m1", True))],
                ("POST", "/api/v1/statuses"): [(200, status_entity([]))]},
        }
        for code_name, routes in cases.items():
            with self.subTest(code=code_name):
                self.db = str(self.root / f"state-{len(list(self.root.iterdir()))}.sqlite")  # fresh budget
                pid = self.approved_media_post()
                code, _, _, _ = self.live(pid, FakeMastodon(routes))
                self.assertEqual(code, 3)
                self.assertEqual(self.status(pid), ("needs_review", code_name))

    def test_pause_during_processing_stops_waiting(self):
        pid = self.approved_media_post()
        fake = FakeMastodon({("POST", "/api/v2/media"): [(202, media_entity("m1", False))]})

        def server(req):  # the operator runs 'pause' while the upload is being processed
            response = fake(req)
            with connect(self.db) as other:
                set_paused(other, True)
            return response

        server.paths = fake.paths  # type: ignore[attr-defined]
        code, out, _, sleeper = self.live(pid, server)  # type: ignore[arg-type]
        self.assertEqual(code, 3, out)
        sleeper.assert_not_called()
        self.assertEqual(server.paths(), [("POST", "/api/v2/media")])
        self.assertEqual(self.status(pid), ("needs_review", "mastodon_media_processing_paused_uncertain"))

    def test_write_marker_set_before_first_upload(self):
        item = build_attachment(self.image, "A described image")
        account = {"id": "masto", "platform": "mastodon", "env_prefix": PREFIX, "server": SERVER, "handle": "alice"}
        seen = []

        def network(req):
            seen.append(write_started())
            raise HTTPError(req.full_url, 422, "x", email.message.Message(), io.BytesIO(b"{}"))

        publishers.reset_write_marker()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=network), \
                patch("reachweaver.capabilities.load_registry", return_value=media_registry()):
            with self.assertRaisesRegex(PublishError, "mastodon_media_upload_http_422"):
                publish_mastodon(account, "Hello", "p1", media=[item])
        self.assertEqual(seen, [True])

    def test_text_only_mastodon_send_unchanged(self):
        pid = self.draft("Plain text post")
        self.assertEqual(self.cli("approve", pid)[0], 0)
        server = FakeMastodon({("POST", "/api/v1/statuses"): [(200, {
            "id": "5", "url": f"{SERVER}/@alice/5", "visibility": "public", "account": {"username": "alice"}})]})
        code, out, _, _ = self.live(pid, server)
        self.assertEqual(code, 0, out)
        form = parse_qs(server.requests[0]["body"].decode())
        self.assertEqual(form, {"status": ["Plain text post"], "visibility": ["public"]})


if __name__ == "__main__":
    unittest.main()
