# Open-source social media skills and automation research

**Reviewed:** 2026-10-08. **Goal:** collect the strongest ideas from the existing skill ecosystem into an original, secure, executable Claude Code workflow, without blindly copying code or assuming every README claim is true. This is a researched shortlist plus one 106-skill corpus, **not an exhaustive census of every project on GitHub**. Sources change; verify their latest release/license before reuse.

## Comparative landscape

| Source | What it contributes conceptually | Opportunity or remaining dependency addressed by ReachWeaver | Code/license decision |
|---|---|---|---|
| [social-media-skills/skills](https://github.com/social-media-skills/skills) | **106 skill modules**, brand voice, audience research, hook-writing, visual/video, platform playbooks, campaign calendar, community, publishing and reporting | Combine the workflows with a persistent reviewed outbox, receipts and provenance-aware measurement rather than relying on writing guidance alone | Upstream README says MIT. We independently authored instructions and CLI rather than bulk-copying its prose |
| [scrollmark/social-skills](https://github.com/scrollmark/social-skills) | Reads tone/subtext, short-form narrative and video production grammars | Add substantive technical proof, approval, tracking and cross-platform feedback | Inspiration, no code copied |
| [namddp/social-media-agent-skills](https://github.com/namddp/social-media-agent-skills) | Cross-platform voice, source mining, drafts and local validation; explicitly **no unattended publishing** | Provide optional permitted API execution in addition to offline lint and review | Inspiration, no code copied |
| [guyaga/claude-code-social-media-skill](https://github.com/guyaga/claude-code-social-media-skill) | Claude skill driving many platforms via Upload Post API | Keep execution trace and local safety independent of a paid third-party posting gateway | Do not copy, optional future adapter |
| [publora/skills](https://github.com/publora/skills) | 10-network MCP publishing, managed account authorization and scheduling | Separate campaign planning/analytics and independent durable records; document hosted service dependency | Do not copy, optional MCP integration |
| [Schmedu/social-post-skill](https://github.com/Schmedu/social-post-skill) | Brand-aware draft / schedule / edit / revert commands mapped to Social Media Manager MCP | Build explicit local approval gates and distinguish real publish receipts from draft objects | Upstream reports MIT; no code copied |
| [Batsirai/social-media-agent](https://github.com/Batsirai/social-media-agent) | Buffer/Postiz scheduling adapter and content calendar | Treat providers as swappable; avoid assuming a free plan or access to every channel | No code copied; pricing/access recheck needed |
| [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) | Rich self-hosted scheduler, media library, analytics, API/MCP/CLI and team collaboration | Prefer **integration** over reimplementing full social suite; keep strategy/provenance outside backend | AGPL-3.0. Do not paste internal implementation into this MIT repository |
| [borghei/Claude-Skills social-media-manager](https://github.com/borghei/Claude-Skills/blob/main/marketing/social-media-manager/SKILL.md) | B2B SaaS calendars, pillars, community routines, crisis planning | Add deterministic actions and complete auditability; avoid inflexible frequency benchmarks | File declares MIT + Commons Clause; do not reproduce its text |
| [borghei/Claude-Skills social-content](https://github.com/borghei/Claude-Skills/blob/main/marketing/social-content/SKILL.md) | Platform-native writing, hook patterns and repurposing | Source-backed proofs, measurement link and brand separation | File declares MIT; no code copied |
| [xcrrr/claude-skills social-media](https://github.com/xcrrr/claude-skills/blob/main/skills/writing/social-media/SKILL.md) | Writing for threads, LinkedIn and Instagram | Add operational security, scheduling and feedback loop | File declares MIT; no code copied |
| [alirezarezvani/claude-skills social-media-manager](https://github.com/alirezarezvani/claude-skills/blob/main/marketing-skill/skills/social-media-manager/SKILL.md) | Basic calendar, content pillars, growth analytics | Treat suggested growth thresholds as hypotheses, not universal cross-platform targets | Inspiration, no code copied |
| [claude-office-skills/skills twitter automation](https://github.com/claude-office-skills/skills/blob/main/twitter-automation/SKILL.md) | X scheduling, threads, analytics tasks | Treat unsupported tool names, unverified posting-time advice and rigid cadence as not-yet-verified; enforce execution checks | Inspiration, no code copied |

**Important distinction:** The last column describes our implementation/reuse decision; it does not declare that every upstream project has security bugs. Comparisons are against a multi-account Claude-driven system with the user's specific requirements.

## Comprehensive 106-skill source catalog

The following names were enumerated from the source's GitHub `skills/` directory on 2026-10-08 using the GitHub contents API. Names are catalog entries, **not** 106 bundled/vendored implementations and **not** endorsements of their current technical correctness. They are indexed here so future maintainers can selectively research specialists rather than blindly install everything in a privileged runtime.

**Foundation, discovery, research, planning:**
`brand-profile`, `voice-builder`, `writing-style-and-tone`, `audience-research`, `social-strategy`, `content-pillars`, `goals-and-kpis`, `profile-optimization`, `idea-generation-and-ideation`, `content-research-and-sourcing`, `competitor-analysis`, `viral-reverse-engineering`, `content-calendar`, `batch-content-plan`, `campaign-and-launch-planning`, `seasonal-and-moment-marketing`, `trend-jacking`, `data-and-original-research`.

**Writing, creative types, storytelling:**
`hook-writer`, `caption-writer`, `linkedin-post-writer`, `thread-writer`, `threads-post`, `text-post-and-microblog`, `carousel-writer`, `story-writer`, `short-form-video-script`, `reels-script`, `tiktok-script`, `scripting-and-storyboarding`, `reply-and-comment-writer`, `educational-content-and-how-to`, `storytelling-and-narrative`, `contrarian-and-opinion`, `behind-the-scenes-and-founder`, `before-after-and-transformation`, `social-proof-and-testimonials`, `listicle-and-roundup`, `meme-and-culture`, `interactive-content`, `livestream-and-realtime`, `podcast-and-audiograms`, `email-and-newsletter`.

**Platform specializations:**
`instagram-growth`, `instagram-seo`, `instagram-reels-publishing`, `tiktok-growth`, `tiktok-photo-mode`, `tiktok-video-publishing`, `linkedin-growth`, `linkedin-company-pages`, `x-growth`, `threads-growth`, `facebook-strategy`, `facebook-groups`, `pinterest-growth`, `pinterest-seo`, `pinterest-pin-design`, `youtube-long-form`, `youtube-shorts`, `youtube-publishing-and-metadata`, `thumbnail-design`, `reddit-marketing`.

**Visual, audio and production:**
`design-and-templates`, `image-prompt`, `quote-cards-and-text-graphics`, `infographic-and-data-viz`, `ai-image-editing`, `ai-video`, `ai-voiceover`, `ai-music-and-sound`, `canva`, `capcut`, `captions-and-clipping`, `descript`, `flux`, `heygen`, `ideogram`, `kling`, `luma`, `nano-banana`, `opus-clip`, `runway`, `suno`, `synthesia`, `talking-head-and-piece-to-camera`, `veo-3`.

**Distribution, operations, growth, measurement:**
`hashtag-strategy`, `social-seo`, `ai-search-optimization`, `cross-platform-repurposing`, `content-recycling`, `link-in-bio-and-traffic`, `lead-magnets-and-funnels`, `social-selling-and-dm`, `collabs-and-cross-promotion`, `ugc-and-influencer`, `creator-monetization`, `engagement-routine`, `community-management`, `crisis-and-moderation`, `scheduling-and-queue`, `platform-specs-and-validation`, `analytics-and-reporting`, `content-audit`, `experimentation-and-ab-testing`.

## Design decisions derived from the research

| Reusable lesson | Implementation in ReachWeaver | Verification state |
|---|---|---|
| Progressive skill specialization | One slim router SKILL.md with 5 on-demand references and three starter assets | Implemented, skill validator pending |
| Identity-first content | Voice/brand card template and separation rules | Implemented as guidance; no profile auto-extraction yet |
| Verified creative, native repurposing | Evidence and format protocols, conservative lint for 12 platform draft families | Implemented as guidance + basic lint; qualitative output still requires editor judgment |
| Actual publishing with receipts | Text-only adapters for Bluesky, Mastodon, Telegram (Update 2026-10-09: and LinkedIn member text, plus an optional Postiz backend); CLI stores remote IDs | Mock-tested; live validation pending, no real receipt |
| Avoid ghost-posts or duplicates | State machine, dry-run opt-in, atomic claim, needs_review and reconciliation | Unit-tested locally |
| Privacy and access control | Ignored JSON mapping, environment-only credentials, user login/MFA, safe API errors | Unit-tested partly; no OAuth vault |
| Attention learning | Latest-per-post native export snapshots; metric-specific coverage | Unit-tested; automated fetch pending |
| Crisis / kill switch | Pause/resume command and event log | Unit-tested |
| Keep provider choice open | Native adapter boundary and separate optional Postiz/MCP roadmap | Architecture defined; external adapters pending |
| Verify platform capability | Dated platform matrix and checklist per adapter | Documented; no 2026 API apps approved here |

## Current platform restrictions relevant to architecture

- **Bluesky**: official documentation describes `app.bsky.feed.post` records and UTF-8 byte offsets for rich-text facets: https://docs.bsky.app/docs/tutorials/creating-a-post
- **Mastodon**: `POST /api/v1/statuses` accepts bearer tokens and `Idempotency-Key` (stored for up to one hour): https://docs.joinmastodon.org/methods/statuses/
- **LinkedIn**: versioned Posts API with account-specific scopes, content-type distinctions and required headers; implement only after receiving app approval: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
- **TikTok**: Direct Post requires approved `video.publish` scope; unaudited clients restrict posts to private visibility: https://developers.tiktok.com/docs/en/content-posting-api-get-started
- **YouTube**: videos uploaded by unverified API projects created after 2020-07-28 are private until compliance audit: https://developers.google.com/youtube/v3/docs/videos/insert
- **Claude Code**: project and personal skills and invocation model: https://code.claude.com/docs/en/skills

## Decision on licenses and aggregation

Do not vendor all 106 skill directories or install third-party publishing scripts merely because they are public. Even permissive source has attribution and maintenance costs; some components have differing license obligations. The approach used here is **conceptual synthesis + original code + citations + minimal external runtime dependencies**. Evaluate each future imported module for license, correctness, privilege footprint and security against its actual upstream commit before inclusion.

## Follow-up evaluation program

1. Challenge with real user prompts (new release, multilingual paper, account recovery, platform-specific format, analytics anomalies).
2. Test hallucination/no-source/no-permission cases and scripted action limits.
3. Add agent evals of high/medium/low quality posts against a human-reviewed rubric; do not overfit to token heuristics.
4. Perform scheduled quarterly checks of provider APIs, account-scoped permissions, social publishing rules, licensing and skill catalogue drift.
5. Compare campaigns on real first-party metrics using matched post age/cohorts, rather than promising a universal best time/algorithm trick.
