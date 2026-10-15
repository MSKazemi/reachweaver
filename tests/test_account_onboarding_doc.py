"""docs/ACCOUNT-ONBOARDING.md must name every credential variable the code reads (issue #21, B2).

The credential table says it comes from ``CREDENTIAL_SUFFIXES``, ``OPT_IN_CREDENTIAL_SUFFIXES`` and
``BACKEND_CREDENTIAL_SUFFIX`` in ``reachweaver/accounts.py``. These tests read that table and fail
when a platform with a credential variable has no row, or when a row omits one of its variables.
Offline: no credential is read and no request is made.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from reachweaver.accounts import BACKEND_CREDENTIAL_SUFFIX, CREDENTIAL_SUFFIXES, OPT_IN_CREDENTIAL_SUFFIXES

DOC = Path(__file__).resolve().parents[1] / "docs" / "ACCOUNT-ONBOARDING.md"
HEADER = "| Platform | Variables read (`<P>` = `env_prefix`) | Credential to create |"


def credential_rows(text: str) -> dict[str, str]:
    """{first cell lowercased: variables cell} of the credential table."""
    lines = text.splitlines()
    start = lines.index(HEADER) + 2  # skip the header and the separator row
    rows = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows[cells[0].lower()] = cells[1]
    return rows


def missing_rows(text: str) -> list[str]:
    """Credential variables (as ``platform:<P>SUFFIX``) the table does not name for their platform."""
    rows = credential_rows(text)
    missing = []
    for platform in sorted(set(CREDENTIAL_SUFFIXES) | set(OPT_IN_CREDENTIAL_SUFFIXES)):
        suffixes = dict.fromkeys(CREDENTIAL_SUFFIXES.get(platform, ()) + OPT_IN_CREDENTIAL_SUFFIXES.get(platform, ()))
        cells = [variables for label, variables in rows.items() if label.startswith(platform)]
        for suffix in suffixes:
            if not any(f"`<P>{suffix}`" in variables for variables in cells):
                missing.append(f"{platform}:<P>{suffix}")
    if not any(f"`<P>{BACKEND_CREDENTIAL_SUFFIX}`" in variables for variables in rows.values()):
        missing.append(f"backend:<P>{BACKEND_CREDENTIAL_SUFFIX}")
    return missing


class CredentialTableTests(unittest.TestCase):
    def setUp(self):
        self.text = DOC.read_text(encoding="utf-8")

    def test_every_platform_with_a_credential_variable_has_a_row(self):
        self.assertEqual(missing_rows(self.text), [])

    def test_a_dropped_row_is_detected(self):
        lines = [line for line in self.text.splitlines() if not line.startswith("| Facebook Page (adapter disabled)")]
        self.assertEqual(missing_rows("\n".join(lines)), ["facebook:<P>_PAGE_ACCESS_TOKEN"])
        lines = [line.replace("`<P>_CLIENT_SECRET`", "") if line.startswith("| LinkedIn |") else line
                 for line in self.text.splitlines()]
        self.assertEqual(missing_rows("\n".join(lines)), ["linkedin:<P>_CLIENT_SECRET"])

    def test_facebook_page_fields_and_revocation_are_documented(self):
        fields = [line for line in self.text.splitlines() if line.startswith("| `page_id`, `page_name` |")]
        self.assertEqual(len(fields), 1)
        self.assertIn("Facebook Page", fields[0])
        self.assertIn("manual-export metadata", fields[0])
        revoke = [line for line in self.text.splitlines() if line.startswith("- **Meta (")]
        self.assertEqual(len(revoke), 1)
        self.assertIn("Facebook Page", revoke[0])
        self.assertIn("(FACEBOOK.md)", revoke[0])


if __name__ == "__main__":
    unittest.main()
