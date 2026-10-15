"""Reply proposals carry the original message, evidence and the constraints of their own platform (public issue #47).

Offline. A draft never quotes the comment; it carries a sanitized excerpt, the matched rule and docs link as
evidence, and limits that differ per platform. Human-authored-only platforms get no draft at all.
"""
from __future__ import annotations

import unittest

from reachweaver.engagement import DEFAULT_REPLY_LIMIT, HUMAN_AUTHORED_ONLY, triage_engagement
from reachweaver.policy import CONSERVATIVE_TEXT_LIMITS
from tests.test_engagement import export, item

QUESTION = "How do I configure the docs map? I could not find the setting."
DOCS = {"schema_version": "reachweaver.docs-map.v1",
        "topics": [{"topic": "Docs map", "keywords": ["docs map"], "url": "https://example.org/docs/engagement"}]}
URLS = {"bluesky": "https://bsky.app/profile/a.example/post/1", "mastodon": "https://mastodon.social/@a/1",
        "github": "https://github.com/o/r/issues/1#issuecomment-1", "hackernews": "https://news.ycombinator.com/item?id=1",
        "linkedin": "https://www.linkedin.com/feed/update/urn:li:activity:1/", "x": "https://x.com/a/status/1"}


def triage(*platforms):
    items = [item(f"c-{p}", QUESTION, platform=p, url=URLS[p], account_id=f"acct-{p}") for p in platforms]
    return triage_engagement(export(*items), DOCS)


class PerPlatformConstraintsTests(unittest.TestCase):
    PLATFORMS = ("bluesky", "mastodon", "linkedin", "x")

    def setUp(self):
        self.report = triage(*self.PLATFORMS)
        self.drafts = {d["platform"]: d for d in self.report["drafts"]}

    def test_every_platform_gets_its_own_draft_with_its_own_recorded_limit(self):
        self.assertEqual(set(self.drafts), set(self.PLATFORMS))
        for platform, draft in self.drafts.items():
            self.assertEqual(draft["constraints"]["conservative_text_limit"],
                             CONSERVATIVE_TEXT_LIMITS.get(platform, DEFAULT_REPLY_LIMIT), platform)
            self.assertLessEqual(len(draft["text"]), draft["constraints"]["conservative_text_limit"], platform)

    def test_the_limits_really_differ_between_platforms(self):
        limits = {p: d["constraints"]["conservative_text_limit"] for p, d in self.drafts.items()}
        self.assertEqual(limits, {"bluesky": 300, "mastodon": 450, "linkedin": 2800, "x": 260})
        self.assertGreater(len(set(limits.values())), 2)

    def test_each_draft_says_how_to_post_it_on_that_platform_and_that_nothing_is_sent(self):
        for platform, draft in self.drafts.items():
            instructions = draft["constraints"]["manual_review_instructions"]
            self.assertIn(platform, instructions)
            self.assertFalse(draft["constraints"]["human_authored_only"], platform)
            self.assertFalse(draft["approval"]["approved"])
            self.assertEqual(draft["status"], "draft_requires_approval")

    def test_each_draft_carries_the_original_excerpt_and_the_evidence_it_rests_on(self):
        for platform, draft in self.drafts.items():
            self.assertIn("configure the docs map", draft["original_excerpt"], platform)
            self.assertEqual(draft["evidence"]["docs_link"]["url"], "https://example.org/docs/engagement", platform)
            self.assertTrue(draft["evidence"]["matched_rules"], platform)
            self.assertNotIn("I could not find the setting", draft["text"])  # the reply never quotes the comment
            self.assertEqual(draft["reply_to_url"], URLS[platform])

    def test_a_human_authored_only_platform_gets_no_draft_and_says_so(self):
        report = triage("hackernews", "bluesky")
        self.assertEqual({d["platform"] for d in report["drafts"]}, {"bluesky"})
        hn = next(i for i in report["items"] if i["platform"] == "hackernews")
        self.assertIn("hackernews", HUMAN_AUTHORED_ONLY)
        self.assertEqual(hn["action"], "human_authored_reply_only")
        self.assertTrue(hn["constraints"]["human_authored_only"])
        self.assertIn("hackernews", hn["constraints"]["manual_review_instructions"])

    def test_a_platform_without_a_comment_api_degrades_to_manual_review_instructions(self):
        report = triage("github")
        (draft,) = report["drafts"]
        self.assertEqual(draft["constraints"]["reachweaver_send_support"], "none")
        self.assertIn("post it yourself", draft["constraints"]["manual_review_instructions"])


if __name__ == "__main__":
    unittest.main()
