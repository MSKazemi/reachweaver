# Predeclared learning loop: deliberate experiments, no p-value theater

Use this when the user asks about best hooks, posting times, formats, growth, why an item flopped, or which message works. The engine measures **directional associations** from existing data; it cannot establish causal lift in organic feeds.

## Design BEFORE publishing

1. State a single falsifiable hypothesis (e.g. proof-first hook vs generic opener), exact cohort (platform + account), and one changed variable. Keep topic, format, CTA, timing windows, audience and length as consistent as possible; note what cannot be controlled.
2. Predeclare primary metric, denominator (none/impressions/views), safety guardrail metric, a minimum useful relative lift, a maximum guardrail drop, at least 2 paired comparisons (recommend 3–5), and the snapshot age (e.g. 168h ±24h).
3. Record a dated plan **before** sending. The timestamp field is self-declared and is not independently tamper-proof; keep a Git commit or signed audit record when stronger preregistration is required.
4. Publish distinct posts only through the existing review/approval workflow. **Do not** auto-publish variants for the sake of an experiment. No repeated unsolicited posts, identical repost flooding, or algorithm claims.
5. Collect native cumulative snapshot metrics at approximately equal post ages using `metrics` / `import-metrics`; missing is unknown, not zero. Data sources/definitions must match. Analytics tools may undercount conversions; external website outcomes require separate attribution checks.
6. Evaluate offline. Report exclusions and missingness, absolute rates and denominator, per-pair directional agreement, relative lift and guardrail change. If fewer pairs than planned or zero baseline, report `inconclusive`.
7. Run a second independent test or consider an actual randomized first-party experiment before treating any finding as a rule. Don't assign statistical significance or causality to sequential organic comparisons.

```bash
python -m reachweaver.cli experiment-evaluate --file examples/experiment.example.json
```

Replace every example post ID with a **real** ReachWeaver published post ID. Both posts in each pair need a send timestamp and snapshots around the target age. Cross-account/platform comparisons, missing denominators, incomplete pairs, changed data sources and publications predating the declared plan are excluded. The CLI readout does not call social APIs or claim to know their ranking weights.

## Preregister the plan and keep the readout honest

Before any arm is published, write a growth plan (start from `assets/growth-plan.example.json` or `examples/growth-plan.example.json`), then run:

```bash
python -m reachweaver.cli experiment-preregister examples/growth-plan.example.json --out private/plan.prereg.json
python -m reachweaver.cli experiment-verify examples/growth-plan.example.json --hash sha256:<plan_hash>
```

- **Variants:** 2–3 arms, exactly one control. Each arm declares the ONE dimension it changes (`hook`, `framing`, `format`, `cta` or `timing`), and the other labels and the CTA link stay identical. More than 3 arms, near-duplicate respins, or a different CTA URL in a hook test are rejected.
- **Primary metric:** must be a qualified signal: `link_clicks` per impression, `replies`, `saves`, `profile_visits`, `repo_stars_utm` or `docs_visits`. Likes, views, impressions, reposts and followers are guardrails only.
- **Plan:** a falsifiable hypothesis that does not overclaim, one account/platform cohort, an observation window, `minimum_pairs` (6 or more recommended: with fewer, even unanimous pairs are compatible with chance under a sign test), a stopping rule that never stops early on a win, and a note for each of account_baseline, time, topic, platform, follower_count and feed_changes.
- **Tamper evidence:** the record holds a sha256 `plan_hash` and is written to a NEW file only. Commit it before the first post: the hash shows edits, not the date.
- **Design:** optionally declare `assignment` as `randomized`, `alternating` or `unrandomized` (the default). Only `randomized` is described as more than associational, and even then it is an unverified owner declaration, not causal proof.
- **Readout:** after publishing, write a post map (`kind: reachweaver.growth-post-map`, with `pairs` listing `{arm_id: post_id}` per pair) and run `experiment-readout --prereg FILE --posts MAP.json [--hash H] [--json]`. It refuses a record whose hash does not verify, and any post that is unpublished or outside the cohort's account and platform. It copies metric, denominator, window and thresholds into `experiment-evaluate` and only reads the database. It prints the label (`insufficient_data`, `inconclusive` or `directional`), the design, the novelty and single-outlier flags and a non-causal disclaimer. `directional` needs at least 6 pairs and a sign-test p of 0.05 or less (6 of 6, or 10 of 12). Never report "proven", "significant" or causal lift. For a `docs_visits` or `repo_stars_utm` primary metric, save `attribution-report --analytics CSV --json > attribution.json` and add `--attribution attribution.json`: each post's attributed sessions (or conversions) is its value, a post without one skips its pair (never 0); `--attribution-source ga4` reads the GA4 section of a report made with `--ga4-csv` instead, and the guardrail still comes from native snapshots. Issues, pull requests and contributors are repository-level and cannot be credited to a post.
- **Next test:** `experiment-next --results FILE` runs `growth.next_hypotheses` over a JSON list of earlier results. It records negative results and suggests the next dimension from the fixed order hook → framing → format → cta → timing. A suggestion is a hypothesis for the user to approve, not a draft or a schedule.
- **Calibration:** `experiment-simulate --seed S --pairs N --lift L --noise X --trials T` shows how often each label occurs on SIMULATED data. With seed 16 and noise 0.3, a zero true lift is labelled directional in 0.4–3.9% of runs at 6–20 pairs, and a true 20% lift in only 1–12%. Expect `inconclusive` from small tests, and never present simulated rates as account results.

- **Several experiments at once:** pass saved `experiment-readout --json` files to `baseline-eval --readout FILE ... --since D --until D`. It adds a seeded bootstrap interval of the median paired difference and a Holm–Bonferroni adjusted p (from the readout's own sign test) across all comparisons, and lists owner-declared randomized readouts apart from correlational ones. Labels are copied unchanged and `separated_after_holm` also needs `directional`, so two 6-of-6 readouts (p = 0.03125 each) become 0.0625 and `no_clear_difference`. A readout with a post outside the period is excluded whole.

Registration is not approval: every arm still goes through per-item approval, the dry run and the posting caps. Full rules: `docs/GROWTH-EXPERIMENTS.md` in the repository.

## Separate timing hypotheses from results

For a descriptive baseline from native cumulative snapshots, run `reachweaver timing-evaluate --account ACCOUNT --timezone Europe/Rome --source native-export --metric clicks --denominator impressions`. The CLI looks for a minimum of three matched-age posts in each of **two** six-hour dayparts and identifies only an `exploratory_candidate` if the median outcome rates differ. `--stratify format|campaign` repeats this per stratum; too little data gives `insufficient_data` and a `cold_start` pointer to calendar-plan's generic baseline instead of a winner. This is **not** a preregistered randomized trial and not a proven best-time rule; signal is confounded by creative, audience and distribution changes. A current-month reference might be stale; require independent future performance measurement before updating the posting strategy, and preserve an exploration/control slot. The command is offline and never schedules or publishes.

## Useful outcome hierarchy

- **Awareness:** views/reach/impressions only with native definitions and coverage.
- **Interest:** saves, shares, comments reflecting specific questions, click-through with the proper denominator.
- **Adoption:** qualified landing-page sessions, activated users, newsletter signups, repository uses, recurring contributors. Track the next step without treating attributed metrics as causal incrementality.
- **Trust:** verified discussions, corrections welcomed, endorsements given voluntarily; not purchased engagement.

The primary metric should follow the campaign objective; select a guardrail that would reveal erosion of usefulness (e.g. click lift should not crush saves per impression). Avoid post-hoc cherry-picking, universal optimal posting times, or projecting growth without user data.
