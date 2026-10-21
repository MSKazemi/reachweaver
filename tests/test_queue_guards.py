"""Rate/queue health safeguards: entirely local or mocked; no social network calls."""
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

from hold_support import claim as claim_sending
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    add_post, connect, finish_send, get_post, queue_health,
    reconcile, set_paused, transition, uncertain_send,
)


class GuardedOutbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "outbox.sqlite")
        self.config = str(Path(self.temp.name) / "accounts.json")
        Path(self.config).write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_GUARD",
             "handle": "owner.bsky.social"},
            # Own env_prefix: load_accounts refuses identical or overlapping prefixes (#5).
            {"id": "other-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_GUARD2",
             "handle": "owner.bsky.social"},
        ]}), encoding="utf-8")

    def add(self, db, account="my-bluesky", platform="bluesky"):
        # Distinct wording per post: these budget fixtures are not repeats, and the send-time repeat guard
        # (sheet item 1) would otherwise refuse the second identical copy before the budget is reached.
        count = db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        pid = add_post(db, account, platform, f"Useful evidence-backed technical note number {count + 1}")
        transition(db, pid, "approve")
        return pid

    def test_rolling_attempt_cap_counts_uncertain_and_reconciled(self):
        a, b, c = None, None, None
        with connect(self.db) as db:
            a, b, c = [self.add(db) for _ in range(3)]
            self.assertTrue(claim_sending(db, a, now="2026-10-08T09:00:00+00:00",
                                          max_per_24h=2, min_gap_minutes=60))
            uncertain_send(db, a, "remote_timeout_outcome_unknown")
            self.assertTrue(claim_sending(db, b, now="2026-10-08T10:01:00+00:00",
                                          max_per_24h=2, min_gap_minutes=60))
            finish_send(db, b, "remote-b", "https://bsky.app/profile/test/post/b")
            with self.assertRaisesRegex(ValidationError, "rolling_24h"):
                claim_sending(db, c, now="2026-10-08T11:03:00+00:00",
                              max_per_24h=2, min_gap_minutes=60)
            self.assertEqual(get_post(db,c)["status"], "approved")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 2)
            reconcile(db, a, False)
            with self.assertRaisesRegex(ValidationError, "rolling_24h"):
                claim_sending(db, c, now="2026-10-08T12:03:00+00:00",
                              max_per_24h=2, min_gap_minutes=60)

    def test_minimum_gap_and_multiple_accounts(self):
        with connect(self.db) as db:
            a, b = self.add(db), self.add(db)
            other = self.add(db, account="other-bluesky")
            self.assertTrue(claim_sending(db, a, now="2026-10-08T09:00:00+00:00",
                                          max_per_24h=2, min_gap_minutes=60))
            with self.assertRaisesRegex(ValidationError,"minimum_send_attempt_gap"):
                claim_sending(db,b,now="2026-10-08T09:30:00+00:00",
                              max_per_24h=2,min_gap_minutes=60)
            self.assertTrue(claim_sending(db, other, now="2026-10-08T09:31:00+00:00",
                                          max_per_24h=2,min_gap_minutes=60))
            self.assertTrue(claim_sending(db, b, now="2026-10-08T10:00:00+00:00",
                                          max_per_24h=2,min_gap_minutes=60))
            self.assertEqual(get_post(db,b)["status"],"sending")

    def test_rolling_window_reset_after_24h_and_restart(self):
        with connect(self.db) as db:
            first, second = self.add(db), self.add(db)
            self.assertTrue(claim_sending(db, first, now="2026-10-08T09:00:00+00:00",
                                          max_per_24h=1))
            uncertain_send(db, first, "unknown")
        with connect(self.db) as again:
            self.assertTrue(claim_sending(again,second,now="2026-10-09T09:01:00+00:00",
                                          max_per_24h=1))
            self.assertEqual(again.execute(
                "SELECT count(*) FROM event_log WHERE action='send_claimed'").fetchone()[0],2)

    def test_eligibility_check_returns_false_before_account_cap(self):
        with connect(self.db) as db:
            first, second = self.add(db), self.add(db)
            self.assertTrue(claim_sending(db,first,now="2026-10-08T09:00:00+00:00",
                                          max_per_24h=1))
            self.assertFalse(claim_sending(db,first,now="2026-10-08T09:01:00+00:00",
                                           max_per_24h=1))
            self.assertEqual(db.execute(
                "SELECT count(*) FROM event_log WHERE action='send_claimed'").fetchone()[0],1)
            self.assertEqual(get_post(db,second)["status"],"approved")

    def test_paused_and_bad_budgets_fail_closed(self):
        with connect(self.db) as db:
            pid=self.add(db)
            for kwargs in ({"max_per_24h":0},{"max_per_24h":11},{"min_gap_minutes":-1},
                           {"min_gap_minutes":1441},{"max_per_24h":True}):
                with self.assertRaises(ValidationError):
                    claim_sending(db,pid,**kwargs)
            set_paused(db,True)
            with self.assertRaisesRegex(ValidationError,"emergency pause"):
                claim_sending(db,pid,max_per_24h=2,min_gap_minutes=60)
            self.assertEqual(get_post(db,pid)["status"],"approved")

    def test_queue_health_reports_overdue_uncertain_and_stale(self):
        now="2026-10-08T12:00:00+00:00"
        with connect(self.db) as db:
            stale=self.add(db)
            self.assertTrue(claim_sending(db,stale,now="2026-10-08T10:00:00+00:00"))
            ambiguous=self.add(db)
            self.assertTrue(claim_sending(db,ambiguous,now="2026-10-08T11:00:00+00:00"))
            uncertain_send(db,ambiguous,"timeout")
            overdue=self.add(db)
            transition(db,overdue,"schedule","2026-10-08T08:00:00+00:00")
            future=self.add(db)
            transition(db,future,"schedule","2026-10-09T08:00:00+00:00")
            before=db.execute("SELECT count(*) FROM event_log").fetchone()[0]
            health=queue_health(db,now=now,stale_minutes=30)
            self.assertTrue(health["attention_required"])
            self.assertFalse(health["paused"])
            self.assertEqual(health["send_attempts_rolling_24h_by_account"]["my-bluesky"],2)
            self.assertEqual([p["post_id"] for p in health["stale_sending"]],[stale])
            self.assertEqual([p["post_id"] for p in health["ambiguous_needs_review"]],[ambiguous])
            self.assertEqual([p["post_id"] for p in health["overdue_scheduled"]],[overdue])
            self.assertEqual(db.execute("SELECT count(*) FROM event_log").fetchone()[0],before)
            self.assertEqual(db.execute("SELECT status FROM posts WHERE id=?",(stale,)).fetchone()[0],
                             "sending")

    def test_missing_send_timestamp_flagged_without_unhandled_exception(self):
        with connect(self.db) as db:
            pid=self.add(db)
            db.execute("UPDATE posts SET status='sending',send_started_at=NULL WHERE id=?",(pid,))
            health=queue_health(db,now="2026-10-08T12:00:00+00:00")
            self.assertEqual(health["stale_sending"][0]["reason"],"missing_send_started_at")

    def test_cli_queue_health_never_connects_or_publishes(self):
        with connect(self.db) as db:
            self.add(db)
        stdout=io.StringIO()
        with patch("reachweaver.cli.publish") as publish, patch("reachweaver.cli.check_connection") as connect_check:
            with contextlib.redirect_stdout(stdout):
                code=main(["--db",self.db,"queue-health"])
        self.assertEqual(code,0)
        data=json.loads(stdout.getvalue())
        self.assertIn("status_counts",data)
        publish.assert_not_called()
        connect_check.assert_not_called()

    def test_live_cli_enforces_limits_before_network_send(self):
        with connect(self.db) as db:
            a,b=self.add(db),self.add(db)
        argv=["--db",self.db,"--accounts",self.config,"publish","--live","--post-id",a,
              "--max-per-24h","1","--min-gap-minutes","15"]
        env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod",
             "REACHWEAVER_TEST_GUARD_IDENTIFIER":"owner.bsky.social",
             "REACHWEAVER_TEST_GUARD_APP_PASSWORD":"mock-app-secret"}
        output=io.StringIO()
        with patch.dict(os.environ,env),patch("reachweaver.cli.check_connection",return_value=object()),\
             patch("reachweaver.cli.publish",return_value=("remote-1","https://bsky.app/profile/test/post/1")) as publisher,\
             contextlib.redirect_stdout(output):
            self.assertEqual(main(argv),0)
            self.assertEqual(publisher.call_count,1)
            output.seek(0);output.truncate(0)
            blocked=[b if arg==a else arg for arg in argv]
            self.assertEqual(main(blocked),2)
            self.assertEqual(publisher.call_count,1)
        self.assertIn("rolling_24h_send_attempt_cap_reached",output.getvalue())
        with connect(self.db) as db:
            self.assertEqual(get_post(db,b)["status"],"approved")

    def test_scheduled_only_query_skips_unscheduled_and_stale_not_fresh(self):
        from reachweaver.storage import pending
        current=datetime(2026,10,8,12,tzinfo=timezone.utc)
        with connect(self.db) as db:
            unscheduled=self.add(db)
            stale=self.add(db)
            fresh=self.add(db)
            future=self.add(db)
            transition(db,stale,"schedule",(current-timedelta(hours=5)).isoformat())
            transition(db,fresh,"schedule",(current-timedelta(minutes=10)).isoformat())
            transition(db,future,"schedule",(current+timedelta(hours=2)).isoformat())
            chosen=pending(db,limit=10,now=current.isoformat(),scheduled_only=True,
                           scheduled_not_before=(current-timedelta(hours=3)).isoformat())
            self.assertEqual([p["id"] for p in chosen],[fresh])
            self.assertEqual(get_post(db,unscheduled)["status"],"approved")
            self.assertEqual(get_post(db,stale)["status"],"scheduled")
            with self.assertRaises(ValidationError):
                pending(db,now=current.isoformat(),
                        scheduled_not_before=(current-timedelta(hours=3)).isoformat())

    def test_safe_worker_cli_does_not_send_unapproved_or_missed_window(self):
        now=datetime.now(timezone.utc)
        with connect(self.db) as db:
            unscheduled=self.add(db)
            stale=self.add(db)
            fresh=self.add(db)
            transition(db,stale,"schedule",(now-timedelta(hours=5)).isoformat(timespec="seconds"))
            transition(db,fresh,"schedule",(now-timedelta(minutes=5)).isoformat(timespec="seconds"))
        env={"REACHWEAVER_ALLOW_LIVE":"1","REACHWEAVER_ENV":"prod",
             "REACHWEAVER_TEST_GUARD_IDENTIFIER":"owner.bsky.social",
             "REACHWEAVER_TEST_GUARD_APP_PASSWORD":"mock-app-secret"}
        output=io.StringIO()
        with patch.dict(os.environ,env),patch("reachweaver.cli.check_connection",return_value=object()),\
             patch("reachweaver.cli.publish",return_value=("remote-fresh","https://example.com/remote")) as remote,\
             contextlib.redirect_stdout(output):
            code=main(["--db",self.db,"--accounts",self.config,"publish","--live",
                       "--scheduled-only","--max-lateness-minutes","180"])
        self.assertEqual(code,0)
        self.assertEqual(remote.call_count,1)
        self.assertEqual(remote.call_args.args[2],fresh)
        with connect(self.db) as db:
            self.assertEqual(get_post(db,unscheduled)["status"],"approved")
            self.assertEqual(get_post(db,stale)["status"],"scheduled")
            self.assertEqual(get_post(db,fresh)["status"],"published")

    def test_safe_worker_dry_run_remains_network_free(self):
        with connect(self.db) as db:
            self.add(db)
        out=io.StringIO()
        with patch("reachweaver.cli.publish") as publish,contextlib.redirect_stdout(out):
            code=main(["--db",self.db,"publish","--scheduled-only"])
        self.assertEqual(code,0)
        self.assertIn("DRY RUN: 0 due approved posts",out.getvalue())
        publish.assert_not_called()

    def test_live_cli_bad_cap_blocked_before_send(self):
        with connect(self.db) as db:
            a=self.add(db)
        with patch("reachweaver.cli.publish") as publisher:
            with contextlib.redirect_stderr(io.StringIO()):
                code=main(["--db",self.db,"publish","--live","--post-id",a,"--max-per-24h","0"])
        self.assertEqual(code,2)
        publisher.assert_not_called()


if __name__=="__main__":
    unittest.main()
