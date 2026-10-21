# Guarded scheduled delivery and queue operations — Issue #11

**Operational status:** No real user account has been connected or live smoke-tested. The code guards below are offline-tested and the deployment examples are **inert**, not evidence of a running worker. Enabling real delivery requires the owner's explicit approval and a supervised pilot.

## Implemented protection

- The existing SQLite outbox only sends exact approved content after explicit live opt-in and account identity preflight.
- **Approval payload binding (issue #7):** every approval is bound to a SHA-256 of the exact account, platform, text, schedule time, source URL and campaign. A post that changed after approval, or that was approved before this binding existed, is `BLOCKED` before the identity preflight and again inside the claim lock. It stays approved/scheduled until a human re-approves it. See the [upgrade note](#upgrade-note-approval-payload-binding-issue-7).
- **Send options and repeats (2026-10-21):** a Mastodon account's visibility, language and content warning, and a Postiz account's server and integration, are bound into each approval; an exact repeat of material the same account already shared is refused inside the claim, so two approved copies never both go out. See the [upgrade note](#upgrade-note-send-option-binding-and-the-send-time-repeat-guard-2026-10-21).
- **Per-account rolling 24-hour cap:** default two send attempts, configurable with --max-per-24h (1–10); default 60-minute gap configurable with --min-gap-minutes (15–1440). These are ReachWeaver operator safeguards, **not provider API quotas or dollar-cost ceilings**.
- Both are checked inside the SQLite BEGIN IMMEDIATE claim. Durable send_claimed audit events count even after uncertainty or human reconciliation, so a restart or draft reset does not restore quota.
- **Provider cooldowns after a 429 (issue #11):** a rate-limit answer from a provider records a per-account cooldown. Until it ends, that account's **other, unclaimed** posts are `SKIPPED` before any claim or request and stay approved/scheduled. The post that got the 429 goes to `needs_review` as before and is **never resent automatically**. Read-only identity checks (not Bluesky) may be retried twice with jitter before the claim; no content write is ever retried. See [provider cooldowns](#provider-rate-limits-and-cooldowns-issue-11).
- **queue-health** reports local status totals, send attempts by account in the last 24 hours, overdue scheduled posts, stale sending, needs_review and emergency pause. It contacts no provider, does not retry, and omits content/tokens.
- **--scheduled-only --max-lateness-minutes 180** skips approved-but-unscheduled posts and prevents late missed slots from being sent. Skipped overdue items remain scheduled for manual review. Old one-off live publishing without --scheduled-only remains available for compatibility; an unattended worker **must use --scheduled-only**.

## Operator workflow

~~~bash
reachweaver doctor
reachweaver health                  # one offline readiness view; exit 1 when blocked
reachweaver health --json --stale-minutes 60
reachweaver dead-letter             # needs_review grouped by error code + reconcile steps
reachweaver queue-health
reachweaver queue-health --stale-minutes 60

# Safe preview: never contacts social platforms
reachweaver publish --scheduled-only --max-lateness-minutes 180 --limit 10

# After reading each exact post and approving it:
reachweaver show POST_ID
reachweaver approve POST_ID
reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00

# Real publishing only AFTER explicit owner authorization and test-post pilot,
# and only on the production host (REACHWEAVER_ENV=prod is required as well, issue #5):
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --scheduled-only \
  --max-lateness-minutes 180 --max-per-24h 2 \
  --min-gap-minutes 60 --limit 10
~~~

A credential-filled environment must exist only on the trusted host, and the provider authorization must be validated independently. A dry-run or scheduled local post is not a remote receipt.

## Upgrade note: approval payload binding (issue #7)

Owner-approved 2026-10-08 (decision record). Opening an existing database adds three nullable columns to `posts` (`approved_payload_sha256`, `approved_by`, `approval_channel`). No existing row is rewritten, and **no existing approval is grandfathered**:

- Every post that was `approved` or `scheduled` before the upgrade has no hash. No `publish --live` run (CLI or Postiz backend) will claim it. Each run prints `BLOCKED POST_ID: approval_hash_missing_reapproval_required ... no social post sent`. The post keeps its status, `last_error` is set and one `approval_binding_failed` event is written. `schedule` and `calendar-plan --apply` refuse it with `Nothing scheduled`.
- A post whose text, account, platform, source URL, campaign or due time was changed outside the sanctioned `approve`/`schedule` path is refused the same way, with `approval_hash_mismatch_reapproval_required`.
- `reachweaver health` reports both cases as `attention` with code `approval_rebind_required` and lists the posts under `approval_rebind_required` (`queue-health` lists them too). `publish` dry-run marks them `BLOCKED`.

Re-approve each listed post by hand after reading it again. There is no in-place rebind and no bypass flag:

~~~bash
reachweaver health                      # lists posts needing re-approval
reachweaver unapprove POST_ID           # back to draft; clears the old binding
reachweaver show POST_ID                # exact text, account, links; "approval": {"binding": ...}
reachweaver approve POST_ID --approver YOUR_NAME   # binds a fresh hash and prints it
reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00   # only if it was scheduled before
~~~

If the text is wrong, do not approve it: cancel it and create a corrected draft. `--approver` (or `$USER`/`$LOGNAME`) is attribution, not authentication. An unattended worker prints `BLOCKED` for these posts on every run until they are re-approved, so clear the list before you enable a timer. Rollback is a code revert; the extra columns are harmless to older code (proposal section 4).

## Upgrade note: verified-identity binding (wave 9 item 2)

Owner-approved 2026-10-10 (proposal, section 2). Opening an existing database adds one nullable column, `posts.approved_identity`. Nothing else is rewritten.

- **Off by default.** Every approval made before the upgrade, and every approval made without `--verify-identity`, has `approved_identity` NULL and keeps its exact `approval/1` hash. It is not invalidated, because this feature is opt-in and changes nothing until you use it. That is a documented default, not a silent grandfathering; the next bullet is the owner-controlled switch that ends it.
- **Making it mandatory.** `reachweaver approval-mode --require-identity` latches `settings.approval_identity_mode`. From then on, plain `approve` is refused offline with `approval_identity_required`; `publish --live` refuses every approved or scheduled post without a stored identity (before the identity preflight and any request) with `approval_identity_missing_reapproval_required`; `schedule` refuses it (`Nothing scheduled`); and `health` and the `publish` dry run list it under `approval_rebind_required`. There is no bypass flag, and no command removes the latch.
- **Upgrade path for posts approved before the latch** (the same ritual as the other re-approvals, with the new flag):

~~~bash
reachweaver health                                       # lists posts needing re-approval
reachweaver unapprove POST_ID
reachweaver show POST_ID
reachweaver approve POST_ID --verify-identity --online   # one read-only identity request, then binds the fingerprint
reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00   # only if it was scheduled before
~~~

- **What a mismatch means.** If the account entry (handle, server, service, ids, or the account id itself) is edited after approval, `publish --live` prints `BLOCKED POST_ID: approval_identity_mismatch_reapproval_required` before any request. The post stays `approved` or `scheduled`, `last_error` holds the code and one `approval_binding_failed` event is written. Re-approve with the ritual above after checking that the config change was yours. The messages never contain the handle, the fingerprint or any remote value.
- **Limits.** Postiz-backed accounts and the TikTok/YouTube video runners cannot be identity-bound, and those senders pass no fingerprint; with `--require-identity` latched their posts are not sent, so leave it off if you rely on them. To leave the mode, remove the latch by hand ([account onboarding, 1e](ACCOUNT-ONBOARDING.md#1e-optional-bind-the-verified-identity-to-each-approval-wave-9-item-2)). A Bluesky `--verify-identity` reserves one `createSession` call like `connect-check`. Rollback is a code revert; the extra column is harmless to older code.

## Upgrade note: approval nonce and replay ledger (wave 9 item 1)

Owner-approved 2026-10-10 (proposal, section 1). Opening an existing database adds one nullable column, `posts.approval_nonce`. Nothing else is rewritten. A file `approval-ledger.jsonl` (mode 0600) is created next to the database by the first `approve` after the upgrade.

- **Not grandfathered.** Every post that is `approved` or `scheduled` at upgrade time has no nonce. `publish --live` prints `BLOCKED POST_ID: approval_nonce_missing_reapproval_required` before the identity preflight and any request, `schedule` refuses it (`Nothing scheduled`), and `health` and the `publish` dry run list it under `approval_rebind_required`. Posts that are `draft`, `published`, `cancelled`, `sending` or `needs_review` are untouched. Clear the list before you enable a timer:

~~~bash
reachweaver health                 # lists posts needing re-approval
reachweaver unapprove POST_ID
reachweaver show POST_ID
reachweaver approve POST_ID        # draws a fresh nonce
reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00   # only if it was scheduled before
~~~

- **Normal use is unchanged.** `approve` then `publish --live` sends once; the claim appends one line to the ledger just before the post becomes `sending`. A human `unapprove` then `approve` draws a new nonce and is accepted.
- **Withdrawals are recorded (security decision item 12, owner-approved 2026-10-21).** `unapprove` and `cancel` of an approved or scheduled post append one `withdrawn` line for its nonce, before the post changes. If a database backup taken while the post was approved is restored later, `publish --live` prints `BLOCKED POST_ID: approval_withdrawn_reapproval_required` before the identity preflight and any request: the post stays `approved`, `last_error` holds the code, one `approval_replay_blocked` event is written and `health` lists it as `attention`. If you still want to send it, review it and re-approve with the ritual above (the `unapprove` adds no second line). Each post and nonce gets at most one ledger line (`consumed` or `withdrawn`). A withdrawal always succeeds: if the ledger is missing, unreadable or broken, the post is still withdrawn, the `unapprove`/`cancel` event ends in `ledger=unrecorded reason=approval_ledger_unavailable`, and the command prints `WARNING: the withdrawal of post POST_ID is not recorded in the approval ledger ...` on stderr. Restore the ledger then, because a restored backup could still carry that approval. Existing ledgers need no change. A version older than this one reads a `withdrawn` line as a broken chain and refuses every send and approval, so do not downgrade after a withdrawal (or restore the database and the ledger together from a backup taken before the upgrade).
- **A refused replay** prints `BLOCKED POST_ID: approval_replay_reapproval_required`: the approval was already used or restored from an older copy (`approval_withdrawn_reapproval_required` when it had been withdrawn, see above). The post stays `approved`, `last_error` holds the code and one `approval_replay_blocked` event is written. If it is a genuine second send you want, review it and re-approve with the ritual above. If you did not expect it, treat it as a restored backup or tampering, `pause`, and check the account by hand.
- **Crash window.** The ledger line is written before the status flip. If the process dies between them, the row stays `approved` with a consumed nonce: it is refused (safe direction) and `health` lists it; recover with `unapprove` and `approve`.
- **Ledger problems fail closed.** A missing, unreadable, symlinked, wrongly-permissioned (not 0600 or not owned by the running user) or chain-broken ledger prints `approval_ledger_unavailable` and nothing is claimed or approved. Restore the file from a backup taken with the database; back both up together, and keep them in the same private directory. The ledger is never silently recreated once any post carries a nonce. If the file is lost and cannot be restored, the owner decides after review whether to create an empty one by hand; that forgets every earlier consumption, so check the account for duplicates first.
- **Where it lives.** The ledger sits in the database's directory and is shared by every database file in that directory. Copying a database to another directory also needs its ledger copy. An in-memory database cannot approve or claim.
- **Limits.** The ledger is writable by the same Unix user. It makes silent edits detectable and a naive or accidental restore refused (of a used or a withdrawn approval); it does not stop an attacker who rewrites the whole chain, and a withdrawal made while the ledger was unusable is not recorded. Run the worker as its own system user so the ledger is not writable from the agent's account (THREAT-MODEL.md T7). Rollback is a code revert; the extra column and file are ignored by older code, and the replay gap returns.

## Upgrade note: send-option binding and the send-time repeat guard (2026-10-21)

Owner-approved 2026-10-21. Opening an existing database adds two nullable columns, `posts.approved_send_options` and `posts.approved_allow_repeat`. Nothing else is rewritten, and every stored approval hash stays valid: the new payload keys exist only when a column is set.

- **Mastodon and Postiz approvals need one re-approval.** `approve` now binds a Mastodon account's `visibility`, `language` and `spoiler_text`, and a Postiz account's `postiz_base_url` and `postiz_integration_id`. A Mastodon or Postiz post that is `approved` or `scheduled` at upgrade time has no binding and is not grandfathered: `publish --live` prints `BLOCKED POST_ID: approval_send_options_missing_reapproval_required` before the identity preflight and any request, and the post stays approved. `health` and the dry run list Mastodon rows under `approval_rebind_required` without loading accounts; a Postiz row is listed by the live run. Bluesky, Telegram, LinkedIn and the video platforms are unaffected. Clear the list before you enable a timer:

~~~bash
reachweaver health                 # lists Mastodon posts needing re-approval
reachweaver unapprove POST_ID
reachweaver show POST_ID           # check approval.send_options: the values that will be bound
reachweaver approve POST_ID        # prints "Bound send options: ..."
reachweaver schedule POST_ID --at 2026-10-15T09:00:00+02:00   # only if it was scheduled before
~~~

- **Changing those settings later** refuses the send with `approval_send_options_changed_reapproval_required` (post stays approved, one `approval_binding_failed` event). Restore the approved values or re-approve.
- **Exact repeats are refused at send time.** A post whose video, image or exact wording the same account has already published, is sending, has in `needs_review` or recorded with `mark-shared` is `SKIPPED ... repeat_of_shared_material` before any claim or request (worker log reason `repeat_of_shared_material`). Nothing is written and it stays approved, so an unattended worker repeats the line on every run until you `cancel` it or re-approve it with `approve --allow-repeat`. Approvals made with `--allow-repeat` before this upgrade carry no bound flag; re-approve them with `--allow-repeat` if every copy should still go out.
- **Posts with images or a video need an AI-label answer (unreleased, owner-approved 2026-10-21).** A media post whose AI-label decision is required or unknown and that has no bound `--ai-label` answer is `BLOCKED ai_label_unanswered` before any claim or request and stays approved/scheduled; this includes media posts approved before the upgrade. Re-approve: `unapprove`, `show`, then approve with `--ai-label applied|not_needed` ([AI-CONTENT-LABELS.md](AI-CONTENT-LABELS.md#when-approval-asks-for-an-answer)). Text-only posts are unaffected.
- Rollback is a code revert; the extra columns are ignored by older code.

## Health and dead-letter view (issue #26)

`reachweaver health [--json] [--stale-minutes N]` is offline. For every account in `config/accounts.json` it shows:

- the adapter (`live_text`, `backend` for Postiz, or `manual`) as the capability registry decides it, and whether that registry entry is older than 120 days;
- `research_freshness`: the oldest checked date and the fresh/aging/stale/undated counts of that platform's facts in the distribution, discovery, profile-audit and sign-up registries (a one-line total for all registries is printed near the top and kept in `global.registry_freshness`; detail with `reachweaver registry-age`);
- credential variable **names** present and missing (values are never printed);
- for LinkedIn, the pinned version status and the recorded authorization expiry; for Postiz, whether the backend is configured; for Mastodon, the server and whether `max_characters` is pinned;
- local queue counts by status, send attempts in the rolling 24 hours against the effective budget (including the caps file), the time the minimum gap next allows an attempt, `needs_review`, stale `sending`, overdue schedules and approved/scheduled posts that need re-approval (`approval_rebind_required`, issue #7).

It also shows the emergency pause, the live opt-in flag and posts that belong to accounts no longer configured. The overall result is `ok`, `attention` or `blocked`, and each reason has a code:

| Level | Codes |
| --- | --- |
| `blocked` (exit 1) | `emergency_pause_active`, `accounts_config_unloadable`, `caps_file_invalid`, `capability_registry_invalid`, `linkedin_version_unsupported`, `linkedin_authorization_expired`, `instagram_authorization_expired`/`threads_authorization_expired` once that adapter is live, `credentials_missing` on an account with approved or scheduled posts |
| `attention` (exit 0) | `credentials_missing` with nothing queued, `registry_entry_stale`, `research_facts_stale`, `research_registry_invalid`, `manual_platform_has_queued_posts`, `rolling_budget_exhausted`, `needs_review`, `stale_sending`, `overdue_scheduled`, `approval_rebind_required`, `provider_cooldown_active`, `provider_cooldown_ceiling_clamped`, `linkedin_version_ageing`, `linkedin_authorization_warning`/`_unknown`, `instagram_authorization_*`/`threads_authorization_*` (`_warning`; `_expired` while the adapter is manual-only; `_unknown` only once it is live), `queued_posts_for_unconfigured_accounts`, `no_accounts_configured` |

A monitor can alert on a nonzero exit. `health` never contacts a provider, so `ok` does not prove a token still works; `connect-check --online` does that.

`reachweaver dead-letter [--json]` (also embedded in `health --json` as `dead_letter`) lists every `needs_review` post grouped by a sanitized error code such as `network_or_timeout_uncertain`, `http_401` or `postiz_accepted_platform_receipt_unverified`. Raw error text is never shown. Each group has the oldest age in minutes, a suggested action and the reconcile steps. Each post also carries its `send_class` (`certain_not_sent`, `likely_not_created`, `ambiguous`, or `unknown` for failures recorded before issue #11) with a short explanation; see [classification](#how-a-failure-is-classified). Nothing is retried: each post must be checked against the native account and reconciled by hand.

## Operational metrics and alerting (issue #26)

`reachweaver ops-metrics [--format json|prometheus] [--since 24h|ISO8601] [--stale-minutes 30] [--optout F] [--out FILE] [--now ISO8601]` prints read-only counts from the local database. It runs SELECT statements only. It never contacts a provider, retries a send, reconciles or changes a row (a test checks that the database file is byte-identical afterwards).

The database is opened strictly read-only (`storage.connect_readonly`: SQLite `mode=ro` plus `PRAGMA query_only`). A missing `--db` path fails with exit 2 (`Database ... does not exist; read-only checks never create one`) instead of creating an empty database that would report zeros, so a mistyped path in a timer shows up as a failed unit, not as a quiet queue. A database from an older ReachWeaver that lacks a column ops-metrics reads (for example `posts.approved_payload_sha256`) fails with exit 2 and names the missing columns; ops-metrics never migrates it. Run `reachweaver init` (or any write command) once with the new version to add the columns, then retry. `--since` accepts at most 3650 days and `--now` must lie between 1970-01-01 and 9998-12-31; anything else is exit 2, never a traceback.

| Metric (prefix `reachweaver_`) | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `send_claims_total` | counter | account_id, platform | `send_claimed` audit events (live send attempts started) |
| `published_total` | counter | account_id, platform | adapter publish receipts (`published` events) |
| `send_failures_total` | counter | account_id, platform, send_class | sends moved to `needs_review`; `send_class` is `certain_not_sent`, `likely_not_created`, `ambiguous` or `unknown` |
| `reconciled_total` | counter | account_id, platform, outcome | human reconciliations, `published` or `not_published` |
| `approval_binding_failures_total` | counter | account_id, platform, reason | claims refused by the approval hash (issue #7) |
| `provider_cooldowns_recorded_total` | counter | account_id, platform, stage | provider 429 cooldowns recorded (`preflight` or `send`) |
| `posts` | gauge | account_id, platform, status | current posts per status |
| `needs_review_posts` | gauge | account_id, platform, send_class | uncertain sends awaiting reconcile, by class |
| `dead_letter_posts` | gauge | none | total `needs_review` posts |
| `stale_sending_posts` | gauge | account_id, platform | `sending` older than `--stale-minutes` (or with no valid start time) |
| `overdue_scheduled_posts` | gauge | account_id, platform | scheduled posts past their due time |
| `due_unsent_posts` | gauge | account_id, platform | approved or scheduled posts that are due now and unclaimed |
| `oldest_due_unsent_age_seconds` | gauge | none | age of the oldest of those (0 when none) |
| `approval_rebind_required_posts` | gauge | account_id, platform, reason | approved/scheduled posts refused until re-approved |
| `send_attempts_rolling_24h` | gauge | account_id, platform | claims in the rolling 24 hours |
| `needs_review_credential_posts` | gauge | account_id, platform, cause | needs_review posts whose stored error code is HTTP 401/403 (`permission`) or 426 (`version_sunset`); local state only, it never proves a token works |
| `provider_cooldown_active`, `provider_cooldown_remaining_seconds` | gauge | account_id, platform | active 429 cooldowns |
| `optout_blocked_posts`, `optout_list_entries` | gauge | account_id, platform / none | queued posts that mention an opt-out listed handle (counted, never named); list size |
| `emergency_paused` | gauge | none | 1 while the emergency pause is on |
| `ops_metrics_generated_timestamp_seconds` | gauge | none | when the metrics were computed |

Without `--since`, the event families are all-time counters ending in `_total`, so `rate()` and `increase()` work. With `--since` they become gauges ending in `_in_window` (for example `send_claims_in_window`), and `ops_metrics_window_start_timestamp_seconds` records the window start. `data-purge` can delete old events; Prometheus treats the resulting drop as a counter reset.

**Labels never carry personal data or secrets.** A label value is one of: an account id that matches the `accounts.json` id rule, a known platform, a post status, or a fixed code (send class, binding reason, cooldown stage, reconcile outcome). Any other stored value is reported as `invalid`, `other` or `unknown`. Post text, third-party handles, URLs, provider error text and credential values never appear. `tests/test_security_regressions.py` (`OpsMetricsLeakTests`) seeds secret-shaped and personal data into posts and events and checks both formats.

**Prometheus textfile.** `--format prometheus` writes the [text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/) (accessed 2026-10-09): one `# HELP` and `# TYPE` line per family, escaped label values, families and samples sorted, so the same state always gives the same bytes. `--out FILE` writes a temporary file in the same directory and renames it over `FILE` (mode 0644), so node_exporter's [textfile collector](https://github.com/prometheus/node_exporter#textfile-collector) (accessed 2026-10-09), which reads only `*.prom` files, never sees a partial file. The file holds counts and the labels above, nothing secret.

`--out` refuses, with exit 2 and nothing written, a target that could destroy data: the state database itself (the same path, the same resolved path, or a hard link to it), any symbolic link (even one pointing at a `*.prom` file), a directory, and an **existing** file whose name does not end in `.prom` or `.json`. A target that does not exist yet may have any name. Point `--out` at a dedicated file such as `.../textfile_collector/reachweaver.prom`.

### Example timer for the textfile collector (example only, not installed)

```ini
# /etc/systemd/system/reachweaver-metrics.service  (EXAMPLE; adjust paths and user)
[Unit]
Description=ReachWeaver read-only operational metrics for node_exporter

[Service]
Type=oneshot
User=reachweaver
Group=reachweaver
WorkingDirectory=/opt/reachweaver
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
# ops-metrics opens the database read-only (SQLite mode=ro) and runs SELECT statements only;
# the state directory is listed for SQLite's own locking. No credentials and no live opt-in are needed.
ReadWritePaths=/var/lib/reachweaver /var/lib/node_exporter/textfile_collector
ExecStart=/opt/reachweaver/.venv/bin/reachweaver --db /var/lib/reachweaver/reachweaver.sqlite ops-metrics --format prometheus --out /var/lib/node_exporter/textfile_collector/reachweaver.prom

# /etc/systemd/system/reachweaver-metrics.timer  (EXAMPLE)
[Unit]
Description=Refresh ReachWeaver metrics every 5 minutes

[Timer]
OnCalendar=*:0/5
AccuracySec=30s
Unit=reachweaver-metrics.service

[Install]
WantedBy=timers.target
```

The `reachweaver` user needs write access to the textfile directory, and node_exporter needs read access to the file. Start node_exporter with `--collector.textfile.directory=/var/lib/node_exporter/textfile_collector`. Add `--optout /etc/reachweaver/optout.json` to count opt-out blocks; a named list that is missing or malformed makes the run fail (exit 2), which the staleness alert below catches.

### Example alert rules (examples; checked against a running Prometheus on 2026-10-16)

On 2026-10-16 these rules passed `promtool check rules` (9 rules) and were loaded into a running Prometheus 3.15.0 (official release, checksum verified). It scraped real `ops-metrics --format prometheus` output from a synthetic, paused database. All nine rules evaluated as healthy with no errors, and `ReachWeaverEmergencyPause` went to `pending` as expected. `tests/test_alert_rules_doc.py` keeps every metric name in these rules in step with what `ops-metrics` emits. They have not run on a production host, so review the thresholds before use. node_exporter's textfile collector serves the correct content type. A plain file server does not: it needs `fallback_scrape_protocol: PrometheusText0.0.4` in Prometheus 3.

```yaml
groups:
  - name: reachweaver
    rules:
      - alert: ReachWeaverNeedsReview
        expr: reachweaver_dead_letter_posts > 0
        for: 5m
        annotations:
          summary: "Uncertain send(s) need a human check of the native account, then reconcile (never resend)."
      - alert: ReachWeaverDeadLettersIncreasing
        expr: delta(reachweaver_dead_letter_posts[1h]) > 0
        annotations:
          summary: "New needs_review posts in the last hour; run 'reachweaver dead-letter'."
      - alert: ReachWeaverCredentialOrSunsetFailure
        expr: sum(reachweaver_needs_review_credential_posts) > 0
        annotations:
          summary: "A send failed with 401/403/426; follow the revoked-permission and API-sunset runbook (reconcile first)."
      - alert: ReachWeaverEmergencyPause
        expr: reachweaver_emergency_paused == 1
        for: 15m
        annotations:
          summary: "Emergency pause is on; nothing will be sent until 'reachweaver resume'."
      - alert: ReachWeaverStaleSending
        expr: sum(reachweaver_stale_sending_posts) > 0
        annotations:
          summary: "A post is stuck in sending; check the native account, then reconcile."
      - alert: ReachWeaverOldestDueTooOld
        # Below the default 180-minute --max-lateness-minutes, so it fires before the worker skips the post.
        expr: reachweaver_oldest_due_unsent_age_seconds > 7200
        for: 10m
        annotations:
          summary: "A due post has not been sent for over 2 hours (pause, cooldown, budget, binding or worker down)."
      - alert: ReachWeaverApprovalRebindRequired
        expr: sum(reachweaver_approval_rebind_required_posts) > 0
        annotations:
          summary: "Approved posts are refused until a human re-approves them (unapprove, show, approve)."
      - alert: ReachWeaverOptOutBlocked
        expr: sum(reachweaver_optout_blocked_posts) > 0
        annotations:
          summary: "Queued posts mention an opt-out listed handle; edit or cancel them."
      - alert: ReachWeaverMetricsStale
        expr: time() - reachweaver_ops_metrics_generated_timestamp_seconds > 1800
        annotations:
          summary: "ops-metrics has not refreshed for 30 minutes; the metrics timer may be failing."
```

`sum()` over a family with no samples returns nothing, so these rules stay quiet on an empty queue. A clear alert is not proof that a post went out; only the native account is.

## One-page operations summary (issue #26)

`reachweaver ops-summary [--json] [--since 24h|ISO8601] [--stale-minutes 30] [--optout F] [--now ISO8601]` answers "is the worker healthy?" in one call. It does not add new checks. It composes [`health`](#health-and-dead-letter-view-issue-26) (adapter capability from the registry, credential variable names, rolling budget, cooldowns, pause, dead letters) with [`ops-metrics`](#operational-metrics-and-alerting-issue-26) (claims, publishes, failures, stale sending, due age) into one document. `queue-health` data is included through both.

- **Read-only and offline.** The database is opened with `connect_readonly` (SQLite `mode=ro`): a missing database is exit 2 and is not created, and an older schema is named and not migrated. No provider is contacted, nothing is retried or reconciled, and no credential value is read into the report (only whether each variable is present).
- **Exit code.** 1 only when the overall level is `blocked` (same rule as `health`), otherwise 0, so a timer or monitor can alert on it. If the accounts file or caps file cannot be loaded the report still prints, as `blocked`.
- **Redaction by allow-list.** The JSON carries: account ids matching the id shape, known platforms, fixed reason codes (no `detail` text, which can name post ids or paths), missing credential variable names, counts, ISO times, and metric totals. Post text, third-party handles, URLs, provider error text and credential values never appear; the result is also passed through `privacy.redact_value`. Anything outside the shape becomes `invalid` or `other`.
- **Fields.** `overall`, `reasons[]` (`level`, `code`, `account_id`), `global` (pause, live opt-in flag, runtime environment, registry review date and stale platforms, status counts, opt-out list size), `accounts[]` (adapter `live_text|backend|manual`, registry status and staleness, missing variable names, budget, cooldown end, queue by status, `needs_review`, `stale_sending`, `overdue_scheduled`, `approval_rebind_required`), `dead_letter` (`total`, `by_code`, `by_send_class`) and `metric_totals` (each `ops-metrics` family summed over its samples; use `ops-metrics` for per-label detail).
- **Limits.** `ok` is local state: it is not proof that a token still works (`connect-check --online` does that) or that a remote post exists. An attention or blocked result is a prompt for the owner; it never authorises a resend or re-approval.

Tests: `tests/test_ops_summary.py`.

## Structured worker logs (issue #26)

`reachweaver publish --log-json [--log-file PATH]` adds one JSON object per line ([JSON Lines](https://jsonlines.org/), accessed 2026-10-09) for every worker decision, in a dry run and in a live run alike. The normal stdout text is unchanged. Lines go to stderr, or are appended to `PATH` with `--log-file` (which needs `--log-json`).

The log only observes. It does not change which posts are due, their order, claims, approval checks, the live gate, budgets, cooldowns, opt-out blocks or reconciliation. `tests/test_structured_logs.py` runs the same seeded database and the same scripted provider with and without `--log-json`, as a dry run and as a mocked live run (opt-in and production marker patched in, every HTTP request mocked). It checks that the rows, provider requests, stdout and exit codes are identical. Two read-only `SELECT`s are the only extra database access: they read back a Postiz backend hand-off result, and they run only when logging is on.

**Failures.** The destination is opened and the `run_start` line is written before the due query, before any account is loaded and before any claim. If that fails, the run is refused with exit 2 and nothing else happens. Causes include a missing or read-only directory, a symlink, a FIFO, a directory, a file another user owns, an existing file readable or writable by group or others, or a stderr that cannot be written. `--log-file` opens with `O_APPEND|O_CREAT|O_NOFOLLOW`, creates the file 0600 whatever the umask, and never truncates it. If a write fails later in the run (disk full, closed pipe), the line is dropped and counted in `dropped_lines`, and a `WARNING` goes to stderr at exit. The send continues exactly as without logging.

**Every line** has `schema_version` (1), `run_id`, `seq` (1, 2, … within the run), `ts` (UTC ISO 8601, seconds), `event` and `dry_run`. `run_id` is the run's UTC start time in nanoseconds plus 6 random hex characters (for example `20261009T101500.123456789Z-ab12cd`). It sorts by start time and strictly increases within one process.

| `event` | Extra fields | When |
| --- | --- | --- |
| `run_start` | `live_requested`, `scheduled_only`, `limit`, `post_id` (or null) | first line, before any work |
| `gate_refused` | `reason`: `live_gate`, `emergency_pause` or `human_confirmation` | `--live` without `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`, the emergency pause is on, or the opt-in human confirmation (#7 D6) is on and stdin/stdout is not a terminal; no account was loaded |
| `post_skipped` | `post_id`, `account_id`, `platform`, `reason` (plus `rate_limited` for `identity_preflight_failed`) | the post was not claimed (a dry run gives one per due post) |
| `post_claimed` | `post_id`, `account_id`, `platform`, `backend` | the atomic claim succeeded; a send is about to start |
| `post_published` | `post_id`, `account_id`, `platform`, `backend`, `remote_id`, `remote_url` | the adapter returned a receipt, stored as before |
| `post_needs_review` | `post_id`, `account_id`, `platform`, `backend`, `send_class`, `error_code`, `rate_limited` | the claimed post went to `needs_review`; it is never resent |
| `preflight_retry` | `post_id`, `account_id`, `platform`, `attempt` (the retry number, 1 or 2), `delay_ms` (the wait chosen before the retry), `code` (sanitized error code, for example `http_503`) | the read-only identity check before the claim failed with a retryable error and is retried after the wait (see "Preflight backoff" below); live runs only, never after a claim. Security decision item 3, owner-approved 2026-10-21: written to the log only, never to the database |
| `run_end` | `outcome` (`completed`/`error`), `exit_code`, `error_code`, `counts` {`due`, `skipped`, `claimed`, `published`, `needs_review`, `preflight_retries`}, `dropped_lines`, and in a dry run `emergency_paused` | last line, also when the run stops with an error |

`post_skipped` reasons are a fixed list. A dry run reports the first reason in live-run order, or `dry_run` when nothing would block the post:

| `reason` | Meaning |
| --- | --- |
| `dry_run` | preview only; a live run would try this post |
| `not_due` | `--post-id` names a post that is not approved and due (the run then exits 2) |
| `paused` | emergency pause (dry-run preview, or found inside the claim lock) |
| `account_missing` | no configured account, or its platform differs |
| `manual_only` | the platform has no live adapter |
| `optout_blocked` | mentions an opt-out listed handle |
| `rebind_required` | approval hash missing or mismatched (issue #7) |
| `cooldown` | provider cooldown after a 429 (issue #11), including a shared Postiz workspace cooldown |
| `session_budget` | the local Bluesky `createSession` budget would be exceeded; no request sent |
| `capability_refused` | the capability registry refused text publishing |
| `credentials_missing` | credential variables are absent (their names are not logged) |
| `content_preflight_failed` | the text failed the platform preflight |
| `identity_preflight_failed` | the read-only account identity check failed |
| `budget` | rolling 24-hour cap or minimum gap |
| `claim_refused` | any other claim refusal |
| `not_eligible` | the claim found the post no longer due or approved |
| `backend_refused` | the Postiz backend path returned without claiming |

**What never appears:** post text, third-party handles, emails, credential variable names or values, provider error text or bodies, and URL query strings or fragments. `account_id` must match the `accounts.json` id rule (otherwise `invalid`). `platform` must be a known platform (otherwise `other`). `post_id` and `remote_id` are allow-listed by shape. `remote_url` keeps only scheme, host, port and path; the database keeps the full stored URL. `error_code` is the sanitized adapter code (for example `http_503`, `network_or_timeout_uncertain`, `postiz_accepted_platform_receipt_unverified`), or the error kind (`validation_error`, `publish_error`) when the message is free text. Every value also passes `privacy.redact` with emails on. Two suites check all of this with secret-shaped, personal and query-string data planted in posts, provider replies and errors: `tests/test_structured_logs.py` and `tests/test_security_regressions.py` (`WorkerLogLeakTests`).

Example (one dry-run post, wrapped here; one line each in the log):

```json
{"dry_run": true, "event": "run_start", "limit": 10, "live_requested": false, "post_id": null, "run_id": "20261009T101500.123456789Z-ab12cd", "scheduled_only": true, "schema_version": 1, "seq": 1, "ts": "2026-10-09T10:15:00+00:00"}
{"account_id": "my-bluesky", "dry_run": true, "event": "post_skipped", "platform": "bluesky", "post_id": "3f2a9c1d0b7e4a65", "reason": "optout_blocked", "run_id": "20261009T101500.123456789Z-ab12cd", "schema_version": 1, "seq": 2, "ts": "2026-10-09T10:15:00+00:00"}
{"counts": {"claimed": 0, "due": 1, "needs_review": 0, "preflight_retries": 0, "published": 0, "skipped": 1}, "dropped_lines": 0, "dry_run": true, "emergency_paused": false, "error_code": null, "event": "run_end", "exit_code": 0, "outcome": "completed", "run_id": "20261009T101500.123456789Z-ab12cd", "schema_version": 1, "seq": 3, "ts": "2026-10-09T10:15:00+00:00"}
```

### journald and jq (examples only)

Under systemd, a service's stderr reaches the journal by default. `StandardError=` defaults to `inherit` (a copy of standard output), and `StandardOutput=` defaults to `journal` ([systemd.exec(5)](https://man7.org/linux/man-pages/man5/systemd.exec.5.html), accessed 2026-10-09; a host can change both defaults in systemd-system.conf). To log there, append `--log-json` to the `ExecStart=` line of your copy of the [example service](../deploy/systemd/reachweaver-publish.service.example); the repository's example is unchanged. Then filter with [jq](https://jqlang.org/manual/) (accessed 2026-10-09):

```bash
# Decision lines of today's runs (journalctl -o cat prints only the message)
journalctl -u reachweaver-publish.service --since today -o cat | jq -c 'select(.schema_version == 1)'
# Why posts were skipped, counted by reason
journalctl -u reachweaver-publish.service --since -7d -o cat | jq -r 'select(.event == "post_skipped") | .reason' | sort | uniq -c
# Every send that needs a human reconcile, with its class and code
journalctl -u reachweaver-publish.service --since -7d -o cat | jq -c 'select(.event == "post_needs_review") | {ts, post_id, account_id, send_class, error_code}'
# Identity-preflight retries per account and code (retries on every run mean a degrading provider)
journalctl -u reachweaver-publish.service --since -7d -o cat | jq -r 'select(.event == "preflight_retry") | "\(.account_id) \(.code)"' | sort | uniq -c
# Runs that ended in an error or lost log lines
jq -c 'select(.event == "run_end" and (.outcome != "completed" or .dropped_lines > 0))' /var/log/reachweaver/worker.jsonl
```

With `--log-file /var/log/reachweaver/worker.jsonl` the directory must exist, belong to the service user and be in `ReadWritePaths=`. Rotate the file with your own tool; ReachWeaver only appends. A log line is the worker's local record, not a platform receipt: confirm a `post_published` line against the native account like any other local state.

### Trace one post

`reachweaver trace POST_ID` prints everything recorded about one post, oldest first: its audit events from the database (draft, approval, send claim, outcome, reconcile) and, with `--log`, the worker-log lines that name it together with the start and end lines of those runs. So you can see which run claimed it, whether that run was a dry run, and how the run ended. Identity-preflight retries appear as `preflight_retry` lines with their `code`, `attempt` and `delay_ms`.

```bash
reachweaver trace 3e2f452ea59e4958                                   # database events only
reachweaver trace 3e2f452ea59e4958 --log /var/log/reachweaver/worker.jsonl
journalctl -u reachweaver-publish.service --since -7d -o cat | reachweaver trace 3e2f452ea59e4958 --log -
reachweaver trace 3e2f452ea59e4958 --log worker.jsonl --json          # for scripts
```

It is read-only: the database is opened `mode=ro` and never created, and nothing is sent, retried or contacted. A log is treated as untrusted input. Only lines with the worker-log schema version and a known event name are used, only the fields above are kept, every value is redacted, a line over 16 KiB is skipped, a file over 64 MiB, a symbolic link or a missing file is refused, and the number of skipped lines is reported. Lines from `journalctl` without `-o cat` are also accepted (the prefix before the JSON is ignored). The trace is a local record, like the log itself: confirm anything important on the native account.

## Provider rate limits and cooldowns (issue #11)

Owner-approved 2026-10-08 (decisions D11–D16). **The rule this does not change:** a claimed post is never retried, resent or released automatically. A 429 can only make ReachWeaver wait longer before trying a *different* post of the same account.

The providers' own published limits (Bluesky, Telegram, Mastodon, LinkedIn), with sources and access dates, are in [PLATFORMS.md](PLATFORMS.md#provider-rate-limits-issue-11). They are reference data and do not change the behaviour below.

**What happens on a 429.**

| Where the 429 came from | The post itself | Other posts of the account |
| --- | --- | --- |
| Identity preflight, before the claim (the read-only GETs below, Bluesky `createSession`, Postiz `GET /integrations`) | `BLOCKED ... no post sent`; stays approved/scheduled; no claim | cooldown recorded (`stage=preflight`) |
| The content create, after the claim | `REVIEW ... http_429 [class]`; `needs_review`; never resent | cooldown recorded (`stage=send`, with the class) |

While a cooldown is active, `publish --live` prints `SKIPPED POST_ID: account_provider_cooldown_active until=<UTC> account=<id> source=<label>; not claimed, post stays approved, no request sent` for each other post of that account, before the identity preflight and before any claim. `claim_sending` refuses the same posts inside its lock as a second line of defence. Other accounts are unaffected. The dry run (`publish` without `--live`) lists every active cooldown and marks affected due posts `SKIPPED`.

**Shared Postiz workspace (owner-approved 2026-10-09).** Postiz-backed accounts that declare the same `postiz_workspace` in `accounts.json` share one cooldown: a 429 on one records `scope=postiz-workspace:<slug>`, and each other member is skipped with `... scope=postiz-workspace:<slug> recorded_by=<account>`. This happens in the dry run, in the pre-claim gate, and inside `claim_sending` under its lock. Every `publish --live` run records the loaded accounts' declared workspaces in the `cooldown_scope_members` table. The dry run reads that table and loads no accounts, so it shows the grouping as of the last live run. Without the field, nothing changes. See [POSTIZ.md](POSTIZ.md) and the proposal.

**Bluesky `createSession` budget.** Bluesky limits `createSession` to 30 per 5 minutes and 300 per day per account ([Rate Limits](https://bsky.network/docs/rate-limits), read 2026-10-09). ReachWeaver calls it twice per live publish (identity preflight, then inside the send) and once per `connect-check --online`. Before any of those requests it reserves the calls in a local counter and refuses when a run would exceed **half** of either limit (15 per 5 minutes, 150 per day). The worker prints `SKIPPED POST_ID: bluesky_create_session_budget_exhausted account=<id> window=300s used=N needed=2 local_limit=15; no createSession request sent; not claimed, post stays approved`, logs reason `session_budget`, and writes a `bluesky_create_session_refused` event. A failed session still counts. Two `accounts.json` entries for the same handle on the same service share one counter. `health` shows `rate_limits.create_session_budget` per direct Bluesky account, and the dry run previews refusals. There is no reset command: wait for the window to pass.

**How long.** The deadline comes from the provider when it says, otherwise from a default, and is always clamped to **60 seconds – 24 hours**:

| Source label | Used for |
| --- | --- |
| `retry_after` | a standard `Retry-After` header (delay-seconds or an HTTP-date, RFC 9110 §10.2.3), any provider |
| `telegram_retry_after` | Telegram `parameters.retry_after`, read from at most 4 KB of the error body |
| `x_ratelimit_reset` | Mastodon `X-RateLimit-Reset` (ISO 8601) when there is no `Retry-After` |
| `daily_reset` | direct LinkedIn: the next 00:00 UTC (LinkedIn documents a daily reset and no header) |
| `default` | anything else, including Bluesky (its `ratelimit-reset` unit is not verified) and Postiz: 15 minutes |

A value above 24 hours is clamped and `health` raises `provider_cooldown_ceiling_clamped`, so a hostile or buggy header cannot silence an account for weeks unnoticed. A value that is not a number or a future date is ignored and the default applies. Cooldowns only extend: the latest recorded deadline wins. Nothing from a header or body is stored or printed except the computed UTC deadline and its source label.

**Seeing them.** `health` reports `provider_cooldown_active` (attention, exit 0) per account, with a `provider_cooldown` block and a top-level `provider_cooldowns` list. `queue-health` lists `provider_cooldowns` and sets `attention_required`. Cooldowns are durable `provider_cooldown` events in `event_log`, so they survive a restart; `data-purge` keeps any such event younger than 48 hours.

**Ending one early.** There is no command for this, on purpose. Wait for the deadline. If a cooldown is clearly wrong (for example a clock jump), `pause` first, then fix the host clock; do not delete audit events.

**Preflight backoff.** Before the claim, a read-only identity GET (Mastodon `verify_credentials`, Telegram `getMe`/`getChat`/`getChatMember`, LinkedIn `userinfo`, Postiz `GET /integrations`) that fails with 429, 500, 502, 503, 504 or a network error is retried **at most twice** with "full jitter" (a random wait up to 2 s, then up to 4 s; never more than 30 s). A server-supplied delay above 30 s means no retry: the cooldown is recorded and the post is `BLOCKED`. The emergency pause is re-checked before every retry. Bluesky `createSession` is a `POST` that spends its own 30-per-5-minutes budget and is **never** retried automatically. Set `"preflight_max_retries": 0` for a provider in `caps.json` to turn retries off. With `publish --log-json`, each retry writes a `preflight_retry` line and `run_end` counts them (`counts.preflight_retries`); see [structured worker logs](#structured-worker-logs-issue-26). The jitter alone adds at most 6 seconds per post; when the server asks for a delay of up to 30 seconds, that delay is waited instead, so the worst case is about 60 seconds per post.

### How a failure is classified

After the claim, every failure still goes to `needs_review`. ReachWeaver adds one label to the audit event, used only for the cooldown event and `dead-letter` guidance:

| Class | Meaning | Example |
| --- | --- | --- |
| `certain_not_sent` (C1) | local control flow shows the create request was never handed to the network | Bluesky `createSession` failing inside the send; LinkedIn offline preflight; a missing credential |
| `likely_not_created` (C2) | a 429 on the create call from an adapter whose provider documents (or whose server source shows) that the limit is enforced before the create runs: Bluesky `createRecord`, Mastodon `POST /api/v1/statuses`, Telegram `sendMessage` | `http_429` after `createRecord` |
| `ambiguous` (C3) | anything else after the write started: timeouts, 5xx, 401/403/426 on create, malformed receipts, unexpected errors, and LinkedIn and Postiz 429s (neither documents what a 429'd create did) | `linkedin_http_429_uncertain`, `postiz:postiz_http_500` |

Sources are in the proposal, section 2.2. C2 is evidence-backed, not an API guarantee: proxies, forks and self-hosted servers can differ. Reconcile every class by checking the native account.

## Account hold after an unresolved send (issue #41, draft for owner review)

When a send ends in an unknown state (`needs_review`), the account's real state is not known: the post may or may not be live. Until a person has looked at the native account and run `reconcile` on that post, **no further post is claimed for the same account**. A `sending` post left behind for more than 15 minutes (a crash, a lost worker) holds the account the same way; a `sending` post newer than that is a live send and does not.

- `publish --live` prints `SKIPPED POST_ID: account_unresolved_send_hold: post OTHER on account A is needs_review ...; not claimed, post stays approved, no request sent`, before the identity preflight and again inside the claim lock. With `--post-id` the exit code is 2. The dry run says a live run would skip it.
- The hold is derived from the posts table, so there is nothing to forget and nothing to edit, and **no flag or command lifts it except resolving the post that causes it**: `reconcile POST --published ...` after checking the account, or `reconcile POST --not-published` (for a stale `sending` post, with `--force` only after checking that no send is in flight).
- It applies per account. Other accounts, drafts, cancelled and published posts do not hold anything. Pre-existing databases need no migration.
- Why: the rolling cap and minimum gap only slow the next send down; an ambiguous send could otherwise be followed within the hour by a second post, or by a resend of the same text from a copy.

## Per-account caps file (tighten-only)

`config/caps.json` (ignored by version control; start from `config/caps.example.json`, or point `$REACHWEAVER_CAPS` at a file) sets per-account send-attempt limits for `publish --live`:

~~~json
{"version": 2,
 "defaults": {"max_per_24h": 2, "min_gap_minutes": 60},
 "providers": {"linkedin": {"max_per_24h": 1, "cooldown_floor_seconds": 3600},
               "telegram": {"preflight_max_retries": 0}},
 "accounts": {"my-linkedin": {"max_per_24h": 1, "min_gap_minutes": 240}}}
~~~

For each post the effective budget is the **strictest** of the CLI flag, the file `defaults`, the matching `providers` entries and the account entry: the lowest `max_per_24h` and the longest `min_gap_minutes`. A file value looser than the CLI is ignored, so the file can only tighten limits. Values must be within the CLI ranges (1–10 and 15–1440). A malformed file stops `publish` before anything is sent. The numbers go into the unchanged `claim_sending` budget check. `health` shows the effective values.

**Version 2 (issue #11).** The optional `providers` block is keyed by `bluesky`, `mastodon`, `telegram`, `linkedin` or `postiz`. A direct account uses its platform's entry; a Postiz-backed account uses both `postiz` and its platform's entry. Besides `max_per_24h`/`min_gap_minutes`, a provider entry may set:

- `cooldown_floor_seconds` (60–86400): the shortest cooldown after a 429. The built-in floor is 60; the effective floor is the **largest** value. The 24-hour ceiling cannot be changed.
- `preflight_max_retries` (0–2): read-only preflight retries. The built-in value is 2; the effective value is the **smallest**. Bluesky is always 0.

`providers` is refused in a version 1 file, and these two keys are refused outside `providers`. **Version 1 files stay valid unchanged.** Rollback: code older than this release rejects a version 2 file, so `publish --live` refuses to run; restore a version 1 file before downgrading. This caps file sets no dollar ceiling (spend caps for paid tiers are issue #24).

## Spend ceilings are a precondition for any paid-API adapter

ReachWeaver charges nothing and meters no money. Today no ReachWeaver send can spend money: every platform with a live or mock-tested adapter documents no posting fee in the capability registry, YouTube is limited by quota rather than price, and the only platform whose registry entry lists a per-request price, **X** (pay-per-use credits per post created, higher with a URL; prices subject to change), has no ReachWeaver adapter. The Postiz backend refuses X for the same reason. What limits sending today is the attempt budget above, the minimum gap, provider session budgets and 429 cooldowns.

Owner decision of 2026-10-21: no monetary ceiling is built while no paid adapter exists, because it would guard nothing. Any future adapter for a paid API, such as X, must ship together with these rules, each reviewed as its own decision:

1. The owner first sets the provider's own maximum spend per billing cycle in its developer console. ReachWeaver cannot check this, so the adapter proposal records it as the owner's statement.
2. `caps.json` gains a tighten-only `max_spend_usd_per_30d` per provider. Before the claim, ReachWeaver estimates `attempts in the last 30 days × the registry price for this kind of request` and refuses with `spend_ceiling_reached` when the next attempt would go over it. Counting attempts rather than confirmed posts over-counts rather than under-counts.
3. A registry price older than the staleness window refuses live sends to that provider until the price is checked again.
4. The cost shown by `show` and the dashboard stays labelled as an estimate, never as an invoice.

## Runbook: revoked permissions and API version sunsets

**Symptoms.**

- `connect-check --online` or the pre-claim identity check fails with `http_401` or `http_403`. `publish --live` prints `BLOCKED ... no post sent`, and the post stays `approved` with no send claim.
- A post fails after its claim with `http_401`, `http_403` or (LinkedIn) `linkedin_http_426_uncertain`. It is in `needs_review`, and `dead-letter` groups it under that code.
- `health` reports `linkedin_version_ageing` (10–11 months), `linkedin_version_unsupported` (12+ months), `linkedin_authorization_expired` or `_warning`.
- A Mastodon instance changed its character limit, moved domain or is shutting down. `connect-check --online` reports that the pinned `max_characters` now exceeds the instance value, or the identity check fails.

**Steps for a revoked or expired permission.**

1. `reachweaver pause`, and stop the worker timer if one is installed.
2. `reachweaver dead-letter`. Reconcile every listed post against the native account **before** anything else: a 401 or 403 after a claim does not prove the post was not created.
3. At the provider, find out what changed: an app password deleted, an OAuth grant revoked or expired (LinkedIn tokens currently last 60 days), a bot removed from a channel or its admin rights reduced, or a Postiz integration disconnected (`postiz-check --account ID --online`). See [revoking tokens at each provider](ACCOUNT-ONBOARDING.md#revoking-tokens-at-each-provider).
4. If the revocation was deliberate (for example a suspected leak), follow [disconnect](ACCOUNT-ONBOARDING.md#3-disconnect) and stop here.
5. Otherwise re-authorize with the least privilege needed, store the new credential through your secret manager ([provisioning](ACCOUNT-ONBOARDING.md#1b-provision-the-credential-without-exposing-it)), and for LinkedIn update `authorization_expires_at`.
6. `reachweaver credentials-audit`, then `reachweaver connect-check --account ID --online`, then `reachweaver health`.
7. `reachweaver resume` only when `health` no longer reports `blocked` for that account. Supervise the next post with `publish --live --post-id`.

**Steps for a LinkedIn version sunset.** LinkedIn supports each monthly version for at least one year and answers an inactive version with HTTP 426 ([versioning](https://learn.microsoft.com/en-us/linkedin/marketing/versioning)). ReachWeaver warns from month 10 and refuses the live send from month 12, before the claim.

1. When `health` or `doctor` shows `linkedin_version_ageing`, read the LinkedIn changelog for breaking changes to the Posts API between the pinned version and the current one.
2. If the request or response shape changed, the adapter needs a code change and review: the mock tests encode the documented shape and cannot detect a provider change. Otherwise update `linkedin_version` in `config/accounts.json`.
3. `reachweaver health`, then `connect-check --account ID --online`, then one supervised `publish --live --post-id`.
4. If a 426 already put posts in `needs_review`, reconcile them first (a 426 normally means nothing was created, but check the profile).

**Steps for a Mastodon instance change.**

1. Limit change: run `connect-check --account ID --online`. It reads the public `/api/v2/instance` limits. Lower or pin `max_characters` in the account config to the reported value and re-run `check POST_ID` on queued drafts.
2. Domain move or account migration to another instance: tokens are per instance. `pause`, reconcile `needs_review` posts on the **old** instance, create a token on the new instance, update `server` and `handle`, run `connect-check --online` and `health`, then `resume`.
3. Instance shutting down: treat it as a disconnect. Cancel queued posts for that account (`cancel POST_ID`), remove the account and unset its variables.

**Pick the right provider reference.**

| Provider | Token lifetime and expiry | Version or API change | Where to look |
|---|---|---|---|
| LinkedIn | about 60 days; `authorization_expires_at` | monthly versions, HTTP 426 after at least a year | `health` shows `linkedin_version_*` and `linkedin_authorization_*` |
| Instagram, Facebook, Threads (Meta) | long-lived tokens about 60 days; `authorization_expires_at` (Instagram, Threads); `health` shows `instagram_authorization_*` and `threads_authorization_*` 7 days ahead | pinned Graph API version (`v25.0` in the Instagram adapter); Meta retires old versions and some metrics (for example `post_impressions` from v25) | adapters are disabled until a live receipt; re-read the Meta changelog before enabling |
| YouTube, TikTok | OAuth grants can be revoked in the account's security page; no refresh flow exists here | quotas and audit status change; the registry video switches stay `off` | re-run the sourced-facts review in the adapter's proposal before enabling |
| Bluesky, Mastodon, Telegram, Postiz | app passwords, tokens and keys are revoked by deleting them | instance limits and domains change | `connect-check --online`, `postiz-check --online` |

These are the repository's own notes, not a promise about any provider's current policy; verify each date at the provider before relying on it.

The `reachweaver_needs_review_credential_posts` metric (see the [metrics table](#operational-metrics-and-alerting-issue-26)) counts posts already in `needs_review` with a 401/403 or 426 code, so the symptoms above can page you. It reads the local database only. After a revocation or sunset, the [privacy lifecycle](PRIVACY-LIFECYCLE.md#backup-recovery-and-disconnect) covers disconnecting the account and purging data.

Record each incident in your own notes with the post ids and the native URLs you used to reconcile.

## Example systemd integration — never installed by the repository

The [service](../deploy/systemd/reachweaver-publish.service.example) and [timer](../deploy/systemd/reachweaver-publish.timer.example) are disabled/inert template files, not deployed. The service uses a separate owner-created /etc/reachweaver/ALLOW_LIVE gate, an unprivileged user, private local state and scheduled-only lateness guard. It does not install account credentials, create a secret file, set REACHWEAVER_ALLOW_LIVE or REACHWEAVER_ENV, or enable anything. Its comments show the recommended systemd `LoadCredential=` lines (credentials read from `$CREDENTIALS_DIRECTORY`, never from the unit's environment) and the production-host-only `Environment=REACHWEAVER_ENV=prod` line; see [account onboarding](ACCOUNT-ONBOARDING.md#unattended-host-systemd-loadcredential).

1. On the trusted owner machine, create a dedicated non-login service user, private SQLite state directory (0700; DB 0600), private account metadata and least-privilege provider authorization.
2. Complete [the first supervised live pilot](FIRST-LIVE-PILOT.md) with an exact reviewed test post, identity check and independently observed remote URL, including failure/reconciliation practice.
3. Inspect paths and privileges in example units; install and test with the service command modified to a **dry run without --live** first. Provide credentials with `LoadCredential=` from root-owned 0600 files (preferred), or a secure local secrets manager; do not commit or expose keys in systemd files or GitHub. The dry run's `Live gate:` line must show `credential source credentials_directory`.
4. Only after the owner separately accepts recurring publishing, configure explicit live opt-in in the service environment (`REACHWEAVER_ALLOW_LIVE=1` and, on this production host only, `REACHWEAVER_ENV=prod`), create the manual ALLOW_LIVE marker and activate the timer.
5. During a provider outage, remove the marker or stop the timer, use reachweaver pause and queue-health. Inspect the account for all sending/needs_review IDs and reconcile with real evidence, **never automatically resending** a possibly successful write.

A timer on the owner's own persistent host (not ephemeral GitHub Actions) can run one guarded due sweep. The repository cannot remotely install or enable it. If the host was offline, posts late beyond the configured window stay scheduled until the owner reviews, cancels or reschedules them.

## Failure-mode contract

| Condition | Effect |
| --- | --- |
| Unapproved or wrong account | Cannot publish |
| Approved before the #7 upgrade, or changed after approval | BLOCKED before the identity preflight and in the claim; stays approved/scheduled; health `attention` until re-approved |
| Unscheduled in --scheduled-only mode | Skipped |
| Missed local scheduled time by more than allowed lateness | Skipped; manual attention |
| Account attempt limit/gap violated | Atomic claim denied before social POST |
| Caps file stricter than CLI | Stricter value used; claim denied before social POST |
| Caps file looser than CLI or malformed | Looser value ignored; malformed file stops the run before any send |
| Global emergency pause | Claim denied |
| Process dies after claim | sending persists; queue-health flags staleness |
| Remote timeout or uncertain write | needs_review; never blind retry |
| Provider 429 on the create, after the claim | needs_review (classified C1/C2/C3 for guidance); never resent; account cooldown recorded |
| Provider 429 on the read-only preflight | BLOCKED before the claim; stays approved/scheduled; account cooldown recorded |
| Account in a provider cooldown | Other unclaimed posts SKIPPED before the preflight and refused in the claim; stay approved/scheduled |
| Another post of the account is `needs_review`, or `sending` for over 15 minutes (#41) | `account_unresolved_send_hold`: later posts SKIPPED before the preflight and refused in the claim; stay approved/scheduled until a person runs `reconcile` on that post |
| Transient read-only preflight failure (5xx, network) | At most 2 jittered retries before the claim (never Bluesky); then BLOCKED |
| caps.json version 2 read by older code | Rejected; live run refuses to start |
| Reconciled as not published | Attempt remains counted in rolling quota |
| Worker restarted | SQLite attempt history remains |
| Unsupported platform or credentials | Skipped; no fake post receipt |

## Not solved yet

Provider 429 cooldowns, preflight backoff and tighten-only provider budgets exist (above) but are mock-tested only: no provider's real 429 headers have been observed by this repository. ReachWeaver still has no spend management, no complete remote receipt monitor, no cross-host leases and no production-grade worker with alerts and recovery. Issue #11 stays open. The next slices need approved external smoke testing, endpoint-specific cost ceilings and alerting checked against a live Prometheus (`ops-metrics` and the [example alert rules](#example-alert-rules-examples-only-untested-against-a-live-prometheus) exist but are untested there). The sourced Bluesky and Telegram rate-limit figures from the proposal should be written back into the capability registry in a separate change. Use [calendar planner](CALENDAR-PLANNER.md) for audience-timezone proposals and [queue architecture](ARCHITECTURE.md) for state transitions.

CI tests local behavior across Python 3.11–3.14 and checks the standalone Claude Code runtime mirror. No CI job can publish.
