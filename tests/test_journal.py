"""The posting journal and archive (offline; nothing is sent, and the database is only read).

Covers what ``journal`` shows (text, times, account, approver, remote link, fixed-code timeline, file paths and
hashes), the filters and formats, hand-posted material, the safety properties (read-only, no provider text, spreadsheet
formulas neutralised, private files) and the archive folder (exact text, metadata, hash-verified copies, additive only).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mastodon_media import png  # noqa: E402
from test_media import av_mp4  # noqa: E402

from reachweaver import attachments, journal, tiktok  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.storage import (add_post, claim_sending, connect, event, finish_send, record_shared,  # noqa: E402
                                 set_post_media, set_post_video, transition)

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_TEXT = "RAW-PROVIDER-ERROR-TEXT-must-never-appear"


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = str(self.root / "state" / "outbox.sqlite")
        self.accounts = ROOT / "config" / "accounts.example.json"
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(av_mp4(mdat_size=4096))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--plain", "--db", self.db_path, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def published(self, db, account, platform, text, url, *, approver="tester", media=None, video=None):
        pid = add_post(db, account, platform, text)
        if media:
            set_post_media(db, pid, [attachments.build_attachment(media, "A described chart")])
        if video:
            set_post_video(db, pid, tiktok.build_video_record(video))
        transition(db, pid, "approve", approver=approver, approval_channel="cli_flag",
                   ai_label="not_needed" if media or video else None)  # AI-label precondition: media needs an answer
        claim_sending(db, pid)
        finish_send(db, pid, "rid-" + pid[:6], url)
        return pid

    def seed(self):
        with connect(self.db_path) as db:
            bsky = self.published(db, "my-bluesky", "bluesky", "We shipped version three today",
                                  "https://bsky.app/profile/me/post/1")
            masto = self.published(db, "my-mastodon", "mastodon", "A chart of the results", "https://m.example/@me/2",
                                   media=self.image)
            tt = self.published(db, "my-tiktok", "tiktok", "Demo clip caption", "https://tiktok.example/v/3",
                                video=self.video) if False else None
            draft = add_post(db, "my-bluesky", "bluesky", "Still a draft, not posted")
        return bsky, masto, tt, draft


class JournalContent(Workspace):
    def test_a_published_post_shows_text_time_account_approver_link_and_timeline(self):
        bsky, *_ = self.seed()
        code, out, _ = self.run_cli("journal", "--format", "json", "--post-id", bsky)
        self.assertEqual(code, 0, out)
        entry = json.loads(out)["entries"][0]
        self.assertEqual((entry["id"], entry["source"], entry["platform"], entry["account"], entry["status"]),
                         (bsky, "reachweaver", "bluesky", "my-bluesky", "published"))
        self.assertEqual(entry["text"], "We shipped version three today")
        self.assertEqual(entry["remote_url"], "https://bsky.app/profile/me/post/1")
        self.assertEqual(entry["approved_by"], "tester")
        self.assertRegex(entry["when"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(len(entry["approval_sha256"]), 12)
        actions = [item["action"] for item in entry["timeline"]]
        self.assertIn("published", actions)
        self.assertEqual(actions, sorted(actions, key=actions.index))  # in order of occurrence
        self.assertTrue(all(re.fullmatch(r"[a-z_]{1,40}", a) for a in actions))

    def test_default_lists_what_went_out_and_all_statuses_adds_drafts(self):
        bsky, masto, _, draft = self.seed()
        ids = {e["id"] for e in json.loads(self.run_cli("journal", "--format", "json")[1])["entries"]}
        self.assertEqual(ids, {bsky, masto})
        ids = {e["id"] for e in json.loads(self.run_cli("journal", "--all-statuses", "--format", "json")[1])["entries"]}
        self.assertEqual(ids, {bsky, masto, draft})

    def test_filters_limit_and_unknown_post(self):
        bsky, masto, _, _ = self.seed()
        only = json.loads(self.run_cli("journal", "--platform", "mastodon", "--format", "json")[1])["entries"]
        self.assertEqual([e["id"] for e in only], [masto])
        only = json.loads(self.run_cli("journal", "--account", "my-bluesky", "--format", "json")[1])["entries"]
        self.assertEqual([e["id"] for e in only], [bsky])
        self.assertEqual(len(json.loads(self.run_cli("journal", "--limit", "1", "--format", "json")[1])["entries"]), 1)
        future = json.loads(self.run_cli("journal", "--since", "2999-01-01", "--format", "json")[1])["entries"]
        self.assertEqual(future, [])
        past = json.loads(self.run_cli("journal", "--until", "2000-01-01", "--format", "json")[1])["entries"]
        self.assertEqual(past, [])
        self.assertEqual(self.run_cli("journal", "--post-id", "nope")[0], 2)
        self.assertEqual(self.run_cli("journal", "--limit", "0")[0], 2)
        self.assertEqual(self.run_cli("journal", "--since", "yesterday")[0], 2)

    def test_files_are_listed_with_path_hash_size_and_whether_they_are_still_there(self):
        _, masto, *_ = self.seed()
        entry = json.loads(self.run_cli("journal", "--format", "json", "--post-id", masto)[1])["entries"][0]
        (image,) = entry["files"]
        self.assertEqual((image["kind"], image["source"]), ("image", "local"))
        self.assertEqual(image["sha256"], hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(Path(image["path"]).resolve(), self.image.resolve())
        self.assertTrue(image["available"])
        self.image.unlink()
        entry = json.loads(self.run_cli("journal", "--format", "json", "--post-id", masto)[1])["entries"][0]
        self.assertFalse(entry["files"][0]["available"])

    def test_verify_files_notices_a_changed_file(self):
        _, masto, *_ = self.seed()
        verified = json.loads(self.run_cli("journal", "--verify-files", "--format", "json", "--post-id", masto)[1])
        self.assertTrue(verified["entries"][0]["files"][0]["matches_record"])
        self.image.write_bytes(png(note=b"edited after posting"))
        changed = json.loads(self.run_cli("journal", "--verify-files", "--format", "json", "--post-id", masto)[1])
        self.assertFalse(changed["entries"][0]["files"][0]["matches_record"])

    def test_hand_posted_material_is_included_and_can_be_left_out(self):
        self.seed()
        with connect(self.db_path) as db:
            shared = record_shared(db, platform="youtube", account_id=None, content="Launch video text", images=[],
                                   video=hashlib.sha256(b"v").hexdigest(), links=[], posted_at="2026-10-01T10:00:00+02:00",
                                   url="https://youtu.be/EXAMPLE", note="launch")
        entries = json.loads(self.run_cli("journal", "--format", "json")[1])["entries"]
        by_hand = next(e for e in entries if e["id"] == shared)
        self.assertEqual((by_hand["source"], by_hand["status"], by_hand["when"], by_hand["note"]),
                         ("by_hand", "shared_by_hand", "2026-10-01T08:00:00+00:00", "launch"))
        self.assertEqual(by_hand["files"][0]["kind"], "video")
        self.assertIn("file was not kept", by_hand["files"][0]["note"])
        left_out = json.loads(self.run_cli("journal", "--no-by-hand", "--format", "json")[1])["entries"]
        self.assertNotIn(shared, [e["id"] for e in left_out])

    def test_provider_text_in_the_audit_log_is_never_shown(self):
        bsky, *_ = self.seed()
        with connect(self.db_path) as db:
            event(db, "needs_review", bsky, PROVIDER_TEXT)
        for fmt in ("json", "csv", "markdown", "table"):
            self.assertNotIn(PROVIDER_TEXT, self.run_cli("journal", "--format", fmt)[1], fmt)


class JournalFormatsAndSafety(Workspace):
    def test_table_markdown_csv_and_json_agree_on_the_entries(self):
        bsky, masto, *_ = self.seed()
        for fmt in ("table", "markdown", "csv", "json"):
            out = self.run_cli("journal", "--format", fmt)[1]
            self.assertIn(bsky, out, fmt)
            self.assertIn(masto, out, fmt)
        rows = self.run_cli("journal", "--format", "csv")[1].splitlines()
        self.assertEqual(rows[0].split(",")[:3], ["when", "source", "platform"])
        self.assertEqual(len(rows), 3)

    def test_csv_cells_that_look_like_formulas_are_neutralised_and_markdown_pipes_escaped(self):
        with connect(self.db_path) as db:
            self.published(db, "my-bluesky", "bluesky", "=HYPERLINK(\"http://evil\",\"x\") | pipe", "https://x.example/1")
        csv_out = self.run_cli("journal", "--format", "csv")[1]
        self.assertIn("'=HYPERLINK", csv_out)
        self.assertNotIn(",=HYPERLINK", csv_out)
        md = self.run_cli("journal", "--format", "markdown")[1]
        self.assertIn("\\| pipe", md)  # the pipe in the text is escaped, so it cannot split the table row

    def test_out_file_is_new_and_owner_only(self):
        self.seed()
        target = self.root / "journal.md"
        code, out, _ = self.run_cli("journal", "--format", "markdown", "--out", str(target))
        self.assertEqual(code, 0, out)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertIn("Posting journal", target.read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("journal", "--format", "markdown", "--out", str(target))[0], 2)  # never overwrite

    def test_it_is_read_only_and_a_missing_database_creates_nothing(self):
        code, out, _ = self.run_cli("journal")
        self.assertEqual(code, 0)
        self.assertIn("No posts recorded yet", out)
        self.assertFalse(Path(self.db_path).exists())
        self.seed()
        before = hashlib.sha256(Path(self.db_path).read_bytes()).hexdigest()
        for argv in (("journal",), ("journal", "--verify-files", "--format", "json")):
            self.run_cli(*argv)
        self.assertEqual(hashlib.sha256(Path(self.db_path).read_bytes()).hexdigest(), before)

    def test_module_has_no_network_browser_or_environment_access(self):
        text = (ROOT / "reachweaver/journal.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("urllib", "http.client", "socket", "requests", "webbrowser", "subprocess", "os.environ",
                          "getenv", "credential("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)


class Archive(Workspace):
    def archive(self, *extra, out=None):
        target = out or self.root / "archive"
        code, text, err = self.run_cli("archive", "--out", str(target), "--json", *extra)
        return code, (json.loads(text) if text.strip().startswith("{") else text), err, target

    def test_creates_a_private_folder_with_text_metadata_and_an_index(self):
        bsky, masto, *_ = self.seed()
        code, result, _, target = self.archive()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["created"])
        self.assertEqual(sorted(result["archived"]), sorted([bsky, masto]))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertTrue((target / journal.MARKER).is_file())
        folder = next(p for p in target.iterdir() if p.is_dir() and bsky[:24] in p.name)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual((folder / "post.txt").read_text(encoding="utf-8"), "We shipped version three today")
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertNotIn("text", meta)
        self.assertEqual(meta["text_sha256"], hashlib.sha256(b"We shipped version three today").hexdigest())
        self.assertEqual(meta["remote_url"], "https://bsky.app/profile/me/post/1")
        for name in ("post.txt", "meta.json"):
            self.assertEqual(stat.S_IMODE((folder / name).stat().st_mode), 0o600)
        index = (target / "index.md").read_text(encoding="utf-8")
        self.assertIn(bsky, index)
        self.assertIn(masto, index)
        self.assertEqual(stat.S_IMODE((target / "index.csv").stat().st_mode), 0o600)

    def test_files_are_copied_only_on_request_and_only_if_they_still_match(self):
        _, masto, *_ = self.seed()
        code, result, _, target = self.archive()
        folder = next(p for p in target.iterdir() if p.is_dir() and masto[:24] in p.name)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["files"][0]["archive"], "not_requested")
        self.assertFalse((folder / "files").exists())

        code, result, _, target2 = self.archive("--copy-files", out=self.root / "archive2")
        self.assertEqual((code, result["files_copied"], result["files_unavailable"]), (0, 1, 0))
        folder = next(p for p in target2.iterdir() if p.is_dir() and masto[:24] in p.name)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        copied = folder / meta["files"][0]["archived_as"]
        self.assertEqual(copied.read_bytes(), self.image.read_bytes())
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)

        self.image.write_bytes(png(note=b"changed after posting"))  # no longer what was posted
        code, result, _, target3 = self.archive("--copy-files", out=self.root / "archive3")
        self.assertEqual((result["files_copied"], result["files_unavailable"]), (0, 1))
        folder = next(p for p in target3.iterdir() if p.is_dir() and masto[:24] in p.name)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["files"][0]["archive"], "changed")
        self.assertEqual(list((folder / "files").iterdir()), [])  # a changed file is never presented as the original

        self.image.unlink()
        _, result, _, target4 = self.archive("--copy-files", out=self.root / "archive4")
        folder = next(p for p in target4.iterdir() if p.is_dir() and masto[:24] in p.name)
        self.assertEqual(json.loads((folder / "meta.json").read_text(encoding="utf-8"))["files"][0]["archive"], "missing")

    def test_rerunning_adds_only_new_posts_and_never_overwrites(self):
        bsky, masto, *_ = self.seed()
        _, first, _, target = self.archive()
        folder = next(p for p in target.iterdir() if p.is_dir() and bsky[:24] in p.name)
        (folder / "post.txt").write_text("edited by hand", encoding="utf-8")
        with connect(self.db_path) as db:
            new = self.published(db, "my-bluesky", "bluesky", "A third post about something else entirely",
                                 "https://bsky.app/profile/me/post/9")
        _, second, _, _ = self.archive()
        self.assertFalse(second["created"])
        self.assertEqual(second["archived"], [new])
        self.assertEqual(sorted(second["skipped_existing"]), sorted([bsky, masto]))
        self.assertEqual((folder / "post.txt").read_text(encoding="utf-8"), "edited by hand")
        self.assertIn(new, (target / "index.md").read_text(encoding="utf-8"))

    def test_hand_posted_material_is_archived_with_hash_only_files(self):
        with connect(self.db_path) as db:
            shared = record_shared(db, platform="youtube", account_id="yt", content="Launch video", images=[],
                                   video=hashlib.sha256(b"v").hexdigest(), links=[], posted_at=None, url="", note="")
        _, result, _, target = self.archive("--copy-files")
        self.assertEqual(result["archived"], [shared])
        folder = next(p for p in target.iterdir() if p.is_dir())
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["files"][0]["archive"], "hash_only")
        self.assertEqual((folder / "post.txt").read_text(encoding="utf-8"), "Launch video")

    def test_default_folder_is_next_to_the_database_and_odd_targets_are_refused(self):
        self.seed()
        code, out, _ = self.run_cli("archive")
        self.assertEqual(code, 0, out)
        self.assertTrue((Path(self.db_path).parent / "archive" / journal.MARKER).is_file())
        plain = self.root / "plain-dir"
        plain.mkdir()
        self.assertEqual(self.run_cli("archive", "--out", str(plain))[0], 2)  # exists, not an archive
        link = self.root / "link"
        link.symlink_to(self.root / "archive-real")
        self.assertEqual(self.run_cli("archive", "--out", str(link))[0], 2)

    def test_no_database_means_no_archive_is_created(self):
        code, out, _ = self.run_cli("archive", "--out", str(self.root / "archive"))
        self.assertEqual(code, 0)
        self.assertIn("No posts recorded yet", out)
        self.assertFalse((self.root / "archive").exists())


if __name__ == "__main__":
    unittest.main()
