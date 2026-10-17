"""Local, read-only dashboard server for ``reachweaver ui`` (stdlib ``http.server`` only).

Threat model (a browser on the same machine is the only client):

* binds to loopback only and refuses any other host;
* every URL carries a per-run random token (``http://127.0.0.1:PORT/<token>/``); a wrong or
  missing token is a bare 403, so another local process or a web page that guesses the port gets
  nothing, and the token never leaves the URL path (``Referrer-Policy: no-referrer``);
* ``Host`` must be the loopback name and port (DNS-rebinding defence); a cross-site ``Origin`` or
  ``Sec-Fetch-Site`` is refused;
* GET (and HEAD) only: there is no route that changes state, approves, publishes or pauses;
* the database is opened ``mode=ro`` per request (``ui_data.open_database``), never created;
* a strict Content-Security-Policy (no inline script or style, no remote anything);
* request lines are not logged (they contain the token); errors are JSON without tracebacks.

Importing this module opens no socket; ``make_server`` binds only when called.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import home, start, ui_assets, ui_data
from .accounts import accounts_path as resolve_accounts_path
from .accounts import load_accounts
from .distribution import GOALS, build_distribution_plan, load_registry
from .optout import load_optout, optout_source
from .policy import ValidationError
from .privacy import redact

LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
POST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
FILTER_VALUE = re.compile(r"[A-Za-z0-9_.:@ -]{1,80}")
CAMPAIGN_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,200}")  # any printable name; used only as a query parameter
SAFETY_TTL = 60  # seconds: the folder check hashes the files of unsent posts, so auto-refresh reuses it briefly
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                                "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
STATIC = {"": ("text/html; charset=utf-8", ui_assets.INDEX_HTML),
          "app.css": ("text/css; charset=utf-8", ui_assets.APP_CSS),
          "app.js": ("text/javascript; charset=utf-8", ui_assets.APP_JS)}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, *, database: Path, accounts_path: str | None,
                 optout_path: str | None, token: str):
        super().__init__(address, handler)
        self.database = database
        self.accounts_path = accounts_path
        self.optout_path = optout_path
        self.token = token
        self.safety_lock = threading.Lock()
        self.safety_cache: tuple[float, dict] | None = None

    @property
    def allowed_hosts(self) -> frozenset[str]:
        port = self.server_address[1]
        return frozenset(f"{name}:{port}" for name in LOOPBACK_HOSTS)


def make_server(database: str | Path, *, accounts_path: str | None = None, optout_path: str | None = None,
                host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> DashboardServer:
    """Bind a dashboard server on loopback. Raises ValidationError for a non-loopback host or a
    database that does not exist (this never creates one)."""
    if host not in LOOPBACK_HOSTS:
        raise ValidationError(f"The dashboard binds to loopback only ({', '.join(LOOPBACK_HOSTS)}); "
                              f"refusing host {host!r}")
    database = Path(database)
    if not database.is_file():
        raise ValidationError(f"Database {database} does not exist; the dashboard is read-only and never creates "
                              "one. Run 'reachweaver init' first")
    with ui_data.open_database(database):  # refuse a corrupt or pre-schema file now, not in the browser
        pass
    if not 0 <= port <= 65535:
        raise ValidationError("port must be 0..65535")
    token = token or secrets.token_urlsafe(24)
    bind = "127.0.0.1"  # 'localhost' is only accepted as a Host header name, never resolved for binding
    return DashboardServer((bind, port), Handler, database=database, accounts_path=accounts_path,
                           optout_path=optout_path, token=token)


def url_for(server: DashboardServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}/{server.token}/"


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "ReachWeaverDashboard"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection: no idle keep-alive threads, no body desync
    timeout = 10  # seconds a client may hold a connection without sending a complete request

    # -- plumbing ---------------------------------------------------------------------------------
    def log_message(self, format, *args):  # noqa: A002 - request lines contain the token: log nothing
        return

    def _send(self, status: int, content_type: str, body: bytes, *, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, document: dict) -> None:
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def _error(self, status: int, message: str, **extra) -> None:
        body = json.dumps({"error": redact(message)}).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, extra=extra)

    def _allowed(self) -> bool:
        """Host (DNS rebinding), Origin and Fetch-Metadata checks. False after the refusal is sent."""
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self._error(403, "forbidden: unexpected Host header")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            self._error(403, "forbidden: cross-origin request")
            return False
        # Fetch Metadata: a cross-site fetch/script/image request is refused. A top-level navigation
        # (Sec-Fetch-Mode: navigate) may arrive as cross-site, e.g. a link or an extension opening the
        # URL; it still needs the token and cannot read anything it did not already know the URL for.
        if (self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")
                and self.headers.get("Sec-Fetch-Mode") != "navigate"):
            self._error(403, "forbidden: cross-site request")
            return False
        return True

    # -- methods ----------------------------------------------------------------------------------
    def _refuse_method(self):
        self.close_connection = True  # a request body we never read must not desync a kept-alive connection
        self._error(405, "read-only dashboard: GET only", Allow="GET, HEAD", Connection="close")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse_method

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._allowed():
            return
        try:
            parts = urlsplit(self.path)
            query = parse_qs(parts.query, max_num_fields=12)
        except ValueError:
            self._error(400, "bad request")
            return
        segments = parts.path.split("/")[1:]  # ['<token>', ...]
        if not segments or not hmac.compare_digest(segments[0].encode(), self.server.token.encode()):
            self._error(403, "forbidden")
            return
        if len(segments) == 1:  # '/<token>' without the slash: relative asset URLs would resolve outside the token
            self.send_response(301)
            self.send_header("Location", f"/{segments[0]}/")
            self.send_header("Content-Length", "0")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            return
        rest = "/".join(segments[1:])
        try:
            if rest in STATIC:
                content_type, body = STATIC[rest]
                self._send(200, content_type, body.encode("utf-8"))
            elif rest.startswith("api/"):
                self._api(rest[4:], query)
            else:
                self._error(404, "not found")
        except ValidationError as exc:
            self._error(404 if str(exc).startswith(("Unknown post id", "Unknown campaign")) else 400, str(exc))
        except (OSError, ValueError, KeyError):
            self._error(500, "internal error reading local state")

    # -- API --------------------------------------------------------------------------------------
    @staticmethod
    def _filter(query: dict, name: str) -> str | None:
        values = query.get(name)
        if not values or not values[0]:
            return None
        if not FILTER_VALUE.fullmatch(values[0]):
            raise ValidationError(f"invalid {name} filter")
        return values[0]

    def _accounts(self):
        return ui_data.load_accounts_view(lambda: load_accounts(self.server.accounts_path))

    def _safety(self, con) -> dict:
        server = self.server
        with server.safety_lock:
            cached = server.safety_cache
            if cached is not None and time.monotonic() - cached[0] < SAFETY_TTL:
                return cached[1]
            accounts, error = self._accounts()
            document = ui_data.safety_view(con, server.database, None if error else accounts,
                                           resolve_accounts_path(server.accounts_path), home.root() or Path.cwd(),
                                           home.root())
            document["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            server.safety_cache = (time.monotonic(), document)
            return document

    def _api(self, route: str, query: dict) -> None:
        server = self.server
        if route == "next":  # the same read-only guidance as the `start` command
            guide = start.build_guide(server.database, server.accounts_path)
            guide["steps"] = [step for step in guide["steps"] if step["command"] != "reachweaver ui"]  # you are in it
            self._json(200, ui_data.redact_value(guide))
            return
        if route == "distribution/markets":
            registry = load_registry()
            self._json(200, {"goals": list(GOALS),
                             "markets": [{"id": m["id"], "label": m["label"]} for m in registry["markets"]]})
            return
        if route == "distribution":
            region, goal = self._filter(query, "region"), self._filter(query, "goal")
            if region is None or goal not in GOALS:
                raise ValidationError("distribution needs region and goal (one of " + ", ".join(GOALS) + ")")
            limit = self._filter(query, "limit") or "8"
            if not limit.isdigit():
                raise ValidationError("invalid limit")
            try:
                plan = build_distribution_plan(region, goal, limit=max(1, min(int(limit), 20)))
            except ValueError as exc:  # unknown region: the message lists the valid ones
                raise ValidationError(str(exc)) from None
            self._json(200, plan)
            return
        with ui_data.open_database(server.database) as con:
            if route == "overview":
                accounts, error = self._accounts()
                configured = optout_source(server.optout_path) is not None
                self._json(200, ui_data.overview(con, accounts, error, optout=load_optout(server.optout_path),
                                                 optout_configured=configured))
            elif route == "posts":
                self._json(200, ui_data.posts_list(
                    con, status=self._filter(query, "status"), account_id=self._filter(query, "account"),
                    campaign=self._filter(query, "campaign"), platform=self._filter(query, "platform")))
            elif route.startswith("posts/"):
                post_id = route[len("posts/"):]
                if not POST_ID.fullmatch(post_id):
                    raise ValidationError("Unknown post id")
                accounts, _ = self._accounts()
                self._json(200, ui_data.post_detail(con, post_id, accounts))
            elif route == "accounts":
                accounts, error = self._accounts()
                self._json(200, ui_data.accounts_view(accounts, error))
            elif route == "journal":
                self._json(200, ui_data.journal_view(con, platform=self._filter(query, "platform"),
                                                     account=self._filter(query, "account")))
            elif route == "schedule":
                self._json(200, ui_data.schedule_view(con))
            elif route == "safety":
                self._json(200, self._safety(con))
            elif route == "suggest":
                self._json(200, ui_data.suggestions_view(con))
            elif route == "results":
                self._json(200, ui_data.results_view(con, account=self._filter(query, "account"),
                                                     platform=self._filter(query, "platform")))
            elif route == "campaigns":
                self._json(200, ui_data.campaigns_view(con))
            elif route == "campaign":
                name = (query.get("name") or [""])[0]
                if not CAMPAIGN_NAME.fullmatch(name):
                    raise ValidationError("invalid campaign name")
                self._json(200, ui_data.campaign_detail(con, name))
            else:
                self._error(404, "not found")
