"""Milestone follow-up suggestions: owner-set metric thresholds that PROPOSE follow-ups for human review.

``reachweaver followup-suggest --rules FILE`` reads the owner's own published posts and their metric snapshots
from the local database **read-only** (``storage.connect_readonly``: SQLite ``mode=ro`` plus ``query_only``; a
missing database fails and is never created, an older schema is never migrated). When a post's exact metric value
reaches an owner-set threshold, it prints a short *brief* for a follow-up (a thank-you post with a resource link, a
recap, an adaptation for another configured account, a manual pin/feature note, or a longer write-up idea) and the
exact next commands of the normal flow (``draft`` -> ``show`` -> the owner's own ``approve``; or ``idea add``).

Without ``--rules`` the thresholds come from the owner's own results (``derive_rules``): per account, 2 x the median
likes + replies + reposts at about 7 days (``insights``' matched-age rule, at least 10 posts with a result), labelled
"derived from your N posts". Too little data gives a friendly next step, never a guessed threshold.

What it never does, by construction (there is no import of a publisher, approval or send function here, and
``SEND_PATH = None`` is asserted in tests):

* no like, comment, reply, DM, repost, follow or vote, automated or otherwise;
* no draft, approval, schedule or send: the output is text for a person (or Claude) to act on through the normal flow;
* no network request;
* no suggestion at all for posts on human-only or moderator-only destinations (Reddit, Hacker News and every
  platform the distribution registry marks ``human_only``), and none of them is ever a cross-post target;
* a handle on the local opt-out list (``optout.py``) is never named in a suggestion; only a count is shown.

Values: only exact counts (the INTEGER metric columns) are compared. Platform estimates and suppressed, delayed or
unavailable values (``metric_status``) are never used, exactly like ``storage.report``. Crossing a threshold is an
observation, not evidence that the post caused anything.

Dedupe: ``--state FILE`` names a private JSON file of ``(post_id, rule_id)`` pairs already suggested; it is read
when present and written (atomically, mode 0600) only with ``--record``. It holds ids and a timestamp, no text.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .policy import PLATFORMS, ValidationError
from .storage import METRIC_FIELDS, metric_states, parse_utc

SEND_PATH = None  # Asserted in tests: this module can never draft, approve, schedule or send.
RULES_KIND = "reachweaver.followup-rules"
STATE_KIND = "reachweaver.followup-state"
OUTPUT_SCHEMA = "reachweaver.followup-suggestions.v1"
SCHEMA_VERSION = 1
KINDS = ("thank_and_link", "recap_thread", "cross_post", "pin_or_feature", "write_up")
LABEL = "suggestion for review; nothing is drafted, approved or sent"
HARD_RULES = (
    "Never auto-like, auto-comment, auto-reply, auto-DM, auto-repost or auto-follow: every follow-up is a new post "
    "the owner reviews and approves through the normal flow, or a manual step the owner takes in the official app.",
    "Reddit, Hacker News and other human-only or moderator-only communities get no follow-up suggestion and are "
    "never a cross-post target.",
    "Handles on the local opt-out list are never named or suggested for a mention.",
    "Thresholds are observations of exact counts only (estimates excluded); crossing one does not show the post "
    "caused anything.",
)
# Human-only / moderator-only destinations (the safety rule; the registry adds every platform it marks human_only).
_HUMAN_ONLY_BASE = frozenset({"reddit", "hackernews", "hacker_news"})
RULE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ACCOUNT = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
RULE_KEYS = frozenset({"id", "metric", "threshold", "within_hours", "platforms", "accounts", "kind", "link",
                       "target_platforms", "note"})
RULE_REQUIRED = ("id", "metric", "threshold", "kind")
MAX_RULES = 200
MAX_RULES_BYTES = 1_000_000
MAX_STATE_BYTES = 4_000_000
MAX_STATE_ENTRIES = 20_000
MAX_POSTS = 5000
NOTE_MAX = 280
THRESHOLD_MAX = 10 ** 12
WITHIN_HOURS_MAX = 24 * 366
# Without --rules: one "took off" rule per account, derived from that account's own matched-age results
# (docs/INSIGHTS.md "Where your results are used"). Not owner-settable here; --rules overrides it entirely.
DERIVED_METRIC = "interactions"  # likes + replies + reposts, exact in one snapshot (as insights)
DERIVED_MULTIPLIER = 2
DERIVED_MIN_POSTS = 10
DERIVED_AGE_HOURS = 168
DERIVED_TOLERANCE_HOURS = 24
DERIVED_KIND = "recap_thread"


def human_only_platforms() -> frozenset[str]:
    """Reddit and Hacker News always; plus every platform the distribution registry marks ``human_only``."""
    try:
        from .distribution import load_registry
        registry = {p["id"] for p in load_registry().get("platforms", []) if p.get("human_only") is True}
    except (ValidationError, OSError, ValueError, KeyError, TypeError):
        registry = set()
    return _HUMAN_ONLY_BASE | frozenset(registry)


# --------------------------------------------------------------------- rules

def _fail(where: str, message: str) -> ValidationError:
    return ValidationError(f"followup rules: {where}: {message}")


def _str_list(value, where: str, pattern=None, allowed=None) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 50 or not all(isinstance(v, str) for v in value):
        raise _fail(where, "must be a nonempty list of at most 50 strings")
    for item in value:
        if allowed is not None and item not in allowed:
            raise _fail(where, f"unknown platform {item[:40]!r}")
        if pattern is not None and not pattern.fullmatch(item):
            raise _fail(where, f"{item[:40]!r} is not a valid account id")
    return list(dict.fromkeys(value))


def validate_rules(raw) -> list[dict]:
    """Normalized rules; any defect raises (a rule set never half-loads).

    Accepts ``{"schema_version": 1, "kind": "reachweaver.followup-rules", "rules": [...]}`` or a bare list.
    """
    if isinstance(raw, dict):
        unknown = sorted(set(raw) - {"schema_version", "kind", "rules", "description"})
        if unknown:
            raise _fail("top level", f"unknown keys {', '.join(unknown)}")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise _fail("top level", f"schema_version must be {SCHEMA_VERSION}")
        if raw.get("kind", RULES_KIND) != RULES_KIND:
            raise _fail("top level", f"kind must be {RULES_KIND!r}")
        rules = raw.get("rules")
    else:
        rules = raw
    if not isinstance(rules, list) or not rules:
        raise _fail("rules", "must be a nonempty list of rule objects")
    if len(rules) > MAX_RULES:
        raise _fail("rules", f"at most {MAX_RULES} rules")
    out: list[dict] = []
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        where = f"rules[{index}]"
        if not isinstance(rule, dict):
            raise _fail(where, "must be an object")
        missing = [key for key in RULE_REQUIRED if key not in rule]
        if missing:
            raise _fail(where, f"missing {', '.join(missing)}")
        unknown = sorted(set(rule) - RULE_KEYS)
        if unknown:
            raise _fail(where, f"unknown keys {', '.join(unknown)}")
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not RULE_ID.fullmatch(rule_id):
            raise _fail(where, "id must be 1..63 lowercase letters, digits or hyphens")
        if rule_id in seen:
            raise _fail(where, f"duplicate id {rule_id!r}")
        seen.add(rule_id)
        where = f"rule {rule_id}"
        if rule["metric"] not in METRIC_FIELDS:
            raise _fail(where, f"metric must be one of {', '.join(METRIC_FIELDS)}")
        threshold = rule["threshold"]
        if type(threshold) is not int or not 1 <= threshold <= THRESHOLD_MAX:
            raise _fail(where, f"threshold must be an integer 1..{THRESHOLD_MAX}")
        if rule["kind"] not in KINDS:
            raise _fail(where, f"kind must be one of {', '.join(KINDS)}")
        within = rule.get("within_hours")
        if within is not None and (type(within) is not int or not 1 <= within <= WITHIN_HOURS_MAX):
            raise _fail(where, f"within_hours must be an integer 1..{WITHIN_HOURS_MAX}")
        platforms = _str_list(rule["platforms"], f"{where}.platforms", allowed=PLATFORMS) if "platforms" in rule else None
        accounts = _str_list(rule["accounts"], f"{where}.accounts", pattern=ACCOUNT) if "accounts" in rule else None
        targets = None
        if "target_platforms" in rule:
            if rule["kind"] != "cross_post":
                raise _fail(where, "target_platforms applies only to kind cross_post")
            targets = _str_list(rule["target_platforms"], f"{where}.target_platforms", allowed=PLATFORMS)
            blocked = sorted(set(targets) & human_only_platforms())
            if blocked:
                raise _fail(where, f"target_platforms {', '.join(blocked)} are human-only or moderator-only; "
                                   "ReachWeaver never suggests cross-posting there")
        link = rule.get("link")
        if link is not None:
            from .campaigns import public_url
            if not isinstance(link, str) or len(link) > 2000 or not public_url(link):
                raise _fail(where, "link must be a public https URL (no credentials, IP address or local host)")
        note = rule.get("note")
        if note is not None:
            if not isinstance(note, str) or not 0 < len(note.strip()) <= NOTE_MAX or any(
                    ord(ch) < 32 for ch in note):
                raise _fail(where, f"note must be one line of 1..{NOTE_MAX} characters")
            from .privacy import redact
            if redact(note) != note:
                raise _fail(where, "note looks like it contains a credential; remove it")
        out.append({"id": rule_id, "metric": rule["metric"], "threshold": threshold, "kind": rule["kind"],
                    "within_hours": within, "platforms": platforms, "accounts": accounts,
                    "target_platforms": targets, "link": link, "note": note.strip() if note else None})
    return out


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    if path.is_symlink():
        raise ValidationError(f"{label} {path} cannot be a symbolic link")
    if not path.is_file():
        raise ValidationError(f"{label} {path} does not exist or is not a regular file")
    if path.stat().st_size > limit:
        raise ValidationError(f"{label} {path} is larger than {limit} bytes")
    return path.read_bytes()


def load_rules(path: str | Path) -> list[dict]:
    raw = _read_bounded(Path(path), MAX_RULES_BYTES, "Rules file")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValidationError(f"followup rules: {path} is not valid UTF-8 JSON") from None
    return validate_rules(data)


def derive_rules(con, *, now: datetime, account_id: str | None = None) -> tuple[list[dict], dict]:
    """Default "took off" rules from the owner's OWN results, used only when ``--rules`` is omitted. SELECT only.

    Per account x platform (``insights.typical_results``: matched age 168 h +/- 24 h, one snapshot source, exact
    counts only): threshold = ceil(2 x the median interactions), compared against posts' exact interactions from
    that same source no later than 192 h after posting. Fewer than ``DERIVED_MIN_POSTS`` posts with a result, a
    median of 0, or a human-only platform gives no rule for that account, and says why.
    """
    from math import ceil

    from .insights import typical_results
    blocked = human_only_platforms()
    rules: list[dict] = []
    accounts: list[dict] = []
    used: set[str] = set()
    for cohort in typical_results(con, account_id=account_id, now=now):
        n, middle = cohort["posts_with_result"], cohort["median"]
        entry = {"account_id": cohort["account_id"], "platform": cohort["platform"], "source": cohort["source"],
                 "posts_with_result": n, "median": middle, "threshold": None}
        if cohort["platform"] in blocked:
            entry.update(status="human_only_platform", text="human-only or moderator-only platform: no follow-up")
        elif n < DERIVED_MIN_POSTS:
            entry.update(status="too_little_data",
                         text=f"{n} of your posts have a result at about 7 days; {DERIVED_MIN_POSTS} are needed")
        elif not middle:
            entry.update(status="median_zero", text=f"your typical post had 0 {DERIVED_METRIC} at about 7 days, "
                                                    "so 'twice the usual' means nothing yet; pass --rules FILE")
        else:
            threshold = max(1, ceil(DERIVED_MULTIPLIER * middle))
            slug = re.sub(r"[^a-z0-9-]", "-", cohort["account_id"].lower())
            rule_id = ("took-off-" + slug)[:63].rstrip("-")
            if rule_id in used:
                rule_id = (rule_id[:50] + "-" + re.sub(r"[^a-z0-9-]", "-", cohort["platform"]))[:63]
            used.add(rule_id)
            basis = (f"Threshold derived from your {n} posts: {DERIVED_MULTIPLIER} x your median of {middle:g} "
                     f"{DERIVED_METRIC} (likes + replies + reposts) about 7 days after posting, source "
                     f"'{cohort['source']}'. An observation, not a cause.")
            entry.update(status="derived", threshold=threshold, rule_id=rule_id,
                         text=f"derived from your {n} posts: {DERIVED_MULTIPLIER} x median {middle:g} = {threshold}")
            rules.append({"id": rule_id, "metric": DERIVED_METRIC, "threshold": threshold, "kind": DERIVED_KIND,
                          "within_hours": DERIVED_AGE_HOURS + DERIVED_TOLERANCE_HOURS,
                          "platforms": [cohort["platform"]], "accounts": [cohort["account_id"]],
                          "target_platforms": None, "link": None, "note": None, "source": cohort["source"],
                          "derived_from": basis})
        accounts.append(entry)
    info = {"basis": "derived_from_your_own_results", "metric": DERIVED_METRIC, "multiplier": DERIVED_MULTIPLIER,
            "post_age_hours": DERIVED_AGE_HOURS, "age_tolerance_hours": DERIVED_TOLERANCE_HOURS,
            "within_hours": DERIVED_AGE_HOURS + DERIVED_TOLERANCE_HOURS, "min_posts": DERIVED_MIN_POSTS,
            "kind": DERIVED_KIND, "accounts": accounts,
            "note": ("'Took off' = at least twice this account's own median at the same post age. Pass --rules FILE "
                     "to set your own thresholds instead.")}
    return rules, info


# --------------------------------------------------------------------- dedupe state

def load_state(path: str | Path | None) -> dict:
    """``{"schema_version":1,"kind":...,"suggested":[{"post_id","rule_id","recorded_at"}]}``; missing = empty."""
    empty = {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND, "suggested": []}
    if path is None:
        return empty
    target = Path(path)
    if target.is_symlink():
        raise ValidationError(f"State file {target} cannot be a symbolic link")
    if not target.exists():
        return empty
    if os.name == "posix" and stat.S_IMODE(target.stat().st_mode) & 0o077:
        raise ValidationError(f"State file {target} is not private; restrict it with chmod 600 before running")
    raw = _read_bounded(target, MAX_STATE_BYTES, "State file")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValidationError(f"State file {target} is not valid JSON; refusing to guess what was suggested") from None
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION
            or data.get("kind") != STATE_KIND or not isinstance(data.get("suggested"), list)):
        raise ValidationError(f"State file {target} is not a followup-suggest state file "
                              f"(schema_version {SCHEMA_VERSION}, kind {STATE_KIND})")
    entries = [e for e in data["suggested"] if isinstance(e, dict) and isinstance(e.get("post_id"), str)
               and isinstance(e.get("rule_id"), str)]
    return {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND, "suggested": entries}


def write_state(path: str | Path, state: dict, suggestions: list[dict], now: datetime) -> int:
    """Record the suggested (post_id, rule_id) pairs; atomic replace through a new 0600 file. Returns added count."""
    known = {(e["post_id"], e["rule_id"]) for e in state["suggested"]}
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    added = []
    for item in suggestions:
        key = (item["post_id"], item["rule_id"])
        if key not in known:
            known.add(key)
            added.append({"post_id": key[0], "rule_id": key[1], "recorded_at": stamp})
    merged = {"schema_version": SCHEMA_VERSION, "kind": STATE_KIND,
              "suggested": (state["suggested"] + added)[-MAX_STATE_ENTRIES:]}
    target = Path(path)
    if target.is_symlink():
        raise ValidationError(f"State file {target} cannot be a symbolic link")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix=".followup-state-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return len(added)


# --------------------------------------------------------------------- database (SELECT only)

_POSTED_AT = ("COALESCE((SELECT MAX(e.at) FROM event_log e WHERE e.post_id=p.id AND e.action='published'),"
              " p.send_started_at, p.scheduled_at, p.created_at)")


def load_published_posts(con, *, account_id: str | None = None, since: str | None = None) -> list[dict]:
    """The owner's published top-level posts (replies excluded), newest first, at most ``MAX_POSTS``. SELECT only."""
    where = ["p.status='published'", "p.reply_target IS NULL"]
    params: list[str | int] = []
    if account_id is not None:
        where.append("p.account_id=?")
        params.append(account_id)
    query = (f"""SELECT * FROM (SELECT p.id, p.account_id, p.platform, p.campaign, p.content, p.remote_url,
                                       {_POSTED_AT} AS posted_at
                                FROM posts p WHERE {' AND '.join(where)})
                 {'WHERE posted_at>=?' if since else ''} ORDER BY posted_at DESC, id LIMIT ?""")
    if since:
        params.append(since)
    params.append(MAX_POSTS)
    return [dict(row) for row in con.execute(query, params)]


def snapshots_by_post(con, post_ids: list[str]) -> dict[str, list]:
    """``{post_id: [snapshot rows, oldest first]}``. SELECT only."""
    out: dict[str, list] = {pid: [] for pid in post_ids}
    for start in range(0, len(post_ids), 400):
        chunk = post_ids[start:start + 400]
        rows = con.execute(f"SELECT * FROM metric_snapshots WHERE post_id IN ({','.join('?' for _ in chunk)}) "
                           "ORDER BY observed_at, id", chunk)
        for row in rows:
            out[row["post_id"]].append(row)
    return out


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(parse_utc(value))


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def _state(row, metric: str) -> tuple[str, int | None]:
    """``metric_states`` for one metric; ``interactions`` (derived rules only) is exact only when likes, replies
    and reposts are all exact in the same snapshot, and is then their sum."""
    states = metric_states(row)
    if metric != DERIVED_METRIC:
        return states[metric]
    parts = [states[name] for name in ("likes", "replies", "reposts")]
    if all(label == "exact" for label, _ in parts):
        return "exact", sum(value for _, value in parts)
    other = next((label for label, _ in parts if label not in ("exact", "unknown")), "unknown")
    return other, None


def _observation(snapshots: list, metric: str, deadline: datetime | None,
                 source: str | None = None) -> tuple[dict | None, str | None]:
    """The latest exact observation of ``metric`` (no later than ``deadline``; only snapshots of ``source`` when
    given) and, when there is none, the status of the latest non-exact one (``estimated``, ``suppressed``...) so
    the skip can say why."""
    best, other = None, None
    for row in snapshots:  # oldest first, so the last match wins
        if source is not None and row["source"] != source:
            continue
        try:
            observed = _dt(row["observed_at"])
        except ValidationError:
            continue
        if deadline is not None and observed > deadline:
            continue
        label, value = _state(row, metric)
        if label == "exact":
            best = {"value": value, "observed_at": observed, "source": row["source"],
                    "window_start": row["window_start"], "window_end": row["window_end"]}
        elif label != "unknown":
            other = label
    return best, (None if best else other)


# --------------------------------------------------------------------- suggestions

def _mentions(content: str, optout: list[dict], platform: str) -> tuple[list[str], int]:
    """@-mentions in the original post that may be credited, and how many are withheld by the opt-out list."""
    from .optout import listed_entry, mentioned_handles
    allowed, withheld = [], 0
    for handle, _start, _end in mentioned_handles(content):
        if listed_entry(handle, optout, platform) is not None:
            withheld += 1
        elif "@" + handle not in allowed:
            allowed.append("@" + handle)
    return allowed[:10], withheld


def _cross_targets(rule: dict, post: dict, accounts: dict | None, blocked: frozenset[str]) -> list[dict]:
    if not accounts:
        return []
    wanted = set(rule["target_platforms"]) if rule["target_platforms"] else None
    out = []
    for account_id, account in sorted(accounts.items()):
        platform = account.get("platform")
        if (account_id == post["account_id"] or platform == post["platform"] or platform in blocked
                or (wanted is not None and platform not in wanted)):
            continue
        out.append({"account_id": account_id, "platform": platform})
    return out


def _brief(rule: dict, post: dict, hit: dict, *, mentions: list[str], withheld: int,
           targets: list[dict], accounts_known: bool) -> tuple[str, list[str], bool]:
    """(brief for the person or Claude who drafts, next commands of the normal flow, manual_only)."""
    pid, account, platform = post["id"], post["account_id"], post["platform"]
    remote = post["remote_url"] if post.get("remote_url") else None
    from .campaigns import public_url
    remote_public = remote if remote and public_url(remote) else None
    seen = f"Post {pid} on {platform} ({account}) shows {hit['value']} {rule['metric']} (threshold {rule['threshold']})."
    show = f"RW show {pid}"
    source_flag = f" --source-url {shlex.quote(rule['link'])}" if rule["link"] else ""
    credit = ""
    if mentions:
        credit = f" You may credit people the original post already mentions: {', '.join(mentions)}."
    if withheld:
        credit += (f" {withheld} mention(s) in the original are on the opt-out list: do not name or tag them.")
    if rule["kind"] == "thank_and_link":
        resource = f"the resource {rule['link']}" if rule["link"] else "one resource the owner names (docs, repo or guide)"
        brief = (f"{seen} Draft one short new post from the same account: thank readers in plain words and point to "
                 f"{resource}. One link, no hashtag stuffing, no request to like, share or follow.{credit}")
        commands = [show, f"RW draft --account {account} --file followup.txt{source_flag}",
                    "RW show NEW_POST_ID  # the owner reviews and approves it themselves"]
        return brief, commands, False
    if rule["kind"] == "recap_thread":
        back = f" Link back to the original ({remote_public})." if remote_public else ""
        brief = (f"{seen} Draft a recap from the same account: what the post covered, the most useful follow-up "
                 f"detail and any questions readers raised (read them yourself; quote nobody without permission)."
                 f"{back} Keep every claim backed by the original sources.{credit}")
        recap_flag = f" --source-url {shlex.quote(remote_public)}" if remote_public else ""
        commands = [show, f"RW draft --account {account} --file recap.txt{recap_flag}",
                    "RW show NEW_POST_ID  # the owner reviews and approves it themselves"]
        return brief, commands, False
    if rule["kind"] == "cross_post":
        recap_flag = f" --source-url {shlex.quote(remote_public)}" if remote_public else ""
        if targets:
            names = ", ".join(f"{t['account_id']} ({t['platform']})" for t in targets)
            brief = (f"{seen} Adapt (do not copy verbatim) the idea for: {names}. Follow each platform's norms and "
                     f"length; run repeat-check before the owner approves.{credit}")
            commands = [show] + [f"RW draft --account {t['account_id']} --file adapted-{t['platform']}.txt{recap_flag}"
                                 for t in targets] + ["RW repeat-check --post-id NEW_POST_ID"]
            return brief, commands, False
        why = ("no other configured account matches this rule" if accounts_known
               else "the accounts config could not be read, so no target account is known")
        brief = (f"{seen} A cross-platform adaptation would fit, but {why}. Configure the target account first, or "
                 "prepare a manual copy with share.")
        return brief, [show, "RW doctor"], True
    if rule["kind"] == "pin_or_feature":
        brief = (f"{seen} Manual step for the owner: consider pinning this post on the {platform} profile or "
                 "featuring it (profile link, README, newsletter) in the official app. ReachWeaver has no pin action "
                 "and takes none.")
        return brief, [show], True
    # write_up
    idea = f"Write-up: expand post {pid} ({rule['metric']} {hit['value']}) into a longer article"
    source = f" --source {shlex.quote(remote_public)}" if remote_public else ""
    brief = (f"{seen} Idea for a longer write-up (blog, docs page or article): expand what readers responded to, "
             f"with sources and examples. Capture it in the ideas inbox; it is not a draft.{credit}")
    return brief, [show, f"RW idea add {shlex.quote(idea)}{source} --tag followup"], False


def build_suggestions(con, rules: list[dict], *, now: datetime, account_id: str | None = None,
                      since: str | None = None, optout: list[dict] | None = None, accounts: dict | None = None,
                      accounts_error: str | None = None, state: dict | None = None) -> dict:
    """The follow-up report. SELECT statements only; nothing is written, drafted or sent."""
    optout = optout or []
    blocked = human_only_platforms()
    already = {(e["post_id"], e["rule_id"]) for e in (state or {}).get("suggested", [])}
    since_utc = parse_utc(since) if since else None
    posts = load_published_posts(con, account_id=account_id, since=since_utc)
    snaps = snapshots_by_post(con, [p["id"] for p in posts])
    suggestions: list[dict] = []
    skipped: list[dict] = []
    counts = {"posts_considered": len(posts), "rules": len(rules), "suggestions": 0, "below_threshold": 0,
              "no_exact_value": 0, "no_snapshot": 0, "human_only_platform": 0, "already_suggested": 0,
              "not_matching_filters": 0}
    for post in posts:
        if post["platform"] in blocked:
            counts["human_only_platform"] += 1
            skipped.append({"post_id": post["id"], "platform": post["platform"], "rule_id": None,
                            "reason": "human_only_platform"})
            continue
        if not snaps[post["id"]]:
            counts["no_snapshot"] += 1
            continue
        try:
            posted = _dt(post["posted_at"])
        except ValidationError:
            posted = None
        for rule in rules:
            if ((rule["platforms"] and post["platform"] not in rule["platforms"])
                    or (rule["accounts"] and post["account_id"] not in rule["accounts"])):
                counts["not_matching_filters"] += 1
                continue
            deadline = None
            if rule["within_hours"] is not None:
                if posted is None:
                    counts["no_exact_value"] += 1
                    continue
                deadline = posted + timedelta(hours=rule["within_hours"])
            hit, other = _observation(snaps[post["id"]], rule["metric"], deadline, rule.get("source"))
            if hit is None:
                counts["no_exact_value"] += 1
                if other is not None:
                    skipped.append({"post_id": post["id"], "platform": post["platform"], "rule_id": rule["id"],
                                    "reason": f"no_exact_value ({other} values are never compared)"})
                continue
            if hit["value"] < rule["threshold"]:
                counts["below_threshold"] += 1
                continue
            if (post["id"], rule["id"]) in already:
                counts["already_suggested"] += 1
                skipped.append({"post_id": post["id"], "platform": post["platform"], "rule_id": rule["id"],
                                "reason": "already_suggested"})
                continue
            mentions, withheld = _mentions(post["content"] or "", optout, post["platform"])
            targets = _cross_targets(rule, post, accounts, blocked) if rule["kind"] == "cross_post" else []
            brief, commands, manual = _brief(rule, post, hit, mentions=mentions, withheld=withheld,
                                             targets=targets, accounts_known=accounts is not None)
            if rule["note"]:
                brief += f" Owner note: {rule['note']}"
            if rule.get("derived_from"):
                brief += f" {rule['derived_from']}"
            suggestions.append({
                "label": LABEL, "post_id": post["id"], "account_id": post["account_id"],
                "platform": post["platform"], "campaign": post["campaign"] or None, "posted_at": post["posted_at"],
                "remote_url": post["remote_url"] or None, "rule_id": rule["id"], "kind": rule["kind"],
                "metric": rule["metric"], "threshold": rule["threshold"], "value": hit["value"],
                "value_status": "exact", "observed_at": hit["observed_at"].isoformat(timespec="seconds"),
                "source": hit["source"], "window": ({"start": hit["window_start"], "end": hit["window_end"]}
                                                   if hit["window_start"] else None),
                "snapshot_age_hours": _hours(now - hit["observed_at"]),
                "hours_after_publish": _hours(hit["observed_at"] - posted) if posted else None,
                "within_hours": rule["within_hours"], "manual_only": manual,
                "cross_post_targets": targets if rule["kind"] == "cross_post" else None,
                "mentions_withheld_by_optout": withheld, "suggestion": brief, "next_commands": commands})
    counts["suggestions"] = len(suggestions)  # newest post first (the SELECT order), rules in file order
    return {"schema_version": OUTPUT_SCHEMA, "generated_at": now.isoformat(timespec="seconds"), "label": LABEL,
            "filters": {"account": account_id, "since": since_utc}, "counts": counts,
            "suggestions": suggestions, "skipped": skipped, "accounts_error": accounts_error,
            "hard_rules": list(HARD_RULES),
            "note": ("Read-only: the database was opened mode=ro and nothing was drafted, approved, scheduled, "
                     "sent, liked, replied to or messaged. Each suggestion is a brief for a human-reviewed draft.")}


def _derived_lines(info: dict | None) -> list[str]:
    if not info:
        return []
    lines = ["Thresholds derived from your own results (no --rules given): 'took off' = "
             f"{info['multiplier']} x your median {info['metric']} about {info['post_age_hours']} h after posting."]
    lines += [f"- {a['account_id']} ({a['platform']}): {a['text']}" for a in info["accounts"]]
    return lines


TOO_LITTLE_DATA = (
    "Not enough of your own results yet to say what 'took off' means for you.",
    "Either pass --rules FILE with your own thresholds (examples/followup-rules.example.json), or record results "
    "for at least 10 published posts per account about 7 days after posting:",
)


def render_too_little(report: dict) -> str:
    """Text for a derived-thresholds run that found no account with enough results (nothing to compare)."""
    from .insights import HOW_TO_GET_DATA
    lines = list(TOO_LITTLE_DATA) + [f"  - {item}" for item in HOW_TO_GET_DATA]
    accounts = (report.get("derived_thresholds") or {}).get("accounts", [])
    if accounts:
        lines += ["", "Per account:"] + [f"- {a['account_id']} ({a['platform']}): {a['text']}" for a in accounts]
    lines += ["", report["note"]]
    return "\n".join(lines)


def render_text(report: dict) -> str:
    counts = report["counts"]
    lines = [f"Follow-up suggestions ({LABEL})",
             f"{counts['suggestions']} suggestion(s) from {counts['posts_considered']} published post(s) and "
             f"{counts['rules']} rule(s); below threshold {counts['below_threshold']}, no exact value "
             f"{counts['no_exact_value']}, no snapshot {counts['no_snapshot']}, human-only skipped "
             f"{counts['human_only_platform']}, already suggested {counts['already_suggested']}."]
    lines += _derived_lines(report.get("derived_thresholds"))
    for item in report["suggestions"]:
        age = item["snapshot_age_hours"]
        lines += ["", f"- {item['post_id']} [{item['platform']} / {item['account_id']}] rule {item['rule_id']} "
                      f"({item['kind']}{', manual only' if item['manual_only'] else ''}): {item['metric']} "
                      f"{item['value']} >= {item['threshold']}, observed {item['observed_at']} "
                      f"({age} h ago, source {item['source']})",
                  f"  Suggestion: {item['suggestion']}"]
        lines += [f"  Next: {command}" for command in item["next_commands"]]
    if report.get("accounts_error"):
        lines += ["", f"Accounts config not used: {report['accounts_error']}"]
    if report["skipped"]:
        lines += ["", "Skipped:"] + [f"- {s['post_id']} {s['rule_id'] or ''} {s['reason']}".replace("  ", " ")
                                     for s in report["skipped"][:50]]
    if "state_recorded" in report:
        lines += ["", f"Recorded {report['state_recorded']} new post/rule pair(s) in the state file."]
    lines += ["", "Hard rules:"] + [f"- {rule}" for rule in HARD_RULES]
    lines += ["", report["note"]]
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI

def add_parser(sub) -> None:
    """``followup-suggest`` (called from ``cli_parser.make_parser``)."""
    p = sub.add_parser("followup-suggest", help="Read-only: when a published post's exact metric crosses an owner-set "
                       "threshold, suggest a follow-up for human review (brief + next commands); never likes, "
                       "replies, DMs, reposts, drafts or sends")
    p.add_argument("--rules", type=Path, metavar="FILE",
                   help="Owner rules JSON (see examples/followup-rules.example.json). Default: one 'took off' rule "
                        "per account derived from your own results (2 x your median likes + replies + reposts at "
                        "about 7 days, at least 10 posts with a result)")
    p.add_argument("--account", metavar="ID", help="Only posts of this account")
    p.add_argument("--since", metavar="DATE",
                   help="Only posts published at or after this ISO 8601 time with timezone (e.g. 2026-10-01T00:00:00Z)")
    p.add_argument("--state", type=Path, metavar="FILE",
                   help="Private dedupe file of post/rule pairs already suggested (read if present; skipped pairs "
                        "are not suggested again)")
    p.add_argument("--record", action="store_true",
                   help="With --state: record this run's suggestions in that file (atomic, mode 0600; ids only)")
    p.add_argument("--optout", type=Path,
                   help="Opt-out list JSON (default $REACHWEAVER_OPTOUT or config/optout.json if present); listed "
                        "handles are never named in a suggestion")
    p.add_argument("--now", help="ISO 8601 time with timezone for snapshot ages (default: current UTC time)")
    p.add_argument("--json", action="store_true", help="Machine-readable report")


def run_cli(args) -> int:
    """Dispatch for ``followup-suggest``: database mode=ro only; only ``--record`` writes (the ``--state`` file)."""
    from .accounts import load_accounts
    from .optout import load_optout
    from .storage import connect_readonly, database_path, schema_gaps
    if args.record and args.state is None:
        raise ValidationError("--record requires --state FILE")
    if args.account is not None and not ACCOUNT.fullmatch(args.account):
        raise ValidationError("--account must be an account id (lowercase letters, digits and '-')")
    now = _dt(args.now) if args.now else datetime.now(timezone.utc)
    if args.since:
        parse_utc(args.since)
    rules = load_rules(args.rules) if args.rules is not None else None
    state = load_state(args.state)
    optout = load_optout(args.optout)
    accounts, accounts_error = None, None
    if rules is not None and any(rule["kind"] == "cross_post" for rule in rules):
        try:
            accounts = load_accounts(args.accounts)
        except ValidationError as exc:
            accounts_error = str(exc)
    database = database_path(args.db)
    derived = None
    with connect_readonly(database) as db:
        gaps = schema_gaps(db, ("posts", "metric_snapshots", "event_log"))
        if gaps:
            raise ValidationError(f"{database} predates this version's schema (missing {', '.join(gaps)}); "
                                  "followup-suggest is read-only and never migrates. Run 'reachweaver init' once "
                                  "to upgrade it, then retry")
        if rules is None:  # no --rules: thresholds derived from this account's own results
            rules, derived = derive_rules(db, now=now, account_id=args.account)
        report = None
        if rules:
            report = build_suggestions(db, rules, now=now, account_id=args.account, since=args.since,
                                       optout=optout, accounts=accounts, accounts_error=accounts_error, state=state)
    if report is None:  # derived mode with too little data: a friendly next step, not an error
        report = {"schema_version": OUTPUT_SCHEMA, "generated_at": now.isoformat(timespec="seconds"),
                  "label": LABEL, "status": "too_little_data", "derived_thresholds": derived,
                  "suggestions": [], "message": " ".join(TOO_LITTLE_DATA),
                  "note": "Read-only: the database was opened mode=ro and nothing was drafted, approved, "
                          "scheduled or sent."}
        if args.record:
            report["state_recorded"] = 0
        print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_too_little(report))
        return 0
    if derived is not None:
        report["derived_thresholds"] = derived
    if args.record:
        report["state_recorded"] = write_state(args.state, state, report["suggestions"], now)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render_text(report))
    return 0
