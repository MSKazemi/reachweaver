"""Mirror canonical CLI runtime into the standalone Claude skill.

``python3 scripts/sync_skill.py``          write the bundle and the plugin mirror
``python3 scripts/sync_skill.py --check``  list out-of-date bundle files, exit 1, write nothing

The plugin is a lean ``plugin/``
subfolder (manifest, hook, README, LICENSE and a byte-identical copy of the skill bundle) rather
than the repository root with its tests and docs. ``plugin/skills/reachweaver`` is generated here.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "reachweaver"
SKILL = ROOT / ".claude/skills/reachweaver"
TARGET = SKILL / "scripts/reachweaver"
PLUGIN_SKILL = ROOT / "plugin/skills/reachweaver"
PLUGIN_ROOT_FILES = ("LICENSE", "NOTICE", "DISCLAIMER.md")  # copied verbatim into plugin/
SKILL_ROOT_FILES = ("LICENSE", "NOTICE", "DISCLAIMER.md")  # accompany standalone skill ZIP and local install
REGISTRIES = ("distribution_registry.json", "platform_registry.json", "profile_audit_registry.json",
              "accounts.example.json", "discovery_registry.json", "signup_registry.json",
              "voice_tells.json", "ai_search_registry.json", "ai_disclosure_registry.json", "social_web_registry.json")  # accounts.example.json: the example accounts file `workspace --init` copies; kept equal to config/
# Canonical files mirrored into the skill's assets/ (flat, by basename).
ASSET_SOURCES = (
    "examples/brand.reachweaver.json", "examples/campaign.reachweaver.json",
    "examples/experiment.example.json", "examples/growth-plan.example.json",
    "examples/brand-preview.reachweaver.json", "examples/campaign-preview.reachweaver.json",
    "config/accounts.example.json", "config/profile.example.json", "examples/example-post.txt",
    "examples/manual-batch.example.json",
    "examples/discovery-plan.example.json",
    "examples/creator-candidates.example.json", "examples/creator-source.example.json",
    "examples/creator-coverage.example.json", "examples/creative-preflight.example.json",
    "examples/editorial-plan.example.json",
    "examples/profile-post-audit.example.json",
    "examples/profile-post-metrics.example.csv",
    "examples/followup-rules.example.json",
    "examples/engage-scout.example.json", "examples/engage-candidates.example.json",
    "examples/social-web-setup.example.json",
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
    for name in SKILL_ROOT_FILES:
        files[SKILL / name] = (ROOT / name).read_bytes()
    files[TARGET.parent / "run.py"] = RUN_PY.encode("utf-8")
    return files


def _bundle_files() -> list[Path]:
    """Files of the skill bundle that ship (no bytecode caches)."""
    return sorted(p for p in SKILL.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


def plugin_expected() -> dict[Path, bytes]:
    """The plugin's copy of the skill bundle, as {path: bytes}, built from the bundle on disk."""
    files = {PLUGIN_SKILL / p.relative_to(SKILL): p.read_bytes() for p in _bundle_files()}
    for name in PLUGIN_ROOT_FILES:
        files[ROOT / "plugin" / name] = (ROOT / name).read_bytes()
    return files


def check() -> list[Path]:
    """Bundle and plugin-mirror files that are missing, differ or are stale. Never writes."""
    want = expected()
    diff = [p for p, data in want.items() if not p.is_file() or p.read_bytes() != data]
    if TARGET.is_dir():
        diff += [p for p in TARGET.glob("*.py") if p not in want]
    mirror = plugin_expected()
    diff += [p for p, data in mirror.items() if not p.is_file() or p.read_bytes() != data]
    if PLUGIN_SKILL.is_dir():
        diff += [p for p in PLUGIN_SKILL.rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and p not in mirror]
    return sorted(set(diff))


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
    # Every redistributed standalone skill includes the actual license and NOTICE.
    for name in SKILL_ROOT_FILES:
        shutil.copy2(ROOT / name, SKILL / name)
    # Mirror the finished bundle into the lean plugin folder; drop stale files and caches.
    mirror = plugin_expected()
    for path, data in mirror.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if PLUGIN_SKILL.is_dir():
        for path in sorted(PLUGIN_SKILL.rglob("*"), reverse=True):
            if path.is_file() and path not in mirror:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for cache in PLUGIN_SKILL.rglob("__pycache__"):
        shutil.rmtree(cache)


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
