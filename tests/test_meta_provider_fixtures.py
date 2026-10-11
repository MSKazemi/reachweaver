"""Instagram (#20) and Facebook Page (#21) provider-failure fixtures, replayed offline.

Synthetic Graph-shaped responses in ``tests/fixtures/providers/meta/`` (not recordings) cover an
unsupported consumer account, missing permissions, app-review-required, bad media, asynchronous
container processing and a Page mismatch. Every error body carries ``REMOTE_MARKER``: the adapters
read status codes only, so the marker must never reach an error string. Both adapters stay
registry-disabled; the flipped registry is a temp copy exactly as in test_instagram/test_facebook.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))  # Reuse sibling mocks under any runner.
import test_facebook as tf  # noqa: E402
import test_instagram as ti  # noqa: E402
from reachweaver.capabilities import load_registry  # noqa: E402
from reachweaver.connections import check_connection  # noqa: E402
from reachweaver.policy import SUPPORTED_LIVE, ValidationError  # noqa: E402
from reachweaver.publishers import PUBLISHERS, PublishError, reset_write_marker, write_started
from gate_support import publish  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers" / "meta"
REMOTE_MARKER = "REMOTE-TEXT-MUST-NOT-LEAK"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def serve(fake, route_of, route, name):
    """Wrap a mock so the given route answers with the fixture (an error status raises HTTPError)."""
    fx = fixture(name)

    def call(req):
        if route_of(req) != route:
            return fake(req)
        fake.requests.append(req)
        fake.routes.append(route)
        if fx["status"] >= 400:
            raise HTTPError(req.full_url, fx["status"], "mock", Message(), BytesIO(json.dumps(fx["body"]).encode()))
        return ti.FakeResponse(200, json.dumps(fx["body"]).encode())
    return call


class FixtureHygieneTests(unittest.TestCase):
    def test_fixtures_are_synthetic_and_carry_no_credential(self):
        names = sorted(p.name for p in FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(names), 13)
        for name in names:
            with self.subTest(fixture=name):
                fx = fixture(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                text = json.dumps(fx).lower()
                for word in ("access_token", "bearer", "authorization"):
                    self.assertNotIn(word, text)
                if fx["status"] >= 400:
                    self.assertIn(REMOTE_MARKER, fx["body"]["error"]["message"])

    def test_both_adapters_stay_registry_disabled(self):
        for pid in ("instagram", "facebook"):
            entry = next(e for e in load_registry()["platforms"] if e["id"] == pid)
            self.assertEqual((entry["reachweaver_adapter"], entry["status"]), ("none", "requires-review"))
            self.assertNotIn(pid, SUPPORTED_LIVE)
            self.assertIn(pid, PUBLISHERS)
        with patch("reachweaver.publishers._send_http") as send, patch.dict(os.environ, {**ti.ENV, **tf.ENV}):
            with self.assertRaises(ValidationError):
                publish(ti.account(), ti.CAPTION, "p1", media=[ti.record()])
            with self.assertRaises(ValidationError):
                publish(tf.account(), "Hello Page", "p1")
        send.assert_not_called()


class InstagramFixtureTests(ti.FlippedRegistry):
    def run_with(self, fake, wrapped=None):
        with patch.dict(os.environ, ti.ENV), patch("reachweaver.publishers._send_http", side_effect=wrapped or fake):
            return publish(ti.account(), ti.CAPTION, "p1", media=[ti.record()])

    def assert_clean(self, exc):
        self.assertNotIn(REMOTE_MARKER, repr(exc))
        self.assertNotIn(ti.FAKE_TOKEN, repr(exc))

    def test_consumer_account_is_refused_before_any_write(self):
        fake = ti.FakeMeta()
        with self.assertRaisesRegex(PublishError, "^instagram_http_400: request refused") as ctx:
            self.run_with(fake, serve(fake, fake.route_of, "me", "ig-me-400-consumer-account.json"))
        self.assert_clean(ctx.exception)
        self.assertEqual(fake.routes, ["me"])
        self.assertFalse(write_started())
        personal = ti.FakeMeta(me=fixture("ig-me-200-personal-account.json")["body"])
        with self.assertRaisesRegex(ValidationError, "not_professional"):
            self.run_with(personal)
        self.assertEqual(personal.routes, ["me"])
        self.assertFalse(write_started())

    def test_missing_permission_and_app_review_required_are_pre_write(self):
        for name in ("ig-quota-403-missing-permission.json", "ig-quota-403-app-review-required.json"):
            fake = ti.FakeMeta()
            with self.subTest(fixture=name), self.assertRaisesRegex(
                    PublishError, "lacks instagram_business_content_publish; nothing was sent") as ctx:
                self.run_with(fake, serve(fake, fake.route_of, "quota", name))
            self.assertEqual(ctx.exception.status, 403)
            self.assert_clean(ctx.exception)
            self.assertEqual(fake.routes, ["me", "quota"])
            self.assertFalse(write_started())

    def test_bad_media_rejected_at_container_is_uncertain_and_never_retried(self):
        for name in ("ig-create-400-bad-media-url.json", "ig-create-400-aspect-ratio.json"):
            reset_write_marker()
            fake = ti.FakeMeta()
            with self.subTest(fixture=name), self.assertRaisesRegex(
                    PublishError, "^instagram_create_http_400_uncertain: container refused") as ctx:
                self.run_with(fake, serve(fake, fake.route_of, "create", name))
            self.assert_clean(ctx.exception)
            self.assertTrue(write_started())
            self.assertEqual(fake.routes.count("create"), 1)
            self.assertNotIn("publish", fake.routes)

    def test_async_container_processing_from_fixtures(self):
        progress = fixture("ig-container-in-progress.json")["body"]["status_code"]
        fake = ti.FakeMeta(statuses=(progress, progress, "FINISHED"))
        self.assertEqual(self.run_with(fake)[0], ti.MEDIA_ID)
        self.assertEqual(self.sleeps, [60, 60])
        self.assertEqual(fake.routes.count("status"), 3)
        failed = fixture("ig-container-error.json")["body"]["status_code"]
        reset_write_marker()
        fake = ti.FakeMeta(statuses=(progress, failed))
        with self.assertRaisesRegex(PublishError, "^instagram_container_error_not_public_uncertain$"):
            self.run_with(fake)
        self.assertNotIn("publish", fake.routes)
        self.assertTrue(write_started())

    def test_connection_check_reports_missing_permission_without_remote_text(self):
        fake = ti.FakeMeta()
        wrapped = serve(fake, fake.route_of, "quota", "ig-quota-403-missing-permission.json")
        with patch.dict(os.environ, ti.ENV), patch("reachweaver.publishers._send_http", side_effect=wrapped), \
                self.assertRaises(PublishError) as ctx:
            check_connection(ti.account())
        self.assert_clean(ctx.exception)
        self.assertEqual(ctx.exception.status, 403)


class FacebookFixtureTests(tf.FlippedRegistry):
    def run_with(self, fake, wrapped=None):
        with patch.dict(os.environ, tf.ENV), patch("reachweaver.publishers._send_http", side_effect=wrapped or fake):
            return publish(tf.account(), tf.TEXT, "p1")

    @staticmethod
    def route_of(req):
        return tf.FakeGraph.ROUTES.get((req.get_method(), urlsplit(req.full_url).path))

    def serve(self, fake, route, name):
        return serve(fake, self.route_of, route, name)

    def assert_clean(self, exc):
        self.assertNotIn(REMOTE_MARKER, repr(exc))
        self.assertNotIn(tf.FAKE_TOKEN, repr(exc))

    def test_missing_permission_and_expired_token_are_pre_write(self):
        for name, status, needle in (("fb-me-403-missing-permission.json", 403, "^facebook_http_403: token refused"),
                                     ("fb-me-400-expired-token.json", 400, "^facebook_http_400: request refused")):
            fake = tf.FakeGraph()
            with self.subTest(fixture=name), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_with(fake, self.serve(fake, "me", name))
            self.assertEqual(ctx.exception.status, status)
            self.assert_clean(ctx.exception)
            self.assertEqual(fake.routes, ["me"])
            self.assertFalse(write_started())

    def test_page_mismatch_fixture_is_refused_before_any_write(self):
        fake = tf.FakeGraph(me=fixture("fb-me-200-page-mismatch.json")["body"])
        with self.assertRaisesRegex(ValidationError, "identity_mismatch"):
            self.run_with(fake)
        self.assertEqual(fake.routes, ["me"])
        self.assertFalse(write_started())

    def test_review_required_and_bad_link_on_feed_are_uncertain_and_never_retried(self):
        for name, status, needle in (("fb-feed-403-app-review-required.json", 403, "^facebook_feed_http_403_uncertain$"),
                                     ("fb-feed-400-bad-link.json", 400, "^facebook_feed_http_400_uncertain: post refused")):
            reset_write_marker()
            fake = tf.FakeGraph()
            with self.subTest(fixture=name), self.assertRaisesRegex(PublishError, needle) as ctx:
                self.run_with(fake, self.serve(fake, "feed", name))
            self.assertEqual(ctx.exception.status, status)
            self.assert_clean(ctx.exception)
            self.assertTrue(write_started())
            self.assertEqual(fake.routes.count("feed"), 1)
            self.assertNotIn("post", fake.routes)


if __name__ == "__main__":
    unittest.main()
