# Timing intelligence and evidence-aware calendar planning

**Read this when asked:** best time or day to post, schedule 5–100 posts, batch publishing, content calendar, automatic posting, engagement timing, platform-specific posting patterns. Also consult `platform-algorithms.md`, `publishing.md`, and the [dated research report](../../../../docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md) when available in the full repository. In a copied standalone skill, links outside this folder may not exist; use these references directly.

## Never confuse three separate actions

- **Propose** is analysis only: produce a calendar and evidence/confidence for human review, with `status=proposed`. No network request.
- **Native scheduled** is a user-confirmed entry in LinkedIn, Meta Business Suite/Instagram, TikTok, YouTube Studio, X, Threads, Pinterest or other platform **after** user logs in, grants access and confirms that platform's own queue accepted it. Without receipt, say `native_handoff_pending`, NOT `scheduled`.
- **ReachWeaver scheduled** is a locally approved draft with a UTC due timestamp. It is *not remotely published*. Auto-send requires a trusted persistent scheduler, real account authorization, `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`, no emergency pause, and supported adapter. Only Bluesky/Mastodon/Telegram/LinkedIn-member have mock-tested text send adapters; none have live account verification. Unsupported platforms remain manual.

## Low-confidence 2026 population priors

One provider analysis covered 52M+ posts across its own customer population and observed engagement, not randomized experiments on the operator's accounts: [Buffer, 2026-03-05](https://buffer.com/resources/state-of-social-media-engagement-2026/). Time suggestions are **audience-local** and **candidate windows**, not promises or universal algorithm weights.

| Platform | Candidate time windows | Baseline label |
|---|---|---|
| LinkedIn | Weekdays 15:00–20:00; sample Wed 16:00 | `industry_observational` |
| Instagram | Weekdays 18:00–21:00; separately test Thu 09:00 | `industry_observational` |
| Facebook | Weekdays 08:00–11:00; sample Thu 09:00 | `industry_observational` |
| X | Weekdays 06:00–11:00; sample Tue 09:00 | `industry_observational` |
| Threads | Weekdays 06:00–11:00; sample Thu 09:00 | `industry_observational` |
| TikTok | Weekends 08:00–11:00; sample Sun 09:00 | `industry_observational` |
| Bluesky | Weekend afternoons/evenings 16:00–19:00; test weekday evenings | `industry_observational`; see [updated Bluesky study](https://buffer.com/resources/best-time-to-post-on-bluesky/) |
| YouTube | Use own Audience heatmap; consider staff availability for live/Premieres | `first_party_preferred`; official [FAQ](https://support.google.com/youtube/answer/141805?hl=en) says long-term performance **not known to depend on publish time** |
| Mastodon, Telegram, Pinterest, Reddit | Platform/subscriber/community-specific audience data; do not fabricate a globally best clock hour | `unknown_prior` |
| Product Hunt | Pacific-time launch-day cycle, user/staff launch availability | `platform_fixed_cycle` |
| Hacker News | Human-written, human-submitted only; never automate | `automation_disallowed` |

These windows will change with sample mix, platform features and audience. More current own-account evidence overrides all third-party population summaries.

## Inputs for each recommendation

Require only available information, make unknown fields explicit. Use:

- `brand_id`, `account_id`, `platform`, `post_id` or `variant_id`, content `format`, audience segment, goal, due-after/deadline/embargo, human reviewer;
- audience IANA timezone (e.g. `Europe/Rome`, `America/New_York`) or justified multiple cohort zones; content language; excluded days and practical response coverage;
- permitted native scheduler formats, upload/auth status, account eligibility, desired weekly cadence, queue horizon, already scheduled posts and spacing constraints;
- owned native audience activity, post metadata, native metric snapshots, first-party website conversion/GitHub downstream metrics, their metric definitions, observation age and data coverage.

Do not scrape personal analytics or personal audience information from a platform without approved permission. Do not store authorization tokens in Git; no third-party analytics is proof of first-party outcomes.

## Owner-exported audience activity (`calendar-plan --audience-activity FILE`)

When the owner can see hour-of-week audience activity in a native dashboard (for example "followers online" or "most active times"), ask them to copy it into ReachWeaver's audience-activity CSV. The columns are `account_id,timezone,weekday,hour,activity,source,as_of`, with optional `consent,notes`. This layout is **ReachWeaver's own interchange format**: no real platform export has been verified against it, so never say a platform exports this file. The bundled example `examples/audience-activity.example.csv` is synthetic and is never evidence for a real account.

- The owner fills in the file. Do not scrape dashboards or ask for credentials to fetch the numbers.
- Leave hours the dashboard does not show out of the file, or set them to `unknown`. Never fill them with 0.
- The output tags each slot `evidence=first_party_audience_activity`, `confidence=unverified`, with `provenance` (source, `as_of`, rank, `stale`, DST status). Pass these labels on to the user unchanged. Activity is presence, not outcome, so it is not proof that a time performs better.
- When `stale: true` (`as_of` older than 90 days by default; change with `--audience-max-age-days N`), ask for a fresh export before relying on the slots. `--audience-top-hours N` (1-24, default 5) sets how many top hours become slots.
- Use either `--audience-activity` or `--slot`, not both. The planner stays advisory, and approval, `--apply` and publishing gates are unchanged.

## Selection method (transparent baseline, not a trained prediction model)

1. **Eligibility first.** Check account ownership, official native/API capability, format, user authorization, moderation/community policy and embargo. Set `delivery=native_manual`, `delivery=reachweaver_worker` or `delivery=reminder_only`. Reject automatic HN and ordinary Reddit bot promotion. No secret key or browser session scraping.
2. **Create candidates** from own-account follower activity, prior account/format post times or the low-confidence table above. When data absent, rotate two or three reasonable audience-local windows, and state no empirical optimum established.
3. **Normalize clock.** Resolve an actual date in an IANA zone, convert to ISO8601 explicit offset and UTC, and preserve zone in the plan. Validate daylight-saving gaps/folds; never interpret `09:00` as operator-local unless confirmed.
4. **Prioritize goals and fairness.** Prefer proven audience presence, relevant format, creative utility and staffing. Avoid back-to-back posts or aggressive duplication. Do not put a large launch on a national holiday without explicit review. Respect provider maximum advance scheduling horizon.
5. **If comparable history exists:** segment *same* account, platform, format, content purpose and metric definition; compare median/robust rate at matched post ages (+24h, +7d). Do not compare raw views to impressions, different follower sizes, bought promotion to organic or absent metrics to zero. Require meaningful post counts across slots before making an account-specific inference; otherwise output `insufficient_evidence`.
6. **Continue exploring:** Reserve, as a policy suggestion, ~20% of future noncritical posts for another allowed time slot. This 20% is a heuristic **not** a platform ranking factor or statistically optimal exploration rate. For high-value launches, favor staff/community availability and human editorial judgment.
7. **Produce a reason + confidence.** `account_observed` may support a measured recommendation but not causal certainty; `industry_observational` is low; `editorial_hypothesis` is tentative; `unknown` is explicitly unknown.
8. **Separate schedule from approval.** Present the exact per-account content and publication time for review; only call `reachweaver approve` / `schedule` once the human has explicitly approved the item. Do not auto-edit an approved post; cancel/unapprove and recreate if content changed. Native UI scheduling needs separate human confirmation/receipt.
9. **Measure and revisit.** At the same post ages collect native metrics and downstream goal; show source, missingness and sample sizes. Weekly evaluate slots; monthly refresh platform docs/prior assumptions. Report confounders such as holiday, format, topic, link and audience composition. Never claim causal uplift solely from observational before/after.

## Calendar deliverable schema

Return a visible scheduling table, plus CSV/JSON on request, with at least:

| Field | Meaning |
|---|---|
| `brand_id`, `account_id` | Authorized identity / precise destination |
| `platform`, `post_id`, `format` | Channel, immutable draft link and asset type |
| `audience_zone`, `scheduled_local`, `scheduled_utc` | Zoned exact date/time; DST-safe |
| `time_evidence`, `evidence_checked_at`, `confidence` | First-party/industry/editorial source and uncertainty |
| `delivery` | `native_manual` / `reachweaver_worker` / `reminder_only` |
| `schedule_status` | `proposed` / `awaiting_approval` / `native_handoff_pending` / `locally_scheduled` / `remote_confirmed` |
| `receipt`, `next_measure_at` | Real remote queue receipt when supported; next matched-age metrics checkpoint |

Example illustrative row (NOT proof of user account, a published post, or validated optimal time):

`YazSes | personal-linkedin | linkedin | YS-202610 | text | Europe/Rome | 2026-10-14T16:00:00+02:00 | 2026-10-14T14:00:00Z | industry_observational | 2026-10-08 | low | native_manual | native_handoff_pending | none | +24h/+7d`

Never fabricate IDs, upload URLs, queue receipts or evidence. If `YazSes` is not an authorized brand profile, use an explicitly illustrative example instead of a real saved campaign.

## Batch draft → review → schedule → publish → measure

For a user who writes 10 posts today, keep all 10 drafts; adapt them to each platform, build a collision-aware calendar, show a single review table, then submit each human-approved exact variant to its allowed transport.

For native-only platforms, the skill can prepare copy and instructions and open a human handoff, but it must **not** report `scheduled` until there is evidence from the platform UI/provider. For the three ReachWeaver mock-tested text adapters, queue locally with `reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00`. This is never proof a worker will run. Show `reachweaver publish` dry-run; live publishing remains disabled without explicit authorization, host, credentials and tested adapter.

For YouTube, prefer title/thumbnail/retention and audience interest; an exact time may help **early viewers** but does not establish **long-term algorithm advantage** per official guidance. For Hacker News, do not auto-submit or place model-written submission content.

## Executable offline planner (current implementation)

The portable skill includes the same `reachweaver calendar-plan` command as the main repository. First `reachweaver show ID` and approve each exact platform/account variant. Use `calendar-plan --account ACCOUNT --timezone IANA_ZONE --start YYYY-MM-DD --days 14` to get evidence-labeled, collision-aware **proposals**, not remote schedules. Add `--slot 'Wed@16:00'` to supply your account's observed or hypothesized audience windows; the CLI labels them `operator_supplied/unverified`. Optional `--blackout YYYY-MM-DD` avoids dates; `--spacing-hours 20` sets account-specific spacing. For previewing drafts, add `--include-drafts` (never combinable with `--apply`). To reserve a capped share of the batch for testing new times, add `--explore-share 0.25 --explore-slot 'Sat@11:00'` (share above 0, at most 0.5; default 0 = off): floor(share x posts) posts, evenly interleaved by post order, go to the explore slots and are labelled `role: explore`, `evidence: operator_explore_hypothesis`, `confidence: unverified`; the rest stay `role: exploit` on the evidence-backed slots. The split is fixed, never randomized, so say "exploration", not "experiment"; spacing, blackouts and `--apply` gating are unchanged. The `exploration.baseline_eval_slots` list gives the matching `baseline-eval --slot` roles.

Only after human approvals and review, `--apply` queues times in **local SQLite** for the four existing text-only Bluesky/Mastodon/Telegram/LinkedIn-member adapters. The method rechecks approval and collisions atomically, uses explicit IANA zones/UTC and rejects ambiguous/nonexistent DST times. LinkedIn organization pages, DEV.to, Hashnode, TikTok, Instagram, Facebook, YouTube, X, Threads, Reddit and Pinterest do **not** gain authorized publishing from this change; present a native/manual handoff instead. No cron worker or analytics-trained prediction is activated. The full repository has implementation details in `docs/CALENDAR-PLANNER.md`.

## Evaluate actual imported account outcomes, safely

Run `reachweaver timing-evaluate --account ACCOUNT --timezone IANA_ZONE --source NATIVE_EXPORT_LABEL --metric clicks --denominator impressions --age-hours 168 --tolerance-hours 24 --min-posts-per-slot 3` only when real, owner-permitted native metric snapshots have already been imported. This **read-only** evaluation groups posts by weekday/weekend six-hour windows at matched age, and compares robust median rates within ONE account and declared source. Missing snapshots are **unknown**, not zero. An exploratory candidate requires at least two sample-qualified slots; it remains an observation with uncontrolled topic/format/follower and selection biases. Add `--stratify format` (text/image/video from the stored attachment) or `--stratify campaign` to get the same readout per stratum; a stratum with too few matched posts is `insufficient_data` with no candidate and a `cold_start` pointer to calendar-plan's generic baseline slots (or `none_available` where the planner has no prior). Report that pointer as a low-confidence starting point, never as a winner. Never infer a precise clock-minute from a six-hour median. Never trigger `calendar-plan --apply` or publishing from this result. In the full repo, see `docs/TIMING-EVALUATION.md`; in copied standalone skills use this reference and `references/experimental-learning.md`.

## Compare against the non-personalized baseline

Run `reachweaver baseline-eval --account ACCOUNT --timezone IANA_ZONE --source NATIVE_EXPORT_LABEL --metric clicks --denominator impressions --since YYYY-MM-DD --until YYYY-MM-DD --slot exploit:Tue@09:00 [--slot explore:Sat@11:00] [--campaign-arm explore:CAMPAIGN] [--readout READOUT.json]` to ask whether the account's own slots did better than `calendar-plan`'s generic slot (the one it proposes without `--slot`). Fix the period before looking at results. Each arm is one comparison with a seeded percentile bootstrap interval of the median rate difference and a permutation p-value; Holm–Bonferroni corrects across all comparisons in the run. Status is `insufficient_data` (fewer than `--min-posts`, default 5, rates in either group; estimates are `null`, not 0), `no_clear_difference`, or `separated_after_holm` (interval excludes zero and adjusted p ≤ 1 − confidence). Every arm-versus-baseline result is **correlational**. Report the role (`explore`/`exploit`), the exploration share and the excluded counts. Small organic samples usually give `insufficient_data` or `no_clear_difference`; say so plainly. The command is read-only and never changes the planner, a queue or a post. Platforms without a generic slot (e.g. Mastodon, Telegram) have no baseline. To compare website or repository outcomes instead of a platform rate, save `reachweaver attribution-report --analytics CSV --json > attribution.json` and replace `--source/--metric/--denominator` with `--attribution attribution.json --outcome docs_visits` (attributed sessions) or `--outcome repo_stars_utm` (attributed conversions); a post without an attributed count is excluded, never 0. Add `--attribution-source ga4` to use the GA4 section of a report made with `--ga4-csv` (GA4 sessions, key events as conversions). In the full repo, see `docs/TIMING-EVALUATION.md`.

## Upstream source cadence

Review [official scheduling help](platform-algorithms.md) and the dated full-repo report before each new integration; monthly thereafter. Revalidate horizon, eligible account type, native post types, third-party API permissions, media support and ranking guidance. Store last-reviewed date, not an implication that algorithmic factors are frozen.
