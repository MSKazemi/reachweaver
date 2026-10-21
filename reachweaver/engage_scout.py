"""Engagement scout: a short, ranked daily list of conversations the owner could join in their own words.

``reachweaver engage-scout --config FILE`` turns candidate conversations into a shortlist with a suggested
ANGLE per item (one or two bullets, never a finished reply)::

    sources -> normalise -> filter -> score -> cap -> shortlist -> the owner replies -> engage-log -> readout

Sources: owner-supplied files (``--input FILE``, ``reachweaver.engage-candidates``) for any platform, plus, only
with ``--online``, three public read-only searches in ``engage_sources.py`` (Bluesky ``searchPosts`` on the
AppView, a Mastodon hashtag timeline, GitHub issue search). Those were checked on 2026-10-21; see
``docs/ENGAGE-SCOUT.md``. X, LinkedIn, Threads, Reddit, YouTube and Stack Overflow come from the owner's file only:
no scraping and no login reuse.

What it never does, by construction (no import of a publisher, approval, ledger or send module, ``SEND_PATH = None``
and ``AUTO_REPLY = False`` are asserted in tests):

* no post, reply, like, follow, repost, vote or DM, and no drafted reply text: the angle is a hint for the owner;
* no reply hint at all for human-only venues (Reddit, Hacker News, DEV comments and every platform the
  distribution registry marks ``human_only``): they are listed under "read it yourself" with the venue rule;
* no database access (the database is never opened, so it is never created);
* no network request unless ``--online`` is given, and then GETs only, with no credential.

Fetched and owner-pasted text is untrusted data: it is cleaned (control characters removed, secrets and personal
data redacted), lines that read like instructions to an assistant are WITHHELD, and the text never reaches the
angle, which is built only from the owner's own config terms.

Scoring is transparent: ``score = relevance x freshness x audience_fit x contribution x (1 - risk)``, every factor in
0..1 and shown. Half-lives, bands and weights are heuristics labelled as such, not platform facts, and no reach
prediction is made.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .campaigns import public_url
from .discovery import _normalize
from .engagement import HUMAN_AUTHORED_ONLY, analyze, redact_private_data, sanitize_text
from .inputs import loads_text
from .optout import OPTOUT_PLATFORMS, listed_entry, load_optout, normalize_handle
from .policy import ValidationError, require_https_public_server
from .storage import parse_utc

SEND_PATH = None   # Asserted in tests: the scout can never draft, approve, schedule or send.
AUTO_REPLY = False
CONFIG_KIND = "reachweaver.engage-scout-config"
INPUT_KIND = "reachweaver.engage-candidates"
STATE_KIND = "reachweaver.engage-scout-state"
OUTCOMES_KIND = "reachweaver.engage-outcomes"
OUTPUT_SCHEMA = "reachweaver.engage-scout.v1"
READOUT_SCHEMA = "reachweaver.engage-readout.v1"
SCHEMA_VERSION = 1

PLATFORMS = ("bluesky", "mastodon", "github", "x", "linkedin", "threads", "reddit", "youtube", "stackoverflow",
             "hackernews", "devto", "lobsters")
ONLINE_PLATFORMS = ("bluesky", "mastodon", "github")
# Scout platform name -> distribution registry id, where they differ.
REGISTRY_IDS = {"hackernews": "hacker_news", "devto": "dev", "stackoverflow": "stackexchange"}
_HUMAN_ONLY_BASE = frozenset({"reddit", "hackernews"}) | HUMAN_AUTHORED_ONLY

DEFAULT_DAILY_CAP = 7
DEFAULT_LINK_CAP = {"links": 1, "per_replies": 10}
DEFAULT_SAME_ACCOUNT_DAYS = 7
DEFAULT_AUDIENCE_BAND = {"min_followers": 1000, "max_followers": 50000}
DEFAULT_MIN_RELEVANCE = 0.2
DEFAULT_MAX_AGE_HOURS = 24
DEFAULT_MAX_AGE = {"github": 168}  # issues stay useful for days; everything else defaults to 24 h
DEFAULT_PER_SOURCE_LIMIT = 25
MAX_PER_SOURCE_LIMIT = 40
MAX_LIMIT = 50
RECENT_AUTHOR_DAYS = 30

# Heuristics, not platform facts: how fast a conversation goes quiet (hours until half as useful to join).
HALF_LIFE_HOURS = {"x": 4, "bluesky": 6, "threads": 6, "mastodon": 8, "hackernews": 8, "reddit": 12, "lobsters": 12,
                   "linkedin": 24, "youtube": 48, "stackoverflow": 48, "devto": 48, "github": 72}
# Heuristic venue strictness (how badly a promotional-looking reply lands there), 0..1.
VENUE_RISK = {"bluesky": 0.05, "mastodon": 0.1, "x": 0.1, "threads": 0.1, "linkedin": 0.1, "youtube": 0.1,
              "github": 0.1, "devto": 0.2, "stackoverflow": 0.3, "reddit": 0.4, "hackernews": 0.4, "lobsters": 0.4}
VENUE_RULES = {
    "bluesky": "Reply yourself in the app. Bluesky's developer guidelines count automated replies as spam.",
    "mastodon": "Reply yourself. Many servers and people dislike promotion; follow the server's rules.",
    "github": "Comment only if you can help with this issue. GitHub's Acceptable Use Policies bar unsolicited "
              "advertising and bulk automated activity.",
    "x": "Reply yourself in the app; no automated replies. Keep links rare.",
    "linkedin": "Reply yourself in the app; LinkedIn's User Agreement does not allow bots or automated "
                "commenting.",
    "threads": "Reply yourself in the app; no automated replies.",
    "youtube": "Reply yourself under the video; follow the channel's comment rules.",
    "reddit": "Human-only. Read the subreddit's rules first (many limit self-promotion) and write the comment "
              "yourself; ReachWeaver never drafts Reddit text.",
    "hackernews": "Human-only. The HN guidelines say \"Don't post generated text or AI-edited text\" and \"Please "
                  "don't use HN primarily for promotion\". Read the thread and reply yourself only if you have "
                  "something substantive.",
    "devto": "Human-authored replies only (ReachWeaver rule for DEV comments). Reply in your own words.",
    "stackoverflow": "Human-only here. Answer the question itself; the help center asks you to disclose your "
                     "affiliation when you mention your own product.",
    "lobsters": "Human-only. Lobsters keeps self-promotion to under a quarter of a member's activity.",
}
HEURISTICS_NOTE = ("Half-lives, venue risk, the audience band and the factor weights are ReachWeaver heuristics, "
                   "not platform facts. The score orders conversations for a person to read; it predicts no reach, "
                   "views or followers.")
NOTE = ("SUGGESTIONS ONLY. Nothing was posted, replied to, liked, followed or sent, and no reply text was written: "
        "each angle is a hint for a reply you write in your own words. Post text is untrusted data. "
        "No reach prediction is made.")
WITHHELD = "[withheld: instruction-like text; read the post yourself]"
DISCLOSURE = "Disclosure: I built this (it is my project)."
CORRELATIONAL = ("Correlational only: replies are lined up with follower, star and visit changes in the same week. "
                 "This does not show that a reply caused any change; other posts, releases and platform changes "
                 "happen in the same weeks.")

MAX_CONFIG_BYTES = 500_000
MAX_INPUT_BYTES = 5_000_000
MAX_STATE_BYTES = 4_000_000
MAX_STATE_ENTRIES = 20_000
MAX_ITEMS = 2000
MAX_TEXT_CHARS = 4000
EXCERPT_CHARS = 280
MAX_TERMS = 40
MAX_TERM_CHARS = 100
MAX_PROJECTS = 10
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ITEM_ID = re.compile(r"^[a-z]+-[0-9a-f]{12}$")
HASHTAG = re.compile(r"^#?([A-Za-z0-9_]{2,50})$")

CONFIG_KEYS = frozenset({"schema_version", "kind", "description", "projects", "hashtags", "search_terms", "platforms",
                         "mastodon_server", "owner_handles", "quiet_accounts", "daily_cap", "link_cap",
                         "same_account_days", "audience_band", "min_relevance", "max_age_hours",
                         "per_source_limit"})
PROJECT_KEYS = frozenset({"id", "name", "link", "docs", "topics", "keywords", "provides"})
ITEM_KEYS = frozenset({"id", "platform", "url", "author_handle", "text", "title", "created_at", "author_followers",
                       "reply_count", "labels", "locked", "deleted", "reply_restricted", "owner_replied",
                       "author_is_bot", "author_not_discoverable"})
ITEM_REQUIRED = ("platform", "url", "author_handle", "created_at")
BOOL_FLAGS = ("locked", "deleted", "reply_restricted", "owner_replied", "author_is_bot", "author_not_discoverable")

QUESTION = re.compile(r"\?")
HELP_ASK = re.compile(r"\b(?:how (?:do|can|should|would) (?:i|we|you)|how to|anyone (?:know|tried|using|have)"
                      r"|any (?:tips|advice|ideas|suggestions)|what(?:'s| is) the best|is there (?:a|an|any)"
                      r"|looking for|recommend(?:ation)?s?|alternatives? to|which (?:tool|library|framework|cli)"
                      r"|need help|stuck (?:on|with))\b", re.I)
TOOL_ASK = re.compile(r"\b(?:is there (?:a|an|any) (?:tool|library|cli|app|plugin|skill|way)"
                      r"|looking for (?:a|an) (?:tool|library|cli|app|plugin|skill|way)"
                      r"|recommend(?:ation)?s? (?:for|on)|what (?:tools?|librar(?:y|ies)|clis?|apps?) (?:do|should|would)"
                      r"|which (?:tool|library|cli|app|plugin)|alternatives? to|any (?:tools?|librar(?:y|ies)|clis?|"
                      r"apps?|plugins?) (?:for|that))\b", re.I)
PROBLEM = re.compile(r"\b(?:error|exception|traceback|fail(?:s|ed|ing)? to|broken|bug|crash(?:es|ed)?"
                     r"|doesn'?t work|not working|stack ?trace)\b", re.I)
CLAIM = re.compile(r"\b(?:i think|i believe|unpopular opinion|hot take|in my experience|the problem with"
                   r"|nobody talks about|overrated|underrated)\b", re.I)
HELP_LABELS = frozenset({"help wanted", "help-wanted", "question", "good first issue", "needs help"})
# Text addressed to an AI assistant is an instruction, not post content (same idea as release_brief.ADDRESSED).
ADDRESSED = re.compile(r"(?:^|[\s>*_\"'(])(?:claude|chatgpt|gpt-?\d*|copilot|gemini|assistant|ai agent|llm)\s*[,:]",
                       re.I)


# --------------------------------------------------------------------- small helpers

def _fail(where: str, message: str) -> ValidationError:
    return ValidationError(f"{where}: {message}")


def _now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(parse_utc(value))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _read_bounded(path: str | Path, limit: int, label: str) -> str:
    source = Path(path)
    try:
        with source.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise ValidationError(f"{label} {source} cannot be read ({exc.__class__.__name__})") from None
    if len(raw) > limit:
        raise ValidationError(f"{label} {source} is larger than {limit} bytes")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise ValidationError(f"{label} {source} is not UTF-8 text") from None


def _read_json(path: str | Path, limit: int, label: str) -> Any:
    text = _read_bounded(path, limit, label)
    try:
        return loads_text(text, f"{label} {Path(path).name}")
    except json.JSONDecodeError:
        raise ValidationError(f"{label} {Path(path).name} is not valid JSON; nothing was changed") from None


def _str(value: object, where: str, limit: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise _fail(where, f"must be at most {limit} characters")
    return value


def _int(value: object, where: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise _fail(where, f"must be an integer {low}..{high}")
    return value


def _str_list(value: object, where: str, limit: int, item_limit: int = MAX_TERM_CHARS) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise _fail(where, f"must be a list of at most {limit} strings")
    out: list[str] = []
    for index, item in enumerate(value):
        text = _str(item, f"{where}[{index}]", item_limit)
        assert text is not None
        if text not in out:
            out.append(text)
    return out


def _clean_url(value: str) -> str:
    """Public HTTPS URL without fragment (a comment anchor is dropped for identity and dedupe)."""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))


def _norm(text: str) -> str:
    return " ".join(_normalize(text).split())


def human_only_platforms() -> frozenset[str]:
    """Reddit, Hacker News and DEV always; plus every scout platform the distribution registry marks human_only."""
    try:
        from .distribution import load_registry
        flagged = {p["id"] for p in load_registry().get("platforms", []) if p.get("human_only") is True}
    except (ValidationError, OSError, ValueError, KeyError, TypeError):
        flagged = set()
    return _HUMAN_ONLY_BASE | frozenset(p for p in PLATFORMS if REGISTRY_IDS.get(p, p) in flagged)


def author_key(platform: str, handle: str) -> str:
    """A stable id for an author; the state file stores this, never the handle."""
    return hashlib.sha256(f"{platform}:{normalize_handle(handle)}".encode()).hexdigest()[:16]


def item_id(platform: str, url: str) -> str:
    return f"{platform}-{hashlib.sha256(_clean_url(url).encode()).hexdigest()[:12]}"


# --------------------------------------------------------------------- config

def _project(raw: object, where: str) -> dict:
    if not isinstance(raw, dict):
        raise _fail(where, "must be an object")
    unknown = set(raw) - PROJECT_KEYS
    if unknown:
        raise _fail(where, f"unknown keys {', '.join(sorted(unknown))}")
    pid = _str(raw.get("id"), f"{where}.id", 63)
    if not SLUG.fullmatch(pid or ""):
        raise _fail(f"{where}.id", "must be lowercase letters, digits and '-'")
    out: dict[str, Any] = {"id": pid, "name": _str(raw.get("name", pid), f"{where}.name", 80)}
    for key in ("link", "docs"):
        value = raw.get(key)
        if value is not None and (not isinstance(value, str) or not public_url(value)):
            raise _fail(f"{where}.{key}", "must be a public https URL without credentials")
        out[key] = value
    for key in ("topics", "keywords", "provides"):
        out[key] = _str_list(raw.get(key, []), f"{where}.{key}", MAX_TERMS)
    if not (out["topics"] or out["keywords"] or out["provides"]):
        raise _fail(where, "needs at least one of topics, keywords or provides")
    out["terms"] = [(t, _norm(t)) for t in dict.fromkeys(out["topics"] + out["keywords"] + out["provides"])
                    if _norm(t)]
    out["provides_terms"] = [(t, _norm(t)) for t in out["provides"] if _norm(t)]
    return out


def validate_config(raw: object) -> dict:
    """Strict, all-or-nothing validation of the owner's scout config (unknown keys refused)."""
    if not isinstance(raw, dict):
        raise ValidationError("engage-scout config must be a JSON object")
    unknown = set(raw) - CONFIG_KEYS
    if unknown:
        raise ValidationError(f"engage-scout config: unknown keys {', '.join(sorted(unknown))}")
    if raw.get("schema_version") != SCHEMA_VERSION or raw.get("kind") != CONFIG_KIND:
        raise ValidationError(f"engage-scout config needs schema_version {SCHEMA_VERSION} and kind {CONFIG_KIND}")
    projects = raw.get("projects")
    if not isinstance(projects, list) or not 1 <= len(projects) <= MAX_PROJECTS:
        raise ValidationError(f"config.projects must list 1..{MAX_PROJECTS} projects")
    parsed = [_project(p, f"config.projects[{i}]") for i, p in enumerate(projects)]
    if len({p["id"] for p in parsed}) != len(parsed):
        raise ValidationError("config.projects: project ids must be unique")
    platforms = _str_list(raw.get("platforms", list(PLATFORMS)), "config.platforms", len(PLATFORMS), 20)
    bad = [p for p in platforms if p not in PLATFORMS]
    if bad:
        raise ValidationError(f"config.platforms: unknown {', '.join(bad)} (allowed: {', '.join(PLATFORMS)})")
    hashtags = []
    for index, tag in enumerate(_str_list(raw.get("hashtags", []), "config.hashtags", 5, 51)):
        match = HASHTAG.fullmatch(tag)
        if not match:
            raise _fail(f"config.hashtags[{index}]", "must be letters, digits or _ (2-50), optional leading #")
        hashtags.append(match.group(1).lower())
    search_terms = _str_list(raw.get("search_terms", []), "config.search_terms", 5)
    server = raw.get("mastodon_server")
    if server is not None:
        if not isinstance(server, str):
            raise ValidationError("config.mastodon_server must be an https origin")
        server = require_https_public_server(server)
    owner = raw.get("owner_handles", {})
    if not isinstance(owner, dict) or set(owner) - set(PLATFORMS):
        raise ValidationError(f"config.owner_handles must map platforms ({', '.join(PLATFORMS)}) to handles")
    owner_handles = {p: [normalize_handle(h) for h in _str_list(v if isinstance(v, list) else [v],
                                                                 f"config.owner_handles.{p}", 10)]
                     for p, v in owner.items()}
    quiet = [normalize_handle(h) for h in _str_list(raw.get("quiet_accounts", []), "config.quiet_accounts", 1000)]
    link = raw.get("link_cap", DEFAULT_LINK_CAP)
    if not isinstance(link, dict) or set(link) != {"links", "per_replies"}:
        raise ValidationError("config.link_cap must be {\"links\": N, \"per_replies\": M}")
    link_cap = {"links": _int(link["links"], "config.link_cap.links", 0, 10),
                "per_replies": _int(link["per_replies"], "config.link_cap.per_replies", 1, 100)}
    if link_cap["links"] > link_cap["per_replies"]:
        raise ValidationError("config.link_cap.links cannot exceed per_replies")
    band = raw.get("audience_band", DEFAULT_AUDIENCE_BAND)
    if not isinstance(band, dict) or set(band) != {"min_followers", "max_followers"}:
        raise ValidationError("config.audience_band must be {\"min_followers\": N, \"max_followers\": M}")
    audience = {"min_followers": _int(band["min_followers"], "config.audience_band.min_followers", 1, 10**9),
                "max_followers": _int(band["max_followers"], "config.audience_band.max_followers", 1, 10**10)}
    if audience["min_followers"] > audience["max_followers"]:
        raise ValidationError("config.audience_band.min_followers cannot exceed max_followers")
    min_rel = raw.get("min_relevance", DEFAULT_MIN_RELEVANCE)
    if type(min_rel) not in (int, float) or not 0 <= min_rel <= 1:
        raise ValidationError("config.min_relevance must be a number 0..1")
    ages = raw.get("max_age_hours", {})
    if not isinstance(ages, dict) or set(ages) - set(PLATFORMS) - {"default"}:
        raise ValidationError("config.max_age_hours must map 'default' or platforms to hours")
    max_age = {"default": DEFAULT_MAX_AGE_HOURS, **DEFAULT_MAX_AGE}
    for key, value in ages.items():
        max_age[key] = _int(value, f"config.max_age_hours.{key}", 1, 24 * 90)
    return {"projects": parsed, "platforms": platforms, "hashtags": hashtags, "search_terms": search_terms,
            "mastodon_server": server, "owner_handles": owner_handles, "quiet_accounts": set(quiet),
            "daily_cap": _int(raw.get("daily_cap", DEFAULT_DAILY_CAP), "config.daily_cap", 1, MAX_LIMIT),
            "link_cap": link_cap,
            "same_account_days": _int(raw.get("same_account_days", DEFAULT_SAME_ACCOUNT_DAYS),
                                      "config.same_account_days", 0, 365),
            "audience_band": audience, "min_relevance": float(min_rel), "max_age_hours": max_age,
            "per_source_limit": _int(raw.get("per_source_limit", DEFAULT_PER_SOURCE_LIMIT),
                                     "config.per_source_limit", 1, MAX_PER_SOURCE_LIMIT)}


def load_config(path: str | Path) -> dict:
    return validate_config(_read_json(path, MAX_CONFIG_BYTES, "Config file"))


# --------------------------------------------------------------------- candidates (owner files and online sources)

def _opt_count(value: object, where: str) -> int | None:
    if value is None:
        return None
    return _int(value, where, 0, 10**10)


def validate_candidate(raw: object, where: str, source: str) -> dict:
    """One candidate conversation, in the owner-file shape that online sources also produce."""
    if not isinstance(raw, dict):
        raise _fail(where, "must be an object")
    unknown = set(raw) - ITEM_KEYS
    if unknown:
        raise _fail(where, f"unknown keys {', '.join(sorted(unknown))}")
    for key in ITEM_REQUIRED:
        if key not in raw:
            raise _fail(where, f"missing {key}")
    platform = raw["platform"]
    if platform not in PLATFORMS:
        raise _fail(f"{where}.platform", f"must be one of {', '.join(PLATFORMS)}")
    url = raw["url"]
    if not isinstance(url, str) or len(url) > 2048 or not public_url(_clean_url(url)):
        raise _fail(f"{where}.url", "must be a public https URL without credentials")
    handle = _str(raw["author_handle"], f"{where}.author_handle", 200)
    created = datetime.fromisoformat(parse_utc(_str(raw["created_at"], f"{where}.created_at", 40) or ""))
    text = raw.get("text", "")
    title = raw.get("title")
    if not isinstance(text, str) or (title is not None and not isinstance(title, str)):
        raise _fail(where, "text and title must be strings")
    if not text.strip() and not (title or "").strip():
        raise _fail(where, "needs text or title")
    labels = raw.get("labels", [])
    if not isinstance(labels, list) or len(labels) > 50 or not all(isinstance(x, str) for x in labels):
        raise _fail(f"{where}.labels", "must be a list of at most 50 strings")
    flags = {}
    for key in BOOL_FLAGS:
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise _fail(f"{where}.{key}", "must be true or false")
        flags[key] = value
    if "id" in raw:
        _str(raw["id"], f"{where}.id", 200)  # owner reference only; the scout derives its own id from the URL
    return {"id": item_id(platform, url), "platform": platform, "url": _clean_url(url), "author": handle,
            "author_key": author_key(platform, handle or ""), "created_at": created, "raw_text": text[:200_000],
            "raw_title": (title or "")[:1000], "followers": _opt_count(raw.get("author_followers"),
                                                                        f"{where}.author_followers"),
            "replies": _opt_count(raw.get("reply_count"), f"{where}.reply_count"),
            "labels": [x.strip().casefold() for x in labels if x.strip()], **flags, "source": source}


def load_candidates(path: str | Path) -> list[dict]:
    data = _read_json(path, MAX_INPUT_BYTES, "Candidates file")
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or data.get("kind") != INPUT_KIND
            or not isinstance(data.get("items"), list)):
        raise ValidationError(f"Candidates file {Path(path).name} needs schema_version {SCHEMA_VERSION}, "
                              f"kind {INPUT_KIND} and an items list")
    if set(data) - {"schema_version", "kind", "items", "description"}:
        raise ValidationError(f"Candidates file {Path(path).name}: unknown top-level keys")
    if len(data["items"]) > MAX_ITEMS:
        raise ValidationError(f"Candidates file {Path(path).name} holds more than {MAX_ITEMS} items")
    return [validate_candidate(item, f"{Path(path).name} items[{i}]", "owner_file")
            for i, item in enumerate(data["items"])]


# --------------------------------------------------------------------- untrusted text

def clean_untrusted(text: str) -> tuple[str, dict]:
    """Sanitised, redacted text with instruction-like lines withheld, plus the safety analysis."""
    clean, flags = sanitize_text(text, MAX_TEXT_CHARS)
    clean = redact_private_data(clean)[0]
    result = analyze(clean, flags)
    hidden = any(m["rule"] == "hidden_unicode_controls" for m in result["matched_rules"])
    suspicious = (any(m["label"] == "suspicious" and m["rule"] != "hidden_unicode_controls"
                      for m in result["matched_rules"]) or bool(ADDRESSED.search(clean)))
    withheld = 0
    if suspicious:
        lines = []
        for line in clean.split("\n"):
            hit = ADDRESSED.search(line) or any(m["label"] == "suspicious" for m in analyze(line)["matched_rules"])
            if hit and line.strip():
                lines.append(WITHHELD)
                withheld += 1
            else:
                lines.append(line)
        clean = "\n".join(lines)
        if not withheld:  # the pattern spans lines: withhold the whole text rather than guess
            clean, withheld = WITHHELD, 1
    labels = {m["label"] for m in result["matched_rules"]}
    return clean, {"withheld_lines": withheld, "instruction_like": suspicious, "hidden_controls": hidden,
                   "spam": "spam" in labels,
                   "harassment": "harassment" in labels, "opt_out_request": result["opt_out"]}


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= EXCERPT_CHARS else flat[:EXCERPT_CHARS - 1].rstrip() + "…"


# --------------------------------------------------------------------- scoring

def _matches(text: str, terms: list[tuple[str, str]]) -> list[str]:
    """Config terms (original, normalised) whose normalised words appear as a phrase in ``text``.

    A term contained in a longer matched term ("claude code" in "claude code skill") is not counted twice.
    """
    padded = f" {_norm(text)} "
    hits = [(term, norm) for term, norm in terms if f" {norm} " in padded]
    return [term for term, norm in hits
            if not any(other != norm and f" {norm} " in f" {other} " for _, other in hits)]


def _longest_first(term: str) -> tuple[int, str]:
    return (-len(term), term)


def relevance(item: dict, config: dict) -> tuple[float, dict]:
    """Owner-term overlap; a match in the title or first line weighs 1.0, elsewhere 0.6; 1 - exp(-sum)."""
    head = f"{item['title']}\n{item['text'].split(chr(10), 1)[0]}"
    best: tuple[float, dict] = (0.0, {"project": None, "matched": []})
    known = {norm for project in config["projects"] for _, norm in project["terms"]}
    hashtags = [(f"#{t}", t) for t in config["hashtags"] if t not in known]
    for project in config["projects"]:
        terms = project["terms"] + hashtags
        in_head = set(_matches(head, terms))
        in_body = {t for t in _matches(f"{item['title']}\n{item['text']}", terms)
                   if t not in in_head and not any(f" {_norm(t)} " in f" {_norm(h)} " for h in in_head)}
        weight = len(in_head) * 1.0 + len(in_body) * 0.6
        value = round(1 - math.exp(-weight), 3) if weight else 0.0
        if value > best[0]:
            matched = [{"term": t, "where": "title_or_first_line"} for t in sorted(in_head, key=_longest_first)]
            matched += [{"term": t, "where": "body"} for t in sorted(in_body, key=_longest_first)]
            best = (value, {"project": project["id"], "matched": matched})
    return best


def freshness(platform: str, age_hours: float) -> float:
    return round(0.5 ** (max(0.0, age_hours) / HALF_LIFE_HOURS.get(platform, 24)), 3)


def _follower_fit(followers: int | None, band: dict) -> float:
    if followers is None:
        return 0.5
    low, high = band["min_followers"], band["max_followers"]
    value = max(followers, 1)
    if low <= value <= high:
        return 1.0
    decades = math.log10(low / value) if value < low else math.log10(value / high)
    return round(max(0.1, 1 - 0.4 * decades), 3)


def _reply_fit(replies: int | None) -> float:
    return 0.6 if replies is None else round(1 / (1 + replies / 10), 3)


def audience_fit(item: dict, band: dict) -> tuple[float, dict]:
    follower, reply = _follower_fit(item["followers"], band), _reply_fit(item["replies"])
    return round(math.sqrt(follower * reply), 3), {"followers": item["followers"], "follower_fit": follower,
                                                   "replies_so_far": item["replies"], "reply_fit": reply}


def contribution(item: dict) -> tuple[float, list[str], bool]:
    """Does the post ask, report a problem or make a claim the owner can address? Returns (value, signals, tool_ask)."""
    text = f"{item['title']}\n{item['text']}"
    value, signals = 0.15, []
    if QUESTION.search(text):
        value += 0.3
        signals.append("asks a question")
    if HELP_ASK.search(text):
        value += 0.3
        signals.append("asks for help or advice")
    if PROBLEM.search(text):
        value += 0.25
        signals.append("reports a problem or error")
    if HELP_LABELS & set(item["labels"]):
        value += 0.3
        signals.append("labelled " + ", ".join(sorted(HELP_LABELS & set(item["labels"]))))
    if CLAIM.search(text):
        value += 0.15
        signals.append("makes a claim you can add a data point to")
    tool_ask = bool(TOOL_ASK.search(text))
    if tool_ask:
        signals.append("asks for a tool or approach")
    return round(min(1.0, value), 3), signals, tool_ask


def risk(item: dict, *, replies_to_author: int, link_cap_reached: bool) -> tuple[float, list[str]]:
    value, reasons = VENUE_RISK.get(item["platform"], 0.2), [f"venue strictness {VENUE_RISK.get(item['platform'], 0.2)}"]
    if item["safety"]["instruction_like"]:
        value += 0.3
        reasons.append("instruction-like text was withheld")
    elif item["safety"]["hidden_controls"]:
        value += 0.3
        reasons.append("hidden bidirectional control characters were removed")
    if replies_to_author:
        value += min(0.3, 0.1 * replies_to_author)
        reasons.append(f"you replied to this account {replies_to_author} time(s) in {RECENT_AUTHOR_DAYS} days")
    if link_cap_reached:
        value += 0.1
        reasons.append("link cap reached: answer without a link")
    return round(min(0.9, value), 3), reasons


# --------------------------------------------------------------------- state (ids only, 0600)

def _empty_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND, "shown": [], "marks": []}


def load_state(path: str | Path | None) -> dict:
    """``shown`` [{id, platform, author_key, shown_at}] and ``marks`` [{id, ..., action, link, marked_at}]."""
    if path is None:
        return _empty_state()
    target = Path(path)
    if target.is_symlink():
        raise ValidationError(f"State file {target} cannot be a symbolic link")
    if not target.exists():
        return _empty_state()
    if os.name == "posix" and stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise ValidationError(f"State file {target} is not private; restrict it with chmod 600 before running")
    data = _read_json(target, MAX_STATE_BYTES, "State file")
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION or data.get("kind") != STATE_KIND
            or not isinstance(data.get("shown"), list) or not isinstance(data.get("marks"), list)):
        raise ValidationError(f"State file {target} is not an engage-scout state file "
                              f"(schema_version {SCHEMA_VERSION}, kind {STATE_KIND})")

    def ok(entry: object, keys: tuple[str, ...]) -> bool:
        return isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in keys)

    shown = [e for e in data["shown"] if ok(e, ("id", "platform", "author_key", "shown_at"))]
    marks = [e for e in data["marks"] if ok(e, ("id", "platform", "author_key", "action", "marked_at"))
             and e["action"] in ("replied", "skipped")]
    return {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND, "shown": shown, "marks": marks}


def write_state(path: str | Path, state: dict) -> None:
    """Atomic replace through a new 0600 file; refuses a symlink."""
    target = Path(path)
    if target.is_symlink():
        raise ValidationError(f"State file {target} cannot be a symbolic link")
    merged = {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND, "shown": state["shown"][-MAX_STATE_ENTRIES:],
              "marks": state["marks"][-MAX_STATE_ENTRIES:]}
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".engage-scout-state-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _when(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(parse_utc(value))
    except ValidationError:
        return None


def record_shown(state: dict, report: dict, now: datetime) -> int:
    known = {e["id"] for e in state["shown"]}
    added = 0
    for item in report["shortlist"] + report["read_it_yourself"]:
        if item["id"] not in known:
            state["shown"].append({"id": item["id"], "platform": item["platform"], "author_key": item["author_key"],
                                   "shown_at": _iso(now)})
            known.add(item["id"])
            added += 1
    return added


def mark(state: dict, item: str, action: str, *, link: bool, at: datetime) -> dict:
    """Record that the owner replied to (or skipped) a shortlist item; the item must have been recorded as shown."""
    if not ITEM_ID.fullmatch(item):
        raise ValidationError("--mark takes a shortlist id such as bluesky-0123456789ab")
    shown = next((e for e in state["shown"] if e["id"] == item), None)
    if shown is None:
        raise ValidationError(f"{item} is not in this state file; run engage-scout --record FILE first")
    if action != "replied" and link:
        raise ValidationError("--with-link only applies to --replied")
    entry = {"id": item, "platform": shown["platform"], "author_key": shown["author_key"], "action": action,
             "link": link, "marked_at": _iso(at)}
    state["marks"] = [e for e in state["marks"] if e["id"] != item] + [entry]
    return entry


# --------------------------------------------------------------------- the pipeline

def _angle(item: dict, project: dict | None, matched: list[dict], signals: list[str], link_allowed: bool) -> list[str]:
    """One or two hint bullets built from config terms only; never post text and never a finished reply."""
    term = matched[0]["term"] if matched else (project["name"] if project else "the topic")
    bullets: list[str] = []
    if "reports a problem or error" in signals:
        bullets.append(f"Help with the problem first: ask for the exact error or version, or share the fix you "
                       f"know from your work on {term}.")
    elif "asks a question" in signals or "asks for help or advice" in signals:
        bullets.append(f"Answer the question directly from your own experience with {term}; one concrete step "
                       f"beats a summary.")
    elif any(s.startswith("makes a claim") for s in signals):
        bullets.append(f"Add one data point or counterexample from your own work on {term}.")
    else:
        bullets.append(f"Add one specific experience about {term} and ask a genuine follow-up question.")
    if link_allowed and project:
        bullets.append(f"Only if {project['name']} genuinely answers what they asked, mention it after the answer, "
                       f"with the disclosure line.")
    else:
        bullets.append("No link: the reply should stand on its own.")
    return bullets


def scout(candidates: list[dict], config: dict, *, now: datetime, state: dict, optout: list[dict],
          platforms: list[str] | None = None, limit: int | None = None) -> dict:
    """normalise -> filter -> score -> cap -> shortlist. Pure: no network, no file, no database."""
    wanted = set(platforms or config["platforms"]) & set(config["platforms"])
    human_only = human_only_platforms()
    projects = {p["id"]: p for p in config["projects"]}
    shown_ids = {e["id"] for e in state["shown"]}
    marked = {e["id"]: e for e in state["marks"]}
    replies = sorted((e for e in state["marks"] if e["action"] == "replied"), key=lambda e: e["marked_at"])
    recent_cut = now - timedelta(days=RECENT_AUTHOR_DAYS)
    same_cut = now - timedelta(days=config["same_account_days"])
    author_recent: dict[str, int] = {}
    author_blocked: set[str] = set()
    for entry in replies:
        when = _when(entry["marked_at"])
        if when and when >= recent_cut:
            author_recent[entry["author_key"]] = author_recent.get(entry["author_key"], 0) + 1
        if when and config["same_account_days"] and when >= same_cut:
            author_blocked.add(entry["author_key"])
    for entry in state["shown"]:  # suggested within the window counts too: one suggestion per account per window
        when = _when(entry["shown_at"])
        if when and config["same_account_days"] and when >= same_cut and entry["platform"] not in human_only:
            author_blocked.add(entry["author_key"])
    cap = config["link_cap"]
    window = replies[-(cap["per_replies"] - 1):] if cap["per_replies"] > 1 else []
    links_recent = sum(1 for e in window if e.get("link") is True)
    link_budget = max(0, cap["links"] - links_recent)
    today = now.date()
    shown_today = sum(1 for e in state["shown"] if (_when(e["shown_at"]) or now).date() == today
                      and e["platform"] not in human_only)
    daily = min(config["daily_cap"], limit) if limit else config["daily_cap"]
    remaining = max(0, daily - shown_today)

    dropped: dict[str, int] = {}
    dropped_items: list[dict] = []

    def drop(cand: dict, reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1
        dropped_items.append({"id": cand["id"], "platform": cand["platform"], "reason": reason})

    seen_urls: set[str] = set()
    eligible: list[dict] = []
    reading: list[dict] = []
    for cand in candidates:
        if cand["url"] in seen_urls:
            drop(cand, "duplicate")
            continue
        seen_urls.add(cand["url"])
        platform = cand["platform"]
        handle = normalize_handle(cand["author"])
        if platform not in wanted:
            drop(cand, "platform_not_selected")
            continue
        if handle in config["owner_handles"].get(platform, []):
            drop(cand, "own_post")
            continue
        if listed_entry(cand["author"], optout, platform if platform in OPTOUT_PLATFORMS else None):
            drop(cand, "opt_out")  # the handle is never printed
            continue
        if handle in config["quiet_accounts"]:
            drop(cand, "quiet_account")
            continue
        if cand["author_is_bot"]:
            drop(cand, "bot_account")
            continue
        if cand["author_not_discoverable"]:
            drop(cand, "author_not_discoverable")
            continue
        if cand["owner_replied"] or cand["id"] in marked:
            drop(cand, "already_engaged")
            continue
        if cand["id"] in shown_ids:
            drop(cand, "already_shown")
            continue
        if cand["locked"] or cand["deleted"] or cand["reply_restricted"]:
            drop(cand, "locked_deleted_or_reply_restricted")
            continue
        age = (now - cand["created_at"]).total_seconds() / 3600
        if age < -1:
            drop(cand, "timestamp_in_future")
            continue
        if age > config["max_age_hours"].get(platform, config["max_age_hours"]["default"]):
            drop(cand, "too_old")
            continue
        text, safety = clean_untrusted(cand["raw_text"])
        title = clean_untrusted(cand["raw_title"])[0] if cand["raw_title"] else ""
        if safety["spam"] or safety["harassment"]:
            drop(cand, "spam_or_harassment")
            continue
        if safety["opt_out_request"]:
            drop(cand, "asks_not_to_be_contacted")
            continue
        item = {**cand, "text": text, "title": title, "safety": safety, "age_hours": round(max(0.0, age), 1)}
        rel, rel_detail = relevance(item, config)
        if rel < config["min_relevance"]:
            drop(cand, "low_relevance")
            continue
        item["relevance"], item["relevance_detail"] = rel, rel_detail
        item["freshness"] = freshness(platform, age)
        if platform in human_only:
            reading.append(item)
        else:
            eligible.append(item)

    for item in eligible:
        item["audience_fit"], item["audience_detail"] = audience_fit(item, config["audience_band"])
        item["contribution"], item["signals"], item["tool_ask"] = contribution(item)
        item["risk"], item["risk_reasons"] = risk(item, replies_to_author=author_recent.get(item["author_key"], 0),
                                                  link_cap_reached=link_budget == 0)
        item["score"] = round(item["relevance"] * item["freshness"] * item["audience_fit"] * item["contribution"]
                              * (1 - item["risk"]), 4)
    eligible.sort(key=lambda i: (-i["score"], i["age_hours"], i["id"]))

    shortlist: list[dict] = []
    authors: set[str] = set()
    links_used = 0
    for item in eligible:
        if item["author_key"] in author_blocked:
            drop(item, "same_account_within_days")
            continue
        if item["author_key"] in authors:
            drop(item, "same_account_this_run")
            continue
        if len(shortlist) >= remaining:
            drop(item, "daily_cap")
            continue
        authors.add(item["author_key"])
        project = projects.get(item["relevance_detail"]["project"] or "")
        provides = bool(project and _matches(f"{item['title']}\n{item['text']}", project["provides_terms"]))
        if not item["tool_ask"]:
            link_allowed, link_reason = False, "the post does not ask for a tool or approach"
        elif not (project and project.get("link")):
            link_allowed, link_reason = False, "no project link in the config"
        elif not provides:
            link_allowed, link_reason = False, "the ask does not match what the project provides"
        elif links_used >= link_budget:
            link_allowed, link_reason = False, (f"link cap: at most {cap['links']} link(s) in {cap['per_replies']} "
                                                f"replies ({links_recent} in your recent recorded replies)")
        else:
            link_allowed, link_reason = True, "the post asks for a tool the project provides and the link cap allows it"
            links_used += 1
        why = [f"matches '{m['term']}' ({m['where'].replace('_', ' ')})" for m in item["relevance_detail"]["matched"]]
        why += item["signals"] + [f"posted {item['age_hours']} h ago"]
        shortlist.append({
            "rank": len(shortlist) + 1, "id": item["id"], "platform": item["platform"], "url": item["url"],
            "author": item["author"], "author_key": item["author_key"], "age_hours": item["age_hours"],
            "source": item["source"], "project": item["relevance_detail"]["project"],
            "score": item["score"],
            "factors": {"relevance": item["relevance"], "freshness": item["freshness"],
                        "audience_fit": item["audience_fit"], "contribution": item["contribution"],
                        "risk": item["risk"]},
            "factor_detail": {"matched_terms": item["relevance_detail"]["matched"],
                              "half_life_hours": HALF_LIFE_HOURS.get(item["platform"], 24),
                              "audience": item["audience_detail"], "contribution_signals": item["signals"],
                              "risk_reasons": item["risk_reasons"]},
            "why": why, "angle": _angle(item, project, item["relevance_detail"]["matched"], item["signals"],
                                        link_allowed),
            "link": {"allowed": link_allowed, "reason": link_reason,
                     "project_link": project.get("link") if (link_allowed and project) else None,
                     "disclosure": DISCLOSURE if link_allowed else None},
            "venue_rule": VENUE_RULES[item["platform"]],
            "excerpt": _excerpt(f"{item['title']} {item['text']}".strip()),
            "excerpt_status": "untrusted_post_text_cleaned_not_verbatim",
            "text_withheld_lines": item["safety"]["withheld_lines"],
        })

    reading.sort(key=lambda i: (-(i["relevance"] * i["freshness"]), i["age_hours"], i["id"]))
    read_list: list[dict] = []
    for item in reading:
        if len(read_list) >= daily:
            drop(item, "daily_cap")
            continue
        read_list.append({"id": item["id"], "platform": item["platform"], "url": item["url"],
                          "author_key": item["author_key"], "age_hours": item["age_hours"],
                          "relevance": item["relevance"], "freshness": item["freshness"],
                          "matched_terms": item["relevance_detail"]["matched"],
                          "mode": "read_it_yourself", "angle": None, "link": None,
                          "venue_rule": VENUE_RULES[item["platform"]]})
    return {"schema": OUTPUT_SCHEMA, "generated_at": _iso(now), "note": NOTE, "heuristics_note": HEURISTICS_NOTE,
            "reach_prediction": None,
            "caps": {"daily_cap": daily, "already_shown_today": shown_today, "slots_left": remaining,
                     "link_cap": cap, "links_in_recent_recorded_replies": links_recent,
                     "link_suggestions_this_run": links_used, "same_account_days": config["same_account_days"]},
            "shortlist": shortlist, "read_it_yourself": read_list,
            "dropped": dict(sorted(dropped.items())), "dropped_items": dropped_items,
            "candidates_seen": len(candidates)}


# --------------------------------------------------------------------- weekly readout

def load_outcomes(path: str | Path) -> list[dict]:
    """Owner-recorded snapshots: cumulative ``followers``/``stars`` and an optional ``profile_visits`` as reported."""
    data = _read_json(path, MAX_INPUT_BYTES, "Outcomes file")
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION
            or data.get("kind") != OUTCOMES_KIND or not isinstance(data.get("snapshots"), list)):
        raise ValidationError(f"Outcomes file needs schema_version {SCHEMA_VERSION}, kind {OUTCOMES_KIND} and a "
                              "snapshots list")
    if set(data) - {"schema_version", "kind", "snapshots", "description"}:
        raise ValidationError("Outcomes file: unknown top-level keys")
    out = []
    for index, raw in enumerate(data["snapshots"][:MAX_STATE_ENTRIES]):
        where = f"snapshots[{index}]"
        if not isinstance(raw, dict) or set(raw) - {"observed_at", "platform", "followers", "stars",
                                                    "profile_visits", "source"}:
            raise _fail(where, "allowed keys: observed_at, platform, followers, stars, profile_visits, source")
        if raw.get("platform") not in PLATFORMS:
            raise _fail(f"{where}.platform", f"must be one of {', '.join(PLATFORMS)}")
        entry = {"observed_at": datetime.fromisoformat(parse_utc(_str(raw.get("observed_at"), where, 40) or "")),
                 "platform": raw["platform"]}
        for key in ("followers", "stars", "profile_visits"):
            entry[key] = _opt_count(raw.get(key), f"{where}.{key}")
        _str(raw.get("source"), f"{where}.source", 200, required=False)
        out.append(entry)
    return sorted(out, key=lambda e: e["observed_at"])


def _value_at(snapshots: list[dict], platform: str, key: str, at: datetime) -> int | None:
    found = None
    for snap in snapshots:
        if snap["observed_at"] > at:
            break
        if snap["platform"] == platform and snap[key] is not None:
            found = snap[key]
    return found


def _inside(stamp: str, start: datetime, stop: datetime) -> bool:
    when = _when(stamp)
    return bool(when and start < when <= stop)


def readout(state: dict, outcomes: list[dict] | None, *, now: datetime, weeks: int) -> dict:
    """Per-week replies (from engage-log marks) next to follower, star and visit changes. Correlational only."""
    end = now
    rows = []
    platforms = sorted({e["platform"] for e in state["marks"]} | {s["platform"] for s in outcomes or []})
    for back in range(weeks):
        stop, start = end - timedelta(days=7 * back), end - timedelta(days=7 * (back + 1))

        marks = [e for e in state["marks"] if _inside(e["marked_at"], start, stop)]
        row: dict[str, Any] = {"week_start": _iso(start), "week_end": _iso(stop),
                               "shown": sum(1 for e in state["shown"] if _inside(e["shown_at"], start, stop)),
                               "replied": sum(1 for e in marks if e["action"] == "replied"),
                               "replied_with_link": sum(1 for e in marks if e["action"] == "replied" and e.get("link")),
                               "skipped": sum(1 for e in marks if e["action"] == "skipped"), "platforms": {}}
        for platform in platforms:
            cell: dict[str, Any] = {"replied": sum(1 for e in marks if e["platform"] == platform
                                                   and e["action"] == "replied")}
            if outcomes is not None:
                for key in ("followers", "stars"):
                    before, after = (_value_at(outcomes, platform, key, start),
                                     _value_at(outcomes, platform, key, stop))
                    cell[f"{key}_change"] = after - before if before is not None and after is not None else None
                visits = [s["profile_visits"] for s in outcomes if s["platform"] == platform
                          and s["profile_visits"] is not None and start < s["observed_at"] <= stop]
                cell["profile_visits_reported"] = visits[-1] if visits else None
            row["platforms"][platform] = cell
        rows.append(row)
    return {"schema": READOUT_SCHEMA, "generated_at": _iso(now), "weeks": rows, "label": "correlational",
            "note": CORRELATIONAL + " Unknown values are null, never 0." +
                    ("" if outcomes is not None else " No --outcomes file was given, so only reply counts are shown."),
            "see_also": "attribution-report (UTM and referrer visits) and followup-suggest (per-post milestones)"}


# --------------------------------------------------------------------- rendering

def render_text(report: dict) -> str:
    lines = [f"Engagement scout ({report['generated_at']}): {len(report['shortlist'])} to consider, "
             f"{len(report['read_it_yourself'])} to read yourself", report["note"], ""]
    caps = report["caps"]
    lines.append(f"Caps: {caps['slots_left']} of {caps['daily_cap']} daily slots left; links: at most "
                 f"{caps['link_cap']['links']} in {caps['link_cap']['per_replies']} replies; same account once per "
                 f"{caps['same_account_days']} days.")
    for item in report["shortlist"]:
        f = item["factors"]
        lines += ["", f"{item['rank']}. [{item['platform']}] {item['url']}  (id {item['id']})",
                  f"   by {item['author']}, {item['age_hours']} h ago; score {item['score']} = relevance "
                  f"{f['relevance']} x freshness {f['freshness']} x audience {f['audience_fit']} x contribution "
                  f"{f['contribution']} x (1 - risk {f['risk']})",
                  "   why: " + "; ".join(item["why"]),
                  f"   post (untrusted, cleaned): {item['excerpt']}"]
        lines += [f"   angle: {bullet}" for bullet in item["angle"]]
        link = item["link"]
        lines.append(f"   link: {'yes, ' + link['project_link'] if link['allowed'] else 'no'} ({link['reason']})")
        if link["allowed"]:
            lines.append(f"   disclosure (use it with the link): {link['disclosure']}")
        lines.append(f"   rule: {item['venue_rule']}")
    if report["read_it_yourself"]:
        lines += ["", "Read it yourself (human-only venues: no angle, no link, no drafted text):"]
        for item in report["read_it_yourself"]:
            lines.append(f"- [{item['platform']}] {item['url']} ({item['age_hours']} h ago) - {item['venue_rule']}")
    if report["dropped"]:
        lines += ["", "Dropped: " + ", ".join(f"{k} {v}" for k, v in report["dropped"].items())]
    for source in report.get("sources", []):
        lines.append(f"Source {source['source']}: {source['status']}"
                     + (f" ({source['received']} received)" if "received" in source else ""))
    if report.get("planned_requests"):
        lines += ["", "Not queried (add --online to run these read-only GETs, no credential):"]
        lines += [f"- GET {r['url']}" for r in report["planned_requests"]]
    lines += ["", report["heuristics_note"],
              "Record what you did: reachweaver engage-log --state FILE --mark ID --replied [--with-link] | --skipped"]
    return "\n".join(lines)


def render_readout(result: dict) -> str:
    lines = ["Engagement readout (" + result["label"] + ")", result["note"], ""]
    for row in result["weeks"]:
        lines.append(f"{row['week_start'][:10]} .. {row['week_end'][:10]}: shown {row['shown']}, replied "
                     f"{row['replied']} ({row['replied_with_link']} with a link), skipped {row['skipped']}")
        for platform, cell in row["platforms"].items():
            extra = ", ".join(f"{k.replace('_', ' ')} {'unknown' if v is None else v}"
                              for k, v in cell.items() if k != "replied")
            lines.append(f"   {platform}: replied {cell['replied']}" + (f"; {extra}" if extra else ""))
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def add_parser(sub) -> None:
    """``engage-scout`` and ``engage-log`` (called from ``cli_parser.make_parser``)."""
    p = sub.add_parser("engage-scout", help="Ranked daily shortlist of conversations to join in your own words, with "
                       "an angle per item; never posts, replies, likes or follows; network only with --online")
    p.add_argument("--config", required=True, type=Path, metavar="FILE",
                   help="Owner scout config JSON (see examples/engage-scout.example.json)")
    p.add_argument("--input", action="append", type=Path, default=[], metavar="FILE",
                   help="Owner candidates file (reachweaver.engage-candidates, e.g. pasted X/LinkedIn/Threads/Reddit/"
                        "YouTube/Stack Overflow links); repeatable")
    p.add_argument("--online", action="store_true",
                   help="Also run the read-only public searches (Bluesky, Mastodon hashtag, GitHub issues): GET only, "
                        "no credential, bounded, stops on a rate limit")
    p.add_argument("--platform", action="append", default=[], metavar="P",
                   help="Only these platforms (repeatable or comma-separated)")
    p.add_argument("--limit", type=int, metavar="N", help=f"Shortlist at most N items (1..{MAX_LIMIT}; also capped by "
                   "the config's daily_cap)")
    p.add_argument("--state", type=Path, metavar="FILE",
                   help="Private state file to read for dedupe (items already shown or marked are dropped)")
    p.add_argument("--record", type=Path, metavar="STATE",
                   help="Read and update this private state file (atomic, mode 0600; ids only) so items are not "
                        "shown again")
    p.add_argument("--optout", type=Path,
                   help="Opt-out list JSON (default $REACHWEAVER_OPTOUT or config/optout.json if present)")
    p.add_argument("--now", help="ISO 8601 time with timezone for ages (default: current UTC time)")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p = sub.add_parser("engage-log", help="Record which engage-scout items you replied to or skipped, or print a "
                       "weekly correlational readout; writes only the private state file")
    p.add_argument("--state", required=True, type=Path, metavar="FILE", help="The engage-scout --record state file")
    p.add_argument("--mark", metavar="ID", help="Shortlist id to mark (from engage-scout)")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--replied", action="store_true", help="With --mark: you replied yourself")
    action.add_argument("--skipped", action="store_true", help="With --mark: you decided not to reply")
    p.add_argument("--with-link", action="store_true", help="With --replied: your reply included your project link")
    p.add_argument("--at", help="ISO 8601 time of the reply (default: now)")
    p.add_argument("--readout", action="store_true", help="Weekly readout of replies next to outcomes (correlational)")
    p.add_argument("--outcomes", type=Path, metavar="FILE",
                   help="With --readout: owner-recorded follower/star/visit snapshots (reachweaver.engage-outcomes)")
    p.add_argument("--weeks", type=int, default=4, metavar="N", help="With --readout: weeks to show (1..26, default 4)")
    p.add_argument("--now", help="ISO 8601 time with timezone (default: current UTC time)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")


def run_scout_cli(args) -> int:
    """Dispatch for ``engage-scout``: never opens the database; only --record writes (the state file)."""
    from .inputs import split_list_args
    if args.state and args.record and args.state.resolve() != args.record.resolve():
        raise ValidationError("--state and --record name different files; use --record STATE alone")
    if args.limit is not None and not 1 <= args.limit <= MAX_LIMIT:
        raise ValidationError(f"--limit must be 1..{MAX_LIMIT}")
    platforms = split_list_args(args.platform)
    bad = [p for p in platforms if p not in PLATFORMS]
    if bad:
        raise ValidationError(f"--platform: unknown {', '.join(bad)} (allowed: {', '.join(PLATFORMS)})")
    if not args.input and not args.online:
        raise ValidationError("give --input FILE (owner candidates) and/or --online (read-only public searches)")
    now = _now(args.now)
    config = load_config(args.config)
    state_path = args.record or args.state
    state = load_state(state_path)
    optout = load_optout(args.optout)
    candidates: list[dict] = []
    for path in args.input:
        candidates += load_candidates(path)
    from . import engage_sources  # the GET-only adapters live there; this module has no network code
    selected = [p for p in ONLINE_PLATFORMS if p in (platforms or config["platforms"]) and p in config["platforms"]]
    planned: list[dict] = []
    if args.online:
        fetched, sources = engage_sources.fetch(config, selected, now)
        malformed = 0
        for index, raw in enumerate(fetched):
            source = raw.pop("_source", "online")
            try:
                candidates.append(validate_candidate(raw, f"online[{index}]", source))
            except ValidationError:
                malformed += 1
        if malformed:
            sources.append({"source": "normalise", "status": f"{malformed} malformed item(s) skipped"})
    else:
        sources, planned = [], engage_sources.plan(config, selected, now)
    report = scout(candidates[:MAX_ITEMS * 2], config, now=now, state=state, optout=optout,
                   platforms=platforms or None, limit=args.limit)
    report["mode"] = "online" if args.online else "offline"
    report["sources"] = sources
    if not args.online:
        report["planned_requests"] = planned
    if args.record:
        report["state_recorded"] = record_shown(state, report, now)
        write_state(args.record, state)
    for item in report["shortlist"] + report["read_it_yourself"]:
        item.pop("author_key", None)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return 0


def run_log_cli(args) -> int:
    """Dispatch for ``engage-log``: --mark writes the state file; --readout only reads."""
    if bool(args.mark) == bool(args.readout):
        raise ValidationError("engage-log needs exactly one of --mark ID or --readout")
    now = _now(args.now)
    if args.readout:
        if args.replied or args.skipped or args.with_link or args.at:
            raise ValidationError("--replied, --skipped, --with-link and --at only apply to --mark")
        if not 1 <= args.weeks <= 26:
            raise ValidationError("--weeks must be 1..26")
        if not args.state.exists():
            raise ValidationError(f"State file {args.state} does not exist")
        result = readout(load_state(args.state), load_outcomes(args.outcomes) if args.outcomes else None,
                         now=now, weeks=args.weeks)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else render_readout(result))
        return 0
    if args.outcomes:
        raise ValidationError("--outcomes only applies to --readout")
    if not (args.replied or args.skipped):
        raise ValidationError("--mark needs --replied or --skipped")
    if not args.state.exists():
        raise ValidationError(f"State file {args.state} does not exist; run engage-scout --record FILE first")
    state = load_state(args.state)
    entry = mark(state, args.mark, "replied" if args.replied else "skipped", link=args.with_link,
                 at=_now(args.at) if args.at else now)
    write_state(args.state, state)
    result = {"marked": {k: v for k, v in entry.items() if k != "author_key"},
              "note": "Recorded in the private state file (ids only). Nothing was posted or sent."}
    print(json.dumps(result, indent=2) if args.json else
          f"Marked {entry['id']} as {entry['action']}" + (" (with a link)" if entry["link"] else "")
          + ". Nothing was posted or sent.")
    return 0
