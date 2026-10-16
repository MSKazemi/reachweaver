"""The package metadata, the CI package job and the release workflow's package steps (offline, static).

``scripts/check_package.py`` checks a built wheel and sdist; these tests check what is built *from*: that
``pyproject.toml`` describes the project completely and in step with the code, that every bundled registry is listed as
package data, and that CI and the release workflow build, check and (in the audited ``publish`` job only) attach the
package without widening the workflow's permissions.
"""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
JOBS = RELEASE.split("\njobs:\n", 1)[1]
VERIFY = ("\n" + JOBS).split("\n  verify:\n", 1)[1].split("\n  publish:\n", 1)[0]
PUBLISH = ("\n" + JOBS).split("\n  publish:\n", 1)[1]
PACKAGE_JOB = CI.split("\n  package:\n", 1)[1].split("\n  tests:\n", 1)[0]


class Metadata(unittest.TestCase):
    def test_version_matches_the_code(self):
        init = (ROOT / "reachweaver/__init__.py").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'__version__ = "([^"]+)"', init).group(1), PROJECT["version"])

    def test_license_is_an_spdx_expression_and_the_license_files_exist(self):
        self.assertEqual(PROJECT["license"], "Apache-2.0")
        self.assertEqual(PROJECT["license-files"], ["LICENSE", "NOTICE", "DISCLAIMER.md"])
        for name in PROJECT["license-files"]:
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertFalse([c for c in PROJECT["classifiers"] if c.startswith("License ::")])  # PEP 639: not both
        self.assertGreaterEqual(int(re.match(r"setuptools>=(\d+)", PYPROJECT["build-system"]["requires"][0]).group(1)), 77)

    def test_every_bundled_registry_is_listed_as_package_data(self):
        shipped = {p.name for p in (ROOT / "reachweaver").glob("*.json")}
        listed = set(PYPROJECT["tool"]["setuptools"]["package-data"]["reachweaver"])
        self.assertEqual(listed, shipped, "a registry added to reachweaver/ must be added to package-data")

    def test_the_runtime_stays_dependency_free_and_python_floor_matches_the_type_checker(self):
        self.assertEqual(PROJECT["dependencies"], [])
        self.assertNotIn("optional-dependencies", PROJECT)
        self.assertEqual(PROJECT["requires-python"], ">=3.11")
        self.assertEqual(PYPROJECT["tool"]["mypy"]["python_version"], "3.11")

    def test_console_script_target_exists(self):
        module, _, function = PROJECT["scripts"]["reachweaver"].partition(":")
        self.assertEqual(module, "reachweaver.cli")
        self.assertIn(f"def {function}(", (ROOT / "reachweaver/cli.py").read_text(encoding="utf-8"))

    def test_urls_classifiers_and_readme_are_complete(self):
        urls = PROJECT["urls"]
        self.assertEqual(set(urls), {"Homepage", "Documentation", "Source", "Issues", "Changelog"})
        for url in urls.values():
            self.assertTrue(url.startswith("https://github.com/MSKazemi/reachweaver"), url)
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f"Programming Language :: Python :: {version}", PROJECT["classifiers"])
        self.assertEqual(PROJECT["readme"], "README.md")
        self.assertTrue((ROOT / "README.md").is_file())

    def test_the_readme_and_the_packaging_guide_link_the_wheel_of_the_current_version(self):
        version = PROJECT["version"]
        wheel = f"releases/download/v{version}/reachweaver-{version}-py3-none-any.whl"
        for name in ("README.md", "docs/PACKAGING.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            links = re.findall(r"releases/download/v([\d.]+)/reachweaver-([\d.]+)-py3-none-any\.whl", text)
            self.assertTrue(links, name)
            self.assertEqual({v for pair in links for v in pair}, {version}, f"{name}: update the wheel link with the version")
            self.assertIn(wheel, text)

    def test_the_wheel_stays_minimal_no_manifest_pulls_in_private_folders(self):
        # A MANIFEST.in is deliberately absent: without one an sdist holds only the package, tests, pyproject, README and
        # licenses, so a build from a working folder cannot sweep up docs/, state/ or notes.
        self.assertFalse((ROOT / "MANIFEST.in").exists())
        self.assertNotIn("include-package-data", PYPROJECT.get("tool", {}).get("setuptools", {}))


class CiPackageJob(unittest.TestCase):
    def test_ci_builds_checks_and_uploads_the_package_read_only(self):
        self.assertIn("pipx run --spec build==", PACKAGE_JOB)
        self.assertIn("pipx run --spec twine==", PACKAGE_JOB)
        self.assertIn("twine check --strict dist/*", PACKAGE_JOB)
        self.assertIn("python scripts/check_package.py dist --smoke", PACKAGE_JOB)
        self.assertIn("name: reachweaver-python-dist", PACKAGE_JOB)
        self.assertNotIn("write", PACKAGE_JOB)
        self.assertNotIn("secrets.", PACKAGE_JOB)
        self.assertIn("\npermissions:\n  contents: read\n", CI)

    def test_the_build_tools_are_pinned_to_the_same_versions_in_ci_and_release(self):
        for tool in ("build", "twine"):
            ci = re.findall(rf"--spec ({tool}==\d+(?:\.\d+)+)", CI)
            release = re.findall(rf"--spec ({tool}==\d+(?:\.\d+)+)", RELEASE)
            self.assertEqual(len(ci), 1, tool)
            self.assertEqual(set(release), set(ci), tool)

    def test_makefile_has_a_local_package_target_with_the_same_checks(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^package:")
        self.assertIn("scripts/check_package.py", makefile)


class ReleaseWorkflowPackageSteps(unittest.TestCase):
    def test_verify_builds_and_checks_the_package_after_the_gate_and_before_publish_can_run(self):
        names = re.findall(r"(?m)^      - name: (.+)$", VERIFY)
        gate = next(i for i, n in enumerate(names) if n.startswith("Release gate"))
        build = next(i for i, n in enumerate(names) if n.startswith("Build the Python package"))
        check = next(i for i, n in enumerate(names) if n.startswith("Package contents"))
        self.assertLess(gate, build)
        self.assertLess(build, check)
        self.assertIn("python scripts/check_package.py dist --smoke", VERIFY)
        self.assertIn("twine check --strict dist/*", VERIFY)
        self.assertNotIn("gh release", VERIFY)

    def test_publish_rebuilds_tests_and_checksums_the_package_then_attaches_it_to_the_release(self):
        step = next(s for s in re.split(r"(?m)^      - ", PUBLISH) if s.startswith("name: Build the Python package"))
        for command in ("pyproject-build --outdir dist .", "python -m zipfile -t dist/*.whl", "tar -tzf dist/*.tar.gz",
                        "python scripts/check_package.py dist", "sha256sum --check --strict ../python-dist.sha256"):
            self.assertIn(command, step)
        self.assertNotIn("--smoke", step)  # smoke runs once, in the read-only job
        release = [line.strip() for line in PUBLISH.splitlines() if line.strip().startswith("run: gh release create")][0]
        for asset in ("skill.zip skill.zip.sha256", "dist/*", "python-dist.sha256"):
            self.assertIn(asset, release)
        self.assertIn("--verify-tag", release)

    def test_no_new_permission_job_or_trigger_was_added_for_packaging(self):
        self.assertEqual(re.findall(r"(?m)^  ([a-z-]+):$", JOBS), ["verify", "publish"])
        self.assertEqual(set(re.findall(r"(?m)^\s+([a-z-]+): (?:read|write)\b", RELEASE)), {"contents"})
        self.assertEqual(len(re.findall(r"(?m)^\s+[a-z-]+: write\b", RELEASE)), 1)
        for forbidden in ("id-token", "attestations", "packages:", "pypi", "twine upload", "gh-action-pypi-publish"):
            self.assertNotIn(forbidden, RELEASE)


if __name__ == "__main__":
    unittest.main()
