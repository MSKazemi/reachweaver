"""Public account metadata, and the single credential accessor (issue #5).

``accounts.json`` holds public identifiers only. Credentials are read through
``credential(name)``: from ``$CREDENTIALS_DIRECTORY/<NAME>`` when systemd's
``LoadCredential=`` provides that directory, otherwise from the environment
variable ``NAME``. No value is ever logged; errors name the variable only.
"""
from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path
from typing import Iterable, Mapping
from . import home
from .policy import (MASTODON_MEDIA_DEFAULTS, MASTODON_VISIBILITIES, PLATFORMS, ValidationError,
                     require_https_public_server)
from .inputs import read_json_file

ENV_PREFIX = re.compile(r"^REACHWEAVER_[A-Z0-9_]+$")
ACCOUNT_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
PERSONA_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")  # optional "persona" metadata, e.g. work or family (persona_guard)
SECRET_FIELD = re.compile(r"(token|password|secret|cookie|session|api.?key|private.?key)", re.I)
DEFAULT_BLUESKY_SERVICE = "https://bsky.social"
LANGUAGE = re.compile(r"^[a-z]{2}$")  # ISO 639-1 shape; the code list itself is not bundled
MAX_SPOILER_CHARS = 200

# Credential variable suffixes per native adapter (credential_hygiene re-exports this table).
# Every suffix here takes part in the env_prefix overlap rule, whether or not an account uses it.
CREDENTIAL_SUFFIXES: dict[str, tuple[str, ...]] = {
    "bluesky": ("_IDENTIFIER", "_APP_PASSWORD"),
    "mastodon": ("_TOKEN",),
    "telegram": ("_BOT_TOKEN",),
    "linkedin": ("_ACCESS_TOKEN", "_CLIENT_ID", "_CLIENT_SECRET"),
    # Instagram adapter (issue #20, owner-approved 2026-10-09; disabled until a live receipt): unreachable
    # until the registry allows it, but its variable name is reserved now so the env_prefix overlap
    # rule and credentials-audit cover it.
    "instagram": ("_ACCESS_TOKEN",),
    # Video adapter (issue #23, owner-approved 2026-10-09; disabled until a live receipt): unreachable
    # until the registry allows it, but its variable name is reserved now so the env_prefix overlap
    # rule and credentials-audit cover it.
    "tiktok": ("_TIKTOK_ACCESS_TOKEN",),
    # Video upload adapter (issue #22, owner-approved 2026-10-09; disabled until a live receipt):
    # unreachable until the registry allows it, but its variable name is reserved now so the
    # env_prefix overlap rule and credentials-audit cover it.
    "youtube": ("_ACCESS_TOKEN",),
    # Threads adapter (issue #19, owner-approved 2026-10-09; disabled until a live receipt): unreachable
    # until the registry allows it, but its variable
    # name is reserved now so the env_prefix overlap rule and credentials-audit cover it.
    "threads": ("_ACCESS_TOKEN",),
    # Facebook Page adapter (issue #21, owner-approved 2026-10-09; disabled until a live receipt):
    # unreachable until the registry allows it, but its variable name is reserved now so the
    # env_prefix overlap rule and credentials-audit cover it.
    "facebook": ("_PAGE_ACCESS_TOKEN",),
}
# Suffixes an account reads only after an explicit per-account opt-in (issue #14):
# the LinkedIn app client id/secret are read only with "scope_check": "introspect".
SCOPE_CHECK_FIELD = "scope_check"
SCOPE_CHECK_INTROSPECT = "introspect"
OPT_IN_CREDENTIAL_SUFFIXES: dict[str, tuple[str, ...]] = {
    "linkedin": ("_CLIENT_ID", "_CLIENT_SECRET"),
}
BACKEND_CREDENTIAL_SUFFIX = "_POSTIZ_API_KEY"  # equals postiz.CREDENTIAL_ENV_SUFFIX (asserted in tests)
# Every suffix any adapter or backend reads. The env_prefix overlap rule uses all of them,
# so changing an account's platform or backend can never make two loaded accounts collide.
ALL_CREDENTIAL_SUFFIXES: tuple[str, ...] = tuple(dict.fromkeys(
    [suffix for names in CREDENTIAL_SUFFIXES.values() for suffix in names] + [BACKEND_CREDENTIAL_SUFFIX]))

# Credentials ReachWeaver reads outside any account (sources.py --online; the #7 D7 approval key,
# storage.APPROVAL_KEY_CREDENTIAL). No account's env_prefix may derive or overlap one, or that
# account would read another tool's secret.
NON_ACCOUNT_CREDENTIALS: tuple[str, ...] = ("REACHWEAVER_GITHUB_TOKEN", "REACHWEAVER_APPROVAL_SECRET")

CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"  # set by systemd for LoadCredential=
MAX_CREDENTIAL_BYTES = 64 * 1024
CREDENTIAL_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,254}$")  # also keeps file names inside the directory
# C0 controls and DEL. No supported credential (app password, bearer/bot token, API key) contains
# one; a CR/LF/NUL would break the HTTP header or URL it is placed in, and http.client then
# raises an error that echoes the value (RFC 9110 section 5.5: field values exclude CR, LF, NUL).
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
# Values read from a credentials directory in this process, so privacy.redact can strip them
# verbatim exactly as it strips secret-named environment variables. Never printed or persisted.
# Keyed by (name, value): a file rewritten between reads keeps both values redactable.
_LOADED_FILE_CREDENTIALS: dict[tuple[str, str], None] = {}


class CredentialError(ValidationError):
    """An unusable credential source. The message names the variable, never a value or path."""


class CredentialsDirectoryError(CredentialError):
    """$CREDENTIALS_DIRECTORY itself is unusable (relative, unreadable, writable by others)."""


def credential_source(env: Mapping[str, str] | None = None) -> str:
    """``credentials_directory`` when $CREDENTIALS_DIRECTORY is set and non-empty, else ``environment``."""
    env = os.environ if env is None else env
    return "credentials_directory" if env.get(CREDENTIALS_DIRECTORY_ENV) else "environment"


def _open_credentials_directory(env: Mapping[str, str]) -> int:
    directory = env[CREDENTIALS_DIRECTORY_ENV]
    if not os.path.isabs(directory):
        raise CredentialsDirectoryError(f"{CREDENTIALS_DIRECTORY_ENV} must be an absolute path")
    if os.open not in os.supports_dir_fd or os.listdir not in os.supports_fd:
        raise CredentialsDirectoryError(f"{CREDENTIALS_DIRECTORY_ENV} is not supported on this platform")
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        raise CredentialsDirectoryError(f"{CREDENTIALS_DIRECTORY_ENV} is not a readable directory") from None
    try:
        info = os.fstat(fd)  # checks the opened directory itself, so no check/use race
        if not stat.S_ISDIR(info.st_mode):
            raise CredentialsDirectoryError(f"{CREDENTIALS_DIRECTORY_ENV} is not a directory")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise CredentialsDirectoryError(f"{CREDENTIALS_DIRECTORY_ENV} is group- or world-writable; "
                                            "refusing to read credentials from it")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_credential_file(dir_fd: int, name: str) -> str | None:
    # O_NONBLOCK: a FIFO planted under the name cannot hang the open; fstat then refuses it.
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_CLOEXEC", 0))
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CredentialError(f"credential {name} is a symlink; refusing to follow it") from None
        raise CredentialError(f"credential {name} is unreadable") from None
    chunks: list[bytes] = []
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CredentialError(f"credential {name} is not a regular file")
        if info.st_size > MAX_CREDENTIAL_BYTES:
            raise CredentialError(f"credential {name} is larger than {MAX_CREDENTIAL_BYTES} bytes")
        total = 0
        while total <= MAX_CREDENTIAL_BYTES:  # bounded even if the file grows after fstat
            chunk = os.read(fd, MAX_CREDENTIAL_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_CREDENTIAL_BYTES:
            raise CredentialError(f"credential {name} is larger than {MAX_CREDENTIAL_BYTES} bytes")
    finally:
        os.close(fd)
    try:
        value = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError(f"credential {name} is not valid UTF-8") from None
    for ending in ("\r\n", "\n"):  # exactly one trailing line ending (Unix or Windows)
        if value.endswith(ending):
            return value[:-len(ending)]
    return value


def _refuse_control_characters(name: str, value: str | None) -> str | None:
    if value is not None and _CONTROL_CHARACTERS.search(value):
        raise CredentialError(f"credential {name} contains control characters")  # name only, never the value
    return value


def credential(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """The one credential accessor. Returns the value, or ``None`` when it is not provided.

    With ``$CREDENTIALS_DIRECTORY`` set (systemd ``LoadCredential=``), only the file
    ``$CREDENTIALS_DIRECTORY/<name>`` is read and the environment is not consulted. The
    directory must not be group- or world-writable; the file must be a regular file,
    opened with ``O_NOFOLLOW`` (no symlinks), at most 64 KiB and UTF-8; exactly one trailing
    ``\\n`` or ``\\r\\n`` is stripped. Otherwise the environment variable ``name`` is returned
    unchanged. From either source, a value that still contains a control character (C0 or DEL,
    e.g. a stray CR) raises ``CredentialError`` instead of reaching an HTTP header or URL.
    ``CredentialError`` messages name only the variable, never a value.
    """
    if not CREDENTIAL_NAME.fullmatch(name):
        raise CredentialError("invalid credential variable name")
    env = os.environ if env is None else env
    if not env.get(CREDENTIALS_DIRECTORY_ENV):
        return _refuse_control_characters(name, env.get(name))
    dir_fd = _open_credentials_directory(env)
    try:
        value = _refuse_control_characters(name, _read_credential_file(dir_fd, name))
    finally:
        os.close(dir_fd)
    if value is not None and len(value) >= 4:
        _LOADED_FILE_CREDENTIALS[(name, value)] = None
    return value


def require_credential(name: str, env: Mapping[str, str] | None = None) -> str:
    """``credential(name)``, raising ``missing_credentials: NAME`` when it is absent."""
    value = credential(name, env)
    if value is None:
        raise ValidationError("missing_credentials: " + name)
    return value


def credential_names_available(env: Mapping[str, str] | None = None) -> list[str]:
    """Sorted names (never values) of credential-shaped entries in the active source."""
    env = os.environ if env is None else env
    if not env.get(CREDENTIALS_DIRECTORY_ENV):
        return sorted(n for n in env if CREDENTIAL_NAME.fullmatch(n))
    dir_fd = _open_credentials_directory(env)
    try:
        return sorted(n for n in os.listdir(dir_fd) if CREDENTIAL_NAME.fullmatch(n))
    finally:
        os.close(dir_fd)


def loaded_file_credentials() -> list[tuple[str, str]]:
    """(name, value) pairs read from a credentials directory in this process; for redaction only."""
    return list(_LOADED_FILE_CREDENTIALS)


def credential_variable_candidates(env_prefix: str) -> tuple[str, ...]:
    """Every credential variable name any adapter or backend could derive from ``env_prefix``."""
    return tuple(env_prefix + suffix for suffix in ALL_CREDENTIAL_SUFFIXES)


def env_prefix_conflicts(accounts: Iterable[Mapping]) -> list[dict]:
    """Pairs of accounts whose credential variable names are equal or one begins the other.

    Identical prefixes always conflict. Otherwise, a conflict is any ``A.prefix + s1``
    that equals or is a string prefix of ``B.prefix + s2`` (or the reverse), over every
    suffix any adapter uses: ``REACHWEAVER_X`` + ``_BOT_TOKEN`` is ``REACHWEAVER_X_BOT``
    + ``_TOKEN``. An account whose variables equal or overlap a ``NON_ACCOUNT_CREDENTIALS``
    name is reported alone (``reserved_credential_variable``). Returns account ids and
    variable names only.
    """
    items = [a for a in accounts if isinstance(a.get("env_prefix"), str)]
    found: list[dict] = []
    for i, first in enumerate(items):
        for second in items[i + 1:]:
            ids = sorted([str(first.get("id")), str(second.get("id"))])
            if first["env_prefix"] == second["env_prefix"]:
                found.append({"accounts": ids, "variables": [], "reason": "identical_env_prefix"})
                continue
            names: set[str] = set()
            for x in credential_variable_candidates(first["env_prefix"]):
                for y in credential_variable_candidates(second["env_prefix"]):
                    if x.startswith(y) or y.startswith(x):
                        names.update((x, y))
            if names:
                found.append({"accounts": ids, "variables": sorted(names), "reason": "overlapping_env_prefix"})
    for account in items:  # same rule against credentials no account owns
        names = set()
        for x in credential_variable_candidates(account["env_prefix"]):
            for reserved in NON_ACCOUNT_CREDENTIALS:
                if x.startswith(reserved) or reserved.startswith(x):
                    names.update((x, reserved))
        if names:
            found.append({"accounts": [str(account.get("id"))], "variables": sorted(names),
                          "reason": "reserved_credential_variable"})
    return found


def bluesky_service(account: dict) -> str:
    """The account's PDS/entryway origin (default bsky.social), re-validated on every use."""
    return require_https_public_server(str(account.get("service") or DEFAULT_BLUESKY_SERVICE))


def validate_mastodon_options(raw: dict, account_id: str = "") -> None:
    """Optional, minimal per-account Mastodon defaults. Direct messages are forbidden."""
    label = f" for account {account_id}" if account_id else ""
    visibility = raw.get("visibility", "public")
    if visibility == "direct":
        raise ValidationError(f"Mastodon visibility 'direct' (DM) is forbidden{label}")
    if visibility not in MASTODON_VISIBILITIES:
        raise ValidationError(f"Mastodon visibility must be one of {', '.join(MASTODON_VISIBILITIES)}{label}")
    limit = raw.get("max_characters")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 100_000):
        raise ValidationError(f"Mastodon max_characters must be an integer 1..100000{label}")
    for name, default in MASTODON_MEDIA_DEFAULTS.items():  # issue #13: may only lower the default
        pinned = raw.get(name)
        if pinned is not None and (type(pinned) is not int or not 1 <= pinned <= default):
            raise ValidationError(f"Mastodon {name} must be an integer 1..{default}{label}")
    language = raw.get("language")
    if language is not None and (not isinstance(language, str) or not LANGUAGE.fullmatch(language)):
        raise ValidationError(f"Mastodon language must be a lowercase ISO 639-1 code such as 'en'{label}")
    spoiler = raw.get("spoiler_text")
    if spoiler is not None and (not isinstance(spoiler, str) or not spoiler.strip()
                                or len(spoiler) > MAX_SPOILER_CHARS
                                or any(ord(ch) < 32 or ord(ch) == 127 for ch in spoiler)):
        raise ValidationError(f"Mastodon spoiler_text must be 1..{MAX_SPOILER_CHARS} printable characters{label}")


def accounts_path(path: str | Path | None = None) -> Path:
    """``--accounts``, else ``$REACHWEAVER_ACCOUNTS``, else ``config/accounts.json`` (relative to the working folder)."""
    return Path(path or os.environ.get("REACHWEAVER_ACCOUNTS", str(home.default_path("config/accounts.json"))))


def load_accounts(path: str | Path | None = None) -> dict[str, dict]:
    path = accounts_path(path)
    if not path.is_file():
        raise ValidationError(f"Missing {path}. Copy config/accounts.example.json and customize it (no secrets).")
    try:
        data = read_json_file(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Accounts config is unreadable or not JSON: {path}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise ValidationError("Accounts file needs an 'accounts' list")
    result: dict[str, dict] = {}
    for raw in data["accounts"]:
        if not isinstance(raw, dict):
            raise ValidationError("Each account must be a JSON object")
        if any(SECRET_FIELD.search(k) for k in raw):
            raise ValidationError("Never put tokens, passwords or sessions in accounts.json. Use environment variables.")
        account_id, platform, env_prefix = raw.get("id"), raw.get("platform"), raw.get("env_prefix")
        if not isinstance(account_id, str) or not ACCOUNT_ID.fullmatch(account_id):
            raise ValidationError("Account ids must start with a lowercase letter, then lowercase letters, digits or '-' (2-63 chars)")
        if account_id in result:
            raise ValidationError(f"Duplicate account id: {account_id}")
        if platform not in PLATFORMS:
            raise ValidationError(f"Unknown platform for account {account_id}")
        if "persona" in raw and not (isinstance(raw["persona"], str) and PERSONA_ID.fullmatch(raw["persona"])):
            raise ValidationError(f"Account {account_id}: persona must be a lowercase id such as work or family "
                                  "(2-31 characters)")
        if not isinstance(env_prefix, str) or not ENV_PREFIX.fullmatch(env_prefix):
            raise ValidationError(f"Invalid credential env_prefix for account {account_id}")
        if "postiz_workspace" in raw and "backend" not in raw:  # #11 shared cooldown group
            raise ValidationError(f"postiz_workspace is only valid on an account with \"backend\": \"postiz\" "
                                  f"({account_id})")
        if "backend" in raw:  # explicit owner opt-in publishing backend (issue #30)
            from .postiz import validate_backend_account
            validate_backend_account(raw)
        elif platform == "mastodon":
            raw["server"] = require_https_public_server(str(raw.get("server", "")))
            validate_mastodon_options(raw, account_id)
        elif platform == "bluesky":
            if not raw.get("handle"):
                raise ValidationError(f"Bluesky handle is required for account {account_id}")
            if "service" in raw:
                raw["service"] = bluesky_service(raw)
        elif platform == "telegram":
            if not str(raw.get("chat_id", "")).strip():
                raise ValidationError(f"Telegram chat_id is required for account {account_id}")
        elif platform == "linkedin":
            from .linkedin import validate_account_config
            validate_account_config(raw, account_id)
        elif platform == "instagram":
            from .instagram import validate_account_config as validate_instagram_config
            validate_instagram_config(raw, account_id)
        elif platform == "tiktok":  # video adapter (issue #23): strict only once open_id is set
            from .tiktok import validate_account_config as validate_tiktok_config
            validate_tiktok_config(raw, account_id)
        elif platform == "youtube":  # video upload adapter (issue #22): strict only once channel_id is set
            from .youtube import validate_account_config as validate_youtube_config
            validate_youtube_config(raw, account_id)
        elif platform == "threads":
            from .threads import validate_account_config as validate_threads_config
            validate_threads_config(raw, account_id)
        elif platform == "facebook":
            from .facebook import validate_account_config as validate_facebook_config
            validate_facebook_config(raw, account_id)
        if "authorization_expires_at" in raw and platform not in ("linkedin", "instagram", "threads"):
            raise ValidationError(f"authorization_expires_at is only supported for LinkedIn, Instagram and Threads "
                                  f"accounts ({account_id})")
        if SCOPE_CHECK_FIELD in raw and (platform != "linkedin" or "backend" in raw):
            raise ValidationError(f"{SCOPE_CHECK_FIELD} is only supported for native LinkedIn accounts ({account_id})")
        result[account_id] = raw
    conflicts = env_prefix_conflicts(result.values())
    if conflicts:  # fail closed: two accounts must never be able to read the same credential
        first = conflicts[0]
        if first["reason"] == "reserved_credential_variable":
            raise ValidationError(f"Account {first['accounts'][0]} has an env_prefix whose credential variables "
                                  f"collide with a credential no account owns ({', '.join(first['variables'])}). "
                                  "Choose an env_prefix that does not derive or overlap that name.")
        detail = ("identical env_prefix" if first["reason"] == "identical_env_prefix"
                  else "credential variables collide: " + ", ".join(first["variables"]))
        raise ValidationError(f"Accounts {first['accounts'][0]} and {first['accounts'][1]} have overlapping "
                              f"credential env_prefix ({detail}). Give each account its own env_prefix "
                              "that is not a prefix of another account's credential variable names.")
    return result


def scope_check_enabled(account: Mapping) -> bool:
    """True only for a native LinkedIn account that opted in with ``"scope_check": "introspect"``."""
    return (account.get("platform") == "linkedin" and account.get("backend") is None
            and account.get(SCOPE_CHECK_FIELD) == SCOPE_CHECK_INTROSPECT)


def account_credential_suffixes(account: Mapping) -> tuple[str, ...]:
    """Suffixes this native account actually reads: opt-in suffixes only when opted in (#14),
    and none for manual-only metadata.

    A YouTube account without ``channel_id`` is manual-export metadata (issue #22), so no
    credential is expected for it; the suffix stays reserved for the env_prefix overlap rule.
    """
    platform = account.get("platform")
    if platform == "youtube" and "channel_id" not in account:
        return ()
    names = CREDENTIAL_SUFFIXES.get(platform, ()) if isinstance(platform, str) else ()
    if scope_check_enabled(account):
        return names
    optional = OPT_IN_CREDENTIAL_SUFFIXES.get(platform, ()) if isinstance(platform, str) else ()
    return tuple(name for name in names if name not in optional)


def missing_credentials(account: dict) -> list[str]:
    if account.get("backend") is not None:  # backend accounts use only the backend key
        from .postiz import missing_credentials as backend_missing
        return backend_missing(account)
    names = account_credential_suffixes(account)
    # Names only. Empty counts as missing, from either source (environment or credentials directory).
    return [account["env_prefix"] + suffix for suffix in names if not credential(account["env_prefix"] + suffix)]
