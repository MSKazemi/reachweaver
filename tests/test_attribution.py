"""Offline UTM, metric-definition, attribution and SEO-destination contracts."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from reachweaver.attribution import (
    CANONICAL_METRICS, METRIC_DEFINITIONS_VERIFIED, attribution_report, build_utm_link, campaign_destinations,
    load_analytics_csv, load_github_traffic, load_seo_briefs, metric_definition,
    normalize_destination, parse_utm, referrer_platform, validate_utm_link,
)
from reachweaver.cli import main
from reachweaver.policy import PLATFORMS, ValidationError
from reachweaver.storage import SCHEMA, add_post

ROOT = Path(__file__).resolve().parents[1]
HEADER = "date,landing_url,utm_source,utm_medium,utm_campaign,utm_content,sessions,conversions,referrer\n"


class UtmLinkTests(unittest.TestCase):
    def test_normalizes_and_is_idempotent(self):
        raw = "HTTPS://Example.COM:443/docs?b=2&a=1&utm_source=x&UTM_campaign=old#section"
        link = build_utm_link(raw, platform="bluesky", campaign="Release v0.3", content="Post_1")
        self.assertEqual(link, "https://example.com/docs?a=1&b=2&utm_source=bluesky&utm_medium=social"
                               "&utm_campaign=release-v0-3&utm_content=post-1")
        again = build_utm_link(link, platform="bluesky", campaign="Release v0.3", content="Post_1")
        self.assertEqual(again, link)
        self.assertEqual(validate_utm_link(link), [])
        self.assertEqual(normalize_destination("https://example.com"), "https://example.com/")

    def test_strips_click_trackers_and_keeps_other_params(self):
        link = build_utm_link("https://example.com/p?fbclid=1&gclid=2&msclkid=3&lang=it",
                              platform="linkedin", campaign="c", term="kubernetes")
        self.assertNotIn("clid", link)
        self.assertIn("lang=it", link)
        self.assertEqual(parse_utm(link)["utm_term"], "kubernetes")
        self.assertIn("click-tracking parameter present",
                      validate_utm_link("https://example.com/?utm_source=x&utm_medium=social"
                                        "&utm_campaign=c&gclid=1"))

    def test_rejects_unsafe_destinations_and_unknown_platform(self):
        for url in ("http://example.com/", "https://user:pw@example.com/", "https://localhost/",
                    "https://127.0.0.1/", "ftp://example.com/", "https://exa mple.com/"):
            with self.assertRaises(ValidationError, msg=url):
                build_utm_link(url, platform="x", campaign="c")
        with self.assertRaises(ValidationError):
            build_utm_link("https://example.com/", platform="myspace", campaign="c")
        with self.assertRaises(ValidationError):
            build_utm_link("https://example.com/", platform="x", campaign="!!!")

    def test_origin_allowlist(self):
        allowed = ["https://GitHub.com", "https://mskazemi.github.io/"]
        self.assertTrue(build_utm_link("https://github.com/MSKazemi/reachweaver", platform="x",
                                       campaign="c", allow_origins=allowed).startswith("https://github.com/"))
        with self.assertRaises(ValidationError):
            build_utm_link("https://github.com.evil.example/", platform="x", campaign="c", allow_origins=allowed)
        with self.assertRaises(ValidationError):
            build_utm_link("https://github.com/", platform="x", campaign="c",
                           allow_origins=["https://github.com/path"])
        self.assertIn("destination origin not allowlisted",
                      validate_utm_link(build_utm_link("https://other.example/", platform="x", campaign="c"),
                                        allow_origins=allowed))

    def test_invalid_utm_detected(self):
        self.assertIn("missing utm_campaign", validate_utm_link("https://example.com/?utm_source=x&utm_medium=social"))
        self.assertIn("utm_medium must be 'social'",
                      validate_utm_link("https://example.com/?utm_source=x&utm_medium=email&utm_campaign=c"))
        self.assertTrue(validate_utm_link("https://example.com/?utm_campaign=c&utm_source=x&utm_medium=social"))

    def test_cli_prints_link(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["utm-link", "https://example.com/a#x", "--platform", "mastodon", "--campaign", "c",
                         "--content", "p1", "--allow-origin", "https://example.com"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "https://example.com/a?utm_source=mastodon&utm_medium=social"
                                                 "&utm_campaign=c&utm_content=p1")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["utm-link", "https://bad.example/", "--platform", "x", "--campaign", "c",
                                   "--allow-origin", "https://example.com"]), 2)


class MetricDefinitionTests(unittest.TestCase):
    def test_every_platform_metric_has_valid_availability_and_source(self):
        for platform in PLATFORMS:
            for metric in CANONICAL_METRICS:
                entry = metric_definition(platform, metric)
                self.assertIn(entry["availability"], ("api", "native_export", "unavailable", "unknown"))
                self.assertTrue(entry["source_url"].startswith("https://"), (platform, metric))
                self.assertTrue(entry["version"])
                if entry["availability"] == "unknown":
                    self.assertIsNone(entry["denominator"])

    def test_every_recorded_entry_has_https_source_and_verified_date(self):
        recorded = 0
        for platform in PLATFORMS:
            for metric in CANONICAL_METRICS:
                entry = metric_definition(platform, metric)
                if entry["availability"] == "unknown":
                    self.assertIsNone(entry["verified_date"], (platform, metric))
                    self.assertIsNone(entry["native_field"], (platform, metric))
                    continue
                recorded += 1
                with self.subTest(platform=platform, metric=metric):
                    self.assertTrue(entry["source_url"].startswith("https://"))
                    self.assertTrue(entry["definition"])
                    self.assertEqual(entry["verified_date"], METRIC_DEFINITIONS_VERIFIED)
                    self.assertEqual(date.fromisoformat(entry["verified_date"]).isoformat(), entry["verified_date"])
                    for extra in entry["additional_sources"]:
                        self.assertTrue(extra.startswith("https://"))
        self.assertGreater(recorded, 40)

    def test_known_and_unknown_entries(self):
        yt = metric_definition("youtube", "watch_time")
        self.assertEqual((yt["native_field"], yt["availability"], yt["denominator"]),
                         ("estimatedMinutesWatched", "api", "views"))
        self.assertEqual(metric_definition("bluesky", "views")["availability"], "unavailable")
        unknown = metric_definition("hashnode", "views")
        self.assertEqual(unknown["availability"], "unknown")
        self.assertIsNone(unknown["native_field"])
        self.assertIsNone(unknown["definition"])
        self.assertIsNone(unknown["verified_date"])
        self.assertEqual(metric_definition("facebook", "saves")["availability"], "unknown")
        # Corrected after verification: Instagram impressions are deprecated, LinkedIn uses member analytics.
        self.assertEqual(metric_definition("instagram", "impressions")["availability"], "unavailable")
        self.assertEqual(metric_definition("linkedin", "impressions")["native_field"], "IMPRESSION")
        self.assertEqual(metric_definition("reddit", "replies")["availability"], "unknown")
        with self.assertRaises(ValidationError):
            metric_definition("youtube", "virality")
        with self.assertRaises(ValidationError):
            metric_definition("myspace", "views")


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.addCleanup(self.db.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.p1 = self.published("bluesky", "rw-launch", "2026-10-01T09:00:00+00:00",
                                 "Read https://example.com/docs?utm_content=x")
        self.p2 = self.published("linkedin", "rw-launch", "2026-10-02T09:00:00+00:00",
                                 "Untagged https://example.com/docs")
        self.late = self.published("x", "rw-launch", "2026-12-01T09:00:00+00:00", "No link at all")
        self.draft = add_post(self.db, "my-x", "x", "Draft only", "rw-launch")
        self.db.execute("""INSERT INTO campaign_variants VALUES('rw-launch','reachweaver','developer-linkedin',
                           'fp',?,'[]','2026-10-01T00:00:00+00:00')""", (self.p2,))

    def published(self, platform, campaign, sent, text):
        pid = add_post(self.db, f"my-{platform}", platform, text, campaign)
        self.db.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?", (sent, pid))
        return pid

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def analytics(self):
        return self.write("ga.csv", HEADER + "\n".join([
            f"2026-10-03,https://example.com/docs,bluesky,social,rw-launch,{self.p1},12,1,",
            f"2026-10-04,https://example.com/docs,bluesky,social,rw-launch,{self.p1},3,,",
            "2026-10-04,https://example.com/docs,linkedin,social,RW Launch,developer-linkedin,,,",
            "2026-10-04,https://example.com/,linkedin,social,rw-launch,,5,0,",
            f"2026-10-05,https://example.com/,x,social,rw-launch,{self.draft},4,0,",
            f"2026-10-05,https://example.com/,mastodon,social,rw-launch,{self.p1},2,0,",
            "2026-10-05,https://example.com/,newsletter,email,spring,,9,1,",
            "2026-10-05,https://example.com/,,,,,7,0,https://t.co/abc",
            "2026-10-05,https://example.com/,,,,,8,,https://www.google.com/",
        ]) + "\n")

    def test_join_separates_attributed_referrer_and_unattributed_without_zero_fill(self):
        before = self.db.execute("SELECT count(*) FROM event_log").fetchone()[0]
        github = [
            load_github_traffic(self.write("views.json", json.dumps({"count": 40, "uniques": 9, "views": [
                {"timestamp": "2026-10-03T00:00:00Z", "count": 30, "uniques": 7},
                {"timestamp": "2026-10-04T00:00:00Z", "count": 10, "uniques": 2}]}))),
            load_github_traffic(self.write("refs.json", json.dumps([
                {"referrer": "news.ycombinator.com", "count": 11, "uniques": 5},
                {"referrer": "Google", "count": 6, "uniques": 4}]))),
        ]
        result = attribution_report(self.db, load_analytics_csv(self.analytics()), github)
        posts = {p["post_id"]: p for p in result["attributed"]["posts"]}
        self.assertEqual(set(posts), {self.p1, self.p2})
        self.assertEqual((posts[self.p1]["sessions"], posts[self.p1]["conversions"]), (15, 1))
        self.assertEqual(posts[self.p1]["conversions_known_rows"], 1)
        # Variant id resolved to post; all-blank values stay unknown, not zero.
        self.assertIsNone(posts[self.p2]["sessions"])
        self.assertIsNone(posts[self.p2]["conversions"])
        self.assertEqual(posts[self.p2]["sessions_known_rows"], 0)
        self.assertEqual(result["attributed"]["campaigns"][0]["campaign"], "rw-launch")
        self.assertEqual(result["attributed"]["campaigns"][0]["sessions"], 5)
        self.assertEqual([r["platform"] for r in result["referrer_only"]["analytics"]], ["x"])
        self.assertEqual(result["referrer_only"]["github"][0]["platform"], "hackernews")
        self.assertEqual(result["github"]["non_social_referrers"][0]["referrer"], "Google")
        self.assertEqual(result["github"]["views"]["level"], "repository_total_not_post")
        self.assertEqual(result["unattributed"]["by_reason"], {
            "no_utm": 1, "unknown_utm": 1, "utm_conflict:utm_source": 1, "utm_matches_unpublished_post": 1})
        gaps = {g["gap"]: g for g in result["coverage_gaps"]}
        self.assertEqual(gaps["published_posts_without_matching_utm_rows"]["post_ids"], [self.late])
        self.assertEqual(gaps["published_posts_with_untagged_links"]["post_ids"], [self.p2])
        self.assertEqual(gaps["published_after_analytics_window"]["post_ids"], [self.late])
        self.assertIn("conversions_unknown_rows", gaps)
        self.assertNotIn("github_views_not_supplied", gaps)
        self.assertTrue(any("NOT causation" in w for w in result["warnings"]))
        self.assertEqual(self.db.execute("SELECT count(*) FROM event_log").fetchone()[0], before)

    def test_campaign_mismatch_is_not_attributed(self):
        path = self.write("c.csv", HEADER + f"2026-10-03,https://example.com/,bluesky,social,other,{self.p1},5,1,\n")
        result = attribution_report(self.db, load_analytics_csv(path))
        self.assertEqual(result["attributed"]["posts"], [])
        self.assertEqual(result["unattributed"]["by_reason"], {"utm_conflict:utm_campaign": 1})
        self.assertIn("github_views_not_supplied", {g["gap"] for g in result["coverage_gaps"]})

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValidationError):
            load_analytics_csv(self.write("bad.csv", "date,page,sessions\n2026-10-01,/,1\n"))
        with self.assertRaises(ValidationError):
            load_analytics_csv(self.write("neg.csv", HEADER + "2026-10-01,/,x,social,c,p,-3,,\n"))
        with self.assertRaises(ValidationError):
            load_analytics_csv(self.write("date.csv", HEADER + "10/01/2026,/,x,social,c,p,3,,\n"))
        with self.assertRaises(ValidationError):
            load_github_traffic(self.write("g.json", json.dumps({"clones": []})))
        with self.assertRaises(ValidationError):
            load_github_traffic(self.write("g2.json", json.dumps([{"referrer": "a", "count": -1, "uniques": 0}])))

    def test_referrer_mapping(self):
        self.assertEqual(referrer_platform("https://l.facebook.com/x"), "facebook")
        self.assertEqual(referrer_platform("lnkd.in"), "linkedin")
        self.assertIsNone(referrer_platform("github.com"))
        self.assertIsNone(referrer_platform("notlinkedin.com"))
        self.assertIsNone(referrer_platform(""))

    def test_cli_report_reads_local_db(self):
        db_dir = self.tmp / "state"
        db_dir.mkdir(mode=0o700)
        db = str(db_dir / "rw.sqlite")
        path = self.write("empty.csv", HEADER + "2026-10-01,https://example.com/,,,,,1,,\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--db", db, "attribution-report", "--analytics", str(path), "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["unattributed"]["by_reason"], {"no_utm": 1})
        self.assertIn("generated_at", data)


class SeoIntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.doc = json.loads((ROOT / "examples/seo-briefs.example.json").read_text())

    def load(self, doc):
        path = self.tmp / "briefs.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return load_seo_briefs(path)

    def test_example_workflows_pick_canonical_landing_pages(self):
        briefs = load_seo_briefs(ROOT / "examples/seo-briefs.example.json")
        release = campaign_destinations(briefs, "release", "reachweaver", platform="bluesky", campaign="v0.3")
        self.assertEqual(release["status"], "ok")
        self.assertIn("/docs/", release["destination"]["canonical_url"])
        self.assertEqual(validate_utm_link(release["utm_link"]), [])
        paper = campaign_destinations(briefs, "paper")
        self.assertEqual(paper["destination"]["page_id"], "example-paper-page")
        self.assertEqual(campaign_destinations(briefs, "paper", "reachweaver")["status"], "no_destination")
        with self.assertRaises(ValidationError):
            campaign_destinations(briefs, "webinar")

    def test_priority_order_and_retired_pages(self):
        self.doc["briefs"].append({**self.doc["briefs"][0], "page_id": "lower", "priority": 3,
                                   "canonical_url": "https://example.org/lower", "primary_keyword": "other"})
        self.doc["briefs"].append({**self.doc["briefs"][0], "page_id": "retired", "priority": 1,
                                   "status": "retired", "canonical_url": "https://example.org/r",
                                   "primary_keyword": "retired"})
        result = campaign_destinations(self.load(self.doc), "release", "reachweaver")
        self.assertEqual(result["destination"]["page_id"], "reachweaver-docs-calendar")
        self.assertEqual([b["page_id"] for b in result["alternatives"]], ["lower"])

    def test_duplicate_canonical_url_and_keyword_conflicts(self):
        self.doc["briefs"].append({**self.doc["briefs"][0], "page_id": "dup-page", "campaign_type": "docs"})
        self.doc["campaigns"].append({"campaign_id": "docs-push", "page_id": "dup-page",
                                      "start": "2026-10-20", "end": "2026-11-05", "status": "active"})
        self.doc["campaigns"].append({"campaign_id": "ended-one", "page_id": "dup-page",
                                      "start": "2026-10-15", "end": "2026-10-16", "status": "ended"})
        result = campaign_destinations(self.load(self.doc), "release", "reachweaver")
        self.assertEqual(result["status"], "review_conflicts")
        types = sorted(c["type"] for c in result["destination_conflicts"])
        self.assertEqual(types, ["duplicate_canonical_url_across_briefs", "same_canonical_url",
                                 "same_primary_keyword"])
        overlap = next(c for c in result["destination_conflicts"] if c["type"] == "same_canonical_url")
        self.assertEqual(overlap["overlap"], ["2026-10-20", "2026-10-29"])
        self.assertNotIn("ended-one", json.dumps(result["destination_conflicts"]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["seo-destinations", "--briefs", str(self.tmp / "briefs.json"), "--type", "release"])
        self.assertEqual(code, 2)

    def test_non_overlapping_windows_do_not_conflict(self):
        self.doc["briefs"].append({**self.doc["briefs"][0], "page_id": "same-kw", "campaign_type": "docs",
                                   "canonical_url": "https://example.org/kw"})
        self.doc["campaigns"].append({"campaign_id": "later", "page_id": "same-kw",
                                      "start": "2026-11-01", "end": "2026-11-05"})
        self.assertEqual(campaign_destinations(self.load(self.doc), "release")["destination_conflicts"], [])

    def test_validation_rejects_bad_contracts_and_secrets(self):
        bad = [
            lambda d: d["briefs"][0].update(canonical_url="https://example.org/a?utm_source=x"),
            lambda d: d["briefs"][0].update(canonical_url="http://example.org/a"),
            lambda d: d["briefs"][0].update(campaign_type="webinar"),
            lambda d: d["briefs"][0].update(priority=0),
            lambda d: d["briefs"][0].pop("primary_keyword"),
            lambda d: d["briefs"].append(dict(d["briefs"][0])),
            lambda d: d["campaigns"][0].update(page_id="missing"),
            lambda d: d["campaigns"][0].update(end="2026-10-01"),
            lambda d: d["source"].update(api_token="x"),
            lambda d: d["briefs"][0].update(audience="ghp_abcdefghijklmnop"),
            lambda d: d.update(schema_version=2),
        ]
        for mutate in bad:
            doc = json.loads(json.dumps(self.doc))
            mutate(doc)
            with self.assertRaises(ValidationError):
                self.load(doc)


if __name__ == "__main__":
    unittest.main()
