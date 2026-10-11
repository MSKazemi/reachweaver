# Global distribution operating standard

**Review date:** 2026-10-08 | **Status:** Research and offline proposals; not a production posting or first-party analytics optimizer.

## Authority and source of truth

1. `reachweaver/distribution_registry.json` is the **versioned, testable snapshot** of channel identities, scheduling claims, integration reality, market and goal-specific editorial priorities, and experimental time windows. This file is not proof of proprietary algorithm knowledge or a future traffic forecast.
2. `reachweaver/distribution.py` validates and reads that file and can generate offline channel research using `reachweaver distribution-plan`. The same files are shipped in the standalone Claude Code skill. Changes must pass `tests/test_distribution.py` and mirror checks.
3. `docs/GLOBAL-DISTRIBUTION-2026-10-08.md` explains audience strategy, local-market ethics, moderation and pilots. `docs/PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md` explains official algorithm guidance and native scheduler distinctions. `docs/OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md` treats long-term contributor conversion.
4. `docs/PLATFORM-MATRIX.md` is the human-readable view of the **live ReachWeaver delivery adapter** status. It mirrors `reachweaver_adapter` in `reachweaver/platform_registry.json` and the scheduling modes here, and `tests/test_platform_matrix_docs.py` fails when they disagree. If scheduling and API status disagree, **do not infer a new publishing capability**; the most conservative statement prevails. No real accounts have been connected or live end-to-end publishing verified.
5. Skill references are portable distilled operating rules, not an independent source of supposedly verified platform capabilities. Update them when platform or moderation rules change.

## Use it without publishing

```bash
# All commands below are local/read-only, require no social login, and do not schedule any post.
python -m reachweaver.cli distribution-plan --region india --goal adoption --limit 5
python -m reachweaver.cli distribution-plan --region china --goal contributors --limit 6 --json
python -m reachweaver.cli distribution-plan --region nigeria --goal collaboration --date 2026-10-14 --json
# Worldwide has no single audience timezone: supply a specific one for any time experiment.
python -m reachweaver.cli distribution-plan --region global --goal awareness --date 2026-10-14 --timezone Europe/Rome --json
```

From a copied Claude Code skill, use `python ~/.claude/skills/reachweaver/scripts/run.py distribution-plan ...` while working in the target project.

The result explicitly says `OFFLINE_RESEARCH_PROPOSAL_NOT_SCHEDULED`. The **native_schedule** field describes a *platform* or account role; **reachweaver_delivery** says what our code has. The `illustrative_time_tests` field is an untested local-time hypothesis, with its IANA zone converted to an offset-aware UTC instant. Neither field schedules a remote platform post, confirms an account, recommends a guaranteed good hour or grants publishing consent.

## Change-management contract for platform and market research

For every new/changed factual claim:

1. Prefer **current primary sources**: platform policies/help/docs, product controls and API specs. Keep a `https://` URL, observation date, specific relevant account role, eligibility, format limits, and difference between native UI and API. If a source becomes stale/unavailable, mark `unverified` rather than retaining a strong claim.
2. Separate **(a) platform-native scheduling**, **(b) permitted external API/client scheduling**, **(c) currently mock-tested ReachWeaver adapters**, **(d) live verified owner account and real remote receipts**. A provider announcement never proves (d).
3. Record market-priority changes as **editorial hypotheses**; explain target persona, actual problem, language and first-party experiment that could falsify the selection. No invented ranking weights, growth guarantees or universal optimal times.
4. Country and language are separate: localized user documentation or captions do **not** establish speech recognition, hardware or OS language support. Obtain human local-language QA, use correct character sets, and label unsupported features transparently. Check local account-creation requirements and legal/regional availability; do not bypass platform verification.
5. Every modified market must reference defined platform IDs, all four supported goals, an IANA timezone for country-level time proposals, and no duplicated priority IDs. Update both portable and canonical registries with `python scripts/sync_skill.py`.
6. Run `python -m unittest discover -s tests -v`, inspect the PR diff, and let GitHub CI verify Python 3.11, 3.12 and 3.13. Changes to writing/publishing permissions require security review and a separately approved live pilot.
7. Recheck policies before each campaign; dates in a versioned file indicate the last **document review**, not a guarantee that every cited product feature still exists.

## Community-specific rules

- **Hacker News:** An exceptional `human_only` channel. Research and troubleshooting checklists are allowed, but ReachWeaver must never write, polish, translate, automate or schedule HN-facing titles, submissions or comments. See HN official [guidelines](https://news.ycombinator.com/newsguidelines.html), [Show HN](https://news.ycombinator.com/showhn.html), [current restriction](https://news.ycombinator.com/showlim), and [2026 moderator note](https://news.ycombinator.com/item?id=22336638). Do not suggest republishing to circumvent flags or coordinated votes.
- **Reddit:** Native scheduled posts are a moderator capability, **not** a regular-user broadcasting feature ([Reddit help](https://support.reddithelp.com/hc/en-us/articles/15484443169556-Scheduled-and-Recurring-Posts)). Regular promotion is a rule-by-rule manual community decision; avoid repeated mass linking.
- **Facebook Groups, Discord and independent communities:** Permission and local rules outrank growth opportunity. Membership does not confer promotional broadcast authority; no mass unsolicited messaging.
- **Chinese/regional platforms:** Do not claim permission, API access or local availability before account-role and regulation checks. Use human QA for Simplified Chinese, Japanese, Arabic, Swahili, French, Portuguese, Hindi and other languages as appropriate.

## Evidence ladder for existing activity

Use `examples/yazses-channel-audit.example.json` to distinguish **owner_reported** from **verified** activity. Upgrade only with the real URL, timestamp, source account, first-party receipt and, if measured, a native export with clear metric definition. Never commit credentials, private analytics, login cookies, contact lists or third-party personal information.

The channel audit is illustrative and NOT wired into the send queue or account config. Publicly posting content elsewhere does not consent to ReachWeaver posting.

## Sample four-week regional experiment

| Week | Activity | Gate | Observable useful result |
|---|---|---|---|
| 1 | Verify current LinkedIn, DEV, YouTube, GitHub and Snapcraft presence; fix demo-to-install funnel | Owner URLs and public feature validation | Native click sources, useful repo/docs journey |
| 2 | One original offline Linux workflow demo, distinctly adapted to YouTube, DEV and LinkedIn | Human proof/content review; site rules | Watched demos and relevant troubleshooting questions |
| 3 | India tutorial and one China developer/video trial if authenticated and human-localized | Correct account eligibility; language QA; no false Chinese dictation promise | Country-specific qualified site/repo referrals |
| 4 | One Nigeria/Kenya/South Africa and one Japan/Indonesia test | Relevant community/locale, no broad unsolicited broadcasts | Qualified visits, installations, issue authors, time cost and moderation outcomes |

For each post and locale, log `content_evidence`, `published_url`, `platform_account`, `market/language`, `manual/native/API`, `local+UTC time`, `creative_format`, `approval`, `moderation_outcome`, `views_definition`, `+24h/+7d metrics`, `qualified referrals`, `installs`, `contributors`, and `missing_data`. These are *future measurement requirements*, not fields automatically collected by the current planner.

**Metrics rule:** Platform views, impressions, unique people, clicks and repository stars are different denominators. Do not add them into a single fabricated global audience. Prefer qualified acquisition and repeat contribution, with honest uncertainty, to large but irrelevant reach.
