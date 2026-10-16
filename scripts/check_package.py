#!/usr/bin/env python3
"""Offline checks of a built wheel and source distribution (stdlib only; no network, no credentials).

    python scripts/check_package.py DIST_DIR [--smoke]

Reads the files in DIST_DIR (one ``reachweaver-X.Y.Z-py3-none-any.whl`` and one ``reachweaver-X.Y.Z.tar.gz``) and checks
them against this checkout: the version matches ``reachweaver.__version__`` and ``pyproject.toml``; the wheel holds every
runtime module and every bundled registry (a registry added without a ``package-data`` entry fails here), the
console-script entry point, the license files and an Apache-2.0 license expression, declares no runtime dependency and
Python >= 3.11; and neither archive carries anything private (docs, ``.claude``, state, databases, ``.env*`` files,
local accounts, security-review notes, an approval ledger, a workspace marker).

``--smoke`` additionally installs the wheel into a fresh virtual environment (no network, no dependencies) and runs the
installed ``reachweaver`` command from an empty folder, proving the bundled data is found without the checkout.

Exit 0 only when every check passed. Used by CI (``package`` job) and by the release workflow before anything is published.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(
    r"(^|/)(docs|\.claude|\.claude-plugin|\.github|state|media|logs|archive|internal)/"
    r"|\.sqlite|(^|/)\.env|(^|/)secrets\.env$|(^|/)config/accounts\.json$|SECURITY-REVIEW-PROPOSAL"
    r"|approval-ledger\.jsonl|\.reachweaver-workspace$|(^|/)\.git(/|$)|__pycache__|\.pyc$")
LICENSES = ("LICENSE", "NOTICE", "DISCLAIMER.md")
MIN_PYTHON = ">=3.11"


def source_version(root: Path) -> str:
    match = re.search(r'^__version__ = "([^"]+)"', (root / "reachweaver" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if match is None:
        raise SystemExit("cannot read __version__ from reachweaver/__init__.py")
    return match.group(1)


def runtime_files(root: Path) -> list[str]:
    package = root / "reachweaver"
    return sorted(f.name for f in [*package.glob("*.py"), *package.glob("*.json")])


def _only(dist: Path, pattern: str, label: str, errors: list[str]) -> Path | None:
    found = sorted(dist.glob(pattern))
    if len(found) != 1:
        errors.append(f"expected exactly one {label} in {dist}, found {len(found)}")
        return None
    return found[0]


def check_wheel(wheel: Path, root: Path, version: str) -> list[str]:
    errors: list[str] = []
    if wheel.name != f"reachweaver-{version}-py3-none-any.whl":
        errors.append(f"wheel is named {wheel.name}, expected reachweaver-{version}-py3-none-any.whl")
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            errors.append("wheel archive is corrupt")
        names = archive.namelist()
        dist_info = f"reachweaver-{version}.dist-info"
        for name in runtime_files(root):
            if f"reachweaver/{name}" not in names:
                errors.append(f"wheel is missing reachweaver/{name}")
        wanted = {f"reachweaver/{n}" for n in runtime_files(root)}
        for name in names:
            if name.startswith("reachweaver/") and name not in wanted:
                errors.append(f"wheel contains reachweaver/ file not in the checkout: {name}")
        for name in LICENSES:
            if f"{dist_info}/licenses/{name}" not in names:
                errors.append(f"wheel is missing the license file {name}")
        for name in names:
            if FORBIDDEN.search(name):
                errors.append(f"wheel contains a private or unwanted path: {name}")
        try:
            entry = archive.read(f"{dist_info}/entry_points.txt").decode("utf-8")
            metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
        except KeyError as exc:
            return errors + [f"wheel is missing {exc.args[0]}"]
    if not re.search(r"(?m)^reachweaver = reachweaver\.cli:main$", entry):
        errors.append("entry point 'reachweaver = reachweaver.cli:main' is missing")
    fields = {}
    for line in metadata.split("\n\n", 1)[0].splitlines():
        key, _, value = line.partition(": ")
        fields.setdefault(key, []).append(value)
    if fields.get("Version") != [version]:
        errors.append(f"METADATA version {fields.get('Version')} != {version}")
    if fields.get("License-Expression") != ["Apache-2.0"]:
        errors.append(f"METADATA License-Expression is {fields.get('License-Expression')}, expected Apache-2.0")
    if fields.get("Requires-Python") != [MIN_PYTHON]:
        errors.append(f"METADATA Requires-Python is {fields.get('Requires-Python')}, expected {MIN_PYTHON}")
    if fields.get("Requires-Dist"):
        errors.append(f"wheel declares runtime dependencies: {fields['Requires-Dist']} (the runtime must stay stdlib-only)")
    if not fields.get("Project-URL"):
        errors.append("METADATA has no Project-URL entries")
    return errors


def check_sdist(sdist: Path, root: Path, version: str) -> list[str]:
    errors: list[str] = []
    if sdist.name != f"reachweaver-{version}.tar.gz":
        errors.append(f"sdist is named {sdist.name}, expected reachweaver-{version}.tar.gz")
    prefix = f"reachweaver-{version}/"
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
    if not names or not all(n == prefix.rstrip("/") or n.startswith(prefix) for n in names):
        errors.append(f"sdist members are not all under {prefix}")
    inside = {n[len(prefix):] for n in names if n.startswith(prefix)}
    for needed in ("PKG-INFO", "pyproject.toml", "README.md", *LICENSES, *(f"reachweaver/{n}" for n in runtime_files(root))):
        if needed not in inside:
            errors.append(f"sdist is missing {needed}")
    for name in sorted(inside):
        if FORBIDDEN.search(name):
            errors.append(f"sdist contains a private or unwanted path: {name}")
    return errors


def check(dist: Path, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    version = source_version(root)
    declared = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if declared != version:
        errors.append(f"pyproject.toml version {declared} != reachweaver.__version__ {version}")
    wheel = _only(dist, "*.whl", "wheel", errors)
    sdist = _only(dist, "*.tar.gz", "source distribution", errors)
    if wheel is not None:
        errors += check_wheel(wheel, root, version)
    if sdist is not None:
        errors += check_sdist(sdist, root, version)
    return errors


def smoke(dist: Path, version: str) -> list[str]:
    """Install the wheel into a fresh venv and run the installed command from an empty folder."""
    wheel = next(iter(sorted(dist.glob("*.whl"))), None)
    if wheel is None:
        return ["no wheel to install"]
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        env_dir, work = Path(tmp) / "venv", Path(tmp) / "empty"
        work.mkdir()
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        bin_dir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
        python, command = bin_dir / "python", bin_dir / "reachweaver"
        install = subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", str(wheel)],
                                 capture_output=True, text=True)
        if install.returncode != 0:
            return [f"pip install of the wheel failed: {install.stderr.strip()[:300]}"]
        for argv, expect in ((["version"], f"reachweaver {version}"), (["capabilities"], "bluesky"),
                             (["distribution-plan", "--region", "global", "--goal", "adoption", "--limit", "3"], "")):
            run = subprocess.run([str(command), *argv], cwd=work, capture_output=True, text=True)
            if run.returncode != 0 or expect not in run.stdout:
                errors.append(f"`reachweaver {' '.join(argv)}` failed in the installed wheel "
                              f"(exit {run.returncode}): {(run.stderr or run.stdout).strip()[:200]}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline checks of a built wheel and sdist")
    parser.add_argument("dist", type=Path, help="Folder holding the wheel and the source distribution")
    parser.add_argument("--smoke", action="store_true", help="Also install the wheel in a fresh venv and run it")
    args = parser.parse_args(argv)
    errors = check(args.dist, ROOT)
    if args.smoke and not errors:
        errors += smoke(args.dist, source_version(ROOT))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"package-check: OK; reachweaver {source_version(ROOT)} wheel and sdist match the checkout"
          + ("; installed and ran in a clean environment" if args.smoke else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
