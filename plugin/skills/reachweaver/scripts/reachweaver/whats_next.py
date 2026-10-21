"""``reachweaver next``: "What should I post next?" -- at most five concrete things to do, in plain language.

Read-only. It looks at the ideas inbox and, when it exists, the local database (opened ``mode=ro`` + query-only,
never created or migrated) and returns ranked actions, each with ONE command to type. Nothing is run, drafted,
approved, scheduled or sent; a command that would change something stays a command the person types.

Fixed order (a rule, not a prediction of reach):

1. ``safety``: a send that ended in an unknown state, a stalled send, an approval to redo, a missed schedule or
   the emergency stop. Shown first because nothing else should happen before it is understood.
2. ``drafts``: drafts waiting -> ``reachweaver review`` (the owner's own approval loop).
3. ``results_due``: a post 6..30 days old with no numbers from day 6 on -> the exact ``reachweaver metrics`` line.
4. ``idea``: each open idea BY NAME (oldest first) -> ``reachweaver share --platform <your usual networks>
   --text '<idea>'`` (a copy-ready package, never a send), or the browser composer for a long idea.
5. ``rhythm``: days since you last posted on each network you use.
6. ``what_works``: the strongest "what works for you" line from ``insights`` (an observation, not a cause).

With nothing at all yet it points at the browser composer, the ideas inbox and ``reachweaver posted LINK``
(record a post made by hand, with its numbers). Ideas fill the slots the other
kinds leave (at least one idea is always shown when there is one).
"""
from __future__ import annotations

import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import ideas as ideas_inbox
from . import results
from .policy import ValidationError

SCHEMA = "reachweaver.next.v1"
MAX_ACTIONS = 5
KINDS = ("safety", "drafts", "results_due", "idea", "rhythm", "what_works", "first_post", "save_idea", "record_post")
IDEA_TEXT_IN_COMMAND = 280  # a longer idea goes into the browser composer instead of a long command line
IDEA_TITLE = 60
SHARE_WEB = "reachweaver share --web --open"
POSTED = "reachweaver posted LINK --likes N"
STALE_DAYS = 7


def _action(kind: str, text: str, command: str | None, note: str | None = None, **evidence) -> dict:
    return {"kind": kind, "text": text, "command": command, "note": note, "evidence": evidence}


def _shareable(platforms: list[str]) -> list[str]:
    """Usual networks that ``share --platform`` accepts and that are not human-only (those get notes only)."""
    from .share import _human_only, available
    allowed, human = set(available()), _human_only()
    return [p for p in platforms if p in allowed and p not in human]


def _idea_actions(open_ideas: list[dict], networks: list[str]) -> list[dict]:
    out = []
    for item in open_ideas:
        text = " ".join(item["text"].split())
        title = results.first_words(text, IDEA_TITLE)
        if networks and len(text) <= IDEA_TEXT_IN_COMMAND and not results.has_control(text):
            command = f"reachweaver share --platform {','.join(networks)} --text {shlex.quote(text)}"
            note = ("Makes a copy-ready version for " + ", ".join(results.platform_name(p) for p in networks)
                    + " that you paste and post yourself; nothing is sent.")
        else:
            command = SHARE_WEB
            note = ("Paste the idea into the composer in your browser; it makes a ready-to-paste version per "
                    "network. Nothing is sent.")
        out.append(_action("idea", f"Write a post from your idea \"{title}\"", command, note, idea_id=item["id"]))
    return out


def _safety_actions(con: sqlite3.Connection, now: datetime) -> list[dict]:
    from .storage import queue_health
    health = queue_health(con, now=now.isoformat(timespec="seconds"))
    out = []
    for key, text, command in (
            ("ambiguous_needs_review", "{n} send(s) ended in an unknown state: check the platform first, then "
                                       "record what you found", "reachweaver dead-letter"),
            ("stale_sending", "{n} send(s) look stalled: check the platform before touching them",
             "reachweaver queue-health"),
            ("approval_rebind_required", "{n} approval(s) no longer match their post: review them again",
             "reachweaver review"),
            ("overdue_scheduled", "{n} scheduled post(s) missed their time: decide whether each still makes sense",
             "reachweaver list --status scheduled")):
        count = len(health.get(key) or [])
        if count:
            out.append(_action("safety", text.format(n=count), command, "Never resend blindly.",
                               signal=key, count=count))
    if health.get("paused"):
        out.append(_action("safety", "The emergency stop is ON, so nothing will send", "reachweaver queue-health",
                           "Resume only deliberately, when you know why it was paused.", signal="paused", count=1))
    return out


def _rhythm_action(last: dict[str, datetime], now: datetime, networks: list[str]) -> dict | None:
    if not last:
        return None
    days = {platform: max(0, int((now - moment).total_seconds() // 86400)) for platform, moment in last.items()}
    order = sorted(days, key=lambda p: (-days[p], p))
    parts = [f"{results.platform_name(p)} " + ("today" if days[p] == 0 else "yesterday" if days[p] == 1
                                                else f"{days[p]} days ago") for p in order[:5]]
    stalest = order[0]
    text = "Days since you last posted: " + ", ".join(parts)
    if days[stalest] >= STALE_DAYS and stalest in _shareable([stalest]):
        command = SHARE_WEB
        note = f"It has been {days[stalest]} days on {results.platform_name(stalest)}; a short post keeps it alive."
    else:
        command, note = None, None
    return _action("rhythm", text, command, note, days_since_last_post=dict(sorted(days.items())),
                   usual_networks=networks)


def _what_works(con: sqlite3.Connection, now: datetime) -> dict | None:
    from .insights import top_insights
    try:
        lines = top_insights(con, limit=1, now=now)
    except (ValidationError, sqlite3.Error, ValueError):
        return None
    if not lines:
        return None
    line = lines[0]
    return _action("what_works", "What works for you: " + line["text"], "reachweaver insights",
                   "From your own posts: an observation to test, not a cause.",
                   platform=line["platform"], account_id=line["account_id"], n=line["n"])


def _open_ideas(path: Path) -> tuple[list[dict], str | None]:
    try:
        return ideas_inbox.listing(path, status="open"), None
    except (ValidationError, OSError) as exc:
        return [], f"ideas inbox skipped: {exc}"


def build_next(db: str | Path | None, *, now: datetime | None = None, limit: int = MAX_ACTIONS) -> dict:
    """The ranked view (schema ``reachweaver.next.v1``). Never creates or writes anything."""
    from .storage import connect_readonly, database_path, schema_gaps
    if type(limit) is not int or not 1 <= limit <= MAX_ACTIONS:
        raise ValidationError(f"next shows 1..{MAX_ACTIONS} actions")
    current = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    open_ideas, problem = _open_ideas(ideas_inbox.inbox_path(db))
    if problem:
        warnings.append(problem)
    database = database_path(db)
    others: list[dict] = []
    networks: list[str] = []
    counts = {"open_ideas": len(open_ideas), "posts_recorded": 0, "drafts": 0, "results_due": 0}
    if database.is_file():
        try:
            with connect_readonly(database) as con:
                gaps = schema_gaps(con, ("posts", "metric_snapshots"))
                if gaps:
                    warnings.append(f"the database predates this version (missing {', '.join(gaps)}); run "
                                    "'reachweaver init' once to upgrade it")
                else:
                    others, networks, counts = _from_database(con, current, counts)
        except (ValidationError, sqlite3.Error) as exc:
            warnings.append(f"database skipped: {exc}")
    actions = _combine(others, _idea_actions(open_ideas, networks), limit)
    if not actions:
        actions = [_action("first_post", "Write a post in your browser and get a ready-to-paste version for each "
                                         "network", SHARE_WEB, "Runs only on your computer; you post it yourself."),
                   _action("save_idea", "Or save an idea to post about later", "reachweaver idea add 'Your idea'"),
                   _action("record_post", "Posted something yourself? Record it from its link, with its numbers",
                           POSTED, "Only writes a note on this computer; reports and this list then count it.")]
    for rank, item in enumerate(actions, 1):
        item["rank"] = rank
    return {"schema": SCHEMA, "generated_at": current.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "actions": actions, "counts": counts, "usual_networks": networks, "warnings": warnings,
            "read_only": True,
            "note": "Read-only: nothing was drafted, approved, scheduled or sent. Each command is for you to type."}


def _from_database(con: sqlite3.Connection, now: datetime, counts: dict) -> tuple[list[dict], list[str], dict]:
    from .storage import post_status_counts
    by_status: dict[str, int] = {}
    for row in post_status_counts(con):
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["count"]
    counts = {**counts, "posts_recorded": sum(by_status.get(s, 0) for s in ("published", "published_manual")),
              "drafts": by_status.get("draft", 0)}
    networks = _shareable(results.usual_networks(con, now))
    out = _safety_actions(con, now)
    if counts["drafts"]:
        out.append(_action("drafts", f"{counts['drafts']} draft(s) waiting for you: go through them one by one",
                           "reachweaver review", "You decide each one in your own terminal; nothing is sent by "
                                                 "looking.", count=counts["drafts"]))
    due = results.results_due(con, now)
    counts["results_due"] = len(due)
    if due:
        first = due[0]
        more = f" (and {len(due) - 1} more: 'reachweaver report --week' lists them)" if len(due) > 1 else ""
        out.append(_action(
            "results_due",
            f"Add the 7-day numbers of your {results.platform_name(first['platform'])} post "
            f"\"{first['first_words']}\" (posted {first['days_since_posting']} days ago){more}",
            first["command"], "Copy the counts from the app's insights; leave out any you cannot see (unknown is "
                              "not zero).", post_id=first["post_id"], posts_due=len(due)))
    rhythm = _rhythm_action(results.last_posted(con), now, networks)
    if rhythm is not None:
        out.append(rhythm)
    works = _what_works(con, now)
    if works is not None:
        out.append(works)
    return out, networks, counts


def _combine(others: list[dict], idea_actions: list[dict], limit: int) -> list[dict]:
    """Fixed order; ideas fill what is left, at least one idea when there is one (dropping the last extras)."""
    first = [a for a in others if a["kind"] in ("safety", "drafts", "results_due")]
    later = [a for a in others if a["kind"] not in ("safety", "drafts", "results_due")]
    room = max(0, limit - len(first) - len(later))
    ideas = idea_actions[:max(room, 1 if idea_actions else 0)]
    chosen = first + ideas + later
    while len(chosen) > limit and later:
        chosen.remove(later.pop())
    return chosen[:limit]


def render_text(view: dict) -> str:
    counts = view["counts"]
    head = []
    if counts["posts_recorded"]:
        head.append(f"{counts['posts_recorded']} post(s) recorded")
    if counts["open_ideas"]:
        head.append(f"{counts['open_ideas']} open idea(s)")
    lines = ["What to do next" + (f" ({', '.join(head)})" if head else ""), ""]
    for item in view["actions"]:
        lines.append(f"{item['rank']}. {item['text']}")
        if item["command"]:
            lines.append(f"     {item['command']}")
        if item["note"]:
            lines.append(f"     {item['note']}")
    lines += [*(f"Note: {w}" for w in view["warnings"]), ""]
    if not counts["posts_recorded"] and not any(item["kind"] == "record_post" for item in view["actions"]):
        lines.append("Posted something yourself? Record it from its link: " + POSTED)
    lines.append("Read-only: nothing was drafted, approved or sent. More: 'reachweaver report --week' (how your posts "
                 "did), 'reachweaver idea list' (all ideas).")
    return "\n".join(lines)
