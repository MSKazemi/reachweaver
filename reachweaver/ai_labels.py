"""``ai-label-check``: which AI-content label each target platform asks for, per draft (offline, advisory).

The rules come from ``ai_disclosure_registry.json``: one entry per platform (YouTube, Instagram, Facebook, Threads,
TikTok, LinkedIn, X), the EU AI Act Article 50 transparency duty for deployers, and a pointer to the Chinese
labelling rule already sourced in ``china.py``. Every entry cites the page it was read from, an exact quote and
the date it was checked; ``validate_registry`` keeps the file honest and ``tests/test_ai_labels.py`` pins it.

The command reads a draft's ``authorship`` (``human`` / ``ai_assisted`` / ``ai_generated``, from the draft schema
or ``--authorship``) and its media flags: the optional media-manifest key ``realistic_synthetic`` and
``provenance.created_by``. For each target platform it answers ``required``, ``recommended``, ``not_required`` or
``unknown``, says what to tick in the platform's own composer, and cites the source.

It is advisory only. It never blocks, approves, schedules or sends anything (``SEND_PATH = None``); the database
is opened read-only and only for ``--post-id``; no network request, model call or credential read. The approval
precondition for posts with media (owner-approved 2026-10-21) lives in ``ai_label_gate.py`` and calls ``assess``
with the post's bound declarations; this command itself still blocks nothing. The EU wording is read from a
secondary site and nothing here is legal advice.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from .policy import ValidationError

SEND_PATH = None  # Asserted in tests: this module can never send, upload, approve or schedule.
REGISTRY_FILE = Path(__file__).with_name("ai_disclosure_registry.json")
SCHEMA = "reachweaver.ai-disclosure.v1"
DECISIONS = ("required", "unknown", "recommended", "not_required")  # most to least pressing
AUTHORSHIP = ("human", "ai_assisted", "ai_generated")
MEDIA_KINDS = ("image", "video")
SOURCE_KINDS = ("official", "official_newsroom", "official_api", "secondary", "unfetched")
MAX_INPUT_BYTES = 1_000_000
MAX_TARGETS = 50
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PLATFORM_KEYS = ("name", "aliases", "verification", "when_required", "realistic_media", "realistic_media_source",
                  "non_realistic_media", "non_realistic_media_source", "ai_text", "ai_text_basis", "ai_text_source",
                  "native_control", "native_control_verified", "native_control_source", "api_field",
                  "api_field_source", "reads_c2pa", "c2pa_source", "consequences", "consequences_source")
_EU_SOURCES = ("applies_from_source", "text_rule_source", "text_exemption_source", "deep_fake_source",
               "artistic_source", "personal_use_source", "timing_source")
NOT_LEGAL_ADVICE = ("Advisory only, not legal advice. Platform rules and laws change quickly: re-verify the cited "
                    "page before relying on it. ReachWeaver cannot see how a file was made; the owner decides.")


# ---- registry -------------------------------------------------------------------------------------------------

def validate_registry(data: object) -> list[str]:
    """Every problem with a registry document; an empty list means it is usable."""
    if not isinstance(data, dict):
        return ["registry must be a JSON object"]
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA!r}")
    if not isinstance(data.get("checked_on"), str) or not _DATE.match(data["checked_on"]):
        errors.append("checked_on must be YYYY-MM-DD")
    if "not legal advice" not in str(data.get("label", "")):
        errors.append("label must say it is not legal advice")
    if set(data.get("decisions") or {}) != set(DECISIONS):
        errors.append(f"decisions must define exactly {', '.join(DECISIONS)}")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        return errors + ["sources must be a non-empty object"]
    for sid, src in sources.items():
        where = f"sources.{sid}"
        if not _ID.match(str(sid)) or not isinstance(src, dict):
            errors.append(f"{where}: bad id or not an object")
            continue
        if not str(src.get("url", "")).startswith("https://"):
            errors.append(f"{where}.url must be an https URL")
        if src.get("kind") not in SOURCE_KINDS:
            errors.append(f"{where}.kind must be one of {', '.join(SOURCE_KINDS)}")
        if not isinstance(src.get("checked_on"), str) or not _DATE.match(src["checked_on"]):
            errors.append(f"{where}.checked_on must be YYYY-MM-DD")
        quote = src.get("quote")
        if src.get("kind") == "unfetched":
            if quote:
                errors.append(f"{where}: an unfetched source cannot carry a quote")
            if not src.get("note"):
                errors.append(f"{where}: an unfetched source needs a note saying why")
        elif not isinstance(quote, str) or len(quote.strip()) < 10:
            errors.append(f"{where}.quote must be the exact text read on the page")

    def cited(where: str, ids: object, *, decision: object = None) -> None:
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            errors.append(f"{where} must be a list of source ids")
            return
        for sid in ids:
            if sid not in sources:
                errors.append(f"{where}: unknown source {sid!r}")
        if decision not in (None, "unknown") and not any(
                sid in sources and sources[sid].get("kind") != "unfetched" for sid in ids):
            errors.append(f"{where}: decision {decision!r} needs at least one source that was actually read")

    platforms = data.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        return errors + ["platforms must be a non-empty object"]
    seen: dict[str, str] = {}
    for key, entry in platforms.items():
        where = f"platforms.{key}"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be an object")
            continue
        missing = [k for k in _PLATFORM_KEYS if k not in entry]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        for name in [key, *entry["aliases"]]:
            if name in seen:
                errors.append(f"{where}: id or alias {name!r} also used by {seen[name]}")
            seen[name] = key
        if entry["verification"] not in ("verified", "unverified"):
            errors.append(f"{where}.verification must be verified or unverified")
        realistic = entry["realistic_media"]
        if not isinstance(realistic, dict) or set(realistic) != set(MEDIA_KINDS):
            errors.append(f"{where}.realistic_media must give a decision for {', '.join(MEDIA_KINDS)}")
            realistic = {}
        decisions = [*realistic.values(), entry["non_realistic_media"], entry["ai_text"]]
        if any(d not in DECISIONS for d in decisions):
            errors.append(f"{where}: every decision must be one of {', '.join(DECISIONS)}")
        if entry["verification"] == "unverified" and any(d != "unknown" for d in decisions):
            errors.append(f"{where}: an unverified platform can only answer unknown")
        worst = min(realistic.values(), key=DECISIONS.index, default=None) if realistic else None
        cited(f"{where}.realistic_media_source", entry["realistic_media_source"], decision=worst)
        cited(f"{where}.non_realistic_media_source", entry["non_realistic_media_source"],
              decision=entry["non_realistic_media"])
        cited(f"{where}.ai_text_source", entry["ai_text_source"], decision=entry["ai_text"])
        for field, src_field in (("native_control", "native_control_source"), ("api_field", "api_field_source"),
                                 ("consequences", "consequences_source")):
            cited(f"{where}.{src_field}", entry[src_field])
            if entry[field] is not None and not entry[src_field]:
                errors.append(f"{where}.{field} needs a source")
        if entry["native_control_verified"] is True and not entry["native_control_source"]:
            errors.append(f"{where}.native_control_verified needs a source")
        cited(f"{where}.c2pa_source", entry["c2pa_source"])
        if entry["reads_c2pa"] not in (True, False, None) or (entry["reads_c2pa"] is not None
                                                                and not entry["c2pa_source"]):
            errors.append(f"{where}.reads_c2pa must be true/false with a source, or null")
    juris = data.get("jurisdictions")
    if not isinstance(juris, dict):
        return errors + ["jurisdictions must be an object"]
    eu = juris.get("eu_ai_act_art50")
    if not isinstance(eu, dict):
        errors.append("jurisdictions.eu_ai_act_art50 is required")
    else:
        if not isinstance(eu.get("applies_from"), str) or not _DATE.match(eu["applies_from"]):
            errors.append("jurisdictions.eu_ai_act_art50.applies_from must be YYYY-MM-DD")
        if "not legal advice" not in str(eu.get("disclaimer", "")):
            errors.append("jurisdictions.eu_ai_act_art50.disclaimer must say it is not legal advice")
        for field in _EU_SOURCES:
            cited(f"jurisdictions.eu_ai_act_art50.{field}", eu.get(field), decision="required")
            for sid in eu.get(field) or []:
                if sid in sources and sources[sid].get("kind") != "secondary":
                    errors.append(f"jurisdictions.eu_ai_act_art50.{field}: {sid} must be labelled secondary")
    china = juris.get("china")
    if not isinstance(china, dict) or china.get("module") != "reachweaver/china.py" or \
            china.get("rule") != "LEGAL.ai_label":
        errors.append("jurisdictions.china must point at reachweaver/china.py LEGAL.ai_label")
    return errors


def load_registry(path: Path | None = None) -> dict:
    data = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    errors = validate_registry(data)
    if errors:
        raise ValidationError("AI disclosure registry is invalid: " + "; ".join(errors[:5]))
    return data


def _source(registry: dict, ids: list[str]) -> list[dict]:
    out = []
    for sid in ids:
        src = registry["sources"][sid]
        out.append({"id": sid, "url": src["url"], "kind": src["kind"], "quote": src["quote"],
                    "checked_on": src["checked_on"], **({"note": src["note"]} if src.get("note") else {})})
    return out


def resolve_platform(registry: dict, platform: str) -> tuple[str | None, str]:
    """(registry key, family) for a platform id: family is ``platform``, ``china`` or ``none``."""
    from .china import PROFILES
    name = platform.strip().lower()
    for key, entry in registry["platforms"].items():
        if name == key or name in entry["aliases"]:
            return key, "platform"
    if name in PROFILES:
        return name, "china"
    return None, "none"


# ---- inputs ---------------------------------------------------------------------------------------------------

def _tri(value: object, where: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValidationError(f"{where} must be true or false when present")
    return value


def media_from_manifest(manifest: object) -> list[dict]:
    """Image/video assets of a media manifest, reduced to what decides a label. Subtitles are skipped."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), list):
        raise ValidationError("media manifest must be a JSON object with an assets list")
    items = []
    for idx, asset in enumerate(manifest["assets"]):
        if not isinstance(asset, dict):
            raise ValidationError(f"assets[{idx}] must be an object")
        kind = asset.get("kind")
        if kind not in MEDIA_KINDS:
            continue
        raw_provenance = asset.get("provenance")
        provenance: dict = raw_provenance if isinstance(raw_provenance, dict) else {}
        targets = asset.get("targets", [])
        items.append({"id": str(asset.get("id") or f"assets[{idx}]"), "kind": kind,
                      "realistic_synthetic": _tri(asset.get("realistic_synthetic"), f"assets[{idx}].realistic_synthetic"),
                      "created_by": provenance.get("created_by") if isinstance(provenance.get("created_by"), str) else None,
                      "targets": [t for t in targets if isinstance(t, str)] if isinstance(targets, list) else []})
    return items


def media_from_post(row: Any) -> list[dict]:
    """Media stored on a post (``media_json`` images, ``video_json``) with the owner's ``realistic_synthetic``
    answers recorded by ``ai-declare`` (``posts.realistic_synthetic_json``, keyed by attachment SHA-256; absent or
    unreadable means no answer). A malformed media or video record raises ``ValidationError``."""
    from .attachments import decode_media
    from .video import decode_video
    keys = row.keys()
    realism: dict = {}
    if "realistic_synthetic_json" in keys and row["realistic_synthetic_json"]:
        try:
            parsed = json.loads(str(row["realistic_synthetic_json"]))
        except ValueError:
            parsed = {}
        realism = {k: v for k, v in parsed.items() if isinstance(v, bool)} if isinstance(parsed, dict) else {}
    items: list[dict] = []
    if "media_json" in keys and row["media_json"]:
        for idx, image in enumerate(decode_media(row["media_json"])):
            provenance = (image.get("rights") or {}).get("provenance") or {}
            items.append({"id": f"image {idx + 1}", "kind": "image", "sha256": image.get("sha256"),
                          "realistic_synthetic": realism.get(image.get("sha256")),
                          "created_by": provenance.get("created_by"), "targets": []})
    if "video_json" in keys and row["video_json"]:
        video = decode_video(row["video_json"])
        if video:
            items.append({"id": "video", "kind": "video", "sha256": video.get("sha256"),
                          "realistic_synthetic": realism.get(video.get("sha256")), "created_by": None, "targets": []})
    return items


def _authorship(value: object, where: str) -> str | None:
    if value is None:
        return None
    if value not in AUTHORSHIP:
        raise ValidationError(f"{where} must be one of {', '.join(AUTHORSHIP)}")
    return str(value)


def items_from_document(doc: object) -> list[dict]:
    """Drafts to check from a draft, variant or campaign JSON: [{id, platforms, authorship}]."""
    if not isinstance(doc, dict):
        raise ValidationError("the --file JSON must be an object (a draft, a variant or a campaign)")
    top = _authorship(doc.get("authorship"), "authorship")
    if isinstance(doc.get("variants"), list):
        items = []
        for idx, variant in enumerate(doc["variants"]):
            if not isinstance(variant, dict):
                raise ValidationError(f"variants[{idx}] must be an object")
            platform = variant.get("platform")
            items.append({"id": str(variant.get("id") or f"variants[{idx}]"),
                          "platforms": [platform] if isinstance(platform, str) and platform else [],
                          "authorship": _authorship(variant.get("authorship"), f"variants[{idx}].authorship") or top})
        return items
    raw_platforms = doc.get("platforms")
    platforms: list = raw_platforms if isinstance(raw_platforms, list) else (
        [doc["platform"]] if isinstance(doc.get("platform"), str) else [])
    return [{"id": str(doc.get("id") or "draft"), "platforms": [p for p in platforms if isinstance(p, str) and p],
             "authorship": top}]


# ---- assessment -----------------------------------------------------------------------------------------------

def _worst(decisions: list[str]) -> str:
    return min(decisions, key=DECISIONS.index) if decisions else "not_required"


def _applies(item: dict, platform: str, key: str | None, aliases: list[str]) -> bool:
    if not item["targets"]:
        return True
    names = {platform, *(n for n in ([key] if key else []) + aliases)}
    return any(t in names or any(t.startswith(n + "_") for n in names) for t in item["targets"])


def _reason(rule: str, decision: str, why: str, sources: list[dict]) -> dict:
    return {"rule": rule, "decision": decision, "why": why, "sources": sources}


def _platform_reasons(registry: dict, key: str, authorship: str, media: list[dict]) -> list[dict]:
    entry = registry["platforms"][key]
    name = entry["name"]
    if entry["verification"] != "verified":
        return [_reason("platform", "unknown", f"{name}: {entry['when_required']}",
                        _source(registry, entry["realistic_media_source"]))]
    reasons = []
    for item in media:
        flag, creator = item["realistic_synthetic"], item["created_by"]
        if flag is True:
            decision = entry["realistic_media"][item["kind"]]
            why = f"{item['id']} ({item['kind']}) is marked realistic_synthetic. {name}: {entry['when_required']}"
            if entry.get("realistic_media_note"):
                why += " " + entry["realistic_media_note"]
            reasons.append(_reason("platform", decision, why, _source(registry, entry["realistic_media_source"])))
        elif flag is False:
            reasons.append(_reason("platform", entry["non_realistic_media"],
                                   f"{item['id']} ({item['kind']}) is marked not realistic_synthetic.",
                                   _source(registry, entry["non_realistic_media_source"])))
        elif creator == "owner":
            reasons.append(_reason("platform", "not_required",
                                   f"{item['id']} ({item['kind']}) is recorded as made by the owner, with no "
                                   "realistic_synthetic flag.", []))
        else:
            made = "made or edited with a tool" if creator == "tool" else (
                "third-party media" if creator == "third_party" else "media with no recorded provenance")
            reasons.append(_reason("platform", "unknown",
                                   f"{item['id']} ({item['kind']}) is {made}; ReachWeaver cannot tell whether it is "
                                   "realistic. Set realistic_synthetic true or false for it in the media manifest.",
                                   _source(registry, entry["realistic_media_source"])))
    if authorship in ("ai_assisted", "ai_generated"):
        reasons.append(_reason("platform", entry["ai_text"], f"Text is {authorship}. {entry['ai_text_basis']}",
                               _source(registry, entry["ai_text_source"])))
    if not reasons:
        reasons.append(_reason("platform", "not_required",
                               f"{name}'s cited rule is about realistic AI-made media; this draft has none.",
                               _source(registry, entry["realistic_media_source"])))
    return reasons


def _china_reasons(authorship: str, media: list[dict]) -> list[dict]:
    from .china import CHECKED, LEGAL
    rule = LEGAL["ai_label"]
    src = [{"id": "china.LEGAL.ai_label", "url": rule["source"], "kind": "official", "quote": rule["quote"],
            "checked_on": CHECKED, "note": rule["translation"]}]
    ai_media = [m for m in media if m["realistic_synthetic"] is True or m["created_by"] == "tool"]
    if authorship == "ai_generated" or ai_media:
        what = "the text is ai_generated" if authorship == "ai_generated" else \
            f"{', '.join(m['id'] for m in ai_media)} is AI-made or tool-made"
        return [_reason("china", "required", f"{what}. {rule['value']} (in force since 2025-09-01).", src)]
    unclear = [m for m in media if m["realistic_synthetic"] is None and m["created_by"] != "owner"]
    if authorship in ("ai_assisted", "unknown") or unclear:
        return [_reason("china", "unknown", "China's rule covers AI-generated or synthesized content; ReachWeaver "
                        "cannot tell how much of this draft that is. Decide yourself; nothing is pre-selected.", src)]
    return [_reason("china", "not_required", "Human-written text and no AI-made media.", src)]


def _eu_reasons(registry: dict, authorship: str, media: list[dict], *, public_interest: str | None,
                editorial_review: str | None, today: date) -> list[dict]:
    eu = registry["jurisdictions"]["eu_ai_act_art50"]
    s = lambda field: _source(registry, eu[field])  # noqa: E731
    reasons = []
    pending = today < date.fromisoformat(eu["applies_from"])
    lead = (f"Applies from {eu['applies_from']} (not yet in force on {today.isoformat()}). " if pending else "")
    if authorship in ("ai_generated", "ai_assisted", "unknown"):
        if public_interest == "no":
            reasons.append(_reason("eu_ai_act_art50", "not_required", lead + "You said the text does not inform the "
                                   "public on matters of public interest; the Art. 50(4) text duty covers only that.",
                                   s("text_rule_source")))
        elif public_interest is None:
            reasons.append(_reason("eu_ai_act_art50", "unknown", lead + "AI text published to inform the public on "
                                   "matters of public interest must be disclosed unless a person reviewed it and holds "
                                   "editorial responsibility. Answer --public-interest yes|no.",
                                   s("text_rule_source") + s("text_exemption_source")))
        elif editorial_review == "yes":
            reasons.append(_reason("eu_ai_act_art50", "not_required", lead + "Exemption: you said the text had human "
                                   "review or editorial control and a person holds editorial responsibility.",
                                   s("text_exemption_source")))
        elif editorial_review is None:
            reasons.append(_reason("eu_ai_act_art50", "unknown", lead + "AI text on a matter of public interest must "
                                   "be disclosed unless a person reviewed or edited it and holds editorial "
                                   "responsibility for it. Answer --editorial-review yes|no.",
                                   s("text_rule_source") + s("text_exemption_source")))
        elif authorship == "ai_generated":
            reasons.append(_reason("eu_ai_act_art50", "required", lead + "AI-generated text on a matter of public "
                                   "interest without human review or editorial control: disclose that it was "
                                   "artificially generated, clearly and at the first exposure.",
                                   s("text_rule_source") + s("timing_source")))
        else:
            what = "AI-assisted" if authorship == "ai_assisted" else "Text of unrecorded authorship"
            reasons.append(_reason("eu_ai_act_art50", "unknown", lead + f"{what} on a matter of public interest "
                                   "without editorial review: whether it counts as 'artificially generated or "
                                   "manipulated' text is a legal question. Pass --authorship, or disclose to be safe.",
                                   s("text_rule_source")))
    fakes = [m for m in media if m["realistic_synthetic"] is True]
    unclear = [m for m in media if m["realistic_synthetic"] is None and m["created_by"] != "owner"]
    if fakes:
        reasons.append(_reason("eu_ai_act_art50", "required", lead + f"{', '.join(m['id'] for m in fakes)} is "
                               "realistic synthetic media (a possible deep fake): disclose that it was artificially "
                               "generated or manipulated. For an evidently artistic, satirical or fictional work a "
                               "lighter disclosure applies. Personal non-professional use is outside 'deployer'.",
                               s("deep_fake_source") + s("artistic_source") + s("personal_use_source")))
    elif unclear:
        reasons.append(_reason("eu_ai_act_art50", "unknown", lead + "Media of unknown realism: if it could pass as an "
                               "authentic image or video of real people, places or events, it is a deep fake that "
                               "must be disclosed.", s("deep_fake_source")))
    return reasons


def assess(targets: list[dict], media: list[dict], *, registry: dict | None = None, eu: bool = False,
           public_interest: str | None = None, editorial_review: str | None = None,
           today: date | None = None) -> dict:
    """Per-target label advice. ``targets``: [{id, platform, authorship}] with authorship possibly None."""
    registry = registry or load_registry()
    today = today or date.today()
    results = []
    for target in targets:
        platform, authorship = target["platform"].strip().lower(), target["authorship"] or "unknown"
        key, family = resolve_platform(registry, platform)
        entry = registry["platforms"].get(key) if family == "platform" and key else None
        applicable = [m for m in media if _applies(m, platform, key, entry["aliases"] if entry else [])]
        if family == "platform":
            reasons = _platform_reasons(registry, str(key), authorship, applicable)
        elif family == "china":
            reasons = _china_reasons(authorship, applicable)
        else:
            reasons = [_reason("platform", "unknown", f"No AI-label rule is recorded for {platform!r} in the "
                               "registry; check the platform's own rules.", [])]
        platform_decision = _worst([r["decision"] for r in reasons])
        eu_decision = None
        if eu:
            eu_reasons = _eu_reasons(registry, authorship, applicable, public_interest=public_interest,
                                     editorial_review=editorial_review, today=today)
            eu_decision = _worst([r["decision"] for r in eu_reasons]) if eu_reasons else "not_required"
            reasons += eu_reasons
        decision = _worst([r["decision"] for r in reasons])
        results.append({
            "id": target["id"], "platform": platform, "registry_key": key, "family": family,
            "authorship": authorship, "media_checked": [m["id"] for m in applicable], "decision": decision,
            "platform_decision": platform_decision, "eu_decision": eu_decision,
            "what_to_tick": _what_to_tick(registry, family, key, platform_decision, eu_decision),
            "api_field": entry["api_field"] if entry else None,
            "reads_c2pa": entry["reads_c2pa"] if entry else None,
            "consequences": entry["consequences"] if entry else None,
            "reasons": reasons,
        })
    eu_entry = registry["jurisdictions"]["eu_ai_act_art50"]
    return {
        "schema": "reachweaver.ai-label-check.v1", "advisory": True, "registry_checked_on": registry["checked_on"],
        "eu_ai_act_art50": ({"evaluated": True, "applies_from": eu_entry["applies_from"],
                             "disclaimer": eu_entry["disclaimer"]} if eu else
                            {"evaluated": False, "hint": "Add --eu if you publish from, or for an audience in, "
                                                         "the EU (Art. 50 applies from "
                                                         f"{eu_entry['applies_from']})."}),
        "targets": results,
        "counts": {d: sum(1 for r in results if r["decision"] == d) for d in DECISIONS},
        "note": NOT_LEGAL_ADVICE,
    }


def _native_control(registry: dict, family: str, key: str | None) -> str:
    if family == "platform" and key:
        control = registry["platforms"][key]["native_control"]
        return control or "No verified native control recorded; say it plainly in the post text."
    if family == "china" and key:
        from .china import PROFILES
        native = {k: label for k, label, _ in PROFILES[key].get("declarations", [])}
        if "ai" in native:
            return f"The composer's own AI declaration: {native['ai']}"
        return "The composer's own AI label if it has one; otherwise say so prominently in the post."
    return "Say it plainly in the post text; no platform control is recorded."


def _what_to_tick(registry: dict, family: str, key: str | None, platform_decision: str,
                  eu_decision: str | None) -> str:
    parts = []
    if platform_decision != "not_required":
        prefix = "If you decide it applies: " if platform_decision == "unknown" else ""
        parts.append(prefix + _native_control(registry, family, key))
    if eu_decision in ("required", "unknown"):
        prefix = "If you decide it applies: " if eu_decision == "unknown" else ""
        parts.append(prefix + "say in the post itself, clearly and up front, that it was AI-generated "
                     "(EU AI Act Art. 50)")
    if not parts:
        return "Nothing: the cited rules do not ask for a label here (you may still add one)."
    return "; ".join(parts)


# ---- command --------------------------------------------------------------------------------------------------

def _read_json(path: Path, what: str) -> object:
    from .inputs import read_json_file
    if not path.is_file():
        raise ValidationError(f"{what} {path} does not exist")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValidationError(f"{what} {path} is larger than {MAX_INPUT_BYTES} bytes")
    try:
        return read_json_file(path, f"{what} {path.name}")
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{what} {path.name} is not valid JSON: {exc.msg}") from None


def run_command(args: argparse.Namespace) -> int:
    from .inputs import split_list_args
    registry = load_registry()
    override = _authorship(args.authorship, "--authorship")
    platforms = split_list_args(args.platform or [])
    if args.post_id is not None:
        from .storage import connect_readonly, database_path
        with connect_readonly(database_path(args.db)) as con:
            row = con.execute("SELECT * FROM posts WHERE id=?", (args.post_id,)).fetchone()
            if row is None:
                raise ValidationError(f"unknown post {args.post_id!r}")
            stored_media = media_from_post(row)
            stored_authorship = row["authorship"] if "authorship" in row.keys() else None
        items = [{"id": args.post_id, "platforms": platforms or [row["platform"]],
                  "authorship": override or (stored_authorship if stored_authorship in AUTHORSHIP else None)}]
    else:
        items = items_from_document(_read_json(args.file, "draft file"))
        stored_media = []
        for item in items:
            item["authorship"] = override or item["authorship"]
            if platforms:
                item["platforms"] = platforms
    media = media_from_manifest(_read_json(args.media, "media manifest")) if args.media else stored_media
    targets = [{"id": item["id"], "platform": p, "authorship": item["authorship"]}
               for item in items for p in item["platforms"]]
    if not targets:
        raise ValidationError("no target platform: name one with --platform, or give the draft a platform")
    if len(targets) > MAX_TARGETS:
        raise ValidationError(f"at most {MAX_TARGETS} draft/platform pairs per check")
    report = assess(targets, media, registry=registry, eu=args.eu, public_interest=args.public_interest,
                    editorial_review=args.editorial_review)
    if args.post_id is not None and override is None and items[0]["authorship"] is None:
        report["authorship_note"] = ("No authorship is recorded for this post; record it with reachweaver ai-declare "
                                     f"{args.post_id} --authorship human|ai_assisted|ai_generated, or pass --authorship "
                                     "here for a one-off answer.")
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return 0


LABELS = {"required": "LABEL REQUIRED", "recommended": "label recommended", "not_required": "not required",
          "unknown": "unknown"}


def render_text(report: dict) -> str:
    lines = [f"AI content labels (advisory; rules checked {report['registry_checked_on']})", ""]
    for target in report["targets"]:
        lines.append(f"{target['id']} -> {target['platform']}: {LABELS[target['decision']]}"
                     f" (authorship: {target['authorship']})")
        lines.append(f"  Tick: {target['what_to_tick']}")
        if target["api_field"] and target["decision"] != "not_required":
            lines.append(f"  API field (for reference; ReachWeaver sends nothing here): {target['api_field']}")
        if target["reads_c2pa"]:
            lines.append("  Reads C2PA Content Credentials: yes (it may label the post itself)")
        if target["consequences"] and target["platform_decision"] in ("required", "recommended", "unknown"):
            lines.append(f"  If not labelled: {target['consequences']}")
        for reason in target["reasons"]:
            lines.append(f"  - [{reason['rule']}] {LABELS[reason['decision']]}: {reason['why']}")
            for url in dict.fromkeys(src["url"] for src in reason["sources"]):
                src = next(s for s in reason["sources"] if s["url"] == url)
                tag = " (secondary source, not legal advice)" if src["kind"] == "secondary" else ""
                lines.append(f"      source{tag}: {url} (checked {src['checked_on']})")
        lines.append("")
    eu = report["eu_ai_act_art50"]
    lines.append(eu["disclaimer"] if eu["evaluated"] else eu["hint"])
    if report.get("authorship_note"):
        lines.append(report["authorship_note"])
    lines.append(report["note"])
    return "\n".join(lines)
