"""Mirror canonical CLI runtime into the standalone Claude skill.

``python3 scripts/sync_skill.py``          write the bundle
``python3 scripts/sync_skill.py --check``  list out-of-date bundle files, exit 1, write nothing
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reachweaver"
SKILL = ROOT / ".claude/skills/reachweaver"
TARGET = SKILL / "scripts/reachweaver"
REGISTRIES = ("distribution_registry.json", "platform_registry.json", "profile_audit_registry.json")
# Canonical files mirrored into the skill's assets/ (flat, by basename).
ASSET_SOURCES = (
    "examples/brand.reachweaver.json", "examples/campaign.reachweaver.json",
    "examples/experiment.example.json", "examples/growth-plan.example.json",
    "examples/brand-preview.reachweaver.json", "examples/campaign-preview.reachweaver.json",
    "config/accounts.example.json", "examples/example-post.txt",
    "examples/profile-post-audit.example.json",
    "examples/profile-post-metrics.example.csv",
)
RUN_PY = '''#!/usr/bin/env python3
"""Standalone bundled ReachWeaver CLI; runs against the current project directory."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reachweaver.cli import main
raise SystemExit(main())
'''


def expected() -> dict[Path, bytes]:
    """Every bundle file this script owns, as {path: bytes}."""
    files: dict[Path, bytes] = {}
    for file in SOURCE.glob("*.py"):
        files[TARGET / file.name] = file.read_bytes()
    for name in REGISTRIES:
        files[TARGET / name] = (SOURCE / name).read_bytes()
    for rel in ASSET_SOURCES:
        files[SKILL / "assets" / Path(rel).name] = (ROOT / rel).read_bytes()
    files[TARGET.parent / "run.py"] = RUN_PY.encode("utf-8")
    return files


def check() -> list[Path]:
    """Bundle files that are missing, differ or are stale. Never writes."""
    want = expected()
    diff = [p for p, data in want.items() if not p.is_file() or p.read_bytes() != data]
    if TARGET.is_dir():
        diff += [p for p in TARGET.glob("*.py") if p not in want]
    return sorted(diff)


def sync():
    TARGET.mkdir(parents=True, exist_ok=True)
    for file in SOURCE.glob("*.py"):
        shutil.copy2(file, TARGET / file.name)
    # Copy the versioned research registries (distribution + platform capability).
    for name in REGISTRIES:
        shutil.copy2(SOURCE / name, TARGET / name)
    for stale in TARGET.glob("*.py"):
        if not (SOURCE / stale.name).exists():
            stale.unlink()
    # A standalone CLI run may create bytecode; never ship volatile caches in skill.zip.
    for cache in TARGET.rglob("__pycache__"):
        shutil.rmtree(cache)
    assets = SKILL / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for rel in ASSET_SOURCES:
        shutil.copy2(ROOT / rel, assets / Path(rel).name)
    (TARGET.parent / "run.py").write_text(RUN_PY, encoding="utf-8")


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        stale = check()
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)}")
        if stale:
            print("Run: python3 scripts/sync_skill.py")
            raise SystemExit(1)
        print("Standalone skill bundle is up to date")
        raise SystemExit(0)
    sync()
    print("Standalone skill CLI synchronized")
