# CLI cookbook: every command with its key flags

Load on demand; `SKILL.md` stays small. `tests/test_cookbook_drift.py` checks that every subcommand has a row here and that every flag written next to a command exists.

**Prefix.** `RW` means `python3 <skill-dir>/scripts/run.py` (skill-dir holds `SKILL.md`), run **from the user's project directory**. In a full checkout `python3 -m reachweaver` is equivalent, and after `pip install` so is `reachweaver`. Global options go before the command: `--db DB`, `--accounts FILE`, `--profile FILE`, `--home DIR`, `--plain`. In a copied skill, `docs/`, `examples/` and `config/` paths become `assets/` (e.g. `assets/accounts.example.json`); `RW COMMAND --help` shows every flag; `RW --help` lists the everyday commands and `RW --help all` every command by task.

**Legend.** **OWNER** = the owner runs it in their own terminal; **Claude never runs it**: prepare the exact command and hand it over. **online** = contacts a network; only with the owner's permission for that run. Everything else is offline or local, and nothing here posts unless the owner runs `publish --live`.

## The usual offline path

```bash
RW init                       # local SQLite state only, no account
RW doctor                     # accounts config and credential NAMES; calls no API
RW campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --json
RW campaign-export --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --out NEW_DIR
RW campaign-stage --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json
RW list
RW show POST_ID               # the EXACT text and destination the owner reviews
RW check POST_ID
RW repeat-check --post-id POST_ID
RW publish --post-id POST_ID  # dry run
RW report --format markdown
RW next                       # what to post next: ideas by name, numbers due, days since the last post
```

Then the owner, in their own terminal: `approve POST_ID` (or `review` to walk several drafts one by one), and only on the production host with credentials set, `REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID`.

## Get started and look around

| Command | Purpose | Key flags |
|---|---|---|
| `start` | Reads workspace, accounts (names example placeholders), database, queue, ideas and snapshots; prints the exact next command (browser composer first, drafts via the owner's `review`); with no accounts file but posts or ideas already recorded it shows that progress (`next`, `report --week`) instead of first-time setup; runs nothing | `--json` |
| `next` | "What should I post next?": at most 5 ranked, concrete steps: safety issues first, drafts waiting (`review`), posts due for their 7-day numbers (exact `metrics` line), each open idea BY NAME with one `share --platform <usual networks> --text '...'` command (shell-quoted; the browser composer for a long idea), days since the last post per network, the top "what works" line. Read-only; prints the owner's idea text, so mcp-serve withholds it. Bare `suggest` prints the same view | `--json` (schema `reachweaver.next.v1`) |
| `connect` | Status table of configured accounts (credential variable names only); never posts. No accounts yet: next steps, exit 0 (in a terminal it offers the `account-add` flow) | `--account ID`, `--json`; **OWNER** `--guide` (walkthrough, needs a person at a terminal) |
| `account-advisor` | Ranked channel shortlist and setup coaching; creates no account | `--region R`, `--goal G`, `--format F`, `--weekly-hours N`, `--existing`, `--setup PLATFORM_ID`, `--json` |
| `profile` | Private `config/profile.json`: personas, usernames, sign-up details | `--init`, `--persona ID`, `--show`, `--check`, `--set PATH=VALUE`, `--handle PLATFORM:PERSONA=NAME` |
| `account-add` | Append one public account entry to `accounts.json` (loader-validated, duplicates and placeholders refused, `.bak` kept, persona guard); never asks for or stores a secret, prints the variable names | `PLATFORM`, `--handle H`, `--server URL`, `--field KEY=VALUE`, `--id ID`, `--persona ID`, `--env-prefix P`, `--dry-run` |
| `account-remove` | Remove one entry from `accounts.json` (`.bak` kept); never touches credentials, the database, approvals or disconnect state; posts of that account stay and are counted | `ID`, `--dry-run`, `--yes` (needed outside a terminal) |
| `account-route` | Which account/username to use for a persona on a network | `--platform P`, `--persona ID`, `--json` |
| `account-kit` | Plan sign-up packets for accounts the user creates; creates none | `--platform P` (repeat), `--persona ID`, `--questions`, `--out NEW_DIR`, `--json`; **OWNER** `--approve-plan SHA` |
| `context` | Start every session here: one compact read-only summary (accounts, pause, live gate, approval mode, posts waiting for the owner, next steps, owner-only commands); creates nothing | `--json` |
| `describe` | Machine-readable command catalog from the parser: flags, `--json` support, `owner_only`/`network`/`writes`/`read_only` tags and `everyday` (true for the short list `--help` shows); ask for one command to keep it small | `COMMAND`, `--json` |
| `help` | Human help text: no topic = the everyday commands (same as `RW --help`), `all` = every command by task (same as `RW --help all`), a command name = its options; offline. Agents use `describe` instead | `TOPIC` (`all` or a command name) |
| `version` | Installed version and install kind | `--json` |
| `update` | **online** (one GET to github.com): newer skill bundle? | `--check` (default), `--notice`, `--json`; `--apply --yes` only after the user agrees |
| `init` | Create the local database (0600); connects nothing | none |
| `doctor` | Account config and missing credential names; no API call | none |
| `health` | Per-account readiness `ok`/`attention`/`blocked`; exit 1 when blocked | `--json`, `--stale-minutes N` |
| `workspace` | Folder report: config, database, ledger; will a copy work elsewhere. `--init` starts with an empty accounts list (a terminal asks which networks to add placeholders for) | `--json`, `--init DIR`, `--with-examples` (with `--init`: the four example placeholders), `--gather`, `--repair` (dry runs); `--apply` is the owner's decision |
| `ui` | **OWNER**: read-only dashboard on 127.0.0.1 with a per-run URL token; Claude never starts it or reads its URL | `--port N`, `--open` |
| `mcp-serve` | **OWNER** (draft, pending security review): stdio MCP server for other MCP clients such as Claude Desktop; read-only tools only, no approve or publish tool; the owner registers it in the client's config; Claude Code never starts it | `--allow-drafts`, `--root DIR`, `--max-output-bytes N`, `--max-calls-per-minute N` |
| `statusline` | One short read-only line for Claude Code's status line (version, drafts, queued, needs review, ideas, PAUSED); creates no database, no network | none |
| `idea` | Ideas inbox: `add TEXT`, `list`, `drop ID`, `promote ID` to a NEW blocked campaign scaffold; never a draft, approval or send | `--source URL`, `--tag T`, `--status`, `--json`, `--campaign SLUG`, `--out DIR`, `--brand`, `--accounts` |
| `list` | Posts and statuses | `--status S` |
| `show` | Exact text, account and platform preview of `POST_ID` | `POST_ID` |
| `journal` | What was posted, when, where, with files and hashes | `--post-id`, `--platform`, `--account`, `--since`, `--until`, `--all-statuses`, `--format table/json/markdown/csv`, `--out`, `--verify-files` |
| `capabilities` | Platform capability/API-approval registry; never grants permission | `--platform P`, `--json`, `--max-age-days N` |
| `registry-age` | Age of every dated platform fact in the nine bundled research registries (incl. ai-search, ai-disclosure, social-web, platform-risk): fresh, aging (>60 days), stale (>120 days) or undated; exit 0; offline. Before quoting a platform fact, re-verify an aging, stale or undated one at its official source | `--registry NAME`, `--platform P`, `--all`, `--as-of YYYY-MM-DD`, `--json` |
| `completion` | Print a bash/zsh completion script; the owner loads it | `bash` or `zsh` |

## Draft and approve (nothing is sent)

| Command | Purpose | Key flags |
|---|---|---|
| `draft` | Queue a text draft; one per `--account`, all or none | `--account ID` (repeat), `--text T` or `--file F`, `--campaign`, `--source-url`, `--persona ID`, `--reply-to ITEM_ID` (owner adds `--online` to verify the parent) |
| `check` | Validate a draft before approval | `POST_ID`, `--persona ID`, `--optout F`, `--sources CONFIG` |
| `repeat-check` | Same file, wording or link already shared on this platform? | `--post-id ID`, or `--platform P` with `--text`/`--file`/`--image`/`--video`/`--link`; `--days N`, `--json` |
| `mark-shared` | Record what the owner posted by hand (local ledger). With `--url` (the post's link) it also becomes measurable: a `published_manual` "posted by hand" post id (the one id printed, with its exact `metrics` line) that `metrics`/`import-metrics`/`report`/`insights`/`next` and the dashboard include; it can never be approved, scheduled or sent. Without `--url` it says the post is not counted in reports. Offer it after a by-hand post so its results can be measured | `--platform P`, `--account`, `--text`, `--file`, `--image`, `--video`, `--link`, `--url`, `--at` (when it was posted: `now`, `yesterday`, `YYYY-MM-DD`, `'YYYY-MM-DD HH:MM'` local, or ISO), `--note` |
| `review` | **OWNER**: guided approval in the owner's terminal: each draft's sheet and check, then `a` (approve that one post), `s`, `c` (typed CANCEL) or `q`; needs a real terminal; reply drafts left out. Claude never runs it; suggest it when several drafts wait | `--campaign C`, `--account ID`, `--approver NAME`, `--persona`, `--optout F`, `--sources CONFIG`, `--verify-identity --online` |
| `approve` | **OWNER**: approve the exact current text; binds a payload hash (plus a Mastodon account's visibility/language/spoiler_text, a Postiz account's server/integration, and `--allow-repeat` when given). A post with images or a video whose AI-label decision is required/unknown is refused (`ai_label_unanswered`) until the owner answers `--ai-label`; text-only posts are never blocked | `POST_ID`, `--persona`, `--approver`, `--verify-identity --online`, `--allow-repeat` (only when re-sharing is intended), `--ai-label applied\|not_needed` (media posts; bound, approval/media-3) |
| `ai-declare` | Record the owner's AI-content answers on a draft: who wrote the text, whether each image/video is realistic AI-made media; bound at approval for media posts (approval/media-3). Record only answers the owner gave; never guess them | `POST_ID`, `--authorship human\|ai_assisted\|ai_generated\|clear`, `--realistic-synthetic yes\|no\|clear`, `--attachment N`, `--json` |
| `unapprove` | Approved/scheduled post back to draft; binding cleared | `POST_ID` |
| `cancel` | Draft/approved/scheduled post will never be sent | `POST_ID` |
| `schedule` | Due time for an approved post; a worker still has to publish | `POST_ID`, `--at 2026-10-15T09:00:00+02:00` |
| `media-attach` | One image + alt text on a draft, SHA-256 bound at approval; never uploads | `POST_ID`, `--file F --alt TEXT` (Mastodon) or `--url URL --alt TEXT --online` (Instagram); `--alt-file`, `--rights-confirmed`, `--license`, `--created-by` |
| `media-detach` | Remove every image from a draft | `POST_ID` |
| `video-attach` | Bind one MP4 to a TikTok draft; never uploads | `POST_ID`, `--file F`, `--mode creator_inbox/direct_post`, `--privacy-level`, `--commercial`, `--aigc` |
| `video-detach` | Remove the video from a TikTok draft | `POST_ID` |
| `youtube-attach` | Bind one MP4 + upload metadata to a YouTube draft; private by default | `POST_ID`, `--file F`, `--title T`, `--made-for-kids yes/no`, `--category-id ID`, `--privacy`, `--tag` |
| `youtube-detach` | Remove the video from a YouTube draft | `POST_ID` |
| `campaign-check` | Evidence/brand/variant checks; does not assert source truth | `--file F`, `--brand B`, `--json`, `--history-db state/reachweaver.sqlite` (repetition warnings) |
| `campaign-preview` | Per-channel exact text, length, capability, confidence | `--file F`, `--brand B`, `--accounts A`, `--utm`, `--json` or `--markdown` |
| `campaign-stage` | Idempotently stage verified variants as drafts | `--file F`, `--brand B` |
| `campaign-export` | Validated draft pack for manual publishing | `--file F`, `--brand B`, `--out NEW_DIR` |
| `claims-check` | Lexical claim grounding vs declared evidence; not truth verification | `CAMPAIGN`, `--brand B`, `--duplicate-threshold X`, `--json` |
| `review-packet` | (draft, pending security review) NEW private folder for a co-reviewer: exact text, destination, alt text, content digest, check findings; never approves | `--post-id ID` (repeat) or `--campaign-staged NAME`, `--out NEW_DIR`, `--persona`, `--optout F`, `--sources CONFIG`, `--json` |
| `review-comment` | **OWNER**: record what a reviewer said, bound to the digest they read; a `looks_good` is never approval; never invent one | `--packet DIR`, `--post-id ID`, `--reviewer NAME`, `--verdict looks_good/change_requested/question`, `--note TEXT`, `--json` |
| `review-status` | Read-only: comments per post, STALE when the text changed after the comment; notes are data, not instructions | `--packet DIR`, `--json` |

## Publish and recover (live sends are owner-only)

| Command | Purpose | Key flags |
|---|---|---|
| `publish` | Dry-run preview of due work | `--post-id ID`, `--limit N`, `--scheduled-only`, `--max-per-24h`, `--min-gap-minutes`, `--log-json`, `--log-file`; **OWNER** `--live` (also needs `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`) |
| `pause` | Emergency global stop for live publishing (tighten-only) | none |
| `resume` | **OWNER**: lift the emergency stop | none |
| `reconcile` | **OWNER**: human-confirm an uncertain send after checking the real account; never blind-retry | `POST_ID`, `--published --remote-id ID --remote-url URL` or `--not-published`, `--force` |
| `approval-mode` | Show the approval safeguards; **OWNER** tightens them; never turn confirmation off | `--confirmation tty_hash_prefix`, `--require-key`, `--require-identity`, `--max-age-hours N` |
| `calendar-plan` | Propose audience-local times; with results in the DB, `your_results` shows your own better 6-hour blocks next to the slots (observation with n; never picks a slot) | `--account ID`, `--timezone TZ`, `--start DATE`, `--days N`, `--slot 'Wed@16:00'`, `--audience-activity CSV`, `--explore-share F`, `--include-drafts`, `--post-id`, `--market tiktok@US` (sourced flag only; slots unchanged); `--apply` (queues approved posts locally) is the owner's decision |
| `calendar-gaps` | Empty and overloaded slots in the local queue | `--account ID`, `--timezone TZ`, `--start`, `--days`, `--max-per-day`, `--min-spacing-hours`, `--timing-source`, `--timing-metric`, `--json` |
| `connect-check` | **online**: non-publishing identity check of one account | `--account ID`, `--online` |
| `postiz-check` | **online**, read-only Postiz integration listing or receipt lookup | `--account ID`, `--online`, `--post-id` |
| `disconnect` | Stop publishing to one account here (tighten-only); revokes nothing | `--account ID` |
| `reconnect` | **OWNER**: lift a disconnect; challenge typed on /dev/tty | `--account ID` |

## Post by hand

| Command | Purpose | Key flags |
|---|---|---|
| `share` | Copy-ready caption files and checklists per network, including copy-only Bluesky/Mastodon/Telegram/LinkedIn (sending them stays draft → review); over-limit captions shortened at a sentence end or a word (closing link kept) and marked SHORTENED with what was removed; "link in bio" flagged where post links work; `--attach` media header-checked offline; never sends or writes the database | `--platform instagram,threads`, `--text T` or `--file F`, `--note` (human-only venues), `--title`, `--format`, `--attach`, `--no-shorten`, `--strict` (all or nothing; default prepares the unblocked networks, lists each skipped one with its fix and exits 1), `--preview`, `--list`; **OWNER** `--guide`, `--web --open` |
| `manual-export` | Creator-ready package for one manual destination | `--platform P`, `--variant FILE`, `--out NEW_DIR`, `--media MANIFEST`, `--account-meta F`, `--json` |
| `manual-export-batch` | Preflight 1-25 manual-export items, one folder each | `--file BATCH.json`, `--out NEW_DIR`, `--json` |
| `delivery-options` | Route per registry channel: adapter, manual package, human-only, unsupported | `--platform P`, `--json` |

## Monitor (read-only)

| Command | Purpose | Key flags |
|---|---|---|
| `queue-health` | Overdue, `sending` age, `needs_review`, cooldowns, pause | `--stale-minutes N` |
| `dead-letter` | `needs_review` posts by error code, with reconcile steps | `--json` |
| `ops-metrics` | Operational counts for monitoring | `--format json/prometheus`, `--since`, `--out` |
| `ops-summary` | One redacted readiness + metrics report; exit 1 when blocked | `--json`, `--since` |
| `credentials-audit` | Credential hygiene, variable names only; exit 1 on findings | `--history`, `--json` |
| `link-health` | Broken, redirected or preview-less links in stored posts | `--status`, `--account`, `--campaign`, `--online` (**online**), `--max-links`, `--json` |
| `trace` | Everything that happened to one post, oldest first | `POST_ID`, `--log worker.jsonl`, `--json` |

## Measure and learn

| Command | Purpose | Key flags |
|---|---|---|
| `metrics` | Record one post's numbers that the owner read in the app's insights or a native export. Defaults: observed now, source `entered-by-hand` (the owner typed them; never claims an API source). Prints when to record again for the 7-day comparison | `POST_ID`, `--likes`, `--replies`, `--reposts`, `--views`, `--impressions`, `--clicks`, `--observed-at` (`now`/`yesterday`/`YYYY-MM-DD`/`'YYYY-MM-DD HH:MM'` local/ISO), `--source native-export`, `--window-start`, `--window-end` |
| `import-metrics` | Native-analytics CSV, all rows or none; cells count/unknown/suppressed/not_available/delayed/estimated:N | `--csv FILE` |
| `metrics-fetch` | **online**, read-only public Bluesky/Mastodon counters; never posts | `--online`, `--dry-run`, `--account`, `--post-id`, `--max-posts` |
| `report` | Coverage-aware analytics; exact values only; opens the DB read-only and never creates a missing one. At a terminal: a short summary (totals, best post, a table per network, top 5 posts by interactions, posts due for numbers); piped: the unchanged JSON. Claude reads it with `--format json` | `--format text/json/markdown/csv`, `--week` (= `--since 7d`), `--since 7d/30d/24h/YYYY-MM-DD` (posts sent in the window) |
| `youtube-analytics` | Sampling report over stored YouTube snapshots; fetches nothing | `--account`, `--post-id` |
| `timing-evaluate` | Per-account matched-age outcomes; not optimal-time proof | `--account`, `--timezone`, `--source`, `--metric`, `--denominator`, `--stratify format/campaign`, `--since`, `--until` |
| `baseline-eval` | Personalized slots/arms vs the generic baseline; bootstrap CI, non-causal | `--account`, `--timezone`, `--since`, `--until`, `--source`, `--metric`, `--denominator`, `--slot exploit:Tue@09:00`, `--campaign-arm`, `--readout`, `--attribution`, `--attribution-source analytics/ga4`, `--json` |
| `insights` | "What works for you": your own posts' matched-age results by time, weekday, length, link, format, hashtags, hook; median + n per group, top 3 hypotheses to test; never causal, read-only | `--account`, `--platform`, `--since 30d`, `--timezone`, `--source`, `--metric interactions`, `--denominator`, `--min-posts`, `--resamples`, `--json` |
| `platform-risk` | Multi-homing: share of your recorded reach per platform (matched age; post-count fallback, labelled), concentration (top share, HHI), sourced risk flags (X pay-per-use, LinkedIn API terms, Reddit commercial agreement, TikTok US joint venture), owned share; hypotheses only, read-only | `--account`, `--since 30d`, `--metric views/impressions`, `--age-hours`, `--tolerance-hours`, `--owned ID`, `--owned-reach NAME=N`, `--owned-posts NAME=N`, `--market tiktok@US`, `--site URL`, `--setup FILE`, `--json` |
| `attribution-report` | UTM/referrer join of local analytics exports; not causal | `--analytics`, `--github-traffic`, `--ga4-csv`, `--gsc-csv`, `--json` |
| `pilot-report` | Per-arm readout of an owner field-pilot CSV log | `--log CSV`, `--min-weeks`, `--json` |
| `peer-benchmark` | Compare with peers' public counters copied by hand | `--file F`, `--platform`, `--include-individuals`, `--json` |
| `suggest` | Ranked review of profile, posts, SEO, engagement, queue; bare `suggest` prints the same plain view as `next` (its `--json` keeps the suggestions schema: an existing DB query-only plus the open-ideas count, never idea text); `--include-db` also counts open ideas; with a DB it adds P3 "What works for you" items from `insights`, plus a research-freshness item when a registry fact is aging or stale | `--snapshot F`, `--metrics-csv F`, `--owner-authorized`, `--engagement-export`, `--seo-briefs`, `--experiments`, `--include-db`, `--json` |
| `weekly-digest` | `suggest` (+ report) as one private Markdown file; never sent | `--out F`, `--snapshot F`, `--include-db` |

## Experiments

| Command | Purpose | Key flags |
|---|---|---|
| `experiment-preregister` | Validate a growth plan, write a NEW hashed preregistration | `PLAN`, `--out NEW_FILE` |
| `experiment-verify` | Detect post-hoc edits by recomputing the hash | `PLAN`, `--hash sha256:H` |
| `experiment-evaluate` | Matched-age, non-causal readout of post pairs | `--file F` |
| `experiment-readout` | Verify a preregistration, map arms to posts, label the readout | `--prereg F`, `--posts F`, `--hash`, `--attribution`, `--json` |
| `experiment-next` | Next dimension from prior classified results: a hand-made list and/or saved `experiment-readout --json` files (labels used unchanged) | `--results F`, `--readout F` (repeat), `--readout-dir DIR`, `--json` |
| `experiment-simulate` | SIMULATED label distribution for a seeded synthetic run | `--seed`, `--pairs`, `--lift`, `--noise`, `--trials`, `--json` |

## Audit profiles and content

| Command | Purpose | Key flags |
|---|---|---|
| `profile-audit` | Per-platform profile readiness from an owner snapshot | `--file F`, `--platform`, `--json` |
| `post-audit` | Creative checks and comparable-age outcomes | `--file F`, `--metrics-csv`, `--owner-authorized`, `--platform`, `--age-hours`, `--json` |
| `seo-destinations` | Canonical landing page + competing-message check | `--briefs F`, `--type release/paper/product/docs`, `--project`, `--suggest-for` |
| `seo-content-check` | Discoverability of post texts: length, alt text, hashtags, UTM, duplicates | `FILE`, `--name`, `--online` (**online** link previews), `--json` |
| `landing-check` | SEO brief + campaign CTA landing-page checks | `--briefs F`, `--campaign`, `--online` (**online**), `--allow-origin`, `--json` |
| `ai-visibility` | Your own pages vs AI answer engines and search: robots.txt per crawler (search / training / user fetch), noindex, nosnippet, canonical, JSON-LD, `fediverse:creator`, llms.txt as info only; never guarantees citation | `--url U --html F --robots F` (offline), `--briefs F`, `--page-id`, `--online` (**online**, brief pages only), `--allow-origin`, `--json` |
| `utm-link` | Canonical UTM-tagged URL | `URL`, `--platform`, `--campaign`, `--content`, `--term` |
| `creative-preflight` | Editorial checklist for 1-6 drafts; not a reach prediction | `--file F`, `--voice-profile F`, `--json` |
| `voice-profile` | Statistics of how the owner writes, from their own past posts; writes one NEW private file, stores no post text | `--from F` or `--from-db`, `--account ID`, `--platform P`, `--out NEW`, `--json` |
| `voice-check` | Advisory: draft vs the owner's voice profile and a heuristic stock-AI-phrase list; never blocks, rewrite flagged lines yourself | `--profile F`, `--text T`/`--file F`/`--post-id ID`, `--platform P`, `--json` |
| `media-check` | Media manifest: headers, limits, alt text, captions, rights | `MANIFEST`, `--json` |
| `ai-label-check` | Advisory: per target platform, is an AI-content label required / recommended / not required / unknown, what to tick natively, cited source; optional EU AI Act Art. 50 note (secondary source, not legal advice); never blocks | `--file F`/`--post-id ID`, `--platform P`, `--media MANIFEST`, `--authorship A`, `--eu`, `--public-interest yes\|no`, `--editorial-review yes\|no`, `--json` |
| `eu-review-evidence` | Human-review evidence per published post for the EU AI Act Art. 50(4) exemption: approver, time, approved payload hash, text hash, approval-ledger line + integrity, authorship if recorded; database and ledger only read, never repaired; not legal advice | `--since 30d`, `--account`, `--platform`, `--post-id`, `--limit`, `--format text/json/markdown`, `--json` |
| `media-storyboard` | Brief -> vertical-video storyboard, SRT, manifest; generates no media | `--campaign F`, `--out DIR`, `--check`, `--duration`, `--aspect`, `--json` |
| `media-cutlist` | One master video -> per-platform cut list + suggested ffmpeg | `PLAN`, `--json` |

## Research channels, sources, creators and engagement

| Command | Purpose | Key flags |
|---|---|---|
| `distribution-plan` | Regional channel research + hypothetical timing; never schedules | `--region R`, `--goal contributors/adoption/awareness/collaboration`, `--limit`, `--date`, `--timezone` (needed for `global` with a date), `--market US/global/tiktok@US` (sourced market flag; ranking unchanged), `--json` |
| `social-web-plan` | Open social web map: which accounts are on ActivityPub / AT Protocol / Nostr / Farcaster, bridges (Bridgy Fed, opt-in), canonical-home hypothesis, duplicate-reach warnings, identity checklist, gaps; reads accounts metadata only, never the queue | `--accounts A`, `--setup FILE`, `--presence PLATFORM`, `--bridged ID`, `--fediverse-sharing ID`, `--site URL`, `--site-kind ghost/wordpress_activitypub/bridgy_fed_web/plain`, `--json` |
| `discovery-plan` | Platform discovery and topic/hashtag ideas; no reach forecast. With an existing DB (read-only) each tag shows your own results (n, median vs the rest); the ranking never changes | `--file F`, `--account ID`, `--json` |
| `sources-scan` | Rank opt-in GitHub/feed/changelog/DOI signals | `--config F`, `--online` (**online**), `--state F`, `--mark-seen`, `--top N`, `--json` |
| `signal-brief` | One signal -> NEW campaign scaffold + brief; never stages | `--scan F`, `--signal-id ID`, `--brand B`, `--out F`, `--objective`, `--json` |
| `release-brief` | Saved GitHub release JSON or CHANGELOG section -> NEW campaign folder + brief.md; BLOCKED until written; never stages | `--release F` or `--changelog F --version X.Y.Z`, `--repo OWNER/NAME`, `--out NEW_DIR`, `--brand B`, `--accounts A`, `--objective`, `--campaign ID`, `--json` |
| `creator-match` | Creator relevance shortlist from owner-curated evidence; contacts nobody | `--file F`, `--json` |
| `creator-kit` | NEW private source kit for creator commentary; never sent | `--file F`, `--out NEW_DIR`, `--json` |
| `creator-coverage` | Owner-confirmed creator pickup at 48h/7d | `--file F`, `--json` |
| `engagement-fetch` | Plan only; **online** read-only public replies/quotes; never replies | `--online`, `--out F`, `--account`, `--post-id`, `--quotes` |
| `engagement-triage` | Comment triage + reply/issue DRAFTS; no send path | `INPUT`, `--docs-map`, `--out`, `--issues-dir`, `--json` |
| `engagement-queue` | Aging/SLA review queue over an engagement export | `INPUT`, `--docs-map`, `--sla`, `--json` |
| `followup-suggest` | Owner-set metric thresholds -> follow-up briefs + next commands for review; never likes, replies, DMs or drafts. Without `--rules`: "took off" = 2 x your own median interactions at ~7 days (10+ posts with a result), labelled "derived from your N posts" | `--rules F` (optional), `--account ID`, `--since DATE`, `--state F`, `--record`, `--optout F`, `--json` |
| `engage-scout` | Ranked daily shortlist of conversations to join + an angle each (never reply text); human-only venues are read-it-yourself; never posts | `--config F`, `--input F`, `--online` (**online**, GET only), `--platform P`, `--limit N`, `--state F`, `--record STATE`, `--optout F`, `--now`, `--json` |
| `engage-log` | Owner marks a scout item replied/skipped; weekly correlational readout | `--state F`, `--mark ID`, `--replied`, `--with-link`, `--skipped`, `--at`, `--readout`, `--outcomes F`, `--weeks N`, `--json` |

## Data, privacy and schemas

| Command | Purpose | Key flags |
|---|---|---|
| `archive` | Private folder, one sub-folder per post; only adds | `--copy-files` (hash-verified), `--out DIR`, `--post-id`, `--platform`, `--since`, `--json` |
| `media-relink` | After moving: repoint attachment paths whose SHA-256 still matches; dry run | `--from OLD`, `--to NEW`, `--json`; `--apply` is the owner's decision |
| `data-export` | Redacted JSON export of the local database | `--out NEW_FILE` |
| `data-purge` | Retention purge plan (dry run) | `--policy F`; **OWNER** `--apply` |
| `privacy-status` | Database permissions, size, row counts, retention config | `--policy F` |
| `schema-validate` | Canonical schema check of one JSON document | `FILE`, `--type T` |
| `schema-export` | Local rows as canonical schema JSON lines | `--out F`, `--type T` |

## Notes that apply across commands

- **Repeats.** `approve` refuses a blocking repeat unless the owner passes `--allow-repeat`; never suggest it unless the owner says re-sharing is intended. The send refuses an exact repeat again (`repeat_of_shared_material`), so of two approved copies only one goes out unless the other was approved with `--allow-repeat`. Offer `mark-shared` after a by-hand post.
- **Moving.** The workspace is `config/` and `state/` (with `state/approval-ledger.jsonl`); it holds no credentials, which are re-set on the new computer. Never suggest copying a secrets file. Details: `docs/MOVING.md`, `docs/JOURNAL.md`.
- **Adapters.** Only Bluesky, Mastodon, Telegram and LinkedIn (personal profile) have enabled text adapters; Threads, Facebook Page, Instagram, YouTube, TikTok, Mastodon images and replies are mock-tested and registry-refused until the owner records a live receipt. Everything else is manual export. See `references/publishing.md`.
- **Examples.** The bundled campaign and experiment JSON are format demonstrations with placeholders, never auto-approved; real brand and account profiles stay private, outside public Git trees.
