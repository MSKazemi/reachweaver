"""Offline peer benchmark from hand-copied public counters (public issue #11). No network, no state."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from reachweaver import peer_benchmark
from reachweaver.cli import main
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/peer-benchmark.example.json"
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def entry(label, own=False, followers=100, kind="project", platform="mastodon", observed="2026-10-14T09:00:00+00:00", **extra):
    return {"label": label, "platform": platform, "kind": kind, "own": own, "public_data_only": True,
            "source_url": f"https://social.example/@{label.lower()}", "observed_at": observed,
            "counters": {"followers": followers, **extra}}


def doc(*entries):
    return {"schema_version": peer_benchmark.SCHEMA, "entries": list(entries)}


class PeerBenchmarkTests(unittest.TestCase):
    def load(self, data, **kwargs):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "peers.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return peer_benchmark.load_peers(path, **kwargs)

    def test_the_example_compares_followers_and_posts(self):
        data = peer_benchmark.load_peers(EXAMPLE)
        result = peer_benchmark.benchmark(data, now=NOW)
        [report] = result["reports"]
        followers = next(c for c in report["counters"] if c["counter"] == "followers")
        self.assertEqual((followers["own"], followers["peer_median"], followers["peer_min"], followers["peer_max"]),
                         (420, 610, 150, 900))
        self.assertEqual((followers["peers_at_or_below_own"], followers["ratio_to_peer_median"]), (1, 0.689))
        self.assertEqual(report["warnings"], [])
        self.assertEqual(result["status"], "offline_descriptive_not_a_ranking")

    def test_fewer_than_three_peers_gives_no_comparison(self):
        data = self.load(doc(entry("Me", own=True), entry("A", followers=10), entry("B", followers=20)))
        [report] = peer_benchmark.benchmark(data, now=NOW)["reports"]
        row = report["counters"][0]
        self.assertEqual(row["status"], "insufficient_peers")
        self.assertNotIn("peer_median", row)

    def test_a_missing_counter_is_unknown_never_zero(self):
        peers = [entry(n, followers=v, posts=p) for n, v, p in (("A", 10, 1), ("B", 20, 2), ("C", 30, 3))]
        me = entry("Me", own=True, followers=15)  # no posts counter
        [report] = peer_benchmark.benchmark(self.load(doc(me, *peers)), now=NOW)["reports"]
        posts = next(c for c in report["counters"] if c["counter"] == "posts")
        self.assertIsNone(posts["own"])
        self.assertEqual(posts["status"], "unknown")

    def test_dates_far_apart_or_old_raise_warnings(self):
        peers = [entry("A", observed="2026-06-01T00:00:00+00:00"), entry("B"), entry("C")]
        [report] = peer_benchmark.benchmark(self.load(doc(entry("Me", own=True), *peers)), now=NOW)["reports"]
        text = " ".join(report["warnings"])
        self.assertIn("more than 30 days apart", text)
        self.assertIn("over 90", text)

    def test_exactly_one_own_entry_per_platform(self):
        peers = [entry(n) for n in "ABC"]
        [none] = peer_benchmark.benchmark(self.load(doc(*peers)), now=NOW)["reports"]
        self.assertTrue(any("Exactly one entry must have own: true" in w for w in none["warnings"]))
        [two] = peer_benchmark.benchmark(self.load(doc(entry("Me", own=True), entry("Me2", own=True), *peers)), now=NOW)["reports"]
        self.assertTrue(any("More than one own entry" in w for w in two["warnings"]))

    def test_platforms_are_never_pooled(self):
        mastodon = [entry("Me", own=True)] + [entry(n) for n in "ABC"]
        bluesky = [entry("MeB", own=True, platform="bluesky", followers=5)] + [entry(n, platform="bluesky", followers=500) for n in "DEF"]
        result = peer_benchmark.benchmark(self.load(doc(*mastodon, *bluesky)), now=NOW)
        by = {r["platform"]: r for r in result["reports"]}
        self.assertEqual(by["bluesky"]["counters"][0]["peer_median"], 500)
        self.assertEqual(by["mastodon"]["counters"][0]["peer_median"], 100)

    def test_individuals_are_refused_unless_explicitly_included(self):
        data = doc(entry("Me", own=True), entry("Person", kind="person"), entry("B"), entry("C"))
        with self.assertRaisesRegex(ValidationError, "Individual people are excluded"):
            self.load(data)
        self.load(data, include_individuals=True)

    def test_private_or_unsafe_input_is_refused_without_echo(self):
        base = doc(entry("Me", own=True), entry("A"), entry("B"), entry("C"))
        probe = "very-private-value-123"
        mutations = {
            "not public": lambda d: d["entries"][1].update(public_data_only=False),
            "login url": lambda d: d["entries"][1].update(source_url="https://user:pw@social.example/@a"),
            "query url": lambda d: d["entries"][1].update(source_url="https://social.example/@a?token=" + probe),
            "http url": lambda d: d["entries"][1].update(source_url="http://social.example/@a"),
            "private metric": lambda d: d["entries"][1]["counters"].update(impressions=5),
            "negative": lambda d: d["entries"][1]["counters"].update(followers=-1),
            "float": lambda d: d["entries"][1]["counters"].update(followers=1.5),
            "bool": lambda d: d["entries"][1]["counters"].update(followers=True),
            "naive time": lambda d: d["entries"][1].update(observed_at="2026-10-14T09:00:00"),
            "extra key": lambda d: d["entries"][1].update(email=probe),
            "secret key": lambda d: d["entries"][1].update(access_token=probe),
            "unknown platform": lambda d: d["entries"][1].update(platform="myspace"),
            "duplicate": lambda d: d["entries"].append(copy.deepcopy(d["entries"][1])),
            "own not bool": lambda d: d["entries"][1].update(own="yes"),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label):
                data = copy.deepcopy(base)
                mutate(data)
                with self.assertRaises(ValidationError) as caught:
                    self.load(data)
                self.assertNotIn(probe, str(caught.exception))

    def test_wrong_schema_malformed_json_and_size_are_refused(self):
        with self.assertRaises(ValidationError):
            self.load({"schema_version": "other", "entries": []})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bad = Path(tmp.name) / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValidationError):
            peer_benchmark.load_peers(bad)
        big = Path(tmp.name) / "big.json"
        big.write_text(" " * (peer_benchmark.MAX_BYTES + 1), encoding="utf-8")
        with self.assertRaises(ValidationError):
            peer_benchmark.load_peers(big)

    def test_the_cli_is_offline_and_creates_no_database(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
                mock.patch("reachweaver.publishers._send_http", side_effect=AssertionError("network")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            db = Path(tmp) / "none.sqlite"
            code = main(["--db", str(db), "peer-benchmark", "--file", str(EXAMPLE), "--now", "2026-10-15T00:00:00+00:00", "--json"])
            self.assertFalse(db.exists())
        self.assertEqual(code, 0, err.getvalue())
        result = json.loads(out.getvalue())
        self.assertEqual(result["kind"], "peer_benchmark")
        self.assertTrue(any("Nothing was fetched" in line for line in result["limits"]))
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            main(["peer-benchmark", "--file", str(EXAMPLE), "--now", "2026-10-15T00:00:00+00:00"])
        self.assertIn("own is at or above 1 of 3 peers", out2.getvalue())

    def test_the_command_has_no_fetch_path(self):
        import ast
        tree = ast.parse((ROOT / "reachweaver/peer_benchmark.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(("." * node.level) + (node.module or ""))
        self.assertEqual(imported, {"__future__", "json", "re", "collections", "datetime", "pathlib", "statistics",
                                    "urllib.parse", ".policy", ".profile_audit"})


if __name__ == "__main__":
    unittest.main()
