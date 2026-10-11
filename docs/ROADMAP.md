# ReachWeaver — issue-driven roadmap

**Snapshot:** 2026-10-09. This is an issue-level roadmap, not a declaration that all checklist criteria are met. A v0.1 CLI and skill now exist, including offline drafts, approvals, durable local outbox and mock-tested text adapters (plus Threads, Instagram, YouTube, TikTok, Mastodon image and reply send paths that stay disabled until a live receipt); **no live account smoke test or completed production authorization has been verified**. See [implemented status](IMPLEMENTATION-ROADMAP.md) and [product criteria](PRODUCT.md).

## Definition of success

Enable a user to turn a trustworthy update about their own work into platform-specific, authorized posts with reliable receipts and measurable qualified outcomes. Optimize for relevance, time saved, legitimate engagement and conversions—not manufactured numbers or "guaranteed virality".

### First user journey

1. Select an owned GitHub release or RSS update and provide evidence.
2. Create a factual content brief with brand/audience context.
3. Generate two distinct channel-specific variants and validate platform limits.
4. Preview the exact destination account, content, media, privacy and date.
5. Owner connects accounts through approved authentication and authorizes the post.
6. Publish (or export manually if platform permissions prohibit direct publishing).
7. Capture permanent post links and delivery status; inspect outcomes over time.

## P0 — Foundation (must be solved before unsafe remote writes)

| Issue | Deliverable |
| --- | --- |
| #1 | MVP/product success metrics and concrete user journeys |
| #2 | Reuse/build decision using independent evidence and license review |
| #3 | Discoverable Claude Code skill with offline preview contract |
| #4 | Versioned schema, provider abstractions and state machine |
| [#5](https://github.com/MSKazemi/reachweaver/issues/38) | User-assisted OAuth, secure secrets and account selection. Offline slice merged: threat model, [onboarding guide](ACCOUNT-ONBOARDING.md), `credentials-audit`. Owner-approved hardening (2026-10-09, mock-tested): overlapping `env_prefix` refused, production-only live marker `REACHWEAVER_ENV`, systemd `LoadCredential=` through one credential accessor, Claude Code deny rules |
| #6 | Dated per-platform capability, policy and API-approval registry |
| [#7](https://github.com/MSKazemi/reachweaver/issues/39) | Approval, dry-run, immutable preview and emergency stop |
| #8 | CI/tests/fixtures/security/development guides |

**Suggested execution order:** #1 + #2 + #6 research; then #4 schema; #3 + #8 local skill/testing; #5 + #7 authentication and approvals. These are dependencies, not a reason to work on all tickets in one PR.

## P1 — Usable automated publishing and measurement

| Issue | Deliverable |
| --- | --- |
| [#9](https://github.com/MSKazemi/reachweaver/issues/40) | GitHub/RSS/paper/website signals with novelty and provenance |
| #10 | Verified brand-aware platform content and CTA variants |
| [#11](https://github.com/MSKazemi/reachweaver/issues/41) | Durable jobs, scheduling, backoff and duplicate prevention |
| [#12](https://github.com/MSKazemi/reachweaver/issues/42) | Bluesky official publishing adapter |
| [#13](https://github.com/MSKazemi/reachweaver/issues/43) | Mastodon official publishing adapter |
| [#14](https://github.com/MSKazemi/reachweaver/issues/44) | LinkedIn authorized member publishing adapter |
| [#15](https://github.com/MSKazemi/reachweaver/issues/45) | Normalized-but-not-falsely-comparable metrics and UTM attribution |
| [#16](https://github.com/MSKazemi/reachweaver/issues/46) | Measured growth experiments and evidence-based recommendations |
| [#17](https://github.com/MSKazemi/reachweaver/issues/47) | Human-approved comment/mention response suggestions |

**Recommended first technical vertical slice:** #3, #4, #6, #7, #8, #9, #10, #11, #12, then #5 auth integration as needed and #15 metrics. LinkedIn can be added when required developer permission is available. Use *offline mocked provider tests first*; real posting only with explicit owner approval.

## P2 — Expand to additional channels and validate with real users

| Issue | Deliverable |
| --- | --- |
| [#18](https://github.com/MSKazemi/reachweaver/issues/48) | Rich media, alt text, subtitles, accessibility and localization |
| [#19](https://github.com/MSKazemi/reachweaver/issues/49) | Threads integration, gated on Meta app authorization |
| [#20](https://github.com/MSKazemi/reachweaver/issues/50) | Instagram professional-account adapter |
| [#21](https://github.com/MSKazemi/reachweaver/issues/51) | Authorized Facebook Page adapter |
| [#22](https://github.com/MSKazemi/reachweaver/issues/52) | YouTube upload/Shorts and analytics, respecting audit restrictions |
| [#23](https://github.com/MSKazemi/reachweaver/issues/53) | TikTok Direct Post or approved creator-draft fallback |
| #24 | X, Reddit and Pinterest scoped feasibility before separate integrations |
| #25 | Optional SEO-repo integration and campaign attribution |
| [#26](https://github.com/MSKazemi/reachweaver/issues/54) | Spend caps, monitoring, privacy and operations |
| [#27](https://github.com/MSKazemi/reachweaver/issues/55) | Field pilot, benchmark and versioned release readiness |

## Global market research and timing roadmap (introduced 2026-10-08)

- **Implemented:** A versioned offline research registry and deterministic planner cover 36 networks/surfaces and 14 target market profiles; validate scheduling source/capability, explicitly label HN and Reddit restrictions, and convert *hypothetical* regional slots to offset-aware UTC. Standalone Claude Code bundles the same registry. **No owner account is connected by this addition.**
- **Next — evidence and localization [#6, #18]:** Verify account eligibility and platform policies market by market; gather qualified native-language reviewers and collect actual product-language/OS support evidence before localized claims. Make uncertainty and source staleness visible.
- **Next — original attribution [#15, #25]:** Keep real published URLs, first-party time series and genuine GitHub/website installs, with per-market attribution and privacy controls; never infer impressions as unique visitors or contributors.
- **Next — validated time optimization [#16]:** Design matched-age, predeclared audience/format/time experiments from real account analytics; model posting-time uncertainty and false discoveries, not universal algorithm tricks.
- **Next — actual remote integrations [#11, #14, #19–24, #30]:** Only with approved scopes, per-item human authorization, persistent job state, actual provider receipts, safe failure reconciliation and a supervised first live pilot. Native UI schedulers alone are not integrated APIs.

See [global distribution](GLOBAL-DISTRIBUTION-2026-10-08.md), [platform algorithms and native scheduling](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md), and [registry change control](DISTRIBUTION-GOVERNANCE.md).

## Wave status (2026-10-08)

Offline, mock-tested slices merged for #3 (skill acceptance review), #4 (`schema-validate`, read-only `schema-export`, ADR-0003), #5 (threat model, onboarding guide, `credentials-audit`; owner-approved 2026-10-09 hardening: `env_prefix` overlap refusal, `REACHWEAVER_ENV` production marker for live sends, systemd `LoadCredential=` support, agent deny rules), #6/#24 (`capabilities`, fail-closed live gate, X/Reddit/Pinterest no-go by default), #8 (secret scan, no-network guard, templates, ADR-0002, mypy, runtime-deps guard, SHA-pinned Actions), #9 (`sources-scan`: GitHub releases/tags/PRs/issues, RSS/Atom, changelogs, Crossref, robots.txt, pagination), #10 (`claims-check` with nonexistent-feature review flags, `campaign-preview`), #12/#13 (Bluesky graphemes/PDS/rkey receipts; Mastodon instance limits/visibility), #14 (mock-tested LinkedIn member text adapter; opt-in `w_member_social` introspection check owner-approved 2026-10-09), #15/#25 (`utm-link`, `attribution-report`, `seo-destinations`, source-checked metric definitions, snapshot observation windows, SYNTHETIC weekly report example), #16 (`experiment-preregister`/`experiment-verify`, `timing-evaluate`, `baseline-eval` against the non-personalized calendar baseline with bootstrap intervals and Holm–Bonferroni), #17 (`engagement-triage`), #18 (`media-check`, including MP4/MOV codec, frame-rate, audio and fast-start checks, and `media-storyboard` scaffolds with crop and safe-area checks), #19-#23 (`manual-export`), #26 (`data-export`, `data-purge`, `privacy-status`, `health`, `dead-letter`, opt-out list, tighten-only caps file, offline security regression suite in `tests/test_security_regressions.py`), #27 (pilot protocol, release playbook, `pilot-report`, quickstart/troubleshooting/security checklist, blocked-platform alternatives audit, versioned `release_gate.py`) and #30 (opt-in Postiz backend; acceptance is never a platform receipt). Each issue stays open for its remaining live-provider, owner-verification or field-evidence criteria; see the per-issue comments. #7 approval payload binding was owner-approved on 2026-10-08 and implemented (immutable payload hash, no grandfathering, re-approval visible in `health`); the opt-in human-presence confirmation (D6 A+B) and keyed HMAC binding (D7) were owner-approved and merged on 2026-10-09; FIDO-signed approval and replay protection remain open. #11 provider rate limits were owner-approved on 2026-10-08 and implemented (per-account 429 cooldowns, bounded read-only preflight retries, `caps.json` version 2 provider budgets, C1/C2/C3 dead-letter classification; claimed posts are never resent); owner-declared shared Postiz workspace cooldowns and a local Bluesky `createSession` budget followed on 2026-10-09 (owner-approved); the live pilot, observed provider headers, spend caps and worker monitoring remain open. Media adapters were owner-approved and merged on 2026-10-09 as one shared model, every one disabled until a live receipt: #13 Mastodon images (local files) and #20 Instagram single image (owner-hosted URL) share `posts.media_json` and `approval/media-1`; #22 YouTube upload and #23 TikTok creator inbox share the `posts.video_json` video record and `approval/video-1`. The supervised first sends with first-party receipts, and the registry changes that would enable them, remain open.

## Wave status (2026-10-09)

More offline, mock-tested slices merged on 2026-10-09. None of them connects an account or sends anything:

- #10: posting-history repetition and frequency warnings.
- #11: sourced Bluesky, Telegram, Mastodon and LinkedIn rate-limit facts in the platform registry.
- #15: metric value status (`suppressed`, `not_available`, `delayed`, `estimated`); `report` counts exact values only, shows coverage and takes `--format json|markdown|csv`.
- #16: `calendar-plan --audience-activity` turns an owner-exported, consented hour-of-week activity file into slots with provenance.
- #17: `engagement-fetch --online` reads public replies, read-only, as input to `engagement-triage`.
- #18: safe zones sourced from the platforms' ads specifications (no organic-post source found); translated subtitle track checks.
- #26: read-only `ops-metrics` (JSON or Prometheus) with alert examples, and observational `publish --log-json` worker logs.
- #27: `scripts/release_gate.py --scorecard` validates the readiness scorecard in `docs/release-readiness.json`.
- The [platform matrix](PLATFORM-MATRIX.md) is now checked against both registries by a drift test.
- A review pass fixed 11 confirmed bugs. Examples: `ops-metrics --out` can no longer overwrite the database, credentials with control characters are refused, and engagement text is redacted before it is truncated.
- PR #40 states native Threads scheduling at the strength its sources support.

Wave 8 (owner-approved 2026-10-09). These slices change approval, rate-limit or send code and were merged after the owner's security review. All are mock-tested only. Every new send path stays disabled in the capability registry until the owner records a supervised live receipt:

- #7: opt-in D6 human-presence confirmation (`approval-mode --confirmation tty_hash_prefix`) and D7 keyed HMAC approval binding with `REACHWEAVER_APPROVAL_SECRET` (`approval-mode --require-key`); both off by default. Replay of an earlier valid approval row by a DB writer stays open.
- #11: owner-declared `postiz_workspace` shared cooldown and a local Bluesky `createSession` budget.
- #14: opt-in LinkedIn `w_member_social` check through token introspection (`"scope_check": "introspect"`).
- #13: Mastodon image attachments (`media-attach --file`), bound into the approval hash; live image sending disabled.
- #20: Instagram single-image adapter (`media-attach --url`, owner-hosted image), disabled.
- #22: YouTube video upload (`youtube-attach`), disabled.
- #23: TikTok creator-inbox and direct-post video (`video-attach`), disabled behind two registry switches.
- #17: replies as ordinary post rows (`draft --reply-to`) with the reply target in the approval hash, under the owner's own threads only; sending is gated by `reachweaver_reply_adapter`, shipped as `none`.
- #9: private GitHub sources behind three opt-ins, with exfiltration guards and no write path.
- #19: Threads text adapter, disabled.
- #21: Facebook Page text/link adapter (`POST /{page-id}/feed`, Page-token identity check), mock-tested and disabled until a live receipt.

Each issue stays open for its live-provider, owner-verification or field-evidence criteria.

## Release gates

### V0.1 — Credible offline preview
- Skill discoverable in Claude Code with install instructions.
- Can ingest one provided source, produce fact-checked variants and preview without credentials.
- Tests fail on unapproved actions, injected instructions and invalid claims.

### V0.2 — One safe publishing integration
- Authorized account onboarding and explicit approval.
- Exactly one verified, permitted platform adapter with end-to-end delivery receipt.
- No known duplicate postings under repeated or interrupted delivery tests.
- Automatic/manual-export fallbacks are truthful about delivery status.

### V0.3 — Learning loop
- Two supported channels if granted permissions.
- Per-platform analytics, baseline comparisons and attributed destination URLs.
- Suggestions based on transparent experiment windows, uncertainties and opt-in changes.

### V1.0 — Responsible multi-platform workflow
- Relevant P2 integrations approved or marked unsupported with documented alternatives.
- Security/privacy/observability checks and reproducible field pilot.
- Honest comparison with existing tools, documented operating costs and limitations.

## General issue completion checklist

Each PR should include tests, user-facing usage examples, docs updates, changed capability matrix (if relevant), threat/permission review for external operations, and real vs mocked test results. Never claim a platform integration is complete based solely on unit tests if external approval and live verification are still pending.

## Research

See [PLATFORM-RESEARCH.md](PLATFORM-RESEARCH.md) for dated primary sources and constraints. The registry required by #6 must keep these facts current as API policies change.
