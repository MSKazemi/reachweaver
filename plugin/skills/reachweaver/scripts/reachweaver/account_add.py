"""``account-add PLATFORM``: append one correctly shaped, public account entry to ``accounts.json``.

It asks for, accepts and writes public identifiers only (handle, server, chat id, member id...). It never
asks for a password, app password, token or key: it prints the environment variable *names* to set
yourself and points to ``connect --guide``. The new file must load with the normal loader
(``accounts.load_accounts``, every rule unchanged: secret-named fields, id and env_prefix shape,
per-platform validators, env_prefix overlap). Duplicates (same id, or the same identity on the same
network) are refused, and so are the example placeholder values. When the file still holds the unedited
example entry for that network (``workspace --init`` copies one per network), that entry is filled in place
with its id and env_prefix kept, unless ``--id`` is given. The previous file is kept as
``accounts.json.bak`` and the new one replaces it atomically. ``--dry-run`` prints the entry and writes nothing.
The entry is shown as plain lines (``describe``), and a terminal confirmation defaults to yes because the write
is local and reversible. Then, at a terminal and for a network with an enabled adapter, it asks ``CONNECT_NOW``
(default No: posting by hand needs no connection); y continues in this process into the unchanged
``connect --guide --account ID`` flow (the CLI passes it as ``connect_now``). Elsewhere it prints that command.

``account-remove ID`` (``run_remove``) drops one entry the same way (``.bak`` kept, terminal confirmation or
``--yes``). It never touches credentials, the database, approvals or disconnect state; posts recorded for the
account are counted read-only and left in place.

Wrong-account guard: ``--persona`` must be a persona in your profile (when you have one), and an entry whose
username the profile records under another persona, or that contradicts the profile, is refused.

No network, no database, no send path (``SEND_PATH = None``).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from . import home, persona_guard
from . import profile as profile_store
from .accounts import (PLACEHOLDER_VALUES, SECRET_FIELD, account_credential_suffixes, accounts_path,
                       env_prefix_conflicts, load_accounts, placeholder_fields)
from .inputs import read_json_file
from .policy import SUPPORTED_LIVE, ValidationError

SEND_PATH = None  # Asserted in tests: this module can never send, approve or schedule.

# Per network: fields you must give, other public fields you may give (--field KEY=VALUE), the usual
# credential prefix (as in config/accounts.example.json) and one plain question per field.
SCHEMAS: dict[str, dict] = {
    "bluesky": {"required": ("handle",), "optional": ("service",), "prefix": "REACHWEAVER_BSKY"},
    "mastodon": {"required": ("server", "handle"),
                 "optional": ("visibility", "language", "spoiler_text", "max_characters", "max_media_attachments",
                              "image_size_limit", "media_description_limit"),
                 "prefix": "REACHWEAVER_MASTODON"},
    "telegram": {"required": ("chat_id", "bot_username"), "optional": ("channel_username",),
                 "prefix": "REACHWEAVER_TELEGRAM"},
    "linkedin": {"required": ("person_id", "linkedin_version"), "optional": ("authorization_expires_at", "scope_check"),
                 "prefix": "REACHWEAVER_LINKEDIN"},
    "instagram": {"required": ("handle",), "optional": ("ig_user_id", "authorization_expires_at"),
                  "prefix": "REACHWEAVER_INSTAGRAM"},
    "threads": {"required": ("handle",), "optional": ("threads_user_id", "authorization_expires_at"),
                "prefix": "REACHWEAVER_THREADS"},
    "tiktok": {"required": ("handle",), "optional": ("open_id", "display_name"), "prefix": "REACHWEAVER_TIKTOK"},
    "youtube": {"required": ("handle",), "optional": ("channel_id",), "prefix": "REACHWEAVER_YOUTUBE"},
    "facebook": {"required": ("page_name",), "optional": ("page_id",), "prefix": "REACHWEAVER_FACEBOOK"},
}
PLATFORMS = tuple(SCHEMAS)
LINKEDIN_DOC = "https://github.com/MSKazemi/reachweaver/blob/main/docs/LINKEDIN.md"
HANDLE_QUESTIONS = {"bluesky": "Your Bluesky handle, e.g. name.bsky.social",
                    "mastodon": "Your Mastodon username without the server, e.g. name (not @name@server)"}
QUESTIONS = {
    "handle": "Your public username on {label}",
    "server": "Your Mastodon server, e.g. https://mastodon.social",
    "chat_id": "Telegram channel: @channelname or its numeric id",
    "bot_username": "The username of the bot that posts to the channel (you add it as a channel admin)",
    "person_id": "Your LinkedIn member id (the 'sub' value; how to find it: " + LINKEDIN_DOC + ")",
    "linkedin_version": "LinkedIn API version YYYYMM (the 'Latest Version' listed in " + LINKEDIN_DOC + ")",
    "page_name": "The name of the Facebook Page you manage",
}
LABELS = {"bluesky": "Bluesky", "mastodon": "Mastodon", "telegram": "Telegram", "linkedin": "LinkedIn",
          "instagram": "Instagram", "threads": "Threads", "tiktok": "TikTok", "youtube": "YouTube",
          "facebook": "Facebook Pages"}
INTEGER_FIELDS = frozenset({"max_characters", "max_media_attachments", "image_size_limit", "media_description_limit"})
STRIP_AT = frozenset({"handle", "bot_username", "channel_username"})
TELEGRAM_CHAT_ID = re.compile(r"(?:@[A-Za-z0-9_]{5,32}|-?[0-9]+)")  # the rule connect-check applies
IDENTITY_FIELDS = {"bluesky": ("handle",), "mastodon": ("server", "handle"), "telegram": ("chat_id",),
                   "linkedin": ("person_id",), "instagram": ("handle",), "threads": ("handle",),
                   "tiktok": ("handle",), "youtube": ("handle",), "facebook": ("page_name",)}
_PLACEHOLDERS = frozenset(v.casefold() for v in PLACEHOLDER_VALUES)


def _ask(question: str) -> str:
    try:
        return input(question + ": ").strip()
    except EOFError as exc:
        raise ValidationError("Input ended. Nothing was written.") from exc


# What each required field looks like, for a blank answer and the non-terminal error. Shapes, not real accounts.
NEEDED = {"handle": ("your handle", "name.bsky.social"), "server": ("your server", "https://mastodon.social"),
          "chat_id": ("your channel", "@yourchannel"), "bot_username": ("your bot's username", "your_bot"),
          "person_id": ("your LinkedIn member id", "YOUR_MEMBER_ID"),
          "linkedin_version": ("the LinkedIn API version", "YYYYMM"), "page_name": ("your Page name", "Your Page")}
# The identity shapes above are refused like the example file's placeholders, in case one is copied as it is
# (not the server: https://mastodon.social is a real server people use).
_PLACEHOLDERS = (_PLACEHOLDERS | {"yourname"}
                 | frozenset(shape.casefold() for key, (_, shape) in NEEDED.items() if key != "server"))
REQUIRED_ATTEMPTS = 3


def _example(platform: str, key: str) -> str:
    if key == "handle" and platform != "bluesky":
        return "yourname"
    return NEEDED.get(key, (key, "VALUE"))[1]


def _ask_required(platform: str, key: str) -> str:
    """Ask for one required field; a blank answer asks again (with an example) instead of stopping."""
    for attempt in range(REQUIRED_ATTEMPTS):
        question = HANDLE_QUESTIONS.get(platform) if key == "handle" else None
        answer = _ask(question or QUESTIONS.get(key, key).format(label=LABELS[platform]))
        if answer:
            return answer
        if attempt < REQUIRED_ATTEMPTS - 1:
            print(f"  {LABELS[platform]} needs {NEEDED.get(key, (key,))[0]} (like {_example(platform, key)}). "
                  "Type it, or press Ctrl-D to stop.")
    return ""


def _missing_message(platform: str, missing: list[str]) -> str:
    """``Bluesky needs your handle (like name.bsky.social). Add --handle name.bsky.social``"""
    needs = " and ".join(f"{NEEDED.get(k, (k,))[0]} (like {_example(platform, k)})" for k in missing)
    flags = " ".join(f"--{k} {_example(platform, k)}" if k in ("handle", "server") else
                     f"--field {k}={shlex.quote(_example(platform, k))}" for k in missing)
    return f"{LABELS[platform]} needs {needs}. Add {flags}. Nothing was written."


def _clean(key: str, value: str) -> object:
    text = value.strip()
    if key in STRIP_AT:
        text = text.lstrip("@").strip()
    if key in INTEGER_FIELDS:
        try:
            return int(text)
        except ValueError:
            raise ValidationError(f"{key} must be a whole number") from None
    return text


def _fields(platform: str, args) -> dict[str, object]:
    schema = SCHEMAS[platform]
    allowed = set(schema["required"]) | set(schema["optional"])
    values: dict[str, object] = {}
    for item in args.field or []:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValidationError("--field takes KEY=VALUE, for example --field bot_username=mybot")
        if SECRET_FIELD.search(key):
            raise ValidationError("Never put tokens, passwords or sessions in accounts.json. Set them as environment "
                                  "variables; this command prints their names.")
        if key not in allowed:
            raise ValidationError(f"{LABELS[platform]} accounts take these fields: {', '.join(sorted(allowed))} "
                                  f"(not {key!r})")
        values[key] = _clean(key, value)
    for key in ("handle", "server"):
        given = getattr(args, key, None)
        if given is not None:
            if key not in allowed:
                raise ValidationError(f"--{key} is not used for {LABELS[platform]} accounts")
            values[key] = _clean(key, given)
    return values


def _default_id(platform: str, taken: set[str]) -> str:
    base = f"my-{platform}"
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def _default_prefix(platform: str, account_id: str, existing: list[dict]) -> str:
    """The example's prefix for the first account of a network, else one derived from the id."""
    derived = "REACHWEAVER_" + re.sub(r"[^A-Z0-9]+", "_", account_id.upper()).strip("_")
    candidates = ([SCHEMAS[platform]["prefix"]] if account_id == f"my-{platform}" else []) + [derived]
    candidates += [f"{derived}_{n}" for n in range(2, 10)]
    for prefix in candidates:  # the first one whose credential names overlap nobody else's (loader rule)
        if not env_prefix_conflicts(existing + [{"id": account_id, "env_prefix": prefix}]):
            return prefix
    return derived  # the loader then explains the overlap; pass --env-prefix


def _identity(platform: str, account: dict) -> tuple[str, ...] | None:
    values = []
    for key in IDENTITY_FIELDS[platform]:
        value = account.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        values.append(profile_store.normalize_username(value).rstrip("/"))
    return tuple(values)


def _read_existing(path: Path) -> tuple[dict, list[dict]]:
    if not path.exists():
        return {"accounts": []}, []
    load_accounts(path)  # the current file must already be valid; never paper over a broken one
    data = read_json_file(path)
    return data, [dict(a) for a in data["accounts"]]


def _validate(data: dict, account_id: str) -> dict:
    """Load the candidate file with the normal loader; returns the normalized new entry."""
    with tempfile.TemporaryDirectory(prefix="rw-account-add-") as folder:
        candidate = Path(folder) / "accounts.json"
        candidate.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return load_accounts(candidate)[account_id]


def _persona_checks(entry: dict, persona: str | None, others: dict[str, dict], profile: dict | None) -> list[str]:
    """Refusals (raised) and notes (returned) from the wrong-account guard. Never loosens it."""
    notes: list[str] = []
    platform = entry["platform"]
    name = persona_guard.account_username(entry)
    owner = None
    if profile and name:
        for handle in profile.get("handles", []):
            if (profile_store.canonical_platform(handle.get("platform")) == platform
                    and handle.get("status") != "unavailable"
                    and profile_store.normalize_username(handle["username"]) == profile_store.normalize_username(name)):
                owner = handle["persona"]
    if persona:
        everyone = dict(others, **{entry["id"]: entry})
        refusal = persona_guard.check_account(entry, persona, everyone, profile)
        if refusal:
            raise ValidationError(refusal + " Nothing was written.")
        if owner and owner != persona:
            raise ValidationError(f"Wrong-account check: your profile records @{name} on {platform} as your "
                                  f"'{owner}' persona, not '{persona}'. Use --persona {owner}, or fix the profile "
                                  "(`reachweaver profile --handle ...`). Nothing was written.")
        if profile:
            route = persona_guard.route(platform, persona, everyone, profile)
            if route["status"] == "conflict":
                raise ValidationError(route["advice"] + " Nothing was written.")
    elif owner:
        notes.append(f"Your profile records @{name} as your '{owner}' persona; the wrong-account guard will match "
                     f"it by username. Add --persona {owner} to make that explicit.")
    same = [a["id"] for a in others.values() if a.get("platform") == platform]
    if same and not persona and not owner:
        notes.append(f"You now have {len(same) + 1} {LABELS[platform]} accounts ({', '.join(same)} and "
                     f"{entry['id']}). Give each a persona (--persona work, family...) so the wrong-account guard "
                     "can tell them apart (docs/PERSONAS-AND-ACCOUNT-KIT.md).")
    return notes


def _write(path: Path, data: dict) -> Path | None:
    """Keep the previous file as .bak, then replace the file atomically (mode 0600 for a new file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    mode = 0o600
    if path.exists():
        mode = path.stat().st_mode & 0o777
        backup = path.with_name(path.name + ".bak")
        _atomic(backup, path.read_bytes(), mode)
    _atomic(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), mode)
    return backup


def _atomic(target: Path, payload: bytes, mode: int) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


CONNECT_NOW = "Connect it now so ReachWeaver can post for you? [y/N]"


def _offer_connect(platform: str, connect_now) -> bool:
    """After the write, at a terminal (stdin AND stdout, as ``connect --guide`` needs) and for a network with an
    enabled adapter: ask whether to continue into the connection guide. Enter, anything but y/yes, or end of input
    means no, because posting by hand needs no connection. Nothing is asked when no ``connect_now`` was given
    (``connect`` itself adding a first account already continues)."""
    if connect_now is None or platform not in SUPPORTED_LIVE:
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        answer = input(CONNECT_NOW + " ").strip().casefold()
    except EOFError:
        return False
    return answer in ("y", "yes")


def run(args, connect_now=None) -> int:
    """``account-add``. ``connect_now(account_id) -> int`` (given by the CLI) runs ``connect --guide --account ID``
    in this process when the person answers y to ``CONNECT_NOW``; its exit code is returned."""
    platform = args.platform
    if platform not in SCHEMAS:
        raise ValidationError(f"account-add supports {', '.join(PLATFORMS)}")
    schema = SCHEMAS[platform]
    path = accounts_path(args.accounts)
    data, existing = _read_existing(path)
    values = _fields(platform, args)
    missing = [key for key in schema["required"] if not values.get(key)]
    asked = False
    if missing and sys.stdin.isatty() and not args.dry_run:
        print(f"Adding a {LABELS[platform]} account. Public details only: ReachWeaver never asks for a password "
              "or token here.")
        for key in missing:
            answer = _ask_required(platform, key)
            if answer:
                values[key] = _clean(key, answer)
        asked = True
        missing = [key for key in schema["required"] if not values.get(key)]
    if missing:
        raise ValidationError(_missing_message(platform, missing))
    for key, value in values.items():
        if isinstance(value, str) and value.strip().casefold() in _PLACEHOLDERS:
            raise ValidationError(f"{key} {value!r} is the example placeholder; use your own account's value. "
                                  "Nothing was written.")
    if platform == "telegram" and not TELEGRAM_CHAT_ID.fullmatch(str(values["chat_id"])):
        raise ValidationError("Telegram chat_id must be @channelname (5-32 letters, digits or _) or a numeric id. "
                              "Nothing was written.")
    # An unedited example entry of this network (from `workspace --init`) is filled in place, keeping its id
    # and env_prefix, instead of adding a second account next to a placeholder. Never with --id.
    example = None if args.id else next(
        (i for i, a in enumerate(existing) if a.get("platform") == platform and placeholder_fields(a)), None)
    kept = existing if example is None else existing[:example] + existing[example + 1:]
    taken = {str(a.get("id")) for a in kept}
    account_id = str(existing[example]["id"]) if example is not None else args.id or _default_id(platform, taken)
    if account_id in taken:
        raise ValidationError(f"An account with id {account_id!r} already exists in {path}; choose another --id. "
                              "Nothing was written.")
    prefix = args.env_prefix or (str(existing[example]["env_prefix"]) if example is not None else
                                 _default_prefix(platform, account_id, kept))
    entry: dict[str, object] = {"id": account_id, "platform": platform, "env_prefix": prefix}
    entry.update(values)
    if args.persona:
        entry["persona"] = args.persona
    identity = _identity(platform, entry)
    for other in kept:
        if other.get("platform") == platform and identity is not None and _identity(platform, other) == identity:
            raise ValidationError(f"{LABELS[platform]} account {other.get('id')!r} already uses this identity in "
                                  f"{path}. Nothing was written.")
    position = len(kept) if example is None else example
    candidate = dict(data)
    candidate["accounts"] = kept[:position] + [entry] + kept[position:]
    normalized = _validate(candidate, account_id)
    candidate["accounts"] = kept[:position] + [normalized] + kept[position:]
    if example is not None:
        print(f"Filling in the example entry {account_id} (it still held placeholder values) with your details.")
    others = {str(a["id"]): a for a in kept}
    notes = _persona_checks(normalized, args.persona, others, profile_store.load_optional(args.profile))
    version_note = linkedin_version_note(normalized)
    if version_note:
        notes.append(version_note)
    names = [normalized["env_prefix"] + suffix for suffix in account_credential_suffixes(normalized)]
    print(describe(normalized))
    for note in notes:
        print("Note: " + note)
    if args.dry_run:
        print(f"DRY RUN: nothing written. Without --dry-run this entry is added to {path}.")
        return 0
    # A local, reversible write (the previous file is kept as .bak), so Enter means yes.
    if asked and _ask(f"Add this account to {path}? [Y/n]").casefold() not in ("", "y", "yes"):
        print("Cancelled: nothing written.")
        return 0
    backup = _write(path, candidate)
    print(f"Added account {account_id} ({LABELS[platform]}) to {path}."
          + (f" The previous file is kept as {backup}." if backup else ""))
    if _offer_connect(platform, connect_now):  # the same connect --guide flow, unchanged; no secret asked here
        print(f"\nContinuing with the connection steps for {account_id}.")
        return connect_now(account_id)
    # Every next step repeats the options this run was given (--accounts, --db ...), so following one reaches
    # this accounts file, not the default one (UX 2026-10-21).
    if platform in SUPPORTED_LIVE:
        print(f"Set these yourself with {home.command('connect --guide')} (it shows the exact lines), never in "
              "accounts.json: " + ", ".join(names))
        print(f"Next: {home.command(f'connect --guide --account {account_id}')}  (official sign-in steps; you type "
              "any secret yourself, ReachWeaver never asks for it here). After that, write your first post: "
              f"{home.command(f'draft --account {account_id}')} --text '...'")
        print(f"Or post by hand now, no sign-in needed: {home.command(f'share --platform {platform}')} --text '...'")
    else:
        print(f"ReachWeaver has no enabled {LABELS[platform]} adapter, so this entry is public metadata for "
              "manual-export, the calendar and the wrong-account guard."
              + (f" Credential names reserved for a future supervised pilot: {', '.join(names)}." if names else ""))
        print(f"Next: {home.command(f'share --platform {platform}')} --text '...'  (copy-ready text you post by hand)")
    return 0


FIELD_LABELS = {"handle": "Handle", "server": "Server", "chat_id": "Channel", "bot_username": "Bot",
                "channel_username": "Channel name", "person_id": "LinkedIn member id",
                "linkedin_version": "LinkedIn API version", "page_name": "Page", "page_id": "Page id",
                "persona": "Persona", "service": "Server", "visibility": "Visibility"}
_NOT_SHOWN = frozenset({"id", "platform", "env_prefix"})


def linkedin_version_note(entry: dict) -> str | None:
    """A warning (never a refusal) when a LinkedIn entry pins an API version outside the supported window. Uses the
    same check (``linkedin.version_status``) that makes ``health`` report linkedin_version_unsupported, so adding
    the account says it now instead of the dashboard surprising you later. Posting by hand is unaffected."""
    if entry.get("platform") != "linkedin":
        return None
    from .linkedin import VERSION_UNSUPPORTED_MONTHS, version_status
    level, message = version_status(entry.get("linkedin_version"))
    if level == "ok":
        return None
    version = entry.get("linkedin_version")
    if level == "error" and "linkedin_version_unsupported" in message:
        return (f"LinkedIn API version {version} is {VERSION_UNSUPPORTED_MONTHS} months old or more. LinkedIn "
                "supports each version for a minimum of one year, so ReachWeaver refuses automatic LinkedIn sends "
                "with it and the dashboard shows it as blocked. Posting by hand is not affected. Use the 'Latest "
                "Version' from LinkedIn's versioning page (see docs/LINKEDIN.md): set linkedin_version in "
                "accounts.json, or remove this account and add it again with --field linkedin_version=YYYYMM.")
    if level == "warning":
        return (f"LinkedIn API version {version} is getting old; plan to move to a newer version (docs/LINKEDIN.md) "
                "before LinkedIn stops supporting it.")
    return f"LinkedIn API version {version} cannot be used for automatic sending: {message}."


def describe(entry: dict) -> str:
    """The entry as plain lines (Network, the public identity, Account name) instead of raw JSON."""
    rows = [("Network", LABELS.get(str(entry.get("platform")), str(entry.get("platform"))))]
    for key, value in entry.items():
        if key in _NOT_SHOWN or value in (None, ""):
            continue
        shown = f"@{value}" if key in ("handle", "bot_username") and entry.get("platform") != "bluesky" else value
        rows.append((FIELD_LABELS.get(key, key.replace("_", " ").capitalize()), str(shown)))
    rows.append(("Account name", f"{entry['id']} (what you type after --account)"))
    rows.append(("Credential prefix", f"{entry['env_prefix']} (variable names only; values never go in this file)"))
    width = max(len(label) for label, _ in rows) + 2
    return "\n".join(f"  {(label + ':').ljust(width)}{value}" for label, value in rows)


def run_remove(args) -> int:
    """``account-remove ID``: drop one entry from accounts.json, keeping the previous file as .bak.

    Only the public entry goes. Credentials (environment variables, the credentials directory), the database,
    approvals, the approval ledger and disconnect state are never touched; posts recorded for the account stay
    in the database and are counted read-only so the person knows they are there.
    """
    path = accounts_path(args.accounts)
    accounts = load_accounts(path)  # a missing or broken file stops here with its own message
    if args.id not in accounts:
        known = ", ".join(accounts) or "none"
        raise ValidationError(f"Unknown account {args.id!r} in {path} (accounts: {known}). Nothing was written.")
    data = read_json_file(path)
    entry = accounts[args.id]
    print(describe(entry))
    posts = _posts_of(args.db, args.id)
    if posts:
        print(f"{posts} post(s) in the local database belong to {args.id}. They are kept: the database is not "
              "changed, and they cannot be sent while the account is missing from accounts.json.")
    if args.dry_run:
        print(f"DRY RUN: nothing written. Without --dry-run this entry is removed from {path}.")
        return 0
    if sys.stdin.isatty():
        if _ask(f"Remove {args.id} from {path}? The previous file is kept as a .bak [y/N]").casefold() not in ("y", "yes"):
            print("Cancelled: nothing written.")
            return 0
    elif not args.yes:
        raise ValidationError("account-remove asks for confirmation in a terminal; elsewhere add --yes. "
                              "Nothing was written.")
    data["accounts"] = [a for a in data["accounts"] if not (isinstance(a, dict) and a.get("id") == args.id)]
    backup = _write(path, data)
    print(f"Removed account {args.id} ({LABELS.get(entry['platform'], entry['platform'])}) from {path}. "
          f"The previous file is kept as {backup}; to undo, copy it back over {path.name}.")
    names = [entry["env_prefix"] + suffix for suffix in account_credential_suffixes(entry)]
    print("Not touched: credentials, the database, approvals and disconnect state."
          + (f" If you set {', '.join(names)}, remove them yourself when you no longer need them." if names else ""))
    return 0


def _posts_of(db: str | None, account_id: str) -> int:
    """Posts recorded for the account, read-only; 0 when there is no database yet."""
    from .storage import connect_readonly, database_path
    database = database_path(db)
    if not database.is_file():
        return 0
    with connect_readonly(database) as con:
        return int(con.execute("SELECT COUNT(*) FROM posts WHERE account_id=?", (account_id,)).fetchone()[0])
