"""The ``reachweaver`` argument parser: every subcommand, flag and help text (moved out of cli.py).

Only builds the parser; nothing here runs a command, reads a credential, opens the database or touches
the network. ``reachweaver.cli`` re-exports ``make_parser`` and dispatches the parsed arguments.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import confirmation, engage_scout, followups, insights, platform_risk, repeat_guard, review_evidence
from .account_add import PLATFORMS as ACCOUNT_ADD_PLATFORMS
from .account_advisor import FORMATS as ADVISOR_FORMATS
from .attribution import ATTRIBUTION_SOURCES, CAMPAIGN_TYPES
from .campaigns import OBJECTIVES
from .capabilities import DEFAULT_MAX_AGE_DAYS
from .claims import DEFAULT_DUPLICATE_THRESHOLD
from .distribution import GOALS
from .freshness import REGISTRY_FILES
from .journal import ALL_STATUSES as JOURNAL_STATUSES
from .manual_export import MANUAL_PLATFORMS
from .manual_package import generic_platforms
from .media import CREATORS
from .metrics_fetch import DEFAULT_MAX_POSTS, MAX_POSTS_LIMIT
from .pilot import DEFAULT_MIN_WEEKS
from .report_format import REPORT_FORMATS
from .results import HAND_ENTERED_SOURCE
from .schema_export import EXPORT_TYPES
from .schemas import TYPES as SCHEMA_TYPES
from .storage import METRIC_FIELDS, POST_STATUSES
from .tiktok import COMMERCIAL_CHOICES as TIKTOK_COMMERCIAL
from .tiktok import MODES as TIKTOK_MODES
from .tiktok import PRIVACY_LEVELS as TIKTOK_PRIVACY_LEVELS


def _add_history_args(p: argparse.ArgumentParser) -> None:
    """Opt-in, read-only repetition checks against a local DB (history.py, issue #10 S6)."""
    p.add_argument("--history-db", type=Path, metavar="PATH",
                   help="Also compare variants with this EXISTING local database's posts of the same account "
                        "(read-only; never created or modified). Warnings only. The global --db is not used here")
    p.add_argument("--history-days", type=int, metavar="N",
                   help="History look-back for history_near_duplicate, 1..365 (default 30)")
    p.add_argument("--history-window-days", type=int, metavar="W",
                   help="Rolling window for the history_frequency heuristic, 1..365 (default 7)")
    p.add_argument("--history-max-posts", type=int, metavar="K",
                   help="history_frequency warns at K existing posts per window, 1..1000 (default 7; "
                        "a ReachWeaver heuristic, not a platform limit)")


def _add_review_parsers(sub, optout_help: str) -> None:
    """``review-packet``, ``review-comment``, ``review-status`` (reachweaver/review_packet.py; draft, pending
    security review). The database is opened read-only; a comment is never approval."""
    p = sub.add_parser("review-packet", help="Offline: write a NEW private folder with the exact text, destination, "
                       "alt text, content digest and check findings of posts for a co-reviewer; never approves")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--post-id", action="append", metavar="ID", help="A stored post to include (repeatable)")
    which.add_argument("--campaign-staged", metavar="NAME",
                       help="Every draft, approved or scheduled post staged for this campaign id")
    p.add_argument("--out", type=Path, required=True, help="NEW folder (0700; files 0600); refuses an existing path. "
                   "Under <state>/review-packets/ review-status finds it without --packet")
    p.add_argument("--persona", metavar="ID", help="As for check: the persona these posts are for")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is a BLOCKED finding")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): a post linking into a private repository is refused and a private "
                        "source URL is redacted")
    p.add_argument("--json", action="store_true", help="Machine-readable result (folder, files, digests)")
    p = sub.add_parser("review-comment", help="Record a co-reviewer's comment in the packet's private comments.json, "
                       "bound to the post's content digest; never approves or changes a post")
    p.add_argument("--packet", type=Path, required=True, metavar="DIR", help="Folder written by review-packet")
    p.add_argument("--post-id", required=True, metavar="ID", help="A post in that packet")
    p.add_argument("--reviewer", required=True, metavar="NAME", help="Who commented (a label, not authentication)")
    p.add_argument("--verdict", required=True, choices=("looks_good", "change_requested", "question"),
                   help="looks_good is an opinion, not approval")
    p.add_argument("--note", required=True, metavar="TEXT", help="The comment (at most 2000 characters)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("review-status", help="Read-only: reviewer comments per post, STALE when the post changed "
                       "after the comment; never approves")
    p.add_argument("--packet", type=Path, metavar="DIR",
                   help="One packet (default: every packet under <state>/review-packets/)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")


def _add_idea_and_statusline_parsers(sub) -> None:
    """``idea`` (ideas inbox, reachweaver/ideas.py) and ``statusline`` (reachweaver/statusline.py)."""
    p = sub.add_parser("idea", help="Ideas inbox: capture 'post about this later' notes (add, list, drop) and promote "
                       "one to a NEW blocked campaign scaffold; never creates a draft, approval or send")
    actions = p.add_subparsers(dest="idea_action", required=True, metavar="ACTION")
    q = actions.add_parser("add", help="Add an open idea (text up to 2000 characters)")
    q.add_argument("text", help="The idea, in a sentence or two")
    q.add_argument("--source", help="A public https URL the idea comes from (not opened or checked)")
    q.add_argument("--tag", action="append", default=[], help="A lowercase tag; repeatable (at most 8)")
    q.add_argument("--json", action="store_true", help="Machine-readable result")
    q = actions.add_parser("list", help="List ideas, newest last")
    q.add_argument("--status", choices=("open", "promoted", "dropped"), help="Only ideas in this state")
    q.add_argument("--tag", help="Only ideas with this tag")
    q.add_argument("--json", action="store_true", help="Machine-readable result")
    q = actions.add_parser("drop", help="Mark an open idea dropped (kept in the file; only data-purge with "
                           "closed_ideas_days removes it)")
    q.add_argument("id", help="The idea id, e.g. idea-1a2b3c4d")
    q.add_argument("--json", action="store_true", help="Machine-readable result")
    q = actions.add_parser("promote", help="Write a NEW campaign scaffold folder from an open idea; it stays BLOCKED "
                           "until you write and verify it; nothing is staged, approved or sent")
    q.add_argument("id", help="The idea id")
    q.add_argument("--campaign", required=True, metavar="NAME", help="The new campaign_id (a lowercase slug)")
    q.add_argument("--out", required=True, type=Path, metavar="NEW_DIR",
                   help="A NEW folder (0700) for campaign.reachweaver.json and brief.md (0600); refuses an existing one")
    q.add_argument("--brand", type=Path, help="Optional brand JSON: sets brand_id; with --accounts one placeholder "
                                              "variant per approved account")
    q.add_argument("--accounts", dest="idea_accounts", type=Path, metavar="A",
                   help="Optional PUBLIC accounts config used with --brand")
    q.add_argument("--objective", choices=sorted(OBJECTIVES), default="awareness",
                   help="Campaign objective (default awareness)")
    q.add_argument("--json", action="store_true", help="Machine-readable result")
    sub.add_parser("statusline", help="One short read-only line for Claude Code's status line (version, drafts, "
                   "queued, needs review, ideas, PAUSED); never creates a database, reads no stdin, no network")


def _add_journal_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--post-id", help="Only this post")
    p.add_argument("--status", action="append", choices=JOURNAL_STATUSES, metavar="STATUS",
                   help="Include this status; repeatable (default: published, sending, needs_review)")
    p.add_argument("--all-statuses", action="store_true", help="Include drafts, approved and scheduled posts too")
    p.add_argument("--platform", help="Only this platform")
    p.add_argument("--account", help="Only this account id")
    p.add_argument("--since", metavar="DATE", help="From this date (YYYY-MM-DD, UTC) or ISO 8601 time with a timezone")
    p.add_argument("--until", metavar="DATE", help="Up to this date (YYYY-MM-DD, UTC, end of day) or ISO 8601 time")
    p.add_argument("--limit", type=int, default=100, metavar="N", help="Newest N entries (1..5000, default 100)")
    p.add_argument("--no-by-hand", action="store_true", help="Leave out material recorded with mark-shared")


class _TopParser(argparse.ArgumentParser):
    """Top-level parser: a mistyped command gets a short 'Did you mean' instead of a list of ~85 names.

    Abbreviated long options are refused (``allow_abbrev=False``): ``publish --liv`` must not
    parse as ``publish --live`` past an agent deny rule that matches the full spelling. Every
    subcommand parser is built from this class (add_subparsers defaults parser_class to it), so
    the setting reaches all of them.
    """

    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def parse_args(self, args=None, namespace=None):  # type: ignore[override]
        self._seen_argv = list(sys.argv[1:] if args is None else args)  # for the unknown-flag hint in error()
        return super().parse_args(args, namespace)

    def error(self, message):
        import re
        found = re.search(r"argument COMMAND: invalid choice: '([^']*)'", message)
        usage_of: argparse.ArgumentParser = self
        if found:
            action = _subparsers_action(self)
            close = _command_suggestions(found.group(1), list(action.choices))
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            message = f"unknown command '{found.group(1)}'.{hint} 'reachweaver --help all' lists every command by task"
        elif message.startswith("unrecognized arguments: "):
            message, usage_of = _unrecognized_hint(self, message)
        else:
            message = _invalid_choice_hint(message)
        usage_of.print_usage(sys.stderr)
        self.exit(2, f"{usage_of.prog}: error: {message}\n")


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


# Words people type for a job that has another name here. Only existing commands are suggested; `next` is left
# to `start`, which already answers "what do I do next".
COMMAND_ALIASES: dict[str, tuple[str, ...]] = {
    "post": ("share", "draft"), "write": ("share", "draft"), "compose": ("share", "draft"),
    "undo": ("cancel", "unapprove"), "results": ("report",), "stats": ("report",),
}


def _command_suggestions(typed: str, commands: list[str]) -> list[str]:
    import difflib
    aliased = [name for name in COMMAND_ALIASES.get(typed.casefold(), ()) if name in commands]
    close = difflib.get_close_matches(typed, commands, n=3, cutoff=0.6)
    return list(dict.fromkeys(aliased + close))[:3]


def _unrecognized_hint(top: argparse.ArgumentParser, message: str) -> tuple[str, argparse.ArgumentParser]:
    """``unrecognized arguments: --platfrom x`` -> the same plus 'Did you mean --platform?', with the usage of
    the subcommand the flag was given to (argparse reports it on the top-level parser)."""
    import difflib
    seen = getattr(top, "_seen_argv", None) or []
    try:
        action = _subparsers_action(top)
    except StopIteration:
        return message, top
    command = next((token for token in seen if token in action.choices), None)
    target = action.choices[command] if command else top
    own = sorted({flag for a in target._actions for flag in a.option_strings if flag.startswith("--")})
    top_level = {flag for a in top._actions for flag in a.option_strings if flag.startswith("--")}
    hints: list[str] = []
    for token in message[len("unrecognized arguments: "):].split():
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        if command and flag in top_level:
            hints.append(f"{flag} is a global option: put it before the command (reachweaver {flag} ... {command} ...).")
            continue
        close = difflib.get_close_matches(flag, own, n=1, cutoff=0.6)
        if close:
            hints.append(f"Did you mean {close[0]}?")
    help_cmd = f"reachweaver {command} --help" if command else "reachweaver --help"
    tail = (" " + " ".join(dict.fromkeys(hints))) if hints else ""
    return f"{message}.{tail} '{help_cmd}' lists the options", target


def _invalid_choice_hint(message: str) -> str:
    """``argument X: invalid choice: 'blusky' (choose from ...)`` gains 'Did you mean bluesky?'."""
    import ast
    import difflib
    import re
    found = re.search(r"invalid choice: '([^']*)' \(choose from (.*)\)$", message)
    if not found:
        return message
    raw = found.group(2)
    try:  # Python < 3.12 quotes each choice ('a', 'b'); later versions print them bare
        choices = [str(c) for c in ast.literal_eval(f"[{raw}]")]
    except (SyntaxError, ValueError):
        choices = [c.strip().strip("'\"") for c in raw.split(",")]
    close = difflib.get_close_matches(found.group(1), choices, n=1, cutoff=0.6)
    return f"{message}. Did you mean {close[0]}?" if close else message


def make_parser() -> argparse.ArgumentParser:
    parser = _TopParser(prog="reachweaver", description="Approval-first social publishing; dry-run by default.\n\nUsage: reachweaver [--db DB] [--accounts FILE] COMMAND [options]",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, add_help=False)
    from .cli_help import TieredHelpAction  # -h: everyday view; --help all: every command; --help COMMAND: one
    parser.add_argument("-h", "--help", action=TieredHelpAction,
                        help="Show the everyday commands; --help all lists every command by task, "
                             "--help COMMAND shows one command's options")
    parser.add_argument("--version", dest="show_version", action="store_true",
                        help="Print the installed version (the same as the version command) and exit")
    parser.add_argument("--db", help="Local persistent SQLite database path (defaults to state/reachweaver.sqlite)")
    parser.add_argument("--accounts", help="Accounts config path (defaults to config/accounts.json)")
    parser.add_argument("--profile", help="Profile knowledge base path (defaults to config/profile.json in the workspace); "
                                          "personas, usernames and sign-up details, local only")
    parser.add_argument("--home", type=Path, metavar="DIR", help="Use this workspace folder (one that carries a "
                                                               ".reachweaver-workspace file; create it with workspace --init). "
                                                               "Default: the nearest one at or above the current folder")
    parser.add_argument("--plain", action="store_true", help="No colour or tables, even in a terminal (the plain "
                                                              "text format that scripts rely on)")
    # help=SUPPRESS hides argparse's flat 80-line list; the grouped index (cli_help) is the epilog.
    sub = parser.add_subparsers(dest="command", required=False, metavar="COMMAND", help=argparse.SUPPRESS)
    optout_help = "Opt-out list JSON (default $REACHWEAVER_OPTOUT or config/optout.json if present)"
    sub.add_parser("init", help="Initialize local database (not an online account)")
    p = sub.add_parser("version", help="Print the installed ReachWeaver version and install kind; offline")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    _add_idea_and_statusline_parsers(sub)
    p = sub.add_parser("update", help="Check GitHub Releases for a newer ReachWeaver skill bundle (--check, default), "
                       "print a one-line cached --notice, or --apply it; HTTPS GET to github.com only")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Ask GitHub for the latest release now (default)")
    mode.add_argument("--notice", action="store_true",
                      help="At most one request per 24 h; silent on failure or when up to date; for session start")
    mode.add_argument("--apply", action="store_true",
                      help="Download, verify SHA-256 and replace this installed skill; needs --yes")
    p.add_argument("--yes", action="store_true", help="Confirm --apply (the user must have agreed to the update)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("doctor", help="Inspect account configuration and missing credentials without calling APIs")
    p.add_argument("--json", action="store_true", help="Machine-readable checks (credential variable names only)")
    p = sub.add_parser("connect-check", help="Explicit online, non-publishing account identity check")
    p.add_argument("--account", required=True, help="One account id from local ignored config")
    p.add_argument("--online", action="store_true", help="Required: allow authentication/read-only remote API requests")
    p = sub.add_parser("connect", help="Guided account connection (experimental): a status table of your accounts, or "
                       "--guide to walk through them one at a time with you present; takes no secret and never posts")
    p.add_argument("--guide", action="store_true",
                   help="Interactive walkthrough (needs a terminal): official sign-in steps per account, then an optional "
                        "read-only identity check you confirm one account at a time")
    p.add_argument("--account", help="Only this account id")
    p.add_argument("--json", action="store_true", help="Machine-readable status table (no walkthrough)")
    p = sub.add_parser("workspace", help="Read-only: what lives in this folder (config, database, approval ledger, archive), "
                       "what does not (credentials, the installed skill), and whether copying it to another computer "
                       "will work")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--init", nargs="?", const=".", type=Path, metavar="DIR",
                      help="Create the one-folder layout (config/, state/, media/, logs/, marker, README) in DIR "
                           "(default: here); never overwrites or moves existing files")
    mode.add_argument("--gather", action="store_true",
                      help="Copy attachments of unsent posts that live outside media/ into media/ and point the records "
                           "at the copies (hash-verified); dry run unless --apply")
    mode.add_argument("--repair", action="store_true",
                      help="After copying the workspace to a new computer: find attachments of unsent posts again in "
                           "media/ by size and SHA-256 and fix the recorded paths; dry run unless --apply")
    p.add_argument("--apply", action="store_true", help="With --gather or --repair: write the change")
    p.add_argument("--with-examples", action="store_true",
                   help="With --init: seed config/accounts.json with the four example placeholder accounts (the "
                        "old default). Without it the file starts empty, or, in a terminal, with placeholders for "
                        "the networks you name")
    p = sub.add_parser("media-relink", help="After moving the workspace: point recorded attachment paths of unsent posts "
                       "at the new folder, only for files whose SHA-256 still matches; dry run unless --apply")
    p.add_argument("--from", dest="from_prefix", required=True, metavar="OLD_FOLDER", help="Absolute folder the files were in")
    p.add_argument("--to", dest="to_prefix", required=True, metavar="NEW_FOLDER", help="Absolute folder they are in now")
    p.add_argument("--apply", action="store_true", help="Write the change (default: show what would change)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("journal", help="Read-only: what was posted, when, where, with which metadata and files "
                       "(text, account, approver, link, audit timeline, file paths and hashes), table/json/markdown/csv")
    _add_journal_filters(p)
    p.add_argument("--format", choices=("table", "json", "markdown", "csv"), default="table")
    p.add_argument("--out", type=Path, metavar="FILE", help="Write to this NEW owner-only (0600) file instead of stdout")
    p.add_argument("--verify-files", action="store_true",
                   help="Also hash each recorded file that is still on disk and say whether it matches the record")
    p = sub.add_parser("archive", help="Write a private folder with one sub-folder per post: the exact text, the "
                       "metadata and, with --copy-files, hash-verified copies of the attached files; index.md/index.csv. "
                       "Only ever adds; nothing is sent")
    _add_journal_filters(p)
    p.add_argument("--out", type=Path, metavar="DIR", help="Archive folder (default: archive/ next to the database); "
                   "created 0700, or an existing archive folder to add to")
    p.add_argument("--copy-files", action="store_true",
                   help="Copy each attached image and video that is still on disk and still matches its recorded SHA-256")
    p.add_argument("--json", action="store_true", help="Machine-readable summary")
    p = sub.add_parser("repeat-check", help="Read-only: have we already shared this video, image, wording or link on "
                       "this platform? Compares a stored post, or material you name, with the local history")
    p.add_argument("--post-id", help="A stored post to check (its text, images, video and link)")
    p.add_argument("--platform", help="With no --post-id: the platform to check against, e.g. youtube")
    p.add_argument("--account", help="With no --post-id: the account (same-account repeats are the blocking ones)")
    p.add_argument("--text", help="With no --post-id: the post text")
    p.add_argument("--file", type=Path, help="With no --post-id: read the post text from this UTF-8 file")
    p.add_argument("--image", action="append", default=[], type=Path, metavar="FILE", help="An image file; repeatable")
    p.add_argument("--video", type=Path, metavar="FILE", help="A video file (compared by its exact bytes)")
    p.add_argument("--link", action="append", default=[], metavar="URL", help="A link; repeatable")
    p.add_argument("--days", type=int, default=repeat_guard.DEFAULT_DAYS, metavar="N",
                   help="Look-back for similar (not identical) wording, 1..3650 (default 365); identical files and "
                        "wording are always found")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("mark-shared", help="Record material you shared by hand (manual export, a platform app) so "
                       "later drafts are checked against it; with --url also makes it measurable (posted by hand); "
                       "local, never sends")
    p.add_argument("--platform", required=True, help="The platform it was shared on, e.g. youtube")
    p.add_argument("--account", help="The account it was shared from; without it the record counts for every account "
                                      "on that platform")
    p.add_argument("--text", help="The post text")
    p.add_argument("--file", type=Path, help="Read the post text from this UTF-8 file")
    p.add_argument("--image", action="append", default=[], type=Path, metavar="FILE", help="An image file; repeatable")
    p.add_argument("--video", type=Path, metavar="FILE", help="A video file")
    p.add_argument("--link", action="append", default=[], metavar="URL", help="A link; repeatable")
    p.add_argument("--url", default="", help="The link of the post on the platform. With it the post also becomes "
                                             "measurable: a 'published_manual' record that metrics, import-metrics, "
                                             "report, insights and the dashboard include, labelled posted by hand; it "
                                             "can never be approved, scheduled or sent")
    p.add_argument("--at", metavar="WHEN",
                   help="When it was shared (default: now): now, today, yesterday, YYYY-MM-DD (midday), "
                        "'YYYY-MM-DD HH:MM' (your computer's time zone) or ISO 8601 with a timezone; with --url "
                        "this is the post's send time for report --since and insights")
    p.add_argument("--note", default="", help="A short label for yourself")
    p = sub.add_parser("posted", help="Record a post you made by hand from its link, with its numbers if you have "
                       "them, in one step; run it again with new numbers to update them; local, never sends")
    p.add_argument("url", nargs="?", metavar="URL", help="The link of your post (copy it from the app's share menu)")
    for key in ("likes", "replies", "reposts", "views"):
        p.add_argument("--" + key, metavar="N",
                       help=f"{key.capitalize()} you see now: a count, or unknown / suppressed / not_available / "
                            "delayed / estimated:N")
    p.add_argument("--text", help="The post text (optional; reports show its first words)")
    p.add_argument("--at", metavar="WHEN",
                   help="When you posted it (default: now): now, today, yesterday, YYYY-MM-DD, 'YYYY-MM-DD HH:MM' "
                        "(your computer's time zone) or ISO 8601 with a timezone")
    p.add_argument("--account", help="Your account id, when several accounts on that network could have posted it")
    p.add_argument("--platform", help="The network, when the link's address does not say (e.g. your own "
                                      "Mastodon server that is not in your accounts file)")
    p = sub.add_parser("disconnect", help="Stop publishing to one account on this installation (#38): tighten-only, local; "
                       "lists the credential variable names and the steps to revoke at the provider; revokes nothing itself")
    p.add_argument("--account", required=True, help="One account id from local ignored config")
    p = sub.add_parser("reconnect", help="Lift a local disconnect (#38). Human-only: needs an interactive terminal and a "
                       "fresh challenge typed on /dev/tty")
    p.add_argument("--account", required=True, help="The disconnected account id")
    p = sub.add_parser("postiz-check", help="Explicit online, READ-ONLY Postiz integration listing/receipt lookup; never posts")
    p.add_argument("--account", required=True, help="One account with \"backend\": \"postiz\" in local ignored config")
    p.add_argument("--online", action="store_true", help="Required: allow read-only GET requests to the Postiz API")
    p.add_argument("--post-id", help="Also look up this local post's Postiz job state (backend-reported; never reconciles)")
    p = sub.add_parser("credentials-audit", help="Offline credential hygiene: env variable NAMES only, never values; exit 1 on findings")
    p.add_argument("--history", action="append", default=[], type=Path, metavar="FILE",
                   help="Also scan this shell history file for literal REACHWEAVER_* secrets (line numbers only); repeatable")
    p.add_argument("--json", action="store_true", help="Machine-readable names-only report")
    p = sub.add_parser("list", help="List posts and statuses")
    p.add_argument("--status", choices=POST_STATUSES, help="Filter by status")
    p.add_argument("--json", action="store_true", help="Machine-readable array of rows")
    sub.add_parser("show", help="Show exact post text and account metadata for human review; offline").add_argument("post_id")
    p = sub.add_parser("draft", help="Queue a platform-native text draft for an account (repeat --account for one "
                       "draft per account; each still needs its own approval)")
    p.add_argument("--account", required=True, action="append",
                   help="Account id; repeat to create one separate draft per account, all or none")
    body = p.add_mutually_exclusive_group(required=True)
    body.add_argument("--text")
    body.add_argument("--file", type=Path)
    p.add_argument("--campaign", default="")
    p.add_argument("--source-url", default="")
    p.add_argument("--persona", metavar="ID",
                   help="Which of your personas this post is for (for example work or family). Refuses an account that belongs "
                        "to another persona and names the one to switch to; default: preferences.default_persona in the profile")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention refuses the draft (and, with "
                                                             "--reply-to, a listed parent author)")
    p.add_argument("--reply-to", metavar="ITEM",
                   help="Issue #17 (owner-approved 2026-10-09; disabled until a live receipt): make this a reply to an engagement item id "
                        "(bluesky:at://... or mastodon:HOST:ID from engagement-fetch/triage) under one of "
                        "your own published posts; needs --online to verify the parent; sending stays "
                        "blocked by the capability registry")
    p.add_argument("--online", action="store_true",
                   help="With --reply-to: allow the unauthenticated read-only GETs that verify the parent "
                        "(without it the planned requests are printed and nothing is created)")
    p.add_argument("--override-flagged", metavar="LABEL",
                   help="With --reply-to: explicit human override for a parent that triage flags as "
                        "suspicious, harassment, spam or moderation_labelled (must name that exact label); "
                        "opt-out signals are never overridable")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the text (a reply's too) or --source-url links into any visibility:private repository of that config (private repository URLs are never public links)")
    p.add_argument("--json", action="store_true", help="Machine-readable result: the created post id(s); not with --reply-to")
    p = sub.add_parser("check", help="Validate a draft before approval")
    p.add_argument("post_id")
    p.add_argument("--json", action="store_true", help="Machine-readable result: ok and the findings")
    p.add_argument("--persona", metavar="ID",
                   help="Which of your personas this post is for (for example work or family). Refuses an account that belongs "
                        "to another persona and names the one to switch to; default: preferences.default_persona in the profile")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is BLOCKED")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when any published text (post, alt texts, video title/tags), a hosted image URL or the source URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    p = sub.add_parser("review", help="Owner-only guided approval (#11): walk the drafts one by one, oldest first, in your own "
                       "terminal; each shows its review sheet and check result, then a approves it through the approve "
                       "command, s skips, c cancels (type CANCEL), q quits. Needs an interactive terminal; never batches; "
                       "reply drafts are left out")
    p.add_argument("--campaign", help="Only drafts of this campaign")
    p.add_argument("--account", help="Only drafts of this account id")
    p.add_argument("--approver", help="Passed to each approve (attribution, not authentication); defaults to $USER/$LOGNAME")
    p.add_argument("--persona", metavar="ID", help="Passed to each check and approve: refuse an account of another persona")
    p.add_argument("--optout", type=Path, help=optout_help + "; passed to each check and approve")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9), passed to each check and approve: refuse links into private repositories")
    p.add_argument("--verify-identity", action="store_true",
                   help="Each approve runs its one read-only identity check (needs --online), as approve --verify-identity")
    p.add_argument("--online", action="store_true", help="Required with --verify-identity")
    p = sub.add_parser("approve", help="Approve the exact current text; binds an immutable payload hash (#7), plus "
                                       "a Mastodon account's visibility/language/spoiler_text or a Postiz account's "
                                       "server and integration")
    p.add_argument("post_id")
    p.add_argument("--persona", metavar="ID",
                   help="Which of your personas this post is for (for example work or family). Refuses an account that belongs "
                        "to another persona and names the one to switch to; default: preferences.default_persona in the profile")
    p.add_argument("--approver", help="Who approved (attribution, not authentication); defaults to $USER/$LOGNAME")
    p.add_argument("--verify-identity", action="store_true",
                   help="Wave 9 item 2: run the read-only connect-check identity check once and bind a "
                        "fingerprint of the verified account (no token or response stored); needs --online. "
                        "A failed check approves nothing. Binds WHICH account, not WHO approved")
    p.add_argument("--online", action="store_true", help="Required with --verify-identity: allow that one read-only identity request")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention refuses approval")
    p.add_argument("--allow-repeat", action="store_true",
                   help="Approve even though this account has already shared (or has another approved copy of) the same "
                        "video, image or wording on this platform (repeat-check shows what matched). Bound into the "
                        "approval, so the send-time repeat check lets this one post through; also an audit event")
    p.add_argument("--ai-label", choices=("applied", "not_needed"),
                   help="Posts with images or a video only: your answer to the AI-content label question when "
                        "ai-label-check says required or unknown for the post's platform (approval is refused "
                        "without it). applied: you label it yourself (platform composer or text; ReachWeaver sets no "
                        "label); not_needed: you decided no label is needed. Bound into the approval (approval/media-3)")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when any published text (post, alt texts, video title/tags), a hosted image URL or the source URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    p = sub.add_parser("youtube-attach", help="#22 (owner-approved; disabled until a live receipt): attach one local MP4 + upload metadata to a YouTube "
                                              "draft; offline, hashes the file; live upload stays registry-blocked")
    p.add_argument("post_id")
    p.add_argument("--file", type=Path, required=True, help="Local MP4 file (symlinks refused); its SHA-256 is bound")
    p.add_argument("--title", required=True, help="Video title, at most 100 characters, no '<' or '>'")
    p.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private",
                   help="privacyStatus to request (default private; the API default would be public)")
    p.add_argument("--made-for-kids", choices=("yes", "no"), required=True,
                   help="Required human answer for selfDeclaredMadeForKids (YouTube requires every creator to set it)")
    p.add_argument("--category-id", required=True, help="Numeric YouTube category id (videoCategories.list)")
    p.add_argument("--tag", action="append", default=[], help="One tag; repeat (500 characters in total)")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the title or a tag links into any visibility:private repository of that config (private repository URLs are never public links)")
    sub.add_parser("youtube-detach", help="#22: remove the video from a YouTube draft; offline").add_argument("post_id")
    for name, text in (("unapprove", "Withdraw approval: an approved or scheduled post goes back to draft and its "
                                     "payload binding is cleared; local only"),
                       ("cancel", "Cancel a draft, approved or scheduled post so it is never sent; local only")):
        p = sub.add_parser(name, help=text)
        p.add_argument("post_id")
        p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("schedule", help="Give an approved post a due time (explicit timezone offset); a worker "
                                        "you run still has to publish it")
    p.add_argument("post_id")
    p.add_argument("--at", required=True, help="Explicit ISO 8601 timezone offset, e.g. 2026-10-15T09:00:00+02:00")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("media-attach", help="Add one image with required alt text to a DRAFT: a local PNG/JPEG/WebP "
                                            "file for Mastodon (--file, offline) or an owner-hosted HTTPS JPEG URL for "
                                            "Instagram (--url with --online: one read-only GET to hash it). Its SHA-256 "
                                            "(and the URL) are bound at approval (#13, #20); never uploads")
    p.add_argument("post_id")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Mastodon: local image file (not a symlink); checked from its header")
    source.add_argument("--url", help="Instagram: public HTTPS image URL on a host you control (no query string); "
                                      "Meta fetches this URL itself when the post is sent")
    alt = p.add_mutually_exclusive_group(required=True)
    alt.add_argument("--alt", help="Alt text describing the image (required; Mastodon description or Instagram alt_text)")
    alt.add_argument("--alt-file", type=Path, help="Read the alt text from this UTF-8 file")
    p.add_argument("--focus", help="Mastodon only: optional focal point x,y, each -1.0..1.0 (0,0 = centre)")
    p.add_argument("--online", action="store_true", help="Required with --url: allow one read-only GET of the image URL")
    p.add_argument("--rights-confirmed", action="store_true",
                   help="#48: you confirm you hold the rights, consent and likeness permission for this image; "
                        "required with any other rights option")
    p.add_argument("--license", dest="license_id", metavar="ID",
                   help="#48: SPDX id (for example CC-BY-4.0), 'owner' or 'licensed'; stored with the image and bound at approval")
    p.add_argument("--created-by", choices=sorted(CREATORS), help="#48: who made the image (provenance)")
    p.add_argument("--tool", help="#48: with --created-by tool: the generation or editing tool")
    p.add_argument("--attribution", help="#48: attribution text (required for third_party and CC-BY images)")
    p.add_argument("--rights-source-url", metavar="URL", help="#48: public HTTPS page the image came from (provenance)")
    p.add_argument("--alt-translation", action="append", default=[], metavar="LANG=TEXT",
                   help="#48: your own alt text in another language (BCP-47 tag), stored and bound but not sent; repeatable")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention in the alt text refuses the attachment")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config (#9): refuse when the alt text or the image URL links into any visibility:private repository of that config (private repository URLs are never public links)")
    sub.add_parser("media-detach", help="Offline: remove every image attachment from a DRAFT").add_argument("post_id")
    p = sub.add_parser("ai-declare", help="Offline: record your AI-content declarations on a DRAFT: who wrote the "
                                          "text (--authorship) and whether each image or video is realistic "
                                          "AI-made media (--realistic-synthetic); bound at approval for a post with "
                                          "media (approval/media-3) and used by the AI-label precondition")
    p.add_argument("post_id")
    p.add_argument("--authorship", choices=("human", "ai_assisted", "ai_generated", "clear"),
                   help="Who wrote the text: human, ai_assisted or ai_generated (clear removes it)")
    p.add_argument("--realistic-synthetic", choices=("yes", "no", "clear"),
                   help="yes: the image/video could pass as a real photo or recording but was AI-made or altered; "
                        "no: it is not; clear removes the answer. Applies to every attachment unless --attachment")
    p.add_argument("--attachment", type=int, metavar="N",
                   help="Only the Nth attachment (1-based, in send order; a video is 1)")
    p.add_argument("--json", action="store_true", help="Machine-readable result: the declarations and the advice")
    p = sub.add_parser("video-attach", help="Offline: bind one local MP4 to a TikTok DRAFT (issue #23; owner-approved, "
                                            "disabled until a live receipt); approval binds its SHA-256 and the "
                                            "delivery options; a post has images or one video, never both; never uploads")
    p.add_argument("post_id")
    p.add_argument("--file", required=True, type=Path, help="Local MP4 (not a symlink), at most 256 MiB")
    p.add_argument("--mode", choices=TIKTOK_MODES, default="creator_inbox",
                   help="creator_inbox (default): a draft the creator finishes in the TikTok app, never public by "
                        "itself; direct_post: publishes (second registry switch, off)")
    p.add_argument("--privacy-level", choices=TIKTOK_PRIVACY_LEVELS,
                   help="direct_post only, required, no default; must be offered by the creator's creator_info")
    p.add_argument("--commercial", choices=TIKTOK_COMMERCIAL, help="direct_post only, required: commercial disclosure")
    for name in ("comment", "duet", "stitch"):
        p.add_argument(f"--allow-{name}", action="store_true", help=f"direct_post only: turn {name}s on (default off)")
    p.add_argument("--aigc", action="store_true", help="direct_post only: label the video as AI-generated")
    sub.add_parser("video-detach", help="Offline: remove the video from a TikTok DRAFT; the file is untouched"
                   ).add_argument("post_id")
    p = sub.add_parser("calendar-plan", help="Propose audience-local times; optional --apply queues approved posts locally only")
    p.add_argument("--account", required=True, help="One configured social account ID")
    p.add_argument("--timezone", required=True, help="Audience IANA zone, e.g. Europe/Rome; not necessarily operator zone")
    p.add_argument("--start", required=True, help="First eligible local day YYYY-MM-DD")
    p.add_argument("--days", type=int, default=14, help="Window length 1..60 days (default 14)")
    p.add_argument("--spacing-hours", type=int, default=20, help="Minimum gap per account 1..168 hours")
    p.add_argument("--post-id", action="append", help="Select a post explicitly; repeat; default is all approved posts")
    p.add_argument("--limit", type=int, default=30, help="Maximum selected posts, at most 100")
    p.add_argument("--slot", action="append", help="Audience-local preferred slot Wed@16:00; repeat (required for some platforms)")
    p.add_argument("--blackout", action="append", help="Exclude day YYYY-MM-DD; repeat")
    p.add_argument("--include-drafts", action="store_true", help="Preview draft and approved posts; cannot be applied")
    p.add_argument("--apply", action="store_true", help="Explicitly queue already approved items in local SQLite; never publish")
    p.add_argument("--audience-activity", type=Path, metavar="FILE",
                   help="Owner-exported hour-of-week activity CSV (ReachWeaver interchange format, "
                        "examples/audience-activity.example.csv); top hours become slots with provenance")
    p.add_argument("--audience-top-hours", type=int, metavar="N", help="With --audience-activity: top activity hours used, 1..24 (default 5)")
    p.add_argument("--audience-max-age-days", type=int, metavar="N",
                   help="With --audience-activity: warn when as_of is older than N days (default 90)")
    p.add_argument("--explore-share", type=float, default=0.0, metavar="FRACTION",
                   help="Send this share of the posts (above 0, at most 0.5; default 0 = off) to --explore-slot "
                        "times, labelled role=explore; the rest stay on the exploit slots. Not randomized")
    p.add_argument("--explore-slot", action="append", metavar="Wed@11:00",
                   help="With --explore-share: an audience-local time to explore, distinct from the exploit slots; repeat")
    p.add_argument("--market", action="append", default=[], metavar="MARKET",
                   help="Audience market for market-specific notices: US, global or PLATFORM@MARKET (tiktok@US); "
                        "adds a sourced flag only, never changes the slots")
    p = sub.add_parser("calendar-gaps", help="Read-only: empty and overloaded slots per account from the local queue; "
                       "never approves, schedules or publishes")
    p.add_argument("--account", required=True, help="One configured social account ID")
    p.add_argument("--timezone", required=True, help="Audience IANA zone, e.g. Europe/Rome")
    p.add_argument("--start", help="First local day YYYY-MM-DD (default: today in the audience zone)")
    p.add_argument("--days", type=int, default=14, help="Window length 1..60 days (default 14)")
    p.add_argument("--slot", action="append", help="Audience-local preferred slot Wed@16:00; repeat (as calendar-plan)")
    p.add_argument("--blackout", action="append", help="Exclude day YYYY-MM-DD; repeat")
    p.add_argument("--audience-activity", type=Path, metavar="FILE", help="Owner-exported activity CSV; as calendar-plan")
    p.add_argument("--audience-top-hours", type=int, metavar="N", help="With --audience-activity: top hours used, 1..24")
    p.add_argument("--audience-max-age-days", type=int, metavar="N", help="With --audience-activity: stale after N days")
    p.add_argument("--tolerance-minutes", type=int, default=90, help="A queued post within this many minutes fills a slot (1..720, default 90)")
    p.add_argument("--max-per-day", type=int, default=2, help="More queued posts than this on one local day is overloaded (1..20, default 2)")
    p.add_argument("--min-spacing-hours", type=int, default=20, help="Queued posts closer than this are flagged (1..168, default 20)")
    p.add_argument("--timing-source", help="Annotate empty slots with timing-evaluate evidence from this exact imported source")
    p.add_argument("--timing-metric", choices=METRIC_FIELDS, help="With --timing-source: goal numerator, e.g. clicks")
    p.add_argument("--timing-denominator", choices=("views", "impressions"), help="With --timing-source")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("trace", help="Read-only: everything that happened to one post, oldest first: its audit "
                       "events and, with --log, the worker-log lines and runs that handled it (issue #26)")
    p.add_argument("post_id")
    p.add_argument("--log", action="append", default=[], metavar="FILE",
                   help="A publish --log-json file to join (repeat); '-' reads standard input, e.g. "
                   "journalctl -u reachweaver -o cat | reachweaver trace ID --log -")
    p.add_argument("--json", action="store_true", help="Machine-readable trace")
    p = sub.add_parser("link-health", help="Read-only: broken, redirected or preview-less links in stored posts; "
                       "offline unless --online (robots.txt first, one GET per page, never posts)")
    p.add_argument("--status", action="append", metavar="STATUS",
                   help="Post status to scan; repeat (default: approved, scheduled, published)")
    p.add_argument("--account", help="Only posts of this account id")
    p.add_argument("--campaign", help="Only posts of this campaign")
    p.add_argument("--online", action="store_true", help="Allow one GET per distinct landing page (robots.txt first)")
    p.add_argument("--allow-origin", action="append", metavar="ORIGIN",
                   help="With --online: also follow redirects to this https origin; repeat")
    p.add_argument("--max-links", type=int, default=25, help="Distinct pages fetched per run, 1..50 (default 25)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("publish", help="Preview due work; --live + opt-in env flag required to send")
    p.add_argument("--live", action="store_true", help="Opt in to actual network publishing")
    p.add_argument("--limit", type=int, default=10, help="Maximum eligible posts (default 10, max 100)")
    p.add_argument("--post-id", help="Send or preview only this explicitly approved, due post ID (recommended for pilots)")
    p.add_argument("--max-per-24h", type=int, default=2,
                   help="Live per-account rolling 24h send-attempt cap 1..10 (default 2)")
    p.add_argument("--min-gap-minutes", type=int, default=60,
                   help="Live per-account minimum minutes between attempts 15..1440 (default 60)")
    p.add_argument("--scheduled-only", action="store_true",
                   help="Worker-safe mode: skip approved unscheduled posts; only send due scheduled items")
    p.add_argument("--max-lateness-minutes", type=int, default=180,
                   help="With --scheduled-only, skip posts whose due time is >N min late (5..10080, default 180)")
    p.add_argument("--optout", type=Path, help=optout_help + "; loaded once per run, a mention is BLOCKED "
                                                             "before any claim and the post stays approved")
    p.add_argument("--log-json", action="store_true",
                   help="Also emit one redacted JSON line per worker decision to stderr (dry run and live); "
                        "observational only, never post text, handles, secrets or URL query strings")
    p.add_argument("--log-file", type=Path, metavar="PATH",
                   help="With --log-json: append the JSON lines to PATH instead (created 0600, owner-only); "
                        "an unwritable PATH refuses the run before any account or claim")
    p.add_argument("--json", action="store_true",
                   help="Dry run only: the preview as JSON (due posts and what --live would skip); refused with --live")
    p = sub.add_parser("reconcile", help="Human-confirm outcome of an uncertain send; do not blindly retry")
    p.add_argument("post_id")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--published", action="store_true")
    group.add_argument("--not-published", action="store_true")
    p.add_argument("--remote-id", default="")
    p.add_argument("--remote-url", default="")
    p.add_argument("--force", action="store_true",
                   help="Reconcile a post still in 'sending' that was claimed less than 15 minutes ago "
                        "(a send may be in flight). Only after checking the native account yourself")
    sub.add_parser("pause", help="Emergency global stop for all live publishing")
    p = sub.add_parser("approval-mode",
                       help="Show or tighten approval safeguards: human confirmation (#7 D6) and keyed binding (D7); both opt-in")
    p.add_argument("--confirmation", choices=(confirmation.TTY_HASH_PREFIX, "off"),
                   help="tty_hash_prefix: approve and publish --live need a terminal and the typed hash prefix; "
                        "off needs the same ritual while it is on")
    p.add_argument("--require-key", action="store_true",
                   help="Latch keyed approval mode (needs a usable REACHWEAVER_APPROVAL_SECRET here); "
                        "no command removes the latch")
    p.add_argument("--require-identity", action="store_true",
                   help="Latch identity-bound approval (wave 9 item 2): approve needs --verify-identity --online and "
                        "every send needs a stored identity; tighten-only, no command removes the latch")
    p.add_argument("--max-age-hours", type=int, metavar="N",
                   help="Approval time-to-live (#39): an approval older than N hours (1..8760) is expired and the post "
                        "must be re-approved before any send; tighten-only, only a lower value is accepted later")
    p.add_argument("--json", action="store_true",
                   help="Display the current mode as JSON; only without the options above, which stay text-only")
    sub.add_parser("resume", help="Explicitly lift the emergency stop")
    p = sub.add_parser("metrics", help="Record one post's numbers (likes, replies...) that you read in the "
                                        "platform's insights or a native export")
    p.add_argument("post_id", nargs="?", help="The post id ('reachweaver list' shows them); for a post you made "
                                              "by hand, 'reachweaver posted LINK --likes N' needs no id")
    p.add_argument("--observed-at", default="now", metavar="WHEN",
                   help="When you read the numbers (default: now): now, today, yesterday, YYYY-MM-DD, "
                        "'YYYY-MM-DD HH:MM' (your computer's time zone) or ISO 8601 with a timezone")
    p.add_argument("--source", default=HAND_ENTERED_SOURCE,
                   help=f"Where the numbers come from (default '{HAND_ENTERED_SOURCE}': you typed them from the "
                        "platform's own insights; ReachWeaver did not fetch them). Use a label such as "
                        "native-export for an exported file")
    for key in METRIC_FIELDS:
        p.add_argument("--" + key, metavar="VALUE",
                       help="Count, or unknown / suppressed / not_available / delayed / estimated:N")
    p.add_argument("--window-start", help="Optional start of the period the counts cover (same forms as "
                                          "--observed-at)")
    p.add_argument("--window-end", help="Optional end of that period; give both or neither (neither = unknown)")
    p = sub.add_parser("import-metrics", help="Import native-analytics snapshots normalized as CSV (all rows or none)")
    p.add_argument("--csv", required=True, type=Path,
                   help="Columns post_id, observed_at, source, metric columns; optional window_start, window_end. "
                        "A metric cell is a count, empty/unknown, suppressed, not_available, delayed or estimated:N")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("metrics-fetch", help="Explicit online, READ-ONLY poll of public Bluesky/Mastodon post "
                       "counters into metric snapshots; not private analytics, never posts")
    p.add_argument("--online", action="store_true", help="Required (unless --dry-run): allow read-only GET requests")
    p.add_argument("--account", help="Only this bluesky/mastodon account id")
    p.add_argument("--post-id", help="Only this local published post")
    p.add_argument("--max-posts", type=int, default=DEFAULT_MAX_POSTS,
                   help=f"Posts fetched per run (1..{MAX_POSTS_LIMIT}, default {DEFAULT_MAX_POSTS})")
    p.add_argument("--dry-run", action="store_true", help="Print the planned GET requests; no network, no DB write")
    p = sub.add_parser("engagement-fetch", help="READ-ONLY fetch of public replies (and, with --quotes, Bluesky quote "
                       "posts) to your published Bluesky/Mastodon posts into an engagement-triage export; plan "
                       "only unless --online; never replies")
    p.add_argument("--online", action="store_true", help="Allow unauthenticated read-only GET requests (needs --out)")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) engagement export JSON; never overwritten")
    p.add_argument("--account", help="Only this bluesky/mastodon account id")
    p.add_argument("--post-id", help="Only this local published post")
    p.add_argument("--max-posts", type=int, default=DEFAULT_MAX_POSTS,
                   help=f"Posts fetched per run (1..{MAX_POSTS_LIMIT}, default {DEFAULT_MAX_POSTS})")
    p.add_argument("--depth", type=int, default=6, help="Reply levels below each post (1..20, default 6)")
    p.add_argument("--max-replies", type=int, default=100, help="Replies kept per post (1..500, default 100)")
    p.add_argument("--quotes", action="store_true",
                   help="Also read Bluesky quote posts of each post (app.bsky.feed.getQuotes, public AppView, "
                        "unauthenticated, cursor pages); kind quote_post; network only with --online")
    p.add_argument("--max-quotes", type=int, default=100,
                   help="Quote posts kept per Bluesky post with --quotes (1..500, default 100, at most 5 pages)")
    p = sub.add_parser("report", help="Coverage-aware local analytics; no fabricated reach or results")
    p.add_argument("--format", choices=REPORT_FORMATS, default=None, dest="report_format",
                   help="text (the default at a terminal: a short summary, a table per network and your top 5 "
                        "posts), json (the default when the output is piped or redirected; unchanged envelope), "
                        "markdown, or csv (one row per platform and metric; spreadsheet-formula cells neutralised)")
    when = p.add_mutually_exclusive_group()
    when.add_argument("--since", metavar="WHEN",
                      help="Only posts sent in this window and their snapshots observed in it: 24h, 7d, 30d, a date "
                           "(2026-10-01, midnight UTC) or an ISO time with a timezone. Read-only")
    when.add_argument("--week", action="store_true", help="The last 7 days: the same as --since 7d")
    p = sub.add_parser("youtube-analytics", help="#22: offline sampling report over stored metric snapshots of "
                       "published YouTube videos (exact/sampled/unknown labels); read-only, fetches nothing")
    p.add_argument("--account", help="Only this YouTube account id")
    p.add_argument("--post-id", help="Only this local published YouTube post")
    p = sub.add_parser("queue-health", help="Offline status, attempts, overdue and uncertain sends, provider "
                                            "cooldowns; never auto-retry")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p = sub.add_parser("health", help="Offline readiness per configured account: adapters, credentials (names), "
                                      "budgets, provider cooldowns, queue, pause; exit 1 when blocked; never "
                                      "contacts providers")
    p.add_argument("--json", action="store_true", help="Full machine-readable report, including the dead-letter view")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p = sub.add_parser("dead-letter", help="Read-only needs_review posts grouped by sanitized error code + reconcile steps; never retries")
    p.add_argument("--json", action="store_true", help="Machine-readable grouping")
    p = sub.add_parser("ops-metrics", help="Read-only operational counts (claims, failures by send class, cooldowns, "
                                           "pause, dead letters, stale sending, due age) as JSON or Prometheus text")
    p.add_argument("--format", choices=("json", "prometheus"), default="json", help="Output format (default json)")
    p.add_argument("--since", help="Count audit events from this ISO 8601 time or a relative 30m/24h/7d "
                                   "(default: all time, as Prometheus counters)")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p.add_argument("--optout", type=Path, help=optout_help + "; queued posts mentioning it are counted, never named")
    p.add_argument("--out", type=Path, help="Atomically write this file (mode 0644), e.g. a node_exporter "
                                            "textfile-collector *.prom path; an existing file is replaced only if "
                                            "named *.prom or *.json, never the database or a symlink")
    p.add_argument("--now", help="Evaluate at this ISO 8601 time instead of the clock (reproducible output)")
    p = sub.add_parser("ops-summary", help="One redacted read-only report: overall readiness, per-account adapter, "
                                           "budget and queue, dead letters and ops-metrics totals; exit 1 when "
                                           "blocked; never contacts providers")
    p.add_argument("--json", action="store_true", help="Machine-readable report (default: short text)")
    p.add_argument("--since", help="Count audit events from this ISO 8601 time or a relative 30m/24h/7d")
    p.add_argument("--stale-minutes", type=int, default=30,
                   help="Mark sending claims older than this threshold as stale (5..10080)")
    p.add_argument("--optout", type=Path, help=optout_help + "; queued posts mentioning it are counted, never named")
    p.add_argument("--now", help="Evaluate at this ISO 8601 time instead of the clock (reproducible output)")
    p = sub.add_parser("completion", help="Print a shell completion script for commands, options and fixed choices "
                                          "(bash, or zsh via bashcompinit); reads no database and runs nothing")
    p.add_argument("shell", choices=("bash", "zsh"))
    p = sub.add_parser("next", help="What to post next: at most 5 concrete steps (your open ideas by name with a "
                                    "ready share command, drafts waiting, posts due for their 7-day numbers, days "
                                    "since you last posted per network); read-only, runs nothing")
    p.add_argument("--json", action="store_true", help="Machine-readable view (schema reachweaver.next.v1)")
    p = sub.add_parser("start", help="Read-only guide: look at your accounts file, database and queue and print the "
                                     "exact next command to run; runs nothing and never creates the database")
    p.add_argument("--json", action="store_true", help="Machine-readable guide")
    p = sub.add_parser("context", help="Read-only session start for an agent: version, paths, account readiness, pause, "
                                       "live gate, approval mode, post counts, posts waiting for the owner, next steps "
                                       "and owner-only commands, in one call; never creates the database")
    p.add_argument("--json", action="store_true", help="Compact machine-readable summary")
    p = sub.add_parser("help", help="Show the everyday commands; 'help all' lists every command by task and "
                                     "'help COMMAND' shows one command's options; offline, prints text only")
    p.add_argument("topic", nargs="?", metavar="TOPIC", help="'all' for every command, or a command name")
    p = sub.add_parser("describe", help="Machine-readable command catalog generated from the parser: flags, choices, "
                                        "defaults, JSON support and owner-only/network/writes/read-only tags")
    p.add_argument("name", nargs="?", metavar="COMMAND", help="Describe one command, with the help of each flag")
    p.add_argument("--json", action="store_true", help="Machine-readable catalog")
    p = sub.add_parser("ui", help="Serve a read-only dashboard of your local state on 127.0.0.1 (queue, post review "
                                  "sheets, readiness, accounts, channel research); cannot approve, schedule, pause "
                                  "or publish, and never creates the database")
    p.add_argument("--port", type=int, default=0, help="Loopback port (default: a free one)")
    p.add_argument("--open", action="store_true", help="Open the tokenised URL in your default browser")
    p.add_argument("--optout", type=Path, help=optout_help + "; queued posts mentioning it are counted, never named")
    p = sub.add_parser("mcp-serve", help="Experimental: a local MCP server on stdin/stdout for MCP "
                       "clients other than Claude Code (Claude Desktop...); only read-only tools, no approve or "
                       "publish tool; runs until its input closes (docs/MCP-SERVER.md)")
    p.add_argument("--allow-drafts", action="store_true",
                   help="Also offer a draft tool (writes local drafts only; each still needs your own approval)")
    p.add_argument("--root", type=Path, metavar="DIR",
                   help="File arguments must resolve inside this folder (default: the workspace, else the current folder)")
    p.add_argument("--max-output-bytes", type=int, default=262144, metavar="N",
                   help="Refuse a tool result larger than N bytes (default 262144)")
    p.add_argument("--max-calls-per-minute", type=int, default=60, metavar="N",
                   help="Rate limit for tool calls (default 60)")
    p = sub.add_parser("campaign-check", help="Offline evidence/brand/variant checks; does not assert source truth")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, help="Brand file; omitting skips account/voice checking")
    p.add_argument("--json", action="store_true", help="Machine-readable diagnostics")
    p.add_argument("--optout", type=Path, help=optout_help)
    _add_history_args(p)
    p = sub.add_parser("campaign-preview", help="Offline per-channel previews: exact text, platform length, facets, "
                       "capability, evidence, rule-based confidence; never stages or publishes")
    p.add_argument("--file", type=Path, required=True, help="Campaign JSON (read only; never modified)")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON, including optional per-account rules")
    p.add_argument("--accounts", dest="preview_accounts", type=Path,
                   help="Optional PUBLIC accounts config (e.g. Mastodon max_characters); same as global --accounts")
    p.add_argument("--utm", action="store_true", help="Show each link with utm_source/medium/campaign and utm_content=variant id")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is an error")
    _add_history_args(p)
    shape = p.add_mutually_exclusive_group()
    shape.add_argument("--json", action="store_true", help="Machine-readable preview")
    shape.add_argument("--markdown", action="store_true", help="Editable Markdown review sheet")
    p = sub.add_parser("campaign-stage", help="Idempotently stage verified text variants as drafts; never publishes")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, required=True, help="Required explicit brand-account allowlist")
    p.add_argument("--optout", type=Path, help=optout_help)
    p.add_argument("--json", action="store_true", help="Accepted for consistency: the result is always JSON")
    p = sub.add_parser("campaign-export", help="Export validated draft pack for manual publishing; no network")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--brand", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="New output folder; refuses existing output")
    p.add_argument("--optout", type=Path, help=optout_help)
    p = sub.add_parser("suggest", help="Read-only ranked human review of profile, posts, SEO, engagement, experiments and local queue; "
                       "with no input flag it reads an existing local database (query-only) and the ideas inbox")
    p.add_argument("--snapshot", type=Path, help="Owner-supplied profile/post snapshot (local JSON, no remote access)")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-authorized native post CSV, merged only in memory")
    p.add_argument("--owner-authorized", action="store_true", help="Explicitly confirm the metrics CSV was lawfully exported from your own account")
    p.add_argument("--engagement-export", type=Path, help="Existing owner-exported engagement JSON; only aggregate labels are returned")
    p.add_argument("--seo-briefs", type=Path, help="Local SEO export already used by seo-destinations")
    p.add_argument("--experiments", type=Path, help="Local classified experiment results JSON list")
    p.add_argument("--include-db", action="store_true", help="Explicitly read an EXISTING local SQLite DB in query-only mode")
    p.add_argument("--limit", type=int, default=10, help="Show 1..50 ranked review tasks (default 10)")
    p.add_argument("--json", action="store_true", help="Machine-readable curated findings; no raw comments or profile text")
    p = sub.add_parser("weekly-digest", help="Write suggest (and with --include-db the local report) as one private Markdown file; "
                       "local only, never sent")
    p.add_argument("--out", type=Path, required=True, help="NEW Markdown file (mode 0600); refuses an existing file or symlink")
    p.add_argument("--snapshot", type=Path, help="Owner-supplied profile/post snapshot (local JSON, no remote access)")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-authorized native post CSV, merged only in memory")
    p.add_argument("--owner-authorized", action="store_true", help="Confirm the metrics CSV was lawfully exported from your own account")
    p.add_argument("--engagement-export", type=Path, help="Existing owner-exported engagement JSON; only aggregate labels are used")
    p.add_argument("--seo-briefs", type=Path, help="Local SEO export already used by seo-destinations")
    p.add_argument("--experiments", type=Path, help="Local classified experiment results JSON list")
    p.add_argument("--include-db", action="store_true", help="Also read an EXISTING local SQLite DB in query-only mode (queue health and report)")
    p.add_argument("--limit", type=int, default=10, help="Show 1..50 ranked review tasks (default 10)")
    p = sub.add_parser("profile-audit", help="Offline owner-snapshot, per-platform profile readiness and fix list; no network")
    p.add_argument("--file", type=Path, required=True, help="Local JSON snapshot, or a profile .csv (platform,account_id,handle,profile_kind,keywords,field,value); no account login or network")
    p.add_argument("--platform", help="Only this platform from the profile-audit registry")
    p.add_argument("--json", action="store_true", help="Detailed machine-readable checklist with evidence and coverage")
    p = sub.add_parser("post-audit", help="Offline platform-specific creative checks and comparable-age outcome summaries")
    p.add_argument("--metrics-csv", type=Path, help="Optional normalized owner-exported post observations; no DB/network writes")
    p.add_argument("--owner-authorized", action="store_true", help="Confirm the CSV is your own authorized export")
    p.add_argument("--file", type=Path, required=True, help="Same owner-supplied profile/post snapshot")
    p.add_argument("--platform", help="Limit to one registered platform")
    p.add_argument("--age-hours", type=int, default=168, help="Native metric post age 24..720 hours (default 168)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Observation age tolerance 1..168 hours")
    p.add_argument("--min-posts", type=int, default=3, help="Minimum matched-age posts per format cohort 3..30")
    p.add_argument("--json", action="store_true", help="Detailed no-write JSON results")
    p = sub.add_parser("peer-benchmark", help="Offline: compare your public counters with peers' public counters that you copied by hand into a local file (#11); no fetching, no logins, nothing stored")
    p.add_argument("--file", type=Path, required=True, help="Local JSON (schema reachweaver.peer-benchmark.v1); see examples/peer-benchmark.example.json")
    p.add_argument("--platform", help="Only this platform id")
    p.add_argument("--include-individuals", action="store_true", help="Also accept kind: person entries (excluded by default; privacy check first)")
    p.add_argument("--now", help="Reference time for the staleness warning (ISO 8601 with offset); default: the current time")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("distribution-plan", help="Offline regional channel research and hypothetical timing windows; NEVER schedules/posts")
    p.add_argument("--region", required=True, help="Target country/market, e.g. india, china, nigeria or global")
    p.add_argument("--goal", choices=GOALS, required=True, help="One measurable audience objective")
    p.add_argument("--limit", type=int, default=5, help="Number of candidate channels (1..20)")
    p.add_argument("--date", help="YYYY-MM-DD audience-local day for illustrative time tests, not an actual queue")
    p.add_argument("--timezone", help="IANA timezone override, required with --date for global audiences")
    p.add_argument("--market", action="append", default=[], metavar="MARKET",
                   help="Audience market for market-specific notices: US, global or PLATFORM@MARKET (tiktok@US)")
    p.add_argument("--json", action="store_true", help="Return machine-readable RESEARCH-ONLY plan")
    p = sub.add_parser("social-web-plan", help="Offline open social web map: which accounts are on ActivityPub, AT "
                       "Protocol, Nostr or Farcaster, bridges, canonical home, duplicate reach, identity checklist; "
                       "NEVER sends or touches the queue")
    p.add_argument("--accounts", dest="social_accounts", type=Path, metavar="A",
                   help="PUBLIC accounts config (same as global --accounts); a missing file counts as no accounts")
    p.add_argument("--setup", type=Path, metavar="FILE",
                   help="Your own setup JSON (schema reachweaver.social-web-setup.v1): site, extra presences, "
                        "bridges, fediverse sharing, identity steps done; see examples/social-web-setup.example.json")
    p.add_argument("--presence", action="append", default=[], metavar="PLATFORM",
                   help="A network you are on that accounts.json cannot hold, by registry id (e.g. pixelfed, ghost, "
                        "nostr); repeat")
    p.add_argument("--bridged", action="append", default=[], metavar="ID",
                   help="Account id (or platform id) you bridged with Bridgy Fed, an opt-in bridge; repeat")
    p.add_argument("--fediverse-sharing", action="append", default=[], metavar="ID",
                   help="Threads account id (or 'threads') with fediverse sharing turned on; repeat")
    p.add_argument("--site", metavar="URL", help="Your owned site or newsletter, https:// only")
    p.add_argument("--site-kind", choices=("ghost", "wordpress_activitypub", "bridgy_fed_web", "plain"),
                   help="How the site connects to the open social web (default plain)")
    p.add_argument("--json", action="store_true", help="Machine-readable plan (schema reachweaver.social-web-plan.v1)")
    p = sub.add_parser("timing-evaluate", help="Read-only per-account matched-age outcome analysis; NOT optimal-time proof")
    p.add_argument("--account", required=True, help="Configured account ID; exact cohort only")
    p.add_argument("--timezone", required=True, help="Audience IANA timezone for posting time groups")
    p.add_argument("--source", required=True, help="Exact source of manually imported native snapshots")
    p.add_argument("--metric", choices=METRIC_FIELDS, required=True, help="Goal numerator, e.g. clicks")
    p.add_argument("--denominator", choices=("views", "impressions"), required=True)
    p.add_argument("--age-hours", type=int, default=168, help="Compare post ages (24..720 hours)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Observation tolerance, 1..168 hours")
    p.add_argument("--min-posts-per-slot", type=int, default=3, help="Min comparable posts per time group")
    p.add_argument("--campaign", help="Optional narrow cohort; important for topic/format comparability")
    p.add_argument("--since", help="Optional YYYY-MM-DD audience-local first date")
    p.add_argument("--until", help="Optional YYYY-MM-DD audience-local final date")
    p.add_argument("--stratify", choices=("format", "campaign"),
                   help="Also report each format (text/image/video) or campaign separately; a stratum with "
                        "too little data names no winner and points to calendar-plan's generic baseline")
    p = sub.add_parser("baseline-eval", help="Read-only: personalized slots/variant campaigns/readouts vs "
                       "calendar-plan's generic baseline over --since/--until; bootstrap CI + Holm; non-causal")
    p.add_argument("--account", required=True, help="Configured account ID; exact cohort only")
    p.add_argument("--timezone", required=True, help="Audience IANA timezone for slots and the period")
    p.add_argument("--source", help="Exact source of imported native snapshots (required without --attribution)")
    p.add_argument("--metric", choices=METRIC_FIELDS, help="Goal numerator, e.g. clicks (required without --attribution)")
    p.add_argument("--denominator", choices=("views", "impressions"), help="Required without --attribution")
    p.add_argument("--attribution", type=Path, metavar="ATTR.json",
                   help="Saved 'attribution-report --json' output: use each post's attributed count (--outcome) "
                        "instead of --source/--metric/--denominator; a post without one is excluded, never 0")
    p.add_argument("--outcome", choices=("docs_visits", "repo_stars_utm"),
                   help="With --attribution: docs_visits = attributed sessions, repo_stars_utm = attributed conversions")
    p.add_argument("--attribution-source", choices=ATTRIBUTION_SOURCES, default="analytics",
                   help="Which section of --attribution to read: analytics (the --analytics join, default) or ga4 "
                        "(the --ga4-csv join; key events count as conversions). Never mixed")
    p.add_argument("--since", required=True, help="First audience-local send date YYYY-MM-DD (inclusive)")
    p.add_argument("--until", required=True, help="Last audience-local send date YYYY-MM-DD (inclusive)")
    p.add_argument("--slot", action="append", help="Personalized arm [explore:|exploit:]Wed@16:00; repeat, one comparison each")
    p.add_argument("--campaign-arm", action="append", help="Variant campaign arm [explore:|exploit:]CAMPAIGN; repeat")
    p.add_argument("--readout", action="append", type=Path, help="Saved 'experiment-readout --json' file; repeat")
    p.add_argument("--age-hours", type=int, default=168, help="Matched post age (24..720 hours)")
    p.add_argument("--tolerance-hours", type=int, default=24, help="Snapshot age tolerance, 1..168 hours")
    p.add_argument("--slot-tolerance-minutes", type=int, default=60, help="Send-time match around a slot, 0..120")
    p.add_argument("--min-posts", type=int, default=5, help="Minimum posts with a rate per group, 3..100")
    p.add_argument("--resamples", type=int, default=2000, help="Bootstrap/permutation resamples, 200..20000")
    p.add_argument("--confidence", type=float, default=0.95, help="Interval level; alpha = 1 - confidence")
    p.add_argument("--seed", type=int, default=16, help="Fixed seed: same inputs give identical output")
    p.add_argument("--json", action="store_true")
    insights.add_parser(sub)  # insights (reachweaver/insights.py)
    platform_risk.add_parser(sub)  # platform-risk (reachweaver/platform_risk.py)
    review_evidence.add_parser(sub)  # eu-review-evidence (reachweaver/review_evidence.py)
    p = sub.add_parser("experiment-evaluate", help="Matched-age, non-causal readout of preregistered post pairs")
    p.add_argument("--file", type=Path, required=True)
    p = sub.add_parser("experiment-preregister",
                       help="Offline: validate a growth plan and write a NEW hashed preregistration; never publishes")
    p.add_argument("plan", type=Path, help="Growth plan JSON (examples/growth-plan.example.json)")
    p.add_argument("--out", type=Path, required=True, help="NEW 0600 record file; an existing file is never overwritten")
    p = sub.add_parser("experiment-verify", help="Offline: detect post-hoc edits by recomputing a plan's sha256 hash")
    p.add_argument("plan", type=Path, help="Original growth plan JSON, or the preregistration record")
    p.add_argument("--hash", required=True, dest="expected_hash", help="Registered plan_hash, sha256:<hex>")
    p = sub.add_parser("experiment-readout", help="Read-only: verify a preregistration, map arms to posts, label the "
                                                  "matched-age readout; non-causal")
    p.add_argument("--prereg", type=Path, required=True, help="Record written by experiment-preregister")
    p.add_argument("--posts", type=Path, required=True, help="reachweaver.growth-post-map JSON: arm id -> post id per pair")
    p.add_argument("--hash", dest="expected_hash", help="Optional externally kept plan_hash that must also match")
    p.add_argument("--attribution", type=Path, metavar="ATTR.json",
                   help="Saved 'attribution-report --json' output; required for a docs_visits or repo_stars_utm "
                        "primary metric (per-post attributed sessions/conversions; unknown stays unknown, never 0)")
    p.add_argument("--attribution-source", choices=ATTRIBUTION_SOURCES, default="analytics",
                   help="Which section of --attribution to read: analytics (the --analytics join, default) or ga4 "
                        "(the --ga4-csv join; key events count as conversions). Never mixed")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("experiment-next", help="Offline: deterministic next dimension from prior classified results")
    p.add_argument("--results", type=Path, help="JSON list of {experiment_id, dimension, label, direction}")
    p.add_argument("--readout", action="append", type=Path, metavar="FILE",
                   help="Saved 'experiment-readout --json' file; its label and direction are used unchanged; repeat")
    p.add_argument("--readout-dir", type=Path, metavar="DIR",
                   help="Folder of saved 'experiment-readout --json' files (*.json; other JSON files are listed "
                        "as ignored). Give --results, --readout or --readout-dir")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("experiment-simulate", help="Offline SIMULATED label distribution for a seeded synthetic experiment")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--pairs", type=int, required=True, help="Pairs per synthetic experiment (2..100)")
    p.add_argument("--lift", type=float, required=True, help="True relative lift of the treatment, e.g. 0 or 0.2")
    p.add_argument("--noise", type=float, required=True, help="Per-post log-normal noise sd, e.g. 0.3")
    p.add_argument("--trials", type=int, default=1000, help="Synthetic experiments to run (1..20000)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("pilot-report",
                       help="Offline per-arm readout of an owner field-pilot CSV log; descriptive, never causal")
    p.add_argument("--log", type=Path, required=True, help="Operations log CSV (examples/pilot-log.template.csv)")
    p.add_argument("--min-weeks", type=int, default=DEFAULT_MIN_WEEKS,
                   help=f"Refuse the arm comparison unless every logged arm covers N ISO weeks (default {DEFAULT_MIN_WEEKS})")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p = sub.add_parser("schema-validate", help="Offline canonical schema check (1.0, 1.1 or 1.2) of one JSON document")
    p.add_argument("--type", required=True, choices=SCHEMA_TYPES, dest="schema_type")
    p.add_argument("file", type=Path)
    p = sub.add_parser("schema-export", help="Read-only: local outbox rows as canonical schema JSON lines; "
                       "unmapped rows listed with reasons; never writes the DB or publishes")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) .jsonl file; never overwritten; default stdout")
    p.add_argument("--type", choices=EXPORT_TYPES, dest="export_type", help="Export only this document type")
    p = sub.add_parser("engagement-triage", help="Offline comment/mention triage + reply/issue DRAFTS; no send path")
    p.add_argument("input", type=Path, help="Owner-exported normalized engagement JSON")
    p.add_argument("--docs-map", type=Path, help="Owner docs map: topic keywords -> HTTPS docs URL")
    p.add_argument("--out", type=Path, help="New .json or .md file for the triage + drafts; never overwritten")
    p.add_argument("--issues-dir", type=Path, help="Write bug/feature GitHub issue DRAFTS here; never filed")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable report")
    p.add_argument("--optout", type=Path, help=optout_help + "; listed authors get no draft")
    p = sub.add_parser("engagement-queue", help="Offline aging/SLA review queue over an engagement export; read-only, no send path")
    p.add_argument("input", type=Path, help="Owner-exported normalized engagement JSON")
    p.add_argument("--docs-map", type=Path, help="Owner docs map, as for engagement-triage")
    p.add_argument("--optout", type=Path, help=optout_help + "; listed authors are never queued")
    p.add_argument("--now", help="ISO 8601 time with timezone for the age calculation (default: current UTC time)")
    p.add_argument("--sla", action="append", metavar="LABEL=HOURS", help="Override one label's overdue target in hours; repeatable")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable queue")
    followups.add_parser(sub)  # followup-suggest (reachweaver/followups.py)
    engage_scout.add_parser(sub)  # engage-scout, engage-log (reachweaver/engage_scout.py)
    p = sub.add_parser("data-export", help="Offline JSON export of your local DB with secret-looking text redacted")
    p.add_argument("--out", type=Path, help="NEW owner-only (0600) file; default prints to stdout")
    p = sub.add_parser("data-purge", help="Retention purge plan (dry run); --apply deletes/minimizes local rows only")
    p.add_argument("--policy", type=Path, help="Retention JSON (default $REACHWEAVER_RETENTION_POLICY or config/retention.json)")
    p.add_argument("--apply", action="store_true", help="Explicitly apply the plan; never touches active/in-flight posts")
    p = sub.add_parser("privacy-status", help="Offline DB permissions, size, row counts, oldest records, retention config")
    p.add_argument("--policy", type=Path, help="Retention JSON to validate (same defaults as data-purge)")
    p = sub.add_parser("utm-link", help="Offline canonical UTM-tagged destination URL; no network")
    p.add_argument("url")
    p.add_argument("--platform", required=True, help="utm_source; a ReachWeaver platform id")
    p.add_argument("--campaign", required=True, help="utm_campaign; slugged campaign id")
    p.add_argument("--content", help="utm_content; post id or campaign variant id")
    p.add_argument("--term", help="Optional utm_term")
    p.add_argument("--allow-origin", nargs="+", help="Reject destinations outside these https origins")
    p.add_argument("--sources", type=Path, metavar="CONFIG",
                   help="sources-scan config; refuse a destination inside any visibility:private repository")
    p = sub.add_parser("attribution-report", help="Read-only UTM/referrer join of local analytics exports; NOT causal")
    p.add_argument("--analytics", type=Path,
                   help="Website-analytics CSV export (one of --analytics, --ga4-csv or --gsc-csv is required)")
    p.add_argument("--github-traffic", type=Path, action="append",
                   help="Saved traffic/views or popular/referrers JSON; repeat for both")
    p.add_argument("--ga4-csv", type=Path, action="append", metavar="CSV",
                   help="GA4 report or exploration downloaded as CSV (Session source / medium, Session campaign, "
                   "Session manual ad content, Sessions, ...); repeat for non-overlapping periods. Local file only")
    p.add_argument("--gsc-csv", type=Path, action="append", metavar="CSV",
                   help="Search Console Performance export table (Pages, Queries, Dates or Filters CSV); repeat, "
                   "one per table. Search-side context only, never credited to a post. Local file only")
    p.add_argument("--json", action="store_true", help="Full machine-readable report")
    p = sub.add_parser("seo-destinations", help="Offline canonical landing page + competing-message check")
    p.add_argument("--briefs", type=Path, required=True, help="Local SEO briefs JSON (docs/SEO-INTEGRATION.md)")
    p.add_argument("--type", choices=CAMPAIGN_TYPES, required=True)
    p.add_argument("--project", help="Restrict to briefs for this project")
    p.add_argument("--platform", help="With --campaign: also emit a UTM link for the destination")
    p.add_argument("--campaign", help="With --platform or --suggest-for: utm_campaign for the destination link")
    p.add_argument("--suggest-for", action="append", metavar="PLATFORM",
                   help="Also suggest, per platform, how a post points at the destination: link and where to put "
                   "it, length, keyword, tags, alt text; each with its basis (official/unverified/repo_policy/"
                   "hypothesis). Repeat for several platforms")
    p = sub.add_parser("creator-match", help="Offline creator relevance shortlist from owner-curated public-work evidence; "
                       "contacts nobody, predicts no reach (docs/CREATOR-AMPLIFICATION.md)")
    p.add_argument("--file", type=Path, required=True, help="Local owner-curated candidates JSON (reachweaver.creator-candidates.v1); never fetched from platforms")
    p.add_argument("--json", action="store_true", help="Detailed local evidence coverage and editorial explanations")
    p = sub.add_parser("creator-kit", help="Write one NEW private source kit for independent creator commentary; never sent "
                       "(docs/CREATOR-AMPLIFICATION.md)")
    p.add_argument("--file", type=Path, required=True, help="Owner-attested claims, evidence, rights and questions JSON (reachweaver.creator-source.v1)")
    p.add_argument("--out", type=Path, required=True, help="NEW private directory (0700) for owner-review JSON and Markdown (0600); refused if it exists")
    p.add_argument("--json", action="store_true", help="Machine-readable local file paths and review-only status")
    p = sub.add_parser("creator-coverage", help="Offline owner-confirmed creator pickup counts at 48h/7d; never fetches analytics "
                       "(docs/CREATOR-AMPLIFICATION.md)")
    p.add_argument("--file", type=Path, required=True, help="Local derivative-coverage observations JSON (reachweaver.creator-coverage.v1)")
    p.add_argument("--json", action="store_true", help="Machine-readable counts and missingness")
    p = sub.add_parser("creative-preflight", help="Offline editorial checklist for 1-6 draft posts; not a reach prediction, "
                       "never publishes (docs/CREATIVE-PREFLIGHT.md)")
    p.add_argument("--file", type=Path, required=True, help="Owner-controlled JSON brief (reachweaver.creative-preflight.v1), at most 256 KB")
    p.add_argument("--voice-profile", type=Path, metavar="PROFILE", help="Also add advisory voice-check findings per draft "
                   "from a voice-profile file (docs/VOICE-CHECK.md)")
    p.add_argument("--json", action="store_true", help="Machine-readable checklist, findings and limitations")
    p = sub.add_parser("voice-profile", help="Statistics of how you write, from your own past posts; writes one NEW "
                       "private file, stores no post text (docs/VOICE-CHECK.md)",
                       description="Offline. Reads posts you supply (--from FILE: .json, .csv, or text with posts "
                       "separated by a line holding only ---) or your published posts in the local database "
                       "(--from-db, opened read-only) and writes a NEW 0600 JSON profile of plain statistics. Needs "
                       "at least 10 posts; with fewer it reports insufficient_sample, writes nothing and exits 1.")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--from", dest="from_file", type=Path, metavar="FILE", help="Local file of your own posts")
    source.add_argument("--from-db", action="store_true", help="Your published posts in the local database (read-only)")
    p.add_argument("--account", help="With --from-db: only this account's posts")
    p.add_argument("--platform", help="Only posts for this platform (unlabelled file posts count as this platform)")
    p.add_argument("--out", type=Path, required=True, metavar="NEW_FILE", help="NEW profile file (mode 600); refused if it exists")
    p.add_argument("--json", action="store_true", help="Print the profile as JSON")
    p = sub.add_parser("voice-check", help="Advisory: compare a draft with your voice profile and a heuristic list of "
                       "stock AI-style phrases; never blocks (docs/VOICE-CHECK.md)",
                       description="Offline and advisory. Flags features of a draft outside the usual range of your "
                       "own posts and phrases or patterns from a heuristic style list (not an AI detector). Findings "
                       "are info or warn; it never blocks approval or publishing and exits 0 after a check. Without "
                       "--profile only the tell list and generic checks run.")
    p.add_argument("--profile", type=Path, help="Profile written by voice-profile (optional)")
    draft_source = p.add_mutually_exclusive_group(required=True)
    draft_source.add_argument("--text", help="Draft text")
    draft_source.add_argument("--post-id", help="A stored post (read-only database access)")
    draft_source.add_argument("--file", type=Path, help="UTF-8 text file with the draft")
    p.add_argument("--platform", help="Compare with this platform's length statistics (default: the stored post's platform)")
    p.add_argument("--json", action="store_true", help="Machine-readable findings")
    p = sub.add_parser("ai-label-check", help="Advisory: which AI-content label each target platform asks for "
                       "(sourced rules, EU AI Act Art. 50 note); never blocks (docs/AI-CONTENT-LABELS.md)",
                       description="Offline and advisory. Reads a draft's authorship (human, ai_assisted, "
                       "ai_generated) and its media flags (realistic_synthetic, provenance.created_by) and prints, per "
                       "target platform, label required / recommended / not required / unknown, what to tick in the "
                       "platform's own composer and the cited source. Exits 0 after a check, 2 on an input error. "
                       "Not legal advice; rules change, re-verify the cited page.")
    draft_source = p.add_mutually_exclusive_group(required=True)
    draft_source.add_argument("--post-id", help="A stored post (read-only database access)")
    draft_source.add_argument("--file", type=Path, help="Draft, variant or campaign JSON (authorship and platform read from it)")
    p.add_argument("--platform", action="append", metavar="ID", help="Target platform(s), e.g. youtube,instagram; "
                   "replaces the draft's own platform; repeat or comma-separate")
    p.add_argument("--media", type=Path, help="Media manifest JSON (realistic_synthetic and provenance per asset)")
    p.add_argument("--authorship", choices=("human", "ai_assisted", "ai_generated"),
                   help="Who wrote the text (overrides the file; a stored post has none recorded)")
    p.add_argument("--eu", action="store_true", help="Also check EU AI Act Art. 50 (you publish from, or for an "
                   "audience in, the EU); secondary-sourced, not legal advice")
    p.add_argument("--public-interest", choices=("yes", "no"), help="With --eu: does the text inform the public "
                   "on matters of public interest?")
    p.add_argument("--editorial-review", choices=("yes", "no"), help="With --eu: did a person review or edit the "
                   "AI text and hold editorial responsibility for it?")
    p.add_argument("--json", action="store_true", help="Machine-readable advice")
    p = sub.add_parser("account-advisor", help="Offline channel shortlist and setup coaching; creates no account",
                       description="Offline shortlist of social channels for a region, goal, format and weekly time, "
                       "plus optional account-setup steps you do yourself and a coaching prompt for a normal Claude "
                       "chat. Contacts no site, reads no credential, never creates or connects an account "
                       "(docs/ACCOUNT-ADVISOR.md).")
    p.add_argument("--region", default="global", help="Distribution registry market, e.g. global, india, china (default global)")
    p.add_argument("--goal", default="awareness", choices=GOALS, help="Audience objective (default awareness)")
    p.add_argument("--format", default="mixed", choices=ADVISOR_FORMATS, help="Content you will make (default mixed)")
    p.add_argument("--weekly-hours", type=int, default=6, help="Available weekly effort, 1..40 estimated hours (default 6)")
    p.add_argument("--limit", type=int, default=4, help="Number of recommended new channels, 1..8 (default 4)")
    p.add_argument("--existing", action="append", default=[], help="Platform ID you already use (your statement, not verified); repeat for each")
    p.add_argument("--brand", default="My project", help="Public-safe brand name, not private data")
    p.add_argument("--audience", default="People interested in my topic", help="Public-safe audience description")
    p.add_argument("--language", default="en", help="Public content language, e.g. en or pt-BR")
    p.add_argument("--setup", metavar="PLATFORM_ID", help="Print the official starting link, the steps you do yourself and a "
                   "coaching prompt for a normal Claude chat that never opens or operates the site")
    p.add_argument("--json", action="store_true", help="Machine-readable recommendations and optional setup steps")
    p = sub.add_parser("profile", help="Your private profile: personas (work, family...), a username per network and the "
                       "details sign-up forms ask for; local file, no network",
                       description="Create, show (e-mail, phone and birth date partly hidden), check and edit "
                       "config/profile.json. It never holds passwords, tokens, codes or identity documents "
                       "(docs/PERSONAS-AND-ACCOUNT-KIT.md).")
    p.add_argument("--init", action="store_true", help="Create a new private profile (mode 600); refuses to overwrite")
    p.add_argument("--persona", action="append", default=[], metavar="ID", help="With --init: a persona to create; repeat (default: work)")
    p.add_argument("--show", action="store_true", help="Print the profile as JSON with e-mail, phone and birth date partly hidden")
    p.add_argument("--check", action="store_true", help="Validate the file, its permissions and its agreement with accounts.json; exit 1 on a finding")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE", help="Set person.FIELD, personas.ID.FIELD, personas.ID.bio.LANG or preferences.default_persona; empty VALUE clears it; repeat")
    p.add_argument("--handle", action="append", default=[], metavar="PLATFORM:PERSONA=NAME[:STATUS]", help="Record the username of a persona on a network (status planned, claimed or unavailable; default claimed); your statement, never verified; repeat")
    p = sub.add_parser("account-route", help="Which account and username to use for a persona on a network; says which account to switch to",
                       description="Read-only and offline: looks up accounts.json and the profile. Exit 0 when a username is known, "
                       "1 when it is missing or the two files disagree.")
    p.add_argument("--platform", required=True, help="Network, for example instagram or weibo")
    p.add_argument("--persona", metavar="ID", help="Persona id (default: preferences.default_persona in the profile)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("account-kit", help="Prepare sign-up details for accounts you create yourself: one plan, one approval; "
                       "creates no account",
                       description="Offline. Builds one plan for the named platforms and one persona from your profile "
                       "(only the details each form may ask for, in the platform's interface language) and prints it with "
                       "personal values partly hidden. One approval of the plan hash writes the packets to a NEW private "
                       "folder. It opens no site, fills no form and never sees a password, CAPTCHA or code "
                       "(docs/PERSONAS-AND-ACCOUNT-KIT.md).")
    p.add_argument("--platform", action="append", default=[], metavar="ID", help="Network to prepare; repeat or comma-separate (see account-advisor for the ids)")
    p.add_argument("--persona", metavar="ID", help="Persona id (default: preferences.default_persona in the profile)")
    p.add_argument("--questions", action="store_true", help="Print only what is still missing from the profile")
    p.add_argument("--out", type=Path, metavar="NEW_DIR", help="Write the packets here (a folder that does not exist yet); needs --approve-plan")
    p.add_argument("--approve-plan", metavar="SHA256", help="The first 12+ characters of the plan sha256 you reviewed: your single approval for every platform in the plan")
    p.add_argument("--json", action="store_true", help="Machine-readable plan (personal values partly hidden)")
    p = sub.add_parser("account-add", help="Add one account you own to accounts.json (public details only; asks for "
                       "missing ones in a terminal); never asks for or stores a password or token",
                       description="Offline. Appends one correctly shaped entry to config/accounts.json (or --accounts), "
                       "checks it with the normal loader, refuses duplicates and example placeholders, applies the "
                       "wrong-account (persona) guard and keeps the previous file as accounts.json.bak. It prints the "
                       "environment variable NAMES to set yourself and points to 'connect --guide'.")
    p.add_argument("platform", choices=ACCOUNT_ADD_PLATFORMS, metavar="PLATFORM",
                   help="One of " + ", ".join(ACCOUNT_ADD_PLATFORMS))
    p.add_argument("--id", help="Account id (default my-PLATFORM, or my-PLATFORM-2 when taken)")
    p.add_argument("--handle", help="Public username (Bluesky: the full handle such as name.bsky.social)")
    p.add_argument("--server", help="Mastodon server origin, e.g. https://mastodon.social")
    p.add_argument("--field", action="append", default=[], metavar="KEY=VALUE",
                   help="Another public field of this network (chat_id, bot_username, person_id, linkedin_version...); "
                        "secret-named keys are refused; repeat")
    p.add_argument("--persona", metavar="ID", help="Which of your personas this account is (work, family...); checked "
                   "against your profile")
    p.add_argument("--env-prefix", metavar="REACHWEAVER_NAME", help="Credential variable prefix (default: the example's "
                   "prefix for the first account of a network, else derived from the id)")
    p.add_argument("--dry-run", action="store_true", help="Print the entry and checks; write nothing")
    p = sub.add_parser("account-remove", help="Remove one account entry from accounts.json (keeps accounts.json.bak); "
                       "never touches credentials, the database, approvals or disconnect state",
                       description="Offline. Removes one entry from config/accounts.json (or --accounts) after you "
                       "confirm it in a terminal (elsewhere pass --yes). The previous file is kept as "
                       "accounts.json.bak. Posts recorded for the account stay in the database (counted read-only); "
                       "credential variables are yours to remove.")
    p.add_argument("id", metavar="ID", help="The account id, as listed by doctor or connect")
    p.add_argument("--yes", action="store_true", help="Confirm without a prompt (needed outside a terminal)")
    p.add_argument("--dry-run", action="store_true", help="Show what would be removed; write nothing")
    p = sub.add_parser("discovery-plan", help="Offline platform discovery and topic/hashtag recommendations; not a reach forecast or publisher")
    p.add_argument("--file", type=Path, required=True, help="Owner-reviewed JSON with post text, topics and optional dated trend evidence")
    p.add_argument("--account", metavar="ID", help="Show only this account's own results per tag (default: every "
                                                   "account on the plan's platform); read from an existing database "
                                                   "read-only, never re-ranked")
    p.add_argument("--json", action="store_true", help="Evidence, selected/skipped tags and creative checklist")
    p = sub.add_parser("seo-content-check", help="Discoverability checks on owner-supplied post texts (length, alt text, hashtags, "
                       "UTM links, name spelling, cross-platform duplicates); offline unless --online link previews; read-only, never publishes")
    p.add_argument("file", type=Path, help="Local JSON: {\"items\": [{id, platform, text, alt_text?, media_count?, link?, keywords?}], \"names\"?: [...]}")
    p.add_argument("--name", action="append", metavar="NAME", help="Canonical spelling of your project/brand name; variants are flagged; repeat")
    p.add_argument("--online", action="store_true", help="Also check each item's link preview (Open Graph) and liveness: robots.txt first, one GET per distinct page")
    p.add_argument("--allow-origin", action="append", metavar="ORIGIN", help="With --online: also follow redirects to this https origin; repeat")
    p.add_argument("--json", action="store_true", help="Full machine-readable report")
    p = sub.add_parser("media-check", help="Offline media manifest checks: headers, platform limits, alt text, captions, rights; never uploads")
    p.add_argument("manifest", type=Path, help="Media manifest JSON; asset paths are relative to it")
    p.add_argument("--json", action="store_true", help="Machine-readable errors/warnings report")
    p = sub.add_parser("media-cutlist", help="Offline: one master video -> per-platform cut list (segments that fit each "
                       "platform's sourced duration limit, crop to the target aspect, subtitle format, a suggested "
                       "ffmpeg command); opens and edits no media")
    p.add_argument("plan", type=Path, help="Cut-list JSON (docs/MEDIA-ASSETS.md#cut-list-media-cutlist)")
    p.add_argument("--json", action="store_true", help="Machine-readable cut list")
    p = sub.add_parser("media-storyboard", help="Offline: campaign brief -> vertical-video storyboard, SRT skeleton, "
                       "alt-text placeholders, image specs, media manifest, safe-area check; generates no media")
    p.add_argument("--campaign", type=Path, help="Campaign JSON (campaign contract or signal-brief scaffold)")
    p.add_argument("--out", type=Path, help="NEW output folder (0700, files 0600); refuses existing output")
    p.add_argument("--check", type=Path, metavar="STORYBOARD", help="Re-check an edited storyboard.json: safe areas, "
                   "subtitle timing and unresolved placeholders")
    p.add_argument("--duration", type=int, default=30, help="Video length in seconds, 10..180 (default 30)")
    p.add_argument("--aspect", default="9:16", help="Frame aspect W:H (default 9:16)")
    p.add_argument("--video-targets", nargs="+", default=["youtube_shorts", "tiktok", "instagram"])
    p.add_argument("--image-targets", nargs="+", default=["linkedin"])
    p.add_argument("--locale", default="en", help="BCP-47 subtitle/alt-text locale (default en)")
    p.add_argument("--fit", choices=("cover", "contain"), default="cover",
                   help="Assumed player fit for non-9:16 frames: centered crop (default) or letterbox")
    p.add_argument("--safe-zones", type=Path, help="Owner UI-overlay zones JSON overriding the built-ins (sourced from paid-ad specs, accessed 2026-10-09)")
    p.add_argument("--json", action="store_true", help="Machine-readable result")
    p = sub.add_parser("share", help="Easy offline handoff: choose social networks, type a caption and get copy-ready text and checklists; never sends")
    p.add_argument("--platform", action="append", metavar="NAME",
                   help="Social network(s), e.g. instagram,threads or --platform tiktok --platform youtube")
    p.add_argument("--list", action="store_true", help="Show friendly example network names; no network")
    p.add_argument("--guide", action="store_true", help="Ask simple questions in your terminal; no login required")
    body = p.add_mutually_exclusive_group()
    body.add_argument("--text", help="Caption to prepare (one exact text, reviewed separately per platform)")
    body.add_argument("--file", type=Path, help="Existing local UTF-8 file with the caption, including line breaks")
    p.add_argument("--note", action="append", help="Short idea for human-only groups; never used as ready-made post text")
    p.add_argument("--title", help="YouTube video title when a YouTube destination is selected")
    p.add_argument("--format", choices=("text", "image", "video", "carousel", "link"),
                   help="Override a platform's inferred format (image/video requires manual media attachment)")
    p.add_argument("--attach", action="append", type=Path, help="A local photo/video you will attach yourself in each app; "
                   "its header is checked offline against each network's sourced limits (size, duration, aspect "
                   "ratio...), never uploaded")
    p.add_argument("--media", type=Path, help="Optional advanced media-check manifest for existing validation")
    p.add_argument("--out", type=Path, help="New output folder (default: a unique private folder under state/)")
    p.add_argument("--preview", action="store_true", help="Check without writing any files")
    p.add_argument("--no-shorten", action="store_true",
                   help="Keep a caption that is too long for a network as written (a blocking error) instead of "
                        "shortening it at a word boundary with a SHORTENED flag")

    p.add_argument("--web", action="store_true",
                   help="Start the mobile-friendly browser composer on 127.0.0.1 (manual, offline)")
    p.add_argument("--open", action="store_true",
                   help="With --web, open the local composer in your default browser")
    p.add_argument("--port", type=int, default=0,
                   help="With --web, local port; 0 chooses a free port (default)")


    p = sub.add_parser("manual-export", help="Offline creator-ready package for Threads/Instagram/Facebook Pages/YouTube/TikTok, or a generic package for any other manual_only distribution-registry destination; never sends")
    p.add_argument("--platform", required=True, choices=MANUAL_PLATFORMS + generic_platforms(), metavar="PLATFORM",
                   help="threads, instagram, facebook, youtube or tiktok (sourced package), or any other manual_only "
                        "id in the distribution registry such as dev, x, product_hunt or hacker_news (generic package; "
                        "human-only destinations get a notes form, never post text). Chinese platforms such as "
                        "weibo, xiaohongshu, douyin, bilibili and wechat add sourced limits, topics and the "
                        "AI/ad declarations (docs/CHINA.md)")
    p.add_argument("--variant", type=Path, required=True, help="Variant JSON: text, format, link_url, platform_options; talking_points for human-only destinations")
    p.add_argument("--media", type=Path, help="Media manifest JSON validated with media-check rules")
    p.add_argument("--account-meta", type=Path, help="PUBLIC account metadata JSON (handle, account_type); no secrets")
    p.add_argument("--out", type=Path, required=True, help="NEW output folder (0700, files 0600); refuses existing output")
    p.add_argument("--json", action="store_true", help="Print the full machine-readable package")
    p = sub.add_parser("manual-export-batch", help="Offline: preflight 1-25 manual-export items as a whole, then write one package folder each with SHA-256 of the exact text; never sends or schedules")
    p.add_argument("--file", type=Path, required=True, help="Batch JSON: schema_version 1 and items of platform, variant, optional account_meta and at (advisory time with offset); human-only destinations refused")
    p.add_argument("--out", type=Path, required=True, help="NEW output folder (0700, files 0600); one blocking error anywhere writes nothing")
    p.add_argument("--json", action="store_true", help="Print the batch manifest as JSON")
    p = sub.add_parser("delivery-options", help="Offline delivery route per distribution-registry channel (official API adapter, manual package, human-only notes or unsupported); never connects, posts or schedules")
    p.add_argument("--platform", help="One distribution-registry channel id (default: every channel)")
    p.add_argument("--json", action="store_true", help="Machine-readable routes")
    p = sub.add_parser("claims-check", help="Offline LEXICAL claim grounding of variants vs declared evidence; not truth verification")
    p.add_argument("campaign", type=Path, help="Campaign JSON file")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON (banned_phrases, required_disclosure)")
    p.add_argument("--duplicate-threshold", type=float, default=DEFAULT_DUPLICATE_THRESHOLD,
                   help="Word 3-gram Jaccard at which variants are near-duplicates, (0,1] (default 0.6)")
    p.add_argument("--json", action="store_true", help="Machine-readable grounding report")
    p.add_argument("--optout", type=Path, help=optout_help + "; a mention is an error")
    p = sub.add_parser("sources-scan", help="Rank opt-in GitHub/feed/changelog/DOI source signals; local fixtures unless --online; never drafts/publishes")
    p.add_argument("--config", type=Path, required=True, help="sources.reachweaver.json opt-in source list")
    p.add_argument("--online", action="store_true",
                   help="Explicit GET of public GitHub API, https feeds/changelogs (robots.txt honoured) and Crossref; default reads local fixtures")
    p.add_argument("--state", type=Path, help="Optional JSON state of previously seen signal IDs (novelty)")
    p.add_argument("--mark-seen", action="store_true", help="Record shown candidate IDs in --state (local file only)")
    p.add_argument("--allow-private", action="store_true",
                   help="Third private opt-in: also read sources that set visibility AND sensitivity private "
                        "(GitHub, token required); their signals are marked private, never public evidence")
    p.add_argument("--top", type=int, default=10, help="Ranked signals to show (1..100, default 10)")
    p.add_argument("--json", action="store_true", help="Machine-readable RESEARCH-ONLY signals")
    p = sub.add_parser("signal-brief", help="Offline: one sources-scan signal -> NEW campaign scaffold + .brief.md; "
                       "placeholders block staging; no copy generated, never stages/publishes")
    p.add_argument("--scan", type=Path, required=True, help="Saved 'sources-scan --json' output (untrusted data)")
    p.add_argument("--signal-id", required=True, help="Signal id, or a unique prefix of at least 8 hex characters")
    p.add_argument("--brand", type=Path, required=True, help="Brand JSON (brand_id, approved_accounts)")
    p.add_argument("--out", type=Path, required=True,
                   help="NEW campaign .json (0600); a NEW sidecar .brief.md is written next to it; refuses overwrite")
    p.add_argument("--accounts", dest="brief_accounts", type=Path, metavar="A",
                   help="Optional PUBLIC accounts config; one placeholder variant per approved account with a "
                        "live or manual path (same as global --accounts)")
    p.add_argument("--objective", choices=sorted(OBJECTIVES), default="awareness",
                   help="Campaign objective (default awareness)")
    p.add_argument("--allow-duplicate", action="store_true",
                   help="Explicit human override to brief a near_duplicate or previously_seen signal")
    p.add_argument("--json", action="store_true", help="Machine-readable result (paths, validation, skipped accounts)")
    p = sub.add_parser("release-brief", help="Offline: a saved GitHub release JSON or CHANGELOG section -> NEW "
                       "campaign scaffold folder + brief.md; placeholders block staging; never stages/publishes")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--release", type=Path, help="Saved release JSON (gh api repos/OWNER/NAME/releases/latest "
                     "> release.json); untrusted data; ReachWeaver makes no network call")
    src.add_argument("--changelog", type=Path, help="Keep a Changelog CHANGELOG.md (use with --version)")
    p.add_argument("--version", dest="release_version", metavar="X.Y.Z", help="Changelog section to brief")
    p.add_argument("--repo", metavar="OWNER/NAME", help="Repository; must match the release URL; with --changelog "
                   "it gives the evidence URL when the section has no link reference")
    p.add_argument("--out", type=Path, required=True, help="NEW folder (0700) for campaign.reachweaver.json and "
                   "brief.md (0600); refuses an existing path")
    p.add_argument("--brand", type=Path, help="Brand JSON; with --accounts, one placeholder variant per approved account")
    p.add_argument("--accounts", dest="release_accounts", type=Path, metavar="A",
                   help="PUBLIC accounts config (needs --brand; same as global --accounts)")
    p.add_argument("--objective", choices=sorted(OBJECTIVES), default="awareness",
                   help="Campaign objective (default awareness)")
    p.add_argument("--campaign", metavar="ID", help="Campaign id (default release-REPO-TAG)")
    p.add_argument("--json", action="store_true", help="Machine-readable result (paths, validation, skipped accounts)")
    _add_review_parsers(sub, optout_help)
    p = sub.add_parser("capabilities", help="Offline platform capability/API-approval registry; never grants permission")
    p.add_argument("--platform", help="Show one platform, e.g. x, reddit or pinterest")
    p.add_argument("--json", action="store_true", help="Machine-readable full registry entries")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                   help=f"Warn when verified_date is older than N days (default {DEFAULT_MAX_AGE_DAYS})")
    p = sub.add_parser("registry-age", help="Offline age of every dated platform fact in the bundled research "
                                            "registries: fresh/aging (>60 days)/stale (>120 days)/undated; exit 0")
    p.add_argument("--registry", choices=tuple(REGISTRY_FILES), help="Only this registry (default: all nine)")
    p.add_argument("--platform", help="Only this platform id, e.g. x or facebook (facebook_pages matches facebook)")
    p.add_argument("--all", action="store_true", help="List fresh facts too (default: aging, stale and undated only)")
    p.add_argument("--as-of", metavar="YYYY-MM-DD", help="Evaluate at this date instead of today (reproducible output)")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p = sub.add_parser("landing-check", help="SEO brief + campaign CTA landing-page checks; offline unless --online "
                       "(GET own pages, robots.txt honoured, redirects inspected); never publishes")
    p.add_argument("--briefs", type=Path, required=True, help="Local SEO briefs JSON (docs/SEO-INTEGRATION.md)")
    p.add_argument("--campaign", type=Path, help="Campaign JSON whose CTAs and grounded claim phrases are checked")
    p.add_argument("--online", action="store_true",
                   help="Explicit GET of each canonical page: status, redirects, rel=canonical, title, "
                        "Open Graph, noindex, lang, lexical claim phrases")
    p.add_argument("--allow-origin", nargs="+",
                   help="https origins a redirect may move to outside the brief's registrable domain")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    p = sub.add_parser("ai-visibility", help="AI answer-engine and search visibility of your own pages: robots.txt "
                       "per crawler (search, training, user fetch), noindex/nosnippet, canonical, JSON-LD, "
                       "fediverse:creator, llms.txt as info; offline unless --online; never publishes")
    p.add_argument("--briefs", type=Path, help="Local SEO briefs JSON; --online checks only their canonical_url pages")
    p.add_argument("--page-id", nargs="+", help="Brief page_id(s) to check (offline: exactly one)")
    p.add_argument("--url", help="Offline only: the address of the saved page (instead of --briefs)")
    p.add_argument("--html", type=Path, help="Offline: saved HTML of the page")
    p.add_argument("--robots", type=Path, help="Offline: saved robots.txt of the site")
    p.add_argument("--online", action="store_true",
                   help="Explicit GET of each brief page, its robots.txt and /llms.txt (robots.txt honoured, "
                        "redirects inspected)")
    p.add_argument("--allow-origin", nargs="+",
                   help="https origins a redirect may move to outside the brief's registrable domain")
    p.add_argument("--json", action="store_true", help="Machine-readable report")
    from .cli_help import grouped_index  # after every subcommand exists, so the index cannot miss one
    parser.epilog = grouped_index(parser)
    parser.usage = "reachweaver [-h] [--db DB] [--accounts ACCOUNTS] [--plain] COMMAND [options]"  # after the subparsers exist
    return parser
