---
name: Platform adapter / registry update
about: Propose a new publishing adapter or change to platform, scheduling or market evidence
title: "[platform] "
labels: platform
---

<!--
An adapter is only accepted with official, dated, first-party documentation. Third-party blog posts,
scheduler marketing pages and model output are not sufficient. Never include real credentials.
-->

## Platform

- Platform / surface:
- Change type: new delivery adapter / adapter change / registry (planning) data only

## Official documentation (required)

| Item | Official URL | Verified on (YYYY-MM-DD) |
|---|---|---|
| Publishing / posting API |  |  |
| Authentication & scopes |  |  |
| Rate limits / quotas |  |  |
| Platform automation / developer policy |  |  |
| Native scheduling (if any) |  |  |

## Account type (required)

- Account type required: personal / creator / business / page / channel / bot / organization
- Eligibility limits (follower count, verification, region, paid plan):
- Who will test it: owner-assisted pilot with the maintainer's own account / not yet tested

## Approval requirements (required)

- Platform-side app review or audit required? yes / no — link:
- Scopes requested (least privilege):
- Unaudited-app restrictions (e.g. private-only visibility):
- ReachWeaver human approval: every post still requires `approve`, the dry-run default and `--live` + `REACHWEAVER_ALLOW_LIVE=1` + `REACHWEAVER_ENV=prod`, and every credential is read through `accounts.credential`. Confirm this adapter does not bypass them: yes
- Ambiguous outcomes: how is an unknown send result detected and left in `needs_review` without retry?

## Capability status claimed

- [ ] NOT IMPLEMENTED (registry/planning only)
- [ ] MOCK-TESTED (HTTP mocked in `tests/`, no live account)
- [ ] LIVE-TESTED (owner-assisted pilot with first-party receipt; attach sanitized evidence)

## Registry and docs to update

- [ ] `reachweaver/distribution_registry.json` (+ `tests/test_distribution.py`)
- [ ] `docs/PLATFORM-MATRIX.md`
- [ ] `python3 scripts/sync_skill.py` for the bundled skill copy

Rules that must be preserved: Hacker News stays human-only; Reddit stays moderator/community-rule-only.
