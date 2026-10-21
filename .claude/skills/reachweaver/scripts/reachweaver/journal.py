"""The posting journal and the on-disk archive: what was posted, when, where, with which metadata and files.

``journal`` is a read-only view of the local database: every post that went out (or may have gone out), plus the
material you recorded with ``mark-shared``, with the exact text, the account and platform, the times, who approved
it, the link the platform returned, an audit timeline of fixed action codes, and the files that went with it
(path, SHA-256, size, whether the file is still there, and optionally whether it still matches the recorded hash).

``archive`` writes the same record to a folder you can browse, back up or hand to someone: one folder per post with
the exact text (``post.txt``), the metadata (``meta.json``) and, with ``--copy-files``, hash-verified copies of the
attached images and video. The archive is private (folders 0700, files 0600), is created next to the database by
default, and is only ever added to: an existing post folder is never overwritten.

Neither command sends, schedules, approves or contacts anything. The database is opened read-only. The free-text
``detail`` of the audit log (which can hold provider text) and any error text are never emitted.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Iterable

from .policy import ValidationError
from .privacy import write_private_file
from .repeat_guard import sha256_file
from .report_format import csv_cell, markdown_cell
from .storage import (MANUAL_STATUS, journal_posts, parse_utc, post_media, post_timelines, post_video, shared_entries,
                      utc_now)
from .inputs import read_json_file

# posted, or possibly posted; published_manual = posted by hand and recorded with mark-shared --url (2026-10-21)
POSTED_STATUSES = ("published", "sending", "needs_review", "published_manual")
ALL_STATUSES = ("draft", "approved", "scheduled", "sending", "published", "needs_review", "cancelled",
                "published_manual")
MARKER = ".reachweaver-archive"
EXCERPT = 120
_ACTION = re.compile(r"[a-z_]{1,40}")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
NOTE = ("Local record. A remote link is what the platform returned at send time; confirm anything important on the "
        "native account. Files are recorded by path and SHA-256; copies exist only in an archive made with --copy-files.")


def _probe(path: str, expected_sha: str | None, verify: bool) -> dict:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        return {"available": False}
    info: dict = {"available": True}
    if verify and expected_sha:
        info["matches_record"] = sha256_file(target) == expected_sha
    return info


def _post_files(post: dict, verify: bool) -> list[dict]:
    files: list[dict] = []
    try:
        media = post_media(post)
    except ValidationError:
        media = []
        files.append({"kind": "image", "problem": "stored attachment record is malformed"})
    for item in media:
        entry = {"kind": "image", "source": item["source"], "sha256": item["sha256"], "mime": item.get("mime"),
                 "bytes": item.get("bytes"), "alt": item.get("alt")}
        if item["source"] == "local":
            entry["path"] = item["path"]
            entry.update(_probe(item["path"], item["sha256"], verify))
        else:
            entry["url"] = item.get("url")
        files.append(entry)
    try:
        video = post_video(post)
    except ValidationError:
        video = None
        files.append({"kind": "video", "problem": "stored video record is malformed"})
    if video is not None:
        entry = {"kind": "video", "source": "local", "path": video["path"], "sha256": video["sha256"],
                 "mime": video.get("mime"), "bytes": video.get("bytes")}
        entry.update(_probe(video["path"], video["sha256"], verify))
        files.append(entry)
    return files


def post_entry(post: dict, timeline: list[dict], *, verify_files: bool = False) -> dict:
    text = post["content"]
    by_hand = post["status"] == MANUAL_STATUS  # owner-declared: published by hand, recorded with mark-shared --url
    return {"id": post["id"], "source": "posted_by_hand" if by_hand else "reachweaver", "when": post["posted_at"],
            "platform": post["platform"], "account": post["account_id"] or None, "status": post["status"],
            "campaign": post["campaign"] or None,
            "remote_id": post["remote_id"], "remote_url": post["remote_url"], "source_url": post["source_url"] or None,
            "approved_by": post["approved_by"], "approved_at": post["approved_at"],
            "approval_channel": post["approval_channel"],
            "approval_sha256": (post["approved_payload_sha256"] or "")[:12] or None,
            "note": "posted by hand (owner-declared); never sent by ReachWeaver" if by_hand else None,
            "chars": len(text), "text": text, "files": _post_files(post, verify_files),
            "timeline": [item for item in timeline if _ACTION.fullmatch(str(item["action"]))]}


def shared_entry(row: dict) -> dict:
    files: list[dict] = [{"kind": "image", "sha256": sha, "available": None, "note": "hash recorded; the file was not kept"}
                         for sha in row["images"]]
    if row["video"]:
        files.append({"kind": "video", "sha256": row["video"], "available": None,
                      "note": "hash recorded; the file was not kept"})
    when = row["posted_at"] or row["recorded_at"]
    return {"id": row["id"], "source": "by_hand", "when": when, "platform": row["platform"],
            "account": row["account_id"], "status": "shared_by_hand", "campaign": None, "remote_id": None,
            "remote_url": row["url"] or None, "source_url": None, "approved_by": None, "approved_at": None,
            "approval_channel": None, "approval_sha256": None, "note": row["note"] or None,
            "chars": len(row["content"]), "text": row["content"], "files": files,
            "timeline": [{"at": row["recorded_at"], "action": "recorded_by_hand"}]}


def build_entries(con, *, statuses: Iterable[str] = POSTED_STATUSES, platform: str | None = None,
                  account: str | None = None, since: str | None = None, until: str | None = None,
                  post_id: str | None = None, limit: int = 100, by_hand: bool = True,
                  verify_files: bool = False) -> list[dict]:
    """Journal entries, newest first: ReachWeaver posts in ``statuses`` plus (optionally) hand-shared records."""
    rows = journal_posts(con, statuses=tuple(statuses), platform=platform, account=account, since=since, until=until,
                         post_id=post_id, limit=limit)
    timelines = post_timelines(con, [row["id"] for row in rows])
    entries = [post_entry(row, timelines[row["id"]], verify_files=verify_files) for row in rows]
    if by_hand and post_id is None:
        low, high = (parse_utc(since) if since else None), (parse_utc(until) if until else None)
        measured = {row["id"] for row in rows}
        for row in shared_entries(con):
            if row.get("post_id") and (row["post_id"] in measured or MANUAL_STATUS not in statuses):
                continue  # its measurable published_manual record is the journal entry (or was filtered out)
            entry = shared_entry(row)
            if platform is not None and entry["platform"] != platform:
                continue
            if account is not None and entry["account"] not in (None, account):
                continue
            if (low and entry["when"] < low) or (high and entry["when"] > high):
                continue
            entries.append(entry)
    entries.sort(key=lambda e: (e["when"] or "", e["id"]), reverse=True)
    return entries[:limit]


def _excerpt(entry: dict) -> str:
    text = entry.get("text")
    if text is None:
        text = entry.get("text_excerpt") or ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= EXCERPT else flat[:EXCERPT - 1] + "…"


def _file_label(entry: dict) -> str:
    files = entry.get("files") or []
    if not files:
        return "-"
    counts: dict[str, int] = {}
    for item in files:
        counts[item.get("kind", "file")] = counts.get(item.get("kind", "file"), 0) + 1
    return ", ".join(f"{n} {kind}" for kind, n in counts.items())


def _when(entry: dict) -> str:
    return (entry.get("when") or "unknown")[:16].replace("T", " ") + ("Z" if entry.get("when") else "")


def table_rows(entries: list[dict]) -> tuple[list[str], list[list[str]]]:
    headers = ["WHEN (UTC)", "PLATFORM", "ACCOUNT", "STATUS", "ID", "FILES", "LINK", "TEXT"]
    rows = [[_when(e), e["platform"], e["account"] or "-", e["status"], e["id"], _file_label(e),
             e["remote_url"] or "-", _excerpt(e)] for e in entries]
    return headers, rows


def render_json(entries: list[dict]) -> str:
    return json.dumps({"count": len(entries), "entries": entries, "note": NOTE}, indent=2, ensure_ascii=False)


def render_markdown(entries: list[dict], title: str = "Posting journal") -> str:
    lines = [f"# {title}", "", f"{len(entries)} entries, newest first. {NOTE}", "",
             "| When (UTC) | Platform | Account | Status | ID | Files | Link | Text |", "|---|---|---|---|---|---|---|---|"]
    for e in entries:
        cells = [_when(e), e["platform"], e["account"] or "-", e["status"], e["id"], _file_label(e),
                 e["remote_url"] or "-", _excerpt(e)]
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in cells) + " |")
    return "\n".join(lines) + "\n"


CSV_COLUMNS = ("when", "source", "platform", "account", "status", "id", "campaign", "remote_id", "remote_url",
               "approved_by", "approved_at", "approval_sha256", "chars", "files", "file_paths", "text")


def render_csv(entries: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in entries:
        paths = ";".join(f.get("path") or f.get("url") or f"sha256:{f['sha256'][:12]}" for f in e.get("files") or []
                         if f.get("sha256") or f.get("path") or f.get("url"))
        text = e["text"] if e.get("text") is not None else e.get("text_excerpt", "")
        row = [e["when"], e["source"], e["platform"], e["account"], e["status"], e["id"], e["campaign"],
               e["remote_id"], e["remote_url"], e["approved_by"], e["approved_at"], e["approval_sha256"], e["chars"],
               _file_label(e), paths, text]
        writer.writerow([csv_cell(value) if value is not None else "" for value in row])
    return out.getvalue()


# ---- archive -------------------------------------------------------------------------------------------------------

def _folder_name(entry: dict) -> str:
    when = (entry.get("when") or "undated")[:10]
    parts = [when, entry["platform"], entry["account"] or "no-account", entry["id"][:24]]
    return _SAFE.sub("-", "_".join(parts)).strip("-")


def _private_dir(path: Path) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)


def _copy_verified(source: str, destination: Path, expected_sha: str) -> str:
    """Copy a local file into the archive only if its bytes still match the recorded SHA-256."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError:
        return "missing"
    temp = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            import stat as stat_module
            if not stat_module.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return "missing"
            out = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(out, "wb") as target:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
                    target.write(chunk)
        if digest.hexdigest() != expected_sha:
            temp.unlink(missing_ok=True)
            return "changed"
        os.replace(temp, destination)
        return "copied"
    except OSError:
        temp.unlink(missing_ok=True)
        return "missing"
    finally:
        if fd != -1:
            os.close(fd)


def _prepare_archive_dir(out: Path) -> bool:
    """Create the archive folder (returns True) or accept an existing one that carries the marker."""
    if out.is_symlink():
        raise ValidationError(f"{out} is a symbolic link; archives are never written through one")
    marker = out / MARKER
    if out.exists():
        if not out.is_dir() or not marker.is_file():
            raise ValidationError(f"{out} exists and is not a ReachWeaver archive folder (no {MARKER}); "
                                  "choose a new folder or an existing archive")
        return False
    _private_dir(out)
    write_private_file(marker, json.dumps({"kind": "reachweaver-archive", "version": 1, "created_at": utc_now()}) + "\n")
    return True


def write_archive(entries: list[dict], out: Path, *, copy_files: bool = False) -> dict:
    created = _prepare_archive_dir(out)
    archived, skipped, copied, unavailable = [], [], 0, 0
    for entry in entries:
        folder = out / _folder_name(entry)
        if folder.exists():
            skipped.append(entry["id"])
            continue
        _private_dir(folder)
        text = entry["text"] if entry.get("text") is not None else ""
        write_private_file(folder / "post.txt", text)
        meta = {key: value for key, value in entry.items() if key != "text"}
        meta["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        meta["text_excerpt"] = _excerpt(entry)
        meta["archived_at"] = utc_now()
        files = [dict(item) for item in meta.get("files") or []]
        if copy_files:
            _private_dir(folder / "files")
        for number, item in enumerate(files, start=1):
            if item.get("path") and item.get("sha256"):
                if not copy_files:
                    item["archive"] = "not_requested"
                    continue
                name = f"{number:02d}-{item['kind']}-{_SAFE.sub('-', Path(item['path']).name)[:80]}"
                status = _copy_verified(item["path"], folder / "files" / name, item["sha256"])
                item["archive"] = status
                if status == "copied":
                    item["archived_as"] = f"files/{name}"
                    copied += 1
                else:
                    unavailable += 1
            elif item.get("url"):
                item["archive"] = "url_only"
            else:
                item["archive"] = "hash_only"
        meta["files"] = files
        write_private_file(folder / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        archived.append(entry["id"])
    _write_index(out)
    return {"dir": str(out), "created": created, "archived": archived, "skipped_existing": skipped,
            "files_copied": copied, "files_unavailable": unavailable}


def _write_index(out: Path) -> None:
    entries = []
    for meta in sorted(out.glob("*/meta.json")):
        try:
            entries.append(read_json_file(meta) | {"folder": meta.parent.name})
        except (OSError, ValueError):
            continue
    entries.sort(key=lambda e: (e.get("when") or "", e.get("id", "")), reverse=True)
    for name, body in (("index.md", render_markdown(entries, "Posting archive")), ("index.csv", render_csv(entries))):
        target, temp = out / name, out / (name + ".part")
        temp.unlink(missing_ok=True)
        write_private_file(temp, body)
        os.replace(temp, target)
