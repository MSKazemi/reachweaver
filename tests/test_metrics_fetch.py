"""Read-only public-counter polling (issue #15 slice). All HTTP is mocked; no live requests.

The lowest network seam, ``reachweaver.publishers._send_http``, is replaced by a
recorder that answers from canned JSON, so every test can also assert that only
GET requests (the XRPC query or the Mastodon status GET) would leave the process.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from reachweaver import metrics_fetch
from reachweaver.accounts import load_accounts
from reachweaver.cli import main
from reachweaver.storage import add_post, connect

DID = "did:plc:abcdefghijklmnop"
OTHER_DID = "did:plc:zzzzzzzzzzzzzzzz"
MASTO_TOKEN = "mock-masto-read-token-7f3a"
MASTO_ENV = {"REACHWEAVER_MF_MASTO_TOKEN": MASTO_TOKEN}
FIXED_NOW = "2026-10-08T12:00:00+00:00"


def at_uri(rkey: str, did: str = DID) -> str:
    return f"at://{did}/app.bsky.feed.post/{rkey}"


def bsky_view(rkey: str, did: str = DID, **counts) -> dict:
    return {"uri": at_uri(rkey, did), "cid": "bafyreiexamplecid", "indexedAt": FIXED_NOW,
            "author": {"did": did, "handle": "me.bsky.social"}, "record": {"text": "hi"}, **counts}


def masto_status(status_id: str, username: str = "alice", **counts) -> dict:
    return {"id": status_id, "uri": f"https://mastodon.social/users/{username}/statuses/{status_id}",
            "url": f"https://mastodon.social/@{username}/{status_id}", "visibility": "public",
            "account": {"id": "1", "username": username, "acct": username}, **counts}


class FakeResponse:
    def __init__(self, data: dict):
        self.raw = json.dumps(data).encode("utf-8")

    def read(self, limit: int = -1) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNetwork:
    """Stand-in for _send_http: records (method, url, headers) and replays canned answers."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, req):
        self.calls.append((req.get_method(), req.full_url, dict(req.header_items())))
        answer = self.answers.pop(0)
        if isinstance(answer, int):
            raise HTTPError(req.full_url, answer, "error", {}, None)  # type: ignore[arg-type]
        return FakeResponse(answer)


class MetricsFetchTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        base = Path(self.work.name)
        self.db = str(base / "state" / "outbox.sqlite")
        self.config = base / "accounts.json"
        self.config.write_text(json.dumps({"accounts": [
            {"id": "mf-bluesky", "platform": "bluesky", "env_prefix": "REACHWEAVER_MF_BSKY",
             "handle": "me.bsky.social"},
            {"id": "mf-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_MF_MASTO",
             "server": "https://mastodon.social", "handle": "alice@mastodon.social"},
            {"id": "mf-telegram", "platform": "telegram", "env_prefix": "REACHWEAVER_MF_TG",
             "chat_id": "@examplechannel"},
        ]}), encoding="utf-8")
        self.accounts = load_accounts(self.config)
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                      if not k.startswith("REACHWEAVER_")}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        clock = patch("reachweaver.metrics_fetch.utc_now", return_value=FIXED_NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def post(self, account_id: str, *, remote_id: str | None, status: str = "published") -> str:
        platform = self.accounts[account_id]["platform"]
        with connect(self.db) as db:
            post_id = add_post(db, account_id, platform, "A verified research note", account=self.accounts[account_id])
            db.execute("UPDATE posts SET status=?, remote_id=? WHERE id=?", (status, remote_id, post_id))
        return post_id

    def run_fetch(self, network: FakeNetwork, **kwargs):
        with patch("reachweaver.publishers._send_http", side_effect=network), connect(self.db) as db:
            return metrics_fetch.fetch_metrics(db, self.accounts, **kwargs)

    def snapshots(self):
        with connect(self.db) as db:
            return [dict(r) for r in db.execute("SELECT * FROM metric_snapshots ORDER BY post_id")]

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def assert_only_get(self, network: FakeNetwork):
        self.assertTrue(network.calls)
        for method, url, _ in network.calls:
            self.assertEqual(method, "GET", url)
            path = urlsplit(url).path
            self.assertTrue(path == "/xrpc/app.bsky.feed.getPosts" or path.startswith("/api/v1/statuses/"), url)

    def test_bluesky_field_mapping_leaves_undefined_fields_null(self):
        with_saves = self.post("mf-bluesky", remote_id=at_uri("3kaaaaaaaaa22"))
        without_saves = self.post("mf-bluesky", remote_id=at_uri("3kbbbbbbbbb22"))
        network = FakeNetwork({"posts": [
            bsky_view("3kaaaaaaaaa22", likeCount=7, repostCount=2, replyCount=1, quoteCount=4, bookmarkCount=3),
            bsky_view("3kbbbbbbbbb22", likeCount=0, repostCount=5, replyCount=0),
        ]})
        summary = self.run_fetch(network)
        self.assertEqual(len(network.calls), 1)  # both URIs in one getPosts batch
        method, url, headers = network.calls[0]
        self.assertTrue(url.startswith("https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?"))
        self.assertEqual(sorted(parse_qs(urlsplit(url).query)["uris"]),
                         sorted([at_uri("3kaaaaaaaaa22"), at_uri("3kbbbbbbbbb22")]))
        self.assertNotIn("Authorization", headers)
        self.assert_only_get(network)
        rows = {r["post_id"]: r for r in self.snapshots()}
        first, second = rows[with_saves], rows[without_saves]
        self.assertEqual((first["likes"], first["reposts"], first["replies"], first["saves"]), (7, 2, 1, 3))
        self.assertEqual((second["likes"], second["reposts"], second["replies"]), (0, 5, 0))
        self.assertIsNone(second["saves"])  # bookmarkCount absent: unknown, not zero
        for row in (first, second):
            self.assertEqual(row["source"], "bluesky-public-api")
            self.assertEqual(row["observed_at"], FIXED_NOW)
            for field in ("views", "impressions", "clicks", "signups", "window_start", "window_end"):
                self.assertIsNone(row[field], field)
        entry = next(e for e in summary["fetched"] if e["post_id"] == without_saves)
        self.assertEqual(entry["fields_fetched"], {"likes": 0, "replies": 0, "reposts": 5})
        self.assertIn("saves", entry["fields_unknown"])
        self.assertIn("views", entry["fields_unknown"])

    def test_mastodon_field_mapping_unauthenticated_first(self):
        post_id = self.post("mf-mastodon", remote_id="113000000000000001")
        network = FakeNetwork(masto_status("113000000000000001", favourites_count=9, reblogs_count=4,
                                           replies_count=2, quotes_count=1))
        with patch.dict(os.environ, MASTO_ENV):
            summary = self.run_fetch(network)
        self.assertEqual(len(network.calls), 1)
        _, url, headers = network.calls[0]
        self.assertEqual(url, "https://mastodon.social/api/v1/statuses/113000000000000001")
        self.assertNotIn("Authorization", headers)  # public status: the token is never sent
        self.assert_only_get(network)
        row = self.snapshots()[0]
        self.assertEqual((row["post_id"], row["source"]), (post_id, "mastodon-api"))
        self.assertEqual((row["likes"], row["reposts"], row["replies"]), (9, 4, 2))
        for field in ("views", "impressions", "clicks", "saves", "signups"):
            self.assertIsNone(row[field], field)
        self.assertEqual(summary["fetched"][0]["auth"], "none")

    def test_mastodon_retries_once_with_token_on_404_and_token_never_printed(self):
        self.post("mf-mastodon", remote_id="113000000000000002")
        network = FakeNetwork(404, masto_status("113000000000000002", favourites_count=1,
                                                reblogs_count=0, replies_count=0))
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers._send_http", side_effect=network):
            code, out, err = self.cli("metrics-fetch", "--online")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(network.calls), 2)
        self.assertNotIn("Authorization", network.calls[0][2])
        self.assertEqual(network.calls[1][2].get("Authorization"), f"Bearer {MASTO_TOKEN}")
        self.assert_only_get(network)
        self.assertNotIn(MASTO_TOKEN, out + err)
        self.assertIn("bearer_token_retry", out)

    def test_token_never_in_output_on_failure_and_no_second_retry(self):
        self.post("mf-mastodon", remote_id="113000000000000003")
        network = FakeNetwork(401, 401)
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers._send_http", side_effect=network):
            code, out, err = self.cli("metrics-fetch", "--online")
        self.assertEqual(code, 1)
        self.assertEqual(len(network.calls), 2)  # unauthenticated + exactly one token retry
        self.assertNotIn(MASTO_TOKEN, out + err)
        self.assertIn("http_401", out)
        self.assertEqual(self.snapshots(), [])

    def test_no_token_means_no_retry(self):
        self.post("mf-mastodon", remote_id="113000000000000004")
        network = FakeNetwork(404)
        summary = self.run_fetch(network)
        self.assertEqual(len(network.calls), 1)
        self.assertEqual(summary["failed"][0]["reason"], "http_404")

    def test_bluesky_author_mismatch_rejected(self):
        self.post("mf-bluesky", remote_id=at_uri("3kccccccccc22"))
        forged = bsky_view("3kccccccccc22", likeCount=999)
        forged["author"] = {"did": OTHER_DID, "handle": "someone.else"}
        summary = self.run_fetch(FakeNetwork({"posts": [forged]}))
        self.assertEqual(summary["fetched"], [])
        self.assertIn("author_mismatch", summary["failed"][0]["reason"])
        self.assertEqual(self.snapshots(), [])

    def test_mastodon_author_mismatch_rejected(self):
        self.post("mf-mastodon", remote_id="113000000000000005")
        for status in (masto_status("113000000000000005", username="mallory", favourites_count=3),
                       {**masto_status("113000000000000005", favourites_count=3),
                        "account": {"id": "1", "username": "alice", "acct": "alice@evil.example"}},
                       masto_status("999", favourites_count=3)):
            with self.subTest(status=status["account"]):
                summary = self.run_fetch(FakeNetwork(status))
                self.assertEqual(summary["fetched"], [])
                self.assertIn("mismatch", summary["failed"][0]["reason"])
        self.assertEqual(self.snapshots(), [])

    def test_unpublished_and_unsupported_posts_skipped(self):
        draft = self.post("mf-bluesky", remote_id=None, status="draft")
        review = self.post("mf-bluesky", remote_id=at_uri("3kddddddddd22"), status="needs_review")
        no_remote = self.post("mf-mastodon", remote_id=None)
        bad_uri = self.post("mf-bluesky", remote_id="https://bsky.app/profile/x/post/y")
        telegram = self.post("mf-telegram", remote_id="42")
        network = FakeNetwork()
        summary = self.run_fetch(network)
        self.assertEqual(network.calls, [])
        reasons = {s["post_id"]: s["reason"] for s in summary["skipped"]}
        self.assertTrue(reasons[draft].startswith("not_published"))
        self.assertTrue(reasons[review].startswith("not_published"))
        self.assertEqual(reasons[no_remote], "published_without_remote_id")
        self.assertEqual(reasons[bad_uri], "remote_id_is_not_a_bluesky_post_at_uri")
        self.assertNotIn(telegram, reasons)  # other platforms are out of scope
        summary = self.run_fetch(FakeNetwork(), post_id=telegram)
        self.assertEqual(summary["skipped"][0]["reason"], "platform_not_supported_for_public_counters")

    def test_unconfigured_account_skipped(self):
        post_id = self.post("mf-bluesky", remote_id=at_uri("3keeeeeeeee22"))
        accounts = {k: v for k, v in self.accounts.items() if k != "mf-bluesky"}
        with connect(self.db) as db:
            summary = metrics_fetch.fetch_metrics(db, accounts)
        self.assertEqual(summary["skipped"], [{"post_id": post_id, "account_id": "mf-bluesky",
                                               "platform": "bluesky", "reason": "account_not_in_config"}])

    def test_429_stops_the_run_without_retry(self):
        self.post("mf-bluesky", remote_id=at_uri("3kfffffffff22"))
        first = self.post("mf-mastodon", remote_id="113000000000000006")
        second = self.post("mf-mastodon", remote_id="113000000000000007")
        network = FakeNetwork({"posts": [bsky_view("3kfffffffff22", likeCount=1)]}, 429)
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers._send_http", side_effect=network):
            code, out, err = self.cli("metrics-fetch", "--online")
        self.assertEqual(code, 1)
        self.assertEqual(len(network.calls), 2)  # one Mastodon GET answered 429: no token retry, no next post
        summary = json.loads(out)
        self.assertIn("rate_limited", summary["stopped"])
        self.assertEqual(len(summary["fetched"]), 1)  # earlier snapshot kept
        self.assertEqual(sorted(summary["not_attempted"]), sorted([first, second]))
        self.assertEqual(len(self.snapshots()), 1)

    def test_idempotent_within_the_same_second(self):
        self.post("mf-bluesky", remote_id=at_uri("3kggggggggg22"))
        for likes in (3, 3):
            self.run_fetch(FakeNetwork({"posts": [bsky_view("3kggggggggg22", likeCount=likes)]}))
        rows = self.snapshots()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["likes"], 3)

    def test_batches_of_25_and_max_posts_cap(self):
        rkeys = [f"3k{n:011d}" for n in range(30)]
        for rkey in rkeys:
            self.post("mf-bluesky", remote_id=at_uri(rkey))
        every = {"posts": [bsky_view(r, likeCount=1) for r in rkeys]}  # matched by URI, order-free
        network = FakeNetwork(every, every)
        summary = self.run_fetch(network)
        self.assertEqual([len(parse_qs(urlsplit(u).query)["uris"]) for _, u, _ in network.calls], [25, 5])
        self.assertEqual(len(summary["fetched"]), 30)
        capped = self.run_fetch(FakeNetwork({"posts": []}), max_posts=10, dry_run=True)
        self.assertEqual(len(capped["would_fetch"]), 10)
        self.assertEqual(sum(s["reason"] == "deferred_max_posts_10" for s in capped["skipped"]), 20)
        for bad in (0, 201):
            with self.subTest(max_posts=bad), connect(self.db) as db, \
                    self.assertRaisesRegex(Exception, "max_posts must be 1..200"):
                metrics_fetch.fetch_metrics(db, self.accounts, max_posts=bad)

    def test_dry_run_makes_no_request_and_no_write(self):
        self.post("mf-bluesky", remote_id=at_uri("3khhhhhhhhh22"))
        self.post("mf-mastodon", remote_id="113000000000000008")
        with patch.dict(os.environ, MASTO_ENV), patch("reachweaver.publishers._send_http") as network:
            code, out, err = self.cli("metrics-fetch", "--dry-run")
        self.assertEqual(code, 0, err)
        network.assert_not_called()
        plan = json.loads(out)
        self.assertEqual(plan["mode"], "dry_run")
        self.assertEqual({p["request"]["method"] for p in plan["would_fetch"]}, {"GET"})
        self.assertIn("REACHWEAVER_MF_MASTO_TOKEN", out)  # the variable NAME only
        self.assertNotIn(MASTO_TOKEN, out + err)
        self.assertEqual(self.snapshots(), [])

    def test_online_flag_required_and_account_filter(self):
        code, _, err = self.cli("metrics-fetch")
        self.assertEqual(code, 2)
        self.assertIn("--online", err)
        code, _, err = self.cli("metrics-fetch", "--online", "--account", "mf-telegram")
        self.assertEqual(code, 2)
        self.assertIn("only bluesky and mastodon", err)
        code, _, err = self.cli("metrics-fetch", "--online", "--account", "nope")
        self.assertEqual(code, 2)

    def test_no_post_request_ever(self):
        self.post("mf-bluesky", remote_id=at_uri("3kiiiiiiiii22"))
        self.post("mf-mastodon", remote_id="113000000000000009")
        network = FakeNetwork({"posts": [bsky_view("3kiiiiiiiii22", likeCount=2)]}, 404,
                              masto_status("113000000000000009", favourites_count=1))
        with patch.dict(os.environ, MASTO_ENV), \
                patch("reachweaver.publishers.request_json") as post_json, \
                patch("reachweaver.publishers.request_json_with_headers") as post_headers:
            summary = self.run_fetch(network)
        post_json.assert_not_called()
        post_headers.assert_not_called()
        self.assert_only_get(network)
        self.assertEqual(len(summary["fetched"]), 2)

    def test_doctor_notes_read_only_public_polling(self):
        code, out, _ = self.cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("metrics-fetch --online", out)
        self.assertIn("not private analytics", out)


FIXTURE = Path(__file__).parent / "fixtures" / "providers" / "mockstats" / "threshold_cases.json"


class MockstatsProviderTests(unittest.TestCase):
    """Third, invented provider (issue #15): different definitions and privacy thresholds. No network."""

    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE.read_text())

    def norm(self, name):
        case = self.cases[name]
        return metrics_fetch.normalize_mockstats(case["metrics"], threshold=case["threshold"])

    def test_at_or_above_threshold_is_exact_and_differing_definitions_unmapped(self):
        values = self.norm("above_threshold")
        self.assertEqual((values["views"], values["clicks"], values["saves"]), (120, 14, 10))
        for column in ("impressions", "likes", "reposts", "replies", "signups"):
            self.assertIsNone(values[column], column)  # unique_viewers / reactions_all must not leak in
        mapped = set(metrics_fetch.MOCKSTATS_FIELD_MAP)
        self.assertFalse(mapped & set(metrics_fetch.MOCKSTATS_UNMAPPED))

    def test_below_threshold_is_suppressed_and_small_number_dropped(self):
        values = self.norm("below_threshold")
        self.assertEqual(values["views"], 120)
        self.assertEqual((values["clicks"], values["saves"]), ("suppressed", "suppressed"))
        self.assertNotIn(3, values.values())
        self.assertNotIn(9, values.values())

    def test_withheld_and_pending_markers(self):
        values = self.norm("withheld_marker")
        self.assertEqual((values["views"], values["clicks"], values["saves"]),
                         ("suppressed", "not_available", "delayed"))

    def test_malformed_values_stay_unknown_never_zero(self):
        self.assertTrue(all(v is None for v in self.norm("malformed").values()))

    def test_bad_arguments_rejected(self):
        from reachweaver.policy import ValidationError
        with self.assertRaises(ValidationError):
            metrics_fetch.normalize_mockstats({}, threshold=0)
        with self.assertRaises(ValidationError):
            metrics_fetch.normalize_mockstats([])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
