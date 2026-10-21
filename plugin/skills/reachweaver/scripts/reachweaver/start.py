"""``reachweaver start``: say where you are and print the exact next command. Read-only.

It looks at a few local things (the workspace marker, the accounts file, the database and its queue and metric
snapshots, the ideas inbox) and returns ordered steps. It never creates the database or any file, never runs a step
and never approves or sends: an approval or a live send stays a deliberate command the owner types. Steps are text
only. The easiest path for a first-time creator, the browser composer (``share --web``), comes first.
"""
from __future__ import annotations

import shlex
import sqlite3
from pathlib import Path

from . import home
from . import ideas as ideas_inbox
from .accounts import accounts_path as resolve_accounts_path
from .accounts import load_accounts, placeholder_accounts
from .policy import ValidationError
from .storage import database_path, is_paused, metric_snapshot_count, post_status_counts
from .ui_data import open_database

EXAMPLE_ACCOUNTS = "config/accounts.example.json"
SHARE_WEB = "reachweaver share --web --open"
STUDIO = "reachweaver studio --open"  # dashboard + composer in one process, linked to each other
SHARE_GUIDE = "reachweaver share --guide"
REVIEW = "reachweaver review"
REPORT = "reachweaver report --since 30d --format markdown"
REPORT_WEEK = "reachweaver report --week"
NEXT = "reachweaver next"
INSIGHTS = "reachweaver insights"
POSTED = "reachweaver posted LINK --likes N"


def _step(label: str, command: str | None = None, note: str | None = None) -> dict:
    return {"label": label, "command": command, "note": note}


def _posted_step() -> dict:
    return _step("Posted something yourself? Record it from its link, with its numbers", POSTED,
                 "Run it again later with new numbers to update them. Only writes a note on this computer.")


def _share_steps() -> list[dict]:
    return [_step("Easiest: open ReachWeaver in your browser: write a post, get a ready-to-paste version for each "
                  "network, and later see how it did", STUDIO,
                  "Runs only on your computer; you post it in each app yourself. No account, password or social "
                  "network is contacted. Only the composer: " + SHARE_WEB),
            _step("Or answer a few questions in the terminal instead", SHARE_GUIDE,
                  "Guided manual handoff; nothing is published.")]


def _shown_path(path: Path) -> str:
    """The path relative to the folder you are in when it is inside it (``config/accounts.json``), else as is."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except (ValueError, OSError):
        return str(path)


def _open_ideas(db: str | Path | None) -> int:
    """Open ideas in the inbox next to the database; 0 when there is none or it cannot be read (``idea list`` says why)."""
    try:
        return ideas_inbox.counts(ideas_inbox.inbox_path(db))["open"]
    except (ValidationError, OSError):
        return 0


def _placeholder_step(placeholders: list[str], accounts_file: Path) -> dict:
    return _step(f"{len(placeholders)} account(s) still use example placeholders — edit {_shown_path(accounts_file)}",
                 None, f"Put in your own handles and ids ('{home.command('account-add PLATFORM')}' fills the example entry "
                       "for you), or delete the accounts you do not use (" + ", ".join(placeholders)
                       + "). Tokens never go in this file.")


def _ideas_step(count: int) -> dict:
    return _step(f"{count} saved idea(s) in your inbox: pick one to write next", "reachweaver idea list --status open",
                 f"'{NEXT}' names each one with a command that opens the browser composer with that idea as your caption.")


def _hand_posting_guide(db: str | Path | None, database: Path) -> dict | None:
    """No accounts file, but you already post by hand (posts recorded in the database) or saved ideas: show your
    progress and the everyday loop instead of the first-time setup. None for a brand-new user."""
    from datetime import datetime, timezone

    from . import results
    open_ideas = _open_ideas(db)
    posts = drafts = 0
    due: list[dict] = []
    if database.is_file():
        try:
            with open_database(database) as con:
                for row in post_status_counts(con):
                    if row["status"] in ("published", "published_manual"):
                        posts += row["count"]
                    elif row["status"] == "draft":
                        drafts += row["count"]
                due = results.results_due(con, datetime.now(timezone.utc))
        except (ValidationError, sqlite3.Error):
            pass  # an unreadable database: the first-time steps below still apply
    if not (posts or open_ideas or drafts):
        return None
    facts = [f"{posts} post(s) recorded", f"{open_ideas} open idea(s)"] + ([f"{drafts} draft(s)"] if drafts else [])
    steps = [_step("What to post next: your ideas by name, each with a ready command" if open_ideas else
                   "What to do next: one short list of concrete steps", NEXT,
                   "Read-only; it also says how many days since you last posted on each network.")]
    if due:
        steps.append(_step(f"{len(due)} post(s) are ready for their 7-day numbers: copy them from the app's insights",
                           due[0]["command"], "Leave out any count you cannot see (unknown is not zero)."))
    if posts:
        steps.append(_step("How your posts did this week", REPORT_WEEK,
                           "A short summary at your terminal; exact numbers from your own records only."))
    steps.append(_posted_step())
    steps.append(_step("Write another post in your browser and post it by hand", SHARE_WEB,
                       "After posting, record it from its link with 'reachweaver posted' so it counts."))
    steps += [step for step in _no_accounts_steps() if step["command"] not in (SHARE_WEB, SHARE_GUIDE)]
    return {"state": "posting_by_hand", "summary": "You post by hand (no accounts file yet): " + ", ".join(facts) + ".",
            "steps": steps}


def _no_accounts_steps() -> list[dict]:
    steps = _share_steps()
    root = home.root()
    # With --accounts FILE, a workspace or a copy of the example makes config/accounts.json, not that file:
    # only the account-add step (which carries --accounts FILE) is offered.
    named = home.typed_accounts()
    if named is None and root is None:
        steps.append(_step("To post from ReachWeaver itself, make this folder a workspace first",
                           "reachweaver workspace --init .",
                           "Creates config/accounts.json (it asks which networks you use), state/, media/ and logs/ here. "
                           "It holds no secrets and contacts nothing."))
    elif named is None:
        steps.append(_step("Restore this workspace's accounts file (existing files are kept)",
                           f"reachweaver workspace --init {shlex.quote(str(root))}",
                           "Then edit config/accounts.json to list only accounts you control."))
    if named is None and Path(EXAMPLE_ACCOUNTS).is_file():  # only in a source checkout: never suggest copying a file that is not there
        steps.append(_step("Or create only the accounts file from the example in this folder",
                           f"cp {EXAMPLE_ACCOUNTS} config/accounts.json",
                           "Edit it to list only accounts you control. Tokens are never stored in it."))
    steps.append(_step("Or add only the one account you use (asks for public details, never a password)",
                       home.command("account-add bluesky"),
                       "Any of bluesky, mastodon, telegram, linkedin, instagram, threads, tiktok, youtube, facebook."))
    return steps


def _freshness_warnings() -> list[str]:
    """One line when bundled platform facts are aging, stale or a registry is invalid; never raises."""
    from . import freshness
    try:
        line = freshness.warning_line()
    except (OSError, ValueError) as exc:  # a broken bundle must not break the session guide
        return [f"WARNING: could not check research registry freshness: {exc}"]
    return [line + " -- run 'reachweaver registry-age'"] if line else []


def _insight_step(con) -> dict | None:
    """The strongest "what works for you" line from the owner's own matched-age results, if any."""
    from .insights import top_insights
    try:
        lines = top_insights(con, limit=1)
    except (sqlite3.Error, ValidationError, ValueError):
        return None
    if not lines:
        return None
    return _step("What works for you: " + lines[0]["text"], INSIGHTS,
                 "From your own published posts; an observation to test, not a cause.")


def build_guide(db: str | Path | None, accounts_path: str | None) -> dict:
    """{"state", "summary", "steps", "warnings"}; ``state`` is a short code for tests and --json."""
    guide = _build_guide(db, accounts_path)
    guide["warnings"] = _freshness_warnings()
    return guide


def _build_guide(db: str | Path | None, accounts_path: str | None) -> dict:
    try:
        accounts = load_accounts(accounts_path)
        accounts_error = None
    except ValidationError as exc:
        accounts, accounts_error = {}, str(exc)
    database = database_path(db)
    if accounts_error is not None and accounts_error.startswith("Missing "):
        progress = _hand_posting_guide(db, database)
        if progress is not None:
            return progress
        return {"state": "no_accounts_file", "summary": "No accounts file yet.", "steps": _no_accounts_steps()}
    if accounts_error is not None:  # the file exists but cannot be used: never suggest overwriting it
        return {"state": "accounts_invalid", "summary": f"The accounts file cannot be used: {accounts_error}",
                "steps": [_step("Fix the accounts file, then run 'reachweaver doctor' to check it", "reachweaver doctor",
                                "Compare it with config/accounts.example.json; do not overwrite your file.")]}
    accounts_file = resolve_accounts_path(accounts_path)
    placeholders = placeholder_accounts(accounts)
    real = [account_id for account_id in accounts if account_id not in placeholders]
    configured = f"{len(real)} account(s) configured" + (
        f", {len(placeholders)} with example placeholders" if placeholders else "")
    open_ideas = _open_ideas(db)
    if not database.is_file():
        setup = _share_steps()
        if placeholders:
            setup.append(_placeholder_step(placeholders, accounts_file))
        if open_ideas:
            setup.append(_ideas_step(open_ideas))
        setup += [_step("Create the local database (no account is contacted)", "reachweaver init"),
                  _step("See which credential variables are missing", "reachweaver doctor")]
        return {"state": "no_database", "summary": f"{configured}; no local database yet.", "steps": setup}
    try:
        with open_database(database) as con:
            counts: dict[str, int] = {}
            for row in post_status_counts(con):
                counts[row["status"]] = counts.get(row["status"], 0) + row["count"]
            paused = is_paused(con)
            snapshots = metric_snapshot_count(con)
            insight = _insight_step(con) if snapshots else None
    except ValidationError as exc:
        return {"state": "database_unreadable", "summary": str(exc),
                "steps": [_step("Upgrade or recreate the local database", "reachweaver init")]}
    steps: list[dict] = []
    if paused:
        steps.append(_step("The emergency stop is ON, so nothing will send", "reachweaver resume",
                           "Only resume deliberately, when you know why it was paused."))
    if counts.get("needs_review"):
        steps.append(_step(f"{counts['needs_review']} send(s) ended in an unknown state: check the native platform "
                           "first, then record what you found", "reachweaver dead-letter",
                           "Never resend blindly; use reconcile with the evidence."))
    if counts.get("draft"):
        steps.append(_step(f"{counts['draft']} draft(s) waiting: go through them one by one in your own terminal",
                           REVIEW, "Shows each draft's exact text and checks; you decide each one and nothing is sent. "
                                   "To only look: 'reachweaver list --status draft'."))
    if counts.get("approved") or counts.get("scheduled"):
        steps.append(_step("Approved work is queued: preview what a send would do (dry run, no network)",
                           "reachweaver publish"))
    if not any(counts.get(k) for k in ("draft", "approved", "scheduled", "needs_review")):
        if real:
            steps.append(_step("Nothing queued: write a draft", f"reachweaver draft --account {real[0]} --text 'Your text'"))
            steps.append(_step("Or write it in your browser and post it by hand", SHARE_WEB))
        else:
            steps.append(_step("Nothing queued: write a post in your browser and post it by hand", SHARE_WEB))
    if placeholders:
        steps.append(_placeholder_step(placeholders, accounts_file))
    if open_ideas:
        steps.append(_ideas_step(open_ideas))
    if counts.get("published") or counts.get("published_manual"):
        steps.append(_posted_step())
    if snapshots:
        steps.append(_step(f"See how your posts did ({snapshots} metric snapshot(s) recorded)", REPORT,
                           "Exact numbers from your own records only; a missing number stays unknown."))
    if insight:
        steps.append(insight)
    steps.append(_step("Browse everything in a read-only dashboard", "reachweaver ui"))
    total = sum(counts.values())
    return {"state": "needs_review" if counts.get("needs_review") else "paused" if paused else
            "drafts" if counts.get("draft") else "queued" if (counts.get("approved") or counts.get("scheduled"))
            else "empty" if total == 0 else "idle",
            "summary": f"{total} post(s): " + (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items()))
                                               or "none yet") + (". PAUSED." if paused else "."),
            "steps": steps}


def render(guide: dict) -> str:
    lines = [guide["summary"], *guide.get("warnings", []), ""]
    for number, step in enumerate(guide["steps"], 1):
        lines.append(f"{number}. {step['label']}")
        if step["command"]:
            lines.append(f"     {step['command']}")
        if step["note"]:
            lines.append(f"     {step['note']}")
    lines += ["", "This command only reads local state; it runs nothing for you.", "", *everyday_footer()]
    return "\n".join(lines)


# The everyday loop in five verbs, shown under every `start` and bare `reachweaver` (2026-10-21 UX re-measure: a
# fresh folder's screen never named posted, report, idea or next, so people needed --help first). Each command is
# also in the --help everyday list (cli_help.EVERYDAY); tests parse every one.
EVERYDAY = (
    ("write", STUDIO, f"or in the terminal: {SHARE_GUIDE}"),
    ("record", POSTED, None),
    ("results", REPORT_WEEK, None),
    ("ideas", 'reachweaver idea "text"', None),
    ("next", NEXT, None),
)


def everyday_footer() -> list[str]:
    """At most six lines: a heading that points at --help, then one verb and its command per line."""
    width = max(len(verb) for verb, _, _ in EVERYDAY) + 2
    lines = ["Everyday (more: reachweaver --help; every command: reachweaver --help all):"]
    for verb, command, extra in EVERYDAY:
        lines.append(f"  {verb.ljust(width)}{command}" + (f"   ({extra})" if extra else ""))
    return lines
