"""Identity and permission preflights are mocked: absolutely no live social requests."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.connections import VerifiedConnection, check_connection
from reachweaver.policy import ValidationError
from reachweaver.publishers import PublishError, _NoRedirects, request_get_json
from reachweaver.storage import add_post, connect, get_post, transition


class OnlineCheckTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.config = Path(self.work.name) / "accounts.json"
        self.db = Path(self.work.name) / "database.sqlite"
        self.config.write_text(json.dumps({"accounts": [
            {"id":"blue-personal","platform":"bluesky","env_prefix":"REACHWEAVER_CHECK_BSKY","handle":"me.bsky.social"},
            {"id":"masto-personal","platform":"mastodon","env_prefix":"REACHWEAVER_CHECK_MASTO","server":"https://mastodon.social", "handle":"alice@mastodon.social"},
            {"id":"tg-channel","platform":"telegram","env_prefix":"REACHWEAVER_CHECK_TG","chat_id":"@examplechannel","channel_username":"examplechannel", "bot_username":"example_bot"},
            {"id":"li-personal","platform":"linkedin","env_prefix":"REACHWEAVER_CHECK_LI","person_id":"782bbtaQ","linkedin_version":"202609"},
            {"id":"x-personal","platform":"x","env_prefix":"REACHWEAVER_CHECK_X"},
        ]}))
        self.accounts = load_accounts(self.config)
        self.credentials={
            "REACHWEAVER_CHECK_BSKY_IDENTIFIER":"me.bsky.social",
            "REACHWEAVER_CHECK_BSKY_APP_PASSWORD":"private-bsky",
            "REACHWEAVER_CHECK_MASTO_TOKEN":"private-masto",
            "REACHWEAVER_CHECK_TG_BOT_TOKEN":"123456:private_token",
        }

    def cli(self,*args):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            status=main(["--db",str(self.db),"--accounts",str(self.config),*args])
        return status,out.getvalue(),err.getvalue()

    @patch("reachweaver.publishers.request_json")
    def test_bluesky_identity(self,request):
        request.return_value={"did":"did:plc:123","handle":"me.bsky.social","accessJwt":"hidden"}
        with patch.dict(os.environ,self.credentials):
            result=check_connection(self.accounts["blue-personal"])
        self.assertEqual((result.identity,result.write_permission),("verified","unverified"))
        self.assertNotIn("private-bsky",repr(result))
        self.assertEqual(request.call_count,1)

    @patch("reachweaver.publishers.request_json")
    def test_wrong_bluesky_account_blocked(self,request):
        request.return_value={"did":"did:plc:123","handle":"other.bsky.social","accessJwt":"hidden"}
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"identity_mismatch"):
            check_connection(self.accounts["blue-personal"])

    @patch("reachweaver.publishers.request_json")
    def test_missing_secret_before_network(self,request):
        with patch.dict(os.environ,{},clear=True),self.assertRaisesRegex(ValidationError,"missing_credentials"):
            check_connection(self.accounts["blue-personal"])
        request.assert_not_called()

    @patch("reachweaver.publishers.request_get_json")
    def test_mastodon_verified_username(self,request):
        request.return_value={"id":"123","username":"alice", "acct":"alice"}
        with patch.dict(os.environ,self.credentials):
            result=check_connection(self.accounts["masto-personal"])
        self.assertEqual(result.identity,"verified")
        self.assertEqual(result.write_permission,"unverified")
        self.assertTrue(request.call_args.args[0].endswith("/api/v1/accounts/verify_credentials"))

    @patch("reachweaver.publishers.request_get_json")
    def test_wrong_mastodon_user_blocked(self,request):
        request.return_value={"id":"123","username":"bob"}
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"identity_mismatch"):
            check_connection(self.accounts["masto-personal"])

    @patch("reachweaver.publishers.request_get_json")
    def test_telegram_verified_bot_channel_and_rights(self,request):
        request.side_effect=[
          {"ok":True,"result":{"id":122,"username":"example_bot","is_bot":True}},
          {"ok":True,"result":{"id":-100123,"username":"examplechannel","type":"channel"}},
          {"ok":True,"result":{"status":"administrator","can_post_messages":True}},
        ]
        with patch.dict(os.environ,self.credentials):
            result=check_connection(self.accounts["tg-channel"])
        self.assertEqual((result.identity,result.write_permission),("verified","verified"))
        self.assertEqual(request.call_count,3)

    @patch("reachweaver.publishers.request_get_json")
    def test_telegram_other_channel_blocked_before_permission_probe(self,request):
        request.side_effect=[
          {"ok":True,"result":{"id":122,"username":"example_bot","is_bot":True}},
          {"ok":True,"result":{"id":-100123,"username":"unexpected_channel","type":"channel"}},
        ]
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"identity_mismatch"):
            check_connection(self.accounts["tg-channel"])
        self.assertEqual(request.call_count,2)

    @patch("reachweaver.publishers.request_get_json")
    def test_telegram_no_post_right_blocked(self,request):
        request.side_effect=[
          {"ok":True,"result":{"id":122,"username":"example_bot","is_bot":True}},
          {"ok":True,"result":{"id":-100123,"username":"examplechannel","type":"channel"}},
          {"ok":True,"result":{"status":"administrator","can_post_messages":False}},
        ]
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"cannot_post"):
            check_connection(self.accounts["tg-channel"])

    @patch("reachweaver.publishers.request_get_json")
    def test_telegram_other_bot_blocked(self,request):
        request.return_value={"ok":True,"result":{"id":122,"username":"someone_else_bot","is_bot":True}}
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"identity_mismatch"):
            check_connection(self.accounts["tg-channel"])
        self.assertEqual(request.call_count,1)

    @patch("reachweaver.publishers.request_get_json")
    def test_telegram_rejects_direct_message_destination(self,request):
        request.side_effect=[
          {"ok":True,"result":{"id":122,"username":"example_bot","is_bot":True}},
          {"ok":True,"result":{"id":123,"type":"private"}},
        ]
        a=dict(self.accounts["tg-channel"],chat_id="123",channel_username=None)
        with patch.dict(os.environ,self.credentials),self.assertRaisesRegex(ValidationError,"only_channels"):
            check_connection(a)

    @patch("reachweaver.cli.check_connection")
    def test_connect_check_explicit_flag(self,check):
        self.assertEqual(self.cli("connect-check","--account","blue-personal")[0],2)
        check.assert_not_called()
        check.return_value=VerifiedConnection("blue-personal","bluesky","verified","unverified")
        result,out,err=self.cli("connect-check","--account","blue-personal","--online")
        self.assertEqual(result,0)
        self.assertIn('"identity": "verified"',out)
        self.assertIn("No content posted",out)
        self.assertNotIn("private-",out)

    @patch("reachweaver.cli.publish")
    @patch("reachweaver.cli.check_connection")
    def test_blocked_preflight_does_not_claim_or_send(self,check,publish):
        check.side_effect=ValidationError("identity_mismatch")
        with connect(str(self.db)) as db:
            pid=add_post(db,"blue-personal","bluesky","Evidence-backed note")
            transition(db,pid,"approve")
        with patch.dict(os.environ,{**self.credentials,"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod"}):
            result,out,err=self.cli("publish","--live","--post-id",pid)
        self.assertEqual(result,2)
        self.assertIn("BLOCKED",out)
        publish.assert_not_called()
        with connect(str(self.db)) as db:
            self.assertEqual(get_post(db,pid)["status"],"approved")

    @patch("reachweaver.cli.publish")
    @patch("reachweaver.cli.check_connection")
    def test_verified_preflight_still_requires_per_post_approval(self,check,publish):
        check.return_value=VerifiedConnection("blue-personal","bluesky","verified","unverified")
        with connect(str(self.db)) as db:
            pid=add_post(db,"blue-personal","bluesky","Unapproved text")
        with patch.dict(os.environ,{**self.credentials,"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod"}):
            result,out,err=self.cli("publish","--live")
        self.assertEqual(result,0)
        publish.assert_not_called()
        check.assert_not_called()

    def test_unsupported_platform_never_attempts_network(self):
        with self.assertRaisesRegex(ValidationError,"no_authorized_live_connector"):
            check_connection(self.accounts["x-personal"])

    @patch("reachweaver.publishers._send_http")
    def test_mastodon_http_errors_sanitized(self,send):
        secret="top-secret-token"
        send.side_effect=HTTPError("https://mastodon.social/private?token="+secret,403,"secret",None,None)
        with self.assertRaises(PublishError) as ctx:
            request_get_json("https://mastodon.social/api/v1/accounts/verify_credentials",{"Authorization":"Bearer "+secret})
        self.assertEqual(str(ctx.exception),"http_403")
        self.assertNotIn(secret,str(ctx.exception))

    def test_no_redirects_by_design(self):
        self.assertIsNone(_NoRedirects().redirect_request(None,None,302,"Found",{"Location":"https://attacker.example"},"https://attacker.example"))


if __name__ == "__main__":
    unittest.main()

class StateProtectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX mode checks only")
    def test_new_database_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "private" / "data.sqlite"
            with connect(str(file)):
                pass
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(file.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX mode checks only")
    def test_refuse_world_readable_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp) / "private"
            folder.mkdir(mode=0o700)
            file=folder / "data.sqlite"
            file.touch(mode=0o644)
            file.chmod(0o644)
            with self.assertRaisesRegex(ValidationError,"database is not private"):
                with connect(str(file)):
                    pass
            file.chmod(0o600)
            folder.chmod(0o755)
            with self.assertRaisesRegex(ValidationError,"directory is not private"):
                with connect(str(file)):
                    pass

    @unittest.skipUnless(os.name == "posix", "POSIX mode checks only")
    def test_refuse_database_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp) / "target"
            target.touch(mode=0o600)
            link=Path(tmp) / "linked"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValidationError,"symbolic link"):
                with connect(str(link)):
                    pass

class PilotSelectionTests(unittest.TestCase):
    def setUp(self):
        self.work=tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.db=Path(self.work.name)/"state.sqlite"
        self.config=Path(self.work.name)/"accounts.json"
        self.config.write_text(json.dumps({"accounts":[{"id":"owner-account","platform":"bluesky", "env_prefix":"REACHWEAVER_PILOT_BSKY","handle":"owner.bsky.social"}]}))

    def cli(self,*args):
        out,err=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
            result=main(["--db",str(self.db),"--accounts",str(self.config),*args])
        return result,out.getvalue(),err.getvalue()

    def test_post_id_selects_exactly_one_due_post(self):
        with connect(str(self.db)) as db:
            one=add_post(db,"owner-account","bluesky","Approved first post")
            two=add_post(db,"owner-account","bluesky","Approved second post")
            transition(db,one,"approve")
            transition(db,two,"approve")
        status,out,_=self.cli("publish","--post-id",two)
        self.assertEqual(status,0)
        self.assertIn(two,out)
        self.assertNotIn(one,out)
        self.assertIn("1 due approved posts",out)

    def test_unapproved_post_cannot_be_selected(self):
        with connect(str(self.db)) as db:
            post=add_post(db,"owner-account","bluesky","Not yet approved")
        status,out,err=self.cli("publish","--post-id",post)
        self.assertEqual(status,2)
        self.assertIn("not approved and due",err)

    @patch("reachweaver.cli.publish")
    @patch("reachweaver.cli.check_connection")
    def test_live_pilot_never_sends_other_approved_post(self,check,publish):
        with connect(str(self.db)) as db:
            one=add_post(db,"owner-account","bluesky","Do not send this one")
            two=add_post(db,"owner-account","bluesky","Pilot approved separately")
            transition(db,one,"approve")
            transition(db,two,"approve")
        check.return_value=VerifiedConnection("owner-account","bluesky","verified","unverified")
        publish.return_value=("at://post", "https://bsky.app/profile/did:plc:pilot/post/xyz")
        env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod","REACHWEAVER_PILOT_BSKY_IDENTIFIER":"owner.bsky.social","REACHWEAVER_PILOT_BSKY_APP_PASSWORD":"mock-private"}
        with patch.dict(os.environ,env):
            status,out,err=self.cli("publish","--live","--post-id",two)
        self.assertEqual(status,0)
        self.assertEqual(publish.call_count,1)
        self.assertEqual(publish.call_args.args[2],two)
        with connect(str(self.db)) as db:
            self.assertEqual(get_post(db,one)["status"],"approved")
            self.assertEqual(get_post(db,two)["status"],"published")

class OfflineReviewTests(unittest.TestCase):
    def test_show_exact_body_without_remote_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"private.sqlite"
            with connect(str(db)) as con:
                pid=add_post(con,"owner","bluesky","Actual reviewed post with proof: https://example.com")
            out=io.StringIO()
            with contextlib.redirect_stdout(out),patch("reachweaver.publishers._send_http") as network:
                code=main(["--db",str(db),"show",pid])
            self.assertEqual(code,0)
            self.assertEqual(json.loads(out.getvalue())["content"],"Actual reviewed post with proof: https://example.com")
            network.assert_not_called()

class CliErrorHygieneTests(unittest.TestCase):
    def test_online_check_http_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/"accounts.json"
            config.write_text(json.dumps({"accounts":[{"id":"mine","platform":"bluesky","env_prefix":"REACHWEAVER_ERR","handle":"owner.bsky.social"}]}))
            out,err=io.StringIO(),io.StringIO()
            credentials={"REACHWEAVER_ERR_IDENTIFIER":"owner.bsky.social","REACHWEAVER_ERR_APP_PASSWORD":"super-private-string"}
            with patch.dict(os.environ,credentials),patch("reachweaver.publishers.request_json",side_effect=PublishError("http_401")), contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                result=main(["--accounts",str(config),"connect-check","--account","mine","--online"])
            self.assertEqual(result,2)
            self.assertIn("http_401",err.getvalue())
            self.assertNotIn("super-private-string",err.getvalue())
            self.assertNotIn("Traceback",err.getvalue())


class PilotFailureExitTests(unittest.TestCase):
    def test_uncertain_single_send_returns_review_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"db.sqlite"
            config=Path(tmp)/"accounts.json"
            config.write_text(json.dumps({"accounts":[{"id":"pilot","platform":"bluesky","env_prefix":"REACHWEAVER_PILOT","handle":"p.bsky.social"}]}))
            with connect(str(db)) as conn:
                pid=add_post(conn,"pilot","bluesky","One proof-backed pilot")
                transition(conn,pid,"approve")
            env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod","REACHWEAVER_PILOT_IDENTIFIER":"p.bsky.social","REACHWEAVER_PILOT_APP_PASSWORD":"hidden"}
            out=io.StringIO()
            with patch.dict(os.environ,env), patch("reachweaver.cli.check_connection",return_value=VerifiedConnection("pilot","bluesky","verified","unverified")), patch("reachweaver.cli.publish",side_effect=PublishError("network_or_timeout_uncertain")),contextlib.redirect_stdout(out):
                status=main(["--db",str(db),"--accounts",str(config),"publish","--live","--post-id",pid])
            self.assertEqual(status,3)
            self.assertIn("REVIEW",out.getvalue())
            with connect(str(db)) as conn:
                self.assertEqual(get_post(conn,pid)["status"],"needs_review")
