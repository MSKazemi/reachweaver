"""ai-visibility: sourced crawler registry, RFC 9309 robots.txt verdicts, page signals, offline and mocked online.

No test contacts a host: every path replaces ``publishers._send_http``. Fixtures are synthetic.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

from reachweaver import ai_visibility as av
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import audit_profiles, load_registry as load_profile_registry

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ai_visibility"
PAGE = "https://docs.example.org/guide"
ROBOTS_URL = "https://docs.example.org/robots.txt"
LLMS_URL = "https://docs.example.org/llms.txt"
SCHEMA = "reachweaver.profile-post-audit.v1"


def no_network(*_args, **_kwargs):
    raise AssertionError("network used in an offline test")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None, status: int = 200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status


class FakeWeb:
    """Mocked GETs by exact URL; a route is (status, headers, body) and 3xx/4xx/5xx raise HTTPError."""

    def __init__(self, routes: dict):
        self.routes = dict(routes)
        self.requests: list[dict] = []

    def __call__(self, req):
        self.requests.append({"url": req.full_url, "method": req.get_method(), "ua": req.get_header("User-agent")})
        if req.full_url not in self.routes:
            raise AssertionError(f"unexpected request: {req.full_url}")
        status, headers, body = self.routes[req.full_url]
        if status >= 300:
            raise HTTPError(req.full_url, status, "mocked", headers, io.BytesIO(b""))
        return FakeResponse(body, headers, status)


def html(body: str, headers: dict | None = None):
    return 200, {"Content-Type": "text/html; charset=utf-8", **(headers or {})}, body.encode("utf-8")


def text(body: str):
    return 200, {"Content-Type": "text/plain"}, body.encode("utf-8")


def briefs_doc(*urls, status="active"):
    items = [{"page_id": f"p{i}", "canonical_url": url, "title": "T", "primary_keyword": f"kw {i}",
              "audience": "A", "campaign_type": "docs", "priority": 1, "status": status}
             for i, url in enumerate(urls or (PAGE,))]
    return {"schema_version": 1, "briefs": items, "campaigns": []}


def codes(findings):
    return sorted(f["code"] for f in findings)


class Files:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def write(self, name: str, data) -> Path:
        path = self.dir / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def close(self):
        self._tmp.cleanup()


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = av.load_registry()

    def test_bundled_registry_is_valid_sourced_and_dated(self):
        self.assertEqual(av.validate_registry(self.registry), [])
        self.assertFalse(self.registry["citation_guarantee"])
        evidence = [e for c in self.registry["crawlers"] for e in c["evidence"]]
        evidence += [e for s in self.registry["page_signals"] for e in s["evidence"]]
        evidence += [e for m in self.registry["measurement"] for e in m["evidence"]]
        self.assertGreaterEqual(len(evidence), 25)
        for item in evidence:
            self.assertTrue(item["source_url"].startswith("https://"), item)
            self.assertEqual(item["checked_on"], "2026-10-21", item)
            self.assertTrue(item["quote"].strip(), item)

    def test_crawler_roles_separate_search_training_and_user_fetch(self):
        roles = {c["token"]: c["role"] for c in self.registry["crawlers"]}
        self.assertEqual(roles, av.REQUIRED_ROLES)
        robots = {c["token"]: c["robots_txt"] for c in self.registry["crawlers"]}
        self.assertEqual(robots["ChatGPT-User"], "may_not_apply")
        self.assertEqual(robots["Perplexity-User"], "generally_ignored")

    def test_llms_txt_is_optional_and_never_a_ranking_lever(self):
        llms = next(s for s in self.registry["page_signals"] if s["id"] == "llms_txt")
        self.assertEqual(llms["label"], "optional; no major engine has confirmed it reads it")
        self.assertIs(llms["affects_ranking"], False)
        self.assertIn("does not use", llms["meaning"])
        for change in ({"affects_ranking": True}, {"status": "documented_control"}, {"label": "boosts AI ranking"}):
            broken = copy.deepcopy(self.registry)
            next(s for s in broken["page_signals"] if s["id"] == "llms_txt").update(change)
            self.assertTrue(av.validate_registry(broken), change)

    def test_validator_refuses_unsafe_or_unsourced_changes(self):
        cases = []
        broken = copy.deepcopy(self.registry)
        broken["citation_guarantee"] = True
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        next(c for c in broken["crawlers"] if c["token"] == "Google-Extended")["role"] = "search"
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        next(c for c in broken["crawlers"] if c["token"] == "ChatGPT-User")["robots_txt"] = "documented"
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["crawlers"] = [c for c in broken["crawlers"] if c["token"] != "OAI-SearchBot"]
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["crawlers"][0]["evidence"][0]["source_url"] = "http://example.org/"
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["crawlers"][0]["evidence"][0]["checked_on"] = "2027-01-01"
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["crawlers"][0]["evidence"] = []
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["crawlers"].append(dict(broken["crawlers"][0], token="googlebot"))
        cases.append(broken)
        broken = copy.deepcopy(self.registry)
        broken["decision_type"] = "rankings"
        cases.append(broken)
        for index, case in enumerate(cases):
            self.assertTrue(av.validate_registry(case), index)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(cases[0]), encoding="utf-8")
            with self.assertRaises(ValidationError):
                av.load_registry(path)


class RobotsTests(unittest.TestCase):
    def verdict(self, robots: str, token: str, path: str = "/guide") -> dict:
        return av.robots_verdict(av.parse_robots(robots), token, path)

    def test_named_group_beats_wildcard_and_match_is_case_insensitive(self):
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: oai-searchbot\nAllow: /\n"
        self.assertEqual(self.verdict(robots, "OAI-SearchBot"),
                         {"group": "named", "mentioned": True, "allowed": True, "rule": "allow: /"})
        self.assertEqual(self.verdict(robots, "GPTBot"),
                         {"group": "wildcard", "mentioned": False, "allowed": False, "rule": "disallow: /"})

    def test_no_group_and_empty_disallow_allow_everything(self):
        self.assertEqual(self.verdict("", "GPTBot")["group"], "none")
        self.assertTrue(self.verdict("", "GPTBot")["allowed"])
        self.assertTrue(self.verdict("User-agent: GPTBot\nDisallow:\n", "GPTBot")["allowed"])

    def test_longest_match_wins_and_allow_wins_a_tie(self):
        robots = "User-agent: X\nDisallow: /docs/\nAllow: /docs/public\n"
        self.assertTrue(self.verdict(robots, "X", "/docs/public/a")["allowed"])
        self.assertFalse(self.verdict(robots, "X", "/docs/private")["allowed"])
        self.assertTrue(self.verdict("User-agent: X\nDisallow: /a\nAllow: /a\n", "X", "/a")["allowed"])

    def test_wildcards_and_end_anchor(self):
        robots = "User-agent: X\nDisallow: /*.pdf$\nDisallow: /tmp*/\n"
        self.assertFalse(self.verdict(robots, "X", "/files/a.pdf")["allowed"])
        self.assertTrue(self.verdict(robots, "X", "/files/a.pdf?x=1")["allowed"])
        self.assertFalse(self.verdict(robots, "X", "/tmp1/x")["allowed"])

    def test_many_wildcards_match_in_linear_time(self):
        import time
        pattern = "/" + "a*" * 200 + "b"
        start = time.monotonic()
        self.assertFalse(av._pattern_matches(pattern, "/" + "a" * 5000))
        self.assertTrue(av._pattern_matches("/x*y*z$", "/x1y2z"))
        self.assertFalse(av._pattern_matches("/x*y*z$", "/x1y2z3"))
        self.assertTrue(av._pattern_matches("/x$", "/x"))
        self.assertFalse(av._pattern_matches("/x$", "/xy"))
        self.assertLess(time.monotonic() - start, 1.0)

    def test_groups_for_one_token_merge_and_shared_groups_apply_to_each_agent(self):
        robots = ("User-agent: X\nDisallow: /a\n\nUser-agent: Y\nUser-agent: X\nDisallow: /b\n"
                  "# comment\nSitemap: https://docs.example.org/s.xml\n")
        self.assertFalse(self.verdict(robots, "X", "/a")["allowed"])
        self.assertFalse(self.verdict(robots, "X", "/b")["allowed"])
        self.assertTrue(self.verdict(robots, "Y", "/a")["allowed"])
        self.assertFalse(self.verdict(robots, "Y", "/b")["allowed"])


class PageSignalTests(unittest.TestCase):
    def setUp(self):
        self.registry = av.load_registry()

    def analyze(self, body: str, header: str | None = None):
        return av.analyze_page(body, PAGE, self.registry, header)

    def test_fixture_page_signals(self):
        page, findings = self.analyze((FIXTURES / "page.html").read_text(encoding="utf-8"))
        self.assertEqual(page["title"], "Example guide")
        self.assertEqual(page["json_ld"], {"blocks": 1, "invalid": 0,
                                           "types": ["TechArticle", "Person", "Organization"]})
        self.assertEqual(page["fediverse_creator"], "@author@social.example.org")
        self.assertEqual(page["max_snippet"], "50")
        self.assertEqual(page["data_nosnippet_elements"], 1)
        self.assertEqual(codes(findings), ["bing_noarchive_nocache", "max_snippet"])
        self.assertTrue(all(f["severity"] == "info" for f in findings))

    def test_noindex_nosnippet_and_scoped_x_robots_tag(self):
        _, findings = self.analyze('<title>T</title><meta name="description" content="d">'
                                   '<link rel="canonical" href="/guide"><meta name="googlebot" content="nosnippet">')
        self.assertIn("nosnippet", codes(findings))
        self.assertNotIn("noindex", codes(findings))
        _, findings = self.analyze("<title>T</title>", header="googlebot: noindex, max-snippet: 20")
        self.assertIn("noindex", codes(findings))
        self.assertIn("max_snippet", codes(findings))
        _, findings = self.analyze('<title>T</title><meta name="robots" content="none">')
        self.assertIn("noindex", codes(findings))
        _, findings = self.analyze('<title>T</title><meta name="bingbot" content="noindex">')
        noindex = next(f for f in findings if f["code"] == "noindex")
        self.assertIn("(Bing)", noindex["message"])

    def test_missing_and_broken_signals(self):
        page, findings = self.analyze('<meta name="fediverse:creator" content="not a handle">'
                                      '<link rel="canonical" href="https://docs.example.org/other">'
                                      '<script type="application/ld+json">{broken</script>')
        self.assertEqual(codes(findings), ["canonical_mismatch", "fediverse_creator_invalid", "json_ld_invalid",
                                           "meta_description_missing", "title_missing"])
        self.assertEqual(page["json_ld"]["invalid"], 1)
        _, findings = self.analyze("<title>T</title>")
        self.assertIn("structured_data_missing", codes(findings))
        self.assertIn("fediverse_creator_missing", codes(findings))
        self.assertIn("canonical_link_missing", codes(findings))

    def test_page_text_is_cleaned_and_bounded(self):
        page, _ = self.analyze("<title>" + "x" * 5000 + "\x00</title>")
        self.assertLessEqual(len(page["title"]), 300)
        self.assertNotIn("\x00", page["title"])


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.files = Files()
        self.addCleanup(self.files.close)
        patcher = mock.patch("reachweaver.publishers._send_http", side_effect=no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fixture_report_per_crawler(self):
        result = av.ai_visibility(url=PAGE, html=FIXTURES / "page.html", robots=FIXTURES / "robots.txt")
        self.assertEqual((result["mode"], result["status"]), ("offline", "ok"))
        rows = {r["token"]: r for r in result["pages"][0]["crawlers"]}
        self.assertEqual(rows["GPTBot"]["status"], "blocked")
        self.assertEqual(rows["OAI-SearchBot"]["status"], "allowed")
        self.assertEqual(rows["OAI-SearchBot"]["rule"], "allow: /guide$")
        self.assertEqual(rows["Googlebot"]["status"], "not_mentioned_allowed")
        self.assertEqual(rows["Google-Extended"]["status"], "allowed")
        self.assertEqual(rows["PerplexityBot"]["group"], "named")
        finding = next(f for f in result["pages"][0]["findings"] if f["code"] == "training_crawler_blocked")
        self.assertEqual((finding["severity"], finding["crawler"]), ("info", "GPTBot"))
        self.assertEqual(result["pages"][0]["llms_txt"]["state"], "not_checked")
        self.assertEqual(result["limitation"]["checked_on"], "2026-10-21")
        self.assertEqual(len(result["measurement"]), 3)

    def test_blocking_a_search_crawler_is_a_warning_with_its_documented_meaning(self):
        robots = self.files.write("robots.txt", "User-agent: OAI-SearchBot\nDisallow: /\n")
        result = av.ai_visibility(url=PAGE, robots=robots)
        self.assertEqual(result["status"], "warning")
        finding = next(f for f in result["pages"][0]["findings"] if f["code"] == "search_crawler_blocked")
        self.assertIn("ChatGPT search answers", finding["message"])
        self.assertEqual(finding["source"], "https://platform.openai.com/docs/bots")
        self.assertIsNone(result["pages"][0]["page"])
        self.assertEqual(av.exit_code(result), 0)

    def test_html_only_leaves_crawlers_unchecked(self):
        result = av.ai_visibility(url=PAGE, html=FIXTURES / "page.html")
        self.assertEqual({r["status"] for r in result["pages"][0]["crawlers"]}, {"not_checked"})
        self.assertEqual(result["pages"][0]["robots"]["state"], "not_checked")

    def test_url_from_a_single_brief(self):
        briefs = self.files.write("briefs.json", briefs_doc(PAGE, "https://docs.example.org/other"))
        result = av.ai_visibility(briefs=briefs, page_ids=["p1"], robots=FIXTURES / "robots.txt")
        self.assertEqual(result["pages"][0]["url"], "https://docs.example.org/other")
        with self.assertRaises(ValidationError):
            av.ai_visibility(briefs=briefs, robots=FIXTURES / "robots.txt")

    def test_input_errors(self):
        for kwargs in ({"url": PAGE},
                       {"url": PAGE, "briefs": FIXTURES / "x.json", "html": FIXTURES / "page.html"},
                       {"html": FIXTURES / "page.html"},
                       {"url": "http://docs.example.org/", "html": FIXTURES / "page.html"},
                       {"url": PAGE, "html": FIXTURES / "missing.html"},
                       {"online": True},
                       {"online": True, "briefs": FIXTURES / "x.json", "html": FIXTURES / "page.html"},
                       {"online": True, "briefs": FIXTURES / "x.json", "url": PAGE}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                av.ai_visibility(**kwargs)


class OnlineTests(unittest.TestCase):
    def setUp(self):
        self.files = Files()
        self.addCleanup(self.files.close)

    def run_online(self, web: FakeWeb, *urls, **kwargs) -> dict:
        briefs = self.files.write("briefs.json", briefs_doc(*urls))
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            return av.ai_visibility(briefs=briefs, online=True, **kwargs)

    def test_one_get_each_for_robots_page_and_llms_on_the_own_host_only(self):
        page = (FIXTURES / "page.html").read_text(encoding="utf-8")
        web = FakeWeb({ROBOTS_URL: text("User-agent: GPTBot\nDisallow: /\n"), PAGE: html(page),
                       LLMS_URL: (404, {}, b"")})
        result = self.run_online(web)
        self.assertEqual([r["url"] for r in web.requests], [ROBOTS_URL, PAGE, LLMS_URL])
        self.assertTrue(all(r["method"] == "GET" and r["ua"].startswith("ReachWeaver/") for r in web.requests))
        entry = result["pages"][0]
        self.assertEqual(entry["robots"], {"source": ROBOTS_URL, "state": "ok", "fetch": "ok"})
        self.assertEqual(entry["llms_txt"]["state"], "absent")
        self.assertEqual(entry["page"]["json_ld"]["types"], ["TechArticle", "Person", "Organization"])
        self.assertEqual(result["status"], "ok")

    def test_llms_txt_is_info_only_and_fetched_once_per_origin(self):
        other = "https://docs.example.org/other"
        web = FakeWeb({ROBOTS_URL: text("User-agent: *\nAllow: /\n"), PAGE: html("<title>T</title>"),
                       other: html("<title>T</title>"), LLMS_URL: text("# llms")})
        result = self.run_online(web, PAGE, other)
        self.assertEqual(sum(1 for r in web.requests if r["url"] == LLMS_URL), 1)
        self.assertEqual(sum(1 for r in web.requests if r["url"] == ROBOTS_URL), 1)
        for entry in result["pages"]:
            self.assertEqual(entry["llms_txt"]["state"], "present")
            self.assertFalse(entry["llms_txt"]["affects_ranking"])
            self.assertNotIn("llms", " ".join(codes(entry["findings"])))

    def test_robots_disallowing_reachweaver_stops_the_page_fetch(self):
        web = FakeWeb({ROBOTS_URL: text("User-agent: *\nDisallow: /\n")})
        result = self.run_online(web)
        self.assertEqual([r["url"] for r in web.requests], [ROBOTS_URL])
        entry = result["pages"][0]
        self.assertIn("page_not_fetched", codes(entry["findings"]))
        self.assertIn("search_crawler_blocked", codes(entry["findings"]))
        self.assertEqual(entry["llms_txt"]["state"], "unknown_robots_disallowed")
        self.assertEqual(av.exit_code(result), 1)

    def test_unreachable_robots_means_complete_disallow(self):
        web = FakeWeb({ROBOTS_URL: (503, {}, b"")})
        result = self.run_online(web)
        entry = result["pages"][0]
        self.assertEqual(entry["robots"]["state"], "unreachable")
        self.assertEqual({r["status"] for r in entry["crawlers"]}, {"not_mentioned_blocked"})
        self.assertIn("robots_unreachable", codes(entry["findings"]))

    def test_missing_robots_allows_everything(self):
        web = FakeWeb({ROBOTS_URL: (404, {}, b""), PAGE: html("<title>T</title>"), LLMS_URL: (404, {}, b"")})
        result = self.run_online(web)
        entry = result["pages"][0]
        self.assertEqual(entry["robots"]["state"], "absent")
        self.assertEqual({r["status"] for r in entry["crawlers"]}, {"not_mentioned_allowed"})

    def test_off_domain_redirect_is_not_followed(self):
        web = FakeWeb({ROBOTS_URL: text("User-agent: *\nAllow: /\n"),
                       PAGE: (301, {"Location": "https://evil.example.net/guide"}, b""),
                       LLMS_URL: (404, {}, b"")})
        result = self.run_online(web)
        self.assertNotIn("https://evil.example.net/guide", [r["url"] for r in web.requests])
        finding = next(f for f in result["pages"][0]["findings"] if f["code"] == "page_not_fetched")
        self.assertIn("redirect_off_domain", finding["message"])

    def test_x_robots_tag_header_is_read(self):
        web = FakeWeb({ROBOTS_URL: text(""), PAGE: html("<title>T</title>", {"X-Robots-Tag": "noindex"}),
                       LLMS_URL: (404, {}, b"")})
        result = self.run_online(web)
        self.assertIn("noindex", codes(result["pages"][0]["findings"]))

    def test_only_brief_pages_and_known_page_ids(self):
        with self.assertRaises(ValidationError):
            self.run_online(FakeWeb({}), page_ids=["nope"])
        briefs = self.files.write("briefs.json", briefs_doc(PAGE, status="archived"))
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            result = av.ai_visibility(briefs=briefs, online=True)
        self.assertEqual(result["pages"], [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.files = Files()
        self.addCleanup(self.files.close)

    def run_cli(self, argv, web=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("reachweaver.publishers._send_http", side_effect=web or no_network), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_offline_text_and_json_open_no_database(self):
        db = self.files.dir / "never.sqlite"
        argv = ["--db", str(db), "ai-visibility", "--url", PAGE, "--html", str(FIXTURES / "page.html"),
                "--robots", str(FIXTURES / "robots.txt")]
        code, out, _ = self.run_cli(argv)
        self.assertEqual(code, 0)
        self.assertIn("ai-visibility (offline): OK", out)
        self.assertIn("optional; no major engine has confirmed it reads it", out)
        self.assertIn("GPTBot", out)
        code, out, _ = self.run_cli(argv + ["--json"])
        self.assertEqual(json.loads(out)["report"], "ai_visibility")
        self.assertFalse(db.exists())

    def test_bad_usage_is_a_validation_error(self):
        code, _, err = self.run_cli(["ai-visibility", "--online", "--url", PAGE])
        self.assertEqual(code, 2)
        self.assertIn("offline only", err)
        code, _, err = self.run_cli(["ai-visibility", "--url", PAGE])
        self.assertEqual(code, 2)


class ProfileAuditAdditionTests(unittest.TestCase):
    def test_google_search_entry_is_sourced_and_labelled_official(self):
        rows = {p["id"]: p for p in load_profile_registry()["platforms"]}
        entry = rows["google_search"]
        self.assertEqual(entry["profile_checks"][:2], ["search_profile_claimed", "search_console_platform_properties"])
        self.assertEqual(entry["evidence"]["checked_on"], "2026-10-21")
        for field, sources in entry["check_sources"].items():
            self.assertIn(field, entry["profile_checks"])
            for item in sources:
                self.assertEqual((item["label"], item["checked_on"]), ("official", "2026-10-21"))
                self.assertTrue(item["source_url"].startswith("https://"))
        self.assertIn("10,000", entry["eligibility_note"])

    def test_audit_reports_sources_and_the_eligibility_note(self):
        snapshot = {"schema_version": SCHEMA, "profiles": [{
            "platform": "google_search", "account_id": "me", "handle": "me",
            "fields": {"search_profile_claimed": False, "search_console_platform_properties": None,
                       "website": "https://docs.example.org/"}}]}
        report = audit_profiles(snapshot)["reports"][0]
        claimed = next(c for c in report["checklist"] if c["field"] == "search_profile_claimed")
        self.assertEqual(claimed["status"], "needs_fix")
        self.assertTrue(all(s["label"] == "official" for s in claimed["sources"]))
        self.assertIn("eligibility", [a["kind"] for a in report["alerts"]])

    def test_a_check_source_without_evidence_is_refused(self):
        from reachweaver import profile_audit
        registry = json.loads(profile_audit.REGISTRY.read_text(encoding="utf-8"))
        entry = next(p for p in registry["platforms"] if p["id"] == "google_search")
        entry["check_sources"]["website"][0]["label"] = "blog"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with mock.patch.object(profile_audit, "REGISTRY", path), self.assertRaises(ValidationError):
                profile_audit.load_registry()


if __name__ == "__main__":
    unittest.main()
