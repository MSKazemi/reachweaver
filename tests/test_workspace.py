"""Moving a workspace to another computer: the report, the copy, and the path fix (offline).

A workspace is the folder you run ReachWeaver from (``config/`` and ``state/``). These tests copy a real one with
``shutil.copytree`` into another place and run the CLI from there, then check what ``workspace`` reports and that
``media-relink`` repairs recorded attachment paths without changing any approval hash.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mastodon_media import png  # noqa: E402

from reachweaver import attachments, workspace  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.policy import ValidationError  # noqa: E402
from reachweaver.storage import (add_post, approval_binding_problem, approval_payload_sha256, claim_sending,  # noqa: E402
                                 connect, finish_send, get_post, rewrite_attachment_paths, set_post_media, transition)

ROOT = Path(__file__).resolve().parents[1]


class Folder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.old = self.top / "old-computer"
        (self.old / "config").mkdir(parents=True)
        shutil.copy(ROOT / "config/accounts.example.json", self.old / "config/accounts.json")
        self.media_old = self.old / "media"
        self.media_old.mkdir()
        self.image = self.media_old / "chart.png"
        self.image.write_bytes(png())

    def cli(self, cwd, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.chdir(cwd), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(["--plain", *argv])
        return code, out.getvalue(), err.getvalue()

    def seed(self):
        """A workspace with a sent post, an approved post carrying an image, and a draft."""
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            sent = add_post(db, "my-bluesky", "bluesky", "Already sent text")
            transition(db, sent, "approve", approver="tester", approval_channel="cli_flag")
            claim_sending(db, sent)
            finish_send(db, sent, "rid-1", "https://bsky.app/profile/me/post/1")
            pending = add_post(db, "my-mastodon", "mastodon", "Waiting with a chart")
            set_post_media(db, pending, [attachments.build_attachment(self.image, "A described chart")])
            transition(db, pending, "approve", approver="tester", approval_channel="cli_flag")
            draft = add_post(db, "my-bluesky", "bluesky", "Just a draft")
        return sent, pending, draft

    def copy_to_new_computer(self):
        new = self.top / "new-computer"
        shutil.copytree(self.old, new)
        return new


class TheFolderIsPortable(Folder):
    def test_a_copied_workspace_works_from_its_new_place(self):
        sent, pending, draft = self.seed()
        new = self.copy_to_new_computer()
        shutil.rmtree(self.old)  # the old computer is gone
        for argv in (("list",), ("journal", "--all-statuses", "--format", "json")):
            code, out, err = self.cli(new, *argv)
            self.assertEqual(code, 0, err)
            for pid in (sent, pending, draft):
                self.assertIn(pid, out)
        code, out, _ = self.cli(new, "show", sent)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "published")

    def test_no_machine_specific_path_is_stored_for_posts_without_attachments(self):
        sent, _, draft = self.seed()
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            for pid in (sent, draft):
                row = get_post(db, pid)
                self.assertIsNone(row["media_json"])
                self.assertIsNone(row["video_json"])
            cells = " ".join(str(v) for row in db.execute("SELECT * FROM posts WHERE id IN (?,?)", (sent, draft))
                             for v in row if v is not None)
            self.assertNotIn(str(self.top), cells)

    def test_nothing_secret_is_in_the_folder(self):
        self.seed()
        accounts = json.loads((self.old / "config/accounts.json").read_text())
        self.assertFalse(any(re.search(r"token|password|secret", k, re.I) for a in accounts["accounts"] for k in a
                             if k != "env_prefix"))
        self.assertEqual([p.name for p in self.old.rglob(".env*")], [])


class WorkspaceReport(Folder):
    def test_healthy_workspace_is_ready_and_lists_missing_credential_names(self):
        self.seed()
        new = self.copy_to_new_computer()
        # the attachment is recorded by absolute path, so on the new computer that one file is still at the old path
        code, out, _ = self.cli(new, "workspace", "--json")
        report = json.loads(out)
        self.assertEqual(report["database"]["integrity"], "ok")
        self.assertEqual(report["ledger"], "ok")
        self.assertIn("my-mastodon", report["credentials_missing"])
        self.assertTrue(all(re.fullmatch(r"[A-Z0-9_]+", name) for names in report["credentials_missing"].values()
                            for name in names))
        self.assertTrue(any("installed skill" in text for text in report["not_in_the_folder"]))
        self.assertEqual((code, report["ready_to_copy"]), (0, True))  # old path still exists in this test

    def test_a_missing_attachment_of_an_unsent_post_blocks_and_points_at_media_relink(self):
        _, pending, _ = self.seed()
        new = self.copy_to_new_computer()
        shutil.rmtree(self.old)
        code, out, _ = self.cli(new, "workspace")
        self.assertEqual(code, 1)
        self.assertIn("attachment_missing", out)
        self.assertIn(pending, out)
        self.assertIn("media-relink", out)
        self.assertIn("NO, fix the blocking findings first", out)

    def test_a_changed_attachment_is_reported_as_changed(self):
        self.seed()
        self.image.write_bytes(png(note=b"edited"))
        code, out, _ = self.cli(self.old, "workspace", "--json")
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("attachment_changed", [p["code"] for p in report["problems"]])

    def test_a_lost_approval_ledger_blocks(self):
        self.seed()
        (self.old / "state/approval-ledger.jsonl").unlink()
        code, out, _ = self.cli(self.old, "workspace", "--json")
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertTrue(any(p["code"].startswith("approval_ledger_") for p in report["problems"]))

    def test_a_database_with_a_journal_file_is_flagged_as_in_use(self):
        self.seed()
        (self.old / "state/reachweaver.sqlite-journal").write_bytes(b"")
        code, out, _ = self.cli(self.old, "workspace", "--json")
        self.assertEqual(code, 1)
        self.assertIn("database_in_use", [p["code"] for p in json.loads(out)["problems"]])

    def test_no_database_yet_is_information_not_a_failure(self):
        code, out, _ = self.cli(self.old, "workspace")
        self.assertEqual(code, 0)
        self.assertIn("no_database_yet", out)
        self.assertFalse((self.old / "state").exists())

    def test_the_report_lists_the_ideas_inbox_next_to_the_database(self):
        from reachweaver import ideas
        self.seed()
        entry = lambda report: next(p for p in report["paths"] if p["name"] == "ideas inbox")  # noqa: E731
        code, out, err = self.cli(self.old, "workspace", "--json")
        self.assertIn(code, (0, 1), err)
        absent = entry(json.loads(out))
        self.assertEqual((absent["exists"], absent["path"]), (False, "state/ideas.json"))
        ideas.add(self.old / "state/ideas.json", "Write about moving a workspace")
        code, out, err = self.cli(self.old, "workspace", "--json")
        present = entry(json.loads(out))
        self.assertEqual((present["exists"], present["private"]), (True, True if os.name == "posix" else None))
        self.assertGreater(present["bytes"], 0)
        code, text, _ = self.cli(self.old, "workspace")
        self.assertRegex(text, r"found\s+ideas inbox\s+state/ideas\.json")
        self.assertNotIn("Write about moving", out + text)  # names and sizes, never the idea text
        if os.name == "posix":
            os.chmod(self.old / "state/ideas.json", 0o644)
            _, text, _ = self.cli(self.old, "workspace")
            self.assertRegex(text, r"ideas inbox\s+state/ideas\.json\s+\(readable by others")

    def test_no_database_yet_still_lists_an_ideas_inbox(self):
        from reachweaver import ideas
        (self.old / "state").mkdir(mode=0o700)
        ideas.add(self.old / "state/ideas.json", "An idea before any post")
        code, out, _ = self.cli(self.old, "workspace", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        inbox = next(p for p in report["paths"] if p["name"] == "ideas inbox")
        self.assertTrue(inbox["exists"])
        self.assertFalse((self.old / "state/reachweaver.sqlite").exists())

    def test_the_report_is_read_only(self):
        self.seed()
        db = self.old / "state/reachweaver.sqlite"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        self.cli(self.old, "workspace")
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)


class MediaRelink(Folder):
    def moved(self):
        """Seed, move the media folder, return the new media folder."""
        sent, pending, draft = self.seed()
        new_media = self.top / "new-computer-media"
        shutil.move(str(self.media_old), str(new_media))
        return pending, new_media

    def test_dry_run_lists_ok_rows_and_changes_nothing(self):
        pending, new_media = self.moved()
        before = (self.old / "state/reachweaver.sqlite").read_bytes()
        code, out, _ = self.cli(self.old, "media-relink", "--from", str(self.media_old), "--to", str(new_media))
        self.assertEqual(code, 0, out)
        self.assertIn("ok", out)
        self.assertIn("Dry run", out)
        self.assertEqual((self.old / "state/reachweaver.sqlite").read_bytes(), before)

    def test_apply_repairs_the_path_and_keeps_the_approval_valid(self):
        pending, new_media = self.moved()
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            hash_before = approval_payload_sha256(get_post(db, pending))
        code, out, _ = self.cli(self.old, "media-relink", "--from", str(self.media_old), "--to", str(new_media), "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("Relinked 1 post(s)", out)
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            row = get_post(db, pending)
            self.assertEqual(row["status"], "approved")
            self.assertEqual(approval_payload_sha256(row), hash_before)
            self.assertIsNone(approval_binding_problem(row))
            self.assertIn(str(new_media), row["media_json"])
            events = [r["action"] for r in db.execute("SELECT action FROM event_log WHERE post_id=?", (pending,))]
            self.assertIn("attachment_relinked", events)
        code, out, _ = self.cli(self.old, "workspace", "--json")
        self.assertEqual(code, 0, out)

    def test_a_file_that_changed_is_never_relinked(self):
        pending, new_media = self.moved()
        (new_media / "chart.png").write_bytes(png(note=b"not the attached bytes"))
        code, out, _ = self.cli(self.old, "media-relink", "--from", str(self.media_old), "--to", str(new_media), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("changed", out)
        self.assertIn("left 1 untouched", out)
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            self.assertIn(str(self.media_old), get_post(db, pending)["media_json"])

    def test_a_missing_target_is_reported_and_nothing_is_written(self):
        pending, new_media = self.moved()
        (new_media / "chart.png").unlink()
        code, out, _ = self.cli(self.old, "media-relink", "--from", str(self.media_old), "--to", str(new_media), "--apply")
        self.assertEqual(code, 1)
        self.assertIn("missing", out)

    def test_prefix_matching_is_by_folder_not_by_text_and_must_be_absolute(self):
        pending, new_media = self.moved()
        sibling = str(self.media_old) + "-extra"  # shares a text prefix but is another folder
        code, out, _ = self.cli(self.old, "media-relink", "--from", sibling, "--to", str(new_media))
        self.assertEqual(code, 0)
        self.assertIn("No attachment of an unsent post", out)
        self.assertEqual(self.cli(self.old, "media-relink", "--from", "relative/dir", "--to", str(new_media))[0], 2)

    def test_sent_cancelled_and_in_flight_posts_are_never_touched(self):
        sent, pending, draft = self.seed()
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            for status in ("published", "cancelled", "sending"):
                with self.assertRaises(ValidationError):
                    db.execute("UPDATE posts SET status=? WHERE id=?", (status, pending))
                    rewrite_attachment_paths(db, pending, media_json=get_post(db, pending)["media_json"], video_json=None)
                db.execute("UPDATE posts SET status='approved' WHERE id=?", (pending,))

    def test_a_rewrite_that_would_change_the_approval_hash_is_rolled_back(self):
        _, pending, _ = self.seed()
        other = self.top / "other.png"
        other.write_bytes(png(note=b"different bytes"))
        different = attachments.encode_media([attachments.build_attachment(other, "A described chart")])
        with contextlib.chdir(self.old), connect("state/reachweaver.sqlite") as db:
            before = get_post(db, pending)["media_json"]
            with self.assertRaisesRegex(ValidationError, "approval hash"):
                rewrite_attachment_paths(db, pending, media_json=different, video_json=None)
            self.assertEqual(get_post(db, pending)["media_json"], before)


class Boundaries(unittest.TestCase):
    def test_module_has_no_environment_network_or_browser_access(self):
        text = (ROOT / "reachweaver/workspace.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("urllib", "http.client", "socket", "webbrowser", "subprocess", "os.environ", "getenv",
                          "credential("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)

    def test_the_workspace_module_documents_what_is_not_in_the_folder(self):
        self.assertGreaterEqual(len(workspace.NOT_IN_THE_FOLDER), 4)
        self.assertTrue(any("Credentials" in item for item in workspace.NOT_IN_THE_FOLDER))


if __name__ == "__main__":
    unittest.main()
