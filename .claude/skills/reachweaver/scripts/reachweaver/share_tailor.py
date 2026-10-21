"""Deterministic, explainable per-network caption fitting for ``share``. No AI, no network, no database.

One caption goes to several networks with different length rules. ``fit`` measures the caption with the
same rule ReachWeaver already applies for that network and, only when it is too long, shortens it: at the
end of the last sentence that fits when that keeps most of the text, else at the last word boundary with an
ellipsis. A closing link (the last word, or a short last line such as "Read more: https://...") is kept and the
text before it is shortened instead. The result says exactly what happened (``shortened``, both lengths, the
rule, where it was cut, and every link, hashtag and final sentence that was removed), so the person reviewing
the package can see it. ``phrase_warnings`` flags "link in bio" where a link in the post itself works (sourced
lines in ``LINKS_WORK``); it never edits the text.

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

from .manual_export import FACTS, extract_hashtags, extract_links, threads_length, utf16_length
from .policy import CONSERVATIVE_TEXT_LIMITS, LINKEDIN_ESCAPED_MAX, mastodon_text_limit
from .richtext import BLUESKY_MAX_GRAPHEMES, BLUESKY_MAX_UTF8_BYTES, grapheme_count, mastodon_length, utf8_len

ELLIPSIS = "…"
_BREAK = re.compile(r"\s+")
# A sentence ends at . ! ? (or their full-width forms), optionally followed by a closing quote or bracket.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])[\"'”’)\]]*(?=\s|$)")
_CLOSING_LINE = re.compile(r"\n[ \t]*([^\n]*https?://\S+)\s*$")
_TRAILING_URL = re.compile(r"\s(https?://\S+)\s*$")
MAX_CLOSING_CHARS = 140  # a longer last line is ordinary text, not a closing call to action


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


def _candidate(text: str, cut: int, sentence: bool = False) -> str:
    kept = text[:cut].rstrip()
    return kept if sentence else kept.rstrip(",;:") + ELLIPSIS


def _longest(text: str, cuts: list[int], ok: Callable[[str], bool], sentence: bool) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    low, high = 0, len(cuts) - 1
    while low <= high:  # every check here grows with the prefix, so a binary search finds the longest fit
        middle = (low + high) // 2
        candidate = _candidate(text, cuts[middle], sentence)
        if ok(candidate):
            best, low = (candidate, cuts[middle]), middle + 1
        else:
            high = middle - 1
    return best


def _shorten(text: str, ok: Callable[[str], bool]) -> tuple[str, str, str] | None:
    """``(shorter, "sentence" | "word", removed_text)``: the longest prefix ending a sentence that passes
    ``ok``, else the longest word-boundary prefix plus an ellipsis; None when neither fits.

    A sentence cut is used only when it keeps at least half of what a word cut would keep, so a long first
    sentence never shrinks the post to a fragment.
    """
    words = [m.start() for m in _BREAK.finditer(text) if text[:m.start()].strip()]
    sentences = [m.end() for m in _SENTENCE_END.finditer(text) if text[:m.end()].strip()]
    by_word = _longest(text, words, ok, sentence=False)
    by_sentence = _longest(text, sentences, ok, sentence=True)
    if by_sentence and (by_word is None or len(by_sentence[0]) * 2 >= len(by_word[0])):
        return by_sentence[0], "sentence", text[by_sentence[1]:].strip()
    return (by_word[0], "word", text[by_word[1]:].strip()) if by_word else None


def _closing(text: str) -> tuple[str, str, str] | None:
    """``(body, separator, closing)`` when the text ends with a link: the last line holding it, or the link."""
    match = _CLOSING_LINE.search(text)
    if match and match.start(1) > 0 and len(match.group(1)) <= MAX_CLOSING_CHARS:
        return text[:match.start(1)].rstrip(), "\n", match.group(1).strip()
    match = _TRAILING_URL.search(text)
    if match and match.start(1) > 0:
        return text[:match.start(1)].rstrip(), " ", match.group(1)
    return None


def fit(platform: str, text: str, *, shorten: bool = True) -> dict:
    """``{"text", "shortened", "rule", "original_chars", "chars", "removed_hashtags", "removed_links",
    "cut_at", "kept_closing", "removed_text", "note"}``. Pure.

    When the caption ends with a link (alone, or on a short last line such as "Read more: https://..."), that
    closing is kept and the text before it is shortened instead. Everything removed is shown in ``note``: the
    removed passage (its start and its end, so a closing call to action is visible), every link and hashtag.
    """
    rule = RULES.get(platform)
    result = {"text": text, "shortened": False, "rule": rule[0] if rule else None,
              "original_chars": len(text), "chars": len(text), "removed_hashtags": [], "removed_links": [],
              "cut_at": None, "kept_closing": None, "removed_text": None, "note": None}
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
    found: tuple[str, str, str, str | None] | None = None
    closing = _closing(text)
    if closing:
        body, separator, tail = closing
        fitted = _shorten(body, lambda b: rule[1](b + separator + tail))
        if fitted:
            found = (fitted[0] + separator + tail, fitted[1], fitted[2], tail)
    if found is None:
        fitted = _shorten(text, rule[1])
        if fitted:
            found = (fitted[0], fitted[1], fitted[2], None)
    if found is None:
        result["note"] = f"Too long for {rule[0]}, and no word boundary leaves a short enough text; shorten it yourself"
        return result
    shorter, cut_at, cut_text, kept = found
    removed = [tag for tag in dict.fromkeys(extract_hashtags(text)) if tag not in extract_hashtags(shorter)]
    links = [link for link in dict.fromkeys(extract_links(text)) if link not in shorter]
    details = ["cut at " + ("the end of a sentence" if cut_at == "sentence" else "a word (marked …)"),
               "removed: " + _quote(cut_text)]
    if kept:
        details.append(f"kept your closing link: {kept}")
    if links:
        details.append("LINK REMOVED: " + ", ".join(links))
    if removed:
        details.append("hashtags cut: " + ", ".join(removed))
    result.update(text=shorter, shortened=True, chars=len(shorter), removed_hashtags=removed, removed_links=links,
                  cut_at=cut_at, kept_closing=kept, removed_text=cut_text,
                  note=(f"SHORTENED from {len(text)} to {len(shorter)} characters to fit {rule[0]}; "
                        + "; ".join(details) + ". Read the ending before you post"))
    return result


def _quote(text: str, head: int = 40, tail: int = 40) -> str:
    """The removed passage on one line; a long one shows its start and its end (where a call to action sits)."""
    flat = " ".join(text.split())
    if len(flat) <= head + tail + 5:
        return "'" + flat + "'"
    return "'" + flat[:head].rstrip() + f" {ELLIPSIS} " + flat[-tail:].lstrip() + "'"


# "link in bio" sends readers to a profile link; where a link in the post itself works, say so. Each line is
# a sourced statement (URL, checked date); LinkedIn has none, so its line says that it is unverified.
LINK_IN_BIO = re.compile(r"\blinks? in (?:my |the |our )?bio\b", re.I)
CHECKED = "2026-10-21"
LINKS_WORK: dict[str, str] = {
    "bluesky": ("Links are clickable on Bluesky (posts use rich text for links: "
                "https://docs.bsky.app/docs/about-bluesky-content/post-richtext, checked " + CHECKED + ")"),
    "telegram": ("Links are clickable on Telegram (URLs in a message become clickable entities: "
                 "https://core.telegram.org/api/entities, checked " + CHECKED + ")"),
    "mastodon": ("Mastodon posts carry links that start with https:// "
                 "(https://docs.joinmastodon.org/user/posting/, checked " + CHECKED + ")"),
    "linkedin": "A LinkedIn post can hold the link itself (whether it shows as clickable is unverified here)",
}


def phrase_warnings(platform: str, text: str) -> list[str]:
    """Phrases that do not fit this network. Advice only; the text is never changed."""
    if platform in LINKS_WORK and LINK_IN_BIO.search(text):
        return [f"{LINKS_WORK[platform]}; replace 'link in bio' with the link itself"]
    return []
