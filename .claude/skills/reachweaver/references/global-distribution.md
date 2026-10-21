# Global and regional growth: HN, Asia, Africa and scheduling

**Entry condition:** User asks to grow worldwide, rank social channels, reach India/China/Japan/SEA/Africa, fix flagged Hacker News launches, optimize post time or schedule publications. For full research and platform-by-platform table in the repository, read docs/GLOBAL-DISTRIBUTION-2026-10-08.md. If the skill is installed standalone and docs/ is unavailable, use this reference and verify live policies at execution.

## Reproducible offline market selection

The source repo ships `reachweaver/distribution_registry.json`, and the standalone skill bundles an identical snapshot at `scripts/reachweaver/distribution_registry.json`. Use `RW distribution-plan --region india --goal adoption --json` (`RW` is the command prefix from SKILL.md; it works in a checkout and in a skill copied into another project). Supported goal labels: `contributors`, `adoption`, `awareness`, `collaboration`. Regional markets include China, India, Japan, South Korea, Taiwan, Thailand, Indonesia, Philippines, Vietnam, Russia, Nigeria, Kenya, South Africa, Ghana, Egypt, Morocco and Brazil. A global region cannot produce a time proposal without a target IANA timezone.

Each channel in a plan carries `checked_on` and `freshness` (`fresh`, `aging` after 60 days, `stale` after 120). Never present an aging or stale entry as current: say when it was checked and re-verify it at the official source first. `RW registry-age --registry distribution` lists every such fact.

**Every result is explicitly an offline hypothesis, with no account connection or scheduled post.** The registry records native UI features and platform API separately from current ReachWeaver delivery; only the Bluesky/Mastodon/Telegram/LinkedIn-member text adapters are enabled, and only with mocked tests. Do not mistake a scheduled local-time proposal for a provider queue receipt. Two test windows are synthetic controls for experimentation; choose real first-party audience behavior over fixed generic posting hours.

**Correction from primary source:** DEV Community provides a native article scheduler in its editor: https://dev.to/help/writing-editing-scheduling . This does not confer a ReachWeaver DEV publishing integration.

## Routing and priority

1. Identify project, actual supported product features, evidence links and installed media, target market, conversion goal and owned accounts. For YazSes do not confuse a video demo with a connected YouTube account.
2. Mark existing content as VERIFIED (actual URL + native receipt), OWNER-REPORTED (e.g. prior LinkedIn/DEV/YouTube/GitHub/Snapcraft activity), or UNKNOWN. User claims are useful context, not third-party-verified receipts.
3. Prioritize existing conversion surfaces (GitHub, store, website, YouTube, DEV, LinkedIn) ahead of 20 unmaintained social accounts. For Linux/FOSS discussion, consider community-approved Reddit, Bluesky, Mastodon. For visual discovery, prepare distinct Shorts/Reels/TikTok demonstrations.
4. Expand geographically by platform fit, not total world population:
   - **India:** For developer adoption prefer YouTube, LinkedIn, DEV, Hashnode and Peerlist. For India-language awareness trial ShareChat, Moj, Josh, Chingari and Hipi with truthful, original short-video demos and fluent human language review. Public App is only for authentic local updates; Rooter only for gaming-oriented content; Pratilipi for original storytelling. Kutumb and LocalCircles are human-only forums, and Arattai Channels are human-only opt-in owner broadcasts. All these regional additions have `manual_only` ReachWeaver delivery and `unverified` native scheduling; no extra platform account or API access established. Do not suggest Koo (closed 2024) or blocked TikTok in the India plan without a current legal review.
   - **China:** Bilibili, CSDN, Juejin, Zhihu, OSCHINA; authorized Gitee mirror if maintainable; optionally WeChat/Douyin/Weibo/Xiaohongshu only with permitted accounts and quality localized content. Prepare each post with `manual-export --platform <id>` (China-aware package: sourced limits, topic syntax, AI and ad declarations); the owner posts it by hand.
   - **Japan:** Qiita/Zenn, X, YouTube.
   - **Southeast Asia:** Indonesia/Philippines pilots on YouTube, TikTok, Instagram and legitimate developer communities; extend country by country.
   - **Africa:** Start with specific communities in Nigeria, Kenya, South Africa, Ghana, Egypt or Morocco on YouTube, LinkedIn, Facebook and opt-in developer spaces.
5. Never claim YazSes recognizes a regional language merely because captions/blog posts have been translated. Human review translations, technical terminology, culture, license and laws.
6. Keep **community answers** separate from **marketing broadcasting**; no automatic copying of posts into subreddits, Facebook Groups, Discord servers, Telegram/WhatsApp groups.

## Hacker News: mandatory exception to normal content generation

Official live references:
- https://news.ycombinator.com/showlim — temporary restriction of Show HN submissions, seen 2026-10-08
- https://news.ycombinator.com/newsfaq.html — meaning of flagged vs dead
- https://news.ycombinator.com/newsguidelines.html and https://news.ycombinator.com/showhn.html — HN submission/conduct rules
- https://news.ycombinator.com/item?id=22336638 — moderator's 2026-03-28 explicit warning about **any LLM-written or LLM-edited HN text**

**Override normal post-writing flow:** HN-facing title, body and comments must be written and edited by the human. Do not generate or polish them via ReachWeaver. Offer research, a factual checklist, a technical evidence inventory, and diagnostics on actual flagged submission URLs instead. Do not auto-submit, schedule HN posts, recommend delete/repost circumvention, vote coordination, additional identities or booster comments. A genuine Show HN should be nontrivial and directly usable (not merely a landing page). A past flag does not identify the actual cause without link/status/context.

**Hashnode scheduling precision:** Hashnode announced future scheduled publishing as a **Hashnode Pro** feature on June 11, 2026 ([official changelog](https://hashnode.com/changelog/2026-06-11-introducing-hashnode-pro)). This requires the appropriate subscription and does not confer any ReachWeaver Hashnode API integration. DEV Community provides native scheduled articles in its editor without an integrated ReachWeaver publisher.

## Scheduling is three separate questions

1. **Does the platform itself offer a schedule UI?** Official examples: LinkedIn posts (with exclusions), YouTube Studio, Product Hunt scheduled launch (not routine post), Telegram scheduled messages, and eligible TikTok web scheduler. X has scheduling tools. Other sites require exact account/format checks. Never guess.
2. **Can a permitted third-party API/scheduler send on this account?** Requires scopes, budget/terms, consent, and delivery tests; may differ from #1.
3. **Can ReachWeaver send?** Only text-only Bluesky/Mastodon/Telegram and LinkedIn personal-profile (member) adapters are currently enabled, mock-tested and not live-connected; LinkedIn organization pages are unsupported. Threads (text), Facebook Page (text/link), Instagram (single image), YouTube and TikTok (video) adapters, Mastodon images and Bluesky/Mastodon replies also exist, owner-approved 2026-10-09, mock-tested but disabled in the capability registry until the owner records a live receipt, so their `reachweaver_delivery` stays `manual_only`. Other platforms are manual drafts. Native scheduling elsewhere does not create integration here.

Official native scheduling examples:
https://www.linkedin.com/help/linkedin/answer/a1347212
https://support.google.com/youtube/answer/1270709?hl=en
https://help.producthunt.com/en/articles/2724119-how-to-schedule-a-post
https://telegram.org/blog/scheduled-reminders-themes
https://ads.tiktok.com/business/en-US/blog/introducing-video-scheduler-now-you-can-plan-tiktoks-in-advance

## Timing experiment and quality gates

- Prefer account-native analytics, time zones and actual audience location. In absence of data, propose only **candidate test windows**, such as comparable local weekday lunchtime versus early evening; never assert universal optimal weekdays or hours.
- Use IANA time zones and timezone-aware ISO8601 scheduling; include India (Asia/Kolkata), China (Asia/Shanghai), Japan (Asia/Tokyo), Jakarta (Asia/Jakarta), Lagos (Africa/Lagos) and Nairobi (Africa/Nairobi).
- Record exact creative, evidence, account, locale, schedule hypothesis, permissions, approval, delivery receipt, +24h/+7d outcomes, native metric definitions, qualified clicks/installs and moderation/removal status. Compare matched post ages and avoid unsupported causal claims.
- Reddit's spam guidance says promo is not inherently spam, but each community sets rules. Read them. No automated unsolicited bulk posts or engagement manipulation. https://support.reddithelp.com/hc/en-us/articles/360043504051-Spam

**Output to user:** ranked channels for one goal, current vs planned activity and verified vs unknown status, geographies/languages, native vs external vs ReachWeaver scheduling truth, 2–4 week pilot and 1 next measurable experiment. No mass auto-publish without human approval and verified platform capabilities.
