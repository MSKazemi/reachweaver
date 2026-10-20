"""One short, read-only line for Claude Code's status line.

``reachweaver statusline`` (or, fastest, ``python3 -m reachweaver.statusline``) prints for example::

    RW 0.10.0 | drafts 3 | queued 1 | needs review 0 | ideas 2 | PAUSED

- ``drafts``: posts waiting for your approval; ``queued``: approved or scheduled posts not sent yet;
  ``needs review``: sends whose outcome you must check on the platform (``reconcile``); ``sending`` only
  when a send is in flight; ``ideas``: open ideas in the inbox (``reachweaver idea``); ``PAUSED`` when the
  emergency stop is on.
- Read-only and fast: it imports only what it needs, opens an EXISTING database with SQLite ``mode=ro``
  and ``PRAGMA query_only`` and a short busy timeout, and never creates, migrates or locks anything. No
  database prints ``RW: no workspace``.
- Never prints post text, account names, credentials or paths; counts and the version only. No network.
- Claude Code sends session JSON on stdin; this command never reads stdin, so it cannot block on it.
- It always exits 0 so a status bar never shows an error trace; problems print a short ``RW: ...`` line.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 0.25
NO_WORKSPACE = "RW: no workspace"


def _counts(database: Path) -> tuple[dict[str, int], bool]:
    con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS,
                          isolation_level=None)
    try:
        con.execute("PRAGMA query_only=ON")
        statuses = {row[0]: int(row[1]) for row in con.execute("SELECT status, COUNT(*) FROM posts GROUP BY status")}
        try:
            row = con.execute("SELECT value FROM settings WHERE name='paused'").fetchone()
        except sqlite3.OperationalError:  # a very old database without the settings table
            row = None
        return statuses, row is not None and row[0] == "1"
    finally:
        con.close()


def line(db: str | Path | None = None) -> str:
    """The status text for the database ``--db`` / ``$REACHWEAVER_DB`` / the active workspace names."""
    from . import __version__
    from .storage import database_path
    database = database_path(db)
    if database.is_symlink():
        return "RW: database unreadable"
    if not database.is_file():
        return NO_WORKSPACE
    try:
        statuses, paused = _counts(database)
    except sqlite3.OperationalError as exc:
        return "RW: database busy" if "locked" in str(exc).lower() else "RW: database unreadable"
    except sqlite3.Error:
        return "RW: database unreadable"
    parts = [f"RW {__version__}", f"drafts {statuses.get('draft', 0)}",
             f"queued {statuses.get('approved', 0) + statuses.get('scheduled', 0)}"]
    if statuses.get("sending"):
        parts.append(f"sending {statuses['sending']}")
    parts.append(f"needs review {statuses.get('needs_review', 0)}")
    try:
        from .ideas import FILENAME, counts
        inbox = database.parent / FILENAME
        if inbox.is_file():
            parts.append(f"ideas {counts(inbox)['open']}")
    except Exception:  # a damaged inbox must not hide the queue counts
        parts.append("ideas ?")
    if paused:
        parts.append("PAUSED")
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Fast entry point (``python3 -m reachweaver.statusline [--home DIR] [--db PATH]``)."""
    import argparse
    parser = argparse.ArgumentParser(prog="reachweaver statusline",
                                     description="One read-only status line for Claude Code; never creates a database")
    parser.add_argument("--home", type=Path, metavar="DIR", help="Workspace folder (default: the nearest one)")
    parser.add_argument("--db", help="Database path (default: $REACHWEAVER_DB or state/reachweaver.sqlite)")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else 2
    print(render(args.db, args.home))
    return 0


def render(db: str | None, home_dir: Path | None, *, activate: bool = True) -> str:
    """Never raises: any problem becomes a short ``RW: ...`` line."""
    try:
        if activate:
            from . import home
            home.activate(home_dir, Path.cwd())
        return line(db)
    except Exception:
        return "RW: status unavailable" if home_dir is None else NO_WORKSPACE


if __name__ == "__main__":
    raise SystemExit(main())
