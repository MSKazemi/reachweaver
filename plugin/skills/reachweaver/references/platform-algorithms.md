# Platform algorithms, discovery surfaces and native scheduling: operational index

**Read this for:** algorithm questions; strategy by social platform; how to increase visibility without manipulation; which channels can publish later; whether an official ranking signal is verified. Snapshot: **2026-10-08**. This document does NOT claim to reproduce proprietary ranking algorithms or to know fixed weights. For candidate time windows and calendar decisions read [timing-intelligence.md](timing-intelligence.md); for live adapter truth read [publishing.md](publishing.md).

## Evidence rule

Tag all ranking statements as **officially described**, **observational sample** or **testable hypothesis**. Cite a current source + review date. Official help frequently reveals families of signals, NOT weights. Aggregate engagement estimates from Buffer, including its [52M+ post 2026 review](https://buffer.com/resources/state-of-social-media-engagement-2026/), describe that dataset, not guaranteed performance on the user's posts. Viral success cannot be optimized deterministically. Encourage original audience value, avoid engagement bait, mass repetitive posting, manufactured reactions and abusive automation.

## Platform-by-platform

### LinkedIn (technical work, papers, open-source adoption)

- **Officially described:** [LinkedIn Engineering March 12, 2026](https://www.linkedin.com/blog/engineering/feed/engineering-the-next-generation-of-linkedins-feed) explains feed relevance based on member interests, profile professional context, engagement history, content topic, timeliness and trust. The recommender considers evolving sequences of behavior; follower count alone is not an adequate proxy.
- **Design test:** one precise problem, outcome and reproducible proof; expert commentary with authentic replies. Target the reader's professional need, not empty “agree?” engagement prompts. Attribution to company/university must be authorized.
- **Native scheduling:** member 10 min–3 months ([official help](https://www.linkedin.com/help/linkedin/answer/a1347212)); Page admin 1h–3 months ([official help](https://www.linkedin.com/help/linkedin/answer/a548192/scheduled-posts-for-linkedin-pages)); exclusions differ. External API/member/organization access requires approved scopes; ReachWeaver still manual only.

### Instagram (demos, visuals, shareable education)

- **Officially described:** [Meta Jan 2026 report](https://about.fb.com/news/2026/01/2026-ai-drives-performance/) says Instagram original content featured more in US recommendations. Personalization and visual quality matter; there is no verified universal “share count” or completion threshold.
- **Design test:** original screen demo, captions, clear value in opening seconds, compact how-to carousel, user-relevant shares/saves, readable on mobile. Do not copy other people's media without rights.
- **Native scheduling:** supported in app and Meta Business Suite for eligible accounts and post types; feature/horizon/Story limitations depend on account/UI. Verify actual UI before claiming a queued Reel/Story. ReachWeaver has no Meta media API adapter.

### Facebook (communities, software explainer videos)

- **Officially described:** [Meta March 2026](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/) rewards original posts and deprioritizes duplicates/minor alterations in Feed and Reels.
- **Design test:** original case studies, short clips and genuinely useful community participation. Groups may have their own moderation rules.
- **Native scheduling:** Pages via Meta Business Suite; Group moderator/admin tools vary; do not assume ordinary personal-profile API posting. ReachWeaver manual only.

### Threads (conversation-led short professional insights)

- **Officially described:** [Meta March 2025 / Oct 2025 update](https://about.fb.com/news/2025/03/new-threads-features-more-personalized-experience-you-control/) reported that topic-tagged posts generally got more views *in their sample*, not proof of causation.
- **Design test:** relevant topic label, interesting standalone observation, real discussion, responsive human replies.
- **Native scheduling:** ordinary posts up to 75 days per [2025 launch report](https://www.theverge.com/2025/1/23/24350639/threads-schedule-posts-launch-markup); **not replies**. Recheck eligibility/time horizon. ReachWeaver manual only.

### X (rapid engineering conversations, links, release notes)

- **Officially described in [2026 public code](https://github.com/xai-org/x-algorithm):** For You blends followed accounts with discovered accounts and predicts likely interaction actions, dwell and feedback, with visibility filtering and diversity mechanisms. **Weights multiply predicted action probabilities, NOT raw reaction counts**; never infer a fixed “one report cancels N likes” hack. Production data and serving configurations are not fully exposed.
- **Design test:** concise first-person learning, evidence link, follow-up visual/thread, authentic responses and selective real-time discussion, no mass near-duplicate cross-posts.
- **Native scheduling:** supported via official scheduling surfaces such as [X Ads Composer](https://help.x.com/en/business-and-advertising/scheduled-tweets), [Media Studio](https://help.x.com/en/using-x/media-studio-faqs) or eligible web composer. Third-party API pricing/scope must be separately assessed. ReachWeaver manual only.

### TikTok (short software demonstrations)

- **Officially described:** [TikTok Help](https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content) identifies viewer interactions, content information and user information; weights vary across feeds and over time.
- **Design test:** show actual working result early, clear screen legibility and authentic voice, retention/rewatch hypothesis tested with native analytics.
- **Native scheduling:** TikTok's [official Video Scheduler](https://ads.tiktok.com/business/en/blog/introducing-video-scheduler-now-you-can-plan-tiktoks-in-advance) documents 15 minutes to 10 days for eligible account and web upload. Third-party direct posting needs separately audited API access; unavailable in ReachWeaver.

### YouTube and Shorts (evergreen discovery/tutorials)

- **Officially described:** [YouTube's FAQ](https://support.google.com/youtube/answer/141805?hl=en) explains relevance and watch time; publication time **not known to impact long-term performance**, though early viewers/Lives/Premieres may benefit from timing.
- **Design test:** searchable and accurate title/thumbnail, audience/topic fit, honest visuals, retention analysis. Test Shorts separately from long videos.
- **Native scheduling:** [YouTube Studio](https://support.google.com/youtube/answer/1270709?hl=en) has date/time/timezone publish scheduling. Current ReachWeaver has storyboards only, no video publishing adapter.

### Pinterest (evergreen visual search, diagrams)

- **Official ranking precision:** No up-to-date public coefficients verified. Search query intent and relevance are reasonable hypotheses to test, not secretly known weights.
- **Design test:** purpose-built technical diagrams, legible titles, searchable descriptions, appropriate board, valuable destination and rights-compliant original visuals.
- **Native scheduling:** [Pinterest Help](https://help.pinterest.com/en/business/article/schedule-pins) business account, standard Pins, up to 30 days and 10 queued. ReachWeaver manual only.

### Reddit (subreddit-specific discussion)

- **Discovery and governance:** community rules, flair, moderator action, votes, freshness and selected feed sorting differ. Do not promise “front page” effects.
- **Design test:** thoughtful relevant troubleshooting, reproducible guide, ask moderator where self-posts are allowed, transparent conflicts. Use human replies, not mass distribution.
- **Native scheduling:** [Reddit Help](https://support.reddithelp.com/hc/en-us/articles/15484443169556-Scheduled-and-Recurring-Posts), updated Oct 6 2026: reserved for moderators with Manage Posts & Comments on the subreddit; images/video post scheduling unavailable. Normal creator submissions remain human/manual in ReachWeaver.

### Bluesky (open developer network and niche custom feeds)

- **Officially described:** [Bluesky custom feeds](https://bsky.social/about/blog/7-27-2023-custom-feeds) let users select diverse ranking and discovery algorithms; Following is chronological. No single “Bluesky algorithm” governs every feed.
- **Design test:** participate in relevant developer custom feeds, useful posts, legitimate conversation; timely publication especially relevant to chronological followers.
- **Native scheduling:** no general built-in scheduled-post queue verified; use an authorized third-party tool or existing **text-only** ReachWeaver adapter with a reliable worker and credentials. Live account NOT tested.

### Mastodon (fediverse professional niches)

- **Architecture:** decentralized instances with differing timelines and discovery, rather than one global ranking authority; use relevant hashtags, alt text and replies. Treat any “algorithmic weight” claim as unverified.
- **Native scheduling:** [Mastodon official API](https://docs.joinmastodon.org/methods/statuses/) supports `scheduled_at` at least five minutes ahead; UI may depend on client/instance. Existing ReachWeaver text-only adapter stores due UTC locally and requires a worker; it does NOT yet use remote server scheduling. No user auth verified.

### Telegram (existing community and notifications)

- **Discovery:** reaches channel subscribers via notifications/chat list and organic forwarding; NOT a general personalized For You recommendation feed. Frequency and subscriber geography matter.
- **Design test:** subscriber-useful release notes, opt-in notification cadence, forwardable summaries. Avoid forced joins or unsolicited DMs.
- **Native scheduling:** [Telegram](https://telegram.org/blog/6-years) supports scheduled messages in the user app. Bot API text messages are separate; ReachWeaver sends only if a trusted due-time worker executes and permissions exist. Mock-tested, not user-tested.

### Product Hunt (one-off product launches, not a standard social feed)

- **Discovery:** daily launch cycles and genuine maker feedback. Do not seek guaranteed ranking through vote solicitation.
- **Scheduling:** [official help](https://help.producthunt.com/en/articles/2724119-how-to-schedule-a-post) allows selecting a launch date within 30 days; product feed operates on Pacific-time daily cycles. [PH changelog](https://www.producthunt.com/p/producthunt/changelog-september-2-changelog-draft-launches-and-scheduling-updates) describes midnight-Pacific launch with day selection, not arbitrary time choice. Verify current UI. Not currently in ReachWeaver's 12 adapters.

### Hacker News (engineering curiosity; explicitly not a bot channel)

- **Rules:** [HN guidelines](https://news.ycombinator.com/newsguidelines.html) prohibit automated submissions and generated submission text, discourage primarily promotional posting and soliciting votes.
- **Handling:** a human writes/submits a genuinely relevant story manually and participates authentically. Skills may prepare factual research notes for private review, but must never submit it automatically or produce a ready-to-paste model-written HN submission.

### DEV Community / DEV.to (long-form technical developers)

- **Official scheduling:** [DEV Help](https://dev.to/help/writing-editing-scheduling) documents a built-in editor queue via hexagon icon → Schedule Publication, expressed in the user's local timezone. Articles are not auto-published by ReachWeaver; the plugin catalogue supports only manual source-checked Markdown drafts and handoff.
- **Discovery:** readers find articles via topical tags, feeds, followers, and helpful discussion. No public deterministic ranking weights verified. Prioritize reproducible tutorials, useful code/diagrams, appropriate relevant tags and accurate canonical URLs. Test weekday audience-local hours, but label as a hypothesis.
- **Do not** claim DEV lacks native scheduling. Do not treat an article copy placed in a local queue as remotely scheduled.

### Hashnode (technical blogging and independent developer search)

- **Official scheduling availability:** the [June 2026 Hashnode Pro release](https://hashnode.com/changelog/2026-06-11-introducing-hashnode-pro) includes scheduled publishing under paid Pro features and a gated GraphQL API. A [2022 Hashnode announcement](https://townhall.hashnode.com/introducing-article-scheduling-feature-for-all-hashnode-blogs) described wider availability, but should not override the newer plan-specific offering; verify account UI before making promises.
- **Discovery:** develop original, SEO-friendly explanations with clear titles, canonical-source care, authoritative first-hand code and topical relevance; no public ranking formula verified.
- ReachWeaver permits long-form manual-only Markdown drafts and suggested handoff hours, not a Hashnode Pro subscription or API connection.

### Regional/community platforms (needs provider verification)

Discord, Indie Hackers, Peerlist, Bilibili, Zhihu and OSCHINA are **manual distribution channels** in this skill until official scheduling/account permissions have been confirmed. Avoid inventing automated queue controls. For Bilibili/Zhihu/OSCHINA use China-local audience timezones and original, appropriately localized material only when ready to review publication manually.

## Publication policy summary

Always separate **research**, **draft**, **proposed schedule**, **native platform queue receipt**, **local ReachWeaver scheduled record**, **actual remote publication**, and **metric snapshot**. These are different stages, each with different evidence. Avoid presenting a business platform scheduling UI as evidence of available API scopes for a Claude Code agent. See [publishing.md](publishing.md) for owner-assisted auth, preflight and no-blind-retry rules.

## Refresh protocol

At use time, inspect the platform's current official docs; at least monthly refresh the evidence ledger. Log last verified date, content types, plan eligibility, queue horizon, native UI, API conditions and ranking signal version (especially X). Compare first-party creator/account insights and outcomes to population baselines, never fabricate a best time or an unobserved performance gain.
