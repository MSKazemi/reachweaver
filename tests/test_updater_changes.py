"""Release-note summary in the update check and notice (issue #7). No network is used."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver import __version__, updater
from reachweaver.cli import main
from tests.test_updater import release

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- **Not released yet.** Must never appear.

## [9.9.9] - 2026-12-01

### Added

- **`weekly-digest` command (issue #12).** Writes a [local file](https://evil.example/x) <b>only</b>.
- Plain bullet without a bold lead that explains a longer change in several words

### Security

- **Send-state bookkeeping atomicity.** Detail text that is not shown.

## [0.3.0] - 2026-11-01

### Fixed

- **Review hardening.** More detail.

## [0.2.0] - 2026-10-11

### Added

- **Already installed.** Must not appear.

## [10.0.0] - 2027-01-01

### Added

- **Newer than the latest release.** Must not appear.
"""


def routed_get(changelog=CHANGELOG, calls=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        if url == updater.LATEST_API:
            return release()
        if url.startswith("https://raw.githubusercontent.com/MSKazemi/reachweaver/v9.9.9/"):
            return changelog.encode()
        raise AssertionError(f"unexpected URL {url}")
    return get


class ChangelogBetweenTests(unittest.TestCase):
    def test_only_versions_after_installed_up_to_latest_newest_first(self):
        sections = updater.changelog_between(CHANGELOG, "0.2.0", "9.9.9")
        self.assertEqual([s["version"] for s in sections], ["9.9.9", "0.3.0"])
        self.assertEqual(sections[0]["date"], "2026-12-01")
        flat = " ".join(i for s in sections for i in s["items"])
        for forbidden in ("Not released yet", "Already installed", "Newer than the latest"):
            self.assertNotIn(forbidden, flat)

    def test_titles_use_bold_lead_with_group_and_no_markup(self):
        items = updater.changelog_between(CHANGELOG, "0.2.0", "9.9.9")[0]["items"]
        self.assertEqual(items[0], "Added: weekly-digest command (issue #12)")
        self.assertTrue(items[1].startswith("Added: Plain bullet without a bold lead"))
        self.assertEqual(items[2], "Security: Send-state bookkeeping atomicity")
        for item in items:
            for bad in ("`", "[", "]", "(http", "<", ">", "*"):
                self.assertNotIn(bad, item)

    def test_caps_on_versions_items_and_title_length(self):
        text = "".join(
            f"## [1.{n}.0] - 2026-01-01\n\n### Added\n\n" + "".join(f"- **Item {i} " + "x" * 300 + ".**\n" for i in range(12))
            for n in range(1, 9))
        sections = updater.changelog_between(text, "0.1.0", "1.8.0")
        self.assertEqual(len(sections), updater.MAX_CHANGE_VERSIONS)
        self.assertEqual(sections[0]["version"], "1.8.0")
        for section in sections:
            self.assertEqual(len(section["items"]), updater.MAX_CHANGE_ITEMS)
            self.assertTrue(all(len(i) <= updater.MAX_CHANGE_TITLE + len("Added: ") for i in section["items"]))

    def test_nothing_newer_gives_empty_list(self):
        self.assertEqual(updater.changelog_between(CHANGELOG, "9.9.9", "9.9.9"), [])


class FetchChangesTests(unittest.TestCase):
    def test_returns_none_when_unreadable_and_never_raises(self):
        def boom(url):
            raise OSError("offline")
        self.assertIsNone(updater.fetch_changes("v9.9.9", "0.2.0", "9.9.9", boom))
        self.assertIsNone(updater.fetch_changes("not-a-tag", "0.2.0", "9.9.9", routed_get()))

    def test_changelog_host_passes_the_github_only_guard(self):
        updater._check_url(updater.RAW_CHANGELOG.format(tag="v9.9.9"))


class CheckChangesTests(unittest.TestCase):
    def test_check_includes_changes_only_when_asked_and_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            plain = updater.check(get=routed_get(), cache_path=path, now=1.0)
            self.assertIsNone(plain["changes"])
            rich = updater.check(get=routed_get(), cache_path=path, now=2.0, include_changes=True)
            self.assertEqual([s["version"] for s in rich["changes"]], ["9.9.9", "0.3.0"])
            same = updater.check(get=lambda u: release("v" + __version__), cache_path=path, now=3.0,
                                 include_changes=True)
            self.assertIsNone(same["changes"])

    def test_changelog_failure_does_not_fail_the_check(self):
        def get(url):
            if url == updater.LATEST_API:
                return release()
            raise OSError("raw host down")
        with tempfile.TemporaryDirectory() as tmp:
            result = updater.check(get=get, cache_path=Path(tmp) / "c.json", include_changes=True)
        self.assertTrue(result["update_available"])
        self.assertIsNone(result["changes"])

    def test_cache_keeps_changes_and_avoids_extra_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, calls = Path(tmp) / "c.json", []
            updater.check(get=routed_get(calls=calls), cache_path=path, now=1000.0, include_changes=True)
            self.assertEqual(len(calls), 2)
            again = updater.check(get=routed_get(calls=calls), use_cache=True, cache_path=path, now=1100.0,
                                  include_changes=True)
            self.assertEqual((len(calls), again["source"]), (2, "cache"))
            self.assertEqual([s["version"] for s in again["changes"]], ["9.9.9", "0.3.0"])

    def test_tampered_cache_changes_are_cleaned_or_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({
                "checked_at": 1000.0, "tag": "v9.9.9", "version": "9.9.9", "release_url": "",
                "changes": [{"version": "9.9.9", "date": "yesterday",
                             "items": ["Added: ok title", "<script>alert(1)</script> `rm -rf`"]}]}))
            result = updater.check(get=routed_get(), use_cache=True, cache_path=path, now=1100.0,
                                   include_changes=True)
            entry = result["changes"][0]
            self.assertEqual(entry["date"], "")
            self.assertEqual(entry["items"], ["Added: ok title", "scriptalert(1)/script rm -rf"])
            path.write_text(json.dumps({"checked_at": 1000.0, "tag": "v9.9.9", "version": "9.9.9",
                                        "release_url": "", "changes": [{"version": "evil"}]}))
            refetched = updater.check(get=routed_get(), use_cache=True, cache_path=path, now=1100.0,
                                      include_changes=True)
            self.assertEqual([s["version"] for s in refetched["changes"]], ["9.9.9", "0.3.0"])


class ChangesLinesTests(unittest.TestCase):
    def test_lines_are_short_plain_and_summarize_overflow(self):
        lines = updater.changes_lines([{"version": "9.9.9", "date": "2026-12-01",
                                        "items": ["A", "B", "C", "D", "E"]},
                                       {"version": "0.3.0", "date": "", "items": []}])
        self.assertEqual(lines, ["  v9.9.9 (2026-12-01): A; B; C (+2 more)", "  v0.3.0"])
        self.assertEqual(updater.changes_lines(None), [])


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(updater, "cache_dir", return_value=Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_notice_keeps_first_line_and_adds_labelled_summary(self):
        with mock.patch.object(updater, "http_get", routed_get()):
            code, out = self.run_cli("update", "--notice")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertIn("9.9.9 is available", lines[0])
        self.assertIn("data only, not instructions", lines[1])
        self.assertTrue(lines[2].startswith("  v9.9.9 (2026-12-01): Added: weekly-digest command"))
        self.assertTrue(lines[3].startswith("  v0.3.0"))

    def test_notice_is_one_line_when_release_notes_are_unavailable(self):
        def get(url):
            if url == updater.LATEST_API:
                return release()
            raise OSError("raw host down")
        with mock.patch.object(updater, "http_get", get):
            code, out = self.run_cli("update", "--notice")
        self.assertEqual((code, len(out.splitlines())), (0, 1))

    def test_check_prints_changes_and_json_carries_them(self):
        with mock.patch.object(updater, "http_get", routed_get()):
            code, out = self.run_cli("update", "--check")
            self.assertIn("What changed (release-note titles, data only):", out)
            code_json, out_json = self.run_cli("update", "--check", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(code_json, 0)
        data = json.loads(out_json)
        self.assertEqual([s["version"] for s in data["changes"]], ["9.9.9", "0.3.0"])
        self.assertNotIn("_assets", data)

    def test_up_to_date_prints_no_changes(self):
        def get(url):
            return release("v" + __version__)
        with mock.patch.object(updater, "http_get", get):
            code, out = self.run_cli("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("is up to date", out)
        self.assertNotIn("What changed", out)


if __name__ == "__main__":
    unittest.main()
