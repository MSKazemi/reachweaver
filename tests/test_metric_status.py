"""Per-metric value status, exact-only report aggregation and report formats (issue #15). Offline only."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.report_format import CSV_COLUMNS, csv_cell, render_csv, render_markdown
from reachweaver.storage import (
    METRIC_FIELDS, METRIC_STATUS_LABELS, SCHEMA, connect, decode_metric_status, latest_snapshot, list_posts,
    metric_states, migrate, parse_metric_value, published_posts, report, snapshots_for, store_metrics,
)

STATUS_DDL = "  metric_status TEXT,\n"
WINDOW_DDL = "  window_start TEXT, window_end TEXT,\n"
# metric_snapshots exactly as it shipped before #15 (and, separately, before observation windows).
PRE_STATUS_SCHEMA = SCHEMA.replace(STATUS_DDL, "")
PRE_WINDOW_SCHEMA = PRE_STATUS_SCHEMA.replace(WINDOW_DDL, "")
OBSERVED = "2026-10-05T09:00:00+00:00"


def run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class _Db(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        state = self.tmp / "state"
        state.mkdir(mode=0o700)
        self.db = str(state / "rw.sqlite")

    def publish(self, con, post_id, platform="bluesky", *, account="acct", campaign="", sent=None):
        con.execute("""INSERT INTO posts(id,account_id,platform,content,campaign,status,created_at,send_started_at,
                       remote_id) VALUES(?,?,?,?,?,'published','2026-10-01T00:00:00+00:00',?,?)""",
                    (post_id, account, platform, "Hello", campaign, sent, "remote-" + post_id))

    def csv_file(self, text: str) -> str:
        path = self.tmp / "snapshots.csv"
        path.write_text(text, encoding="utf-8")
        return str(path)


class ParseMetricValueTests(unittest.TestCase):
    def test_accepted_tokens(self):
        cases = {None: (None, None), "": (None, None), "  ": (None, None), "unknown": (None, None),
                 "UNKNOWN": (None, None), 0: (0, None), 12: (12, None), "12": (12, None), " 7 ": (7, None),
                 "suppressed": (None, {"status": "suppressed"}), "Suppressed": (None, {"status": "suppressed"}),
                 "not_available": (None, {"status": "not_available"}), "delayed": (None, {"status": "delayed"}),
                 "estimated:120": (None, {"status": "estimated", "value": 120}),
                 "estimated:0": (None, {"status": "estimated", "value": 0})}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_metric_value(raw), expected)

    def test_rejected_values_are_never_coerced(self):
        for raw in (-1, "-1", "12.5", "1,234", "1e3", "estimated", "estimated:", "estimated:-3", "estimated:1.5",
                    "abc", "zero", "n/a", True, 3.0, ["1"], "9" * 19):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                parse_metric_value(raw)

    def test_decode_rejects_malformed_status_column(self):
        self.assertEqual(decode_metric_status(None), {})
        for text in ("{bad", "[]", '{"reach":{"status":"suppressed"}}', '{"views":{"status":"zero"}}',
                     '{"views":{"status":"estimated"}}', '{"views":{"status":"delayed","value":3}}',
                     '{"views":{"status":"estimated","value":-2}}', '{"views":"suppressed"}'):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                decode_metric_status(text)


class StoreAndImportTests(_Db):
    def test_store_keeps_exact_counts_in_columns_and_statuses_in_json(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            store_metrics(con, "p1", OBSERVED, "native-export", likes=5, views="suppressed",
                          impressions="estimated:900", saves="not_available", clicks="delayed", replies="unknown")
            row = con.execute("SELECT * FROM metric_snapshots").fetchone()
            self.assertEqual((row["likes"], row["views"], row["impressions"], row["replies"]), (5, None, None, None))
            self.assertEqual(json.loads(row["metric_status"]), {
                "views": {"status": "suppressed"}, "impressions": {"status": "estimated", "value": 900},
                "saves": {"status": "not_available"}, "clicks": {"status": "delayed"}})
            states = metric_states(row)
            self.assertEqual(states["likes"], ("exact", 5))
            self.assertEqual(states["impressions"], ("estimated", 900))
            self.assertEqual(states["replies"], ("unknown", None))
            detail = con.execute("SELECT detail FROM event_log WHERE action='metrics_recorded'").fetchone()[0]
            self.assertIn("statuses=clicks:delayed,impressions:estimated,saves:not_available,views:suppressed", detail)

    def test_status_only_snapshot_is_allowed_but_all_unknown_is_not(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            store_metrics(con, "p1", OBSERVED, "native-export", views="suppressed")
            with self.assertRaisesRegex(ValidationError, "At least one metric"):
                store_metrics(con, "p1", "2026-10-06T09:00:00+00:00", "native-export", views="unknown", likes="")
            with self.assertRaisesRegex(ValidationError, "Metric views"):
                store_metrics(con, "p1", "2026-10-06T09:00:00+00:00", "native-export", views="lots")

    def test_reimport_replaces_statuses(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            store_metrics(con, "p1", OBSERVED, "native-export", views="delayed", likes=1)
            store_metrics(con, "p1", OBSERVED, "native-export", views=40, likes=2)  # the final export arrived
            row = con.execute("SELECT * FROM metric_snapshots").fetchone()
            self.assertEqual((row["views"], row["likes"], row["metric_status"]), (40, 2, None))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0], 1)

    def test_import_metrics_accepts_every_status_token(self):
        # The original crash: views=suppressed raised "invalid literal for int()".
        with connect(self.db) as con:
            self.publish(con, "p1")
        path = self.csv_file(
            "post_id,observed_at,source,views,impressions,likes,replies,reposts,saves,clicks,signups\n"
            f"p1,{OBSERVED},native-export,suppressed,estimated:1200,7,,unknown,not_available,delayed,0\n")
        code, out, err = run("--db", self.db, "import-metrics", "--csv", path)
        self.assertEqual(code, 0, err)
        self.assertIn("Imported 1 snapshots", out)
        with connect(self.db) as con:
            row = con.execute("SELECT * FROM metric_snapshots").fetchone()
        self.assertEqual({k: row[k] for k in METRIC_FIELDS},
                         {"views": None, "impressions": None, "likes": 7, "replies": None, "reposts": None,
                          "saves": None, "clicks": None, "signups": 0})
        self.assertEqual(set(json.loads(row["metric_status"])), {"views", "impressions", "saves", "clicks"})

    def test_import_is_all_or_nothing_and_names_the_line(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
        path = self.csv_file("post_id,observed_at,source,views\n"
                             f"p1,{OBSERVED},native-export,10\n"
                             "p1,2026-10-06T09:00:00+00:00,native-export,about 20\n")
        code, _, err = run("--db", self.db, "import-metrics", "--csv", path)
        self.assertEqual(code, 2)
        self.assertIn("CSV line 3", err)
        self.assertIn("Nothing imported", err)
        self.assertNotIn("invalid literal", err)
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM event_log WHERE action='metrics_recorded'"
                                         ).fetchone()[0], 0)

    def test_metrics_command_accepts_status_tokens(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
        code, _, err = run("--db", self.db, "metrics", "p1", "--observed-at", OBSERVED, "--source", "native-export",
                           "--views", "suppressed", "--likes", "5", "--impressions", "estimated:300")
        self.assertEqual(code, 0, err)
        code, _, err = run("--db", self.db, "metrics", "p1", "--observed-at", "2026-10-06T09:00:00+00:00",
                           "--source", "native-export", "--views", "-3")
        self.assertEqual(code, 2)
        self.assertIn("Metric views", err)
        with connect(self.db) as con:
            states = metric_states(latest_snapshot(con, "p1"))
        self.assertEqual((states["views"], states["likes"], states["impressions"]),
                         (("suppressed", None), ("exact", 5), ("estimated", 300)))


class MigrationTests(_Db):
    def make_old_db(self, schema):
        con = sqlite3.connect(self.db, isolation_level=None)
        try:
            con.executescript(schema)
            con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                           VALUES('p1','acct','bluesky','Hello','published','2026-09-01T00:00:00+00:00')""")
            con.execute("""INSERT INTO metric_snapshots(post_id,observed_at,source,likes)
                           VALUES('p1','2026-09-02T00:00:00+00:00','native-export',7)""")
        finally:
            con.close()
        Path(self.db).chmod(0o600)

    def columns(self, con):
        return [row[1] for row in con.execute("PRAGMA table_info(metric_snapshots)")]

    def test_pre_status_and_pre_window_databases_migrate_idempotently(self):
        for schema, added in ((PRE_STATUS_SCHEMA, ["metric_status"]),
                              (PRE_WINDOW_SCHEMA, ["window_start", "window_end", "metric_status"])):
            with self.subTest(added=added):
                Path(self.db).unlink(missing_ok=True)
                self.assertNotIn("metric_status", schema)
                self.make_old_db(schema)
                raw = sqlite3.connect(self.db, isolation_level=None)
                try:
                    self.assertEqual(migrate(raw), added)
                    self.assertEqual(migrate(raw), [])  # idempotent
                finally:
                    raw.close()
                with connect(self.db) as con:  # connect() on a migrated DB changes nothing
                    self.assertEqual(self.columns(con).count("metric_status"), 1)
                    old = con.execute("SELECT * FROM metric_snapshots").fetchone()
                    self.assertEqual((old["likes"], old["metric_status"]), (7, None))
                    item = report(con)["bluesky"]
                    self.assertEqual((item["likes"], item["views"]), (7, None))
                    self.assertEqual(item["metrics"]["views"]["status_counts"]["unknown"], 1)
                    store_metrics(con, "p1", "2026-09-09T00:00:00+00:00", "native-export", views="suppressed")
                    self.assertEqual(report(con)["bluesky"]["metrics"]["views"]["status_counts"]["suppressed"], 1)

    def test_report_tolerates_a_connection_that_skipped_migrate(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(PRE_WINDOW_SCHEMA)
        con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                       VALUES('p1','acct','bluesky','Hello','published','2026-09-01T00:00:00+00:00')""")
        con.execute("INSERT INTO metric_snapshots(post_id,observed_at,source,likes) VALUES('p1',?,?,3)",
                    (OBSERVED, "native-export"))
        item = report(con)["bluesky"]
        self.assertEqual((item["likes"], item["metrics"]["likes"]["coverage"]), (3, "1/1"))
        con.close()


class ReportTests(_Db):
    def seed(self):
        with connect(self.db) as con:
            for pid in ("a1", "a2", "a3"):
                self.publish(con, pid, "bluesky")
            self.publish(con, "m1", "mastodon")
            store_metrics(con, "a1", OBSERVED, "native-export", likes=10, views="estimated:500", impressions=100)
            store_metrics(con, "a2", OBSERVED, "native-export", likes=4, views=70, impressions="suppressed")
            # a3: published, no snapshot at all -> unknown for every metric
            store_metrics(con, "m1", OBSERVED, "native-export", views="not_available", likes="delayed")

    def test_coverage_status_counts_and_null_for_zero_coverage(self):
        self.seed()
        with connect(self.db) as con:
            data = report(con)
        blue, masto = data["bluesky"], data["mastodon"]
        self.assertEqual(list(data), ["bluesky", "mastodon"])
        self.assertEqual((blue["published"], blue["metric_coverage"]), (3, 2))
        self.assertEqual(blue["likes"], 14)
        self.assertIsNone(blue["saves"])  # zero coverage: null, never 0
        self.assertIsNone(masto["views"])
        self.assertIsNone(masto["likes"])
        likes = blue["metrics"]["likes"]
        self.assertEqual((likes["value"], likes["exact"], likes["total"], likes["coverage"]), (14, 2, 3, "2/3"))
        self.assertEqual(likes["status_counts"], {"exact": 2, "unknown": 1, "suppressed": 0, "not_available": 0,
                                                  "delayed": 0, "estimated": 0})
        self.assertEqual(blue["metrics"]["impressions"]["status_counts"]["suppressed"], 1)
        self.assertEqual(masto["metrics"]["views"]["status_counts"]["not_available"], 1)
        self.assertEqual(masto["metrics"]["likes"]["status_counts"]["delayed"], 1)
        for item in data.values():
            for summary in item["metrics"].values():
                self.assertEqual(sum(summary["status_counts"].values()), item["published"])
                self.assertEqual(set(summary["status_counts"]), set(METRIC_STATUS_LABELS))

    def test_estimates_are_never_mixed_into_exact_values(self):
        self.seed()
        with connect(self.db) as con:
            views = report(con)["bluesky"]["metrics"]["views"]
            legacy = [row["views"] for pid in ("a1", "a2") for row in snapshots_for(con, pid)]
        self.assertEqual((views["value"], views["exact"]), (70, 1))  # not 570
        self.assertEqual(views["estimated"]["posts"], 1)
        self.assertEqual(views["estimated"]["sum"], 500)
        self.assertIn("never summed", views["estimated"]["note"])
        self.assertEqual(legacy, [None, 70])  # integer-column readers (baseline, timing) see exact only

    def test_only_estimates_means_unknown_value(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            store_metrics(con, "p1", OBSERVED, "native-export", views="estimated:40")
            item = report(con)["bluesky"]
        self.assertIsNone(item["views"])
        self.assertIsNone(item["metrics"]["views"]["value"])
        self.assertEqual(item["metrics"]["views"]["estimated"]["sum"], 40)

    def test_malformed_status_column_degrades_to_unknown(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            con.execute("INSERT INTO metric_snapshots(post_id,observed_at,source,likes,metric_status) "
                        "VALUES('p1',?,?,2,'{oops')", (OBSERVED, "native-export"))
            item = report(con)["bluesky"]
        self.assertEqual(item["likes"], 2)
        self.assertEqual(item["metrics"]["views"]["status_counts"]["unknown"], 1)

    def test_cli_formats(self):
        self.seed()
        code, default, _ = run("--db", self.db, "report")
        self.assertEqual(code, 0)
        code, explicit, _ = run("--db", self.db, "report", "--format", "json")
        self.assertEqual(code, 0)
        default_data, explicit_data = json.loads(default), json.loads(explicit)
        self.assertEqual(set(default_data), {"generated_at", "platforms", "note"})
        self.assertEqual(default_data["platforms"], explicit_data["platforms"])
        self.assertIsNone(default_data["platforms"]["bluesky"]["saves"])

        code, markdown, _ = run("--db", self.db, "report", "--format", "markdown")
        self.assertEqual(code, 0)
        self.assertEqual(markdown, render_markdown(dict(default_data, generated_at=markdown.split(
            "Generated ", 1)[1].split(" from", 1)[0])))
        row = next(line for line in markdown.splitlines() if line.startswith("| bluesky | 3 | 2 |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        self.assertEqual(cells[3:5], ["70 (1/3)", "100 (1/3)"])  # views exact only, never 570
        self.assertEqual(cells[5], "14 (2/3)")
        self.assertIn("unknown", cells)
        self.assertIn("| bluesky | views | 1 | 500 |", markdown)  # separate estimates table
        self.assertIn("| mastodon | views | 0/1 | 0 | 0 | 1 | 0 | 0 |", markdown)

        code, text, _ = run("--db", self.db, "report", "--format", "csv")
        self.assertEqual(code, 0)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(len(rows), 2 * len(METRIC_FIELDS))
        views = next(r for r in rows if (r["platform"], r["metric"]) == ("bluesky", "views"))
        self.assertEqual((views["value"], views["exact_posts"], views["total_posts"], views["estimated_posts"],
                          views["estimated_sum"]), ("70", "1", "3", "1", "500"))
        saves = next(r for r in rows if (r["platform"], r["metric"]) == ("bluesky", "saves"))
        self.assertEqual((saves["value"], saves["unknown_posts"]), ("unknown", "3"))
        self.assertTrue(all("/" not in r["exact_posts"] for r in rows))  # no n/N cell a spreadsheet makes a date

        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as exit_:
            main(["--db", self.db, "report", "--format", "xml"])
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("invalid choice", err.getvalue())

    def test_empty_database_in_every_format(self):
        for fmt in ("json", "markdown", "csv"):
            with self.subTest(fmt=fmt):
                code, out, err = run("--db", self.db, "report", "--format", fmt)
                self.assertEqual(code, 0, err)
        self.assertIn("No published posts.", run("--db", self.db, "report", "--format", "markdown")[1])
        self.assertEqual(run("--db", self.db, "report", "--format", "csv")[1].splitlines(), [",".join(CSV_COLUMNS)])


class InjectionSafetyTests(_Db):
    def test_csv_cell_neutralises_formula_prefixes(self):
        for text in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "\nx", "＝cmd", "＋1", "－1",
                     "＠x"):
            with self.subTest(text=text):
                self.assertEqual(csv_cell(text), "'" + text)
        for value, expected in ((None, "unknown"), (0, "0"), (12, "12"), ("bluesky", "bluesky"), ("", "")):
            self.assertEqual(csv_cell(value), expected)

    def test_hand_edited_platform_cannot_inject_formula_or_markdown(self):
        evil = '=HYPERLINK("https://example.invalid","x")|<b>'
        with connect(self.db) as con:
            self.publish(con, "p1", platform=evil)
            store_metrics(con, "p1", OBSERVED, "native-export", likes=1)
            result = {"generated_at": "2026-10-09T00:00:00+00:00", "platforms": report(con), "note": "n"}
        rows = list(csv.reader(io.StringIO(render_csv(result))))
        self.assertTrue(all(row[0].startswith("'=") for row in rows[1:]))
        for row in rows:
            for cell in row:
                self.assertFalse(cell.startswith(("=", "+", "-", "@")), cell)
        markdown = render_markdown(result)
        self.assertNotIn("<b>", markdown)
        self.assertIn("\\|&lt;b&gt;", markdown)
        line = next(line for line in markdown.splitlines() if "HYPERLINK" in line and "| 1 | 1 |" in line)
        self.assertEqual(line.count(" | "), len(METRIC_FIELDS) + 3)  # the pipe did not add a column

    def test_markdown_cells_escape_links_emphasis_and_code(self):
        # Review finding (2026-10-09), defence in depth: only pipes and HTML were escaped, so a cell could
        # still carry a [link](url), *emphasis*, _emphasis_ or a `code span` into the rendered report.
        from reachweaver.report_format import _md
        self.assertEqual(_md("[click](https://evil.example) *b* _i_ `c` <x> a\\b"),
                         "\\[click\\]\\(https://evil.example\\) \\*b\\* \\_i\\_ \\`c\\` &lt;x&gt; a\\\\b")
        self.assertEqual(_md("bluesky"), "bluesky")
        self.assertEqual(_md("a|b\nc"), "a\\|b c")
        evil = "[x](https://evil.example)`*_"
        with connect(self.db) as con:
            self.publish(con, "p1", platform=evil)
            store_metrics(con, "p1", OBSERVED, "native-export", likes=1)
            result = {"generated_at": "2026-10-09T00:00:00+00:00", "platforms": report(con), "note": "n"}
        markdown = render_markdown(result)
        self.assertNotIn("[x](", markdown)
        self.assertIn("\\[x\\]\\(https://evil.example\\)\\`\\*\\_", markdown)
        # Fixed table text keeps its literal parentheses; only data is escaped.
        self.assertIn("| not supplied (1 snapshots) |", markdown)


class ReadHelperTests(_Db):
    def test_list_and_published_posts_filters_and_order(self):
        with connect(self.db) as con:
            self.publish(con, "b", sent="2026-10-02T00:00:00+00:00", campaign="c1")
            self.publish(con, "a", sent="2026-10-03T00:00:00+00:00", campaign="c2")
            self.publish(con, "z", sent=None, platform="mastodon")
            con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                           VALUES('d','acct','bluesky','Draft','draft','2026-10-01T00:00:00+00:00')""")
            self.assertEqual([r["id"] for r in published_posts(con)], ["z", "b", "a"])  # NULL send first
            self.assertEqual([r["id"] for r in published_posts(con, platform="bluesky")], ["b", "a"])
            self.assertEqual([r["id"] for r in published_posts(con, campaign="c2")], ["a"])
            self.assertEqual([r["id"] for r in published_posts(con, account_id="other")], [])
            self.assertEqual({r["id"] for r in list_posts(con)}, {"a", "b", "z", "d"})
            self.assertEqual([r["id"] for r in list_posts(con, status="draft")], ["d"])

    def test_snapshot_helpers(self):
        with connect(self.db) as con:
            self.publish(con, "p1")
            store_metrics(con, "p1", "2026-10-06T09:00:00+00:00", "native-export", likes=2)
            store_metrics(con, "p1", OBSERVED, "native-export", likes=1)
            store_metrics(con, "p1", "2026-10-07T09:00:00+00:00", "other-tool", likes=3)
            self.assertEqual([r["likes"] for r in snapshots_for(con, "p1")], [2, 1, 3])  # storage order
            self.assertEqual([r["likes"] for r in snapshots_for(con, "p1", source="native-export")], [2, 1])
            self.assertEqual(latest_snapshot(con, "p1")["likes"], 3)
            self.assertIsNone(latest_snapshot(con, "missing"))
            self.assertEqual(snapshots_for(con, "missing"), [])


class MockstatsThresholdReportTests(_Db):
    """Third mocked provider (issue #15): thresholded values flow through store, report and renderers."""

    def test_suppressed_small_counts_are_not_exact_and_legend_is_shown(self):
        from reachweaver.metrics_fetch import MOCKSTATS_SOURCE, normalize_mockstats
        cases = json.loads((Path(__file__).parent / "fixtures" / "providers" / "mockstats"
                            / "threshold_cases.json").read_text())
        with connect(self.db) as con:
            self.publish(con, "ms1", "mockstats")
            self.publish(con, "ms2", "mockstats")
            store_metrics(con, "ms1", OBSERVED, MOCKSTATS_SOURCE,
                          **normalize_mockstats(cases["above_threshold"]["metrics"]))
            store_metrics(con, "ms2", OBSERVED, MOCKSTATS_SOURCE,
                          **normalize_mockstats(cases["below_threshold"]["metrics"]))
            data = report(con)
        item = data["mockstats"]
        clicks = item["metrics"]["clicks"]
        self.assertEqual((clicks["value"], clicks["exact"], clicks["total"]), (14, 1, 2))  # 3 is not summed
        self.assertEqual(clicks["status_counts"]["suppressed"], 1)
        self.assertEqual(item["metrics"]["views"]["value"], 240)
        self.assertIsNone(item["metrics"]["likes"]["value"])  # reactions_all is a different definition
        self.assertIsNone(item["metrics"]["impressions"]["value"])
        envelope = {"platforms": data}
        markdown = render_markdown(envelope)
        self.assertIn("`suppressed` = withheld by the platform", markdown)
        self.assertNotRegex(markdown, r"\| 3 \|")
        rows = list(csv.reader(io.StringIO(render_csv(envelope))))
        header = rows[0]
        saves = next(r for r in rows[1:] if r[header.index("metric")] == "saves")
        self.assertEqual(saves[header.index("suppressed_posts")], "1")

    def test_no_legend_without_suppressed_values(self):
        with connect(self.db) as con:
            self.publish(con, "ok1")
            store_metrics(con, "ok1", OBSERVED, "native-export", likes=4)
            self.assertNotIn("withheld by the platform", render_markdown({"platforms": report(con)}))


if __name__ == "__main__":
    unittest.main()
