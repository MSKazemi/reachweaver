"""``reachweaver studio``: the read-only dashboard and the post composer in ONE process, linked to each other.

The two pages stay two loopback servers with their own per-run tokens and every guard they have alone:

* the dashboard (``ui_server``) is still GET/HEAD-only, opens the database ``mode=ro`` and has no write route;
* the composer (``share_browser``) still checks Host, Origin, its ``X-ReachWeaver-Token`` header and body sizes on
  every POST, and can still only write private local files and the terminal ``published_manual`` record.

What ``studio`` adds is only that each server is told the other's full local URL, so the pages show real links
("Write a new post" opens the composer; "See how your posts did" opens the dashboard's Results). Each URL carries
the OTHER server's token, so:

* it is returned only by a token-gated JSON route of a server in this same process (dashboard ``api/studio``,
  composer ``/api/catalog``), never written into static HTML, never stored, never logged;
* both servers send ``Referrer-Policy: no-referrer`` (and the pages carry the same meta tag), the composer's token
  sits in the URL fragment (never sent in a request), and links are ``rel="noopener noreferrer"``;
* the strict Content-Security-Policies are unchanged (no third-party load; ``connect-src 'self'``), so neither page
  can fetch from the other: a link is a top-level navigation only;
* the tokens are new for every run and both servers bind 127.0.0.1 only.

Holding the dashboard URL of a studio run therefore also gives the composer link: keep both URLs private, as the
terminal says. A dashboard started alone (``ui``) or a composer started alone (``share --web``) has no link and keeps
its copyable-command fallback. Nothing here approves, schedules or sends; Ctrl+C stops both servers.

The only file ``studio`` itself may create is an EMPTY local database when none exists yet (exactly what
``reachweaver init`` does), because the read-only dashboard never creates one.
"""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

from . import share_browser, ui_server
from .policy import ValidationError

SEND_PATH = None


def command_prefix(args) -> tuple[str, ...]:
    """How the dashboard's prompts spell commands: the global options this run was started with."""
    prefix = ["reachweaver"]
    for flag, value in (("--home", getattr(args, "home", None)), ("--db", getattr(args, "db", None)),
                        ("--accounts", getattr(args, "accounts", None))):
        if value:
            prefix += [flag, str(value)]
    return tuple(prefix)


def link(dashboard: ui_server.DashboardServer, composer: share_browser.ShareServer) -> None:
    """Tell each server the other's full local URL (both loopback, both carrying the other's own token)."""
    dashboard.composer_url = composer.url
    composer.dashboard_url = ui_server.url_for(dashboard) + "#/results"


def has_posts(database: Path) -> bool:
    """True when the database holds at least one post (any status). Read-only; False when it cannot be read."""
    from .storage import connect_readonly
    try:
        with connect_readonly(database) as con:
            return con.execute("SELECT 1 FROM posts LIMIT 1").fetchone() is not None
    except Exception:  # noqa: BLE001 - only decides which page opens first; the dashboard reports real problems
        return False


def _wait_forever(threads: list[threading.Thread]) -> None:
    """Until Ctrl+C (KeyboardInterrupt) or until either server loop ends."""
    while all(t.is_alive() for t in threads):
        threads[0].join(0.5)


def serve_both(dashboard: ui_server.DashboardServer, composer: share_browser.ShareServer, *,
               wait: Callable[[list[threading.Thread]], None] | None = None) -> int:
    """Run both servers until ``wait`` returns or raises KeyboardInterrupt (Ctrl+C), then stop and close BOTH."""
    threads = [threading.Thread(target=server.serve_forever, name=name, daemon=True)
               for name, server in (("rw-dashboard", dashboard), ("rw-composer", composer))]
    for thread in threads:
        thread.start()
    try:
        (wait or _wait_forever)(threads)
    except KeyboardInterrupt:
        pass
    finally:
        for server in (dashboard, composer):
            server.shutdown()  # returns once that serve_forever loop has stopped
        for server in (dashboard, composer):
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
    return 0


def run(args) -> int:
    from .storage import connect, database_path
    database = database_path(args.db)
    if database.is_symlink():
        raise ValidationError("State database cannot be a symbolic link")
    created = False
    if not database.exists():
        with connect(args.db):  # the same empty database `reachweaver init` creates; nothing else is written
            pass
        created = True
    dashboard = ui_server.make_server(database, accounts_path=args.accounts,
                                      optout_path=str(args.optout) if args.optout else None, port=args.port,
                                      command_prefix=command_prefix(args))
    try:
        composer = share_browser.ShareServer(args.composer_port, db=args.db, accounts=args.accounts)
    except BaseException:
        dashboard.server_close()
        raise
    link(dashboard, composer)
    dashboard_url = ui_server.url_for(dashboard)
    if created:
        print(f"Created an empty local database at {database} (the same as 'reachweaver init'). No account was "
              "contacted.", flush=True)
    print("ReachWeaver studio (loopback only): your dashboard and your post composer, linked to each other.")
    print(f"Dashboard (read-only): {dashboard_url}")
    print(f"Composer (write a post, copy it into each app yourself): {composer.url}")
    print("Both addresses contain per-run tokens; keep them private. Nothing is posted or sent from either page.")
    print("Press Ctrl+C to stop both.", flush=True)
    if args.open:
        webbrowser.open(dashboard_url if has_posts(database) else composer.url)
    return serve_both(dashboard, composer)
