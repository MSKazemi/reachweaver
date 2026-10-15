"""TikTok: scope-denied and rate-limit cases at every stage, and the visibility the owner sees (public issue #53).

Mocked network only; reuses the harness of ``tests/test_tiktok.py``. These tests pin existing behaviour.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from reachweaver.storage import active_provider_cooldown, connect
from tests import test_tiktok as t


class ScopeDenied(t.FakeTikTok):
    """TikTok answers 200 with an API error body (``scope_not_authorized``) on one route."""

    def __init__(self, deny, **kw):
        super().__init__(**kw)
        self.deny = deny

    def __call__(self, req):
        parts = urlsplit(req.full_url)
        route = self.ROUTES.get((req.get_method(), parts.hostname, parts.path))
        if route != self.deny:
            return super().__call__(req)
        self.requests.append(req)
        self.routes.append(route)
        self.write_flags.append(t.write_started())
        body = {"data": {}, "error": {"code": "scope_not_authorized", "message": "scope", "log_id": "x"}}
        return t.FakeResponse(200, json.dumps(body).encode())


class Base(t.Flipped):
    def cooldown(self):
        with connect(str(self.db)) as con:
            return active_provider_cooldown(con, "tt-me")


class DirectBase(Base):
    DIRECT = True

    def direct(self, level="SELF_ONLY", *extra):
        return self.approved("--mode", "direct_post", "--privacy-level", level, "--commercial", "own_brand", *extra)


class InboxRateAndScopeTests(Base):
    def test_user_info_429_retries_boundedly_records_a_cooldown_and_keeps_the_post_approved(self):
        pid = self.approved()
        fake = t.FakeTikTok(errors={"user_info": 429})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("BLOCKED", out)
        self.assertIn("tiktok_user_info_http_429", out)
        self.assertEqual(set(fake.routes), {"user_info"})
        self.assertLessEqual(len(fake.routes), 4)  # bounded preflight retries, never an endless loop
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertEqual(self.events(pid, "send_claimed"), [])
        self.assertIsNotNone(self.cooldown())
        self.assert_no_secret(out, err)

    def test_init_401_names_the_missing_scope_and_nothing_is_uploaded(self):
        pid = self.approved()
        fake = t.FakeTikTok(errors={"inbox_init": 401})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertNotIn("upload", fake.routes)
        row = self.row(pid)
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("lacks the upload/publish scope", row["last_error"])
        self.assertIn("class=certain_not_sent", self.events(pid, "needs_review")[0])
        self.assert_no_secret(out, err)

    def test_init_429_after_the_claim_is_certain_not_sent_with_a_cooldown_and_is_never_resent(self):
        pid = self.approved()
        fake = t.FakeTikTok(errors={"inbox_init": 429})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertNotIn("upload", fake.routes)
        row = self.row(pid)
        self.assertEqual((row["status"], row["last_error"]), ("needs_review", "tiktok_init_http_429"))
        self.assertIn("class=certain_not_sent", self.events(pid, "needs_review")[0])
        self.assertIsNotNone(self.cooldown())
        again, out2, _ = self.send(t.FakeTikTok(), pid)  # the post is not retried automatically
        self.assertEqual(again, 2, out2)

    def test_an_api_scope_not_authorized_body_blocks_init_without_an_upload(self):
        pid = self.approved()
        fake = ScopeDenied("inbox_init")
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertNotIn("upload", fake.routes)
        self.assertEqual(self.row(pid)["status"], "needs_review")
        self.assertIn("scope_not_authorized", self.row(pid)["last_error"])
        self.assert_no_secret(out, err)


class DirectRateAndScopeTests(DirectBase):
    def test_creator_info_401_says_video_publish_is_missing_and_blocks_before_the_claim(self):
        pid = self.direct()
        fake = t.FakeTikTok(errors={"creator_info": 401})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("lacks video.publish", out)
        self.assertEqual(fake.routes, ["user_info", "creator_info"])
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertEqual(self.events(pid, "send_claimed"), [])
        self.assertIsNone(self.cooldown())
        self.assert_no_secret(out, err)

    def test_creator_info_429_records_a_cooldown_and_keeps_the_post_approved(self):
        pid = self.direct()
        fake = t.FakeTikTok(errors={"creator_info": 429})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("tiktok_creator_info_http_429", out)
        self.assertNotIn("direct_init", fake.routes)
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertIsNotNone(self.cooldown())

    def test_user_info_429_in_direct_mode_blocks_before_creator_info(self):
        pid = self.direct()
        fake = t.FakeTikTok(errors={"user_info": 429})
        status, out, _ = self.send(fake, pid)
        self.assertEqual(status, 2)
        self.assertNotIn("creator_info", fake.routes)
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assertIsNotNone(self.cooldown())

    def test_direct_init_401_names_the_scope_and_nothing_is_uploaded(self):
        pid = self.direct()
        fake = t.FakeTikTok(errors={"direct_init": 401})
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 3, out + err)
        self.assertNotIn("upload", fake.routes)
        row = self.row(pid)
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("lacks the upload/publish scope", row["last_error"])
        self.assertIn("class=certain_not_sent", self.events(pid, "needs_review")[0])

    def test_an_api_scope_not_authorized_body_at_creator_info_blocks_before_the_claim(self):
        pid = self.direct()
        fake = ScopeDenied("creator_info")
        status, out, err = self.send(fake, pid)
        self.assertEqual(status, 2, out + err)
        self.assertIn("scope_not_authorized", out)
        self.assertEqual(fake.routes, ["user_info", "creator_info"])
        self.assertEqual(self.row(pid)["status"], "approved")
        self.assert_no_secret(out, err)


class VisibilityTheOwnerSeesTests(DirectBase):
    def attach(self, pid, level):
        return self.cli("video-attach", pid, "--file", str(self.video), "--mode", "direct_post", "--privacy-level",
                        level, "--commercial", "own_brand", env={})

    def draft(self):
        from reachweaver.storage import add_post
        with connect(str(self.db)) as con:
            return add_post(con, "tt-me", "tiktok", t.CAPTION)

    def test_attach_and_show_print_the_exact_level_for_each_choice(self):
        for level in ("SELF_ONLY", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS", "PUBLIC_TO_EVERYONE"):
            with self.subTest(level=level):
                self.fresh_db()
                pid = self.draft()
                status, out, err = self.attach(pid, level)
                self.assertEqual(status, 0, out + err)
                self.assertIn(f"privacy {level}", out)
                self.assertIn("Not uploaded", out)
                status, out, _ = self.cli("show", pid, env={})
                options = json.loads(out)["video"]["record"]["platform_options"]
                self.assertEqual((options["mode"], options["privacy_level"]), ("direct_post", level))

    def test_a_direct_post_has_no_default_level(self):
        pid = self.draft()
        status, _, err = self.cli("video-attach", pid, "--file", str(self.video), "--mode", "direct_post",
                                  "--commercial", "own_brand", env={})
        self.assertEqual(status, 2)
        self.assertIn("explicit --privacy-level", err)

    def test_approving_binds_the_level_and_changing_it_needs_an_unapprove_first(self):
        pid = self.direct("SELF_ONLY")
        status, out, err = self.attach(pid, "FOLLOWER_OF_CREATOR")
        self.assertEqual(status, 2)
        self.assertIn("unapprove it first", err)
        status, out, _ = self.cli("show", pid, env={})
        self.assertEqual(json.loads(out)["video"]["record"]["platform_options"]["privacy_level"], "SELF_ONLY")
        self.assertEqual(self.row(pid)["status"], "approved")


class SetupDocTests(unittest.TestCase):
    """The setup guide states the audit, the external user's actions and that there is no bypass."""

    @classmethod
    def setUpClass(cls):
        cls.text = (Path(__file__).resolve().parents[1] / "docs" / "TIKTOK.md").read_text(encoding="utf-8")
        cls.section = cls.text.split("## Setup, review and audit", 1)[1].split("\n## ", 1)[0]

    def test_section_covers_review_audit_and_the_external_user(self):
        for needle in ("Content Posting API product", "video.publish", "video.upload", "must undergo an audit",
                       "restricted to private viewing mode", "target TikTok user must have authorized",
                       "Last updated August 4, 2026", "2026-10-15"):
            self.assertIn(needle, self.section)

    def test_section_offers_no_bypass_and_says_what_is_not_verified(self):
        self.assertIn("**No bypass.**", self.section)
        self.assertIn("will not work around the audit", self.section)
        self.assertIn("**Not verified:**", self.section)
        for forbidden in ("to bypass the audit", "skip the audit", "work around the audit by"):
            self.assertNotIn(forbidden, self.section)

    def test_every_source_link_is_the_official_developer_portal_or_terms(self):
        links = re.findall(r"\]\((https://[^)]+)\)", self.section)
        self.assertTrue(links)
        for link in links:
            self.assertTrue(link.startswith("https://developers.tiktok.com/"), link)


if __name__ == "__main__":
    unittest.main()
