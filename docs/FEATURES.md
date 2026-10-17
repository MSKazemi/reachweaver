# All features, in detail

This page holds the detailed feature guides that used to make the README long. Nothing here is needed to get started: begin with the [Start here guide](START-HERE.md) and the [quickstart](QUICKSTART.md). Come back when you want a specific capability.

Every feature below is **offline and draft-only** unless it says otherwise. None of them publishes anything, and none of them promises views, followers or growth. Words you do not know are explained in the [glossary](GLOSSARY.md).

**Jump to:** [Growth actions and analytics](#evidence-ranked-social-growth-actions-offline-issue-6) · [International channels](#international-social-discovery-research-only-issue-64) · [Profile and post audits](#per-platform-profile-and-post-audits-offline-issue-3) · [Supported operations](#supported-operations) · [Campaigns and experiments](#new-in-v02-source-bound-campaigns-and-evidence-aware-learning) · [Media checks](#media-asset-checks-offline-issue-18-slice) · [Scheduling](#safe-batch-calendar-planning-experimental) · [Choosing networks](#which-social-networks-to-prioritize-for-real-attention) · [DEV Community](#dev-community-build-technical-authority-without-spam)

## Evidence-ranked social growth actions (offline, Issue #6)

`reachweaver suggest` combines **individual profile fixes**, **native post-metric coverage**, **unresolved publishing-safety incidents**, **owner-exported engagement triage**, **SEO destination conflicts** and **preregistered experiment history** into one evidence-ranked, human-review-only task list. Safety and privacy issues come first. It never guesses account rankings or predicts likes/impressions.

```bash
reachweaver suggest --snapshot examples/profile-post-audit.example.json --json
# Optional: add owner-exported data from an EXISTING private DB; no DB created or modified
reachweaver --db state/reachweaver.sqlite suggest --include-db --snapshot state/owned-profiles.json --limit 15
```

`reachweaver weekly-digest --out state/digest.md` takes the same inputs and writes them as one private Markdown file; nothing is sent. See [the next-actions runbook](NEXT-ACTIONS.md). Real native account analytics need an owner-approved export or a separately authorized collector; `suggest` itself never connects to a platform, changes the queue, drafts, replies, likes, schedules or publishes.

## Consent-aware native social analytics from local CSV (offline)

Profiles and content audits can now incorporate **your own source-labeled native metrics**, without connecting social accounts or modifying databases. Export the metrics you are authorized to use, normalize the columns once, and run:

```bash
reachweaver post-audit --file examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv --owner-authorized --json
reachweaver suggest --snapshot examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv --owner-authorized --json
```

The CSV is read into memory, never published or saved elsewhere; owner acknowledgement is **not** independent API verification. Blank metrics remain unknown and duplicate posts are rejected. See [native CSV import contract](PROFILE-METRICS-CSV.md) and [individual platform audits](PROFILE-AND-POST-AUDITS.md).

## International social discovery (research only, issue #64)

The offline distribution planner catalogs **110 channels across 18 market pilots**, including India-focused ShareChat, Moj, Josh, Chingari, Hipi, Public App, Kutumb, Arattai Channels, LocalCircles, Pratilipi and Rooter; plus Xiaohongshu/REDnote, Weixin Channels, Naver, VK and other regional platforms. See [regional channel research and India use cases](REGIONAL-CHANNELS-2026-10-14.md) and [community, creator and publishing platform research](COMMUNITY-AND-CREATOR-PLATFORMS-2026-10-15.md) and try `reachweaver distribution-plan --region india --goal awareness --limit 12 --json`. New entries are **manual-only** with `unverified` native scheduling; community discussion and opt-in channel messages stay human-only. No new API adapter, account connection or remote publication is implied.

## Per-platform profile and post audits (offline, Issue #3)

**ReachWeaver now audits each network on its own terms.** LinkedIn headlines and featured proof, YouTube channel organization and retention, Instagram original Reels and reach, DEV technical articles, Mastodon profile metadata and verified links, Reddit rules, Product Hunt maker stories, and Chinese developer channels receive **different profile checklists and native content experiments**. Twenty-five platform and community surfaces are described in the [complete audit playbook](PROFILE-AND-POST-AUDITS.md). This is not a universal ranking formula or a guarantee of likes, impressions, views or follows.

```bash
# Pure offline example: no account, API keys, website fetch, local DB or changes.
reachweaver profile-audit --file examples/profile-post-audit.example.json --json
reachweaver post-audit --file examples/profile-post-audit.example.json --age-hours 168 --min-posts 3 --json

# For a REAL audit, replace the example with your OWN owner-confirmed profile
# observations and source-labeled native analytics kept locally under state/.
reachweaver profile-audit --file state/owned-profiles.json --platform linkedin --json
reachweaver post-audit --file state/owned-profiles.json --platform youtube --json

# Peer benchmark from public counters you copied by hand (no fetching, nothing stored; docs/PEER-BENCHMARK.md)
reachweaver peer-benchmark --file examples/peer-benchmark.example.json
```

The profile score is **only an observed checklist-completion heuristic** and is omitted when less than 60% of the platform's fields have been checked. Post rates require compatible format, account, platform, native metric source and observation age; missing data is **unknown**. Inputs include no passwords or cookies and the audit commands never log in, change a profile, draft, schedule, publish, follow or generate votes. Without an actual owner-supplied snapshot, the skill offers a **general playbook**, not an audit of your real accounts. See the [portable skill reference](../.claude/skills/reachweaver/references/profile-post-audit.md).

## Developer attention and contributor growth

For a source-backed, moderation-aware 30-day plan prioritizing DEV, LinkedIn, GitHub-native discovery and Bluesky over indiscriminate cross-posting, see the [open-source developer growth playbook](OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md). It covers first-time-contributor onboarding, platform rules, Reddit/HN recovery, qualified-traffic measurement and the changed Hacktoberfest 2026 rules. It is strategic research, **not** evidence of published posts or promised reach.

**Claude Code growth workflow:** The installed skill now loads [its contributor-growth reference](../.claude/skills/reachweaver/references/open-source-growth.md) when you ask about GitHub visitors/contributors or restricted HN/Reddit promotion. Example: `/reachweaver — Grow first-time contributors for YazSes using DEV, LinkedIn, GitHub and Bluesky over the next 30 days. Keep Hacker News human-only and Reddit community-compliant. Show the measurable checklist; do not publish anything without approval.`

## Worldwide audience selection: executable, offline research

ReachWeaver now includes a **versioned and tested research registry** spanning 100 networks/surfaces and 18 country/global market profiles, including India, China, Japan, Southeast Asia, Africa and Brazil. It also records native scheduling evidence separately from ReachWeaver's actual account and API capabilities. Results are **editorial channel hypotheses**, never guaranteed traffic, a detected account connection, a remote scheduling receipt or publishing permission.

```bash
# No account credentials or network access needed. These NEVER queue or publish.
python3 -m reachweaver.cli distribution-plan --region india --goal adoption --limit 5
python3 -m reachweaver.cli distribution-plan --region china --goal contributors --json
python3 -m reachweaver.cli distribution-plan --region nigeria --goal collaboration --date 2026-10-14 --json
# A worldwide audience has no one local timezone:
python3 -m reachweaver.cli distribution-plan --region global --goal awareness --date 2026-10-14 --timezone Europe/Rome --json
```

Use `--goal contributors|adoption|awareness|collaboration`. Candidate local lunchtime/evening windows are **illustrative tests**, not verified optimal hours; the output includes offset-aware local and UTC timestamps. Hacker News is a special **human-authored, manual-only** channel and must not receive AI-written or AI-edited submission content.

Read [global strategy](GLOBAL-DISTRIBUTION-2026-10-08.md), [operating/governance standard](DISTRIBUTION-GOVERNANCE.md), [platform algorithm and scheduling evidence](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md), [developer contributor playbook](OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md), and the [owner-reported activity inventory](../examples/yazses-channel-audit.example.json). The canonical machine-readable data is `reachweaver/distribution_registry.json`; the complete standalone Claude Code skill bundles that registry and the planner.

## Supported operations

| Capability | v0.1 |
|---|---|
| Strategic research and campaign creation via Claude Code | Router, specialized creative and evidence references + templates |
| Worldwide region/goal channel planner | Offline tested registry, explicit native/API/ReachWeaver status, optional IANA-zone experimental slots; NO posting |
| Distinct content drafts across 12 platforms | Skill instructions + conservative text lint |
| Account inventory / missing-credential check | Local config only; no interactive OAuth UI |
| Authenticated account identity preflight | Explicit online check for Bluesky, Mastodon, Telegram and LinkedIn (`/v2/userinfo` `sub`); mock-tested, no account linked. Checks for Instagram, TikTok, YouTube, Threads and Facebook exist but are refused before any credential read while those adapters are disabled |
| One-post supervised pilot isolation | `show POST_ID`, `publish --post-id POST_ID`, remote identity check before send |
| Approval, scheduling, dry-run | Functional local CLI |
| Safe persistent outbox, kill switch, reconciliation | SQLite-backed local CLI |
| Bluesky / Mastodon / Telegram | Text-only live API adapters, mocked tests; one real text post each verified for Bluesky and Mastodon ([receipts](receipts/)), none for Telegram. Mastodon image attachments (#13) and Bluesky/Mastodon reply sending (#17) are mock-tested but registry-disabled until a live receipt. Bluesky: 300-grapheme check, link/hashtag facets, optional custom PDS `service`, deterministic record key, verified `uri`/`cid` receipt. Mastodon: pinned per-instance `max_characters`, `visibility` (no DMs), optional `language`/`spoiler_text`, verified receipt. See [PLATFORMS.md](PLATFORMS.md#bluesky-and-mastodon-adapter-details-issues-12-13) |
| LinkedIn personal profile (member) | Text-only Posts API adapter with pinned `Linkedin-Version`, mocked tests; organization pages unsupported ([guide](LINKEDIN.md)) |
| Threads, Facebook Page, Instagram, YouTube, TikTok | Mock-tested adapters (Threads text #19, Facebook Page text/link #21, Instagram single image #20, YouTube upload #22, TikTok creator-inbox video #23), owner-approved 2026-10-09 and **disabled** in the capability registry until a supervised live receipt; `manual-export` creator packages meanwhile |
| X, Reddit, Pinterest | Creative/manual workflow only; no adapter |
| Weibo, Xiaohongshu, Douyin, Bilibili, WeChat Official Accounts, Zhihu and six more Chinese platforms | China-aware `manual-export` packages: sourced limits, platform topic syntax, AI-label and ad-label decisions; you post by hand ([CHINA.md](CHINA.md)). No adapter: no open posting API the owner qualifies for |
| Analytics / comparisons | User-supplied snapshot ingestion, read-only polling of public Bluesky/Mastodon counters (`metrics-fetch`), coverage-aware local report and matched-age directional experiment evaluator |
| Campaign validation and brand identity boundaries | Offline evidence/brand JSON checks; immutable idempotent draft staging |
| Private/owner analytics fetching (views, impressions, clicks) or media rendering | Not implemented; `metrics-fetch` reads only public like/repost/reply(/bookmark) counters |
| Cloud scheduler with cross-device durable state | Not implemented; see persistent cron guidance |

## New in v0.2: source-bound campaigns and evidence-aware learning

Instead of letting a model produce untraceable copy and automatically broadcasting it, build a **brand-specific, evidence-registered campaign package**. The offline validator checks required source declarations, private brand-to-account boundaries, unsupported media formats and near-identical cross-channel text. It **cannot** independently establish that a webpage supports the claim; Claude/human editorial verification is still required.

```bash
# Works even before accounts or API credentials are connected
python3 -m reachweaver.cli campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --json
python3 -m reachweaver.cli campaign-export --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --out /tmp/reachweaver-manual-review

# Requires your ignored local config/accounts.json and a reviewed brand account allowlist.
# Repeated calls with an identical manifest return the same draft IDs; changing a staged manifest is rejected.
python3 -m reachweaver.cli campaign-stage --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json

# After human approval + separately authorized real publishing, import native metric snapshots:
python3 -m reachweaver.cli experiment-evaluate --file examples/experiment.example.json
```

**Preregister before publishing:** `experiment-preregister examples/growth-plan.example.json --out FILE` rejects more than 3 arms, multi-dimension or near-duplicate variants, vanity primary metrics (likes, views) and early stopping on a win, and it requires notes acknowledging six confounders. `experiment-verify PLAN --hash H` then shows whether the plan was edited after registration. `experiment-readout --prereg FILE --posts MAP.json` verifies the hash, maps arms to published posts and reads out against the local database without writing to it. Readouts are labeled only `insufficient_data`, `inconclusive` or `directional`, never proven. Each readout states whether the design was owner-declared randomized or only associational. `experiment-simulate` shows, on SIMULATED data, how often each label occurs. See [growth experiments](GROWTH-EXPERIMENTS.md).

The experiment example contains illustrative **post IDs that must be replaced** with actual published posts and recorded native snapshots. Its readout requires matched ages and a predeclared hypothesis, metric, denominator, minimum pairs and guardrail; it never claims randomized causal proof. No proprietary ranking algorithm or guaranteed view count is assumed.

**Claude operating methodology:** identity/voice → audience and source research → evidence-bound campaign → truthful hooks and platform-native drafts → user review → approved outbox → native analytics → next test. The optimized router skill and on-demand references are in `.claude/skills/reachweaver/`; creative/evidence/experiment guides are progressively loaded rather than dumping 106 skills into the agent context. See competitive audit and [agent evaluation cases](../evals/README.md).

## Media asset checks (offline, Issue #18 slice)

`reachweaver media-check media/manifest.json --json` reads real PNG/JPEG/GIF/WebP/MP4/MOV headers. It checks dimensions, duration, size, extension mismatch, MP4/MOV video and audio codec, average frame rate, audio sample rate and channels, `moov`-before-`mdat`, sourced per-platform limits (unknown limits are skipped with a warning, never invented), image alt text, SRT/WebVTT captions for video, license and provenance (`rights_confirmed` must be true), and English-first translations. ReachWeaver **does not upload or generate media**; generation tools are optional, user-configured and need no paid model. See [media assets](MEDIA-ASSETS.md).

`reachweaver media-storyboard --campaign CAMPAIGN.json --out media/storyboard` turns one campaign brief (the campaign contract or a `signal-brief` scaffold) into a vertical-video storyboard (JSON and Markdown), an SRT subtitle skeleton with valid timing, a `media-check` manifest with a LinkedIn image draft and the vertical video, and an image spec per platform. Alt text, captions, subtitle text and scene copy are `{{write: ...}}` placeholders and `rights_confirmed` starts `false`, so `media-check` blocks until a person writes them. It also flags text or focal boxes that a centered crop or a platform's UI overlay would hide. The built-in TikTok, Instagram Reels and YouTube Shorts safe zones come from each platform's first-party **paid-ad** creative specs (accessed 2026-10-09); organic overlays may differ, and every check says so. Supply other sourced numbers with `--safe-zones`. `media-check` also compares translated subtitle tracks cue by cue with the source track (cue count and timing, preserved terms, numbers and URLs).

## First-party timing-outcome analysis (offline, observational)

`timing-evaluate` now compares **already published** posts in one configured account using the operator's existing source-labeled native snapshot imports. It matches post ages and computes median rates by weekday/weekend six-hour audience-local dayparts. Missing values are excluded; an exploratory candidate needs observations in at least two time groups. It never fetches private analytics, changes a queue, guarantees reach or connects/publishes to a social platform.

```bash
reachweaver timing-evaluate --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --age-hours 168 --tolerance-hours 24 --min-posts-per-slot 3 [--stratify format|campaign]
```

`--stratify format` (text/image/video, from the post's stored attachment) or `--stratify campaign` adds the same readout per stratum. A stratum with too little data says `insufficient_data`, names no winner and points to `calendar-plan`'s generic baseline slots instead (`cold_start`); the pooled result does the same when it has too few comparable slots.

`baseline-eval` measures improvement against the **non-personalized baseline**: over a required `--since/--until` period it compares posts at your own `--slot` times (or in a `--campaign-arm` variant campaign) with posts at `calendar-plan`'s generic slot. Each comparison gets a seeded percentile bootstrap interval and a permutation p-value, and Holm–Bonferroni corrects across every comparison in the run, including saved `experiment-readout` files. Results are labeled explore or exploit and split into correlational and (owner-declared) randomized. Too few posts gives `insufficient_data`, never 0. Readout labels and the strict sign-test gate are unchanged.

```bash
reachweaver baseline-eval --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --since 2026-09-01 --until 2026-10-31 --slot exploit:Tue@09:00 --slot explore:Sat@11:00
```

See [timing evaluation and limits](TIMING-EVALUATION.md) and [calendar planner](CALENDAR-PLANNER.md). `native-export` is a user-provided provenance label, not verification that analytics really came from the platform. Use experimental human-reviewed slots; the planner is **not** automatically retrained or scheduled by this result.

## Social algorithms and best-time scheduling research (2026-10-08)

The Claude Code skill now includes [platform algorithm/discovery guidance](../.claude/skills/reachweaver/references/platform-algorithms.md), an [evidence-aware scheduling method](../.claude/skills/reachweaver/references/timing-intelligence.md), and a [dated native-scheduling comparison](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). These distinguish provider scheduling UIs from authorized publishing APIs and the existing local outbox, including a platform-by-platform status matrix, audience-local timing experiments and a reviewable calendar schema.

**Not implemented:** automatic discovery of optimal hours from personal analytics, private platform analytics acquisition (only public Bluesky/Mastodon counters via `metrics-fetch`), a background scheduler, or platform-wide native/API scheduling. Time windows are observational starting points; not guaranteed visibility. ReachWeaver CLI remains approval-first and dry-run by default.

## Safe batch calendar planning (experimental)

ReachWeaver now has a working **offline calendar planner**: after you write and individually approve posts, it can propose audience-local/UTC publication slots with low-confidence industry priors or your own explicitly supplied windows, avoid blackouts and collisions, and provide a reviewable JSON schedule. Only the existing Bluesky/Mastodon/Telegram/LinkedIn-member **text-only** adapters can accept `--apply` for **local SQLite queueing**, never immediate network publication. LinkedIn organization pages, YouTube, TikTok, Meta and other platforms stay **native/manual scheduling handoffs** until an enabled publishing adapter (the mock-tested ones above stay registry-disabled until a live receipt) and an account connection exist.

```bash
# Replace the dates with a future publication window.
reachweaver calendar-plan --account my-bluesky --timezone Europe/Rome --start 2026-10-15 --days 14
# After reviewing exact posts + approving each post, queue due times locally:
reachweaver calendar-plan --account my-bluesky --timezone Europe/Rome --start 2026-10-15 --days 14 --apply
reachweaver publish    # DRY RUN: no post sent, no background worker created
```

For platforms without a useful population-time prior (including Mastodon, Telegram and YouTube), provide account-selected slots such as `--slot 'Tue@10:00' --slot 'Thu@16:00'`. See [calendar planner quickstart](CALENDAR-PLANNER.md) and [algorithm/scheduling evidence](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). This feature **does not** learn an optimal time from connected analytics, automatically obtain credentials, run a daemon or guarantee visibility.

To plan from your own audience data, copy the hour-of-week "most active times" numbers from a native analytics dashboard you can see into ReachWeaver's audience-activity CSV and pass `--audience-activity FILE`. The CSV is **ReachWeaver's own interchange format**: no real platform export has been verified against it, and [examples/audience-activity.example.csv](../examples/audience-activity.example.csv) is synthetic. The top activity hours become candidate slots. Every proposed entry carries provenance (`first_party_audience_activity`, the export's `source` and `as_of`), and the output warns when `as_of` is older than 90 days. Hours you did not export stay missing instead of being treated as zero. Without the flag the output is unchanged. The planner stays advisory: it never approves or schedules anything on its own.

## Guarded scheduled delivery and queue diagnostics (Issue #11 slice)

A guarded live send now has conservative **per-account rolling 24-hour limits (default 2 attempts)** and a **60-minute minimum gap**, both checked atomically in the SQLite outbox. Past uncertain attempts still count even after restart/reconciliation. These are ReachWeaver limits, **not** cost controls.

**Provider rate limits (owner-approved 2026-10-08).** When a provider answers 429, ReachWeaver records a per-account cooldown from its `Retry-After` (or Telegram `retry_after`, Mastodon `X-RateLimit-Reset`, LinkedIn's daily reset, otherwise 15 minutes), clamped to 60 seconds–24 hours. Until it ends, that account's other posts are `SKIPPED` before any request and stay approved. The post that got the 429 goes to `needs_review` and is **never resent automatically**; `dead-letter` labels it `certain_not_sent`, `likely_not_created` or `ambiguous` to guide the human check. Only the read-only identity check before the claim may be retried (twice at most, never Bluesky). `health`, `queue-health` and the dry run show active cooldowns. `config/caps.json` version 2 adds tighten-only per-provider budgets; version 1 files still work. See [provider rate limits](WORKER-OPERATIONS.md#provider-rate-limits-and-cooldowns-issue-11). Since 2026-10-09 (owner-approved), Postiz-backed accounts that declare the same `postiz_workspace` share one 429 cooldown, and Bluesky `connect-check`/publish refuse before any request once the local `createSession` counter reaches half of Bluesky's documented 30 per 5 minutes or 300 per day (`bluesky_create_session_budget_exhausted`); see account onboarding on [shared Postiz workspaces](ACCOUNT-ONBOARDING.md#account-and-environment-separation) and the [identity check](ACCOUNT-ONBOARDING.md#1d-verify-identity-explicit-online-never-posts). All of this is mock-tested; no real provider 429 has been observed. The new offline `queue-health` command reports overdue local schedules, stale sending, `needs_review`, pause state and account attempt totals without connecting to social platforms.

```bash
reachweaver queue-health
reachweaver publish --scheduled-only --max-lateness-minutes 180 # DRY RUN
# Only after the owner's supervised live pilot, authorization and host credential setup:
# REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --scheduled-only --max-lateness-minutes 180 --max-per-24h 2 --min-gap-minutes 60
```

`reachweaver health` gives one offline readiness view per configured account (adapter, registry age, credential variable names, LinkedIn version and authorization expiry, Postiz, queue, rolling budget, pause, live opt-in and posts needing re-approval) and exits 1 when anything is `blocked`.

**Approvals are bound to the exact payload (issue #7).** `approve` stores a SHA-256 of the account, platform, exact text, schedule time, source URL and campaign (binding tag `reachweaver.storage.approval/1`), plus any image attachments (`approval/media-1`), video record (`approval/video-1`) or reply target (#17). A post that changes after approval is never sent: it stays approved/scheduled and is `BLOCKED` before any network call. **Upgrading:** posts approved or scheduled before this release are not grandfathered. `health` lists them as `approval_rebind_required`; re-approve each one with `reachweaver unapprove POST_ID`, `reachweaver show POST_ID`, `reachweaver approve POST_ID` (then `schedule` again). See the [upgrade note](WORKER-OPERATIONS.md#upgrade-note-approval-payload-binding-issue-7). The hash detects unsanctioned edits; it does not prove a human ran `approve` (threat model T7/T8). `reachweaver dead-letter` groups `needs_review` posts by sanitized error code with reconcile steps. An optional `config/caps.json` (see `config/caps.example.json`) sets per-account caps that can only **tighten** the `--max-per-24h`/`--min-gap-minutes` values. Built-in tighten-only pilot caps (`caps.BUILTIN_PROVIDER_CAPS`, owner decisions D19-4 and D21-5, 2026-10-09) apply with or without a caps file: Threads accounts at most 2 sends per rolling 24 hours, Facebook Page accounts at most 2 per 24 hours and at least 60 minutes apart (Postiz-backed ones included), so a looser flag or file value cannot exceed them. A local `config/optout.json` lists handles or domains that are never replied to, mentioned or tagged ([opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list)); `draft` and `approve` refuse such mentions, and `publish --live` withholds an already-approved post before claiming it. For revoked permissions and API version sunsets, follow the [runbook](WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets).

An unattended worker must use `--scheduled-only` to skip approved-but-unscheduled posts and missed stale time windows. The [worker runbook](WORKER-OPERATIONS.md) includes **inert, disabled-by-default** systemd service/timer examples. No actual worker or account has been deployed by this repository, and uncertain sends still require human reconciliation.

## Optional Postiz backend (Issue #30 slice, mock-tested)

An account can opt in to `"backend": "postiz"` so an approved, due text post is sent through your own Postiz instance or Postiz Cloud (Bluesky, Mastodon, Telegram, Threads, LinkedIn, Facebook only). ReachWeaver stays the approval authority: same `--live` + `REACHWEAVER_ALLOW_LIVE=1` + `REACHWEAVER_ENV=prod`, pause, budgets and durable claim. A read-only integration check runs before the claim, and the key comes only from `{env_prefix}_POSTIZ_API_KEY`. **A Postiz acceptance is not a platform receipt:** the post goes to `needs_review` until you check the native post and `reconcile`. Reddit and X are refused. `show POST_ID` previews the exact Postiz request offline. If Postiz is down, the post stays approved, so you can post that text yourself and then `cancel` it here. Postiz is AGPL-3.0, and ReachWeaver only calls its HTTP API. The licensing and credential review (2026-10-15) and a ready owner smoke-test procedure are in the doc. **The smoke test has not been run:** no Postiz account has been connected or live-tested. See [docs/POSTIZ.md](POSTIZ.md).

## Ranked publishing opportunities from your own sources (Issue #9 slice)

`sources-scan` reads an opt-in list of GitHub releases, tags, merged PRs and labelled closed-as-completed issues, RSS/Atom feeds, website or Keep a Changelog changelogs, and Crossref DOI records. It ranks them as `SourceSignal`s with evidence URLs, freshness, novelty, audience relevance and a transparent score breakdown. Bot PRs, trivial PRs, drafts, private sources, reposts and items you have already seen are held back. By default it reads only local fixtures. `--online` makes explicit, GET-only requests to public GitHub repositories (paginated, capped by `max_pages`), https feeds and changelogs (after a robots.txt check), and the Crossref API. Fetched text is untrusted data, and the command never drafts or publishes. A private GitHub repository is read only with three opt-ins (`visibility` and `sensitivity` both `private`, plus `--allow-private`). This was owner-approved on 2026-10-09 (proposal). Its signals are marked private and are never public evidence or public links until a person marks each evidence record `publishable`. With `--sources CONFIG`, `draft` (reply drafts too), `media-attach`, `youtube-attach` and `approve` also refuse (and `check` reports as `BLOCKED`) a link into a `visibility: private` repository of that config (`private_source_link`). **Without `--sources` these commands do not know the private repositories and cannot detect such a link**, so pass it whenever a private source is configured. `signal-brief` then turns one saved signal into a new campaign scaffold plus a `.brief.md` sidecar: evidence URLs with an untrusted excerpt, one `{{write: ...}}` placeholder variant per approved account with a live or manual path, and the per-account length limits. It generates no copy, and the scaffold stays BLOCKED until a person writes every placeholder and verifies every source. See [docs/SOURCES.md](SOURCES.md).

```bash
reachweaver sources-scan --config examples/sources.reachweaver.json --top 5
reachweaver sources-scan --config examples/sources.reachweaver.json --json > scan.json
reachweaver signal-brief --scan scan.json --signal-id SIGNAL_ID --brand examples/brand.reachweaver.json \
  --accounts config/accounts.example.json --out private/campaign.json
```

## Which social networks to prioritize for real attention

ReachWeaver now includes an evidence-dated [channel choice framework](../.claude/skills/reachweaver/references/channel-selection.md) and [platform-specific growth playbooks](../.claude/skills/reachweaver/references/platform-playbooks.md). A larger October 2026 open-source ecosystem audit compares leading social/marketing skills with schedulers, licenses and safe integration decisions.

- For **AI, Kubernetes, open-source and research**: start with LinkedIn plus original YouTube demonstrations. Use Reddit and Hacker News for **selective, genuinely human-written community activity** and technical feedback, not autonomous distribution.
- For **broader consumer attention**: reuse **original** demonstration footage as Instagram Reels, YouTube Shorts and TikTok-native edits if video-production capacity exists; then consider Threads/X/Bluesky for written conversations.
- For **retention**: opt-in Telegram/Discord/newsletter; these are generally not substitutes for public discovery.
- **Data caveat:** global monthly users, registered members, weekly users and Shorts video plays are fundamentally different. No channel guarantees views; measure first-party qualified outcomes.

Example Claude Code request:

> /reachweaver — Audit the distribution options for my public GitHub release. Compare high-volume reach with technical audience fit using official 2026 sources. Prioritize two channels, give channel-native proof-backed drafts, explain platform-specific restrictions, and design a 14-day measured experiment. Do not auto-publish.

## DEV Community: build technical authority without spam

**[DEV authority and collaboration playbook](DEV-AUTHORITY-PLAYBOOK-2026-10-08.md)** is a dedicated operating guide for creating a credible profile, discovering engineering readers, human-written comments, one evidence-rich article per week, selecting up to four relevant tags, using DEV's native editor scheduler, and measuring qualified GitHub/collaboration outcomes. Use the bundled Claude Code specialist reference at `.claude/skills/reachweaver/references/dev-community.md` and the portable human-author brief at `.claude/skills/reachweaver/assets/dev-article-brief.md`.

**Critical limits:** DEV prohibits AI-generated comments/replies (apart from basic grammar/translation/accessibility assistance) and restricts AI-generated promotional/clout/backlink articles; read [official rules](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/). DEV has [native article scheduling](https://dev.to/help/writing-editing-scheduling), but **ReachWeaver has no DEV adapter, scheduled publishing receipt, account connection or native analytics fetch**. For this channel, prepare an evidence inventory, author-edited brief and manual scheduling handoff—not fabricated automation.

Claude Code example:

> /reachweaver — Audit my public DEV profile and my three best engineering posts. Recommend prioritized, evidence-checked profile improvements; a 30-day human-owned technical writing and community participation plan; a DEV native-scheduling checklist; and first-party metrics for GitHub contributors and real collaborations. Do not generate DEV comments or automatically publish.

## Local dashboard and friendlier terminal output (experimental)

`reachweaver ui` serves a read-only dashboard of your local state on `127.0.0.1`: what needs a human, the queue, each post's exact text with a length meter and approval state, accounts and the offline channel research. It cannot approve, schedule, pause or publish. `reachweaver start` prints the exact next command for your current state, `reachweaver completion bash|zsh` prints a Tab-completion script, and at a terminal `show` prints a readable review sheet. See the [dashboard guide](DASHBOARD.md).
