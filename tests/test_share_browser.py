"""Browser Share Composer: loopback-only, token-and-origin-protected, manual-only tests.

The only HTTP used here is a test server bound to 127.0.0.1. No network
providers, credentials, cookies, social accounts or live publishing are used.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from reachweaver import home
from reachweaver.policy import ValidationError
from reachweaver.share_browser import (
    BIND_ADDRESS, SEND_PATH, ShareServer, MAX_REQUEST_BYTES, _incoming, catalog,
)


@contextmanager
def running_server():
    srv = ShareServer(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=3)


def request(srv, method, path, body=None, *, token=None, origin=None, content_type=None, host=None):
    conn = http.client.HTTPConnection(BIND_ADDRESS, srv.server_port, timeout=5)
    headers = {"Host": host if host is not None else f"{BIND_ADDRESS}:{srv.server_port}"}
    if token is not None:
        headers["X-ReachWeaver-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    if content_type is not None:
        headers["Content-Type"] = content_type
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    try:
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        content = res.read()
        return res.status, dict(res.getheaders()), content
    finally:
        conn.close()


class BrowserComposerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        old = home.root()
        home.set_root(self.root)
        self.addCleanup(lambda: home.set_root(old))

    def test_local_only_and_no_send_path(self):
        self.assertEqual(BIND_ADDRESS, "127.0.0.1")
        self.assertIsNone(SEND_PATH)
        with self.assertRaises(ValidationError):
            ShareServer(-1)
        with self.assertRaises(ValidationError):
            ShareServer(65536)
        with running_server() as srv:
            self.assertEqual(srv.server_address[0], BIND_ADDRESS)
            self.assertTrue(srv.url.startswith("http://127.0.0.1:"))
            self.assertIn("/#", srv.url)
            self.assertGreater(len(srv.token), 30)

    def test_ui_is_self_contained_with_strict_security_headers(self):
        with running_server() as srv:
            code, headers, body = request(srv, "GET", "/")
            html = body.decode("utf-8")
            self.assertEqual(code, 200)
            self.assertIn("One story.", html)
            self.assertIn("Share manually", html)
            self.assertNotIn("<script src=", html)
            self.assertNotIn("<link rel=", html)
            self.assertNotIn("analytics.", html)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
            self.assertIn("script-src 'nonce-", headers["Content-Security-Policy"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertNotIn(srv.token, html, "secret must be in link fragment only")
            self.assertFalse((self.root / "state").exists())

    def test_catalog_requires_token_and_lists_only_manual_destinations(self):
        with running_server() as srv:
            status, _, _ = request(srv, "GET", "/api/catalog")
            self.assertEqual(status, 403)
            status, _, response = request(srv, "GET", "/api/catalog", token=srv.token)
            self.assertEqual(status, 200)
            data = json.loads(response)
            ids = {p["id"] for p in data["platforms"]}
            self.assertTrue({"instagram", "threads", "tiktok", "youtube", "reddit"}.issubset(ids))
            self.assertNotIn("bluesky", ids, "live-capable networks use a separate approval gate")
            reddit = next(p for p in data["platforms"] if p["id"] == "reddit")
            self.assertTrue(reddit["human_only"])
            self.assertEqual(data["max_destinations"], 10)

    def test_cross_site_and_unauthenticated_requests_are_refused(self):
        with running_server() as srv:
            body = {"platforms": ["threads"], "text": "An original project update"}
            for token, origin, host in (
                (None, srv.origin, None),
                ("not-the-token", srv.origin, None),
                (srv.token, "https://attacker.example", None),
                (srv.token, None, None),
                (srv.token, srv.origin, "attacker.example"),
            ):
                with self.subTest(token=token, origin=origin, host=host):
                    code, _, _ = request(srv, "POST", "/api/prepare", body, token=token,
                                         origin=origin, host=host, content_type="application/json")
                    self.assertEqual(code, 403)
            code, _, _ = request(srv, "OPTIONS", "/api/prepare", token=srv.token, origin=srv.origin)
            self.assertEqual(code, 405)
            self.assertFalse((self.root / "state").exists())

    def test_preview_never_writes_and_prepare_saves_handoff_only(self):
        with running_server() as srv:
            item = {"platforms": ["instagram", "threads"], "text": "A helpful project demo"}
            extra = {"token": srv.token, "origin": srv.origin, "content_type": "application/json"}
            code, _, raw = request(srv, "POST", "/api/preview", item, **extra)
            self.assertEqual(code, 200, raw.decode())
            preview = json.loads(raw)
            self.assertEqual({p["id"] for p in preview["platforms"]}, {"instagram", "threads"})
            self.assertFalse((self.root / "state").exists(), "preview must not create local files")
            self.assertTrue(all(not p["published"] for p in preview["platforms"]))
            self.assertEqual(preview["platforms"][0]["text"], item["text"])
            code, _, raw = request(srv, "POST", "/api/prepare", item, **extra)
            self.assertEqual(code, 200, raw.decode())
            prepared = json.loads(raw)
            self.assertFalse(prepared["published"])
            out = Path(prepared["saved_to"])
            self.assertEqual(out.parent, self.root / "state" / "shares")
            self.assertTrue((out / "OPEN-FIRST.md").is_file())
            self.assertEqual((out / "instagram" / "caption.txt").read_text(encoding="utf-8"),
                             item["text"])
            self.assertEqual((out / "threads" / "caption.txt").read_text(encoding="utf-8"),
                             item["text"])
            self.assertFalse(list(out.glob(".pending-*")), "no internal staging artifacts")
            self.assertIn("NOT PUBLISHED", (out / "OPEN-FIRST.md").read_text(encoding="utf-8"))
            if __import__("os").name == "posix":
                import stat
                self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((out / "OPEN-FIRST.md").stat().st_mode), 0o600)

    def test_human_only_venues_get_notes_not_generated_posts(self):
        with running_server() as srv:
            extra = {"token": srv.token, "origin": srv.origin, "content_type": "application/json"}
            data = {"platforms": ["reddit"], "note": "Read rules and add a personal answer"}
            status, _, body = request(srv, "POST", "/api/prepare", data, **extra)
            self.assertEqual(status, 200, body.decode())
            out = Path(json.loads(body)["saved_to"])
            self.assertTrue((out / "reddit" / "NOTES.md").is_file())
            self.assertFalse((out / "reddit" / "post.txt").exists())
            bad = {"platforms": ["reddit"], "text": "AI promotional post",
                   "note": "Make this viral"}
            status, _, body = request(srv, "POST", "/api/prepare", bad, **extra)
            self.assertEqual(status, 422, body.decode())
            self.assertIn("human-only", body.decode())

    def test_one_invalid_network_blocks_every_write(self):
        with running_server() as srv:
            extra = {"token": srv.token, "origin": srv.origin, "content_type": "application/json"}
            bad = {"platforms": ["instagram", "threads"], "text": "x" * 600}
            status, _, body = request(srv, "POST", "/api/prepare", bad, **extra)
            self.assertEqual(status, 422, body.decode())
            self.assertIn("threads", body.decode())
            self.assertFalse((self.root / "state").exists())

    def test_bad_body_content_type_and_limits_blocked(self):
        with running_server() as srv:
            headers = {"token": srv.token, "origin": srv.origin}
            status, _, _ = request(srv, "POST", "/api/prepare", b'{}', content_type="text/plain",
                                    **headers)
            self.assertEqual(status, 415)
            status, _, _ = request(srv, "POST", "/api/prepare", b'not-json',
                                    content_type="application/json", **headers)
            self.assertEqual(status, 422)
            status, _, _ = request(srv, "POST", "/api/prepare", b"x" * (MAX_REQUEST_BYTES + 1),
                                    content_type="application/json", **headers)
            self.assertEqual(status, 413)
            status, _, _ = request(srv, "POST", "/api/prepare",
                                    {"platforms": ["threads"], "text": "content", "out": "/tmp/hijack"},
                                    content_type="application/json", **headers)
            self.assertEqual(status, 422)
            self.assertFalse((self.root / "state").exists())

    def test_backend_refuses_unexpected_content_and_too_many_networks(self):
        with self.assertRaises(ValidationError):
            _incoming({"platforms": ["reddit"], "note": "note", "password": "no"})
        with self.assertRaises(ValidationError):
            _incoming({"platforms": ["threads"] * 20, "text": "text"})
        with self.assertRaises(ValidationError):
            _incoming({"platforms": ["bluesky"], "text": "text"})
        self.assertFalse((self.root / "state").exists())


if __name__ == "__main__":
    unittest.main()
