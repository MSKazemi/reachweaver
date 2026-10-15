# Community, creator and publishing platforms: research notes (2026-10-15)

Research behind the registry entries for public issues #15-#22, #27-#30, #32-#37. It records what the platforms' **own** pages say about posting, APIs, scheduling, limits, automation and self-promotion, what could not be verified, and the decision each entry records in `reachweaver/distribution_registry.json`. Hacker News and Reddit are not part of this batch and keep their existing human-only and moderator-only rules.

**What this is not.** It is research metadata, not legal advice and not permission to post. Every entry is `manual_only`: ReachWeaver has no adapter for any of these surfaces, never sends to them, and a planner suggestion is never consent to publish. `human_only` means ReachWeaver will not draft or plan for that surface.

## Method and its limits

- Official pages only (help centres, developer docs, terms, community guidelines). No third-party blog is a source for a rule.
- Pages were fetched through a tool that **summarizes** each page, so a quoted snippet (kept to 25 words) was not diffed against the live page by the researcher. A page that needed JavaScript or returned 403 is marked as such; where a rule came from a search-result excerpt instead of the page, that is stated.
- **Independent spot-check.** After the research, the key quotes and numbers were checked against the live pages by direct HTTP (not through the summarizing tool): 19 of 20 key quotes matched, and 33 of 36 number-and-rule groups matched in full. The misses were handled, not ignored: one agent-reported claim was **wrong** (an apparent contradiction between two Beehiiv pages about which plans can use the Create post API; the live page says Pro and Enterprise and the claim was removed), one figure was not on its page (a Slack limit of 30 scheduled messages per 5 minutes per channel, removed), and two were wording differences (Tumblr and Vimeo, corrected to the pages' own words). Quotes from pages that returned 403 or needed JavaScript could not be checked this way and stay flagged below.
- Anything not established is listed under "Not verified". Missing is unknown, never "allowed". Re-check a page before relying on it; platforms change their rules.

## Decisions at a glance

| Platform (`id`) | Issue | Scheduling mode | `human_only` | Why |
|---|---|---|---|---|
| Lobsters (`lobsters`) | #15 | `unverified` | yes | Invite-only; self-promotion limited to under a quarter of activity; commercial promotion is flagged as spam; no posting API documented |
| Lemmy (`lemmy`) | #16 | `native_conditional` | yes | 1.0 post scheduling is in open beta and instance-dependent; promotion rules are set per instance and community |
| GitHub Discussions (`github_discussions`) | #17 | `unverified` | no | Own-repository announcements fit; bulk promotion elsewhere is barred by the Acceptable Use Policies |
| Hugging Face (`huggingface`) | #18 | `unverified` | yes | Content Policy lists advertising and bulk activity as spam; Posts are PRO-only; articles are screened |
| Quora (`quora`) | #19 | `unverified` | yes | Self-promotion only inside a sincere, direct answer; repeated identical answers may be spam |
| Stack Exchange (`stackexchange`) | #20 | `unverified` | yes | Help Center promotion rule and API v2.3 read directly (curl, 2026-10-15); no scheduling documented; human-only |
| Tumblr (`tumblr`) | #21 | `native` | yes | Scheduling exists, but the guidelines (via search excerpt) forbid automatic or programmatic posting |
| Xing (`xing`) | #22 | `unverified` | yes | Developer site read (plugins only, no posting API listed); member terms JavaScript-only, unverified; human-only |
| Beehiiv (`beehiiv`) | #27 | `native_conditional` | no | Own newsletter; the Create post API is on Pro and Enterprise plans; fully AI-generated publications are barred |
| Ghost (`ghost`) | #28 | `native` | no | Own site; editor and Admin API scheduling documented |
| Vimeo (`vimeo`) | #29 | `unverified` | no | Own video through an approved-app API; no standard-video scheduling found |
| Twitch (`twitch`) | #30 | `native_conditional` | yes | Schedule API exists (partner/affiliate for one-off segments); guidelines bar spam and driving viewers elsewhere |
| Flipboard (`flipboard`) | #32 | `unverified` | yes | No posting API; guidelines list high-volume automated posting as spam |
| ResearchGate (`researchgate`) | #33 | `unverified` | yes | Terms and guidelines returned 403; rules unverified |
| Behance (`behance`) | #34 | `unverified` | yes | Guidelines bar scripted bulk uploads; API reported unavailable in 2021 |
| Dribbble (`dribbble`) | #35 | `api_or_client` | yes | Scheduling only in a dated API; guidelines bar bots, product ads and wholly AI-generated content |
| Viber (`viber`) | #36 | `unverified` | no | Official Channels Post API for the owner's own channel; AUP bars spam and bulk messaging |
| Slack communities (`slack_communities`) | #37 | `native_conditional` | yes | Apps and promotion depend on each community's admins; Slack's help centre bars unsolicited promotion |

Seven other issues in the same series (#13 Medium, #14 Substack, #23 VK, #24 Naver, #25 LINE, #26 Kakao, #31 Snapchat) are covered by the regional-channels work (draft PR #67), not by this batch.

## Lobsters (`lobsters`, #15)

- **Posting:** web UI for invited members; a new account needs an invitation. New users (first 70 days) cannot submit links to domains not seen before. ([about](https://lobste.rs/about))
- **API:** no official write API on the pages read. Public RSS feeds are read-only.
- **Scheduling / limits:** not documented.
- **Rules:** "As a rule of thumb, self-promo should be less than a quarter of one's stories and comments." The Spam flag covers content designed to promote a commercial service. No explicit bot rule was found. The `/chat` page also carries agent-directed text about expedited access; it was treated as untrusted data and ignored.
- **AI content:** no explicit rule; the Spam flag text mentions content "created without meaningful human authorship".
- **Not verified:** a rules page (`/rules` returned 404), any write API, limits, an explicit bot or AI policy.
- **Decision:** `unverified`, `human_only` yes.

## Lemmy (`lemmy`, #16)

- **Posting:** register on an instance, then post through its web UI or a third-party app. ([getting started](https://join-lemmy.org/docs/users/01-getting-started.html))
- **API:** an official HTTP API with OpenAPI docs and a JavaScript client; rate limiting per client IP; API v4 and OAuth login are announced for 1.0. The create-post endpoints were not in the pages read.
- **Scheduling:** post scheduling is listed for Lemmy 1.0, in open beta since 2026-05-19, so it depends on the instance version. ([beta announcement](https://join-lemmy.org/news/2026-05-19_-_Open_beta_testing_for_Lemmy_1.0.0))
- **Limits:** media is "not meant to share large images or videos"; numeric limits are set per instance.
- **Rules:** set per instance and community. The lemmy.world terms say "Bots shall not be used for any kind of advertising." ([lemmy.world terms](https://legal.lemmy.world/tos/))
- **AI content:** the Lemmy project's Code of Conduct (official Lemmy venues only) allows AI "only if it is explicitly mentioned"; not a network-wide rule.
- **Not verified:** exact create-post endpoints, a per-user scheduled-post limit (seen only in a search snippet), the final 1.0 release, numeric limits.
- **Decision:** `native_conditional`, `human_only` yes.

## Stack Overflow / Stack Exchange (`stackexchange`, #20)

- **Read directly on 2026-10-15** (the first research pass was refused by the summarising tool; a plain HTTP fetch of the same pages worked): the [promotion page](https://stackoverflow.com/help/promotion) says "The community here tends to vote down overt self-promotion and flag it as spam" and that mentioning your product, website or other contribution means you "must disclose your affiliation in your post". The [API v2.3 documentation](https://api.stackexchange.com/docs) describes authentication and write support (`write_access` scope, for example `/questions/add`), applications registered on Stack Apps, and throttles.
- **Not verified:** the AI-content policy, per-site rules (each Stack Exchange site has its own meta), and any scheduling feature (none is documented in the pages above).
- **Decision:** `unverified` scheduling, `human_only` yes, because a project announcement does not fit a Q&A site and undisclosed promotion is flagged as spam.

## GitHub Discussions (`github_discussions`, #17)

- **Posting:** web UI. "Any authenticated user who can view the repository can create a discussion in that repository." ([quickstart](https://docs.github.com/en/discussions/quickstart))
- **API:** GraphQL only: the `createDiscussion` mutation needs a title, body, repository id and category id. "Access tokens require the repo scope for private repositories." ([guide](https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions)) No REST endpoint for repository discussions was found.
- **Scheduling:** not documented.
- **Limits:** content-creation requests are limited to "no more than 80 content-generating requests per minute" ([rate limits](https://docs.github.com/en/graphql/overview/rate-limits-and-node-limits-for-the-graphql-api)); body and media limits were not found.
- **Rules:** the Acceptable Use Policies bar "bulk distribution of promotions and advertising prohibited by GitHub terms and policies". The Terms of Service say accounts registered by bots are not permitted; a machine account for automated tasks is. ([AUP](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies))
- **AI content:** none found.
- **Not verified:** native scheduling, post limits, an AI rule.
- **Decision:** `unverified`, `human_only` no: an announcement in a repository you maintain is a normal use. Promotion in repositories you do not maintain is human-written only.

## Hugging Face community posts (`huggingface`, #18)

- **Posting:** Hub Discussions through a repository's Community tab, the `huggingface_hub` library or CLI; Posts and community articles through the website. ([discussions docs](https://huggingface.co/docs/hub/repositories-pull-requests-discussions))
- **API:** `create_discussion` and `comment_discussion` with an access token. No API for Posts or community articles was found. ([guide](https://huggingface.co/docs/huggingface_hub/guides/community))
- **Scheduling:** not documented.
- **Limits:** Hub API rate limits are in 5-minute windows (1,000 requests free, 2,500 PRO as of September 2025); per-action discussion limits are "not currently documented". Posts are a PRO feature, one per day per the organization page (which may be stale).
- **Rules:** the Content Policy lists "Spam (e.g., advertising products/services, excessive bulk activity, or disrupting user experience)". ([content policy](https://huggingface.co/content-policy))
- **AI content:** only for community articles: the Blog Explorers page says low-quality, non-original or LLM-generated posts are hidden from the blog page. Not a Hub-wide rule.
- **Not verified:** an official docs page for Posts, native scheduling, per-action limits, a Hub-wide AI rule.
- **Decision:** `unverified`, `human_only` yes.

## Quora (`quora`, #19)

- **Posting:** the web and app editors; in a Space, contributors post directly and others submit for approval (from a search excerpt of the help article; the page returned 403).
- **API:** no official posting API found. Quora's blog lists a Poe API, which is for LLM bots, not for posting to Quora.
- **Scheduling / limits:** not verified.
- **Rules:** "Self-promotion in answers is permitted only in the context of a sincere, helpful, and direct response". ([Quora for Business](https://business.quora.com/resources/writing-on-quora-as-a-business)) Repeated identical answers and answers that depend on leaving Quora may be treated as spam (search excerpt only).
- **AI content:** none found.
- **Not verified:** the verbatim Platform Policies and Terms (help.quora.com and the terms page returned 403), any API, scheduling, limits.
- **Decision:** `unverified`, `human_only` yes.

## Tumblr (`tumblr`, #21)

- **Posting:** web dashboard and apps; the v2 API with the Neue Post Format.
- **API:** `POST /v2/blog/{blog}/posts` with OAuth 1.0a or OAuth 2 (scopes `basic`, `write`, `offline_access`); apps must be registered. Whether registration is open to every new developer is not verified. ([Tumblr docs repository](https://raw.githubusercontent.com/tumblr/docs/master/api.md))
- **Scheduling:** yes: in the editor use the arrow next to Post and choose Schedule ([help](https://help.tumblr.com/knowledge-base/scheduled-posts/)); the API supports `state=queue` with `publish_on`.
- **Limits:** per user per day, 250 new published posts (including reblogs), 250 image uploads and 20 video uploads; the queue publishes at most 50 a day and stores 1,000; API rate limits 1,000 calls per hour and 5,000 per day per consumer key (from the docs repository).
- **Rules:** the User Guidelines say "Don't register accounts or post content automatically, systematically, or programmatically." This quote comes from a search excerpt because the guidelines page needs JavaScript; re-check it on the live page.
- **AI content:** none found site-wide; individual Communities set their own.
- **Not verified:** the live guideline text, new-developer access, text character limits.
- **Decision:** `native`, `human_only` yes.

## Xing (`xing`, #22)

- **Read directly on 2026-10-15:** the [XING Developer site](https://dev.xing.com/) lists only plugins (Share on XING, Login with XING, partner Job Integration and Apply with XING). It lists no API for creating member or company posts.
- **Not verified:** the member terms and community rules (`xing.com/terms` is a JavaScript page that returns no text without a browser), company-page posting, scheduling, automation and AI rules.
- **Decision:** `unverified`, `human_only` yes as a conservative default. Read the terms in a browser before changing it.

## Beehiiv (`beehiiv`, #27)

- **Posting:** web editor; flow Compose, Audience, Email, Web, Review, then schedule or publish. ([help](https://www.beehiiv.com/support/article/26311866535575-how-to-create-and-publish-a-post))
- **API:** `POST /v2/publications/{publicationId}/posts` needs the `posts:write` scope; `status` is `draft` (default) or `confirmed`; creation is asynchronous. The endpoint reference says "This endpoint is available to publications on the Pro and Enterprise plans." ([endpoint](https://developers.beehiiv.com/api-reference/posts/create)); confirm your plan before relying on it. Custom HTML emails, ad or poll blocks and some Post Builder blocks cannot be created.
- **Scheduling:** from the Review step; plan limits not stated.
- **Limits:** no per-post limit documented; Gmail clips emails once the HTML exceeds 102 KB; API rate limits per minute by plan (Free 30, Lite 180, Pro 500, Enterprise 800).
- **Rules:** the Acceptable Use Policy bans "Using bots or scripts to simulate human activity or engagement of any kind" and newsletters "created solely to advertise or sell products or services without substantive editorial Content". ([AUP](https://www.beehiiv.com/aup))
- **AI content:** "Publications that rely entirely on AI-generated material without meaningful human input are not permitted."
- **Not verified:** per-post limits, scheduling plan limits, a mobile publish path.
- **Decision:** `native_conditional`, `human_only` no: it is the owner's own publication with human editorial input; default to draft and require approval.

## Ghost (`ghost`, #28)

- **Posting:** the Admin editor; the Admin API (integration or staff tokens). The Content API is read-only. ([Admin API](https://docs.ghost.org/admin-api))
- **API:** `POST /ghost/api/admin/posts/`; token auth signs a short-lived JWT with the Admin API key. On Ghost(Pro) the pricing page lists custom integrations and the Admin API as not included on Starter (from the pricing page via the summarizing tool; not independently verified).
- **Scheduling:** yes, in the editor and through the API by "setting the `status` to `scheduled` and setting `published_at` to a datetime in the future". ([scheduling](https://docs.ghost.org/admin-api/posts/scheduling-a-post))
- **Limits:** none documented.
- **Rules:** the Terms bar unsolicited promotional material and spam, and say "Accounts registered by "bots" or other automated methods are not permitted." Posting to your own site is not addressed. ([terms](https://ghost.org/terms/))
- **AI content:** none found.
- **Not verified:** API rate limits, post size limits, the commercial-use clause in full context.
- **Decision:** `native`, `human_only` no: the owner's own site; default to draft and require approval.

## Flipboard (`flipboard`, #32)

- **Posting:** add a link, image or Note to a magazine by hand, or submit an RSS feed at flipboard.com/publishers, which staff review. ([web help](https://flipboard.helpshift.com/hc/en/1-flipboard/faq/1551-add-to-magazine-on-the-web/))
- **API:** no official public posting API found.
- **Scheduling / limits:** not documented.
- **Rules:** the Community Guidelines list "Content automatically posted at high volumes" and "Unapproved content that advertises products or services for profit or gain" as spam. ([guidelines](https://flipboard.helpshift.com/hc/en/1-flipboard/faq/751-community-guidelines-1614302844/))
- **AI content:** none found.
- **Not verified:** the Terms text (the body did not load), the RSS Guidelines, limits.
- **Decision:** `unverified`, `human_only` yes.

## Vimeo (`vimeo`, #29)

- **Posting:** the web upload page and apps; the official API.
- **API:** needs an app and a token with the upload scope; developers who have not purchased a paid plan, including free users, "must request upload access", reviewed by hand in up to five business days, while paid plans are approved automatically ([help article](https://help.vimeo.com/hc/en-us/articles/12427803706001-How-to-request-API-upload-access)). The developer.vimeo.com pages returned no readable text, so the current flow and what the API cannot do are unverified. ([upload access](https://help.vimeo.com/hc/en-us/articles/12427803706001-How-to-request-API-upload-access))
- **Scheduling:** no scheduled release for standard videos was found; live events can be scheduled on Advanced, Premium or Enterprise-with-Events plans.
- **Limits:** 24 hours maximum duration; keep files under 200 GB; most plans have a total storage cap.
- **Rules:** the Terms bar access by automated means and spam or misleading metadata, and ask for synthetic content to be labelled (via the summarizing tool; clause numbers not confirmed). ([terms](https://vimeo.com/terms))
- **Not verified:** the developer docs, API rate limits, standard-video scheduling, any rule on promoting your own software.
- **Decision:** `unverified`, `human_only` no: the owner's own video through the sanctioned API.

## Twitch (`twitch`, #30)

- **Posting:** go live from the Creator Dashboard with broadcasting software; manage the schedule in the dashboard.
- **API:** the Helix REST API with OAuth 2.0; the registering account needs 2FA; `channel:manage:schedule` writes schedule segments. "Only partners and affiliates may create non-recurring segments." ([Schedule API](https://dev.twitch.tv/docs/api/schedule/))
- **Scheduling:** yes; recurring segments cannot start in the future and overlapping recurring segments fail.
- **Limits:** token-bucket rate limits per client ID; 429 when the bucket is empty.
- **Rules:** "Twitch may not be used to drive users to a livestream on another platform or service." Spam, botting and artificial viewership are barred. ([Community Guidelines](https://safety.twitch.tv/articles/en_US/Knowledge/Community-Guidelines)) The Terms and Developer Agreement did not render.
- **AI content:** only deepfakes used for harassment are named.
- **Not verified:** the help-centre schedule article (404 or a CSS error), the automated-means clause, the bucket size.
- **Decision:** `native_conditional`, `human_only` yes.

## Behance (`behance`, #34)

- **Posting:** the web project editor (from a search summary; the help articles returned 403).
- **API:** the only official statement found is an Adobe staff reply from 2021: "We are working through a technical migration, and as a result, the Behance API is unavailable." The current status is unknown.
- **Scheduling:** a search summary lists project scheduling under Behance Pro settings; the page returned 403, so it is unverified.
- **Rules:** the Community Guidelines list "Using automated or scripting processes (such as bulk or automated uploading of content through a script)" and bar engagement-boosting schemes; the community is for people to "publicly share one's own creative work". ([guidelines](https://www.behance.net/misc/community))
- **AI content:** not on the guidelines page.
- **Not verified:** all help articles (403), the current API status, the Additional Terms, limits.
- **Decision:** `unverified`, `human_only` yes.

## Dribbble (`dribbble`, #35)

- **Posting:** the web uploader. ([creating shots](https://help.dribbble.com/en/articles/11062601-creating-dribbble-shots))
- **API:** v2 can create shots with OAuth2 and the `upload` scope for a pro, team or team-member account; apps must be registered; the developer terms say access can be refused. The documented image limits conflict with the newer help-centre limits, so the API docs may be stale. ([shots](https://developer.dribbble.com/v2/shots/))
- **Scheduling:** only in the API: `scheduled_for`, "Timestamp to publish the shot", on shot creation. Whether the web uploader has a schedule control is not verified.
- **Limits:** help centre: images up to 10 MB, videos up to 20 MB; API: up to 12 tags, 60 requests a minute and 1,440 a day per user.
- **Rules:** "Do not use automations (like bots or scripts) to like, save, follow, or disingenuously engage on the platform." and "Do not use Dribbble Shots to advertise your product or service." ([guidelines](https://dribbble.com/guidelines))
- **AI content:** "Do not share content that is wholly generated by AI."
- **Not verified:** web-UI scheduling, title and description limits, whether new developers can currently get API access.
- **Decision:** `api_or_client`, `human_only` yes.

## ResearchGate (`researchgate`, #33)

- **Posting:** members add publications or unpublished work through the web UI; no composer for ordinary social posts was documented. At most 5 research items can be added at once. ([how to add research](https://help.researchgate.net/research-and-publications/how-to-add-research))
- **API / scheduling:** none found or documented.
- **Rules:** the Terms of Service and Community Guidelines returned 403 to every fetch, so the bot and self-promotion rules are **unverified**. Members must hold the rights to what they upload.
- **AI content:** none found in the pages that could be read.
- **Decision:** `unverified`, `human_only` yes, until the rules are read in a browser.

## Viber (`viber`, #36)

- **Posting:** admins post in Channels, and in restricted Communities, from the app. Channel superadmins can also post through the Channels Post API: "Own a Channel – only Channel super admins can get access to API." ([Channels Post API](https://developers.viber.com/docs/tools/channels-post-api/))
- **API:** the Channels Post API (own channel, token from the channel's tools) and the bot REST API. "Since 5.02.24, Viber bots can only be created on commercial terms", and bots reach subscribers only.
- **Scheduling:** per the help centre channel article, admins can schedule up to a year ahead (a search snippet; the page returned 403).
- **Limits:** Channels Post API text up to 7,000 characters and video up to 50 MB; bot API 100 messages per user per hour without a reply and 500 requests per 10 seconds on broadcast.
- **Rules:** the Acceptable Use Policy says "Do not SPAM, do not send bulk messages" and bars "bulk messaging, auto-messaging and auto-dialling". ([AUP](https://www.viber.com/en/terms/viber-public-content-policy/))
- **AI content:** none found.
- **Not verified:** the help-centre pages (403), Community scheduling and posting rules, broadcast authorization, a numeric Channels Post API rate limit.
- **Decision:** `unverified`, `human_only` no: the owner's own channel has an official API, but never bulk or bot-broadcast to people who did not subscribe.

## Slack communities (`slack_communities`, #37)

- **Posting:** type a message in the app, or post through the Web API (`chat.postMessage`, `chat:write`); the app must be in the channel unless it has `chat:write.public`. ([chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage))
- **API:** installed by OAuth; Workspace Owners can require app approval, so posting in a community you do not own depends on its admins. ([app approval](https://slack.com/help/articles/222386767-Manage-app-approval-for-your-workspace))
- **Scheduling:** yes in the apps ("You can send messages in real time, or schedule them to send later.") and through `chat.scheduleMessage` (a message can be scheduled up to 120 days ahead). ([help](https://slack.com/help/articles/201457107-Send-and-read-messages))
- **Limits:** messages over 40,000 characters are truncated; 4,000 or fewer is recommended; about one message per second per channel.
- **Rules:** "It's against Slack's Acceptable Use Policy to send unsolicited communications, promotions, advertisements, or spam." ([help](https://slack.com/help/articles/1500001422062-Use-Slack-Connect-to-start-a-DM-with-someone-at-another-company)) The API terms bar abusive use; each community's own channel rules for promotion are not an official Slack source.
- **AI content:** none found.
- **Not verified:** the full Acceptable Use Policy text, an explicit bot-disclosure rule, any community's own rules.
- **Decision:** `native_conditional`, `human_only` yes.

## Re-read of eight earlier entries (`medium`, `substack`, `vk`, `naver_blog`, `naver_cafe`, `line_openchat`, `kakao_openchat`, `snapchat`; #13, #14, #23 to #26, #31)

These were registered from the regional-channels work with only a landing page as source. They were re-read on 2026-10-15 by direct HTTP fetch. Short quotes (25 words or fewer) are from the pages named; everything not listed here is **not verified**.

| Surface | What was read | What it says |
|---|---|---|
| Medium | [Medium API docs repository](https://github.com/Medium/medium-api-docs) | Archived 2023-03-02; "The Medium API is no longer supported. We do not recommend using it." |
| Substack | [Terms of Use](https://substack.com/tos) | Users may not use a tool that "Crawls," "scrapes," or "spiders" any page, data, or portion of Substack "(through use of manual or automated means)". |
| VK | [Terms of Service](https://vk.com/terms) | 6.3.6 bars "the mass mailing of messages to other Website Users without their prior consent"; 6.3.9 bars "using automated scripts (programs) for collecting information on the Site and/or interacting with the Site"; 5.13.4.1: "No outside commercial or political advertising is permitted in the Community". |
| Naver Cafe | [Open API list](https://developers.naver.com/docs/common/openapiguide/apilist.md) | A login-method Cafe API lets an outside service join a cafe or register an article (`POST https://openapi.naver.com/v1/cafe/{clubid}/menu/{menuid}/articles`), using a Naver Login access token. |
| Naver Blog | same list | Blog appears as a search target and, via Naver Share, a share destination; no blog-writing API is listed. |
| LINE OpenChat | [Messaging API overview](https://developers.line.biz/en/docs/messaging-api/overview/) | "Use the Messaging API to build bots to provide personalized experiences on LINE to your users", through a LINE Official Account. OpenChat is not mentioned. |
| KakaoTalk Open Chat | [Kakao Developers docs](https://developers.kakao.com/docs/latest/en/kakaotalk-channel/common) | The product menu lists Kakao Talk Channel, Message, Share, Social and Push Notification; it lists no Open Chat API. |
| Snapchat Spotlight | [Spotlight page](https://www.snapchat.com/spotlight), help and Community Guidelines | JavaScript-heavy; nothing about posting, API, scheduling or limits could be read. |

**Not verified:** Medium rules, scheduling and import (help pages returned 403); Substack scheduling, API and self-promotion rules (support pages returned 403); VK API access, `wall.post` scheduling and limits (the developer page was down for maintenance); Naver scheduling, limits and any cafe-level rule, and Naver Blog limits; the LINE OpenChat guideline and Kakao Open Chat rules (JavaScript-only pages); every Snapchat rule. A statement that an API was "not found" means it is not in the pages named above, not that none exists. Every entry stays `manual_only` with `unverified` scheduling.

