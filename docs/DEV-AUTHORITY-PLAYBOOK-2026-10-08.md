# DEV Community: authority, useful visibility and collaboration

**Evidence review:** 2026-10-08. **Owner example:** [@mskazemi](https://dev.to/mskazemi). **Status:** strategy and human-led operating procedure, **not** a connected DEV account, measured growth result, or ReachWeaver DEV publisher. This page specializes the [developer-community guide](OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md), [platform scheduling audit](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md) and [measurement framework](../.claude/skills/reachweaver/references/measurement.md).

## Executive decision and intended positioning

DEV is a strong **niche engineering authority** channel: tutorials, failure analyses, reproducible experiments and long-lived professional relationships. It is not a guaranteed viral-reach engine or a substitute for LinkedIn business development or GitHub project onboarding. Use DEV as one of a small number of maintained channels; evaluate qualified readers and actual relationships rather than follower count alone.

**Coherent technical specialty:** practical AI-agent reliability and verifiability in production infrastructure, including safe Kubernetes operations, human authorization, auditable agent actions, benchmarks and distributed AI systems. Secondary topic: privacy-preserving Linux productivity/accessibility where YazSes provides verifiable lessons. Each project stays correctly attributed; do not imply private NovaFabric material is public or claim institutional endorsement.

**Primary outcomes, in priority order:** (1) recurring substantive conversations with peers; (2) verified GitHub readers, issue authors, testers or accepted contributions; (3) legitimate research and consulting/collaboration inquiries; (4) eligible traffic to a helpful original tutorial or demo. Views, reactions, followers and impressions are *diagnostic*, not the final outcome.

## 1. What is verified about DEV discovery?

| Question | Official evidence | Action and limit |
| --- | --- | --- |
| How does the Relevant feed work? | DEV's [feed engineering description](https://dev.to/devteam/feature-update-feed-3go5) describes **freshness, relevance to followed authors/tags, popularity (comments/reactions), moderation feedback**. It dates from 2021 and described a changing ranking experiment. | Create reader-relevant posts, select accurate tags and genuinely discuss; **do not imply these are fixed 2026 weights**. |
| How do users discover people and topics? | [Customizing Your Feed](https://dev.to/help/customizing-your-feed) documents Relevant / Latest / Top, followed tags and authors. | Join topical conversations and follow the people you actually want to learn from. No claim of follow-back benefits. |
| How many tags? | [DEV editor guidance](https://dev.to/p/editor_guide/) and [first-post guidance](https://dev.to/devteam/writing-your-first-post-on-dev-3m13/) support up to **four** relevant tags. | Prefer a truthful mix of a broad, specialist and content-format tag; obey tag-specific rules. Four are a ceiling, not mandatory padding. |
| Is native scheduling possible? | [Writing, Editing and Scheduling](https://dev.to/help/writing-editing-scheduling) documents editor **Schedule Publication** and a date/time in the author's **local time zone**. | Schedule through DEV manually and verify its queue; ReachWeaver cannot currently schedule a DEV post on the site. |
| How do canonical links and series work? | [DEV writing help](https://dev.to/help/writing-editing-scheduling) documents original-domain canonical links, RSS import and article series. | Use `canonical_url` when **actually** cross-posting a pre-existing canonical original. Avoid duplicate/article flooding; canonical is not a search ranking guarantee. |
| Can APIs publish? | [Forem v1 API](https://developers.forem.com/api/v1) documents authenticated article draft/create/update endpoints. | **API existence is not ReachWeaver support**; integration, owner credentials, permission review, tests and deduplication remain unimplemented. |
| Are AI-generated community comments allowed? | [DEV AI guidelines](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/) explicitly ask members **not** to use bots or AI to generate comments (except grammar/translation/accessibility assistance). | **Human writes DEV comments and replies independently. Do not generate pasteable DEV comments, auto-replies or DM text.** |
| May AI write promotional articles to build a personal brand? | Same [AI guidelines](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/) require disclosed, fact-checked AI assistance and reject AI articles primarily to promote a business, build a personal brand or farm backlinks. | Write substantive engineering work for readers, not AI-generated clout campaigns; disclose real AI assistance where used. Human owner must stand behind every fact. |

**Evidence levels:** the official feed description is historical platform communication, not a reverse-engineered ranking model. The examples/cadences/times below are **operational hypotheses to test**, not provider-endorsed thresholds or guaranteed growth.

## 2. Audit the actual account before making changes

Public example account: https://dev.to/mskazemi ; some indexed articles include [AI agent failure](https://dev.to/mskazemi/i-recorded-my-kubernetes-ai-agent-failing-on-purpose-1ib2), [RBAC/linter failure](https://dev.to/mskazemi/our-linters-safe-autofix-would-have-silently-disabled-rbac-log) and [25-verifier experiment](https://dev.to/mskazemi/a-25-verifier-panel-measured-an-effective-size-of-100-lgg). These illustrate a strong original-evidence angle. **Do not use indexed follower/post totals as a current baseline**: inspect the signed-in dashboard and record an as-of timestamp instead. Review the brief YazSes listing for accuracy/completeness manually before deleting or replacing anything.

Profile quality checklist (human controls any actual edits):

- **Identity:** real name/consistent photograph; honest current expertise, location/availability only as comfortable publicly; no false institution/client endorsement.
- **One-line positioning:** solve a recognizable problem rather than listing 25 tools. Example *direction*, not an imposed bio: "Building verifiable AI agents for real infrastructure: Kubernetes, MLOps, safety gates and reproducible evaluation."
- **Proof paths:** one clean public portfolio/project homepage, GitHub profile, best reproducible article and a professional contact route supported by DEV's current profile UI. Check that links work. Do not put private telephone/email or client secrets into the repo.
- **Trust:** distinguish research previews from production products; show current project status and limitations accurately.
- **Reader orientation:** first three visible article titles should communicate a concrete engineering takeaway. Review outdated, duplicate or near-empty posts without assuming they are empty from search previews alone.
- **Consistency:** avatar, preferred technical themes and attribution should not contradict personal website, GitHub or LinkedIn. Audit rather than pretend to edit a DEV account.

Record private baseline: profile URL and audit date; published article IDs/permalinks; each article topic, tags, published date and native views/reactions/comments when visible; followers/tags if accessible; monthly collaboration opportunities; Google Analytics / website / GitHub referral baseline if permissioned. Missing values are **unknown**, never 0.

## 3. Following, commenting and being a real member

**Do follow relevant people selectively; do not mass-follow in the hope of algorithmic rewards.** Good targets include engineers publishing direct experiment artifacts, open-source maintainers, platform/SRE/MLOps practitioners, research colleagues and talented newcomers. Start with **5–10 genuinely relevant follows/week** as a workload budget, not a platform algorithm rule. Follow tags like `kubernetes`, `devops`, `ai`, `opensource`, `security`, `python`, `testing` or `architecture` only where they match the article and community rules.

**Human-only commentary process** (not an AI text-generation template):

1. Read the entire article and, where material, the cited code/source.
2. Identify one verifiable point you understand, one practical limitation or an unanswered technical question.
3. Write your comment in your own words, based on your experience. Add evidence/corrections respectfully.
4. Only include a repo link if it directly resolves the author's question and affiliation is transparent; otherwise leave the link out.
5. Return to respond to genuine follow-ups. Never script comments, sentiment, follow-back requests, reaction exchange, engagement pods or mass unsolicited DMs.

**Workload experiment:** baseline 3–5 meaningful comments *per week* and one substantial article per week; if time and discussion quality allow, scale toward 3–5 comments on active days. This is a **suggested human time budget**, not a DEV growth rule. Quality and repeat dialogue outrank volume. No AI-generated examples for posting to DEV: offer observation/question **criteria** only.

**Collaboration ladder:** useful public exchange → repeat technical discussion → optional specific invitation for a public issue/benchmark or talk → transparent opt-in contact outside DEV when welcome. Pitch concrete bounded collaboration, not "please star" requests. Track *counts and outcomes* privately, not people's personal details in this repository. No automated relationship scoring or unauthorized outreach.

## 4. Content engine: be useful before being promotional

A repeatable **human-authored engineering article** should answer:

1. What failed / was puzzling / mattered to engineers?
2. What environment, baseline and reproduction are necessary?
3. What code, measurements or committed artifacts show it?
4. Why did the obvious approach fail? Which trade-offs mattered?
5. What can a reader do differently today? What remains untested?
6. What *single* relevant invitation makes sense (discussion, issue, replication), without converting the article into an advertisement?

**Content pillar / project mapping**

| Pillar | Useful, evidence-checkable article direction | Project connection | Relevant readers |
| --- | --- | --- | --- |
| Safe production AI | Tool-execution gates vs prompt-level guardrails; incident outcome verification | KubeIntellect | SRE, platform engineers, AI infrastructure |
| Trustworthy multi-agent work | Reviewer correlation and false confidence, reproducibility, verifier-owned tests | idkmesh and published experiments | Agent framework engineers, security researchers |
| AI platform operations | Practical MLOps observability or deployment trade-off demonstrated with permitted public code | Public experiments, ExaMLOps lessons, NovaFabric only with approved disclosure | MLOps/HPC/cloud teams |
| Privacy and accessible workflows | Reproducible offline Linux dictation or hands-free UX test | YazSes | Linux/FOSS/accessibility and QA contributors |
| Methods and research | How to reproduce an energy/evaluation measurement; uncertainties and negative results | Public papers / authorized data | Researchers and performance engineers |

**Example title angles (verify every premise before publication):** "Why a successful kubectl command did not fix the outage"; "When 25 verifiers behave like one"; "The safe linter fix that would have broken an access check"; "A reproducible way to measure an AI agent's energy footprint". These are *editorial hypotheses*, not documented future results.

Use a clear precise title; working screenshots/figures only when supported and authorized; code snippets readers can execute; linked original sources; short explanatory sections; one bounded call for technical feedback. Avoid four project announcements in one week. If a project is only a preview, say so. Avoid copying competitor articles or publishing unverifiable synthetic benchmarks.

For **AI involvement**: independently verify work; disclose assistance in an appropriate location if AI contributed; do not write AI-produced promotional articles primarily for clout/SEO. If the content cannot stand without brand promotion or the owner cannot verify its substance, **do not publish it**.

## 5. Editorial production and native scheduling

**Authoring mode:** plan/research/verify → human writer creates article → technical and policy review → optional approved grammar assistance (with applicable disclosure) → manually create a DEV draft → optional DEV native schedule → separately verify actual published permalink. Store drafts locally only when the author has permission and avoids confidential data.

Article checklist:

- [ ] One named audience and a useful problem, not a marketing announcement
- [ ] Facts traced to public/approved code, paper, experiment or first-party observation
- [ ] Limitations, prerequisites and reproducible steps included
- [ ] Rights and confidential affiliation checked
- [ ] Title/description, accessible figures and up to four accurate tags
- [ ] Relevant series if applicable; canonical URL only when an original already exists
- [ ] DEV AI guidelines and copyright checked, disclosure where needed
- [ ] Link is helpful and not primarily a backlink-building strategy
- [ ] Human reviewed exact text in the DEV editor; no automatic publication assumed

**Native DEV scheduling (verified as a platform capability, not tested for this account):** Open a draft, click the editor's hexagon/gear near Publish, choose **Schedule Publication**, enter date/time in *the account's local time zone*, and verify it appears in the owner dashboard. When actually live, save the native permalink and publication time. A local ReachWeaver planned date is **not** a DEV queue receipt.

**Time-slot pilot, Europe/Rome:** candidate A = Tuesday 14:00–16:00; B = Thursday 14:00–16:00; C = Sunday 17:00–19:00. These come from a practical Europe/US-overlap hypothesis, **not a measured DEV optimum**. Alternate over 6–8 *original* articles (never repeatedly republish the same post); track topic, followers, article length and age as confounders. Avoid claiming timing caused any observed difference. If target readers are elsewhere, localize test windows to their IANA time zone and record UTC conversion/DST. Recheck timing guide before scheduling.

**API distinction:** Forem documents article-create/update/draft endpoints, but ReachWeaver currently has **NO DEV authentication, draft creation, calendar queue, comments, follows or metrics API adapter**. Do not use the Bluesky/Mastodon/Telegram publisher pretending it can send to DEV. Never ask for a DEV token in a chat or store it in Git.

## 6. Thirty-day and ninety-day execution

| Interval | Concrete output / owner activity | Measurement |
| --- | --- | --- |
| Days 1–3 | Audit @mskazemi, correct broken profile paths or inaccurate short posts, choose **one** flagship engineering pillar and two relevant tags/author circles | Timestamped baseline and list of fixes; no fabricated profile stats |
| Week 1 | Publish one human-owned reproducible engineering article, follow 5–10 fitting authors, contribute 3–5 genuine human comments | Native permalink, source proof, discussion quality |
| Week 2 | Publish an adjacent **distinct** practical tutorial; continue relationships and answer replies | Repeat participants, 24h/7d article metrics where available |
| Week 3 | Publish a comparison or honest negative result, invite replication on one well-defined public issue | Genuine issue authors, useful tests, collaborations |
| Week 4 | Publish one new technical case study; analyze 6–8 prior/new posts if available, otherwise clearly record insufficient sample; plan next month | Views *by article age*, qualitative discussion, qualified referrals, hours spent |
| Days 31–60 | Continue one quality article/week; build a 3–5-article **series**, collaborate on one technical tutorial or reproducible benchmark if mutually agreed | Returning engineers, backlinks/referrals as context not the purpose, accepted contributions |
| Days 61–90 | Double down on one proven content pillar, guest technical discussions if invited, stop time sinks, revise positioning from observed outcomes | Growth in meaningful relationships and attributable downstream work; never guaranteed "influencer" rank |

**Sensible time budget:** 2h evidence/article writing + 1–2h research/discussions + 30min review per week, flexed based on actual technical work. If writing credible experiments takes longer, decrease volume instead of using low-quality AI content.

## 7. Measurement: from attention to real opportunity

Use the same observation ages (+24h, +7d, +28d when possible), native definitions and source dates. The [blank logging template](../examples/dev-performance-log.csv) is **manual**, not a data export integration. Collect metric snapshots from DEV dashboard, permissioned Google Analytics, and GitHub traffic only when actually accessible.

- **Leading:** useful unique article views; substantive human comments (not all comments are equal); replies from returning practitioners; contextual saves/reactions if exposed; follower changes with time and platform caveats.
- **Downstream:** owned website referrals that can be reasonably attributed; GitHub referrers, issue reports, merged PRs, opted-in speaking/research/consulting requests. Attribution is often approximate and is not proof of causation.
- **Work efficiency:** publish/research/discussion hours; qualified conversations per hour, with a consistent definition established before counting.
- **Moderation and reputation:** removals, self-promotion complaints, corrections, policy breaches; any breach halts the relevant automation/planned promotion.
- **Not available?** Write `unknown` / blank with source status. Never convert missing impressions into 0 or compare cross-platform metrics as identical denominators.

### Weekly learning review

Record exactly: (1) what was **published**, with native URL; (2) what was merely drafted/scheduled locally; (3) what worked and what didn't; (4) genuine technical feedback; (5) useful website/repository outcomes; (6) elapsed owner time; (7) one next measurable hypothesis; (8) account/policy/permission blockers. One good engineer adopting the project can outweigh thousands of indifferent impressions.

## 8. Claude Code / ReachWeaver behavior contract

When someone asks `/reachweaver audit my DEV profile`, the skill must:

1. Confirm account identity, fetch public profile and owned post URLs when accessible, distinguish live findings from cached snippets, ask for private dashboard metrics only if necessary; never invent current totals.
2. Inspect technical positioning, links, posts, topic fit, reader utility, tags and project truth; produce **ranked, actionable fixes** and a safe owner-edit checklist.
3. Offer a community **research queue** (topics/authors to read and criteria for substantive participation), **not** generated comments, scripted follows or DMs.
4. Offer human-writer outlines / evidence cards and an editorial calendar; only assist with DEV article content within its AI disclosure/anti-promotion rules. Do not fabricate publications or propose AI-brand-clout articles.
5. Label everything as **research**, **draft**, **candidate slot**, **manually scheduled (owner verified)** or **published (native permalink observed)**. No unsupported automation.
6. Return a 30-day plan and measurement rubric with missingness; avoid fixed "best posting time" claims.
7. Refer to the [portable skill DEV reference](../.claude/skills/reachweaver/references/dev-community.md) for the concise operational workflow.

## Sources, provenance and recheck rule

Official/primary, assessed 2026-10-08:

- DEV [Customizing Your Feed](https://dev.to/help/customizing-your-feed)
- DEV [Feature update: Feed](https://dev.to/devteam/feature-update-feed-3go5) (**historical**, not current algorithm weights)
- DEV [Writing, Editing and Scheduling](https://dev.to/help/writing-editing-scheduling)
- DEV [Editor Guide](https://dev.to/p/editor_guide/)
- DEV [First-post guidance](https://dev.to/devteam/writing-your-first-post-on-dev-3m13/)
- DEV [AI-assisted article and commenting guidelines](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/)
- [Forem official API v1](https://developers.forem.com/api/v1)

Recheck official policy and actual account UI immediately before any feature implementation or manual schedule. This document recommends no off-platform scraping, no login bypass, no comment/follow automation and no unapproved publication. If platform rules become stricter, platform rules take precedence.
