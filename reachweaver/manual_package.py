"""Generic manual packages for every other ``manual_only`` destination (private #44, item C2).

``manual_export`` has sourced, platform-specific packages for five platforms. This module covers
every other destination that ``distribution_registry.json`` marks ``reachweaver_delivery:
manual_only`` (DEV, X, Product Hunt, Reddit, Hacker News, ...). It knows nothing about those
platforms beyond what the registries already record, so it invents no limit: a text or media
limit that no registry field records is reported as ``LIMIT_UNKNOWN``.

Destinations the registry marks ``human_only`` never get agent-written post text. Their package
is a notes form and an explicit "write this yourself" line; a variant ``text`` or ``title`` for
them is a blocking error.

Nothing here sends, uploads, schedules or shares anything, and it adds no delivery mode. There is
no send path (``SEND_PATH = None``), no network import and no outbox or database write. Every
package carries ``status = "manual_export_not_published"`` and never claims that a post exists.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from . import china
from .campaigns import _sensitive_keys, public_url
from .capabilities import load_registry as load_capability_registry
from .distribution import load_registry as load_distribution_registry
from .manual_export import (MANUAL_PLATFORMS, STATUS, _check_text_common, extract_hashtags, extract_links,
                            extract_mentions, utf16_length)
from .media import constraint_table, read_manifest, validate_media_manifest
from .policy import ValidationError
from .privacy import write_private_file
from .storage import utc_now

PACKAGE_KIND = "reachweaver.manual-package.v1"
SEND_PATH = None  # Asserted in tests: this module can never send, upload or share.
LIMIT_UNKNOWN = "limit unknown — check the platform"
WRITE_IT_YOURSELF = "Write this yourself."
# Registry ids that already have a dedicated, sourced manual_export package.
DEDICATED = {"facebook_pages": "facebook", "instagram": "instagram", "threads": "threads",
             "tiktok": "tiktok", "youtube": "youtube"}
# Distribution-registry ids that the capability registry and media.py name differently.
CAPABILITY_ID = {"dev": "devto"}
MEDIA_TARGETS = {"dev": ("dev", "devto")}
FORMATS = ("text", "link", "image", "video", "carousel")
VARIANT_FIELDS = frozenset({"schema_version", "type", "id", "draft_id", "brand_id", "campaign_id", "platform",
                            "account_id", "locale", "format", "text", "link_url", "platform_options",
                            "evidence_ids", "created_at", "media", "utm", "talking_points"})
ACCOUNT_META_FIELDS = frozenset({"account_id", "platform", "handle", "display_name", "account_type", "notes"})
# ReachWeaver's own cap on human-only notes (not a platform limit): notes, never a ready-made post.
MAX_TALKING_POINTS = 10
MAX_TALKING_POINT_CHARS = 200
DEV_AI_GUIDELINES = "https://dev.to/guidelines-for-ai-assisted-articles-on-dev/"


def _entries() -> dict[str, dict]:
    return {p["id"]: p for p in load_distribution_registry()["platforms"]}


def generic_platforms() -> tuple[str, ...]:
    """Every manual_only registry id that has no dedicated manual_export package, sorted."""
    return tuple(sorted(key for key, p in _entries().items()
                        if p["reachweaver_delivery"] == "manual_only" and key not in DEDICATED))


def _destination(platform: str, copy_handoff: bool = False) -> dict:
    """The registry entry for a generic package destination.

    ``copy_handoff`` (used only by ``share``, owner decision 2026-10-21) also accepts a text network that
    ReachWeaver can send to through its API (``mock_tested_text``), so a creator without a connected account
    still gets text to copy and post by hand. The package stays a manual handoff: no send path, no queue.
    Human-only destinations are never ``mock_tested_text``, so their notes-only rule is unchanged.
    """
    if platform in MANUAL_PLATFORMS or platform in DEDICATED:
        raise ValidationError(f"{platform!r} has a dedicated package: use manual-export --platform "
                              f"{DEDICATED.get(platform, platform)}")
    entry = _entries().get(platform)
    if entry is None:
        raise ValidationError(f"{platform!r} is not in the distribution registry")
    if copy_handoff and entry["reachweaver_delivery"] == "mock_tested_text" and entry["human_only"] is not True:
        return entry
    if entry["reachweaver_delivery"] != "manual_only":
        raise ValidationError(f"{platform!r} is {entry['reachweaver_delivery']!r}, not manual_only, in the "
                              "distribution registry; the generic manual package covers manual_only only")
    return entry


def _options(variant: dict) -> dict:
    options = variant.get("platform_options", {})
    return options if isinstance(options, dict) else {}


# --- Preflight ---------------------------------------------------------------------------

def package_preflight(platform: str, variant: object, account_meta: dict | None = None, *,
                      copy_handoff: bool = False) -> tuple[list[str], list[str]]:
    """Return (blocking errors, warnings) for one generic manual package. Offline and pure."""
    try:
        entry = _destination(platform, copy_handoff)
    except ValidationError as exc:
        return [str(exc)], []
    if not isinstance(variant, dict):
        return ["variant must be a JSON object"], []
    label, human_only = entry["label"], entry["human_only"] is True
    errors = _sensitive_keys(variant, "variant")
    warnings: list[str] = []
    unknown = sorted(set(variant) - VARIANT_FIELDS)
    if unknown:
        warnings.append(f"variant has unrecognized fields (ignored): {', '.join(unknown)}")
    if "platform" in variant and variant["platform"] != platform:
        errors.append(f"variant.platform is {variant['platform']!r} but --platform is {platform!r}")
    if "media" in variant:
        warnings.append("variant.media is ignored; supply real files through a media manifest (--media)")
    fmt = variant.get("format", "text")
    if fmt not in FORMATS:
        errors.append(f"variant.format {fmt!r} is not one of {', '.join(FORMATS)}")
    if "link_url" in variant and not public_url(variant["link_url"]):
        errors.append("variant.link_url must be a public HTTPS URL without credentials")
    raw_options = variant.get("platform_options", {})
    if not isinstance(raw_options, dict):
        errors.append("variant.platform_options must be an object")
    options = _options(variant)
    china_profile = china.is_china(platform) and not human_only
    for key in sorted(set(options) - ({"title", "topics"} if china_profile else {"title"})):
        warnings.append(f"platform_options.{key} is not used by the generic package (ignored)")
    text, title, points = variant.get("text"), options.get("title"), variant.get("talking_points")
    if human_only:
        for name, value in (("variant.text", text), ("platform_options.title", title)):
            if value is not None:
                errors.append(f"{name} is not accepted: {label} is human-only, so ReachWeaver packages no "
                              "agent-written post text for it. Remove it, put short notes in talking_points "
                              "and write the post yourself")
        if points is not None:
            if not isinstance(points, list) or not all(isinstance(p, str) and p.strip() for p in points):
                errors.append("variant.talking_points must be a list of non-empty strings")
            else:
                if len(points) > MAX_TALKING_POINTS:
                    errors.append(f"variant.talking_points has {len(points)} items; ReachWeaver allows at most "
                                  f"{MAX_TALKING_POINTS} short notes for a human-only destination")
                for index, point in enumerate(points):
                    _check_text_common(point, errors, f"talking_points[{index}]")
                    if len(point) > MAX_TALKING_POINT_CHARS:
                        errors.append(f"talking_points[{index}] is {len(point)} characters; notes for a human-only "
                                      f"destination are capped at {MAX_TALKING_POINT_CHARS} (ReachWeaver's cap, "
                                      "not a platform limit) so they cannot carry a ready-made post")
    else:
        if not isinstance(text, str):
            errors.append("variant.text must be a string (exact post text)")
        elif not text.strip():
            errors.append("variant.text must contain substantive text")
        else:
            _check_text_common(text, errors)
        if title is not None:
            if not isinstance(title, str) or not title.strip():
                errors.append("platform_options.title must be a non-empty string when present")
            else:
                _check_text_common(title, errors, "title")
        if points is not None:
            warnings.append("variant.talking_points is used only for human-only destinations (ignored)")
        if china_profile:
            china_errors, china_warnings = china.preflight(platform, text if isinstance(text, str) else None,
                                                           title if isinstance(title, str) else None,
                                                           options.get("topics"))
            errors += china_errors
            warnings += china_warnings
    if account_meta is not None:
        errors.extend(_check_account_meta(platform, account_meta, warnings))
    return errors, warnings


def _check_account_meta(platform: str, meta: object, warnings: list[str]) -> list[str]:
    if not isinstance(meta, dict):
        return ["account metadata must be a JSON object"]
    errors = _sensitive_keys(meta, "account_meta")
    unknown = sorted(set(meta) - ACCOUNT_META_FIELDS)
    if unknown:
        warnings.append(f"account metadata has unrecognized fields (ignored): {', '.join(unknown)}")
    if "platform" in meta and meta["platform"] != platform:
        errors.append(f"account metadata is for {meta['platform']!r}, not {platform!r}")
    return errors


# --- Registry facts (read only; nothing is invented) --------------------------------------

def _capability_entry(platform: str) -> dict | None:
    wanted = CAPABILITY_ID.get(platform, platform)
    return next((p for p in load_capability_registry()["platforms"] if p["id"] == wanted), None)


def _official_help(entry: dict) -> dict:
    source = entry["scheduling"].get("official_source")
    if isinstance(source, str) and source.startswith("https://"):
        return {"url": source, "checked_on": entry["checked_on"], "note": (
            "Official page the distribution registry cites for this destination's scheduling. It may describe "
            "scheduling rather than the composer; open the platform yourself from there.")}
    return {"url": None, "checked_on": entry["checked_on"], "note": (
        "The registry has no official source for this destination; find its own posting page yourself.")}


def _limits(platform: str, capability: dict | None) -> dict:
    media: list[dict] = []
    tables = constraint_table()
    for target in MEDIA_TARGETS.get(platform, (platform,)):
        for kind, rows in tables.get(target, {}).items():
            for name, row in rows.items():
                known = row["value"] is not None
                media.append({"table": target, "kind": kind, "rule": name, "value": row["value"],
                              "display": json.dumps(row["value"]) if known else LIMIT_UNKNOWN,
                              "severity": row["severity"] if known else None, "source": row["source"],
                              "verified_date": row["verified_date"], "note": row["note"]})
    notes = []
    if capability is not None:
        notes = [{"note": item, "source": capability["official_docs_url"],
                  "verified_date": capability["verified_date"]} for item in capability["compliance_constraints"]]
    return {
        "text": {"value": None, "display": LIMIT_UNKNOWN,
                 "note": "No registry field records a text limit for this destination."},
        "media": media or [{"table": None, "kind": None, "rule": None, "value": None, "display": LIMIT_UNKNOWN,
                            "severity": None, "source": None, "verified_date": None,
                            "note": "No media.py constraint table covers this destination."}],
        "capability_registry_notes": notes,
    }


# --- Media manifest -------------------------------------------------------------------------

def _rights(asset: dict) -> dict | None:
    raw = asset.get("provenance")
    provenance: dict = raw if isinstance(raw, dict) else {}
    record = {"rights_confirmed": asset.get("rights_confirmed"), "license": asset.get("license"),
              "attribution": asset.get("attribution") or None,
              "created_by": provenance.get("created_by"), "tool": provenance.get("tool") or None,
              "source_url": provenance.get("source_url") or None}
    return record if any(value is not None for value in record.values()) else None


def _media_section(platform: str, fmt: str, manifest, base_dir) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    targets = MEDIA_TARGETS.get(platform, (platform,))
    required = {"image": ["image"], "video": ["video"], "carousel": ["image", "video"]}.get(fmt, [])
    section: dict[str, Any] = {"required_kinds": required, "manifest": None, "validated": False, "assets": [],
                               "targets": list(targets)}
    if manifest is None:
        if required:
            warnings.append(f"no media manifest supplied: {', '.join(required)} not validated; attach the file "
                            "yourself and run media-check before posting")
        return section, errors, warnings
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        section["manifest"] = str(path)
        manifest, base_dir = read_manifest(path), path.parent
    elif not isinstance(manifest, dict) or base_dir is None:
        raise ValidationError("media_manifest must be a manifest path, or a dict with media_base_dir")
    result = validate_media_manifest(manifest, base_dir)
    section["validated"] = True
    errors.extend(f"media: {e}" for e in result["errors"])
    warnings.extend(f"media: {w}" for w in result["warnings"])
    reports = {(r.get("id"), r.get("path")): r for r in result["assets"]}
    for asset in manifest.get("assets", []) if isinstance(manifest.get("assets"), list) else []:
        if not isinstance(asset, dict) or not set(targets) & set(asset.get("targets") or []):
            continue
        report = reports.get((asset.get("id"), asset.get("path")), {})
        section["assets"].append({
            "id": asset.get("id"), "kind": asset.get("kind"), "path": asset.get("path"),
            "alt_text": asset.get("alt_text"), "alt_text_localized": asset.get("alt_text_localized", {}),
            "subtitles": [s.get("path") for s in asset.get("subtitles", []) if isinstance(s, dict)],
            "burned_in_captions": asset.get("burned_in_captions") is True,
            "rights": _rights(asset),
            "detected": {k: report.get(k) for k in ("detected_container", "width", "height",
                                                    "duration_seconds", "bytes")},
        })
    kinds = [a["kind"] for a in section["assets"] if a["kind"] in ("image", "video")]
    if fmt == "carousel":
        if len(kinds) < 2:
            errors.append(f"carousel needs at least 2 image/video assets targeting {platform} in the manifest")
    elif required and not any(k in required for k in kinds):
        errors.append(f"media manifest has no {required[0]} asset targeting {platform} "
                      f"(targets: {', '.join(targets)})")
    return section, errors, warnings


# --- Package -------------------------------------------------------------------------------

def build_package(platform: str, variant: dict, media_manifest=None, account_meta: dict | None = None, *,
                  media_base_dir=None, generated_at: str | None = None, copy_handoff: bool = False) -> dict:
    """Build a generic manual package. It never sends anything; status is always manual-only."""
    entry = _destination(platform, copy_handoff)
    if not isinstance(variant, dict):
        raise ValidationError("variant must be a JSON object")
    label, human_only = entry["label"], entry["human_only"] is True
    meta = account_meta if isinstance(account_meta, dict) else {}
    errors, warnings = package_preflight(platform, variant, account_meta, copy_handoff=copy_handoff)
    fmt = variant.get("format", "text")
    media, media_errors, media_warnings = _media_section(platform, fmt if isinstance(fmt, str) else "",
                                                         media_manifest, media_base_dir)
    errors += media_errors
    warnings += media_warnings
    china_profile = china.is_china(platform) and not human_only
    if china_profile:
        china_errors, china_warnings = china.media_check(platform, media["assets"])
        errors += china_errors
        warnings += china_warnings
    link_url = variant.get("link_url")
    if human_only:
        points = variant.get("talking_points")
        content: dict[str, Any] = {
            "ready_to_paste": False, "format": fmt,
            "talking_points": list(points) if isinstance(points, list) else [],
            "write_it_yourself": (f"{WRITE_IT_YOURSELF} {label} is human-only: ReachWeaver wrote no post text "
                                  "for it. Use the notes form, write the post in your own words and follow the "
                                  "community's own rules."),
        }
        links: dict[str, Any] = {"in_text": [], "link_url": link_url}
    else:
        text = cast(str, variant.get("text")) if isinstance(variant.get("text"), str) else ""
        title = _options(variant).get("title")
        title = title if isinstance(title, str) else None
        content = {"ready_to_paste": True, "format": fmt, "text": text, "title": title,
                   "measured": {"chars": len(text), "utf16_units": utf16_length(text),
                                "utf8_bytes": len(text.encode("utf-8")),
                                "title_chars": len(title) if title is not None else None},
                   "hashtags": extract_hashtags((title or "") + "\n" + text), "mentions": extract_mentions(text)}
        links = {"in_text": extract_links(text), "link_url": link_url}
    capability = _capability_entry(platform)
    package: dict[str, Any] = {
        "kind": PACKAGE_KIND,
        "schema_version": 1,
        "status": STATUS,
        "platform": platform,
        "platform_label": label,
        "human_only": human_only,
        "generated_at": generated_at or utc_now(),
        "ready_for_human_review": not errors,
        "preflight": {"errors": errors, "warnings": warnings},
        "content": content,
        "links": links,
        "media": media,
        "limits": _limits(platform, capability),
        "official_help": _official_help(entry),
        "account": {k: meta.get(k) for k in ("account_id", "handle", "display_name", "account_type")},
        "registry": {"reachweaver_delivery": entry["reachweaver_delivery"], "human_only": human_only,
                     "category": entry["category"], "scheduling_mode": entry["scheduling"]["mode"],
                     "scheduling_scope": entry["scheduling"]["scope"], "checked_on": entry["checked_on"],
                     "capability": None if capability is None else {
                         "id": capability["id"], "status": capability["status"],
                         "reachweaver_adapter": capability["reachweaver_adapter"],
                         "verified_date": capability["verified_date"]}},
        "notice": (f"MANUAL PACKAGE ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on "
                   f"{label}. This package is not evidence that a post exists. The account holder decides "
                   "whether to post, by hand, in the platform's own app or website."),
    }
    if china_profile:
        package["china"] = china.section(platform, content.get("text"), content.get("title"),
                                         _options(variant).get("topics"))
        package["limits"]["text"] = china.text_limit(platform)
    package["checklist"] = _checklist(package)
    return package


def _checklist(package: dict) -> list[str]:
    platform, label = package["platform"], package["platform_label"]
    who = next((package["account"][k] for k in ("handle", "display_name", "account_id")
                if package["account"].get(k)), "(not supplied)")
    help_page = package["official_help"]
    steps = ["Verify every claim against its cited source (campaign-check / claims-check); this package "
             "does not prove anything is true.",
             f"Signed in to the intended {label} account: {who}"]
    steps.append(f"Open {label} yourself; official page cited by the registry (checked {help_page['checked_on']}): "
                 f"{help_page['url']}" if help_page["url"] else
                 f"The registry has no official page for {label}; find its own posting page yourself.")
    mode = package["registry"]["scheduling_mode"]
    if mode == "prohibited":
        steps.append(f"Do not schedule or automate this: the registry marks {label} scheduling as prohibited.")
    elif mode == "moderator_only":
        steps.append(f"Post by hand: the registry marks {label} native scheduling as moderator-only.")
    if package["human_only"]:
        steps.append(package["content"]["write_it_yourself"])
        steps.append("Fill in NOTES.md privately first; it is a notes form, not text to paste.")
    else:
        if package["content"]["title"] is not None:
            steps.append("Paste title.txt as the title, exactly.")
        steps.append(f"Paste post.txt into {label} exactly; do not let tools add text, links or watermarks.")
    steps.append(f"Text limit: {package['limits']['text']['display']}.")
    if platform == "dev" and not package["human_only"]:
        steps.append("DEV: disclose AI assistance as DEV's AI guidelines require, and never post AI-generated "
                     f"comments or replies ({DEV_AI_GUIDELINES}).")
    for asset in package["media"]["assets"]:
        rights = asset["rights"] or {}
        steps.append(f"Attach {asset['kind']} {asset['path']}"
                     + (f"; enter alt text: {asset['alt_text']}" if asset.get("alt_text") else "")
                     + (f"; upload caption file(s): {', '.join(asset['subtitles'])}" if asset["subtitles"] else "")
                     + (f"; rights: license {rights.get('license')}, rights_confirmed "
                        f"{rights.get('rights_confirmed')}" if rights else "; no rights record in the manifest"))
    if package["media"]["required_kinds"] and not package["media"]["validated"]:
        steps.append("Run media-check on the media you attach (alt text, captions, rights, limits).")
    if "china" in package:
        steps.extend(china.checklist(platform, package))
    steps.append("Confirm rights, consent and disclosure for all text and media.")
    steps.append("If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver "
                 "records nothing from this package.")
    return steps


def render_markdown(package: dict) -> str:
    label = package["platform_label"]
    lines = [f"# {label} manual package checklist", "",
             f"Status: `{package['status']}`", "", package["notice"], ""]
    if package["preflight"]["errors"]:
        lines += ["## Blocking errors", ""] + [f"- {e}" for e in package["preflight"]["errors"]] + [""]
    if package["preflight"]["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in package["preflight"]["warnings"]] + [""]
    content = package["content"]
    if package["human_only"]:
        lines += ["## Write this yourself", "", content["write_it_yourself"], "",
                  "There is no post text in this package. NOTES.md is a private notes form.", ""]
    else:
        lines += ["## Exact text", ""]
        if content["title"] is not None:
            lines += [f"Title ({content['measured']['title_chars']} characters):", "", "```text",
                      content["title"], "```", ""]
        lines += [f"Post ({content['measured']['chars']} characters, {content['measured']['utf16_units']} UTF-16 "
                  f"units, {content['measured']['utf8_bytes']} UTF-8 bytes):", "",
                  "```text", content["text"], "```", "",
                  "- Hashtags: " + (", ".join(content["hashtags"]) or "none"),
                  "- Mentions: " + (", ".join(content["mentions"]) or "none"),
                  "- Links: " + (", ".join(package["links"]["in_text"]) or "none")]
    if package["links"]["link_url"]:
        lines.append(f"- Destination link: {package['links']['link_url']}")
    lines.append("")
    limits = package["limits"]
    lines += ["## Limits (from the registries only)" if "china" not in package else "## Limits", "",
              f"- Text: {limits['text']['display']}"]
    for row in limits["media"]:
        if row["table"] is None:
            lines.append(f"- Media: {row['display']}")
        else:
            lines.append(f"- Media `{row['table']}` {row['kind']} `{row['rule']}`: {row['display']}"
                         + (f" ({row['severity']}, verified {row['verified_date']}) {row['source']}"
                            if row["value"] is not None else f" ({row['source']})"))
    for note in limits["capability_registry_notes"]:
        lines.append(f"- Capability registry note (verified {note['verified_date']}): {note['note']} {note['source']}")
    lines.append("")
    if package["media"]["assets"]:
        lines += ["## Media", ""]
        for asset in package["media"]["assets"]:
            rights = asset["rights"]
            lines.append(f"- `{asset['path']}` ({asset['kind']}); alt text: {asset['alt_text'] or 'none'}; rights: "
                         + (", ".join(f"{k} {v}" for k, v in rights.items() if v is not None) if rights
                            else "no rights record in the manifest"))
        lines.append("")
    if "china" in package:
        lines += _china_markdown(package)
    lines += ["## Checklist", ""] + [f"- [ ] {step}" for step in package["checklist"]] + [""]
    registry = package["registry"]
    capability = registry["capability"]
    lines += ["## Why this is manual", "",
              f"Distribution registry: delivery `{registry['reachweaver_delivery']}`, human-only "
              f"`{str(registry['human_only']).lower()}`, scheduling `{registry['scheduling_mode']}`, checked "
              f"{registry['checked_on']}."
              + (f" Capability registry `{capability['id']}`: status `{capability['status']}`, adapter "
                 f"`{capability['reachweaver_adapter']}`, verified {capability['verified_date']}."
                 if capability else ""), ""]
    return "\n".join(lines) + "\n"


def _china_markdown(package: dict) -> list[str]:
    block = package["china"]
    lines = [f"## China ({block['label_zh']})", "", f"Post it yourself in: {block['composer']}.", "",
             block["api_status"], ""]
    measured = block["measured"]
    if measured:
        lines.append("- Measured: " + ", ".join(f"{k} {v}" for k, v in measured.items()))
    if block["topics"]["rendered"]:
        lines.append("- Topics (topics.txt): " + block["topics"]["rendered"].replace("\n", " "))
    for key, fact in block["facts"].items():
        value = fact["value"] if fact["value"] is not None else china.LIMIT_UNKNOWN
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v!r}" if k == "forbidden" else f"{k} {v}" for k, v in value.items())
        lines.append(f"- `{key}`: {value} ({fact['status']}, {fact['checked_on']})"
                     + (f" {fact['source']}" if fact["source"] else "")
                     + (f" Quote: {fact['quote']} ({fact['translation']})" if fact["quote"] else "")
                     + (f" {fact['note']}" if fact["note"] else ""))
    lines += ["", "### Decisions only you can make (nothing pre-selected)", ""]
    for decision in block["human_decisions"]:
        lines.append(f"- **{decision['id']}**: {decision['question']} Options: {' / '.join(decision['options'])}. "
                     f"Source: {decision['source']}. {decision['note']}")
    lines += ["", "### Rules that apply on every Chinese platform", ""]
    for rule in block["legal"].values():
        lines.append(f"- {rule['value']}. {rule['source']} Quote: {rule['quote']} ({rule['translation']}) "
                     f"{rule['note']}")
    return lines + [""]


def render_notes(package: dict) -> str:
    """The human-only notes form: talking points and blanks, never text to paste."""
    label = package["platform_label"]
    lines = [f"# {label} notes (write the post yourself)", "", f"Status: `{package['status']}`", "",
             f"**{package['content']['write_it_yourself']}** These are notes, not text to paste.", ""]
    points = package["content"]["talking_points"]
    if points:
        lines += ["## Points you might cover (rephrase in your own words)", ""] + [f"- {p}" for p in points] + [""]
    if package["links"]["link_url"]:
        lines += [f"Link you may cite: {package['links']['link_url']}", ""]
    lines += ["## Your notes", "",
              "- What I want to say, in my own words:",
              "- Why this community would care:",
              "- Community rules I checked (and where):",
              "- Links I will include:",
              "- Post URL after I post it (keep it as first-party evidence):", ""]
    return "\n".join(lines)


def write_package(package: dict, destination: str | Path) -> list[str]:
    """Write the package to a NEW owner-only directory (0700) of 0600 files; never overwrite."""
    if package.get("status") != STATUS or package.get("kind") != PACKAGE_KIND or package["preflight"]["errors"]:
        raise ValidationError("Refusing to write a package with blocking preflight errors")
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ValidationError("Output directory already exists; refusing to overwrite a prior export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(destination, 0o700)
    if os.name == "posix":
        os.chmod(destination, 0o700)
    files = {"manual-export.json": json.dumps(package, indent=2, ensure_ascii=False) + "\n",
             "CHECKLIST.md": render_markdown(package)}
    if package["human_only"]:
        files["NOTES.md"] = render_notes(package)
    else:
        if package["content"]["title"] is not None:
            files["title.txt"] = package["content"]["title"]
        files["post.txt"] = package["content"]["text"]
        if package.get("china", {}).get("topics", {}).get("rendered"):
            files["topics.txt"] = package["china"]["topics"]["rendered"]
    written = []
    for name, text in files.items():
        path = destination / name
        write_private_file(path, text)
        written.append(str(path))
    return written
