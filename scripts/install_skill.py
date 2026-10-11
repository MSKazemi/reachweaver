#!/usr/bin/env python3
"""Install the ReachWeaver Claude Code skill (stdlib only; no pip, no account, no credentials).

  python3 scripts/install_skill.py                 # latest GitHub Release -> ~/.claude/skills/reachweaver
  python3 scripts/install_skill.py --project       # -> ./.claude/skills/reachweaver (this project only)
  python3 scripts/install_skill.py --from-checkout # copy the bundle from this clone (no network)

The release path downloads skill.zip and skill.zip.sha256 from github.com, refuses a checksum
mismatch, and only then unpacks. Re-running it upgrades in place; later, ask Claude Code or run
`python3 <skill>/scripts/run.py update --check`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://github.com/MSKazemi/reachweaver/releases/latest/download"


def _get(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "reachweaver-installer"}), timeout=30) as r:  # noqa: S310
        if not (r.geturl().startswith("https://") and "github" in r.geturl().split("/")[2]):
            raise SystemExit("Refusing a redirect away from GitHub")
        return r.read(20 * 1024 * 1024 + 1)


def _from_release(dest: Path) -> dict:
    data, sha = _get(f"{BASE}/skill.zip"), _get(f"{BASE}/skill.zip.sha256").decode()
    if hashlib.sha256(data).hexdigest() != (sha.split() or [""])[0].lower():
        raise SystemExit("SHA-256 mismatch: nothing installed")
    # Reuse the bundle's own verified unpack/swap code (it re-checks the digest and every path).
    prefix = "reachweaver/scripts/reachweaver/"
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.namelist():
            if member.startswith(prefix) and ".." not in member.split("/") and member.endswith((".py", ".json")):
                out = Path(tmp) / member[len("reachweaver/scripts/"):]
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(z.read(member))
        sys.path.insert(0, tmp)
        from reachweaver import updater  # type: ignore[import-not-found]
        return updater.install_zip(data, sha, dest)


def _from_checkout(dest: Path) -> dict:
    src = Path(__file__).resolve().parents[1] / ".claude/skills/reachweaver"
    if not (src / "SKILL.md").is_file():
        raise SystemExit("Run this from a ReachWeaver clone (.claude/skills/reachweaver not found)")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return {"installed_version": "checkout", "path": str(dest), "previous_copy": None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", action="store_true", help="Install into ./.claude/skills instead of ~/.claude/skills")
    ap.add_argument("--from-checkout", action="store_true", help="Copy from this clone instead of downloading")
    args = ap.parse_args(argv)
    dest = (Path.cwd() if args.project else Path.home()) / ".claude/skills/reachweaver"
    result = _from_checkout(dest) if args.from_checkout else _from_release(dest)
    print(f"Installed ReachWeaver {result['installed_version']} at {result['path']}")
    print("Restart Claude Code, then use /reachweaver. Nothing was published and no account was connected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
