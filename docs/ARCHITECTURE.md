# Architecture and state protocol

```text
Owned repos / release notes / research / website / first-party analytics
             |
             v
Claude Code skill (/reachweaver): brand + audience + source-checked claims
             |
             v
Versioned campaign JSON -> offline validator -> account/brand allowlist
             |                              +--> manual draft package
             v
Atomic/idempotent SQLite draft staging (never authorizes a send)
             |
             v
Trusted human reviewer: claim check / rights / account / timing / approve
             |
             v
Local CLI + owner-only sqlite state -> explicit online account identity preflight
             |
             v
Scheduled due claim (BEGIN IMMEDIATE), optionally exact --post-id
             |
             +--> Dry-run default; global emergency pause
             |
             +--> Live sender (explicit flag + environment opt-in + production marker)
                         |
                         +--> Bluesky / Mastodon / Telegram / LinkedIn-member text APIs
                         +--> Optional Postiz backend (acceptance = needs_review)
                         +--> Registry-gated, OFF until a live receipt (mock-tested only):
                         |      Threads text, Facebook Page text/link, Instagram image,
                         |      Mastodon images, Bluesky/Mastodon replies (registry switch),
                         |      YouTube / TikTok video (SUPPORTED_LIVE_VIDEO + registry switch)
                         +--> Unsupported: manual-only, no fake success
                         |
                         v
              Remote receipt or needs_review (never blind retry)
                         |
                         v
     Platform-native analytics export -> snapshots -> coverage-aware report
                         |
                         v
       Matched-age paired directional evaluator -> next hypothesis
```

## One ranked, read-only next-action report (Issue #6)

`reachweaver/suggest.py` composes *existing validated* profile/post audit summaries, `engagement.triage_engagement` rule labels, `attribution.seo_conflicts`, `growth.next_hypotheses`, and (only with `--include-db`) `storage.queue_health` / `storage.report`. CLI parsing resolves optional files and uses `storage.connect_readonly` (SQLite `mode=ro` + `query_only=ON`). No `storage.connect`, migrations, HTTP requests, authorization or social side effects are allowed.

Priority is static workflow urgency, not a platform/ranking score: P0 remote uncertainty/opt-out, P1 blockers, P2 owner-confirmed profile/creative fixes or measurement coverage, P3 preregistered experiments. The report withholds raw commenter text/identities and private SEO URLs; account-level snapshot sources are not conflated with platform-level aggregated SQLite metrics. Missing observations are unknown, and a recommendation is never execution authorization. See [next actions](NEXT-ACTIONS.md).

`reachweaver/digest.py` renders a `suggest` result (and, only with `--include-db`, the `storage.report` envelope through `report_format.render_markdown`) as one Markdown file. The CLI loads inputs through the same branch as `suggest`. `digest.write_digest` is its only side effect: it creates a NEW file with `privacy.write_private_file` (mode 0600, `O_EXCL`, `O_NOFOLLOW`) and refuses an existing path or symlink. No network, no `storage.connect`, no send path; free text is escaped with `report_format.markdown_cell` and identifiers are reduced to a safe charset. See [next actions](NEXT-ACTIONS.md#weekly-digest-weekly-digest-issue-45-w7).

## Attested normalized native CSV evidence overlay

`reachweaver/profile_metrics_csv.py` loads a bounded, owner-authorized **local** CSV only when the operator explicitly supplies `--metrics-csv --owner-authorized` to `post-audit` or `suggest`. It does not call provider APIs, perform OAuth, open SQLite or write files. The strict schema joins only to accounts already in the profile snapshot; duplicate post keys are refused and metric source labels are **operator assertions**, not verified provider signatures. Dates are timezone-aware and normalized to UTC; missing metrics stay missing. The resulting in-memory posts go through the existing matched-age `post-audit` logic. See [CSV profile analytics](PROFILE-METRICS-CSV.md).

## Independent offline social profile/post audits

`reachweaver/profile_audit.py` and `profile_audit_registry.json` are a **local/read-only sidecar**. The commands `profile-audit` and `post-audit` execute **before** any SQLite connection and never import authorization credentials, write account metadata, stage drafts, schedule posts or invoke a social API. The strict versioned owner-snapshot contract rejects credential-like keys/values, unknown fields/metrics, invalid booleans, negative/nonfinite counts and overly large inputs. User-supplied fields are **unverified observations**.

A registry rule defines each network's applicable field checklist, officially sourced platform capabilities or editorial hypotheses, format experiments, and one action/exposure metric pair. The profile output exposes checklist coverage and produces a completeness percentage only when enough fields are known; **it is not a provider algorithm or a traffic prediction**. The post audit only compares native metrics inside same platform/account/format/source and matched post age; missing denominators, source or dates are excluded rather than zero-filled. It never runs experiments or blindly promotes the "winning" idea. Human-only channels remain human-only.

See [profile and post audits](PROFILE-AND-POST-AUDITS.md), [analytics protocol](GROWTH-EXPERIMENTS.md) and [user privacy](PRIVACY-LIFECYCLE.md). Do not add online reads to these offline commands without a dedicated security review and owner-approved scopes.

## Boundaries

- `reachweaver/policy.py`: platform catalogue, conservative text lint (placeholders, length, control characters, bidi overrides/isolates and invisible format characters), URL origin guardrails.
- `reachweaver/accounts.py`: schema for **public** account metadata, the `env_prefix` overlap rule (`env_prefix_conflicts`, enforced by `load_accounts`) and the **single credential accessor** `credential`/`require_credential`: environment variables, or only `$CREDENTIALS_DIRECTORY/<NAME>` files when systemd `LoadCredential=` sets that directory (issue #5). See [account and credential hardening](#account-and-credential-hardening-issue-5).
- `reachweaver/storage.py`: SQLite posts, event log, snapshots, emergency settings. POSIX state directory/file permissions are enforced (0700/0600; symlinks rejected); writes are local and persistent. `BEGIN IMMEDIATE` atomically claims sending eligibility; concurrently scheduled clients cannot claim the same post twice. `migrate()` runs inside `connect()` and only adds missing nullable columns (`metric_snapshots.window_start`/`window_end`, and the approval binding columns `posts.approved_payload_sha256`/`approved_by`/`approval_channel`, plus the #7 D7 `posts.approved_payload_mac`, `posts.media_json` for image attachments (#13, #20) `posts.video_json` for the one video record (#22, #23) and `posts.reply_target` for a reply (#17)) to an older database.
- `reachweaver/storage.py` metric snapshots (#15): an exact count lives in its INTEGER column; `suppressed`, `not_available`, `delayed` and `estimated:N` leave it NULL and are recorded in the nullable JSON `metric_status` column (added by `migrate()`, idempotent). Integer-column readers therefore see exact values only. Read helpers `list_posts`, `published_posts`, `snapshots_for`, `latest_snapshot` and `metric_states` replace raw SELECTs in `report`, `timing_analysis`, `baseline_eval`, `experiments` and `attribution`. `report()` aggregates exact values only: `null` at zero coverage, per-metric coverage and status counts, estimates in a separate labelled block.
- `reachweaver/report_format.py`: pure renderer for `report --format json|markdown|csv`. JSON keeps the existing envelope; Markdown prints exact values, incomplete coverage by status and estimates as separate tables (escaping pipes and HTML); CSV prints one row per platform and metric and prefixes formula-like cells with `'` (OWASP CSV Injection).
- `reachweaver/sendgate.py` (wave 9 item 3, owner-approved 2026-10-10; proposal): the send gate for direct Python callers. `storage.claim_sending` registers each committed claim (post id, account, platform, content hash, media hash list, caps) after COMMIT; `publishers.publish()` and `publishers.publish_video()` call `sendgate.consume` as their first statement, before the capability registry, credentials or any request. A missing, mismatched, reused or stale (15 minutes) claim, a claim made without caps in the CLI's range (1..10 per 24 h, gap of 15 minutes or more), or a closed `REACHWEAVER_ALLOW_LIVE` / `REACHWEAVER_ENV=prod` gate refuses with a fixed `send_*` code and nothing sent. The CLI loop and `postiz.run_live_send` already claim first, so their behaviour and signatures are unchanged. `connections.check_connection` records the objects it returns, so a hand-built `VerifiedConnection` passed to `publish()` is ignored and the identity is checked again. In-process only; see THREAT-MODEL.md T8 for the limit.
- `reachweaver/publishers.py`: platform-specific HTTP adapters. Sanitizes failures so Telegram bot token URLs and OAuth bearer tokens are never printed/logged. Redirects are rejected for all API methods. A sanitized error may carry an HTTP `status` and a bounded `retry_hint` (issue #11). Issue #13 (owner-approved 2026-10-09; disabled until a live receipt) adds a stdlib `multipart_body` encoder and the Mastodon image flow (`POST /api/v2/media`, bounded `GET /api/v1/media/:id` polling that honours the emergency pause, then `POST /api/v1/statuses` with `media_ids[]`); the write marker is set before the first upload. `publish()` refuses media unless the account is a direct Mastodon account and `capabilities.require_capability("mastodon", "image", "publish")` passes, which the bundled registry refuses.
- `reachweaver/attachments.py` (issues #13/#20, owner-approved 2026-10-09; disabled until a live receipt): the shared image attachment model; offline validation of local image attachments for Mastodon drafts: header-detected PNG/JPEG/WebP via the `media` parsers, size/count/alt-text limits from `policy.mastodon_media_limits` (owner pins only lower), focal point parsing, the strict `posts.media_json` codec, and `read_approved_bytes`, which reads a file once and returns its bytes only if they still hash to the recorded SHA-256. `storage` stores the list in the nullable `posts.media_json` column (added by `migrate()`); `approval_payload` uses the image tag `reachweaver.storage.approval/media-1` only when attachments exist, adding each attachment's SHA-256, MIME type, alt text and focus in order, so text-only hashes are unchanged. `published_text` (post text plus alt texts) feeds every opt-out check. The capability registry entry may list `live_media_formats` (validated against `capabilities.LIVE_MEDIA_FORMATS`); none does.
- `reachweaver/video.py` (issues #22/#23, owner-approved 2026-10-09; disabled until a live receipt): the shared video record in the nullable `posts.video_json` column: core `kind, version, platform, path, sha256, bytes, mime, duration_seconds, width, height, video_codec, frame_rate_fps` plus a `platform_options` object that each platform module validates. `bound_view` (everything except `path`) is what `approval_payload` binds under `reachweaver.storage.approval/video-1`. A post carries images (`media_json`) or one video, never both (`set_post_media`, `set_post_video`, `approve` and the live loop refuse both), and the record's `platform` must equal the post's. Video sending is gated only by `capabilities.require_video_mode` (`policy.SUPPORTED_LIVE_VIDEO` plus the platform's registry video switch), never by `reachweaver_adapter`.
- `reachweaver/ratelimit.py`: pure `Retry-After`/`X-RateLimit-Reset`/Telegram `retry_after` parsing, the C1/C2/C3 send-failure classification, cooldown deadlines (60 s–24 h) and bounded jittered backoff for the read-only pre-claim identity preflight only (issue #11). It never retries or resends a claimed post. Since #11 (2026-10-09): shared Postiz workspace cooldown scopes and the pre-network Bluesky `createSession` budget.
- `reachweaver/connections.py`: explicit online account identity checks, before claiming a live send. Bluesky authenticates and verifies the handle, Mastodon reads the authenticated user, Telegram checks bot/channel/permissions, LinkedIn compares the `userinfo` `sub`. The Instagram, TikTok, YouTube, Threads and Facebook checks exist but `check_connection` refuses those platforms first (outside `SUPPORTED_LIVE`/`SUPPORTED_LIVE_VIDEO`). No secrets/remote responses persisted. Identity proof is **not** permission proof on Bluesky/Mastodon.
- `reachweaver/credential_hygiene.py`: offline `credentials-audit`. Reports credential variable **names** per account (set/missing), orphans, placeholder values, the same secret reused across variables (salted in-memory digests, never output), accounts whose `env_prefix` makes them read the same or an overlapping variable, unsafe credential files or directories, environment credentials shadowed by a credentials directory, and, only for history files named with `--history`, the line numbers of literal `REACHWEAVER_*` secret assignments. It reads every value through `accounts.credential` and reports `credential_source`. See threat model and [account onboarding](ACCOUNT-ONBOARDING.md).
- `reachweaver/campaigns.py`: versioned brand/source/variant contracts, error/warning preflight, safe manual export and transactional idempotent *draft-only* stage. Does not fact-check linked webpages automatically.
- `reachweaver/experiments.py`: preregistered-plan *declaration* and matched-snapshot readout with missingness, cohort/guardrail checks, no causal claims.
- `reachweaver/ops_metrics.py`: read-only operational metrics (`ops-metrics`, issue #26). Counts from the audit `event_log` and current post/queue state through SELECT-only `storage` helpers (`post_status_counts`, `queued_posts`, `event_activity`, `needs_review_details`) plus `queue_health`. Renders JSON or the Prometheus text format and replaces an `--out` file atomically; `--out` refuses the state database, symlinks and any existing file not named `*.prom`/`*.json`. The CLI opens the database with `storage.connect_readonly` and fails (exit 2) on a missing database or on one whose schema lacks a column it reads (`storage.schema_gaps`), never creating or migrating it. Labels are allow-listed (account id shape, known platform, status, fixed reason/class/stage codes); anything else becomes `invalid`/`other`/`unknown`.
- `reachweaver/audience.py`: read-only loader for the owner-exported audience-activity CSV (ReachWeaver interchange format, issue #16) and its advisory weekly slots with provenance for `calendar-plan --audience-activity`. No network, no database.
- `reachweaver/history.py`: opt-in repetition checks of campaign variants against the local posting history (`campaign-check`/`campaign-preview --history-db`, issue #10). Reads an existing database through `storage.connect_readonly` (`mode=ro`, `query_only`, never created) and the SELECT-only `storage.posting_history`; reuses `claims.jaccard`. Emits `history_near_duplicate` and the `history_frequency` brand-hygiene heuristic as warnings only, naming post ids, statuses and dates, never prior text.
- `reachweaver/worker_log.py`: `publish --log-json [--log-file PATH]` structured worker logs (issue #26). One `privacy.structured_log` JSON line per decision with fixed event names (`run_start`, `gate_refused`, `post_skipped`, `post_claimed`, `post_published`, `post_needs_review`, `run_end`) and reason codes, a sortable `run_id` and a `seq`. Identifiers are allow-listed by shape, errors reduced to sanitized codes, remote URLs to scheme/host/path; post text, handles and secrets are never passed in. Purely observational: the destination is opened (0600, `O_APPEND|O_NOFOLLOW`) and `run_start` written before the due query, account load or claim, so an unusable log refuses the run (exit 2); later write failures are counted, never raised. The only database access is two `SELECT`s after a Postiz hand-off, made only when logging is on.
- `reachweaver/youtube_analytics.py`: read-only YouTube analytics-sampling report (`youtube-analytics`, issue #22 follow-up). SELECT-only over `posts`, `metric_snapshots` and the `published` audit event through `storage.metric_states`; opened with `connect_readonly`, no network, no credential. Labels each metric exact / sampled / suppressed / delayed / not_available / unknown, repeats the video receipt fields, never turns unknown into zero and never sums estimates with exact counts. See [YouTube analytics sampling report](YOUTUBE.md#analytics-sampling-report).
- `reachweaver/youtube.py`: YouTube video upload adapter (issue #22, owner-approved 2026-10-09; disabled until a live receipt), **unreachable**: the registry keeps YouTube's `reachweaver_video_upload` switch `off` and `policy.SUPPORTED_LIVE_VIDEO` is empty. Its record is the shared video record (`video.py`) with YouTube `platform_options`. Video spec validation and SHA-256 hashing for `youtube-attach`, `channels.list?mine=true` identity, and the resumable `videos.insert` upload. See [YouTube video upload](#youtube-video-upload-issue-22-owner-approved-2026-10-09-disabled-until-a-live-receipt).
- `reachweaver/replies.py` (issue #17, owner-approved 2026-10-09; disabled until a live receipt): reply targets for `draft --reply-to ITEM --online`. It reads the parent through unauthenticated, redirect-free GETs: Bluesky `getPostThread` with `parentHeight=20`, Mastodon `statuses/:id` and `/context`. It refuses the draft unless the thread root is a post ReachWeaver published for this account. It also re-triages the parent text, where `suspicious`, `harassment`, `spam` or a Bluesky moderation label need an exact `--override-flagged` label, and applies the opt-out list to the parent author (never overridable). The output is canonical JSON stored in the nullable `posts.reply_target` column, which `migrate()` adds. `storage.approval_payload` hashes that string, so a changed target needs re-approval. `target_problems` re-checks it at `approve`/`check`/`publish`. `capabilities.require_reply_capability` refuses every reply claim while the registry entry's `reachweaver_reply_adapter` is `none`, which is the bundled value. The send reuses `publishers.publish(reply_target=...)`: Bluesky `record.reply` holds root and parent strongRefs, and Mastodon gets `in_reply_to_id` with the stricter of the parent's and the account's visibility. There is no second pipeline. A reply is text-only: attaching images or a video to a reply row, approving one, or sending one is refused (`storage.REPLY_MEDIA_REFUSED`). See SECURITY-REVIEW-PROPOSAL-17-reply-send.md.
- `reachweaver/cli.py`: explicit human interfaces, default dry-run, no daemon.
- `reachweaver/confirmation.py` (#7 D6, owner-approved 2026-10-09: proposal): an opt-in human-presence check (`settings.approval_confirmation = tty_hash_prefix`, off by default, set with `approval-mode`). When it is on, `approve` and `publish --live` (native and Postiz paths, pre-claim and pre-network) require terminals on stdin/stdout and the first 8 hex characters of the payload sha256, typed on `/dev/tty` (never stdin), compared in constant time, with 3 attempts. It adds friction and does not prove a human is present: a pseudo-terminal can pass it. The keyed half (D7) lives in `storage.py`: an optional `posts.approved_payload_mac` HMAC-SHA256 tag under `REACHWEAVER_APPROVAL_SECRET`, read via `accounts.credential`, verified wherever the plain hash is.
- `.claude/skills/reachweaver`: on-demand procedural intelligence, bundled executable copy.

### Status transitions

```text
DRAFT --approve (binds hash H)--> APPROVED --schedule (verify H, rebind time)--> SCHEDULED
  ^                                  |                                              |
  |                                  |                                              |
  +---unapprove (clears H)-----------+------------------unapprove (clears H)--------+
  |                                  |                                              |
  |                                  +--claim (H must match) --+--claim (H must match, due)
  |                                                            v
  +---reconcile-not-published<--NEEDS_REVIEW<--(uncertain)--SENDING--> PUBLISHED

DRAFT/APPROVED/SCHEDULED --cancel (clears H)--> CANCELLED
APPROVED/SCHEDULED with H missing or not matching: claim and schedule refused, status unchanged
```

Unapproved posts are never eligible; scheduled posts are not eligible before due timestamp. `SENDING` may remain after process termination because the remote outcome is unknown. Never reset that state automatically. Posts cannot be edited after approval; recreating a draft preserves review integrity.

**Approval payload binding (issue #7, owner-approved 2026-10-08).** Each transition in the diagram treats the hash `H` as follows:

- **`approve`** (`draft` only) stores `posts.approved_payload_sha256`. This is a SHA-256 over the canonical JSON (the `schemas.canonical_json` serialization) of the binding tag `reachweaver.storage.approval/1`, `account_id`, `platform`, `content` (byte-exact), `scheduled_at` (always null at approve time), `source_url` and `campaign`. A post with images uses the tag `reachweaver.storage.approval/media-1` and adds `media` (per image, in order: `sha256, mime, alt, focus`, plus `url` for a hosted image); a post with a video uses `reachweaver.storage.approval/video-1` and adds `video` (the shared record without its local path). A reply row (#17) also includes `reply_target`; the key is absent for ordinary posts, and a reply is always text-only, so it never carries `media` or `video`. A text-only post's payload, and so its hash, is byte-identical to before; a post never carries both kinds. The #7 D7 keyed tag MACs this hash, so it covers image and video bytes exactly as it covers text. It also stores `approved_by`/`approval_channel` (`approve --approver NAME`, otherwise `$USER`/`$LOGNAME`, otherwise unattributed). These two are attribution, not authentication, and are not hashed. `approve` prints the hash prefix and the bound fields. `show` prints the binding state.
- **`schedule`** and **`calendar-plan --apply`** first verify `H` against the current row. A post that changed, or that has no hash, is refused with nothing changed (the calendar batch rolls back). After the check passes, `H` is rebound to the new `scheduled_at`, so tampered content is never laundered into a fresh hash.
- **`unapprove`** and **`cancel`** clear `H` and the attribution. `reconcile` is unchanged (owner decision D9). A stale hash on a reconciled draft is inert, because only `approved`/`scheduled` rows are claimable and `approve` always recomputes the hash.
- **`claim_sending`** recomputes `H` inside its `BEGIN IMMEDIATE`, after the pause check and before the rolling budgets. The sender also passes `expected_sha256`, the hash of the copy it is about to send, so a row read before the lock cannot differ from the bound row that gets claimed. A missing hash (`approval_hash_missing_reapproval_required`, a row approved before the upgrade) or a changed one (`approval_hash_mismatch_reapproval_required`) fails closed. The post stays `approved`/`scheduled` (D5), `last_error` is set, one `approval_binding_failed` event is written (hash prefixes only, deduplicated), nothing is claimed, no budget is used, and `ValidationError` is raised. A table without the columns fails closed with `approval_binding_unavailable`. On such a table `approve`/`schedule` are refused too, while `unapprove`/`cancel` keep working.
- **Both live senders**, the CLI `publish --live` loop and Postiz `run_live_send`, call `check_approval_binding` before the identity preflight. An unbound or changed post is `BLOCKED` without any network call. `publish` dry-run flags such posts without writing anything.
- **`migrate()`** adds the three nullable columns to older databases and does not grandfather existing approvals (D2). `queue_health`/`health` list every post that needs re-approval as `attention` (D10).
- **Re-approval** is always `unapprove`, `show`, `approve` (then `schedule` again if the post was scheduled). There is no in-place rebind and no bypass switch. See [worker operations](WORKER-OPERATIONS.md#upgrade-note-approval-payload-binding-issue-7) and SECURITY-REVIEW-PROPOSAL-7-11.md.

By default the hash is unkeyed: anyone who can write the database can change the content *and* recompute the hash. It detects unsanctioned mutation (naive writes, scripts, future code paths); it does not authenticate the approver. The opt-in keyed HMAC binding (D7, `approval-mode --require-key` with `REACHWEAVER_APPROVAL_SECRET`, owner-approved 2026-10-09) closes that gap only while the key stays out of the writer's reach; a signed binding and replay protection are still open (threat model T7/T8). Opt-in `approve --verify-identity --online` (wave 9 item 2, owner-approved 2026-10-10) additionally binds a fingerprint of the verified account entry (`approval/id-1`, `reachweaver/identity.py`); `publish --live` refuses a changed entry before any request, and `approval-mode --require-identity` makes it mandatory. It binds which account, not who approved.

## Offline account-aware calendar planner

`reachweaver/calendar.py` is the pure/offline **proposal layer** between approved drafts and the existing durable outbox. It accepts an explicitly configured account ID, an audience IANA timezone, a future date window, optional user-supplied weekly slots/blackouts and a minimum spacing rule. The starter windows are low-confidence observational priors, not an ML ranking model. It creates no remote post or schedule receipt.

`calendar-plan --apply` takes the proposed plan, acquires a SQLite `BEGIN IMMEDIATE` lock, rechecks each account/platform/approval, emergency stop, future timestamp and existing schedules, then applies all transitions or rolls back all. It is restricted to the four text-only locally supported publishing adapters (`policy.SUPPORTED_LIVE`: Bluesky, Mastodon, Telegram, LinkedIn member); manual-only networks cannot be applied. It never enables a worker or remote auth. DST ambiguous/nonexistent hours raise errors rather than silently folding.

`reachweaver/audience.py` (issue #16) loads an owner-exported hour-of-week audience-activity CSV in ReachWeaver's own interchange format (`reachweaver.audience-activity.v1`; no platform export has been verified against it). It is stdlib-only and read-only: a bounded read (1 MiB, 10,000 rows), strict columns, one timezone/source/`as_of` per account, duplicate (account, weekday, hour) rows rejected, missing or unknown hours kept missing (never zero), formula-injection prefixes rejected (OWASP), and `source`/`consent` redacted through `privacy.redact`. `utc_hour_of_week` normalises each row with the UTC offsets of the `as_of` week; DST gaps are `nonexistent` and folds use `fold=0`. `derive_slots` ranks known, positive hours into weekly slots with provenance and a staleness flag (default 90 days). `propose_calendar(..., audience_activity=...)` uses those slots instead of the generic prior, attaches `provenance` to every entry, adds a top-level `audience_activity` summary, and skips (and lists) DST-gap/fold occurrences instead of failing. With `audience_activity=None` the code path and output are unchanged; a golden-output test enforces this. The module opens no database and makes no network call.

See [calendar quickstart](CALENDAR-PLANNER.md). Future ML optimization and durable worker operation remain separate from this safe baseline.

## Scheduler

Install on a single trusted persistent host where the DB and credentials survive restarts. Example (user sets env securely via process supervisor, not crontab plaintext):

```text
*/15 * * * * cd /path/to/reachweaver && /path/to/venv/bin/reachweaver publish --live --scheduled-only --max-lateness-minutes 180 --max-per-24h 2 --min-gap-minutes 60
```

Only enable after user-approved credentials, reviewed scope, and testing. Multiple hosts against a shared SQLite file over NFS are not supported; production scale requires PostgreSQL/leases, retries respecting idempotency and reconciliation. Platform external receipts are the source of truth.

## Rolling live budgets, provider cooldowns, due-time guard and queue health (issue #11)

In `storage.claim_sending`, optional `max_per_24h` and `min_gap_minutes` are checked **inside the existing SQLite BEGIN IMMEDIATE lock**, before transitioning an eligible post to `sending`. CLI `publish --live` supplies conservative defaults of two attempts per account per rolling 24 hours and 60 minutes between attempts. All `send_claimed` events in the durable audit log count for the owning account, including uncertain and later human-reconciled attempts. The budgets are **application-level**, not spend controls; `caps.json` version 2 can tighten them per provider, and provider 429s add the cooldowns described below. Direct library callers may still omit the budgets in `claim_sending` itself, but since wave 9 item 3 `publishers.publish()` refuses (`send_caps_required`) a claim made without caps in the CLI's range, and refuses any call without a prior claim; the cooldown check in `claim_sending` always applies.

`pending(..., scheduled_only=True, scheduled_not_before=...)` powers unattended safe mode: approved-but-unscheduled posts cannot be sent by that worker query and scheduled items older than the selected lateness window cannot starve fresh due work. Missed items remain scheduled and show up in `queue-health` for human review. The CLI validates 5–10080 minutes of lateness, defaulting to 180 for scheduled-only mode.

`queue_health` reads status totals, account attempt counts, overdue schedules, `sending` older than an owner-chosen threshold, all `needs_review` records, approved/scheduled posts whose approval binding needs re-approval (`approval_rebind_required`, issue #7) and active provider cooldowns (`provider_cooldowns`); it never contacts providers or mutates/retries posts.

**Provider rate limits (owner-approved 2026-10-08, proposal part 2, D11–D16).** `reachweaver/ratelimit.py` holds the pure parts; nothing in it can move a claimed post.

- **Error capture (D15).** `publishers.http_error` turns an `HTTPError` into the same sanitized `http_<code>` as before, plus two typed attributes on `PublishError`: `status` and `retry_hint` (`RetryHint(delay_seconds, source)`). `ratelimit.hint_from_http_error` reads, for 429/503 only, a `Retry-After` header (RFC 9110 delay-seconds or HTTP-date), Telegram `parameters.retry_after` from at most 4 KB of the error body (only when the caller passes `telegram_body=True`: Telegram `sendMessage` and the `getMe`/`getChat`/`getChatMember` preflight), or Mastodon `X-RateLimit-Reset`. Values longer than 64 characters, non-numeric text and past dates are ignored. Bluesky `ratelimit-reset` is not used (unit unverified). The no-redirect opener, the 1 MB body cap and the rule that no URL, header or body text reaches an error are unchanged. `postiz._call` and `linkedin._actionable` keep the attributes through their own codes (`PublishError.with_code`).
- **Classification (D11).** Each adapter calls `publishers.mark_write_started()` immediately before it hands the content-create request to the network (Bluesky `createRecord`, Mastodon `statuses`, Telegram `sendMessage`, LinkedIn `rest/posts`, Postiz `POST /posts`); the senders call `reset_write_marker()` before `publish`. `ratelimit.classify_send_failure` returns `certain_not_sent` when the marker is unset, `likely_not_created` for a 429 from a direct Bluesky, Mastodon or Telegram create, and `ambiguous` otherwise (unexpected exceptions are always `ambiguous`). The label is appended to the `needs_review` event (`uncertain_send(..., send_class=)`) and shown by `dead-letter`. It never changes state.
- **Cooldown (D12).** On a 429 (pre-claim preflight or post-claim create), `ratelimit.record_rate_limit` computes the deadline with `cooldown_deadline` (provider hint, else LinkedIn's next 00:00 UTC for a direct LinkedIn call, else 15 minutes; clamped to [floor, 24 h], floor 60 s or higher from caps) and `storage.record_provider_cooldown` appends one `provider_cooldown` event: `account=<id> until=<UTC> source=<label> reason=http_429 stage=preflight|send [class=<label>] [clamped=ceiling]`, with `post_id` NULL (preflight) or the claimed post. `storage.provider_cooldowns` returns the latest active deadline per account (cooldowns only extend; malformed rows are ignored). Both live senders check `active_provider_cooldown` after the pause, opt-out and approval-binding gates and **before** the capability check, credentials and identity preflight, printing `SKIPPED ... account_provider_cooldown_active` with no network use. `claim_sending` re-checks inside its `BEGIN IMMEDIATE`, after the pause and the approval hash and before the budgets, and raises without writing anything. `privacy.apply_purge` keeps `provider_cooldown` events younger than 48 hours, like `send_claimed`.
- **Preflight backoff (D13).** `ratelimit.preflight_with_backoff` wraps only the identity check (`check_connection`, `postiz.verify_integration`). It retries a 429, 500, 502, 503, 504 or network error at most `preflight_retries(platform, caps)` times (≤ 2; 0 for Bluesky, whose check is `POST createSession`), sleeping `uniform(0, min(30, 2·2^n))` or the server delay if that is larger and ≤ 30 s. A server delay above 30 s stops at once. `is_paused` is checked before and after every sleep. All of this happens before `claim_sending`.
- **Shared scopes and the `createSession` counter (owner-approved 2026-10-09, proposal).** `ratelimit.cooldown_scopes(account)` maps an owner-declared `postiz_workspace` to `postiz-workspace:<slug>`. `record_rate_limit` writes it as `scope=` on the `provider_cooldown` event. `storage.active_provider_cooldown(con, account_id, scopes=)` matches the account's own events plus any event of its scopes, united with the `cooldown_scope_members` table that each live run refreshes through `sync_cooldown_scopes`. `claim_sending(..., cooldown_scopes=)` uses the same check under its lock. `ratelimit.reserve_bluesky_sessions` calls `storage.reserve_create_sessions`, which counts `bluesky_create_session` events per `bsky-account:<sha256 prefix>` in each documented window (`session_windows`, registry facts, ⌊50%⌋) under `BEGIN IMMEDIATE`, and records the reservation or a `bluesky_create_session_refused` event before any request. Both additions only add waiting.
- **Budgets (D14).** `caps.json` version 2 adds a `providers` block (`caps.providers_for`, `effective_caps(..., providers=)`, `effective_rate_limits`); see [worker operations](WORKER-OPERATIONS.md#per-account-caps-file-tighten-only). `caps.BUILTIN_PROVIDER_CAPS` (owner decisions D19-4 and D21-5, 2026-10-09) is applied by `effective_caps` with or without a file: Threads at most 2 per 24 h, Facebook at most 2 per 24 h and at least 60 minutes apart.
- **Unchanged (D16).** Every post-claim failure still calls `uncertain_send` and ends in `needs_review`; there is no resend, `release-claim` or automatic reconcile path. Deployment is a separate explicit owner action, via the [worker runbook](WORKER-OPERATIONS.md) and inert `deploy/systemd/` samples. Do not run the older bare `publish --live` as unattended cron, because it can also publish approved unscheduled posts by design for supervised pilots.

## Current engineering limitations

- No automatic OAuth wizard or long-lived refresh token rotation; Bluesky App Password and user-provisioned credentials read through `accounts.credential`: `{env_prefix}` + `_IDENTIFIER`/`_APP_PASSWORD` (Bluesky), `_TOKEN` (Mastodon), `_BOT_TOKEN` (Telegram), `_ACCESS_TOKEN` (LinkedIn member; plus `_CLIENT_ID`/`_CLIENT_SECRET` only with the #14 `scope_check` opt-in), `_POSTIZ_API_KEY` (optional Postiz backend), and, reserved for the disabled adapters, `_ACCESS_TOKEN` (Threads, Instagram, YouTube), `_TIKTOK_ACCESS_TOKEN` (TikTok) and `_PAGE_ACCESS_TOKEN` (Facebook Page) (`accounts.CREDENTIAL_SUFFIXES`). The optional #7 D7 approval key `REACHWEAVER_APPROVAL_SECRET` is read the same way but belongs to no account. All live adapters are mock-tested only; no real owner account receipt exists.
- No enabled image/video upload: the Mastodon image (#13), Instagram image (#20), YouTube (#22) and TikTok (#23) paths are mock-tested and refused by the capability registry (and, for video, the empty `policy.SUPPORTED_LIVE_VIDEO`) until a live receipt. No multi-post threads, editing/deleting posts or media/accessibility generation. `media-check` validates owner-supplied media manifests, alt text, captions and rights offline only, and `media-storyboard` writes placeholder-only storyboard scaffolds with no media generation (#18).
- No automatic analytics fetch, follower discovery, live comment reading or social listening. `engagement-fetch --online` reads public replies under your own published Bluesky/Mastodon posts on explicit request only, and `engagement-triage` labels exported comments and writes reply/issue drafts only, with no send path (#17); `attribution-report` joins owner-exported analytics/GitHub traffic files read-only (#15).
- No web dashboard, multi-operator RBAC, cross-device synchronization or queue worker daemon.
- Conservative platform caps may reject content that provider would accept; verify live API limits when adding adapters.
- Mastodon Idempotency-Key is supported upstream for up to one hour only; all ambiguous sends still need review.
- Bluesky defaults to the bsky.social entryway. An optional owner-configured HTTPS `service` origin selects another PDS (#12), but this is mock-tested only and has no live PDS receipt.
- Existing draft content is not encrypted in SQLite; keep DB on a private protected filesystem and minimize private data.

## Campaign staging invariants (v0.2)

- `campaign_id` + `variant_id` is unique in `campaign_variants`; a staged campaign fingerprint covers exact brand/campaign/variant input. Re-running unchanged content returns existing IDs without new posts; mutation requires a new version/ID. The operation is wrapped in one `BEGIN IMMEDIATE` transaction to avoid partial inserts.
- Staging verifies a brand allowlist and that the configured account platform matches the requested post; it never calls a network API and creates only `draft` rows. It snapshots the declared evidence links/claims for audit, **but cannot prove those statements are true**.
- Only `text` variants can be staged. Other creative deliverables (Reels, videos, images, carousels) are human-reviewed briefs until independent rights/media/API implementations exist.
- Portable standalone skill mirrors the canonical Python files under `scripts/reachweaver`; CI rejects divergence.

## Observational timing evaluation (issue #16)

`reachweaver/timing_analysis.py` adds **offline, read-only** grouping of existing native source-labeled metric snapshots from an explicitly named account. It checks IANA audience timezone and optional local date/campaign filters, requires a single numerator/denominator metric definition and compares the nearest available cumulative snapshot at a fixed post age (24–720 hours) with a strict tolerance. A missing snapshot, zero denominator or different source is excluded, never treated as zero; snapshot totals are not summed. It reports sample counts, per-daypart median and IQR, plus conservative exploratory flags requiring two minimum-sized groups. Six-hour dayparts are not exact publish-time prescriptions, and these are unrandomized associations subject to major unmeasured confounding. There is no network, queue modification or automatic selection of a `calendar-plan` slot. See [timing documentation](TIMING-EVALUATION.md).

`reachweaver/calendar_gaps.py` (CLI `calendar-gaps`, public issue #9) is a **read-only** report built on `calendar`'s candidate slots (operator `--slot`, owner audience-activity, or the generic baseline) and the posts in `scheduled` status. It labels each slot `filled`, `empty` or `past`, flags days over `--max-per-day` and posts closer than `--min-spacing-hours`, and can annotate empty slots with `timing_analysis` output. It runs on a `connect_readonly` connection (never migrates, never creates the database), makes no network request and cannot approve, schedule or publish. It cannot see posts queued in a platform's native scheduler.

## Evaluation against the non-personalized baseline (issue #16 slice)

`reachweaver/baseline_eval.py` (CLI `baseline-eval`) compares account-specific `--slot` arms and `--campaign-arm` variant campaigns with posts at `calendar-plan`'s generic slot, read through the new read-only accessor `calendar.generic_baseline_slots(platform)`. The period (`--since/--until`, audience-local send date) is required. Rates use the same nearest-age, exact-source snapshot rule as `timing_analysis.py`, and missing snapshots are counted, never zeroed. Statistics are stdlib-only and deterministic: a percentile bootstrap seeded by `--seed` plus the arm id, a two-sided permutation test of the median difference (exact up to 20,000 relabellings), and Holm–Bonferroni across every tested comparison in the run. Groups below `--min-posts` report `insufficient_data` with `null` estimates. Saved `experiment-readout --json` files are added as experiment items: their labels are copied unchanged from `growth.classify_result`, whose strict sign-test gate is not modified, and `separated_after_holm` requires `directional`. Results are split into `correlational` and `randomized` (owner-declared, via `growth_readout.describe_design`). The CLI branch is an early branch that opens the database only to `SELECT` (plus `storage.get_post`) and returns; it imports no publisher, connection or network code and cannot approve, schedule or publish. See [TIMING-EVALUATION.md](TIMING-EVALUATION.md#evaluate-against-the-non-personalized-baseline-baseline-eval).

## Experiment readout invariants (v0.2)

- Declared experiment plan fixes the primary and guardrail metrics, denominator, age/tolerance window, decision thresholds and minimum number of pairs. Plan timestamp is self-reported; for stronger auditability, store a timestamped/signed pre-publication copy.
- Each pair must use the same account, platform and metric data source; pairs across different cohorts are not aggregated. Both metrics are drawn from a single native snapshot nearest to the target elapsed time since `send_started_at` (not guaranteed exact publish time).
- Incomplete pairs, missing values, zero denominator, source conflicts, post-plan violations and insufficient repetitions are reported as excluded/inconclusive, never silently zero-filled.
- Organic feed distribution is confounded and unrandomized. `directional_positive` reports a directional association only; not causation, p-value significance or future reach guarantees.

## Account verification and first pilot safety (v0.3)

`doctor` never accesses the network. `connect-check --account ID --online` explicitly authorizes authentication/read-only requests without creating content. Live `publish --live` runs the same verification **before** the durable claim; failed identity checks leave the draft approval untouched. `publish --post-id ID` only evaluates one approved/due post; `show ID` prints the complete exact proposed text/destination for owner review. Post-claim network failures still transition to `needs_review` without automatic retry. Session establishment and provider permissions can change after verification, so a supervised native receipt remains mandatory. See [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md).

## Account and credential hardening (issue #5)

Owner-approved on 2026-10-09. These four changes tighten the credential-isolation and live-publishing rules; no other approval, dry-run, emergency-stop or reconciliation rule changed.

```text
publish --live
   |-- policy.require_live_gate(): REACHWEAVER_ALLOW_LIVE == "1" and REACHWEAVER_ENV == "prod"
   |       else ERROR (exit 2): no account loaded, no credential read, no request made
   |-- load_accounts(): per-account validation, then env_prefix_conflicts() -> fail closed
   |-- per post: pause, opt-out, approval hash, cooldown, capability, missing_credentials ...
   v
adapters (publishers, connections, linkedin, instagram [disabled], tiktok [disabled], youtube [disabled], threads [disabled], facebook [disabled], postiz, metrics_fetch, sources)
   '-- accounts.credential(NAME) / require_credential(NAME)
          $CREDENTIALS_DIRECTORY set?  yes -> only the file $CREDENTIALS_DIRECTORY/NAME
                                       no  -> only the environment variable NAME
```

- **Live gate.** `policy.require_live_gate` is the only place the two environment opt-ins are compared, and `cli.execute` calls it in the `publish` branch right after `--live` is seen, before `_accounts()`, for native adapters and the Postiz backend alike. Without `--live` the command is a dry run whatever the environment says. `policy.runtime_environment` echoes only `dev`, `staging` or `prod`; anything else is `unrecognized` (or `unset`) in `doctor`, `health`, `privacy-status`, `credentials-audit`, the dry run and the refusal message. No other command makes a write request: `connect-check`, `postiz-check`, `metrics-fetch --online` and `sources-scan --online` are identity checks or GETs, and engagement triage has no send path.
- **env_prefix overlap.** `accounts.env_prefix_conflicts` compares every pair of accounts over `ALL_CREDENTIAL_SUFFIXES` (every native adapter suffix plus `_POSTIZ_API_KEY`, whatever the account's platform). Identical prefixes, or any candidate variable that equals or is a string prefix of the other account's candidate, are refused by `load_accounts` with the two account ids and the variable names. `credentials-audit` reports the same rule for raw account lists.
- **Credential accessor.** `accounts.credential` is the one read path; `require_credential` turns a missing value into `missing_credentials: NAME`. With `$CREDENTIALS_DIRECTORY` set it opens the directory (absolute; `fstat` refuses group/world-writable), then the file relative to that descriptor with `O_NOFOLLOW | O_NONBLOCK`, refuses non-regular files, reads at most 64 KiB + 1 byte, decodes UTF-8 and strips one trailing newline. It never falls back to the environment while the directory is set. `CredentialError` messages name the variable only. Values read from files are kept in process memory so `privacy.redact` strips them exactly like secret environment variables. `tests/test_hardening.py` asserts that no other module reads a credential from the environment.
- **Agent harness.** `.claude/settings.json` denies Claude Code Bash commands containing `approve`, `publish --live`, `REACHWEAVER_ALLOW_LIVE=1` or `REACHWEAVER_ENV=prod`, plus (#7 decision 7) `approval-mode ... --confirmation off` and any command or file read naming `REACHWEAVER_APPROVAL_SECRET`. That is a harness configuration, not a ReachWeaver control.

## Canonical schemas and replaceable connectors (issue #4)

`reachweaver/schemas.py` defines versioned (`schema_version: "1.2"`; 1.0 and 1.1 documents stay valid) stdlib-validated contracts: `SourceSignal`, `Evidence`, `ContentBrief`, `Draft`, `PlatformVariant`, `MediaAsset`, `Account`, `Approval`, `PublishJob`, `PublishReceipt`, `AnalyticsSnapshot` and `Experiment`. It also defines a pure `transition_job` state machine for `draft > reviewed > approved > scheduled > publishing > published|failed|unknown` (+ `cancelled`). It is offline and side-effect free, and it does **not** replace or modify the SQLite outbox. `storage.claim_sending`, the emergency stop, budgets and reconciliation still govern every real send.

Decision: connectors stay replaceable because no vendor-specific type crosses the connector boundary. Upstream documents use plain `policy.PLATFORMS` values. Provider knobs live only in `PlatformVariant.platform_options`, and unknown top-level fields are rejected. A connector consumes a validated variant and emits a vendor-neutral `PublishReceipt`. A human `Approval` binds `account_id`, `platform` and the SHA-256 of the canonical-JSON payload. Any payload edit, account or platform mismatch, revoked or expired approval, or replay of a published/unknown idempotency key is rejected before `publishing`. `unknown` can only be resolved by a receipt, never by re-sending. Secret-like keys are rejected in every document. See [SCHEMAS.md](SCHEMAS.md) for field rules, the storage status mapping (`sending` ≈ `publishing`, `needs_review` ≈ `unknown`), migration rules and the `schema-validate` CLI.
## Offline engagement triage (issue #17 slice)

`reachweaver/engagement.py` is a **pure, offline** labeler for comments, replies and mentions that the owner exports into `reachweaver.engagement-export.v1` JSON. It runs deterministic keyword and regex rules and records the matched rule ids as the explanation. It orders a review queue (bug reports and questions first, spam last) and writes reply drafts and Markdown GitHub-issue drafts to **new** local files (`open(..., "x")`, never overwritten). Comment text is untrusted: control and format characters are stripped and length is capped. Before matching, text is folded: NFKC, casefold, accents and Arabic harakat removed, apostrophes unified, Persian/Arabic letter variants (`ي/ی`, `ك/ک`), tatweel and ZWNJ normalized. Prompt-injection directives become `suspicious`, with the excerpt withheld. All whitespace is collapsed before matching, so directives split across lines are still caught. Injection detection is best-effort keyword matching and can be evaded; human sign-off on every draft is the real control. Drafts are fixed templates plus owner-supplied docs-map URLs. They never quote comment text or handles. Spam, harassment, suspicious items, owner comments, opt-outs, threads that already have a draft, and the human-authored-only platforms (DEV, HN) get no draft.

Rules come in hand-written packs for `en`, `de`, `fr`, `es`, `it`, `pt` and `fa` (`RULE_PACKS`, `OPT_OUT_PACKS`; non-English rule ids are `<lang>_<rule>`). Each pack covers every label plus opt-out phrases. `guess_language()` is a transparent heuristic: script letter counts plus stopword overlap, `confidence = min(0.95, share × min(1, hits/4))`, with `candidates`/`mixed` for mixed-language comments. It is reported per item as `language_guess` and never as certain. Safety labels (`suspicious`, `harassment`, `spam`) and opt-out phrases run from every pack on every comment. Content rules run from English plus the guessed language or languages, and from every pack when confidence is below 0.5. Reply drafts stay English unless the owner adds `templates.<lang>` to the docs map. Such a template is used only for a confident, single-language guess, and `{docs}` is substituted with `str.replace`, never `str.format`. Every draft records `language_guess`, `template_language`, `machine_translated: false` and a native-speaker review note. There is no translation path. Per-language rule approach and false-positive caveats are in [ENGAGEMENT.md](ENGAGEMENT.md#multilingual-rules).

The module imports no network, publisher or subprocess code and defines **no send, reply, follow, like, DM or issue-filing path**. `SEND_PATH = None` and the prohibited-action constants are asserted in `tests/test_engagement.py`. Drafts carry `status: "draft_requires_approval"`. Posting stays a manual owner action in the official app. The live reply adapter (`replies.py`, see above) was owner-approved on 2026-10-09 and is gated off in the registry until a live receipt (`reachweaver_reply_adapter: none`). It reuses the ordinary approval, receipt and ambiguous-send rules. See [ENGAGEMENT.md](ENGAGEMENT.md#approved-replies-issue-17-owner-approved-2026-10-09-sending-blocked-by-the-registry-until-a-live-receipt).
## Read-only reply fetch (issue #17 slice)

`reachweaver/engagement_fetch.py` turns the public replies under your own published posts into a `reachweaver.engagement-export.v1` file for `engagement-triage`. CLI `engagement-fetch [--online --out FILE] [--account ID] [--post-id ID] [--max-posts N] [--depth N] [--max-replies N]` opens the database with `storage.connect_readonly` (SQLite `mode=ro`; a missing database is exit 2, an older schema is read as it is and never migrated). Without `--online` it prints the planned requests (no network, no file).

```text
posts (published, remote_id) + accounts.json  -- metrics_fetch.select_posts (shared eligibility, newest first)
        +-> bluesky  -> GET public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?uri&depth&parentHeight=0  no auth
        |               root author DID == URI DID; breadth-first walk <= depth, <= max_replies
        +-> mastodon -> GET {server}/api/v1/statuses/:id (owner check) -> GET .../:id/context   no auth
                        descendants linked to the post via in_reply_to_id, <= depth, <= max_replies; HTML -> text
        |  engagement.redact_private_data on <= 8000 chars, then cap 4000 (split fragments dropped); dedupe; <= 5000 items
        v
engagement.load_export (same contract as triage) -> privacy.write_private_file (NEW 0600 file)
```

Invariants: every request goes through `publishers.request_get_json` without headers (GET, no redirects, bounded body and timeout, sanitized `http_NNN` errors), so no credential is ever read or sent. The module writes no database row and imports no publish, claim or cooldown code. A 429 raises `metrics_fetch.RateLimited`, which stops the run; the provider delay is reported read-only and no cooldown event is written. Tests replace `publishers._send_http` and assert GET-only, header-free requests, the 0600 mode and an unchanged database. See [ENGAGEMENT.md](ENGAGEMENT.md#fetching-replies-engagement-fetch).

## Privacy lifecycle: redaction, export and retention (issue #26 slice)

`reachweaver/privacy.py` is offline and stdlib-only. `redact()` removes bearer/basic credentials, JWTs, Telegram bot tokens (including `/bot<token>/` URLs), Bluesky app-password shapes, GitHub `ghp_`/`github_pat_` tokens, URL user-info passwords, secret-named `key=value` pairs and, verbatim, the values of any `REACHWEAVER_*_TOKEN|_PASSWORD|_APP_PASSWORD|_BOT_TOKEN|_SECRET|_API_KEY` environment variable; emails are optional. `structured_log()` writes one sorted JSON line with a UTC `ts`, redacting every string value (emails by default); `worker_log.py` uses it for `publish --log-json`. `export_data()` returns posts, events, campaign variants, metric snapshots and settings with `schema_version`; `data-export --out` creates a new 0600 file with `O_EXCL`/`O_NOFOLLOW` and never overwrites.

Retention is two-phase. `plan_purge(con, policy, now)` is read-only; `apply_purge(con, plan)` runs in one `BEGIN IMMEDIATE` transaction with `PRAGMA secure_delete=ON` and **re-checks every hard rule in SQL**, so a stale or edited plan cannot widen the purge:

| Policy key | Effect when older than N days | Never touched |
| --- | --- | --- |
| `metric_snapshots_days` | delete snapshot rows | — |
| `event_log_days` | delete audit rows | `send_claimed` newer than 48 h; events of `sending`/`needs_review` posts |
| `cancelled_drafts_days` | delete cancelled post (+ its snapshots/events) | posts referenced by `campaign_variants` or with any `send_claimed` event: content becomes `[purged]` instead |
| `published_content_days` | content → `[purged]` | id, account, platform, `remote_id`, `remote_url`, status, timestamps |
| `campaign_evidence_days` | `evidence_json` minimized to evidence ids | variant rows and fingerprints (idempotency); variants of active posts |

Posts in `draft`/`approved`/`scheduled`/`sending`/`needs_review` are never altered or deleted. A `privacy_purge` event records only counts. `claim_sending`, `transition`, `reconcile` and the rolling budget are unchanged. `privacy-status` reads `PRAGMA database_list` for the DB path and reports file/dir modes, size, row counts, oldest records, retention configuration, the names (never values) of secret env vars present, and live-safety switches. See [privacy lifecycle](PRIVACY-LIFECYCLE.md).
## Campaign links, metric definitions and attribution (issues #15, #25)

`reachweaver/attribution.py` is pure and offline. `build_utm_link` normalizes a public HTTPS destination: it lowercases the host, drops the fragment, `utm_*` parameters and click trackers, and sorts the remaining query. It then appends `utm_source`, `utm_medium=social`, `utm_campaign`, an optional `utm_content` and an optional `utm_term` in a fixed order. The result is deterministic and idempotent, and an optional exact-origin allowlist is enforced. A versioned per-platform metric table (`metric_definition`) records the native field, definition, denominator, availability, source URL and `verified_date` only where the source was fetched and checked (LinkedIn entries describe member post analytics). Every other metric reports `unknown`. Snapshots may carry an optional UTC observation window; `report` adds per-platform `window_coverage` without changing its other keys. [WEEKLY-REPORT-EXAMPLE.md](WEEKLY-REPORT-EXAMPLE.md) is regenerated from SYNTHETIC fixtures by its test.

`attribution_report` reads the `posts` and `campaign_variants` tables (SELECT only) and joins them to an owner-exported analytics CSV and saved GitHub traffic JSON files. The output keeps three classes apart:

- exact post- or campaign-level UTM matches. These require a **published** post and a consistent source, medium and campaign.
- domain-level `referrer_only` rows, which are never credited to a post.
- `unattributed` rows, grouped by reason.

Blank sessions and conversions stay `None` and are counted separately from known values. Landing URLs pass through `report_landing_page` before they reach a bucket's `landing_pages`: only scheme, host, port, path and the canonical `utm_*` parameters survive, and the rest goes through `privacy.redact`, so tokens and e-mail addresses in an exported URL never reach the report (#26). Coverage gaps and non-causal warnings are always included. `load_seo_briefs` and `campaign_destinations` implement the [SEO integration contract](SEO-INTEGRATION.md). They take a local JSON file (credential-like keys are rejected) and return the highest-priority canonical landing page. They also flag duplicate canonical URLs, and overlapping non-ended campaigns that share a URL or primary keyword. None of this code makes network calls, writes to the database, changes the queue or publishes. See [ATTRIBUTION.md](ATTRIBUTION.md).
## Offline media manifest validation (issue #18 slice)

`reachweaver/media.py` is a **read-only, offline** pre-publication gate for images, videos and subtitle sidecars declared in a JSON manifest. It parses real headers with the standard library only: PNG IHDR, JPEG SOF, GIF, WebP VP8/VP8L/VP8X, and ISO BMFF `ftyp`/`moov/mvhd`/`trak/tkhd`/`hdlr`, applying the rotation matrix. For video it also reads the `stsd` sample-entry fourcc of the first video and audio track (H.264, HEVC, AV1, VP9/VP8, AAC via the `esds` object type, Opus, AC-3/E-AC-3), the average frame rate from `mdhd` timescale plus `stts` deltas (variable frame rate only when the deltas show it), the audio sample rate and channel count, and whether `moov` precedes `mdat`. The ISO BMFF walk is bounded: a 64 MiB `moov` read cap, 16 levels of container nesting, 200,000 boxes, 1,000,000 `stts` entries, and a stop at the first malformed box size. Each guard that fires becomes a warning and leaves the value `unknown`. It also checks file size, extension against magic bytes, and SRT/WebVTT well-formedness and monotonic timing. Translated subtitle tracks (a `subtitles[]` locale other than the asset's) are compared cue by cue with the asset's first well-formed source-locale track by `compare_subtitle_tracks`: cue count, missing or extra cues and start/end drift beyond `subtitle_timing_tolerance_ms` (default 500) are errors (warnings for a track marked `alignment: "independent"`), and a dropped `preserve_terms` entry, number or URL is a warning. Values it cannot parse are reported as `unknown`, and the checks that depend on them are skipped with warnings. `CONSTRAINTS` is a per-target, per-kind table in which every row carries a value (or `None` when official docs do not establish one), a source URL, a `verified_date` and a severity. `None` rows are never enforced and always produce a skip warning. Codec and frame-rate rows are errors for Instagram Reels and TikTok (documented codecs, 23–60 fps checked against the average) and warnings for YouTube's recommended encoding. They are `unknown` for Bluesky, Mastodon and LinkedIn's Videos API. Accessibility rules (alt text required for images, sidecar captions for videos), rights and provenance rules (`rights_confirmed` must be true, third-party media needs a license and attribution, tool output needs a tool name) and English-first locale and translation warnings are applied per asset. Paths must stay inside the manifest directory. `media-check MANIFEST [--json]` returns 2 on errors. The module creates no SQLite rows, makes no network calls, and performs no upload, render, transcode or generation. Media publishing remains unimplemented; see [MEDIA-ASSETS.md](MEDIA-ASSETS.md).

`reachweaver/storyboard.py` builds on it without changing its rules. `build_storyboard` reads one campaign file and returns a deterministic scaffold: timed scenes (hook, up to three evidence beats, CTA), SRT (and, for WebVTT-only targets, WebVTT) skeletons that `validate_subtitle_text` accepts, a schema-1 media manifest and per-platform image specs copied from `CONSTRAINTS`. Every creative field is a `{{write: ...}}` placeholder and `rights_confirmed` is `false`; the builder refuses to return a manifest that validates or that fails for any reason other than missing files, placeholders or unconfirmed rights. `check_safe_area` maps each target's UI-overlay zone (fractions of a 9:16 viewport) into frame coordinates for a `cover` (centered crop) or `contain` (letterbox) fit and flags boxes outside it: text is an error, a focal box a warning. Built-in `SAFE_ZONES` are `sourced` from first-party paid-ad specs (TikTok's In-Feed safe-zone template, Meta's Reels ad specs, the Google Ads vertical-video safe-zone image; accessed 2026-10-09) and carry `placement: "ads"`, the stated numbers and the evidence; each check warns that organic overlays may differ. `--safe-zones FILE` overrides them, and an entry counts as `owner_sourced` only with a public https source and a verification date; otherwise it stays `unverified` and every check that uses it adds a warning. `--check` re-reads an edited storyboard with zones from the built-in table or the override, never from the edited file. CLI `media-storyboard` is an early branch that returns before the `connect` block; it writes a NEW 0700 folder of 0600 files and makes no network call. `validate_media_manifest` now also rejects a subtitle file that still contains a placeholder.
## Lexical claim grounding (issue #10 slice)

`reachweaver/claims.py` is a pure, deterministic, offline checker that `validate_campaign` calls after its structural checks. The same checker runs alone as `claims-check CAMPAIGN --brand BRAND [--json]`. For each variant it extracts number-like tokens and risky phrases from the NFKC-normalized text, with URLs masked. It then matches them against the `claim` and optional `excerpt` text of the evidence IDs that variant cites. A token or phrase passes if a match is found. A finding records its severity, the original-text span and offsets, and the evidence IDs that matched.

- **Error** (blocks staging and export): unsupported numbers, percentages, versions, dates or multipliers; release language without cited `kind: "release"` or releases-URL evidence; a brand `banned_phrases` hit; a missing brand `required_disclosure`.
- **Review** (`needs_human_review`, shown as a warning): testimonial or quote, partnership or endorsement, customer-count and superlative language that cited evidence does not lexically contain, and feature claims (`category: "feature"`). `find_feature_claims` reads the object of a trigger such as `adds`, `now supports` or `integrates with` up to the clause end (at most 8 words), drops stopwords, generic words and numbers, and requires each remaining word, with case and simple plurals folded, in cited evidence. Negated and interrogative claims are skipped. `landing.py` skips grounded `feature` spans.
- **Warning**: near-duplicate variants (word 3-gram Jaccard ≥ 0.6 by default); a declared `cta_url` or `cta_phrase` missing from the text; with `--history-db`, `history_near_duplicate` (same account's local posts) and the `history_frequency` heuristic (`reachweaver/history.py`).

The contract extensions are optional and backwards compatible: evidence `excerpt` and `kind`, campaign or variant `cta_url`, variant `cta_phrase`, and brand `banned_phrases` and `required_disclosure`. **This is lexical grounding, not truth verification.** The checker never fetches sources and trusts the excerpt as typed. See [claim grounding](CLAIM-GROUNDING.md).
## Opt-in source ingestion and opportunity ranking (issue #9)

`reachweaver/sources.py` reads an explicitly opted-in source list (`sources.reachweaver.json`). Each source declares its `sensitivity`. The readers parse GitHub REST JSON (releases, tags, merged PRs, closed-as-completed issues with owner-selected labels), RSS 2.0/Atom feeds, website changelog pages (`html.parser`, `<article>` or `<h2>` sections), Keep a Changelog `CHANGELOG.md` files and Crossref DOI records, and normalize them into ranked `SourceSignal` dicts. Each signal has a stable sha256 ID, an RFC 3339 date, evidence URLs, a cleaned and truncated summary, confidence, uncertainties, exponential-decay freshness, novelty (from a local seen-state file and near-duplicate titles) and keyword relevance. The rank breakdown is transparent. The scan runs before `with connect`, so it never touches the SQLite outbox and cannot draft, approve, schedule or publish. Its only possible write is the owner-chosen `--state` file, written with `--mark-seen`.

The default is offline: the scan reads only local fixtures. `--online` makes GET requests through `publishers._send_http` (no redirects), with bounded reads and sanitized error codes. GitHub requests must first confirm that the repository is public. List endpoints follow RFC 8288 `rel="next"` links (read through the header-aware `_fetch_response` helper) only while they stay on `https://api.github.com`, up to the per-source `max_pages` (default 3, max 10). Non-API web fetches (feeds, changelogs) consult robots.txt once per host per run (`RobotsCache`) and skip the source as `robots_disallowed`. `--online` DOI sources GET `https://api.crossref.org/works/{doi}`, adding `mailto` only when the owner sets `crossref_mailto`. The optional `REACHWEAVER_GITHUB_TOKEN` is sent only to api.github.com and is never printed. Feeds are rejected before parsing if they contain DOCTYPE/ENTITY declarations or use an encoding that could hide them. Private sources, bot or trivial PRs, drafts and prereleases (unless opted in) are excluded. Instruction-like source text stays inert data: it only lowers confidence. See [SOURCES.md](SOURCES.md).

`reachweaver/briefs.py` (`signal-brief`) is the hand-off from a signal to the campaign contract. It reads a saved `sources-scan --json` file as untrusted input and normalizes one signal again: `clean_text`, a fresh `INSTRUCTION_LIKE` check, URL checks through `campaigns.public_url` plus a Markdown-safe character set, and scores only in 0..1. It then builds a deterministic scaffold. Evidence records carry the signal URLs with a mapped `kind`, the summary as an `excerpt` marked untrusted, and `checked_by: "unverified"`, with no `verification` or `checked_at`. Each account in both the accounts config and the brand's `approved_accounts` whose platform has a live or manual path (`preview.capability` over `platform_registry.json`, or `manual_export.MANUAL_PLATFORMS`) gets one variant whose `text`, `angle` and `cta` are `{{write: ...}}` placeholders. `audience` and each `claim` are placeholders too. `campaigns.validate_campaign` rejects a placeholder in those fields, and `validate_post` rejects one in post text, so the scaffold cannot be staged. `build_brief` runs `validate_campaign` itself and refuses to write a scaffold that would pass. The sidecar `.brief.md` shows source text only inside a fenced data block whose fence is longer than any backtick run in that text. Both files are written with `privacy.write_private_file` (new, `0600`, `O_EXCL`/`O_NOFOLLOW`). The command runs before `with connect`, so it opens no database, and it uses no network and no model. No copy is generated, and nothing is staged, approved, scheduled or published.
`reachweaver/private_sources.py` (issue #9, owner-approved 2026-10-09) holds the exfiltration guards for private GitHub sources. A private repository is read only with all three opt-ins: `visibility: private` and `sensitivity: private` on the source, plus `sources-scan --allow-private`. Without them, `sources.scan_sources` skips the source before any read, or refuses it after the metadata request (`repository_not_confirmed_public`). Private signals carry `private: true` and `publishable: false`. `briefs.build_brief` copies that to evidence as `source_visibility: "private"` and scaffolds no `cta_url`. `campaigns.validate_campaign` refuses such evidence until a human sets `publishable: true`, and it refuses any link into the private repository. `cli._private_link_refusal` applies the same rule to `draft`, `media-attach`, `youtube-attach`, `check` and `approve`, but only when `--sources CONFIG` is passed; without it those commands do not know the private repositories and cannot detect such a link. `attribution.build_utm_link(private_repos=...)`, `preview.utm_rewrite` and `utm-link --sources` refuse to tag those URLs. `privacy.export_data` and `campaigns.export_campaign` redact private evidence excerpts and URLs. `token_scope_report` classifies a classic token's `X-OAuth-Scopes` header by scope name. A missing header (fine-grained tokens) means "scope unknown". The module imports only `policy` and touches no network, credential, approval or publishing path.

## Fail-closed platform capability registry (issue #6)

`reachweaver/platform_registry.json` is a versioned, offline research snapshot with one entry for every platform in `policy.PLATFORMS`. Each entry records its official docs, account types, auth method and scopes, approval requirements, format statuses (`api_direct`/`api_gated`/`manual_export`/`unavailable`/`unknown`), metrics, edit/delete, rate limits with a source, fees, compliance constraints, `verified_date` and HTTPS sources. `reachweaver/capabilities.py` validates completeness, enums, ISO dates and provenance. It also checks that the `live_text` adapters exactly match `policy.SUPPORTED_LIVE` and that Reddit keeps its moderator-only, human-only constraint. `require_capability(platform, fmt, action)` raises `ValidationError` for any unknown platform, format or action, and for any status other than `api_direct`; `publish` also needs a text `live_text` adapter, or an image format listed in the entry's optional `live_media_formats` (none is). Video goes through `require_video_mode` and replies through `require_reply_capability` (`reachweaver_reply_adapter`), both off in the bundled registry. `publishers.publish()` and the `publish --live` loop call it **before** identity preflight, the durable claim or any network request. This is a strengthening guard only: approval, dry-run, emergency stop, credential isolation and ambiguous-send reconciliation are unchanged. The offline `capabilities` CLI lists the registry, exits nonzero if it is invalid, and warns on entries older than `--max-age-days` (default 120). The skill bundle carries a byte-identical copy. The registry never grants permission. See [PLATFORMS.md](PLATFORMS.md) for the table, the X/Reddit/Pinterest go/no-go decisions (#24) and the update process.
## Preregistered growth experiments (issue #16 slice)

`reachweaver/growth.py` is a pure, offline guard layer around the matched-age readout in `experiments.py`, and `reachweaver/growth_readout.py` connects it to the database read path and adds simulation. `growth.py` has four parts:

- `check_variant_set` accepts 2–3 arms with exactly one control. Every arm declares one `differs_in` dimension (`hook`, `framing`, `format`, `cta` or `timing`) that equals the plan dimension. Declared labels and the CTA must match the control in every other dimension. Links in the text (host + path, query ignored) must match unless the CTA is under test. Arms that are near-duplicates under `claims.jaccard` are rejected.
- `preregister` validates the plan. The hypothesis must not overclaim. The cohort is one account and one platform. The primary metric comes from a qualified-signal allowlist, and vanity metrics are allowed only as guardrails. The plan also needs an observation window, `minimum_pairs` (with a warning below the 6-pair sign-test heuristic), a stopping rule that cannot stop early on a win, and notes acknowledging six named confounders. It returns a record with a sha256 `plan_hash` over canonical JSON and a self-reported `registered_at`. `write_preregistration` creates a new 0600 file with `O_EXCL`/`O_NOFOLLOW` and never overwrites. `verify_preregistration` recomputes the hash and compares it in constant time.
- `classify_result` maps `evaluate_experiment` output to `insufficient_data`, `inconclusive` or `directional`. A `directional` label requires the preregistered minimum and at least 6 pairs, a strict-majority direction with a descriptive sign-test p of at most 0.05 (`MAX_DIRECTIONAL_SIGN_P`), the minimum useful lift and no guardrail breach. It flags first-pair novelty and single-outlier dominance. No `proven` label exists. A plan may declare `assignment` (`randomized`, `alternating` or `unrandomized`), which is covered by the hash.
- `next_hypotheses` keeps negative results and suggests the next dimension using fixed, ordered rules: finish pending, replicate a single positive, take the next untested dimension, then the least tested. It is not a learned recommender.

`growth_readout.py` adds four pieces:

- `growth_plan_to_experiment(record, post_map, con)` refuses a record whose hash does not verify. It also refuses a post that is unknown, unpublished or outside the cohort's account and platform. Otherwise it copies metric, denominator, window, tolerance, thresholds and minimum pairs into an `experiment-evaluate` plan that carries the `plan_hash`. It only `SELECT`s.
- `describe_design` returns the readout's `design` block. Only an owner-declared `randomized` assignment is more than `associational`, and it is never `verified`.
- `run_readout` runs verify → convert → `evaluate_experiment` → `classify_result`.
- `simulate_readout` uses `random.Random(seed)` to build in-memory synthetic pairs and reports the label distribution with Wilson intervals. It never touches the database.

`experiment-preregister PLAN --out FILE`, `experiment-verify PLAN --hash H`, `experiment-next --results FILE` and `experiment-simulate ...` are early offline CLI branches: they return before the SQLite outbox is opened. `experiment-readout --prereg FILE --posts MAP` runs inside the `connect` block and only reads. Neither module imports publisher, connection or network code. Registration has `status: registered_not_approved`. Publishing every arm still goes through per-item approval, the dry-run default, per-account caps, the emergency stop and ambiguous-send reconciliation, none of which change. The hash proves the plan is unchanged, not when it was written. See [GROWTH-EXPERIMENTS.md](GROWTH-EXPERIMENTS.md).

## Optional Postiz publishing backend (issue #30 slice)

`reachweaver/postiz.py` adds one opt-in **backend**: an account entry with `"backend": "postiz"` keeps its real `platform` and adds `postiz_base_url` (public HTTPS origin only, via `policy.require_https_public_server`), optional `postiz_api_prefix` (`/public/v1` cloud or `/api/public/v1` self-hosted), `postiz_integration_id` and `handle`. The only credential is `{env_prefix}_POSTIZ_API_KEY`, read at request time. Wiring is additive: `accounts.load_accounts` delegates validation for backend entries, `missing_credentials` returns the backend key name, `publishers.publish()` dispatches backend accounts to `postiz.publish`, `connections.check_connection` refuses them, `doctor` prints `backend=postiz`, and the `publish --live` loop hands a backend post to `postiz.run_live_send`. Accounts without `backend` follow the unchanged native path.

`run_live_send` keeps every existing gate: `--live` plus `REACHWEAVER_ALLOW_LIVE=1` and the production marker (checked by the CLI before any account is loaded, issue #5), an approved and due post, the emergency stop (checked before any request and again inside `claim_sending`), text lint and the rolling budget. Before the durable claim it runs the opt-out gate (issue #26; an @-mention of a listed handle or domain is `BLOCKED`), a fail-closed backend capability gate (a Postiz provider mapping, registry text `api_direct`/`api_gated`, platform status `supported`/`requires-review`; Reddit always refused, X refused until #24) and a read-only `GET /integrations` identity preflight (configured id exists, provider maps to the platform, `profile` equals `handle`, not disabled). Preflight failure prints `BLOCKED` and leaves the post `approved`. After the claim it sends one `POST /posts` with `type: now`, one integration, the exact approved text and `shortLink: false`. Postiz `draft`/`schedule` and Postiz approvals are never used, so `pause` stays authoritative.

**A Postiz acceptance is not a platform receipt.** A well-formed `[{postId, integration}]` response raises `postiz.BackendAccepted` (a `PublishError`). The CLI records `needs_review`, `remote_id = postiz:<postId>`, `remote_url = NULL` and `last_error = postiz_accepted_platform_receipt_unverified`, plus a `needs_review` event with the same detail. Every other post-claim outcome (HTTP 4xx/5xx, timeout, malformed or mismatched response, unexpected exception) is `needs_review` with a sanitized `postiz:` code, and nothing is retried. The storage schema and state machine are unchanged: `sending -> needs_review` already exists, and only a human `reconcile` with the native id/URL can reach `published`. `postiz-check --account ID --online [--post-id ID]` is read-only: it lists integrations (id, identifier, profile, disabled) and, optionally, looks up the job's backend-reported `state` and `releaseURL` (public HTTPS only) via `GET /posts`; it never reconciles. Requests go through `publishers._send_http` (no redirects, 25 s timeout, 1 MB cap). Errors carry status codes only, never the key or a response body. Verified API facts, unknowns, AGPL-3.0 licensing and the MCP decision are in [POSTIZ.md](POSTIZ.md). No Postiz instance or account has been connected or live-tested.

## Bluesky and Mastodon adapter hardening (issues #12, #13 slice)

`reachweaver/richtext.py` is pure and offline. It holds the conservative grapheme segmenter (a stdlib `unicodedata` approximation of UAX #29 that never under-counts), Bluesky link and hashtag facets at UTF-8 byte offsets, plain-text mention detection, the deterministic TID record key and the Mastodon URL-weighted length. `policy.validate_post(platform, content, account=None)` uses these for Bluesky's real limits (300 graphemes, 3000 UTF-8 bytes). For Mastodon it uses `min(450, account max_characters)`. Draft creation, campaign staging, `check`, the `publish --live` preflight and `publishers.publish()` all pass the account, so a pinned instance limit can only tighten the check.

Bluesky requests go to `accounts.bluesky_service(account)`: the default `https://bsky.social` or an owner-set `service`, re-validated by `require_https_public_server` before any credential is read. Session `didDoc` endpoints are never followed. `createRecord` sends a TID-syntax `rkey` derived from the post id and the draft `created_at`, so a re-send of the same post should collide on the PDS rather than duplicate. That expectation comes from the AT Protocol repo semantics and has not been observed live. Mention facets are never built, because they need DID resolution and ReachWeaver performs none. Mastodon sends a minimal form body: `status`, `visibility` (`public`, `unlisted` or `private`; `direct` is rejected at config load and again in the adapter), plus optional owner-pinned `language` and `spoiler_text`. It keeps the `Idempotency-Key`, which Mastodon honours for one hour only.

Receipts are checked after the request and every mismatch is uncertain. A Bluesky `uri` must name the session DID, the `app.bsky.feed.post` collection and the requested rkey, and `cid` must be present. A Mastodon receipt needs `id` and an HTTPS `url`/`uri`, and any returned `account.username` and `visibility` must match. Every such error is raised as a `PublishError` after the durable claim, so the CLI moves the post to `needs_review` and never retries. Adapters return `publishers.Receipt`, a two-tuple subclass whose sanitized `extra` fields (Bluesky `cid`, Mastodon `visibility`) `storage.finish_send` appends to the `published` audit event. No schema migration is needed.

`connect-check --online` for Mastodon makes one more call, an unauthenticated `GET /api/v2/instance` (`connections.mastodon_instance_limits`). It reports status and media limits and exits 2 when the effective text limit would exceed the instance's. It never writes config. `show` adds an offline `platform_preview` with the exact destination details: service, handle and record key for Bluesky; server, handle and visibility for Mastodon. Approval, dry-run, emergency stop, credential isolation and ambiguous-send reconciliation are unchanged. See the adapter details in [PLATFORMS.md](PLATFORMS.md).

## Manual export for review-gated platforms (issues #19-#23 slice)

`reachweaver/manual_export.py` is the **offline creator-draft fallback** for Threads, Instagram, Facebook Pages, YouTube and TikTok. All five need app review or an API audit that the owner has not obtained. Each now has a mock-tested adapter (Threads #19, Instagram #20, Facebook Pages #21, YouTube #22, TikTok #23; owner-approved 2026-10-09, disabled until a live receipt), and the capability registry keeps every one unreachable, so manual export stays the publishing path. `platform_preflight(platform, variant, account_meta=None)` returns blocking errors and warnings from dated, sourced official limits (`FACTS`). These include Threads' 500 characters and 5 links; Instagram's 2,200-character, 30-hashtag and 20-@-tag caption; YouTube's 100-character title, 5,000-byte description and `<`/`>` ban; and TikTok's 2,200 UTF-16-unit caption. Preflight also enforces the required media format and rejects any Facebook target that is not a Page (profiles and groups are refused). Facts that the official pages did not establish are `unknown`; facts on which they disagree are `conflicting` (the YouTube unverified-project private lock). Neither kind is ever relied on. `build_export` validates an optional media manifest through `media.validate_media_manifest` instead of duplicating media limits. It records the registry status and the refusal from `require_capability(..., "publish")`. It lists `human_decisions` (TikTok privacy, interactions, commercial and AI labels; YouTube privacy, made-for-kids and synthetic-content disclosure) with `selected: null`, and ignores any variant value that tries to pre-select them. The `status` is always `manual_export_not_published`, and the wording never claims that a post exists. `write_export` and CLI `manual-export` write only to a **new** 0700 directory of 0600 files (`O_EXCL`/`O_NOFOLLOW`), and write nothing when preflight blocks. The module imports no publisher, connection or network code (`SEND_PATH = None`); it creates no SQLite rows and changes no approval, dry-run, emergency-stop, credential or reconciliation rule. See [MANUAL-EXPORT.md](MANUAL-EXPORT.md).
## LinkedIn member text publisher (issue #14)

`reachweaver/linkedin.py` holds all LinkedIn logic; `publishers.py` only gains `publish_linkedin` (one `PUBLISHERS` entry) and a backward-compatible `request_json_with_headers` helper that returns `(status, lower-cased headers, JSON or {})` with the same 25 s timeout, no-redirect opener, 1 MB body bound and sanitized `http_<code>` / `network_or_timeout_uncertain` errors as `request_json`. It exists because the Posts API returns the post URN only in the `x-restli-id` header, often with an empty body.

```text
accounts.json (person_id, linkedin_version, optional authorization_expires_at, scope_check)   env: {prefix}_ACCESS_TOKEN
                                                     (+ _CLIENT_ID, _CLIENT_SECRET only with scope_check)
        |                                                                         |
        v                                                                         v
load_accounts: member id pattern, YYYYMM format, ISO expiry, organization fields refused
        |
publish --live (flag + REACHWEAVER_ALLOW_LIVE=1 + REACHWEAVER_ENV=prod, approved + due, not paused)
        |
check_connection -> linkedin.verify_identity
        |   offline: organization author? version >= 12 months? token expired?  -> BLOCKED, post stays approved
        |   GET https://api.linkedin.com/v2/userinfo  -> sub == person_id (exact)   else BLOCKED
        |   only with "scope_check": "introspect" (opt-in): linkedin.verify_write_scope
        |     POST https://www.linkedin.com/oauth/v2/introspectToken (form: client_id, client_secret, token)
        |     active + 3L + own client_id + w_member_social + expires_at > now + 1 h   else BLOCKED
        |     (<= 1 request per account per run, <= 3 incl. retries; result in memory only, 15 min)
        v
claim_sending (BEGIN IMMEDIATE, rolling budgets)
        |
POST https://api.linkedin.com/rest/posts  (Linkedin-Version, X-Restli-Protocol-Version: 2.0.0)
        |   body: author urn:li:person:{id}, escaped little-text commentary, PUBLIC, MAIN_FEED, PUBLISHED
        v
201 + x-restli-id urn:li:share|ugcPost -> published (remote_id = URN, remote_url = /feed/update/{urn}/)
anything else (non-201, missing/odd header, HTTP/network error) -> needs_review, never retried
```

Invariants:

- **Member only.** The author is always `urn:li:person:{person_id}`. `urn:li:organization` values and any organization/page/author configuration field are refused at config load and again in the preflight. `w_member_social` never implies `w_organization_social`.
- **Version lifecycle.** LinkedIn supports each monthly version for a minimum of one year. `version_status` warns at 10 months and returns an error from month 12; a version in the future, malformed or older than the first versioned release (202206) is also an error. The live path fails closed on error. `doctor` reports the status offline.
- **Token lifecycle.** The optional, non-secret `authorization_expires_at` field records when the owner's OAuth grant expires. Its value must parse as an ISO-8601 timestamp with an offset and it is accepted only on LinkedIn accounts. The unchanged `SECRET_FIELD` rule still rejects every key containing `token`, `secret`, `password` and so on, including `token_expires_at`. Expired tokens are refused before any request; tokens within 7 days of expiry produce a `doctor` warning. A missing value is reported as unknown.
- **Literal text.** Every little-format reserved character (`| { } @ [ ] ( ) < > # \ * _ ~`) is backslash-escaped, so approved text cannot create mentions. A `#` at a word start followed by a letter or digit is kept as a documented hashtag element.
- **Credential isolation.** The bearer token is read through `accounts.credential` (environment or `$CREDENTIALS_DIRECTORY`) at call time and never appears in error strings, which are fixed codes plus fixed guidance (401/403/426/429 hints) only.
- **Ambiguity.** The Posts API documents no idempotency key. Every post-claim failure ends in `_uncertain`, which the CLI records as `needs_review` for human reconciliation.

- **Opt-in write-scope check (owner-approved 2026-10-09).** Only an account with `"scope_check": "introspect"` reads `{prefix}_CLIENT_ID`/`{prefix}_CLIENT_SECRET` (both in `accounts.CREDENTIAL_SUFFIXES`, so the `env_prefix` overlap rule always covers them; `accounts.account_credential_suffixes` lists them as required only after the opt-in). Introspection runs inside `check_connection`, before the claim, and fails closed on any refusal or HTTP error. `publish_member_post` re-checks the in-memory result offline before `mark_write_started`. See SECURITY-REVIEW-PROPOSAL-14-linkedin-scope.md.

Known gaps: no OAuth wizard or refresh flow (LinkedIn currently issues 60-day access tokens), no default `w_member_social` scope probe before sending (userinfo proves identity only; the introspection check is opt-in), no media/article/document posts, no edit/delete, no analytics. The public post URL format is the one LinkedIn documents for `ugcPost` URNs and is assumed for `share` URNs; the URN is the receipt.


## Instagram single-image adapter (issue #20, owner-approved 2026-10-09; disabled until a live receipt)

Two new modules. `reachweaver/remote_media.py` handles owner-hosted image URLs: URL rules (HTTPS, public DNS host via `require_https_public_server`, port 443, printable ASCII, no userinfo, query or fragment), a read-only download (DNS answers must all be global addresses, no credentials or cookies, no redirects, at most 8,000,000 bytes, through `publishers._open`), JPEG header checks (`media._parse_jpeg`, aspect ratio 4:5 to 1.91:1) and the `source: "url"` record of the shared attachment model (`source, url, sha256, mime, bytes, width, height, alt, focus`; codec in `attachments.py`). `reachweaver/instagram.py` holds the Graph API logic. `publishers.py` gains `publish_instagram`, `MEDIA_ONLY_PLATFORMS` and `media=`/`paused=` arguments on `publish()`; `connections.py` gains `_instagram`. Both sit behind the unchanged `SUPPORTED_LIVE` gate and `require_capability("instagram", "image", "publish")`.

Approval binding: the nullable `posts.media_json` column (migration) and, for a post with an attachment, the image payload (`reachweaver.storage.approval/media-1`) that adds `media: [{sha256, mime, alt, focus, url}]`. Text-only payloads are byte-identical to `approval/1`. Column, tag, codec and refusal code `approval_media_changed_reapproval_required` are shared with #13 as one attachment model (owner decision D20-7, 2026-10-09): `attachments.MEDIA_PLATFORMS` fixes the source each platform accepts (Mastodon `local`, Instagram `url`), `check_media_list` dispatches to the platform's own limits, and `bound_view` adds `url` only for hosted images.

Registry: `capabilities.ADAPTERS` gains `live_media` (a media-only platform, whose text format is `unavailable`); the optional, non-empty `live_media_formats` list is limited to `LIVE_MEDIA_FORMATS` (`mastodon: image`, `instagram: image`) and needs a live adapter and `api_direct` formats. The bundled registry uses neither, so Mastodon images and Instagram stay refused.

```text
publish --live: live gate -> pause -> account -> SUPPORTED_LIVE -> opt-out (caption + alt) -> approval hash
        -> cooldown -> image capability -> media record rules -> GET image URL, sha256 == approved
           (changed: BLOCKED approval_media_changed_reapproval_required, post stays approved)
        -> credentials -> GET /me (user_id, username, account_type) -> GET content_publishing_limit
claim_sending -> adapter: GET image URL again, sha256 == approved (else C1, no write)
mark_write_started()
POST /{ig}/media {image_url, caption, alt_text} (Bearer)      -> container id
GET /{container}?fields=status_code  (<= 5 checks, 60 s apart; pause re-read before each wait)
POST /{ig}/media_publish {creation_id}                         -> media id = remote_id
GET /{media}?fields=id,permalink,username,media_type           -> remote_url if the shape is recognized
any failure after mark_write_started() -> *_uncertain -> needs_review, never retried
```

Meta fetches the image URL itself after the container POST, so the last ReachWeaver hash check cannot prove which bytes Meta received (proposal, residual risk). See [INSTAGRAM.md](INSTAGRAM.md).

## TikTok video delivery (issue #23, owner-approved 2026-10-09; disabled until a live receipt)

`reachweaver/tiktok.py` holds all TikTok logic: account validation, fail-closed MP4 checks on `media.probe_bytes` output, the TikTok `platform_options` of the shared video record (`reachweaver/video.py`, also used by YouTube), identity (`GET /v2/user/info/`), the `creator_info` query, init, chunked `PUT` (`publishers.request_put_bytes`), status polling and `run_live_send`. Two registry switches gate it, `reachweaver_video_inbox` and `reachweaver_video_direct_post`, both validated by `capabilities.validate_registry` and enforced by `require_video_mode()`. The inbox switch must agree with `policy.SUPPORTED_LIVE_VIDEO`, which is empty, so neither switch can be on today. The CLI hands a post to `run_live_send` only when its platform is in that set. `connections.check_connection` and `publishers.publish` keep refusing TikTok.

```text
video-attach (offline, drafts only) -> posts.video_json = shared video record (video.py: platform, sha256, bytes,
                                       mime, parsed headers, platform_options {mode, privacy_level, ...})
approve -> approval/video-1 hash = /1 fields + the record without its path (text-only posts keep /1)
publish --live -> require_live_gate, pause (CLI) -> run_live_send:
   require_video_mode -> opt-out -> check_approval_binding (keyed, #7 D7) -> D6 confirmation -> cooldown (+#11 scopes)
   -> re-read file (O_NOFOLLOW, <= 256 MiB), sha256 == record, else approval_binding_failed (BLOCKED)
   -> GET user info (+ creator_info for direct_post)        before the claim, bounded read-only retries
   -> claim_sending(expected_sha256)                         pause, binding, budgets under lock
   -> init (publish_id, upload_url; no content) -> mark_write_started() -> PUT chunks (206..201)
   -> status polls: inbox SEND_TO_USER_INBOX -> needs_review tiktok_creator_inbox_delivered_not_public
                    direct PUBLISH_COMPLETE + public id -> published; anything else -> needs_review
```

An inbox upload is never `published`. It stays in `needs_review` (the Postiz acceptance pattern, with no schema change to the state machine) until a human reconciles it with the native video. So `report`, metrics and attribution cannot count a draft that nobody can see. See [TIKTOK.md](TIKTOK.md) and the proposal.


## YouTube video upload (issue #22, owner-approved 2026-10-09; disabled until a live receipt)

`reachweaver/youtube.py` holds all YouTube logic. `publishers.publish_video` (separate from the text `publish`), `connections._youtube` and the CLI sit behind two gates that are both closed in the bundled build: `policy.SUPPORTED_LIVE_VIDEO` is empty, and the YouTube registry switch `reachweaver_video_upload` is `off` (the validator allows it only on YouTube, needs `video: api_direct` and sourced `rate_limits.facts` for `live`, and requires the live set to equal `SUPPORTED_LIVE_VIDEO`). `capabilities.require_video_mode("youtube", "upload")` is the gate; `require_capability(p, "video", "publish")` never enables video. The draft's `live_video` adapter value was folded into this switch at integration (2026-10-09). A draft gains a video through `youtube-attach` (nullable `posts.video_json`, added by `migrate()`), stored as the shared video record (`video.py`); the approval hash then binds `reachweaver.storage.approval/video-1`: the text fields plus the record without its path (file SHA-256, bytes, MIME, parsed headers and `platform_options` `{title, tags, category_id, privacy_status, self_declared_made_for_kids}`; the description is `content`). Posts without a video keep the `approval/1` payload byte for byte.

```text
publish --live: live gate -> pause -> account -> post_video (malformed -> BLOCKED)
        |  SUPPORTED_LIVE_VIDEO + video spec? else SKIPPED manual_only
        |  opt-out over content + title + tags -> approval hash -> file re-hash (changed -> BLOCKED, event)
        |  D6 confirmation (#7, opt-in) -> cooldown (+#11 scopes) -> require_video_mode(youtube, upload)
        |  -> credentials -> validate_post
check_connection -> GET /youtube/v3/channels?part=id&mine=true   ids == [channel_id] else BLOCKED
claim_sending (re-checks pause + video-1 hash, keyed tag when on, under lock)
youtube.upload_video: re-open (O_NOFOLLOW) + re-hash pass 1 -> mismatch: refuse, nothing sent
        POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status -> 200 + Location
        Location must stay on https://www.googleapis.com/upload/youtube/v3/videos (never stored)
        mark_write_started()
        PUT chunks (8 MiB, Content-Range) while hashing; 308 Range must equal the chunk end
        pause re-read between chunks; digest compared BEFORE the final chunk
        201 video resource: id, snippet.channelId, status.privacyStatus, uploadStatus checked
any failure after mark_write_started() -> *_uncertain -> needs_review; never resumed or resent
```

See [YOUTUBE.md](YOUTUBE.md) and the proposal.

## Threads text adapter (issue #19, owner-approved 2026-10-09; disabled until a live receipt)

`reachweaver/threads.py` holds all Threads logic; `publishers.py` gains `publish_threads` and `connections.py` gains `_threads`, both behind the unchanged `SUPPORTED_LIVE` and `require_capability(..., "publish")` gates. The bundled registry keeps Threads at `reachweaver_adapter: "none"` and text `api_gated`, so neither entry can be reached. `policy.validate_post` adds the official 500-character (emoji as UTF-8 bytes) and 5-link limits for Threads, which only tightens the check. `accounts.CREDENTIAL_SUFFIXES["threads"]` reserves `{env_prefix}_ACCESS_TOKEN` for the overlap rule and `credentials-audit`.

```text
check_connection -> threads.verify_identity          (before claim_sending)
        |   offline: id/handle shape, unknown keys, authorization_expires_at past -> BLOCKED
        |   GET /me?fields=id,username            id == threads_user_id, username == handle
        |   GET /{id}/threads_publishing_limit    quota_usage < quota_total, else BLOCKED
        v
claim_sending -> mark_write_started()
POST /{id}/threads (media_type=TEXT, text)    -> container id
sleep 30 s; GET /{container}?fields=status    -> FINISHED (<= 5 checks, 60 s apart)
        |   emergency pause re-read before each wait, after it and before publishing (D19-2)
POST /{id}/threads_publish (creation_id)      -> media id = remote_id
GET /{media}?fields=id,permalink,username,owner,text -> remote_url if it names the handle
any failure after mark_write_started() -> *_uncertain -> needs_review, never retried
```

The C1/C3 boundary sits before the container POST (conservative; proposal). See [THREADS.md](THREADS.md).

## Facebook Page adapter (issue #21, owner-approved 2026-10-09; disabled until a live receipt)

`reachweaver/facebook.py` is registered in `PUBLISHERS` and `connections._CHECKS`, but it is refused before either lookup: `facebook` is not in `SUPPORTED_LIVE`, and the registry adapter is `none`.

```text
check_connection -> facebook.verify_identity            (before claim_sending)
        |   offline: page_id/page_name shape, unknown keys, Graph v25.0 past 2028-07-29 -> BLOCKED
        |   GET /v25.0/me?fields=id,name          id == page_id (a User token fails here)
        |   GET /v25.0/{page_id}?fields=id,name   id == page_id, name == page_name
        v
claim_sending -> mark_write_started()
POST /v25.0/{page_id}/feed  message (+ link = first https URL of the approved text)
id must be {page_id}_{digits}                  -> remote_id
GET /v25.0/{post_id}?fields=id,permalink_url,from,message -> remote_url if www.facebook.com
any failure after mark_write_started() -> facebook_feed_*_uncertain -> needs_review, never retried
```

The token is sent only in `Authorization: Bearer`. See [FACEBOOK.md](FACEBOOK.md) and the proposal.

## Field pilot protocol and operations-log readout (issue #27 slice)

`reachweaver/pilot.py` is a **pure, offline** reader for the owner-maintained CSV operations log of a field pilot. The log has one row per unit of work with `date`, `arm` (`manual`, `scheduler` or `reachweaver`), `task` and nine measures: `minutes_spent`, `posts_published`, `duplicates`, `unrecoverable_errors`, `interventions`, `factual_errors_found`, `clicks`, `impressions` and `downstream_conversions`. `load_pilot_log` rejects a file that lacks a column. It reports, and excludes, any row with a bad date, an unknown arm, a negative or non-numeric value, a blank task, too many or too few cells, or more duplicates than published posts; it never repairs a row. A blank cell is `None` (unknown), never zero. `pilot_report` returns per-arm totals with `known_rows`/`unknown_rows`/`coverage`, seven rates computed only over rows where both numerator and denominator are known (each with its numerator, denominator and row counts; a zero denominator gives no rate), distinct ISO weeks and minutes by task. Its arm comparison is labelled `descriptive_not_causal` and is **refused** unless at least two arms are logged and every logged arm covers `min_weeks` ISO weeks (default 2). Differences are reported against the `manual` arm only. There is no winner, lift or significance field.

CLI `pilot-report --log FILE.csv [--min-weeks N] [--json]` is an early offline branch: it returns before the SQLite outbox is opened, and exits 1 when malformed rows were reported. The module imports only `policy.ValidationError` and the standard library: no storage, publisher, connection or network code. It changes no approval, dry-run, emergency-stop, credential-isolation or ambiguous-send rule. The preregistered protocol (consent, stop conditions, baseline, counterbalanced arm blocks, outcome windows, metric definitions, threats to validity and decision criteria) is in [FIELD-PILOT-PROTOCOL.md](FIELD-PILOT-PROTOCOL.md), and the gated release-to-campaign command sequence is in [RELEASE-PLAYBOOK.md](RELEASE-PLAYBOOK.md). No pilot has been run, so neither document contains results.

## Onboarding audit and versioned release gate (issue #27 slice)

`reachweaver/onboarding.py` is a **pure, offline** audit over the two research registries. `blocker_audit()` gives every capability-registry platform without a live text adapter exactly one reviewed alternative from `CAPABILITY_ALTERNATIVES`: `manual_export` (only for `manual_export.MANUAL_PLATFORMS`), `draft_export` (`campaign-export`, only for `policy.PLATFORMS` ids) or `human_only`. Every distribution-registry channel without a mock-tested adapter is classified from its own fields: `not_applicable` becomes `unsupported`, a `human_only` channel becomes `human_only`, and other manual channels become `manual_export`/`draft_export` when they map to a ReachWeaver platform id, otherwise `native_manual` (no export exists). It returns errors when a blocked platform has no entry, an entry is stale or names a path that cannot work, a human-only venue would receive generated drafts, the two registries disagree, or Reddit or Hacker News stops being `human_only`. `render_markdown()` produces the table embedded between markers in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives). The module imports only the registry loaders, `manual_export.MANUAL_PLATFORMS` and `policy`; it has no CLI subcommand, opens no database and has no send path (`SEND_PATH = None`).

`scripts/release_gate.py` (`make release-gate [TAG=vX.Y.Z]`) is the offline release gate. It checks that `pyproject.toml`, `reachweaver/__init__.py` and the bundled skill copy carry one `X.Y.Z` version (and that `--tag` matches it), that `CHANGELOG.md` has a non-empty section for that version (dated before tagging), that required documents exist, that README links the onboarding documents and names every `sub.add_parser` subcommand of `cli.py`, that relative Markdown links resolve, that the skill bundle is byte-identical, that the blocker audit is clean and its table current, that the release-readiness scorecard [`docs/release-readiness.json`](release-readiness.json) is valid, and finally that `python3 -m unittest discover -s tests` exits 0 with `OK`. Tests run only when every other check passed. `--skip-tests` makes the best possible result `incomplete` (exit 3), never a pass. The gate uses no network, credential, version-control command or outbox. On a pushed `vX.Y.Z` tag, `.github/workflows/release.yml` runs it with `--tag` and the tests in a read-only `verify` job, together with CI's hygiene checks and `scripts/release_ref_guard.py` (owner's tag at the current `origin/main` commit, from `GITHUB_*` variables and local `git rev-parse` only); a separate owner-only `publish` job, the only one with `contents: write`, re-runs the guard and creates the GitHub Release ([maintainer release checks](RELEASE-PLAYBOOK.md#maintainer-release-checks)). `tests/test_onboarding.py` copies the repository (tracked and untracked non-ignored files), runs every `python3 -m reachweaver` line of [QUICKSTART.md](QUICKSTART.md) in a fresh interpreter with a minimal environment, a throwaway `HOME` and patched sockets, and executes each `manual_export`/`draft_export` alternative. Approval, dry-run, emergency-stop, credential-isolation and ambiguous-send rules are unchanged.

The release-readiness scorecard `docs/release-readiness.json` has one row per platform (every capability-registry id, plus Hacker News from the distribution registry), per backend (Postiz) and per product area (sources, content, scheduler, analytics, engagement, media, security, privacy/ops). Each row carries a status from `not_started`, `offline_mock_tested`, `manual_export_only`, `live_receipt_verified` or `blocked`, its issue number (or `null` when no issue owns it), evidence as repository paths or `tests/file.py::ClassName` references, and blockers of kind `owner`, `live` or `policy`. `python3 scripts/release_gate.py --scorecard [--json]` validates and prints it (exit 0 valid, 1 invalid), and the gate's `readiness` check runs the same validation. The validator rejects unknown statuses or fields, evidence paths that do not exist or escape the repository, test names the file does not define, a capability-registry platform with no row, a missing required area, and statuses the registries contradict: `offline_mock_tested` needs a `live_text` adapter, `manual_export_only` needs a `manual_export`/`draft_export` alternative in `onboarding.CAPABILITY_ALTERNATIVES`, and a human-only distribution channel must stay `blocked`. Every status short of `live_receipt_verified` must name at least one blocker. `live_receipt_verified` is accepted only with a `receipt` field naming a checked-in JSON file with `area`, an https `remote_url`, a time-zoned `observed_at`, `source` and `first_party: true`. No such receipt exists, so no row claims that status, and a test asserts it.

## Campaign channel previews (issue #10 slice)

`reachweaver/preview.py` builds a deterministic, offline review report for every variant of a campaign. `build_preview(campaign, brand, accounts=None, *, utm=False)` reads its inputs and never mutates or writes them. It imports no publisher, connection, storage or network code (`SEND_PATH = None`), and it generates no copy.

```text
campaign.json + brand.json (+ optional public accounts.json)
        |
validate_campaign(..., grounding=False)   structural errors/warnings, split per variant / campaign
check_claims(campaign, brand)             lexical findings + grounded[] (per-account brand rules merged)
load_registry()                           format status: text, thread->text, carousel_outline->carousel
        |
per variant: pieces[] (text | thread parts | caption + slides)
        measure()        Bluesky graphemes + UTF-8 bytes; Mastodon 23-char URLs vs account max_characters; conservative cap
        text_features()  links, hashtags, mentions; Bluesky facets at byte offsets
        utm_rewrite()    optional build_utm_link(..., content=variant id), side by side
        first_part_check / carousel status   review item / registry warning
        compute_confidence()  high | medium | low + rationale (+ editor rationale)
        |
render_text | render_markdown | JSON   (CLI campaign-preview; exit 2 when any variant is low)
```

Contract changes are optional and backwards compatible:

- `claims.variant_body` defines the checked body for `thread` parts and for a `carousel_outline` caption plus slides, so grounding, banned phrases, prohibited claims and near-duplicate checks cover every part.
- `validate_campaign` checks the shape of `parts` and `slides` and validates each part with `validate_post`. It still emits the unchanged `TEXT_ONLY_STAGING` error for every non-text format, so `campaign-stage` and `campaign-export` stage and export text variants only, as before.
- `claims.effective_brand_rules` merges the optional brand `accounts[account_id]` override additively: the union of banned phrases, every distinct disclosure, and the lowest `max_hashtags`. A new `too_many_hashtags` error appears only when a cap is set. Brand files without these fields give the same findings as before.

The confidence level follows rules, not a model, and inherits the limits of lexical grounding. Capability statuses are registry research, not consent. Approval, dry-run, emergency stop, credential isolation and ambiguous-send reconciliation are unchanged. See [campaign preview](CAMPAIGN-PREVIEW.md).

## Offline health, dead-letter view, opt-out list and caps file (issue #26 slice)

Four small offline modules, none of which contacts a provider:

```text
accounts.json + env (names) + platform_registry.json + SQLite (read-only) + caps.json
        |
health.build_health  -> per account: adapter (require_capability / require_backend_capability),
        |               registry staleness, credential names, LinkedIn version/expiry,
        |               Postiz config, queue counts, rolling attempts vs effective budget,
        |               needs_review / stale sending / overdue  -> ok | attention | blocked
        +-> health.dead_letter -> needs_review grouped by sanitized code + reconcile steps

SQLite (SELECT only: event_log + posts + settings) + optional optout.json
        |
ops_metrics.collect -> counters (claims, publishes, failures by send class, reconciles, binding
        |              failures, cooldowns) + gauges (status, needs_review, stale sending, due age,
        |              cooldowns, rebind, opt-out blocks, pause) with allow-listed labels
        +-> render_json | render_prometheus -> stdout | write_textfile (temp + os.replace, 0644)

optout.json -> optout.load_optout -> engagement.triage_engagement (listed author: no reply/issue draft)
                                  -> claims.check_claims (optout_mention error) -> validate_campaign
                                     -> campaign-check / claims-check / campaign-stage / campaign-export
                                  -> optout.optout_mentions -> draft (refused) / check (BLOCKED) / approve (refused)
                                  -> optout.publish_block_reason (loaded once per publish run)
                                     -> publish dry run: lists due posts that would be BLOCKED
                                     -> publish --live, direct adapters:
                                        account match -> SUPPORTED_LIVE -> OPT-OUT GATE -> capability gate
                                        -> credentials -> lint -> identity preflight -> claim_sending -> send
                                     -> publish --live, Postiz run_live_send:
                                        pause -> OPT-OUT GATE -> lint + capability + GET /integrations
                                        -> claim_sending -> POST /posts

caps.json -> caps.effective_caps(min max_per_24h, max min_gap) -> claim_sending(max_per_24h, min_gap_minutes)
```

Invariants:

- **Read-only health.** `health.py` reuses `storage.queue_health`, the capability registry checks and `linkedin.version_status`/`token_status`. It runs only `SELECT` statements, checks only whether credential variables are set, and prints names, never values. `blocked` exits 1; `attention` exits 0 so routine states (missing credentials before onboarding, a stale registry entry) do not page anyone. A missing accounts file, an invalid caps file or an invalid registry is reported as `blocked` instead of crashing.
- **Dead letter never retries.** `dead_letter` groups `needs_review` posts by `sanitize_error_code(last_error)`: the stored text is redacted, then only the first one or two `[a-z][a-z0-9_]*` segments before `:` are kept, so provider text and secrets cannot reach the report. Age comes from the latest `needs_review` event. The only remedy it offers is human `reconcile`.
- **Opt-out fails closed and over-matches.** A named opt-out file that is missing or malformed stops the command. Entries hold only `handle` or `domain`, `platform` and `recorded_on`. Bare handles match any instance, and domains match sub-domain handles and whole instances, because a false positive only removes a draft. Mentions are `@` tokens not preceded by a word character, `@`, `.` or `/`, so e-mail addresses and URL paths are ignored. `draft` refuses a mention (no row is created), `check` reports it, and `approve` refuses it. `publish` loads the list once per run before any claim, so a malformed file aborts the run. In `publish --live` the opt-out gate is a **pre-claim, pre-network** check, placed like the capability gate: in the direct-adapter loop it runs before the capability gate, credential check, identity preflight and `claim_sending`; in Postiz `run_live_send` it runs right after the pause check and before the read-only `GET /integrations`. A blocked post prints `BLOCKED`, gets no `send_claimed` event and stays `approved`. `run_live_send(optout=None)` loads the default list itself, so a direct caller cannot skip the gate. Publish output names only a mention count, never the listed handle. The gate can only withhold a send; `claim_sending`, `transition` and `reconcile` are unchanged.
- **Caps tighten only.** `effective_caps` returns the lowest `max_per_24h` and the longest `min_gap_minutes` of the CLI flag, the file defaults and the account entry. A looser file value cannot win, and a malformed file stops `publish` before any claim. The CLI passes the result into the existing `claim_sending` parameters for both the direct adapters and the Postiz backend. `claim_sending`, `transition`, `reconcile`, the emergency pause and the live opt-in are unchanged.

- **Read-only metrics with allow-listed labels.** `ops_metrics.collect` reuses `queue_health` and four SELECT-only `storage` helpers; it never writes, retries or contacts a provider. Event `detail` text can hold provider error text (`needs_review` reasons), so only fixed tokens are parsed from it (the trailing `class=`, a leading `reason=`, the cooldown's `account=`/`stage=`) and each is checked against a fixed list before it becomes a label. Post text is read only to count opt-out mentions; it is never emitted. Without `--since`, event counts are all-time Prometheus counters (`*_total`); with it they become `*_in_window` gauges. `data-purge` can delete events, which Prometheus treats as a counter reset.

Not implemented: alerting itself (only example rules, untested against a live Prometheus), traces, and per-provider spend ceilings in money. See [worker operations](WORKER-OPERATIONS.md#health-and-dead-letter-view-issue-26), the [runbook](WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets) and the [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list).

## Canonical export of the legacy outbox (issue #4 slice)

`reachweaver/schema_export.py` is the migration path from the SQLite outbox to the canonical schema 1.2 documents (current `SCHEMA_VERSION`). `export_documents(con, accounts)` sets `PRAGMA query_only` for the duration of the export and restores it afterwards. It reads `posts`, `event_log`, `campaign_variants`, `metric_snapshots` and the public `accounts.json` mapping, and returns validated documents plus an `unmapped` list with a source row and reason for each skipped document. CLI `schema-export [--out FILE] [--type T]` is a DB branch inside the `connect` block. It prints JSON lines to stdout, or writes them to a new 0600 file through `privacy.write_private_file`, and refuses an existing path.

```text
posts (+campaign_variants) --> PlatformVariant + PublishJob   (state via STORAGE_STATUS_MAP; locale "und")
posts published + event    --> PublishReceipt                 (api_response | owner_verified_native_account)
metric_snapshots           --> AnalyticsSnapshot              (null = unknown; metric_status -> 1.2 metric_status)
evidence_json              --> Evidence
accounts.json (public)     --> Account                        (only with brand_id + owner consent)
Draft, Approval            --> unmapped                       (authorship / payload hash never recorded)
```

The module imports no publisher, connection or vendor code. It changes no storage schema, state machine, claim, transition, reconcile or publish semantics. It never mints an `Approval`: exported jobs reference an `approval_id` that is listed as unmapped, so `transition_job` fails closed on them. Documents with secret-looking text are skipped, not altered. ADR-0003 records the connector-replaceability decision and its fitness tests. [SCHEMAS.md](SCHEMAS.md) has the full mapping table.

## Read-only public counter polling (issue #15 slice)

`reachweaver/metrics_fetch.py` turns published Bluesky and Mastodon receipts into metric snapshots. It reads public counters only, not private analytics. CLI `metrics-fetch --online [--account ID] [--post-id ID] [--max-posts N] [--dry-run]` is a DB branch inside the `connect` block. Without `--online` it refuses, unless `--dry-run` is given. `--dry-run` reads the outbox and prints the planned requests with no network and no write.

```text
posts (published, remote_id) + accounts.json (bluesky|mastodon, no backend)
        |  select_posts: newest first, max 50 (<=200), skipped/deferred with reasons
        +-> bluesky  -> GET public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris=..(<=25)   no auth
        |               author.did == DID in at:// URI, else author_mismatch
        +-> mastodon -> GET {server}/api/v1/statuses/:id   no auth
                        401/404 + {env_prefix}_TOKEN set -> exactly one bearer retry
                        id, account.username and local acct == configured handle
        |  map_counts: documented fields only; missing/invalid -> NULL (unknown)
        v
store_metrics(post_id, observed_at=utc_now(), source=bluesky-public-api|mastodon-api)
        -> UNIQUE(post_id, observed_at, source) upsert -> report / experiment readouts
```

Invariants:

- **No platform writes.** Every request goes through `publishers.request_get_json`: GET, no redirects, bounded body and timeout, and sanitized `http_NNN` errors. Tests replace `publishers._send_http` and assert that each request is a GET to `getPosts` or `/api/v1/statuses/`, and that `request_json` (POST) is never called. Credentials go only in the one Mastodon retry header. They are never printed or stored, and the dry-run shows only the variable name. Bluesky credentials are never read.
- **Unknown is not zero.** Only `likes`, `reposts`, `replies` and (Bluesky `bookmarkCount`, when present) `saves` are filled. `views`, `impressions`, `clicks` and `signups` stay NULL. A response with no documented counter writes no row. The field mapping is cross-referenced to `attribution.metric_definition` in [ATTRIBUTION.md](ATTRIBUTION.md#2b-read-only-public-counter-polling-metrics-fetch).
- **Polite and non-retrying.** Requests run one at a time with a per-run cap. A 429 raises `RateLimited`, which stops the run at once. Earlier snapshots are kept and the remaining posts are listed under `not_attempted`. Apart from the single documented Mastodon token retry, nothing is retried.
- **Outbox state untouched.** The module calls only `store_metrics` (which adds a `metrics_recorded` event). It never changes `posts.status` and cannot claim, send or reconcile. The approval, dry-run, emergency-pause, credential-isolation and ambiguous-send rules are unchanged.

Not implemented: owner-authorized private analytics (LinkedIn `memberCreatorPostAnalytics`, X `non_public_metrics`, Meta insights and others), follower-count polling, scheduled or background polling, and Telegram view counts (which need the MTProto client API, not the Bot API).

## Landing-page checks (issue #25 slice)

`reachweaver/landing.py` checks the pages that campaigns send readers to. CLI `landing-check --briefs FILE [--campaign CAMPAIGN.json] [--online] [--allow-origin O...] [--json]` is an early branch that returns before the `connect` block, so it never opens the database. The offline part reuses `attribution.normalize_destination`, `validate_utm_link`, `load_seo_briefs` and `seo_conflicts`, plus `claims.check_claims` for the grounded claim phrases.

`reachweaver/link_health.py` (CLI `link-health`, public issue #10) applies the same guarded fetcher (`landing.fetch_landing`: robots.txt first, inspected redirects, https and registrable-domain rules, size cap) to the links already stored in local posts. It runs on a `connect_readonly` connection (never migrates, never creates the database), groups links by UTM-stripped destination, and sends at most one GET per distinct page and at most `--max-links` pages (1..50) per run, only with `--online`. Statuses: `ok`, `redirected`, `broken`, `blocked_by_robots`, `unusable`, `not_checked`. Only `broken` and `unusable` are errors (exit code 1). It cannot approve, schedule, publish or write.

```text
briefs.json --canonical_url_findings--> per-page errors (https, fragment, utm/trackers, normal form)
campaign.json --cta_url + tagged links--> validate_utm_link -> strip tags -> brief canonical_url? else cta_no_brief
              --check_claims grounded[]--> phrases per linked page
   |  --online only
   v
RobotsCache.skip_reason(url)  (sources.py; per host, per run)
   -> publishers._send_http GET (no auto-redirect, 25 s timeout)
      3xx: inspect Location -> <=5 hops, https, same registrable domain or --allow-origin
      2xx: Content-Length and read() capped at 2 MB -> html.parser (_LandingHTML)
   -> rel=canonical, title, meta description, og:*, noindex, html lang, lexical claim phrases
   -> page status ok|warning|error; exit 1 on any error
```

Invariants:

- **Network only behind `--online`.** Without it, no request is made. With it, every request is a GET with the ReachWeaver User-Agent and no credentials, sent through `publishers._send_http`, which is the single point tests mock. Tests cover a 404, an off-domain redirect, a canonical mismatch, a missing `og:image`, `noindex`, a robots.txt disallow, an oversized body and missing claim phrases without contacting any host.
- **Redirects are decisions, not side effects.** The opener refuses redirects, so each hop is checked before it is fetched. A refused target is recorded in the chain and never requested, and neither is its robots.txt.
- **Untrusted page content.** Values from pages pass through `sources.clean_text` and are truncated. Script, style, template, noscript and svg content is excluded from page text.
- **Lexical, not truth.** `claim_phrase_not_on_page` is a wording mismatch warning. Nothing here verifies a claim.
- **No outbox effect.** The module imports no storage code and cannot draft, approve, schedule, publish or reconcile. The approval, dry-run, emergency-pause, credential-isolation and ambiguous-send rules are unchanged.

Not implemented: HEAD requests (one bounded GET is enough), JavaScript rendering, per-platform preview rendering, and a full Public Suffix List.
