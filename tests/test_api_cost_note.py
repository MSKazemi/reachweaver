"""The approval preview and the dry run show what a send is documented to cost (public issue #39). Offline, read-only."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from reachweaver.capabilities import api_cost_note, load_registry
from reachweaver.cli import main
from reachweaver.storage import add_post, connect, transition


class ApiCostNoteTests(unittest.TestCase):
    def test_the_note_is_the_registrys_sourced_fee_text_and_date(self):
        registry = load_registry()
        for entry in registry["platforms"]:
            with self.subTest(platform=entry["id"]):
                note = api_cost_note(entry["id"], registry)
                self.assertEqual(note["api_fees"], entry["fees"])
                self.assertEqual(note["verified_date"], entry["verified_date"])
                self.assertIn("prices can change", note["source"])

    def test_a_platform_without_an_entry_is_unknown_never_free(self):
        note = api_cost_note("not-a-platform")
        self.assertIn("unknown", note["api_fees"])
        self.assertIsNone(note["verified_date"])
        self.assertNotIn("none", note["api_fees"].lower().split("(")[0])

    def test_the_note_says_there_is_no_monetary_cap_and_what_is_enforced_instead(self):
        note = api_cost_note("bluesky")
        self.assertEqual(note["reachweaver_charges"], "nothing; it meters no money")
        self.assertIn("no monetary cap", note["enforced_before_each_attempt"])
        self.assertIn("rolling send-attempt cap", note["enforced_before_each_attempt"])

    def test_a_paid_platform_shows_its_documented_price_from_the_registry(self):
        fees = api_cost_note("x")["api_fees"]
        self.assertRegex(fees, r"\$\d")
        self.assertEqual(fees, next(p["fees"] for p in load_registry()["platforms"] if p["id"] == "x"))


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "outbox.sqlite")
        with connect(self.db) as db:
            self.pid = add_post(db, "my-bluesky", "bluesky", "A note", "launch", "https://example.org/r")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_show_includes_the_api_cost(self):
        code, out, err = self.cli("show", self.pid)
        self.assertEqual(code, 0, err)
        cost = json.loads(out)["api_cost"]
        self.assertEqual(cost["platform"], "bluesky")
        self.assertEqual(cost["api_fees"], api_cost_note("bluesky")["api_fees"])

    def test_the_dry_run_prints_one_cost_line_per_platform_with_due_posts(self):
        with connect(self.db) as db:
            transition(db, self.pid, "approve")
            other = add_post(db, "my-mastodon", "mastodon", "Another note", "launch", "https://example.org/r")
            transition(db, other, "approve")
        code, out, err = self.cli("publish")
        self.assertEqual(code, 0, err)
        self.assertEqual(sum(1 for line in out.splitlines() if line.startswith("API cost (bluesky)")), 1)
        self.assertEqual(sum(1 for line in out.splitlines() if line.startswith("API cost (mastodon)")), 1)
        self.assertIn("ReachWeaver meters no money", out)

    def test_no_cost_line_without_due_posts(self):
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertNotIn("API cost (", out)


if __name__ == "__main__":
    unittest.main()
