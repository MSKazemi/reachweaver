"""LinkedIn member text publisher (issue #14). Every HTTP exchange is mocked: no network, no live post."""
from __future__ import annotations

import contextlib
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

from reachweaver import linkedin
from reachweaver.accounts import load_accounts, missing_credentials
from reachweaver.capabilities import require_capability
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.policy import CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, SUPPORTED_LIVE, ValidationError
from reachweaver.publishers import PUBLISHERS, PublishError, request_json_with_headers
from gate_support import publish  # noqa: E402
from reachweaver.storage import add_post, connect, get_post, transition

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
FAKE_TOKEN = "fake-linkedin-member-token-0000"  # not a real credential
PREFIX = "REACHWEAVER_TEST_LINKEDIN"
ENV = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}


def account(**overrides):
    base = {"id": "li-me", "platform": "linkedin", "env_prefix": PREFIX,
            "person_id": "782bbtaQ", "linkedin_version": "202609",
            "authorization_expires_at": "2026-12-01T00:00:00+00:00"}
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, status=201, headers=None, body=b""):
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


class FakeLinkedIn:
    """Routes mocked requests: GET /v2/userinfo and POST /rest/posts. Records each Request."""

    def __init__(self, *, sub="782bbtaQ", post_status=201, post_headers=None, post_error=None):
        self.requests = []
        self.sub = sub
        self.post_status = post_status
        self.post_headers = {"x-restli-id": "urn:li:share:6844785523593134080"} if post_headers is None else post_headers
        self.post_error = post_error

    def __call__(self, req):
        self.requests.append(req)
        if req.full_url == linkedin.USERINFO_URL and req.get_method() == "GET":
            return FakeResponse(200, {"Content-Type": "application/json"},
                                json.dumps({"sub": self.sub, "name": "Test Member"}).encode())
        if req.full_url == linkedin.POSTS_URL and req.get_method() == "POST":
            if self.post_error:
                raise self.post_error
            return FakeResponse(self.post_status, self.post_headers, b"")
        raise AssertionError("unexpected request")


class VersionAndTokenLifecycleTests(unittest.TestCase):
    def test_version_status_windows(self):
        today = date(2026, 10, 8)
        self.assertEqual(linkedin.version_status("202609", today)[0], "ok")
        self.assertEqual(linkedin.version_status("202512", today)[0], "warning")  # 10 months
        level, message = linkedin.version_status("202510", today)               # 12 months: sunset window
        self.assertEqual(level, "error")
        self.assertIn("linkedin_version_unsupported", message)
        self.assertEqual(linkedin.version_status("202701", today)[0], "error")  # future
        for bad in ("2026-09", "202613", "202205", 202609, None, "20260"):
            with self.subTest(bad=bad):
                self.assertEqual(linkedin.version_status(bad, today)[0], "error")

    def test_version_status_month_boundaries_match_documented_policy(self):
        today = date(2026, 10, 8)
        expected = {"202610": "ok", "202601": "ok", "202512": "warning", "202511": "warning",
                    "202510": "error", "202509": "error"}  # ages 0, 9, 10, 11, 12, 13
        for version, level in expected.items():
            with self.subTest(version=version):
                self.assertEqual(linkedin.version_status(version, today)[0], level)
        self.assertIn("linkedin_version_ageing", linkedin.version_status("202511", today)[1])
        self.assertIn("linkedin_version_unsupported", linkedin.version_status("202509", today)[1])
        # Future and malformed values are errors with the separate documented code.
        for bad in ("202611", "2026-09", None):
            with self.subTest(bad=bad):
                level, message = linkedin.version_status(bad, today)
                self.assertEqual(level, "error")
                self.assertIn("linkedin_version_invalid", message)
                self.assertNotIn("linkedin_version_unsupported", message)

    def test_token_status(self):
        self.assertEqual(linkedin.token_status(account(), NOW)[0], "ok")
        self.assertEqual(linkedin.token_status(account(authorization_expires_at="2026-10-12T00:00:00Z"), NOW)[0], "warning")
        self.assertEqual(linkedin.token_status(account(authorization_expires_at="2026-10-01T00:00:00Z"), NOW)[0], "expired")
        no_expiry = account()
        del no_expiry["authorization_expires_at"]
        self.assertEqual(linkedin.token_status(no_expiry, NOW)[0], "unknown")

    def test_doctor_notes_warn_without_reading_token(self):
        notes = linkedin.doctor_notes(account(linkedin_version="202512", authorization_expires_at="2026-10-01T00:00:00Z"), now=NOW)
        self.assertTrue(any(n.startswith("WARNING: linkedin_version_ageing") for n in notes))
        self.assertTrue(any(n.startswith("ERROR: linkedin_token_expired") for n in notes))
        # Without the opt-in scope check, doctor explains the residual risk (INFO, issue #14 draft).
        notes = linkedin.doctor_notes(account(), now=NOW)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("INFO: linkedin_write_scope_unverified"))
        self.assertEqual(linkedin.doctor_notes(account(scope_check="introspect"), now=NOW), [])


class PayloadAndEscapingTests(unittest.TestCase):
    def test_payload_shape_is_documented_text_only_member_post(self):
        payload = linkedin.build_post_payload("782bbtaQ", "Release 1.2 is out")
        self.assertEqual(payload, {
            "author": "urn:li:person:782bbtaQ",
            "commentary": "Release 1.2 is out",
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        })
        self.assertNotIn("content", payload)

    def test_headers(self):
        headers = linkedin.api_headers(FAKE_TOKEN, "202609")
        self.assertEqual(headers["Linkedin-Version"], "202609")
        self.assertEqual(headers["X-Restli-Protocol-Version"], "2.0.0")
        self.assertEqual(headers["Authorization"], "Bearer " + FAKE_TOKEN)
        with self.assertRaises(ValidationError):
            linkedin.api_headers(FAKE_TOKEN, "2026-09")

    def test_little_text_reserved_characters_escaped(self):
        raw = r"a|b{c}d@e[f]g(h)i<j>k\l*m_n~o"
        self.assertEqual(linkedin.escape_commentary(raw),
                         r"a\|b\{c\}d\@e\[f\]g\(h\)i\<j\>k\\l\*m\_n\~o")
        # Documented bullet example: '* Point' must become '\* Point'.
        self.assertEqual(linkedin.escape_commentary("* Point 1\n* Point 2"), "\\* Point 1\n\\* Point 2")
        # No mention can be smuggled in: @[Name](urn:li:organization:1) renders literally.
        self.assertEqual(linkedin.escape_commentary("@[Org](urn:li:organization:1)"),
                         r"\@\[Org\]\(urn:li:organization:1\)")

    def test_hashtags_kept_only_at_word_start(self):
        self.assertEqual(linkedin.escape_commentary("Ship it #opensource"), "Ship it #opensource")
        self.assertEqual(linkedin.escape_commentary("issue#12 and # alone"), r"issue\#12 and \# alone")
        self.assertEqual(linkedin.escape_commentary("#tag", keep_hashtags=False), r"\#tag")

    def test_organization_and_foreign_authors_rejected(self):
        for bad in ("urn:li:organization:5515715", "urn:li:organizationBrand:1", "urn:li:company:1",
                    "has space", "", None, "a:b"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                linkedin.build_post_payload(bad, "text")
        self.assertEqual(linkedin.normalize_person_id("urn:li:person:5abc_dEfgH"), "5abc_dEfgH")


class ReceiptTests(unittest.TestCase):
    def test_x_restli_id_parsed_into_urn_and_url(self):
        urn, url = linkedin.parse_post_receipt(201, {"X-RestLi-Id": "urn:li:share:6844785523593134080"})
        self.assertEqual(urn, "urn:li:share:6844785523593134080")
        self.assertEqual(url, "https://www.linkedin.com/feed/update/urn:li:share:6844785523593134080/")
        urn, _ = linkedin.parse_post_receipt(201, {"x-restli-id": "urn%3Ali%3AugcPost%3A68447855235931240"})
        self.assertEqual(urn, "urn:li:ugcPost:68447855235931240")

    def test_missing_or_odd_receipt_is_uncertain(self):
        cases = [(201, {}, "missing_x_restli_id_uncertain"),
                 (201, {"x-restli-id": "  "}, "missing_x_restli_id_uncertain"),
                 (201, {"x-restli-id": "urn:li:organization:1"}, "unrecognized_post_urn_uncertain"),
                 (200, {"x-restli-id": "urn:li:share:1"}, "unexpected_status_200_uncertain")]
        for status, headers, needle in cases:
            with self.subTest(needle=needle), self.assertRaisesRegex(PublishError, needle):
                linkedin.parse_post_receipt(status, headers)


class HttpHelperTests(unittest.TestCase):
    @patch("reachweaver.publishers._send_http")
    def test_headers_returned_and_empty_body_allowed(self, send):
        send.return_value = FakeResponse(201, {"X-RestLi-Id": "urn:li:share:1"}, b"")
        status, headers, body = request_json_with_headers("https://api.linkedin.com/rest/posts", {"a": 1})
        self.assertEqual((status, headers["x-restli-id"], body), (201, "urn:li:share:1", {}))

    @patch("reachweaver.publishers._send_http")
    def test_oversized_and_invalid_bodies_rejected(self, send):
        send.return_value = FakeResponse(201, {}, b"x" * 1_000_001)
        with self.assertRaisesRegex(PublishError, "response_too_large"):
            request_json_with_headers("https://api.linkedin.com/rest/posts", {})
        send.return_value = FakeResponse(201, {}, b"[1]")
        with self.assertRaisesRegex(PublishError, "unexpected_response_type"):
            request_json_with_headers("https://api.linkedin.com/rest/posts", {})

    @patch("reachweaver.publishers._send_http")
    def test_token_never_in_errors(self, send):
        send.side_effect = HTTPError("https://api.linkedin.com/rest/posts?t=" + FAKE_TOKEN, 403,
                                     "Bearer " + FAKE_TOKEN, None, None)
        with patch.dict(os.environ, ENV), self.assertRaises(PublishError) as ctx:
            linkedin.publish_member_post(account(), "Hello", "p1", now=NOW)
        message = str(ctx.exception)
        self.assertNotIn(FAKE_TOKEN, message)
        self.assertIn("w_member_social", message)
        self.assertTrue(message.endswith("verify the profile before reconciling"))
        self.assertIn("uncertain", message)
        with patch.dict(os.environ, ENV), self.assertRaises(PublishError) as ctx:
            linkedin.verify_identity(account(), now=NOW)
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertIn("openid profile", str(ctx.exception))

    @patch("reachweaver.publishers._send_http")
    def test_deprecated_version_and_network_errors_are_uncertain(self, send):
        send.side_effect = HTTPError(linkedin.POSTS_URL, 426, "NONEXISTENT_VERSION", None, None)
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(PublishError, "http_426_uncertain"):
            linkedin.publish_member_post(account(), "Hello", "p1", now=NOW)
        send.side_effect = TimeoutError()
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(PublishError, "^linkedin_network_or_timeout_uncertain$"):
            linkedin.publish_member_post(account(), "Hello", "p1", now=NOW)


class PublishAndIdentityTests(unittest.TestCase):
    def setUp(self):
        clock = patch("reachweaver.linkedin._utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_registered_and_capability_consistent(self):
        self.assertIn("linkedin", SUPPORTED_LIVE)
        self.assertIn("linkedin", PUBLISHERS)
        self.assertEqual(require_capability("linkedin")["reachweaver_adapter"], "live_text")
        with self.assertRaises(ValidationError):
            require_capability("linkedin", "image", "publish")  # media not implemented
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(account()), [PREFIX + "_ACCESS_TOKEN"])

    def test_request_on_the_wire(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            urn, url = linkedin.publish_member_post(account(), "Proof: https://example.com (v1.2)", "p1")
        self.assertEqual(urn, "urn:li:share:6844785523593134080")
        self.assertTrue(url.startswith("https://www.linkedin.com/feed/update/urn:li:share:"))
        req = fake.requests[0]
        self.assertEqual((req.full_url, req.get_method()), (linkedin.POSTS_URL, "POST"))
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["linkedin-version"], "202609")
        self.assertEqual(headers["x-restli-protocol-version"], "2.0.0")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["authorization"], "Bearer " + FAKE_TOKEN)
        body = json.loads(req.data.decode())
        self.assertEqual(body["author"], "urn:li:person:782bbtaQ")
        self.assertEqual(body["commentary"], r"Proof: https://example.com \(v1.2\)")

    def test_identity_verified_and_mismatch_blocked(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            result = check_connection(account())
        self.assertEqual((result.platform, result.identity, result.write_permission),
                         ("linkedin", "verified", "unverified"))
        self.assertEqual(fake.requests[0].full_url, "https://api.linkedin.com/v2/userinfo")
        self.assertNotIn(FAKE_TOKEN, repr(result))
        other = FakeLinkedIn(sub="782bbtaq")  # case differs: ids are compared exactly
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=other), \
                self.assertRaisesRegex(ValidationError, "identity_mismatch"):
            check_connection(account())

    def test_old_version_and_expired_token_refused_before_network(self):
        for bad, needle in ((account(linkedin_version="202510"), "linkedin_version_unsupported"),
                            (account(authorization_expires_at="2026-10-08T11:59:00+00:00"), "linkedin_token_expired"),
                            (account(person_id="urn:li:organization:5515715"), "organization_unsupported"),
                            (account(organization_id="5515715"), "organization_unsupported")):
            with self.subTest(needle=needle), patch.dict(os.environ, ENV), \
                    patch("reachweaver.publishers._send_http") as send:
                with self.assertRaisesRegex(ValidationError, needle):
                    check_connection(bad)
                with self.assertRaisesRegex(ValidationError, needle):
                    linkedin.publish_member_post(bad, "Hello", "p1")
                send.assert_not_called()

    def test_library_publish_runs_identity_preflight(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            remote_id, _ = publish(account(), "Library caller post", "p1")
        self.assertEqual([r.full_url for r in fake.requests], [linkedin.USERINFO_URL, linkedin.POSTS_URL])
        self.assertEqual(remote_id, "urn:li:share:6844785523593134080")


class AccountConfigTests(unittest.TestCase):
    def load(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
            return load_accounts(path)

    def test_valid_member_account(self):
        loaded = self.load(account(person_id="urn:li:person:782bbtaQ"))["li-me"]
        self.assertEqual(loaded["person_id"], "782bbtaQ")

    def test_invalid_linkedin_configs_rejected(self):
        no_person = account()
        del no_person["person_id"]
        no_version = account()
        del no_version["linkedin_version"]
        for bad, needle in ((no_person, "person_id is required"), (no_version, "YYYYMM"),
                            (account(linkedin_version="2026-09"), "YYYYMM"),
                            (account(person_id="urn:li:organization:1"), "organization_unsupported"),
                            (account(organization_urn="urn:li:organization:1"), "organization_unsupported"),
                            (account(author="urn:li:organization:1"), "organization_unsupported"),
                            (account(author_urn="urn:li:organization:1"), "organization_unsupported"),
                            (account(authorization_expires_at="AQUvlL_DYEzvT2wz1QJiEPeLioeA"), "ISO-8601"),
                            (account(access_token="nope"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)

    def test_token_named_expiry_key_still_rejected_by_secret_field_rule(self):
        # The unchanged accounts.SECRET_FIELD rule has no exceptions: 'token_expires_at'
        # is refused even with a harmless timestamp value; use authorization_expires_at.
        legacy = account()
        legacy["token_expires_at"] = legacy.pop("authorization_expires_at")
        with self.assertRaisesRegex(ValidationError, "Never put tokens"):
            self.load(legacy)

    def test_token_expiry_metadata_only_for_linkedin(self):
        with self.assertRaisesRegex(ValidationError, "only supported for LinkedIn"):
            self.load({"id": "my-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_B", "handle": "a.bsky.social",
                       "authorization_expires_at": "2026-12-01T00:00:00+00:00"})


class CliLiveTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.db = Path(self.work.name) / "state" / "outbox.sqlite"
        self.config = Path(self.work.name) / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [account()]}), encoding="utf-8")
        clock = patch("reachweaver.linkedin._utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def draft(self, text="Evidence-backed release note: https://example.com"):
        with connect(str(self.db)) as db:
            return add_post(db, "li-me", "linkedin", text)

    def test_end_to_end_live_requires_approval_and_stores_receipt(self):
        pid = self.draft()
        fake = FakeLinkedIn()
        env = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with patch.dict(os.environ, env), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live")  # unapproved draft: nothing is due
            self.assertEqual(status, 0)
            self.assertEqual(fake.requests, [])
            self.assertEqual(self.cli("publish", "--live", "--post-id", pid)[0], 2)
            self.assertEqual(fake.requests, [])
            with connect(str(self.db)) as db:
                self.assertEqual(get_post(db, pid)["status"], "draft")
                transition(db, pid, "approve")
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("PUBLISHED " + pid, out)
        self.assertEqual([r.full_url for r in fake.requests], [linkedin.USERINFO_URL, linkedin.POSTS_URL])
        self.assertNotIn(FAKE_TOKEN, out + err)
        with connect(str(self.db)) as db:
            row = get_post(db, pid)
            self.assertEqual(row["status"], "published")
            self.assertEqual(row["remote_id"], "urn:li:share:6844785523593134080")
            self.assertEqual(row["remote_url"],
                             "https://www.linkedin.com/feed/update/urn:li:share:6844785523593134080/")

    def test_without_env_opt_in_no_request(self):
        pid = self.draft()
        with connect(str(self.db)) as db:
            transition(db, pid, "approve")
        with patch.dict(os.environ, ENV, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        send.assert_not_called()

    def test_dry_run_sends_nothing_and_leaves_the_post_unpublished(self):
        # Without --live, publish is a dry run even with the live gate open and a token present.
        pid = self.draft()
        with connect(str(self.db)) as db:
            transition(db, pid, "approve")
        env = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with patch.dict(os.environ, env), patch("reachweaver.publishers._send_http") as send:
            status, out, err = self.cli("publish", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("DRY RUN: 1 due approved posts; no requests sent", out)
        send.assert_not_called()
        self.assertNotIn(FAKE_TOKEN, out + err)
        with connect(str(self.db)) as db:
            row = get_post(db, pid)
            claims = db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=? AND action='send_claimed'",
                                (pid,)).fetchone()[0]
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]), ("approved", None, None))
        self.assertEqual(claims, 0)

    def test_show_prints_an_offline_linkedin_preview_without_reading_the_token(self):
        # private #14: the same review aid Bluesky and Mastodon get; account config only.
        text = "Release *notes* (v2) https://example.com"
        pid = self.draft(text)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send, \
                patch("reachweaver.accounts.credential") as read_credential:
            status, out, err = self.cli("show", pid)
        self.assertEqual(status, 0, out + err)
        send.assert_not_called()
        read_credential.assert_not_called()
        preview = json.loads(out)["platform_preview"]
        self.assertEqual(preview["account_id"], "li-me")
        self.assertIsNone(preview["handle"])
        self.assertEqual(preview["member_urn"], "urn:li:person:782bbtaQ")
        self.assertEqual(preview["visibility"], linkedin.build_post_payload("782bbtaQ", text)["visibility"])
        self.assertEqual((preview["counted_length"], preview["text_limit"]),
                         (len(text), CONSERVATIVE_TEXT_LIMITS["linkedin"]))
        self.assertEqual((preview["escaped_commentary_length"], preview["escaped_commentary_limit"]),
                         (len(text) + 4, LINKEDIN_ESCAPED_MAX))  # '*', '*', '(' and ')' each gain a backslash
        self.assertTrue(preview["within_limits"])
        self.assertIn("dry run", preview["dry_run"])
        self.assertNotIn(FAKE_TOKEN, out + err)

    def test_preview_flags_escaped_commentary_over_the_limit(self):
        # draft refuses such text, so the preview helper is called directly on a stand-in row
        from reachweaver.cli import _platform_preview
        row = {"id": "p1", "account_id": "li-me", "platform": "linkedin", "content": "*" * 1600}
        with patch("reachweaver.publishers._send_http") as send:
            preview = _platform_preview(row, load_accounts(self.config)["li-me"])
        send.assert_not_called()
        self.assertEqual((preview["counted_length"], preview["escaped_commentary_length"]), (1600, 3200))
        self.assertFalse(preview["within_limits"])

    def test_show_preview_without_account_config_has_no_member_urn(self):
        pid = self.draft()
        self.config.write_text(json.dumps({"accounts": []}), encoding="utf-8")
        status, out, _ = self.cli("show", pid)
        self.assertEqual(status, 0)
        preview = json.loads(out)["platform_preview"]
        self.assertIsNone(preview["member_urn"])
        self.assertEqual(preview["account_config"], "not loaded; member URN unknown")
        self.assertEqual(preview["visibility"], linkedin.VISIBILITY)

    def test_missing_receipt_header_goes_to_needs_review_not_retried(self):
        pid = self.draft()
        with connect(str(self.db)) as db:
            transition(db, pid, "approve")
        fake = FakeLinkedIn(post_headers={})
        with patch.dict(os.environ, {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
            self.assertEqual(status, 3)
            self.assertIn("linkedin_missing_x_restli_id_uncertain", out)
            again, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(again, 2)  # not due any more: never auto-retried
        self.assertEqual(sum(r.full_url == linkedin.POSTS_URL for r in fake.requests), 1)
        with connect(str(self.db)) as db:
            self.assertEqual(get_post(db, pid)["status"], "needs_review")

    def test_old_version_blocks_before_claim(self):
        self.config.write_text(json.dumps({"accounts": [account(linkedin_version="202510")]}), encoding="utf-8")
        pid = self.draft()
        with connect(str(self.db)) as db:
            transition(db, pid, "approve")
        with patch.dict(os.environ, {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2)
        self.assertIn("BLOCKED", out)
        self.assertIn("linkedin_version_unsupported", out)
        send.assert_not_called()
        with connect(str(self.db)) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_doctor_reports_lifecycle_offline(self):
        self.config.write_text(json.dumps({"accounts": [
            account(linkedin_version="202512", authorization_expires_at="2026-10-10T00:00:00+00:00")]}), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertEqual(status, 0)
        self.assertIn("li-me: linkedin; supported (text); env variables missing: " + PREFIX + "_ACCESS_TOKEN", out)
        self.assertIn("WARNING: linkedin_version_ageing", out)
        self.assertIn("WARNING: linkedin_token_expiring", out)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
