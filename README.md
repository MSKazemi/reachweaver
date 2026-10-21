# ReachWeaver

<img src="plugin/.claude-plugin/icon.png" alt="ReachWeaver icon: Persian-inspired turquoise and gold geometric knot" align="right" width="112" height="112">

**A Claude Code social media skill that helps you plan, write and safely publish posts about your real work, one human-approved post at a time.** Give it a release, a paper or a demo. It drafts a post that fits each platform, shows you exactly what would be sent, and sends nothing until you say yes.

> **Status: experimental** (release history in [CHANGELOG.md](CHANGELOG.md)). Planning, drafting, checking and previewing work fully offline. For real sending, text-only adapters exist for Bluesky, Mastodon, Telegram and LinkedIn personal profiles, and one approved text post each was sent through ReachWeaver to Bluesky and Mastodon on 2026-10-11 (first-party receipts in [docs/receipts/](docs/receipts/)); Telegram and LinkedIn have had no live send and no other platform has a receipt. Adapters for Threads text, Facebook Page text/link, Instagram single images, YouTube and TikTok video, Mastodon images and Bluesky/Mastodon replies exist too, but they are tested against simulated servers only and stay switched off until the owner records a supervised live post. For those platforms, and all others, ReachWeaver prepares a draft or a package that you post by hand (`manual-export`). This is not a promise of viral growth.

**New here? Start here:** [quickstart](docs/QUICKSTART.md) (10 minutes, no account needed) · [plain-language guide](docs/START-HERE.md) · [glossary](docs/GLOSSARY.md) · [troubleshooting](docs/TROUBLESHOOTING.md) · [security checklist](docs/SECURITY-CHECKLIST.md) · [all docs by goal](docs/README.md) · [changelog](CHANGELOG.md) · [contribute](CONTRIBUTING.md) · public-release safety checklist (maintainers)

> **Independent-use and liability notice.** ReachWeaver is experimental, user-operated source code provided **"AS IS"** under Apache-2.0 Sections 7–9, with **no warranty** except as required by applicable law or agreed in writing. The original contributors do not direct, authorize or supervise independent users' posts, misuse, accounts, modifications or third-party services merely by publishing this code; each operator must review their own permissions and actions. Neither this notice nor the license guarantees immunity from non-waivable liability. ReachWeaver is not affiliated with Anthropic or the listed platforms. Read the [full responsible-use and liability notice](DISCLAIMER.md), [privacy policy](docs/PRIVACY.md) and [security policy](SECURITY.md).

## What is ReachWeaver?

Think of it as a careful assistant for sharing your work online. You bring something real that you made. ReachWeaver, together with Claude Code, helps you:

1. **Find the angle**: who might care, and which networks suit them.
2. **Write drafts** that fit each network (a LinkedIn post is not a Bluesky post).
3. **Check them**: length, links, placeholders, claims that need a source.
4. **Preview** exactly what would be sent, to which account, and when.
5. **Send only after you approve** that exact text, or export it so you post it by hand.
6. **Learn** from the real numbers you import afterwards.

ReachWeaver itself runs on your own computer and keeps its records there, in a small private database. It has no account or server of its own.

## What it will never do

- It never posts without your approval of that exact text. Change one letter after approving and the approval no longer counts.
- It never lets you approve the same video, image or wording twice for the same account on a platform by accident: `repeat-check` compares each draft with your history first, and you must pass `--allow-repeat` on purpose ([repeat guard](docs/REPEAT-GUARD.md)).
- It never sends anything by default. Every command is a **dry run** (a rehearsal that contacts no one) unless you add the live switches on purpose.
- It never stores your passwords. Account details go in an ignored file with **no secrets**, and credentials are read from your own environment only at the moment of sending.
- It never buys views, fakes engagement, scrapes, bypasses CAPTCHAs or mass-messages strangers.
- It never writes for Hacker News or Reddit. Those stay human-only.
- It never resends a post when it is unsure whether the first attempt worked. It stops and asks you to look.

## Which platforms work today?

| What you want | Today |
|---|---|
| Plan, draft, check and preview, with or without accounts | **Works offline** on any computer with Python 3.11 or newer |
| Send a text post to **Bluesky** or **Mastodon** | **Works**: one real post each was sent on 2026-10-11 ([receipts](docs/receipts/)) |
| Send a text post to **Telegram** (channel) or **LinkedIn** (personal profile) | Built and tested with simulated servers; **no real send yet**, so try it on a test account first |
| Threads, Facebook Pages, Instagram, YouTube, TikTok | **Manual**: ReachWeaver prepares a package and a checklist, and you post in the app |
| X, Pinterest, DEV, Hashnode and dozens of other networks | **Manual**: drafts or a research plan; you post |
| Reddit, Hacker News | **Human-only**: you write and post, ReachWeaver writes nothing |

The full, dated table is the [platform matrix](docs/PLATFORM-MATRIX.md). The list of blocked platforms and what to do instead is in [troubleshooting](docs/TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives).

## How a post travels

```text
idea or source -> draft -> check -> YOU approve -> dry-run preview -> (optional) send -> receipt
                                       ^                                   ^
                          approval is tied to the           needs --live, REACHWEAVER_ALLOW_LIVE=1
                          exact text and account            and REACHWEAVER_ENV=prod, all on purpose
```

Nothing to the right of **YOU approve** can happen without you. The [plain-language guide](docs/START-HERE.md) walks through each step with the commands.

## Get started

You need **Python 3.11 or newer**. There is nothing else to install: ReachWeaver uses only Python's standard library.

### Easiest option: Share a post in your browser

You do not need to know JSON, use the terminal for every post, or connect any social account. Start the **local browser Share Composer** with:

```bash
python3 -m reachweaver share --web --open
```

Write one caption, select social networks, preview all drafts and save private handoff files. Open each app and publish **manually**. Nothing is sent to a social platform and no paid service, new API key or server hosting is required. See [Browser Share Composer](docs/BROWSER-SHARE-COMPOSER.md).

### Easiest option: Share a post without connecting an account

You can prepare captions for Instagram, Threads, TikTok, YouTube, LinkedIn, Bluesky, Mastodon, Telegram and many other networks, then paste them in the apps yourself. Each network gets its own copy, shortened at a word boundary (and clearly marked SHORTENED) only when it is over that network's limit, and an attached photo or video is checked offline against the sizes, durations and aspect ratios ReachWeaver has sources for. No JSON, password, account connection, subscription or API key is required:

```bash
python3 -m reachweaver share --guide
```

Or prepare two destinations at once:

```bash
python3 -m reachweaver share --platform instagram,threads --text "Here is our latest demonstration!" --out state/my-first-share
```

Open the resulting **OPEN-FIRST.md** file. It tells you which caption to copy, which app to open and what to check before posting. Media files are checked from their headers, never copied or uploaded. **This is manual preparation only: it never publishes or schedules posts.** See the [easy sharing guide](docs/EASY-SHARING.md), including photos, videos, YouTube titles and human-only community rules.

### Option 1: with Claude Code (recommended)

Install the plugin once, then just describe what you want:

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

Then ask, for example:

```text
/reachweaver
Analyze my latest GitHub release and create a two-week evidence-based social campaign
for a technical audience. Make distinct LinkedIn, Bluesky and YouTube Shorts drafts.
Explain what can be published automatically, prepare a dry run, and wait for approval.
```

Claude does the research and writing inside your Claude Code session, on your own subscription: ReachWeaver itself makes no model calls and needs no API key. Its commands do the checking, approval and record keeping. See [using ReachWeaver with Claude Code](docs/CLAUDE-CODE.md) for the first session, who does what, and how to keep usage low. Not sure which install route fits you? See [how to install: pick one route](docs/INSTALL.md). If you will publish for real, also run `pipx install reachweaver` so you have the `reachweaver` command in your own terminal.

### Option 2: try it on your computer first, with no account

```bash
git clone https://github.com/MSKazemi/reachweaver.git
cd reachweaver
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json doctor
```

You should see your example accounts listed with their credential names reported as **missing**, and the line `Live opt-in: disabled`. That is the correct, safe starting state. The [quickstart](docs/QUICKSTART.md) continues from here through a full draft, approval and dry-run preview. It takes about ten minutes and cannot publish anything.

### Option 3: set up your own accounts

Only when you are ready, and only for accounts you control:

```bash
python3 -m pip install -e .            # optional: gives you a plain `reachweaver` command
reachweaver account-add bluesky        # asks for your public handle only; never a password or token
cp config/accounts.example.json config/accounts.json   # or copy the example and edit it: NO passwords or tokens
reachweaver doctor                     # offline; lists which credential names are still missing
reachweaver init                       # creates your private local database
reachweaver connect                    # offline status table: which accounts still need credentials
reachweaver connect --guide            # you at the terminal: walks through the accounts one at a time
```

`connect --guide` shows, for each account, the official page to sign in on and the steps to create a limited credential. You sign in, solve any verification or robot check yourself, and keep the credential in your own environment. It never asks for a password, never drives a browser, never posts, and contacts a platform only after you type `y` for that one account. Publishing still needs your approval of every post.

Running more than one identity on a network (a work and a family Instagram), or missing an account on a network? Keep a private [profile](docs/PERSONAS-AND-ACCOUNT-KIT.md): `reachweaver profile --init`, then `--persona work` on `draft` refuses the wrong account and names the one to switch to, and `reachweaver account-kit --platform weibo --persona work` prepares your sign-up details in the platform's language behind one approval. You still create each account and complete every verification yourself.

Credentials are set privately in your own environment, never typed into Claude, a chat or an issue. [Account onboarding](docs/ACCOUNT-ONBOARDING.md) explains how, one platform at a time. Then follow the [security checklist](docs/SECURITY-CHECKLIST.md) and the [first live pilot](docs/FIRST-LIVE-PILOT.md) (for Bluesky: the copy-and-paste [one-post pilot](docs/BLUESKY-PILOT.md)). The safest first real send is one text post on a throwaway account.

## The five commands you will use most

| I want to... | Command | What happens |
|---|---|---|
| See what is set up | `reachweaver doctor` | Lists accounts and missing credential names. Offline. |
| Write a draft | `reachweaver draft --account my-bluesky --file examples/example-post.txt` | Saves a draft. Sends nothing. |
| See exactly what would be sent | `reachweaver show POST_ID` | Prints the exact text, account, status and preview. |
| Approve that exact text | `reachweaver approve POST_ID --approver YOUR_NAME` | Ties your approval to the exact text. Still sends nothing. |
| Rehearse the send | `reachweaver publish --post-id POST_ID` | Prints `DRY RUN` and contacts no one. |

Several drafts waiting? `reachweaver review` walks them one by one in your terminal: it shows each one and asks you to approve, skip, cancel or quit, so you never copy a post id by hand. Stuck or unsure? `reachweaver pause` stops all publishing until you run `reachweaver resume`, and `reachweaver cancel POST_ID` withdraws a post you no longer want. `reachweaver --help` (or `reachweaver help`) prints the 16 everyday commands with one plain line each; `reachweaver --help all` lists every command by task, and `reachweaver help COMMAND` explains one. Every command is listed in the [command reference](#command-reference) below.

## Where to read next

| If you want to... | Read |
|---|---|
| Understand the ideas in plain words | [Start here guide](docs/START-HERE.md), [glossary](docs/GLOSSARY.md) |
| Run a safe first preview | [Quickstart](docs/QUICKSTART.md) |
| Fix an error message | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Connect a real account | [Account onboarding](docs/ACCOUNT-ONBOARDING.md), [security checklist](docs/SECURITY-CHECKLIST.md), [first live pilot](docs/FIRST-LIVE-PILOT.md) |
| See what each platform supports | [Platform matrix](docs/PLATFORM-MATRIX.md), [platform guides](docs/PLATFORMS.md) |
| Post to a network ReachWeaver cannot send to | [Manual export](docs/MANUAL-EXPORT.md) |
| Plan a campaign from evidence | [Schemas](docs/SCHEMAS.md), [campaign preview](docs/CAMPAIGN-PREVIEW.md), [claim grounding](docs/CLAIM-GROUNDING.md), [release to draft](docs/RELEASE-TO-DRAFT.md), [review packets](docs/REVIEW-PACKET.md) |
| Pick channels for a region or goal | `reachweaver distribution-plan`, [global strategy](docs/GLOBAL-DISTRIBUTION-2026-10-08.md) |
| Reach the fediverse and Bluesky without double posting | `reachweaver social-web-plan`, [open social web](docs/OPEN-SOCIAL-WEB.md) |
| See how much of your reach depends on one platform | `reachweaver platform-risk`, [platform risk](docs/PLATFORM-RISK.md) |
| Measure what worked | [Attribution](docs/ATTRIBUTION.md), [growth experiments](docs/GROWTH-EXPERIMENTS.md), [weekly report example](docs/WEEKLY-REPORT-EXAMPLE.md) |
| Run it unattended on a server | [Worker operations](docs/WORKER-OPERATIONS.md) |
| Understand how it works inside | [Architecture](docs/ARCHITECTURE.md), [security policy](docs/SECURITY.md) |
| Browse every feature | [All features in detail](docs/FEATURES.md) |

## Algorithm-aware discovery and relevant hashtags

`reachweaver discovery-plan --file examples/discovery-plan.example.json --json` proposes **relevant niche, core and broad hashtags**, native Threads topic labels, and optional dated, owner-researched trends for the post's actual topic. Its 16 platform-specific guidance profiles describe verified signal *families* (not secret ranking weights), useful creative checks and first-party measurements. For YouTube it prioritizes title, thumbnail, viewer satisfaction and topic terms over tag stuffing. Unrelated, stale, region-mismatched or inadequately sourced trends are excluded. The score is editorial text relevance, **not predicted reach**. There is no real-time trend API, no new paid service, no account connection and no automatic posting. See [discovery optimization](docs/DISCOVERY-OPTIMIZATION.md).

## More than posting

ReachWeaver also has offline tools that never touch an account. Each is covered in [all features in detail](docs/FEATURES.md):

- **Next actions**: `reachweaver suggest` turns your profile checks, imported metrics and open safety issues into one ranked to-do list. It never guesses rankings or predicts likes.
- **Profile and post audits**: `profile-audit` and `post-audit` check each network on its own terms (LinkedIn, YouTube, Instagram, DEV, Mastodon, Reddit and more), and `peer-benchmark` compares public counters you copied by hand.
- **Worldwide channel research**: `distribution-plan` ranks channels for a region and goal. The registry catalogs **110 channels across 18 market pilots**. It is a research hypothesis, never a promise of traffic and never permission to post.
- **Posting journal and archive**: `journal` lists what you posted, when, on which account, with the exact text, who approved it, the link and the files; `archive` writes it to a private folder with copies of the files. See the [journal guide](docs/JOURNAL.md).
- **One folder for everything that is yours**: `reachweaver workspace --init DIR` makes a single folder (config, database, approval ledger, archive, media, logs; no credentials). To use another computer, install ReachWeaver there, copy the folder, and run `reachweaver workspace --repair --apply`. See [your workspace folder](docs/MOVING.md).
- **Repeat guard**: `repeat-check` and `mark-shared` stop you posting the same video, image or wording twice. Record what you posted by hand with `mark-shared`; drafts are compared with it, and with everything ReachWeaver has sent, before approval. See the [repeat guard guide](docs/REPEAT-GUARD.md).
- **Campaigns from evidence**: `campaign-check`, `campaign-preview`, `claims-check` and `campaign-export` keep every claim tied to a source.
- **Analytics you import yourself**: `import-metrics`, `report`, `engagement-triage` and `experiment-*` help you learn from real numbers. Missing numbers are shown as unknown, never as zero.

## Safety in one page

- **Approval is per post and tied to the exact text.** A plan, a campaign file, a calendar or a Claude Code session is never approval.
- **Dry run is the default.** A live send needs `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`, and only on the machine you publish from.
- **Credentials stay in your environment**, never in a file in this repository, never in chat. `reachweaver credentials-audit` checks for leaks and prints names only.
- **One bad send never snowballs.** If the result of a send is unknown, that account is held until you check the real account and run `reconcile`.
- **You can always stop.** `pause` is a single emergency switch; `disconnect --account ID` stops one account.
- **Your data stays on your computer** in `state/`. See the [privacy policy](docs/PRIVACY.md).

The detailed disclosure of what this tool reads, runs and sends is in the next section.

## Security and reality checks

If you read only one section before connecting a real account, read this one. It states exactly what ReachWeaver reads, runs and sends.

**What this plugin runs, reads and sends (disclosure for plugin reviewers and users):**

- **On session start** one hook runs `update --notice`: a single HTTPS GET to github.com at most every 24 h to look for a newer release. When a newer release exists it also reads that release's `CHANGELOG.md` (same host family, one more GET) and prints a short "What changed" list of release-note titles between your version and the latest. In each version, Security entries come first, then entries that name a platform in your local accounts config, marked `(for you: bluesky)`; only the `platform` field of each account is read, never a handle, env prefix or credential, and the same caps and character allowlist apply. It sends no data from your machine, is silent offline, never updates by itself, and is disabled by `touch ~/.cache/reachweaver/no-update-check`.
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

## Command reference

Every command is listed here with its options. Most people only need the five in [the commands you will use most](#the-five-commands-you-will-use-most). Commands that can send something say so; everything else is offline. Open the list below.

<details>
<summary>Show all commands and options</summary>


```text
reachweaver init                         local DB initialization
reachweaver doctor                       check account mapping / missing env names
reachweaver credentials-audit [--history FILE] [--json]  offline credential hygiene: names only, never values; exit 1 on findings
reachweaver workspace [--json]  read-only: what lives in this folder (config, database, approval ledger, archive) and what does not (credentials, the installed skill); database integrity, ledger consistency, credential NAMES missing here, attachment files missing, changed or outside the folder, a secrets file sitting in it; exit 1 when copying would not work (docs/MOVING.md)
reachweaver workspace --init [DIR]  create the one-folder layout (config/, state/, media/, logs/, marker file, README, secrets.env.example); never overwrites or moves existing files
reachweaver workspace --gather [--apply]  copy attachments of unsent posts that live outside media/ into media/ (hash-verified) and point the records at the copies; dry run unless --apply
reachweaver workspace --repair [--apply]  after copying the folder to a new computer: find attachments again in media/ by size and SHA-256 and fix the recorded paths; approval hashes stay identical; dry run unless --apply
reachweaver --home DIR ...  global option: use this workspace folder (default: the nearest folder with a .reachweaver-workspace file at or above the current one)
reachweaver media-relink --from OLD_FOLDER --to NEW_FOLDER [--apply] [--json]  after moving the workspace: point recorded attachment paths of unsent posts at the new folder, only for files whose SHA-256 still matches and only if the approval hash stays identical; dry run unless --apply
reachweaver journal [--post-id ID] [--status S]... [--all-statuses] [--platform P] [--account ID] [--since DATE] [--until DATE] [--limit N] [--no-by-hand] [--verify-files] [--format table|json|markdown|csv] [--out FILE]  read-only: what was posted, when, where, with which metadata (text, approver, link, audit timeline) and files (path, SHA-256, still on disk?); includes `mark-shared` records; the database is opened read-only (docs/JOURNAL.md)
reachweaver archive [filters as journal] [--out DIR] [--copy-files] [--json]  write a private folder (0700/0600): one sub-folder per post with post.txt, meta.json and, with --copy-files, hash-verified copies of the attached files, plus index.md/index.csv; only ever adds, never overwrites; sends nothing
reachweaver repeat-check (--post-id ID | --platform P [--account ID] [--text T | --file F] [--image F]... [--video F] [--link URL]...) [--days N] [--json]  read-only: has this platform already received the same video file, image file, wording or link (stored posts plus `mark-shared` records)? exit 1 only for a repeat that would block approval; never carries earlier text (docs/REPEAT-GUARD.md)
reachweaver mark-shared --platform P [--account ID] [--text T | --file F] [--image F]... [--video F] [--link URL]... [--url U] [--at ISO] [--note N]  record material you posted by hand so later drafts are checked against it; local ledger only, posts nothing
reachweaver idea add TEXT [--source URL] [--tag T]... [--json]  ideas inbox: save a 'post about this later' note (up to 2000 characters, https source only) in a private ideas.json next to the database; not a draft, never queued or sent (docs/CLAUDE-CODE.md)
reachweaver idea list [--status open|promoted|dropped] [--tag T] [--json] / idea drop ID  read or retire ideas
reachweaver idea promote ID --campaign NAME --out NEW_DIR [--brand B] [--accounts A] [--objective O] [--json]  NEW blocked campaign scaffold (campaign.reachweaver.json + brief.md) with the idea as the brief and placeholders; campaign-check reports it BLOCKED until written and verified; never creates a post or approval
reachweaver statusline [--home DIR] [--db PATH]  one read-only line for Claude Code's status bar (version, drafts, queued, needs review, ideas, PAUSED); never creates a database, ignores stdin, no network (docs/CLAUDE-CODE.md#status-line)
reachweaver approve POST_ID ... [--allow-repeat]  refuses a blocking repeat (same video/image file or identical wording already shared by this account on this platform) unless --allow-repeat, which records a `repeat_override` audit event
reachweaver connect [--guide] [--account ID] [--json]  experimental: offline status table of your accounts (credential variable names only); --guide walks through them one at a time with you at a terminal, one read-only identity check per account you confirm; takes no secret, never posts
reachweaver draft --account X --text Y [--optout F]   create a reviewable draft; refused if it @-mentions an opted-out handle/domain
reachweaver draft --account X --file F    create draft from UTF-8 file
reachweaver draft --account A --account B [--account ...] --text Y   one separate draft per account (all or none; refused for two accounts on the same platform or with --reply-to); each needs its own approve
reachweaver draft --account X --file F --reply-to ITEM [--online] [--override-flagged LABEL]
                                          #17 (owner-approved 2026-10-09; gated off): reply draft under your own published Bluesky/Mastodon post; plan only without --online; sending blocked by the capability registry (`reachweaver_reply_adapter: none`)
reachweaver list [--status STATUS]        display post statuses
reachweaver check POST_ID [--optout F]    draft lint, including opt-out mentions
reachweaver review-packet (--post-id ID ... | --campaign-staged NAME) --out NEW_DIR [--json]  NEW private folder for a co-reviewer: exact text, destination, alt text, content digest, check findings; database read-only; never approval (docs/REVIEW-PACKET.md)
reachweaver review-comment --packet DIR --post-id ID --reviewer NAME --verdict looks_good|change_requested|question --note TEXT  owner records a reviewer's comment in the packet's comments.json, bound to the digest; looks_good is not approval
reachweaver review-status [--packet DIR] [--json]  comments per post; STALE when the post changed after the comment; read-only
reachweaver approve POST_ID [--approver NAME] [--optout F]  approve one post; binds its payload hash and a fresh per-approval nonce (wave 9 item 1: the first send consumes it in an append-only, hash-chained approval-ledger.jsonl next to the database, so a restored older approval of the same post is refused with approval_replay_reapproval_required before any request; approvals made before the upgrade must be re-approved once); refused on an opt-out mention
reachweaver approve POST_ID --verify-identity --online  opt-in (wave 9 item 2): one read-only connect-check identity request, then bind a fingerprint of the verified account entry (account id, platform, handle/server/ids; no token or response stored) under tag approval/id-1; a failed check approves nothing; a later send whose account entry no longer matches is BLOCKED before any request (approval_identity_mismatch_reapproval_required). Binds WHICH account, not WHO approved
reachweaver review [--campaign C] [--account ID] [--approver NAME] [--optout F]  #11 (owner-approved 2026-10-21), owner only: walk the drafts one by one, oldest first, in your own terminal; each shows the show sheet and check result, then a = run approve for that one post (same checks, typed hash prefix when approval-mode confirmation is on), s = skip, c = cancel (type CANCEL), q = quit; answers come from /dev/tty, refused without a terminal; never batches, never --allow-repeat; reply drafts left out; ends with the exact ids approved, skipped, cancelled and refused (docs/RECOMMENDED-SETUP.md#approve-several-drafts-one-by-one)
reachweaver unapprove POST_ID            revoke approval (not published); records the withdrawal in approval-ledger.jsonl, so a database restored from an older backup cannot send it (approval_withdrawn_reapproval_required)
reachweaver media-attach POST_ID --file IMG (--alt TEXT | --alt-file F) [--focus x,y] [--optout F] [--rights-confirmed --license ID --created-by owner|tool|third_party [--attribution T] [--tool N] [--rights-source-url URL] [--alt-translation LANG=TEXT]...]  (#48 draft: optional bound rights record)  add one PNG/JPEG/WebP with required alt text to a Mastodon DRAFT (warns if the image carries EXIF GPS data; never strips it); approval binds its SHA-256, MIME, alt and focus; offline, never uploads (#13, owner-approved 2026-10-09; disabled until a live receipt: live image send is registry-refused, docs/SECURITY-REVIEW-PROPOSAL-13-mastodon-media.md)
reachweaver media-detach POST_ID         remove every attachment from a draft (local files untouched)
reachweaver schedule POST_ID --at ISO8601 set UTC due time
reachweaver video-attach POST_ID --file V.mp4 [--mode creator_inbox|direct_post] [--privacy-level L --commercial C] [--allow-comment] [--allow-duet] [--allow-stitch] [--aigc]  offline: bind one MP4 to a TikTok draft; approve binds its SHA-256; never uploads (issue #23, owner-approved 2026-10-09; disabled until a live receipt; docs/TIKTOK.md)
reachweaver video-detach POST_ID         offline: remove the video from a TikTok draft
reachweaver calendar-plan --account ID --timezone TZ --start D [--days 14] [--slot Wed@16:00]... [--audience-activity FILE [--audience-top-hours 5] [--audience-max-age-days 90]] [--apply]  propose audience-local slots (advisory); --audience-activity uses your own exported hour-of-week activity with per-slot provenance; [--explore-share F --explore-slot Sat@11:00...] sends floor(F x posts) posts (F at most 0.5, default 0 = off) to your explore times, labelled role=explore, never randomized; [--market US|global|tiktok@US] adds a sourced market flag for TikTok (separate US recommendation system since 2026-01-23; re-verify), never changes the slots (docs/CALENDAR-PLANNER.md)
reachweaver calendar-gaps --account ID --timezone TZ [--start D] [--days 14] [--slot Wed@16:00]... [--tolerance-minutes 90] [--max-per-day 2] [--min-spacing-hours 20] [--timing-source S --timing-metric M --timing-denominator views|impressions] [--json]  # read-only: empty and overloaded slots in the local queue; never schedules (issue #9)
reachweaver cancel POST_ID               cancel only before delivery; an approved post's withdrawal is recorded in the approval ledger like unapprove
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
reachweaver                              no command: the same guidance as start, plus a pointer to --help
reachweaver --help | -h                  the everyday commands only (start, share, idea, draft, check, review, publish, list, report, metrics-fetch, suggest, ui, connect, workspace, update, help), one plain line each
reachweaver --help all                   every command, grouped by task, plus the global options; --help COMMAND prints that command's own help
reachweaver help [all|COMMAND]           the same as --help, --help all and COMMAND --help; offline, prints text only
reachweaver start [--json]               experimental, read-only: look at your workspace, accounts file (accounts still using example placeholders are named), database, queue, ideas inbox and metric snapshots and print the exact next command to run, the browser composer first; runs nothing and never creates the database
reachweaver context [--json]             read-only session start for Claude Code in one call: version, paths, account readiness, pause, live gate, approval mode, post counts, posts waiting for you, next steps and the owner-only commands; never creates the database (docs/CLAUDE-CODE.md)
reachweaver describe [COMMAND] [--json]  machine-readable command catalog generated from the parser: flags, choices, defaults, JSON support and owner_only/network/writes/read_only tags
                                          --json also on draft, list, check, doctor, schedule, cancel, unapprove, import-metrics, campaign-stage, approval-mode (display only) and the dry-run publish; with --json an error also prints {"error": {"code", "message"}} on stdout
reachweaver ui [--port N] [--open] [--optout F]  experimental, read-only local dashboard on 127.0.0.1 (overview, queue, post review sheets, ideas, accounts, channel research, a "Work with Claude" page that builds prompts to paste into your own Claude Code session, owner-only command cards and a live activity feed); cannot approve, schedule, pause or publish and calls no model; the URL carries a per-run token (docs/DASHBOARD.md)
reachweaver mcp-serve [--allow-drafts] [--root DIR]  experimental: local MCP server on stdin/stdout for MCP clients other than Claude Code (Claude Desktop); read-only tools generated from the catalog, no approve or publish tool; --allow-drafts adds draft only (docs/MCP-SERVER.md)
reachweaver completion {bash,zsh}        experimental: print a Tab-completion script for commands, options and fixed choices; reads no database and runs nothing
reachweaver privacy-status [--policy F]   offline DB perms/size/row counts/oldest records/retention config
reachweaver data-export [--out FILE]      redacted JSON export of local DB (new 0600 file)
reachweaver data-purge [--policy F] [--apply]  retention plan (dry run); --apply purges local rows only
reachweaver pause / resume               emergency publishing switch
reachweaver disconnect --account ID     #38 (draft): stop publishing to one account on this installation (tighten-only, local; lists the credential variable names and revocation steps, revokes nothing itself)
reachweaver reconnect --account ID      #38 (draft): lift it; human-only (interactive terminal plus a fresh challenge typed on /dev/tty)
reachweaver approval-mode [--confirmation tty_hash_prefix|off] [--require-key] [--require-identity]  #7 D6/D7 and wave 9 item 2 (--require-identity: plain approve is refused and approvals without a stored identity are not sent; tighten-only, no command removes it): show or tighten approval safeguards; opt-in terminal + typed hash prefix for approve and publish --live; keyed HMAC binding with REACHWEAVER_APPROVAL_SECRET (docs/SECURITY-REVIEW-PROPOSAL-7-d6-d7.md)
reachweaver reconcile POST_ID --published --remote-id ID [--remote-url URL]
reachweaver reconcile POST_ID --not-published  manual outcome review
reachweaver reconcile POST_ID ... --force      also reconcile a post still 'sending' that was claimed <15 min ago (a send may be in flight; check the native account first)
reachweaver metrics POST_ID --observed-at ISO8601 --source native-export --views 100 [--likes suppressed] [--window-start T --window-end T]
reachweaver import-metrics --csv FILE     import normalized snapshots, all rows or none; cells: count, empty/unknown, suppressed, not_available, delayed, estimated:N; optional window_start/window_end (docs/ATTRIBUTION.md#2a-bis-metric-value-status-privacy-thresholds-and-report-formats-issue-15)
reachweaver report [--format json|markdown|csv] [--since 7d|30d|24h|DATE]  --since keeps only posts sent in that window and their snapshots observed in it; exact values only, per-metric coverage + status counts, estimates listed separately; null/unknown (never 0) at zero coverage; CSV formula cells neutralised
reachweaver metrics-fetch --online [--account ID] [--post-id ID] [--max-posts N] [--dry-run]  read-only GET of PUBLIC Bluesky/Mastodon counters for published posts; stops on 429; not private analytics (docs/ATTRIBUTION.md#2b-read-only-public-counter-polling-metrics-fetch)
reachweaver campaign-check --file F --brand B [--json] [--optout F] [--history-db DB [--history-days N] [--history-window-days W] [--history-max-posts K]]  offline schema/source/account checks; @-mention of an opted-out handle is an error; --history-db adds read-only repetition/frequency warnings against the same account's local posts (docs/CLAIM-GROUNDING.md#posting-history)
reachweaver claims-check CAMPAIGN --brand B [--json] [--optout F]    lexical grounding of figures/claims/feature claims vs declared evidence; not truth verification (docs/CLAIM-GROUNDING.md)
reachweaver campaign-preview --file F --brand B [--accounts A] [--utm] [--optout F] [--history-db DB [--history-days N] [--history-window-days W] [--history-max-posts K]] [--json|--markdown]  per-channel exact text, length budget, facets, capability, evidence, rule-based confidence; threads/carousel outlines; never stages (docs/CAMPAIGN-PREVIEW.md)
reachweaver campaign-export --file F --brand B --out DIR [--optout F]  manual-ready draft package
reachweaver youtube-attach POST_ID --file CLIP.mp4 --title T --made-for-kids yes|no --category-id N [--privacy private|unlisted|public] [--tag T]...  #22 (owner-approved 2026-10-09; disabled until a live receipt): bind one local MP4 + upload fields to a YouTube draft; offline; live upload stays registry-blocked (docs/YOUTUBE.md)
reachweaver youtube-detach POST_ID         #22: remove the video from a YouTube draft; offline
reachweaver youtube-analytics [--account ID] [--post-id ID]  #22: read-only sampling report over stored metric snapshots of published YouTube videos; labels exact / sampled / suppressed / delayed / not_available / unknown, never invents a view count, fetches nothing (docs/YOUTUBE.md#analytics-sampling-report)
reachweaver manual-export --platform threads|instagram|facebook|youtube|tiktok|<any other manual_only id> --variant F [--media MANIFEST] [--account-meta F] --out DIR [--json]  creator-ready package + checklist; other manual_only destinations get a generic package (limits unknown unless a registry records them; human-only ones get a write-it-yourself notes form); never sends (docs/MANUAL-EXPORT.md)
reachweaver manual-export-batch --file BATCH.json --out DIR [--json]  1-25 manual-export items preflighted as a whole (one blocking error writes nothing), one package folder each plus a manifest with the SHA-256 of every exact-text file; human-only destinations refused; an item's `at` is advisory, never scheduled (docs/MANUAL-EXPORT.md#batch-of-manual-packages)
reachweaver share [--platform P,...] (--text T | --file F) [--note N]... [--title T] [--format F] [--attach FILE]... [--media MANIFEST] [--out DIR] [--preview] [--no-shorten] [--list] [--guide] [--web [--open] [--port N]]  offline copy-ready package per network (including Bluesky, Mastodon, Telegram and LinkedIn, copy-only); a caption over a network's limit is shortened at a word boundary and marked SHORTENED; --attach files get offline header checks against sourced limits; human-only venues get notes only; never writes the database or sends (docs/EASY-SHARING.md)
reachweaver delivery-options [--platform ID] [--json]  offline route per distribution-registry channel: official API adapter (the four mock-tested ones), manual package, human-only notes form or unsupported, with the exact manual-export command; connects, posts and schedules nothing
reachweaver campaign-stage --file F --brand B [--optout F]    idempotently create draft posts only
reachweaver experiment-evaluate --file F       analyze paired native metrics; no causal claim
reachweaver experiment-preregister PLAN --out FILE  validate a 2-3 arm growth plan, write a NEW hashed record (docs/GROWTH-EXPERIMENTS.md)
reachweaver experiment-verify PLAN --hash H     detect post-hoc plan edits by recomputing the sha256 plan_hash
reachweaver experiment-readout --prereg FILE --posts MAP.json [--hash H] [--attribution ATTR.json] [--json]  read-only: verify hash, map arms to posts, label + design + flags; non-causal; --attribution (saved attribution-report --json) supplies docs_visits/repo_stars_utm per post, unknown never 0
reachweaver experiment-next --results FILE [--json]  offline: next dimension from prior classified results; keeps negative results
reachweaver experiment-simulate --seed S --pairs N --lift L --noise X [--trials T] [--json]  offline SIMULATED label distribution; never stored
reachweaver timing-evaluate --account ID --timezone TZ --source S --metric M --denominator views|impressions [--age-hours 168] [--tolerance-hours 24] [--min-posts-per-slot 3] [--campaign C] [--since D] [--until D] [--stratify format|campaign]  read-only matched-age daypart medians; per-stratum readouts; too little data -> cold_start pointer to calendar-plan's generic baseline, never a winner (docs/TIMING-EVALUATION.md)
reachweaver baseline-eval --account ID --timezone TZ (--source S --metric M --denominator views|impressions | --attribution ATTR.json --outcome docs_visits|repo_stars_utm) --since D --until D [--slot [explore:|exploit:]Wed@16:00]... [--campaign-arm [explore:|exploit:]C]... [--readout FILE]... [--min-posts 5] [--resamples 2000] [--confidence 0.95] [--seed 16] [--json]  read-only: vs calendar-plan's generic baseline; seeded bootstrap CI, Holm-Bonferroni, correlational vs randomized, insufficient_data not 0 (docs/TIMING-EVALUATION.md)
reachweaver insights [--account ID] [--platform P] [--since 30d|DATE] [--timezone TZ] [--source S] [--metric interactions|M] [--denominator views|impressions] [--min-posts 5] [--resamples 1000] [--json]  read-only "What works for you": per account, compares your own published posts' matched-age results by time of day, weekday, length, link, format, hashtags and opening hook; median and n per group, 'too little data' under 5, top 3 hypotheses to test with experiment-*; never causal; never creates the database (docs/INSIGHTS.md)
reachweaver platform-risk [--account ID] [--since 30d|DATE] [--metric views|impressions] [--age-hours 168] [--owned ID]... [--owned-reach NAME=N]... [--owned-posts NAME=N]... [--market US|global|tiktok@US]... [--site URL | --setup FILE] [--json]  read-only multi-homing report: share of your recorded reach per platform (matched age; post-count fallback, labelled), concentration (top share, HHI), sourced risk flags (X pay-per-use API, LinkedIn API terms, Reddit commercial agreement, TikTok US joint venture) and hypotheses; never creates the database (docs/PLATFORM-RISK.md)
reachweaver pilot-report --log FILE.csv [--min-weeks N] [--json]  offline per-arm field-pilot log readout; blank = unknown; descriptive, refuses arms under N ISO weeks (docs/FIELD-PILOT-PROTOCOL.md)
reachweaver schema-validate --type TYPE FILE   offline canonical schema check, versions 1.0, 1.1 and 1.2 (docs/SCHEMAS.md)
reachweaver schema-export [--out FILE] [--type T]  read-only: local DB rows as validated canonical JSON lines (current schema version) (new 0600 file or stdout); unmapped rows listed with reasons; never mints Approvals (docs/ADR-0003-CANONICAL-CONTRACTS.md)
reachweaver engagement-fetch [--online --out FILE] [--account ID] [--post-id ID] [--max-posts N] [--depth N] [--max-replies N] [--quotes [--max-quotes N]]  read-only, unauthenticated GET of PUBLIC replies (and, with --quotes, Bluesky quote posts) to your published Bluesky/Mastodon posts into a NEW 0600 engagement-triage export; plan only without --online; redacted; stops on 429; never replies or writes the DB (docs/ENGAGEMENT.md#fetching-replies-engagement-fetch)
reachweaver engagement-triage EXPORT.json [--docs-map F] [--out F.md|F.json] [--issues-dir DIR] [--optout F] [--json]  offline comment triage; reply/issue DRAFTS only, no send path; no draft for opted-out authors (docs/ENGAGEMENT.md)
reachweaver engagement-queue EXPORT.json [--docs-map F] [--optout F] [--now ISO] [--sla LABEL=HOURS] [--json]  offline aging/SLA review queue (overdue first); read-only, no database, no send path; SLA hours are owner-adjustable defaults
reachweaver followup-suggest --rules FILE [--account ID] [--since DATE] [--state FILE [--record]] [--optout F] [--json]  read-only: when a published post's EXACT metric reaches an owner-set threshold, a follow-up brief + next commands for review (thank-and-link, recap, cross-post adaptation, pin note, write-up idea); never likes, replies, DMs, reposts, drafts or sends; none for Reddit/HN (docs/FOLLOWUPS.md)
reachweaver engage-scout --config FILE [--input FILE ...] [--online] [--platform P ...] [--limit N] [--state FILE | --record STATE] [--optout F] [--json]  ranked daily shortlist of conversations to join, each with all five score factors and a suggested angle (never reply text); Reddit/HN/DEV/Stack Overflow listed as read-it-yourself; links only on a direct tool ask, capped at 1 in 10 replies, always with a disclosure line; --online adds GET-only Bluesky/Mastodon/GitHub searches; never posts, replies, likes or follows, never opens the DB (docs/ENGAGE-SCOUT.md)
reachweaver engage-log --state FILE (--mark ID --replied [--with-link] | --mark ID --skipped | --readout [--outcomes F] [--weeks N]) [--json]  record which scout items you replied to yourself; weekly readout of replies next to follower/star/visit snapshots, labelled correlational
reachweaver utm-link URL --platform P --campaign C [--content POST_ID] [--allow-origin O...] [--sources CONFIG]  canonical UTM link; refuses private source repo URLs
reachweaver attribution-report [--analytics CSV] [--github-traffic JSON]... [--ga4-csv CSV]... [--gsc-csv CSV]... [--json]  read-only UTM/referrer join; also reads GA4 and Search Console CSV files you downloaded (never fetched); at least one input file
reachweaver seo-destinations --briefs FILE --type release|paper|product|docs [--project P] [--suggest-for PLATFORM ...] [--campaign C]  landing page + conflicts; with --suggest-for, per-platform link/length/keyword/tag advice, each labelled official, unverified, repo_policy or hypothesis
reachweaver weekly-digest --out FILE.md [suggest input flags] [--include-db] [--limit N]  suggest + local report as one private (0600) Markdown file; new file only, local only, never sent (docs/NEXT-ACTIONS.md)
reachweaver seo-content-check FILE.json [--name NAME]... [--online [--allow-origin O...]] [--json]  discoverability checks on post texts you supply (length, alt text, hashtags, UTM link, name spelling, cross-platform duplicates; --online adds link-preview checks); every finding labelled official/repo_policy/hypothesis; read-only, exit 2 on errors
reachweaver creative-preflight --file BRIEF.json [--json]  offline editorial checklist for 1-6 draft posts (audience and topic terms, opening, source links, format, accessibility, CTA, hashtag and duplicate findings); not a reach prediction; no prompts for human-only Reddit/Hacker News; never publishes (docs/CREATIVE-PREFLIGHT.md)
reachweaver voice-profile (--from FILE | --from-db [--account ID]) [--platform P] --out NEW_FILE [--json]  offline: plain statistics of how you write (length per platform, sentences, emoji, hashtags, links, first person, punctuation, openings, recurring words) from your own posts in a file or your published posts (database read-only); NEW 0600 file, no post text stored; under 10 posts reports insufficient_sample and exits 1 (docs/VOICE-CHECK.md)
reachweaver voice-check [--profile FILE] (--text T | --file F | --post-id ID) [--platform P] [--json]  advisory: draft features outside your usual range and stock phrases from a heuristic style list (not an AI detector); info/warn only, never blocks, exit 0; creative-preflight takes --voice-profile FILE for the same findings
reachweaver ai-label-check (--file F | --post-id ID) [--platform P ...] [--media MANIFEST] [--authorship human|ai_assisted|ai_generated] [--eu [--public-interest yes|no] [--editorial-review yes|no]] [--json]  advisory: per target platform, whether its AI-content rule asks for a label (required/recommended/not required/unknown), what to tick in the native composer and the cited source; the EU AI Act Art. 50 note is secondary-sourced, not legal advice; never blocks, exit 0 (docs/AI-CONTENT-LABELS.md)
reachweaver eu-review-evidence [--since 30d|DATE] [--account ID] [--platform P] [--post-id ID]... [--limit 500] [--format text|json|markdown] [--json]  read-only human-review evidence per published post for the EU AI Act Art. 50(4) exemption: who approved, when, approved payload hash, text hash, approval-ledger line and integrity, authorship when recorded; database and ledger only read, never repaired; not legal advice (docs/AI-CONTENT-LABELS.md#human-review-evidence-report-eu-review-evidence)
reachweaver creator-match --file CANDIDATES.json [--json]  offline shortlist of creators YOU curated, scored on owner-entered editorial fit; opted-out/declined, stale, unreviewed or off-topic entries are held; contacts nobody, predicts no reach (docs/CREATOR-AMPLIFICATION.md)
reachweaver creator-kit --file SOURCE.json --out NEW_DIR [--json]  NEW private folder (0700/0600) with source-kit.json and CREATOR-REVIEW.md: claims with evidence and limits, rights, consent and disclosure reminders; refuses an existing path; never sent
reachweaver creator-coverage --file COVERAGE.json [--json]  offline counts of owner-confirmed independent creator pickups at 48h/7d; unverified items kept out of totals; fetches nothing
reachweaver landing-check --briefs FILE [--campaign CAMPAIGN.json] [--online] [--allow-origin O...] [--json]  canonical URL/CTA/UTM checks; --online: status, redirects, rel=canonical, Open Graph, noindex, lang, lexical claim phrases (docs/SEO-INTEGRATION.md)
reachweaver ai-visibility (--url URL --html F --robots F | --briefs FILE [--page-id ID] [--online]) [--allow-origin O...] [--json]  your own pages vs AI answer engines and search: robots.txt per crawler, noindex/nosnippet, canonical, JSON-LD, fediverse:creator; llms.txt as info only; no citation guarantee (docs/AI-SEARCH-VISIBILITY.md)
reachweaver trace POST_ID [--log FILE | --log -] [--json]  # read-only: one post's audit events joined with the worker-log lines and runs that handled it, oldest first (issue #26)
reachweaver link-health [--status approved --status scheduled --status published] [--account ID] [--campaign C] [--online [--allow-origin O...]] [--max-links 25] [--json]  # read-only: broken, redirected or preview-less links in your stored posts; offline unless --online; robots.txt first (issue #10)
reachweaver media-check MANIFEST [--json]      offline media headers/limits/alt text/captions/rights; never uploads
reachweaver media-attach POST_ID --url HTTPS_URL (--alt T | --alt-file F) --online  Instagram draft: one owner-hosted JPEG URL; one read-only GET; SHA-256 + URL bound at approval (#20, owner-approved 2026-10-09; disabled until a live receipt)
reachweaver media-detach POST_ID               offline: remove a draft's image attachments
reachweaver media-storyboard --campaign CAMPAIGN.json --out NEW_DIR [--duration S] [--aspect 9:16] [--video-targets T...] [--image-targets T...] [--locale L] [--fit cover|contain] [--safe-zones F] [--json]  offline storyboard + SRT skeleton + alt-text placeholders + image specs + media manifest; generates no media (docs/MEDIA-ASSETS.md)
reachweaver media-storyboard --check STORYBOARD.json [--safe-zones F] [--json]  re-check edited text/focal boxes against crop and UI-overlay safe areas, subtitle timing and placeholders
reachweaver media-cutlist PLAN.json [--json]  offline: one master video -> per-platform cut list (segments that fit each sourced duration limit, crop to the target aspect, subtitle format, a suggested ffmpeg command you run yourself); opens and edits no media (docs/MEDIA-ASSETS.md)
reachweaver sources-scan --config F [--online] [--allow-private] [--state S] [--top N] [--json]  rank opt-in GitHub/feed/changelog/DOI signals; research only
reachweaver signal-brief --scan SCAN.json --signal-id ID --brand B --out CAMPAIGN.json [--accounts A] [--objective O] [--allow-duplicate] [--json]  NEW 0600 campaign scaffold + .brief.md from one signal; placeholders block staging; no copy generated (docs/SOURCES.md)
reachweaver release-brief (--release RELEASE.json | --changelog CHANGELOG.md --version X.Y.Z) [--repo OWNER/NAME] --out NEW_DIR [--brand B --accounts A] [--objective O] [--campaign ID] [--json]  offline: a saved GitHub release (gh api ... > release.json) -> NEW campaign folder + brief.md; release text is untrusted data; BLOCKED until you write and verify; workflow template in examples/github-workflows/ (docs/RELEASE-TO-DRAFT.md)
reachweaver capabilities [--platform P] [--json] [--max-age-days 120]  offline capability/API-approval registry (docs/PLATFORMS.md)
reachweaver registry-age [--registry NAME] [--platform P] [--all] [--as-of YYYY-MM-DD] [--json]  offline age of every dated platform fact in the five bundled research registries: fresh, aging (>60 days), stale (>120 days) or undated; always exit 0 (docs/PLATFORMS.md#registry-freshness)
reachweaver distribution-plan --region R --goal G [--limit N] [--date D --timezone TZ] [--market US|global|tiktok@US]... [--json]  offline worldwide channel hypotheses; never schedules or posts; --market adds a sourced market flag (TikTok US since 2026-01-23), never changes the ranking
reachweaver social-web-plan [--setup FILE] [--presence P]... [--bridged ID]... [--fediverse-sharing ID]... [--site URL [--site-kind K]] [--json]  offline open social web map: protocols, bridges, canonical home, duplicate reach, identity checklist; never sends (docs/OPEN-SOCIAL-WEB.md)
reachweaver account-advisor [--region R] [--goal G] [--format F] [--weekly-hours N] [--limit N] [--existing ID]... [--setup ID [--brand B] [--audience A] [--language L]] [--json]  offline shortlist of platforms to start, from the distribution registry; --setup prints the official start page, the steps you do yourself and a coaching prompt for a normal Claude chat that never opens the site; creates and connects no account (docs/ACCOUNT-ADVISOR.md)
reachweaver profile [--init [--persona ID]...] [--show] [--check] [--set PATH=VALUE]... [--handle PLATFORM:PERSONA=NAME[:STATUS]]...  your private profile (personas, a username per network, sign-up details); local file, never holds passwords (docs/PERSONAS-AND-ACCOUNT-KIT.md)
reachweaver account-add PLATFORM [--handle H] [--server URL] [--field KEY=VALUE]... [--id ID] [--persona ID] [--env-prefix P] [--dry-run]  offline: append one public account entry to accounts.json (asks in a terminal for what is missing), validated by the normal loader; refuses duplicates and example placeholders, fills an unedited example entry in place, keeps accounts.json.bak, applies the wrong-account guard; never asks for or stores a secret, prints the variable names to set and points to connect --guide
reachweaver account-route --platform P [--persona ID] [--json]  which account and username to use for a persona on a network, and which account to switch to; offline
reachweaver account-kit --platform P... [--persona ID] [--questions] [--out NEW_DIR --approve-plan SHA256] [--json]  offline: one plan and one approval write sign-up details (in the platform's language) for accounts you create yourself; creates no account
reachweaver draft|check|approve ... --persona ID  refuse an account that belongs to another persona and name the one to switch to (default: preferences.default_persona)
reachweaver postiz-check --account ID --online [--post-id ID]  read-only Postiz integration listing / job lookup; never posts (docs/POSTIZ.md)
```

**Field pilot and release playbook (issue #27 slice):** [FIELD-PILOT-PROTOCOL.md](docs/FIELD-PILOT-PROTOCOL.md) is a preregistration template for comparing a manual workflow, a conventional scheduler and ReachWeaver on one owned project and two authorized networks; copy `examples/pilot-log.template.csv` (header only) and read it with `pilot-report`. [RELEASE-PLAYBOOK.md](docs/RELEASE-PLAYBOOK.md) walks a release through every existing command and human gate. **No pilot has been run and there are no pilot results.**

**Campaign links and attribution (offline):** `utm-link` builds deterministic, tracker-free UTM links. `attribution-report` joins your own analytics CSV and saved GitHub traffic JSON to local posts. It also reads a GA4 report or exploration you downloaded as CSV (joined by session source, medium, campaign and ad content) and Search Console Performance export tables (shown as search context for the pages your posts link to, never credited to a post); ReachWeaver does not connect to Google. It keeps exact UTM matches, domain-level referrer-only rows and unattributed rows separate, and never zero-fills or claims causation. `seo-destinations` reads a local export from the [SEO repository](https://github.com/MSKazemi/seo) and flags competing campaigns. `landing-check` checks that campaign CTAs point at a brief's canonical page and, only with `--online`, fetches each landing page (robots.txt honoured, redirects capped) to check its status, canonical link, social preview and claim wording. Each recorded per-platform metric definition carries the official `source_url` it was checked against and a `verified_date`. See [attribution](docs/ATTRIBUTION.md), a [SYNTHETIC weekly report example](docs/WEEKLY-REPORT-EXAMPLE.md) and the [SEO integration contract](docs/SEO-INTEGRATION.md).

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

</details>

## Install and update

**Already installed? Upgrade the Claude Code plugin and pipx CLI separately.** Run these commands in your **normal terminal** (skip any component you do not use):

```bash
claude plugin marketplace update reachweaver
claude plugin update reachweaver@reachweaver
pipx upgrade reachweaver
```

Then run `/reload-plugins` inside Claude Code or restart it. The first command refreshes the plugin marketplace, the second updates the installed plugin, and the third upgrades the separately installed CLI. For a standalone skill, project-local install, or source checkout, use the [complete upgrade command guide](docs/UPGRADE.md).


**Recommended: Claude Code plugin** (Claude Code manages install and updates; needs the repository to be reachable by git):

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

Use it as `/reachweaver:reachweaver` or just ask. A `SessionStart` hook runs `update --notice` (one GET to github.com at most every 24 h, silent offline) and Claude tells you when a newer release exists. A `PreToolUse` hook blocks Claude's shell commands that would run an owner-only action such as `approve` or `publish --live` ([the plugin's agent guard](docs/CLAUDE-CODE.md#who-does-what)). To update: `/plugin marketplace update reachweaver`, then `/plugin update reachweaver@reachweaver`, then `/reload-plugins` (or turn on auto-update for the marketplace under `/plugin`). The plugin is the lean `plugin/` folder (manifest, hooks, README, LICENSE and a generated copy of the skill bundle; `python3 scripts/sync_skill.py` refreshes it), not the whole repository, so an install copies only what the plugin needs. Bump `version` in `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` with every release (a test checks it matches `reachweaver.__version__`).

**Alternative: standalone installer** (no plugin system; copies the skill into `~/.claude/skills`):

```bash
# Latest GitHub Release, checksum-verified, into ~/.claude/skills/reachweaver
curl -fsSLO https://raw.githubusercontent.com/MSKazemi/reachweaver/main/scripts/install_skill.py
python3 install_skill.py            # add --project to install into ./.claude/skills instead
# From a clone, no network: python3 scripts/install_skill.py --from-checkout
```

Restart Claude Code and use `/reachweaver`. The skill checks for a newer release at the start of a session (at most one GET per 24 h, silent offline; opt out with `touch ~/.cache/reachweaver/no-update-check`; `update --apply` only applies to this installer route, never to a plugin), tells you, and updates only after you agree. By hand: `python3 ~/.claude/skills/reachweaver/scripts/run.py update --check` then `update --apply --yes`. The installer and updater verify the release's SHA-256, refuse unsafe archive paths and keep the previous copy under `~/.cache/reachweaver/previous/`. Releases come from the `release.yml` workflow on a `vX.Y.Z` tag; from a clone with no network use `--from-checkout`.

### As a Python package (command line only)

ReachWeaver is on [PyPI](https://pypi.org/project/reachweaver/), and every release also carries the same wheel and source archive on GitHub, for servers and scripts. You need Python 3.11 or newer; there are no dependencies.

```bash
pipx install reachweaver          # or: python3 -m pip install reachweaver
reachweaver version
# the same file from the GitHub release:
pipx install "https://github.com/MSKazemi/reachweaver/releases/download/v0.14.0/reachweaver-0.14.0-py3-none-any.whl"
```

Checksums, what CI checks in the package, and why it is not on PyPI or GitHub Packages (yet) are in [the Python package guide](docs/PACKAGING.md).

### Using the skill in another project

The project skill lives at `.claude/skills/reachweaver/SKILL.md`. For any other repository, install that folder as a skill (user-level `~/.claude/skills/reachweaver`, or the target's `.claude/skills/reachweaver`); the installable ZIP below also works. The standalone package includes a CLI at `scripts/run.py`, all referenced guidelines, and self-contained sample manifests in `assets/`:

```bash
# Run inside the target project, pointing to where you installed the skill
python3 ~/.claude/skills/reachweaver/scripts/run.py doctor    # first command: shows what is missing
python3 ~/.claude/skills/reachweaver/scripts/run.py --help
```

In a skill-only install the repository's `examples/` and `config/` files are in the skill's `assets/` folder (for example `config/accounts.example.json` is `assets/accounts.example.json`, `examples/example-post.txt` is `assets/example-post.txt`). If `doctor` reports missing accounts, copy `assets/accounts.example.json` to `config/accounts.json` in your project; it holds no secrets.

This is compatible with Claude Code's documented project/personal skill paths: https://code.claude.com/docs/en/skills.

### Installable skill ZIP from GitHub Actions

Every successful **main-branch** CI run packages the complete `.claude/skills/reachweaver` directory (including its executable offline runtime, references and assets) as the `reachweaver-claude-skill` artifact containing `skill.zip`. Open the run under [GitHub Actions](https://github.com/MSKazemi/reachweaver/actions), download the **trusted main-branch artifact** while it is retained, and inspect before installing. Do not install ZIPs from untrusted pull requests; no account credentials are inside this bundle.

For normal use, cloning this repository and invoking `/reachweaver` from Claude Code also uses the current skill without needing any ZIP.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/sync_skill.py    # keep standalone skill CLI in sync with canonical code
python3 scripts/secret_scan.py   # tracked-file secret scan; fixtures allowlisted in .secret-scan-allow
python3 scripts/lint.py          # stdlib syntax + JSON/JSONL lint
python3 scripts/check_runtime_deps.py  # runtime must stay stdlib-only
make check                       # all of the above (sync, scan, lint, deps, test)
make typecheck                   # pinned mypy via pipx (CI runs it too)
make plugin-eval-load            # parse the plugin's behaviour evals with the claude CLI; no model run
make plugin-eval                 # LOCAL ONLY: plugin behaviour evals, real model calls on your Claude plan (docs/PLUGIN-EVALS.md)
make release-gate                # offline versioned release gate: versions, CHANGELOG, docs, bundle, blockers, readiness, tests
python3 scripts/release_gate.py --scorecard [--json]  # per-platform/area release-readiness scorecard
python3 scripts/pilot_benchmark.py [--posts N] [--json]  # offline mock-only harness skeleton for the pilot report; synthetic, not pilot evidence
# When using the skill-creator validator in a compatible environment, validate and package the skill.
```

CI runs pure offline tests only, plus a hygiene job (secret scan, stdlib lint, compileall, pinned ruff hard-error rules, runtime-dependency guard, pinned mypy). A pushed `vX.Y.Z` tag re-runs those checks and the full release gate in a read-only job before a separate owner-only job publishes the GitHub Release ([maintainer release checks](docs/RELEASE-PLAYBOOK.md#maintainer-release-checks)). Runtime and tooling decisions are recorded in ADR-0002. See [contributing](CONTRIBUTING.md) for fresh-clone commands, [security](docs/SECURITY.md#reporting-a-vulnerability-private) for private vulnerability reporting, and the [roadmap](docs/ROADMAP.md).

## Relationship with existing projects

- **MSKazemi/novareach**: strategy and storytelling across projects; do not duplicate marketing positioning.
- **MSKazemi/seo**: SEO, attribution, broader acquisition intelligence and social distribution analysis.
- **ReachWeaver**: actionable, permissioned social content operations with data/receipts.

Copyright 2026 Mohsen Seyedkazemi Ardebili. Original implementation released under the [Apache License 2.0](LICENSE); redistributions must keep the [NOTICE](NOTICE) file. upstream inspirations are linked and attributed without copying their source. See [research](docs/UPSTREAM-RESEARCH.md).
