# Roadmap, prioritized by accountable outcomes

This document distinguishes implementation, validation and aspiration. A completed checklist requires working code, tests, first-party evidence and updated docs.

## P0 — Reliable, safe foundation

- [x] Project-level and standalone Claude Code skill with narrow trigger, progressive references and original assets.
- [x] Original Python CLI with human per-item approvals, immutable drafts after approval, UTC scheduler and default dry run.
- [x] Approval bound to an immutable payload hash (#7, owner-approved 2026-10-08): re-verified in the claim, on `schedule` and on `calendar-plan --apply`; legacy approvals need re-approval and are listed by `health`. Opt-in additions (owner-approved 2026-10-09, mock-tested): D6 human-presence confirmation (`approval-mode --confirmation tty_hash_prefix`, friction only) and D7 keyed HMAC binding with `REACHWEAVER_APPROVAL_SECRET` (`approval-mode --require-key` latches it); both off by default. FIDO-signed approval and replay protection are still open (threat model T7/T8).
- [x] Local SQLite durable outbox with concurrent claim lock, needs_review recovery, stop/resume and audit events.
- [x] Bluesky/Mastodon/Telegram/LinkedIn-member text adapters with mocked protocol tests (real account verification pending; no live receipt exists).
- [x] Manual first-party analytics import and missingness-aware summaries.
- [ ] Verify the first live test post and failure/permission paths using user-owned test accounts. Offline account identity and Telegram permission checks implemented; real first-party receipt pending.
- [x] Enforce protected POSIX `state/` directory and SQLite permissions; document owner-assisted environment secrets and the first pilot. No secret manager integration or OAuth wizard yet.
- [ ] Conduct dependency, token and threat-model review for the first production installation.

## P1 — Real cross-platform capability

- [ ] LinkedIn personal and organization OAuth app approval; Posts API text/image/document uploads and receipts. (Partial, 2026-10-09: member text adapter is mock-tested, issue #14, [LINKEDIN.md](LINKEDIN.md), with an opt-in `"scope_check": "introspect"` token introspection check for `w_member_social` (owner-approved 2026-10-09); organization pages, media and a real receipt are still open.)
- [ ] Instagram/Facebook/Threads Meta developer app, token refresh and media pipeline respecting account eligibility. (Partial, 2026-10-09, owner-approved: Threads text adapter, issue #19, and Instagram single-image adapter with an owner-hosted image URL, issue #20, are mock-tested and disabled in the capability registry until the owner records a live receipt; Facebook Page text/link adapter, issue #21, is mock-tested and disabled until a live receipt too. No Meta app review, token refresh or real receipt yet.)
- [ ] X, YouTube and TikTok scoped API auth, user consent, media upload, async processing/audits and quota checks. (Partial, 2026-10-09, owner-approved: YouTube resumable upload, issue #22, and TikTok creator-inbox and direct-post video, issue #23, share one bound video record and are mock-tested; every registry video switch is `off` and `policy.SUPPORTED_LIVE_VIDEO` is empty until a live receipt. X has no adapter. No OAuth app audit or real receipt yet.)
- [ ] Adapter interface for supported third-party scheduler (self-hosted Postiz or permissioned MCP) without copying licenses. (Partial, 2026-10-09: optional Postiz REST backend is mock-tested, issue #30, [POSTIZ.md](POSTIZ.md); no instance connected, MCP deliberately unused.)
- [ ] Native analytics connectors with consent/scope checks and normalized provenance, freshness and coverage.
- [ ] Media accessibility workflow: captions/alt text, ownership/licensing, short video specs and reusable templates. (Partial, 2026-10-09: `media-attach`/`media-detach` add one image with required alt text to a Mastodon draft, issue #13, or an Instagram draft; the file hash, MIME type, alt text and focus are bound into the approval hash, tag `approval/media-1`. Live image sending stays disabled until a live receipt.)
- [ ] Human preview dashboard, approval expiration and multi-account routing.
- [ ] Human-approved replies sent through ReachWeaver (#17). (Partial, 2026-10-09, owner-approved: `draft --reply-to ITEM --online` creates an ordinary post row whose verified reply target is bound into the approval hash, only under the owner's own published Bluesky/Mastodon threads; sending needs `reachweaver_reply_adapter: live_reply`, which the bundled registry ships as `none` until a live receipt.)

## Offline calendar-planning foundation (2026-10-08)

- [x] Deterministic `calendar-plan` CLI for batch audience-local proposals, timezone/DST correctness, existing-post collision checks, account-specific spacing and blackouts.
- [x] Optional atomic **local-only** due-time application to individually approved text drafts for current Bluesky/Mastodon/Telegram/LinkedIn-member adapters (`policy.SUPPORTED_LIVE`); manual handoff for all other platforms.
- [x] Tests for malformed plans, explicit approval, emergency pause, timezones, collision races and rollback; documentation and standalone skill sync.
- [ ] First-party audience insights, analytics-trained scheduling, stable multi-account calendar dashboard and persistent supervised worker; **not claimed as implemented**.

## Issue #11 scheduled-delivery safety slice (2026-10-08)

- [x] Atomic per-account rolling 24-hour send-attempt cap and minimum claim-gap safeguards, enforced through durable claimed-attempt history even after crash/restart and reconciliation.
- [x] Read-only `queue-health` diagnostics for overdue schedules, stalled claims and uncertain publishes; never blind retry.
- [x] Optional `--scheduled-only` and max lateness for an unattended approved-post sweep; unscheduled approvals and old missed slots cannot auto-publish in the recommended worker mode.
- [x] Inert systemd service and timer examples with owner-controlled gate, restricted paths and an explicit supervised rollout procedure; no actual service installed or enabled.
- [x] Offline regression tests and portable skill mirror updated; complete CI matrix required before merge.
- [x] Provider rate limits (owner-approved 2026-10-08, proposal part 2): `Retry-After`/Telegram `retry_after`/Mastodon `X-RateLimit-Reset` parsing, per-account 429 cooldowns (60 s–24 h) that skip only unclaimed posts, C1/C2/C3 classification for dead-letter guidance, at most 2 jittered retries of the read-only preflight (never Bluesky `createSession`), tighten-only `caps.json` version 2 provider budgets; claimed posts are never resent. Mock-tested only.
- [x] Owner-declared `postiz_workspace` shared 429 cooldown for Postiz accounts of one organization and a local Bluesky `createSession` budget (half the documented 30 per 5 minutes and 300 per day) checked before any request (owner-approved 2026-10-09, tighten-only). Mock-tested only.
- [ ] Remaining #11: approved real-account pilot, observed provider 429 headers, remote status polling/reconciliation, alerts, secure installer, worker uptime monitoring and spend ceilings. **Do not close #11.** See [worker runbook](WORKER-OPERATIONS.md).

## Issue #26 health, dead-letter, opt-out and caps slice (2026-10-08)

- [x] Offline `health [--json] [--stale-minutes N]`: per-account adapter, registry staleness, credential names, LinkedIn version/expiry, Postiz, queue, rolling budget, pause and live opt-in; `ok | attention | blocked`, exit 1 when blocked.
- [x] Read-only `dead-letter` view of `needs_review` posts grouped by sanitized error code with reconcile steps; no retry path.
- [x] Local `config/optout.json`: no reply/issue draft for listed authors; `optout_mention` error in campaign and claims checks.
- [x] Tighten-only `config/caps.json` for `publish --live` (strictest of CLI and file); a looser file value is ignored.
- [x] Runbook for revoked permissions, LinkedIn version sunsets and Mastodon instance changes ([worker operations](WORKER-OPERATIONS.md#runbook-revoked-permissions-and-api-version-sunsets)).
- [ ] Remaining #26: alerts, traces/retry metrics, per-provider spend ceilings, dependency-compromise and poisoned-feed tests. **Do not close #26.**

## Native owner-exported profile/post CSV overlay (2026-10-12, issue #3)

- [x] Normalize consent-acknowledged, owner-supplied native metrics CSV **in memory**, with explicit format/source/time/account constraints and no network or DB writes.
- [x] Enforce duplicate-post refusal, strict booleans, exact known metric columns, limits (2MB/100 posts), timezone-aware stamps and meaningful unknown-versus-zero semantics.
- [x] `post-audit` / `suggest` use `--metrics-csv FILE --owner-authorized`; the supplied JSON must declare each account. Preserve original inputs, reject credentials and return provenance caveats.
- [ ] Not a direct OAuth/native analytics connector, proof of ownership or unattended sync; API read scopes, service compliance and live pilots remain separate work under #3 / #15.
- [x] [CSV specification](PROFILE-METRICS-CSV.md), fixture and offline validation added.

## Per-network profile and post audit foundation (2026-10-11, issue #3)

- [x] Add a versioned 25-platform profile/post audit registry with evidence tiers, checked dates, individual field checklists, content experiments and relevant native outcome metrics.
- [x] Add offline `profile-audit --file OWNER.json [--platform]` with checklist coverage, source-labelled known/unknown observations, owner-reported completeness heuristic, bio/claim/clean-link and optional brand consistency findings.
- [x] Add offline `post-audit --file OWNER.json [--age-hours 168]` with explicitly owner-reviewed creative flags, single-platform/account/format/source/age metric cohorts, missingness and descriptive medians.
- [x] Add README/skill routing, privacy/creative guidance, self-contained illustrative example, Python offline tests and portable skill mirror.
- [ ] Not yet done: consented live read-only profile APIs, platform-specific media quality/retention importers, independently verified profile content or causal reach uplift, production experiments and continuous account learning. See [full audit guide](PROFILE-AND-POST-AUDITS.md) and [issue #3](https://github.com/MSKazemi/reachweaver/issues/3). No external account touched.

## Issue #6 — Evidence-ranked next actions (2026-10-12)

- [x] `suggest` CLI synthesizes **existing** per-network profile/post observations, native metric coverage, queue safety, owner-exported engagement, SEO brief conflicts and experiment-next, all optional and read-only.
- [x] Explicit urgency ordering (P0 unresolved privacy/remote ambiguity > P1 confirmed blockers > P2 actionable fixes/missing observations > P3 controlled tests), transparent provenance, no untrusted comment excerpts and withheld SEO destinations.
- [x] Optional `--include-db` uses strictly query-only SQLite on an existing database, never silently creates or migrates it; no social APIs, automated engagement or publish permissions.
- [x] README, architecture, Claude Code skill and offline validation/tests added. See [next actions](NEXT-ACTIONS.md) and [issue #6](https://github.com/MSKazemi/reachweaver/issues/6).
- [ ] Additional optional integrations: owner-confirmed native account analytics export adapters, longitudinal first-party conversion readouts, interactive review dashboard and validated experiment follow-ups. This command is a deterministic review queue, not a model that predicts growth.

## P2 — Evidence-driven attention intelligence

- [ ] Dataset of post ideas/outcomes per platform and project (privacy-preserving).
- [ ] Account-aware content discovery from owned projects and changelogs, with verifiable citations and source checks. (Partial: read-only `sources-scan` and `signal-brief`; since 2026-10-09, owner-approved, private GitHub repositories are read only behind three opt-ins, issue #9, with exfiltration guards: private signals are not publishable until a human marks them, private repository URLs are never public links, exports redact them, and there is no write path.)
- [ ] Native-format adaptation benchmark with factuality/voice scoring; A/B hypotheses with confounder reporting.
- [ ] Analytics ingestion from Google Search Console/GA4/UTM and GitHub outcomes via separate existing SEO skill.
- [ ] Automated weekly review with prompt-injection boundaries and suggested experiments, never fabricated reach.
- [ ] Optional recommendation exploration with transparent uncertain estimates, platform/cohort/time controls.

## Issue #16 read-only timing analysis slice (2026-10-08)

- [x] New `timing-evaluate` analyzes only already imported source-labeled snapshots from one account/platform at matched post ages and one denominator. Reads no private APIs and never modifies scheduled content.
- [x] Six-hour audience-local weekday/weekend time groups report medians/IQR, evidence coverage, exclusion counts and explicitly **exploratory** candidate status after minimum post counts in two groups.
- [x] Regression tests cover account/campaign/source isolation, missing/zero data, nearest snapshot, dates/timezones, sparse samples, ties and read-only CLI.
- [ ] Not complete: consented first-party audience-activity connector, statistically robust future holdout tests, personalized model validation, qualified GitHub/site conversion attribution and independent long-term evaluation. See [timing protocol](TIMING-EVALUATION.md) and [#16](https://github.com/MSKazemi/reachweaver/issues/46).

## Issue #16 baseline evaluation slice (2026-10-08)

- [x] New read-only `baseline-eval` compares account-specific `--slot` and `--campaign-arm` outcomes against `calendar-plan`'s generic non-personalized slot over a required `--since/--until` period, from stored source-labeled snapshots at matched post age.
- [x] Seeded percentile bootstrap intervals, exact/Monte Carlo permutation p-values and Holm–Bonferroni across all comparisons in a run; explore/exploit roles; `correlational` and `randomized` (owner-declared) results kept apart; `insufficient_data` with `null` estimates below the minimum sample.
- [x] Saved `experiment-readout` results can be evaluated together; their labels and the strict sign-test gate are unchanged.
- [ ] Not complete: no real account evaluated, no multi-week pilot run (#27), no consented audience-activity import, no learned recommender.

## P3 — Scalable, governed operations

- [ ] PostgreSQL storage, transactional worker leases, backoff/rate limiting with provider-specific ceilings.
- [ ] Renewable OAuth credential vault, key rotation, history-wide secret scanning and audit log signing. (Tracked-file secret scanning with a reviewed allowlist runs in CI since #8: `scripts/secret_scan.py`.)
- [ ] Multiple operator RBAC, consent history, legal retention and deletion, tenant separation.
- [ ] CI conformance tests against API sandboxes/test accounts (never production-by-default).
- [ ] Public docs, reproducible evaluation suite, independent security assessment and measured real-world case studies.

## Definition of done for new adapters

Official documentation link + dated verification; auth scopes and account eligibility; HTTP or SDK implementation; sensitive data handling; mock tests of success, 401, 403, 429, timeout, unknown outcome; idempotency/reconciliation; rate limits/backoff; real authorized test account smoke test; rollback/disconnect; accessible content where relevant; docs and versioned capability matrix. No adapter is marked live until this checklist is satisfied.

## v0.2 verified offline slice — 2026-10-08

Implemented: source-bound JSON campaign lint, private brand account allowlists, conservative cross-platform duplicate warning, evidence `checked_at`/declaration, immutable idempotent draft staging, non-publishing draft export, directional matched-age paired experiment evaluator, creator-focused skill references, manual agent-quality eval corpus and regression tests. These are partial realizations of issue #10 and issue #16 plus safety improvements for #3/#4/#7/#8; the issues stay open if their larger scopes remain incomplete.

Remaining P0/P1: real OAuth-assisted account onboarding, first-party API authorization checks for each channel, durable cross-device scheduler, media/video creation and accessibility pipeline, official LinkedIn/Meta/YouTube/TikTok publishing, attribution of website conversions, native metric collectors, human reviews of actual output quality, supervised real-world pilot and independent benchmark. None is implied by a passing offline test suite.

## v0.3 account safety gate (2026-10-08)

Added explicit `connect-check --account ID --online` with provider identity matching and sanitized remote auth failures. The live publisher performs this preflight **before** claiming a post, so configuration mismatches preserve the approved state without attempting a send. Bluesky checks returned handle/DID; Mastodon compares authenticated local username; Telegram validates expected bot username, channel identity and posting rights, rejecting private chats. Telegram returns verified permissions; Mastodon/Bluesky write scopes cannot be established by read-only tests. The HTTP client refuses redirects so credentials cannot be forwarded to unexpected origins, and private SQLite state creation fails closed for unsafe POSIX modes/symlinks. Added `show` (full exact preview) and `publish --post-id` (one-post pilot isolation). The supervised first pilot still **requires owner credentials/consent and native post receipt**; see [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md).
