"""`seo-destinations --suggest-for`: per-platform suggestions with an explicit basis; offline, no ranking claims."""
from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from reachweaver import seo_platforms
from reachweaver.cli import main
from reachweaver.policy import PLATFORMS, ValidationError
from test_ui_data import referenced_names

BRIEFS = Path(__file__).resolve().parent.parent / "examples" / "seo-briefs.example.json"
DESTINATION = {"page_id": "p", "canonical_url": "https://example.org/docs/page", "title": "T",
               "primary_keyword": "approval-first social scheduling", "audience": "devs",
               "campaign_type": "release", "priority": 1, "project": "x", "status": "active"}
BASES = {"official", "unverified", "repo_policy", "hypothesis"}
# Words that would turn advice into a reach or ranking promise.
PROMISES = re.compile(r"\b(rank\w*|algorithm\w*|boost\w*|viral\w*|guarantee\w*|more reach|reach more)\b", re.I)


def suggestions(platform, campaign=None):
    return seo_platforms.platform_suggestion(DESTINATION, platform, campaign)["suggestions"]


class SuggestionTests(unittest.TestCase):
    def test_every_platform_gets_sourced_or_labelled_advice_and_no_promises(self):
        for platform in sorted(PLATFORMS):
            for item in suggestions(platform, "launch-1"):
                self.assertIn(item["basis"], BASES, (platform, item))
                self.assertNotRegex(item["advice"], PROMISES, (platform, item))
                if item["basis"] in ("official", "unverified"):
                    self.assertTrue(item["source"].startswith("https://"), (platform, item))
                    self.assertRegex(item["checked"], r"^\d{4}-\d{2}-\d{2}$")
                else:
                    self.assertIsNone(item["source"])

    def test_instagram_tags_separate_the_api_cap_from_the_app_limit(self):
        tags = [i for i in suggestions("instagram") if i["topic"] == "tags"]
        api = next(i for i in tags if "30" in i["advice"])
        app = next(i for i in tags if "five" in i["advice"])
        self.assertEqual(api["basis"], "official")
        self.assertIn("API", api["advice"])
        self.assertEqual(app["basis"], "unverified")
        self.assertTrue(app["source"].startswith("https://"))

    def test_link_placement_follows_the_recorded_facts(self):
        link = {p: next(i for i in suggestions(p) if i["topic"] == "link") for p in ("instagram", "youtube", "threads",
                                                                                        "mastodon")}
        self.assertEqual(link["instagram"]["basis"], "unverified")
        self.assertIn("profile link", link["instagram"]["advice"])
        self.assertEqual(link["youtube"]["basis"], "official")
        self.assertIn("description", link["youtube"]["advice"])
        self.assertIn("at most 5 links", link["threads"]["advice"])
        self.assertEqual(link["mastodon"]["basis"], "repo_policy")

    def test_campaign_gives_a_utm_link_and_no_campaign_says_how(self):
        tagged = seo_platforms.platform_suggestion(DESTINATION, "linkedin", "launch-1")
        self.assertIn("utm_source=linkedin", tagged["link"])
        self.assertIn("utm_campaign=launch-1", tagged["link"])
        plain = seo_platforms.platform_suggestion(DESTINATION, "linkedin")
        self.assertEqual(plain["link"], DESTINATION["canonical_url"])
        self.assertTrue(any("--campaign" in i["advice"] for i in plain["suggestions"]))

    def test_platform_specific_rules(self):
        self.assertTrue(any(i["topic"] == "authorship" for i in suggestions("devto")))
        self.assertTrue(any(i["topic"] == "community" for i in suggestions("reddit")))
        threads_tags = [i for i in suggestions("threads") if i["topic"] == "tags"]
        self.assertEqual([i["basis"] for i in threads_tags], ["official"])
        self.assertFalse(any(i["topic"] == "tags" for i in suggestions("telegram")))  # no hashtag habit claimed

    def test_keyword_hashtag(self):
        self.assertEqual(seo_platforms.keyword_hashtag("approval-first social scheduling"), "#ApprovalFirstSocialScheduling")
        self.assertEqual(seo_platforms.keyword_hashtag("Café déjà vu"), "#CaféDéjàVu")
        self.assertIsNone(seo_platforms.keyword_hashtag("!!!"))
        self.assertIsNone(seo_platforms.keyword_hashtag("word " * 20))

    def test_validation_order_and_no_destination(self):
        with self.assertRaises(ValidationError):
            seo_platforms.platform_suggestions(DESTINATION, ["myspace"])
        with self.assertRaises(ValidationError):
            seo_platforms.platform_suggestions(DESTINATION, [])
        result = seo_platforms.platform_suggestions(DESTINATION, ["Mastodon", "bluesky", "mastodon"])
        self.assertEqual([p["platform"] for p in result["platforms"]], ["mastodon", "bluesky"])
        self.assertEqual(seo_platforms.platform_suggestions(None, ["bluesky"])["platforms"], [])

    def test_module_names_no_network_database_or_send_path(self):
        names = referenced_names(seo_platforms.__file__)
        self.assertEqual(names & {"socket", "urlopen", "urllib", "requests", "connect", "publish", "credential",
                                  "sqlite3", "subprocess"}, set())


class CommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_output_is_unchanged_without_the_flag(self):
        code, out, _ = self.run_cli("seo-destinations", "--briefs", str(BRIEFS), "--type", "release",
                                    "--project", "reachweaver")
        self.assertNotIn("platform_suggestions", json.loads(out))
        self.assertIn(code, (0, 2))

    def test_flag_adds_suggestions_for_each_platform(self):
        code, out, err = self.run_cli("seo-destinations", "--briefs", str(BRIEFS), "--type", "release",
                                      "--project", "reachweaver", "--suggest-for", "bluesky", "--suggest-for",
                                      "youtube", "--campaign", "reachweaver-v0-3-release")
        self.assertIn(code, (0, 2), err)
        result = json.loads(out)["platform_suggestions"]
        self.assertEqual([p["platform"] for p in result["platforms"]], ["bluesky", "youtube"])
        self.assertIn("hypothesis", result["note"])

    def test_unknown_platform_exits_2(self):
        code, _, err = self.run_cli("seo-destinations", "--briefs", str(BRIEFS), "--type", "release",
                                    "--suggest-for", "myspace")
        self.assertEqual(code, 2)
        self.assertIn("Unknown platform", err)


if __name__ == "__main__":
    unittest.main()
