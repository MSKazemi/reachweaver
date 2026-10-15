"""Reply drafts are checked against owner evidence with the lexical claims check (public issue #5).

Offline: no network, no send path, drafts stay ``draft_requires_approval``.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.engagement import (
    CLAIMS_CHECK_LIMITATION, DRAFT_STATUS, check_reply_drafts, load_reply_evidence, render_markdown,
    triage_engagement,
)
from reachweaver.policy import ValidationError
from tests.test_engagement import DOCS, EXPORT, export, item, load

DOCS_MAP = "reachweaver.docs-map.v1"
RELEASE = {"id": "rel", "kind": "release", "url": "https://github.com/MSKazemi/reachweaver/releases/tag/v0.2.0",
           "claim": "ReachWeaver v0.2.0 was released and supports 4 text adapters.",
           "excerpt": "ReachWeaver v0.2.0 was released and supports 4 text adapters."}


def docs_map(templates=None, evidence=None, topics=None):
    doc = {"schema_version": DOCS_MAP,
           "topics": topics if topics is not None else
           [{"topic": "Docs", "keywords": ["docs map"], "url": "https://example.org/docs/engagement"}]}
    if templates:
        doc["templates"] = templates
    if evidence is not None:
        doc["evidence"] = evidence
    return doc


QUESTION = export(item("q1", "How do I configure the docs map?"))


def drafts_for(templates=None, evidence=None, data=QUESTION):
    return triage_engagement(data, docs_map(templates, evidence))


class BuiltInTemplateTests(unittest.TestCase):
    def test_every_fixture_draft_is_clean_without_any_evidence(self):
        report = triage_engagement(load(EXPORT), load(DOCS))
        self.assertTrue(report["drafts"])
        for draft in report["drafts"]:
            self.assertEqual(draft["claims_check"]["status"], "clean", (draft["draft_id"], draft["claims_check"]))
        summary = report["claims_check"]
        self.assertEqual((summary["checked"], summary["clean"], summary["review"], summary["error"]),
                         (len(report["drafts"]), len(report["drafts"]), 0, 0))
        self.assertEqual(summary["limitation"], CLAIMS_CHECK_LIMITATION)

    def test_checking_never_changes_the_draft_or_its_status(self):
        report = triage_engagement(load(EXPORT), load(DOCS))
        for draft in report["drafts"]:
            self.assertEqual(draft["status"], DRAFT_STATUS)
            self.assertFalse(draft["approval"]["approved"])
        self.assertEqual(report["status"], "triage_only_no_send")


class OwnerTemplateTests(unittest.TestCase):
    def test_an_unsupported_figure_in_an_owner_template_is_an_error(self):
        report = drafts_for({"en": {"technical_question": "Yes, we cover 99 platforms. {docs}"}})
        check = report["drafts"][0]["claims_check"]
        self.assertEqual(check["status"], "error")
        self.assertIn("unsupported_number", {f["code"] for f in check["findings"]})
        self.assertEqual(report["claims_check"]["error"], 1)
        self.assertEqual(report["drafts"][0]["status"], DRAFT_STATUS)

    def test_a_figure_supported_by_listed_evidence_is_clean(self):
        template = {"en": {"technical_question": "It supports 4 text adapters today. {docs}"}}
        self.assertEqual(drafts_for(template, [RELEASE])["drafts"][0]["claims_check"]["status"], "clean")
        self.assertEqual(drafts_for(template, [])["drafts"][0]["claims_check"]["status"], "error")

    def test_release_language_needs_release_evidence(self):
        template = {"en": {"technical_question": "That was released last week. {docs}"}}
        bare = drafts_for(template)["drafts"][0]["claims_check"]
        self.assertEqual((bare["status"], [f["code"] for f in bare["findings"]]), ("error", ["unsupported_release"]))
        listed = drafts_for(template, [RELEASE])["drafts"][0]["claims_check"]
        self.assertEqual(listed["status"], "clean")

    def test_superlatives_and_feature_claims_need_human_review(self):
        template = {"en": {"technical_question": "It is the fastest tool and now supports TikTok scheduling. {docs}"}}
        check = drafts_for(template)["drafts"][0]["claims_check"]
        self.assertEqual(check["status"], "review")
        self.assertEqual({f["severity"] for f in check["findings"]}, {"review"})
        self.assertEqual(drafts_for(template)["claims_check"]["review"], 1)

    def test_warnings_that_do_not_apply_to_replies_are_not_reported(self):
        report = drafts_for(None, None, export(item("a", "How do I configure the docs map?"),
                                               item("b", "How do I configure the docs map?",
                                                    in_reply_to=None,
                                                    url="https://bsky.app/profile/y.example/post/b")))
        for draft in report["drafts"]:
            self.assertEqual(draft["claims_check"]["findings"], [])


class EvidenceLoaderTests(unittest.TestCase):
    def test_valid_evidence_round_trips_and_is_optional(self):
        self.assertEqual(load_reply_evidence(None), [])
        self.assertEqual(load_reply_evidence(docs_map()), [])
        loaded = load_reply_evidence(docs_map(evidence=[RELEASE]))
        self.assertEqual(loaded[0]["id"], "rel")
        self.assertEqual(loaded[0]["kind"], "release")

    def test_bad_evidence_is_refused(self):
        bad = [
            "not-a-list",
            ["not-an-object"],
            [{"id": "has space", "claim": "x"}],
            [{"id": "a", "claim": ""}],
            [{"id": "a", "claim": "x"}, {"id": "a", "claim": "y"}],
            [{"id": "a", "claim": "x", "kind": "testimonial"}],
            [{"id": "a", "claim": "x", "url": "http://example.org"}],
            [{"id": "a", "claim": "x", "url": "https://user:pw@example.org/x"}],
            [{"id": "a", "claim": "x", "excerpt": "e" * 5001}],
            [{"id": f"e{n}", "claim": "x"} for n in range(51)],
        ]
        for evidence in bad:
            with self.subTest(evidence=str(evidence)[:60]), self.assertRaises(ValidationError):
                load_reply_evidence(docs_map(evidence=evidence))


class RenderingAndCliTests(unittest.TestCase):
    def test_markdown_shows_the_status_and_findings(self):
        report = drafts_for({"en": {"technical_question": "We cover 99 platforms. {docs}"}})
        text = render_markdown(report)
        self.assertIn("Claims check (lexical, not truth): error", text)
        self.assertIn("unsupported_number", text)

    def test_check_reply_drafts_handles_an_empty_list(self):
        summary = check_reply_drafts([], [])
        self.assertEqual((summary["checked"], summary["clean"], summary["review"], summary["error"]), (0, 0, 0, 0))

    def test_cli_triage_json_carries_claims_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "in.json").write_text(json.dumps(QUESTION), encoding="utf-8")
            (tmp / "docs.json").write_text(json.dumps(docs_map(
                {"en": {"technical_question": "We cover 99 platforms. {docs}"}})), encoding="utf-8")
            out = tmp / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["engagement-triage", str(tmp / "in.json"), "--docs-map", str(tmp / "docs.json"),
                             "--out", str(out)])
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["claims_check"]["error"], 1)
            self.assertEqual(report["drafts"][0]["claims_check"]["status"], "error")


if __name__ == "__main__":
    unittest.main()
