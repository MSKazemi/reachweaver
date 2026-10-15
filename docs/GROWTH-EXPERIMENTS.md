# Preregistered growth experiments — issue #16 (offline)

**Status:** Implemented as an offline guard layer in `reachweaver/growth.py` and `reachweaver/growth_readout.py`. It sits around the existing matched-age readout (`experiment-evaluate`, `reachweaver/experiments.py`). It validates a plan **before** anything is published and makes later edits detectable. It turns a verified preregistration into a read-only readout, labels the result conservatively and states its design (randomized or associational). Seeded simulations calibrate the labels, and a deterministic backlog suggests what to test next. It does **not** publish, schedule, draft, fetch analytics, randomize feed exposure or learn a recommender. This is one slice of [issue #16](https://github.com/MSKazemi/reachweaver/issues/46), not its completion.

## Workflow

```bash
# 1. Write a plan (copy examples/growth-plan.example.json), then validate it and register it.
#    The output file must be NEW: an existing file is never overwritten.
reachweaver experiment-preregister examples/growth-plan.example.json --out private/hook-test.prereg.json

# 2. Commit the record (or timestamp it externally) BEFORE the first post goes out.
# 3. Publish each arm only through the normal draft -> approve -> dry-run -> live workflow.
# 4. Before reading results, confirm the plan was not edited after registration:
reachweaver experiment-verify examples/growth-plan.example.json --hash sha256:<plan_hash from step 1>
# exit 0 = unchanged; exit 1 = edited or wrong hash

# 5. Map each arm to its published post ids (see "Post map" below), then read out.
#    Read-only: verifies the hash, converts, evaluates against the local DB and labels.
reachweaver experiment-readout --prereg private/hook-test.prereg.json --posts private/hook-test.posts.json
reachweaver experiment-readout --prereg private/hook-test.prereg.json --posts private/hook-test.posts.json --json > private/hook-test.readout.json

# 6. Keep every classified result (negative ones too) in a JSON list and ask for the next test:
reachweaver experiment-next --results private/results.json

# Optional: see how often each label occurs on SIMULATED data before trusting a design.
reachweaver experiment-simulate --seed 16 --pairs 6 --lift 0 --noise 0.3 --trials 2000
```

`experiment-readout` accepts an optional `--hash sha256:<hex>`. Pass the plan hash that you committed or timestamped elsewhere, and it must also match. This catches a record whose plan and `plan_hash` were both rewritten. `experiment-next` and `experiment-simulate` never open the database, and `experiment-readout` only reads it.

## Plan schema (`kind: "reachweaver.growth-plan"`, `schema_version: 1`)

| Field | Rule |
|---|---|
| `experiment_id` | Lowercase slug. |
| `hypothesis` | 20–1000 characters. Overclaiming words (`guaranteed`, `viral`, `proven`, `definitely`, `always`, `certainly`) are rejected. |
| `dimension` | Exactly one of `hook`, `framing`, `format`, `cta`, `timing`. |
| `cohort` | One `platform` (from `policy.PLATFORMS`) and one `account_id`. Cohorts are never pooled. |
| `primary_metric` | `{signal, denominator}`. `signal` must be a qualified signal (table below). |
| `guardrail_metrics` | At least one. May include vanity metrics. A guardrail cannot also be the primary metric. |
| `observation_window` | `metric_age_hours` 24–720, `age_tolerance_hours` 1–168 and below the age, `start`/`end` as `YYYY-MM-DD`, at most 120 days. |
| `minimum_pairs` | Integer 2–100. Below 6 gives a warning (see the heuristic below). |
| `minimum_relative_lift`, `maximum_guardrail_drop` | Numbers in [0, 1], fixed before publishing. |
| `stopping_rule` | `type` is `fixed_pairs` or `window_end`. `max_pairs` must be ≥ `minimum_pairs` and ≤ 100. `stop_early_only_for` may list only `guardrail_breach`, `account_incident` and `platform_policy_change`. Any other key is rejected, so a plan cannot declare "stop when winning". |
| `confounders_acknowledged` | An object with a nonempty note for each of `account_baseline`, `time`, `topic`, `platform`, `follower_count` and `feed_changes`. More confounders may be added. |
| `variants` | 2–3 arms (see below). |
| `assignment` | Optional: `randomized`, `alternating` or `unrandomized` (the default). It records how the arms were assigned to slots or send order. It is declared by the owner and is covered by the hash. See [design labels](#design-labels-randomized-vs-associational). |
| `notes` | Optional free text. |

Unknown top-level fields and secret-like keys (`token`, `password`, `secret`, `cookie`, `session`, `api_key`, …) are rejected.

### Qualified primary signals

| Signal | Allowed denominators | Source |
|---|---|---|
| `link_clicks` | `impressions` only | native snapshot field `clicks` |
| `replies` | `impressions`, `views`, `none` | native snapshot field `replies` |
| `saves` | `impressions`, `views`, `none` | native snapshot field `saves` |
| `profile_visits` | `impressions`, `none` | native analytics export; no snapshot field yet |
| `repo_stars_utm` | `none` | stars attributed by UTM (`attribution-report`) |
| `docs_visits` | `none` | UTM-attributed docs sessions (`attribution-report`) |

`likes`, `views`, `impressions`, `reposts` and `followers` are **guardrail-only**. A plan that uses one of them as the primary metric is rejected. Signals that are not native snapshot fields produce a warning: collect them through first-party UTM attribution, not `experiment-evaluate`.

## Variant rules

Each arm has `id`, `role` (`control` or `treatment`, with exactly one control), `differs_in`, `text`, the labels `hook`, `framing`, `format` and `timing`, and `cta_url` and/or `cta_phrase`.

- **2–3 arms only.** Four or more is rejected as mass blasting.
- **One dimension.** Every arm's `differs_in` must be a single string equal to the plan `dimension`. A list is rejected.
- **Everything else is held constant.** For every dimension except the tested one, each treatment's label must equal the control's. The CTA is compared as normalized `cta_url` host/path plus folded `cta_phrase`.
- **Detectable drift is rejected.** Unless the tested dimension is `cta`, the set of links in the text (host + path) must match the control. A different destination URL in a hook test is an error. Query strings are ignored, so per-arm `utm_content` values are fine.
- **The tested dimension must really differ.** An identical label or CTA is rejected. In a hook test, a near-duplicate opening line is also rejected.
- **No near-duplicate posts.** Arms are compared with `claims.jaccard` (word 3-gram Jaccard, default threshold 0.6, the same function as claim grounding). Each arm is a distinct post shown to the same audience, so a respin with a few words changed is repost flooding. Hold the *declared* dimensions constant and write a genuinely distinct post.

These checks compare declared labels and detectable links. They cannot prove that two texts differ only in the declared dimension, so a human still reviews every arm.

## Preregistration and tamper evidence

`preregister(plan)` runs every check, raises `ValidationError` on any error, and returns a record with these fields:

- `plan_hash`: `sha256:` plus the hex digest of the plan's canonical JSON (keys sorted, `(",", ":")` separators, UTF-8, `ensure_ascii=false`). Whitespace and key order do not change it. Any value edit does.
- `registered_at`: the local UTC clock.
- `power_heuristic`, `warnings` and `limitations`.
- `status: "registered_not_approved"`.
- the plan itself.

`write_preregistration` creates a **new** 0600 file (`O_EXCL`/`O_NOFOLLOW`). An existing path or symlink is refused. `verify_preregistration(plan, hash)` accepts either the original plan or the record. With a record, it also checks the embedded `plan_hash`. It compares in constant time.

**Limit:** a hash match shows that the plan is unchanged. It does not prove *when* the plan was written. `registered_at` is self-reported, so commit the record to Git or use an external timestamp before the first post.

## Minimum-sample heuristic

No significance claim is made. Instead, the guard uses a two-sided exact sign test as a floor. If every one of *n* pairs points the same way, the probability under a coin-flip null is `2 × 0.5ⁿ`. That is 0.0625 for n = 5 and 0.03125 for n = 6, so **6 is the smallest number of pairs at which even unanimous agreement is unlikely by chance**. Below 6, preregistration warns and a readout can be at most `inconclusive`. This is a lower bound on the sample needed to read a direction, not a power calculation for an effect size.

The same threshold also gates the `directional` label (`MAX_DIRECTIONAL_SIGN_P = 0.05`). The descriptive sign-test p over untied pairs must be at most 0.05, which takes 6 of 6, 8 of 8, 9 of 10 (p = 0.021) or 10 of 12 (p = 0.039) agreeing pairs. A strict majority alone is no longer enough. The [simulations below](#simulated-calibration-simulated) found that without this gate, about a third of zero-lift experiments were labelled `directional`.

## Readout labels (`classify_result`)

The input is `evaluate_experiment` output. The plan supplies `minimum_pairs` (default 6, never below 2), `minimum_relative_lift` (default 0.1) and `maximum_guardrail_drop` (default 0.1). The only labels are:

| Label | When |
|---|---|
| `insufficient_data` | Fewer usable pairs than the preregistered minimum. Excluded pairs are reported; missing data is not zero. |
| `inconclusive` | No strict majority of pairs agrees with the pooled direction. Or one of these: no pooled lift was reported, the lift is below the minimum useful effect, a positive result breached the guardrail, there are fewer than 6 pairs, the sign-test p is above 0.05, or a novelty/outlier flag fired. |
| `directional` | At least `max(minimum_pairs, 6)` pairs, enough agreement for a sign-test p of 0.05 or less, a lift at or above the minimum, no guardrail breach and no flags. The output includes `direction` (`positive`/`negative`). It means a directional association to replicate on a fresh cohort. |

There is no `proven`, `significant`, `winner` or `causal` label. Flags:

- **`novelty_effect`**: the first pair (in plan order, assumed chronological) carries at least 50% of the total absolute rate difference in the pooled direction, or dropping it reverses the direction.
- **`single_outlier`**: any other pair carries at least 50% of the total absolute difference, or removing it reverses the direction, or the median pair difference points against the pooled total.

The output also reports per-direction counts, the majority share, a descriptive sign-test p, the median pair difference and the evaluator's original verdict. `experiment-evaluate` now applies the same minimum-pairs and sign-test gate, so it no longer says `directional_positive` for a tiny sample (it returns `no_consistent_win`); a clearly negative, well-supported result is `directional_negative`.

## Post map and readout (`experiment-readout`)

The post map is a small file that the owner writes. It records which published post carried which arm in each pair:

```json
{
  "kind": "reachweaver.growth-post-map",
  "schema_version": 1,
  "experiment_id": "hook-proof-vs-generic-2026-10",
  "treatment_arm": "treatment-proof-first",
  "pairs": [
    {"control-generic": "<published post id>", "treatment-proof-first": "<published post id>"}
  ]
}
```

`treatment_arm` is needed only when the plan has two treatment arms, because each readout compares the control with one treatment. `growth_plan_to_experiment(record, post_map, con)` builds the `experiment-evaluate` plan. It **refuses** when:

- the input is not a `reachweaver.growth-preregistration` record;
- the record's `plan_hash` does not verify, or `--hash` was given and differs;
- the embedded plan fails validation;
- the map's `experiment_id` differs from the plan's, a pair uses an arm id the plan does not have, or a post is reused;
- the map lists more pairs than `stopping_rule.max_pairs`;
- a control or treatment post is unknown, is not `published` with a send timestamp, or is on a different account or platform than the cohort;
- the primary signal is not a native snapshot field (`profile_visits`, `repo_stars_utm`, `docs_visits`). Use `attribution-report` for those.

The database is only read. The converter copies these values unchanged from the registered plan:

- the primary metric (`link_clicks` becomes the snapshot field `clicks`) and its denominator;
- `metric_age_hours` and `age_tolerance_hours`;
- `minimum_pairs`, `minimum_relative_lift` and `maximum_guardrail_drop`.

`pre_registered_at` is the record's `registered_at`. `experiment-evaluate` reads one guardrail: the first that is a native snapshot field on the primary's denominator. The others are listed under `conversion.guardrails_not_evaluated` with a reason. The converted plan also carries the `plan_hash`, cohort, dimension, assignment and arm ids, so each readout can be traced to its registration.

The readout runs `evaluate_experiment` and then `classify_result`, and prints:

- the label and direction;
- the design block;
- usable, mapped and skipped pairs (missing data is excluded, never counted as zero);
- the `novelty_effect` and `single_outlier` flags;
- the explanations and warnings;
- a fixed non-causal disclaimer.

It warns about three things: posts sent outside the preregistered window, one arm always sent first (send order fully confounded with the arm), and a declared `alternating` design whose send order is not balanced.

## Design labels: randomized vs associational

`describe_design(assignment)` produces the `design` block of every readout. `verified` is always `false`, because ReachWeaver cannot see how the owner chose the order.

| `assignment` | `interpretation` | Meaning |
|---|---|---|
| `randomized` | `randomized_assignment_owner_declared` | The owner says arm order or slot was randomized, for example with a coin flip per pair. That removes systematic order bias in expectation. Feed allocation, audience and topic still vary, so the result is a within-cohort comparison, not a causal or proven effect. |
| `alternating` | `associational` | Order alternated by a fixed rule. That balances order, but it is not random. |
| `unrandomized` (or omitted) | `associational` | Ordinary organic posts. |

A design that is not declared `randomized` can only be described as associational. The code enforces this, and the readout adds an explanation that says so. Together with the missing-data exclusions and the `insufficient_data` label, the output separates correlation, owner-declared randomized comparisons and missing data.

## Simulated calibration (SIMULATED)

`simulate_readout(seed, n_pairs, true_lift, noise, trials=...)` and `experiment-simulate` generate synthetic paired snapshots with `random.Random(seed)`. Each synthetic experiment goes through the same `classify_result`, and the labels are counted. Nothing is written to the database. The same seed and parameters always give the same output.

The model, per synthetic pair:

- a shared log-normal topic/week factor (`pair_sd` 0.3) applies to both arms;
- each post has log-normal rate noise (`noise`) and log-normal impressions (about 1,000, sd 0.25);
- clicks and saves are Poisson counts, with base rates of 1% clicks and 2% saves per impression;
- only clicks carry `true_lift`. The guardrail (saves) has no true effect.

`minimum_pairs` equals the number of pairs and `minimum_relative_lift` is 0.1. A test checks that the in-memory pooled readout equals `evaluate_experiment` on the same snapshots.

**SIMULATED results.** Seed 16, 2,000 trials per row, noise 0.3. The interval is a 95% Wilson interval for the Monte Carlo rate. The last column is the spread of the pooled relative-lift estimate across trials.

| Pairs | True lift | insufficient_data | inconclusive | directional (rate; 95% CI) | positive / negative | median estimated lift [p05, p95] |
|---|---|---|---|---|---|---|
| 5 | 0.0 | 0 | 2000 | 0 (0.0%; 0.0–0.2%) | 0 / 0 | -0.001 [-0.375, 0.591] |
| 5 | 0.2 | 0 | 2000 | 0 (0.0%; 0.0–0.2%) | 0 / 0 | 0.181 [-0.242, 0.863] |
| 5 | 0.5 | 0 | 2000 | 0 (0.0%; 0.0–0.2%) | 0 / 0 | 0.505 [-0.026, 1.399] |
| 6 | 0.0 | 0 | 1956 | 44 (2.2%; 1.6–2.9%) | 20 / 24 | -0.002 [-0.345, 0.547] |
| 6 | 0.2 | 0 | 1936 | 64 (3.2%; 2.5–4.1%) | 59 / 5 | 0.182 [-0.220, 0.832] |
| 6 | 0.5 | 0 | 1772 | 228 (11.4%; 10.1–12.9%) | 228 / 0 | 0.497 [0.001, 1.297] |
| 8 | 0.0 | 0 | 1991 | 9 (0.4%; 0.2–0.9%) | 4 / 5 | 0.002 [-0.307, 0.436] |
| 8 | 0.2 | 0 | 1976 | 24 (1.2%; 0.8–1.8%) | 24 / 0 | 0.177 [-0.168, 0.706] |
| 8 | 0.5 | 0 | 1847 | 153 (7.6%; 6.6–8.9%) | 153 / 0 | 0.508 [0.068, 1.176] |
| 12 | 0.0 | 0 | 1940 | 60 (3.0%; 2.3–3.8%) | 32 / 28 | 0.002 [-0.252, 0.346] |
| 12 | 0.2 | 0 | 1855 | 145 (7.2%; 6.2–8.5%) | 137 / 8 | 0.182 [-0.108, 0.594] |
| 12 | 0.5 | 0 | 1417 | 583 (29.1%; 27.2–31.2%) | 583 / 0 | 0.503 [0.123, 1.027] |
| 20 | 0.0 | 0 | 1922 | 78 (3.9%; 3.1–4.8%) | 33 / 45 | 0.003 [-0.211, 0.255] |
| 20 | 0.2 | 0 | 1759 | 241 (12.0%; 10.7–13.6%) | 238 / 3 | 0.187 [-0.056, 0.496] |
| 20 | 0.5 | 0 | 1005 | 995 (49.8%; 47.6–51.9%) | 995 / 0 | 0.498 [0.202, 0.908] |

To reproduce a row, run for example `reachweaver experiment-simulate --seed 16 --pairs 6 --lift 0 --noise 0.3 --trials 2000`. `insufficient_data` is 0 because the simulation has no missing snapshots.

Under this model only, the SIMULATED runs show:

- **The gate was needed.** Under the earlier strict-majority rule, with the same seed, `directional` came up in 32.4%, 33.9% and 36.7% of zero-lift experiments at 6, 8 and 12 pairs. With the sign-test gate it comes up in 0.4–3.9% at 6–20 pairs, consistent with the gate's 0.05 two-sided bound.
- **Below 6 pairs, nothing is `directional`**, even with a true lift of 50%.
- **The label is conservative.** A true 20% lift is labelled `directional` in only 1–12% of experiments at 6–20 pairs, and a 50% lift in 8–50%. `inconclusive` is the expected outcome of a small organic test, and it is not evidence of no effect.
- **Single estimates are wide.** At 6 pairs and zero true lift, the middle 90% of pooled-lift estimates runs from about -34% to +55%.

These numbers describe synthetic data from one assumed noise model. They say nothing about any real account or platform. Before registering a design, vary `--noise`, `--pairs` and `--lift` to check it against your own assumptions.

## Hypothesis backlog (`next_hypotheses`)

The input is a list of `{experiment_id, dimension, label, direction}`. The output keeps every result, lists **negative results**, and makes one suggestion. Negative results are `inconclusive` results and `directional` results whose direction is not positive. They are never dropped, so a failed idea is not silently retried. The suggestion comes from fixed rules, applied in order:

1. `finish_pending`: an experiment with `insufficient_data` and no completed result is finished first.
2. `replicate`: a dimension whose latest completed result is its only `directional` positive is replicated on a fresh cohort.
3. `next_untested`: the first dimension with no completed test, in the fixed order `hook → framing → format → cta → timing`. Content changes come first because they are the cheapest and least confounded. Timing comes last because it is the most confounded by feed and audience activity.
4. `least_tested`: the dimension with the fewest completed tests, with ties broken by the same order.

The output always has `requires_human_approval: true`. A suggestion is a hypothesis to preregister, not a draft, schedule or permission to publish. `experiment-next --results FILE [--json]` runs it over a JSON list of earlier results. The `--json` output of `experiment-readout` includes the `experiment_id`, `dimension`, `label` and `direction` fields that each entry needs.

## Several experiments at once (`baseline-eval --readout`)

Save each readout with `experiment-readout ... --json > FILE` and pass the files to `baseline-eval --readout FILE [--readout FILE ...]` with an explicit `--since/--until` (see [TIMING-EVALUATION.md](TIMING-EVALUATION.md#evaluate-against-the-non-personalized-baseline-baseline-eval)). For each readout it adds a seeded percentile bootstrap interval of the median paired difference and a Holm–Bonferroni adjusted p over all comparisons in the run, using the readout's own sign-test p. Readouts declared `randomized` are listed apart from correlational ones. The readout `label` is copied unchanged, and `separated_after_holm` also requires `directional`, so the strict gate is never weakened: two 6-of-6 `directional` readouts (p = 0.03125 each) are Holm-adjusted to 0.0625 and reported as `no_clear_difference`. A readout with any post outside the period is excluded whole.

## Causal limitations

Sequential organic posts are observational. Feed allocation, follower growth, topic, holidays, platform ranking changes and the time of day all differ between arms. Matching post age and holding declared dimensions constant reduces some confounding but does not remove it. A `directional` label is an association in one account/platform cohort. It does not show that the change caused the outcome or will repeat, and it promises nothing about reach. Treat a finding as a pattern only after an independent replication.

## No-go tactics

These are rejected by code or remain out of scope:

- More than 3 arms, near-duplicate respins, and identical reposts to "test" timing.
- Likes, views or impressions as the success metric.
- Early stopping on a favourable result. Editing the metric, thresholds or window after registration (`experiment-verify` detects this).
- Pooling accounts or platforms. Calling a result proven or causal.
- Automatic publishing or scheduling of experiment arms. Every arm still needs per-item human approval, the dry-run default, the per-account caps (`--max-per-24h`, `--min-gap-minutes`) and the emergency stop. This is tested, not just stated (`tests/test_growth_policy.py`): an AST guard proves the advice modules (`growth`, `growth_readout`, `baseline_eval`, `timing_analysis`, `experiments`, `audience`) import no publisher, adapter or network module and only read helpers from `storage`, and name no claim, approve, schedule or send function; and two experiment arms driven through the CLI stay drafts until approved, are a dry run by default, are refused without the live opt-in, are held to one send per account in a sweep by the minimum gap, stop on `pause`, and are blocked when edited after approval.
- Scraping, fake engagement or buying reach to fill a sample.

## Not implemented

- Native collection of `profile_visits`.
- Randomized exposure. ReachWeaver neither randomizes nor verifies assignment: `assignment: randomized` is the owner's declaration.
- A real multi-week pilot (#27). Bootstrap intervals, Holm–Bonferroni correction and comparison against the `calendar-plan` non-personalized baseline exist in `baseline-eval` (reporting only), but no real account has been evaluated.
