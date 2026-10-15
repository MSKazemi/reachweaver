"""Publishing-backend contract conformance (issue #30): the native path and the Postiz backend.

``publishers.PublishingBackend`` declares preview, create, receipt lookup and the reconciliation
hand-off. The same checks run against ``publishers.NATIVE_PATH`` (Bluesky, mocked at
``publishers.request_json``) and ``postiz.POSTIZ_BACKEND`` (synthetic fixtures from
``tests/fixtures/providers/postiz/`` replayed at ``publishers._send_http``). No network, no real
credential, no live send.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_support import grant  # noqa: E402
from test_postiz import FakePostiz, pz_fixture  # noqa: E402

from reachweaver import publishers  # noqa: E402
from reachweaver.accounts import load_accounts  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.policy import ValidationError  # noqa: E402
from reachweaver.postiz import POSTIZ_BACKEND, BackendAccepted  # noqa: E402
from reachweaver.publishers import NATIVE_PATH, PublishError, PublishingBackend, backend_for  # noqa: E402
from reachweaver.richtext import deterministic_tid  # noqa: E402
from reachweaver.storage import add_post, connect, get_post, transition  # noqa: E402

FAKE_KEY = "pz-fake-key-for-contract-0001"  # not a real credential
FAKE_APP_PASSWORD = "mock-contract-app-pw"  # not a real credential
ACCOUNTS = {"accounts": [
    {"id": "ct-native", "platform": "bluesky", "env_prefix": "REACHWEAVER_CTN", "handle": "owner.bsky.social"},
    {"id": "ct-postiz", "platform": "bluesky", "env_prefix": "REACHWEAVER_CTP", "handle": "fixture-user.example",
     "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-fixture-1"},
]}
CREDENTIALS = {"REACHWEAVER_CTN_IDENTIFIER": "owner.bsky.social", "REACHWEAVER_CTN_APP_PASSWORD": FAKE_APP_PASSWORD,
               "REACHWEAVER_CTP_POSTIZ_API_KEY": FAKE_KEY}
LIVE = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
TEXT = "Contract check: release notes https://example.org/r"
SESSION = {"did": "did:plc:owner123", "handle": "owner.bsky.social", "accessJwt": "mock-access"}
URI = "at://did:plc:owner123/app.bsky.feed.post/3kcontract2"
CREATED = {"uri": URI, "cid": "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"}


def no_network(*_args, **_kwargs):
    raise AssertionError("network used")


class ContractBase(unittest.TestCase):
    def setUp(self):
        publishers.reset_write_marker()
        self.addCleanup(publishers.reset_write_marker)
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.config = Path(work.name) / "accounts.json"
        self.db = str(Path(work.name) / "state.sqlite")
        self.config.write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.accounts = load_accounts(self.config)
        self.native, self.postiz = self.accounts["ct-native"], self.accounts["ct-postiz"]

    def paths(self):
        return ((NATIVE_PATH, self.native), (POSTIZ_BACKEND, self.postiz))

    @staticmethod
    def postiz_wire(**kwargs):
        return FakePostiz(integrations=pz_fixture("integrations-list.json")["body"],
                          create=pz_fixture("posts-create-accepted.json")["body"], **kwargs)


class ContractShapeTests(ContractBase):
    def test_both_paths_satisfy_the_protocol_and_the_account_selects_the_path(self):
        for backend, account in self.paths():
            with self.subTest(path=backend.name):
                self.assertIsInstance(backend, PublishingBackend)
                self.assertIs(backend_for(account), backend)
                self.assertEqual(backend.acceptance_status,
                                 "published" if backend.receipt_is_first_party else "needs_review")
        self.assertEqual((NATIVE_PATH.name, POSTIZ_BACKEND.name), ("native", "postiz"))
        self.assertFalse(POSTIZ_BACKEND.receipt_is_first_party)  # an acceptance is never a receipt
        with self.assertRaisesRegex(ValidationError, "only 'postiz'"):
            backend_for({**self.postiz, "backend": "buffer"})  # no implicit or unknown backend


class PreviewTests(ContractBase):
    def test_preview_is_offline_credential_free_and_byte_exact(self):
        with patch("reachweaver.publishers._send_http", side_effect=no_network), \
                patch("reachweaver.publishers.request_json", side_effect=no_network):
            for backend, account in self.paths():
                with self.subTest(path=backend.name):
                    shown = backend.preview(account, TEXT)
                    self.assertEqual((shown["path"], shown["content"], shown["network"]), (backend.name, TEXT, "none"))
                    self.assertEqual(shown["account_id"], account["id"])
                    self.assertNotIn(FAKE_KEY, json.dumps(shown))

    def test_preview_refuses_invalid_text_and_the_other_paths_account(self):
        for backend, account in self.paths():
            with self.subTest(path=backend.name):
                with self.assertRaises(ValidationError):
                    backend.preview(account, "x" * 301)  # over Bluesky's 300-grapheme limit
                other = self.postiz if backend is NATIVE_PATH else self.native
                with self.assertRaises(ValidationError):
                    backend.preview(other, TEXT)

    def test_postiz_preview_matches_the_request_create_sends(self):
        shown = POSTIZ_BACKEND.preview(self.postiz, TEXT)["request"]
        wire = self.postiz_wire()
        grant(self.postiz, TEXT, "ct-1")
        with patch.dict(os.environ, {**CREDENTIALS, **LIVE}), patch("reachweaver.publishers._send_http", wire), \
                self.assertRaises(BackendAccepted):
            POSTIZ_BACKEND.create(self.postiz, TEXT, "ct-1")
        [create] = wire.creates
        body = json.loads(create.data)
        self.assertEqual((create.get_method(), create.full_url), (shown["method"], shown["url"]))
        self.assertEqual((body["type"], body["shortLink"]), (shown["type"], shown["shortLink"]))
        self.assertEqual(body["posts"][0]["integration"]["id"], shown["integration_id"])
        self.assertEqual(body["posts"][0]["value"][0]["content"], TEXT)


class CreateTests(ContractBase):
    def test_create_without_a_durable_claim_sends_nothing(self):
        with patch.dict(os.environ, {**CREDENTIALS, **LIVE}), \
                patch("reachweaver.publishers._send_http", side_effect=no_network), \
                patch("reachweaver.publishers.request_json", side_effect=no_network):
            for backend, account in self.paths():
                with self.subTest(path=backend.name), self.assertRaisesRegex(ValidationError, "send_"):
                    backend.create(account, TEXT, f"unclaimed-{backend.name}")

    def test_native_create_returns_a_first_party_receipt(self):
        grant(self.native, TEXT, "ct-native-1")
        with patch.dict(os.environ, {**CREDENTIALS, **LIVE}), \
                patch("reachweaver.publishers.request_json", side_effect=[SESSION, SESSION, CREATED]) as request:
            receipt = NATIVE_PATH.create(self.native, TEXT, "ct-native-1")
        self.assertEqual(receipt[0], URI)
        self.assertTrue(receipt[1].startswith("https://bsky.app/profile/"))
        self.assertEqual(request.call_count, 3)
        self.assertTrue(publishers.write_started())

    def test_postiz_create_raises_an_acceptance_that_is_a_publish_error_after_one_create(self):
        grant(self.postiz, TEXT, "ct-postiz-1")
        wire = self.postiz_wire()
        with patch.dict(os.environ, {**CREDENTIALS, **LIVE}), patch("reachweaver.publishers._send_http", wire), \
                self.assertRaises(BackendAccepted) as accepted:
            POSTIZ_BACKEND.create(self.postiz, TEXT, "ct-postiz-1")
        self.assertIsInstance(accepted.exception, PublishError)  # a generic caller maps it to needs_review
        self.assertEqual(accepted.exception.backend_ref, "postiz:pz-fixture-job-1")
        self.assertEqual([r.get_method() for r in wire.requests], ["GET", "POST"])
        self.assertTrue(publishers.write_started())


class ReceiptLookupTests(ContractBase):
    def test_lookup_is_read_only_and_never_a_first_party_receipt(self):
        with patch("reachweaver.publishers._send_http", side_effect=no_network), \
                patch("reachweaver.publishers.request_json", side_effect=no_network):
            self.assertIsNone(NATIVE_PATH.lookup_receipt(self.native, URI, "2026-10-15T09:00:00+00:00"))
        wire = FakePostiz(posts=pz_fixture("posts-list-published.json")["body"])
        with patch.dict(os.environ, CREDENTIALS), patch("reachweaver.publishers._send_http", wire):
            found = POSTIZ_BACKEND.lookup_receipt(self.postiz, "postiz:pz-fixture-job-1", "2026-10-15T09:00:00+00:00")
        self.assertEqual(found, {"backend_ref": "postiz:pz-fixture-job-1", "found": True, "state": "PUBLISHED",
                                 "release_url": "https://bsky.app/profile/fixture-user.example/post/3kfixture22"})
        self.assertEqual([r.get_method() for r in wire.requests], ["GET"])
        self.assertNotIn("FIXTURE-BODY-MARKER", json.dumps(found))


class ReconciliationHandOffTests(ContractBase):
    """Through the real CLI: a successful create leaves the post in ``acceptance_status``; from
    ``needs_review`` only a human ``reconcile`` reaches ``published``."""

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def approved(self, account_id):
        with connect(self.db) as db:
            pid = add_post(db, account_id, "bluesky", TEXT)
            transition(db, pid, "approve")
        return pid

    def status(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)["status"]

    def test_each_path_ends_in_its_declared_acceptance_status(self):
        native = self.approved("ct-native")
        with connect(self.db) as db:
            rkey = deterministic_tid(native, get_post(db, native)["created_at"])  # the stable record key
        created = {**CREATED, "uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}"}
        with patch("reachweaver.publishers.request_json", side_effect=[SESSION, SESSION, created]):
            code, out, err = self.cli("publish", "--live", "--post-id", native, env={**CREDENTIALS, **LIVE})
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.status(native), NATIVE_PATH.acceptance_status)

        backend = self.approved("ct-postiz")
        with patch("reachweaver.publishers._send_http", self.postiz_wire()):
            code, out, err = self.cli("publish", "--live", "--post-id", backend, env={**CREDENTIALS, **LIVE})
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.status(backend), POSTIZ_BACKEND.acceptance_status)
        with patch("reachweaver.publishers._send_http", side_effect=no_network):
            code, _, err = self.cli("reconcile", backend, "--published", "--remote-id", URI.replace("3kcontract2", "3kfixture22"),
                                    "--remote-url", "https://bsky.app/profile/fixture-user.example/post/3kfixture22")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.status(backend), "published")


if __name__ == "__main__":
    unittest.main()
