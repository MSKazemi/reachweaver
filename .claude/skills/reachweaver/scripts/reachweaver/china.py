"""China-aware manual packages for Chinese social platforms (offline only).

No Chinese platform has an open posting API that ReachWeaver may use (see
docs/REGIONAL-API-FEASIBILITY-2026-10-15.md), so the account holder posts by hand in the platform's
own app. This module makes that hand-off China-ready: sourced text limits, CJK-aware counting, the
platform's own topic syntax, and the declarations only the account holder can make (the CAC
AI-generated content label, advertising, originality).

Every limit is a sourced fact with a status:

- ``verified``: an official help, rule or legal page says it. Only these can block a package.
- ``verified_client``: the platform's own creator web page enforces it in its code. It is real
  platform behavior but may change at any redeploy, so exceeding it is a warning.
- ``verified_api``: the platform's developer API documents it. The native composer may differ, so
  exceeding it is a warning.
- ``unknown``: shown as ``LIMIT_UNKNOWN``; nothing is checked.

Nothing here sends, uploads, schedules or opens a browser (``SEND_PATH = None``). Human-only
destinations (Douban, Maimai, Qzone, ...) keep the notes form and get no profile here.
"""
from __future__ import annotations

import math
import re
from typing import Any

SEND_PATH = None  # Asserted in tests: this module can never send, upload or share.
LIMIT_UNKNOWN = "limit unknown — check the platform"
CHECKED = "2026-10-17"
BLOCKING_STATUS = "verified"
STATUSES = ("verified", "verified_client", "verified_api", "unknown")

CAC_LABELING = "https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm"
GB_45438 = "https://openstd.samr.gov.cn/bzgk/gb/std_list?p.p1=0&p.p2=45438"
SAMR_ADS = ("https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/"
            "art_d93a579afd45413e8576e4623fab348f.html")
WEIBO_LENGTH = "https://kefu.weibo.com/faqdetail?id=21510"
WEIBO_TOPIC = "https://kefu.weibo.com/faqdetail?id=14734"
WEIBO_IMAGES = "https://kefu.weibo.com/faqdetail?id=20965"
WEIBO_IMAGE_SIZE = "https://kefu.weibo.com/faqdetail?id=12483"
XHS_PUBLISH = ("https://fe-static.xhscdn.com/formula-static/ugc/public/resource/js/project-publish-vue.8c29a95f.js "
               "(code of https://creator.xiaohongshu.com/publish/publish)")
XHS_RULES = "https://www.xiaohongshu.com/crown/community/rules"
BILI_OPEN = "https://member.bilibili.com/arcopen/user/open-doc/search?search_str=稿件&article_list_id=4"
DOUYIN_AI = "https://95152.douyin.com/article/98871770975704696"
DOUYIN_LABEL = "https://95152.douyin.com/article/4011788173291140"
DOUYIN_API = ("https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/video-management/"
              "douyin/create-video/video-create")
WECHAT_DRAFT = "https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add"
WECHAT_MASS = "https://kf.qq.com/faq/120911VrYVrA130805byM32u.html"
WECHAT_ORIGINAL = "https://mp.weixin.qq.com/cgi-bin/readtemplate?t=business/faq_operation_tmpl"
ZHIHU_TERMS = "https://www.zhihu.com/term/zhihu-terms"


def _fact(value, source, quote="", translation="", note="", status="verified", checked=CHECKED):
    assert status in STATUSES
    return {"value": value, "status": status, "source": source, "checked_on": checked,
            "quote": quote, "translation": translation, "note": note}


def _unknown(note):
    return _fact(None, None, note=note, status="unknown")


# Rules that apply to every Chinese platform, whatever its own composer offers.
LEGAL: dict[str, dict] = {
    "ai_label": _fact(
        "Users who publish AI-generated or synthesized content must declare it and label it with the "
        "platform's own labelling function", CAC_LABELING,
        "第十条 用户使用网络信息内容传播服务发布生成合成内容的，应当主动声明并使用服务提供者提供的标识功能进行标识。",
        "Art. 10: users who publish generated or synthesized content through an online content service shall "
        "proactively declare it and label it with the labelling function the service provides.",
        "CAC, MIIT, MPS and NRTA Measures for Labeling AI-Generated Synthetic Content (国信办通字〔2025〕2号), in "
        "force since 2025-09-01 (Art. 14). The same article forbids removing, altering or hiding a label. "
        "Mandatory national standard GB 45438-2025 sets the labelling method: " + GB_45438),
    "ad_label": _fact(
        "Internet ads must be identifiable as ads; product promotion through knowledge-sharing, experience "
        "posts or reviews with a purchase link must be prominently marked 广告", SAMR_ADS,
        "第九条 互联网广告应当具有可识别性，能够使消费者辨明其为广告。……通过知识介绍、体验分享、消费测评等形式推销商品或者服务，"
        "并附加购物链接等购买方式的，广告发布者应当显著标明“广告”。",
        "Art. 9: internet ads must be identifiable so consumers can tell they are ads. Promoting goods or "
        "services through knowledge introductions, experience sharing or reviews with a purchase link "
        "requires a prominent \"广告\" (advertisement) label.",
        "SAMR Measures for the Administration of Internet Advertising (Order No. 72), in force since 2023-05-01."),
}

# Per-platform facts. Keys: text_max (body), title_max, topic, images, image_max_bytes, plus notes.
PROFILES: dict[str, dict[str, Any]] = {
    "weibo": {
        "label_zh": "微博",
        "composer": "微博 app or weibo.com 发布框",
        "counting": "chars",
        "text_max": _fact(5000, WEIBO_LENGTH, "最多可以发布5000汉字", "up to 5,000 Chinese characters",
                          "ReachWeaver counts every character as one, so a mostly-Latin post may be refused "
                          "here that Weibo would accept; over 5,000, Weibo suggests a 头条文章 (article)."),
        "title_max": None,
        "topic": _fact({"syntax": "#{}#", "min": 4, "max": 32, "forbidden": "#@<>&\n\t\r"}, WEIBO_TOPIC,
                       "输入“#话题词#”……长度需在4个字（含）到32个字（含）之间",
                       "type \"#topic#\" ... the topic must be 4 to 32 characters long",
                       "A topic outside these rules is posted but gets no topic page."),
        "images": _fact("more than 9 images supported; at most 9 when images and videos are mixed",
                        WEIBO_IMAGES, "发布超9图时只需要在相册中选择超过9张图片",
                        "to post more than 9 images, select more than 9 in the album",
                        "The maximum number of images was not stated on the official FAQ."),
        "image_max_bytes": _fact(20 * 1024 * 1024, WEIBO_IMAGE_SIZE, "单张图片大小要在20M以下",
                                 "each image must be under 20 MB", "gif, jpg and png."),
        "declarations": [],
    },
    "xiaohongshu": {
        "label_zh": "小红书",
        "composer": "小红书 app or creator.xiaohongshu.com 发布笔记",
        "counting": "xhs_title",
        "text_max": _fact(1000, XHS_PUBLISH, "正文最多支持1000字", "body supports at most 1,000 characters",
                          "Read from the creator page's own code on 2026-10-17; ReachWeaver counts characters.",
                          status="verified_client"),
        "title_max": _fact(20, XHS_PUBLISH, "标题最多输入20字哦~", "the title takes at most 20 characters",
                           "The page counts a character above U+007F (and ^) as one and ASCII as half, "
                           "rounded up; ReachWeaver uses the same weighted count.", status="verified_client"),
        "topic": _fact({"syntax": "#{}", "max_count": 10}, XHS_PUBLISH, "最多支持添加10个话题",
                       "at most 10 topics", "Add each topic with the composer's topic picker (#).",
                       status="verified_client"),
        "images": _unknown("The maximum number of images per note was not established from an official page."),
        "image_max_bytes": None,
        "declarations": [
            ("ai", "笔记含AI合成内容", XHS_PUBLISH),
            ("ad", "内容包含营销广告", XHS_PUBLISH),
            ("sponsored", "利益相关 (declare sponsorship or merchant support)", XHS_RULES),
        ],
        "notes": [_fact("Sponsored or merchant-supported content must declare the interest; brand deals go "
                        "through 蒲公英", XHS_RULES, "如果你在分享和创作过程中受到商家提供的赞助或便利，请申明利益相关",
                        "if a merchant sponsored or helped you, declare the interest",
                        "Community rules page (JavaScript-rendered; read from its code).",
                        status="verified_client"),
                  _fact("Native scheduling: 1 hour to 14 days ahead", XHS_PUBLISH, "支持选择未来 1 小时～14 天内",
                        "a time from 1 hour to 14 days ahead can be chosen",
                        "Platform-native scheduling in the creator page; ReachWeaver schedules nothing.",
                        status="verified_client")],
    },
    "douyin": {
        "label_zh": "抖音",
        "composer": "抖音 app or creator.douyin.com 发布",
        "counting": "chars",
        "text_max": _fact(1000, DOUYIN_API, note="Open-platform create_video title field (up to 1,000 "
                          "characters); the app's own caption limit was not verified.", status="verified_api"),
        "title_max": None,
        "topic": None,  # The composer's # picker exists, but no official page states the syntax.
        "images": _unknown("The maximum number of images in an image post was not verified."),
        "image_max_bytes": None,
        "declarations": [("ai", "内容由AI生成", DOUYIN_LABEL)],
        "notes": [_fact("Creators must declare AI content; undeclared AI videos get limited distribution",
                        DOUYIN_AI, "对于未主动声明的AI视频内容，平台将限制传播。",
                        "AI video content that is not proactively declared will have its distribution limited.")],
    },
    "bilibili": {
        "label_zh": "哔哩哔哩",
        "composer": "Bilibili 创作中心 (member.bilibili.com) 投稿",
        "counting": "chars",
        "text_max": _fact(249, BILI_OPEN, "视频描述，长度小于250", "video description, length under 250",
                          "Open Platform submission API; the creator-centre form may allow more.",
                          status="verified_api"),
        "title_max": _fact(79, BILI_OPEN, "稿件标题，长度小于80", "submission title, length under 80",
                           "Open Platform submission API.", status="verified_api"),
        "topic": _fact({"syntax": "tags", "max_total": 199}, BILI_OPEN, "总长度小于200",
                       "tags comma-separated, total length under 200", "Open Platform submission API; enter "
                       "tags in the form's tag field, not in the description.", status="verified_api"),
        "images": None,
        "image_max_bytes": None,
        "declarations": [],
        "originality": _fact(["自制 (original)", "转载 (repost; source required)"], BILI_OPEN,
                             "1-原创，2-转载(转载时source必填)", "1 original, 2 repost (source required for a repost)",
                             status="verified_api"),
    },
    "wechat": {
        "label_zh": "微信公众号",
        "composer": "mp.weixin.qq.com 新的创作 > 文章",
        "counting": "chars",
        "text_max": _fact(19999, WECHAT_DRAFT, note="draft_add API: content under 20,000 characters and 1 MB.",
                          status="verified_api"),
        "title_max": _fact(32, WECHAT_DRAFT, note="draft_add API: title up to 32 characters.",
                           status="verified_api"),
        "topic": None,
        "images": None,
        "image_max_bytes": None,
        "declarations": [],
        "originality": _fact(["原创声明 (declare original)", "no declaration"], WECHAT_ORIGINAL,
                             "如下情形不得对文章进行原创声明……3.6.1.4 营销性质的内容",
                             "articles may not be declared original in these cases ... 3.6.1.4 marketing content",
                             "Misuse permanently removes the originality feature."),
        "notes": [_fact("Mass sends: a subscription account may send one per day, a service account four per "
                        "month", WECHAT_MASS, "公众号……每天只可以群发一条消息；服务号……每个月可群发4条消息",
                        "subscription accounts: one mass send per day; service accounts: four per month",
                        "Plan one article per send; ReachWeaver schedules nothing.")],
    },
    "zhihu": {
        "label_zh": "知乎",
        "composer": "知乎 app or zhihu.com 写文章 / 想法",
        "counting": "chars",
        "text_max": _unknown("No official text limit verified."),
        "title_max": _unknown("No official title limit verified."),
        "topic": None, "images": None, "image_max_bytes": None,
        "declarations": [],
        "notes": [_fact("Content made with generative AI that could be mistaken for real must be labelled "
                        "prominently", ZHIHU_TERMS, "利用……生成式人工智能等新技术新应用制作的非真实信息……应当以显著方式予以标识",
                        "non-real information made with generative AI ... must be labelled prominently",
                        "知乎协议 (Zhihu user agreement).")],
    },
}
# Chinese platforms with no verified platform facts: the China-wide rules still apply.
for _pid, _zh in (("wechat_channels", "视频号"), ("kuaishou", "快手"), ("toutiao", "头条号"), ("csdn", "CSDN"),
                  ("juejin", "稀土掘金"), ("oschina", "开源中国")):
    PROFILES[_pid] = {"label_zh": _zh, "composer": f"{_zh} app or creator site", "counting": "chars",
                      "text_max": _unknown("No official text limit verified."), "title_max": None,
                      "topic": None, "images": None, "image_max_bytes": None, "declarations": []}

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ebef]")
_WEIBO_TOPIC = re.compile(r"#([^#\s]{1,40})#")
_OPEN_HASH = re.compile(r"(?<![\w&#/])#(\w+)(?!#)")


def platforms() -> tuple[str, ...]:
    return tuple(sorted(PROFILES))


def is_china(platform: str) -> bool:
    return platform in PROFILES


def han_count(text: str) -> int:
    return len(_HAN.findall(text))


def xhs_title_length(text: str) -> int:
    """Xiaohongshu's own title count: UTF-16 units above U+007F (and ^) weigh 2, others 1; ceil(sum/2)."""
    units = text.encode("utf-16-le")
    weight = sum(2 if (cp := int.from_bytes(units[i:i + 2], "little")) > 127 or cp == 94 else 1
                 for i in range(0, len(units), 2))
    return math.ceil(weight / 2)


def weibo_topics(text: str) -> list[str]:
    return [m.group(1) for m in _WEIBO_TOPIC.finditer(text)]


def render_topics(platform: str, topics: list[str]) -> str:
    """Topics in the platform's own syntax, for topics.txt. Never inserted into the post text."""
    topic = PROFILES[platform].get("topic")
    syntax = topic["value"]["syntax"] if topic else "#{}"
    if syntax == "tags":
        return ",".join(topics)
    return "\n".join(syntax.format(t) for t in topics)


def _length_check(fact, measured: int, what: str, errors: list[str], warnings: list[str]) -> None:
    if not fact or fact["value"] is None or measured <= fact["value"]:
        return
    message = f"{what} is {measured}; the limit is {fact['value']} [source: {fact['source']}, {fact['status']} " \
              f"{fact['checked_on']}]"
    if fact["status"] == BLOCKING_STATUS:
        errors.append(message)
    else:
        warnings.append(message + " (not an official help page, so this is a warning: the platform may differ)")


def preflight(platform: str, text: str | None, title: str | None,
              topics: object) -> tuple[list[str], list[str]]:
    """China-specific (errors, warnings). Pure and offline; the generic checks run separately."""
    profile = PROFILES[platform]
    errors: list[str] = []
    warnings: list[str] = []
    if isinstance(text, str) and text.strip():
        _length_check(profile["text_max"], len(text), "post text length", errors, warnings)
        if not han_count(text + (title or "")):
            warnings.append(f"the text has no Chinese characters; {profile['label_zh']} readers expect "
                            "human-reviewed Simplified Chinese (registry China localization note)")
        if platform == "weibo":
            rule = profile["topic"]["value"]
            for name in weibo_topics(text):
                if not rule["min"] <= len(name) <= rule["max"] or any(ch in rule["forbidden"] for ch in name):
                    warnings.append(f"Weibo topic #{name}# must be {rule['min']}-{rule['max']} characters without "
                                    f"# @ < > & or line breaks, or it gets no topic page [source: {WEIBO_TOPIC}]")
            for name in _OPEN_HASH.findall(_WEIBO_TOPIC.sub(" ", text)):
                warnings.append(f"#{name} has no closing #: Weibo topics are written #{name}# "
                                f"[source: {WEIBO_TOPIC}]")
    if isinstance(title, str) and title.strip():
        measured = xhs_title_length(title) if profile["counting"] == "xhs_title" else len(title)
        _length_check(profile.get("title_max"), measured, "title length", errors, warnings)
    if topics is not None:
        if not isinstance(topics, list) or not all(isinstance(t, str) and t.strip() and "#" not in t
                                                   for t in topics):
            errors.append("platform_options.topics must be a list of topic words without #")
        else:
            topic = profile.get("topic")
            if topic is None:
                warnings.append(f"{profile['label_zh']} has no verified topic syntax; topics.txt uses #topic")
            else:
                rule = topic["value"]
                if "max_count" in rule and len(topics) > rule["max_count"]:
                    _length_check({**topic, "value": rule["max_count"]}, len(topics), "topic count",
                                  errors, warnings)
                if "max_total" in rule:
                    _length_check({**topic, "value": rule["max_total"]}, len(",".join(topics)),
                                  "tag total length", errors, warnings)
                if platform == "weibo":
                    for name in topics:
                        if not rule["min"] <= len(name) <= rule["max"]:
                            warnings.append(f"Weibo topic {name!r} must be {rule['min']}-{rule['max']} characters "
                                            f"[source: {WEIBO_TOPIC}]")
    return errors, warnings


def media_check(platform: str, assets: list[dict]) -> tuple[list[str], list[str]]:
    """Check attached images against a sourced per-image size limit (Weibo only so far)."""
    errors: list[str] = []
    warnings: list[str] = []
    size = PROFILES[platform].get("image_max_bytes")
    for asset in assets if size else []:
        detected = asset.get("detected") or {}
        if asset.get("kind") == "image" and isinstance(detected.get("bytes"), int):
            _length_check(size, detected["bytes"], f"image {asset.get('path')} size in bytes", errors, warnings)
    return errors, warnings


def _decision(key, question, options, source, note=""):
    return {"id": key, "question": question, "options": options, "selected": None, "required": True,
            "decided_by": "account holder in the native app", "source": source, "note": note}


def human_decisions(platform: str) -> list[dict]:
    """Declarations only the account holder can make. Nothing is pre-selected."""
    profile = PROFILES[platform]
    native = {key: label for key, label, _ in profile.get("declarations", [])}
    ai_where = (f" Use the composer's own option: {native['ai']}." if "ai" in native else
                " Use the composer's own AI label if it has one; otherwise say so prominently in the post.")
    ad_where = ""
    if "ad" in native:
        ad_where = f" Composer option: {native['ad']}" + (f"; also {native['sponsored']}" if "sponsored" in native
                                                          else "") + "."
    decisions = [
        _decision("ai_generated_label",
                  "Was any of this text, image, audio or video generated or synthesized with AI (including a "
                  "draft written by Claude or another assistant)? If yes, declare and label it." + ai_where,
                  ["yes: declare and label it", "no"], LEGAL["ai_label"]["source"],
                  "China's labelling measures (Art. 10, in force 2025-09-01) put this duty on the person who "
                  "publishes. ReachWeaver cannot tell how you made the final text, so nothing is pre-selected."),
        _decision("advertising_label",
                  "Does this promote a product or service, carry a purchase link, or was it paid for or "
                  "sponsored? If yes, mark it as an ad (广告) and declare the interest." + ad_where,
                  ["yes: mark it 广告 / declare the interest", "no"], LEGAL["ad_label"]["source"],
                  "SAMR Internet Advertising Measures Art. 9."),
    ]
    originality = profile.get("originality")
    if originality:
        decisions.append(_decision("originality", "Declare this as original, or as a repost with its source? "
                                   "Marketing content must not be declared original on WeChat.",
                                   originality["value"], originality["source"], originality["note"]))
    return decisions


def section(platform: str, text: str | None, title: str | None, topics: object) -> dict:
    """The ``china`` block of a manual package: facts, measured lengths, topics and decisions."""
    profile = PROFILES[platform]
    measured: dict[str, Any] = {}
    if isinstance(text, str):
        measured.update({"chars": len(text), "han_chars": han_count(text)})
    if isinstance(title, str):
        measured["title_chars"] = len(title)
        if profile["counting"] == "xhs_title":
            measured["title_xhs_weighted"] = xhs_title_length(title)
    clean = [t for t in topics if isinstance(t, str)] if isinstance(topics, list) else []
    facts = {key: profile[key] for key in ("text_max", "title_max", "topic", "images", "image_max_bytes",
                                            "originality") if profile.get(key)}
    return {
        "label_zh": profile["label_zh"],
        "composer": profile["composer"],
        "timezone": "Asia/Shanghai",
        "measured": measured,
        "topics": {"items": clean, "rendered": render_topics(platform, clean) if clean else None},
        "facts": facts,
        "notes": profile.get("notes", []),
        "legal": LEGAL,
        "human_decisions": human_decisions(platform),
        "api_status": ("No ReachWeaver adapter. WeChat Official Accounts has an official publish API for verified "
                       "enterprise accounts only; every other Chinese platform here has no open posting API "
                       "ReachWeaver may use (docs/REGIONAL-API-FEASIBILITY-2026-10-15.md)."),
    }


def text_limit(platform: str) -> dict:
    """The package's ``limits.text`` row, from the sourced fact when there is one."""
    fact = PROFILES[platform]["text_max"]
    if not fact or fact["value"] is None:
        return {"value": None, "display": LIMIT_UNKNOWN, "note": (fact or {}).get("note") or
                "No official text limit verified for this destination."}
    blocking = fact["status"] == BLOCKING_STATUS
    return {"value": fact["value"], "status": fact["status"], "source": fact["source"],
            "checked_on": fact["checked_on"],
            "display": f"{fact['value']} characters ({fact['status']} {fact['checked_on']}"
                       + ("" if blocking else "; warning only") + ")",
            "note": fact["note"]}


def checklist(platform: str, package: dict) -> list[str]:
    profile = PROFILES[platform]
    china = package["china"]
    steps = [f"Open {profile['label_zh']} yourself ({profile['composer']}); ReachWeaver opens nothing.",
             "Have a fluent reader review the Simplified Chinese before posting; do not paste unreviewed "
             "machine translation."]
    if china["topics"]["rendered"]:
        steps.append("Add the topics from topics.txt"
                     + (" in the tag field." if profile.get("topic") and profile["topic"]["value"]["syntax"] == "tags"
                        else " with the composer's topic picker; the post text was not changed."))
    for decision in china["human_decisions"]:
        steps.append(f"Decide yourself ({decision['id']}): {decision['question']}")
    for note in china["notes"]:
        steps.append(f"{note['value']} [{note['source']}]")
    steps.append("Time zone for Chinese audiences: Asia/Shanghai (UTC+8). Any time in this package is advisory; "
                 "nothing is scheduled.")
    return steps
