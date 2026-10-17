"""`trace`: one post's audit events joined with worker-log lines, read-only and offline (issue #26)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import trace as post_trace
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, claim_sending, connect, connect_readonly, finish_send, transition, uncertain_send
from reachweaver.worker_log import WorkerLog

SECRET = "-".join(("very", "private", "app", "password", "value"))
NOW = "2026-10-08T10:00:00+00:00"


def worker_run(stream, posts, *, outcome):
    """Write the lines a real `publish --log-json` run would write for these posts."""
    log = WorkerLog(stream, dry_run=False)
    log.start(live_requested=True, scheduled_only=False, limit=10, post_id=None)
    log.due(len(posts))
    for post in posts:
        log.claimed(post)
        if outcome == "published":
            log.published(post, "at://did:x/post/1", "https://bsky.app/profile/o/post/1?token=" + SECRET)
        else:
            log.needs_review(post, send_class="ambiguous", code="network_timeout")
    log.end(exit_code=0)
    return log.run_id


class TraceBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "t.sqlite"
        env = patch.dict(os.environ, {"REACHWEAVER_T_APP_PASSWORD": SECRET})  # how a credential value becomes known
        env.start()
        self.addCleanup(env.stop)
        with connect(str(self.db)) as con:
            self.sent = add_post(con, "acct-a", "bluesky", "Sent text")
            transition(con, self.sent, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.sent, now=NOW))
            finish_send(con, self.sent, "at://did:x/post/1", "https://bsky.app/profile/o/post/1")
            self.review = add_post(con, "acct-b", "bluesky", "Ambiguous text")
            transition(con, self.review, "approve", approver="owner")
            self.assertTrue(claim_sending(con, self.review, now=NOW))
            uncertain_send(con, self.review, "network error " + SECRET)
            self.draft = add_post(con, "acct-a", "bluesky", "Never sent")
            sent_row = dict(con.execute("SELECT * FROM posts WHERE id=?", (self.sent,)).fetchone())
            other_row = dict(con.execute("SELECT * FROM posts WHERE id=?", (self.review,)).fetchone())
        self.log = self.base / "worker.jsonl"
        buffer = io.StringIO()
        self.run_published = worker_run(buffer, [sent_row], outcome="published")
        self.run_other = worker_run(buffer, [other_row], outcome="needs_review")
        self.log.write_text("not json at all\n" + buffer.getvalue() + '{"event": "run_start", "schema_version": 99}\n',
                            encoding="utf-8")

    def trace(self, post_id, logs=(), stdin=None):
        with connect_readonly(self.db) as con:
            return post_trace.build_trace(con, post_id, logs, stdin=stdin)


class TraceTests(TraceBase):
    def test_database_events_and_the_matching_run_are_joined_oldest_first(self):
        trace = self.trace(self.sent, [self.log])
        self.assertEqual(trace["post"]["status"], "published")
        self.assertEqual(trace["send_attempts"], 1)
        self.assertIn("Published after 1 send attempt", trace["summary"])
        self.assertEqual([r["run_id"] for r in trace["runs"]], [self.run_published])  # not the other post's run
        run = trace["runs"][0]
        self.assertEqual((run["outcome"], run["exit_code"], run["dry_run"]), ("completed", 0, False))
        self.assertEqual(run["this_post"], ["post_claimed", "post_published"])
        sources = {item["source"] for item in trace["timeline"]}
        self.assertEqual(sources, {"database", "worker_log"})
        stamps = [item["at"] for item in trace["timeline"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertIn("send_claimed", [item["event"] for item in trace["timeline"]])

    def test_unknown_lines_are_skipped_and_counted_not_trusted(self):
        stats = self.trace(self.sent, [self.log])["worker_logs"]
        self.assertEqual(stats["skipped_not_worker_log"], 2)  # the plain line and the wrong schema version
        self.assertEqual(stats["files"], ["worker.jsonl"])

    def test_secrets_never_reach_the_trace(self):
        blob = json.dumps([self.trace(self.review, [self.log]), self.trace(self.sent, [self.log])])
        self.assertNotIn(SECRET, blob)

    def test_needs_review_says_check_the_platform_first(self):
        trace = self.trace(self.review, [self.log])
        self.assertIn("unknown state", trace["summary"])
        self.assertIn("post_needs_review", trace["runs"][0]["this_post"])

    def test_no_attempt_and_no_log(self):
        trace = self.trace(self.draft)
        self.assertEqual(trace["send_attempts"], 0)
        self.assertEqual(trace["runs"], [])
        self.assertIsNone(trace["worker_logs"])
        self.assertIn("No send attempt yet", trace["summary"])
        self.assertIn("No worker log given", post_trace.render_text(trace))

    def test_journalctl_prefixed_lines_and_stdin_are_read(self):
        prefixed = "".join("Oct 08 10:00:00 host reachweaver[1]: " + line + "\n"
                           for line in self.log.read_text(encoding="utf-8").splitlines())
        trace = self.trace(self.sent, ["-"], stdin=io.StringIO(prefixed))
        self.assertEqual([r["run_id"] for r in trace["runs"]], [self.run_published])
        self.assertEqual(trace["worker_logs"]["files"], ["stdin"])

    def test_oversized_lines_and_bad_files_are_refused(self):
        big = self.base / "big.jsonl"
        big.write_text("{" + "x" * (post_trace.MAX_LINE_BYTES + 10) + "}\n", encoding="utf-8")
        self.assertEqual(self.trace(self.sent, [big])["worker_logs"]["skipped_too_long"], 1)
        link = self.base / "link.jsonl"
        os.symlink(self.log, link)
        with self.assertRaises(ValidationError):
            self.trace(self.sent, [link])
        with self.assertRaises(ValidationError):
            self.trace(self.sent, [self.base / "missing.jsonl"])

    def test_unknown_post_is_refused(self):
        with self.assertRaises(ValidationError):
            self.trace("nope")

    def test_text_rendering_lists_runs_and_timeline(self):
        text = post_trace.render_text(self.trace(self.sent, [self.log]))
        self.assertIn("Worker runs that handled this post:", text)
        self.assertIn(self.run_published, text)
        self.assertIn("post_published", text)
        self.assertIn("Nothing was sent", text)


class TraceCommandTests(TraceBase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_command_is_read_only_and_json_matches(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        names = sorted(p.name for p in self.db.parent.iterdir())
        code, out, err = self.run_cli("trace", self.sent, "--log", str(self.log), "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["report"], "reachweaver_post_trace")
        code, out, _ = self.run_cli("trace", self.sent)
        self.assertEqual(code, 0)
        self.assertIn("Timeline (UTC):", out)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), names)

    def test_missing_database_is_not_created(self):
        missing = self.base / "none" / "x.sqlite"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(missing), "trace", "abc"])
        self.assertEqual(code, 2)
        self.assertFalse(missing.parent.exists())

    def test_unknown_post_exits_2(self):
        self.assertEqual(self.run_cli("trace", "nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
