#!/usr/bin/env python3
"""Generate the documentation website's content from the repository's public Markdown.

The Markdown in docs/ and the other public guides stays the single source of truth. This script copies each page into
website/src/content/docs/ with front matter (title, description, sidebar label, edit link, last-updated date), rewrites
repository links to site routes or GitHub URLs, splits CHANGELOG.md into one page per release, builds the command
reference from the real argparse parser, and writes llms.txt, llms-full.txt and the sidebar for website/astro.config.mjs.

Only files tracked by the public git index are read (``git ls-files``), and private-only names are refused a second
time below, so a private document in a dual-git working tree never reaches the site. Standard library only, no network.

    python scripts/build_docs_site.py            # write the generated content
    python scripts/build_docs_site.py --check    # exit 1 when a mapped source is missing or a page has no description
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"
CONTENT = WEBSITE / "src" / "content" / "docs"
GENERATED = WEBSITE / "src" / "generated"
PUBLIC = WEBSITE / "public"

REPO = "MSKazemi/reachweaver"
GITHUB = f"https://github.com/{REPO}"
SITE = "https://mskazemi.com"  # the GitHub Pages custom domain; mskazemi.github.io 301-redirects here
BASE = "/reachweaver"
SITE_URL = f"{SITE}{BASE}"
# Hand-written pages that live in the content folder and are never overwritten.
HAND_WRITTEN = ("index.mdx", "faq.mdx", "404.md")
DESCRIPTION_MAX = 160

# Names that exist only in the private superset. They are never tracked by the public index, so this list is a
# second barrier for a run outside a git checkout or a mistake in the dual-git filters.
PRIVATE_MARKERS = ("/internal/", "/decisions/", "SECURITY-REVIEW-PROPOSAL", "THREAT-MODEL", "/ADR-",
                   "COMPETITIVE-REVIEW", "ECOSYSTEM-AUDIT", "PUBLIC-RELEASE-CHECKLIST", "ANTHROPIC-DIRECTORY",
                   "LEGAL-AND-LIABILITY", ".claude/plans/", ".claude/memory/", ".git-private/", ".dualgit/")

# (folder, sidebar label, [(source, slug or None, sidebar label or None)]) in reading order. A docs/ page missing
# from this table still gets a page, in the "More guides" group, so a new document is never silently dropped.
SKILL = ".claude/skills/reachweaver"
SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str | None, str | None], ...]], ...] = (
    ("start", "Get started", (
        ("README.md", "overview", "Overview"),
        ("docs/START-HERE.md", None, "Start here"),
        ("docs/QUICKSTART.md", None, "Quickstart"),
        ("docs/RECOMMENDED-SETUP.md", None, "Recommended setup"),
        ("docs/INSTALL.md", None, "Install"),
        ("docs/CLAUDE-CODE.md", None, "Use with Claude Code"),
        ("docs/EASY-SHARING.md", None, "Easy sharing"),
        ("docs/BROWSER-SHARE-COMPOSER.md", None, "Browser share composer"),
        ("docs/FEATURES.md", None, "All features"),
        ("docs/GLOSSARY.md", None, "Glossary"),
        ("docs/TROUBLESHOOTING.md", None, "Troubleshooting"),
        ("docs/UPGRADE.md", None, "Upgrade"),
        ("docs/MOVING.md", "workspace", "Your workspace folder"),
        ("docs/README.md", "documentation-map", "Documentation map"),
    )),
    ("publishing", "Accounts and publishing", (
        ("docs/FREE-PUBLISHING.md", None, "Free automatic publishing"),
        ("docs/SECURITY-CHECKLIST.md", None, "Security checklist"),
        ("docs/ACCOUNT-ONBOARDING.md", None, "Account onboarding"),
        ("docs/ACCOUNT-ADVISOR.md", None, "Account advisor"),
        ("docs/PERSONAS-AND-ACCOUNT-KIT.md", None, "Personas and account kit"),
        ("docs/FIRST-LIVE-PILOT.md", None, "First live pilot"),
        ("docs/BLUESKY-PILOT.md", None, "Bluesky one-post pilot"),
        ("docs/FIELD-PILOT-PROTOCOL.md", None, "Field pilot protocol"),
        ("docs/PLATFORM-MATRIX.md", None, "Platform matrix"),
        ("docs/MANUAL-EXPORT.md", None, "Manual export"),
        ("docs/REPEAT-GUARD.md", None, "Repeat guard"),
        ("docs/REVIEW-PACKET.md", None, "Review packets"),
        ("docs/JOURNAL.md", None, "Posting journal"),
        ("docs/WORKER-OPERATIONS.md", None, "Scheduled delivery"),
    )),
    ("platforms", "Platforms", (
        ("docs/PLATFORMS.md", "registry", "Capability registry"),
        ("docs/MASTODON.md", None, "Mastodon"),
        ("docs/LINKEDIN.md", None, "LinkedIn"),
        ("docs/THREADS.md", None, "Threads"),
        ("docs/FACEBOOK.md", None, "Facebook Page"),
        ("docs/INSTAGRAM.md", None, "Instagram"),
        ("docs/INSTAGRAM-SETUP.md", None, "Instagram setup"),
        ("docs/INSTAGRAM-PILOT.md", None, "Instagram pilot"),
        ("docs/YOUTUBE.md", None, "YouTube"),
        ("docs/TIKTOK.md", None, "TikTok"),
        ("docs/CHINA.md", None, "Chinese platforms"),
        ("docs/OPEN-SOCIAL-WEB.md", None, "Open social web"),
        ("docs/POSTIZ.md", None, "Postiz backend"),
        ("docs/POSTIZ-SCOPE-DETECTION.md", None, "Postiz scope detection"),
    )),
    ("create", "Plan and create", (
        ("docs/EDITORIAL-SYSTEM.md", None, "Editorial planning"),
        ("docs/CALENDAR-PLANNER.md", None, "Calendar planner"),
        ("docs/CAMPAIGN-PREVIEW.md", None, "Campaign preview"),
        ("docs/CLAIM-GROUNDING.md", None, "Claim grounding"),
        ("docs/CREATIVE-PREFLIGHT.md", None, "Creative preflight"),
        ("docs/VOICE-CHECK.md", None, "Voice check"),
        ("docs/AI-CONTENT-LABELS.md", None, "AI content labels"),
        ("docs/MEDIA-ASSETS.md", None, "Media assets"),
        ("docs/RELEASE-TO-DRAFT.md", None, "Release to draft"),
        ("docs/RELEASE-PLAYBOOK.md", None, "Release playbook"),
        ("docs/SOURCES.md", None, "Sources and topics"),
        ("docs/CREATOR-AMPLIFICATION.md", None, "Creator amplification"),
        ("docs/DISTRIBUTION-GOVERNANCE.md", None, "Distribution governance"),
        ("docs/DASHBOARD.md", None, "Local dashboard"),
        ("docs/MCP-SERVER.md", None, "Local MCP server"),
    )),
    ("grow", "Measure and grow", (
        ("docs/INSIGHTS.md", None, "What works for you"),
        ("docs/NEXT-ACTIONS.md", None, "Next actions"),
        ("docs/FOLLOWUPS.md", None, "Milestone follow-ups"),
        ("docs/ENGAGEMENT.md", None, "Engagement triage"),
        ("docs/ENGAGE-SCOUT.md", None, "Engagement scout"),
        ("docs/PROFILE-AND-POST-AUDITS.md", None, "Profile and post audits"),
        ("docs/PROFILE-METRICS-CSV.md", None, "Profile metrics CSV"),
        ("docs/PEER-BENCHMARK.md", None, "Peer benchmark"),
        ("docs/PLATFORM-RISK.md", None, "Platform risk"),
        ("docs/ATTRIBUTION.md", None, "Links and attribution"),
        ("docs/TIMING-EVALUATION.md", None, "Timing evaluation"),
        ("docs/GROWTH-EXPERIMENTS.md", None, "Growth experiments"),
        ("docs/WEEKLY-REPORT-EXAMPLE.md", None, "Weekly report example"),
        ("docs/FOLLOWER-POLLING-DESIGN.md", None, "Follower polling design"),
        ("docs/AI-SEARCH-VISIBILITY.md", None, "AI search visibility"),
        ("docs/SEO-INTEGRATION.md", None, "SEO integration"),
        ("docs/DISCOVERY-OPTIMIZATION.md", None, "Discovery optimization"),
        ("docs/GITHUB-DISCOVERY.md", None, "GitHub discovery"),
        ("docs/OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md", "open-source-growth-playbook", "Open-source growth playbook"),
        ("docs/DEV-AUTHORITY-PLAYBOOK-2026-10-08.md", None, "DEV Community playbook"),
    )),
    ("research", "Research", (
        ("docs/GLOBAL-DISTRIBUTION-2026-10-08.md", None, "Global distribution"),
        ("docs/REGIONAL-CHANNELS-2026-10-14.md", None, "Regional channels"),
        ("docs/REGIONAL-API-FEASIBILITY-2026-10-15.md", None, "Regional API feasibility"),
        ("docs/COMMUNITY-AND-CREATOR-PLATFORMS-2026-10-15.md", None, "Community and creator platforms"),
        ("docs/TEN-MORE-SOCIAL-NETWORKS-2026-10-17.md", None, "Ten more social networks"),
        ("docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md", None, "Algorithms and scheduling"),
        ("docs/PLATFORM-RESEARCH.md", None, "Platform research"),
        ("docs/UPSTREAM-RESEARCH.md", None, "Open-source landscape"),
        ("docs/BROWSER-ASSIST-DECISION.md", None, "Browser-assist decision"),
        ("docs/INCREMENTAL-DELIVERY-GROUPS-2026-10-08.md", None, "Incremental delivery groups"),
    )),
    ("claude-code", "Claude Code skill", (
        (f"{SKILL}/SKILL.md", "skill", "The skill (SKILL.md)"),
        ("plugin/README.md", "plugin", "Claude Code plugin"),
        ("docs/PLUGIN-EVALS.md", None, "Plugin behaviour evals"),
        ("evals/README.md", "skill-evals", "Skill trigger evals"),
    )),
    ("reference", "Reference", (
        ("docs/ARCHITECTURE.md", None, "Architecture"),
        ("docs/SCHEMAS.md", None, "Schemas"),
        ("docs/PACKAGING.md", None, "Python package"),
        ("docs/PRODUCT.md", None, "Product requirements"),
    )),
    ("project", "Project", (
        ("docs/ROADMAP.md", None, "Roadmap"),
        ("docs/IMPLEMENTATION-ROADMAP.md", None, "Outcome roadmap"),
        ("CONTRIBUTING.md", None, "Contributing"),
        ("docs/BRANCH-LIFECYCLE.md", None, "Branch lifecycle"),
        ("docs/SECURITY.md", "security", "Security and trust"),
        ("SECURITY.md", "report-a-vulnerability", "Report a vulnerability"),
        ("docs/PRIVACY.md", None, "Privacy notice"),
        ("docs/PRIVACY-LIFECYCLE.md", None, "Data lifecycle"),
        ("DISCLAIMER.md", None, "Disclaimer"),
        ("assets/brand/README.md", "brand", "Brand"),
    )),
)
# Skill references and templates get their own sidebar groups under claude-code/.
SKILL_GROUPS = ((f"{SKILL}/references/", "claude-code/references", "Skill references"),
                (f"{SKILL}/assets/", "claude-code/templates", "Skill templates"))

# Search-result descriptions for the pages people land on first. Every other page uses its opening paragraph.
DESCRIPTIONS = {
    "README.md": "ReachWeaver is an open-source Claude Code skill that drafts and checks social posts and sends one "
                 "only after you approve the exact text. Dry run by default.",
    "docs/QUICKSTART.md": "Try ReachWeaver in ten minutes with no account: install, draft a post, check it and "
                          "preview exactly what would be sent. Nothing can be published.",
    "docs/INSTALL.md": "Install ReachWeaver as a Claude Code skill, a Claude Code plugin or a Python package "
                       "(pip install reachweaver), and pick the route that fits you.",
    "docs/START-HERE.md": "ReachWeaver in plain words: the ideas behind it and the journey of one social post "
                          "from idea to draft, check, approval and a measured result.",
    f"{SKILL}/assets/brand-card.md": "A fill-in brand card for one identity: accounts, audience, goal, canonical "
                                     "links, verified proof, voice examples, do-not-claim list and review authority.",
    f"{SKILL}/assets/experiment.md": "A fill-in campaign experiment record: audience, evidence, baseline, hypothesis, "
                                     "creative A and B, metrics, snapshot dates, confounders and observed results.",
    "docs/README.md": "Every ReachWeaver guide, sorted by goal: get started, connect an account, plan and write "
                      "posts, measure results, and run the project safely.",
}


@dataclass
class Page:
    source: str          # repo-relative path, or "" for a generated page
    route: str           # site route without base or slashes, e.g. "start/quickstart"
    title: str
    label: str
    body: str
    description: str = ""
    order: int = 0
    updated: str = ""    # ISO 8601 commit date of the source
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{SITE_URL}/{self.route}/"


def public_files() -> list[str]:
    """Paths in the public git index; refuses private names a second time."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        files = [p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        files = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.md") if ".git" not in p.parts]
    return sorted(p for p in files if not is_private(p))


def is_private(path: str) -> bool:
    probe = "/" + path
    return any(marker in probe for marker in PRIVATE_MARKERS)


def last_commit_date(path: str) -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", path], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        out = ""
    return out


def slugify(stem: str) -> str:
    stem = re.sub(r"-20\d\d-\d\d-\d\d$", "", stem)  # dated research notes keep the date in the title, not the URL
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def split_title(text: str) -> tuple[str, str]:
    """(H1 text, body without the H1). Starlight renders the title itself."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return line[2:].strip(), "\n".join(lines[:index] + lines[index + 1:]).lstrip("\n")
        if line.strip():
            break
    return "", text


def clean_title(title: str) -> str:
    title = title.replace("`", "")
    title = re.sub(r"\s*\([Ii]ssues? #\d+(?: slices?)?\)", "", title)             # "(issue #43)", "(issue #18 slices)"
    title = re.sub(r"\((?:[Ii]ssue) #\d+,\s*", "(", title)                         # "(issue #21, owner-approved"
    title = re.sub(r"\s*[—-]\s*[Ii]ssue #\d+\b", "", title)                         # "— Issue #11"
    title = re.sub(r"\s*\(issue #\d+ slices?\)", "", title)
    return re.sub(r"\s{2,}", " ", title).strip()


MD_LINK = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?((?:\s+\"[^\"]*\")?)\s*\)")
REF_DEF = re.compile(r"^(\s{0,3}\[[^\]]+\]:\s*)(\S+)(.*)$", re.M)
HTML_ATTR = re.compile(r"\b(src|href)=\"([^\"]+)\"")


def plain_text(markdown: str) -> str:
    text = MD_LINK.sub(lambda m: m.group(2), markdown)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*`]+", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)  # emphasis underscores only; snake_case names stay
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def describe(body: str, fallback: str) -> str:
    candidates: list[str] = []
    for block in re.split(r"\n\s*\n", strip_code(body)):
        block = block.strip()
        if not block or block[0] in "#|<-" or block.startswith(("```", "~~~", "* ", "1.", "!", "[")):
            continue
        if len(plain_text(block)) < 50:  # a one-line label is not a description
            continue
        candidates.append(block)
        break
    text = plain_text(candidates[0]) if candidates else fallback
    text = re.sub(r"^(Status|Note|Summary|Scope|Purpose|Date|Updated|Version)\s*:\s*", "", text)
    if not (candidates and candidates[0].lstrip("> *").startswith("`")):  # a command name keeps its case
        text = text[:1].upper() + text[1:]
    if len(text) <= DESCRIPTION_MAX:
        return text
    cut = text[:DESCRIPTION_MAX - 1]
    sentence = cut.rfind(". ")
    if sentence > 80:
        return cut[:sentence + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:—-") + "…"


def strip_code(text: str) -> str:
    return re.sub(r"^(```|~~~).*?^\1\s*$", "", text, flags=re.M | re.S)


class Linker:
    """Rewrite repository links: a page we publish becomes a site route; any other public file a GitHub URL;
    a private or missing target loses its link and keeps its text."""

    def __init__(self, routes: dict[str, str], files: list[str]):
        self.routes = routes
        self.files = set(files)
        self.dirs = {posixpath.dirname(f) for f in files}
        while True:
            parents = {posixpath.dirname(d) for d in self.dirs} - self.dirs
            if not parents or parents == {""}:
                break
            self.dirs |= parents
        self.dropped: list[tuple[str, str]] = []

    def resolve(self, target: str, source: str, image: bool = False) -> str | None:
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#") or target.startswith("//"):
            return target
        path, _, anchor = target.partition("#")
        path = unquote(path)
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(source), path)) if path else source
        anchor = f"#{anchor}" if anchor else ""
        if joined.startswith("..") or joined.startswith("/"):
            self.dropped.append((source, target))
            return None
        if joined in self.routes and not image:
            return f"{BASE}/{self.routes[joined]}/{anchor}"
        if joined in self.files:
            if image:
                return f"https://raw.githubusercontent.com/{REPO}/main/{joined}"
            return f"{GITHUB}/blob/main/{joined}{anchor}"
        if joined.rstrip("/") in self.dirs:
            return f"{GITHUB}/tree/main/{joined.rstrip('/')}"
        self.dropped.append((source, target))
        return None

    def rewrite(self, text: str, source: str) -> str:
        parts = re.split(r"(^(?:```|~~~).*?^(?:```|~~~)\s*$)", text, flags=re.M | re.S)
        for index in range(0, len(parts), 2):  # odd indexes are fenced code; leave them alone
            parts[index] = self._rewrite_prose(parts[index], source)
        return "".join(parts)

    def _rewrite_prose(self, text: str, source: str) -> str:
        def link(match: re.Match) -> str:
            bang, label, target, title = match.groups()
            url = self.resolve(target, source, image=bool(bang))
            if url is None:
                return label if not bang else ""
            return f"{bang}[{label}]({url}{title})"

        def ref(match: re.Match) -> str:
            url = self.resolve(match.group(2), source)
            return f"{match.group(1)}{url or GITHUB}{match.group(3)}"

        def attr(match: re.Match) -> str:
            url = self.resolve(match.group(2), source, image=match.group(1) == "src")
            return f'{match.group(1)}="{url or GITHUB}"'

        text = MD_LINK.sub(link, text)
        text = REF_DEF.sub(ref, text)
        return HTML_ATTR.sub(attr, text)


def front_matter(page: Page) -> str:
    data: dict = {"title": page.title, "description": page.description,
                  "sidebar": {"label": page.label, "order": page.order}}
    if page.source:
        data["editUrl"] = f"{GITHUB}/edit/main/{page.source}"
    data.update(page.extra)
    # JSON is valid YAML, and it escapes every quote, colon and leading symbol a title or description may carry.
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in data.items()]
    if page.updated:
        lines.append(f"lastUpdated: {page.updated[:10]}")  # a bare YAML date, which the schema reads as a Date
    lines += ["---", ""]
    return "\n".join(lines)


def doc_pages(files: list[str]) -> tuple[list[Page], list[dict]]:
    """Pages for every mapped source plus any unmapped docs/*.md page, and the sidebar groups."""
    pages: list[Page] = []
    sidebar: list[dict] = []
    mapped: set[str] = set()
    available = set(files)
    for folder, group_label, entries in SECTIONS:
        items: list = []
        for order, (source, slug, label) in enumerate(entries):
            if source not in available:
                continue
            mapped.add(source)
            route = f"{folder}/{slug or slugify(Path(source).stem)}"
            pages.append(Page(source, route, "", label or "", "", order=order))
            items.append({"slug": route})
        # Groups after the first start collapsed; Starlight still opens the group holding the current page.
        group: dict = {"label": group_label, "collapsed": folder != "start", "items": items}
        if folder == "claude-code":
            for prefix, route_prefix, sub_label in SKILL_GROUPS:
                sub = [f for f in files if f.startswith(prefix) and f.endswith(".md") and "/" not in f[len(prefix):]]
                sub_items = []
                for order, source in enumerate(sub):
                    mapped.add(source)
                    route = f"{route_prefix}/{slugify(Path(source).stem)}"
                    pages.append(Page(source, route, "", "", "", order=order))
                    sub_items.append({"slug": route})
                if sub_items:
                    items.append({"label": sub_label, "collapsed": True, "items": sub_items})
        sidebar.append(group)
    extra = [f for f in files if f.startswith("docs/") and f.endswith(".md") and f not in mapped
             and f.count("/") == 1]
    if extra:
        items = []
        for order, source in enumerate(extra):
            route = f"guides/{slugify(Path(source).stem)}"
            pages.append(Page(source, route, "", "", "", order=order))
            items.append({"slug": route})
        sidebar.insert(len(sidebar) - 3, {"label": "More guides", "items": items})
    return pages, sidebar


CHANGELOG_RELEASE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*-\s*(?P<date>\d{4}-\d{2}-\d{2}))?", re.M)


def changelog_pages(text: str) -> tuple[list[Page], dict]:
    """One page per release, newest first, and an index page; [Unreleased] stays only when it has content."""
    body = strip_front_matter(text)
    _, body = split_title(body)
    matches = list(CHANGELOG_RELEASE.finditer(body))
    intro = body[:matches[0].start()].strip() if matches else body
    releases: list[Page] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[match.end():end].strip()
        version, date = match.group("version"), match.group("date") or ""
        if not section:
            continue
        slug = "unreleased" if version.lower() == "unreleased" else "v" + slugify(version)
        title = "Unreleased changes" if slug == "unreleased" else f"ReachWeaver {version} release notes"
        label = "Unreleased" if slug == "unreleased" else f"{version}" + (f" ({date})" if date else "")
        lead = f"Released on {date}." if date else "Changes on main that are not in a release yet."
        description = describe(section, "") or lead
        description = f"{'What changed in ReachWeaver ' + version if slug != 'unreleased' else 'Unreleased ReachWeaver changes'}" \
                      f"{' (' + date + ')' if date else ''}: {description}"
        releases.append(Page("CHANGELOG.md", f"project/changelog/{slug}", title, label,
                             f"{lead}\n\n{section}\n", description=shorten(description), order=index + 1,
                             updated=f"{date}T00:00:00Z" if date else ""))
    rows = "\n".join(f"| [{p.label.split(' (')[0]}]({BASE}/{p.route}/) | {p.updated[:10] or 'not released'} |"
                     for p in releases)
    index_body = (f"{intro}\n\nEvery release, newest first. Each page lists what changed and how to upgrade.\n\n"
                  f"| Release | Date |\n|---|---|\n{rows}\n")
    index = Page("CHANGELOG.md", "project/changelog", "Changelog",
                 "Changelog", index_body,
                 description="Every ReachWeaver release and what changed in it, newest first, with upgrade notes "
                             "for each version.", order=0)
    group = {"label": "Release notes", "collapsed": True,
             "items": [{"slug": index.route}] + [{"slug": p.route} for p in releases]}
    return [index] + releases, group


def shorten(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= DESCRIPTION_MAX:
        return text
    cut = text[:DESCRIPTION_MAX - 1]
    return cut[:cut.rfind(" ")].rstrip(",;:—-") + "…"


# ---------------------------------------------------------------- command reference
def cli_pages() -> tuple[list[Page], dict]:
    os.environ["COLUMNS"] = "96"  # stable usage wrapping, whatever terminal runs the build
    sys.path.insert(0, str(ROOT))
    from reachweaver import __version__ as _version  # noqa: F401  (import check: the package is importable)
    from reachweaver.cli_help import GROUPS, summary
    from reachweaver.cli_parser import _subparsers_action, make_parser

    parser = make_parser()
    commands = _subparsers_action(parser).choices
    helps = {a.dest: a.help for a in _subparsers_action(parser)._choices_actions}
    groups = [(title, [c for c in names if c in commands]) for title, names in GROUPS]
    grouped = {c for _, names in groups for c in names}
    leftover = sorted(c for c in commands if c not in grouped)
    if leftover:
        groups.append(("Other commands", leftover))

    pages: list[Page] = []
    index_rows: list[str] = []
    for order, (title, names) in enumerate(groups, start=1):
        slug = slugify(re.sub(r"\(.*?\)", "", title))
        route = f"reference/cli/{slug}"
        sections = [_command_section(name, commands[name], helps.get(name)) for name in names]
        body = (f"{len(names)} commands. Every command runs on your own computer; nothing is sent to a social "
                f"network unless a publishing command is run by the owner with its live switches.\n\n"
                + "\n".join(sections))
        clean = re.sub(r"\s*\(.*?\)", "", title)
        description = shorten(f"ReachWeaver command reference, {clean.lower()}: "
                              + ", ".join(names[:8]) + (" and more" if len(names) > 8 else "")
                              + ". Usage and every option.")
        pages.append(Page("", route, f"CLI reference: {clean}", clean, body, description=description, order=order))
        for name in names:
            index_rows.append(f"| [`{name}`]({BASE}/{route}/#reachweaver-{name}) | {_cell(summary(helps.get(name)) or '')} |")
    top_options = _options_table(parser)
    index_body = ("The `reachweaver` command line, generated from the program's own argument parser, so it always "
                  "matches the installed version. `reachweaver --help` shows the everyday commands, "
                  "`reachweaver --help all` lists every command by task and `reachweaver COMMAND --help` shows one.\n\n"
                  "Every command is a dry run or a local operation by default. Live publishing needs the owner's "
                  "approval of the exact text and the live switches; see the "
                  f"[security checklist]({BASE}/publishing/security-checklist/).\n\n"
                  f"## Global options\n\nPut these before the command name.\n\n{top_options}\n\n"
                  f"## All {len(commands)} commands\n\n| Command | What it does |\n|---|---|\n" + "\n".join(index_rows) + "\n")
    index = Page("", "reference/cli", "CLI reference", "All commands", index_body,
                 description=f"Complete reference for the reachweaver command line: all {len(commands)} commands "
                             "with usage and options, grouped by task.", order=0)
    group = {"label": "CLI reference", "collapsed": False,
             "items": [{"slug": index.route}] + [{"slug": p.route} for p in pages]}
    return [index] + pages, group


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _help_of(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    if not action.help or action.help == argparse.SUPPRESS:
        return ""
    try:
        return parser._get_formatter()._expand_help(action)
    except (KeyError, TypeError, ValueError):
        return action.help


def _options_table(parser: argparse.ArgumentParser) -> str:
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)) or action.help == argparse.SUPPRESS:
            continue
        if action.option_strings:
            metavar = ""
            if action.nargs != 0:
                metavar = " " + (action.metavar if isinstance(action.metavar, str) else action.dest.upper())
            name = ", ".join(f"`{o}{metavar}`" for o in action.option_strings)
        else:
            name = f"`{action.metavar or action.dest}`"
        choices = f" One of: {', '.join(f'`{c}`' for c in action.choices)}." if action.choices and not isinstance(
            action, argparse._SubParsersAction) and len(action.choices) <= 12 else ""
        rows.append(f"| {name} | {_cell(_help_of(parser, action))}{_cell(choices) if not choices else choices} |")
    if not rows:
        return "No options."
    return "| Option | Description |\n|---|---|\n" + "\n".join(rows)


def _command_section(name: str, parser: argparse.ArgumentParser, help_text: str | None, depth: int = 2) -> str:
    hashes = "#" * depth
    usage = parser.format_usage().replace("usage: ", "", 1).rstrip()
    parts = [f"{hashes} reachweaver {name}", ""]
    if help_text:
        parts += [_cell(help_text).replace("\\|", "|"), ""]
    if parser.description and parser.description != help_text:
        parts += [parser.description.strip(), ""]
    parts += ["```sh", usage, "```", "", _options_table(parser), ""]
    nested = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    for action in nested:
        nested_help = {a.dest: a.help for a in action._choices_actions}
        for sub_name, sub_parser in action.choices.items():
            parts.append(_command_section(f"{name} {sub_name}", sub_parser, nested_help.get(sub_name), depth + 1))
    return "\n".join(parts)


# ---------------------------------------------------------------- llms.txt
def llms_files(pages: list[Page], version: str) -> tuple[str, str]:
    by_section: dict[str, list[Page]] = {}
    for page in pages:
        if page.route.startswith("project/changelog/"):
            continue
        by_section.setdefault(page.route.split("/")[0], []).append(page)
    labels = {folder: label for folder, label, _ in SECTIONS} | {"guides": "More guides"}
    head = (
        "# ReachWeaver\n\n"
        "> ReachWeaver is an open-source (Apache-2.0) Claude Code skill and Python command line for evidence-backed, "
        "approval-first social media publishing. It plans, drafts, checks and previews posts for each network, and "
        "sends nothing until the owner approves the exact text; every command is a dry run by default.\n\n"
        f"Current version: {version}. Install: `pip install reachweaver` (Python 3.11+, no runtime dependencies) or "
        "install the Claude Code skill/plugin. Runs locally and keeps its records in a local SQLite database; it has "
        "no account or server of its own. Text publishing adapters exist for Bluesky, Mastodon, Telegram and LinkedIn "
        "personal profiles; other networks get a prepared package that the owner posts by hand. It never buys "
        "engagement, scrapes, or writes for Hacker News or Reddit, and it does not promise viral growth.\n\n"
        f"- Source: {GITHUB}\n- Documentation: {SITE_URL}/\n- Package: https://pypi.org/project/reachweaver/\n"
        f"- Full text of the documentation: {SITE_URL}/llms-full.txt\n"
    )
    sections = []
    order = [folder for folder, _, _ in SECTIONS] + ["guides"]
    for folder in sorted(by_section, key=lambda f: order.index(f) if f in order else len(order)):
        lines = [f"- [{p.title}]({p.url}): {p.description}" for p in by_section[folder]]
        sections.append(f"## {labels.get(folder, folder.title())}\n\n" + "\n".join(lines))
    short = head + "\n" + "\n\n".join(sections) + "\n"
    full_parts = [head]
    for page in pages:
        if page.route.startswith("project/changelog/"):
            continue
        full_parts.append(f"\n\n---\n\n# {page.title}\n\nSource: {page.url}\n\n{page.body.strip()}\n")
    return short, "".join(full_parts)


# ---------------------------------------------------------------- main
def build(check: bool = False) -> int:
    files = public_files()
    pages, sidebar = doc_pages(files)
    missing = [s for _, _, entries in SECTIONS for s, _, _ in entries if s not in set(files)]
    routes = {p.source: p.route for p in pages}
    changelog, changelog_group = changelog_pages((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    routes["CHANGELOG.md"] = "project/changelog"
    cli, cli_group = cli_pages()
    linker = Linker(routes, files)

    for page in pages:
        text = strip_front_matter((ROOT / page.source).read_text(encoding="utf-8"))
        title, body = split_title(text)
        if page.source == "README.md":
            title = "ReachWeaver: approval-first social publishing for Claude Code"
            body = re.sub(r"^<img [^>]*>\s*\n", "", body)
        elif page.source.endswith("SKILL.md"):
            title = "The ReachWeaver skill for Claude Code (SKILL.md)"
        page.title = clean_title(title) or page.label or Path(page.source).stem
        page.label = page.label or page.title.split(":")[0].split(" — ")[0]
        page.body = linker.rewrite(body, page.source)
        page.description = DESCRIPTIONS.get(page.source) or describe(page.body, page.title)
        page.updated = last_commit_date(page.source)
    for page in changelog:
        page.body = linker.rewrite(page.body, "CHANGELOG.md")
        if not page.updated:
            page.updated = last_commit_date("CHANGELOG.md")

    all_pages = pages + changelog + cli
    reference = next(g for g in sidebar if g["label"] == "Reference")
    reference["items"].insert(0, cli_group)
    project = next(g for g in sidebar if g["label"] == "Project")
    project["items"].insert(2, changelog_group)

    problems = [f"missing source: {m}" for m in missing]
    problems += [f"no description: {p.route}" for p in all_pages if not p.description]
    seen: dict[str, str] = {}
    for page in all_pages:
        if page.route in seen:
            problems.append(f"duplicate route {page.route}: {seen[page.route]} and {page.source}")
        seen[page.route] = page.source
    if check:
        for line in problems:
            print(line, file=sys.stderr)
        return 1 if problems else 0

    if CONTENT.exists():
        for child in CONTENT.iterdir():
            if child.name in HAND_WRITTEN:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for page in all_pages:
        target = CONTENT / f"{page.route}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(front_matter(page) + page.body.rstrip() + "\n", encoding="utf-8")

    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    GENERATED.mkdir(parents=True, exist_ok=True)
    meta = {"version": version.group(1) if version else "", "site": SITE, "base": BASE, "github": GITHUB,
            "pages": len(all_pages)}
    (GENERATED / "sidebar.json").write_text(json.dumps(sidebar, indent=2) + "\n", encoding="utf-8")
    (GENERATED / "site-meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    # The sitemap's <lastmod>: the last commit date of each page's source.
    lastmod = {p.route: p.updated[:10] for p in all_pages if p.updated}
    (GENERATED / "lastmod.json").write_text(json.dumps(lastmod, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    short, full = llms_files(all_pages, meta["version"])
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "llms.txt").write_text(short, encoding="utf-8")
    (PUBLIC / "llms-full.txt").write_text(full, encoding="utf-8")

    print(f"wrote {len(all_pages)} pages ({len(pages)} guides, {len(changelog)} changelog, {len(cli)} CLI) "
          f"to {CONTENT.relative_to(ROOT)}")
    if linker.dropped:
        print(f"{len(linker.dropped)} links to private or missing files were reduced to plain text")
    for line in problems:
        print(f"warning: {line}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Validate the mapping without writing anything")
    args = parser.parse_args(argv)
    return build(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
