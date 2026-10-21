"""Posts the owner published BY HAND, made measurable (``mark-shared --url``; owner-approved 2026-10-21).

A ``published_manual`` record is born terminal. These tests prove that it can never be approved, unapproved,
cancelled, scheduled, claimed, reconciled, retried or picked by any send/worker query; that the repeat guard behaves
exactly as ``mark-shared`` always did (no self-match, no double finding, still blocking the same material); that
metrics, import-metrics, metrics-fetch (Mastodon and DID-form Bluesky links), report (with --since), insights, the
journal and the dashboard data include it, labelled posted by hand; and that the one-time widening of an older
database's status CHECK keeps every row, approval hash and foreign key. Synthetic data only; no network.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gate_support import LIVE_ENV

from reachweaver import ledger, metrics_fetch, repeat_guard, sendgate, ui_data
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.privacy import plan_purge
from reachweaver.storage import (
    MANUAL_STATUS, POST_STATUSES, REPEAT_REFUSAL, SCHEMA, add_post, approval_binding_problem,
    approval_rebind_required, claim_sending, connect, connect_readonly, get_post, manual_status_supported, pending,
    queue_health, record_manual_publish, reconcile, report, set_post_media, set_post_video, shared_entries,
    store_metrics, transition,
)

SERVER = "https://social.example.org"
MASTO = {"id": "my-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_MM_MASTO", "server": SERVER,
         "handle": "owner"}
BSKY = {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_MM_BSKY", "handle": "owner.bsky.social"}
# A mock token, so a live run would reach the (mocked) adapter if it ever selected a by-hand record.
ENV = {**LIVE_ENV, "REACHWEAVER_MM_MASTO_TOKEN": "mock-masto-token"}
DID = "did:plc:abcdefghijklmnopqrstuvwx"
TEXT = "We shipped the offline importer today; notes and thanks are in the changelog."
OTHER = "A completely different note about a spring workshop for first-time contributors."
MASTO_URL = f"{SERVER}/@owner/109876543210"
POSTED_AT = "2026-10-20T09:00:00+02:00"
OLD_CHECK = "CHECK(status IN ('draft','approved','scheduled','sending','published','needs_review','cancelled'))"


def old_schema() -> str:
    """The schema of v0.14.0 and earlier: no published_manual in the CHECK, no shared_material.post_id."""
    new_check = "CHECK(status IN ('draft','approved','scheduled','sending','published','needs_review','cancelled'," \
                "'published_manual'))"
    assert new_check in SCHEMA
    text = SCHEMA.replace(new_check, OLD_CHECK)
    line = "  links_json TEXT NOT NULL DEFAULT '[]',\n  post_id TEXT  -- 2026-10-21: the measurable published_manual " \
           "record this entry belongs to, if any\n"
    assert line in text
    return text.replace(line, "  links_json TEXT NOT NULL DEFAULT '[]'\n")


class Base(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.db = str(self.root / "state" / "db.sqlite")
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [MASTO, BSKY]}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        sendgate.reset()

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def mark(self, *extra, url=MASTO_URL, text=TEXT, platform="mastodon"):
        argv = ["mark-shared", "--platform", platform, "--text", text, "--at", POSTED_AT, *extra]
        if url is not None:
            argv += ["--url", url]
        return self.cli(*argv)

    def manual(self, *extra, **kw) -> str:
        code, out, err = self.mark(*extra, **kw)
        self.assertEqual(code, 0, out + err)
        pid = out.split("Measurable as post ", 1)[1].split(" ", 1)[0]
        self.assertEqual(self.row(pid)["status"], MANUAL_STATUS)
        return pid

    def row(self, pid):
        with connect(self.db) as db:
            return dict(get_post(db, pid))

    def counts(self):
        with connect(self.db) as db:
            return (db.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
                    db.execute("SELECT COUNT(*) FROM shared_material").fetchone()[0])


class RecordTests(Base):
    def test_url_creates_a_terminal_measurable_record_linked_to_its_ledger_entry(self):
        code, out, err = self.mark("--account", "my-masto")
        self.assertEqual(code, 0, out + err)
        self.assertIn("posted by hand, published_manual", out)
        self.assertIn("can never be approved, scheduled or sent", out)
        with connect(self.db) as db:
            post = dict(db.execute("SELECT * FROM posts").fetchone())
            entries = shared_entries(db)
            recorded = db.execute("SELECT detail FROM event_log WHERE action='manual_publish_recorded'").fetchall()
        self.assertEqual(post["status"], MANUAL_STATUS)
        self.assertEqual((post["platform"], post["account_id"], post["content"]), ("mastodon", "my-masto", TEXT))
        self.assertEqual(post["remote_url"], MASTO_URL)
        self.assertEqual(post["remote_id"], "109876543210")  # derived offline from the link
        self.assertEqual(post["send_started_at"], "2026-10-20T07:00:00+00:00")
        for column in ("approved_at", "scheduled_at", "approved_payload_sha256", "approved_by", "approval_channel",
                       "approved_payload_mac", "approved_identity", "approval_nonce", "approved_send_options",
                       "approved_allow_repeat", "media_json", "video_json", "reply_target", "last_error"):
            self.assertIsNone(post[column], column)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["post_id"], post["id"])
        self.assertEqual(entries[0]["account_id"], "my-masto")
        self.assertEqual(len(recorded), 1)
        self.assertIn("provenance=owner_declared_manual_publish", recorded[0]["detail"])

    def test_without_url_mark_shared_is_unchanged_and_writes_no_post(self):
        code, out, _ = self.mark(url=None)
        self.assertEqual(code, 0)
        self.assertIn("Nothing was posted or sent.", out)
        self.assertEqual(self.counts(), (0, 1))

    def test_account_is_optional_and_stored_as_unknown(self):
        pid = self.manual()
        self.assertEqual(self.row(pid)["account_id"], "")
        self.assertIsNone(self.row(pid)["remote_id"])  # no account: no id can be derived for metrics-fetch

    def test_refusals_write_nothing(self):
        self.manual()
        before = self.counts()
        cases = [("--url", "ftp://example.org/x"), ("--url", "https://example.org/a b"),
                 ("--url", "not a link")]
        for flag, value in cases:
            code, _, err = self.cli("mark-shared", "--platform", "mastodon", "--text", OTHER, flag, value)
            self.assertEqual(code, 2, (value, err))
        code, _, err = self.cli("mark-shared", "--platform", "mastodon", "--text", OTHER,
                                "--url", "https://social.example.org/@owner/1", "--at", "2999-01-01T00:00:00+00:00")
        self.assertEqual(code, 2)
        self.assertIn("future", err)
        code, _, err = self.cli("mark-shared", "--platform", "mastodon", "--text", OTHER,
                                "--url", MASTO_URL + "/?utm_source=x")  # the same post, canonical form
        self.assertEqual(code, 2)
        self.assertIn("already recorded", err)
        code, _, _ = self.cli("mark-shared", "--platform", "nowhere", "--text", OTHER, "--url", "https://x.example/1")
        self.assertEqual(code, 2)
        code, _, _ = self.cli("mark-shared", "--platform", "mastodon", "--url", "https://x.example/2")
        self.assertEqual(code, 2)  # no material at all
        self.assertEqual(self.counts(), before)

    def test_storage_function_validates_its_own_inputs(self):
        with connect(self.db) as db:
            for kw in ({"platform": "nowhere"}, {"url": "javascript:alert(1)"}, {"account_id": " "},
                       {"content": "", "links": []}, {"remote_id": ""}):
                args = {"platform": "mastodon", "url": "https://social.example.org/@owner/5", "content": TEXT, **kw}
                with self.assertRaises(ValidationError, msg=kw):
                    record_manual_publish(db, **args)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)


class NeverSendsTests(Base):
    """Every send, approve, schedule, retry, reconcile and worker path refuses or ignores the record."""

    def setUp(self):
        super().setUp()
        self.pid = self.manual("--account", "my-masto")
        self.snapshot = self.row(self.pid)

    def assertUnchanged(self):
        self.assertEqual(self.row(self.pid), self.snapshot)

    def test_transition_has_no_edge_out_of_published_manual(self):
        with connect(self.db) as db:
            for command, at in (("approve", None), ("unapprove", None), ("cancel", None),
                                ("schedule", "2030-01-01T00:00:00+00:00")):
                with self.assertRaises(ValidationError) as caught:
                    transition(db, self.pid, command, at)
                self.assertIn("published_manual", str(caught.exception))
        self.assertUnchanged()

    def test_cli_approve_unapprove_cancel_schedule_and_reconcile_refuse(self):
        for argv in (("approve", self.pid), ("approve", self.pid, "--allow-repeat"), ("unapprove", self.pid),
                     ("cancel", self.pid), ("schedule", self.pid, "--at", "2030-01-01T00:00:00+00:00"),
                     ("reconcile", self.pid, "--published", "--remote-id", "1"),
                     ("reconcile", self.pid, "--not-published")):
            code, out, err = self.cli(*argv)
            self.assertEqual(code, 2, (argv, out, err))
        with connect(self.db) as db:
            with self.assertRaises(ValidationError):
                reconcile(db, self.pid, True, "1", force=True)
            with self.assertRaises(ValidationError):
                reconcile(db, self.pid, False, force=True)
        self.assertUnchanged()

    def test_attachment_setters_refuse(self):
        with connect(self.db) as db:
            with self.assertRaises(ValidationError):
                set_post_media(db, self.pid, [])
            with self.assertRaises(ValidationError):
                set_post_video(db, self.pid, None)
        self.assertUnchanged()

    def test_no_due_work_query_and_no_claim_selects_it(self):
        with connect(self.db) as db:
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00"), [])
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00", post_id=self.pid), [])
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00", scheduled_only=True), [])
            self.assertFalse(claim_sending(db, self.pid, "2099-01-01T00:00:00+00:00", max_per_24h=10))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)
        self.assertUnchanged()

    def test_publish_dry_run_and_live_never_send_it(self):
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertNotIn(self.pid, out)
        for extra in ((), ("--post-id", self.pid), ("--scheduled-only",)):
            with patch("reachweaver.publishers.request_get_json") as get, \
                    patch("reachweaver.publishers.request_json") as post:
                code, out, err = self.cli("publish", "--live", *extra, env=ENV)
            get.assert_not_called()
            post.assert_not_called()
            self.assertNotIn(f"PUBLISHED {self.pid}", out + err)
        self.assertUnchanged()

    def test_health_queue_dead_letter_and_rebind_views_ignore_it(self):
        with connect(self.db) as db:
            queue = queue_health(db)
            self.assertFalse(queue["attention_required"])
            self.assertEqual(queue["status_counts"], {MANUAL_STATUS: 1})
            self.assertEqual(approval_rebind_required(db), [])
            self.assertEqual(ledger.audit(db)["problems"], [])
        code, out, _ = self.cli("dead-letter", "--json")
        self.assertEqual(code, 0)
        self.assertNotIn(self.pid, out)
        for argv in (("health", "--json"), ("queue-health",), ("ops-metrics",), ("statusline",), ("list",),
                     ("show", self.pid), ("data-export",), ("schema-export",), ("privacy-status",)):
            code, out, err = self.cli(*argv)
            self.assertNotIn("Traceback", out + err, argv)
            self.assertIn(code, (0, 1), (argv, err))
        self.assertUnchanged()

    def test_no_code_path_updates_a_row_into_or_out_of_published_manual(self):
        root = Path(__file__).resolve().parents[1] / "reachweaver"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "UPDATE posts" in line and "published_manual" in line:
                    # the only UPDATE that names it is the privacy purge's content redaction (status kept)
                    self.assertEqual(path.name, "privacy.py", line)
                    self.assertNotIn("status=", line.split("WHERE")[0], line)
        storage = (root / "storage.py").read_text(encoding="utf-8")
        allowed = storage[storage.index("def _transition_locked"):storage.index("if command not in allowed")]
        self.assertNotIn("published_manual", allowed)
        self.assertNotIn("MANUAL_STATUS", allowed)


class RepeatGuardTests(Base):
    def test_a_later_draft_with_the_same_wording_is_blocked_exactly_like_mark_shared(self):
        pid = self.manual("--account", "my-masto")
        with connect(self.db) as db:
            draft = add_post(db, "my-masto", "mastodon", TEXT)
            findings = repeat_guard.findings_for_post(db, get_post(db, draft))
        self.assertEqual(len(findings), 1)  # the ledger entry only: the record is not compared twice
        self.assertEqual(findings[0]["source"], "ledger")
        self.assertEqual(len(repeat_guard.blocking(findings)), 1)
        code, _, err = self.cli("approve", draft)
        self.assertEqual(code, 2)
        self.assertIn("same wording", err)
        code, out, _ = self.cli("repeat-check", "--post-id", pid, "--json")
        self.assertEqual(code, 0)  # the draft is only "also queued"; its own ledger entry is never a finding
        self.assertEqual([(f["source"], f["ref"]) for f in json.loads(out)["findings"]], [("post", draft)])

    def test_unknown_account_still_counts_for_every_account(self):
        self.manual()
        with connect(self.db) as db:
            draft = add_post(db, "my-masto", "mastodon", TEXT)
        self.assertEqual(self.cli("approve", draft)[0], 2)

    def test_send_time_check_still_refuses_an_approved_copy(self):
        with connect(self.db) as db:
            draft = add_post(db, "my-masto", "mastodon", TEXT)
        code, out, err = self.cli("approve", draft)
        self.assertEqual(code, 0, out + err)
        self.manual("--account", "my-masto")  # posted by hand after the approval
        with patch("reachweaver.publishers.request_get_json") as get, \
                patch("reachweaver.publishers.request_json") as post:
            code, out, err = self.cli("publish", "--live", "--post-id", draft, env=ENV)
        self.assertEqual(code, 2, out + err)
        self.assertIn(REPEAT_REFUSAL, out + err)
        get.assert_not_called()
        post.assert_not_called()


class MeasurementTests(Base):
    def test_metrics_import_report_since_and_formats(self):
        pid = self.manual("--account", "my-masto")
        self.assertEqual(self.cli("metrics", pid, "--observed-at", "2026-10-21T09:00:00+00:00", "--source",
                                  "native-export", "--likes", "12", "--views", "300")[0], 0)
        export = self.root / "export.csv"
        export.write_text("post_id,observed_at,source,likes\n" f"{pid},2026-10-22T09:00:00+00:00,native-csv,15\n",
                          encoding="utf-8")
        self.assertEqual(self.cli("import-metrics", "--csv", str(export))[0], 0)
        code, out, _ = self.cli("report")
        data = json.loads(out)["platforms"]["mastodon"]
        self.assertEqual((data["published"], data["posted_by_hand"], data["likes"]), (1, 1, 15))
        self.assertEqual(json.loads(self.cli("report", "--since", "2026-10-19")[1])["platforms"]["mastodon"]
                         ["posted_by_hand"], 1)
        self.assertEqual(json.loads(self.cli("report", "--since", "2026-10-21")[1])["platforms"], {})
        self.assertIn("| mastodon | 1 (1 posted by hand) |", self.cli("report", "--format", "markdown")[1])
        rows = list(csv.DictReader(io.StringIO(self.cli("report", "--format", "csv")[1])))
        self.assertEqual({row["posted_by_hand"] for row in rows}, {"1"})

    def test_sent_posts_count_without_a_by_hand_label(self):
        with connect(self.db) as db:
            sent = add_post(db, "my-masto", "mastodon", OTHER)
            db.execute("UPDATE posts SET status='published', send_started_at=? WHERE id=?",
                       ("2026-10-19T09:00:00+00:00", sent))
            store_metrics(db, sent, "2026-10-20T09:00:00+00:00", "native", likes=3)
        self.manual("--account", "my-masto")
        with connect(self.db) as db:
            data = report(db)["mastodon"]
        self.assertEqual((data["published"], data["posted_by_hand"], data["likes"]), (2, 1, 3))

    def test_insights_count_it_and_say_so(self):
        pid = self.manual("--account", "my-masto")
        with connect(self.db) as db:
            store_metrics(db, pid, "2026-10-27T09:00:00+00:00", "native", likes=4)
        code, out, _ = self.cli("insights", "--json")
        self.assertEqual(code, 0)
        cohort = json.loads(out)["cohorts"][0]
        self.assertEqual((cohort["published_in_window"], cohort["posted_by_hand"]), (1, 1))
        self.assertIn("1 of them posted by hand", self.cli("insights")[1])

    def test_journal_shows_one_entry_labelled_by_hand(self):
        pid = self.manual("--account", "my-masto")
        self.mark(url=None, text=OTHER)  # an ordinary ledger-only entry stays listed as before
        code, out, _ = self.cli("journal", "--format", "json")
        entries = json.loads(out)["entries"]
        self.assertEqual(sorted(e["source"] for e in entries), ["by_hand", "posted_by_hand"])
        manual = next(e for e in entries if e["id"] == pid)
        self.assertEqual(manual["status"], MANUAL_STATUS)
        self.assertIn("posted by hand", manual["note"])
        entries = json.loads(self.cli("journal", "--format", "json", "--no-by-hand")[1])["entries"]
        self.assertEqual(entries, [])

    def test_dashboard_data_labels_it(self):
        pid = self.manual("--account", "my-masto")
        with connect(self.db) as db:
            store_metrics(db, pid, "2026-10-21T09:00:00+00:00", "native", likes=4)
        with connect_readonly(self.db) as ro:
            results = ui_data.results_view(ro)
            posted = ui_data.journal_view(ro)
            detail = ui_data.post_detail(ro, pid, {})
        self.assertEqual([(p["id"], p["posted_by_hand"]) for p in results["posts"]], [(pid, True)])
        self.assertEqual(results["platforms"][0]["posted_by_hand"], 1)
        self.assertEqual([(e["id"], e["source"]) for e in posted["entries"]], [(pid, "posted_by_hand")])
        self.assertTrue(detail["posted_by_hand"])
        self.assertEqual(detail["results"][0]["age_hours"], 26.0)  # measured from the declared publish time
        self.assertIn(f"reachweaver metrics {pid}", detail["next_steps"][0]["command"])
        self.assertNotIn("approve", " ".join(s["command"] for s in detail["next_steps"]))
        from reachweaver.ui_assets import APP_JS
        self.assertIn('published_manual: "posted by you"', APP_JS)  # the dashboard's plain label (2026-10-21)

    def test_privacy_purge_redacts_old_by_hand_text_like_published_text(self):
        pid = self.manual("--account", "my-masto")
        with connect(self.db) as db:
            plan = plan_purge(db, {"published_content_days": 1}, now="2027-01-01T00:00:00+00:00")
        self.assertIn(pid, plan["actions"]["published_content_redact"])


class MetricsFetchTests(Base):
    def test_platform_id_from_url_is_offline_and_strict(self):
        derive = metrics_fetch.platform_id_from_url
        self.assertEqual(derive("mastodon", MASTO_URL, MASTO), "109876543210")
        self.assertEqual(derive("mastodon", f"{SERVER}/users/owner/statuses/42", MASTO), "42")
        for url in ("https://other.example.org/@owner/42", f"{SERVER}/@someone/42", f"{SERVER}/@owner/abc",
                    f"{SERVER}/@owner/42?x=1", "http://social.example.org/@owner/42"):
            self.assertIsNone(derive("mastodon", url, MASTO), url)
        self.assertIsNone(derive("mastodon", MASTO_URL, None))
        self.assertIsNone(derive("mastodon", MASTO_URL, BSKY))
        self.assertEqual(derive("bluesky", f"https://bsky.app/profile/{DID}/post/3kabc", BSKY),
                         f"at://{DID}/app.bsky.feed.post/3kabc")
        self.assertIsNone(derive("bluesky", "https://bsky.app/profile/owner.bsky.social/post/3kabc", BSKY))
        self.assertIsNone(derive("x", "https://x.com/owner/status/1", {"platform": "x"}))

    def test_mastodon_record_is_fetched_read_only_and_author_checked(self):
        pid = self.manual("--account", "my-masto")
        code, out, _ = self.cli("metrics-fetch", "--dry-run", "--post-id", pid)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["would_fetch"][0]["post_id"], pid)
        status = {"id": "109876543210", "account": {"username": "owner", "acct": "owner"},
                  "favourites_count": 7, "reblogs_count": 2, "replies_count": 1}
        with patch("reachweaver.metrics_fetch._get", return_value=status) as get:
            code, out, _ = self.cli("metrics-fetch", "--online", "--post-id", pid)
        self.assertEqual(code, 0, out)
        get.assert_called_once()
        self.assertEqual(json.loads(out)["fetched"][0]["fields_fetched"], {"likes": 7, "reposts": 2, "replies": 1})
        stranger = dict(status, account={"username": "stranger", "acct": "stranger"})
        with patch("reachweaver.metrics_fetch._get", return_value=stranger):
            code, out, _ = self.cli("metrics-fetch", "--online", "--post-id", pid)
        self.assertEqual(code, 1)
        self.assertIn("author_mismatch", out)

    def test_bluesky_record_needs_a_did_link_and_the_configured_handle(self):
        url = f"https://bsky.app/profile/{DID}/post/3kabc"
        pid = self.manual("--account", "my-bsky", url=url, platform="bluesky")
        uri = f"at://{DID}/app.bsky.feed.post/3kabc"
        found = {"posts": [{"uri": uri, "author": {"did": DID, "handle": "impostor.bsky.social"}, "likeCount": 9}]}
        with patch("reachweaver.metrics_fetch._get", return_value=found):
            code, out, _ = self.cli("metrics-fetch", "--online", "--post-id", pid)
        self.assertEqual(code, 1)
        self.assertIn("author is not the configured handle", out)
        found["posts"][0]["author"]["handle"] = "owner.bsky.social"
        with patch("reachweaver.metrics_fetch._get", return_value=found):
            code, out, _ = self.cli("metrics-fetch", "--online", "--post-id", pid)
        self.assertEqual(code, 0, out)
        handle_link = self.manual("--account", "my-bsky", url="https://bsky.app/profile/owner.bsky.social/post/3kxyz",
                                  platform="bluesky", text=OTHER)
        code, out, _ = self.cli("metrics-fetch", "--dry-run", "--post-id", handle_link)
        self.assertIn("posted_by_hand_without_platform_id", out)


class WideningTests(Base):
    """An older database gets the new CHECK once, keeping every row, approval and foreign key."""

    def make_old_database(self):
        Path(self.db).parent.mkdir(mode=0o700)
        raw = sqlite3.connect(self.db)
        raw.executescript(old_schema())
        raw.close()
        os.chmod(self.db, 0o600)
        with connect(self.db) as db:
            self.assertFalse(manual_status_supported(db))
            draft = add_post(db, "my-masto", "mastodon", OTHER)
            approved = add_post(db, "my-masto", "mastodon", "An approved post that is waiting for its worker.")
            sent = add_post(db, "my-masto", "mastodon", "An earlier post that went out.")
            db.execute("UPDATE posts SET status='published', send_started_at='2026-10-01T09:00:00+00:00' WHERE id=?",
                       (sent,))
            store_metrics(db, sent, "2026-10-02T09:00:00+00:00", "native", likes=2)
        code, out, err = self.cli("approve", approved)
        self.assertEqual(code, 0, out + err)
        return draft, approved, sent

    def dump(self):
        with connect(self.db) as db:
            return ([dict(r) for r in db.execute("SELECT * FROM posts ORDER BY id")],
                    [dict(r) for r in db.execute("SELECT * FROM metric_snapshots ORDER BY id")])

    def test_first_record_widens_the_check_and_keeps_everything(self):
        draft, approved, sent = self.make_old_database()
        posts_before, snaps_before = self.dump()
        pid = self.manual("--account", "my-masto")
        posts_after, snaps_after = self.dump()
        self.assertEqual([p for p in posts_after if p["id"] != pid], posts_before)
        self.assertEqual(snaps_after, snaps_before)
        with connect(self.db) as db:
            self.assertTrue(manual_status_supported(db))
            self.assertIsNone(approval_binding_problem(get_post(db, approved)))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIsNotNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_posts_due'")
                                 .fetchone())
            widened = db.execute("SELECT COUNT(*) FROM event_log WHERE action='posts_status_check_widened'")
            self.assertEqual(widened.fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):  # the CHECK still rejects anything else
                db.execute("UPDATE posts SET status='bogus' WHERE id=?", (draft,))
            with self.assertRaises(sqlite3.IntegrityError):  # and the snapshot foreign key is enforced again
                db.execute("INSERT INTO metric_snapshots(post_id,observed_at,source,likes) "
                           "VALUES('missing-post','2026-10-02T09:00:00+00:00','native',1)")
        self.manual("--account", "my-masto", url=f"{SERVER}/@owner/2", text="Another hand-posted note for later.")
        with connect(self.db) as db:
            widened = db.execute("SELECT COUNT(*) FROM event_log WHERE action='posts_status_check_widened'")
            self.assertEqual(widened.fetchone()[0], 1)  # only once
        self.assertEqual(self.cli("approve", draft)[0], 0)  # the ordinary flow is untouched

    def test_inside_an_open_transaction_nothing_changes(self):
        self.make_old_database()
        posts_before, _ = self.dump()
        with connect(self.db) as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ValidationError):
                record_manual_publish(db, platform="mastodon", url=MASTO_URL, content=TEXT)
            db.execute("ROLLBACK")
            self.assertFalse(manual_status_supported(db))
        self.assertEqual(self.dump()[0], posts_before)

    def test_status_list_and_check_constraint_agree(self):
        self.assertIn(MANUAL_STATUS, POST_STATUSES)
        self.assertIn("'published_manual'", SCHEMA)


if __name__ == "__main__":
    unittest.main()
