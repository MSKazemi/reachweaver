"""Your workspace: what lives in the folder you run ReachWeaver from, what does not, and whether a copy will work.

A ReachWeaver workspace is a folder holding ``config/`` (public account metadata, the opt-out list, optional caps and
retention policy) and ``state/`` (the private database, the approval ledger next to it, and the archive made by
``archive``). Credentials are NOT in it: they live in the environment, a password manager or systemd credentials, so a
copied folder carries no secret and the new computer needs them set again.

``workspace`` is read-only. It reports the layout, whether the database is healthy and safe to copy right now, whether
the approval ledger still matches it, which credential variable NAMES are missing here, and which recorded attachment
files are missing or changed on this computer (attachments are recorded by absolute path and SHA-256, so a moved
workspace needs ``media-relink`` for posts that have not been sent yet).

``media-relink`` rewrites the folder prefix of recorded attachment paths. It is a dry run unless ``apply`` is set, it
only touches posts that are not in flight and not history, it changes a record only after the file at the new path has
been read and found to match the recorded SHA-256, and ``storage.rewrite_attachment_paths`` proves in one transaction that
the approval hash did not change. No network, credential or browser is involved.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Mapping

from . import ledger
from .accounts import missing_credentials
from .attachments import decode_media, encode_media
from .policy import ValidationError
from .repeat_guard import sha256_file
from .storage import RELINK_STATUSES, rewrite_attachment_paths, schema_gaps
from .video import decode_video, encode_video

UNSENT = ("draft", "approved", "scheduled", "needs_review")
OPTIONAL_FILES = (("opt-out list", "config/optout.json"), ("send caps", "config/caps.json"),
                  ("retention policy", "config/retention.json"))
NOT_IN_THE_FOLDER = (
    "Credentials (tokens, app passwords, the approval key): environment variables, your password manager or systemd "
    "credentials. Set them again on the new computer; `connect` lists the names still missing.",
    "The installed skill or plugin itself: install ReachWeaver on the new computer (`/plugin install ...`), then copy "
    "only this workspace.",
    "The update cache in ~/.cache/reachweaver (version check, previous-version backups): rebuilt on its own.",
    "Any worker log you pointed `publish --log-file` at, and any attachment files kept outside this folder.",
)


def _private(path: Path) -> bool | None:
    if os.name != "posix" or not path.exists():
        return None
    return stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def _entry(label: str, path: Path, *, must_be_private: bool = False) -> dict:
    exists = path.exists()
    return {"name": label, "path": str(path), "exists": exists, "bytes": path.stat().st_size if exists and path.is_file() else None,
            "private": _private(path) if must_be_private else None}


def _attachments(post: Mapping) -> list[tuple[str, dict]]:
    """(kind, record) for each local attachment of a stored post; malformed records are skipped (reported elsewhere)."""
    found: list[tuple[str, dict]] = []
    try:
        found.extend(("image", item) for item in decode_media(post["media_json"]) if item["source"] == "local")
    except ValidationError:
        pass
    try:
        video = decode_video(post["video_json"])
    except ValidationError:
        video = None
    if video is not None:
        found.append(("video", video))
    return found


def _file_state(record: dict, verify: bool) -> str:
    path = Path(record["path"])
    if path.is_symlink() or not path.is_file():
        return "missing"
    if verify and sha256_file(path) != record["sha256"]:
        return "changed"
    return "ok"


def inspect(con, db_path: Path, accounts: Mapping[str, dict] | None, accounts_path: Path, base: Path) -> dict:
    """Read-only report. ``con`` is a read-only connection to the database."""
    paths = [_entry("accounts", accounts_path), *[_entry(label, base / rel) for label, rel in OPTIONAL_FILES],
             _entry("database", db_path, must_be_private=True),
             _entry("approval ledger", db_path.parent / ledger.LEDGER_FILENAME, must_be_private=True),
             _entry("archive", db_path.parent / "archive", must_be_private=True)]
    problems: list[dict] = []

    def add(code: str, level: str, detail: str, advice: str) -> None:
        problems.append({"code": code, "level": level, "detail": detail, "advice": advice})

    database: dict = {"counts": {}}
    try:
        verdict = con.execute("PRAGMA integrity_check").fetchone()[0]
        database["integrity"] = "ok" if verdict == "ok" else "failed"
        database["counts"] = {r[0]: r[1] for r in con.execute("SELECT status, COUNT(*) FROM posts GROUP BY status")}
        database["schema_gaps"] = schema_gaps(con)
    except sqlite3.Error:
        database["integrity"] = "failed"
    if database["integrity"] != "ok":
        add("database_integrity_failed", "blocking", "SQLite reports the database is not intact",
            "Do not copy it. Restore from a backup, or export what you can with `data-export`.")
    for suffix in ("-journal", "-wal"):
        if Path(str(db_path) + suffix).exists():
            add("database_in_use", "blocking", f"{db_path.name}{suffix} exists, so a ReachWeaver run may be in progress",
                "Let it finish, then copy the folder. Copy the database and its -journal/-wal files together if you must.")
    if database.get("schema_gaps"):
        add("database_older_version", "info", "this database predates the installed version",
            "It is upgraded in place the first time a writing command opens it.")
    audit = ledger.audit(con)
    if audit["status"] in ("broken", "unavailable"):
        add("approval_ledger_" + audit["status"], "blocking", "the approval ledger does not match the database",
            "Copy `state/approval-ledger.jsonl` together with the database; without it approvals cannot be re-checked "
            "and a send for those approvals is refused.")

    files: list[dict] = []
    rows = con.execute("SELECT id, status, media_json, video_json FROM posts WHERE status<>'cancelled'").fetchall()
    for row in rows:
        for kind, record in _attachments(row):
            state = _file_state(record, verify=row["status"] in UNSENT)
            files.append({"post_id": row["id"], "status": row["status"], "kind": kind, "path": record["path"],
                          "state": state, "unsent": row["status"] in UNSENT})
    for item in files:
        if item["unsent"] and item["state"] != "ok":
            add("attachment_" + item["state"], "blocking", f"post {item['post_id']} ({item['status']}): {item['kind']} {item['path']}",
                "Put the file back at that path, or `media-relink --from OLD_FOLDER --to NEW_FOLDER`, then re-check.")
    if any(f["state"] != "ok" for f in files if not f["unsent"]):
        add("history_files_missing", "info", "some files of posts already sent are not on this computer",
            "Nothing to do: the record keeps their hashes. `archive --copy-files` keeps copies of the ones you still have.")

    credentials: dict[str, list[str]] = {}
    if accounts is None:
        add("accounts_file_missing", "info", f"{accounts_path} not found",
            "Copy config/accounts.json from the old computer, or start from config/accounts.example.json.")
    else:
        credentials = {name: missing_credentials(account) for name, account in accounts.items()}
    return {"paths": paths, "database": database, "ledger": audit["status"], "files": files,
            "credentials_missing": {k: v for k, v in credentials.items() if v},
            "not_in_the_folder": list(NOT_IN_THE_FOLDER), "problems": problems,
            "ready_to_copy": not any(p["level"] == "blocking" for p in problems)}


def render(report: dict) -> str:
    lines = ["Workspace", ""]
    for entry in report["paths"]:
        mark = "found  " if entry["exists"] else "absent "
        note = "" if entry["private"] in (None, True) else "  (readable by others: chmod it to 700/600)"
        lines.append(f"  {mark}{entry['name']:<16} {entry['path']}{note}")
    database = report["database"]
    if database.get("counts"):
        counts = ", ".join(f"{n} {status}" for status, n in sorted(database["counts"].items()))
        lines += ["", f"Database: {database['integrity']}; posts: {counts}", f"Approval ledger: {report['ledger']}"]
    unsent = [f for f in report["files"] if f["unsent"]]
    if report["files"]:
        bad = [f for f in unsent if f["state"] != "ok"]
        lines += ["", f"Attachments: {len(report['files'])} recorded file(s), {len(unsent)} on posts not yet sent, "
                      f"{len(bad)} of those missing or changed here"]
    if report["credentials_missing"]:
        lines += ["", "Credentials to set on this computer (names only):"]
        for account, names in report["credentials_missing"].items():
            lines.append(f"  {account}: {', '.join(names)}")
    lines += ["", "Not in this folder:"] + [f"  - {text}" for text in report["not_in_the_folder"]]
    if report["problems"]:
        lines += ["", "Findings:"]
        for p in report["problems"]:
            lines.append(f"  [{p['level']}] {p['code']}: {p['detail']}")
            lines.append(f"      -> {p['advice']}")
    lines += ["", "Ready to copy and use on another computer: " + ("yes" if report["ready_to_copy"] else "NO, fix the blocking findings first")]
    return "\n".join(lines)


# ---- media-relink --------------------------------------------------------------------------------------------------

def _moved(path: str, old: PurePosixPath, new: PurePosixPath) -> str | None:
    candidate = PurePosixPath(path)
    try:
        return str(new / candidate.relative_to(old))
    except ValueError:
        return None


def plan_relink(con, old_prefix: str, new_prefix: str) -> list[dict]:
    """One row per attachment under ``old_prefix`` of a post that may be relinked, with the check result."""
    old, new = PurePosixPath(old_prefix), PurePosixPath(new_prefix)
    if not old.is_absolute() or not new.is_absolute():
        raise ValidationError("--from and --to must be absolute folder paths")
    plan = []
    marks = ",".join("?" for _ in RELINK_STATUSES)
    for row in con.execute(f"SELECT id, status, media_json, video_json FROM posts WHERE status IN ({marks})", RELINK_STATUSES):
        for kind, record in _attachments(row):
            target = _moved(record["path"], old, new)
            if target is None:
                continue
            state = _file_state({"path": target, "sha256": record["sha256"]}, verify=True)
            plan.append({"post_id": row["id"], "status": row["status"], "kind": kind, "from": record["path"],
                         "to": target, "state": state})
    return plan


def apply_relink(con, plan: list[dict], old_prefix: str, new_prefix: str) -> dict:
    """Rewrite posts whose every moved attachment verified; others are left untouched and reported."""
    old, new = PurePosixPath(old_prefix), PurePosixPath(new_prefix)
    by_post: dict[str, list[dict]] = {}
    for item in plan:
        by_post.setdefault(item["post_id"], []).append(item)
    done, skipped = [], []
    for post_id, items in by_post.items():
        if any(item["state"] != "ok" for item in items):
            skipped.append(post_id)
            continue
        row = con.execute("SELECT media_json, video_json FROM posts WHERE id=?", (post_id,)).fetchone()
        media = decode_media(row["media_json"])
        for item in media:
            if item["source"] == "local":
                moved = _moved(item["path"], old, new)
                if moved is not None:
                    item["path"] = moved
        video = decode_video(row["video_json"])
        if video is not None:
            moved = _moved(video["path"], old, new)
            if moved is not None:
                video["path"] = moved
        rewrite_attachment_paths(con, post_id, media_json=encode_media(media),
                                 video_json=encode_video(video) if video is not None else None)
        done.append(post_id)
    return {"relinked": done, "skipped": skipped}
