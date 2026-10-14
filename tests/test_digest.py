"""Tests for the weekly digest (issue #45 W7): local file only, no network, no send path."""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.digest import render_digest, write_digest
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import load_snapshot
from reachweaver.suggest import build_suggestions
from reachweaver.storage import add_post, claim_sending, connect, finish_send, store_metrics, transition

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "examples/profile-post-audit.example.json"
SEO = ROOT / "examples/seo-briefs.example.json"


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(socket, "create_connection", side_effect=AssertionError("network used")), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.suggestions = build_suggestions(snapshot=load_snapshot(SNAP), limit=6)

    def test_structure_and_limits_are_always_present(self):
        text = render_digest(self.suggestions, generated_at="2026-10-13T00:00:00+00:00")
        self.assertTrue(text.startswith("# ReachWeaver weekly digest"))
        shown, total = self.suggestions["shown"], self.suggestions["total_actionable_signals"]
        self.assertGreater(shown, 0)
        self.assertIn(f"## Next actions ({shown} of {total} signals)", text)
        self.assertIn("## What this digest is not", text)
        self.assertIn("not a platform algorithm", text)
        self.assertIn("Not included. Pass `--include-db`", text)
        self.assertIn("never sent by ReachWeaver", text)

    def test_every_action_carries_evidence_and_next_step(self):
        text = render_digest(self.suggestions, generated_at="t")
        self.assertEqual(text.count("   - Evidence: `"), len(self.suggestions["actions"]))
        self.assertEqual(text.count("   - Next: "), len(self.suggestions["actions"]))

    def test_free_text_is_escaped_and_identifiers_are_sanitised(self):
        hostile = json.loads(json.dumps(self.suggestions))
        action = hostile["actions"][0]
        action["platform"] = "x|<script>[a](http://evil.example)"
        action["title"] = "# injected\n| table |"
        action["kind"] = "kind`; rm -rf"
        text = render_digest(hostile, generated_at="t")
        self.assertNotIn("<script>", text)
        self.assertNotIn("[a](http", text)
        self.assertNotIn("\n# injected", text)
        self.assertIn("`kindrm-rf`", text)

    def test_empty_actions_do_not_claim_optimised(self):
        empty = dict(self.suggestions, actions=[], shown=0, total_actionable_signals=0)
        text = render_digest(empty, generated_at="t")
        self.assertIn("does not prove accounts are optimized", text)

    def test_rejects_a_foreign_schema(self):
        with self.assertRaises(ValidationError):
            render_digest({"schema_version": "other"}, generated_at="t")

    def test_report_with_no_published_posts_says_so(self):
        envelope = {"generated_at": "t", "platforms": {}, "note": ""}
        self.assertIn("no published posts yet", render_digest(self.suggestions, envelope, generated_at="t"))


class WriteTests(unittest.TestCase):
    def test_new_owner_only_file_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digest.md"
            write_digest(path, "hello\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(ValidationError, "refuses to overwrite"):
                write_digest(path, "second\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")

    def test_symlink_target_is_never_written_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            link = Path(tmp) / "link.md"
            link.symlink_to(victim)
            with self.assertRaises(ValidationError):
                write_digest(link, "overwritten")
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_missing_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValidationError, "existing directory"):
                write_digest(Path(tmp) / "nope" / "d.md", "x")


class CliTests(unittest.TestCase):
    def test_writes_file_offline_without_creating_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, dbp = Path(tmp) / "d.md", Path(tmp) / "must-not-exist.sqlite"
            code, stdout, _ = run(["--db", str(dbp), "weekly-digest", "--snapshot", str(SNAP),
                                   "--seo-briefs", str(SEO), "--out", str(out), "--limit", "8"])
            self.assertEqual(code, 0)
            self.assertIn("Nothing was sent or published", stdout)
            self.assertTrue(out.is_file())
            self.assertFalse(dbp.exists())
            self.assertIn("## Next actions (", out.read_text(encoding="utf-8"))

    def test_same_actions_as_suggest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.md"
            code, suggest_json, _ = run(["suggest", "--snapshot", str(SNAP), "--limit", "12", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(run(["weekly-digest", "--snapshot", str(SNAP), "--limit", "12", "--out", str(out)])[0], 0)
            text = out.read_text(encoding="utf-8")
            for action in json.loads(suggest_json)["actions"]:
                self.assertIn(f"signal `{action['signal']}`", text)

    def test_existing_output_is_refused_and_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.md"
            out.write_text("mine", encoding="utf-8")
            code, _, err = run(["weekly-digest", "--snapshot", str(SNAP), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertIn("refuses to overwrite", err)
            self.assertEqual(out.read_text(encoding="utf-8"), "mine")

    def test_no_input_and_missing_out_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "d.md"
            self.assertEqual(run(["weekly-digest", "--out", str(out)])[0], 2)
            self.assertFalse(out.exists())
            with self.assertRaises(SystemExit):
                run(["weekly-digest", "--snapshot", str(SNAP)])

    def test_input_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "snap.json"
            link.symlink_to(SNAP)
            out = Path(tmp) / "d.md"
            code, _, err = run(["weekly-digest", "--snapshot", str(link), "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertIn("weekly-digest input", err)
            self.assertFalse(out.exists())

    def test_include_db_adds_report_and_leaves_the_database_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp, out = Path(tmp) / "local.sqlite", Path(tmp) / "d.md"
            with connect(dbp) as db:
                pid = add_post(db, "owner", "bluesky", "Synthetic digest fixture post")
                transition(db, pid, "approve")
                self.assertTrue(claim_sending(db, pid))
                finish_send(db, pid, "remote-1", None)
                store_metrics(db, pid, "2026-10-12T00:00:00+00:00", "native", views=42)
            before = dbp.read_bytes()
            code, _, _ = run(["--db", str(dbp), "weekly-digest", "--snapshot", str(SNAP),
                              "--include-db", "--out", str(out)])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("### Published-post totals", text)
            self.assertIn("| bluesky | 1 |", text)
            self.assertIn("42", text)
            self.assertNotIn("Synthetic digest fixture post", text)
            self.assertNotIn("Not included. Pass `--include-db`", text)
            self.assertEqual(dbp.read_bytes(), before)

    def test_include_db_with_missing_database_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbp, out = Path(tmp) / "missing.sqlite", Path(tmp) / "d.md"
            code, _, _ = run(["--db", str(dbp), "weekly-digest", "--snapshot", str(SNAP),
                              "--include-db", "--out", str(out)])
            self.assertEqual(code, 2)
            self.assertFalse(dbp.exists())
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
