"""Privacy lifecycle: redaction, structured logs, export and retention purge. Offline only."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hold_support import claim as claim_sending
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.privacy import (
    PURGED, apply_purge, export_data, load_policy, plan_purge, privacy_status,
    redact, structured_log, validate_policy,
)
from reachweaver.storage import (
    add_post, connect, event, finish_send, get_post, store_metrics,
    transition, uncertain_send,
)

TELEGRAM = "123456789" + ":AAH4kq0e2Xz_zQ3dYv8n7Lk1Mm9Pp0Rr5Ss"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvd25lciJ9.c2lnbmF0dXJlLXZhbHVl"
GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PAT = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"
OLD = "2026-01-01T00:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"
RECENT = "2026-10-08T00:00:00+00:00"
POLICY = {"metric_snapshots_days": 30, "event_log_days": 30, "cancelled_drafts_days": 30,
          "published_content_days": 30, "campaign_evidence_days": 30}


class Redaction(unittest.TestCase):
    def test_each_token_form(self):
        cases = {
            "Authorization: Bearer abcdefghijklmnop.qrstu": "abcdefghijklmnop",
            f"jwt {JWT} end": JWT,
            f"https://api.telegram.org/bot{TELEGRAM}/sendMessage": TELEGRAM,
            f"token {TELEGRAM}": TELEGRAM,
            "app password abcd-efgh-ijkl-mnop": "abcd-efgh-ijkl-mnop",
            f"classic {GHP}": GHP,
            f"fine-grained {PAT}": PAT,
            "api_key=s3cr3tvalue&next=1": "s3cr3tvalue",
            'password: "hunter two"': "hunter two",
            "https://user:pa55word@example.org/x": "pa55word",
        }
        for text, secret in cases.items():
            with self.subTest(text=text):
                out = redact(text, env={})
                self.assertNotIn(secret, out)
                self.assertIn("REDACTED", out)
        self.assertIn("&next=1", redact("api_key=s3cr3tvalue&next=1", env={}))

    def test_benign_text_and_email_flag(self):
        text = "Release v1.2 notes: see https://example.org/post?id=42 at 12:30 by owner@example.org"
        self.assertEqual(redact(text, env={}), text)
        self.assertNotIn("owner@example.org", redact(text, emails=True, env={}))
        self.assertIn("uuid 123e4567-e89b-12d3-a456-426614174000",
                      redact("uuid 123e4567-e89b-12d3-a456-426614174000", env={}))

    def test_secret_env_values_redacted_verbatim(self):
        env = {"REACHWEAVER_MAIN_TOKEN": "plainOpaqueValue1", "REACHWEAVER_BSKY_APP_PASSWORD": "Weird!Pass#99",
               "REACHWEAVER_TG_BOT_TOKEN": "zzzBotValue", "REACHWEAVER_X_PASSWORD": "pw-value-x",
               "REACHWEAVER_DB": "state/not-secret.sqlite"}
        out = redact("a plainOpaqueValue1 b Weird!Pass#99 c zzzBotValue d pw-value-x e state/not-secret.sqlite", env=env)
        for name in ("plainOpaqueValue1", "Weird!Pass#99", "zzzBotValue", "pw-value-x"):
            self.assertNotIn(name, out)
        self.assertIn("state/not-secret.sqlite", out)
        self.assertIn("[REDACTED:env:REACHWEAVER_MAIN_TOKEN]", out)

    def test_structured_log_one_json_line_never_contains_env_secret(self):
        stream = io.StringIO()
        with patch.dict(os.environ, {"REACHWEAVER_LOG_TOKEN": "opaque-env-secret-123"}):
            line = structured_log("send_failed", _stream=stream, account="my-bluesky",
                                  detail="upstream said opaque-env-secret-123",
                                  nested={"urls": [f"https://api.telegram.org/bot{TELEGRAM}/getMe"]},
                                  contact="owner@example.org", attempt=2)
        written = stream.getvalue()
        self.assertEqual(written, line + "\n")
        self.assertEqual(len(written.splitlines()), 1)
        record = json.loads(line)
        self.assertEqual(record["event"], "send_failed")
        self.assertTrue(record["ts"].endswith("+00:00"))
        self.assertEqual(record["attempt"], 2)
        for secret in ("opaque-env-secret-123", TELEGRAM, "owner@example.org"):
            self.assertNotIn(secret, written)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        os.chmod(self.temp.name, 0o700)
        self.db = str(Path(self.temp.name) / "state.sqlite")

    def published(self, db, content="Published note"):
        pid = add_post(db, "my-bluesky", "bluesky", content)
        transition(db, pid, "approve")
        self.assertTrue(claim_sending(db, pid, now=OLD))
        finish_send(db, pid, "at://remote/" + pid, "https://bsky.app/post/" + pid)
        return pid

    def seed(self, db):
        """One row of every protected and purgeable kind, aged OLD where relevant."""
        ids = {}
        ids["draft"] = add_post(db, "my-bluesky", "bluesky", "Draft secret plan")
        ids["approved"] = add_post(db, "my-bluesky", "bluesky", "Approved copy")
        transition(db, ids["approved"], "approve")
        ids["scheduled"] = add_post(db, "my-bluesky", "bluesky", "Scheduled copy")
        transition(db, ids["scheduled"], "approve")
        transition(db, ids["scheduled"], "schedule", "2026-12-01T09:00:00+00:00")
        ids["sending"] = add_post(db, "my-bluesky", "bluesky", "Sending copy")
        transition(db, ids["sending"], "approve")
        self.assertTrue(claim_sending(db, ids["sending"], now=OLD))
        ids["review"] = add_post(db, "my-bluesky", "bluesky", "Uncertain copy")
        transition(db, ids["review"], "approve")
        self.assertTrue(claim_sending(db, ids["review"], now=OLD))
        uncertain_send(db, ids["review"], "network_or_timeout_uncertain")
        ids["published"] = self.published(db)
        ids["recent_pub"] = add_post(db, "my-bluesky", "bluesky", "Recently published")
        transition(db, ids["recent_pub"], "approve")
        self.assertTrue(claim_sending(db, ids["recent_pub"], now=RECENT))
        finish_send(db, ids["recent_pub"], "at://remote/recent", None)
        ids["cancelled"] = add_post(db, "my-bluesky", "bluesky", "Cancelled draft")
        transition(db, ids["cancelled"], "cancel")
        ids["cancelled_variant"] = add_post(db, "my-bluesky", "bluesky", "Cancelled staged variant")
        transition(db, ids["cancelled_variant"], "cancel")
        db.execute("""INSERT INTO campaign_variants VALUES(?,?,?,?,?,?,?)""",
                   ("camp", "brand", "v1", "f" * 64, ids["cancelled_variant"],
                    json.dumps([{"id": "e1", "url": "https://example.org", "claim": "private roadmap claim"}]), OLD))
        db.execute("""INSERT INTO campaign_variants VALUES(?,?,?,?,?,?,?)""",
                   ("camp", "brand", "v2", "e" * 64, ids["draft"],
                    json.dumps([{"id": "e2", "claim": "draft evidence"}]), OLD))
        store_metrics(db, ids["published"], OLD, "native-export", views=10)
        store_metrics(db, ids["published"], RECENT, "native-export", views=20)
        # Age everything created "now" into the past so only hard rules protect rows.
        db.execute("UPDATE posts SET created_at=?", (OLD,))
        db.execute("UPDATE event_log SET at=? WHERE action<>'send_claimed'", (OLD,))
        event(db, "metrics_recorded", ids["published"], "fresh", at=RECENT)
        return ids

    def snapshot(self, db):
        return {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ("posts", "event_log", "campaign_variants", "metric_snapshots", "settings")}

    def test_export_contents_redacted_and_private_file(self):
        with connect(self.db) as db:
            ids = self.seed(db)
            pid = add_post(db, "my-bluesky", "bluesky", f"leak check {GHP}")
            data = export_data(db, now=NOW)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["exported_at"], NOW)
        self.assertEqual(set(data["tables"]),
                         {"posts", "events", "campaign_variants", "metric_snapshots", "settings"})
        self.assertEqual(data["counts"]["posts"], len(ids) + 1)
        self.assertEqual(data["counts"]["campaign_variants"], 2)
        self.assertEqual(data["counts"]["metric_snapshots"], 2)
        dumped = json.dumps(data)
        self.assertNotIn(GHP, dumped)
        self.assertIn("Draft secret plan", dumped)
        self.assertTrue(any(p["id"] == pid for p in data["tables"]["posts"]))
        out = Path(self.temp.name) / "export.json"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(main(["--db", self.db, "data-export", "--out", str(out)]), 0)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        self.assertEqual(json.loads(out.read_text())["format"], "reachweaver-local-export")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", self.db, "data-export", "--out", str(out)]), 2)  # never overwrite

    def test_dry_run_plan_and_cli_make_no_changes(self):
        policy_file = Path(self.temp.name) / "retention.json"
        policy_file.write_text(json.dumps({"retention_days": POLICY}))
        with connect(self.db) as db:
            self.seed(db)
            before = self.snapshot(db)
            plan = plan_purge(db, POLICY, now=NOW)
            self.assertTrue(plan["dry_run"])
            self.assertGreater(sum(plan["counts"].values()), 0)
            self.assertEqual(self.snapshot(db), before)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--db", self.db, "data-purge", "--policy", str(policy_file)]), 0)
        self.assertTrue(json.loads(out.getvalue())["dry_run"])
        with connect(self.db) as db:
            self.assertEqual(self.snapshot(db), before)

    def test_apply_respects_every_hard_safety_rule(self):
        with connect(self.db) as db:
            ids = self.seed(db)
            protected_before = {k: dict(get_post(db, ids[k]))
                                for k in ("draft", "approved", "scheduled", "sending", "review")}
            # Recent send_claimed within 48h for a published post (budget window).
            claims_recent = db.execute("SELECT id FROM event_log WHERE action='send_claimed' AND at=?",
                                       (RECENT,)).fetchall()
            self.assertEqual(len(claims_recent), 1)
            plan = plan_purge(db, POLICY, now=NOW)
            # A tampered plan cannot touch protected rows either.
            plan["actions"]["published_content_redact"] += [ids["draft"], ids["sending"]]
            plan["actions"]["cancelled_posts_delete"] += [ids["cancelled_variant"], ids["review"]]
            plan["actions"]["cancelled_posts_minimize"] += [ids["approved"]]
            plan["actions"]["event_log_delete"] += [r["id"] for r in db.execute("SELECT id FROM event_log")]
            plan["actions"]["campaign_evidence_minimize"].append(["camp", "v2"])
            result = apply_purge(db, plan, now=NOW)

            for key, before in protected_before.items():
                self.assertEqual(dict(get_post(db, ids[key])), before, key)
            pub = get_post(db, ids["published"])
            self.assertEqual(pub["content"], PURGED)
            for field in ("id", "account_id", "remote_id", "remote_url", "status", "created_at", "send_started_at"):
                self.assertIsNotNone(pub[field], field)
            self.assertEqual(pub["status"], "published")
            self.assertEqual(get_post(db, ids["recent_pub"])["content"], "Recently published")
            with self.assertRaises(ValidationError):
                get_post(db, ids["cancelled"])
            minimized = get_post(db, ids["cancelled_variant"])
            self.assertEqual(minimized["content"], PURGED)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM campaign_variants").fetchone()[0], 2)
            v1 = db.execute("SELECT * FROM campaign_variants WHERE variant_id='v1'").fetchone()
            self.assertEqual(v1["fingerprint"], "f" * 64)
            self.assertNotIn("private roadmap claim", v1["evidence_json"])
            v2 = db.execute("SELECT * FROM campaign_variants WHERE variant_id='v2'").fetchone()
            self.assertIn("draft evidence", v2["evidence_json"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed' AND at=?",
                                        (RECENT,)).fetchone()[0], 1)
            for key in ("sending", "review"):
                self.assertGreater(db.execute("SELECT COUNT(*) FROM event_log WHERE post_id=?",
                                              (ids[key],)).fetchone()[0], 0, key)
            snapshots = [r["observed_at"] for r in db.execute("SELECT observed_at FROM metric_snapshots")]
            self.assertEqual(snapshots, [RECENT])
            self.assertEqual(result["counts"]["cancelled_posts_delete"], 1)
            self.assertEqual(result["counts"]["published_content_redact"], 1)

            purge = db.execute("SELECT * FROM event_log WHERE action='privacy_purge'").fetchall()
            self.assertEqual(len(purge), 1)
            self.assertEqual(json.loads(purge[0]["detail"]), result["counts"])
            self.assertIsNone(purge[0]["post_id"])
            # Idempotent: a second plan finds nothing more to do for content.
            again = plan_purge(db, POLICY, now=NOW)
            self.assertEqual(again["counts"]["published_content_redact"], 0)
            self.assertEqual(again["counts"]["cancelled_posts_delete"], 0)

    def test_null_policy_keeps_everything_and_validation(self):
        with connect(self.db) as db:
            self.seed(db)
            plan = plan_purge(db, {}, now=NOW)
            self.assertEqual(sum(plan["counts"].values()), 0)
        for bad in ({"event_log_days": 0}, {"event_log_days": "30"}, {"unknown_days": 3}, []):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_policy(bad)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REACHWEAVER_RETENTION_POLICY", None)
            with patch("reachweaver.privacy.DEFAULT_POLICY_PATH", Path(self.temp.name) / "missing.json"):
                with self.assertRaisesRegex(ValidationError, "No retention policy"):
                    load_policy(None)

    def test_privacy_status_reports_permissions_counts_and_policy(self):
        policy_file = Path(self.temp.name) / "retention.json"
        policy_file.write_text(json.dumps(POLICY))
        with patch.dict(os.environ, {"REACHWEAVER_STATUS_TOKEN": "do-not-print-me"}):
            with connect(self.db) as db:
                self.seed(db)
                status = privacy_status(db, policy_path=policy_file)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--db", self.db, "privacy-status", "--policy", str(policy_file)]), 0)
        self.assertEqual(status["database"]["file_mode"], "0600")
        self.assertEqual(status["database"]["directory_mode"], "0700")
        self.assertTrue(status["database"]["file_private"])
        self.assertGreater(status["database"]["size_bytes"], 0)
        self.assertEqual(status["row_counts"]["campaign_variants"], 2)
        self.assertEqual(status["oldest_records"]["posts_created_at"], OLD)
        self.assertTrue(status["retention_policy"]["configured"])
        self.assertIn("REACHWEAVER_STATUS_TOKEN", status["secret_env_names_present"])
        printed = out.getvalue()
        self.assertNotIn("do-not-print-me", printed)
        self.assertIn("live_safety", json.loads(printed))


class IdeasInbox(unittest.TestCase):
    """The ideas inbox (state/ideas.json next to the database) in export, purge and status."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        os.chmod(self.temp.name, 0o700)
        self.db = str(Path(self.temp.name) / "state.sqlite")
        self.inbox = Path(self.temp.name) / "ideas.json"

    def seed_ideas(self):
        """open (old), dropped (old), promoted (old), dropped (recent); returns their ids by name."""
        from reachweaver import ideas
        names = ("open_old", "dropped_old", "promoted_old", "dropped_recent")
        ids = {name: ideas.add(self.inbox, f"Idea {name.replace('_', ' ')}")["id"] for name in names}
        data = json.loads(self.inbox.read_text(encoding="utf-8"))
        aged = {"open_old": ("open", OLD, None), "dropped_old": ("dropped", OLD, OLD),
                "promoted_old": ("promoted", OLD, OLD), "dropped_recent": ("dropped", OLD, RECENT)}
        for item in data["ideas"]:
            name = next(n for n, i in ids.items() if i == item["id"])
            item["status"], item["created_at"], item["updated_at"] = aged[name]
        self.inbox.write_text(json.dumps(data), encoding="utf-8")
        return ids

    def remaining(self):
        return {item["id"] for item in json.loads(self.inbox.read_text(encoding="utf-8"))["ideas"]}

    def test_export_includes_the_inbox_with_the_same_redaction(self):
        with connect(self.db) as db:
            absent = export_data(db, now=NOW)["ideas_inbox"]
            self.assertEqual((absent["state"], absent["count"], absent["ideas"]), ("absent", 0, []))
            ids = self.seed_ideas()
            with patch.dict(os.environ, {"REACHWEAVER_IDEA_TOKEN": "Idea open"}):  # an env secret value is stripped
                data = export_data(db, now=NOW)
        inbox = data["ideas_inbox"]
        self.assertEqual((inbox["state"], inbox["count"], inbox["path"]), ("present", 4, str(self.inbox.resolve())))
        self.assertEqual({i["id"] for i in inbox["ideas"]}, set(ids.values()))
        self.assertIn("[REDACTED:env:REACHWEAVER_IDEA_TOKEN]", json.dumps(inbox))
        self.assertEqual(set(data["tables"]), {"posts", "events", "campaign_variants", "metric_snapshots", "settings"})
        out = Path(self.temp.name) / "export.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", self.db, "data-export", "--out", str(out)]), 0)
        self.assertIn("Idea dropped old", out.read_text(encoding="utf-8"))

    def test_a_damaged_inbox_is_reported_not_fatal(self):
        self.inbox.write_text("{not json", encoding="utf-8")
        os.chmod(self.inbox, 0o600)
        with connect(self.db) as db:
            exported = export_data(db, now=NOW)["ideas_inbox"]
            plan = plan_purge(db, {**POLICY, "closed_ideas_days": 30}, now=NOW)
            status = privacy_status(db)["ideas_inbox"]
        self.assertEqual((exported["state"], exported["ideas"]), ("unreadable", []))
        self.assertIn("not valid UTF-8 JSON", exported["error"])
        self.assertEqual((plan["ideas_inbox"], plan["counts"]["closed_ideas_delete"]), ("unreadable", 0))
        self.assertEqual(status["state"], "unreadable")
        self.assertEqual(self.inbox.read_text(encoding="utf-8"), "{not json")

    def test_policy_without_the_key_keeps_every_idea_and_plans_the_same_rows(self):
        with connect(self.db) as db:
            Lifecycle.seed(self, db)  # same rows as the lifecycle tests
            before = plan_purge(db, POLICY, now=NOW)
            self.seed_ideas()
            after = plan_purge(db, POLICY, now=NOW)
        self.assertEqual(after["actions"]["closed_ideas_delete"], [])
        for key in ACTION_KEYS_WITHOUT_IDEAS:
            self.assertEqual(after["actions"][key], before["actions"][key], key)
        self.assertEqual(after["protected"]["open_ideas_never_touched"], 1)
        self.assertIsNone(after["policy"]["closed_ideas_days"])

    def published(self, db, content="Published note"):  # used by Lifecycle.seed
        return Lifecycle.published(self, db, content)

    def test_dry_run_lists_closed_old_ideas_and_changes_nothing(self):
        ids = self.seed_ideas()
        before = self.inbox.read_bytes()
        policy_file = Path(self.temp.name) / "retention.json"
        policy_file.write_text(json.dumps({"retention_days": {"closed_ideas_days": 30}}))
        with connect(self.db) as db:
            plan = plan_purge(db, {"closed_ideas_days": 30}, now=NOW)
        self.assertEqual(sorted(plan["actions"]["closed_ideas_delete"]), sorted([ids["dropped_old"], ids["promoted_old"]]))
        self.assertEqual(plan["protected"]["open_ideas_never_touched"], 1)
        self.assertEqual(plan["ideas_inbox"], "present")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--db", self.db, "data-purge", "--policy", str(policy_file)]), 0)
        printed = json.loads(out.getvalue())
        self.assertTrue(printed["dry_run"])
        self.assertEqual(printed["counts"]["closed_ideas_delete"], 2)
        self.assertEqual(self.inbox.read_bytes(), before)

    def test_apply_deletes_only_closed_old_ideas_and_never_an_open_one(self):
        ids = self.seed_ideas()
        with connect(self.db) as db:
            plan = plan_purge(db, {"closed_ideas_days": 30}, now=NOW)
            plan["actions"]["closed_ideas_delete"].append(ids["open_old"])  # a tampered plan
            result = apply_purge(db, plan, now=NOW)
            detail = db.execute("SELECT detail FROM event_log WHERE action='privacy_purge'").fetchone()["detail"]
        self.assertEqual(result["counts"]["closed_ideas_delete"], 2)
        self.assertEqual(json.loads(detail)["closed_ideas_delete"], 2)
        self.assertEqual(self.remaining(), {ids["open_old"], ids["dropped_recent"]})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.inbox.stat().st_mode), 0o600)
        policy_file = Path(self.temp.name) / "retention.json"
        policy_file.write_text(json.dumps({"retention_days": {"closed_ideas_days": 30}}))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--db", self.db, "data-purge", "--policy", str(policy_file), "--apply"]), 0)
        self.assertEqual(json.loads(out.getvalue())["counts"]["closed_ideas_delete"], 0)  # idempotent

    def test_apply_without_an_inbox_creates_none(self):
        with connect(self.db) as db:
            result = apply_purge(db, {"actions": {"closed_ideas_delete": ["idea-00000000"]}}, now=NOW)
        self.assertEqual(result["counts"]["closed_ideas_delete"], 0)
        self.assertFalse(self.inbox.exists())

    def test_status_reports_permissions_and_counts_never_text(self):
        with connect(self.db) as db:
            self.assertEqual(privacy_status(db)["ideas_inbox"]["state"], "absent")
            self.seed_ideas()
            status = privacy_status(db)["ideas_inbox"]
        self.assertEqual(status["counts"], {"open": 1, "promoted": 1, "dropped": 2})
        self.assertEqual(status["oldest_created_at"], OLD)
        self.assertGreater(status["size_bytes"], 0)
        if os.name == "posix":
            self.assertEqual((status["file_mode"], status["file_private"]), ("0600", True))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--db", self.db, "privacy-status"]), 0)
        printed = out.getvalue()
        self.assertEqual(json.loads(printed)["ideas_inbox"]["counts"]["dropped"], 2)
        self.assertNotIn("Idea open old", printed)


ACTION_KEYS_WITHOUT_IDEAS = ("metric_snapshots_delete", "event_log_delete", "cancelled_posts_delete",
                             "cancelled_posts_minimize", "published_content_redact", "campaign_evidence_minimize")


if __name__ == "__main__":
    unittest.main()
