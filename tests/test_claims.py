"""Golden offline tests for LEXICAL claim grounding (issue #10). No network, no publishing."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from reachweaver.campaigns import read_json, validate_brand, validate_campaign
from reachweaver.claims import check_claims, extract_tokens, find_feature_claims, jaccard
from reachweaver.cli import main

ROOT = Path(__file__).resolve().parents[1]


def codes(report, index=0):
    return [f["code"] for f in report["variants"][index]["findings"]]


def categories(report, index=0):
    return {f.get("category") for f in report["variants"][index]["findings"] if f["code"] == "needs_human_review"}


class ClaimGroundingTests(unittest.TestCase):
    def setUp(self):
        self.campaign = read_json(ROOT / "examples/campaign.reachweaver.json")
        self.brand = read_json(ROOT / "examples/brand.reachweaver.json")
        self.evidence = self.campaign["evidence"][0]

    def set_text(self, text, index=0):
        self.campaign["variants"][index]["text"] = text

    def test_reference_example_is_clean(self):
        report = check_claims(self.campaign, self.brand)
        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["error"], 0)
        self.assertEqual(report["summary"]["review"], 0)
        self.assertEqual(report["method"], "lexical")
        self.assertIn("does not verify", report["limitations"])

    def test_unsupported_metric_is_blocking_error(self):
        self.set_text("ReachWeaver cut failed posts by 40% for 12 teams.")
        report = check_claims(self.campaign, self.brand)
        findings = [f for f in report["variants"][0]["findings"] if f["code"] == "unsupported_number"]
        self.assertEqual({f["span"] for f in findings}, {"40%", "12"})
        self.assertTrue(all(f["severity"] == "error" and f["matched_evidence"] == [] for f in findings))
        text = self.campaign["variants"][0]["text"]
        self.assertTrue(all(text[f["start"]:f["end"]] == f["span"] for f in findings))
        self.assertFalse(report["valid"])
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("unsupported_number" in e for e in errors))

    def test_supported_metric_records_matched_evidence(self):
        self.evidence["excerpt"] = "In our pilot, failed sends dropped by 40% across the queue."
        self.set_text("Failed sends dropped by 40% in the pilot.")
        report = check_claims(self.campaign, self.brand)
        self.assertNotIn("unsupported_number", codes(report))
        grounded = report["variants"][0]["grounded"]
        self.assertEqual(grounded[0]["span"], "40%")
        self.assertEqual(grounded[0]["matched_evidence"], ["source-repo"])
        self.assertEqual(validate_campaign(self.campaign, brand=self.brand)[0], [])

    def test_percentage_is_not_grounded_by_plain_number_or_multiplier(self):
        self.evidence["excerpt"] = "Throughput was 3 times higher and 40 requests succeeded."
        self.set_text("40% faster and 3x throughput.")
        report = check_claims(self.campaign, self.brand)
        spans = {f["span"] for f in report["variants"][0]["findings"] if f["code"] == "unsupported_number"}
        self.assertEqual(spans, {"40%"})  # 3x is grounded by "3 times"; 40% is not grounded by 40

    def test_number_only_in_uncited_evidence_is_still_an_error(self):
        extra = deepcopy(self.evidence)
        extra.update({"id": "benchmark", "excerpt": "Median latency was 250 ms."})
        self.campaign["evidence"].append(extra)
        self.set_text("Median latency was 250 ms.")
        finding = next(f for f in check_claims(self.campaign, self.brand)["variants"][0]["findings"]
                       if f["code"] == "unsupported_number")
        self.assertIn("uncited evidence benchmark", finding["message"])
        self.campaign["variants"][0]["evidence_ids"].append("benchmark")
        self.assertNotIn("unsupported_number", codes(check_claims(self.campaign, self.brand)))

    def test_versions_dates_and_urls(self):
        self.evidence["excerpt"] = "Version 0.2 was tagged on 2026-10-08."
        self.set_text("v0.2 landed in October 2026, on Oct 8. Details: https://example.org/r/2024/991")
        report = check_claims(self.campaign, self.brand)
        self.assertNotIn("unsupported_number", codes(report))  # URL digits are ignored
        self.set_text("v0.3 lands on 2026-11-01.")
        spans = {f["span"] for f in check_claims(self.campaign, self.brand)["variants"][0]["findings"]}
        self.assertEqual(spans, {"v0.3", "2026-11-01"})

    def test_unicode_and_locale_numbers(self):
        self.evidence["excerpt"] = "The project has 1,000 installs and 10% of them are teams."
        for text in ("Now 1.000 installs, 10 % teams.", "Now 1 000 installs, 10 % teams.",
                     "Now １,０００ installs, １０％ teams.", "Now 1000 installs, 10 percent teams."):
            with self.subTest(text=text):
                self.set_text(text)
                report = check_claims(self.campaign, self.brand)
                self.assertNotIn("unsupported_number", codes(report))
        kinds = {t["kind"]: sorted(t["keys"]) for t in extract_tokens("١٢ users, 3×, 2.5M, 1.2.3")}
        self.assertEqual(kinds["multiplier"], [("mult", "3")])
        self.assertIn(("num", "12"), [k for t in extract_tokens("١٢ users") for k in t["keys"]])
        self.assertIn(("num", "2500000"), kinds["number"])
        self.assertEqual(kinds["version"], [("ver", "1.2.3")])

    def test_invented_testimonial_needs_review(self):
        self.set_text('"This saved our launch week," said a maintainer at a large company.')
        report = check_claims(self.campaign, self.brand)
        self.assertEqual(categories(report), {"quote", "testimonial"})
        self.assertTrue(report["valid"])  # review is human judgment, not an automatic block
        _, warnings = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("needs_human_review" in w for w in warnings))

    def test_verbatim_quote_in_evidence_is_grounded(self):
        self.evidence["excerpt"] = 'Ada Example wrote: "the approval queue caught a wrong account" in issue 4.'
        self.set_text('Ada Example: "the approval queue caught a wrong account".')
        report = check_claims(self.campaign, self.brand)
        self.assertNotIn("quote", categories(report))
        self.assertIn("quote", {g["type"] for g in report["variants"][0]["grounded"]})

    def test_partnership_and_customer_claims_need_review_unless_supported(self):
        self.set_text("Trusted by maintainers and partnered with a major cloud. Used by thousands of developers.")
        cats = categories(check_claims(self.campaign, self.brand))
        self.assertEqual(cats, {"partnership", "customer_count"})
        self.evidence["excerpt"] = "ReachWeaver is trusted by maintainers of three projects."
        self.set_text("Trusted by maintainers of three projects.")
        self.assertEqual(categories(check_claims(self.campaign, self.brand)), set())

    def test_customer_count_with_number_must_match_same_evidence(self):
        self.evidence["excerpt"] = "Over 1,000 developers starred the repository."
        self.set_text("1000 developers starred it.")
        self.assertEqual(categories(check_claims(self.campaign, self.brand)), set())
        self.set_text("1000 customers rely on it.")
        report = check_claims(self.campaign, self.brand)
        self.assertIn("customer_count", categories(report))

    def test_superlatives_need_review(self):
        for text, span in (("The fastest way to stage drafts.", "fastest"), ("The first open outbox.", "The first"),
                           ("#1 approval CLI.", "#1"), ("The only tool you need.", "The only"),
                           ("Best way to review posts.", "Best")):
            with self.subTest(text=text):
                self.set_text(text)
                found = [f for f in check_claims(self.campaign, self.brand)["variants"][0]["findings"]
                         if f.get("category") == "superlative"]
                self.assertEqual([f["span"] for f in found], [span])
        self.set_text("Follow best practices: first, review the draft; only then approve.")
        self.assertEqual(categories(check_claims(self.campaign, self.brand)), set())

    def test_release_claim_requires_release_evidence(self):
        self.set_text("ReachWeaver is now available for every maintainer.")
        report = check_claims(self.campaign, self.brand)
        self.assertIn("unsupported_release", codes(report))
        self.assertFalse(report["valid"])
        self.evidence["kind"] = "release"
        self.assertNotIn("unsupported_release", codes(check_claims(self.campaign, self.brand)))
        self.evidence.pop("kind")
        self.evidence["url"] = "https://github.com/MSKazemi/reachweaver/releases/tag/v0.2.0"
        self.assertNotIn("unsupported_release", codes(check_claims(self.campaign, self.brand)))
        self.assertEqual(validate_campaign(self.campaign, brand=self.brand)[0], [])

    def test_near_duplicates_warn_with_configurable_threshold(self):
        base = "ReachWeaver keeps a durable local outbox and asks a human to approve every exact post before sending"
        self.set_text(base, 0)
        self.set_text(base + " anything.", 1)
        report = check_claims(self.campaign, self.brand)
        dup = [f for f in report["variants"][1]["findings"] if f["code"] == "near_duplicate"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["severity"], "warning")
        self.assertEqual(dup[0]["other_variant_id"], "developer-linkedin")
        self.assertGreaterEqual(dup[0]["similarity"], 0.6)
        self.assertEqual(codes(check_claims(self.campaign, self.brand, duplicate_threshold=1.0), 1), [])
        self.assertEqual(jaccard("a b c d", "w x y z"), 0.0)
        with self.assertRaises(ValueError):
            check_claims(self.campaign, self.brand, duplicate_threshold=0)
        _, warnings = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("near_duplicate" in w for w in warnings))

    def test_banned_phrase_and_required_disclosure(self):
        self.brand["banned_phrases"] = ["game-changer", "10x engineer"]
        self.brand["required_disclosure"] = "I maintain ReachWeaver"
        self.assertEqual(validate_brand(self.brand), [])
        self.set_text("A GAME-CHANGER for maintainers. I maintain ReachWeaver.")
        report = check_claims(self.campaign, self.brand)
        banned = [f for f in report["variants"][0]["findings"] if f["code"] == "banned_phrase"]
        self.assertEqual([f["span"] for f in banned], ["GAME-CHANGER"])
        self.assertNotIn("missing_disclosure", codes(report))
        self.assertIn("missing_disclosure", codes(report, 1))
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any("banned_phrase" in e for e in errors))
        self.assertTrue(any("missing_disclosure" in e for e in errors))
        self.brand["banned_phrases"] = ["", 3]
        self.assertTrue(any("banned_phrases" in i for i in validate_brand(self.brand)))

    def test_declared_cta_presence(self):
        self.campaign["cta_url"] = "https://github.com/MSKazemi/reachweaver"
        self.assertNotIn("missing_cta", codes(check_claims(self.campaign, self.brand)))
        self.set_text("Approval-first social publishing: https://github.com/MSKazemi/reachweaver/?utm_source=linkedin")
        self.assertNotIn("missing_cta", codes(check_claims(self.campaign, self.brand)))
        self.set_text("Approval-first social publishing for maintainers.")
        self.campaign["variants"][1]["cta_phrase"] = "review the design"
        report = check_claims(self.campaign, self.brand)
        self.assertEqual(codes(report), ["missing_cta"])
        self.assertEqual(codes(report, 1), ["missing_cta"])
        self.campaign["cta_url"] = "http://localhost/x"
        self.assertTrue(any("cta_url" in e for e in validate_campaign(self.campaign, brand=self.brand)[0]))

    def test_contract_extensions_are_validated(self):
        self.evidence["kind"] = "rumour"
        self.evidence["excerpt"] = ""
        errors, _ = validate_campaign(self.campaign, brand=self.brand)
        self.assertTrue(any(".kind" in e for e in errors))
        self.assertTrue(any(".excerpt" in e for e in errors))

    def test_malformed_variants_do_not_crash(self):
        self.campaign["variants"].append("not-an-object")
        self.campaign["variants"].append({"id": "x", "text": 5, "evidence_ids": "nope"})
        report = check_claims(self.campaign, self.brand)
        self.assertEqual(len(report["variants"]), 3)
        self.assertEqual(check_claims({}, None)["variants"], [])

    def test_cli_claims_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaign, brand = Path(tmp) / "c.json", Path(tmp) / "b.json"
            brand.write_text(json.dumps(self.brand), encoding="utf-8")
            campaign.write_text(json.dumps(self.campaign), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["claims-check", str(campaign), "--brand", str(brand), "--json"]), 0)
            self.assertTrue(json.loads(out.getvalue())["valid"])
            self.set_text("Now 3x faster with 500 stars.")
            campaign.write_text(json.dumps(self.campaign), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["claims-check", str(campaign), "--brand", str(brand)]), 2)
            self.assertIn("unsupported_number", out.getvalue())
            self.assertIn("Lexical grounding only", out.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["claims-check", str(campaign), "--brand", str(brand),
                                       "--duplicate-threshold", "2"]), 2)


class FeatureClaimTests(unittest.TestCase):
    """Golden cases for nonexistent-feature detection: a human-review flag, never a blocking error."""

    def setUp(self):
        self.campaign = read_json(ROOT / "examples/campaign.reachweaver.json")
        self.brand = read_json(ROOT / "examples/brand.reachweaver.json")
        self.evidence = self.campaign["evidence"][0]
        self.evidence["excerpt"] = ("ReachWeaver adds an approval queue and supports Mastodon instances. "
                                    "Each reply caches its policy checks.")

    def set_text(self, text, index=0):
        self.campaign["variants"][index]["text"] = text

    def features(self, index=0):
        report = check_claims(self.campaign, self.brand)
        return report, [f for f in report["variants"][index]["findings"] if f.get("category") == "feature"]

    # --- positive: invented capabilities are flagged ------------------------------------------
    def test_nonexistent_feature_is_flagged_for_review_not_blocked(self):
        self.set_text("Big news: ReachWeaver now supports TikTok scheduling.")
        report, found = self.features()
        self.assertEqual(len(found), 1)
        finding = found[0]
        self.assertEqual((finding["code"], finding["severity"]), ("needs_human_review", "review"))
        self.assertEqual(finding["span"], "now supports TikTok scheduling")
        self.assertEqual(finding["missing_terms"], ["TikTok", "scheduling"])
        self.assertIn("possible nonexistent feature", finding["message"])
        text = self.campaign["variants"][0]["text"]
        self.assertEqual(text[finding["start"]:finding["end"]], finding["span"])
        self.assertTrue(report["valid"])
        errors, warnings = validate_campaign(self.campaign, brand=self.brand)
        self.assertEqual(errors, [])  # staging is not blocked by a feature finding
        self.assertTrue(any("needs_human_review" in w and "TikTok" in w for w in warnings))

    def test_each_trigger_family_is_detected(self):
        for text, span in (
                ("Adds native support for Matrix and Discord.", "Adds native support for Matrix and Discord"),
                ("It integrates with Postiz.", "integrates with Postiz"),
                ("Introducing webhook alerts for every account.", "Introducing webhook alerts for every account"),
                ("The CLI ships with a Grafana dashboard.", "ships with a Grafana dashboard"),
                ("Compatible with Hootsuite exports.", "Compatible with Hootsuite exports"),
                ("The outbox includes an AI image generator.", "includes an AI image generator"),
                ("You can now schedule Instagram stories.", "can now schedule Instagram stories"),
                ("ReachWeaver added Threads publishing", "added Threads publishing")):
            with self.subTest(text=text):
                self.set_text(text)
                self.assertEqual([f["span"] for f in self.features()[1]], [span])

    def test_partly_supported_claim_names_only_missing_terms(self):
        self.set_text("ReachWeaver supports Mastodon and Buffer instances.")
        found = self.features()[1]
        self.assertEqual(found[0]["missing_terms"], ["Buffer"])
        self.assertEqual(found[0]["matched_evidence"], ["source-repo"])

    def test_feature_only_in_uncited_evidence_names_that_evidence(self):
        extra = deepcopy(self.evidence)
        extra.update({"id": "tiktok-docs", "excerpt": "TikTok scheduling is documented here."})
        self.campaign["evidence"].append(extra)
        self.set_text("ReachWeaver now supports TikTok scheduling.")
        self.assertIn("uncited evidence tiktok-docs", self.features()[1][0]["message"])
        self.campaign["variants"][0]["evidence_ids"].append("tiktok-docs")
        report, found = self.features()
        self.assertEqual(found, [])
        feature = next(g for g in report["variants"][0]["grounded"] if g["type"] == "feature")
        self.assertEqual(feature["matched_evidence"], ["tiktok-docs"])

    def test_feature_in_thread_part_is_flagged_with_body_offsets(self):
        variant = self.campaign["variants"][0]
        variant.pop("text")
        variant.update({"format": "thread", "parts": ["Approval first.", "It now integrates with Zapier."]})
        found = self.features()[1]
        self.assertEqual(found[0]["span"], "now integrates with Zapier")
        body = "Approval first.\n\nIt now integrates with Zapier."
        self.assertEqual(body[found[0]["start"]:found[0]["end"]], found[0]["span"])

    def test_preview_confidence_drops_to_medium_not_low(self):
        from reachweaver.preview import build_preview
        self.set_text("ReachWeaver now supports TikTok scheduling. https://github.com/MSKazemi/reachweaver")
        preview = build_preview(self.campaign, self.brand)
        self.assertEqual(preview["variants"][0]["confidence"]["level"], "medium")
        self.assertIn("feature", " ".join(preview["variants"][0]["confidence"]["rationale"]))

    # --- negative: supported or non-claims are not flagged ------------------------------------
    def test_supported_feature_is_grounded(self):
        self.set_text("ReachWeaver supports Mastodon instances and adds an approval queue.")
        report, found = self.features()
        self.assertEqual(found, [])
        spans = [g["span"] for g in report["variants"][0]["grounded"] if g["type"] == "feature"]
        self.assertEqual(spans, ["supports Mastodon instances", "adds an approval queue"])

    def test_case_and_simple_plurals_are_normalized(self):
        for text in ("Now supports MASTODON INSTANCE.", "Adds approval queues.", "Adds a cache for replies.",
                     "Adds policy checks.", "Adds policies.", "It now has Mastodon's instance support."):
            with self.subTest(text=text):
                self.set_text(text)
                self.assertEqual(self.features()[1], [])

    def test_stopwords_and_generic_words_are_ignored(self):
        for text in ("Works with you, not against you.", "It now has full native support for the queue.",
                     "Supports all of this, and more.", "Now with better, faster, easier features."):
            with self.subTest(text=text):
                self.set_text(text)
                self.assertEqual(self.features()[1], [])

    def test_negated_and_interrogative_claims_are_not_assertions(self):
        for text in ("ReachWeaver does not support TikTok scheduling.", "It doesn't integrate with TikTok.",
                     "ReachWeaver no longer supports TikTok.", "It never integrates with TikTok.",
                     "Does it now support TikTok?", "Which network should it support for TikTok fans?"):
            with self.subTest(text=text):
                self.set_text(text)
                self.assertEqual(self.features()[1], [])

    def test_plain_text_without_trigger_and_reference_example_are_clean(self):
        report = check_claims(read_json(ROOT / "examples/campaign.reachweaver.json"), self.brand)
        self.assertEqual(report["summary"]["review"], 0)
        self.set_text("TikTok scheduling would be great one day.")
        self.assertEqual(self.features()[1], [])

    # --- edge cases --------------------------------------------------------------------------
    def test_urls_numbers_and_hyphenated_words(self):
        self.set_text("Details: https://example.org/adds/tiktok-support")
        self.assertEqual(self.features()[1], [])  # URL path words are masked
        self.set_text("A value-added outbox.")
        self.assertEqual(self.features()[1], [])  # 'added' inside a hyphenated word is not a trigger
        self.set_text("Adds 3 TikTok exports.")
        report, found = self.features()
        self.assertEqual(found[0]["missing_terms"], ["TikTok", "exports"])  # the number is check 1's job
        self.assertIn("unsupported_number", codes(report))

    def test_window_stops_at_clause_and_sentence_boundaries(self):
        for text, missing in (("Adds an approval queue, which TikTok users love.", None),
                              ("Supports Mastodon instances; TikTok is next.", None),
                              ("Supports Mastodon instances because TikTok said so.", None),
                              ("Supports Mastodon instances and it runs on TikTok.", None),
                              ("Supports Mastodon instances and TikTok.", ["TikTok"])):
            with self.subTest(text=text):
                self.set_text(text)
                found = self.features()[1]
                self.assertEqual(found[0]["missing_terms"] if found else None, missing)
        self.set_text("Adds " + " ".join(f"word{n}" for n in range(20)))
        self.assertEqual(len(self.features()[1][0]["missing_terms"]), 8)  # bounded window

    def test_unicode_text_is_normalized(self):
        self.set_text("Ｎｏｗ ｓｕｐｐｏｒｔｓ Ｍａｓｔｏｄｏｎ instances.")
        self.assertEqual(self.features()[1], [])
        self.set_text("Now supports Ｔｉｋｔｏｋ.")
        self.assertEqual(self.features()[1][0]["missing_terms"], ["Ｔｉｋｔｏｋ"])

    def test_find_feature_claims_api(self):
        claims = find_feature_claims("It now integrates with Postiz and Buffer. Works with you.")
        self.assertEqual([(c["trigger"], c["terms"]) for c in claims],
                         [("now integrates with", ["Postiz", "Buffer"])])
        self.assertEqual(find_feature_claims(""), [])


if __name__ == "__main__":
    unittest.main()
