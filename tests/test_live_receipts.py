"""The checked-in live receipts stay first-party, scoped and consistent with the scorecard and the docs (owner request, 2026-10-15).

Offline: reads local files only. The receipts record one text post each, sent through ReachWeaver on 2026-10-11 and
re-observed on 2026-10-15 from the platforms' public APIs. They prove those two posts exist; they prove nothing else.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RECEIPTS = {"bluesky": "docs/receipts/bluesky-2026-10-11.json", "mastodon": "docs/receipts/mastodon-2026-10-11.json"}
HOSTS = {"bluesky": "bsky.app", "mastodon": "mastodon.social"}
SECRETISH = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|authorization|bearer|access[_-]?token|accessjwt|cookie)")


def load(area: str) -> dict:
    return json.loads((ROOT / RECEIPTS[area]).read_text(encoding="utf-8"))


class LiveReceiptTests(unittest.TestCase):
    def test_each_receipt_is_first_party_dated_and_points_at_the_platform(self):
        for area in RECEIPTS:
            with self.subTest(area=area):
                data = load(area)
                self.assertEqual(data["area"], area)
                self.assertIs(data["first_party"], True)
                self.assertEqual(urlsplit(data["remote_url"]).scheme, "https")
                self.assertEqual(urlsplit(data["remote_url"]).hostname, HOSTS[area])
                self.assertRegex(data["published_at"], r"^2026-10-11T")
                self.assertRegex(data["observed_at"], r"^2026-10-\d\dT.*Z$")
                self.assertGreaterEqual(data["observed_at"][:10], data["published_at"][:10])
                self.assertIn("without credentials", data["source"])
                self.assertIn("approve, send_claimed, published", data["reachweaver_event_log"])

    def test_receipts_hold_no_credential_and_claim_no_reach_or_growth(self):
        for area in RECEIPTS:
            raw = (ROOT / RECEIPTS[area]).read_text(encoding="utf-8")
            with self.subTest(area=area):
                self.assertIsNone(SECRETISH.search(raw.replace("without credentials", "")), "credential-like word in a receipt")
                self.assertNotRegex(raw.lower(), r"guarantee|viral|went viral|grew|growth result")
                data = load(area)
                self.assertIn("says nothing about reach, growth or the other adapters", data["limits"])
                self.assertEqual(set(data["engagement_at_observation"].values()), {0})

    def test_the_scorecard_names_exactly_these_receipts(self):
        scorecard = json.loads((ROOT / "docs/release-readiness.json").read_text(encoding="utf-8"))
        verified = {a["id"]: a["receipt"] for a in scorecard["areas"] if a["status"] == "live_receipt_verified"}
        self.assertEqual(verified, RECEIPTS)
        for area, path in RECEIPTS.items():
            row = next(a for a in scorecard["areas"] if a["id"] == area)
            self.assertIn(path, row["evidence"])
        files = sorted(p.name for p in (ROOT / "docs/receipts").glob("*.json"))
        self.assertEqual(files, sorted(Path(p).name for p in RECEIPTS.values()))

    def test_the_public_docs_say_the_same_thing(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("one approved text post each was sent through ReachWeaver to Bluesky and Mastodon on 2026-10-11", readme)
        self.assertIn("Telegram and LinkedIn have had no live send", readme)
        matrix = (ROOT / "docs/PLATFORM-MATRIX.md").read_text(encoding="utf-8")
        for area, path in RECEIPTS.items():
            self.assertIn(f"receipts/{Path(path).name}", matrix, area)
        for pid in ("telegram", "linkedin"):
            row = next(line for line in matrix.splitlines() if line.startswith("| ") and f"| `{pid}` |" in line and "live_text" in line)
            self.assertIn("| `none` |", row, pid)


if __name__ == "__main__":
    unittest.main()
