"""Offline SEO and discoverability checks for owner-supplied social post text (issue #45 W4).

Input is one local JSON file of draft or published post texts. By default this module has NO network
access, reads no credential and opens no database. It publishes, schedules and edits nothing; it returns
findings for a human to act on. Link-preview (Open Graph) checks need the page itself, so they run only
with the explicit ``online`` option, through the same guarded fetcher as ``landing-check`` and
``link-health`` (robots.txt first, inspected redirects, one GET per distinct page, no image requests).
Name-consistency checks are offline: the owner lists the canonical spellings and variants are flagged.

Every finding carries a ``basis`` so a hypothesis is never mistaken for a rule:

* ``official``: a limit a provider documents (cited in the module that owns it).
* ``repo_policy``: ReachWeaver's own conservative limit or rule (``policy.CONSERVATIVE_TEXT_LIMITS``).
* ``hypothesis``: editorial advice with no ranking claim behind it. No finding here says that a
  hashtag, keyword or format changes reach or search ranking.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .attribution import validate_utm_link
from .instagram import content_problems as instagram_caption_problems
from .policy import CONSERVATIVE_TEXT_LIMITS, ValidationError
from .inputs import read_json_file

SCHEMA = "reachweaver.seo-content-check.v1"
MAX_ITEMS = 500
MAX_TEXT_CHARS = 30_000
MAX_KEYWORDS = 8
SEVERITIES = ("error", "warn", "info")
NEAR_DUPLICATE_RATIO = 0.9
MIN_WORDS_FOR_DUPLICATE = 6
MAX_STUFFED_HASHTAG_RATIO = 0.5  # hypothesis threshold, not a platform rule
HUMAN_AUTHORED_ONLY = frozenset({"hackernews", "devto"})
MAX_NAMES = 5
MAX_NAME_CHARS = 60
MAX_PREVIEW_PAGES = 25
ALLOWED_KEYS = frozenset({"id", "platform", "text", "alt_text", "media_count", "link", "keywords"})
_URL = re.compile(r"https?://\S+", re.I)
_HASHTAG = re.compile(r"(?<!\w)#(\w+)", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)


def _finding(severity: str, code: str, message: str, basis: str) -> dict:
    return {"severity": severity, "code": code, "message": message, "basis": basis}


def load_items(data: object) -> list[dict]:
    """Validate the input contract: ``{"items": [...]}`` or a bare list; unknown keys are refused."""
    raw = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw or len(raw) > MAX_ITEMS:
        raise ValidationError(f"seo-content-check input needs 1..{MAX_ITEMS} items")
    seen, items = set(), []
    for n, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict) or set(entry) - ALLOWED_KEYS:
            raise ValidationError(f"item {n}: must be an object with only {', '.join(sorted(ALLOWED_KEYS))}")
        item_id, platform, text = entry.get("id"), entry.get("platform"), entry.get("text")
        if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > 200 or item_id in seen:
            raise ValidationError(f"item {n}: id must be a unique non-empty string")
        seen.add(item_id)
        if not isinstance(platform, str) or not platform.strip():
            raise ValidationError(f"item {item_id}: platform is required")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValidationError(f"item {item_id}: text must be a non-empty string up to {MAX_TEXT_CHARS} characters")
        alt, link, keywords, media = entry.get("alt_text"), entry.get("link"), entry.get("keywords", []), entry.get("media_count", 0)
        if alt is not None and not isinstance(alt, str):
            raise ValidationError(f"item {item_id}: alt_text must be a string")
        if link is not None and not isinstance(link, str):
            raise ValidationError(f"item {item_id}: link must be a string")
        if type(media) is not int or not 0 <= media <= 100:
            raise ValidationError(f"item {item_id}: media_count must be an integer 0..100")
        if (not isinstance(keywords, list) or len(keywords) > MAX_KEYWORDS
                or any(not isinstance(k, str) or not 1 <= len(k) <= 80 for k in keywords)):
            raise ValidationError(f"item {item_id}: keywords must be at most {MAX_KEYWORDS} phrases of 1..80 characters")
        items.append({"id": item_id, "platform": platform.strip().casefold(), "text": text, "alt_text": alt,
                      "media_count": media, "link": link, "keywords": keywords})
    return items


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _dedupe_key(text: str) -> list[str]:
    """Words of the text without URLs and hashtags, so only the wording is compared."""
    return _WORD.findall(_HASHTAG.sub(" ", _URL.sub(" ", _fold(text))))


def load_names(data: object, extra: list[str] | None = None) -> list[str]:
    """Canonical spellings to enforce: top-level ``names`` of the input file plus ``extra`` (``--name``)."""
    declared = data.get("names") if isinstance(data, dict) else None
    if declared is not None and not isinstance(declared, list):
        raise ValidationError("names must be a list of canonical spellings")
    names: list[str] = []
    for value in [*(declared or []), *(extra or [])]:
        if (not isinstance(value, str) or not 2 <= len(value.strip()) <= MAX_NAME_CHARS
                or not re.fullmatch(r"[\w .&+'-]+", value.strip(), re.UNICODE)):
            raise ValidationError(f"a name must be 2..{MAX_NAME_CHARS} characters of letters, digits, spaces or . & + ' -")
        if value.strip() not in names:
            names.append(value.strip())
    if len(names) > MAX_NAMES:
        raise ValidationError(f"at most {MAX_NAMES} names")
    return names


def _name_pattern(name: str) -> re.Pattern:
    """Matches the name written as one word, or split at its capitals, with optional space, '-' or '_'."""
    parts = re.findall(r"[A-Z]?[^A-Z\s_-]+|[A-Z]+(?![^A-Z\s_-])", name) or [name]
    body = r"[\s_-]?".join(re.escape(part) for part in parts)
    return re.compile(r"(?<![\w@#/.])" + body + r"(?!\w)", re.IGNORECASE | re.UNICODE)


def name_findings(item: dict, names: list[str]) -> list[dict]:
    """Flag a name written differently from its canonical spelling (URLs and hashtags are ignored)."""
    text = _HASHTAG.sub(" ", _URL.sub(" ", item["text"]))
    findings = []
    for name in names:
        wrong = sorted({m.group(0) for m in _name_pattern(name).finditer(text) if m.group(0) != name})
        for variant in wrong:
            findings.append(_finding("warn", "name_variant",
                                     f"Written as {variant!r}; the canonical spelling is {name!r}. Use one spelling on every "
                                     "platform so the name reads as one entity.", "repo_policy"))
    return findings


def preview_findings(items: list[dict], *, allow_origins=None, fetch=None, robots=None) -> dict[str, list[dict]]:
    """Link-preview (Open Graph) findings for each item's ``link``; network only when this is called.

    One GET per distinct landing page (UTM stripped) through ``landing.fetch_landing`` after robots.txt.
    """
    from .attribution import normalize_destination, normalize_origin
    from .landing import fetch_landing
    from .link_health import _classify_fetch
    from .sources import RobotsCache
    fetch = fetch or fetch_landing
    robots = robots or RobotsCache()
    allowed = {normalize_origin(origin) for origin in allow_origins or []}
    severity = {"error": "error", "warning": "warn"}
    by_destination: dict[str, list[dict]] = {}
    out: dict[str, list[dict]] = {}
    for item in items:
        if not item["link"]:
            continue
        try:
            destination = normalize_destination(item["link"])
        except ValidationError:
            continue  # the offline link_attribution finding already says why
        if destination not in by_destination:
            if len(by_destination) >= MAX_PREVIEW_PAGES:
                out.setdefault(item["id"], []).append(_finding(
                    "info", "preview_not_checked_limit",
                    f"Only {MAX_PREVIEW_PAGES} distinct pages are fetched per run; this link was not checked.", "repo_policy"))
                continue
            status, summary, found = _classify_fetch(fetch(destination, robots=robots, allowed_origins=allowed), destination)
            by_destination[destination] = [
                _finding("info" if status == "blocked_by_robots" else severity[f["severity"]], "preview_" + f["code"],
                         f"Link {destination}: {f['message']}", "repo_policy") for f in found]
        out.setdefault(item["id"], []).extend(dict(f) for f in by_destination[destination])
    return out


def check_item(item: dict) -> list[dict]:
    findings: list[dict] = []
    platform, text = item["platform"], item["text"]
    limit = CONSERVATIVE_TEXT_LIMITS.get(platform)
    if limit is None:
        findings.append(_finding("info", "no_length_limit_recorded",
                                 f"No conservative text limit is recorded for {platform!r}; length is not checked.", "repo_policy"))
    elif len(text) > limit:
        findings.append(_finding("error", "text_too_long",
                                 f"Text has {len(text)} characters; the conservative {platform} limit is {limit}.", "repo_policy"))
    if platform == "instagram":
        for problem in instagram_caption_problems(text):
            findings.append(_finding("error", "instagram_caption_rule", problem, "official"))
    if platform in HUMAN_AUTHORED_ONLY:
        findings.append(_finding("info", "human_authored_only",
                                 f"{platform} requires human-written text; use this check only on text you wrote yourself.", "repo_policy"))
    if item["media_count"] > 0 and not (item["alt_text"] or "").strip():
        findings.append(_finding("warn", "alt_text_missing",
                                 "Media is attached but alt text is empty; add a one-sentence description for screen readers and text search.",
                                 "hypothesis"))
    tags = [m.group(1).casefold() for m in _HASHTAG.finditer(text)]
    repeated = sorted({t for t in tags if tags.count(t) > 1})
    if repeated:
        findings.append(_finding("warn", "hashtag_repeated", "Repeated hashtag(s): " + ", ".join("#" + t for t in repeated) + ".", "hypothesis"))
    words = _WORD.findall(_fold(text))
    if len(words) >= 4 and tags and len(tags) / len(words) > MAX_STUFFED_HASHTAG_RATIO:
        findings.append(_finding("warn", "hashtag_heavy",
                                 f"{len(tags)} of {len(words)} words are hashtags; read it aloud and keep only the ones a reader would search.",
                                 "hypothesis"))
    if item["keywords"]:
        haystack = _fold(text)
        missing = [k for k in item["keywords"] if _fold(k) not in haystack]
        if len(missing) == len(item["keywords"]):
            findings.append(_finding("info", "keywords_absent",
                                     "None of the declared topic phrases appear in the text; add one only if it fits naturally and accurately, never to stuff.",
                                     "hypothesis"))
    if item["link"]:
        for problem in validate_utm_link(item["link"]):
            findings.append(_finding("warn", "link_attribution", f"Link: {problem}.", "repo_policy"))
        if item["link"] not in text:
            findings.append(_finding("info", "link_not_in_text",
                                     "The declared link does not appear in the text (fine if the platform shows it separately or in the bio).",
                                     "repo_policy"))
    return findings


def _duplicate_findings(items: list[dict]) -> dict[str, list[dict]]:
    """Identical or near-identical wording on different platforms (compared as word sets)."""
    out: dict[str, list[dict]] = {}
    keyed = [(it, _dedupe_key(it["text"])) for it in items]
    for i, (a, wa) in enumerate(keyed):
        if len(wa) < MIN_WORDS_FOR_DUPLICATE:
            continue
        same = []
        for b, wb in keyed[i + 1:]:
            if b["platform"] == a["platform"] or len(wb) < MIN_WORDS_FOR_DUPLICATE:
                continue
            sa, sb = set(wa), set(wb)
            if wa == wb or len(sa & sb) / len(sa | sb) >= NEAR_DUPLICATE_RATIO:
                same.append(b)
        for member in same:
            for one, other in ((a, member), (member, a)):
                out.setdefault(one["id"], []).append(_finding(
                    "info", "cross_platform_duplicate",
                    f"Same wording as {other['id']!r} on {other['platform']}; consider adapting it to each platform's reader "
                    "(an editorial hypothesis, not a ranking claim).", "hypothesis"))
    return out


def check_items(items: list[dict], *, names: list[str] | None = None, online: bool = False,
                allow_origins=None, fetch=None) -> dict:
    duplicates = _duplicate_findings(items)
    previews = preview_findings(items, allow_origins=allow_origins, fetch=fetch) if online else {}
    results = []
    for item in items:
        findings = (check_item(item) + (name_findings(item, names) if names else [])
                    + duplicates.get(item["id"], []) + previews.get(item["id"], []))
        findings.sort(key=lambda f: SEVERITIES.index(f["severity"]))
        results.append({"id": item["id"], "platform": item["platform"], "findings": findings})
    counts = {s: sum(1 for r in results for f in r["findings"] if f["severity"] == s) for s in SEVERITIES}
    return {
        "schema_version": SCHEMA, "status": "read_only_no_publish", "items": results, "counts": counts,
        "policy": {
            "network_access": online, "publishes": False,
            "note": ("Findings are for a human to act on. 'official' and 'repo_policy' findings state a documented or "
                     "project-set limit; 'hypothesis' findings are editorial advice with no claim about reach or search "
                     "ranking. Open Graph link previews need the page: use --online (or landing-check)."),
        },
    }


def check_file(path: Path, *, names: list[str] | None = None, online: bool = False, allow_origins=None) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValidationError("seo-content-check input must be an existing regular file <=2 MB (no symlinks)")
    try:
        data = read_json_file(path)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc.msg}") from exc
    return check_items(load_items(data), names=load_names(data, names), online=online, allow_origins=allow_origins)


def render_text(report: dict) -> str:
    c = report["counts"]
    lines = [f"seo-content-check: {len(report['items'])} items; {c['error']} error, {c['warn']} warn, {c['info']} info"]
    for r in report["items"]:
        for f in r["findings"]:
            lines.append(f"  [{f['severity']}] {r['id']} ({r['platform']}) {f['code']} [{f['basis']}]: {f['message']}")
    return "\n".join(lines)
