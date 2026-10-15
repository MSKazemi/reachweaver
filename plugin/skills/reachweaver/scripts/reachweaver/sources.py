"""Opt-in source ingestion: rank publishing opportunities from owned/public evidence (issue #9).

Readers normalize GitHub REST JSON (releases, tags, merged pull requests, closed-as-completed
labelled issues), RSS 2.0 / Atom feeds, website changelog pages (HTML ``<article>`` or
``<h2>`` sections), Keep a Changelog style ``CHANGELOG.md`` files and Crossref DOI work
records into ``SourceSignal`` dicts.

Trust boundary:
- Every string read from a fixture or a network response is UNTRUSTED DATA. It is
  stripped of control/format characters and markup, truncated and carried inertly.
  It is never interpreted as an instruction; instruction-like text only lowers
  confidence and adds an uncertainty flag.
- Scanning never drafts, approves, schedules or publishes. The only optional write
  is an owner-chosen local JSON "seen" state file.
- Network access requires explicit ``online=True``: GET only, no redirects, bounded
  reads, sanitized errors. GitHub requests target public repositories (the repo
  metadata must say ``private: false``) unless ALL THREE private opt-ins hold: the
  source sets ``"visibility": "private"`` and ``"sensitivity": "private"`` and the
  caller passes ``allow_private=True`` (``--allow-private``). A private repository
  without them is refused before any list endpoint is read, and nothing from it is
  emitted or stored. Private signals are marked ``private``/``publishable: false``
  (see private_sources.py). An optional ``REACHWEAVER_GITHUB_TOKEN`` is
  sent only to api.github.com and is never printed, logged or persisted. GitHub
  pagination follows RFC 8288 ``rel="next"`` links only while they stay on
  https://api.github.com, and at most ``max_pages`` pages per source.
- Non-API web fetches (feeds, changelog pages/files) first consult the host's
  robots.txt (fetched once per host per run); a disallowed or unreachable robots.txt
  skips the source. API hosts (api.github.com, api.crossref.org) are not crawled.
- XML feeds containing DOCTYPE/ENTITY declarations are rejected before parsing. HTML is
  parsed with ``html.parser`` only; script/style/template content is never read.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import Request
from urllib.robotparser import RobotFileParser

from . import private_sources, publishers
from .accounts import credential
from .campaigns import SLUG, _sensitive_keys, public_url
from .policy import ValidationError

SCHEMA_VERSION = 1
GITHUB_API = "https://api.github.com"
GITHUB_API_HOST = "api.github.com"
CROSSREF_API = "https://api.crossref.org/works/"
RAW_GITHUB_HOST = "raw.githubusercontent.com"
TOKEN_ENV = "REACHWEAVER_GITHUB_TOKEN"
USER_AGENT = "ReachWeaver/0.3 (+https://github.com/MSKazemi/reachweaver)"
ROBOTS_AGENT = "ReachWeaver"
KINDS = frozenset({"github_releases", "github_tags", "github_pulls", "github_issues", "feed", "changelog", "doi"})
GITHUB_KINDS = frozenset({"github_releases", "github_tags", "github_pulls", "github_issues"})
CHANGELOG_FORMATS = frozenset({"markdown", "html"})
SENSITIVITY = frozenset({"public", "private"})
MAX_SOURCES = 50
MAX_ITEMS_PER_SOURCE = 50
MAX_JSON_BYTES = 1_000_000
MAX_FEED_BYTES = 2_000_000
MAX_STATE_BYTES = 5_000_000
MAX_STATE_ENTRIES = 5000
MAX_ROBOTS_BYTES = 512_000
MAX_HEADER_CHARS = 8192
GITHUB_PER_PAGE = 30
DEFAULT_MAX_PAGES = 3
MAX_PAGES_LIMIT = 10
MAX_ISSUE_LABELS = 20
MAX_ENTRY_TEXT = 4000
MAX_CHANGELOG_LINES = 20_000
TITLE_LIMIT = 200
SUMMARY_LIMIT = 500
NEAR_DUPLICATE_RATIO = 0.88
DEFAULT_HALF_LIFE_DAYS = 14.0
DEFAULT_WEIGHTS = {"freshness": 0.35, "novelty": 0.25, "audience_relevance": 0.25, "confidence": 0.15}
BASE_CONFIDENCE = {"github_release": 0.9, "github_pull": 0.85, "github_issue": 0.8, "paper": 0.9,
                   "changelog_entry": 0.75, "feed_item": 0.7, "github_tag": 0.6}
NOTE = ("RESEARCH ONLY. Source text is untrusted data, not instructions. Nothing was drafted, "
        "approved, scheduled or published; verify each claim against its evidence URL before drafting.")
PRIVATE_NOTE = ("PRIVATE SOURCES READ. Signals marked private carry private repository text: keep this "
                "output private, never paste it into a public post, and note that a private repository "
                "URL is never a public link. Campaign evidence from them stays blocked until a person "
                "sets publishable: true on that evidence record.")

REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
DOI = re.compile(r"^10\.\d{4,9}/[^\s\"<>\x00-\x1f\x7f-\x9f]{1,200}$")
MAILTO = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}$")
LINK_VALUE = re.compile(r"<([^<>]{1,2048})>((?:\s*;\s*[A-Za-z*-]{1,30}\s*=\s*(?:\"[^\"]{0,200}\"|[^\s;,\"]{0,200}))*)")
LINK_REL = re.compile(r";\s*rel\s*=\s*(?:\"([^\"]{0,200})\"|([^\s;,\"]{1,200}))", re.I)
ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
MD_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
MD_LINK_REF = re.compile(r"^ {0,3}\[([^\]]{1,200})\]:[ \t]*<?(\S{1,2048}?)>?(?:[ \t]+.*)?$")
MD_INLINE_LINK = re.compile(r"\[([^\]]{0,300})\]\(([^()\s]{0,2048})\)")
MD_BULLET = re.compile(r"^\s{0,8}(?:[-*+]|\d{1,3}[.)])\s+")
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_.-]{20,255}$")
BOT_LOGIN = re.compile(r"(?:\[bot\]$|^dependabot|^renovate)", re.I)
NOISE_LABELS = frozenset({"dependencies", "dependabot", "renovate", "chore", "ci", "documentation", "docs"})
NOISE_TITLE = re.compile(
    r"^\s*(?:(?:chore|ci|docs?|build|style|tests?)(?:\([^)]*\))?!?:|bump\s+\S+\s+from\s|"
    r"merge\s+(?:branch|pull request)\b|update\s+dependenc)", re.I)
PRERELEASE = re.compile(r"[-.](?:alpha|beta|rc|pre|preview|dev)\b", re.I)
INSTRUCTION_LIKE = re.compile(
    r"(?:ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts|rules)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)|system\s+prompt|developer\s+message"
    r"|you\s+are\s+now\b|act\s+as\s+(?:an?\s+)?(?:ai|assistant|admin)"
    r"|(?:approve|publish|schedule|post)\s+(?:this|it|everything|all)\b[^.]{0,60}\b(?:now|immediately|without)"
    r"|REACHWEAVER_ALLOW_LIVE|--live\b|<\s*/?\s*(?:system|assistant|instructions?)\s*>)", re.I)
XML_FORBIDDEN = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)
XML_ENCODING = re.compile(rb"^\s*<\?xml[^>]{0,200}?encoding\s*=\s*[\"']([A-Za-z0-9._-]{1,40})[\"']", re.I)
SAFE_XML_ENCODINGS = frozenset({"utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin-1", "windows-1252"})
ATOM = "{http://www.w3.org/2005/Atom}"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"
TOKEN_WORD = re.compile(r"[a-z0-9]+(?:[-+#.][a-z0-9]+)*[+#]*")


class SourceError(RuntimeError):
    """Sanitized per-source failure: a fixed code, never a URL, header or response body."""


# --------------------------------------------------------------------- text hygiene

def clean_text(value: object, limit: int) -> tuple[str, bool]:
    """Return inert single-line text: markup/entities removed, Cc/Cf chars dropped, truncated."""
    if not isinstance(value, str):
        return "", False
    text = re.sub(r"<[^>]{0,2000}>", " ", value[:200_000])
    text = re.sub(r"<[^>]{0,2000}>", " ", html.unescape(text))  # entity-encoded markup
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs", "Co") else ch for ch in text)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit - 1].rstrip() + "…", True
    return text, False


def _tokens(text: str) -> list[str]:
    return TOKEN_WORD.findall(text.casefold())


def near_duplicate(a: str, b: str) -> bool:
    """Near-identical titles; titles that differ in any number (e.g. versions) are distinct."""
    na, nb = " ".join(_tokens(a)), " ".join(_tokens(b))
    if not na or not nb or re.findall(r"\d+", na) != re.findall(r"\d+", nb):
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= NEAR_DUPLICATE_RATIO


def signal_id(kind: str, url: str) -> str:
    return hashlib.sha256(f"{kind}\n{url}".encode("utf-8")).hexdigest()


def _rfc3339(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse_rfc822(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# --------------------------------------------------------------------- bounded I/O

def _fetch_response(url: str, *, max_bytes: int, headers: dict | None = None) -> tuple[bytes, dict[str, str]]:
    """GET returning (body, selected headers) with the same guarantees as ``_fetch``.

    No redirects (publishers opener), bounded read, sanitized error code. Only the
    ``Link``, ``Content-Type`` and ``X-OAuth-Scopes`` (scope names, never a token) headers
    are returned, lowercased and length-bounded;
    a response object without headers (as in mocked tests) yields an empty dict.
    """
    req = Request(url, method="GET", headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with publishers._send_http(req) as response:
            raw = response.read(max_bytes + 1)
            selected = _selected_headers(getattr(response, "headers", None))
    except HTTPError as exc:
        raise SourceError(f"http_{int(exc.code)}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise SourceError("network_or_timeout") from None
    if not isinstance(raw, bytes):
        raise SourceError("unexpected_response")
    if len(raw) > max_bytes:
        raise SourceError("response_too_large")
    return raw, selected


def _selected_headers(headers: object) -> dict[str, str]:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return {}
    selected = {}
    for name in ("Link", "Content-Type", private_sources.SCOPE_HEADER):
        try:
            value = getter(name)
        except Exception:  # noqa: BLE001 - a hostile/odd header object must not break the scan
            value = None
        if isinstance(value, str) and len(value) <= MAX_HEADER_CHARS:
            selected[name.casefold()] = value
    return selected


def _fetch(url: str, *, max_bytes: int, headers: dict | None = None) -> bytes:
    """GET with no redirects (publishers opener), bounded read and a sanitized error code."""
    return _fetch_response(url, max_bytes=max_bytes, headers=headers)[0]


def next_link(value: object) -> str | None:
    """Return the first RFC 8288 ``rel="next"`` target in a ``Link`` header value, else None."""
    if not isinstance(value, str) or len(value) > MAX_HEADER_CHARS:
        return None
    for match in LINK_VALUE.finditer(value):
        for rel in LINK_REL.finditer(match.group(2)):
            relations = (rel.group(1) if rel.group(1) is not None else rel.group(2)).casefold().split()
            if "next" in relations:
                return match.group(1).strip()
    return None


def github_next_allowed(url: str) -> bool:
    """A pagination link may receive the token only if it is https://api.github.com/repos|repositories/..."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname == GITHUB_API_HOST and port in (None, 443)
            and parts.netloc.casefold() in (GITHUB_API_HOST, f"{GITHUB_API_HOST}:443")
            and not parts.fragment and "\\" not in url
            and parts.path.startswith(("/repos/", "/repositories/")) and ".." not in parts.path.split("/"))


class RobotsCache:
    """robots.txt verdicts for non-API web fetches, fetched once per host and cached per run.

    2xx: parsed with ``urllib.robotparser``. 401/403: everything disallowed (robotparser
    convention). Other 4xx: no robots.txt, everything allowed (RFC 9309 2.3.1.3).
    5xx, redirects (never followed), timeouts, oversize: unreachable, so the source is
    skipped conservatively (RFC 9309 2.3.1.4 says assume complete disallow).
    """

    def __init__(self) -> None:
        self._hosts: dict[tuple, RobotFileParser | str] = {}
        self.fetches = 0

    def skip_reason(self, url: str) -> str | None:
        try:
            parts = urlsplit(url)
            host, port = parts.hostname, parts.port
        except ValueError:
            return "robots_unavailable"
        if parts.scheme != "https" or not host:
            return "robots_unavailable"
        key = (host, port or 443)
        if key not in self._hosts:
            self._hosts[key] = self._load(f"https://{host}{'' if port in (None, 443) else f':{port}'}/robots.txt")
        verdict = self._hosts[key]
        if isinstance(verdict, str):
            return verdict
        return None if verdict.can_fetch(ROBOTS_AGENT, url) else "robots_disallowed"

    def _load(self, robots_url: str) -> RobotFileParser | str:
        self.fetches += 1
        parser = RobotFileParser()
        try:
            raw = _fetch(robots_url, max_bytes=MAX_ROBOTS_BYTES, headers={"Accept": "text/plain"})
        except SourceError as exc:
            code = str(exc)
            if code in ("http_401", "http_403"):
                cast(Any, parser).disallow_all = True
                return parser
            if code.startswith("http_4"):
                cast(Any, parser).allow_all = True
                return parser
            return "robots_unavailable"
        parser.parse(raw.decode("utf-8", errors="replace").splitlines())
        return parser


def _web_fetch(url: str, *, max_bytes: int, accept: str, robots: RobotsCache | None) -> bytes:
    """Non-API web GET: consult robots.txt first; never sends credentials."""
    if robots is None:
        raise SourceError("robots_check_required")
    reason = robots.skip_reason(url)
    if reason:
        raise _Skipped(reason)
    return _fetch(url, max_bytes=max_bytes, headers={"Accept": accept})


class _Skipped(Exception):
    """Internal: a source is skipped (not failed), e.g. robots_disallowed."""


def _read_local(path: Path, max_bytes: int) -> bytes:
    try:
        if not path.is_file():
            raise SourceError("fixture_not_found")
        if path.stat().st_size > max_bytes:
            raise SourceError("fixture_too_large")
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        raise SourceError("fixture_unreadable") from None
    if len(raw) > max_bytes:
        raise SourceError("fixture_too_large")
    return raw


def _json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise SourceError("malformed_json") from None


# --------------------------------------------------------------------- readers

def _record(kind, title, url, published, body, evidence, uncertainties) -> dict:
    return {"kind": kind, "title": title, "url": url, "published": published, "body": body,
            "evidence_urls": [u for u in dict.fromkeys(evidence) if public_url(u)],
            "uncertainties": list(uncertainties)}


def _items(data: object, label: str, limit: int = MAX_ITEMS_PER_SOURCE) -> list[dict]:
    if not isinstance(data, list):
        raise SourceError(f"unexpected_{label}_shape")
    return [item for item in data[:limit] if isinstance(item, dict)]


def parse_github_releases(data: object, repo: str, *, include_prereleases: bool = False,
                          limit: int = MAX_ITEMS_PER_SOURCE) -> tuple[list, list]:
    records, skipped = [], []
    for item in _items(data, "releases", limit):
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 200:
            skipped.append({"reason": "release_without_tag"})
            continue
        url = f"https://github.com/{repo}/releases/tag/{quote(tag, safe='')}"
        if item.get("draft"):
            skipped.append({"url": url, "reason": "draft_release_excluded"})
            continue
        if item.get("prerelease") and not include_prereleases:
            skipped.append({"url": url, "reason": "prerelease_excluded"})
            continue
        name = item.get("name") if isinstance(item.get("name"), str) and item["name"].strip() else tag
        published = _parse_iso(item.get("published_at")) or _parse_iso(item.get("created_at"))
        records.append(_record("github_release", f"{repo}: {name}", url, published, item.get("body"),
                               [url, f"https://github.com/{repo}"], []))
    return records, skipped


def parse_github_tags(data: object, repo: str, *, include_prereleases: bool = False,
                      limit: int = MAX_ITEMS_PER_SOURCE) -> tuple[list, list]:
    records, skipped = [], []
    for item in _items(data, "tags", limit):
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            skipped.append({"reason": "tag_without_name"})
            continue
        url = f"https://github.com/{repo}/releases/tag/{quote(name, safe='')}"
        if PRERELEASE.search(name) and not include_prereleases:
            skipped.append({"url": url, "reason": "prerelease_excluded"})
            continue
        records.append(_record("github_tag", f"{repo}: tag {name}", url, None, "", [url],
                               ["tag_api_has_no_date", "tag_has_no_release_notes"]))
    return records, skipped


def _pull_noise(item: dict, title: str) -> str | None:
    user = cast(dict, item.get("user")) if isinstance(item.get("user"), dict) else {}
    login = cast(str, user.get("login")) if isinstance(user.get("login"), str) else ""
    if user.get("type") == "Bot" or BOT_LOGIN.search(login):
        return "bot_author"
    labels = {str(label.get("name", "")).casefold() for label in item.get("labels") or [] if isinstance(label, dict)}
    if labels & NOISE_LABELS:
        return "noise_label"
    if NOISE_TITLE.search(title):
        return "trivial_title"
    return None


def parse_github_pulls(data: object, repo: str, *, force_include: frozenset = frozenset(),
                       limit: int = MAX_ITEMS_PER_SOURCE) -> tuple[list, list]:
    records, skipped = [], []
    for item in _items(data, "pulls", limit):
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            skipped.append({"reason": "pull_without_number"})
            continue
        url = f"https://github.com/{repo}/pull/{number}"
        merged = _parse_iso(item.get("merged_at"))
        if merged is None:
            skipped.append({"url": url, "reason": "not_merged"})
            continue
        title, _ = clean_text(item.get("title"), TITLE_LIMIT)
        noise = None if url in force_include else _pull_noise(item, title)
        if noise:
            skipped.append({"url": url, "reason": noise})
            continue
        records.append(_record("github_pull", f"{repo}#{number}: {title}", url, merged, item.get("body"),
                               [url], []))
    return records, skipped


def parse_github_issues(data: object, repo: str, *, labels: list[str],
                        limit: int = MAX_ITEMS_PER_SOURCE) -> tuple[list, list]:
    """Closed-as-completed issues carrying at least one owner-selected label; PR-shaped items skipped."""
    wanted = {label.strip().casefold() for label in labels}
    records, skipped = [], []
    for item in _items(data, "issues", limit):
        number = item.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            skipped.append({"reason": "issue_without_number"})
            continue
        if "pull_request" in item:  # the issues API also lists pull requests
            skipped.append({"url": f"https://github.com/{repo}/pull/{number}", "reason": "pull_request_not_issue"})
            continue
        url = f"https://github.com/{repo}/issues/{number}"
        if item.get("state") != "closed" or item.get("state_reason") != "completed":
            skipped.append({"url": url, "reason": "not_closed_as_completed"})
            continue
        names = {str(label.get("name", "")).strip().casefold() for label in item.get("labels") or []
                 if isinstance(label, dict)}
        if not names & wanted:
            skipped.append({"url": url, "reason": "label_not_selected"})
            continue
        title, _ = clean_text(item.get("title"), TITLE_LIMIT)
        closed = _parse_iso(item.get("closed_at"))
        records.append(_record("github_issue", f"{repo}#{number}: {title}", url, closed, item.get("body"),
                               [url], [] if closed else ["issue_close_date_unknown"]))
    return records, skipped


# ------------------------------------------------- website / changelog readers

def _anchor_slug(text: str) -> str:
    """GitHub-style heading anchor (best effort): lowercase, punctuation dropped, spaces -> '-'."""
    kept = "".join(ch for ch in text.casefold() if ch.isalnum() or ch in " -_")
    return quote(kept.strip().replace(" ", "-")[:120], safe="-_")


def _page_base(url: str) -> str:
    return url.split("#", 1)[0]


def _entry_record(*, title: str, entry_url: str, page_url: str, published, body: str,
                  uncertainties: list[str]) -> dict:
    # Campaign evidence needs a fragment-free public URL; ``url`` may be page#anchor (identity only).
    evidence = [u for u in (entry_url, page_url) if public_url(u)]
    if entry_url not in evidence:
        uncertainties = [*uncertainties, "entry_url_is_page_anchor"]
    return _record("changelog_entry", title, entry_url, published, body, evidence, uncertainties)


def _md_plain(text: str) -> str:
    return MD_INLINE_LINK.sub(lambda m: m.group(1), text)


def _md_sections(text: str) -> tuple[list[dict], dict[str, str]]:
    sections: list[dict]
    refs: dict[str, str]
    current: dict[str, Any] | None
    sections, refs, current, fence = [], {}, None, None
    for line in text.splitlines()[:MAX_CHANGELOG_LINES]:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else (fence or marker)
            continue
        if fence:
            continue
        ref = MD_LINK_REF.match(line)
        if ref:
            refs.setdefault(ref.group(1).strip().casefold(), ref.group(2))
            continue
        heading = MD_HEADING.match(line)
        if heading and len(heading.group(1)) <= 2:
            current = None
            if len(heading.group(1)) == 2:
                current = {"heading": (heading.group(2) or "").strip()[:500], "body": []}
                sections.append(current)
            continue
        if current is None or not stripped or sum(len(b) for b in current["body"]) >= MAX_ENTRY_TEXT:
            continue
        if heading:  # ### Added / ### Fixed subsections
            current["body"].append(f"{_md_plain(heading.group(2) or '')}:")
        else:
            current["body"].append(_md_plain(MD_BULLET.sub("", stripped)))
    return sections, refs


def _md_version(heading: str) -> tuple[str, str, str | None]:
    """Split ``[1.2.0](url) - date`` / ``[1.2.0] - date`` / ``1.2.0 - date`` into version, rest, url."""
    inline = MD_INLINE_LINK.match(heading)
    if inline:
        return inline.group(1).strip(), heading[inline.end():], inline.group(2)
    if heading.startswith("[") and "]" in heading:
        end = heading.index("]")
        return heading[1:end].strip(), heading[end + 1:], None
    version, _, rest = heading.partition(" ")
    return version.strip(), rest, None


def parse_changelog_markdown(raw: bytes, *, page_url: str, project: str) -> tuple[list, list]:
    """Keep a Changelog style: each ``## [version] - YYYY-MM-DD`` heading is one entry.

    Plain markdown line rules only: ATX headings, fenced code skipped, and link reference
    definitions (``[1.2.0]: https://...``) used as the per-version evidence URL.
    """
    if b"\x00" in raw:
        raise SourceError("unsupported_changelog_encoding")
    sections, refs = _md_sections(raw.decode("utf-8-sig", errors="replace"))
    if not sections:
        raise SourceError("no_changelog_entries_found")
    base = _page_base(page_url)
    records, skipped = [], []
    for section in sections[:MAX_ITEMS_PER_SOURCE]:
        version, rest, inline_url = _md_version(section["heading"])
        if not version:
            skipped.append({"reason": "changelog_heading_without_version"})
            continue
        if version.casefold() == "unreleased":
            skipped.append({"reason": "unreleased_section_skipped"})
            continue
        if "yanked" in rest.casefold():
            skipped.append({"reason": "yanked_release_skipped"})
            continue
        date = ISO_DATE.search(rest)
        published = _parse_iso(date.group(1)) if date else None
        per_entry = next((u for u in (inline_url, refs.get(version.casefold())) if public_url(u)), None)
        entry_url = per_entry or f"{base}#{_anchor_slug(_md_plain(section['heading']))}"
        records.append(_entry_record(
            title=f"{project}: {_md_plain(version)}", entry_url=entry_url, page_url=base, published=published,
            body=" ".join(section["body"]),
            uncertainties=[] if published else ["changelog_entry_date_unparseable"]))
    return records, skipped


class _ChangelogHTML(HTMLParser):
    """Collects ``<article>`` entries and ``<h2>`` sections; script/style/template text is ignored."""

    IGNORED = frozenset({"script", "style", "template", "noscript", "svg", "math", "iframe", "object"})
    HEADINGS = frozenset({"h1", "h2", "h3", "h4"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: list[dict] = []
        self.sections: list[dict] = []
        self.ignored_depth = 0
        self.article_depth = 0
        self.article_heading: str | None = None
        self.section_heading = False
        self.section: dict | None = None

    @staticmethod
    def _entry(values: dict) -> dict:
        return {"title": [], "body": [], "href": None, "id": values.get("id"), "datetime": None}

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORED:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        values = dict(attrs)
        if tag == "article":
            if self.article_depth == 0 and len(self.articles) < MAX_ITEMS_PER_SOURCE:
                self.articles.append(self._entry(values))
            self.article_depth += 1
        article = self.articles[-1] if self.article_depth and self.articles else None
        if (tag in self.HEADINGS and article is not None and not article["title"]
                and self.article_heading is None):
            self.article_heading = tag
            article["id"] = article["id"] or values.get("id")
        if tag == "h2" and len(self.sections) < MAX_ITEMS_PER_SOURCE:
            self.section = self._entry(values)
            self.sections.append(self.section)
            self.section_heading = True
        elif tag in ("h1", "h2"):
            self.section = None  # a page title, or sections beyond the cap, end the current section
        if tag == "a" and isinstance(values.get("href"), str):
            if article is not None and self.article_heading and article["href"] is None:
                article["href"] = values["href"]
            if self.section is not None and self.section_heading and self.section["href"] is None:
                self.section["href"] = values["href"]
        if tag == "time" and isinstance(values.get("datetime"), str):
            for entry in (article, self.section):
                if entry is not None and entry["datetime"] is None:
                    entry["datetime"] = values["datetime"][:64]

    def handle_endtag(self, tag):
        if tag in self.IGNORED:
            self.ignored_depth = max(0, self.ignored_depth - 1)
            return
        if self.ignored_depth:
            return
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag == self.article_heading:
            self.article_heading = None
        if tag == "h2":
            self.section_heading = False

    def handle_data(self, data):
        if self.ignored_depth or not data.strip():
            return
        if self.article_depth and self.articles:
            article = self.articles[-1]
            self._append(article["title"] if self.article_heading else article["body"], data)
        if self.section is not None:
            self._append(self.section["title"] if self.section_heading else self.section["body"], data)

    @staticmethod
    def _append(target: list[str], data: str) -> None:
        if sum(len(x) for x in target) < MAX_ENTRY_TEXT:
            target.append(data)


def _html_entry_url(entry: dict, base: str, title: str) -> str:
    href = entry["href"]
    if isinstance(href, str) and href.strip():
        try:
            candidate = urljoin(base, href.strip())
            same_host = urlsplit(candidate).hostname == urlsplit(base).hostname
        except ValueError:
            candidate, same_host = "", False
        if same_host and public_url(candidate):
            return candidate  # off-host heading links are ignored, never followed
    ident = entry["id"]
    if isinstance(ident, str) and ident.strip():
        return f"{base}#{quote(ident.strip()[:120], safe='-_.')}"
    return f"{base}#{_anchor_slug(title)}"


def parse_changelog_html(raw: bytes, *, page_url: str) -> tuple[list, list]:
    """Website changelog/news page: ``<article>`` entries, else ``<h2>`` sections (html.parser only)."""
    if b"\x00" in raw:
        raise SourceError("unsupported_changelog_encoding")
    parser = _ChangelogHTML()
    try:
        parser.feed(raw.decode("utf-8", errors="replace"))
        parser.close()
    except (ValueError, AssertionError, RecursionError):
        raise SourceError("malformed_html") from None
    entries = parser.articles or parser.sections
    if not entries:
        raise SourceError("no_changelog_entries_found")
    base = _page_base(page_url)
    records, skipped = [], []
    for entry in entries:
        title = " ".join(" ".join(entry["title"]).split())
        if not title:
            skipped.append({"reason": "entry_without_heading"})
            continue
        body = " ".join(" ".join(entry["body"]).split())
        published = _parse_iso(entry["datetime"])
        if published is None:
            date = ISO_DATE.search(title) or ISO_DATE.search(body[:300])
            published = _parse_iso(date.group(1)) if date else None
        records.append(_entry_record(title=title, entry_url=_html_entry_url(entry, base, title), page_url=base,
                                     published=published, body=body,
                                     uncertainties=[] if published else ["changelog_entry_date_unparseable"]))
    return records, skipped


def _safe_xml(raw: bytes) -> ET.Element:
    """Reject DOCTYPE/ENTITY (XXE, billion laughs) and encodings that could hide them."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in raw:
        raise SourceError("unsupported_feed_encoding")
    declared = XML_ENCODING.match(raw.removeprefix(b"\xef\xbb\xbf"))
    if declared and declared.group(1).decode("ascii").casefold() not in SAFE_XML_ENCODINGS:
        raise SourceError("unsupported_feed_encoding")
    if XML_FORBIDDEN.search(raw):
        raise SourceError("feed_doctype_or_entity_rejected")
    try:
        return ET.fromstring(raw)
    except (ET.ParseError, ValueError, RecursionError):
        raise SourceError("malformed_feed_xml") from None


def _text(element: ET.Element | None, path: str) -> str:
    found = element.find(path) if element is not None else None
    return "".join(found.itertext()) if found is not None else ""


def parse_feed(raw: bytes) -> tuple[list, list]:
    root = _safe_xml(raw)
    records, skipped = [], []
    if root.tag == "rss":
        channel = root.find("channel")
        entries = channel.findall("item") if channel is not None else []
        for item in entries[:MAX_ITEMS_PER_SOURCE]:
            link = _text(item, "link").strip()
            guid = item.find("guid")
            if not link and guid is not None and guid.get("isPermaLink", "true") != "false":
                link = (guid.text or "").strip()
            published = _parse_rfc822(_text(item, "pubDate")) or _parse_iso(_text(item, DC_DATE))
            records.append((link, _text(item, "title"), published, _text(item, "description")))
    elif root.tag == ATOM + "feed":
        for entry in root.findall(ATOM + "entry")[:MAX_ITEMS_PER_SOURCE]:
            link = ""
            for candidate in entry.findall(ATOM + "link"):
                if candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
                    link = candidate.get("href", "").strip()
                    break
            published = _parse_iso(_text(entry, ATOM + "published")) or _parse_iso(_text(entry, ATOM + "updated"))
            body = _text(entry, ATOM + "summary") or _text(entry, ATOM + "content")
            records.append((link, _text(entry, ATOM + "title"), published, body))
    else:
        raise SourceError("unsupported_feed_format")
    normalized = []
    for link, title, published, body in records:
        if not public_url(link):
            skipped.append({"reason": "item_without_public_https_link"})
            continue
        normalized.append(_record("feed_item", title, link, published, body, [link],
                                  [] if published else ["feed_item_date_unparseable"]))
    return normalized, skipped


def parse_crossref(data: object, expected_doi: str) -> tuple[list, list]:
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, dict):
        raise SourceError("crossref_message_missing")
    doi = message.get("DOI")
    if not isinstance(doi, str) or doi.casefold() != expected_doi.casefold():
        raise SourceError("doi_mismatch")
    titles = message.get("title")
    title = titles[0] if isinstance(titles, list) and titles and isinstance(titles[0], str) else ""
    published = None
    for key in ("published", "published-online", "published-print", "issued"):
        parts = (message.get(key) or {}).get("date-parts") if isinstance(message.get(key), dict) else None
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            values = [p for p in parts[0][:3] if isinstance(p, int) and not isinstance(p, bool)]
            try:
                published = datetime(*cast("tuple[int, int, int]", (values + [1, 1])[:3]),
                                     tzinfo=timezone.utc) if values else None
            except ValueError:
                published = None
            if published:
                break
    url = "https://doi.org/" + quote(doi, safe="/:;()._-")
    venue = message.get("container-title")
    venue = venue[0] if isinstance(venue, list) and venue and isinstance(venue[0], str) else ""
    body = (f"{venue}. " if venue else "") + (cast(str, message.get("abstract")) if isinstance(message.get("abstract"), str) else "")
    uncertainties = [] if published else ["publication_date_unknown"]
    if published and not (isinstance(parts, list) and len(parts[0]) >= 3):
        uncertainties.append("publication_date_partial")
    return [_record("paper", title, url, published, body, [url], uncertainties)], []


# --------------------------------------------------------------------- config / state

def load_config(path: str | Path) -> tuple[dict, Path]:
    path = Path(path)
    raw = _read_config_bytes(path)
    try:
        config = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Invalid sources config JSON: {getattr(exc, 'msg', 'decode error')}") from None
    issues = validate_config(config)
    if issues:
        raise ValidationError("; ".join(issues))
    return config, path.resolve().parent


def _read_config_bytes(path: Path) -> bytes:
    try:
        return _read_local(path, MAX_JSON_BYTES)
    except SourceError as exc:
        raise ValidationError(f"Sources config: {exc}") from None


def validate_config(config: object) -> list[str]:
    if not isinstance(config, dict):
        return ["sources config must be a JSON object"]
    issues = [i.replace("campaign/brand", "sources config") for i in _sensitive_keys(config, "config")]
    if config.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version must be 1")
    keywords = config.get("keywords", [])
    if not isinstance(keywords, list) or len(keywords) > 100 or not all(
            isinstance(k, str) and 0 < len(k.strip()) <= 60 for k in keywords):
        issues.append("keywords must be a list of at most 100 nonempty strings (<=60 chars)")
    half_life = config.get("half_life_days", DEFAULT_HALF_LIFE_DAYS)
    if isinstance(half_life, bool) or not isinstance(half_life, (int, float)) or not 1 <= half_life <= 365:
        issues.append("half_life_days must be a number in 1..365")
    if not isinstance(config.get("include_prereleases", False), bool):
        issues.append("include_prereleases must be true/false")
    mailto = config.get("crossref_mailto")
    if mailto is not None and (not isinstance(mailto, str) or len(mailto) > 254 or not MAILTO.fullmatch(mailto)):
        issues.append("crossref_mailto must be an email address the owner chose to share with Crossref")
    weights = config.get("weights", DEFAULT_WEIGHTS)
    if (not isinstance(weights, dict) or set(weights) != set(DEFAULT_WEIGHTS)
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in weights.values())
            or sum(weights.values()) <= 0):
        issues.append(f"weights must set nonnegative numbers for exactly {', '.join(sorted(DEFAULT_WEIGHTS))}")
    overrides = config.get("overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - {"include_urls", "exclude_urls"} or not all(
            isinstance(v, list) and all(isinstance(u, str) for u in v) for v in overrides.values()):
        issues.append("overrides may contain only include_urls/exclude_urls string lists")
    sources = config.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        issues.append(f"sources must list 1..{MAX_SOURCES} explicitly opted-in sources")
        return issues
    seen = set()
    for index, source in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(source, dict):
            issues.append(f"{where} must be an object")
            continue
        sid = source.get("id")
        if not isinstance(sid, str) or not SLUG.fullmatch(sid) or sid in seen:
            issues.append(f"{where}.id must be a unique lowercase slug")
        seen.add(sid)
        kind = source.get("kind")
        if kind not in KINDS:
            issues.append(f"{where}.kind must be one of {', '.join(sorted(KINDS))}")
        if source.get("sensitivity") not in SENSITIVITY:
            issues.append(f"{where}.sensitivity must be declared explicitly: public or private")
        visibility = source.get("visibility", "public")
        if visibility not in private_sources.VISIBILITIES:
            issues.append(f"{where}.visibility must be public or private when present")
        elif visibility == "private":
            if kind not in GITHUB_KINDS:
                issues.append(f"{where}.visibility private is supported only for GitHub API sources")
            if source.get("sensitivity") != "private":
                issues.append(f"{where}.visibility private requires sensitivity private (both opt-ins are needed)")
        if not isinstance(source.get("enabled", True), bool):
            issues.append(f"{where}.enabled must be true/false")
        if kind in GITHUB_KINDS:
            repo = source.get("repo")
            if not isinstance(repo, str) or not REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
                issues.append(f"{where}.repo must be owner/name")
            pages = source.get("max_pages", DEFAULT_MAX_PAGES)
            if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= MAX_PAGES_LIMIT:
                issues.append(f"{where}.max_pages must be an integer in 1..{MAX_PAGES_LIMIT}")
        elif "max_pages" in source:
            issues.append(f"{where}.max_pages applies only to GitHub API sources")
        if kind == "github_issues":
            labels = source.get("labels")
            if (not isinstance(labels, list) or not 1 <= len(labels) <= MAX_ISSUE_LABELS
                    or not all(isinstance(x, str) and 0 < len(x.strip()) <= 50 for x in labels)):
                issues.append(f"{where}.labels must list 1..{MAX_ISSUE_LABELS} owner-selected label names "
                              "(e.g. release-note, announcement)")
        if kind == "feed" and not public_url(source.get("url")):
            issues.append(f"{where}.url must be a public https feed URL")
        if kind == "changelog":
            issues.extend(_changelog_issues(source, where))
        if kind == "doi" and (not isinstance(source.get("doi"), str) or not DOI.fullmatch(source["doi"])):
            issues.append(f"{where}.doi must look like 10.NNNN/suffix")
        fixture = source.get("fixture")
        if fixture is not None and (not isinstance(fixture, str) or not fixture.strip() or "\x00" in fixture):
            issues.append(f"{where}.fixture must be a relative/absolute local path string")
        if kind != "changelog":  # a changelog source validates its own project label above
            project = source.get("project")
            if project is not None and (not isinstance(project, str) or not 0 < len(project.strip()) <= 100):
                issues.append(f"{where}.project must be a nonempty label of at most 100 characters")
    issues.extend(_project_sensitivity_issues(sources))
    return issues


def _project_keys(source: dict) -> list[str]:
    """Identities a source belongs to: its explicit project label, its GitHub repo, the repo behind a raw changelog URL."""
    keys = []
    project = source.get("project")
    if isinstance(project, str) and project.strip():
        keys.append("project " + project.strip().casefold())
    repo = source.get("repo")
    if isinstance(repo, str) and REPO.fullmatch(repo):
        keys.append("repository " + repo.casefold())
    url = source.get("url")
    if source.get("kind") == "changelog" and isinstance(url, str):
        parts = urlsplit(url)
        segments = [p for p in parts.path.split("/") if p]
        if parts.hostname == RAW_GITHUB_HOST and len(segments) >= 2:
            keys.append(f"repository {segments[0]}/{segments[1]}".casefold())
    return keys


def _project_sensitivity_issues(sources: list) -> list[str]:
    """Sensitivity is a property of the project: two sources of one project may not disagree.

    Without this, a private-repo source marked ``public`` next to a ``private`` one would let a
    project's signals cross the public/private line depending on which entry happened to match.
    """
    issues: list[str] = []
    seen: dict[str, tuple[int, str]] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or source.get("sensitivity") not in SENSITIVITY:
            continue
        for key in _project_keys(source):
            first = seen.setdefault(key, (index, source["sensitivity"]))
            if first[1] != source["sensitivity"]:
                issues.append(f"sources[{index}].sensitivity is {source['sensitivity']} but sources[{first[0]}] "
                              f"declares {first[1]} for the same {key}; one project has one sensitivity")
    return issues


def _changelog_issues(source: dict, where: str) -> list[str]:
    issues = []
    fmt, url, evidence = source.get("format"), source.get("url"), source.get("evidence_url")
    if fmt not in CHANGELOG_FORMATS:
        issues.append(f"{where}.format must be markdown (Keep a Changelog CHANGELOG.md) or html (website page)")
    if url is not None and not public_url(url):
        issues.append(f"{where}.url must be a public https URL")
    elif fmt == "markdown" and url is not None and urlsplit(url).hostname != RAW_GITHUB_HOST:
        issues.append(f"{where}.url for a markdown changelog must be https://{RAW_GITHUB_HOST}/...")
    if fmt == "html" and url is None:
        issues.append(f"{where}.url is required for an html changelog page (it anchors entry links)")
    if evidence is not None and not public_url(evidence):
        issues.append(f"{where}.evidence_url must be a public https URL")
    if url is None and evidence is None:
        issues.append(f"{where} needs url or evidence_url so every entry links public evidence")
    if url is None and source.get("fixture") is None:
        issues.append(f"{where} needs url (online) or fixture (local file)")
    project = source.get("project")
    if project is not None and (not isinstance(project, str) or not 0 < len(project.strip()) <= 100):
        issues.append(f"{where}.project must be a nonempty title prefix of at most 100 characters")
    return issues


def load_state(path: str | Path | None) -> dict:
    """Previously seen signals: {"schema_version":1,"seen":[{"id","title","url",...}]}; missing = empty."""
    if path is None or not Path(path).exists():
        return {"schema_version": SCHEMA_VERSION, "seen": []}
    try:
        state = json.loads(_read_local(Path(path), MAX_STATE_BYTES).decode("utf-8"))
    except (SourceError, UnicodeError, json.JSONDecodeError):
        raise ValidationError("State file unreadable or malformed; refusing to guess novelty") from None
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION or not isinstance(state.get("seen"), list):
        raise ValidationError("State file must be {\"schema_version\": 1, \"seen\": [...]}")
    state["seen"] = [s for s in state["seen"] if isinstance(s, dict) and isinstance(s.get("id"), str)]
    return state


def write_state(path: str | Path, state: dict, signals: list[dict], now: datetime) -> int:
    """Atomically record emitted signal IDs as seen (owner-only local file). Returns added count."""
    known = {entry["id"] for entry in state["seen"]}
    # A private signal is recorded by id only: no title or URL of a private repo is persisted.
    added = [({"id": s["id"], "private": True, "recorded_at": _rfc3339(now)} if s.get("private") else
              {"id": s["id"], "title": s["title"], "url": s["url"], "recorded_at": _rfc3339(now)})
             for s in signals if s["id"] not in known]
    merged = {"schema_version": SCHEMA_VERSION, "seen": (state["seen"] + added)[-MAX_STATE_ENTRIES:]}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sources-state-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return len(added)


# --------------------------------------------------------------------- scoring

def freshness_score(published: datetime | None, now: datetime, half_life_days: float) -> float:
    """Exponential decay 0.5 ** (age_days / half_life); unknown date scores 0."""
    if published is None:
        return 0.0
    age_days = max(0.0, (now - published).total_seconds() / 86400)
    return 0.5 ** (age_days / half_life_days)


def audience_relevance(text: str, keywords: list[str]) -> tuple[float, list[str]]:
    """Share of keywords found (word/phrase match), saturating at three matches; none configured = 0.5."""
    if not keywords:
        return 0.5, []
    haystack = f" {' '.join(_tokens(text))} "
    matched = [k for k in keywords if _tokens(k) and f" {' '.join(_tokens(k))} " in haystack]
    return min(1.0, len(matched) / min(len(keywords), 3)), matched


def _finalize(record: dict, source_id: str, now: datetime, *, private: bool = False) -> dict:
    title, title_cut = clean_text(record["title"], TITLE_LIMIT)
    summary, summary_cut = clean_text(record["body"], SUMMARY_LIMIT)
    uncertainties = list(record["uncertainties"])
    confidence = BASE_CONFIDENCE[record["kind"]]
    published = record["published"]
    if published is not None and published > now:
        uncertainties.append("future_timestamp_clamped")
        published = now
    if published is None:
        confidence -= 0.2
        if not any(u.endswith(("no_date", "date_unknown", "date_unparseable")) for u in uncertainties):
            uncertainties.append("publication_date_unknown")
    raw_text = f"{record['title']} {record['body'] if isinstance(record['body'], str) else ''}"
    if INSTRUCTION_LIKE.search(raw_text[:200_000]) or INSTRUCTION_LIKE.search(f"{title} {summary}"):
        confidence -= 0.2
        uncertainties.append("instruction_like_text_treated_as_data")
    if not title:
        title = record["url"]
        uncertainties.append("missing_title")
        confidence -= 0.1
    if title_cut:
        uncertainties.append("title_truncated")
    if summary_cut:
        uncertainties.append("summary_truncated")
    if not summary:
        uncertainties.append("no_summary_text")
    return {
        "id": signal_id(record["kind"], record["url"]), "kind": record["kind"], "source_id": source_id,
        "title": title, "url": record["url"], "published_at": _rfc3339(published),
        "evidence_urls": record["evidence_urls"] or [record["url"]], "summary": summary,
        "untrusted_text": True, "verification": "unverified_source_text",
        "confidence": round(max(0.05, min(1.0, confidence)), 4), "uncertainties": uncertainties,
        "visibility": "private" if private else "public", "private": private,
        # Private text is never public evidence until a human sets publishable on the campaign evidence.
        "publishable": not private,
        "_published": published,
    }


def rank_signals(signals: list[dict], *, now: datetime, keywords: list[str], half_life_days: float,
                 weights: dict, seen: list[dict]) -> list[dict]:
    """Score novelty in chronological order, then rank by transparent weighted components."""
    seen_ids = {entry["id"] for entry in seen}
    seen_titles = [(entry["id"], entry.get("title", "")) for entry in seen if isinstance(entry.get("title"), str)]
    earlier: list[tuple[str, str]] = []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    total_weight = sum(weights.values())
    for signal in sorted(signals, key=lambda s: (s["_published"] or oldest, s["id"])):
        signal["previously_seen"] = signal["id"] in seen_ids
        duplicate = None
        if not signal["previously_seen"]:
            duplicate = next((sid for sid, title in earlier + seen_titles if near_duplicate(signal["title"], title)), None)
        signal["duplicate_of"] = duplicate
        signal["novelty"] = 0.0 if signal["previously_seen"] else 0.2 if duplicate else 1.0
        signal["status"] = "previously_seen" if signal["previously_seen"] else "near_duplicate" if duplicate else "candidate"
        earlier.append((signal["id"], signal["title"]))
        signal["freshness"] = round(freshness_score(signal["_published"], now, half_life_days), 4)
        relevance, matched = audience_relevance(f"{signal['title']} {signal['summary']}", keywords)
        signal["audience_relevance"], signal["matched_keywords"] = round(relevance, 4), matched
        breakdown = {}
        for component, weight in weights.items():
            share = weight / total_weight
            breakdown[component] = {"value": signal[component], "weight": round(share, 4),
                                    "contribution": round(signal[component] * share, 4)}
        signal["rank_breakdown"] = breakdown
        signal["rank_score"] = round(sum(signal[c] * w for c, w in weights.items()) / total_weight, 4)
    ranked = sorted(signals, key=lambda s: (-s["rank_score"], -(s["_published"] or oldest).timestamp(), s["id"]))
    for signal in ranked:
        signal.pop("_published", None)
    return ranked


# --------------------------------------------------------------------- scan

def _github_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


GITHUB_PATHS = {
    "github_releases": "releases?per_page={n}",
    "github_tags": "tags?per_page={n}",
    "github_pulls": "pulls?state=closed&sort=updated&direction=desc&per_page={n}",
    "github_issues": "issues?state=closed&sort=updated&direction=desc&per_page={n}",
}


def github_paged(url: str, headers: dict, max_pages: int) -> tuple[object, list[dict]]:
    """Follow RFC 8288 ``rel="next"`` up to ``max_pages``; refuse any next link off https://api.github.com."""
    items: list = []
    notes: list[dict] = []
    visited: set[str] = set()
    for page in range(max_pages):
        raw, meta = _fetch_response(url, max_bytes=MAX_JSON_BYTES, headers=headers)
        data = _json(raw)
        if not isinstance(data, list):
            if page == 0:
                return data, notes  # the parser reports the unexpected shape
            raise SourceError("unexpected_page_shape")
        items.extend(data)
        visited.add(url)
        target = next_link(meta.get("link"))
        if target is None:
            break
        if not github_next_allowed(target) or target in visited:
            notes.append({"reason": "pagination_next_link_refused"})
            break
        if page + 1 == max_pages:
            notes.append({"reason": "pagination_max_pages_reached"})
            break
        url = target
    return items, notes


def _github_online(kind: str, repo: str, token: str | None, max_pages: int = DEFAULT_MAX_PAGES, *,
                   private: bool = False, scope: dict | None = None) -> tuple[object, list]:
    """Read repo metadata first; ``private=True`` only after all three opt-ins were checked.

    ``scope`` (a dict) receives the first ``X-OAuth-Scopes`` value seen, for the token report.
    """
    if private and not token:
        raise SourceError("private_source_requires_token")  # no request is made
    owner, name = (quote(part, safe="") for part in repo.split("/"))
    headers = _github_headers(token)
    raw, selected = _fetch_response(f"{GITHUB_API}/repos/{owner}/{name}", max_bytes=MAX_JSON_BYTES, headers=headers)
    if scope is not None and "header" not in scope:
        scope["header"] = selected.get(private_sources.SCOPE_HEADER.casefold())
    meta = _json(raw)
    notes: list = []
    if private:
        # Declared private: the metadata must agree (GitHub Enterprise "internal" is private too).
        if not isinstance(meta, dict) or meta.get("private") is not True \
                or meta.get("visibility", "private") not in ("private", "internal"):
            raise SourceError("repository_not_confirmed_private")
        note = private_sources.repository_permission_note(meta)
        if note:
            notes.append({"reason": note})
    # Public-only: never ingest a private repo merely because a token can read it.
    elif not isinstance(meta, dict) or meta.get("private") is not False or meta.get("visibility", "public") != "public":
        raise SourceError("repository_not_confirmed_public")
    path = GITHUB_PATHS[kind].format(n=GITHUB_PER_PAGE)
    items, paging = github_paged(f"{GITHUB_API}/repos/{owner}/{name}/{path}", headers, max_pages)
    return items, notes + paging


def crossref_url(doi: str, mailto: str | None = None) -> str:
    """``https://api.crossref.org/works/{doi}`` for a pattern-validated DOI, fully URL-quoted."""
    if not isinstance(doi, str) or not DOI.fullmatch(doi):
        raise SourceError("invalid_doi")
    url = CROSSREF_API + quote(doi, safe="")
    if mailto:  # only when the owner set crossref_mailto; there is no default address
        url += "?" + urlencode({"mailto": mailto})
    return url


def _fixture_path(source: dict, base_dir: Path) -> Path | None:
    fixture = source.get("fixture")
    if fixture is None:
        return None
    return Path(fixture) if Path(fixture).is_absolute() else base_dir / fixture


def _load_changelog(source: dict, fixture_path: Path | None, online: bool, robots: RobotsCache | None) -> tuple[list, list]:
    url = source.get("url")
    page_url = cast(str, source.get("evidence_url") or url)  # validate_config: url or evidence_url is set
    if online and url is not None:
        accept = "text/markdown, text/plain" if source["format"] == "markdown" else "text/html, application/xhtml+xml"
        raw = _web_fetch(url, max_bytes=MAX_FEED_BYTES, accept=accept, robots=robots)
    elif fixture_path is not None:
        raw = _read_local(fixture_path, MAX_FEED_BYTES)
    else:
        return [], [{"reason": "offline_no_fixture_use_online"}]
    if source["format"] == "markdown":
        return parse_changelog_markdown(raw, page_url=page_url, project=source.get("project") or source["id"])
    return parse_changelog_html(raw, page_url=page_url)


def _load_records(source: dict, config: dict, base_dir: Path, online: bool, token: str | None,
                  force_include: frozenset, robots: RobotsCache | None = None,
                  scope: dict | None = None) -> tuple[list, list]:
    kind = source["kind"]
    fixture_path = _fixture_path(source, base_dir)
    if kind == "changelog":
        return _load_changelog(source, fixture_path, online, robots)
    if not online and fixture_path is None:
        return [], [{"reason": "offline_no_fixture_use_online"}]
    prereleases = config.get("include_prereleases", False)
    if kind == "feed":
        raw = (_web_fetch(source["url"], max_bytes=MAX_FEED_BYTES, robots=robots,
                          accept="application/rss+xml, application/atom+xml, application/xml")
               if online else _read_local(cast(Path, fixture_path), MAX_FEED_BYTES))
        return parse_feed(raw)
    if kind == "doi":
        # Crossref is an API (not crawled): GET only, no credentials, optional owner-set mailto.
        raw = (_fetch(crossref_url(source["doi"], config.get("crossref_mailto")), max_bytes=MAX_JSON_BYTES,
                      headers={"Accept": "application/json"})
               if online else _read_local(cast(Path, fixture_path), MAX_JSON_BYTES))
        return parse_crossref(_json(raw), source["doi"])
    max_pages = source.get("max_pages", DEFAULT_MAX_PAGES)
    limit = max(MAX_ITEMS_PER_SOURCE, GITHUB_PER_PAGE * max_pages)
    notes: list = []
    if online:
        data, notes = _github_online(kind, source["repo"], token, max_pages,
                                     private=source.get("visibility") == "private", scope=scope)
    else:
        data = _json(_read_local(cast(Path, fixture_path), MAX_JSON_BYTES))
    if kind == "github_releases":
        records, skipped = parse_github_releases(data, source["repo"], include_prereleases=prereleases, limit=limit)
    elif kind == "github_tags":
        records, skipped = parse_github_tags(data, source["repo"], include_prereleases=prereleases, limit=limit)
    elif kind == "github_issues":
        records, skipped = parse_github_issues(data, source["repo"], labels=source["labels"], limit=limit)
    else:
        records, skipped = parse_github_pulls(data, source["repo"], force_include=force_include, limit=limit)
    return records, skipped + notes


def scan_sources(config: dict, *, base_dir: Path, online: bool = False, state: dict | None = None,
                 now: datetime | None = None, top: int = 10, env: Mapping[str, str] | None = None,
                 allow_private: bool = False) -> dict:
    """Return ranked SourceSignals plus skipped/error diagnostics. Never drafts or publishes.

    ``allow_private`` is the third private opt-in (``--allow-private``); without it a
    ``visibility: private`` source is skipped before any read.
    """
    issues = validate_config(config)
    if issues:
        raise ValidationError("; ".join(issues))
    if not 1 <= top <= 100:
        raise ValidationError("--top must be 1..100")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state = state or {"schema_version": SCHEMA_VERSION, "seen": []}
    env = os.environ if env is None else env
    token = None
    if online and any(s.get("kind") in GITHUB_KINDS for s in config["sources"]):
        candidate = str(credential(TOKEN_ENV, env) or "").strip()  # env or $CREDENTIALS_DIRECTORY
        if candidate and not TOKEN_SHAPE.fullmatch(candidate):
            raise ValidationError(f"{TOKEN_ENV} is set but malformed; value not shown")
        token = candidate or None
    overrides = config.get("overrides", {})
    force_include = frozenset(overrides.get("include_urls", []))
    excluded = frozenset(overrides.get("exclude_urls", []))
    signals, skipped, errors = [], [], []
    robots = RobotsCache() if online else None  # one robots.txt fetch per host per run
    scope: dict = {}
    private_read = False
    for source in config["sources"]:
        sid = source["id"]
        if not source.get("enabled", True):
            skipped.append({"source_id": sid, "reason": "source_disabled"})
            continue
        private = source.get("visibility") == "private"  # validate_config: implies sensitivity private
        if source["sensitivity"] != "public" and not private:
            # Sensitivity private without visibility private: never read, fetched or emitted.
            skipped.append({"source_id": sid, "reason": "private_source_excluded"})
            continue
        if private and not allow_private:
            skipped.append({"source_id": sid, "reason": "private_source_needs_allow_private"})
            continue
        try:
            records, dropped = _load_records(source, config, base_dir, online, token, force_include, robots,
                                             scope)
        except _Skipped as exc:
            skipped.append({"source_id": sid, "reason": str(exc)})
            continue
        except SourceError as exc:
            errors.append({"source_id": sid, "error": str(exc)})
            continue
        private_read = private_read or private
        # Diagnostics of a private source never carry its item URLs.
        skipped.extend({"source_id": sid, **({k: v for k, v in item.items() if k != "url"} if private else item)}
                       for item in dropped)
        for record in records:
            if record["url"] in excluded:
                skipped.append({"source_id": sid, "reason": "manual_exclude",
                                **({} if private else {"url": record["url"]})})
                continue
            signals.append(_finalize(record, sid, now, private=private))
    best: dict[str, dict] = {}
    for signal in signals:  # same evidence URL from several sources (e.g. release + tag) -> keep strongest
        prior = best.get(signal["url"])
        if prior is not None and (prior["private"] or signal["private"]):
            for item in (prior, signal):  # the same URL from any private source stays private
                item["visibility"], item["private"], item["publishable"] = "private", True, False
        if prior is None or signal["confidence"] > prior["confidence"]:
            if prior is not None:
                skipped.append(_duplicate_note(prior))
            best[signal["url"]] = signal
        else:
            skipped.append(_duplicate_note(signal))
    ranked = rank_signals(list(best.values()), now=now, keywords=config.get("keywords", []),
                          half_life_days=float(config.get("half_life_days", DEFAULT_HALF_LIFE_DAYS)),
                          weights=config.get("weights", DEFAULT_WEIGHTS), seen=state["seen"])
    result = {
        "status": "RESEARCH_ONLY", "generated_at": _rfc3339(now), "online": bool(online),
        "github_authenticated": bool(token), "private_sources_allowed": bool(allow_private),
        "counts": {"ranked": len(ranked), "shown": min(top, len(ranked)), "skipped": len(skipped), "errors": len(errors),
                   "candidates": sum(1 for s in ranked if s["status"] == "candidate"),
                   "private": sum(1 for s in ranked if s["private"])},
        "signals": ranked[:top], "skipped": skipped, "errors": errors, "note": NOTE,
    }
    if online and token and "header" in scope:
        result["token_scope"] = private_sources.token_scope_report(scope["header"], authenticated=True)
    if private_read:
        result["private_note"] = PRIVATE_NOTE
    return result


def _duplicate_note(signal: dict) -> dict:
    note = {"source_id": signal["source_id"], "reason": "duplicate_url"}
    return note if signal["private"] else {**note, "url": signal["url"]}


def scan_config_file(path: str | Path, *, online: bool = False, state_path: str | Path | None = None,
                     top: int = 10, mark_seen: bool = False, now: datetime | None = None,
                     allow_private: bool = False) -> dict:
    if mark_seen and state_path is None:
        raise ValidationError("--mark-seen requires --state FILE")
    config, base_dir = load_config(path)
    state = load_state(state_path)
    result = scan_sources(config, base_dir=base_dir, online=online, state=state, now=now, top=top,
                          allow_private=allow_private)
    if mark_seen:
        fresh = [s for s in result["signals"] if s["status"] == "candidate"]
        result["state_recorded"] = write_state(cast("str | Path", state_path), state, fresh, now or datetime.now(timezone.utc))
    return result


def render_scan(result: dict) -> str:
    mode = "online (explicit GET)" if result["online"] else "offline fixtures"
    lines = [f"{result['status']}: {result['counts']['ranked']} ranked source signals ({mode}); "
             f"showing {result['counts']['shown']}"]
    for index, s in enumerate(result["signals"], 1):
        b = s["rank_breakdown"]
        private = s.get("private") is True
        lines.append(f"{index}. [{s['rank_score']:.4f}] {s['kind']} {s['status']}"
                     f"{' PRIVATE (not public evidence)' if private else ''}: {s['title']}")
        lines.append(f"   evidence: {s['url']} | published: {s['published_at'] or 'unknown'}")
        lines.append("   " + " ".join(f"{k}={v['value']:.2f}x{v['weight']:.2f}" for k, v in b.items()))
        if s["summary"] and private:
            lines.append("   summary withheld from text output (private source; it is in --json only)")
        elif s["summary"]:
            lines.append(f"   summary (untrusted data): {s['summary'][:160]}")
        if s["uncertainties"]:
            lines.append(f"   uncertainties: {', '.join(s['uncertainties'])}")
    if result["skipped"]:
        reasons: dict[str, int] = {}
        for item in result["skipped"]:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        lines.append("Skipped: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    for error in result["errors"]:
        lines.append(f"ERROR {error['source_id']}: {error['error']}")
    for warning in (result.get("token_scope") or {}).get("warnings", []):
        lines.append(f"TOKEN WARNING: {warning}")
    if "private_note" in result:
        lines.append(result["private_note"])
    if "state_recorded" in result:
        lines.append(f"Recorded {result['state_recorded']} candidate IDs as seen in local state file.")
    lines.append(result["note"])
    return "\n".join(lines)
