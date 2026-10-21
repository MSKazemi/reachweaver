"""Saved packages, the autosaved draft, attached media and "Mark as posted" for the browser Share Composer.

Local file work only, below the workspace: packages in ``state/shares/share-*``, the editor draft and media the
owner picked in ``state/composer``. The one database write is :func:`storage.record_manual_publish`, which makes a
terminal ``published_manual`` record that no approval, schedule, claim or send path selects. No network call, no
send queue, no approval, no schedule.

Every name that arrives from the browser is checked against a strict pattern, then resolved and confirmed to sit
directly inside its private folder; symlinks are refused and never followed (``O_NOFOLLOW`` on every open), and
files are created with ``O_EXCL`` so nothing existing is overwritten except through the explicit, confirmed
"update this package" swap.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
from datetime import datetime
from typing import Any, Callable, Iterable

from . import home
from .policy import ValidationError
from .privacy import write_private_file

SEND_PATH = None
SHARES = Path("state") / "shares"
COMPOSER = Path("state") / "composer"
PACKAGE_NAME = re.compile(r"share-\d{8}-\d{6}-[0-9a-f]{12}")
MEDIA_ID = re.compile(r"[0-9a-f]{16}")
MEDIA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
MAX_MEDIA_BYTES = 256 * 1024 * 1024
MAX_MEDIA_FILES = 10  # per post, the same cap as share --attach
MAX_STORE_FILES = 30  # files kept in the draft media store at once
MAX_JSON_FILE_BYTES = 2 * 1024 * 1024
MAX_PACKAGES_LISTED = 200
DRAFT_FILE = "draft.json"
EDITOR_FILE = "composer.json"
POSTED_FILE = "POSTED.json"
MEDIA_FOLDER = "media"
POSTED_LABEL = "posted by hand"
# Share destination id -> the platform id the history database uses (the same alias share_tailor.POLICY_ID uses).
LEDGER_ID = {"dev": "devto"}
_EDITOR_FIELDS = frozenset({"platforms", "text", "title", "note", "overrides", "media"})
DRAFT_FIELDS = _EDITOR_FIELDS | {"package", "revision"}
_CHUNK = 1 << 20


class Conflict(ValidationError):
    """The package changed (or was marked posted) since the editor loaded it; nothing was written."""


def _nofollow(flags: int) -> int:
    return flags | getattr(os, "O_NOFOLLOW", 0)


def _private_dir(relative: Path) -> Path:
    """``relative`` inside the workspace (else the working folder), created 0700 one component at a time. No
    component below the workspace may be a symlink, so everything stays inside it; the folder must be private."""
    anchor = home.default_path(Path(".")).absolute()
    target = anchor / relative
    current = anchor
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValidationError(f"The composer folder {relative} may not be or pass through a symlink.")
        if not current.exists():
            os.mkdir(current, 0o700)
    if not target.is_dir():
        raise ValidationError(f"{relative} is not a folder.")
    if os.name == "posix" and stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise ValidationError(f"The folder {relative} must be private (chmod 700).")
    return target


def shares_dir() -> Path:
    return _private_dir(SHARES)


def composer_dir() -> Path:
    return _private_dir(COMPOSER)


def media_dir() -> Path:
    return _private_dir(COMPOSER / MEDIA_FOLDER)


def _inside(base: Path, name: str) -> Path:
    """``base/name`` after confirming the resolved path is a direct child of the resolved base and not a symlink."""
    path = base / name
    if path.is_symlink():
        raise ValidationError("Symlinks are not followed in the composer folders.")
    if path.resolve().parent != base.resolve():
        raise ValidationError("That name points outside the composer folder.")
    return path


def package_path(name: object) -> Path:
    """A saved package folder: a strict ``share-YYYYMMDD-HHMMSS-<12 hex>`` name directly inside state/shares."""
    if not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name):
        raise ValidationError("Unknown package name.")
    path = _inside(shares_dir(), name)
    if not path.is_dir():
        raise ValidationError(f"No saved package called {name}.")
    return path


def read_json_file(path: Path, limit: int = MAX_JSON_FILE_BYTES) -> Any:
    """Parse a small private JSON file without following a symlink."""
    fd = os.open(path, _nofollow(os.O_RDONLY))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValidationError(f"{path.name} is not a small regular file.")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(limit + 1)
    finally:
        if fd != -1:
            os.close(fd)
    return json.loads(data.decode("utf-8"))


def copy_file(source: Path, destination: Path, limit: int = MAX_MEDIA_BYTES) -> int:
    """Copy one regular file: the source is opened without following symlinks, the destination must be new."""
    src = os.open(source, _nofollow(os.O_RDONLY))
    try:
        info = os.fstat(src)
        if not stat.S_ISREG(info.st_mode):
            raise ValidationError(f"{source.name} is not a regular file.")
        if info.st_size > limit:
            raise ValidationError(f"{source.name} is larger than {limit // (1024 * 1024)} MiB.")
        dst = os.open(destination, _nofollow(os.O_CREAT | os.O_EXCL | os.O_WRONLY), 0o600)
        copied = 0
        try:
            while True:
                chunk = os.read(src, _CHUNK)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > limit:
                    raise ValidationError(f"{source.name} grew past the size limit while copying.")
                view = memoryview(chunk)
                while view:
                    view = view[os.write(dst, view):]
        except BaseException:
            os.close(dst)
            os.unlink(destination)
            raise
        os.close(dst)
        return copied
    finally:
        os.close(src)


# --- editor state (draft and composer.json) -------------------------------------------------------------------

def _string(value: Any, limit: int, field: str, *, allow_none: bool = True) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValidationError(f"{field} must be text of at most {limit} bytes.")
    return value


def clean_editor(obj: Any, *, fields: frozenset[str] = DRAFT_FIELDS) -> dict:
    """Keep only the editor fields, with their types and sizes checked. Nothing else (no token) is ever stored."""
    if not isinstance(obj, dict):
        raise ValidationError("The editor state must be a JSON object.")
    unknown = set(obj) - fields
    if unknown:
        raise ValidationError("Unexpected editor fields: " + ", ".join(sorted(unknown)[:5]))
    platforms = obj.get("platforms") or []
    if not isinstance(platforms, list) or len(platforms) > 10 or not all(
            isinstance(p, str) and 0 < len(p) < 100 for p in platforms):
        raise ValidationError("platforms must be a list of at most ten network ids.")
    overrides = obj.get("overrides") or {}
    if not isinstance(overrides, dict) or len(overrides) > 10 or not all(
            isinstance(k, str) and len(k) < 100 and isinstance(v, str) and len(v.encode("utf-8")) <= 65536
            for k, v in overrides.items()):
        raise ValidationError("overrides must map at most ten network ids to text.")
    media = obj.get("media") or []
    if not isinstance(media, list) or len(media) > MAX_MEDIA_FILES or not all(
            isinstance(m, str) and len(m) <= 120 for m in media):
        raise ValidationError(f"media must list at most {MAX_MEDIA_FILES} attached files.")
    result: dict[str, Any] = {
        "platforms": list(platforms), "text": _string(obj.get("text"), 65536, "text"),
        "title": _string(obj.get("title"), 1024, "title"), "note": _string(obj.get("note"), 1024, "note") or "",
        "overrides": dict(overrides), "media": list(media),
    }
    if "package" in fields:
        package = obj.get("package")
        if package is not None and (not isinstance(package, str) or not PACKAGE_NAME.fullmatch(package)):
            raise ValidationError("package must be a saved package name or null.")
        revision = obj.get("revision")
        if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{16}", revision)):
            raise ValidationError("revision must be a package revision or null.")
        result["package"], result["revision"] = package, revision
    return result


def save_draft(obj: Any) -> dict:
    """Replace the autosaved draft (0600, atomic rename). Media ids that no longer exist are dropped."""
    draft = clean_editor(obj)
    draft["media"] = [m for m in draft["media"] if _media_file(m) is not None]
    folder = composer_dir()
    target = _inside(folder, DRAFT_FILE)
    fd, temp = tempfile.mkstemp(prefix=".draft-", dir=folder)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(draft, handle, ensure_ascii=False)
        os.replace(temp, target)  # replaces a name, never writes through a symlink
    except BaseException:
        if os.path.lexists(temp):
            os.unlink(temp)
        raise
    return draft


def load_draft() -> dict | None:
    folder = composer_dir()
    path = _inside(folder, DRAFT_FILE)
    if not path.exists():
        return None
    try:
        draft = clean_editor(read_json_file(path))
    except (ValidationError, ValueError, UnicodeError):
        return None  # a damaged draft is not worth a crash: the editor starts empty
    draft["media"] = [m for m in draft["media"] if _media_file(m) is not None]
    return draft


def clear_draft() -> None:
    folder = composer_dir()
    path = _inside(folder, DRAFT_FILE)
    if path.exists():
        path.unlink()
    _sweep_media(keep=())


# --- media store -----------------------------------------------------------------------------------------------

def media_kind(name: str) -> str | None:
    from .share import IMAGE_SUFFIXES, VIDEO_SUFFIXES
    suffix = Path(name).suffix.casefold()
    return "image" if suffix in IMAGE_SUFFIXES else "video" if suffix in VIDEO_SUFFIXES else None


def safe_media_name(filename: object) -> str:
    """The base name the browser sent, reduced to ``[A-Za-z0-9._-]`` and checked against the share allowlist."""
    from .share import IMAGE_SUFFIXES, VIDEO_SUFFIXES
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        raise ValidationError("The file needs a name.")
    base = re.split(r"[\\/]", filename)[-1]
    stem, suffix = os.path.splitext(base)
    suffix = suffix.casefold()
    if suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
        raise ValidationError("Choose a photo or video: " + ", ".join(sorted(IMAGE_SUFFIXES | VIDEO_SUFFIXES)) + ".")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")[:60] or "media"
    return stem + suffix


def _media_file(media_id: object) -> Path | None:
    """The one file in ``media/<id>/``, or None. The id is 16 hex digits; no symlink anywhere on the way."""
    if not isinstance(media_id, str) or not MEDIA_ID.fullmatch(media_id):
        return None
    folder = media_dir()
    box = folder / media_id
    if box.is_symlink() or not box.is_dir() or box.resolve().parent != folder.resolve():
        return None
    entries = list(os.scandir(box))
    if len(entries) != 1 or entries[0].is_symlink() or not entries[0].is_file(follow_symlinks=False) \
            or not MEDIA_NAME.fullmatch(entries[0].name):
        return None
    return box / entries[0].name


def media_path(media_id: object) -> Path:
    path = _media_file(media_id)
    if path is None:
        raise ValidationError("An attached file is missing, a symlink or not a regular file; attach it again.")
    return path


def media_info(media_id: str) -> dict:
    path = media_path(media_id)
    return {"id": media_id, "name": path.name, "size": path.lstat().st_size, "kind": media_kind(path.name)}


def _sweep_media(keep: Iterable[str]) -> None:
    """Delete draft media the editor no longer references. A symlink is unlinked itself, never followed."""
    wanted = set(keep)
    folder = media_dir()
    for entry in os.scandir(folder):
        if entry.name in wanted:
            continue
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(folder / entry.name)
        else:
            os.unlink(folder / entry.name)


def _new_box() -> tuple[str, Path]:
    folder = media_dir()
    if sum(1 for e in os.scandir(folder) if not e.name.startswith(".")) >= MAX_STORE_FILES:
        draft = load_draft()
        _sweep_media(keep=(draft or {}).get("media", []))
        if sum(1 for e in os.scandir(folder) if not e.name.startswith(".")) >= MAX_STORE_FILES:
            raise ValidationError("Too many attached files are waiting; remove some or clear the form first.")
    media_id = secrets.token_hex(8)
    box = folder / media_id
    os.mkdir(box, 0o700)
    return media_id, box


def store_media(filename: object, size: int, read_into: Callable[[Callable[[bytes], None]], bool]) -> dict:
    """Stream one uploaded photo/video into ``media/<id>/<safe name>``. ``read_into(write)`` feeds the body and
    returns False when it was short or too slow; then nothing is kept."""
    name = safe_media_name(filename)
    if not 0 < size <= MAX_MEDIA_BYTES:
        raise ValidationError(f"Each file must be 1 byte to {MAX_MEDIA_BYTES // (1024 * 1024)} MiB.")
    media_id, box = _new_box()
    try:
        fd = os.open(box / name, _nofollow(os.O_CREAT | os.O_EXCL | os.O_WRONLY), 0o600)
        written = [0]

        def write(chunk: bytes) -> None:
            written[0] += len(chunk)
            if written[0] > size:
                raise ValidationError("The file is larger than it said.")
            view = memoryview(chunk)
            while view:
                view = view[os.write(fd, view):]

        try:
            complete = read_into(write)
        finally:
            os.close(fd)
        if not complete or written[0] != size:
            raise ValidationError("The file upload was incomplete or too slow; nothing was kept.")
    except BaseException:
        shutil.rmtree(box)
        raise
    return media_info(media_id)


# --- packages --------------------------------------------------------------------------------------------------

def _network_dirs(pkg: Path) -> list[str]:
    from .share import available
    allowed = set(available())
    found = [e.name for e in os.scandir(pkg) if e.name in allowed and e.is_dir(follow_symlinks=False)]
    order: list[str] = []
    editor_file = pkg / EDITOR_FILE
    if editor_file.is_file() and not editor_file.is_symlink():
        try:
            order = [p for p in clean_editor(read_json_file(editor_file), fields=_EDITOR_FIELDS)["platforms"]
                     if p in found]
        except (ValidationError, ValueError, UnicodeError):
            order = []
    return order + sorted(set(found) - set(order))


def _package_media(pkg: Path) -> list[Path]:
    folder = pkg / MEDIA_FOLDER
    if not folder.exists() and not folder.is_symlink():
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise ValidationError("The package media folder may not be a symlink.")
    files = []
    for entry in sorted(os.scandir(folder), key=lambda e: e.name):
        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            raise ValidationError(f"Package media {entry.name} is a symlink or not a regular file; nothing was read.")
        files.append(folder / entry.name)
    return files


def _export(pkg: Path, platform: str) -> dict:
    data = read_json_file(_inside(pkg, platform) / "manual-export.json")
    if not isinstance(data, dict):
        raise ValidationError(f"{platform}/manual-export.json is not a package.")
    return data


def posted_record(pkg: Path, platform: str) -> dict | None:
    folder = _inside(pkg, platform)
    path = folder / POSTED_FILE
    if path.is_symlink():
        raise ValidationError("A posted record may not be a symlink.")
    if not path.exists():
        return None
    data = read_json_file(path, 64 * 1024)
    return data if isinstance(data, dict) else None


def revision(pkg: Path) -> str:
    """A fingerprint of the package's content: every network's package JSON, the editor file and the media."""
    digest = hashlib.sha256()
    for platform in _network_dirs(pkg):
        digest.update(platform.encode() + b"\0")
        digest.update(json.dumps(_export(pkg, platform), sort_keys=True).encode("utf-8"))
    editor_file = pkg / EDITOR_FILE
    if editor_file.is_file() and not editor_file.is_symlink():
        digest.update(json.dumps(read_json_file(editor_file), sort_keys=True).encode("utf-8"))
    for path in _package_media(pkg):
        digest.update(f"{path.name}:{path.lstat().st_size}".encode())
    return digest.hexdigest()[:16]


def _created(name: str) -> str:
    stamp = name.split("-")[1] + name.split("-")[2]
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def package_state(pkg: Path) -> dict:
    """What the browser shows for one package: per network exported or posted (by hand), plus the revision."""
    from .share import _human_only
    human = _human_only()
    networks = []
    for platform in _network_dirs(pkg):
        export = _export(pkg, platform)
        posted = posted_record(pkg, platform)
        ledger = LEDGER_ID.get(platform, platform)
        networks.append({
            "id": platform, "label": str(export.get("platform_label") or platform),
            "human_only": platform in human, "state": "posted" if posted else "exported",
            "measurable": _measurable(ledger) and platform not in human,
            "posted": None if posted is None else {
                k: posted.get(k) for k in ("post_id", "url", "posted_at", "label", "status")},
        })
    return {"name": pkg.name, "created": _created(pkg.name), "networks": networks,
            "media": [p.name for p in _package_media(pkg)], "revision": revision(pkg),
            "any_posted": any(n["state"] == "posted" for n in networks)}


def _measurable(ledger: str) -> bool:
    from .policy import PLATFORMS
    return ledger in PLATFORMS


def list_packages() -> list[dict]:
    """Saved packages, newest first. A folder that is damaged or not a package is skipped, never repaired."""
    base = shares_dir()
    names = sorted((e.name for e in os.scandir(base)
                    if PACKAGE_NAME.fullmatch(e.name) and e.is_dir(follow_symlinks=False)), reverse=True)
    result = []
    for name in names[:MAX_PACKAGES_LISTED]:
        try:
            result.append(package_state(base / name))
        except (ValidationError, OSError, ValueError, UnicodeError):
            continue
    return result


def editor_from_package(pkg: Path) -> dict:
    """The editor fields saved with the package (composer.json), or rebuilt from each network's package JSON."""
    editor_file = pkg / EDITOR_FILE
    if editor_file.is_file() and not editor_file.is_symlink():
        editor = clean_editor(read_json_file(editor_file), fields=_EDITOR_FIELDS)
        editor["platforms"] = [p for p in editor["platforms"] if p in _network_dirs(pkg)]
        return editor
    text: str | None = None
    title: str | None = None
    note = ""
    overrides: dict[str, str] = {}
    platforms = _network_dirs(pkg)
    for platform in platforms:
        content = _export(pkg, platform).get("content") or {}
        if isinstance(content.get("talking_points"), list) and content["talking_points"] and not note:
            note = str(content["talking_points"][0])
            continue
        body = content.get("text")
        if isinstance(body, str):
            if text is None:
                text = body
            elif body != text:
                overrides[platform] = body
        if platform == "youtube" and isinstance(content.get("title"), str):
            title = content["title"]
    return clean_editor({"platforms": platforms, "text": text, "title": title, "note": note,
                         "overrides": overrides, "media": [p.name for p in _package_media(pkg)]},
                        fields=_EDITOR_FIELDS)


def media_into_store(pkg: Path) -> list[dict]:
    """Copy a package's media into the draft media store (no symlink followed) so the editor can reuse them."""
    infos = []
    for source in _package_media(pkg):
        name = safe_media_name(source.name)
        media_id, box = _new_box()
        try:
            copy_file(source, box / name)
        except BaseException:
            shutil.rmtree(box)
            raise
        infos.append(media_info(media_id))
    return infos


def write_package(packages: list[tuple[str, dict]], media: list[tuple[Path, str]], editor: dict,
                  *, update: str | None = None, expected_revision: str | None = None) -> Path:
    """Stage the whole package privately, then rename it into place.

    New (``update`` None): a fresh ``share-...`` name; an existing folder is never touched. Update: the named
    package is replaced only if its revision still equals ``expected_revision`` and no network in it is marked
    posted; otherwise :class:`Conflict` and nothing changes. The caller holds the server's write lock.
    """
    from .share import _readme, write_one
    base = shares_dir()
    old = package_path(update) if update is not None else None
    if old is not None:
        state = package_state(old)
        if state["any_posted"]:
            raise Conflict("A network in this package is marked as posted, so it cannot be changed. "
                           "Save as a new package instead.")
        if expected_revision != state["revision"]:
            raise Conflict("This package changed since you opened it. Open it again, or save as a new package.")
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=base))
    try:
        names: list[str] = []
        renamed: dict[str, str] = {}  # source path -> path inside the package, for the media check records
        if media:
            os.mkdir(stage / MEDIA_FOLDER, 0o700)
            for source, name in media:
                final, index = name, 1
                while final in names:
                    stem, suffix = os.path.splitext(name)
                    final, index = f"{stem}-{index}{suffix}", index + 1
                copy_file(source, stage / MEDIA_FOLDER / final)
                names.append(final)
                renamed[str(source)] = MEDIA_FOLDER + "/" + final
        for platform, pkg in packages:
            for row in (pkg.get("share") or {}).get("media_checks") or []:
                row["path"] = renamed.get(str(row["path"]), Path(str(row["path"])).name)
            write_one(platform, pkg, stage / platform)
        readme = _readme(packages, [])
        if names:
            readme += ("\n## Media files in this package\n\nCopied into the media folder (not uploaded). Their headers "
                       "were checked offline (see the Check lines above); rights, consent, alt text and captions are "
                       "yours to check.\n\n" + "".join(f"- media/{n}\n" for n in names))
        write_private_file(stage / "OPEN-FIRST.md", readme)
        saved = dict(clean_editor(editor, fields=_EDITOR_FIELDS), media=names)
        write_private_file(stage / EDITOR_FILE, json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
        if old is None:
            target = base / ("share-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(6))
            if os.path.lexists(target):
                raise ValidationError("The generated output destination already exists.")
            stage.rename(target)
            return target.absolute()
        retired = base / (".replaced-" + secrets.token_hex(6))
        old.rename(retired)
        try:
            stage.rename(old)
        except BaseException:
            retired.rename(old)
            raise
        shutil.rmtree(retired)
        return old.absolute()
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


# --- mark as posted --------------------------------------------------------------------------------------------

_CLI_WORDS = (("--url is required: the link of the post on the platform", "Paste the link of your post."),
              ("--url", "The post link"), ("--at", "The posted time"),
              ("Give at least one of --text/--file, --image, --video or --link",
               "This network has no caption or media in the package to record."))


def _plain(message: str) -> str:
    for old, new in _CLI_WORDS:
        message = message.replace(old, new)
    return message


def mark_posted(name: object, platform: object, url: object, posted_at: object, db: str | None) -> dict:
    """Record that the owner published one network of a saved package BY HAND, via record_manual_publish.

    Refused before any write: an unknown package or network, a human-only venue, a network the history database
    cannot measure, or a network already marked. record_manual_publish's own refusals (bad link, duplicate link,
    a future time, no material) come back as ValidationError with the CLI flag names replaced by plain words.
    """
    from .repeat_guard import sha256_file
    from .share import _human_only
    from .storage import connect, parse_utc, record_manual_publish, utc_now
    pkg = package_path(name)
    if not isinstance(platform, str) or platform not in _network_dirs(pkg):
        raise ValidationError("That network is not in this package.")
    if platform in _human_only():
        raise ValidationError("This is a human-only venue: record your own post with 'reachweaver mark-shared' in "
                              "the terminal if you want it in your history.")
    ledger = LEDGER_ID.get(platform, platform)
    if not _measurable(ledger):
        raise ValidationError("ReachWeaver cannot measure posts on this network yet, so nothing was recorded.")
    if posted_record(pkg, platform) is not None:
        raise Conflict("This network is already marked as posted in this package.")
    if posted_at is not None and (not isinstance(posted_at, str) or len(posted_at) > 64):
        raise ValidationError("The posted time must be an ISO 8601 date with a timezone.")
    export = _export(pkg, platform)
    content = export.get("content") or {}
    text: str = content["text"] if isinstance(content.get("text"), str) else ""
    link = url if isinstance(url, str) else ""  # record_manual_publish refuses an empty link
    files = _package_media(pkg)
    images = [sha256_file(p) for p in files if media_kind(p.name) == "image"]
    videos = [sha256_file(p) for p in files if media_kind(p.name) == "video"]
    try:
        when = parse_utc(posted_at) if posted_at else utc_now()
        with connect(db) as con:
            made = record_manual_publish(con, platform=ledger, url=link, content=text, images=images,
                                         video=videos[0] if videos else None, posted_at=posted_at or None)
    except ValidationError as exc:
        raise ValidationError(_plain(str(exc))) from None
    record = {"post_id": made["post_id"], "shared_id": made["shared_id"], "status": made["status"],
              "provenance": made["provenance"], "label": POSTED_LABEL, "platform": ledger,
              "url": link.strip(), "posted_at": when, "recorded_at": utc_now()}
    try:
        write_private_file(_inside(pkg, platform) / POSTED_FILE, json.dumps(record, indent=2) + "\n")
    except OSError:
        raise ValidationError(f"Recorded in your history as post {made['post_id']} (posted by hand), but the "
                              "package folder could not be marked; check its permissions.") from None
    return record
