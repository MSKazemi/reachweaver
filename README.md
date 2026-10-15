# ReachWeaver

<img src="plugin/.claude-plugin/icon.png" alt="ReachWeaver icon: Persian-inspired turquoise and gold geometric knot" align="right" width="112" height="112">

**Claude Code social media skill for evidence-backed content planning, approval-first scheduling, publishing workflows, and analytics.** Turn one verified achievement into useful platform-native content, review each destination, and learn from real results.

> Status: **v0.2 experimental**. A working offline-first CLI and Claude Code skill are included. Live **text-only** API adapters exist for Bluesky, Mastodon, Telegram and LinkedIn personal profiles (member posts), but no personal accounts are connected and no live end-to-end publishing has been run. Mock-tested adapters for Threads text, Facebook Page text/link, Instagram single images, YouTube and TikTok video, Mastodon images and Bluesky/Mastodon replies also exist (owner-approved 2026-10-09); the capability registry keeps each one disabled until the owner records a supervised live receipt, so those platforms stay draft/manual-only (`manual-export`), as do all others. This is not a promise of viral growth.

> **Independent-use and liability notice.** ReachWeaver is experimental, user-operated source code provided **"AS IS"** under Apache-2.0 Sections 7–9, with **no warranty** except as required by applicable law or agreed in writing. The original contributors do not direct, authorize or supervise independent users' posts, misuse, accounts, modifications or third-party services merely by publishing this code; each operator must review their own permissions and actions. Neither this notice nor the license guarantees immunity from non-waivable liability. ReachWeaver is not affiliated with Anthropic or the listed platforms. Read the [full responsible-use and liability notice](DISCLAIMER.md), [privacy policy](docs/PRIVACY.md) and [security policy](SECURITY.md).

**Start here:** [quickstart](docs/QUICKSTART.md) · [what works now](docs/PLATFORM-MATRIX.md) · [install the Claude Code skill](#claude-code-skill) · [contribute](CONTRIBUTING.md) · [GitHub discovery and naming](docs/GITHUB-DISCOVERY.md) · public-release safety checklist

**At a glance:** Draft distinct social posts, propose account-aware publishing times, export unsupported networks for manual posting, and review outcomes from imported native analytics. Automatic multi-network publishing, live account authorization, and algorithm-driven reach are **not** established by the current offline tests. The release checklist separates public code review from any live-posting permission.

**Visual identity:** [official icon](plugin/.claude-plugin/icon.png) · [brand usage](assets/brand/README.md)

## Why this exists

A valuable paper, GitHub release or engineering demo deserves to reach the people who can use it. ReachWeaver treats distribution like engineering: verified inputs, platform-native outputs, approval gates, durable execution records and measurable feedback. It is not a bot farm or mass engagement automation.

**Compared with standalone social writing skills:** ReachWeaver adds executable dry-run publishing, per-item approval and account isolation, local SQLite outbox state, ambiguous-send reconciliation, a pause switch, measurement coverage and research documentation. It does **not** yet replace commercial social suites or provide platform-wide OAuth, media upload or analytics aggregation.

## 5-minute setup

Requirements: Python 3.11+; no third-party runtime dependencies.

**New here? Start with the [quickstart](docs/QUICKSTART.md):** clone to a safe one-post dry-run preview and manual export packages with no account, no credentials and no network, every step tested by `tests/test_onboarding.py`. If something fails, see [troubleshooting](docs/TROUBLESHOOTING.md); before any live send, go through the [security checklist](docs/SECURITY-CHECKLIST.md).

```bash
# 1. Create or clone the ReachWeaver repository; from its root:
python3 -m pip install -e .        # optional: alternatively python3 -m reachweaver.cli
cp config/accounts.example.json config/accounts.json

# 2. Edit config/accounts.json: use only accounts you control. NO API keys in this file.
reachweaver doctor                # offline; shows which environment variable names are missing
reachweaver init                  # creates owner-only local SQLite state
# Once the owner provides tokens privately outside Claude:
# reachweaver connect-check --account my-bluesky --online   # authenticates, never publishes
# reachweaver connect-check --account my-mastodon --online  # also reads public /api/v2/instance limits

# 3. Draft a post and check it
reachweaver draft --account my-bluesky --file examples/example-post.txt --campaign first-campaign
reachweaver list
reachweaver show POST_ID          # review EXACT body, account, source, status, approval binding + offline platform_preview
reachweaver check POST_ID

# 4. Inspect and explicitly authorize this exact post (binds a hash of the exact payload)
reachweaver approve POST_ID --approver YOUR_NAME
reachweaver publish --post-id POST_ID  # preview ONE approved, due post; NO networking
reachweaver schedule POST_ID --at '2026-10-15T09:00:00+02:00'  # local due time; a future post is not previewable until due

# 5. ONLY when account auth is configured and the user approves sending
# Export actual credentials privately through a local secret manager/environment.
# Only for one explicitly approved post, with a supervised account verification,
# on the production host only (REACHWEAVER_ENV=prod is required as well, issue #5):
# REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID
```

If no account configured yet, use the skill for research, content and plans; it will generate platform-native drafts with a manual publishing handoff. Never send passwords, OTPs or OAuth tokens into a chat or GitHub issue.

The next human-assisted milestone is a single supervised real-world pilot, not automatic mass cross-posting. Follow **[First live pilot](docs/FIRST-LIVE-PILOT.md)** (for Bluesky on a throwaway account, the copy-pasteable **[Bluesky one-post pilot](docs/BLUESKY-PILOT.md)** runbook) for local credential entry, read-only identity validation, the exact-post approval workflow, reconciliation and a private evidence checklist. **No account has been connected or live-tested in this repository.**

## Evidence-ranked social growth actions (offline, Issue #6)

`reachweaver suggest` combines **individual profile fixes**, **native post-metric coverage**, **unresolved publishing-safety incidents**, **owner-exported engagement triage**, **SEO destination conflicts** and **preregistered experiment history** into one evidence-ranked, human-review-only task list. Safety and privacy issues come first. It never guesses account rankings or predicts likes/impressions.

```bash
reachweaver suggest --snapshot examples/profile-post-audit.example.json --json
# Optional: add owner-exported data from an EXISTING private DB; no DB created or modified
reachweaver --db state/reachweaver.sqlite suggest --include-db --snapshot state/owned-profiles.json --limit 15
```

`reachweaver weekly-digest --out state/digest.md` takes the same inputs and writes them as one private Markdown file; nothing is sent. See [the next-actions runbook](docs/NEXT-ACTIONS.md). Real native account analytics need an owner-approved export or a separately authorized collector; `suggest` itself never connects to a platform, changes the queue, drafts, replies, likes, schedules or publishes.

## Consent-aware native social analytics from local CSV (offline)

Profiles and content audits can now incorporate **your own source-labeled native metrics**, without connecting social accounts or modifying databases. Export the metrics you are authorized to use, normalize the columns once, and run:

```bash
reachweaver post-audit --file examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv --owner-authorized --json
reachweaver suggest --snapshot examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv --owner-authorized --json
```

The CSV is read into memory, never published or saved elsewhere; owner acknowledgement is **not** independent API verification. Blank metrics remain unknown and duplicate posts are rejected. See [native CSV import contract](docs/PROFILE-METRICS-CSV.md) and [individual platform audits](docs/PROFILE-AND-POST-AUDITS.md).

## International social discovery (research only, issue #64)

The offline distribution planner catalogs **90 channels across 18 market pilots**, including India-focused ShareChat, Moj, Josh, Chingari, Hipi, Public App, Kutumb, Arattai Channels, LocalCircles, Pratilipi and Rooter; plus Xiaohongshu/REDnote, Weixin Channels, Naver, VK and other regional platforms. See [regional channel research and India use cases](docs/REGIONAL-CHANNELS-2026-10-14.md) and [community, creator and publishing platform research](docs/COMMUNITY-AND-CREATOR-PLATFORMS-2026-10-15.md) and try `reachweaver distribution-plan --region india --goal awareness --limit 12 --json`. New entries are **manual-only** with `unverified` native scheduling; community discussion and opt-in channel messages stay human-only. No new API adapter, account connection or remote publication is implied.

## Per-platform profile and post audits (offline, Issue #3)

**ReachWeaver now audits each network on its own terms.** LinkedIn headlines and featured proof, YouTube channel organization and retention, Instagram original Reels and reach, DEV technical articles, Mastodon profile metadata and verified links, Reddit rules, Product Hunt maker stories, and Chinese developer channels receive **different profile checklists and native content experiments**. Twenty-five platform and community surfaces are described in the [complete audit playbook](docs/PROFILE-AND-POST-AUDITS.md). This is not a universal ranking formula or a guarantee of likes, impressions, views or follows.

```bash
# Pure offline example: no account, API keys, website fetch, local DB or changes.
reachweaver profile-audit --file examples/profile-post-audit.example.json --json
reachweaver post-audit --file examples/profile-post-audit.example.json --age-hours 168 --min-posts 3 --json

# For a REAL audit, replace the example with your OWN owner-confirmed profile
# observations and source-labeled native analytics kept locally under state/.
reachweaver profile-audit --file state/owned-profiles.json --platform linkedin --json
reachweaver post-audit --file state/owned-profiles.json --platform youtube --json
```

The profile score is **only an observed checklist-completion heuristic** and is omitted when less than 60% of the platform's fields have been checked. Post rates require compatible format, account, platform, native metric source and observation age; missing data is **unknown**. Inputs include no passwords or cookies and the audit commands never log in, change a profile, draft, schedule, publish, follow or generate votes. Without an actual owner-supplied snapshot, the skill offers a **general playbook**, not an audit of your real accounts. See the [portable skill reference](.claude/skills/reachweaver/references/profile-post-audit.md).

## Developer attention and contributor growth

For a source-backed, moderation-aware 30-day plan prioritizing DEV, LinkedIn, GitHub-native discovery and Bluesky over indiscriminate cross-posting, see the [open-source developer growth playbook](docs/OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md). It covers first-time-contributor onboarding, platform rules, Reddit/HN recovery, qualified-traffic measurement and the changed Hacktoberfest 2026 rules. It is strategic research, **not** evidence of published posts or promised reach.

**Claude Code growth workflow:** The installed skill now loads [its contributor-growth reference](.claude/skills/reachweaver/references/open-source-growth.md) when you ask about GitHub visitors/contributors or restricted HN/Reddit promotion. Example: `/reachweaver — Grow first-time contributors for YazSes using DEV, LinkedIn, GitHub and Bluesky over the next 30 days. Keep Hacker News human-only and Reddit community-compliant. Show the measurable checklist; do not publish anything without approval.`

## Worldwide audience selection: executable, offline research

ReachWeaver now includes a **versioned and tested research registry** spanning 36 networks/surfaces and 14 country/global market profiles, including India, China, Japan, Southeast Asia, Africa and Brazil. It also records native scheduling evidence separately from ReachWeaver's actual account and API capabilities. Results are **editorial channel hypotheses**, never guaranteed traffic, a detected account connection, a remote scheduling receipt or publishing permission.

```bash
# No account credentials or network access needed. These NEVER queue or publish.
python3 -m reachweaver.cli distribution-plan --region india --goal adoption --limit 5
python3 -m reachweaver.cli distribution-plan --region china --goal contributors --json
python3 -m reachweaver.cli distribution-plan --region nigeria --goal collaboration --date 2026-10-14 --json
# A worldwide audience has no one local timezone:
python3 -m reachweaver.cli distribution-plan --region global --goal awareness --date 2026-10-14 --timezone Europe/Rome --json
```

Use `--goal contributors|adoption|awareness|collaboration`. Candidate local lunchtime/evening windows are **illustrative tests**, not verified optimal hours; the output includes offset-aware local and UTC timestamps. Hacker News is a special **human-authored, manual-only** channel and must not receive AI-written or AI-edited submission content.

Read [global strategy](docs/GLOBAL-DISTRIBUTION-2026-10-08.md), [operating/governance standard](docs/DISTRIBUTION-GOVERNANCE.md), [platform algorithm and scheduling evidence](docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md), [developer contributor playbook](docs/OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md), and the [owner-reported activity inventory](examples/yazses-channel-audit.example.json). The canonical machine-readable data is `reachweaver/distribution_registry.json`; the complete standalone Claude Code skill bundles that registry and the planner.

## Claude Code skill

From the ReachWeaver repository, start `claude` and type:

```text
/reachweaver
Analyze my latest GitHub release and create a two-week evidence-based social campaign
for a technical audience. Make distinct LinkedIn, Bluesky and YouTube Shorts drafts.
Explain what can be published automatically, prepare a dry run, and wait for approval.
```

The project skill lives at `.claude/skills/reachweaver/SKILL.md`. For any other repository, install that folder as a skill (user-level `~/.claude/skills/reachweaver`, or the target's `.claude/skills/reachweaver`); the installable ZIP below also works. The standalone package includes a CLI at `scripts/run.py`, all referenced guidelines, and self-contained sample manifests in `assets/`:

```bash
# Run inside the target project, pointing to where you installed the skill
python3 ~/.claude/skills/reachweaver/scripts/run.py doctor    # first command: shows what is missing
python3 ~/.claude/skills/reachweaver/scripts/run.py --help
```

In a skill-only install the repository's `examples/` and `config/` files are in the skill's `assets/` folder (for example `config/accounts.example.json` is `assets/accounts.example.json`, `examples/example-post.txt` is `assets/example-post.txt`). If `doctor` reports missing accounts, copy `assets/accounts.example.json` to `config/accounts.json` in your project; it holds no secrets.

This is compatible with Claude Code's documented project/personal skill paths: https://code.claude.com/docs/en/skills.

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
| Bluesky / Mastodon / Telegram | Text-only live API adapters, mocked tests; real accounts not verified. Mastodon image attachments (#13) and Bluesky/Mastodon reply sending (#17) are mock-tested but registry-disabled until a live receipt. Bluesky: 300-grapheme check, link/hashtag facets, optional custom PDS `service`, deterministic record key, verified `uri`/`cid` receipt. Mastodon: pinned per-instance `max_characters`, `visibility` (no DMs), optional `language`/`spoiler_text`, verified receipt. See [PLATFORMS.md](docs/PLATFORMS.md#bluesky-and-mastodon-adapter-details-issues-12-13) |
| LinkedIn personal profile (member) | Text-only Posts API adapter with pinned `Linkedin-Version`, mocked tests; organization pages unsupported ([guide](docs/LINKEDIN.md)) |
| Threads, Facebook Page, Instagram, YouTube, TikTok | Mock-tested adapters (Threads text #19, Facebook Page text/link #21, Instagram single image #20, YouTube upload #22, TikTok creator-inbox video #23), owner-approved 2026-10-09 and **disabled** in the capability registry until a supervised live receipt; `manual-export` creator packages meanwhile |
| X, Reddit, Pinterest | Creative/manual workflow only; no adapter |
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

**Preregister before publishing:** `experiment-preregister examples/growth-plan.example.json --out FILE` rejects more than 3 arms, multi-dimension or near-duplicate variants, vanity primary metrics (likes, views) and early stopping on a win, and it requires notes acknowledging six confounders. `experiment-verify PLAN --hash H` then shows whether the plan was edited after registration. `experiment-readout --prereg FILE --posts MAP.json` verifies the hash, maps arms to published posts and reads out against the local database without writing to it. Readouts are labeled only `insufficient_data`, `inconclusive` or `directional`, never proven. Each readout states whether the design was owner-declared randomized or only associational. `experiment-simulate` shows, on SIMULATED data, how often each label occurs. See [growth experiments](docs/GROWTH-EXPERIMENTS.md).

The experiment example contains illustrative **post IDs that must be replaced** with actual published posts and recorded native snapshots. Its readout requires matched ages and a predeclared hypothesis, metric, denominator, minimum pairs and guardrail; it never claims randomized causal proof. No proprietary ranking algorithm or guaranteed view count is assumed.

**Claude operating methodology:** identity/voice → audience and source research → evidence-bound campaign → truthful hooks and platform-native drafts → user review → approved outbox → native analytics → next test. The optimized router skill and on-demand references are in `.claude/skills/reachweaver/`; creative/evidence/experiment guides are progressively loaded rather than dumping 106 skills into the agent context. See competitive audit and [agent evaluation cases](evals/README.md).

## CLI reference

```text
reachweaver init                         local DB initialization
reachweaver doctor                       check account mapping / missing env names
reachweaver credentials-audit [--history FILE] [--json]  offline credential hygiene: names only, never values; exit 1 on findings
reachweaver draft --account X --text Y [--optout F]   create a reviewable draft; refused if it @-mentions an opted-out handle/domain
reachweaver draft --account X --file F    create draft from UTF-8 file
reachweaver draft --account X --file F --reply-to ITEM [--online] [--override-flagged LABEL]
                                          #17 (owner-approved 2026-10-09; gated off): reply draft under your own published Bluesky/Mastodon post; plan only without --online; sending blocked by the capability registry (`reachweaver_reply_adapter: none`)
reachweaver list [--status STATUS]        display post statuses
reachweaver check POST_ID [--optout F]    draft lint, including opt-out mentions
reachweaver approve POST_ID [--approver NAME] [--optout F]  approve one post; binds its payload hash and a fresh per-approval nonce (wave 9 item 1: the first send consumes it in an append-only, hash-chained approval-ledger.jsonl next to the database, so a restored older approval of the same post is refused with approval_replay_reapproval_required before any request; approvals made before the upgrade must be re-approved once); refused on an opt-out mention
reachweaver approve POST_ID --verify-identity --online  opt-in (wave 9 item 2): one read-only connect-check identity request, then bind a fingerprint of the verified account entry (account id, platform, handle/server/ids; no token or response stored) under tag approval/id-1; a failed check approves nothing; a later send whose account entry no longer matches is BLOCKED before any request (approval_identity_mismatch_reapproval_required). Binds WHICH account, not WHO approved
reachweaver unapprove POST_ID            revoke approval (not published)
reachweaver media-attach POST_ID --file IMG (--alt TEXT | --alt-file F) [--focus x,y] [--optout F]  add one PNG/JPEG/WebP with required alt text to a Mastodon DRAFT (warns if the image carries EXIF GPS data; never strips it); approval binds its SHA-256, MIME, alt and focus; offline, never uploads (#13, owner-approved 2026-10-09; disabled until a live receipt: live image send is registry-refused, docs/SECURITY-REVIEW-PROPOSAL-13-mastodon-media.md)
reachweaver media-detach POST_ID         remove every attachment from a draft (local files untouched)
reachweaver schedule POST_ID --at ISO8601 set UTC due time
reachweaver video-attach POST_ID --file V.mp4 [--mode creator_inbox|direct_post] [--privacy-level L --commercial C] [--allow-comment] [--allow-duet] [--allow-stitch] [--aigc]  offline: bind one MP4 to a TikTok draft; approve binds its SHA-256; never uploads (issue #23, owner-approved 2026-10-09; disabled until a live receipt; docs/TIKTOK.md)
reachweaver video-detach POST_ID         offline: remove the video from a TikTok draft
reachweaver calendar-plan --account ID --timezone TZ --start D [--days 14] [--slot Wed@16:00]... [--audience-activity FILE [--audience-top-hours 5] [--audience-max-age-days 90]] [--apply]  propose audience-local slots (advisory); --audience-activity uses your own exported hour-of-week activity with per-slot provenance (docs/CALENDAR-PLANNER.md)
reachweaver calendar-gaps --account ID --timezone TZ [--start D] [--days 14] [--slot Wed@16:00]... [--tolerance-minutes 90] [--max-per-day 2] [--min-spacing-hours 20] [--timing-source S --timing-metric M --timing-denominator views|impressions] [--json]  # read-only: empty and overloaded slots in the local queue; never schedules (issue #9)
reachweaver cancel POST_ID               cancel only before delivery
reachweaver publish [--limit N] [--optout F]  show eligible work and which posts the opt-out list would block; no network
reachweaver publish --live               send only with env opt-in; opt-out mentions are BLOCKED before any claim
reachweaver publish --live --scheduled-only --max-lateness-minutes 180    guarded unattended sweep; owner setup only
reachweaver publish --live --max-per-24h 2 --min-gap-minutes 60          conservative account attempt caps; config/caps.json can only tighten them; a provider 429 cools the account down (never resends)
reachweaver publish [--live] --log-json [--log-file PATH]  one redacted JSON line per worker decision (fixed event/reason codes, no post text, secrets or URL queries) to stderr or an appended 0600 file; observational only, an unusable PATH refuses the run before any work (docs/WORKER-OPERATIONS.md)
reachweaver queue-health [--stale-minutes 30]  offline overdue/unknown outcome diagnostics
reachweaver health [--json] [--stale-minutes 30]  offline per-account readiness: adapter, registry age, credential NAMES, LinkedIn version/expiry, Postiz, queue, 24h budget, pause, live opt-in; ok|attention|blocked, exit 1 when blocked
reachweaver dead-letter [--json]          read-only needs_review grouped by sanitized error code, age + reconcile steps; never retries
reachweaver ops-metrics [--format json|prometheus] [--since 24h] [--optout F] [--out FILE]  read-only counts (claims, publishes, failures by send class, cooldowns, pause, dead letters, stale sending, oldest due age); Prometheus textfile output, written atomically; labels are account ids, platform and fixed codes only (docs/WORKER-OPERATIONS.md#operational-metrics-and-alerting-issue-26)
reachweaver ops-summary [--json] [--since 24h] [--stale-minutes 30] [--optout F] [--now T]  one redacted read-only report: overall readiness, per-account adapter/budget/queue, dead letters, ops-metrics totals; exit 1 when blocked (docs/WORKER-OPERATIONS.md#one-page-operations-summary-issue-26)
reachweaver privacy-status [--policy F]   offline DB perms/size/row counts/oldest records/retention config
reachweaver data-export [--out FILE]      redacted JSON export of local DB (new 0600 file)
reachweaver data-purge [--policy F] [--apply]  retention plan (dry run); --apply purges local rows only
reachweaver pause / resume               emergency publishing switch
reachweaver approval-mode [--confirmation tty_hash_prefix|off] [--require-key] [--require-identity]  #7 D6/D7 and wave 9 item 2 (--require-identity: plain approve is refused and approvals without a stored identity are not sent; tighten-only, no command removes it): show or tighten approval safeguards; opt-in terminal + typed hash prefix for approve and publish --live; keyed HMAC binding with REACHWEAVER_APPROVAL_SECRET (docs/SECURITY-REVIEW-PROPOSAL-7-d6-d7.md)
reachweaver reconcile POST_ID --published --remote-id ID [--remote-url URL]
reachweaver reconcile POST_ID --not-published  manual outcome review
reachweaver reconcile POST_ID ... --force      also reconcile a post still 'sending' that was claimed <15 min ago (a send may be in flight; check the native account first)
reachweaver metrics POST_ID --observed-at ISO8601 --source native-export --views 100 [--likes suppressed] [--window-start T --window-end T]
reachweaver import-metrics --csv FILE     import normalized snapshots, all rows or none; cells: count, empty/unknown, suppressed, not_available, delayed, estimated:N; optional window_start/window_end (docs/ATTRIBUTION.md#2a-bis-metric-value-status-privacy-thresholds-and-report-formats-issue-15)
reachweaver report [--format json|markdown|csv]  exact values only, per-metric coverage + status counts, estimates listed separately; null/unknown (never 0) at zero coverage; CSV formula cells neutralised
reachweaver metrics-fetch --online [--account ID] [--post-id ID] [--max-posts N] [--dry-run]  read-only GET of PUBLIC Bluesky/Mastodon counters for published posts; stops on 429; not private analytics (docs/ATTRIBUTION.md#2b-read-only-public-counter-polling-metrics-fetch)
reachweaver campaign-check --file F --brand B [--json] [--optout F] [--history-db DB [--history-days N] [--history-window-days W] [--history-max-posts K]]  offline schema/source/account checks; @-mention of an opted-out handle is an error; --history-db adds read-only repetition/frequency warnings against the same account's local posts (docs/CLAIM-GROUNDING.md#posting-history)
reachweaver claims-check CAMPAIGN --brand B [--json] [--optout F]    lexical grounding of figures/claims/feature claims vs declared evidence; not truth verification (docs/CLAIM-GROUNDING.md)
reachweaver campaign-preview --file F --brand B [--accounts A] [--utm] [--optout F] [--history-db DB [--history-days N] [--history-window-days W] [--history-max-posts K]] [--json|--markdown]  per-channel exact text, length budget, facets, capability, evidence, rule-based confidence; threads/carousel outlines; never stages (docs/CAMPAIGN-PREVIEW.md)
reachweaver campaign-export --file F --brand B --out DIR [--optout F]  manual-ready draft package
reachweaver youtube-attach POST_ID --file CLIP.mp4 --title T --made-for-kids yes|no --category-id N [--privacy private|unlisted|public] [--tag T]...  #22 (owner-approved 2026-10-09; disabled until a live receipt): bind one local MP4 + upload fields to a YouTube draft; offline; live upload stays registry-blocked (docs/YOUTUBE.md)
reachweaver youtube-detach POST_ID         #22: remove the video from a YouTube draft; offline
reachweaver youtube-analytics [--account ID] [--post-id ID]  #22: read-only sampling report over stored metric snapshots of published YouTube videos; labels exact / sampled / suppressed / delayed / not_available / unknown, never invents a view count, fetches nothing (docs/YOUTUBE.md#analytics-sampling-report)
reachweaver manual-export --platform threads|instagram|facebook|youtube|tiktok --variant F [--media MANIFEST] [--account-meta F] --out DIR [--json]  creator-ready package + checklist; never sends (docs/MANUAL-EXPORT.md)
reachweaver campaign-stage --file F --brand B [--optout F]    idempotently create draft posts only
reachweaver experiment-evaluate --file F       analyze paired native metrics; no causal claim
reachweaver experiment-preregister PLAN --out FILE  validate a 2-3 arm growth plan, write a NEW hashed record (docs/GROWTH-EXPERIMENTS.md)
reachweaver experiment-verify PLAN --hash H     detect post-hoc plan edits by recomputing the sha256 plan_hash
reachweaver experiment-readout --prereg FILE --posts MAP.json [--hash H] [--json]  read-only: verify hash, map arms to posts, label + design + flags; non-causal
reachweaver experiment-next --results FILE [--json]  offline: next dimension from prior classified results; keeps negative results
reachweaver experiment-simulate --seed S --pairs N --lift L --noise X [--trials T] [--json]  offline SIMULATED label distribution; never stored
reachweaver baseline-eval --account ID --timezone TZ --source S --metric M --denominator views|impressions --since D --until D [--slot [explore:|exploit:]Wed@16:00]... [--campaign-arm [explore:|exploit:]C]... [--readout FILE]... [--min-posts 5] [--resamples 2000] [--confidence 0.95] [--seed 16] [--json]  read-only: vs calendar-plan's generic baseline; seeded bootstrap CI, Holm-Bonferroni, correlational vs randomized, insufficient_data not 0 (docs/TIMING-EVALUATION.md)
reachweaver pilot-report --log FILE.csv [--min-weeks N] [--json]  offline per-arm field-pilot log readout; blank = unknown; descriptive, refuses arms under N ISO weeks (docs/FIELD-PILOT-PROTOCOL.md)
reachweaver schema-validate --type TYPE FILE   offline canonical schema check, versions 1.0, 1.1 and 1.2 (docs/SCHEMAS.md)
reachweaver schema-export [--out FILE] [--type T]  read-only: local DB rows as validated canonical JSON lines (current schema version) (new 0600 file or stdout); unmapped rows listed with reasons; never mints Approvals (docs/ADR-0003-CANONICAL-CONTRACTS.md)
reachweaver engagement-fetch [--online --out FILE] [--account ID] [--post-id ID] [--max-posts N] [--depth N] [--max-replies N]  read-only, unauthenticated GET of PUBLIC replies to your published Bluesky/Mastodon posts into a NEW 0600 engagement-triage export; plan only without --online; redacted; stops on 429; never replies or writes the DB (docs/ENGAGEMENT.md#fetching-replies-engagement-fetch)
reachweaver engagement-triage EXPORT.json [--docs-map F] [--out F.md|F.json] [--issues-dir DIR] [--optout F] [--json]  offline comment triage; reply/issue DRAFTS only, no send path; no draft for opted-out authors (docs/ENGAGEMENT.md)
reachweaver engagement-queue EXPORT.json [--docs-map F] [--optout F] [--now ISO] [--sla LABEL=HOURS] [--json]  offline aging/SLA review queue (overdue first); read-only, no database, no send path; SLA hours are owner-adjustable defaults
reachweaver utm-link URL --platform P --campaign C [--content POST_ID] [--allow-origin O...] [--sources CONFIG]  canonical UTM link; refuses private source repo URLs
reachweaver attribution-report --analytics CSV [--github-traffic JSON]... [--json]  read-only UTM/referrer join
reachweaver seo-destinations --briefs FILE --type release|paper|product|docs [--project P]  landing page + conflicts
reachweaver weekly-digest --out FILE.md [suggest input flags] [--include-db] [--limit N]  suggest + local report as one private (0600) Markdown file; new file only, local only, never sent (docs/NEXT-ACTIONS.md)
reachweaver seo-content-check FILE.json [--name NAME]... [--online [--allow-origin O...]] [--json]  discoverability checks on post texts you supply (length, alt text, hashtags, UTM link, name spelling, cross-platform duplicates; --online adds link-preview checks); every finding labelled official/repo_policy/hypothesis; read-only, exit 2 on errors
reachweaver landing-check --briefs FILE [--campaign CAMPAIGN.json] [--online] [--allow-origin O...] [--json]  canonical URL/CTA/UTM checks; --online: status, redirects, rel=canonical, Open Graph, noindex, lang, lexical claim phrases (docs/SEO-INTEGRATION.md)
reachweaver link-health [--status approved --status scheduled --status published] [--account ID] [--campaign C] [--online [--allow-origin O...]] [--max-links 25] [--json]  # read-only: broken, redirected or preview-less links in your stored posts; offline unless --online; robots.txt first (issue #10)
reachweaver media-check MANIFEST [--json]      offline media headers/limits/alt text/captions/rights; never uploads
reachweaver media-attach POST_ID --url HTTPS_URL (--alt T | --alt-file F) --online  Instagram draft: one owner-hosted JPEG URL; one read-only GET; SHA-256 + URL bound at approval (#20, owner-approved 2026-10-09; disabled until a live receipt)
reachweaver media-detach POST_ID               offline: remove a draft's image attachments
reachweaver media-storyboard --campaign CAMPAIGN.json --out NEW_DIR [--duration S] [--aspect 9:16] [--video-targets T...] [--image-targets T...] [--locale L] [--fit cover|contain] [--safe-zones F] [--json]  offline storyboard + SRT skeleton + alt-text placeholders + image specs + media manifest; generates no media (docs/MEDIA-ASSETS.md)
reachweaver media-storyboard --check STORYBOARD.json [--safe-zones F] [--json]  re-check edited text/focal boxes against crop and UI-overlay safe areas, subtitle timing and placeholders
reachweaver sources-scan --config F [--online] [--allow-private] [--state S] [--top N] [--json]  rank opt-in GitHub/feed/changelog/DOI signals; research only
reachweaver signal-brief --scan SCAN.json --signal-id ID --brand B --out CAMPAIGN.json [--accounts A] [--objective O] [--allow-duplicate] [--json]  NEW 0600 campaign scaffold + .brief.md from one signal; placeholders block staging; no copy generated (docs/SOURCES.md)
reachweaver capabilities [--platform P] [--json] [--max-age-days 120]  offline capability/API-approval registry (docs/PLATFORMS.md)
reachweaver distribution-plan --region R --goal G [--limit N] [--date D --timezone TZ] [--json]  offline worldwide channel hypotheses; never schedules or posts
reachweaver postiz-check --account ID --online [--post-id ID]  read-only Postiz integration listing / job lookup; never posts (docs/POSTIZ.md)
```

**Field pilot and release playbook (issue #27 slice):** [FIELD-PILOT-PROTOCOL.md](docs/FIELD-PILOT-PROTOCOL.md) is a preregistration template for comparing a manual workflow, a conventional scheduler and ReachWeaver on one owned project and two authorized networks; copy `examples/pilot-log.template.csv` (header only) and read it with `pilot-report`. [RELEASE-PLAYBOOK.md](docs/RELEASE-PLAYBOOK.md) walks a release through every existing command and human gate. **No pilot has been run and there are no pilot results.**

**Campaign links and attribution (offline):** `utm-link` builds deterministic, tracker-free UTM links. `attribution-report` joins your own analytics CSV and saved GitHub traffic JSON to local posts. It keeps exact UTM matches, domain-level referrer-only rows and unattributed rows separate, and never zero-fills or claims causation. `seo-destinations` reads a local export from the [SEO repository](https://github.com/MSKazemi/seo) and flags competing campaigns. `landing-check` checks that campaign CTAs point at a brief's canonical page and, only with `--online`, fetches each landing page (robots.txt honoured, redirects capped) to check its status, canonical link, social preview and claim wording. Each recorded per-platform metric definition carries the official `source_url` it was checked against and a `verified_date`. See [attribution](docs/ATTRIBUTION.md), a [SYNTHETIC weekly report example](docs/WEEKLY-REPORT-EXAMPLE.md) and the [SEO integration contract](docs/SEO-INTEGRATION.md).

Use `--db PATH` and `--accounts PATH` as global options preceding the subcommand when needed. Dates must include a timezone offset. `reachweaver publish` without `--live` cannot send anything; even `--live` requires `REACHWEAVER_ALLOW_LIVE=1` **and** `REACHWEAVER_ENV=prod` (checked before any account or credential is loaded), approved due posts, the right account configuration, and available credentials.

**Account and credential hardening (issue #5, owner-approved 2026-10-09).** `load_accounts` refuses two accounts whose `env_prefix` values are identical or overlap (one account's credential variable name equals or begins another's, over every adapter suffix and the Postiz key). Credentials come from environment variables or, on an unattended host, from systemd `LoadCredential=` files in `$CREDENTIALS_DIRECTORY` (absolute, not group/world-writable, no symlinks or FIFOs, at most 64 KiB, UTF-8); while that directory is set the environment is not consulted. `doctor`, `health` and `credentials-audit` show the credential source and `REACHWEAVER_ENV`, never a value. See [account onboarding](docs/ACCOUNT-ONBOARDING.md#unattended-host-systemd-loadcredential).

**Public counter polling (issue #15 slice):** `metrics-fetch --online` reads the public like, repost, reply and (Bluesky only, when present) bookmark counters of your own **published** Bluesky and Mastodon posts and stores one `bluesky-public-api` / `mastodon-api` snapshot per post. Bluesky uses the unauthenticated public AppView `app.bsky.feed.getPosts` (25 URIs per GET) and checks that the author DID matches the post URI. Mastodon uses an unauthenticated `GET /api/v1/statuses/:id`, retried once with `{env_prefix}_TOKEN` only on 401/404, and checks that the author is the configured handle. Views, impressions and clicks are not exposed and stay unknown (NULL), never 0. Requests run one at a time, at most 50 posts per run by default (200 maximum), and a 429 stops the run without retrying. `--dry-run` uses no network. This is not private analytics. See [attribution, section 2b](docs/ATTRIBUTION.md#2b-read-only-public-counter-polling-metrics-fetch).

**LinkedIn member posts (issue #14):** a `linkedin` account needs `person_id` (the `sub` from `/v2/userinfo`) and a pinned `linkedin_version` (`YYYYMM`), with an optional non-secret `authorization_expires_at`. The token is read only from `{env_prefix}_ACCESS_TOKEN`. `doctor` warns offline when the version is 10+ months old or the token is within 7 days of expiry; `connect-check --online` and `publish --live` refuse a version 12+ months old, an expired token, a `sub` mismatch or any organization author before claiming the send. The receipt is the `x-restli-id` post URN. `userinfo` proves identity, not the `w_member_social` write scope. An **opt-in** `"scope_check": "introspect"` adds LinkedIn's read-only token introspection before the claim; it needs the app's `{env_prefix}_CLIENT_ID` and `{env_prefix}_CLIENT_SECRET` and was owner-approved 2026-10-09 (proposal). See [LinkedIn onboarding](docs/LINKEDIN.md).

**Instagram single-image adapter (issue #20, owner-approved 2026-10-09; disabled until a live receipt):** `reachweaver/instagram.py` implements the official container flow (`POST /{ig-user-id}/media` with `image_url`, `caption` and `alt_text`, `status_code` polls, `media_publish`, permalink receipt) with a `GET /me` identity check (`user_id`, `username`, professional `account_type`) and a read-only `content_publishing_limit` quota check before the claim. Instagram fetches the image from a public URL, so the owner hosts the JPEG; `media-attach --url ... --online` downloads it read-only and approval binds its SHA-256 and URL. A live send downloads it again before the claim and immediately before the container request, and refuses changed bytes. It is tested with mocked HTTP only and is **unreachable**: the capability registry keeps Instagram image `api_gated` with no adapter, so `publish --live` and `connect-check --online` refuse it before any credential read. It stays that way until Meta App Review, a supervised first live receipt and a reviewed registry change (proposal, [guide](docs/INSTAGRAM.md)). Use `manual-export --platform instagram` meanwhile.

**TikTok video adapter (issue #23, owner-approved 2026-10-09; disabled until a live receipt):** `reachweaver/tiktok.py` implements the official Content Posting API flow (init, chunked `PUT` upload, status polling) with a creator-inbox draft upload as the first mode: the creator finishes and posts the video in the TikTok app, so ReachWeaver records the upload as `needs_review` (`tiktok_creator_inbox_delivered_not_public`), never as published, until the owner reconciles the native video. Direct Post sits behind a second registry switch, with an explicit, bound privacy level that must appear in the creator's live `privacy_level_options` (never PUBLIC by default). `video-attach` binds the MP4's SHA-256 into the approval hash. Everything is mock-tested only and **unreachable**: the registry keeps `reachweaver_video_inbox` and `reachweaver_video_direct_post` off and `policy.SUPPORTED_LIVE_VIDEO` is empty (proposal, [guide](docs/TIKTOK.md)). Use `manual-export --platform tiktok` meanwhile.

**YouTube upload adapter (issue #22, owner-approved 2026-10-09; disabled until a live receipt):** `reachweaver/youtube.py` implements the official resumable `videos.insert` upload (session POST, 256 KB-multiple chunks, `308` Range checks, `201` receipt) with a `channels.list?mine=true` channel check before the claim. `youtube-attach` binds the MP4's SHA-256, title, tags, category, privacy (default `private`) and a required made-for-kids answer into the approval hash (binding tag `reachweaver.storage.approval/video-1`); the file is re-hashed before the claim and again while it streams. It is tested with mocked HTTP only and is **unreachable**: the registry keeps YouTube `api_gated` with no adapter and `SUPPORTED_LIVE_VIDEO` is empty, so `publish --live` refuses it before any credential read. Uploads from unverified Google API projects may be locked private (proposal, [guide](docs/YOUTUBE.md)). Use `manual-export --platform youtube` meanwhile.

**Threads text adapter (issue #19, owner-approved 2026-10-09; disabled until a live receipt):** `reachweaver/threads.py` implements the official two-step text flow (container, status wait, publish, permalink receipt) with `GET /me` identity and a read-only `threads_publishing_limit` quota check before the claim. It is tested with mocked HTTP only and is **unreachable**: the capability registry keeps Threads `api_gated` with no adapter, so `publish --live` and `connect-check --online` refuse it before any credential read. It stays that way until the owner's Meta App Review, a supervised first live receipt and a reviewed registry change (proposal, [guide](docs/THREADS.md)). Use `manual-export --platform threads` meanwhile.

**Facebook Page adapter (issue #21, owner-approved 2026-10-09; disabled until a live receipt):** `reachweaver/facebook.py` posts text, plus a `link` taken from the approved text, to one configured Page via `POST /{page-id}/feed` (Graph API v25.0). Before the claim it checks that the token is a Page token for that exact Page (`GET /me` and `GET /{page-id}`). It is tested with mocked HTTP only and is **unreachable**: the capability registry keeps Facebook `api_gated` with no adapter, so `publish --live` and `connect-check --online` refuse it before any credential read. It stays that way until the owner's Meta App Review, a supervised first live receipt and a reviewed registry change (proposal, [guide](docs/FACEBOOK.md)). Use `manual-export --platform facebook` meanwhile.

**Interrupted or ambiguous send?** The post remains in `sending` or `needs_review`, not automatically resent. Manually inspect the actual account and then `reconcile`. This avoids silently duplicating a post at the cost of requiring human follow-up.

## Media asset checks (offline, Issue #18 slice)

`reachweaver media-check media/manifest.json --json` reads real PNG/JPEG/GIF/WebP/MP4/MOV headers. It checks dimensions, duration, size, extension mismatch, MP4/MOV video and audio codec, average frame rate, audio sample rate and channels, `moov`-before-`mdat`, sourced per-platform limits (unknown limits are skipped with a warning, never invented), image alt text, SRT/WebVTT captions for video, license and provenance (`rights_confirmed` must be true), and English-first translations. ReachWeaver **does not upload or generate media**; generation tools are optional, user-configured and need no paid model. See [media assets](docs/MEDIA-ASSETS.md).

`reachweaver media-storyboard --campaign CAMPAIGN.json --out media/storyboard` turns one campaign brief (the campaign contract or a `signal-brief` scaffold) into a vertical-video storyboard (JSON and Markdown), an SRT subtitle skeleton with valid timing, a `media-check` manifest with a LinkedIn image draft and the vertical video, and an image spec per platform. Alt text, captions, subtitle text and scene copy are `{{write: ...}}` placeholders and `rights_confirmed` starts `false`, so `media-check` blocks until a person writes them. It also flags text or focal boxes that a centered crop or a platform's UI overlay would hide. The built-in TikTok, Instagram Reels and YouTube Shorts safe zones come from each platform's first-party **paid-ad** creative specs (accessed 2026-10-09); organic overlays may differ, and every check says so. Supply other sourced numbers with `--safe-zones`. `media-check` also compares translated subtitle tracks cue by cue with the source track (cue count and timing, preserved terms, numbers and URLs).

## First-party timing-outcome analysis (offline, observational)

`timing-evaluate` now compares **already published** posts in one configured account using the operator's existing source-labeled native snapshot imports. It matches post ages and computes median rates by weekday/weekend six-hour audience-local dayparts. Missing values are excluded; an exploratory candidate needs observations in at least two time groups. It never fetches private analytics, changes a queue, guarantees reach or connects/publishes to a social platform.

```bash
reachweaver timing-evaluate --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --age-hours 168 --tolerance-hours 24 --min-posts-per-slot 3
```

`baseline-eval` measures improvement against the **non-personalized baseline**: over a required `--since/--until` period it compares posts at your own `--slot` times (or in a `--campaign-arm` variant campaign) with posts at `calendar-plan`'s generic slot. Each comparison gets a seeded percentile bootstrap interval and a permutation p-value, and Holm–Bonferroni corrects across every comparison in the run, including saved `experiment-readout` files. Results are labeled explore or exploit and split into correlational and (owner-declared) randomized. Too few posts gives `insufficient_data`, never 0. Readout labels and the strict sign-test gate are unchanged.

```bash
reachweaver baseline-eval --account my-bluesky --timezone Europe/Rome \
  --source native-export --metric clicks --denominator impressions \
  --since 2026-09-01 --until 2026-10-31 --slot exploit:Tue@09:00 --slot explore:Sat@11:00
```

See [timing evaluation and limits](docs/TIMING-EVALUATION.md) and [calendar planner](docs/CALENDAR-PLANNER.md). `native-export` is a user-provided provenance label, not verification that analytics really came from the platform. Use experimental human-reviewed slots; the planner is **not** automatically retrained or scheduled by this result.

## Social algorithms and best-time scheduling research (2026-10-08)

The Claude Code skill now includes [platform algorithm/discovery guidance](.claude/skills/reachweaver/references/platform-algorithms.md), an [evidence-aware scheduling method](.claude/skills/reachweaver/references/timing-intelligence.md), and a [dated native-scheduling comparison](docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). These distinguish provider scheduling UIs from authorized publishing APIs and the existing local outbox, including a platform-by-platform status matrix, audience-local timing experiments and a reviewable calendar schema.

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

For platforms without a useful population-time prior (including Mastodon, Telegram and YouTube), provide account-selected slots such as `--slot 'Tue@10:00' --slot 'Thu@16:00'`. See [calendar planner quickstart](docs/CALENDAR-PLANNER.md) and [algorithm/scheduling evidence](docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). This feature **does not** learn an optimal time from connected analytics, automatically obtain credentials, run a daemon or guarantee visibility.

To plan from your own audience data, copy the hour-of-week "most active times" numbers from a native analytics dashboard you can see into ReachWeaver's audience-activity CSV and pass `--audience-activity FILE`. The CSV is **ReachWeaver's own interchange format**: no real platform export has been verified against it, and [examples/audience-activity.example.csv](examples/audience-activity.example.csv) is synthetic. The top activity hours become candidate slots. Every proposed entry carries provenance (`first_party_audience_activity`, the export's `source` and `as_of`), and the output warns when `as_of` is older than 90 days. Hours you did not export stay missing instead of being treated as zero. Without the flag the output is unchanged. The planner stays advisory: it never approves or schedules anything on its own.

## Guarded scheduled delivery and queue diagnostics (Issue #11 slice)

A guarded live send now has conservative **per-account rolling 24-hour limits (default 2 attempts)** and a **60-minute minimum gap**, both checked atomically in the SQLite outbox. Past uncertain attempts still count even after restart/reconciliation. These are ReachWeaver limits, **not** cost controls.

**Provider rate limits (owner-approved 2026-10-08).** When a provider answers 429, ReachWeaver records a per-account cooldown from its `Retry-After` (or Telegram `retry_after`, Mastodon `X-RateLimit-Reset`, LinkedIn's daily reset, otherwise 15 minutes), clamped to 60 seconds–24 hours. Until it ends, that account's other posts are `SKIPPED` before any request and stay approved. The post that got the 429 goes to `needs_review` and is **never resent automatically**; `dead-letter` labels it `certain_not_sent`, `likely_not_created` or `ambiguous` to guide the human check. Only the read-only identity check before the claim may be retried (twice at most, never Bluesky). `health`, `queue-health` and the dry run show active cooldowns. `config/caps.json` version 2 adds tighten-only per-provider budgets; version 1 files still work. See [provider rate limits](docs/WORKER-OPERATIONS.md#provider-rate-limits-and-cooldowns-issue-11). Since 2026-10-09 (owner-approved), Postiz-backed accounts that declare the same `postiz_workspace` share one 429 cooldown, and Bluesky `connect-check`/publish refuse before any request once the local `createSession` counter reaches half of Bluesky's documented 30 per 5 minutes or 300 per day (`bluesky_create_session_budget_exhausted`); see account onboarding on [shared Postiz workspaces](docs/ACCOUNT-ONBOARDING.md#account-and-environment-separation) and the [identity check](docs/ACCOUNT-ONBOARDING.md#1d-verify-identity-explicit-online-never-posts). All of this is mock-tested; no real provider 429 has been observed. The new offline `queue-health` command reports overdue local schedules, stale sending, `needs_review`, pause state and account attempt totals without connecting to social platforms.

```bash
reachweaver queue-health
reachweaver publish --scheduled-only --max-lateness-minutes 180 # DRY RUN
# Only after the owner's supervised live pilot, authorization and host credential setup:
# REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --scheduled-only --max-lateness-minutes 180 --max-per-24h 2 --min-gap-minutes 60
```

`reachweaver health` gives one offline readiness view per configured account (adapter, registry age, credential variable names, LinkedIn version and authorization expiry, Postiz, queue, rolling budget, pause, live opt-in and posts needing re-approval) and exits 1 when anything is `blocked`.

**Approvals are bound to the exact payload (issue #7).** `approve` stores a SHA-256 of the account, platform, exact text, schedule time, source URL and campaign (binding tag `reachweaver.storage.approval/1`), plus any image attachments (`approval/media-1`), video record (`approval/video-1`) or reply target (#17). A post that changes after approval is never sent: it stays approved/scheduled and is `BLOCKED` before any network call. **Upgrading:** posts approved or scheduled before this release are not grandfathered. `health` lists them as `approval_rebind_required`; re-approve each one with `reachweaver unapprove POST_ID`, `reachweaver show POST_ID`, `reachweaver approve POST_ID` (then `schedule` again). See the [upgrade note](docs/WORKER-OPERATIONS.md#upgrade-note-approval-payload-binding-issue-7). The hash detects unsanctioned edits; it does not prove a human ran `approve` (threat model T7/T8). `reachweaver dead-letter` groups `needs_review` posts by sanitized error code with reconcile steps. An optional `config/caps.json` (see `config/caps.example.json`) sets per-account caps that can only **tighten** the `--max-per-24h`/`--min-gap-minutes` values. Built-in tighten-only pilot caps (`caps.BUILTIN_PROVIDER_CAPS`, owner decisions D19-4 and D21-5, 2026-10-09) apply with or without a caps file: Threads accounts at most 2 sends per rolling 24 hours, Facebook Page accounts at most 2 per 24 hours and at least 60 minutes apart (Postiz-backed ones included), so a looser flag or file value cannot exceed them. A local `config/optout.json` lists handles or domains that are never replied to, mentioned or tagged ([opt-out list](docs/PRIVACY-LIFECYCLE.md#opt-out-list)); `draft` and `approve` refuse such mentions, and `publish --live` withholds an already-approved post before claiming it. For revoked permissions and API version sunsets, follow the [runbook](docs/WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets).

An unattended worker must use `--scheduled-only` to skip approved-but-unscheduled posts and missed stale time windows. The [worker runbook](docs/WORKER-OPERATIONS.md) includes **inert, disabled-by-default** systemd service/timer examples. No actual worker or account has been deployed by this repository, and uncertain sends still require human reconciliation.

## Optional Postiz backend (Issue #30 slice, mock-tested)

An account can opt in to `"backend": "postiz"` so an approved, due text post is sent through your own Postiz instance or Postiz Cloud (Bluesky, Mastodon, Telegram, Threads, LinkedIn, Facebook only). ReachWeaver stays the approval authority: same `--live` + `REACHWEAVER_ALLOW_LIVE=1` + `REACHWEAVER_ENV=prod`, pause, budgets and durable claim. A read-only integration check runs before the claim, and the key comes only from `{env_prefix}_POSTIZ_API_KEY`. **A Postiz acceptance is not a platform receipt:** the post goes to `needs_review` until you check the native post and `reconcile`. Reddit and X are refused. Postiz is AGPL-3.0; ReachWeaver only calls its HTTP API. No Postiz account has been connected or live-tested. See [docs/POSTIZ.md](docs/POSTIZ.md).

## Ranked publishing opportunities from your own sources (Issue #9 slice)

`sources-scan` reads an opt-in list of GitHub releases, tags, merged PRs and labelled closed-as-completed issues, RSS/Atom feeds, website or Keep a Changelog changelogs, and Crossref DOI records. It ranks them as `SourceSignal`s with evidence URLs, freshness, novelty, audience relevance and a transparent score breakdown. Bot PRs, trivial PRs, drafts, private sources, reposts and items you have already seen are held back. By default it reads only local fixtures. `--online` makes explicit, GET-only requests to public GitHub repositories (paginated, capped by `max_pages`), https feeds and changelogs (after a robots.txt check), and the Crossref API. Fetched text is untrusted data, and the command never drafts or publishes. A private GitHub repository is read only with three opt-ins (`visibility` and `sensitivity` both `private`, plus `--allow-private`). This was owner-approved on 2026-10-09 (proposal). Its signals are marked private and are never public evidence or public links until a person marks each evidence record `publishable`. With `--sources CONFIG`, `draft` (reply drafts too), `media-attach`, `youtube-attach` and `approve` also refuse (and `check` reports as `BLOCKED`) a link into a `visibility: private` repository of that config (`private_source_link`). **Without `--sources` these commands do not know the private repositories and cannot detect such a link**, so pass it whenever a private source is configured. `signal-brief` then turns one saved signal into a new campaign scaffold plus a `.brief.md` sidecar: evidence URLs with an untrusted excerpt, one `{{write: ...}}` placeholder variant per approved account with a live or manual path, and the per-account length limits. It generates no copy, and the scaffold stays BLOCKED until a person writes every placeholder and verifies every source. See [docs/SOURCES.md](docs/SOURCES.md).

```bash
reachweaver sources-scan --config examples/sources.reachweaver.json --top 5
reachweaver sources-scan --config examples/sources.reachweaver.json --json > scan.json
reachweaver signal-brief --scan scan.json --signal-id SIGNAL_ID --brand examples/brand.reachweaver.json \
  --accounts config/accounts.example.json --out private/campaign.json
```

## Security and reality checks

**What this plugin runs, reads and sends (disclosure for plugin reviewers and users):**

- **On session start** one hook runs `update --notice`: a single HTTPS GET to github.com at most every 24 h to look for a newer release. When a newer release exists it also reads that release's `CHANGELOG.md` (same host family, one more GET) and prints a short "What changed" list of release-note titles between your version and the latest. It sends no data from your machine, is silent offline, never updates by itself, and is disabled by `touch ~/.cache/reachweaver/no-update-check`.
- **Everything else is local and offline by default.** Planning, drafting, validation, `publish` without `--live` and `manual-export` never open a network connection. Local state stays in `state/` and your ignored account config.
- **Credentials** are read only from `REACHWEAVER_*` environment variables you set yourself, only at send time, and are used only against that platform's own API host (for example Bluesky, Mastodon, Telegram, LinkedIn). Regression tests assert they never appear in exports, reports, health output, dead letters or logs. Live sending additionally needs `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod`, an approved due post and an explicit `--live`. Claude does not send posts on its own.
- **Optional online checks** (identity preflight, `landing-check --online`) run only when you invoke them explicitly.

- Only human-authenticated, permitted accounts. User completes MFA, official consent and app approvals.
- Credentials loaded from environment at send time; account metadata only in ignored local JSON. `credentials-audit` checks offline for placeholder, orphaned, reused or colliding credential variables and for literal secrets in a shell history file you name, printing variable names and line numbers only. See [account onboarding](docs/ACCOUNT-ONBOARDING.md) and the threat model.
- Never commit secrets, cookies, sessions, analytics containing personal data, private posts or confidential research.
- No scraping/bypassing CAPTCHAs, fake engagement, mass unsolicited outreach, purchased views or guaranteed algorithms.
- GitHub Actions only tests the code. It does NOT publish posts; ephemeral runner storage cannot provide a safe durable outbox.
- Content, view metrics and claims require first-party evidence. Missing metrics are not zero.

Local data lifecycle (offline): `privacy-status`, `data-export --out FILE` and `data-purge --policy config/retention.json` (dry run; `--apply` required) — see [privacy lifecycle](docs/PRIVACY-LIFECYCLE.md). Not legal advice.

See [privacy policy](docs/PRIVACY.md), [security](docs/SECURITY.md), [architecture](docs/ARCHITECTURE.md), [platform matrix](docs/PLATFORM-MATRIX.md) and [research](docs/UPSTREAM-RESEARCH.md).

## Documentation index

| Start here | Operate safely | Reference |
|---|---|---|
| [Quickstart](docs/QUICKSTART.md) | [Security checklist](docs/SECURITY-CHECKLIST.md) | [Architecture](docs/ARCHITECTURE.md) |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | [Security policy](docs/SECURITY.md), threat model | [Platforms and capability registry](docs/PLATFORMS.md) |
| [Changelog](CHANGELOG.md) | [Account onboarding](docs/ACCOUNT-ONBOARDING.md), [first live pilot](docs/FIRST-LIVE-PILOT.md) | [Schemas](docs/SCHEMAS.md), [manual export](docs/MANUAL-EXPORT.md) |
| [Contributing](CONTRIBUTING.md) | [Worker operations](docs/WORKER-OPERATIONS.md), [privacy lifecycle](docs/PRIVACY-LIFECYCLE.md) | [Release playbook](docs/RELEASE-PLAYBOOK.md), [field pilot protocol](docs/FIELD-PILOT-PROTOCOL.md) |
| [Platform matrix](docs/PLATFORM-MATRIX.md) | Platform guides: [LinkedIn](docs/LINKEDIN.md), [Mastodon](docs/MASTODON.md), [Postiz](docs/POSTIZ.md) | Disabled adapters: [Threads](docs/THREADS.md), [Facebook Page](docs/FACEBOOK.md), [Instagram](docs/INSTAGRAM.md), [YouTube](docs/YOUTUBE.md), [TikTok](docs/TIKTOK.md) |

**Every blocked platform has an alternative.** Platforms without a live adapter get a `manual-export` creator package, `campaign-export` drafts, or an explicit human-only or unsupported marker; Reddit and Hacker News stay human-only. The generated table is in [troubleshooting](docs/TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives), and the release gate fails if a new blocked platform has none.

## Which social networks to prioritize for real attention

ReachWeaver now includes an evidence-dated [channel choice framework](.claude/skills/reachweaver/references/channel-selection.md) and [platform-specific growth playbooks](.claude/skills/reachweaver/references/platform-playbooks.md). A larger October 2026 open-source ecosystem audit compares leading social/marketing skills with schedulers, licenses and safe integration decisions.

- For **AI, Kubernetes, open-source and research**: start with LinkedIn plus original YouTube demonstrations. Use Reddit and Hacker News for **selective, genuinely human-written community activity** and technical feedback, not autonomous distribution.
- For **broader consumer attention**: reuse **original** demonstration footage as Instagram Reels, YouTube Shorts and TikTok-native edits if video-production capacity exists; then consider Threads/X/Bluesky for written conversations.
- For **retention**: opt-in Telegram/Discord/newsletter; these are generally not substitutes for public discovery.
- **Data caveat:** global monthly users, registered members, weekly users and Shorts video plays are fundamentally different. No channel guarantees views; measure first-party qualified outcomes.

Example Claude Code request:

> /reachweaver — Audit the distribution options for my public GitHub release. Compare high-volume reach with technical audience fit using official 2026 sources. Prioritize two channels, give channel-native proof-backed drafts, explain platform-specific restrictions, and design a 14-day measured experiment. Do not auto-publish.

### Install and update

**Recommended: Claude Code plugin** (Claude Code manages install and updates; needs the repository to be reachable by git):

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

Use it as `/reachweaver:reachweaver` or just ask. A `SessionStart` hook runs `update --notice` (one GET to github.com at most every 24 h, silent offline) and Claude tells you when a newer release exists. To update: `/plugin marketplace update reachweaver`, then `/plugin update reachweaver@reachweaver`, then `/reload-plugins` (or turn on auto-update for the marketplace under `/plugin`). The plugin is the lean `plugin/` folder (manifest, hook, README, LICENSE and a generated copy of the skill bundle; `python3 scripts/sync_skill.py` refreshes it), not the whole repository, so an install copies only what the plugin needs. Bump `version` in `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` with every release (a test checks it matches `reachweaver.__version__`).

**Alternative: standalone installer** (no plugin system; copies the skill into `~/.claude/skills`):

```bash
# Latest GitHub Release, checksum-verified, into ~/.claude/skills/reachweaver
curl -fsSLO https://raw.githubusercontent.com/MSKazemi/reachweaver/main/scripts/install_skill.py
python3 install_skill.py            # add --project to install into ./.claude/skills instead
# From a clone, no network: python3 scripts/install_skill.py --from-checkout
```

Restart Claude Code and use `/reachweaver`. The skill checks for a newer release at the start of a session (at most one GET per 24 h, silent offline; opt out with `touch ~/.cache/reachweaver/no-update-check`; `update --apply` only applies to this installer route, never to a plugin), tells you, and updates only after you agree. By hand: `python3 ~/.claude/skills/reachweaver/scripts/run.py update --check` then `update --apply --yes`. The installer and updater verify the release's SHA-256, refuse unsafe archive paths and keep the previous copy under `~/.cache/reachweaver/previous/`. Releases come from the `release.yml` workflow on a `vX.Y.Z` tag; until the first release is tagged (and while the repository is private) use `--from-checkout`.

### Installable skill ZIP from GitHub Actions

Every successful **main-branch** CI run packages the complete `.claude/skills/reachweaver` directory (including its executable offline runtime, references and assets) as the `reachweaver-claude-skill` artifact containing `skill.zip`. Open the run under [GitHub Actions](https://github.com/MSKazemi/reachweaver/actions), download the **trusted main-branch artifact** while it is retained, and inspect before installing. Do not install ZIPs from untrusted pull requests; no account credentials are inside this bundle.

For normal use, cloning this repository and invoking `/reachweaver` from Claude Code also uses the current skill without needing any ZIP.

## DEV Community: build technical authority without spam

**[DEV authority and collaboration playbook](docs/DEV-AUTHORITY-PLAYBOOK-2026-10-08.md)** is a dedicated operating guide for creating a credible profile, discovering engineering readers, human-written comments, one evidence-rich article per week, selecting up to four relevant tags, using DEV's native editor scheduler, and measuring qualified GitHub/collaboration outcomes. Use the bundled Claude Code specialist reference at `.claude/skills/reachweaver/references/dev-community.md` and the portable human-author brief at `.claude/skills/reachweaver/assets/dev-article-brief.md`.

**Critical limits:** DEV prohibits AI-generated comments/replies (apart from basic grammar/translation/accessibility assistance) and restricts AI-generated promotional/clout/backlink articles; read [official rules](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/). DEV has [native article scheduling](https://dev.to/help/writing-editing-scheduling), but **ReachWeaver has no DEV adapter, scheduled publishing receipt, account connection or native analytics fetch**. For this channel, prepare an evidence inventory, author-edited brief and manual scheduling handoff—not fabricated automation.

Claude Code example:

> /reachweaver — Audit my public DEV profile and my three best engineering posts. Recommend prioritized, evidence-checked profile improvements; a 30-day human-owned technical writing and community participation plan; a DEV native-scheduling checklist; and first-party metrics for GitHub contributors and real collaborations. Do not generate DEV comments or automatically publish.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/sync_skill.py    # keep standalone skill CLI in sync with canonical code
python3 scripts/secret_scan.py   # tracked-file secret scan; fixtures allowlisted in .secret-scan-allow
python3 scripts/lint.py          # stdlib syntax + JSON/JSONL lint
python3 scripts/check_runtime_deps.py  # runtime must stay stdlib-only
make check                       # all of the above (sync, scan, lint, deps, test)
make typecheck                   # pinned mypy via pipx (CI runs it too)
make release-gate                # offline versioned release gate: versions, CHANGELOG, docs, bundle, blockers, readiness, tests
python3 scripts/release_gate.py --scorecard [--json]  # per-platform/area release-readiness scorecard
python3 scripts/pilot_benchmark.py [--posts N] [--json]  # offline mock-only harness skeleton for the pilot report; synthetic, not pilot evidence
# When using the skill-creator validator in a compatible environment, validate and package the skill.
```

CI runs pure offline tests only, plus a hygiene job (secret scan, stdlib lint, compileall, pinned ruff hard-error rules, runtime-dependency guard, pinned mypy). Runtime and tooling decisions are recorded in ADR-0002. See [contributing](CONTRIBUTING.md) for fresh-clone commands, [security](docs/SECURITY.md#reporting-a-vulnerability-private) for private vulnerability reporting, and the [roadmap](docs/ROADMAP.md).

## Relationship with existing projects

- **MSKazemi/novareach**: strategy and storytelling across projects; do not duplicate marketing positioning.
- **MSKazemi/seo**: SEO, attribution, broader acquisition intelligence and social distribution analysis.
- **ReachWeaver**: actionable, permissioned social content operations with data/receipts.

Copyright 2026 Mohsen Seyedkazemi Ardebili. Original implementation released under the [Apache License 2.0](LICENSE); redistributions must keep the [NOTICE](NOTICE) file. upstream inspirations are linked and attributed without copying their source. See [research](docs/UPSTREAM-RESEARCH.md).
