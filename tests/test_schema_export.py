"""Read-only legacy outbox -> canonical schema 1.1 export (issue #4); offline only."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hold_support import claim
from reachweaver import storage
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.schema_export import EXPORT_TYPES, export_documents, render_jsonl
from reachweaver.schemas import SCHEMA_VERSION, STORAGE_STATUS_MAP, payload_sha256, transition_job, validate

ROOT = Path(__file__).resolve().parents[1]
FAKE_APP_PASSWORD = "wxyz-2468-qrst-1357"  # fake, shaped like a Bluesky app password
CONSENT = {"subject": "account_owner", "obtained": True, "recorded_at": "2026-10-01T09:00:00Z"}
EVIDENCE = [{"id": "source-repo", "url": "https://github.com/MSKazemi/reachweaver",
             "claim": "ReachWeaver has a local-first CLI.", "verification": "primary_source_checked",
             "checked_at": "2026-10-08T15:00:00+02:00", "kind": "repository"}]
ACCOUNTS = {"accounts": [
    {"id": "my-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_BSKY",
     "handle": "example.bsky.social", "brand_id": "reachweaver", "display_name": "Example",
     "consent": [CONSENT]},
    {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_MASTODON",
     "server": "https://mastodon.social", "handle": "example"},
]}


def _stage(con, post_id, variant_id, campaign="intro", evidence=None):
    con.execute("INSERT INTO campaign_variants(campaign_id,brand_id,variant_id,fingerprint,post_id,evidence_json,"
                "recorded_at) VALUES(?,?,?,?,?,?,?)",
                (campaign, "reachweaver", variant_id, "f" * 64, post_id,
                 json.dumps(EVIDENCE if evidence is None else evidence), "2026-10-08T10:00:00+00:00"))


def seed(con) -> dict[str, str]:
    """One staged post per storage status, plus the edge cases that must stay unmapped."""
    ids: dict[str, str] = {}

    def post(name, platform="bluesky", account="my-bluesky", text=None, staged=True, evidence=None):
        pid = storage.add_post(con, account, platform, text or f"ReachWeaver test post {name}.", "intro")
        if staged:
            _stage(con, pid, name, evidence=evidence)
        ids[name] = pid
        return pid

    post("draft")
    storage.transition(con, post("approved"), "approve")
    pid = post("scheduled")
    storage.transition(con, pid, "approve")
    storage.transition(con, pid, "schedule", "2026-10-15T07:00:00+00:00")
    pid = post("sending")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    pid = post("published")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    storage.finish_send(con, pid, "at://did:plc:abc/app.bsky.feed.post/3kpub",
                        "https://bsky.app/profile/did:plc:abc/post/3kpub")
    storage.store_metrics(con, pid, "2026-10-16T08:00:00+00:00", "native-export", views=120, clicks=4)
    pid = post("reconciled", platform="mastodon", account="my-mastodon")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    storage.uncertain_send(con, pid, "network_or_timeout_uncertain")
    storage.reconcile(con, pid, True, "109876543210", "https://mastodon.social/@example/109876543210")
    pid = post("needs-review")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    storage.uncertain_send(con, pid, "network_or_timeout_uncertain")
    pid = post("redrafted")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    storage.uncertain_send(con, pid, "network_or_timeout_uncertain")
    storage.reconcile(con, pid, False)
    storage.transition(con, post("cancelled"), "cancel")
    # Edge cases that must be reported, never guessed.
    pid = post("free-text-error")
    storage.transition(con, pid, "approve")
    claim(con, pid)
    storage.uncertain_send(con, pid, "Something odd happened")
    pid = post("unstaged", staged=False)
    storage.store_metrics(con, pid, "2026-10-16T08:00:00+00:00", "native-export", views=1)
    post("secret", text=f"Do not leak {FAKE_APP_PASSWORD} please.")
    post("minimized", evidence=[{"id": "source-repo", "url": "https://github.com/MSKazemi/reachweaver"}])
    return ids


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SchemaExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.db = base / "state" / "reachweaver.sqlite"
        self.accounts = base / "accounts.json"
        self.accounts.write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        with storage.connect(str(self.db)) as con:
            self.ids = seed(con)
        self.env = patch.dict(os.environ, {"REACHWEAVER_BSKY_APP_PASSWORD": FAKE_APP_PASSWORD}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.accounts), *argv])
        return code, out.getvalue(), err.getvalue()

    def export(self):
        code, out, err = self.run_cli("schema-export")
        self.assertEqual(code, 0, err)
        docs = [json.loads(line) for line in out.splitlines()]
        return docs, json.loads(err), out + err

    def by_source(self, unmapped, name):
        return [u for u in unmapped if u["source"] == f"posts:{self.ids[name]}"]

    def test_every_exported_document_validates_and_every_status_is_covered(self):
        docs, summary, _ = self.export()
        self.assertTrue(docs)
        for doc in docs:
            self.assertEqual(validate(doc["type"], doc), [], doc["id"])
            self.assertEqual(doc["schema_version"], SCHEMA_VERSION)
        jobs = {doc["idempotency_key"]: doc for doc in docs if doc["type"] == "PublishJob"}
        expected = {"draft": "draft", "approved": "approved", "scheduled": "scheduled", "sending": "publishing",
                    "published": "published", "reconciled": "published", "needs-review": "unknown",
                    "redrafted": "draft", "cancelled": "cancelled"}
        for name, state in expected.items():
            self.assertEqual(jobs[self.ids[name]]["state"], state, name)
        self.assertEqual({j["state"] for j in jobs.values()}, set(STORAGE_STATUS_MAP.values()))
        for job in jobs.values():
            self.assertEqual(job["payload_sha256"], payload_sha256(job["payload"]))
            self.assertEqual(job["payload"]["locale"], "und")
        self.assertEqual(jobs[self.ids["sending"]]["attempts"], 1)
        self.assertEqual(jobs[self.ids["redrafted"]]["attempts"], 1)  # surviving send_claimed events
        self.assertNotIn("approval_id", jobs[self.ids["redrafted"]])
        self.assertEqual(jobs[self.ids["needs-review"]]["error_code"], "network_or_timeout_uncertain")
        self.assertEqual(jobs[self.ids["scheduled"]]["scheduled_at"], "2026-10-15T07:00:00Z")
        receipts = {doc["idempotency_key"]: doc for doc in docs if doc["type"] == "PublishReceipt"}
        self.assertEqual(set(receipts), {self.ids["published"], self.ids["reconciled"]})
        self.assertEqual(receipts[self.ids["published"]]["evidence"], "api_response")
        self.assertEqual(receipts[self.ids["reconciled"]]["evidence"], "owner_verified_native_account")
        snapshots = [doc for doc in docs if doc["type"] == "AnalyticsSnapshot"]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["metrics"]["views"], 120)
        self.assertIsNone(snapshots[0]["metrics"]["likes"])  # missing stays unknown, never zero
        evidence = [doc for doc in docs if doc["type"] == "Evidence"]
        self.assertEqual([e["id"] for e in evidence], ["intro:source-repo"])
        self.assertEqual(evidence[0]["checked_at"], "2026-10-08T13:00:00Z")
        self.assertEqual(evidence[0]["checked_by"], "unknown")
        accounts = [doc for doc in docs if doc["type"] == "Account"]
        self.assertEqual([a["id"] for a in accounts], ["my-bluesky"])
        self.assertEqual(summary["counts"]["PublishJob"], len(jobs))
        self.assertEqual(summary["counts"]["Approval"], 0)
        self.assertEqual(summary["counts"]["Draft"], 0)

    def test_unmapped_rows_carry_reasons_and_no_approval_is_minted(self):
        docs, summary, _ = self.export()
        unmapped = summary["unmapped"]
        self.assertTrue(all(u["reason"] and u["type"] in EXPORT_TYPES for u in unmapped))
        self.assertFalse([d for d in docs if d["type"] in ("Approval", "Draft")])
        approvals = {u["id"] for u in unmapped if u["type"] == "Approval"}
        for doc in docs:
            if doc["type"] == "PublishJob" and "approval_id" in doc:
                self.assertIn(doc["approval_id"], approvals)  # references resolve to an explained gap
        self.assertTrue(all("approved_payload_sha256" in u["reason"] for u in unmapped if u["type"] == "Approval"))
        drafts = [u for u in unmapped if u["type"] == "Draft"]
        self.assertEqual(len(drafts), len(self.ids))
        self.assertIn("authorship", drafts[0]["reason"])
        reasons = {u["type"]: u["reason"] for u in self.by_source(unmapped, "unstaged")}
        self.assertIn("brand_id is not recorded", reasons["PlatformVariant"])
        self.assertIn("brand_id is not recorded", reasons["PublishJob"])
        job = [u for u in self.by_source(unmapped, "free-text-error") if u["type"] == "PublishJob"]
        self.assertIn("error_code", job[0]["reason"])
        secret = {u["type"]: u["reason"] for u in self.by_source(unmapped, "secret")}
        self.assertIn("secret-looking", secret["PlatformVariant"])
        snapshot = [u for u in unmapped if u["type"] == "AnalyticsSnapshot"]
        self.assertEqual(len(snapshot), 1)
        self.assertIn("no recorded remote post id", snapshot[0]["reason"])
        evidence = [u for u in unmapped if u["type"] == "Evidence"]
        self.assertTrue(any("conflicting content" in u["reason"] for u in evidence))
        account = [u for u in unmapped if u["type"] == "Account"]
        self.assertEqual([u["id"] for u in account], ["my-mastodon"])
        self.assertIn("consent", account[0]["reason"])

    def test_snapshot_window_is_exported_when_storage_has_one(self):
        pid = self.ids["published"]
        with storage.connect(str(self.db)) as con:
            storage.store_metrics(con, pid, "2026-10-17T10:00:00+02:00", "native-export", likes=3,
                                  window_start="2026-10-09T02:00:00+02:00", window_end="2026-10-16T00:00:00Z")
            # A hand-edited row with only one bound must be reported, never completed by guessing.
            con.execute("INSERT INTO metric_snapshots(post_id,observed_at,source,likes,window_start) "
                        "VALUES(?,?,?,?,?)", (pid, "2026-10-18T08:00:00+00:00", "native-export", 5,
                                              "2026-10-11T00:00:00+00:00"))
        docs, summary, _ = self.export()
        snapshots = {d["observed_at"]: d for d in docs if d["type"] == "AnalyticsSnapshot"}
        self.assertEqual(set(snapshots), {"2026-10-16T08:00:00Z", "2026-10-17T08:00:00Z"})
        windowed = snapshots["2026-10-17T08:00:00Z"]
        self.assertEqual((windowed["window_start"], windowed["window_end"]),
                         ("2026-10-09T00:00:00Z", "2026-10-16T00:00:00Z"))  # normalized to UTC Z
        self.assertEqual(windowed["schema_version"], "1.2")
        self.assertEqual(validate("AnalyticsSnapshot", windowed), [])
        unknown = snapshots["2026-10-16T08:00:00Z"]
        self.assertNotIn("window_start", unknown)  # NULL window = unknown, left out, never invented
        self.assertNotIn("window_end", unknown)
        half = [u for u in summary["unmapped"] if u["type"] == "AnalyticsSnapshot" and "window" in u["reason"]]
        self.assertEqual(len(half), 1)
        self.assertIn("both window_start and window_end", half[0]["reason"])

    def test_snapshot_metric_status_is_exported_as_schema_1_2(self):
        pid = self.ids["published"]
        with storage.connect(str(self.db)) as con:
            storage.store_metrics(con, pid, "2026-10-17T08:00:00+00:00", "native-export", likes=3,
                                  views="suppressed", impressions="estimated:900", clicks="delayed")
            # A hand-edited, malformed status column is reported, never read as unknown or zero.
            con.execute("INSERT INTO metric_snapshots(post_id,observed_at,source,likes,metric_status) "
                        "VALUES(?,?,?,?,?)", (pid, "2026-10-18T08:00:00+00:00", "native-export", 5, "{not json"))
        docs, summary, _ = self.export()
        snapshots = {d["observed_at"]: d for d in docs if d["type"] == "AnalyticsSnapshot"}
        status = snapshots["2026-10-17T08:00:00Z"]
        self.assertEqual(status["metric_status"], {"views": {"status": "suppressed"},
                                                   "impressions": {"status": "estimated", "value": 900},
                                                   "clicks": {"status": "delayed"}})
        self.assertIsNone(status["metrics"]["views"])  # never coerced to 0
        self.assertIsNone(status["metrics"]["impressions"])  # an estimate is not an exact count
        self.assertEqual(validate("AnalyticsSnapshot", status), [])
        self.assertNotIn("metric_status", snapshots["2026-10-16T08:00:00Z"])  # no statuses -> field absent
        self.assertNotIn("2026-10-18T08:00:00Z", snapshots)
        bad = [u for u in summary["unmapped"] if u["type"] == "AnalyticsSnapshot" and "metric_status" in u["reason"]]
        self.assertEqual(len(bad), 1)

    def test_exported_jobs_cannot_be_driven_to_publishing(self):
        docs, _, _ = self.export()
        approved = next(d for d in docs if d["type"] == "PublishJob" and d["state"] == "approved")
        with self.assertRaisesRegex(ValidationError, "not approved"):
            transition_job(approved, "publishing", now="2026-10-15T08:00:00Z")

    def test_no_secrets_in_any_output(self):
        out_file = Path(self.temp.name) / "export.jsonl"
        code, stdout, stderr = self.run_cli("schema-export", "--out", str(out_file))
        self.assertEqual(code, 0, stderr)
        _, _, streamed = self.export()
        for text in (stdout, stderr, out_file.read_text(encoding="utf-8"), streamed):
            self.assertNotIn(FAKE_APP_PASSWORD, text)
            for key in ("token", "password", "secret", "cookie", "api_key"):
                self.assertNotIn(f'"{key}"', text.lower())

    def test_export_never_mutates_the_database(self):
        # data_version changes for this watcher connection only if ANOTHER connection commits.
        watcher = sqlite3.connect(str(self.db))
        self.addCleanup(watcher.close)
        version, digest = watcher.execute("PRAGMA data_version").fetchone()[0], file_hash(self.db)
        self.assertEqual(self.run_cli("schema-export")[0], 0)
        self.assertEqual(self.run_cli("schema-export", "--type", "PublishJob")[0], 0)
        self.assertEqual(self.run_cli("schema-export", "--out", str(Path(self.temp.name) / "a.jsonl"))[0], 0)
        self.assertEqual(watcher.execute("PRAGMA data_version").fetchone()[0], version)
        self.assertEqual(file_hash(self.db), digest)
        with storage.connect(str(self.db)) as con:
            con.execute("INSERT INTO settings(name,value) VALUES('probe','1')")  # control: a write IS seen
        self.assertNotEqual(watcher.execute("PRAGMA data_version").fetchone()[0], version)
        with storage.connect(str(self.db)) as con:
            with self.assertRaises(sqlite3.OperationalError):  # query_only is active during the export
                with patch("reachweaver.schema_export._export_snapshots",
                           side_effect=lambda out, con, *a: con.execute("DELETE FROM metric_snapshots")):
                    export_documents(con, None)
            self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0], 0)  # restored afterwards
            self.assertEqual(con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0], 2)

    def test_out_file_is_new_private_and_never_overwritten(self):
        out_file = Path(self.temp.name) / "export.jsonl"
        code, stdout, stderr = self.run_cli("schema-export", "--out", str(out_file))
        self.assertEqual(code, 0, stderr)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(out_file.stat().st_mode), 0o600)
        lines = out_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), sum(json.loads(stdout)["counts"].values()))
        self.assertTrue(all(validate(json.loads(line)["type"], json.loads(line)) == [] for line in lines))
        out_file.write_text("owner notes\n", encoding="utf-8")
        code, _, stderr = self.run_cli("schema-export", "--out", str(out_file))
        self.assertEqual(code, 2)
        self.assertIn("never overwrites", stderr)
        self.assertEqual(out_file.read_text(encoding="utf-8"), "owner notes\n")
        if os.name == "posix":
            link = Path(self.temp.name) / "link.jsonl"
            link.symlink_to(Path(self.temp.name) / "missing-target.jsonl")
            self.assertEqual(self.run_cli("schema-export", "--out", str(link))[0], 2)
            self.assertFalse((Path(self.temp.name) / "missing-target.jsonl").exists())

    def test_type_filter_and_stdout_jsonl(self):
        code, stdout, stderr = self.run_cli("schema-export", "--type", "PublishReceipt")
        self.assertEqual(code, 0, stderr)
        docs = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual({d["type"] for d in docs}, {"PublishReceipt"})
        summary = json.loads(stderr)
        self.assertEqual(set(summary["counts"]), {"PublishReceipt"})
        self.assertTrue(all(u["type"] == "PublishReceipt" for u in summary["unmapped"]))
        code, stdout, stderr = self.run_cli("schema-export", "--type", "Approval")
        self.assertEqual((code, stdout), (0, ""))
        self.assertTrue(json.loads(stderr)["unmapped"])
        with storage.connect(str(self.db)) as con, self.assertRaises(ValidationError):
            export_documents(con, None, types=["Tweet"])

    def test_missing_accounts_config_is_reported_not_fatal(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(Path(self.temp.name) / "none.json"),
                         "schema-export", "--type", "Account"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")
        unmapped = json.loads(err.getvalue())["unmapped"]
        self.assertEqual(len(unmapped), 1)
        self.assertIn("accounts config not loaded", unmapped[0]["reason"])

    def test_render_jsonl_is_canonical_one_document_per_line(self):
        with storage.connect(str(self.db)) as con:
            docs = export_documents(con, None)["documents"]
        text = render_jsonl(docs)
        self.assertEqual(len(text.splitlines()), len(docs))
        self.assertNotIn(": ", text.splitlines()[0])


VENDOR_MODULES = {"publishers", "postiz", "linkedin", "connections", "richtext", "manual_export"}
WRITE_ENDPOINTS = ("com.atproto.repo.createRecord", "/api/v1/statuses", "/sendMessage", "/rest/posts",
                   '"POST", "/posts"')


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1])
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


class ConnectorReplaceabilityTests(unittest.TestCase):
    """ADR-0003 fitness functions: vendor code stays behind the connector boundary."""

    def test_canonical_contract_modules_import_no_vendor_code(self):
        for name in ("schemas.py", "schema_export.py"):
            self.assertFalse(_imports(ROOT / "reachweaver" / name) & VENDOR_MODULES, name)

    def test_only_connector_modules_hold_publishing_write_endpoints(self):
        holders = {path.stem for path in (ROOT / "reachweaver").glob("*.py")
                   if any(marker in path.read_text(encoding="utf-8") for marker in WRITE_ENDPOINTS)}
        # metrics_fetch reads GET /api/v1/statuses/:id (issue #15) and engagement_fetch reads
        # GET /api/v1/statuses/:id[/context] (issue #17); each is allowed only while it stays
        # read-only: no POST helper, no write method and no other write endpoint. replies (issue #17
        # draft) verifies a reply parent with the same unauthenticated GETs; the reply write itself
        # stays in publishers.
        readers = {"metrics_fetch", "engagement_fetch", "replies"}
        self.assertEqual(holders - readers, {"publishers", "postiz", "linkedin"})
        for name in sorted(readers):
            reader = (ROOT / "reachweaver" / f"{name}.py").read_text(encoding="utf-8")
            for forbidden in ("request_json(", "request_json_with_headers", '"POST"', "'POST'", "method=",
                              *(m for m in WRITE_ENDPOINTS if m != "/api/v1/statuses")):
                with self.subTest(module=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, reader)

    @unittest.skipUnless((ROOT / "docs/ADR-0003-CANONICAL-CONTRACTS.md").exists(), "private-only design record")
    def test_adr_exists_and_names_the_boundary(self):
        text = (ROOT / "docs/ADR-0003-CANONICAL-CONTRACTS.md").read_text(encoding="utf-8")
        for needle in ("platform_options", "publishers.py", "postiz.py", "linkedin.py", "schema-export",
                       "Status:"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
