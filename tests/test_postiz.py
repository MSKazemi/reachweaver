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
from gate_support import bound_send_options, publish  # noqa: E402
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
            # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
            transition(db, pid, "approve", send_options=bound_send_options(self.config, account))
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


PZ_FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "postiz"
FX_ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_PZFX_POSTIZ_API_KEY": FAKE_KEY}
FX_ACCOUNT = {"id": "pz-fx", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZFX", "handle": "fixture-user.example",
              "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-fixture-1"}
BODY_MARKER = "FIXTURE-BODY-MARKER"


def pz_fixture(name):
    return json.loads((PZ_FIXTURES / name).read_text(encoding="utf-8"))


def pz_fixture_error(name):
    """The fixture as the HTTPError urllib would raise; its body and reason carry markers that must never surface."""
    fx = pz_fixture(name)
    body = json.dumps(fx["body"]).encode("utf-8")
    return HTTPError("https://api.postiz.com/public/v1/posts", fx["status"], BODY_MARKER + " " + FAKE_KEY,
                     fx["headers"], io.BytesIO(body))


class PostizFixtureTests(unittest.TestCase):
    """Issue #30 acceptance 2d (duplicate job) and 2f (missing scope), replayed from synthetic fixtures in
    ``tests/fixtures/providers/postiz/``. Postiz documents no idempotency key for ``POST /posts``, so the
    only guarantee pinned here is ReachWeaver's: one durable claim per approved post, one create request,
    and never a retry."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        root = Path(self.work.name)
        self.config, self.db = root / "accounts.json", root / "state" / "db.sqlite"
        self.config.write_text(json.dumps({"accounts": [FX_ACCOUNT]}))

    def approved(self, text="Fixture release notes https://example.org/r"):
        with connect(str(self.db)) as db:
            pid = add_post(db, "pz-fx", "bluesky", text)
            # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
            transition(db, pid, "approve", send_options=bound_send_options(self.config, "pz-fx"))
        return pid

    def live(self, fake, pid):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, FX_ENV, clear=True), patch("reachweaver.publishers._send_http", fake), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.config), "publish", "--live", "--post-id", pid])
        return code, out.getvalue(), err.getvalue()

    def row(self, pid):
        with connect(str(self.db)) as db:
            return get_post(db, pid)

    def actions(self, pid):
        with connect(str(self.db)) as db:
            return [r[0] for r in db.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id", (pid,))]

    def log(self):
        with connect(str(self.db)) as db:
            return " ".join(str(r[0]) for r in db.execute("SELECT detail FROM event_log"))

    def assert_nothing_leaks(self, *texts):
        for text in (*texts, self.log()):
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn(BODY_MARKER, text)

    def assert_second_run_sends_nothing(self, pid):
        again = FakePostiz(integrations=pz_fixture("integrations-list.json")["body"])
        code, _, _ = self.live(again, pid)
        self.assertEqual((code, again.requests), (2, []))  # not due: no preflight, no create

    def test_fixtures_are_synthetic_and_carry_no_credential(self):
        names = sorted(p.name for p in PZ_FIXTURES.glob("*.json"))
        for name in ("integrations-list.json", "integrations-disabled.json", "posts-create-accepted.json",
                     "posts-create-two-jobs.json", "posts-403-missing-scope.json", "posts-list-published.json"):
            self.assertIn(name, names)
        for name in names:
            with self.subTest(fixture=name):
                fx = pz_fixture(name)
                self.assertIs(fx["_fixture"]["recorded"], False)
                self.assertNotIn("Authorization", json.dumps(fx))
                self.assertNotIn(FAKE_KEY, json.dumps(fx))

    def test_create_returning_two_jobs_is_a_possible_duplicate_for_review_never_retried(self):
        pid = self.approved()
        fake = FakePostiz(integrations=pz_fixture("integrations-list.json")["body"],
                          create=pz_fixture("posts-create-two-jobs.json")["body"])
        code, out, err = self.live(fake, pid)
        self.assertEqual(code, 3, out + err)
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"], row["remote_id"]),
                         ("needs_review", "postiz:postiz_unexpected_create_response_uncertain", None))
        self.assertIn("[ambiguous]", out)  # the write started: the jobs may be live
        self.assertEqual(len(fake.creates), 1)  # one POST /posts, no retry, no delete
        self.assertNotIn("pz-fixture-job", out + err + self.log())  # neither job is treated as the receipt
        self.assertEqual(self.actions(pid).count("send_claimed"), 1)
        self.assert_second_run_sends_nothing(pid)
        self.assert_nothing_leaks(out, err)

    def test_two_runs_racing_on_one_approved_post_send_one_create(self):
        """Both runs pass the read-only preflight; the other process wins the claim first."""
        from reachweaver import storage
        pid = self.approved()
        listing = pz_fixture("integrations-list.json")["body"]
        winner = FakePostiz(integrations=listing, create=pz_fixture("posts-create-accepted.json")["body"])
        loser = FakePostiz(integrations=listing, create=pz_fixture("posts-create-accepted.json")["body"])
        real_claim, winner_result = storage.claim_sending, []

        def racing_claim(con, post_id, *args, **kwargs):
            if not winner_result:
                with patch("reachweaver.storage.claim_sending", real_claim):
                    winner_result.append(self.live(winner, post_id))
            return real_claim(con, post_id, *args, **kwargs)

        with patch("reachweaver.storage.claim_sending", racing_claim):
            code, out, err = self.live(loser, pid)
        self.assertEqual(code, 2, out + err)
        self.assertIn(f"SKIPPED {pid}: no longer eligible", out)
        self.assertEqual([r.get_method() for r in loser.requests], ["GET"])  # preflight only
        [(winner_code, winner_out, _)] = winner_result
        self.assertEqual(winner_code, 3, winner_out)  # a Postiz acceptance is needs_review, as always
        self.assertEqual(len(winner.creates) + len(loser.creates), 1)
        row = self.row(pid)
        self.assertEqual((row["status"], row["remote_id"]), ("needs_review", "postiz:pz-fixture-job-1"))
        self.assertEqual(self.actions(pid).count("send_claimed"), 1)
        self.assert_nothing_leaks(out, err, winner_out)

    def test_scope_shaped_403_on_create_needs_review_redacts_and_never_retries(self):
        """A platform grant without write scope cannot be detected beforehand (POSTIZ-SCOPE-DETECTION.md);
        the 403 arrives on the create itself and is handled like any post-claim failure."""
        pid = self.approved()
        fake = FakePostiz(integrations=pz_fixture("integrations-list.json")["body"],
                          create=pz_fixture_error("posts-403-missing-scope.json"))
        code, out, err = self.live(fake, pid)
        self.assertEqual(code, 3, out + err)
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"], row["remote_id"]),
                         ("needs_review", "postiz:postiz_http_403_forbidden", None))
        self.assertIn("[ambiguous]", out)
        self.assertEqual(len(fake.creates), 1)
        self.assertNotIn("provider_cooldown", self.actions(pid))  # a 403 is not a rate limit
        self.assert_second_run_sends_nothing(pid)
        self.assert_nothing_leaks(out, err, row["last_error"])

    def test_disabled_integration_is_blocked_before_the_claim_through_the_cli(self):
        pid = self.approved()
        fake = FakePostiz(integrations=pz_fixture("integrations-disabled.json")["body"])
        code, out, err = self.live(fake, pid)
        self.assertEqual(code, 2, out + err)
        self.assertIn(f"BLOCKED {pid}: Postiz backend preflight failed (postiz_integration_disabled)", out)
        self.assertEqual([r.get_method() for r in fake.requests], ["GET"])
        self.assertEqual(fake.creates, [])
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertNotIn("send_claimed", self.actions(pid))
        self.assert_nothing_leaks(out, err)


class PausingPostiz(FakePostiz):
    """The operator runs ``reachweaver pause`` while the read-only GET /integrations is in flight."""

    def __init__(self, db_path, **kwargs):
        super().__init__(**kwargs)
        self.db_path = db_path

    def __call__(self, req):
        if req.get_method() == "GET" and "/integrations" in req.full_url:
            with connect(self.db_path) as con:
                set_paused(con, True)
        return super().__call__(req)


class PostizIssue56Tests(PostizTests):
    """Issue #56 (roadmap #30) acceptance gaps closed offline: paused queue mid-preflight, backend job states,
    credential compartments, the inspectable offline preview in ``show`` and the manual fallback. Every HTTP
    exchange is a fake; no test reaches a host. Inherits the fixtures of ``PostizTests`` only; the inherited
    test methods are excluded below so they run once."""

    def requests_text(self, fake):
        return " ".join(f"{r.full_url} {dict(r.header_items())} {r.data!r}" for r in fake.requests)

    # --- paused queue ------------------------------------------------------
    def test_pause_during_the_preflight_stops_the_send_at_the_claim(self):
        pid = self.approved()
        fake = PausingPostiz(str(self.db))
        with patch("reachweaver.publishers._send_http", fake):
            code, out, err = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2, out + err)
        self.assertIn("pause", (out + err).lower())
        self.assertEqual([r.get_method() for r in fake.requests], ["GET"])  # preflight only, no POST /posts
        self.assertEqual(self.status(pid)["status"], "approved")
        with connect(str(self.db)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)

    # --- observed remote id and permalink ----------------------------------
    def test_backend_job_states_are_reported_and_never_reconcile(self):
        pid = self.approved()
        with patch("reachweaver.publishers._send_http", FakePostiz()):
            self.cli("publish", "--live", "--post-id", pid)
        cases = {
            "queued": ({"posts": [{"id": "pz-post-9", "state": "QUEUE"}]}, (True, "QUEUE", None)),
            "error": ({"posts": [{"id": "pz-post-9", "state": "ERROR", "error": "provider said SECRET-DETAIL",
                                  "releaseURL": ""}]}, (True, "ERROR", None)),
            "missing": ({"posts": [{"id": "someone-else", "state": "PUBLISHED"}]}, (False, None, None)),
            "unrecognised": ({"posts": [{"id": "pz-post-9", "state": "PAUSED"}]}, (True, "unknown", None)),
        }
        for name, (reply, expected) in cases.items():
            with self.subTest(name):
                fake = FakePostiz(posts=reply)
                with patch("reachweaver.publishers._send_http", fake):
                    code, out, err = self.cli("postiz-check", "--account", "pz-blue", "--online", "--post-id", pid)
                self.assertEqual(code, 0, out + err)
                lookup = json.loads(out)["receipt_lookup"]
                self.assertEqual((lookup["found"], lookup["state"], lookup["release_url"]), expected)
                self.assertEqual(lookup["backend_ref"], "postiz:pz-post-9")
                self.assertNotIn("SECRET-DETAIL", out + err)
                self.assertTrue(all(r.get_method() == "GET" for r in fake.requests))
                self.assertEqual(self.status(pid)["status"], "needs_review")

    def test_lookup_refuses_an_ambiguous_post_without_a_backend_job_id(self):
        pid = self.approved()
        with patch("reachweaver.publishers._send_http", FakePostiz(create=http_error(502))):
            self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual((self.status(pid)["status"], self.status(pid)["remote_id"]), ("needs_review", None))
        fake = FakePostiz(posts={"posts": [{"id": "anything", "state": "PUBLISHED"}]})
        with patch("reachweaver.publishers._send_http", fake):
            code, _, err = self.cli("postiz-check", "--account", "pz-blue", "--online", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("no Postiz backend reference", err)
        self.assertFalse(any("/posts" in r.full_url for r in fake.requests))  # never guesses a job

    def test_reconcile_stores_the_observed_native_id_and_permalink(self):
        pid = self.approved()
        with patch("reachweaver.publishers._send_http", FakePostiz()):
            self.cli("publish", "--live", "--post-id", pid)
        native_id = "at://did:plc:me/app.bsky.feed.post/abc"
        permalink = "https://bsky.app/profile/me.bsky.social/post/abc"
        code, _, err = self.cli("reconcile", pid, "--published", "--remote-id", native_id, "--remote-url", permalink, env={})
        self.assertEqual(code, 0, err)
        row = self.status(pid)
        self.assertEqual((row["status"], row["remote_id"], row["remote_url"]), ("published", native_id, permalink))
        with connect(str(self.db)) as db:
            actions = [r[0] for r in db.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id", (pid,))]
        self.assertEqual(actions.count("send_claimed"), 1)
        self.assertIn("reconciled_published", actions)

    # --- credential compartments -------------------------------------------
    def test_each_backend_account_sends_only_its_own_key(self):
        other_key = "pz-other-workspace-key-0002"  # not a real credential
        native_marker = "native-app-pw-marker"  # not a real credential; must never be sent
        env = {**ENV, "REACHWEAVER_PZLI_POSTIZ_API_KEY": other_key, "REACHWEAVER_N_APP_PASSWORD": native_marker}
        pid = self.approved(account="pz-li", platform="linkedin", text="Compartment check")
        fake = FakePostiz(create=[{"postId": "p-li", "integration": "int-li-2"}])
        with patch("reachweaver.publishers._send_http", fake):
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=env)
        self.assertEqual(code, 3, out + err)
        self.assertEqual({r.get_header("Authorization") for r in fake.requests}, {other_key})
        sent = self.requests_text(fake)
        self.assertNotIn(FAKE_KEY, sent)  # pz-blue's key never travels with pz-li's requests
        self.assertNotIn(native_marker, sent)
        self.assertNotIn(other_key, out + err)

    def test_another_accounts_key_never_substitutes_for_a_missing_one(self):
        pid = self.approved(account="pz-li", platform="linkedin", text="Missing own key")
        env = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod", "REACHWEAVER_PZ_POSTIZ_API_KEY": FAKE_KEY}
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=env)
        self.assertEqual(code, 2, out + err)
        self.assertIn("REACHWEAVER_PZLI_POSTIZ_API_KEY", out)
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.status(pid)["status"], "approved")

    # --- inspectable offline preview and manual fallback --------------------
    def show(self, pid, env=None):
        with patch("reachweaver.publishers._send_http", side_effect=AssertionError("network")), \
                patch("reachweaver.publishers.request_json", side_effect=AssertionError("network")):
            return self.cli("show", pid, env=env or {})

    def test_show_previews_the_postiz_request_not_a_native_record(self):
        text = "Exact approved text https://example.org/a?b=1"
        pid = self.approved(text=text)
        code, out, err = self.show(pid)
        self.assertEqual(code, 0, err)
        shown = json.loads(out)
        self.assertEqual(shown["content"], text)
        preview = shown["platform_preview"]
        self.assertEqual(preview["path"], "postiz")
        self.assertEqual(preview["request"], {"method": "POST", "url": "https://api.postiz.com/public/v1/posts",
                                              "type": "now", "shortLink": False, "integration_id": "int-blue-1"})
        self.assertEqual((preview["network"], preview["handle"]), ("none", "me.bsky.social"))
        self.assertNotIn("record_key", preview)  # the native Bluesky createRecord is not what this account sends
        self.assertNotIn("service", preview)
        self.assertNotIn("content", preview)
        self.assertIn("needs_review", preview["receipt"])
        self.assertIn("cancel", preview["manual_fallback"])
        _, keyed_out, _ = self.show(pid, env={"REACHWEAVER_PZ_POSTIZ_API_KEY": FAKE_KEY})
        self.assertNotIn(FAKE_KEY, keyed_out)

    def test_native_show_preview_is_unchanged(self):
        native = {"id": "blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_N", "handle": "a.bsky.social"}
        self.config.write_text(json.dumps({"accounts": [native]}))
        pid = self.approved(account="blue")
        code, out, err = self.show(pid)
        self.assertEqual(code, 0, err)
        preview = json.loads(out)["platform_preview"]
        self.assertNotIn("path", preview)
        self.assertIn("record_key", preview)
        self.assertEqual(preview["service"], "https://bsky.social")

    def test_postiz_unavailable_leaves_the_post_for_a_manual_fallback(self):
        text = "Manual fallback text https://example.org/r"
        pid = self.approved(text=text)
        down = FakePostiz(integrations=URLError("unreachable"))
        with patch("reachweaver.publishers._send_http", down), patch("reachweaver.ratelimit._sleep"):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("postiz_network_or_timeout_uncertain", out)
        self.assertEqual(down.creates, [])
        self.assertEqual(self.status(pid)["status"], "approved")
        code, out, _ = self.show(pid)  # exact text and destination, offline, for the owner to post natively
        self.assertEqual(json.loads(out)["content"], text)
        code, _, err = self.cli("cancel", pid, env={})
        self.assertEqual(code, 0, err)
        again = FakePostiz()
        with patch("reachweaver.publishers._send_http", again):
            code, _, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual((code, again.requests), (2, []))  # a cancelled post is never sent later


for _inherited in [name for name in vars(PostizTests) if name.startswith("test_")]:
    setattr(PostizIssue56Tests, _inherited, None)  # run the parent's tests once, in PostizTests


class PostizIssue56DocTests(unittest.TestCase):
    """The smoke test is documented as a ready procedure and not claimed; licensing and credential review dated."""

    root = Path(__file__).resolve().parents[1]

    def doc(self):
        return (self.root / "docs" / "POSTIZ.md").read_text(encoding="utf-8")

    def test_smoke_test_procedure_exists_and_is_not_claimed_without_a_receipt(self):
        text = self.doc()
        self.assertIn("## Owner smoke test", text)
        for step in ("postiz-check --account", "publish --live --post-id", "reconcile POST_ID --published",
                     "pause"):
            self.assertIn(step, text)
        if not list((self.root / "docs" / "receipts").glob("postiz*")):
            self.assertIn("**Status: NOT RUN.**", text)

    def test_licensing_and_credential_review_are_dated_and_sourced(self):
        text = self.doc()
        self.assertIn("## Licensing and credential review (2026-10-15)", text)
        for needle in ("https://github.com/gitroomhq/postiz-app/blob/main/LICENSE", "AGPL-3.0",
                       "https://postiz.com/terms", "{env_prefix}_POSTIZ_API_KEY", "Apache-2.0"):
            self.assertIn(needle, text)


class LicenseWordingTests(unittest.TestCase):
    """Issue #30 licensing record: ReachWeaver is Apache-2.0, so no skill copy may call it MIT code."""

    def test_skill_publishing_reference_names_the_project_license(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn("Apache License", (root / "LICENSE").read_text(encoding="utf-8")[:200])
        for copy in (".claude/skills/reachweaver/references/publishing.md",
                     "plugin/skills/reachweaver/references/publishing.md"):
            with self.subTest(copy=copy):
                text = (root / copy).read_text(encoding="utf-8")
                self.assertIn("do not copy AGPL internals into ReachWeaver's Apache-2.0 code", text)
                self.assertNotIn("MIT code", text)


if __name__ == "__main__":
    unittest.main()
