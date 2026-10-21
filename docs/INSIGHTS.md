# What works for you (`insights`)

`reachweaver insights` looks at **your own published posts** (including posts you published by hand and recorded with `mark-shared --url`; `posted_by_hand` counts them per cohort) and the results you recorded for them, and tells you which simple features went together with better or worse results. It is read-only and offline: it opens the database with `mode=ro`, never creates or migrates it, makes no network request and cannot draft, approve, schedule or publish anything.

Every line it prints is an **observation about your past posts and a hypothesis to test**, never a cause and never a promise of future reach.

```bash
reachweaver insights                                  # every account, all time
reachweaver insights --account my-bluesky --since 30d --timezone Europe/Rome
reachweaver insights --platform mastodon --json       # stable machine-readable report
```

Example (synthetic data):

```text
What works for you (your own published posts; observations, not causes)
Result: interactions (likes + replies + reposts) about 168 h after posting (+/- 24 h); times in Europe/Rome.

my-bluesky (bluesky): 30 of 30 published posts have a result from source 'bluesky-public-api'; median 21.
  1. Try more of: posts with a link -- median 29 (n=12) vs 17.5 for posts without a link (n=18) (+66%). A hypothesis to test, not a cause.
     Not a separate finding (their posts are mostly in this group): posts tagged #docs (6 of 6), posts with 3+ hashtags (6 of 6), posts tagged #ci (6 of 6), posts tagged #devops (6 of 6)
  2. Try more of: posts opening with a statement -- median 25 (n=18) vs 17.5 for posts with other openings (n=12) (+43%). A hypothesis to test, not a cause.
     12 of these 18 posts are also in line 1.
  3. Try less of: posts sent 06:00-12:00 (Europe/Rome) -- median 16 (n=10) vs 21.5 for posts sent at other times (n=20) (-26%). A hypothesis to test, not a cause.
```

## Where the data comes from

Only results you recorded yourself:

- `metrics-fetch --online` (the owner runs it): public Bluesky and Mastodon counters (likes, replies, reposts; no views).
- `import-metrics --csv FILE`: a native analytics export.
- `metrics POST_ID --likes N --replies N --reposts N`: one snapshot by hand, from the app's own insights (recorded now with source `entered-by-hand` unless you give `--observed-at` and `--source`).

When a post has results but none at the matched age (for example you recorded them the day after posting), the text says so plainly: "You recorded results 20 hours after posting; insights compares results about 7 days after posting. Record again around 2026-10-27 or use --age-hours 25." The date is the post's 7-day mark (in `--timezone`); when it has already passed only the `--age-hours` hint is given. The JSON carries the same facts as `cohorts[].results_at_other_ages`. A cohort without an account (a post recorded by hand without `--account`) is shown as "posted by you (no account)".

With too little data the command exits 0 and says how to get more. When the database does not exist yet it says so and does not create it.

## Method

| Step | Rule |
|---|---|
| Cohort | One account x one platform x one snapshot `source` label. Accounts are never pooled. Sources are never mixed, because each counts differently. By default the source with the most matched posts is used (ties: alphabetical); others are listed under `other_sources`. `--source` picks one. |
| Posts | Published, top-level posts (replies are left out) with a valid send time, optionally since `--since` (`30d`, `YYYY-MM-DD` = 00:00 UTC, or an ISO time). |
| Result per post | The value in the **one** snapshot nearest to `--age-hours` (default 168 h) after the send time, within `--tolerance-hours` (default 24 h). This is the same rule as `baseline-eval` and `timing-evaluate` (shared helper `baseline_eval.matched_snapshot`): exact counts only, windowed snapshots skipped, snapshots never summed. A post without one is **excluded and counted**, never treated as 0. |
| Metric | Default `interactions` = likes + replies + reposts, all three exact in the same snapshot. These three are the counters every recording path has, including the public counters of `metrics-fetch`, which expose no views or impressions. `--metric` picks any stored count; `--denominator views|impressions` turns it into a rate (as in `timing-evaluate`). |
| Features | Time of day (6-hour blocks in `--timezone`, default UTC), weekday, weekday vs weekend, text length (short <= 100 characters, medium 101-280, long > 280), link (an `http(s)://` link in the text), format (text, image or video from the stored attachment columns, as `timing-evaluate --stratify format`), hashtag count (0, 1-2, 3+), opening hook, and each hashtag (the 20 most used). |
| Opening hook | The first sentence (up to the first `.`, `!`, `?` or line break) ends with `?` = **question**; otherwise the text starts with a digit = **number**; otherwise **statement**. Deliberately simple and deterministic. |
| Comparison | For each feature value: median of that group vs median of **all other posts in the cohort**, with both counts, the difference and the relative difference. |
| Strength | `too little data` when the group or the rest has fewer than `--min-posts` (default 5) posts; otherwise `observation`. There is no stronger label: this command never says "cause" or "proven". |
| Interval | Optional approximate 95% percentile bootstrap interval of the median difference (`--resamples`, default 1000; `0` turns it off), with a fixed `--seed`, so the same data gives the same output. Reuses `baseline_eval.bootstrap_independent`. |
| Top lines | Up to 3 lines per account. Groups whose interval excludes zero come first, then the largest relative difference. One line per feature. A group whose posts are mostly (80%+) inside an earlier line's group is folded into that line (`overlaps_with`) instead of being reported as a second finding; a line that shares half or more of its posts with an earlier one says so (`shares_posts_with_lines`). |

A cohort needs at least `2 x --min-posts` posts with a result (10 by default) before any line is printed.

## Limits

- **Correlation, not causation.** You chose what to post and when; nothing was randomly assigned. A difference can come from topic, audience growth, promotion, news, platform changes or chance.
- **Confounding between features.** Long posts may also be the ones with links; morning posts may all be one kind of announcement. Each comparison looks at one feature at a time and does not adjust for the others. The overlap notes catch only the most obvious cases.
- **Small samples and many comparisons.** Medians of a few posts move in coarse steps, and with dozens of groups some will differ by chance alone. The bootstrap interval is approximate, tends to be too narrow in small samples and is **not** corrected for multiple comparisons (for a corrected comparison of planned arms use `baseline-eval`, which applies Holm-Bonferroni).
- **Only recorded results count.** Missing is unknown, never zero; the excluded counts say how many posts had no result at the matched age.
- `send_started_at` is when the send started, not a guaranteed publication time.
- Nothing changes on its own: no plan, slot or draft is updated from these lines.

## Test a line before acting on it

Treat a line as a question for a planned experiment:

1. Write a growth plan that alternates the two variants (for example with and without a link), see [growth experiments](GROWTH-EXPERIMENTS.md) and `examples/growth-plan.example.json`.
2. `reachweaver experiment-simulate --seed 1 --pairs 8 --lift 0.2 --noise 0.3` shows how many pairs a real difference needs.
3. `reachweaver experiment-preregister PLAN --out RECORD` before posting, `reachweaver experiment-readout ...` afterwards.

## JSON schema (`--json`, `reachweaver.insights` version 1)

Keys are stable within `schema_version` 1; new keys may be added, existing ones are not renamed or removed.

```text
kind              "reachweaver.insights"
schema_version    1
status            "ok" | "not_enough_data"   ("no_database" when the database file does not exist; then only
                                              kind, schema_version, status, database, how_to_get_data)
filters           {account, platform, since, since_utc, now, timezone, source}
method            {metric, denominator, unit, post_age_hours, age_tolerance_hours, min_posts_per_group,
                   comparison, strength_labels, interval: {method, resamples, confidence, seed} | null}
excluded          {reason: count} for posts without a usable send time
cohorts[]         {account_id, platform, source, other_sources: {source: posts_with_result},
                   published_in_window, posted_by_hand, posts_with_result, excluded: {reason: count},
                   status: "ok" | "not_enough_data", overall: {n, median, p25, p75},
                   features[]: {feature, label, groups_by,
                                groups[]: {value, label, rest_label, n, median, rest_n, rest_median,
                                           difference, relative_difference,
                                           interval: {confidence, difference: [low, high], excludes_zero, method} | null,
                                           strength: "too little data" | "observation"}},
                   suggestions[],
                   results_at_other_ages (only when some posts have results, none at the matched age):
                     {posts, hours_after_posting[], record_again_around: "YYYY-MM-DD" | null,
                      suggested_age_hours, matched_age_hours}}
suggestions[]     every cohort's lines: {account_id, platform, direction: "more" | "less", feature, value, label,
                   rest_label, n, median, rest_n, rest_median, relative_difference, interval, strength, text,
                   overlaps_with[]: {feature, value, label, n, shared},
                   shares_posts_with_lines[]: {line, shared}, test_with}
how_to_get_data[] plain-language steps (empty when status is "ok")
limits[]          the limits above, as sentences
disclaimer        one sentence
```

`feature` ids: `time_of_day`, `weekday`, `day_type`, `length`, `link`, `format`, `hashtag_count`, `hook`, `tag`. Values are `null` when unknown (for example `relative_difference` when the rest's median is 0).

## Using it from other views

`reachweaver.insights.top_insights(con, account_id=None, platform=None, since=None)` returns the same lines (without bootstrap intervals, so it is fast) and `[]` when there is not enough data. The read-only dashboard (`reachweaver ui`) shows them on the **Results** page under "What works for you".

## Where your results are used

Every command below that recommends something shows your own results next to its fixed rule. They all use the same method as `insights` with its defaults: interactions (likes + replies + reposts) at the matched age of 168 h (+/- 24 h), the snapshot source with the most matched posts, one account at a time, and groups of at least 5 posts against at least 5 others before a difference is called an `observation`. Smaller groups say `too little data`. No bootstrap interval is computed here, so run `insights` for the full report. They all read the database only (`SELECT`). None of them changes a score, slot, approval, schedule or send path, and nothing is fed back automatically.

| Command | What it shows | What it never does |
|---|---|---|
| `discovery-plan` | `your_results`: for each recommended tag, how your posts carrying it did (n, median against the rest, strength). Tags match the lowercased hashtags in the post text. Use `--account ID` to show one account. The database is read only when it already exists. | It never changes `editorial_relevance_score`, the order or the quotas. `score_adjustment` is always `null`. See [why](DISCOVERY-OPTIMIZATION.md#your-own-results-next-to-each-tag). |
| `followup-suggest` without `--rules` | One "took off" rule per account: `ceil(2 x your median)` interactions, compared only against snapshots from the same source taken no later than 192 h after posting. Each one is labelled "derived from your N posts". It needs at least 10 posts with a result. Below that, or with a median of 0, it prints a friendly next step and suggests nothing. | It invents no threshold. Human-only platforms get no rule. `--rules FILE` replaces the derived rules completely. |
| `experiment-next` | `--readout FILE` / `--readout-dir DIR` read saved `experiment-readout --json` files. `experiment-readout` and `experiment-evaluate` store nothing themselves, so save their JSON output. Labels and directions are copied unchanged. Each experiment counts once (the readout with the most usable pairs), and an id that is already in `--results` keeps that entry. | It never relabels a readout. |
| `suggest` (with a database) | P3 `what_works_for_you` items from `top_insights`, marked `observational_only`, each with its n and medians. It also adds one P3 `research_freshness` item when a bundled registry fact is aging or stale (`freshness.warning_line`). | P3 sorts after every safety, blocker and measurement item. |
| `calendar-plan` (proposals only) | `your_results`: your results by 6-hour block and weekday in `--timezone`, the blocks that did better (`better_blocks`), and the block each proposed slot falls in, with your own n and median. It appears only when at least one of your posts has a result. | The entries keep the planner's slots. `--apply` output and the scheduling path are untouched. To try a better block, pass it yourself as `--slot` or `--explore-slot`. |

These are observations from your own past posts, not causes. Test one before you change a habit (see [Test a line before acting on it](#test-a-line-before-acting-on-it)).
