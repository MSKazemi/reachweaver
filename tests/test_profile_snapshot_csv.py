"""Profile snapshots as CSV and the advisory cross-platform consistency check (public issue #3, offline)."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.profile_audit import (audit_profiles, cross_platform_consistency, load_snapshot, render_summary,
                                       snapshot_from_csv)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CSV = ROOT / "examples/profile-snapshot.example.csv"
HEADER = "platform,account_id,handle,profile_kind,keywords,field,value\n"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ProfileCsvTests(unittest.TestCase):
    def write(self, text: str, name: str = "profiles.csv") -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_example_loads_and_blank_means_unknown(self):
        data = load_snapshot(EXAMPLE_CSV)
        self.assertEqual([p["platform"] for p in data["profiles"]], ["linkedin", "devto"])
        linkedin = data["profiles"][0]
        self.assertEqual(linkedin["keywords"], ["Kubernetes", "observability"])
        self.assertIs(linkedin["fields"]["avatar"], True)
        self.assertIs(linkedin["fields"]["featured"], False)
        self.assertIsNone(linkedin["fields"]["banner"])  # blank cell: unknown, not false
        self.assertEqual((data["brand_name"], data["preferred_handle"]),
                         ("ExampleProject (illustrative only)", "sample-dev"))

    def test_csv_and_json_snapshots_give_the_same_audit(self):
        data = load_snapshot(EXAMPLE_CSV)
        as_json = self.write(json.dumps(data), "same.json")
        self.assertEqual(audit_profiles(load_snapshot(as_json)), audit_profiles(data))

    def test_cli_reads_a_csv_without_network_or_state(self):
        code, out, err = run_cli("profile-audit", "--file", str(EXAMPLE_CSV), "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["platforms_covered"], 2)
        self.assertEqual(result["cross_platform_consistency"]["profiles_compared"], 2)
        self.assertEqual([r["platform"] for r in result["reports"]], ["linkedin", "devto"])
        code, out, _ = run_cli("profile-audit", "--file", str(EXAMPLE_CSV))
        self.assertEqual(code, 0)
        self.assertIn("Cross-platform consistency (advisory, 2 profiles compared)", out)

    def test_malformed_csv_fails_closed_without_echoing_the_row(self):
        probe = "private-looking-value-xyz"
        cases = {
            "header": "platform,account_id\nlinkedin,a1\n",
            "unknown field": HEADER + f"linkedin,a1,me,personal,,nope,{probe}\n",
            "bad bool": HEADER + f"linkedin,a1,me,personal,,avatar,{probe}\n",
            "duplicate field": HEADER + "linkedin,a1,me,personal,,bio,x\nlinkedin,a1,,,,bio,y\n",
            "contradicting handle": HEADER + "linkedin,a1,me,personal,,bio,x\nlinkedin,a1,other,,,avatar,true\n",
            "missing handle": HEADER + "linkedin,a1,,personal,,bio,x\n",
            "unsupported platform": HEADER + "myspace,a1,me,personal,,bio,x\n",
            "value without field": HEADER + "linkedin,a1,me,personal,,,orphan\n",
            "short row": HEADER + "linkedin,a1,me\n",
            "no rows": HEADER,
            "credential-like value": HEADER + "linkedin,a1,me,personal,,bio," + "ghp" + "_" + "a1" * 18 + "\n",
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValidationError) as caught:
                    load_snapshot(self.write(text))
                self.assertNotIn(probe, str(caught.exception))
                self.assertNotIn("ghp" + "_", str(caught.exception))

    def test_nul_bytes_oversize_and_row_limit_are_refused(self):
        with self.assertRaises(ValidationError):
            snapshot_from_csv(HEADER + "linkedin,a1,me,personal,,bio,a\x00b\n")
        rows = "".join(f"linkedin,a{i % 50},me,personal,,bio,{i}\n" for i in range(2001))
        with self.assertRaises(ValidationError):
            snapshot_from_csv(HEADER + rows)

    def test_a_json_file_is_unchanged_by_the_csv_path(self):
        data = load_snapshot(ROOT / "examples/profile-post-audit.example.json")
        self.assertEqual(len(data["profiles"]), 3)
        self.assertIn("posts", data)


class CrossPlatformConsistencyTests(unittest.TestCase):
    def profile(self, platform, handle, **fields):
        return {"platform": platform, "account_id": f"{platform}-1", "handle": handle, "profile_kind": "personal",
                "fields": fields}

    def kinds(self, result):
        return {f["kind"] for f in result["findings"]}

    def test_agreeing_profiles_produce_no_finding(self):
        a = self.profile("linkedin", "sample-dev", display_name="Example Developer", website="https://www.example.org/a",
                         bio="Practical Kubernetes lessons")
        b = self.profile("devto", "@Sample-Dev", display_name="  example   developer ", website="https://example.org/b",
                         bio="Kubernetes tutorials")
        result = cross_platform_consistency([a, b])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["profiles_compared"], 2)

    def test_each_difference_is_reported_as_advisory(self):
        a = self.profile("linkedin", "alice", display_name="Alice A", website="https://example.org", bio="Rust compilers")
        b = self.profile("devto", "bob", display_name="Bob B", website="https://example.net", bio="Gardening stories")
        result = cross_platform_consistency([a, b])
        self.assertEqual(self.kinds(result), {"display_name_differs", "handle_differs", "website_host_differs",
                                              "bio_wording_unshared"})
        self.assertIn("never part of a score", result["basis"])

    def test_unknown_fields_are_skipped_and_listed_not_guessed(self):
        a = self.profile("linkedin", "same", display_name="Same Name")
        b = self.profile("devto", "same", display_name=None)
        result = cross_platform_consistency([a, b])
        self.assertEqual(result["findings"], [])
        fields = {(n["profile"], n["field"]) for n in result["not_compared"]}
        self.assertIn(("devto:devto-1", "display_name"), fields)
        self.assertIn(("linkedin:linkedin-1", "website"), fields)
        self.assertIn(("devto:devto-1", "bio"), fields)

    def test_only_profiles_of_the_same_kind_are_compared(self):
        a = self.profile("linkedin", "alice", display_name="Alice")
        b = {**self.profile("youtube", "acme", display_name="Acme"), "profile_kind": "organization"}
        result = cross_platform_consistency([a, b])
        self.assertEqual((result["findings"], result["profiles_compared"]), ([], 0))

    def test_it_does_not_change_scores_and_ignores_the_platform_filter(self):
        data = load_snapshot(EXAMPLE_CSV)
        full = audit_profiles(data)
        one = audit_profiles(data, platform="linkedin")
        self.assertEqual(full["reports"][0]["readiness_heuristic_score"], one["reports"][0]["readiness_heuristic_score"])
        self.assertEqual(full["cross_platform_consistency"], one["cross_platform_consistency"])
        self.assertGreater(len(one["cross_platform_consistency"]["findings"]), 0)
        stripped = copy.deepcopy(full)
        stripped.pop("cross_platform_consistency")
        self.assertNotIn("Cross-platform consistency", render_summary(stripped))
        self.assertIn("Cross-platform consistency", render_summary(full))


if __name__ == "__main__":
    unittest.main()
