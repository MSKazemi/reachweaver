"""Local browser composer for manual social-media handoffs.

This is intentionally a separate, explicit user-run server from the read-only
operations dashboard. Only 127.0.0.1 is bound; it has no provider API client,
credentials, automatic sends, remote assets or persistent web session.
POSTs may create *private local manual-export folders only* after user action:
save a package (new, or a confirmed update of one not marked posted), autosave the
editor draft, attach a photo/video, and mark one network of a saved package as
posted by hand (``storage.record_manual_publish``: a terminal record that nothing
can send), optionally naming an account that the server checks against
accounts.json (read, never written). Nothing here can approve, schedule or send;
see share_composer_store.

``/api/idea`` only READS one open idea's text from the ideas inbox (the page offers it as the caption; the inbox
is never changed). ``share --web --text/--idea`` seeds the autosaved draft through the same store, and never
replaces an unsaved draft: the page offers the text instead. Under ``reachweaver studio`` the server also knows the
dashboard's full local URL (``dashboard_url``, carrying the dashboard's own token) and returns it only from the
token-gated catalog route, so the page can link to Results; every guard here is unchanged.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import unquote, urlsplit
import webbrowser

from . import share_composer_store as store
from .distribution import load_registry as load_distribution_registry
from .inputs import nesting_depth
from .manual_export import LABELS, MANUAL_PLATFORMS, YOUTUBE_TITLE_MISSING
from .manual_package import MAX_TALKING_POINT_CHARS
from .policy import ValidationError
from .share import (MAX_DESTINATIONS, MAX_TEXT_BYTES, MEDIA_CHECK_NOTE, _human_only, _media_lines,
                    api_text_platforms, destinations, make_packages)
from .share_browser_ui import HTML

SEND_PATH = None
BIND_ADDRESS = "127.0.0.1"
MAX_REQUEST_BYTES = 96 * 1024
MAX_BODY_DEPTH = 32  # a composer request nests 3-4 levels; deeper JSON is refused before parsing
REQUEST_TIMEOUT_SECONDS = 10  # per socket read, and the whole-body deadline for a POST
MEDIA_TIMEOUT_SECONDS = 120  # whole-body deadline for one photo/video upload (at most store.MAX_MEDIA_BYTES)
_ALLOWED_FIELDS = frozenset({"platforms", "text", "title", "note", "overrides", "media"})
_SAVE_FIELDS = frozenset({"mode", "package", "revision", "confirm"})
# Every POST route and the body it takes. Nothing else is routed; there is no send, approve or schedule route.
JSON_ROUTES = frozenset({"/api/preview", "/api/prepare", "/api/packages", "/api/package/open",
                         "/api/package/duplicate", "/api/mark-posted", "/api/draft/load", "/api/draft/save",
                         "/api/draft/clear", "/api/accounts", "/api/idea"})
IDEA_ID = re.compile(r"idea-[0-9a-f]{8}")
SEED_TEXT_BYTES = 8 * 1024  # an idea is at most ideas.TEXT_MAX characters; --text for the composer stays short too
MEDIA_ROUTE = "/api/media"
# Technical wording from the shared share/manual-export rules -> plain words for the page. The rule and its
# safety meaning are unchanged; only CLI flag and field names are replaced. Applied to errors and warnings.
PLAIN_WORDS = (
    ("variant.text must contain substantive text", "needs a caption: write a few words first"),
    ("YouTube needs platform_options.title (the video title); variant.text is the description",
     "YouTube needs a video title; your caption becomes the description"),
    (YOUTUBE_TITLE_MISSING, "YouTube needs a video title; your caption becomes the description"),
    ("Human-only communities need --note, not pre-written post text",
     "Human-only communities get your own community note, never a prepared post"),
    ("This selection is human-only. Remove --text/--file and supply --note",
     "this selection is human-only: write a short personal community note, and write the real post yourself "
     "in that community"),
    ("--title is only for YouTube", "The video title is only used for YouTube"),
    ("Choose a network with --platform instagram,threads or --guide", "Choose at least one network."),
    ("; try 'reachweaver share --list' or 'reachweaver delivery-options'.", "; choose one from the list."),
    ("Unknown manual destination", "Unknown network"),
    (MEDIA_CHECK_NOTE, "These checks ran offline from the file header only; alt text, captions, rights and consent "
                       "are yours to check"),
)
_NO_MEDIA = re.compile(r"no media manifest supplied: (?P<kinds>[a-z, ]+) not validated; attach the file yourself "
                       r"and run media-check before posting")
# manual_export.media_unchecked's wording; the CLI-only hint in brackets has no meaning on this page
_MEDIA_UNCHECKED = re.compile(r"(?P<need>[^.:]+ needs (?:a photo or video|a photo|a video|photos or videos)): "
                              r"attach it in the app\. .*?\(share --attach FILE or media-check MANIFEST checks it first\)")
METRICS_NEXT = "reachweaver metrics {post_id} --likes N --replies N"  # --observed-at defaults to now, --source to entered-by-hand


def plain(message: str, label: str = "") -> str:
    """``message`` in plain words for the composer page (see PLAIN_WORDS); ``label`` names the network."""
    found = _NO_MEDIA.search(message)
    if found and label:
        need = "a photo or video" if "image" in found.group("kinds") else "a video"
        message = message.replace(found.group(0), f"{label} needs {need}. Add it here or attach it in the "
                                                  f"{label} app.")
    unchecked = _MEDIA_UNCHECKED.search(message)
    if unchecked and label:
        need = unchecked.group("need").replace("This post needs", f"{label} needs", 1)  # the card names its network
        message = message.replace(unchecked.group(0), f"{need}. Add it here or attach it in the {label} app.")
    for old, new in PLAIN_WORDS:
        message = message.replace(old, new)
    return message


def catalog() -> list[dict[str, Any]]:
    """Copy-only destinations. ``api_capable`` networks are copy-only here too; it never means connected."""
    from .manual_package import generic_platforms
    items = {p["id"]: p for p in load_distribution_registry()["platforms"]}
    api = set(api_text_platforms())
    supported = set(MANUAL_PLATFORMS) | set(generic_platforms()) | api
    featured = ("instagram", "threads", "tiktok", "youtube", "facebook", "linkedin", "bluesky",
                "pinterest", "x", "dev", "xiaohongshu", "sharechat")
    order = [p for p in featured if p in supported]
    order.extend(sorted(supported - set(order)))
    return [
        {
            "id": p,
            "label": LABELS[p] if p in LABELS else items[p]["label"],
            "human_only": bool(items[p]["human_only"]) if p in items else False,
            "featured": p in featured,
            "api_capable": p in api,
        }
        for p in order
    ]


def _attachments(obj: dict) -> list[Path]:
    """The draft media ids the editor attached, resolved inside the private media store (no symlink)."""
    media = obj.get("media")
    if media is None:
        return []
    if not isinstance(media, list) or len(media) > store.MAX_MEDIA_FILES:
        raise ValidationError(f"Attach at most {store.MAX_MEDIA_FILES} photos or videos.")
    if len(set(map(str, media))) != len(media):
        raise ValidationError("The same file is attached twice.")
    return [store.media_path(m) for m in media]


def _incoming(obj: Any, files: list[Path] | None = None) -> list[tuple[str, dict]]:
    """Reject unrecognized or oversized fields before invoking existing package rules. ``files`` (a duplicated
    package's own media) replaces the draft media ids in ``obj['media']``."""
    if not isinstance(obj, dict):
        raise ValidationError("The post must be a JSON object.")
    unknown = set(obj) - _ALLOWED_FIELDS
    if unknown:
        raise ValidationError("Unexpected fields: " + ", ".join(sorted(unknown)))
    if files is None:
        files = _attachments(obj)
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
    note = obj.get("note")
    if note is None:  # null means no note, like a missing field
        note = ""
    if not isinstance(note, str):
        raise ValidationError("Community note must be plain text.")
    if len(note) > MAX_TALKING_POINT_CHARS:
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
                [platform], post, source_notes, source_title, None, files, None
            )
            packages.extend(result)
            errors.extend(problems)
    else:
        packages, errors = make_packages(selected, text, notes, title, None, files, None)
    if errors:
        raise ValidationError("; ".join(errors))
    return packages


def _safe_preview(packages: list[tuple[str, dict]]) -> list[dict[str, Any]]:
    result = []
    for platform, pkg in packages:
        content = pkg["content"]
        human = pkg.get("human_only") is True
        rows = (pkg.get("share") or {}).get("media_checks") or []
        media_lines = set(_media_lines(rows)) | {MEDIA_CHECK_NOTE} if rows else set()
        warnings = pkg["preflight"]["warnings"]
        label = pkg["platform_label"]
        result.append({
            "id": platform,
            "label": label,
            "human_only": human,
            "text": None if human else content["text"],
            "title": content.get("title"),
            "format": content.get("format", "notes" if human else "text"),
            "warnings": [plain(w, label) for w in warnings if w not in media_lines],
            "media_checks": [plain(w, label) for w in warnings if w in media_lines],
            "note": "Write this yourself under the community rules" if human else
                    (pkg.get("share") or {}).get("send_from_reachweaver") or
                    "Copy after reviewing the native app's options",
            "shortened": bool(((pkg.get("share") or {}).get("tailoring") or {}).get("shortened")),
            "published": False,
        })
    return result


def _only(obj: Any, allowed: frozenset[str] | set[str]) -> dict:
    if not isinstance(obj, dict):
        raise ValidationError("The request must be a JSON object.")
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ValidationError("Unexpected fields: " + ", ".join(sorted(unknown)[:5]))
    return obj


def _editor_fields(post: dict) -> dict:
    return {k: v for k, v in post.items() if k in ("platforms", "text", "title", "note", "overrides", "media")}


def _write_private(packages: list[tuple[str, dict]], files: list[Path] | None = None, editor: dict | None = None,
                   *, update: str | None = None, revision: str | None = None) -> Path:
    """All checks first; stage everything privately and rename only on success (store.write_package)."""
    media = [(path, path.name) for path in files or []]
    return store.write_package(packages, media, editor or {}, update=update, expected_revision=revision)


def _prepare(obj: Any) -> dict:
    """Save as a NEW package (default), or update one only with mode "update", its name, the revision the editor
    loaded and confirm: true. Every network is rechecked first; one failure writes nothing."""
    obj = _only(obj, _ALLOWED_FIELDS | _SAVE_FIELDS)
    post = {k: v for k, v in obj.items() if k not in _SAVE_FIELDS}
    mode = obj.get("mode", "new")
    update = None
    if mode == "update":
        if obj.get("confirm") is not True:
            raise ValidationError("Updating replaces this package's files: confirm it first. Nothing was saved.")
        update = obj.get("package")
        store.package_path(update)
    elif mode == "new":
        if any(k in obj for k in ("package", "revision", "confirm")):
            raise ValidationError("Save as new takes no package name; nothing existing is overwritten.")
    else:
        raise ValidationError("Choose save as new or update this package.")
    packages = _incoming(post)
    target = _write_private(packages, _attachments(post), _editor_fields(post), update=update,
                            revision=obj.get("revision"))
    state = store.package_state(target)
    return {"ready": True, "saved_to": str(target), "count": len(packages), "published": False,
            "package": state, "updated": update is not None, "display_name": state["display_name"],
            "folder": state["folder"],
            "notice": "Private files prepared locally. Open OPEN-FIRST.md and post by hand."}


def _open_package(obj: Any) -> dict:
    """Load a saved package back into the editor. Its media are copied into the draft media store, and the
    draft becomes this package (so a reload keeps it open)."""
    pkg = store.package_path(_only(obj, {"package"}).get("package"))
    editor = store.editor_from_package(pkg)
    state = store.package_state(pkg)
    store._sweep_media(keep=())  # opening replaces the editor content, and with it the draft's attachments
    media = store.media_into_store(pkg)
    editor["media"] = [m["id"] for m in media]
    draft = store.save_draft(dict(editor, package=pkg.name, revision=state["revision"]))
    return {"editor": draft, "media": media, "package": state}


def _duplicate_package(obj: Any) -> dict:
    """A copy of a saved package as a NEW package: rechecked, media copied (no symlink), no posted marks."""
    pkg = store.package_path(_only(obj, {"package"}).get("package"))
    editor = store.editor_from_package(pkg)
    files = store._package_media(pkg)
    post = {k: v for k, v in editor.items() if k != "media"}
    packages = _incoming(post, files)
    target = _write_private(packages, files, editor)
    return {"ready": True, "saved_to": str(target), "published": False, "package": store.package_state(target),
            "notice": f"Copied {pkg.name} into a new package. Nothing was uploaded or published."}


def _mark_posted(obj: Any, db: str | None, accounts: str | None = None) -> dict:
    """``account_id`` is optional; when given it must exist in accounts.json for this network's platform."""
    obj = _only(obj, {"package", "platform", "url", "posted_at", "account_id"})
    record = store.mark_posted(obj.get("package"), obj.get("platform"), obj.get("url"), obj.get("posted_at"), db,
                               account_id=obj.get("account_id"), accounts=accounts)
    return {"posted": record, "package": store.package_state(store.package_path(obj["package"])),
            "next": METRICS_NEXT.format(post_id=record["post_id"]),
            "notice": f"Recorded as {store.POSTED_LABEL} (post {record['post_id']}). Nothing was posted or sent, "
                      "and this record can never be approved, scheduled or sent."}


def _accounts(obj: Any, accounts: str | None) -> dict:
    """Read-only: public ids, platforms and handles from accounts.json for the optional account choice."""
    _only(obj, set())
    return {"accounts": store.list_accounts(accounts), "ledger_ids": dict(store.LEDGER_ID)}


def _draft_load(obj: Any, server: "ShareServer | None" = None) -> dict:
    _only(obj, set())
    draft = store.load_draft()
    media = [store.media_info(m) for m in draft["media"]] if draft else []
    offer = None
    if server is not None and server.offer is not None:  # a --text/--idea seed that met an unsaved draft: once
        offer, server.offer = server.offer, None
    return {"draft": draft, "media": media, "offer": offer}


def idea_text(idea_id: object, db: str | None) -> dict:
    """Read-only: one OPEN idea's id and text from the ideas inbox next to the database, for the composer to offer
    as a caption. Nothing is written; the inbox is never changed (an idea stays open until you drop or promote it)."""
    from . import ideas as ideas_inbox
    if not isinstance(idea_id, str) or not IDEA_ID.fullmatch(idea_id):
        raise ValidationError("Unknown idea.")
    for item in ideas_inbox.listing(ideas_inbox.inbox_path(db), status="open"):
        if item["id"] == idea_id:
            return {"id": item["id"], "text": item["text"]}
    raise ValidationError(f"No open idea called {idea_id}. It may have been dropped or promoted already.")


def _idea(obj: Any, db: str | None) -> dict:
    return {"idea": idea_text(_only(obj, {"idea"}).get("idea"), db)}


def draft_is_empty(draft: dict | None) -> bool:
    """True when there is nothing in the composer worth keeping: no caption, network, note, title, media or open
    package (a draft that is only whitespace counts as empty)."""
    if not draft:
        return True
    return not ((draft.get("text") or "").strip() or draft.get("platforms") or (draft.get("note") or "").strip()
                or (draft.get("title") or "").strip() or draft.get("media") or draft.get("overrides")
                or draft.get("package"))


def seed_draft(text: str, label: str) -> dict:
    """Start the composer from ``text`` (``share --web --text`` / ``--idea``). The draft is written through the
    normal store (allowlisted fields only) when the composer holds no unsaved draft. An unsaved draft is NEVER
    replaced: the result says so and carries an ``offer`` that the page shows once, so the owner can choose."""
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("The text to start the composer with is empty.")
    if len(text.encode("utf-8")) > SEED_TEXT_BYTES:
        raise ValidationError(f"The text to start the composer with is too long (at most {SEED_TEXT_BYTES} bytes); "
                              "paste it into the composer instead.")
    if draft_is_empty(store.load_draft()):
        store.save_draft({"text": text})
        return {"seeded": True, "offer": None,
                "message": f"Started the composer with {label}. It is a private draft on this computer; nothing is "
                           "posted."}
    return {"seeded": False, "offer": {"text": text, "label": label},
            "message": f"The composer already holds a post, so it was kept. The page offers {label} instead; you "
                       "choose whether to use it."}


def _draft_save(obj: Any) -> dict:
    draft = store.save_draft(obj)
    return {"saved": True, "media": len(draft["media"])}


def _draft_clear(obj: Any) -> dict:
    _only(obj, set())
    store.clear_draft()
    return {"cleared": True}


def _json(data: Any) -> bytes:
    try:
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate cannot be UTF-8; ASCII escapes keep the response valid JSON
        return json.dumps(data).encode("ascii")


def _lone_surrogate(obj: Any) -> bool:
    """True when the parsed request holds a lone UTF-16 surrogate (JSON allows "\\ud800"; UTF-8 does not)."""
    try:
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


class ShareServer(ThreadingHTTPServer):
    """Loopback-only HTTP server. A new random token authorizes API calls."""
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int = 0, db: str | None = None, accounts: str | None = None):
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValidationError("Local browser port must be between 0 and 65535.")
        self.token = secrets.token_urlsafe(32)
        self.db = db  # the history database "Mark as posted" records into (--db, else the workspace default)
        self.accounts = accounts  # accounts.json read (never written) for the optional account choice
        self.write_lock = threading.Lock()  # one composer request touches the files or the database at a time
        # Set only by `studio`: the dashboard's full local URL (it carries the DASHBOARD's own token), returned by
        # the token-gated catalog route so the page can link to Results. A composer started alone has none.
        self.dashboard_url: str | None = None
        self.offer: dict | None = None  # a --text/--idea seed kept out of an unsaved draft; offered once by the page
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

    def setup(self) -> None:
        super().setup()
        # A local client must not keep an unfinished request thread forever.
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

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
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
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
        # Bytes, not str: compare_digest raises TypeError on a non-ASCII str header value.
        return hmac.compare_digest(supplied.encode("utf-8"), self.server.token.encode("ascii"))

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
            self._json_response(200, {"platforms": catalog(), "max_destinations": MAX_DESTINATIONS,
                                      "dashboard": self.server.dashboard_url})
        else:
            self._json_response(404, {"errors": ["Not found."]})

    def _stream_body(self, size: int, sink: Callable[[bytes], None], timeout: float) -> bool:
        """Feed exactly ``size`` body bytes to ``sink`` within one overall deadline; False when short or slow."""
        deadline = time.monotonic() + timeout
        remaining = size
        try:
            while remaining:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.connection.settimeout(min(left, REQUEST_TIMEOUT_SECONDS))
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    return False
                sink(chunk)
                remaining -= len(chunk)
        except OSError:
            return False
        finally:
            self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        return True

    def _read_body(self, size: int) -> bytes | None:
        """The whole body within one overall deadline; None for a short or slow-dripped body."""
        chunks: list[bytes] = []
        return b"".join(chunks) if self._stream_body(size, chunks.append, REQUEST_TIMEOUT_SECONDS) else None

    def _refuse_method(self) -> None:
        self.close_connection = True  # an unread request body must not desync a kept-alive connection
        self._json_response(405, {"errors": ["Only GET and same-origin POST are supported; "
                                             "cross-origin requests are not."]})

    do_OPTIONS = do_PUT = do_DELETE = do_PATCH = _refuse_method

    def _content_type(self) -> str:
        return self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

    def _post_media(self) -> None:
        """One photo/video as a raw body (application/octet-stream), its name in X-ReachWeaver-Filename."""
        self.close_connection = True  # any refusal may leave body bytes unread
        if self._content_type() != "application/octet-stream":
            self._json_response(415, {"errors": ["Send the file as application/octet-stream."]})
            return
        raw_size = self.headers.get("Content-Length", "")
        if not raw_size.isdecimal() or not 0 < int(raw_size) <= store.MAX_MEDIA_BYTES:
            self._json_response(413, {"errors": [
                f"Each file must be 1 byte to {store.MAX_MEDIA_BYTES // (1024 * 1024)} MiB."]})
            return
        size = int(raw_size)
        try:
            name = unquote(self.headers.get("X-ReachWeaver-Filename", ""), errors="strict")
            info = store.store_media(name, size,
                                     lambda write: self._stream_body(size, write, MEDIA_TIMEOUT_SECONDS))
            self._json_response(200, {"media": info, "notice": "Kept privately on this computer. Nothing was "
                                                               "uploaded to a social network."})
        except (ValidationError, UnicodeError, ValueError) as exc:
            self._json_response(422, {"errors": [str(exc)]})
        except OSError:
            self._json_response(500, {"errors": ["Could not keep the file. Check disk permissions and space."]})

    def do_POST(self) -> None:
        if not self._host_ok() or not self._token_ok() or not self._origin_ok():
            self.close_connection = True
            self._json_response(403, {"errors": ["Not authorized from this browser session."]})
            return
        path = urlsplit(self.path).path
        if path == MEDIA_ROUTE:
            self._post_media()
            return
        if self._content_type() != "application/json":
            self.close_connection = True
            self._json_response(415, {"errors": ["Send application/json only."]})
            return
        raw_size = self.headers.get("Content-Length", "")
        if not raw_size.isdecimal() or not 0 < int(raw_size) <= MAX_REQUEST_BYTES:
            self.close_connection = True
            self._json_response(413, {"errors": ["Request is missing or too large."]})
            return
        if path not in JSON_ROUTES:
            self.close_connection = True
            self._json_response(404, {"errors": ["Not found."]})
            return
        body = self._read_body(int(raw_size))
        if body is None:
            self.close_connection = True
            self._json_response(408, {"errors": ["The request body was incomplete or too slow."]})
            return
        try:
            text = body.decode("utf-8")
            if nesting_depth(text) > MAX_BODY_DEPTH:  # explicit, so it no longer depends on the interpreter
                raise RecursionError("nested too deeply")
            obj = json.loads(text)
            if _lone_surrogate(obj):  # refuse before any message could quote it back
                self._json_response(400, {"errors": ["The request contains invalid Unicode (a lone surrogate)."]})
                return
            if path == "/api/preview":
                self._json_response(200, {
                    "ready": True,
                    "platforms": _safe_preview(_incoming(obj)),
                    "notice": "Preview only. Nothing saved, scheduled, uploaded or published.",
                })
                return
            with self.server.write_lock:
                data = self._route(path, obj)
            self._json_response(200, data)
        except store.Conflict as exc:  # the package changed or is marked posted: nothing was written
            self._json_response(409, {"errors": [str(exc)]})
        except RecursionError:  # deeply nested JSON: refuse it rather than drop the connection
            self._json_response(422, {"errors": ["The request JSON is nested too deeply."]})
        except (ValidationError, UnicodeError, ValueError, TypeError) as exc:
            self._json_response(422, {"errors": [plain(str(exc))]})
        except OSError:
            self._json_response(500, {"errors": [
                "Could not read or save local handoff files. Check disk permissions and available space."
            ]})

    def _route(self, path: str, obj: Any) -> dict:
        """Every POST route except preview and media. None of them approves, schedules or sends anything."""
        if path == "/api/prepare":
            return _prepare(obj)
        if path == "/api/packages":
            _only(obj, set())
            return {"packages": store.list_packages()}
        if path == "/api/package/open":
            return _open_package(obj)
        if path == "/api/package/duplicate":
            return _duplicate_package(obj)
        if path == "/api/mark-posted":
            return _mark_posted(obj, self.server.db, self.server.accounts)
        if path == "/api/accounts":
            return _accounts(obj, self.server.accounts)
        if path == "/api/draft/load":
            return _draft_load(obj, self.server)
        if path == "/api/idea":  # read-only: one open idea's text, which the page offers as the caption
            return _idea(obj, self.server.db)
        if path == "/api/draft/save":
            return _draft_save(obj)
        if path == "/api/draft/clear":
            return _draft_clear(obj)
        raise ValidationError("Not found.")


def seed_from(text: str | None, idea: str | None, db: str | None) -> tuple[str, str] | None:
    """(text, label) for ``share --web --text`` / ``--idea``, or None. The idea is only read from the inbox."""
    if text is not None and idea is not None:
        raise ValidationError("Choose --text or --idea for the composer, not both.")
    if idea is not None:
        found = idea_text(idea.strip().casefold(), db)
        return found["text"], f"the text of idea {found['id']}"
    if text is not None:
        return text, "your --text"
    return None


def apply_seed(server: ShareServer, seed: tuple[str, str] | None) -> None:
    """Seed the draft (or keep an unsaved one and let the page offer the text) and say which happened."""
    if seed is None:
        return
    result = seed_draft(*seed)
    server.offer = result["offer"]
    print(result["message"], flush=True)


def serve(port: int = 0, open_browser: bool = False, db: str | None = None, accounts: str | None = None,
          *, text: str | None = None, idea: str | None = None) -> int:
    """Owner-run loopback browser, stopped by Ctrl+C. No scheduled/background tasks."""
    seed = seed_from(text, idea, db)  # checked before a port is bound: a bad idea id starts nothing
    server = ShareServer(port, db=db, accounts=accounts)
    try:
        apply_seed(server, seed)
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
