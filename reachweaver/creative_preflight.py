"""Offline, explainable editorial preflight for social post candidates (``creative-preflight``).

This is NOT a social platform ranking or virality prediction. No network, account, credential,
database, approval, scheduling or publishing operation takes place: the command reads one local
owner-controlled JSON brief and prints a checklist. Platforms the profile-audit registry marks
``human_only`` (Reddit, Hacker News) and the human-authored-only surfaces of ``seo_content`` get
no generated wording, not even brainstorming prompts.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from .policy import ValidationError
from .profile_audit import _reject_sensitive, load_registry
from .seo_content import HUMAN_AUTHORED_ONLY, check_items

SCHEMA = "reachweaver.creative-preflight.v1"
MAX_BYTES = 256_000
MAX_CANDIDATES = 6
GOALS = frozenset({"awareness", "discussion", "qualified_visits"})
WEIGHTS = {"audience_fit": 20, "opening_clarity": 20, "source_linkage": 20,
           "platform_format": 15, "topic_relevance": 10, "accessibility": 10, "call_to_action": 5}
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_URL = re.compile(r"https?://\S+", re.I)
_HYPE = re.compile(r"\b(?:guaranteed viral|guaranteed views|secret algorithm|100% guaranteed)\b", re.I)
# Scripts usually written without spaces between words: a word count says nothing about their length.
_UNSPACED = re.compile(r"[฀-໿က-႟ក-៿぀-ヿ㐀-䶿一-鿿豈-﫿]")
VIDEO = frozenset({"short_video", "shorts", "reel", "video", "live", "spotlight", "story"})
IMAGE = frozenset({"image", "carousel", "pin", "infographic", "document"})
ROOT_REQUIRED = frozenset({"schema_version", "topic", "audience", "goal", "candidates"})
ROOT_FIELDS = ROOT_REQUIRED | {"facts"}
FACT_FIELDS = frozenset({"id", "claim", "source_url"})
CANDIDATE_REQUIRED = frozenset({"id", "platform", "format", "text"})
FIELDS = CANDIDATE_REQUIRED | {"evidence_refs", "alt_text", "captions", "cta", "title"}
NOTE = ("Offline editorial checklist, not a ranking formula, expected impressions or proof of causal lift. "
        "Owner-provided facts, sources and accessibility claims have not been independently verified. "
        "Nothing was posted, scheduled, edited, fetched or measured.")
HUMAN_ONLY_NOTE = ("Human-only destination: write and post this yourself under the community's own rules; "
                   "ReachWeaver generates no wording, prompts or hooks for it.")


def _fail(message: str) -> ValidationError:
    return ValidationError("creative-preflight: " + message)


def _keys(value: object, required: frozenset[str], allowed: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise _fail(f"{label} must be a JSON object")
    missing, unknown = sorted(required - set(value)), sorted(set(value) - allowed)
    if missing:
        raise _fail(f"{label} is missing required field(s): {', '.join(missing)}")
    if unknown:
        raise _fail(f"{label} has unknown field(s): {', '.join(unknown)}")
    return value


def _string(value: object, label: str, length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _fail(f"{label} must be a string")
    text = value.strip()
    if len(text) > length or (not text and not allow_empty):
        raise _fail(f"{label} must be {'' if allow_empty else 'non-empty and '}at most {length} characters")
    return text


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise _fail(f"{label} must be 1-64 letters, digits, '_' or '-'")
    return value


def _ids(value: object, label: str, max_items: int = 10) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise _fail(f"{label} must be a list of at most {max_items} IDs")
    ids = [_id(x, f"{label} entry") for x in value]
    if len(ids) != len(set(ids)):
        raise _fail(f"{label} lists an ID twice")
    return ids


def _source_url(value: object) -> str:
    raw = _string(value, "fact source_url", 500)
    try:
        parsed = urlsplit(raw)
        valid = bool(parsed.scheme == "https" and parsed.hostname and not parsed.username
                     and not parsed.password and parsed.port in (None, 443)
                     and not any(c.isspace() for c in raw))
    except ValueError:
        valid = False
    if not valid:
        raise _fail("fact source_url must be an ordinary https:// URL (no credentials or other port)")
    return raw


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _matches(text: str, terms: list[str]) -> bool:
    """Conservative lexical hint, not semantic audience inference."""
    words = set(_WORD.findall(_fold(_URL.sub(" ", text))))
    for phrase in terms:
        part = _fold(phrase)
        pieces = _WORD.findall(part)
        if pieces and (all(x in words for x in pieces) or (len(part) > 1 and part in _fold(text))):
            return True
    return False


def human_only_platforms() -> frozenset[str]:
    """Registry ``human_only`` platforms plus the human-authored-only surfaces of seo_content."""
    return frozenset(p["id"] for p in load_registry()["platforms"] if p.get("human_only")) | HUMAN_AUTHORED_ONLY


def validate(value: object) -> dict:
    """Return a normalized copy of an owner brief; reject unknown fields and never treat references as facts."""
    try:
        _reject_sensitive(value)
    except ValidationError as exc:
        raise _fail(str(exc)) from None
    obj = _keys(value, ROOT_REQUIRED, ROOT_FIELDS, "brief")
    if obj["schema_version"] != SCHEMA:
        raise _fail(f"schema_version must be {SCHEMA!r}")
    audience = _keys(obj["audience"], frozenset({"description", "keywords"}),
                     frozenset({"description", "keywords"}), "audience")
    terms = audience["keywords"]
    if not isinstance(terms, list) or not 1 <= len(terms) <= 10:
        raise _fail("audience keywords must be a list of 1-10 strings")
    if not isinstance(obj["goal"], str) or obj["goal"] not in GOALS:
        raise _fail("goal must be one of: " + ", ".join(sorted(GOALS)))
    data: dict = {
        "topic": _string(obj["topic"], "topic", 160),
        "audience": {"description": _string(audience["description"], "audience description", 250),
                     "keywords": [_string(t, "audience keyword", 80) for t in terms]},
        "goal": obj["goal"], "facts": [], "candidates": [],
    }
    facts = obj.get("facts", [])
    if not isinstance(facts, list) or len(facts) > 10:
        raise _fail("facts must be a list of at most 10 items")
    for raw in facts:
        fact = _keys(raw, FACT_FIELDS, FACT_FIELDS, "fact")
        fact_id = _id(fact["id"], "fact id")
        if any(f["id"] == fact_id for f in data["facts"]):
            raise _fail(f"fact id {fact_id!r} is used twice")
        data["facts"].append({"id": fact_id, "claim": _string(fact["claim"], "fact claim", 700),
                              "source_url": _source_url(fact["source_url"])})
    fact_ids = {f["id"] for f in data["facts"]}
    candidates = obj["candidates"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise _fail(f"candidates must be a list of 1-{MAX_CANDIDATES} drafts")
    platforms = {entry["id"] for entry in load_registry()["platforms"]}
    for raw in candidates:
        c = _keys(raw, CANDIDATE_REQUIRED, FIELDS, "candidate")
        cid = _id(c["id"], "candidate id")
        if any(x["id"] == cid for x in data["candidates"]):
            raise _fail(f"candidate id {cid!r} is used twice")
        if not isinstance(c["platform"], str) or c["platform"] not in platforms:
            raise _fail(f"candidate {cid}: platform must be one of the profile-audit registry ids: "
                        + ", ".join(sorted(platforms)))
        item: dict = {"id": cid, "platform": c["platform"], "format": _string(c["format"], "format", 40),
                      "text": _string(c["text"], "text", 3900),
                      "evidence_refs": _ids(c.get("evidence_refs", []), "evidence_refs")}
        undefined = [ref for ref in item["evidence_refs"] if ref not in fact_ids]
        if undefined:
            raise _fail(f"candidate {cid}: evidence_refs name undefined fact id(s): {', '.join(undefined)}")
        for key, limit in (("alt_text", 800), ("title", 220), ("cta", 250)):
            if key in c:
                item[key] = _string(c[key], key, limit, allow_empty=True)
        if "captions" in c:
            if type(c["captions"]) is not bool:
                raise _fail("captions must be true or false")
            item["captions"] = c["captions"]
        data["candidates"].append(item)
    return data


def criterion(key: str, status: str, reason: str, action: str, basis: str = "editorial_hypothesis") -> dict:
    return {"code": key, "status": status, "weight": WEIGHTS[key], "reason": reason,
            "basis": basis, "next_action": action if status != "met" else None}


def _opening(first: str) -> tuple[str, str]:
    if _HYPE.search(first):
        return "improve", "Opening makes a risky promise (guaranteed views or a secret algorithm)"
    if _UNSPACED.search(first):
        return "unknown", "Opening length is counted in words; this script is written without spaces, so check it yourself"
    words = len(_WORD.findall(_URL.sub(" ", first)))
    if 5 <= words <= 26:
        return "met", "First line fits a tentative editorial length"
    return "improve", f"First line has {words} words; 5-26 is the tentative editorial range"


def assess(candidate: dict, data: dict, accepted: set[str]) -> dict:
    """Declared editorial checks, independently reviewed by the operator."""
    text = candidate["text"]
    audience_match = _matches(text, data["audience"]["keywords"])
    checks = [criterion("audience_fit", "met" if audience_match else "improve",
                        "Declared audience term appears lexically" if audience_match else
                        "No audience keyword found; semantic relevance was not evaluated",
                        "Check audience relevance and include only naturally fitting terms.")]
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    opening_status, opening_reason = _opening(first)
    checks.append(criterion("opening_clarity", opening_status, opening_reason,
                            "Write a specific, evidence-grounded first sentence."))
    linked = bool(candidate["evidence_refs"])
    checks.append(criterion("source_linkage", "met" if linked else "unknown",
                            "Owner supplied source references (truth still unchecked)" if linked else
                            "No supporting source IDs supplied",
                            "Add reviewed fact IDs and personally verify material claims.",
                            "owner_reported_not_verified"))
    format_ok = candidate["format"] in accepted
    checks.append(criterion("platform_format", "met" if format_ok else "improve",
                            "Format appears in editorial audit registry" if format_ok else
                            "Format is not in this platform's editorial audit list: " + ", ".join(sorted(accepted)),
                            "Confirm current native editor support, or choose a listed format.",
                            "repo_registry_not_publishing_permission"))
    topical = _matches(text + "\n" + candidate.get("title", ""), [data["topic"]])
    checks.append(criterion("topic_relevance", "met" if topical else "improve",
                            "Topic words appear in the candidate" if topical else
                            "No topic phrase found by lexical matching",
                            "Make the actual topic explicit, without keyword stuffing."))
    if candidate["format"] in VIDEO:
        has_access = candidate.get("captions") is True
        access_status = "met" if has_access else "unknown" if "captions" not in candidate else "improve"
        action = "Supply accurate, legible captions or subtitles before publication."
    elif candidate["format"] in IMAGE:
        has_access = bool(candidate.get("alt_text"))
        access_status = "met" if has_access else "improve"
        action = "Add descriptive alt text, where the native editor supports it."
    else:
        has_access, access_status, action = True, "met", "Review accessibility if media is later attached."
    checks.append(criterion("accessibility", access_status,
                            "Accessibility metadata reported" if has_access else
                            "Accessibility metadata needs operator review",
                            action, "owner_reported_not_verified"))
    cta = candidate.get("cta", "")
    if cta and _fold(cta) in _fold(text):
        cta_status, detail = "met", "Declared CTA appears in the draft"
    elif data["goal"] == "awareness" and not cta:
        cta_status, detail = "unknown", "Awareness goal does not require a CTA"
    else:
        cta_status, detail = "improve", "Clear declared and included CTA is missing"
    checks.append(criterion("call_to_action", cta_status, detail,
                            "Add one helpful, non-coercive next step matching your goal."))
    pending = sorted((x for x in checks if x["status"] != "met"), key=lambda x: (-x["weight"], x["code"]))
    return {"id": candidate["id"], "platform": candidate["platform"], "format": candidate["format"],
            "editorial_points": sum(x["weight"] for x in checks if x["status"] == "met"),
            "out_of": 100, "assessed_weight": sum(x["weight"] for x in checks if x["status"] != "unknown"),
            "checks": checks, "next_actions": [x["next_action"] for x in pending[:3]],
            "note": "Editorial checklist only; not comparable across platforms or predictive of views."}


def analyze(value: object) -> dict:
    """Evaluate at most six user-provided alternatives, without platform connections."""
    data = validate(value)
    formats = {p["id"]: set(p["post_strategy"]["formats"]) for p in load_registry()["platforms"]}
    human_only = human_only_platforms()
    results = [assess(c, data, formats[c["platform"]]) for c in data["candidates"]]
    seo_items = [{"id": c["id"], "platform": c["platform"], "text": c["text"],
                  "alt_text": c.get("alt_text"), "link": None, "media_count": int(c["format"] in IMAGE),
                  "keywords": data["audience"]["keywords"][:8]} for c in data["candidates"]]
    findings = {item["id"]: item["findings"] for item in check_items(seo_items)["items"]}
    for row in results:
        row["human_only"] = row["platform"] in human_only
        if row["human_only"]:
            row["human_only_note"] = HUMAN_ONLY_NOTE
        row["additional_findings"] = findings[row["id"]]
        row["next_actions"].extend(item["message"] for item in findings[row["id"]]
                                   if item["severity"] in ("error", "warn"))
    topic = data["topic"]
    any_human_only = any(row["human_only"] for row in results)
    prompts = [] if any_human_only else [
        f"What would you check first when approaching {topic}?",
        f"Which trade-offs matter most for {topic}?",
        f"What is one useful example of {topic}?",
    ]
    return {"schema_version": SCHEMA, "status": "read_only_no_publish", "goal": data["goal"],
            "candidate_count": len(results), "candidates": results,
            "hook_brainstorm_prompts": prompts,
            "hook_note": ("No prompts: a human-only destination is in this brief." if any_human_only else
                          "Prompts require original source-supported human writing, not automatic publication."),
            "limitations": NOTE, "network_access": False, "database_access": False,
            "publishes": False, "schedules": False, "model_calls": False}


def check_file(path: Path) -> dict:
    """Read one regular local JSON file of at most MAX_BYTES (no symlinks) and analyze it."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise _fail(f"--file must be an existing regular file, not a symlink: {path}")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise _fail(f"--file is larger than {MAX_BYTES // 1000} KB")
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise _fail("--file is not UTF-8 text") from None
    except json.JSONDecodeError as exc:
        raise _fail(f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from None
    except RecursionError:
        raise _fail("JSON nesting is too deep") from None
    return analyze(data)


def render_text(report: dict) -> str:
    lines = [f"Creative preflight: {report['candidate_count']} draft(s), offline editorial review"]
    for row in report["candidates"]:
        lines.append(f"  {row['id']} [{row['platform']}/{row['format']}]: "
                     f"{row['editorial_points']}/100 checklist points; assessed {row['assessed_weight']}/100")
        if row.get("human_only"):
            lines.append(f"    {row['human_only_note']}")
        for item in row["checks"]:
            if item["status"] != "met":
                lines.append(f"    {item['status']}: {item['code']}: {item['next_action']}")
        for item in row["additional_findings"]:
            if item["severity"] in ("error", "warn"):
                lines.append(f"    {item['severity']}: {item['code']}: {item['message']}")
    return "\n".join([*lines, "", report["limitations"]])
