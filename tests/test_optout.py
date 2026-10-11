"""Local opt-out list: never reply to, mention or tag a listed handle/domain. Offline only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.campaigns import stage_campaign, validate_campaign
from reachweaver.claims import check_claims
from reachweaver.cli import main
from reachweaver.engagement import triage_engagement, triage_files
from reachweaver.optout import (
    OPTOUT_ENV, listed_entry, load_optout, mentioned_handles, optout_mentions, validate_optout,
)
from reachweaver.accounts import load_accounts
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect, get_post, transition
from test_no_network import NetworkGuard, _offline_env

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
LIST = {"version": 1, "entries": [
    {"handle": "@Quiet.Example.Social", "platform": "bluesky", "recorded_on": "2026-10-08"},
    {"handle": "hush@mastodon.example"},
    {"handle": "nobody"},
    {"domain": "private.example"},
]}


def item(i: str, text: str, **extra) -> dict:
    base = {"id": i, "platform": "bluesky", "account_id": "my-bluesky", "author_handle": "someone",
            "author_is_owner": False, "text": text, "url": f"https://bsky.app/profile/x.example/post/{i}",
            "created_at": "2026-10-01T09:00:00Z", "in_reply_to": None, "thread_root_url": None}
    base.update(extra)
    return base


def export(*items: dict) -> dict:
    return {"schema_version": "reachweaver.engagement-export.v1", "items": list(items)}


def campaign_with(text_suffix: str) -> dict:
    campaign = json.loads((EXAMPLES / "campaign.reachweaver.json").read_text(encoding="utf-8"))
    bsky = next(v for v in campaign["variants"] if v["platform"] == "bluesky")
    bsky["text"] = bsky["text"] + text_suffix
    return campaign


class OptoutListTests(unittest.TestCase):
    def setUp(self):
        self.entries = validate_optout(LIST)

    def test_example_file_is_valid(self):
        entries = load_optout(ROOT / "config/optout.example.json")
        self.assertEqual({e["kind"] for e in entries}, {"handle", "domain"})

    def test_rejects_malformed_lists(self):
        bad = [
            {"entries": []}, {"version": 2, "entries": []}, {"version": 1, "entries": {}},
            {"version": 1, "entries": [], "extra": 1},
            {"version": 1, "entries": [{}]},
            {"version": 1, "entries": [{"handle": "a", "domain": "b.example"}]},
            {"version": 1, "entries": [{"handle": "a", "reason": "asked nicely"}]},
            {"version": 1, "entries": [{"handle": "has space"}]},
            {"version": 1, "entries": [{"domain": "nodot"}]},
            {"version": 1, "entries": [{"handle": "a", "platform": "myspace"}]},
            {"version": 1, "entries": [{"handle": "a", "recorded_on": "yesterday"}]},
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                validate_optout(raw)

    def test_handle_matching_is_conservative(self):
        listed = lambda h, p=None: listed_entry(h, self.entries, p) is not None  # noqa: E731
        self.assertTrue(listed("quiet.example.social", "bluesky"))
        self.assertTrue(listed("@QUIET.example.social"))
        self.assertFalse(listed("quiet.example.social", "mastodon"))  # platform-scoped entry
        self.assertTrue(listed("hush@mastodon.example", "mastodon"))
        self.assertTrue(listed("hush"))  # bare form still honoured
        self.assertFalse(listed("hush@other.example"))
        self.assertTrue(listed("nobody@any.instance"))  # bare entry covers every instance
        self.assertTrue(listed("private.example"))
        self.assertTrue(listed("alice.private.example"))  # sub-domain handle
        self.assertTrue(listed("alice@private.example"))  # whole instance
        self.assertFalse(listed("someone"))
        self.assertFalse(listed("notprivate.example"))
        self.assertIsNone(listed_entry("anyone", [], None))

    def test_mentions_ignore_emails_and_urls(self):
        text = "Thanks @alice.bsky.social and @bob@mastodon.example! Mail carol@example.org, see https://x.example/@dave."
        self.assertEqual([h for h, _, _ in mentioned_handles(text)], ["alice.bsky.social", "bob@mastodon.example"])
        hits = optout_mentions("cc @Hush@mastodon.example and @friend", self.entries)
        self.assertEqual([h["span"] for h in hits], ["@Hush@mastodon.example"])
        self.assertEqual(optout_mentions("cc @hush", []), [])

    def test_load_sources(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop(OPTOUT_ENV, None)
            self.assertEqual(load_optout(), [])  # no config/optout.json in the repository
            with self.assertRaisesRegex(ValidationError, "not found"):
                load_optout(Path(tmp) / "missing.json")
            path = Path(tmp) / "optout.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "not JSON"):
                load_optout(path)
            path.write_text(json.dumps(LIST), encoding="utf-8")
            os.environ[OPTOUT_ENV] = str(path)
            self.assertEqual(len(load_optout()), 4)


class EngagementOptoutTests(unittest.TestCase):
    def test_listed_author_gets_no_reply_or_issue_draft(self):
        data = export(
            item("bug-listed", "The CLI crashes with a traceback on start.", author_handle="@quiet.example.social"),
            item("bug-other", "The CLI crashes with a traceback on start too.", author_handle="someone",
                 url="https://bsky.app/profile/y.example/post/2"),
            item("q-domain", "How do I configure the docs map?", author_handle="alice.private.example"),
            item("asks", "Great work, but please stop tagging me.", author_handle="newperson"),
        )
        report = triage_engagement(data, optout=LIST)
        by_id = {i["id"]: i for i in report["items"]}
        self.assertEqual(by_id["bug-listed"]["action"], "optout_listed_no_contact")
        self.assertEqual(by_id["q-domain"]["action"], "optout_listed_no_contact")
        self.assertTrue(by_id["bug-listed"]["flags"]["optout_listed"])
        drafted = {d["item_id"] for d in report["drafts"]}
        self.assertNotIn("bug-listed", drafted)
        self.assertNotIn("q-domain", drafted)
        self.assertIn("bug-other", drafted)
        issues = {i["item_id"] for i in report["issue_drafts"]}
        self.assertEqual(issues, {"bug-other"})
        self.assertEqual(report["optout"]["list_entries"], 4)
        self.assertEqual(report["optout"]["listed_authors_skipped"], 2)
        self.assertEqual(report["optout"]["requests_to_record"], ["asks"])
        self.assertEqual(by_id["asks"]["action"], "opt_out_no_contact")

    def test_without_list_behaviour_is_unchanged(self):
        data = export(item("bug", "The CLI crashes with a traceback on start.", author_handle="quiet.example.social"))
        report = triage_engagement(data)
        self.assertEqual(report["items"][0]["action"], "draft_reply_requires_approval")
        self.assertFalse(report["items"][0]["flags"]["optout_listed"])
        self.assertEqual(report["optout"]["list_entries"], 0)

    def test_cli_engagement_triage_uses_optout_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "export.json"
            source.write_text(json.dumps(export(
                item("bug", "The CLI crashes with a traceback on start.", author_handle="nobody"))), encoding="utf-8")
            optout = Path(tmp) / "optout.json"
            optout.write_text(json.dumps(LIST), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["--db", str(Path(tmp) / "never" / "x.sqlite"), "engagement-triage", str(source),
                             "--optout", str(optout), "--json"])
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["drafts"], [])
            self.assertEqual(data["items"][0]["action"], "optout_listed_no_contact")
            report, _ = triage_files(source, optout_path=optout)
            self.assertEqual(report["optout"]["listed_authors_skipped"], 1)
            self.assertFalse((Path(tmp) / "never").exists())


class CampaignOptoutTests(unittest.TestCase):
    def setUp(self):
        self.brand = json.loads((EXAMPLES / "brand.reachweaver.json").read_text(encoding="utf-8"))
        self.entries = validate_optout(LIST)

    def test_claims_check_flags_mention_as_error(self):
        campaign = campaign_with(" Thanks @hush@mastodon.example")
        self.assertTrue(check_claims(campaign, self.brand)["valid"])  # no list: no opt-out finding
        result = check_claims(campaign, self.brand, optout=self.entries)
        self.assertFalse(result["valid"])
        findings = [f for v in result["variants"] for f in v["findings"] if f["code"] == "optout_mention"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "error")
        self.assertEqual(findings[0]["span"], "@hush@mastodon.example")

    def test_campaign_check_blocks_and_stage_refuses(self):
        campaign = campaign_with(" cc @someone.private.example")
        errors, _ = validate_campaign(campaign, brand=self.brand, optout=self.entries)
        self.assertTrue(any("optout_mention" in e for e in errors), errors)
        self.assertEqual(validate_campaign(campaign, brand=self.brand)[0], [])
        accounts = {"my-bluesky": {"id": "my-bluesky", "platform": "bluesky", "handle": "x.bsky.social",
                                   "env_prefix": "REACHWEAVER_X"},
                    "my-linkedin": {"id": "my-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_Y",
                                    "person_id": "abc", "linkedin_version": "202609"}}
        with tempfile.TemporaryDirectory() as tmp, connect(str(Path(tmp) / "s" / "db.sqlite")) as db:
            with self.assertRaisesRegex(ValidationError, "optout_mention"):
                stage_campaign(db, campaign, self.brand, accounts, optout=self.entries)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_cli_campaign_and_claims_check_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.json"
            path.write_text(json.dumps(campaign_with(" cc @nobody")), encoding="utf-8")
            optout = Path(tmp) / "optout.json"
            optout.write_text(json.dumps(LIST), encoding="utf-8")
            brand = str(EXAMPLES / "brand.reachweaver.json")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["campaign-check", "--file", str(path), "--brand", brand]), 0)
                self.assertEqual(main(["campaign-check", "--file", str(path), "--brand", brand,
                                       "--optout", str(optout)]), 2)
                self.assertEqual(main(["claims-check", str(path), "--brand", brand, "--optout", str(optout)]), 2)
                with patch.dict(os.environ, {OPTOUT_ENV: str(optout)}):
                    self.assertEqual(main(["claims-check", str(path), "--brand", brand]), 2)
                self.assertEqual(main(["claims-check", str(path), "--brand", brand,
                                       "--optout", str(Path(tmp) / "missing.json")]), 2)
            self.assertIn("optout_mention", out.getvalue())

    def _preview_campaign(self) -> tuple[dict, dict]:
        campaign = json.loads((EXAMPLES / "campaign-preview.reachweaver.json").read_text(encoding="utf-8"))
        brand = json.loads((EXAMPLES / "brand-preview.reachweaver.json").read_text(encoding="utf-8"))
        self.assertTrue(brand.get("accounts"), "example must exercise per-account brand rules")
        by_id = {v["id"]: v for v in campaign["variants"]}
        by_id["preview-bluesky-thread"]["parts"][-1] += " cc @hush@mastodon.example"
        by_id["preview-linkedin-carousel"]["slides"][-1] += " with @nobody"
        return campaign, brand

    def test_thread_parts_and_carousel_slides_with_per_account_brand_rules(self):
        from reachweaver.claims import variant_body
        campaign, brand = self._preview_campaign()
        self.assertTrue(check_claims(campaign, brand)["valid"])  # no list: unchanged behaviour
        result = check_claims(campaign, brand, optout=self.entries)
        self.assertFalse(result["valid"])
        hits = {v["variant_id"]: [f for f in v["findings"] if f["code"] == "optout_mention"]
                for v in result["variants"]}
        self.assertEqual([f["span"] for f in hits["preview-bluesky-thread"]], ["@hush@mastodon.example"])
        self.assertEqual([f["span"] for f in hits["preview-linkedin-carousel"]], ["@nobody"])
        self.assertEqual(hits["preview-bluesky"], [])
        by_id = {v["id"]: v for v in campaign["variants"]}
        for vid, (finding,) in ((k, v) for k, v in hits.items() if v):
            body = variant_body(by_id[vid])
            self.assertEqual(body[finding["start"]:finding["end"]], finding["span"])  # offsets index the body
        errors, _ = validate_campaign(campaign, brand=brand, optout=self.entries)
        self.assertEqual(sum("optout_mention" in e for e in errors), 2, errors)

    def test_campaign_preview_reports_optout_mentions(self):
        from reachweaver.preview import build_preview
        campaign, brand = self._preview_campaign()
        clean = build_preview(campaign, brand)
        flagged = build_preview(campaign, brand, optout=self.entries)
        codes = {v["variant_id"]: {f.get("code") for f in v["findings"]} for v in flagged["variants"]}
        self.assertIn("optout_mention", codes["preview-bluesky-thread"])
        self.assertIn("optout_mention", codes["preview-linkedin-carousel"])
        self.assertNotIn("optout_mention", {f.get("code") for v in clean["variants"] for f in v["findings"]})
        levels = {v["variant_id"]: v["confidence"]["level"] for v in flagged["variants"]}
        self.assertEqual(levels["preview-bluesky-thread"], "low")
        with tempfile.TemporaryDirectory() as tmp:
            path, optout = Path(tmp) / "campaign.json", Path(tmp) / "optout.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            optout.write_text(json.dumps(LIST), encoding="utf-8")
            brand_path = str(EXAMPLES / "brand-preview.reachweaver.json")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["campaign-preview", "--file", str(path), "--brand", brand_path]), 0)
                self.assertEqual(main(["campaign-preview", "--file", str(path), "--brand", brand_path,
                                       "--optout", str(optout)]), 2)

    def test_unmodified_example_passes_with_list(self):
        campaign = copy.deepcopy(json.loads((EXAMPLES / "campaign.reachweaver.json").read_text(encoding="utf-8")))
        self.assertTrue(check_claims(campaign, self.brand, optout=self.entries)["valid"])


class DraftApprovePublishOptoutTests(unittest.TestCase):
    """Opt-out re-checks at draft/check/approve time and a pre-claim gate in publish --live."""

    BSKY_ENV = {"REACHWEAVER_OO_BSKY_IDENTIFIER": "owner.bsky.social",
                "REACHWEAVER_OO_BSKY_APP_PASSWORD": "mock-" + "app-pw"}  # built: not a literal secret
    POSTIZ_ENV = {"REACHWEAVER_OO_PZ_POSTIZ_API_KEY": "pz-" + "fake-" + "optout-key"}  # built: not a literal
    BLOCKED_TEXT = "Release notes are out. Thanks @hush@mastodon.example for testing"
    CLEAN_TEXT = "Release notes are out https://example.org/r"

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        root = Path(self.work.name)
        self.db, self.config = str(root / "state" / "db.sqlite"), root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "oo-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_OO_BSKY",
             "handle": "owner.bsky.social"},
            {"id": "oo-postiz", "platform": "bluesky", "env_prefix": "REACHWEAVER_OO_PZ",
             "handle": "owner.bsky.social", "backend": "postiz",
             "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-blue-1"},
        ]}), encoding="utf-8")
        self.optout = root / "optout.json"
        self.optout.write_text(json.dumps(LIST), encoding="utf-8")
        env = patch.dict(os.environ, _offline_env(), clear=True)  # no credentials, live opt-in or list env
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def approved(self, text, account="oo-bluesky"):
        with connect(self.db) as db:
            pid = add_post(db, account, "bluesky", text)
            transition(db, pid, "approve")
        return pid

    def assert_approved_unclaimed(self, pid):
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            claims = db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=? AND action='send_claimed'",
                                (pid,)).fetchone()[0]
        self.assertEqual(claims, 0)

    # --- draft / check / approve -------------------------------------------
    def test_draft_refused_and_nothing_created(self):
        code, _, err = self.cli("draft", "--account", "oo-bluesky", "--text", self.BLOCKED_TEXT,
                                "--optout", str(self.optout))
        self.assertEqual(code, 2)
        self.assertIn("Draft refused", err)
        self.assertIn("optout_mention", err)
        self.assertIn("'@hush@mastodon.example'", err)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)
        with patch.dict(os.environ, {OPTOUT_ENV: str(self.optout)}):  # same env fallback as other commands
            self.assertEqual(self.cli("draft", "--account", "oo-bluesky", "--text", "cc @nobody")[0], 2)
        code, out, _ = self.cli("draft", "--account", "oo-bluesky", "--text", "cc @friend",
                                "--optout", str(self.optout))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cli("draft", "--account", "oo-bluesky", "--text", self.BLOCKED_TEXT)[0], 0)  # no list

    def test_check_flags_mention(self):
        with connect(self.db) as db:
            pid = add_post(db, "oo-bluesky", "bluesky", self.BLOCKED_TEXT)
        self.assertEqual(self.cli("check", pid)[0], 0)
        code, out, _ = self.cli("check", pid, "--optout", str(self.optout))
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", out)
        self.assertIn("optout_mention", out)

    def test_approve_refused_with_clear_message(self):
        with connect(self.db) as db:
            pid = add_post(db, "oo-bluesky", "bluesky", self.BLOCKED_TEXT)
        code, _, err = self.cli("approve", pid, "--optout", str(self.optout))
        self.assertEqual(code, 2)
        self.assertIn(f"Refusing to approve {pid}", err)
        self.assertIn("opt-out list", err)
        with patch.dict(os.environ, {OPTOUT_ENV: str(self.optout)}):
            self.assertEqual(self.cli("approve", pid)[0], 2)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            approvals = db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=? AND action='approve'",
                                   (pid,)).fetchone()[0]
        self.assertEqual(approvals, 0)
        code, out, _ = self.cli("approve", pid)  # without a list the existing behaviour is unchanged
        self.assertEqual(code, 0)
        self.assertIn("-> approved", out)

    # --- publish -----------------------------------------------------------
    def test_live_native_publish_blocked_pre_claim_without_network(self):
        blocked = self.approved(self.BLOCKED_TEXT)  # approved before the person opted out
        clean = self.approved(self.CLEAN_TEXT)
        env = {**self.BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with NetworkGuard() as guard, \
                patch("reachweaver.cli.check_connection", return_value=object()) as identity, \
                patch("reachweaver.cli.publish", return_value=("at://remote/1", "https://bsky.app/x")) as sender:
            code, out, _ = self.cli("publish", "--live", "--post-id", blocked, "--optout", str(self.optout), env=env)
            self.assertEqual(code, 2)
            self.assertIn(f"BLOCKED {blocked}: optout_mention", out)
            self.assertIn("post stays approved", out)
            self.assertNotIn("hush", out)  # live output names a count, never the listed handle
            identity.assert_not_called()  # gate precedes the read-only identity preflight
            sender.assert_not_called()
            code, out, _ = self.cli("publish", "--live", "--optout", str(self.optout), env=env)  # batch run
        self.assertEqual(code, 0, out)
        self.assertEqual(guard.attempts, [])
        self.assertIn(f"BLOCKED {blocked}", out)
        self.assertIn(f"PUBLISHED {clean}", out)  # the gate is selective: a clean post still goes out
        self.assertEqual([c.args[1] for c in sender.call_args_list], [self.CLEAN_TEXT])
        self.assertEqual(identity.call_count, 1)
        self.assert_approved_unclaimed(blocked)

    def test_live_postiz_publish_blocked_pre_claim_without_network(self):
        from reachweaver import postiz
        pid = self.approved(self.BLOCKED_TEXT, account="oo-postiz")
        env = {**self.POSTIZ_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with NetworkGuard() as guard, patch("reachweaver.publishers._send_http") as http:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, "--optout", str(self.optout), env=env)
            self.assertEqual(code, 2)
            self.assertIn(f"BLOCKED {pid}: optout_mention", out)
            # A direct caller that passes no preloaded list still gets the env/default list.
            with connect(self.db) as db, patch.dict(os.environ, {**env, OPTOUT_ENV: str(self.optout)}), \
                    contextlib.redirect_stdout(io.StringIO()) as direct:
                account = load_accounts(self.config)["oo-postiz"]
                self.assertEqual(postiz.run_live_send(db, get_post(db, pid), account,
                                                      max_per_24h=2, min_gap_minutes=60), 2)
            self.assertIn("optout_mention", direct.getvalue())
        http.assert_not_called()  # neither the integrations GET nor a create
        self.assertEqual(guard.attempts, [])
        self.assert_approved_unclaimed(pid)

    def test_malformed_optout_file_aborts_before_any_claim(self):
        pid = self.approved(self.CLEAN_TEXT)
        bad = Path(self.work.name) / "bad-optout.json"
        bad.write_text(json.dumps({"version": 1, "entries": [{"handle": "x", "reason": "no"}]}), encoding="utf-8")
        env = {**self.BSKY_ENV, "REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}
        with NetworkGuard() as guard, patch("reachweaver.cli.check_connection") as identity, \
                patch("reachweaver.cli.publish") as sender:
            for path in (bad, Path(self.work.name) / "missing.json"):
                with self.subTest(path=path.name):
                    code, _, err = self.cli("publish", "--live", "--optout", str(path), env=env)
                    self.assertEqual(code, 2)
                    self.assertRegex(err, r"(?i)opt-?out")
                    self.assertEqual(self.cli("publish", "--optout", str(path))[0], 2)  # dry run fails closed too
        identity.assert_not_called()
        sender.assert_not_called()
        self.assertEqual(guard.attempts, [])
        self.assert_approved_unclaimed(pid)

    def test_dry_run_shows_which_due_posts_would_be_blocked(self):
        blocked = self.approved(self.BLOCKED_TEXT)
        clean = self.approved(self.CLEAN_TEXT)
        with NetworkGuard() as guard:
            code, out, _ = self.cli("publish", "--optout", str(self.optout))
            plain_code, plain, _ = self.cli("publish")
        self.assertEqual((code, plain_code), (0, 0))
        self.assertEqual(guard.attempts, [])
        self.assertIn("DRY RUN: 2 due approved posts", out)
        self.assertIn(f"BLOCKED {blocked}: optout_mention: 1 @-mention", out)
        self.assertNotIn(f"BLOCKED {clean}", out)
        self.assertIn("1 due post(s) would be BLOCKED", out)
        self.assertNotIn("BLOCKED", plain)  # no list configured: nothing filtered
        self.assert_approved_unclaimed(blocked)


if __name__ == "__main__":
    unittest.main()
