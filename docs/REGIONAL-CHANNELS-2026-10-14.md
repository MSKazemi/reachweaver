# Regional social channels and country pilots — 2026-10-14

**Research-only expansion ([issue #64](https://github.com/MSKazemi/reachweaver/issues/64)).** The [distribution registry](../reachweaver/distribution_registry.json) adds 27 distinct channels and four markets. The separate [API capability matrix](PLATFORM-MATRIX.md) is intentionally unchanged. **No OAuth, API adapter, remote scheduler, account access, scraping, content generation for human-only channels, or posts have been enabled.** All new entries are `manual_only`, scheduling `unverified`, checked on 2026-10-14; provider home/help pages are pointers for follow-up review, **not proof of access or scheduling support**.

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
- **India:** ShareChat/Moj are candidate locally relevant creator surfaces. [ShareChat's creator rules](https://help.sharechat.com/faq/original-creator/) describe original vs copied posts. India's [2020 government blocking notice](https://pib.gov.in/PressReleasePage.aspx?PRID=1635206) included TikTok and WeChat. Since this notice is historical, check present law/availability; do **not** recommend TikTok in the India pilot without independent reassessment.
- **Russia:** VK, OK, Dzen and Habr are research candidates. Review local law, lawful platform availability and applicable sanctions before action. [Habr rules](https://habr.com/en/docs/help/rules/) restrict advertising and self-promotion, so Habr is human-only knowledge sharing rather than a marketing channel.
- **Indonesia, Brazil, global:** KASKUS remains human-only; Kwai, Medium, Substack and Snapchat Spotlight are manual/author-controlled discovery experiments, not enabled ReachWeaver publishers.

## Separate enablement gate

To move any surface beyond this safe catalog, first document official API/scopes and eligibility for the relevant country/account/content format, current native scheduling evidence, approved application access, rate limits and terms; conduct the security/privacy/IP review and a supervised owner-authorized pilot. Do not use scraping, browser impersonation to avoid restrictions, unsolicited direct messages, fake engagement or copied media. Keep exact-destination approval, dry-run default, emergency stop, credentials isolation and ambiguous-send reconciliation intact. The `distribution-plan` command remains **offline and non-executing**.

**Provenance:** Original editorial metadata and links to official or provider-operated pages; no third-party source code, user account data, media, or marketing contacts included. As-of date applies only to new research entries; previously checked platforms retain their older dates.
