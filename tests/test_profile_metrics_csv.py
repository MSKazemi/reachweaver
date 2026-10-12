"""Owner-consented local CSV metric overlay: strict provenance, no network or writes."""
from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import audit_posts, load_snapshot
from reachweaver.profile_metrics_csv import merge_authorized_csv
from reachweaver.suggest import build_suggestions

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/profile-post-audit.example.json"
CSV = ROOT / "examples/profile-post-metrics.example.csv"

HEADER = "platform,account_id,post_id,format,source,published_at,observed_at,clicks,impressions,hook_clear\n"
ROW = "linkedin,sample-linkedin,new-test,text,owner-native-export,2026-09-06T10:00:00+02:00,2026-09-13T10:00:00+02:00,2,100,true\n"


class CsvProfileAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_snapshot(EXAMPLE)

    def overlay(self, text: str, *, authorized: bool=True):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "native.csv"
            path.write_text(text, encoding="utf-8")
            return merge_authorized_csv(self.snapshot, path, owner_authorized=authorized)

    def test_explicit_owner_acknowledgement_must_precede_read(self):
        with self.assertRaisesRegex(ValidationError, "owner-authorized"):
            merge_authorized_csv(self.snapshot, CSV, owner_authorized=False)
        with self.assertRaises(ValidationError):
            self.overlay(HEADER + ROW, authorized=False)

    def test_official_source_is_user_label_not_proof(self):
        result, evidence=merge_authorized_csv(self.snapshot, CSV, owner_authorized=True)
        self.assertEqual(evidence["rows_added"],3)
        self.assertEqual(evidence["total_posts"],len(self.snapshot["posts"])+3)
        self.assertTrue(evidence["acknowledgement_is_not_independent_ownership_verification"])
        self.assertEqual(evidence["platforms"],{"linkedin":2,"youtube":1})
        self.assertEqual(len(self.snapshot["posts"]),4)  # input not mutated
        self.assertEqual(len(result["posts"]),7)
        self.assertEqual(result["posts"][-1]["metrics"]["views"],240)
        self.assertNotIn("clicks", result["posts"][-1]["metrics"])
        self.assertEqual(result["posts"][-1]["review_flags"]["hook_clear"],True)
        audit=audit_posts(result, platform="linkedin")
        self.assertEqual(audit["format_cohorts"][0]["sample_size"],5)

    def test_csv_requires_exact_profile_account_identity(self):
        with self.assertRaisesRegex(ValidationError,"unknown or unapproved"):
            self.overlay(HEADER + ROW.replace("sample-linkedin", "some-other-account"))
        with self.assertRaisesRegex(ValidationError,"unknown or unapproved"):
            self.overlay(HEADER + ROW.replace("linkedin", "mastodon"))

    def test_duplicate_post_in_csv_or_original_is_rejected(self):
        with self.assertRaisesRegex(ValidationError,"Duplicate native"):
            self.overlay(HEADER + ROW + ROW)
        with self.assertRaisesRegex(ValidationError,"Duplicate native"):
            self.overlay(HEADER + ROW.replace("new-test","sample-p1"))
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/"duplicate.json"
            duplicated={**self.snapshot,"posts":[*self.snapshot["posts"],self.snapshot["posts"][0]]}
            p.write_text(json.dumps(duplicated), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError,"Duplicate post observation"):
                load_snapshot(p)

    def test_bad_header_and_sensitive_data_fail_closed(self):
        for payload in (
            "account_id,post_id,platform\nsample-linkedin,new-test,linkedin\n",
            HEADER.replace("impressions","api_key") + ROW,
            HEADER.replace("clicks","clicks,clicks") + ROW,
            HEADER + ROW.replace("new-test","ghp_FAKESECRET123456"),
        ):
            with self.subTest(payload=payload[:35]):
                with self.assertRaises(ValidationError):
                    self.overlay(payload)

    def test_datetime_timezone_and_observation_order(self):
        with self.assertRaisesRegex(ValidationError, "timezone"):
            self.overlay(HEADER + ROW.replace("2026-09-06T10:00:00+02:00","2026-09-06T10:00:00"))
        with self.assertRaisesRegex(ValidationError,"precede"):
            self.overlay(HEADER + ROW.replace("2026-09-13T10:00:00+02:00","2026-09-01T10:00:00+02:00"))
        with self.assertRaisesRegex(ValidationError,"ISO 8601"):
            self.overlay(HEADER + ROW.replace("2026-09-06T10:00:00+02:00","not-a-date"))

    def test_numeric_counts_must_be_nonnegative_and_not_floats(self):
        for invalid in ("-1","nan","inf","1.5","1e5","1000000000001"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    self.overlay(HEADER + ROW.replace(",2,100,true",f",{invalid},100,true"))
        merged, evidence=self.overlay(HEADER + ROW.replace(",2,100,true",",0,,"))
        self.assertEqual(merged["posts"][-1]["metrics"]["clicks"],0)
        self.assertNotIn("impressions",merged["posts"][-1]["metrics"])
        self.assertEqual(merged["posts"][-1]["review_flags"],{})

    def test_manual_flags_must_be_true_false_or_blank(self):
        with self.assertRaisesRegex(ValidationError,"hook_clear"):
            self.overlay(HEADER + ROW.replace(",true\n",",maybe\n"))

    def test_empty_oversize_symlink_refused_without_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            empty=base/"empty.csv"
            empty.write_text(HEADER)
            with self.assertRaisesRegex(ValidationError,"no native"):
                merge_authorized_csv(self.snapshot,empty,owner_authorized=True)
            big=base/"large.csv"
            big.write_text("x"*2_000_001)
            with self.assertRaises(ValidationError):
                merge_authorized_csv(self.snapshot,big,owner_authorized=True)
            symlink=base/"symlink.csv"
            symlink.symlink_to(CSV)
            with self.assertRaises(ValidationError):
                merge_authorized_csv(self.snapshot,symlink,owner_authorized=True)

    def test_no_network_no_db_no_change_when_reading_csv_with_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"should-not-create.sqlite"
            commands=[
                ["post-audit","--file",str(EXAMPLE),"--metrics-csv",str(CSV),
                 "--owner-authorized","--platform","linkedin","--json"],
                ["suggest","--snapshot",str(EXAMPLE),"--metrics-csv",str(CSV),
                 "--owner-authorized","--json"],
            ]
            for args in commands:
                out=io.StringIO()
                with patch.object(socket,"create_connection",side_effect=AssertionError("network was used")), \
                     contextlib.redirect_stdout(out):
                    self.assertEqual(main(["--db",str(db),*args]),0)
                result=json.loads(out.getvalue())
                if args[0]=="post-audit":
                    self.assertEqual(result["normalized_csv"]["rows_added"],3)
                else:
                    self.assertEqual(result["source_coverage"]["normalized_csv"]["rows_added"],3)
                    self.assertIn("owner_authorized_local_csv_readonly",result["sources_used"])
                self.assertFalse(db.exists())

    def test_cli_refuses_unacknowledged_or_orphan_csv(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["post-audit","--file",str(EXAMPLE),"--metrics-csv",str(CSV)]),2)
            self.assertEqual(main(["suggest","--snapshot",str(EXAMPLE),"--metrics-csv",str(CSV)]),2)
            self.assertEqual(main(["suggest","--metrics-csv",str(CSV),"--owner-authorized"]),2)
            self.assertEqual(main(["suggest","--owner-authorized","--snapshot",str(EXAMPLE)]),2)

    def test_malformed_extra_columns_do_not_create_partial_snapshot(self):
        with self.assertRaises(ValidationError):
            self.overlay(HEADER + ROW.rstrip("\n") + ",extra\n")
        self.assertEqual(len(self.snapshot["posts"]),4)


if __name__=="__main__":
    unittest.main()
