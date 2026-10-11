# Field pilot protocol: one owned project, two authorized networks, three workflows

**Status (2026-10-08):** This is a **preregistration template and protocol**, the offline preparation slice of [issue #27](https://github.com/MSKazemi/reachweaver/issues/27). **No pilot has been run.** No account is connected, no post has been published and there are no pilot results. Every threshold cell below is intentionally empty: the owner fills it in and freezes it **before** the first pilot post. The measurement tool is `reachweaver pilot-report` (offline, described in [section 8](#8-measurement-tool-pilot-report)).

The question the pilot answers is narrow: **for one owner, one public project and two networks, does the ReachWeaver workflow (approval-first, source-aware) produce publishing that is at least as accurate, less error-prone and cheaper in operator time than a manual workflow and a conventional scheduler, and does it produce real downstream outcomes?** It is not a test of virality, and it cannot show that any workflow *causes* more reach.

## 1. The success rule

**Nothing is declared a success because an API request returned 200.** The pilot counts only first-party evidence:

| Claim | Evidence that counts | Evidence that does **not** count |
|---|---|---|
| A post exists | The owner opens the native post URL in the official app and confirms the exact text and account. ReachWeaver records it as `published` with a verified receipt, or the owner runs `reconcile --published --remote-url ...` after checking. | An HTTP 2xx, a Postiz `postId`, a scheduler "queued"/"sent" badge, a `published` status nobody checked, a screenshot of a draft |
| A post was seen | Native analytics export (or the platform's own counter) with its definition, timestamp and post age | Follower count multiplied by anything, industry reach benchmarks |
| A post drove a visit | `attribution-report` row in `attributed.posts`: exact `utm_content`, `utm_source`, `utm_medium=social`, `utm_campaign` on a published post | `referrer_only` rows (domain level), GitHub traffic totals, "traffic went up that week" |
| A downstream outcome | A conversion event the owner defined before the pilot, recorded by the owner's own analytics on a UTM-attributed session | A star or visit that happened in the same week but cannot be joined to a tagged link |

A missing value is **unknown**, never zero. A pilot that ends with unknowns is reported with those unknowns.

## 2. Scope, consent and stop conditions

### Scope

- **One owned public project** (a repository whose releases the owner publishes). Record its URL, its default branch and the release(s) used as sources.
- **Two authorized networks**, the same two in every arm. ReachWeaver only has (mock-tested) live text adapters for Bluesky, Mastodon, Telegram and LinkedIn personal profiles ([PLATFORMS.md](PLATFORMS.md); updated 2026-10-09 after issue #14), so the two networks must come from that list for the ReachWeaver arm to be a real arm. A network without an adapter can only test the manual-export path and does not belong in this pilot.
- **One account per network**, owned by the owner. No client, employer, community or third-party account.

### Consent (all recorded in the private pilot record before the first post)

1. The owner confirms in writing that they own or administer each account and the project.
2. The owner has read the current terms of service and API/automation policy of each network, with the date read.
3. The owner signs in, completes MFA and creates credentials personally ([FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md), [ACCOUNT-ONBOARDING.md](ACCOUNT-ONBOARDING.md)). Credentials never pass through Claude, chat, an issue, a PR or a screenshot.
4. Every post in every arm is individually approved by the owner as exact text and destination. The pilot plan is **not** consent to publish any post.
5. If audience members are quoted or analysed (for example in replies), only aggregate, non-identifying counts are kept.
6. The owner may withdraw at any time. Withdrawal ends the pilot. The data collected so far is reported, not discarded.

### Stop conditions

Run `reachweaver pause` immediately, stop all three arms and write an incident note when any of these occurs:

- a post goes to the wrong account, or any external write was not approved by the owner;
- a credential appears anywhere outside the owner's secret store (run `credentials-audit`);
- a duplicate post is published silently (not caught before publishing);
- a published post contains a factual error that needs a public correction;
- a platform warns, rate-limits, restricts or sanctions an account;
- the owner withdraws consent, or a network changes its policy in a way that affects automation.

A stop is a result. It is recorded with the date, the arm, what happened and how it was resolved. Restarting needs a new, explicit owner decision recorded in the pilot record.

## 3. Design

### Arms

| Arm | `arm` value | What the operator does | What stays the same |
|---|---|---|---|
| (a) Manual workflow | `manual` | Writes each post by hand from the release notes, posts it in the official app or web UI, copies the link, exports metrics by hand | Same project, sources, networks, accounts, UTM links, metric exports, audit |
| (b) Conventional scheduler | `scheduler` | Writes each post by hand, queues it in a scheduling tool the owner is already authorized to use (a platform's native scheduler where one exists, or a third-party scheduler). Records the tool name and version. ReachWeaver has no integration with it | as above |
| (c) ReachWeaver | `reachweaver` | Follows the [release playbook](RELEASE-PLAYBOOK.md): `sources-scan` → campaign file → `claims-check` → `campaign-stage` → `show`/`approve` → `calendar-plan` → supervised `publish --live --post-id` → `reconcile` | as above |

All arms use **the same UTM scheme**: every link is built with `reachweaver utm-link URL --platform P --campaign C --content ID` so that `attribution-report` treats every arm alike. In the manual and scheduler arms the operator still generates the link with `utm-link` (an offline command, which publishes nothing) and pastes it, so measurement is not part of the treatment.

### Schedule

1. **Baseline period.** At least two ISO weeks of the manual workflow *before* any arm starts, for example on routine project updates. It establishes the operator's normal time per post, error rate and the accounts' typical outcomes. Baseline rows use `arm=manual` in a separate log file (or a separate date range declared in the preregistration) so that they are not mixed with the manual arm.
2. **Arm blocks.** Each ISO week belongs to exactly one arm. Arms rotate in a counterbalanced order fixed in advance, for example `manual, scheduler, reachweaver, reachweaver, scheduler, manual`, so that operator learning and account growth do not all favour the arm that runs last. Each arm needs at least `min_weeks` ISO weeks (the `pilot-report` default is 2); more is better.
3. **Source assignment.** The list of releases or updates to announce is fixed before the pilot and assigned to weeks by the rotation, not chosen per arm. The operator must not save the most newsworthy release for one arm.
4. **Volume cap.** The same posts-per-week ceiling applies to every arm and stays inside ReachWeaver's live defaults (two attempts per account per rolling 24 hours, 60 minutes apart). The cap is a preregistered value.

### Account age and exposure context

Record for each account at pilot start and at every arm boundary, in the private pilot record:

- account creation date and age in days on the pilot start date;
- follower count (or subscriber count) with the date read;
- number of posts in the 90 days before the pilot;
- any paid promotion, boosts or ads (the pilot uses none; record it if it happens);
- outside exposure: the project mentioned by someone else, a conference talk, a newsletter, a launch elsewhere, a holiday, a platform outage or a known ranking change.

These do not enter the CSV log. They are covariates a reader needs to judge the comparison.

### Outcome windows

Fixed before the pilot and identical for every arm:

| Measure | Window |
|---|---|
| Native post metrics | Snapshots at about +24 h and +7 d after the native publication time, imported with `import-metrics` and a consistent `source` label |
| UTM sessions and conversions | Sessions whose date falls within 7 days after the post (or the preregistered window), from one analytics tool for the whole pilot |
| GitHub traffic | Save `traffic/views` and `traffic/popular/referrers` **at least weekly**, because the API keeps only the last 14 days ([ATTRIBUTION.md](ATTRIBUTION.md)) |
| Stars | Repository-level counts by date. Stars are never credited to a post (see below) |
| Unresolved errors | Every `needs_review` (or scheduler failure, or manual mis-post) is followed until resolved; its resolution time is the elapsed time from detection to resolution |

## 4. Measures and how each is recorded

The CSV log ([section 8](#8-measurement-tool-pilot-report)) has one row per unit of work. The operator fills it in the same day. **A blank cell means unknown**; write `0` only when the value is known to be zero.

| Measure | CSV column | Definition | Source of truth |
|---|---|---|---|
| Time spent | `minutes_spent` | Wall-clock operator minutes on this task, from a timer started and stopped for the task. Include research, drafting, review, approval, scheduling, publishing, checking the native post, reconciliation and metric export | Operator timesheet (a timer, not a later estimate) |
| Posts published | `posts_published` | Posts confirmed in the native app under the success rule in section 1 | Native post URL |
| Duplicated posts | `duplicates` | Published posts that repeat an earlier pilot post on the same account (same item posted twice by a retry, or near-identical text and link). Never more than `posts_published` | Native account history; for ReachWeaver also the event log (`data-export`) |
| Unrecoverable errors | `unrecoverable_errors` | Outcomes the workflow could not resolve by itself: a ReachWeaver `needs_review` (see `queue-health`), a scheduler post marked failed or unknown, a manual post that had to be deleted or reposted. Log the reconciliation work as its own row with `task=reconcile` so that its minutes show the resolution cost | `queue-health`, scheduler logs, native account |
| User interventions | `interventions` | **Unplanned** human actions: re-authenticating, fixing a config, editing a failed post, a manual reconcile, a support lookup. Planned approval gates are not interventions; count them separately in the pilot record | Operator log |
| Factual errors | `factual_errors_found` | Factual errors found in **published** posts by the post-publication audit (below) | Audit sheet |
| Clicks | `clicks` | UTM-attributed landing sessions for the posts in this row, from `attribution-report` (`attributed.posts[].sessions`). Use this definition for every arm. Do not substitute platform "link clicks" in one arm only | `attribution-report --json` |
| Impressions | `impressions` | Native impressions **only where the platform exports them** with a documented definition. Bluesky and Mastodon do not expose view or impression counts ([ATTRIBUTION.md](ATTRIBUTION.md)): leave the cell blank, and the click-through rate stays unknown for those rows | Native export |
| Downstream conversions | `downstream_conversions` | Conversions on UTM-attributed sessions, for one conversion event defined before the pilot (for example: a docs page beyond the landing page, an install-guide visit or an outbound click to the repository's star button) | `attribution-report` (`conversions`) |

### Factual accuracy

Two instruments, both applied to **every arm** so the measurement does not favour ReachWeaver:

1. **Lexical grounding.** Put each arm's post texts into a campaign file with the same evidence list and run `reachweaver claims-check CAMPAIGN --brand BRAND --json`. Record the error and review findings per post in the private audit sheet. This is lexical grounding, not truth verification ([CLAIM-GROUNDING.md](CLAIM-GROUNDING.md)).
2. **Human audit.** After each arm block, a reviewer who did not write the posts checks every published post against its primary source and lists each wrong number, version, date, feature or overstatement. Where possible the reviewer is not told which arm produced the post. The count goes into `factual_errors_found` for the row that published the post.

### Stars and docs visits

GitHub does not tell a repository owner which link a stargazer followed, so **a star is never attributed to a post**. Stars are reported as a repository-level time series (for example from the stargazer timestamps in the GitHub REST starring API, https://docs.github.com/en/rest/activity/starring) next to the arm calendar, with the warning that the series is confounded by everything else in section 6. The `repo_stars_utm` signal in [GROWTH-EXPERIMENTS.md](GROWTH-EXPERIMENTS.md) means a star-intent conversion recorded by the owner's own analytics on a UTM-tagged session, not a GitHub star count. Docs visits count only through UTM-attributed sessions in `attribution-report`.

## 5. Data handling

- Keep the pilot log, audit sheet, analytics exports and GitHub traffic files **outside the public repository**, for example in a private directory under the owner's home. None of them goes into a public issue or PR.
- Exports must be aggregated. No per-user rows, handles, emails or IP addresses.
- Retain the raw native exports for the life of the pilot report so that every number in it can be traced.
- ReachWeaver's local data lifecycle commands apply ([PRIVACY-LIFECYCLE.md](PRIVACY-LIFECYCLE.md)).

## 6. Threats to validity

The pilot is small and observational. A reader must see these next to any number:

- **History and seasonality.** Arms run in different weeks. A holiday, a conference, an outage or a ranking change in one week affects only one arm.
- **Maturation.** Followers and project visibility grow during the pilot, which favours later blocks. Counterbalancing reduces this but does not remove it.
- **Operator learning and fatigue.** The operator gets faster at every workflow over time and may tire of one. The timesheet cannot separate skill from tool.
- **Allegiance bias.** The operator is likely the ReachWeaver developer. Mitigations: a preregistered protocol, a separate reviewer for the factual audit, arm-blind review where possible, and publishing negative results.
- **Content differences.** Different releases are announced in different weeks. Some news is simply more interesting. Fixed source assignment reduces selection by the operator, not the variation between releases.
- **Contamination.** The same audience sees every arm. A strong post in one week can lift the next week's results.
- **Measurement differences.** Ad blockers, consent banners and link shorteners reduce UTM coverage, possibly unevenly. Platforms differ in what they expose. One analytics tool and one UTM scheme for all arms keep the instrument constant, not complete.
- **Small samples.** With a few weeks per arm and a handful of posts, rates move a lot from one post. `pilot-report` reports rates with their numerators and denominators so that small denominators are visible. No significance test is computed and none should be claimed.
- **Multiple comparisons.** Seven rates per arm across three arms will show some differences by chance. The primary measures are fixed in advance (section 7).
- **Self-reported logs.** Minutes, interventions and duplicates are typed by the operator. Keep the timer export and the native account history as evidence.
- **GitHub traffic retention.** Traffic older than 14 days is gone if it was not saved in time.

## 7. Preregistration (fill in and freeze before the first post)

Copy this table into the private pilot record, fill every cell, then record the file's SHA-256 (for example `sha256sum pilot-preregistration.md`) in a commit or with a third-party timestamp before the first pilot post. Editing it later is a protocol deviation and must be reported.

| Item | Value |
|---|---|
| Project URL | |
| Network 1 / account id | |
| Network 2 / account id | |
| Conventional scheduler tool and version | |
| Baseline weeks (ISO weeks) | |
| Arm rotation (ISO week → arm) | |
| `min_weeks` per arm for `pilot-report` | |
| Posts-per-week cap (every arm) | |
| Source list and week assignment | |
| Native snapshot ages | |
| UTM attribution window (days) | |
| Analytics tool (one for the whole pilot) | |
| Conversion event definition | |
| Primary measures (at most three) | |
| Factual-audit reviewer | |
| Continue threshold: minutes per post, ReachWeaver vs manual | |
| Continue threshold: minimum coverage for clicks and conversions | |
| Maximum `needs_review` resolution time | |
| Deviations log location | |

### Decision criteria for continuing

Decided after the last arm block, from `pilot-report`, the audit sheet and the incident log:

1. **Stop** (do not continue ReachWeaver toward a release claim) if any safety KPI is non-zero for the ReachWeaver arm: unauthorized external writes, wrong-account posts, leaked secrets or silent duplicate sends ([PRODUCT.md](PRODUCT.md) section 4). Fix the cause, then run a new pilot.
2. **Stop** if a `needs_review` stayed unresolved without evidence, or took longer than the preregistered maximum to resolve.
3. **Extend, do not conclude,** if `pilot-report` refuses the comparison (an arm has fewer than `min_weeks` weeks) or if coverage for the primary measures is below the preregistered minimum.
4. **Modify** if the ReachWeaver arm shows more published factual errors or more duplicates than the manual arm, or needs more unplanned interventions per post than the preregistered threshold allows. Report it as a negative result and name the step that caused it.
5. **Continue** only when none of 1–4 applies **and** the preregistered minutes-per-post threshold is met. Even then, the conclusion is written as a descriptive observation for this owner, project and two networks ("in this pilot, the ReachWeaver arm used X minutes per post against Y in the manual arm, over N posts"), never as a general or causal claim.

## 8. Measurement tool: `pilot-report`

```bash
cp examples/pilot-log.template.csv ~/pilot-private/pilot-log.csv   # header only; fill one row per task
reachweaver pilot-report --log ~/pilot-private/pilot-log.csv            # text summary
reachweaver pilot-report --log ~/pilot-private/pilot-log.csv --json     # full machine-readable report
reachweaver pilot-report --log ~/pilot-private/pilot-log.csv --min-weeks 3
```

Columns: `date` (YYYY-MM-DD), `arm` (`manual`, `scheduler` or `reachweaver`), `task` (short label such as `draft`, `post`, `reconcile`, `metrics-export`), then the nine measures from section 4. `minutes_spent` may be a decimal; the other measures are nonnegative integers.

What the command does, all offline:

- **Blank = unknown.** Unknown cells are left out of sums and counted per field (`known_rows`, `unknown_rows`, `coverage`). A rate uses only rows where both its numerator and denominator are known, and reports `numerator`, `denominator`, `rows_used` and `rows_excluded_unknown`. A zero denominator gives no rate (`zero_denominator`).
- **Malformed rows are reported, not repaired.** A bad date, an unknown arm, a negative or non-numeric value, a blank task, too many or too few cells, or `duplicates` greater than `posts_published` is listed with its line number and reasons and left out of every total. The exit code is 1 when any row is malformed, so fix the log and rerun. A missing column rejects the file (exit 2). Clicks above impressions is a warning, because it usually means mixed sources.
- **Per-arm output.** Row count, distinct ISO weeks, date range, totals, the rates `minutes_per_post`, `duplicate_rate`, `unrecoverable_error_rate`, `interventions_per_post`, `factual_errors_per_post`, `click_through_rate` and `conversions_per_click`, and minutes by task.
- **Comparison refused on insufficient data.** It needs at least two logged arms, and every logged arm must cover `--min-weeks` distinct ISO weeks (default 2). Otherwise `comparison.status` is `refused` with reasons. An arm with no rows is listed in `arms_not_logged`; record a dropped arm as a protocol deviation.
- **Descriptive label.** A shown comparison is labelled `descriptive_not_causal`, with each arm's rate and its denominator, and the difference from the `manual` arm when that arm is present. There is no winner, significance or lift field.
- **No side effects.** It reads one CSV. It does not open the SQLite outbox, contact a network, or draft, approve, schedule or publish anything.

## 8a. Offline end-to-end test and benchmark-harness skeleton

Two offline pieces prepare the pilot's tooling without contributing any pilot evidence:

- `tests/test_e2e_offline.py` walks the shipped example files through draft, show, check, approve, dry-run, the live-gate refusal and a mock send, with every socket refused and the Bluesky HTTP layer replaced by a recording mock. It also asserts that an unapproved or edited post is never sent. It complements `tests/test_onboarding.py`, which runs the QUICKSTART commands from a fresh copy.
- `python3 scripts/pilot_benchmark.py [--posts N] [--json]` is a harness **skeleton**. It runs the same workflow for N synthetic posts (one throwaway account per post, because the per-account caps are never loosened) against a throwaway database with the mock and reports local CPU seconds per step. Its output is labelled `synthetic` and `not_pilot_evidence`. It measures no operator minutes, no reach, no accuracy and no real account, and it must never be pasted into the pilot report as a result. The pilot's numbers come only from the operations log and `pilot-report`.

Passing either one shows that the offline code path works. It is not a platform receipt and not a pilot result.

## 9. Pilot report outline

The final report, written after the decision, must include every section, including empty ones:

1. Preregistration hash and every deviation from it.
2. Sample size: posts per arm and network, ISO weeks per arm, rows logged, malformed rows.
3. Metric definitions as used (section 4), with any change and its date.
4. Results per arm from `pilot-report --json`, with coverage, numerators and denominators. Unknowns are shown as unknown.
5. Safety KPIs, stop events and incidents.
6. Factual audit: claims-check findings and human audit errors per arm.
7. `needs_review` items and their resolution times.
8. Downstream outcomes: attributed sessions and conversions, the repository star series next to the arm calendar, and GitHub referrer data marked as domain level.
9. Account age and exposure context.
10. Threats to validity that applied, and negative results.
11. Decision (stop, extend, modify or continue) with the criterion that triggered it.

The report contains no projected reach, no "lift" without its denominator and no number that is not traceable to a native export, an `attribution-report` output or the operator log.
