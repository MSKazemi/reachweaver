"""docs/PLATFORM-MATRIX.md must agree with the registries and the code (issues #14, #30).

Only the tables between ``<!-- platform-matrix:start -->`` / ``:end -->`` and
``<!-- backend-matrix:start -->`` / ``:end -->`` are parsed, and only the first
backticked token of the checked cells is compared, so prose can change freely while
the status facts cannot drift. Offline; reads local files only.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import unittest

from reachweaver import capabilities, distribution, manual_export, media, metrics_fetch, postiz, storyboard
from reachweaver.policy import SUPPORTED_LIVE

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/PLATFORM-MATRIX.md"
COLUMNS = ("Platform", "Registry id", "ReachWeaver adapter", "Via Postiz backend", "Native scheduling",
           "Owner live receipt", "Onboarding dependency", "Media", "Analytics")
BACKEND_COLUMNS = ("Backend", "Kind", "ReachWeaver status", "Platforms", "Approval authority",
                   "Owner live receipt")
# platform_registry id -> distribution_registry id where the two registries differ.
DISTRIBUTION_ALIAS = {"devto": "dev", "facebook": "facebook_pages"}
# media/storyboard target id -> platform_registry id.
MEDIA_ALIAS = {"youtube_shorts": "youtube"}
DELIVERY_FOR_ADAPTER = {"live_text": "mock_tested_text", "none": "manual_only"}


def _scorecard_receipts() -> dict[str, dict[str, str]]:
    """Areas the readiness scorecard marks live_receipt_verified: id -> receipt file name and observed date."""
    import json
    data = json.loads((ROOT / "docs/release-readiness.json").read_text(encoding="utf-8"))
    found = {}
    for area in data["areas"]:
        if area["status"] == "live_receipt_verified":
            name = Path(area["receipt"]).name
            receipt = json.loads((ROOT / area["receipt"]).read_text(encoding="utf-8"))
            found[area["capability_id"]] = {"file": name, "date": receipt["published_at"][:10]}
    return found


SCORECARD_RECEIPTS = _scorecard_receipts()
_CODE = re.compile(r"`([^`]+)`")


def parse_table(text: str, name: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of the markdown table between ``<!-- {name}:start -->`` and ``:end -->``."""
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError(f"{name}: expected exactly one {start} ... {end} block")
    lines = [ln.strip() for ln in text[text.index(start) + len(start):text.index(end)].splitlines() if ln.strip()]
    if len(lines) < 3:
        raise ValueError(f"{name}: table needs a header, a separator and at least one row")
    split = [[cell.strip() for cell in ln.strip("|").split("|")] for ln in lines]
    if tuple(split[0]) != columns:
        raise ValueError(f"{name}: header {split[0]} != {list(columns)}")
    rows = []
    for number, cells in enumerate(split[2:], start=3):
        if len(cells) != len(columns):
            raise ValueError(f"{name}: line {number} has {len(cells)} cells, expected {len(columns)}")
        rows.append(dict(zip(columns, cells)))
    return rows


def first_code(cell: str) -> str | None:
    match = _CODE.search(cell)
    return match.group(1) if match else None


def codes(cell: str) -> set[str]:
    return set(_CODE.findall(cell))


def _ids(table: dict, alias: dict[str, str]) -> set[str]:
    return {alias.get(key, key) for key in table}


def mismatches(rows: list[dict[str, str]], backend_rows: list[dict[str, str]], platforms: dict,
               dist: dict) -> list[str]:
    """Every disagreement between the matrix rows and the registries/code."""
    errors: list[str] = []
    registry = {p["id"]: p for p in platforms["platforms"]}
    schedule = {p["id"]: p for p in dist["platforms"]}
    seen = [first_code(r["Registry id"]) for r in rows]
    if len(seen) != len(set(seen)):
        errors.append(f"duplicate registry ids in matrix: {seen}")
    if set(seen) != set(registry):
        errors.append(f"matrix ids {sorted(map(str, seen))} != platform_registry ids {sorted(registry)}")
    check_ids = _ids(media.CONSTRAINTS, MEDIA_ALIAS)
    board_ids = _ids(storyboard.SAFE_ZONES, MEDIA_ALIAS) | _ids(storyboard.IMAGE_CANVAS, MEDIA_ALIAS)
    for row in rows:
        pid = first_code(row["Registry id"])
        entry = registry.get(pid or "")
        if entry is None:
            continue
        adapter = first_code(row["ReachWeaver adapter"])
        if adapter != entry["reachweaver_adapter"]:
            errors.append(f"{pid}: adapter {adapter!r} != registry {entry['reachweaver_adapter']!r}")
        backend = first_code(row["Via Postiz backend"])
        expected_backend = "allowed" if pid in postiz.PROVIDERS else "refused"
        if backend != expected_backend:
            errors.append(f"{pid}: Postiz {backend!r} != postiz.PROVIDERS says {expected_backend!r}")
        dist_entry = schedule.get(DISTRIBUTION_ALIAS.get(pid, pid))
        if dist_entry is None:
            errors.append(f"{pid}: no distribution_registry entry")
        else:
            mode = first_code(row["Native scheduling"])
            if mode != dist_entry["scheduling"]["mode"]:
                errors.append(f"{pid}: native scheduling {mode!r} != registry {dist_entry['scheduling']['mode']!r}")
            if DELIVERY_FOR_ADAPTER.get(adapter or "") != dist_entry["reachweaver_delivery"]:
                errors.append(f"{pid}: adapter {adapter!r} contradicts distribution delivery "
                              f"{dist_entry['reachweaver_delivery']!r}")
        receipt = SCORECARD_RECEIPTS.get(pid)
        cell = row["Owner live receipt"]
        if receipt is None and first_code(cell) != "none":
            errors.append(f"{pid}: an owner live receipt needs dated first-party evidence and a test update")
        if receipt is not None and (first_code(cell) != receipt["date"] or f"receipts/{receipt['file']}" not in cell):
            errors.append(f"{pid}: Owner live receipt cell must show {receipt['date']} and link receipts/{receipt['file']}")
        tools = codes(row["Media"])
        for tool, covered in (("media-check", pid in check_ids), ("media-storyboard", pid in board_ids),
                              ("manual-export", pid in manual_export.MANUAL_PLATFORMS)):
            if (tool in tools) != covered:
                errors.append(f"{pid}: Media column {'omits' if covered else 'claims'} {tool}")
        analytics = row["Analytics"]
        fetch = pid in metrics_fetch.SOURCES
        if ("metrics-fetch --online" in codes(analytics)) != fetch:
            errors.append(f"{pid}: Analytics column {'omits' if fetch else 'claims'} metrics-fetch")
        if fetch and not all(word in analytics.lower() for word in ("views", "impressions", "clicks", "unknown")):
            errors.append(f"{pid}: metrics-fetch rows must say views/impressions/clicks are unknown")
    live = {first_code(r["Registry id"]) for r in rows if first_code(r["ReachWeaver adapter"]) == "live_text"}
    if live != set(SUPPORTED_LIVE):
        errors.append(f"live_text rows {sorted(map(str, live))} != policy.SUPPORTED_LIVE {sorted(SUPPORTED_LIVE)}")
    kinds = [first_code(r["Kind"]) for r in backend_rows]
    if kinds != [postiz.BACKEND]:
        errors.append(f"backend rows {kinds} != [{postiz.BACKEND!r}]")
    for row in backend_rows:
        if codes(row["Platforms"]) != set(postiz.PROVIDERS):
            errors.append(f"backend platforms {sorted(codes(row['Platforms']))} != postiz.PROVIDERS")
        if first_code(row["Owner live receipt"]) != "none":
            errors.append("backend: an owner live receipt needs dated first-party evidence and a test update")
    return errors


class PlatformMatrixDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = MATRIX.read_text(encoding="utf-8")
        cls.rows = parse_table(cls.text, "platform-matrix", COLUMNS)
        cls.backend = parse_table(cls.text, "backend-matrix", BACKEND_COLUMNS)
        cls.platforms = capabilities.load_registry()
        cls.dist = distribution.load_registry()

    def test_matrix_agrees_with_registries_and_code(self):
        self.assertEqual(mismatches(self.rows, self.backend, self.platforms, self.dist), [])

    def test_linkedin_is_member_text_and_postiz_is_a_backend_row(self):
        linkedin = next(r for r in self.rows if first_code(r["Registry id"]) == "linkedin")
        self.assertEqual(first_code(linkedin["ReachWeaver adapter"]), "live_text")
        self.assertIn("personal profile", linkedin["ReachWeaver adapter"])
        self.assertIn("organization pages refused", linkedin["ReachWeaver adapter"])
        self.assertEqual(self.backend[0]["Backend"], "Postiz")
        self.assertIn("mock-tested", self.backend[0]["ReachWeaver status"])

    def test_adapter_drift_is_detected(self):
        changed = deepcopy(self.platforms)
        next(p for p in changed["platforms"] if p["id"] == "linkedin")["reachweaver_adapter"] = "none"
        self.assertTrue(any("linkedin: adapter" in e for e in mismatches(self.rows, self.backend, changed, self.dist)))

    def test_scheduling_and_missing_platform_drift_is_detected(self):
        dist = deepcopy(self.dist)
        next(p for p in dist["platforms"] if p["id"] == "telegram")["scheduling"]["mode"] = "unverified"
        self.assertTrue(any("telegram: native scheduling" in e
                            for e in mismatches(self.rows, self.backend, self.platforms, dist)))
        rows = [r for r in self.rows if first_code(r["Registry id"]) != "hashnode"]
        self.assertTrue(any("platform_registry ids" in e for e in mismatches(rows, self.backend, self.platforms, self.dist)))

    def test_claimed_receipt_and_wrong_analytics_are_detected(self):
        rows = deepcopy(self.rows)
        telegram_row = next(r for r in rows if first_code(r["Registry id"]) == "telegram")
        telegram_row["Owner live receipt"] = "`2026-10-09`"
        telegram = next(r for r in rows if first_code(r["Registry id"]) == "telegram")
        telegram["Analytics"] = "`metrics-fetch --online`"
        errors = mismatches(rows, self.backend, self.platforms, self.dist)
        self.assertTrue(any("owner live receipt" in e for e in errors))
        self.assertTrue(any("telegram: Analytics column claims metrics-fetch" in e for e in errors))

    def test_parser_rejects_malformed_tables(self):
        good = "<!-- platform-matrix:start -->\n| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS)
        with self.assertRaises(ValueError):
            parse_table(good + "\n| a | b |\n<!-- platform-matrix:end -->", "platform-matrix", COLUMNS)
        with self.assertRaises(ValueError):
            parse_table(good + "\n", "platform-matrix", COLUMNS)
        with self.assertRaises(ValueError):
            parse_table(self.text.replace("| Registry id |", "| Id |", 1), "platform-matrix", COLUMNS)


if __name__ == "__main__":
    unittest.main()
