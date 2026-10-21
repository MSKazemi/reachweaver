"""Composer UX round 3 (2026-10-21): "Your networks", the account a link names, numbers typed in the browser,
"(copy)" duplicates and idea labels without ids.

Owner-approved write path only (save, duplicate, Mark as posted via storage.record_manual_publish, numbers via
storage.store_metrics on a post this composer recorded by hand). Test servers bind 127.0.0.1; synthetic accounts,
captions and links; a temporary database. No provider, credential or network call; nothing may approve, schedule or
send.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from argparse import Namespace

from reachweaver import posted, share_browser, share_composer_store as store, ui_assets
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect, pending, record_manual_publish

from test_share_browser_composer import CAPTION, ComposerBase

BSKY_WORK = "https://bsky.app/profile/work-creator.bsky.social/post/3kux3synthetic1"
BSKY_HOME = "https://bsky.app/profile/home-creator.bsky.social/post/3kux3synthetic2"
BSKY_OTHER = "https://bsky.app/profile/someone-else.bsky.social/post/3kux3synthetic3"
MASTO = "https://social.example.org/@creator/113000000000000001"
# Synthetic public account entries: names of credential variables only (env_prefix), never a credential.
ACCOUNTS = {"accounts": [
    {"id": "work-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX3_WORK",
     "handle": "work-creator.bsky.social"},
    {"id": "home-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX3_HOME",
     "handle": "home-creator.bsky.social"},
    {"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_UX3_MASTO",
     "server": "https://social.example.org", "handle": "creator"},
    {"id": "my-linkedin", "platform": "linkedin", "env_prefix": "REACHWEAVER_UX3_LI", "person_id": "synthPerson1",
     "linkedin_version": "202609"},
    {"id": "my-devto", "platform": "devto", "env_prefix": "REACHWEAVER_UX3_DEV", "username": "creator"},
    {"id": "sample-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_UX3_SAMPLE",
     "handle": "example.bsky.social"},  # an unedited example placeholder: never "Your network", never resolved
]}


class UX3Base(ComposerBase):
    def setUp(self):
        super().setUp()
        (self.root / "config").mkdir()
        self.accounts_file = self.root / "config" / "accounts.json"
        self.accounts_file.write_text(json.dumps(ACCOUNTS), encoding="utf-8")

    def saved(self, platforms=("bluesky",)) -> str:
        return self.save_new({"platforms": list(platforms), "text": CAPTION})["package"]["name"]

    def mark(self, name, url, **extra):
        return self.post("/api/mark-posted", dict({"package": name, "platform": extra.pop("platform", "bluesky"),
                                                   "url": url}, **extra))

    def count(self, sql: str, *args) -> int:
        if not Path(self.db).exists():
            return 0
        with connect(self.db) as con:
            return con.execute(sql, args).fetchone()[0]

    def nothing_recorded(self, name: str) -> None:
        self.assertFalse(list(self.shares.glob(f"{name}/*/{store.POSTED_FILE}")))
        self.assertEqual(self.count("SELECT COUNT(*) FROM posts"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM metric_snapshots"), 0)

    def never_sendable(self) -> None:
        with connect(self.db) as con:
            self.assertEqual(pending(con), [])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts WHERE status!='published_manual'").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM posts WHERE approved_at IS NOT NULL").fetchone()[0], 0)


class YourNetworksTests(UX3Base):
    def test_configured_networks_come_from_accounts_in_file_order_without_placeholders(self):
        code, data = self.post("/api/accounts", {})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["your_networks"], ["bluesky", "mastodon", "linkedin", "dev"])  # devto -> dev
        catalog_ids = {p["id"] for p in share_browser.catalog()}
        self.assertTrue(set(data["your_networks"]) <= catalog_ids)
        self.assertNotIn("REACHWEAVER_", json.dumps(data))

    def test_no_or_broken_accounts_file_means_no_networks(self):
        self.accounts_file.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.post("/api/accounts", {})[1]["your_networks"], [])
        self.accounts_file.unlink()
        self.assertEqual(self.post("/api/accounts", {})[1]["your_networks"], [])


class LinkAccountTests(UX3Base):
    def test_skip_uses_the_account_the_link_names_like_the_cli(self):
        name = self.saved()
        code, data = self.mark(name, BSKY_WORK, account_id="")
        self.assertEqual(code, 200, data)
        self.assertEqual((data["posted"]["account_id"], data["posted"]["account_from"]), ("work-bluesky", "link"))
        # The same answer as `reachweaver posted LINK` for this link and these accounts.
        loaded = {a["id"]: a for a in ACCOUNTS["accounts"]}
        self.assertEqual(posted.pick_account("bluesky", BSKY_WORK, loaded, None, ["sample-bluesky"])[0], "work-bluesky")
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT account_id FROM posts").fetchone()[0], "work-bluesky")
        record = json.loads((self.shares / name / "bluesky" / store.POSTED_FILE).read_text(encoding="utf-8"))
        self.assertEqual(record["account_id"], "work-bluesky")

    def test_resolution_mirrors_posted_for_every_link_shape(self):
        loaded = {a["id"]: a for a in ACCOUNTS["accounts"]}
        holders = ["sample-bluesky"]
        cases = [("bluesky", BSKY_WORK, "work-bluesky"), ("bluesky", BSKY_HOME, "home-bluesky"),
                 ("bluesky", BSKY_OTHER, None), ("mastodon", MASTO, "my-mastodon"),
                 ("mastodon", "https://other.example.net/@creator/113000000000000001", None),  # another server
                 ("bluesky", "https://bsky.app/profile/did:plc:abc123/post/3kux3", None),
                 ("bluesky", "https://bsky.app/profile/example.bsky.social/post/3kux3", None)]  # placeholder only
        for ledger, url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(store.account_from_link(ledger, url, loaded, holders), expected)
                if expected is not None:  # the CLI agrees whenever the link decides
                    self.assertEqual(posted.pick_account(ledger, url, loaded, None, holders)[0], expected)

    def test_a_chosen_account_the_link_contradicts_is_refused_before_any_write(self):
        name = self.saved()
        code, data = self.mark(name, BSKY_WORK, account_id="home-bluesky")
        self.assertEqual(code, 422, data)
        self.assertIn("which is your account work-bluesky, not home-bluesky", data["errors"][0])
        self.assertIn("Nothing was recorded", data["errors"][0])
        code, data = self.mark(name, BSKY_OTHER, account_id="work-bluesky")
        self.assertEqual(code, 422, data)
        self.assertIn("@someone-else.bsky.social, not by work-bluesky", data["errors"][0])
        self.nothing_recorded(name)

    def test_a_chosen_account_the_link_agrees_with_or_cannot_judge_is_kept(self):
        name = self.saved(("bluesky", "linkedin"))
        code, data = self.mark(name, BSKY_WORK, account_id="work-bluesky")
        self.assertEqual(code, 200, data)
        self.assertEqual((data["posted"]["account_id"], data["posted"]["account_from"]), ("work-bluesky", "chosen"))
        code, data = self.mark(name, "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/",
                               platform="linkedin", account_id="my-linkedin")
        self.assertEqual(code, 200, data)
        self.assertEqual(data["posted"]["account_id"], "my-linkedin")

    def test_no_clue_in_the_link_records_without_an_account(self):
        name = self.saved()
        code, data = self.mark(name, BSKY_OTHER)
        self.assertEqual(code, 200, data)
        self.assertIsNone(data["posted"]["account_id"])
        self.assertIsNone(data["posted"]["account_from"])

    def test_link_account_route_is_read_only_and_quiet_on_bad_input(self):
        before = self.accounts_file.read_bytes()
        cases = [({"platform": "bluesky", "url": BSKY_WORK}, "work-bluesky"),
                 ({"platform": "mastodon", "url": MASTO}, "my-mastodon"),
                 ({"platform": "bluesky", "url": BSKY_OTHER}, None),
                 ({"platform": "bluesky", "url": "not a link"}, None),
                 ({"platform": "nowhere", "url": BSKY_WORK}, None),
                 ({"platform": 7, "url": BSKY_WORK}, None),
                 ({"platform": "bluesky", "url": ["x"]}, None)]
        for body, expected in cases:
            with self.subTest(body=body):
                code, data = self.post("/api/link-account", body)
                self.assertEqual(code, 200, data)
                self.assertEqual(data, {"account_id": expected})
        self.assertEqual(self.post("/api/link-account", {"platform": "bluesky", "url": BSKY_WORK, "x": 1})[0], 422)
        self.assertEqual(self.accounts_file.read_bytes(), before)
        self.assertFalse(Path(self.db).exists())


class NumbersTests(UX3Base):
    def snapshots(self):
        with connect(self.db) as con:
            return [dict(r) for r in con.execute("SELECT post_id, source, likes, replies, reposts, views "
                                                 "FROM metric_snapshots ORDER BY observed_at")]

    def test_mark_posted_with_numbers_stores_one_hand_entered_snapshot(self):
        name = self.saved()
        code, data = self.mark(name, BSKY_WORK, numbers={"likes": 12, "replies": 3, "reposts": 0, "views": None})
        self.assertEqual(code, 200, data)
        post_id = data["posted"]["post_id"]
        self.assertEqual(data["posted"]["numbers"], {"likes": 12, "replies": 3, "reposts": 0})
        self.assertEqual(self.snapshots(), [{"post_id": post_id, "source": "entered-by-hand", "likes": 12,
                                             "replies": 3, "reposts": 0, "views": None}])
        record = json.loads((self.shares / name / "bluesky" / store.POSTED_FILE).read_text(encoding="utf-8"))
        self.assertNotIn("numbers", record)  # numbers live in the history database only
        self.never_sendable()

    def test_bad_numbers_are_refused_before_anything_is_written(self):
        name = self.saved()
        bad = [{"likes": "12"}, {"likes": 1.5}, {"likes": True}, {"likes": -1}, {"likes": 10 ** 9 + 1},
               {"stars": 4}, [12], "12", {"likes": {"n": 1}}]
        for numbers in bad:
            with self.subTest(numbers=numbers):
                code, data = self.mark(name, BSKY_WORK, numbers=numbers)
                self.assertEqual(code, 422, data)
                self.assertIn("Nothing was recorded", data["errors"][0])
                self.nothing_recorded(name)
        code, data = self.mark(name, BSKY_WORK, numbers={"likes": 10 ** 9, "views": 0})
        self.assertEqual(code, 200, data)

    def test_update_numbers_adds_a_snapshot_for_the_post_this_package_recorded(self):
        name = self.saved()
        post_id = self.mark(name, BSKY_WORK)[1]["posted"]["post_id"]
        self.assertEqual(self.count("SELECT COUNT(*) FROM metric_snapshots"), 0)
        code, data = self.post("/api/numbers", {"package": name, "platform": "bluesky",
                                                "numbers": {"likes": 40, "views": 900}})
        self.assertEqual(code, 200, data)
        self.assertEqual((data["post_id"], data["numbers"], data["results"]),
                         (post_id, {"likes": 40, "views": 900}, "reachweaver ui"))
        self.assertEqual(self.snapshots(), [{"post_id": post_id, "source": "entered-by-hand", "likes": 40,
                                             "replies": None, "reposts": None, "views": 900}])
        self.never_sendable()

    def test_update_numbers_refusals_write_nothing(self):
        name = self.saved(("bluesky", "threads"))
        self.mark(name, BSKY_WORK)
        cases = [({"package": name, "platform": "threads", "numbers": {"likes": 1}}, 422, "Mark this network"),
                 ({"package": name, "platform": "bluesky", "numbers": {}}, 422, "at least one number"),
                 ({"package": name, "platform": "bluesky", "numbers": {"likes": "1"}}, 422, "whole number"),
                 ({"package": name, "platform": "instagram", "numbers": {"likes": 1}}, 422, "not in this package"),
                 ({"package": "share-../../x", "platform": "bluesky", "numbers": {"likes": 1}}, 422, "Unknown package"),
                 ({"package": name, "platform": "bluesky", "numbers": {"likes": 1}, "post_id": "x"}, 422,
                  "Unexpected fields")]
        for body, status, words in cases:
            with self.subTest(body=body):
                code, data = self.post("/api/numbers", body)
                self.assertEqual(code, status, data)
                self.assertIn(words, data["errors"][0])
        self.assertEqual(self.count("SELECT COUNT(*) FROM metric_snapshots"), 0)

    def test_numbers_are_refused_for_a_post_not_recorded_by_hand_from_this_package(self):
        name = self.saved()
        self.mark(name, BSKY_WORK)
        marker = self.shares / name / "bluesky" / store.POSTED_FILE
        original = json.loads(marker.read_text(encoding="utf-8"))
        with connect(self.db) as con:
            draft = add_post(con, "work-bluesky", "bluesky", "A synthetic draft that is not posted")
            other = record_manual_publish(con, platform="bluesky", url=BSKY_HOME, content="other",
                                          link_is_enough=True)["post_id"]
        tampered = [dict(original, post_id=draft),  # a draft (sendable after approval): never
                    dict(original, post_id=other),  # another hand record with another link: never
                    dict(original, post_id="../../etc"), dict(original, url=BSKY_OTHER)]
        for record in tampered:
            with self.subTest(record=record):
                marker.write_text(json.dumps(record), encoding="utf-8")
                code, data = self.post("/api/numbers", {"package": name, "platform": "bluesky",
                                                        "numbers": {"likes": 5}})
                self.assertEqual(code, 422, data)
        self.assertEqual(self.count("SELECT COUNT(*) FROM metric_snapshots"), 0)
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT status FROM posts WHERE id=?", (draft,)).fetchone()[0], "draft")


class DuplicateAndIdeaLabelTests(UX3Base):
    def test_duplicate_names_the_new_package_and_its_title_says_copy(self):
        original = self.save_new({"platforms": ["threads"], "text": CAPTION})["package"]
        code, data = self.post("/api/package/duplicate", {"package": original["name"]})
        self.assertEqual(code, 200, data)
        copy = data["package"]
        self.assertNotEqual(copy["name"], original["name"])
        self.assertIn(store.COPY_SUFFIX + " · ", copy["display_name"])
        self.assertNotIn(store.COPY_SUFFIX, original["display_name"])
        self.assertIn(copy["folder"], data["notice"])
        self.assertNotIn(original["name"], data["notice"])
        names = {p["name"]: p["display_name"] for p in self.post("/api/packages")[1]["packages"]}
        self.assertEqual(names[original["name"]], original["display_name"])  # the original is unchanged

    def test_idea_label_is_the_text_shortened_never_the_id(self):
        short = share_browser.idea_label("Behind the scenes of the speckled mugs")
        self.assertEqual(short, "your idea “Behind the scenes of the speckled mugs”")
        long = share_browser.idea_label("word " * 40)
        self.assertTrue(long.endswith("…”"), long)
        self.assertLessEqual(len(long), len("your idea “”") + share_browser.IDEA_LABEL_CHARS + 1)
        self.assertNotIn("‮", share_browser.idea_label("evil‮flip"))


class PageStringTests(unittest.TestCase):
    """Static checks of the shipped pages; the clicks, overflow and targets ran in a real browser."""

    def test_composer_page(self):
        html = share_browser.HTML
        for needle in ("Your networks", "your_networks", "Update numbers", "Likes, Replies, Reposts, Views",
                       "optional, as shown in the app", "(from the link)", '"visibilitychange"', "/api/link-account",
                       "/api/numbers", "See it in your results", "Duplicated as a new package", "your idea “",
                       'class="results" id="results-top"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)
        for gone in ('"idea " + data.idea.id', "reachweaver metrics", "white-space: nowrap; overflow: hidden; }"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, html)
        self.assertRegex(html, r"\.skip:focus \{[^}]*min-height: 44px")
        self.assertRegex(html, r"@media \(max-width: 640px\) \{[^@]*\.netlist a \{[^}]*min-height: 44px")

    def test_dashboard_page(self):
        js, css = ui_assets.APP_JS, ui_assets.APP_CSS
        self.assertIn("Pick one and write it in the composer", js)
        self.assertIn("Or write a new post", js)
        self.assertNotIn("'reachweaver next' names each one", js)
        self.assertRegex(css, r"\.skip:focus \{[^}]*min-height: var\(--tap\)")
        self.assertRegex(css, r"\.writecta \{[^}]*min-height: var\(--tap\)")


class CliParityTests(unittest.TestCase):
    def test_posted_hint_parses_and_names_the_link(self):
        from reachweaver.cli_parser import make_parser
        hint = share_browser.POSTED_NEXT.format(url=BSKY_WORK)
        args = make_parser().parse_args(hint.split()[1:])
        self.assertEqual((args.command, args.url), ("posted", BSKY_WORK))
        self.assertIsInstance(args, Namespace)

    def test_clean_numbers_is_strict(self):
        self.assertEqual(store.clean_numbers(None), {})
        self.assertEqual(store.clean_numbers({"likes": 0, "views": None}), {"likes": 0})
        with self.assertRaises(ValidationError):
            store.clean_numbers({}, required=True)


if __name__ == "__main__":
    unittest.main()
