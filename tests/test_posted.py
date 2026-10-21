"""``reachweaver posted URL``: record a post made by hand and its numbers in one step (UX re-measure 2026-10-21).

Proves the explicit host map, account picking, that a link already recorded gets its numbers ADDED instead of
failing, that a bad value writes nothing, that the record is the same never-sendable ``published_manual`` row as
``mark-shared --url`` (pending/claim/publish/approve never see it), and the discoverability surfaces (everyday help,
empty report/next, start, share, list, bare ``metrics``). Synthetic data only; no network.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gate_support import LIVE_ENV

from reachweaver import catalog, cli_help, mcp_server, posted, repeat_guard, sendgate, start, term, whats_next
from reachweaver.cli import main, make_parser
from reachweaver.policy import PLATFORMS
from reachweaver.report_format import POSTED_HINT
from reachweaver.storage import (MANUAL_STATUS, claim_sending, connect, pending, shared_entries, snapshots_for)

BSKY = {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_PT_BSKY", "handle": "owner.bsky.social"}
BSKY2 = {"id": "work-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_PT_WORK", "handle": "work.example.org"}
MASTO = {"id": "my-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_PT_MASTO",
         "server": "https://social.example.org", "handle": "owner"}
BSKY_URL = "https://bsky.app/profile/owner.bsky.social/post/3kpostaaa"
DID_URL = "https://bsky.app/profile/did:plc:abcdefghijklmnopqrstuvwx/post/3kpostbbb"
IG_URL = "https://www.instagram.com/p/SyntheticIG1/"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Tty(io.StringIO):
    def isatty(self):
        return True


class Base(unittest.TestCase):
    accounts: list[dict] | None = [BSKY, MASTO]

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.db = str(self.root / "state" / "db.sqlite")
        self.config = self.root / "accounts.json"
        if self.accounts is not None:
            self.config.write_text(json.dumps({"accounts": self.accounts}), encoding="utf-8")
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)
        sendgate.reset()
        term.set_plain(False)
        self.addCleanup(term.set_plain, False)

    def cli(self, *argv, env=None, tty=False):
        out, err = (Tty(), Tty()) if tty else (io.StringIO(), io.StringIO())
        with patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def posts(self) -> list[dict]:
        if not Path(self.db).exists():
            return []
        with connect(self.db) as db:
            return [dict(row) for row in db.execute("SELECT * FROM posts ORDER BY created_at, id")]

    def snaps(self, post_id: str) -> list[dict]:
        with connect(self.db) as db:
            return [dict(row) for row in snapshots_for(db, post_id)]


class HostMapTests(unittest.TestCase):
    def test_every_mapped_platform_is_one_mark_shared_accepts(self):
        self.assertLessEqual(set(posted.HOSTS.values()), set(PLATFORMS))

    def test_hosts_and_their_subdomains_map_and_lookalikes_do_not(self):
        cases = {
            "https://bsky.app/profile/a.bsky.social/post/1": "bluesky",
            "https://www.instagram.com/p/X/": "instagram",
            "https://www.linkedin.com/posts/someone_activity-1": "linkedin",
            "https://www.tiktok.com/@me/video/1": "tiktok",
            "https://m.youtube.com/shorts/abc": "youtube", "https://youtu.be/abc": "youtube",
            "https://www.threads.net/@me/post/abc": "threads", "https://www.threads.com/@me/post/abc": "threads",
            "https://x.com/me/status/1": "x", "https://mobile.twitter.com/me/status/1": "x",
            "https://www.facebook.com/page/posts/1": "facebook", "https://t.me/channel/12": "telegram",
            "https://dev.to/me/a-post-1abc": "devto", "https://www.reddit.com/r/x/comments/1/t/": "reddit",
            "https://www.pinterest.com/pin/1/": "pinterest", "https://me.hashnode.dev/a-post": "hashnode",
        }
        for url, platform in cases.items():
            self.assertEqual(posted.platform_from_url(url), platform, url)
        for url in ("https://notbsky.app/profile/a/post/1", "https://bsky.app.example.net/post/1",
                    "https://example.org/x", "https://instagram.com.example.org/p/1"):
            self.assertIsNone(posted.platform_from_url(url), url)

    def test_a_configured_mastodon_server_counts_and_an_unknown_one_does_not(self):
        accounts = {"my-masto": MASTO}
        self.assertEqual(posted.platform_from_url("https://social.example.org/@owner/1", accounts), "mastodon")
        self.assertIsNone(posted.platform_from_url("https://other.example.net/@owner/1", accounts))
        message = posted.unknown_host_message("https://other.example.net/@owner/1")
        self.assertIn("--platform mastodon", message)
        self.assertIn("Nothing recorded", message)

    def test_handles_are_read_only_from_known_link_shapes(self):
        self.assertEqual(posted.handle_in_url("bluesky", BSKY_URL), "owner.bsky.social")
        self.assertIsNone(posted.handle_in_url("bluesky", DID_URL))  # a DID is not a handle
        self.assertEqual(posted.handle_in_url("x", "https://x.com/Owner/status/1"), "owner")
        self.assertEqual(posted.handle_in_url("tiktok", "https://www.tiktok.com/@owner/video/1"), "owner")
        self.assertIsNone(posted.handle_in_url("instagram", IG_URL))
        self.assertIsNone(posted.handle_in_url("linkedin", "https://www.linkedin.com/posts/owner_x-activity-1"))


class RecordTests(Base):
    def test_one_command_records_the_post_and_its_numbers(self):
        code, out, err = self.cli("posted", BSKY_URL, "--likes", "12", "--replies", "3", "--text", "Release notes",
                                  "--at", "yesterday")
        self.assertEqual(code, 0, out + err)
        [post] = self.posts()
        self.assertEqual((post["status"], post["platform"], post["account_id"], post["remote_url"]),
                         (MANUAL_STATUS, "bluesky", "my-bsky", BSKY_URL))
        self.assertIsNone(post["approved_at"])
        [snap] = self.snaps(post["id"])
        self.assertEqual((snap["likes"], snap["replies"], snap["reposts"], snap["source"]),
                         (12, 3, None, "entered-by-hand"))  # a missing count stays unknown, never 0
        lines = out.strip().splitlines()
        self.assertLessEqual(len(lines), 4, out)
        self.assertIn('Recorded "Release notes" on Bluesky', lines[0])
        self.assertIn("as my-bsky", lines[0])
        self.assertIn("nothing was posted or sent", lines[0])
        self.assertIn("12 likes, 3 replies", out)
        self.assertEqual(lines[-1], "Next: reachweaver report --week")

    def test_the_same_link_again_adds_numbers_instead_of_failing(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "5", "--text", "Release notes")[0], 0)
        [post] = self.posts()
        with connect(self.db) as db:  # an earlier observation, so the second one is a new snapshot
            db.execute("UPDATE metric_snapshots SET observed_at='2026-01-01T00:00:00+00:00'")
        code, out, err = self.cli("posted", BSKY_URL + "/", "--likes", "20", "--views", "300")
        self.assertEqual(code, 0, out + err)
        self.assertIn('Updated numbers for "Release notes" on Bluesky.', out)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual([s["likes"] for s in self.snaps(post["id"])], [5, 20])
        code, out, _ = self.cli("posted", BSKY_URL)
        self.assertEqual(code, 0)
        self.assertIn("Already recorded", out)
        self.assertIn("Nothing changed", out)
        self.assertEqual(len(self.snaps(post["id"])), 2)

    def test_a_post_sent_by_reachweaver_gets_its_numbers_too(self):
        with connect(self.db) as db:
            from reachweaver.storage import add_post
            pid = add_post(db, "my-bsky", "bluesky", "Sent by the adapter")
            db.execute("UPDATE posts SET status='published', remote_url=?, send_started_at='2026-10-20T09:00:00+00:00' "
                       "WHERE id=?", (BSKY_URL, pid))
        code, out, err = self.cli("posted", BSKY_URL, "--likes", "7")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Updated numbers", out)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.posts()[0]["status"], "published")
        self.assertEqual([s["likes"] for s in self.snaps(pid)], [7])

    def test_a_link_without_text_is_enough_and_compares_nothing(self):
        code, out, err = self.cli("posted", IG_URL, "--likes", "40")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"your Instagram post {IG_URL}", out)
        self.assertIn("No Instagram account is set up here", out)
        [post] = self.posts()
        self.assertEqual((post["content"], post["account_id"]), ("", ""))
        with connect(self.db) as db:
            [entry] = shared_entries(db)
            self.assertEqual((entry["content"], entry["links"], entry["post_id"]), ("", [], post["id"]))
            found = repeat_guard.find_repeats(db, platform="instagram", account_id=None,
                                              material={"text": "", "images": [], "video": None, "links": []})
        self.assertEqual(found, [])
        code, out, _ = self.cli("report", "--format", "text")
        self.assertIn("instagram.com/…/SyntheticIG1", out)
        self.assertIn("posted by you (no account)", out)

    def test_bad_values_and_bad_links_write_nothing(self):
        for argv in (("posted", BSKY_URL, "--likes", "many"), ("posted", "https://unknown.example.net/p/1"),
                     ("posted", "not a link"), ("posted", "https://bsky.app/profile/a/post/1‮"),
                     ("posted", BSKY_URL, "--platform", "blusky"), ("posted", BSKY_URL, "--at", "2999-01-01 10:00"),
                     ("posted", BSKY_URL, "--account", "my-masto")):
            code, out, err = self.cli(*argv)
            self.assertEqual(code, 2, (argv, out, err))
            self.assertEqual(self.posts(), [], argv)
        _, _, err = self.cli("posted", "https://unknown.example.net/p/1")
        self.assertIn("Which network is unknown.example.net?", err)
        _, _, err = self.cli("posted", BSKY_URL, "--platform", "blusky")
        self.assertIn("Did you mean bluesky?", err)

    def test_bare_posted_and_bare_metrics_explain_and_create_nothing(self):
        code, _, err = self.cli("posted")
        self.assertEqual(code, 2)
        self.assertIn("Give the link to your post", err)
        code, _, err = self.cli("metrics")
        self.assertEqual(code, 2)
        self.assertIn("reachweaver posted LINK --likes N", err)
        self.assertIn("reachweaver list", err)
        self.assertNotIn("required: post_id", err)
        self.assertFalse(Path(self.db).exists())


class AccountTests(Base):
    accounts = [BSKY, BSKY2, MASTO]

    def test_the_handle_in_the_link_picks_the_account(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "1")[0], 0)
        self.assertEqual(self.posts()[0]["account_id"], "my-bsky")
        url = "https://social.example.org/@owner/109876543210"
        self.assertEqual(self.cli("posted", url, "--likes", "1")[0], 0)
        masto = [p for p in self.posts() if p["platform"] == "mastodon"][0]
        self.assertEqual((masto["account_id"], masto["remote_id"]), ("my-masto", "109876543210"))

    def test_several_accounts_and_no_clue_ask_for_account(self):
        code, _, err = self.cli("posted", DID_URL, "--likes", "1")
        self.assertEqual(code, 2)
        self.assertIn("--account with one of: my-bsky, work-bsky", err)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.cli("posted", DID_URL, "--likes", "1", "--account", "work-bsky")[0], 0)
        self.assertEqual(self.posts()[0]["account_id"], "work-bsky")

    def test_a_link_naming_someone_else_is_recorded_without_an_account(self):
        code, out, _ = self.cli("posted", "https://bsky.app/profile/someone.else/post/3kx", "--likes", "1")
        self.assertEqual(code, 0)
        self.assertIn("names @someone.else, which is none of your Bluesky accounts", out)
        self.assertEqual(self.posts()[0]["account_id"], "")


class SingleAccountTests(Base):
    accounts = [BSKY, {"id": "example-ig", "platform": "instagram", "env_prefix": "REACHWEAVER_PT_IG",
                       "handle": "example"}]  # an unedited example placeholder

    def test_the_only_account_is_picked_and_a_placeholder_never_is(self):
        self.assertEqual(self.cli("posted", DID_URL, "--likes", "1")[0], 0)
        self.assertEqual(self.posts()[0]["account_id"], "my-bsky")
        code, out, _ = self.cli("posted", IG_URL, "--likes", "1")
        self.assertEqual(code, 0)
        self.assertIn("No Instagram account is set up here", out)
        self.assertEqual([p["account_id"] for p in self.posts() if p["platform"] == "instagram"], [""])

    def test_report_names_the_account_by_its_handle(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "9", "--text", "Hello")[0], 0)
        _, out, _ = self.cli("report", "--format", "text")
        self.assertIn("Bluesky, @owner.bsky.social,", out)


class NoAccountsFileTests(Base):
    accounts = None

    def test_posting_by_hand_needs_no_accounts_file(self):
        code, out, err = self.cli("posted", BSKY_URL, "--likes", "3")
        self.assertEqual(code, 0, out + err)
        self.assertIn("No Bluesky account is set up here", out)
        self.assertEqual(self.posts()[0]["account_id"], "")


class NeverSendableTests(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "4", "--text", "By hand")[0], 0)
        self.pid = self.posts()[0]["id"]

    def test_queue_claim_publish_and_approve_never_see_it(self):
        with connect(self.db) as db:
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00"), [])
            self.assertEqual(pending(db, 100, "2099-01-01T00:00:00+00:00", post_id=self.pid), [])
            self.assertFalse(claim_sending(db, self.pid, "2099-01-01T00:00:00+00:00", max_per_24h=10))
        code, out, _ = self.cli("publish")
        self.assertEqual(code, 0)
        self.assertNotIn(self.pid, out)
        with patch("reachweaver.publishers.request_get_json") as get, \
                patch("reachweaver.publishers.request_json") as post:
            code, out, err = self.cli("publish", "--live", "--post-id", self.pid,
                                      env={**LIVE_ENV, "REACHWEAVER_PT_BSKY_APP_PASSWORD": "mock-app-password"})
        get.assert_not_called()
        post.assert_not_called()
        self.assertNotIn(f"PUBLISHED {self.pid}", out + err)
        for argv in (("approve", self.pid), ("schedule", self.pid, "--at", "2030-01-01T00:00:00+00:00")):
            self.assertEqual(self.cli(*argv)[0], 2, argv)
        self.assertEqual(self.posts()[0]["status"], MANUAL_STATUS)
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM event_log WHERE action='send_claimed'").fetchone()[0], 0)

    def test_updating_numbers_keeps_it_terminal(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "8")[0], 0)
        self.assertEqual(self.posts()[0]["status"], MANUAL_STATUS)
        self.assertIsNone(self.posts()[0]["approved_at"])

    def test_list_on_a_terminal_names_columns_and_marks_no_account(self):
        self.assertEqual(self.cli("posted", IG_URL, "--likes", "1")[0], 0)
        _, out, _ = self.cli("list", tty=True)
        plain = ANSI.sub("", out)
        self.assertIn("ACCOUNT", plain)
        self.assertIn("—", plain)
        _, piped, _ = self.cli("list")
        self.assertNotIn("—", piped)  # scripts keep the raw rows
        self.assertNotIn("ACCOUNT", piped)


class DiscoverabilityTests(Base):
    accounts = None

    def test_everyday_help_lists_posted_and_drops_check(self):
        self.assertIn("posted", cli_help.EVERYDAY_NAMES)
        self.assertNotIn("check", cli_help.EVERYDAY_NAMES)
        self.assertEqual(len(cli_help.EVERYDAY), 16)
        self.assertIn("check", {name for _, names in cli_help.GROUPS for name in names})

    def test_catalog_and_mcp(self):
        self.assertEqual(catalog.COMMANDS["posted"]["tags"], ("writes",))
        self.assertIn("posted", mcp_server.NEVER_EXPOSED)
        self.assertNotIn("posted", mcp_server.exposed_commands(make_parser(), allow_drafts=True))

    def test_empty_report_next_and_list_point_to_posted(self):
        _, out, err = self.cli("report", "--format", "text")
        self.assertIn(POSTED_HINT, out)
        self.assertIn("reachweaver posted LINK --likes N", err)
        self.assertFalse(Path(self.db).exists())
        _, out, _ = self.cli("report", "--week", "--format", "text")
        self.assertIn(POSTED_HINT, out)
        view = whats_next.build_next(self.db)
        self.assertIn(whats_next.POSTED, [a["command"] for a in view["actions"]])
        self.assertIn("reachweaver posted LINK", whats_next.render_text(view))

    def test_start_shows_posted_once_something_was_posted_and_every_command_parses(self):
        self.assertEqual(self.cli("posted", BSKY_URL, "--likes", "2")[0], 0)
        guide = start.build_guide(self.db, str(self.config))
        commands = [step["command"] for step in guide["steps"] if step["command"]]
        self.assertIn(start.POSTED, commands)
        parser = make_parser()
        for command in commands:
            if command.startswith("reachweaver "):  # a step may also be a plain shell command (cp ...)
                parser.parse_args(shlex.split(command)[1:])

    def test_share_says_how_to_record_the_post_afterwards(self):
        out_dir = self.root / "packages"
        code, out, err = self.cli("share", "--platform", "bluesky", "--text", "A small update", "--out", str(out_dir))
        self.assertEqual(code, 0, err)
        self.assertIn("After you post, record it: reachweaver posted LINK --likes N", out)
        self.assertIn("reachweaver posted LINK --likes N", (out_dir / "OPEN-FIRST.md").read_text(encoding="utf-8"))
        self.assertFalse(Path(self.db).exists())


if __name__ == "__main__":
    unittest.main()
