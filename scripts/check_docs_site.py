#!/usr/bin/env python3
"""Check the built documentation website (website/dist) for search-engine and privacy defects.

Every HTML page must have a unique <title>, a meta description of 50-160 characters, a canonical URL equal to its own
address, one <h1>, an html lang attribute, an Open Graph image, parseable JSON-LD and alt text on every image. Every
indexable page must be in the sitemap. robots.txt, llms.txt and llms-full.txt must exist. No file may contain a
link to a private-only document (the public docs may name such a file in plain text, as the public repository
does, but never link to it). Standard library only; reads files, makes no network request.

    python scripts/check_docs_site.py [website/dist]
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_URL = "https://mskazemi.com/reachweaver"
PRIVATE_MARKERS = ("SECURITY-REVIEW-PROPOSAL", "THREAT-MODEL", "docs/internal/", "COMPETITIVE-REVIEW",
                   "ECOSYSTEM-AUDIT", "PUBLIC-RELEASE-CHECKLIST", "ANTHROPIC-DIRECTORY", "reachweaver-private")


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.lang = ""
        self.h1 = 0
        self.images_without_alt = 0
        self.json_ld: list[str] = []
        self._in_title = False
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang") or ""
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.meta.setdefault(key, a.get("content") or "")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href") or ""
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img" and a.get("alt") is None:
            self.images_without_alt += 1
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self.json_ld.append("")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script":
            self._in_ld = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self.json_ld[-1] += data


def check(dist: Path) -> list[str]:
    problems: list[str] = []
    titles: dict[str, str] = {}
    sitemap = "".join(p.read_text(encoding="utf-8") for p in dist.glob("sitemap-[0-9]*.xml"))
    sitemap_urls = set(re.findall(r"<loc>([^<]+)</loc>", sitemap))
    if not sitemap_urls:
        problems.append("sitemap: no URLs")
    for name in ("robots.txt", "llms.txt", "llms-full.txt", "og.png", "favicon.png", "sitemap.xml"):
        if not (dist / name).is_file():
            problems.append(f"missing {name}")

    pages = sorted(dist.rglob("*.html"))
    for path in pages:
        rel = path.relative_to(dist).as_posix()
        page = Page()
        page.feed(path.read_text(encoding="utf-8"))
        url = f"{SITE_URL}/" + (rel[:-len("index.html")] if rel.endswith("index.html") else rel)
        is_404 = rel == "404.html"
        where = f"{rel}:"
        if not page.title.strip():
            problems.append(f"{where} no <title>")
        elif page.title in titles:
            problems.append(f"{where} title duplicates {titles[page.title]}: {page.title!r}")
        titles.setdefault(page.title, rel)
        description = page.meta.get("description", "")
        if not 50 <= len(description) <= 160:
            problems.append(f"{where} description is {len(description)} characters")
        if not is_404:
            if page.canonical != url:
                problems.append(f"{where} canonical {page.canonical!r} is not {url!r}")
            if url not in sitemap_urls:
                problems.append(f"{where} not in the sitemap")
        if page.h1 != 1:
            problems.append(f"{where} {page.h1} <h1> elements")
        if not page.lang:
            problems.append(f"{where} no html lang")
        if not page.meta.get("og:image"):
            problems.append(f"{where} no og:image")
        if page.images_without_alt:
            problems.append(f"{where} {page.images_without_alt} images without alt")
        if not is_404 and not page.json_ld:
            problems.append(f"{where} no JSON-LD")
        for block in page.json_ld:
            try:
                json.loads(block)
            except ValueError as exc:
                problems.append(f"{where} JSON-LD does not parse: {exc}")

    for path in dist.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".txt", ".xml", ".json", ".js"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for target in re.findall(r"(?:href|src)=\"([^\"]+)\"|\]\(([^)\s]+)\)", text):
                link = target[0] or target[1]
                for marker in PRIVATE_MARKERS:
                    if marker in link:
                        problems.append(f"{path.relative_to(dist)}: links to private {link!r}")
    print(f"checked {len(pages)} HTML pages, {len(sitemap_urls)} sitemap URLs: {len(problems)} problems")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dist = Path(args[0]) if args else ROOT / "website" / "dist"
    if not dist.is_dir():
        print(f"{dist} does not exist; build the site first", file=sys.stderr)
        return 2
    problems = check(dist)
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
