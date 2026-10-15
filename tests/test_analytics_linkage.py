"""An exported analytics snapshot is linked to its account, platform post id, campaign, window and source (public issue #45).

Every link is asserted against the storage rows it came from, so the export cannot attach a number to the wrong
post, account or campaign, and cannot invent a link (a non-slug campaign or an unknown window is left out).
Offline: temp SQLite, read-only ``schema-export``.
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

from hold_support import claim
from reachweaver import storage
from reachweaver.cli import main
from reachweaver.schemas import validate
from tests.test_schema_export import ACCOUNTS, FAKE_APP_PASSWORD, seed

REMOTE = "at://did:plc:abc/app.bsky.feed.post/3kpub"


class LinkageFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.db = base / "state" / "reachweaver.sqlite"
        self.accounts = base / "accounts.json"
        self.accounts.write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        with storage.connect(str(self.db)) as con:
            self.ids = seed(con)
        env = patch.dict(os.environ, {"REACHWEAVER_BSKY_APP_PASSWORD": FAKE_APP_PASSWORD})
        env.start()
        self.addCleanup(env.stop)

    def export(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.accounts), "schema-export"])
        self.assertEqual(code, 0, err.getvalue())
        return [json.loads(line) for line in out.getvalue().splitlines()], json.loads(err.getvalue())

    def published_post(self):
        with storage.connect(str(self.db)) as con:
            return storage.get_post(con, self.ids["published"])


class SnapshotLinkageTests(LinkageFixture):
    def test_each_snapshot_carries_the_post_account_platform_remote_id_and_source_of_its_rows(self):
        with storage.connect(str(self.db)) as con:
            storage.store_metrics(con, self.ids["published"], "2026-10-17T10:00:00+02:00", "linkedin-analytics",
                                  likes=3, window_start="2026-10-09T02:00:00+02:00", window_end="2026-10-16T00:00:00Z")
            rows = [dict(r) for r in con.execute("SELECT * FROM metric_snapshots WHERE post_id=? ORDER BY id",
                                                 (self.ids["published"],))]
        post = self.published_post()
        docs, _ = self.export()
        snapshots = [d for d in docs if d["type"] == "AnalyticsSnapshot"]
        self.assertEqual(len(snapshots), len(rows))
        self.assertEqual(len(rows), 2)
        for doc, row in zip(sorted(snapshots, key=lambda d: d["observed_at"]), rows):
            self.assertEqual(validate("AnalyticsSnapshot", doc), [])
            self.assertEqual((doc["account_id"], doc["platform"], doc["remote_post_id"]),
                             (post["account_id"], post["platform"], post["remote_id"]))
            self.assertEqual(doc["remote_post_id"], REMOTE)
            self.assertEqual(doc["source"], row["source"])
            self.assertEqual(doc["campaign_id"], "intro")  # the staged campaign, not a guess
            for key in ("views", "clicks", "likes"):
                self.assertEqual(doc["metrics"][key], row[key], key)

    def test_the_snapshot_points_at_the_same_job_and_receipt_as_the_post(self):
        post = self.published_post()
        docs, _ = self.export()
        (snapshot,) = [d for d in docs if d["type"] == "AnalyticsSnapshot"]
        jobs = {d["id"]: d for d in docs if d["type"] == "PublishJob"}
        job = jobs[snapshot["job_id"]]
        self.assertEqual((job["idempotency_key"], job["account_id"], job["campaign_id"]),
                         (post["id"], post["account_id"], "intro"))
        receipts = [d for d in docs if d["type"] == "PublishReceipt" and d["job_id"] == job["id"]]
        self.assertEqual([r["remote_post_id"] for r in receipts], [snapshot["remote_post_id"]])

    def test_the_observation_window_is_exported_in_utc_and_an_unknown_window_is_left_out(self):
        with storage.connect(str(self.db)) as con:
            storage.store_metrics(con, self.ids["published"], "2026-10-17T10:00:00+02:00", "native-export", likes=3,
                                  window_start="2026-10-09T02:00:00+02:00", window_end="2026-10-16T00:00:00Z")
        docs, _ = self.export()
        by_time = {d["observed_at"]: d for d in docs if d["type"] == "AnalyticsSnapshot"}
        known = by_time["2026-10-17T08:00:00Z"]
        self.assertEqual((known["window_start"], known["window_end"]), ("2026-10-09T00:00:00Z", "2026-10-16T00:00:00Z"))
        unknown = by_time["2026-10-16T08:00:00Z"]
        self.assertFalse({"window_start", "window_end"} & set(unknown))

    def test_a_campaign_that_is_not_a_slug_is_left_out_rather_than_rewritten(self):
        with storage.connect(str(self.db)) as con:
            pid = storage.add_post(con, "my-bluesky", "bluesky", "Unstaged post with an odd campaign label.",
                                   "Not A Slug!")
            storage.transition(con, pid, "approve")
            claim(con, pid)
            storage.finish_send(con, pid, "at://did:plc:abc/app.bsky.feed.post/3kodd",
                                "https://bsky.app/profile/did:plc:abc/post/3kodd")
            storage.store_metrics(con, pid, "2026-10-16T09:00:00+00:00", "native-export", views=7)
        docs, _ = self.export()
        odd = [d for d in docs if d["type"] == "AnalyticsSnapshot" and d["remote_post_id"].endswith("3kodd")]
        self.assertEqual(len(odd), 1)
        self.assertNotIn("campaign_id", odd[0])
        self.assertEqual(validate("AnalyticsSnapshot", odd[0]), [])

    def test_a_snapshot_whose_post_has_no_remote_id_is_reported_and_never_exported(self):
        docs, summary = self.export()
        exported = {d["remote_post_id"] for d in docs if d["type"] == "AnalyticsSnapshot"}
        self.assertEqual(exported, {REMOTE})
        reasons = [u["reason"] for u in summary["unmapped"] if u["type"] == "AnalyticsSnapshot"]
        self.assertTrue(reasons)
        self.assertTrue(all("no recorded remote post id" in r for r in reasons), reasons)

    def test_a_missing_metric_is_exported_as_unknown_not_zero(self):
        docs, _ = self.export()
        (snapshot,) = [d for d in docs if d["type"] == "AnalyticsSnapshot"]
        self.assertEqual((snapshot["metrics"]["views"], snapshot["metrics"]["clicks"]), (120, 4))
        for name in ("impressions", "likes", "replies", "reposts", "saves", "signups"):
            self.assertIsNone(snapshot["metrics"][name], f"{name} was never imported, so it is unknown, not 0")


if __name__ == "__main__":
    unittest.main()
