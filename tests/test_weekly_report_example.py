"""docs/WEEKLY-REPORT-EXAMPLE.md must be reproducible from SYNTHETIC fixtures via `report`
and `attribution-report`. Offline only: local temp SQLite and fixture files.

To refresh the generated block after an intentional output change:
    REACHWEAVER_WRITE_WEEKLY_EXAMPLE=1 python3 -m unittest tests.test_weekly_report_example
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from reachweaver.attribution import metric_definition
from reachweaver.cli import main
from reachweaver.report_format import render_markdown
from reachweaver.storage import connect, utc_now

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/weekly_report"
DOC = ROOT / "docs/WEEKLY-REPORT-EXAMPLE.md"
BEGIN, END = "<!-- BEGIN GENERATED: tests/test_weekly_report_example.py -->", "<!-- END GENERATED -->"
# Snapshot column -> canonical metric in attribution.metric_definition (likes/signups have none).
CANONICAL = {"views": "views", "impressions": "impressions", "replies": "replies",
             "reposts": "shares", "saves": "saves", "clicks": "clicks"}


SECTION_A = "A. Latest native snapshot per published post (`reachweaver report --format markdown`)"
# A fixed timestamp keeps the generated block reproducible; the real command prints the run time.
GENERATED_AT = "2026-10-05T12:00:00+00:00"


def _v(value) -> str:
    return "unknown" if value is None else str(value)


def section_a(report_json: dict) -> str:
    """Section A is exactly what `report --format markdown` prints, at heading level 3."""
    return render_markdown(dict(report_json, generated_at=GENERATED_AT), level=3, title=SECTION_A)


def render(report_json: dict, attribution: dict) -> str:
    """Markdown for the generated part of the weekly report. Missing values print 'unknown'."""
    platforms = report_json["platforms"]
    out = [section_a(report_json).rstrip("\n")]
    out += ["", "### B. What each column means on each platform (`metric_definition`, table "
            f"{metric_definition('bluesky', 'views')['version']})", "",
            "| Platform | " + " | ".join(CANONICAL) + " |", "|---|" + "---|" * len(CANONICAL)]
    for platform in sorted(platforms):
        cells = []
        for column, canonical in CANONICAL.items():
            entry = metric_definition(platform, canonical)
            field = f"`{entry['native_field']}`" if entry["native_field"] else "none"
            cells.append(f"{field} ({entry['availability']})")
        out.append(f"| {platform} | " + " | ".join(cells) + " |")

    inputs = attribution["inputs"]
    out += ["", "### C. Website sessions joined to posts (`reachweaver attribution-report`)", "",
            f"Analytics rows: {inputs['analytics_rows']}, dates {inputs['analytics_date_range'][0]} to "
            f"{inputs['analytics_date_range'][1]}; GitHub traffic files: {inputs['github_files']}.", "",
            "| Class | Key | Rows | Sessions (known rows) | Conversions (known rows) |", "|---|---|---|---|---|"]
    for item in attribution["attributed"]["posts"]:
        out.append(f"| attributed post | {item['platform']} `{item['post_id']}` | {item['rows']} | "
                   f"{_v(item['sessions'])} ({item['sessions_known_rows']}) | "
                   f"{_v(item['conversions'])} ({item['conversions_known_rows']}) |")
    for item in attribution["attributed"]["campaigns"]:
        out.append(f"| campaign only, no post id | `{item['campaign']}` | {item['rows']} | "
                   f"{_v(item['sessions'])} ({item['sessions_known_rows']}) | "
                   f"{_v(item['conversions'])} ({item['conversions_known_rows']}) |")
    for item in attribution["referrer_only"]["analytics"]:
        out.append(f"| referrer only, domain level | {item['platform']} | {item['rows']} | "
                   f"{_v(item['sessions'])} ({item['sessions_known_rows']}) | "
                   f"{_v(item['conversions'])} ({item['conversions_known_rows']}) |")
    un = attribution["unattributed"]
    reasons = ", ".join(f"{k}={v}" for k, v in un["by_reason"].items())
    out.append(f"| unattributed | {reasons} | {un['rows']} | {_v(un['sessions'])} ({un['sessions_known_rows']}) | "
               f"{_v(un['conversions'])} ({un['conversions_known_rows']}) |")

    views = attribution["github"]["views"]
    out += ["", "### D. GitHub traffic (repository level, never credited to a post)", ""]
    if views:
        out.append(f"- Views: {views['count']} ({views['uniques']} unique) over {views['days']} days, "
                   f"{views['first'][:10]} to {views['last'][:10]}; level `{views['level']}`.")
    for ref in attribution["referrer_only"]["github"]:
        out.append(f"- Social referrer `{ref['referrer']}` ({ref['platform']}): {ref['count']} views, "
                   f"{ref['uniques']} unique.")
    for ref in attribution["github"]["non_social_referrers"]:
        out.append(f"- Other referrer `{ref['referrer']}`: {ref['count']} views, {ref['uniques']} unique.")

    out += ["", "### E. Coverage gaps (unknown, not zero)", ""]
    for gap in attribution["coverage_gaps"]:
        detail = {k: v for k, v in gap.items() if k not in ("gap", "meaning")}
        text = ", ".join(f"{k}={' '.join(v) if isinstance(v, list) else v}" for k, v in detail.items())
        out.append(f"- `{gap['gap']}`" + (f": {text}" if text else ""))
    return "\n".join(out) + "\n"


def _cli(args) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(args)
    if code != 0:
        raise AssertionError(f"{args} exited {code}")
    return json.loads(out.getvalue())


class WeeklyReportExampleTests(unittest.TestCase):
    def build(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = Path(tmp.name) / "state"
        state.mkdir(mode=0o700)
        db = str(state / "synthetic.sqlite")
        fixture = json.loads((FIXTURES / "posts.json").read_text(encoding="utf-8"))
        with connect(db) as con:
            for post in fixture["posts"]:
                con.execute("""INSERT INTO posts(id,account_id,platform,content,campaign,status,created_at,
                               approved_at,send_started_at,remote_id) VALUES(?,?,?,?,?,'published',?,?,?,?)""",
                            (post["id"], post["account_id"], post["platform"], post["content"], post["campaign"],
                             utc_now(), post["send_started_at"], post["send_started_at"], post["remote_id"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--db", db, "import-metrics", "--csv", str(FIXTURES / "snapshots.csv")]), 0)
        report_json = _cli(["--db", db, "report"])
        markdown = io.StringIO()
        with contextlib.redirect_stdout(markdown):
            self.assertEqual(main(["--db", db, "report", "--format", "markdown"]), 0)
        attribution = _cli(["--db", db, "attribution-report", "--analytics", str(FIXTURES / "analytics.csv"),
                            "--github-traffic", str(FIXTURES / "github-views.json"),
                            "--github-traffic", str(FIXTURES / "github-referrers.json"), "--json"])
        return report_json, attribution, markdown.getvalue()

    def test_doc_block_is_reproducible_from_report_outputs(self):
        report_json, attribution, markdown = self.build()
        # Section A is the CLI's own Markdown body (same tables, below its title and timestamp line).
        def body(text):  # drop title + timestamp lines; compare heading text, not heading level
            return [line.lstrip("#") for line in text.splitlines()[4:]]
        self.assertEqual(body(section_a(report_json)), body(markdown))
        block = render(report_json, attribution)
        text = DOC.read_text(encoding="utf-8")
        head, rest = text.split(BEGIN + "\n", 1)
        current, tail = rest.split(END, 1)
        if os.environ.get("REACHWEAVER_WRITE_WEEKLY_EXAMPLE") == "1":
            DOC.write_text(head + BEGIN + "\n" + block + END + tail, encoding="utf-8")
            current = block
        self.assertEqual(current, block, "docs/WEEKLY-REPORT-EXAMPLE.md is stale; see this module's docstring")

    def test_synthetic_labels_and_no_fabricated_zeros(self):
        report_json, attribution, _ = self.build()
        platforms = report_json["platforms"]
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("SYNTHETIC", doc.splitlines()[0])
        self.assertIn("SYNTHETIC", doc.split(BEGIN)[0].split("\n\n", 2)[1])  # first paragraph too
        self.assertIn("SYNTHETIC", (FIXTURES / "posts.json").read_text(encoding="utf-8"))
        # Bluesky exposes no views (metric_definition: unavailable), imported as not_available:
        # report() gives null with zero coverage, never 0, and the rendering says unknown.
        self.assertEqual(platforms["bluesky"]["coverage_by_metric"]["views"], 0)
        self.assertIsNone(platforms["bluesky"]["views"])
        self.assertEqual(platforms["bluesky"]["metrics"]["views"]["status_counts"]["not_available"], 1)
        row = next(line for line in render(report_json, attribution).splitlines() if line.startswith("| bluesky | 1"))
        self.assertIn("| unknown |", row)
        # Window coverage: only the LinkedIn snapshot carried a window (stored in UTC).
        self.assertEqual(platforms["linkedin"]["window_coverage"],
                         {"with_window": 1, "without_window": 0,
                          "earliest_window_start": "2026-09-30T00:00:00+00:00",
                          "latest_window_end": "2026-10-05T00:00:00+00:00"})
        self.assertEqual(platforms["bluesky"]["window_coverage"]["with_window"], 0)
        # Latest snapshot, not a sum of the two Bluesky snapshots.
        self.assertEqual(platforms["bluesky"]["likes"], 31)
        posts = {p["platform"]: p for p in attribution["attributed"]["posts"]}
        self.assertEqual((posts["bluesky"]["sessions"], posts["bluesky"]["conversions"]), (69, 3))
        self.assertIsNone(posts["linkedin"]["conversions"])
        gaps = {g["gap"]: g for g in attribution["coverage_gaps"]}
        self.assertEqual(gaps["published_posts_with_untagged_links"]["post_ids"], ["5e0b000000000004"])
        exported = json.dumps([platforms, attribution])
        for forbidden in ("token", "password", "@"):
            self.assertNotIn(forbidden, exported.lower())


if __name__ == "__main__":
    unittest.main()
