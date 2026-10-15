"""Threads text adapter draft (issue #19). Every HTTP exchange is mocked: no network, no live post.

Two halves:
* ``UnreachableTests``: with the bundled registry (Threads adapter ``none``, text ``api_gated``)
  every entry point refuses Threads before a credential read or request.
* ``Flipped*`` tests: a TEMPORARY copy of the registry with Threads flipped to ``live_text`` (and
  ``policy.SUPPORTED_LIVE`` patched to match) shows the whole mocked contract: happy path,
  400/401/403/429/500, wrong account, exhausted quota and needs_review after the write started.
  The issue #49 acceptance cases (successful post, rejected permission, rate limit, expired token,
  approved preview and matching receipt reconciled without duplicates) are mapped to test names in
  docs/THREADS.md, "Issue #49 acceptance evidence".
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from reachweaver import capabilities, threads
from reachweaver.accounts import (CREDENTIAL_SUFFIXES, env_prefix_conflicts, load_accounts,
                                  missing_credentials)
from reachweaver.capabilities import load_registry, require_capability, validate_registry
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.credential_hygiene import audit_environment, expected_variables
from reachweaver.policy import SUPPORTED_LIVE, ValidationError, validate_post
from reachweaver.publishers import PUBLISHERS, PublishError, reset_write_marker, write_started
from gate_support import publish  # noqa: E402
from reconcile_support import assert_reconcile_closes_needs_review  # noqa: E402
from reachweaver.storage import active_provider_cooldown, add_post, connect, get_post, transition

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
FAKE_TOKEN = "fake-threads-user-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_THREADS"
ENV = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}
LIVE_ENV = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
USER_ID, HANDLE = "1234567", "threadsapitestuser"
CONTAINER_ID, MEDIA_ID = "17890000000000001", "17890000000000002"
BASE = urlsplit(threads.API_BASE)


def account(**overrides):
    base = {"id": "th-me", "platform": "threads", "env_prefix": PREFIX, "handle": HANDLE,
            "threads_user_id": USER_ID, "authorization_expires_at": "2026-12-01T00:00:00+00:00"}
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


def http_error(url, code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    # The message and body carry the token: they must never reach an error string.
    return HTTPError(url, code, "Bearer " + FAKE_TOKEN, headers, io.BytesIO(FAKE_TOKEN.encode()))


class FakeThreads:
    """Routes mocked Graph requests by (method, path). ``errors`` maps a route name to an HTTP code."""

    ROUTES = {
        ("GET", "/v1.0/me"): "me",
        ("GET", f"/v1.0/{USER_ID}/threads_publishing_limit"): "quota",
        ("POST", f"/v1.0/{USER_ID}/threads"): "create",
        ("GET", f"/v1.0/{CONTAINER_ID}"): "status",
        ("POST", f"/v1.0/{USER_ID}/threads_publish"): "publish",
        ("GET", f"/v1.0/{MEDIA_ID}"): "media",
    }

    def __init__(self, *, me=None, usage=3, statuses=("FINISHED",), media=None, errors=None):
        self.requests, self.routes = [], []
        self.me = me or {"id": USER_ID, "username": HANDLE}
        self.usage = usage
        self.statuses = list(statuses)
        self.media = media if media is not None else {
            "id": MEDIA_ID, "permalink": f"https://www.threads.net/@{HANDLE}/post/abcdefg",
            "username": HANDLE, "owner": {"id": USER_ID}, "text": None}
        self.errors = errors or {}

    def __call__(self, req):
        parts = urlsplit(req.full_url)
        if (parts.scheme, parts.hostname) != (BASE.scheme, BASE.hostname):
            raise AssertionError("request to an unexpected host")
        route = self.ROUTES.get((req.get_method(), parts.path))
        if route is None:
            raise AssertionError("unexpected request")
        self.requests.append(req)
        self.routes.append(route)
        if route in self.errors:
            raise http_error(req.full_url, self.errors[route], retry_after=120)
        body = {
            "me": lambda: self.me,
            "quota": lambda: {"data": [{"quota_usage": self.usage,
                                        "config": {"quota_total": 250, "quota_duration": 86400}}]},
            "create": lambda: {"id": CONTAINER_ID},
            "status": lambda: {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0],
                               "id": CONTAINER_ID},
            "publish": lambda: {"id": MEDIA_ID},
            "media": lambda: self.media,
        }[route]()
        return FakeResponse(200, json.dumps(body).encode())

    def form(self, route):
        req = self.requests[self.routes.index(route)]
        return {k: v[0] for k, v in parse_qs(req.data.decode(), strict_parsing=True).items()}

    def query(self, route):
        req = self.requests[self.routes.index(route)]
        return {k: v[0] for k, v in parse_qs(urlsplit(req.full_url).query).items()}


def flipped_registry() -> dict:
    """The registry change the owner would make AFTER review and a live receipt (temp copy only)."""
    registry = copy.deepcopy(load_registry())
    entry = next(e for e in registry["platforms"] if e["id"] == "threads")
    entry.update({
        "status": "supported", "reachweaver_adapter": "live_text",
        "rate_limits": {
            "summary": "250 API-published posts per profile per 24-hour moving period; quota via "
                       "GET /{threads-user-id}/threads_publishing_limit",
            "source": "https://developers.facebook.com/documentation/threads/overview.md",
            "accessed": "2026-10-09",
            "facts": [{"scope": "profile", "action": "API-published posts (24-hour moving period)",
                       "limit": 250, "unit": "posts", "window_seconds": 86400}],
        },
    })
    entry["formats"]["text"] = "api_direct"
    return registry


class FlippedRegistry(unittest.TestCase):
    """Patches a temp registry copy and every SUPPORTED_LIVE binding; the bundled file is untouched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        path = self.tmp / "platform_registry.json"
        path.write_text(json.dumps(flipped_registry()), encoding="utf-8")
        live = frozenset(SUPPORTED_LIVE | {"threads"})
        for target, value in (("reachweaver.capabilities.REGISTRY_FILE", path),
                              ("reachweaver.policy.SUPPORTED_LIVE", live),
                              ("reachweaver.capabilities.SUPPORTED_LIVE", live),
                              ("reachweaver.publishers.SUPPORTED_LIVE", live),
                              ("reachweaver.connections.SUPPORTED_LIVE", live),
                              ("reachweaver.cli.SUPPORTED_LIVE", live),
                              ("reachweaver.threads._utc_now", lambda: NOW)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sleeps: list[float] = []
        patcher = patch("reachweaver.threads._sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_write_marker()


class UnreachableTests(unittest.TestCase):
    """Under the bundled registry the adapter cannot be reached."""

    def test_bundled_registry_keeps_threads_gated(self):
        entry = next(e for e in load_registry()["platforms"] if e["id"] == "threads")
        self.assertEqual((entry["reachweaver_adapter"], entry["formats"]["text"], entry["status"]),
                         ("none", "api_gated", "requires-review"))
        self.assertNotIn("threads", SUPPORTED_LIVE)
        self.assertIn("threads", PUBLISHERS)  # present, but publish() refuses before the lookup
        with self.assertRaisesRegex(ValidationError, "api_gated"):
            require_capability("threads", "text", "publish")

    def test_library_publish_and_connection_refused_before_any_request(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.threads._token") as token:
            with self.assertRaisesRegex(ValidationError, "not yet implemented for threads"):
                publish(account(), "Hello Threads", "p1")
            with self.assertRaisesRegex(ValidationError, "platform_has_no_authorized_live_connector"):
                check_connection(account())
        send.assert_not_called()
        token.assert_not_called()

    def test_cli_live_publish_skips_threads_and_keeps_post_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, config = Path(tmp) / "outbox.sqlite", Path(tmp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
            with connect(str(db)) as con:
                pid = add_post(con, "th-me", "threads", "Release notes are out: https://example.com")
                transition(con, pid, "approve")
            out = io.StringIO()
            with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                status = main(["--db", str(db), "--accounts", str(config), "publish", "--live", "--post-id", pid])
            self.assertEqual(status, 2)
            self.assertIn("requires manual publishing", out.getvalue())
            send.assert_not_called()
            with connect(str(db)) as con:
                self.assertEqual(get_post(con, pid)["status"], "approved")

    def test_flipped_copy_is_valid_only_with_matching_supported_live(self):
        registry = flipped_registry()
        self.assertIn("live_text adapters must equal policy.SUPPORTED_LIVE", "; ".join(validate_registry(registry)))
        with patch("reachweaver.capabilities.SUPPORTED_LIVE", frozenset(SUPPORTED_LIVE | {"threads"})):
            self.assertEqual(validate_registry(registry), [])
        self.assertEqual(json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8")), load_registry())


class ConfigAndCredentialTests(unittest.TestCase):
    def load(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
            return load_accounts(path)

    def test_valid_account_normalized(self):
        loaded = self.load(account(handle="@ThreadsApiTestUser"))["th-me"]
        self.assertEqual((loaded["handle"], loaded["threads_user_id"]), ("ThreadsApiTestUser", USER_ID))

    def test_invalid_configs_rejected(self):
        no_handle = account()
        del no_handle["handle"]
        no_id = account()
        del no_id["threads_user_id"]
        for bad, needle in ((no_handle, "handle is required"),
                            (account(threads_user_id="abc"), "numeric"),
                            (account(handle="bad handle"), "letters, digits"),
                            (account(reply_to_id="1"), "unsupported fields reply_to_id"),
                            (account(topic_tag="x"), "unsupported fields topic_tag"),
                            (account(link_attachment="https://example.com"), "unsupported fields link_attachment"),
                            (account(authorization_expires_at="soon"), "ISO-8601"),
                            (account(access_token="nope"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)
        # Without threads_user_id the account is manual-only metadata and loads as before,
        # but the adapter preflight still refuses it before any request.
        manual = {"id": "th-manual", "platform": "threads", "env_prefix": PREFIX, "display_name": "Me"}
        self.assertEqual(self.load(manual)["th-manual"], manual)
        with self.assertRaisesRegex(ValidationError, "threads_unsupported_fields: display_name"):
            threads.preflight(manual)
        with self.assertRaisesRegex(ValidationError, "threads_user_id is required"):
            threads.preflight(no_id)

    def test_credential_suffix_registered_for_overlap_rule_and_hygiene(self):
        self.assertEqual(CREDENTIAL_SUFFIXES["threads"], ("_ACCESS_TOKEN",))
        self.assertEqual(expected_variables(account()), (PREFIX + "_ACCESS_TOKEN",))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(account()), [PREFIX + "_ACCESS_TOKEN"])
        # REACHWEAVER_A + _ACCESS_TOKEN equals REACHWEAVER_A_ACCESS + _TOKEN (Mastodon).
        clash = [account(env_prefix="REACHWEAVER_A"),
                 {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_A_ACCESS"}]
        self.assertEqual(env_prefix_conflicts(clash)[0]["reason"], "overlapping_env_prefix")
        with self.assertRaisesRegex(ValidationError, "overlapping"):
            self.load(clash[0], {**clash[1], "server": "https://mastodon.social"})
        report = audit_environment([account()], {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN})
        self.assertEqual(report["accounts"][0]["set"], [PREFIX + "_ACCESS_TOKEN"])
        self.assertNotIn(FAKE_TOKEN, json.dumps(report))

    def test_doctor_notes_offline(self):
        notes = threads.doctor_notes(account(authorization_expires_at="2026-10-10T00:00:00+00:00"), now=NOW)
        self.assertIn("blocked by the capability registry", notes[0])
        self.assertIn("WARNING: threads_token_expiring", notes[1])
        self.assertIn("unknown", threads.token_status({}, NOW)[0])


class ContentRuleTests(unittest.TestCase):
    def test_official_text_and_link_limits(self):
        self.assertEqual(threads.content_problems("a" * 500), [])
        self.assertIn("501 characters", threads.content_problems("a" * 501)[0])
        self.assertIn("limit is 500", threads.content_problems("\U0001F600" * 126)[0])  # 4 bytes each
        six = " ".join(f"https://example.com/{i}" for i in range(6))
        self.assertIn("6 links", threads.content_problems(six)[0])

    def test_validate_post_tightens_never_loosens(self):
        self.assertEqual(validate_post("threads", "Short and fine"), [])
        self.assertTrue(validate_post("threads", "\U0001F600" * 126))
        self.assertTrue(any("conservative threads limit" in p for p in validate_post("threads", "a" * 451)))


class FlippedLibraryTests(FlippedRegistry):
    def run_publish(self, fake, acct=None, text="Release notes: https://example.com"):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            return publish(acct or account(), text, "p1")

    def test_happy_path_requests_and_receipt(self):
        fake = FakeThreads()
        receipt = self.run_publish(fake)
        self.assertEqual(fake.routes, ["me", "quota", "create", "status", "publish", "media"])
        self.assertEqual(tuple(receipt), (MEDIA_ID, f"https://www.threads.net/@{HANDLE}/post/abcdefg"))
        self.assertEqual(receipt.extra, {"container_id": CONTAINER_ID, "text_match": "unknown"})
        self.assertEqual(self.sleeps, [30])
        # Exactly the approved text and nothing else: no link_attachment, reply, tag or cross-share.
        self.assertEqual(fake.form("create"), {"media_type": "TEXT", "text": "Release notes: https://example.com",
                                               "access_token": FAKE_TOKEN})
        self.assertEqual(fake.form("publish"), {"creation_id": CONTAINER_ID, "access_token": FAKE_TOKEN})
        self.assertEqual(fake.query("me")["fields"], "id,username")
        self.assertEqual(fake.query("quota")["fields"], "quota_usage,config")
        self.assertTrue(write_started())

    def test_in_progress_container_polls_once_per_minute_then_publishes(self):
        fake = FakeThreads(statuses=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
        self.assertEqual(self.run_publish(fake)[0], MEDIA_ID)
        self.assertEqual(self.sleeps, [30, 60, 60])

    def test_container_never_ready_is_uncertain_after_five_checks(self):
        fake = FakeThreads(statuses=("IN_PROGRESS",))
        with self.assertRaisesRegex(PublishError, "^threads_container_not_ready_not_public_uncertain$"):
            self.run_publish(fake)
        self.assertEqual(fake.routes.count("status"), 5)
        self.assertNotIn("publish", fake.routes)

    def test_container_error_or_already_published_is_uncertain(self):
        for status, code in (("ERROR", "threads_container_error_not_public_uncertain"),
                             ("EXPIRED", "threads_container_expired_not_public_uncertain"),
                             ("PUBLISHED", "threads_container_already_published_uncertain"),
                             ("WEIRD", "threads_container_status_unrecognized_uncertain")):
            with self.subTest(status=status), self.assertRaisesRegex(PublishError, "^" + code + "$"):
                self.run_publish(FakeThreads(statuses=(status,)))

    def test_wrong_account_refused_before_any_write(self):
        for me in ({"id": "7654321", "username": HANDLE}, {"id": USER_ID, "username": "someone.else"},
                   {"id": USER_ID}):
            fake = FakeThreads(me=me)
            with self.subTest(me=me), self.assertRaises((ValidationError, PublishError)) as ctx:
                self.run_publish(fake)
            self.assertNotIn("create", fake.routes)
            self.assertFalse(write_started())
            if "username" in me:
                self.assertIn("identity_mismatch", str(ctx.exception))

    def test_exhausted_or_unreadable_quota_refused_before_any_write(self):
        fake = FakeThreads(usage=250)
        with self.assertRaisesRegex(ValidationError, "threads_publishing_quota_exhausted: 250/250"):
            self.run_publish(fake)
        self.assertEqual(fake.routes, ["me", "quota"])
        fake = FakeThreads()
        fake.usage = "3"  # not an integer
        with self.assertRaisesRegex(PublishError, "threads_quota_unreadable"):
            self.run_publish(fake)
        self.assertFalse(write_started())

    def test_preflight_http_errors_are_pre_write_and_actionable(self):
        for route, code, needle in (("me", 400, "threads_http_400: request refused"),
                                    ("me", 401, "threads_http_401: token invalid, revoked or expired"),
                                    ("me", 403, "threads_http_403: token lacks threads_basic"),
                                    ("me", 429, "^threads_http_429$"), ("me", 500, "^threads_http_500$"),
                                    ("quota", 400, "confirm the token carries threads_basic and "
                                                   "threads_content_publish"),
                                    ("quota", 403, "lacks threads_content_publish")):
            fake = FakeThreads(errors={route: code})
            with self.subTest(route=route, code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertFalse(write_started())
            self.assertNotIn("create", fake.routes)

    def test_post_write_http_errors_are_uncertain(self):
        for route, code, needle in (("create", 400, "^threads_create_http_400_uncertain: container refused"),
                                    ("create", 401, "^threads_create_http_401_uncertain$"),
                                    ("create", 403, "^threads_create_http_403_uncertain$"),
                                    ("create", 429, "^threads_create_http_429_uncertain: rate limited"),
                                    ("create", 500, "^threads_create_http_500_uncertain$"),
                                    ("status", 500, "^threads_status_http_500_uncertain$"),
                                    ("publish", 400, "^threads_publish_http_400_uncertain$"),
                                    ("publish", 429, "^threads_publish_http_429_uncertain"),
                                    ("publish", 500, "^threads_publish_http_500_uncertain$")):
            reset_write_marker()
            fake = FakeThreads(errors={route: code})
            with self.subTest(route=route, code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertTrue(write_started())
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertEqual(fake.routes.count("create"), 1)  # never retried

    def test_network_timeout_after_write_is_uncertain(self):
        def timeout(req):
            if req.get_method() == "POST":
                raise TimeoutError()
            return FakeThreads()(req)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=timeout), \
                self.assertRaisesRegex(PublishError, "^threads_create_network_or_timeout_uncertain$"):
            publish(account(), "Hello", "p1")

    def test_receipt_naming_another_profile_is_uncertain(self):
        for media in ({"id": MEDIA_ID, "username": "someone.else"},
                      {"id": MEDIA_ID, "owner": {"id": "999"}},
                      {"id": MEDIA_ID, "permalink": "https://www.threads.com/@someone.else/post/x1"},
                      {"id": "1"}):
            with self.subTest(media=media), self.assertRaisesRegex(PublishError, "receipt_.*_uncertain"):
                self.run_publish(FakeThreads(media=media))

    def test_permalink_unavailable_or_unrecognized_keeps_media_id(self):
        receipt = self.run_publish(FakeThreads(errors={"media": 500}))
        self.assertEqual(tuple(receipt), (MEDIA_ID, None))
        self.assertEqual(receipt.extra["permalink"], "unavailable")
        receipt = self.run_publish(FakeThreads(media={"id": MEDIA_ID, "permalink": "https://evil.example/@x/post/1",
                                                      "text": "Release notes: https://example.com"}))
        self.assertEqual(tuple(receipt), (MEDIA_ID, None))
        self.assertEqual((receipt.extra["permalink"], receipt.extra["text_match"]), ("unrecognized", "true"))

    def test_missing_ids_are_uncertain(self):
        fake = FakeThreads()
        original = fake.__call__

        def no_media_id(req):
            response = original(req)
            if fake.routes[-1] == "publish":
                return FakeResponse(200, b"{}")
            return response
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=no_media_id), \
                self.assertRaisesRegex(PublishError, "^threads_publish_missing_media_id_uncertain$"):
            publish(account(), "Hello", "p1")

    def test_offline_refusals_before_network(self):
        for bad, needle in ((account(authorization_expires_at="2026-10-09T11:00:00+00:00"), "threads_token_expired"),
                            (account(threads_user_id="x"), "numeric")):
            with self.subTest(needle=needle), patch.dict(os.environ, ENV), \
                    patch("reachweaver.publishers._send_http") as send:
                with self.assertRaisesRegex(ValidationError, needle):
                    check_connection(bad)
                with self.assertRaisesRegex(ValidationError, needle):
                    threads.publish_text_post(bad, "Hello", "p1")
                send.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send, \
                self.assertRaisesRegex(ValidationError, "missing_credentials: " + PREFIX + "_ACCESS_TOKEN"):
            check_connection(account())
        send.assert_not_called()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                self.assertRaisesRegex(ValidationError, "6 links"):
            threads.publish_text_post(account(), " ".join(f"https://e.com/{i}" for i in range(6)), "p1")
        send.assert_not_called()
        self.assertFalse(write_started())

    def test_connection_reports_verified_write_permission(self):
        fake = FakeThreads()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            result = check_connection(account())
        self.assertEqual((result.platform, result.identity, result.write_permission),
                         ("threads", "verified", "verified"))
        self.assertEqual(fake.routes, ["me", "quota"])
        self.assertNotIn(FAKE_TOKEN, repr(result))


class FlippedCliTests(FlippedRegistry):
    def setUp(self):
        super().setUp()
        self.db = self.tmp / "state" / "outbox.sqlite"
        self.config = self.tmp / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def approved(self, text="Release notes are out: https://example.com"):
        with connect(str(self.db)) as con:
            pid = add_post(con, "th-me", "threads", text)
            transition(con, pid, "approve")
        return pid

    def status_of(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)["status"]

    def test_unapproved_draft_never_sent(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "th-me", "threads", "Draft only")
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            self.assertEqual(self.cli("publish", "--live", "--post-id", pid)[0], 2)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.status_of(pid), "draft")

    def test_without_live_gate_no_request(self):
        pid = self.approved()
        with patch.dict(os.environ, ENV, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, _, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        send.assert_not_called()

    def test_end_to_end_receipt_stored(self):
        pid = self.approved()
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("PUBLISHED " + pid, out)
        self.assertNotIn(FAKE_TOKEN, out + err)
        self.assertEqual(fake.routes, ["me", "quota", "create", "status", "publish", "media"])
        with connect(str(self.db)) as con:
            row = get_post(con, pid)
            self.assertEqual((row["status"], row["remote_id"]), ("published", MEDIA_ID))
            self.assertEqual(row["remote_url"], f"https://www.threads.net/@{HANDLE}/post/abcdefg")
            events = [r[0] for r in con.execute("SELECT detail FROM event_log WHERE post_id=?", (pid,))]
        self.assertTrue(any(f"container_id={CONTAINER_ID}" in (e or "") for e in events))

    def test_wrong_account_blocked_before_claim(self):
        pid = self.approved()
        fake = FakeThreads(me={"id": "7654321", "username": "someone.else"})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("identity_mismatch", out)
        self.assertNotIn("create", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")

    def test_preflight_429_records_cooldown_and_keeps_post_approved(self):
        pid = self.approved()
        fake = FakeThreads(errors={"me": 429})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.ratelimit._sleep"):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("BLOCKED", out)
        self.assertNotIn("create", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")
        with connect(str(self.db)) as con:
            self.assertIsNotNone(active_provider_cooldown(con, "th-me"))

    def events(self, pid):
        with connect(str(self.db)) as con:
            return [r[0] for r in con.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id", (pid,))]

    def test_contract_rejected_permission_blocks_before_claim_and_keeps_post_approved(self):
        # Issue #49 contract case "rejected permission": a 403 on the identity read (no threads_basic)
        # or on the quota read (no threads_content_publish) stops before the claim and the write.
        for route, hint in (("me", "lacks threads_basic"), ("quota", "lacks threads_content_publish")):
            with self.subTest(route=route):
                pid = self.approved(f"Release notes {route}: https://example.com")
                fake = FakeThreads(errors={route: 403})
                with patch.dict(os.environ, LIVE_ENV), \
                        patch("reachweaver.publishers._send_http", side_effect=fake), \
                        patch("reachweaver.ratelimit._sleep"):
                    status, out, err = self.cli("publish", "--live", "--post-id", pid)
                self.assertEqual(status, 2, out + err)
                self.assertIn(f"BLOCKED {pid}", out)
                self.assertIn(hint, out)
                self.assertNotIn(FAKE_TOKEN, out + err)
                self.assertNotIn("create", fake.routes)
                self.assertEqual(self.status_of(pid), "approved")
                self.assertNotIn("send_claimed", self.events(pid))
                with connect(str(self.db)) as con:  # a permission refusal is not a rate limit
                    self.assertIsNone(active_provider_cooldown(con, "th-me"))

    def test_contract_expired_token_blocks_before_claim_and_keeps_post_approved(self):
        # Issue #49 contract case "expired token". Recorded expiry in the past: refused offline,
        # before the token is read or any request is made.
        self.config.write_text(json.dumps({"accounts": [account(
            authorization_expires_at="2026-10-09T11:00:00+00:00")]}), encoding="utf-8")
        pid = self.approved("Release notes expired: https://example.com")
        fake = FakeThreads()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.threads._token") as token:
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("threads_token_expired", out)
        self.assertEqual(fake.routes, [])
        token.assert_not_called()
        self.assertEqual(self.status_of(pid), "approved")
        self.assertNotIn("send_claimed", self.events(pid))
        # Expired or revoked at Meta although the recorded expiry is still ahead: the identity read
        # is refused (401) and the post stays approved; nothing is written.
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
        fake = FakeThreads(errors={"me": 401})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.ratelimit._sleep"):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("threads_http_401: token invalid, revoked or expired", out)
        self.assertNotIn(FAKE_TOKEN, out + err)
        self.assertNotIn("create", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")
        self.assertNotIn("send_claimed", self.events(pid))

    def test_approved_preview_matches_the_receipt_and_is_published_exactly_once(self):
        # Issue #49: the dry-run preview shows the approved text without a request; the live send
        # posts exactly that text and stores the matching receipt; re-runs never duplicate it.
        text = "Release notes are out: https://example.com"
        pid = self.approved(text)
        fake = FakeThreads(media={"id": MEDIA_ID, "permalink": f"https://www.threads.net/@{HANDLE}/post/abcdefg",
                                  "username": HANDLE, "owner": {"id": USER_ID}, "text": text})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, preview, _ = self.cli("publish", "--post-id", pid)
            self.assertEqual(status, 0)
            self.assertIn("DRY RUN: 1 due approved posts; no requests sent", preview)
            self.assertIn(f"{pid} threads th-me: {text!r}", preview)
            self.assertEqual(fake.routes, [])
            self.assertEqual(self.cli("publish", "--live", "--post-id", pid)[0], 0)
            self.assertEqual(fake.form("create")["text"], text)  # the previewed, approved text
            routes_after_send = list(fake.routes)
            again, _, _ = self.cli("publish", "--live", "--post-id", pid)
            sweep, _, _ = self.cli("publish", "--live")
        self.assertEqual((again, sweep), (2, 0))
        self.assertEqual(fake.routes, routes_after_send)  # no request at all after the receipt
        self.assertEqual((fake.routes.count("create"), fake.routes.count("publish")), (1, 1))
        with connect(str(self.db)) as con:
            row = get_post(con, pid)
            details = [r[0] or "" for r in con.execute(
                "SELECT detail FROM event_log WHERE post_id=? AND action='published'", (pid,))]
        self.assertEqual((row["status"], row["remote_id"]), ("published", MEDIA_ID))
        self.assertEqual(len(details), 1)
        self.assertIn("text_match=true", details[0])
        self.assertEqual(self.events(pid).count("send_claimed"), 1)

    def test_reconciled_receipt_cannot_be_applied_twice(self):
        # Issue #49: after a human records the matching remote receipt, a second reconcile (with the
        # same or another receipt) is refused, so one approved post never maps to two remote posts.
        pid = self.approved("Release notes twice: https://example.com")
        fake = FakeThreads(errors={"publish": 500})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            self.assertEqual(self.cli("publish", "--live", "--post-id", pid)[0], 3)
        url = f"https://www.threads.net/@{HANDLE}/post/abcdefg"
        status, out, err = self.cli("reconcile", pid, "--published", "--remote-id", MEDIA_ID, "--remote-url", url)
        self.assertEqual(status, 0, out + err)
        for remote_id in (MEDIA_ID, "17890000000000003"):
            with self.subTest(remote_id=remote_id):
                status, out, err = self.cli("reconcile", pid, "--published", "--remote-id", remote_id)
                self.assertNotEqual(status, 0)
                self.assertIn("only accepts sending/needs_review", out + err)
        status, out, err = self.cli("reconcile", pid, "--not-published")
        self.assertNotEqual(status, 0)
        with connect(str(self.db)) as con:
            row = get_post(con, pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]), ("published", MEDIA_ID, url))
        self.assertEqual(self.events(pid).count("reconciled_published"), 1)
        self.assertEqual(fake.routes.count("create"), 1)

    def test_post_write_failure_goes_to_needs_review_and_is_never_resent(self):
        for route, code in (("create", 400), ("create", 401), ("create", 429), ("publish", 500)):
            with self.subTest(route=route, code=code):
                self.db = self.tmp / f"{route}-{code}" / "outbox.sqlite"  # fresh queue: no min-gap carry-over
                pid = self.approved(f"Release notes {route} {code}: https://example.com")
                fake = FakeThreads(errors={route: code})
                with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
                    status, out, _ = self.cli("publish", "--live", "--post-id", pid)
                    again, _, _ = self.cli("publish", "--live", "--post-id", pid)
                self.assertEqual((status, again), (3, 2))
                self.assertIn(f"threads_{route}_http_{code}_uncertain", out)
                self.assertIn("[ambiguous]", out)
                self.assertEqual(fake.routes.count("create"), 1)
                self.assertEqual(self.status_of(pid), "needs_review")
                if code == 429:
                    with connect(str(self.db)) as con:
                        self.assertIsNotNone(active_provider_cooldown(con, "th-me"))

    def test_needs_review_is_closed_by_reconcile_and_never_resent(self):
        def needs_review_post(label):
            self.db = self.tmp / f"reconcile-{label}" / "outbox.sqlite"
            pid = self.approved(f"Release notes {label}: https://example.com")
            fake = FakeThreads(errors={"publish": 500})  # the container may or may not have been published
            with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
                status, out, _ = self.cli("publish", "--live", "--post-id", pid)
            self.assertEqual(status, 3, out)
            self.assertEqual(fake.routes.count("publish"), 1)
            return pid

        def live(*argv):
            fake = FakeThreads()
            with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
                status, _, _ = self.cli("publish", "--live", *argv)
            return status, fake.routes

        assert_reconcile_closes_needs_review(self, needs_review_post, live, remote_id=MEDIA_ID,
                                             remote_url=f"https://www.threads.net/@{HANDLE}/post/abcdefg")

    def test_doctor_reports_draft_status_offline(self):
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertEqual(status, 0)
        self.assertIn("th-me: threads;", out)
        self.assertIn("blocked by the capability registry", out)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
