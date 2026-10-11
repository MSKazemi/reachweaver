"""Offline text measurement and Bluesky/Mastodon rich-text helpers (issues #12, #13).

Pure stdlib functions with no network or storage access.

Grapheme counting approximates Unicode UAX #29 extended grapheme clusters using
only ``unicodedata`` (Python has no Extended_Pictographic or Grapheme_Cluster_Break
property). Every simplification is chosen so the count can only be *higher* than
the real one, never lower: an approximate check may reject a post Bluesky would
accept, but should never accept a post Bluesky would reject. Implemented: CR LF,
controls, Hangul syllable sequences, combining marks (Mn/Me/Mc), ZWJ, variation
selectors, emoji modifiers, tag characters, emoji ZWJ sequences (approximate
pictographic ranges) and regional-indicator pairs. Not implemented (and therefore
over-counted): Prepend characters and Indic conjunct clusters (GB9b, GB9c).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

BLUESKY_MAX_GRAPHEMES = 300   # app.bsky.feed.post text maxGraphemes
BLUESKY_MAX_UTF8_BYTES = 3000  # app.bsky.feed.post text maxLength (UTF-8 bytes per Lexicon spec)
BLUESKY_TAG_MAX_GRAPHEMES = 64  # app.bsky.richtext.facet#tag
BLUESKY_TAG_MAX_BYTES = 640
MASTODON_URL_WEIGHT = 23  # characters_reserved_per_url default (instance-reported; conservative floor)

_CR, _LF, _CONTROL, _EXTEND, _ZWJ, _RI, _SPACING, _L, _V, _T, _LV, _LVT, _OTHER = range(13)

# Mc code points that UAX #29 excludes from SpacingMark (they break like Other).
_MC_NOT_SPACING = frozenset([0x102B, 0x102C, 0x1038, 0x1062, 0x1063, 0x1064, 0x1067, 0x1068,
                             0x1069, 0x106A, 0x106B, 0x106C, 0x106D, 0x1083, 0x1087, 0x1088,
                             0x1089, 0x108A, 0x108B, 0x108C, 0x108F, 0x109A, 0x109B, 0x109C,
                             0x1A61, 0x1A63, 0x1A64, 0xAA7B, 0xAA7D, 0x11720, 0x11721])

# Approximation of Extended_Pictographic (emoji-data.txt). Only consulted after a
# ZWJ that follows a pictograph; over-inclusion there is limited to emoji contexts.
_PICTOGRAPHIC = (
    (0x00A9, 0x00A9), (0x00AE, 0x00AE), (0x203C, 0x203C), (0x2049, 0x2049), (0x2122, 0x2122),
    (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B), (0x2328, 0x2328),
    (0x2388, 0x2388), (0x23CF, 0x23CF), (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2),
    (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x27BF),
    (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55),
    (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299),
    (0x1F000, 0x1F0FF), (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A), (0x1F1AD, 0x1F1E5),
    (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A), (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A),
    (0x1F23C, 0x1F23F), (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F), (0x1F7D5, 0x1F7FF), (0x1F80C, 0x1F80F),
    (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F), (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF),
    (0x1F90C, 0x1F93A), (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
)


def _is_pictographic(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _PICTOGRAPHIC)


def _break_class(cp: int) -> int:
    if cp == 0x0D:
        return _CR
    if cp == 0x0A:
        return _LF
    if cp == 0x200D:
        return _ZWJ
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return _RI
    if (0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F or cp == 0x200C
            or 0xFF9E <= cp <= 0xFF9F):
        return _EXTEND  # emoji modifiers, tag characters, ZWNJ, halfwidth sound marks
    if 0x1100 <= cp <= 0x115F or 0xA960 <= cp <= 0xA97C:
        return _L
    if 0x1160 <= cp <= 0x11A7 or 0xD7B0 <= cp <= 0xD7C6:
        return _V
    if 0x11A8 <= cp <= 0x11FF or 0xD7CB <= cp <= 0xD7FB:
        return _T
    if 0xAC00 <= cp <= 0xD7A3:
        return _LV if (cp - 0xAC00) % 28 == 0 else _LVT
    category = unicodedata.category(chr(cp))
    if category in ("Cc", "Zl", "Zp", "Cf", "Cs"):
        return _CONTROL  # Prepend Cf (e.g. U+0600) is treated as Control: over-counts only.
    if category in ("Mn", "Me"):
        return _EXTEND  # includes variation selectors FE00-FE0F and E0100-E01EF
    if category == "Mc":
        return _OTHER if cp in _MC_NOT_SPACING else _SPACING
    if cp in (0x0E33, 0x0EB3):
        return _SPACING
    return _OTHER


def graphemes(text: str) -> list[str]:
    """Split text into (approximate, never under-counted) extended grapheme clusters."""
    clusters: list[str] = []
    current: list[str] = []
    prev = None
    pict_extend = False  # current cluster ends with ExtPict Extend*
    pict_zwj = False     # ... followed by ZWJ (GB11 may join a following pictograph)
    ri_run = 0           # consecutive regional indicators ending at prev
    for ch in text:
        cp = ord(ch)
        cls = _break_class(cp)
        pict = cls == _OTHER and _is_pictographic(cp)
        if prev is None:
            join = False
        elif prev == _CR and cls == _LF:
            join = True
        elif prev in (_CONTROL, _CR, _LF) or cls in (_CONTROL, _CR, _LF):
            join = False
        elif prev == _L and cls in (_L, _V, _LV, _LVT):
            join = True
        elif prev in (_LV, _V) and cls in (_V, _T):
            join = True
        elif prev in (_LVT, _T) and cls == _T:
            join = True
        elif cls in (_EXTEND, _ZWJ, _SPACING):
            join = True
        elif pict_zwj and pict:
            join = True
        elif prev == _RI and cls == _RI and ri_run % 2 == 1:
            join = True
        else:
            join = False
        if current and not join:
            clusters.append("".join(current))
            current = []
        current.append(ch)
        pict_zwj = cls == _ZWJ and pict_extend
        pict_extend = pict or (cls == _EXTEND and pict_extend)
        ri_run = ri_run + 1 if cls == _RI else 0
        prev = cls
    if current:
        clusters.append("".join(current))
    return clusters


def grapheme_count(text: str) -> int:
    return len(graphemes(text))


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------- facets

_URL = re.compile(r"https?://[^\s<>\[\]\"']+")
_TRAILING = ".,;:!?'\"。，、！？；：」』）】〉》"
_TAG = re.compile(r"(?:^|(?<=\s))#([^\d\s#][^\s#]*)")
# Handle syntax per the docs' example parser; mentions are reported, never linked.
_MENTION = re.compile(r"(?:^|(?<=[\s(]))@((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                      r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)")


def _trim_url(link: str) -> str:
    while link:
        if link[-1] in _TRAILING:
            link = link[:-1]
        elif link[-1] == ")" and link.count(")") > link.count("("):
            link = link[:-1]
        else:
            break
    return link


def _byte_index(text: str, char_index: int) -> int:
    return len(text[:char_index].encode("utf-8"))


def _link_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for match in _URL.finditer(text):
        link = _trim_url(match.group(0))
        if len(link) > len(match.group(0).split("://", 1)[0]) + 3:
            spans.append((match.start(), match.start() + len(link), link))
    return spans


def link_facets(text: str) -> list[dict]:
    """Bluesky link facets at UTF-8 byte offsets (byteStart inclusive, byteEnd exclusive)."""
    return [_facet(text, start, end, {"$type": "app.bsky.richtext.facet#link", "uri": link})
            for start, end, link in _link_spans(text)]


def _facet(text: str, start: int, end: int, feature: dict) -> dict:
    return {"index": {"byteStart": _byte_index(text, start), "byteEnd": _byte_index(text, end)},
            "features": [feature]}


def _tag_spans(text: str, taken: list[tuple[int, int]], *, bluesky_limits: bool = True) -> list[tuple[int, int, str]]:
    spans = []
    for match in _TAG.finditer(text):
        tag = match.group(1)
        while tag and unicodedata.category(tag[-1]).startswith("P"):
            tag = tag[:-1]
        if not tag or all(unicodedata.category(c).startswith("P") for c in tag):
            continue
        if bluesky_limits and (grapheme_count(tag) > BLUESKY_TAG_MAX_GRAPHEMES
                               or utf8_len(tag) > BLUESKY_TAG_MAX_BYTES):
            continue
        start, end = match.start(), match.start(1) + len(tag)
        if any(start < t_end and t_start < end for t_start, t_end in taken):
            continue
        spans.append((start, end, tag))
    return spans


def bluesky_facets(text: str) -> list[dict]:
    """Link and hashtag facets, sorted and non-overlapping. Mentions are never faceted.

    A mention facet needs the mentioned account's DID; ReachWeaver performs no handle
    resolution, so "@handle" text is published as plain text (see unlinked_mentions).
    """
    links = _link_spans(text)
    tags = _tag_spans(text, [(s, e) for s, e, _ in links])
    facets = [(s, _facet(text, s, e, {"$type": "app.bsky.richtext.facet#link", "uri": v})) for s, e, v in links]
    facets += [(s, _facet(text, s, e, {"$type": "app.bsky.richtext.facet#tag", "tag": v})) for s, e, v in tags]
    return [facet for _, facet in sorted(facets, key=lambda item: item[0])]


def links(text: str) -> list[str]:
    """http(s) links as a reader would see them (trailing punctuation trimmed), in order."""
    return [link for _, _, link in _link_spans(text)]


def link_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, link) Python string offsets of each http(s) link."""
    return _link_spans(text)


def hashtags(text: str) -> list[str]:
    """Word-start hashtags outside links, as written ("#tag").

    Same syntax as Bluesky tag facets, but tags longer than the Bluesky facet limit are
    still counted, so a hashtag cap can never be dodged with an overlong tag.
    """
    taken = [(s, e) for s, e, _ in _link_spans(text)]
    return ["#" + tag for _, _, tag in _tag_spans(text, taken, bluesky_limits=False)]


def unlinked_mentions(text: str) -> list[str]:
    links = [(s, e) for s, e, _ in _link_spans(text)]
    return ["@" + m.group(1) for m in _MENTION.finditer(text)
            if not any(m.start() < e and s < m.end() for s, e in links)]


# ---------------------------------------------------------------- record keys

_TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"
TID_PATTERN = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")


def deterministic_tid(post_id: str, created_at: str) -> str:
    """Stable TID-syntax record key for one local post.

    The 53-bit timestamp is the draft's creation second plus a hash-derived
    sub-second offset; the 10-bit clock id is hash-derived. Re-sending the same
    post therefore targets the same record key, so the PDS should refuse a second
    create instead of publishing a duplicate. The TID timestamp is not a claim
    about publication time (record keys are user-controlled per the AT Protocol).
    """
    if not isinstance(post_id, str) or not post_id:
        raise ValueError("post_id required for a deterministic record key")
    when = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = int(when.timestamp())
    if seconds < 0:
        raise ValueError("created_at before the UNIX epoch")
    digest = hashlib.sha256(b"reachweaver-bluesky-rkey-v1:" + post_id.encode("utf-8")).digest()
    micros = seconds * 1_000_000 + int.from_bytes(digest[:4], "big") % 1_000_000
    if micros >= 1 << 53:
        raise ValueError("created_at outside TID range")
    value = (micros << 10) | (int.from_bytes(digest[4:6], "big") & 0x3FF)
    tid = "".join(_TID_ALPHABET[(value >> (5 * (12 - i))) & 31] for i in range(13))
    if not TID_PATTERN.fullmatch(tid):
        raise ValueError("derived record key is not valid TID syntax")
    return tid


# ---------------------------------------------------------------- Mastodon

def mastodon_length(text: str, url_weight: int = MASTODON_URL_WEIGHT) -> int:
    """Conservative Mastodon length: code points, each URL counted as max(len, url_weight).

    Mastodon counts each URL as characters_reserved_per_url and remote mentions by
    username only, so counting the full text (with short URLs padded up) is never lower.
    """
    total, last = 0, 0
    for start, end, _ in _link_spans(text):
        total += (start - last) + max(end - start, url_weight)
        last = end
    return total + len(text) - last


def bluesky_preview(text: str) -> dict:
    """Compact offline description of how a Bluesky record would be built."""
    facets = []
    encoded = text.encode("utf-8")
    for facet in bluesky_facets(text):
        feature = facet["features"][0]
        start, end = facet["index"]["byteStart"], facet["index"]["byteEnd"]
        facets.append({"type": feature["$type"].rsplit("#", 1)[-1],
                       "text": encoded[start:end].decode("utf-8"),
                       "byteStart": start, "byteEnd": end})
    return {"graphemes": grapheme_count(text), "grapheme_limit": BLUESKY_MAX_GRAPHEMES,
            "utf8_bytes": len(encoded), "byte_limit": BLUESKY_MAX_UTF8_BYTES,
            "grapheme_method": "approximate UAX #29 (never under-counts)",
            "facets": facets, "mentions_as_plain_text": unlinked_mentions(text)}
