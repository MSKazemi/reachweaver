"""The beginner documentation exists, links together, and the README stays a front page (offline).

The release gate already requires every CLI subcommand to appear in README and every relative link to resolve;
these tests pin what the gate cannot know: that a newcomer can reach the guide and glossary from the first screen,
that no reference material crept back above the fold, and that the glossary covers the words the guide relies on.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DOCS = ROOT / "docs"
BEGINNER_PAGES = ("START-HERE.md", "GLOSSARY.md", "README.md", "FEATURES.md")

# Terms that the beginner pages use and a newcomer may not know; each must have a glossary entry.
GLOSSARY_TERMS = ("Approval", "Dry run", "Draft", "Live", "Manual export", "Credential", "Reconcile", "Pause",
                  "needs_review", "Live receipt", "Mock-tested", "Human-only", "Payload hash")


class PlainLanguageDocsTests(unittest.TestCase):
    def test_beginner_pages_exist_and_are_not_empty(self):
        for name in BEGINNER_PAGES:
            with self.subTest(page=name):
                page = DOCS / name
                self.assertTrue(page.is_file(), name)
                self.assertGreater(len(page.read_text(encoding="utf-8").strip()), 500)

    def test_first_screen_points_a_newcomer_to_the_guides(self):
        first_screen = README[:4500]
        for target in ("docs/QUICKSTART.md", "docs/START-HERE.md", "docs/GLOSSARY.md", "docs/TROUBLESHOOTING.md"):
            with self.subTest(target=target):
                self.assertIn(f"]({target})", first_screen)

    def test_readme_front_page_comes_before_the_reference(self):
        order = [README.index(h) for h in ("## What is ReachWeaver?", "## Get started", "## Where to read next",
                                           "## Security and reality checks", "## Command reference")]
        self.assertEqual(order, sorted(order))
        # the long option lists live in the collapsible reference, not on the front page
        self.assertLess(README.index("## Get started"), README.index("<details>"))
        self.assertLess(README.index("## Where to read next"), README.index("## Command reference"))

    def test_readme_keeps_the_honest_limits_and_the_plain_statement_of_what_it_never_does(self):
        self.assertIn("## What it will never do", README)
        self.assertIn("never posts without your approval", README)
        self.assertIn("This is not a promise of viral growth.", README)

    def test_glossary_defines_the_terms_the_guides_use(self):
        glossary = (DOCS / "GLOSSARY.md").read_text(encoding="utf-8")
        entries = {m.group(1).strip("`") for m in re.finditer(r"^\*\*(.+?)\*\*", glossary, re.M)}
        entries |= {e.split(" / ")[0] for e in entries}
        for term in GLOSSARY_TERMS:
            with self.subTest(term=term):
                self.assertTrue(any(e.lower() == term.lower() or e.lower().startswith(term.lower()) for e in entries), term)

    def test_docs_index_links_every_beginner_page(self):
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        for name in ("START-HERE.md", "QUICKSTART.md", "GLOSSARY.md", "TROUBLESHOOTING.md", "FEATURES.md"):
            with self.subTest(page=name):
                self.assertIn(f"]({name})", index)

    def test_start_here_never_tells_a_newcomer_to_put_secrets_anywhere_but_the_environment(self):
        guide = (DOCS / "START-HERE.md").read_text(encoding="utf-8")
        self.assertIn("Never type them into Claude", guide)
        self.assertNotRegex(guide, r"(?i)\bexport\s+REACHWEAVER_\w*(TOKEN|PASSWORD|SECRET)\w*\s*=\s*\S")


if __name__ == "__main__":
    unittest.main()
