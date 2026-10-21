"""Tiered command help for ``reachweaver --help``.

argparse prints one flat list of ~120 subcommands. This module gives two views instead:

* ``reachweaver --help`` (``-h``, ``reachweaver help``): a short "Everyday" view of the commands a creator
  uses week to week, one plain-language line each (``EVERYDAY``).
* ``reachweaver --help all`` (``reachweaver help all``): every command, grouped by the job a person is doing
  (``GROUPS``), plus the global options.

``reachweaver --help COMMAND`` and ``reachweaver help COMMAND`` print that command's own help, which is
unchanged (``reachweaver COMMAND --help``). Only the overview changes: every command stays callable and
``describe --json`` still lists all of them. ``tests/test_cli_help.py`` fails if a subcommand is missing
from every group or an everyday entry names no real command, so neither view can drift; an ungrouped
command still appears under "Other" rather than disappearing.
"""
from __future__ import annotations

import argparse
import re
import sys
import textwrap

# (title, command names in the order a person meets them)
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Get started and look around", ("start", "help", "context", "describe", "connect", "account-add", "account-remove", "account-advisor", "profile", "account-route", "account-kit", "version", "init", "doctor", "health", "workspace", "ui", "list", "show", "journal", "capabilities", "registry-age", "completion", "mcp-serve")),
    ("Draft and approve (nothing is sent)", (
        "idea", "draft", "check", "repeat-check", "mark-shared", "review", "approve", "unapprove", "cancel", "schedule", "media-attach", "media-detach", "ai-declare",
        "video-attach", "video-detach", "youtube-attach", "youtube-detach", "campaign-check", "campaign-preview",
        "campaign-stage", "campaign-export", "claims-check", "review-packet", "review-comment",
        "review-status")),
    ("Publish and recover (owner only; live sends are gated)", (
        "publish", "pause", "resume", "reconcile", "approval-mode", "calendar-plan", "calendar-gaps",
        "connect-check", "postiz-check", "disconnect", "reconnect")),
    ("Post by hand", ("share", "manual-export", "manual-export-batch", "delivery-options")),
    ("Monitor", ("statusline", "queue-health", "dead-letter", "ops-metrics", "ops-summary", "credentials-audit", "link-health", "trace")),
    ("Measure and learn", (
        "metrics", "import-metrics", "metrics-fetch", "report", "youtube-analytics", "timing-evaluate",
        "baseline-eval", "insights", "platform-risk", "attribution-report", "pilot-report", "peer-benchmark", "suggest",
        "weekly-digest")),
    ("Experiments", (
        "experiment-preregister", "experiment-verify", "experiment-evaluate", "experiment-readout", "experiment-next",
        "experiment-simulate")),
    ("Audit profiles and content", ("profile-audit", "post-audit", "seo-destinations", "seo-content-check",
                                    "landing-check", "ai-visibility", "utm-link", "creative-preflight", "voice-profile", "voice-check", "ai-label-check",
                                    "eu-review-evidence",
                                    "media-check", "media-storyboard", "media-cutlist")),
    ("Research channels and sources", ("distribution-plan", "social-web-plan", "discovery-plan", "sources-scan", "signal-brief",
                                       "release-brief")),
    ("Creator amplification (offline, owner review)", ("creator-match", "creator-kit", "creator-coverage")),
    ("Engagement", ("engagement-fetch", "engagement-triage", "engagement-queue", "followup-suggest",
                    "engage-scout", "engage-log")),
    ("Data, privacy and schemas", ("archive", "media-relink", "data-export", "data-purge", "privacy-status", "schema-validate", "schema-export")),
    ("Updates", ("update",)),
)

# The everyday view: (command, plain-language line[, a second hint line]). Chosen from what the user-facing
# docs walk a creator through, in that order: README "Easy sharing" and docs/START-HERE.md (share, share --web,
# draft, check, approval, the dry-run publish), docs/QUICKSTART.md (draft, check, publish preview, list), the
# bundled skill's everyday loop (.claude/skills/reachweaver/SKILL.md: idea inbox, draft, review, report,
# metrics-fetch, suggest) and the setup pages (connect, workspace, update, ui). They are also the commands the
# user docs name most often (share, publish, workspace, connect, idea and draft lead that count). The single-post
# approval command is reached through `review`, which walks the drafts and runs it for each one with the same
# checks; agent-facing commands (context, describe, mcp-serve) and specialist ones stay in `--help all`. The
# lines only describe: publish stays a dry run without the owner's live switches, and review stays owner-only.
EVERYDAY: tuple[tuple[str, ...], ...] = (
    ("start", "See where you are and the exact next command to type"),
    ("share", "Turn one caption into copy-ready posts you paste into each app yourself",
     "share --web opens the same composer in your browser; nothing goes online"),
    ("idea", "Save a post idea for later; list, drop or promote it to a campaign"),
    ("draft", "Save a post for one account as a draft; nothing is sent"),
    ("check", "Look for problems in a draft before you approve it"),
    ("review", "Go through your drafts one by one and approve the ones you want (you only)"),
    ("publish", "Preview what would be sent; a dry run unless you, the owner, turn on live"),
    ("list", "See your posts and where each one stands"),
    ("report", "What your published posts achieved, from the numbers recorded so far"),
    ("metrics-fetch", "Fetch the public like, repost and reply counts of your Bluesky/Mastodon posts"),
    ("suggest", "A ranked list of things worth improving, for you to review"),
    ("ui", "Open a read-only dashboard of your posts in your browser"),
    ("connect", "See which accounts are ready, or walk through connecting them"),
    ("workspace", "What lives in this folder and whether it moves to another computer"),
    ("update", "Check whether a newer ReachWeaver is out"),
    ("help", "This overview; help all lists every command, help COMMAND explains one"),
)
EVERYDAY_NAMES = frozenset(entry[0] for entry in EVERYDAY)
FOOTER = ("Nothing is sent without your explicit approval and the live-send gates; run publish without --live",
          "for a dry run. See docs/QUICKSTART.md to try it with no account and no network.")
SUMMARY_WIDTH = 66


def summary(text: str | None) -> str:
    """One short line from a subcommand's help: drop issue references and parentheticals, cut at the
    first sentence break."""
    if not text:
        return ""
    text = re.sub(r"\s*\((?:[^()]*#\d+[^()]*|issue #\d+[^()]*)\)", "", text)  # "(#22 ...)" / "(issue #23 ...)"
    text = re.sub(r"^#\d+(?: \([^)]*\))?:\s*", "", text)  # a leading "#22: "
    text = re.split(r"(?<=[a-z0-9)])[.;](?:\s|$)|;\s", " ".join(text.split()), maxsplit=1)[0].rstrip(".;")
    text = text[:1].upper() + text[1:]
    return textwrap.shorten(text, width=SUMMARY_WIDTH, placeholder="…")


def grouped_index(parser: argparse.ArgumentParser) -> str:
    """The grouped command map, built from the parser's own subcommands and their help."""
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {choice.dest: choice.help for choice in action._choices_actions}
    known = set(action.choices)
    width = max(len(name) for name in known) + 2
    lines = ["Commands by task (reachweaver COMMAND --help for details):"]
    groups = [(title, [n for n in names if n in known]) for title, names in GROUPS]
    other = sorted(known - {n for _, names in GROUPS for n in names})
    if other:
        groups.append(("Other", other))
    for title, names in groups:
        if not names:
            continue
        lines += ["", title + ":"]
        for name in names:
            lines.append(f"  {name.ljust(width)}{summary(helps.get(name))}".rstrip())
    lines += ["", *FOOTER]
    return "\n".join(lines)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def everyday_help(parser: argparse.ArgumentParser) -> str:
    """The short top-level help: usage, the everyday commands, and how to see all of them."""
    known = set(_subparsers(parser).choices)
    rows = [entry for entry in EVERYDAY if entry[0] in known]
    width = max(len(entry[0]) for entry in rows) + 2
    lines = [parser.format_usage().rstrip(), "", "Approval-first social publishing; dry-run by default.", "",
             "Everyday commands (reachweaver COMMAND --help for details):"]
    for name, line, *extra in rows:
        lines.append(f"  {name.ljust(width)}{line}")
        lines += [f"  {'':{width}}{more}" for more in extra]
    lines += ["", f"Run `reachweaver --help all` (or `reachweaver help all`) to see all {len(known)} commands by group.",
              "", *FOOTER]
    return "\n".join(lines) + "\n"


def full_help(parser: argparse.ArgumentParser) -> str:
    """The whole map: usage, description, global options and every command grouped by task."""
    saved = parser.epilog
    parser.epilog = grouped_index(parser)
    try:
        return parser.format_help()
    finally:
        parser.epilog = saved


def help_text(parser: argparse.ArgumentParser, topic: str | None) -> str:
    """Help for a topic: None (everyday), "all" (every command) or one command name. Prints nothing itself."""
    if topic is None:
        return everyday_help(parser)
    if topic == "all":
        return full_help(parser)
    choices = _subparsers(parser).choices
    if topic in choices:
        return choices[topic].format_help()
    import difflib
    close = difflib.get_close_matches(topic, list(choices), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    from .policy import ValidationError
    raise ValidationError(f"unknown help topic '{topic}'.{hint} Use 'all' or a command name; "
                          "'reachweaver --help all' lists every command by task")


class TieredHelpAction(argparse._HelpAction):
    """``-h/--help [TOPIC]`` on the top-level parser: everyday view, ``all``, or one command's help.

    A subclass of argparse's own help action, so the catalog (``describe``) and shell completion keep
    treating it as the help flag. Subcommands keep argparse's standard ``-h``.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None, **kwargs):
        argparse.Action.__init__(self, option_strings=option_strings, dest=dest, default=default, nargs="?",
                                 metavar="TOPIC", help=help, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from .policy import ValidationError
        try:
            text = help_text(parser, values)
        except ValidationError as exc:
            parser.error(str(exc))
        parser._print_message(text, sys.stdout)
        parser.exit()


_HINTS = (
    (re.compile(r"^Missing \S*accounts|^Missing \S+: no accounts yet"), "Next: 'reachweaver account-add bluesky' adds your first account (public "
                                           "details only). To post by hand with no account at all: "
                                           "'reachweaver share --guide'."),
    (re.compile(r"^Unknown post id"), "Next: 'reachweaver list' shows the post ids."),
    (re.compile(r"^Unknown account"), "Next: 'reachweaver doctor' lists the configured accounts; "
                                      "'reachweaver account-add PLATFORM' adds one."),
    (re.compile(r"^Unknown network"), "Next: 'reachweaver share --list' shows the network names."),
    (re.compile(r"does not exist; .*never create"), "Next: 'reachweaver init' creates the local database."),
    (re.compile(r"State directory is not private"), "Next: restrict the state folder with chmod 700."),
    (re.compile(r"YouTube needs a video title"), "Next: add --title \"Your video title\" to the same command."),
)


def error_hint(message: str) -> str | None:
    """A one-line next step for a common, harmless failure, or None. Shown only to a person at a terminal."""
    for pattern, hint in _HINTS:
        if pattern.search(message):
            return hint
    return None


def file_error_message(exc: BaseException, parsed: argparse.Namespace | None) -> str | None:
    """A plain sentence for a file named on the command line that cannot be opened, or None.

    ``main`` prints it instead of Python's ``[Errno 2] No such file or directory: 'x'``. Only the path the
    operating system reported is shown (made absolute, so the person sees which folder was searched).
    """
    import os
    filename = getattr(exc, "filename", None)
    if not isinstance(exc, OSError) or not isinstance(filename, (str, os.PathLike)):
        return None
    name = os.fsdecode(filename)
    where = os.path.dirname(os.path.abspath(name))
    if isinstance(exc, FileNotFoundError):
        text_flag = parsed is not None and hasattr(parsed, "text") and hasattr(parsed, "file")
        return (f"File not found: {name} (looked in {where}). Check the name"
                + (', or use --text "..." instead.' if text_flag else " and the folder you run the command from."))
    if isinstance(exc, IsADirectoryError):
        return f"{name} is a folder, not a file. Give the path of a file inside it."
    if isinstance(exc, NotADirectoryError):
        return f"Part of the path {name} is a file, not a folder. Check the path."
    if isinstance(exc, PermissionError):
        return (f"Permission denied: {name}. Your user cannot read or write it here; check its owner and "
                "permissions (ls -l), or use a copy you own.")
    return None
