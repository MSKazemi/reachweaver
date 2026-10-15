"""Facebook Page adapter draft (issue #21). Every HTTP exchange is mocked: no network, no live post.

Two halves:
* ``UnreachableTests``: with the bundled registry (Facebook adapter ``none``, text and link
  ``api_gated``) every entry point refuses Facebook before a credential read or request.
* ``Flipped*`` tests: a TEMPORARY copy of the registry with Facebook flipped to ``live_text`` (and
  ``policy.SUPPORTED_LIVE`` patched to match) shows the whole mocked contract: happy path,
  400/401/403/429/500, user token or wrong Page, and needs_review after the write started.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from reachweaver import capabilities, facebook
from reachweaver.accounts import (CREDENTIAL_SUFFIXES, env_prefix_conflicts, load_accounts,
                                  missing_credentials)
from reachweaver.capabilities import load_registry, require_capability, validate_registry
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.credential_hygiene import audit_environment, expected_variables
from reachweaver.policy import SUPPORTED_LIVE, ValidationError
from reachweaver.publishers import PUBLISHERS, PublishError, reset_write_marker, write_started
from gate_support import publish  # noqa: E402
from reachweaver.storage import active_provider_cooldown, add_post, connect, get_post, transition

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
FAKE_TOKEN = "fake-facebook-page-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_FBPAGE"
ENV = {PREFIX + "_PAGE_ACCESS_TOKEN": FAKE_TOKEN}
LIVE_ENV = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
PAGE_ID, PAGE_NAME, USER_ID = "102030405060708", "ReachWeaver Test Page", "9988776655"
POST_ID = f"{PAGE_ID}_123456789012345"
PERMALINK = f"https://www.facebook.com/{POST_ID}"
TEXT = "Release notes are out: https://example.com/notes (and https://example.org/more)"
BASE = urlsplit(facebook.API_BASE)


def account(**overrides):
    base = {"id": "fb-page", "platform": "facebook", "env_prefix": PREFIX, "page_id": PAGE_ID,
            "page_name": PAGE_NAME}
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


class FakeGraph:
    """Routes mocked Graph requests by (method, path). ``errors`` maps a route name to an HTTP code."""

    ROUTES = {
        ("GET", f"/{facebook.GRAPH_API_VERSION}/me"): "me",
        ("GET", f"/{facebook.GRAPH_API_VERSION}/{PAGE_ID}"): "page",
        ("POST", f"/{facebook.GRAPH_API_VERSION}/{PAGE_ID}/feed"): "feed",
        ("GET", f"/{facebook.GRAPH_API_VERSION}/{POST_ID}"): "post",
    }

    def __init__(self, *, me=None, page=None, feed=None, post=None, errors=None):
        self.requests, self.routes = [], []
        self.me = me if me is not None else {"id": PAGE_ID, "name": PAGE_NAME}
        self.page = page if page is not None else {"id": PAGE_ID, "name": PAGE_NAME}
        self.feed = feed if feed is not None else {"id": POST_ID}
        self.post = post if post is not None else {"id": POST_ID, "permalink_url": PERMALINK,
                                                   "from": {"id": PAGE_ID, "name": PAGE_NAME}}
        self.errors = errors or {}

    def __call__(self, req):
        parts = urlsplit(req.full_url)
        if (parts.scheme, parts.hostname) != (BASE.scheme, BASE.hostname):
            raise AssertionError("request to an unexpected host")
        if FAKE_TOKEN in req.full_url or (req.data and FAKE_TOKEN.encode() in req.data):
            raise AssertionError("token in URL or body")
        if req.get_header("Authorization") != "Bearer " + FAKE_TOKEN:
            raise AssertionError("missing bearer header")
        route = self.ROUTES.get((req.get_method(), parts.path))
        if route is None:
            raise AssertionError("unexpected request")
        self.requests.append(req)
        self.routes.append(route)
        if route in self.errors:
            raise http_error(req.full_url, self.errors[route], retry_after=120)
        body = {"me": self.me, "page": self.page, "feed": self.feed, "post": self.post}[route]
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
    entry = next(e for e in registry["platforms"] if e["id"] == "facebook")
    entry.update({
        "status": "supported", "reachweaver_adapter": "live_text",
        "rate_limits": {
            "summary": "Pages API calls with a Page token: 4800 x engaged users per 24 hours (BUC); "
                       "no Page post count limit published",
            "source": "https://developers.facebook.com/docs/graph-api/overview/rate-limiting",
            "accessed": "2026-10-09",
            "facts": [{"scope": "page (Page access token, Business Use Case)",
                       "action": "Pages API calls per engaged user", "limit": 4800, "unit": "calls",
                       "window_seconds": 86400}],
            "not_published": ["Page feed posts per day", "HTTP status for throttling codes 4/17/32/613/80001"],
        },
    })
    entry["formats"]["text"] = "api_direct"
    entry["formats"]["link"] = "api_direct"
    return registry


class FlippedRegistry(unittest.TestCase):
    """Patches a temp registry copy and every SUPPORTED_LIVE binding; the bundled file is untouched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        path = self.tmp / "platform_registry.json"
        path.write_text(json.dumps(flipped_registry()), encoding="utf-8")
        live = frozenset(SUPPORTED_LIVE | {"facebook"})
        for target, value in (("reachweaver.capabilities.REGISTRY_FILE", path),
                              ("reachweaver.policy.SUPPORTED_LIVE", live),
                              ("reachweaver.capabilities.SUPPORTED_LIVE", live),
                              ("reachweaver.publishers.SUPPORTED_LIVE", live),
                              ("reachweaver.connections.SUPPORTED_LIVE", live),
                              ("reachweaver.cli.SUPPORTED_LIVE", live),
                              ("reachweaver.facebook._utc_now", lambda: NOW)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        reset_write_marker()


class UnreachableTests(unittest.TestCase):
    """Under the bundled registry the adapter cannot be reached."""

    def test_bundled_registry_keeps_facebook_gated(self):
        entry = next(e for e in load_registry()["platforms"] if e["id"] == "facebook")
        self.assertEqual((entry["reachweaver_adapter"], entry["formats"]["text"], entry["formats"]["link"],
                          entry["status"]), ("none", "api_gated", "api_gated", "requires-review"))
        self.assertNotIn("facebook", SUPPORTED_LIVE)
        self.assertIn("facebook", PUBLISHERS)  # present, but publish() refuses before the lookup
        with self.assertRaisesRegex(ValidationError, "api_gated"):
            require_capability("facebook", "text", "publish")

    def test_library_publish_and_connection_refused_before_any_request(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.facebook._token") as token:
            with self.assertRaisesRegex(ValidationError, "not yet implemented for facebook"):
                publish(account(), "Hello Page", "p1")
            with self.assertRaisesRegex(ValidationError, "platform_has_no_authorized_live_connector"):
                check_connection(account())
        send.assert_not_called()
        token.assert_not_called()

    def test_cli_live_publish_and_connect_check_skip_facebook(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, config = Path(tmp) / "outbox.sqlite", Path(tmp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
            with connect(str(db)) as con:
                pid = add_post(con, "fb-page", "facebook", TEXT)
                transition(con, pid, "approve")
            out = io.StringIO()
            with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send, \
                    patch("reachweaver.facebook._token") as token, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                status = main(["--db", str(db), "--accounts", str(config), "publish", "--live", "--post-id", pid])
                main(["--db", str(db), "--accounts", str(config), "connect-check", "--online",
                      "--account", "fb-page"])
            self.assertEqual(status, 2)
            self.assertIn("requires manual publishing", out.getvalue())
            send.assert_not_called()
            token.assert_not_called()
            with connect(str(db)) as con:
                self.assertEqual(get_post(con, pid)["status"], "approved")

    def test_flipped_copy_is_valid_only_with_matching_supported_live(self):
        registry = flipped_registry()
        self.assertIn("live_text adapters must equal policy.SUPPORTED_LIVE", "; ".join(validate_registry(registry)))
        with patch("reachweaver.capabilities.SUPPORTED_LIVE", frozenset(SUPPORTED_LIVE | {"facebook"})):
            self.assertEqual(validate_registry(registry), [])
        self.assertEqual(json.loads(capabilities.REGISTRY_FILE.read_text(encoding="utf-8")), load_registry())


class ConfigAndCredentialTests(unittest.TestCase):
    def load(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
            return load_accounts(path)

    def test_valid_account_normalized(self):
        loaded = self.load(account(page_id=f" {PAGE_ID} ", page_name="  ReachWeaver   Test Page "))["fb-page"]
        self.assertEqual((loaded["page_id"], loaded["page_name"]), (PAGE_ID, PAGE_NAME))

    def test_invalid_configs_rejected(self):
        no_name = account()
        del no_name["page_name"]
        for bad, needle in ((no_name, "page_name is required"),
                            (account(page_id="my-page"), "numeric"),
                            (account(page_id=""), "numeric"),
                            (account(page_name="x" * 201), "at most 200"),
                            (account(group_id="1"), "unsupported fields group_id"),
                            (account(target_type="profile"), "unsupported fields target_type"),
                            (account(scheduled_publish_time="1"), "unsupported fields scheduled_publish_time"),
                            (account(published=False), "unsupported fields published"),
                            (account(link="https://example.com"), "unsupported fields link"),
                            (account(authorization_expires_at="2026-12-01T00:00:00+00:00"), "unsupported fields authorization_expires_at"),
                            (account(page_access_token="nope"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)
        # Without page_id the account is manual-only metadata and loads as before,
        # but the adapter preflight still refuses it before any request.
        manual = {"id": "fb-manual", "platform": "facebook", "env_prefix": PREFIX, "display_name": "Me"}
        self.assertEqual(self.load(manual)["fb-manual"], manual)
        with self.assertRaisesRegex(ValidationError, "facebook_unsupported_fields: display_name"):
            facebook.preflight(manual)
        no_id = account()
        del no_id["page_id"]
        with self.assertRaisesRegex(ValidationError, "page_id is required"):
            facebook.preflight(no_id)
        with self.assertRaisesRegex(ValidationError, "wrong_platform"):
            facebook.preflight(account(platform="threads"))

    def test_credential_suffix_registered_for_overlap_rule_and_hygiene(self):
        self.assertEqual(CREDENTIAL_SUFFIXES["facebook"], ("_PAGE_ACCESS_TOKEN",))
        self.assertEqual(expected_variables(account()), (PREFIX + "_PAGE_ACCESS_TOKEN",))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(account()), [PREFIX + "_PAGE_ACCESS_TOKEN"])
        # REACHWEAVER_A + _PAGE_ACCESS_TOKEN equals REACHWEAVER_A_PAGE + _ACCESS_TOKEN (LinkedIn).
        clash = [account(env_prefix="REACHWEAVER_A"),
                 {"id": "li", "platform": "linkedin", "env_prefix": "REACHWEAVER_A_PAGE"}]
        self.assertEqual(env_prefix_conflicts(clash)[0]["reason"], "overlapping_env_prefix")
        with self.assertRaisesRegex(ValidationError, "overlapping"):
            self.load(clash[0], {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_A_PAGE_ACCESS",
                                 "server": "https://mastodon.social"})
        report = audit_environment([account()], {PREFIX + "_PAGE_ACCESS_TOKEN": FAKE_TOKEN})
        self.assertEqual(report["accounts"][0]["set"], [PREFIX + "_PAGE_ACCESS_TOKEN"])
        self.assertNotIn(FAKE_TOKEN, json.dumps(report))

    def test_version_lifecycle_offline(self):
        self.assertEqual(facebook.GRAPH_API_VERSION, "v25.0")
        self.assertEqual(facebook.GRAPH_API_AVAILABLE_UNTIL, date(2028, 7, 29))
        self.assertEqual(facebook.version_status(date(2026, 10, 9))[0], "ok")
        self.assertIn("ageing", facebook.version_status(date(2028, 6, 1))[1])
        self.assertIn("expired", facebook.version_status(date(2028, 7, 29))[1])
        with patch("reachweaver.facebook._utc_now", lambda: datetime(2028, 8, 1, tzinfo=timezone.utc)), \
                patch("reachweaver.publishers._send_http") as send, \
                self.assertRaisesRegex(ValidationError, "facebook_graph_version_expired"):
            facebook.publish_page_post(account(), "Hello", "p1")
        send.assert_not_called()
        notes = facebook.doctor_notes({"platform": "facebook"}, now=NOW)
        self.assertIn("blocked by the capability registry", notes[0])
        self.assertIn("manual-export metadata only", notes[1])

    def test_doctor_names_permissions_and_setup_gaps_offline(self):
        """Names only: no credential read, no request, and the permissions stay unverified."""
        with patch("reachweaver.facebook.credential") as read, patch("reachweaver.publishers._send_http") as send:
            complete = facebook.doctor_notes(account(), now=NOW)
            no_name = account()
            del no_name["page_name"]
            missing_name = facebook.doctor_notes(no_name, now=NOW)
            manual = facebook.doctor_notes({"platform": "facebook", "env_prefix": PREFIX}, now=NOW)
        read.assert_not_called()
        send.assert_not_called()
        scope = [n for n in complete if "facebook_write_scope_unverified" in n]
        self.assertEqual(len(scope), 1)
        self.assertTrue(scope[0].startswith("INFO: "))
        for permission in ("pages_manage_posts", "pages_read_engagement", "pages_show_list"):
            self.assertIn(permission, scope[0])
        self.assertIn("unverified until checked live", scope[0])
        self.assertIn("needs_review", scope[0])
        self.assertFalse(any(n.startswith("SETUP:") for n in complete))
        setup = [n for n in missing_name if n.startswith("SETUP:")]
        self.assertEqual(len(setup), 1)
        self.assertIn("'page_name'", setup[0])
        self.assertNotIn("'page_id'", setup[0])
        manual_setup = [n for n in manual if n.startswith("SETUP:")][0]
        self.assertIn("'page_id'", manual_setup)
        self.assertIn("'page_name'", manual_setup)
        self.assertEqual(facebook.setup_gaps(account(page_name="   ")), [
            "config field 'page_name' (the Page name from GET /{page_id}?fields=id,name)"])
        for note in complete + missing_name + manual:
            self.assertNotIn(FAKE_TOKEN, note)


class PayloadTests(unittest.TestCase):
    def test_form_is_a_pure_function_of_the_approved_text(self):
        self.assertEqual(facebook.feed_form(TEXT), {"message": TEXT, "link": "https://example.com/notes"})
        self.assertEqual(facebook.feed_form("No links here"), {"message": "No links here"})
        # Only https links are attached; http/www stay plain text in the message.
        self.assertEqual(facebook.feed_form("See http://a.example and www.b.example"),
                         {"message": "See http://a.example and www.b.example"})
        self.assertEqual(facebook.derived_link("Read https://x.example/a."), "https://x.example/a")

    def test_permalink_shape(self):
        self.assertTrue(facebook.permalink_ok(PERMALINK))
        self.assertTrue(facebook.permalink_ok("https://www.facebook.com/permalink.php?story_fbid=1&id=2"))
        for bad in ("http://www.facebook.com/1_2", "https://evil.example/1_2", "https://u:p@www.facebook.com/1",
                    "https://www.facebook.com:8443/1", "https://www.facebook.com/", "https://www.facebook.com/1#x",
                    None, "https://www.facebook.com/" + "a" * 600):
            with self.subTest(bad=bad):
                self.assertFalse(facebook.permalink_ok(bad))


class FlippedLibraryTests(FlippedRegistry):
    def run_publish(self, fake, acct=None, text=TEXT):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            return publish(acct or account(), text, "p1")

    def test_happy_path_requests_and_receipt(self):
        fake = FakeGraph(post={"id": POST_ID, "permalink_url": PERMALINK, "from": {"id": PAGE_ID}, "message": TEXT})
        receipt = self.run_publish(fake)
        self.assertEqual(fake.routes, ["me", "page", "feed", "post"])
        self.assertEqual(tuple(receipt), (POST_ID, PERMALINK))
        self.assertEqual(receipt.extra, {"text_match": "true"})
        # Exactly the approved text and its first https link; no published/scheduling/targeting field.
        self.assertEqual(fake.form("feed"), {"message": TEXT, "link": "https://example.com/notes"})
        self.assertEqual(fake.query("me"), {"fields": "id,name"})
        self.assertEqual(fake.query("page"), {"fields": "id,name"})
        self.assertEqual(fake.query("post"), {"fields": "id,permalink_url,from,message"})
        self.assertTrue(write_started())

    def test_user_token_or_wrong_page_refused_before_any_write(self):
        for kwargs in ({"me": {"id": USER_ID, "name": "Some Person"}},          # a User token
                       {"me": {"id": "555", "name": PAGE_NAME}},                 # another Page's token
                       {"page": {"id": "555", "name": PAGE_NAME}},
                       {"page": {"id": PAGE_ID, "name": "Another Page"}},
                       {"me": {"id": PAGE_ID, "name": "Another Page"}},
                       {"me": {"name": PAGE_NAME}}):
            fake = FakeGraph(**kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises((ValidationError, PublishError)) as ctx:
                self.run_publish(fake)
            self.assertNotIn("feed", fake.routes)
            self.assertFalse(write_started())
            if "id" in kwargs.get("me", kwargs.get("page", {})):
                self.assertIn("identity_mismatch", str(ctx.exception))

    def test_preflight_http_errors_are_pre_write_and_actionable(self):
        for route, code, needle in (("me", 400, "facebook_http_400: request refused"),
                                    ("me", 401, "facebook_http_401: Page token invalid"),
                                    ("me", 403, "facebook_http_403"),
                                    ("me", 429, "^facebook_http_429$"), ("me", 500, "^facebook_http_500$"),
                                    ("page", 400, "facebook_http_400: Page read refused"),
                                    ("page", 403, "facebook_http_403: Page read refused")):
            fake = FakeGraph(errors={route: code})
            with self.subTest(route=route, code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertFalse(write_started())
            self.assertNotIn("feed", fake.routes)

    def test_post_write_http_errors_are_uncertain(self):
        for code, needle in ((400, "^facebook_feed_http_400_uncertain: post refused"),
                             (401, "^facebook_feed_http_401_uncertain$"),
                             (403, "^facebook_feed_http_403_uncertain$"),
                             (429, "^facebook_feed_http_429_uncertain: rate limited"),
                             (500, "^facebook_feed_http_500_uncertain$")):
            reset_write_marker()
            fake = FakeGraph(errors={"feed": code})
            with self.subTest(code=code), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_publish(fake)
            self.assertTrue(write_started())
            self.assertEqual(ctx.exception.status, code)
            self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
            self.assertEqual(fake.routes.count("feed"), 1)  # never retried

    def test_network_timeout_after_write_is_uncertain(self):
        fake = FakeGraph()

        def timeout(req):
            if req.get_method() == "POST":
                raise TimeoutError()
            return fake(req)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=timeout), \
                self.assertRaisesRegex(PublishError, "^facebook_feed_network_or_timeout_uncertain$"):
            publish(account(), "Hello", "p1")
        self.assertTrue(write_started())

    def test_unexpected_post_ids_are_uncertain(self):
        for feed, needle in (({}, "facebook_feed_missing_post_id_uncertain"),
                             ({"id": 12345}, "facebook_feed_missing_post_id_uncertain"),
                             ({"id": "123456789"}, "facebook_feed_post_id_unrecognized_uncertain"),
                             ({"id": "555_123"}, "facebook_receipt_page_mismatch_uncertain")):
            reset_write_marker()
            with self.subTest(feed=feed), self.assertRaisesRegex(PublishError, "^" + needle + "$"):
                self.run_publish(FakeGraph(feed=feed))
            self.assertTrue(write_started())

    def test_receipt_naming_another_post_or_page_is_uncertain(self):
        for post, needle in (({"id": f"{PAGE_ID}_1"}, "receipt_id_mismatch"),
                             ({"id": POST_ID, "from": {"id": "555"}}, "receipt_page_mismatch")):
            with self.subTest(post=post), self.assertRaisesRegex(PublishError, needle + "_uncertain"):
                self.run_publish(FakeGraph(post=post))

    def test_permalink_unavailable_or_unrecognized_keeps_post_id(self):
        receipt = self.run_publish(FakeGraph(errors={"post": 500}))
        self.assertEqual(tuple(receipt), (POST_ID, None))
        self.assertEqual(receipt.extra["permalink"], "unavailable")
        receipt = self.run_publish(FakeGraph(post={"id": POST_ID, "permalink_url": "https://evil.example/x",
                                                   "message": "edited"}))
        self.assertEqual(tuple(receipt), (POST_ID, None))
        self.assertEqual((receipt.extra["permalink"], receipt.extra["text_match"]), ("unrecognized", "false"))

    def test_offline_refusals_before_network(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            for bad, needle in ((account(page_id="x"), "numeric"), (account(page_name=""), "page_name is required")):
                with self.subTest(needle=needle):
                    with self.assertRaisesRegex(ValidationError, needle):
                        check_connection(bad)
                    with self.assertRaisesRegex(ValidationError, needle):
                        facebook.publish_page_post(bad, "Hello", "p1")
            with self.assertRaisesRegex(ValidationError, "substantive text"):
                facebook.publish_page_post(account(), "   ", "p1")
        send.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send, \
                self.assertRaisesRegex(ValidationError, "missing_credentials: " + PREFIX + "_PAGE_ACCESS_TOKEN"):
            check_connection(account())
        send.assert_not_called()
        self.assertFalse(write_started())

    def test_connection_reports_identity_but_unverified_write_permission(self):
        fake = FakeGraph()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            result = check_connection(account())
        self.assertEqual((result.platform, result.identity, result.write_permission),
                         ("facebook", "verified", "unverified"))
        self.assertEqual(fake.routes, ["me", "page"])
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

    def approved(self, text=TEXT):
        with connect(str(self.db)) as con:
            pid = add_post(con, "fb-page", "facebook", text)
            transition(con, pid, "approve")
        return pid

    def status_of(self, pid):
        with connect(str(self.db)) as con:
            return get_post(con, pid)["status"]

    def test_unapproved_draft_never_sent(self):
        with connect(str(self.db)) as con:
            pid = add_post(con, "fb-page", "facebook", "Draft only")
        fake = FakeGraph()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            self.assertEqual(self.cli("publish", "--live", "--post-id", pid)[0], 2)
        self.assertEqual(fake.routes, [])
        self.assertEqual(self.status_of(pid), "draft")

    def test_edited_after_approval_never_sent(self):
        pid = self.approved()
        with connect(str(self.db)) as con:  # bypass the CLI: a raw edit after approval
            con.execute("UPDATE posts SET content=? WHERE id=?", (TEXT + " https://evil.example", pid))
        fake = FakeGraph()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("BLOCKED", out)
        self.assertEqual(fake.routes, [])

    def test_without_live_gate_no_request(self):
        pid = self.approved()
        with patch.dict(os.environ, ENV, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, _, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        send.assert_not_called()

    def test_dry_run_sends_nothing(self):
        pid = self.approved()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send:
            self.cli("publish", "--post-id", pid)
        send.assert_not_called()
        self.assertEqual(self.status_of(pid), "approved")

    def test_end_to_end_receipt_stored(self):
        pid = self.approved()
        fake = FakeGraph()
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("PUBLISHED " + pid, out)
        self.assertNotIn(FAKE_TOKEN, out + err)
        self.assertEqual(fake.routes, ["me", "page", "feed", "post"])
        with connect(str(self.db)) as con:
            row = get_post(con, pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]), ("published", POST_ID, PERMALINK))

    def test_wrong_page_blocked_before_claim(self):
        pid = self.approved()
        fake = FakeGraph(me={"id": USER_ID, "name": "Some Person"})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("identity_mismatch", out)
        self.assertNotIn("feed", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")

    def test_preflight_429_records_cooldown_and_keeps_post_approved(self):
        pid = self.approved()
        fake = FakeGraph(errors={"me": 429})
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake), \
                patch("reachweaver.ratelimit._sleep"):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("BLOCKED", out)
        self.assertNotIn("feed", fake.routes)
        self.assertEqual(self.status_of(pid), "approved")
        with connect(str(self.db)) as con:
            self.assertIsNotNone(active_provider_cooldown(con, "fb-page"))

    def test_post_write_failure_goes_to_needs_review_and_is_never_resent(self):
        for code in (400, 401, 429, 500):
            with self.subTest(code=code):
                self.db = self.tmp / f"feed-{code}" / "outbox.sqlite"  # fresh queue: no min-gap carry-over
                pid = self.approved(f"Release notes {code}: https://example.com")
                fake = FakeGraph(errors={"feed": code})
                with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
                    status, out, _ = self.cli("publish", "--live", "--post-id", pid)
                    again, _, _ = self.cli("publish", "--live", "--post-id", pid)
                self.assertEqual((status, again), (3, 2))
                self.assertIn(f"facebook_feed_http_{code}_uncertain", out)
                self.assertIn("[ambiguous]", out)
                self.assertNotIn(FAKE_TOKEN, out)
                self.assertEqual(fake.routes.count("feed"), 1)
                self.assertEqual(self.status_of(pid), "needs_review")
                if code == 429:
                    with connect(str(self.db)) as con:
                        self.assertIsNotNone(active_provider_cooldown(con, "fb-page"))

    def test_emergency_pause_blocks_before_any_request(self):
        pid = self.approved()
        self.assertEqual(self.cli("pause")[0], 0)
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers._send_http") as send:
            status, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertNotEqual(status, 0)
        send.assert_not_called()
        self.assertEqual(self.status_of(pid), "approved")

    def test_doctor_reports_draft_status_offline(self):
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertEqual(status, 0)
        self.assertIn("fb-page: facebook;", out)
        self.assertIn("blocked by the capability registry", out)
        self.assertIn("fb-page: INFO: facebook_write_scope_unverified: the Page token needs pages_manage_posts", out)
        self.assertNotIn("SETUP:", out)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
