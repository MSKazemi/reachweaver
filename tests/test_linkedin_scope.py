"""Opt-in LinkedIn write-scope check via token introspection (issue #14, owner-approved 2026-10-09).

Every HTTP exchange is mocked: no network, no live post, no real credential.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

from reachweaver import linkedin
from reachweaver.accounts import env_prefix_conflicts, load_accounts, missing_credentials
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.credential_hygiene import audit_environment, render_audit
from reachweaver.policy import ValidationError
from reachweaver.privacy import redact
from reachweaver.publishers import PublishError
from gate_support import publish  # noqa: E402
from reachweaver.storage import add_post, connect, get_post, transition

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 12, 1, tzinfo=timezone.utc)
PREFIX = "REACHWEAVER_TEST_LISCOPE"
# Fake values, never real credentials; each is asserted absent from every output.
FAKE_TOKEN = "fake-li-member-access-value-7c1e"
FAKE_CLIENT_ID = "fakeclientid77"
FAKE_APP_SECRET = "fake-li-app-secret-value-93ab"
ENV = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN, PREFIX + "_CLIENT_ID": FAKE_CLIENT_ID,
       PREFIX + "_CLIENT_SECRET": FAKE_APP_SECRET}
LIVE = {**ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}


def account(**overrides):
    base = {"id": "li-scope", "platform": "linkedin", "env_prefix": PREFIX, "person_id": "782bbtaQ",
            "linkedin_version": "202609", "authorization_expires_at": "2026-12-01T00:00:00+00:00",
            "scope_check": "introspect"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def introspection(**overrides):
    body = {"active": True, "client_id": FAKE_CLIENT_ID, "authorized_at": 1759000000,
            "created_at": 1759000000, "status": "active", "expires_at": int(EXPIRES.timestamp()),
            "scope": "openid,profile,w_member_social", "auth_type": "3L"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
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
    """Routes GET /v2/userinfo, POST introspectToken and POST /rest/posts; records each Request."""

    def __init__(self, *, body=None, error=None):
        self.requests = []
        self.body = introspection() if body is None else body
        self.error = error

    def urls(self):
        return [r.full_url for r in self.requests]

    def __call__(self, req):
        self.requests.append(req)
        if req.full_url == linkedin.USERINFO_URL and req.get_method() == "GET":
            return FakeResponse(200, {"Content-Type": "application/json"},
                                json.dumps({"sub": "782bbtaQ"}).encode())
        if req.full_url == linkedin.INTROSPECT_URL and req.get_method() == "POST":
            if self.error is not None:
                raise self.error
            return FakeResponse(200, {"Content-Type": "application/json"}, json.dumps(self.body).encode())
        if req.full_url == linkedin.POSTS_URL and req.get_method() == "POST":
            return FakeResponse(201, {"x-restli-id": "urn:li:share:6844785523593134080"}, b"")
        raise AssertionError("unexpected request")


def http_error(code):
    # The URL and message deliberately contain the secrets: neither may reach any output.
    return HTTPError(linkedin.INTROSPECT_URL + "?client_secret=" + FAKE_APP_SECRET, code,
                     "Bearer " + FAKE_TOKEN, None, None)


class Base(unittest.TestCase):
    def setUp(self):
        linkedin.reset_scope_cache()
        self.addCleanup(linkedin.reset_scope_cache)
        clock = patch("reachweaver.linkedin._utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        sleeper = patch("reachweaver.ratelimit._sleep", lambda _seconds: None)  # preflight backoff never sleeps
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def assert_no_secret(self, text):
        for value in (FAKE_TOKEN, FAKE_APP_SECRET):
            self.assertNotIn(value, text)


class EvaluateIntrospectionTests(Base):
    def evaluate(self, body, acct=None):
        return linkedin.evaluate_introspection(body, acct or account(), FAKE_CLIENT_ID, NOW)

    def test_active_member_token_with_scope_passes(self):
        check = self.evaluate(introspection())
        self.assertEqual((check.ok, check.code, check.expires_at, check.notes), (True, "ok", EXPIRES, ()))
        # Space-delimited scope (token endpoint style) is accepted too.
        self.assertTrue(self.evaluate(introspection(scope="openid profile w_member_social")).ok)

    def test_refusals_fail_closed(self):
        cases = [
            (introspection(scope="openid,profile"), "linkedin_scope_missing_w_member_social"),
            (introspection(scope="w_member_social_x,r_member_social"), "linkedin_scope_missing_w_member_social"),
            (introspection(scope=None), "linkedin_scope_missing_w_member_social"),
            (introspection(scope=["w_member_social"]), "linkedin_scope_missing_w_member_social"),
            (introspection(active=False, status="revoked"), "linkedin_token_revoked"),
            (introspection(active=False, status="expired"), "linkedin_token_expired"),
            (introspection(active=False), "linkedin_token_inactive"),
            (introspection(active="true"), "linkedin_token_inactive"),
            (introspection(active=None), "linkedin_token_inactive"),
            (introspection(status="weird"), "linkedin_token_inactive"),
            (introspection(client_id="another-app"), "linkedin_token_client_mismatch"),
            (introspection(auth_type="2L"), "linkedin_token_not_member_token"),
            (introspection(expires_at=None), "linkedin_introspection_missing_expiry"),
            (introspection(expires_at=True), "linkedin_introspection_missing_expiry"),
            (introspection(expires_at="1797000000"), "linkedin_introspection_missing_expiry"),
            (introspection(expires_at=int((NOW - timedelta(seconds=1)).timestamp())), "linkedin_token_expired"),
            (introspection(expires_at=int((NOW + timedelta(minutes=59)).timestamp())),
             "linkedin_token_expires_within_margin"),
        ]
        for body, code in cases:
            with self.subTest(code=code, body=body):
                check = self.evaluate(body)
                self.assertFalse(check.ok)
                self.assertEqual(check.code, code)

    def test_expiry_consistency_notes(self):
        unrecorded = self.evaluate(introspection(), account(authorization_expires_at=None))
        self.assertTrue(unrecorded.ok)
        self.assertEqual(len(unrecorded.notes), 1)
        self.assertIn("linkedin_expiry_unrecorded", unrecorded.notes[0])
        self.assertIn("2026-12-01T00:00:00Z", unrecorded.notes[0])
        mismatch = self.evaluate(introspection(), account(authorization_expires_at="2027-01-15T00:00:00+00:00"))
        self.assertTrue(mismatch.ok)  # LinkedIn's value is authoritative; the config is only flagged
        self.assertTrue(mismatch.notes[0].startswith("WARNING: linkedin_expiry_mismatch"))
        close = self.evaluate(introspection(), account(authorization_expires_at="2026-12-01T06:00:00+00:00"))
        self.assertEqual(close.notes, ())


class VerifyWriteScopeTests(Base):
    def test_request_on_the_wire_is_form_post_without_secret_in_url(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            check = linkedin.verify_write_scope(account())
        self.assertTrue(check.ok)
        req = fake.requests[0]
        self.assertEqual((req.full_url, req.get_method()), ("https://www.linkedin.com/oauth/v2/introspectToken", "POST"))
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["content-type"], "application/x-www-form-urlencoded")
        self.assertNotIn("authorization", headers)
        self.assertEqual(parse_qs(req.data.decode()), {"client_id": [FAKE_CLIENT_ID],
                                                       "client_secret": [FAKE_APP_SECRET],
                                                       "token": [FAKE_TOKEN]})
        self.assertNotIn(FAKE_APP_SECRET, req.full_url)
        self.assert_no_secret(repr(check))

    def test_one_request_per_account_per_run_cached_in_memory(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            for _ in range(5):
                linkedin.verify_write_scope(account())
        self.assertEqual(fake.urls(), [linkedin.INTROSPECT_URL])

    def test_cached_refusal_is_not_requested_again(self):
        fake = FakeLinkedIn(body=introspection(scope="openid,profile"))
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            for _ in range(3):
                with self.assertRaisesRegex(ValidationError, "linkedin_scope_missing_w_member_social"):
                    linkedin.verify_write_scope(account())
        self.assertEqual(len(fake.requests), 1)

    def test_cache_expires(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            linkedin.verify_write_scope(account(), now=NOW)
            linkedin.verify_write_scope(account(), now=NOW + timedelta(seconds=linkedin.SCOPE_CACHE_SECONDS + 1))
        self.assertEqual(len(fake.requests), 2)

    def test_call_budget_bounds_requests_across_transient_failures(self):
        fake = FakeLinkedIn(error=http_error(503))
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            for _ in range(linkedin.MAX_INTROSPECTIONS_PER_ACCOUNT):
                with self.assertRaisesRegex(PublishError, "^linkedin_introspect_http_503$"):
                    linkedin.verify_write_scope(account())
            with self.assertRaisesRegex(ValidationError, "call_budget_exhausted"):
                linkedin.verify_write_scope(account())
        self.assertEqual(len(fake.requests), linkedin.MAX_INTROSPECTIONS_PER_ACCOUNT)

    def test_http_errors_are_sanitized_and_actionable(self):
        for code, needle in ((400, "linkedin_introspect_http_400: LinkedIn rejected the client id or the token"),
                             (401, "linkedin_introspect_http_401: LinkedIn rejected the client secret"),
                             (429, "linkedin_introspect_http_429"), (500, "linkedin_introspect_http_500")):
            linkedin.reset_scope_cache()
            with self.subTest(code=code), patch.dict(os.environ, ENV), \
                    patch("reachweaver.publishers._send_http", side_effect=FakeLinkedIn(error=http_error(code))):
                with self.assertRaises(PublishError) as ctx:
                    linkedin.verify_write_scope(account())
                self.assertTrue(str(ctx.exception).startswith(needle))
                self.assertEqual(ctx.exception.status, code)
                self.assert_no_secret(str(ctx.exception))
        linkedin.reset_scope_cache()
        with patch.dict(os.environ, ENV), \
                patch("reachweaver.publishers._send_http", side_effect=FakeLinkedIn(error=TimeoutError())), \
                self.assertRaisesRegex(PublishError, "^linkedin_introspect_network_or_timeout_uncertain$"):
            linkedin.verify_write_scope(account())

    def test_missing_app_credentials_refused_before_network(self):
        for missing in (PREFIX + "_CLIENT_ID", PREFIX + "_CLIENT_SECRET"):
            env = {k: v for k, v in ENV.items() if k != missing}
            with self.subTest(missing=missing), patch.dict(os.environ, env, clear=True), \
                    patch("reachweaver.publishers._send_http") as send:
                with self.assertRaisesRegex(ValidationError, "missing_credentials: " + missing):
                    linkedin.verify_write_scope(account())
                send.assert_not_called()

    def test_not_enabled_is_refused_and_never_requests(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            with self.assertRaisesRegex(ValidationError, "linkedin_scope_check_not_enabled"):
                linkedin.verify_write_scope(account(scope_check=None))
            send.assert_not_called()

    def test_write_refused_without_fresh_passing_check(self):
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http") as send:
            with self.assertRaisesRegex(ValidationError, "linkedin_write_scope_not_verified"):
                linkedin.publish_member_post(account(), "Hello", "p1")
            send.assert_not_called()


class ConnectionTests(Base):
    def test_opted_in_connection_reports_verified_write_permission(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            result = check_connection(account())
        self.assertEqual((result.identity, result.write_permission), ("verified", "verified"))
        self.assertEqual(fake.urls(), [linkedin.USERINFO_URL, linkedin.INTROSPECT_URL])
        self.assert_no_secret(repr(result))

    def test_disabled_is_unchanged(self):
        fake = FakeLinkedIn()
        env = {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}
        with patch.dict(os.environ, env, clear=True), patch("reachweaver.publishers._send_http", side_effect=fake):
            self.assertEqual(missing_credentials(account(scope_check=None)), [])
            result = check_connection(account(scope_check=None))
            remote_id, _ = publish(account(scope_check=None), "Library caller post", "p1")
        self.assertEqual((result.write_permission, result.notes), ("unverified", ()))
        self.assertEqual(fake.urls(), [linkedin.USERINFO_URL, linkedin.USERINFO_URL, linkedin.POSTS_URL])
        self.assertEqual(remote_id, "urn:li:share:6844785523593134080")

    def test_library_publish_runs_scope_check_before_write(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            publish(account(), "Library caller post", "p1")
        self.assertEqual(fake.urls(), [linkedin.USERINFO_URL, linkedin.INTROSPECT_URL, linkedin.POSTS_URL])

    def test_opted_in_missing_app_credentials_listed(self):
        with patch.dict(os.environ, {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN}, clear=True):
            self.assertEqual(missing_credentials(account()), [PREFIX + "_CLIENT_ID", PREFIX + "_CLIENT_SECRET"])


class CliTests(Base):
    def setUp(self):
        super().setUp()
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.db = Path(self.work.name) / "outbox.sqlite"
        self.config = Path(self.work.name) / "accounts.json"
        self.write_config(account())

    def write_config(self, *accounts):
        self.config.write_text(json.dumps({"accounts": list(accounts)}), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return status, out.getvalue(), err.getvalue()

    def approved_post(self):
        with connect(str(self.db)) as db:
            pid = add_post(db, "li-scope", "linkedin", "Evidence-backed release note: https://example.com")
            transition(db, pid, "approve")
        return pid

    def status_of(self, pid):
        with connect(str(self.db)) as db:
            return get_post(db, pid)["status"]

    def test_live_send_with_scope_proceeds(self):
        pid = self.approved_post()
        fake = FakeLinkedIn()
        with patch.dict(os.environ, LIVE), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn("PUBLISHED " + pid, out)
        self.assertEqual(fake.urls(), [linkedin.USERINFO_URL, linkedin.INTROSPECT_URL, linkedin.POSTS_URL])
        self.assertEqual(self.status_of(pid), "published")
        self.assert_no_secret(out + err)

    def refused_before_claim(self, fake, needle):
        pid = self.approved_post()
        with patch.dict(os.environ, LIVE), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn(f"BLOCKED {pid}: account identity preflight failed", out)
        self.assertIn(needle, out)
        self.assertNotIn(linkedin.POSTS_URL, fake.urls())  # nothing sent
        self.assertEqual(self.status_of(pid), "approved")  # not claimed; eligible after fixing the token
        self.assert_no_secret(out + err)
        return out

    def test_missing_scope_refused_pre_claim(self):
        self.refused_before_claim(FakeLinkedIn(body=introspection(scope="openid,profile")),
                                  "linkedin_scope_missing_w_member_social")

    def test_inactive_or_expired_refused_pre_claim(self):
        for body, needle in ((introspection(active=False, status="revoked"), "linkedin_token_revoked"),
                             (introspection(expires_at=int((NOW - timedelta(days=1)).timestamp())),
                              "linkedin_token_expired")):
            with self.subTest(needle=needle):
                linkedin.reset_scope_cache()
                self.refused_before_claim(FakeLinkedIn(body=body), needle)

    def test_http_errors_fail_closed_pre_claim(self):
        for code in (400, 401, 500):
            with self.subTest(code=code):
                linkedin.reset_scope_cache()
                fake = FakeLinkedIn(error=http_error(code))
                self.refused_before_claim(fake, f"linkedin_introspect_http_{code}")
                # A 5xx is a transient read: at most 2 preflight retries, so at most 3 introspections.
                self.assertLessEqual(fake.urls().count(linkedin.INTROSPECT_URL),
                                     linkedin.MAX_INTROSPECTIONS_PER_ACCOUNT)

    def test_disabled_live_send_unchanged(self):
        self.write_config(account(scope_check=None))
        pid = self.approved_post()
        fake = FakeLinkedIn()
        with patch.dict(os.environ, {PREFIX + "_ACCESS_TOKEN": FAKE_TOKEN, "REACHWEAVER_ALLOW_LIVE": "1",
                                     "REACHWEAVER_ENV": "prod"}, clear=True), \
                patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertEqual(fake.urls(), [linkedin.USERINFO_URL, linkedin.POSTS_URL])

    def test_expiry_mismatch_note_printed_without_secrets(self):
        self.write_config(account(authorization_expires_at="2027-03-01T00:00:00+00:00"))
        pid = self.approved_post()
        with patch.dict(os.environ, LIVE), patch("reachweaver.publishers._send_http", side_effect=FakeLinkedIn()):
            status, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(status, 0, out + err)
        self.assertIn(f"NOTE {pid}: WARNING: linkedin_expiry_mismatch", out)
        self.assert_no_secret(out + err)

    def test_connect_check_reports_scope_without_raw_response(self):
        fake = FakeLinkedIn()
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=fake):
            status, out, err = self.cli("connect-check", "--account", "li-scope", "--online")
        self.assertEqual(status, 0, out + err)
        result = json.loads(out)
        self.assertEqual(result["write_permission"], "verified")
        self.assertNotIn("scope", result)  # the scope list and the raw response are never printed
        self.assertNotIn(FAKE_CLIENT_ID, out)
        self.assert_no_secret(out + err)

    def test_doctor_explains_residual_risk_offline(self):
        self.write_config(account(scope_check=None))
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertEqual(status, 0)
        self.assertIn("INFO: linkedin_write_scope_unverified", out)
        self.assertIn('"scope_check": "introspect"', out)
        send.assert_not_called()
        self.write_config(account())
        with patch.dict(os.environ, {}, clear=True), patch("reachweaver.publishers._send_http") as send:
            status, out, _ = self.cli("doctor")
        self.assertIn(f"{PREFIX}_ACCESS_TOKEN, {PREFIX}_CLIENT_ID, {PREFIX}_CLIENT_SECRET", out)
        self.assertNotIn("linkedin_write_scope_unverified", out)
        send.assert_not_called()


class CredentialHandlingTests(Base):
    def load(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            path.write_text(json.dumps({"accounts": list(entries)}), encoding="utf-8")
            return load_accounts(path)

    def test_config_validation(self):
        self.assertEqual(self.load(account())["li-scope"]["scope_check"], "introspect")
        for bad, needle in ((account(scope_check="on"), "scope_check must be"),
                            (account(scope_check=True), "scope_check must be"),
                            ({"id": "my-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_B",
                              "handle": "a.bsky.social", "scope_check": "introspect"}, "only supported for native"),
                            (account(client_secret="x"), "Never put tokens")):
            with self.subTest(needle=needle), self.assertRaisesRegex(ValidationError, needle):
                self.load(bad)

    def test_credentials_audit_lists_new_names_and_never_values(self):
        accounts = {"li-scope": account(), "li-plain": account(id="li-plain", env_prefix="REACHWEAVER_TEST_LIPLAIN",
                                                                scope_check=None)}
        env = {**ENV, "REACHWEAVER_TEST_LIPLAIN_CLIENT_SECRET": "fake-unused-app-secret-4411"}
        report = audit_environment(accounts, env)
        rows = {r["account_id"]: r for r in report["accounts"]}
        self.assertEqual(rows["li-scope"]["set"], [PREFIX + "_ACCESS_TOKEN", PREFIX + "_CLIENT_ID",
                                                   PREFIX + "_CLIENT_SECRET"])
        self.assertEqual(rows["li-plain"]["missing"], ["REACHWEAVER_TEST_LIPLAIN_ACCESS_TOKEN"])
        # An app secret exported for an account that did not opt in is an unused secret on the host.
        orphans = [f for f in report["findings"] if f["code"] == "orphan_credential"]
        self.assertEqual(orphans[0]["variables"], ["REACHWEAVER_TEST_LIPLAIN_CLIENT_SECRET"])
        dumped = json.dumps(report) + render_audit(report)
        self.assert_no_secret(dumped)
        self.assertNotIn("fake-unused-app-secret-4411", dumped)

    def test_overlap_rule_covers_client_suffixes(self):
        first = {"id": "li-a", "platform": "linkedin", "env_prefix": "REACHWEAVER_APP"}
        second = {"id": "masto-b", "platform": "mastodon", "env_prefix": "REACHWEAVER_APP_CLIENT"}
        conflicts = env_prefix_conflicts([first, second])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("REACHWEAVER_APP_CLIENT_ID", conflicts[0]["variables"])

    def test_client_secret_env_value_is_redacted(self):
        env = {PREFIX + "_CLIENT_SECRET": FAKE_APP_SECRET}
        self.assertNotIn(FAKE_APP_SECRET, redact("leaked " + FAKE_APP_SECRET + " here", env=env))


class MissingScopeMessageTests(Base):
    """Operator-facing wording for a missing w_member_social, before and after the claim (issue #14)."""

    def test_introspection_refusal_names_code_and_fix_without_leaks(self):
        for scope in ("openid,profile", None, "w_member_social_x"):
            linkedin.reset_scope_cache()
            fake = FakeLinkedIn(body=introspection(scope=scope))
            with self.subTest(scope=scope), patch.dict(os.environ, ENV), \
                    patch("reachweaver.publishers._send_http", side_effect=fake):
                with self.assertRaises(ValidationError) as ctx:
                    linkedin.verify_write_scope(account())
                message = str(ctx.exception)
                self.assertTrue(message.startswith("linkedin_scope_missing_w_member_social: "), message)
                self.assertIn("Share on LinkedIn", message)
                self.assertIn("OAuth consent", message)
                self.assertNotIn("openid", message)  # the granted scope list is never echoed
                self.assert_no_secret(message)

    def test_userinfo_403_names_identity_scopes_not_write_scope(self):
        err = HTTPError(linkedin.USERINFO_URL + "?t=" + FAKE_TOKEN, 403, "Bearer " + FAKE_TOKEN, None, None)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=err), \
                self.assertRaises(PublishError) as ctx:
            linkedin.verify_identity(account(scope_check=None), now=NOW)
        message = str(ctx.exception)
        self.assertIn("openid profile", message)
        self.assertIn("identity cannot be checked", message)
        self.assert_no_secret(message)

    def test_post_403_without_opt_in_is_ambiguous_and_names_scope(self):
        err = HTTPError(linkedin.POSTS_URL + "?t=" + FAKE_TOKEN, 403, "Bearer " + FAKE_TOKEN, None, None)
        with patch.dict(os.environ, ENV), patch("reachweaver.publishers._send_http", side_effect=err), \
                patch.object(linkedin, "preflight", return_value="782bbtaQ"), \
                self.assertRaises(PublishError) as ctx:
            linkedin.publish_member_post(account(scope_check=None), "Hello", "p1", now=NOW)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("linkedin_http_403_uncertain: ACCESS_DENIED"), message)
        self.assertIn("w_member_social", message)
        self.assertIn("Share on LinkedIn", message)
        self.assertTrue(message.endswith("verify the profile before reconciling"))
        self.assert_no_secret(message)

    def test_send_without_fresh_scope_check_names_the_preflight(self):
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(
                ValidationError, "^linkedin_write_scope_not_verified: run the identity preflight"):
            linkedin.require_verified_write_scope(account(), now=NOW)


if __name__ == "__main__":
    unittest.main()
