"""Approval replay ledger (wave 9 item 1, owner-approved 2026-10-10).

``docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md`` section 1. Every ``approve`` draws a fresh 128-bit
``approval_nonce`` that the approval hash binds (tag ``approval/2``, only when a nonce is present).
``storage.claim_sending`` appends one ``consumed`` line for ``(post_id, approval_nonce)`` to an
append-only, hash-chained file, ``approval-ledger.jsonl``, next to the database, BEFORE it flips the
row to ``sending``. A restored older approval row (or a ``published`` row set back to ``approved``)
carries a nonce the ledger already holds and is refused with ``approval_replay_reapproval_required``
before any network call. A human ``unapprove`` then ``approve`` draws a new nonce and works.

Withdrawal (security decision item 12, owner-approved 2026-10-21): ``unapprove`` and ``cancel`` of a
row that carries a nonce append one ``withdrawn`` line for ``(post_id, approval_nonce)`` under the
same flock, before the row changes (``storage.transition``). ``check`` and ``consume`` treat a
withdrawn nonce like a consumed one, with its own fixed code ``approval_withdrawn_reapproval_required``,
so a database backup restored after a withdrawal cannot re-arm the withdrawn approval. A pair already
in the ledger (consumed, or withdrawn before) gets no second line: each pair holds at most one line,
whatever its event. A withdrawal is never refused because of the ledger: if the line cannot be written
the row is still withdrawn and the caller reports the gap (``withdraw`` returns the fixed code).
Ledgers written before this change hold only ``consumed`` lines and stay valid; older code reads a
``withdrawn`` line as a broken chain and fails closed.

File: mode 0600, ``O_NOFOLLOW``, owned by the running user, private directory (the database
directory, already checked by ``storage.connect``), appended with ``O_APPEND`` under an exclusive
``flock`` and ``fsync``. One JSON line per event; ``prev`` is the SHA-256 of the previous line's
bytes (without its newline), the first line points at 64 zeros.

Honest limit (docs/THREAT-MODEL.md T7): the file is editable by the same Unix user. The chain makes
silent in-place edits and mid-file removal detectable; a same-user attacker who rewrites the whole
chain is not stopped, and removing only the tail is caught by ``audit`` only while the database row
still exists. Real separation needs the ledger owned by the worker's system user or an owner
signature. A withdrawal made while the ledger was unusable (missing, unreadable, broken) is not
recorded, so a restore after it is not caught; the CLI prints a warning when that happens.

Every refusal is a fixed code; no message carries a path, an OS error, a nonce or a hash.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from pathlib import Path

try:  # POSIX only; the repo's deployments are POSIX, other platforms rely on the lock inside SQLite
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

LEDGER_FILENAME = "approval-ledger.jsonl"
NONCE_TAG = "reachweaver.storage.approval/2"
GENESIS = "0" * 64
MAX_LEDGER_BYTES = 32 * 1024 * 1024

REPLAY = "approval_replay_reapproval_required"
UNAVAILABLE = "approval_ledger_unavailable"
BROKEN = "approval_ledger_broken"
NONCE_MISSING = "approval_nonce_missing_reapproval_required"
WITHDRAWN = "approval_withdrawn_reapproval_required"
LEDGER_CODES = (REPLAY, UNAVAILABLE, BROKEN, NONCE_MISSING, WITHDRAWN)
REPLAY_EVENT = "approval_replay_blocked"
CONSUMED_EVENT = "consumed"
WITHDRAWN_EVENT = "withdrawn"
LINE_EVENTS = (CONSUMED_EVENT, WITHDRAWN_EVENT)
_CLOSED_CODE = {CONSUMED_EVENT: REPLAY, WITHDRAWN_EVENT: WITHDRAWN}

_NONCE = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_KEYS = {"v", "post_id", "approval_nonce", "payload_sha256", "event", "ts", "prev"}


def new_nonce() -> str:
    """A fresh 128-bit approval nonce (32 lowercase hex characters)."""
    return secrets.token_hex(16)


def is_nonce(value: object) -> bool:
    return isinstance(value, str) and bool(_NONCE.fullmatch(value))


def ledger_path(con: sqlite3.Connection) -> Path | None:
    """The ledger next to the database file of ``con``; None for an in-memory or unknown database."""
    try:
        rows = con.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return None
    for row in rows:
        if row[1] == "main":
            return Path(row[2]).parent / LEDGER_FILENAME if row[2] else None
    return None


class _Unavailable(Exception):
    """Internal: carries only a fixed code (never an OS error text)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _open(path: Path, *, write: bool, create: bool = False) -> int:
    flags = (os.O_RDWR | os.O_APPEND) if write else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if create:
        flags |= os.O_CREAT
    try:
        if os.name == "posix" and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise _Unavailable(UNAVAILABLE)
        fd = os.open(path, flags, 0o600)
    except _Unavailable:
        raise
    except OSError:
        raise _Unavailable(UNAVAILABLE) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LEDGER_BYTES:
            raise _Unavailable(UNAVAILABLE)
        if os.name == "posix" and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid()):
            raise _Unavailable(UNAVAILABLE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(fd, 1 << 20)
        except OSError:
            raise _Unavailable(UNAVAILABLE) from None
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _parse(data: bytes) -> tuple[list[dict], str]:
    """Verify the chain. Returns (entries, hash of the last line). Raises _Unavailable(BROKEN)."""
    if not data:
        return [], GENESIS
    if not data.endswith(b"\n"):
        raise _Unavailable(BROKEN)  # a torn or truncated last line
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    prev = GENESIS
    for raw in data[:-1].split(b"\n"):
        try:
            item = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _Unavailable(BROKEN) from None
        if (not isinstance(item, dict) or set(item) != _KEYS or item["v"] != 1 or item["event"] not in LINE_EVENTS
                or not all(isinstance(item[k], str) for k in _KEYS - {"v"})
                or not is_nonce(item["approval_nonce"]) or not _HEX64.fullmatch(item["payload_sha256"])
                or item["prev"] != prev):
            raise _Unavailable(BROKEN)
        key = (item["post_id"], item["approval_nonce"])
        if key in seen:
            raise _Unavailable(BROKEN)  # one line per approval: consumed once, or withdrawn once, never both
        seen.add(key)
        entries.append(item)
        prev = hashlib.sha256(raw).hexdigest()
    return entries, prev


def _load(path: Path) -> tuple[list[dict], str]:
    fd = _open(path, write=False)
    try:
        return _parse(_read_all(fd))
    finally:
        os.close(fd)


def _problem(path: Path | None) -> tuple[list[dict], str] | str:
    """Entries and last hash, or a fixed code."""
    if path is None:
        return UNAVAILABLE
    try:
        return _load(path)
    except _Unavailable as exc:
        return exc.code


def ensure(con: sqlite3.Connection) -> str | None:
    """Make sure a usable ledger exists before an approval is granted. None, or a fixed code.

    Created lazily (mode 0600) on the first approve. A missing file while some post already carries
    a nonce is refused, not silently recreated: that is what a deleted ledger looks like.
    """
    path = ledger_path(con)
    if path is None:
        return UNAVAILABLE
    if path.is_symlink():
        return UNAVAILABLE
    if path.exists():
        result = _problem(path)
        return result if isinstance(result, str) else None
    if _nonce_rows(con):
        return UNAVAILABLE
    try:
        fd = _open(path, write=True, create=True)
    except _Unavailable as exc:
        return exc.code
    os.close(fd)
    return None


def _nonce_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    try:
        have = {row[1] for row in con.execute("PRAGMA table_info(posts)")}
        if "approval_nonce" not in have:
            return []
        return con.execute("SELECT id,status,approval_nonce FROM posts WHERE approval_nonce IS NOT NULL").fetchall()
    except sqlite3.Error:
        return []


def _closed(entries: list[dict], post_id: str, nonce: str) -> str | None:
    """``REPLAY`` when the pair was consumed, ``WITHDRAWN`` when it was withdrawn, else None."""
    for entry in entries:
        if entry["post_id"] == post_id and entry["approval_nonce"] == nonce:
            return _CLOSED_CODE[entry["event"]]
    return None


def check(con: sqlite3.Connection, post_id: str, nonce: str) -> str | None:
    """Read-only: None when ``(post_id, nonce)`` is neither consumed nor withdrawn; else ``REPLAY``,
    ``WITHDRAWN`` or ``UNAVAILABLE``.

    A missing, unreadable, wrongly-permissioned, symlinked or chain-broken ledger is UNAVAILABLE
    (fail closed, nothing is claimed).
    """
    result = _problem(ledger_path(con))
    if isinstance(result, str):
        return UNAVAILABLE
    return _closed(result[0], post_id, nonce)


def _append(con: sqlite3.Connection, post_id: str, nonce: str, payload_sha256: str, now: str, *,
            event: str) -> str | None:
    """Append one ``event`` line for an open pair (flock, re-verify, append, fsync).

    None when the line was written; the pair's closing code (``REPLAY``/``WITHDRAWN``) when the ledger
    already holds a line for it (nothing is written); ``UNAVAILABLE`` when the ledger cannot be used.
    """
    path = ledger_path(con)
    if path is None or not is_nonce(nonce) or not _HEX64.fullmatch(str(payload_sha256)):
        return UNAVAILABLE
    try:
        fd = _open(path, write=True)
    except _Unavailable:
        return UNAVAILABLE
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            entries, prev = _parse(_read_all(fd))
        except _Unavailable:
            return UNAVAILABLE
        closed = _closed(entries, post_id, nonce)
        if closed:
            return closed
        line = json.dumps({"v": 1, "post_id": post_id, "approval_nonce": nonce, "payload_sha256": payload_sha256,
                           "event": event, "ts": now, "prev": prev},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
        try:
            if os.write(fd, line) != len(line):
                return UNAVAILABLE
            os.fsync(fd)
        except OSError:
            return UNAVAILABLE
        return None
    finally:
        os.close(fd)  # closing releases the flock


def consume(con: sqlite3.Connection, post_id: str, nonce: str, payload_sha256: str, now: str) -> str | None:
    """Append one ``consumed`` line. None, ``REPLAY``, ``WITHDRAWN`` or ``UNAVAILABLE``."""
    return _append(con, post_id, nonce, payload_sha256, now, event=CONSUMED_EVENT)


def withdraw(con: sqlite3.Connection, post_id: str, nonce: str, payload_sha256: object, now: str) -> str | None:
    """Record that a human withdrew the approval ``(post_id, nonce)`` (``unapprove`` or ``cancel``).

    None when the pair is closed in the ledger afterwards: a ``withdrawn`` line was appended, or the
    pair was already consumed or withdrawn (no second line). ``UNAVAILABLE`` when nothing could be
    written; the caller still withdraws the row and reports the gap. ``payload_sha256`` is the row's
    stored hash, recorded for the audit trail; a missing or malformed one is recorded as zeros.
    """
    digest = payload_sha256 if isinstance(payload_sha256, str) and _HEX64.fullmatch(payload_sha256) else GENESIS
    result = _append(con, post_id, nonce, digest, now, event=WITHDRAWN_EVENT)
    return UNAVAILABLE if result == UNAVAILABLE else None


def audit(con: sqlite3.Connection) -> dict:
    """Read-only health view: ``{"status", "problems"}``. Never creates or changes anything.

    status: ``ok``, ``absent`` (no ledger and no nonce in the database: normal after an upgrade),
    ``not_checked`` (in-memory database or no nonce column), ``broken``, ``unavailable``. Problems
    carry post ids and fixed codes only: an approved row whose nonce is already consumed (replay or
    crash window) or withdrawn (a database restored after ``unapprove``/``cancel``), or a
    sending/published/needs_review row whose nonce the ledger does not hold as consumed.
    """
    path = ledger_path(con)
    rows = _nonce_rows(con)
    if path is None:
        return {"status": "not_checked", "problems": []}
    if not path.exists() and not path.is_symlink():
        if rows:
            return {"status": "unavailable", "problems": [{"post_id": None, "reason": UNAVAILABLE}]}
        return {"status": "absent", "problems": []}
    result = _problem(path)
    if isinstance(result, str):
        return {"status": "broken" if result == BROKEN else "unavailable",
                "problems": [{"post_id": None, "reason": result}]}
    closed = {(e["post_id"], e["approval_nonce"]): e["event"] for e in result[0]}
    problems = []
    for row in rows:
        post_id, status, nonce = row[0], row[1], row[2]
        held = closed.get((post_id, nonce))
        if status in ("approved", "scheduled") and held:
            problems.append({"post_id": post_id, "reason": _CLOSED_CODE[held]})
        elif status in ("sending", "published", "needs_review") and held != CONSUMED_EVENT:
            problems.append({"post_id": post_id, "reason": BROKEN})
    return {"status": "ok" if not problems else "broken", "problems": problems}


def refusal_text(reason: str, post_id: str) -> str:
    """Operator text for a ledger reason code. Fixed strings plus the post id; no path, nonce or hash."""
    steps = (f"re-approve after review: reachweaver unapprove {post_id}; reachweaver show {post_id}; "
             f"reachweaver approve {post_id}")
    if reason == REPLAY:
        return (f"{reason}: the approval of post {post_id} was already used or restored from an older copy; {steps}")
    if reason == WITHDRAWN:
        return (f"{reason}: the approval of post {post_id} was withdrawn (unapprove or cancel) and this approved "
                f"copy comes from an older database or was edited back; {steps}")
    if reason == NONCE_MISSING:
        return (f"{reason}: post {post_id} was approved before approval nonces existed (wave 9 item 1); {steps}")
    return (f"{reason}: the approval ledger ({LEDGER_FILENAME}, next to the database) is missing, unreadable, "
            "not private, or its hash chain is broken; restore it from backup or have the owner review it; "
            "nothing was claimed")
