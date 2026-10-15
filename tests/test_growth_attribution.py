"""experiment-readout and baseline-eval read attribution-report outcomes (private issue #16 R4).

`docs_visits` and `repo_stars_utm` are qualified primary signals that no native snapshot holds. Both
commands now take a saved `attribution-report --json` file and use each post's attributed sessions or
conversions. A post the report does not credit is unknown and excluded, never counted as 0. Offline.
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from reachweaver.attribution import (
    ATTRIBUTION_OUTCOMES, attributed_outcome, attribution_report, load_analytics_csv, load_attribution_outcomes,
)
from reachweaver.baseline_eval import evaluate_against_baseline, render_text
from reachweaver.cli import main
from reachweaver.growth_readout import growth_plan_to_experiment, run_readout
from reachweaver.policy import ValidationError
from reachweaver.storage import SCHEMA, add_post, connect, get_post, store_metrics
from test_growth_readout import CONTROL, TREATMENT, DbCase, growth_plan, post_map, record_for, seed_pairs
from test_no_network import NetworkGuard

HEADER = "date,landing_url,utm_source,utm_medium,utm_campaign,utm_content,sessions,conversions\n"
WINDOW = ("2026-09-01", "2026-10-31")


def analytics_csv(path: Path, rows: list[tuple[str, int | None, int | None]], *, platform: str = "bluesky",
                  campaign: str = "c") -> Path:
    """One row per (post id, sessions, conversions); None writes a blank (unknown) cell. Plus two
    window-defining rows that credit no post."""
    lines = [f"{WINDOW[0]},https://docs.example.org/,,,,,3,\n", f"{WINDOW[1]},https://docs.example.org/,,,,,4,\n"]
    for post_id, sessions, conversions in rows:
        cells = ["" if v is None else str(v) for v in (sessions, conversions)]
        lines.append(f"2026-09-15,https://docs.example.org/start,{platform},social,{campaign},{post_id},"
                     f"{cells[0]},{cells[1]}\n")
    path.write_text(HEADER + "".join(lines), encoding="utf-8")
    return path


def saved_report(con, tmp: Path, rows, **kwargs) -> Path:
    """Run the real attribution-report join and save its JSON, as `attribution-report --json > F` would."""
    report = attribution_report(con, load_analytics_csv(analytics_csv(tmp / "analytics.csv", rows, **kwargs)))
    out = tmp / "attribution.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def docs_plan(signal: str = "docs_visits") -> dict:
    return growth_plan(primary_metric={"signal": signal, "denominator": "none"},
                       guardrail_metrics=[{"signal": "saves", "denominator": "none"}])


class LoaderTests(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.addCleanup(self.con.close)

    def published(self, when: str, campaign: str = "c", account: str = "my-bluesky", platform: str = "bluesky"):
        post_id = add_post(self.con, account, platform, f"Genuine note {when}", campaign)
        self.con.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?", (when, post_id))
        return post_id

    def test_reads_the_real_report_and_keeps_unknown_as_none(self):
        a, b, c = (self.published(f"2026-09-0{d}T10:00:00+00:00") for d in (2, 3, 4))
        outcomes = load_attribution_outcomes(saved_report(self.con, self.tmp, [(a, 12, 2), (b, None, None)]))
        self.assertEqual(outcomes["analytics_date_range"], (date(2026, 9, 1), date(2026, 10, 31)))
        self.assertEqual(outcomes["posts"][a]["sessions"], 12)
        self.assertEqual(outcomes["posts"][a]["conversions"], 2)
        self.assertIsNone(outcomes["posts"][b]["sessions"])
        self.assertNotIn(c, outcomes["posts"])
        self.assertEqual(ATTRIBUTION_OUTCOMES, {"docs_visits": "sessions", "repo_stars_utm": "conversions"})
        row = lambda pid: get_post(self.con, pid)  # noqa: E731
        self.assertEqual(attributed_outcome(outcomes, row(a), "docs_visits", age_hours=168), (12, None))
        self.assertEqual(attributed_outcome(outcomes, row(a), "repo_stars_utm", age_hours=168), (2, None))
        self.assertEqual(attributed_outcome(outcomes, row(b), "docs_visits", age_hours=168),
                         (None, "sessions_unknown"))
        self.assertEqual(attributed_outcome(outcomes, row(c), "docs_visits", age_hours=168),
                         (None, "no_attributed_rows"))
        with self.assertRaisesRegex(ValidationError, "not read from attribution-report"):
            attributed_outcome(outcomes, row(a), "link_clicks", age_hours=168)

    def test_window_and_cohort_rules_exclude_rather_than_zero_fill(self):
        early = self.published("2026-08-31T10:00:00+00:00")
        late = self.published("2026-10-25T10:00:00+00:00")  # 25 Oct + 7 days is past 31 Oct
        edge = self.published("2026-10-24T10:00:00+00:00")  # exactly 7 days of window left
        other = self.published("2026-09-05T10:00:00+00:00")
        outcomes = load_attribution_outcomes(saved_report(self.con, self.tmp, [(p, 5, 1) for p in
                                                                               (early, late, edge, other)]))
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, early), "docs_visits", age_hours=168),
                         (None, "sent_before_analytics_window"))
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, late), "docs_visits", age_hours=168),
                         (None, "analytics_window_shorter_than_post_age"))
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, edge), "docs_visits", age_hours=168),
                         (5, None))
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, edge), "docs_visits", age_hours=169),
                         (None, "analytics_window_shorter_than_post_age"))
        outcomes["posts"][other]["account_id"] = "someone-else"
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, other), "docs_visits", age_hours=168),
                         (None, "attribution_cohort_mismatch"))
        outcomes["analytics_date_range"] = None
        self.assertEqual(attributed_outcome(outcomes, get_post(self.con, other), "docs_visits", age_hours=168),
                         (None, "no_analytics_window"))

    def test_malformed_files_are_refused_without_echoing_content(self):
        cases = {
            "not json {secret-marker": "not valid JSON",
            json.dumps({"kind": "something else"}): "attributed.posts",
            json.dumps({"attributed": {"posts": [{"post_id": "p", "platform": "bluesky", "account_id": "a",
                                                  "sessions": -1}]}}): "nonnegative integer",
            json.dumps({"attributed": {"posts": [{"post_id": "p", "platform": "bluesky", "account_id": "a",
                                                  "sessions": True}]}}): "nonnegative integer",
            json.dumps({"attributed": {"posts": [{"post_id": "p", "platform": "bluesky", "account_id": "a"}] * 2}}):
                "listed twice",
            json.dumps({"attributed": {"posts": [{"post_id": "p"}]}}): "platform and account_id",
            json.dumps({"attributed": {"posts": []}, "inputs": {"analytics_date_range": ["2026-10-01", "2026-09-01"]}}):
                "ends before it starts",
            json.dumps({"attributed": {"posts": []}, "inputs": {"analytics_date_range": ["secret-marker", "x"]}}):
                "YYYY-MM-DD",
        }
        for index, (text, message) in enumerate(cases.items()):
            path = self.tmp / f"bad{index}.json"
            path.write_text(text, encoding="utf-8")
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message) as raised:
                load_attribution_outcomes(path)
            self.assertNotIn("secret-marker", str(raised.exception))


class ReadoutAttributionTests(DbCase):
    def seed(self, sessions):
        """Six pairs (saves guardrail at 168 h) and their attributed sessions/conversions."""
        pairs = seed_pairs(self.con, [(1, 1)] * len(sessions))
        rows = []
        for pair, (c_value, t_value) in zip(pairs, sessions):
            for arm, value in ((CONTROL, c_value), (TREATMENT, t_value)):
                if value != "absent":
                    rows.append((pair[arm], value, None if value is None else value // 5))
        return pairs, load_attribution_outcomes(saved_report(self.con, self.dir, rows, campaign="hook-test"))

    def test_docs_visits_readout_uses_attributed_sessions_and_the_strict_gate(self):
        pairs, outcomes = self.seed([(10, 15), (12, 18), (9, 14), (11, 16), (10, 17), (13, 19)])
        with NetworkGuard() as guard:
            result = run_readout(self.con, record_for(docs_plan()), post_map(pairs), attribution=outcomes)
        self.assertEqual(guard.attempts, [])
        plan = result["experiment_plan"]
        self.assertEqual((plan["primary_metric"], plan["primary_source"], plan["primary_signal"]),
                         ("sessions", "attribution_report", "docs_visits"))
        self.assertEqual(plan["guardrail_metric"], "saves")
        evaluation = result["evaluation"]
        self.assertEqual(evaluation["analytics_date_range"], list(WINDOW))
        self.assertEqual([(p["control_rate"], p["treatment_rate"]) for p in evaluation["pair_results"]][:2],
                         [(10, 15), (12, 18)])
        self.assertEqual(evaluation["guardrail_relative_change"], 0.0)
        self.assertEqual(evaluation["relative_lift"], round((99 - 65) / 65, 4))
        self.assertEqual((result["label"], result["direction"]), ("directional", "positive"))
        self.assertEqual(result["design"]["interpretation"], "associational")
        self.assertTrue(any("never 0" in w for w in evaluation["warnings"]))

    def test_unknown_attribution_skips_the_pair_and_never_counts_zero(self):
        pairs, outcomes = self.seed([(10, 15), (12, "absent"), (None, 14), (11, 16), (10, 17), (13, 19)])
        result = run_readout(self.con, record_for(docs_plan()), post_map(pairs), attribution=outcomes)
        self.assertEqual(result["evaluation"]["pairs_skipped"],
                         [{"pair": "pair[1]", "reason": "no_attributed_rows:treatment"},
                          {"pair": "pair[2]", "reason": "sessions_unknown:control"}])
        self.assertEqual(result["pairs_usable"], 4)
        self.assertNotIn(0, [p["control_rate"] for p in result["evaluation"]["pair_results"]])
        self.assertEqual(result["label"], "insufficient_data")  # 4 usable pairs < preregistered 6

    def test_repo_stars_utm_reads_conversions(self):
        pairs, outcomes = self.seed([(10, 15)] * 6)
        result = run_readout(self.con, record_for(docs_plan("repo_stars_utm")), post_map(pairs), attribution=outcomes)
        self.assertEqual(result["experiment_plan"]["primary_metric"], "conversions")
        self.assertEqual({(p["control_rate"], p["treatment_rate"]) for p in result["evaluation"]["pair_results"]},
                         {(2, 3)})

    def test_flag_and_metric_must_match(self):
        pairs, outcomes = self.seed([(10, 15)] * 2)
        with self.assertRaisesRegex(ValidationError, "Pass a saved 'attribution-report --json' file"):
            growth_plan_to_experiment(record_for(docs_plan()), post_map(pairs), self.con)
        with self.assertRaisesRegex(ValidationError, "reads only docs_visits, repo_stars_utm"):
            growth_plan_to_experiment(record_for(growth_plan()), post_map(pairs), self.con, attribution=outcomes)
        profile = growth_plan(primary_metric={"signal": "profile_visits", "denominator": "none"},
                              guardrail_metrics=[{"signal": "saves", "denominator": "none"}])
        with self.assertRaisesRegex(ValidationError, "reads only"):
            growth_plan_to_experiment(record_for(profile), post_map(pairs), self.con, attribution=outcomes)

    def test_cli_experiment_readout_attribution_flag(self):
        pairs, _ = self.seed([(10, 15), (12, 18), (9, 14), (11, 16), (10, 17), (13, 19)])
        (self.dir / "prereg.json").write_text(json.dumps(record_for(docs_plan())), encoding="utf-8")
        (self.dir / "posts.json").write_text(json.dumps(post_map(pairs)), encoding="utf-8")
        base = ["--db", str(self.db_path), "experiment-readout", "--prereg", str(self.dir / "prereg.json"),
                "--posts", str(self.dir / "posts.json")]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), NetworkGuard() as guard:
            code = main([*base, "--attribution", str(self.dir / "attribution.json"), "--json"])
            refused = main(base)
        self.assertEqual(guard.attempts, [])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(refused, 2)
        self.assertIn("--attribution", err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["label"], "directional")


class BaselineAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db_path = str(self.dir / "state" / "outbox.sqlite")
        rows = []
        with connect(self.db_path) as con:
            def post(when, sessions):
                post_id = add_post(con, "my-bluesky", "bluesky", f"Genuine note {when.isoformat()}", "c")
                con.execute("UPDATE posts SET status='published',send_started_at=? WHERE id=?",
                            (when.isoformat(), post_id))
                store_metrics(con, post_id, (when + timedelta(hours=168)).isoformat(), "native-export",
                              clicks=1, impressions=1000)
                if sessions != "absent":
                    rows.append((post_id, sessions, None))
            for offset, value in enumerate((10, 11, 12, 10, 11, 12, 10, 11, "absent", "absent", None)):
                post(datetime(2026, 9, 1 + offset, 18, tzinfo=timezone.utc), value)  # generic 18:00 slot
            for day, value in zip((1, 8, 15, 22, 29), (20, 21, 22, 23, 24)):
                post(datetime(2026, 9, day, 9, tzinfo=timezone.utc), value)  # Tuesdays 09:00
            self.attribution = saved_report(con, self.dir, rows)

    def run_eval(self, **changes):
        params = dict(account_id="my-bluesky", platform="bluesky", audience_timezone="UTC", source=None,
                      metric=None, denominator=None, since="2026-09-01", until="2026-09-30",
                      slots=["exploit:Tue@09:00"], attribution=load_attribution_outcomes(self.attribution),
                      outcome="docs_visits")
        params.update(changes)
        with connect(self.db_path) as con:
            return evaluate_against_baseline(con, **params)

    def test_known_answer_on_attributed_sessions_with_unknowns_excluded(self):
        result = self.run_eval()
        self.assertEqual(result["measure"]["metric"], "docs_visits")
        self.assertEqual(result["measure"]["attribution_field"], "sessions")
        self.assertEqual(result["measure"]["analytics_date_range"], list(WINDOW))
        self.assertEqual(result["cohort"]["source"], "attribution-report")
        # Two posts without a UTM row and one with blank sessions are excluded; were they 0 the
        # baseline median would drop from 11 to 10.
        self.assertEqual(result["posts"]["excluded"], {"no_attributed_rows:baseline": 2,
                                                       "sessions_unknown:baseline": 1})
        self.assertEqual(result["baseline"]["median_rate"], 11)
        (item,) = result["correlational"]
        self.assertEqual((item["estimate"], item["relative_estimate"]), (11, 1.0))
        self.assertEqual((item["p_value"], item["p_value_method"]), (round(30 / 1287, 6), "exact_permutation"))
        self.assertEqual(item["status"], "separated_after_holm")
        self.assertTrue(any("never 0" in w for w in result["warnings"]))
        self.assertIn("docs_visits/none", render_text(result))

    def test_native_mode_is_unchanged_and_modes_do_not_mix(self):
        native = self.run_eval(source="native-export", metric="clicks", denominator="impressions",
                               attribution=None, outcome=None)
        self.assertEqual(native["measure"], {"metric": "clicks", "denominator": "impressions", "post_age_hours": 168,
                                             "age_tolerance_hours": 24,
                                             "statistic": "median rate (arm minus baseline)"})
        self.assertNotIn("never 0: an untagged link", " ".join(native["warnings"]))
        with self.assertRaisesRegex(ValidationError, "replaces --source"):
            self.run_eval(source="native-export")
        with self.assertRaisesRegex(ValidationError, "needs --outcome"):
            self.run_eval(outcome=None)
        with self.assertRaisesRegex(ValidationError, "needs --attribution"):
            self.run_eval(attribution=None)
        with self.assertRaisesRegex(ValidationError, "--source"):
            self.run_eval(attribution=None, outcome=None)

    def test_cli_baseline_eval_attribution_flags(self):
        accounts = self.dir / "accounts.json"
        accounts.write_text(json.dumps({"accounts": [{"id": "my-bluesky", "platform": "bluesky",
                                                      "env_prefix": "REACHWEAVER_T_BSKY",
                                                      "handle": "example.bsky.social"}]}), encoding="utf-8")
        base = ["--db", self.db_path, "--accounts", str(accounts), "baseline-eval", "--account", "my-bluesky",
                "--timezone", "UTC", "--since", "2026-09-01", "--until", "2026-09-30", "--slot", "exploit:Tue@09:00"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), NetworkGuard() as guard:
            code = main([*base, "--attribution", str(self.attribution), "--outcome", "docs_visits", "--json"])
            mixed = main([*base, "--attribution", str(self.attribution), "--outcome", "docs_visits",
                          "--source", "native-export"])
        self.assertEqual(guard.attempts, [])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(mixed, 2)
        self.assertEqual(json.loads(out.getvalue())["correlational"][0]["estimate"], 11)


if __name__ == "__main__":
    unittest.main()
