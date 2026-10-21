"""Composer UX round 4 (2026-10-21): an idea that arrives while a saved package is open, the posted-package update
lock, preview status from the current editor, the media tap area and the theme carried from the dashboard.

Test servers bind 127.0.0.1; synthetic captions, image bytes and links; a temporary database. No provider,
credential or network call; nothing may approve, schedule or send. The only new server output is a read-only
``package_unchanged`` flag on ``/api/draft/load``.
"""
from __future__ import annotations

import re
import unittest

from reachweaver import share_browser, share_composer_store as store, ui_assets

from test_share_browser_composer import CAPTION, URL, ComposerBase, png_bytes

IDEA = "A synthetic idea about offline tools for writing posts by hand."


class PackageUnchangedTests(ComposerBase):
    """``/api/draft/load`` says whether the draft is a saved package exactly as it is on disk."""

    BODY = {"platforms": ["threads", "bluesky"], "text": CAPTION}

    def load(self) -> dict:
        code, data = self.post("/api/draft/load")
        self.assertEqual(code, 200, data)
        return data

    def opened(self, body: dict | None = None) -> dict:
        name = self.save_new(body or self.BODY)["package"]["name"]
        code, data = self.post("/api/package/open", {"package": name})
        self.assertEqual(code, 200, data)
        return data["editor"]

    def test_no_draft_or_a_plain_draft_is_not_a_saved_package(self):
        self.assertFalse(self.load()["package_unchanged"])
        self.post("/api/draft/save", {"platforms": ["threads"], "text": "Unsaved words"})
        self.assertFalse(self.load()["package_unchanged"])
        self.assertFalse(store.draft_matches_package(None))

    def test_an_opened_package_is_unchanged_until_the_editor_changes_it(self):
        editor = self.opened()
        self.assertTrue(self.load()["package_unchanged"])
        code, _ = self.post("/api/draft/save", dict(editor, text=IDEA))
        self.assertEqual(code, 200)
        self.assertFalse(self.load()["package_unchanged"])
        self.post("/api/draft/save", dict(editor, platforms=["threads"]))
        self.assertFalse(self.load()["package_unchanged"])
        self.post("/api/draft/save", dict(editor, overrides={"threads": "Another version"}))
        self.assertFalse(self.load()["package_unchanged"])
        self.post("/api/draft/save", editor)  # back to the saved content
        self.assertTrue(self.load()["package_unchanged"])

    def test_marking_posted_keeps_it_unchanged_but_an_update_is_still_refused(self):
        editor = self.opened()
        name = editor["package"]
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL})
        self.assertEqual(code, 200, data)
        self.assertTrue(self.load()["package_unchanged"])
        code, data = self.post("/api/prepare", dict(self.BODY, text=IDEA, mode="update", package=name,
                                                    revision=editor["revision"], confirm=True))
        self.assertEqual(code, 409, data)
        self.assertIn("cannot be changed", data["errors"][0])

    def test_a_package_changed_on_disk_or_gone_is_not_unchanged(self):
        editor = self.opened()
        self.post("/api/draft/save", dict(editor, revision="0" * 16))
        self.assertFalse(self.load()["package_unchanged"])
        self.post("/api/draft/save", editor)
        self.assertTrue(self.load()["package_unchanged"])
        (self.shares / editor["package"] / store.EDITOR_FILE).write_text("{not json", encoding="utf-8")
        self.assertFalse(self.load()["package_unchanged"])  # damaged: never a crash, never "unchanged"

    def test_media_must_be_the_same_files(self):
        code, data = self.upload(png_bytes(), "photo.png")
        self.assertEqual(code, 200, data)
        editor = self.opened(dict(self.BODY, platforms=["bluesky"], media=[data["media"]["id"]]))
        self.assertEqual(len(editor["media"]), 1)
        self.assertTrue(self.load()["package_unchanged"])
        self.post("/api/draft/save", dict(editor, media=[]))
        self.assertFalse(self.load()["package_unchanged"])

    def test_the_flag_writes_nothing(self):
        editor = self.opened()
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertTrue(self.load()["package_unchanged"])
        after = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        self.assertEqual(self.load()["draft"]["package"], editor["package"])


class ComposerPageTests(unittest.TestCase):
    """Static checks of the shipped composer page; the clicks, layout and focus ran in a real browser."""

    html = share_browser.HTML

    def test_an_idea_starts_a_new_post_when_a_saved_package_is_open(self):
        for needle in ("if (isCleanPackage()) { startNewPost(value, label); return; }", "Started a new post from ",
                       "is still in Saved packages", "data.package_unchanged === true", 'api("/api/draft/clear", {})'):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_unsaved_changes_get_the_choice_at_the_top_of_the_editor_in_view_with_focus(self):
        write = self.html.index('aria-labelledby="write-title"')
        offer = self.html.index('<div id="offer" class="offer hidden" role="alertdialog" aria-labelledby="offer-text">')
        self.assertLess(write, offer)
        self.assertLess(offer, self.html.index('<div class="docbar">'))
        self.assertIn('el("offer").scrollIntoView({block:"start"});', self.html)
        self.assertIn('el("offer-yes").focus({preventScroll:true});', self.html)
        self.assertRegex(self.html, r"\.offer \{ position: sticky;")
        # Choice buttons are full-size buttons (44px), not the 32px "small" ones.
        self.assertIn('<button id="offer-yes" class="primary" type="button">Use it as my caption</button>', self.html)
        self.assertIn('<button id="offer-no" class="secondary" type="button">Keep my draft</button>', self.html)
        self.assertNotIn('button("Use it as my caption", "small")', self.html)
        self.assertRegex(self.html, r"\.offer \.primary, \.offer \.secondary \{ min-height: 44px; \}")

    def test_update_is_never_offered_for_a_package_with_a_posted_network(self):
        self.assertIn('id="update-why" class="hint hidden"', self.html)
        self.assertIn('aria-describedby="update-why"', self.html)
        self.assertIn("if (!previewValid || !current.package || postedLocked()) return;", self.html)
        self.assertIn("!current.package || postedLocked();", self.html)  # lockBusy keeps it disabled
        self.assertIn('(mode === "update" && postedLocked())', self.html)
        # The confirmation no longer mentions the posted case: it is never shown in that state.
        self.assertNotIn("A package with a network marked as posted cannot be changed.", self.html)

    def test_preview_status_follows_the_editor_content(self):
        self.assertIn('if (state && !isCleanPackage()) return ["Changed, not saved", "chip"];', self.html)
        self.assertIn("chip.dataset.net = item.id;", self.html)
        self.assertIn("refreshChips();", self.html)
        self.assertIn("baseline = editorKey();", self.html)

    def test_media_label_and_field_are_one_tap_area(self):
        self.assertRegex(self.html, r'<label for="media-input" class="media-drop">Photos or videos \(optional\)\s*'
                                    r'<input id="media-input" type="file"[^>]*>\s*</label>')
        self.assertRegex(self.html, r"\.media-drop \{[^}]*min-height: 44px")

    def test_theme_from_the_dashboard_link_is_honoured_and_kept(self):
        head = self.html[:self.html.index("</head>")]
        self.assertIn('<script nonce="__CSP_NONCE__">', head)
        self.assertIn('.get("theme")', head)
        self.assertIn('["auto", "light", "dark"].indexOf(choice) >= 0', head)
        self.assertIn('window.localStorage.setItem("rw-theme", choice)', head)
        self.assertIn('document.documentElement.setAttribute("data-theme", choice)', head)
        self.assertEqual(head.count("try {"), 3)  # every storage access is wrapped
        # Still nothing that writes markup.
        for gone in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval("):
            self.assertNotIn(gone, self.html)


class DashboardThemeLinkTests(unittest.TestCase):
    def test_composer_links_carry_the_settings_theme(self):
        js = ui_assets.APP_JS
        self.assertIn('return url.replace(/&theme=[a-z]+/g, "") + "&theme=" + (THEMES.includes(theme) ? theme : "auto");', js)
        self.assertIn('document.addEventListener("click", themeOnComposerLink, true);', js)
        self.assertIn("location.assign(withTheme(target))", js)
        # No new browser-storage key on the dashboard: the fragment carries the choice.
        self.assertEqual(sorted(set(re.findall(r'"(rw-[a-z]+)"', js))), ["rw-alerts", "rw-auto", "rw-draft", "rw-theme"])


if __name__ == "__main__":
    unittest.main()
