"""Creator-ready manual-export packages for app-review-gated platforms (issues #19-#23).

Threads, Instagram, Facebook Pages, YouTube and TikTok all need Meta/Google/TikTok app
review, API audits or account eligibility that the owner has not obtained. This module is
the offline fallback: it turns one variant (plus an optional media manifest) into a JSON
package and a Markdown checklist for a human to use in the platform's own app or website.

Nothing here sends, uploads, schedules or shares anything. There is no send path
(``SEND_PATH = None``), no network import and no outbox write. Every package carries
``status = "manual_export_not_published"``. Its wording never claims that a post exists.
Choices the platform assigns to the account holder (TikTok privacy, YouTube privacy and
"made for kids") are always left unselected for the human. Facts are dated and sourced.
A fact the cited official page did not establish is marked ``unknown``. Conflicting official
statements are marked ``conflicting`` and are never relied on.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, cast

from .campaigns import _sensitive_keys, public_url
from .capabilities import load_registry, require_capability
from .media import read_manifest, validate_media_manifest
from .policy import BIDI_CONTROLS, CONSERVATIVE_TEXT_LIMITS, INVISIBLE_FORMAT_CHARS, PLACEHOLDER, ValidationError
from .privacy import write_private_file
from .storage import utc_now

STATUS = "manual_export_not_published"
PACKAGE_KIND = "reachweaver.manual-export.v1"
SEND_PATH = None  # Asserted in tests: this module can never send, upload or share.
VERIFIED_DATE = "2026-10-08"
MANUAL_PLATFORMS = ("facebook", "instagram", "threads", "tiktok", "youtube")
LABELS = {"facebook": "Facebook Pages", "instagram": "Instagram", "threads": "Threads",
          "tiktok": "TikTok", "youtube": "YouTube"}

# --- Official sources fetched on VERIFIED_DATE ------------------------------------------
THREADS_POSTS = "https://developers.facebook.com/docs/threads/posts"
THREADS_START = "https://developers.facebook.com/docs/threads/get-started"
IG_PUBLISHING = "https://developers.facebook.com/docs/instagram-platform/content-publishing"
IG_MEDIA_REF = "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media"
IG_PROFILE_LINK = "https://help.instagram.com/362497417173378"
FB_PAGE_POSTS = "https://developers.facebook.com/docs/pages-api/posts"
FB_USER_FEED = "https://developers.facebook.com/docs/graph-api/reference/user/feed"
FB_GROUPS_V19 = "https://developers.facebook.com/docs/graph-api/changelog/version19.0"
YT_INSERT = "https://developers.google.com/youtube/v3/docs/videos/insert"
YT_VIDEOS = "https://developers.google.com/youtube/v3/docs/videos"
YT_REVISIONS = "https://developers.google.com/youtube/v3/revision_history"
YT_KIDS = "https://support.google.com/youtube/answer/9528076"
TT_START = "https://developers.tiktok.com/doc/content-posting-api-get-started"
TT_DIRECT = "https://developers.tiktok.com/doc/content-posting-api-reference-direct-post"
TT_GUIDELINES = "https://developers.tiktok.com/doc/content-sharing-guidelines"

# Not a FACT: the 5-tag app rule is known only from a press report quoting Instagram (its own help page
# returned HTTP 400 on 2026-10-17), so it warns and never blocks. The API reference still documents 30.
IG_APP_MAX_HASHTAGS = 5
IG_APP_HASHTAGS = {
    "value": IG_APP_MAX_HASHTAGS, "basis": "reported",
    "source": "https://www.socialmediatoday.com/news/instagram-implements-new-limits-on-hashtag-use/808309/",
    "verified_date": "2026-10-17",
    "quote": "Instagram will gradually update the number of hashtags that you can include in a caption for "
             "a reel or post to five.",
    "note": "Social Media Today, 2025-12-18, quoting Instagram. A gradual in-app rollout; separate from the "
            "publishing API's documented 30.",
}


def _fact(value, source, quote="", note="", status="verified"):
    return {"value": value, "status": status, "source": source, "verified_date": VERIFIED_DATE,
            "quote": quote, "note": note}


def _unknown(source, note):
    return _fact(None, source, note=note, status="unknown")


FACTS: dict[str, dict[str, dict]] = {
    "threads": {
        "text_max_chars": _fact(500, THREADS_POSTS, "Text posts are limited to 500 characters.",
                                "Emojis are counted as their UTF-8 byte length; ReachWeaver counts "
                                "emoji-like code points by UTF-8 bytes (conservative)."),
        "max_links": _fact(5, THREADS_POSTS, "The number of links is restricted to 5 or less."),
        "topic_tags_per_post": _fact(1, THREADS_POSTS, "Only one topic tag is allowed per post."),
        "topic_tag_length": _fact([1, 50], THREADS_POSTS, "Topic tags must be at least 1 character and no "
                                  "more than 50 characters long."),
        "api_permissions": _fact(["threads_basic", "threads_content_publish"], THREADS_START,
                                 note="threads_basic is required for all endpoints; threads_content_publish "
                                 "for publishing endpoints."),
        "app_review": _fact("App Review approval of each permission, with the app released, before users "
                            "without an app role can grant it; invited Threads testers can grant it earlier",
                            THREADS_START),
        "account_eligibility": _unknown(THREADS_START, "The fetched page does not state account-type "
                                        "requirements for publishing."),
        "media_constraints": _unknown(THREADS_POSTS, "No Threads table in reachweaver/media.py yet; "
                                      "media-check skips Threads limits with a warning."),
    },
    "instagram": {
        "caption_max_chars": _fact(2200, IG_MEDIA_REF, "Maximum 2200 characters, 30 hashtags, and 20 @ tags."),
        "max_hashtags": _fact(30, IG_MEDIA_REF, "Maximum 2200 characters, 30 hashtags, and 20 @ tags."),
        "max_mentions": _fact(20, IG_MEDIA_REF, "Maximum 2200 characters, 30 hashtags, and 20 @ tags."),
        "media_required": _fact(True, IG_PUBLISHING, note="Container creation takes image_url or video_url; "
                                "text-only posts are not part of the content publishing API."),
        "api_account_eligibility": _fact("Instagram professional account", IG_PUBLISHING,
                                         note="Applies to API publishing. The fetched pages do not split "
                                         "Business vs Creator."),
        "api_permissions": _fact({"instagram_login": ["instagram_business_basic",
                                                      "instagram_business_content_publish"],
                                  "facebook_login": ["instagram_basic", "instagram_content_publish",
                                                     "pages_read_engagement"]}, IG_PUBLISHING),
        "alt_text": _fact("up to 1000 characters; single images and carousel images only; not Reels or "
                          "stories", IG_MEDIA_REF, note="Enforced by reachweaver/media.py CONSTRAINTS['instagram']."),
        "caption_links_clickable": _unknown(IG_PROFILE_LINK, "No official statement found. Do not rely on a "
                                            "caption URL being tappable; the Help Center documents a profile "
                                            "website link."),
    },
    "facebook": {
        "target": _fact("Facebook Page only", FB_PAGE_POSTS, note="Page posts use a Page access token from a "
                        "person who can perform CREATE_CONTENT, MANAGE and MODERATE tasks on the Page."),
        "api_permissions": _fact(["pages_manage_engagement", "pages_manage_posts", "pages_read_engagement",
                                  "pages_read_user_engagement", "publish_video (video only)"], FB_PAGE_POSTS),
        "personal_profile": _fact("not supported", FB_USER_FEED, note="The User feed reference states that "
                                  "creating a post is not possible on this endpoint."),
        "groups": _fact("not supported", FB_GROUPS_V19, note="Groups API permissions, including "
                        "publish_to_groups, were removed from all versions on 2024-04-22."),
        "text_max_chars": _unknown(FB_PAGE_POSTS, "Not stated on the fetched page; ReachWeaver applies its "
                                   "conservative cap from policy.py instead."),
        "media_constraints": _unknown(FB_PAGE_POSTS, "No Facebook table in reachweaver/media.py yet."),
    },
    "youtube": {
        "title_max_chars": _fact(100, YT_VIDEOS, "The property value has a maximum length of 100 characters"),
        "description_max_bytes": _fact(5000, YT_VIDEOS, "The property value has a maximum length of 5000 bytes"),
        "forbidden_characters": _fact(["<", ">"], YT_VIDEOS, note="Title and description accept all valid "
                                      "UTF-8 characters except < and >."),
        "tags_max_chars": _fact(500, YT_VIDEOS, note="Commas between tags count; a tag containing a space "
                                "counts as if wrapped in quotation marks."),
        "privacy_status_values": _fact(["private", "unlisted", "public"], YT_VIDEOS,
                                       note="If the API request omits it, videos.insert defaults to public. "
                                       "ReachWeaver never relies on a default."),
        "made_for_kids_declaration": _fact("required", YT_KIDS, "Regardless of your location, we require you "
                                           "to tell us whether or not your videos are made for kids.",
                                           "The creator is legally responsible for the designation (COPPA)."),
        "altered_or_synthetic_disclosure": _fact("status.containsSyntheticMedia", YT_VIDEOS,
                                                 note="Lets the channel owner disclose realistic altered or "
                                                 "synthetic content."),
        "description_links": _fact("clickable only for eligible channels", YT_VIDEOS, note="URLs in the "
                                   "description render as clickable links only when the channel meets "
                                   "platform-level requirements such as verification or Advanced Features."),
        "api_upload_scopes": _fact(["https://www.googleapis.com/auth/youtube.upload",
                                    "https://www.googleapis.com/auth/youtube",
                                    "https://www.googleapis.com/auth/youtubepartner",
                                    "https://www.googleapis.com/auth/youtube.force-ssl"], YT_INSERT,
                                   note="videos.insert accepts any one of these scopes."),
        "unverified_project_private_lock": _fact(None, YT_INSERT, status="conflicting", note=(
            "The 2020-07-28 revision-history entry says videos.insert uploads from unverified API projects "
            "created after 28 July 2020 are restricted to private viewing until an audit. The videos.insert "
            "page (last updated 2026-10-08) says such uploads are not restricted to private viewing mode. "
            "Treated as unknown. It concerns API uploads only, not uploads made by the owner in YouTube "
            "Studio. Sources: " + YT_INSERT + " ; " + YT_REVISIONS)),
    },
    "tiktok": {
        "caption_max_utf16": _fact(2200, TT_DIRECT, "The maximum length is 2200 in UTF-16 runes."),
        "privacy_level_options": _fact(["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR",
                                        "SELF_ONLY"], TT_DIRECT, note="An API value must match one of the "
                                       "creator's privacy_level_options from creator_info."),
        "privacy_no_default": _fact(True, TT_GUIDELINES, "Users must manually select the privacy status from a "
                                    "dropdown and there should be no default value."),
        "interaction_defaults_off": _fact(True, TT_GUIDELINES, "Users must manually turn on these interaction "
                                          "settings and none should be checked by default."),
        "unaudited_client_visibility": _fact("private viewing mode only", TT_START, "All content posted by "
                                             "unaudited clients will be restricted to private viewing mode.",
                                             "An audit is required to lift the restriction. Applies to "
                                             "Content Posting API clients, not to the TikTok app itself."),
        "api_permissions": _fact(["video.publish"], TT_START, note="Direct Post requires approval for the "
                                 "video.publish scope and the user's authorization. Draft-upload scope "
                                 "names were not on the fetched page."),
        "caption_editable_no_watermark": _fact(True, TT_GUIDELINES, note="Preset caption text and hashtags "
                                               "must stay editable; API clients must not add promotional "
                                               "watermarks or logos."),
        "caption_links_clickable": _unknown(TT_DIRECT, "No official statement found. Do not rely on a caption "
                                            "URL being tappable."),
    },
}

ALLOWED_FORMATS = {
    "threads": ("text", "link", "image", "video", "carousel"),
    "facebook": ("text", "link", "image", "video"),
    "instagram": ("image", "video", "carousel"),
    "tiktok": ("video",),
    "youtube": ("video",),
}
DEFAULT_FORMAT = {"threads": "text", "facebook": "text"}  # Media platforms must declare a format.
MEDIA_TARGETS = {"threads": ("threads",), "facebook": ("facebook",), "instagram": ("instagram",),
                 "tiktok": ("tiktok",), "youtube": ("youtube", "youtube_shorts")}
VARIANT_FIELDS = frozenset({"schema_version", "type", "id", "draft_id", "brand_id", "campaign_id",
                            "platform", "account_id", "locale", "format", "text", "link_url",
                            "platform_options", "evidence_ids", "created_at", "media", "utm"})
KNOWN_OPTIONS = {"youtube": frozenset({"title", "tags", "shorts"}), "facebook": frozenset({"target_type"}),
                 "instagram": frozenset(), "threads": frozenset(), "tiktok": frozenset()}
# Options a variant must never pre-select: the account holder decides them in the native UI.
HUMAN_ONLY_OPTIONS = frozenset({
    "privacy", "privacy_status", "privacy_level", "visibility", "made_for_kids",
    "self_declared_made_for_kids", "selfdeclaredmadeforkids", "contains_synthetic_media",
    "is_aigc", "disable_comment", "disable_duet", "disable_stitch", "brand_content_toggle",
    "brand_organic_toggle",
})
FACEBOOK_TARGET_OK = frozenset({"page"})
FACEBOOK_TARGET_REJECTED = frozenset({"profile", "personal", "personal_profile", "user", "timeline", "group"})
ACCOUNT_META_FIELDS = frozenset({"account_id", "platform", "handle", "display_name", "account_type",
                                 "tiktok_api_audit", "notes"})
TIKTOK_AUDIT = frozenset({"unaudited", "audited", "unknown"})

_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_HASHTAG = re.compile(r"(?<![\w&#/])#(\w+)")
_MENTION = re.compile(r"(?<![\w@/.])@([\w][\w.]*)")


# --- Text measurement -------------------------------------------------------------------

def _emoji_like(ch: str) -> bool:
    cp = ord(ch)
    return (cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0xFE00 <= cp <= 0xFE0F
            or cp in (0x200D, 0x20E3) or unicodedata.category(ch) == "So")


def threads_length(text: str) -> int:
    """Conservative Threads count: emoji-like code points weigh their UTF-8 byte length."""
    return sum(len(ch.encode("utf-8")) if _emoji_like(ch) else 1 for ch in text)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def youtube_tags_length(tags: list[str]) -> int:
    """Length as counted by the YouTube docs: commas count, spaced tags count with quotes."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def extract_links(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;:!?)]}") for m in _URL.finditer(text)]


def _masked(text: str) -> str:
    return _URL.sub(" ", text)


def extract_hashtags(text: str) -> list[str]:
    return ["#" + m.group(1) for m in _HASHTAG.finditer(_masked(text))]


def extract_mentions(text: str) -> list[str]:
    return ["@" + m.group(1).rstrip(".") for m in _MENTION.finditer(_masked(text))]


def _cited(message: str, fact: dict) -> str:
    return f"{message} [source: {fact['source']}, verified {fact['verified_date']}]"


# --- Preflight ---------------------------------------------------------------------------

def _options(variant: dict) -> dict:
    options = variant.get("platform_options", {})
    return options if isinstance(options, dict) else {}


def _target_type(variant: dict, account_meta: dict | None) -> str | None:
    value = _options(variant).get("target_type")
    if value is None and isinstance(account_meta, dict):
        value = account_meta.get("account_type")
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def _check_text_common(text: str, errors: list[str], label: str = "text") -> None:
    if PLACEHOLDER.search(text):
        errors.append(f"{label} contains an unresolved placeholder/TODO")
    if any((ord(ch) < 32 and ch not in "\n\t\r") or ord(ch) == 127 for ch in text):
        errors.append(f"{label} contains control characters")
    # Same Trojan Source rule as policy.validate_post (salvaged from draft PR #65): the text the owner
    # reviews must be the text they paste.
    if any(ch in BIDI_CONTROLS for ch in text):
        errors.append(f"{label} contains bidirectional override/embedding/isolate characters (U+202A-U+202E, "
                      "U+2066-U+2069), which can make the reviewed text differ from what is pasted")
    if any(ch in INVISIBLE_FORMAT_CHARS for ch in text):
        errors.append(f"{label} contains invisible format characters (for example U+2060 word joiner or U+FEFF)")


def platform_preflight(platform: str, variant: object,
                       account_meta: dict | None = None) -> tuple[list[str], list[str]]:
    """Return (blocking errors, warnings) for one manual-export variant. Offline and pure."""
    errors: list[str] = []
    warnings: list[str] = []
    if platform not in MANUAL_PLATFORMS:
        return [f"manual-export covers {', '.join(MANUAL_PLATFORMS)}; use campaign-export for {platform!r}"], []
    if not isinstance(variant, dict):
        return ["variant must be a JSON object"], []
    errors.extend(_sensitive_keys(variant, "variant"))
    unknown = sorted(set(variant) - VARIANT_FIELDS)
    if unknown:
        warnings.append(f"variant has unrecognized fields (ignored): {', '.join(unknown)}")
    if "platform" in variant and variant["platform"] != platform:
        errors.append(f"variant.platform is {variant['platform']!r} but --platform is {platform!r}")
    if "media" in variant:
        warnings.append("variant.media is ignored; supply real files through a media manifest (--media)")
    text = variant.get("text")
    if not isinstance(text, str):
        errors.append("variant.text must be a string (exact caption/description text)")
        text = ""
    elif not text.strip() and platform != "youtube":
        errors.append("variant.text must contain substantive text")
    else:
        _check_text_common(text, errors)
    fmt = variant.get("format", DEFAULT_FORMAT.get(platform))
    if fmt is None:
        errors.append(f"variant.format is required for {LABELS[platform]} (one of "
                      f"{', '.join(ALLOWED_FORMATS[platform])})")
    elif fmt not in ALLOWED_FORMATS[platform]:
        need = {"instagram": "an image or video (text-only posts are not supported)",
                "tiktok": "a video", "youtube": "a video"}.get(platform, "a supported format")
        errors.append(f"{LABELS[platform]} needs {need}; format {fmt!r} is not one of "
                      f"{', '.join(ALLOWED_FORMATS[platform])}")
    if "link_url" in variant and not public_url(variant["link_url"]):
        errors.append("variant.link_url must be a public HTTPS URL without credentials")
    raw_options = variant.get("platform_options", {})
    if not isinstance(raw_options, dict):
        errors.append("variant.platform_options must be an object")
    options = _options(variant)
    for key in sorted(options):
        if key.casefold() in HUMAN_ONLY_OPTIONS:
            warnings.append(f"platform_options.{key} is ignored: the account holder must choose it in the "
                            f"native {LABELS[platform]} interface; ReachWeaver never pre-selects it")
        elif key not in KNOWN_OPTIONS[platform]:
            warnings.append(f"platform_options.{key} is not used for {LABELS[platform]} (ignored)")
    links, hashtags, mentions = extract_links(text), extract_hashtags(text), extract_mentions(text)
    conservative = CONSERVATIVE_TEXT_LIMITS[platform]
    facts = FACTS[platform]

    if platform == "threads":
        count = threads_length(text)
        if count > facts["text_max_chars"]["value"]:
            errors.append(_cited(f"Threads text is {count} characters (emoji counted by UTF-8 bytes); "
                                 f"limit is {facts['text_max_chars']['value']}", facts["text_max_chars"]))
        if len(links) > facts["max_links"]["value"]:
            errors.append(_cited(f"Threads text has {len(links)} links; at most 5 are allowed",
                                 facts["max_links"]))
        if len(hashtags) > 1:
            warnings.append(_cited(f"{len(hashtags)} hashtags found; Threads allows one topic tag per post, "
                                   "so the others may not become tags", facts["topic_tags_per_post"]))
        for tag in hashtags:
            if len(tag) - 1 > 50:
                warnings.append(_cited(f"topic tag {tag[:20]}... is longer than 50 characters",
                                       facts["topic_tag_length"]))
    elif platform == "instagram":
        if len(text) > 2200:
            errors.append(_cited(f"Instagram caption is {len(text)} characters; limit is 2200",
                                 facts["caption_max_chars"]))
        if len(hashtags) > 30:
            errors.append(_cited(f"Instagram caption has {len(hashtags)} hashtags; limit is 30",
                                 facts["max_hashtags"]))
        elif len(hashtags) > IG_APP_MAX_HASHTAGS:
            warnings.append(f"Instagram caption has {len(hashtags)} hashtags; the Instagram app is moving to at "
                            f"most {IG_APP_MAX_HASHTAGS} per post or reel, so keep the most relevant five "
                            f"[source: {IG_APP_HASHTAGS['source']}, reported (not an official page), checked "
                            f"{IG_APP_HASHTAGS['verified_date']}]")
        if len(mentions) > 20:
            errors.append(_cited(f"Instagram caption has {len(mentions)} @ tags; limit is 20",
                                 facts["max_mentions"]))
        if links or "link_url" in variant:
            warnings.append("Instagram: do not rely on a caption URL being tappable (unverified); put the "
                            "destination on your profile and say so in the caption")
    elif platform == "tiktok":
        count = utf16_length(text)
        if count > 2200:
            errors.append(_cited(f"TikTok caption is {count} UTF-16 units; limit is 2200",
                                 facts["caption_max_utf16"]))
        if links or "link_url" in variant:
            warnings.append("TikTok: do not rely on a caption URL being tappable (unverified); use your "
                            "profile link")
    elif platform == "youtube":
        title = options.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append("YouTube needs platform_options.title (the video title); variant.text is the "
                          "description")
        else:
            _check_text_common(title, errors, "YouTube title")
            if len(title) > 100:
                errors.append(_cited(f"YouTube title is {len(title)} characters; limit is 100",
                                     facts["title_max_chars"]))
            if "<" in title or ">" in title:
                errors.append(_cited("YouTube title must not contain < or >", facts["forbidden_characters"]))
        size = len(text.encode("utf-8"))
        if size > 5000:
            errors.append(_cited(f"YouTube description is {size} UTF-8 bytes; limit is 5000 bytes",
                                 facts["description_max_bytes"]))
        if "<" in text or ">" in text:
            errors.append(_cited("YouTube description must not contain < or >", facts["forbidden_characters"]))
        if not text.strip():
            warnings.append("YouTube description is empty")
        tags = options.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
                errors.append("platform_options.tags must be a list of non-empty strings")
            elif youtube_tags_length(tags) > 500:
                errors.append(_cited(f"YouTube tags total {youtube_tags_length(tags)} characters; limit is 500",
                                     facts["tags_max_chars"]))
        if "shorts" in options and not isinstance(options["shorts"], bool):
            errors.append("platform_options.shorts must be true or false")
        if links:
            warnings.append(_cited("description links are clickable only for eligible channels",
                                   facts["description_links"]))
    elif platform == "facebook":
        if len(text) > conservative:
            errors.append(f"Facebook text is {len(text)} characters; ReachWeaver's conservative cap is "
                          f"{conservative} (official limit unknown)")
        target = _target_type(variant, account_meta)
        if target is None:
            errors.append("Facebook target must be declared as a Page (platform_options.target_type or "
                          "account_meta.account_type = 'page'); profiles and groups are not supported")
        elif target in FACEBOOK_TARGET_REJECTED:
            errors.append(_cited(f"Facebook target {target!r} rejected: ReachWeaver exports for Facebook "
                                 "Pages only, never personal profiles or groups", facts["personal_profile"]
                                 if target != "group" else facts["groups"]))
        elif target not in FACEBOOK_TARGET_OK:
            errors.append(f"Facebook target {target!r} is not recognized; only 'page' is supported")
        meta_type = _target_type({}, account_meta)
        if target is not None and meta_type is not None and meta_type != target:
            errors.append(f"Facebook target conflict: variant says {target!r}, account metadata says "
                          f"{meta_type!r}; both must be 'page'")
    if platform != "facebook" and len(text) > conservative:
        warnings.append(f"text exceeds ReachWeaver's conservative {platform} cap of {conservative}; the "
                        "official limit above is what applies to a manual post")
    if account_meta is not None:
        errors.extend(_check_account_meta(platform, account_meta, warnings))
    return errors, warnings


def _check_account_meta(platform: str, meta: object, warnings: list[str]) -> list[str]:
    if not isinstance(meta, dict):
        return ["account metadata must be a JSON object"]
    errors = _sensitive_keys(meta, "account_meta")
    unknown = sorted(set(meta) - ACCOUNT_META_FIELDS)
    if unknown:
        warnings.append(f"account metadata has unrecognized fields (ignored): {', '.join(unknown)}")
    if "platform" in meta and meta["platform"] != platform:
        errors.append(f"account metadata is for {meta['platform']!r}, not {platform!r}")
    audit = meta.get("tiktok_api_audit", "unknown")
    if audit not in TIKTOK_AUDIT:
        errors.append("account_meta.tiktok_api_audit must be unaudited, audited or unknown")
    return errors


# --- Package -----------------------------------------------------------------------------

def _media_section(platform: str, fmt: str | None, manifest, base_dir) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = {"image": ["image"], "video": ["video"], "carousel": ["image", "video"]}.get(fmt or "", [])
    section: dict[str, Any] = {"required_kinds": required, "manifest": None, "validated": False, "assets": [],
               "constraints_reference": "reachweaver/media.py CONSTRAINTS (targets: "
                                        + ", ".join(MEDIA_TARGETS[platform]) + "); run media-check MANIFEST"}
    if manifest is None:
        if required:
            warnings.append(f"no media manifest supplied: {', '.join(required)} not validated; attach the file "
                            "yourself and run media-check before posting")
        return section, errors, warnings
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        section["manifest"] = str(path)
        manifest, base_dir = read_manifest(path), path.parent
    elif not isinstance(manifest, dict) or base_dir is None:
        raise ValidationError("media_manifest must be a manifest path, or a dict with media_base_dir")
    result = validate_media_manifest(manifest, base_dir)
    section["validated"] = True
    section["constraints_verified_date"] = result["constraints_verified_date"]
    errors.extend(f"media: {e}" for e in result["errors"])
    warnings.extend(f"media: {w}" for w in result["warnings"])
    reports = {(r.get("id"), r.get("path")): r for r in result["assets"]}
    targets = set(MEDIA_TARGETS[platform])
    for asset in manifest.get("assets", []) if isinstance(manifest.get("assets"), list) else []:
        if not isinstance(asset, dict) or not targets & set(asset.get("targets") or []):
            continue
        report = reports.get((asset.get("id"), asset.get("path")), {})
        section["assets"].append({
            "id": asset.get("id"), "kind": asset.get("kind"), "path": asset.get("path"),
            "alt_text": asset.get("alt_text"), "alt_text_localized": asset.get("alt_text_localized", {}),
            "subtitles": [s.get("path") for s in asset.get("subtitles", []) if isinstance(s, dict)],
            "burned_in_captions": asset.get("burned_in_captions") is True,
            "detected": {k: report.get(k) for k in ("detected_container", "width", "height",
                                                    "duration_seconds", "bytes")},
        })
    kinds = [a["kind"] for a in section["assets"] if a["kind"] in ("image", "video")]
    if fmt == "carousel":
        if len(kinds) < 2:
            errors.append(f"carousel needs at least 2 image/video assets targeting {platform} in the manifest")
    elif required and not any(k in required for k in kinds):
        errors.append(f"media manifest has no {required[0]} asset targeting {platform} "
                      f"(targets: {', '.join(MEDIA_TARGETS[platform])})")
    return section, errors, warnings


def _decision(key, question, options, source, note=""):
    return {"id": key, "question": question, "options": options, "selected": None, "required": True,
            "decided_by": "account holder in the native app", "source": source, "note": note}


def _human_decisions(platform: str, meta: dict) -> list[dict]:
    if platform == "tiktok":
        audit = meta.get("tiktok_api_audit", "unknown")
        return [
            _decision("privacy_level", "Who can view this video? Pick it yourself in TikTok; there is no "
                      "default.", FACTS["tiktok"]["privacy_level_options"]["value"], TT_GUIDELINES,
                      "Only the options your account offers apply. ReachWeaver never pre-selects public. "
                      f"Content Posting API audit status: {audit}. Unaudited API clients are limited to "
                      "private viewing; ReachWeaver has no TikTok API client and this package is for the "
                      "TikTok app."),
            _decision("interactions", "Allow comments, Duets and Stitches?", ["allow", "do not allow"],
                      TT_GUIDELINES, "TikTok's guidelines for API clients start these unchecked."),
            _decision("commercial_content", "Is this commercial content?",
                      ["no", "your brand", "branded content (paid partnership)"], TT_GUIDELINES,
                      "Branded content cannot be private on TikTok."),
            _decision("ai_generated_label", "Does the video contain AI-generated content that needs a label?",
                      ["yes", "no"], TT_DIRECT),
        ]
    if platform == "youtube":
        return [
            _decision("privacy_status", "Choose the visibility in YouTube Studio.",
                      FACTS["youtube"]["privacy_status_values"]["value"], YT_VIDEOS,
                      "ReachWeaver never pre-selects visibility. Check the actual visibility in Studio "
                      "after upload."),
            _decision("made_for_kids", "Is this video made for kids? (legal declaration; COPPA)",
                      ["yes, made for kids", "no, not made for kids"], YT_KIDS,
                      "Required for every video. Only the creator can make this declaration."),
            _decision("altered_or_synthetic_content", "Does the video contain realistic altered or synthetic "
                      "content that must be disclosed?", ["yes", "no"], YT_VIDEOS),
        ]
    return []


def _eligibility(platform: str, meta: dict, target: str | None) -> list[dict]:
    who = meta.get("handle") or meta.get("display_name") or meta.get("account_id") or "(not supplied)"
    items = [{"item": f"Signed in to the intended {LABELS[platform]} account: {who}", "source": None}]
    if platform == "threads":
        items.append({"item": "You control a Threads profile; posting happens in the Threads app or website",
                      "source": THREADS_START})
    elif platform == "instagram":
        items.append({"item": "Any Instagram account you control can post in the app. A future API adapter "
                      "would need an Instagram professional account; that is not required for this package",
                      "source": IG_PUBLISHING})
    elif platform == "facebook":
        items.append({"item": f"Target is a Facebook Page you manage (declared: {target or 'missing'}); never "
                      "a personal profile or group", "source": FB_PAGE_POSTS})
    elif platform == "youtube":
        items.append({"item": "You own or manage the YouTube channel and upload through YouTube Studio",
                      "source": YT_KIDS})
    elif platform == "tiktok":
        items.append({"item": "You are the creator of the TikTok account and post in the TikTok app; "
                      "the caption stays editable and no watermark is added", "source": TT_GUIDELINES})
    return items


def _link_notes(platform: str) -> list[str]:
    return {
        "threads": ["At most 5 links per post.", "Links stay as typed; ReachWeaver adds none."],
        "instagram": ["Do not rely on caption URLs being tappable (unverified). Put the destination on your "
                      "profile."],
        "tiktok": ["Do not rely on caption URLs being tappable (unverified). Use your profile link."],
        "youtube": ["Description URLs are clickable only for channels that meet YouTube's requirements "
                    "(for example verification or Advanced Features)."],
        "facebook": ["Links in Page posts stay as typed; ReachWeaver adds none."],
    }[platform]


def _api_capability(platform: str, fmt: str | None) -> dict:
    entry = next(e for e in load_registry()["platforms"] if e["id"] == platform)
    try:
        require_capability(platform, cast(str, fmt) if fmt in entry["formats"] else "video", "publish")
    except ValidationError as exc:
        allowed, reason = False, str(exc)
    else:  # pragma: no cover - the registry has no adapter for these platforms
        allowed, reason = True, "registry unexpectedly allows API publishing; investigate before use"
    return {"registry_status": entry["status"], "reachweaver_adapter": entry["reachweaver_adapter"],
            "formats": entry["formats"], "verified_date": entry["verified_date"],
            "api_publish_allowed": allowed, "reason": reason}


def build_export(platform: str, variant: dict, media_manifest=None, account_meta: dict | None = None, *,
                 media_base_dir=None, generated_at: str | None = None) -> dict:
    """Build a creator-ready manual package. It never sends anything; status is always manual-only."""
    if platform not in MANUAL_PLATFORMS:
        raise ValidationError(f"manual-export covers {', '.join(MANUAL_PLATFORMS)}; use campaign-export "
                              f"for {platform!r}")
    if not isinstance(variant, dict):
        raise ValidationError("variant must be a JSON object")
    meta = account_meta if isinstance(account_meta, dict) else {}
    errors, warnings = platform_preflight(platform, variant, account_meta)
    text = cast(str, variant.get("text")) if isinstance(variant.get("text"), str) else ""
    options = _options(variant)
    fmt = variant.get("format", DEFAULT_FORMAT.get(platform))
    media, media_errors, media_warnings = _media_section(platform, fmt if isinstance(fmt, str) else None,
                                                         media_manifest, media_base_dir)
    errors += media_errors
    warnings += media_warnings
    content: dict = {"text": text, "format": fmt}
    if platform == "youtube":
        content.update({"title": options.get("title"), "description": text,
                        "tags": options.get("tags", []), "shorts": options.get("shorts")})
        measured = {"title_chars": len(options["title"]) if isinstance(options.get("title"), str) else None,
                    "description_utf8_bytes": len(text.encode("utf-8"))}
    elif platform == "threads":
        measured = {"threads_chars": threads_length(text)}
    elif platform == "tiktok":
        measured = {"utf16_units": utf16_length(text)}
    else:
        measured = {"chars": len(text)}
    content["measured"] = measured
    title = cast(str, options.get("title")) if platform == "youtube" and isinstance(options.get("title"), str) else ""
    hashtags = extract_hashtags(title + "\n" + text)
    limit = {"instagram": 30, "threads": 1}.get(platform)
    target = _target_type(variant, account_meta) if platform == "facebook" else None
    package = {
        "kind": PACKAGE_KIND,
        "schema_version": 1,
        "status": STATUS,
        "platform": platform,
        "platform_label": LABELS[platform],
        "generated_at": generated_at or utc_now(),
        "ready_for_human_review": not errors,
        "preflight": {"errors": errors, "warnings": warnings},
        "content": content,
        "hashtags": {"items": hashtags, "count": len(hashtags), "limit": limit},
        "mentions": {"items": extract_mentions(text), "count": len(extract_mentions(text)),
                     "limit": 20 if platform == "instagram" else None},
        "links": {"in_text": extract_links(text), "link_url": variant.get("link_url"),
                  "notes": _link_notes(platform)},
        "media": media,
        "human_decisions": _human_decisions(platform, meta),
        "account": {k: meta.get(k) for k in ("account_id", "handle", "display_name", "account_type")}
                   | ({"target_type": target} if platform == "facebook" else {})
                   | ({"tiktok_api_audit": meta.get("tiktok_api_audit", "unknown")} if platform == "tiktok"
                      else {}),
        "account_eligibility": _eligibility(platform, meta, target),
        "api_capability": _api_capability(platform, fmt if isinstance(fmt, str) else None),
        "platform_facts": json.loads(json.dumps(FACTS[platform])),
        "notice": (f"MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on "
                   f"{LABELS[platform]}. This package is not evidence that a post exists. The account holder "
                   "decides whether to post it, in the platform's own app."),
    }
    package["checklist"] = _checklist(package)
    return package


def _checklist(package: dict) -> list[str]:
    platform, label = package["platform"], package["platform_label"]
    steps = ["Verify every claim against its cited source (campaign-check / claims-check); this package "
             "does not prove anything is true."]
    steps += [item["item"] for item in package["account_eligibility"]]
    if platform == "youtube":
        steps.append("Paste title.txt as the title and description.txt as the description, exactly.")
    else:
        steps.append(f"Paste caption.txt into {label} exactly; do not let tools add text, links or watermarks.")
    for asset in package["media"]["assets"]:
        steps.append(f"Attach {asset['kind']} {asset['path']}"
                     + (f"; enter alt text: {asset['alt_text']}" if asset.get("alt_text") else "")
                     + (f"; upload caption file(s): {', '.join(asset['subtitles'])}" if asset["subtitles"] else ""))
    if package["media"]["required_kinds"] and not package["media"]["validated"]:
        steps.append("Run media-check on the media you attach (alt text, captions, rights, limits).")
    if package["human_decisions"]:
        steps.append("Make each decision yourself in the native app: "
                     + ", ".join(d["id"] for d in package["human_decisions"]) + ". Nothing is pre-selected.")
    steps.append("Confirm rights, consent and disclosure for all text and media.")
    steps.append("If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver "
                 "records nothing from this package.")
    return steps


def render_markdown(package: dict) -> str:
    label = package["platform_label"]
    lines = [f"# {label} manual export checklist", "",
             f"Status: `{package['status']}`", "", package["notice"], ""]
    if package["preflight"]["errors"]:
        lines += ["## Blocking errors", ""] + [f"- {e}" for e in package["preflight"]["errors"]] + [""]
    if package["preflight"]["warnings"]:
        lines += ["## Warnings", ""] + [f"- {w}" for w in package["preflight"]["warnings"]] + [""]
    content = package["content"]
    lines += ["## Exact text", ""]
    if package["platform"] == "youtube":
        lines += [f"Title ({content['measured']['title_chars']} characters):", "", "```text",
                  content.get("title") or "", "```", "",
                  f"Description ({content['measured']['description_utf8_bytes']} UTF-8 bytes):", ""]
    lines += ["```text", content["text"], "```", ""]
    lines += ["## Hashtags, mentions and links", "",
              f"- Hashtags ({package['hashtags']['count']}"
              + (f", limit {package['hashtags']['limit']}" if package["hashtags"]["limit"] else "") + "): "
              + (", ".join(package["hashtags"]["items"]) or "none"),
              f"- Mentions ({package['mentions']['count']}): " + (", ".join(package["mentions"]["items"]) or "none"),
              "- Links: " + (", ".join(package["links"]["in_text"]) or "none")]
    if package["links"]["link_url"]:
        lines.append(f"- Destination link: {package['links']['link_url']}")
    lines += [f"- {note}" for note in package["links"]["notes"]] + [""]
    if package["human_decisions"]:
        lines += ["## Decisions only you can make (nothing is pre-selected)", ""]
        for d in package["human_decisions"]:
            lines.append(f"- [ ] `{d['id']}`: {d['question']} Options: {' / '.join(d['options'])}. "
                         f"{d['note']}".rstrip())
        lines.append("")
    lines += ["## Checklist", ""] + [f"- [ ] {step}" for step in package["checklist"]] + [""]
    api = package["api_capability"]
    lines += ["## Why this is manual", "",
              f"Registry status `{api['registry_status']}`, adapter `{api['reachweaver_adapter']}`, verified "
              f"{api['verified_date']}. {api['reason']}", ""]
    lines += ["## Sourced platform facts", ""]
    for key, fact in package["platform_facts"].items():
        value = fact["value"] if fact["value"] is not None else fact["status"]
        lines.append(f"- `{key}`: {value} ({fact['status']}, {fact['verified_date']}) {fact['source']}"
                     + (f" - {fact['note']}" if fact["note"] else ""))
    return "\n".join(lines) + "\n"


def write_export(package: dict, destination: str | Path) -> list[str]:
    """Write the package to a NEW owner-only directory (0700) of 0600 files; never overwrite."""
    if package.get("status") != STATUS or package["preflight"]["errors"]:
        raise ValidationError("Refusing to write a package with blocking preflight errors")
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ValidationError("Output directory already exists; refusing to overwrite a prior export")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(destination, 0o700)
    if os.name == "posix":
        os.chmod(destination, 0o700)
    files = {"manual-export.json": json.dumps(package, indent=2, ensure_ascii=False) + "\n",
             "CHECKLIST.md": render_markdown(package)}
    if package["platform"] == "youtube":
        files["title.txt"] = package["content"].get("title") or ""
        files["description.txt"] = package["content"]["text"]
    else:
        files["caption.txt"] = package["content"]["text"]
    written = []
    for name, text in files.items():
        path = destination / name
        write_private_file(path, text)
        written.append(str(path))
    return written
