"""Every readiness reason code that `health` (and so the dashboard's Overview) can emit has plain words with a fix.

Offline: reads the source of reachweaver/health.py and the dashboard script; no server, database or network.
"""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

from reachweaver import account_add, health, ledger, ui_assets, ui_data
from reachweaver.cli import main

HEALTH_SOURCE = Path(health.__file__).read_text(encoding="utf-8")
LITERAL = re.compile(r'_reason\(\s*(?:"[a-z]+"|[^,()]+(?:\([^()]*\))?[^,()]*),\s*"([a-z0-9_]+)"')
FORMATTED = re.compile(r'_reason\(\s*(?:"[a-z]+"|[^,()]+(?:\([^()]*\))?[^,()]*),\s*\n?\s*f"([^"]+)"')
# The f-string codes in health.py, expanded by hand. A new f-string code fails the count check below until it is
# listed here, so no code can reach the page without its help text.
EXPANDED = {
    "linkedin_authorization_{t_level}": ["linkedin_authorization_warning", "linkedin_authorization_unknown"],
    "{platform}_authorization_{'expiry_invalid' if invalid else 'expired'}": [
        f"{p}_authorization_{k}" for p in ("instagram", "threads") for k in ("expiry_invalid", "expired")],
    "{platform}_authorization_{t_level}": [
        f"{p}_authorization_{k}" for p in ("instagram", "threads") for k in ("warning", "unknown")],
}


def help_keys() -> set[str]:
    block = ui_assets.APP_JS.split("const REASON_HELP = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^\s*([a-z0-9_]+):", block, re.M))


def emitted_codes() -> set[str]:
    codes = set(LITERAL.findall(HEALTH_SOURCE))
    formatted = FORMATTED.findall(HEALTH_SOURCE)
    for template in formatted:
        if template not in EXPANDED:
            raise AssertionError(f"health.py emits a new formatted reason code {template!r}: list it in EXPANDED")
        codes.update(EXPANDED[template])
    codes.update(ledger.LEDGER_CODES)  # the approval-ledger audit codes health passes through
    return codes


class ReasonHelpTests(unittest.TestCase):
    def test_the_scan_finds_the_codes_it_should(self):
        codes = emitted_codes()
        for known in ("linkedin_version_unsupported", "emergency_pause_active", "credentials_missing",
                      "provider_cooldown_ceiling_clamped", "threads_authorization_expiry_invalid"):
            self.assertIn(known, codes)
        self.assertGreaterEqual(len(codes), 35)
        self.assertEqual(len(FORMATTED.findall(HEALTH_SOURCE)), len(EXPANDED))
        # Every _reason(...) call is a literal code, a listed f-string, the definition, or the ledger pass-through.
        unmatched = re.findall(r'_reason\((?!\s*(?:"[a-z]+"|[^,()]+(?:\([^()]*\))?[^,()]*),\s*\n?\s*f?")[^\n]*',
                               HEALTH_SOURCE)
        self.assertEqual(len(unmatched), 2, unmatched)
        self.assertTrue(unmatched[0].startswith("_reason(level: str, code: str"))
        self.assertIn('_reason("attention", code, ids', unmatched[1])

    def test_every_emitted_code_has_plain_help_on_the_dashboard(self):
        missing = sorted(emitted_codes() - help_keys())
        self.assertEqual(missing, [], "add REASON_HELP text (with the fix) in ui_assets.APP_JS for these codes")
        self.assertEqual(sorted(ui_data.PROBLEM_CODES - help_keys()), [])

    def test_linkedin_version_help_names_the_fix_and_says_hand_posting_is_fine(self):
        block = ui_assets.APP_JS.split("const REASON_HELP = {", 1)[1]
        line = next(x for x in block.splitlines() if x.strip().startswith("linkedin_version_unsupported:"))
        self.assertIn("Posting by hand is not affected", line)
        self.assertIn("linkedin_version", line)
        self.assertIn("docs/LINKEDIN.md", line)
        self.assertIn('linkedin_version_unsupported: "LinkedIn API version too old', ui_assets.APP_JS)  # its title


class LinkedInVersionWarningTests(unittest.TestCase):
    def test_account_add_warns_but_still_adds_an_old_linkedin_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts = Path(tmp) / "accounts.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--accounts", str(accounts), "account-add", "linkedin", "--field", "person_id=abc123XYZ",
                             "--field", "linkedin_version=202209"])
            self.assertEqual(code, 0)
            self.assertTrue(accounts.is_file())  # a warning, not a refusal
            text = out.getvalue()
            self.assertIn("Note: LinkedIn API version 202209 is 12 months old or more", text)
            self.assertIn("Posting by hand is not affected", text)

    def test_no_note_for_a_current_version_or_another_network(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        current = f"{now.year}{now.month:02d}"
        self.assertIsNone(account_add.linkedin_version_note({"platform": "linkedin", "linkedin_version": current}))
        self.assertIsNone(account_add.linkedin_version_note({"platform": "bluesky"}))


class NextStepOpensTests(unittest.TestCase):
    def test_steps_say_which_ones_a_page_can_open(self):
        self.assertEqual(ui_data.step_opens("reachweaver share --web --open"), "composer")
        self.assertEqual(ui_data.step_opens("reachweaver share --web --open --idea idea-0a1b2c3d"), "composer")
        self.assertEqual(ui_data.step_opens("reachweaver idea list --status open"), "ideas")
        for terminal in ("reachweaver review", "reachweaver resume", "reachweaver share --guide",
                         "reachweaver share --webby", None, ""):
            self.assertIsNone(ui_data.step_opens(terminal))
        view = ui_data.next_view({"state": "drafts", "summary": "s", "warnings": [], "steps": [
            {"label": "Drafts", "command": "reachweaver review", "note": None}]})
        self.assertIsNone(view["primary"]["opens"])
        self.assertEqual(view["secondary"][0]["opens"], "composer")


if __name__ == "__main__":
    unittest.main()
