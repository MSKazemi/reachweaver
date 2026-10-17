"""Offline, owner-curated creator matching, source kits, and earned-coverage reporting.

No account access, network calls, outbound messages, or publishing. Scores are editorial
heuristics based on operator assertions, NOT creator response probabilities or reach forecasts.
Creator data is only what the owner supplies; nothing is looked up, scraped or enriched. Every
shortlist row carries a consent-based contact rule, and Reddit rows the human-only rule.
"""
from __future__ import annotations

from collections import Counter
from contextlib import suppress
from datetime import date, datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
from typing import NoReturn
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .policy import ValidationError
from .privacy import write_private_file

CANDIDATES_SCHEMA = "reachweaver.creator-candidates.v1"
SOURCE_SCHEMA = "reachweaver.creator-source.v1"
COVERAGE_SCHEMA = "reachweaver.creator-coverage.v1"
WEIGHTS = {"topic": 30, "audience": 20, "remix": 20, "conversation": 15, "relationship": 10, "reach": 5}
KINDS = {"commentary", "tutorial", "critique", "reaction", "review", "duet", "stitch", "remix", "collab", "repost", "citation"}
PLATFORMS = {"youtube", "tiktok", "instagram", "threads", "linkedin", "bluesky", "mastodon", "x", "facebook", "reddit", "pinterest", "devto", "other"}
BLOCKED = {"opted_out", "declined"}
# Controls, zero-width space, direction marks/overrides/isolates and BOM. ZWNJ/ZWJ (U+200C/U+200D) stay
# allowed: Persian, Indic scripts and emoji sequences need them.
INVISIBLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
SENSITIVE = re.compile(r"(?:ghp_[A-Za-z0-9]{8}|github_pat_|sk-[A-Za-z0-9]{12}|bearer\s+\S+|AKIA[0-9A-Z]{16})", re.I)
ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
MAX_BYTES = 512_000
CONTACT_RULE = ("ReachWeaver contacts nobody. If you reach out, do it yourself, one person at a time, only through a "
                "channel this creator publishes for such requests; never make coverage a condition, treat silence as a no "
                "and disclose any payment, gift, free access or other connection.")
BLOCKED_RULE = "Do not contact: this creator opted out or declined."
REDDIT_RULE = ("Reddit is human-only: follow the community's self-promotion rules and its moderators; never send "
               "unsolicited private messages or chat requests.")
DISCLOSURE_REMINDERS = (
    "Coverage is the creator's independent choice: do not ask for a positive review, set wording or a posting time.",
    "If you give or offer anything of value (payment, free access, gifts, affiliate terms) or have a personal or "
    "business relationship with the creator, say so plainly and expect the creator to disclose it, using the "
    "platform's paid-partnership label where one exists; advertising and endorsement-disclosure rules where you "
    "and the audience are may require it.",
    "ReachWeaver does not decide whether a disclosure is legally required; check before anyone publishes.",
)


def _err(message: str) -> NoReturn:
    raise ValidationError("Creator amplification: " + message)


def _object(value, required: set[str], optional: set[str], name: str) -> dict:
    if not isinstance(value, dict):
        _err(name + " must be an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        _err(name + " is missing required field(s): " + ", ".join(sorted(missing)))
    if extra:
        _err(name + " has unrecognized field(s): " + ", ".join(sorted(extra)))
    return value


def _string(value, name: str, minimum: int = 1, maximum: int = 350) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum or value != value.strip():
        _err(name + " must be a nonblank, trimmed string of allowed length")
    if INVISIBLE.search(value) or SENSITIVE.search(value):
        _err(name + " contains forbidden controls or credential-like text")
    return value


def _id(value, name: str) -> str:
    text = _string(value, name, maximum=64)
    if not ID.fullmatch(text):
        _err(name + " must be a lowercase slug")
    return text


def _enum(value, choices: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        _err(name + " must be one of: " + ", ".join(sorted(choices)))
    return value


def _url(value, name: str) -> str:
    raw = _string(value, name, maximum=1000)
    # Only the parsing may raise ValueError: ValidationError is a ValueError too, and the specific
    # messages below must reach the operator instead of a generic one.
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").lower().rstrip(".")
        port = u.port
    except ValueError:
        _err(name + " is not a valid HTTPS URL")
    if u.scheme != "https" or not host or u.username or u.password or port not in (None, 443):
        _err(name + " must be a public HTTPS URL without credentials or alternate ports")
    if not host.isascii() or re.search(r"[\s<>\x60\[\]()]", raw):
        _err(name + " contains whitespace, markup syntax, or a non-ASCII hostname (use the xn-- form)")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")) or "." not in host:
        _err(name + " must use a public host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _err(name + " must not use an IP address")
    if re.search(r"(?:^|[?&])(token|key|api_key|password|secret|session|code|auth)=", u.query, re.I):
        _err(name + " must not contain credential query parameters")
    return raw


def _date(value, name: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        _err(name + " must be a date string YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _err(name + " must be YYYY-MM-DD")
    if parsed.isoformat() != value or parsed > datetime.now(timezone.utc).date():
        _err(name + " must be a valid nonfuture date")
    return parsed


def _datetime(value, name: str) -> datetime:
    example = " (for example 2026-10-14T12:00:00Z)"
    if not isinstance(value, str) or not 16 <= len(value) <= 35:
        _err(name + " must be an ISO 8601 date and time with a timezone" + example)
    raw = _string(value, name, 16, 35)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _err(name + " must be an ISO 8601 date and time with a timezone" + example)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _err(name + " needs a timezone such as Z or +02:00" + example)
    if parsed.astimezone(timezone.utc) > datetime.now(timezone.utc):
        _err(name + " cannot be in the future")
    return parsed.astimezone(timezone.utc)


def _label(text: str) -> str:
    """Comparison form of a topic label: NFKC, case-folded, single spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _strings(value, name: str, *, minimum: int = 1, maximum: int = 12, max_chars: int = 80) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _err(f"{name} must be a list of {minimum}-{maximum} strings")
    texts = [_string(x, name + " item", maximum=max_chars) for x in value]
    if len({_label(x) for x in texts}) != len(texts):
        _err(name + " contains duplicates")
    return texts


def _urls(value, name: str, *, minimum: int = 1, maximum: int = 5) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _err(f"{name} must be a list of {minimum}-{maximum} URLs")
    urls = [_url(x, name + " item") for x in value]
    if len(set(urls)) != len(urls):
        _err(name + " contains duplicate URLs")
    return urls


def _rating(value, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 4:
        _err(name + " must be an operator-entered integer 0..4")
    return value


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    d: dict = {}
    for k, v in pairs:
        if k in d:
            _err("duplicate JSON key")
        d[k] = v
    return d


def load_input(path: str | Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        _err("input must be an existing regular local file (not a symlink)")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        _err(f"input is larger than {MAX_BYTES // 1000} KB")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except UnicodeDecodeError:
        _err("input is not UTF-8 text")
    except json.JSONDecodeError as exc:
        _err(f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}")
    except RecursionError:
        _err("JSON nesting is too deep")
    if not isinstance(result, dict):
        _err("top-level JSON must be an object")
    return result


def creator_rank(data: dict) -> dict:
    _object(data, {"schema_version", "campaign_id", "topic_terms", "creators"}, set(), "candidate input")
    if data["schema_version"] != CANDIDATES_SCHEMA:
        _err("unsupported candidates schema")
    cid = _id(data["campaign_id"], "campaign_id")
    topics = {_label(x) for x in _strings(data["topic_terms"], "topic_terms", maximum=10)}
    creators = data["creators"]
    if not isinstance(creators, list) or not 1 <= len(creators) <= 200:
        _err("creators must contain 1..200 owner-curated entries")
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for row in creators:
        _object(row, {"id", "platform", "handle", "profile_url", "topics", "recent_work_urls", "reviewed_on", "status", "audience_reason", "remix_idea", "editorial_ratings"},
                {"relationship", "reach_tier", "conversation_url"}, "creator entry")
        rid = _id(row["id"], "creator id")
        platform = _enum(row["platform"], PLATFORMS, "platform")
        handle = _string(row["handle"], "public professional handle", maximum=100)
        dedup = (platform, _label(handle))
        if dedup in seen or rid in ids:
            _err("duplicate creator id or platform/handle")
        seen.add(dedup)
        ids.add(rid)
        _url(row["profile_url"], "creator profile")
        recent = _urls(row["recent_work_urls"], "recent_work_urls", minimum=0, maximum=3)
        observed = _date(row["reviewed_on"], "reviewed_on")
        status = _enum(row["status"], {"candidate", "reviewed", "opted_out", "declined"}, "creator status")
        _string(row["audience_reason"], "audience_reason", minimum=15, maximum=300)
        _string(row["remix_idea"], "remix_idea", minimum=15, maximum=300)
        overlap = sorted(topics & {_label(x) for x in _strings(row["topics"], "creator topics", maximum=12)})
        ratings = _object(row["editorial_ratings"], {"audience_value", "remix_potential"}, {"conversation"}, "editorial_ratings")
        values: dict[str, int | None] = {
            "topic": min(4, len(overlap) * 2), "audience": _rating(ratings["audience_value"], "audience_value"),
            "remix": _rating(ratings["remix_potential"], "remix_potential"), "conversation": None,
            "relationship": None, "reach": None}
        conversation = row.get("conversation_url")
        if conversation is not None:
            _url(conversation, "conversation_url")
        if "conversation" in ratings:
            conversation_rating = _rating(ratings["conversation"], "conversation")
            if conversation_rating > 0 and not conversation:
                _err("positive conversation rating requires public conversation_url")
            values["conversation"] = conversation_rating
        rel = _enum(row.get("relationship", "unknown"), {"unknown", "none", "familiar", "collaborated"}, "relationship")
        tier = _enum(row.get("reach_tier", "unknown"), {"unknown", "micro", "niche", "established", "large"}, "reach_tier")
        values["relationship"] = {"unknown": None, "none": 0, "familiar": 2, "collaborated": 4}[rel]
        values["reach"] = {"unknown": None, "micro": 1, "niche": 2, "established": 3, "large": 4}[tier]
        coverage = sum(WEIGHTS[k] for k, v in values.items() if v is not None)
        stale = (datetime.now(timezone.utc).date() - observed).days > 180
        reasons = []
        if status in BLOCKED:
            reasons.append("opt_out_or_declined")
        if not overlap:
            reasons.append("no_topic_overlap")
        if not recent:
            reasons.append("no_recent_public_work")
        if stale:
            reasons.append("stale_evidence_over_180_days")
        if status == "candidate":
            reasons.append("candidate_not_reviewed")
        if coverage < 70:
            reasons.append("insufficient_score_coverage")
        eligible = not reasons
        score = round(sum(WEIGHTS[k] * v / 4 for k, v in values.items() if v is not None) / coverage * 100, 1) if eligible else None
        rules = [BLOCKED_RULE if status in BLOCKED else CONTACT_RULE] + ([REDDIT_RULE] if platform == "reddit" else [])
        result.append({"id": rid, "platform": platform, "handle": handle, "score": score,
                       "decision": "reviewable" if eligible else "hold", "reasons": reasons,
                       "profile_url": row["profile_url"], "recent_work_urls": recent, "conversation_url": conversation,
                       "observed_weight_percent": coverage, "matching_topics": overlap,
                       "weight_points": {k: None if v is None else round(WEIGHTS[k] * v / 4, 2) for k, v in values.items()},
                       "audience_reason": row["audience_reason"], "remix_idea": row["remix_idea"],
                       "contact_rules": rules, "evidence_kind": "owner_supplied_unverified"})
    result.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), x["id"]))
    return {"schema_version": "reachweaver.creator-ranking.v1", "campaign_id": cid,
            "status": "OFFLINE_OWNER_REVIEW_ONLY", "weights": WEIGHTS, "items": result,
            "disclosure_reminders": list(DISCLOSURE_REMINDERS),
            "disclaimer": ("Heuristic editorial fit from owner assertions; no platform reads, no reach or response "
                           "predicted, no contact sent. A score is never permission to contact anyone.")}


def creator_kit(data: dict) -> dict:
    _object(data, {"schema_version", "campaign_id", "title", "original_url", "summary", "claims", "topics", "creator_questions", "reuse_notice"},
            {"media"}, "creator source")
    if data["schema_version"] != SOURCE_SCHEMA:
        _err("unsupported source kit schema")
    cid = _id(data["campaign_id"], "campaign_id")
    title = _string(data["title"], "title", maximum=160)
    original = _url(data["original_url"], "original_url")
    summary = _string(data["summary"], "summary", minimum=30, maximum=600)
    _strings(data["topics"], "topics", maximum=12)
    questions = _strings(data["creator_questions"], "creator_questions", maximum=5, max_chars=220)
    reuse = _enum(data["reuse_notice"], {"text_citation_only", "explicit_owner_permission_for_listed_media"}, "reuse_notice")
    claims = data["claims"]
    if not isinstance(claims, list) or not 1 <= len(claims) <= 6:
        _err("claims must have 1..6 source-backed entries")
    checked = []
    for claim in claims:
        _object(claim, {"text", "evidence_url", "limitation"}, set(), "claim")
        checked.append({"text": _string(claim["text"], "claim", 12, 300),
                        "evidence_url": _url(claim["evidence_url"], "claim evidence URL"),
                        "limitation": _string(claim["limitation"], "claim limitation", 10, 300)})
    media = data.get("media", [])
    if not isinstance(media, list) or len(media) > 8:
        _err("media must be 0..8 reusable links")
    checked_media = []
    for asset in media:
        _object(asset, {"name", "url", "owner_attests_rights", "reuse_terms"}, set(), "media")
        if type(asset["owner_attests_rights"]) is not bool or asset["owner_attests_rights"] is not True:
            _err("every included asset must have owner-attested rights")
        checked_media.append({"name": _string(asset["name"], "media name", maximum=100),
                              "url": _url(asset["url"], "media URL"),
                              "reuse_terms": _string(asset["reuse_terms"], "reuse_terms", minimum=10, maximum=250)})
    if checked_media and reuse != "explicit_owner_permission_for_listed_media":
        _err("listed media require explicit reusable-media permission")
    return {"schema_version": "reachweaver.creator-kit.v1", "campaign_id": cid,
            "status": "DRAFT_REQUIRES_HUMAN_REVIEW_NOT_SENT", "title": title, "original_url": original,
            "summary": summary, "claims": checked, "topics": data["topics"], "creator_questions": questions,
            "media": checked_media, "reuse_notice": reuse,
            "editorial_paths": ["Independent reproduction/tutorial", "Constructive critique/reaction", "Short explainer with original source credit"],
            "contact_rule": CONTACT_RULE, "disclosure_reminders": list(DISCLOSURE_REMINDERS),
            "disclaimer": "Owner-attested input only. Evidence, rights, platform settings, and every message require human verification."}


def _md(text: str) -> str:
    return re.sub(r"([\\\x60*_{}\[\]()#+.!|<>])", r"\\\1", text)


def write_kit(kit: dict, folder: str | Path) -> list[str]:
    """Create a NEW private directory containing a JSON kit and review-only Markdown. No network.

    The directory is created 0700 and each file with ``privacy.write_private_file`` (new 0600 file,
    O_EXCL, no symlink following). An existing path is refused, never overwritten; on any failure the
    partly written directory is removed again.
    """
    directory = Path(folder)
    if directory.exists() or directory.is_symlink():
        _err("output directory already exists; choose a new location")
    if not directory.parent.is_dir() or directory.parent.is_symlink():
        _err("output parent must already exist and not be a symlink")
    claims = "\n".join("- " + _md(x["text"]) + " — [evidence](" + x["evidence_url"] + "); limitation: " + _md(x["limitation"]) for x in kit["claims"])
    questions = "\n".join("- " + _md(x) for x in kit["creator_questions"])
    media = "\n".join("- " + _md(x["name"]) + " — " + x["url"] + " (owner-attested terms: " + _md(x["reuse_terms"]) + ")" for x in kit["media"]) or "- No media rights supplied; do not reproduce clips or images."
    disclosure = "\n".join("- " + _md(x) for x in kit["disclosure_reminders"])
    review = ("# Creator source kit — HUMAN REVIEW, NOT SENT\n\n" +
              "**Owner-attested inputs, not independently verified by ReachWeaver.** No creator has been contacted.\n\n" +
              "## Story\n" + _md(kit["title"]) + "\n\n" + _md(kit["summary"]) + "\n\n" +
              "**Original:** " + kit["original_url"] + "\n\n## Source-checked claims to verify\n" + claims +
              "\n\n## Angles another creator can independently explore\n" + questions +
              "\n\n## Remix/asset rights\n" + media +
              "\n\n## Consent and disclosure\n- " + _md(kit["contact_rule"]) + "\n" + disclosure +
              "\n\n## Human checklist\n- [ ] Open and independently verify every evidence link and limitation.\n" +
              "- [ ] Confirm rights, attribution, and the original platform's remix settings.\n" +
              "- [ ] Choose one creator whose recent work genuinely fits; respect opt-outs.\n" +
              "- [ ] Send any individual invitation yourself only where requested/permitted; never bulk DM, auto-follow or mass-tag.\n" +
              "- [ ] Disclose any material connection or sponsorship where required.\n" +
              "- [ ] Measure confirmed independent coverage, not invented impressions.\n")
    paths = [directory / "source-kit.json", directory / "CREATOR-REVIEW.md"]
    directory.mkdir(mode=0o700)
    try:
        if os.name == "posix":
            directory.chmod(0o700)  # mkdir's mode is reduced by the umask
        for path, text in zip(paths, (json.dumps(kit, indent=2, ensure_ascii=False) + "\n", review)):
            write_private_file(path, text)
    except BaseException:
        for path in paths:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        with suppress(OSError):
            directory.rmdir()
        raise
    return [str(p) for p in paths]


def _url_key(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), (p.hostname or "").lower(), p.path.rstrip("/") or "/", p.query, ""))


def coverage_report(data: dict) -> dict:
    _object(data, {"schema_version", "campaign_id", "original_url", "published_at", "items"}, set(), "coverage input")
    if data["schema_version"] != COVERAGE_SCHEMA:
        _err("unsupported coverage schema")
    cid = _id(data["campaign_id"], "campaign_id")
    original = _url(data["original_url"], "original_url")
    published = _datetime(data["published_at"], "published_at")
    items = data["items"]
    if not isinstance(items, list) or len(items) > 500:
        _err("items must be a list up to 500")
    seen, confirmed, pending = set(), [], 0
    for item in items:
        _object(item, {"creator_id", "url", "platform", "kind", "observed_at", "status", "independent"}, set(), "coverage item")
        creator = _id(item["creator_id"], "creator_id")
        url = _url(item["url"], "coverage URL")
        if _url_key(url) == _url_key(original):
            _err("derivative URL must be distinct from original URL")
        key = _url_key(url)
        if key in seen:
            _err("duplicate derivative URL")
        seen.add(key)
        platform = _enum(item["platform"], PLATFORMS, "coverage platform")
        kind = _enum(item["kind"], KINDS, "coverage kind")
        observed = _datetime(item["observed_at"], "observed_at")
        if observed < published:
            _err("derivative observed before original publication")
        status = _enum(item["status"], {"owner_confirmed", "unverified"}, "coverage status")
        if type(item["independent"]) is not bool:
            _err("independent must be boolean")
        if status != "owner_confirmed" or not item["independent"]:
            pending += 1
            continue
        elapsed = (observed - published).total_seconds()
        confirmed.append({"creator_id": creator, "platform": platform, "kind": kind, "at_48h": elapsed <= 48 * 3600,
                          "at_7d": elapsed <= 7 * 24 * 3600})
    return {"schema_version": "reachweaver.creator-coverage-report.v1", "campaign_id": cid,
            "status": "OFFLINE_OWNER_ATTESTED_NOT_EXTERNALLY_VERIFIED", "original_url": original,
            "total_observations": len(items), "unverified_or_non_independent": pending,
            "confirmed_independent_items": len(confirmed), "distinct_confirmed_creators": len({x["creator_id"] for x in confirmed}),
            "confirmed_items_48h": sum(x["at_48h"] for x in confirmed),
            "confirmed_items_7d": sum(x["at_7d"] for x in confirmed),
            "distinct_creators_48h": len({x["creator_id"] for x in confirmed if x["at_48h"]}),
            "distinct_creators_7d": len({x["creator_id"] for x in confirmed if x["at_7d"]}),
            "by_platform": dict(sorted(Counter(x["platform"] for x in confirmed).items())),
            "by_kind": dict(sorted(Counter(x["kind"] for x in confirmed).items())),
            "disclaimer": "Owner-confirmed URL observations only; no network checks, combined views, downstream causality or earned-viral guarantee."}


def render_rank(report: dict) -> str:
    lines = ["Creator opportunities — OFFLINE, HUMAN REVIEW ONLY", "Weighted editorial heuristic; no platform data fetched or messages sent."]
    for item in report["items"]:
        score = str(item["score"]) if item["score"] is not None else "HOLD"
        why = ", ".join(item["reasons"] or item["matching_topics"])
        lines.append(f"  {item['id']} ({item['platform']}): {score} | evidence coverage {item['observed_weight_percent']}% | {why}")
        if item["decision"] == "reviewable":
            lines.append(f"    Profile: {item['profile_url']}")
            lines.append(f"    Why: {item['audience_reason']}")
            lines.append(f"    Independent angle: {item['remix_idea']}")
        if item["decision"] == "reviewable" or "opt_out_or_declined" in item["reasons"]:
            lines.extend(f"    Rule: {rule}" for rule in item["contact_rules"])
    lines.extend(["Disclosure:"] + [f"  - {x}" for x in report["disclosure_reminders"]])
    return "\n".join(lines)


def render_coverage(report: dict) -> str:
    return ("Creator coverage — OFFLINE OWNER ATTESTATIONS ONLY\n" +
            f"  Verified by owner, independent items: {report['confirmed_independent_items']}\n" +
            f"  Distinct creators: {report['distinct_confirmed_creators']}\n" +
            f"  Within 48h / 7d: {report['confirmed_items_48h']} / {report['confirmed_items_7d']}\n" +
            f"  Pending/unverified/non-independent: {report['unverified_or_non_independent']}\n" +
            "No API verification, unsolicited outreach, or causal reach claim.")
