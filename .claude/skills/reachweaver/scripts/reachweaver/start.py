"""``reachweaver start``: say where you are and print the exact next command. Read-only.

It looks at three local things (accounts file, database, queue) and returns ordered steps. It
never creates the database, never runs a step and never approves or sends: an approval or a live
send stays a deliberate command the owner types. Steps are text only.
"""
from __future__ import annotations

from pathlib import Path

from .accounts import load_accounts
from .policy import ValidationError
from .storage import database_path, is_paused, post_status_counts
from .ui_data import open_database

EXAMPLE_ACCOUNTS = "config/accounts.example.json"


def _step(label: str, command: str | None = None, note: str | None = None) -> dict:
    return {"label": label, "command": command, "note": note}


def build_guide(db: str | Path | None, accounts_path: str | None) -> dict:
    """{"state": ..., "summary": ..., "steps": [...]}; ``state`` is a short code for tests and --json."""
    try:
        accounts = load_accounts(accounts_path)
        accounts_error = None
    except ValidationError as exc:
        accounts, accounts_error = {}, str(exc)
    database = database_path(db)
    if accounts_error is not None and accounts_error.startswith("Missing "):
        return {"state": "no_accounts_file", "summary": "No accounts file yet.", "steps": [
            _step("Create your accounts file from the example (it holds no secrets)",
                  f"cp {EXAMPLE_ACCOUNTS} config/accounts.json",
                  "Edit it to list only accounts you control. Tokens are never stored in it."),
            _step("Or prepare a post for Instagram, TikTok and other networks without any account",
                  "reachweaver share --guide", "Guided manual handoff; nothing is published."),
            _step("Or just try the no-account preview", None, "docs/QUICKSTART.md walks through it with no network.")]}
    if accounts_error is not None:  # the file exists but cannot be used: never suggest overwriting it
        return {"state": "accounts_invalid", "summary": f"The accounts file cannot be used: {accounts_error}",
                "steps": [_step("Fix the accounts file, then run 'reachweaver doctor' to check it", "reachweaver doctor",
                                "Compare it with config/accounts.example.json; do not overwrite your file.")]}
    if not database.is_file():
        return {"state": "no_database", "summary": f"{len(accounts)} account(s) configured; no local database yet.",
                "steps": [_step("Prepare a manual social post with no database or password",
                                "reachweaver share --guide", "Saves an offline package to paste by hand."),
                          _step("Create the local database (no account is contacted)", "reachweaver init"),
                          _step("See which credential variables are missing", "reachweaver doctor")]}
    try:
        with open_database(database) as con:
            counts: dict[str, int] = {}
            for row in post_status_counts(con):
                counts[row["status"]] = counts.get(row["status"], 0) + row["count"]
            paused = is_paused(con)
    except ValidationError as exc:
        return {"state": "database_unreadable", "summary": str(exc),
                "steps": [_step("Upgrade or recreate the local database", "reachweaver init")]}
    steps: list[dict] = []
    first = next(iter(accounts), "ACCOUNT")
    if paused:
        steps.append(_step("The emergency stop is ON, so nothing will send", "reachweaver resume",
                           "Only resume deliberately, when you know why it was paused."))
    if counts.get("needs_review"):
        steps.append(_step(f"{counts['needs_review']} send(s) ended in an unknown state: check the native platform "
                           "first, then record what you found", "reachweaver dead-letter",
                           "Never resend blindly; use reconcile with the evidence."))
    if counts.get("draft"):
        steps.append(_step(f"{counts['draft']} draft(s) waiting: review the exact text and its checks",
                           "reachweaver list --status draft",
                           "Then 'show POST_ID' and 'check POST_ID'. Approving is your decision, in your terminal."))
    if counts.get("approved") or counts.get("scheduled"):
        steps.append(_step("Approved work is queued: preview what a send would do (dry run, no network)",
                           "reachweaver publish"))
    if not any(counts.get(k) for k in ("draft", "approved", "scheduled", "needs_review")):
        steps.append(_step("Nothing queued: write a draft", f"reachweaver draft --account {first} --text 'Your text'"))
    steps.append(_step("Browse everything in a read-only dashboard", "reachweaver ui"))
    total = sum(counts.values())
    return {"state": "needs_review" if counts.get("needs_review") else "paused" if paused else
            "drafts" if counts.get("draft") else "queued" if (counts.get("approved") or counts.get("scheduled"))
            else "empty" if total == 0 else "idle",
            "summary": f"{total} post(s): " + (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items()))
                                               or "none yet") + (". PAUSED." if paused else "."),
            "steps": steps}


def render(guide: dict) -> str:
    lines = [guide["summary"], ""]
    for number, step in enumerate(guide["steps"], 1):
        lines.append(f"{number}. {step['label']}")
        if step["command"]:
            lines.append(f"     {step['command']}")
        if step["note"]:
            lines.append(f"     {step['note']}")
    lines += ["", "This command only reads local state; it runs nothing for you."]
    return "\n".join(lines)
