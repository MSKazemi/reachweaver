"""`workspace --init` works from a pip install, which has no checkout `config/` or skill `assets/` next to it (offline).

A user who runs `pipx install reachweaver` and then `reachweaver workspace --init .` must get `config/accounts.json`,
or the next `doctor` stops with "Missing .../config/accounts.json". The example file therefore ships inside the package.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "reachweaver"


class PackageExampleAccountsTests(unittest.TestCase):
    def test_packaged_copy_equals_the_canonical_example(self):
        self.assertEqual((PACKAGE / "accounts.example.json").read_bytes(), (ROOT / "config/accounts.example.json").read_bytes())

    def test_it_is_declared_as_package_data(self):
        self.assertIn('"accounts.example.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_init_creates_accounts_json_with_only_the_package_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site"
            site.mkdir()
            shutil.copytree(PACKAGE, site / "reachweaver", ignore=shutil.ignore_patterns("__pycache__"))
            self.assertFalse((site / "config").exists() or (site / "assets").exists())  # the pip layout
            workspace = Path(tmp) / "my_social_media"
            code = ("import sys; sys.path.insert(0, sys.argv[2]); from pathlib import Path; "
                    "from reachweaver.workspace import init_workspace; init_workspace(Path(sys.argv[1]))")
            result = subprocess.run([sys.executable, "-I", "-c", code, str(workspace), str(site)], cwd=tmp,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            accounts = json.loads((workspace / "config" / "accounts.json").read_text(encoding="utf-8"))
            self.assertTrue(accounts["accounts"])


if __name__ == "__main__":
    unittest.main()
