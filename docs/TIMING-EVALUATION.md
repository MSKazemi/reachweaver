# Timing-outcome evaluation — issue #16 (offline, observational)

**Status:** Implemented as read-only **exploratory** analyses (`timing-evaluate`, `baseline-eval`) of the operator's previously imported, source-labeled metrics. It does **not** fetch analytics, connect social accounts, discover platform ranking weights, train an ML model, prove an optimal time, or schedule a post. This is an incremental part of [issue #16](https://github.com/MSKazemi/reachweaver/issues/46), not its completion.

## Purpose

The initial [calendar planner](CALENDAR-PLANNER.md) uses low-confidence industry time windows or explicitly selected audience hours. The `timing-evaluate` command answers a narrower, testable question: **in this one account and source-labeled metric cohort, which six-hour dayparts have shown higher typical native outcome rates at matched post ages?** Results must be checked against a future independent set of posts, not promoted as a hidden algorithm hack.

## Safe use

1. Publish only approved, authorized, original material through a separate supported workflow. An account must be owned/authorized. No test publishes a real post.
2. Collect **native platform** metric snapshots for each published post (e.g., 7 days after publication). Label their actual provenance consistently, and obtain any required consent before exporting and storing. `native-export` is a user-supplied label, **not** cryptographic proof of provenance.
3. Import privately into the protected local SQLite DB. Do not commit post analytics, credentials, follower data or sensitive comments. Published posts must have `send_started_at`; this is an approximation of publication time.
4. Evaluate ONE configured account, platform, and named data source at a time. Prefer an explicit `--campaign` filter when topic, message intent or format otherwise differ substantially.

```bash
# Import real, approved user-exported snapshots; NO network access:
reachweaver import-metrics --csv /private/path/to/native-metrics.csv

# Observational / read-only report, NO queue changes:
reachweaver timing-evaluate \
  --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --age-hours 168 --tolerance-hours 24 --min-posts-per-slot 3 \
  --campaign first-campaign --since 2026-09-01 --until 2026-10-01 \
  [--stratify format|campaign]
```

`--timezone` denotes **audience-local** time. The source, account, format/cohort, metric definition, denominator and age window must be consistent. Importing the same cumulative post snapshot more than once does NOT add counts: the evaluator selects the one source-matching, nearest-age observation for each post.

## Readout

- **`posts_with_comparable_snapshots`**: number of published posts with valid native snapshot rate near requested age. Invalid/missing are excluded, never assigned zero.
- **`slots`**: weekday/weekend x four **six-hour** windows (00–06, 06–12, 12–18, 18–24), with sample count, median metric/denominator rate, and interquartile range (p25–p75). No raw unpublished post text is included.
- **`decision`**: `insufficient_comparable_slots` if fewer than two groups have the minimum number of matched observations; `no_clear_separation` if their medians tie; otherwise `exploratory_candidate` with the highest observed median and a comparison to another qualified time group. All are **descriptive associations**, not guaranteed future lift or statistical proof.
- **`excluded`**: per-reason counts for missing/invalid send timestamps and missing source-matched comparable snapshots or non-positive denominator.
- **`cold_start`** (only when `decision` is `insufficient_comparable_slots`): no slot is reported as better. Instead it points to `calendar-plan`'s generic, non-personalized slots for the platform (the same baseline `baseline-eval` uses; for example Bluesky `Mon@18:00` … `Sun@18:00`), with `evidence: industry_observational` and `confidence: low`. A platform without a generic prior (Mastodon, Telegram and others) gets `fallback: none_available`, `generic_baseline: null` and a note to choose slots from your own audience insights.
- **`strata`** (only with `--stratify`, issue #16 R2): the same readout (`slots`, `decision`, `candidate`) for each stratum separately, sorted by name, with its own `posts_with_comparable_snapshots`. `--stratify format` groups posts as `text`, `image` (an attached image, `media_json`) or `video` (an attached video, `video_json`); only the presence of the stored attachment is read, nothing is decoded, and a row with both is `unknown`. `--stratify campaign` groups by the post's campaign, with `(no campaign)` for posts without one. A stratum with fewer than two slots at `--min-posts-per-slot` gets `status: insufficient_data`, `candidate: null` and its own `cold_start` pointer, never a winner. The pooled fields above are unchanged by the flag and still mix strata, so compare within one stratum. Topic, promotion and follower growth remain uncontrolled inside a stratum.
- **`warnings`**: always disclose confounding by topic, format, follower count, algorithm changes, holidays, promotion and the selected winner's potential regression to the mean.

Examples of **inconclusive** evidence: only three posts total, one fully populated daypart, zero denominator, unsupported cross-platform metric comparison, stale samples, or no exact source matches. The correct action is to gather clean data, not force a recommendation.

## Work with calendar-plan without automatic feedback loops

`timing-evaluate` does **not** modify `calendar-plan` or call `--apply`. If a group looks promising, choose one or two *specific* weekday/hour combinations within that group as a **human-reviewed experimental hypothesis**, not as an assertion that the six-hour median proves a precise minute. Use `calendar-plan --slot 'Wed@16:00' --slot 'Fri@09:00'` to propose a new batch while reserving a reasonable number of comparison slots, or keep the current slots and add `--explore-share 0.25 --explore-slot 'Wed@16:00'` so a capped, labelled share of the batch tries the new time ([CALENDAR-PLANNER.md](CALENDAR-PLANNER.md#2c-reserve-a-capped-share-for-exploration---explore-share-issue-16)). Review exact posts and accounts before any local application or manual native handoff. Only user-authorized publishing can take place later through the existing guarded workflow.

`calendar-plan --audience-activity FILE` is a separate input. It takes candidate slots from the owner's exported hour-of-week **audience activity** (presence), not from outcomes ([CALENDAR-PLANNER.md](CALENDAR-PLANNER.md#2b-plan-from-your-own-audience-activity-export---audience-activity-issue-16)). Those slots are labelled `first_party_audience_activity` / `unverified`, and evaluating them is still the job of `timing-evaluate` and `baseline-eval`. Neither command reads the activity CSV, and no result is fed back into the planner automatically.

A better experiment predeclares hypotheses, metric/guardrail and observation age and collects a fresh holdout cohort, controlling other factors where practical. Use `experiment-evaluate` for pre-registered paired post comparisons. Sequential organic exposure is not randomized. A discovered `exploratory_candidate` must **never** be called a causal improvement.

## Evaluate against the non-personalized baseline (`baseline-eval`)

`timing-evaluate` describes dayparts. `baseline-eval` asks a narrower question: **over an explicit period, did posts at the account's own slots (or in a named variant campaign) show different matched-age outcome rates than posts at `calendar-plan`'s generic, non-personalized slot?** The baseline is exactly the slot `calendar-plan` proposes when no `--slot` is given (for example Bluesky 18:00 every day, LinkedIn 16:00 on weekdays; `calendar.generic_baseline_slots`). Platforms without a generic prior (Mastodon, Telegram and others) have no baseline, and the command refuses `--slot`/`--campaign-arm` for them.

```bash
# Choose the period BEFORE looking at results. Read-only; no network, no queue change.
reachweaver baseline-eval \
  --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --since 2026-09-01 --until 2026-10-31 \
  --slot exploit:Tue@09:00 --slot explore:Sat@11:00 \
  --campaign-arm explore:hook-proof-first \
  --readout private/hook-test.readout.json \
  [--age-hours 168] [--tolerance-hours 24] [--slot-tolerance-minutes 60] \
  [--min-posts 5] [--resamples 2000] [--confidence 0.95] [--seed 16] [--json]

# Website/repository outcome instead of a platform rate (issue #16):
reachweaver attribution-report --analytics private/analytics.csv --json > private/attribution.json
reachweaver baseline-eval --account my-bluesky --timezone Europe/Rome \
  --since 2026-09-01 --until 2026-10-31 --slot exploit:Tue@09:00 \
  --attribution private/attribution.json --outcome docs_visits|repo_stars_utm
```

How posts are grouped:

- Only **published** posts of the one configured account/platform whose **audience-local send date** is in `--since..--until` (both required, inclusive, at most 367 days) are used.
- A post within `--slot-tolerance-minutes` of a `--slot` belongs to that arm. A post whose campaign equals a `--campaign-arm` belongs to that arm. A post at the generic slot that is in no arm is **baseline**. A post that matches two arms is excluded as `ambiguous_arm`; a post that matches nothing is counted as unassigned. A `--slot` whose window overlaps the generic slot, or another `--slot`, is refused.
- Each post gets one rate: `metric / denominator` from the single exact-`--source` snapshot nearest the target age (same rule as `timing-evaluate`). A post without one is counted in `posts.excluded` as `no_matched_snapshot:<group>`, never as zero.
- With `--attribution FILE --outcome docs_visits|repo_stars_utm` (instead of `--source/--metric/--denominator`, which are then refused), each post's value is its attributed `sessions` or `conversions` from a saved `attribution-report --json` file, and the medians compare counts per post. A post the report does not credit, with a blank count, sent before the analytics window or with less than `--age-hours` of window after it is excluded as `<reason>:<group>`, never counted as zero. The rules are the same as for `experiment-readout --attribution` ([GROWTH-EXPERIMENTS.md](GROWTH-EXPERIMENTS.md#website-and-repository-outcomes---attribution-issue-16)).

What is reported for each arm (one comparison against the baseline):

- **Estimate:** median arm rate minus median baseline rate, and the relative difference when the baseline median is above zero.
- **Uncertainty:** a percentile bootstrap interval (`--resamples`, `--confidence`). Randomness comes from `random.Random` seeded with `--seed` and the arm id, so the same inputs always give the same output, and adding or reordering arms does not change another arm's numbers.
- **p-value:** a two-sided permutation test of the median difference. It is exact (every relabelling) when there are at most 20,000 relabellings, otherwise Monte Carlo with the add-one correction, so it is never 0. Known answers: 3 posts at 1, 2, 3 against 4, 5, 6 gives p = 0.2, and even perfectly separated 4-versus-4 groups give p = 4/70 ≈ 0.057.
- **Multiple testing:** Holm–Bonferroni across **every comparison with a p-value in the run**, arms and readouts together (`multiple_testing.family_size`). With three tested arms the smallest p is tripled.
- **Status:** `insufficient_data` when either group has fewer than `--min-posts` (default 5) rates, with every estimate, interval and p set to `null`, not 0; `separated_after_holm` only when the interval excludes zero **and** the Holm-adjusted p is at most `1 - confidence`; otherwise `no_clear_difference`. `direction` is `arm_higher` or `arm_lower`.
- **Role:** each arm is declared `explore:` or `exploit:` (default `explore`). `calendar-plan --explore-share` lists the matching values in `exploration.baseline_eval_slots`. `explore_exploit` reports post counts per role and the exploration share. An exploit arm with no clear difference is flagged.

**Correlational versus randomized.** All arm-versus-baseline comparisons are in the `correlational` list: the operator chose when to post, so they are associations. Saved `experiment-readout --json` files (`--readout`, repeatable) are added as `preregistered_experiment` items. A readout goes in the `randomized` list only when its plan declared `assignment: randomized`, which is the owner's unverified declaration; any other design goes in `correlational`. A readout is used whole or not at all: if any of its posts was sent outside the period, it is listed in `excluded_readouts`. Its interval covers the median paired difference, its p-value is the readout's own sign test, and its `readout_label` is copied unchanged. The strict sign-test gate (owner decision, 2026-10-08) is the only source of `directional`: `separated_after_holm` additionally requires that label, so this report can only be more conservative. For example, two `directional` readouts with 6 of 6 agreeing pairs each (p = 0.03125) become Holm-adjusted 0.0625 and are reported as `no_clear_difference`, labels untouched.

Limits: small-sample percentile bootstrap intervals tend to be too narrow, and medians of a few posts move in coarse steps. Holm controls false positives only within one run, so re-running over other periods or arms after seeing results is itself multiple testing. The generic slot is a low-confidence industry prior, so the baseline is a comparison group, not a proven default. Nothing here can approve, schedule or publish, and `calendar-plan` is not changed.

## Limitations and next steps

- The database currently records `send_started_at` (start of request), not independently verified remote publication time; small timestamp error can change bins near boundaries.
- Format is only the attachment type stored on the post row (text, image or video), not a richer format such as a carousel, thread or Short. `--stratify format`/`--stratify campaign` and a `--campaign` filter reduce, but do not eliminate, topic/format confounding, and each stratum needs its own minimum sample.
- Cross-platform metric definitions are incompatible; per-account/source segmentation is mandatory.
- Website sessions and conversions come only from the owner's own analytics export joined by `attribution-report` (`baseline-eval --attribution`); `timing-evaluate` still reads platform snapshots only. Use `clicks` only when its platform definition and denominator are verified. Repository-level collaboration outcomes (issues, pull requests, contributors) cannot be credited to a post or slot.
- `timing-evaluate` itself reports medians and IQRs only. Bootstrap intervals, Holm–Bonferroni correction and the comparison against the non-personalized baseline live in `baseline-eval` (above). There is no hierarchical recommender or calendar auto-optimization. Both readouts intentionally stay observational.
- Next work in #16: consented native audience activity import, more exact format/goal cohorts, preregistered out-of-sample time trials, and running `baseline-eval` over a real multi-week pilot (#27). No real account has been evaluated yet.

## Regression and bundled skill checks

```bash
python -m unittest discover -s tests -v
python scripts/sync_skill.py
python -m unittest discover -s tests -v
```

CI tests the full suite on Python 3.11–3.13 and checks that the portable Claude Code skill's runtime matches canonical code. No tests use live social APIs.
