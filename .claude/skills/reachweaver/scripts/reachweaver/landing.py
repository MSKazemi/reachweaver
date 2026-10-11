"""Landing-page checks for SEO briefs and campaign CTAs (issue #25 follow-up).

``landing_check`` runs the checks behind the ``landing-check`` CLI command.

Offline (the default) it reads two local files and opens no connection:
- every brief ``canonical_url`` must be https, carry no fragment and no ``utm_*`` or
  click-tracker parameter, and already be in ``normalize_destination`` form;
- every campaign CTA (``cta_url`` on the campaign or a variant, and any UTM-tagged
  link in a variant body) must be a valid tagged link (``validate_utm_link``) whose
  destination, once the tags are stripped, is a brief ``canonical_url``. A CTA that
  points at a page with no brief is an error.

Online (explicit ``online=True`` only) it also GETs each canonical page once:
- robots.txt is consulted first for every host, through ``sources.RobotsCache``
  (a disallowed or unreachable robots.txt means the page is not fetched);
- redirects are never followed by urllib (``publishers._send_http``). Each 3xx is
  inspected here: at most ``MAX_REDIRECTS`` hops, https only, and on the brief's
  registrable domain unless the owner allowlisted the target origin;
- bodies are capped at ``MAX_BODY_BYTES`` and requests time out (25 s);
- HTML is parsed with ``html.parser`` only. Script, style, template, noscript and
  svg content is not read as page text. Every value taken from a page is untrusted
  data: it is cleaned, truncated and reported, never followed as an instruction.

The claim check is LEXICAL: it looks for the campaign's grounded claim phrases
(``claims.check_claims`` ``grounded[]`` spans) in the page text. A missing phrase is
a wording mismatch between post and page. It is not truth verification.
Nothing here drafts, approves, schedules, publishes or writes to the database.
"""
from __future__ import annotations

import codecs
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.request import Request

from . import publishers
from .attribution import (
    TRACKING_PARAMS, _reject_secrets, load_seo_briefs, normalize_destination, normalize_origin,
    parse_utm, seo_conflicts, validate_utm_link,
)
from .campaigns import _sensitive_keys, read_json
from .claims import _URL, _normalize, _phrase_regex, check_claims, variant_body
from .policy import ValidationError
from .sources import USER_AGENT, RobotsCache, clean_text

REPORT = "landing_check"
MAX_PAGES = 50
MAX_BODY_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_TEXT_CHARS = 1_000_000
MAX_VALUE_CHARS = 300
REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
HTML_TYPES = ("text/html", "application/xhtml+xml")
SKIP_TEXT_TAGS = frozenset({"script", "style", "template", "noscript", "svg"})
# Multi-label suffixes under which every owner gets their own registrable domain. A small
# embedded subset of the Public Suffix List (stdlib has none): ICANN second-level
# suffixes plus shared-hosting "private" suffixes. Hosts not covered fall back to the
# last two labels. Allow anything else explicitly with ``allow_origins``.
MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "org.nz", "ac.nz",
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "co.kr", "or.kr", "ac.kr",
    "com.br", "com.cn", "com.tr", "com.mx", "com.ar", "co.in", "ac.in", "co.za", "ac.ir", "co.ir",
    "github.io", "gitlab.io", "pages.dev", "netlify.app", "vercel.app", "herokuapp.com",
    "readthedocs.io", "web.app", "firebaseapp.com", "blogspot.com", "wordpress.com",
    "azurewebsites.net", "cloudfront.net", "s3.amazonaws.com", "surge.sh", "fly.dev",
    "onrender.com", "glitch.me", "neocities.org", "codeberg.page", "substack.com", "medium.com",
})
CLAIM_LIMITATION = ("Lexical mismatch check only: grounded claim phrases from claims-check are searched "
                    "for in the page title and visible text. A missing phrase means the post and the page "
                    "use different wording. This is not truth verification: neither result shows a claim is true.")
NOTE = ("Landing-page review aid. Offline mode reads local files only. --online sends one GET per "
        "canonical page (plus robots.txt per host and inspected redirects) and nothing else. No post "
        "was drafted, approved, scheduled or published, and no database was opened.")
SEVERITIES = ("error", "warning")


def _finding(code: str, severity: str, message: str, **extra) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


def _status(findings: list[dict]) -> str:
    if any(f["severity"] == "error" for f in findings):
        return "error"
    return "warning" if findings else "ok"


def _clean(value: object) -> str | None:
    text, _ = clean_text(value, MAX_VALUE_CHARS)
    return text or None


def registrable_domain(host: str) -> str:
    """Approximate eTLD+1 for ``host`` (see MULTI_LABEL_SUFFIXES); lowercase, no trailing dot."""
    host = host.lower().rstrip(".")
    labels = host.split(".")
    for size in (3, 2):
        if len(labels) > size and ".".join(labels[-size:]) in MULTI_LABEL_SUFFIXES:
            return ".".join(labels[-size - 1:])
    return ".".join(labels[-2:])


# --------------------------------------------------------------------- offline checks

def canonical_url_findings(url: object, label: str = "canonical_url") -> list[dict]:
    """Errors that keep ``url`` from being a canonical landing URL (empty list when it is)."""
    if not isinstance(url, str) or not url.strip():
        return [_finding("canonical_url_missing", "error", f"{label} is missing")]
    try:
        parts = urlsplit(url)
    except ValueError:
        return [_finding("canonical_url_invalid", "error", f"{label} is not a valid URL")]
    findings = []
    if parts.scheme.lower() != "https":
        findings.append(_finding("canonical_url_not_https", "error", f"{label} must use https"))
    if parts.fragment or url.endswith("#"):
        findings.append(_finding("canonical_url_fragment", "error", f"{label} must not contain a #fragment"))
    tracking = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)
                       if k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS})
    if tracking:
        findings.append(_finding("canonical_url_tracking_params", "error",
                                 f"{label} must not carry tracking parameters: {', '.join(tracking)}",
                                 parameters=tracking))
    if findings:
        return findings
    try:
        normalized = normalize_destination(url)
    except ValidationError as exc:
        return [_finding("canonical_url_invalid", "error", f"{label}: {exc}")]
    if normalized != url:
        findings.append(_finding("canonical_url_not_normalized", "error",
                                 f"{label} is not in canonical form (lowercase host, no default port, "
                                 "sorted query)", expected=normalized))
    return findings


def _load_briefs(path: Path) -> tuple[dict, list[dict], list[dict]]:
    """Return (raw document, page entries, document-level findings). Raises on unreadable input."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid SEO briefs JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("briefs"), list) or not raw["briefs"]:
        raise ValidationError("SEO briefs file needs a non-empty 'briefs' list")
    _reject_secrets(raw)
    pages = []
    for index, brief in enumerate(raw["briefs"][:200]):
        brief = brief if isinstance(brief, dict) else {}
        page_id = brief.get("page_id") if isinstance(brief.get("page_id"), str) else f"briefs[{index}]"
        url = brief.get("canonical_url")
        pages.append({"page_id": page_id, "canonical_url": url if isinstance(url, str) else None,
                      "brief_status": brief.get("status", "active"),
                      "findings": canonical_url_findings(url, f"briefs[{index}].canonical_url")})
    document: list[dict] = []
    try:
        validated = load_seo_briefs(path)
    except ValidationError as exc:
        document.append(_finding("briefs_contract_invalid", "error",
                                 f"SEO briefs fail the contract (docs/SEO-INTEGRATION.md): {exc}"))
    else:
        by_id = {p["page_id"]: p for p in pages}
        for conflict in seo_conflicts(validated):
            if conflict["type"] != "duplicate_canonical_url_across_briefs":
                continue
            for page_id in conflict["page_ids"]:
                by_id[page_id]["findings"].append(_finding(
                    "duplicate_canonical_url", "warning",
                    f"canonical_url is shared with {', '.join(i for i in conflict['page_ids'] if i != page_id)}"))
    return raw, pages, document


def _link_candidates(campaign: dict) -> list[dict]:
    """Every link worth checking: declared ``cta_url`` values and links inside variant bodies."""
    links = []
    if isinstance(campaign.get("cta_url"), str) and campaign["cta_url"].strip():
        links.append({"variant_id": None, "source": "campaign.cta_url", "url": campaign["cta_url"].strip()})
    variants: list = campaign["variants"] if isinstance(campaign.get("variants"), list) else []
    for variant in variants[:30]:
        if not isinstance(variant, dict):
            continue
        vid = variant.get("id") if isinstance(variant.get("id"), str) else None
        if isinstance(variant.get("cta_url"), str) and variant["cta_url"].strip():
            links.append({"variant_id": vid, "source": "variant.cta_url", "url": variant["cta_url"].strip()})
        for match in _URL.finditer(variant_body(variant)):
            url = match.group().rstrip(".,;:!?")
            if url.lower().startswith(("http://", "https://")):
                links.append({"variant_id": vid, "source": "variant.text", "url": url})
    return links


def _check_link(link: dict, canonical: dict[str, str]) -> tuple[str | None, list[dict]]:
    """Return (matched page_id or None, findings) for one campaign link."""
    url = link["url"]
    tagged = bool(parse_utm(url)) or any(k.lower().startswith("utm_") for k, _ in
                                         parse_qsl(urlsplit(url).query, keep_blank_values=True))
    declared = link["source"] != "variant.text"
    where = {"variant_id": link["variant_id"], "source": link["source"], "url": url}
    try:
        destination = normalize_destination(url)
    except ValidationError as exc:
        if declared or tagged:
            return None, [_finding("cta_invalid", "error", f"CTA link is not a public https URL: {exc}", **where)]
        return None, []
    page_id = canonical.get(destination)
    if not declared and not tagged:
        return page_id, []  # an untagged reference link in the text: matched for claims only
    findings = []
    if tagged:
        for issue in validate_utm_link(url):
            findings.append(_finding("utm_invalid", "error", f"UTM link: {issue}", **where))
    else:
        for item in canonical_url_findings(url, "cta_url"):
            findings.append({**item, "code": item["code"].replace("canonical_url", "cta_url"), **where})
        findings.append(_finding("cta_untagged", "warning", "declared CTA has no UTM tags; build it with "
                                 "utm-link --content POST_ID so attribution-report can credit the post", **where))
    if page_id is None:
        findings.append(_finding("cta_no_brief", "error",
                                 f"CTA points to {destination}, which is not the canonical_url of any brief",
                                 destination=destination, **where))
    return page_id, findings


def _check_campaign(campaign: dict, pages: list[dict]) -> tuple[dict, dict[str, list[dict]]]:
    """Campaign CTA findings plus, per page_id, the grounded phrases its variants assert."""
    canonical = {p["canonical_url"]: p["page_id"] for p in pages
                 if p["canonical_url"] and not any(f["severity"] == "error" for f in p["findings"])}
    findings: list[dict] = [_finding("campaign_secret_like_field", "error", issue)
                            for issue in _sensitive_keys(campaign, "campaign")]
    links = _link_candidates(campaign)
    variant_pages: dict[str | None, set[str]] = {}
    checked_links = []
    for link in links:
        page_id, link_findings = _check_link(link, canonical)
        findings.extend(link_findings)
        checked_links.append({**link, "page_id": page_id})
        if page_id:
            variant_pages.setdefault(link["variant_id"], set()).add(page_id)
    if not any(link["source"] != "variant.text" or link["page_id"] for link in checked_links):
        findings.append(_finding("campaign_no_landing_link", "warning",
                                 "no CTA or link in this campaign points to a brief's canonical page"))
    grounding = check_claims(campaign)
    by_page: dict[str, dict[str, dict]] = {}
    for variant in grounding["variants"]:
        vid = variant["variant_id"]
        targets = variant_pages.get(vid, set()) | variant_pages.get(None, set())
        for item in variant["grounded"]:
            if item["type"] == "feature":
                continue  # a feature span carries the draft's own verb ("now supports X"), not page wording
            key = " ".join(item["span"].casefold().split())
            for page_id in sorted(targets) if key else ():
                entry = by_page.setdefault(page_id, {}).setdefault(
                    key, {"phrase": item["span"], "type": item["type"], "variant_ids": []})
                if vid not in entry["variant_ids"]:
                    entry["variant_ids"].append(vid)
    phrases = {page_id: list(entries.values()) for page_id, entries in by_page.items()}
    summary = {"campaign_id": campaign.get("campaign_id"), "status": _status(findings),
               "links": checked_links, "findings": findings,
               "pages_linked": sorted({p for ids in variant_pages.values() for p in ids})}
    return summary, phrases


# --------------------------------------------------------------------- online fetch

class _LandingHTML(HTMLParser):
    """Collect head metadata and visible text; never executes or follows anything."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.seen_html = False
        self.title_parts: list[str] = []
        self.title_seen = False
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.robots: list[str] = []
        self.canonicals: list[str] = []
        self.text: list[str] = []
        self._text_len = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs if isinstance(k, str)}
        if tag == "html" and not self.seen_html:
            self.seen_html = True
            self.lang = a.get("lang", "").strip() or None
        elif tag == "title" and not self.title_seen:
            self._in_title = True
        elif tag == "meta":
            content = a.get("content", "").strip()
            for key in (a.get("property", ""), a.get("name", "")):
                key = key.strip().lower()
                if key in ("robots", "googlebot"):
                    self.robots.append(content.lower())
                elif key and key not in self.meta:
                    self.meta[key] = content
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href", "").strip():
            self.canonicals.append(a["href"].strip())
        if tag in SKIP_TEXT_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title_seen = True
        if tag in SKIP_TEXT_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip and self._text_len < MAX_TEXT_CHARS:
            self.text.append(data)
            self._text_len += len(data)


def parse_landing(html: str) -> dict:
    """Extract lang, title, meta, robots, canonical links and visible text with html.parser."""
    parser = _LandingHTML()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        pass  # malformed markup: keep what was parsed
    title = " ".join("".join(parser.title_parts).split())
    return {"lang": parser.lang, "title": title or None, "meta": parser.meta, "robots": parser.robots,
            "canonicals": parser.canonicals,
            "text": " ".join(" ".join(parser.text).split())[:MAX_TEXT_CHARS]}


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    try:
        value = getter(name)
    except Exception:  # noqa: BLE001 - an odd header object must not break the check
        return None
    return value if isinstance(value, str) and len(value) <= 8192 else None


def _decode(raw: bytes, content_type: str | None) -> str:
    charset = "utf-8"
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset" and value.strip().strip("\"'"):
            charset = value.strip().strip("\"'")
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = "utf-8"
    return raw.decode(charset, errors="replace")


def _redirect_allowed(target: str, root: str, allowed: set[str]) -> str | None:
    """None when the redirect target may be fetched, else a finding code."""
    try:
        parts = urlsplit(target)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "redirect_invalid"
    if parts.scheme.lower() != "https" or not host:
        return "redirect_not_https"
    origin = f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"
    if registrable_domain(host) != root and origin not in allowed:
        return "redirect_off_domain"
    return None


def fetch_landing(url: str, *, robots: RobotsCache, allowed_origins: set[str] | None = None) -> dict:
    """GET ``url`` with robots.txt, inspected-redirect and size guards; never raises for HTTP failures.

    Returns ``status`` (final HTTP status or None), ``final_url``, ``redirect_chain``,
    ``headers`` (content-type, x-robots-tag), ``body`` (bytes or None) and ``error``
    (a sanitized code or None) plus ``error_detail``.
    """
    allowed = allowed_origins or set()
    root = registrable_domain(urlsplit(url).hostname or "")
    chain: list[dict] = []
    current = url
    result: dict[str, Any] = {"status": None, "final_url": url, "redirect_chain": chain,
                              "headers": {}, "body": None, "error": None, "error_detail": None}

    def fail(code: str, detail: str | None = None) -> dict:
        result.update(error=code, error_detail=detail, final_url=current)
        return result

    while True:
        reason = robots.skip_reason(current)
        if reason:
            return fail(reason, "robots.txt does not allow ReachWeaver to fetch this URL"
                        if reason == "robots_disallowed" else "robots.txt could not be read; not fetched")
        req = Request(current, method="GET", headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"})
        try:
            with publishers._send_http(req) as response:
                headers = getattr(response, "headers", None)
                status = getattr(response, "status", None)
                if not isinstance(status, int):
                    getcode = getattr(response, "getcode", None)
                    status = getcode() if callable(getcode) else 200
                result["status"] = status
                content_type = _header(headers, "Content-Type")
                result["headers"] = {"content-type": content_type,
                                     "x-robots-tag": _header(headers, "X-Robots-Tag")}
                length = _header(headers, "Content-Length")
                if length and length.strip().isascii() and length.strip().isdecimal() and int(length.strip()) > MAX_BODY_BYTES:
                    return fail("body_too_large", f"Content-Length exceeds {MAX_BODY_BYTES} bytes")
                raw = response.read(MAX_BODY_BYTES + 1)
        except HTTPError as exc:
            code = int(exc.code)
            location = _header(getattr(exc, "headers", None), "Location")
            try:
                exc.close()
            except Exception:  # noqa: BLE001 - closing a mocked error body is best effort
                pass
            if code not in REDIRECT_CODES:
                result["status"] = code
                return fail(f"http_{code}")
            if not location or not location.strip():
                result["status"] = code
                return fail("redirect_without_location")
            target = urljoin(current, location.strip())
            chain.append({"status": code, "from": current, "to": target})
            if len(chain) > MAX_REDIRECTS:
                result["status"] = code
                return fail("too_many_redirects", f"more than {MAX_REDIRECTS} redirects")
            if any(hop["from"] == target for hop in chain):
                result["status"] = code
                return fail("redirect_loop")
            refused = _redirect_allowed(target, root, allowed)
            if refused:
                result["status"] = code
                current = target
                return fail(refused, "redirect target not fetched")
            current = target
            continue
        except (URLError, TimeoutError, OSError, ValueError):
            return fail("network_or_timeout")
        if not isinstance(raw, bytes):
            return fail("unexpected_response")
        if len(raw) > MAX_BODY_BYTES:
            return fail("body_too_large", f"body exceeds {MAX_BODY_BYTES} bytes; not parsed")
        if not 200 <= int(status) < 300:
            return fail(f"http_{status}")
        result.update(final_url=current, body=raw)
        return result


def _online_findings(page: dict, fetched: dict, phrases: list[dict]) -> tuple[dict, list[dict]]:
    """Inspect one fetched page; returns (online summary, findings)."""
    canonical = page["canonical_url"]
    summary: dict[str, Any] = {"fetched": fetched["body"] is not None, "http_status": fetched["status"],
                               "final_url": fetched["final_url"], "redirect_chain": fetched["redirect_chain"]}
    findings: list[dict] = []
    if fetched["error"]:
        code = fetched["error"]
        severity = "error"
        messages = {
            "robots_disallowed": "robots.txt disallows fetching this page; it was not checked",
            "robots_unavailable": "robots.txt could not be read; the page was not fetched (RFC 9309: assume disallow)",
            "redirect_off_domain": "redirect leaves the brief's registrable domain; allow the origin with --allow-origin",
            "redirect_not_https": "redirect target is not https",
            "too_many_redirects": f"more than {MAX_REDIRECTS} redirects",
            "redirect_loop": "redirect loop",
            "body_too_large": f"response body exceeds {MAX_BODY_BYTES} bytes; not parsed",
        }
        message = messages.get(code, f"landing page fetch failed: {code}")
        findings.append(_finding(code if not code.startswith("http_") else "http_error", severity, message,
                                 http_status=fetched["status"], url=fetched["final_url"]))
        summary["error"] = code
        return summary, findings
    if fetched["final_url"] != canonical:
        findings.append(_finding("canonical_url_redirects", "warning",
                                 f"canonical_url redirects to {fetched['final_url']}; link the final URL in "
                                 "the brief and the posts", final_url=fetched["final_url"]))
    content_type = (fetched["headers"].get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type not in HTML_TYPES:
        findings.append(_finding("not_html", "error", f"response is {content_type}, not an HTML page"))
        summary["content_type"] = content_type
        return summary, findings
    parsed = parse_landing(_decode(fetched["body"], fetched["headers"].get("content-type")))
    meta = parsed["meta"]
    # rel=canonical
    declared: list[str | None] = []
    for href in parsed["canonicals"]:
        target = urljoin(fetched["final_url"], href)
        try:
            declared.append(normalize_destination(target))
        except ValidationError:
            declared.append(None)
            findings.append(_finding("canonical_link_invalid", "error",
                                     "<link rel=canonical> is not a public https URL", href=_clean(href)))
    summary["canonical_link"] = _clean(parsed["canonicals"][0]) if parsed["canonicals"] else None
    valid = [d for d in declared if d]
    if not parsed["canonicals"]:
        findings.append(_finding("canonical_link_missing", "warning", "page has no <link rel=canonical>"))
    elif len(set(valid)) > 1:
        findings.append(_finding("canonical_link_multiple", "error",
                                 "page declares more than one distinct <link rel=canonical>"))
    if valid and canonical not in valid:
        findings.append(_finding("canonical_mismatch", "error",
                                 f"<link rel=canonical> is {valid[0]}, but the brief says {canonical}",
                                 declared=valid[0]))
    # title / description
    summary["title"] = _clean(parsed["title"])
    if not summary["title"]:
        findings.append(_finding("title_missing", "error", "page has no <title>"))
    summary["meta_description"] = _clean(meta.get("description"))
    if not summary["meta_description"]:
        findings.append(_finding("meta_description_missing", "warning", "page has no meta description"))
    # Open Graph social preview
    og = {key: _clean(meta.get(f"og:{key}")) for key in ("title", "description", "image")}
    summary["og"] = og
    for key in ("title", "description", "image"):
        if not og[key]:
            findings.append(_finding(f"og_{key}_missing", "warning",
                                     f"page has no og:{key}; social previews may be blank or guessed"))
    if og["image"]:
        image = urljoin(fetched["final_url"], meta["og:image"])
        if urlsplit(image).scheme.lower() != "https":
            findings.append(_finding("og_image_not_https", "error", "og:image must be an https URL",
                                     og_image=_clean(image)))
    # indexing
    header_robots = (fetched["headers"].get("x-robots-tag") or "").lower()
    directives = {d.strip() for value in parsed["robots"] + [header_robots] for d in value.split(",")}
    noindex = bool(directives & {"noindex", "none"}) or "noindex" in header_robots
    summary["noindex"] = noindex
    if noindex:
        findings.append(_finding("noindex", "warning",
                                 "page asks search engines not to index it (meta robots or X-Robots-Tag)"))
    summary["html_lang"] = _clean(parsed["lang"])
    if not summary["html_lang"]:
        findings.append(_finding("html_lang_missing", "warning", "<html> has no lang attribute"))
    # lexical claim phrases
    haystack = " ".join(_normalize(" ".join(filter(None, (parsed["title"], parsed["text"]))))[0].split())
    found: list[dict] = []
    missing: list[dict] = []
    for item in phrases:
        pattern = _phrase_regex(item["phrase"])
        (found if pattern and pattern.search(haystack) else missing).append(item)
    for item in missing:
        findings.append(_finding("claim_phrase_not_on_page", "warning",
                                 f"grounded claim phrase {item['phrase']!r} (variants: "
                                 f"{', '.join(str(v) for v in item['variant_ids'])}) is not in the page text; "
                                 "lexical mismatch only, not a truth check",
                                 variant_ids=item["variant_ids"], phrase=item["phrase"]))
    summary["claims"] = {"method": "lexical", "checked": len(phrases), "found": found, "missing": missing,
                         "limitation": CLAIM_LIMITATION}
    return summary, findings


# --------------------------------------------------------------------- entry point

def landing_check(briefs_path, campaign_path=None, *, online: bool = False, allow_origins=None) -> dict:
    """Run offline landing checks, and the online page checks only when ``online`` is True."""
    _, pages, document = _load_briefs(Path(briefs_path))
    allowed = {normalize_origin(o) for o in allow_origins or []}
    campaign_summary = None
    phrases: dict[str, list[dict]] = {}
    if campaign_path is not None:
        campaign_summary, phrases = _check_campaign(read_json(campaign_path), pages)
    linked = set(campaign_summary["pages_linked"]) if campaign_summary else set()
    if online:
        robots = RobotsCache()
        budget = MAX_PAGES
        for page in pages:
            if page["findings"] and any(f["severity"] == "error" for f in page["findings"]):
                page["online"] = {"fetched": False, "skipped": "canonical_url_invalid"}
                continue
            if page["brief_status"] != "active" and page["page_id"] not in linked:
                page["online"] = {"fetched": False, "skipped": f"brief_status_{page['brief_status']}"}
                continue
            if budget <= 0:
                page["online"] = {"fetched": False, "skipped": "page_limit"}
                page["findings"].append(_finding("not_checked_page_limit", "warning",
                                                 f"only {MAX_PAGES} pages are fetched per run"))
                continue
            budget -= 1
            fetched = fetch_landing(page["canonical_url"], robots=robots, allowed_origins=allowed)
            summary, findings = _online_findings(page, fetched, phrases.get(page["page_id"], []))
            page["online"] = summary
            page["findings"].extend(findings)
    elif phrases:
        for page in pages:
            if page["page_id"] in phrases:
                page["claims_pending"] = len(phrases[page["page_id"]])
    for page in pages:
        page["status"] = _status(page["findings"])
    all_findings = document + [f for p in pages for f in p["findings"]] + \
        (campaign_summary["findings"] if campaign_summary else [])
    summary = {severity: sum(1 for f in all_findings if f["severity"] == severity) for severity in SEVERITIES}
    summary.update({f"pages_{s}": sum(1 for p in pages if p["status"] == s) for s in ("ok", "warning", "error")})
    return {"report": REPORT, "mode": "online" if online else "offline", "status": _status(all_findings),
            "summary": summary, "document_findings": document, "pages": pages, "campaign": campaign_summary,
            "claim_check": CLAIM_LIMITATION, "note": NOTE}


def exit_code(result: dict) -> int:
    return 1 if result["status"] == "error" else 0


def render_text(result: dict) -> str:
    s = result["summary"]
    lines = [f"landing-check ({result['mode']}): {result['status'].upper()} - {len(result['pages'])} page(s): "
             f"{s['pages_ok']} ok, {s['pages_warning']} warning, {s['pages_error']} error"]

    def show(findings: list[dict], indent: str = "    "):
        for f in findings:
            lines.append(f"{indent}{f['severity'].upper()} {f['code']}: {f['message']}")

    show(result["document_findings"], "  ")
    for page in result["pages"]:
        lines.append(f"  [{page['status']}] {page['page_id']} {page['canonical_url'] or '(no canonical_url)'}")
        online = page.get("online")
        if online and online.get("fetched"):
            hops = " -> ".join([h["from"] for h in online["redirect_chain"]] + [online["final_url"]])
            lines.append(f"    HTTP {online['http_status']} {hops}")
            lines.append(f"    title={online.get('title')!r} lang={online.get('html_lang')!r} "
                         f"noindex={online.get('noindex')} og:image={online.get('og', {}).get('image')!r}")
            claims = online.get("claims")
            if claims and claims["checked"]:
                lines.append(f"    claim phrases on page (lexical): {len(claims['found'])}/{claims['checked']}")
        elif online and online.get("skipped"):
            lines.append(f"    not fetched: {online['skipped']}")
        show(page["findings"])
    campaign = result["campaign"]
    if campaign:
        lines.append(f"  campaign {campaign['campaign_id']}: {campaign['status']}; linked pages: "
                     f"{', '.join(campaign['pages_linked']) or 'none'}")
        show(campaign["findings"])
    if result["mode"] == "offline":
        lines.append("Offline only: liveness, rel=canonical, title, Open Graph, noindex, lang and claim "
                     "phrases need --online.")
    lines.append(result["claim_check"])
    lines.append(result["note"])
    return "\n".join(lines)
