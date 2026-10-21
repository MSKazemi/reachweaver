"""`reachweaver next` (and bare `suggest`): at most five concrete steps, ideas by name. Read-only, offline, synthetic."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shlex
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver import catalog, ideas, mcp_server, whats_next
from reachweaver.cli import main, make_parser
from reachweaver.storage import (add_post, claim_sending, connect, record_manual_publish, store_metrics, transition,
                                 uncertain_send)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.db = self.state / "rw.sqlite"

    def inbox(self) -> Path:
        return ideas.inbox_path(self.db)

    def by_hand(self, platform: str, text: str, *, days: float, slug: str) -> str:
        with connect(str(self.db)) as con:
            return record_manual_publish(con, platform=platform, url=f"https://example.org/{platform}/{slug}",
                                         content=text, posted_at=ago(days=days))["post_id"]

    def numbers(self, post_id: str, *, days_ago: float = 0, **values) -> None:
        with connect(str(self.db)) as con:
            store_metrics(con, post_id, ago(days=days_ago), "entered-by-hand", **values)

    def view(self) -> dict:
        return whats_next.build_next(str(self.db), now=NOW)

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, "create_connection", side_effect=AssertionError("network used")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue(), err.getvalue()


class ViewTests(Base):
    def test_nothing_yet_points_at_the_browser_composer_and_creates_nothing(self):
        view = self.view()
        self.assertEqual(view["schema"], "reachweaver.next.v1")
        self.assertEqual([a["kind"] for a in view["actions"]], ["first_post", "save_idea"])
        self.assertEqual(view["actions"][0]["command"], "reachweaver share --web --open")
        self.assertTrue(view["read_only"])
        self.assertFalse(self.state.exists())

    def test_each_open_idea_is_named_and_long_lists_are_capped_at_five(self):
        self.state.mkdir(mode=0o700)
        for n in range(7):
            ideas.add(self.inbox(), f"Idea number {n} about glazing")
        view = self.view()
        self.assertEqual(len(view["actions"]), whats_next.MAX_ACTIONS)
        self.assertEqual([a["rank"] for a in view["actions"]], [1, 2, 3, 4, 5])
        self.assertIn('"Idea number 0 about glazing"', view["actions"][0]["text"])
        self.assertEqual(view["counts"]["open_ideas"], 7)

    def test_an_idea_becomes_a_share_command_for_the_networks_you_use_quoted_for_the_shell(self):
        self.state.mkdir(mode=0o700)
        idea_text = "It's $5 & \"cheap\"; `rm -rf` $(whoami)"
        ideas.add(self.inbox(), idea_text)
        self.by_hand("instagram", "first", days=3, slug="a")
        self.by_hand("instagram", "second", days=2, slug="b")
        self.by_hand("bluesky", "third", days=1, slug="c")
        action = next(a for a in self.view()["actions"] if a["kind"] == "idea")
        argv = shlex.split(action["command"])
        self.assertEqual(argv[:4], ["reachweaver", "share", "--platform", "instagram,bluesky"])
        self.assertEqual(argv[4:], ["--text", idea_text])  # one argument, exactly the idea
        make_parser().parse_args(argv[1:])

    def test_a_long_idea_goes_to_the_browser_composer_instead_of_a_long_command(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "word " * 80)
        self.by_hand("bluesky", "posted", days=1, slug="a")
        action = next(a for a in self.view()["actions"] if a["kind"] == "idea")
        self.assertEqual(action["command"], whats_next.SHARE_WEB)
        self.assertLessEqual(len(action["text"]), 100)

    def test_drafts_results_due_and_days_since_last_post(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "Kiln opening video")
        old = self.by_hand("instagram", "Glaze test out of the kiln", days=7, slug="old")
        self.by_hand("linkedin", "Open studio", days=0.1, slug="new")
        with connect(str(self.db)) as con:
            add_post(con, "acct", "bluesky", "a draft")
        kinds = [a["kind"] for a in self.view()["actions"]]
        self.assertEqual(kinds, ["drafts", "results_due", "idea", "rhythm"])
        view = self.view()
        due = view["actions"][1]
        self.assertEqual(due["command"], f"reachweaver metrics {old} --likes N --replies N --reposts N")
        self.assertIn('"Glaze test out of the kiln"', due["text"])
        self.assertIn("7 days ago", due["text"])
        self.assertEqual(view["actions"][0]["command"], "reachweaver review")
        rhythm = view["actions"][3]
        self.assertEqual(rhythm["evidence"]["days_since_last_post"], {"instagram": 7, "linkedin": 0})
        self.assertIn("Instagram 7 days ago, LinkedIn today", rhythm["text"])
        self.numbers(old, likes=12)  # day-7 numbers recorded: no longer due
        self.assertNotIn("results_due", [a["kind"] for a in self.view()["actions"]])

    def test_early_numbers_do_not_count_as_the_7_day_numbers(self):
        post = self.by_hand("bluesky", "Thread on photographing pieces", days=8, slug="x")
        self.numbers(post, days_ago=7, likes=3)  # one day after posting
        self.assertIn("results_due", [a["kind"] for a in self.view()["actions"]])

    def test_an_unknown_send_comes_first(self):
        with connect(str(self.db)) as con:
            post = add_post(con, "acct", "bluesky", "hello")
            transition(con, post, "approve", approver="owner")
            claim_sending(con, post)
            uncertain_send(con, post, "timeout")
        first = self.view()["actions"][0]
        self.assertEqual((first["kind"], first["command"]), ("safety", "reachweaver dead-letter"))

    def test_the_database_and_inbox_are_never_written(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "An idea")
        self.by_hand("bluesky", "posted", days=8, slug="a")
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.state.iterdir()}
        self.view()
        self.cli("next")
        self.cli("next", "--json")
        self.cli("suggest")
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.state.iterdir()}
        self.assertEqual(before, after)

    def test_never_an_approve_publish_or_schedule_command(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "An idea")
        self.by_hand("bluesky", "posted", days=8, slug="a")
        with connect(str(self.db)) as con:
            add_post(con, "acct", "bluesky", "a draft")
        for action in self.view()["actions"]:
            words = shlex.split(action["command"] or "")[1:2]
            self.assertNotIn(words, (["approve"], ["publish"], ["schedule"], ["resume"]), action)


class CliTests(Base):
    def test_text_and_json(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "Behind the scenes of my studio")
        code, out, err = self.cli("next")
        self.assertEqual(code, 0, err)
        self.assertIn('1. Write a post from your idea "Behind the scenes of my studio"', out)
        self.assertIn("nothing was drafted, approved or sent", out)
        code, out, _ = self.cli("next", "--json")
        doc = json.loads(out)
        self.assertEqual(doc["schema"], "reachweaver.next.v1")
        self.assertEqual(set(doc["actions"][0]), {"rank", "kind", "text", "command", "note", "evidence"})

    def test_bare_suggest_prints_the_same_view_and_json_keeps_its_schema(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "Behind the scenes of my studio")
        _, nxt, _ = self.cli("next")
        code, out, err = self.cli("suggest")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith(nxt), out)
        self.assertIn("reachweaver suggest --include-db", out)
        code, out, _ = self.cli("suggest", "--json")
        doc = json.loads(out)
        self.assertEqual(doc["schema_version"], "reachweaver.suggestions.v1")
        self.assertNotIn("studio", out)  # counts only in the JSON an MCP client can read

    def test_suggest_include_db_now_carries_the_ideas_signal(self):
        self.state.mkdir(mode=0o700)
        ideas.add(self.inbox(), "Behind the scenes of my studio")
        self.by_hand("bluesky", "posted", days=1, slug="a")
        code, out, err = self.cli("suggest", "--include-db", "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertIn("open_ideas", [a["signal"] for a in doc["actions"]])
        self.assertIn("reachweaver next", next(a for a in doc["actions"] if a["signal"] == "open_ideas")["next_step"])
        self.assertNotIn("studio", out)


class RegistrationTests(unittest.TestCase):
    def test_next_is_read_only_everyday_and_not_an_mcp_tool(self):
        from reachweaver import cli_help
        self.assertEqual(catalog.COMMANDS["next"]["tags"], ("read_only",))
        self.assertIn("next", cli_help.EVERYDAY_NAMES)
        self.assertIn("next", mcp_server.WITHHELD_COMMANDS)
        self.assertNotIn("next", mcp_server.exposed_commands(make_parser()))


if __name__ == "__main__":
    unittest.main()
