# Regional social channels and country pilots — 2026-10-14

**Research-only expansion ([issue #64](https://github.com/MSKazemi/reachweaver/issues/64)).** The [distribution registry](../reachweaver/distribution_registry.json) adds 36 distinct channels (27 on 2026-10-14 plus 9 India-focused surfaces on 2026-10-15) and four markets. The separate [API capability matrix](PLATFORM-MATRIX.md) is intentionally unchanged. **No OAuth, API adapter, remote scheduler, account access, scraping, content generation for human-only channels, or posts have been enabled.** All new entries are `manual_only`, scheduling `unverified`, and carry their own `checked_on` dates (2026-10-14 or 2026-10-15); provider home/help pages are pointers for follow-up review, **not proof of access or scheduling support**.

| ID | Channel | Research audience | Path | Provider starting point |
|---|---|---|---|---|
| `xiaohongshu` | Xiaohongshu / REDnote | China | native manual | [Provider page](https://creator.xiaohongshu.com/) |
| `wechat_channels` | Weixin / WeChat Channels | China | native manual | [Provider page](https://www.tencent.net.cn/products/channels/) |
| `toutiao` | Toutiao / Toutiaohao | China | native manual | [Provider page](https://baike.toutiao.com/detail/242/255/0) |
| `baidu_tieba` | Baidu Tieba | China | human only | [Provider page](https://tieba.baidu.com/) |
| `douban` | Douban | China | human only | [Provider page](https://www.douban.com/) |
| `maimai` | Maimai | China | human only | [Provider page](https://maimai.cn/) |
| `qzone` | Qzone | China | human only | [Provider page](https://qzone.qq.com/) |
| `note_jp` | note (Japan) | Japan | native manual | [Provider page](https://note.com/) |
| `naver_blog` | Naver Blog | South Korea | native manual | [Provider page](https://help.naver.com/service/5593/contents/15555) |
| `naver_cafe` | Naver Cafe | South Korea | human only | [Provider page](https://help.naver.com/service/5622/contents/15212) |
| `kakao_openchat` | KakaoTalk Open Chat | South Korea | human only | [Provider page](https://open.kakao.com/) |
| `dcard` | Dcard | Taiwan | human only | [Provider page](https://www.dcard.tw/) |
| `ptt` | PTT | Taiwan | human only | [Provider page](https://www.ptt.cc/) |
| `pantip` | Pantip | Thailand | human only | [Provider page](https://pantip.com/) |
| `line_openchat` | LINE OpenChat | Japan / Thailand | human only | [Provider page](https://help.line.me/line/smartphone/categoryId/200000000/) |
| `zalo_official_account` | Zalo Official Account | Vietnam | human only | [Provider page](https://oa.zalo.me/home) |
| `kaskus` | KASKUS | Indonesia | human only | [Provider page](https://www.kaskus.co.id/) |
| `sharechat` | ShareChat | India | native manual | [Provider page](https://creators.sharechat.com/) |
| `moj` | Moj | India | native manual | [Provider page](https://sharechat.com/about/company/) |
| `josh` | Josh | India | native manual | [Provider page](https://myjosh.in/) |
| `chingari` | Chingari | India | native manual | [Provider page](https://blog.chingari.io/how-to/) |
| `hipi` | Hipi | India | native manual | [Provider page](https://hipihelpcenter.zee5.com/portal/en/kb/articles/how-do-i-download-hipi) |
| `public_app` | Public App | India | native manual | [Provider page](https://inshorts.group/) |
| `kutumb` | Kutumb | India | human only | [Provider page](https://www.kutumbapp.com/) |
| `arattai_channels` | Arattai Channels | India | human only | [Provider page](https://help.arattai.in/portal/en/kb/articles/about-channels) |
| `localcircles` | LocalCircles | India | human only | [Provider page](https://www.localcircles.com/a/) |
| `pratilipi` | Pratilipi | India | native manual | [Provider page](https://english.pratilipi.com/help/articles/75903534-how-do-i-publish-a-story-or-a-story-part) |
| `rooter` | Rooter | India | native manual | [Provider page](https://pages.rooter.gg/) |
| `vk` | VK | Russia | native manual | [Provider page](https://vk.com/) |
| `odnoklassniki` | Odnoklassniki / OK | Russia | native manual | [Provider page](https://ok.ru/) |
| `dzen` | Dzen | Russia | native manual | [Provider page](https://dzen.ru/) |
| `habr` | Habr | Russia | human only | [Provider page](https://habr.com/en/docs/help/rules/) |
| `medium` | Medium | Global | native manual | [Provider page](https://medium.com/) |
| `substack` | Substack | Global | native manual | [Provider page](https://substack.com/) |
| `snapchat` | Snapchat Spotlight | Global | native manual | [Provider page](https://www.snapchat.com/spotlight) |
| `kwai` | Kwai | Brazil / international | native manual | [Provider page](https://www.kwai.com/) |

**Human-only** means only the owner can write and publish a contribution manually after reviewing forum/group rules; no ReachWeaver draft or export path exists. Other **native manual** entries also have no ReachWeaver-specific export adapter: users must use the platform's own interface. All audiences and ordering are editorial hypotheses, not platform algorithm claims or posting-time predictions.

## Specific governance decisions

- **Mainland China:** Keep WeChat Official Accounts distinct from [Weixin Channels](https://www.tencent.net.cn/products/channels/); test original videos and professionally reviewed Simplified Chinese. [Xiaohongshu's creator center](https://creator.xiaohongshu.com/) is available, but its [developer scope listing](https://openaccount.xiaohongshu.com/docs/scope) describes `write_notes` as planned/restricted, **not a generally approved API**. Tieba, Douban, Maimai and Qzone stay human-only. [Toutiao creator documentation](https://baike.toutiao.com/detail/242/255/0) describes a native creator workflow, not a ReachWeaver connector.
- **South Korea:** Naver Blog is article-oriented; [its search visibility help](https://help.naver.com/service/5593/contents/15555) explains visibility settings. [Naver Cafe help](https://help.naver.com/service/5622/contents/15212) describes group-mediated posting. Cafe and KakaoTalk Open Chat are human-only and not message-broadcast tools.
- **Taiwan, Thailand and Japan:** Use human-reviewed Traditional Chinese, Thai and Japanese respectively. Dcard, PTT, Pantip and [LINE OpenChat](https://help.line.me/line/smartphone/categoryId/200000000/) are community conversations, not automated distribution. Japanese note is an author platform.
- **Vietnam:** [Zalo Official Account](https://oa.zalo.me/home) is for communicating with a legitimate opted-in audience; no cold messaging, contact harvesting or presumed broadcast API.
- **India:** ShareChat/Moj plus Josh, Chingari and Hipi are *candidate original-video surfaces*, not interchangeable supported APIs. [ShareChat's creator rules](https://help.sharechat.com/faq/original-creator/) distinguish original work from copies. Josh's [official creator page](https://myjosh.in/) and [VerSe parent site](https://www.verse.in/) describe local-language short video; [Chingari](https://blog.chingari.io/how-to/) has 2026 creator materials; [Hipi's help](https://hipihelpcenter.zee5.com/portal/en/kb/articles/how-do-i-download-hipi) describes the video app. [Public App's parent company](https://inshorts.group/) describes hyperlocal creator videos, not general-purpose promotion. [Kutumb](https://www.kutumbapp.com/) and [LocalCircles](https://www.localcircles.com/a/) are moderated human-only communities. [Arattai Channels](https://help.arattai.in/portal/en/kb/articles/about-channels) are opt-in, admin-managed follower feeds; its [Zoho Flow integration](https://help.arattai.in/portal/en/kb/articles/integrating-zoho-flow-with-arattai) documents a separate owner-authorized automation path, **not** a ReachWeaver connector or permission to message anyone. [Pratilipi](https://english.pratilipi.com/help/articles/75903534-how-do-i-publish-a-story-or-a-story-part) is original-language storytelling, not a tech ad feed; [Rooter](https://pages.rooter.gg/) is a gaming-specific creator surface. India's [2020 government blocking notice](https://pib.gov.in/PressReleasePage.aspx?PRID=1635206) listed TikTok/WeChat; current law and service availability require a separate check. [Koo announced closure in July 2024](https://indianexpress.com/article/technology/social/indian-social-media-app-koo-is-shutting-down-9430164/) and is **not** registered as an active channel.
- **Russia:** VK, OK, Dzen and Habr are research candidates. Review local law, lawful platform availability and applicable sanctions before action. [Habr rules](https://habr.com/en/docs/help/rules/) restrict advertising and self-promotion, so Habr is human-only knowledge sharing rather than a marketing channel.
- **Indonesia, Brazil, global:** KASKUS remains human-only; Kwai, Medium, Substack and Snapchat Spotlight are manual/author-controlled discovery experiments, not enabled ReachWeaver publishers.


## India pilot design (research update 2026-10-15)

| Audience/content fit | Candidate examples | ReachWeaver posture |
|---|---|---|
| Software demos for vernacular viewers | ShareChat, Moj, Josh, Chingari, Hipi | Manual original short video; do not assume automated scheduling, APIs, or reach metrics |
| Local civic/educational relevance | Public App | Only genuinely local updates; creator/editor access unverified |
| Communities or professional discussion | Kutumb, LocalCircles | Human-written and human-posted, follow moderator rules, no unsolicited cross-posting |
| A channel with an *existing opted-in audience* | Arattai Channels | Owner-admin only; no contact harvesting, DMs or automated sends in ReachWeaver |
| Original literary storytelling | Pratilipi | Native original author workflow only; don't reuse it for a generic software advertisement |
| Gaming and livestream audiences | Rooter | Manually created relevant gaming content; not the default developer growth channel |

India is not one content-language cohort: choose a genuine audience (e.g. English, Hindi, Tamil, Telugu, Bengali, Marathi, Kannada, Malayalam, Gujarati), have a fluent human review claims and scripts, and measure native results separately. A translated caption must not be treated as evidence that the advertised app supports the language. `distribution-plan --region india --goal awareness --limit 12 --json` proposes channels only; it neither uploads videos nor schedules posts. **Do not describe 2026 provider homepages as proof of current self-service API authorization.**

## Official API feasibility (issue #68)

The [regional API feasibility matrix](REGIONAL-API-FEASIBILITY-2026-10-15.md) reads the official developer documentation for Douyin, LINE Official Account, WeChat Official Accounts, WeChat Channels, Bilibili, Weibo, Xiaohongshu and Kuaishou: scopes, account eligibility, quotas, content types, scheduling, data retention and the confirmation each API returns. Every verdict is NO-GO or owner-gated CONDITIONAL; no adapter, account or registry delivery value changed, and the Douyin findings say nothing about Toutiao.

## Separate enablement gate

To move any surface beyond this safe catalog, first document official API/scopes and eligibility for the relevant country/account/content format, current native scheduling evidence, approved application access, rate limits and terms; conduct the security/privacy/IP review and a supervised owner-authorized pilot. Do not use scraping, browser impersonation to avoid restrictions, unsolicited direct messages, fake engagement or copied media. Keep exact-destination approval, dry-run default, emergency stop, credentials isolation and ambiguous-send reconciliation intact. The `distribution-plan` command remains **offline and non-executing**.

**Provenance:** Original editorial metadata and links to official or provider-operated pages; no third-party source code, user account data, media, or marketing contacts included. As-of date applies only to new research entries; previously checked platforms retain their older dates.

## India: blocking orders checked (issue #64)

#64 asks to review India's platform restrictions instead of recommending blocked apps. On 2026-10-15 the two public Ministry of Electronics and IT notices that name blocked apps were read directly: [29 June 2020, 59 apps](https://pib.gov.in/PressReleasePage.aspx?PRID=1635206) and [2 September 2020, 118 apps](https://www.pib.gov.in/PressReleasePage.aspx?PRID=1650669), both issued under section 69A of the Information Technology Act. TikTok is named in both; the first also names Helo, Vigo, Likee and UC Browser. **None of the eleven India surfaces in the registry (ShareChat, Moj, Josh, Chingari, Hipi, Public App, Kutumb, Arattai Channels, LocalCircles, Pratilipi, Rooter) appears by name in either notice**, and TikTok is not an India entry.

**Not reviewed:** later blocking orders, any order not published in these two notices, whether each app is available in Indian app stores today (Hipi in particular was not checked), and state or sectoral rules. This is a check against two published lists, not legal advice and not a statement that an app is permitted. Every India entry stays `manual_only` with `unverified` scheduling, and the owner must confirm availability and rules before using any of them.

