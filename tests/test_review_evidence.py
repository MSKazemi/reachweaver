"""`eu-review-evidence`: read-only human-review evidence for published posts (EU AI Act Art. 50(4) helper).

Offline, synthetic posts only. The report must never write the database or the approval ledger, never repair
anything, and must cope with databases that lack newer columns (authorship, nonce, binding).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from reachweaver.cli import main
from reachweaver.ledger import LEDGER_FILENAME
from reachweaver.review_evidence import NOTICE, build, render_markdown, render_text
from reachweaver.storage import add_post, claim_sending, connect, connect_readonly, finish_send, transition

NOW = "2026-10-21T12:00:00+00:00"
SENT = "2026-10-01T09:00:00+00:00"


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        os.chmod(self.root, 0o700)
        self.db = str(self.root / "outbox.sqlite")
        self.ledger = self.root / LEDGER_FILENAME

    def publish(self, db, content="A synthetic research note", *, account="my-bluesky", platform="bluesky",
                approver: str | None = "owner", when=SENT) -> str:
        pid = add_post(db, account, platform, content, "evidence")
        transition(db, pid, "approve", approver=approver)
        self.assertTrue(claim_sending(db, pid, now=when))
        self.assertTrue(finish_send(db, pid, f"remote-{pid}", f"https://example.org/{pid}"))
        return pid

    def report(self, **kwargs) -> dict:
        with connect_readonly(self.db) as db:
            return build(db, now=datetime.fromisoformat(NOW), **kwargs)


class EvidenceTests(Base):
    def test_full_flow_is_recorded_with_matching_ledger_line(self):
        with connect(self.db) as db:
            pid = self.publish(db, "Exact approved text")
            stored = db.execute("SELECT approved_payload_sha256, approved_at FROM posts WHERE id=?", (pid,)).fetchone()
        report = self.report()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["notice"], NOTICE)
        self.assertEqual(report["ledger"]["chain"], "verified")
        self.assertEqual(report["ledger"]["status"], "ok")
        post = report["posts"][0]
        self.assertEqual(post["post_id"], pid)
        self.assertEqual(post["evidence"], "recorded")
        self.assertEqual(post["approved_by"], "owner")
        self.assertEqual(post["approved_payload_sha256"], stored["approved_payload_sha256"])
        self.assertEqual(post["approved_at"], stored["approved_at"])
        self.assertEqual(post["text_sha256"], hashlib.sha256("Exact approved text".encode("utf-8")).hexdigest())
        self.assertEqual(post["ledger"]["state"], "consumed")
        self.assertEqual(post["ledger"]["at"], SENT)
        self.assertTrue(post["ledger"]["payload_sha256_matches"])
        self.assertEqual(post["gaps"], ["no authorship declaration recorded"])
        self.assertIsNone(report["authorship_column"])
        self.assertEqual(report["legal_basis"]["kind"], "secondary")
        self.assertIn("human review or editorial control", report["legal_basis"]["exemption_quote"])
        self.assertFalse(report["database_written"])
        self.assertFalse(report["ledger_written"])

    def test_unnamed_approver_and_pre_nonce_rows_are_partial_and_legacy_rows_missing(self):
        with connect(self.db) as db:
            unnamed = self.publish(db, "Approved without a name", approver=None)
            old = self.publish(db, "Approved before nonces", when="2026-10-02T09:00:00+00:00")
            legacy = add_post(db, "my-bluesky", "bluesky", "Legacy row", "evidence")
            db.execute("UPDATE posts SET approval_nonce=NULL WHERE id=?", (old,))
            db.execute("UPDATE posts SET status='published' WHERE id=?", (legacy,))
        posts = {p["post_id"]: p for p in self.report()["posts"]}
        self.assertEqual(posts[unnamed]["evidence"], "partial")
        self.assertEqual(posts[unnamed]["approver_source"], "library call (no CLI)")
        self.assertIn("no approver name recorded", posts[unnamed]["gaps"])
        self.assertEqual(posts[old]["ledger"]["state"], "no_nonce")
        self.assertEqual(posts[old]["evidence"], "partial")
        self.assertEqual(posts[legacy]["evidence"], "missing")

    def test_authorship_column_and_confirmation_token_are_read_when_present(self):
        with connect(self.db) as db:
            pid = self.publish(db)
            db.execute("ALTER TABLE posts ADD COLUMN authorship TEXT")
            db.execute("UPDATE posts SET authorship='ai_assisted' WHERE id=?", (pid,))
            db.execute("INSERT INTO event_log(at, post_id, action, detail) VALUES(?,?,?,?)",
                       (NOW, pid, "approve", "approval_sha256=abc approver=owner channel=cli_flag "
                        "confirmation=tty_hash_prefix provider-text-SHOULD-NOT-APPEAR"))
        report = self.report()
        post = report["posts"][0]
        self.assertEqual(report["authorship_column"], "authorship")
        self.assertEqual(post["authorship"], "ai_assisted")
        self.assertTrue(post["authorship_recorded"])
        self.assertEqual(post["human_confirmation"], "tty_hash_prefix")
        for rendered in (json.dumps(report), render_text(report), render_markdown(report)):
            self.assertNotIn("SHOULD-NOT-APPEAR", rendered)

    def test_filters(self):
        with connect(self.db) as db:
            first = self.publish(db, "First", account="my-bluesky", platform="bluesky")
            second = self.publish(db, "Second", account="my-mastodon", platform="mastodon",
                                  when="2026-10-03T09:00:00+00:00")
            db.execute("UPDATE event_log SET at=? WHERE post_id=? AND action='published'", ("2026-09-01T00:00:00+00:00",
                                                                                          first))
        self.assertEqual([p["post_id"] for p in self.report(account="my-mastodon")["posts"]], [second])
        self.assertEqual([p["post_id"] for p in self.report(platform="bluesky")["posts"]], [first])
        self.assertEqual([p["post_id"] for p in self.report(post_ids=[first])["posts"]], [first])
        self.assertEqual([p["post_id"] for p in self.report(since="2026-10-01")["posts"]], [second])
        limited = self.report(limit=1)
        self.assertTrue(limited["summary"]["truncated"])
        self.assertEqual(self.report(account="nobody")["status"], "no_published_posts")


class ReadOnlyTests(Base):
    def test_cli_formats_change_nothing(self):
        with connect(self.db) as db:
            self.publish(db)
        before = (digest(Path(self.db)), digest(self.ledger), sorted(p.name for p in self.root.iterdir()))
        code, out = run("--db", self.db, "eu-review-evidence", "--json", "--now", NOW)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["kind"], "reachweaver.eu-review-evidence")
        code, md = run("--db", self.db, "eu-review-evidence", "--format", "markdown", "--since", "30d", "--now", NOW)
        self.assertEqual(code, 0)
        self.assertIn(NOTICE, md)
        self.assertIn("| Post | Platform | Published |", md)
        self.assertIn("Verify on EUR-Lex", md)
        code, text = run("--db", self.db, "eu-review-evidence")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("NOTE: " + NOTICE))
        self.assertEqual((digest(Path(self.db)), digest(self.ledger), sorted(p.name for p in self.root.iterdir())),
                         before)

    def test_broken_ledger_is_reported_and_never_repaired(self):
        with connect(self.db) as db:
            self.publish(db)
        self.ledger.write_text(self.ledger.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
        os.chmod(self.ledger, 0o600)
        before = digest(self.ledger)
        report = self.report()
        self.assertEqual(report["ledger"]["status"], "broken")
        self.assertEqual(report["ledger"]["chain"], "approval_ledger_broken")
        self.assertEqual(report["posts"][0]["ledger"]["state"], "ledger_unreadable")
        self.assertEqual(report["posts"][0]["evidence"], "partial")
        self.assertEqual(digest(self.ledger), before)

    def test_missing_ledger_is_not_created(self):
        with connect(self.db) as db:
            self.publish(db)
        self.ledger.unlink()
        report = self.report()
        self.assertEqual(report["ledger"]["status"], "unavailable")
        self.assertFalse(self.ledger.exists())

    def test_missing_database_is_not_created(self):
        code, out = run("--db", self.db, "eu-review-evidence", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "no_database")
        self.assertFalse(Path(self.db).exists())

    def test_old_database_without_newer_columns(self):
        con = sqlite3.connect(self.db)
        con.executescript("""CREATE TABLE posts (id TEXT PRIMARY KEY, account_id TEXT, platform TEXT, content TEXT,
                             status TEXT, created_at TEXT, approved_at TEXT);
                             INSERT INTO posts VALUES ('p1','my-bluesky','bluesky','Old text','published',
                             '2026-01-01T00:00:00+00:00','2026-01-01T01:00:00+00:00');""")
        con.close()
        report = self.report()
        post = report["posts"][0]
        self.assertEqual(post["evidence"], "missing")
        self.assertIsNone(post["published_at"])
        self.assertEqual(report["ledger"]["status"], "absent")
        self.assertEqual(report["ledger"]["chain"], "absent")


if __name__ == "__main__":
    unittest.main()
