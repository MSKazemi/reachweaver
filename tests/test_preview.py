"""Offline campaign-preview tests (issue #10 slice). No network, no staging, no publishing."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from reachweaver import preview as preview_module
from reachweaver.campaigns import TEXT_ONLY_STAGING, read_json, validate_brand, validate_campaign
from reachweaver.claims import check_claims, effective_brand_rules, variant_body
from reachweaver.cli import main
from reachweaver.preview import build_preview, capability, measure, render_markdown, render_text
from reachweaver.capabilities import load_registry
from reachweaver.richtext import grapheme_count, hashtags, utf8_len

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "examples/campaign-preview.reachweaver.json"
BRAND = ROOT / "examples/brand-preview.reachweaver.json"
REPO = "https://github.com/MSKazemi/reachweaver"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class PreviewTestCase(unittest.TestCase):
    def setUp(self):
        self.campaign = read_json(CAMPAIGN)
        self.brand = read_json(BRAND)
        self.registry = load_registry()

    def preview(self, accounts=None, utm=False):
        return build_preview(self.campaign, self.brand, accounts, utm=utm, registry=self.registry)

    def variant(self, variant_id, accounts=None, utm=False):
        return next(v for v in self.preview(accounts, utm)["variants"] if v["variant_id"] == variant_id)

    def raw(self, variant_id):
        return next(v for v in self.campaign["variants"] if v["id"] == variant_id)


class SamplePreviewTests(PreviewTestCase):
    def test_sample_covers_three_channel_styles_with_exact_text_and_source_links(self):
        result = self.preview()
        self.assertEqual(result["campaign_errors"], [])
        self.assertEqual(result["summary"], {"high": 5, "medium": 0, "low": 0})
        texts = [v for v in result["variants"] if v["format"] == "text"]
        self.assertEqual({v["platform"] for v in texts}, {"linkedin", "bluesky", "mastodon"})
        for v in texts:
            self.assertEqual(v["text"], self.raw(v["variant_id"])["text"])
            self.assertTrue(v["stageable"])
            self.assertTrue(v["evidence"] and all(e["url"].startswith("https://") for e in v["evidence"]))
        bluesky = next(v for v in texts if v["platform"] == "bluesky")
        self.assertEqual(bluesky["grounded"][0]["span"], "14")
        self.assertEqual(bluesky["grounded"][0]["evidence_urls"],
                         [REPO + "/blob/main/reachweaver/platform_registry.json"])

    def test_sample_brand_and_campaign_validate(self):
        self.assertEqual(validate_brand(self.brand), [])
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertEqual(sorted(errors), sorted([f"variants[3]: {TEXT_ONLY_STAGING}",
                                                 f"variants[4]: {TEXT_ONLY_STAGING}"]))

    def test_build_preview_never_mutates_inputs(self):
        before_campaign, before_brand = deepcopy(self.campaign), deepcopy(self.brand)
        self.preview(utm=True)
        self.assertEqual(self.campaign, before_campaign)
        self.assertEqual(self.brand, before_brand)

    def test_module_has_no_send_path(self):
        self.assertIsNone(preview_module.SEND_PATH)
        source = (ROOT / "reachweaver/preview.py").read_text(encoding="utf-8")
        for forbidden in ("publishers", "connections", "urllib.request", "socket", "storage", "postiz"):
            self.assertNotIn(forbidden, source)


class LengthTests(PreviewTestCase):
    def test_bluesky_counts_graphemes_and_bytes(self):
        text = "Family 👨‍👩‍👧 and 日本語 #tag " + REPO
        length = measure("bluesky", text)
        self.assertEqual(length["count"], grapheme_count(text))
        self.assertEqual(length["utf8_bytes"], utf8_len(text))
        self.assertLess(length["count"], length["utf8_bytes"])
        self.assertEqual(length["remaining"], 300 - length["count"])
        self.assertEqual(length["remaining_bytes"], 3000 - length["utf8_bytes"])
        self.assertTrue(length["within_limit"])
        self.assertFalse(measure("bluesky", "x" * 301)["within_limit"])

    def test_bluesky_facets_use_byte_offsets(self):
        self.raw("preview-bluesky")["text"] = "é #tag " + REPO
        facets = self.variant("preview-bluesky")["pieces"][0]["features"]["facets"]
        tag = next(f for f in facets if f["type"] == "tag")
        self.assertEqual((tag["byteStart"], tag["byteEnd"]), (3, 7))  # "é" is two UTF-8 bytes
        link = next(f for f in facets if f["type"] == "link")
        self.assertEqual(link["text"], REPO)

    def test_mastodon_uses_account_max_characters_and_23_char_urls(self):
        short = "See https://a.io/x"
        self.assertEqual(measure("mastodon", short)["count"], len("See ") + 23)
        default = measure("mastodon", "hello")
        self.assertEqual(default["limit"], 450)
        self.assertIn("conservative", default["limit_source"])
        accounts = {"my-mastodon": {"platform": "mastodon", "max_characters": 200}}
        v = self.variant("preview-mastodon", accounts=accounts)
        self.assertEqual(v["pieces"][0]["length"]["limit"], 200)
        self.assertEqual(v["pieces"][0]["length"]["limit_source"], "account max_characters")
        self.assertEqual(v["account_config"], "loaded")
        accounts["my-mastodon"]["max_characters"] = 5000  # can never raise the cap
        self.assertEqual(self.variant("preview-mastodon", accounts=accounts)["pieces"][0]["length"]["limit"], 450)

    def test_mastodon_account_limit_overflow_is_low_confidence(self):
        accounts = {"my-mastodon": {"platform": "mastodon", "max_characters": 100}}
        v = self.variant("preview-mastodon", accounts=accounts)
        self.assertFalse(v["pieces"][0]["length"]["within_limit"])
        self.assertLess(v["pieces"][0]["length"]["remaining"], 0)
        self.assertEqual(v["confidence"]["level"], "low")
        self.assertTrue(any("mastodon limit is 100" in e for e in v["validation"]["errors"]))

    def test_other_platforms_use_conservative_cap(self):
        length = measure("linkedin", "abc")
        self.assertEqual((length["limit"], length["remaining"]), (2800, 2797))
        self.assertIsNone(measure("myspace", "abc")["limit"])


class ConfidenceTests(PreviewTestCase):
    def test_high_medium_low_follow_the_rule(self):
        self.assertEqual(self.variant("preview-bluesky")["confidence"]["level"], "high")
        self.raw("preview-bluesky")["text"] = "ReachWeaver is trusted by maintainers: " + REPO
        medium = self.variant("preview-bluesky")["confidence"]
        self.assertEqual(medium["level"], "medium")
        self.assertTrue(any("partnership" in line for line in medium["rationale"]))
        self.raw("preview-bluesky")["text"] = "ReachWeaver cut failed posts by 40%: " + REPO
        low = self.variant("preview-bluesky")["confidence"]
        self.assertEqual(low["level"], "low")
        self.assertTrue(any("unsupported_number" in line for line in low["rationale"]))
        self.assertIn("medium = human-review items present", low["rule"])

    def test_rationale_is_explained_and_editable(self):
        v = self.variant("preview-linkedin")
        self.assertEqual(v["confidence"]["editor_rationale"], self.raw("preview-linkedin")["rationale"])
        self.assertIn("capability-registry", v["confidence"]["rationale"][0])
        self.assertIn("editable", v["confidence"]["editable"].lower())
        self.raw("preview-linkedin")["rationale"] = ""
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any(".rationale" in e for e in errors))

    def test_warnings_do_not_lower_confidence(self):
        self.campaign["variants"][2]["cta_phrase"] = "not in the text anywhere"
        v = self.variant("preview-mastodon")
        self.assertIn("missing_cta", [f["code"] for f in v["findings"]])
        self.assertEqual(v["confidence"]["level"], "high")

    def test_campaign_level_errors_make_every_variant_low(self):
        self.campaign["evidence"][0]["verification"] = "vibes"
        result = self.preview()
        self.assertTrue(result["campaign_errors"])
        self.assertEqual(result["summary"]["low"], 5)
        self.assertFalse(any(v["stageable"] for v in result["variants"]))


class UtmTests(PreviewTestCase):
    def test_links_are_tagged_side_by_side_with_variant_id(self):
        v = self.variant("preview-bluesky", utm=True)
        piece = v["pieces"][0]
        link = piece["utm"]["links"][0]
        self.assertEqual(link["original"], REPO)
        self.assertEqual(link["utm"], REPO + "?utm_source=bluesky&utm_medium=social"
                         "&utm_campaign=reachweaver-channel-previews&utm_content=preview-bluesky")
        self.assertEqual(piece["text"], self.raw("preview-bluesky")["text"])
        self.assertIn(link["utm"], piece["utm"]["text"])
        self.assertGreater(piece["utm"]["length"]["count"], piece["length"]["count"])
        self.assertNotIn("utm", self.variant("preview-bluesky")["pieces"][0])

    def test_untaggable_link_is_reported_not_rewritten(self):
        self.raw("preview-linkedin")["text"] = "Plain http link http://example.com/page and " + REPO
        links = self.variant("preview-linkedin", utm=True)["pieces"][0]["utm"]
        self.assertIsNone(links["links"][0]["utm"])
        self.assertIn("https", links["links"][0]["error"])
        self.assertIn("http://example.com/page", links["text"])
        self.assertIn("utm_content=preview-linkedin", links["text"])

    def test_cli_utm_never_modifies_campaign_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.json"
            path.write_bytes(CAMPAIGN.read_bytes())
            code, out, _ = run_cli("campaign-preview", "--file", str(path), "--brand", str(BRAND), "--utm", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(path.read_bytes(), CAMPAIGN.read_bytes())
            self.assertTrue(json.loads(out)["utm"])


class ThreadTests(PreviewTestCase):
    def test_parts_are_numbered_and_measured_per_part(self):
        v = self.variant("preview-bluesky-thread")
        self.assertEqual([p["label"] for p in v["pieces"]], ["1/4", "2/4", "3/4", "4/4"])
        for piece, text in zip(v["pieces"], self.raw("preview-bluesky-thread")["parts"]):
            self.assertEqual(piece["text"], text)
            self.assertEqual(piece["length"]["count"], grapheme_count(text))
        self.assertTrue(v["thread"]["first_part"]["standalone"])
        self.assertFalse(v["stageable"])
        self.assertEqual(v["staging_note"], TEXT_ONLY_STAGING)
        self.assertEqual(v["confidence"]["level"], "high")
        self.assertIn("inserts no numbering", v["thread"]["numbering_note"])

    def test_typed_numbering_is_grounded_like_any_figure(self):
        self.raw("preview-bluesky-thread")["parts"][1] = "2/4 First, a human approves the exact text and account."
        v = self.variant("preview-bluesky-thread")
        self.assertIn("unsupported_number", [f["code"] for f in v["findings"]])

    def test_over_limit_part_is_an_error(self):
        self.raw("preview-bluesky-thread")["parts"][1] = "x " * 160
        v = self.variant("preview-bluesky-thread")
        self.assertFalse(v["pieces"][1]["length"]["within_limit"])
        self.assertTrue(any("parts[1]" in e for e in v["validation"]["errors"]))
        self.assertEqual(v["confidence"]["level"], "low")

    def test_first_part_must_stand_alone(self):
        self.raw("preview-bluesky-thread")["parts"][0] = "🧵 Guardrails."
        v = self.variant("preview-bluesky-thread")
        finding = next(f for f in v["findings"] if f["code"] == "thread_first_part_not_standalone")
        self.assertEqual(finding["severity"], "review")
        self.assertIn("hook", finding["message"])
        self.assertIn("link", finding["message"])
        self.assertEqual(v["confidence"]["level"], "medium")
        self.raw("preview-bluesky-thread")["parts"][0] = "Three guardrails sit between a draft and a post, review the design."
        self.assertFalse(self.variant("preview-bluesky-thread")["thread"]["first_part"]["has_link_or_cta"])
        self.raw("preview-bluesky-thread")["cta_phrase"] = "review the design"
        self.assertTrue(self.variant("preview-bluesky-thread")["thread"]["first_part"]["standalone"])

    def test_grounding_covers_every_part_and_names_the_part(self):
        self.raw("preview-bluesky-thread")["parts"][2] = "Failed sends dropped 40% after the registry change."
        v = self.variant("preview-bluesky-thread")
        finding = next(f for f in v["findings"] if f["code"] == "unsupported_number")
        self.assertEqual(finding["piece"], "3/4")
        self.assertEqual(v["confidence"]["level"], "low")
        body = variant_body(self.raw("preview-bluesky-thread"))
        self.assertEqual(body[finding["start"]:finding["end"]], "40%")

    def test_thread_contract_is_validated(self):
        thread = self.raw("preview-bluesky-thread")
        thread["text"] = "also text"
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("not text" in e for e in errors))
        del thread["text"]
        thread["parts"] = ["only one"]
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("parts must be a list of 2..25" in e for e in errors))


class CarouselTests(PreviewTestCase):
    def test_linkedin_carousel_unavailable_warns(self):
        v = self.variant("preview-linkedin-carousel")
        self.assertEqual(v["carousel"]["slides"], 4)
        self.assertEqual(v["carousel"]["capability_status"], "unavailable")
        self.assertIn("carousel_unavailable", [f["code"] for f in v["findings"]])
        self.assertEqual([p["kind"] for p in v["pieces"]], ["caption", "slide", "slide", "slide", "slide"])
        self.assertFalse(v["stageable"])

    def test_other_carousel_statuses(self):
        self.assertEqual(capability("telegram", "carousel_outline", self.registry)["status"], "api_direct")
        self.assertEqual(capability("instagram", "carousel_outline", self.registry)["label"], "gated")
        carousel = self.raw("preview-linkedin-carousel")
        carousel["platform"], carousel["account_id"] = "bluesky", "my-bluesky"
        v = self.variant("preview-linkedin-carousel")
        self.assertIn("carousel_unverified", [f["code"] for f in v["findings"]])
        self.assertEqual(capability("myspace", "text", self.registry)["status"], "unknown")

    def test_slides_are_validated(self):
        self.raw("preview-linkedin-carousel")["slides"] = []
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("slides must be a list of 1..20" in e for e in errors))


class PerAccountBrandRuleTests(PreviewTestCase):
    def test_account_banned_phrase_applies_only_to_that_account(self):
        for vid in ("preview-linkedin", "preview-bluesky"):
            self.raw(vid)["text"] = "Written by a thought leader: " + REPO
        report = check_claims(self.campaign, self.brand)
        linkedin, bluesky = report["variants"][0], report["variants"][1]
        banned = [f for f in linkedin["findings"] if f["code"] == "banned_phrase"]
        self.assertEqual(banned[0]["scope"], "account:my-linkedin")
        self.assertIn("for account my-linkedin", banned[0]["message"])
        self.assertNotIn("banned_phrase", [f["code"] for f in bluesky["findings"]])

    def test_global_rules_still_apply_with_overrides(self):
        self.raw("preview-linkedin")["text"] = "A game-changer: " + REPO
        report = check_claims(self.campaign, self.brand)
        banned = [f for f in report["variants"][0]["findings"] if f["code"] == "banned_phrase"]
        self.assertEqual(len(banned), 1)
        self.assertNotIn("scope", banned[0])

    def test_max_hashtags_per_account(self):
        self.raw("preview-linkedin")["text"] = "Four tags " + REPO + " #a #b #c #d"
        v = self.variant("preview-linkedin")
        finding = next(f for f in v["findings"] if f["code"] == "too_many_hashtags")
        self.assertEqual((finding["severity"], finding["scope"]), ("error", "account:my-linkedin"))
        self.assertEqual(finding["hashtags"], ["#a", "#b", "#c", "#d"])
        self.assertEqual(v["brand_rules"]["max_hashtags"], 3)
        self.assertEqual(v["brand_rules"]["hashtag_count"], 4)
        self.assertEqual(v["confidence"]["level"], "low")
        self.raw("preview-bluesky")["text"] = "No cap here " + REPO + " #a #b #c #d"
        self.assertNotIn("too_many_hashtags", [f["code"] for f in self.variant("preview-bluesky")["findings"]])

    def test_overlong_hashtag_still_counts(self):
        self.assertEqual(hashtags("#" + "a" * 80 + " #b"), ["#" + "a" * 80, "#b"])

    def test_merge_is_additive_and_takes_the_lowest_cap(self):
        self.brand["required_disclosure"] = "I maintain ReachWeaver"
        self.brand["max_hashtags"] = 2
        self.brand["accounts"]["my-linkedin"]["required_disclosure"] = "Disclosure: maintainer"
        rules = effective_brand_rules(self.brand, "my-linkedin")
        self.assertEqual([d["text"] for d in rules["required_disclosures"]],
                         ["I maintain ReachWeaver", "Disclosure: maintainer"])
        self.assertEqual((rules["max_hashtags"], rules["max_hashtags_scope"]), (2, "brand"))
        self.assertEqual([p["phrase"] for p in rules["banned_phrases"]], ["game-changer", "thought leader"])
        self.raw("preview-linkedin")["text"] = "I maintain ReachWeaver. " + REPO
        codes = [(f["code"], f.get("scope")) for f in check_claims(self.campaign, self.brand)["variants"][0]["findings"]]
        self.assertIn(("missing_disclosure", "account:my-linkedin"), codes)
        self.assertNotIn(("missing_disclosure", None), codes)

    def test_absent_accounts_keeps_existing_behaviour(self):
        brand = read_json(ROOT / "examples/brand.reachweaver.json")
        campaign = read_json(ROOT / "examples/campaign.reachweaver.json")
        brand["banned_phrases"] = ["production operation"]
        report = check_claims(campaign, brand)
        finding = next(f for f in report["variants"][0]["findings"] if f["code"] == "banned_phrase")
        self.assertEqual(finding["message"], "brand bans the phrase 'production operation'")
        self.assertNotIn("scope", finding)
        self.assertFalse(effective_brand_rules(brand, "my-linkedin")["account_override"])

    def test_brand_accounts_are_validated(self):
        self.brand["accounts"]["stranger"] = {"max_hashtags": 1}
        self.brand["accounts"]["my-bluesky"] = {"max_hashtags": -1, "tone": "loud", "banned_phrases": [""]}
        self.brand["accounts"]["my-mastodon"] = {"required_disclosure": ""}
        issues = validate_brand(self.brand)
        self.assertTrue(any("stranger" in i and "approved_accounts" in i for i in issues))
        self.assertTrue(any("unknown rule fields tone" in i for i in issues))
        self.assertTrue(any("my-bluesky.max_hashtags" in i for i in issues))
        self.assertTrue(any("my-bluesky.banned_phrases" in i for i in issues))
        self.assertTrue(any("my-mastodon.required_disclosure" in i for i in issues))
        self.brand["accounts"] = []
        self.assertTrue(any("brand.accounts must be an object" in i for i in validate_brand(self.brand)))


class CliTests(unittest.TestCase):
    def test_text_json_and_markdown_outputs(self):
        base = ("campaign-preview", "--file", str(CAMPAIGN), "--brand", str(BRAND))
        code, out, _ = run_cli(*base)
        self.assertEqual(code, 0)
        self.assertIn("PREVIEW reachweaver-channel-previews", out)
        self.assertIn("nothing was staged", out.lower())
        code, out, _ = run_cli(*base, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["report"], "campaign_preview")
        code, out, _ = run_cli(*base, "--markdown", "--utm")
        self.assertEqual(code, 0)
        self.assertIn("### Rationale (editable)", out)
        self.assertIn("| Original | With UTM |", out)
        with self.assertRaises(SystemExit):
            run_cli(*base, "--json", "--markdown")

    def test_cli_opens_no_socket(self):
        refuse = AssertionError("network access attempted")
        with patch.object(socket, "create_connection", side_effect=refuse), \
                patch.object(socket.socket, "connect", side_effect=refuse), \
                patch.object(socket, "getaddrinfo", side_effect=refuse):
            code, out, _ = run_cli("campaign-preview", "--file", str(CAMPAIGN), "--brand", str(BRAND),
                                   "--utm", "--markdown")
        self.assertEqual(code, 0)
        self.assertIn("utm_content=preview-mastodon", out)

    def test_low_confidence_exits_2_and_accounts_option_loads_public_config(self):
        campaign = read_json(CAMPAIGN)
        campaign["variants"][0]["text"] = "Cut failures by 40%: " + REPO
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps(campaign), encoding="utf-8")
            db = Path(tmp) / "never.sqlite"
            code, out, _ = run_cli("--db", str(db), "campaign-preview", "--file", str(path), "--brand", str(BRAND),
                                   "--accounts", str(ROOT / "config/accounts.example.json"), "--json")
            self.assertEqual(code, 2)
            data = json.loads(out)
            self.assertTrue(data["accounts_loaded"])
            self.assertEqual(data["summary"]["low"], 1)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
