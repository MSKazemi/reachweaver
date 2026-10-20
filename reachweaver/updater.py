"""Install and update the bundled Claude Code skill from a GitHub Release.

Network use is explicit and narrow: ``update --check`` / ``--apply`` and the cached ``--notice``
make HTTPS GETs to github.com hosts only. Nothing here reads credentials, touches ``state/`` or
the approval/publishing path; a release is a skill bundle (code + references), never account data.
The only local file read besides the cache is the accounts config, and from it only each account's
``platform`` id (``configured_platforms``), to order and flag the "What changed" list; nothing
from it is sent anywhere.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__, home
from .policy import PLATFORMS, ValidationError
from .inputs import read_json_file

REPO = "MSKazemi/reachweaver"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
LATEST_DOWNLOAD = f"https://github.com/{REPO}/releases/latest/download"
ZIP_NAME, SHA_NAME = "skill.zip", "skill.zip.sha256"
BUNDLE_DIR = "reachweaver"          # top-level folder inside skill.zip
CHECK_TTL_SECONDS = 24 * 3600
MAX_BYTES = 20 * 1024 * 1024
MAX_UNPACKED = 80 * 1024 * 1024
OPT_OUT_FILE = "no-update-check"  # touch it in the cache dir; no environment read (credential-isolation guard)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_BUNDLE_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
RAW_CHANGELOG = f"https://raw.githubusercontent.com/{REPO}/{{tag}}/CHANGELOG.md"
_CHANGELOG_VERSION_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$")
_CHANGELOG_GROUP_RE = re.compile(r"^### (.+?)\s*$")
_CHANGELOG_BOLD_RE = re.compile(r"^- \*\*(.+?)\*\*")
MAX_CHANGE_VERSIONS = 5
MAX_CHANGE_ITEMS = 6
MAX_CHANGE_TITLE = 100
# Release notes are untrusted text that the session-start notice puts in front of an agent:
# keep a narrow character set, hard caps and no markup, links or backticks.
_TITLE_ALLOWED = re.compile(r"[^A-Za-z0-9 .,:;()/#'+_-]")
# "What changed for you" (issue #45 W2): Security entries first, then entries that name a platform
# the user has configured. Bullets read per version before ordering and capping.
MAX_SCANNED_ITEMS = 200
MAX_ACCOUNTS_BYTES = 1024 * 1024
FOR_YOU_TAG = "(for you: {platforms})"  # fixed text; {platforms} only ever holds policy.PLATFORMS ids
# How each platform id is written in release-note titles. "x" alone is too common a token, so
# only "X" (capitalized) or "Twitter" counts for it. Matching is on whole words.
_PLATFORM_PATTERNS = {
    "x": re.compile(r"(?<![A-Za-z0-9])(?:X|Twitter)(?![A-Za-z0-9])"),
    "devto": re.compile(r"(?<![A-Za-z0-9])(?:devto|dev\.to|DEV Community)(?![A-Za-z0-9])", re.I),
}


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(str(text).strip())
    if not match:
        raise ValidationError(f"Not a plain MAJOR.MINOR.PATCH version: {str(text)[:40]!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def cache_dir() -> Path:
    return Path.home() / ".cache" / "reachweaver"


def skill_root() -> Path | None:
    """The installed skill directory this module runs from, or None for a checkout / pip install."""
    root = Path(__file__).resolve().parents[2]
    if (root / "SKILL.md").is_file() and (root / "scripts" / "run.py").is_file():
        return root
    return None


def install_kind(path: Path | None = None) -> str:
    """"plugin" (managed by /plugin), "skill" (copied bundle) or "checkout_or_pip"."""
    here = (path or Path(__file__)).resolve()
    if "/plugins/cache/" in here.as_posix():
        return "plugin"
    return "skill" if skill_root() else "checkout_or_pip"


def _check_url(url: str) -> None:
    parts = urlparse(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or not (host == "github.com" or host.endswith(".github.com")
                                       or host.endswith(".githubusercontent.com")):
        raise ValidationError("Refusing a non-GitHub or non-HTTPS update URL")


def http_get(url: str, timeout: float = 10.0) -> bytes:
    _check_url(url)
    request = Request(url, headers={"User-Agent": f"reachweaver/{__version__}",
                                    "Accept": "application/vnd.github+json, application/octet-stream"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme and host checked above
        _check_url(response.geturl())
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError("Update download is larger than the 20 MB limit")
    return data


def fetch_latest(get=None) -> dict:
    get = get or http_get
    release = json.loads(get(LATEST_API).decode("utf-8"))
    if release.get("draft") or release.get("prerelease"):
        raise ValidationError("Latest release is a draft or prerelease; not offering it")
    tag = str(release.get("tag_name", ""))
    parse_version(tag)
    assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
    if not assets.get(ZIP_NAME) or not assets.get(SHA_NAME):
        raise ValidationError(f"Release {tag} lacks {ZIP_NAME} and {SHA_NAME}; nothing to install")
    return {"tag": tag, "version": tag.lstrip("v"), "release_url": str(release.get("html_url", "")),
            "zip_url": assets[ZIP_NAME], "sha_url": assets[SHA_NAME]}


def _read_cache(path: Path) -> dict | None:
    try:
        data = read_json_file(path)
        return data if isinstance(data, dict) and isinstance(data.get("checked_at"), (int, float)) else None
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, info: dict, now: float, changes: list[dict] | None = None,
                 platforms: list[str] | tuple[str, ...] = ()) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            payload = {"checked_at": now, "tag": info["tag"], "version": info["version"],
                       "release_url": info["release_url"]}
            if changes is not None:
                payload["changes"] = changes
                payload["changes_for_platforms"] = list(platforms)  # which "for you" flags they carry
            json.dump(payload, handle)
    except OSError:
        pass  # a cache that cannot be written only costs one more request tomorrow


def _clean_title(text: str) -> str:
    cleaned = _TITLE_ALLOWED.sub("", text.replace("`", ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:;-")
    return cleaned[:MAX_CHANGE_TITLE].rstrip()


def configured_platforms(path: str | Path | None) -> list[str]:
    """Platform ids of the local accounts config, for the "for you" flag (issue #45 W2).

    Reads only each account's ``platform`` value and keeps only known ``policy.PLATFORMS`` ids;
    every other field (ids, handles, env prefixes) is ignored, and no environment variable or
    credential is read. A missing, oversized or malformed file gives ``[]``; this never raises.
    """
    try:
        file = Path(path or home.default_path("config/accounts.json"))
        if not file.is_file() or file.stat().st_size > MAX_ACCOUNTS_BYTES:
            return []
        data = read_json_file(file)
    except (OSError, ValueError):
        return []
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return []
    return sorted({item["platform"] for item in accounts
                   if isinstance(item, dict) and isinstance(item.get("platform"), str)
                   and item["platform"] in PLATFORMS})


def _mentions(title: str, platforms: list[str] | tuple[str, ...]) -> list[str]:
    found = []
    for platform in platforms:
        pattern = _PLATFORM_PATTERNS.get(platform) or re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(platform)}(?![A-Za-z0-9])", re.I)
        if pattern.search(title):
            found.append(platform)
    return found


def _prioritized(items: list[str], platforms: list[str] | tuple[str, ...]) -> list[str]:
    """Security first, then entries naming a configured platform (tagged), then the rest; stable."""
    ranked = []
    for index, item in enumerate(items):
        named = _mentions(item, platforms)
        rank = 0 if item.lower().startswith("security:") else 1 if named else 2
        text = f"{FOR_YOU_TAG.format(platforms=', '.join(named))} {item}" if named else item
        ranked.append((rank, index, text))
    return [text for _, _, text in sorted(ranked)][:MAX_CHANGE_ITEMS]


def changelog_between(text: str, installed: str, latest: str, platforms: list[str] | tuple[str, ...] = ()) -> list[dict]:
    """Summarize CHANGELOG.md sections newer than ``installed`` and not newer than ``latest``.

    Returns newest first: ``{"version", "date", "items": ["Added: title", ...]}``. Only the bold
    lead of each bullet (or the first words) is kept, so the result is short, plain text.
    Within a version, ``Security`` entries come first, then entries naming one of ``platforms``
    (prefixed with the fixed ``FOR_YOU_TAG``), then the rest in file order; the cap applies
    after that ordering, so a Security entry is never cut for a later group.
    """
    platforms = tuple(p for p in platforms if p in PLATFORMS)
    low, high = parse_version(installed), parse_version(latest)
    sections: list[dict] = []
    current: dict | None = None
    group = ""
    for line in text.splitlines():
        match = _CHANGELOG_VERSION_RE.match(line)
        if match:
            version = match.group(1)
            keep = low < parse_version(version) <= high
            current = {"version": version, "date": match.group(2) or "", "items": []} if keep else None
            if current is not None:
                sections.append(current)
            group = ""
            continue
        if line.startswith("## "):
            current = None  # [Unreleased] or an unparseable heading: never reported
            continue
        if current is None:
            continue
        heading = _CHANGELOG_GROUP_RE.match(line)
        if heading:
            group = _clean_title(heading.group(1))
            continue
        if line.startswith("- "):
            bold = _CHANGELOG_BOLD_RE.match(line)
            title = _clean_title(bold.group(1) if bold else line[2:])
            if title and len(current["items"]) < MAX_SCANNED_ITEMS:
                current["items"].append(f"{group}: {title}" if group else title)
    for section in sections:
        section["items"] = _prioritized(section["items"], platforms)
    sections.sort(key=lambda sec: parse_version(sec["version"]), reverse=True)
    return sections[:MAX_CHANGE_VERSIONS]


def fetch_changes(tag: str, installed: str, latest: str, get=None,
                  platforms: list[str] | tuple[str, ...] = ()) -> list[dict] | None:
    """Release-note summary between two versions, or None when it cannot be read (never raises)."""
    get = get or http_get
    try:
        parse_version(tag)
        text = get(RAW_CHANGELOG.format(tag=tag)).decode("utf-8", "replace")
        return changelog_between(text, installed, latest, platforms)
    except (OSError, ValueError, KeyError):
        return None


def _sanitize_changes(value: object) -> list[dict] | None:
    """Re-validate a cached change list; a cache file is never trusted."""
    if not isinstance(value, list):
        return None
    clean: list[dict] = []
    for entry in value[:MAX_CHANGE_VERSIONS]:
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
            return None
        try:
            parse_version(entry["version"])
        except ValidationError:
            return None
        raw_date = entry.get("date")
        date = raw_date if isinstance(raw_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date) else ""
        items = [_clean_title(i) for i in entry.get("items", []) if isinstance(i, str)][:MAX_CHANGE_ITEMS]
        clean.append({"version": entry["version"], "date": date,
                      "items": [i for i in items if i]})
    return clean


def check(*, get=None, use_cache: bool = False, now: float | None = None,
          cache_path: Path | None = None, include_changes: bool = False,
          platforms: list[str] | tuple[str, ...] = ()) -> dict:
    """Compare the installed version with the latest release. Network only when needed.

    ``platforms`` (from ``configured_platforms``) orders and flags the change list. A cached
    list made for other platforms is re-read once and the cache rewritten with its original
    ``checked_at``, so a changed accounts file costs at most one more GET.
    """
    get = get or http_get
    now = time.time() if now is None else now
    path = cache_path or cache_dir() / "update-check.json"
    platforms = sorted({p for p in platforms if p in PLATFORMS})
    cached = _read_cache(path) if use_cache else None
    refreshed = False
    if cached and 0 <= now - cached["checked_at"] < CHECK_TTL_SECONDS:
        info = {"tag": cached.get("tag", ""), "version": cached.get("version", ""),
                "release_url": cached.get("release_url", "")}
        parse_version(info["tag"])
        changes = _sanitize_changes(cached.get("changes"))
        if changes is not None and cached.get("changes_for_platforms", []) != platforms:
            changes, refreshed = None, True  # flags were made for another accounts file
        source = "cache"
    else:
        info = fetch_latest(get)
        changes = None
        source = "github"
    newer = parse_version(info["version"]) > parse_version(__version__)
    if include_changes and newer and changes is None:
        changes = fetch_changes(info["tag"], __version__, info["version"], get, platforms)
    if source == "github":
        _write_cache(path, info, now, changes, platforms)
    elif refreshed and changes is not None and cached:
        _write_cache(path, info, cached["checked_at"], changes, platforms)
    return {"installed": __version__, "latest": info["version"], "tag": info["tag"],
            "update_available": newer, "release_url": info["release_url"], "source": source,
            "install_kind": install_kind(), "changes": changes if newer else None,
            "for_you_platforms": platforms, "_assets": info}


def _bundle_version(text: str) -> str:
    match = _BUNDLE_VERSION_RE.search(text)
    if not match:
        raise ValidationError("Bundle has no __version__")
    return match.group(1)


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    total = 0
    for info in archive.infolist():
        name = info.filename
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValidationError("Bundle contains a symlink; refusing it")
        dest = (target / name).resolve()
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or target.resolve() not in (dest, *dest.parents):
            raise ValidationError("Bundle contains an unsafe path; refusing it")
        if name.split("/", 1)[0] != BUNDLE_DIR:
            raise ValidationError(f"Bundle entry outside {BUNDLE_DIR}/; refusing it")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise ValidationError("Bundle unpacks larger than the 80 MB limit")
    archive.extractall(target)


def verify_sha256(data: bytes, sha_text: str) -> str:
    expected = sha_text.strip().split()[0].lower() if sha_text.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValidationError("Checksum file does not hold a SHA-256 digest")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValidationError("SHA-256 mismatch: the download is not the published bundle; nothing installed")
    return actual


def install_zip(data: bytes, sha_text: str, dest: Path, *, replace_version: str | None = None) -> dict:
    """Verify, unpack and atomically swap a skill bundle into ``dest``.

    With ``replace_version`` the bundle must be strictly newer. The previous copy is kept in the
    user cache (never inside a skills directory, where it would load as a duplicate skill).
    """
    verify_sha256(data, sha_text)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".reachweaver-install-", dir=dest.parent))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _safe_extract(archive, staging)
        new = staging / BUNDLE_DIR
        skill_md = new / "SKILL.md"
        if not skill_md.is_file() or not skill_md.read_text(encoding="utf-8").startswith("---\nname: reachweaver\n"):
            raise ValidationError("Bundle is not the reachweaver skill")
        if not (new / "scripts" / "run.py").is_file():
            raise ValidationError("Bundle has no scripts/run.py")
        version = _bundle_version((new / "scripts" / "reachweaver" / "__init__.py").read_text(encoding="utf-8"))
        if replace_version and parse_version(version) <= parse_version(replace_version):
            raise ValidationError(f"Bundle {version} is not newer than {replace_version}")
        backup = None
        if dest.exists():
            backup = cache_dir() / "previous" / f"{dest.name}-{int(time.time())}"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(backup))
        try:
            os.replace(new, dest)
        except OSError:
            if backup:
                shutil.move(str(backup), str(dest))
            raise
        return {"installed_version": version, "path": str(dest), "previous_copy": str(backup) if backup else None}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def apply_update(result: dict, *, get=None, dest: Path | None = None) -> dict:
    get = get or http_get
    if dest is None and install_kind() == "plugin":
        raise ValidationError("Installed as a Claude Code plugin: update it with `/plugin marketplace update "
                              "reachweaver` then `/plugin update reachweaver@reachweaver`, then /reload-plugins.")
    target = dest or skill_root()
    if target is None:
        raise ValidationError("This is a source checkout or pip install, not an installed skill bundle. "
                              "Update with `git pull` (or reinstall with scripts/install_skill.py).")
    if target.is_symlink() or any((p / ".git").exists() for p in (target, *target.parents)):
        raise ValidationError("This skill folder is a symlink or lives inside a git checkout (a symlinked "
                              "install resolves into the clone). Refusing to move it; use `git pull` and "
                              "`python3 scripts/sync_skill.py` instead.")
    assets = result["_assets"]
    data = get(assets["zip_url"])
    sha_text = get(assets["sha_url"]).decode("utf-8", "replace")
    return install_zip(data, sha_text, target, replace_version=result["installed"])


def changes_lines(changes: list[dict] | None) -> list[str]:
    """Plain-text, data-only summary lines for a notice; empty when nothing is known."""
    lines = []
    for entry in changes or []:
        items = entry.get("items") or []
        shown = "; ".join(items[:3]) + (f" (+{len(items) - 3} more)" if len(items) > 3 else "")
        lines.append(f"  v{entry['version']}" + (f" ({entry['date']})" if entry.get("date") else "")
                     + (f": {shown}" if shown else ""))
    return lines


def for_you_note(platforms: list[str]) -> str:
    """One fixed line naming the configured platform ids the flags were matched against."""
    ids = ", ".join(p for p in platforms if p in PLATFORMS)
    return (f"  Security entries are listed first; '(for you: ...)' marks entries that name a platform in "
            f"your accounts config ({ids}).")


def notice_line(result: dict) -> str:
    how = ("`/plugin marketplace update reachweaver`, then `/plugin update reachweaver@reachweaver`"
           if result.get("install_kind") == "plugin" else "python3 <skill>/scripts/run.py update --apply --yes")
    return (f"ReachWeaver {result['latest']} is available (installed {result['installed']}). "
            f"Tell the user; update only if they agree: {how}")
