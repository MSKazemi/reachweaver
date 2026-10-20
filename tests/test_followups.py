"""`followup-suggest`: owner-set thresholds propose follow-up briefs for review; read-only, no engagement path."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from reachweaver import followups
from reachweaver.cli import main
from reachweaver.policy import ValidationError
from reachweaver.storage import connect, connect_readonly, store_metrics

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-10-20T12:00:00+00:00"
ROOT = Path(__file__).resolve().parents[1]


def rule(**kw):
    base = {"id": "r1", "metric": "likes", "threshold": 10, "kind": "thank_and_link"}
    base.update(kw)
    return base


class FollowupBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.db = self.base / "state" / "rw.sqlite"
        with connect(str(self.db)) as con:
            self._post(con, "p-blue", "acct-blue", "bluesky", "Shipped v1, thanks @alice and @bob.example",
                       "2026-10-18T10:00:00+00:00", "https://bsky.app/profile/o.example/post/abc")
            self._post(con, "p-masto", "acct-masto", "mastodon", "Release notes", "2026-10-19T08:00:00+00:00",
                       "https://mastodon.example/@o/1")
            self._post(con, "p-reddit", "acct-reddit", "reddit", "A community post", "2026-10-18T09:00:00+00:00",
                       "https://www.reddit.com/r/example/comments/x")
            self._post(con, "p-draft", "acct-blue", "bluesky", "Not published", None, None, status="draft")
            self._post(con, "p-old", "acct-blue", "bluesky", "Old post", "2026-09-01T08:00:00+00:00", None)
            # p-blue: 4 likes at +2h, 30 likes at +30h (exact), estimated views only
            store_metrics(con, "p-blue", "2026-10-18T12:00:00+00:00", "native-export", likes="4")
            store_metrics(con, "p-blue", "2026-10-19T16:00:00+00:00", "native-export", likes="30",
                          views="estimated:9000")
            store_metrics(con, "p-masto", "2026-10-19T20:00:00+00:00", "metrics-fetch", likes="12", reposts="3")
            store_metrics(con, "p-reddit", "2026-10-19T09:00:00+00:00", "native-export", likes="500")
            store_metrics(con, "p-old", "2026-09-02T08:00:00+00:00", "native-export", likes="50")
        self.rules_path = self.base / "rules.json"

    @staticmethod
    def _post(con, pid, account, platform, content, published_at, remote_url, status="published"):
        con.execute("INSERT INTO posts(id,account_id,platform,content,status,created_at,remote_url) "
                    "VALUES(?,?,?,?,?,?,?)", (pid, account, platform, content, status, "2026-09-01T00:00:00+00:00",
                                              remote_url))
        if published_at:
            con.execute("INSERT INTO event_log(at,post_id,action,detail) VALUES(?,?,?,?)",
                        (published_at, pid, "published", "remote_id=x"))

    def write_rules(self, rules):
        self.rules_path.write_text(json.dumps({"schema_version": 1, "kind": followups.RULES_KIND, "rules": rules}),
                                   encoding="utf-8")
        return self.rules_path

    def build(self, rules, **kw):
        with connect_readonly(self.db) as con:
            return followups.build_suggestions(con, followups.validate_rules(rules), now=NOW, **kw)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), *argv])
        return code, out.getvalue(), err.getvalue()


class SuggestionTests(FollowupBase):
    def test_threshold_crossing_uses_latest_exact_value(self):
        report = self.build([rule()])
        by_post = {s["post_id"]: s for s in report["suggestions"]}
        self.assertEqual(set(by_post), {"p-blue", "p-masto", "p-old"})
        blue = by_post["p-blue"]
        self.assertEqual((blue["value"], blue["value_status"], blue["source"]), (30, "exact", "native-export"))
        self.assertEqual(blue["snapshot_age_hours"], 20.0)
        self.assertEqual(blue["hours_after_publish"], 30.0)
        self.assertEqual(blue["label"], followups.LABEL)
        self.assertIn("RW show p-blue", blue["next_commands"])
        self.assertTrue(any(c.startswith("RW draft --account acct-blue --file followup.txt") for c in blue["next_commands"]))
        self.assertNotIn("Shipped v1", json.dumps(report))  # post text is never echoed

    def test_below_threshold_is_counted_not_suggested(self):
        report = self.build([rule(threshold=31)])
        self.assertEqual([s["post_id"] for s in report["suggestions"]], ["p-old"])
        self.assertEqual(report["counts"]["below_threshold"], 2)

    def test_within_hours_window(self):
        report = self.build([rule(within_hours=24)])  # p-blue had only 4 likes within 24 h
        ids = {s["post_id"] for s in report["suggestions"]}
        self.assertNotIn("p-blue", ids)
        self.assertIn("p-masto", ids)  # 12 likes at +12 h
        report = self.build([rule(within_hours=36)])
        self.assertIn("p-blue", {s["post_id"] for s in report["suggestions"]})

    def test_estimates_are_never_compared(self):
        report = self.build([rule(metric="views", threshold=100)])
        self.assertEqual(report["suggestions"], [])
        reasons = [s for s in report["skipped"] if s["post_id"] == "p-blue"]
        self.assertEqual(len(reasons), 1)
        self.assertIn("estimated", reasons[0]["reason"])

    def test_platform_and_account_filters(self):
        report = self.build([rule(platforms=["mastodon"])])
        self.assertEqual([s["post_id"] for s in report["suggestions"]], ["p-masto"])
        report = self.build([rule(accounts=["acct-blue"])])
        self.assertEqual({s["post_id"] for s in report["suggestions"]}, {"p-blue", "p-old"})
        report = self.build([rule()], account_id="acct-masto")
        self.assertEqual([s["post_id"] for s in report["suggestions"]], ["p-masto"])
        report = self.build([rule()], since="2026-10-01T00:00:00Z")
        self.assertNotIn("p-old", {s["post_id"] for s in report["suggestions"]})

    def test_reddit_and_hn_get_no_suggestion_and_are_never_targets(self):
        report = self.build([rule(), rule(id="r2", kind="cross_post")],
                            accounts={"acct-blue": {"platform": "bluesky"}, "acct-reddit": {"platform": "reddit"},
                                      "acct-li": {"platform": "linkedin"}})
        self.assertNotIn("p-reddit", {s["post_id"] for s in report["suggestions"]})
        self.assertIn({"post_id": "p-reddit", "platform": "reddit", "rule_id": None,
                       "reason": "human_only_platform"}, report["skipped"])
        for item in report["suggestions"]:
            if item["kind"] == "cross_post":
                self.assertNotIn("reddit", {t["platform"] for t in item["cross_post_targets"]})
                self.assertNotIn(item["platform"], {t["platform"] for t in item["cross_post_targets"]})
        with self.assertRaisesRegex(ValidationError, "human-only"):
            followups.validate_rules([rule(kind="cross_post", target_platforms=["reddit"])])
        self.assertTrue({"reddit", "hackernews"} <= followups.human_only_platforms())

    def test_cross_post_without_accounts_is_manual_note(self):
        report = self.build([rule(kind="cross_post")], accounts=None, accounts_error="Missing accounts")
        blue = next(s for s in report["suggestions"] if s["post_id"] == "p-blue")
        self.assertTrue(blue["manual_only"])
        self.assertEqual(blue["cross_post_targets"], [])

    def test_optout_handles_are_withheld(self):
        optout = [{"kind": "handle", "value": "alice", "platform": None}]
        report = self.build([rule()], optout=optout)
        blue = next(s for s in report["suggestions"] if s["post_id"] == "p-blue")
        self.assertEqual(blue["mentions_withheld_by_optout"], 1)
        self.assertNotIn("@alice", blue["suggestion"])
        self.assertIn("@bob.example", blue["suggestion"])

    def test_every_kind_produces_brief_and_normal_flow_commands(self):
        report = self.build([rule(id=k.replace("_", "-"), kind=k) for k in followups.KINDS],
                            accounts={"acct-li": {"platform": "linkedin"}}, account_id="acct-blue")
        kinds = {s["kind"]: s for s in report["suggestions"] if s["post_id"] == "p-blue"}
        self.assertEqual(set(kinds), set(followups.KINDS))
        self.assertTrue(kinds["pin_or_feature"]["manual_only"])
        self.assertTrue(any(c.startswith("RW idea add ") and "--tag followup" in c
                            for c in kinds["write_up"]["next_commands"]))
        self.assertIn("RW draft --account acct-li --file adapted-linkedin.txt",
                      " ".join(kinds["cross_post"]["next_commands"]))
        forbidden = ("approve", "publish", "schedule", "--live", "--reply-to")
        for item in report["suggestions"]:
            for command in item["next_commands"]:
                self.assertFalse(any(word in command.split() for word in forbidden), command)

    def test_no_send_path(self):
        self.assertIsNone(followups.SEND_PATH)
        source = (ROOT / "reachweaver" / "followups.py").read_text(encoding="utf-8")
        for name in ("publishers", "claim_sending", "transition(", "add_post", "sendgate", "urllib", "http.client"):
            self.assertNotIn(name, source)


class RulesValidationTests(unittest.TestCase):
    def test_bad_rules_raise_clear_errors(self):
        cases = [
            ([], "nonempty"),
            ([rule(metric="followers")], "metric must be one of"),
            ([rule(threshold=0)], "threshold"),
            ([rule(threshold=True)], "threshold"),
            ([rule(kind="auto_like")], "kind must be one of"),
            ([rule(within_hours=0)], "within_hours"),
            ([rule(), rule()], "duplicate id"),
            ([rule(extra=1)], "unknown keys"),
            ([rule(platforms=["myspace"])], "unknown platform"),
            ([rule(link="http://localhost/x")], "public https"),
            ([rule(target_platforms=["linkedin"])], "only to kind cross_post"),
            ({"schema_version": 2, "rules": [rule()]}, "schema_version"),
        ]
        for raw, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                followups.validate_rules(raw)

    def test_example_file_validates(self):
        rules = followups.load_rules(ROOT / "examples" / "followup-rules.example.json")
        self.assertEqual({r["kind"] for r in rules}, set(followups.KINDS))


class CliTests(FollowupBase):
    def test_json_shape_and_read_only(self):
        self.write_rules([rule()])
        before = self.db.read_bytes()
        code, out, _ = self.cli("followup-suggest", "--rules", str(self.rules_path), "--now", NOW_ISO, "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["schema_version"], followups.OUTPUT_SCHEMA)
        for key in ("generated_at", "label", "filters", "counts", "suggestions", "skipped", "hard_rules", "note"):
            self.assertIn(key, report)
        self.assertEqual(self.db.read_bytes(), before)

    def test_total_changes_unchanged(self):
        with connect_readonly(self.db) as con:
            before = con.total_changes
            followups.build_suggestions(con, followups.validate_rules([rule()]), now=NOW)
            self.assertEqual(con.total_changes, before)

    def test_missing_database_is_not_created(self):
        self.write_rules([rule()])
        missing = self.base / "nowhere" / "rw.sqlite"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(missing), "followup-suggest", "--rules", str(self.rules_path)])
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())

    def test_invalid_rules_exit_2_with_clean_message(self):
        self.write_rules([rule(metric="followers")])
        code, out, err = self.cli("followup-suggest", "--rules", str(self.rules_path))
        self.assertEqual(code, 2)
        self.assertIn("metric must be one of", err)
        self.assertNotIn("Traceback", err)
        self.rules_path.write_text("{not json", encoding="utf-8")
        code, _, err = self.cli("followup-suggest", "--rules", str(self.rules_path))
        self.assertEqual(code, 2)
        self.assertIn("not valid UTF-8 JSON", err)

    def test_record_requires_state(self):
        self.write_rules([rule()])
        code, _, err = self.cli("followup-suggest", "--rules", str(self.rules_path), "--record")
        self.assertEqual(code, 2)
        self.assertIn("--record requires --state", err)

    def test_dedupe_state(self):
        self.write_rules([rule()])
        state = self.base / "private" / "followups-state.json"
        args = ("followup-suggest", "--rules", str(self.rules_path), "--now", NOW_ISO, "--json", "--state", str(state))
        code, out, _ = self.cli(*args)  # read-only without --record: no file written
        self.assertEqual((code, len(json.loads(out)["suggestions"])), (0, 3))
        self.assertFalse(state.exists())
        code, out, _ = self.cli(*args, "--record")
        self.assertEqual((code, json.loads(out)["state_recorded"]), (0, 3))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
        stored = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(stored["kind"], followups.STATE_KIND)
        self.assertEqual(set(stored["suggested"][0]), {"post_id", "rule_id", "recorded_at"})  # ids only, no text
        code, out, _ = self.cli(*args, "--record")
        report = json.loads(out)
        self.assertEqual((report["suggestions"], report["counts"]["already_suggested"], report["state_recorded"]),
                         ([], 3, 0))

    def test_text_output_labels_and_hard_rules(self):
        self.write_rules([rule()])
        code, out, _ = self.cli("followup-suggest", "--rules", str(self.rules_path), "--now", NOW_ISO)
        self.assertEqual(code, 0)
        self.assertIn(followups.LABEL, out)
        self.assertIn("Never auto-like", out)
        self.assertIn("Next: RW show p-blue", out)


if __name__ == "__main__":
    unittest.main()
