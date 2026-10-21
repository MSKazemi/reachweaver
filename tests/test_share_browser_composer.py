"""Browser Share Composer workflow: reopen, duplicate, update, autosave, media and "Mark as posted".

Owner-approved slice 2026-10-21. Every route is exercised through a test server bound to 127.0.0.1 with
synthetic captions, synthetic image bytes and a temporary history database. No provider, credential, cookie,
account or network call is used, and nothing may approve, schedule or send: the only database write is
storage.record_manual_publish, which makes a terminal published_manual record.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import tempfile
import threading
import unittest
import zlib
from unittest.mock import patch

from reachweaver import home, share_composer_store as store
from reachweaver.share_browser import BIND_ADDRESS, JSON_ROUTES, MAX_REQUEST_BYTES, ShareServer
from reachweaver.storage import connect, pending

CAPTION = "We released a small offline helper for preparing posts by hand."
URL = "https://www.threads.net/@owner.example/post/C0mp0serTest1"
# Synthetic public account entries (no credentials; env_prefix names only).
ACCOUNTS = {"accounts": [
    {"id": "owner-threads", "platform": "threads", "env_prefix": "REACHWEAVER_THREADS_ONE",
     "username": "owner.example"},
    {"id": "owner-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_BSKY_ONE",
     "handle": "owner.example.bsky.social"},
    {"id": "owner-devto", "platform": "devto", "env_prefix": "REACHWEAVER_DEVTO_ONE", "username": "owner"},
]}


def png_bytes(width: int = 1080, height: int = 1350) -> bytes:
    """A real, tiny PNG (one grey row repeated) so the offline header probe can read its size."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


@contextmanager
def running_server(db: str | None):
    srv = ShareServer(0, db=db)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


def call(srv, path, body=None, *, headers=None, raw: bytes | None = None, ctype="application/json",
         good=True, method="POST"):
    conn = http.client.HTTPConnection(BIND_ADDRESS, srv.server_port, timeout=10)
    sent = {"Host": f"{BIND_ADDRESS}:{srv.server_port}"}
    if good:
        sent.update({"X-ReachWeaver-Token": srv.token, "Origin": srv.origin})
    if ctype:
        sent["Content-Type"] = ctype
    sent.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    try:
        conn.request(method, path, body=data, headers=sent)
        res = conn.getresponse()
        content = res.read()
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        return res.status, parsed
    finally:
        conn.close()


class ComposerBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        old = home.root()
        home.set_root(self.root)
        self.addCleanup(lambda: home.set_root(old))
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.db = str(self.root / "history" / "reachweaver.sqlite")
        self.srv_cm = running_server(self.db)
        self.srv = self.srv_cm.__enter__()
        self.addCleanup(self.srv_cm.__exit__, None, None, None)

    def post(self, path, body=None, **kw):
        return call(self.srv, path, {} if body is None else body, **kw)

    def upload(self, data: bytes, name: str = "photo.png", **kw):
        headers = {"X-ReachWeaver-Filename": name}
        headers.update(kw.pop("headers", {}))
        return call(self.srv, "/api/media", raw=data, ctype=kw.pop("ctype", "application/octet-stream"),
                    headers=headers, **kw)

    def save_new(self, body: dict) -> dict:
        code, data = self.post("/api/prepare", dict(body, mode="new"))
        self.assertEqual(code, 200, data)
        return data

    @property
    def shares(self) -> Path:
        return self.root / "state" / "shares"

    def packages(self) -> list[str]:
        return sorted(p.name for p in self.shares.iterdir() if store.PACKAGE_NAME.fullmatch(p.name)) \
            if self.shares.exists() else []


class ReopenDuplicateUpdateTests(ComposerBase):
    BODY = {"platforms": ["threads", "youtube", "reddit"], "text": CAPTION, "title": "Offline helper",
            "note": "Read the subreddit rules first", "overrides": {"threads": "A short Threads version"}}

    def test_saved_package_is_listed_and_reopens_with_every_field(self):
        saved = self.save_new(self.BODY)
        name = saved["package"]["name"]
        self.assertFalse(saved["published"])
        code, data = self.post("/api/packages")
        self.assertEqual(code, 200, data)
        [listed] = data["packages"]
        self.assertEqual(listed["name"], name)
        self.assertEqual({n["id"]: n["state"] for n in listed["networks"]},
                         {"threads": "exported", "youtube": "exported", "reddit": "exported"})
        reddit = next(n for n in listed["networks"] if n["id"] == "reddit")
        self.assertTrue(reddit["human_only"])
        self.assertFalse(reddit["measurable"])
        code, opened = self.post("/api/package/open", {"package": name})
        self.assertEqual(code, 200, opened)
        editor = opened["editor"]
        self.assertEqual(editor["platforms"], ["threads", "youtube", "reddit"])
        self.assertEqual(editor["text"], CAPTION)
        self.assertEqual(editor["title"], "Offline helper")
        self.assertEqual(editor["note"], "Read the subreddit rules first")
        self.assertEqual(editor["overrides"], {"threads": "A short Threads version"})
        self.assertEqual(editor["package"], name)
        self.assertEqual(editor["revision"], listed["revision"])
        # Opening makes the package the draft, so a reload keeps it open.
        code, draft = self.post("/api/draft/load")
        self.assertEqual(draft["draft"]["package"], name)

    def test_package_without_editor_file_is_rebuilt_from_its_network_files(self):
        name = self.save_new(self.BODY)["package"]["name"]
        (self.shares / name / store.EDITOR_FILE).unlink()
        code, opened = self.post("/api/package/open", {"package": name})
        self.assertEqual(code, 200, opened)
        self.assertEqual(opened["editor"]["note"], "Read the subreddit rules first")
        self.assertEqual(opened["editor"]["title"], "Offline helper")
        self.assertEqual({opened["editor"]["text"], *opened["editor"]["overrides"].values()},
                         {CAPTION, "A short Threads version"})

    def test_duplicate_makes_a_new_package_and_never_touches_the_original(self):
        name = self.save_new(self.BODY)["package"]["name"]
        before = (self.shares / name / "threads" / "caption.txt").read_bytes()
        code, dup = self.post("/api/package/duplicate", {"package": name})
        self.assertEqual(code, 200, dup)
        self.assertNotEqual(dup["package"]["name"], name)
        self.assertEqual(len(self.packages()), 2)
        copy = self.shares / dup["package"]["name"]
        self.assertEqual((copy / "threads" / "caption.txt").read_bytes(), before)
        self.assertEqual((self.shares / name / "threads" / "caption.txt").read_bytes(), before)
        self.assertTrue(all(n["state"] == "exported" for n in dup["package"]["networks"]))

    def test_save_as_new_twice_never_overwrites(self):
        first = self.save_new(self.BODY)["package"]["name"]
        second = self.save_new(self.BODY)["package"]["name"]
        self.assertNotEqual(first, second)
        code, data = self.post("/api/prepare", dict(self.BODY, mode="new", package=first))
        self.assertEqual(code, 422, data)
        code, data = self.post("/api/prepare", dict(self.BODY, mode="replace", package=first, confirm=True))
        self.assertEqual(code, 422, data)
        self.assertEqual(len(self.packages()), 2)

    def test_update_needs_confirmation_and_the_loaded_revision(self):
        saved = self.save_new(self.BODY)["package"]
        name, revision = saved["name"], saved["revision"]
        changed = dict(self.BODY, overrides={"threads": "An updated Threads version"})
        code, data = self.post("/api/prepare", dict(changed, mode="update", package=name, revision=revision))
        self.assertEqual(code, 422, data)
        self.assertIn("confirm", data["errors"][0])
        code, data = self.post("/api/prepare", dict(changed, mode="update", package=name,
                                                    revision="0" * 16, confirm=True))
        self.assertEqual(code, 409, data)
        self.assertIn("changed since you opened it", data["errors"][0])
        self.assertEqual((self.shares / name / "threads" / "caption.txt").read_text(encoding="utf-8"),
                         "A short Threads version")
        code, data = self.post("/api/prepare", dict(changed, mode="update", package=name, revision=revision,
                                                    confirm=True))
        self.assertEqual(code, 200, data)
        self.assertTrue(data["updated"])
        self.assertEqual(data["package"]["name"], name)
        self.assertEqual(self.packages(), [name])
        self.assertEqual((self.shares / name / "threads" / "caption.txt").read_text(encoding="utf-8"),
                         "An updated Threads version")
        self.assertFalse([p for p in self.shares.iterdir() if p.name.startswith((".pending-", ".replaced-"))])
        self.assertNotEqual(data["package"]["revision"], revision)

    def test_an_invalid_update_leaves_the_package_as_it_was(self):
        saved = self.save_new(self.BODY)["package"]
        bad = dict(self.BODY, overrides={"threads": "x" * 700})
        code, data = self.post("/api/prepare", dict(bad, mode="update", package=saved["name"],
                                                    revision=saved["revision"], confirm=True))
        self.assertEqual(code, 422, data)
        self.assertEqual((self.shares / saved["name"] / "threads" / "caption.txt").read_text(encoding="utf-8"),
                         "A short Threads version")


class DraftTests(ComposerBase):
    def test_draft_round_trip_is_private_and_holds_only_editor_fields(self):
        code, data = self.post("/api/draft/load")
        self.assertEqual((code, data["draft"]), (200, None))
        draft = {"platforms": ["threads"], "text": CAPTION, "title": None, "note": "", "overrides": {},
                 "media": [], "package": None, "revision": None}
        code, data = self.post("/api/draft/save", draft)
        self.assertEqual(code, 200, data)
        path = self.root / "state" / "composer" / store.DRAFT_FILE
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertNotIn(self.srv.token, path.read_text(encoding="utf-8"))
        code, data = self.post("/api/draft/load")
        self.assertEqual(data["draft"]["text"], CAPTION)
        for extra in ({"token": "abc"}, {"password": "x"}, {"out": "/tmp/x"}):
            with self.subTest(extra=extra):
                code, data = self.post("/api/draft/save", dict(draft, **extra))
                self.assertEqual(code, 422, data)
        code, data = self.post("/api/draft/save", dict(draft, package="../../etc"))
        self.assertEqual(code, 422, data)
        code, _ = self.post("/api/draft/clear")
        self.assertEqual(code, 200)
        self.assertFalse(path.exists())
        self.assertFalse((self.root / "state" / "shares").exists() and self.packages())

    def test_a_damaged_draft_starts_the_editor_empty(self):
        folder = store.composer_dir()
        (folder / store.DRAFT_FILE).write_text("{not json", encoding="utf-8")
        code, data = self.post("/api/draft/load")
        self.assertEqual((code, data["draft"]), (200, None))

    def test_a_symlinked_draft_is_never_read_or_written_through(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps({"platforms": ["threads"], "text": "outside"}), encoding="utf-8")
        folder = store.composer_dir()
        os.symlink(outside, folder / store.DRAFT_FILE)
        code, data = self.post("/api/draft/load")
        self.assertEqual(code, 422, data)
        code, data = self.post("/api/draft/save", {"platforms": [], "text": "x"})
        self.assertEqual(code, 422, data)
        self.assertIn("outside", outside.read_text(encoding="utf-8"))


class MediaTests(ComposerBase):
    def test_upload_preview_checks_and_package_copy(self):
        code, data = self.upload(png_bytes(), "../../My Photo!.png")
        self.assertEqual(code, 200, data)
        media = data["media"]
        self.assertEqual(media["name"], "My-Photo.png")
        self.assertEqual(media["kind"], "image")
        stored = store.media_path(media["id"])
        self.assertEqual(stored.parent.parent, (self.root / "state" / "composer" / "media").absolute())
        body = {"platforms": ["instagram", "bluesky"], "text": CAPTION, "media": [media["id"]]}
        code, preview = self.post("/api/preview", body)
        self.assertEqual(code, 200, preview)
        cards = {c["id"]: c for c in preview["platforms"]}
        # The findings share computes, unchanged: a PNG breaks Instagram's sourced JPEG-only rule.
        self.assertTrue(any(line.startswith("MEDIA PROBLEM") and "png" in line
                            for line in cards["instagram"]["media_checks"]), cards["instagram"]["media_checks"])
        self.assertTrue(any("1080x1350" in line for line in cards["bluesky"]["media_checks"]),
                        cards["bluesky"]["media_checks"])
        self.assertTrue(any("ran offline" in line for line in cards["bluesky"]["media_checks"]))
        self.assertFalse(any("ran offline" in w for w in cards["bluesky"]["warnings"]))
        saved = self.save_new(body)
        folder = self.shares / saved["package"]["name"]
        self.assertEqual((folder / "media" / "My-Photo.png").read_bytes(), png_bytes())
        self.assertEqual(saved["package"]["media"], ["My-Photo.png"])
        export = json.loads((folder / "bluesky" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["share"]["media_checks"][0]["path"], "media/My-Photo.png")
        self.assertNotIn(str(self.root), json.dumps(export["share"]["media_checks"]))
        self.assertIn("media/My-Photo.png", (folder / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        # Reopen copies the package media back into the editor's store under a new id.
        code, opened = self.post("/api/package/open", {"package": saved["package"]["name"]})
        self.assertEqual(code, 200, opened)
        self.assertEqual([m["name"] for m in opened["media"]], ["My-Photo.png"])
        self.assertEqual(opened["editor"]["media"], [opened["media"][0]["id"]])

    def test_extension_allowlist_size_and_type_limits(self):
        for name in ("run.exe", "vector.svg", "noext", "", "photo.png.html"):
            with self.subTest(name=name):
                code, data = self.upload(png_bytes(), name)
                self.assertEqual(code, 422, data)
        code, data = self.upload(png_bytes(), "photo.png", ctype="application/json")
        self.assertEqual(code, 415, data)
        code, data = self.upload(b"", "photo.png")
        self.assertEqual(code, 413, data)
        conn = http.client.HTTPConnection(BIND_ADDRESS, self.srv.server_port, timeout=5)
        try:  # an oversize Content-Length is refused before a byte of the body is read
            conn.putrequest("POST", "/api/media", skip_host=True)
            for key, value in (("Host", f"{BIND_ADDRESS}:{self.srv.server_port}"), ("Origin", self.srv.origin),
                               ("X-ReachWeaver-Token", self.srv.token), ("X-ReachWeaver-Filename", "big.mp4"),
                               ("Content-Type", "application/octet-stream"),
                               ("Content-Length", str(store.MAX_MEDIA_BYTES + 1))):
                conn.putheader(key, value)
            conn.endheaders()
            res = conn.getresponse()
            self.assertEqual(res.status, 413)
        finally:
            conn.close()
        self.assertEqual([p for p in store.media_dir().iterdir()], [])

    def test_a_short_or_slow_upload_keeps_nothing(self):
        import socket
        with patch("reachweaver.share_browser.MEDIA_TIMEOUT_SECONDS", 1):
            sock = socket.create_connection((BIND_ADDRESS, self.srv.server_port), timeout=10)
            try:
                sock.sendall((f"POST /api/media HTTP/1.1\r\nHost: {BIND_ADDRESS}:{self.srv.server_port}\r\n"
                              f"Origin: {self.srv.origin}\r\nX-ReachWeaver-Token: {self.srv.token}\r\n"
                              "X-ReachWeaver-Filename: clip.mp4\r\nContent-Type: application/octet-stream\r\n"
                              "Content-Length: 5000\r\n\r\n").encode() + b"\x00" * 100)
                status = sock.recv(4096).split(b"\r\n", 1)[0]
            finally:
                sock.close()
        self.assertIn(b" 422", status)
        self.assertEqual(list(store.media_dir().iterdir()), [])

    def test_too_many_files_and_unknown_media_ids_are_refused(self):
        for bad in (["../../etc/passwd"], ["0123456789abcdef/../x"], ["zz"], "one", [1]):
            with self.subTest(bad=bad):
                code, data = self.post("/api/preview", {"platforms": ["threads"], "text": CAPTION, "media": bad})
                self.assertEqual(code, 422, data)
        code, data = self.upload(png_bytes(), "a.png")
        self.assertEqual(code, 200, data)
        twice = [data["media"]["id"]] * 2
        code, data = self.post("/api/preview", {"platforms": ["threads"], "text": CAPTION, "media": twice})
        self.assertEqual(code, 422, data)

    def test_symlinked_media_is_never_followed(self):
        secret = self.root / "outside-secret.png"
        secret.write_bytes(png_bytes(10, 10))
        box = store.media_dir() / "0123456789abcdef"
        box.mkdir(mode=0o700)
        os.symlink(secret, box / "photo.png")
        code, data = self.post("/api/prepare", {"platforms": ["threads"], "text": CAPTION,
                                                "media": ["0123456789abcdef"]})
        self.assertEqual(code, 422, data)
        self.assertIn("symlink", data["errors"][0])
        os.symlink(store.media_dir() / "0123456789abcdef", store.media_dir() / "fedcba9876543210")
        code, data = self.post("/api/preview", {"platforms": ["threads"], "text": CAPTION,
                                                "media": ["fedcba9876543210"]})
        self.assertEqual(code, 422, data)
        self.assertEqual(self.packages(), [])
        # A package whose media folder holds a symlink is refused on open and duplicate; nothing is copied.
        name = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        (self.shares / name / "media").mkdir(mode=0o700)
        os.symlink(secret, self.shares / name / "media" / "photo.png")
        for path in ("/api/package/open", "/api/package/duplicate"):
            with self.subTest(path=path):
                code, data = self.post(path, {"package": name})
                self.assertEqual(code, 422, data)
                self.assertIn("symlink", data["errors"][0])
        self.assertEqual(len(self.packages()), 1)
        self.assertTrue(secret.exists())

    def test_copy_file_refuses_a_symlink_source_and_an_existing_destination(self):
        target = self.root / "real.png"
        target.write_bytes(png_bytes(4, 4))
        link = self.root / "link.png"
        os.symlink(target, link)
        with self.assertRaises(OSError):
            store.copy_file(link, self.root / "copy.png")
        with self.assertRaises(FileExistsError):
            store.copy_file(target, target)


class MarkPostedTests(ComposerBase):
    def saved(self, platforms=("threads", "reddit", "xiaohongshu"), note="Read the rules") -> str:
        body = {"platforms": list(platforms), "text": CAPTION}
        if "reddit" in platforms:
            body["note"] = note
        return self.save_new(body)["package"]["name"]

    def test_mark_posted_records_a_terminal_by_hand_record_and_nothing_sendable(self):
        name = self.saved()
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL,
                                                    "posted_at": "2026-10-20T09:00:00Z"})
        self.assertEqual(code, 200, data)
        posted = data["posted"]
        self.assertEqual(posted["status"], "published_manual")
        self.assertEqual(posted["label"], "posted by hand")
        self.assertEqual(posted["posted_at"], "2026-10-20T09:00:00+00:00")
        states = {n["id"]: n["state"] for n in data["package"]["networks"]}
        self.assertEqual(states, {"threads": "posted", "reddit": "exported", "xiaohongshu": "exported"})
        self.assertIn("can never be approved, scheduled or sent", data["notice"])
        record = json.loads((self.shares / name / "threads" / store.POSTED_FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["post_id"], posted["post_id"])
        with connect(self.db) as con:
            rows = [dict(r) for r in con.execute("SELECT id, status, platform, content, remote_url, approved_at "
                                                 "FROM posts")]
            self.assertEqual(rows, [{"id": posted["post_id"], "status": "published_manual", "platform": "threads",
                                     "content": CAPTION, "remote_url": URL, "approved_at": None}])
            self.assertEqual(pending(con), [])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts WHERE status IN "
                                         "('approved','scheduled','sending','needs_review')").fetchone()[0], 0)
        # The same network again: refused before the database; the same link elsewhere: the store refuses it.
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL})
        self.assertEqual(code, 409, data)
        other = self.saved(platforms=("threads",))
        code, data = self.post("/api/mark-posted", {"package": other, "platform": "threads", "url": URL})
        self.assertEqual(code, 422, data)
        self.assertIn("already recorded", data["errors"][0])
        self.assertFalse((self.shares / other / "threads" / store.POSTED_FILE).exists())
        # A posted package cannot be updated in place.
        current = next(p for p in self.post("/api/packages")[1]["packages"] if p["name"] == name)
        code, data = self.post("/api/prepare", {"platforms": ["threads"], "text": "new", "mode": "update",
                                                "package": name, "revision": current["revision"],
                                                "confirm": True})
        self.assertEqual(code, 409, data)
        self.assertIn("marked as posted", data["errors"][0])

    def test_record_manual_publish_refusals_surface_as_plain_messages(self):
        name = self.saved()
        cases = [
            ({"platform": "threads", "url": "not a link"}, "Paste the full link to your post, starting with https://"),
            ({"platform": "threads", "url": ""}, "Paste the link"),
            ({"platform": "threads", "url": "javascript:alert(1)"}, "starting with https://"),
            ({"platform": "threads", "url": URL, "posted_at": "2999-01-01T00:00:00Z"}, "in the future"),
            ({"platform": "threads", "url": URL, "posted_at": "yesterday"}, "ISO 8601"),
            ({"platform": "reddit", "url": URL}, "human-only"),
            ({"platform": "xiaohongshu", "url": URL}, "cannot measure"),
            ({"platform": "instagram", "url": URL}, "not in this package"),
            ({"platform": "../../etc", "url": URL}, "not in this package"),
        ]
        for extra, words in cases:
            with self.subTest(extra=extra):
                code, data = self.post("/api/mark-posted", dict(extra, package=name))
                self.assertEqual(code, 422, data)
                self.assertIn(words, data["errors"][0])
                self.assertNotIn("--url", data["errors"][0])
        self.assertFalse(list(self.shares.glob(f"{name}/*/{store.POSTED_FILE}")))
        if Path(self.db).exists():
            with connect(self.db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_dev_maps_to_the_devto_history_platform(self):
        name = self.save_new({"platforms": ["dev"], "text": CAPTION})["package"]["name"]
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "dev",
                                                    "url": "https://dev.to/owner/offline-helper-1a2b"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["posted"]["platform"], "devto")

    def test_media_material_is_recorded_for_the_repeat_guard(self):
        code, data = self.upload(png_bytes(), "photo.png")
        media_id = data["media"]["id"]
        name = self.save_new({"platforms": ["threads"], "text": CAPTION, "media": [media_id]})["package"]["name"]
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL})
        self.assertEqual(code, 200, data)
        with connect(self.db) as con:
            images = con.execute("SELECT images_json FROM shared_material").fetchone()[0]
        self.assertEqual(len(json.loads(images)), 1)


class WritePathSecurityTests(ComposerBase):
    def test_every_new_route_needs_token_origin_and_host(self):
        name = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        before = sorted(str(p) for p in self.root.rglob("*"))
        bodies = {"/api/package/open": {"package": name}, "/api/package/duplicate": {"package": name},
                  "/api/mark-posted": {"package": name, "platform": "threads", "url": URL},
                  "/api/draft/save": {"platforms": ["threads"], "text": "x"}, "/api/draft/clear": {},
                  "/api/draft/load": {}, "/api/packages": {}, "/api/prepare": {"platforms": ["threads"], "text": "x"},
                  "/api/accounts": {}}
        self.assertEqual(set(bodies) | {"/api/preview"}, set(JSON_ROUTES))
        port = self.srv.server_port
        variants = [
            {"X-ReachWeaver-Origin-Test": "no token", "Origin": self.srv.origin},
            {"X-ReachWeaver-Token": "wrong-" + "a" * 40, "Origin": self.srv.origin},
            {"X-ReachWeaver-Token": self.srv.token, "Origin": "https://attacker.example"},
            {"X-ReachWeaver-Token": self.srv.token, "Origin": "null"},
            {"X-ReachWeaver-Token": self.srv.token},
            {"X-ReachWeaver-Token": self.srv.token, "Origin": self.srv.origin, "Host": "attacker.example"},
            {"X-ReachWeaver-Token": self.srv.token, "Origin": self.srv.origin, "Host": f"localhost:{port}"},
        ]
        for path, body in bodies.items():
            for headers in variants:
                with self.subTest(path=path, headers=sorted(headers)):
                    code, data = call(self.srv, path, body, headers=headers, good=False)
                    self.assertEqual(code, 403, data)
        for headers in variants:
            code, data = call(self.srv, "/api/media", raw=png_bytes(4, 4), ctype="application/octet-stream",
                              headers=dict(headers, **{"X-ReachWeaver-Filename": "a.png"}), good=False)
            self.assertEqual(code, 403, data)
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
        self.assertFalse(Path(self.db).exists(), "no refused request may open the history database")

    def test_oversize_and_non_json_bodies_on_new_routes(self):
        for path in sorted(JSON_ROUTES):
            with self.subTest(path=path):
                code, _ = call(self.srv, path, raw=b"x" * (MAX_REQUEST_BYTES + 1))
                self.assertEqual(code, 413)
                code, _ = call(self.srv, path, raw=b"{}", ctype="text/plain")
                self.assertEqual(code, 415)
                code, _ = call(self.srv, path, raw=b"[" * 1000)
                self.assertEqual(code, 422)
        code, _ = call(self.srv, "/api/send", {})
        self.assertEqual(code, 404)
        code, _ = call(self.srv, "/api/approve", {})
        self.assertEqual(code, 404)

    def test_package_names_cannot_traverse(self):
        self.save_new({"platforms": ["threads"], "text": CAPTION})
        outside = self.root / "state" / "share-20261021-120000-aaaaaaaaaaaa"
        outside.mkdir()
        for bad in ("../share-20261021-120000-aaaaaaaaaaaa", "/etc", "..", "share-20261021-120000-aaaaaaaaaaaa/..",
                    "share-20261021-120000-AAAAAAAAAAAA", "", None, 7, ["x"], "state/shares/x"):
            for path, body in (("/api/package/open", {"package": bad}), ("/api/package/duplicate", {"package": bad}),
                               ("/api/mark-posted", {"package": bad, "platform": "threads", "url": URL}),
                               ("/api/prepare", {"platforms": ["threads"], "text": "x", "mode": "update",
                                                 "package": bad, "revision": "0" * 16, "confirm": True})):
                with self.subTest(path=path, bad=bad):
                    code, data = self.post(path, body)
                    self.assertEqual(code, 422, data)
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_symlinked_package_or_shares_folder_is_refused(self):
        real = self.root / "elsewhere" / "share-20261021-120000-bbbbbbbbbbbb"
        real.mkdir(parents=True)
        (real / "threads").mkdir()
        store.shares_dir()
        os.symlink(real, self.shares / "share-20261021-120000-bbbbbbbbbbbb")
        code, data = self.post("/api/package/open", {"package": "share-20261021-120000-bbbbbbbbbbbb"})
        self.assertEqual(code, 422, data)
        code, data = self.post("/api/packages")
        self.assertEqual((code, data["packages"]), (200, []))
        # The whole shares folder as a symlink: nothing is listed, read or written.
        os.rename(self.shares, self.root / "state" / "moved")
        os.symlink(self.root / "state" / "moved", self.shares)
        code, data = self.post("/api/packages")
        self.assertEqual(code, 422, data)
        code, data = self.post("/api/prepare", {"platforms": ["threads"], "text": CAPTION})
        self.assertEqual(code, 422, data)
        self.assertEqual([p.name for p in (self.root / "state" / "moved").iterdir()],
                         ["share-20261021-120000-bbbbbbbbbbbb"])

    def test_a_posted_marker_that_is_a_symlink_is_refused(self):
        name = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        target = self.root / "victim.txt"
        target.write_text("keep", encoding="utf-8")
        os.symlink(target, self.shares / name / "threads" / store.POSTED_FILE)
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL})
        self.assertEqual(code, 422, data)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
        self.assertFalse(Path(self.db).exists())

    def test_composer_module_has_no_send_path_and_the_dashboard_stays_get_only(self):
        from reachweaver import share_browser, ui_server
        self.assertIsNone(store.SEND_PATH)
        self.assertIsNone(share_browser.SEND_PATH)
        source = Path(store.__file__).read_text(encoding="utf-8") + Path(share_browser.__file__).read_text(
            encoding="utf-8")
        for word in ("publisher", "claim_sending", "transition(", "approve_post", "schedule_post", "urlopen",
                     "http.client", "import requests", "socket.create_connection"):
            self.assertNotIn(word, source)
        # The read-only dashboard (`ui`) keeps refusing every write method: the composer is the only write path.
        for method in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
            self.assertIs(getattr(ui_server.Handler, method), ui_server.Handler._refuse_method)

    def test_accounts_route_is_read_only(self):
        config = self.root / "config"
        config.mkdir()
        (config / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        before = (config / "accounts.json").read_bytes()
        code, data = self.post("/api/accounts", {})
        self.assertEqual(code, 200, data)
        self.assertEqual((config / "accounts.json").read_bytes(), before)
        self.assertFalse(Path(self.db).exists())
        code, data = self.post("/api/accounts", {"path": "/etc/passwd"})
        self.assertEqual(code, 422, data)

    def test_database_is_untouched_until_mark_posted(self):
        self.save_new({"platforms": ["threads"], "text": CAPTION})
        self.post("/api/packages")
        self.post("/api/draft/save", {"platforms": ["threads"], "text": CAPTION})
        self.assertFalse(Path(self.db).exists())
        with self.assertRaises(sqlite3.OperationalError):
            sqlite3.connect(f"file:{self.db}?mode=ro", uri=True).execute("SELECT 1")


class AccountChoiceTests(ComposerBase):
    """The optional "which account" on Mark as posted: listed read-only, validated by the server."""

    def setUp(self):
        super().setUp()
        (self.root / "config").mkdir()
        (self.root / "config" / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")

    def nothing_recorded(self, name: str) -> None:
        self.assertFalse(list(self.shares.glob(f"{name}/*/{store.POSTED_FILE}")))
        if Path(self.db).exists():
            with connect(self.db) as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_accounts_lists_public_ids_and_handles_only(self):
        code, data = self.post("/api/accounts", {})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["accounts"], [
            {"id": "owner-bsky", "platform": "bluesky", "name": "owner.example.bsky.social"},
            {"id": "owner-devto", "platform": "devto", "name": "owner"},
            {"id": "owner-threads", "platform": "threads", "name": "owner.example"}])
        self.assertEqual(data["ledger_ids"], {"dev": "devto"})
        self.assertNotIn("REACHWEAVER_", json.dumps(data))  # no env_prefix (credential variable names)

    def test_missing_or_invalid_accounts_file_offers_no_choice(self):
        (self.root / "config" / "accounts.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(self.post("/api/accounts", {})[1]["accounts"], [])
        (self.root / "config" / "accounts.json").unlink()
        self.assertEqual(self.post("/api/accounts", {})[1]["accounts"], [])

    def test_unknown_mismatched_or_malformed_account_is_refused_before_any_write(self):
        name = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        cases = [("someone-else", "no account called someone-else"),
                 ("owner-bsky", "is for bluesky, not Threads"),
                 ("../owner-threads", "Choose an account from the list"),
                 (7, "Choose an account from the list"),
                 ("Owner-Threads", "Choose an account from the list")]
        for account, words in cases:
            with self.subTest(account=account):
                code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL,
                                                            "account_id": account})
                self.assertEqual(code, 422, data)
                self.assertIn(words, data["errors"][0])
                self.assertIn("Nothing was recorded", data["errors"][0])
                self.nothing_recorded(name)

    def test_unreadable_accounts_file_refuses_a_chosen_account(self):
        name = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        (self.root / "config" / "accounts.json").unlink()
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL,
                                                    "account_id": "owner-threads"})
        self.assertEqual(code, 422, data)
        self.assertIn("could not be read", data["errors"][0])
        self.nothing_recorded(name)

    def test_a_matching_account_is_recorded_and_no_account_stays_valid(self):
        name = self.save_new({"platforms": ["threads", "dev"], "text": CAPTION})["package"]["name"]
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL,
                                                    "account_id": "owner-threads"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["posted"]["account_id"], "owner-threads")
        threads = next(n for n in data["package"]["networks"] if n["id"] == "threads")
        self.assertEqual(threads["posted"]["account_id"], "owner-threads")
        post_id = data["posted"]["post_id"]
        self.assertEqual(data["next"], f"reachweaver metrics {post_id} --observed-at TIME --source native-export "
                                       "--likes N --replies N")
        from reachweaver.cli_parser import make_parser
        args = make_parser().parse_args(data["next"].split()[1:])  # the suggested command parses as given
        self.assertEqual((args.command, args.post_id, args.likes), ("metrics", post_id, "N"))
        with connect(self.db) as con:
            row = con.execute("SELECT account_id, status, approved_at FROM posts WHERE id=?", (post_id,)).fetchone()
            self.assertEqual(tuple(row), ("owner-threads", "published_manual", None))
            self.assertEqual(pending(con), [])
        # dev maps to the devto history platform, so a devto account matches it; no account at all is fine too.
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "dev", "account_id": "owner-devto",
                                                    "url": "https://dev.to/owner/offline-helper-1a2b"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["posted"]["account_id"], "owner-devto")
        other = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        code, data = self.post("/api/mark-posted", {"package": other, "platform": "threads", "account_id": None,
                                                    "url": URL + "2"})
        self.assertEqual(code, 200, data)
        self.assertIsNone(data["posted"]["account_id"])


class DisplayNameAndPlainWordsTests(ComposerBase):
    def test_package_gets_a_readable_name_from_the_caption_and_the_folder_pattern_is_unchanged(self):
        saved = self.save_new({"platforms": ["threads"], "text": "Our first speckled mugs are out of the kiln "
                                                                 "today and they look great"})
        name = saved["package"]["name"]
        self.assertTrue(store.PACKAGE_NAME.fullmatch(name), name)
        self.assertRegex(saved["display_name"], r"^Our first speckled mugs are out of the… · \d{1,2} [A-Z][a-z]{2} "
                                                r"\d\d:\d\d$")
        self.assertEqual(saved["folder"], f"state/shares/{name}")
        info = json.loads((self.shares / name / store.INFO_FILE).read_text(encoding="utf-8"))
        self.assertEqual(info, {"display_name": saved["display_name"]})
        listed = self.post("/api/packages")[1]["packages"]
        self.assertEqual(listed[0]["display_name"], saved["display_name"])

    def test_display_name_strips_controls_and_bidi_and_stays_plain_text(self):
        markup = "<b>Hi</b>\n\tthere <script>alert(1)</script> and a much longer tail of words"
        shown = self.save_new({"platforms": ["threads"], "text": markup})["display_name"]
        title = shown.split(" · ")[0]
        self.assertTrue(title.startswith("<b>Hi</b> there <script>"), title)  # data; the page uses textContent
        self.assertLessEqual(len(title), store.MAX_TITLE_CHARS + 1)
        self.assertNotIn("\n", shown)
        self.assertNotIn("\t", shown)
        # The share preflight already refuses bidi overrides and controls in a caption; the name is cleaned anyway
        # (a package rebuilt from older files never carries them into the page).
        hostile = "Hi" + chr(0x202E) + chr(0x200B) + chr(7) + chr(0x2028) + chr(0xD800) + "there" + chr(0xE000)
        self.assertEqual(store.display_title(hostile), "Hi there")
        self.assertEqual(store.display_title("   "), "Untitled post")
        self.assertEqual(store.display_title("", "Read the rules"), "Read the rules")

    def test_a_tampered_stored_name_is_not_trusted(self):
        saved = self.save_new({"platforms": ["threads"], "text": "Kiln day"})
        folder = self.shares / saved["package"]["name"]
        for bad in ("Fine\u202eevil", "x" * 500, 42, "line\nbreak"):
            with self.subTest(bad=bad):
                (folder / store.INFO_FILE).write_text(json.dumps({"display_name": bad}), encoding="utf-8")
                shown = store.package_display(folder)
                self.assertTrue(shown.startswith("Kiln day · "), shown)
        (folder / store.INFO_FILE).unlink()
        os.symlink(self.root / "elsewhere.json", folder / store.INFO_FILE)
        (self.root / "elsewhere.json").write_text(json.dumps({"display_name": "Planted"}), encoding="utf-8")
        self.assertTrue(store.package_display(folder).startswith("Kiln day · "))

    def test_preview_warnings_and_errors_are_plain(self):
        code, data = self.post("/api/preview", {"platforms": ["instagram", "tiktok"], "text": CAPTION})
        self.assertEqual(code, 200, data)
        cards = {c["id"]: c for c in data["platforms"]}
        self.assertIn("Instagram needs a photo or video. Add it here or attach it in the Instagram app.",
                      cards["instagram"]["warnings"])
        self.assertIn("TikTok needs a video. Add it here or attach it in the TikTok app.", cards["tiktok"]["warnings"])
        self.assertFalse(any("manifest" in w for c in cards.values() for w in c["warnings"]))
        code, data = self.post("/api/preview", {"platforms": ["youtube"], "text": CAPTION})
        self.assertEqual(code, 422, data)
        self.assertIn("YouTube needs a video title", data["errors"][0])
        self.assertNotIn("platform_options", data["errors"][0])
        code, data = self.post("/api/preview", {"platforms": ["reddit"], "text": "A ready promo post"})
        self.assertEqual(code, 422, data)
        self.assertIn("human-only", data["errors"][0])
        self.assertNotIn("--note", data["errors"][0])
        code, data = self.post("/api/preview", {"platforms": [], "text": CAPTION})
        self.assertEqual(code, 422, data)
        self.assertEqual(data["errors"], ["Choose at least one network."])

    def test_page_strings_brand_and_accessibility_hooks(self):
        from reachweaver.share_browser_ui import HTML
        for needed in ("<title>ReachWeaver — Write a post</title>", 'aria-hidden="true">RW</span>', "#0b6e69",
                       "@media (prefers-color-scheme: dark)", 'role="status"', '"aria-current", "step"',
                       "Ready to post", "Paste the full link to your post, starting with https://",
                       "See how your posts did: run ", '"reachweaver ui"', "Copy folder path",
                       "Recorded as posted by you on ", "Next: when you have numbers, run ", "Which account? (optional)",
                       "min-height: 44px", "Write", "Choose", "Preview", "Save", "Post"):
            self.assertIn(needed, HTML)
        for gone in ("innerHTML", "Exported", "outerHTML", "insertAdjacentHTML", "eval(", "✦"):
            self.assertNotIn(gone, HTML)


if __name__ == "__main__":
    unittest.main()
