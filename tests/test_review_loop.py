"""#11 guided review loop (`reachweaver review`), owner-approved 2026-10-21 with D1-D5 as recommended. Offline.

Every terminal is a fake: ``/dev/tty`` is replaced by a scripted ``FakeTty`` and stdin by a terminal that
fails the test if it is ever read. Nothing here can publish: there is no live flag, no credential and no
network; approval only changes the local test database.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import agent_guard, catalog, confirmation, mcp_server, review_loop
from reachweaver.cli import main, make_parser
from reachweaver.storage import add_post, approval_payload_sha256, connect, get_post, record_shared

TEXT = ("A useful verified research note about the importer", "Second note: clearer errors for new users",
        "Third note: a safer default for everyone")
ENV = {"USER": "owner-at-terminal"}


class FakeTty:
    """Stands in for /dev/tty: scripted answers, recorded prompts, counts opens."""

    def __init__(self, answers, on_read=None):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.opened = 0
        self.on_read = on_read

    def __call__(self):
        self.opened += 1
        return self

    def write(self, text):
        self.prompts.append(text)

    def readline(self):
        if self.on_read:
            self.on_read()
        return self.answers.pop(0) if self.answers else None

    def close(self):
        pass


class Terminal(io.StringIO):
    def isatty(self):
        return True


class NoReadStdin:
    """A terminal stdin that must never be read: every answer comes from /dev/tty only."""

    def isatty(self):
        return True

    def read(self, *a):
        raise AssertionError("stdin was read")

    readline = read


class NoTtyAllowed:
    def __call__(self):
        raise AssertionError("/dev/tty was opened")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_REVIEW",
             "handle": "example.bsky.social"},
            {"id": "other-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_REVIEW_OTHER",
             "handle": "other.bsky.social"}]}), encoding="utf-8")
        with connect(self.db) as db:
            self.ids = [add_post(db, "my-bluesky", "bluesky", text, "launch") for text in TEXT]

    def cli(self, *argv, tty=None, terminal=True, stdin=None):
        out = Terminal() if terminal else io.StringIO()
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, ENV, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(patch("sys.stdin", stdin if stdin is not None else NoReadStdin()))
            stack.enter_context(patch("reachweaver.confirmation._DevTty", tty if tty is not None else NoTtyAllowed()))
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def status(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)["status"]

    def statuses(self):
        return [self.status(pid) for pid in self.ids]

    def events(self, action):
        with connect(self.db) as db:
            return [dict(r) for r in db.execute("SELECT post_id, detail FROM event_log WHERE action=? ORDER BY id",
                                                (action,))]


class RefusalTests(Base):
    def test_piped_stdout_is_refused_before_anything_is_shown_or_read(self):
        code, out, err = self.cli("review", terminal=False)
        self.assertEqual(code, 2)
        self.assertIn(review_loop.REQUIRES_TTY, err)
        self.assertEqual(out, "")
        self.assertEqual(self.statuses(), ["draft"] * 3)

    def test_piped_stdin_is_refused_too(self):
        code, out, err = self.cli("review", stdin=io.StringIO("a\na\na\n"))
        self.assertEqual(code, 2)
        self.assertIn(review_loop.REQUIRES_TTY, err)
        self.assertNotIn(TEXT[0], out)
        self.assertEqual(self.statuses(), ["draft"] * 3)

    def test_no_controlling_terminal_is_refused_before_anything_is_shown(self):
        def no_tty():
            raise OSError("no tty")
        code, out, err = self.cli("review", tty=no_tty)
        self.assertEqual(code, 2)
        self.assertIn(review_loop.TTY_UNAVAILABLE, err)
        self.assertNotIn(TEXT[0], out)

    def test_require_terminal_helper(self):
        with self.assertRaises(review_loop.ValidationError):
            review_loop.require_terminal(io.StringIO(), Terminal())
        review_loop.require_terminal(NoReadStdin(), Terminal())  # no exception

    def test_verify_identity_needs_online(self):
        code, out, err = self.cli("review", "--verify-identity", tty=FakeTty([]))
        self.assertEqual(code, 2)
        self.assertIn("--verify-identity requires explicit --online", err)
        self.assertNotIn(TEXT[0], out)


class FlowTests(Base):
    def test_approve_skip_cancel_one_post_per_answer(self):
        tty = FakeTty(["a\n", "s\n", "c\n", "CANCEL\n"])
        code, out, err = self.cli("review", "--approver", "mohsen", tty=tty)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.statuses(), ["approved", "draft", "cancelled"])
        for text in TEXT:  # the real show sheet for every post
            self.assertIn(text, out)
        self.assertIn("approved: " + self.ids[0], out)
        self.assertIn("skipped: " + self.ids[1], out)
        self.assertIn("cancelled: " + self.ids[2], out)
        self.assertIn(f"reachweaver publish --post-id {self.ids[0]}", out)
        approvals = self.events("approve")
        self.assertEqual([e["post_id"] for e in approvals], [self.ids[0]])
        self.assertIn("approver=mohsen", approvals[0]["detail"])
        self.assertTrue(all(p.startswith(f"Post {self.ids[0]}: [a]pprove") for p in tty.prompts[:1]))
        self.assertIn(f"Type CANCEL to cancel post {self.ids[2]}", "".join(tty.prompts))

    def test_quit_changes_nothing_and_lists_the_rest(self):
        code, out, _ = self.cli("review", tty=FakeTty(["a\n", "q\n"]))
        self.assertEqual(code, 0)
        self.assertEqual(self.statuses(), ["approved", "draft", "draft"])
        self.assertIn("not reached: " + " ".join(self.ids[1:]), out)

    def test_end_of_input_quits_without_approving(self):
        code, out, _ = self.cli("review", tty=FakeTty([]))
        self.assertEqual(code, 0)
        self.assertEqual(self.statuses(), ["draft"] * 3)
        self.assertIn("0 approved", out)

    def test_no_approval_without_an_explicit_answer(self):
        # Empty lines and unknown words never count as approval; three of them skip the post.
        tty = FakeTty(["\n", "yes\n", "approve all\n", "\n", "\n", "\n", "q\n"])
        code, out, _ = self.cli("review", tty=tty)
        self.assertEqual(code, 0)
        self.assertEqual(self.statuses(), ["draft"] * 3)
        self.assertEqual(self.events("approve"), [])
        self.assertIn(f"No valid answer for {self.ids[0]}", out)

    def test_cancel_needs_the_exact_word(self):
        code, out, _ = self.cli("review", tty=FakeTty(["c\n", "cancel\n", "q\n"]))
        self.assertEqual(code, 0)
        self.assertEqual(self.statuses(), ["draft"] * 3)
        self.assertIn("Not cancelled", out)

    def test_campaign_and_account_filters(self):
        with connect(self.db) as db:
            other = add_post(db, "other-bluesky", "bluesky", "A note for the other account", "spring")
        code, out, _ = self.cli("review", "--account", "other-bluesky", tty=FakeTty(["a\n"]))
        self.assertEqual(code, 0)
        self.assertEqual(self.status(other), "approved")
        self.assertEqual(self.statuses(), ["draft"] * 3)
        code, out, _ = self.cli("review", "--campaign", "launch", tty=FakeTty(["q\n"]))
        self.assertIn("3 draft(s) in campaign launch", out)

    def test_reply_drafts_are_left_out(self):
        with connect(self.db) as db:
            db.execute("UPDATE posts SET reply_target=? WHERE id=?", ('{"platform":"bluesky"}', self.ids[1]))
        code, out, _ = self.cli("review", tty=FakeTty(["s\n", "s\n"]))
        self.assertEqual(code, 0)
        self.assertNotIn(TEXT[1], out)
        self.assertIn("reply drafts not in this loop: " + self.ids[1], out)

    def test_post_changed_while_reviewing_is_not_approved(self):
        def tamper():
            with connect(self.db) as db:
                db.execute("UPDATE posts SET content='Swapped while the owner read it' WHERE id=?", (self.ids[0],))
        tty = FakeTty(["a\n", "q\n"], on_read=tamper)
        code, out, _ = self.cli("review", tty=tty)
        self.assertEqual(self.status(self.ids[0]), "draft")
        self.assertIn("changed while you were reviewing", out)

    def test_repeat_guard_blocks_as_in_approve(self):
        with connect(self.db) as db:
            record_shared(db, platform="bluesky", account_id=None, content=TEXT[0], images=[], video=None, links=[],
                          posted_at="2026-10-01T10:00:00+02:00", url="https://example.org/p", note="by hand")
        code, out, _ = self.cli("review", tty=FakeTty(["a\n", "q\n"]))
        self.assertEqual(code, 1)
        self.assertEqual(self.status(self.ids[0]), "draft")
        self.assertIn("NOT approved", out)
        self.assertIn("refused: " + self.ids[0], out)
        self.assertEqual(self.events("repeat_override"), [])

    def test_each_approval_calls_transition_like_approve(self):
        import reachweaver.cli as cli_module
        calls = []
        real = cli_module.transition

        def spy(con, post_id, command, *args, **kwargs):
            calls.append((post_id, command, args, kwargs))
            return real(con, post_id, command, *args, **kwargs)
        with patch.object(cli_module, "transition", spy):
            code, out, err = self.cli("approve", self.ids[0], "--approver", "mohsen", terminal=False)
            self.assertEqual(code, 0, out + err)
            code, out, err = self.cli("review", "--approver", "mohsen", tty=FakeTty(["a\n", "q\n"]))
            self.assertEqual(code, 0, out + err)
        self.assertEqual(len(calls), 2)
        (first_id, *direct), (second_id, *looped) = calls
        self.assertEqual((first_id, second_id), (self.ids[0], self.ids[1]))
        self.assertEqual(direct, looped)
        self.assertEqual(looped[0], "approve")

    def test_approve_argv_never_allows_a_repeat(self):
        args = make_parser().parse_args(["review", "--approver", "me", "--optout", "o.json"])
        argv = review_loop.approve_argv(args, "P1")
        self.assertEqual(argv, ["approve", "P1", "--approver=me", "--optout=o.json"])
        self.assertNotIn("--allow-repeat", argv)
        parsed = make_parser().parse_args(argv)
        self.assertEqual((parsed.approver, str(parsed.optout), parsed.allow_repeat), ("me", "o.json", False))


class ConfirmationTests(Base):
    def setUp(self):
        super().setUp()
        with connect(self.db) as db:
            confirmation.enable(db)

    def prefix(self, pid):
        with connect(self.db) as db:
            return approval_payload_sha256(get_post(db, pid), scheduled_at=None, use_row_schedule=False)[:8]

    def test_hash_prefix_is_asked_for_every_approval(self):
        tty = FakeTty(["a\n", self.prefix(self.ids[0]) + "\n", "a\n", self.prefix(self.ids[1]) + "\n", "q\n"])
        code, out, err = self.cli("review", tty=tty)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.statuses(), ["approved", "approved", "draft"])
        self.assertEqual(len(self.events("human_confirmation_passed")), 2)
        self.assertEqual(sum("Type the first 8 characters" in p for p in tty.prompts), 2)

    def test_wrong_prefix_approves_nothing(self):
        tty = FakeTty(["a\n", "00000000\n", "00000000\n", "00000000\n", "q\n"])
        code, out, _ = self.cli("review", tty=tty)
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(), ["draft"] * 3)
        self.assertIn(confirmation.FAILED, out)


class OwnerOnlyTests(unittest.TestCase):
    def test_catalog_marks_it_owner_only_and_interactive(self):
        self.assertIn("review", catalog.owner_only_commands())
        self.assertIn("interactive", catalog.COMMANDS["review"]["tags"])
        self.assertTrue(catalog.is_owner_only("reachweaver review --campaign launch"))
        self.assertFalse(catalog.is_owner_only("reachweaver review-packet --post-id P1 --out d"))

    def test_agent_guard_refuses_review_but_not_review_packet(self):
        for command in ("reachweaver review", "reachweaver review --campaign launch",
                        "python3 -m reachweaver.cli review", "python3 scripts/run.py --db x review --account a",
                        "bash -c 'reachweaver review'",
                        "python3 -c \"from reachweaver.cli import main; main(['review'])\""):
            with self.subTest(command=command):
                self.assertIsNotNone(agent_guard.check_command(command))
        for command in ("reachweaver review-packet --post-id P1 --out d", "reachweaver review-status --packet d",
                        "python3 -c \"from reachweaver.cli import main; main(['review-status', '--packet', 'd'])\"",
                        "reachweaver draft --account a --text 'please review this'"):
            with self.subTest(command=command):
                self.assertIsNone(agent_guard.check_command(command))

    def test_mcp_server_never_exposes_it(self):
        self.assertIn("review", mcp_server.NEVER_EXPOSED)


if __name__ == "__main__":
    unittest.main()
