"""Guided review loop (#11): ``reachweaver review`` walks the drafts one at a time in the owner's terminal.

Owner-approved 2026-10-21 with the recommended answers D1-D5 of the review-loop security proposal.
It adds no approval logic of its own; it only removes hand-copied post ids:

* D1/D2: both stdin and stdout must be terminals and ``/dev/tty`` must open, checked before anything
  is shown; every answer is read from the controlling terminal (tty(4), the same pattern as
  ``confirmation.py``), never from stdin, so piping cannot answer. Like the D6 confirmation this is
  friction, not a security boundary: a local process can drive a pseudo-terminal.
* For each draft, oldest first, it runs the real ``show`` and ``check`` commands (the same output the
  owner gets by hand), then asks: ``a`` approve, ``s`` skip, ``c`` cancel, ``q`` quit.
* D3: ``a`` runs the real ``approve`` command for that one post id, through the same parser and
  ``execute`` as a typed ``approve POST_ID``: the same pre-checks (persona, opt-out, private links,
  media, repeat guard with no ``--allow-repeat``, reply target, identity binding when latched), the
  same ``transition(..., "approve")`` and, with ``approval-mode --confirmation tty_hash_prefix``, the
  same typed hash prefix for every post. One answer approves one post; there is no batch and no
  default answer. The post must be byte-for-byte the row that was shown, or it is not approved.
* D4: ``c`` runs the real ``cancel`` command after the owner types ``CANCEL``.
* D5: reply drafts (``draft --reply-to``) are left out; they are approved one by one with ``approve``.
* At the end it prints the exact ids approved, skipped, cancelled, refused and not reached, and the
  next commands (a ``publish --post-id`` dry run, or ``schedule``).

Owner-only: the command catalog tags it ``owner_only`` and ``interactive``, the plugin's agent guard
refuses it, and the MCP server never exposes it.
"""
from __future__ import annotations

import argparse
import sys
from typing import Callable

from . import confirmation
from .policy import ValidationError

REQUIRES_TTY = "review_requires_tty"
TTY_UNAVAILABLE = "review_tty_unavailable"
MAX_INVALID_ANSWERS = 3
ANSWERS = {"a": "approve", "approve": "approve", "s": "skip", "skip": "skip",
           "c": "cancel", "cancel": "cancel", "q": "quit", "quit": "quit"}
CANCEL_WORD = "CANCEL"
CHECK_FLAGS = ("persona", "optout", "sources")


def require_terminal(stdin=None, stdout=None) -> None:
    """Refuse unless stdin and stdout are terminals (D2), before anything is read or shown."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        interactive = bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        raise ValidationError(f"{REQUIRES_TTY}: review is owner-only and needs an interactive terminal on stdin and "
                              "stdout; nothing was shown or approved. Run it in your own terminal, or approve one "
                              "post at a time: reachweaver show POST_ID, then reachweaver approve POST_ID")


def _default_tty():
    return confirmation._DevTty()  # looked up at call time, so tests can replace it


def ask(open_tty: Callable, prompt: str) -> str | None:
    """Write ``prompt`` to the controlling terminal and read one line from it; None at end of input."""
    tty = open_tty()
    try:
        tty.write(prompt)
        line = tty.readline()
    finally:
        tty.close()
    return None if line is None else line.strip()


def ask_choice(open_tty: Callable, post_id: str) -> str:
    """approve, skip, cancel or quit. No default: an empty or unknown answer asks again; end of input quits."""
    for _ in range(MAX_INVALID_ANSWERS):
        line = ask(open_tty, f"Post {post_id}: [a]pprove, [s]kip, [c]ancel this post, [q]uit? ")
        if line is None:
            return "quit"
        choice = ANSWERS.get(line.lower())
        if choice is not None:
            return choice
        say(open_tty, "Type a, s, c or q, then Enter.\n")
    print(f"No valid answer for {post_id}; skipped, nothing changed")
    return "skip"


def say(open_tty: Callable, text: str) -> None:
    tty = open_tty()
    try:
        tty.write(text)
    finally:
        tty.close()


def _global_dests(parser: argparse.ArgumentParser) -> list[str]:
    return [a.dest for a in parser._actions
            if a.option_strings and not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))]


def command_args(make_parser: Callable, review_args, argv: list[str]):
    """Parse ``argv`` exactly as a typed command, then carry over the review run's global options."""
    parser = make_parser()
    parsed = parser.parse_args(argv)
    for dest in _global_dests(parser):
        setattr(parsed, dest, getattr(review_args, dest, None))
    return parsed


def _flag_argv(args, names) -> list[str]:
    argv: list[str] = []
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            argv.append(f"--{name}={value}")  # one word, so a value starting with '-' stays a value
    return argv


def approve_argv(args, post_id: str) -> list[str]:
    """The exact ``approve`` command this answer runs. Never ``--allow-repeat``."""
    argv = ["approve", post_id, *_flag_argv(args, ("approver", *CHECK_FLAGS))]
    if getattr(args, "verify_identity", False):
        argv += ["--verify-identity", "--online"]
    return argv


def _drafts(args) -> tuple[list[str], list[str]]:
    """(draft ids in the loop, oldest first; reply draft ids left out)."""
    from .storage import connect_readonly, database_path, reply_target_text
    query, params = "SELECT * FROM posts WHERE status='draft'", []
    if args.campaign is not None:
        query += " AND campaign=?"
        params.append(args.campaign)
    if args.account is not None:
        query += " AND account_id=?"
        params.append(args.account)
    with connect_readonly(database_path(args.db)) as db:
        rows = db.execute(query + " ORDER BY created_at, rowid", params).fetchall()
    queue = [row["id"] for row in rows if reply_target_text(row) is None]
    replies = [row["id"] for row in rows if reply_target_text(row) is not None]
    return queue, replies


def _row(args) -> Callable[[str], dict | None]:
    from .storage import connect_readonly, database_path

    def read(post_id: str) -> dict | None:
        with connect_readonly(database_path(args.db)) as db:
            row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return None if row is None else dict(row)
    return read


def _run_step(execute: Callable, parsed) -> tuple[int | None, str | None]:
    """Run one real command; (exit code, None) or (None, the refusal text). Other errors propagate."""
    from .privacy import redact
    from .publishers import PublishError
    try:
        return execute(parsed), None
    except (ValidationError, PublishError, OSError, ValueError) as exc:
        return None, redact(str(exc))


def run(args, *, execute: Callable, make_parser: Callable, stdin=None, stdout=None,
        open_tty: Callable | None = None) -> int:
    """The loop. 0 when nothing was refused, 1 when an approval or cancellation was refused, 130 on Ctrl-C."""
    require_terminal(stdin, stdout)
    open_tty = open_tty or _default_tty
    try:
        open_tty().close()
    except OSError:
        raise ValidationError(f"{TTY_UNAVAILABLE}: cannot open {confirmation.TTY_PATH} (no controlling terminal); "
                              "nothing was shown or approved") from None
    queue, replies = _drafts(args)
    read = _row(args)
    done: dict[str, list[str]] = {"approved": [], "skipped": [], "cancelled": [], "refused": [], "not reached": []}
    scope = "".join([f" in campaign {args.campaign}" if args.campaign is not None else "",
                     f" for account {args.account}" if args.account is not None else ""])
    print(f"Review: {len(queue)} draft(s){scope}, oldest first. One answer acts on one post; nothing is sent.")
    try:
        for index, post_id in enumerate(queue, 1):
            outcome = _review_one(args, post_id, index, len(queue), execute, make_parser, open_tty, read)
            if outcome == "quit":
                done["not reached"] = queue[index - 1:]
                break
            done[outcome].append(post_id)
    except KeyboardInterrupt:  # the summary still names every id by its real state
        reviewed = {pid for ids in done.values() for pid in ids}
        for pid in queue:
            if pid not in reviewed:
                status = (read(pid) or {}).get("status")
                done[status if status in ("approved", "cancelled") else "not reached"].append(pid)
        print("\nInterrupted.")
        _summary(done, replies)
        return 130
    _summary(done, replies)
    return 1 if done["refused"] else 0


def _review_one(args, post_id: str, index: int, total: int, execute, make_parser, open_tty, read) -> str:
    shown = read(post_id)
    if shown is None or shown["status"] != "draft":
        print(f"\n[{index}/{total}] {post_id} is no longer a draft; skipped")
        return "skipped"
    print(f"\n[{index}/{total}] Post {post_id}: account {shown['account_id']} ({shown['platform']}), "
          f"campaign {shown['campaign'] or '-'}")
    for argv in (["show", post_id], ["check", post_id, *_flag_argv(args, CHECK_FLAGS)]):
        code, refusal = _run_step(execute, command_args(make_parser, args, argv))
        if refusal is not None:
            print(f"{argv[0]} could not run ({refusal}); skipped, nothing changed")
            return "skipped"
    choice = ask_choice(open_tty, post_id)
    if choice in ("quit", "skip"):
        return choice if choice == "quit" else "skipped"
    if choice == "cancel":
        typed = ask(open_tty, f"Type {CANCEL_WORD} to cancel post {post_id} (it will never be sent): ")
        if typed != CANCEL_WORD:
            print(f"Not cancelled; {post_id} skipped, nothing changed")
            return "skipped"
    if read(post_id) != shown:  # the exact row that was shown, or nothing
        print(f"{post_id} changed while you were reviewing it; nothing changed. Review it again")
        return "skipped"
    argv = approve_argv(args, post_id) if choice == "approve" else ["cancel", post_id]
    _, refusal = _run_step(execute, command_args(make_parser, args, argv))
    status = (read(post_id) or {}).get("status")
    wanted = "approved" if choice == "approve" else "cancelled"
    if status == wanted:
        return wanted
    print(f"NOT {wanted}: {post_id} is {status or 'missing'}" + (f" ({refusal})" if refusal else ""))
    return "refused"


def _summary(done: dict[str, list[str]], replies: list[str]) -> None:
    print("\nReview summary: " + ", ".join(f"{len(ids)} {name}" for name, ids in done.items()))
    for name, ids in done.items():
        if ids:
            print(f"  {name}: {' '.join(ids)}")
    if replies:
        print(f"  reply drafts not in this loop: {' '.join(replies)} (show and approve each one by hand)")
    if done["approved"]:
        print("Next (nothing is sent yet): a dry run, or a due time with its offset, for each approved post:")
        for post_id in done["approved"]:
            print(f"  reachweaver publish --post-id {post_id}    or    reachweaver schedule {post_id} --at TIME")
