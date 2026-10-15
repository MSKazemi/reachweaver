"""The backup, recovery and incident procedures stay true to the code they describe (public issue #54).

Docs tests, offline: every command a procedure names must be a real subcommand, every error code it quotes must
exist in the source, the ledger must be part of the backup, and the relative links it relies on must resolve.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from reachweaver.cli import make_parser

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LIFECYCLE = (DOCS / "PRIVACY-LIFECYCLE.md").read_text(encoding="utf-8")
CHECKLIST = (DOCS / "SECURITY-CHECKLIST.md").read_text(encoding="utf-8")
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "reachweaver").glob("*.py"))


def section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def subcommands() -> set[str]:
    names: set[str] = set()
    for action in make_parser()._actions:
        names |= set(getattr(action, "choices", None) or ())
    return names


BACKUP = section(LIFECYCLE, "## Backup, recovery and disconnect", "## Incident response")
INCIDENT = section(LIFECYCLE, "## Incident response", "## What remains open")


class BackupAndRecoveryTests(unittest.TestCase):
    def test_the_backup_includes_the_approval_ledger_next_to_the_database(self):
        backup = section(BACKUP, "- **Backup:**", "- **Recovery:**")
        self.assertIn("approval-ledger.jsonl", backup)
        self.assertIn("together", backup)
        self.assertIn("reachweaver pause", backup)

    def test_recovery_names_the_fail_closed_codes_and_both_exist_in_the_source(self):
        recovery = section(BACKUP, "- **Recovery:**", "- **Permission revoked")
        for code in ("approval_ledger_unavailable", "approval_replay_reapproval_required"):
            self.assertIn(code, recovery)
            self.assertIn(code, SOURCE, f"{code} is documented but no longer produced by the code")
        self.assertIn("Never create an empty ledger by hand", recovery)

    def test_the_documented_ledger_file_name_is_the_one_the_code_writes(self):
        self.assertIn('LEDGER_FILENAME = "approval-ledger.jsonl"', SOURCE)


class IncidentProcedureTests(unittest.TestCase):
    def test_every_command_in_backup_and_incident_sections_is_a_real_subcommand(self):
        named = set(re.findall(r"`reachweaver ([a-z][a-z-]+)", BACKUP + INCIDENT))
        self.assertTrue({"pause", "resume", "reconcile", "dead-letter", "queue-health", "privacy-status"} <= named)
        self.assertEqual(named - subcommands(), set(), "a documented command does not exist")

    def test_the_procedure_has_roles_timings_and_the_steps_in_order(self):
        self.assertIn("**Roles.**", INCIDENT)
        self.assertIn("incident owner", INCIDENT)
        rows = [line for line in INCIDENT.splitlines() if line.startswith("| ") and not line.startswith("| When")
                and not line.startswith("|---")]
        whens = [row.split("|")[1].strip() for row in rows]
        self.assertEqual(whens[0], "Immediately")
        self.assertGreaterEqual(len(rows), 7)
        steps = [row.split("|")[2].strip() for row in rows]
        self.assertLess(steps.index("Stop every new send."), steps.index("Make the record match reality."))
        self.assertLess(steps.index("Make the record match reality."), steps.index("Recover and resume."))

    def test_the_pause_comes_before_anything_else_and_resume_comes_last(self):
        self.assertLess(INCIDENT.index("reachweaver pause"), INCIDENT.index("reachweaver reconcile"))
        self.assertLess(INCIDENT.index("reachweaver reconcile"), INCIDENT.index("reachweaver resume"))

    def test_it_says_never_to_paste_secrets_and_never_to_resend_blindly(self):
        self.assertIn("Never paste a token", INCIDENT)
        self.assertIn("Never re-send blindly", INCIDENT)

    def test_it_makes_no_legal_promise_beyond_pointing_at_qualified_advice(self):
        self.assertIn("qualified advice", INCIDENT)
        self.assertNotIn("compliant", INCIDENT.lower())
        self.assertIn("working targets", INCIDENT)


class LinkedDocsTests(unittest.TestCase):
    def test_relative_links_in_these_sections_point_at_existing_files(self):
        for text in (BACKUP, INCIDENT):
            for target in re.findall(r"\]\((?!https?://)([^)#]+)(?:#[^)]*)?\)", text):
                self.assertTrue((DOCS / target).is_file(), f"docs/{target} does not exist")

    def test_the_checklist_section_this_procedure_extends_still_exists_with_the_same_anchor(self):
        self.assertIn("## C. When something goes wrong", CHECKLIST)
        self.assertIn("SECURITY-CHECKLIST.md#c-when-something-goes-wrong", INCIDENT)
        block = CHECKLIST.split("## C. When something goes wrong", 1)[1].split("\n## ", 1)[0]
        for needle in ("reachweaver pause", "reconcile", "reachweaver resume"):
            self.assertIn(needle, block)

    def test_the_lifecycle_doc_and_the_checklist_both_say_the_ledger_is_backed_up_with_the_database(self):
        self.assertIn("approval-ledger.jsonl", CHECKLIST)
        self.assertIn("backed up together with the database", CHECKLIST)


if __name__ == "__main__":
    unittest.main()
