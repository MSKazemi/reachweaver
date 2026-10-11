"""Offline contracts for opt-in source ingestion (issue #9). No real network is ever used."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from reachweaver import sources
from reachweaver.cli import main
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/sources"
CONFIG = FIXTURES / "sources.test.json"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
TOKEN = "ghp" + "_FAKEtokenFORtestsONLY1234567890abcd"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def no_network(req):
    raise AssertionError(f"unexpected network request in offline test: {req.full_url}")


class SourceScanTests(unittest.TestCase):
    def scan(self, config=None, **kwargs):
        config = config or json.loads(CONFIG.read_text(encoding="utf-8"))
        kwargs.setdefault("top", 100)
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            return sources.scan_sources(config, base_dir=FIXTURES, now=NOW, **kwargs)

    def by_url(self, result, url):
        return next(s for s in result["signals"] if s["url"] == url)

    def test_release_normalization_is_canonical_and_inert(self):
        result = self.scan()
        url = "https://github.com/example-org/example-tool/releases/tag/v1.2.0"
        signal = self.by_url(result, url)
        self.assertEqual(signal["id"], hashlib.sha256(f"github_release\n{url}".encode()).hexdigest())
        self.assertEqual(signal["kind"], "github_release")
        self.assertEqual(signal["published_at"], "2026-10-01T12:00:00Z")
        self.assertIn(url, signal["evidence_urls"])
        self.assertTrue(signal["untrusted_text"])
        self.assertEqual(signal["verification"], "unverified_source_text")
        # A hostile html_url in the fixture is ignored; the URL is rebuilt from repo + tag.
        older = self.by_url(result, "https://github.com/example-org/example-tool/releases/tag/v1.1.0")
        self.assertEqual(older["summary"], "Adds HPC scheduler integration & metrics.")
        self.assertNotIn("evil.example.net", json.dumps(result))
        for item in result["signals"]:
            self.assertLessEqual(len(item["summary"]), sources.SUMMARY_LIMIT)
            self.assertTrue(all(u.startswith("https://") for u in item["evidence_urls"]))

    def test_prompt_injection_is_carried_only_as_flagged_data(self):
        result = self.scan()
        signal = self.by_url(result, "https://github.com/example-org/example-tool/releases/tag/v1.2.0")
        self.assertIn("IGNORE ALL PREVIOUS INSTRUCTIONS", signal["summary"])
        self.assertIn("instruction_like_text_treated_as_data", signal["uncertainties"])
        self.assertAlmostEqual(signal["confidence"], 0.7)
        self.assertNotIn("‮", signal["summary"])
        self.assertNotIn("\u0007", signal["summary"])
        self.assertEqual(result["status"], "RESEARCH_ONLY")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            with mock.patch("reachweaver.publishers._send_http", side_effect=no_network), \
                    mock.patch.dict(os.environ, {"REACHWEAVER_ALLOW_LIVE": "1"}):
                code, out, _ = run_cli("--db", str(db), "sources-scan", "--config", str(CONFIG), "--json")
            self.assertEqual(code, 1)  # poisoned feed fixture is reported as a source error
            self.assertFalse(db.exists(), "scanning must never open the outbox or create drafts")
            self.assertIn("Nothing was drafted", json.loads(out)["note"])

    def test_ranking_order_and_transparent_breakdown(self):
        result = self.scan()
        scores = [s["rank_score"] for s in result["signals"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(result["signals"][0]["url"], "https://github.com/example-org/example-tool/pull/41")
        for signal in result["signals"]:
            breakdown = signal["rank_breakdown"]
            self.assertEqual(set(breakdown), set(sources.DEFAULT_WEIGHTS))
            self.assertAlmostEqual(sum(v["weight"] for v in breakdown.values()), 1.0, places=3)
            self.assertAlmostEqual(sum(v["contribution"] for v in breakdown.values()), signal["rank_score"], places=3)
        undated = [s for s in result["signals"] if s["published_at"] is None]
        self.assertTrue(undated and all(s["freshness"] == 0.0 for s in undated))
        self.assertTrue(all(s["rank_score"] <= result["signals"][0]["rank_score"] for s in undated))

    def test_freshness_relevance_and_custom_weights(self):
        self.assertAlmostEqual(sources.freshness_score(NOW - timedelta(days=14), NOW, 14), 0.5)
        self.assertAlmostEqual(sources.freshness_score(NOW - timedelta(days=28), NOW, 14), 0.25)
        self.assertEqual(sources.freshness_score(None, NOW, 14), 0.0)
        score, matched = sources.audience_relevance("Kubernetes autoscaling for data center GPUs", ["kubernetes", "data center", "speech"])
        self.assertEqual(matched, ["kubernetes", "data center"])
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(sources.audience_relevance("anything", []), (0.5, []))
        config = json.loads(CONFIG.read_text())
        config["weights"] = {"freshness": 0, "novelty": 0, "audience_relevance": 1, "confidence": 0}
        top = self.scan(config)["signals"][0]
        self.assertEqual(top["kind"], "paper")  # matches all keywords
        self.assertEqual(top["rank_score"], top["audience_relevance"])

    def test_duplicates_reposts_and_previously_seen_are_held_back(self):
        result = self.scan()
        original = self.by_url(result, "https://blog.example.org/posts/speech-hpc")
        repost = self.by_url(result, "https://blog.example.org/posts/speech-hpc-repost")
        self.assertEqual(original["status"], "candidate")
        self.assertEqual(repost["status"], "near_duplicate")
        self.assertEqual(repost["duplicate_of"], original["id"])
        self.assertLess(repost["novelty"], original["novelty"])
        # Release and tag share one evidence URL: keep only the stronger release record.
        same = [s for s in result["signals"] if s["url"].endswith("/releases/tag/v1.2.0")]
        self.assertEqual([s["kind"] for s in same], ["github_release"])
        self.assertIn("duplicate_url", {s["reason"] for s in result["skipped"]})
        self.assertFalse(sources.near_duplicate("tool v1.2.0 released", "tool v1.2.1 released"))
        state = {"schema_version": 1, "seen": [{"id": original["id"], "title": original["title"]}]}
        again = self.scan(state=state)
        seen = self.by_url(again, original["url"])
        self.assertEqual((seen["status"], seen["novelty"], seen["previously_seen"]), ("previously_seen", 0.0, True))

    def test_bot_and_trivial_pull_requests_are_filtered_with_manual_override(self):
        result = self.scan()
        pulls = [s for s in result["signals"] if s["kind"] == "github_pull"]
        self.assertEqual([s["url"].rsplit("/", 1)[-1] for s in pulls], ["41"])
        reasons = {s["url"].rsplit("/", 1)[-1]: s["reason"] for s in result["skipped"] if s["source_id"] == "tool-pulls"}
        self.assertEqual(reasons, {"42": "bot_author", "43": "bot_author", "44": "trivial_title",
                                   "45": "trivial_title", "46": "trivial_title", "47": "not_merged"})
        config = json.loads(CONFIG.read_text())
        docs_pr = "https://github.com/example-org/example-tool/pull/45"
        config["overrides"] = {"include_urls": [docs_pr], "exclude_urls": ["https://github.com/example-org/example-tool/pull/41"]}
        overridden = self.scan(config)
        urls = {s["url"] for s in overridden["signals"]}
        self.assertIn(docs_pr, urls)
        self.assertNotIn("https://github.com/example-org/example-tool/pull/41", urls)

    def test_prereleases_and_drafts(self):
        result = self.scan()
        urls = {s["url"] for s in result["signals"]}
        self.assertFalse(any("rc.1" in u or "beta" in u or "v1.3.0" in u for u in urls))
        self.assertNotIn("Private draft notes", json.dumps(result))
        config = json.loads(CONFIG.read_text())
        config["include_prereleases"] = True
        urls = {s["url"] for s in self.scan(config)["signals"]}
        self.assertIn("https://github.com/example-org/example-tool/releases/tag/v1.2.0-rc.1", urls)
        self.assertFalse(any("v1.3.0" in u for u in urls))  # drafts are never emitted

    def test_entity_and_doctype_feeds_are_rejected_before_parsing(self):
        result = self.scan()
        self.assertIn({"source_id": "poisoned-feed", "error": "feed_doctype_or_entity_rejected"}, result["errors"])
        self.assertNotIn("poisoned", json.dumps(result["signals"]))
        cases = {
            b'<?xml version="1.0"?><!ENTITY x "y"><rss/>': "feed_doctype_or_entity_rejected",
            b'<!doctype rss><rss version="2.0"/>': "feed_doctype_or_entity_rejected",
            '<?xml version="1.0" encoding="UTF-16"?><rss/>'.encode("utf-16"): "unsupported_feed_encoding",
            b'<?xml version="1.0" encoding="cp500"?><rss/>': "unsupported_feed_encoding",
            b"<rss><channel><item>": "malformed_feed_xml",
            b"<html><body>not a feed</body></html>": "unsupported_feed_format",
        }
        for raw, code in cases.items():
            with self.subTest(code=code), self.assertRaises(sources.SourceError) as caught:
                sources.parse_feed(raw)
            self.assertEqual(str(caught.exception), code)

    def test_rss_atom_and_doi_parsing(self):
        result = self.scan()
        atom = self.by_url(result, "https://news.example.org/2026/community-call")
        self.assertEqual((atom["kind"], atom["published_at"]), ("feed_item", "2026-10-04T10:00:00Z"))
        undated = self.by_url(result, "https://news.example.org/2026/undated")
        self.assertIn("feed_item_date_unparseable", undated["uncertainties"])
        rss = self.by_url(result, "https://blog.example.org/posts/speech-hpc")
        self.assertEqual(rss["summary"], "How we measured speech latency on HPC nodes.")
        self.assertEqual(rss["published_at"], "2026-10-02T08:00:00Z")
        self.assertNotIn("http://blog.example.org/posts/plain-http", json.dumps(result))
        paper = self.by_url(result, "https://doi.org/10.5555/reachweaver.fixture.2026")
        self.assertEqual((paper["kind"], paper["published_at"]), ("paper", "2026-09-20T00:00:00Z"))
        self.assertNotIn("jats", paper["summary"])
        with self.assertRaises(sources.SourceError) as caught:
            sources.parse_crossref(json.loads((FIXTURES / "crossref-work.json").read_text()), "10.5555/other")
        self.assertEqual(str(caught.exception), "doi_mismatch")

    def test_private_sources_are_never_read_or_emitted(self):
        result = self.scan()
        self.assertIn({"source_id": "private-repo", "reason": "private_source_excluded"}, result["skipped"])
        self.assertNotIn("PRIVATE-SECRET-ROADMAP", json.dumps(result))
        with mock.patch.object(sources, "_read_local", wraps=sources._read_local) as reader:
            self.scan()
        self.assertFalse(any("private-notes" in str(c.args[0]) for c in reader.call_args_list))

    def test_malformed_and_missing_inputs_fail_per_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "bad.json").write_text("{not json", encoding="utf-8")
            (base / "shape.json").write_text('{"message": "rate limited"}', encoding="utf-8")
            config = {"schema_version": 1, "sources": [
                {"id": "bad-json", "kind": "github_releases", "repo": "a/b", "sensitivity": "public", "fixture": "bad.json"},
                {"id": "bad-shape", "kind": "github_pulls", "repo": "a/b", "sensitivity": "public", "fixture": "shape.json"},
                {"id": "missing", "kind": "feed", "url": "https://x.example.org/f.xml", "sensitivity": "public", "fixture": "nope.xml"},
                {"id": "good", "kind": "doi", "doi": "10.5555/reachweaver.fixture.2026", "sensitivity": "public",
                 "fixture": str(FIXTURES / "crossref-work.json")},
            ]}
            with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
                result = sources.scan_sources(config, base_dir=base, now=NOW)
        self.assertEqual({e["source_id"]: e["error"] for e in result["errors"]},
                         {"bad-json": "malformed_json", "bad-shape": "unexpected_pulls_shape", "missing": "fixture_not_found"})
        self.assertEqual(len(result["signals"]), 1)

    def test_config_validation_requires_explicit_opt_in_and_no_secrets(self):
        bad = {"schema_version": 1, "github_token": "x", "sources": [
            {"id": "a", "kind": "github_releases", "repo": "not a repo", "sensitivity": "public"},
            {"id": "b", "kind": "feed", "url": "http://insecure.example.org/feed"},
            {"id": "c", "kind": "doi", "doi": "nope", "sensitivity": "public"},
        ]}
        issues = " | ".join(sources.validate_config(bad))
        for expected in ("secret-like", "repo must be owner/name", "public https feed URL",
                         "sensitivity must be declared", "doi must look like"):
            self.assertIn(expected, issues)
        self.assertTrue(sources.validate_config({"schema_version": 1, "sources": []}))
        self.assertEqual(sources.validate_config(json.loads((ROOT / "examples/sources.reachweaver.json").read_text())), [])

    def test_example_config_runs_offline_without_network(self):
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            code, out, err = run_cli("sources-scan", "--config", str(ROOT / "examples/sources.reachweaver.json"), "--top", "3")
        self.assertEqual(code, 0, err)
        self.assertIn("RESEARCH_ONLY", out)
        self.assertIn("offline_no_fixture_use_online=5", out)

    def test_mark_seen_writes_owner_only_state_and_suppresses_novelty(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "seen.json"
            code, out, err = run_cli("sources-scan", "--config", str(CONFIG), "--json", "--state", str(state), "--mark-seen")
            self.assertEqual(code, 1, err)
            first = json.loads(out)
            self.assertGreater(first["state_recorded"], 0)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            code, out, _ = run_cli("sources-scan", "--config", str(CONFIG), "--json", "--state", str(state))
            statuses = {s["status"] for s in json.loads(out)["signals"]}
            self.assertNotIn("candidate", statuses)
            code, _, err = run_cli("sources-scan", "--config", str(CONFIG), "--mark-seen")
            self.assertEqual(code, 2)
            self.assertIn("--mark-seen requires --state", err)


class OnlineMockTests(unittest.TestCase):
    """Online code paths with a mocked opener; tokens must never surface in output."""

    def setUp(self):
        self.requests = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "sources.json"
        self.config.write_text(json.dumps({"schema_version": 1, "keywords": ["speech"], "sources": [
            {"id": "rel", "kind": "github_releases", "repo": "example-org/example-tool", "sensitivity": "public"},
            {"id": "blog", "kind": "feed", "url": "https://blog.example.org/feed.xml", "sensitivity": "public"},
        ]}), encoding="utf-8")

    def fake(self, private=False, fail=None):
        def send(req):
            self.requests.append((req.full_url, req.get_method(), req.get_header("Authorization")))
            if fail is not None:
                raise fail
            if req.full_url == "https://api.github.com/repos/example-org/example-tool":
                return io.BytesIO(json.dumps({"private": private, "visibility": "private" if private else "public"}).encode())
            if req.full_url.startswith("https://api.github.com/repos/example-org/example-tool/releases"):
                return io.BytesIO((FIXTURES / "github-releases.json").read_bytes())
            if req.full_url == "https://blog.example.org/robots.txt":
                return io.BytesIO(b"User-agent: *\nDisallow: /private/\n")
            if req.full_url == "https://blog.example.org/feed.xml":
                return io.BytesIO((FIXTURES / "feed-rss.xml").read_bytes())
            raise AssertionError(req.full_url)
        return send

    def run_online(self, send, *extra):
        with mock.patch("reachweaver.publishers._send_http", side_effect=send), \
                mock.patch.dict(os.environ, {sources.TOKEN_ENV: TOKEN}):
            return run_cli("sources-scan", "--config", str(self.config), "--online", "--json", *extra)

    def test_token_only_sent_to_github_and_never_printed(self):
        code, out, err = self.run_online(self.fake())
        self.assertEqual(code, 0, err)
        self.assertNotIn(TOKEN, out + err)
        result = json.loads(out)
        self.assertTrue(result["online"] and result["github_authenticated"])
        self.assertTrue(all(method == "GET" for _, method, _ in self.requests))
        for url, _, auth in self.requests:
            if url.startswith("https://api.github.com/"):
                self.assertEqual(auth, f"Bearer {TOKEN}")
            else:
                self.assertIsNone(auth, "credentials must never be sent to feed hosts")
        self.assertTrue(any(s["kind"] == "feed_item" for s in result["signals"]))

    def test_http_errors_are_sanitized(self):
        error = HTTPError(f"https://api.github.com/?access_token={TOKEN}", 401, f"Bad credentials {TOKEN}", {}, None)
        code, out, err = self.run_online(self.fake(fail=error))
        self.assertEqual(code, 1)
        self.assertNotIn(TOKEN, out + err)
        self.assertEqual({e["error"] for e in json.loads(out)["errors"]}, {"http_401"})

    def test_private_repository_is_refused_even_with_token(self):
        code, out, _ = self.run_online(self.fake(private=True))
        self.assertEqual(code, 1)
        self.assertIn({"source_id": "rel", "error": "repository_not_confirmed_public"}, json.loads(out)["errors"])
        self.assertFalse(any("/releases" in url for url, _, _ in self.requests))

    def test_malformed_token_is_rejected_without_echo(self):
        # A control character is refused by accounts.credential itself (review F1), by name only.
        for bad, message in (("not a token  with spaces", "value not shown"),
                             ("not a token \n with spaces", "contains control characters")):
            with self.subTest(bad=bad), mock.patch("reachweaver.publishers._send_http", side_effect=no_network), \
                    mock.patch.dict(os.environ, {sources.TOKEN_ENV: bad}):
                code, out, err = run_cli("sources-scan", "--config", str(self.config), "--online")
            self.assertEqual(code, 2)
            self.assertNotIn("with spaces", out + err)
            self.assertIn(message, err)

    def test_oversized_response_is_bounded(self):
        with mock.patch("reachweaver.publishers._send_http", return_value=io.BytesIO(b"x" * 101)):
            with self.assertRaises(sources.SourceError) as caught:
                sources._fetch("https://blog.example.org/feed.xml", max_bytes=100)
        self.assertEqual(str(caught.exception), "response_too_large")

    def test_cli_rejects_invalid_config(self):
        self.config.write_text(json.dumps({"schema_version": 1, "sources": [{"id": "x", "kind": "rss"}]}))
        code, _, err = run_cli("sources-scan", "--config", str(self.config))
        self.assertEqual(code, 2)
        self.assertIn("kind must be one of", err)
        with self.assertRaises(ValidationError):
            sources.load_state(self.config)


class FakeResponse(io.BytesIO):
    """A mocked opener response that also carries headers (e.g. RFC 8288 Link)."""

    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}


class FakeWeb:
    """Routes mocked GETs by exact URL; records URL, method, User-Agent and Authorization."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests = []

    def __call__(self, req):
        self.requests.append({"url": req.full_url, "method": req.get_method(),
                              "ua": req.get_header("User-agent"), "auth": req.get_header("Authorization")})
        route = self.routes.get(req.full_url)
        if route is None:
            raise AssertionError(f"unexpected request: {req.full_url}")
        if isinstance(route, Exception):
            raise route
        body, headers = route if isinstance(route, tuple) else (route, {})
        return FakeResponse(body, headers)

    def urls(self):
        return [r["url"] for r in self.requests]


def http_error(url, code):
    return HTTPError(url, code, "error", {}, None)


REPO_META = json.dumps({"private": False, "visibility": "public"}).encode()
ISSUES_URL = ("https://api.github.com/repos/example-org/example-tool/issues"
              "?state=closed&sort=updated&direction=desc&per_page=30")


class IssueReaderTests(unittest.TestCase):
    def config(self, **extra):
        source = {"id": "tool-issues", "kind": "github_issues", "repo": "example-org/example-tool",
                  "sensitivity": "public", "labels": ["release-note", "announcement"], "fixture": "github-issues.json"}
        source.update(extra)
        return {"schema_version": 1, "keywords": ["kubernetes"], "sources": [source]}

    def test_only_closed_completed_issues_with_selected_labels(self):
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            result = sources.scan_sources(self.config(), base_dir=FIXTURES, now=NOW, top=100)
        self.assertEqual(sorted(s["url"].rsplit("/", 1)[-1] for s in result["signals"]), ["101", "102"])
        reasons = {s["url"].rsplit("/", 1)[-1]: s["reason"] for s in result["skipped"]}
        self.assertEqual(reasons, {"103": "not_closed_as_completed", "104": "not_closed_as_completed",
                                   "105": "label_not_selected", "106": "pull_request_not_issue",
                                   "107": "not_closed_as_completed"})
        self.assertTrue(any("/pull/106" in s["url"] for s in result["skipped"]))  # PR-shaped, never emitted
        top = next(s for s in result["signals"] if s["url"].endswith("/issues/101"))
        self.assertEqual((top["kind"], top["published_at"]), ("github_issue", "2026-10-05T10:00:00Z"))
        self.assertEqual(top["evidence_urls"], ["https://github.com/example-org/example-tool/issues/101"])
        self.assertNotIn("evil.example.net", json.dumps(result))  # html_url ignored, URL rebuilt from repo/number
        flagged = next(s for s in result["signals"] if s["url"].endswith("/issues/102"))
        self.assertIn("instruction_like_text_treated_as_data", flagged["uncertainties"])

    def test_labels_and_max_pages_are_validated(self):
        for labels in (None, [], ["ok", ""], ["x" * 51], ["l"] * 21):
            with self.subTest(labels=labels):
                issues = " | ".join(sources.validate_config(self.config(labels=labels)))
                self.assertIn("labels must list", issues)
        for pages in (0, 11, True, "3"):
            with self.subTest(pages=pages):
                self.assertIn("max_pages must be an integer", " | ".join(sources.validate_config(self.config(max_pages=pages))))
        self.assertEqual(sources.validate_config(self.config(max_pages=10)), [])
        feed = {"schema_version": 1, "sources": [{"id": "f", "kind": "feed", "url": "https://x.example.org/f.xml",
                                                  "sensitivity": "public", "max_pages": 2}]}
        self.assertIn("max_pages applies only to GitHub", " | ".join(sources.validate_config(feed)))


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.issues = json.loads((FIXTURES / "github-issues.json").read_text(encoding="utf-8"))

    def config(self, **extra):
        source = {"id": "tool-issues", "kind": "github_issues", "repo": "example-org/example-tool",
                  "sensitivity": "public", "labels": ["release-note", "announcement"]}
        source.update(extra)
        return {"schema_version": 1, "sources": [source]}

    def run_scan(self, web, config):
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            return sources.scan_sources(config, base_dir=FIXTURES, now=NOW, online=True, top=100,
                                        env={sources.TOKEN_ENV: TOKEN})

    def page(self, items, next_url=None):
        headers = {"Link": f'<{next_url}>; rel="next", <https://api.github.com/x?page=9>; rel="last"'} if next_url else {}
        return json.dumps(items).encode(), headers

    def test_link_header_parsing(self):
        self.assertEqual(sources.next_link('<https://api.github.com/a?page=2>; rel="next"'), "https://api.github.com/a?page=2")
        self.assertEqual(sources.next_link('<https://a.example/1>; rel="prev", <https://a.example/3>; rel="prev next"'),
                         "https://a.example/3")
        self.assertEqual(sources.next_link("<https://a.example/3>; title=\"x, y\"; rel=next"), "https://a.example/3")
        for value in (None, "", '<https://a.example/1>; rel="last"', "garbage", "x" * 9000):
            self.assertIsNone(sources.next_link(value))

    def test_next_link_must_stay_on_api_github_over_https(self):
        good = "https://api.github.com/repositories/123/issues?state=closed&page=2"
        self.assertTrue(sources.github_next_allowed(good))
        self.assertTrue(sources.github_next_allowed("https://api.github.com/repos/o/r/issues?page=2"))
        for bad in ("http://api.github.com/repos/o/r/issues?page=2", "https://evil.example.net/repos/o/r/issues",
                    "https://api.github.com.evil.example.net/repos/o/r", "https://user@api.github.com/repos/o/r",
                    "https://api.github.com:8443/repos/o/r", "https://api.github.com/user/repos?page=2",
                    "https://api.github.com/repos/o/r/../../user", "https://api.github.com/repos/o/r#x",
                    "https://api.github.com\\@evil.example.net/repos/o/r"):
            with self.subTest(url=bad):
                self.assertFalse(sources.github_next_allowed(bad))

    def test_pages_are_followed_with_token_and_capped_by_max_pages(self):
        page2 = "https://api.github.com/repositories/123/issues?state=closed&page=2"
        page3 = "https://api.github.com/repositories/123/issues?state=closed&page=3"
        web = FakeWeb({
            "https://api.github.com/repos/example-org/example-tool": REPO_META,
            ISSUES_URL: self.page(self.issues[:3], page2),
            page2: self.page(self.issues[3:], page3),
            page3: self.page([], "https://api.github.com/repositories/123/issues?state=closed&page=4"),
        })
        result = self.run_scan(web, self.config(max_pages=2))
        self.assertNotIn(page3, web.urls())  # capped at 2 list pages
        self.assertEqual(web.urls().count(page2), 1)
        self.assertIn({"source_id": "tool-issues", "reason": "pagination_max_pages_reached"}, result["skipped"])
        self.assertEqual(sorted(s["url"].rsplit("/", 1)[-1] for s in result["signals"]), ["101", "102"])
        reasons = {s.get("url", "").rsplit("/", 1)[-1]: s["reason"] for s in result["skipped"] if "url" in s}
        self.assertEqual(reasons["105"], "label_not_selected")  # item from page 2 was parsed
        self.assertTrue(all(r["auth"] == f"Bearer {TOKEN}" for r in web.requests))
        self.assertTrue(all(r["method"] == "GET" and r["ua"] == sources.USER_AGENT for r in web.requests))
        self.assertFalse(any(u.endswith("/robots.txt") for u in web.urls()), "API hosts are not crawled")
        # Default cap is 3 pages.
        web = FakeWeb({**web.routes, page3: self.page(self.issues[:1], "https://api.github.com/repos/o/r/issues?page=4")})
        self.run_scan(web, self.config())
        self.assertEqual(len([u for u in web.urls() if "issues" in u]), 3)

    def test_off_host_or_downgraded_next_link_is_refused_and_never_receives_token(self):
        for hostile in ("https://evil.example.net/repos/example-org/example-tool/issues?page=2",
                        "http://api.github.com/repos/example-org/example-tool/issues?page=2",
                        ISSUES_URL):  # a self-referencing loop is refused as well
            with self.subTest(next_url=hostile):
                web = FakeWeb({"https://api.github.com/repos/example-org/example-tool": REPO_META,
                               ISSUES_URL: self.page(self.issues, hostile)})
                result = self.run_scan(web, self.config())
                self.assertEqual(web.urls().count(ISSUES_URL), 1)
                self.assertFalse(any("evil.example.net" in u or u.startswith("http://") for u in web.urls()))
                self.assertIn({"source_id": "tool-issues", "reason": "pagination_next_link_refused"}, result["skipped"])
                self.assertEqual(len(result["signals"]), 2)  # first page still used

    def test_releases_paginate_and_fixture_offline_ignores_max_pages(self):
        url = "https://api.github.com/repos/example-org/example-tool/releases?per_page=30"
        page2 = "https://api.github.com/repos/example-org/example-tool/releases?per_page=30&page=2"
        releases = json.loads((FIXTURES / "github-releases.json").read_text(encoding="utf-8"))
        web = FakeWeb({"https://api.github.com/repos/example-org/example-tool": REPO_META,
                       url: self.page(releases[:1], page2), page2: self.page(releases[1:])})
        config = {"schema_version": 1, "sources": [{"id": "rel", "kind": "github_releases",
                                                    "repo": "example-org/example-tool", "sensitivity": "public"}]}
        result = self.run_scan(web, config)
        self.assertIn(page2, web.urls())
        self.assertIn("https://github.com/example-org/example-tool/releases/tag/v1.1.0", {s["url"] for s in result["signals"]})
        self.assertFalse([s for s in result["skipped"] if s["reason"].startswith("pagination")])

    def test_header_helper_keeps_bounds_and_sanitized_errors(self):
        with mock.patch("reachweaver.publishers._send_http", return_value=FakeResponse(b"x" * 101, {"Link": "<a>"})):
            with self.assertRaises(sources.SourceError) as caught:
                sources._fetch_response("https://api.github.com/x", max_bytes=100)
        self.assertEqual(str(caught.exception), "response_too_large")
        huge = {"Link": "<https://api.github.com/repos/o/r?page=2>; rel=\"next\"" + " " * 9000, "Set-Cookie": "s=1"}
        with mock.patch("reachweaver.publishers._send_http", return_value=FakeResponse(b"[]", huge)):
            body, headers = sources._fetch_response("https://api.github.com/x", max_bytes=100)
        self.assertEqual((body, headers), (b"[]", {}))  # oversized Link dropped; other headers never returned
        error = HTTPError(f"https://api.github.com/?access_token={TOKEN}", 502, f"boom {TOKEN}", {}, None)
        with mock.patch("reachweaver.publishers._send_http", side_effect=error):
            with self.assertRaises(sources.SourceError) as caught:
                sources._fetch_response("https://api.github.com/x", max_bytes=100)
        self.assertEqual(str(caught.exception), "http_502")
        with mock.patch("reachweaver.publishers._send_http", return_value=io.BytesIO(b"[]")):
            self.assertEqual(sources._fetch_response("https://api.github.com/x", max_bytes=100), (b"[]", {}))


class ChangelogReaderTests(unittest.TestCase):
    MD_URL = "https://raw.githubusercontent.com/example-org/example-tool/main/CHANGELOG.md"
    MD_PAGE = "https://github.com/example-org/example-tool/blob/main/CHANGELOG.md"
    HTML_URL = "https://www.example.org/changelog"

    def md_source(self, **extra):
        source = {"id": "tool-changelog", "kind": "changelog", "format": "markdown", "sensitivity": "public",
                  "url": self.MD_URL, "evidence_url": self.MD_PAGE, "project": "example-tool",
                  "fixture": "changelog-keepachangelog.md"}
        source.update(extra)
        return source

    def html_source(self, **extra):
        source = {"id": "site-changelog", "kind": "changelog", "format": "html", "sensitivity": "public",
                  "url": self.HTML_URL, "fixture": "changelog-page.html"}
        source.update(extra)
        return source

    def scan(self, *srcs, online=False, web=None):
        config = {"schema_version": 1, "keywords": ["speech", "kubernetes"], "sources": list(srcs)}
        with mock.patch("reachweaver.publishers._send_http", side_effect=web or no_network):
            return sources.scan_sources(config, base_dir=FIXTURES, now=NOW, online=online, top=100,
                                        env={sources.TOKEN_ENV: TOKEN})

    def test_keep_a_changelog_markdown_entries(self):
        result = self.scan(self.md_source())
        by_title = {s["title"]: s for s in result["signals"]}
        self.assertEqual(sorted(by_title), ["example-tool: 0.9.0", "example-tool: 1.0.0",
                                            "example-tool: 1.1.0", "example-tool: 1.2.0"])
        latest = by_title["example-tool: 1.2.0"]
        self.assertEqual(latest["kind"], "changelog_entry")
        self.assertEqual(latest["url"], "https://github.com/example-org/example-tool/compare/v1.1.0...v1.2.0")
        self.assertEqual(latest["published_at"], "2026-10-01T00:00:00Z")
        self.assertIn(self.MD_PAGE, latest["evidence_urls"])
        self.assertIn("GPU-aware autoscaling", latest["summary"])
        self.assertNotIn("9.9.9", json.dumps(result))  # headings inside fenced code are not entries
        self.assertNotIn("Work in progress", json.dumps(result))  # Unreleased is never announced
        self.assertIn("instruction_like_text_treated_as_data", by_title["example-tool: 1.1.0"]["uncertainties"])
        anchored = by_title["example-tool: 1.0.0"]
        self.assertEqual(anchored["url"], f"{self.MD_PAGE}#100---2026-08-20")
        self.assertEqual(anchored["evidence_urls"], [self.MD_PAGE])  # fragment-free, campaign-eligible
        self.assertIn("entry_url_is_page_anchor", anchored["uncertainties"])
        self.assertEqual(anchored["summary"], "First stable release. See the docs.")
        self.assertIn("changelog_entry_date_unparseable", by_title["example-tool: 0.9.0"]["uncertainties"])
        reasons = {s["reason"] for s in result["skipped"]}
        self.assertTrue({"unreleased_section_skipped", "yanked_release_skipped"} <= reasons)

    def test_html_article_entries_ignore_scripts_templates_and_off_host_links(self):
        result = self.scan(self.html_source())
        by_title = {s["title"]: s for s in result["signals"]}
        self.assertEqual(sorted(by_title), ["Maintenance notes", "Version 1.9", "Version 2.0: streaming speech recognition"])
        first = by_title["Version 2.0: streaming speech recognition"]
        self.assertEqual((first["url"], first["published_at"]), ("https://www.example.org/changelog/2-0", "2026-10-03T09:00:00Z"))
        self.assertEqual(first["summary"], "October 3, 2026 Adds streaming speech recognition & an HPC batch mode.")
        second = by_title["Version 1.9"]
        self.assertEqual(second["url"], "https://www.example.org/changelog#version-19")
        self.assertEqual(second["published_at"], "2026-09-10T00:00:00Z")
        dump = json.dumps(result)
        for absent in ("evil.example.net", "Injected from script", "hidden template text", "color: red"):
            self.assertNotIn(absent, dump)
        notes = by_title["Maintenance notes"]
        self.assertIn("instruction_like_text_treated_as_data", notes["uncertainties"])
        self.assertIn("changelog_entry_date_unparseable", notes["uncertainties"])

    def test_html_h2_sections_when_no_articles(self):
        raw = (b"<html><body><h1>News</h1><p>intro text</p>"
               b"<h2 id=\"release-3\">Release 3 - 2026-10-05</h2><p>Faster HPC scheduler.</p>"
               b"<h2>Release 2</h2><p>Published <time datetime=\"2026-09-01\">Sep 1</time>. Bug fixes.</p>"
               b"<h2><a href=\"javascript:alert(1)\">Release 1</a></h2><p>2026-08-01 first.</p></body></html>")
        records, _ = sources.parse_changelog_html(raw, page_url="https://www.example.org/news")
        self.assertEqual([r["url"] for r in records], ["https://www.example.org/news#release-3",
                                                      "https://www.example.org/news#release-2",
                                                      "https://www.example.org/news#release-1"])
        self.assertEqual([r["published"].date().isoformat() for r in records], ["2026-10-05", "2026-09-01", "2026-08-01"])
        self.assertNotIn("intro text", json.dumps([r["body"] for r in records]))
        for raw, code in ((b"<html><p>no entries</p></html>", "no_changelog_entries_found"),
                          (b"<h2>x</h2>\x00", "unsupported_changelog_encoding")):
            with self.subTest(code=code), self.assertRaises(sources.SourceError) as caught:
                sources.parse_changelog_html(raw, page_url="https://www.example.org/news")
            self.assertEqual(str(caught.exception), code)
        with self.assertRaises(sources.SourceError):
            sources.parse_changelog_markdown(b"# Title only\nno versions\n", page_url=self.MD_PAGE, project="p")

    def test_changelog_config_validation(self):
        def issues(**extra):
            return " | ".join(sources.validate_config({"schema_version": 1, "sources": [self.md_source(**extra)]}))
        self.assertEqual(issues(), "")
        self.assertIn("format must be markdown", issues(format="rst"))
        self.assertIn(f"must be https://{sources.RAW_GITHUB_HOST}", issues(url="https://example.org/CHANGELOG.md"))
        self.assertIn("public https URL", issues(url="http://raw.githubusercontent.com/o/r/main/CHANGELOG.md"))
        self.assertIn("needs url or evidence_url", issues(url=None, evidence_url=None))
        self.assertIn("evidence_url must be a public https URL", issues(evidence_url="https://127.0.0.1/x"))
        self.assertIn("project must be", issues(project=""))
        html_issues = " | ".join(sources.validate_config({"schema_version": 1, "sources": [self.html_source(url=None)]}))
        self.assertIn("url is required for an html changelog", html_issues)

    def test_online_changelog_checks_robots_and_never_sends_token(self):
        web = FakeWeb({
            "https://raw.githubusercontent.com/robots.txt": http_error("https://raw.githubusercontent.com/robots.txt", 404),
            self.MD_URL: (FIXTURES / "changelog-keepachangelog.md").read_bytes(),
            "https://www.example.org/robots.txt": b"User-agent: *\nAllow: /\n",
            self.HTML_URL: (FIXTURES / "changelog-page.html").read_bytes(),
        })
        result = self.scan(self.md_source(fixture=None), self.html_source(fixture=None), online=True, web=web)
        self.assertEqual(result["errors"], [])
        self.assertEqual(web.urls(), ["https://raw.githubusercontent.com/robots.txt", self.MD_URL,
                                      "https://www.example.org/robots.txt", self.HTML_URL])
        self.assertTrue(all(r["auth"] is None and r["ua"] == sources.USER_AGENT for r in web.requests))
        self.assertEqual(len({s["source_id"] for s in result["signals"]}), 2)


class RobotsTests(unittest.TestCase):
    FEED = "https://blog.example.org/feed.xml"

    def feeds(self, *urls):
        return {"schema_version": 1, "sources": [
            {"id": f"feed-{i}", "kind": "feed", "url": url, "sensitivity": "public"} for i, url in enumerate(urls)]}

    def scan(self, web, config):
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            return sources.scan_sources(config, base_dir=FIXTURES, now=NOW, online=True, top=100)

    def test_disallowed_feed_is_skipped_without_fetching_it(self):
        for robots in (b"User-agent: ReachWeaver\nDisallow: /feed.xml\n", b"User-agent: *\nDisallow: /\n"):
            with self.subTest(robots=robots):
                web = FakeWeb({"https://blog.example.org/robots.txt": robots})
                result = self.scan(web, self.feeds(self.FEED))
                self.assertEqual(web.urls(), ["https://blog.example.org/robots.txt"])
                self.assertIn({"source_id": "feed-0", "reason": "robots_disallowed"}, result["skipped"])
                self.assertEqual(result["errors"], [])

    def test_robots_fetched_once_per_host_and_cached_per_run(self):
        other = "https://blog.example.org/other.xml"
        third = "https://blog.example.org/atom.xml"
        web = FakeWeb({"https://blog.example.org/robots.txt": b"User-agent: *\nDisallow: /other.xml\n",
                       self.FEED: (FIXTURES / "feed-rss.xml").read_bytes(),
                       third: (FIXTURES / "feed-atom.xml").read_bytes()})
        result = self.scan(web, self.feeds(self.FEED, other, third))
        self.assertEqual(web.urls().count("https://blog.example.org/robots.txt"), 1)
        self.assertEqual(web.urls(), ["https://blog.example.org/robots.txt", self.FEED, third])
        self.assertNotIn(other, web.urls())
        self.assertIn({"source_id": "feed-1", "reason": "robots_disallowed"}, result["skipped"])
        self.assertTrue(any(s["source_id"] == "feed-0" for s in result["signals"]))

    def test_robots_status_handling(self):
        robots = "https://blog.example.org/robots.txt"
        cases = {404: None, 410: None, 401: "robots_disallowed", 403: "robots_disallowed",
                 500: "robots_unavailable", 503: "robots_unavailable", 301: "robots_unavailable"}
        for code, reason in cases.items():
            with self.subTest(code=code):
                web = FakeWeb({robots: http_error(robots, code), self.FEED: (FIXTURES / "feed-rss.xml").read_bytes()})
                result = self.scan(web, self.feeds(self.FEED))
                skipped = [s["reason"] for s in result["skipped"] if s["source_id"] == "feed-0"]
                if reason is None:
                    self.assertIn(self.FEED, web.urls())
                else:
                    self.assertEqual(skipped, [reason])
                    self.assertNotIn(self.FEED, web.urls())
        web = FakeWeb({robots: TimeoutError("slow")})
        self.assertIn({"source_id": "feed-0", "reason": "robots_unavailable"}, self.scan(web, self.feeds(self.FEED))["skipped"])
        web = FakeWeb({robots: b"x" * (sources.MAX_ROBOTS_BYTES + 1)})
        self.assertIn({"source_id": "feed-0", "reason": "robots_unavailable"}, self.scan(web, self.feeds(self.FEED))["skipped"])

    def test_robots_cache_unit(self):
        web = FakeWeb({"https://a.example.org/robots.txt": b"User-agent: *\nDisallow: /x\n"})
        cache = sources.RobotsCache()
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            self.assertIsNone(cache.skip_reason("https://a.example.org/y"))
            self.assertEqual(cache.skip_reason("https://a.example.org/x/1"), "robots_disallowed")
            self.assertEqual(cache.skip_reason("http://a.example.org/y"), "robots_unavailable")
        self.assertEqual(cache.fetches, 1)
        self.assertEqual(web.requests[0]["ua"], "ReachWeaver/0.3 (+https://github.com/MSKazemi/reachweaver)")


class CrossrefTests(unittest.TestCase):
    DOI = "10.5555/reachweaver.fixture.2026"

    def config(self, **extra):
        config = {"schema_version": 1, "sources": [{"id": "paper", "kind": "doi", "doi": self.DOI, "sensitivity": "public"}]}
        config.update(extra)
        return config

    def test_online_doi_fetches_crossref_without_default_email(self):
        url = "https://api.crossref.org/works/10.5555%2Freachweaver.fixture.2026"
        web = FakeWeb({url: (FIXTURES / "crossref-work.json").read_bytes()})
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            result = sources.scan_sources(self.config(), base_dir=FIXTURES, now=NOW, online=True,
                                          env={sources.TOKEN_ENV: TOKEN})
        self.assertEqual(web.urls(), [url])  # no robots.txt for an API, no mailto unless configured
        self.assertIsNone(web.requests[0]["auth"])
        self.assertEqual(result["signals"][0]["url"], f"https://doi.org/{self.DOI}")
        self.assertFalse(result["github_authenticated"])

    def test_owner_mailto_is_added_only_when_configured(self):
        url = "https://api.crossref.org/works/10.5555%2Freachweaver.fixture.2026?mailto=owner%40example.org"
        web = FakeWeb({url: (FIXTURES / "crossref-work.json").read_bytes()})
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            result = sources.scan_sources(self.config(crossref_mailto="owner@example.org"), base_dir=FIXTURES,
                                          now=NOW, online=True)
        self.assertEqual((web.urls(), len(result["signals"])), ([url], 1))
        self.assertNotIn("owner@example.org", json.dumps(result))
        for bad in ("not-an-email", "a@b", "x@example.org\r\nHost: evil", 42, "a" * 250 + "@example.org"):
            with self.subTest(mailto=bad):
                self.assertIn("crossref_mailto", " | ".join(sources.validate_config(self.config(crossref_mailto=bad))))

    def test_doi_validation_and_quoting(self):
        quoted = sources.crossref_url("10.5555/a?b#c/../d")
        self.assertEqual(quoted, "https://api.crossref.org/works/10.5555%2Fa%3Fb%23c%2F..%2Fd")
        for bad in ("10.1/x", "nope", "10.5555/a b", "10.5555/\x01x", "10.5555/x\u0085y", "11.5555/x", "10.5555/"):
            with self.subTest(doi=bad):
                with self.assertRaises(sources.SourceError):
                    sources.crossref_url(bad)
                self.assertIn("doi must look like", " | ".join(sources.validate_config(
                    {"schema_version": 1, "sources": [{"id": "p", "kind": "doi", "doi": bad, "sensitivity": "public"}]})))

    def test_crossref_errors_and_mismatch_are_sanitized(self):
        url = "https://api.crossref.org/works/10.5555%2Freachweaver.fixture.2026"
        for route, code in ((http_error(url, 404), "http_404"),
                            (json.dumps({"message": {"DOI": "10.5555/other"}}).encode(), "doi_mismatch")):
            web = FakeWeb({url: route})
            with self.subTest(code=code), mock.patch("reachweaver.publishers._send_http", side_effect=web):
                result = sources.scan_sources(self.config(), base_dir=FIXTURES, now=NOW, online=True)
                self.assertEqual(result["errors"], [{"source_id": "paper", "error": code}])


class HostileFixtureTests(unittest.TestCase):
    """Malformed items and prompt injection in feeds and changelogs (#9): inert, flagged, never actionable."""
    MD_URL = "https://raw.githubusercontent.com/example-org/example-tool/main/CHANGELOG.md"
    MD_PAGE = "https://github.com/example-org/example-tool/blob/main/CHANGELOG.md"

    def scan(self, *srcs):
        config = {"schema_version": 1, "sources": list(srcs)}
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            return sources.scan_sources(config, base_dir=FIXTURES, now=NOW, top=100)

    @staticmethod
    def feed(source_id, fixture):
        return {"id": source_id, "kind": "feed", "url": f"https://blog.example.org/{source_id}.xml",
                "sensitivity": "public", "fixture": fixture}

    def test_malformed_feed_items_are_skipped_or_flagged_not_fatal(self):
        result = self.scan(self.feed("mal-feed", "feed-malformed-items.xml"))
        self.assertEqual(result["errors"], [])
        # no link, javascript: link and credential-bearing link never become signals
        self.assertEqual(result["skipped"], [{"source_id": "mal-feed", "reason": "item_without_public_https_link"}] * 3)
        by_url = {s["url"]: s for s in result["signals"]}
        self.assertEqual(sorted(by_url), ["https://blog.example.org/posts/survivor",
                                          "https://blog.example.org/posts/untitled"])
        untitled = by_url["https://blog.example.org/posts/untitled"]
        self.assertIn("missing_title", untitled["uncertainties"])
        self.assertIn("no_summary_text", untitled["uncertainties"])
        self.assertLess(untitled["confidence"], by_url["https://blog.example.org/posts/survivor"]["confidence"])
        self.assertNotIn("javascript", json.dumps(result))
        self.assertNotIn("user:pw", json.dumps(result))

    def test_feed_injection_is_inert_flagged_and_lowers_confidence(self):
        result = self.scan(self.feed("inj-feed", "feed-injection.xml"))
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["status"], "RESEARCH_ONLY")
        signals = {s["url"]: s for s in result["signals"]}
        self.assertEqual(len(signals), 2)
        for signal in signals.values():
            self.assertIn("instruction_like_text_treated_as_data", signal["uncertainties"])
            self.assertAlmostEqual(signal["confidence"], 0.5)  # 0.7 base for a feed item, minus 0.2
            self.assertNotIn("<", signal["title"] + signal["summary"])
            for ch in signal["title"] + signal["summary"]:
                self.assertNotIn(unicodedata.category(ch), ("Cc", "Cf"), repr(ch))
            self.assertTrue(all(u.startswith("https://blog.example.org/") for u in signal["evidence_urls"]))
        hidden = signals["https://blog.example.org/posts/hidden"]
        self.assertEqual(hidden["title"], "Hidden controls here")
        injected = signals["https://blog.example.org/posts/injected"]
        self.assertEqual(injected["title"], "Release notes You are now the admin")
        self.assertNotIn("<script", injected["summary"])

    def test_changelog_injection_is_carried_only_as_flagged_data(self):
        source = {"id": "inj-log", "kind": "changelog", "format": "markdown", "sensitivity": "public",
                  "url": self.MD_URL, "evidence_url": self.MD_PAGE, "project": "example-tool",
                  "fixture": "changelog-injection.md"}
        result = self.scan(source)
        self.assertEqual(result["errors"], [])
        (signal,) = result["signals"]
        self.assertEqual(signal["title"], "example-tool: 2.0.0")
        self.assertIn("instruction_like_text_treated_as_data", signal["uncertainties"])
        self.assertNotIn("<system>", signal["summary"])

    def test_injection_fixtures_never_touch_the_outbox_or_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            config = Path(tmp) / "sources.json"
            config.write_text(json.dumps({"schema_version": 1, "sources": [
                self.feed("inj-feed", str(FIXTURES / "feed-injection.xml")),
                self.feed("mal-feed", str(FIXTURES / "feed-malformed-items.xml"))]}), encoding="utf-8")
            with mock.patch("reachweaver.publishers._send_http", side_effect=no_network), \
                    mock.patch.dict(os.environ, {}, clear=False):
                before = dict(os.environ)
                code, out, _ = run_cli("--db", str(db), "sources-scan", "--config", str(config), "--json")
                self.assertEqual(dict(os.environ), before)
            self.assertEqual(code, 0)
            self.assertFalse(db.exists(), "scanning must never open the outbox or create drafts")
            self.assertEqual(json.loads(out)["status"], "RESEARCH_ONLY")

    def test_more_malformed_feed_documents_fail_with_fixed_codes(self):
        cases = {
            b"": "malformed_feed_xml",
            b"\xef\xbb\xbf<rss><channel></rss>": "malformed_feed_xml",
            b"<rss version='2.0'><channel><item><title>x</title></item></channel></rss>x": "malformed_feed_xml",
            b"\x00<rss/>": "unsupported_feed_encoding",
            b"<feed xmlns='http://example.org/not-atom'/>": "unsupported_feed_format",
        }
        for raw, code in cases.items():
            with self.subTest(raw=raw[:30]), self.assertRaises(sources.SourceError) as caught:
                sources.parse_feed(raw)
            self.assertEqual(str(caught.exception), code)
        records, skipped = sources.parse_feed(b"<rss version='2.0'/>")  # no channel: empty, not an error
        self.assertEqual((records, skipped), ([], []))


if __name__ == "__main__":
    unittest.main()
