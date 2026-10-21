"""Your workspace: what lives in the folder you run ReachWeaver from, what does not, and whether a copy will work.

A ReachWeaver workspace is a folder holding ``config/`` (public account metadata, the opt-out list, optional caps and
retention policy) and ``state/`` (the private database, the approval ledger and the ideas inbox next to it, and the
archive made by ``archive``). Credentials are NOT in it: they live in the environment, a password manager or systemd credentials, so a
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

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Mapping

from . import ledger
from .accounts import missing_credentials
from .attachments import decode_media, encode_media
from .ideas import FILENAME as IDEAS_FILENAME
from .policy import ValidationError
from .repeat_guard import sha256_file
from .storage import RELINK_STATUSES, rewrite_attachment_paths, schema_gaps, utc_now
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


def inspect(con, db_path: Path, accounts: Mapping[str, dict] | None, accounts_path: Path, base: Path,
            root: Path | None = None) -> dict:
    """Read-only report. ``con`` is a read-only connection to the database."""
    paths = [_entry("accounts", accounts_path), *[_entry(label, base / rel) for label, rel in OPTIONAL_FILES],
             _entry("database", db_path, must_be_private=True),
             _entry("approval ledger", db_path.parent / ledger.LEDGER_FILENAME, must_be_private=True),
             _entry("ideas inbox", db_path.parent / IDEAS_FILENAME, must_be_private=True),
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

    if root is None:
        add("not_a_marked_workspace", "info", "there is no .reachweaver-workspace marker here or above",
            "`workspace --init DIR` creates the one-folder layout (config, state, media, logs); until then copy config/ and "
            "state/ yourself.")
    else:
        outside = [f for f in files if f["unsent"] and not _inside(f["path"], root)]
        if outside:
            add("attachment_outside_workspace", "warning",
                f"{len(outside)} attachment(s) of unsent posts live outside {root}",
                "`workspace --gather --apply` copies them into media/ so the folder is self-contained.")
        secret_files = sorted(f.name for f in root.iterdir() if f.is_file() and (f.name == "secrets.env" or f.name.startswith(".env"))
                              and not f.name.endswith(".example"))
        if secret_files:
            add("secrets_in_folder", "warning", f"{', '.join(secret_files)} in this folder holds plain-text credentials",
                "ReachWeaver never writes it. Do not copy it with the workspace or over an insecure channel; keep it chmod 600 "
                "and move credentials through your password manager.")

    credentials: dict[str, list[str]] = {}
    if accounts is None:
        add("accounts_file_missing", "info", f"{accounts_path} not found",
            "Copy config/accounts.json from the old computer, or add an account: reachweaver account-add bluesky.")
    else:
        credentials = {name: missing_credentials(account) for name, account in accounts.items()}
    return {"workspace": str(root) if root is not None else None, "paths": paths, "database": database, "ledger": audit["status"], "files": files,
            "credentials_missing": {k: v for k, v in credentials.items() if v},
            "not_in_the_folder": list(NOT_IN_THE_FOLDER), "problems": problems,
            "ready_to_copy": not any(p["level"] == "blocking" for p in problems)}


def render(report: dict) -> str:
    lines = ["Workspace" + (f": {report['workspace']}" if report.get("workspace") else " (no marker file; see workspace --init)"), ""]
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


def apply_relink(con, plan: list[dict]) -> dict:
    """Rewrite posts whose every moved attachment verified; others are left untouched and reported.

    ``plan`` rows carry the exact ``from`` and ``to`` path; a post is rewritten only if all of its rows are ``ok``.
    """
    by_post: dict[str, list[dict]] = {}
    for item in plan:
        by_post.setdefault(item["post_id"], []).append(item)
    done, skipped = [], []
    for post_id, items in by_post.items():
        if any(item["state"] != "ok" for item in items):
            skipped.append(post_id)
            continue
        moved = {item["from"]: item["to"] for item in items}
        row = con.execute("SELECT media_json, video_json FROM posts WHERE id=?", (post_id,)).fetchone()
        media = decode_media(row["media_json"])
        for entry in media:
            if entry["source"] == "local" and entry["path"] in moved:
                entry["path"] = moved[entry["path"]]
        video = decode_video(row["video_json"])
        if video is not None and video["path"] in moved:
            video["path"] = moved[video["path"]]
        rewrite_attachment_paths(con, post_id, media_json=encode_media(media),
                                 video_json=encode_video(video) if video is not None else None)
        done.append(post_id)
    return {"relinked": done, "skipped": skipped}


# ---- the one-folder layout: init, gather, repair -------------------------------------------------------------------

LAYOUT = (("config", 0o755), ("state", 0o700), ("media", 0o700), ("logs", 0o700))
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
README_TEXT = """\
This folder is a ReachWeaver workspace: everything that is yours, in one place.

  config/   your accounts (handles and servers only, never a secret), opt-out list, caps
  state/    the private database, the approval ledger, your ideas inbox, the archive of what you posted
  media/    keep the images and videos you attach here
  logs/     use `reachweaver publish --log-file logs/worker.jsonl` for worker logs

To move to another computer: install ReachWeaver there, copy this whole folder (but NOT secrets.env if you made
one), `cd` into it and run `reachweaver workspace --repair --apply`. Set your credentials again on the new
computer (`reachweaver connect` lists the names). Details: docs/MOVING.md.

Credentials are never stored in this folder by ReachWeaver. If you keep your own secrets.env here, keep it
private (chmod 600) and do not send it over an insecure channel.
"""


def _example_accounts() -> Path | None:
    here = Path(__file__).resolve()
    # The first is the copy that ships inside the pip package; the others serve a checkout and a skill bundle.
    for candidate in (here.parent / "accounts.example.json", here.parents[1] / "config" / "accounts.example.json",
                      here.parents[2] / "assets" / "accounts.example.json"):
        if candidate.is_file():
            return candidate
    return None


def placeholder_accounts_json(networks: list[str]) -> str:
    """``config/accounts.json`` text with one unedited placeholder entry per chosen network (none: an empty list).

    The four networks in the example file reuse its entry; any other ``account-add`` network gets the same shape
    with ``example`` in its required public fields. ``account-add NETWORK`` later fills such an entry in place,
    and ``start``/``context`` report it as a placeholder until then.
    """
    from .account_add import SCHEMAS
    example = _example_accounts()
    shipped: dict[str, dict] = {}
    if example is not None:
        for entry in json.loads(example.read_text(encoding="utf-8"))["accounts"]:
            shipped.setdefault(entry["platform"], entry)
    entries = []
    for network in dict.fromkeys(networks):
        if network in shipped:
            entries.append(shipped[network])
            continue
        schema = SCHEMAS[network]
        entries.append({"id": f"my-{network}", "platform": network, "env_prefix": schema["prefix"],
                        **{field: "example" for field in schema["required"]}})
    return json.dumps({"accounts": entries}, indent=2) + "\n"


def ask_networks(attempts: int = 5) -> list[str]:
    """In a terminal, ask which networks to prepare placeholder accounts for; elsewhere (or on Enter) none."""
    import difflib
    import sys
    from .account_add import PLATFORMS
    if not sys.stdin.isatty():
        return []
    for _ in range(attempts):
        try:
            answer = input("Which networks do you use? (e.g. bluesky,instagram — Enter to skip): ").strip()
        except EOFError:
            return []
        if not answer:
            return []
        names = [n.strip().casefold() for n in answer.replace(" ", ",").split(",") if n.strip()]
        names = ["bluesky" if n == "bsky" else n for n in names]
        unknown = [n for n in names if n not in PLATFORMS]
        if not unknown:
            return list(dict.fromkeys(names))
        for name in unknown:
            close = difflib.get_close_matches(name, PLATFORMS, n=1, cutoff=0.6)
            print(f"Unknown network {name!r}." + (f" Did you mean {close[0]}?" if close else ""))
        print("Choose from: " + ", ".join(PLATFORMS) + ". Try again, or press Enter to skip.")
    return []


def next_steps(root: Path, networks: list[str] | None, accounts_created: bool) -> list[str]:
    """What to type after ``workspace --init``: the exact account-add command(s), or how to post by hand."""
    if not accounts_created:
        return ["Next: reachweaver doctor  (checks the accounts file that was already there)"]
    go = "" if root == Path.cwd().resolve() else f"cd {_shell_quote(str(root))}, then "
    if networks is None:
        return [f"Next: {go}replace the example accounts with yours: reachweaver account-add bluesky (fills the "
                "bluesky example in place); repeat for each network you use."]
    if networks:
        return ([f"Placeholders added for: {', '.join(networks)} (not real accounts yet). Next: {go}fill each in:"]
                + [f"  reachweaver account-add {network}" for network in networks])
    return [f"No accounts yet. Next: {go}reachweaver account-add bluesky (or instagram, mastodon...; public "
            "details only), or post by hand right away: reachweaver share --guide"]


def _shell_quote(text: str) -> str:
    import shlex
    return shlex.quote(text)


def init_workspace(root: Path, networks: list[str] | None = None) -> dict:
    """Create (or complete) the one-folder layout. Existing files are never overwritten or moved.

    ``networks``: None copies the whole example accounts file (four placeholder accounts, what ``workspace --init
    --with-examples`` asks for); a list writes placeholders for those networks only, and an empty list an empty
    accounts list, so a new folder starts with no pretend accounts.
    """
    from .home import MARKER
    created: list[str] = []
    kept: list[str] = []
    root.mkdir(parents=True, exist_ok=True)

    def make_dir(rel: str, mode: int) -> None:
        folder = root / rel
        (kept if folder.is_dir() else created).append(rel + "/")
        folder.mkdir(mode=mode, exist_ok=True)

    def make_file(rel: str, text: str, mode: int = 0o644) -> None:
        target = root / rel
        if target.exists():
            kept.append(rel)
            return
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        created.append(rel)

    for rel, mode in LAYOUT:
        make_dir(rel, mode)
    make_file(MARKER, json.dumps({"kind": "reachweaver-workspace", "version": 1, "created_at": utc_now()}) + "\n")
    example = _example_accounts()
    if networks is not None:
        make_file("config/accounts.json", placeholder_accounts_json(networks))
    elif example is not None:
        make_file("config/accounts.json", example.read_text(encoding="utf-8"))
    names: list[str] = []
    try:
        from .accounts import load_accounts, account_credential_suffixes
        for account in load_accounts(root / "config" / "accounts.json").values():
            names += [account["env_prefix"] + suffix for suffix in account_credential_suffixes(account)]
    except ValidationError:
        pass
    template = ("# Names only. Copy to secrets.env, fill in the values on the computer you use, keep it chmod 600,\n"
                "# and never copy it with the workspace. Load it in YOUR terminal: set -a; . ./secrets.env; set +a\n"
                + "".join(f"{name}=\n" for name in sorted(set(names))))
    make_file("secrets.env.example", template)
    make_file(".gitignore", "state/\nmedia/\nlogs/\nsecrets.env\n.env*\n")
    make_file("README-WORKSPACE.txt", README_TEXT)
    for rel in ("state", "media", "logs"):
        if os.name == "posix":
            os.chmod(root / rel, 0o700)
    return {"root": str(root), "created": created, "kept": kept}


def _inside(path: str, folder: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _unsent_attachments(con):
    marks = ",".join("?" for _ in RELINK_STATUSES)
    for row in con.execute(f"SELECT id, status, media_json, video_json FROM posts WHERE status IN ({marks})", RELINK_STATUSES):
        for kind, record in _attachments(row):
            yield row, kind, record


def plan_gather(con, root: Path) -> list[dict]:
    """Attachments of unsent posts that live outside ``media/``: where each would be copied to."""
    media = root / "media"
    plan = []
    for row, kind, record in _unsent_attachments(con):
        if _inside(record["path"], media):
            continue
        target = media / f"{record['sha256'][:8]}-{_SAFE.sub('-', Path(record['path']).name)[:80]}"
        state = _file_state(record, verify=True)
        plan.append({"post_id": row["id"], "status": row["status"], "kind": kind, "from": record["path"],
                     "to": str(target), "state": state, "sha256": record["sha256"]})
    return plan


def copy_verified(source: str, target: str, expected_sha: str) -> bool:
    """Copy a file into the workspace only if its bytes match the recorded SHA-256. Existing identical files are reused."""
    destination = Path(target)
    if destination.is_file() and sha256_file(destination) == expected_sha:
        return True
    temp = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    try:
        with open(source, "rb") as handle, open(temp, "xb") as out:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected_sha:
            temp.unlink(missing_ok=True)
            return False
        os.chmod(temp, 0o600)
        os.replace(temp, destination)
        return True
    except OSError:
        temp.unlink(missing_ok=True)
        return False


def plan_repair(con, root: Path) -> list[dict]:
    """Attachments of unsent posts whose recorded path is gone, found again inside ``media/`` by size and SHA-256."""
    media = root / "media"
    by_size: dict[int, list[Path]] = {}
    if media.is_dir():
        for found in sorted(media.rglob("*")):
            if found.is_file() and not found.is_symlink() and not found.name.endswith(".part"):
                by_size.setdefault(found.stat().st_size, []).append(found)
    plan = []
    for row, kind, record in _unsent_attachments(con):
        if _file_state(record, verify=False) == "ok":
            continue
        match = None
        for candidate in by_size.get(record.get("bytes"), []):
            if sha256_file(candidate) == record["sha256"]:
                match = candidate
                break
        plan.append({"post_id": row["id"], "status": row["status"], "kind": kind, "from": record["path"],
                     "to": str(match) if match else None, "state": "ok" if match else "missing"})
    return plan
