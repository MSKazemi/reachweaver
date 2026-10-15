# Local data lifecycle: export, retention and redaction (issue #26 slice)

> **Not legal advice.** This page describes what the software does and offers engineering notes that may help an operator meet obligations such as the EU GDPR. Whether and how a law applies depends on your situation; consult a qualified data-protection professional.

Everything below is **offline**. No command on this page contacts a social platform, sends a post, or changes the approval, dry-run, emergency-stop, credential or ambiguous-send rules.

## What ReachWeaver stores locally

The SQLite state database (default `state/reachweaver.sqlite`, mode 0600 in a 0700 directory) holds:

| Table | Contents | Personal data risk |
| --- | --- | --- |
| `posts` | draft/approved/published text (including operator-authored outgoing reply drafts where applicable), reply-target identifier, optional image/video metadata, account id, platform, campaign, source URL, remote id/URL, timestamps, last error code, approval payload hash, approver label (`approved_by`: an OS login or a typed name) and approval channel | Post text may name or quote people; unpublished drafts are confidential; `approved_by` is personal data and appears in `data-export` |
| `event_log` | state-transition audit (action, post id, short detail) | Low; details hold ids, counts and error codes |
| `campaign_variants` | campaign/variant ids, fingerprint, staged evidence JSON | Evidence claims may include private plans |
| `metric_snapshots` | per-post aggregate counts from native exports | Aggregates, normally not about an identifiable person |
| `settings` | emergency pause flag | None |

ReachWeaver does not currently maintain a database of fetched incoming comments, follower lists or third-party profiles. **Outgoing replies authored or approved by the operator can be stored as posts**, including reply targets. `engagement-triage` reads an operator-provided export and writes draft files outside the database; imported and exported files may include other people's data. The [opt-out list](#opt-out-list) is a separate local file. Apply appropriate access and retention controls to imported data and draft files.

## Commands

```bash
reachweaver privacy-status                       # perms, size, row counts, oldest records, policy
reachweaver data-export --out state/export.json  # NEW 0600 file in the ignored private dir; refuses to overwrite
reachweaver data-export                          # same JSON on stdout
cp config/retention.example.json config/retention.json   # edit the days
reachweaver data-purge                           # DRY RUN: shows planned counts and ids
reachweaver data-purge --apply                   # applies the plan to local rows only
```

`data-purge` and `privacy-status` read the policy from `--policy FILE`, else `$REACHWEAVER_RETENTION_POLICY`, else `config/retention.json` if it exists. A missing key or `null` means "keep forever".

```json
{"version": 1, "retention_days": {
  "metric_snapshots_days": 400, "event_log_days": 365, "cancelled_drafts_days": 30,
  "published_content_days": 180, "campaign_evidence_days": 365}}
```

### Hard rules a policy cannot override

- Posts in `draft`, `approved`, `scheduled`, `sending` or `needs_review` are never altered or deleted.
- `send_claimed` audit events newer than 48 hours (the rolling send budget window plus margin) and all events of `sending`/`needs_review` posts are kept. So are `provider_cooldown` events newer than 48 hours (a cooldown after a provider 429 lasts at most 24 hours, issue #11); they hold an account id, a UTC deadline and fixed codes only.
- Published posts keep id, account, platform, `remote_id`, `remote_url`, status and timestamps, so a duplicate send stays detectable; only `content` becomes `[purged]`.
- `campaign_variants` rows and fingerprints are never deleted (they make staging idempotent); only `evidence_json` is minimized to evidence ids. Cancelled posts that a variant references, or that had a send attempt, are content-minimized instead of deleted.
- Apply re-checks these rules in SQL inside one transaction, so an edited or stale plan cannot widen the purge. One `privacy_purge` event with counts only (no content) is logged.

### Export format

`export_data` returns `{"format": "reachweaver-local-export", "schema_version": 1, "exported_at", "counts", "tables": {posts, events, campaign_variants, metric_snapshots, settings}}`. Secret-looking substrings (tokens, app passwords, `password=…`, values of secret env vars) are replaced with `[REDACTED…]`. The export still contains private drafts: store it like the database.

### Redacted structured logs

`reachweaver.privacy.structured_log(event, **fields)` writes one JSON line with a UTC `ts`. Every string value, nested ones included, passes through `redact()` (emails too, by default). Use it for any new operational logging rather than `print` of raw API data. Pattern-based redaction lowers the risk of a leak; it does not rule one out.

## Opt-out list

`config/optout.json` lists people, accounts or whole domains that ReachWeaver must never reply to, mention or tag. The file is ignored by version control; start from `config/optout.example.json`. It is checked offline:

| Where | Effect |
| --- | --- |
| `engagement-triage` | A listed author gets action `optout_listed_no_contact`: no reply draft and no issue draft |
| `campaign-check`, `claims-check`, `campaign-preview`, `campaign-stage`, `campaign-export` | An @-mention of a listed handle or domain is an `optout_mention` **error**, so the variant cannot be staged or exported |
| `draft` | A draft that @-mentions a listed handle or domain is **refused**; no post row is created |
| `check POST_ID` | The mention is reported as `BLOCKED: ... optout_mention ...` (exit 1) |
| `approve POST_ID` | Approval is **refused** with the mention named; the post stays `draft` |
| `publish` (dry run) | Each due post that mentions a listed entry is shown as `BLOCKED ...: optout_mention: N @-mention(s)` |
| `publish --live` | A **pre-claim gate** in both the direct-adapter loop and the Postiz `run_live_send` path: the post is printed as `BLOCKED`, is never claimed, makes no network request and stays `approved` |

Every command reads `--optout FILE`, else `$REACHWEAVER_OPTOUT`, else `config/optout.json` if it exists. With no file, nothing is filtered. A named file that is missing, unreadable or malformed is an error: the command stops instead of running with a partial list. `publish` loads the list once per run, before the first claim, so a malformed file aborts the whole run (dry run included) before any post is claimed or any request is sent.

The publish-time check catches posts that were drafted or approved **before** the person opted out. It only ever withholds a send: it runs before the identity preflight, the durable claim and any request, so a blocked post is left `approved` with no `send_claimed` event, exactly like the other pre-claim gates. Publish output names only the number of matching mentions, never the handle, because worker logs may be kept; run `reachweaver check POST_ID --optout FILE` to see which mention to remove. A blocked post stays in the due queue until you edit it into a new draft or cancel it.

```json
{"version": 1, "entries": [
  {"handle": "someone.example.social", "platform": "bluesky", "recorded_on": "2026-10-08"},
  {"handle": "someone@mastodon.example", "recorded_on": "2026-10-08"},
  {"domain": "example.org", "recorded_on": "2026-10-08"}]}
```

Each entry has exactly one of `handle` or `domain`, plus optional `platform` and `recorded_on` (`YYYY-MM-DD`). Other keys are rejected, so the list cannot accumulate reasons, messages or other personal data. Matching is conservative, because a false match only costs a draft:

- Handles are compared case-insensitively, with or without a leading `@`.
- A bare `name` also matches `name@any.instance`, and `name@instance` also matches a bare `@name` mention.
- A `domain` matches the domain itself, any sub-domain handle (Bluesky domain handles such as `alice.example.org`) and every `user@example.org` (a whole Mastodon instance).
- An entry with `platform` applies only to that platform; without one it applies everywhere.
- E-mail addresses and URL paths are not treated as mentions.

**How a person asks to opt out.** Any of these is enough; no form or justification is required:

- a reply or comment such as "stop tagging me", "don't contact me", "unsubscribe" or "opt out" under one of your posts. `engagement-triage` detects these phrases and lists the item ids in the report's `optout.requests_to_record`;
- a direct message or e-mail to the owner;
- a GitHub issue or discussion on the project. The handle alone is enough; they should not post other personal data there.

**How the owner records it.**

1. Confirm the request comes from the account it names (for example, the handle posted it).
2. Add one entry to `config/optout.json`: the handle (or the domain, if they ask for a whole organization or instance), the platform when the request is platform-specific, and today's date as `recorded_on`.
3. Run `reachweaver claims-check CAMPAIGN --brand BRAND` on every campaign in progress and remove the mentions it flags. Run `reachweaver publish` (dry run) to list approved posts that would now be `BLOCKED`, then cancel them (`reachweaver cancel POST_ID`) and draft corrected text. `draft`, `approve` and `publish --live` re-check the list, so a missed post is withheld rather than sent.
4. Do not reply to confirm unless they asked for a confirmation. Keep the entry until they ask to be removed from the list.

The list is personal data about the people on it. Keep it local, back it up with the database, and never publish it. Under the GDPR a person can object to processing for direct-marketing purposes, after which that processing must stop (Art. 21(2)-(3)); a do-not-contact list is one way to honour that. This is an engineering note, not legal advice.

## GDPR-aware notes for EU operators

These are engineering notes, not compliance determinations.

- **Who is the controller.** You run ReachWeaver on your own host for your own accounts; no data is sent to the maintainers. Purely personal or household activity is outside the GDPR (Art. 2(2)(c)). Promoting an organization or business usually is not.
- **Data minimization and storage limitation (Art. 5(1)(c), 5(1)(e)).** Configure a retention policy and run `data-purge` periodically. Do not paste other people's personal data into drafts, evidence or campaign files unless you need it. Published-content redaction keeps only what duplicate-send protection needs.
- **Separate published material from private analytics.** Published post text is already public on the platform; aggregate metrics and unpublished drafts are private. Retention keys are per content type so you can, for example, keep metrics for a year and redact published text after six months. If you ever store third-party comments or replies, put them in a separate store with their own retention, never in `posts` or `metric_snapshots`.
- **Access and portability (Art. 15, Art. 20).** `data-export` gives a machine-readable copy of everything held locally. For a request from a third party (for example someone quoted in a post or, in future, a commenter), filter the export to records about that person before disclosing it; do not hand over your whole database.
- **Erasure (Art. 17).** For a third party's data, edit or cancel affected drafts and let `data-purge` remove cancelled posts, or redact published content. For published posts, deleting the **remote** post is a separate manual action on the platform; ReachWeaver does not delete remote posts. Backups, filesystem snapshots, old exports and the platforms' own copies are out of scope for `data-purge`: handle them separately.
- **Security of processing (Art. 32).** Keep the 0600/0700 permissions that `privacy-status` checks, use full-disk encryption, and keep credentials in environment variables or a secret manager only.
- **Records and breaches (Art. 30, Art. 33).** `privacy-status` output and `privacy_purge` events can support your records. If a token or the database leaks, run `reachweaver pause`, revoke the credentials at the provider, and assess whether the 72-hour supervisory-authority notification applies.

## Backup, recovery and disconnect

- **Backup:** stop any worker, `reachweaver pause`, then copy **both** the DB file and `approval-ledger.jsonl` from the same directory, taken together, with their permissions preserved (`cp -p`), or use `sqlite3 state/reachweaver.sqlite ".backup state/backup.sqlite"` plus a copy of the ledger. The ledger records which approvals were already used; a database restored without its matching ledger either refuses to send or forgets a consumed approval (see Recovery). Backups hold the same private data (post text, handles, opt-out entries); apply the same retention by hand.
- **Recovery:** restore the database and its ledger **together** into a 0700 directory, both mode 0600, then run `privacy-status` and `queue-health`. Reconcile every `sending`/`needs_review` post against the platform before resuming. If the ledger is missing, unreadable, symlinked, wrongly permissioned or chain-broken, nothing is claimed or approved and the error is `approval_ledger_unavailable`: restore the file from the backup taken with the database. A database restored from an older backup than the ledger makes an approval that was already sent look unused: it is refused with `approval_replay_reapproval_required`. Check the account for the native post first, then `unapprove` and `approve` again if you really want a second send. Never create an empty ledger by hand before checking the account for duplicates: it forgets every earlier consumption ([details](WORKER-OPERATIONS.md#upgrade-note-approval-nonce-and-replay-ledger-wave-9-item-1)).
- **Permission revoked or API version sunset:** the operational steps (pause, reconcile `needs_review` posts first, re-authorize or update the pinned version) are in the [runbook](WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets). A revoked token does not delete local data or remote posts; if you will not reconnect, follow the disconnect step below and then export or purge.
- **Disconnect an account:** `reachweaver pause`, `reachweaver disconnect --account ID` (draft: stops this installation publishing to it; `reconnect` is terminal-only), revoke the app password or token at the provider, unset the env vars, remove the account from `config/accounts.json`, then export or purge as policy requires.

## Incident response (operator procedure)

This is the order to work in when a token, the database or an approval key may have leaked, an unexpected post went out, or the account was used by someone else. It complements the short list in [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md#c-when-something-goes-wrong). The timings are working targets for an operator, not legal deadlines.

**Roles.** ReachWeaver has one operator: the account owner, who approves every send. On a team, name an **incident owner** and a **deputy** before anything goes wrong; only they run `pause`, `resume` and `reconcile`, and the credential holder revokes at the provider.

| When | Step | Command or action |
|---|---|---|
| Immediately | Stop every new send. | `reachweaver pause` (blocks every new claim). Stop any worker or timer. |
| Within the hour | Cut off the credential or key. | Revoke the token or app password at the provider ([per provider](ACCOUNT-ONBOARDING.md#revoking-tokens-at-each-provider)); for a leaked approval key, replace `REACHWEAVER_APPROVAL_SECRET` everywhere and re-approve every queued post. Then unset the variable and rotate. |
| Within the hour | Preserve evidence before changing state. | Copy the database and `approval-ledger.jsonl` together (see Backup above); keep `reachweaver dead-letter` and `reachweaver queue-health` output. Never paste a token, app password or the database into a chat, issue or screenshot. |
| Same day | Find out what really went out. | `reachweaver queue-health` and `reachweaver dead-letter` list `needs_review` and stale `sending` posts; `reachweaver report` lists what is recorded as published. Open the account in the native app and compare, one post at a time. |
| Same day | Make the record match reality. | Delete an unexpected post in the native app if needed, then `reachweaver reconcile POST_ID --published` or `--not-published`. Never re-send blindly. |
| Same day | Assess exposure. | Run `reachweaver privacy-status`. If personal data of other people (for example the opt-out list or drafts quoting someone) may have been exposed, assess your notification duties with qualified advice; the GDPR notes above give the 72-hour supervisory-authority rule where it applies. |
| After the cause is known | Recover and resume. | Re-authorize with a fresh credential, run `reachweaver doctor` and `reachweaver health`, re-approve what you still want to send, then `reachweaver resume`. |
| Afterwards | Write it down privately. | What happened, when it was noticed, what was exposed, what was revoked, what changed. A vulnerability in ReachWeaver itself is reported privately ([SECURITY.md](SECURITY.md#reporting-a-vulnerability-private)), never in a public issue. |

## What remains open in issue #26

Done so far: redaction, structured logs, local export, configurable retention, an offline privacy status, the offline `health` command with its read-only dead-letter view, the opt-out list (with re-checks at `draft`, `check`, `approve` and a pre-claim `publish` gate), a tighten-only per-account caps file, and the [revoked-permission and API-sunset runbook](WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets). The dependency-compromise and poisoned-feed security tests, once listed here as open, exist: `tests/test_security_regressions.py` (`PoisonedFeedTests`, `PoisonedFeedPipelineTests`) and `tests/test_dependency_policy.py` (`RuntimeDependencyPolicyTest`, `SupplyChainGuardTest`). Still open: distributed traces and retry metrics, platform incident alerts, and per-provider/API **spend** budgets (the caps file limits send attempts, not money).
