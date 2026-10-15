"""Test-only helper for fixtures that build mixed post states on one account (issue #41).

Since the account hold, ``claim_sending`` refuses to claim a post while another post of the same account is
``needs_review`` (or ``sending`` for longer than the reconcile threshold). Real runs never reach that state in sequence, but several fixtures
seed one post per status on a single account. ``unblocked`` moves the account's unresolved posts aside for the
duration of one claim and restores their exact statuses afterwards. Production code has no such switch.
"""
from __future__ import annotations

import contextlib

from reachweaver import storage


@contextlib.contextmanager
def unblocked(con, account_id: str):
    """Temporarily set the account's ``needs_review`` and ``sending`` posts to ``cancelled`` so a fixture can claim another post."""
    held = con.execute("SELECT id, status FROM posts WHERE account_id=? AND status IN ('needs_review','sending')",
                       (account_id,)).fetchall()
    stale = [(r["id"], r["status"]) for r in held]
    for pid, _ in stale:
        con.execute("UPDATE posts SET status='cancelled' WHERE id=?", (pid,))
    try:
        yield
    finally:
        for pid, status in stale:
            con.execute("UPDATE posts SET status=? WHERE id=?", (status, pid))


def claim(con, pid: str, *args, **kwargs):
    """``storage.claim_sending`` with the account hold lifted, for fixtures only."""
    account = con.execute("SELECT account_id FROM posts WHERE id=?", (pid,)).fetchone()["account_id"]
    with unblocked(con, account):
        return storage.claim_sending(con, pid, *args, **kwargs)
