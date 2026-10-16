"""Terminal views of doctor and health: formatting only, offline. Piped output stays the old text."""
from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path

from reachweaver import health_view
from reachweaver.health import build_health, render_health
from reachweaver.storage import connect

ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOW = "2026-10-16T12:00:00+00:00"


class Tty(io.StringIO):
    def isatty(self):
        return True


ACCOUNTS = {
    "my-bsky": {"id": "my-bsky", "platform": "bluesky", "env_prefix": "RW_TEST_BSKY_NEVER_SET", "handle": "a.bsky.social"},
    "my-linkedin": {"id": "my-linkedin", "platform": "linkedin", "env_prefix": "RW_TEST_LI_NEVER_SET"},
}


class DoctorViewTests(unittest.TestCase):
    def test_piped_lines_keep_the_historic_format_and_notes_follow_their_account(self):
        findings = health_view.doctor_findings(ACCOUNTS)
        lines = health_view.doctor_plain_lines(findings)
        self.assertTrue(lines[0].startswith("my-bsky: bluesky; supported (text); env variables missing: "))
        self.assertTrue(any(line.startswith("  my-linkedin: ") for line in lines), lines)
        self.assertFalse(any("\x1b" in line for line in lines))

    def test_terminal_view_has_a_verdict_a_table_and_the_same_notes(self):
        findings = health_view.doctor_findings(ACCOUNTS)
        text = ANSI.sub("", health_view.render_doctor(findings, ["Live opt-in: disabled"], Tty()))
        self.assertIn("2 of 2 account(s) are missing credentials", text)
        self.assertRegex(text, r"ACCOUNT\s+PLATFORM\s+ADAPTER\s+CREDENTIALS")
        self.assertIn("Live opt-in: disabled", text)
        self.assertIn("Next: reachweaver health", text)
        for finding in findings:  # nothing the piped view says is dropped at a terminal
            for note in finding["notes"]:
                self.assertIn(note.split(";")[0][:40], text)
            for name in finding["missing"]:
                self.assertIn(name, text)

    def test_no_accounts_is_said_plainly(self):
        self.assertIn("No accounts are configured.", health_view.render_doctor([], [], Tty()))

    def test_credential_values_are_never_part_of_the_view(self):
        findings = health_view.doctor_findings(ACCOUNTS)
        self.assertEqual({k for f in findings for k in f}, {"name", "platform", "status", "missing", "notes"})


class HealthViewTests(unittest.TestCase):
    def report(self, accounts=ACCOUNTS):
        with tempfile.TemporaryDirectory() as tmp:
            with connect(str(Path(tmp) / "h.sqlite")) as db:
                return build_health(db, accounts, now=NOW, env={})

    def test_verdict_table_and_attention_list_are_present(self):
        report = self.report()
        text = ANSI.sub("", health_view.render_health_sheet(report, Tty()))
        self.assertTrue(text.startswith(f"Readiness: {report['overall'].upper()}"))
        self.assertRegex(text, r"ACCOUNT\s+PLATFORM\s+STATUS\s+SENDS/24H\s+NEEDS REVIEW\s+CREDENTIALS")
        self.assertIn("What needs attention", text)
        self.assertIn("credentials_missing", text)
        self.assertIn(report["note"], text)

    def test_credentials_only_is_explained_as_not_stopping_drafting(self):
        report = self.report({"my-bsky": ACCOUNTS["my-bsky"]})
        self.assertEqual({r["code"] for r in report["reasons"]}, {"credentials_missing"})
        self.assertIn("Drafting, review and dry runs still work", health_view.next_step(report))

    def test_other_reasons_are_not_hidden_behind_the_credentials_sentence(self):
        report = self.report()  # the LinkedIn account also carries version/authorization findings
        self.assertGreater(len({r["code"] for r in report["reasons"]}), 1)
        self.assertNotIn("only credentials are missing", health_view.next_step(report))

    def test_a_dead_letter_is_the_first_next_step(self):
        report = self.report()
        report["dead_letter"] = {"total": 2}
        step = health_view.next_step(report)
        self.assertIn("reachweaver dead-letter", step)
        self.assertIn("never resend blindly", step)

    def test_emergency_stop_says_resume_is_deliberate(self):
        report = self.report()
        report["global"] = dict(report["global"], emergency_paused=True)
        self.assertIn("deliberately", health_view.next_step(report))

    def test_a_clean_report_points_at_start(self):
        report = self.report({})
        report["reasons"], report["overall"] = [], "ok"
        self.assertIn("nothing needs attention", health_view.next_step(report))

    def test_piped_text_is_still_the_original_renderer(self):
        report = self.report()
        self.assertTrue(render_health(report).startswith("OVERALL: "))

    def test_new_module_reads_no_environment_and_the_database(self):
        source = Path(health_view.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"os\.environ|getenv|sqlite3|connect\(")


if __name__ == "__main__":
    unittest.main()
