"""Offline conformance tests. These tests must never contact social platforms."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from reachweaver.accounts import load_accounts, missing_credentials
from reachweaver.cli import main
from reachweaver.policy import ValidationError, validate_post, require_https_public_server
from reachweaver.publishers import PublishError, publish_bluesky, publish_mastodon, publish_telegram, request_json, url_facets
from reachweaver.storage import (
    add_post, claim_sending, connect, finish_send, get_post, is_paused, parse_utc,
    pending, reconcile, report, set_paused, store_metrics, transition, uncertain_send,
)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "outbox.sqlite")
        self.config = Path(self.tmp.name) / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id":"my-bluesky", "platform":"bluesky", "env_prefix":"REACHWEAVER_TEST_BSKY", "handle":"example.bsky.social"},
            {"id":"my-mastodon", "platform":"mastodon", "env_prefix":"REACHWEAVER_TEST_MASTO", "server":"https://mastodon.social", "handle":"example"},
            {"id":"my-telegram", "platform":"telegram", "env_prefix":"REACHWEAVER_TEST_TG", "chat_id":"@examplechannel", "channel_username":"examplechannel", "bot_username":"example_bot"},
            {"id":"my-linkedin", "platform":"linkedin", "env_prefix":"REACHWEAVER_TEST_LI", "person_id":"782bbtaQ", "linkedin_version":"202609"},
            {"id":"my-x", "platform":"x", "env_prefix":"REACHWEAVER_TEST_X"},
        ]}),encoding="utf-8")

    def cli(self,*argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            result=main(["--db",self.db,"--accounts",str(self.config),*argv])
        return result, stdout.getvalue(), stderr.getvalue()

    def new(self,platform="bluesky",account_id="my-bluesky",content="A useful verified research note"):
        with connect(self.db) as db:
            return add_post(db,account_id,platform,content)


class StorageTests(BaseCase):
    def test_draft_is_never_due(self):
        post_id=self.new()
        with connect(self.db) as db:
            self.assertEqual(get_post(db,post_id)["status"],"draft")
            self.assertEqual(len(pending(db)),0)
            transition(db,post_id,"approve")
            self.assertEqual(len(pending(db)),1)

    def test_schedule_respects_utc_and_future(self):
        post_id=self.new()
        when=parse_utc((datetime.now(timezone.utc)+timedelta(days=2)).isoformat())
        with connect(self.db) as db:
            transition(db,post_id,"approve")
            transition(db,post_id,"schedule",when)
            self.assertEqual(get_post(db,post_id)["scheduled_at"],when)
            self.assertEqual(len(pending(db)),0)
            self.assertFalse(claim_sending(db,post_id))

    def test_timezone_is_mandatory(self):
        with self.assertRaises(ValidationError):
            parse_utc("2026-10-15T09:00:00")
        self.assertEqual(parse_utc("2026-10-15T09:00:00+02:00"),"2026-10-15T07:00:00+00:00")

    def test_atomic_claim_only_once(self):
        post_id=self.new()
        with connect(self.db) as db:
            transition(db,post_id,"approve")
        with connect(self.db) as first, connect(self.db) as second:
            self.assertTrue(claim_sending(first,post_id))
            self.assertFalse(claim_sending(second,post_id))
            self.assertEqual(get_post(first,post_id)["status"],"sending")

    def test_pause_blocks_publish(self):
        post_id=self.new()
        with connect(self.db) as db:
            transition(db,post_id,"approve")
            set_paused(db,True)
            self.assertTrue(is_paused(db))
            with self.assertRaises(ValidationError):
                claim_sending(db,post_id)
            set_paused(db,False)
            self.assertTrue(claim_sending(db,post_id))

    def test_uncertain_outcome_requires_reconciliation(self):
        post_id=self.new()
        with connect(self.db) as db:
            transition(db,post_id,"approve")
            self.assertTrue(claim_sending(db,post_id))
            uncertain_send(db,post_id,"network_or_timeout_uncertain")
            self.assertEqual(len(pending(db)),0)
            self.assertEqual(get_post(db,post_id)["status"],"needs_review")
            with self.assertRaises(ValidationError):
                transition(db,post_id,"approve")
            reconcile(db,post_id,True,"remote-123","https://example.org/post")
            self.assertEqual(get_post(db,post_id)["status"],"published")
            self.assertEqual(len(pending(db)),0)

    def test_reconcile_not_published_resets_approval(self):
        post_id=self.new()
        with connect(self.db) as db:
            transition(db,post_id,"approve")
            claim_sending(db,post_id)
            reconcile(db,post_id,False,force=True)  # freshly claimed: P2 needs --force (see test_send_state_atomicity)
            self.assertEqual(get_post(db,post_id)["status"],"draft")
            self.assertIsNone(get_post(db,post_id)["approved_at"])

    def test_metrics_latest_snapshot_not_sum(self):
        post_id=self.new()
        with connect(self.db) as db:
            transition(db,post_id,"approve")
            claim_sending(db,post_id)
            finish_send(db,post_id,"remote-id","https://example.com")
            store_metrics(db,post_id,"2026-10-15T12:00:00+00:00","native-export",views=100,likes=5)
            store_metrics(db,post_id,"2026-10-16T12:00:00+00:00","native-export",views=200,likes=11)
            data=report(db)["bluesky"]
            self.assertEqual(data["views"],200)
            self.assertEqual(data["likes"],11)
            self.assertEqual(data["metric_coverage"],1)
            self.assertEqual(data["coverage_by_metric"]["clicks"],0)
            self.assertEqual(data["coverage_by_metric"]["views"],1)

    def test_metrics_invalid_values(self):
        post_id=self.new()
        with connect(self.db) as db:
            with self.assertRaises(ValidationError):
                store_metrics(db,post_id,"2026-10-15T12:00:00+00:00","export",views=-1)
            with self.assertRaises(ValidationError):
                store_metrics(db,post_id,"2026-10-15T12:00:00+00:00","export")

    def test_post_lint_placeholder_and_limits(self):
        self.assertTrue(validate_post("bluesky","TODO: add proof"))
        self.assertTrue(validate_post("bluesky","x"*301))
        self.assertFalse(validate_post("bluesky","A real useful statement."))


class AccountTests(BaseCase):
    def test_accounts_load_without_secrets(self):
        accounts=load_accounts(self.config)
        self.assertEqual(len(accounts),5)
        self.assertEqual(accounts["my-mastodon"]["server"],"https://mastodon.social")
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(missing_credentials(accounts["my-bluesky"]),
                             ["REACHWEAVER_TEST_BSKY_IDENTIFIER","REACHWEAVER_TEST_BSKY_APP_PASSWORD"])

    def test_reject_literal_tokens_in_config(self):
        self.config.write_text(json.dumps({"accounts":[{"id":"secret-account","platform":"mastodon",
                           "env_prefix":"REACHWEAVER_TEST_MASTO","server":"https://mastodon.social","access_token":"bad"}]}))
        with self.assertRaises(ValidationError):
            load_accounts(self.config)

    def test_reject_local_origin_and_bad_scheme(self):
        for value in ("http://mastodon.social","https://127.0.0.1","https://localhost","https://mastodon.social/path", "https://user:pass@mastodon.social"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                require_https_public_server(value)


class PublisherTests(BaseCase):
    def test_facet_utf8_byte_offsets(self):
        content="Hi 🇮🇹 https://example.com/a?x=1."
        facets=url_facets(content)
        self.assertEqual(len(facets),1)
        start=facets[0]["index"]["byteStart"]
        end=facets[0]["index"]["byteEnd"]
        self.assertEqual(content.encode("utf-8")[start:end].decode("utf-8"),"https://example.com/a?x=1")

    @patch("reachweaver.publishers.request_json")
    def test_bluesky_session_and_record(self,mock):
        mock.side_effect=[{"did":"did:plc:xyz","handle":"example.bsky.social","accessJwt":"jwt-private"},{"uri":"at://did:plc:xyz/app.bsky.feed.post/ab123","cid":"bafyreibjifzpqj6o6wcq3hejh7y4z4z2vmiklkvykc57tw3pcbx3kxifpm"}]
        account=load_accounts(self.config)["my-bluesky"]
        with patch.dict(os.environ,{"REACHWEAVER_TEST_BSKY_IDENTIFIER":"u.bsky.social","REACHWEAVER_TEST_BSKY_APP_PASSWORD":"pw"}):
            uri,url=publish_bluesky(account,"Research: https://example.com","post-1")
        self.assertEqual(uri,"at://did:plc:xyz/app.bsky.feed.post/ab123")
        self.assertEqual(url,"https://bsky.app/profile/did:plc:xyz/post/ab123")
        record=mock.call_args_list[1].args[1]["record"]
        self.assertEqual(record["$type"],"app.bsky.feed.post")
        self.assertEqual(len(record["facets"]),1)

    @patch("reachweaver.publishers.request_json")
    def test_mastodon_idempotency(self,mock):
        mock.return_value={"id":"987","url":"https://mastodon.social/@user/987"}
        account=load_accounts(self.config)["my-mastodon"]
        with patch.dict(os.environ,{"REACHWEAVER_TEST_MASTO_TOKEN":"private"}):
            remote,url=publish_mastodon(account,"Technical lessons learned","deadbeef")
        self.assertEqual(remote,"987")
        self.assertEqual(mock.call_args.kwargs["headers"]["Idempotency-Key"],"deadbeef")
        self.assertTrue(mock.call_args.kwargs["form"])

    @patch("reachweaver.publishers.request_json")
    def test_telegram_channel(self,mock):
        mock.return_value={"ok":True,"result":{"message_id":42}}
        account=load_accounts(self.config)["my-telegram"]
        with patch.dict(os.environ,{"REACHWEAVER_TEST_TG_BOT_TOKEN":"private"}):
            remote,url=publish_telegram(account,"Useful news", "post-2")
        self.assertEqual((remote,url),("42","https://t.me/examplechannel/42"))

    @patch("reachweaver.publishers._send_http")
    def test_http_error_never_exposes_token_url(self,urlopen):
        secret="very-private-bot-token"
        urlopen.side_effect=HTTPError(f"https://api.telegram.org/bot{secret}/sendMessage",401,"unauthorized",None,None)
        with self.assertRaises(PublishError) as error:
            request_json(f"https://api.telegram.org/bot{secret}/sendMessage",{"text":"hello"})
        self.assertEqual(str(error.exception),"http_401")
        self.assertNotIn(secret,str(error.exception))


class CliTests(BaseCase):
    def test_doctor_no_network(self):
        result,out,err=self.cli("doctor")
        self.assertEqual(result,0)
        self.assertIn("my-x: x; planned/manual only",out)
        self.assertIn("my-linkedin: linkedin; supported (text)",out)
        self.assertIn("REACHWEAVER_TEST_LI_ACCESS_TOKEN",out)
        self.assertIn("REACHWEAVER_TEST_BSKY_APP_PASSWORD",out)

    def test_publish_requires_explicit_opt_in(self):
        post=self.new()
        with connect(self.db) as db:
            transition(db,post,"approve")
        with patch("reachweaver.cli.publish") as mocked, patch.dict(os.environ,{},clear=True):
            result,out,err=self.cli("publish")
            self.assertEqual(result,0)
            self.assertIn("DRY RUN",out)
            self.assertEqual(mocked.call_count,0)
            result,out,err=self.cli("publish","--live")
            self.assertEqual(result,2)
            self.assertIn("REACHWEAVER_ALLOW_LIVE=1",err)
            self.assertEqual(mocked.call_count,0)
        with connect(self.db) as db:
            self.assertEqual(get_post(db,post)["status"],"approved")

    def test_published_by_mocked_adapter(self):
        post=self.new()
        with connect(self.db) as db:
            transition(db,post,"approve")
        env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod","REACHWEAVER_TEST_BSKY_IDENTIFIER":"name","REACHWEAVER_TEST_BSKY_APP_PASSWORD":"app-pw"}
        with patch.dict(os.environ,env), patch("reachweaver.cli.check_connection") as checked, patch("reachweaver.cli.publish",return_value=("at://post","https://bsky.app/example")) as mocked:
            result,out,err=self.cli("publish","--live")
            self.assertEqual(result,0)
            self.assertIn("PUBLISHED",out)
            self.assertEqual(mocked.call_count,1)
        with connect(self.db) as db:
            self.assertEqual(get_post(db,post)["status"],"published")
            self.assertEqual(len(pending(db)),0)

    def test_ambiguous_network_error_not_retried(self):
        post=self.new()
        with connect(self.db) as db:
            transition(db,post,"approve")
        env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod","REACHWEAVER_TEST_BSKY_IDENTIFIER":"name","REACHWEAVER_TEST_BSKY_APP_PASSWORD":"app-pw"}
        with patch.dict(os.environ,env), patch("reachweaver.cli.check_connection") as checked, patch("reachweaver.cli.publish",side_effect=PublishError("network_or_timeout_uncertain")) as mocked:
            r1,out1,e1=self.cli("publish","--live")
            r2,out2,e2=self.cli("publish","--live")
        self.assertEqual((r1,r2),(0,0))
        self.assertEqual(mocked.call_count,1)
        self.assertIn("REVIEW",out1)
        with connect(self.db) as db:
            self.assertEqual(get_post(db,post)["status"],"needs_review")

    def test_unsupported_connector_never_sends(self):
        post=self.new("x","my-x")
        with connect(self.db) as db:
            transition(db,post,"approve")
        with patch.dict(os.environ,{"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod"}),patch("reachweaver.cli.publish") as mocked:
            result,out,err=self.cli("publish","--live")
        self.assertEqual(result,0)
        self.assertIn("requires manual",out)
        self.assertEqual(mocked.call_count,0)

    def test_explicit_crisis_stop(self):
        self.assertEqual(self.cli("pause")[0],0)
        self.assertEqual(self.cli("resume")[0],0)


if __name__ == "__main__":
    unittest.main()
