"""Bluesky (#12) and Mastodon (#13) adapter slice. All HTTP is mocked; no live requests."""
from __future__ import annotations

import contextlib
import io
from email.message import Message
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from reachweaver import publishers
from reachweaver.accounts import load_accounts
from reachweaver.attachments import check_media_list
from reachweaver.capabilities import require_capability
from reachweaver.media import CONSTRAINTS
from reachweaver.ratelimit import classify_send_failure
from reachweaver.cli import main
from reachweaver.connections import check_connection, mastodon_instance_limits
from reachweaver.policy import ValidationError, mastodon_text_limit, validate_post
from reachweaver.publishers import PublishError, bluesky_record, publish_bluesky, publish_mastodon
from gate_support import bound_send_options, publish  # noqa: E402
from reachweaver.richtext import (
    TID_PATTERN, bluesky_facets, deterministic_tid, grapheme_count, graphemes,
    mastodon_length, unlinked_mentions,
)
from reachweaver.storage import add_post, connect, get_post, transition

FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"  # 7 code points, 25 bytes
CID = "bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"  # docs example shape
BSKY_ENV = {"REACHWEAVER_BM_BSKY_IDENTIFIER": "owner.bsky.social",
            "REACHWEAVER_BM_BSKY_APP_PASSWORD": "mock-app-pw"}
MASTO_ENV = {"REACHWEAVER_BM_MASTO_TOKEN": "mock-masto-token"}


def session(did="did:plc:owner123", handle="owner.bsky.social"):
    return {"did": did, "handle": handle, "accessJwt": "mock-access"}


class GraphemeTests(unittest.TestCase):
    def test_cluster_counts(self):
        cases = {
            FAMILY: 1,                                    # emoji ZWJ family
            "\U0001F1EE\U0001F1F9\U0001F1EF\U0001F1F5": 2,  # two flags = regional-indicator pairs
            "\U0001F1EE\U0001F1F9\U0001F1EF": 2,          # odd indicator stands alone
            "éàö": 3,                   # combining accents
            "漢字かな한국": 6,                              # CJK + precomposed Hangul
            "각": 1,                      # conjoining Hangul jamo
            "\U0001F44D\U0001F3FD": 1,                    # emoji modifier
            "1️⃣": 1,                           # keycap
            "❤️": 1,                            # variation selector
            "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F": 1,  # tag flag
            "\U0001F3F3️‍\U0001F308": 1,        # rainbow flag ZWJ sequence
            "a\r\nb": 3,                                  # CR LF is one cluster
            "a‍b": 2,                                # ZWJ between letters does not join
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(grapheme_count(text), expected)
                self.assertEqual("".join(graphemes(text)), text)

    def test_unimplemented_rules_only_over_count(self):
        # Indic conjuncts (GB9c) are not implemented: 4 >= the 3 a full UAX #29 segmenter returns.
        self.assertGreaterEqual(grapheme_count("नमस्ते"), 3)
        self.assertEqual(grapheme_count("؀a"), 2)  # Prepend treated as a break

    def test_bluesky_limit_is_300_graphemes_not_code_points(self):
        decomposed = "é" * 300  # 600 code points, 900 bytes
        self.assertEqual(validate_post("bluesky", decomposed), [])
        self.assertTrue(any("301 grapheme" in p for p in validate_post("bluesky", "é" * 301)))
        self.assertEqual(validate_post("bluesky", "漢" * 300), [])
        self.assertTrue(validate_post("bluesky", "漢" * 301))

    def test_bluesky_byte_limit_is_secondary_guard(self):
        self.assertEqual(validate_post("bluesky", FAMILY * 120), [])  # 120 graphemes, 3000 bytes
        problems = validate_post("bluesky", FAMILY * 121)
        self.assertEqual(len(problems), 1)
        self.assertIn("3025 UTF-8 bytes", problems[0])


class FacetTests(unittest.TestCase):
    def assertSlices(self, text, facets):
        encoded = text.encode("utf-8")
        return [encoded[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode("utf-8") for f in facets]

    def test_link_and_tag_offsets_with_emoji_cjk_and_combining(self):
        text = f"{FAMILY} 研究 café #研究。 see https://example.com/a_(b)。 and #open_source!"
        facets = bluesky_facets(text)
        self.assertEqual(self.assertSlices(text, facets), ["#研究", "https://example.com/a_(b)", "#open_source"])
        kinds = [f["features"][0]["$type"] for f in facets]
        self.assertEqual(kinds, ["app.bsky.richtext.facet#tag", "app.bsky.richtext.facet#link",
                                 "app.bsky.richtext.facet#tag"])
        self.assertEqual(facets[0]["features"][0]["tag"], "研究")
        self.assertEqual(facets[1]["features"][0]["uri"], "https://example.com/a_(b)")
        starts = [f["index"]["byteStart"] for f in facets]
        self.assertEqual(starts, sorted(starts))

    def test_no_tag_for_numbers_or_url_fragments_and_no_overlap(self):
        text = "Release #2026 at https://example.com/docs#install (#v2)"
        facets = bluesky_facets(text)
        self.assertEqual(self.assertSlices(text, facets), ["https://example.com/docs#install"])

    def test_tag_over_64_graphemes_is_plain_text(self):
        self.assertEqual(bluesky_facets("#" + "a" * 65), [])
        self.assertEqual(len(bluesky_facets("#" + "a" * 64)), 1)

    def test_mentions_are_never_faceted(self):
        text = "Thanks @alice.bsky.social and (@bob.example.com) via https://example.com/@carol.example"
        facets = bluesky_facets(text)
        self.assertFalse(any("mention" in f["features"][0]["$type"] for f in facets))
        self.assertEqual(unlinked_mentions(text), ["@alice.bsky.social", "@bob.example.com"])


class BlueskyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.config = Path(self.work.name) / "accounts.json"
        self.db = str(Path(self.work.name) / "state.sqlite")
        self.write_accounts({"id": "own-pds", "platform": "bluesky", "env_prefix": "REACHWEAVER_BM_BSKY",
                             "handle": "owner.bsky.social", "service": "https://pds.example.org/"})

    def write_accounts(self, *accounts):
        self.config.write_text(json.dumps({"accounts": list(accounts)}), encoding="utf-8")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def account(self):
        return load_accounts(self.config)["own-pds"]

    def test_default_service_is_bsky_social(self):
        account = {"id": "d", "platform": "bluesky", "env_prefix": "REACHWEAVER_BM_BSKY", "handle": "owner.bsky.social"}
        rkey = deterministic_tid("abc", "2026-10-08T10:00:00+00:00")
        with patch.dict(os.environ, BSKY_ENV), patch("reachweaver.publishers.request_json") as request:
            request.side_effect = [session(), {"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}", "cid": CID}]
            publish_bluesky(account, "Hello", "abc", created_at="2026-10-08T10:00:00+00:00")
        self.assertEqual([c.args[0] for c in request.call_args_list],
                         ["https://bsky.social/xrpc/com.atproto.server.createSession",
                          "https://bsky.social/xrpc/com.atproto.repo.createRecord"])

    def test_custom_pds_origin_used_for_session_record_and_identity(self):
        account = self.account()
        self.assertEqual(account["service"], "https://pds.example.org")
        created = "2026-10-08T09:30:00+00:00"
        rkey = deterministic_tid("post-1", created)
        with patch.dict(os.environ, BSKY_ENV), patch("reachweaver.publishers.request_json") as request:
            request.side_effect = [session(), {"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}", "cid": CID}]
            receipt = publish_bluesky(account, "Notes: https://example.com", "post-1", created_at=created)
            request.side_effect = None
            request.return_value = session()
            check_connection(account)
        urls = [c.args[0] for c in request.call_args_list]
        self.assertEqual(urls, ["https://pds.example.org/xrpc/com.atproto.server.createSession",
                                "https://pds.example.org/xrpc/com.atproto.repo.createRecord",
                                "https://pds.example.org/xrpc/com.atproto.server.createSession"])
        body = request.call_args_list[1].args[1]
        self.assertEqual(body["rkey"], rkey)
        self.assertEqual(body["repo"], "did:plc:owner123")
        self.assertEqual(receipt, (f"at://did:plc:owner123/app.bsky.feed.post/{rkey}",
                                   f"https://bsky.app/profile/did:plc:owner123/post/{rkey}"))
        self.assertEqual(receipt.extra, {"cid": CID})

    def test_pds_validation_rejects_http_ip_localhost(self):
        for bad in ("http://pds.example.org", "https://127.0.0.1", "https://[::1]", "https://localhost",
                    "https://pds.local", "https://pds.example.org/xrpc", "https://u:p@pds.example.org",
                    "https://pds.example.org:8443"):
            with self.subTest(service=bad):
                self.write_accounts({"id": "own-pds", "platform": "bluesky", "env_prefix": "REACHWEAVER_BM_BSKY",
                                     "handle": "owner.bsky.social", "service": bad})
                with self.assertRaises(ValidationError):
                    load_accounts(self.config)
                raw = {"id": "x", "platform": "bluesky", "env_prefix": "REACHWEAVER_BM_BSKY",
                       "handle": "owner.bsky.social", "service": bad}
                with patch.dict(os.environ, BSKY_ENV), patch("reachweaver.publishers._send_http") as network:
                    with self.assertRaises(ValidationError):
                        publish_bluesky(raw, "Hello", "p1")
                    with self.assertRaises(ValidationError):
                        check_connection(raw)
                network.assert_not_called()

    def test_receipt_mismatches_are_uncertain(self):
        created = "2026-10-08T09:30:00+00:00"
        rkey = deterministic_tid("post-2", created)
        bad_receipts = {
            "bluesky_receipt_repo_mismatch_uncertain": {"uri": f"at://did:plc:someoneelse/app.bsky.feed.post/{rkey}", "cid": CID},
            "bluesky_receipt_rkey_mismatch_uncertain": {"uri": "at://did:plc:owner123/app.bsky.feed.post/3k4duaz5vfs2b", "cid": CID},
            "bluesky_receipt_missing_cid_uncertain": {"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}"},
            "bluesky_missing_post_uri_uncertain": {"uri": f"at://did:plc:owner123/app.bsky.feed.like/{rkey}", "cid": CID},
        }
        for code, response in bad_receipts.items():
            with self.subTest(code=code), patch.dict(os.environ, BSKY_ENV), \
                    patch("reachweaver.publishers.request_json", side_effect=[session(), response]):
                with self.assertRaisesRegex(PublishError, code):
                    publish_bluesky(self.account(), "Hello", "post-2", created_at=created)

    def test_deterministic_tid_record_key(self):
        a = deterministic_tid("0123456789abcdef", "2026-10-08T09:30:00+00:00")
        self.assertRegex(a, TID_PATTERN)
        self.assertEqual(a, deterministic_tid("0123456789abcdef", "2026-10-08T09:30:00+00:00"))
        self.assertNotEqual(a, deterministic_tid("fedcba9876543210", "2026-10-08T09:30:00+00:00"))
        self.assertLess(deterministic_tid("x", "2026-01-01T00:00:00+00:00"),
                        deterministic_tid("x", "2026-10-08T00:00:00+00:00"))  # sortable by time
        with self.assertRaises(ValidationError), patch("reachweaver.publishers._send_http") as network:
            publish_bluesky(self.account(), "Hello", "p", created_at="not-a-date")
        network.assert_not_called()

    def test_live_send_uses_stable_rkey_and_retry_collision_goes_to_review(self):
        with connect(self.db) as db:
            pid = add_post(db, "own-pds", "bluesky", "Proof-backed note https://example.com #research")
            transition(db, pid, "approve")
            created = get_post(db, pid)["created_at"]
        rkey = deterministic_tid(pid, created)
        env = {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        receipt = {"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}", "cid": CID}
        with patch.dict(os.environ, env), \
                patch("reachweaver.publishers.request_json", side_effect=[session(), session(), receipt]) as request:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 0, out)
        record_body = request.call_args_list[2].args[1]
        self.assertEqual(record_body["rkey"], rkey)
        self.assertEqual([f["features"][0]["$type"].rsplit("#")[-1] for f in record_body["record"]["facets"]],
                         ["link", "tag"])
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "published")
            detail = db.execute("SELECT detail FROM event_log WHERE action='published' AND post_id=?", (pid,)).fetchone()[0]
        self.assertIn(f"cid={CID}", detail)

    def test_resend_targets_same_rkey_and_conflict_is_an_error(self):
        # After a lost response, a later re-send of the same post (e.g. after an
        # incorrect human reconcile) reuses the record key; the PDS refuses the
        # duplicate create and the adapter surfaces an error, never a success.
        created = "2026-10-08T09:30:00+00:00"
        bodies = []

        def first_ok_then_conflict(req):
            body = json.loads(req.data.decode("utf-8")) if req.data else {}
            if req.full_url.endswith("createSession"):
                return io.BytesIO(json.dumps(session()).encode())
            bodies.append(body)
            if len(bodies) == 1:
                rkey = body["rkey"]
                return io.BytesIO(json.dumps({"uri": f"at://did:plc:owner123/app.bsky.feed.post/{rkey}",
                                              "cid": CID}).encode())
            raise HTTPError(req.full_url, 400, "InvalidRequest", None, None)
        with patch.dict(os.environ, BSKY_ENV), \
                patch("reachweaver.publishers._send_http", side_effect=first_ok_then_conflict):
            publish_bluesky(self.account(), "Hello", "post-9", created_at=created)
            with self.assertRaisesRegex(PublishError, "http_400"):
                publish_bluesky(self.account(), "Hello", "post-9", created_at=created)
        self.assertEqual(bodies[0]["rkey"], bodies[1]["rkey"])
        self.assertEqual(bodies[0]["rkey"], deterministic_tid("post-9", created))

    def test_cli_post_claim_conflict_goes_to_needs_review(self):
        with connect(self.db) as db:
            pid = add_post(db, "own-pds", "bluesky", "Already sent once")
            transition(db, pid, "approve")
        with patch.dict(os.environ, {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers.request_json",
                      side_effect=[session(), session(), PublishError("http_400")]):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 3)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "needs_review")

    def test_cli_receipt_mismatch_moves_to_needs_review(self):
        with connect(self.db) as db:
            pid = add_post(db, "own-pds", "bluesky", "One supervised pilot")
            transition(db, pid, "approve")
        wrong = {"uri": "at://did:plc:intruder/app.bsky.feed.post/3k4duaz5vfs2b", "cid": CID}
        with patch.dict(os.environ, {**BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers.request_json", side_effect=[session(), session(), wrong]):
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 3)
        self.assertIn("REVIEW", out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "needs_review")
            self.assertEqual(get_post(db, pid)["last_error"], "bluesky_receipt_repo_mismatch_uncertain")

    def test_show_preview_is_offline_and_compact(self):
        text = f"{FAMILY} 漢字 https://example.com #research @alice.bsky.social"
        with connect(self.db) as db:
            pid = add_post(db, "own-pds", "bluesky", text)
            created = get_post(db, pid)["created_at"]
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("show", pid)
        network.assert_not_called()
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual(shown["content"], text)
        preview = shown["platform_preview"]
        self.assertEqual(preview["graphemes"], grapheme_count(text))
        self.assertEqual(preview["grapheme_limit"], 300)
        self.assertEqual([f["type"] for f in preview["facets"]], ["link", "tag"])
        self.assertEqual(preview["facets"][0]["text"], "https://example.com")
        self.assertEqual(preview["mentions_as_plain_text"], ["@alice.bsky.social"])
        self.assertEqual(preview["record_key"], deterministic_tid(pid, created))
        self.assertEqual(preview["service"], "https://pds.example.org")


class MastodonAdapterTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.config = Path(self.work.name) / "accounts.json"
        self.db = str(Path(self.work.name) / "state.sqlite")
        self.base = {"id": "masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_BM_MASTO",
                     "server": "https://social.example.org", "handle": "alice"}
        self.write(**{})

    def write(self, **options):
        self.config.write_text(json.dumps({"accounts": [{**self.base, **options}]}), encoding="utf-8")

    def account(self):
        return load_accounts(self.config)["masto"]

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_instance_limit_applied_never_raised(self):
        self.write(max_characters=300)
        account = self.account()
        self.assertEqual(mastodon_text_limit(account), 300)
        self.assertEqual(validate_post("mastodon", "x" * 300, account), [])
        self.assertTrue(validate_post("mastodon", "x" * 301, account))
        self.assertEqual(validate_post("mastodon", "x" * 301), [])  # no account: conservative 450
        self.write(max_characters=5000)
        self.assertEqual(mastodon_text_limit(self.account()), 450)
        self.assertTrue(validate_post("mastodon", "x" * 451, self.account()))
        for bad in (0, -1, True, "500", 1.5):
            with self.subTest(bad=bad):
                self.write(max_characters=bad)
                with self.assertRaises(ValidationError):
                    self.account()

    def test_short_urls_count_as_reserved_length(self):
        self.assertEqual(mastodon_length("see https://a.io now"), 4 + 23 + 4)
        self.write(max_characters=30)
        self.assertTrue(validate_post("mastodon", "see https://a.io now", self.account()))

    def test_draft_and_publish_preflight_use_pinned_limit(self):
        self.write(max_characters=20)
        code, _, err = self.cli("draft", "--account", "masto", "--text", "x" * 21)
        self.assertEqual(code, 2)
        self.assertIn("limit is 20", err)
        with connect(self.db) as db:  # bypass draft validation to test the live preflight
            pid = add_post(db, "masto", "mastodon", "y" * 25)
            # Sheet items 5/9: bind the account's send options as `reachweaver approve` does.
            transition(db, pid, "approve", send_options=bound_send_options(self.config, "masto"))
        with patch.dict(os.environ, {**MASTO_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}), \
                patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid)
        self.assertEqual(code, 2)
        self.assertIn("failed preflight", out)
        network.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_direct_visibility_rejected(self):
        self.write(visibility="direct")
        with self.assertRaisesRegex(ValidationError, "direct.*forbidden"):
            self.account()
        for bad in ("Public", "followers", "", None, 1):
            with self.subTest(bad=bad):
                self.write(visibility=bad)
                with self.assertRaises(ValidationError):
                    self.account()
        raw = {**self.base, "visibility": "direct"}
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers._send_http") as network:
            with self.assertRaisesRegex(ValidationError, "forbidden"):
                publish_mastodon(raw, "Hello", "p1")
        network.assert_not_called()

    def test_payload_minimal_with_optional_defaults(self):
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers.request_json") as request:
            request.return_value = {"id": "1", "url": "https://social.example.org/@alice/1", "visibility": "public"}
            publish_mastodon(self.account(), "Hello", "abc123")
            self.assertEqual(request.call_args.args[1], {"status": "Hello", "visibility": "public"})
            self.assertEqual(request.call_args.kwargs["headers"]["Idempotency-Key"], "abc123")
            self.write(visibility="unlisted", language="en", spoiler_text="Release notes")
            request.return_value = {"id": "2", "url": "https://social.example.org/@alice/2", "visibility": "unlisted"}
            receipt = publish_mastodon(self.account(), "Hello", "abc124")
        self.assertEqual(request.call_args.args[1], {"status": "Hello", "visibility": "unlisted",
                                                     "language": "en", "spoiler_text": "Release notes"})
        self.assertEqual(receipt.extra, {"visibility": "unlisted"})
        for field, bad in (("language", "eng"), ("language", "EN"), ("spoiler_text", ""), ("spoiler_text", "a\nb"),
                           ("spoiler_text", "x" * 201)):
            with self.subTest(field=field, bad=bad):
                self.write(**{field: bad})
                with self.assertRaises(ValidationError):
                    self.account()

    def test_receipt_requires_id_and_link_and_matching_account(self):
        cases = {
            "mastodon_missing_post_id_uncertain": {"url": "https://social.example.org/@alice/1"},
            "mastodon_missing_post_url_uncertain": {"id": "1", "url": None},
            "mastodon_receipt_account_mismatch_uncertain": {"id": "1", "url": "https://social.example.org/@bob/1",
                                                            "account": {"username": "bob", "acct": "bob"}},
            "mastodon_receipt_visibility_mismatch_uncertain": {"id": "1", "url": "https://social.example.org/@alice/1",
                                                               "visibility": "private"},
        }
        for code, response in cases.items():
            with self.subTest(code=code), patch.dict(os.environ, MASTO_ENV), \
                    patch("reachweaver.publishers.request_json", return_value=response):
                with self.assertRaisesRegex(PublishError, code):
                    publish_mastodon(self.account(), "Hello", "p1")
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers.request_json",
                                                      return_value={"id": "7", "url": None,
                                                                    "uri": "https://social.example.org/users/alice/statuses/7",
                                                                    "account": {"username": "Alice"}}):
            self.assertEqual(publish_mastodon(self.account(), "Hello", "p1"),
                             ("7", "https://social.example.org/users/alice/statuses/7"))

    def instance(self, max_characters=500):
        return {"domain": "social.example.org",
                "configuration": {"statuses": {"max_characters": max_characters, "max_media_attachments": 4,
                                               "characters_reserved_per_url": 23},
                                  "media_attachments": {"supported_mime_types": ["image/png", "video/mp4"],
                                                        "image_size_limit": 16777216, "video_size_limit": 103809024,
                                                        "description_limit": 1500}}}

    def test_instance_check_parsing_without_token(self):
        self.write(max_characters=300)
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers.request_get_json",
                                                      return_value=self.instance()) as request:
            result = mastodon_instance_limits(self.account())
        self.assertEqual(request.call_args.args, ("https://social.example.org/api/v2/instance",))
        self.assertEqual(result["statuses"], {"max_characters": 500, "max_media_attachments": 4,
                                              "characters_reserved_per_url": 23})
        self.assertEqual(result["media_attachments"]["image_size_limit"], 16777216)
        self.assertEqual(result["media_attachments"]["supported_mime_types"], 2)
        self.assertIsNone(result["media_attachments"]["video_frame_rate_limit"])
        self.assertEqual((result["effective_text_limit"], result["ok"]), (300, True))
        self.write(max_characters=1000)
        with patch("reachweaver.publishers.request_get_json", return_value=self.instance(400)):
            result = mastodon_instance_limits(self.account())
        self.assertFalse(result["ok"])
        self.assertIn("configured max_characters exceeds the instance value; lower it", result["problems"])
        with patch("reachweaver.publishers.request_get_json", return_value={"domain": "x"}), \
                self.assertRaisesRegex(PublishError, "missing_configuration"):
            mastodon_instance_limits(self.account())

    def test_connect_check_online_reports_instance(self):
        identity = {"id": "1", "username": "alice", "acct": "alice"}
        with patch.dict(os.environ, MASTO_ENV), \
                patch("reachweaver.publishers.request_get_json", side_effect=[identity, self.instance()]) as request:
            code, out, _ = self.cli("connect-check", "--account", "masto", "--online")
        self.assertEqual(code, 0, out)
        shown = json.loads(out)
        self.assertEqual(shown["instance"]["statuses"]["max_characters"], 500)
        self.assertEqual(shown["instance"]["effective_text_limit"], 450)
        self.assertNotIn("mock-masto-token", out)
        instance_call = request.call_args_list[1]
        self.assertEqual(len(instance_call.args), 1)  # no Authorization header for the public probe
        with patch.dict(os.environ, MASTO_ENV), \
                patch("reachweaver.publishers.request_get_json", side_effect=[identity, self.instance(300)]):
            code, out, _ = self.cli("connect-check", "--account", "masto", "--online")
        self.assertEqual(code, 2)  # default 450 would exceed the instance: owner must pin
        self.assertFalse(json.loads(out)["instance"]["ok"])

    def test_show_preview_includes_exact_account_and_visibility(self):
        self.write(visibility="unlisted", max_characters=400)
        with connect(self.db) as db:
            pid = add_post(db, "masto", "mastodon", "Hello https://example.com")
        with patch("reachweaver.publishers._send_http") as network:
            code, out, _ = self.cli("show", pid)
        network.assert_not_called()
        preview = json.loads(out)["platform_preview"]
        self.assertEqual((preview["server"], preview["handle"], preview["visibility"]),
                         ("https://social.example.org", "alice", "unlisted"))
        self.assertEqual((preview["counted_length"], preview["text_limit"]), (6 + 23, 400))

    def test_library_publish_applies_account_limit(self):
        self.write(max_characters=10)
        with patch("reachweaver.publishers._send_http") as network, \
                self.assertRaisesRegex(ValidationError, "limit is 10"):
            publish(self.account(), "x" * 11, "p1")
        network.assert_not_called()


FIXTURES = Path(__file__).parent / "fixtures" / "providers" / "bluesky"
FIXTURE_ENV = {"REACHWEAVER_BM_BSKY_IDENTIFIER": "fixture-user.example.test",
               "REACHWEAVER_BM_BSKY_APP_PASSWORD": "mock-app-pw-fixture"}
FIXTURE_ACCOUNT = {"id": "fx", "platform": "bluesky", "env_prefix": "REACHWEAVER_BM_BSKY",
                   "handle": "fixture-user.example.test"}


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_error(name, url="https://bsky.social/xrpc/x"):
    """The fixture as the HTTPError urllib raises for its status, headers and body."""
    fx = load_fixture(name)
    headers = Message()
    for key, value in fx["headers"].items():
        headers[key] = value
    return HTTPError(url, fx["status"], "mock", headers, io.BytesIO(json.dumps(fx["body"]).encode()))


def fixture_ok(name, **override):
    """A 200 fixture as a response body; the access token is built at run time, never stored."""
    fx = load_fixture(name)
    return io.BytesIO(json.dumps({**fx["body"], **override, "accessJwt": "mock-" + "access"}).encode())


def matching_session():
    """The wrong-account fixture shape, but for the configured account."""
    return fixture_ok("create-session-wrong-account.json", did="did:plc:aaaaaaaaaaaaaaaaaaaaaaaa",
                      handle="fixture-user.example.test")


class BlueskyProviderFixtureTests(unittest.TestCase):
    """Issue #12: synthetic provider error and identity fixtures replayed through the HTTP layer."""

    def setUp(self):
        publishers.reset_write_marker()
        self.addCleanup(publishers.reset_write_marker)
        env = patch.dict(os.environ, FIXTURE_ENV)
        env.start()
        self.addCleanup(env.stop)

    def run_flow(self, *responses, call=None):
        """Replay responses (an exception is raised, a BytesIO is returned) in order.

        Returns (raised exception or result, the mocked network)."""
        call = call or (lambda: publish_bluesky(FIXTURE_ACCOUNT, "Hello", "p1"))
        with patch("reachweaver.publishers._send_http", side_effect=list(responses)) as net:
            try:
                return call(), net
            except Exception as exc:  # returned for the assertion, with the request count
                return exc, net

    def test_fixtures_are_valid_and_carry_no_credential(self):
        names = sorted(p.name for p in FIXTURES.glob("*.json"))
        self.assertGreaterEqual(len(names), 9)
        for name in names:
            with self.subTest(fixture=name):
                fx = load_fixture(name)
                self.assertFalse(fx["_fixture"]["recorded"])
                self.assertNotIn("accessJwt", fx["body"])
                self.assertNotIn("refreshJwt", fx["body"])

    def test_401_on_create_session_is_sanitized_and_certainly_not_sent(self):
        for name, provider_code in (("create-session-401-expired-token.json", "ExpiredToken"),
                                    ("create-session-401-authentication-required.json", "AuthenticationRequired")):
            with self.subTest(fixture=name):
                publishers.reset_write_marker()
                exc, net = self.run_flow(fixture_error(name))
                self.assertIsInstance(exc, PublishError)
                self.assertEqual(str(exc), "http_401")
                self.assertEqual(exc.status, 401)
                self.assertNotIn(provider_code, repr(exc))
                self.assertNotIn("mock-app-pw-fixture", repr(exc))
                self.assertEqual(net.call_count, 1)  # createRecord never attempted
                self.assertFalse(publishers.write_started())
                self.assertEqual(classify_send_failure("bluesky", None, exc, write_started=False),
                                 "certain_not_sent")

    def test_401_on_create_record_is_ambiguous_never_certain(self):
        exc, net = self.run_flow(matching_session(), fixture_error("create-record-401-invalid-token.json"))
        self.assertEqual(str(exc), "http_401")
        self.assertEqual(net.call_count, 2)
        self.assertTrue(publishers.write_started())
        self.assertEqual(classify_send_failure("bluesky", None, exc, write_started=True), "ambiguous")

    def test_401_on_connect_check_is_reported_not_swallowed(self):
        exc, _ = self.run_flow(fixture_error("create-session-401-expired-token.json"),
                               call=lambda: check_connection(FIXTURE_ACCOUNT))
        self.assertIsInstance(exc, PublishError)
        self.assertEqual(str(exc), "http_401")

    def test_429_on_create_session_has_no_delay_hint_from_ratelimit_reset(self):
        exc, net = self.run_flow(fixture_error("create-session-429.json"))
        self.assertEqual((str(exc), exc.status), ("http_429", 429))
        self.assertIsNone(exc.retry_hint)  # ratelimit-reset unit is unverified; default applies
        self.assertEqual(net.call_count, 1)
        self.assertFalse(publishers.write_started())

    def test_429_on_create_record_with_retry_after_is_bounded_hint_and_likely_not_created(self):
        exc, _ = self.run_flow(matching_session(), fixture_error("create-record-429-retry-after.json"))
        self.assertEqual((str(exc), exc.status), ("http_429", 429))
        self.assertEqual((exc.retry_hint.delay_seconds, exc.retry_hint.source), (120, "retry_after"))
        self.assertTrue(publishers.write_started())
        self.assertEqual(classify_send_failure("bluesky", None, exc, write_started=True), "likely_not_created")
        self.assertNotIn("RateLimitExceeded", repr(exc))

    def test_wrong_account_session_is_refused_before_any_write(self):
        exc, net = self.run_flow(fixture_ok("create-session-wrong-account.json"))
        self.assertIsInstance(exc, ValidationError)
        self.assertIn("identity_mismatch", str(exc))
        self.assertEqual(net.call_count, 1)
        self.assertFalse(publishers.write_started())

    def test_wrong_account_and_inactive_account_fail_connect_check(self):
        exc, _ = self.run_flow(fixture_ok("create-session-wrong-account.json"),
                               call=lambda: check_connection(FIXTURE_ACCOUNT))
        self.assertIn("identity_mismatch", str(exc))
        exc, _ = self.run_flow(fixture_ok("create-session-inactive-account.json"),
                               call=lambda: check_connection(FIXTURE_ACCOUNT))
        self.assertIsInstance(exc, ValidationError)
        self.assertIn("bluesky_account_inactive", str(exc))

    def test_create_record_for_other_repo_is_receipt_mismatch_not_success(self):
        created = "2026-10-08T09:30:00+00:00"
        rkey = deterministic_tid("p1", created)
        other = load_fixture("create-session-wrong-account.json")["body"]["did"]
        receipt = io.BytesIO(json.dumps({"uri": f"at://{other}/app.bsky.feed.post/{rkey}", "cid": CID}).encode())
        exc, _ = self.run_flow(matching_session(), receipt,
                               call=lambda: publish_bluesky(FIXTURE_ACCOUNT, "Hello", "p1", created_at=created))
        self.assertEqual(str(exc), "bluesky_receipt_repo_mismatch_uncertain")

    def test_adapter_never_sends_an_embed(self):
        self.assertNotIn("embed", bluesky_record("Hello https://example.com #research"))
        rkey = deterministic_tid("p1", "2026-10-08T09:30:00+00:00")
        sent = []

        def fake(req):
            if req.full_url.endswith("createSession"):
                return matching_session()
            sent.append(json.loads(req.data.decode()))
            return io.BytesIO(json.dumps({"uri": f"at://did:plc:aaaaaaaaaaaaaaaaaaaaaaaa/app.bsky.feed.post/{rkey}",
                                          "cid": CID}).encode())
        with patch("reachweaver.publishers._send_http", side_effect=fake):
            publish_bluesky(FIXTURE_ACCOUNT, "Hello https://example.com", "p1", created_at="2026-10-08T09:30:00+00:00")
        self.assertEqual(len(sent), 1)
        self.assertNotIn("embed", sent[0]["record"])

    def test_provider_embed_and_blob_errors_are_sanitized_and_ambiguous(self):
        for name, provider_text in (("create-record-400-blob-too-large.json", "BlobTooLarge"),
                                    ("create-record-400-invalid-embed.json", "blob ref")):
            with self.subTest(fixture=name):
                publishers.reset_write_marker()
                exc, _ = self.run_flow(matching_session(), fixture_error(name))
                self.assertEqual(str(exc), "http_400")
                self.assertNotIn(provider_text, repr(exc))
                self.assertEqual(classify_send_failure("bluesky", None, exc, write_started=True), "ambiguous")

    def test_bluesky_image_and_video_publish_stay_refused_by_the_capability_registry(self):
        for fmt in ("image", "video"):
            with self.subTest(fmt=fmt), self.assertRaisesRegex(ValidationError, "refused"):
                require_capability("bluesky", fmt, "publish")

    def test_bluesky_image_media_limits_come_from_the_sourced_table(self):
        image = CONSTRAINTS["bluesky"]["image"]
        self.assertEqual(image["max_bytes"]["value"], 2_000_000)
        self.assertIn("embed/images", image["max_bytes"]["source"])
        self.assertEqual(image["alt_text_max_chars"]["value"], 2000)
        self.assertEqual(CONSTRAINTS["bluesky"]["video"]["allowed_containers"]["value"], ["mp4"])

    def test_media_attach_is_refused_for_bluesky_drafts(self):
        with self.assertRaisesRegex(ValidationError, "only supported for Mastodon"):
            check_media_list([{"source": "local"}], "bluesky")


if __name__ == "__main__":
    unittest.main()
