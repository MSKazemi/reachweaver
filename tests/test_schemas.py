"""Canonical schema 1.1 contracts (1.0 still valid) and the pure publish state machine; offline only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.schemas import (
    MIGRATIONS, SCHEMA_VERSION, SCHEMAS, STATES, STORAGE_STATUS_MAP, SUPPORTED_VERSIONS, TRANSITIONS, TYPES,
    migrate, payload_sha256, require_valid, transition_job, validate,
)
from reachweaver import storage

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples/schemas"
NOW = "2026-10-15T08:00:00Z"
CONSENT = {"subject": "account_owner", "obtained": True, "recorded_at": "2026-10-01T09:00:00Z"}


def load(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def env(doc_type, /, **fields):
    return {"schema_version": SCHEMA_VERSION, "type": doc_type, **fields}


MINIMAL = {
    "SourceSignal": env("SourceSignal", id="sig-1", brand_id="reachweaver", kind="paper", title="A paper",
                        doi="10.1145/3600006.3613139", observed_at="2026-10-08T10:00:00Z"),
    "Evidence": env("Evidence", id="ev-1", claim="The CLI is dry-run by default.",
                    url="https://github.com/MSKazemi/reachweaver", verification="primary_source_checked",
                    checked_by="owner", checked_at="2026-10-08T10:00:00Z"),
    "ContentBrief": env("ContentBrief", id="brief-1", brand_id="reachweaver", campaign_id="intro",
                        audience_id="maintainers", objective="contributors", locale="pt-BR",
                        key_message="Safe publishing", evidence_ids=["ev-1"],
                        utm={"source": "dev", "medium": "social", "campaign": "intro"},
                        created_at="2026-10-08T10:00:00Z"),
    "Draft": env("Draft", id="draft-1", brief_id="brief-1", brand_id="reachweaver", campaign_id="intro",
                 locale="zh-Hans-CN", body="Draft body", evidence_ids=["ev-1"], authorship="ai_assisted",
                 created_at="2026-10-08T10:00:00Z"),
    "MediaAsset": env("MediaAsset", id="m-1", kind="image", mime_type="image/png", uri="a.png",
                      sha256="a" * 64, bytes=10, alt_text="A chart", license="CC-BY-4.0",
                      people_depicted=False),
    "Account": env("Account", id="my-bluesky", platform="bluesky", brand_id="reachweaver",
                   display_name="Owner", handle="owner.bsky.social", env_prefix="REACHWEAVER_BSKY",
                   audience_ids=["maintainers"], consent=[CONSENT]),
    "PublishReceipt": env("PublishReceipt", id="rcpt-1", job_id="job-bluesky-intro-1", account_id="my-bluesky",
                          platform="bluesky", idempotency_key="reachweaver:job-bluesky-intro-1:v1",
                          outcome="published", remote_post_id="at://did:plc:abc/app.bsky.feed.post/3k",
                          evidence="api_response", observed_at="2026-10-15T08:00:05Z"),
    "AnalyticsSnapshot": env("AnalyticsSnapshot", id="snap-1", job_id="job-1", account_id="my-bluesky",
                             platform="bluesky", remote_post_id="at://x/y", observed_at="2026-10-16T08:00:00Z",
                             source="native-export", metrics={"views": 0, "clicks": None}),
    "Experiment": env("Experiment", id="exp-1", campaign_id="intro", hypothesis="Proof-led hooks help",
                      primary_metric="clicks", denominator="impressions", guardrail_metric="replies",
                      variant_ids=["a", "b"], age_hours=168, tolerance_hours=24, min_pairs=3,
                      status="planned", declared_at="2026-10-08T10:00:00Z"),
}


class SchemaValidation(unittest.TestCase):
    def test_samples_are_valid_and_cover_media_variants(self):
        names = sorted(p.name for p in SAMPLES.glob("*.json"))
        self.assertGreaterEqual(len(names), 3)
        formats = set()
        for name in names:
            doc = load(name)
            self.assertEqual(validate(doc["type"], doc), [], name)
            payload = doc.get("payload", doc)
            formats.add(payload.get("format"))
        self.assertTrue({"image", "video"} <= formats)

    def test_every_type_has_a_valid_minimal_document(self):
        docs = dict(MINIMAL, PlatformVariant=load("image-variant.json"),
                    Approval=load("text-post-approval.json"), PublishJob=load("text-post-job.json"))
        self.assertEqual(set(docs), set(TYPES))
        for kind, doc in docs.items():
            self.assertEqual(validate(kind, doc), [], kind)

    def assertInvalid(self, kind, doc, fragment):
        errors = validate(kind, doc)
        self.assertTrue(any(fragment in e for e in errors), (fragment, errors))

    def test_malformed_documents_are_rejected(self):
        ev = MINIMAL["Evidence"]
        cases = [
            ({k: v for k, v in ev.items() if k != "claim"}, "Evidence.claim: required"),
            (dict(ev, checked_at="2026-10-08T10:00:00+02:00"), "RFC 3339 UTC"),
            (dict(ev, checked_at="2026-13-40T10:00:00Z"), "RFC 3339 UTC"),
            (dict(ev, url="http://example.com/x"), "public https"),
            (dict(ev, url="https://example.com/x?token=abc"), "Evidence.url"),
            (dict(ev, verification="vibes"), "must be one of"),
            (dict(ev, surprise=1), "unknown field"),
            ({k: v for k, v in ev.items() if k != "url"}, "traceable url or doi"),
            (dict(ev, schema_version="2.0"), "run migrate()"),
            (dict(ev, type="Draft"), "expected Evidence"),
            ({k: v for k, v in ev.items() if k != "schema_version"}, "schema_version: required"),
        ]
        for doc, fragment in cases:
            self.assertInvalid("Evidence", doc, fragment)
        self.assertInvalid("Draft", dict(MINIMAL["Draft"], locale="english"), "locale: invalid format")
        self.assertInvalid("Draft", dict(MINIMAL["Draft"], evidence_ids=[]), "evidence_ids")
        self.assertInvalid("Experiment", dict(MINIMAL["Experiment"], age_hours=True), "integer")
        self.assertInvalid("Experiment", dict(MINIMAL["Experiment"], guardrail_metric="clicks"), "must differ")
        self.assertInvalid("AnalyticsSnapshot", dict(MINIMAL["AnalyticsSnapshot"], metrics={"views": None}),
                           "unknown, not zero")
        self.assertInvalid("AnalyticsSnapshot", dict(MINIMAL["AnalyticsSnapshot"], metrics={"views": -1}), "out of range")
        self.assertInvalid("PublishReceipt", {**MINIMAL["PublishReceipt"], "remote_post_id": None}, "must not be null")
        receipt = {k: v for k, v in MINIMAL["PublishReceipt"].items() if k != "remote_post_id"}
        self.assertInvalid("PublishReceipt", receipt, "required when published")
        self.assertInvalid("PublishReceipt", dict(MINIMAL["PublishReceipt"], outcome="failed"), "error_code")
        with self.assertRaises(ValidationError):
            validate("Tweet", {})
        self.assertInvalid("Evidence", [], "must be a JSON object")

    def test_account_rejects_secret_like_fields_and_needs_owner_consent(self):
        acct = MINIMAL["Account"]
        for key in ("access_token", "app_password", "api_key", "session_cookie"):
            self.assertInvalid("Account", dict(acct, **{key: "x"}), "secret-like")
        self.assertInvalid("Account", dict(acct, consent=[dict(CONSENT, obtained=False)]), "owner authorization")
        self.assertInvalid("Account", {k: v for k, v in acct.items() if k != "handle"}, "handle")
        self.assertInvalid("Account", dict(acct, platform="mastodon", server="http://127.0.0.1"), "server")
        variant = load("image-variant.json")
        variant["platform_options"] = {"nested": {"refresh_token": "x"}}
        self.assertInvalid("PlatformVariant", variant, "secret-like")

    def test_media_accessibility_rights_and_format_rules(self):
        image = load("image-variant.json")
        del image["media"][0]["alt_text"]
        self.assertInvalid("PlatformVariant", image, "alt_text: required")
        video = load("video-variant.json")
        video["media"][0]["consent"] = []
        self.assertInvalid("PlatformVariant", video, "depicted people")
        video = load("video-variant.json")
        del video["media"][0]["duration_seconds"]
        self.assertInvalid("PlatformVariant", video, "duration_seconds")
        video = load("video-variant.json")
        video["media"][0]["mime_type"] = "image/png"
        self.assertInvalid("PlatformVariant", video, "video/*")
        video = load("video-variant.json")
        video["format"] = "text"
        self.assertInvalid("PlatformVariant", video, "does not match format")
        image = load("image-variant.json")
        image["media"][0]["uri"] = "https://user:pw@example.com/a.png"
        self.assertInvalid("PlatformVariant", image, "public https")
        image = load("image-variant.json")
        image["vendor_field"] = 1
        self.assertInvalid("PlatformVariant", image, "platform_options")

    def test_cross_platform_payload_is_rejected(self):
        job = load("text-post-job.json")
        job["payload"]["text"] = "x" * 301  # within linkedin, beyond the 300-grapheme bluesky limit
        job["payload_sha256"] = payload_sha256(job["payload"])
        self.assertInvalid("PublishJob", job, "bluesky limit is 300")
        job = load("text-post-job.json")
        job["platform"] = "mastodon"
        self.assertInvalid("PublishJob", job, "does not match payload.platform")
        job = load("text-post-job.json")
        job["account_id"] = "other-bluesky"
        self.assertInvalid("PublishJob", job, "does not match payload.account_id")
        job = load("text-post-job.json")
        job["payload"]["platform"] = "hackernews"
        self.assertInvalid("PublishJob", job, "must be one of")

    def test_canonical_hash_is_key_order_independent(self):
        payload = load("text-post-job.json")["payload"]
        reordered = dict(reversed(list(payload.items())))
        self.assertEqual(payload_sha256(payload), payload_sha256(reordered))
        self.assertNotEqual(payload_sha256(payload), payload_sha256(dict(payload, text=payload["text"] + "!")))
        self.assertRegex(payload_sha256(payload), r"^[0-9a-f]{64}$")


WINDOW = {"window_start": "2026-10-09T00:00:00Z", "window_end": "2026-10-16T00:00:00+00:00"}


def snapshot(version=SCHEMA_VERSION, **fields):
    return dict(MINIMAL["AnalyticsSnapshot"], schema_version=version, **fields)


class SnapshotWindow(unittest.TestCase):
    """Schema 1.1: optional AnalyticsSnapshot observation window (minor, backwards compatible)."""

    def assertInvalid(self, doc, fragment):
        errors = validate("AnalyticsSnapshot", doc)
        self.assertTrue(any(fragment in e for e in errors), (fragment, errors))

    def test_version_policy_is_one_global_minor_bump(self):
        self.assertEqual(SCHEMA_VERSION, "1.2")
        self.assertEqual(SUPPORTED_VERSIONS, ("1.0", "1.1", "1.2"))
        self.assertEqual(set(MIGRATIONS), {"1.0", "1.1"})
        added = {(kind, name) for kind, fields in SCHEMAS.items()
                 for name, spec in fields.items() if spec.since != "1.0"}
        self.assertEqual(added, {("AnalyticsSnapshot", "window_start"), ("AnalyticsSnapshot", "window_end"),
                                 ("AnalyticsSnapshot", "metric_status")})
        self.assertTrue(all(not SCHEMAS[kind][name].required for kind, name in added))  # rule 2: optional only

    def test_1_0_documents_stay_valid(self):
        self.assertEqual(validate("AnalyticsSnapshot", snapshot("1.0")), [])
        for name in ("text-post-job.json", "text-post-approval.json", "image-variant.json", "video-variant.json"):
            doc = load(name)
            self.assertEqual(doc["schema_version"], "1.0", name)
            self.assertEqual(validate(doc["type"], doc), [], name)
        # A 1.0 document cannot use a field that 1.0 did not define.
        self.assertInvalid(snapshot("1.0", **WINDOW), "added in schema 1.1")

    def test_1_1_snapshot_with_window_is_valid(self):
        self.assertEqual(validate("AnalyticsSnapshot", snapshot(**WINDOW)), [])
        self.assertEqual(validate("AnalyticsSnapshot", snapshot()), [])  # no window = unknown window
        sample = load("analytics-snapshot-window.json")
        self.assertEqual((sample["schema_version"], sample["type"]), ("1.1", "AnalyticsSnapshot"))
        self.assertEqual(validate("AnalyticsSnapshot", sample), [])
        instant = snapshot(window_start="2026-10-16T08:00:00Z", window_end="2026-10-16T08:00:00Z")
        self.assertEqual(validate("AnalyticsSnapshot", instant), [])  # start == end == observed_at

    def test_half_window_is_invalid(self):
        self.assertInvalid(snapshot(window_start=WINDOW["window_start"]), "both window_start and window_end")
        self.assertInvalid(snapshot(window_end=WINDOW["window_end"]), "both window_start and window_end")
        self.assertInvalid(snapshot(window_start=None, window_end=WINDOW["window_end"]), "must not be null")

    def test_start_after_end_is_invalid(self):
        self.assertInvalid(snapshot(window_start="2026-10-12T00:00:00Z", window_end="2026-10-11T23:59:59Z"),
                           "window_start: must not be after window_end")

    def test_end_after_observed_at_is_invalid(self):
        self.assertInvalid(snapshot(window_start="2026-10-09T00:00:00Z", window_end="2026-10-16T08:00:01Z"),
                           "window_end: must not be after observed_at")

    def test_window_bounds_must_be_rfc3339_utc(self):
        self.assertInvalid(snapshot(window_start="2026-10-09T02:00:00+02:00", window_end=WINDOW["window_end"]),
                           "RFC 3339 UTC")
        self.assertInvalid(snapshot(window_start="2026-10-09", window_end=WINDOW["window_end"]), "RFC 3339 UTC")

    def test_migrate_1_0_to_1_1_is_identity_plus_version(self):
        old = snapshot("1.0")
        migrated = migrate(old)
        self.assertEqual(migrated, dict(old, schema_version=SCHEMA_VERSION))
        self.assertEqual(migrate(snapshot("1.1", **WINDOW)), snapshot(SCHEMA_VERSION, **WINDOW))
        self.assertEqual(old["schema_version"], "1.0")  # input untouched
        self.assertEqual(validate("AnalyticsSnapshot", migrated), [])
        self.assertEqual(migrate(migrated), migrated)  # current version is the identity path

    def test_migrated_job_keeps_its_approved_payload_hash(self):
        job, approval = load("text-post-job.json"), load("text-post-approval.json")
        migrated = migrate(job)
        self.assertEqual(migrated["schema_version"], SCHEMA_VERSION)
        self.assertEqual(migrated["payload"], job["payload"])  # embedded 1.0 payload is not rewritten
        self.assertEqual(payload_sha256(migrated["payload"]), approval["approved_payload_sha256"])
        self.assertEqual(validate("PublishJob", migrated), [])
        moved = transition_job(migrated, "publishing", approval=approval, now=NOW)
        self.assertEqual(moved["state"], "publishing")
        newer_payload = copy.deepcopy(job)
        newer_payload["payload"]["schema_version"] = "1.1"
        newer_payload["payload_sha256"] = payload_sha256(newer_payload["payload"])
        errors = validate("PublishJob", newer_payload)
        self.assertTrue(any("newer than its container" in e for e in errors), errors)


STATUS = {"likes": {"status": "suppressed"}, "impressions": {"status": "estimated", "value": 1200},
          "saves": {"status": "not_available"}, "clicks": {"status": "delayed"}}


class SnapshotMetricStatus(unittest.TestCase):
    """Schema 1.2 (#15): optional per-metric status; 1.0 and 1.1 documents stay valid."""

    def assertInvalid(self, doc, fragment):
        errors = validate("AnalyticsSnapshot", doc)
        self.assertTrue(any(fragment in e for e in errors), (fragment, errors))

    def test_1_2_snapshot_with_statuses_is_valid(self):
        self.assertEqual(validate("AnalyticsSnapshot", snapshot(metric_status=STATUS)), [])
        sample = load("analytics-snapshot-status.json")
        self.assertEqual((sample["schema_version"], sample["type"]), ("1.2", "AnalyticsSnapshot"))
        self.assertEqual(validate("AnalyticsSnapshot", sample), [])
        # Status-only snapshot: an observation that every value was withheld is still evidence.
        only = snapshot(metrics={}, metric_status={"views": {"status": "suppressed"}})
        self.assertEqual(validate("AnalyticsSnapshot", only), [])

    def test_older_versions_stay_valid_and_cannot_carry_status(self):
        for version in ("1.0", "1.1"):
            with self.subTest(version=version):
                self.assertEqual(validate("AnalyticsSnapshot", snapshot(version)), [])
                self.assertInvalid(snapshot(version, metric_status=STATUS), "added in schema 1.2")
        self.assertEqual(validate("AnalyticsSnapshot", migrate(snapshot("1.0"))), [])

    def test_status_rules(self):
        self.assertInvalid(snapshot(metric_status={"views": {"status": "estimated"}}), "needs the platform's estimate")
        self.assertInvalid(snapshot(metric_status={"views": {"status": "suppressed", "value": 3}}),
                           "only an estimated status carries a value")
        self.assertInvalid(snapshot(metric_status={"views": {"status": "zero"}}), "must be one of")
        self.assertInvalid(snapshot(metric_status={"reach": {"status": "suppressed"}}), "unknown field")
        self.assertInvalid(snapshot(metric_status={"views": {"status": "estimated", "value": -1}}), "out of range")
        exact = MINIMAL["AnalyticsSnapshot"]["metrics"]
        both = next(k for k, v in exact.items() if v is not None)
        self.assertInvalid(snapshot(metric_status={both: {"status": "delayed"}}), "not both")
        self.assertInvalid(snapshot(metrics={}, metric_status={}), "at least one observed metric")


class Migration(unittest.TestCase):
    def test_identity_and_unknown_versions(self):
        doc = dict(load("text-post-job.json"), schema_version=SCHEMA_VERSION)
        migrated = migrate(doc)
        self.assertEqual(migrated, doc)
        self.assertIsNot(migrated, doc)
        migrated["payload"]["text"] = "changed"
        self.assertNotEqual(doc["payload"]["text"], "changed")
        for version in ("0.9", "2.0", None, 1, ["1.0"]):
            with self.assertRaisesRegex(ValidationError, "Unsupported schema_version"):
                migrate(dict(doc, schema_version=version))


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.job = load("text-post-job.json")
        self.approval = load("text-post-approval.json")
        self.receipt = MINIMAL["PublishReceipt"]

    def draft_job(self):
        job = copy.deepcopy(self.job)
        job["state"] = "draft"
        del job["approval_id"]
        return job

    def test_happy_path_and_lifecycle_table(self):
        self.assertEqual(set(TRANSITIONS), set(STATES))
        job = transition_job(self.draft_job(), "reviewed", now=NOW)
        job["approval_id"] = self.approval["id"]
        job = transition_job(job, "approved", approval=self.approval, now=NOW)
        job = transition_job(job, "scheduled", approval=self.approval, now=NOW)
        job = transition_job(job, "publishing", approval=self.approval, account=MINIMAL["Account"], now=NOW)
        self.assertEqual(job["attempts"], 1)
        done = transition_job(job, "published", receipt=self.receipt, now=NOW)
        self.assertEqual(done["state"], "published")
        self.assertEqual(done["remote_post_id"], self.receipt["remote_post_id"])
        self.assertEqual(job["state"], "publishing")  # pure: input untouched

    def test_unapproved_publication_is_rejected(self):
        draft = self.draft_job()
        with self.assertRaisesRegex(ValidationError, "Illegal transition draft -> publishing"):
            transition_job(draft, "publishing", approval=self.approval, now=NOW)
        reviewed = transition_job(draft, "reviewed", now=NOW)
        with self.assertRaisesRegex(ValidationError, "Illegal transition reviewed -> publishing"):
            transition_job(reviewed, "publishing", now=NOW)
        with self.assertRaisesRegex(ValidationError, "not approved"):
            transition_job(self.job, "publishing", now=NOW)
        revoked = dict(self.approval, decision="revoked")
        with self.assertRaisesRegex(ValidationError, "revoked"):
            transition_job(self.job, "publishing", approval=revoked, now=NOW)
        ai_review = dict(self.approval, reviewer_kind="agent")
        with self.assertRaises(ValidationError):
            transition_job(self.job, "publishing", approval=ai_review, now=NOW)
        with self.assertRaisesRegex(ValidationError, "expired"):
            transition_job(self.job, "publishing", approval=self.approval, now="2026-10-23T00:00:00Z")
        with self.assertRaisesRegex(ValidationError, "Emergency pause"):
            transition_job(self.job, "publishing", approval=self.approval, paused=True, now=NOW)
        scheduled = transition_job(self.job, "scheduled", approval=self.approval, now=NOW)
        with self.assertRaisesRegex(ValidationError, "not due"):
            transition_job(scheduled, "publishing", approval=self.approval, now="2026-10-15T06:59:59Z")

    def test_wrong_account_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Wrong account"):
            transition_job(self.job, "publishing", approval=dict(self.approval, account_id="other-bluesky"), now=NOW)
        with self.assertRaisesRegex(ValidationError, "Wrong account"):
            transition_job(self.job, "publishing", approval=self.approval,
                           account=dict(MINIMAL["Account"], id="other-bluesky"), now=NOW)

    def test_cross_platform_approval_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Cross-platform"):
            transition_job(self.job, "publishing", approval=dict(self.approval, platform="mastodon"), now=NOW)
        mastodon = dict(MINIMAL["Account"], platform="mastodon", server="https://mastodon.social")
        with self.assertRaisesRegex(ValidationError, "Wrong account"):
            transition_job(self.job, "publishing", approval=self.approval, account=mastodon, now=NOW)

    def test_payload_hash_change_after_approval_is_rejected(self):
        tampered = copy.deepcopy(self.job)
        tampered["payload"]["text"] += " Now with an unreviewed claim."
        with self.assertRaisesRegex(ValidationError, "payload_sha256"):
            transition_job(tampered, "publishing", approval=self.approval, now=NOW)
        tampered["payload_sha256"] = payload_sha256(tampered["payload"])  # self-consistent but not approved
        with self.assertRaisesRegex(ValidationError, "changed after approval"):
            transition_job(tampered, "publishing", approval=self.approval, now=NOW)

    def test_replay_after_success_and_no_blind_retry(self):
        publishing = transition_job(self.job, "publishing", approval=self.approval, now=NOW)
        done = transition_job(publishing, "published", receipt=self.receipt, now=NOW)
        for target in ("publishing", "published"):
            with self.assertRaisesRegex(ValidationError, "Replay rejected"):
                transition_job(done, target, approval=self.approval, receipt=self.receipt, now=NOW)
        with self.assertRaisesRegex(ValidationError, "Replay rejected: idempotency"):
            transition_job(self.job, "publishing", approval=self.approval, prior_receipts=[self.receipt], now=NOW)
        unknown_receipt = {k: v for k, v in self.receipt.items() if k != "remote_post_id"}
        unknown_receipt.update(id="rcpt-2", outcome="unknown", error_code="timeout_uncertain", evidence="none")
        unknown = transition_job(publishing, "unknown", receipt=unknown_receipt, now=NOW)
        with self.assertRaisesRegex(ValidationError, "Illegal transition unknown -> publishing"):
            transition_job(unknown, "publishing", approval=self.approval, now=NOW)
        reconciled = transition_job(unknown, "published", receipt=dict(self.receipt, evidence="owner_verified_native_account"), now=NOW)
        self.assertNotIn("error_code", reconciled)
        failed_receipt = dict(unknown_receipt, outcome="failed", error_code="confirmed_not_published")
        failed = transition_job(unknown, "failed", receipt=failed_receipt, now=NOW)
        redraft = transition_job(failed, "draft", now=NOW)
        self.assertNotIn("approval_id", redraft)
        with self.assertRaisesRegex(ValidationError, "Receipt job_id"):
            transition_job(publishing, "published", receipt=dict(self.receipt, job_id="job-other"), now=NOW)
        with self.assertRaisesRegex(ValidationError, "Receipt outcome"):
            transition_job(publishing, "failed", receipt=self.receipt, now=NOW)

    def test_storage_status_map_covers_existing_statuses(self):
        statuses = set(re.search(r"status IN \(([^)]*)\)", storage.SCHEMA).group(1).replace("'", "").split(","))
        self.assertEqual(set(STORAGE_STATUS_MAP), statuses)
        self.assertTrue(set(STORAGE_STATUS_MAP.values()) <= set(STATES))


class SchemaCli(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()

    def test_schema_validate_ok_and_error_exit_codes(self):
        code, out = self.run_cli("schema-validate", "--type", "PlatformVariant", str(SAMPLES / "video-variant.json"))
        self.assertEqual(code, 0)
        self.assertIn("OK: PlatformVariant 1.0", out)
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.json"
            bad.write_text(json.dumps(dict(MINIMAL["Account"], api_key="x")), encoding="utf-8")
            code, out = self.run_cli("schema-validate", "--type", "Account", str(bad))
            self.assertEqual(code, 1)
            self.assertIn("secret-like", out)
            bad.write_text("{not json", encoding="utf-8")
            self.assertEqual(self.run_cli("schema-validate", "--type", "Account", str(bad))[0], 2)

    def test_require_valid_raises(self):
        with self.assertRaises(ValidationError):
            require_valid("Draft", {})


if __name__ == "__main__":
    unittest.main()
