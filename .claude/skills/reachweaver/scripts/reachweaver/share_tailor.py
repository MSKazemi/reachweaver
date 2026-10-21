"""Deterministic, explainable per-network caption fitting for ``share``. No AI, no network, no database.

One caption goes to several networks with different length rules. ``fit`` measures the caption with the
same rule ReachWeaver already applies for that network and, only when it is too long, shortens it at the
last word boundary that fits and adds an ellipsis. The result says exactly what happened (``shortened``,
both lengths, the rule and the hashtags that were cut), so the person reviewing the package can see it.

Rules come from code that already exists, never from new research:

* Bluesky, Mastodon, Telegram, LinkedIn: the rule ``draft`` and ``check`` use (``policy.validate_post``).
* Threads, Instagram, TikTok, YouTube description: the sourced limits in ``manual_export.FACTS``.
* Facebook Pages: ReachWeaver's conservative cap (the official limit is unknown, as manual-export says).

Every other network has no known limit here, so its text is left exactly as written. Hashtags are never
moved: the repository has no sourced rule for moving them on any network, so the text keeps its order and
the existing per-network hashtag warnings still apply.
"""
from __future__ import annotations

import re
from typing import Callable

from .manual_export import FACTS, extract_hashtags, threads_length, utf16_length
from .policy import CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, mastodon_text_limit
from .richtext import BLUESKY_MAX_GRAPHEMES, BLUESKY_MAX_UTF8_BYTES, grapheme_count, mastodon_length, utf8_len

ELLIPSIS = "…"
_BREAK = re.compile(r"\s+")


def _linkedin_ok(text: str) -> bool:
    from .linkedin import escape_commentary  # lazy, as policy.validate_post does
    return (len(text) <= CONSERVATIVE_TEXT_LIMITS["linkedin"]
            and len(escape_commentary(text)) <= LINKEDIN_ESCAPED_MAX)


# platform -> (plain-language rule, check). The check is True when the text fits.
RULES: dict[str, tuple[str, Callable[[str], bool]]] = {
    "bluesky": (f"{BLUESKY_MAX_GRAPHEMES} graphemes and {BLUESKY_MAX_UTF8_BYTES} UTF-8 bytes, the rule draft and "
                "check use for Bluesky",
                lambda t: grapheme_count(t) <= BLUESKY_MAX_GRAPHEMES and utf8_len(t) <= BLUESKY_MAX_UTF8_BYTES),
    "mastodon": (f"{mastodon_text_limit(None)} characters with each link counted as at least 23, the conservative "
                 "rule draft and check use for Mastodon (your server may allow more)",
                 lambda t: mastodon_length(t) <= mastodon_text_limit(None)),
    "telegram": (f"{CONSERVATIVE_TEXT_LIMITS['telegram']} characters, the conservative rule draft and check use "
                 "for Telegram",
                 lambda t: len(t) <= CONSERVATIVE_TEXT_LIMITS["telegram"]),
    "linkedin": (f"{CONSERVATIVE_TEXT_LIMITS['linkedin']} characters and {LINKEDIN_ESCAPED_MAX} after escaping, "
                 "the conservative rule draft and check use for LinkedIn", _linkedin_ok),
    "threads": (f"{FACTS['threads']['text_max_chars']['value']} characters with emoji counted by UTF-8 bytes "
                f"(official, {FACTS['threads']['text_max_chars']['source']})",
                lambda t: threads_length(t) <= FACTS["threads"]["text_max_chars"]["value"]),
    "instagram": (f"{FACTS['instagram']['caption_max_chars']['value']} characters (official, "
                  f"{FACTS['instagram']['caption_max_chars']['source']})",
                  lambda t: len(t) <= FACTS["instagram"]["caption_max_chars"]["value"]),
    "tiktok": (f"{FACTS['tiktok']['caption_max_utf16']['value']} UTF-16 units (official, "
               f"{FACTS['tiktok']['caption_max_utf16']['source']})",
               lambda t: utf16_length(t) <= FACTS["tiktok"]["caption_max_utf16"]["value"]),
    "youtube": (f"{FACTS['youtube']['description_max_bytes']['value']} UTF-8 bytes for the description (official, "
                f"{FACTS['youtube']['description_max_bytes']['source']})",
                lambda t: len(t.encode("utf-8")) <= FACTS["youtube"]["description_max_bytes"]["value"]),
    "facebook": (f"{CONSERVATIVE_TEXT_LIMITS['facebook']} characters, ReachWeaver's conservative cap (the official "
                 "limit is unknown)",
                 lambda t: len(t) <= CONSERVATIVE_TEXT_LIMITS["facebook"]),
}
# Share ids whose ReachWeaver guardrail lives under another policy id.
POLICY_ID = {"dev": "devto"}


def _candidate(text: str, cut: int) -> str:
    return text[:cut].rstrip().rstrip(",;:") + ELLIPSIS


def _shorten(text: str, ok: Callable[[str], bool]) -> str | None:
    """Longest word-boundary prefix plus an ellipsis that passes ``ok``; None when no word boundary fits."""
    cuts = [m.start() for m in _BREAK.finditer(text) if text[:m.start()].strip()]
    best: str | None = None
    low, high = 0, len(cuts) - 1
    while low <= high:  # every check here grows with the prefix, so a binary search finds the longest fit
        middle = (low + high) // 2
        candidate = _candidate(text, cuts[middle])
        if ok(candidate):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def fit(platform: str, text: str, *, shorten: bool = True) -> dict:
    """``{"text", "shortened", "rule", "original_chars", "chars", "removed_hashtags", "note"}``. Pure."""
    rule = RULES.get(platform)
    result = {"text": text, "shortened": False, "rule": rule[0] if rule else None,
              "original_chars": len(text), "chars": len(text), "removed_hashtags": [], "note": None}
    if rule is None:
        guard = CONSERVATIVE_TEXT_LIMITS.get(POLICY_ID.get(platform, platform))
        if guard is not None and len(text) > guard:
            result["note"] = (f"Text is {len(text)} characters. ReachWeaver has no official limit for this network; its "
                              f"internal guardrail is {guard} (not a platform rule), so check the app's own counter. "
                              "Left as written")
        return result
    if rule[1](text):
        return result
    if not shorten:
        result["note"] = f"Too long for {rule[0]}; left as written because --no-shorten was given"
        return result
    shorter = _shorten(text, rule[1])
    if shorter is None:
        result["note"] = f"Too long for {rule[0]}, and no word boundary leaves a short enough text; shorten it yourself"
        return result
    removed = [tag for tag in dict.fromkeys(extract_hashtags(text)) if tag not in extract_hashtags(shorter)]
    result.update(text=shorter, shortened=True, chars=len(shorter), removed_hashtags=removed,
                  note=(f"SHORTENED from {len(text)} to {len(shorter)} characters to fit {rule[0]}. Read the ending "
                        "before you post" + (f"; hashtags cut: {', '.join(removed)}" if removed else "")))
    return result
