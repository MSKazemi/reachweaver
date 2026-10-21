"""Retry metrics in the worker log (issue #26, security decision item 3, owner-approved 2026-10-21).

D1 record each read-only identity-preflight retry, D2 in the worker log only (no database write),
D3 a failing observer is swallowed. Every HTTP exchange is scripted at ``publishers._send_http``;
nothing reaches a host, no test sleeps and nothing is published for real.
"""
from __future__ import annotations

import io
import json
import random
import re
import unittest
from unittest.mock import patch

from test_rate_limits import MARKER, MASTO_ME, PZ_INTEGRATIONS, RateLimitBase, Wire, http

from reachweaver.publishers import PublishError
from reachweaver.ratelimit import preflight_with_backoff
from reachweaver.storage import connect
from reachweaver.trace import build_trace, render_text
from reachweaver.worker_log import COUNT_KEYS, EVENTS, WorkerLog

STATUS = {"id": "9", "url": "https://mastodon.example.org/@owner/9", "account": {"username": "owner"},
          "visibility": "public"}
CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}(?::[a-z][a-z0-9_]{0,79})?$")


def failing_twice(codes=("http_503", "http_502")):
    answers = iter([PublishError(code, status=int(code[-3:])) for code in codes] + ["verified"])

    def check():
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value
    return check


class ObserverTests(unittest.TestCase):
    def test_called_once_per_retry_before_the_sleep_with_the_sanitized_code(self):
        seen, slept, order = [], [], []

        def observe(retry, delay, code):
            seen.append((retry, delay, code))
            order.append("observe")

        def sleep(seconds):
            slept.append(seconds)
            order.append("sleep")
        result = preflight_with_backoff(failing_twice(), max_retries=2, paused=lambda: False, retry_on=PublishError,
                                        rng=random.Random(5), sleep=sleep, on_retry=observe)
        self.assertEqual(result, "verified")
        self.assertEqual(seen, [(1, slept[0], "http_503"), (2, slept[1], "http_502")])
        self.assertEqual(order, ["observe", "sleep", "observe", "sleep"])

    def test_free_text_errors_reach_the_observer_as_a_kind_never_as_text(self):
        seen = []
        secret = f"upstream said token={MARKER} mail owner@example.com"
        answers = iter([PublishError(secret, status=503), "ok"])

        def check():
            value = next(answers)
            if isinstance(value, Exception):
                raise value
            return value
        preflight_with_backoff(check, max_retries=2, paused=lambda: False, retry_on=PublishError,
                               sleep=lambda _s: None, on_retry=lambda *a: seen.append(a))
        self.assertEqual([code for _, _, code in seen], ["publish_error"])
        self.assertNotIn(MARKER, repr(seen))

    def test_a_raising_observer_never_changes_the_outcome(self):
        def boom(*_args):
            raise RuntimeError("disk full")
        for observer in (None, boom, lambda *_a: None):
            with self.subTest(observer=observer):
                slept = []
                self.assertEqual(preflight_with_backoff(failing_twice(), max_retries=2, paused=lambda: False,
                                                        retry_on=PublishError, rng=random.Random(9),
                                                        sleep=slept.append, on_retry=observer), "verified")
                expected = random.Random(9)
                self.assertEqual(slept, [expected.uniform(0, 2.0), expected.uniform(0, 4.0)])
                calls = []

                def always():
                    calls.append(1)
                    raise PublishError("http_503", status=503)
                with self.assertRaisesRegex(PublishError, "http_503"):
                    preflight_with_backoff(always, max_retries=2, paused=lambda: False, retry_on=PublishError,
                                           sleep=lambda _s: None, on_retry=observer)
                self.assertEqual(len(calls), 3)

    def test_no_retry_means_no_call_and_pause_still_wins(self):
        seen = []
        self.assertEqual(preflight_with_backoff(lambda: "ok", max_retries=2, paused=lambda: False,
                                                retry_on=PublishError, on_retry=lambda *a: seen.append(a)), "ok")
        with self.assertRaises(PublishError):  # a permanent error is not retried
            preflight_with_backoff(lambda: (_ for _ in ()).throw(PublishError("http_401", status=401)),
                                   max_retries=2, paused=lambda: False, retry_on=PublishError,
                                   on_retry=lambda *a: seen.append(a))
        self.assertEqual(seen, [])


class WorkerLogLineTests(unittest.TestCase):
    POST = {"id": "p1", "account_id": "rl-mastodon", "platform": "mastodon"}

    def test_line_shape_and_run_end_count(self):
        self.assertIn("preflight_retry", EVENTS)
        self.assertIn("preflight_retries", COUNT_KEYS)
        stream = io.StringIO()
        log = WorkerLog(stream, dry_run=False)
        log.start(live_requested=True, scheduled_only=False, limit=1, post_id=None)
        hook = log.retry_hook(self.POST)
        hook(1, 1.2345, "http_503")
        hook(2, 3.0, "http_502")
        log.end(exit_code=0)
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        retries = [r for r in records if r["event"] == "preflight_retry"]
        self.assertEqual([(r["attempt"], r["delay_ms"], r["code"]) for r in retries],
                         [(1, 1234, "http_503"), (2, 3000, "http_502")])
        self.assertEqual(set(retries[0]), {"schema_version", "run_id", "seq", "ts", "event", "dry_run", "post_id",
                                           "account_id", "platform", "attempt", "delay_ms", "code"})
        self.assertEqual(records[-1]["counts"]["preflight_retries"], 2)

    def test_hostile_values_are_replaced_never_echoed_and_never_raise(self):
        stream = io.StringIO()
        log = WorkerLog(stream, dry_run=False)
        log.start(live_requested=True, scheduled_only=False, limit=1, post_id=None)
        for retry, delay, code in ((True, float("nan"), f"Free text {MARKER}"), ("1", -5, None),
                                   (99, float("inf"), "http_503 extra"), (1, 10 ** 9, object())):
            log.preflight_retry({"id": None, "account_id": 7}, retry, delay, code)
        log.preflight_retry(None, 1, 1.0, "http_503")
        records = [json.loads(line) for line in stream.getvalue().splitlines()][1:]
        self.assertNotIn(MARKER, stream.getvalue())
        self.assertEqual([(r["attempt"], r["delay_ms"], r["code"]) for r in records],
                         [(None, None, "unclassified"), (None, None, "unclassified"), (None, None, "unclassified"),
                          (1, 3_600_000, "unclassified"), (1, 1000, "http_503")])  # 10**9 s is clamped
        self.assertEqual([(r["post_id"], r["account_id"], r["platform"]) for r in records[:4]],
                         [("invalid", "invalid", "other")] * 4)
        self.assertEqual(log.counts["preflight_retries"], 5)

    def test_disabled_log_passes_no_observer(self):
        log = WorkerLog(None, dry_run=False)
        self.assertIsNone(log.retry_hook(self.POST))


class CliRetryMetricsTests(RateLimitBase):
    def records(self, text):
        return [json.loads(line) for line in text.splitlines() if line.startswith("{")]

    def mastodon_wire(self):
        return Wire({("GET", "verify_credentials"): [lambda: http(503), lambda: http(502), MASTO_ME],
                     ("POST", "/api/v1/statuses"): [STATUS]})

    def test_native_preflight_retries_are_logged_and_counted(self):
        pid = self.approved("rl-mastodon", text="Retry the read, never the write")
        wire = self.mastodon_wire()
        code, out, err = self.live(wire, "--post-id", pid, "--log-json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((wire.count("GET", "verify_credentials"), wire.count("POST", "/api/v1/statuses")), (3, 1))
        records = self.records(err)
        retries = [r for r in records if r["event"] == "preflight_retry"]
        self.assertEqual([(r["post_id"], r["account_id"], r["platform"], r["attempt"], r["code"]) for r in retries],
                         [(pid, "rl-mastodon", "mastodon", 1, "http_503"), (pid, "rl-mastodon", "mastodon", 2, "http_502")])
        self.assertEqual([r["delay_ms"] for r in retries], [round(s * 1000) for s in self.sleeps])
        events = [r["event"] for r in records]
        self.assertLess(events.index("preflight_retry"), events.index("post_claimed"))  # all before the claim
        self.assertEqual(records[-1]["counts"]["preflight_retries"], 2)
        self.assertNotIn(MARKER, out + err)
        # D2: nothing about retries is written to the database.
        with connect(self.db) as db:
            actions = {row[0] for row in db.execute("SELECT DISTINCT action FROM event_log")}
        self.assertFalse(any("retry" in action for action in actions), actions)

    def test_the_same_run_without_the_log_behaves_identically(self):
        results = []
        for name, extra in (("plain", ()), ("logged", ("--log-json",))):
            self.db = str(self.base / name / "rl.sqlite")  # one fresh database per run
            pid = self.approved("rl-mastodon", text="The same note in both databases")
            self.sleeps.clear()
            wire = self.mastodon_wire()
            code, out, err = self.live(wire, "--post-id", pid, *extra)
            with connect(self.db) as db:
                actions = [row[0] for row in db.execute("SELECT action FROM event_log WHERE post_id=? ORDER BY id",
                                                        (pid,))]
            results.append((code, out.replace(pid, "<id>"), wire.calls, len(self.sleeps), actions,
                            self.row(pid)["status"]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][-1], "published")

    def test_a_failing_log_write_never_changes_the_send(self):
        pid = self.approved("rl-mastodon", text="Logging may fail, the send may not")
        wire = self.mastodon_wire()
        with patch("reachweaver.worker_log.WorkerLog.preflight_retry", side_effect=RuntimeError("disk full")):
            code, out, err = self.live(wire, "--post-id", pid, "--log-json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual((wire.count("GET", "verify_credentials"), wire.count("POST", "/api/v1/statuses")), (3, 1))
        self.assertEqual(len(self.sleeps), 2)
        self.assertEqual(self.row(pid)["status"], "published")

    def test_postiz_backend_preflight_retry_is_logged(self):
        pid = self.approved("rl-postiz", text="Backend note")
        wire = Wire({("GET", "/integrations"): [lambda: http(503), PZ_INTEGRATIONS],
                     ("POST", "/public/v1/posts"): [[{"postId": "pz-1", "integration": "int-masto-1"}]]})
        code, out, err = self.live(wire, "--post-id", pid, "--log-json")
        self.assertEqual(code, 3, out + err)  # a Postiz acceptance is needs_review, as without the log
        self.assertEqual(wire.count("GET", "/integrations"), 2)
        retries = [r for r in self.records(err) if r["event"] == "preflight_retry"]
        self.assertEqual(len(retries), 1, out + err)
        self.assertEqual((retries[0]["post_id"], retries[0]["account_id"], retries[0]["attempt"]),
                         (pid, "rl-postiz", 1))
        self.assertRegex(retries[0]["code"], CODE)
        self.assertNotIn(MARKER, err)
        self.assertEqual(self.records(err)[-1]["counts"]["preflight_retries"], 1)

    def test_trace_shows_the_retries(self):
        pid = self.approved("rl-mastodon", text="Trace me")
        log_file = self.base / "worker.jsonl"
        code, out, err = self.live(self.mastodon_wire(), "--post-id", pid, "--log-json", "--log-file", str(log_file))
        self.assertEqual(code, 0, out + err)
        with connect(self.db) as db:
            trace = build_trace(db, pid, [log_file])
        retries = [item for item in trace["timeline"] if item["event"] == "preflight_retry"]
        self.assertEqual([(item["attempt"], item["code"]) for item in retries], [(1, "http_503"), (2, "http_502")])
        self.assertEqual(trace["runs"][0]["this_post"][:2], ["preflight_retry", "preflight_retry"])
        text = render_text(trace)
        self.assertRegex(text, r"preflight_retry \[.*\]  code=http_503, attempt=1, delay_ms=\d+")


if __name__ == "__main__":
    unittest.main()
