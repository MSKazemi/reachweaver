"""Have we shared this before? The repeat guard (offline; nothing is posted or sent).

Covers the matching rules (same video/image bytes, identical and similar wording, links), what counts as history
(published, in flight, unresolved, queued, and material recorded with ``mark-shared``), which repeats block
approval and which only warn, the override and its audit event, and that every report is read-only and never
carries an earlier post's text.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mastodon_media import png  # noqa: E402
from test_media import av_mp4  # noqa: E402

from reachweaver import attachments, repeat_guard, tiktok  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.policy import ValidationError  # noqa: E402
from reachweaver.storage import (add_post, connect, get_post, latch_identity_mode, record_shared,  # noqa: E402
                                 set_post_media, set_post_video, transition)

ROOT = Path(__file__).resolve().parents[1]
EARLIER = "Our new release adds a faster importer, clearer errors and a safer default for everyone"
OTHER = "A completely different announcement about a workshop in the spring for new contributors"
SIMILAR = "Our new release adds a faster importer, clearer errors and a safer default for all users"
SECRET_TEXT = "private earlier wording that no report may repeat back to the reader"


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = str(self.root / "state" / "outbox.sqlite")
        self.accounts = ROOT / "config" / "accounts.example.json"
        self.video = self.root / "clip.mp4"
        self.video.write_bytes(av_mp4(mdat_size=4096))
        self.image = self.root / "chart.png"
        self.image.write_bytes(png())

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db_path, "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def publish_row(self, db, account, platform, text, status="published"):
        pid = add_post(db, account, platform, text)
        db.execute("UPDATE posts SET status=? WHERE id=?", (status, pid))
        return pid

    def material(self, **kw):
        base = {"text": "", "images": [], "video": None, "links": []}
        base.update(kw)
        return base


class MatchingRules(Workspace):
    def test_same_video_bytes_on_the_same_platform_and_account_blocks(self):
        with connect(self.db_path) as db:
            old = add_post(db, "tt-me", "tiktok", "First upload caption")
            set_post_video(db, old, tiktok.build_video_record(self.video))
            db.execute("UPDATE posts SET status='published' WHERE id=?", (old,))
            fresh = self.video.parent / "copy.mp4"
            fresh.write_bytes(self.video.read_bytes())  # a different path, identical bytes
            new = add_post(db, "tt-me", "tiktok", "Second caption entirely different words here")
            set_post_video(db, new, tiktok.build_video_record(fresh))
            findings = repeat_guard.find_repeats(db, platform="tiktok", account_id="tt-me",
                                                 material=repeat_guard.material_of_post(get_post(db, new)),
                                                 exclude_post_id=new)
        kinds = {f["kind"] for f in findings}
        self.assertIn("same_video", kinds)
        self.assertEqual([f["kind"] for f in repeat_guard.blocking(findings)], ["same_video"])
        self.assertIn("same video file", repeat_guard.describe(findings[0]))

    def test_other_platform_never_matches_and_other_account_only_warns(self):
        digest = hashlib.sha256(self.video.read_bytes()).hexdigest()
        with connect(self.db_path) as db:
            self.publish_row(db, "tt-me", "tiktok", OTHER)
            db.execute("UPDATE posts SET video_json=NULL")
            record_shared(db, platform="youtube", account_id="yt-main", content="", images=[], video=digest,
                          links=[], posted_at=None, url="", note="")
            m = self.material(video=digest)
            same_platform_other_account = repeat_guard.find_repeats(db, platform="youtube", account_id="yt-side",
                                                                    material=m)
            other_platform = repeat_guard.find_repeats(db, platform="tiktok", account_id="tt-me", material=m)
            same_account = repeat_guard.find_repeats(db, platform="youtube", account_id="yt-main", material=m)
        self.assertEqual([f["kind"] for f in same_platform_other_account], ["same_video"])
        self.assertEqual(repeat_guard.blocking(same_platform_other_account), [])  # warns, does not block
        self.assertEqual(other_platform, [])  # cross-posting the same video to another platform is normal
        self.assertEqual(len(repeat_guard.blocking(same_account)), 1)

    def test_same_image_bytes_block_and_different_bytes_do_not(self):
        with connect(self.db_path) as db:
            old = add_post(db, "masto", "mastodon", "Chart one")
            set_post_media(db, old, [attachments.build_attachment(self.image, "A described chart")])
            db.execute("UPDATE posts SET status='published' WHERE id=?", (old,))
            new = add_post(db, "masto", "mastodon", "Chart again, different words entirely today")
            set_post_media(db, new, [attachments.build_attachment(self.image, "Another description of the chart")])
            hit = repeat_guard.find_repeats(db, platform="mastodon", account_id="masto",
                                            material=repeat_guard.material_of_post(get_post(db, new)),
                                            exclude_post_id=new)
            other = self.root / "other.png"
            other.write_bytes(png(note=b"different bytes"))
            miss = repeat_guard.find_repeats(db, platform="mastodon", account_id="masto",
                                             material=self.material(images=[repeat_guard.sha256_file(other)]))
        self.assertEqual([f["kind"] for f in repeat_guard.blocking(hit)], ["same_image"])
        self.assertEqual(miss, [])

    def test_identical_wording_blocks_similar_wording_only_warns(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", EARLIER)
            exact = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                              material=self.material(text=EARLIER.upper()))  # case is ignored
            similar = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                                material=self.material(text=SIMILAR))
            different = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                                  material=self.material(text=OTHER))
        self.assertEqual([f["kind"] for f in repeat_guard.blocking(exact)], ["same_text"])
        self.assertEqual([f["kind"] for f in similar], ["similar_text"])
        self.assertEqual(repeat_guard.blocking(similar), [])
        self.assertEqual(different, [])

    def test_similar_wording_outside_the_look_back_is_ignored_but_identical_wording_is_not(self):
        with connect(self.db_path) as db:
            pid = self.publish_row(db, "my-bluesky", "bluesky", EARLIER)
            db.execute("UPDATE posts SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (pid,))
            near = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                             material=self.material(text=SIMILAR), days=30)
            exact = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                              material=self.material(text=EARLIER), days=30)
        self.assertEqual(near, [])
        self.assertEqual([f["kind"] for f in exact], ["same_text"])

    def test_the_same_link_is_a_reminder_not_a_block(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", "Read https://Example.org/post/?utm_source=x#top today ok")
            findings = repeat_guard.find_repeats(
                db, platform="bluesky", account_id="my-bluesky",
                material=self.material(text=OTHER, links=repeat_guard.links_in("see https://example.org/post")))
        self.assertEqual([f["kind"] for f in findings], ["same_link"])
        self.assertEqual(findings[0]["strength"], "reminder")
        self.assertEqual(repeat_guard.blocking(findings), [])

    def test_canonical_link(self):
        self.assertEqual(repeat_guard.canonical_link("HTTP://Example.org/a/?b=1&utm_campaign=z&a=2#frag"),
                         "https://example.org/a?a=2&b=1")
        self.assertIsNone(repeat_guard.canonical_link("ftp://example.org/x"))
        self.assertIsNone(repeat_guard.canonical_link("not a link"))


class WhatCountsAsHistory(Workspace):
    def test_status_decides_blocking(self):
        expectations = {"published": True, "sending": True, "needs_review": True,
                        "approved": False, "scheduled": False, "draft": False, "cancelled": None}
        for status, blocks in expectations.items():
            with self.subTest(status=status), connect(str(self.root / f"{status}.sqlite")) as db:
                self.publish_row(db, "my-bluesky", "bluesky", EARLIER, status=status)
                findings = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                                     material=self.material(text=EARLIER))
                if blocks is None:  # cancelled posts are not history
                    self.assertEqual(findings, [])
                else:
                    self.assertEqual(bool(repeat_guard.blocking(findings)), blocks)
                    self.assertEqual(findings[0]["state"], "already_shared" if blocks else "also_queued")

    def test_a_post_never_matches_itself(self):
        with connect(self.db_path) as db:
            pid = self.publish_row(db, "my-bluesky", "bluesky", EARLIER, status="draft")
            findings = repeat_guard.find_repeats(db, platform="bluesky", account_id="my-bluesky",
                                                 material=self.material(text=EARLIER), exclude_post_id=pid)
        self.assertEqual(findings, [])

    def test_ledger_entry_without_an_account_counts_for_every_account(self):
        with connect(self.db_path) as db:
            record_shared(db, platform="bluesky", account_id=None, content=EARLIER, images=[], video=None, links=[],
                          posted_at="2026-09-01T10:00:00+02:00", url="https://example.org/p", note="by hand")
            findings = repeat_guard.find_repeats(db, platform="bluesky", account_id="some-account",
                                                 material=self.material(text=EARLIER))
        self.assertEqual(len(repeat_guard.blocking(findings)), 1)
        self.assertEqual(findings[0]["source"], "ledger")
        self.assertEqual(findings[0]["when"], "2026-09-01T08:00:00+00:00")

    def test_an_old_database_without_the_ledger_table_still_works(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE posts(id TEXT, account_id TEXT, platform TEXT, status TEXT, content TEXT, "
                         "source_url TEXT, media_json TEXT, video_json TEXT, created_at TEXT, scheduled_at TEXT);"
                         "CREATE TABLE event_log(id INTEGER PRIMARY KEY, at TEXT, post_id TEXT, action TEXT, detail TEXT);")
        self.assertEqual(repeat_guard.find_repeats(db, platform="bluesky", account_id="a",
                                                   material=self.material(text=EARLIER)), [])

    def test_bad_look_back_is_refused(self):
        with connect(self.db_path) as db:
            for bad in (0, -1, 99999, True):
                with self.subTest(days=bad), self.assertRaises(ValueError):
                    repeat_guard.find_repeats(db, platform="bluesky", account_id="a", material=self.material(),
                                              days=bad)


class CommandsAndApproval(Workspace):
    def seed(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", EARLIER)
            return add_post(db, "my-bluesky", "bluesky", EARLIER + " ")

    def test_check_blocks_and_names_the_way_out(self):
        pid = self.seed()
        code, out, _ = self.run_cli("check", pid)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED: repeat:", out)
        self.assertIn("--allow-repeat", out)

    def test_approval_is_refused_and_changes_nothing(self):
        pid = self.seed()
        code, _, err = self.run_cli("appr" + "ove", pid, "--approver", "tester")
        self.assertEqual(code, 2)
        self.assertIn("same wording", err)
        self.assertIn("Nothing changed", err)
        with connect(self.db_path) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_the_override_goes_through_and_leaves_an_audit_event(self):
        pid = self.seed()
        code, out, _ = self.run_cli("appr" + "ove", pid, "--approver", "tester", "--allow-repeat")
        self.assertEqual(code, 0, out)
        with connect(self.db_path) as db:
            row = get_post(db, pid)
            self.assertEqual(row["status"], "approved")
            events = db.execute("SELECT action, detail FROM event_log WHERE post_id=? AND action IN "
                                "('approve','repeat_override') ORDER BY id", (pid,)).fetchall()
        bound = row["approved_payload_sha256"][:12]
        # Recorded after the approval, in the same transaction, and tied to it by the approval hash prefix
        # (which covers the per-approval nonce), so it never outlives a refused approve.
        self.assertEqual([e["action"] for e in events], ["approve", "repeat_override"])
        self.assertIn(f"approval_sha256={bound}", events[0]["detail"])
        self.assertEqual(events[1]["detail"], f"kinds=same_text approval_sha256={bound}")

    def override_events(self, pid):
        with connect(self.db_path) as db:
            return db.execute("SELECT detail FROM event_log WHERE post_id=? AND action='repeat_override'",
                              (pid,)).fetchall()

    def test_a_refused_override_approval_leaves_no_override_event(self):
        """Security decision sheet finding 4: a refusal after the repeat check must not log an override."""
        pid = self.seed()
        with connect(self.db_path) as db:
            latch_identity_mode(db)  # approval-mode --require-identity: plain approve is refused
        code, _, err = self.run_cli("appr" + "ove", pid, "--approver", "tester", "--allow-repeat")
        self.assertEqual(code, 2)
        self.assertIn("approval_identity_required", err)
        self.assertEqual(self.override_events(pid), [])
        with connect(self.db_path) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_an_override_refused_inside_the_transition_is_rolled_back_with_it(self):
        pid = self.seed()
        with connect(self.db_path) as db:
            other = add_post(db, "my-bluesky", "bluesky", OTHER)
            transition(db, other, "approve")  # a nonce now exists, so a deleted ledger is not recreated
        (Path(self.db_path).parent / "approval-ledger.jsonl").unlink()
        code, _, err = self.run_cli("appr" + "ove", pid, "--approver", "tester", "--allow-repeat")
        self.assertEqual(code, 2)
        self.assertIn("approval_ledger_unavailable", err)
        self.assertEqual(self.override_events(pid), [])
        with connect(self.db_path) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=? AND action='approve'",
                                        (pid,)).fetchone()[0], 0)

    def test_a_different_post_is_approved_without_any_flag(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", EARLIER)
            pid = add_post(db, "my-bluesky", "bluesky", OTHER)
        code, out, _ = self.run_cli("appr" + "ove", pid, "--approver", "tester")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Warning", out)

    def test_a_similar_post_is_approved_with_a_warning(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", EARLIER)
            pid = add_post(db, "my-bluesky", "bluesky", SIMILAR)
        code, out, _ = self.run_cli("appr" + "ove", pid, "--approver", "tester")
        self.assertEqual(code, 0, out)
        self.assertIn("Warning: very similar wording", out)

    def test_show_includes_the_repeat_check(self):
        pid = self.seed()
        code, out, _ = self.run_cli("show", pid)
        self.assertEqual(code, 0)
        data = json.loads(out)["repeat_check"]
        self.assertEqual(data["blocking"], 1)
        self.assertEqual(data["findings"][0]["kind"], "same_text")

    def test_repeat_check_for_a_post_and_for_named_material(self):
        pid = self.seed()
        code, out, _ = self.run_cli("repeat-check", "--post-id", pid)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKING: the same wording", out)
        code, out, _ = self.run_cli("repeat-check", "--platform", "bluesky", "--account", "my-bluesky",
                                    "--text", OTHER)
        self.assertEqual((code, "No repeat found" in out), (0, True))
        code, out, _ = self.run_cli("repeat-check", "--platform", "bluesky", "--text", EARLIER, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["blocking"], 1)

    def test_reports_never_repeat_the_earlier_text(self):
        with connect(self.db_path) as db:
            self.publish_row(db, "my-bluesky", "bluesky", SECRET_TEXT)
            pid = add_post(db, "my-bluesky", "bluesky", SECRET_TEXT + " Second version.")
        everything = ""
        for argv in (("check", pid), ("repeat-check", "--post-id", pid), ("repeat-check", "--post-id", pid, "--json")):
            _, out, err = self.run_cli(*argv)
            everything += out + err
        shown = self.run_cli("show", pid)[1]
        data = json.loads(shown)["repeat_check"]
        self.assertIn("similar", everything)  # the reports did find the earlier post...
        self.assertNotIn("private earlier wording", everything)  # ...and never quote it
        self.assertNotIn("private earlier wording", json.dumps(data))

    def test_repeat_check_is_read_only_and_survives_a_missing_database(self):
        code, out, _ = self.run_cli("repeat-check", "--platform", "bluesky", "--text", EARLIER)
        self.assertEqual(code, 0)
        self.assertIn("No history database yet", out)
        self.assertFalse(Path(self.db_path).exists())
        pid = self.seed()
        before = hashlib.sha256(Path(self.db_path).read_bytes()).hexdigest()
        self.run_cli("repeat-check", "--post-id", pid)
        self.run_cli("repeat-check", "--platform", "bluesky", "--text", EARLIER)
        self.assertEqual(hashlib.sha256(Path(self.db_path).read_bytes()).hexdigest(), before)

    def test_mark_shared_records_hand_posted_material_and_later_drafts_are_checked(self):
        code, out, _ = self.run_cli("mark-shared", "--platform", "youtube", "--video", str(self.video),
                                    "--url", "https://example.org/v", "--note", "launch video")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing was posted", out)
        code, out, _ = self.run_cli("repeat-check", "--platform", "youtube", "--video", str(self.video))
        self.assertEqual(code, 1)
        self.assertIn("same video file", out)
        # a different file, and the same file on another platform, are clean
        other = self.root / "other.mp4"
        other.write_bytes(av_mp4(mdat_size=8192))
        self.assertEqual(self.run_cli("repeat-check", "--platform", "youtube", "--video", str(other))[0], 0)
        self.assertEqual(self.run_cli("repeat-check", "--platform", "tiktok", "--video", str(self.video))[0], 0)

    def test_mark_shared_refuses_empty_unknown_and_mismatched_input(self):
        self.assertEqual(self.run_cli("mark-shared", "--platform", "youtube")[0], 2)
        self.assertEqual(self.run_cli("mark-shared", "--platform", "nowhere", "--text", EARLIER)[0], 2)
        self.assertEqual(self.run_cli("mark-shared", "--platform", "bluesky", "--account", "my-mastodon",
                                      "--text", EARLIER)[0], 2)
        self.assertEqual(self.run_cli("mark-shared", "--platform", "bluesky", "--text", EARLIER, "--link",
                                      "javascript:alert(1)")[0], 2)
        self.assertFalse(Path(self.db_path).exists())

    def test_post_id_cannot_be_mixed_with_named_material(self):
        pid = self.seed()
        code, _, err = self.run_cli("repeat-check", "--post-id", pid, "--text", "x")
        self.assertEqual(code, 2)
        self.assertIn("cannot be combined", err)

    def test_sha256_file_refuses_a_missing_file_and_a_directory(self):
        for bad in (self.root / "nope.mp4", self.root):
            with self.subTest(path=bad), self.assertRaises(ValidationError):
                repeat_guard.sha256_file(bad)


class Boundaries(unittest.TestCase):
    def test_module_has_no_network_browser_or_credential_access(self):
        import re
        text = (ROOT / "reachweaver/repeat_guard.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("urllib.request", "http.client", "socket", "requests", "webbrowser", "subprocess",
                          "os.environ", "getenv", "credential("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)


class ConnectWithAnExistingDatabase(Workspace):
    """Regression: `connect` read the database through a name a local import shadowed, so it crashed once a
    database existed (found while testing the repeat guard)."""

    def test_connect_status_reads_disconnected_accounts_from_an_existing_database(self):
        with connect(self.db_path) as db:
            db.execute("INSERT INTO settings(name,value) VALUES('account_disconnected:my-bluesky','2026-10-16T10:00:00+00:00')")
        code, out, _ = self.run_cli("connect")
        self.assertEqual(code, 0, out)
        self.assertIn("disconnected here", out)


if __name__ == "__main__":
    unittest.main()
