"""Shared assertion for registry evidence dates (registry freshness).

A registry fact's checked date is pinned to the research batch that added it, but re-verifying the fact
later legitimately moves the date forward. These checks keep what the old exact-date pins guarded (a
real ISO date, never backfilled before its research batch, never later than the snapshot that records
it, never in the future) while letting a refreshed fact pass without editing the test.
"""
from __future__ import annotations

from datetime import date


def assert_checked_since(test, value: object, batch: str, snapshot: str | None = None) -> None:
    """``batch <= value <= snapshot`` (when given) and ``value <= today``; ``value`` is ``YYYY-MM-DD``."""
    test.assertIsInstance(value, str)
    assert isinstance(value, str)
    test.assertRegex(value, r"^\d{4}-\d{2}-\d{2}$")
    checked = date.fromisoformat(value)
    test.assertGreaterEqual(checked, date.fromisoformat(batch), f"{value} is earlier than its research batch {batch}")
    if snapshot is not None:
        test.assertLessEqual(checked, date.fromisoformat(snapshot), f"{value} is later than the snapshot {snapshot}")
    test.assertLessEqual(checked, date.today(), f"{value} is in the future")
