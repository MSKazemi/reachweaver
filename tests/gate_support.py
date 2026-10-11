"""Test-only helper for suites that exercise ``publishers.publish`` / ``publish_video`` as a library.

Since wave 9 item 3 those functions refuse a call that was not preceded by a committed
``storage.claim_sending`` of the same post (``reachweaver/sendgate.py``). These suites test the
adapters behind the gate with mocked transports and a fake post id and no database, so the wrappers
below record the same registry entry the real claim records (``sendgate.register_claim``, no new
production hook) and hold the live env gate for the duration of ONE call. Nothing is skipped: the
gate itself still runs in full, and ``tests/test_send_gate.py`` proves it refuses without this help.
"""
from __future__ import annotations

import os
from unittest.mock import patch

from reachweaver import connections, publishers, sendgate
from reachweaver.connections import VerifiedConnection

LIVE_ENV = {"REACHWEAVER_ALLOW_LIVE": "1", "REACHWEAVER_ENV": "prod"}


def grant(account, content, post_id, media=None) -> None:
    """Record a claim for exactly this call (as ``claim_sending`` would, with in-range live caps)."""
    if isinstance(account, dict) and isinstance(content, str) and isinstance(post_id, str):
        sendgate.register_claim(post_id, account_id=account.get("id"), platform=account.get("platform"),
                                content=content, media=media, max_per_24h=1, min_gap_minutes=60)


def publish(account, content, post_id, **kwargs):
    grant(account, content, post_id, kwargs.get("media"))
    with patch.dict(os.environ, LIVE_ENV):
        return publishers.publish(account, content, post_id, **kwargs)


def publish_video(account, content, post_id, video, **kwargs):
    grant(account, content, post_id)
    with patch.dict(os.environ, LIVE_ENV):
        return publishers.publish_video(account, content, post_id, video, **kwargs)


def issued_connection(*args, **kwargs) -> VerifiedConnection:
    """A VerifiedConnection that counts as returned by ``check_connection`` (what a patched
    ``cli.check_connection`` stands in for); a bare ``VerifiedConnection(...)`` is re-checked."""
    value = VerifiedConnection(*args, **kwargs)
    connections._ISSUED[id(value)] = value
    return value
