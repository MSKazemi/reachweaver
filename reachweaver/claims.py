"""Deterministic, offline LEXICAL claim grounding for campaign variants.

This module compares the *words and numbers* of each draft with the text a human
declared as evidence (`evidence[].claim` and optional `evidence[].excerpt`). It is
not truth verification: it never fetches a source, cannot tell whether an excerpt
is genuine or current, and cannot understand paraphrase, negation or context. A
passing report means "every checked token has a lexical anchor in cited evidence",
nothing more. Human source review remains mandatory.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import urlparse

from .richtext import hashtags

DEFAULT_DUPLICATE_THRESHOLD = 0.6
PART_SEPARATOR = "\n\n"  # joins thread parts / carousel caption + slides into one checked body
BRAND_RULE_FIELDS = ("banned_phrases", "required_disclosure", "max_hashtags")
SEVERITIES = ("error", "review", "warning")
EVIDENCE_KINDS = frozenset({
    "release", "repository", "documentation", "paper", "benchmark", "announcement",
    "testimonial", "partnership", "metric", "other",
})
LIMITATION = ("Lexical grounding only: tokens and phrases are matched against declared evidence "
              "text. This does not verify that a source is real, current or says what the excerpt "
              "claims, and it cannot judge paraphrase, negation or context.")

_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\"'“”]+", re.I)
_MONTHS = {name: index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",),
    ("jun", "june"), ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"),
    ("oct", "october"), ("nov", "november"), ("dec", "december")), start=1) for name in names}
_MONTH = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_ORD = r"(?:st|nd|rd|th)?"
_DATES = (
    re.compile(r"(?<![\w.])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?(?![\w])"),
    re.compile(r"(?<![\w./])(?P<a>\d{1,2})[/.](?P<b>\d{1,2})[/.](?P<y>\d{4})(?![\w])"),
    re.compile(rf"(?<!\w){_MONTH}\s+(?P<d>\d{{1,2}}){_ORD},?\s+(?P<y>\d{{4}})(?!\w)", re.I),
    re.compile(rf"(?<![\w.])(?P<d>\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MONTH},?\s+(?P<y>\d{{4}})(?!\w)", re.I),
    re.compile(rf"(?<!\w){_MONTH}\s+(?P<y>\d{{4}})(?!\w)", re.I),
    re.compile(rf"(?<!\w){_MONTH}\s+(?P<d>\d{{1,2}}){_ORD}(?!\w)", re.I),
    re.compile(rf"(?<![\w.])(?P<d>\d{{1,2}}){_ORD}\s+(?:of\s+)?{_MONTH}(?!\w)", re.I),
    re.compile(r"(?<![\w:.])(?P<h>\d{1,2}):(?P<mi>\d{2})(?::\d{2})?(?:\s?(?P<ap>am|pm))?(?![\w:])", re.I),
    re.compile(r"(?<![\w:.])(?P<h>\d{1,2})\s?(?P<ap>am|pm)(?!\w)", re.I),
)
_NUMBER = re.compile(r"""
    (?<![\w.,'’])
    (?P<v>[vV](?=\d))?
    (?P<n>\d+(?:[.,'’]\d+|\ \d{3}(?!\d))*)
    (?:
        \ ?(?P<pct>%|percent\b|per\ cent\b)
      | (?P<mult>\ ?[xX×]|-fold|\ times)(?!\w)
      | (?P<scale>\ ?(?:[kK]|M|B|bn)(?!\w)|\ (?:thousand|million|billion)\b)
      | (?P<ord>st|nd|rd|th)(?!\w)
    )?
    (?!\w)
""", re.X)
_SCALE = {"k": 1000, "m": 10**6, "b": 10**9, "bn": 10**9, "thousand": 1000, "million": 10**6, "billion": 10**9}

_RELEASE = re.compile(
    r"\b(?:released|re-released|launch(?:ed|es|ing)|shipped|out now|now (?:available|live|out)|"
    r"available (?:now|today)|is (?:now )?live|goes live|went live|general availability|just dropped)\b", re.I)
_RISKY = (
    ("partnership", re.compile(
        r"\b(?:partner(?:ed|s|ing)? with|in partnership with|official partner|endorsed by|endorsement (?:from|by)|"
        r"backed by|sponsored by|funded by|certified by|recommended by|trusted by|used by|relied (?:up)?on by|"
        r"adopted by|chosen by|in collaboration with|collaborat(?:ed|ing) with)\b", re.I)),
    ("testimonial", re.compile(
        r"\b(?:said|says|told (?:us|me)|according to|testimonials?|raved|praised|loved by|swears by)\b", re.I)),
    ("superlative", re.compile(
        r"\bbest\b(?![- ]practices?\b)|\b(?:fastest|quickest|cheapest|largest|biggest|smallest|"
        r"most (?:popular|advanced|powerful|secure|reliable|accurate|complete|comprehensive|trusted|used|loved)|"
        r"market[- ]leading|industry[- ]leading|the leading|unmatched|unrivall?ed|unparalleled|world[- ]class|"
        r"state[- ]of[- ]the[- ]art|number one|no\.\s?1|first[- ]ever|(?:the|world['’]s|industry['’]s) first|"
        r"first (?:to|of its kind)|the only|only one)\b|(?<!\w)#\s?1(?!\d)", re.I)),
)
_CUSTOMER = re.compile(
    r"(?<![\w.,])(?P<qty>\d[\d.,'’ ]*\+?\s?(?:[kKMB]|bn|thousand|million|billion)?\+?|hundreds|thousands|millions|dozens)"
    r"\s+(?:of\s+)?(?:(?:happy|active|paying|satisfied|monthly|daily|weekly|registered|github)\s+)?"
    r"(?P<noun>users?|customers?|compan(?:y|ies)|teams?|developers?|downloads?|stars?|installs?|installations?|"
    r"organi[sz]ations?|orgs?|clients?|members?|subscribers?|followers?|contributors?|maintainers?|engineers?|"
    r"businesses|enterprises?|deployments?)\b", re.I)
# Feature/capability claims: a trigger phrase followed by the object it asserts. The object's
# content terms (stopwords removed, simple plurals folded) must all occur in cited evidence.
_FEATURE = re.compile(r"""
    (?<![\w-])(?:
        (?:now|finally)\s+(?:has|have|offers?|includes?|handles?|supports?|integrates\s+(?:with|into)|
            (?:works|comes|ships)\s+with|with|lets\s+you|can)
      | can\s+now
      | (?:adds?|added|adding)(?:\s+(?:native\s+|full\s+|first[- ]class\s+|built[- ]in\s+)?support\s+for)?
      | introduc(?:es|ed|ing)
      | supports | support\s+for
      | integrat(?:es|ed|ing)\s+(?:with|into) | integrations?\s+(?:with|for)
      | (?:works|ships|comes)\s+with | compatible\s+with
      | includes
    )(?![\w-])
""", re.X | re.I)
# Where the asserted object ends: sentence punctuation, brackets/quotes, dashes or a new clause.
_FEATURE_END = re.compile(
    r"[;:!?\n()\[\]{}\"“”«»—–]|\.(?=\s|$)|,?\s+(?:that|which|who|so|because|while|when|whenever|but|if|"
    r"unless|until|without|instead|than|not|and\s+(?:it|we|you|they|this|that))\b", re.I)
_NEGATION = re.compile(r"(?:\b(?:not|no|never|nor|without|cannot)|n['’]t)\s+(?:\w+\s+){0,2}$", re.I)
FEATURE_WINDOW_WORDS = 8  # at most this many words after the trigger are read as the claimed feature
FEATURE_STOPWORDS = frozenset("""
    a an the and or nor of to in on at by for from with without into onto over under via per as than then
    is are was were be been being am has have had do does did done it its itself this that these those there here
    you your yours we us our ours they them their i me my he she him his her who whom whose what which
    all any some every each both either neither more most much many few several other another such same own
    not no only just also even really very quite too so now today finally officially already still soon
    can could will would should may might must shall able let lets help helps get gets got make makes made
    use uses using used work works working run runs running go goes new newly native natively full fully
    first class built builtin out box better faster easier easy easily simple simply seamless seamlessly
    smooth smoothly powerful great nice improved improvement improvements enhanced richer flexible robust
    support supports supported supporting feature features capability capabilities ability option options
    way ways tool tools thing things lot lots plus etc like one ones kind kinds type types set sets
    across between through about up down off along around within whole entire various multiple
    against before after during behind beyond toward towards upon since inside outside near
""".split())
_TERM = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
_QUOTES = re.compile(r"“([^”\n]{1,400})”|\"([^\"\n]{1,400})\"|„([^“”\n]{1,400})[“”]|«\s?([^»\n]{1,400}?)\s?»|「([^」\n]{1,400})」")
_WORD = re.compile(r"\w+")


def _normalize(text: str) -> tuple[str, list[int]]:
    """Char-wise NFKC with Unicode decimal digits mapped to ASCII; keeps an offset map."""
    out: list[str] = []
    offsets: list[int] = []
    for index, ch in enumerate(text):
        category = unicodedata.category(ch)
        if category == "Nd":
            mapped = str(unicodedata.decimal(ch))
        elif ch == "٫":
            mapped = "."
        elif ch == "٬":
            mapped = ","
        elif category == "No":
            mapped = ch  # superscripts/fractions would otherwise fabricate digits
        else:
            mapped = unicodedata.normalize("NFKC", ch)
        for piece in mapped:
            out.append(piece)
            offsets.append(index)
    offsets.append(len(text))
    return "".join(out), offsets


def _fold(text: str) -> str:
    return " ".join(_normalize(text)[0].casefold().split())


def _canon(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _readings(raw: str) -> tuple[set[str], set[str]]:
    s = raw.replace("’", "'")
    values: set[str] = set()
    try:
        if re.fullmatch(r"\d+", s):
            values.add(_canon(Decimal(s)))
        if re.fullmatch(r"\d{1,3}(?:([.,' ])\d{3})(?:\1\d{3})*", s):
            values.add(_canon(Decimal(re.sub(r"[.,' ]", "", s))))
        if re.fullmatch(r"\d+[.,]\d+", s):
            values.add(_canon(Decimal(s.replace(",", "."))))
        m = re.fullmatch(r"(\d{1,3}(?:([.,' ])\d{3})(?:\2\d{3})*)([.,])(\d+)", s)
        if m and m.group(2) != m.group(3):
            values.add(_canon(Decimal(re.sub(r"[.,' ]", "", m.group(1)) + "." + m.group(4))))
    except InvalidOperation:
        pass
    versions = {s} if re.fullmatch(r"\d+(?:\.\d+)+", s) else set()
    return values, versions


def _mask(text: str, spans) -> str:
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _date_token(match, pattern_index: int) -> tuple[set, set] | None:
    g = match.groupdict()
    if g.get("mon") == "may":
        return None  # the lowercase modal verb, not the month
    try:
        if pattern_index == 0:
            day = date(int(g["y"]), int(g["m"]), int(g["d"]))
            return _date_keys([day])
        if pattern_index == 1:
            days = []
            for d, m in ((g["a"], g["b"]), (g["b"], g["a"])):
                try:
                    days.append(date(int(g["y"]), int(m), int(d)))
                except ValueError:
                    pass
            return _date_keys(days) if days else None
        if pattern_index in (2, 3):
            return _date_keys([date(int(g["y"]), _MONTHS[g["mon"].casefold().rstrip(".")], int(g["d"]))])
        if pattern_index == 4:
            key = ("date", f"{int(g['y']):04d}-{_MONTHS[g['mon'].casefold().rstrip('.')]:02d}")
            return {key}, {key, ("num", str(int(g["y"])))}
        if pattern_index in (5, 6):
            month = _MONTHS[g["mon"].casefold().rstrip(".")]
            date(2000, month, int(g["d"]))  # leap-year tolerant range check
            key = ("date", f"--{month:02d}-{int(g['d']):02d}")
            return {key}, {key}
        hour, minute = int(g["h"]), int(g.get("mi") or 0)
        if g.get("ap"):
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if g["ap"].casefold() == "pm" else 0)
        if hour > 23 or minute > 59:
            return None
        key = ("time", f"{hour:02d}:{minute:02d}")
        return {key}, {key}
    except (ValueError, KeyError):
        return None


def _date_keys(days) -> tuple[set, set]:
    claims = {("date", d.isoformat()) for d in days}
    provided = set(claims)
    for d in days:
        provided |= {("date", f"{d.year:04d}-{d.month:02d}"), ("date", f"--{d.month:02d}-{d.day:02d}"),
                     ("num", str(d.year))}
    return claims, provided


def extract_tokens(text: str) -> list[dict]:
    """Return number/percentage/version/date/multiplier tokens with original offsets.

    Each token has `keys` (what the text asserts) and `provides` (what it can ground
    when it occurs in evidence). Ambiguous locale separators keep every plausible reading.
    """
    norm, offsets = _normalize(text)
    masked = _mask(norm, [m.span() for m in _URL.finditer(norm)])
    tokens: list[dict] = []

    def add(start: int, end: int, kind: str, keys: set, provides: set):
        tokens.append({"kind": kind, "span": text[offsets[start]:offsets[end - 1] + 1],
                       "start": offsets[start], "end": offsets[end - 1] + 1,
                       "keys": keys, "provides": provides | keys})

    for index, pattern in enumerate(_DATES):
        taken = []
        for match in pattern.finditer(masked):
            result = _date_token(match, index)
            if result:
                add(*match.span(), "time" if index >= 7 else "date", *result)
                taken.append(match.span())
        masked = _mask(masked, taken)
    for match in _NUMBER.finditer(masked):
        raw = match.group("n")
        values, versions = _readings(raw)
        if not values and not versions:
            # e.g. "2024 100": not a valid grouping, treat the parts independently
            base = match.start("n")
            for part in re.finditer(r"\d+", raw):
                key = ("num", _canon(Decimal(part.group())))
                add(base + part.start(), base + part.end(), "number", {key}, {key, ("ver", part.group())})
            continue
        if match.group("v"):
            keys = {("ver", raw)}
            add(*match.span(), "version", keys, keys)
        elif match.group("pct"):
            keys = {("pct", v) for v in values}
            add(*match.span(), "percentage", keys, keys)
        elif match.group("mult"):
            keys = {("mult", v) for v in values}
            add(*match.span(), "multiplier", keys, keys)
        elif match.group("scale"):
            word = match.group("scale").strip().casefold()
            keys = {("num", _canon(Decimal(v) * _SCALE[word])) for v in values}
            add(*match.span(), "number", keys, keys)
        elif match.group("ord"):
            keys = {("ord", v) for v in values}
            add(*match.span(), "ordinal", keys, keys)
        else:
            keys = {("num", v) for v in values} | {("ver", v) for v in versions}
            kind = "version" if versions and len(raw.split(".")) > 2 else "number"
            add(*match.span(), kind, keys, keys | {("ver", raw)})
    tokens.sort(key=lambda t: t["start"])
    return tokens


def _word_forms(word: str) -> set[str]:
    """Casefolded word plus its simple singular readings (``-s``, ``-es``, ``-ies``, possessive).

    Both sides of a comparison are expanded, so ``caches``/``cache`` and ``boxes``/``box``
    meet on a shared form. Deliberately crude: no irregular plurals, no stemming of verbs.
    """
    w = unicodedata.normalize("NFKC", word).casefold().replace("’", "'")
    if w.endswith("'s"):
        w = w[:-2]
    forms = {w}
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        forms.add(w[:-1])
        if w.endswith("es"):
            forms.add(w[:-2])
        if len(w) > 4 and w.endswith("ies"):
            forms.add(w[:-3] + "y")
    return forms


def _text_forms(text: str) -> set[str]:
    forms: set[str] = set()
    for match in _TERM.finditer(_normalize(text)[0]):
        forms |= _word_forms(match.group())
    return forms


def _feature_terms(window: str) -> list[tuple[str, int, int]]:
    """Content terms of a claimed feature: no stopwords, no bare numbers or versions (check 1 covers those)."""
    terms = []
    for match in _TERM.finditer(window):
        word = match.group()
        folded = word.casefold().replace("’", "'")
        base = folded[:-2] if folded.endswith("'s") else folded
        if len(base) < 2 or base in FEATURE_STOPWORDS or re.fullmatch(r"v?\d[\d.]*", base):
            continue
        terms.append((word, match.start(), match.end()))
    return terms


def find_feature_claims(text: str) -> list[dict]:
    """Return feature/capability claims (``adds X``, ``now supports X``, ``integrates with X``...).

    Each item has the trigger, the claimed ``terms`` and original-text ``span``/``start``/``end``
    from the trigger to the last content term. Negated ("does not support"), interrogative
    ("does it support X?") and term-less ("works with you") matches are skipped.
    """
    norm, offsets = _normalize(text)
    masked = _mask(norm, [m.span() for m in _URL.finditer(norm)])
    claims: list[dict] = []
    for match in _FEATURE.finditer(masked):
        before = masked[max(0, match.start() - 40):match.start()]
        if _NEGATION.search(before):
            continue
        rest = masked[match.end():]
        end = _FEATURE_END.search(rest)
        if end and end.group() == "?":
            continue  # a question is not an assertion
        cut = end.start() if end else len(rest)
        following = _FEATURE.search(rest, 0, cut)  # "supports X and adds Y" is two claims
        clause = rest[:following.start() if following else cut]
        words = list(re.finditer(r"\S+", clause))[:FEATURE_WINDOW_WORDS]
        window = clause[:words[-1].end()] if words else ""
        terms = _feature_terms(window)
        if not terms:
            continue

        def orig(a: int, b: int) -> tuple[int, int]:
            return offsets[a], offsets[b - 1] + 1

        base = match.end()
        start, stop = orig(match.start(), base + terms[-1][2])
        claims.append({"trigger": text[slice(*orig(*match.span()))],
                       "terms": [text[slice(*orig(base + t[1], base + t[2]))] for t in terms],
                       "span": text[start:stop], "start": start, "end": stop})
    return claims


def _evidence_index(evidence: list) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in index:
            continue
        parts = [item.get(field) for field in ("claim", "excerpt")]
        text = "\n".join(p for p in parts if isinstance(p, str))
        provided: set = set()
        for token in extract_tokens(text):
            provided |= token["provides"]
        index[item["id"]] = {"text": text, "folded": _fold(text), "provides": provided,
                             "forms": _text_forms(text), "kind": item.get("kind"), "url": item.get("url")}
    return index


def _is_release_evidence(entry: dict) -> bool:
    if entry["kind"] == "release":
        return True
    try:
        path = urlparse(entry["url"]).path if isinstance(entry["url"], str) else ""
    except ValueError:
        return False
    return bool(re.search(r"/(?:-/)?releases(?:/|$)|/releases/tag/", path))


def _phrase_regex(phrase: str) -> re.Pattern | None:
    folded = " ".join(_normalize(phrase)[0].split())
    if not folded:
        return None
    body = r"\s+".join(re.escape(part) for part in folded.split(" "))
    left = r"(?<!\w)" if re.match(r"\w", folded) else ""
    right = r"(?!\w)" if re.search(r"\w$", folded) else ""
    return re.compile(left + body + right, re.I)


# Scripts written without spaces between words, where U+200B ZERO WIDTH SPACE marks the word
# break: Thai, Lao, Myanmar (+ Extended-A/B) and Khmer (+ Khmer Symbols); The Unicode Standard
# 16.0, chapter 16 "South and Southeast Asia" (https://www.unicode.org/versions/Unicode16.0.0/,
# accessed 2026-10-09).
_ZWSP_SEPARATED = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1780, 0x17FF), (0x19E0, 0x19FF),
                   (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F))


def _zwsp_separated(ch: str) -> bool:
    return any(low <= ord(ch) <= high for low, high in _ZWSP_SEPARATED)


def _drop_format_chars(text: str) -> str:
    """Remove format (Cf) characters so an invisible character cannot split a word in two.

    U+200B next to a Thai, Lao, Khmer or Myanmar letter is the script's word break, so it becomes
    a space instead; elsewhere (``re\u200bceipts``) it is dropped like U+200C/U+200D, U+2060,
    U+FEFF, U+00AD and the bidi controls. Dropping ZWNJ merges the parts of a Persian word, which
    is harmless for similarity. Linear time: neighbours are found in one pass each way.
    """
    visible = [unicodedata.category(ch) != "Cf" for ch in text]
    after = [False] * len(text)  # is the next visible character in a ZWSP-separated script?
    following = False
    for index in range(len(text) - 1, -1, -1):
        after[index] = following
        if visible[index]:
            following = _zwsp_separated(text[index])
    out: list[str] = []
    preceding = False
    for index, ch in enumerate(text):
        if visible[index]:
            out.append(ch)
            preceding = _zwsp_separated(ch)
        elif ch == "\u200b" and (preceding or after[index]):
            out.append(" ")
    return "".join(out)


def _shingles(text: str) -> set:
    # NFC first, so a decomposed accent (e + U+0301) meets its precomposed form under the
    # char-wise NFKC of _normalize; then format characters are dropped (see _drop_format_chars).
    text = _drop_format_chars(unicodedata.normalize("NFC", text))
    words = _WORD.findall(_URL.sub(" ", _normalize(text)[0]).casefold())
    if len(words) < 3:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard(left: str, right: str) -> float:
    """Word 3-gram Jaccard similarity in [0, 1]; URLs are ignored, case and Unicode forms folded."""
    a, b = _shingles(left), _shingles(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def variant_body(variant: dict) -> str:
    """The full text a variant asserts: ``text``, or thread ``parts`` / carousel caption + ``slides``.

    Thread parts and carousel pieces are joined with PART_SEPARATOR, so finding offsets
    index into that joined body. Non-string pieces are skipped (validation reports them).
    """
    if not isinstance(variant, dict):
        return ""
    fmt = variant.get("format", "text")
    text = cast(str, variant.get("text")) if isinstance(variant.get("text"), str) else ""
    if fmt == "thread" and isinstance(variant.get("parts"), list):
        return PART_SEPARATOR.join(p for p in variant["parts"] if isinstance(p, str))
    if fmt == "carousel_outline" and isinstance(variant.get("slides"), list):
        pieces = ([text] if text else []) + [s for s in variant["slides"] if isinstance(s, str)]
        return PART_SEPARATOR.join(pieces)
    return text


def effective_brand_rules(brand: dict | None, account_id: object = None) -> dict:
    """Global brand rules merged with the optional ``brand.accounts[account_id]`` override.

    The merge is additive and can never weaken a global rule: banned phrases are the
    union, every distinct required disclosure must appear, and the hashtag cap is the
    lowest one set. Without an ``accounts`` entry the global rules apply unchanged.
    """
    rules: dict[str, Any] = {"account_id": account_id if isinstance(account_id, str) else None, "account_override": False,
             "banned_phrases": [], "required_disclosures": [], "max_hashtags": None, "max_hashtags_scope": None}
    if not isinstance(brand, dict):
        return rules
    scopes = [("brand", brand)]
    overrides = brand.get("accounts")
    if isinstance(account_id, str) and isinstance(overrides, dict) and isinstance(overrides.get(account_id), dict):
        scopes.append((f"account:{account_id}", overrides[account_id]))
        rules["account_override"] = True
    seen_phrases: set[str] = set()
    for scope, source in scopes:
        phrases = source.get("banned_phrases")
        for phrase in phrases if isinstance(phrases, list) else []:
            if isinstance(phrase, str) and phrase.strip() and _fold(phrase) not in seen_phrases:
                seen_phrases.add(_fold(phrase))
                rules["banned_phrases"].append({"phrase": phrase, "scope": scope})
        disclosure = source.get("required_disclosure")
        if isinstance(disclosure, str) and disclosure.strip() and not any(
                _fold(d["text"]) == _fold(disclosure) for d in rules["required_disclosures"]):
            rules["required_disclosures"].append({"text": disclosure.strip(), "scope": scope})
        cap = source.get("max_hashtags")
        if type(cap) is int and cap >= 0 and (rules["max_hashtags"] is None or cap < rules["max_hashtags"]):
            rules["max_hashtags"], rules["max_hashtags_scope"] = cap, scope
    return rules


def _finding(code: str, severity: str, message: str, *, span=None, start=None, end=None,
             matched=(), **extra) -> dict:
    item = {"code": code, "severity": severity, "message": message, "span": span,
            "start": start, "end": end, "matched_evidence": sorted(matched)}
    item.update(extra)
    return item


def _variant_report(index: int, variant: dict, evidence: dict[str, dict], brand: dict | None,
                    campaign: dict, optout: list | None = None) -> dict:
    text = variant_body(variant)
    cited_ids = [x for x in variant.get("evidence_ids", []) if isinstance(x, str)] \
        if isinstance(variant.get("evidence_ids"), list) else []
    cited = {eid: evidence[eid] for eid in cited_ids if eid in evidence}
    uncited = {eid: entry for eid, entry in evidence.items() if eid not in cited}
    findings: list[dict] = []
    grounded: list[dict] = []
    norm, offsets = _normalize(text)
    masked = _mask(norm, [m.span() for m in _URL.finditer(norm)])

    def orig(start: int, end: int) -> tuple[str, int, int]:
        s, e = offsets[start], offsets[end - 1] + 1
        return text[s:e], s, e

    # (1) every number-like token must be anchored in cited evidence
    for token in extract_tokens(text):
        matched = {eid for eid, entry in cited.items() if token["keys"] & entry["provides"]}
        if matched:
            grounded.append({"type": token["kind"], "span": token["span"], "start": token["start"],
                             "end": token["end"], "matched_evidence": sorted(matched)})
            continue
        elsewhere = sorted(eid for eid, entry in uncited.items() if token["keys"] & entry["provides"])
        hint = (f"; appears only in uncited evidence {', '.join(elsewhere)}: add it to evidence_ids"
                if elsewhere else "; add a source excerpt containing it or remove the figure")
        findings.append(_finding("unsupported_number", "error",
                                 f"{token['kind']} {token['span']!r} is not present in cited evidence{hint}",
                                 span=token["span"], start=token["start"], end=token["end"],
                                 token_kind=token["kind"]))

    # (2) risky claim language needs explicit lexical support or human review
    def supported(phrase: str) -> set:
        folded = _fold(phrase)
        return {eid for eid, entry in cited.items() if folded and folded in entry["folded"]}

    for category, pattern in _RISKY:
        for match in pattern.finditer(masked):
            span, s, e = orig(*match.span())
            matched = supported(span)
            if matched:
                grounded.append({"type": category, "span": span, "start": s, "end": e,
                                 "matched_evidence": sorted(matched)})
            else:
                findings.append(_finding("needs_human_review", "review",
                                         f"{category} language {span!r} is not explicitly supported by cited evidence",
                                         span=span, start=s, end=e, category=category))
    for match in _QUOTES.finditer(masked):
        inner = next(g for g in match.groups() if g is not None)
        if len(_WORD.findall(inner)) < 3:
            continue  # scare quotes around a term are not testimony
        span, s, e = orig(*match.span())
        matched = supported(inner)
        if matched:
            grounded.append({"type": "quote", "span": span, "start": s, "end": e,
                             "matched_evidence": sorted(matched)})
        else:
            findings.append(_finding("needs_human_review", "review",
                                     "quotation is not verbatim in cited evidence; possible invented testimonial",
                                     span=span, start=s, end=e, category="quote"))
    for match in _CUSTOMER.finditer(masked):
        span, s, e = orig(*match.span())
        noun = _fold(match.group("noun"))
        stem = re.sub(r"(?:ies|s)$", "", noun)
        qty_tokens = extract_tokens(match.group("qty"))
        qty_keys = set().union(*(t["keys"] for t in qty_tokens)) if qty_tokens else set()
        matched = {eid for eid, entry in cited.items() if stem and stem in entry["folded"]
                   and ((qty_keys & entry["provides"]) if qty_keys else _fold(match.group("qty")) in entry["folded"])}
        if matched:
            grounded.append({"type": "customer_count", "span": span, "start": s, "end": e,
                             "matched_evidence": sorted(matched)})
        else:
            findings.append(_finding("needs_human_review", "review",
                                     f"customer/usage count {span!r} lacks cited evidence naming that quantity",
                                     span=span, start=s, end=e, category="customer_count"))

    # (2b) feature claims: every content term of "adds/supports/integrates with X" must occur in cited evidence
    for claim in find_feature_claims(text):
        hits = {term: {eid for eid, entry in cited.items() if _word_forms(term) & entry["forms"]}
                for term in claim["terms"]}
        missing = [term for term, ids in hits.items() if not ids]
        matched = set().union(*hits.values())
        if not missing:
            grounded.append({"type": "feature", "span": claim["span"], "start": claim["start"],
                             "end": claim["end"], "matched_evidence": sorted(matched)})
            continue
        elsewhere = sorted(eid for eid, entry in uncited.items()
                           if all(_word_forms(term) & entry["forms"] for term in missing))
        hint = (f"; found in uncited evidence {', '.join(elsewhere)}: add it to evidence_ids" if elsewhere
                else "; confirm the source describes this feature, quote it in an excerpt, or remove the claim")
        findings.append(_finding("needs_human_review", "review",
                                 f"feature claim {claim['span']!r}: {', '.join(repr(t) for t in missing)} "
                                 f"not found in cited evidence (possible nonexistent feature){hint}",
                                 span=claim["span"], start=claim["start"], end=claim["end"],
                                 matched=matched, category="feature", missing_terms=missing))

    # (3) released/launched claims need a cited release evidence item
    for match in _RELEASE.finditer(masked):
        span, s, e = orig(*match.span())
        releases = {eid for eid, entry in cited.items() if _is_release_evidence(entry)}
        if releases:
            grounded.append({"type": "release", "span": span, "start": s, "end": e,
                             "matched_evidence": sorted(releases)})
            continue
        elsewhere = sorted(eid for eid, entry in uncited.items() if _is_release_evidence(entry))
        hint = f"; cite {', '.join(elsewhere)}" if elsewhere else \
            "; add evidence with kind \"release\" or a releases URL"
        findings.append(_finding("unsupported_release", "error",
                                 f"release language {span!r} without cited release evidence{hint}",
                                 span=span, start=s, end=e))

    # (5) brand voice: banned phrases, required disclosure, hashtag cap (global + per-account), declared CTA
    rules = effective_brand_rules(brand, variant.get("account_id"))

    def scoped(scope: str) -> dict:
        return {} if scope == "brand" else {"scope": scope}

    def where(scope: str) -> str:
        return "" if scope == "brand" else f" for account {scope.split(':', 1)[1]}"

    for rule in rules["banned_phrases"]:
        phrase_pattern = _phrase_regex(rule["phrase"])
        for match in phrase_pattern.finditer(norm) if phrase_pattern else ():
            span, s, e = orig(*match.span())
            findings.append(_finding("banned_phrase", "error",
                                     f"brand bans the phrase {rule['phrase']!r}{where(rule['scope'])}",
                                     span=span, start=s, end=e, **scoped(rule["scope"])))
    for rule in rules["required_disclosures"]:
        if _fold(rule["text"]) not in _fold(text):
            findings.append(_finding("missing_disclosure", "error",
                                     f"required brand disclosure {rule['text']!r}{where(rule['scope'])} is missing",
                                     **scoped(rule["scope"])))
    if rules["max_hashtags"] is not None:
        tags = hashtags(text)
        if len(tags) > rules["max_hashtags"]:
            findings.append(_finding("too_many_hashtags", "error",
                                     f"{len(tags)} hashtags exceed the brand limit of {rules['max_hashtags']}"
                                     f"{where(rules['max_hashtags_scope'])}; avoid hashtag stuffing",
                                     hashtags=tags, **scoped(rules["max_hashtags_scope"])))
    cta_url = variant.get("cta_url") or campaign.get("cta_url")
    if isinstance(cta_url, str) and cta_url.strip():
        want = urlparse(cta_url.strip())
        present = False
        for match in _URL.finditer(norm):
            try:
                got = urlparse(match.group().rstrip(".,;:!?"))
            except ValueError:
                continue
            if (got.hostname or "").casefold() == (want.hostname or "").casefold() and \
                    got.path.rstrip("/") == want.path.rstrip("/"):
                present = True
        if not present:
            findings.append(_finding("missing_cta", "warning",
                                     f"declared CTA link {cta_url!r} is not in the text"))
    cta_phrase = variant.get("cta_phrase")
    if isinstance(cta_phrase, str) and cta_phrase.strip() and _fold(cta_phrase) not in _fold(text):
        findings.append(_finding("missing_cta", "warning", f"declared CTA phrase {cta_phrase.strip()!r} is not in the text"))

    # (6) never mention or tag a person/domain on the local opt-out list (optout.py)
    if optout:
        from .optout import optout_mentions
        platform = variant.get("platform") if isinstance(variant.get("platform"), str) else None
        for hit in optout_mentions(text, optout, platform):
            findings.append(_finding("optout_mention", "error",
                                     f"{hit['span']!r} is on the local opt-out list ({hit['entry_kind']}); "
                                     "remove the mention/tag",
                                     span=hit["span"], start=hit["start"], end=hit["end"]))

    return {"index": index, "variant_id": variant.get("id"), "platform": variant.get("platform"),
            "cited_evidence": sorted(cited), "findings": findings, "grounded": grounded}


def check_claims(campaign: dict, brand: dict | None = None, *,
                 duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
                 optout: list | None = None, history: dict | None = None) -> dict:
    """Return a structured lexical grounding report; never raises on malformed variants.

    ``optout``: validated entries from ``optout.load_optout``; mentions are errors.
    ``history``: optional ``history.load_history`` result; adds the read-only posting-history
    warnings (``history_near_duplicate``, ``history_frequency``) and a ``history`` summary.
    """
    if isinstance(duplicate_threshold, bool) or not isinstance(duplicate_threshold, (int, float)) \
            or not 0 < duplicate_threshold <= 1:
        raise ValueError("duplicate_threshold must be in (0, 1]")
    campaign = campaign if isinstance(campaign, dict) else {}
    evidence = _evidence_index(campaign.get("evidence", []))
    raw_variants = cast(list, campaign.get("variants")) if isinstance(campaign.get("variants"), list) else []
    variants = [(i, v) for i, v in enumerate(raw_variants[:30]) if isinstance(v, dict)]
    reports = [_variant_report(i, v, evidence, brand, campaign, optout) for i, v in variants]
    # (4) near-duplicate drafts within one campaign: word 3-gram Jaccard
    for a in range(len(variants)):
        for b in range(a + 1, len(variants)):
            left, right = variant_body(variants[a][1]), variant_body(variants[b][1])
            if not left or not right:
                continue
            score = jaccard(left, right)
            if score >= duplicate_threshold:
                reports[b]["findings"].append(_finding(
                    "near_duplicate", "warning",
                    f"word 3-gram Jaccard {score:.2f} with variant {variants[a][1].get('id')!r} "
                    f"(threshold {duplicate_threshold:.2f}); rewrite the angle instead of copy-pasting",
                    other_variant_id=variants[a][1].get("id"), similarity=round(score, 3)))
    # (4b) repetition against the account's local posting history (history.py; warnings only)
    if history is not None:
        from .history import history_findings
        position = {index: n for n, (index, _) in enumerate(variants)}
        for index, found in history_findings(variants, history).items():
            reports[position[index]]["findings"].extend(found)
    for report in reports:
        report["findings"].sort(key=lambda f: (SEVERITIES.index(f["severity"]),
                                               f["start"] if f["start"] is not None else -1))
    summary = {severity: sum(1 for r in reports for f in r["findings"] if f["severity"] == severity)
               for severity in SEVERITIES}
    summary["grounded"] = sum(len(r["grounded"]) for r in reports)
    result = {"report": "claim_grounding", "method": "lexical", "campaign_id": campaign.get("campaign_id"),
              "valid": summary["error"] == 0, "duplicate_threshold": duplicate_threshold,
              "summary": summary, "variants": reports, "limitations": LIMITATION}
    if history is not None:
        from .history import history_summary
        result["history"] = history_summary(history)
    return result
