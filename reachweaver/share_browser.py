"""Local browser composer for manual social-media handoffs.

This is intentionally a separate, explicit user-run server from the read-only
operations dashboard. Only 127.0.0.1 is bound; it has no provider API client,
credentials, automatic sends, remote assets or persistent web session.
POSTs may create *private local manual-export folders only* after user action.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
import webbrowser

from . import home
from .distribution import load_registry as load_distribution_registry
from .manual_export import LABELS, MANUAL_PLATFORMS, write_export
from .manual_package import MAX_TALKING_POINT_CHARS, write_package
from .policy import ValidationError
from .privacy import write_private_file
from .share import MAX_DESTINATIONS, MAX_TEXT_BYTES, _human_only, _readme, destinations, make_packages
from .share_browser_ui import HTML

SEND_PATH = None
BIND_ADDRESS = "127.0.0.1"
MAX_REQUEST_BYTES = 96 * 1024
_ALLOWED_FIELDS = frozenset({"platforms", "text", "title", "note", "overrides"})


def catalog() -> list[dict[str, Any]]:
    """Only existing *manual* destinations. This does not mean connected accounts."""
    from .manual_package import generic_platforms
    items = {p["id"]: p for p in load_distribution_registry()["platforms"]}
    supported = set(MANUAL_PLATFORMS) | set(generic_platforms())
    featured = ("instagram", "threads", "tiktok", "youtube", "facebook",
                "pinterest", "x", "dev", "xiaohongshu", "sharechat")
    order = [p for p in featured if p in supported]
    order.extend(sorted(supported - set(order)))
    return [
        {
            "id": p,
            "label": LABELS[p] if p in LABELS else items[p]["label"],
            "human_only": bool(items[p]["human_only"]) if p in items else False,
            "featured": p in featured,
        }
        for p in order
    ]


def _incoming(obj: Any) -> list[tuple[str, dict]]:
    """Reject unrecognized or oversized fields before invoking existing package rules."""
    if not isinstance(obj, dict):
        raise ValidationError("The post must be a JSON object.")
    unknown = set(obj) - _ALLOWED_FIELDS
    if unknown:
        raise ValidationError("Unexpected fields: " + ", ".join(sorted(unknown)))
    raw_platforms = obj.get("platforms")
    if not isinstance(raw_platforms, list) or not all(
        isinstance(p, str) and 0 < len(p) < 100 for p in raw_platforms
    ):
        raise ValidationError("Select one or more social networks.")
    if len(raw_platforms) > MAX_DESTINATIONS:
        raise ValidationError(f"Choose at most {MAX_DESTINATIONS} networks.")
    selected = destinations(raw_platforms)
    text = obj.get("text")
    if text is not None and not isinstance(text, str):
        raise ValidationError("The caption must be plain text.")
    if isinstance(text, str) and len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValidationError("Caption is too large (maximum 64 KiB).")
    title = obj.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 200):
        raise ValidationError("YouTube title must be plain text, at most 200 characters.")
    note = obj.get("note", "")
    if not isinstance(note, str) or len(note) > MAX_TALKING_POINT_CHARS:
        raise ValidationError(
            f"Community note must be at most {MAX_TALKING_POINT_CHARS} characters."
        )
    notes = [note] if note.strip() else []
    overrides = obj.get("overrides", {})
    if not isinstance(overrides, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in overrides.items()
    ):
        raise ValidationError("Platform edits must be a map of network names to text.")
    human = _human_only()
    for platform, edited in overrides.items():
        if platform not in selected:
            raise ValidationError(f"An edit targets a network you did not select: {platform}.")
        if platform in human:
            raise ValidationError(f"{platform} is human-only; you cannot prepare post text.")
        if len(edited.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValidationError(f"Edited text for {platform} exceeds 64 KiB.")
    if overrides:
        if title is not None and "youtube" not in selected:
            raise ValidationError("Video title is only for a selected YouTube destination.")
        if notes and not any(p in human for p in selected):
            raise ValidationError("Community notes require a human-only destination.")
        packages: list[tuple[str, dict]] = []
        errors: list[str] = []
        for platform in selected:
            post = None if platform in human else overrides.get(platform, text)
            source_notes = notes if platform in human else []
            source_title = title if platform == "youtube" else None
            result, problems = make_packages(
                [platform], post, source_notes, source_title, None, [], None
            )
            packages.extend(result)
            errors.extend(problems)
    else:
        packages, errors = make_packages(selected, text, notes, title, None, [], None)
    if errors:
        raise ValidationError("; ".join(errors))
    return packages


def _safe_preview(packages: list[tuple[str, dict]]) -> list[dict[str, Any]]:
    result = []
    for platform, pkg in packages:
        content = pkg["content"]
        human = pkg.get("human_only") is True
        result.append({
            "id": platform,
            "label": pkg["platform_label"],
            "human_only": human,
            "text": None if human else content["text"],
            "title": content.get("title"),
            "format": content.get("format", "notes" if human else "text"),
            "warnings": pkg["preflight"]["warnings"],
            "note": "Write this yourself under the community rules" if human else
                    "Copy after reviewing the native app's options",
            "published": False,
        })
    return result


def _write_private(packages: list[tuple[str, dict]]) -> Path:
    """All checks first; stage everything privately and rename only on success."""
    parent = home.default_path(Path("state") / "shares")
    if parent.is_symlink():
        raise ValidationError("The Share output directory may not be a symlink.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        mode = stat.S_IMODE(parent.stat().st_mode)
        if mode & 0o077:
            raise ValidationError("The Share output folder must be private (chmod 700).")
    prefix = "share-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-"
    target = parent / (prefix + secrets.token_hex(6))
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
    try:
        for platform, pkg in packages:
            if platform in MANUAL_PLATFORMS:
                write_export(pkg, stage / platform)
            else:
                write_package(pkg, stage / platform)
        write_private_file(stage / "OPEN-FIRST.md", _readme(packages, []))
        if os.path.lexists(target):
            raise ValidationError("The generated output destination already exists.")
        stage.rename(target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return target


def _json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class ShareServer(ThreadingHTTPServer):
    """Loopback-only HTTP server. A new random token authorizes API calls."""
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int = 0):
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValidationError("Local browser port must be between 0 and 65535.")
        self.token = secrets.token_urlsafe(32)
        super().__init__((BIND_ADDRESS, port), ShareHandler)

    @property
    def origin(self) -> str:
        return f"http://{BIND_ADDRESS}:{self.server_port}"

    @property
    def url(self) -> str:
        # The fragment is not sent in HTTP requests or Referer headers.
        return self.origin + "/#" + self.token


class ShareHandler(BaseHTTPRequestHandler):
    server: ShareServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log captions, paths, tokens, URL fragments, headers or HTTP bodies.
        pass

    def _headers(self, code: int, mime: str, size: int, *, nonce: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        csp = ("default-src 'none'; base-uri 'none'; object-src 'none'; "
               "frame-ancestors 'none'; form-action 'none'; connect-src 'self'; "
               "img-src 'none'; font-src 'none'; "
               f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'")
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()

    def _send(self, code: int, data: bytes, mime: str = "application/json; charset=utf-8",
              *, nonce: str = "") -> None:
        self._headers(code, mime, len(data), nonce=nonce)
        self.wfile.write(data)

    def _json_response(self, code: int, data: Any) -> None:
        self._send(code, _json(data))

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"{BIND_ADDRESS}:{self.server.server_port}"

    def _token_ok(self) -> bool:
        supplied = self.headers.get("X-ReachWeaver-Token", "")
        return hmac.compare_digest(supplied, self.server.token)

    def _origin_ok(self) -> bool:
        return self.headers.get("Origin") == self.server.origin

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json_response(403, {"errors": ["Invalid local host."]})
            return
        path = urlsplit(self.path).path
        if path == "/":
            nonce = secrets.token_urlsafe(18)
            html = HTML.replace("__CSP_NONCE__", nonce).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8", nonce=nonce)
        elif path == "/api/catalog":
            if not self._token_ok():
                self._json_response(403, {"errors": ["This browser session is not authorized."]})
                return
            self._json_response(200, {"platforms": catalog(), "max_destinations": MAX_DESTINATIONS})
        else:
            self._json_response(404, {"errors": ["Not found."]})

    def do_OPTIONS(self) -> None:
        self._json_response(405, {"errors": ["Cross-origin requests are not supported."]})

    def do_POST(self) -> None:
        if not self._host_ok() or not self._token_ok() or not self._origin_ok():
            self._json_response(403, {"errors": ["Not authorized from this browser session."]})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json_response(415, {"errors": ["Send application/json only."]})
            return
        raw_size = self.headers.get("Content-Length", "")
        if not raw_size.isdecimal() or not 0 < int(raw_size) <= MAX_REQUEST_BYTES:
            self.close_connection = True
            self._json_response(413, {"errors": ["Request is missing or too large."]})
            return
        path = urlsplit(self.path).path
        if path not in ("/api/preview", "/api/prepare"):
            self._json_response(404, {"errors": ["Not found."]})
            return
        try:
            body = self.rfile.read(int(raw_size))
            obj = json.loads(body.decode("utf-8"))
            packages = _incoming(obj)
            if path == "/api/preview":
                self._json_response(200, {
                    "ready": True,
                    "platforms": _safe_preview(packages),
                    "notice": "Preview only. Nothing saved, scheduled, uploaded or published.",
                })
            else:
                destination = _write_private(packages)
                self._json_response(200, {
                    "ready": True, "saved_to": str(destination), "count": len(packages),
                    "published": False,
                    "notice": "Private files prepared locally. Open OPEN-FIRST.md and post by hand.",
                })
        except (ValidationError, UnicodeError, ValueError, TypeError) as exc:
            self._json_response(422, {"errors": [str(exc)]})
        except OSError:
            self._json_response(500, {"errors": [
                "Could not save local handoff files. Check disk permissions and available space."
            ]})


def serve(port: int = 0, open_browser: bool = False) -> int:
    """Owner-run loopback browser, stopped by Ctrl+C. No scheduled/background tasks."""
    server = ShareServer(port)
    try:
        print("ReachWeaver manual Share Composer (offline, loopback only)")
        print("Open this private local URL in your browser:")
        print(server.url, flush=True)
        print("This URL is for your current session. Keep it private.")
        print("No account is connected. No post can be published from this page.")
        print("Press Ctrl+C to stop.", flush=True)
        if open_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
    return 0
