# ReachWeaver — product requirements and success criteria

**Status:** living product specification, 2026-10-08. This document is about intended product behavior; see [implementation reality](IMPLEMENTATION-ROADMAP.md), [architecture](ARCHITECTURE.md) and [platform capability matrix](PLATFORM-MATRIX.md) before claiming any feature is available in production.

## 1. Purpose and product promise

Turn a **real, verifiable achievement** into genuinely useful, audience-specific communication, publish only through authorized channels and explicit approval, and learn from first-party outcomes. ReachWeaver is a Claude Code **editorial and distribution workflow** with a local CLI; it is not an engagement farm, bot network, social scraping utility or guaranteed viral-growth engine.

The differentiator is an auditable loop: original evidence -> content brief -> channel-native creative -> human review -> consent-bound delivery -> receipts -> measured learning.

## 2. Primary users and jobs to be done

| Persona | Core job | Content evidence | Meaningful outcome |
| --- | --- | --- | --- |
| Open-source maintainer | Tell likely adopters about a substantive release or fix | Public GitHub release, changelog, documentation | Relevant documentation visits, issue discussions, contributions |
| Research author | Translate a peer-reviewed result into an accurate practitioner summary | Public paper, DOI, validated tables and author-approved limitations | Qualified article reads, citations, technical conversations |
| Technical founder or engineer | Explain a useful, provable product improvement | Public launch notes, demonstration, method, public website | Qualified trial signups, requests for demos, engaged technical readers |

Multiple brands or social identities must never be silently combined. User-configurable variables include project/brand, audience, target geography and language, account owner, permitted source repositories, platform/account choice, proof links, disclosure needs, publishing frequency, spend ceiling, privacy retention, review policy and target conversion. The system must not invent these choices from a user's GitHub access or a private document.

## 3. Three end-to-end user journeys

### A. Announce an owned GitHub release

**Input:** an authorized public GitHub release URL, exact release version/date, changelog, 1–5 evidence-backed product changes, target users, accounts and call to action.

**Current achievable result:** Claude Code can prepare distinct drafts, the local CLI can store and lint text, a human can approve a specific item, preview pending sends and import eventual outcome metrics. Mock-tested text adapters exist for Bluesky, Mastodon, Telegram and LinkedIn personal profiles (member; organization pages are refused), plus an optional mock-tested Postiz backend, but actual account connection and end-to-end delivery are **not yet verified**: no real owner account receipt exists. Other networks use manual creative handoff.

**Human checkpoint:** verify the release actually exists and is public; check every claimed feature, screenshots/media rights, exact social account and the post preview; authenticate personally and explicitly approve external posting. Unavailable providers must be exported for manual publication, never silently simulated as success.

**Proposed success signal:** qualified release-notes/docs visits, meaningful technical replies, contributor inquiries, not post count or raw likes alone.

### B. Explain a published scientific paper

**Input:** author-approved public paper/DOI, precise scientific claim, methods, dataset and limitations, numerical results traceable to the source, practitioner audience and attribution.

**Current achievable result:** the skill guides extraction and editing of evidence-backed platform-native posts or scripts; no automatic DOI research connector or external factual verification is guaranteed by the CLI.

**Human checkpoint:** confirm acceptance/publication status, figures, dataset sizes, caveats, attribution, and whether institutional review or embargo applies. Never change a paper's conclusions to create a stronger hook.

**Proposed success signal:** qualified paper or project-page visits, useful peer discussion and documented collaboration inquiries. Any citation-growth claim needs its own evidence.

### C. Repurpose an owned website article

**Input:** publicly shareable article URL, publication date, factual supporting references, target segment, brand voice and desired action.

**Current achievable result:** Claude Code prepares per-platform copy and a campaign brief. The current runtime does not crawl a website or fetch Google Analytics/Search Console automatically; locally imported metrics can be summarized with missingness indicators.

**Human checkpoint:** verify content rights, claims, intended social identities and any user-tracking disclosures. Unverified platform publishing remains manual-only.

**Proposed success signal:** UTM-attributed engaged visits and relevant opt-in conversion events, with measurement gaps explicit.

## 4. Measurement model and guardrails

**Primary proposed north-star:** number of **qualified, attributable project outcomes** generated by an approved campaign, such as interested documentation visitors, legitimate contributors, requested demos, opt-in subscribers or substantive researcher conversations. Outcome definition and observation window must be chosen by the account owner before optimizing.

**Diagnostic metrics:** reach, unique reach, impressions, views/watch time, engagement rate, substantive replies, shares, saves, profile visits, clicks, referral traffic and subsequent adoption actions where authorized sources provide them.

**Interpretation rules:**
- Never sum incompatible view/impression definitions across platforms or label missing metrics as zero.
- Record data source, platform, account, observation window, units, denominator, data freshness and missingness with every measurement.
- Separate a content-quality result from platform distribution effects, audience size, posting schedule and algorithm changes.
- Prefer matched observation windows and pre-registered hypotheses. A rise in views alone does not prove improved conversion or causal impact.
- Preserve a human-reviewed baseline before declaring a lift. A measurement with insufficient sample size is exploratory, not proven.

**Safety KPIs:** unauthorized external writes = 0; wrong-account posts = 0; leaked secrets = 0; silent duplicate sends = 0. Track factual corrections, user-requested removals, compliance issues and availability separately rather than hiding them inside growth scores.

## 5. Current scope vs release gates

### Experimental v0.1 (code exists; real provider smoke tests pending)
- Project and standalone Claude Code skill, guided content templates.
- Python CLI with local SQLite outbox, per-item approve/schedule/dry-run, pause, manual reconciliation and local metrics import.
- Mocked text-only Bluesky, Mastodon and Telegram API adapters. (Update 2026-10-09: a mock-tested LinkedIn member text adapter, issue #14, and an optional mock-tested Postiz backend, issue #30, were added since; none has a real live receipt.)
- Published README/architecture/security notes and offline tests.
- **Not a production-readiness claim:** tokens, accounts, platform reviews and actual remote send receipts still require verification.

### v0.2: first authorized, observable vertical slice
- One user-owned test account on one officially permitted platform, with documented permission/scopes and live account verification.
- Verified source -> platform-specific approved text -> due scheduled job -> true remote post ID/URL -> reconciled delivery outcome.
- Demonstrated wrong-account, expired credentials, rate/quota refusal, remote timeouts and ambiguous send recovery without duplicate delivery.
- Security review, explicit secret-handling setup, kill switch and operational runbook.
- Baseline metrics export, defined observation window and honest campaign report.

### v1.0: repeatable, measurable, consent-first workflow
- At least two independently live-verified platforms; unsupported ones correctly remain manual-only.
- Account-bound approvals, official API compliance, platform-specific rate/cost enforcement and safe delivery receipts.
- Authorized analytics ingestion with provenance, denominator and missingness, plus optional site/UTM attribution where permitted.
- Real-world evaluation with replicable before/after methodology, no manufactured success claims and documented operational limitations.

These are **release quality gates**, not statements that the corresponding features are finished. Gate status is tracked in [implementation roadmap](IMPLEMENTATION-ROADMAP.md).

## 6. Explicitly out of scope

No guaranteed virality, fake followers, engagement pods, purchased interactions, unsanctioned bulk DMs, credential collection in chat, CAPTCHA/MFA bypass, scraping behind access controls, fabricated statistics, fake endorsements or synthetic testimonials presented as real. Never disclose private research or private repository content without explicit rights and owner permission. No automatic publication on networks that require app review or permissions not yet obtained.

## 7. Immediate evidence-based MVP experiment

Use a public GitHub release or publicly posted technical article as a **single owned source**. Write two genuinely different native variants for two chosen audiences/platforms. Require the owner to review original claims and exact copy. Publish manually or via **one authorized and live-tested account only**, observe outcomes after a declared time window and log missing metrics. Run one meaningful improvement hypothesis; do not mechanically repost duplicate content to manufacture volume.

## 8. Requirements traceability

- This specification addresses [issue #1](https://github.com/MSKazemi/reachweaver/issues/1) (personas, outcomes, 3 worked journeys, MVP/v1.0 gates).
- Tool reuse, legal feasibility, schema, auth, platform policy, approvals and CI are owned by [#2](https://github.com/MSKazemi/reachweaver/issues/2), [#4](https://github.com/MSKazemi/reachweaver/issues/4), [#5](https://github.com/MSKazemi/reachweaver/issues/5), [#6](https://github.com/MSKazemi/reachweaver/issues/6), [#7](https://github.com/MSKazemi/reachweaver/issues/7), [#8](https://github.com/MSKazemi/reachweaver/issues/8).
- First metrics integration and experimentation are [#15](https://github.com/MSKazemi/reachweaver/issues/15) and [#16](https://github.com/MSKazemi/reachweaver/issues/16).

**Open product choices (not blockers for this document):** select actual brands/accounts, countries, languages, budgets, user approvals and conversion event definitions with the owner before any live pilot. These must remain user-controlled rather than guessed.
