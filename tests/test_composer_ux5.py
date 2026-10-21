"""Composer and dashboard UX round 5 (2026-10-22): fewer clicks in the five studio tasks.

Server side: ``/api/save-and-mark`` does "Save as new package" and Mark as posted in one request (owner approved
2026-10-21) behind the composer's existing guards (loopback, token, Host/Origin, JSON limits, write lock). Every
check of both runs before anything is written; when only the database refuses the record after the save, the
answer names the package that was kept. The record is the usual terminal ``published_manual`` one: never approved,
scheduled or sent. Page side: live previews, Mark as posted on each preview card, caption focus, the Overview's
ideas with "Write this", display names, "3 days after posting" and the small accessibility fixes.

Test servers bind 127.0.0.1; synthetic captions, links and accounts; a temporary database. No provider,
credential or network call.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import unittest
from unittest.mock import patch

from reachweaver import results, share_browser, share_composer_store as store, ui_assets
from reachweaver.policy import ValidationError
from reachweaver.storage import connect, pending

from test_share_browser_composer import ACCOUNTS, CAPTION, URL, ComposerBase, call

OTHER_URL = "https://www.threads.net/@owner.example/post/C0mp0serTest2"
BSKY_URL = "https://bsky.app/profile/owner.example.bsky.social/post/3kux5test1"
BSKY_OTHER = "https://bsky.app/profile/someone-else.bsky.social/post/3kux5test2"


class SaveAndMarkTests(ComposerBase):
    """``/api/save-and-mark``: a NEW package plus a by-hand record, or nothing (or, if only the record is refused
    after the save, the saved package named)."""

    POST = {"platforms": ["threads", "bluesky", "reddit", "xiaohongshu"], "text": CAPTION,
            "note": "Read the community rules first"}

    def setUp(self):
        super().setUp()
        (self.root / "config").mkdir()
        (self.root / "config" / "accounts.json").write_text(json.dumps(ACCOUNTS), encoding="utf-8")
        self.srv.accounts = str(self.root / "config" / "accounts.json")

    def mark(self, **extra) -> tuple[int, dict]:
        body = dict(self.POST, platform="threads", url=URL)
        body.update(extra)
        return self.post("/api/save-and-mark", body)

    def posts(self) -> list[dict]:
        if not Path(self.db).exists():
            return []
        with connect(self.db) as con:
            return [dict(r) for r in con.execute("SELECT id, status, platform, account_id, content, remote_url, "
                                                 "approved_at, approved_by, scheduled_at FROM posts")]

    def nothing_written(self, before_packages: list[str] | None = None) -> None:
        self.assertEqual(self.packages(), before_packages or [])
        self.assertEqual(self.posts(), [])

    def test_saves_a_new_package_and_records_a_terminal_by_hand_post(self):
        code, data = self.mark(platform="bluesky", url=BSKY_URL, numbers={"likes": 12, "replies": 3})
        self.assertEqual(code, 200, data)
        [name] = self.packages()
        self.assertTrue(data["saved"])
        self.assertFalse(data["published"] if "published" in data else False)
        self.assertEqual(data["package"]["name"], name)
        self.assertTrue(data["saved_to"].endswith(name))
        self.assertIn("Saved as a new package", data["notice"])
        self.assertIn("can never be approved, scheduled or sent", data["notice"])
        posted = data["posted"]
        self.assertEqual((posted["status"], posted["label"]), ("published_manual", "posted by hand"))
        self.assertEqual((posted["account_id"], posted["account_from"]), ("owner-bsky", "link"))  # rules unchanged
        self.assertEqual(posted["numbers"], {"likes": 12, "replies": 3})
        states = {n["id"]: n["state"] for n in data["package"]["networks"]}
        self.assertEqual(states, {"threads": "exported", "bluesky": "posted", "reddit": "exported",
                                  "xiaohongshu": "exported"})
        marker = json.loads((self.shares / name / "bluesky" / store.POSTED_FILE).read_text(encoding="utf-8"))
        self.assertEqual(marker["post_id"], posted["post_id"])
        self.assertEqual(self.posts(), [{"id": posted["post_id"], "status": "published_manual", "platform": "bluesky",
                                         "account_id": "owner-bsky", "content": CAPTION, "remote_url": BSKY_URL,
                                         "approved_at": None, "approved_by": None, "scheduled_at": None}])
        with connect(self.db) as con:
            self.assertEqual(pending(con), [])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts WHERE status IN "
                                         "('approved','scheduled','sending','needs_review')").fetchone()[0], 0)
        # The saved package is a normal one: the next network goes through the plain route, and it never updates.
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL,
                                                    "account_id": "owner-threads"})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["posted"]["account_id"], "owner-threads")

    def test_always_a_new_package_never_an_update(self):
        first = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]["name"]
        for extra in ({"package": first}, {"mode": "update"}, {"revision": "0" * 16}, {"confirm": True},
                      {"approve": True}, {"schedule": "2026-10-23T09:00:00Z"}):
            with self.subTest(extra=extra):
                code, data = self.mark(**extra)
                self.assertEqual(code, 422, data)
                self.assertIn("Unexpected fields", data["errors"][0])
        code, data = self.mark()
        self.assertEqual(code, 200, data)
        self.assertNotEqual(data["package"]["name"], first)
        self.assertEqual(len(self.packages()), 2)
        self.assertFalse((self.shares / first / "threads" / store.POSTED_FILE).exists())

    def test_every_refusal_before_the_save_writes_nothing(self):
        cases = [
            ({"url": "not a link"}, "starting with https://"),
            ({"url": ""}, "Paste the link"),
            ({"url": "javascript:alert(1)"}, "starting with https://"),
            ({"url": URL + chr(0x202E)}, "control or text-direction characters"),
            ({"posted_at": "2999-01-01T00:00:00Z"}, "in the future"),
            ({"posted_at": "yesterday"}, "ISO 8601"),
            ({"posted_at": 7}, "ISO 8601"),
            ({"platform": "reddit"}, "human-only"),
            ({"platform": "xiaohongshu"}, "cannot measure"),
            ({"platform": "instagram"}, "not in this post"),
            ({"platform": None}, "not in this post"),
            ({"platform": "../../etc"}, "not in this post"),
            ({"account_id": "owner-bsky"}, "is for bluesky, not Threads"),
            ({"account_id": "nobody-here"}, "no account called nobody-here"),
            ({"platform": "bluesky", "account_id": "owner-bsky", "url": BSKY_OTHER}, "not by owner-bsky"),
            ({"numbers": {"likes": -1}}, "whole number"),
            ({"numbers": {"likes": "12"}}, "whole number"),
            ({"numbers": {"stars": 3}}, "Unexpected numbers: stars"),
            ({"text": ""}, "needs a caption"),
            ({"platforms": []}, "Choose at least one network"),
        ]
        for extra, words in cases:
            with self.subTest(extra=extra):
                code, data = self.mark(**extra)
                self.assertEqual(code, 422, data)
                self.assertIn(words, data["errors"][0])
                self.assertNotIn("--url", data["errors"][0])
                self.assertNotIn("saved", data)
                self.nothing_written()
        self.assertFalse(Path(self.db).exists(), "a refused save-and-mark never creates the history database")

    def test_a_link_already_recorded_is_refused_before_the_save(self):
        code, data = self.mark()
        self.assertEqual(code, 200, data)
        before = self.packages()
        code, data = self.mark(text="A different caption for the same link, still synthetic.")
        self.assertEqual(code, 422, data)
        self.assertIn("already recorded", data["errors"][0])
        self.assertIn("nothing was saved or recorded", data["errors"][0])
        self.assertEqual(self.packages(), before)
        self.assertEqual(len(self.posts()), 1)

    def test_the_duplicate_precheck_opens_the_database_read_only(self):
        self.mark()
        database = Path(self.db)
        stamp = database.stat().st_mtime_ns, database.read_bytes()
        with self.assertRaisesRegex(ValidationError, "already recorded"):
            store.record_precheck("threads", URL, None, self.db)
        self.assertEqual((database.stat().st_mtime_ns, database.read_bytes()), stamp)
        store.record_precheck("threads", OTHER_URL, None, self.db)  # a new link passes
        store.record_precheck("threads", URL, None, str(self.root / "no-such.sqlite"))  # no database: nothing yet
        self.assertFalse((self.root / "no-such.sqlite").exists())

    def test_if_the_record_fails_after_the_save_the_answer_says_what_was_saved(self):
        def refuse(*args, **kwargs):
            raise ValidationError("--at is in the future: give the time you actually posted it; nothing recorded")
        with patch("reachweaver.storage.record_manual_publish", side_effect=refuse):
            code, data = self.mark()
        self.assertEqual(code, 422, data)
        [name] = self.packages()
        self.assertTrue(data["saved"])
        self.assertFalse(data["published"])
        self.assertEqual(data["package"]["name"], name)
        self.assertTrue(data["saved_to"].endswith(name))
        message = data["errors"][0]
        self.assertIn("Saved as a new package: " + data["package"]["display_name"], message)
        self.assertIn("but it was not marked as posted", message)
        self.assertIn("The posted time is in the future", message)
        self.assertIn("The package is kept", message)
        self.assertEqual({n["id"]: n["state"] for n in data["package"]["networks"]}["threads"], "exported")
        self.assertFalse(list(self.shares.glob(f"{name}/*/{store.POSTED_FILE}")))
        self.assertEqual(self.posts(), [])
        # The kept package is an ordinary saved one: Mark as posted there records it now.
        code, data = self.post("/api/mark-posted", {"package": name, "platform": "threads", "url": URL})
        self.assertEqual(code, 200, data)

    def test_a_database_error_after_the_save_is_reported_the_same_way(self):
        with patch("reachweaver.storage.record_manual_publish", side_effect=sqlite3.OperationalError("locked")):
            code, data = self.mark()
        self.assertEqual(code, 422, data)
        self.assertTrue(data["saved"])
        self.assertIn("the history database could not be written", data["errors"][0])
        self.assertNotIn("locked", data["errors"][0])
        self.assertEqual(len(self.packages()), 1)

    def test_a_failed_save_records_nothing(self):
        with patch.object(share_browser, "_write_private", side_effect=OSError("disk full")):
            code, data = self.mark()
        self.assertEqual(code, 500, data)
        self.assertNotIn("saved", data)
        self.nothing_written()

    def test_guards_refuse_without_token_origin_or_host_and_write_nothing(self):
        body = dict(self.POST, platform="threads", url=URL)
        port = self.srv.server_port
        for headers in ({"Origin": self.srv.origin},
                        {"X-ReachWeaver-Token": "wrong-" + "a" * 40, "Origin": self.srv.origin},
                        {"X-ReachWeaver-Token": self.srv.token, "Origin": "https://attacker.example"},
                        {"X-ReachWeaver-Token": self.srv.token},
                        {"X-ReachWeaver-Token": self.srv.token, "Origin": self.srv.origin,
                         "Host": f"localhost:{port}"}):
            with self.subTest(headers=sorted(headers)):
                code, data = call(self.srv, "/api/save-and-mark", body, headers=headers, good=False)
                self.assertEqual(code, 403, data)
        self.nothing_written()
        self.assertFalse(Path(self.db).exists())

    def test_write_lock_is_held(self):
        acquired = []
        real = self.srv.write_lock

        class Spy:
            def __enter__(self):
                acquired.append(True)
                return real.__enter__()

            def __exit__(self, *exc):
                return real.__exit__(*exc)
        self.srv.write_lock = Spy()
        code, data = self.mark()
        self.assertEqual(code, 200, data)
        self.assertEqual(acquired, [True])

    def test_previews_say_where_mark_as_posted_is_offered(self):
        code, data = self.post("/api/preview", self.POST)
        self.assertEqual(code, 200, data)
        shown = {p["id"]: (p["measurable"], p["ledger"]) for p in data["platforms"]}
        self.assertEqual(shown, {"threads": (True, "threads"), "bluesky": (True, "bluesky"),
                                 "reddit": (False, "reddit"), "xiaohongshu": (False, "xiaohongshu")})
        code, data = self.post("/api/preview", {"platforms": ["dev"], "text": CAPTION})
        self.assertEqual((data["platforms"][0]["ledger"], data["platforms"][0]["measurable"]), ("devto", True))
        self.assertFalse(Path(self.db).exists())

    def test_the_composer_still_has_no_send_path(self):
        self.assertIsNone(share_browser.SEND_PATH)
        self.assertIsNone(store.SEND_PATH)
        source = Path(share_browser.__file__).read_text(encoding="utf-8")
        for word in ("publisher", "claim_sending", "transition(", "approve_post", "schedule_post", "urlopen"):
            self.assertNotIn(word, source)
        self.assertIn("/api/save-and-mark", share_browser.JSON_ROUTES)
        self.assertFalse(any(word in route for route in share_browser.JSON_ROUTES
                             for word in ("send", "publish", "approve", "schedule")))


class ComposerPageTests(unittest.TestCase):
    """Static checks of the shipped composer page; the clicks, focus and layout ran in a real browser."""

    html = share_browser.HTML

    def test_previews_follow_typing_with_a_debounce_and_drop_stale_answers(self):
        for needle in ("const LIVE_DELAY = 400;", "previewAbort.abort();", "new AbortController()",
                       'api("/api/preview", body, controller ? controller.signal : undefined)',
                       "if (seq !== previewSeq || key !== JSON.stringify(payload())) return;",
                       'e.name === "AbortError"', "schedulePreview();  // every network is checked again",
                       "if (document.activeElement !== parts.editor"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)
        # The explicit button stays only as a fallback refresh; it no longer leads the page.
        self.assertIn('<button id="preview" class="secondary" type="button">Refresh previews</button>', self.html)
        self.assertNotIn("Preview my posts", self.html)
        self.assertNotIn("Preview again", self.html)

    def test_the_caption_takes_focus_on_wide_screens_or_from_a_write_link(self):
        self.assertIn('window.matchMedia("(min-width: 821px)")', self.html)
        self.assertIn('get("write") === "1"', self.html)
        self.assertIn("if (writeWanted) el(\"caption\").focus();", self.html)
        self.assertIn('else if (wide) el("caption").focus({preventScroll:true});', self.html)
        self.assertIn("if (ideaWanted || pendingOffer || current.package || el(\"caption\").value.trim()) return;",
                      self.html)

    def test_mark_as_posted_on_each_card_saves_first_when_needed(self):
        for needle in ('parts.mark.textContent = "Mark " + item.label + " as posted";',
                       'api("/api/save-and-mark", Object.assign(payload(), {platform:item.id}, body))',
                       'api("/api/mark-posted", Object.assign({package:current.package, platform:item.id}, body))',
                       "e.data.saved === true", "adoptSaved(e.data.package)", "!item.measurable"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.html)

    def test_the_mark_form_goes_link_then_numbers_and_enter_records(self):
        start = self.html.index("function buildMarkForm(")
        body = self.html[start:self.html.index("\n }\n", start)]
        self.assertLess(body.index("const url = document.createElement"), body.index("numbersFieldset("))
        self.assertLess(body.index("numbersFieldset("), body.index('text("label", "Which account? (optional)")'))
        self.assertLess(body.index('text("label", "Which account? (optional)")'), body.index("datetime-local"))
        self.assertIn('if (event.key === "Enter" && event.target.tagName === "INPUT") { event.preventDefault(); '
                      "onRecord(); }", body)
        # Account preselection is unchanged: one account is chosen, the link's account wins until you choose.
        self.assertIn("else if (mine.length === 1 && !select.dataset.touched) select.value = mine[0].id;", self.html)
        self.assertIn('api("/api/link-account", {platform:ledger, url:link})', self.html)

    def test_a_fresh_page_never_says_saving_before_you_type(self):
        self.assertIn("if (preselected) { stable = snapshot(); scheduleSave(true); }", self.html)
        self.assertIn('if (quiet !== true) { loudSave = true; setSaved("Saving…", ""); }', self.html)
        self.assertIn('if (wrote) setSaved("Draft saved", "ok");', self.html)
        self.assertIn('<span id="saved" class="saved" role="status" aria-live="polite">Not saved yet</span>',
                      self.html)

    def test_page_still_offers_no_send_approve_or_schedule(self):
        for word in ("/api/send", "/api/approve", "/api/schedule", ">Publish<", ">Schedule<", ">Approve<"):
            self.assertNotIn(word, self.html)


class DashboardTests(unittest.TestCase):
    """The read-only dashboard's part: ideas on the Overview, names, ages and the accessibility fixes."""

    js = ui_assets.APP_JS
    css = ui_assets.APP_CSS
    page = ui_assets.INDEX_HTML

    def test_overview_offers_the_newest_ideas_with_write_this(self):
        self.assertIn("const IDEAS_ON_OVERVIEW = 3;", self.js)
        self.assertIn('api("ideas?status=open")', self.js)
        self.assertIn('writeLink("Write this", i.id, "Write a post from your idea: " + i.excerpt)', self.js)
        self.assertIn('command("reachweaver share --web --open --idea " + i.id)', self.js)  # the dashboard alone
        self.assertIn('step.opens === "ideas" ? ideaPicks() : null', self.js)

    def test_write_links_tell_the_composer_you_came_to_write(self):
        self.assertIn('(ideaId ? "&idea=" + encodeURIComponent(ideaId) : "&write=1")', self.js)
        self.assertIn("cta.href = composerHref();", self.js)

    def test_platform_display_names_match_the_terminal(self):
        found = re.search(r"const PLATFORM_NAMES = (\{[^}]*\});", self.js)
        self.assertIsNotNone(found)
        self.assertEqual(json.loads(found.group(1)), results.NAMES)
        self.assertIn('platform ? platformName(platform) : "unknown"', self.js)
        self.assertIn('h("span", { class: "chip info" }, platformName(a.platform))', self.js)

    def test_results_say_days_after_posting(self):
        self.assertNotIn('" h after posting"', self.js)
        self.assertIn('sincePosting(s.age_hours) + " after posting"', self.js)
        self.assertIn('return days + (days === 1 ? " day" : " days");', self.js)

    def test_ideas_filters_and_search_have_plain_names(self):
        self.assertIn('"aria-label": label + " (" + n + ")"', self.js)
        self.assertIn('[["", "All", all], ["open", "Open", d.counts.open]', self.js)
        self.assertIn('"Search your ideas")', self.js)

    def test_settings_checkboxes_are_44px_rows(self):
        self.assertIn("label.auto { display: flex; align-items: center; gap: 8px; font-size: 14px; margin: 0; "
                      "min-height: 44px;", self.css + self.page)


if __name__ == "__main__":
    unittest.main()
