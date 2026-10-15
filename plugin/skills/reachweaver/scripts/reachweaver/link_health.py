"""Link-health monitor for stored campaign links (public issue #10).

``link_health`` reads the links in the posts stored in the local database and reports which
are broken, redirected, blocked by robots.txt or missing Open Graph preview tags.

Offline (the default) it only inspects the URLs: https, no credentials, and valid UTM tags on
links that appear in a post body. It opens no connection.

Online (explicit ``online=True`` only) it GETs each distinct landing page once, UTM tags
stripped, through ``landing.fetch_landing``: robots.txt is consulted first for every host (a
disallowed or unreadable robots.txt means the page is not fetched), redirects are inspected
rather than followed blindly (https only, same registrable domain unless the owner allowlisted
the origin), bodies and the number of pages are capped, and requests time out. Images are never
requested. Every value read from a page is untrusted data: cleaned, truncated and reported,
never followed as an instruction.

Nothing here drafts, approves, schedules, publishes or writes to the database.

Every URL the report prints goes through ``attribution.report_landing_page``: scheme, host, path and
the canonical ``utm_*`` parameters only, then ``privacy.redact``. A stored link whose query carries a
token or an e-mail address (``?token=``, ``?email=``) is still grouped and, with ``--online``, fetched
by its full destination, but the query values never reach the output; such a link is marked
``"redacted": true`` (issues #15, #26).
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from .attribution import normalize_destination, normalize_origin, report_landing_page, validate_utm_link
from .landing import (
    HTML_TYPES, MAX_PAGES, MAX_REDIRECTS, _clean, _decode, _finding, fetch_landing, parse_landing,
)
from .policy import ValidationError
from .privacy import redact
from .sources import RobotsCache

REPORT = "link_health"
DEFAULT_STATUSES = ("approved", "scheduled", "published")
VALID_STATUSES = ("draft", "approved", "scheduled", "sending", "published", "needs_review", "cancelled")
DEFAULT_MAX_LINKS = 25
MAX_LINKS_LIMIT = MAX_PAGES
_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_TRAILING = ".,;:!?"
NOTE = ("Link-health review aid. Offline mode inspects stored URLs only. --online sends one GET per distinct "
        "landing page (UTM tags stripped) after robots.txt; it never requests images, never follows an "
        "off-domain redirect and writes nothing. A healthy result is a point-in-time observation, not a "
        "guarantee, and says nothing about the page content.")


def extract_urls(text: object) -> list[str]:
    """http(s) URLs in ``text`` in order of appearance, trailing sentence punctuation removed."""
    if not isinstance(text, str):
        return []
    return [match.rstrip(_TRAILING) for match in _URL.findall(text) if match.rstrip(_TRAILING)]


def collect_links(con, *, statuses=DEFAULT_STATUSES, account_id: str | None = None,
                  campaign: str | None = None) -> tuple[dict[str, dict], list[dict], int]:
    """Group stored links by landing destination.

    Returns ``(by_destination, unusable, post_count)``. ``unusable`` lists URLs that cannot be
    normalized to a public https destination (http, credentials, whitespace, private host).
    """
    bad = [s for s in statuses if s not in VALID_STATUSES]
    if not statuses or bad:
        raise ValidationError(f"--status must be one or more of {', '.join(VALID_STATUSES)}")
    query = f"SELECT id, account_id, platform, content, source_url, status FROM posts WHERE status IN ({','.join('?' * len(statuses))})"
    params: list[Any] = list(statuses)
    if account_id:
        query += " AND account_id=?"
        params.append(account_id)
    if campaign:
        query += " AND campaign=?"
        params.append(campaign)
    posts = con.execute(query + " ORDER BY created_at, id", params).fetchall()
    grouped: dict[str, dict] = {}
    unusable: list[dict] = []
    for post in posts:
        found = [(url, "body") for url in extract_urls(post["content"])]
        found += [(url, "source_url") for url in extract_urls(post["source_url"])]
        for raw, where in found:
            try:
                destination = normalize_destination(raw)
            except ValidationError as exc:
                unusable.append({"url": _clean(raw), "post_id": post["id"], "where": where, "problem": str(exc)})
                continue
            entry = grouped.setdefault(destination, {"destination": destination, "post_ids": [], "platforms": [],
                                                     "raw_urls": [], "utm_issues": {}})
            for key, value in (("post_ids", post["id"]), ("platforms", post["platform"])):
                if value not in entry[key]:
                    entry[key].append(value)
            if raw not in entry["raw_urls"]:
                entry["raw_urls"].append(raw)
            if where == "body" and raw not in entry["utm_issues"]:
                entry["utm_issues"][raw] = validate_utm_link(raw)
    return grouped, unusable, len(posts)


def _classify_fetch(fetched: dict, destination: str) -> tuple[str, dict, list[dict]]:
    """(status, online summary, findings) for one fetched destination."""
    summary: dict[str, Any] = {"http_status": fetched["status"], "final_url": _clean(fetched["final_url"]),
                               "redirect_chain": fetched["redirect_chain"]}
    findings: list[dict] = []
    code = fetched["error"]
    if code in ("robots_disallowed", "robots_unavailable"):
        findings.append(_finding(code, "warning", "robots.txt does not allow, or could not confirm, a fetch; "
                                 "the link was not checked", url=destination))
        return "blocked_by_robots", summary, findings
    if code in ("redirect_off_domain", "redirect_not_https", "redirect_invalid"):
        findings.append(_finding(code, "warning", "the link redirects somewhere ReachWeaver will not follow "
                                 "(off the page's domain or not https); allow a trusted origin with --allow-origin",
                                 final_url=_clean(fetched["final_url"])))
        summary["error"] = code
        return "redirected", summary, findings
    if code:
        message = {"too_many_redirects": f"more than {MAX_REDIRECTS} redirects", "redirect_loop": "redirect loop",
                   "network_or_timeout": "no response (network error or timeout)",
                   "body_too_large": "response body is too large to inspect"}.get(code, f"fetch failed: {code}")
        findings.append(_finding("http_error" if code.startswith("http_") else code, "error", message,
                                 http_status=fetched["status"], url=destination))
        summary["error"] = code
        return "broken", summary, findings
    status = "ok"
    if fetched["redirect_chain"] or fetched["final_url"] != destination:
        try:
            moved = normalize_destination(fetched["final_url"]) != destination
        except ValidationError:
            moved = True
        if moved:
            status = "redirected"
            findings.append(_finding("redirects", "warning", f"the link redirects to {_clean(fetched['final_url'])}; "
                                     "link the final URL in future posts", final_url=_clean(fetched["final_url"])))
    content_type = (fetched["headers"].get("content-type") or "").split(";")[0].strip().lower()
    summary["content_type"] = content_type or None
    if content_type and content_type not in HTML_TYPES:
        findings.append(_finding("not_html", "warning", f"response is {content_type}; no preview tags to check"))
        return status, summary, findings
    parsed = parse_landing(_decode(fetched["body"], fetched["headers"].get("content-type")))
    meta = parsed["meta"]
    og = {key: _clean(meta.get(f"og:{key}")) for key in ("title", "description", "image")}
    summary["og"] = og
    for key, value in og.items():
        if not value:
            findings.append(_finding(f"og_{key}_missing", "warning",
                                     f"page has no og:{key}; social previews may be blank or guessed"))
    if og["image"] and urlsplit(urljoin(fetched["final_url"], meta["og:image"])).scheme.lower() != "https":
        findings.append(_finding("og_image_not_https", "warning", "og:image is not an https URL"))
    summary["title"] = _clean(parsed["title"])
    if not summary["title"]:
        findings.append(_finding("title_missing", "warning", "page has no <title>"))
    return status, summary, findings


def _scrub_text(text: str) -> str:
    """Report-safe text: each URL cut to origin, path and utm_* parameters, then secrets and e-mails redacted."""
    return redact(_URL.sub(lambda match: report_landing_page(match.group(0)), text), emails=True)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    return value


def _report_safe(link: dict) -> dict:
    """Strip query values, secrets and e-mail addresses from what is printed (not from what is fetched)."""
    destination = _scrub_text(link["destination"])
    safe = {**link, "destination": destination, "findings": _scrub(link["findings"]),
            "online": _scrub(link["online"])}
    if destination != link["destination"]:
        safe["redacted"] = True
    return safe


def link_health(con, *, statuses=DEFAULT_STATUSES, account_id: str | None = None, campaign: str | None = None,
                online: bool = False, allow_origins=None, max_links: int = DEFAULT_MAX_LINKS,
                robots: RobotsCache | None = None, fetch=fetch_landing) -> dict:
    """Report the health of every link stored in matching posts; network only when ``online``."""
    if type(max_links) is not int or not 1 <= max_links <= MAX_LINKS_LIMIT:
        raise ValidationError(f"--max-links must be an integer 1..{MAX_LINKS_LIMIT}")
    allowed = {normalize_origin(origin) for origin in allow_origins or []}
    grouped, unusable, post_count = collect_links(con, statuses=statuses, account_id=account_id, campaign=campaign)
    robots = robots or RobotsCache()
    links: list[dict] = []
    budget = max_links
    for destination, entry in sorted(grouped.items()):
        findings = [_finding("utm_issue", "warning", f"{url}: " + "; ".join(issues))
                    for url, issues in entry["utm_issues"].items() if issues]
        record: dict[str, Any] = {"destination": destination, "post_ids": entry["post_ids"],
                                  "platforms": entry["platforms"], "links_in_posts": len(entry["raw_urls"]),
                                  "status": "not_checked_offline", "online": None, "findings": findings}
        if online:
            if budget <= 0:
                record["status"] = "not_checked"
                findings.append(_finding("not_checked_link_limit", "warning",
                                         f"only {max_links} distinct pages are fetched per run; raise --max-links"))
            else:
                budget -= 1
                fetched = fetch(destination, robots=robots, allowed_origins=allowed)
                record["status"], record["online"], extra = _classify_fetch(fetched, destination)
                findings.extend(extra)
        links.append(record)
    for item in unusable:
        links.append({"destination": item["url"], "post_ids": [item["post_id"]], "platforms": [],
                      "links_in_posts": 1, "status": "unusable", "online": None,
                      "findings": [_finding("unusable_link", "error", item["problem"], where=item["where"])]})
    links = [_report_safe(link) for link in links]  # after every fetch: only the printed copy is cut
    counts: dict[str, int] = {}
    for link in links:
        counts[link["status"]] = counts.get(link["status"], 0) + 1
    every = [f for link in links for f in link["findings"]]
    return {"report": REPORT, "mode": "online" if online else "offline",
            "filters": {"statuses": list(statuses), "account": account_id, "campaign": campaign},
            "posts_scanned": post_count, "distinct_destinations": len(grouped),
            "summary": {"links": counts, "errors": sum(1 for f in every if f["severity"] == "error"),
                        "warnings": sum(1 for f in every if f["severity"] == "warning")},
            "links": links, "note": NOTE}


def exit_code(report: dict) -> int:
    return 1 if report["summary"]["errors"] else 0


def render_text(report: dict) -> str:
    summary = report["summary"]
    counts = ", ".join(f"{n} {k}" for k, n in sorted(summary["links"].items())) or "no links"
    lines = [f"link-health ({report['mode']}): {report['posts_scanned']} post(s), "
             f"{report['distinct_destinations']} distinct destination(s): {counts}; "
             f"{summary['errors']} error(s), {summary['warnings']} warning(s)"]
    for link in report["links"]:
        lines.append(f"  [{link['status']}] {link['destination']}  (posts: {', '.join(link['post_ids'])})")
        online = link.get("online")
        if online and online.get("http_status"):
            lines.append(f"    HTTP {online['http_status']} -> {online['final_url']}")
        for finding in link["findings"]:
            lines.append(f"    {finding['severity'].upper()} {finding['code']}: {finding['message']}")
    if report["mode"] == "offline":
        lines.append("Offline only: liveness, redirects and Open Graph tags need --online.")
    lines.append(report["note"])
    return "\n".join(lines)
