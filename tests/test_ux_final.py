"""Final UX copy fixes (2026-10-21 measurement): untitled posts, flagship Mastodon links, account questions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import account_add, posted, ui_data
from reachweaver.storage import connect, record_manual_publish


class UntitledPostTitleTests(unittest.TestCase):
    def test_a_post_recorded_from_its_link_alone_is_named_like_in_the_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "rw.sqlite")
            with connect(db) as con:
                record_manual_publish(con, platform="instagram", url="https://www.instagram.com/p/Abc123/",
                                      posted_at="2026-10-20T09:00:00+00:00", link_is_enough=True)
            with connect(db) as con:
                items = ui_data.posts_list(con)["posts"]
        titles = [item["excerpt"] for item in items]
        self.assertTrue(any(t.startswith("Instagram post of 20 Oct") for t in titles), titles)
        self.assertNotIn("", titles)

    def test_title_keeps_real_text_and_falls_back_to_the_network(self):
        self.assertEqual(ui_data._title("Hello world", "bluesky", None), "Hello world")
        self.assertEqual(ui_data._title("", "bluesky", None), "Bluesky post")
        self.assertEqual(ui_data._title(None, None, None), "Your post")


class FlagshipMastodonTests(unittest.TestCase):
    def test_mastodon_social_is_mastodon_without_a_configured_account(self):
        self.assertEqual(posted.platform_from_url("https://mastodon.social/@someone/1131", {}), "mastodon")


class AccountQuestionTests(unittest.TestCase):
    def ask(self, platform: str) -> str:
        asked: list[str] = []
        with patch.object(account_add, "_ask", side_effect=lambda q: asked.append(q) or "name"):
            account_add._ask_required(platform, "handle")
        return asked[0]

    def test_each_network_gets_its_own_handle_question(self):
        self.assertIn("name.bsky.social", self.ask("bluesky"))
        mastodon = self.ask("mastodon")
        self.assertIn("without the server", mastodon)
        self.assertNotIn("bsky", mastodon)
        self.assertEqual(self.ask("instagram"), "Your public username on Instagram")

    def test_linkedin_questions_point_to_the_online_guide(self):
        self.assertIn(account_add.LINKEDIN_DOC, account_add.QUESTIONS["person_id"])
        self.assertNotIn("see docs/LINKEDIN.md", account_add.QUESTIONS["person_id"])



class Round5FixTests(unittest.TestCase):
    def test_overview_idea_picks_never_print_null(self):
        from reachweaver import ui_assets
        js = ui_assets.APP_JS
        start = js.index("function ideaPicks()")
        body = js[start:js.index("function stepBlock", start)]
        self.assertIn(".filter(Boolean)", body)  # replaceChildren(null) would print the text "null"

    def test_results_platform_cell_says_how_many_were_posted_by_you(self):
        from reachweaver import ui_assets
        self.assertIn('", all by you"', ui_assets.APP_JS)
        self.assertNotIn('posted_by_hand + " posted by you"', ui_assets.APP_JS)


if __name__ == "__main__":
    unittest.main()
