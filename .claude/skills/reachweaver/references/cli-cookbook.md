# CLI cookbook: the reproducible offline path

Moved out of `SKILL.md` so the always-loaded file stays small. Commands marked as owner-run are run by the owner in their own terminal, never by Claude.

## Commands

**Command prefix.** `RW` means `python3 <skill-dir>/scripts/run.py`, where skill-dir is the directory containing this SKILL.md; run it **from the user's project directory**. Inside a full ReachWeaver checkout `python3 -m reachweaver.cli` is equivalent. The `python -m reachweaver.cli` lines below are written for the checkout: for a copied skill, replace that prefix with `RW`. Paths under `docs/`, `examples/` and `config/` exist only in a checkout; in a copied skill use `assets/` (e.g. `assets/accounts.example.json`).

**First run.** Run `RW doctor`. If accounts are missing, copy `assets/accounts.example.json` to `config/accounts.json` in the user's project and edit public handles only. Never ask for secrets in chat; credentials come only from environment variables the owner sets. Do not assume copied references outside the skill folder are present.

```bash
python -m reachweaver.cli init  # local SQLite state only
python -m reachweaver.cli doctor
python -m reachweaver.cli credentials-audit  # names only, never values; exit 1 on findings
# With permission to contact the provider, after owner enters credentials privately:
# python -m reachweaver.cli connect-check --account my-bluesky --online
python -m reachweaver.cli campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --json  # add --history-db state/reachweaver.sqlite for repetition warnings
python -m reachweaver.cli campaign-export --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --out /tmp/reachweaver-preview
# Configure local ignored config/accounts.json ONLY when ready to stage; never store credentials in the repo.
python -m reachweaver.cli campaign-stage --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json
python -m reachweaver.cli list
python -m reachweaver.cli show POST_ID  # review EXACT full text and destination
python -m reachweaver.cli check POST_ID
python -m reachweaver.cli approve POST_ID
python -m reachweaver.cli schedule POST_ID --at 2026-10-15T09:00:00+02:00
python -m reachweaver.cli publish --post-id POST_ID  # one-post DRY RUN
# Only with actual account authorization, explicit user review, environment secret configuration, trusted persistent host.
# The OWNER runs this on the production host (REACHWEAVER_ENV=prod is also required); Claude never runs it:
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 python -m reachweaver.cli publish --live --post-id POST_ID
python -m reachweaver.cli import-metrics --csv /path/to/private-native-export.csv  # cells: count, unknown, suppressed, not_available, delayed, estimated:N
python -m reachweaver.cli metrics-fetch --online --dry-run  # then without --dry-run: read-only PUBLIC Bluesky/Mastodon counters; never posts
python -m reachweaver.cli report --format markdown  # exact values only + coverage/status; json (default) or csv
python -m reachweaver.cli engagement-fetch  # plan only; --online --out replies.json on request, then engagement-triage replies.json
python -m reachweaver.cli engagement-queue export.json  # offline aging/SLA queue of items needing a human; read-only
python -m reachweaver.cli weekly-digest --snapshot state/owned-profiles.json --out state/digest.md  # suggest (+ report with --include-db) as one private Markdown file; never sent
python -m reachweaver.cli ops-metrics --format prometheus  # read-only monitoring counts
python -m reachweaver.cli ops-summary --json  # one redacted read-only readiness + metrics report; exit 1 when blocked
python -m reachweaver.cli start  # read-only: prints the exact next command for the current accounts/database/queue state (--json); runs nothing
python -m reachweaver.cli ui  # the OWNER runs this in their own terminal: read-only local dashboard on 127.0.0.1 (queue, post review sheets, readiness, accounts); cannot approve, schedule, pause or publish; the URL carries a per-run token, so never paste it into chat or an issue; Claude must not start it or read its URL
python -m reachweaver.cli completion bash  # prints a shell completion script (bash, or zsh via bashcompinit); reads no database; the owner loads it, Claude does not
python -m reachweaver.cli experiment-evaluate --file examples/experiment.example.json
python -m reachweaver.cli media-check media/manifest.json --json  # offline media gate; no upload
python -m reachweaver.cli media-attach POST_ID --file chart.png --alt "Bar chart: downloads rose from 10 to 40"  # Mastodon draft image; bound at approval; live image send is registry-refused (#13, owner-approved, disabled until a live receipt)
python -m reachweaver.cli media-attach POST_ID --url https://img.example.org/chart.jpg --alt "Bar chart: downloads rose from 10 to 40" --online  # Instagram draft image URL; one read-only GET; bound at approval; live send is registry-refused (#20, owner-approved, disabled until a live receipt)
python -m reachweaver.cli media-detach POST_ID  # remove a draft's attachments
python -m reachweaver.cli video-attach POST_ID --file demo.mp4  # TikTok draft video, default --mode creator_inbox (a draft the creator finishes in the app); SHA-256 and options bound at approval; never uploads; live send is registry-refused (#23, owner-approved, disabled until a live receipt); video-detach POST_ID removes it
python -m reachweaver.cli youtube-attach POST_ID --file demo.mp4 --title "Demo: first run" --made-for-kids no --category-id CATEGORY_ID  # YouTube draft video, --privacy private by default; offline, SHA-256 bound at approval; live upload is registry-refused (#22, owner-approved, disabled until a live receipt); youtube-detach POST_ID removes it
python -m reachweaver.cli youtube-analytics [--account ID] [--post-id ID]  # read-only, offline sampling report over stored metric snapshots of published YouTube videos; exact/sampled/suppressed/delayed/not_available/unknown labels, never invents a view count (#22)
python -m reachweaver.cli draft --account my-mastodon --file reply.txt --reply-to ITEM_ID  # owner-written reply under the owner's own thread; prints the plan only, the owner adds --online to verify the parent; reply target bound at approval; sending is registry-refused (#17, owner-approved, disabled until a live receipt); see references/engagement.md
python -m reachweaver.cli approval-mode  # show the opt-in approval safeguards (#7 D6 confirmation, D7 keyed binding, wave 9 identity binding); only the OWNER tightens them (--confirmation tty_hash_prefix, --require-key, --require-identity); `approve --verify-identity --online` is an owner-run approval step, never run by the agent; never turn confirmation off or handle REACHWEAVER_APPROVAL_SECRET
python -m reachweaver.cli media-storyboard --campaign examples/campaign.reachweaver.json --out media/storyboard  # placeholder storyboard + SRT + manifest; no generation
# Optional read-only, after REAL platform-native metrics imported; no platform writes:
python -m reachweaver.cli timing-evaluate --account my-bluesky --timezone Europe/Rome --source native-export --metric clicks --denominator impressions --stratify format  # per-stratum readout; a stratum with too little data names no winner and points to calendar-plan's generic baseline (cold_start)
python -m reachweaver.cli baseline-eval --account my-bluesky --timezone Europe/Rome --source native-export --metric clicks --denominator impressions --since 2026-09-01 --until 2026-10-31 --slot exploit:Tue@09:00
```

For a **copied standalone skill**, substitute `<installed-skill>/assets/campaign.reachweaver.json` and `<installed-skill>/assets/brand.reachweaver.json` for repository `examples/` paths (and the matching `assets/experiment.example.json` for plans). These JSON fixtures are bundled and checked for drift. Real brand/account profiles must be stored privately outside public Git trees. The campaign example is a **format demonstration** and is not auto-approved. The experiment sample contains placeholders; replace with actual published post IDs and native metric snapshots. Do **not** publish example posts without reviewing all claims and account mappings. Only Bluesky, Mastodon, Telegram and LinkedIn (personal profile/member; organization pages are refused) have existing text-only adapters and *mocked* identity/permission checks; no user account has been connected or independently live-tested, so no owner account receipt exists. The optional Postiz backend is also mock-tested only and stays behind ReachWeaver approvals. Threads (text), Facebook Page (text/link), Instagram (single image), YouTube and TikTok (video) adapters, Mastodon image attachments and Bluesky/Mastodon reply sending also exist, owner-approved 2026-10-09, mock-tested only and disabled in the capability registry until the owner records a live receipt. For the first actual pilot, use `docs/FIRST-LIVE-PILOT.md` in the full repository (the standalone reference is `references/publishing.md`). An identity check is not a scope or publication guarantee. Others are **manual-draft** until separately implemented and authorized. See `references/publishing.md` and `docs/PLATFORM-MATRIX.md` in the full repository.

## Added after this cookbook was written

```bash
RW connect [--account ID] [--json]   # offline status table of the configured accounts (credential variable names only); creates nothing
# OWNER-RUN, in the owner's own terminal (it needs a person at a terminal): RW connect --guide
RW repeat-check --post-id POST_ID                  # read-only: same video/image file, identical wording or link already shared on this platform?
RW repeat-check --platform youtube --video clip.mp4 # before drafting; exact file bytes only
RW mark-shared --platform youtube --video clip.mp4 --text "..." --url URL --note "launch"  # record what the owner posted by hand; local ledger only
# approve POST_ID --allow-repeat is an OWNER decision, only when re-sharing is intended
```
