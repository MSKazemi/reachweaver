"""The repository keeps credentials and local state out of Git, and CI scans for secrets (public issue #38). Offline.

Uses ``git check-ignore`` on the real ``.gitignore`` and reads the CI workflow; no network, nothing is written.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUST_BE_IGNORED = (".env", ".env.local", ".env.production", "state/reachweaver.sqlite", "config/accounts.json",
                   "settings.local", "notes.local")
MUST_NOT_BE_IGNORED = ("config/accounts.example.json", ".env.example", "README.md", "reachweaver/storage.py")


class GitignoreProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            raise unittest.SkipTest("needs a git checkout")

    def ignored(self, path: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, capture_output=True, check=False)
        return result.returncode == 0

    def test_credentials_and_local_state_are_ignored(self):
        for path in MUST_BE_IGNORED:
            with self.subTest(path=path):
                self.assertTrue(self.ignored(path), f"{path} must be git-ignored")

    def test_examples_and_source_are_not_ignored(self):
        for path in MUST_NOT_BE_IGNORED:
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path), f"{path} must stay trackable")

    def test_no_credential_or_state_file_is_tracked(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        bad = [p for p in tracked if re.search(r"(^|/)\.env($|\.(?!example$))|(^|/)state/|(^|/)config/accounts\.json$|\.sqlite$", p)]
        self.assertEqual(bad, [])

    def test_ci_runs_the_secret_scan_in_the_hygiene_job_without_secrets(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        hygiene = workflow.split("hygiene:", 1)[1].split("\n  tests:", 1)[0]
        self.assertIn("scripts/secret_scan.py", hygiene)
        self.assertNotIn("secrets.", hygiene)


if __name__ == "__main__":
    unittest.main()
