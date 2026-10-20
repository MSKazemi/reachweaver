"""Evidence-bound, brand-isolated campaign bundles; offline validation and staging.

These checks validate declared structure and provenance, NOT the truth of a webpage.
A human/agent must verify that each primary source actually supports each claim.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import cast
from urllib.parse import parse_qsl, urlparse

from .claims import BRAND_RULE_FIELDS, DEFAULT_DUPLICATE_THRESHOLD, EVIDENCE_KINDS, check_claims, variant_body
from .policy import PLACEHOLDER, ValidationError, validate_post
from .private_sources import (VISIBILITIES, is_private_evidence, links_in, private_evidence_repos,
                              redact_private_evidence, url_in_private_repo)
from .storage import add_post, event, parse_utc, utc_now
from .inputs import read_json_file

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
OBJECTIVES = frozenset({"qualified_visits", "contributors", "signups", "discussion", "awareness"})
VALID_VERIFICATION = frozenset({"primary_source_checked", "owner_attested"})
SENSITIVE_KEY = re.compile(r"(?:token|password|secret|cookie|session|api.?key|private.?key)",re.I)
TEXT_ONLY_STAGING = "runtime only stages text posts; other formats are creative briefs"
MAX_THREAD_PARTS = 25
MAX_CAROUSEL_SLIDES = 20
MAX_SLIDE_CHARS = 2000
MAX_HASHTAG_CAP = 30
MAX_RATIONALE_CHARS = 1000


def read_json(path: str | Path) -> dict:
    try:
        obj = read_json_file(Path(path))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid campaign/brand JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise ValidationError("Manifest must be a JSON object")
    return obj


def public_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    if any(SENSITIVE_KEY.search(key) or key.casefold() in ("sig", "signature", "auth", "code")
           for key,_ in parse_qsl(parsed.query,keep_blank_values=True)):
        return False
    return (parsed.scheme == "https" and "." in host and not host.endswith((".local", ".localhost"))
            and host not in ("localhost",) and not parsed.username and not parsed.password
            and port in (None, 443) and not parsed.fragment and "\\" not in url)


def _sensitive_keys(obj, location="root") -> list[str]:
    issues=[]
    if isinstance(obj, dict):
        for key, value in obj.items():
            if SENSITIVE_KEY.search(str(key)):
                issues.append(f"{location}.{key}: secret-like fields are forbidden in campaign/brand files")
            issues.extend(_sensitive_keys(value,f"{location}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            issues.extend(_sensitive_keys(value,f"{location}[{index}]"))
    return issues


def validate_brand(brand: dict) -> list[str]:
    issues = _sensitive_keys(brand,"brand")
    if brand.get("schema_version") != 1:
        issues.append("brand.schema_version must be 1")
    if not isinstance(brand.get("brand_id"), str) or not SLUG.fullmatch(brand["brand_id"]):
        issues.append("brand.brand_id must be a lowercase slug")
    for field in ("display_name", "voice"):
        if not isinstance(brand.get(field), str) or not brand[field].strip():
            issues.append(f"brand.{field} must be a nonempty string")
    accounts = brand.get("approved_accounts")
    if not isinstance(accounts, list) or not accounts or any(not isinstance(x, str) or not SLUG.fullmatch(x) for x in accounts):
        issues.append("brand.approved_accounts must be a list of account IDs")
    if isinstance(accounts, list) and len(accounts) != len(set(str(a) for a in accounts)):
        issues.append("brand.approved_accounts cannot contain duplicates")
    if not isinstance(brand.get("prohibited_claims"), list) or not all(isinstance(x, str) for x in brand.get("prohibited_claims", [])):
        issues.append("brand.prohibited_claims must be a list of strings (empty allowed)")
    banned = brand.get("banned_phrases", [])
    if not isinstance(banned, list) or any(not isinstance(x, str) or not x.strip() for x in banned):
        issues.append("brand.banned_phrases must be a list of nonempty strings when present")
    issues.extend(_brand_rule_issues(brand, "brand", banned_checked=True))
    overrides = brand.get("accounts")
    if overrides is not None:
        if not isinstance(overrides, dict):
            issues.append("brand.accounts must be an object of per-account rule overrides when present")
        else:
            approved = accounts if isinstance(accounts, list) else []
            for account_id, rules in overrides.items():
                path = f"brand.accounts.{account_id}"
                if account_id not in approved:
                    issues.append(f"{path}: per-account rules need an account listed in brand.approved_accounts")
                if not isinstance(rules, dict):
                    issues.append(f"{path} must be an object")
                    continue
                unknown = sorted(set(rules) - set(BRAND_RULE_FIELDS))
                if unknown:
                    issues.append(f"{path}: unknown rule fields {', '.join(unknown)}; allowed: {', '.join(BRAND_RULE_FIELDS)}")
                issues.extend(_brand_rule_issues(rules, path))
    return issues


def _brand_rule_issues(rules: dict, path: str, *, banned_checked: bool = False) -> list[str]:
    """Shared checks for global brand rules and one ``brand.accounts`` override."""
    issues = []
    banned = rules.get("banned_phrases", [])
    if not banned_checked and (not isinstance(banned, list) or any(not isinstance(x, str) or not x.strip() for x in banned)):
        issues.append(f"{path}.banned_phrases must be a list of nonempty strings when present")
    disclosure = rules.get("required_disclosure")
    if disclosure is not None and (not isinstance(disclosure, str) or not disclosure.strip() or len(disclosure) > 280):
        issues.append(f"{path}.required_disclosure must be a nonempty string of at most 280 characters when present")
    cap = rules.get("max_hashtags")
    if cap is not None and (type(cap) is not int or not 0 <= cap <= MAX_HASHTAG_CAP):
        issues.append(f"{path}.max_hashtags must be an integer 0..{MAX_HASHTAG_CAP} when present")
    return issues


def validate_campaign(campaign: dict, *, brand: dict | None = None, accounts: dict | None = None,
                      duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
                      optout: list | None = None,
                      grounding: bool = True, history: dict | None = None) -> tuple[list[str], list[str]]:
    """Errors block staging; warnings demand editorial judgment but are not claims of truth.

    ``grounding=False`` skips merging the claims.py report (callers such as preview.py
    that run ``check_claims`` themselves); it never relaxes any structural check.
    ``optout``: validated opt-out entries; an @-mention of a listed handle is an error.
    ``history``: optional ``history.load_history`` result; its findings are warnings only.
    """
    errors, warnings = _sensitive_keys(campaign,"campaign"), []
    if "cta_url" in campaign and not public_url(campaign["cta_url"]):
        errors.append("cta_url must be a public HTTPS URL without credentials when present")
    if campaign.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for key in ("campaign_id", "brand_id"):
        value = campaign.get(key)
        if not isinstance(value, str) or not SLUG.fullmatch(value):
            errors.append(f"{key} must be a lowercase slug")
    if not isinstance(campaign.get("audience"), str) or not campaign["audience"].strip():
        errors.append("audience must name a real target group")
    if isinstance(campaign.get("audience"), str) and PLACEHOLDER.search(campaign["audience"]):
        errors.append("audience: unresolved placeholder; write it before staging")
    if not isinstance(campaign.get("objective"), str) or campaign.get("objective") not in OBJECTIVES:
        errors.append(f"objective must be one of {', '.join(sorted(OBJECTIVES))}")
    evidence = campaign.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must include at least one source")
        evidence = []
    if len(evidence) > 100:
        errors.append("At most 100 evidence records per campaign")
        evidence = evidence[:100]
    source_ids = set()
    for idx, item in enumerate(evidence):
        path = f"evidence[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        sid = item.get("id")
        if not isinstance(sid, str) or not SLUG.fullmatch(sid) or sid in source_ids:
            errors.append(f"{path}.id must be a unique lowercase slug")
        else:
            source_ids.add(sid)
        if not public_url(item.get("url")):
            errors.append(f"{path}.url must be a public HTTPS URL without credentials")
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            errors.append(f"{path}.claim must describe the specific supported claim")
        elif PLACEHOLDER.search(item["claim"]):
            errors.append(f"{path}.claim: unresolved placeholder; write the claim the source supports")
        excerpt = item.get("excerpt")
        if excerpt is not None and (not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 5000):
            errors.append(f"{path}.excerpt must be nonempty source text of at most 5000 characters when present")
        if "kind" in item and (not isinstance(item["kind"], str) or item["kind"] not in EVIDENCE_KINDS):
            errors.append(f"{path}.kind must be one of {', '.join(sorted(EVIDENCE_KINDS))} when present")
        if not isinstance(item.get("verification"), str) or item.get("verification") not in VALID_VERIFICATION:
            errors.append(f"{path}.verification needs primary_source_checked or owner_attested")
        checked = item.get("checked_at")
        if not isinstance(checked,str):
            errors.append(f"{path}.checked_at must be ISO 8601 with a timezone")
        else:
            try:
                checked_utc = datetime.fromisoformat(parse_utc(checked))
                if checked_utc > datetime.now(timezone.utc):
                    errors.append(f"{path}.checked_at cannot be in the future")
                elif (datetime.now(timezone.utc)-checked_utc).days > 90:
                    warnings.append(f"{path}: source check is more than 90 days old; review volatile claims")
            except ValidationError:
                errors.append(f"{path}.checked_at must include a timezone")
        # Verification label records a declaration only; it is not proof that the URL was checked.
        if item.get("verification") == "owner_attested":
            warnings.append(f"{path} is owner-attested rather than independently source-checked")
        errors.extend(_private_evidence_issues(item, path))
    variants = campaign.get("variants")
    if not isinstance(variants, list) or not variants:
        errors.append("variants must include at least one draft")
        variants = []
    if len(variants) > 30:
        errors.append("At most 30 variants per campaign; split into reviewed batches")
        variants = variants[:30]
    variant_ids = set()
    for idx, variant in enumerate(variants):
        path = f"variants[{idx}]"
        if not isinstance(variant, dict):
            errors.append(f"{path} must be an object")
            continue
        vid = variant.get("id")
        if not isinstance(vid, str) or not SLUG.fullmatch(vid) or vid in variant_ids:
            errors.append(f"{path}.id must be a unique lowercase slug")
        else:
            variant_ids.add(vid)
        account_id = variant.get("account_id")
        platform = variant.get("platform")
        if not isinstance(account_id, str) or not SLUG.fullmatch(account_id):
            errors.append(f"{path}.account_id must be a lowercase slug")
        if accounts is not None:
            acc = accounts.get(account_id) if isinstance(account_id, str) else None
            if not acc or acc.get("platform") != platform:
                errors.append(f"{path}: account not configured for platform {platform}")
        if brand is not None and account_id not in brand.get("approved_accounts", []):
            errors.append(f"{path}: account is not approved for brand {campaign.get('brand_id')}")
        content = variant.get("text")
        fmt = variant.get("format", "text")
        meta = accounts.get(account_id) if accounts is not None and isinstance(account_id, str) else None
        meta = meta if isinstance(meta, dict) and meta.get("platform") == platform else None
        if fmt == "thread":
            parts = variant.get("parts")
            if "text" in variant:
                errors.append(f"{path}: thread variants carry their content in parts[], not text")
            if not isinstance(parts, list) or not 2 <= len(parts) <= MAX_THREAD_PARTS:
                errors.append(f"{path}.parts must be a list of 2..{MAX_THREAD_PARTS} posts for format thread")
            else:
                for n, part in enumerate(parts):
                    if not isinstance(part, str):
                        errors.append(f"{path}.parts[{n}] must be a string")
                    else:
                        errors.extend(f"{path}.parts[{n}]: {x}" for x in validate_post(cast(str, platform), part, meta))
        elif fmt == "carousel_outline":
            slides = variant.get("slides")
            if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_CAROUSEL_SLIDES:
                errors.append(f"{path}.slides must be a list of 1..{MAX_CAROUSEL_SLIDES} slides for format carousel_outline")
            elif any(not isinstance(x, str) or not x.strip() or len(x) > MAX_SLIDE_CHARS for x in slides):
                errors.append(f"{path}.slides must be nonempty strings of at most {MAX_SLIDE_CHARS} characters")
            if content is not None:
                if not isinstance(content, str):
                    errors.append(f"{path}.text (carousel caption) must be a string when present")
                else:
                    errors.extend(f"{path}: {x}" for x in validate_post(cast(str, platform), content, meta))
        elif not isinstance(content, str):
            errors.append(f"{path}.text must be a string")
        else:
            errors.extend(f"{path}: {x}" for x in validate_post(cast(str, platform), content, meta))
        rationale = variant.get("rationale")
        if rationale is not None and (not isinstance(rationale, str) or not rationale.strip()
                                      or len(rationale) > MAX_RATIONALE_CHARS):
            errors.append(f"{path}.rationale must be a nonempty string of at most {MAX_RATIONALE_CHARS} characters when present")
        claim_ids = variant.get("evidence_ids")
        if not isinstance(claim_ids, list) or not claim_ids:
            errors.append(f"{path}.evidence_ids cannot be empty")
        else:
            for claim_id in claim_ids:
                if not isinstance(claim_id, str) or claim_id not in source_ids:
                    errors.append(f"{path}: unknown evidence id {claim_id!r}")
        for field in ("angle", "cta"):
            if not isinstance(variant.get(field), str) or not variant[field].strip():
                errors.append(f"{path}.{field} must be a nonempty string")
        # Scaffolds (briefs.py) leave {{write: ...}} placeholders; none may survive into staging.
        for field in ("angle", "cta", "cta_phrase", "rationale"):
            if isinstance(variant.get(field), str) and PLACEHOLDER.search(variant[field]):
                errors.append(f"{path}.{field}: unresolved placeholder; write it before staging")
        if "cta_url" in variant and not public_url(variant["cta_url"]):
            errors.append(f"{path}.cta_url must be a public HTTPS URL without credentials when present")
        if "cta_phrase" in variant and (not isinstance(variant["cta_phrase"], str) or not variant["cta_phrase"].strip()):
            errors.append(f"{path}.cta_phrase must be a nonempty string when present")
        if fmt != "text":
            errors.append(f"{path}: {TEXT_ONLY_STAGING}")
        body = variant_body(variant)
        if brand is not None and body:
            for banned in brand.get("prohibited_claims",[]):
                if isinstance(banned,str) and banned.strip() and banned.casefold() in body.casefold():
                    errors.append(f"{path}: contains prohibited claim from brand profile")
        if body.strip():
            first_line = body.strip().splitlines()[0].strip()
            if len(first_line) > 200:
                warnings.append(f"{path}: first line is long; review opening for platform fit")
    errors.extend(_private_link_issues(campaign, variants))
    if brand is None:
        warnings.append("No brand file supplied; account ownership and voice not checked")
    else:
        errors.extend(validate_brand(brand))
        if brand.get("brand_id") != campaign.get("brand_id"):
            errors.append("Campaign brand_id does not match supplied brand profile")
    for i, left in enumerate(variants):
        if not isinstance(left, dict) or not isinstance(left.get("text"), str):
            continue
        for j in range(i + 1, len(variants)):
            right = variants[j]
            if not isinstance(right, dict) or not isinstance(right.get("text"), str):
                continue
            if left.get("platform") != right.get("platform") and SequenceMatcher(
                    None, left["text"].lower(), right["text"].lower()).ratio() >= 0.92:
                warnings.append(f"variants[{i}] and variants[{j}] may be copy-paste cross-posts; revise natively")
    if not grounding:
        return errors, warnings
    # Lexical claim grounding (claims.py): errors block staging; review items stay human decisions.
    report = check_claims(campaign, brand if isinstance(brand, dict) else None,
                          duplicate_threshold=duplicate_threshold, optout=optout, history=history)
    for item in report["variants"]:
        for finding in item["findings"]:
            message = f"variants[{item['index']}]: {finding['code']}: {finding['message']}"
            (errors if finding["severity"] == "error" else warnings).append(message)
    return errors, warnings


def _private_evidence_issues(item: dict, path: str) -> list[str]:
    """Issue #9: private-source evidence is blocked until a human marks it publishable."""
    issues = []
    visibility = item.get("source_visibility", "public")
    if not isinstance(visibility, str) or visibility not in VISIBILITIES:
        issues.append(f"{path}.source_visibility must be public or private when present")
    if "publishable" in item and not isinstance(item["publishable"], bool):
        issues.append(f"{path}.publishable must be true/false when present")
    if is_private_evidence(item) and item.get("publishable") is not True:
        issues.append(f"{path} comes from a private source; cite it only after you confirm its content may "
                      "be made public, then set \"publishable\": true on this evidence record")
    return issues


def _private_link_issues(campaign: dict, variants: list) -> list[str]:
    """A URL inside a private source repository is never a public link, even when publishable."""
    repos = private_evidence_repos(campaign)
    if not repos:
        return []
    issues = []
    if url_in_private_repo(campaign.get("cta_url"), repos):
        issues.append("cta_url points into a private source repository; private repository URLs are never public links")
    for idx, variant in enumerate(variants):
        if not isinstance(variant, dict):
            continue
        links = [variant.get("cta_url"), *links_in(variant_body(variant))]
        if any(url_in_private_repo(link, repos) for link in links):
            issues.append(f"variants[{idx}]: links into a private source repository; private repository URLs "
                          "are never public links")
    return issues


def stage_campaign(con, campaign: dict, brand: dict, accounts: dict, *, optout: list | None = None) -> list[dict]:
    """All-or-nothing, idempotent draft staging; no approval or network effects."""
    errors, _ = validate_campaign(campaign, brand=brand, accounts=accounts, optout=optout)
    if errors:
        raise ValidationError("; ".join(errors))
    results = []
    con.execute("BEGIN IMMEDIATE")
    try:
        for variant in campaign["variants"]:
            fingerprint = hashlib.sha256(json.dumps({
                "campaign": campaign, "brand": brand, "variant": variant
            }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            prior = con.execute("SELECT * FROM campaign_variants WHERE campaign_id=? AND variant_id=?",
                                (campaign["campaign_id"], variant["id"])).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValidationError("Campaign changed after staging; create a new campaign_id or variant ID")
                results.append({"variant_id": variant["id"], "post_id": prior["post_id"], "created": False})
                continue
            # A private source URL is never recorded as the post's source link (issue #9).
            cited = [x for eid in variant["evidence_ids"] for x in campaign["evidence"] if x["id"] == eid]
            source_url = next((x["url"] for x in cited if not is_private_evidence(x)), "")
            post_id = add_post(con, variant["account_id"], variant["platform"], variant["text"], campaign["campaign_id"], source_url,
                               account=accounts.get(variant["account_id"]))
            con.execute("""INSERT INTO campaign_variants(campaign_id,brand_id,variant_id,fingerprint,post_id,evidence_json,recorded_at)
                           VALUES(?,?,?,?,?,?,?)""", (campaign["campaign_id"],campaign["brand_id"],variant["id"],fingerprint,
                           post_id,json.dumps([e for e in campaign["evidence"] if e["id"] in variant["evidence_ids"]]),utc_now()))
            event(con,"campaign_variant_staged",post_id,f"campaign={campaign['campaign_id']} variant={variant['id']}")
            results.append({"variant_id": variant["id"], "post_id": post_id, "created": True})
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return results


def export_campaign(campaign: dict, brand: dict, destination: Path, *, optout: list | None = None) -> list[str]:
    """Export human-readable manual posting drafts; destination must already be NEW."""
    errors, _ = validate_campaign(campaign,brand=brand,optout=optout)
    if errors:
        raise ValidationError("; ".join(errors))
    if any(destination.iterdir()):
        raise ValidationError("Output directory must be empty; refusing to overwrite approved work")
    # Private-source excerpts and URLs never leave the local machine in an export pack (issue #9).
    exported = {**campaign, "evidence": redact_private_evidence(campaign.get("evidence"))}
    (destination / "campaign.json").write_text(json.dumps(exported,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    posts = destination / "drafts"
    posts.mkdir()
    files = [str(destination / "campaign.json")]
    for v in campaign["variants"]:
        filename=f"{v['platform']}-{v['id']}.txt"
        path=posts / filename
        path.write_text(v["text"]+"\n",encoding="utf-8")
        files.append(str(path))
    (destination / "MANUAL-REVIEW.txt").write_text(
        "DRAFTS ONLY. Verify every claim against its cited source, check account/format/media rights, "
        "and obtain human approval before any external publishing. No integration has posted these.\n",
        encoding="utf-8")
    files.append(str(destination / "MANUAL-REVIEW.txt"))
    return files
