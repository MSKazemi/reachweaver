"""Issue #7 D6 (human-presence confirmation) and D7 (keyed approval binding). Offline.

Owner-approved 2026-10-09: docs/SECURITY-REVIEW-PROPOSAL-7-d6-d7.md. Every
terminal is a fake (``/dev/tty`` is never opened except one real pseudo-terminal test) and every
provider call is mocked; nothing here can publish.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import confirmation, postiz
from reachweaver.accounts import NON_ACCOUNT_CREDENTIALS, load_accounts
from reachweaver.cli import main
from reachweaver.health import build_health
from reachweaver.policy import ValidationError
from reachweaver.privacy import redact
from reachweaver.storage import (
    APPROVAL_KEY_CREDENTIAL, APPROVAL_KEY_UNAVAILABLE, KEYED_MODE_SETTING, MAC_MISMATCH, MAC_MISSING,
    SCHEMA, ApprovalKeyMode, add_post, approval_binding_problem, approval_key_mode, approval_mac,
    approval_payload_sha256, approval_rebind_required, check_approval_binding, claim_sending, connect,
    get_post, keyed_mode_latched, queue_health, transition,
)

NOW = "2026-10-09T12:00:00+00:00"
FUTURE = "2026-10-20T09:00:00+00:00"
KEY = "FAKE-offline-test-approval-key-0123456789abcdef"
OTHER_KEY = "FAKE-offline-test-other-approval-key-fedcba9876543210"
WITH_KEY = {APPROVAL_KEY_CREDENTIAL: KEY}


def events(db, action):
    return [r["detail"] for r in db.execute("SELECT detail FROM event_log WHERE action=? ORDER BY id", (action,))]


def forge(db, pid, content="Forged after approval"):
    """An attacker with DB write but no key: edit content and recompute the plain hash."""
    db.execute("UPDATE posts SET content=? WHERE id=?", (content, pid))
    row = get_post(db, pid)
    db.execute("UPDATE posts SET approved_payload_sha256=? WHERE id=?", (approval_payload_sha256(row), pid))


class FakeTty:
    """Stands in for /dev/tty: scripted answers, recorded prompts."""

    def __init__(self, answers, on_read=None):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.closed = False
        self.on_read = on_read

    def __call__(self):
        return self

    def write(self, text):
        self.prompts.append(text)

    def readline(self):
        if self.on_read:
            self.on_read()
        return self.answers.pop(0) if self.answers else None

    def close(self):
        self.closed = True


class Terminal(io.StringIO):
    def isatty(self):
        return True


class NoReadStdin:
    """A terminal stdin that must never be read: the answer comes from /dev/tty only."""

    def isatty(self):
        return True

    def read(self, *a):
        raise AssertionError("stdin was read")

    readline = read


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = str(self.root / "outbox.sqlite")

    def approved(self, db, content="A useful verified research note"):
        pid = add_post(db, "my-bluesky", "bluesky", content, "launch", "https://example.org/r")
        transition(db, pid, "approve")
        return pid


# --------------------------------------------------------------------------- D7 keyed binding
class KeyedBindingStorageTests(Base):
    def test_keyed_approve_writes_mac_latches_and_claims(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            pid = self.approved(db)
            row = get_post(db, pid)
            self.assertEqual(row["approved_payload_mac"],
                             approval_mac(KEY.encode(), pid, row["approved_payload_sha256"]))
            self.assertTrue(row["approved_payload_mac"].startswith("hmac-sha256/1:"))
            self.assertTrue(keyed_mode_latched(db))
            self.assertIn("keyed=hmac-sha256/1", events(db, "approve")[-1])
            self.assertTrue(claim_sending(db, pid, now=NOW, expected_sha256=approval_payload_sha256(row)))

    def test_forged_row_with_recomputed_plain_hash_is_refused(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            pid = self.approved(db)
            forge(db, pid)
            forged = get_post(db, pid)
            self.assertEqual(forged["approved_payload_sha256"], approval_payload_sha256(forged))  # plain hash fooled
            with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                claim_sending(db, pid, now=NOW)
            row = get_post(db, pid)
            self.assertEqual((row["status"], row["last_error"]), ("approved", MAC_MISMATCH))
            self.assertEqual(events(db, "send_claimed"), [])
            self.assertEqual(len(events(db, "approval_binding_failed")), 1)

    def test_fabricated_approval_without_mac_is_refused(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            self.approved(db)  # latches keyed mode
            pid = add_post(db, "my-bluesky", "bluesky", "Never approved by a human")
            row = get_post(db, pid)
            db.execute("UPDATE posts SET status='approved', approved_payload_sha256=? WHERE id=?",
                       (approval_payload_sha256(row, scheduled_at=None, use_row_schedule=False), pid))
            with self.assertRaisesRegex(ValidationError, MAC_MISSING):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "approved")

    def test_mac_copied_to_identical_post_is_refused(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            first = self.approved(db, content="Same words")
            second = add_post(db, "my-bluesky", "bluesky", "Same words", "launch", "https://example.org/r")
            src = get_post(db, first)
            db.execute("UPDATE posts SET status='approved', approved_payload_sha256=?, approved_payload_mac=?, "
                       "approval_nonce=? WHERE id=?",  # the nonce is part of the hash (wave 9 item 1): copy it too
                       (src["approved_payload_sha256"], src["approved_payload_mac"], src["approval_nonce"], second))
            self.assertEqual(approval_payload_sha256(get_post(db, second)), src["approved_payload_sha256"])
            with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                claim_sending(db, second, now=NOW)

    def test_other_key_does_not_verify(self):
        with connect(self.db) as db:
            with patch.dict(os.environ, WITH_KEY):
                pid = self.approved(db)
            with patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: OTHER_KEY}):
                with self.assertRaisesRegex(ValidationError, MAC_MISMATCH):
                    claim_sending(db, pid, now=NOW)

    def test_key_missing_after_latch_fails_closed_without_touching_posts(self):
        with connect(self.db) as db:
            with patch.dict(os.environ, WITH_KEY):
                pid = self.approved(db)
                draft = add_post(db, "my-bluesky", "bluesky", "A second careful note")
            env = {k: v for k, v in os.environ.items() if k != APPROVAL_KEY_CREDENTIAL}
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(ValidationError, APPROVAL_KEY_UNAVAILABLE):
                    claim_sending(db, pid, now=NOW)
                self.assertEqual(check_approval_binding(db, pid, now=NOW), APPROVAL_KEY_UNAVAILABLE)
                row = get_post(db, pid)
                self.assertEqual((row["status"], row["last_error"]), ("approved", None))  # config, not post
                self.assertEqual(events(db, "approval_binding_failed"), [])
                with self.assertRaisesRegex(ValidationError, APPROVAL_KEY_UNAVAILABLE):
                    transition(db, draft, "approve")
                self.assertEqual(get_post(db, draft)["status"], "draft")
                with self.assertRaisesRegex(ValidationError, "Nothing changed by schedule"):
                    transition(db, pid, "schedule", FUTURE)
                self.assertEqual(transition(db, pid, "unapprove"), "draft")  # revoking never needs the key
                self.assertIsNone(get_post(db, pid)["approved_payload_mac"])
                self.assertEqual(transition(db, draft, "cancel"), "cancelled")

    def test_mac_present_but_latch_removed_and_no_key_fails_closed(self):
        with connect(self.db) as db:
            with patch.dict(os.environ, WITH_KEY):
                pid = self.approved(db)
            db.execute("DELETE FROM settings WHERE name=?", (KEYED_MODE_SETTING,))
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValidationError, APPROVAL_KEY_UNAVAILABLE):
                    claim_sending(db, pid, now=NOW)

    def test_latch_is_tighten_only_and_key_presence_alone_requires_mac(self):
        with connect(self.db) as db:
            pid = self.approved(db)  # unkeyed, no latch
            db.execute("DELETE FROM settings WHERE name=?", (KEYED_MODE_SETTING,))
            with patch.dict(os.environ, WITH_KEY):  # verifier holds the key: downgrade impossible
                with self.assertRaisesRegex(ValidationError, MAC_MISSING):
                    claim_sending(db, pid, now=NOW)
                self.assertEqual(check_approval_binding(db, pid, now=NOW), MAC_MISSING)
                self.assertTrue(keyed_mode_latched(db))  # the pre-claim gate latched it

    def test_unknown_latch_value_still_counts_as_keyed(self):
        with connect(self.db) as db:
            pid = self.approved(db)
            db.execute("INSERT INTO settings(name,value) VALUES(?, 'garbage')", (KEYED_MODE_SETTING,))
            with patch.dict(os.environ, {}, clear=True):
                self.assertTrue(approval_key_mode(db).required)
                with self.assertRaisesRegex(ValidationError, APPROVAL_KEY_UNAVAILABLE):
                    claim_sending(db, pid, now=NOW)

    def test_migration_unkeyed_approvals_work_until_key_then_reapproval(self):
        with connect(self.db) as db, patch.dict(os.environ, {}, clear=True):
            pid = self.approved(db)
            self.assertIsNone(get_post(db, pid)["approved_payload_mac"])
            self.assertIsNone(approval_binding_problem(get_post(db, pid), approval_key_mode(db)))
        with connect(self.db) as db, patch.dict(os.environ, WITH_KEY, clear=True):
            self.assertEqual([p["reason"] for p in approval_rebind_required(db, approval_key_mode(db))],
                             [MAC_MISSING])
            with self.assertRaisesRegex(ValidationError, MAC_MISSING):
                claim_sending(db, pid, now=NOW)
            self.assertEqual(get_post(db, pid)["status"], "approved")  # D5: stays approved
            transition(db, pid, "unapprove")
            transition(db, pid, "approve")  # D2: a fresh human re-approval, now keyed
            self.assertTrue(claim_sending(db, pid, now=NOW))

    def test_old_database_gains_mac_column(self):
        old = SCHEMA.replace("  approval_channel TEXT,\n  approved_payload_mac TEXT,\n", "  approval_channel TEXT,\n")
        self.assertNotEqual(old, SCHEMA)
        con = sqlite3.connect(self.db, isolation_level=None)
        con.executescript(old)
        con.close()
        Path(self.db).chmod(0o600)
        with connect(self.db) as db:
            self.assertIn("approved_payload_mac", [r[1] for r in db.execute("PRAGMA table_info(posts)")])

    def test_schedule_verifies_old_mac_then_rebinds(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            pid = self.approved(db)
            first = get_post(db, pid)["approved_payload_mac"]
            transition(db, pid, "schedule", FUTURE)
            row = get_post(db, pid)
            self.assertNotEqual(row["approved_payload_mac"], first)
            self.assertIsNone(approval_binding_problem(row, approval_key_mode(db)))
            db.execute("UPDATE posts SET approved_payload_mac=? WHERE id=?", ("hmac-sha256/1:" + "0" * 64, pid))
            with self.assertRaisesRegex(ValidationError, MAC_MISMATCH + ".*Nothing scheduled"):
                transition(db, pid, "schedule", "2026-10-21T09:00:00+00:00")
            self.assertEqual(get_post(db, pid)["scheduled_at"], FUTURE)

    def test_short_or_control_character_key_is_unusable_and_never_echoed(self):
        for bad in ("short-key", KEY + "\r"):
            with self.subTest(bad=len(bad)), connect(self.db) as db, \
                    patch.dict(os.environ, {APPROVAL_KEY_CREDENTIAL: bad}, clear=True):
                mode = approval_key_mode(db)
                self.assertIsNone(mode.key)
                self.assertTrue(mode.required)
                self.assertIn(APPROVAL_KEY_CREDENTIAL, mode.key_error)
                self.assertNotIn(bad.strip(), mode.key_error)
                draft = add_post(db, "my-bluesky", "bluesky", "Another careful note")
                with self.assertRaises(ValidationError) as caught:
                    transition(db, draft, "approve")
                self.assertIn(APPROVAL_KEY_UNAVAILABLE, str(caught.exception))
                self.assertNotIn(bad.strip(), str(caught.exception))

    def test_key_from_loadcredential_directory(self):
        creds = self.root / "creds"
        creds.mkdir(mode=0o700)
        path = creds / APPROVAL_KEY_CREDENTIAL
        path.write_text(KEY + "\n")
        path.chmod(0o600)
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(creds)}, clear=True), connect(self.db) as db:
            pid = self.approved(db)
            self.assertEqual(get_post(db, pid)["approved_payload_mac"],
                             approval_mac(KEY.encode(), pid, get_post(db, pid)["approved_payload_sha256"]))
            self.assertEqual(redact(f"oops {KEY}"), f"oops [REDACTED:env:{APPROVAL_KEY_CREDENTIAL}]")

    def test_key_is_a_reserved_secret_and_never_stored(self):
        self.assertIn(APPROVAL_KEY_CREDENTIAL, NON_ACCOUNT_CREDENTIALS)
        self.assertNotIn(KEY, repr(ApprovalKeyMode(key=KEY.encode())))
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            self.assertEqual(redact(f"value {KEY}"), f"value [REDACTED:env:{APPROVAL_KEY_CREDENTIAL}]")
            pid = self.approved(db)
            transition(db, pid, "schedule", FUTURE)
            dump = "\n".join(str(tuple(r)) for t in ("posts", "event_log", "settings")
                             for r in db.execute(f"SELECT * FROM {t}"))
            self.assertNotIn(KEY, dump)

    def test_queue_health_offline_vs_checked(self):
        with connect(self.db) as db:
            with patch.dict(os.environ, WITH_KEY):
                self.approved(db)
            with patch.dict(os.environ, {}, clear=True):
                offline = queue_health(db, now=NOW)
                self.assertEqual(offline["approval_keyed_binding"]["key_available"], None)
                self.assertFalse(offline["attention_required"])
                checked = queue_health(db, now=NOW, key_mode=approval_key_mode(db))
                self.assertIs(checked["approval_keyed_binding"]["key_available"], False)
                self.assertTrue(checked["attention_required"])
                self.assertEqual(checked["approval_rebind_required"], [])  # a process problem, not per post
                report = build_health(db, {}, now=NOW, env={})
                self.assertEqual(report["overall"], "blocked")
                self.assertIn("approval_key_unavailable", [r["code"] for r in report["reasons"]])


class KeyedBindingCliTests(Base):
    def setUp(self):
        super().setUp()
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_KEYED",
             "handle": "example.bsky.social"}]}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live_env(self, **extra):
        return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
                "REACHWEAVER_TEST_KEYED_IDENTIFIER": "name", "REACHWEAVER_TEST_KEYED_APP_PASSWORD": "app-pw", **extra}

    def draft(self):
        with connect(self.db) as db:
            return add_post(db, "my-bluesky", "bluesky", "A useful verified research note")

    def test_approve_reports_keyed_and_live_send_blocks_forged_row_before_preflight(self):
        pid = self.draft()
        code, out, err = self.cli("approve", pid, "--approver", "owner", env=WITH_KEY)
        self.assertEqual(code, 0, err)
        self.assertIn("keyed hmac-sha256/1", out)
        self.assertNotIn(KEY, out + err)
        with connect(self.db) as db:
            forge(db, pid)
        with patch("reachweaver.cli.check_connection") as preflight, patch("reachweaver.cli.publish") as sent:
            code, out, _ = self.cli("publish", "--live", "--post-id", pid, env=self.live_env(**WITH_KEY))
        self.assertEqual(code, 2)
        self.assertIn(MAC_MISMATCH, out)
        preflight.assert_not_called()
        sent.assert_not_called()

    def test_keyed_reapproval_then_live_send(self):
        pid = self.draft()
        self.cli("approve", pid, env=WITH_KEY)
        receipt = ("at://did:plc:x/app.bsky.feed.post/1", None)
        with patch("reachweaver.cli.check_connection"), patch("reachweaver.cli.publish", return_value=receipt) as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env(**WITH_KEY))
        self.assertEqual(code, 0, out + err)
        sent.assert_called_once()
        with patch("reachweaver.cli.check_connection") as preflight:
            other = self.draft()
            self.cli("approve", other, env=WITH_KEY)
            code, out, err = self.cli("publish", "--live", "--post-id", other, env=self.live_env())  # key gone
        self.assertEqual(code, 2)
        self.assertIn(APPROVAL_KEY_UNAVAILABLE, out)
        preflight.assert_not_called()

    def test_dry_run_and_show_read_no_credential_even_in_keyed_mode(self):
        pid = self.draft()
        self.cli("approve", pid, env=WITH_KEY)
        boom = AssertionError("credential read")
        with patch("reachweaver.accounts.credential", side_effect=boom):
            code, out, err = self.cli("publish", env=WITH_KEY)
            self.assertEqual(code, 0, err)
            self.assertIn("Keyed approval binding (#7 D7): ON", out)
            self.assertNotIn("would be BLOCKED until re-approved", out)
            code, out, err = self.cli("show", pid, env=WITH_KEY)
        self.assertEqual(code, 0, err)
        shown = json.loads(out)["approval"]
        self.assertEqual((shown["binding"], shown["keyed_tag"]), ("bound", "present"))

    def test_approval_mode_status_and_require_key(self):
        code, out, err = self.cli("approval-mode", "--require-key")
        self.assertEqual(code, 2)
        self.assertIn(APPROVAL_KEY_CREDENTIAL, err)
        with connect(self.db) as db:
            self.assertFalse(keyed_mode_latched(db))
        code, out, err = self.cli("approval-mode", "--require-key", env=WITH_KEY)
        self.assertEqual(code, 0, err)
        self.assertIn("Keyed approval binding (#7 D7): required; latched=yes", out)
        self.assertNotIn(KEY, out + err)
        code, out, _ = self.cli("approval-mode")
        self.assertIn("latched=yes", out)
        self.assertIn("Human confirmation (#7 D6): off", out)


class KeyedBindingPostizTests(Base):
    def setUp(self):
        super().setUp()
        config = self.root / "accounts.json"
        config.write_text(json.dumps({"accounts": [
            {"id": "pz-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZK", "handle": "me.bsky.social",
             "backend": "postiz", "postiz_base_url": "https://api.postiz.com",
             "postiz_integration_id": "int-blue-1"}]}))
        self.account = load_accounts(config)["pz-blue"]
        self.integration = postiz.VerifiedIntegration("pz-blue", "bluesky", "int-blue-1", "bluesky")

    def test_postiz_blocks_forged_keyed_row_before_backend(self):
        with patch.dict(os.environ, WITH_KEY), connect(self.db) as db:
            pid = add_post(db, "pz-blue", "bluesky", "Release notes https://example.org/r")
            transition(db, pid, "approve")
            forge(db, pid, "Release notes https://attacker.example/r")
            out = io.StringIO()
            with patch("reachweaver.postiz.verify_integration", return_value=self.integration) as preflight, \
                    patch("reachweaver.publishers.publish") as sent, contextlib.redirect_stdout(out):
                code = postiz.run_live_send(db, get_post(db, pid), self.account, max_per_24h=2,
                                            min_gap_minutes=60, optout=[])
            self.assertEqual(code, 2)
            self.assertIn(MAC_MISMATCH, out.getvalue())
            preflight.assert_not_called()
            sent.assert_not_called()


# --------------------------------------------------------------------- D6 human confirmation
class ConfirmPrefixTests(unittest.TestCase):
    DIGEST = "ab12cd34" + "e" * 56

    def run_confirm(self, tty, stdin=None, stdout=None):
        return confirmation.confirm_prefix(self.DIGEST, prompt="approve post p1",
                                           stdin=stdin or NoReadStdin(), stdout=stdout or Terminal(), open_tty=tty)

    def test_correct_prefix_passes_and_tty_is_closed(self):
        tty = FakeTty(["ab12cd34\n"])
        self.run_confirm(tty)
        self.assertTrue(tty.closed)
        self.assertEqual(len(tty.prompts), 1)
        self.assertNotIn("ab12cd34", "".join(tty.prompts))  # the prompt never reveals the answer

    def test_case_and_whitespace_are_tolerated(self):
        self.run_confirm(FakeTty(["  AB12CD34 \r\n"]))

    def test_no_tty_on_stdin_or_stdout_is_refused_before_opening_dev_tty(self):
        for stdin, stdout in ((io.StringIO("ab12cd34\n"), Terminal()), (NoReadStdin(), io.StringIO())):
            tty = FakeTty(["ab12cd34\n"])
            with self.subTest(stdin=type(stdin).__name__), \
                    self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.REQUIRES_TTY):
                self.run_confirm(tty, stdin=stdin, stdout=stdout)
            self.assertEqual(tty.prompts, [])

    def test_piped_stdin_cannot_answer(self):
        piped = io.StringIO("ab12cd34\n")  # an agent piping the right answer
        with self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.REQUIRES_TTY):
            self.run_confirm(FakeTty([]), stdin=piped)
        self.assertEqual(piped.tell(), 0)  # never read

    def test_dev_tty_unavailable(self):
        def no_tty():
            raise OSError(6, "No such device or address")
        with self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.TTY_UNAVAILABLE):
            self.run_confirm(no_tty)

    def test_wrong_prefix_three_times_exhausts(self):
        tty = FakeTty(["00000000\n", "ab12cd3\n", "ab12cd345\n", "ab12cd34\n"])
        with self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.FAILED):
            self.run_confirm(tty)
        self.assertEqual(sum("attempt" in p for p in tty.prompts), confirmation.MAX_ATTEMPTS)
        self.assertEqual(tty.answers, ["ab12cd34\n"])  # no fourth attempt
        self.assertTrue(tty.closed)

    def test_wrong_then_right_passes(self):
        self.run_confirm(FakeTty(["deadbeef\n", "ab12cd34\n"]))

    def test_non_ascii_answer_is_just_wrong(self):
        with self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.FAILED):
            self.run_confirm(FakeTty(["ab12cd3é\n"] * 3))

    def test_end_of_input_aborts(self):
        with self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.ABORTED):
            self.run_confirm(FakeTty([]))

    @unittest.skipUnless(hasattr(os, "openpty") and sys.platform.startswith("linux"), "needs a Linux pty")
    def test_real_pseudo_terminal_channel_reads_a_line(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        channel = confirmation._DevTty(os.ttyname(slave))
        self.addCleanup(channel.close)
        os.write(master, b"ab12cd34\n")
        self.assertEqual(channel.readline().strip(), "ab12cd34")
        channel.write("prompt")
        import select
        seen = b""  # the pty may echo the typed line first
        while b"prompt" not in seen and select.select([master], [], [], 2)[0]:
            seen += os.read(master, 100)
        self.assertIn(b"prompt", seen)


class ConfirmationSettingTests(Base):
    def test_off_by_default_and_unknown_value_counts_as_on(self):
        with connect(self.db) as db:
            self.assertIsNone(confirmation.confirmation_mode(db))
            db.execute("INSERT INTO settings(name,value) VALUES(?, 'weird')", (confirmation.SETTING,))
            self.assertEqual(confirmation.confirmation_mode(db), confirmation.TTY_HASH_PREFIX)

    def test_unknown_mode_cannot_be_enabled(self):
        with connect(self.db) as db, self.assertRaises(ValidationError):
            confirmation.enable(db, "nope")


class ConfirmationCliTests(Base):
    def setUp(self):
        super().setUp()
        self.config = self.root / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_TEST_CONF",
             "handle": "example.bsky.social"}]}), encoding="utf-8")
        with connect(self.db) as db:
            confirmation.enable(db)

    def cli(self, *argv, env=None, tty=None, terminal=False):
        out = Terminal() if terminal else io.StringIO()
        err = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env or {}, clear=True))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            if terminal:
                stack.enter_context(patch("sys.stdin", NoReadStdin()))
            stack.enter_context(patch("reachweaver.confirmation._DevTty",
                                      tty if tty is not None else FakeTty([])))
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def live_env(self):
        return {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod",
                "REACHWEAVER_TEST_CONF_IDENTIFIER": "name", "REACHWEAVER_TEST_CONF_APP_PASSWORD": "app-pw"}

    def draft(self):
        with connect(self.db) as db:
            return add_post(db, "my-bluesky", "bluesky", "A useful verified research note")

    def prefix(self, pid):
        with connect(self.db) as db:
            return approval_payload_sha256(get_post(db, pid), scheduled_at=None, use_row_schedule=False)[:8]

    def approve_with_tty(self, pid):
        code, out, err = self.cli("approve", pid, tty=FakeTty([self.prefix(pid) + "\n"]), terminal=True)
        self.assertEqual(code, 0, out + err)
        return out

    def test_approve_without_terminal_is_refused_and_draft_unchanged(self):
        pid = self.draft()
        code, out, err = self.cli("approve", pid)  # stdout is a pipe, as in an agent subprocess
        self.assertEqual(code, 2)
        self.assertIn(confirmation.REQUIRES_TTY, err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertEqual(len(events(db, "human_confirmation_failed")), 1)

    def test_approve_with_terminal_and_correct_prefix(self):
        pid = self.draft()
        out = self.approve_with_tty(pid)
        self.assertIn("payload sha256 ", out)
        self.assertIn("confirmed tty_hash_prefix", out)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertIn("confirmation=tty_hash_prefix", events(db, "approve")[-1])
            self.assertEqual(len(events(db, "human_confirmation_passed")), 1)

    def test_approve_wrong_prefix_leaves_draft(self):
        pid = self.draft()
        code, out, err = self.cli("approve", pid, tty=FakeTty(["00000000\n"] * 3), terminal=True)
        self.assertEqual(code, 2)
        self.assertIn(confirmation.FAILED, err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
            self.assertIn("reason=human_confirmation_failed", events(db, "human_confirmation_failed")[-1])

    def test_post_changed_while_confirming_is_not_approved(self):
        pid = self.draft()
        answer = self.prefix(pid)

        def tamper():
            with connect(self.db) as db:
                db.execute("UPDATE posts SET content='Swapped while the human typed' WHERE id=?", (pid,))
        code, _, err = self.cli("approve", pid, tty=FakeTty([answer + "\n"], on_read=tamper), terminal=True)
        self.assertEqual(code, 2)
        self.assertIn("changed after it was shown for confirmation", err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")

    def test_live_without_terminal_refused_before_any_account_load(self):
        pid = self.draft()
        self.approve_with_tty(pid)
        with patch("reachweaver.cli.load_accounts", side_effect=AssertionError("account loaded")), \
                patch("reachweaver.cli.publish") as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, "--log-json", env=self.live_env())
        self.assertEqual(code, 2)
        self.assertIn(confirmation.REQUIRES_TTY, err)
        self.assertIn('"reason": "human_confirmation"', err)
        sent.assert_not_called()

    def test_live_with_terminal_and_prefix_sends_exact_confirmed_payload(self):
        pid = self.draft()
        self.approve_with_tty(pid)
        with connect(self.db) as db:
            digest = approval_payload_sha256(get_post(db, pid))
        receipt = ("at://did:plc:x/app.bsky.feed.post/1", None)
        with patch("reachweaver.cli.check_connection"), patch("reachweaver.cli.publish", return_value=receipt) as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env(),
                                      tty=FakeTty([digest[:8] + "\n"]), terminal=True)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"payload sha256 {digest}", out)
        sent.assert_called_once()

    def test_live_wrong_prefix_no_claim_no_send(self):
        pid = self.draft()
        self.approve_with_tty(pid)
        with patch("reachweaver.cli.check_connection") as preflight, patch("reachweaver.cli.publish") as sent:
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=self.live_env(),
                                      tty=FakeTty(["00000000\n"] * 3), terminal=True)
        self.assertEqual(code, 2)
        self.assertIn(confirmation.FAILED, err)
        preflight.assert_not_called()
        sent.assert_not_called()
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])

    def test_disable_needs_the_ritual(self):
        code, _, err = self.cli("approval-mode", "--confirmation", "off")
        self.assertEqual(code, 2)
        self.assertIn(confirmation.REQUIRES_TTY, err)
        with connect(self.db) as db:
            self.assertEqual(confirmation.confirmation_mode(db), confirmation.TTY_HASH_PREFIX)
        with patch("reachweaver.confirmation.secrets.token_hex", return_value="cafef00d" + "0" * 56):
            code, out, err = self.cli("approval-mode", "--confirmation", "off",
                                      tty=FakeTty(["cafef00d\n"]), terminal=True)
        self.assertEqual(code, 0, err)
        self.assertIn("Human confirmation (#7 D6): off", out)
        with connect(self.db) as db:
            self.assertIsNone(confirmation.confirmation_mode(db))
            self.assertEqual(len(events(db, "approval_confirmation_disabled")), 1)


class ConfirmationPostizTests(Base):
    def test_postiz_send_requires_confirmation_before_preflight(self):
        config = self.root / "accounts.json"
        config.write_text(json.dumps({"accounts": [
            {"id": "pz-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZC", "handle": "me.bsky.social",
             "backend": "postiz", "postiz_base_url": "https://api.postiz.com",
             "postiz_integration_id": "int-blue-1"}]}))
        account = load_accounts(config)["pz-blue"]
        with connect(self.db) as db, patch.dict(os.environ, {}, clear=True):
            pid = add_post(db, "pz-blue", "bluesky", "Release notes https://example.org/r")
            transition(db, pid, "approve")
            confirmation.enable(db)
            with patch("reachweaver.postiz.verify_integration") as preflight, \
                    patch("reachweaver.publishers.publish") as sent, contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(confirmation.ConfirmationError, confirmation.REQUIRES_TTY):
                postiz.run_live_send(db, get_post(db, pid), account, max_per_24h=2, min_gap_minutes=60, optout=[])
            preflight.assert_not_called()
            sent.assert_not_called()
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])


if __name__ == "__main__":
    unittest.main()
