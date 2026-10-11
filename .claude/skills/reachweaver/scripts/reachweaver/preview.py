"""Deterministic, offline per-channel previews of campaign variants (issue #10 slice).

For every variant this shows the exact text, a platform-accurate length with the
remaining budget, links/hashtags/mentions (Bluesky facets at UTF-8 byte offsets),
the capability-registry status, the evidence each grounded token or phrase is
anchored on, the lexical grounding findings and a rule-based ``confidence`` with an
explained rationale. ``utm=True`` adds UTM-tagged copies of the variant's links
side by side with the originals.

It never writes the campaign file, never stages, approves, schedules or publishes,
and imports no publisher, connection or network code. No copy is generated: every
string shown comes from the campaign file. Confidence is computed from rules, not a
model, and it inherits the limits of lexical grounding (claims.py).
"""
from __future__ import annotations

from typing import cast

import re

from .attribution import build_utm_link
from .campaigns import TEXT_ONLY_STAGING, validate_campaign
from .capabilities import load_registry
from .claims import LIMITATION, PART_SEPARATOR, check_claims, effective_brand_rules, variant_body
from .private_sources import private_evidence_repos
from .policy import CONSERVATIVE_TEXT_LIMITS, ValidationError, mastodon_text_limit
from .richtext import (
    BLUESKY_MAX_GRAPHEMES, BLUESKY_MAX_UTF8_BYTES, MASTODON_URL_WEIGHT, bluesky_preview,
    grapheme_count, hashtags, link_spans, links, mastodon_length, utf8_len,
)

SEND_PATH = None  # Asserted in tests: previews can never send, stage or schedule.
CONFIDENCE_RULE = ("high = no blocking errors and no human-review items (every figure grounded in cited "
                   "evidence); medium = human-review items present; low = any blocking error")
EDITABLE_NOTE = ("Rationale is editable: write your own reasoning in the variant's optional 'rationale' "
                 "field. It is shown verbatim and never changes the computed level.")
HOOK_MIN_WORDS = 4
CAPABILITY_FORMAT = {"text": "text", "thread": "text", "carousel_outline": "carousel"}
STATUS_LABELS = {"api_direct": "api_direct", "api_gated": "gated", "manual_export": "manual",
                 "unavailable": "unavailable", "unknown": "unknown"}
_VARIANT_MESSAGE = re.compile(r"^variants\[(\d+)\][.:]")
_THREAD_MARKER = re.compile(r"^\s*(?:(?:🧵|\(?\d{1,2}\s*/\s*\d{0,2}\)?|(?:a\s+)?thread\s*[:：👇]+)\s*)+", re.I)
_MENTION = re.compile(r"(?<![\w@/.])@([A-Za-z0-9_][\w.-]*(?:@[\w-]+(?:\.[\w-]+)+)?)")
_WORD = re.compile(r"\w+")


# --- Measurement and features -------------------------------------------------------------

def measure(platform: object, text: str, account: dict | None = None) -> dict:
    """Platform-accurate length and remaining budget for one post body.

    Bluesky: approximate grapheme clusters (never under-counted) and UTF-8 bytes.
    Mastodon: code points with each URL counted as at least 23, against the account's
    ``max_characters`` when it is lower than the conservative cap. Elsewhere: Unicode
    code points against ReachWeaver's conservative cap (not a provider spec).
    """
    if platform == "bluesky":
        count, size = grapheme_count(text), utf8_len(text)
        return {"unit": "graphemes", "count": count, "limit": BLUESKY_MAX_GRAPHEMES,
                "remaining": BLUESKY_MAX_GRAPHEMES - count, "utf8_bytes": size,
                "byte_limit": BLUESKY_MAX_UTF8_BYTES, "remaining_bytes": BLUESKY_MAX_UTF8_BYTES - size,
                "within_limit": count <= BLUESKY_MAX_GRAPHEMES and size <= BLUESKY_MAX_UTF8_BYTES,
                "method": "approximate UAX #29 grapheme clusters (never under-counts) and UTF-8 bytes",
                "limit_source": "app.bsky.feed.post Lexicon (maxGraphemes 300, maxLength 3000 bytes)"}
    if platform == "mastodon":
        default = CONSERVATIVE_TEXT_LIMITS["mastodon"]
        configured = (account or {}).get("max_characters")
        try:
            limit = mastodon_text_limit(account)
        except ValidationError:
            limit, configured = default, None
        source = ("account max_characters" if type(configured) is int and configured < default
                  else "ReachWeaver conservative cap (no lower account max_characters)")
        count = mastodon_length(text)
        return {"unit": "characters", "count": count, "limit": limit, "remaining": limit - count,
                "within_limit": count <= limit,
                "method": f"code points; each URL counted as at least {MASTODON_URL_WEIGHT}",
                "limit_source": source}
    if isinstance(platform, str) and platform in CONSERVATIVE_TEXT_LIMITS:
        limit = CONSERVATIVE_TEXT_LIMITS[platform]
        return {"unit": "code points", "count": len(text), "limit": limit, "remaining": limit - len(text),
                "within_limit": len(text) <= limit, "method": "Unicode code points",
                "limit_source": "ReachWeaver conservative cap (lower than or equal to the provider limit)"}
    return {"unit": "code points", "count": len(text), "limit": None, "remaining": None,
            "within_limit": None, "method": "Unicode code points", "limit_source": "unknown platform"}


def _mentions(text: str) -> list[str]:
    masked = text
    for start, end, _ in reversed(link_spans(text)):
        masked = masked[:start] + " " * (end - start) + masked[end:]
    return ["@" + m.group(1).rstrip(".-") for m in _MENTION.finditer(masked)]


def text_features(platform: object, text: str) -> dict:
    """Links, hashtags and mentions; Bluesky adds link/tag facets at UTF-8 byte offsets."""
    features: dict[str, object] = {"links": links(text), "hashtags": hashtags(text), "mentions": _mentions(text)}
    if platform == "bluesky":
        record = bluesky_preview(text)
        features["facets"] = record["facets"]
        features["mentions"] = record["mentions_as_plain_text"]
        features["mention_note"] = "Mentions stay plain text: ReachWeaver performs no handle-to-DID resolution."
    return features


def capability(platform: object, fmt: object, registry: dict) -> dict:
    """Registry status for the variant's format. Research data, never consent."""
    reg_format = CAPABILITY_FORMAT.get(fmt, fmt) if isinstance(fmt, str) else "text"
    entry = next((e for e in registry["platforms"] if e["id"] == platform), None)
    if entry is None or reg_format not in entry["formats"]:
        return {"platform": platform, "format": reg_format, "status": "unknown", "label": "unknown",
                "platform_status": None, "adapter": None, "live_adapter": False, "verified_date": None,
                "note": "Not in the capability registry; treat as unavailable."}
    status = entry["formats"][reg_format]
    live = reg_format == "text" and status == "api_direct" and entry["reachweaver_adapter"] == "live_text"
    if live:
        note = ("ReachWeaver has a live text adapter; sending still needs human approval, "
                "publish --live, REACHWEAVER_ALLOW_LIVE=1 and REACHWEAVER_ENV=prod.")
    elif status == "api_direct":
        note = "Provider documents a direct API, but ReachWeaver has no adapter for it; publish manually."
    elif status == "api_gated":
        note = "Provider API needs app review or approval the owner has not obtained; manual posting only."
    elif status == "manual_export":
        note = "Manual export only."
    elif status == "unavailable":
        note = f"The registry marks {reg_format} as unavailable on {platform}."
    else:
        note = f"{platform} {reg_format} is not verified in the registry; treat as unavailable."
    if fmt == "thread":
        note += (" Each part is a separate text post; no ReachWeaver adapter chains replies, "
                 "so a thread is posted by hand.")
    return {"platform": platform, "format": reg_format, "status": status, "label": STATUS_LABELS[status],
            "platform_status": entry["status"], "adapter": entry["reachweaver_adapter"], "live_adapter": live,
            "verified_date": entry["verified_date"], "note": note}


def utm_rewrite(text: str, *, platform: object, campaign_id: object, variant_id: object,
                private_repos=()) -> tuple[str, list[dict]]:
    """Return ``(text with UTM-tagged links, [{original, utm, error}])``; the input is not modified.

    A link inside a ``private_repos`` repository is never tagged (issue #9): it is reported as an error.
    """
    rewritten, items = text, []
    for start, end, link in reversed(link_spans(text)):
        try:
            tagged = build_utm_link(link, platform=cast(str, platform), campaign=cast(str, campaign_id),
                                    content=cast("str | None", variant_id), private_repos=private_repos)
        except ValidationError as exc:
            items.append({"original": link, "utm": None, "error": str(exc)})
            continue
        items.append({"original": link, "utm": tagged, "error": None})
        rewritten = rewritten[:start] + tagged + rewritten[end:]
    items.reverse()
    return rewritten, items


# --- Variant structure ------------------------------------------------------------------

def _pieces(variant: dict) -> list[dict]:
    """Labelled pieces in the same order and join as claims.variant_body."""
    fmt = variant.get("format", "text")
    text = variant.get("text") if isinstance(variant.get("text"), str) else ""
    if fmt == "thread" and isinstance(variant.get("parts"), list):
        parts = [p for p in variant["parts"] if isinstance(p, str)]
        return [{"kind": "part", "number": n, "label": f"{n}/{len(parts)}", "text": p}
                for n, p in enumerate(parts, 1)]
    if fmt == "carousel_outline" and isinstance(variant.get("slides"), list):
        slides = [s for s in variant["slides"] if isinstance(s, str)]
        caption = [{"kind": "caption", "number": None, "label": "caption", "text": text}] if text else []
        return caption + [{"kind": "slide", "number": n, "label": f"slide {n}/{len(slides)}", "text": s}
                          for n, s in enumerate(slides, 1)]
    return [{"kind": "text", "number": None, "label": "text", "text": text}]


def first_part_check(part: str, variant: dict, campaign: dict) -> dict:
    """Does a thread's first part stand alone: a hook line plus a link or the declared CTA?"""
    masked = part
    for start, end, _ in reversed(link_spans(part)):
        masked = masked[:start] + " " * (end - start) + masked[end:]
    lines = [line for line in masked.strip().splitlines() if line.strip()]
    first = _THREAD_MARKER.sub("", lines[0]) if lines else ""
    hook_words = len(_WORD.findall(first))
    cta_phrase = variant.get("cta_phrase")
    has_phrase = isinstance(cta_phrase, str) and cta_phrase.strip() and \
        " ".join(cta_phrase.casefold().split()) in " ".join(part.casefold().split())
    has_link = bool(links(part))
    result: dict[str, object] = {"hook_words": hook_words, "has_hook": hook_words >= HOOK_MIN_WORDS,
              "has_link": has_link, "has_cta_phrase": bool(has_phrase),
              "has_link_or_cta": has_link or bool(has_phrase)}
    result["standalone"] = result["has_hook"] and result["has_link_or_cta"]
    declared = variant.get("cta_url") or campaign.get("cta_url")
    result["declared_cta_url"] = declared if isinstance(declared, str) else None
    return result


def _item(code: str, severity: str, message: str, **extra) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


def compute_confidence(errors: list[dict], review: list[dict], warnings: list[dict],
                       grounded: list[dict], editor_rationale: object = None) -> dict:
    """Deterministic level plus the reasons that produced it (see CONFIDENCE_RULE)."""
    level = "low" if errors else "medium" if review else "high"
    ids = sorted({eid for g in grounded for eid in g.get("matched_evidence", [])})
    rationale = [
        f"{len(grounded)} figure(s) or claim phrase(s) lexically matched in cited evidence: {', '.join(ids)}"
        if grounded else "No figures or risky claim phrases needed grounding.",
        f"{len(errors)} blocking error(s): {', '.join(sorted({e['code'] for e in errors}))}"
        if errors else "No blocking errors: figures, release language, length and brand rules passed.",
        f"{len(review)} item(s) need human review: "
        f"{', '.join(sorted({r.get('category') or r['code'] for r in review}))}"
        if review else "No human-review items.",
    ]
    if warnings:
        rationale.append(f"{len(warnings)} warning(s) do not change the level: "
                         f"{', '.join(sorted({w['code'] for w in warnings}))}")
    rationale.append("Lexical grounding only: a human must still open each cited source.")
    return {"level": level, "rule": CONFIDENCE_RULE, "rationale": rationale,
            "editor_rationale": editor_rationale.strip() if isinstance(editor_rationale, str)
            and editor_rationale.strip() else None, "editable": EDITABLE_NOTE}


# --- Campaign preview ---------------------------------------------------------------------

def build_preview(campaign: dict, brand: dict, accounts: dict | None = None, *, utm: bool = False,
                  registry: dict | None = None, optout: list | None = None,
                  history: dict | None = None) -> dict:
    """Build the full offline preview. Reads its inputs only; never mutates or writes them.

    ``optout``: validated opt-out entries; an @-mention of a listed handle is an error.
    ``history``: optional ``history.load_history`` result; adds posting-history warnings,
    which never change the confidence level, and a ``history`` summary.
    """
    campaign = campaign if isinstance(campaign, dict) else {}
    registry = registry if registry is not None else load_registry()
    errors, warnings = validate_campaign(campaign, brand=brand, accounts=accounts, grounding=False)
    by_variant_errors: dict[int, list[str]] = {}
    by_variant_warnings: dict[int, list[str]] = {}
    campaign_errors: list[str] = []
    campaign_warnings: list[str] = []
    for messages, target, rest in ((errors, by_variant_errors, campaign_errors),
                                   (warnings, by_variant_warnings, campaign_warnings)):
        for message in messages:
            match = _VARIANT_MESSAGE.match(message)
            if match:
                target.setdefault(int(match.group(1)), []).append(message)
            else:
                rest.append(message)
    grounding = check_claims(campaign, brand if isinstance(brand, dict) else None, optout=optout,
                             history=history)
    claims_by_index = {item["index"]: item for item in grounding["variants"]}
    evidence = {e["id"]: e for e in campaign.get("evidence", []) if isinstance(e, dict)
                and isinstance(e.get("id"), str)} if isinstance(campaign.get("evidence"), list) else {}
    raw: list = campaign["variants"] if isinstance(campaign.get("variants"), list) else []
    previews = []
    for index, variant in enumerate(raw[:30]):
        if not isinstance(variant, dict):
            campaign_errors.extend(by_variant_errors.pop(index, []))
            continue
        previews.append(_variant_preview(index, variant, campaign, brand, accounts, registry, evidence,
                                         claims_by_index.get(index, {}), by_variant_errors.get(index, []),
                                         by_variant_warnings.get(index, []), campaign_errors, utm))
    summary = {level: sum(1 for p in previews if p["confidence"]["level"] == level)
               for level in ("high", "medium", "low")}
    result = {"report": "campaign_preview", "campaign_id": campaign.get("campaign_id"),
            "brand_id": campaign.get("brand_id"), "utm": bool(utm), "accounts_loaded": accounts is not None,
            "registry_reviewed_on": registry.get("reviewed_on"), "campaign_errors": campaign_errors,
            "campaign_warnings": campaign_warnings, "summary": summary, "variants": previews,
            "limitations": [
                LIMITATION,
                "Preview only: nothing was staged, approved, scheduled or published, and the campaign file "
                "was not modified.",
                "Capability statuses are dated registry research, not consent or account authorization.",
                "Thread/carousel outlines are creative briefs; only text variants can be staged.",
            ]}
    if "history" in grounding:
        result["history"] = grounding["history"]
    return result


def _variant_preview(index, variant, campaign, brand, accounts, registry, evidence, claims, v_errors,
                     v_warnings, campaign_errors, utm) -> dict:
    platform, fmt = variant.get("platform"), variant.get("format", "text")
    account_id = variant.get("account_id")
    account = accounts.get(account_id) if isinstance(accounts, dict) and isinstance(account_id, str) else None
    account = account if isinstance(account, dict) and account.get("platform") == platform else None
    pieces = _pieces(variant)
    starts, offset = [], 0
    for piece in pieces:
        starts.append(offset)
        offset += len(piece["text"]) + len(PART_SEPARATOR)
    for piece in pieces:
        piece["length"] = measure(platform, piece["text"], account) if piece["kind"] != "slide" else \
            {"unit": "code points", "count": len(piece["text"]), "limit": None, "remaining": None,
             "within_limit": None, "method": "Unicode code points",
             "limit_source": "slide text is a design brief, not a post body"}
        piece["features"] = text_features(platform, piece["text"])
        if utm:
            tagged, items = utm_rewrite(piece["text"], platform=platform,
                                        campaign_id=campaign.get("campaign_id"), variant_id=variant.get("id"),
                                        private_repos=private_evidence_repos(campaign))
            piece["utm"] = {"text": tagged, "links": items,
                            "length": measure(platform, tagged, account) if piece["kind"] != "slide" else None}

    def locate(start):
        if start is None or len(pieces) < 2:
            return None
        label = None
        for piece, begin in zip(pieces, starts):
            if start >= begin:
                label = piece["label"]
        return label

    findings = []
    for finding in claims.get("findings", []):
        item = dict(finding)
        if locate(finding.get("start")):
            item["piece"] = locate(finding.get("start"))
        findings.append(item)
    grounded = []
    for item in claims.get("grounded", []):
        entry = dict(item)
        entry["evidence_urls"] = [evidence[eid].get("url") for eid in item["matched_evidence"] if eid in evidence]
        if locate(item.get("start")):
            entry["piece"] = locate(item.get("start"))
        grounded.append(entry)

    extra: list[dict] = []
    thread = carousel = None
    cap = capability(platform, fmt, registry)
    if fmt == "thread" and pieces and pieces[0]["kind"] == "part":
        check = first_part_check(pieces[0]["text"], variant, campaign)
        thread = {"parts": len(pieces), "first_part": check,
                  "numbering_note": ("Labels such as 1/4 are shown for review only; ReachWeaver inserts no "
                                     "numbering, and numbering typed into a part is grounded like any other figure.")}
        if not check["standalone"]:
            missing = [name for name, ok in (("a hook line of at least %d words" % HOOK_MIN_WORDS, check["has_hook"]),
                                             ("a link or the declared cta_phrase", check["has_link_or_cta"])) if not ok]
            extra.append(_item("thread_first_part_not_standalone", "review",
                               "first part does not stand alone; it needs " + " and ".join(missing),
                               category="thread_first_part", piece=pieces[0]["label"]))
    if fmt == "carousel_outline":
        slides = sum(1 for p in pieces if p["kind"] == "slide")
        carousel = {"slides": slides, "capability_status": cap["status"],
                    "note": "Per-platform slide limits are not in the registry; check the platform before designing."}
        if cap["status"] == "unavailable":
            extra.append(_item("carousel_unavailable", "warning",
                               f"{platform} carousel is unavailable in the capability registry; "
                               "publish the outline as a document or image set by hand, or choose another platform"))
        elif cap["status"] == "unknown":
            extra.append(_item("carousel_unverified", "warning",
                               f"{platform} carousel support is not verified in the registry; treat as unavailable"))
    staging_notice = f"variants[{index}]: {TEXT_ONLY_STAGING}"
    validation_errors = [m for m in v_errors if m != staging_notice]
    findings.extend(extra)
    err_items = ([_item("validation", "error", m) for m in validation_errors]
                 + [f for f in findings if f["severity"] == "error"]
                 + [_item("campaign", "error", m) for m in campaign_errors])
    review_items = [f for f in findings if f["severity"] == "review"]
    warn_items = [f for f in findings if f["severity"] == "warning"] + [_item("validation", "warning", m) for m in v_warnings]
    rules = effective_brand_rules(brand, account_id)
    rules["hashtag_count"] = len(hashtags(variant_body(variant)))
    cited = [x for x in variant.get("evidence_ids", []) if isinstance(x, str)] \
        if isinstance(variant.get("evidence_ids"), list) else []
    return {
        "index": index, "variant_id": variant.get("id"), "platform": platform, "account_id": account_id,
        "account_config": "loaded" if account else "not loaded", "format": fmt,
        "angle": variant.get("angle"), "cta": variant.get("cta"),
        "stageable": fmt == "text" and not v_errors and not campaign_errors and not err_items,
        "staging_note": None if fmt == "text" else TEXT_ONLY_STAGING,
        "text": variant_body(variant), "pieces": pieces, "thread": thread, "carousel": carousel,
        "capability": cap, "brand_rules": rules,
        "evidence": [{"id": eid, "url": evidence[eid].get("url"), "kind": evidence[eid].get("kind"),
                      "claim": evidence[eid].get("claim"), "verification": evidence[eid].get("verification"),
                      "checked_at": evidence[eid].get("checked_at")} for eid in cited if eid in evidence],
        "grounded": grounded, "findings": findings,
        "validation": {"errors": validation_errors, "warnings": v_warnings},
        "confidence": compute_confidence(err_items, review_items, warn_items, grounded, variant.get("rationale")),
    }


# --- Rendering ----------------------------------------------------------------------------

def _length_line(length: dict | None) -> str:
    if not length:
        return "n/a"
    if length["limit"] is None:
        return f"{length['count']} {length['unit']} ({length['limit_source']})"
    line = f"{length['count']}/{length['limit']} {length['unit']} ({length['remaining']} left)"
    if "utf8_bytes" in length:
        line += f", {length['utf8_bytes']}/{length['byte_limit']} UTF-8 bytes ({length['remaining_bytes']} left)"
    return line + ("" if length["within_limit"] else " OVER LIMIT")


def _history_line(history: dict) -> str:
    return (f"compared with {history['posts_considered']} local post(s) of the same accounts "
            f"(last {history['days']} days, Jaccard >= {history['duplicate_threshold']:.2f}; frequency heuristic "
            f"{history['max_posts']} per {history['window_days']} days; {history['excluded_same_campaign']} "
            f"post(s) of this campaign skipped{'; TRUNCATED' if history['truncated'] else ''}). "
            "Read-only; warnings only.")


def render_text(preview: dict) -> str:
    out = [f"PREVIEW {preview['campaign_id']} (offline; nothing staged, approved or published) | "
           f"confidence high={preview['summary']['high']} medium={preview['summary']['medium']} "
           f"low={preview['summary']['low']}"]
    for message in preview["campaign_errors"]:
        out.append(f"  CAMPAIGN ERROR: {message}")
    for message in preview["campaign_warnings"]:
        out.append(f"  CAMPAIGN WARNING: {message}")
    if preview.get("history"):
        out.append(f"  history: {_history_line(preview['history'])}")
    for v in preview["variants"]:
        cap = v["capability"]
        out.append(f"== {v['variant_id']} | {v['platform']} | {v['account_id']} | {v['format']} | "
                   f"confidence {v['confidence']['level'].upper()}")
        out.append(f"  capability: {cap['format']} {cap['status']} ({cap['label']}): {cap['note']}")
        if v["staging_note"]:
            out.append(f"  staging: {v['staging_note']}")
        for piece in v["pieces"]:
            f = piece["features"]
            out.append(f"  [{piece['label']}] length {_length_line(piece['length'])}")
            out.append(f"    hashtags {f['hashtags'] or '-'} mentions {f['mentions'] or '-'} links {f['links'] or '-'}")
            for line in piece["text"].splitlines() or [""]:
                out.append(f"    | {line}")
            for link in (piece.get("utm") or {}).get("links", []):
                out.append(f"    utm: {link['original']} -> {link['utm'] or 'NOT TAGGED: ' + str(link['error'])}")
            if piece.get("utm") and piece["utm"]["links"] and piece["utm"]["length"]:
                out.append(f"    length with utm {_length_line(piece['utm']['length'])}")
        if v["thread"]:
            first = v["thread"]["first_part"]
            out.append(f"  thread: {v['thread']['parts']} parts; first part standalone={first['standalone']} "
                       f"(hook words {first['hook_words']}, link or CTA {first['has_link_or_cta']})")
        if v["carousel"]:
            out.append(f"  carousel: {v['carousel']['slides']} slides; registry carousel "
                       f"{v['carousel']['capability_status']}")
        for e in v["evidence"]:
            out.append(f"  evidence {e['id']}: {e.get('url') or '(missing)'}")
        for g in v["grounded"]:
            out.append(f"  grounded {g['type']} {g['span']!r} -> {', '.join(u for u in g['evidence_urls'] if isinstance(u, str)) or '(missing)'}")
        for message in v["validation"]["errors"]:
            out.append(f"  ERROR: {message}")
        for finding in v["findings"]:
            where = f" [{finding['piece']}]" if finding.get("piece") else ""
            out.append(f"  {finding['severity'].upper()} {finding['code']}{where}: {finding['message']}")
        for line in v["confidence"]["rationale"]:
            out.append(f"  why: {line}")
        if v["confidence"]["editor_rationale"]:
            out.append(f"  editor rationale: {v['confidence']['editor_rationale']}")
    out.extend(preview["limitations"])
    return "\n".join(out)


def _fence(text: str) -> str:
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def render_markdown(preview: dict) -> str:
    """Editable Markdown review sheet. Edit the rationale bullets freely; they are not read back."""
    s = preview["summary"]
    out = [f"# Campaign preview: {preview['campaign_id']}", "",
           f"Offline preview. Nothing was staged, approved, scheduled or published. Confidence: "
           f"{s['high']} high, {s['medium']} medium, {s['low']} low.", "",
           f"Confidence rule: {CONFIDENCE_RULE}.", ""]
    if preview.get("history"):
        out += [f"Posting history: {_history_line(preview['history'])}", ""]
    if preview["campaign_errors"] or preview["campaign_warnings"]:
        out.append("## Campaign-level diagnostics")
        out += [f"- ERROR: {m}" for m in preview["campaign_errors"]]
        out += [f"- WARNING: {m}" for m in preview["campaign_warnings"]]
        out.append("")
    for v in preview["variants"]:
        cap = v["capability"]
        out += [f"## {v['variant_id']} ({v['platform']}, {v['format']})", "",
                f"- Account: `{v['account_id']}` (config {v['account_config']})",
                f"- Angle: {v['angle']}", f"- CTA: {v['cta']}",
                f"- Capability: `{cap['format']}` is `{cap['status']}` ({cap['label']}). {cap['note']}",
                f"- Confidence: **{v['confidence']['level']}**"]
        if v["staging_note"]:
            out.append(f"- Staging: {v['staging_note']}")
        if v["thread"]:
            first = v["thread"]["first_part"]
            out.append(f"- Thread: {v['thread']['parts']} parts; first part stands alone: "
                       f"{'yes' if first['standalone'] else 'no'}. {v['thread']['numbering_note']}")
        if v["carousel"]:
            out.append(f"- Carousel: {v['carousel']['slides']} slides; registry carousel status "
                       f"`{v['carousel']['capability_status']}`. {v['carousel']['note']}")
        out.append("")
        for piece in v["pieces"]:
            f = piece["features"]
            out += [f"### {piece['label']}", "", _fence(piece["text"]), "",
                    f"- Length: {_length_line(piece['length'])}",
                    f"- Hashtags: {', '.join(f['hashtags']) or 'none'}; mentions: {', '.join(f['mentions']) or 'none'}; "
                    f"links: {', '.join(f['links']) or 'none'}"]
            if f.get("facets"):
                out.append("- Bluesky facets: " + ", ".join(
                    f"{x['type']} `{x['text']}` bytes {x['byteStart']}-{x['byteEnd']}" for x in f["facets"]))
            if piece.get("utm"):
                if piece["utm"]["links"]:
                    out += ["", "| Original | With UTM |", "|---|---|"]
                    out += [f"| {x['original']} | {x['utm'] or 'not tagged: ' + str(x['error'])} |"
                            for x in piece["utm"]["links"]]
                    if piece["utm"]["length"]:
                        out.append(f"\nLength with UTM links: {_length_line(piece['utm']['length'])}")
            out.append("")
        out.append("### Evidence and grounding")
        out += [f"- `{e['id']}` ({e['verification']}, checked {e['checked_at']}): {e.get('url') or '(missing)'}" for e in v["evidence"]]
        out += [f"- grounded {g['type']} `{g['span']}` in {', '.join(g['matched_evidence'])}" for g in v["grounded"]]
        out += [f"- ERROR: {m}" for m in v["validation"]["errors"]]
        out += [f"- {x['severity'].upper()} `{x['code']}`"
                f"{' [' + x['piece'] + ']' if x.get('piece') else ''}: {x['message']}" for x in v["findings"]]
        out += ["", "### Rationale (editable)"]
        out += [f"- {line}" for line in v["confidence"]["rationale"]]
        out.append(f"- Editor: {v['confidence']['editor_rationale'] or '(add your reasoning here)'}")
        out.append("")
    out.append("## Limitations")
    out += [f"- {line}" for line in preview["limitations"]]
    return "\n".join(out) + "\n"
