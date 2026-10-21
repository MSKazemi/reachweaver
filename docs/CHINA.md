# Publishing to Chinese social platforms

ReachWeaver prepares posts for Weibo (微博), Xiaohongshu / REDnote (小红书), Douyin (抖音), Bilibili (哔哩哔哩), WeChat Official Accounts (微信公众号), Zhihu (知乎), WeChat Channels (视频号), Kuaishou (快手), Toutiao (头条号), CSDN, Juejin (稀土掘金) and OSCHINA (开源中国). **You post them yourself** in the platform's own app or creator site. ReachWeaver sends, uploads and schedules nothing on any Chinese platform.

## Why it is manual

None of these platforms has an open posting API that ReachWeaver may use for you. The details are in the [regional API feasibility study](REGIONAL-API-FEASIBILITY-2026-10-15.md) (read 2026-10-15/16):

| Platform | Official posting API | What that means |
|---|---|---|
| WeChat Official Accounts | `draft/add` + `freepublish/submit` | Only for **verified enterprise** accounts since July 2025; personal accounts lost it. ReachWeaver has no adapter. |
| Douyin | `create_video` | Test-stage "Capability Lab" permission; publishing norms bar marketing content and individual businesses. |
| Weibo | `statuses/share` only | A link-share widget for the app's own domain, not general posting. |
| Xiaohongshu | none open | Only `basic_info` is open; `write_notes` is "planned". |
| Bilibili, Kuaishou, WeChat Channels | not verified / none found | Native upload only. |

Browser automation of these sites is out of scope ([browser-assist decision](BROWSER-ASSIST-DECISION.md)).

No account yet, or the sign-up form is in a language you do not read? `reachweaver account-kit --platform weibo --persona work` prepares your nickname, e-mail, phone and bio in Simplified Chinese and lists the form labels (注册, 手机号, 验证码, 实名认证 and more) with their English meaning. You create the account and complete every code and identity check yourself ([account kit](PERSONAS-AND-ACCOUNT-KIT.md)).

## Prepare a post

```bash
reachweaver manual-export --platform weibo --variant weibo.json --out exports/weibo-launch
```

```json
{
  "platform": "weibo",
  "text": "ReachWeaver 是一个离线优先的开源工具，帮你准备社交媒体帖子。#开源工具# https://example.org/docs",
  "platform_options": {"topics": ["开源工具", "开发者社区"]}
}
```

- `text` is the exact post body. `platform_options.title` is the title (Xiaohongshu, Bilibili, WeChat, Zhihu).
- `platform_options.topics` is a list of topic words without `#`. ReachWeaver writes them in the platform's own syntax to `topics.txt` (Weibo `#开源工具#`, Xiaohongshu `#开源`, Bilibili a comma-separated tag list). It never adds them to your post text.
- Output: `post.txt` (and `title.txt`, `topics.txt`), `CHECKLIST.md` and `manual-export.json`, in a new 0700 folder. Status is always `manual_export_not_published`.
- Several posts at once: `manual-export-batch` with Asia/Shanghai times such as `"at": "2026-11-02T20:00:00+08:00"`. The time is advisory; nothing is scheduled. See [MANUAL-EXPORT.md](MANUAL-EXPORT.md#batch-of-manual-packages).

## What the package checks

| Platform | Check | Status | Source |
|---|---|---|---|
| Weibo | Post up to 5,000 characters ("最多可以发布5000汉字"); over it **blocks** | verified | [Weibo FAQ 21510](https://kefu.weibo.com/faqdetail?id=21510) |
| Weibo | Topics are `#话题词#`, 4 to 32 characters, no `# @ < > &` or line breaks; a `#tag` without the closing `#` warns | verified | [Weibo FAQ 14734](https://kefu.weibo.com/faqdetail?id=14734) |
| Weibo | Each image under 20 MB (with `--media`); over it blocks | verified | [Weibo FAQ 12483](https://kefu.weibo.com/faqdetail?id=12483) |
| Xiaohongshu | Title 20, counted the way the creator page counts it (ASCII is half a character); body 1,000; at most 10 topics | creator page code | [creator.xiaohongshu.com](https://creator.xiaohongshu.com/publish/publish) |
| Bilibili | Title under 80, description under 250, tags under 200 in total | developer API | [Bilibili open platform](https://member.bilibili.com/arcopen/user/open-doc/search?search_str=稿件&article_list_id=4) |
| WeChat Official Accounts | Title 32, content under 20,000 characters | developer API | [draft_add](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add) |
| Douyin | Text up to 1,000 characters | developer API | [create_video](https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/douyin/create-video/video-create) |
| All | Text with no Chinese characters warns: have a fluent reader review the Simplified Chinese | ReachWeaver rule | registry China note |
| All | Placeholders, control characters and Trojan Source characters block | ReachWeaver rule | [MANUAL-EXPORT.md](MANUAL-EXPORT.md#preflight-rules) |

**Only `verified` limits (an official help, rule or legal page) block.** A limit read from a creator page's own code, or from a developer API reference, is a **warning**: the page can change at any redeploy, and the in-app composer may differ from the API. Anything else reads `limit unknown — check the platform`. Every fact in `manual-export.json` carries its source URL, the date it was checked (2026-10-17), the Chinese quote and an English translation.

## Decisions only you make

Each package lists these with nothing pre-selected:

- **AI-generated content label (`ai_generated_label`).** China's *Measures for Labeling AI-Generated Synthetic Content* (《人工智能生成合成内容标识办法》, in force since 2025-09-01) Article 10: "用户使用网络信息内容传播服务发布生成合成内容的，应当主动声明并使用服务提供者提供的标识功能进行标识" (users who publish generated or synthesized content must declare it and label it with the platform's labelling function) ([CAC](https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm)). **If Claude or another assistant drafted the text, it may well count.** Use the composer's own option, for example Xiaohongshu's "笔记含AI合成内容" or Douyin's "内容由AI生成". Douyin says undeclared AI videos get limited distribution ([Douyin](https://95152.douyin.com/article/98871770975704696)).
- **Advertising label (`advertising_label`).** SAMR *Internet Advertising Measures* Article 9: ads must be identifiable, and promotion through reviews or experience posts with a purchase link must be marked "广告" ([SAMR](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_d93a579afd45413e8576e4623fab348f.html)). Xiaohongshu also asks you to declare sponsorship (利益相关) and to run brand deals through 蒲公英.
- **Originality (`originality`).** WeChat: marketing content may not be declared original (原创声明), and misuse removes the feature permanently ([WeChat rules](https://mp.weixin.qq.com/cgi-bin/readtemplate?t=business/faq_operation_tmpl)). Bilibili: choose 自制 (original) or 转载 (repost, with its source).

Other notes in the checklist: a WeChat subscription account can mass-send once a day and a service account four times a month ([Tencent support](https://kf.qq.com/faq/120911VrYVrA130805byM32u.html)); Xiaohongshu's creator page can schedule a note 1 hour to 14 days ahead (native scheduling, not ReachWeaver); Zhihu's user agreement requires a prominent label on AI-made content ([Zhihu terms](https://www.zhihu.com/term/zhihu-terms)).

## After you post

Keep the native post URL: it is the only evidence that a post exists. Record it with `reachweaver mark-shared --platform weibo --text "..." --url URL` so the [repeat guard](REPEAT-GUARD.md) warns you before the same wording goes out again. Views and likes come only from the platform's own analytics; with `--url` you can record them on the printed post id with `reachweaver metrics` or `import-metrics` ([measure posts you shared by hand](MANUAL-EXPORT.md#measure-posts-you-shared-by-hand)).

## Limits of this guide

- No row is legal advice, and no row says your account is eligible for anything. Check each platform's current rules yourself before you post.
- Douban, Maimai, Qzone and Tieba stay **human-only**: ReachWeaver writes no post text for them and gives you a notes form instead.
- A WeChat Official Accounts adapter would need a verified enterprise account, the owner's security review and a supervised live test. None exists.
