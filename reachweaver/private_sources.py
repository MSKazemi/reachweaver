"""Exfiltration guards for opt-in private GitHub source signals (issue #9, owner-approved 2026-10-09).

A private repository is read only when ALL THREE opt-ins hold: the source entry sets
``"visibility": "private"``, it sets ``"sensitivity": "private"``, and the owner passes
``sources-scan --allow-private``. This module holds the shared rules that keep what such
a scan reads from leaking outward:

- Every signal from a private source carries ``visibility: "private"``, ``private: true``
  and ``publishable: false``. Campaign evidence derived from it carries
  ``source_visibility: "private"`` and is refused by ``validate_campaign`` until a human
  sets ``"publishable": true`` on that evidence record at review time.
- A URL inside a private repository (github.com, api.github.com or
  raw.githubusercontent.com) is never a public link: UTM tagging and campaign links
  refuse it, even when the evidence itself was marked publishable.
- Exports replace private evidence excerpts and URLs with a marker.
- Token scope: GitHub returns ``X-OAuth-Scopes`` for classic tokens ("lists the scopes
  your token has authorized", https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps,
  accessed 2026-10-09). The classic ``repo`` scope "Grants full access to public and private
  repositories including read and write access to code" (same page), and that page lists no
  read-only private-repository scope, so a classic token that can read a private repo can
  also write to it. GitHub's first-party docs do not state whether a fine-grained token's
  response carries ``X-OAuth-Scopes``; third-party reports say it does not. A missing header
  therefore means "scope unknown", never "read-only".

Nothing here touches the network, credentials, approval or publishing.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .policy import ValidationError

VISIBILITIES = frozenset({"public", "private"})
PRIVATE = "private"
REDACTED_PRIVATE = "[REDACTED:private_source]"
SCOPE_HEADER = "X-OAuth-Scopes"
SCOPES_DOC = "https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps"
FINE_GRAINED_DOC = ("https://docs.github.com/en/rest/authentication/"
                    "permissions-required-for-fine-grained-personal-access-tokens")
# Read-only fine-grained repository permissions each GitHub reader needs, per FINE_GRAINED_DOC
# (accessed 2026-10-09): GET /repos/{owner}/{repo} and /tags -> Metadata (read);
# /releases -> Contents (read); /pulls -> Pull requests (read); /issues -> Issues (read).
FINE_GRAINED_READ = {
    "github_releases": ("metadata", "contents"),
    "github_tags": ("metadata",),
    "github_pulls": ("metadata", "pull_requests"),
    "github_issues": ("metadata", "issues"),
}
_REPO_PART = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SCOPE = re.compile(r"^[a-z0-9:_-]{1,64}$")
_URL = re.compile(r"https?://[^\s<>\"'`)\]}]+", re.I)


def is_private_signal(signal: Mapping) -> bool:
    """True for any marker other than an explicit public one (a tampered scan fails closed)."""
    if signal.get("private") is True:
        return True
    visibility = signal.get("visibility", "public")
    return visibility != "public"


def is_private_evidence(item: object) -> bool:
    if not isinstance(item, Mapping):
        return False
    visibility = item.get("source_visibility", "public")
    return item.get("private") is True or visibility != "public"


def repo_of_url(url: object) -> str | None:
    """``owner/repo`` (casefolded) for a GitHub web, API or raw URL, else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None
    segments = [unquote(s) for s in parts.path.split("/") if s]
    if host in ("github.com", "www.github.com", "raw.githubusercontent.com"):
        pair = segments[:2]
    elif host == "api.github.com" and segments[:1] == ["repos"]:
        pair = segments[1:3]
    else:
        return None
    if len(pair) != 2 or not all(_REPO_PART.fullmatch(p) for p in pair) or pair[1] in (".", ".."):
        return None
    owner, name = pair
    name = name[:-4] if name.casefold().endswith(".git") else name
    return f"{owner}/{name}".casefold()


def url_in_private_repo(url: object, repos: Iterable[str]) -> bool:
    repo = repo_of_url(url)
    return repo is not None and repo in {r.casefold() for r in repos}


def private_repos_from_config(config: Mapping) -> frozenset[str]:
    """Repos the owner declared ``visibility: private`` (enabled or not)."""
    sources = config.get("sources") if isinstance(config, Mapping) else None
    return frozenset(
        s["repo"].casefold() for s in sources or []
        if isinstance(s, Mapping) and s.get("visibility") == PRIVATE and isinstance(s.get("repo"), str))


def private_evidence_repos(campaign: Mapping) -> frozenset[str]:
    """Repos behind any private evidence record of a campaign."""
    evidence = campaign.get("evidence") if isinstance(campaign, Mapping) else None
    repos = set()
    for item in evidence if isinstance(evidence, list) else []:
        if is_private_evidence(item):
            repo = repo_of_url(item.get("url"))
            if repo:
                repos.add(repo)
    return frozenset(repos)


def refuse_private_link(url: object, repos: Iterable[str]) -> None:
    if url_in_private_repo(url, repos):
        raise ValidationError("Destination is inside a private source repository; private repository "
                              "URLs are never public links")


def links_in(text: object) -> list[str]:
    return _URL.findall(text) if isinstance(text, str) else []


def redact_private_evidence(items: object) -> object:
    """Copy of an evidence list with private records' ``excerpt`` and ``url`` replaced."""
    if not isinstance(items, list):
        return items
    out: list = []
    for item in items:
        if is_private_evidence(item):
            item = dict(item)
            for key in ("excerpt", "url"):
                if key in item:
                    item[key] = REDACTED_PRIVATE
            item["redacted"] = "private_source"
        out.append(item)
    return out


def token_scope_report(header: object, *, authenticated: bool) -> dict:
    """Classify a classic-token ``X-OAuth-Scopes`` value; scope names only, never the token."""
    report: dict = {"header": SCOPE_HEADER, "reported": False, "scopes": [], "write_capable": [],
                    "warnings": [], "source": SCOPES_DOC}
    if not authenticated:
        return report
    if not isinstance(header, str):
        report["warnings"].append(
            "token_scope_unknown: no X-OAuth-Scopes header. GitHub documents that header for classic "
            "tokens only; a fine-grained token does not expose its permissions this way, so read-only "
            "access cannot be verified. Use a fine-grained token limited to the selected repositories "
            "with read-only Metadata, Contents, Issues and Pull requests.")
        return report
    scopes = [s.strip().casefold() for s in header.split(",") if s.strip()]
    scopes = [s for s in scopes if _SCOPE.fullmatch(s)][:50]
    report["reported"], report["scopes"] = True, scopes
    # Only "read:*" scopes are read-only; everything else (repo, public_repo, write:*, admin:*,
    # workflow, delete_repo, gist, user, ...) can change something.
    report["write_capable"] = [s for s in scopes if not s.startswith("read:")]
    if report["write_capable"]:
        report["warnings"].append(
            "token_has_write_scopes: this classic token reports scopes that can write ("
            + ", ".join(report["write_capable"]) + "). Classic 'repo' grants read AND write to every "
            "private repository you can access. Replace it with a fine-grained token limited to the "
            "selected repositories with read-only permissions.")
    return report


def repository_permission_note(meta: object) -> str | None:
    """Note when repo metadata reports push/maintain/admin. GitHub's REST reference lists the
    ``permissions`` booleans without defining whether they reflect the token or the user's role
    (https://docs.github.com/en/rest/repos/repos#get-a-repository, accessed 2026-10-09)."""
    perms = meta.get("permissions") if isinstance(meta, Mapping) else None
    if isinstance(perms, Mapping) and any(perms.get(k) is True for k in ("push", "maintain", "admin")):
        return "repository_permissions_report_write_access"
    return None
