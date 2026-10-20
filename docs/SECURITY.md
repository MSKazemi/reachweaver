# Security and trust policy

## Reporting a vulnerability (private)

Please **do not** open a public issue, discussion or pull request for a vulnerability, and never paste credentials, tokens, cookies, session files or exploit details publicly.

1. Report privately by e-mail to **mohsen.seyedkazemi@gmail.com** with `ReachWeaver security` in the subject. Describe the version and a safe reproduction, without sharing live credentials; revoke exposed tokens at their provider first.
2. If [GitHub private vulnerability reporting](https://github.com/MSKazemi/reachweaver/security/advisories/new) is enabled and visible, that private channel can be used instead. **Do not assume the form is active** solely because the link exists.
3. If neither private channel is usable, submit a public issue saying only `requesting a private security contact`, with no incident details. See [root SECURITY.md](../SECURITY.md).

In scope: credential or token exposure (in output, logs, audit rows, errors or redirects, or through `$CREDENTIALS_DIRECTORY` file handling), any bypass of the human-approval gate, the dry-run default, the live gate (`--live` + `REACHWEAVER_ALLOW_LIVE=1` + `REACHWEAVER_ENV=prod`) or the emergency `pause`, two accounts reading the same credential despite the `env_prefix` overlap rule, automatic retry of an ambiguous send, unintended network access from offline commands or tests, and SSRF/redirect issues in platform clients. Include the affected version or commit, an offline reproduction where possible, and the impact. If you leaked a real credential while testing, **revoke it at the provider first**.

Operators and maintainers: the short, actionable [security checklist](SECURITY-CHECKLIST.md) covers the first live send, each release and incident response.

Supported versions: the latest `main` branch. Fixes land there; there are no long-term support branches yet.

## Threat model

Assets: account authority, access tokens, authenticated sessions, unpublished research, marketing schedule, account reputation, analytics and private user interactions. Trust boundaries: AI model, local host, repository/CI, external webpages and social platform APIs.

The detailed STRIDE threat model covers token theft, prompt injection, wrong-account publishing, supply-chain integrity, redirects, registry poisoning, SQLite tampering, approval spoofing, replay and external backends. Internal working design-review details are maintained privately; the public policy below summarizes controls and remaining limitations.

### Controls implemented

1. No account secrets accepted in `config/accounts.json`: field name guard blocks token/password/session/cookie keys. Config is in `.gitignore`.
2. No file-based implicit `.env` loading; the human deployer controls the credential source. Credentials come from environment variables or, when `$CREDENTIALS_DIRECTORY` is set (systemd `LoadCredential=`, issue #5), only from files in that directory, through one accessor (`accounts.credential`). That accessor refuses a relative or group-/world-writable directory, symlinks (`O_NOFOLLOW`), FIFOs and other non-regular files, files over 64 KiB and non-UTF-8 content, never falls back to the environment while the directory is set, and names only the variable in errors. File values are redacted by `privacy.redact` exactly like secret environment variables. `tests/test_hardening.py` checks that no module in `reachweaver/` reads a credential any other way.
3. The default `publish` command never contacts a platform. Explicit `--live`, `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod` (issue #5, owner-approved 2026-10-09), approval and due timestamp are enforced. The three live opt-ins are checked by `policy.require_live_gate` before any account is loaded, any credential is read or any request is made, for native adapters and the Postiz backend. An unrecognized `REACHWEAVER_ENV` value is never echoed.
   Approval is bound to the exact payload (issue #7, owner-approved 2026-10-08). `approve` stores a SHA-256 over the canonical JSON of `account_id`, `platform`, byte-exact `content`, `scheduled_at`, `source_url` and `campaign`. `claim_sending` re-verifies it under its `BEGIN IMMEDIATE` lock before any budget or network use, and both live senders (CLI loop and Postiz) check it before the identity preflight. A changed post, or one approved before the upgrade (no grandfathering), fails closed: it stays `approved`/`scheduled`, nothing is claimed, and `health` lists it as `attention` until a human runs `unapprove`, `show`, `approve`. **Residual risk:** the hash is unkeyed, so a process that can write the DB can rewrite the content *and* recompute the hash. It detects unsanctioned mutation; it does not authenticate the approver, and `approved_by` is attribution only. A keyed HMAC or signed approval (decision D7) is still open. See threat model T7/T8 and SECURITY-REVIEW-PROPOSAL-7-11.md.
4. SQLite transactional claims prevent duplicate concurrent claims on the same state database.
5. Unknown send outcomes are non-retriable until humans reconcile the remote record.
6. Emergency pause prevents new claims. Audit log captures state transitions without API payloads or secrets.
7. HTTPS-only Mastodon domain origin validation limits accidental local-network targets.
8. External API errors are reduced to sanitized codes rather than printing provider responses or credential-bearing URLs.
9. Tests mock API requests; CI performs no social publishing and receives no account credentials. `tests/test_no_network.py` patches every socket entry point and fails if importing the package or running the offline CLI paths attempts a connection or DNS lookup.
10. CI runs `scripts/secret_scan.py` over every tracked file (private key blocks, AWS/GitHub/Slack/Telegram tokens, JWTs, Bluesky app-password shapes outside example files, literal password/token assignments). It prints `path:line:pattern-id` only, never the matched value. Synthetic test fixtures are suppressed only through reviewed, reason-bearing entries in `.secret-scan-allow`. It is pattern-based, so it is a backstop, not a guarantee.
11. The runtime is stdlib-only, so no third-party package runs with account tokens. `scripts/check_runtime_deps.py` (CI hygiene job, `make deps`, `tests/test_dependency_policy.py`) fails if `pyproject.toml` declares any runtime or optional dependency, statically or through `[project] dynamic`/`[tool.setuptools.dynamic]`, and its message requires a pinned `pip-audit` step and a lock file first. Development tools (ruff, mypy) run through `pipx run` at exact versions, outside the project environment. See ADR-0002.
12. CI runs with `permissions: contents: read`, receives no social credentials, and pins every third-party GitHub Action to a full commit SHA.
13. Recorded provider responses must be scrubbed before commit: no tokens or cookies, and synthetic DIDs, handles and user ids. Provider fixtures may not use `.secret-scan-allow` entries ([tests/fixtures/providers/README.md](../tests/fixtures/providers/README.md)).
14. `load_accounts` refuses identical or overlapping `env_prefix` values (issue #5): no two accounts may have credential variable names, over every adapter suffix and the Postiz `_POSTIZ_API_KEY` suffix, that are equal or where one is a string prefix of the other (`accounts.env_prefix_conflicts`). The error names the two account ids and variable names only.
15. The repository's `.claude/settings.json` denies Claude Code any Bash command that runs `reachweaver ... approve`, `publish --live`, or contains `REACHWEAVER_ALLOW_LIVE=1` or `REACHWEAVER_ENV=prod`. Since #7 (owner decision 7, 2026-10-09) it also denies `approval-mode ... --confirmation off`, any Bash command naming `REACHWEAVER_APPROVAL_SECRET`, and reading a `LoadCredential=` file of that name. It is a harness rule for this checkout, not a ReachWeaver control: it does not bind other agents, other checkouts or a human.
16. The Claude Code plugin carries a `PreToolUse` hook (`plugin/hooks/hooks.json`, script `reachweaver/agent_guard.py`, owner-reviewed 2026-10-20) that blocks, with exit code 2, any `Bash`, `PowerShell` or `Monitor` command that runs ReachWeaver (`reachweaver`, `run.py`, `python3 -m reachweaver[.cli]`) with `approve`, `account-kit --approve-plan`, `publish --live`, `resume`, `reconcile`, `reconnect` or `approval-mode --confirmation off`, or that sets `REACHWEAVER_ALLOW_LIVE`, sets `REACHWEAVER_ENV=prod` or names `REACHWEAVER_APPROVAL_SECRET`. It parses chains, wrappers, `bash -c`, `eval` and command substitution and refuses unreadable or inline-Python commands that name an owner-only action. Like item 15 it is defence in depth for a cooperative agent, not a boundary: a script file run later, an encoded PowerShell command or another language's subprocess call is not seen. The CLI refuses abbreviated long options (`allow_abbrev=False`), so a shortened `--liv` cannot slip past either rule.

### Approval expiry (issue #39, draft for owner review)

`approval-mode --max-age-hours N` (1 to 8760) turns on an approval time-to-live. It is **off by default**, so nothing changes until the owner sets it. When on, the claim, `schedule`, `calendar-plan --apply`, `health` and `show` all treat a post whose `approved_at` is older than N hours as `approval_expired_reapproval_required`: the post stays `approved` or `scheduled`, nothing is sent, no budget is used, one audit event is written, and the fix is the usual `unapprove`, `show`, `approve`. Tampering keeps its own reason code (the expiry is checked last). `schedule` refuses a due time that falls after the approval would lapse. The setting is tighten-only: a command accepts only a lower value; raising or clearing it is an edit of the `settings` row by the owner. An unreadable stored value fails closed (every approval counts as expired). Limits: the clock is the host clock, and the age runs from `approve` (a `schedule` keeps `approved_at`), so a post approved far ahead of a due time must be approved again closer to it.

### Not yet implemented

- OS keyring/KMS integrations, OAuth PKCE, rotation automation, organization RBAC, a keyed or signed approval binding and a human-presence check on `approve` (decisions D7 and D6), encrypted-at-rest DB, browser-mediated OAuth and immutable signed audit trails.
- Secret scanning of git history, untracked files and encoded/obfuscated values (the CI scanner covers tracked files only).
- A dependency vulnerability audit (`pip-audit`). It is not needed while the runtime has no third-party dependencies, which control 11 enforces. It must land in the same change as the first runtime dependency.
- Tracing, platform incident alerts, per-provider spend budgets and dependency-compromise tests (remaining issue #26 scope). An offline `health` command, a read-only `dead-letter` view, a local opt-out list and a tighten-only caps file now exist; see [worker operations](WORKER-OPERATIONS.md#health-and-dead-letter-view-issue-26). Provider 429 handling (per-account cooldowns, a bounded read-only preflight retry, `caps.json` version 2 provider budgets) exists for issue #11 and never resends a claimed post; see [provider rate limits](WORKER-OPERATIONS.md#provider-rate-limits-and-cooldowns-issue-11).
- DNS rebinding protection for user-configured social server origins. Avoid untrusted configuration and only use reputable service origins; verify DNS and TLS on the trusted host.

### Human account workflow

- Sign in yourself on the provider's official website; complete MFA yourself. Generate least-privilege tokens with scoped publishing access where possible.
- Enter tokens only into local trusted secret manager/environment, never into public git, Claude prompts, screenshots, logs or documentation.
- Review account and organization permissions before posting. Pause publishing when a token leaks, account identity changes, or a misleading scheduled claim is detected.
- Respect provider's platform rules and national laws (privacy, disclosures, advertising, copyright and consumer protection). Always mark paid/sponsored partnerships as required.
- For emergency stop: `reachweaver pause`. If a post is in `sending`/`needs_review`, check external result before manual reconciliation.

Security issues must be reported privately; see [Reporting a vulnerability](#reporting-a-vulnerability-private) above.

## Additional v0.2 controls and residual risk

- Campaign and brand JSON reject secret-like key names (token/password/session/cookie/API key); source URLs must use HTTPS with a public hostname, no literal IP/localhost/credentials. **No URL is fetched by the validator.** This is format preflight rather than a complete SSRF defense for future fetchers; network fetch implementations will need DNS/IP/redirect defenses.
- Brand account allowlists and configured account/platform checking are enforced before campaign staging. This prevents accidental cross-project staging, but approval and publication still depend on the operator correctly identifying and reviewing the destination.
- Staged campaigns are immutable by `(campaign_id,variant_id)` fingerprint; there is no silent overwrite or automatic retry. Idempotency is local to one SQLite DB. Campaign evidence is archived as owner-declared metadata, not independent proof that a URL supports a claim.
- External webpage/repository/social copy can contain adversarial instructions. Treat as **untrusted data**; do not execute or install third-party skill scripts automatically. Review SHA/permission/license/trust before installing external skills.
- The experiment evaluator requires native snapshots at matched ages and never calls remote APIs, but self-reported plan timestamps and analytics can still be forged. Do not call the output causal or proof of algorithm weights.
- Local campaign/brand/evidence files can contain sensitive product plans even without tokens. Store real profiles under ignored `data/brands/` (or outside the repo) and keep exports private. Sample files only contain safe public examples.

## v0.3 identity and local state controls

- Added explicit `connect-check --online` for identity verification against the intended Bluesky handle, Mastodon local account, or Telegram bot and **channel** identity. Telegram `getChatMember` checks channel posting permission. This is an authenticated external check, **not** social publishing. It cannot prove Mastodon `write:statuses` or Bluesky future posting eligibility. No OAuth redirect flow is implemented.
- Live sends now verify intended remote identity **before** claiming an approved post. Mismatch/read failures block without sending and preserve original approval. After a claim, delivery errors remain in `needs_review` and are never retried automatically.
- A strict no-redirect HTTP opener prevents forwarding Bearer Authorization headers or Telegram bot-token URL paths to a provider-supplied redirect destination. Response bodies remain unlogged and HTTP/network errors have generic codes. Dynamic Mastodon server configuration still requires a trusted operator; DNS rebinding and transport-level supply-chain risk are *not* completely mitigated.
- POSIX SQLite `state/` must have mode 0700 and the database 0600. New files are created privately; existing group/world-readable directories or files and DB symlinks are rejected. This prevents accidental local disclosure, **not** malicious access by the same Unix user or root. Windows ACL hardening remains future work.
- `show` displays exact private draft copy for human approval. Do not run it in public CI logs. Use `publish --post-id` for one-post supervised tests, after `doctor`/`connect-check` and the actual content approval. The ordinary multi-post publisher remains supported only for trusted supervised/approved workflows.

See [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md) for owner-assisted onboarding and evidence collection without sharing credentials.

## Privacy lifecycle controls (issue #26 slice)

- `reachweaver.privacy.redact()` / `structured_log()` strip bearer/basic credentials, JWTs, Telegram bot tokens (also in bot URLs), Bluesky app-password shapes, GitHub `ghp_`/`github_pat_` tokens, URL passwords, secret-named `key=value` pairs and, verbatim, values of `REACHWEAVER_*_TOKEN`/`_PASSWORD`/`_APP_PASSWORD`/`_BOT_TOKEN` (and `_SECRET`/`_API_KEY`) env vars. Pattern redaction is defense in depth, **not** a guarantee: an unrecognized secret format can still pass, so keep secrets out of post text and error strings in the first place.
- `data-export` redacts secret-looking text, but the export still contains private drafts and analytics. It is created as a new 0600 file and never overwrites an existing path.
- `data-purge` is dry-run unless `--apply`. Apply re-checks hard rules inside one `BEGIN IMMEDIATE` transaction: active/in-flight posts (`draft`…`needs_review`) are never touched, `send_claimed` events younger than 48 h and events of `sending`/`needs_review` posts are kept, `campaign_variants` rows/fingerprints are never deleted, and published posts keep `remote_id`/`remote_url`/status/timestamps (content becomes `[purged]`). Only counts are logged (`privacy_purge`). `secure_delete` is enabled for the purge, but backups, filesystem snapshots, prior exports and the remote platforms retain their copies.
- `privacy-status` prints only the **names** of secret env vars present, never their values.

See [privacy lifecycle](PRIVACY-LIFECYCLE.md).

## Credential hygiene audit (issue #5 slice)

- `credentials-audit [--history FILE] [--json]` is offline. It reads credentials through the same accessor as the adapters (the environment, or the files in `$CREDENTIALS_DIRECTORY` when that is set) plus the accounts config, and reports variable **names** only: `credential_source`, set and missing per account, `placeholder_value`, `orphan_credential`, `credential_value_reused` (compared through salted in-memory SHA-256 digests that are never output), `shared_credential_variable` and `overlapping_env_prefix` (for raw account lists; `load_accounts` refuses both), `unsafe_credential_file`, `unsafe_credentials_directory`, `env_credential_shadowed` (an exported credential ignored because a credentials directory is set) and, for history files named explicitly, `shell_history_literal` with line numbers only and `history_file_not_private`. It exits 1 on findings. Missing credentials are status, not findings.
- It opens no database and uses no network. `doctor` points to it.
- `tests/test_credential_hygiene.py` also asserts, with sockets blocked, that `publish --live` with missing, placeholder or provider-rejected (HTTP 401) credentials sends nothing, records no send claim, leaves the approval untouched and never prints the value.
- Provisioning patterns (hidden `read`, `pass`, `op run`, `secret-tool`, systemd `LoadCredential=` and the `EnvironmentFile` 0600 fallback) and per-provider revocation are in [ACCOUNT-ONBOARDING.md](ACCOUNT-ONBOARDING.md).
