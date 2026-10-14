# Global distribution playbook — 2026-10-08

**Purpose:** Build genuine worldwide visibility for public, owned open-source work (especially YazSes) without spam, unsupported feature claims, or unauthorized publication. **This document is a research/operating guide, not evidence that ReachWeaver has connected an account or published a post.** Recheck platform rules and eligibility at execution time.

## Living data and current implementation

This narrative guide is complemented by the **[tested market/channel registry](../reachweaver/distribution_registry.json)**, the [operating change-control standard](DISTRIBUTION-GOVERNANCE.md), and [research on platform algorithms and timing](PLATFORM-ALGORITHMS-SCHEDULING-2026-10-08.md). Generate a read-only country-specific proposal with `python -m reachweaver.cli distribution-plan --region china --goal adoption --json`. It is NOT a publishing queue. New primary-source corrections belong in the registry and mirrored skill; verified native scheduling does not imply ReachWeaver API integration.

## Added regional research (2026-10-14)

The [regional channel register](REGIONAL-CHANNELS-2026-10-14.md) documents 27 new manual-only channels and four more market pilots (South Korea, Taiwan, Thailand, Russia). No API/scheduler or account authorization is implied; messaging groups and moderated communities remain human-only. The India awareness shortlist excludes TikTok pending updated regional availability and legal review.

## Existing footprint and evidence classification

The owner reports: (1) a LinkedIn YazSes promotional post and reshare, (2) DEV Community articles dated June 22 and September 23, (3) a YazSes YouTube demonstration, (4) the main GitHub repository, and (5) Snapcraft Forum technical discussion. These are **owner-reported, not independently verified here**; collect canonical URLs and native analytics before marking them VERIFIED. All other platforms: **account/content status unknown**. Never equate the existence of a post with an authenticated ReachWeaver account.

## Priorities — software adoption beats generic impressions

| Tier | Platform/region | Realistic strategy | Qualified outcome |
|---|---|---|---|
| A | GitHub, official website/docs, Snapcraft listing | Make installation, demo, changelog, privacy model and support paths immediately clear | Installs, docs visits, substantive stars/issues/contributions |
| A | YouTube + Shorts | Real Linux offline-dictation demonstration, subtitles, exact install CTA | Retained views, qualified site and repo visits |
| A | LinkedIn + DEV Community | Personal engineering story, factual technical how-to, different creative per platform | Real technical discussion and referral visits |
| A | Reddit, Bluesky, Mastodon | Relevant communities and opt-in conversations; no repeated link dropping | Useful replies, feedback, relevant visits; no moderation actions |
| A (India) | YouTube, LinkedIn, DEV, Hashnode, Peerlist, selected Instagram | English-first tutorials and builds; experiment with Hindi captions when useful, never misrepresent app language support | India-specific qualified referrals |
| A (China) | Bilibili, CSDN, OSCHINA, Zhihu, Juejin, Gitee | Human-reviewed Simplified Chinese tutorials and subtitled demos; authorized account creation and optional maintainable code mirror | Reproducible trial, discussions and Chinese-language referrals |
| B (Japan) | Qiita, Zenn, X, YouTube | Japanese technical tutorial, credible open-source workflow; technical value before self-promotion | Japan-specific technical readers |
| B (Southeast Asia) | YouTube, TikTok, Instagram, Facebook; developer groups | Start Indonesia and Philippines, then Vietnam/Thailand/Malaysia; locally relevant video and community answers | Regional retained/qualified visitors |
| B (Africa) | YouTube, LinkedIn, Facebook, eligible tech communities | Pilot Nigeria, Kenya, South Africa, Ghana, Egypt, Morocco with appropriate English, Arabic or French materials | Qualified adoption and local partnerships |
| B (launch events) | Product Hunt and legitimate product directories | One substantial release launch, not constant rebroadcasting | Launch conversations, qualified traffic |
| C | Hacker News | Only occasional genuinely interesting work; human-authored submission and manual posting; current Show HN restriction | Authentic discussion, not view chasing |
| C (community retention) | Discord, Telegram, opt-in WhatsApp communities | User support and contributor nurturing rather than cold blasts | Returning users and resolved issues |
| Test only | TikTok/Instagram/Threads/general X/Indie Hackers | Keep if first-party conversion validates effort | Relevant conversions, not views alone |

### Geographic logic

**India:** DataReportal 2026 (largely late-2025 reach): approximately 500M YouTube ad-reachable users, 481M Instagram, 170M LinkedIn registered members, versus about 30.8M Reddit advertising reach. Different metrics, not equivalent monthly active users. Do not prioritize Reddit above all other surfaces for India merely because its global tech conversations are visible.

**China:** A separate ecosystem is needed. Beyond the proposed Bilibili/Zhihu/OSCHINA trio, add CSDN, Juejin and potentially Gitee for code discovery. Weixin/WeChat, Weibo, Douyin and Kuaishou are broader reach options only if real account access, good native content and human review exist. Some platforms may require local verification; do not bypass it. Do not promise Chinese speech recognition until YazSes actually supports and tests it.

**Japan:** YouTube and X are large and Qiita/Zenn are specifically useful for engineers. DataReportal reports late-2025 X advertising reach around 71.2M and LinkedIn registered members around 5.4M. These distinct measures nevertheless suggest LinkedIn-only outreach is a poor default for Japan.

**Southeast Asia:** Indonesia is a compelling first pilot. DataReportal reported adult TikTok ad reach around 180M and YouTube reach around 151M in late 2025; these are advertising estimates, not actual YazSes users. Localized tutorials matter.

**Africa:** Treat different regions/countries individually. In Nigeria, DataReportal reported YouTube reach around 30.5M, Facebook reach around 38M, and 13M registered LinkedIn members in late 2025. Start with Nigeria, Kenya, South Africa, Ghana and then relevant North African technical communities. Use English, Arabic or French only when the content has credible human review. Avoid assuming that every community wants promotional posts.

Do not ignore Latin America, the Middle East or other countries; after validating primary cohorts, add new markets when demand appears in native first-party analytics. Worldwide coverage is an open-ended portfolio, not a promise of omnipresence.

## Scheduling matrix: platform capability vs ReachWeaver reality

Definitions: **native** means an end-user feature at the platform; **conditional** means account type, admin status or format matters; **external** means a separately authorized scheduler/API; **manual/verify** means scheduling has not been established. A platform having a native scheduling button is not evidence that ReachWeaver can use it.

| Platform | Human-native scheduling status | ReachWeaver v0.2 execution status |
|---|---|---|
| YouTube/Shorts | Native scheduled publish in YouTube Studio | Storyboard/manual; no uploader |
| LinkedIn personal and Pages | Native, with supported formats, account admin/role constraints | Draft/manual |
| X | Native via supported X scheduling tools | Draft/manual |
| TikTok | Conditional web video scheduler; official guidance describes eligible creator/business accounts and up to 10 days | Storyboard/manual, no media upload |
| Product Hunt | Native scheduled *launch* (current help says within 30 days); not recurring content scheduler | Launch brief/manual |
| Telegram | Native scheduled messages, including channels with rights | Text adapter mock-tested; no account connected or live verified |
| Instagram Reels/Posts | Conditional account/format/native or Meta suite scheduling | Storyboard/manual |
| Facebook Pages | Conditional Page/admin/Meta suite scheduling | Draft/manual |
| Facebook Groups | Conditional admin/moderator privileges; do not assume normal members can schedule | Community discussion only, manual |
| Bluesky | External approved scheduler/API possible; do not assume native | Text-only API adapter, mock-tested, not connected |
| Mastodon | Instance/client/API-dependent scheduling | Text-only API adapter, mock-tested, not connected |
| Reddit | No general automatic cross-community publishing assumed | Manual with subreddit rules |
| Hacker News | **No automation: manual posting and personally written text only** | Research/checklist only; never draft HN-facing text |
| DEV Community | **Native article scheduling in its editor** ([DEV Help](https://dev.to/help/writing-editing-scheduling)); local account capability still requires checking | ReachWeaver manual; no DEV API adapter |
| Hashnode | **Native scheduled publishing in Hashnode Pro** ([official June 2026 announcement](https://hashnode.com/changelog/2026-06-11-introducing-hashnode-pro)); account subscription required | ReachWeaver draft/manual; no Hashnode integration |
| Indie Hackers, Peerlist | Native scheduling unverified | Manual/verify |
| Discord | No general native scheduled-message guarantee; server bots require permission | Manual/permission-first |
| Bilibili, Zhihu, OSCHINA, CSDN, Juejin | Creator scheduling and account eligibility must be individually verified | Localized manual/verify |
| Gitee | Code hosting, not a social post scheduler | Manual authorized mirror decision |
| WeChat Official Accounts, Douyin, Kuaishou, Weibo | Local verification and creator tooling must be confirmed | Manual/verify |
| Qiita, Zenn | Native scheduling not verified | Manual/verify |
| WhatsApp groups/channels | No general unrestricted broadcast scheduler assumed | Opt-in only; no cold bulk messages |

Native scheduling reference links: https://www.linkedin.com/help/linkedin/answer/a1347212 ; https://support.google.com/youtube/answer/1270709?hl=en ; https://help.producthunt.com/en/articles/2724119-how-to-schedule-a-post ; https://telegram.org/blog/scheduled-reminders-themes ; https://ads.tiktok.com/business/en-US/blog/introducing-video-scheduler-now-you-can-plan-tiktoks-in-advance .

## Hacker News: why posts might be flagged

**Do not assert a diagnosis without URLs and account/submission history.** The official FAQ distinguishes user/moderator-flagged posts from dead posts (which can involve software, flags or moderation). Factors to inspect: promotional tone, redundant link/repost, prior account participation, actually runnable project, whether the same landing page was submitted before, discussion history and current platform-wide policy.

- As checked on 2026-10-08, HN displays an official **temporary Show HN restriction** due to a large influx of submissions, many by newer users unfamiliar with its culture. This may affect opportunities but does not prove why any particular submission was flagged.
- HN official guidelines: no primarily promotional use; no vote/comment solicitation; no automated posting; and **no generated submission text**. A moderator edit dated 2026-03-28 explicitly says not to use an LLM to generate **or edit even a little** of HN-facing text. ReachWeaver must not write, rewrite, polish, translate or schedule HN submissions/comments. The human owner writes them personally.
- Show HN requires a nontrivial personally made thing that the reader can *try now*. Landing pages and article-only announcements are not Show HNs; a technical article can be submitted as a normal link when intrinsically interesting.
- Do not delete/repost to circumvent moderation, swap domains/query strings, coordinate votes/comments, or use alternate accounts. A materially new release may merit a separate genuine submission later, only after checking the current HN rules. Community participation must be authentic and unforced.
- For flagged submissions, collect links/screenshots, [flagged] vs [dead] status, dates and comments and diagnose without assuming appeal success.

Official HN rules: https://news.ycombinator.com/showlim ; https://news.ycombinator.com/newsfaq.html ; https://news.ycombinator.com/newsguidelines.html ; https://news.ycombinator.com/showhn.html ; https://news.ycombinator.com/item?id=22336638 .

## Community boundaries

Reddit confirms self-promotion is **not inherently spam**, yet subreddit rules vary (some disallow promotion, others use a 10% heuristic, not a universal Reddit policy). Repeated unsolicited promotional mass posting and automated self-promotion are spam risks. Facebook Groups, Discord, Telegram and regional forums have venue-specific rules. Read them before posting, provide a genuinely useful answer, disclose connection to the project and never bulk DM or coordinate engagement.

Reddit official: https://support.reddithelp.com/hc/en-us/articles/360043504051-Spam ; https://support.reddithelp.com/hc/en-us/articles/28012014962580-How-do-I-keep-spam-out-of-my-community .

## Evidence-based timing — hypotheses, never universal claims

No universal 2026 "best time" formula is verified for all platforms. Segment the audience by **country + IANA time zone + language + platform + content format**. Prefer first-party audience-online/retention metrics when actually available. As a starting experiment only, compare a weekday local lunch window (roughly 12:00–14:00) against early evening (roughly 18:00–21:00), under comparable content and posting circumstances. Example time zones: Asia/Kolkata, Asia/Shanghai, Asia/Tokyo, Asia/Jakarta, Africa/Lagos, Africa/Nairobi, Europe/Rome. Never claim these windows maximize reach; week/daypart hypotheses need real trials.

Keep local IANA zone and resulting offset-aware scheduled timestamp, with daylight-saving conversions handled correctly. Record +24-hour and +7-day outcomes, moderation status and meaningful click/install signals. Do not sum native impressions across platforms as unique humans, conflate TikTok views with YouTube views, or infer causal timing effects from one post.

## Four-week initial experiment

1. **Week 1:** Verify links to the five reported existing surfaces; audit installation/README/website/Snapcraft readiness, existing native metrics, and open-source community rules.
2. **Week 2:** Make one **new verified technical proof** (e.g., offline Linux dictation demo) and adapt responsibly to YouTube, LinkedIn, DEV and one approved community. Localize subtitles only when correct.
3. **Week 3:** Test one India-targeted audience asset and, if account/translation capacity is available, one Chinese native technical article and one Bilibili video. Otherwise keep China manual and unlaunched.
4. **Week 4:** Test one Africa country-specific and one Japan/Indonesia asset or collaboration, evaluate real qualified outcomes, and stop channels without demonstrated fit.

Never auto-repost a single announcement to all 30 surfaces. Preserve language quality and platform community expectations.

**Required campaign fields:** canonical evidence URL/date, brand and authorized account, audience/country/language, platform and community, format, original value, CTA, local time and IANA time zone, native/external/manual delivery capability, actual permission and human approval, real published receipt if any, metric definitions and coverage, moderation outcome, qualified referrals/installs and next test. Missing outcomes are unknown, not zero.

## Sources and caution

- Regional estimates (mostly **late 2025**, published as 2026 reports; advertising reach or registered members, not comparable monthly-active totals):
  https://datareportal.com/reports/digital-2026-india
  https://datareportal.com/reports/digital-2026-china
  https://datareportal.com/reports/digital-2026-japan
  https://datareportal.com/reports/digital-2026-indonesia
  https://datareportal.com/reports/digital-2026-nigeria
- China developer venues: https://www.csdn.net/ ; https://www.oschina.net ; https://gitee.com/oschina
- Japan technical community: https://qiita.com/about ; https://help.qiita.com/ja/articles/qiita-community-guideline
- African developer ecosystem: https://www.bcg.com/publications/2026/develop-the-developers-a-strategic-priority-for-africa
- HN, Reddit and scheduling primary sources above.

**Verification boundary:** This is a prioritized market hypothesis and a record of external official guidance, not field-tested traffic predictions, a list of account connections or a guarantee of any algorithm advantage. Reverify regional access, creator privileges and API restrictions before a live launch.
