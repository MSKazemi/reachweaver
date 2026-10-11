"""Optional observation windows on metric snapshots and the old-schema migration (#15)."""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, WINDOW_FIELDS, connect, migrate, report, store_metrics

WINDOW_DDL = "  window_start TEXT, window_end TEXT,\n"
# The metric_snapshots table exactly as it shipped before window columns existed.
OLD_SCHEMA = SCHEMA.replace(WINDOW_DDL, "")


def columns(con) -> list[str]:
    return [row[1] for row in con.execute("PRAGMA table_info(metric_snapshots)")]


class MetricWindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = Path(tmp.name) / "state"
        state.mkdir(mode=0o700)
        self.db = str(state / "rw.sqlite")
        self.tmp = Path(tmp.name)

    def make_old_db(self):
        self.assertNotEqual(OLD_SCHEMA, SCHEMA, "window DDL line moved; update WINDOW_DDL")
        con = sqlite3.connect(self.db, isolation_level=None)
        try:
            con.executescript(OLD_SCHEMA)
            con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                           VALUES('p1','acct','bluesky','Hello','published','2026-09-01T00:00:00+00:00')""")
            con.execute("""INSERT INTO metric_snapshots(post_id,observed_at,source,likes,replies)
                           VALUES('p1','2026-09-02T00:00:00+00:00','native-export',7,NULL)""")
        finally:
            con.close()
        Path(self.db).chmod(0o600)

    def test_old_schema_db_is_migrated_in_place_and_keeps_working(self):
        self.make_old_db()
        with connect(self.db) as con:
            self.assertTrue(set(WINDOW_FIELDS) <= set(columns(con)))
            old = con.execute("SELECT * FROM metric_snapshots").fetchone()
            self.assertEqual((old["likes"], old["replies"], old["window_start"], old["window_end"]),
                             (7, None, None, None))
            data = report(con)["bluesky"]
            self.assertEqual((data["likes"], data["coverage_by_metric"]["replies"]), (7, 0))
            self.assertEqual(data["window_coverage"]["without_window"], 1)
            self.assertEqual(migrate(con), [])  # idempotent
            store_metrics(con, "p1", "2026-09-09T00:00:00+00:00", "native-export", likes=9,
                          window_start="2026-09-02T00:00:00Z", window_end="2026-09-09T00:00:00+00:00")
            data = report(con)["bluesky"]
            self.assertEqual(data["likes"], 9)
            self.assertEqual(data["window_coverage"], {
                "with_window": 1, "without_window": 0,
                "earliest_window_start": "2026-09-02T00:00:00+00:00",
                "latest_window_end": "2026-09-09T00:00:00+00:00"})
        with connect(self.db) as con:  # reopening a migrated DB changes nothing
            self.assertEqual(columns(con).count("window_start"), 1)

    def test_report_keeps_existing_keys_on_migrated_db(self):
        self.make_old_db()
        with connect(self.db) as con:
            item = report(con)["bluesky"]
        for key in ("published", "metric_coverage", "views", "likes", "coverage_by_metric"):
            self.assertIn(key, item)

    def test_window_validation(self):
        self.make_old_db()
        observed = "2026-09-09T00:00:00+00:00"
        bad = [
            {"window_start": "2026-09-01T00:00:00Z"},                                 # one bound only
            {"window_end": "2026-09-01T00:00:00Z"},
            {"window_start": "2026-09-05T00:00:00Z", "window_end": "2026-09-01T00:00:00Z"},  # reversed
            {"window_start": "2026-09-01T00:00:00Z", "window_end": "2026-09-10T00:00:00Z"},  # after observed
            {"window_start": "2026-09-01", "window_end": "2026-09-02T00:00:00Z"},           # no timezone
        ]
        with connect(self.db) as con:
            for window in bad:
                with self.subTest(window=window), self.assertRaises(ValidationError):
                    store_metrics(con, "p1", observed, "native-export", likes=1, **window)
            store_metrics(con, "p1", observed, "native-export", likes=1)  # no window = unknown window
            self.assertIsNone(con.execute("SELECT window_start FROM metric_snapshots WHERE observed_at=?",
                                          (observed,)).fetchone()[0])

    def test_csv_import_reads_optional_window_columns(self):
        self.make_old_db()
        path = self.tmp / "snapshots.csv"
        path.write_text("post_id,observed_at,source,likes,window_start,window_end\n"
                        "p1,2026-09-09T00:00:00+00:00,native-export,4,2026-09-02T00:00:00+02:00,"
                        "2026-09-08T23:00:00Z\n"
                        "p1,2026-09-10T00:00:00+00:00,native-export,5,,\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", self.db, "import-metrics", "--csv", str(path)]), 0)
        with connect(self.db) as con:
            rows = con.execute("SELECT window_start,window_end FROM metric_snapshots ORDER BY observed_at").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(None, None), ("2026-09-01T22:00:00+00:00",
                                                                    "2026-09-08T23:00:00+00:00"), (None, None)])
        bad = self.tmp / "bad.csv"
        bad.write_text("post_id,observed_at,source,likes,window_start\n"
                       "p1,2026-09-11T00:00:00+00:00,native-export,4,2026-09-02T00:00:00Z\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--db", self.db, "import-metrics", "--csv", str(bad)]), 2)


if __name__ == "__main__":
    unittest.main()
