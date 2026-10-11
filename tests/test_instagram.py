"""Instagram single-image adapter (issue #20, owner-approved 2026-10-09; disabled until a live receipt). Every HTTP exchange is mocked: no network, no live post.

* ``UnreachableTests``: with the bundled registry (Instagram adapter ``none``, image ``api_gated``,
  no ``live_media_formats``) every entry point refuses Instagram before a credential read or request.
* ``RemoteMedia*`` / ``Binding*``: owner-hosted image URL validation, the read-only download and the
  approval binding of the image bytes (approval/media-1 payload), all offline.
* ``Flipped*``: a TEMPORARY copy of the registry with Instagram flipped to ``live_media`` (and
  ``policy.SUPPORTED_LIVE`` patched to match) shows the whole mocked contract: happy path, the
  image re-check before the container POST, 400/401/429/500, wrong account, exhausted quota, the
  emergency pause during polling and needs_review after the write started.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import os
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from reachweaver import capabilities, instagram, remote_media
from reachweaver.accounts import CREDENTIAL_SUFFIXES, load_accounts, missing_credentials
from reachweaver.capabilities import load_registry, require_capability, validate_registry
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.credential_hygiene import audit_environment, expected_variables
from reachweaver.policy import SUPPORTED_LIVE, ValidationError, validate_post
from reachweaver.publishers import PUBLISHERS, PublishError, reset_write_marker, write_started
from gate_support import publish  # noqa: E402
from reachweaver.storage import (APPROVAL_BINDING_TAG, active_provider_cooldown, BINDING_MEDIA_CHANGED, add_post, approval_payload,
                                 approval_payload_sha256, claim_sending, connect, get_post, post_media,
                                 published_text, set_post_media, transition)

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
FAKE_TOKEN = "fake-instagram-user-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_IG"
ENV = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}
LIVE_ENV = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
IG_ID, HANDLE = "17841400000000001", "igapitestuser"
CONTAINER_ID, MEDIA_ID = "17890000000000101", "17890000000000102"
IMAGE_HOST = "img.example.org"
IMAGE_URL = f"https://{IMAGE_HOST}/media/chart.jpg"
ALT = "Bar chart: monthly downloads rose from 10 to 40"
CAPTION = "Release notes are out. Link in bio."
GRAPH = urlsplit(instagram.API_BASE)
PUBLIC_IP = ["93.184.215.14"]
BUNDLED_REGISTRY_FILE = capabilities.REGISTRY_FILE


def jpeg(width=1080, height=1080, extra=b"") -> bytes:
    """A minimal baseline JPEG header (SOI, SOF0 with dimensions, EOI); enough for the header parser."""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + extra + b"\xff\xd9"


IMAGE = jpeg()
IMAGE_SHA = hashlib.sha256(IMAGE).hexdigest()


def account(**overrides):
    base = {"id": "ig-me", "platform": "instagram", "env_prefix": PREFIX, "handle": HANDLE,
            "ig_user_id": IG_ID, "authorization_expires_at": "2026-12-01T00:00:00+00:00"}
    base.update(overrides)
    return base


def record(**overrides):
    base = {"source": "url", "url": IMAGE_URL, "sha256": IMAGE_SHA, "mime": "image/jpeg", "bytes": len(IMAGE),
            "width": 1080, "height": 1080, "alt": ALT, "focus": None}
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


def http_error(url, code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    # The message and body carry the token: they must never reach an error string.
    return HTTPError(url, code, "Bearer " + FAKE_TOKEN, headers, io.BytesIO(FAKE_TOKEN.encode()))


class FakeMeta:
    """Routes mocked requests by (method, host, path). ``errors`` maps a route name to an HTTP code.

    ``images`` is the sequence of bodies the owner's image host serves (the last one repeats).
    """

    def __init__(self, *, me=None, usage=3, statuses=("FINISHED",), media=None, errors=None, images=(IMAGE,)):
        self.requests, self.routes = [], []
        self.me = me or {"user_id": IG_ID, "username": HANDLE, "account_type": "BUSINESS", "id": "998877"}
        self.usage = usage
        self.statuses = list(statuses)
        self.images = list(images)
        self.media = media if media is not None else {
            "id": MEDIA_ID, "permalink": "https://www.instagram.com/p/AbC123xyz/", "username": HANDLE,
            "media_type": "IMAGE"}
        self.errors = errors or {}

    def route_of(self, req):
        parts = urlsplit(req.full_url)
        if parts.scheme != "https":
            raise AssertionError("non-https request")
        if parts.hostname == IMAGE_HOST and req.get_method() == "GET" and parts.path == "/media/chart.jpg":
            return "image"
        if parts.hostname != GRAPH.hostname:
            raise AssertionError("request to an unexpected host")
        base = GRAPH.path
        return {("GET", f"{base}/me"): "me",
                ("GET", f"{base}/{IG_ID}/content_publishing_limit"): "quota",
                ("POST", f"{base}/{IG_ID}/media"): "create",
                ("GET", f"{base}/{CONTAINER_ID}"): "status",
                ("POST", f"{base}/{IG_ID}/media_publish"): "publish",
                ("GET", f"{base}/{MEDIA_ID}"): "media"}[(req.get_method(), parts.path)]

    def __call__(self, req):
        route = self.route_of(req)
        self.requests.append(req)
        self.routes.append(route)
        if route in self.errors:
            raise http_error(req.full_url, self.errors[route], retry_after=120)
        if route == "image":
            body = self.images.pop(0) if len(self.images) > 1 else self.images[0]
            return FakeResponse(200, body, {"Content-Type": "image/jpeg"})
        body = {
            "me": lambda: self.me,
            "quota": lambda: {"data": [{"quota_usage": self.usage,
                                        "config": {"quota_total": 50, "quota_duration": 86400}}]},
            "create": lambda: {"id": CONTAINER_ID},
            "status": lambda: {"status_code": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0],
                               "id": CONTAINER_ID},
            "publish": lambda: {"id": MEDIA_ID},
            "media": lambda: self.media,
        }[route]()
        return FakeResponse(200, json.dumps(body).encode())

    def request(self, route):
        return self.requests[self.routes.index(route)]

    def json_body(self, route):
        return json.loads(self.request(route).data.decode())

    def query(self, route):
        return {k: v[0] for k, v in parse_qs(urlsplit(self.request(route).full_url).query).items()}


def flipped_registry() -> dict:
    """The registry change the owner would make AFTER review and a live receipt (temp copy only)."""
    registry = json.loads(BUNDLED_REGISTRY_FILE.read_text(encoding="utf-8"))
    entry = next(e for e in registry["platforms"] if e["id"] == "instagram")
    entry.update({
        "status": "supported", "reachweaver_adapter": "live_media", "live_media_formats": ["image"],
        "rate_limits": {
            "summary": "content_publishing_limit quota_total currently 50 per 86400 s (endpoint reference); the "
                       "content publishing guide says 100 API-published posts per 24-hour moving period",
            "source": "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/"
                      "ig-user/content_publishing_limit",
            "accessed": "2026-10-09",
            "facts": [{"scope": "account", "action": "API-published posts (24-hour moving period)",
                       "limit": 50, "unit": "posts", "window_seconds": 86400}],
        },
    })
    entry["formats"]["image"] = "api_direct"
    return registry


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = patch("reachweaver.remote_media._resolve", lambda host: list(PUBLIC_IP))
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_write_marker()


class FlippedRegistry(Base):
    """Patches a temp registry copy and every SUPPORTED_LIVE binding; the bundled file is untouched."""

    def setUp(self):
        super().setUp()
        path = self.tmp / "platform_registry.json"
        path.write_text(json.dumps(flipped_registry()), encoding="utf-8")
        live = frozenset(SUPPORTED_LIVE | {"instagram"})
        for target, value in (("reachweaver.capabilities.REGISTRY_FILE", path),
                              ("reachweaver.policy.SUPPORTED_LIVE", live),
                              ("reachweaver.capabilities.SUPPORTED_LIVE", live),
                              ("reachweaver.publishers.SUPPORTED_LIVE", live),
                              ("reachweaver.connections.SUPPORTED_LIVE", live),
                              ("reachweaver.cli.SUPPORTED_LIVE", live),
                              ("reachweaver.instagram._utc_now", lambda: NOW)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sleeps: list[float] = []
        patcher = patch("reachweaver.instagram._sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)


class CliMixin:
    tmp: Path

    def setup_cli(self, entries=None):
        self.db = self.tmp / "state" / "outbox.sqlite"
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": entries or [account()]}), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def draft_with_image(self, fake, caption=CAPTION):
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "instagram", caption)
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("media-attach", pid, "--url", IMAGE_URL, "--alt", ALT, "--online")
        assert status == 0, out + err
        return pid

    def approved_with_image(self, fake, caption=CAPTION):
        pid = self.draft_with_image(fake, caption)
        status, out, err = self.cli("approve", pid)
        assert status == 0, out + err
        return pid

    def row(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)


# --------------------------------------------------------------------------- unreachable

class UnreachableTests(CliMixin, Base):
    """Under the bundled registry the adapter cannot be reached."""

    def test_bundled_registry_keeps_instagram_gated(self):
        entry = next(e for e in load_registry()["platforms"] if e["id"] == "instagram")
        self.assertEqual((entry["reachweaver_adapter"], entry["formats"]["image"], entry["status"]),
                         ("none", "api_gated", "requires-review"))
        self.assertNotIn("live_media_formats", entry)
        self.assertNotIn("instagram", SUPPORTED_LIVE)
        self.assertIn("instagram", PUBLISHERS)  # present, but publish() refuses before the lookup
        for fmt, needle in (("image", "api_gated"), ("text", "unavailable")):
            with self.subTest(fmt=fmt), self.assertRaisesRegex(ValidationError, needle):
                require_capability("instagram", fmt, "publish")

    def test_library_publish_and_connection_refused_before_any_request(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.instagram._token") as token:
            with self.assertRaisesRegex(ValidationError, "not yet implemented for instagram"):
                publish(account(), CAPTION, "p1", media=[record()])
            with self.assertRaisesRegex(ValidationError, "platform_has_no_authorized_live_connector"):
                check_connection(account())
        send.assert_not_called()
        token.assert_not_called()

    def test_registry_alone_refuses_even_if_supported_live_is_widened(self):
        # Defense in depth: widening SUPPORTED_LIVE without the reviewed registry change fails closed.
        live = frozenset(SUPPORTED_LIVE | {"instagram"})
        with patch("reachweaver.publishers.SUPPORTED_LIVE", live), patch("reachweaver.capabilities.SUPPORTED_LIVE", live), \
                patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.instagram._token") as token:
            with self.assertRaisesRegex(ValidationError, "Invalid capability registry"):
                publish(account(), CAPTION, "p1", media=[record()])
        send.assert_not_called()
        token.assert_not_called()

    def test_cli_live_publish_skips_instagram_and_keeps_post_approved(self):
        self.setup_cli()
        pid = self.approved_with_image(FakeMeta())
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("requires manual publishing", out)
        send.assert_not_called()
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_flipped_copy_is_valid_only_with_matching_supported_live(self):
        registry = flipped_registry()
        self.assertIn("live_text adapters must equal policy.SUPPORTED_LIVE", "; ".join(validate_registry(registry)))
        with patch("reachweaver.capabilities.SUPPORTED_LIVE", frozenset(SUPPORTED_LIVE | {"instagram"})):
            self.assertEqual(validate_registry(registry), [])
            entry = require_capability("instagram", "image", "publish", registry=registry)
            self.assertEqual(entry["reachweaver_adapter"], "live_media")
            for fmt in ("text", "video", "carousel"):
                with self.subTest(fmt=fmt), self.assertRaises(ValidationError):
                    require_capability("instagram", fmt, "publish", registry=registry)
        self.assertEqual(json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8")), load_registry())

    def test_registry_rejects_bad_live_media_entries(self):
        live = frozenset(SUPPORTED_LIVE | {"instagram"})

        def variant(mutate):
            registry = flipped_registry()
            mutate(next(e for e in registry["platforms"] if e["id"] == "instagram"), registry)
            return "; ".join(validate_registry(registry))
        cases = (
            (lambda e, r: e.update(live_media_formats=["video"]), "live_media_formats must list"),
            (lambda e, r: e.update(live_media_formats=[]), "live_media_formats must list"),
            (lambda e, r: e.pop("live_media_formats"), "live_media_formats must list"),
            (lambda e, r: e["formats"].update(image="api_gated"), "needs a live adapter and api_direct"),
            (lambda e, r: e["rate_limits"].pop("facts"), "live_media adapter needs sourced rate_limits.facts"),
            (lambda e, r: e.update(status="requires-review"), "must match a live adapter"),
            (lambda e, r: next(x for x in r["platforms"] if x["id"] == "bluesky").update(
                live_media_formats=["image"]), "bluesky: live_media_formats must list"),
        )
        with patch("reachweaver.capabilities.SUPPORTED_LIVE", live):
            for mutate, needle in cases:
                with self.subTest(needle=needle):
                    self.assertIn(needle, variant(mutate))


# --------------------------------------------------------------------------- config and content

class ConfigAndCredentialTests(Base):
    def load(self, *entries):
        path = self.tmp / "accounts.json"
        path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
        return load_accounts(path)

    def test_valid_account_normalized(self):
        loaded = self.load(account(handle="@IgApiTestUser"))["ig-me"]
        self.assertEqual((loaded["handle"], loaded["ig_user_id"]), ("IgApiTestUser", IG_ID))

    def test_invalid_configs_rejected(self):
        no_handle = account()
        del no_handle["handle"]
        for bad, needle in ((no_handle, "handle is required"),
                            (account(ig_user_id="abc"), "numeric"),
                            (account(ig_user_id=17841400000000001), "numeric"),
                            (account(handle="bad handle"), "letters, digits"),
                            (account(user_tags="x"), "unsupported fields user_tags"),
                            (account(location_id="1"), "unsupported fields location_id"),
                            (account(authorization_expires_at="soon"), "ISO-8601"),
                            (account(access_token="nope"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)
        # Without ig_user_id the account is manual-only metadata and loads as before, but the
        # adapter preflight still refuses it before any request.
        manual = {"id": "ig-manual", "platform": "instagram", "env_prefix": PREFIX, "display_name": "Me"}
        self.assertEqual(self.load(manual)["ig-manual"], manual)
        with self.assertRaisesRegex(ValidationError, "instagram_unsupported_fields: display_name"):
            instagram.preflight(manual)

    def test_credential_suffix_registered_for_overlap_rule_and_hygiene(self):
        self.assertEqual(CREDENTIAL_SUFFIXES["instagram"], ("_ACCESS_TOKEN",))
        self.assertEqual(expected_variables(account()), (PREFIX + "_ACCESS_TOKEN",))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(account()), [PREFIX + "_ACCESS_TOKEN"])
        with self.assertRaisesRegex(ValidationError, "overlapping"):
            self.load(account(env_prefix="REACHWEAVER_A"),
                      {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_A_ACCESS",
                       "server": "https://mastodon.social"})
        report = audit_environment([account()], {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN})
        self.assertEqual(report["accounts"][0]["set"], [PREFIX + "_ACCESS_TOKEN"])
        self.assertNotIn(FAKE_TOKEN, json.dumps(report))

    def test_doctor_notes_offline(self):
        notes = instagram.doctor_notes(account(authorization_expires_at="2026-10-10T00:00:00+00:00"), now=NOW)
        self.assertIn("blocked by the capability registry", notes[0])
        self.assertIn("WARNING: instagram_token_expiring", notes[1])
        self.assertEqual(instagram.token_status({}, NOW)[0], "unknown")
        expired = instagram.doctor_notes(account(authorization_expires_at="2026-10-01T00:00:00+00:00"), now=NOW)
        self.assertIn("ERROR: instagram_token_expired", expired[1])


class ContentRuleTests(unittest.TestCase):
    def test_official_caption_limits(self):
        self.assertEqual(instagram.content_problems("a" * 2200), [])
        self.assertIn("2201 characters", instagram.content_problems("a" * 2201)[0])
        self.assertEqual(instagram.content_problems(" ".join(f"#t{i}" for i in range(30))), [])
        self.assertIn("31 hashtags", instagram.content_problems(" ".join(f"#t{i}" for i in range(31)))[0])
        self.assertEqual(instagram.content_problems(" ".join(f"@u{i}" for i in range(20))), [])
        self.assertIn("21 @ tags", instagram.content_problems(" ".join(f"@u{i}" for i in range(21)))[0])

    def test_validate_post_tightens_never_loosens(self):
        self.assertEqual(validate_post("instagram", CAPTION), [])
        self.assertTrue(any("31 hashtags" in p for p in validate_post("instagram", " ".join(f"#t{i}" for i in range(31)))))
        self.assertTrue(any("conservative instagram limit" in p for p in validate_post("instagram", "a" * 2101)))


# --------------------------------------------------------------------------- remote media

class RemoteMediaTests(Base):
    def test_url_rules(self):
        self.assertEqual(remote_media.validate_image_url(IMAGE_URL), IMAGE_URL)
        self.assertEqual(remote_media.validate_image_url("https://IMG.example.org:443/a.jpg"),
                         "https://img.example.org/a.jpg")
        for bad, needle in (("http://img.example.org/a.jpg", "https"),
                            ("https://img.example.org/a.jpg?sig=secret", "query string"),
                            ("https://img.example.org/a.jpg#x", "query string"),
                            ("https://user:pw@img.example.org/a.jpg", "credentials"),
                            ("https://img.example.org:8443/a.jpg", "port 443"),
                            ("https://10.0.0.5/a.jpg", "IP-address"),
                            ("https://localhost/a.jpg", "Local network"),
                            ("https://printer.local/a.jpg", "Local network"),
                            ("https://img.example.org/ä.jpg", "printable ASCII"),
                            ("https://img.example.org/a b.jpg", "printable ASCII"),
                            ("https://img.example.org/", "file path"),
                            ("https://img.example.org/a/../b.jpg", "file path"),
                            ("", "printable ASCII"), (None, "printable ASCII")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValidationError, needle):
                remote_media.validate_image_url(bad)

    def test_download_is_read_only_bounded_and_credential_free(self):
        fake = FakeMeta()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            self.assertEqual(remote_media.download_image(IMAGE_URL), IMAGE)
        req = fake.request("image")
        self.assertEqual(req.get_method(), "GET")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)
        self.assertIsNone(req.data)

    def test_download_refusals(self):
        for host_ips, needle in ((["10.0.0.7"], "image_url_not_public"), (["127.0.0.1"], "image_url_not_public"),
                                 (["93.184.215.14", "192.168.1.2"], "image_url_not_public"),
                                 ([], "image_url_unresolvable")):
            with self.subTest(ips=host_ips), patch("reachweaver.remote_media._resolve", lambda h, i=host_ips: i), \
                    patch("reachweaver.publishers._send_http") as send, \
                    self.assertRaisesRegex(remote_media.ImageFetchError, needle):
                remote_media.download_image(IMAGE_URL)
            send.assert_not_called()
        for code, needle in ((302, "image_url_redirect_refused"), (404, "^image_url_http_404$")):
            with self.subTest(code=code), patch("reachweaver.publishers._send_http",
                                                side_effect=http_error(IMAGE_URL, code)), \
                    self.assertRaisesRegex(remote_media.ImageFetchError, needle) as ctx:
                remote_media.download_image(IMAGE_URL)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        big = FakeResponse(200, b"\xff\xd8\xff" + b"0" * 20, {"Content-Length": "9000000"})
        with patch("reachweaver.publishers._send_http", return_value=big), \
                self.assertRaisesRegex(remote_media.ImageFetchError, "image_too_large"):
            remote_media.download_image(IMAGE_URL)
        with patch("reachweaver.publishers._send_http", return_value=FakeResponse(200, b"x" * 101)), \
                self.assertRaisesRegex(remote_media.ImageFetchError, "image_too_large"):
            remote_media.download_image(IMAGE_URL, max_bytes=100)
        with patch("reachweaver.publishers._send_http", side_effect=TimeoutError()), \
                self.assertRaisesRegex(remote_media.ImageFetchError, "^image_url_unreachable$"):
            remote_media.download_image(IMAGE_URL)
        truncated = FakeResponse(200, IMAGE)
        truncated.read = lambda limit=-1: (_ for _ in ()).throw(http.client.IncompleteRead(b""))  # type: ignore[method-assign]
        with patch("reachweaver.publishers._send_http", return_value=truncated), \
                self.assertRaisesRegex(remote_media.ImageFetchError, "^image_url_unreachable$"):
            remote_media.download_image(IMAGE_URL)

    def test_image_rules(self):
        self.assertEqual(remote_media.inspect_image(jpeg(800, 1000)), ("image/jpeg", 800, 1000))  # 4:5
        self.assertEqual(remote_media.inspect_image(jpeg(1910, 1000))[1:], (1910, 1000))  # 1.91:1
        for data, needle in ((jpeg(799, 1000), "aspect ratio"), (jpeg(1911, 1000), "aspect ratio"),
                             (b"\x89PNG\r\n\x1a\n" + b"0" * 30, "JPEG"),
                             (b"\xff\xd8\xff\xd9", "dimensions")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                remote_media.inspect_image(data)

    def test_record_round_trip_and_strict_decode(self):
        built = remote_media.build_remote_attachment(IMAGE_URL, f"  {ALT}  ", fetch=lambda url: IMAGE)
        self.assertEqual(built, record())
        encoded = remote_media.encode_media([built])
        self.assertEqual(remote_media.decode_media(encoded), [built])
        self.assertEqual(remote_media.bound_view([built]),
                         [{"sha256": IMAGE_SHA, "mime": "image/jpeg", "alt": ALT, "focus": None, "url": IMAGE_URL}])
        for bad in (record(url="https://img.example.org/a.jpg?t=1"), record(mime="image/png"),
                    record(focus="0,0"), record(sha256="00"), {**record(), "path": "/tmp/x"},
                    record(bytes=0), record(alt=" ")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                remote_media.decode_media(json.dumps([bad]))
        for bad_text in ("[]", "{}", "not json"):
            with self.subTest(text=bad_text), self.assertRaises(ValidationError):
                remote_media.decode_media(bad_text)

    def test_alt_text_rules(self):
        for bad, needle in (("", "required"), ("image", "placeholder"), ("a" * 1001, "limit is 1000"),
                            ("ok\x07", "control"), ("ok‮", "bidirectional")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                remote_media.validate_alt(bad)

    def test_fetch_approved_bytes_refuses_changed_image(self):
        self.assertEqual(remote_media.fetch_approved_bytes(record(), fetch=lambda u: IMAGE), IMAGE)
        for served in (jpeg(1080, 1080, b"\xff\xfe\x00\x04hi"), jpeg(1080, 1350), b"not an image"):
            with self.subTest(served=served[:8]), self.assertRaisesRegex(ValidationError, "^" + BINDING_MEDIA_CHANGED):
                remote_media.fetch_approved_bytes(record(), fetch=lambda u, s=served: s)


# --------------------------------------------------------------------------- binding and CLI (bundled registry)

class BindingTests(CliMixin, Base):
    def setUp(self):
        super().setUp()
        self.setup_cli()

    def test_text_only_payload_is_unchanged_v1(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "bluesky", "Hello")
            row = get_post(con, pid)
            self.assertEqual(approval_payload(row)["binding"], APPROVAL_BINDING_TAG)
            self.assertNotIn("media", approval_payload(row))
            legacy = {k: row[k] for k in row.keys() if k != "media_json"}  # a pre-#20 row
            self.assertEqual(approval_payload_sha256(row), approval_payload_sha256(legacy))

    def test_media1_payload_binds_bytes_and_url_and_tamper_blocks_claim(self):
        pid = self.approved_with_image(FakeMeta())
        row = self.row(pid)
        payload = approval_payload(row)
        self.assertEqual(payload["binding"], "reachweaver.storage.approval/media-1")
        self.assertEqual(payload["media"], remote_media.bound_view([record()]))
        for column_change in ({"url": "https://img.example.org/other.jpg"}, {"sha256": "0" * 64}, {"alt": "Other"}):
            with self.subTest(change=column_change), connect(str(self.db)) as con:
                original = row["media_json"]
                con.execute("UPDATE posts SET media_json=? WHERE id=?",
                            (remote_media.encode_media([record(**column_change)]), pid))
                with self.assertRaisesRegex(ValidationError, "approval_hash_mismatch_reapproval_required"):
                    claim_sending(con, pid, max_per_24h=1)
                con.execute("UPDATE posts SET media_json=?, last_error=NULL WHERE id=?", (original, pid))
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_media_commands_offline_rules(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "instagram", CAPTION)
            other = add_post(con, "ig-me", "bluesky", "Text")
        with patch("reachweaver.publishers._send_http") as send:
            status, _, err = self.cli("media-attach", pid, "--url", IMAGE_URL, "--alt", ALT)
            self.assertEqual(status, 2)
            self.assertIn("requires explicit --online", err)
            status, _, err = self.cli("media-attach", other, "--url", IMAGE_URL, "--alt", ALT, "--online")
            self.assertEqual(status, 2)
            self.assertIn("only supported for Mastodon drafts (local --file) and Instagram drafts (owner-hosted --url)", err)
        send.assert_not_called()
        fake = FakeMeta()
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("media-attach", pid, "--url", IMAGE_URL, "--alt", ALT, "--online")
            self.assertEqual(status, 0, out + err)
            status, _, err = self.cli("media-attach", pid, "--url", IMAGE_URL, "--alt", ALT, "--online")
            self.assertEqual(status, 2)
            self.assertIn("exactly one image", err)
        self.assertEqual(fake.routes, ["image", "image"])  # read-only GETs of the owner's host only
        self.assertIn(f"sha256 {IMAGE_SHA[:12]}", out)
        status, out, _ = self.cli("show", pid)
        shown = json.loads(out)
        self.assertEqual(shown["media"]["attachments"][0]["sha256"], IMAGE_SHA)
        self.assertIn("refused", shown["media"]["live_image_publish"])
        self.assertEqual(self.cli("check", pid)[0], 0)
        status, out, _ = self.cli("approve", pid)
        self.assertEqual(status, 0)
        self.assertIn("media=1 (sha256, mime, alt, focus, url each)", out)
        status, _, err = self.cli("media-detach", pid)
        self.assertEqual(status, 2)
        self.assertIn("unapprove", err)
        self.cli("unapprove", pid)
        self.assertEqual(self.cli("media-detach", pid)[0], 0)
        self.assertEqual(post_media(self.row(pid)), [])
        with connect(str(self.db)) as con:
            events = [r[0] for r in con.execute("SELECT detail FROM event_log WHERE action='media_set'")]
        self.assertTrue(all(IMAGE_URL not in e and ALT not in e for e in events))

    def test_optout_handle_in_alt_text_refused(self):
        optout = self.tmp / "optout.json"
        optout.write_text(json.dumps({"version": 1, "entries": [{"handle": "@someone", "platform": "instagram"}]}),
                          encoding="utf-8")
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "instagram", CAPTION)
        with patch("reachweaver.publishers._send_http", side_effect=FakeMeta()):
            status, _, err = self.cli("media-attach", pid, "--url", IMAGE_URL, "--alt", "Photo with @someone",
                                      "--online", "--optout", str(optout))
        self.assertEqual(status, 2, err)
        self.assertIn("Attachment refused", err)
        with connect(str(self.db)) as con:  # a record that slipped in another way is caught at approval
            set_post_media(con, pid, [record(alt="Photo with @someone")])
            self.assertIn("@someone", published_text(get_post(con, pid)))
        status, _, err = self.cli("approve", pid, "--optout", str(optout))
        self.assertEqual(status, 2)
        self.assertIn("Refusing to approve", err)

    def test_malformed_record_is_never_approved(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "instagram", CAPTION)
            con.execute("UPDATE posts SET media_json='[{\"url\":1}]' WHERE id=?", (pid,))
        status, _, err = self.cli("approve", pid)
        self.assertEqual(status, 2)
        self.assertIn("media", err)
        self.assertEqual(self.row(pid)["status"], "draft")


# --------------------------------------------------------------------------- flipped: library

class FlippedLibraryTests(FlippedRegistry):
    def run_publish(self, fake, acct=None, media=None, paused=None):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            return publish(acct or account(), CAPTION, "p1", media=[record()] if media is None else media,
                           paused=paused)

    def test_happy_path_requests_and_receipt(self):
        fake = FakeMeta()
        receipt = self.run_publish(fake)
        self.assertEqual(fake.routes, ["me", "quota", "image", "create", "status", "publish", "media"])
        self.assertEqual(tuple(receipt), (MEDIA_ID, "https://www.instagram.com/p/AbC123xyz/"))
        self.assertEqual(receipt.extra, {"container_id": CONTAINER_ID, "image_digest": IMAGE_SHA[:16]})
        # Exactly the approved caption, URL and alt text; nothing else (no tags, location, collaborators).
        self.assertEqual(fake.json_body("create"), {"image_url": IMAGE_URL, "caption": CAPTION, "alt_text": ALT})
        self.assertEqual(fake.json_body("publish"), {"creation_id": CONTAINER_ID})
        for route in ("create", "publish"):
            req = fake.request(route)
            self.assertEqual(req.get_header("Authorization"), "Bearer " + FAKE_TOKEN)
            self.assertNotIn(FAKE_TOKEN, req.full_url)
        self.assertEqual(fake.query("me")["fields"], "user_id,username,account_type")
        self.assertEqual(fake.query("quota")["fields"], "quota_usage,config")
        self.assertEqual(fake.query("status")["fields"], "status_code")
        self.assertNotIn("authorization", {k.lower() for k, _ in fake.request("image").header_items()})
        self.assertEqual(self.sleeps, [])
        self.assertTrue(write_started())

    def test_image_changed_or_unreachable_before_container_is_refused_pre_write(self):
        for images, errors, needle in (((jpeg(1080, 1080, b"\xff\xfe\x00\x03x"),), {}, BINDING_MEDIA_CHANGED),
                                       ((IMAGE,), {"image": 404}, "image_url_http_404")):
            reset_write_marker()
            fake = FakeMeta(images=images, errors=errors)
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.run_publish(fake)
            self.assertNotIn("create", fake.routes)
            self.assertFalse(write_started())

    def test_in_progress_polls_once_per_minute_then_publishes(self):
        fake = FakeMeta(statuses=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
        self.assertEqual(self.run_publish(fake)[0], MEDIA_ID)
        self.assertEqual(self.sleeps, [60, 60])

    def test_container_never_ready_is_uncertain_after_five_checks(self):
        fake = FakeMeta(statuses=("IN_PROGRESS",))
        with self.assertRaisesRegex(PublishError, "^instagram_container_not_ready_not_public_uncertain$"):
            self.run_publish(fake)
        self.assertEqual(fake.routes.count("status"), 5)
        self.assertNotIn("publish", fake.routes)

    def test_container_error_or_already_published_is_uncertain(self):
        for status, code in (("ERROR", "instagram_container_error_not_public_uncertain"),
                             ("EXPIRED", "instagram_container_expired_not_public_uncertain"),
                             ("PUBLISHED", "instagram_container_already_published_uncertain"),
                             ("WEIRD", "instagram_container_status_unrecognized_uncertain")):
            with self.subTest(status=status), self.assertRaisesRegex(PublishError, "^" + code + "$"):
                self.run_publish(FakeMeta(statuses=(status,)))

    def test_emergency_pause_stops_before_media_publish(self):
        fake = FakeMeta(statuses=("IN_PROGRESS", "FINISHED"))
        flags = iter([False, True])  # not paused before the write; paused during the wait
        with self.assertRaisesRegex(PublishError, "instagram_paused_before_publish_not_public_uncertain"):
            self.run_publish(fake, paused=lambda: next(flags))
        self.assertIn("create", fake.routes)
        self.assertNotIn("publish", fake.routes)
        reset_write_marker()
        fake = FakeMeta()
        with self.assertRaisesRegex(ValidationError, "emergency pause"):
            self.run_publish(fake, paused=lambda: True)
        self.assertNotIn("create", fake.routes)
        self.assertFalse(write_started())

    def test_wrong_account_or_personal_account_refused_before_any_write(self):
        for me, needle in (({"user_id": "1234", "username": HANDLE, "account_type": "BUSINESS"}, "identity_mismatch"),
                           ({"user_id": IG_ID, "username": "someone.else", "account_type": "BUSINESS"},
                            "identity_mismatch"),
                           ({"user_id": IG_ID, "username": HANDLE, "account_type": "PERSONAL"},
                            "not_professional"),
                           ({"user_id": IG_ID, "username": HANDLE}, "not_professional"),
                           ({"username": HANDLE, "account_type": "BUSINESS"}, "missing_identity")):
            fake = FakeMeta(me=me)
            with self.subTest(me=me), self.assertRaisesRegex((ValidationError, PublishError), needle):
                self.run_publish(fake)
            self.assertNotIn("create", fake.routes)
            self.assertNotIn("image", fake.routes)
            self.assertFalse(write_started())
        self.assertEqual(self.run_publish(FakeMeta(me={"user_id": int(IG_ID), "username": HANDLE.upper(),
                                                       "account_type": "MEDIA_CREATOR"}))[0], MEDIA_ID)

    def test_exhausted_or_unreadable_quota_refused_before_any_write(self):
        fake = FakeMeta(usage=50)
        with self.assertRaisesRegex(ValidationError, "instagram_publishing_quota_exhausted: 50/50"):
            self.run_publish(fake)
        self.assertEqual(fake.routes, ["me", "quota"])
        fake = FakeMeta()
        fake.usage = "3"  # not an integer
        with self.assertRaisesRegex(PublishError, "instagram_quota_unreadable"):
            self.run_publish(fake)
        self.assertFalse(write_started())

    def test_preflight_http_errors_are_pre_write_and_actionable(self):
        for route, code, needle in (("me", 400, "instagram_http_400: request refused"),
                                    ("me", 401, "instagram_http_401: token invalid"),
                                    ("me", 429, "^instagram_http_429$"), ("me", 500, "^instagram_http_500$"),
                                    ("quota", 403, "lacks instagram_business_content_publish")):
            fake = FakeMeta(errors={route: code})
            with self.subTest(route=route, code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertFalse(write_started())
            self.assertNotIn("create", fake.routes)

    def test_post_write_http_errors_are_uncertain_and_never_retried(self):
        for route, code, needle in (("create", 400, "^instagram_create_http_400_uncertain: container refused"),
                                    ("create", 401, "^instagram_create_http_401_uncertain$"),
                                    ("create", 429, "^instagram_create_http_429_uncertain: rate limited"),
                                    ("create", 500, "^instagram_create_http_500_uncertain$"),
                                    ("status", 500, "^instagram_status_http_500_uncertain$"),
                                    ("publish", 400, "^instagram_publish_http_400_uncertain$"),
                                    ("publish", 429, "^instagram_publish_http_429_uncertain"),
                                    ("publish", 500, "^instagram_publish_http_500_uncertain$")):
            reset_write_marker()
            fake = FakeMeta(errors={route: code})
            with self.subTest(route=route, code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertTrue(write_started())
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertEqual(fake.routes.count("create"), 1)

    def test_network_timeout_after_write_is_uncertain(self):
        fake = FakeMeta()

        def timeout(req):
            if req.get_method() == "POST":
                raise TimeoutError()
            return fake(req)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=timeout), \
                self.assertRaisesRegex(PublishError, "^instagram_create_network_or_timeout_uncertain$"):
            publish(account(), CAPTION, "p1", media=[record()])

    def test_receipt_contradicting_the_send_is_uncertain(self):
        for media in ({"id": MEDIA_ID, "username": "someone.else"}, {"id": "1"},
                      {"id": MEDIA_ID, "media_type": "VIDEO"}):
            with self.subTest(media=media), self.assertRaisesRegex(PublishError, "receipt_.*_uncertain"):
                self.run_publish(FakeMeta(media=media))

    def test_permalink_unavailable_or_unrecognized_keeps_media_id(self):
        receipt = self.run_publish(FakeMeta(errors={"media": 500}))
        self.assertEqual(tuple(receipt), (MEDIA_ID, None))
        self.assertEqual(receipt.extra["permalink"], "unavailable")
        for link in ("https://evil.example/p/AbC/", "https://www.instagram.com/p/AbC/?utm=1", "http://instagram.com/p/x/"):
            receipt = self.run_publish(FakeMeta(media={"id": MEDIA_ID, "permalink": link}))
            self.assertEqual((tuple(receipt), receipt.extra["permalink"]), ((MEDIA_ID, None), "unrecognized"))

    def test_missing_ids_are_uncertain(self):
        fake = FakeMeta()

        def no_media_id(req):
            response = fake(req)
            return FakeResponse(200, b"{}") if fake.routes[-1] == "publish" else response
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=no_media_id), \
                self.assertRaisesRegex(PublishError, "^instagram_publish_missing_media_id_uncertain$"):
            publish(account(), CAPTION, "p1", media=[record()])

    def test_offline_refusals_before_network(self):
        cases = ((account(authorization_expires_at="2026-10-09T11:00:00+00:00"), [record()], "instagram_token_expired"),
                 (account(ig_user_id="x"), [record()], "numeric"),
                 (account(), [], "instagram_image_required"),
                 (account(), [record(), record()], "instagram_image_required"))
        for acct, media, needle in cases:
            with self.subTest(needle=needle), patch.dict(os.environ, ENV), \
                    patch("reachweaver.publishers._send_http") as send, self.assertRaisesRegex(ValidationError, needle):
                instagram.publish_image_post(acct, CAPTION, "p1", media)
            send.assert_not_called()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            with self.assertRaisesRegex(ValidationError, "instagram_image_required"):
                publish(account(), CAPTION, "p1", media=[])
            with self.assertRaisesRegex(ValidationError, "No ReachWeaver live media adapter for bluesky"):
                publish({"id": "b", "platform": "bluesky", "env_prefix": "REACHWEAVER_B", "handle": "x.bsky.social"},
                        "Hello", "p1", media=[record()])
            with self.assertRaisesRegex(ValidationError, "31 hashtags"):
                instagram.publish_image_post(account(), " ".join(f"#t{i}" for i in range(31)), "p1", [record()])
        send.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send, \
                self.assertRaisesRegex(ValidationError, "missing_credentials: " + PREFIX + "_ACCESS_TOKEN"):
            check_connection(account())
        send.assert_not_called()
        self.assertFalse(write_started())

    def test_connection_reports_verified_write_permission(self):
        fake = FakeMeta()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            result = check_connection(account())
        self.assertEqual((result.platform, result.identity, result.write_permission),
                         ("instagram", "verified", "verified"))
        self.assertEqual(fake.routes, ["me", "quota"])
        self.assertNotIn(FAKE_TOKEN, repr(result))


# --------------------------------------------------------------------------- flipped: CLI

class FlippedCliTests(CliMixin, FlippedRegistry):
    def setUp(self):
        super().setUp()
        self.setup_cli()

    def live(self, fake, pid):
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            return self.cli("publish", "--live", "--post-id", pid)

    def test_end_to_end_receipt_stored(self):
        pid = self.approved_with_image(FakeMeta())
        fake = FakeMeta()
        status, out, err = self.live(fake, pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("PUBLISHED " + pid, out)
        self.assertNotIn(FAKE_TOKEN, out + err)
        # Pre-claim image check, identity + quota, then the adapter re-checks the image right before the POST.
        self.assertEqual(fake.routes, ["image", "me", "quota", "image", "create", "status", "publish", "media"])
        row = self.row(pid)
        self.assertEqual((row["status"], row["remote_id"]), ("published", MEDIA_ID))
        with connect(str(self.db)) as con:
            events = [r[0] for r in con.execute("SELECT detail FROM event_log WHERE post_id=?", (pid,))]
        self.assertTrue(any(f"container_id={CONTAINER_ID}" in (e or "") and f"image_digest={IMAGE_SHA[:16]}" in e
                            for e in events))

    def test_image_changed_after_approval_blocks_before_claim(self):
        pid = self.approved_with_image(FakeMeta())
        fake = FakeMeta(images=(jpeg(1080, 1080, b"\xff\xfe\x00\x03x"),))
        status, out, _ = self.live(fake, pid)
        self.assertEqual(status, 2)
        self.assertIn(BINDING_MEDIA_CHANGED, out)
        self.assertEqual(fake.routes, ["image"])  # no Graph request, no credential use
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"]), ("approved", BINDING_MEDIA_CHANGED))
        # Restoring the approved bytes at the URL unblocks the send without re-approval.
        self.assertEqual(self.live(FakeMeta(), pid)[0], 0)

    def test_image_swapped_between_checks_is_certain_not_sent(self):
        pid = self.approved_with_image(FakeMeta())
        fake = FakeMeta(images=(IMAGE, jpeg(1080, 1080, b"\xff\xfe\x00\x03x")))
        status, out, _ = self.live(fake, pid)
        self.assertEqual(status, 3)
        self.assertIn("[certain_not_sent]", out)
        self.assertNotIn("create", fake.routes)
        self.assertEqual(self.row(pid)["status"], "needs_review")

    def test_unreachable_image_skips_without_claim(self):
        pid = self.approved_with_image(FakeMeta())
        fake = FakeMeta(errors={"image": 503})
        status, out, _ = self.live(fake, pid)
        self.assertEqual(status, 2)
        self.assertIn("image URL check failed (image_url_http_503)", out)
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_caption_only_instagram_post_is_never_claimed(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "ig-me", "instagram", CAPTION)
            transition(con, pid, "approve")
        fake = FakeMeta()
        status, out, _ = self.live(fake, pid)
        self.assertEqual(status, 2)
        self.assertIn("instagram_image_required", out)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.row(pid)["status"], "approved")

    def test_unapproved_draft_dry_run_and_missing_live_gate_send_nothing(self):
        pid = self.draft_with_image(FakeMeta())
        fake = FakeMeta()
        self.assertEqual(self.live(fake, pid)[0], 2)
        self.assertEqual(self.row(pid)["status"], "draft")
        self.cli("approve", pid)
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish")
            self.assertEqual(status, 0)
            self.assertIn(f"media: 1 image(s) image/jpeg sha256 {IMAGE_SHA[:12]}", out)
            with patch.dict(os.environ, ENV, clear=True):
                status, _, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        self.assertEqual(fake.routes, [])

    def test_backend_account_never_sends_media(self):
        # Postiz refuses Instagram accounts outright; a Bluesky backend row with a smuggled image
        # record shows the backend path itself refuses media before any claim.
        self.setup_cli([{"id": "bsky-pz", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_PZ",
                         "handle": "example.bsky.social", "backend": "postiz",
                         "postiz_base_url": "https://postiz.example.org", "postiz_integration_id": "int-1"}])
        with connect(str(self.db)) as con:
            pid = add_post(con, "bsky-pz", "bluesky", "Hello")
            con.execute("UPDATE posts SET media_json=? WHERE id=?", (remote_media.encode_media([record()]), pid))
            transition(con, pid, "approve")
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send:
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("media_attachments_unsupported via a publishing backend", out, err)
        send.assert_not_called()

    def test_post_write_failure_goes_to_needs_review_and_is_never_resent(self):
        for route, code in (("create", 400), ("create", 429), ("publish", 500)):
            with self.subTest(route=route, code=code):
                self.db = self.tmp / f"{route}-{code}" / "outbox.sqlite"  # fresh queue: no min-gap carry-over
                pid = self.approved_with_image(FakeMeta())
                fake = FakeMeta(errors={route: code})
                status, out, _ = self.live(fake, pid)
                again, _, _ = self.live(fake, pid)
                self.assertEqual((status, again), (3, 2))
                self.assertIn(f"instagram_{route}_http_{code}_uncertain", out)
                self.assertIn("[ambiguous]", out)
                self.assertEqual(fake.routes.count("create"), 1)
                self.assertEqual(self.row(pid)["status"], "needs_review")
                with connect(str(self.db)) as con:
                    self.assertEqual(active_provider_cooldown(con, "ig-me") is not None, code == 429)

    def test_doctor_reports_draft_status_offline(self):
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertEqual(status, 0)
        self.assertIn("ig-me: instagram;", out)
        self.assertIn("blocked by the capability registry", out)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
