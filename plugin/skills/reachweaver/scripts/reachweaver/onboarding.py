"""Offline audit: every blocked or gated platform has a manual alternative or an explicit marker.

Issue #27. ReachWeaver has live text adapters for four platforms only. For every other
platform in the capability registry (``platform_registry.json``) and every channel in the
worldwide distribution registry (``distribution_registry.json``), this module names what an
owner can do instead, as one of five kinds:

- ``manual_export``: ``reachweaver manual-export`` writes a creator package and checklist.
- ``draft_export``: ``reachweaver campaign-export`` writes text drafts the owner posts by hand.
- ``native_manual``: a research-only channel with no ReachWeaver platform id, so no export
  exists. The owner writes and posts in the native app; ``distribution-plan`` lists it.
- ``human_only``: ReachWeaver writes no text for this venue. The owner writes and posts it.
- ``unsupported``: not a publishing target for ReachWeaver at all.

The audit reads the two registries and returns rows plus consistency errors. It never grants
permission, opens a database, touches credentials or uses the network (``SEND_PATH = None``).
``scripts/release_gate.py`` and ``tests/test_onboarding.py`` fail when a new blocked platform
appears without an entry, when a human-only venue would get generated drafts, or when the
table in ``docs/TROUBLESHOOTING.md`` drifts from :func:`render_markdown`.
"""
from __future__ import annotations

from .capabilities import load_registry as load_capability_registry
from .distribution import load_registry as load_distribution_registry
from .manual_export import MANUAL_PLATFORMS
from .policy import PLATFORMS, SUPPORTED_LIVE

SEND_PATH = None  # Asserted in tests: this module can never send, stage or schedule.

KINDS = ("manual_export", "draft_export", "native_manual", "human_only", "unsupported")
COMMANDS = {
    "manual_export": "reachweaver manual-export --platform {platform} --variant VARIANT.json --out NEW_DIR",
    "draft_export": "reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR",
    "native_manual": None,
    "human_only": None,
    "unsupported": None,
}

# One reviewed decision per capability-registry platform that has no live ReachWeaver adapter.
# The notes repeat decisions already recorded in docs/PLATFORMS.md and docs/MANUAL-EXPORT.md.
CAPABILITY_ALTERNATIVES: dict[str, tuple[str, str]] = {
    "x": ("draft_export", "No adapter until the owner records a cost ceiling (PLATFORMS.md go/no-go); "
                          "post the exported text in the X app"),
    "threads": ("manual_export", "Threads API needs Meta app review; creator package with 500-character "
                                 "and 5-link checks"),
    "instagram": ("manual_export", "Professional account and Meta app review required; owner attaches "
                                   "the media in the app"),
    "facebook": ("manual_export", "Pages only; profiles and groups are refused by the export preflight"),
    "youtube": ("manual_export", "API audit required for public uploads; owner uploads in YouTube Studio "
                                 "and decides privacy and disclosures"),
    "tiktok": ("manual_export", "Content Posting API audit required; owner posts and decides privacy "
                                "and labels"),
    "reddit": ("human_only", "Human-authored and human-posted only; native scheduling is moderator-only. "
                             "ReachWeaver writes no Reddit text"),
    "pinterest": ("draft_export", "Business account and app access unconfirmed; Pins need owner-supplied "
                                  "media (check it with media-check)"),
    "devto": ("draft_export", "Human-authored articles only (DEV AI rules); use the DEV article brief "
                              "and DEV's own scheduler"),
    "hashnode": ("draft_export", "No adapter; native scheduling needs Hashnode Pro; post the article "
                                 "by hand"),
}

# Distribution-registry ids that name a capability-registry platform differently.
DISTRIBUTION_TO_CAPABILITY = {"dev": "devto", "facebook_pages": "facebook"}


def _is_live(entry: dict) -> bool:
    from .capabilities import is_live_entry  # live_text or the issue #20 live_media
    return is_live_entry(entry)


def _row(registry: str, platform_id: str, label: str, status: str, kind: str, note: str) -> dict:
    template = COMMANDS.get(kind)
    return {
        "registry": registry,
        "id": platform_id,
        "label": label,
        "status": status,
        "kind": kind,
        "command": template.format(platform=platform_id) if template else None,
        "note": note,
    }


def blocker_audit(capability_registry: dict | None = None, distribution_registry: dict | None = None,
                  alternatives: dict[str, tuple[str, str]] | None = None) -> dict:
    """Return ``{"rows": [...], "errors": [...], "live": [...]}`` for both registries.

    ``errors`` is empty only when every blocked or gated platform has exactly one valid
    alternative, human-only venues get no generated drafts, and both registries agree.
    """
    caps = capability_registry if capability_registry is not None else load_capability_registry()
    dist = distribution_registry if distribution_registry is not None else load_distribution_registry()
    alts = CAPABILITY_ALTERNATIVES if alternatives is None else alternatives
    errors: list[str] = []
    rows: list[dict] = []
    live: list[str] = []

    human_only = {DISTRIBUTION_TO_CAPABILITY.get(p["id"], p["id"])
                  for p in dist.get("platforms", []) if p.get("human_only") is True}
    cap_entries = {p["id"]: p for p in caps.get("platforms", []) if isinstance(p, dict) and "id" in p}
    cap_kind: dict[str, str] = {}
    for platform_id in sorted(cap_entries):
        entry = cap_entries[platform_id]
        if _is_live(entry):
            live.append(platform_id)
            if platform_id in alts:
                errors.append(f"{platform_id}: has a live adapter; remove its stale manual-alternative entry")
            continue
        if platform_id not in alts:
            errors.append(f"{platform_id}: {entry.get('status')} platform has no manual alternative "
                          f"and no explicit unsupported marker")
            continue
        kind, note = alts[platform_id]
        if kind not in KINDS:
            errors.append(f"{platform_id}: unknown alternative kind {kind!r}")
            continue
        if not isinstance(note, str) or not note.strip():
            errors.append(f"{platform_id}: alternative needs a non-empty note")
        if kind in ("native_manual", "unsupported"):
            errors.append(f"{platform_id}: {kind} is reserved for channels outside the capability registry")
        if kind == "draft_export" and platform_id not in PLATFORMS:
            errors.append(f"{platform_id}: campaign-export accepts only policy.PLATFORMS ids")
        if kind == "manual_export" and platform_id not in MANUAL_PLATFORMS:
            errors.append(f"{platform_id}: manual-export does not cover this platform")
        if platform_id in MANUAL_PLATFORMS and kind != "manual_export" and platform_id not in human_only:
            errors.append(f"{platform_id}: manual-export covers this platform; use kind manual_export")
        if platform_id in human_only and kind != "human_only":
            errors.append(f"{platform_id}: human-only venue must not receive generated drafts (kind human_only)")
        cap_kind[platform_id] = kind
        rows.append(_row("capability", platform_id, entry.get("label", platform_id), entry.get("status", ""),
                         kind, note))
    for platform_id in sorted(set(alts) - set(cap_entries)):
        errors.append(f"{platform_id}: manual-alternative entry for a platform missing from the capability registry")
    if set(live) != set(SUPPORTED_LIVE):
        errors.append("live adapters in the capability registry do not match policy.SUPPORTED_LIVE")

    for entry in dist.get("platforms", []):
        platform_id = entry.get("id")
        delivery = entry.get("reachweaver_delivery")
        if delivery == "mock_tested_text":
            continue
        mapped = DISTRIBUTION_TO_CAPABILITY.get(platform_id, platform_id)
        if delivery == "not_applicable":
            kind, note = "unsupported", "Not a social publishing target for ReachWeaver"
        elif delivery == "manual_only" and entry.get("human_only") is True:
            kind, note = "human_only", "Human-only venue: the owner writes and posts; no generated drafts"
        elif delivery == "manual_only" and mapped in PLATFORMS:
            kind = "manual_export" if mapped in MANUAL_PLATFORMS else "draft_export"
            note = "Owner posts in the native app from an exported draft"
        elif delivery == "manual_only":
            kind = "native_manual"
            note = "No ReachWeaver platform id or export; the owner writes and posts in the native app"
        else:
            errors.append(f"{platform_id}: unknown ReachWeaver delivery {delivery!r}")
            continue
        if mapped in cap_kind and cap_kind[mapped] != kind:
            errors.append(f"{platform_id}: distribution registry implies {kind} but capability entry "
                          f"{mapped} says {cap_kind[mapped]}")
        rows.append(_row("distribution", platform_id, entry.get("label", platform_id), delivery, kind, note))

    for venue in ("hacker_news", "reddit"):
        if not any(r["registry"] == "distribution" and r["id"] == venue and r["kind"] == "human_only"
                   for r in rows):
            errors.append(f"{venue}: must stay human_only (no generated text, no automation)")
    return {"rows": rows, "errors": errors, "live": sorted(live)}


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|") if value else "—"


def render_markdown(audit: dict) -> str:
    """Deterministic Markdown tables; docs/TROUBLESHOOTING.md embeds this output verbatim."""
    lines = [
        "Live text adapters (still need approval, `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`): "
        + ", ".join(f"`{p}`" for p in audit["live"]) + ".",
        "",
        "**Capability registry** (`reachweaver/platform_registry.json`):",
        "",
        "| Platform | Registry status | Alternative | Command | Note |",
        "|---|---|---|---|---|",
    ]
    for row in audit["rows"]:
        if row["registry"] == "capability":
            command = f"`{row['command']}`" if row["command"] else "none"
            lines.append(f"| {_cell(row['label'])} (`{row['id']}`) | {row['status']} | {row['kind']} | "
                         f"{command} | {_cell(row['note'])} |")
    lines += [
        "",
        "**Distribution registry** (`reachweaver/distribution_registry.json`, channels without a live adapter):",
        "",
        "| Alternative | Channels |",
        "|---|---|",
    ]
    for kind in KINDS:
        ids = sorted(r["id"] for r in audit["rows"] if r["registry"] == "distribution" and r["kind"] == kind)
        if ids:
            lines.append(f"| {kind} | {', '.join(f'`{i}`' for i in ids)} |")
    return "\n".join(lines) + "\n"
