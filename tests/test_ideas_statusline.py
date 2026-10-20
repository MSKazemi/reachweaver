"""Ideas inbox, the Claude Code status line and the plugin fact-check agent (offline, synthetic, temp dirs)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from reachweaver import __version__, home, ideas, statusline
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect, set_paused

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "examples/brand.reachweaver.json"
ACCOUNTS = ROOT / "config/accounts.example.json"
SOURCE = "https://example.org/notes/repeat-guard"


def no_network(*args, **kwargs):
    raise AssertionError("network access attempted by an offline ideas/statusline test")


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def table_snapshot(db: Path) -> tuple[list, list]:
    con = sqlite3.connect(db)
    try:
        return (con.execute("SELECT * FROM posts ORDER BY id").fetchall(),
                con.execute("SELECT * FROM event_log ORDER BY id").fetchall())
    finally:
        con.close()


class _TempState(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.db = self.tmp / "state" / "reachweaver.sqlite"
        self.inbox = self.db.parent / ideas.FILENAME
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("REACHWEAVER_DB", None)
        net = mock.patch.object(socket, "create_connection", side_effect=no_network)
        net.start()
        self.addCleanup(net.stop)
        self.addCleanup(home.set_root, None)

    def cli(self, *argv):
        return run_cli("--db", str(self.db), *argv)

    def add(self, text="Write about the repeat guard", *extra):
        code, out, err = self.cli("idea", "add", text, "--json", *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)


class IdeasInboxTests(_TempState):
    def test_add_list_drop_round_trip_in_a_private_file_and_no_database(self):
        first = self.add("Write about the repeat guard", "--source", SOURCE, "--tag", "Release", "--tag", "docs")
        second = self.add("A thread on approval binding")
        self.assertRegex(first["id"], r"^idea-[0-9a-f]{8}$")
        self.assertEqual((first["tags"], first["source_url"], first["status"]), (["release", "docs"], SOURCE, "open"))
        self.assertFalse(self.db.exists(), "adding an idea must never create the database")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.inbox.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.inbox.parent.stat().st_mode) & 0o077, 0)
        code, out, _ = self.cli("idea", "list", "--json")
        self.assertEqual([i["id"] for i in json.loads(out)["ideas"]], [first["id"], second["id"]])
        code, out, _ = self.cli("idea", "list", "--tag", "release", "--json")
        self.assertEqual([i["id"] for i in json.loads(out)["ideas"]], [first["id"]])
        self.assertEqual(self.cli("idea", "drop", second["id"])[0], 0)
        code, out, _ = self.cli("idea", "list", "--status", "dropped", "--json")
        self.assertEqual([i["id"] for i in json.loads(out)["ideas"]], [second["id"]])
        code, out, _ = self.cli("idea", "list")
        self.assertIn(first["id"], out)
        self.assertIn("source: " + SOURCE, out)
        code, _, err = self.cli("idea", "drop", second["id"])
        self.assertEqual(code, 2)
        self.assertIn("only an open idea", err)

    def test_input_limits(self):
        cases = {
            "empty": ("   ",),
            "too long": ("x" * (ideas.TEXT_MAX + 1),),
            "control": ("bell\x07",),
            "secret": ("my token ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",),
            "http source": ("idea", "--source", "http://example.org/x"),
            "credential source": ("idea", "--source", "https://user:pw@example.org/x"),
            "local source": ("idea", "--source", "https://localhost/x"),
            "bad tag": ("idea", "--tag", "Not A Tag"),
            "too many tags": ("idea", *sum((["--tag", f"t{i}"] for i in range(ideas.TAGS_MAX + 1)), [])),
        }
        for name, argv in cases.items():
            with self.subTest(name):
                code, out, err = self.cli("idea", "add", *argv)
                self.assertEqual(code, 2, out)
                self.assertTrue(err.startswith("ERROR:"))
                self.assertNotIn("ghp_", err)
        self.assertEqual(ideas.clean_text("  ok\n\tfine  "), "ok\n\tfine")

    def test_damaged_or_unsafe_inbox_is_refused(self):
        self.add()
        self.inbox.write_text('{"schema_version": 1, "ideas": [{"id": "nope"}]}', encoding="utf-8")
        code, _, err = self.cli("idea", "list")
        self.assertEqual(code, 2)
        self.assertIn("damaged", err)
        if os.name == "posix":
            self.inbox.unlink()
            target = self.tmp / "elsewhere.json"
            target.write_text('{"schema_version": 1, "ideas": []}', encoding="utf-8")
            self.inbox.symlink_to(target)
            self.assertEqual(self.cli("idea", "add", "x")[0], 2)
            self.inbox.unlink()
            self.inbox.write_text('{"schema_version": 1, "ideas": []}', encoding="utf-8")
            self.inbox.chmod(0o644)
            code, _, err = self.cli("idea", "add", "x")
            self.assertEqual(code, 2)
            self.assertIn("not private", err)

    def test_the_inbox_has_no_send_path(self):
        self.assertIsNone(ideas.SEND_PATH)
        source = (ROOT / "reachweaver/ideas.py").read_text(encoding="utf-8")
        for forbidden in ("publishers", "stage_campaign", "transition(", "add_post", "connect(", "urlopen", "http"
                          "_get"):
            self.assertNotIn(forbidden, source, forbidden)


class IdeaPromoteTests(_TempState):
    def promote(self, idea_id, out, *extra):
        return self.cli("idea", "promote", idea_id, "--campaign", "repeat-guard-post", "--out", str(out), *extra)

    def test_promote_writes_a_blocked_scaffold_and_never_touches_the_post_queue(self):
        with connect(str(self.db)) as con:  # an existing queue with one draft
            add_post(con, "my-bluesky", "bluesky", "An existing draft that must stay untouched.")
        before = table_snapshot(self.db)
        db_bytes = hashlib.sha256(self.db.read_bytes()).hexdigest()
        idea = self.add("Explain why approvals are bound to a payload hash", "--source", SOURCE, "--tag", "security")
        out = self.tmp / "campaigns" / "repeat-guard"
        code, stdout, err = self.promote(idea["id"], out, "--json")
        self.assertEqual(code, 0, err)
        result = json.loads(stdout)
        self.assertEqual(result["status"], ideas.SCAFFOLD_STATUS)
        self.assertEqual(table_snapshot(self.db), before, "promote must not add or change posts or events")
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), db_bytes)
        campaign_file, brief_file = out / ideas.CAMPAIGN_FILE, out / ideas.BRIEF_FILE
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
            for path in (campaign_file, brief_file):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        campaign = json.loads(campaign_file.read_text(encoding="utf-8"))
        self.assertEqual((campaign["schema_version"], campaign["campaign_id"]), (1, "repeat-guard-post"))
        self.assertEqual(campaign["brief"]["idea_text"], idea["text"])
        self.assertEqual(campaign["evidence"][0]["url"], SOURCE)
        self.assertNotIn("verification", campaign["evidence"][0])
        self.assertIn(idea["text"], brief_file.read_text(encoding="utf-8"))
        # campaign-check reads the scaffold in the existing contract format and reports it BLOCKED.
        code, stdout, _ = run_cli("campaign-check", "--file", str(campaign_file), "--json")
        checked = json.loads(stdout)
        self.assertEqual((code, checked["valid"]), (2, False))
        expected = re.compile(r"placeholder|verification needs|checked_at must|variants must include")
        self.assertEqual([e for e in checked["errors"] if not expected.search(e)], [])
        # The idea is now promoted and cannot be promoted twice.
        code, stdout, _ = self.cli("idea", "list", "--status", "promoted", "--json")
        promoted = json.loads(stdout)["ideas"]
        self.assertEqual(promoted[0]["promoted"], {"campaign_id": "repeat-guard-post", "out": str(out)})
        code, _, err = self.promote(idea["id"], self.tmp / "again")
        self.assertEqual(code, 2)
        self.assertFalse((self.tmp / "again").exists())
        self.assertEqual(table_snapshot(self.db), before)

    def test_promote_never_creates_the_database(self):
        idea = self.add()
        self.assertEqual(self.promote(idea["id"], self.tmp / "out")[0], 0)
        self.assertFalse(self.db.exists())

    def test_brand_and_accounts_scaffold_placeholder_variants_that_stay_blocked(self):
        idea = self.add("Announce the ideas inbox", "--source", SOURCE)
        out = self.tmp / "with-brand"
        code, stdout, err = self.promote(idea["id"], out, "--brand", str(BRAND), "--accounts", str(ACCOUNTS),
                                         "--objective", "discussion", "--json")
        self.assertEqual(code, 0, err)
        campaign = json.loads((out / ideas.CAMPAIGN_FILE).read_text(encoding="utf-8"))
        self.assertEqual((campaign["brand_id"], campaign["objective"]), ("reachweaver", "discussion"))
        self.assertEqual(sorted(v["account_id"] for v in campaign["variants"]), ["my-bluesky", "my-linkedin"])
        self.assertTrue(all("{{write:" in v["text"] for v in campaign["variants"]))
        code, stdout, _ = run_cli("campaign-check", "--file", str(out / ideas.CAMPAIGN_FILE), "--brand", str(BRAND),
                                  "--json")
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(stdout)["valid"])

    def test_promote_refusals_leave_the_idea_open(self):
        idea = self.add()
        existing = self.tmp / "exists"
        existing.mkdir()
        cases = (("--campaign", "Not A Slug", "--out", str(self.tmp / "a")),
                 ("--campaign", "fine-name", "--out", str(existing)))
        for argv in cases:
            with self.subTest(argv=argv):
                code, _, err = self.cli("idea", "promote", idea["id"], *argv)
                self.assertEqual(code, 2)
                self.assertTrue(err.startswith("ERROR:"))
        self.assertFalse((self.tmp / "a").exists())
        self.assertEqual(ideas.listing(self.inbox, status="open")[0]["id"], idea["id"])
        self.assertEqual(self.cli("idea", "promote", "idea-00000000", "--campaign", "x1", "--out",
                                  str(self.tmp / "b"))[0], 2)


class StatuslineTests(_TempState):
    PATTERN = re.compile(r"^RW \d+\.\d+\.\d+ \| drafts \d+ \| queued \d+( \| sending \d+)? \| needs review \d+"
                         r"( \| ideas (\d+|\?))?( \| PAUSED)?$")

    def test_no_database_prints_no_workspace_and_creates_nothing(self):
        for argv in (("statusline",), ("--db", str(self.db), "statusline")):
            with self.subTest(argv=argv):
                with mock.patch.dict(os.environ, {"REACHWEAVER_DB": str(self.db)}):
                    code, out, err = run_cli(*argv)
                self.assertEqual((code, out, err), (0, statusline.NO_WORKSPACE + "\n", ""))
        self.assertFalse(self.db.parent.exists(), "the status line must never create the state folder")

    def test_counts_paused_and_ideas_without_changing_the_database(self):
        with connect(str(self.db)) as con:
            secret_text = "Draft text SECRET-MARKER-123 must never reach a status bar."
            ids = [add_post(con, "my-bluesky", "bluesky", f"{secret_text} {n}") for n in range(4)]
            con.execute("UPDATE posts SET status='approved' WHERE id=?", (ids[1],))
            con.execute("UPDATE posts SET status='needs_review' WHERE id=?", (ids[2],))
            con.execute("UPDATE posts SET status='scheduled' WHERE id=?", (ids[3],))
        self.add()
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, out, err = self.cli("statusline")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"RW {__version__} | drafts 1 | queued 2 | needs review 1 | ideas 1\n")
        self.assertRegex(out.strip(), self.PATTERN)
        with connect(str(self.db)) as con:
            set_paused(con, True)
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        out = statusline.render(str(self.db), None)
        self.assertTrue(out.endswith(" | PAUSED"), out)
        self.assertNotIn("SECRET-MARKER", out)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual([p.name for p in self.db.parent.iterdir() if p.name.endswith(("-journal", "-wal", "-shm"))],
                         [])

    def test_unreadable_database_and_bad_home_never_raise(self):
        self.db.parent.mkdir(mode=0o700)
        self.db.write_bytes(b"not a database at all" * 10)
        self.assertEqual(statusline.render(str(self.db), None), "RW: database unreadable")
        self.assertEqual(statusline.render(None, self.tmp / "no-such-workspace"), statusline.NO_WORKSPACE)
        self.inbox.write_text("{damaged", encoding="utf-8")
        self.db.unlink()
        with connect(str(self.db)):
            pass
        self.assertTrue(statusline.render(str(self.db), None).endswith("| ideas ?"))

    def test_fast_module_entry_ignores_stdin_and_is_quick(self):
        with connect(str(self.db)) as con:
            add_post(con, "my-bluesky", "bluesky", "One synthetic draft.")
        payload = json.dumps({"model": {"display_name": "x"}, "workspace": {"current_dir": str(self.tmp)},
                              "session_id": "synthetic"})
        env = {k: v for k, v in os.environ.items() if k != "REACHWEAVER_DB"}
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            done = subprocess.run([sys.executable, "-m", "reachweaver.statusline", "--db", str(self.db)],
                                  input=payload, capture_output=True, text=True, cwd=self.tmp, env=env, timeout=30)
            timings.append(time.perf_counter() - start)
            self.assertEqual((done.returncode, done.stderr), (0, ""))
            self.assertEqual(done.stdout, f"RW {__version__} | drafts 1 | queued 0 | needs review 0\n")
        # Generous bound for slow CI; the target (about 0.07 s on a laptop) is documented in docs/CLAUDE-CODE.md.
        self.assertLess(min(timings), 2.0)
        empty = self.tmp / "empty-project"
        empty.mkdir()
        done = subprocess.run([sys.executable, "-m", "reachweaver", "statusline"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, cwd=empty, env=env, timeout=30)
        self.assertEqual(done.stdout, statusline.NO_WORKSPACE + "\n")

    def test_the_fast_path_does_not_import_the_full_cli(self):
        code = ("import sys, reachweaver.statusline as s; s.render(None, None); "
                "print('reachweaver.cli' in sys.modules, 'reachweaver.publishers' in sys.modules)")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=self.tmp, timeout=30,
                              env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(done.stdout.strip(), "False False")


class FactCheckAgentTests(unittest.TestCase):
    AGENT = ROOT / "plugin/agents/fact-check.md"
    ALLOWED_TOOLS = {"Read", "Grep", "Glob", "WebFetch"}

    def frontmatter(self) -> tuple[dict, str]:
        text = self.AGENT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head, body = text[4:].split("\n---\n", 1)
        fields = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
        return fields, body

    def test_agent_is_read_only_and_uses_plugin_supported_fields(self):
        # Fields: https://code.claude.com/docs/en/sub-agents (read 2026-10-20): only name and description are
        # required; plugin subagents ignore hooks, mcpServers and permissionMode.
        fields, body = self.frontmatter()
        self.assertEqual(fields["name"], "fact-check")
        self.assertRegex(fields["name"], r"^[a-z][a-z0-9-]{1,63}$")
        self.assertTrue(0 < len(fields["description"]) <= 1024)
        tools = {t.strip() for t in fields["tools"].split(",")}
        self.assertEqual(tools, self.ALLOWED_TOOLS)
        for key in ("hooks", "mcpServers", "permissionMode", "disallowedTools", "initialPrompt"):
            self.assertNotIn(key, fields)
        for label in ("`verified`", "`unverified`", "`contradicted`"):
            self.assertIn(label, body)
        self.assertIn("Never edit, create or delete files", body)
        self.assertIn("untrusted data", body)

    def test_agent_lives_only_in_the_plugin(self):
        # .claude/agents/ is not part of the published tree, so repository users get the agent through the plugin.
        self.assertFalse((ROOT / ".claude/agents/fact-check.md").exists())
        self.assertEqual(sorted(p.name for p in (ROOT / "plugin/agents").iterdir()), ["fact-check.md"])


if __name__ == "__main__":
    unittest.main()
