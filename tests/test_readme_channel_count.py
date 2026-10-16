"""README's channel count must match the bundled distribution registry (it said 90 after 92 were registered)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from reachweaver import distribution

ROOT = Path(__file__).resolve().parents[1]


class ReadmeChannelCountTests(unittest.TestCase):
    def test_readme_channel_count_matches_the_registry(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        counts = re.findall(r"catalogs \*\*(\d+) channels", text)
        self.assertEqual(len(counts), 1, counts)
        self.assertEqual(int(counts[0]), len(distribution.load_registry()["platforms"]))

    def test_features_page_channel_count_matches_the_registry(self) -> None:
        text = (ROOT / "docs/FEATURES.md").read_text(encoding="utf-8")
        counts = re.findall(r"catalogs \*\*(\d+) channels", text)
        self.assertEqual(len(counts), 1, counts)
        self.assertEqual(int(counts[0]), len(distribution.load_registry()["platforms"]))


if __name__ == "__main__":
    unittest.main()
