"""UX round 3 (2026-10-21 strict re-measure of a non-technical creator), terminal side.

1. ``posted``: asks when at a terminal, says "posted today (DATE)" otherwise, and running it again with --at/--text/
   --account CORRECTS a post recorded by hand (``published_manual``) while a post ReachWeaver sent stays as it was.
   The corrected record is still never sendable.
2. ``idea "text"`` is ``idea add "text"``; ``next`` offers a terminal alternative (``share --guide --text``) whose
   caption the guide shows to keep or edit.
3. Bare ``reachweaver`` / ``start`` end with a short "Everyday" footer.
4. The guide's YouTube title prompt, plain media-check lines and plain length counters.
5. Unknown flags without the usage block, ``--version`` without the install kind, an empty ``list`` with a next step.
6. A post without text is named by network and day in report and next.
Synthetic data only; no network.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from gate_support import LIVE_ENV

from reachweaver import __version__, cli_help, posted, results, sendgate, share, share_tailor, start, term, whats_next
from reachweaver.cli import main, make_parser
from reachweaver.cli_parser import idea_shorthand
from reachweaver.policy import ValidationError
from reachweaver.storage import (MANUAL_CORRECTED_EVENT, MANUAL_STATUS, add_post, claim_sending, connect,
                                 correct_manual_publish, pending, shared_entries)

BSKY = {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX3_BSKY", "handle": "owner.bsky.social"}
BSKY2 = {"id": "work-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX3_WORK", "handle": "work.example.org"}
BSKY_URL = "https://bsky.app/profile/owner.bsky.social/post/3kuxthree"
IG_URL = "https://www.instagram.com/p/SyntheticUX3/"


class Tty(io.StringIO):
    def isatty(self):
        return True


class Base(unittest.TestCase):
    accounts: list[dict] | None = [BSKY, BSKY2]

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.db = str(self.root / "state" / "db.sqlite")
        self.config = self.root / "accounts.json"
        if self.accounts is not None:
            self.config.write_text(json.dumps({"accounts": self.accounts}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        sendgate.reset()
        term.set_plain(False)
        self.addCleanup(term.set_plain, False)

    def cli(self, *argv, env=None, stdin: str | None = None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env or {}))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            if stdin is not None:
                stack.enter_context(patch("sys.stdin", Tty(stdin)))
            try:
                code = main(["--db", self.db, "--accounts", str(self.config), *argv])
            except SystemExit as exc:  # argparse errors
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def post(self) -> dict:
        with connect(self.db) as db:
            [row] = [dict(r) for r in db.execute("SELECT * FROM posts")]
        return row

    def events(self, action: str) -> list[dict]:
        with connect(self.db) as db:
            return [dict(r) for r in db.execute("SELECT * FROM event_log WHERE action=? ORDER BY id", (action,))]


def _day(iso: str) -> str:
    return results.local_date(results.to_time(iso))


class PostedWhenTests(Base):
    def test_a_script_gets_today_and_is_told_how_to_correct_it(self):
        code, out, err = self.cli("posted", IG_URL, "--likes", "40")
        self.assertEqual(code, 0, out + err)
        today = results.local_date(datetime.now(timezone.utc))
        self.assertIn(f"posted today ({today}) — if not, run again with --at yesterday", out)

    def test_a_person_at_a_terminal_is_asked_and_enter_means_today(self):
        with patch.object(posted, "_interactive", return_value=True), \
                patch("builtins.input", side_effect=["last week", "yesterday"]) as ask:
            code, out, err = self.cli("posted", IG_URL, "--likes", "40")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(ask.call_args_list[0].args[0], posted.WHEN_PROMPT)
        self.assertIn("Try again (Enter means today)", out)
        yesterday = results.local_date(datetime.now(timezone.utc) - timedelta(days=1))
        self.assertEqual(_day(self.post()["send_started_at"]), yesterday)
        self.assertIn(f"posted {yesterday}", out)
        self.assertNotIn("if not, run again", out)

    def test_enter_at_the_prompt_records_today_without_the_correction_hint(self):
        with patch.object(posted, "_interactive", return_value=True), patch("builtins.input", return_value=""):
            code, out, _ = self.cli("posted", IG_URL)
        self.assertEqual(code, 0)
        self.assertIn("posted today (", out)
        self.assertNotIn("if not, run again", out)

    def test_end_of_input_at_the_prompt_records_nothing(self):
        with patch.object(posted, "_interactive", return_value=True), patch("builtins.input", side_effect=EOFError):
            code, _, err = self.cli("posted", IG_URL)
        self.assertEqual(code, 2)
        self.assertIn("Nothing recorded", err)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

    def test_the_prompt_is_never_shown_with_at_or_when_piped(self):
        with patch("builtins.input", side_effect=AssertionError("asked")):
            self.assertEqual(self.cli("posted", IG_URL, "--at", "yesterday")[0], 0)


class PostedCorrectionTests(Base):
    def setUp(self):
        super().setUp()
        code, out, err = self.cli("posted", BSKY_URL, "--likes", "40")  # recorded "today" by mistake
        self.assertEqual(code, 0, out + err)
        self.pid = self.post()["id"]

    def test_at_again_corrects_the_date_and_the_7_day_reminder(self):
        before = self.post()["send_started_at"]
        code, out, err = self.cli("posted", BSKY_URL, "--at", "yesterday", "--likes", "41")
        self.assertEqual(code, 0, out + err)
        after = self.post()
        yesterday = results.local_date(datetime.now(timezone.utc) - timedelta(days=1))
        self.assertEqual(_day(after["send_started_at"]), yesterday)
        self.assertIn(f"Corrected: posted {yesterday} (was {_day(before)})", out)
        reminder = results.local_date(results.to_time(after["send_started_at"]) + timedelta(hours=168))
        self.assertIn(f"update them around {reminder}", out)
        self.assertNotIn("were not changed", out)
        [corrected] = self.events(MANUAL_CORRECTED_EVENT)
        self.assertEqual(corrected["post_id"], self.pid)
        self.assertIn("fields=send_started_at", corrected["detail"])
        self.assertIn(f"previous_posted_at={before}", corrected["detail"])
        with connect(self.db) as db:
            [entry] = shared_entries(db)
        self.assertEqual(entry["posted_at"], after["send_started_at"])  # the repeat guard's ledger follows

    def test_text_and_account_are_corrected_and_the_event_never_holds_the_text(self):
        code, out, err = self.cli("posted", BSKY_URL, "--text", "Studio tour", "--account", "work-bsky")
        self.assertEqual(code, 0, out + err)
        row = self.post()
        self.assertEqual((row["content"], row["account_id"]), ("Studio tour", "work-bsky"))
        self.assertIn('Corrected the text: "Studio tour"', out)
        self.assertIn("Corrected the account: work-bsky", out)
        [corrected] = self.events(MANUAL_CORRECTED_EVENT)
        self.assertNotIn("Studio tour", corrected["detail"])
        self.assertIn("previous_account=my-bsky account=work-bsky", corrected["detail"])
        with connect(self.db) as db:
            [entry] = shared_entries(db)
        self.assertEqual((entry["content"], entry["account_id"]), ("Studio tour", "work-bsky"))

    def test_the_corrected_record_is_still_never_sendable(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--at", "yesterday", "--text", "Fixed")[0], 0)
        row = self.post()
        self.assertEqual(row["status"], MANUAL_STATUS)
        for column in ("approved_at", "approved_by", "scheduled_at"):
            self.assertIsNone(row[column], column)
        with connect(self.db) as db:
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00"), [])
            self.assertFalse(claim_sending(db, self.pid, "2099-01-01T00:00:00+00:00", max_per_24h=10))
        with patch("reachweaver.publishers.request_get_json") as get, \
                patch("reachweaver.publishers.request_json") as send:
            code, out, err = self.cli("publish", "--live", "--post-id", self.pid,
                                      env={**LIVE_ENV, "REACHWEAVER_UX3_BSKY_APP_PASSWORD": "mock-app-password"})
        get.assert_not_called()
        send.assert_not_called()
        self.assertNotIn(f"PUBLISHED {self.pid}", out + err)
        for argv in (("approve", self.pid), ("schedule", self.pid, "--at", "2030-01-01T00:00:00+00:00")):
            self.assertEqual(self.cli(*argv)[0], 2, argv)
        self.assertEqual(self.post()["status"], MANUAL_STATUS)

    def test_bad_corrections_change_nothing(self):
        before = self.post()
        for argv, words in ((("--at", "2999-01-01 10:00"), "in the future"),
                            (("--account", "nobody"), "not a configured bluesky account"),
                            (("--at", "not a date"), "not understood")):
            code, _, err = self.cli("posted", BSKY_URL, *argv)
            self.assertEqual(code, 2, argv)
            self.assertIn(words, err)
        self.assertEqual(self.post(), before)
        self.assertEqual(self.events(MANUAL_CORRECTED_EVENT), [])

    def test_a_time_after_numbers_already_entered_is_refused(self):
        with connect(self.db) as db:
            db.execute("UPDATE metric_snapshots SET observed_at='2026-01-02T00:00:00+00:00'")
        code, _, err = self.cli("posted", BSKY_URL, "--at", "2026-01-05")
        self.assertEqual(code, 2)
        self.assertIn("before that time", err)

    def test_nothing_new_says_so_and_bare_again_names_the_correction(self):
        code, out, _ = self.cli("posted", BSKY_URL, "--account", "my-bsky")
        self.assertEqual(code, 0)
        self.assertIn("nothing to correct", out)
        self.assertEqual(self.events(MANUAL_CORRECTED_EVENT), [])
        _, out, _ = self.cli("posted", BSKY_URL)
        self.assertIn("--at yesterday", out)


class SentPostsAreNeverCorrectedTests(Base):
    def test_a_post_reachweaver_sent_keeps_time_text_and_account(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bsky", "bluesky", "Sent by the adapter")
            db.execute("UPDATE posts SET status='published', remote_url=?, send_started_at='2026-10-20T09:00:00+00:00' "
                       "WHERE id=?", (BSKY_URL, pid))
        before = self.post()
        code, out, err = self.cli("posted", BSKY_URL, "--at", "2026-10-18", "--text", "Other", "--likes", "3")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ReachWeaver sent this post itself", out)
        after = self.post()
        for column in ("status", "send_started_at", "content", "account_id"):
            self.assertEqual(after[column], before[column], column)
        self.assertEqual(self.events(MANUAL_CORRECTED_EVENT), [])

    def test_storage_refuses_every_status_but_published_manual(self):
        with connect(self.db) as db:
            for status in ("draft", "approved", "scheduled", "sending", "published", "needs_review", "cancelled"):
                pid = add_post(db, "my-bsky", "bluesky", f"A {status} post")
                db.execute("UPDATE posts SET status=? WHERE id=?", (status, pid))
                with self.assertRaises(ValidationError, msg=status):
                    correct_manual_publish(db, pid, content="changed", posted_at="2026-10-01T00:00:00+00:00")
                row = db.execute("SELECT status, content FROM posts WHERE id=?", (pid,)).fetchone()
                self.assertEqual((row["status"], row["content"]), (status, f"A {status} post"))
            with self.assertRaises(ValidationError):
                correct_manual_publish(db, "no-such-post", content="x")


class IdeaShorthandTests(Base):
    accounts = None

    def test_idea_text_is_idea_add(self):
        code, out, err = self.cli("idea", "Behind the scenes of my studio")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Saved. When you're ready: reachweaver next", out)
        _, listed, _ = self.cli("idea", "list", "--json")
        self.assertEqual([i["text"] for i in json.loads(listed)["ideas"]], ["Behind the scenes of my studio"])

    def test_the_rewrite_skips_global_options_and_keeps_actions(self):
        self.assertEqual(idea_shorthand(["--db", "x", "idea", "hello"]), ["--db", "x", "idea", "add", "hello"])
        self.assertEqual(idea_shorthand(["--plain", "idea", "--tag", "t", "hi"]),
                         ["--plain", "idea", "add", "--tag", "t", "hi"])
        for argv in (["idea", "list"], ["idea", "drop", "idea-1a2b3c4d"], ["idea", "--help"], ["idea"],
                     ["share", "idea"], ["--db", "idea", "list"]):
            self.assertEqual(idea_shorthand(argv), argv)
        self.assertEqual(make_parser().parse_args(["idea", "x"]).idea_action, "add")

    def test_help_names_the_short_form(self):
        line = dict((entry[0], entry[1]) for entry in cli_help.EVERYDAY)["idea"]
        self.assertIn('reachweaver idea "text"', line)

    def test_next_offers_the_browser_and_a_terminal_guide_with_the_idea_as_caption(self):
        self.assertEqual(self.cli("idea", "Behind the scenes of my studio")[0], 0)
        view = whats_next.build_next(self.db)
        [idea] = [a for a in view["actions"] if a["kind"] == "idea"]
        self.assertTrue(idea["command"].startswith("reachweaver share --web --open --idea idea-"))
        alt = idea["alternative"]["command"]
        self.assertEqual(alt, "reachweaver share --guide --text 'Behind the scenes of my studio'")
        parsed = make_parser().parse_args(shlex.split(alt)[1:])
        self.assertEqual((parsed.guide, parsed.text), (True, "Behind the scenes of my studio"))
        self.assertIn("Or in the terminal: " + alt, whats_next.render_text(view))

    def test_an_idea_with_a_direction_character_gets_the_plain_guide(self):
        self.assertEqual(whats_next.guide_command("evil‮text"), "reachweaver share --guide")


class GuideTests(Base):
    accounts = None

    def setUp(self):
        super().setUp()
        self.out = self.root / "packages"

    def test_the_guide_shows_a_given_caption_to_keep(self):
        code, out, err = self.cli("share", "--guide", "--text", "Behind the scenes", "--out", str(self.out),
                                  stdin="instagram\n\n\n\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Your caption:\n  Behind the scenes\nKeep this caption?", out)
        self.assertEqual((self.out / "instagram" / "caption.txt").read_text(encoding="utf-8").strip(),
                         "Behind the scenes")

    def test_the_guide_lets_you_write_a_new_caption(self):
        code, out, err = self.cli("share", "--guide", "--text", "Old idea", "--out", str(self.out),
                                  stdin="instagram\ne\nA brand new caption\n\n\n\n")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((self.out / "instagram" / "caption.txt").read_text(encoding="utf-8").strip(),
                         "A brand new caption")

    def test_youtube_title_says_it_is_required_reasks_once_then_skips_in_plain_words(self):
        code, out, err = self.cli("share", "--guide", "--out", str(self.out),
                                  stdin="instagram,youtube\nA caption\n\n\n\n\n\n")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out.count(share.YOUTUBE_TITLE_PROMPT), 2)
        self.assertIn("(required for YouTube; Enter skips YouTube)", out)
        self.assertIn(share.YOUTUBE_GUIDE_SKIP, err)
        self.assertNotIn("--title", err)

    def test_a_title_on_the_second_try_is_used(self):
        code, out, err = self.cli("share", "--guide", "--out", str(self.out),
                                  stdin="youtube\nA caption\n\n\n\nStudio tour\n\n")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Studio tour", (self.out / "youtube" / "title.txt").read_text(encoding="utf-8"))


class PlainWordsTests(unittest.TestCase):
    def rows(self, **extra):
        row = {"path": "/tmp/clip.mp4", "kind": "video", "table": "instagram", "problems": [], "advice": [],
               "not_checked": ["moov_before_mdat", "subtitle_formats"], "notes": [],
               "detected": {"container": "mp4", "width": "unknown", "height": "unknown",
                            "duration_seconds": "unknown"}}
        row.update(extra)
        return [row]

    def test_media_lines_show_problems_and_one_plain_line_never_field_names(self):
        lines = share._media_lines(self.rows())
        text = "\n".join(lines)
        for jargon in ("moov_before_mdat", "subtitle_formats", "unknown", "not checked (", "mp4,"):
            self.assertNotIn(jargon, text)
        self.assertEqual(lines, ["Media clip.mp4: no problem found",
                                 "Media clip.mp4: some details could not be checked from the file header "
                                 "(that's normal)"])
        lines = share._media_lines(self.rows(problems=["duration 200s is over the 90s limit"],
                                             detected={"container": "mp4", "width": 1080, "height": 1920,
                                                       "duration_seconds": 200}))
        self.assertIn("MEDIA PROBLEM: duration 200s is over the 90s limit", lines)
        self.assertNotIn("no problem found", "\n".join(lines))
        self.assertTrue(all("1080x1920, 200s" in line for line in lines if line.startswith("Media clip")))

    def test_lengths_read_as_characters_or_allowed_bytes(self):
        self.assertEqual(share_tailor.measure("instagram", "x" * 56), "56 of 2200 characters allowed")
        self.assertEqual(share_tailor.measure("tiktok", "x" * 56), "56 of 2200 characters allowed")
        self.assertIn("as 2", share_tailor.measure("tiktok", "hi \U0001F600"))
        self.assertEqual(share_tailor.measure("youtube", "x" * 56), "56 of 5000 bytes allowed")
        self.assertEqual(share_tailor.measure("bluesky", "x" * 56), "56 of 300 characters allowed")
        for platform in ("instagram", "tiktok", "youtube", "bluesky", "threads", "mastodon"):
            self.assertNotRegex(share_tailor.measure(platform, "hello"), r"UTF-16|graphemes|/")


class SmallFixesTests(Base):
    accounts = None

    def test_unknown_flag_prints_only_the_error_and_hint(self):
        code, _, err = self.cli("posted", IG_URL, "--platfrom", "instagram")
        self.assertEqual(code, 2)
        self.assertNotIn("usage:", err)
        self.assertIn("Did you mean --platform?", err)

    def test_version_is_the_number_and_verbose_adds_the_install_kind(self):
        _, out, _ = self.cli("--version")
        self.assertEqual(out, f"reachweaver {__version__}\n")
        _, out, _ = self.cli("version")
        self.assertEqual(out, f"reachweaver {__version__}\n")
        _, out, _ = self.cli("version", "--verbose")
        self.assertIn("install kind: ", out)
        _, out, _ = self.cli("version", "--json")
        self.assertIn("install_kind", json.loads(out))

    def test_an_empty_list_says_how_to_write_one(self):
        _, out, err = self.cli("list")
        self.assertEqual(out, "")
        self.assertIn("No posts yet. Write one: reachweaver studio --open (or reachweaver share --guide)", err)


class FooterTests(Base):
    accounts = None

    def test_bare_reachweaver_ends_with_a_short_everyday_footer(self):
        code, out, _ = self.cli()
        self.assertEqual(code, 0)
        tail = out.strip().splitlines()[-6:]
        self.assertTrue(tail[0].startswith("Everyday (more: reachweaver --help"), tail)
        for word in ("reachweaver studio --open", "reachweaver share --guide", "reachweaver posted LINK",
                     "reachweaver report --week", 'reachweaver idea "text"', "reachweaver next"):
            self.assertIn(word, "\n".join(tail))
        self.assertFalse(Path(self.db).exists())

    def test_footer_is_at_most_six_lines_every_command_parses_and_is_in_everyday_help(self):
        lines = start.everyday_footer()
        self.assertLessEqual(len(lines), 6)
        parser = make_parser()
        for _, command, _ in start.EVERYDAY:
            words = shlex.split(command.replace("LINK", "https://bsky.app/profile/a/post/1").replace(" N", " 1"))
            parsed = parser.parse_args(words[1:])
            self.assertIn(parsed.command, cli_help.EVERYDAY_NAMES)
        self.assertIn("Everyday (more:", start.render(start.build_guide(self.db, str(self.config))))


class UntitledLabelTests(Base):
    accounts = None

    def test_a_post_without_text_is_named_by_network_and_day(self):
        self.assertEqual(self.cli("posted", IG_URL, "--likes", "40", "--at", "2026-10-18 10:00")[0], 0)
        _, out, _ = self.cli("report", "--format", "text")
        self.assertIn("Instagram post of 18 Oct", out)
        self.assertNotIn("SyntheticUX3", out)
        self.assertNotIn('"Instagram post', out)  # a label, not quoted words

    def test_label_adds_the_year_when_it_is_not_this_year(self):
        now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
        self.assertEqual(results.untitled_label("instagram", "2025-03-04T12:00:00+00:00", now=now),
                         "Instagram post of 4 Mar 2025")
        self.assertEqual(results.untitled_label("youtube", None), "YouTube post")


if __name__ == "__main__":
    unittest.main()
