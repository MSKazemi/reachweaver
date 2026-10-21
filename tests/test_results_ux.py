"""How did my posts do? report at a terminal, show's numbers, metrics/mark-shared defaults, insights' age note, start.

Synthetic data only; no network; nothing here can approve, schedule or send.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from reachweaver import ideas, insights, results, start
from reachweaver.cli import main
from reachweaver.storage import (connect, get_post, record_manual_publish, shared_entries, snapshots_for,
                                 store_metrics)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "state" / "rw.sqlite"
        self.config = self.root / "missing-accounts.json"
        env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")},
                         clear=True)
        env.start()
        self.addCleanup(env.stop)

    def by_hand(self, platform: str, text: str, *, hours: float, slug: str, account: str | None = None) -> str:
        with connect(str(self.db)) as con:
            return record_manual_publish(con, platform=platform, url=f"https://example.org/{platform}/{slug}",
                                         content=text, posted_at=ago(hours=hours), account_id=account)["post_id"]

    def numbers(self, post_id: str, *, hours_ago: float = 0, source: str = "entered-by-hand", **values) -> None:
        with connect(str(self.db)) as con:
            store_metrics(con, post_id, ago(hours=hours_ago), source, **values)

    def cli(self, *argv, tty: bool = False) -> tuple[int, str, str]:
        out, err = (TTY() if tty else io.StringIO()), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", str(self.db), "--accounts", str(self.config), *argv])
        return code, out.getvalue(), err.getvalue()

    def week(self) -> tuple[str, str, str]:
        glaze = self.by_hand("instagram", "My new ceramic glaze test came out of the kiln today", hours=60, slug="g")
        thread = self.by_hand("bluesky", "Thread on how I photograph small pieces", hours=30, slug="t")
        quiet = self.by_hand("linkedin", "Studio open day next week", hours=10, slug="o")
        self.numbers(glaze, likes=95, replies=7)
        self.numbers(thread, likes=25, replies=2)
        return glaze, thread, quiet


class ReportTests(Base):
    def test_a_terminal_gets_a_short_summary_and_a_pipe_keeps_the_json(self):
        glaze, thread, quiet = self.week()
        code, text, err = self.cli("report", "--week", tty=True)
        self.assertEqual(code, 0, err)
        first = text.splitlines()[0]
        self.assertTrue(first.startswith("This week (since "), first)
        self.assertIn("3 posts · 120 likes · 9 replies", first)
        self.assertIn('best: "My new ceramic glaze test came out of…" on Instagram (95 likes, 7 replies)', first)
        self.assertIn("Top posts by interactions", text)
        lines = text.splitlines()
        top = [line for line in lines if line.startswith("  1.") or line.startswith("  2.")]
        self.assertIn(glaze, top[0])
        self.assertIn(thread, top[1])
        self.assertIn("posted by you (no account)", top[0])
        self.assertIn(f"reachweaver metrics {quiet} --likes N", text)  # the post with no numbers yet
        self.assertIn("insights compares results about 7 days after posting", text)
        self.assertNotIn("{", text)
        code, piped, _ = self.cli("report", "--week")  # not a terminal: the JSON envelope, as before
        self.assertEqual(json.loads(piped)["platforms"]["instagram"]["likes"], 95)
        explicit = json.loads(self.cli("report", "--week", "--format", "json", tty=True)[1])
        self.assertEqual(explicit["platforms"], json.loads(piped)["platforms"])

    def test_week_is_since_7d_and_cannot_be_combined_with_since(self):
        self.week()
        _, week, _ = self.cli("report", "--week")
        _, since, _ = self.cli("report", "--since", "7d")
        strip = lambda doc: {k: v for k, v in json.loads(doc).items() if k not in ("generated_at", "window")}  # noqa: E731
        self.assertEqual(strip(week), strip(since))
        with self.assertRaises(SystemExit) as ctx:
            self.cli("report", "--week", "--since", "30d")
        self.assertEqual(ctx.exception.code, 2)

    def test_unknown_counts_print_as_a_dash_never_zero(self):
        self.week()
        _, text, _ = self.cli("report", "--format", "text")
        row = next(line for line in text.splitlines() if line.startswith("Instagram"))
        self.assertEqual(row.split()[:7], ["Instagram", "1", "1", "95", "7", "-", "-"])
        quiet = next(line for line in text.splitlines() if line.startswith("LinkedIn"))
        self.assertEqual(quiet.split()[:7], ["LinkedIn", "1", "0", "-", "-", "-", "-"])
        self.assertIn("Only the last 7 days: reachweaver report --week", text)

    def test_no_database_at_a_terminal(self):
        code, text, err = self.cli("report", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("no published posts recorded", text)
        self.assertFalse(self.db.exists())

    def test_stored_text_cannot_steer_the_terminal(self):
        post = self.by_hand("bluesky", "evil \x1b[31mred\x1b[0m text", hours=5, slug="e")
        self.numbers(post, likes=1)
        _, text, _ = self.cli("report", "--format", "text")
        self.assertNotIn("\x1b", text)


class ShowTests(Base):
    def test_show_lists_the_latest_numbers_with_when_and_source(self):
        post = self.by_hand("instagram", "Glaze test", hours=50, slug="s")
        self.numbers(post, hours_ago=26, likes=40)
        self.numbers(post, hours_ago=2, likes=95, replies=7)
        self.numbers(post, hours_ago=1, source="native-export", views=1200, likes="suppressed")
        code, out, err = self.cli("show", post)
        self.assertEqual(code, 0, err)
        shown = json.loads(out)["results"]["latest_by_source"]
        self.assertEqual([s["source"] for s in shown], ["native-export", "entered-by-hand"])
        self.assertEqual(shown[1]["numbers"], {"likes": 95, "replies": 7})
        self.assertEqual(shown[1]["hours_after_posting"], 48)
        self.assertEqual(shown[0]["other_states"], {"likes": "suppressed"})
        _, sheet, _ = self.cli("show", post, tty=True)
        self.assertIn("RESULTS", sheet)
        self.assertIn("95 likes, 7 replies", sheet)
        self.assertIn("source 'entered-by-hand'", sheet)
        self.assertIn("posted by you (no account) on instagram", sheet)
        self.assertIn("Posted by hand", sheet)

    def test_a_post_without_numbers_says_how_to_add_them(self):
        post = self.by_hand("bluesky", "Thread", hours=5, slug="n")
        _, sheet, _ = self.cli("show", post, tty=True)
        self.assertIn("No numbers recorded yet", sheet)
        self.assertIn(f"reachweaver metrics {post} --likes N --replies N --reposts N", sheet)


class MetricsTests(Base):
    def test_defaults_to_now_and_an_honest_hand_entered_source(self):
        post = self.by_hand("bluesky", "Thread", hours=20, slug="m")
        code, out, err = self.cli("metrics", post, "--likes", "25", "--replies", "2")
        self.assertEqual(code, 0, err)
        with connect(str(self.db)) as con:
            snap = snapshots_for(con, post)[0]
        self.assertEqual(snap["source"], "entered-by-hand")
        observed = datetime.fromisoformat(snap["observed_at"])
        self.assertEqual(snap["observed_at"], observed.astimezone(timezone.utc).isoformat(timespec="seconds"))
        self.assertLess(abs((observed - datetime.now(timezone.utc)).total_seconds()), 120)
        self.assertIn("20 hours after posting", out)
        self.assertIn("record again around", out)

    def test_human_times_and_explicit_values_still_work(self):
        post = self.by_hand("bluesky", "Thread", hours=60, slug="m")
        self.assertEqual(self.cli("metrics", post, "--likes", "3", "--observed-at", "yesterday")[0], 0)
        self.assertEqual(self.cli("metrics", post, "--likes", "4", "--observed-at", "2026-10-20T09:00:00+02:00",
                                  "--source", "native-export")[0], 0)
        with connect(str(self.db)) as con:
            rows = {(r["source"], r["observed_at"]) for r in snapshots_for(con, post)}
        self.assertIn(("native-export", "2026-10-20T07:00:00+00:00"), rows)
        code, _, err = self.cli("metrics", post, "--likes", "3", "--observed-at", "last tuesday")
        self.assertEqual(code, 2)
        self.assertIn("now, today, yesterday", err)


class MarkSharedTests(Base):
    def test_with_url_one_measurable_id_and_human_time(self):
        code, out, err = self.cli("mark-shared", "--platform", "bluesky", "--text", "Thread", "--url",
                                  "https://example.org/bsky/1", "--at", "yesterday")
        self.assertEqual(code, 0, err)
        self.assertNotIn("shared-", out)
        post_id = out.split("Measurable as post ", 1)[1].split(" ", 1)[0]
        self.assertEqual(out.count(post_id), 2)  # the id, then the exact metrics command
        self.assertIn(f"reachweaver metrics {post_id} --likes N", out)
        with connect(str(self.db)) as con:
            sent = get_post(con, post_id)["send_started_at"]
        self.assertTrue(sent.endswith("+00:00"))
        hours = (datetime.now(timezone.utc) - datetime.fromisoformat(sent)).total_seconds() / 3600
        self.assertLess(abs(hours - 24), 1.5)  # a daylight-saving night moves it by at most one hour

    def test_without_url_says_it_is_not_counted(self):
        code, out, _ = self.cli("mark-shared", "--platform", "linkedin", "--text", "Open day", "--at", "today")
        self.assertEqual(code, 0)
        self.assertIn("Not counted in reports. Add --url <link to the post> to measure it.", out)
        with connect(str(self.db)) as con:
            self.assertTrue(shared_entries(con, "linkedin")[0]["posted_at"].endswith("+00:00"))


class InsightsTests(Base):
    def test_early_results_are_explained_and_the_account_is_named(self):
        post = self.by_hand("bluesky", "Thread", hours=30, slug="i", account=None)
        self.numbers(post, hours_ago=10, likes=5, replies=1, reposts=0)
        with connect(str(self.db)) as con:
            report = insights.analyze(con, now=NOW, resamples=0)
        cohort = report["cohorts"][0]
        note = cohort["results_at_other_ages"]
        self.assertEqual(note["hours_after_posting"], [20])
        self.assertEqual(note["suggested_age_hours"], 25)
        self.assertEqual(note["record_again_around"],
                         (NOW - timedelta(hours=30) + timedelta(hours=168)).date().isoformat())
        text = insights.render_text(report)
        self.assertIn("posted by you (no account) (bluesky)", text)
        self.assertNotIn("account unknown", text)
        self.assertIn("You recorded results 20 hours after posting; insights compares results about 7 days after "
                      "posting. Record again around", text)
        self.assertIn("or use --age-hours 25.", text)

    def test_results_at_the_matched_age_add_no_note(self):
        post = self.by_hand("bluesky", "Thread", hours=200, slug="i")
        self.numbers(post, hours_ago=30, likes=5, replies=1, reposts=0)  # 170 h after posting
        with connect(str(self.db)) as con:
            cohort = insights.analyze(con, now=NOW, resamples=0)["cohorts"][0]
        self.assertEqual(cohort["posts_with_result"], 1)
        self.assertNotIn("results_at_other_ages", cohort)

    def test_note_helpers(self):
        sent = NOW - timedelta(hours=300)
        late = results.early_note([(sent, [250.0])], NOW)
        self.assertIsNone(late["record_again_around"])
        self.assertIn("To compare the results you have, use --age-hours 250.", results.early_note_text(late))
        self.assertIsNone(results.early_note([(sent, [])], NOW))
        self.assertIsNone(results.early_note([(sent, [150.0, 10.0])], NOW))
        fresh = NOW - timedelta(hours=30)  # recorded at 20 h: can still get its 7-day numbers
        mixed = results.early_note([(sent, [250.0]), (fresh, [20.0])], NOW)
        self.assertEqual((mixed["posts"], mixed["hours_after_posting"], mixed["suggested_age_hours"]), (1, [20], 25))
        self.assertIsNotNone(mixed["record_again_around"])


class StartTests(Base):
    def test_hand_posting_without_an_accounts_file_shows_progress(self):
        glaze = self.by_hand("instagram", "Glaze", hours=24 * 7, slug="g")
        ideas.add(ideas.inbox_path(self.db), "Behind the scenes")
        guide = start.build_guide(str(self.db), str(self.config))
        self.assertEqual(guide["state"], "posting_by_hand")
        self.assertIn("1 post(s) recorded, 1 open idea(s)", guide["summary"])
        commands = [s["command"] for s in guide["steps"] if s["command"]]
        self.assertEqual(commands[0], start.NEXT)
        self.assertIn(f"reachweaver metrics {glaze} --likes N --replies N --reposts N", commands)
        self.assertIn(start.REPORT_WEEK, commands)
        self.assertIn(start.SHARE_WEB, commands)

    def test_a_brand_new_user_still_starts_with_the_browser(self):
        guide = start.build_guide(str(self.db), str(self.config))
        self.assertEqual(guide["state"], "no_accounts_file")
        self.assertEqual(guide["steps"][0]["command"], start.STUDIO)


if __name__ == "__main__":
    unittest.main()
