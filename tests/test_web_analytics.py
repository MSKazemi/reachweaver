"""Offline Google Search Console and GA4 CSV import for attribution-report (roadmap P2).

Every fixture is SYNTHETIC and written to a temporary folder. Nothing is fetched: the
CLI tests run with socket entry points patched to fail, and the join only reads SQLite.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import web_analytics
from reachweaver.attribution import attribution_report, load_analytics_csv
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post
from reachweaver.web_analytics import (
    combine_ga4, combine_gsc, load_ga4_csv, load_gsc_csv, parse_count, parse_day, parse_decimal,
)

GA4_PREAMBLE = ("# ----------------------------------------\n"
                "# Traffic acquisition: Session source / medium\n"
                "# Account: Synthetic account\n"
                "# Property: Synthetic property\n"
                "# ----------------------------------------\n"
                "#\n"
                "# All Users\n"
                "# Start date: 20261001\n"
                "# End date: 20261015\n")
GA4_HEADER = ("Session source / medium,Session campaign,Session manual ad content,Sessions,Engaged sessions,"
              "Engagement rate,Key events\n")
PAGES_HEADER = "Top pages,Clicks,Impressions,CTR,Position\n"


class TempFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def write(self, name, text, encoding="utf-8"):
        path = self.tmp / name
        path.write_bytes(text.encode(encoding) if isinstance(text, str) else text)
        return path

    def ga4(self, body, name="ga4.csv", preamble=GA4_PREAMBLE, header=GA4_HEADER):
        return self.write(name, preamble + header + body)


class NumberFormatTests(unittest.TestCase):
    def test_counts_accept_plain_and_grouped_whole_numbers(self):
        for text, expected in (("1234", 1234), ("1,234", 1234), ("1.234", 1234), ("1 234", 1234),
                               ("1 234", 1234), ("1 234", 1234), ("1'234", 1234),
                               ("12,345,678", 12345678), ("0", 0), (" 7 ", 7)):
            with self.subTest(text=text):
                self.assertEqual(parse_count(text, "Sessions", 2), expected)

    def test_blank_count_is_unknown_not_zero(self):
        self.assertIsNone(parse_count("", "Sessions", 2))
        self.assertIsNone(parse_count("  ", "Sessions", 2))
        self.assertIsNone(parse_count(None, "Sessions", 2))

    def test_counts_reject_decimals_signs_mixed_grouping_and_non_ascii_digits(self):
        for text in ("12.5", "1,23", "-3", "+3", "1e3", "1,234.567", "1,234,56", "abc", "١٢",
                     "1,,234", "1 234,567", "10000000000000"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_count(text, "Sessions", 2)

    def test_decimals_are_locale_tolerant(self):
        for text, expected in (("12.3", 12.3), ("12,3", 12.3), ("1,234.5", 1234.5), ("1.234,5", 1234.5),
                               ("1,234,567", 1234567.0), ("8", 8.0), ("1'234.5", 1234.5)):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_decimal(text, "Position", 2), expected)

    def test_percent_values_become_fractions(self):
        self.assertAlmostEqual(parse_decimal("3.5%", "CTR", 2, percent=True), 0.035)
        self.assertAlmostEqual(parse_decimal("3,5 %", "CTR", 2, percent=True), 0.035)
        self.assertAlmostEqual(parse_decimal("0.25", "CTR", 2, percent=True), 0.25)
        self.assertIsNone(parse_decimal("", "CTR", 2, percent=True))

    def test_decimals_reject_garbage(self):
        for text in ("abc", "1..2", "-1", "1,2,3", "%", "1.2.3,4,5", "12.", "NaN", "inf"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_decimal(text, "Position", 2, percent=True)
        with self.assertRaisesRegex(ValidationError, "without %"):
            parse_decimal("12%", "Position", 2)

    def test_days(self):
        self.assertEqual(parse_day("20261001", "Date", 2), "2026-10-01")
        self.assertEqual(parse_day("2026-10-01", "Date", 2), "2026-10-01")
        self.assertIsNone(parse_day("", "Date", 2))
        for text in ("2026/10/01", "20261341", "01-10-2026", "2026-1-1"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_day(text, "Date", 2)


class Ga4ParseTests(TempFiles):
    def test_happy_path_with_comment_preamble_totals_and_trailing_chart(self):
        path = self.ga4('bluesky / social,rw-launch,abc,"1,234",800,0.65,3\n'
                        "t.co / referral,(referral),(not set),12,5,0.4,0\n"
                        "google / organic,(organic),(not set),40,30,0.75,\n"
                        '"(direct) / (none)",(direct),(not set),9,1,0.1,0\n'
                        ',,,"1,295",836,0.65,3\n'
                        "\n# ----\n# Chart data\nNth day,Sessions\n0001,5\n")
        export = load_ga4_csv(path)
        self.assertEqual(export["date_range"], ("2026-10-01", "2026-10-15"))
        self.assertEqual(export["date_range_source"], "export_header")
        self.assertEqual(export["dimensions"], ["source_medium", "campaign", "content"])
        self.assertEqual(export["metrics"], ["sessions", "engaged_sessions", "key_events"])
        self.assertEqual(export["ignored_columns"], ["engagement rate"])
        self.assertEqual(export["totals_row"], {"sessions": 1295, "engaged_sessions": 836, "key_events": 3})
        self.assertEqual(export["trailing_lines_ignored"], 2)
        rows = export["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual((rows[0]["utm_source"], rows[0]["utm_medium"], rows[0]["utm_campaign"],
                          rows[0]["utm_content"], rows[0]["sessions"]), ("bluesky", "social", "rw-launch", "abc", 1234))
        self.assertEqual(rows[0]["line"], 11)
        # Untagged rows keep only the source, as a referrer; placeholders become empty.
        self.assertEqual((rows[1]["utm_source"], rows[1]["referrer"]), ("", "t.co"))
        self.assertEqual(rows[2]["referrer"], "google")
        self.assertIsNone(rows[2]["key_events"])
        self.assertEqual((rows[3]["referrer"], rows[3]["utm_campaign"]), ("", ""))

    def test_api_names_and_date_column(self):
        path = self.ga4("bluesky,social,rw-launch,abc,20261003,4,2,1\n"
                        "bluesky,social,rw-launch,abc,20261005,6,3,0\n",
                        preamble="", header="sessionSource,sessionMedium,sessionCampaignName,"
                                            "sessionManualAdContent,date,sessions,engagedSessions,keyEvents\n")
        export = load_ga4_csv(path)
        self.assertEqual(export["date_range"], ("2026-10-03", "2026-10-05"))
        self.assertEqual(export["date_range_source"], "date_column")
        self.assertEqual([r["date"] for r in export["rows"]], ["2026-10-03", "2026-10-05"])

    def test_bom_and_crlf_and_legacy_conversions_label(self):
        text = "﻿" + GA4_PREAMBLE.replace("\n", "\r\n") + \
            "Session source,Session medium,Sessions,Conversions\r\nbluesky,social,3,1\r\n"
        export = load_ga4_csv(self.write("bom.csv", text))
        self.assertEqual(export["metrics"], ["sessions", "key_events"])
        self.assertEqual(export["rows"][0]["key_events"], 1)
        # A platform id with medium social is tagged even without a campaign column.
        self.assertEqual(export["rows"][0]["utm_source"], "bluesky")

    def test_no_dates_anywhere_means_unknown_range(self):
        export = load_ga4_csv(self.ga4("bluesky / social,rw,abc,3,1,0.3,0\n", preamble=""))
        self.assertIsNone(export["date_range"])
        self.assertIsNone(export["date_range_source"])

    def test_rejections_name_the_problem(self):
        cases = {
            "unknown column": ("Session source / medium,Sessions,Bananas\nx / y,1,2\n", "Bananas.*not supported"),
            "user scoped": ("First user source / medium,Sessions\nx / y,1\n", "user-scoped"),
            "no sessions": ("Session source / medium,Engaged sessions\nx / y,1\n", "needs a Sessions column"),
            "no source": ("Session campaign,Sessions\nc,1\n", "needs Session source / medium"),
            "only source": ("Session source,Sessions\nx,1\n", "needs Session source / medium"),
            "both forms": ("Session source / medium,Session source,Sessions\nx / y,x,1\n", "either"),
            "duplicate": ("Session source / medium,Landing page,Landing page + query string,Sessions\nx / y,/,/,1\n",
                          "both give landing"),
            "same twice": ("Session source / medium,Sessions,sessions\nx / y,1,1\n", "both give sessions"),
            "fractional sessions": ("Session source / medium,Sessions\nx / y,12.5\n", "line 11|Line 11"),
            "negative": ("Session source / medium,Sessions\nx / y,-1\n", "Sessions|sessions"),
            "width": ("Session source / medium,Sessions\nx / y,1,2\n", "3 cells; the header has 2"),
            "quoting": ('Session source / medium,Sessions\n"x / y,1\n', "quoting"),
            "semicolon": ("Session source / medium;Sessions\nx / y;1\n", "semicolon"),
            "empty column": ("Session source / medium,,Sessions\nx / y,,1\n", "empty column name"),
            "date format": ("Session source / medium,Date,Sessions\nx / y,10/03/2026,1\n", "YYYY-MM-DD"),
            "date outside": ("Session source / medium,Date,Sessions\nx / y,20261020,1\n", "outside"),
            "date missing": ("Session source / medium,Date,Sessions\nx / y,20261002,1\nz / w,,1\n",
                             "every row needs a Date"),
            "two totals": ("Session source / medium,Sessions\n,1\nGrand total,1\n", "second totals row"),
        }
        for name, (table, pattern) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValidationError, pattern):
                load_ga4_csv(self.write(f"{name}.csv", GA4_PREAMBLE + table))

    def test_header_dates_must_be_valid_and_ordered(self):
        with self.assertRaisesRegex(ValidationError, "End date is before"):
            load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n",
                                  preamble="# Start date: 20261015\n# End date: 20261001\n"))
        with self.assertRaisesRegex(ValidationError, "Start date and # End date must be"):
            load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n", preamble="# Start date: Oct 1\n# End date: 20261001\n"))

    def test_file_level_refusals(self):
        with self.assertRaisesRegex(ValidationError, "empty"):
            load_ga4_csv(self.write("empty.csv", ""))
        with self.assertRaisesRegex(ValidationError, "no table header"):
            load_ga4_csv(self.write("comments.csv", GA4_PREAMBLE))
        with self.assertRaisesRegex(ValidationError, "not UTF-8"):
            load_ga4_csv(self.write("latin1.csv", GA4_HEADER + "caf\xe9 / social,c,a,1,1,0.1,1\n", "latin-1"))
        with self.assertRaisesRegex(ValidationError, "regular file"):
            load_ga4_csv(self.tmp / "missing.csv")
        with self.assertRaisesRegex(ValidationError, "regular file"):
            load_ga4_csv(self.tmp)
        target = self.ga4("x / y,c,a,1,1,0.1,1\n")
        link = self.tmp / "link.csv"
        os.symlink(target, link)
        with self.assertRaisesRegex(ValidationError, "no symlinks"):
            load_ga4_csv(link)

    def test_size_and_row_caps(self):
        path = self.ga4("x / y,c,a,1,1,0.1,1\n" * 5)
        with patch.object(web_analytics, "MAX_EXPORT_BYTES", 100), \
                self.assertRaisesRegex(ValidationError, "larger than"):
            load_ga4_csv(path)
        with patch.object(web_analytics, "MAX_EXPORT_ROWS", 4), \
                self.assertRaisesRegex(ValidationError, "more than 4 data rows"):
            load_ga4_csv(path)
        with patch.object(web_analytics, "MAX_EXPORT_ROWS", 5):
            self.assertEqual(len(load_ga4_csv(path)["rows"]), 5)
        self.assertEqual(web_analytics.MAX_EXPORT_ROWS, 100_000)  # GA4's documented CSV maximum

    def test_errors_never_echo_cell_values(self):
        secret = "someone@example.com"
        for body in (f"x / y,c,a,{secret},1,0.1,1\n", f"x / y,c,a,1,1,0.1,{secret}\n"):
            with self.subTest(body=body), self.assertRaises(ValidationError) as caught:
                load_ga4_csv(self.ga4(body))
            self.assertNotIn(secret, str(caught.exception))
        with self.assertRaises(ValidationError) as caught:
            load_ga4_csv(self.write("hdr.csv", f"Session source / medium,Sessions,{secret}\nx / y,1,2\n"))
        self.assertNotIn(secret, str(caught.exception))

    def test_combine_refuses_overlapping_or_undated_exports(self):
        first = load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n", "a.csv"))
        second = load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n", "b.csv",
                                       preamble="# Start date: 20261016\n# End date: 20261031\n"))
        overlap = load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n", "c.csv",
                                        preamble="# Start date: 20261015\n# End date: 20261020\n"))
        undated = load_ga4_csv(self.ga4("x / y,c,a,1,1,0.1,1\n", "d.csv", preamble=""))
        self.assertEqual(len(combine_ga4([second, first])), 2)
        self.assertEqual(combine_ga4([undated]), [undated])
        with self.assertRaisesRegex(ValidationError, "overlapping"):
            combine_ga4([first, overlap])
        with self.assertRaisesRegex(ValidationError, "known date range"):
            combine_ga4([first, undated])
        with self.assertRaisesRegex(ValidationError, "at most"):
            combine_ga4([undated] * (web_analytics.MAX_EXPORT_FILES + 1))


class GscParseTests(TempFiles):
    def test_pages_table(self):
        table = load_gsc_csv(self.write("Pages.csv", "﻿" + PAGES_HEADER +
                                        'https://example.com/docs/,12,"1,200",1%,8.5\n'
                                        "https://example.com/,0,0,0%,0\n"))
        self.assertEqual(table["kind"], "pages")
        first, second = table["rows"]
        self.assertEqual((first["key"], first["clicks"], first["impressions"]), ("https://example.com/docs/", 12, 1200))
        self.assertAlmostEqual(first["ctr"], 0.01)
        self.assertEqual(first["position"], 8.5)
        # Search Console writes "~"/"-" as 0: a Position of 0 is unknown.
        self.assertIsNone(second["position"])
        self.assertEqual(second["clicks"], 0)

    def test_table_kinds_are_recognized_by_first_column(self):
        for header, kind in (("Top queries", "queries"), ("Query", "queries"), ("Page", "pages"),
                             ("Top pages", "pages")):
            with self.subTest(header=header):
                self.assertEqual(load_gsc_csv(self.write("t.csv", f"{header},Clicks,Impressions\nfoo,1,2\n"))["kind"],
                                 kind)
        dates = load_gsc_csv(self.write("Dates.csv", "Date,Clicks,Impressions,CTR,Position\n"
                                                     "2026-10-02,5,100,5%,9.1\n20261001,1,10,10%,7\n"))
        self.assertEqual([r["key"] for r in dates["rows"]], ["2026-10-02", "2026-10-01"])
        filters = load_gsc_csv(self.write("Filters.csv", "Filter,Value\nSearch type,Web\nDate,Last 3 months\n"))
        self.assertEqual(filters["kind"], "filters")
        self.assertEqual(filters["rows"][1], {"filter": "Date", "value": "Last 3 months"})

    def test_optional_ctr_and_position(self):
        table = load_gsc_csv(self.write("p.csv", "Top pages,Impressions,Clicks\nhttps://example.com/,10,1\n"))
        self.assertEqual(table["rows"][0]["clicks"], 1)
        self.assertIsNone(table["rows"][0]["ctr"])
        self.assertIsNone(table["rows"][0]["position"])

    def test_rejections(self):
        cases = {
            "countries": ("Country,Clicks,Impressions\nItaly,1,2\n", "not used"),
            "devices": ("Device,Clicks,Impressions\nMobile,1,2\n", "not used"),
            "unknown table": ("Keyword,Clicks,Impressions\nfoo,1,2\n", "not a Search Console"),
            "unknown metric": ("Top pages,Clicks,Impressions,Revenue\nhttps://e.com/,1,2,3\n", "Revenue"),
            "missing impressions": ("Top pages,Clicks\nhttps://e.com/,1\n", "Clicks and Impressions"),
            "duplicate metric": ("Top pages,Clicks,Clicks,Impressions\nhttps://e.com/,1,1,2\n", "two columns"),
            "duplicate page": (PAGES_HEADER + "https://e.com/,1,2,1%,1\nhttps://e.com/,1,2,1%,1\n", "double-counted"),
            "duplicate date": ("Date,Clicks,Impressions\n2026-10-01,1,2\n20261001,1,2\n", "double-counted"),
            "blank key": (PAGES_HEADER + ",1,2,1%,1\n", "first column is blank"),
            "ctr over 100": (PAGES_HEADER + "https://e.com/,1,2,150%,1\n", "at most 100%"),
            "bad clicks": (PAGES_HEADER + "https://e.com/,1.5,2,1%,1\n", "Clicks"),
            "filters columns": ("Filter,Value,Extra\nDate,x,y\n", "exactly the columns"),
            "no header": ("\n\n", "no header"),
            "width": (PAGES_HEADER + "https://e.com/,1,2\n", "3 cells; the header has 5"),
        }
        for name, (text, pattern) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(ValidationError, pattern):
                load_gsc_csv(self.write(f"{name}.csv", text))

    def test_combine_requires_one_table_per_kind(self):
        pages = load_gsc_csv(self.write("a.csv", PAGES_HEADER + "https://e.com/,1,2,1%,1\n"))
        queries = load_gsc_csv(self.write("q.csv", "Top queries,Clicks,Impressions\nfoo,1,2\n"))
        self.assertEqual(set(combine_gsc([pages, queries])), {"pages", "queries"})
        with self.assertRaisesRegex(ValidationError, "Two --gsc-csv files are pages"):
            combine_gsc([pages, pages])
        with self.assertRaisesRegex(ValidationError, "at most"):
            combine_gsc([pages] * (web_analytics.MAX_EXPORT_FILES + 1))

    def test_size_cap(self):
        path = self.write("big.csv", PAGES_HEADER + "https://e.com/,1,2,1%,1\n")
        with patch.object(web_analytics, "MAX_EXPORT_BYTES", 10), \
                self.assertRaisesRegex(ValidationError, "larger than"):
            load_gsc_csv(path)


class JoinTests(TempFiles):
    def setUp(self):
        super().setUp()
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)
        self.p1 = self.published("bluesky", "2026-10-02T09:00:00+00:00",
                                 "Docs https://example.com/docs?utm_source=bluesky&utm_content=x and "
                                 "https://github.com/example/repo.")
        self.p2 = self.published("linkedin", "2026-10-03T09:00:00+00:00", "See https://example.com/missing/")
        self.late = self.published("mastodon", "2026-11-20T09:00:00+00:00", "No link")
        self.draft = add_post(self.db, "my-x", "x", "Draft only", "rw-launch")

    def published(self, platform, sent, text):
        pid = add_post(self.db, f"my-{platform}", platform, text, "rw-launch")
        self.db.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?", (sent, pid))
        return pid

    def ga4_export(self, header=GA4_HEADER, preamble=GA4_PREAMBLE):
        return load_ga4_csv(self.ga4("\n".join([
            f'bluesky / social,rw-launch,{self.p1},"1,200",800,0.6,3',
            f"bluesky / social,RW Launch,{self.p1},30,,0.1,",
            "linkedin / social,rw-launch,(not set),25,10,0.4,1",
            "t.co / referral,(referral),(not set),12,5,0.4,0",
            "m.facebook.com / referral,(referral),(not set),4,1,0.25,0",
            "google / organic,(organic),(not set),40,30,0.75,2",
            "newsletter / email,spring,(not set),9,4,0.4,0",
            f"x / social,rw-launch,{self.draft},4,1,0.25,0",
            f"mastodon / social,rw-launch,{self.p1},2,1,0.5,0",
            ',,,"1,326",852,0.6,6',
        ]) + "\n", header=header, preamble=preamble))

    def gsc_tables(self):
        pages = load_gsc_csv(self.write("Pages.csv", PAGES_HEADER +
                                        "https://example.com/docs/,30,900,3.33%,6.2\n"
                                        "https://example.com/,50,2000,2.5%,4.1\n"
                                        "https://example.com/blog/post?ref=feed&token=abc,7,70,10%,3\n"))
        queries = load_gsc_csv(self.write("Queries.csv", "Top queries,Clicks,Impressions,CTR,Position\n"
                                          "reachweaver,20,100,20%,1.2\nmail someone@example.com,1,5,20%,9\n"
                                          "social scheduler,,40,0%,15\n"))
        dates = load_gsc_csv(self.write("Dates.csv", "Date,Clicks,Impressions,CTR,Position\n"
                                        "2026-10-01,40,1500,2.67%,5\n2026-10-02,47,1470,3.2%,5\n"))
        filters = load_gsc_csv(self.write("Filters.csv", "Filter,Value\nSearch type,Web\nDate,Custom\n"))
        return combine_gsc([pages, queries, dates, filters])

    def test_ga4_rows_join_to_posts_like_utm_rows(self):
        before = self.db.total_changes
        result = attribution_report(self.db, None, ga4=combine_ga4([self.ga4_export()]))
        self.assertEqual(self.db.total_changes, before)  # read-only
        ga4 = result["ga4"]
        self.assertEqual(ga4["source"], "owner_supplied_ga4_csv_export")
        self.assertEqual(ga4["date_range"], ["2026-10-01", "2026-10-15"])
        self.assertEqual(ga4["rows"], 9)
        (post,) = ga4["attributed"]["posts"]
        self.assertEqual((post["post_id"], post["platform"], post["rows"]), (self.p1, "bluesky", 2))
        self.assertEqual((post["sessions"], post["sessions_known_rows"]), (1230, 2))
        self.assertEqual((post["engaged_sessions"], post["engaged_sessions_known_rows"]), (800, 1))
        self.assertEqual((post["key_events"], post["key_events_known_rows"]), (3, 1))
        (campaign,) = ga4["attributed"]["campaigns"]
        self.assertEqual((campaign["campaign"], campaign["level"], campaign["sessions"]),
                         ("rw-launch", "campaign_only_no_post_id", 25))
        self.assertEqual([(r["platform"], r["sessions"], r["level"]) for r in ga4["referrer_only"]],
                         [("facebook", 4, "session_source_domain_not_post"),
                          ("x", 12, "session_source_domain_not_post")])
        self.assertEqual(ga4["unattributed"]["by_reason"], {
            "no_utm": 1, "unknown_utm": 1, "utm_conflict:utm_source": 1, "utm_matches_unpublished_post": 1})
        self.assertEqual(ga4["unattributed"]["sessions"], 55)
        gaps = {g["gap"]: g for g in ga4["coverage_gaps"]}
        self.assertEqual(gaps["published_posts_without_matching_ga4_rows"]["post_ids"], sorted([self.p2, self.late]))
        self.assertIn("NOT zero", gaps["published_posts_without_matching_ga4_rows"]["meaning"])
        self.assertEqual(gaps["published_after_ga4_window"]["post_ids"], [self.late])
        self.assertEqual(gaps["ga4_engaged_sessions_unknown_rows"]["rows"], 1)
        self.assertNotIn("ga4_manual_ad_content_column_missing", gaps)
        (file_info,) = ga4["files"]
        self.assertEqual(file_info["export_totals_row"]["sessions"], 1326)  # reported, never joined
        self.assertTrue(any("NOT causation" in w for w in ga4["warnings"]))
        self.assertTrue(any("Owner-supplied" in w for w in ga4["warnings"]))
        # The generic analytics part is empty and says so, instead of listing every post as unmatched.
        top_gaps = {g["gap"] for g in result["coverage_gaps"]}
        self.assertIn("analytics_csv_not_supplied", top_gaps)
        self.assertNotIn("published_posts_without_matching_utm_rows", top_gaps)
        self.assertEqual(result["inputs"]["ga4_files"], 1)
        self.assertEqual(result["inputs"]["ga4_date_range"], ["2026-10-01", "2026-10-15"])
        self.assertIsNone(result["search_console"])

    def test_ga4_without_ad_content_or_key_events_reports_gaps(self):
        export = load_ga4_csv(self.ga4("bluesky / social,rw-launch,3\n", preamble="",
                                       header="Session source / medium,Session campaign,Sessions\n"))
        ga4 = attribution_report(self.db, None, ga4=[export])["ga4"]
        self.assertEqual(ga4["attributed"]["posts"], [])
        self.assertEqual(ga4["attributed"]["campaigns"][0]["sessions"], 3)
        gaps = {g["gap"] for g in ga4["coverage_gaps"]}
        self.assertTrue({"ga4_manual_ad_content_column_missing", "ga4_date_range_unknown",
                         "ga4_engaged_sessions_column_missing", "ga4_key_events_column_missing",
                         "ga4_engaged_sessions_unknown_rows", "ga4_key_events_unknown_rows"} <= gaps)
        self.assertIsNone(ga4["date_range"])

    def test_two_ga4_periods_combine_into_one_window(self):
        october = self.ga4_export()
        later = load_ga4_csv(self.ga4(f"bluesky / social,rw-launch,{self.p1},5,1,0.2,0\n", "later.csv",
                                      preamble="# Start date: 20261016\n# End date: 20261130\n"))
        ga4 = attribution_report(self.db, None, ga4=combine_ga4([october, later]))["ga4"]
        self.assertEqual(ga4["date_range"], ["2026-10-01", "2026-11-30"])
        self.assertEqual(ga4["attributed"]["posts"][0]["sessions"], 1235)
        self.assertNotIn("published_after_ga4_window", {g["gap"] for g in ga4["coverage_gaps"]})

    def test_search_console_is_page_context_never_post_credit(self):
        result = attribution_report(self.db, None, gsc=self.gsc_tables())
        gsc = result["search_console"]
        self.assertEqual(gsc["source"], "owner_supplied_search_console_performance_export")
        self.assertEqual((gsc["date_range"], gsc["date_range_source"]), (["2026-10-01", "2026-10-02"], "dates_table"))
        self.assertEqual((gsc["date_filter_label"], gsc["search_type"]), ("Custom", "Web"))
        self.assertEqual(gsc["tables"], {"pages": 3, "queries": 3, "dates": 2, "filters": 2})
        self.assertEqual(gsc["dates_total"], {"clicks": 87, "clicks_known_days": 2, "impressions": 2970,
                                              "impressions_known_days": 2, "days": 2,
                                              "level": "property_by_date_not_post"})
        (linked,) = gsc["pages_linked_from_posts"]
        self.assertEqual((linked["page"], linked["clicks"], linked["linked_from_post_ids"], linked["level"]),
                         ("https://example.com/docs/", 30, [self.p1], "page_search_context_not_post"))
        gaps = {g["gap"]: g for g in gsc["coverage_gaps"]}
        # The /missing page is on a host the export covers; github.com is not, so it is not flagged.
        self.assertEqual(gaps["post_destinations_not_in_gsc_pages"]["pages"],
                         [{"page": "example.com/missing", "post_ids": [self.p2]}])
        self.assertIn("NOT zero", gaps["post_destinations_not_in_gsc_pages"]["meaning"])
        self.assertNotIn("gsc_date_range_unknown", gaps)
        self.assertEqual([p["page"] for p in gsc["top_pages"]],
                         ["https://example.com/", "https://example.com/docs/", "https://example.com/blog/post"])
        self.assertEqual([q["query"] for q in gsc["top_queries"]][:2], ["reachweaver", "mail [REDACTED:email]"])
        self.assertIsNone(gsc["top_queries"][2]["clicks"])  # blank stays unknown and sorts last
        self.assertTrue(any("never credited" in w for w in gsc["warnings"]))
        self.assertTrue(any("1,000 rows" in w for w in gsc["warnings"]))
        # Search clicks never become attributed post rows.
        self.assertEqual(result["attributed"]["posts"], [])
        self.assertIsNone(result["ga4"])
        self.assertEqual(result["inputs"]["gsc_files"], 4)
        self.assertNotIn("token=abc", json.dumps(result))

    def test_search_console_without_dates_or_pages(self):
        queries = load_gsc_csv(self.write("Queries.csv", "Top queries,Clicks,Impressions\nfoo,1,2\n"))
        gsc = attribution_report(self.db, None, gsc=combine_gsc([queries]))["search_console"]
        self.assertEqual({g["gap"] for g in gsc["coverage_gaps"]}, {"gsc_pages_table_not_supplied",
                                                                    "gsc_date_range_unknown"})
        self.assertIsNone(gsc["date_range"])
        self.assertIsNone(gsc["dates_total"])
        self.assertEqual(gsc["top_pages"], [])

    def test_analytics_only_report_is_unchanged_apart_from_new_null_sections(self):
        analytics = self.write("a.csv", "date,landing_url,utm_source,utm_medium,utm_campaign,utm_content,sessions,"
                                        f"conversions,referrer\n2026-10-03,https://example.com/docs,bluesky,social,"
                                        f"rw-launch,{self.p1},5,1,\n")
        result = attribution_report(self.db, load_analytics_csv(analytics))
        self.assertIsNone(result["ga4"])
        self.assertIsNone(result["search_console"])
        self.assertEqual(list(result["attributed"]["posts"][0]),
                         ["post_id", "platform", "account_id", "campaign", "rows", "sessions", "sessions_known_rows",
                          "conversions", "conversions_known_rows", "landing_pages"])
        gaps = {g["gap"] for g in result["coverage_gaps"]}
        self.assertIn("published_posts_without_matching_utm_rows", gaps)
        self.assertNotIn("analytics_csv_not_supplied", gaps)


class CliTests(TempFiles):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, "create_connection", side_effect=AssertionError("network used")), \
                patch.object(socket.socket, "connect", side_effect=AssertionError("network used")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.tmp / "state" / "rw.sqlite"), *argv])
        return code, out.getvalue(), err.getvalue()

    def files(self):
        ga4 = self.ga4("bluesky / social,rw-launch,abc,3,2,0.6,1\nt.co / referral,(referral),(not set),2,1,0.5,0\n")
        pages = self.write("Pages.csv", PAGES_HEADER + "https://example.com/,1,10,10%,2\n")
        return ga4, pages

    def test_json_output_shape(self):
        ga4, pages = self.files()
        code, out, _ = self.run_cli("attribution-report", "--ga4-csv", str(ga4), "--gsc-csv", str(pages), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data), {"inputs", "attributed", "referrer_only", "unattributed", "github", "ga4",
                                     "search_console", "coverage_gaps", "warnings", "generated_at"})
        self.assertEqual(set(data["ga4"]), {"source", "date_range", "rows", "files", "attributed", "referrer_only",
                                            "unattributed", "coverage_gaps", "warnings"})
        self.assertEqual(set(data["search_console"]), {
            "source", "date_range", "date_range_source", "date_filter_label", "search_type", "tables",
            "dates_total", "pages_linked_from_posts", "top_pages", "top_queries", "coverage_gaps", "warnings"})
        self.assertEqual(data["inputs"]["ga4_files"], 1)
        self.assertEqual(data["inputs"]["gsc_files"], 1)
        self.assertEqual(data["ga4"]["referrer_only"][0]["platform"], "x")

    def test_text_output_labels_owner_data_and_no_causality(self):
        ga4, pages = self.files()
        code, out, _ = self.run_cli("attribution-report", "--ga4-csv", str(ga4), "--gsc-csv", str(pages))
        self.assertEqual(code, 0)
        self.assertIn("GA4 export (owner-supplied, 2026-10-01 to 2026-10-15): rows=2", out)
        self.assertIn("GA4 REFERRER-ONLY x (domain level): sessions=2", out)
        self.assertIn("SEARCH CONSOLE export (owner-supplied, unknown dates): search-side context, never credited", out)
        self.assertIn("SEARCH CONSOLE GAP: gsc_date_range_unknown", out)
        self.assertIn("no causal claim", out)

    def test_analytics_and_new_exports_together(self):
        ga4, _ = self.files()
        analytics = self.write("a.csv", "date,page,utm_source,utm_medium,utm_campaign,utm_content,sessions\n"
                                        "2026-10-01,/,,,,,4\n")
        code, out, _ = self.run_cli("attribution-report", "--analytics", str(analytics), "--ga4-csv", str(ga4),
                                    "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["inputs"]["analytics_rows"], 1)
        self.assertEqual(data["ga4"]["rows"], 2)

    def test_no_input_is_refused_before_a_database_exists(self):
        code, _, err = self.run_cli("attribution-report")
        self.assertEqual(code, 2)
        self.assertIn("at least one of --analytics, --ga4-csv or --gsc-csv", err)
        self.assertFalse((self.tmp / "state" / "rw.sqlite").exists())

    def test_bad_exports_exit_2_with_one_error_line(self):
        bad = self.write("bad.csv", "Session source / medium,Sessions,Bananas\nx / y,1,2\n")
        code, out, err = self.run_cli("attribution-report", "--ga4-csv", str(bad))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("ERROR:", err)
        self.assertNotIn("Traceback", err)
        pages = self.write("Pages.csv", PAGES_HEADER + "https://example.com/,1,10,10%,2\n")
        code, _, err = self.run_cli("attribution-report", "--gsc-csv", str(pages), "--gsc-csv", str(pages))
        self.assertEqual(code, 2)
        self.assertIn("Two --gsc-csv files are pages", err)


if __name__ == "__main__":
    unittest.main()
