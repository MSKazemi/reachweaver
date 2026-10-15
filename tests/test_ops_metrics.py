"""Read-only operational metrics (issue #26): counters, gauges, formats, determinism, no writes.

Offline: a temporary database seeded through the storage API at fixed times. Nothing is sent.
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
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hold_support import claim as claim_sending
from reachweaver.cli import main
from reachweaver.ops_metrics import (
    collect, parse_since, render_json, render_prometheus, write_textfile,
)
from reachweaver.optout import validate_optout
from reachweaver.policy import ValidationError
from reachweaver.storage import (
    add_post, connect, finish_send, reconcile, record_provider_cooldown, set_paused,
    transition, uncertain_send,
)

T0 = "2026-10-08T10:00:00+00:00"
NOW = "2026-10-08T12:00:00+00:00"
BSKY = {"account_id": "rw-bsky", "platform": "bluesky"}
MASTO = {"account_id": "rw-masto", "platform": "mastodon"}
OPTOUT = validate_optout({"version": 1, "entries": [{"handle": "blocked-person"}]})

METRIC = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\\n]|\\\\|\\"|\\n)*)"')
SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? (-?[0-9]+)$")


def parse_exposition(text: str) -> dict:
    """Strict parser for the subset of text format 0.0.4 that render_prometheus may emit.

    Fails on: a missing final newline, blank lines, a sample before its HELP/TYPE, a family
    declared twice, a TYPE other than counter/gauge, bad names, unescaped label values,
    duplicate label sets, or unsorted families or samples.
    """
    assert text.endswith("\n") and "\n\n" not in text, "lines must be newline-terminated, none blank"
    families: dict[str, dict] = {}
    order: list[str] = []
    current = None
    for line in text[:-1].split("\n"):
        if line.startswith("# HELP "):
            name, _, help_text = line[len("# HELP "):].partition(" ")
            assert METRIC.fullmatch(name) and name not in families, line
            assert help_text and "\n" not in help_text, line
            families[name] = {"help": help_text, "type": None, "samples": []}
            order.append(name)
            current = name
        elif line.startswith("# TYPE "):
            name, _, kind = line[len("# TYPE "):].partition(" ")
            assert name == current and families[name]["type"] is None, line
            assert kind in ("counter", "gauge"), line
            families[name]["type"] = kind
        else:
            assert not line.startswith("#"), line
            match = SAMPLE.fullmatch(line)
            assert match, f"bad sample line: {line!r}"
            name, labels_text, value = match.groups()
            assert name == current and families[name]["type"], line
            labels = []
            if labels_text:
                consumed = ",".join(m.group(0) for m in LABEL.finditer(labels_text))
                assert consumed == labels_text, f"bad label set: {labels_text!r}"
                labels = [(m.group(1), m.group(2)) for m in LABEL.finditer(labels_text)]
                assert [k for k, _ in labels] == sorted({k for k, _ in labels}), line
            key = tuple(labels)
            assert key not in [s[0] for s in families[name]["samples"]], f"duplicate: {line}"
            families[name]["samples"].append((key, int(value)))
    assert order == sorted(order), "families must be sorted"
    for name, family in families.items():
        keys = [s[0] for s in family["samples"]]
        assert keys == sorted(keys), f"{name} samples must be sorted"
    return families


def sample(report: dict, name: str, **labels) -> int | None:
    family = next(f for f in report["metrics"] if f["name"] == "reachweaver_" + name)
    for item in family["samples"]:
        if item["labels"] == labels:
            return item["value"]
    return None


def samples(report: dict, name: str) -> list[dict]:
    return next(f for f in report["metrics"] if f["name"] == "reachweaver_" + name)["samples"]


class _Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        os.chmod(self.tmp, 0o700)
        self.db = str(self.tmp / "state" / "rw.sqlite")

    def cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, *map(str, argv)])
        return code, out.getvalue(), err.getvalue()


class SeededOpsMetricsTests(_Workspace):
    """One database exercising every counter and gauge."""

    def setUp(self):
        super().setUp()
        with mock.patch("reachweaver.storage.utc_now", return_value=T0), connect(self.db) as con:
            def approved(account, platform, text):
                post = add_post(con, account, platform, text, "rw26")
                transition(con, post, "approve", approver="test-owner")
                return post
            # rw-bsky: one published, one ambiguous, one certain_not_sent then reconciled, one unclassified.
            self.published = approved("rw-bsky", "bluesky", "Release one")
            self.assertTrue(claim_sending(con, self.published, now=T0))
            finish_send(con, self.published, "at://did:plc:x/app.bsky.feed.post/1", None)
            self.ambiguous = approved("rw-bsky", "bluesky", "Release two")
            self.assertTrue(claim_sending(con, self.ambiguous, now=T0))
            uncertain_send(con, self.ambiguous, "network_or_timeout_uncertain", send_class="ambiguous")
            self.reconciled = approved("rw-bsky", "bluesky", "Release three")
            self.assertTrue(claim_sending(con, self.reconciled, now=T0))
            uncertain_send(con, self.reconciled, "http_400", send_class="certain_not_sent")
            reconcile(con, self.reconciled, False)
            self.unclassified = approved("rw-bsky", "bluesky", "Release four")
            self.assertTrue(claim_sending(con, self.unclassified, now=T0))
            uncertain_send(con, self.unclassified, "legacy_error")
            # rw-masto: stale sending, a due scheduled post, a future one, a tampered approval, an opt-out mention.
            self.stale = approved("rw-masto", "mastodon", "Stuck post")
            self.assertTrue(claim_sending(con, self.stale, now=T0))
            self.due = approved("rw-masto", "mastodon", "Due post")
            transition(con, self.due, "schedule", "2026-10-08T11:00:00+00:00")
            self.future = approved("rw-masto", "mastodon", "Future post")
            transition(con, self.future, "schedule", "2026-10-08T13:00:00+00:00")
            self.tampered = approved("rw-masto", "mastodon", "Original text")
            con.execute("UPDATE posts SET content='Changed text' WHERE id=?", (self.tampered,))
            with self.assertRaises(ValidationError):
                claim_sending(con, self.tampered, now=T0)
            self.optout_post = approved("rw-masto", "mastodon", "Thanks @blocked-person for the report")
            add_post(con, "rw-masto", "mastodon", "Just a draft")
            # Cooldowns: one after a send 429 (bluesky), one post-less preflight 429 (mastodon).
            record_provider_cooldown(con, "rw-bsky", "2026-10-08T13:00:00+00:00", "retry_after", stage="send",
                                     post_id=self.ambiguous, send_class="ambiguous", now="2026-10-08T10:30:00+00:00")
            record_provider_cooldown(con, "rw-masto", "2026-10-08T12:30:00+00:00", "default", stage="preflight",
                                     now="2026-10-08T11:30:00+00:00")

    def report(self, **kw) -> dict:
        with connect(self.db) as con:
            return collect(con, now=NOW, **kw)

    def test_event_counters_all_time(self):
        r = self.report()
        bsky, masto = BSKY, MASTO
        self.assertEqual(sample(r, "send_claims_total", **bsky), 4)
        self.assertEqual(sample(r, "send_claims_total", **masto), 1)
        self.assertEqual(sample(r, "published_total", **bsky), 1)
        self.assertIsNone(sample(r, "published_total", **masto))
        self.assertEqual(sample(r, "send_failures_total", **bsky, send_class="ambiguous"), 1)
        self.assertEqual(sample(r, "send_failures_total", **bsky, send_class="certain_not_sent"), 1)
        self.assertEqual(sample(r, "send_failures_total", **bsky, send_class="unknown"), 1)
        self.assertEqual(sample(r, "reconciled_total", **bsky, outcome="not_published"), 1)
        self.assertEqual(sample(r, "approval_binding_failures_total", **masto,
                                reason="approval_hash_mismatch_reapproval_required"), 1)
        self.assertEqual(sample(r, "provider_cooldowns_recorded_total", **bsky, stage="send"), 1)
        # The post-less preflight event takes its account from the event and its platform from the posts.
        self.assertEqual(sample(r, "provider_cooldowns_recorded_total", **masto, stage="preflight"), 1)
        types = {f["name"]: f["type"] for f in r["metrics"]}
        self.assertEqual(types["reachweaver_send_claims_total"], "counter")
        self.assertIsNone(r["window"])

    def test_current_state_gauges(self):
        r = self.report(optout=OPTOUT, optout_configured=True)
        bsky, masto = BSKY, MASTO
        self.assertEqual(sample(r, "emergency_paused"), 0)
        self.assertEqual(sample(r, "posts", **bsky, status="published"), 1)
        self.assertEqual(sample(r, "posts", **bsky, status="needs_review"), 2)
        self.assertEqual(sample(r, "posts", **bsky, status="draft"), 1)  # the reconciled one
        self.assertEqual(sample(r, "posts", **masto, status="approved"), 2)
        self.assertEqual(sample(r, "posts", **masto, status="scheduled"), 2)
        self.assertEqual(sample(r, "posts", **masto, status="sending"), 1)
        self.assertEqual(sample(r, "needs_review_posts", **bsky, send_class="ambiguous"), 1)
        self.assertEqual(sample(r, "needs_review_posts", **bsky, send_class="unknown"), 1)
        self.assertEqual(sample(r, "dead_letter_posts"), 2)
        self.assertEqual(sample(r, "stale_sending_posts", **masto), 1)
        self.assertEqual(sample(r, "overdue_scheduled_posts", **masto), 1)
        self.assertEqual(sample(r, "approval_rebind_required_posts", **masto,
                                reason="approval_hash_mismatch_reapproval_required"), 1)
        self.assertEqual(sample(r, "provider_cooldown_active", **bsky), 1)
        self.assertEqual(sample(r, "provider_cooldown_remaining_seconds", **bsky), 3600)
        self.assertEqual(sample(r, "provider_cooldown_remaining_seconds", **masto), 1800)
        self.assertEqual(sample(r, "send_attempts_rolling_24h", **bsky), 4)
        # Due: the 11:00 scheduled post, the tampered and the opt-out approvals (approved at 10:00).
        self.assertEqual(sample(r, "due_unsent_posts", **masto), 3)
        self.assertEqual(sample(r, "oldest_due_unsent_age_seconds"), 7200)
        self.assertEqual(sample(r, "optout_list_entries"), 1)
        self.assertEqual(sample(r, "optout_blocked_posts", **masto), 1)
        self.assertEqual(r["optout_list"], {"configured": True, "entries": 1})

    def test_cooldowns_expire_and_pause_shows(self):
        with connect(self.db) as con:
            set_paused(con, True)
            r = collect(con, now="2026-10-08T14:00:00+00:00")
        self.assertEqual(sample(r, "emergency_paused"), 1)
        self.assertEqual(samples(r, "provider_cooldown_active"), [])
        self.assertEqual(samples(r, "optout_blocked_posts"), [])  # no list loaded: nothing counted

    def test_since_window_counts_only_recent_events_as_gauges(self):
        r = self.report(since="2026-10-08T11:00:00+00:00")
        self.assertEqual(r["window"], {"since": "2026-10-08T11:00:00+00:00", "until": NOW})
        names = {f["name"]: f["type"] for f in r["metrics"]}
        self.assertNotIn("reachweaver_send_claims_total", names)
        self.assertEqual(names["reachweaver_send_claims_in_window"], "gauge")
        self.assertEqual(samples(r, "send_claims_in_window"), [])
        self.assertEqual(sample(r, "provider_cooldowns_recorded_in_window", account_id="rw-masto",
                                platform="mastodon", stage="preflight"), 1)
        self.assertEqual(sample(r, "ops_metrics_window_start_timestamp_seconds"), 1791457200)
        # Relative windows resolve against the observation time.
        self.assertEqual(self.report(since="1h")["window"]["since"], "2026-10-08T11:00:00+00:00")

    def test_prometheus_output_is_valid_exposition_format(self):
        r = self.report(optout=OPTOUT)
        families = parse_exposition(render_prometheus(r))
        self.assertEqual(set(families), {f["name"] for f in r["metrics"]})
        for family in r["metrics"]:
            parsed = families[family["name"]]
            self.assertEqual(parsed["type"], family["type"])
            self.assertEqual(len(parsed["samples"]), len(family["samples"]))
        self.assertIn(((("account_id", "rw-bsky"), ("platform", "bluesky"), ("send_class", "ambiguous")), 1),
                      families["reachweaver_needs_review_posts"]["samples"])
        windowed = parse_exposition(render_prometheus(self.report(since="24h")))
        self.assertTrue(all(f["type"] == "gauge" for f in windowed.values()))

    def test_output_is_deterministic(self):
        first, second = self.report(), self.report()
        self.assertEqual(render_prometheus(first), render_prometheus(second))
        self.assertEqual(render_json(first), render_json(second))
        a = self.cli("ops-metrics", "--format", "prometheus", "--now", NOW)
        b = self.cli("ops-metrics", "--format", "prometheus", "--now", NOW)
        self.assertEqual(a, b)
        self.assertEqual(a[0], 0)
        self.assertEqual(a[1], render_prometheus(first))

    def test_cli_is_read_only(self):
        path = Path(self.db)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        for argv in (("ops-metrics",), ("ops-metrics", "--format", "prometheus", "--since", "7d"),
                     ("ops-metrics", "--format", "json", "--now", NOW, "--stale-minutes", "5")):
            with self.subTest(argv=argv):
                code, out, err = self.cli(*argv)
                self.assertEqual(code, 0, err)
                self.assertTrue(out)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_cli_json_matches_module_and_parses(self):
        code, out, _ = self.cli("ops-metrics", "--now", NOW)
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["report"], "reachweaver_ops_metrics")
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(out, render_json(self.report()))

    def test_cli_out_replaces_file_atomically_with_0644(self):
        target = self.tmp / "textfile" / "reachweaver.prom"
        target.parent.mkdir()
        target.write_text("old content\n", encoding="utf-8")
        code, out, err = self.cli("ops-metrics", "--format", "prometheus", "--now", NOW, "--out", target)
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertIn("database unchanged", err)
        self.assertEqual(target.read_text(encoding="utf-8"), render_prometheus(self.report()))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["reachweaver.prom"])

    def test_cli_rejects_bad_arguments(self):
        cases = (("--since", "tomorrow"), ("--since", "2099-01-01T00:00:00+00:00"), ("--since", "0h"),
                 ("--stale-minutes", "1"), ("--now", "2026-10-08T12:00:00"),
                 ("--out", self.tmp), ("--out", self.tmp / "missing" / "x.prom"))
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, err = self.cli("ops-metrics", "--now", NOW, *argv) if argv[0] != "--now" \
                    else self.cli("ops-metrics", *argv)
                self.assertEqual(code, 2)
                self.assertIn("ERROR", err)
                self.assertEqual(out, "")


class CredentialCauseTests(_Workspace):
    def test_permission_and_sunset_needs_review_are_counted_by_cause(self):
        with mock.patch("reachweaver.storage.utc_now", return_value=T0), connect(self.db) as con:
            for account, platform, error, text in (
                    ("rw-bsky", "bluesky", "http_401", "one"), ("rw-bsky", "bluesky", "http_403", "two"),
                    ("rw-li", "linkedin", "linkedin_http_426_uncertain", "three"),
                    ("rw-masto", "mastodon", "http_500", "four")):
                post = add_post(con, account, platform, text, "rw26")
                transition(con, post, "approve", approver="test-owner")
                self.assertTrue(claim_sending(con, post, now=T0))
                uncertain_send(con, post, error, send_class="ambiguous")
            report = collect(con, now=NOW)
        self.assertEqual(sample(report, "needs_review_credential_posts", account_id="rw-bsky",
                                platform="bluesky", cause="permission"), 2)
        self.assertEqual(sample(report, "needs_review_credential_posts", account_id="rw-li",
                                platform="linkedin", cause="version_sunset"), 1)
        self.assertEqual(len(samples(report, "needs_review_credential_posts")), 2)  # http_500 is not counted
        parse_exposition(render_prometheus(report))

    def test_family_is_declared_with_no_samples_on_an_empty_queue(self):
        with connect(self.db) as con:
            report = collect(con, now=NOW)
        self.assertEqual(samples(report, "needs_review_credential_posts"), [])


class EmptyAndEdgeTests(_Workspace):
    def test_empty_database_declares_every_family_with_zero_scalars(self):
        with connect(self.db) as con:
            r = collect(con, now=NOW)
        families = parse_exposition(render_prometheus(r))
        self.assertIn("reachweaver_send_claims_total", families)
        scalars = {name: f["samples"] for name, f in families.items() if f["samples"]}
        self.assertEqual(scalars, {
            "reachweaver_dead_letter_posts": [((), 0)],
            "reachweaver_emergency_paused": [((), 0)],
            "reachweaver_oldest_due_unsent_age_seconds": [((), 0)],
            "reachweaver_ops_metrics_generated_timestamp_seconds": [((), 1791460800)],
            "reachweaver_optout_list_entries": [((), 0)],
        })
        self.assertEqual(r["optout_list"], {"configured": False, "entries": 0})

    def test_labels_outside_the_allow_lists_are_replaced(self):
        with connect(self.db) as con:
            for post_id, account, platform in (("p1", "https://evil.example/?token=abc", "bluesky"),
                                               ("p2", "Alice Smith", "myspace"),
                                               ("p3", "ok-account", 'quote"\nnewline')):
                con.execute("""INSERT INTO posts(id,account_id,platform,content,status,created_at)
                               VALUES(?,?,?,?,'draft',?)""", (post_id, account, platform, "text", T0))
            r = collect(con, now=NOW)
        text = render_prometheus(r) + render_json(r)
        for needle in ("evil.example", "token", "Alice", "myspace", "quote", "newline"):
            self.assertNotIn(needle, text)
        self.assertEqual(sample(r, "posts", account_id="invalid", platform="bluesky", status="draft"), 1)
        self.assertEqual(sample(r, "posts", account_id="invalid", platform="other", status="draft"), 1)
        self.assertEqual(sample(r, "posts", account_id="ok-account", platform="other", status="draft"), 1)
        parse_exposition(render_prometheus(r))

    def test_label_and_help_escaping(self):
        report = {"metrics": [{"name": "reachweaver_x", "type": "gauge", "help": "back\\slash\nline",
                               "samples": [{"labels": {"b": 'q"\\\n', "a": "plain"}, "value": 3}]}]}
        text = render_prometheus(report)
        self.assertEqual(text, '# HELP reachweaver_x back\\\\slash\\nline\n# TYPE reachweaver_x gauge\n'
                               'reachweaver_x{a="plain",b="q\\"\\\\\\n"} 3\n')
        families = parse_exposition(text)
        self.assertEqual(families["reachweaver_x"]["samples"], [((("a", "plain"), ("b", 'q\\"\\\\\\n')), 3)])

    def test_parse_since(self):
        self.assertEqual(parse_since("30m", NOW), "2026-10-08T11:30:00+00:00")
        self.assertEqual(parse_since("2d", NOW), "2026-10-06T12:00:00+00:00")
        self.assertEqual(parse_since("2026-10-08T13:00:00+02:00", NOW), "2026-10-08T11:00:00+00:00")
        for bad in ("", "5s", "-1h", "1w", "2026-10-08", "2026-10-09T00:00:00+00:00", "4000d"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_since(bad, NOW)

    def test_since_overflow_is_a_validation_error(self):
        """Review F1-6: 999999d raised OverflowError (a traceback) instead of exit 2."""
        for bad in ("999999d", "999999h", "99999d", "3651d", "0001-01-01T00:30:00+01:00",
                    "0001-01-01T00:00:00+00:00"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_since(bad, NOW)
        with self.assertRaises(ValidationError):
            parse_since("7d", "0001-01-02T00:00:00+00:00")  # the window would start before year 1
        self.assertEqual(parse_since("3650d", NOW), "2016-10-10T12:00:00+00:00")
        with connect(self.db):
            pass
        for argv in (("--since", "999999d"), ("--now", "0001-01-01T00:00:00+01:00"),
                     ("--now", "0001-01-01T00:00:00+00:00"), ("--now", "9999-12-31T23:59:59+00:00"),
                     ("--now", "1969-12-31T23:59:59+00:00")):
            with self.subTest(argv=argv):
                code, out, err = self.cli("ops-metrics", *argv)
                self.assertEqual(code, 2, err)
                self.assertIn("ERROR", err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(out, "")

    def test_write_textfile_cleans_up_on_failure(self):
        target = self.tmp / "m.prom"
        with mock.patch("reachweaver.ops_metrics.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_textfile(target, "x 1\n")
        self.assertEqual(list(self.tmp.iterdir()), [])


class OutTargetTests(_Workspace):
    """Review F1-1: --out replaced ANY file (the SQLite DB included) and chmodded it 0644."""

    def setUp(self):
        super().setUp()
        with connect(self.db) as con:
            add_post(con, "rw-bsky", "bluesky", "A private unpublished draft")
        self.state = Path(self.db).parent

    def digest(self, path: Path) -> tuple[str, int]:
        return hashlib.sha256(path.read_bytes()).hexdigest(), stat.S_IMODE(path.stat().st_mode)

    def assert_refused(self, target, needle):
        db = Path(self.db)
        before = self.digest(db)
        listing = sorted(p.name for p in self.state.iterdir())
        code, out, err = self.cli("ops-metrics", "--format", "prometheus", "--now", NOW, "--out", target)
        self.assertEqual(code, 2, err)
        self.assertIn("ERROR", err)
        self.assertIn(needle, err)
        self.assertNotIn("database unchanged", err)
        self.assertEqual(out, "")
        self.assertEqual(self.digest(db), before)  # bytes and the 0600 mode
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), listing)  # no temp file left behind

    def test_database_itself_is_refused(self):
        for target in (self.db, str(self.state / ".." / "state" / "rw.sqlite"),
                       os.path.relpath(self.db)):
            with self.subTest(target=target):
                self.assert_refused(target, "state database")

    @unittest.skipUnless(os.name == "posix", "hard links and symlinks")
    def test_hard_link_and_symlink_to_the_database_are_refused(self):
        hard = self.state / "rw.prom"
        os.link(self.db, hard)
        self.assert_refused(hard, "state database")
        soft = self.tmp / "metrics.prom"
        os.symlink(self.db, soft)
        self.assert_refused(soft, "symbolic link")
        self.assertTrue(soft.is_symlink())

    @unittest.skipUnless(os.name == "posix", "symlinks")
    def test_symlink_to_a_prom_file_is_refused(self):
        real = self.tmp / "real.prom"
        real.write_text("old 1\n", encoding="utf-8")
        link = self.tmp / "link.prom"
        os.symlink(real, link)
        self.assert_refused(link, "symbolic link")
        self.assertEqual(real.read_text(encoding="utf-8"), "old 1\n")

    def test_existing_file_must_end_in_prom_or_json(self):
        for name in ("notes.txt", "reachweaver.sqlite", "prom", "metrics.prom.bak", "config"):
            with self.subTest(name=name):
                target = self.tmp / name
                target.write_text("keep me\n", encoding="utf-8")
                self.assert_refused(target, ".prom or .json")
                self.assertEqual(target.read_text(encoding="utf-8"), "keep me\n")

    def test_allowed_targets(self):
        for name, fmt in (("existing.prom", "prometheus"), ("existing.json", "json"), ("NEW.txt", "json"),
                          ("brand-new.prom", "prometheus")):
            with self.subTest(name=name):
                target = self.tmp / name
                if name.startswith("existing"):
                    target.write_text("old\n", encoding="utf-8")
                code, _, err = self.cli("ops-metrics", "--format", fmt, "--now", NOW, "--out", target)
                self.assertEqual(code, 0, err)
                self.assertIn("database unchanged", err)
                self.assertNotEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_write_textfile_guards_directly(self):
        db = Path(self.db)
        with self.assertRaisesRegex(ValidationError, "state database"):
            write_textfile(db, "x 1\n", database=db)
        other = self.tmp / "other.txt"
        other.write_text("keep\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, ".prom or .json"):
            write_textfile(other, "x 1\n")
        self.assertEqual(other.read_text(encoding="utf-8"), "keep\n")


class ReadOnlyDatabaseTests(_Workspace):
    """Review F1-5: ops-metrics opened the DB with connect(), creating and migrating it."""

    def test_missing_database_fails_and_creates_nothing(self):
        for argv in (("ops-metrics",), ("ops-metrics", "--format", "prometheus", "--out", self.tmp / "m.prom")):
            with self.subTest(argv=argv):
                code, out, err = self.cli(*argv)
                self.assertEqual(code, 2, err)
                self.assertIn("does not exist", err)
                self.assertEqual(out, "")
                self.assertEqual(list(self.tmp.iterdir()), [])  # no state/ directory, no DB, no --out file

    def test_old_schema_database_is_never_migrated(self):
        state = Path(self.db).parent
        state.mkdir(mode=0o700)
        con = sqlite3.connect(self.db)
        con.executescript("""
            CREATE TABLE posts(id TEXT PRIMARY KEY, account_id TEXT, platform TEXT, content TEXT,
              campaign TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
              status TEXT, created_at TEXT, approved_at TEXT, scheduled_at TEXT, remote_id TEXT,
              remote_url TEXT, last_error TEXT, send_started_at TEXT);
            CREATE TABLE event_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
              post_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO posts(id,account_id,platform,content,status,created_at) VALUES
              ('a1','rw-bsky','bluesky','Hello','approved','2026-10-01T00:00:00+00:00');""")
        con.commit()
        con.close()
        os.chmod(self.db, 0o600)
        before = hashlib.sha256(Path(self.db).read_bytes()).hexdigest()
        code, out, err = self.cli("ops-metrics", "--now", NOW)
        self.assertEqual(code, 2, err)
        self.assertIn("posts.approved_payload_sha256", err)
        self.assertIn("never migrates", err)
        self.assertEqual(out, "")
        self.assertEqual(hashlib.sha256(Path(self.db).read_bytes()).hexdigest(), before)
        columns = {row[1] for row in sqlite3.connect(self.db).execute("PRAGMA table_info(posts)")}
        self.assertNotIn("approved_payload_sha256", columns)

    def test_existing_current_database_is_opened_read_only(self):
        with connect(self.db):
            pass
        with mock.patch("reachweaver.cli.connect", side_effect=AssertionError("read-write open")):
            code, out, err = self.cli("ops-metrics", "--now", NOW)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["report"], "reachweaver_ops_metrics")


if __name__ == "__main__":
    unittest.main()
