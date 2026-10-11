# ReachWeaver platform and ecosystem research

**Research snapshot:** 2026-10-08. These are implementation constraints and initial leads, **not a claim of integration, API approval, or user authorization**. Consult current official documentation again immediately before implementation. APIs, cost, scopes and account requirements change frequently.

## Key external dependency: platform approvals

| Service | Initial feasibility finding | Primary documentation | Planned issue |
| --- | --- | --- | --- |
| Bluesky | Posting is based on AT Protocol `app.bsky.feed.post` records; SDKs and account credentials/sessions are documented. Use supported auth and receipt URIs. | [Creating a post](https://docs.bsky.app/docs/tutorials/creating-a-post) | [#12](https://github.com/MSKazemi/reachweaver/issues/42) |
| Mastodon | Official `POST /api/v1/statuses` and `POST /api/v2/media`; federated instance rules may differ. | [Statuses](https://docs.joinmastodon.org/methods/statuses/), [media](https://docs.joinmastodon.org/methods/statuses/media/) | [#13](https://github.com/MSKazemi/reachweaver/issues/43) |
| LinkedIn | `w_member_social` is an available member publishing permission; organization/community features have distinct restrictions. Versioned Posts API requires supported headers and version checks. | [Access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access), [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api) | [#14](https://github.com/MSKazemi/reachweaver/issues/44) |
| Threads | Official Meta Threads API and app authorization for creating/publishing content; available features depend on granted permissions. | [Meta's Threads API Postman workspace](https://www.postman.com/meta/threads/collection/dht3nzz/threads-api) | [#19](https://github.com/MSKazemi/reachweaver/issues/49) |
| Instagram | Instagram Graph API publishing is oriented to professional Business/Creator accounts; consumer account direct publishing cannot be assumed. Login/permissions differ by integration model. | [Meta's Instagram API workspace](https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api) | [#20](https://github.com/MSKazemi/reachweaver/issues/50) |
| Facebook | Page role, access tokens, app permissions and review must be verified before specifying allowed content formats; do not assume personal profile publishing. | [Meta developers](https://developers.facebook.com/docs/graph-api) | [#21](https://github.com/MSKazemi/reachweaver/issues/51) |
| YouTube | `videos.insert` supports uploads. Uploads by unverified projects created after 2020-07-28 can be restricted to private; public release may require audit. Quota model changed during 2026. | [Uploads](https://developers.google.com/youtube/v3/docs/videos/insert), [revision history](https://developers.google.com/youtube/v3/revision_history) | [#22](https://github.com/MSKazemi/reachweaver/issues/52) |
| TikTok | Direct Post requires relevant posting permission and approved app setup. Unaudited clients are restricted to private viewing; explicit creator preferences and audit are important. Creator draft export can be a fallback. | [Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-get-started), [sharing guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines) | [#23](https://github.com/MSKazemi/reachweaver/issues/53) |
| X | Official `POST /2/tweets` exists and pay-per-use pricing is advertised; do not assume a free quota. Check spending and app access at implementation time. | [X developer](https://developer.twitter.com/), [manage posts documentation](https://github.com/xdevplatform/docs/blob/main/x-api/posts/manage-tweets/quickstart.mdx) | #24 |
| Reddit | OAuth developer access and submission endpoints exist, subject to responsible builder/developer terms and subreddit restrictions; unsolicited automated outreach, unauthorized scraping and vote manipulation are not permitted. | [Data API help](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Don't break the site](https://support.reddithelp.com/hc/en-us/articles/360043512931-Don-t-break-the-site) | #24 |
| Pinterest | Official create-Pin and board endpoints; new app access restrictions affecting non-business users are explicitly documented for registrations after 2026-09-14. | [Create boards/Pins](https://developers.pinterest.com/docs/work-with-organic-content-and-users/create-boards-and-pins/) | #24 |

### No unsafe fallbacks

If a platform does not permit automated publishing for the configured user/app, ReachWeaver should create a **human-reviewed manual-export package**, not scrape private endpoints, bypass an audit, impersonate a normal browser session to evade restrictions, or claim an unverified upload is public.

## Existing projects to study rather than copy blindly

1. [Postiz](https://github.com/gitroomhq/postiz-app): open-source social scheduler with API, analytics and automation integrations. Its repository is AGPL-3.0. Evaluate use as an *optional backend* and verify obligations before copying any code or incorporating it into another licensed product.
2. [Anthropic marketing draft-content skill](https://github.com/anthropics/knowledge-work-plugins/blob/main/marketing/skills/draft-content/SKILL.md): examples of audience/brand-tailored channel content, hooks and SEO. Study skill structure and ideas; license-check any source reuse.
3. [Anthropic agent skills documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview): a Claude Code project skill is installed in `.claude/skills/<name>/SKILL.md`; use progressive disclosure and minimal core instructions.
4. [YouTube Analytics metrics](https://developers.google.com/youtube/analytics/metrics): views, engaged views, watch time and retention are distinct measurements. Never pretend raw reach metrics are fully comparable across networks.

## Differentiation hypothesis — test, don't assert

Existing schedulers already handle publication; ReachWeaver may offer additional value if it can demonstrably:
- Discover high-value announcements in real technical work with evidence and novelty filtering.
- Convert the announcement into *different*, credible messages for each audience and network.
- Keep humans in control while supporting repeatable, secure multi-account workflows.
- Attribute outcomes to projects/websites and learn from properly defined experiments.
- Integrate optional SEO workflows without assuming social shares directly increase search rankings.

Benchmark these hypotheses with offline and opt-in field tests under [#27](https://github.com/MSKazemi/reachweaver/issues/55). No engagement improvement should be stated as fact until measured.

## Security, ethics and privacy baseline

OAuth/account-approved authorization, least privilege, revocation, encrypted/token-vault storage, redaction, prompt-injection resistance, accurate claims, user consent, anti-spam frequency caps, explicit human approvals and clear audit trails are release gates, not optional enhancements.

See also [#5](https://github.com/MSKazemi/reachweaver/issues/38), #6, [#7](https://github.com/MSKazemi/reachweaver/issues/39) and [#26](https://github.com/MSKazemi/reachweaver/issues/54).
