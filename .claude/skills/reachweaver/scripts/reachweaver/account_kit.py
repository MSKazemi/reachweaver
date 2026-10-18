"""Account kit: prepare everything for creating accounts yourself, behind one reviewable plan and one approval.

From your profile (``config/profile.json``) and a small reviewed registry (``signup_registry.json``) this builds, for
each platform you name and one persona, a packet: the display name, username candidates (or the nickname for
platforms that use one), e-mail, phone, birth date, bio and website that the platform's sign-up form may ask for, in
the language of its interface, plus a short form-label glossary (Simplified Chinese, Japanese, Korean) and the steps
that stay with you. Only the details a platform may ask for are included (data minimisation).

What it does NOT do, by design (docs/ACCOUNT-ADVISOR.md and the owner decisions of 2026-10-16/17: no tool operates
platform sites): open or drive any site or
app, fill a form, read or create a password, receive or solve a CAPTCHA or an SMS or e-mail code, pass an identity
check, check whether a username is free, or register anything. You paste the prepared values and complete every
verification yourself. Writing the packets to a new private folder is local and needs the plan's hash, which is the
single approval for all platforms in the plan. Nothing here uses the network (``SEND_PATH = None``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from . import distribution
from .account_advisor import CHANNELS
from .policy import ValidationError
from .profile import (canonical_platform, mask_date, mask_email, mask_phone, normalize_username)

SEND_PATH = None  # Asserted in tests: this module can never send, submit a form or contact a platform.
REGISTRY_FILE = Path(__file__).with_name("signup_registry.json")
PLAN_SCHEMA = "reachweaver.account-kit/1"
MAX_PLATFORMS = 12
MIN_APPROVAL_CHARS = 12
FIELDS = ("display_name", "username", "email", "country", "bio", "website", "phone", "birth_date")
USERNAME_KINDS = ("handle", "nickname")
CANDIDATE = re.compile(r"^[a-z0-9][a-z0-9._]{1,28}[a-z0-9]$")
MAX_CANDIDATES = 6
NO_GENERATED_TEXT = ("bio", "website")  # human-only venues: ReachWeaver prepares no profile text for them
REVERSE_ALIASES = {"facebook": "facebook_pages", "devto": "dev"}  # capability id -> advisor id


def load_registry() -> dict:
    return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))


def validate_registry(registry: dict) -> list[str]:
    errors: list[str] = []
    if registry.get("schema") != "reachweaver.signup/1":
        errors.append("schema must be reachweaver.signup/1")
    defaults = registry.get("defaults", {})
    glossary = registry.get("glossary", {})
    if not set(defaults.get("may_ask", [])) <= set(FIELDS) or "display_name" not in defaults.get("may_ask", []):
        errors.append("defaults.may_ask must be known fields and include display_name")
    for name, entry in registry.get("platforms", {}).items():
        if name not in CHANNELS:
            errors.append(f"{name}: not a platform of account-advisor")
        if not set(entry.get("add", [])) <= set(FIELDS):
            errors.append(f"{name}: add lists an unknown field")
        if entry.get("username_kind", "handle") not in USERNAME_KINDS:
            errors.append(f"{name}: unknown username_kind")
        locale = entry.get("ui_locale", defaults.get("ui_locale", "en"))
        if locale != "en" and locale not in glossary:
            errors.append(f"{name}: ui_locale {locale} has no glossary")
        if entry.get("region") and entry["region"] not in registry.get("region_notes", {}):
            errors.append(f"{name}: region has no region_note")
    for locale, rows in glossary.items():
        if not rows or not all(isinstance(r, dict) and set(r) == {"en", "local"} and r["en"] and r["local"]
                               for r in rows):
            errors.append(f"glossary {locale}: every row needs en and local")
    if not registry.get("human_steps") or not all(isinstance(s, str) and s for s in registry["human_steps"]):
        errors.append("human_steps must be a non-empty list of sentences")
    return errors


def advisor_id(platform: str) -> str:
    """The account-advisor id for a capability-registry id (``facebook`` -> ``facebook_pages``)."""
    return REVERSE_ALIASES.get(platform, platform)


def human_only_platforms() -> set[str]:
    """Advisor platforms the distribution registry marks human-only: no profile text is prepared for them."""
    return {p["id"] for p in distribution.load_registry().get("platforms", []) if p.get("human_only") is True} \
        & set(CHANNELS)


def _labels() -> dict[str, str]:
    return {p["id"]: p.get("label", p["id"]) for p in distribution.load_registry().get("platforms", [])}


def platform_spec(platform: str, registry: dict | None = None) -> dict:
    """The merged sign-up facts for one advisor platform id (defaults + the platform's own entry)."""
    registry = registry or load_registry()
    if platform not in CHANNELS:
        raise ValidationError(f"No reviewed starting page for '{platform}'. `reachweaver account-advisor` lists "
                              "the platforms this feature covers")
    defaults, entry = registry["defaults"], registry["platforms"].get(platform, {})
    may_ask = list(dict.fromkeys(list(defaults["may_ask"]) + list(entry.get("add", []))))
    if platform in human_only_platforms():
        may_ask = [f for f in may_ask if f not in NO_GENERATED_TEXT]
    locale = entry.get("ui_locale", defaults["ui_locale"])
    return {"platform": platform, "label": _labels().get(platform, platform), "starting_url": CHANNELS[platform].url,
            "may_ask": may_ask, "username_kind": entry.get("username_kind", defaults["username_kind"]),
            "ui_locale": locale, "glossary": registry["glossary"].get(locale, []),
            "region_note": registry.get("region_notes", {}).get(entry.get("region", ""), ""),
            "status": defaults["status"]}


def _resolve(platform: str) -> tuple[str, str]:
    canonical = canonical_platform(platform)
    advisor = advisor_id(canonical) if canonical else None
    if canonical is None or advisor not in CHANNELS:
        raise ValidationError(f"No reviewed starting page for '{platform}'. `reachweaver account-advisor` lists "
                              "the platforms this feature covers (Hacker News and similar venues are human-only "
                              "and have no kit)")
    return canonical, advisor


def _pick(value: object, locale: str) -> tuple[str | None, str | None]:
    """(text, language) for the interface locale: exact, same base language, English, then any. str -> (text, None)."""
    if isinstance(value, str):
        return value, None
    if not isinstance(value, dict) or not value:
        return None, None
    base = locale.split("-")[0]
    for lang in [locale, *sorted(k for k in value if k.split("-")[0] == base), "en", *sorted(value)]:
        if lang in value:
            return value[lang], lang
    return None, None


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower().replace(" ", "")


def username_candidates(profile: dict, persona: str, platform: str) -> list[str]:
    """Up to six username ideas, best first: your persona's names elsewhere (consistency), your stems, then names.

    Names you marked unavailable on this platform are left out. Availability is never checked: the form decides.
    """
    canonical = canonical_platform(platform) or platform
    entry = profile.get("personas", {}).get(persona, {})
    person = profile.get("person", {})
    taken = {normalize_username(h["username"]) for h in profile.get("handles", [])
             if h.get("persona") == persona and canonical_platform(h.get("platform")) == canonical
             and h.get("status") == "unavailable"}
    # A domain-style handle (Bluesky's alex.example.org) is not a good username elsewhere: skip names with 2+ dots.
    raw = [h["username"] for h in profile.get("handles", [])
           if h.get("persona") == persona and h.get("status") == "claimed" and h["username"].count(".") < 2]
    raw += list(entry.get("username_stems", []))
    given, family = _fold(person.get("given_name", "")), _fold(person.get("family_name", ""))
    if given and family:
        raw += [given + family, f"{given}.{family}", f"{given}_{family}", f"{given}{family}.{persona}"]
    names: list[str] = []
    for item in raw:
        name = _fold(str(item).lstrip("@"))
        if CANDIDATE.fullmatch(name) and normalize_username(name) not in taken and name not in names:
            names.append(name)
    return names[:MAX_CANDIDATES]


def _question(key: str, persona: str) -> str:
    if key == "person.email":
        return "What e-mail address should the sign-ups use?"
    if key == f"personas.{persona}.display_name":
        return f"What display name should your {persona} persona use?"
    if key == f"personas.{persona}.username_stems":
        return (f"Which username would you like for your {persona} persona (letters, digits, '.' or '_', "
                "3-30 characters)?")
    parts = key.split(".")
    if len(parts) == 4 and parts[2] in ("display_name", "bio"):
        what = "display name" if parts[2] == "display_name" else "bio"
        return (f"Optional: your {persona} {what} in {parts[3]} (Claude can draft one from your existing text in "
                "chat for you to review).")
    return f"Please provide {key}."


def build_packet(profile: dict, persona: str, platform: str) -> dict:
    """Everything prepared for one platform and one persona, plus what is still missing. Pure and offline."""
    canonical, advisor = _resolve(platform)
    if persona not in profile.get("personas", {}):
        raise ValidationError(f"'{persona}' is not a persona in your profile "
                              f"({', '.join(sorted(profile.get('personas', {}))) or 'none defined'})")
    registry = load_registry()
    spec = platform_spec(advisor, registry)
    person, entry, locale = profile.get("person", {}), profile["personas"][persona], spec["ui_locale"]
    fields: dict[str, object] = {}
    missing: list[dict] = []
    optional: list[dict] = []

    def ask(bucket: list[dict], key: str) -> None:
        bucket.append({"key": key, "question": _question(key, persona)})

    name, name_lang = _pick(entry.get("display_name"), locale)
    if name is None:
        ask(missing, f"personas.{persona}.display_name")
    else:
        fields["display_name"] = name
        if locale != "en" and isinstance(entry.get("display_name"), dict) and name_lang != locale \
                and name_lang is not None and name_lang.split("-")[0] != locale.split("-")[0]:
            ask(optional, f"personas.{persona}.display_name.{locale}")
    if spec["username_kind"] == "nickname":
        if name is not None:
            fields["nickname"] = name
    else:
        candidates = username_candidates(profile, persona, canonical)
        if candidates:
            fields["username_candidates"] = candidates
        else:
            ask(missing, f"personas.{persona}.username_stems")
    if "email" in spec["may_ask"]:
        email = entry.get("email") or person.get("email")
        if email:
            fields["email"] = email
        else:
            ask(missing, "person.email")
    for key in ("phone", "birth_date", "country"):
        if key in spec["may_ask"] and person.get(key):
            fields[key] = person[key]
    if "website" in spec["may_ask"] and entry.get("website"):
        fields["website"] = entry["website"]
    if "bio" in spec["may_ask"] and entry.get("bio"):
        bio, bio_lang = _pick(entry["bio"], locale)
        fields["bio"] = bio
        if bio_lang:
            fields["bio_language"] = bio_lang
            if locale != "en" and bio_lang.split("-")[0] != locale.split("-")[0]:
                ask(optional, f"personas.{persona}.bio.{locale}")
    fields["interface_language"] = locale
    return {"platform": canonical, "advisor_id": advisor, "label": spec["label"], "persona": persona,
            "starting_url": spec["starting_url"], "ui_locale": locale, "username_kind": spec["username_kind"],
            "status": spec["status"], "may_ask": spec["may_ask"], "fields": fields, "missing": missing,
            "optional_missing": optional, "glossary": spec["glossary"], "region_note": spec["region_note"],
            "human_steps": [step.replace("PLATFORM:PERSONA", f"{canonical}:{persona}")
                            for step in registry["human_steps"]],
            "after": f"reachweaver profile --handle {canonical}:{persona}=USERNAME"}


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _plan_hash(persona: str, packets: list[dict]) -> str:
    return hashlib.sha256(_canonical_json({"label": PLAN_SCHEMA, "persona": persona, "packets": packets})).hexdigest()


def _merge_questions(packets: list[dict], bucket: str) -> list[dict]:
    merged: dict[str, dict] = {}
    for packet in packets:
        for item in packet[bucket]:
            row = merged.setdefault(item["key"], {"key": item["key"], "question": item["question"], "platforms": []})
            row["platforms"].append(packet["platform"])
    return list(merged.values())


def plan(profile: dict, persona: str, platforms: list[str]) -> dict:
    """One plan for all named platforms: the packets, the open questions and the sha256 you approve."""
    if not platforms or len(platforms) > MAX_PLATFORMS:
        raise ValidationError(f"Name 1-{MAX_PLATFORMS} platforms with --platform")
    packets: list[dict] = []
    for name in platforms:
        packet = build_packet(profile, persona, name)
        if all(p["platform"] != packet["platform"] for p in packets):
            packets.append(packet)
    questions = _merge_questions(packets, "missing")
    return {"schema": PLAN_SCHEMA, "persona": persona, "packets": packets, "questions": questions,
            "optional_questions": _merge_questions(packets, "optional_missing"), "ready": not questions,
            "plan_sha256": _plan_hash(persona, packets)}


def _mask_field(key: str, value: object) -> str:
    if key == "email":
        return mask_email(value)
    if key == "phone":
        return mask_phone(value)
    if key == "birth_date":
        return mask_date(value)
    if key == "bio":
        return f"{len(str(value))} characters"
    if key == "username_candidates":
        return ", ".join(value)  # type: ignore[arg-type]
    return str(value)


def masked_plan(plan_: dict) -> dict:
    """A copy for ``--json`` on screen: e-mail, phone and birth date partly hidden, bio replaced by its length."""
    view = json.loads(json.dumps(plan_))
    for packet in view["packets"]:
        packet["fields"] = {key: _mask_field(key, value) if key in ("email", "phone", "birth_date", "bio") else value
                            for key, value in packet["fields"].items()}
    return view


def render_plan(plan_: dict) -> str:
    """The plan as text. E-mail, phone and birth date are partly hidden; the packets hold the full values."""
    persona = plan_["persona"]
    lines = [f"Account kit plan for persona '{persona}'. Nothing has been written, opened or sent.", ""]
    for packet in plan_["packets"]:
        lines.append(f"{packet['label']} ({packet['platform']}): open it yourself at {packet['starting_url']}")
        lines.append(f"  interface language {packet['ui_locale']}; {packet['username_kind']}; "
                     "form fields are an expectation, the live form decides")
        for key, value in packet["fields"].items():
            if key not in ("bio_language", "interface_language"):
                lines.append(f"  prepared {key}: {_mask_field(key, value)}")
        if packet["region_note"]:
            lines.append(f"  note: {packet['region_note']}")
        lines.append("")
    if plan_["questions"]:
        lines.append(f"Answer {len(plan_['questions'])} question(s) first (save each with "
                     "`reachweaver profile --set KEY=VALUE`), then run this again:")
        lines += [f"  {q['key']}: {q['question']} [{', '.join(q['platforms'])}]" for q in plan_["questions"]]
        lines.append("")
    if plan_["optional_questions"]:
        lines.append("Optional improvements:")
        lines += [f"  {q['key']}: {q['question']}" for q in plan_["optional_questions"]]
        lines.append("")
    lines.append(f"Plan sha256: {plan_['plan_sha256']}")
    if plan_["ready"]:
        platform_flags = " ".join(f"--platform {p['platform']}" for p in plan_["packets"])
        lines.append("Approve once to write the packets to a NEW private folder (the full values are in the files):")
        lines.append(f"  reachweaver account-kit --persona {persona} {platform_flags} --out NEW_DIR "
                     f"--approve-plan {plan_['plan_sha256'][:MIN_APPROVAL_CHARS]}")
    lines.append("ReachWeaver creates no account, fills no form and sees no password, CAPTCHA or code: you do "
                 "those steps yourself.")
    return "\n".join(lines) + "\n"


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def _render_signup(packet: dict) -> str:
    lines = [f"# Create your {packet['persona']} account on {packet['label']}", "",
             "> This file contains personal data. Delete this folder when you are done.", "",
             f"Official page, which you open yourself: {packet['starting_url']}",
             f"Interface language: {packet['ui_locale']}. The platform's form decides what it really asks for; "
             "the values below are what you may need.", ""]
    if packet["region_note"]:
        lines += [f"**Note:** {packet['region_note']}", ""]
    lines += ["## Values to paste", ""]
    for key, value in packet["fields"].items():
        if key in ("bio_language", "interface_language"):
            continue
        if key == "username_candidates":
            lines.append("- **username** (try them in this order; the form tells you which are free):")
            lines += [f"  {i}. `{name}`" for i, name in enumerate(value, 1)]
        elif key == "bio":
            lines += ["- **bio**:", "", "```text", str(value), "```", ""]
        else:
            lines.append(f"- **{key}**: `{value}`")
    lines += ["", "## Steps that stay with you", ""]
    lines += [f"{i}. {step}" for i, step in enumerate(packet["human_steps"], 1)]
    if packet["glossary"]:
        lines += ["", "## Form labels", "", "Word list by ReachWeaver, not from the platform; have a fluent reader "
                  "confirm a label you rely on.", "", "| English | Label |", "|---|---|"]
        lines += [f"| {row['en']} | {row['local']} |" for row in packet["glossary"]]
    lines += ["", "## After the account exists", "", f"    {packet['after']}", ""]
    return "\n".join(lines)


def write_plan(plan_: dict, out_dir: str | Path, approved_sha256: str) -> list[str]:
    """Write the packets to a NEW private folder. ``approved_sha256`` (first 12+ hex) is the single approval."""
    if not plan_.get("ready"):
        raise ValidationError(f"Nothing written: {len(plan_.get('questions', []))} question(s) are still open")
    if _plan_hash(plan_["persona"], plan_["packets"]) != plan_.get("plan_sha256"):
        raise ValidationError("Nothing written: the plan does not match its own hash")
    given = (approved_sha256 or "").strip().lower()
    if len(given) < MIN_APPROVAL_CHARS or not re.fullmatch(r"[0-9a-f]+", given) \
            or not plan_["plan_sha256"].startswith(given):
        raise ValidationError(f"Nothing written: --approve-plan must be the first {MIN_APPROVAL_CHARS}+ characters "
                              "of the plan sha256 you reviewed")
    out = Path(out_dir)
    if not out.parent.is_dir():
        raise ValidationError("Nothing written: the parent folder of --out does not exist")
    if out.exists():
        raise ValidationError("Nothing written: --out must be a NEW folder")
    os.mkdir(out, 0o700)
    os.chmod(out, 0o700)
    written: list[str] = []
    try:
        _write_private(out / "plan.json", json.dumps(plan_, indent=2, ensure_ascii=False) + "\n")
        written.append("plan.json")
        readme = ["# Account kit", "", "> This folder contains personal data. Delete it when you are done.", "",
                  f"Persona: {plan_['persona']}. Plan sha256: {plan_['plan_sha256']}", "",
                  "ReachWeaver created no account and sent nothing. Each sub-folder has a SIGNUP.md to follow:", ""]
        for packet in plan_["packets"]:
            folder = out / packet["platform"]
            os.mkdir(folder, 0o700)
            os.chmod(folder, 0o700)
            _write_private(folder / "SIGNUP.md", _render_signup(packet))
            _write_private(folder / "fields.json", json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
            written += [f"{packet['platform']}/SIGNUP.md", f"{packet['platform']}/fields.json"]
            readme.append(f"- {packet['label']}: {packet['platform']}/SIGNUP.md")
        _write_private(out / "README.md", "\n".join(readme) + "\n")
        written.append("README.md")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return written
