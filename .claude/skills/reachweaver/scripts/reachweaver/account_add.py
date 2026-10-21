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

Wrong-account guard: ``--persona`` must be a persona in your profile (when you have one), and an entry whose
username the profile records under another persona, or that contradicts the profile, is refused.

No network, no database, no send path (``SEND_PATH = None``).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from . import persona_guard
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
QUESTIONS = {
    "handle": "Your public username on {label} (for Bluesky the full handle, e.g. name.bsky.social)",
    "server": "Your Mastodon server, e.g. https://mastodon.social",
    "chat_id": "Telegram channel: @channelname or its numeric id",
    "bot_username": "The username of the bot that posts to the channel (you add it as a channel admin)",
    "person_id": "Your LinkedIn member id (the 'sub' value, see docs/LINKEDIN.md)",
    "linkedin_version": "LinkedIn API version YYYYMM (the 'Latest Version' in docs/LINKEDIN.md)",
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


def run(args) -> int:
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
            answer = _ask(QUESTIONS.get(key, key).format(label=LABELS[platform]))
            if answer:
                values[key] = _clean(key, answer)
        asked = True
        missing = [key for key in schema["required"] if not values.get(key)]
    if missing:
        flags = " ".join(f"--{k} VALUE" if k in ("handle", "server") else f"--field {k}=VALUE" for k in missing)
        raise ValidationError(f"{LABELS[platform]} needs {', '.join(missing)}. Add: {flags}. Nothing was written.")
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
    names = [normalized["env_prefix"] + suffix for suffix in account_credential_suffixes(normalized)]
    print(json.dumps(normalized, indent=2, ensure_ascii=False))
    for note in notes:
        print("Note: " + note)
    if args.dry_run:
        print(f"DRY RUN: nothing written. Without --dry-run this entry is added to {path}.")
        return 0
    if asked and _ask(f"Add this account to {path}? [y/N]").casefold() not in ("y", "yes"):
        print("Cancelled: nothing written.")
        return 0
    backup = _write(path, candidate)
    print(f"Added account {account_id} ({LABELS[platform]}) to {path}."
          + (f" The previous file is kept as {backup}." if backup else ""))
    if platform in SUPPORTED_LIVE:
        print("Set these yourself, as environment variables or systemd credentials, never in accounts.json: "
              + ", ".join(names))
        print(f"Next: reachweaver connect --guide --account {account_id}  (official sign-in steps; you type any "
              "secret yourself, ReachWeaver never asks for it here). Then draft and review as usual.")
    else:
        print(f"ReachWeaver has no enabled {LABELS[platform]} adapter, so this entry is public metadata for "
              "manual-export, the calendar and the wrong-account guard. Post by hand: "
              f"reachweaver share --platform {platform} --text '...'"
              + (f". Credential names reserved for a future supervised pilot: {', '.join(names)}" if names else ""))
    return 0
