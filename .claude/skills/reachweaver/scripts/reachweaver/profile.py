"""The owner's profile knowledge base: who you are, which personas you run and which username each one uses.

``config/profile.json`` (schema ``reachweaver.profile/1``) is a private, local, owner-written file. It holds the
facts a sign-up form asks for (name, e-mail, phone, birth date), your personas (for example ``work`` and ``family``,
each with a display name, a bio per language and username stems) and one username per network and persona. The
account guard (``persona_guard``) and the sign-up kit (``account_kit``) read it. Nothing here opens a network
connection, a browser or a database (``SEND_PATH = None``).

Data minimisation: the schema is closed. Unknown fields are refused, and so is any key that looks like a secret or an
identity document (password, token, cookie, one-time code, recovery code, passport, national id, card, IBAN, tax
code). Passwords stay in your password manager and are never part of this file. Values are never echoed in an error.
The file is written atomically with mode 0600 and is in ``.gitignore``.
"""
from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from . import home
from .account_advisor import CHANNELS
from .accounts import PERSONA_ID
from .policy import PLATFORMS, ValidationError

SEND_PATH = None  # Asserted in tests: this module can never send, upload or contact anything.
SCHEMA = "reachweaver.profile/1"
MAX_BYTES = 256 * 1024
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
USERNAME = re.compile(r"^[\w.\-]{1,64}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PHONE = re.compile(r"^\+[1-9][0-9]{6,14}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
TIMEZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]{1,63}$")
FORBIDDEN_KEY = re.compile(
    r"(password|passwd|passphrase|token|secret|cookie|session|api.?key|private.?key|otp|totp|2fa|mfa|recovery"
    r"|passport|national.?id|ssn|tax|iban|card|cvv|id.?number|credential)", re.I)
HANDLE_STATUSES = ("planned", "claimed", "unavailable")
DEFAULT_STATUS = "claimed"  # the owner's own statement that the account exists; ReachWeaver never verifies it
ALIASES = {"facebook_pages": "facebook", "dev": "devto"}  # advisor/distribution ids -> capability ids
KNOWN_PLATFORMS = frozenset({ALIASES.get(name, name) for name in CHANNELS} | PLATFORMS)

TOP_KEYS = ("schema", "person", "personas", "handles", "preferences")
PERSON_FIELDS = ("given_name", "family_name", "native_name", "email", "phone", "birth_date", "country", "timezone",
                 "languages")
PERSONA_FIELDS = ("display_name", "bio", "email", "username_stems", "website", "avatar")
LIST_FIELDS = ("languages", "username_stems")
I18N_FIELDS = ("display_name", "bio")
SENSITIVE = ("email", "phone", "birth_date")  # masked whenever the profile is displayed
_BIDI = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def canonical_platform(name: object) -> str | None:
    """The capability-registry id for a platform name (``facebook_pages`` -> ``facebook``), else None."""
    if not isinstance(name, str):
        return None
    key = name.strip().casefold()
    key = ALIASES.get(key, key)
    return key if key in KNOWN_PLATFORMS else None


def normalize_username(value: str) -> str:
    """Comparison form: NFKC, surrounding space and a leading ``@`` removed, case-folded."""
    return unicodedata.normalize("NFKC", str(value)).strip().lstrip("@").strip().casefold()


def _clean_text(value: object, limit: int, multiline: bool = False) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return False
    for char in value:
        if char in _BIDI:
            return False
        if unicodedata.category(char) == "Cc" and not (multiline and char == "\n"):
            return False
    return True


def _walk_keys(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def _check_i18n(value: object, label: str, limit: int, multiline: bool, errors: list[str]) -> None:
    if isinstance(value, str):
        if not _clean_text(value, limit, multiline):
            errors.append(f"{label} must be 1-{limit} characters without control or bidi characters")
    elif isinstance(value, dict) and value:
        for lang, text in value.items():
            if not isinstance(lang, str) or not LANGUAGE.fullmatch(lang):
                errors.append(f"{label} has an invalid language code")
            elif not _clean_text(text, limit, multiline):
                errors.append(f"{label}.{lang} must be 1-{limit} characters without control or bidi characters")
    else:
        errors.append(f"{label} must be a text or a {{language: text}} object")


def _check_username(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not USERNAME.fullmatch(normalize_username(value).replace(" ", " ")) \
            or any(unicodedata.category(c) == "Cc" for c in value):
        errors.append(f"{label} must be 1-64 letters, digits, '.', '_' or '-' (a leading @ is fine)")


def _check_person(person: object, errors: list[str]) -> None:
    if not isinstance(person, dict):
        errors.append("person must be an object")
        return
    for key in person:
        if key not in PERSON_FIELDS:
            errors.append(f"person.{key}: unknown field (allowed: {', '.join(PERSON_FIELDS)})")
    for key in ("given_name", "family_name", "native_name"):
        if key in person and not _clean_text(person[key], 100):
            errors.append(f"person.{key} must be 1-100 characters without control or bidi characters")
    if "email" in person and not (isinstance(person["email"], str) and len(person["email"]) <= 254
                                  and EMAIL.fullmatch(person["email"])):
        errors.append("person.email is not a valid e-mail address")
    if "phone" in person and not (isinstance(person["phone"], str) and PHONE.fullmatch(person["phone"])):
        errors.append("person.phone must be in international form, for example +390000000000")
    if "birth_date" in person:
        try:
            when = date.fromisoformat(person["birth_date"]) if isinstance(person["birth_date"], str) else None
        except ValueError:
            when = None
        if when is None or when > date.today() or when.year < 1900:
            errors.append("person.birth_date must be a past date as YYYY-MM-DD")
    if "country" in person and not (isinstance(person["country"], str) and COUNTRY.fullmatch(person["country"])):
        errors.append("person.country must be a two-letter ISO 3166 code such as IT")
    if "timezone" in person and not (isinstance(person["timezone"], str) and TIMEZONE.fullmatch(person["timezone"])):
        errors.append("person.timezone must look like Europe/Rome")
    if "languages" in person:
        langs = person["languages"]
        if not (isinstance(langs, list) and 1 <= len(langs) <= 12
                and all(isinstance(x, str) and LANGUAGE.fullmatch(x) for x in langs)):
            errors.append("person.languages must be 1-12 language codes such as en or zh-Hans")


def _check_persona(name: str, persona: object, errors: list[str]) -> None:
    label = f"personas.{name}"
    if not isinstance(persona, dict):
        errors.append(f"{label} must be an object")
        return
    for key in persona:
        if key not in PERSONA_FIELDS:
            errors.append(f"{label}.{key}: unknown field (allowed: {', '.join(PERSONA_FIELDS)})")
    if "display_name" in persona:
        _check_i18n(persona["display_name"], f"{label}.display_name", 100, False, errors)
    if "bio" in persona:
        _check_i18n(persona["bio"], f"{label}.bio", 2000, True, errors)
    if "email" in persona and not (isinstance(persona["email"], str) and len(persona["email"]) <= 254
                                   and EMAIL.fullmatch(persona["email"])):
        errors.append(f"{label}.email is not a valid e-mail address")
    if "username_stems" in persona:
        stems = persona["username_stems"]
        if not (isinstance(stems, list) and 1 <= len(stems) <= 8):
            errors.append(f"{label}.username_stems must be a list of 1-8 usernames")
        else:
            for stem in stems:
                _check_username(stem, f"{label}.username_stems item", errors)
    if "website" in persona:
        site = persona["website"]
        parts = urlsplit(site) if isinstance(site, str) and len(site) <= 200 else None
        if not (parts and parts.scheme in ("http", "https") and parts.netloc and _clean_text(site, 200)):
            errors.append(f"{label}.website must be an http(s) URL of at most 200 characters")
    if "avatar" in persona and not _clean_text(persona["avatar"], 300):
        errors.append(f"{label}.avatar must be a file path of at most 300 characters")


def validate_profile(data: object) -> list[str]:
    """Every problem with ``data`` as plain sentences. Never includes a value."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["the profile must be a JSON object"]
    for key in _walk_keys(data):
        if FORBIDDEN_KEY.search(key):
            errors.append(f"field '{key}': never store passwords, tokens, one-time or recovery codes, identity "
                          "documents, card or tax numbers here; keep them in your password manager")
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be '{SCHEMA}'")
    for key in data:
        if key not in TOP_KEYS:
            errors.append(f"{key}: unknown top-level field (allowed: {', '.join(TOP_KEYS)})")
    _check_person(data.get("person", {}), errors)
    personas = data.get("personas", {})
    persona_ids: set[str] = set()
    if not isinstance(personas, dict):
        errors.append("personas must be an object")
    else:
        for name, persona in personas.items():
            if not (isinstance(name, str) and PERSONA_ID.fullmatch(name)):
                errors.append("a persona id must be 2-31 characters: a lowercase letter, then lowercase letters, "
                              "digits or '-'")
                continue
            persona_ids.add(name)
            _check_persona(name, persona, errors)
    handles = data.get("handles", [])
    if not isinstance(handles, list):
        errors.append("handles must be a list")
    else:
        seen: set[tuple[str, str]] = set()
        for index, handle in enumerate(handles):
            label = f"handles[{index}]"
            if not isinstance(handle, dict) or set(handle) != {"platform", "persona", "username", "status"}:
                errors.append(f"{label} needs exactly platform, persona, username and status")
                continue
            platform = canonical_platform(handle["platform"])
            if platform is None:
                errors.append(f"{label}: unknown platform")
            if handle["persona"] not in persona_ids:
                errors.append(f"{label}: unknown persona")
            _check_username(handle["username"], f"{label}.username", errors)
            if handle["status"] not in HANDLE_STATUSES:
                errors.append(f"{label}.status must be one of {', '.join(HANDLE_STATUSES)}")
            if platform and handle["persona"] in persona_ids:
                if (platform, handle["persona"]) in seen:
                    errors.append(f"{label}: duplicate entry for this platform and persona")
                seen.add((platform, handle["persona"]))
    prefs = data.get("preferences", {})
    if not isinstance(prefs, dict):
        errors.append("preferences must be an object")
    else:
        for key in prefs:
            if key != "default_persona":
                errors.append(f"preferences.{key}: unknown field (allowed: default_persona)")
        if "default_persona" in prefs and prefs["default_persona"] not in persona_ids:
            errors.append("preferences.default_persona must name one of your personas")
    return errors


def profile_path(path: str | Path | None = None) -> Path:
    """``--profile``, else ``config/profile.json`` (inside the active workspace).

    No environment variable on purpose: tests/test_hardening.py keeps every environment read on a short allow-list.
    """
    return Path(path) if path else home.default_path("config/profile.json")


def load_profile(path: str | Path | None = None) -> dict:
    target = profile_path(path)
    if not target.is_file():
        raise ValidationError(f"Missing {target}. Create it with `reachweaver profile --init` "
                              "(it stays on this computer and is in .gitignore)")
    try:
        if target.stat().st_size > MAX_BYTES:
            raise ValidationError(f"Profile is larger than {MAX_BYTES} bytes: {target}")
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Profile is unreadable or not JSON: {target}") from exc
    errors = validate_profile(data)
    if errors:
        raise ValidationError("Profile is invalid: " + "; ".join(errors[:5])
                              + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else "")
                              + ". Run `reachweaver profile --check`")
    return data


def load_optional(path: str | Path | None = None) -> dict | None:
    """The profile, or None when no file exists. A file that exists but is invalid still raises."""
    return load_profile(path) if profile_path(path).is_file() else None


def save_profile(data: dict, path: str | Path | None = None) -> Path:
    """Validate, then replace the file atomically with mode 0600. The old file survives any failure."""
    errors = validate_profile(data)
    if errors:
        raise ValidationError("Profile not saved: " + "; ".join(errors[:5]))
    target = profile_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def permissions_finding(path: str | Path | None = None) -> str | None:
    """A sentence when other users could read the file, else None. Not applicable off POSIX."""
    target = profile_path(path)
    try:
        mode = target.stat().st_mode & 0o777
    except OSError:
        return None
    if os.name == "posix" and mode & 0o077:
        return f"{target} is readable by other users (mode {mode:04o}); run: chmod 600 {target}"
    return None


def template(personas: list[str] | tuple[str, ...] = ("work",)) -> dict:
    """An empty, valid profile with the named personas."""
    for name in personas:
        if not PERSONA_ID.fullmatch(name):
            raise ValidationError(f"Invalid persona id {name!r}: use a lowercase letter, then lowercase letters, "
                                  "digits or '-' (2-31 characters)")
    return {"schema": SCHEMA, "person": {}, "personas": {name: {} for name in personas}, "handles": [],
            "preferences": {}}


def _split_list(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("["):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise ValidationError("A list value must be JSON like [\"a\",\"b\"] or comma-separated") from None
        return [str(item).strip() for item in value] if isinstance(value, list) else []
    return [part.strip() for part in text.split(",") if part.strip()]


def set_value(data: dict, dotted: str, raw: str) -> None:
    """Set one allowed field by dotted path; an empty value clears it. The document is re-validated first."""
    parts = dotted.split(".")
    bad = ValidationError(f"Cannot set '{dotted}'. Use person.FIELD, personas.ID.FIELD, personas.ID.bio.LANG or "
                          "preferences.default_persona; usernames go through --handle")
    work = copy.deepcopy(data)
    if parts[0] == "person" and len(parts) == 2 and parts[1] in PERSON_FIELDS:
        holder, key = work.setdefault("person", {}), parts[1]
        lang = None
    elif parts[0] == "personas" and len(parts) in (3, 4) and parts[2] in PERSONA_FIELDS:
        if not PERSONA_ID.fullmatch(parts[1]):
            raise bad
        holder, key = work.setdefault("personas", {}).setdefault(parts[1], {}), parts[2]
        lang = parts[3] if len(parts) == 4 else None
        if lang is not None and (key not in I18N_FIELDS or not LANGUAGE.fullmatch(lang)):
            raise bad
    elif parts == ["preferences", "default_persona"]:
        holder, key, lang = work.setdefault("preferences", {}), "default_persona", None
    else:
        raise bad
    if key in LIST_FIELDS:
        value: object = _split_list(raw)
    else:
        value = raw.strip() if "\n" not in raw else raw
    if lang is not None:
        current = holder.get(key)
        table = dict(current) if isinstance(current, dict) else ({} if current is None else {"en": current})
        if value:
            table[lang] = value
        else:
            table.pop(lang, None)
        value = table
    if value in ("", [], {}):
        holder.pop(key, None)
    else:
        holder[key] = value
    errors = validate_profile(work)
    if errors:
        raise ValidationError("Not changed: " + "; ".join(errors[:3]))
    data.clear()
    data.update(work)


def parse_handle_spec(spec: str) -> tuple[str, str, str, str]:
    """``platform:persona=username[:status]`` -> (platform, persona, username, status)."""
    left, sep, right = spec.partition("=")
    platform, colon, persona = left.partition(":")
    if not (sep and colon and platform.strip() and persona.strip() and right.strip()):
        raise ValidationError("A handle looks like PLATFORM:PERSONA=USERNAME or PLATFORM:PERSONA=USERNAME:STATUS "
                              f"(status: {', '.join(HANDLE_STATUSES)})")
    username, _, status = right.rpartition(":")
    if not username or status not in HANDLE_STATUSES:
        username, status = right, DEFAULT_STATUS
    return platform.strip(), persona.strip(), username.strip().lstrip("@").strip(), status


def set_handle(data: dict, platform: str, persona: str, username: str, status: str = DEFAULT_STATUS) -> None:
    """Add or replace the username of one persona on one platform. Re-validated before anything changes."""
    canonical = canonical_platform(platform)
    if canonical is None:
        raise ValidationError("Unknown platform. `reachweaver account-advisor` lists the platforms")
    work = copy.deepcopy(data)
    kept = [h for h in work.get("handles", [])
            if not (canonical_platform(h.get("platform")) == canonical and h.get("persona") == persona)]
    kept.append({"platform": canonical, "persona": persona, "username": username.strip().lstrip("@").strip(),
                 "status": status})
    work["handles"] = kept
    errors = validate_profile(work)
    if errors:
        raise ValidationError("Not changed: " + "; ".join(errors[:3]))
    data.clear()
    data.update(work)


def mask_email(value: object) -> str:
    if not isinstance(value, str) or "@" not in value:
        return ""
    local, _, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(value: object) -> str:
    if not isinstance(value, str) or len(value) < 8:
        return ""
    return value[:3] + "*" * (len(value) - 7) + value[-4:]


def mask_date(value: object) -> str:
    return f"{value[:4]}-**-**" if isinstance(value, str) and len(value) >= 4 else ""


def masked_view(data: dict) -> dict:
    """A copy safe to print: e-mail, phone and birth date are partly hidden; names and handles are public."""
    view = copy.deepcopy(data)
    for scope in [view.get("person", {})] + list(view.get("personas", {}).values()):
        if isinstance(scope, dict):
            if "email" in scope:
                scope["email"] = mask_email(scope["email"])
            if "phone" in scope:
                scope["phone"] = mask_phone(scope["phone"])
            if "birth_date" in scope:
                scope["birth_date"] = mask_date(scope["birth_date"])
    return view
