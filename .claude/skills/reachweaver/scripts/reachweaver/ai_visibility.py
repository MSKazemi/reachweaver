"""AI answer-engine and search visibility of the owner's own pages (``ai-visibility``).

Read-only. It answers one question per page: what does this page and its robots.txt ask each
documented search, AI-training and user-fetch crawler to do, and which page signals do Google and
Bing document for their AI answers? Every crawler meaning comes from ``ai_search_registry.json``
(official URL, checked date and verbatim quote per entry, validated by ``validate_registry``).

Offline (the default) it reads a saved HTML file and/or a saved robots.txt (``--html``,
``--robots``) and opens no connection. Online (explicit ``online=True`` only) it checks the
``canonical_url`` of the owner's SEO briefs, the same consent boundary as ``landing-check``: no
other URL can be fetched. robots.txt is read once per host through ``sources.RobotsCache`` (which
keeps the text), each page is fetched by ``landing.fetch_landing`` (robots.txt honoured for
ReachWeaver's own user agent, redirects inspected and kept on the brief's registrable domain unless
``--allow-origin``, bodies capped), and ``/llms.txt`` is requested once per origin through the same
guard and reported as optional information only.

The robots.txt matcher follows RFC 9309: case-insensitive product-token group match, groups for
the same token merged, the ``*`` group as fallback, the longest matching rule wins and ``allow``
wins a tie. A verdict says what the file asks; it is not proof of how an operator behaves, and
nothing here guarantees crawling, indexing, citation or ranking. Page text is untrusted data: it
is cleaned, truncated and reported, never followed. Nothing is drafted, approved, scheduled or
published, and no database is opened.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .attribution import normalize_destination, normalize_origin
from .landing import (
    HTML_TYPES, MAX_BODY_BYTES, MAX_PAGES, _LandingHTML, _clean, _decode, _load_briefs, fetch_landing,
)
from .policy import ValidationError
from .sources import MAX_ROBOTS_BYTES, RobotsCache

REPORT = "ai_visibility"
REGISTRY_FILE = Path(__file__).with_name("ai_search_registry.json")
DECISION_TYPE = "documented_crawler_controls_not_ranking_or_citation_guarantees"
ROLES = frozenset({"search", "training", "training_and_grounding", "user_fetch"})
ROBOTS_TXT = frozenset({"documented", "may_not_apply", "generally_ignored"})
TIERS = frozenset({"official_docs", "official_blog", "proposal", "standard"})
SIGNAL_STATUSES = frozenset({"documented_control", "informational", "optional_unconfirmed"})
REQUIRED_ROLES = {
    "Googlebot": "search", "OAI-SearchBot": "search", "PerplexityBot": "search", "Bingbot": "search",
    "GPTBot": "training", "Google-Extended": "training_and_grounding",
    "ChatGPT-User": "user_fetch", "Perplexity-User": "user_fetch",
}
LLMS_LABEL = "optional; no major engine has confirmed it reads it"
TOKEN = re.compile(r"^[A-Za-z_-]{1,64}$")
FEDIVERSE_HANDLE = re.compile(r"^@?[A-Za-z0-9_.-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")
ROBOTS_META_NAMES = ("robots", "googlebot", "bingbot")
VALUE_DIRECTIVES = frozenset({"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"})
MAX_JSON_LD_BLOCKS = 20
MAX_JSON_LD_CHARS = 200_000
MAX_TYPES = 20
NOTE = ("AI-visibility review aid. Offline mode reads local files only. --online sends, per brief page, one "
        "GET for the page and, per host, one for robots.txt and one for /llms.txt, all through the landing-check "
        "guard; nothing else. Nothing guarantees crawling, indexing, citation or ranking. No post was drafted, "
        "approved, scheduled or published, and no database was opened.")


# --------------------------------------------------------------------- registry

def _https(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and not (parts.username or parts.password)


def _evidence_errors(where: str, evidence: object, reviewed: date | None) -> list[str]:
    if not isinstance(evidence, list) or not evidence:
        return [f"{where}: evidence must be a non-empty list"]
    errors = []
    for item in evidence:
        if not isinstance(item, dict):
            errors.append(f"{where}: evidence entry must be an object")
            continue
        if not _https(item.get("source_url")):
            errors.append(f"{where}: evidence needs an https source_url")
        quote = item.get("quote")
        if not isinstance(quote, str) or not 0 < len(quote.strip()) <= 400:
            errors.append(f"{where}: evidence needs a verbatim quote of 1-400 characters")
        if item.get("tier") not in TIERS:
            errors.append(f"{where}: evidence tier must be one of {sorted(TIERS)}")
        try:
            checked = date.fromisoformat(item["checked_on"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{where}: evidence checked_on must be an ISO 8601 date")
            continue
        if reviewed and checked > reviewed:
            errors.append(f"{where}: checked_on cannot be later than reviewed_on")
    return errors


def validate_registry(registry: object) -> list[str]:
    """Contract, provenance and safety invariants of the bundled AI-search registry."""
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return ["schema_version must be 1"]
    errors: list[str] = []
    reviewed: date | None = None
    try:
        reviewed = date.fromisoformat(registry["reviewed_on"])
    except (KeyError, TypeError, ValueError):
        errors.append("reviewed_on must be an ISO 8601 date")
    if registry.get("decision_type") != DECISION_TYPE:
        errors.append("decision_type must disclaim ranking and citation guarantees")
    if registry.get("citation_guarantee") is not False:
        errors.append("citation_guarantee must be false: no control guarantees an AI citation")
    errors += _evidence_errors("limitation", [registry.get("limitation")], reviewed)
    crawlers = registry.get("crawlers")
    if not isinstance(crawlers, list) or not crawlers:
        return errors + ["crawlers must be a non-empty list"]
    seen: dict[str, dict] = {}
    for crawler in crawlers:
        if not isinstance(crawler, dict) or not isinstance(crawler.get("token"), str) \
                or not TOKEN.fullmatch(crawler["token"]):
            errors.append("crawler entry needs a robots.txt product token")
            continue
        token = crawler["token"]
        if token.casefold() in {t.casefold() for t in seen}:
            errors.append(f"duplicate crawler token: {token}")
        seen[token] = crawler
        if crawler.get("role") not in ROLES:
            errors.append(f"{token}: role must be one of {sorted(ROLES)}")
        if crawler.get("robots_txt") not in ROBOTS_TXT:
            errors.append(f"{token}: robots_txt must be one of {sorted(ROBOTS_TXT)}")
        for key in ("operator", "surface", "if_blocked"):
            if not isinstance(crawler.get(key), str) or not crawler[key].strip():
                errors.append(f"{token}: {key} must be a non-empty string")
        errors += _evidence_errors(token, crawler.get("evidence"), reviewed)
    for token, role in REQUIRED_ROLES.items():
        if token not in seen:
            errors.append(f"{token}: required crawler missing")
        elif seen[token].get("role") != role:
            errors.append(f"{token}: role must stay {role}")
    if "Google-Extended" in seen and not any(
            "does not impact a site's inclusion in Google Search" in e.get("quote", "")
            for e in seen["Google-Extended"].get("evidence", []) if isinstance(e, dict)):
        errors.append("Google-Extended must cite that it does not affect Google Search inclusion")
    for token in ("ChatGPT-User", "Perplexity-User"):
        if token in seen and seen[token].get("robots_txt") == "documented":
            errors.append(f"{token}: user-initiated fetches are not documented as obeying robots.txt")
    signals = registry.get("page_signals")
    if not isinstance(signals, list) or not signals:
        return errors + ["page_signals must be a non-empty list"]
    ids: set[str] = set()
    for signal in signals:
        sid = signal.get("id") if isinstance(signal, dict) else None
        if not isinstance(sid, str) or sid in ids:
            errors.append("page signal needs a unique id")
            continue
        ids.add(sid)
        if signal.get("status") not in SIGNAL_STATUSES:
            errors.append(f"{sid}: status must be one of {sorted(SIGNAL_STATUSES)}")
        if not isinstance(signal.get("meaning"), str) or not signal["meaning"].strip():
            errors.append(f"{sid}: meaning must be a non-empty string")
        errors += _evidence_errors(sid, signal.get("evidence"), reviewed)
    for sid in ("noindex", "nosnippet", "max_snippet", "noarchive_nocache", "structured_data",
                "fediverse_creator", "llms_txt"):
        if sid not in ids:
            errors.append(f"{sid}: required page signal missing")
    llms = next((s for s in signals if isinstance(s, dict) and s.get("id") == "llms_txt"), {})
    if llms and (llms.get("status") != "optional_unconfirmed" or llms.get("affects_ranking") is not False
                 or llms.get("label") != LLMS_LABEL):
        errors.append(f"llms_txt must stay optional_unconfirmed, affects_ranking false, label {LLMS_LABEL!r}")
    measurement = registry.get("measurement")
    if not isinstance(measurement, list) or not measurement:
        return errors + ["measurement must be a non-empty list"]
    for item in measurement:
        mid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(mid, str) or not isinstance(item.get("label"), str):
            errors.append("measurement entry needs id and label")
            continue
        errors += _evidence_errors(mid, item.get("evidence"), reviewed)
    return errors


def load_registry(path: Path | None = None) -> dict:
    """The bundled, validated AI-search research snapshot."""
    try:
        registry = json.loads((path or REGISTRY_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("AI-search registry unavailable") from exc
    errors = validate_registry(registry)
    if errors:
        raise ValidationError("Invalid AI-search registry: " + "; ".join(errors))
    return registry


# --------------------------------------------------------------------- robots.txt (RFC 9309)

def parse_robots(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    """Groups of (lowercased user-agent values, [(allow, path pattern)]). Other lines are ignored."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    in_rules = False
    for raw in text[:MAX_ROBOTS_BYTES].splitlines():
        line = raw.split("#", 1)[0].strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules:
                groups.append((agents, rules))
                agents, rules, in_rules = [], [], False
            agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            if value:  # an empty disallow matches nothing (RFC 9309 2.2.2)
                rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))
    return groups


def _pattern_matches(pattern: str, path: str) -> bool:
    """RFC 9309 path match with ``*`` and a trailing ``$``; leftmost-find, so no regex backtracking."""
    anchored = pattern.endswith("$")
    parts = (pattern[:-1] if anchored else pattern).split("*")
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    if len(parts) == 1:
        return pos == len(path) if anchored else True
    for part in parts[1:-1]:
        found = path.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    last = parts[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= pos
    return path.find(last, pos) >= 0


def robots_verdict(groups: list, token: str, path: str) -> dict:
    """What robots.txt asks ``token`` to do for ``path``: group used, allowed, deciding rule."""
    wanted = token.lower()
    named = [rules for agents, rules in groups if wanted in agents]
    wildcard = [rules for agents, rules in groups if "*" in agents]
    chosen = named or wildcard
    group = "named" if named else "wildcard" if wildcard else "none"
    best: tuple[int, bool, str] | None = None
    for rules in chosen:
        for allow, pattern in rules:
            if _pattern_matches(pattern, path):
                candidate = (len(pattern), allow, pattern)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and allow and not best[1]):
                    best = candidate
    allowed = True if best is None else best[1]
    rule = None if best is None else f"{'allow' if best[1] else 'disallow'}: {best[2]}"
    return {"group": group, "mentioned": bool(named), "allowed": allowed, "rule": rule}


def _robots_path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


# --------------------------------------------------------------------- page parsing

class _AIPageHTML(_LandingHTML):
    """landing's head/meta parser plus robots meta by name, JSON-LD blocks and data-nosnippet."""

    def __init__(self) -> None:
        super().__init__()
        self.robots_by_name: dict[str, list[str]] = {}
        self.json_ld: list[str] = []
        self._ld: list[str] | None = None
        self._ld_len = 0
        self.data_nosnippet = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs if isinstance(k, str)}
        if "data-nosnippet" in a:
            self.data_nosnippet += 1
        if tag == "meta":
            name = a.get("name", "").strip().lower()
            if name in ROBOTS_META_NAMES:
                self.robots_by_name.setdefault(name, []).append(a.get("content", ""))
        if (tag == "script" and a.get("type", "").split(";")[0].strip().lower() == "application/ld+json"
                and len(self.json_ld) < MAX_JSON_LD_BLOCKS):
            self._ld, self._ld_len = [], 0
        super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.json_ld.append("".join(self._ld))
            self._ld = None
        super().handle_endtag(tag)

    def handle_data(self, data):
        if self._ld is not None and self._ld_len < MAX_JSON_LD_CHARS:
            self._ld.append(data)
            self._ld_len += len(data)
        super().handle_data(data)


def parse_directives(values: list[str]) -> dict[str, str | None]:
    """Robots meta / X-Robots-Tag rules as {rule: value or None}; ``none`` expands to noindex, nofollow."""
    found: dict[str, str | None] = {}
    for value in values:
        for part in value.split(","):
            name, sep, arg = part.strip().partition(":")
            name = name.strip().lower()
            if not name:
                continue
            if name == "none":
                found.setdefault("noindex", None)
                found.setdefault("nofollow", None)
                continue
            found.setdefault(name, arg.strip() if sep and name in VALUE_DIRECTIVES else None)
    return found


def _header_directives(header: str | None) -> dict[str, list[str]]:
    """X-Robots-Tag values by agent ("robots" when unscoped); ``googlebot: noindex`` is scoped."""
    scoped: dict[str, list[str]] = {}
    if not header:
        return scoped
    agent = "robots"
    for part in header.split(","):
        head, sep, rest = part.strip().partition(":")
        if sep and head.strip().lower() in ("googlebot", "bingbot"):
            agent, part = head.strip().lower(), rest
        scoped.setdefault(agent, []).append(part)
    return scoped


def _json_ld_types(blocks: list[str]) -> tuple[list[str], int]:
    """(@type values found, number of blocks that do not parse as JSON)."""
    types: list[str] = []
    invalid = 0

    def visit(node: object, depth: int = 0) -> None:
        if depth > 4 or len(types) >= MAX_TYPES:
            return
        if isinstance(node, list):
            for item in node[:50]:
                visit(item, depth + 1)
        elif isinstance(node, dict):
            kind = node.get("@type")
            for value in (kind if isinstance(kind, list) else [kind]):
                cleaned = _clean(value) if isinstance(value, str) else None
                if cleaned and cleaned not in types and len(types) < MAX_TYPES:
                    types.append(cleaned)
            if "@graph" in node:
                visit(node["@graph"], depth + 1)

    for block in blocks:
        try:
            visit(json.loads(block))
        except (json.JSONDecodeError, RecursionError, ValueError):
            invalid += 1
    return types, invalid


def _finding(code: str, severity: str, message: str, **extra) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


def _signal(registry: dict, sid: str) -> dict:
    return next(s for s in registry["page_signals"] if s["id"] == sid)


def _source(entry: dict) -> str:
    return entry["evidence"][0]["source_url"]


def analyze_page(html: str, url: str, registry: dict, x_robots_tag: str | None = None) -> tuple[dict, list[dict]]:
    """Page-level signals and findings for one HTML document whose address is ``url``."""
    parser = _AIPageHTML()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        pass  # malformed markup: keep what was parsed
    findings: list[dict] = []
    title = _clean(" ".join("".join(parser.title_parts).split()))
    description = _clean(parser.meta.get("description"))
    if not title:
        findings.append(_finding("title_missing", "warning", "page has no <title>"))
    if not description:
        findings.append(_finding("meta_description_missing", "warning", "page has no meta description"))
    # canonical
    try:
        expected = normalize_destination(url)
    except ValidationError:
        expected = None
    declared: list[str] = []
    for href in parser.canonicals:
        try:
            declared.append(normalize_destination(urljoin(url, href)))
        except ValidationError:
            findings.append(_finding("canonical_link_invalid", "warning",
                                     "<link rel=canonical> is not a public https URL", href=_clean(href)))
    if not parser.canonicals:
        findings.append(_finding("canonical_link_missing", "warning", "page has no <link rel=canonical>"))
    elif len(set(declared)) > 1:
        findings.append(_finding("canonical_link_multiple", "warning",
                                 "page declares more than one distinct <link rel=canonical>"))
    if declared and expected and expected not in declared:
        findings.append(_finding("canonical_mismatch", "warning",
                                 f"<link rel=canonical> is {declared[0]}, not {expected}", declared=declared[0]))
    # robots meta and X-Robots-Tag, per engine
    by_name = {name: list(values) for name, values in parser.robots_by_name.items()}
    for agent, values in _header_directives(x_robots_tag).items():
        by_name.setdefault(agent, []).extend(values)
    general = by_name.get("robots", [])
    google = parse_directives(general + by_name.get("googlebot", []))
    bing = parse_directives(general + by_name.get("bingbot", []))
    if "noindex" in google or "noindex" in bing:
        engines = [n for n, d in (("Google", google), ("Bing", bing)) if "noindex" in d]
        findings.append(_finding("noindex", "warning", f"noindex ({', '.join(engines)}). "
                                 + _signal(registry, "noindex")["meaning"],
                                 source=_source(_signal(registry, "noindex"))))
    if "nosnippet" in google:
        findings.append(_finding("nosnippet", "warning", "nosnippet: " + _signal(registry, "nosnippet")["meaning"],
                                 source=_source(_signal(registry, "nosnippet"))))
    if google.get("max-snippet") is not None:
        findings.append(_finding("max_snippet", "info", f"max-snippet:{_clean(google['max-snippet'])}: "
                                 + _signal(registry, "max_snippet")["meaning"],
                                 source=_source(_signal(registry, "max_snippet"))))
    if {"noarchive", "nocache"} & set(bing):
        signal = _signal(registry, "noarchive_nocache")
        findings.append(_finding("bing_noarchive_nocache", "info",
                                 f"{'/'.join(sorted({'noarchive', 'nocache'} & set(bing)))} for Bing: "
                                 + signal["meaning"], source=_source(signal)))
    # structured data
    types, invalid = _json_ld_types(parser.json_ld)
    if invalid:
        findings.append(_finding("json_ld_invalid", "warning",
                                 f"{invalid} JSON-LD block(s) do not parse as JSON; tools cannot read them"))
    if not parser.json_ld:
        signal = _signal(registry, "structured_data")
        findings.append(_finding("structured_data_missing", "info",
                                 "no JSON-LD structured data. " + signal["meaning"], source=_source(signal)))
    # fediverse:creator
    creator = _clean(parser.meta.get("fediverse:creator"))
    signal = _signal(registry, "fediverse_creator")
    if not creator:
        findings.append(_finding("fediverse_creator_missing", "info",
                                 "no <meta name=\"fediverse:creator\">. " + signal["meaning"], source=_source(signal)))
    elif not FEDIVERSE_HANDLE.fullmatch(creator):
        findings.append(_finding("fediverse_creator_invalid", "warning",
                                 "fediverse:creator should be a handle like @user@server.example",
                                 value=creator, source=_source(signal)))
    page = {"title": title, "meta_description": description,
            "canonical_link": _clean(parser.canonicals[0]) if parser.canonicals else None,
            "robots": {"google": sorted(google), "bing": sorted(bing)},
            "max_snippet": _clean(google.get("max-snippet")) if google.get("max-snippet") is not None else None,
            "data_nosnippet_elements": parser.data_nosnippet,
            "json_ld": {"blocks": len(parser.json_ld), "invalid": invalid, "types": types},
            "fediverse_creator": creator}
    return page, findings


# --------------------------------------------------------------------- crawler rules

def crawler_report(registry: dict, url: str, robots_state: str, robots_text: str | None) -> tuple[list[dict], list[dict]]:
    """Per-crawler verdicts for ``url`` and the findings they raise.

    ``robots_state``: ``ok`` (``robots_text`` given), ``absent`` (HTTP 4xx: no rules, RFC 9309 2.3.1.3),
    ``unreachable`` (5xx/network: complete disallow, RFC 9309 2.3.1.4) or ``not_checked``.
    """
    groups = parse_robots(robots_text) if robots_state == "ok" and robots_text is not None else []
    path = _robots_path(url)
    rows: list[dict] = []
    findings: list[dict] = []
    for crawler in registry["crawlers"]:
        row = {key: crawler[key] for key in ("token", "operator", "role", "surface", "robots_txt")}
        row["source"] = _source(crawler)
        if robots_state == "not_checked":
            row.update(group=None, mentioned=None, allowed=None, rule=None, status="not_checked")
            rows.append(row)
            continue
        if robots_state == "unreachable":
            verdict = {"group": "none", "mentioned": False, "allowed": False,
                       "rule": "robots.txt unreachable: complete disallow (RFC 9309 2.3.1.4)"}
        else:
            verdict = robots_verdict(groups, crawler["token"], path)
        row.update(verdict)
        state = "allowed" if verdict["allowed"] else "blocked"
        row["status"] = state if verdict["mentioned"] else f"not_mentioned_{state}"
        rows.append(row)
        if verdict["allowed"]:
            continue
        where = f"robots.txt blocks {crawler['token']} for this URL ({verdict['rule']}). "
        if crawler["role"] == "search":
            findings.append(_finding("search_crawler_blocked", "warning", where + crawler["if_blocked"],
                                     crawler=crawler["token"], source=row["source"]))
        elif crawler["role"] == "user_fetch":
            findings.append(_finding("user_fetch_blocked", "info", where + crawler["if_blocked"],
                                     crawler=crawler["token"], source=row["source"]))
        else:
            findings.append(_finding("training_crawler_blocked", "info",
                                     where + crawler["if_blocked"] + " Your choice; not a search fault.",
                                     crawler=crawler["token"], source=row["source"]))
    if robots_state == "unreachable":
        findings.append(_finding("robots_unreachable", "warning",
                                 "robots.txt could not be fetched (server or network error); RFC 9309 tells "
                                 "crawlers to assume complete disallow until it is reachable again"))
    return rows, findings


def _llms_entry(registry: dict, state: str) -> dict:
    signal = _signal(registry, "llms_txt")
    return {"state": state, "label": signal["label"], "affects_ranking": False, "source": _source(signal),
            "meaning": signal["meaning"]}


# --------------------------------------------------------------------- entry points

def _read_capped(path: Path, limit: int, what: str) -> str:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise ValidationError(f"Could not read the {what} file") from exc
    if len(raw) > limit:
        raise ValidationError(f"The {what} file is larger than {limit} bytes")
    return raw.decode("utf-8", errors="replace")


def _status(findings: list[dict]) -> str:
    if any(f["severity"] == "error" for f in findings):
        return "error"
    return "warning" if any(f["severity"] == "warning" for f in findings) else "ok"


def _result(mode: str, pages: list[dict], registry: dict) -> dict:
    for page in pages:
        page["status"] = _status(page["findings"])
    findings = [f for page in pages for f in page["findings"]]
    summary = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in ("error", "warning", "info")}
    summary["pages"] = len(pages)
    measurement = [{"id": m["id"], "label": m["label"], "source": _source(m),
                    "checked_on": m["evidence"][0]["checked_on"]} for m in registry["measurement"]]
    limitation = registry["limitation"]
    return {"report": REPORT, "mode": mode, "status": _status(findings), "summary": summary, "pages": pages,
            "measurement": measurement, "registry_reviewed_on": registry["reviewed_on"],
            "limitation": {"quote": limitation["quote"], "source": limitation["source_url"],
                           "checked_on": limitation["checked_on"]},
            "note": NOTE}


def _offline_url(url: str | None, briefs: Path | None, page_ids: list[str] | None) -> tuple[str, str]:
    """(page_id, url) for offline mode: ``--url`` or one brief's canonical_url."""
    if url is not None and briefs is not None:
        raise ValidationError("Use --url or --briefs, not both")
    if url is not None:
        return "page", normalize_destination(url)
    if briefs is None:
        raise ValidationError("Offline mode needs the page URL: --url URL or --briefs FILE [--page-id ID]")
    _, pages, _ = _load_briefs(Path(briefs))
    if page_ids:
        pages = [p for p in pages if p["page_id"] in page_ids]
    if len(pages) != 1:
        raise ValidationError("Offline mode checks one page: pick it with --page-id")
    page = pages[0]
    if any(f["severity"] == "error" for f in page["findings"]) or not page["canonical_url"]:
        raise ValidationError(f"Brief {page['page_id']} has no valid canonical_url (run landing-check)")
    return page["page_id"], page["canonical_url"]


def ai_visibility(*, briefs: Path | None = None, page_ids: list[str] | None = None, url: str | None = None,
                  html: Path | None = None, robots: Path | None = None, online: bool = False,
                  allow_origins: list[str] | None = None, registry: dict | None = None) -> dict:
    """Offline report from saved files, or (``online=True``) of the briefs' canonical pages."""
    registry = registry or load_registry()
    if online:
        if html is not None or robots is not None or url is not None:
            raise ValidationError("--online checks the briefs' canonical pages; --url, --html and --robots are offline only")
        if briefs is None:
            raise ValidationError("--online needs --briefs FILE: only your briefs' canonical pages are fetched")
        return _online(Path(briefs), page_ids, allow_origins, registry)
    if html is None and robots is None:
        raise ValidationError("Offline mode needs --html FILE and/or --robots FILE (or use --online with --briefs)")
    page_id, page_url = _offline_url(url, briefs, page_ids)
    findings: list[dict] = []
    robots_info: dict[str, str | None]
    if robots is not None:
        rows, crawler_findings = crawler_report(registry, page_url, "ok",
                                                _read_capped(robots, MAX_ROBOTS_BYTES, "robots.txt"))
        robots_info = {"source": "file", "state": "ok"}
    else:
        rows, crawler_findings = crawler_report(registry, page_url, "not_checked", None)
        robots_info = {"source": None, "state": "not_checked"}
    findings += crawler_findings
    page_info = None
    if html is not None:
        page_info, page_findings = analyze_page(_read_capped(html, MAX_BODY_BYTES, "HTML"), page_url, registry)
        findings += page_findings
    entry = {"page_id": page_id, "url": page_url, "robots": robots_info, "crawlers": rows, "page": page_info,
             "llms_txt": _llms_entry(registry, "not_checked"), "findings": findings}
    return _result("offline", [entry], registry)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    origin = f"https://{host}" if parts.port in (None, 443) else f"https://{host}:{parts.port}"
    return origin, f"{origin}/robots.txt"


def _online(briefs: Path, page_ids: list[str] | None, allow_origins: list[str] | None, registry: dict) -> dict:
    _, pages, document = _load_briefs(briefs)
    if page_ids:
        unknown = sorted(set(page_ids) - {p["page_id"] for p in pages})
        if unknown:
            raise ValidationError(f"Unknown --page-id: {', '.join(unknown)}")
        pages = [p for p in pages if p["page_id"] in page_ids]
    else:
        pages = [p for p in pages if p["brief_status"] == "active"]
    allowed = {normalize_origin(o) for o in allow_origins or []}
    cache = RobotsCache()
    llms: dict[str, str] = {}
    entries: list[dict] = []
    budget = MAX_PAGES
    for page in pages:
        url = page["canonical_url"]
        entry: dict[str, Any] = {"page_id": page["page_id"], "url": url, "robots": None, "crawlers": [],
                                 "page": None, "llms_txt": _llms_entry(registry, "not_checked"), "findings": []}
        entries.append(entry)
        if not url or any(f["severity"] == "error" for f in page["findings"]):
            entry["findings"].append(_finding("canonical_url_invalid", "error",
                                              "brief has no valid canonical_url; not fetched (run landing-check)"))
            continue
        if budget <= 0:
            entry["findings"].append(_finding("not_checked_page_limit", "warning",
                                              f"only {MAX_PAGES} pages are checked per run"))
            continue
        budget -= 1
        origin, robots_url = _origin(url)
        cache.skip_reason(url)  # reads robots.txt once per host; the text is kept in cache.documents
        outcome, text = cache.documents.get(robots_url, ("not_checked", None))
        state = "ok" if outcome == "ok" else "absent" if outcome.startswith("http_4") else "unreachable"
        entry["robots"] = {"source": robots_url, "state": state, "fetch": outcome}
        entry["crawlers"], crawler_findings = crawler_report(registry, url, state, text)
        entry["findings"] += crawler_findings
        fetched = fetch_landing(url, robots=cache, allowed_origins=allowed)
        if fetched["error"]:
            entry["findings"].append(_finding("page_not_fetched", "error",
                                              f"page not fetched ({fetched['error']}); page signals not checked",
                                              http_status=fetched["status"], url=fetched["final_url"]))
        else:
            content_type = (fetched["headers"].get("content-type") or "").split(";")[0].strip().lower()
            if content_type and content_type not in HTML_TYPES:
                entry["findings"].append(_finding("not_html", "error", f"response is {content_type}, not HTML"))
            else:
                entry["page"], page_findings = analyze_page(
                    _decode(fetched["body"], fetched["headers"].get("content-type")), url, registry,
                    fetched["headers"].get("x-robots-tag"))
                entry["page"]["final_url"] = fetched["final_url"]
                entry["findings"] += page_findings
        if origin not in llms:
            got = fetch_landing(f"{origin}/llms.txt", robots=cache, allowed_origins=allowed)
            error = got["error"]
            llms[origin] = ("present" if not error else "absent" if error in ("http_404", "http_410")
                            else f"unknown_{error}")
        entry["llms_txt"] = _llms_entry(registry, llms[origin])
    result = _result("online", entries, registry)
    result["document_findings"] = document
    if any(f["severity"] == "error" for f in document):
        result["status"] = "error"
    return result


def exit_code(result: dict) -> int:
    return 1 if result["status"] == "error" else 0


def render_text(result: dict) -> str:
    s = result["summary"]
    lines = [f"ai-visibility ({result['mode']}): {result['status'].upper()} - {s['pages']} page(s), "
             f"{s['error']} error, {s['warning']} warning, {s['info']} info"]
    for page in result["pages"]:
        lines.append(f"  [{page['status']}] {page['page_id']} {page['url']}")
        robots = page.get("robots") or {}
        lines.append(f"    robots.txt: {robots.get('state', 'not_checked')}")
        for row in page["crawlers"]:
            if row["status"] == "not_checked":
                continue
            rule = f" ({row['rule']})" if row.get("rule") else ""
            lines.append(f"      {row['token']:<16} {row['role']:<22} {row['status']}{rule}")
        info = page.get("page")
        if info:
            lines.append(f"    title={info['title']!r} canonical={info['canonical_link']!r} "
                         f"robots(google)={','.join(info['robots']['google']) or '-'}")
            lines.append(f"    JSON-LD types={','.join(info['json_ld']['types']) or '-'} "
                         f"fediverse:creator={info['fediverse_creator']!r}")
        llms = page["llms_txt"]
        lines.append(f"    llms.txt: {llms['state']} ({llms['label']})")
        for f in page["findings"]:
            lines.append(f"    {f['severity'].upper()} {f['code']}: {f['message']}")
    lines.append("Measure with: " + "; ".join(f"{m['label']} ({m['source']})" for m in result["measurement"]))
    limitation = result["limitation"]
    lines.append(f"Limitation (Google, checked {limitation['checked_on']}): \"{limitation['quote']}\"")
    lines.append(result["note"])
    return "\n".join(lines)
