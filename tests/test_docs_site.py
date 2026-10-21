"""The documentation website generator (scripts/build_docs_site.py), its output checker (scripts/check_docs_site.py)
and the Pages workflow. Offline and stdlib only: Node and Astro are not needed, nothing is written into the checkout.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


site = _load("build_docs_site")
checker = _load("check_docs_site")
WORKFLOW = (ROOT / ".github" / "workflows" / "docs-site.yml").read_text(encoding="utf-8")


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.files = site.public_files()
        self.pages, self.sidebar = site.doc_pages(self.files)

    def test_check_mode_finds_no_problem(self):
        self.assertEqual(site.build(check=True), 0)

    def test_every_public_doc_gets_a_page(self):
        sources = {page.source for page in self.pages}
        docs = [f for f in self.files if f.startswith("docs/") and f.endswith(".md") and f.count("/") == 1]
        self.assertTrue(docs)
        self.assertEqual([d for d in docs if d not in sources], [])

    def test_no_private_source(self):
        for page in self.pages:
            self.assertFalse(site.is_private(page.source), page.source)
        for path in ("docs/internal/x.md", "docs/SECURITY-REVIEW-PROPOSAL-1.md", "docs/THREAT-MODEL.md",
                     "docs/ADR-0001-X.md", ".claude/plans/p.md", "docs/decisions/d.md"):
            self.assertTrue(site.is_private(path), path)

    def test_routes_are_unique_lowercase_and_dateless(self):
        routes = [page.route for page in self.pages]
        self.assertEqual(len(routes), len(set(routes)))
        for route in routes:
            self.assertRegex(route, r"^[a-z0-9-]+(/[a-z0-9-]+)+$")
            self.assertNotRegex(route, r"20\d\d-\d\d-\d\d")

    def test_sidebar_lists_every_page(self):
        listed: set[str] = set()

        def walk(items):
            for item in items:
                if "slug" in item:
                    listed.add(item["slug"])
                walk(item.get("items", []))

        walk(self.sidebar)
        self.assertEqual({p.route for p in self.pages} - listed, set())


class TransformTest(unittest.TestCase):
    def setUp(self):
        self.linker = site.Linker({"docs/QUICKSTART.md": "start/quickstart", "README.md": "start/overview"},
                                  ["docs/QUICKSTART.md", "README.md", "examples/a.json", "plugin/icon.png"])

    def test_links_become_routes_github_urls_or_text(self):
        text = ("[q](QUICKSTART.md#step-2) [home](../README.md) [ex](../examples/a.json) [dir](../examples/) "
                "[secret](internal/notes.md) [web](https://example.org) [top](#here) ![i](../plugin/icon.png)")
        out = self.linker.rewrite(text, "docs/GUIDE.md")
        self.assertIn("[q](/reachweaver/start/quickstart/#step-2)", out)
        self.assertIn("[home](/reachweaver/start/overview/)", out)
        self.assertIn("[ex](https://github.com/MSKazemi/reachweaver/blob/main/examples/a.json)", out)
        self.assertIn("[dir](https://github.com/MSKazemi/reachweaver/tree/main/examples)", out)
        self.assertIn(" secret ", out)
        self.assertNotIn("internal/notes.md", out)
        self.assertIn("[web](https://example.org)", out)
        self.assertIn("[top](#here)", out)
        self.assertIn("raw.githubusercontent.com/MSKazemi/reachweaver/main/plugin/icon.png", out)

    def test_fenced_code_is_left_alone(self):
        text = "```md\n[q](QUICKSTART.md)\n```\n[q](QUICKSTART.md)\n"
        out = self.linker.rewrite(text, "docs/GUIDE.md")
        self.assertIn("```md\n[q](QUICKSTART.md)\n```", out)
        self.assertIn("[q](/reachweaver/start/quickstart/)", out)

    def test_title_and_description(self):
        self.assertEqual(site.clean_title("Mastodon publishing (issue #43)"), "Mastodon publishing")
        self.assertEqual(site.clean_title("Facebook Page publishing (issue #21, owner-approved, disabled)"),
                         "Facebook Page publishing (owner-approved, disabled)")
        self.assertEqual(site.clean_title("What works for you (`insights`)"), "What works for you (insights)")
        body = "| table |\n\nShort.\n\n" + "A sentence that explains the page well enough for a search result. " * 4
        description = site.describe(body, "fallback")
        self.assertLessEqual(len(description), site.DESCRIPTION_MAX)
        self.assertTrue(description.startswith("A sentence"))
        self.assertIn("posted_by_hand", site.describe("`x` counts `posted_by_hand` rows for each cohort of posts, "
                                                       "offline and read-only, never guessing.", ""))

    def test_front_matter_is_quoted_and_dated(self):
        page = site.Page("docs/A.md", "start/a", 'Title: with "quotes"', "A", "", description="Desc: x",
                         updated="2026-10-21T10:00:00+02:00")
        fm = site.front_matter(page)
        self.assertIn('title: "Title: with \\"quotes\\""', fm)
        self.assertIn("lastUpdated: 2026-10-21\n", fm)
        self.assertIn("editUrl: \"https://github.com/MSKazemi/reachweaver/edit/main/docs/A.md\"", fm)

    def test_changelog_splits_per_release(self):
        text = ("# Changelog\n\nIntro.\n\n## [Unreleased]\n\n## [1.2.0] - 2026-10-20\n\nAdded a thing that matters "
                "to people reading release notes.\n\n## [1.1.0] - 2026-10-01\n\nFixed another thing for readers.\n")
        pages, group = site.changelog_pages(text)
        self.assertEqual([p.route for p in pages],
                         ["project/changelog", "project/changelog/v1-2-0", "project/changelog/v1-1-0"])
        self.assertEqual(pages[1].title, "ReachWeaver 1.2.0 release notes")
        self.assertTrue(pages[1].description.startswith("What changed in ReachWeaver 1.2.0 (2026-10-20)"))
        self.assertEqual(len(group["items"]), 3)

    def test_cli_reference_covers_every_command(self):
        from reachweaver.cli_parser import _subparsers_action, make_parser

        commands = set(_subparsers_action(make_parser()).choices)
        pages, _ = site.cli_pages()
        body = "\n".join(p.body for p in pages)
        missing = [c for c in commands if f"## reachweaver {c}\n" not in body]
        self.assertEqual(missing, [])
        self.assertIn(f"All {len(commands)} commands", pages[0].body)

    def test_llms_txt_lists_pages(self):
        page = site.Page("docs/A.md", "start/a", "A title", "A", "Body text.", description="A description.")
        short, full = site.llms_files([page], "9.9.9")
        self.assertTrue(short.startswith("# ReachWeaver\n\n> "))
        self.assertIn("- [A title](https://mskazemi.com/reachweaver/start/a/): A description.", short)
        self.assertIn("Body text.", full)
        self.assertIn("Current version: 9.9.9", short)


class CheckerTest(unittest.TestCase):
    PAGE = ('<!doctype html><html lang="en"><head><title>{title}</title>'
            '<meta name="description" content="A description that is long enough to pass the length rule here.">'
            '<link rel="canonical" href="https://mskazemi.com/reachweaver/{path}">'
            '<meta property="og:image" content="https://mskazemi.com/reachweaver/og.png">'
            '<script type="application/ld+json">{{"@context":"https://schema.org"}}</script></head>'
            '<body><h1>T</h1>{extra}</body></html>')

    def _dist(self, tmp: Path, pages: dict[str, str]) -> Path:
        for name in ("robots.txt", "llms.txt", "llms-full.txt", "og.png", "favicon.png"):
            (tmp / name).write_text("x", encoding="utf-8")
        urls = []
        for rel, html in pages.items():
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            urls.append("https://mskazemi.com/reachweaver/" + rel.removesuffix("index.html"))
        locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        (tmp / "sitemap-0.xml").write_text(f"<urlset>{locs}</urlset>", encoding="utf-8")
        return tmp

    def test_good_page_passes_and_defects_are_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            good = self.PAGE.format(title="Good", path="a/", extra="")
            self.assertEqual(checker.check(self._dist(tmp, {"a/index.html": good})), [])
            bad = self.PAGE.format(title="Good", path="wrong/",
                                   extra='<img src="x.png"><a href="docs/internal/x.md">x</a>')
            problems = checker.check(self._dist(tmp, {"a/index.html": good, "b/index.html": bad}))
            joined = "\n".join(problems)
            self.assertIn("title duplicates", joined)
            self.assertIn("canonical", joined)
            self.assertIn("images without alt", joined)
            self.assertIn("links to private", joined)

    def test_plain_text_mention_of_a_private_name_is_allowed(self):
        with tempfile.TemporaryDirectory() as raw:
            page = self.PAGE.format(title="T", path="a/", extra="<p>See THREAT-MODEL.md in the private repo.</p>")
            self.assertEqual(checker.check(self._dist(Path(raw), {"a/index.html": page})), [])


class WorkflowTest(unittest.TestCase):
    def test_actions_are_pinned_to_full_commit_shas(self):
        uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", ref)

    def test_deploys_only_from_main_with_least_privilege(self):
        self.assertIn("permissions:\n  contents: read", WORKFLOW)
        deploy = WORKFLOW[WORKFLOW.index("  deploy:"):]
        self.assertIn("github.ref == 'refs/heads/main'", deploy)
        self.assertIn("github.event_name != 'pull_request'", deploy)
        self.assertIn("pages: write", deploy)
        self.assertNotIn("pages: write", WORKFLOW[:WORKFLOW.index("  deploy:")])

    def test_never_uses_secrets_or_live_publishing(self):
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("--live", WORKFLOW)
        self.assertNotIn("REACHWEAVER_ALLOW_LIVE", WORKFLOW)

    def test_site_dependencies_are_exact_pins(self):
        import json

        package = json.loads((ROOT / "website" / "package.json").read_text(encoding="utf-8"))
        for name, version in {**package.get("dependencies", {}), **package.get("devDependencies", {})}.items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", name)
        self.assertTrue((ROOT / "website" / "package-lock.json").is_file())


if __name__ == "__main__":
    unittest.main()
