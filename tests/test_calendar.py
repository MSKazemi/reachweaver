"""Offline calendar planner regression tests: no network or live sends."""
import sqlite3
import unittest
from datetime import datetime, timezone

from reachweaver.calendar import propose_calendar, apply_calendar
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, approval_payload_sha256

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        # The real schema (approval binding columns included, #7): apply_calendar refuses
        # to schedule an approval that is not bound to its payload hash.
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)

    def post(self, pid, platform='bluesky', status='approved', account='personal', scheduled_at=None):
        self.db.execute('INSERT INTO posts(id,account_id,platform,content,status,created_at,scheduled_at) '
                        'VALUES(?,?,?,?,?,?,?)',
                        (pid, account, platform, 'Useful release note.', status, '2026-10-01T00:00:00+00:00',
                         scheduled_at))
        if status in ('approved', 'scheduled'):  # bind like a human approve (+ schedule) would
            row = self.db.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
            nonce = 'ab' * 16  # wave 9 item 1: a human approve also draws a nonce, bound into the hash
            self.db.execute('UPDATE posts SET approval_nonce=? WHERE id=?', (nonce, pid))
            row = self.db.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
            self.db.execute('UPDATE posts SET approved_payload_sha256=? WHERE id=?',
                            (approval_payload_sha256(row), pid))

    def plan(self, *, platform='bluesky', account='personal', start='2026-10-12', days=14,
             timezone_name='Europe/Rome', **kw):
        return propose_calendar(self.db, account_id=account, platform=platform, audience_timezone=timezone_name,
                                start=start, days=days, now=NOW, **kw)

    def test_utc_and_local_time_and_draft_safety(self):
        self.post('a')
        self.post('b')
        plan = self.plan()
        self.assertEqual(len(plan['entries']), 2)
        self.assertEqual(plan['entries'][0]['scheduled_local'], '2026-10-12T18:00+02:00')
        self.assertEqual(plan['entries'][0]['scheduled_utc'], '2026-10-12T16:00:00+00:00')
        self.assertEqual(plan['entries'][1]['scheduled_local'], '2026-10-13T18:00+02:00')
        self.assertEqual(plan['entries'][0]['schedule_status'], 'proposed_only')
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE status='approved'").fetchone()[0],2)
        self.assertEqual(plan['entries'][0]['evidence'],'industry_observational')

    def test_apply_is_local_only_after_approval(self):
        self.post('a')
        self.post('b')
        result = apply_calendar(self.db, self.plan(), now=NOW)
        self.assertEqual([row['schedule_status'] for row in result['entries']], ['locally_scheduled']*2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE status='scheduled'").fetchone()[0], 2)
        self.assertIn('local SQLite only',result['delivery_note'])

    def test_draft_preview_never_allowed_for_apply(self):
        self.post('a', status='draft')
        with self.assertRaises(ValidationError): self.plan()
        preview = self.plan(include_drafts=True)
        self.assertEqual(preview['entries'][0]['post_status'], 'draft')
        with self.assertRaisesRegex(ValidationError, 'lack exact-content approval'):
            apply_calendar(self.db, preview, now=NOW)
        self.assertEqual(self.db.execute("SELECT status FROM posts WHERE id='a'").fetchone()[0],'draft')

    def test_manual_network_cannot_apply(self):
        self.post('a',platform='x')
        plan=self.plan(platform='x')
        self.assertEqual(plan['entries'][0]['delivery'],'manual_native_handoff')
        with self.assertRaisesRegex(ValidationError, 'manual-only'):
            apply_calendar(self.db,plan,now=NOW)
        self.assertEqual(self.db.execute("SELECT status FROM posts WHERE id='a'").fetchone()[0],'approved')

    def test_existing_post_collision_and_min_spacing(self):
        self.post('old', status='scheduled', scheduled_at='2026-10-12T16:00:00+00:00')
        self.post('a')
        planned=self.plan()
        self.assertEqual(planned['entries'][0]['scheduled_local'],'2026-10-13T18:00+02:00')

    def test_atomic_rollback_when_plan_stale(self):
        self.post('a')
        self.post('b')
        plan=self.plan()
        self.db.execute("UPDATE posts SET status='cancelled' WHERE id='b'")
        with self.assertRaisesRegex(ValidationError, 'Posts changed'):
            apply_calendar(self.db,plan,now=NOW)
        self.assertEqual(self.db.execute("SELECT status FROM posts WHERE id='a'").fetchone()[0],'approved')

    def test_atomic_rollback_on_new_collision(self):
        self.post('a')
        plan=self.plan()
        self.post('c',status='scheduled',scheduled_at=plan['entries'][0]['scheduled_utc'])
        with self.assertRaisesRegex(ValidationError, 'collision'):
            apply_calendar(self.db,plan,now=NOW)
        self.assertEqual(self.db.execute("SELECT status FROM posts WHERE id='a'").fetchone()[0],'approved')

    def test_approval_pause_blocks_batch(self):
        self.post('a')
        plan=self.plan()
        self.db.execute("INSERT INTO settings VALUES ('paused','1')")
        with self.assertRaisesRegex(ValidationError,'paused'):
            apply_calendar(self.db,plan,now=NOW)

    def test_first_party_custom_slot_does_not_forge_evidence(self):
        self.post('a',platform='mastodon')
        plan=self.plan(platform='mastodon',slots=['Wed@10:00'],timezone_name='America/New_York')
        self.assertEqual(plan['entries'][0]['scheduled_local'], '2026-10-14T10:00-04:00')
        self.assertEqual(plan['entries'][0]['evidence'], 'operator_supplied')
        self.assertEqual(plan['entries'][0]['confidence'], 'unverified')
        with self.assertRaisesRegex(ValidationError,'supply one or more --slot'):
            self.plan(platform='mastodon')

    def test_dst_gap_rejected(self):
        self.post('a')
        with self.assertRaisesRegex(ValidationError,'DST'):
            self.plan(start='2027-03-28',days=1,slots=['Sun@02:30'])

    def test_dst_fold_rejected(self):
        self.post('a')
        with self.assertRaisesRegex(ValidationError,'DST'):
            self.plan(start='2026-10-25',days=1,slots=['Sun@02:30'])

    def test_blackout_and_post_order(self):
        self.post('a')
        self.post('b')
        plan=self.plan(blackout=['2026-10-12'])
        self.assertEqual([r['scheduled_local'] for r in plan['entries']],
                         ['2026-10-13T18:00+02:00','2026-10-14T18:00+02:00'])

    def test_exhaustion_fails_without_changes(self):
        self.post('a')
        self.post('b')
        with self.assertRaisesRegex(ValidationError,'Insufficient collision-free'):
            self.plan(days=1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE status='approved'").fetchone()[0],2)

    def test_selected_account_and_duplicate_rejections(self):
        self.post('a')
        with self.assertRaisesRegex(ValidationError,'distinct'):
            self.plan(post_ids=['a','a'])
        with self.assertRaisesRegex(ValidationError,'Selected post does not belong'):
            self.plan(account='different',post_ids=['a'])
        with self.assertRaisesRegex(ValidationError,'Unknown IANA'):
            self.plan(timezone_name='Mars/Phobos')
        with self.assertRaisesRegex(ValidationError,'Duplicate slot'):
            self.plan(slots=['Mon@18:00','Mon@18:00'])

    def test_developer_articles_remain_manual_and_need_owner_time_slots(self):
        from reachweaver.policy import validate_post
        for platform in ('devto', 'hashnode'):
            self.assertEqual(validate_post(platform, '# An original technical article'), [])
            self.post(platform,platform=platform)
            with self.assertRaisesRegex(ValidationError,'supply one or more --slot'):
                self.plan(platform=platform)
            plan=self.plan(platform=platform,slots=['Wed@10:00'])
            self.assertEqual(plan['entries'][0]['delivery'],'manual_native_handoff')
            with self.assertRaisesRegex(ValidationError,'manual-only'):
                apply_calendar(self.db,plan,now=NOW)

    def test_only_approved_bounded_and_not_auto_published(self):
        self.post('a')
        self.post('b',status='draft')
        plan=self.plan()
        self.assertEqual(len(plan['entries']),1)
        self.assertNotIn('publish',plan['entries'][0]['schedule_status'])
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE status='scheduled'").fetchone()[0],0)



class ExplorationShareTests(unittest.TestCase):
    """Issue #16 R3: a capped, labelled explore/exploit split; proposals only, never randomized."""

    setUp = CalendarTests.setUp
    post = CalendarTests.post
    plan = CalendarTests.plan

    def test_golden_output_is_unchanged_without_the_flag(self):
        import json
        self.post('a')
        self.post('b')

        def entry(pid, local, utc):
            return {
                "native_horizon_days": None, "within_native_horizon_now": None,
                "post_id": pid, "account_id": "personal", "platform": "bluesky",
                "post_status": "approved", "audience_timezone": "Europe/Rome",
                "scheduled_local": local, "scheduled_utc": utc,
                "evidence": "industry_observational", "confidence": "low",
                "evidence_url": "https://buffer.com/resources/best-time-to-post-social-media/",
                "delivery": "reachweaver_local_queue", "schedule_status": "proposed_only",
            }
        golden = {
            "account_id": "personal", "platform": "bluesky",
            "start_date": "2026-10-12", "end_date": "2026-10-25", "spacing_hours": 20,
            "delivery_note": "This is only a proposed local calendar; no credentials or platforms contacted. "
                             "Native scheduling needs a separate confirmed platform receipt.",
            "entries": [entry('a', '2026-10-12T18:00+02:00', '2026-10-12T16:00:00+00:00'),
                        entry('b', '2026-10-13T18:00+02:00', '2026-10-13T16:00:00+00:00')],
        }
        expected = json.dumps(golden, indent=2)
        self.assertEqual(json.dumps(self.plan(), indent=2), expected)
        self.assertEqual(json.dumps(self.plan(explore_share=0), indent=2), expected)
        self.assertEqual(json.dumps(self.plan(explore_share=0.0, explore_slots=None), indent=2), expected)

    def test_capped_interleaved_roles_and_labels(self):
        for pid in 'abcd':
            self.post(pid)
        plan = self.plan(explore_share=0.5, explore_slots=['Sat@11:00'])
        entries = plan['entries']
        self.assertEqual([e['role'] for e in entries], ['exploit', 'explore', 'exploit', 'explore'])
        self.assertEqual([e['scheduled_local'] for e in entries],
                         ['2026-10-12T18:00+02:00', '2026-10-17T11:00+02:00',
                          '2026-10-13T18:00+02:00', '2026-10-24T11:00+02:00'])
        for e in entries:
            self.assertEqual(e['schedule_status'], 'proposed_only')
            if e['role'] == 'explore':
                self.assertEqual((e['evidence'], e['confidence'], e['evidence_url']),
                                 ('operator_explore_hypothesis', 'unverified', None))
            else:
                self.assertEqual((e['evidence'], e['confidence']), ('industry_observational', 'low'))
        block = plan['exploration']
        self.assertEqual((block['explore_entries'], block['exploit_entries']), (2, 2))
        self.assertEqual(block['explore_share_cap'], 0.5)
        self.assertFalse(block['randomized'])
        self.assertEqual(block['assignment'], 'deterministic_interleave')
        self.assertEqual(block['explore_slots'], ['Sat@11:00'])
        self.assertEqual(len(block['exploit_slots']), 7)
        # The generic prior is baseline-eval's own baseline, so only the explore slot is an arm.
        self.assertEqual(block['baseline_eval_slots'], ['explore:Sat@11:00'])
        self.assertTrue(any('Nothing is randomized' in note for note in block['notes']))
        self.assertEqual(self.db.execute("SELECT count(*) FROM posts WHERE status='approved'").fetchone()[0], 4)

    def test_explore_count_never_exceeds_floor_of_share(self):
        for count, share, expected in ((1, 0.3, 0), (3, 0.3, 0), (4, 0.25, 1), (7, 0.5, 3), (10, 0.3, 3)):
            with self.subTest(count=count, share=share):
                self.db.execute("DELETE FROM posts")
                for i in range(count):
                    self.post(f'p{i}')
                plan = self.plan(days=60, explore_share=share, explore_slots=['Sat@11:00', 'Sun@11:00'])
                self.assertEqual(plan['exploration']['explore_entries'], expected)
                self.assertEqual(sum(e['role'] == 'explore' for e in plan['entries']), expected)
                self.assertEqual(any('Too few selected posts' in n for n in plan['exploration']['notes']),
                                 expected == 0)

    def test_operator_exploit_slots_become_baseline_eval_arms(self):
        self.post('a', platform='mastodon')
        self.post('b', platform='mastodon')
        plan = self.plan(platform='mastodon', slots=['Wed@10:00'], explore_share=0.5, explore_slots=['Thu@15:00'])
        self.assertEqual(plan['exploration']['baseline_eval_slots'], ['exploit:Wed@10:00', 'explore:Thu@15:00'])
        self.assertEqual(plan['exploration']['exploit_evidence'], 'operator_supplied')

    def test_spacing_blackout_and_apply_gating_still_hold(self):
        self.post('a')
        self.post('b')
        plan = self.plan(days=21, explore_share=0.5, explore_slots=['Mon@20:00'], blackout=['2026-10-19'])
        # Mon 12 20:00 is 2 h after the exploit post (spacing 20 h); Mon 19 is blacked out.
        self.assertEqual(plan['entries'][1]['scheduled_local'], '2026-10-26T20:00+01:00')
        with self.assertRaisesRegex(ValidationError, 'Insufficient collision-free'):
            self.plan(days=7, explore_share=0.5, explore_slots=['Mon@20:00'])
        applied = apply_calendar(self.db, plan, now=NOW)
        self.assertEqual([e['schedule_status'] for e in applied['entries']], ['locally_scheduled'] * 2)
        self.assertEqual([e['role'] for e in applied['entries']], ['exploit', 'explore'])
        self.post('c', status='draft')
        preview = self.plan(post_ids=['c'], include_drafts=True, explore_share=0.5, explore_slots=['Sat@11:00'])
        with self.assertRaisesRegex(ValidationError, 'lack exact-content approval'):
            apply_calendar(self.db, preview, now=NOW)

    def test_invalid_share_and_slots_are_refused(self):
        self.post('a')
        for share in (0.51, 1, -0.1, float('nan'), float('inf'), True):
            with self.subTest(share=share), self.assertRaisesRegex(ValidationError, 'explore-share'):
                self.plan(explore_share=share, explore_slots=['Sat@11:00'])
        with self.assertRaisesRegex(ValidationError, 'needs at least one --explore-slot'):
            self.plan(explore_share=0.2)
        with self.assertRaisesRegex(ValidationError, '--explore-slot needs --explore-share'):
            self.plan(explore_slots=['Sat@11:00'])
        with self.assertRaisesRegex(ValidationError, 'repeats an exploit slot'):
            self.plan(explore_share=0.5, explore_slots=['Mon@18:00'])
        with self.assertRaisesRegex(ValidationError, 'Duplicate slot'):
            self.plan(explore_share=0.5, explore_slots=['Sat@11:00', 'sat@11:00'])


class CalendarCLITests(unittest.TestCase):
    def test_cli_plan_and_apply_do_not_publish(self):
        import contextlib
        import io
        import json
        import tempfile
        from datetime import timedelta
        from pathlib import Path
        from reachweaver.cli import main
        from reachweaver.storage import connect, add_post, transition

        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "outbox.sqlite")
            config = Path(temp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [
                {"id": "my-bluesky", "platform": "bluesky",
                 "env_prefix": "REACHWEAVER_TEST_PLAN",
                 "handle": "test.bsky.social"}
            ]}), encoding="utf-8")
            with connect(db_path) as db:
                pid = add_post(db, "my-bluesky", "bluesky", "A factual and helpful open-source tip")
                transition(db, pid, "approve")
            start = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
            argv = [
                "--db", db_path, "--accounts", str(config), "calendar-plan",
                "--account", "my-bluesky", "--timezone", "UTC",
                "--start", start, "--days", "5", "--post-id", pid,
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(argv), 0)
            proposal = json.loads(output.getvalue())
            self.assertEqual(proposal["entries"][0]["schedule_status"], "proposed_only")
            with connect(db_path) as db:
                self.assertEqual(db.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()[0], "approved")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(argv + ["--apply"]), 0)
            applied = json.loads(output.getvalue())
            self.assertEqual(applied["entries"][0]["schedule_status"], "locally_scheduled")
            with connect(db_path) as db:
                row = db.execute("SELECT status,remote_id FROM posts WHERE id=?", (pid,)).fetchone()
                self.assertEqual(row["status"], "scheduled")
                self.assertIsNone(row["remote_id"])

    def test_cli_explore_share_labels_roles_and_refuses_over_cap(self):
        import contextlib
        import io
        import json
        import tempfile
        from datetime import timedelta
        from pathlib import Path
        from reachweaver.cli import main
        from reachweaver.storage import connect, add_post, transition

        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "outbox.sqlite")
            config = Path(temp) / "accounts.json"
            config.write_text(json.dumps({"accounts": [
                {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_PLAN",
                 "handle": "test.bsky.social"}]}), encoding="utf-8")
            with connect(db_path) as db:
                for text in ("First factual tip", "Second factual tip"):
                    transition(db, add_post(db, "my-bluesky", "bluesky", text), "approve")
            start = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
            argv = ["--db", db_path, "--accounts", str(config), "calendar-plan", "--account", "my-bluesky",
                    "--timezone", "UTC", "--start", start, "--days", "14"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(argv + ["--explore-share", "0.5", "--explore-slot", "Sat@11:00"]), 0)
            proposal = json.loads(output.getvalue())
            self.assertEqual([e["role"] for e in proposal["entries"]], ["exploit", "explore"])
            self.assertEqual(proposal["exploration"]["baseline_eval_slots"], ["explore:Sat@11:00"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(argv), 0)
            self.assertNotIn("exploration", json.loads(output.getvalue()))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(argv + ["--explore-share", "0.6", "--explore-slot", "Sat@11:00"]), 2)
            self.assertIn("at most 0.5", err.getvalue())
            with connect(db_path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM posts WHERE status='approved'").fetchone()[0], 2)

if __name__ == '__main__':
    unittest.main()
