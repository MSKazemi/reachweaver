"""Skill install/update: checksum, safe unpack, version ordering, cache. No network is used."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from reachweaver import __version__, updater
from reachweaver.cli import main
from reachweaver.policy import ValidationError

SKILL = "---\nname: reachweaver\ndescription: x\n---\n"


def bundle(version="9.9.9", extra=None, skill=SKILL):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("reachweaver/SKILL.md", skill)
        z.writestr("reachweaver/scripts/run.py", "")
        z.writestr("reachweaver/scripts/reachweaver/__init__.py", f'__version__ = "{version}"\n')
        for name, content in (extra or {}).items():
            z.writestr(name, content)
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest() + "  skill.zip\n"


def release(tag="v9.9.9"):
    return json.dumps({"tag_name": tag, "html_url": "https://github.com/MSKazemi/reachweaver/releases/tag/" + tag,
                       "assets": [{"name": "skill.zip", "browser_download_url": "https://github.com/x/skill.zip"},
                                  {"name": "skill.zip.sha256", "browser_download_url": "https://github.com/x/sha"}]}).encode()


class InstallZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "skills" / "reachweaver"
        env = mock.patch.object(updater, "cache_dir", return_value=Path(self.tmp.name) / "cache")
        env.start()
        self.addCleanup(env.stop)

    def test_installs_and_keeps_previous_copy_outside_skills(self):
        self.dest.mkdir(parents=True)
        (self.dest / "old.txt").write_text("old")
        data, sha = bundle()
        out = updater.install_zip(data, sha, self.dest, replace_version="0.2.0")
        self.assertEqual(out["installed_version"], "9.9.9")
        self.assertTrue((self.dest / "SKILL.md").is_file())
        self.assertFalse((self.dest / "old.txt").exists())
        self.assertTrue((Path(out["previous_copy"]) / "old.txt").is_file())
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], ["reachweaver"])  # no staging/dup skill

    def test_checksum_mismatch_installs_nothing(self):
        data, _ = bundle()
        with self.assertRaises(ValidationError):
            updater.install_zip(data, "0" * 64, self.dest)
        self.assertFalse(self.dest.exists())

    def test_refuses_traversal_symlink_foreign_and_wrong_skill(self):
        for extra, skill in (({"reachweaver/../evil": "x"}, SKILL), ({"other/x": "x"}, SKILL),
                             ({}, "---\nname: other\n---\n")):
            data, sha = bundle(extra=extra, skill=skill)
            with self.subTest(extra=list(extra), skill=skill[:20]), self.assertRaises(ValidationError):
                updater.install_zip(data, sha, self.dest)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            info = zipfile.ZipInfo("reachweaver/link")
            info.external_attr = 0o120777 << 16
            z.writestr(info, "/etc/passwd")
        data = buf.getvalue()
        with self.assertRaises(ValidationError):
            updater.install_zip(data, hashlib.sha256(data).hexdigest(), self.dest)
        self.assertFalse(self.dest.exists())

    def test_not_newer_is_refused_and_existing_install_untouched(self):
        self.dest.mkdir(parents=True)
        (self.dest / "keep").write_text("1")
        data, sha = bundle("0.2.0")
        with self.assertRaises(ValidationError):
            updater.install_zip(data, sha, self.dest, replace_version="0.2.0")
        self.assertTrue((self.dest / "keep").is_file())


class CheckTests(unittest.TestCase):
    def test_version_ordering_and_rejects_prerelease(self):
        self.assertGreater(updater.parse_version("v0.10.0"), updater.parse_version("0.9.9"))
        with self.assertRaises(ValidationError):
            updater.parse_version("1.0.0-rc1")

    def test_check_reports_update_and_cache_avoids_second_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, calls = Path(tmp) / "c.json", []
            def get(url):
                calls.append(url)
                return release()
            first = updater.check(get=get, cache_path=path, now=1000.0)
            self.assertTrue(first["update_available"])
            second = updater.check(get=get, use_cache=True, cache_path=path, now=1100.0)
            self.assertEqual((len(calls), second["source"]), (1, "cache"))
            updater.check(get=get, use_cache=True, cache_path=path, now=1000.0 + updater.CHECK_TTL_SECONDS + 1)
            self.assertEqual(len(calls), 2)

    def test_same_version_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = updater.check(get=lambda u: release("v" + __version__), cache_path=Path(tmp) / "c.json")
            self.assertFalse(r["update_available"])

    def test_release_without_assets_or_draft_is_refused(self):
        with self.assertRaises(ValidationError):
            updater.fetch_latest(lambda u: json.dumps({"tag_name": "v1.0.0", "assets": []}).encode())
        with self.assertRaises(ValidationError):
            updater.fetch_latest(lambda u: json.dumps({"tag_name": "v1.0.0", "prerelease": True}).encode())

    def test_only_https_github_hosts(self):
        for url in ("http://github.com/x", "https://evil.example/x", "https://github.com.evil.example/x"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                updater._check_url(url)
        updater._check_url("https://objects.githubusercontent.com/x")


class PluginTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_manifests_match_package_version_and_validate_shape(self):
        plugin = json.loads((self.ROOT / "plugin/.claude-plugin/plugin.json").read_text())
        market = json.loads((self.ROOT / ".claude-plugin/marketplace.json").read_text())
        entry = market["plugins"][0]
        self.assertEqual({plugin["version"], entry["version"]}, {__version__})
        self.assertEqual((plugin["name"], entry["name"]), ("reachweaver", "reachweaver"))
        # The plugin is a lean subfolder: the directory validator times out on the whole repository.
        self.assertEqual(entry["source"], "./plugin")
        self.assertTrue((self.ROOT / "plugin/skills/reachweaver/SKILL.md").is_file())
        self.assertFalse((self.ROOT / ".claude-plugin/plugin.json").exists())

    def test_plugin_folder_is_lean_and_listing_ready(self):
        plugin = self.ROOT / "plugin"
        files = [p for p in plugin.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        self.assertLess(len(files), 300)
        self.assertFalse([p for p in files if p.stat().st_size > 256 * 1024], "files over 256 KiB are held by the directory")
        images = sorted(p.relative_to(plugin).as_posix() for p in files
                        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
        self.assertEqual(images, [".claude-plugin/icon.png"])
        for name in ("tests", "docs", "reachweaver"):
            self.assertFalse((plugin / name).exists(), name)
        self.assertGreaterEqual(len((plugin / "README.md").read_text().split()), 40)
        self.assertTrue((plugin / "LICENSE").is_file())
        for name in ("DISCLAIMER.md", "NOTICE"):
            self.assertTrue((plugin / name).is_file(), name)
        self.assertIn("no warranty", (plugin / "README.md").read_text().lower())
        header = (plugin / ".claude-plugin/icon.png").read_bytes()[:24]  # listing icon: square PNG, 512 to 2048 px
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        self.assertEqual(width, height)
        self.assertTrue(512 <= width <= 2048)
        self.assertLess((plugin / ".claude-plugin/icon.png").stat().st_size, 2 * 1024 * 1024)

    def test_plugin_skill_mirror_matches_the_canonical_bundle(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sync_skill", self.ROOT / "scripts/sync_skill.py")
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        self.assertEqual(sync.check(), [], "run python3 scripts/sync_skill.py")

    def test_session_hook_only_checks_and_script_exists(self):
        hooks = json.loads((self.ROOT / "plugin/hooks/hooks.json").read_text())["hooks"]["SessionStart"]
        command = hooks[0]["hooks"][0]["command"]
        self.assertTrue(command.endswith('run.py" update --notice'))
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/reachweaver/scripts/run.py", command)
        self.assertNotIn("--apply", command)
        self.assertTrue((self.ROOT / "plugin/skills/reachweaver/scripts/run.py").is_file())

    def test_plugin_install_is_never_applied_in_place(self):
        fake = Path("/home/u/.claude/plugins/cache/reachweaver/reachweaver/0.2.0/x.py")
        self.assertEqual(updater.install_kind(fake), "plugin")
        with mock.patch.object(updater, "install_kind", return_value="plugin"), \
                self.assertRaises(ValidationError) as caught:
            updater.apply_update({"_assets": {}, "installed": "0.2.0"})
        self.assertIn("/plugin update", str(caught.exception))
        self.assertIn("/plugin update", updater.notice_line(
            {"latest": "9.9.9", "installed": "0.2.0", "install_kind": "plugin"}))


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.object(updater, "cache_dir", return_value=Path(tmp.name))
        env.start()
        self.addCleanup(env.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue()

    def test_version_is_offline(self):
        code, out = self.run_cli("version", "--json")
        self.assertEqual((code, json.loads(out)["version"]), (0, __version__))

    def test_notice_silent_on_failure_and_when_opted_out(self):
        with mock.patch.object(updater, "http_get", side_effect=OSError("offline")):
            self.assertEqual(self.run_cli("update", "--notice"), (0, ""))
        (updater.cache_dir() / updater.OPT_OUT_FILE).parent.mkdir(parents=True, exist_ok=True)
        (updater.cache_dir() / updater.OPT_OUT_FILE).touch()
        with mock.patch.object(updater, "http_get", side_effect=AssertionError("network")):
            self.assertEqual(self.run_cli("update", "--notice"), (0, ""))

    def test_notice_prints_one_line_when_newer(self):
        with mock.patch.object(updater, "http_get", return_value=release()):
            code, out = self.run_cli("update", "--notice")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("9.9.9", out)

    def test_check_without_release_is_friendly(self):
        import urllib.error
        err = urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)
        with mock.patch.object(updater, "http_get", side_effect=err), \
                contextlib.redirect_stderr(io.StringIO()) as e:
            code, out = self.run_cli("update", "--check")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("No release found", e.getvalue())
        self.assertNotIn("HTTP Error", e.getvalue())

    def test_apply_refuses_symlink_and_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "clone" / ".claude" / "skills" / "reachweaver"
            real.mkdir(parents=True)
            (Path(tmp) / "clone" / ".git").mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real)
            for target in (real, link):
                with self.subTest(target=target.name), self.assertRaises(ValidationError):
                    updater.apply_update({"_assets": {}, "installed": "0.2.0"}, dest=target)
            self.assertTrue(real.is_dir())

    def test_apply_without_yes_changes_nothing(self):
        with mock.patch.object(updater, "http_get", return_value=release()), \
                mock.patch.object(updater, "apply_update", side_effect=AssertionError("applied")):
            code, out = self.run_cli("update", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("--yes", out)


if __name__ == "__main__":
    unittest.main()
