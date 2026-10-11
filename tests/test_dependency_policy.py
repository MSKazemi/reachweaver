"""Runtime-dependency policy guard (issue #8, ADR-0002) and supply-chain regressions (#26).

Offline; never installs or fetches anything.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_runtime_deps.py"


def _run(pyproject_text: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pyproject.toml"
        path.write_text(textwrap.dedent(pyproject_text), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPT), "--pyproject", str(path)],
                              capture_output=True, text=True, check=False)
        return proc.returncode, proc.stderr


class RuntimeDependencyPolicyTest(unittest.TestCase):
    def test_repository_pyproject_has_no_runtime_dependencies(self):
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("runtime-deps: OK", proc.stderr)

    def test_added_dependency_fails_and_requires_pip_audit(self):
        code, err = _run('[project]\nname = "x"\ndependencies = ["requests>=2"]\n')
        self.assertEqual(code, 1)
        self.assertIn("dependencies is not empty", err)
        self.assertIn("pip-audit", err)

    def test_missing_dependencies_key_fails(self):
        code, err = _run('[project]\nname = "x"\n')
        self.assertEqual(code, 1)
        self.assertIn("dependencies = []", err)

    def test_non_empty_optional_extra_fails(self):
        code, err = _run('[project]\nname = "x"\ndependencies = []\n'
                         '[project.optional-dependencies]\nsocial = ["httpx"]\n')
        self.assertEqual(code, 1)
        self.assertIn("optional-dependencies] social", err)

    def test_empty_extra_and_unreadable_file(self):
        code, _ = _run('[project]\nname = "x"\ndependencies = []\n[project.optional-dependencies]\ndev = []\n')
        self.assertEqual(code, 0)
        code, err = _run('[project\n')
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_violations_function_without_project_table(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import check_runtime_deps
        finally:
            sys.path.remove(str(SCRIPT.parent))
        self.assertEqual(check_runtime_deps.violations({}), ["pyproject.toml has no [project] table"])


class SupplyChainGuardTest(unittest.TestCase):
    """Issue #26 dependency-compromise regressions, extending the pyproject guard above.

    ``check_runtime_deps.py`` only reads pyproject.toml. These tests close the paths around
    it: an undeclared third-party import, a planted module in the bundled skill or on a
    script's ``sys.path[0]`` that shadows a stdlib module, a dynamic dependency channel,
    and unpinned CI tooling. Offline; nothing is installed or fetched.
    """

    SKILL = ROOT / ".claude/skills/reachweaver"

    @staticmethod
    def _top_level_imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
            elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str) and not node.args[0].value.startswith(".")
                  and ((isinstance(node.func, ast.Name) and node.func.id == "__import__")
                       or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"))):
                names.add(node.args[0].value.split(".")[0])
        return names

    def test_runtime_imports_only_stdlib_or_reachweaver(self):
        allowed = set(sys.stdlib_module_names) | {"__future__", "reachweaver"}
        for package in (ROOT / "reachweaver", self.SKILL / "scripts/reachweaver"):
            for path in sorted(package.glob("*.py")):
                with self.subTest(module=str(path.relative_to(ROOT))):
                    self.assertEqual(self._top_level_imports(path) - allowed, set(),
                                     "a third-party import must be declared and audited first (ADR-0002)")

    def test_bundled_skill_ships_no_extra_python(self):
        canonical = {p.name for p in (ROOT / "reachweaver").glob("*.py")}
        bundled = {p.relative_to(self.SKILL).as_posix() for p in self.SKILL.rglob("*.py")
                   if "__pycache__" not in p.parts}
        self.assertEqual(bundled, {"scripts/run.py"} | {f"scripts/reachweaver/{name}" for name in canonical})

    def test_no_stdlib_shadowing_module_on_any_script_search_path(self):
        # Directories that become sys.path[0]: the repo root (python -m), scripts/ (scripts/*.py),
        # tests/ (unittest discover -s tests) and the bundled skill's scripts/ (run.py). A planted
        # json.py or tomllib.py there would run instead of the stdlib module. reachweaver/ itself
        # is a package (absolute imports), so its calendar.py does not shadow the stdlib.
        stdlib = set(sys.stdlib_module_names)
        for directory in (ROOT, ROOT / "scripts", ROOT / "tests", self.SKILL / "scripts"):
            for entry in directory.iterdir():
                name = entry.stem if entry.suffix == ".py" else entry.name if entry.is_dir() else None
                with self.subTest(path=str(entry.relative_to(ROOT))):
                    self.assertNotIn(name, stdlib)

    def test_repository_pyproject_has_no_dynamic_dependency_channel(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dynamic = set(data["project"].get("dynamic", []))
        self.assertFalse(dynamic & {"dependencies", "optional-dependencies"})
        setuptools_dynamic = data.get("tool", {}).get("setuptools", {}).get("dynamic", {})
        self.assertFalse(set(setuptools_dynamic) & {"dependencies", "optional-dependencies"})
        requires = data["build-system"]["requires"]
        self.assertTrue(requires and all(r.startswith("setuptools") for r in requires), requires)

    def test_ci_actions_pinned_to_commit_sha_and_tools_to_exact_versions(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", ci, re.M)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", "pin third-party actions to a full commit SHA")
        for spec in re.findall(r"--spec\s+(\S+)", ci):
            self.assertRegex(spec, r"^[A-Za-z0-9_.-]+==\d+(\.\d+)+$", "pin CI tools to an exact version")
        for line in re.findall(r"^.*\bpip3? install\b.*$", ci, re.M):
            self.assertIn("--require-hashes", line, "a CI pip install must be hash-pinned")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        for var in ("RUFF_VERSION", "MYPY_VERSION"):
            self.assertRegex(makefile, rf"(?m)^{var} \?= \d+\.\d+\.\d+$")

    def test_guard_rejects_dynamic_optional_dependencies(self):
        # Fixed in #26: setuptools accepts `dynamic = ["optional-dependencies"]` filled from
        # [tool.setuptools.dynamic], a channel the static [project] checks cannot see. The guard
        # now rejects it. The repository itself is clean
        # (test_repository_pyproject_has_no_dynamic_dependency_channel).
        code, err = _run('[project]\nname = "x"\ndependencies = []\ndynamic = ["optional-dependencies"]\n'
                         '[tool.setuptools.dynamic]\noptional-dependencies.social = {file = ["social.txt"]}\n')
        self.assertEqual(code, 1, err)
        self.assertIn("dynamic lists 'optional-dependencies'", err)
        self.assertIn("[tool.setuptools.dynamic] declares 'optional-dependencies'", err)

    def test_guard_rejects_each_dynamic_dependency_channel_alone(self):
        cases = {
            "project dynamic dependencies": ('[project]\nname = "x"\ndynamic = ["dependencies"]\n',
                                             "dynamic lists 'dependencies'"),
            "setuptools dynamic only": ('[project]\nname = "x"\ndependencies = []\n'
                                        '[tool.setuptools.dynamic]\ndependencies = {file = ["req.txt"]}\n',
                                        "[tool.setuptools.dynamic] declares 'dependencies'"),
            "dynamic not a list": ('[project]\nname = "x"\ndependencies = []\ndynamic = "dependencies"\n',
                                   "dynamic must be a list"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(case=name):
                code, err = _run(text)
                self.assertEqual(code, 1, err)
                self.assertIn(message, err)

    def test_guard_allows_dynamic_non_dependency_fields(self):
        code, err = _run('[project]\nname = "x"\ndependencies = []\ndynamic = ["version"]\n'
                         '[tool.setuptools.dynamic]\nversion = {attr = "x.__version__"}\n')
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
