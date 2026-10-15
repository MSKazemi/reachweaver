"""Postiz backend (issue #30): every HTTP exchange is mocked; nothing reaches any host."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from reachweaver import postiz
from reachweaver.accounts import load_accounts, missing_credentials
from reachweaver.cli import main
from reachweaver.connections import check_connection
from reachweaver.policy import ValidationError
from reachweaver.publishers import PublishError
from gate_support import publish  # noqa: E402
from reachweaver.storage import add_post, connect, event, get_post, set_paused, transition

FAKE_KEY = "pz-fake-key-for-tests-0001"
# One env_prefix per account (#5: load_accounts refuses identical or overlapping prefixes), so a
# Postiz workspace key used by several accounts is exported once per account prefix.
ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_PZ_POSTIZ_API_KEY": FAKE_KEY,
       "REACHWEAVER_PZLI_POSTIZ_API_KEY": FAKE_KEY, "REACHWEAVER_PZWRONG_POSTIZ_API_KEY": FAKE_KEY}
INTEGRATIONS = [
    {"id": "int-blue-1", "name": "Me", "identifier": "bluesky", "picture": "https://x/p.png",
     "disabled": False, "profile": "me.bsky.social", "customer": {"id": "c1", "name": "Mine"}},
    {"id": "int-li-2", "name": "Page", "identifier": "linkedin-page", "disabled": False, "profile": "acme"},
]
ACCOUNTS = {"accounts": [
    {"id": "pz-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZ", "handle": "me.bsky.social",
     "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-blue-1"},
    {"id": "pz-li", "platform": "linkedin", "env_prefix": "REACHWEAVER_PZLI", "handle": "acme",
     "backend": "postiz", "postiz_base_url": "https://postiz.example.org", "postiz_integration_id": "int-li-2"},
    {"id": "pz-wrong", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZWRONG", "handle": "someone.else",
     "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-blue-1"},
]}


class FakeResponse:
    def __init__(self, data):
        self.raw = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self.raw[:limit] if limit and limit > 0 else self.raw


class FakePostiz:
    """Records Request objects; scripted replies keyed by (method, path-prefix)."""

    def __init__(self, create=None, integrations=None, posts=None):
        self.requests = []
        self.create = create if create is not None else [{"postId": "pz-post-9", "integration": "int-blue-1"}]
        self.integrations = INTEGRATIONS if integrations is None else integrations
        self.posts = posts

    def __call__(self, req):
        self.requests.append(req)
        path = req.full_url.split("/v1", 1)[1]
        reply = self.create if req.get_method() == "POST" else (
            self.integrations if path.startswith("/integrations") else self.posts)
        if isinstance(reply, BaseException):
            raise reply
        return FakeResponse(reply)

    @property
    def creates(self):
        return [r for r in self.requests if r.get_method() == "POST"]


def http_error(code):
    return HTTPError("https://api.postiz.com/public/v1/posts", code, "err", {}, io.BytesIO(b'{"secret":"' + FAKE_KEY.encode() + b'"}'))


class PostizTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        root = Path(self.work.name)
        self.config, self.db = root / "accounts.json", root / "state" / "db.sqlite"
        self.config.write_text(json.dumps(ACCOUNTS))
        self.accounts = load_accounts(self.config)

    def approved(self, account="pz-blue", platform="bluesky", text="Release notes https://example.org/r"):
        with connect(str(self.db)) as db:
            pid = add_post(db, account, platform, text)
            transition(db, pid, "approve")
        return pid

    def cli(self, *args, env=ENV):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def status(self, pid):
        with connect(str(self.db)) as db:
            return get_post(db, pid)

    # --- configuration -------------------------------------------------
    def test_config_normalizes_prefix_and_credentials(self):
        self.assertEqual(self.accounts["pz-blue"]["postiz_api_prefix"], "/public/v1")
        self.assertEqual(self.accounts["pz-li"]["postiz_api_prefix"], "/api/public/v1")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(self.accounts["pz-blue"]), ["REACHWEAVER_PZ_POSTIZ_API_KEY"])

    def test_config_refusals(self):
        base = dict(ACCOUNTS["accounts"][0])
        cases = {
            "reddit": {**base, "platform": "reddit"},
            "x": {**base, "platform": "x"},
            "instagram": {**base, "platform": "instagram"},
            "backend": {**base, "backend": "buffer"},
            "http": {**base, "postiz_base_url": "http://api.postiz.com"},
            "localhost": {**base, "postiz_base_url": "https://localhost"},
            "ip": {**base, "postiz_base_url": "https://10.0.0.5"},
            "path": {**base, "postiz_base_url": "https://api.postiz.com/public/v1"},
            "prefix": {**base, "postiz_api_prefix": "/v2"},
            "integration": {**base, "postiz_integration_id": "../etc"},
            "handle": {**base, "handle": ""},
            "unknown-key": {**base, "postiz_allow_private_host": True},
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.config.write_text(json.dumps({"accounts": [raw]}))
                with self.assertRaises(ValidationError):
                    load_accounts(self.config)

    def test_capability_gate_is_fail_closed(self):
        self.assertEqual(postiz.require_backend_capability("linkedin")["id"], "linkedin")
        for platform in ("reddit", "x", "instagram", "pinterest", "youtube", "tiktok", "devto", "hashnode"):
            with self.subTest(platform), self.assertRaises(ValidationError):
                postiz.require_backend_capability(platform)

    def test_native_connect_check_refuses_backend_account(self):
        with self.assertRaisesRegex(ValidationError, "postiz-check"):
            check_connection(self.accounts["pz-blue"])

    # --- payload and identity ------------------------------------------
    def test_payload_shape_single_integration_exact_text(self):
        fake = FakePostiz()
        text = "Exact approved text\nwith https://example.org/a?b=1"
        with patch.dict(os.environ, ENV, clear=True), patch("reachweaver.publishers._send_http", fake), \
                self.assertRaises(postiz.BackendAccepted) as ctx:
            publish(self.accounts["pz-blue"], text, "local-1")
        self.assertEqual(ctx.exception.backend_ref, "postiz:pz-post-9")
        self.assertIsInstance(ctx.exception, PublishError)
        get, post = fake.requests
        self.assertEqual(get.full_url, "https://api.postiz.com/public/v1/integrations")
        self.assertEqual(post.full_url, "https://api.postiz.com/public/v1/posts")
        self.assertEqual(post.get_header("Authorization"), FAKE_KEY)  # raw key, no Bearer prefix
        body = json.loads(post.data)
        self.assertEqual(body["type"], "now")
        self.assertFalse(body["shortLink"])
        self.assertEqual(body["tags"], [])
        self.assertTrue(body["date"].endswith("Z"))
        self.assertEqual(body["posts"], [{"integration": {"id": "int-blue-1"},
                                          "value": [{"content": text, "image": []}],
                                          "settings": {"__type": "bluesky"}}])

    def test_self_hosted_prefix_and_observed_provider(self):
        fake = FakePostiz(create=[{"postId": "p2", "integration": "int-li-2"}])
        with patch.dict(os.environ, ENV, clear=True), patch("reachweaver.publishers._send_http", fake), \
                self.assertRaises(postiz.BackendAccepted):
            publish(self.accounts["pz-li"], "LinkedIn page update", "local-2")
        self.assertEqual(fake.requests[1].full_url, "https://postiz.example.org/api/public/v1/posts")
        self.assertEqual(json.loads(fake.requests[1].data)["posts"][0]["settings"], {"__type": "linkedin-page"})

    def test_identity_mismatches(self):
        rows = [{"id": r["id"], "identifier": r["identifier"], "profile": r["profile"], "disabled": r["disabled"]}
                for r in INTEGRATIONS]
        account = self.accounts["pz-blue"]
        with self.assertRaisesRegex(ValidationError, "profile"):
            postiz.match_integration(self.accounts["pz-wrong"], rows)
        with self.assertRaisesRegex(ValidationError, "not found"):
            postiz.match_integration({**account, "postiz_integration_id": "missing"}, rows)
        with self.assertRaisesRegex(ValidationError, "provider"):
            postiz.match_integration({**account, "postiz_integration_id": "int-li-2", "handle": "acme"}, rows)
        with self.assertRaisesRegex(ValidationError, "disabled"):
            postiz.match_integration(account, [{**rows[0], "disabled": True}])

    # --- CLI live flow --------------------------------------------------
    def test_integration_mismatch_refused_before_claim(self):
        pid = self.approved(account="pz-wrong")
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", out)
        self.assertEqual(fake.creates, [])
        self.assertEqual(self.status(pid)["status"], "approved")
        with connect(str(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)

    def test_acceptance_goes_to_needs_review_not_published(self):
        pid = self.approved()
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 3)
        self.assertIn("platform receipt unverified", out)
        row = self.status(pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]),
                         ("needs_review", "postiz:pz-post-9", None))
        self.assertEqual(row["last_error"], postiz.ACCEPTED_REASON)
        self.assertEqual(len(fake.creates), 1)
        # Never auto-retried: a second live run finds nothing due.
        with patch("reachweaver.publishers._send_http", fake):
            code, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertEqual(len(fake.creates), 1)
        code, out, _ = self.cli("report", env={})
        self.assertEqual(json.loads(out)["platforms"], {})

    def test_post_claim_failures_need_review_and_hide_key(self):
        failures = [http_error(500), http_error(502), http_error(429), http_error(401), http_error(403),
                    URLError("timeout"), TimeoutError(), [{"postId": "x", "integration": "other"}], {"weird": 1}, []]
        for number, failure in enumerate(failures):
            with self.subTest(failure=repr(failure)[:40]):
                with connect(str(self.db)) as db:  # isolate the rolling budget and the 429 cooldown (#11)
                    db.execute("DELETE FROM event_log WHERE action IN ('send_claimed','provider_cooldown')")
                    db.execute("UPDATE posts SET status='cancelled' WHERE status='needs_review'")  # #41: the previous iteration's hold
                pid = self.approved(text=f"Release candidate {number}")
                fake = FakePostiz(create=failure)
                with patch("reachweaver.publishers._send_http", fake):
                    code, out, err = self.cli("publish", "--live", "--post-id", pid)
                row = self.status(pid)
                self.assertEqual(code, 3, out)
                self.assertEqual(row["status"], "needs_review")
                self.assertTrue(row["last_error"].startswith("postiz:postiz_"))
                self.assertEqual(len(fake.creates), 1)
                with connect(str(self.db)) as db:
                    log = " ".join(r[0] for r in db.execute("SELECT detail FROM event_log"))
                for text in (out, err, row["last_error"], log):
                    self.assertNotIn(FAKE_KEY, text)
                    self.assertNotIn("secret", text)

    def test_preflight_http_errors_block_before_claim(self):
        for code_value in (401, 403, 429, 500):
            with self.subTest(code_value):
                with connect(str(self.db)) as db:  # a preflight 429 records a cooldown (#11); isolate it
                    db.execute("DELETE FROM event_log WHERE action='provider_cooldown'")
                pid = self.approved(text=f"Preflight {code_value}")
                fake = FakePostiz(integrations=http_error(code_value))
                # 429/500 on the read-only GET are retried twice (#11, D13); never really sleep.
                with patch("reachweaver.publishers._send_http", fake), patch("reachweaver.ratelimit._sleep"):
                    code, out, err = self.cli("publish", "--live", "--post-id", pid)
                self.assertEqual(code, 2)
                self.assertIn(f"postiz_http_{code_value}", out)
                self.assertNotIn(FAKE_KEY, out + err)
                self.assertEqual(self.status(pid)["status"], "approved")
                self.assertEqual(fake.creates, [])

    def test_missing_key_and_malformed_key_never_echoed(self):
        pid = self.approved()
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env={"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"})
            self.assertEqual(code, 2)
            self.assertIn("REACHWEAVER_PZ_POSTIZ_API_KEY", out)
            bad = "bad key\r\nX-Injected: 1"
            code, out, err = self.cli("publish", "--live", "--post-id", pid,
                                      env={"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_PZ_POSTIZ_API_KEY": bad})
        self.assertEqual(code, 2)
        self.assertNotIn(bad, out + err)
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.status(pid)["status"], "approved")

    def test_unapproved_post_never_sent(self):
        with connect(str(self.db)) as db:
            pid = add_post(db, "pz-blue", "bluesky", "Draft only, not approved")
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, _, err = self.cli("publish", "--live", "--post-id", pid)
            code_all, out_all, _ = self.cli("publish", "--live")
        self.assertEqual(code, 2)
        self.assertIn("not approved", err)
        self.assertEqual(code_all, 0)
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.status(pid)["status"], "draft")

    def test_pause_respected_cli_and_direct(self):
        pid = self.approved()
        with connect(str(self.db)) as db:
            set_paused(db, True)
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, _, err = self.cli("publish", "--live", "--post-id", pid)
            self.assertEqual(code, 2)
            self.assertIn("pause", err.lower())
            with connect(str(self.db)) as db, patch.dict(os.environ, ENV, clear=True), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(postiz.run_live_send(db, get_post(db, pid), self.accounts["pz-blue"],
                                                      max_per_24h=2, min_gap_minutes=60), 2)
        self.assertIn("pause", out.getvalue())
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.status(pid)["status"], "approved")

    def test_dry_run_never_contacts_postiz(self):
        pid = self.approved()
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, _ = self.cli("publish", "--post-id", pid)
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertEqual(fake.requests, [])

    def test_live_opt_in_env_still_required(self):
        pid = self.approved()
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, _, err = self.cli("publish", "--live", "--post-id", pid,
                                    env={"REACHWEAVER_PZ_POSTIZ_API_KEY": FAKE_KEY})
        self.assertEqual(code, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", err)
        self.assertEqual(fake.requests, [])

    # --- read-only CLI -----------------------------------------------------
    def test_postiz_check_requires_online_and_is_read_only(self):
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, _, err = self.cli("postiz-check", "--account", "pz-blue")
            self.assertEqual(code, 2)
            self.assertIn("--online", err)
            self.assertEqual(fake.requests, [])
            code, out, err = self.cli("postiz-check", "--account", "pz-blue", "--online")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["identity"], "verified")
        self.assertEqual([r["id"] for r in result["integrations"]], ["int-blue-1", "int-li-2"])
        self.assertNotIn("picture", out)
        self.assertNotIn(FAKE_KEY, out + err)
        self.assertTrue(all(r.get_method() == "GET" for r in fake.requests))

    def test_postiz_check_reports_mismatch(self):
        with patch("reachweaver.publishers._send_http", FakePostiz()):
            code, out, _ = self.cli("postiz-check", "--account", "pz-wrong", "--online")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["identity"], "mismatch")

    def test_receipt_lookup_is_backend_reported_and_never_reconciles(self):
        pid = self.approved()
        with patch("reachweaver.publishers._send_http", FakePostiz()):
            self.cli("publish", "--live", "--post-id", pid)
        posts = {"posts": [{"id": "pz-post-9", "state": "PUBLISHED", "content": "body never printed",
                            "releaseURL": "https://bsky.app/profile/me/post/abc"}]}
        fake = FakePostiz(posts=posts)
        with patch("reachweaver.publishers._send_http", fake):
            code, out, _ = self.cli("postiz-check", "--account", "pz-blue", "--online", "--post-id", pid)
        self.assertEqual(code, 0)
        lookup = json.loads(out)["receipt_lookup"]
        self.assertEqual((lookup["found"], lookup["state"], lookup["release_url"]),
                         (True, "PUBLISHED", "https://bsky.app/profile/me/post/abc"))
        self.assertNotIn("body never printed", out)
        self.assertIn("startDate=", fake.requests[-1].full_url)
        self.assertEqual(self.status(pid)["status"], "needs_review")  # human reconcile still required
        unsafe = {"posts": [{"id": "pz-post-9", "state": "PUBLISHED", "releaseURL": "javascript:alert(1)"}]}
        with patch("reachweaver.publishers._send_http", FakePostiz(posts=unsafe)):
            code, out, _ = self.cli("postiz-check", "--account", "pz-blue", "--online", "--post-id", pid)
        self.assertIsNone(json.loads(out)["receipt_lookup"]["release_url"])
        code, _, _ = self.cli("reconcile", pid, "--published", "--remote-id", "at://did:plc:me/app.bsky.feed.post/abc",
                              "--remote-url", "https://bsky.app/profile/me/post/abc", env={})
        self.assertEqual(code, 0)
        self.assertEqual(self.status(pid)["status"], "published")

    def test_doctor_shows_backend_offline(self):
        with patch("reachweaver.publishers._send_http", side_effect=AssertionError("network")):
            code, out, _ = self.cli("doctor", env={})
        self.assertEqual(code, 0)
        self.assertIn("pz-blue: bluesky; backend=postiz", out)
        self.assertIn("REACHWEAVER_PZ_POSTIZ_API_KEY", out)

    def test_native_accounts_unchanged(self):
        native = {"id": "blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_N", "handle": "a.bsky.social"}
        self.config.write_text(json.dumps({"accounts": [native]}))
        account = load_accounts(self.config)["blue"]
        self.assertNotIn("backend", account)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(missing_credentials(account), ["REACHWEAVER_N_IDENTIFIER", "REACHWEAVER_N_APP_PASSWORD"])

    def test_record_acceptance_only_from_sending(self):
        pid = self.approved()
        with connect(str(self.db)) as db:
            postiz.record_backend_acceptance(db, pid, "postiz:zzz")
            self.assertEqual(get_post(db, pid)["status"], "approved")
            event(db, "noop", pid)


if __name__ == "__main__":
    unittest.main()
