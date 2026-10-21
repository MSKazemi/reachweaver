"""Read-only open social web planner (ActivityPub, AT Protocol, Nostr, Farcaster, bridges).

Reads the bundled ``social_web_registry.json`` (sourced protocol facts keyed by the
distribution registry's platform ids), the owner's public accounts config and an optional
setup file the owner writes by hand. It reports which accounts sit on which protocol, a
canonical-home hypothesis, duplicate-reach warnings caused by bridges or opt-in federation,
an identity checklist and gaps.

It never contacts a network, reads a credential, opens the database, touches the send queue,
or creates, approves, schedules or publishes anything. ``bridge_duplicate_risk`` is a pure
helper other planning code may call; it is deliberately not wired into approve, publish or
the repeat guard.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from .inputs import read_json_file
from .policy import ValidationError

REGISTRY_FILE = Path(__file__).with_name("social_web_registry.json")
PLAN_SCHEMA = "reachweaver.social-web-plan.v1"
SETUP_SCHEMA = "reachweaver.social-web-setup.v1"
PROTOCOLS = frozenset({"activitypub", "atproto", "nostr", "farcaster", "matrix", "diaspora_federation"})
FEDERATION = frozenset({"native", "opt_in", "rollout", "unverified"})
NETWORKS = ("web", "fediverse", "atmosphere", "nostr", "farcaster", "matrix", "diaspora")
NETWORK_LABELS = {"web": "web", "fediverse": "fediverse (ActivityPub)", "atmosphere": "Atmosphere (Bluesky, AT Protocol)",
                  "nostr": "Nostr", "farcaster": "Farcaster", "matrix": "Matrix", "diaspora": "diaspora*"}
SITE_KINDS = ("ghost", "wordpress_activitypub", "bridgy_fed_web", "plain")
NEWSLETTER_IDS = ("substack", "beehiiv")  # owned-list homes with no open protocol recorded here
# accounts.json platform ids that differ from the distribution registry ids.
ACCOUNT_PLATFORM_IDS = {"facebook": "facebook_pages", "devto": "dev"}
NO_PROTOCOL = "none_recorded"
MAX_ITEMS = 200
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
__all__ = ("load_registry", "validate_registry", "load_setup", "build_plan", "bridge_duplicate_risk", "render_text")


def _https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and not (parts.username or parts.password)


def _known_platform_ids() -> set[str]:
    from .distribution import load_registry as load_distribution  # read only; distribution.py is not changed
    return {p["id"] for p in load_distribution()["platforms"]}


def load_registry(path: Path | None = None) -> dict:
    """Load and validate the bundled open social web snapshot."""
    registry = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    errors = validate_registry(registry, _known_platform_ids())
    if errors:
        raise ValueError("Invalid social web registry: " + "; ".join(errors))
    return registry


def _check_sources(owner: str, sources: object, snapshot: date | None, errors: list[str], *, required: bool) -> None:
    if not isinstance(sources, list) or (required and not sources):
        errors.append(f"{owner}: needs a non-empty sources list" if required else f"{owner}: sources must be a list")
        return
    for source in sources:
        if not isinstance(source, dict) or not _https_url(source.get("url")):
            errors.append(f"{owner}: every source needs an HTTPS url")
            continue
        if not isinstance(source.get("quote"), str) or len(source["quote"].strip()) < 10:
            errors.append(f"{owner}: every source needs the verified quote")
        try:
            checked = date.fromisoformat(source["checked_on"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{owner}: source checked_on must be an ISO 8601 date")
            continue
        if snapshot and checked > snapshot:
            errors.append(f"{owner}: source checked_on is later than reviewed_on")


def validate_registry(registry: object, known_platform_ids: Iterable[str] | None = None) -> list[str]:
    """Data contract, provenance and the conservative invariants (bridges are opt-in, Threads federation
    is an owner setting). ``known_platform_ids`` cross-checks ids against the distribution registry."""
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return ["schema_version must be 1"]
    errors: list[str] = []
    snapshot: date | None = None
    try:
        snapshot = date.fromisoformat(registry["reviewed_on"])
    except (KeyError, TypeError, ValueError):
        errors.append("reviewed_on must be an ISO 8601 date")
    if registry.get("decision_type") != "sourced_protocol_facts_and_editorial_hypotheses":
        errors.append("decision_type must separate sourced facts from hypotheses")
    protocols = registry.get("protocols")
    if not isinstance(protocols, list) or not protocols:
        return errors + ["protocols must be a non-empty list"]
    protocol_ids: set[str] = set()
    for proto in protocols:
        key = proto.get("id") if isinstance(proto, dict) else None
        if key not in PROTOCOLS or key in protocol_ids:
            errors.append(f"unknown or duplicate protocol: {key}")
            continue
        protocol_ids.add(key)
        if proto.get("network") not in NETWORKS:
            errors.append(f"{key}: unknown network")
        _check_sources(key, proto.get("sources"), snapshot, errors, required=True)
    checks = registry.get("identity_checks")
    check_ids = {c.get("id") for c in checks if isinstance(c, dict)} if isinstance(checks, list) else set()
    if not isinstance(checks, list) or not checks:
        errors.append("identity_checks must be a non-empty list")
    else:
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not check.get("action"):
                errors.append("identity check needs id and action")
                continue
            _check_sources(check["id"], check.get("sources"), snapshot, errors, required=True)
    platforms = registry.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        return errors + ["platforms must be a non-empty list"]
    known = set(known_platform_ids) if known_platform_ids is not None else None
    mapping: dict[str, dict] = {}
    for entry in platforms:
        key = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(key, str) or key in mapping:
            errors.append(f"platform missing id or duplicate: {key}")
            continue
        mapping[key] = entry
        if known is not None and key not in known:
            errors.append(f"{key}: not a distribution registry platform id")
        if entry.get("protocol") not in protocol_ids:
            errors.append(f"{key}: protocol must be one of the recorded protocols")
        if entry.get("federation") not in FEDERATION:
            errors.append(f"{key}: invalid federation status")
        if not isinstance(entry.get("federation_note"), str) or not entry["federation_note"].strip():
            errors.append(f"{key}: federation_note is required")
        tips = entry.get("identity_checks")
        if not isinstance(tips, list) or any(t not in check_ids for t in tips):
            errors.append(f"{key}: identity_checks must name recorded checks")
        _check_sources(key, entry.get("sources"), snapshot, errors, required=True)
    if mapping.get("threads", {}).get("federation") != "opt_in":
        errors.append("threads: fediverse sharing must stay labelled opt_in (an owner setting)")
    if mapping.get("bluesky", {}).get("protocol") != "atproto":
        errors.append("bluesky: protocol must be atproto")
    bridges = registry.get("bridges")
    if not isinstance(bridges, list) or not bridges:
        errors.append("bridges must be a non-empty list")
    else:
        for bridge in bridges:
            key = bridge.get("id") if isinstance(bridge, dict) else None
            if not isinstance(key, str):
                errors.append("bridge missing id")
                continue
            if bridge.get("opt_in") is not True:
                errors.append(f"{key}: a bridge must be recorded as opt-in; ReachWeaver never enables one")
            connects = bridge.get("connects")
            if not isinstance(connects, list) or len(connects) < 2 or any(n not in NETWORKS for n in connects):
                errors.append(f"{key}: connects must list at least two known networks")
            if bridge.get("mode") != "mirror_existing_account":
                errors.append(f"{key}: unsupported bridge mode")
            _check_sources(key, bridge.get("sources"), snapshot, errors, required=True)
    homes = registry.get("home_options")
    if not isinstance(homes, list) or {h.get("id") for h in homes if isinstance(h, dict)} != set(SITE_KINDS):
        errors.append("home_options must cover exactly: " + ", ".join(SITE_KINDS))
    else:
        for home in homes:
            if any(n not in NETWORKS for n in home.get("reaches", [])) or "web" not in home.get("reaches", []):
                errors.append(f"{home['id']}: reaches must include web and only known networks")
            _check_sources(home["id"], home.get("sources"), snapshot, errors, required=home["id"] != "plain")
    policy = registry.get("canonical_home_policy")
    if not isinstance(policy, dict) or policy.get("status") != "editorial_hypothesis":
        errors.append("canonical_home_policy must be labelled editorial_hypothesis")
    return errors


def _string(value: object, label: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValidationError(f"{label} must be a non-empty printable string of at most {limit} characters")
    return value.strip()


def _id_list(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ValidationError(f"{label} must be a list of ids")
    return [_string(item, label, 64) for item in value]


def load_setup(path: str | Path | None) -> dict:
    """Read the optional hand-written setup file. The file is the owner's own statement, not verified access."""
    if path is None:
        return {}
    try:
        data = read_json_file(Path(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Setup file is unreadable or not JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("schema") != SETUP_SCHEMA:
        raise ValidationError(f"Setup file needs \"schema\": \"{SETUP_SCHEMA}\"")
    unknown = set(data) - {"schema", "site", "presence", "bridges", "fediverse_sharing", "identity", "note"}
    if unknown:
        raise ValidationError("Unknown setup field(s): " + ", ".join(sorted(unknown)))
    setup: dict = {"presence": [], "bridged": [], "fediverse_sharing": _id_list(data.get("fediverse_sharing"), "fediverse_sharing"),
                   "identity": {}, "site": None}
    site = data.get("site")
    if site is not None:
        if not isinstance(site, dict):
            raise ValidationError("site must be an object with url and kind")
        setup["site"] = {"url": site.get("url"), "kind": site.get("kind", "plain")}
    presence = data.get("presence", [])
    if not isinstance(presence, list) or len(presence) > MAX_ITEMS:
        raise ValidationError("presence must be a list")
    for item in presence:
        if not isinstance(item, dict):
            raise ValidationError("each presence must be an object with platform (and optional id, handle)")
        setup["presence"].append({"platform": _string(item.get("platform"), "presence platform", 64),
                                  "id": _string(item["id"], "presence id", 64) if "id" in item else None,
                                  "handle": _string(item["handle"], "presence handle") if "handle" in item else None})
    bridges = data.get("bridges", [])
    if not isinstance(bridges, list) or len(bridges) > MAX_ITEMS:
        raise ValidationError("bridges must be a list")
    for item in bridges:
        if not isinstance(item, dict) or item.get("bridge", "bridgy_fed") != "bridgy_fed":
            raise ValidationError("each bridge must be {\"bridge\": \"bridgy_fed\", \"account\": ID}")
        setup["bridged"].append(_string(item.get("account"), "bridge account", 64))
    identity = data.get("identity", {})
    if not isinstance(identity, dict) or any(not isinstance(v, bool) for v in identity.values()):
        raise ValidationError("identity must map check ids to true/false")
    setup["identity"] = dict(identity)
    return setup


def _presence_reach(presence: Mapping, entry: Mapping | None, networks: Mapping[str, str]) -> list[dict]:
    """Networks one presence reaches, and how. ``networks`` maps protocol id -> network id."""
    reach: list[dict] = []
    if presence.get("platform") == "site":
        reach.append({"network": "web", "via": "owned_site"})
        for network in presence.get("site_reaches", []):
            if network != "web":
                reach.append({"network": network, "via": presence.get("site_via", "native")})
        return reach
    if entry is None:
        return reach
    network = networks.get(entry["protocol"])
    if network:
        federation = entry["federation"]
        if federation == "native":
            reach.append({"network": network, "via": "native"})
        elif federation == "opt_in":
            if presence.get("fediverse_sharing"):
                reach.append({"network": network, "via": "opt_in_sharing"})
        elif federation == "rollout":
            reach.append({"network": network, "via": "rollout_unverified"})
        else:
            reach.append({"network": network, "via": "unverified"})
    if presence.get("bridgy_fed") and any(r["network"] in ("fediverse", "atmosphere") for r in reach):
        for other in ("fediverse", "atmosphere"):
            if all(r["network"] != other for r in reach):
                reach.append({"network": other, "via": "bridgy_fed"})
    return reach


def _label(presence: Mapping) -> str:
    if presence.get("platform") == "site":
        return "your site"
    return f"{presence.get('label', presence.get('platform'))} ({presence.get('id')})"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def bridge_duplicate_risk(presences: Iterable[Mapping], registry: dict | None = None) -> list[dict]:
    """Warnings where two of the owner's presences reach the same open network.

    Each presence is a mapping with ``id``, ``platform`` (distribution registry id, or ``"site"`` with
    ``site_reaches``), and optional ``bridgy_fed`` / ``fediverse_sharing`` booleans. Pure and offline:
    returns advice only and never blocks, approves or alters a post.
    """
    data = registry if registry is not None else load_registry()
    entries = {p["id"]: p for p in data["platforms"]}
    networks = {p["id"]: p["network"] for p in data["protocols"]}
    bridge_source = next(b for b in data["bridges"] if b["id"] == "bridgy_fed")["sources"][1]["url"]
    reached: dict[str, list[tuple[dict, str]]] = {}
    for presence in presences:
        item = dict(presence)
        item.setdefault("label", item.get("platform"))
        for hop in _presence_reach(item, entries.get(str(item.get("platform"))), networks):
            reached.setdefault(hop["network"], []).append((item, hop["via"]))
    warnings: list[dict] = []
    for network in NETWORKS:
        hits = reached.get(network, [])
        if network == "web" or len(hits) < 2:
            continue
        carried = [(p, via) for p, via in hits if via in ("bridgy_fed", "opt_in_sharing")]
        natives = [(p, via) for p, via in hits if via not in ("bridgy_fed", "opt_in_sharing")]
        where = NETWORK_LABELS[network]
        if carried:
            for presence, via in carried:
                others = [p for p, _ in hits if p is not presence]
                how = "bridged into" if via == "bridgy_fed" else "shared to"
                mechanism = "via Bridgy Fed" if via == "bridgy_fed" else "with fediverse sharing on"
                subject = ("Your site" if presence.get("platform") == "site"
                           else f"Your {_label(presence)} account")
                warnings.append({
                    "id": f"duplicate_reach:{network}:{presence.get('id')}",
                    "severity": "warning",
                    "network": network,
                    "accounts": [presence.get("id")] + [p.get("id") for p in others],
                    "message": (f"{subject} is {how} the {where} {mechanism}, so posting the "
                                f"same text natively on {_join([_label(p) for p in others])} reaches some people "
                                "twice: anyone who follows both copies."),
                    "options": ["Post once natively and let the bridge or sharing carry it",
                                "Write a different version for each account",
                                "Turn the bridge or sharing off for one account"],
                    "evidence": "hypothesis: only people who follow both presences see it twice; no audience data was read",
                    "source": bridge_source if via == "bridgy_fed" else entries["threads"]["sources"][0]["url"],
                })
        elif len(natives) >= 2:
            warnings.append({
                "id": f"same_network:{network}",
                "severity": "info",
                "network": network,
                "accounts": [p.get("id") for p, _ in natives],
                "message": (f"{len(natives)} of your accounts are native on the {where}: "
                            f"{_join([_label(p) for p, _ in natives])}. Identical text on each reaches people who "
                            "follow more than one of them repeatedly."),
                "options": ["Give each account its own angle or audience", "Boost or repost from one account instead"],
                "evidence": "hypothesis: depends on how many people follow more than one account",
                "source": None,
            })
    return warnings


def _site(url: object, kind: object) -> dict:
    if not _https_url(url):
        raise ValidationError("site url must be an https:// URL without credentials")
    if kind not in SITE_KINDS:
        raise ValidationError("site kind must be one of: " + ", ".join(SITE_KINDS))
    return {"url": url, "kind": kind}


def build_plan(accounts: Mapping[str, Mapping] | None, *, setup: Mapping | None = None, bridged: Iterable[str] = (),
               presence: Iterable[str] = (), fediverse_sharing: Iterable[str] = (), site_url: str | None = None,
               site_kind: str | None = None, accounts_file: str = "loaded", registry: dict | None = None) -> dict:
    """Offline open social web plan. Never contacts a network, the database or the queue."""
    data = registry if registry is not None else load_registry()
    known = _known_platform_ids()
    entries = {p["id"]: p for p in data["platforms"]}
    networks = {p["id"]: p["network"] for p in data["protocols"]}
    checks = {c["id"]: c for c in data["identity_checks"]}
    homes = {h["id"]: h for h in data["home_options"]}
    setup = dict(setup or {})
    labels = _labels()
    presences: list[dict] = []
    for account_id, account in (accounts or {}).items():
        platform = ACCOUNT_PLATFORM_IDS.get(str(account.get("platform")), str(account.get("platform")))
        presences.append({"id": account_id, "platform": platform, "source": "accounts",
                          "handle": account.get("handle") if isinstance(account.get("handle"), str) else None})
    extra = [dict(p) for p in setup.get("presence", [])] + [{"platform": p, "id": None, "handle": None} for p in presence]
    for item in extra:
        platform = item["platform"]
        if platform not in known:
            raise ValidationError(f"Unknown platform id {platform!r}; use a distribution-plan / account-advisor id")
        item_id = item.get("id") or platform
        if any(p["id"] == item_id for p in presences):
            continue  # same presence named twice (accounts file and setup, or a repeated flag)
        presences.append({"id": item_id, "platform": platform, "source": "setup", "handle": item.get("handle")})
    site = setup.get("site")
    if site_url is not None:
        site = {"url": site_url, "kind": site_kind or (site or {}).get("kind") or "plain"}
    elif site_kind is not None:
        if not site:
            raise ValidationError("--site-kind needs --site URL (or a site in the setup file)")
        site = {**site, "kind": site_kind}
    if site:
        site = _site(site.get("url"), site.get("kind"))
        home = homes[site["kind"]]
        presences.append({"id": "site", "platform": "site", "source": "setup", "handle": site["url"],
                          "site_kind": site["kind"], "site_reaches": list(home["reaches"]),
                          "site_via": "bridgy_fed" if site["kind"] == "bridgy_fed_web" else "native"})
    by_id = {p["id"]: p for p in presences}
    for name in list(setup.get("bridged", [])) + list(bridged):
        target = by_id.get(name) or next((p for p in presences if p["platform"] == name), None)
        if target is None:
            raise ValidationError(f"--bridged {name}: no such account id or platform among your presences")
        protocol = entries.get(target["platform"], {}).get("protocol")
        if target["platform"] == "site":
            raise ValidationError("Bridge a web site with --site-kind bridgy_fed_web, not --bridged")
        if protocol not in ("activitypub", "atproto"):
            raise ValidationError(f"--bridged {name}: Bridgy Fed supports the web, the fediverse and Bluesky "
                                  "(docs checked 2026-10-21), not this platform")
        target["bridgy_fed"] = True
    for name in list(setup.get("fediverse_sharing", [])) + list(fediverse_sharing):
        target = by_id.get(name) or next((p for p in presences if p["platform"] == name), None)
        if target is None or entries.get(target["platform"], {}).get("federation") != "opt_in":
            raise ValidationError(f"--fediverse-sharing {name}: not an account on a platform with opt-in "
                                  "federation (Threads)")
        target["fediverse_sharing"] = True
    rows: list[dict] = []
    for p in presences:
        entry = entries.get(p["platform"])
        p["label"] = "Owned site" if p["platform"] == "site" else labels.get(p["platform"], p["platform"])
        reach = _presence_reach(p, entry, networks)
        rows.append({
            "id": p["id"], "platform": p["platform"], "label": p["label"], "source": p["source"],
            "handle": p.get("handle"),
            "protocol": ("web" if p["platform"] == "site" else entry["protocol"] if entry else NO_PROTOCOL),
            "federation": (p.get("site_kind") if p["platform"] == "site" else entry["federation"] if entry else None),
            "federation_note": (homes[p["site_kind"]]["label"] if p["platform"] == "site" else entry["federation_note"]
                                if entry else "No open protocol recorded in the registry; not a verified absence."),
            "bridged_via": ["bridgy_fed"] if p.get("bridgy_fed") or p.get("site_via") == "bridgy_fed" else [],
            "fediverse_sharing": bool(p.get("fediverse_sharing")) if entry and entry["federation"] == "opt_in" else None,
            "reaches": reach,
            "sources": [s["url"] for s in (entry or {}).get("sources", [])] or
                       [s["url"] for s in homes.get(p.get("site_kind", ""), {}).get("sources", [])],
        })
    by_protocol: dict[str, list[str]] = {}
    by_network: dict[str, list[str]] = {}
    for row in rows:
        by_protocol.setdefault(row["protocol"], []).append(row["id"])
        for hop in row["reaches"]:
            by_network.setdefault(hop["network"], []).append(row["id"])
    warnings = bridge_duplicate_risk(presences, data)
    identity = _identity(rows, setup.get("identity", {}), checks, entries)
    gaps = _gaps(rows, by_network)
    return {
        "schema": PLAN_SCHEMA,
        "status": "OFFLINE_OPEN_SOCIAL_WEB_PLAN",
        "registry_reviewed_on": data["reviewed_on"],
        "accounts_file": accounts_file,
        "presences": rows,
        "by_protocol": by_protocol,
        "by_network": by_network,
        "canonical_home": _canonical_home(rows, data["canonical_home_policy"]),
        "duplicate_reach_warnings": warnings,
        "identity_checklist": identity,
        "gaps": gaps,
        "evidence_note": ("Protocol, federation, bridge and identity facts quote official pages (see each source). "
                          "Presences, bridges, sharing and identity status are the owner's own statements, not "
                          "verified account access. Canonical home and duplicate reach are editorial hypotheses."),
        "network_requests": False,
        "account_access": False,
        "queue_touched": False,
        "posts_created": 0,
        "publishing_approval_granted": False,
    }


def _labels() -> dict[str, str]:
    from .distribution import load_registry as load_distribution
    return {p["id"]: p["label"] for p in load_distribution()["platforms"]}


def _canonical_home(rows: list[dict], policy: Mapping) -> dict:
    site = next((r for r in rows if r["platform"] == "site"), None)
    ghost = next((r for r in rows if r["platform"] == "ghost"), None)
    newsletter = next((r for r in rows if r["platform"] in NEWSLETTER_IDS), None)
    open_account = next((r for r in rows if r["reaches"] and r["platform"] != "site"), None)
    if site and site["federation"] in ("ghost", "wordpress_activitypub", "bridgy_fed_web"):
        kind, target = "owned_activitypub_site", site
        step = "Publish the full piece there first, then share the link from your other accounts."
    elif ghost:
        kind, target = "ghost_publication", ghost
        step = "Publish on Ghost first; with Ghost 6 ActivityPub on, fediverse readers can follow it directly."
    elif site:
        kind, target = "owned_site", site
        step = ("Publish there first. To make the site followable from the fediverse, consider Ghost 6, the WordPress "
                "ActivityPub plugin or Bridgy Fed for web sites (all opt-in; your choice).")
    elif newsletter:
        kind, target = "newsletter", newsletter
        step = "Publish on your newsletter first; no open protocol is recorded for it here, so also consider an owned site."
    elif open_account:
        kind, target = "open_protocol_account", open_account
        step = ("No owned site or newsletter is recorded. Until you have one, treat this open-protocol account as home; "
                "a domain handle (Bluesky) or verified link (Mastodon) ties it to an address you control.")
    else:
        return {"status": policy["status"], "recommendation": "none", "target": None,
                "next_step": "Record an owned site or newsletter (--site URL) or an open-protocol account.",
                "why": policy["rationale"]}
    return {"status": policy["status"], "recommendation": kind, "target": target["id"],
            "target_handle": target.get("handle"), "next_step": step, "why": policy["rationale"]}


def _identity(rows: list[dict], reported: Mapping[str, bool], checks: Mapping[str, Mapping],
              entries: Mapping[str, Mapping]) -> list[dict]:
    items: list[dict] = []
    site = next((r for r in rows if r["platform"] == "site"), None)
    # Accounts a fediverse:creator tag can name: their own fediverse presence, not a bridged mirror.
    fedi = [r["id"] for r in rows if r["platform"] != "site" and any(
        h["network"] == "fediverse" and h["via"] != "bridgy_fed" for h in r["reaches"])]

    def add(check_id: str, applies: list[str], status: str, extra: str = "") -> None:
        check = checks[check_id]
        items.append({"id": check_id, "label": check["label"], "applies_to": applies, "status": status,
                      "action": check["action"] + (" " + extra if extra else ""),
                      "source": check["sources"][0]["url"]})

    if site and fedi:
        add("fediverse_creator", fedi,
            "reported_done" if reported.get("fediverse_creator") else "todo")
    elif fedi:
        add("fediverse_creator", fedi, "todo_needs_site",
            "Record your site (--site) so this applies to your own pages.")
    mastodon = [r["id"] for r in rows if r["platform"] == "mastodon"]
    if mastodon:
        add("mastodon_link_verification", mastodon,
            "reported_done" if reported.get("mastodon_link_verification") else "todo")
    for row in rows:
        if row["platform"] != "bluesky":
            continue
        handle = (row.get("handle") or "").lstrip("@").lower()
        if not handle:
            status = "unknown"
        elif handle.endswith(".bsky.social"):
            status = "todo"
        else:
            status = "looks_done_not_verified"
        add("bluesky_domain_handle", [row["id"]], status)
    for row in rows:
        if "bridgy_fed" in row["bridged_via"] and row["platform"] != "site":
            network = "fediverse" if entries.get(row["platform"], {}).get("protocol") == "atproto" else "Atmosphere"
            items.append({"id": "bridged_identity", "label": "Bridged handle", "applies_to": [row["id"]],
                          "status": "info",
                          "action": (f"In the {network} this account appears under a Bridgy Fed handle (for Bluesky "
                                     "accounts: @[handle]@bsky.brid.gy). Mention it where you list your profiles."),
                          "source": "https://fed.brid.gy/docs"})
    return items


def _gaps(rows: list[dict], by_network: Mapping[str, list[str]]) -> list[dict]:
    gaps: list[dict] = []
    open_networks = [n for n in by_network if n != "web"]
    if not open_networks:
        gaps.append({"id": "no_open_protocol", "message": "No presence on any open protocol (ActivityPub, AT Protocol, "
                     "Nostr, Farcaster). Options: a Mastodon or Bluesky account, Threads fediverse sharing, or an "
                     "ActivityPub-enabled site."})
    if not any(r["platform"] == "site" for r in rows):
        gaps.append({"id": "no_owned_home", "message": "No owned site recorded. Pass --site https://... (and --site-kind) "
                     "so the plan can recommend a canonical home."})
    if "fediverse" in by_network and "atmosphere" not in by_network:
        gaps.append({"id": "no_atmosphere", "message": "You reach the fediverse but not Bluesky. Options: a native "
                     "Bluesky account, or bridge a fediverse account or your site with Bridgy Fed (opt-in)."})
    if "atmosphere" in by_network and "fediverse" not in by_network:
        gaps.append({"id": "no_fediverse", "message": "You reach Bluesky but not the fediverse. Options: a native "
                     "Mastodon (or other ActivityPub) account, or bridge your Bluesky account with Bridgy Fed (opt-in)."})
    for row in rows:
        if row["fediverse_sharing"] is False:
            gaps.append({"id": f"fediverse_sharing_off:{row['id']}",
                         "message": f"{row['label']} ({row['id']}): fediverse sharing is not recorded as on. It is an "
                                    "opt-in account setting (pass --fediverse-sharing ID if you turned it on)."})
    closed = [r["id"] for r in rows if r["protocol"] == NO_PROTOCOL]
    if closed:
        gaps.append({"id": "no_protocol_recorded", "message": "No open protocol recorded for: " + ", ".join(closed)
                     + ". Their followers are reached only natively there."})
    return gaps


def render_text(plan: Mapping) -> str:
    lines = [f"{plan['status']} (registry reviewed {plan['registry_reviewed_on']}; accounts file {plan['accounts_file']})",
             "", "Presences:"]
    for row in plan["presences"]:
        reach = ", ".join(f"{h['network']} ({h['via']})" for h in row["reaches"]) or "no open network"
        lines.append(f"  {row['id']}: {row['label']} | protocol={row['protocol']} | reaches: {reach}")
    home = plan["canonical_home"]
    target = f" -> {home['target']}" if home["target"] else ""
    lines += ["", f"Canonical home ({home['status']}): {home['recommendation']}{target}", f"  {home['next_step']}"]
    lines += ["", "Duplicate reach:"]
    lines += [f"  [{w['severity']}] {w['message']}" for w in plan["duplicate_reach_warnings"]] or ["  none found"]
    lines += ["", "Identity checklist:"]
    lines += [f"  [{i['status']}] {i['label']} ({', '.join(i['applies_to'])})" for i in plan["identity_checklist"]] \
        or ["  nothing applies"]
    lines += ["", "Gaps:"]
    lines += [f"  - {g['message']}" for g in plan["gaps"]] or ["  none"]
    lines += ["", plan["evidence_note"], "No network, credentials, database, queue or publishing operations performed."]
    return "\n".join(lines)
