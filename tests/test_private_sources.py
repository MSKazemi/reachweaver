"""Opt-in private GitHub sources (issue #9 DRAFT): three opt-ins, private marking, no exfiltration.

Every HTTP call is mocked; no real network, account or token is used.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from reachweaver import briefs, private_sources, sources
from reachweaver.accounts import load_accounts
from reachweaver.attribution import build_utm_link
from reachweaver.campaigns import export_campaign, read_json, stage_campaign, validate_campaign
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.preview import build_preview
from reachweaver.privacy import export_data
from reachweaver.storage import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/sources"
BRAND = ROOT / "examples/brand.reachweaver.json"
ACCOUNTS = ROOT / "config/accounts.example.json"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
TOKEN = "ghp" + "_FAKEtokenFORtestsONLY1234567890abcd"
REPO = "example-org/private-tool"
META_URL = f"https://api.github.com/repos/{REPO}"
RELEASES_URL = f"{META_URL}/releases?per_page=30"
PRIVATE_URL = f"https://github.com/{REPO}/releases/tag/v9.9.9"
ROADMAP_TITLE = "PRIVATE-SECRET-ROADMAP"
PUBLIC_DEST = "https://example.org/speech"
PRIVATE_META = json.dumps({"private": True, "visibility": "private",
                           "permissions": {"admin": False, "push": True, "pull": True}}).encode()
PUBLIC_META = json.dumps({"private": False, "visibility": "public"}).encode()


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def no_network(req):
    raise AssertionError(f"unexpected network request: {req.full_url}")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict | None = None):
        super().__init__(body)
        self.headers = headers or {}


class FakeGitHub:
    """Routes mocked GETs by exact URL and records URL, method and whether a token was sent."""

    def __init__(self, meta: bytes = PRIVATE_META, headers: dict | None = None):
        self.routes = {META_URL: (meta, headers or {}),
                       RELEASES_URL: ((FIXTURES / "private-notes.json").read_bytes(), {})}
        self.requests: list[tuple[str, str, str | None]] = []

    def __call__(self, req):
        self.requests.append((req.full_url, req.get_method(), req.get_header("Authorization")))
        if req.full_url not in self.routes:
            raise AssertionError(f"unexpected request: {req.full_url}")
        body, headers = self.routes[req.full_url]
        return FakeResponse(body, headers)


def source(**overrides) -> dict:
    entry = {"id": "private-repo", "kind": "github_releases", "repo": REPO,
             "visibility": "private", "sensitivity": "private"}
    entry.update(overrides)
    return {key: value for key, value in entry.items() if value is not None}


def config(*entries) -> dict:
    return {"schema_version": 1, "keywords": ["speech"], "sources": list(entries) or [source()]}


class OptInTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, cfg: dict) -> Path:
        path = self.tmp / "sources.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def cli(self, cfg: dict, web, *extra, env=None):
        with mock.patch("reachweaver.publishers._send_http", side_effect=web), \
                mock.patch.dict(os.environ, env if env is not None else {sources.TOKEN_ENV: TOKEN}):
            return run_cli("sources-scan", "--config", str(self.write(cfg)), "--online", "--json", *extra)

    def assert_nothing_private(self, *texts):
        for text in texts:
            self.assertNotIn(ROADMAP_TITLE, text)
            self.assertNotIn("confidential", text)
            self.assertNotIn(PRIVATE_URL, text)

    # --------------------------------------------------------------- refusals

    def test_without_visibility_opt_in_a_private_repo_is_refused_and_nothing_is_stored(self):
        web = FakeGitHub()
        state = self.tmp / "state.json"
        code, out, err = self.cli(config(source(visibility=None, sensitivity="public")), web,
                                  "--allow-private", "--state", str(state), "--mark-seen")
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertIn({"source_id": "private-repo", "error": "repository_not_confirmed_public"}, result["errors"])
        self.assertEqual([u for u, _, _ in web.requests], [META_URL], "no list endpoint may be read")
        self.assertEqual(result["signals"], [])
        self.assert_nothing_private(out, err, state.read_text(encoding="utf-8"))

    def test_without_sensitivity_opt_in_the_config_is_refused_before_any_request(self):
        code, out, err = self.cli(config(source(sensitivity="public")), no_network, "--allow-private")
        self.assertEqual(code, 2)
        self.assertIn("visibility private requires sensitivity private", err)
        # With --json the only stdout is the stable error document (error_codes.py): no scan result.
        self.assertEqual(json.loads(out)["error"]["code"], "validation_error")
        self.assertEqual(set(json.loads(out)), {"error"})

    def test_without_allow_private_flag_nothing_is_read(self):
        web = FakeGitHub()
        code, out, err = self.cli(config(), web)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertIn({"source_id": "private-repo", "reason": "private_source_needs_allow_private"}, result["skipped"])
        self.assertEqual(web.requests, [])
        self.assertFalse(result["private_sources_allowed"])
        self.assert_nothing_private(out, err)

    def test_sensitivity_private_without_visibility_stays_excluded_even_with_flag(self):
        web = FakeGitHub()
        code, out, _ = self.cli(config(source(visibility=None)), web, "--allow-private")
        self.assertEqual(code, 0)
        self.assertIn({"source_id": "private-repo", "reason": "private_source_excluded"}, json.loads(out)["skipped"])
        self.assertEqual(web.requests, [])

    def test_private_offline_fixture_also_needs_the_flag(self):
        cfg = config(source(fixture="private-notes.json"))
        with mock.patch("reachweaver.publishers._send_http", side_effect=no_network), \
                mock.patch.object(sources, "_read_local", wraps=sources._read_local) as reader:
            result = sources.scan_sources(cfg, base_dir=FIXTURES, now=NOW)
        self.assertFalse(any("private-notes" in str(c.args[0]) for c in reader.call_args_list))
        self.assertIn({"source_id": "private-repo", "reason": "private_source_needs_allow_private"}, result["skipped"])

    def test_all_opt_ins_but_no_token_makes_no_request(self):
        code, out, _ = self.cli(config(), no_network, "--allow-private", env={})
        self.assertEqual(code, 1)
        self.assertIn({"source_id": "private-repo", "error": "private_source_requires_token"}, json.loads(out)["errors"])

    def test_declared_private_but_repository_public_is_refused(self):
        web = FakeGitHub(meta=PUBLIC_META)
        code, out, _ = self.cli(config(), web, "--allow-private")
        self.assertEqual(code, 1)
        self.assertIn({"source_id": "private-repo", "error": "repository_not_confirmed_private"}, json.loads(out)["errors"])
        self.assertEqual([u for u, _, _ in web.requests], [META_URL])

    def test_visibility_validation(self):
        for entry, message in ((source(visibility="secret"), "visibility must be public or private"),
                               ({"id": "blog", "kind": "feed", "url": "https://blog.example.org/feed.xml",
                                 "visibility": "private", "sensitivity": "private"},
                                "supported only for GitHub API sources")):
            with self.subTest(message=message):
                self.assertTrue(any(message in i for i in sources.validate_config(config(entry))))

    # --------------------------------------------------------------- all three opt-ins

    def test_all_three_opt_ins_mark_every_signal_private(self):
        web = FakeGitHub(headers={"X-OAuth-Scopes": "repo, read:org"})
        state = self.tmp / "state.json"
        code, out, err = self.cli(config(), web, "--allow-private", "--state", str(state), "--mark-seen")
        self.assertEqual(code, 0, err)
        self.assertNotIn(TOKEN, out + err)
        self.assertTrue(all(method == "GET" for _, method, _ in web.requests))
        self.assertTrue(all(auth == f"Bearer {TOKEN}" for _, _, auth in web.requests))
        result = json.loads(out)
        self.assertTrue(result["private_sources_allowed"])
        self.assertEqual(result["counts"]["private"], 1)
        signal = result["signals"][0]
        self.assertEqual((signal["visibility"], signal["private"], signal["publishable"]), ("private", True, False))
        self.assertIn("private_note", result)
        self.assertIn({"source_id": "private-repo", "reason": "repository_permissions_report_write_access"},
                      result["skipped"])
        self.assertFalse(any("url" in item for item in result["skipped"] if item["source_id"] == "private-repo"))
        # The local seen-state file records the private signal by id only.
        saved = json.loads(state.read_text(encoding="utf-8"))["seen"]
        self.assertEqual(len(saved), 1)
        self.assertEqual((saved[0]["id"], saved[0]["private"]), (signal["id"], True))
        self.assertEqual(set(saved[0]), {"id", "private", "recorded_at"})
        self.assert_nothing_private(state.read_text(encoding="utf-8"))
        # Classic-token write scopes are warned about, by scope name only.
        scope = result["token_scope"]
        self.assertTrue(scope["reported"])
        self.assertEqual(scope["write_capable"], ["repo"])
        self.assertTrue(scope["warnings"][0].startswith("token_has_write_scopes"))

    def test_text_output_withholds_private_summary_and_never_prints_token(self):
        web = FakeGitHub()
        with mock.patch("reachweaver.publishers._send_http", side_effect=web), \
                mock.patch.dict(os.environ, {sources.TOKEN_ENV: TOKEN}):
            code, out, err = run_cli("sources-scan", "--config", str(self.write(config())), "--online",
                                     "--allow-private")
        self.assertEqual(code, 0, err)
        self.assertIn("PRIVATE (not public evidence)", out)
        self.assertIn("summary withheld", out)
        self.assertNotIn("confidential", out)
        self.assertIn("TOKEN WARNING: token_scope_unknown", out)
        self.assertNotIn(TOKEN, out + err)

    def test_token_scope_report_units(self):
        report = private_sources.token_scope_report("read:org, read:user", authenticated=True)
        self.assertEqual((report["write_capable"], report["warnings"]), ([], []))
        report = private_sources.token_scope_report("public_repo, workflow, bad scope!", authenticated=True)
        self.assertEqual(report["write_capable"], ["public_repo", "workflow"])
        self.assertTrue(private_sources.token_scope_report(None, authenticated=True)["warnings"][0]
                        .startswith("token_scope_unknown"))
        self.assertEqual(private_sources.token_scope_report(None, authenticated=False)["warnings"], [])

    def test_repo_of_url(self):
        for url in (PRIVATE_URL, f"https://api.github.com/repos/{REPO}/issues/1",
                    f"https://raw.githubusercontent.com/{REPO}/main/CHANGELOG.md",
                    "https://GitHub.com/Example-Org/Private-Tool.git"):
            self.assertEqual(private_sources.repo_of_url(url), REPO, url)
        for url in ("https://github.com/example-org", "https://example.org/example-org/private-tool",
                    "https://api.github.com/users/x", None, "https://github.com/a/.."):
            self.assertIsNone(private_sources.repo_of_url(url), url)


def private_scan() -> dict:
    with mock.patch("reachweaver.publishers._send_http", side_effect=no_network):
        scan = sources.scan_sources(config(source(fixture="private-notes.json")), base_dir=FIXTURES,
                                    now=NOW, allow_private=True)
    return json.loads(json.dumps(scan))


def human_review(campaign: dict, *, publishable: bool, link: str = PUBLIC_DEST) -> dict:
    """Simulate the human edit of a private-signal scaffold with a public destination."""
    done = copy.deepcopy(campaign)
    done["audience"] = "Platform engineers who run speech workloads"
    for item in done["evidence"]:
        item["claim"] = "The linked source describes a streaming speech pipeline for Kubernetes clusters."
        item["verification"] = "primary_source_checked"
        item["checked_at"] = "2026-10-01T09:00:00+00:00"
        item["publishable"] = publishable
    for variant in done["variants"]:
        variant["angle"] = f"What changed for {variant['platform']} readers"
        variant["cta"] = "Read the write-up"
        variant["text"] = (f"A streaming speech pipeline for Kubernetes clusters, written up for "
                           f"{variant['platform']} readers. Details: {link}")
    return done


class CampaignGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = private_scan()
        cls.brand = read_json(BRAND)
        cls.accounts = load_accounts(ACCOUNTS)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def brief(self):
        signal = self.scan["signals"][0]
        self.assertTrue(signal["private"])
        return briefs.build_brief(self.scan, signal["id"], self.brand, self.accounts, now=NOW)

    def test_brief_marks_evidence_private_and_never_uses_private_url_as_link(self):
        result = self.brief()
        campaign = result["campaign"]
        self.assertTrue(result["signal"]["private"])
        self.assertNotIn("cta_url", campaign)
        self.assertTrue(campaign["variants"])
        for variant in campaign["variants"]:
            self.assertNotIn("cta_url", variant)
            self.assertNotIn(PRIVATE_URL, variant["text"])
            self.assertIn("PRIVATE SOURCE", variant["cta_note"])
        for item in campaign["evidence"]:
            self.assertEqual((item["source_visibility"], item["publishable"]), ("private", False))
        self.assertIn("## PRIVATE SOURCE: not public evidence", result["markdown"])
        self.assertTrue(any("comes from a private source" in e for e in result["validation"]["errors"]))

    def test_tampered_scan_without_private_flag_is_still_private_when_visibility_is_odd(self):
        scan = copy.deepcopy(self.scan)
        scan["signals"][0].pop("private")
        scan["signals"][0]["visibility"] = "Public "
        signal = briefs.normalize_signal(scan["signals"][0])
        self.assertTrue(signal["private"])

    def test_campaign_refuses_private_evidence_until_publishable(self):
        blocked = human_review(self.brief()["campaign"], publishable=False)
        errors, _ = validate_campaign(blocked, brand=self.brand, accounts=self.accounts)
        self.assertTrue(errors)
        self.assertTrue(all("comes from a private source" in e for e in errors), errors)
        with self.assertRaisesRegex(ValidationError, "private source"):
            stage_campaign(None, blocked, self.brand, self.accounts)
        allowed = human_review(self.brief()["campaign"], publishable=True)
        self.assertEqual(validate_campaign(allowed, brand=self.brand, accounts=self.accounts)[0], [])
        for bad in ("yes", 1):
            with self.subTest(publishable=bad):
                odd = copy.deepcopy(allowed)
                odd["evidence"][0]["publishable"] = bad
                errors, _ = validate_campaign(odd, brand=self.brand, accounts=self.accounts)
                self.assertTrue(any("publishable must be true/false" in e for e in errors))

    def test_private_repository_links_are_refused_even_when_publishable(self):
        for link in (PRIVATE_URL, f"https://github.com/{REPO}", f"https://raw.githubusercontent.com/{REPO}/main/x"):
            with self.subTest(link=link):
                campaign = human_review(self.brief()["campaign"], publishable=True, link=link)
                errors, _ = validate_campaign(campaign, brand=self.brand, accounts=self.accounts)
                self.assertTrue(any("private source repository" in e for e in errors), errors)
        campaign = human_review(self.brief()["campaign"], publishable=True)
        campaign["cta_url"] = PRIVATE_URL
        errors, _ = validate_campaign(campaign, brand=self.brand, accounts=self.accounts)
        self.assertTrue(any(e.startswith("cta_url points into a private") for e in errors))

    def test_utm_tagging_refuses_private_repository_urls(self):
        repos = {REPO}
        with self.assertRaisesRegex(ValidationError, "private source repository"):
            build_utm_link(PRIVATE_URL, platform="bluesky", campaign="c", private_repos=repos)
        self.assertIn("utm_source=bluesky", build_utm_link("https://github.com/example-org/example-tool",
                                                           platform="bluesky", campaign="c", private_repos=repos))
        campaign = human_review(self.brief()["campaign"], publishable=True, link=PRIVATE_URL)
        preview = build_preview(campaign, self.brand, self.accounts, utm=True)
        links = [link for v in preview["variants"] for p in v["pieces"] for link in p["utm"]["links"]]
        self.assertTrue(links)
        self.assertTrue(all(link["utm"] is None and "private source repository" in link["error"] for link in links))
        cfg = self.tmp / "sources.json"
        cfg.write_text(json.dumps(config()), encoding="utf-8")
        code, out, err = run_cli("utm-link", PRIVATE_URL, "--platform", "bluesky", "--campaign", "c",
                                 "--sources", str(cfg))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("never public links", err)
        code, out, _ = run_cli("utm-link", PUBLIC_DEST, "--platform", "bluesky", "--campaign", "c",
                               "--sources", str(cfg))
        self.assertEqual(code, 0)
        self.assertIn("utm_campaign=c", out)

    def test_staging_and_exports_never_carry_private_text_or_urls(self):
        campaign = human_review(self.brief()["campaign"], publishable=True)
        with connect(str(self.tmp / "state.sqlite")) as db:
            staged = stage_campaign(db, campaign, self.brand, self.accounts)
            self.assertTrue(all(item["created"] for item in staged))
            self.assertEqual({row[0] for row in db.execute("SELECT source_url FROM posts")}, {""})
            exported = json.dumps(export_data(db))
        self.assertNotIn("confidential", exported)
        self.assertNotIn(PRIVATE_URL, exported)
        self.assertNotIn(ROADMAP_TITLE, exported)
        self.assertIn(private_sources.REDACTED_PRIVATE, exported)
        out = self.tmp / "pack"
        out.mkdir()
        export_campaign(campaign, self.brand, out)
        pack = (out / "campaign.json").read_text(encoding="utf-8")
        self.assertNotIn("confidential", pack)
        self.assertNotIn(PRIVATE_URL, pack)
        self.assertIn(private_sources.REDACTED_PRIVATE, pack)

    def test_data_export_redacts_legacy_private_source_url(self):
        campaign = human_review(self.brief()["campaign"], publishable=True)
        with connect(str(self.tmp / "state.sqlite")) as db:
            stage_campaign(db, campaign, self.brand, self.accounts)
            db.execute("UPDATE posts SET source_url=?", (PRIVATE_URL,))  # a row staged before this guard
            data = export_data(db)
        self.assertEqual({p["source_url"] for p in data["tables"]["posts"]}, {private_sources.REDACTED_PRIVATE})
        self.assertGreaterEqual(data["private_source_rows_redacted"], 1)


if __name__ == "__main__":
    unittest.main()
