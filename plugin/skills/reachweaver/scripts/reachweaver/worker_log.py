"""Structured worker logs for ``publish --log-json`` (issue #26, slice S11). Observational only.

One JSON object per line, written through ``privacy.structured_log`` (every value passes
``privacy.redact``, emails included). Each line records one worker decision with a fixed
event name and, for skips and gate refusals, a fixed reason code. Lines never carry post
text, third-party handles, credential names or values, provider error text or URL query
strings: identifiers are allow-listed by shape, errors are reduced to sanitized codes and
remote URLs to scheme, host and path.

The logger never changes what ``publish`` does. ``open_worker_log`` and ``WorkerLog.start``
run before ``pending()``, before any account is loaded and before any claim; a failure
there raises ``ValidationError`` (CLI exit 2) so the run is refused cleanly. After the
start line, a failed write is counted and swallowed, never raised, so a full disk or a
closed pipe cannot interrupt a send or its reconciliation bookkeeping. The only database
access is two ``SELECT`` statements after a Postiz backend hand-off, made only when logging
is enabled.

JSON Lines: https://jsonlines.org/ (accessed 2026-10-09). Under systemd a unit's stderr
defaults to ``StandardError=inherit`` and stdout to ``StandardOutput=journal``, so the lines
reach journald (systemd.exec(5), https://man7.org/linux/man-pages/man5/systemd.exec.5.html,
accessed 2026-10-09).
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import urlsplit

from .accounts import ACCOUNT_ID
from .health import sanitize_error_code
from .policy import PLATFORMS, ValidationError
from .privacy import redact, structured_log
from .storage import (BINDING_MEDIA_CHANGED, BINDING_MISMATCH, BINDING_MISSING, BINDING_UNAVAILABLE, COOLDOWN_REFUSAL,
                      REPEAT_REFUSAL)
from .identity import IDENTITY_MISMATCH, IDENTITY_MISSING
from .send_options import SEND_OPTIONS_CHANGED, SEND_OPTIONS_MISSING

SCHEMA_VERSION = 1
EVENTS = ("run_start", "gate_refused", "post_skipped", "post_claimed", "post_published",
          "post_needs_review", "run_end")
GATE_REASONS = ("live_gate", "emergency_pause", "human_confirmation")
SKIP_REASONS = (
    "dry_run",                    # preview only: nothing blocks it, a live run would try it
    "not_due",                    # --post-id names a post that is not approved and due
    "paused",                     # emergency pause (dry-run preview or seen inside the claim lock)
    "account_missing",            # no configured account, or its platform differs
    "manual_only",                # platform has no live adapter
    "optout_blocked",             # mentions an opt-out listed handle (issue #26)
    "rebind_required",            # approval hash missing or mismatched (issue #7)
    "cooldown",                   # provider cooldown after a 429 (issue #11, including a shared scope)
    "account_disconnected",       # the owner ran `disconnect` for this account (issue #38)
    "unresolved_send_hold",       # another post of the account is needs_review or sending (issue #41)
    "repeat_of_shared_material",  # exact repeat of material this account shared or may have shared (sheet item 1)
    "session_budget",             # Bluesky createSession local budget exhausted (#11)
    "capability_refused",         # capability registry refused text publish
    "credentials_missing",        # credential variables absent (names are not logged)
    "content_preflight_failed",   # validate_post problems
    "identity_preflight_failed",  # read-only account identity check failed
    "budget",                     # rolling 24 h cap or minimum gap
    "claim_refused",              # any other claim refusal
    "not_eligible",               # claim found the post no longer due/approved
    "backend_refused",            # the Postiz backend path returned without a claim
    "reply_refused",              # reply target invalid or not valid for the account now (issue #17)
    "owner_triggered_only",       # platform terms forbid automated posting: held from scheduled/bulk runs
)
OUTCOMES = ("completed", "error")
SEND_CLASSES = ("certain_not_sent", "likely_not_created", "ambiguous", "unknown")
BUDGET_CODES = ("account_rolling_24h_send_attempt_cap_reached", "account_minimum_send_attempt_gap_not_elapsed")
BINDING_CODES = (BINDING_MISSING, BINDING_MISMATCH, BINDING_UNAVAILABLE, BINDING_MEDIA_CHANGED,
                IDENTITY_MISMATCH, IDENTITY_MISSING, SEND_OPTIONS_MISSING, SEND_OPTIONS_CHANGED)
COUNT_KEYS = ("due", "skipped", "claimed", "published", "needs_review")

_POST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REMOTE_ID = re.compile(r"^[A-Za-z0-9._:/~@+=-]{1,512}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}(?::[a-z][a-z0-9_]{0,79})?$")
_URL_PATH = re.compile(r"^[A-Za-z0-9._~/@+:%!$&'()*,;=-]{0,1024}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_RUN_ID_LOCK = threading.Lock()
_last_run_ns = 0


def new_run_id() -> str:
    """Sortable id that strictly increases within a process: UTC nanoseconds plus 6 hex chars."""
    global _last_run_ns
    with _RUN_ID_LOCK:
        now = max(time.time_ns(), _last_run_ns + 1)
        _last_run_ns = now
    seconds, nanos = divmod(now, 1_000_000_000)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(seconds))
    return f"{stamp}.{nanos:09d}Z-{secrets.token_hex(3)}"


def safe_post_id(value: object) -> str:
    return value if isinstance(value, str) and _POST_ID.fullmatch(value) else "invalid"


def safe_account_id(value: object) -> str:
    return value if isinstance(value, str) and ACCOUNT_ID.fullmatch(value) else "invalid"


def safe_platform(value: object) -> str:
    return value if isinstance(value, str) and value in PLATFORMS else "other"


def safe_remote_id(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = redact(str(value), emails=True)
    return text if _REMOTE_ID.fullmatch(text) else "invalid"


def safe_remote_url(value: object) -> str | None:
    """scheme://host[:port]/path only: no userinfo, query string or fragment."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return "invalid"
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or not _HOST.fullmatch(host):
        return "invalid"
    path = parts.path if _URL_PATH.fullmatch(parts.path) else "/invalid"
    return redact(f"{parts.scheme}://{host}{f':{port}' if port else ''}{path}", emails=True)


def error_code(exc: BaseException | None = None, *, stored: object = None) -> str:
    """Sanitized code: a PublishError code as is, a stored ``last_error`` head, or the error kind."""
    if exc is not None:
        text = str(exc)
        if _CODE.fullmatch(text):
            return text
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(exc).__name__).lower()
        return name if _CODE.fullmatch(name) else "error"
    code = sanitize_error_code(stored)
    return code if _CODE.fullmatch(code) else "unclassified"


def claim_refusal_reason(exc: BaseException) -> str:
    """Map a ``claim_sending`` ValidationError to a fixed reason code."""
    text = str(exc)
    if text in BUDGET_CODES:
        return "budget"
    if text.startswith(COOLDOWN_REFUSAL):
        return "cooldown"
    if text.startswith(BINDING_CODES):
        return "rebind_required"
    if text.startswith(REPEAT_REFUSAL):
        return "repeat_of_shared_material"
    if text.startswith("Global emergency pause"):
        return "paused"
    return "claim_refused"


def _send_class(value: object) -> str:
    return value if isinstance(value, str) and value in SEND_CLASSES else "unknown"


def _open_private_append(path: Path) -> TextIO:
    """Append-only handle on a regular, owner-only file; created 0600, never via a symlink."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    for name in ("O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"):  # O_NONBLOCK: a FIFO cannot block us
        flags |= getattr(os, name, 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValidationError(f"--log-file {path} is not a regular file")
        if os.name == "posix":
            if info.st_mode & 0o077:
                raise ValidationError(f"--log-file {path} is readable or writable by group/others; "
                                      "chmod 600 it or choose a new path")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise ValidationError(f"--log-file {path} is owned by another user")
            import fcntl
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
        handle = os.fdopen(fd, "a", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    return handle


class WorkerLog:
    """Fixed-vocabulary JSON-lines recorder for one ``publish`` run. Disabled: every call is a no-op."""

    def __init__(self, stream: TextIO | None, *, dry_run: bool, owned: bool = False):
        self.stream = stream
        self.enabled = stream is not None
        self.dry_run = dry_run
        self.owned = owned
        self.run_id = new_run_id() if self.enabled else ""
        self.seq = 0
        self.dropped = 0
        self.counts = {key: 0 for key in COUNT_KEYS}
        self._started = False
        self._ended = False

    # ------------------------------------------------------------------ writing
    def _write(self, name: str, fields: dict[str, Any], *, strict: bool = False) -> None:
        if not self.enabled:
            return
        try:
            if name not in EVENTS:
                raise ValueError("unknown worker log event")
            self.seq += 1
            structured_log(name, _stream=self.stream, schema_version=SCHEMA_VERSION, run_id=self.run_id,
                           seq=self.seq, dry_run=self.dry_run, **fields)
            assert self.stream is not None
            self.stream.flush()
        except Exception as exc:
            if strict:
                raise ValidationError(f"Structured log is not writable ({type(exc).__name__}); "
                                      "no account was loaded and nothing was sent") from None
            self.dropped += 1  # observational only: never interrupt a send or its bookkeeping

    def _post_fields(self, post) -> dict[str, Any]:
        try:
            return {"post_id": safe_post_id(post["id"]), "account_id": safe_account_id(post["account_id"]),
                    "platform": safe_platform(post["platform"])}
        except Exception:  # observational only: a malformed row never interrupts a send
            return {"post_id": "invalid", "account_id": "invalid", "platform": "other"}

    # ------------------------------------------------------------------ events
    def start(self, *, live_requested: bool, scheduled_only: bool, limit: int, post_id: str | None) -> None:
        """First line; a failure here refuses the run before any work (strict)."""
        self._started = True
        self._write("run_start", {"live_requested": live_requested, "scheduled_only": scheduled_only,
                                  "limit": limit, "post_id": safe_post_id(post_id) if post_id else None},
                    strict=True)

    def due(self, count: int) -> None:
        self.counts["due"] = count

    def gate_refused(self, reason: str) -> None:
        self._write("gate_refused", {"reason": reason if reason in GATE_REASONS else "live_gate"})

    def skipped(self, post, reason: str, **extra: Any) -> None:
        self.counts["skipped"] += 1
        if not self.enabled:
            return
        fields = self._post_fields(post) if post is not None else {}
        fields["reason"] = reason if reason in SKIP_REASONS else "claim_refused"
        if "rate_limited" in extra:
            fields["rate_limited"] = bool(extra["rate_limited"])
        self._write("post_skipped", fields)

    def skipped_id(self, post_id: str, reason: str) -> None:
        self.skipped({"id": post_id, "account_id": None, "platform": None}, reason)

    def claimed(self, post, *, backend: str | None = None) -> None:
        self.counts["claimed"] += 1
        if not self.enabled:
            return
        self._write("post_claimed", {**self._post_fields(post), "backend": backend})

    def published(self, post, remote_id: object, remote_url: object, *, backend: str | None = None) -> None:
        self.counts["published"] += 1
        if not self.enabled:
            return
        try:
            remote = {"remote_id": safe_remote_id(remote_id), "remote_url": safe_remote_url(remote_url)}
        except Exception:  # observational only: never raise after finish_send
            remote = {"remote_id": "invalid", "remote_url": "invalid"}
        self._write("post_published", {**self._post_fields(post), "backend": backend, **remote})

    def needs_review(self, post, *, send_class: object, code: str, rate_limited: bool = False,
                     backend: str | None = None) -> None:
        self.counts["needs_review"] += 1
        if not self.enabled:
            return
        self._write("post_needs_review", {**self._post_fields(post), "backend": backend,
                                          "send_class": _send_class(send_class),
                                          "error_code": code if _CODE.fullmatch(code) else "unclassified",
                                          "rate_limited": bool(rate_limited)})

    def backend_result(self, con, post, code: int, *, backend: str) -> None:
        """After a backend hand-off: read the post's local outcome (SELECT only) and record it."""
        if not self.enabled:
            return
        try:
            row = con.execute("SELECT status,remote_id,remote_url,last_error FROM posts WHERE id=?",
                              (post["id"],)).fetchone()
            detail = con.execute("""SELECT detail FROM event_log WHERE post_id=? AND action='needs_review'
                                    ORDER BY id DESC LIMIT 1""", (post["id"],)).fetchone()
        except Exception:
            self.dropped += 1
            return
        status = row["status"] if row is not None else None
        if status in ("published", "needs_review"):
            self.claimed(post, backend=backend)
        if status == "published":
            self.published(post, row["remote_id"], row["remote_url"], backend=backend)
        elif status == "needs_review":
            tokens = str(detail["detail"] if detail else "").split()
            klass = tokens[-1][len("class="):] if tokens and tokens[-1].startswith("class=") else "unknown"
            self.needs_review(post, send_class=klass, code=error_code(stored=row["last_error"]), backend=backend)
        else:
            self.skipped(post, "backend_refused")

    def probe(self, read: Callable[[], Any], default: Any = None) -> Any:
        """Evaluate a read-only lookup for a log field; disabled or failing: ``default``, never raised."""
        if not self.enabled:
            return default
        try:
            return read()
        except Exception:
            self.dropped += 1
            return default

    def end(self, *, exit_code: int | None, error: BaseException | None = None,
            paused: Callable[[], Any] | None = None) -> None:
        if self._ended or not self._started or not self.enabled:
            return
        self._ended = True
        fields: dict[str, Any] = {"outcome": "error" if error is not None else "completed",
                                  "exit_code": exit_code,
                                  "error_code": error_code(error) if error is not None else None,
                                  "counts": dict(self.counts)}
        if paused is not None:
            fields["emergency_paused"] = bool(self.probe(paused, default=False))
        fields["dropped_lines"] = self.dropped
        self._write("run_end", fields)

    def close(self) -> None:
        if self.enabled and self.dropped:
            try:
                print(f"WARNING: {self.dropped} structured log line(s) could not be written", file=sys.stderr)
            except Exception:
                pass
        if self.owned and self.stream is not None:
            try:
                self.stream.close()
            except Exception:
                pass


def open_worker_log(enabled: bool, path: Path | None, *, dry_run: bool) -> WorkerLog:
    """Validate and open the destination before any work; a failure raises ValidationError."""
    if path is not None and not enabled:
        raise ValidationError("--log-file requires --log-json")
    if not enabled:
        return WorkerLog(None, dry_run=dry_run)
    if path is None:
        return WorkerLog(sys.stderr, dry_run=dry_run)
    try:
        handle = _open_private_append(Path(path))
    except OSError as exc:
        raise ValidationError(f"Cannot open --log-file {path} ({exc.strerror or type(exc).__name__}); "
                              "no account was loaded and nothing was sent") from None
    return WorkerLog(handle, dry_run=dry_run, owned=True)
