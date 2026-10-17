"""Per-platform suggestions for one SEO destination (issue #45 W4 follow-up, `seo-destinations --suggest-for`).

Given the landing page ``seo-destinations`` picked, this says, per platform, how a post can point at it: the
(tagged) link and where to put it, the length limit, the page's keyword, tags and alt text. It reads no
network, account, credential or database, and writes nothing.

Every suggestion carries a ``basis`` so advice is never mistaken for a platform rule:

* ``official``: a provider's documented limit or behaviour, with ``source`` (from ``manual_export.FACTS``).
* ``unverified``: no official statement was found; ``source`` is the page that was checked.
* ``repo_policy``: ReachWeaver's own conservative limit or rule (``policy.CONSERVATIVE_TEXT_LIMITS``,
  human-written-text platforms).
* ``hypothesis``: editorial advice. No suggestion here claims that a keyword, hashtag or link placement
  changes reach or search ranking.
"""
from __future__ import annotations

import re

from .attribution import build_utm_link
from .manual_export import FACTS
from .policy import CONSERVATIVE_TEXT_LIMITS, PLATFORMS, ValidationError
from .seo_content import HUMAN_AUTHORED_ONLY

MAX_PLATFORMS = len(PLATFORMS)
HASHTAG_PLATFORMS = frozenset({"bluesky", "mastodon", "linkedin", "x", "instagram", "tiktok", "youtube", "threads",
                               "facebook", "pinterest"})
CAPTION_LINK_UNRELIABLE = ("instagram", "tiktok")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
NOTE = ("Suggestions for a human writing the post. Only 'official' items are provider rules (see source); "
        "'hypothesis' items are editorial advice with no ranking or reach claim. Nothing was posted or fetched.")


def _item(topic: str, advice: str, basis: str, fact: dict | None = None) -> dict:
    """One suggestion; ``fact`` (from ``manual_export.FACTS``) supplies the source and the date it was checked."""
    return {"topic": topic, "advice": advice, "basis": basis, "source": fact["source"] if fact else None,
            "checked": fact["verified_date"] if fact else None}


def keyword_hashtag(keyword: str) -> str | None:
    """'approval-first social scheduling' -> '#ApprovalFirstSocialScheduling'; None when it would be unusable."""
    words = _WORD.findall(keyword)
    tag = "".join(w[:1].upper() + w[1:] for w in words)
    return "#" + tag if words and len(tag) <= 50 else None


def _fact(platform: str, key: str) -> dict | None:
    return FACTS.get(platform, {}).get(key)


def platform_suggestion(destination: dict, platform: str, campaign: str | None = None) -> dict:
    """Suggestions for one platform. ``destination`` is a validated SEO brief."""
    if platform not in PLATFORMS:
        raise ValidationError(f"Unknown platform {platform!r}; use one of {', '.join(sorted(PLATFORMS))}")
    link = (build_utm_link(destination["canonical_url"], platform=platform, campaign=campaign) if campaign
            else destination["canonical_url"])
    keyword = destination["primary_keyword"]
    items: list[dict] = []

    if platform in CAPTION_LINK_UNRELIABLE:
        fact = _fact(platform, "caption_links_clickable")
        items.append(_item("link", f"Do not rely on a link in the caption. Put {link} in your profile link and say "
                           "in the post where to find it.", "unverified", fact))
    elif platform == "youtube":
        fact = _fact("youtube", "description_links")
        items.append(_item("link", f"Put {link} in the description. Description links are clickable only for "
                           "channels that meet YouTube's requirements.", "official", fact))
    elif platform == "threads":
        fact = _fact("threads", "max_links")
        items.append(_item("link", f"Put {link} in the post text; Threads allows at most 5 links per post.",
                           "official", fact))
    else:
        items.append(_item("link", f"Put {link} in the post text, once.", "repo_policy"))
    if campaign is None:
        items.append(_item("link", "Pass --campaign to get a UTM-tagged link, so visits from this post can be told "
                           "apart in your analytics.", "repo_policy"))

    official_length = {"threads": ("text_max_chars", "characters"), "instagram": ("caption_max_chars", "characters"),
                       "tiktok": ("caption_max_utf16", "UTF-16 code units"),
                       "youtube": ("description_max_bytes", "bytes (description)")}.get(platform)
    if official_length:
        fact = _fact(platform, official_length[0])
        if fact and fact["value"] is not None:
            items.append(_item("length", f"Keep it within {fact['value']} {official_length[1]}.", "official", fact))
    if platform in CONSERVATIVE_TEXT_LIMITS:
        items.append(_item("length", f"ReachWeaver checks {platform} text against {CONSERVATIVE_TEXT_LIMITS[platform]} "
                           "characters, which may be below the platform's own limit.", "repo_policy"))

    items.append(_item("keyword", f"Say what the page is about in the first sentence, using its keyword "
                       f"“{keyword}” in your own words, so the post and the page tell the same story.",
                       "hypothesis"))
    tag = keyword_hashtag(keyword)
    if platform == "threads":
        fact = _fact("threads", "topic_tags_per_post")
        items.append(_item("tags", f"Threads allows one topic tag per post (1 to 50 characters); “{keyword}” "
                           "is a candidate if it fits.", "official", fact))
    elif platform in HASHTAG_PLATFORMS and tag:
        items.append(_item("tags", f"{tag} is one candidate hashtag; one or two relevant tags read better than many.",
                           "hypothesis"))
        if platform == "instagram":
            fact = _fact("instagram", "max_hashtags")
            items.append(_item("tags", "Instagram allows at most 30 hashtags in a caption.", "official",
                               fact))

    fact = _fact(platform, "alt_text")
    if fact and fact["value"]:
        items.append(_item("alt_text", f"Add alt text to images ({fact['value']}).", "official", fact))
    else:
        items.append(_item("alt_text", "Add alt text that describes any image for people who cannot see it.",
                           "hypothesis"))
    if platform in HUMAN_AUTHORED_ONLY:
        items.append(_item("authorship", f"{platform} expects text you wrote yourself; use these as notes, not as "
                           "generated text.", "repo_policy"))
    if platform == "reddit":
        items.append(_item("community", "Post only where the community's rules allow links to your own work; "
                           "ReachWeaver never posts to Reddit for you.", "repo_policy"))
    return {"platform": platform, "link": link, "suggestions": items}


def platform_suggestions(destination: dict | None, platforms: list[str], campaign: str | None = None) -> dict:
    """``{"platforms": [...], "note"}`` for each requested platform (in the order given, duplicates dropped)."""
    if not platforms:
        raise ValidationError("--suggest-for needs at least one platform")
    if len(platforms) > MAX_PLATFORMS:
        raise ValidationError(f"--suggest-for accepts at most {MAX_PLATFORMS} platforms")
    if destination is None:
        return {"platforms": [], "note": "No destination was chosen, so there is nothing to suggest."}
    ordered = list(dict.fromkeys(p.strip().casefold() for p in platforms))
    return {"platforms": [platform_suggestion(destination, p, campaign) for p in ordered], "note": NOTE}
