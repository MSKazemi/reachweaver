"""Assert that ReachWeaver still has no third-party runtime dependencies.

Usage:
    python3 scripts/check_runtime_deps.py [--pyproject FILE]

ReachWeaver's runtime is stdlib-only (ADR-0002), so there is nothing for a
dependency vulnerability auditor to audit. This guard keeps that true: it fails
if ``[project] dependencies`` is missing or non-empty, or if any
``[project.optional-dependencies]`` extra lists a package. It also fails if
``[project] dynamic`` names ``dependencies`` or ``optional-dependencies``, or if
``[tool.setuptools.dynamic]`` fills either one from a file, because a dynamic
dependency channel is invisible to the static checks above. Adding a runtime
dependency therefore requires adding a pinned ``pip-audit`` CI step (and a
lock file) in the same change, and then updating this guard.

Exit codes: 0 = no runtime dependencies, 1 = policy violation, 2 = unreadable file.
Offline, stdlib-only (``tomllib``, Python 3.11+).
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REMEDY = ("ReachWeaver's runtime is stdlib-only (docs/ADR-0002-RUNTIME-AND-TOOLING.md). "
          "Before adding a runtime dependency, add a pinned pip-audit step to CI and a "
          "hash-pinned lock file in the same change, then update scripts/check_runtime_deps.py.")


# Keys through which setuptools can load dependencies from outside [project] (PEP 621 `dynamic`).
DYNAMIC_DEPENDENCY_KEYS = frozenset({"dependencies", "optional-dependencies"})


def violations(pyproject: dict) -> list[str]:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return ["pyproject.toml has no [project] table"]
    problems: list[str] = []
    if "dependencies" not in project:
        problems.append("[project] must declare `dependencies = []` explicitly")
    elif project["dependencies"] != []:
        problems.append(f"[project] dependencies is not empty: {project['dependencies']!r}")
    extras = project.get("optional-dependencies") or {}
    for extra, packages in sorted(extras.items()):
        if packages:
            problems.append(f"[project.optional-dependencies] {extra} is not empty: {packages!r}")
    dynamic = project.get("dynamic") or []
    if not isinstance(dynamic, list):
        problems.append(f"[project] dynamic must be a list: {dynamic!r}")
        dynamic = []
    for key in sorted(DYNAMIC_DEPENDENCY_KEYS & {str(item) for item in dynamic}):
        problems.append(f"[project] dynamic lists {key!r}; dependencies must be declared statically")
    tool = pyproject.get("tool")
    setuptools = tool.get("setuptools") if isinstance(tool, dict) else None
    setuptools_dynamic = setuptools.get("dynamic") if isinstance(setuptools, dict) else None
    if isinstance(setuptools_dynamic, dict):
        for key in sorted(DYNAMIC_DEPENDENCY_KEYS & set(setuptools_dynamic)):
            problems.append(f"[tool.setuptools.dynamic] declares {key!r}; dependencies must be declared statically")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pyproject", type=Path, default=ROOT / "pyproject.toml")
    args = parser.parse_args(argv)
    try:
        data = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"runtime-deps: cannot read {args.pyproject}: {exc}", file=sys.stderr)
        return 2
    problems = violations(data)
    for problem in problems:
        print(f"runtime-deps: {problem}", file=sys.stderr)
    if problems:
        print(f"runtime-deps: FAIL. {REMEDY}", file=sys.stderr)
        return 1
    print("runtime-deps: OK; no third-party runtime dependencies (dependency audit not required)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
