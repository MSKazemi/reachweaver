"""scripts/check_package.py: what makes a wheel or sdist acceptable (offline; builds nothing).

The fixtures are tiny archives built from a fake checkout, so each rule is shown by one defect: a registry missing from
the wheel, a runtime dependency, a private file in the sdist, a version that does not match, and so on.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_package", ROOT / "scripts" / "check_package.py")
check_package = importlib.util.module_from_spec(spec)
sys.modules["check_package"] = check_package
spec.loader.exec_module(check_package)

VERSION = "1.2.3"
METADATA = """Metadata-Version: 2.4
Name: reachweaver
Version: 1.2.3
License-Expression: Apache-2.0
Project-URL: Homepage, https://github.com/MSKazemi/reachweaver
Requires-Python: >=3.11
License-File: LICENSE

Long description.
"""
ENTRY = "[console_scripts]\nreachweaver = reachweaver.cli:main\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.root = self.top / "checkout"
        (self.root / "reachweaver").mkdir(parents=True)
        (self.root / "reachweaver" / "__init__.py").write_text(f'__version__ = "{VERSION}"\n', encoding="utf-8")
        (self.root / "reachweaver" / "cli.py").write_text("def main(): ...\n", encoding="utf-8")
        (self.root / "reachweaver" / "platform_registry.json").write_text("{}", encoding="utf-8")
        (self.root / "pyproject.toml").write_text(f'[project]\nname = "reachweaver"\nversion = "{VERSION}"\n', encoding="utf-8")
        self.dist = self.top / "dist"
        self.dist.mkdir()

    def wheel(self, *, drop=(), add=None, metadata=METADATA, entry=ENTRY, name=f"reachweaver-{VERSION}-py3-none-any.whl"):
        members = {f"reachweaver/{f}": "x" for f in check_package.runtime_files(self.root)}
        info = f"reachweaver-{VERSION}.dist-info"
        members.update({f"{info}/licenses/{n}": "license" for n in check_package.LICENSES})
        members.update({f"{info}/METADATA": metadata, f"{info}/entry_points.txt": entry, f"{info}/WHEEL": "w"})
        members.update(add or {})
        for gone in drop:
            members.pop(gone, None)
        path = self.dist / name
        with zipfile.ZipFile(path, "w") as archive:
            for member, text in members.items():
                archive.writestr(member, text)
        return path

    def sdist(self, *, drop=(), add=None, name=f"reachweaver-{VERSION}.tar.gz", prefix=f"reachweaver-{VERSION}/"):
        members = {"PKG-INFO": "x", "pyproject.toml": "x", "README.md": "x", **{n: "x" for n in check_package.LICENSES},
                   **{f"reachweaver/{f}": "x" for f in check_package.runtime_files(self.root)}}
        members.update(add or {})
        for gone in drop:
            members.pop(gone, None)
        path = self.dist / name
        with tarfile.open(path, "w:gz") as archive:
            for member, text in members.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(prefix + member)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return path

    def good(self):
        self.wheel()
        self.sdist()

    def errors(self):
        return check_package.check(self.dist, self.root)


class WheelRules(Fixture):
    def test_a_correct_pair_passes(self):
        self.good()
        self.assertEqual(self.errors(), [])

    def test_a_missing_bundled_registry_is_caught(self):
        self.wheel(drop=("reachweaver/platform_registry.json",))
        self.sdist()
        self.assertIn("wheel is missing reachweaver/platform_registry.json", self.errors())

    def test_a_file_the_checkout_does_not_have_is_caught(self):
        self.wheel(add={"reachweaver/stray.py": "x"})
        self.sdist()
        self.assertTrue(any("not in the checkout: reachweaver/stray.py" in e for e in self.errors()))

    def test_missing_license_files_entry_point_and_wrong_metadata_are_caught(self):
        self.sdist()
        self.wheel(drop=(f"reachweaver-{VERSION}.dist-info/licenses/NOTICE",), entry="[console_scripts]\n")
        errors = self.errors()
        self.assertIn("wheel is missing the license file NOTICE", errors)
        self.assertIn("entry point 'reachweaver = reachweaver.cli:main' is missing", errors)
        (self.dist / f"reachweaver-{VERSION}-py3-none-any.whl").unlink()
        self.wheel(metadata=METADATA.replace("Apache-2.0", "MIT").replace(">=3.11", ">=3.9"))
        errors = self.errors()
        self.assertTrue(any("License-Expression" in e for e in errors))
        self.assertTrue(any("Requires-Python" in e for e in errors))

    def test_a_runtime_dependency_is_caught(self):
        self.sdist()
        self.wheel(metadata=METADATA.replace("Requires-Python", "Requires-Dist: requests>=2\nRequires-Python"))
        self.assertTrue(any("runtime dependencies" in e for e in self.errors()))

    def test_private_paths_in_the_wheel_are_caught(self):
        self.sdist()
        for bad in ("docs/internal/OWNER-ACTIONS.md", ".claude/settings.json", "state/reachweaver.sqlite",
                    "config/accounts.json", ".env.reachweaver", "secrets.env", "docs/SECURITY-REVIEW-PROPOSAL-x.md",
                    "state/approval-ledger.jsonl", ".reachweaver-workspace", "reachweaver/__pycache__/cli.cpython-312.pyc"):
            with self.subTest(path=bad):
                for old in self.dist.glob("*.whl"):
                    old.unlink()
                self.wheel(add={bad: "x"})
                self.assertTrue(any("private or unwanted path" in e and bad in e for e in self.errors()), bad)

    def test_the_example_files_are_not_mistaken_for_private_ones(self):
        self.sdist(add={"secrets.env.example": "NAMES=\n", "config/accounts.example.json": "{}"})
        self.wheel()
        self.assertEqual(self.errors(), [])


class SdistRules(Fixture):
    def test_missing_essentials_are_caught(self):
        self.wheel()
        self.sdist(drop=("PKG-INFO", "LICENSE", "reachweaver/platform_registry.json"))
        errors = self.errors()
        for needed in ("PKG-INFO", "LICENSE", "reachweaver/platform_registry.json"):
            self.assertIn(f"sdist is missing {needed}", errors)

    def test_private_paths_in_the_sdist_are_caught(self):
        self.wheel()
        for bad in ("docs/internal/x.md", "state/x.sqlite", ".env", ".claude/skills/x", "media/clip.mp4".replace("media/", "logs/"),
                    "docs/SECURITY-REVIEW-PROPOSAL-y.md"):
            with self.subTest(path=bad):
                for old in self.dist.glob("*.tar.gz"):
                    old.unlink()
                self.sdist(add={bad: "x"})
                self.assertTrue(any("sdist contains a private or unwanted path" in e and bad in e for e in self.errors()), bad)

    def test_tests_are_allowed_in_the_sdist(self):
        self.wheel()
        self.sdist(add={"tests/test_x.py": "x"})
        self.assertEqual(self.errors(), [])

    def test_members_outside_the_versioned_folder_are_caught(self):
        self.wheel()
        self.sdist(prefix="other-9.9.9/")
        self.assertTrue(any("not all under" in e for e in self.errors()))


class VersionAndCount(Fixture):
    def test_versions_must_agree_everywhere(self):
        self.good()
        (self.root / "pyproject.toml").write_text('[project]\nname = "reachweaver"\nversion = "9.9.9"\n', encoding="utf-8")
        self.assertTrue(any("pyproject.toml version 9.9.9" in e for e in self.errors()))
        (self.root / "pyproject.toml").write_text(f'[project]\nname = "reachweaver"\nversion = "{VERSION}"\n', encoding="utf-8")
        for old in self.dist.iterdir():
            old.unlink()
        self.wheel(name="reachweaver-1.2.4-py3-none-any.whl")
        self.sdist()
        self.assertTrue(any("expected reachweaver-1.2.3-py3-none-any.whl" in e for e in self.errors()))

    def test_exactly_one_wheel_and_one_sdist(self):
        self.good()
        self.wheel(name="reachweaver-1.2.0-py3-none-any.whl")
        self.assertTrue(any("exactly one wheel" in e for e in self.errors()))
        for old in self.dist.iterdir():
            old.unlink()
        self.assertEqual(sum("exactly one" in e for e in self.errors()), 2)


class CommandLine(Fixture):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(check_package, "ROOT", self.root), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_package.main([str(self.dist), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes_and_messages(self):
        self.good()
        code, out, err = self.run_main()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("package-check: OK", out)
        self.wheel(drop=("reachweaver/cli.py",))
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("ERROR: wheel is missing reachweaver/cli.py", err)
        self.assertEqual(out, "")


class SmokeInstall(Fixture):
    """The smoke step is exercised for real in CI; here its decisions are checked with the subprocess calls faked."""

    def fake_run(self, results, creates_accounts=True):
        calls = []

        def run(argv, **kw):
            calls.append([Path(a).name if i == 0 else a for i, a in enumerate(argv)])
            if creates_accounts and argv[1:3] == ["workspace", "--init"]:  # what the installed command does for real
                (Path(argv[3]) / "config").mkdir(parents=True)
                (Path(argv[3]) / "config" / "accounts.json").write_text("{}", encoding="utf-8")
            return results.pop(0)
        return calls, run

    def done(self, code=0, out="", err=""):
        return subprocess.CompletedProcess([], code, out, err)

    def test_install_then_three_commands_and_a_new_workspace_from_an_empty_folder(self):
        self.good()
        calls, run = self.fake_run([self.done(), self.done(out="reachweaver 1.2.3\n"), self.done(out="bluesky\n"), self.done(out="plan"),
                                    self.done(), self.done()])
        with patch.object(check_package.venv.EnvBuilder, "create"), patch.object(check_package.subprocess, "run", run):
            self.assertEqual(check_package.smoke(self.dist, VERSION), [])
        self.assertEqual([c[1:3] for c in calls[:1]], [["-m", "pip"]])
        self.assertIn("--no-index", calls[0])
        self.assertIn("--no-deps", calls[0])
        self.assertEqual([c[1] for c in calls[1:]], ["version", "capabilities", "distribution-plan", "workspace", "doctor"])

    def test_failures_are_reported(self):
        self.good()
        calls, run = self.fake_run([self.done(code=1, err="boom")])
        with patch.object(check_package.venv.EnvBuilder, "create"), patch.object(check_package.subprocess, "run", run):
            self.assertTrue(any("pip install of the wheel failed" in e for e in check_package.smoke(self.dist, VERSION)))
        calls, run = self.fake_run([self.done(), self.done(out="reachweaver 0.0.1\n"), self.done(out="bluesky"), self.done(code=2, err="no registry"),
                                    self.done(), self.done()])
        with patch.object(check_package.venv.EnvBuilder, "create"), patch.object(check_package.subprocess, "run", run):
            errors = check_package.smoke(self.dist, VERSION)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("`reachweaver version` failed" in e for e in errors))

    def test_a_workspace_without_accounts_json_is_reported(self):
        self.good()
        calls, run = self.fake_run([self.done(), self.done(out="reachweaver 1.2.3\n"), self.done(out="bluesky"), self.done(out="plan"),
                                    self.done(), self.done(code=1, err="Missing config/accounts.json")], creates_accounts=False)
        with patch.object(check_package.venv.EnvBuilder, "create"), patch.object(check_package.subprocess, "run", run):
            errors = check_package.smoke(self.dist, VERSION)
        self.assertEqual(len(errors), 1)
        self.assertIn("workspace --init", errors[0])


if __name__ == "__main__":
    unittest.main()
