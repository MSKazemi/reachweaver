"""seo-content-check: name consistency and opt-in link previews (public issue #4).

No test contacts a host: every online path replaces ``publishers._send_http``.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver.attribution import build_utm_link
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.seo_content import (
    MAX_NAMES, MAX_PREVIEW_PAGES, check_file, check_items, load_items, load_names, name_findings, preview_findings,
)
from tests.test_landing import GOOD_HTML, FakeWeb

PAGE = "https://docs.example.org/guide"
HTML = {"Content-Type": "text/html; charset=utf-8"}
BARE = "<!doctype html><html lang='en'><head><title>Bare</title></head><body>hi</body></html>"


def entry(item_id, text, platform="bluesky", **extra):
    return {"id": item_id, "platform": platform, "text": text, **extra}


def codes(report, item_id):
    return [f["code"] for r in report["items"] if r["id"] == item_id for f in r["findings"]]


def no_network(req):
    raise AssertionError(f"offline mode contacted {req.full_url}")


class NameConsistencyTests(unittest.TestCase):
    def variants(self, text, name="ReachWeaver"):
        return [f["message"].split("'")[1] for f in name_findings({"text": text}, [name])]

    def test_canonical_spelling_and_possessive_are_fine(self):
        for text in ("ReachWeaver is useful", "ReachWeaver's roadmap", "Try ReachWeaver, then tell us."):
            self.assertEqual(self.variants(text), [], text)

    def test_case_and_spacing_variants_are_flagged(self):
        for text, wrong in (("Reachweaver is useful", "Reachweaver"), ("reach weaver is useful", "reach weaver"),
                            ("Reach-Weaver rocks", "Reach-Weaver"), ("REACHWEAVER!", "REACHWEAVER")):
            self.assertEqual(self.variants(text), [wrong], text)

    def test_urls_hashtags_handles_and_longer_words_are_ignored(self):
        for text in ("see #Reachweaver", "https://github.com/MSKazemi/reachweaver", "me@reachweaver.example",
                     "ReachWeavers plural", "unreachweaver"):
            self.assertEqual(self.variants(text), [], text)

    def test_mixed_use_flags_only_the_wrong_spelling(self):
        self.assertEqual(self.variants("Use ReachWeaver and reachweaver together"), ["reachweaver"])

    def test_single_word_names_are_matched_by_case_only(self):
        self.assertEqual(self.variants("python and Python", "Python"), ["python"])

    def test_findings_are_warnings_with_repo_policy_basis(self):
        report = check_items(load_items([entry("a", "reachweaver is useful")]), names=["ReachWeaver"])
        finding = report["items"][0]["findings"][0]
        self.assertEqual((finding["severity"], finding["code"], finding["basis"]), ("warn", "name_variant", "repo_policy"))
        self.assertEqual(report["counts"]["error"], 0)

    def test_no_names_means_no_name_findings(self):
        report = check_items(load_items([entry("a", "reachweaver is useful")]))
        self.assertNotIn("name_variant", codes(report, "a"))

    def test_names_from_the_file_and_the_flag_are_merged_and_validated(self):
        self.assertEqual(load_names({"names": ["ReachWeaver"]}, ["GitHub", "ReachWeaver"]), ["ReachWeaver", "GitHub"])
        self.assertEqual(load_names([entry("a", "x")]), [])
        for bad in ({"names": "ReachWeaver"}, {"names": ["x"]}, {"names": ["<script>"]}, {"names": [3]},
                    {"names": ["a" * 61]}, {"names": [f"Name{n}" for n in range(MAX_NAMES + 1)]}):
            with self.subTest(bad=str(bad)[:50]), self.assertRaises(ValidationError):
                load_names(bad)


class PreviewTests(unittest.TestCase):
    def items(self, *pairs):
        return load_items([entry(i, f"Read {link}", link=link) for i, link in pairs])

    def run_online(self, web, items, **kw):
        with mock.patch("reachweaver.publishers._send_http", side_effect=web):
            return check_items(items, online=True, **kw)

    def test_offline_default_makes_no_request_and_reports_no_network(self):
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            report = check_items(self.items(("a", build_utm_link(PAGE, platform="bluesky", campaign="c1"))))
        self.assertFalse(report["policy"]["network_access"])
        self.assertFalse([c for c in codes(report, "a") if c.startswith("preview_")])

    def test_healthy_page_has_no_preview_findings(self):
        web = FakeWeb({PAGE: (200, HTML, GOOD_HTML.encode())})
        report = self.run_online(web, self.items(("a", build_utm_link(PAGE, platform="bluesky", campaign="c1"))))
        self.assertTrue(report["policy"]["network_access"])
        self.assertEqual([c for c in codes(report, "a") if c.startswith("preview_")], [])
        self.assertEqual([r["url"] for r in web.requests if not r["url"].endswith("/robots.txt")], [PAGE])

    def test_missing_open_graph_tags_are_warnings(self):
        report = self.run_online(FakeWeb({PAGE: (200, HTML, BARE.encode())}), self.items(("a", PAGE)))
        found = {f["code"]: f["severity"] for r in report["items"] for f in r["findings"] if f["code"].startswith("preview_")}
        self.assertEqual(found, {"preview_og_title_missing": "warn", "preview_og_description_missing": "warn",
                                 "preview_og_image_missing": "warn"})

    def test_broken_link_is_an_error_and_robots_block_is_info(self):
        web = FakeWeb({PAGE: (404, {}, b""), "https://blocked.example.org/robots.txt": (200, {}, b"User-agent: *\nDisallow: /\n")})
        report = self.run_online(web, self.items(("a", PAGE), ("b", "https://blocked.example.org/p")))
        by_item = {r["id"]: {f["code"]: f["severity"] for f in r["findings"] if f["code"].startswith("preview_")}
                   for r in report["items"]}
        self.assertEqual(by_item["a"], {"preview_http_error": "error"})
        self.assertEqual(by_item["b"], {"preview_robots_disallowed": "info"})
        self.assertEqual(report["counts"]["error"], 1)

    def test_one_fetch_per_destination_across_items_and_utm_tags(self):
        web = FakeWeb({PAGE: (200, HTML, GOOD_HTML.encode())})
        self.run_online(web, self.items(("a", build_utm_link(PAGE, platform="bluesky", campaign="c1")),
                                        ("b", build_utm_link(PAGE, platform="linkedin", campaign="c1"))))
        self.assertEqual(len([r for r in web.requests if r["url"] == PAGE]), 1)

    def test_items_without_a_link_or_with_an_unusable_link_are_skipped(self):
        items = load_items([entry("a", "no link"), entry("b", "see http://insecure.example.org/x",
                                                         link="http://insecure.example.org/x")])
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
            self.assertEqual(preview_findings(items), {})

    def test_page_limit_leaves_the_rest_unchecked_with_an_info_finding(self):
        pairs = [(f"i{n}", f"https://docs.example.org/p{n}") for n in range(MAX_PREVIEW_PAGES + 1)]
        web = FakeWeb({link: (200, HTML, GOOD_HTML.encode()) for _, link in pairs})
        report = self.run_online(web, self.items(*pairs))
        self.assertIn("preview_not_checked_limit", codes(report, f"i{MAX_PREVIEW_PAGES}"))
        self.assertNotIn("preview_not_checked_limit", codes(report, "i0"))
        self.assertEqual(len([r for r in web.requests if not r["url"].endswith("/robots.txt")]), MAX_PREVIEW_PAGES)


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, doc):
        path = self.dir / "posts.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return str(path)

    def run_cli(self, *argv, web=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("reachweaver.publishers._send_http", side_effect=web or no_network), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["seo-content-check", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_name_flag_and_file_names_work_together(self):
        path = self.write({"items": [entry("a", "reachweaver and github together")], "names": ["ReachWeaver"]})
        code, out, _ = self.run_cli(path, "--name", "GitHub", "--json")
        self.assertEqual(code, 0)
        variants = [f["message"].split("'")[1] for r in json.loads(out)["items"] for f in r["findings"]
                    if f["code"] == "name_variant"]
        self.assertEqual(sorted(variants), ["github", "reachweaver"])

    def test_online_flag_adds_preview_findings_and_exit_code_2_on_a_broken_link(self):
        path = self.write({"items": [entry("a", f"Read {PAGE}", link=PAGE)]})
        code, out, _ = self.run_cli(path, "--online", web=FakeWeb({PAGE: (404, {}, b"")}))
        self.assertEqual(code, 2)
        self.assertIn("preview_http_error", out)
        code, out, _ = self.run_cli(path, "--online", web=FakeWeb({PAGE: (200, HTML, GOOD_HTML.encode())}))
        self.assertEqual(code, 0)

    def test_default_run_is_offline(self):
        path = self.write({"items": [entry("a", f"Read {PAGE}", link=PAGE)]})
        code, out, _ = self.run_cli(path, "--json")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["policy"]["network_access"])

    def test_bad_name_is_refused(self):
        path = self.write({"items": [entry("a", "x text here")]})
        self.assertEqual(self.run_cli(path, "--name", "<x>")[0], 2)

    def test_check_file_signature_stays_compatible(self):
        path = Path(self.write({"items": [entry("a", "Plain text only here")]}))
        self.assertEqual(check_file(path)["counts"]["error"], 0)


if __name__ == "__main__":
    unittest.main()
