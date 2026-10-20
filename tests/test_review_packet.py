"""Review packets and reviewer comments (draft, pending the owner's security review). Offline.

Invariants under test: the database is only opened read-only (posts and event_log never change, total_changes
stays 0), a comment, including ``looks_good``, is never approval, and the approve path never reads this module
or its files.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import attachments, catalog, review_packet, storage
from reachweaver.cli import main
from reachweaver.post_view import approval_view
from reachweaver.storage import add_post, connect, get_post, set_post_media, transition

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mastodon_media import png  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ROOT / "config" / "accounts.example.json"
TEXT = "Version 2 ships ```fenced``` notes today https://example.org/notes"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.db = str(self.state / "rw.sqlite")
        with connect(self.db) as db:
            self.pid = add_post(db, "my-bluesky", "bluesky", TEXT, "launch", "https://example.org/source")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["--plain", "--db", self.db, "--accounts", str(ACCOUNTS), *argv])
            except SystemExit as exc:  # argparse usage errors
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def packet(self, *extra, name="p1"):
        out = self.state / "review-packets" / name
        code, stdout, stderr = self.run_cli("review-packet", "--post-id", self.pid, "--out", str(out), *extra)
        self.assertEqual(code, 0, stderr)
        return out

    def comment(self, packet, verdict="looks_good", note="Reads well", reviewer="Jane Editor", *extra):
        return self.run_cli("review-comment", "--packet", str(packet), "--post-id", self.pid, "--reviewer", reviewer,
                            "--verdict", verdict, "--note", note, *extra)

    def snapshot(self):
        """Bytes of the database file plus every row of posts and event_log, read with a separate connection."""
        con = sqlite3.connect(self.db)
        try:
            tables = {name: con.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                      for name in ("posts", "event_log", "settings")}
        finally:
            con.close()
        return hashlib.sha256(Path(self.db).read_bytes()).hexdigest(), tables

    def change_text(self, text="Changed after review"):
        con = sqlite3.connect(self.db)
        con.execute("UPDATE posts SET content=? WHERE id=?", (text, self.pid))
        con.commit()
        con.close()


class PacketContentTests(Base):
    def test_new_private_folder_with_exact_text_destination_digest_and_findings(self):
        out = self.packet()
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, sorted(["README.md", "packet.json", f"post-{self.pid}.md", f"post-{self.pid}.json"]))
        for path in out.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
        entry = json.loads((out / f"post-{self.pid}.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["text"], TEXT)
        self.assertEqual(entry["destination"], {"platform": "bluesky", "account_id": "my-bluesky",
                                                "handle": "example.bsky.social"})
        self.assertEqual(entry["source_url"], "https://example.org/source")
        with connect(self.db) as db:
            post = get_post(db, self.pid)
            # The digest is the existing one 'show' prints for a draft; no new hash kind.
            self.assertEqual(entry["payload_sha256"], approval_view(post)["approve_would_bind_sha256"])
        code, check_json, _ = self.run_cli("check", self.pid, "--json")
        check = json.loads(check_json)
        self.assertEqual(entry["check"]["findings"], check["findings"])
        self.assertEqual(entry["check"]["ok"], check["ok"])
        self.assertIn("not approval", entry["not_approval"])
        self.assertEqual(entry["comment_template"]["post_id"], self.pid)
        markdown = (out / f"post-{self.pid}.md").read_text(encoding="utf-8")
        self.assertIn("````text\n" + TEXT + "\n````", markdown)  # fence longer than the text's own backticks
        self.assertIn(entry["payload_sha256"], markdown)
        self.assertIn("not approval", markdown)
        self.assertNotIn(str(self.root), markdown)  # no local paths handed to the reviewer
        manifest = json.loads((out / "packet.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], review_packet.PACKET_FORMAT)
        self.assertEqual(manifest["posts"][0]["payload_sha256"], entry["payload_sha256"])

    def test_refuses_an_existing_out_and_writes_nothing_on_refusal(self):
        out = self.packet()
        before = sorted(p.name for p in out.iterdir())
        code, _, err = self.run_cli("review-packet", "--post-id", self.pid, "--out", str(out))
        self.assertEqual(code, 2)
        self.assertIn("NEW folder", err)
        self.assertEqual(sorted(p.name for p in out.iterdir()), before)

    def test_alt_text_is_shown_and_local_paths_are_not(self):
        image = self.root / "chart.png"
        image.write_bytes(png())
        with connect(self.db) as db:
            self.pid = add_post(db, "my-mastodon", "mastodon", "Weekly installs, charted")
            set_post_media(db, self.pid, [attachments.build_attachment(image, "A bar chart of weekly installs")])
        out = self.packet()
        entry = json.loads((out / f"post-{self.pid}.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["media"][0]["alt"], "A bar chart of weekly installs")
        self.assertNotIn("path", entry["media"][0])
        self.assertNotIn(str(image), json.dumps(entry))
        self.assertIn("A bar chart of weekly installs", (out / f"post-{self.pid}.md").read_text(encoding="utf-8"))

    def test_private_source_url_is_redacted_and_a_private_link_in_text_is_refused(self):
        private_item = {"id": "e1", "kind": "repo", "url": "https://github.com/acme/secret-lab",
                        "source_visibility": "private", "excerpt": "internal benchmark"}
        with connect(self.db) as db:
            staged = add_post(db, "my-bluesky", "bluesky", "Public text, private provenance", "c1",
                              "https://github.com/acme/secret-lab/blob/main/NOTES.md")
            db.execute("INSERT INTO campaign_variants VALUES(?,?,?,?,?,?,?)",
                       ("c1", "b1", "v1", "f" * 64, staged, json.dumps([private_item]), storage.utc_now()))
            leaky = add_post(db, "my-bluesky", "bluesky", "See https://github.com/acme/secret-lab/pull/3")
        out = self.state / "review-packets" / "c1"
        code, _, err = self.run_cli("review-packet", "--campaign-staged", "c1", "--out", str(out))
        self.assertEqual(code, 0, err)
        entry = json.loads((out / f"post-{staged}.json").read_text(encoding="utf-8"))
        self.assertEqual(entry["source_url"], "[REDACTED:private_source]")
        self.assertEqual(entry["evidence"][0]["url"], "[REDACTED:private_source]")
        everything = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
        self.assertNotIn("secret-lab", everything)
        self.assertNotIn("internal benchmark", everything)
        refused = self.state / "review-packets" / "leaky"
        code, _, err = self.run_cli("review-packet", "--post-id", leaky, "--out", str(refused))
        self.assertEqual(code, 2)
        self.assertIn("private source repository", err)
        self.assertFalse(refused.exists())

    def test_credential_looking_text_is_refused(self):
        token = "gh" + "p_" + "A1b2C3d4" * 5
        with connect(self.db) as db:
            leaky = add_post(db, "my-bluesky", "bluesky", f"oops {token}")
        out = self.state / "review-packets" / "secret"
        code, _, err = self.run_cli("review-packet", "--post-id", leaky, "--out", str(out))
        self.assertEqual(code, 2)
        self.assertNotIn(token, err)
        self.assertFalse(out.exists())

    def test_campaign_staged_selects_only_reviewable_posts(self):
        with connect(self.db) as db:
            first = add_post(db, "my-bluesky", "bluesky", "Variant one", "camp")
            second = add_post(db, "my-bluesky", "bluesky", "Variant two", "camp")
            for variant, pid in (("a", first), ("b", second)):
                db.execute("INSERT INTO campaign_variants VALUES(?,?,?,?,?,?,?)",
                           ("camp", "brand", variant, variant * 64, pid, "[]", storage.utc_now()))
            transition(db, second, "cancel")
        out = self.state / "review-packets" / "camp"
        code, stdout, err = self.run_cli("review-packet", "--campaign-staged", "camp", "--out", str(out), "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([p["post_id"] for p in json.loads(stdout)["posts"]], [first])
        code, _, err = self.run_cli("review-packet", "--post-id", second, "--out", str(self.root / "x"))
        self.assertEqual(code, 2)
        self.assertIn("cancelled", err)


class CommentTests(Base):
    def test_comment_is_bound_stored_privately_in_the_packet_and_says_it_is_not_approval(self):
        out = self.packet()
        code, stdout, err = self.comment(out)
        self.assertEqual(code, 0, err)
        self.assertIn("not approval", stdout)
        self.assertIn("Nothing was approved", stdout)
        path = out / "comments.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        stored = json.loads(path.read_text(encoding="utf-8"))["comments"]
        manifest = json.loads((out / "packet.json").read_text(encoding="utf-8"))
        self.assertEqual(stored[0]["payload_sha256"], manifest["posts"][0]["payload_sha256"])
        self.assertEqual((stored[0]["verdict"], stored[0]["is_approval"]), ("looks_good", False))
        self.assertEqual(stored[0]["database_check"], "matched")
        self.assertEqual([p.name for p in out.iterdir() if p.name.endswith(".tmp")], [])
        with connect(self.db) as db:
            post = get_post(db, self.pid)
            self.assertEqual(post["status"], "draft")
            self.assertIsNone(post["approved_payload_sha256"])

    def test_input_rules(self):
        out = self.packet()
        self.assertEqual(self.comment(out, verdict="approved")[0], 2)  # argparse choices
        self.assertEqual(self.comment(out, note="")[0], 2)
        self.assertEqual(self.comment(out, note="x" * 2001)[0], 2)
        self.assertEqual(self.comment(out, note="look \x1b[31mred")[0], 2)
        self.assertEqual(self.comment(out, note="abc‮def")[0], 2)
        self.assertEqual(self.comment(out, reviewer="Jane\nDoe")[0], 2)
        code, _, err = self.run_cli("review-comment", "--packet", str(out), "--post-id", "notinpacket",
                                    "--reviewer", "J", "--verdict", "question", "--note", "?")
        self.assertEqual(code, 2)
        self.assertIn("not in this review packet", err)
        self.assertFalse((out / "comments.json").exists())
        secret = "gh" + "p_" + "Z9y8X7w6" * 5
        self.assertEqual(self.comment(out, note=f"token {secret}")[0], 0)
        self.assertNotIn(secret, (out / "comments.json").read_text(encoding="utf-8"))

    def test_refuses_a_folder_that_is_not_a_private_packet(self):
        out = self.packet()
        os.chmod(out, 0o755)
        code, _, err = self.comment(out)
        self.assertEqual(code, 2)
        self.assertIn("not private", err)
        os.chmod(out, 0o700)
        (out / "packet.json").unlink()
        self.assertEqual(self.comment(out)[0], 2)

    def test_stale_after_a_text_change_and_no_new_comment_on_old_text(self):
        out = self.packet()
        self.comment(out)
        code, stdout, _ = self.run_cli("review-status", "--packet", str(out), "--json")
        self.assertEqual(code, 0)
        status = json.loads(stdout)
        self.assertEqual(status["posts"][0]["comments"][0]["state"], "current")
        self.change_text()
        code, stdout, _ = self.run_cli("review-status", "--packet", str(out), "--json")
        self.assertEqual(code, 1)
        status = json.loads(stdout)
        self.assertEqual(status["posts"][0]["post_state"], "changed_since_packet")
        self.assertEqual(status["posts"][0]["comments"][0]["state"], "stale")
        self.assertEqual(status["counts"]["stale"], 1)
        code, text, _ = self.run_cli("review-status", "--packet", str(out))
        self.assertIn("STALE [looks_good]", text)
        code, _, err = self.comment(out, verdict="question", note="late")
        self.assertEqual(code, 2)
        self.assertIn("changed after this packet", err)

    def test_approving_the_post_does_not_make_a_comment_stale(self):
        out = self.packet()
        self.comment(out)
        with connect(self.db) as db:
            transition(db, self.pid, "approve", approver="owner", approval_channel="cli_flag")
        code, stdout, _ = self.run_cli("review-status", "--packet", str(out), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["posts"][0]["comments"][0]["state"], "current")

    def test_status_without_packet_lists_the_default_folder_and_without_database_checks_nothing(self):
        code, stdout, _ = self.run_cli("review-status")
        self.assertEqual(code, 0)
        self.assertIn("No review packets", stdout)
        self.packet(name="a")
        self.packet(name="b")
        code, stdout, _ = self.run_cli("review-status", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(stdout)["packets"]), 2)
        out = self.state / "review-packets" / "a"
        os.unlink(self.db)
        code, stdout, err = self.comment(out)
        self.assertEqual(code, 0, err)
        self.assertIn("not checked", stdout)
        self.assertFalse(Path(self.db).exists())  # read-only commands never create the database
        code, stdout, _ = self.run_cli("review-status", "--packet", str(out), "--json")
        self.assertEqual(json.loads(stdout)["posts"][0]["comments"][0]["state"], "not_checked")
        self.assertFalse(Path(self.db).exists())


class InvariantTests(Base):
    def test_no_command_changes_the_database(self):
        """posts, event_log and settings are byte-identical and every connection reports total_changes == 0."""
        real = storage.connect_readonly
        totals = []

        @contextlib.contextmanager
        def spy(path):
            with real(path) as con:
                yield con
                totals.append(con.total_changes)

        def no_write_connect(*_args, **_kwargs):
            raise AssertionError("a review command opened the database read-write")

        before = self.snapshot()
        with patch.object(storage, "connect_readonly", spy), patch("reachweaver.cli.connect", no_write_connect):
            out = self.packet()
            for verdict in review_packet.VERDICTS:
                self.assertEqual(self.comment(out, verdict=verdict)[0], 0)
            self.assertEqual(self.run_cli("review-status", "--packet", str(out))[0], 0)
            self.assertEqual(self.run_cli("review-status")[0], 0)
        self.assertEqual(self.snapshot(), before)
        self.assertGreaterEqual(len(totals), 6)
        self.assertEqual(set(totals), {0})

    def test_looks_good_is_not_approval(self):
        out = self.packet()
        code, stdout, _ = self.comment(out, "looks_good", "Ship it", "Jane", "--json")
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertFalse(result["comment"]["is_approval"])
        self.assertIn("not approval", result["not_approval"])
        code, stdout, _ = self.run_cli("review-status", "--packet", str(out))
        self.assertIn("not approval", stdout)
        self.assertIn("Nothing was approved", stdout)
        code, stdout, _ = self.run_cli("list", "--status", "approved", "--json")
        self.assertEqual(json.loads(stdout), [])

    def test_approve_path_never_reads_review_files(self):
        # Source: no approve-side module names the review module or its files.
        for name in ("storage", "ledger", "confirmation", "sendgate", "publishers", "post_view", "show_view",
                     "persona_guard", "identity"):
            source = (ROOT / "reachweaver" / f"{name}.py").read_text(encoding="utf-8")
            for needle in ("review_packet", "comments.json", "review-packets"):
                self.assertNotIn(needle, source, f"{name}.py mentions {needle}")
        cli_source = (ROOT / "reachweaver" / "cli.py").read_text(encoding="utf-8")
        start = cli_source.index('elif args.command == "approve":')
        end = cli_source.index("elif args.command ==", start + 10)
        self.assertNotIn("review", cli_source[start:end])
        # Imports: loading the approve-side modules never loads the review module.
        probe = ("import sys, reachweaver.storage, reachweaver.ledger, reachweaver.confirmation, "
                 "reachweaver.sendgate, reachweaver.post_view; print('reachweaver.review_packet' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), "False", result.stderr)
        # Behaviour: with every review reader broken, the owner's approve still works and binds the same digest.
        out = self.packet()
        self.comment(out)
        boom = AssertionError("approve read a review file")
        with patch.object(review_packet, "load_packet", side_effect=boom), \
                patch.object(review_packet, "load_comments", side_effect=boom):
            code, _, err = self.run_cli("approve", self.pid, "--approver", "owner")
        self.assertEqual(code, 0, err)
        manifest = json.loads((out / "packet.json").read_text(encoding="utf-8"))
        with connect(self.db) as db:
            self.assertEqual(review_packet.content_sha256(get_post(db, self.pid)),
                             manifest["posts"][0]["payload_sha256"])

    def test_review_module_has_no_write_or_transition_path(self):
        source = (ROOT / "reachweaver" / "review_packet.py").read_text(encoding="utf-8")
        for needle in ("transition(", "storage.connect(", "INSERT ", "UPDATE ", "DELETE ", "event(", "claim_sending",
                       "from .publishers", "from .ledger", "import ledger", "urllib", "http.client", "import socket"):
            self.assertNotIn(needle, source, needle)
        self.assertEqual(set(re.findall(r"storage\.connect\w*\(", source)), {"storage.connect_readonly("})

    def test_catalog_classification(self):
        self.assertIn("review-comment", catalog.owner_only_commands())
        self.assertEqual(catalog.COMMANDS["review-status"]["tags"], ("read_only",))
        self.assertIn("writes", catalog.COMMANDS["review-packet"]["tags"])


if __name__ == "__main__":
    unittest.main()
