"""Signal -> reviewable campaign brief scaffold (issue #9 slice).

``build_brief`` turns ONE ``SourceSignal`` from a saved ``sources-scan --json`` file into
a NEW campaign file that follows the campaign contract, plus a Markdown sidecar for the
human writer. The output is a deterministic scaffold:

- No copy is generated. No language model, paid API or network is used. Every
  creative field (audience, claim, angle, CTA, post text) is an unresolved
  ``{{write: ...}}`` placeholder, so ``validate_post``'s placeholder lint and
  ``validate_campaign`` block staging until a person writes real text.
- Evidence records carry the signal's evidence URLs, its cleaned summary as an
  ``excerpt`` marked untrusted, and ``checked_by: "unverified"``. ``verification`` and
  ``checked_at`` are deliberately left out: only a person who opened the source can
  declare them, so validation reports them until that happens.
- The scan file is untrusted input too. Every field is re-validated and re-cleaned
  here, instruction-like text is re-detected, and source text only ever appears in the
  sidecar inside a fenced block labelled as data. It is never copied into an
  instruction, and it never changes what this module does.
- Writes are two NEW owner-only (0600) files; an existing file is never overwritten.
  Nothing is staged, approved, scheduled or published, and no database is opened.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .campaigns import OBJECTIVES, SLUG, public_url, validate_brand, validate_campaign
from .capabilities import load_registry
from .manual_export import MANUAL_PLATFORMS
from .policy import ValidationError
from .preview import capability, measure
from .private_sources import is_private_signal
from .privacy import write_private_file
from .sources import INSTRUCTION_LIKE, SUMMARY_LIMIT, TITLE_LIMIT, clean_text

SEND_PATH = None  # Asserted in tests: a brief can never stage, approve, schedule or send.
MAX_SCAN_BYTES = 5_000_000
MAX_EVIDENCE_PER_SIGNAL = 10
SIGNAL_ID = re.compile(r"^[0-9a-f]{64}$")
SIGNAL_ID_PREFIX = re.compile(r"^[0-9a-f]{8,64}$")
UNCERTAINTY = re.compile(r"^[a-z0-9_]{1,64}$")
# Characters allowed in a URL that is rendered into the Markdown sidecar (no quotes,
# backticks, angle brackets, braces or whitespace that could break out of the markup).
MARKDOWN_SAFE_URL = re.compile(r"^https://[A-Za-z0-9\-._~:/?\[\]@!$&()*+,;=%]{1,2000}$")
INJECTION_FLAG = "instruction_like_text_treated_as_data"
SIGNAL_STATUSES = frozenset({"candidate", "near_duplicate", "previously_seen"})
SCORES = ("freshness", "novelty", "audience_relevance", "confidence", "rank_score")
# SourceSignal kind -> campaign evidence kind. Only a GitHub release maps to "release",
# so release language for any other signal stays lexically blocked (claims.py) until a
# person checks the source and changes the kind.
EVIDENCE_KIND = {
    "github_release": "release",
    "paper": "paper",
    "changelog_entry": "documentation",
    "github_pull": "repository",
    "github_issue": "repository",
    "github_tag": "repository",
    "feed_item": "announcement",
}
EXCERPT_STATUS = "untrusted_scan_summary_not_verbatim"
STATUS = "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT"
NOTE = ("SCAFFOLD ONLY. No copy was generated and nothing was staged, approved, scheduled or "
        "published. Every {{write: ...}} placeholder blocks staging until a person replaces it.")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------- inputs

def load_scan(path: str | Path) -> dict:
    """Read a saved ``sources-scan --json`` result (untrusted, size-capped)."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SCAN_BYTES + 1)
    except OSError as exc:
        raise ValidationError(f"Cannot read scan file {path}") from exc
    if len(raw) > MAX_SCAN_BYTES:
        raise ValidationError(f"Scan file is larger than {MAX_SCAN_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Scan file is not UTF-8 JSON from 'sources-scan --json'") from exc
    if not isinstance(data, dict) or data.get("status") != "RESEARCH_ONLY" or not isinstance(data.get("signals"), list):
        raise ValidationError("Scan file must be the JSON output of 'sources-scan --json' (status RESEARCH_ONLY, signals[])")
    return data


def find_signal(scan: dict, signal_id: str) -> dict:
    """Exact 64-hex ID, or a unique prefix of at least 8 hex characters."""
    wanted = signal_id.strip().casefold() if isinstance(signal_id, str) else ""
    if not SIGNAL_ID_PREFIX.fullmatch(wanted):
        raise ValidationError("--signal-id must be a signal id (8..64 lowercase hex characters)")
    signals = [s for s in scan["signals"] if isinstance(s, dict) and isinstance(s.get("id"), str)]
    exact = [s for s in signals if s["id"] == wanted]
    matches = exact or [s for s in signals if s["id"].startswith(wanted)]
    if not matches:
        raise ValidationError(f"Unknown signal id {wanted[:16]}: not in the scan file's signals")
    if len(matches) > 1:
        raise ValidationError(f"Signal id prefix {wanted[:16]} is ambiguous ({len(matches)} matches); give more characters")
    return matches[0]


def _score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 4) if 0.0 <= float(value) <= 1.0 else None


def normalize_signal(raw: dict) -> dict:
    """Re-validate and re-clean one signal; the scan file may have been edited by anyone."""
    sid = raw.get("id")
    if not isinstance(sid, str) or not SIGNAL_ID.fullmatch(sid):
        raise ValidationError("Signal id must be 64 lowercase hex characters")
    kind = raw.get("kind")
    if kind not in EVIDENCE_KIND:
        raise ValidationError(f"Signal kind must be one of {', '.join(sorted(EVIDENCE_KIND))}")
    title_value, summary_value = raw.get("title"), raw.get("summary")
    raw_title = title_value if isinstance(title_value, str) else ""
    raw_summary = summary_value if isinstance(summary_value, str) else ""
    title, _ = clean_text(raw_title, TITLE_LIMIT)
    summary, _ = clean_text(raw_summary, SUMMARY_LIMIT)
    uncertainties: list[str] = []
    dropped = False
    for item in raw.get("uncertainties", []) if isinstance(raw.get("uncertainties"), list) else []:
        if isinstance(item, str) and UNCERTAINTY.fullmatch(item):
            if item not in uncertainties:
                uncertainties.append(item)
        else:
            dropped = True
    if dropped:
        uncertainties.append("scan_uncertainty_values_dropped")
    # Re-detect instead of trusting the flag: a tampered scan file could remove it.
    injected = INJECTION_FLAG in uncertainties or bool(
        INSTRUCTION_LIKE.search(f"{raw_title[:200_000]} {raw_summary[:200_000]}")
        or INSTRUCTION_LIKE.search(f"{title} {summary}"))
    if injected and INJECTION_FLAG not in uncertainties:
        uncertainties.append(INJECTION_FLAG)
    urls: list[str] = []
    listed = raw.get("evidence_urls")
    candidates: list = listed if isinstance(listed, list) else []
    for url in [raw.get("url"), *candidates]:  # the signal's own URL is primary when it is usable
        if isinstance(url, str) and public_url(url) and MARKDOWN_SAFE_URL.fullmatch(url) and url not in urls:
            urls.append(url)
    if not urls:
        raise ValidationError("Signal has no public https evidence URL without a fragment; open the source and write the campaign by hand")
    if len(urls) > MAX_EVIDENCE_PER_SIGNAL:
        urls = urls[:MAX_EVIDENCE_PER_SIGNAL]
        uncertainties.append("evidence_urls_truncated")
    published = raw.get("published_at")
    if published is not None:
        try:
            datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except ValueError:
            published = None
            uncertainties.append("scan_published_at_unparseable")
    status = raw.get("status") if raw.get("status") in SIGNAL_STATUSES else "unknown"
    source_id = raw.get("source_id") if isinstance(raw.get("source_id"), str) and SLUG.fullmatch(raw["source_id"]) else None
    return {"id": sid, "kind": kind, "source_id": source_id, "title": title or "(no title)",
            "summary": summary, "evidence_urls": urls, "primary_url": urls[0],
            "published_at": published if isinstance(published, str) else None, "status": status,
            "scores": {name: _score(raw.get(name)) for name in SCORES},
            "uncertainties": uncertainties, "injection_flagged": injected,
            # Fails closed: any visibility other than an explicit "public" is private (issue #9).
            "private": is_private_signal(raw)}


# --------------------------------------------------------------------- scaffold

def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return re.sub(r"-{2,}", "-", words)


def campaign_id_for(signal: dict, now: datetime) -> str:
    """Slug of the title plus the brief date. Flagged text never names the campaign."""
    base = "" if signal["injection_flagged"] else _slug(signal["title"])
    if not base or not base[0].isalpha():
        base = f"signal-{signal['id'][:8]}" if not base else f"signal-{base}"
    stamp = now.strftime("%Y%m%d")
    base = base[:64 - len(stamp) - 1].rstrip("-")
    campaign_id = f"{base}-{stamp}"
    if not SLUG.fullmatch(campaign_id):  # defensive: fall back to the id-derived form
        campaign_id = f"signal-{signal['id'][:8]}-{stamp}"
    return campaign_id


def evidence_kind_for(signal_kind: str, url: str, *, primary: bool) -> str:
    """The mapped kind for the signal's primary URL; a supporting URL is never a release."""
    if primary:
        return EVIDENCE_KIND[signal_kind]
    return "repository" if url.startswith("https://github.com/") else "other"


def account_capability(platform: str, registry: dict) -> tuple[str | None, dict]:
    """``("live"|"manual"|None, registry capability)`` for the platform's text format."""
    cap = capability(platform, "text", registry)
    if cap["live_adapter"]:
        return "live", cap
    if cap["status"] == "manual_export":
        return "manual", cap
    if platform in MANUAL_PLATFORMS and cap["status"] in ("api_direct", "api_gated"):
        return "manual", cap  # a creator-ready manual-export package exists (docs/MANUAL-EXPORT.md)
    return None, cap


def _placeholder(text: str) -> str:
    body = text.replace("{", "(").replace("}", ")")
    return "{{write: " + body + "}}"


def _budget(platform: str, link: str, account: dict | None) -> dict:
    empty = measure(platform, "", account)
    with_link = measure(platform, "\n\n" + link, account)
    budget = {"unit": empty["unit"], "limit": empty["limit"], "limit_source": empty["limit_source"],
              "link_uses": with_link["count"],
              "left_for_copy": None if empty["limit"] is None else empty["limit"] - with_link["count"]}
    if "byte_limit" in empty:
        budget["byte_limit"] = empty["byte_limit"]
        budget["link_uses_bytes"] = with_link["utf8_bytes"]
    return budget


def build_brief(scan: dict, signal_id: str, brand: dict, accounts: dict | None, *,
                objective: str = "awareness", now: datetime | None = None,
                allow_duplicate: bool = False, registry: dict | None = None,
                campaign_file: str = "CAMPAIGN.json", brand_file: str = "BRAND.json") -> dict:
    """Return ``{campaign, markdown, validation, variants_skipped, signal}``; writes nothing."""
    if objective not in OBJECTIVES:
        raise ValidationError(f"--objective must be one of {', '.join(sorted(OBJECTIVES))}")
    brand_issues = validate_brand(brand)
    if brand_issues:
        raise ValidationError("Brand file is invalid: " + "; ".join(brand_issues))
    signal = normalize_signal(find_signal(scan, signal_id))
    if signal["status"] != "candidate" and not allow_duplicate:
        raise ValidationError(f"Signal status is {signal['status']}, not candidate; a duplicate or previously "
                              "seen signal needs explicit human action (--allow-duplicate)")
    moment = _now(now)
    registry = registry if registry is not None else load_registry()
    campaign_id = campaign_id_for(signal, moment)
    private = signal["private"]
    evidence = []
    for n, url in enumerate(signal["evidence_urls"], 1):
        kind = evidence_kind_for(signal["kind"], url, primary=n == 1)
        record = {"id": f"{kind}-{n}", "kind": kind, "url": url,
                  "claim": _placeholder("the specific claim this source supports, after you open it"),
                  "checked_by": "unverified", "source_signal_id": signal["id"]}
        if private:
            # Blocked by validate_campaign until a human sets publishable: true at review time.
            record["source_visibility"], record["publishable"] = "private", False
        if n == 1 and signal["summary"]:
            record["excerpt"] = signal["summary"]
            record["excerpt_status"] = EXCERPT_STATUS
        evidence.append(record)
    evidence_ids = [e["id"] for e in evidence]
    # A private repository URL is never a public link: no CTA URL is scaffolded for it.
    cta_url = None if private else signal["primary_url"]
    approved = brand.get("approved_accounts", [])
    variants, skipped, budgets = [], [], []
    for account_id, account in (accounts or {}).items():
        platform = account.get("platform")
        if account_id not in approved:
            skipped.append({"account_id": account_id, "platform": platform,
                            "reason": f"not in brand {brand['brand_id']} approved_accounts"})
            continue
        mode, cap = account_capability(platform, registry)
        if mode is None:
            skipped.append({"account_id": account_id, "platform": platform,
                            "reason": f"registry text capability is {cap['label']} with no ReachWeaver live or manual path"})
            continue
        budget = _budget(platform, cta_url or "", account)
        text = _placeholder(f"{platform} post for {account_id}; stay within the budget campaign-preview "
                            "shows; ground every claim in the cited excerpt") + (f"\n\n{cta_url}" if cta_url else "")
        variant = {
            "id": account_id, "account_id": account_id, "platform": platform, "format": "text",
            "angle": _placeholder(f"one angle native to {platform} readers"),
            "cta": _placeholder("the one action a reader should take"),
            "evidence_ids": list(evidence_ids),
        }
        if cta_url:
            variant["cta_url"] = cta_url
            variant["cta_note"] = (f"Run 'reachweaver utm-link {cta_url} --platform {platform} --campaign "
                                   f"{campaign_id} --content {account_id}' and put the tagged link in text before staging.")
        else:
            variant["cta_note"] = ("PRIVATE SOURCE: its repository URL is never a public link. Choose a public "
                                   "destination, tag it with 'reachweaver utm-link URL --sources CONFIG', and "
                                   "put it in text before staging.")
        variant["text"] = text
        variants.append(variant)
        budgets.append({"account_id": account_id, "platform": platform, "capability": mode,
                        "registry_status": cap["label"], "verified_date": cap["verified_date"], **budget})
    campaign = {
        "schema_version": 1, "campaign_id": campaign_id, "brand_id": brand["brand_id"],
        "audience": _placeholder("the exact reader group this campaign serves"),
        "objective": objective, **({"cta_url": cta_url} if cta_url else {}),
        "brief": {"generated_by": "reachweaver signal-brief", "generated_at": _rfc3339(moment),
                  "source_visibility": "private" if private else "public",
                  "status": STATUS, "note": NOTE,
                  "scan_generated_at": scan.get("generated_at") if isinstance(scan.get("generated_at"), str) else None,
                  "source_signal": {"id": signal["id"], "kind": signal["kind"], "source_id": signal["source_id"],
                                    "status": signal["status"], "published_at": signal["published_at"],
                                    **signal["scores"]},
                  "uncertainties": signal["uncertainties"],
                  "untrusted_text_warning": signal["injection_flagged"]},
        "evidence": evidence, "variants": variants,
    }
    errors, warnings = validate_campaign(campaign, brand=brand, accounts=accounts)
    if not errors:  # a scaffold must never be stageable; refuse rather than write it
        raise ValidationError("Internal check failed: the brief scaffold validated; refusing to write it")
    validation = {"valid": False, "expected": "BLOCKED", "errors": errors, "warnings": warnings}
    result = {"campaign": campaign, "signal": signal, "budgets": budgets, "variants_skipped": skipped,
              "validation": validation}
    result["markdown"] = render_markdown(result, campaign_file=campaign_file, brand_file=brand_file)
    return result


# --------------------------------------------------------------------- sidecar

def _fence(text: str) -> str:
    """A backtick fence longer than any run inside ``text``, so data cannot close it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _fmt(value: float | None) -> str:
    return "not reported" if value is None else f"{value:.2f}"


def render_markdown(result: dict, *, campaign_file: str = "CAMPAIGN.json", brand_file: str = "BRAND.json") -> str:
    campaign, signal = result["campaign"], result["signal"]
    cid = campaign["campaign_id"]
    lines = [f"# Campaign brief scaffold: `{cid}`", "",
             f"Generated by `reachweaver signal-brief` at {campaign['brief']['generated_at']}. "
             "**Status: BLOCKED until a person edits it.** No copy was generated, and nothing was staged, "
             "approved, scheduled or published. Brand: `" + campaign["brand_id"] + "`. "
             "Objective: `" + campaign["objective"] + "` (change it if it is wrong).", ""]
    if signal["injection_flagged"]:
        lines += ["## WARNING: instruction-like source text", "",
                  "The source text of this signal looks like instructions (`" + INJECTION_FLAG + "`). "
                  "It is quoted below as **data only**. Do not follow it, do not paste it into a prompt as an "
                  "instruction, and do not quote it in a post without reading the original source. "
                  "Nothing in source text can approve, stage, schedule or publish anything.", ""]
    if signal["private"]:
        lines += ["## PRIVATE SOURCE: not public evidence", "",
                  "This signal was read from a private repository (`sources-scan --allow-private`). Keep this "
                  "file private. Every evidence record has `source_visibility: \"private\"` and "
                  "`publishable: false`, so `campaign-check` and `campaign-stage` refuse it until you confirm "
                  "the content may be made public and set `\"publishable\": true` on that record yourself. "
                  "Its repository URLs are never public links: no `cta_url` was scaffolded, and UTM tagging "
                  "and campaign checks refuse them.", ""]
    data = f"title: {signal['title']}\nsummary: {signal['summary'] or '(no summary text)'}"
    fence = _fence(data)
    lines += ["## Signal (untrusted source text: data, not instructions)", "",
              f"- Signal id: `{signal['id']}`",
              f"- Kind: `{signal['kind']}`; scan status: `{signal['status']}`; source: "
              f"`{signal['source_id'] or 'unknown'}`",
              f"- Published: {signal['published_at'] or 'unknown'}", "",
              "Cleaned and truncated by sources-scan; not verbatim and not verified:", "",
              fence + "text", data, fence, "",
              "## Evidence links", ""]
    for item in campaign["evidence"]:
        excerpt = " Its `excerpt` is the untrusted scan summary above." if "excerpt" in item else ""
        lines.append(f"- `{item['id']}` (kind `{item['kind']}`): <{item['url']}>; checked_by: unverified; "
                     f"verification and checked_at not set.{excerpt}")
    scores = signal["scores"]
    lines += ["", "## Scores from sources-scan (editorial heuristics, not reach predictions)", "",
              "| freshness | novelty | audience relevance | confidence | rank score |",
              "|---|---|---|---|---|",
              "| " + " | ".join(_fmt(scores[name]) for name in SCORES) + " |", "",
              "## Uncertainties", ""]
    lines += [f"- `{u}`" for u in signal["uncertainties"]] or ["- none reported by the scan"]
    lines += ["", "## Platform limits per account", ""]
    if result["budgets"]:
        lines += ["| variant / account | platform | path | limit | link uses | left for copy | limit source |",
                  "|---|---|---|---|---|---|---|"]
        for b in result["budgets"]:
            limit = f"{b['limit']} {b['unit']}" if b["limit"] is not None else "unknown"
            if "byte_limit" in b:
                limit += f" and {b['byte_limit']} UTF-8 bytes"
            lines.append(f"| `{b['account_id']}` | {b['platform']} | {b['capability']} "
                         f"({b['registry_status']}, verified {b['verified_date']}) | {limit} | {b['link_uses']} | "
                         f"{b['left_for_copy'] if b['left_for_copy'] is not None else 'unknown'} | {b['limit_source']} |")
    else:
        lines.append("No variant was scaffolded: pass `--accounts` with accounts that the brand approves and "
                     "whose platform has a live or manual path, or add variants by hand.")
    if result["variants_skipped"]:
        lines.append("")
    for s in result["variants_skipped"]:
        lines.append(f"- Skipped `{s['account_id']}` ({s['platform']}): {s['reason']}.")
    lines += ["", "## Before this campaign can be staged", "",
              "1. Open every evidence URL yourself. Rewrite each `claim` as the specific claim the source supports, "
              "then set `verification` (`primary_source_checked` or `owner_attested`) and `checked_at` with a timezone. "
              "The tool never sets them.",
              "2. Replace each `excerpt` with verbatim text you read in the source, or delete it. Every claim, "
              "number, version, date and release word in a post must be grounded in the cited `claim`/`excerpt`.",
              "3. Replace every `{{write: ...}}` placeholder: `audience`, each `angle`, `cta` and `text`. "
              "Write a genuinely different post per platform.",
              "4. Tag each link: run the `utm-link` command in the variant's `cta_note`.",
              "5. These must pass before staging:", "",
              "```bash",
              f"reachweaver claims-check {campaign_file} --brand {brand_file}",
              f"reachweaver campaign-preview --file {campaign_file} --brand {brand_file} --accounts ACCOUNTS.json --utm",
              f"reachweaver campaign-check --file {campaign_file} --brand {brand_file}",
              "```", "",
              "6. Approval is a separate human step after `campaign-stage`; a passing check is not consent to publish.",
              "", "## Validation at creation (expected: BLOCKED)", ""]
    lines += [f"- ERROR: {e}" for e in result["validation"]["errors"]]
    lines += [f"- WARNING: {w}" for w in result["validation"]["warnings"]]
    return "\n".join(lines) + "\n"


def sidecar_path(out: Path) -> Path:
    if out.suffix != ".json":
        raise ValidationError("--out must name a .json campaign file")
    return out.with_suffix(".brief.md")


def write_brief(result: dict, out: str | Path) -> list[str]:
    """Write the campaign JSON and its ``.brief.md`` sidecar as NEW 0600 files."""
    out = Path(out)
    side = sidecar_path(out)
    for path in (out, side):
        if path.exists() or path.is_symlink():
            raise ValidationError(f"Refusing to overwrite existing {path}")
    write_private_file(out, json.dumps(result["campaign"], indent=2, ensure_ascii=False) + "\n")
    try:
        write_private_file(side, result["markdown"])
    except OSError:
        os.unlink(out)
        raise
    return [str(out), str(side)]
