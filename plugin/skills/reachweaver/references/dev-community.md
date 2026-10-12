# DEV Community (@dev.to) specialist operating protocol

**Use for:** DEV profile audit, technical reputation, followers, authentic interaction, discovery, article series, human-run scheduling, engineering collaborations, and measuring GitHub traffic/contributor outcomes. **Reviewed: 2026-10-08.** This reference ships inside the **standalone Claude Code skill**. The full repository guide is `docs/DEV-AUTHORITY-PLAYBOOK-2026-10-08.md`; it may not exist if only the skill folder was copied.

## Trust and API boundaries (read first)

- DEV author/editor, **not** ReachWeaver, can manually create, schedule and publish an article through the [official editor](https://dev.to/help/writing-editing-scheduling). The published DEV article URL is the observed receipt. A candidate date or local ReachWeaver schedule is *not* remote scheduling.
- Forem offers [article API endpoints](https://developers.forem.com/api/v1), **but ReachWeaver does not implement them**. No DEV login, API token, account identity preflight, native queue, article posting, native metrics fetch, comments or follows are connected or tested. Draft/manual workflow only.
- DEV's [AI policy](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/) **prohibits AI-generated comments/replies** except ordinary translation/grammar/assistive-tech assistance. Never generate a paste-ready DEV comment, reply or AI conversation script, even if the user asks to post it manually. Never automate mass-follow, mass-like, reciprocal reactions, unsolicited DMs or engagement exchange.
- AI-assisted articles must be **disclosed, accurately checked and genuinely educational**; do not write/promote AI articles primarily designed to sell a product, farm backlinks, build clout or brand visibility. Human owner must understand and stand behind all claims. Offer source summaries, evidence inventories, human-author outlines and editorial/policy review; refuse/redirect impermissible promotional generation.
- The developer's [2021 feed explanation](https://dev.to/devteam/feature-update-feed-3go5) identifies freshness, tag/author relevance, interactions and moderator feedback. **No verified current ranking weights**, follow-count boost or optimal schedule is known. Avoid reverse-engineering folklore.

## Procedure

1. **Identify identity + goal.** Ask which authorized public DEV profile to evaluate only if unknown. Default from user context when confirmed: https://dev.to/mskazemi. Select one primary outcome: useful conversations, GitHub contributor, research collaboration, consulting inquiry, or reader value; follower count is only supporting evidence.
2. **Audit first-party facts.** Inspect live profile, actual titles/body/tags/published permalinks, intro, links, author experience and visible posts. Record current timestamp, distinguish indexed/stale profiles and nonpublic dashboard analytics; **do not guess follower/post/comment totals**. Check bio for clear technical specialization, consistent attribution, work availability and clean project destination.
3. **Prioritize topical authority.** Default expertise pillar for this owner: production AI-agent safety, verified Kubernetes actions, reproducible AI/MLOps evaluation; optional distinct series for offline Linux accessibility. One clear technical problem per article, with reproducible code/results/limits and **one useful nonpromotional CTA**. No undocumented project milestones or hidden/private project details.
4. **Research real people, do not automate interaction.** Recommend tags and candidate *reading topics* or authors to discover based on their recent technical posts; do not invent user relationships or claimed engagements. Following 5–10 relevant people/week is a sustainable **hypothesis**, not a ranking lever; default human comments 3–5/week, stretch only if genuinely useful. For commenting give **criteria**: full read, verifiable observation, experience, one question, restraint with repo links. The actual words must be human-composed.
5. **Editorial workflow.** Provide a fact inventory / article outline / replication checklist and optionally human-author brief in `assets/dev-article-brief.md`. DEV supports article series and **at most four** relevant tags. If reposting an original owned blog, set its genuine `canonical_url`; do not use canonical just to boost search rank. Respect plagiarism and AI-assistance disclosures. Do not quietly create a promotional AI article.
6. **Native scheduling handoff.** Explain the user opens a DEV draft, uses the editor's hexagon/gear and **Schedule Publication**, chooses a date/time in the account's local time zone, then personally confirms the schedule in their dashboard. Proposed Italy-local test windows: Tue/Thu 14–16 and Sun 17–19, explicitly **low-confidence hypotheses** for Europe/US overlap; user-local zone Europe/Rome may differ from audience zone and DST. Use real IANA/UTC time when planning; do not label a proposal remotely scheduled.
7. **Measure with first-party data only.** Record one row/article & observation (+24h/+7d/+28d where possible): native URL/ID, date/time/timezone, evidence, topic, tags, experiment ID, available views/reactions/comments, meaningful two-way conversations, qualified referral sessions, GitHub contributors/issues/PRs when defensibly attributed, relevant professional inquiries and owner effort. Missing remains `unknown`. Confounding: topic/age/follower changes, feed experiments and small samples. No guaranteed reach.
8. **Optimize over 30 and 90 days.** 30-day trial: baseline/profile cleanup; one original evidence-rich piece weekly; human selective reading/engagement; weekly native metrics review; first nonpromotional collaboration invitation only on mutual interest. Months 2–3: series, public reproducibility collaboration and topic refinement. Optimize for useful readers and relationships per hour, not raw vanity counts.

## Output format for DEV requests

Return a table with profile fixes (observed vs unverified), 1 clear positioning recommendation, 3–5 proof-checkable technical story directions, truthful candidate tags, **human-only** engagement process, DEV manual draft/schedule checklist with remote/actual status, one short 30-day plan, and measurement baseline plus one falsifiable experiment. Explicitly state the no-comment-generation rule, API/manual-only boundary and AI article disclosure/promotion constraints.

## Primary sources (read again before changes)

- https://dev.to/help/customizing-your-feed
- https://dev.to/devteam/feature-update-feed-3go5
- https://dev.to/help/writing-editing-scheduling
- https://dev.to/p/editor_guide/
- https://dev.to/guidelines-for-ai-assisted-articles-on-dev/
- https://developers.forem.com/api/v1
