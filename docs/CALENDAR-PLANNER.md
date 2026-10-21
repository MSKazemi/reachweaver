# Calendar planner: batch drafts to safe, audience-local schedule proposals

**Feature:** `reachweaver calendar-plan` (experimental offline planner). This is a deterministic scheduler **proposal** with optional SQLite queueing. It is **not** a trained algorithm, an analytics crawler, a platform-native scheduled-post API, a worker daemon, or an automatic publisher.

Research and source limitations: [platform algorithms and native scheduling](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). Matching Claude Code reference: [timing-intelligence.md](../.claude/skills/reachweaver/references/timing-intelligence.md).

## Safety and state distinctions

| Label | Actual state | Network side effect |
| --- | --- | --- |
| `proposed_only` | Calculated future audience-local and UTC slot | None |
| `locally_scheduled` | Approved draft now has a due UTC time in the persistent local SQLite outbox | None |
| `manual_native_handoff` | A suggested time on a network not implemented in ReachWeaver | None; the user must schedule on the provider |
| Platform-native scheduled | Only after platform UI or provider API returns a real schedule receipt | Native provider scheduling, *not* implied here |
| Published | Only after an authorized live worker returns an external post receipt | Actual publishing; distinct command and user consent |

Do not mistake `calendar-plan --apply` for sending content. It cannot approve drafts, edit their text, contact a platform or publish. Only the four existing **mock-tested text adapters** for Bluesky, Mastodon, Telegram and LinkedIn personal profiles can use `--apply` to queue already-approved drafts. Even these require configured user credentials, a trusted persistent worker, real-account identity verification and a reviewed live pilot before automatic posting. Other networks receive a **manual scheduling handoff**; `--apply` rejects them explicitly.

## 1. Draft and approve exact content

Create or reuse `config/accounts.json` from the ignored local template. All identifiers must refer to accounts the operator controls.

```bash
python3 -m reachweaver doctor
python3 -m reachweaver draft --account my-bluesky --text 'A useful release note and real evidence.'
python3 -m reachweaver show POST_ID
python3 -m reachweaver check POST_ID
python3 -m reachweaver approve POST_ID
```

Repeat for each distinct, platform-adapted post. A different exact message or account needs its own approval. Use `calendar-plan --include-drafts` only to preview unapproved work.

## 2. Propose without changing a queue

```bash
# Replace the dates with a future window; this is a worked example.
python3 -m reachweaver calendar-plan \
  --account my-bluesky --timezone Europe/Rome \
  --start 2026-10-15 --days 14
```

The output is JSON with the post ID, account, platform, proposed local timestamp with offset, UTC timestamp, evidence tier, confidence, queue horizon info and delivery path. An industry-prior slot has `evidence=industry_observational` and `confidence=low`: **not** a guarantee or a slot measured on your specific account. `timezone` is the intended **audience**, not necessarily the creator's timezone; the converter checks daylight-saving ambiguity instead of silently altering the intended time.

Optionally select exact posts, blackout dates and account-supplied viewing windows:

```bash
python3 -m reachweaver calendar-plan \
  --account my-bluesky --timezone America/New_York \
  --start 2026-10-15 --days 21 --spacing-hours 24 \
  --post-id POST_ID_A --post-id POST_ID_B \
  --blackout 2026-10-18 \
  --slot 'Mon@10:00' --slot 'Wed@16:00' --slot 'Fri@09:00'
```

Custom slots are marked `operator_supplied/unverified` because a time typed by a human cannot itself prove official account analytics. Set slots from permitted native audience-insights reports, or test hypotheses across multiple weeks. Platforms with no defensible population prior (YouTube, Reddit, Mastodon, Telegram, Pinterest, etc.) **require** `--slot`, rather than getting fabricated “optimal” times.

The planner prevents collisions with other locally scheduled posts for the same account and rejects insufficient horizon, invalid timezone/date, duplicate slot/post ID and DST non-existent/ambiguous hours. Default minimum spacing is 20 hours per account, changeable with `--spacing-hours` (1–168). It proposes a fixed simple window per platform; no hidden ranking formula is claimed.

### 2b. Plan from your own audience-activity export (`--audience-activity`, issue #16)

If a native analytics dashboard shows you hour-of-week audience activity (for example "followers online" or "most active times"), copy those numbers into a CSV and let the planner take its candidate slots from your top activity hours:

```bash
python3 -m reachweaver calendar-plan \
  --account my-bluesky --timezone Europe/Rome \
  --start 2026-10-15 --days 14 \
  --audience-activity private/audience-activity.csv \
  [--audience-top-hours 5] [--audience-max-age-days 90]
```

**The CSV layout is ReachWeaver's own interchange format** (`reachweaver.audience-activity.v1`). No real platform export has been verified against it, so you fill it in yourself from a dashboard you are entitled to see. ReachWeaver never fetches, scrapes or checks these numbers. [examples/audience-activity.example.csv](../examples/audience-activity.example.csv) contains **synthetic** data. Its `source` and `notes` say so, and the loader flags any account whose `source`, `consent` or `notes` contains the word `synthetic`.

| Column | Required | Rule |
| --- | --- | --- |
| `account_id` | yes | A configured account ID (`^[a-z][a-z0-9-]{1,62}$`). The file may hold several accounts. The planner uses only the `--account` rows. |
| `timezone` | yes | The IANA zone the dashboard hours are in (checked with `zoneinfo`). One zone per account. |
| `weekday` | yes | `0`–`6` (0 = Monday) or `Mon`..`Sun` / `Monday`..`Sunday`. |
| `hour` | yes | `0`–`23`, the start of the hour bucket. |
| `activity` | yes | A non-negative number, or empty / `unknown` / `n/a`. An unknown value stays missing and is never set to 0. |
| `source` | yes | Free text naming the export, e.g. `Instagram Insights - Most active times`. One source per account. |
| `as_of` | yes | ISO date of the export (`YYYY-MM-DD`), not in the future. One `as_of` per account. |
| `consent` | no | Free text describing your right to use the data, echoed into provenance. |
| `notes` | no | Free text. It is only scanned for `synthetic` and never copied into the output. |

The loader rejects the file on the first problem it finds, naming the line: a missing or unknown column, a duplicate (account, weekday, hour) row, mixed timezone, source or `as_of` for one account, a negative, non-finite or non-numeric activity, a file over 1 MiB or 10,000 rows, non-UTF-8 or NUL bytes, control characters, and **spreadsheet formula injection**. A text cell starting with `=`, `+`, `-`, `@`, tab, CR, LF or their full-width forms is rejected rather than neutralised, following [OWASP CSV Injection](https://community.owasp.org/attacks/CSV_Injection) (accessed 2026-10-09). Secrets and e-mail addresses in `source`/`consent` are redacted in the output.

**How slots are chosen.** Only hours with a known activity above zero are ranked, by activity (ties broken by weekday, then hour). The top `--audience-top-hours` (1–24, default 5) become weekly candidate slots. The planner then fills posts into the earliest collision-free candidates, the same way it uses `--slot`, so rank does not decide order within the window. Each entry gets `evidence=first_party_audience_activity`, `confidence=unverified` and a `provenance` object with the source, `as_of`, age in days, `stale`, the activity weekday/hour/value/rank, the UTC hour of week, the DST status, `synthetic`, `consent` and `verified_by_reachweaver: false`. A top-level `audience_activity` object lists the candidate slots, coverage (rows, known, unknown and missing hours out of 168), skipped DST occurrences and warnings. `--audience-activity` cannot be combined with `--slot`.

**Staleness.** When `as_of` is more than `--audience-max-age-days` (default 90) days old, every slot is marked `stale: true` and a warning asks for a fresh export. Old data is not refused, only flagged.

**DST approximation.** Each row is also normalised to a UTC hour of week (0 = Monday 00:00 UTC) using the UTC offsets of the Monday-to-Sunday week that contains `as_of`. In that week, a local hour that does not exist (spring-forward gap) has no UTC hour and is not ranked, and an ambiguous local hour (fall-back fold) maps to its first occurrence (`fold=0`). If `--timezone` is the CSV zone, slots keep the dashboard's wall-clock hour in every week. If it differs, slots are converted through the `as_of` week's offsets, so they can be off by an hour in weeks where only one of the two zones has changed DST, and a warning says so. When `--timezone` differs from the CSV zone and the `as_of` week contains a fall-back in the planner zone, two activity hours can convert to the same local slot (for a UTC CSV with `as_of` 2026-10-21, Sunday 00:00 and 01:00 UTC are both 02:00 in Europe/Rome). The higher-ranked hour keeps the slot, a warning names the dropped hour, and the next-ranked hour fills its place; `provenance.rank` keeps the activity rank, so it may skip a number. When a candidate falls on a DST gap or fold inside the planning window, that one occurrence is skipped and listed in `dst_skipped_local`. With `--slot`, the same case is an error.

Activity measures when your audience is present. It does not measure outcomes, so it does not show that a time performs better. Check any pattern with `timing-evaluate` and `baseline-eval` ([TIMING-EVALUATION.md](TIMING-EVALUATION.md)). Without `--audience-activity`, the planner's output is byte-for-byte the same as before (regression test `tests/test_audience.py`). With or without it, the planner only proposes: `--apply` still needs individually approved posts and goes through every existing check.

### 2c. Reserve a capped share for exploration (`--explore-share`, issue #16)

Planning only on the slots that look best so far never tests anything else. `--explore-share` sets aside a fixed share of the batch for times you want to try, and labels every entry so you can evaluate the two groups later:

```bash
python3 -m reachweaver calendar-plan \
  --account my-bluesky --timezone Europe/Rome \
  --start 2026-10-15 --days 14 \
  --explore-share 0.25 --explore-slot 'Sat@11:00' [--explore-slot 'Sun@10:00']
```

- **Share and cap.** `FRACTION` must be above 0 and at most 0.5 (`calendar.EXPLORE_SHARE_CAP`). The default 0 turns the feature off, and the output is then byte-for-byte what it was before (golden test `tests/test_calendar.py::ExplorationShareTests`). Exactly floor(share x selected posts) posts explore: post *i* (in the planner's post order) explores when floor((*i*+1) x share) > floor(*i* x share), which spreads them evenly. A batch too small for one explore post at that share gets none, and a note says so.
- **Exploit slots** are the ones the planner would use anyway: the generic prior, your `--slot` times or your `--audience-activity` hours, with their usual evidence and provenance. **Explore slots** are only the `--explore-slot` times you give; the planner invents none, refuses an explore slot that repeats an exploit slot, and refuses `--explore-slot` without a share (and a share without a slot).
- **Labels.** Every entry gets `role: exploit` or `role: explore`. Explore entries carry `evidence: operator_explore_hypothesis`, `confidence: unverified` and no `evidence_url`. A top-level `exploration` block reports the share, the cap, the counts per role, both slot lists, `assignment: deterministic_interleave`, `randomized: false` and `baseline_eval_slots`: the `baseline-eval --slot` values for a later readout. Generic-prior exploit slots are left out of that list because they are `baseline-eval`'s own baseline, and `baseline-eval` refuses a `--slot` within its tolerance of the generic slot, so choose explore times away from it.
- **Not randomized.** Which post explores depends only on its position in the batch, so explore-versus-exploit outcomes are associations, not an experiment. Use `experiment-preregister` with `assignment: randomized` for that.
- **Unchanged safety.** Spacing, blackout dates, DST checks, collision checks and the `--limit` cap apply to both roles. The planner still only proposes: `--apply` needs individually approved posts and goes through every existing check, and nothing is published.

For TikTok, a native scheduling eligibility hint flags proposals beyond the documented ten-day queue horizon. This hint does not indicate the account actually has that capability; recheck its current official UI before scheduling. Some formats and accounts cannot be scheduled natively.

For TikTok the output also carries a `market_notices` entry: since 2026-01-23 a separate US joint venture holds TikTok's US recommendation algorithm, which it announced it will retrain, test and update on US user data ([announcement](https://newsroom.tiktok.com/announcement-from-the-new-tiktok-usds-joint-venture-llc?lang=en), checked 2026-10-21). Pass `--market tiktok@US` (or `--market US`) when your audience is in the US, `--market global` otherwise; without it the notice says the market was not stated. The candidate windows above come from general sources, so re-verify them with your own US audience data. The notice changes no slot. See [platform risk](PLATFORM-RISK.md#tiktok-us-and-global-advice---market).

## 3. Explicitly apply for the existing three local text adapters

Once every selected post is individually approved **and the proposal has been reviewed**:

```bash
python3 -m reachweaver calendar-plan \
  --account my-bluesky --timezone Europe/Rome \
  --start 2026-10-15 --days 14 --apply
python3 -m reachweaver list --status scheduled
python3 -m reachweaver publish       # DRY RUN ONLY: shows posts that are due
```

`--apply` atomically revalidates approval/account/platform and each post's approval payload hash (a post changed after approval, or approved before the hash binding existed, is refused and nothing is scheduled; re-approve it with `unapprove`, `show`, `approve`), ensures every UTC time remains at least five minutes in the future, checks emergency pause and newly created collisions, then schedules all proposed posts together or **none**. It never starts a worker, enables `REACHWEAVER_ALLOW_LIVE`, approves unsent content or bypasses the publishing gates. Running a cron on an *authorized trusted machine* is a separate operational decision documented in [architecture](ARCHITECTURE.md).

For LinkedIn, DEV.to, Hashnode, YouTube, TikTok, Threads, X, Meta, Reddit, Pinterest and others currently without a live adapter, copy the approved native content, select the proposed slot in the platform's own scheduling UI **where allowed**, and independently confirm the actual scheduled queue. Do not mark it remotely scheduled without a receipt.

## 3b. Optional unattended local worker after supervised pilot

The planner never activates a scheduler. On a trusted persistent host, owner-approved due posts can be picked up by an explicitly installed worker. The recommended **scheduled-only** mode protects drafts that are approved but not assigned a time, and skips posts that missed the chosen time window by more than 180 minutes. Separate per-account guards allow two attempts/rolling 24 hours and one hour between attempts by default. Run `reachweaver queue-health` locally to inspect overdue/unknown state; do **not** auto-retry. See the [worker operations runbook](WORKER-OPERATIONS.md) and disabled systemd examples. No real accounts are connected by this code.

## 4. Observe and improve rather than inventing results

Track the platform-native metric definition, account, format, timezone, cohort and post age (+24h, +7d). Use `reachweaver import-metrics`, `report` and `experiment-evaluate` for approved source-labeled snapshots. The current planner deliberately does **not** infer causal best-time uplift from a few unpaired views. Its generic slots (the ones proposed without `--slot`, exposed read-only as `calendar.generic_baseline_slots`) are the non-personalized baseline that `baseline-eval` compares your own slots and variant campaigns against over an explicit `--since/--until` period, with a seeded bootstrap interval, Holm–Bonferroni correction and `insufficient_data` below a minimum sample ([TIMING-EVALUATION.md](TIMING-EVALUATION.md#evaluate-against-the-non-personalized-baseline-baseline-eval)). That report never changes the planner. Owner-exported activity heatmaps can now seed candidate slots (`--audience-activity`, section 2b), but nothing feeds results back automatically. Future adaptive optimization still needs format segmentation and held-out exploration.

**Your measured blocks, next to the proposed slots.** A proposal without `--apply` gets a `your_results` key when at least one of your published posts on this account has a result at the matched age. The key holds your results by 6-hour block and by weekday in `--timezone`, using the `insights` method: interactions at about 7 days, n and median against the rest, and `observation` or `too little data`. It also lists `better_blocks` and `better_weekdays`, which are blocks and days with an `observation` above the rest. `fixed_slots` names the block that each proposed slot (for example the illustrative `Mon@18:00`) falls in, with your own result for that block. The entries are not changed. They keep the planner's slots, `your_results` is never used to pick a time, and the `--apply` output and its write path are exactly as before. To try a block that did better for you, pass it as `--slot` or `--explore-slot` yourself. See [INSIGHTS.md](INSIGHTS.md#where-your-results-are-used).

Existing publishing safety remains unchanged: dry-run default, no automated unknown-success retries, emergency pause, exact per-post consent, and real remote receipts. This is a safe scheduler foundation, not the full multi-network hands-free system.

## 4b. Find gaps in what is already queued: `calendar-gaps`

```bash
python3 -m reachweaver calendar-gaps --account my-bluesky --timezone Europe/Rome --days 14
python3 -m reachweaver calendar-gaps --account my-bluesky --timezone Europe/Rome --start 2026-10-15 --days 14 --json \
  --timing-source native-export --timing-metric clicks --timing-denominator impressions
```

Read-only (issue #9). It takes the same candidate slots as `calendar-plan` (`--slot`, `--audience-activity`, or the generic baseline) and compares them with the posts in `scheduled` status:

| Output | Meaning |
|---|---|
| `filled` / `empty` / `past` | A queued post within `--tolerance-minutes` (default 90) fills a slot; a slot already gone is `past`, never `empty`. |
| `overloaded_days` | More than `--max-per-day` (default 2) queued posts on one audience-local day. |
| `close_pairs` | Two queued posts closer than `--min-spacing-hours` (default 20, the planner's spacing). |
| `off_slot_post_ids` | Queued posts in the window that are not near any candidate slot. |
| `timing_evidence` | With the three `--timing-*` flags, an empty slot shows your own matched-age result for that part of the day. It is an observational association, not a best-time proof. |

It uses a read-only database connection (a missing database is an error, nothing is created or migrated), makes no network request and cannot approve, schedule or publish. Posts queued in a platform's native scheduler, or created outside ReachWeaver, is invisible to it, so an "empty" slot is a prompt to check, not a statement that nothing will go out.

## Verification

Regression suite: `python3 -m unittest discover -s tests -v`. Critical cases cover audience-local/UTC conversion, DST gap/fold, audience-activity CSV validation, injection, staleness, provenance and the no-flag golden output (`tests/test_audience.py`), blackout dates, account mismatch, manual-only apply rejection, draft approval enforcement, insufficient slots, queue collision, stale plan and atomic rollback, and emergency stop. The portable skill mirror is synchronized with `python3 scripts/sync_skill.py` and checked in CI.
