"""seo-content-check is offline, read-only and labels every finding with its basis."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import CONSERVATIVE_TEXT_LIMITS, ValidationError
from reachweaver.seo_content import check_items, load_items

GOOD_LINK = "https://example.org/r?utm_source=bluesky&utm_medium=social&utm_campaign=r1"


def one(**extra) -> dict:
    base = {"id": "p1", "platform": "bluesky", "text": "A short, plain sentence about our release notes today."}
    base.update(extra)
    return base


def codes(report: dict, item_id: str = "p1") -> dict[str, dict]:
    return {f["code"]: f for r in report["items"] if r["id"] == item_id for f in r["findings"]}


class CheckTests(unittest.TestCase):
    def check(self, *items: dict) -> dict:
        return check_items(load_items({"items": list(items)}))

    def test_clean_item_has_no_findings(self):
        report = self.check(one())
        self.assertEqual(report["counts"], {"error": 0, "warn": 0, "info": 0})
        self.assertEqual(report["status"], "read_only_no_publish")
        self.assertFalse(report["policy"]["network_access"])
        self.assertFalse(report["policy"]["publishes"])

    def test_length_over_conservative_limit_is_a_repo_policy_error(self):
        found = codes(self.check(one(text="x" * (CONSERVATIVE_TEXT_LIMITS["bluesky"] + 1))))
        self.assertEqual(found["text_too_long"]["severity"], "error")
        self.assertEqual(found["text_too_long"]["basis"], "repo_policy")

    def test_unknown_platform_is_info_not_a_guess(self):
        found = codes(self.check(one(platform="peerlist")))
        self.assertEqual(found["no_length_limit_recorded"]["severity"], "info")
        self.assertNotIn("text_too_long", found)

    def test_instagram_caption_rules_are_official(self):
        found = codes(self.check(one(platform="instagram", text=" ".join(f"#t{i}" for i in range(31)))))
        self.assertEqual(found["instagram_caption_rule"]["basis"], "official")
        self.assertEqual(found["instagram_caption_rule"]["severity"], "error")

    def test_alt_text_missing_only_with_media(self):
        self.assertNotIn("alt_text_missing", codes(self.check(one())))
        self.assertIn("alt_text_missing", codes(self.check(one(media_count=1))))
        self.assertIn("alt_text_missing", codes(self.check(one(media_count=1, alt_text="  "))))
        self.assertNotIn("alt_text_missing", codes(self.check(one(media_count=2, alt_text="A bar chart"))))

    def test_hashtags_repeated_and_heavy_are_hypotheses(self):
        found = codes(self.check(one(text="Release notes #oss #OSS #python #devtools")))
        self.assertEqual(found["hashtag_repeated"]["basis"], "hypothesis")
        self.assertIn("hashtag_heavy", found)
        self.assertNotIn("hashtag_repeated", codes(self.check(one(text="Release notes for everyone #oss #python"))))

    def test_keywords_absent_only_when_none_present(self):
        text = "Release notes for the approval-first publisher are online now."
        self.assertNotIn("keywords_absent", codes(self.check(one(text=text, keywords=["Approval-First", "calendar"]))))
        self.assertIn("keywords_absent", codes(self.check(one(text=text, keywords=["calendar"]))))

    def test_link_attribution_uses_the_canonical_utm_rules(self):
        text = f"Read the notes {GOOD_LINK}"
        self.assertNotIn("link_attribution", codes(self.check(one(text=text, link=GOOD_LINK))))
        self.assertIn("link_attribution", codes(self.check(one(text=text, link="https://example.org/r"))))
        self.assertIn("link_not_in_text", codes(self.check(one(link=GOOD_LINK))))

    def test_cross_platform_duplicate_ignores_urls_and_hashtags_and_same_platform(self):
        a = one(id="a", text="New release of our approval-first publisher is out today #oss https://a.example/x")
        b = one(id="b", platform="mastodon", text="New release of our approval-first publisher is out today https://b.example/y")
        c = one(id="c", text="New release of our approval-first publisher is out today")
        report = self.check(a, b, c)
        self.assertIn("cross_platform_duplicate", codes(report, "a"))
        self.assertIn("cross_platform_duplicate", codes(report, "b"))
        same_platform_only = self.check(a, c)
        self.assertNotIn("cross_platform_duplicate", codes(same_platform_only, "a"))

    def test_short_texts_are_not_flagged_as_duplicates(self):
        report = self.check(one(id="a", text="Thanks all!"), one(id="b", platform="mastodon", text="Thanks all!"))
        self.assertNotIn("cross_platform_duplicate", codes(report, "a"))

    def test_human_authored_platforms_get_a_reminder(self):
        self.assertIn("human_authored_only", codes(self.check(one(platform="hackernews"))))

    def test_no_finding_claims_a_ranking_effect(self):
        report = self.check(one(text="#a #a #b #c", media_count=1, keywords=["zzz"], link="https://example.org"))
        blob = json.dumps(report).casefold()
        for phrase in ("rank higher", "boost reach", "more reach", "algorithm favors"):
            self.assertNotIn(phrase, blob)


class LoadTests(unittest.TestCase):
    def test_validation_rejects_bad_input(self):
        bad = [None, [], {"items": []}, {"items": [{"id": "a"}]},
               {"items": [one(extra=1)]}, {"items": [one(), one()]},
               {"items": [one(text="")]}, {"items": [one(media_count=-1)]},
               {"items": [one(media_count=True)]}, {"items": [one(keywords=["k"] * 9)]},
               {"items": [one(alt_text=5)]}, {"items": [one(link=5)]},
               {"items": [one(id="x" * 201)]}]
        for data in bad:
            with self.subTest(data=str(data)[:60]), self.assertRaises(ValidationError):
                load_items(data)

    def test_bare_list_is_accepted(self):
        self.assertEqual(len(load_items([one()])), 1)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_exit_codes_and_no_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "never.sqlite"
            ok = Path(tmp) / "ok.json"
            ok.write_text(json.dumps({"items": [one()]}), encoding="utf-8")
            code, out, _ = self.run_cli("--db", str(db), "seo-content-check", str(ok))
            self.assertEqual(code, 0)
            self.assertIn("0 error", out)
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"items": [one(text="x" * 5000)]}), encoding="utf-8")
            code, out, _ = self.run_cli("--db", str(db), "seo-content-check", str(bad), "--json")
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out)["counts"]["error"], 1)
            self.assertFalse(db.exists())

    def test_symlink_and_invalid_json_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "t.json"
            target.write_text("{not json", encoding="utf-8")
            link = Path(tmp) / "l.json"
            link.symlink_to(target)
            for path in (target, link):
                with self.subTest(path=path.name), self.assertRaises(ValidationError):
                    from reachweaver.seo_content import check_file
                    check_file(path)


if __name__ == "__main__":
    unittest.main()
