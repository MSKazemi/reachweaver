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
import unicodedata

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
INFO_FILE = "PACKAGE.json"  # display metadata only (the readable name); nothing that shares or sends reads it
MAX_TITLE_CHARS = 40  # caption words in a display name
MAX_DISPLAY_CHARS = 120
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


_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def _plain_words(value: object) -> str:
    """One line of visible text: control, format (bidi overrides, zero-width), surrogate, private-use and
    unassigned characters become spaces, and runs of whitespace collapse to one space."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(" " if unicodedata.category(ch) in _UNSAFE_CATEGORIES else ch for ch in value[:4096])
    return " ".join(cleaned.split())


def display_title(text: object, note: object = "") -> str:
    """The first words of the caption (else the community note), cut at a word boundary with an ellipsis."""
    words = _plain_words(text) or _plain_words(note)
    if not words:
        return "Untitled post"
    if len(words) <= MAX_TITLE_CHARS:
        return words
    cut = words[:MAX_TITLE_CHARS + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space >= MAX_TITLE_CHARS // 2 else cut[:MAX_TITLE_CHARS]).rstrip(" .,;:-") + "…"


def _short_date(when: datetime) -> str:
    return f"{when.day} {when.strftime('%b %H:%M')}"


COPY_SUFFIX = " (copy)"


def display_name(text: object, note: object, when: datetime, *, copy: bool = False) -> str:
    """A readable package name such as "Our first speckled mugs… · 21 Oct 19:30" ("… (copy) · …" for a duplicate,
    so the two packages never share one name). Display only: the folder keeps its strict
    ``share-YYYYMMDD-HHMMSS-<hex>`` name."""
    return f"{display_title(text, note)}{COPY_SUFFIX if copy else ''} · {_short_date(when)}"


def _valid_display(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_DISPLAY_CHARS and _plain_words(value) == value


def _folder_time(name: str) -> datetime:
    try:
        return datetime.strptime(name.split("-")[1] + name.split("-")[2], "%Y%m%d%H%M%S")
    except (IndexError, ValueError):
        return datetime.now()


def package_display(pkg: Path) -> str:
    """The stored display name (PACKAGE.json, checked again on every read), else one derived from the caption."""
    info = pkg / INFO_FILE
    if info.is_file() and not info.is_symlink():
        try:
            data = read_json_file(info, 16 * 1024)
            if isinstance(data, dict) and _valid_display(data.get("display_name")):
                return str(data["display_name"])
        except (ValidationError, ValueError, UnicodeError, OSError):
            pass
    try:
        editor = editor_from_package(pkg)
        text, note = editor.get("text"), editor.get("note")
    except (ValidationError, ValueError, UnicodeError, OSError):
        text, note = "", ""
    return display_name(text, note, _folder_time(pkg.name))


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
            "id": platform, "label": str(export.get("platform_label") or platform), "ledger": ledger,
            "human_only": platform in human, "state": "posted" if posted else "exported",
            "measurable": _measurable(ledger) and platform not in human,
            "posted": None if posted is None else {
                k: posted.get(k) for k in ("post_id", "url", "posted_at", "label", "status", "account_id")},
        })
    return {"name": pkg.name, "created": _created(pkg.name), "display_name": package_display(pkg),
            "folder": (SHARES / pkg.name).as_posix(), "networks": networks,
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
                  *, update: str | None = None, expected_revision: str | None = None, copy: bool = False) -> Path:
    """Stage the whole package privately, then rename it into place.

    New (``update`` None): a fresh ``share-...`` name; an existing folder is never touched. Update: the named
    package is replaced only if its revision still equals ``expected_revision`` and no network in it is marked
    posted; otherwise :class:`Conflict` and nothing changes. ``copy`` (Duplicate as new) adds " (copy)" to the
    display name. The caller holds the server's write lock.
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
        now = datetime.now()
        shown = display_name(saved.get("text"), saved.get("note"), _folder_time(old.name) if old else now,
                             copy=copy and old is None)
        write_private_file(stage / INFO_FILE, json.dumps({"display_name": shown}, ensure_ascii=False) + "\n")
        if old is None:
            target = base / ("share-" + now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(6))
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

LINK_HELP = "Paste the full link to your post, starting with https://"
_CLI_WORDS = (("--url is required: the link of the post on the platform", "Paste the link of your post."),
              ("--url must be one http(s) link without spaces, at most 2048 characters",
               LINK_HELP + " (one link, no spaces, at most 2048 characters). Nothing was recorded."),
              ("--url must be an http(s) link with a host", LINK_HELP + ". Nothing was recorded."),
              ("--url", "The post link"), ("--at", "The posted time"),
              ("Give at least one of --text/--file, --image, --video or --link",
               "This network has no caption or media in the package to record."))


def _plain(message: str) -> str:
    for old, new in _CLI_WORDS:
        message = message.replace(old, new)
    return message


def _account_names(raw: dict) -> str:
    for key in ("handle", "username", "channel_username"):
        value = _plain_words(raw.get(key))
        if value and len(value) <= 100:
            return value
    return ""


def list_accounts(accounts: str | Path | None = None) -> list[dict]:
    """Public account identifiers for the optional "which account" choice: id, platform and a handle. Read-only;
    a missing or invalid accounts file simply means no choice is offered (the record is made without one)."""
    from .accounts import load_accounts
    try:
        loaded = load_accounts(accounts)
    except (ValidationError, OSError, ValueError, UnicodeError):
        return []
    return [{"id": account_id, "platform": str(raw.get("platform")), "name": _account_names(raw)}
            for account_id, raw in sorted(loaded.items())]


def configured_networks(accounts: str | Path | None = None) -> list[str]:
    """The composer network ids of the workspace's own accounts (example placeholders left out), in accounts.json
    order, for "Your networks". Read-only; a missing or invalid accounts file means none."""
    from .accounts import load_accounts, placeholder_accounts
    try:
        loaded = load_accounts(accounts)
    except (ValidationError, OSError, ValueError, UnicodeError):
        return []
    skip = set(placeholder_accounts(loaded))
    composer_id = {ledger: share_id for share_id, ledger in LEDGER_ID.items()}
    out: list[str] = []
    for account_id, raw in loaded.items():
        platform = composer_id.get(str(raw.get("platform")), str(raw.get("platform")))
        if account_id not in skip and platform not in out:
            out.append(platform)
    return out


def account_from_link(ledger: str, url: str, loaded: dict, placeholders: Iterable[str] = ()) -> str | None:
    """The one configured (non-placeholder) ``ledger`` account whose handle the post link names, else None.

    The same match as ``reachweaver posted`` (``posted.pick_account``): ``posted.handle_in_url`` reads the handle
    from the link's shape, ``posted.account_handles`` lists an account's handles, and a Mastodon account must also
    be on the link's server. No clue in the link, or two accounts with that handle: None (nothing is guessed)."""
    from .posted import account_handles, handle_in_url, host_of
    named = handle_in_url(ledger, url)
    if not named:
        return None
    host, skip = host_of(url), set(placeholders)
    matched = [i for i, a in loaded.items() if a.get("platform") == ledger and i not in skip
               and (ledger != "mastodon" or host_of(str(a.get("server") or "")) == host)
               and named in account_handles(a)]
    return matched[0] if len(matched) == 1 else None


def link_account(ledger: object, url: object, accounts: str | Path | None = None) -> str | None:
    """Read-only: the account a post link names (``account_from_link``), for the page to preselect. Anything that
    is not a known platform id and one plausible link gives None; a missing accounts file gives None."""
    from .accounts import load_accounts, placeholder_accounts
    from .policy import PLATFORMS
    from .storage import manual_url_problem
    if not isinstance(ledger, str) or ledger not in PLATFORMS or not isinstance(url, str) or manual_url_problem(url):
        return None
    try:
        loaded = load_accounts(accounts)
    except (ValidationError, OSError, ValueError, UnicodeError):
        return None
    return account_from_link(ledger, url.strip(), loaded, placeholder_accounts(loaded))


def resolve_account(account_id: object, ledger: str, label: str, url: str,
                    accounts: str | Path | None = None) -> tuple[str | None, str | None]:
    """``(account id or None, "chosen" | "link" | None)`` for Mark as posted.

    A chosen account is checked by :func:`check_account`. With no choice ("Not sure / skip"), the account the link
    names (:func:`account_from_link`, as ``reachweaver posted`` does) is used and reported as ``"link"``. A chosen
    account that the link contradicts is refused before any write: the link names another configured account, or
    a handle the chosen account does not have (when the chosen account lists handles)."""
    from .accounts import load_accounts, placeholder_accounts
    from .posted import account_handles, handle_in_url
    chosen = check_account(account_id, ledger, label, accounts)
    named = handle_in_url(ledger, url)
    if not named:
        return chosen, "chosen" if chosen else None
    try:
        loaded = load_accounts(accounts)
    except (ValidationError, OSError, ValueError, UnicodeError):
        return chosen, "chosen" if chosen else None  # a chosen account was already checked against the file
    from_link = account_from_link(ledger, url, loaded, placeholder_accounts(loaded))
    if chosen is None:
        return (from_link, "link") if from_link else (None, None)
    shown = _plain_words(named)[:100]
    if from_link is not None and from_link != chosen:
        raise ValidationError(f"The link is a post by @{shown}, which is your account {from_link}, not {chosen}. "
                              f"Choose {from_link} (or “Not sure / skip”) and record it again. Nothing was recorded.")
    handles = account_handles(loaded[chosen])
    if handles and named not in handles:
        raise ValidationError(f"The link is a post by @{shown}, not by {chosen}. Check the link, or choose “Not "
                              "sure / skip”. Nothing was recorded.")
    return chosen, "chosen"


# --- numbers typed from the app ("Likes, Replies, Reposts, Views") ----------------------------------------------

NUMBER_FIELDS = ("likes", "replies", "reposts", "views")
MAX_NUMBER = 10 ** 9


def clean_numbers(obj: object, *, required: bool = False) -> dict[str, int]:
    """The numbers the page sent, strictly: a JSON object with only likes/replies/reposts/views, each a whole number
    from 0 to 1,000,000,000 or null (skipped). No strings, decimals, booleans or other keys. ``required``: at least
    one. Checked before anything is written."""
    if obj is None:
        obj = {}
    if not isinstance(obj, dict):
        raise ValidationError("The numbers must be a JSON object. Nothing was recorded.")
    unknown = set(obj) - set(NUMBER_FIELDS)
    if unknown:
        raise ValidationError("Unexpected numbers: " + ", ".join(sorted(map(str, unknown))[:5])
                              + ". Use likes, replies, reposts and views. Nothing was recorded.")
    out: dict[str, int] = {}
    for key in NUMBER_FIELDS:
        value = obj.get(key)
        if value is None:
            continue
        if type(value) is not int or not 0 <= value <= MAX_NUMBER:
            raise ValidationError(f"{key.capitalize()} must be a whole number from 0 to 1,000,000,000 (as the app "
                                  "shows it). Nothing was recorded.")
        out[key] = value
    if required and not out:
        raise ValidationError("Type at least one number (likes, replies, reposts or views). Nothing was recorded.")
    return out


def _store_numbers(con: Any, post_id: str, numbers: dict[str, int]) -> str:
    """``storage.store_metrics`` with source ``entered-by-hand``, observed now; returns the observation time."""
    from .results import HAND_ENTERED_SOURCE
    from .storage import _atomic, parse_utc, store_metrics, utc_now
    observed = parse_utc(utc_now())
    with _atomic(con):
        store_metrics(con, post_id, observed, HAND_ENTERED_SOURCE, **numbers)
    return observed


def record_numbers(name: object, platform: object, numbers: object, db: str | None) -> dict:
    """Add the numbers the owner read in the app to a network this composer marked as posted (its POSTED.json).

    Only that post: its id must name a ``published_manual`` row of the same platform and link in the history
    database (a record made by hand, never a ReachWeaver send). Numbers are checked first; a refusal writes
    nothing. The only write is ``storage.store_metrics`` (a metric snapshot); no post changes state."""
    from .storage import MANUAL_STATUS, connect
    pkg = package_path(name)
    if not isinstance(platform, str) or platform not in _network_dirs(pkg):
        raise ValidationError("That network is not in this package.")
    values = clean_numbers(numbers, required=True)
    record = posted_record(pkg, platform)
    if record is None:
        raise ValidationError("Mark this network as posted first: numbers are kept only for a post you recorded "
                              "here. Nothing was recorded.")
    post_id = record.get("post_id")
    if not isinstance(post_id, str) or not re.fullmatch(r"[0-9a-f]{16}", post_id):
        raise ValidationError("This package's posted record is damaged, so no numbers were kept.")
    ledger = LEDGER_ID.get(platform, platform)
    with connect(db) as con:
        row = con.execute("SELECT status, platform, remote_url FROM posts WHERE id=?", (post_id,)).fetchone()
        if row is None or row["status"] != MANUAL_STATUS or row["platform"] != ledger \
                or row["remote_url"] != record.get("url"):
            raise ValidationError("Numbers are kept here only for a post you recorded by hand from this package; "
                                  "this one does not match your history, so nothing was recorded.")
        observed = _store_numbers(con, post_id, values)
    return {"post_id": post_id, "numbers": values, "observed_at": observed}


def check_account(account_id: object, ledger: str, label: str, accounts: str | Path | None = None) -> str | None:
    """None (no account chosen), or an account id that exists in accounts.json for this network's platform."""
    from .accounts import ACCOUNT_ID, load_accounts
    if account_id is None or account_id == "":
        return None
    if not isinstance(account_id, str) or not ACCOUNT_ID.fullmatch(account_id):
        raise ValidationError("Choose an account from the list, or no account. Nothing was recorded.")
    try:
        loaded = load_accounts(accounts)
    except (ValidationError, OSError, ValueError, UnicodeError):
        raise ValidationError("Your accounts file could not be read, so the account could not be checked. Choose "
                              "no account, or fix accounts.json. Nothing was recorded.") from None
    raw = loaded.get(account_id)
    if raw is None:
        raise ValidationError(f"There is no account called {account_id} in accounts.json. Nothing was recorded.")
    if raw.get("platform") != ledger:
        raise ValidationError(f"The account {account_id} is for {raw.get('platform')}, not {label}. "
                              "Nothing was recorded.")
    return account_id


def mark_posted(name: object, platform: object, url: object, posted_at: object, db: str | None,
                account_id: object = None, accounts: str | Path | None = None, numbers: object = None) -> dict:
    """Record that the owner published one network of a saved package BY HAND, via record_manual_publish.

    Refused before any write: an unknown package or network, a human-only venue, a network the history database
    cannot measure, a network already marked, an ``account_id`` that is not in accounts.json for this network's
    platform or that the link contradicts (:func:`resolve_account`; with no account chosen, the one the link names
    is used), a bad link, or bad ``numbers`` (:func:`clean_numbers`). record_manual_publish's own refusals
    (duplicate link, a future time, no material) come back as ValidationError with the CLI flag names replaced by
    plain words. Numbers, when given, are stored for the new post only (``storage.store_metrics``, entered by hand).
    """
    from .repeat_guard import sha256_file
    from .results import has_control
    from .share import _human_only
    from .storage import connect, manual_url_problem, parse_utc, record_manual_publish, utc_now
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
    values = clean_numbers(numbers)
    link = url if isinstance(url, str) else ""  # record_manual_publish refuses an empty link
    problem = manual_url_problem(link)
    if problem:
        raise ValidationError(_plain(problem))
    if has_control(link):
        raise ValidationError("The link contains control or text-direction characters; copy it again from the app's "
                              "share menu. Nothing was recorded.")
    account, account_from = resolve_account(account_id, ledger, str(export.get("platform_label") or platform),
                                            link.strip(), accounts)
    content = export.get("content") or {}
    text: str = content["text"] if isinstance(content.get("text"), str) else ""
    files = _package_media(pkg)
    images = [sha256_file(p) for p in files if media_kind(p.name) == "image"]
    videos = [sha256_file(p) for p in files if media_kind(p.name) == "video"]
    observed = None
    try:
        when = parse_utc(posted_at) if posted_at else utc_now()
        with connect(db) as con:
            made = record_manual_publish(con, platform=ledger, url=link, account_id=account, content=text,
                                         images=images, video=videos[0] if videos else None,
                                         posted_at=posted_at or None)
            if values:
                observed = _store_numbers(con, made["post_id"], values)
    except ValidationError as exc:
        raise ValidationError(_plain(str(exc))) from None
    record = {"post_id": made["post_id"], "shared_id": made["shared_id"], "status": made["status"],
              "provenance": made["provenance"], "label": POSTED_LABEL, "platform": ledger, "account_id": account,
              "account_from": account_from, "url": link.strip(), "posted_at": when, "recorded_at": utc_now(),
              "numbers": values, "numbers_observed_at": observed}
    try:
        kept = {k: v for k, v in record.items() if k not in ("numbers", "numbers_observed_at")}  # numbers live in the database
        write_private_file(_inside(pkg, platform) / POSTED_FILE, json.dumps(kept, indent=2) + "\n")
    except OSError:
        raise ValidationError(f"Recorded in your history as post {made['post_id']} (posted by hand), but the "
                              "package folder could not be marked; check its permissions.") from None
    return record
