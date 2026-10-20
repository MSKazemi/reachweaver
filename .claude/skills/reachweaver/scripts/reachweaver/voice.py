"""Voice profile from the owner's own posts, and an advisory voice and style check of a draft.

``voice-profile`` reads posts the owner supplies (a local text, JSON or CSV file) or the owner's published posts in
the local database (opened read-only) and writes a NEW private (0600) JSON file of plain statistics: length
distributions per platform, sentence length, emoji, hashtag, mention and link counts, first-person usage, question
and exclamation marks, capitalisation, line breaks and lists, punctuation habits, common opening words and short
lists of recurring words and phrases. It stores no post text, ID or link: only aggregates and short word lists.

``voice-check`` compares one draft with that profile and with ``voice_tells.json``, a heuristic list of stock phrases
and patterns that readers often associate with machine-drafted posts. It is NOT an AI detector and claims no
accuracy. Every finding is ``info`` or ``warn``; the command never blocks approval or publishing and always exits 0
after a successful check. No network request, model call, account, credential or write to the database.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .policy import ValidationError
from .privacy import write_private_file
from .storage import connect_readonly, database_path, journal_posts, utc_now

PROFILE_SCHEMA = "reachweaver.voice-profile.v1"
CHECK_SCHEMA = "reachweaver.voice-check.v1"
TELLS_FILE = Path(__file__).with_name("voice_tells.json")
TELLS_SCHEMA = "reachweaver.voice-tells.v1"
MIN_POSTS = 10  # below this a "usual range" says little; report insufficient_sample instead
PLATFORM_MIN_POSTS = 5  # per-platform length statistics need at least this many posts
MAX_INPUT_BYTES = 4_000_000
MAX_PROFILE_BYTES = 512_000
MAX_DRAFT_CHARS = 20_000
MAX_POST_CHARS = 20_000
MAX_POSTS = 5000
MIN_DOC_FREQ = 3  # a word or phrase is listed only when at least this many posts use it
TOP_WORDS, TOP_PHRASES, TOP_OPENINGS, TOP_HASHTAGS = 15, 10, 10, 10
OWN_HABIT_SHARE = 0.2  # a tell found in at least this share of the owner's posts is reported as info only
NOTE = ("Advisory style comparison only. The usual range is the middle 80% (10th to 90th percentile) of your own "
        "posts; a draft outside it may still be right. The tell list is a heuristic style list, not an AI detector, "
        "and claims no accuracy. Nothing was blocked, sent, fetched or written to the database, and no model was "
        "called.")
PROFILE_NOTE = ("Statistics of your own posts only: no post text, ID or link is stored, only counts, percentiles and "
                "short lists of words and phrases that at least 3 posts use. It describes how you have written, not "
                "how you should write, and predicts nothing about reach.")
HUMAN_ONLY_NOTE = ("Human-only destination: these findings are for your own editing. Claude must not rewrite or draft "
                   "this text for you.")

# Numeric per-post features compared with the owner's usual range: (key, label)
FEATURES: tuple[tuple[str, str], ...] = (
    ("length_chars", "Length (characters)"),
    ("words", "Words"),
    ("sentence_words", "Average words per sentence"),
    ("emoji", "Emoji"),
    ("hashtags", "Hashtags"),
    ("mentions", "@-mentions"),
    ("links", "Links"),
    ("questions", "Question marks"),
    ("exclamations", "Exclamation marks"),
    ("line_breaks", "Line breaks"),
    ("list_lines", "List lines"),
    ("em_dashes", "Em dashes"),
    ("semicolons", "Semicolons"),
    ("ellipses", "Ellipses"),
    ("first_person_singular", "I/me/my per 100 words"),
    ("first_person_plural", "We/us/our per 100 words"),
    ("all_caps_words", "ALL-CAPS words per 100 words"),
)
FEATURE_KEYS = tuple(key for key, _ in FEATURES)
PLATFORM_FEATURES = ("length_chars", "words", "hashtags", "emoji")
# Yes/no habits: (key, message when the draft does it but the owner rarely does, message for the reverse)
HABITS: tuple[tuple[str, str, str], ...] = (
    ("starts_lowercase", "The draft starts with a lowercase letter; only {share} of your posts do",
     "The draft starts with a capital letter; {share} of your posts start in lowercase"),
    ("question_opener", "The draft opens with a question; only {share} of your posts do",
     "The draft does not open with a question; {share} of your posts do"),
    ("hashtags_at_end", "The draft puts its hashtags at the end; only {share} of your posts with hashtags do",
     "The draft puts hashtags inside the text; {share} of your posts with hashtags keep them at the end"),
    ("link_at_end", "The draft ends with its link; only {share} of your posts with links do",
     "The draft's link is not at the end; {share} of your posts with links end with it"),
)

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_MENTION = re.compile(r"(?<![\w@])@[A-Za-z0-9_][\w.-]*(?:@[\w.-]+)?", re.UNICODE)
_HASHTAG = re.compile(r"(?<![\w#])#(\w+)", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+(?:[.,]\d+)*", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?…。！？]+(?=\s|$)|\n+")
_LIST_LINE = re.compile(r"^\s*(?:[-*•▪▸‣]|\d{1,2}[.)])\s+")
_TRIPLET = re.compile(r"\b[\w'’-]+(?: [\w'’-]+)?, [\w'’-]+(?: [\w'’-]+)?,? (?:and|or) [\w'’-]+",
                      re.UNICODE | re.I)
_ROCKETS = frozenset("\U0001F680✨\U0001F525\U0001F4A5⚡")  # rocket, sparkles, fire, collision, high voltage
_SINGULAR = frozenset({"i", "me", "my", "mine", "myself", "i'm", "i've", "i'd", "i'll"})
_PLURAL = frozenset({"we", "us", "our", "ours", "ourselves", "we're", "we've", "we'd", "we'll"})
# A small list of very common English words, written for ReachWeaver. A recurring word NOT in this list counts as
# distinctive of the owner. It is deliberately short: non-English posts will list ordinary words as distinctive.
BASELINE = frozenset("""
a about above after again against all also am an and any are aren't as at be because been before being below between
both but by can can't could couldn't did didn't do does doesn't doing don't down during each even every few for from
get gets getting got had hasn't has have haven't having he her here hers him his how however i if in into is isn't it
it's its just know last let like made make makes many may me more most much must my new no nor not now of off on once
one only or other our out over own people really same see she should so some still such than that that's the their
them then there there's these they this those through time to too two under until up us use used using very want was
way we well were what when where which while who why will with without would you your yours yet year years day days
today week good great first next back thing things lot go going here's want new work working via one's
""".split())


def _fail(message: str) -> ValidationError:
    return ValidationError("voice: " + message)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _is_emoji(char: str) -> bool:
    code = ord(char)
    if 0x1F3FB <= code <= 0x1F3FF:  # skin-tone modifiers belong to the emoji before them
        return False
    return 0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or 0x2B00 <= code <= 0x2BFF


def _bare(text: str) -> str:
    """Text without links, mentions and hashtags, for word-based features."""
    return _HASHTAG.sub(" ", _MENTION.sub(" ", _URL.sub(" ", text)))


def _words(text: str) -> list[str]:
    return [w.replace("’", "'") for w in _WORD.findall(_bare(text))]


def features(text: str) -> dict:
    """Plain, explainable statistics of one post (numbers and yes/no habits; no text is kept)."""
    text = _fold(text).strip()
    words = _words(text)
    lower = [w.casefold() for w in words]
    n_words = len(words)
    per100 = (lambda count: round(100.0 * count / n_words, 2)) if n_words else (lambda count: 0.0)
    sentences = [s for s in _SENTENCE_END.split(_bare(text)) if _WORD.search(s)]
    lines = [line for line in text.splitlines() if line.strip()]
    hashtags = _HASHTAG.findall(text)
    links = _URL.findall(text)
    first_letter = next((c for c in text if c.isalpha()), "")
    first_line = lines[0] if lines else ""
    first_sentence = re.split(r"(?<=[.!?])\s|\n", first_line, maxsplit=1)[0]
    value: dict = {
        "length_chars": len(text),
        "words": n_words,
        "sentence_words": round(n_words / len(sentences), 2) if sentences else 0.0,
        "emoji": sum(1 for c in text if _is_emoji(c)),
        "hashtags": len(hashtags),
        "mentions": len(_MENTION.findall(text)),
        "links": len(links),
        "questions": text.count("?") + text.count("？"),
        "exclamations": text.count("!") + text.count("！"),
        "line_breaks": max(len(lines) - 1, 0),
        "list_lines": sum(1 for line in lines if _LIST_LINE.match(line)),
        "em_dashes": text.count("—") + len(re.findall(r"(?<=\w) -- (?=\w)", text)),
        "semicolons": text.count(";"),
        "ellipses": text.count("…") + len(re.findall(r"\.{3,}", text)),
        "first_person_singular": per100(sum(1 for w in lower if w in _SINGULAR)),
        "first_person_plural": per100(sum(1 for w in lower if w in _PLURAL)),
        "all_caps_words": per100(sum(1 for w in words if len(w) >= 2 and w.isalpha() and w.isupper())),
        # structural tell inputs
        "triplets": len(_TRIPLET.findall(_bare(text))),
        "rocket_emoji": sum(1 for c in text if c in _ROCKETS),
        "emoji_bullet_lines": sum(1 for line in lines if _is_emoji(line.lstrip()[:1] or " ")),
        "question_opener": int(first_sentence.rstrip().endswith(("?", "？"))),
        # habits
        "starts_lowercase": bool(first_letter) and first_letter.islower(),
    }
    value["question_opener_habit"] = bool(value["question_opener"])
    if hashtags:
        first = _HASHTAG.search(text)
        tail = text[first.start():] if first else ""
        value["hashtags_at_end"] = not _WORD.search(_HASHTAG.sub(" ", _MENTION.sub(" ", _URL.sub(" ", tail))))
    if links:
        value["link_at_end"] = text.rstrip().endswith(links[-1].rstrip())
    return value


# ---- statistics -------------------------------------------------------------------------------------------------

def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (pos - low), 2)


def _stats(values: list[float]) -> dict:
    ordered = sorted(float(v) for v in values)
    return {"median": _quantile(ordered, 0.5), "p10": _quantile(ordered, 0.1), "p25": _quantile(ordered, 0.25),
            "p75": _quantile(ordered, 0.75), "p90": _quantile(ordered, 0.9)}


def _share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 3) if flags else None


# ---- AI-tell list -----------------------------------------------------------------------------------------------

def _phrase_regex(phrase: str) -> re.Pattern:
    body = ""
    for char in phrase.casefold():
        if char == " ":
            body += r"\s+"
        elif char in "'’":
            body += "['’]"
        elif char == "-":
            body += r"[-‐‑\s]"
        else:
            body += re.escape(char)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.I | re.UNICODE)


def load_tells(path: Path | None = None) -> dict:
    """Read and validate the heuristic tell list; every regex is compiled once here."""
    data = json.loads((path or TELLS_FILE).read_text(encoding="utf-8"))
    if data.get("schema_version") != TELLS_SCHEMA or not isinstance(data.get("tells"), list):
        raise _fail(f"tell list must have schema_version {TELLS_SCHEMA!r} and a tells list")
    seen: set[str] = set()
    compiled = []
    structural_inputs = {"em_dashes", "triplets", "rocket_emoji", "emoji_bullet_lines", "question_opener",
                         "hashtags", "exclamations"}
    for entry in data["tells"]:
        tid, kind = entry.get("id"), entry.get("kind")
        if not isinstance(tid, str) or tid in seen or entry.get("severity") not in ("info", "warn") \
                or not isinstance(entry.get("reason"), str) or not entry["reason"]:
            raise _fail(f"tell {tid!r} needs a unique id, severity info|warn and a reason")
        seen.add(tid)
        item = {"id": tid, "kind": kind, "severity": entry["severity"], "reason": entry["reason"]}
        if kind == "phrase":
            if not isinstance(entry.get("phrases"), list) or not all(isinstance(p, str) and p.strip()
                                                                     for p in entry["phrases"]):
                raise _fail(f"tell {tid!r} needs a list of phrases")
            item["patterns"] = [(p, _phrase_regex(p)) for p in entry["phrases"]]
        elif kind == "regex":
            item["patterns"] = [(tid, re.compile(entry["pattern"], re.I | re.UNICODE))]
        elif kind == "structural" and entry.get("feature") in structural_inputs \
                and isinstance(entry.get("threshold"), int) and entry["threshold"] >= 1:
            item["feature"], item["threshold"] = entry["feature"], entry["threshold"]
        else:
            raise _fail(f"tell {tid!r} has an unknown kind or feature")
        compiled.append(item)
    return {"label": data["label"], "updated": data.get("updated"), "tells": compiled}


def find_tells(text: str, tells: dict, feats: dict | None = None) -> list[dict]:
    """Tells present in ``text``: phrase/regex hits with the matched words, structural counts at the threshold."""
    text = _fold(text)
    feats = feats if feats is not None else features(text)
    hits = []
    for tell in tells["tells"]:
        if tell["kind"] == "structural":
            count = int(feats[tell["feature"]])
            if count >= tell["threshold"]:
                hits.append({"id": tell["id"], "count": count, "threshold": tell["threshold"]})
            continue
        matched: list[str] = []
        for _, pattern in tell["patterns"]:
            matched.extend(m.group(0) for m in pattern.finditer(text))
        if matched:
            hits.append({"id": tell["id"], "count": len(matched),
                         "matched": sorted({" ".join(m.split())[:80] for m in matched})[:5]})
    return hits


# ---- reading posts ----------------------------------------------------------------------------------------------

def _read_bounded(path: Path, limit: int, label: str) -> str:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise _fail(f"{label} must be an existing regular file, not a symlink: {path}")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise _fail(f"{label} is larger than {limit // 1000} KB")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _fail(f"{label} is not UTF-8 text") from None


def _post_item(raw: object) -> tuple[str, str | None]:
    if isinstance(raw, str):
        return raw, None
    if isinstance(raw, dict):
        text = raw.get("text", raw.get("content"))
        platform = raw.get("platform")
        if isinstance(text, str) and (platform is None or isinstance(platform, str)):
            return text, platform
    raise _fail("each JSON post must be a string or an object with a 'text' (or 'content') string and an optional "
                "'platform' string")


def load_posts_file(path: Path) -> list[tuple[str, str | None]]:
    """Owner-supplied posts: ``.json`` (list, or {"posts": list}), ``.csv`` (a text/content column, optional
    platform column) or any other file as UTF-8 text with posts separated by a line holding only ``---``."""
    body = _read_bounded(path, MAX_INPUT_BYTES, "--from file")
    suffix = Path(path).suffix.casefold()
    if suffix == ".json":
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise _fail(f"invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from None
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            data = data["posts"]
        if not isinstance(data, list):
            raise _fail('a JSON file must hold a list of posts or {"posts": [...]}')
        posts = [_post_item(item) for item in data]
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(body))
        columns = {name.strip().casefold(): name for name in (reader.fieldnames or [])}
        text_col = next((columns[c] for c in ("text", "content", "post", "body") if c in columns), None)
        if text_col is None:
            raise _fail("the CSV needs a header with a 'text' (or 'content', 'post', 'body') column")
        platform_col = columns.get("platform")
        posts = [(row.get(text_col) or "", (row.get(platform_col) or None) if platform_col else None)
                 for row in reader]
    else:
        posts = [(chunk, None) for chunk in re.split(r"(?m)^[ \t]*---[ \t]*$", body)]
    if len(posts) > MAX_POSTS:
        raise _fail(f"at most {MAX_POSTS} posts per profile")
    return posts


def load_posts_db(db: str | Path | None, *, account: str | None, platform: str | None) -> list[tuple[str, str | None]]:
    """The owner's published posts from the local database, opened strictly read-only (never created)."""
    with connect_readonly(database_path(db)) as con:
        rows = journal_posts(con, statuses=("published",), platform=platform, account=account, limit=MAX_POSTS)
    return [(row["content"], row["platform"]) for row in rows]


def load_post_text(db: str | Path | None, post_id: str) -> tuple[str, str]:
    """One stored post's text and platform, read-only (drafts included: that is what a voice check is for)."""
    with connect_readonly(database_path(db)) as con:
        row = con.execute("SELECT platform, content FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        raise _fail(f"unknown post {post_id!r}")
    return row["content"], row["platform"]


# ---- building the profile ---------------------------------------------------------------------------------------

def _top(counter: Counter, limit: int, minimum: int) -> list[dict]:
    ranked = sorted(((term, n) for term, n in counter.items() if n >= minimum), key=lambda x: (-x[1], x[0]))
    return [{"text": term, "posts": n} for term, n in ranked[:limit]]


def build_profile(posts: list[tuple[str, str | None]], *, platform: str | None = None, source: str = "file",
                  tells: dict | None = None) -> dict:
    """Aggregate statistics of the owner's posts, or ``insufficient_sample`` below MIN_POSTS usable posts."""
    platform = platform.strip().casefold() if platform else None
    usable: list[tuple[str, str | None]] = []
    skipped = {"empty": 0, "too_long": 0, "other_platform": 0}
    for text, label in posts:
        text = _fold(text).strip()
        label = label.strip().casefold() if isinstance(label, str) and label.strip() else None
        if not text:
            skipped["empty"] += 1
        elif len(text) > MAX_POST_CHARS:
            skipped["too_long"] += 1
        elif platform and label not in (None, platform):
            skipped["other_platform"] += 1
        else:
            usable.append((text, platform or label))
    base = {"schema_version": PROFILE_SCHEMA, "source": source, "platform_filter": platform,
            "post_count": len(usable), "minimum_posts": MIN_POSTS, "skipped": skipped}
    if len(usable) < MIN_POSTS:
        return {**base, "status": "insufficient_sample",
                "message": f"{len(usable)} usable post(s); at least {MIN_POSTS} are needed for a voice profile. "
                           "No profile was written."}
    tells = tells or load_tells()
    rows = [features(text) for text, _ in usable]
    overall = {key: _stats([row[key] for row in rows]) for key in FEATURE_KEYS}
    by_platform: dict[str, dict] = {}
    platforms = Counter(label or "unlabelled" for _, label in usable)
    for name, count in sorted(platforms.items()):
        if name == "unlabelled" or count < PLATFORM_MIN_POSTS:
            continue
        subset = [row for row, (_, label) in zip(rows, usable) if label == name]
        by_platform[name] = {"post_count": count,
                             **{key: _stats([r[key] for r in subset]) for key in PLATFORM_FEATURES}}
    habits = {
        "starts_lowercase": _share([row["starts_lowercase"] for row in rows]),
        "question_opener": _share([row["question_opener_habit"] for row in rows]),
        "hashtags_at_end": _share([row["hashtags_at_end"] for row in rows if "hashtags_at_end" in row]),
        "link_at_end": _share([row["link_at_end"] for row in rows if "link_at_end" in row]),
        "posts_with_emoji": _share([row["emoji"] > 0 for row in rows]),
        "posts_with_hashtags": _share([row["hashtags"] > 0 for row in rows]),
        "posts_with_links": _share([row["links"] > 0 for row in rows]),
    }
    openings: Counter = Counter()
    words: Counter = Counter()
    phrases: Counter = Counter()
    tags: Counter = Counter()
    tell_posts: Counter = Counter()
    for text, _ in usable:
        tokens = [w.casefold() for w in _words(text)]
        if tokens and not tokens[0].isdigit():
            openings[tokens[0]] += 1
        words.update({w for w in tokens if len(w) >= 3 and w.isalpha() and w not in BASELINE})
        phrases.update({f"{a} {b}" for a, b in zip(tokens, tokens[1:])
                        if a.isalpha() and b.isalpha() and not (a in BASELINE and b in BASELINE)})
        tags.update({tag.casefold() for tag in _HASHTAG.findall(text)})
        tell_posts.update({hit["id"] for hit in find_tells(text, tells)})
    digests = sorted(hashlib.sha256(text.encode("utf-8")).hexdigest() for text, _ in usable)
    return {**base, "status": "ok", "created_at": utc_now(),
            "sample_sha256": hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest(),
            "platforms": dict(sorted(platforms.items())),
            "features": overall, "by_platform": by_platform, "habits": habits,
            "openings": _top(openings, TOP_OPENINGS, 2),
            "distinctive_words": _top(words, TOP_WORDS, MIN_DOC_FREQ),
            "distinctive_phrases": _top(phrases, TOP_PHRASES, MIN_DOC_FREQ),
            "hashtags": _top(tags, TOP_HASHTAGS, 2),
            "tell_rates": {tid: round(n / len(usable), 3) for tid, n in sorted(tell_posts.items())},
            "tells_updated": tells["updated"],
            "note": PROFILE_NOTE}


def write_profile(profile: dict, out: Path) -> str:
    """Write a NEW owner-only (0600) JSON file; refuse an existing path or a missing folder."""
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise _fail(f"--out {out} already exists; choose a new file name (profiles are never overwritten)")
    if not out.parent.is_dir():
        raise _fail(f"--out folder {out.parent} does not exist")
    write_private_file(out, json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    return str(out)


# ---- checking a draft -------------------------------------------------------------------------------------------

def load_profile(path: Path) -> dict:
    """Read a profile written by ``voice-profile`` and check the fields the comparison relies on."""
    try:
        data = json.loads(_read_bounded(path, MAX_PROFILE_BYTES, "--profile"))
    except json.JSONDecodeError as exc:
        raise _fail(f"--profile: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from None
    if not isinstance(data, dict) or data.get("schema_version") != PROFILE_SCHEMA or data.get("status") != "ok":
        raise _fail(f"--profile must be a {PROFILE_SCHEMA} file with status ok (made by reachweaver voice-profile)")

    def numeric_stats(block: object, keys: tuple[str, ...], label: str) -> None:
        if not isinstance(block, dict):
            raise _fail(f"--profile: {label} must be an object")
        for key in keys:
            stats = block.get(key)
            if not isinstance(stats, dict) or not all(isinstance(stats.get(q), (int, float)) and
                                                      not isinstance(stats.get(q), bool)
                                                      for q in ("median", "p10", "p25", "p75", "p90")):
                raise _fail(f"--profile: {label}.{key} needs numeric median, p10, p25, p75 and p90")

    numeric_stats(data.get("features"), FEATURE_KEYS, "features")
    by_platform = data.get("by_platform", {})
    if not isinstance(by_platform, dict):
        raise _fail("--profile: by_platform must be an object")
    for name, block in by_platform.items():
        numeric_stats(block, PLATFORM_FEATURES, f"by_platform.{name}")
    for key in ("habits", "tell_rates"):
        if not isinstance(data.get(key, {}), dict):
            raise _fail(f"--profile: {key} must be an object")
    if not isinstance(data.get("post_count"), int):
        raise _fail("--profile: post_count must be an integer")
    return data


def _fmt(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def check_text(text: str, profile: dict | None = None, platform: str | None = None,
               tells: dict | None = None) -> dict:
    """Advisory findings for one draft: features outside the owner's usual range, and heuristic tells."""
    if not isinstance(text, str) or not text.strip():
        raise _fail("the draft text is empty")
    if len(text) > MAX_DRAFT_CHARS:
        raise _fail(f"the draft is longer than {MAX_DRAFT_CHARS} characters")
    tells = tells or load_tells()
    platform = platform.strip().casefold() if platform else None
    feats = features(text)
    findings: list[dict] = []
    basis = None
    if profile is not None:
        platform_block: dict | None = profile.get("by_platform", {}).get(platform) if platform else None
        basis = "platform" if platform_block else "overall"
        for key, label in FEATURES:
            use_platform = platform_block is not None and key in PLATFORM_FEATURES
            block: dict = platform_block if platform_block is not None and use_platform else profile["features"]
            stats = block[key]
            value = feats[key]
            if stats["p10"] <= value <= stats["p90"]:
                continue
            direction = "above" if value > stats["p90"] else "below"
            scope = (f"your {platform} posts" if use_platform else "your posts")
            findings.append({
                "code": f"voice.{key}", "kind": "voice", "severity": "warn", "value": value,
                "usual_range": [stats["p10"], stats["p90"]], "median": stats["median"],
                "basis": "platform" if use_platform else "overall",
                "message": (f"{label}: {_fmt(value)}, {direction} the usual range of {scope} "
                            f"({_fmt(stats['p10'])}-{_fmt(stats['p90'])}, median {_fmt(stats['median'])})")})
        habits = profile.get("habits", {})
        for key, rare, common in HABITS:
            draft_value = feats.get("question_opener_habit") if key == "question_opener" else feats.get(key)
            share = habits.get(key)
            if draft_value is None or not isinstance(share, (int, float)):
                continue
            if draft_value and share < 0.15:
                message = rare.format(share=f"{share:.0%}")
            elif not draft_value and share > 0.85:
                message = common.format(share=f"{share:.0%}")
            else:
                continue
            findings.append({"code": f"habit.{key}", "kind": "voice", "severity": "info", "value": bool(draft_value),
                             "owner_share": share, "message": message})
    rates = (profile or {}).get("tell_rates", {})
    by_id = {tell["id"]: tell for tell in tells["tells"]}
    for hit in find_tells(text, tells, feats):
        tell = by_id[hit["id"]]
        severity = tell["severity"]
        own = rates.get(hit["id"]) if isinstance(rates.get(hit["id"]), (int, float)) else None
        detail = (f"matched: {', '.join(repr(m) for m in hit['matched'])}" if "matched" in hit
                  else f"{hit['count']} (flagged at {hit['threshold']} or more)")
        message = f"{hit['id']}: {detail}. {tell['reason']}"
        if own is not None and own >= OWN_HABIT_SHARE:
            severity = "info"
            message += f" {own:.0%} of your own posts do this too, so it may simply be your style."
        finding = {"code": f"tell.{hit['id']}", "kind": "ai_tell", "severity": severity, "message": message,
                   "reason": tell["reason"], "count": hit["count"]}
        if "matched" in hit:
            finding["matched"] = hit["matched"]
        if own is not None:
            finding["owner_share"] = own
        findings.append(finding)
    findings.sort(key=lambda f: (f["severity"] != "warn", f["kind"] != "voice", f["code"]))
    from .creative_preflight import human_only_platforms  # local import: creative_preflight imports this module
    result: dict = {
        "schema_version": CHECK_SCHEMA, "status": "advisory_never_blocks", "platform": platform,
        "profile_used": profile is not None,
        "profile_post_count": profile.get("post_count") if profile else None,
        "comparison_basis": basis,
        "draft": {key: feats[key] for key in FEATURE_KEYS},
        "findings": findings,
        "counts": {"warn": sum(f["severity"] == "warn" for f in findings),
                   "info": sum(f["severity"] == "info" for f in findings)},
        "tell_list": {"label": tells["label"], "updated": tells["updated"]},
        "limitations": NOTE, "blocks": False, "network_access": False, "model_calls": False,
        "database_writes": False,
    }
    if profile is not None:
        result["profile_hints"] = {"openings": profile.get("openings", [])[:5],
                                   "distinctive_words": profile.get("distinctive_words", [])[:10],
                                   "distinctive_phrases": profile.get("distinctive_phrases", [])[:5]}
    if platform and platform in human_only_platforms():
        result["human_only_note"] = HUMAN_ONLY_NOTE
    return result


# ---- rendering and the CLI --------------------------------------------------------------------------------------

def render_profile(profile: dict, written: str | None) -> str:
    if profile["status"] != "ok":
        return f"insufficient_sample: {profile['message']}"
    f = profile["features"]
    platforms = ", ".join(f"{name} {n}" for name, n in profile["platforms"].items())
    lines = [f"Voice profile written to {written} (owner-only file, mode 600)" if written else "Voice profile",
             f"  posts: {profile['post_count']} ({platforms})",
             f"  length: median {_fmt(f['length_chars']['median'])} characters "
             f"(middle half {_fmt(f['length_chars']['p25'])}-{_fmt(f['length_chars']['p75'])})",
             f"  sentences: median {_fmt(f['sentence_words']['median'])} words",
             f"  per post (median): {_fmt(f['emoji']['median'])} emoji, {_fmt(f['hashtags']['median'])} hashtags, "
             f"{_fmt(f['links']['median'])} links, {_fmt(f['em_dashes']['median'])} em dashes"]
    for name, block in profile["by_platform"].items():
        lines.append(f"  {name}: median {_fmt(block['length_chars']['median'])} characters "
                     f"(middle half {_fmt(block['length_chars']['p25'])}-{_fmt(block['length_chars']['p75'])}), "
                     f"{block['post_count']} posts")
    if profile["openings"]:
        lines.append("  common openings: " + ", ".join(o["text"] for o in profile["openings"][:5]))
    if profile["distinctive_words"]:
        lines.append("  recurring words: " + ", ".join(w["text"] for w in profile["distinctive_words"][:10]))
    lines += ["", profile["note"]]
    return "\n".join(lines)


def render_check(report: dict) -> str:
    counts = report["counts"]
    if report["profile_used"]:
        basis = (f"your profile of {report['profile_post_count']} posts"
                 + (f" ({report['platform']} length statistics)" if report["comparison_basis"] == "platform" else ""))
    else:
        basis = "no profile: tell list and generic checks only"
    lines = [f"Voice check: {counts['warn']} warn, {counts['info']} info (advisory; never blocks)",
             f"Compared with: {basis}"]
    if report.get("human_only_note"):
        lines.append(report["human_only_note"])
    for item in report["findings"]:
        lines.append(f"  {item['severity']:<4}  {item['code']}: {item['message']}")
    if not report["findings"]:
        lines.append("  No findings.")
    return "\n".join([*lines, "", report["tell_list"]["label"], report["limitations"]])


def run_command(args: argparse.Namespace) -> int:
    """CLI entry for ``voice-profile`` and ``voice-check``; never opens the database for writing."""
    if args.command == "voice-profile":
        if args.account and not args.from_db:
            raise _fail("--account applies only with --from-db")
        if args.from_db:
            posts = load_posts_db(args.db, account=args.account, platform=args.platform)
            source = "database"
        else:
            posts = load_posts_file(args.from_file)
            source = "file"
        profile = build_profile(posts, platform=args.platform, source=source)
        written = write_profile(profile, args.out) if profile["status"] == "ok" else None
        if args.json:
            print(json.dumps({**profile, "written_to": written}, indent=2, ensure_ascii=False))
        else:
            print(render_profile(profile, written))
        return 0 if written else 1
    if args.post_id is not None:
        text, stored_platform = load_post_text(args.db, args.post_id)
    elif args.file is not None:
        text, stored_platform = _read_bounded(args.file, MAX_DRAFT_CHARS * 4, "--file"), None
    else:
        text, stored_platform = args.text, None
    check_profile = load_profile(args.profile) if args.profile else None
    report = check_text(text, check_profile, args.platform or stored_platform)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_check(report))
    return 0
