"""Approval binding of send options and the send-time repeat guard (owner-approved 2026-10-21).

Sheet item 5: a Mastodon account's ``visibility``, ``language`` and ``spoiler_text`` are bound into the
approval (``approval/options-1``); a change in accounts.json after approval refuses the send before any
request, and an unchanged account sends exactly the bound values. 5b: a Mastodon approval made before the
binding existed is not grandfathered. Sheet item 9: the same for a Postiz account's server and integration.
Sheet item 1 (D1-D4): ``claim_sending`` refuses an exact repeat of material the same account has already
shared or may have shared; only a bound ``--allow-repeat`` (``approval/repeat-1``) passes, never the
``repeat_override`` audit event. Everything is offline: every request goes to a mock.
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
from pathlib import Path
from unittest.mock import patch

from gate_support import LIVE_ENV
from test_postiz import FAKE_KEY, FakePostiz

from reachweaver import send_options, sendgate
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.schemas import canonical_json
from reachweaver.send_options import SEND_OPTIONS_CHANGED, SEND_OPTIONS_MISSING, SEND_OPTIONS_TAG
from reachweaver.storage import (
    REPEAT_REFUSAL, REPEAT_TAG, SCHEMA, add_post, approval_binding_problem, approval_payload,
    approval_payload_sha256, approval_rebind_required, claim_sending, connect, get_post,
    latch_identity_mode, migrate, record_shared, transition,
)
from reachweaver.worker_log import SKIP_REASONS, claim_refusal_reason

SERVER = "https://social.example.org"
MASTO = {"id": "my-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_SB_MASTO", "server": SERVER,
         "handle": "owner", "visibility": "unlisted", "language": "de", "spoiler_text": "CW: release notes"}
MASTO_TWO = {"id": "my-masto-two", "platform": "mastodon", "env_prefix": "REACHWEAVER_SB_MASTO2", "server": SERVER,
             "handle": "owner"}
BSKY = {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_SB_BSKY", "handle": "owner.bsky.social"}
POSTIZ = {"id": "pz-blue", "platform": "bluesky", "env_prefix": "REACHWEAVER_PZ", "handle": "me.bsky.social",
          "backend": "postiz", "postiz_base_url": "https://api.postiz.com", "postiz_integration_id": "int-blue-1"}
ENV = {**LIVE_ENV, "REACHWEAVER_SB_MASTO_TOKEN": "mock-masto-token", "REACHWEAVER_SB_MASTO2_TOKEN": "mock-two",
       "REACHWEAVER_PZ_POSTIZ_API_KEY": FAKE_KEY}
TEXT = "ReachWeaver 0.13 binds the account send options into each approval."


def events(db, action, post_id=None):
    rows = db.execute("SELECT post_id, detail FROM event_log WHERE action=? ORDER BY id", (action,)).fetchall()
    return [dict(r) for r in rows if post_id is None or r["post_id"] == post_id]


class StatusServer:
    """Stands in for the Mastodon status endpoint; echoes the visibility it was sent, records each form."""

    def __init__(self):
        self.forms: list[dict] = []

    def __call__(self, url, payload, **kwargs):
        self.forms.append(dict(payload))
        n = len(self.forms)
        return {"id": f"20{n}", "url": f"{SERVER}/@owner/20{n}", "visibility": payload["visibility"],
                "account": {"username": "owner"}}


class Base(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.db = str(self.root / "state" / "db.sqlite")
        self.config = self.root / "accounts.json"
        self.write_accounts()
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        sendgate.reset()

    def write_accounts(self, masto=None, postiz=None):
        accounts = [masto or MASTO, MASTO_TWO, BSKY, postiz or POSTIZ]
        self.config.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")

    def cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def draft(self, account="my-masto", platform="mastodon", text=TEXT) -> str:
        with connect(self.db) as db:
            return add_post(db, account, platform, text)

    def approve(self, pid, *extra):
        code, out, err = self.cli("approve", pid, *extra)
        self.assertEqual(code, 0, out + err)
        return out

    def row(self, pid):
        with connect(self.db) as db:
            return get_post(db, pid)

    def age_claims(self):
        """Move earlier send claims out of the rolling budget windows, so only the checks under test decide."""
        with connect(self.db) as db:
            db.execute("UPDATE event_log SET at='2026-01-01T00:00:00+00:00' WHERE action='send_claimed'")

    def live_masto(self, *argv, log=None):
        """A live run whose Mastodon requests go to mocks: identity GET and the status POST."""
        server = StatusServer()
        extra = ["--log-json", "--log-file", str(log)] if log is not None else []
        with patch("reachweaver.publishers.request_get_json", return_value={"username": "owner", "id": "1"}) as get, \
                patch("reachweaver.publishers.request_json", side_effect=server) as post:
            code, out, err = self.cli("publish", "--live", *extra, *argv, env=ENV)
        return code, out + err, server, get, post


class MastodonSendOptionsTests(Base):
    """Sheet item 5."""

    def test_approve_binds_the_options_under_their_own_tag(self):
        pid = self.draft()
        out = self.approve(pid)
        self.assertIn("send options bound (approval/options-1)", out)
        self.assertIn("visibility=unlisted language=de spoiler_text=CW: release notes", out)
        account = load_accounts(self.config)["my-masto"]
        row = self.row(pid)
        self.assertEqual(row["approved_send_options"], send_options.fingerprint(account))
        payload = approval_payload(row)
        self.assertEqual(payload["send_options_binding"], SEND_OPTIONS_TAG)
        self.assertEqual(payload["send_options"], row["approved_send_options"])
        self.assertEqual(approval_payload_sha256(row), row["approved_payload_sha256"])
        with connect(self.db) as db:
            self.assertIn("send_options=bound", events(db, "approve", pid)[0]["detail"])

    def test_unchanged_options_send_exactly_the_bound_values(self):
        pid = self.draft()
        self.approve(pid)
        code, out, server, _, _ = self.live_masto("--post-id", pid)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(server.forms), 1)
        form = server.forms[0]
        self.assertEqual((form["status"], form["visibility"], form["language"], form["spoiler_text"]),
                         (TEXT, "unlisted", "de", "CW: release notes"))
        self.assertEqual(self.row(pid)["status"], "published")

    def test_each_changed_option_refuses_before_any_request_and_restoring_unblocks(self):
        for field, value in (("visibility", "public"), ("language", "en"), ("spoiler_text", None),
                             ("spoiler_text", "CW: other")):
            with self.subTest(field=field, value=value):
                pid = self.draft(text=f"{TEXT} {field} {value}")
                self.approve(pid)
                changed = dict(MASTO)
                if value is None:
                    changed.pop(field)
                else:
                    changed[field] = value
                self.write_accounts(masto=changed)
                log = self.root / f"log-{field}-{value}.jsonl"
                code, out, server, get, post = self.live_masto("--post-id", pid, log=log)
                self.assertEqual(code, 2, out)
                self.assertIn(f"BLOCKED {pid}: {SEND_OPTIONS_CHANGED}", out)
                get.assert_not_called()  # before the identity preflight
                post.assert_not_called()
                row = self.row(pid)
                self.assertEqual((row["status"], row["last_error"]), ("approved", SEND_OPTIONS_CHANGED))
                with connect(self.db) as db:
                    self.assertEqual(len(events(db, "approval_binding_failed", pid)), 1)
                    self.assertEqual(events(db, "send_claimed", pid), [])
                skipped = [json.loads(line) for line in log.read_text().splitlines()
                           if json.loads(line)["event"] == "post_skipped"]
                self.assertEqual([r["reason"] for r in skipped], ["rebind_required"])
                # Restoring the approved values makes the account match the approval again.
                self.write_accounts()
                self.age_claims()
                code, out, server, _, _ = self.live_masto("--post-id", pid)
                self.assertEqual(code, 0, out)
                self.assertEqual(server.forms[0]["visibility"], "unlisted")

    def test_claim_refuses_a_change_that_lands_after_the_pre_claim_check(self):
        pid = self.draft()
        self.approve(pid)
        changed = send_options.fingerprint({**load_accounts(self.config)["my-masto"], "visibility": "public"})
        with connect(self.db) as db:
            with self.assertRaisesRegex(ValidationError, SEND_OPTIONS_CHANGED):
                claim_sending(db, pid, send_options_fingerprint=changed)
            with self.assertRaisesRegex(ValidationError, SEND_OPTIONS_CHANGED):
                claim_sending(db, pid)  # a bound row and a caller that names no options fails closed
            self.assertEqual(get_post(db, pid)["status"], "approved")
            self.assertEqual(events(db, "send_claimed"), [])

    def test_send_gate_refuses_an_account_dict_that_differs_from_the_claimed_row(self):
        from gate_support import grant
        from reachweaver import publishers
        account = load_accounts(self.config)["my-masto"]
        grant(account, TEXT, "gate-post")  # a claim of the row approved with these values
        widened = {**account, "visibility": "public"}
        with patch.dict(os.environ, LIVE_ENV), patch("reachweaver.publishers.request_json") as post, \
                self.assertRaisesRegex(ValidationError, sendgate.TICKET_MISMATCH):
            publishers.publish(widened, TEXT, "gate-post")
        post.assert_not_called()
        # A row approved without options (claim registered with None) is refused for a Mastodon account too.
        sendgate.register_claim("gate-two", account_id="my-masto", platform="mastodon", content=TEXT, media=None,
                                max_per_24h=1, min_gap_minutes=60, send_options=None)
        with patch.dict(os.environ, LIVE_ENV), self.assertRaisesRegex(ValidationError, sendgate.TICKET_MISMATCH):
            publishers.publish(account, TEXT, "gate-two")

    def test_tampered_stored_options_are_a_hash_mismatch(self):
        pid = self.draft()
        self.approve(pid)
        with connect(self.db) as db:
            db.execute("UPDATE posts SET approved_send_options=? WHERE id=?", ("0" * 64, pid))
            self.assertEqual(approval_binding_problem(get_post(db, pid)), "approval_hash_mismatch_reapproval_required")

    def test_unapprove_clears_schedule_keeps_and_reapproval_rebinds(self):
        pid = self.draft()
        self.approve(pid)
        bound = self.row(pid)["approved_send_options"]
        with connect(self.db) as db:
            transition(db, pid, "schedule", "2026-12-01T09:00:00+00:00")
            row = get_post(db, pid)
            self.assertEqual(row["approved_send_options"], bound)
            self.assertIsNone(approval_binding_problem(row))
            transition(db, pid, "unapprove")
            self.assertIsNone(get_post(db, pid)["approved_send_options"])
        self.write_accounts(masto={**MASTO, "visibility": "public"})
        self.approve(pid)
        self.assertNotEqual(self.row(pid)["approved_send_options"], bound)

    def test_approve_refuses_a_mastodon_post_whose_account_cannot_be_loaded(self):
        with connect(self.db) as db:
            pid = add_post(db, "ghost-masto", "mastodon", TEXT)
        code, out, err = self.cli("approve", pid)
        self.assertEqual(code, 2, out + err)
        self.assertIn("approval_send_options_unavailable", err)
        self.assertEqual(self.row(pid)["status"], "draft")

    def test_show_reports_bound_and_changed_options(self):
        pid = self.draft()
        self.approve(pid)
        code, out, _ = self.cli("--plain", "show", pid)
        self.assertEqual(code, 0)
        view = json.loads(out)["approval"]["send_options"]
        self.assertEqual(view["state"], "bound; the account file matches")
        self.write_accounts(masto={**MASTO, "spoiler_text": "CW: changed"})
        view = json.loads(self.cli("--plain", "show", pid)[1])["approval"]["send_options"]
        self.assertEqual(view["state"], SEND_OPTIONS_CHANGED)
        self.assertIn("reapprove", view)


class PreBindingApprovalTests(Base):
    """Sheet item 5b: no grandfathering; other platforms keep their hashes and send unchanged."""

    def old_database(self):
        """A database exactly as before this change (no new columns), with approved Mastodon and Bluesky rows."""
        old = SCHEMA
        for line in ("  approved_send_options TEXT,  -- 2026-10-21 items 5/9 (same placement rule): see "
                     "reachweaver/send_options.py\n",
                     "  approved_allow_repeat TEXT,  -- 2026-10-21 item 1 D2 (same placement rule): '1' = approved "
                     "with --allow-repeat\n"):
            self.assertIn(line, old, "posts DDL moved; update this fixture")
            old = old.replace(line, "")
        Path(self.db).parent.mkdir(mode=0o700, exist_ok=True)
        con = sqlite3.connect(self.db, isolation_level=None)
        con.row_factory = sqlite3.Row
        ids = {}
        try:
            con.executescript(old)
            for account, platform in (("my-masto", "mastodon"), ("my-bsky", "bluesky")):
                ids[platform] = add_post(con, account, platform, f"{TEXT} {platform}")
                transition(con, ids[platform], "approve")  # the previous release's approval: no new columns
        finally:
            con.close()
        os.chmod(self.db, 0o600)
        return ids

    def test_hashes_unchanged_but_old_mastodon_approval_needs_reapproval_once(self):
        ids = self.old_database()
        with connect(self.db) as db:
            self.assertEqual(migrate(db), [])
            for pid in ids.values():
                row = get_post(db, pid)
                self.assertIsNone(row["approved_send_options"])
                self.assertIsNone(row["approved_allow_repeat"])
                # Golden: the payload carries none of the new keys, so the stored hash still matches.
                self.assertNotIn("send_options", approval_payload(row))
                self.assertNotIn("allow_repeat", approval_payload(row))
                self.assertIsNone(approval_binding_problem(row))
            listed = {item["post_id"]: item["reason"] for item in approval_rebind_required(db)}
        self.assertEqual(listed, {ids["mastodon"]: SEND_OPTIONS_MISSING})  # health lists only the Mastodon row
        code, out, _ = self.cli("publish")  # dry run, accounts never loaded
        self.assertEqual(code, 0)
        self.assertIn(f"BLOCKED {SEND_OPTIONS_MISSING}", out)
        code, out, server, get, post = self.live_masto("--post-id", ids["mastodon"])
        self.assertEqual(code, 2, out)
        self.assertIn(f"BLOCKED {ids['mastodon']}: {SEND_OPTIONS_MISSING}", out)
        get.assert_not_called()
        post.assert_not_called()
        self.assertEqual(self.row(ids["mastodon"])["status"], "approved")
        # The documented upgrade path: unapprove, show, approve; then it sends.
        self.assertEqual(self.cli("unapprove", ids["mastodon"])[0], 0)
        self.approve(ids["mastodon"])
        code, out, server, _, _ = self.live_masto("--post-id", ids["mastodon"])
        self.assertEqual(code, 0, out)
        self.assertEqual(server.forms[0]["visibility"], "unlisted")

    def test_payload_without_new_columns_is_byte_identical_to_the_previous_release(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-bsky", "bluesky", TEXT)
            transition(db, pid, "approve")
            row = get_post(db, pid)
        previous = {"binding": "reachweaver.storage.approval/1", "account_id": "my-bsky", "platform": "bluesky",
                    "content": TEXT, "scheduled_at": None, "source_url": "", "campaign": "",
                    "nonce_binding": "reachweaver.storage.approval/2", "approval_nonce": row["approval_nonce"]}
        self.assertEqual(row["approved_payload_sha256"], hashlib.sha256(canonical_json(previous)).hexdigest())


class PostizSendOptionsTests(Base):
    """Sheet item 9."""

    def postiz_run(self, pid):
        fake = FakePostiz()
        with patch("reachweaver.publishers._send_http", fake):
            code, out, err = self.cli("publish", "--live", "--post-id", pid, env=ENV)
        return code, out + err, fake

    def test_unchanged_integration_reaches_the_bound_integration(self):
        pid = self.draft("pz-blue", "bluesky")
        out = self.approve(pid)
        self.assertIn("postiz_integration_id=int-blue-1", out)
        code, out, fake = self.postiz_run(pid)
        self.assertEqual(code, 3, out)  # backend acceptance is needs_review, never published
        self.assertEqual(len(fake.creates), 1)
        body = json.loads(fake.creates[0].data)
        self.assertEqual(body["posts"][0]["integration"], {"id": "int-blue-1"})

    def test_changed_integration_or_server_refuses_before_any_request(self):
        for key, value in (("postiz_integration_id", "int-other-9"), ("postiz_base_url", "https://postiz.example.org")):
            with self.subTest(key=key):
                self.write_accounts()
                pid = self.draft("pz-blue", "bluesky", text=f"{TEXT} {key}")
                self.approve(pid)
                self.write_accounts(postiz={**POSTIZ, key: value})
                code, out, fake = self.postiz_run(pid)
                self.assertEqual(code, 2, out)
                self.assertIn(SEND_OPTIONS_CHANGED, out)
                self.assertEqual(fake.requests, [])  # not even GET /integrations
                self.assertEqual(self.row(pid)["status"], "approved")

    def test_old_postiz_approval_needs_reapproval(self):
        pid = self.draft("pz-blue", "bluesky")
        with connect(self.db) as db:
            transition(db, pid, "approve")  # as approved before the binding existed
        code, out, fake = self.postiz_run(pid)
        self.assertEqual(code, 2, out)
        self.assertIn(SEND_OPTIONS_MISSING, out)
        self.assertEqual(fake.requests, [])


class RepeatAtSendTimeTests(Base):
    """Sheet item 1: D1 recheck at send time, D2 bound override, D3 same account, D4 sending/needs_review count."""

    def two_approved_copies(self, *flags, account="my-masto", platform="mastodon"):
        first, second = self.draft(account, platform), self.draft(account, platform)
        self.approve(first)
        out = self.approve(second, *flags)
        return first, second, out

    def test_two_copies_approved_before_either_is_sent_only_one_goes_out(self):
        first, second, out = self.two_approved_copies()
        self.assertIn("send-time repeat check refuses the others", out)  # said at approve time
        log = self.root / "worker.jsonl"
        code, out, server, _, post = self.live_masto("--limit", "5", log=log)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(server.forms), 1)
        if self.row(first)["status"] != "published":  # both were due; whichever the run took first is sent
            first, second = second, first
        self.assertIn(f"SKIPPED {second}: {REPEAT_REFUSAL}", out)
        self.assertIn(f"post {first}, published", out)
        self.assertNotIn(TEXT, out.split(f"SKIPPED {second}")[1])  # never echoes the text
        self.assertEqual((self.row(first)["status"], self.row(second)["status"]), ("published", "approved"))
        self.assertIsNone(self.row(second)["last_error"])  # nothing written for the refused copy
        with connect(self.db) as db:
            self.assertEqual(events(db, "send_claimed", second), [])
            self.assertEqual(events(db, "approval_binding_failed", second), [])
        reasons = {json.loads(line)["post_id"]: json.loads(line).get("reason") for line in log.read_text().splitlines()
                   if json.loads(line)["event"] == "post_skipped"}
        self.assertEqual(reasons, {second: "repeat_of_shared_material"})
        # Re-refused on every run until a human acts; never sent.
        code, out, server, _, _ = self.live_masto("--post-id", second)
        self.assertEqual(code, 2, out)
        self.assertEqual(server.forms, [])

    def test_claim_under_the_lock_refuses_a_sibling_that_is_sending_or_needs_review(self):
        first, second, _ = self.two_approved_copies()
        options = send_options.fingerprint(load_accounts(self.config)["my-masto"])
        with connect(self.db) as db:
            self.assertTrue(claim_sending(db, first, send_options_fingerprint=options))  # first is now 'sending'
            with self.assertRaisesRegex(ValidationError, f"^{REPEAT_REFUSAL}: .*{first}, sending"):
                claim_sending(db, second, send_options_fingerprint=options)
            self.assertEqual(get_post(db, second)["status"], "approved")
            self.assertEqual(len(events(db, "send_claimed")), 1)

    def test_bound_allow_repeat_passes_and_the_audit_event_alone_never_does(self):
        first, second, out = self.two_approved_copies("--allow-repeat")
        self.assertIn("--allow-repeat bound (approval/repeat-1)", out)
        row = self.row(second)
        self.assertEqual(row["approved_allow_repeat"], "1")
        self.assertEqual((approval_payload(row)["repeat_binding"], approval_payload(row)["allow_repeat"]),
                         (REPEAT_TAG, "1"))
        code, out, server, _, _ = self.live_masto("--post-id", first)
        self.assertEqual(code, 0, out)
        self.age_claims()
        code, out, server, _, _ = self.live_masto("--post-id", second)
        self.assertEqual(code, 0, out)
        self.assertEqual(server.forms[0]["status"], TEXT)
        # A third copy approved without the flag: a repeat_override event in the log is not permission.
        third = self.draft()
        code, _, err = self.cli("approve", third)
        self.assertEqual(code, 2)  # approval itself refuses an exact repeat of published material
        with connect(self.db) as db:
            transition(db, third, "approve", send_options=send_options.fingerprint(
                load_accounts(self.config)["my-masto"]))
            db.execute("INSERT INTO event_log(at, post_id, action, detail) VALUES(?,?,?,?)",
                       ("2026-10-21T00:00:00+00:00", third, "repeat_override", "kinds=same_text"))
        code, out, server, _, _ = self.live_masto("--post-id", third)
        self.assertEqual(code, 2, out)
        self.assertIn(REPEAT_REFUSAL, out)
        self.assertEqual(server.forms, [])

    def test_allow_repeat_set_in_the_database_without_reapproval_is_a_hash_mismatch(self):
        first, second, _ = self.two_approved_copies()
        with connect(self.db) as db:
            db.execute("UPDATE posts SET approved_allow_repeat='1' WHERE id=?", (second,))
            self.assertEqual(approval_binding_problem(get_post(db, second)),
                             "approval_hash_mismatch_reapproval_required")

    def test_another_account_with_the_same_text_is_not_a_repeat(self):
        mine, other = self.draft("my-masto", "mastodon"), self.draft("my-masto-two", "mastodon")
        self.approve(mine)
        self.approve(other)
        code, out, server, _, _ = self.live_masto("--limit", "5")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(server.forms), 2)

    def test_material_recorded_with_mark_shared_blocks_at_send_time(self):
        pid = self.draft()
        self.approve(pid)
        with connect(self.db) as db:  # recorded after approval, e.g. the owner posted it by hand meanwhile
            record_shared(db, platform="mastodon", account_id="my-masto", content=TEXT, images=[], video=None,
                          links=[], posted_at=None, url="", note="")
        code, out, server, get, _ = self.live_masto("--post-id", pid)
        self.assertEqual(code, 2, out)
        self.assertIn(REPEAT_REFUSAL, out)
        get.assert_not_called()  # before the identity preflight
        self.assertEqual(server.forms, [])

    def test_dry_run_shows_the_repeat_refusal(self):
        first, second, _ = self.two_approved_copies()
        self.assertEqual(self.live_masto("--post-id", first)[0], 0)
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertIn(f"SKIPPED {second}: {REPEAT_REFUSAL}", out)
        self.assertIn("Repeat guard: 1 due post(s) would be SKIPPED", out)

    def test_repeat_override_event_is_written_only_when_the_approval_succeeds(self):
        first = self.draft()
        self.approve(first)
        self.assertEqual(self.live_masto("--post-id", first)[0], 0)
        second = self.draft()
        with connect(self.db) as db:
            latch_identity_mode(db)  # a later approve check refuses (identity required)
        code, _, err = self.cli("approve", second, "--allow-repeat")
        self.assertEqual(code, 2, err)
        with connect(self.db) as db:
            self.assertEqual(events(db, "repeat_override", second), [])
            self.assertEqual(get_post(db, second)["status"], "draft")

    def test_worker_log_reason_is_fixed(self):
        self.assertIn("repeat_of_shared_material", SKIP_REASONS)
        self.assertEqual(claim_refusal_reason(ValidationError(f"{REPEAT_REFUSAL}: post x ...")),
                         "repeat_of_shared_material")
        self.assertEqual(claim_refusal_reason(ValidationError(f"{SEND_OPTIONS_CHANGED}: ...")), "rebind_required")


if __name__ == "__main__":
    unittest.main()
