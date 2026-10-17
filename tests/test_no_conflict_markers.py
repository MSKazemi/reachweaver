"""No merge-conflict marker may survive in a tracked text file (a diff3 base marker sat in CHANGELOG.md for four releases)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".md", ".py", ".json", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini"}
SKIP_DIRS = {".git", ".git-private", ".claude", ".dualgit", "build", "dist", "node_modules", "__pycache__", ".venv", "venv"}
# Built from parts so this file does not match itself.
MARKER = re.compile("^(" + "<" * 7 + "|" + r"\|" * 7 + "|" + ">" * 7 + r")( |$)", re.MULTILINE)


def text_files() -> list[Path]:
    found = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts[:-1]):
            continue
        if path.suffix in SUFFIXES and path.is_file():
            found.append(path)
    return found


class NoConflictMarkerTests(unittest.TestCase):
    def test_scan_sees_the_files_that_matter(self) -> None:
        names = {p.relative_to(ROOT).as_posix() for p in text_files()}
        self.assertIn("CHANGELOG.md", names)
        self.assertIn("reachweaver/cli.py", names)

    def test_no_text_file_contains_a_conflict_marker(self) -> None:
        hits = []
        for path in text_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in MARKER.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
        self.assertEqual(hits, [], "merge-conflict markers left in: " + ", ".join(hits))


if __name__ == "__main__":
    unittest.main()
