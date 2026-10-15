"""Offline delivery routes for every distribution-registry channel (salvaged from draft PR #65).

``delivery-options`` answers one question per channel: how could the owner deliver a reviewed
post there today? The classification is not new. It reuses :func:`onboarding.blocker_audit`,
the same audit that generates the blocker table in ``docs/TROUBLESHOOTING.md``, and adds one
route for the four mock-tested API adapters, plus the exact ``manual-export`` command that writes
a reviewed package for every ``manual_only`` channel.

Every route ends with a human: an approved post sent through an official API adapter behind the
owner gates, or a package the owner posts by hand in the platform's own app or website. There is
no route in which a tool fills a composer in the owner's browser (owner decision of 2026-10-16,
issue #1). Human-only channels get a notes form, never post text.

Nothing here sends, schedules, opens a browser or reads a credential (``SEND_PATH = None``).
"""
from __future__ import annotations

from .distribution import load_registry
from .manual_package import DEDICATED
from .onboarding import blocker_audit
from .policy import ValidationError

SEND_PATH = None  # Asserted in tests: this module can never send, upload or schedule.
KIND = "reachweaver.delivery-options.v1"
ROUTES = ("official_api_adapter", "manual_export", "draft_export", "native_manual", "human_only", "unsupported")
NOTES = {
    "official_api_adapter": ("Official API adapter (mock-tested). The owner signs in through the platform's own "
                             "consent flow; ReachWeaver sends only a post approved with `approve`, then "
                             "`publish --live` behind the owner gates. A send is real only with a native receipt."),
    "manual_export": ("Sourced manual package: `manual-export` runs the platform-specific text and media checks; "
                      "the owner posts it by hand in the platform's own app or website."),
    "draft_export": ("No adapter. `manual-export` writes a generic package (or `campaign-export` writes text "
                     "drafts); the owner posts by hand in the platform's own app or website."),
    "native_manual": ("No adapter and no platform-specific export. `manual-export` writes a generic package with "
                      "the exact text; the owner posts by hand in the platform's own app or website."),
    "human_only": ("Human-only venue: ReachWeaver writes no post text. `manual-export` writes only a notes form "
                   "(NOTES.md); the owner writes and posts it themselves under the community's rules."),
    "unsupported": "Not a social publishing target for ReachWeaver.",
}
NOTICE = ("Offline routing only. No account was connected, no browser was opened, and no API request, schedule "
          "or post was made. A route is not permission to post: check the platform's rules and your account first.")


def _manual_command(platform_id: str) -> str:
    return (f"reachweaver manual-export --platform {DEDICATED.get(platform_id, platform_id)} "
            "--variant VARIANT.json --out NEW_DIR")


def _route(entry: dict, kinds: dict[str, str]) -> str:
    if entry["reachweaver_delivery"] == "mock_tested_text":
        return "official_api_adapter"
    return kinds[entry["id"]]


def list_delivery_routes(platform: str | None = None) -> dict:
    """Return the delivery route of one channel, or of every channel, from the bundled registries."""
    registry = load_registry()
    known = {p["id"]: p for p in registry["platforms"]}
    if platform is not None and platform not in known:
        raise ValidationError(f"{platform!r} is not in the distribution registry; run delivery-options to list ids")
    audit = blocker_audit(distribution_registry=registry)
    if audit["errors"]:
        raise ValidationError("Registry blocker audit failed: " + "; ".join(audit["errors"]))
    kinds = {row["id"]: row["kind"] for row in audit["rows"] if row["registry"] == "distribution"}
    selected = [known[platform]] if platform is not None else registry["platforms"]
    channels = []
    for entry in selected:
        route = _route(entry, kinds)
        commands = []
        if entry["reachweaver_delivery"] == "manual_only":
            commands.append(_manual_command(entry["id"]))
        if route == "draft_export":
            commands.append("reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR")
        channels.append({
            "id": entry["id"],
            "label": entry["label"],
            "category": entry["category"],
            "route": route,
            "human_only": entry["human_only"],
            "reachweaver_delivery": entry["reachweaver_delivery"],
            "native_scheduling": entry["scheduling"]["mode"],
            "official_source": entry["scheduling"]["official_source"] or None,
            "research_source": entry.get("research_source"),
            "checked_on": entry["checked_on"],
            "commands": commands,
            "note": NOTES[route],
            "account_connected": "unknown_not_verified",
        })
    counts = {route: sum(1 for c in channels if c["route"] == route) for route in ROUTES}
    return {
        "schema_version": 1,
        "kind": KIND,
        "reviewed_on": registry["reviewed_on"],
        "platform_count": len(channels),
        "route_counts": {route: count for route, count in counts.items() if count},
        "channels": channels,
        "network_requests": False,
        "posts_created": 0,
        "posts_scheduled": 0,
        "notice": NOTICE,
    }


def render_text(result: dict) -> str:
    lines = []
    for item in result["channels"]:
        lines.append(f"{item['id']}: {item['route']} (native scheduling: {item['native_scheduling']}"
                     + (", human-only" if item["human_only"] else "") + ")")
        lines.extend(f"    {command}" for command in item["commands"])
    lines.append(result["notice"])
    return "\n".join(lines) + "\n"
