"""``reachweaver describe``: a machine-readable command catalog for agents such as Claude Code.

The catalog is generated from the argparse parser itself (``cli.make_parser``), so flags, defaults and
choices can never drift from the real command line. Only the *classification* is written by hand, in
``COMMANDS`` below, and ``tests/test_catalog.py`` fails when a subcommand exists without one: a new command
cannot ship unclassified.

Tags (a command carries exactly one of ``read_only`` / ``writes``):

* ``owner_only`` -- the owner runs it in their own terminal; an agent must never run it.
* ``network``    -- may make network requests (usually only with ``--online`` or ``--live``).
* ``writes``     -- may change the local database or write files, in at least one of its modes. Commands
  that open the database read-write (and so create or upgrade it when it is missing) count as writing.
* ``read_only``  -- changes nothing: no database write, no file written, never creates the database.
* ``interactive`` -- needs a person at a terminal or runs until interrupted; not for an agent.

``everyday`` (one boolean per command, schema_version 1, additive) is true for the short list that
``reachweaver --help`` shows (``cli_help.EVERYDAY``); every command is listed either way.

``owner_only_flags`` names the flags that turn an otherwise agent-safe command into an owner-only one
(``publish --live``, ``account-kit --approve-plan``...). The classification is conservative: when a mode
of a command could write, the command is tagged ``writes``.

Nothing here runs a command, reads a credential, opens the database or touches the network.
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from .policy import ValidationError

TAGS = ("owner_only", "network", "writes", "read_only", "interactive")
OWNER_ONLY_NOTE = "The owner runs these in their own terminal; never run them."
DB_NOTE = "opens the database read-write, so it creates or upgrades a missing one"

R, W = ("read_only",), ("writes",)
NW = ("network", "writes")
NR = ("network", "read_only")


def _c(tags: tuple[str, ...], owner_only_flags: tuple[str, ...] = (), note: str = "") -> dict:
    return {"tags": tags, "owner_only_flags": owner_only_flags, "note": note}


# One entry per subcommand. Keep the note short; it is shipped in every `describe` answer.
COMMANDS: dict[str, dict] = {
    # Get started and look around
    "context": _c(R, note="session start: one compact read-only summary"),
    "describe": _c(R, note="this catalog"),
    "help": _c(R, note="prints help text from the parser; offline"),
    "start": _c(R),
    "next": _c(R, note="ideas inbox and the database mode=ro; never creates the database; prints your idea text, "
                       "so mcp-serve withholds it"),
    "version": _c(R),
    "init": _c(W),
    "doctor": _c(R),
    "statusline": _c(R, note="one line for Claude Code's status bar; never creates the database"),
    "idea": _c(W, note="ideas inbox in a private ideas.json; promote writes a NEW campaign scaffold, never a post"),
    "voice-profile": _c(W, note="writes one NEW private profile file; --from-db opens the database read-only"),
    "voice-check": _c(R, note="advisory; --post-id opens the database read-only"),
    "health": _c(W, note=DB_NOTE),
    "workspace": _c(W, note="read-only report unless --init, or --gather/--repair with --apply"),
    "ui": _c(("read_only", "interactive"), note="serves a loopback dashboard until interrupted; the owner opens it"),
    "studio": _c(("writes", "interactive"),
                 note="serves the read-only dashboard and the composer together until interrupted; writes only what "
                      "the composer writes (private local files, posted-by-hand records) and an empty database "
                      "when none exists; never sends"),
    "mcp-serve": _c(("writes", "interactive"),
                    note="draft: stdio MCP server the owner registers in an MCP client; read-only tools, plus draft "
                         "only with --allow-drafts; runs until its input closes"),
    "list": _c(W, note=DB_NOTE),
    "show": _c(W, note=DB_NOTE),
    "journal": _c(W, note="read-only unless --out FILE"),
    "capabilities": _c(R),
    "registry-age": _c(R, note="age of every dated research fact in the bundled registries; offline, never the database"),
    "completion": _c(R),
    "connect": _c(NW, ("--guide",), "the status table is offline and read-only; --guide is the owner's walkthrough"),
    "account-advisor": _c(R),
    "profile": _c(W, note="--show and --check only read"),
    "account-route": _c(R),
    "account-kit": _c(W, ("--approve-plan",), "the plan is read-only; --out with --approve-plan writes packets"),
    "account-remove": _c(W, note="removes one entry from accounts.json (previous file kept as .bak); never touches "
                                 "credentials, the database, approvals or disconnect state; --dry-run writes nothing"),
    "account-add": _c(W, note="appends one public entry to accounts.json (previous file kept as .bak); never a "
                              "secret; --dry-run writes nothing"),
    # Draft and approve
    "draft": _c(NW, note="network only for --reply-to --online"),
    "check": _c(W, note=DB_NOTE),
    "repeat-check": _c(R),
    "mark-shared": _c(W),
    "review": _c(("owner_only", "network", "writes", "interactive"),
                 note="guided approval loop in the owner's terminal; each answer runs approve or cancel for one post"),
    "approve": _c(("owner_only", "network", "writes")),
    "unapprove": _c(W),
    "cancel": _c(W),
    "schedule": _c(W),
    "media-attach": _c(NW, note="network only for --url --online"),
    "media-detach": _c(W),
    "ai-declare": _c(W, note="owner declarations on a draft (authorship, realistic synthetic media); bound at approval for media posts"),
    "video-attach": _c(W),
    "video-detach": _c(W),
    "youtube-attach": _c(W),
    "youtube-detach": _c(W),
    "campaign-check": _c(R),
    "campaign-preview": _c(R),
    "campaign-stage": _c(W, note="stages drafts only; never approves"),
    "campaign-export": _c(W),
    "claims-check": _c(R),
    "review-packet": _c(W, note="database read-only; one NEW private folder per run; never approves"),
    "review-comment": _c(("owner_only", "writes"), note="records another person's words in the packet's comments.json only; never approval"),
    "review-status": _c(R, note="comments per post, STALE after a text change; database read-only; never approval"),
    # Publish and recover
    "publish": _c(NW, ("--live",), "without --live a dry run: no request is sent"),
    "pause": _c(W, note="emergency stop; safe for anyone"),
    "resume": _c(("owner_only", "writes")),
    "reconcile": _c(("owner_only", "writes")),
    "approval-mode": _c(W, ("--confirmation", "--require-key", "--require-identity", "--max-age-hours"),
                        "without flags it only displays the current mode"),
    "calendar-plan": _c(W, note="writes only with --apply"),
    "calendar-gaps": _c(R),
    "connect-check": _c(NW),
    "postiz-check": _c(NW),
    "disconnect": _c(W),
    "reconnect": _c(("owner_only", "writes", "interactive")),
    # Post by hand
    "share": _c(("writes", "interactive"), ("--guide", "--web"), "writes local files only; never sends"),
    "manual-export": _c(W),
    "manual-export-batch": _c(W),
    "delivery-options": _c(R),
    # Monitor
    "queue-health": _c(W, note=DB_NOTE),
    "dead-letter": _c(W, note=DB_NOTE),
    "ops-metrics": _c(W, note="read-only unless --out FILE"),
    "ops-summary": _c(R),
    "credentials-audit": _c(R),
    "link-health": _c(NR, note="network only with --online"),
    "trace": _c(R),
    # Measure and learn
    "metrics": _c(W),
    "import-metrics": _c(W),
    "metrics-fetch": _c(NW),
    "report": _c(W, note=DB_NOTE),
    "youtube-analytics": _c(R),
    "timing-evaluate": _c(W, note=DB_NOTE),
    "baseline-eval": _c(W, note=DB_NOTE),
    "insights": _c(R, note="database mode=ro; never creates the database; observations to test, never causes"),
    "platform-risk": _c(R, note="database mode=ro; reach share per platform, concentration and sourced risk flags; "
                                "hypotheses, never forecasts"),
    "eu-review-evidence": _c(R, note="database mode=ro and approval ledger read-only (never repaired); human-review "
                                     "evidence per published post; not legal advice"),
    "attribution-report": _c(W, note=DB_NOTE),
    "pilot-report": _c(R),
    "peer-benchmark": _c(R),
    "suggest": _c(R),
    "weekly-digest": _c(W),
    # Experiments
    "experiment-preregister": _c(W),
    "experiment-verify": _c(R),
    "experiment-evaluate": _c(W, note=DB_NOTE),
    "experiment-readout": _c(W, note=DB_NOTE),
    "experiment-next": _c(R),
    "experiment-simulate": _c(R),
    # Audit profiles and content
    "profile-audit": _c(R),
    "post-audit": _c(R),
    "seo-destinations": _c(R),
    "seo-content-check": _c(NR, note="network only with --online"),
    "landing-check": _c(NR, note="network only with --online"),
    "ai-visibility": _c(NR, note="network only with --online (own brief pages, their robots.txt and /llms.txt)"),
    "utm-link": _c(R),
    "creative-preflight": _c(R),
    "ai-label-check": _c(R, note="advisory; --post-id opens the database read-only; never blocks, approves or sends"),
    "media-check": _c(R),
    "media-storyboard": _c(W),
    "media-cutlist": _c(R),
    # Research channels and sources
    "distribution-plan": _c(R),
    "social-web-plan": _c(R, note="offline open social web map; reads accounts metadata only, never the queue"),
    "discovery-plan": _c(R),
    "sources-scan": _c(NW, note="network only with --online; --state/--mark-seen write"),
    "signal-brief": _c(W),
    "release-brief": _c(W, note="saved release JSON or CHANGELOG section -> NEW scaffold folder; never a post"),
    # Creator amplification
    "creator-match": _c(R),
    "creator-kit": _c(W),
    "creator-coverage": _c(R),
    # Engagement
    "engagement-fetch": _c(NW, note="network only with --online"),
    "engagement-triage": _c(W, note="writes draft files only with --out/--issues-dir"),
    "engagement-queue": _c(R),
    "engage-scout": _c(NW, note="network only with --online (GET, no credential); never posts, replies, likes or "
                                "follows; only --record writes its private state file; never opens the database"),
    "engage-log": _c(W, note="--mark writes only the private state file; --readout only reads; never opens the "
                             "database"),
    "followup-suggest": _c(W, note="database mode=ro; suggestions only (never likes, replies or drafts); only --record writes its --state file"),
    # Data, privacy and schemas
    "archive": _c(W),
    "media-relink": _c(W, note="dry run unless --apply"),
    "data-export": _c(W),
    "data-purge": _c(W, note="dry run unless --apply"),
    "privacy-status": _c(W, note=DB_NOTE),
    "schema-validate": _c(R),
    "schema-export": _c(W),
    # Updates
    "update": _c(NW, note="--apply --yes only after the owner agrees"),
}


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def classification_problems(names) -> list[str]:
    """Every way the table disagrees with the parser's subcommands (empty when consistent)."""
    names = set(names)
    problems = [f"unclassified command: {n}" for n in sorted(names - set(COMMANDS))]
    problems += [f"stale classification: {n}" for n in sorted(set(COMMANDS) - names)]
    for name, entry in sorted(COMMANDS.items()):
        tags = entry["tags"]
        if any(t not in TAGS for t in tags) or len(set(tags)) != len(tags):
            problems.append(f"{name}: unknown or repeated tag in {tags}")
        if ("read_only" in tags) == ("writes" in tags):
            problems.append(f"{name}: needs exactly one of read_only / writes")
    return problems


def _option_name(action: argparse.Action) -> str:
    if not action.option_strings:
        return action.metavar if isinstance(action.metavar, str) else action.dest
    longs = [s for s in action.option_strings if s.startswith("--")]
    return max(longs, key=len) if longs else action.option_strings[0]


def _jsonable(value):
    if value is None or value is argparse.SUPPRESS:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _type_name(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        return "flag"
    if isinstance(action, argparse._CountAction):
        return "count"
    kind = action.type
    if kind is None:
        return "string"
    if kind is Path:
        return "path"
    return getattr(kind, "__name__", "string")


def _flag(action: argparse.Action, *, with_help: bool) -> dict:
    repeatable = (isinstance(action, (argparse._AppendAction, argparse._AppendConstAction, argparse._ExtendAction))
                  or action.nargs in ("*", "+", argparse.REMAINDER))
    item = {"name": _option_name(action), "positional": not action.option_strings,
            "required": bool(action.required), "type": _type_name(action),
            "choices": [_jsonable(c) for c in action.choices] if action.choices else None,
            "default": None if _type_name(action) == "flag" else _jsonable(action.default),
            "repeatable": repeatable}
    if action.nargs == "?":
        item["value_optional"] = True
    if with_help:
        item["help"] = action.help if isinstance(action.help, str) and action.help != argparse.SUPPRESS else None
        return item
    # The whole-catalog listing is compact: a missing key means false, null or empty.
    return {k: v for k, v in item.items() if k in ("name", "type") or v not in (None, False, [])}


def json_option(sub: argparse.ArgumentParser) -> str | None:
    """How to ask this command for JSON: ``--json``, ``--format json``, or None."""
    for action in sub._actions:
        if "--json" in action.option_strings:
            return "--json"
    for action in sub._actions:
        if "--format" in action.option_strings and action.choices and "json" in action.choices:
            return "--format json"
    return None


def _entry(name: str, sub: argparse.ArgumentParser, help_text: str | None, *, with_help: bool) -> dict:
    meta = COMMANDS.get(name, {"tags": ("writes",), "owner_only_flags": (), "note": "unclassified"})
    actions = [a for a in sub._actions if not isinstance(a, argparse._HelpAction)]
    groups = [{"required": bool(g.required), "flags": [_option_name(a) for a in g._group_actions]}
              for g in sub._mutually_exclusive_groups]
    from .cli_help import EVERYDAY_NAMES
    entry = {"name": name, "help": help_text, "tags": list(meta["tags"]),
             "owner_only_flags": list(meta["owner_only_flags"]), "json": json_option(sub),
             "everyday": name in EVERYDAY_NAMES,
             "flags": [_flag(a, with_help=with_help) for a in actions]}
    if groups:
        entry["exclusive_groups"] = groups
    if meta["note"]:
        entry["note"] = meta["note"]
    return entry


def describe(parser: argparse.ArgumentParser, command: str | None = None) -> dict:
    """The catalog document: every subcommand, or one (with per-flag help) when ``command`` is given."""
    action = _subparsers(parser)
    helps = {choice.dest: choice.help for choice in action._choices_actions}
    if command is not None:
        if command not in action.choices:
            raise ValidationError(f"Unknown command {command!r}; 'reachweaver describe' lists every command")
        return {"catalog": "reachweaver_commands", "schema_version": 1,
                "command": _entry(command, action.choices[command], helps.get(command), with_help=True)}
    top = [a for a in parser._actions if a.option_strings and not isinstance(a, argparse._HelpAction)]
    return {"catalog": "reachweaver_commands", "schema_version": 1, "tags": list(TAGS),
            "global_options": [_flag(a, with_help=False) for a in top],
            "owner_only": {"note": OWNER_ONLY_NOTE, "commands": owner_only_commands()},
            "commands": [_entry(n, sub, helps.get(n), with_help=False) for n, sub in sorted(action.choices.items())]}


def owner_only_commands() -> list[str]:
    """Whole commands and command+flag forms an agent must never run, e.g. ``publish --live``."""
    out = [name for name, meta in sorted(COMMANDS.items()) if "owner_only" in meta["tags"]]
    out += [f"{name} {flag}" for name, meta in sorted(COMMANDS.items()) for flag in meta["owner_only_flags"]]
    return out


def is_owner_only(command_line: str | None) -> bool:
    """True when a suggested command line (``reachweaver resume``, ``share --guide``) is owner-only."""
    if not command_line:
        return False
    try:
        words = shlex.split(command_line)
    except ValueError:
        words = command_line.split()
    words = [w.split("=", 1)[0] for w in words]
    for index, word in enumerate(words):
        meta = COMMANDS.get(word)
        if meta is None:
            continue
        rest = words[index + 1:]
        return "owner_only" in meta["tags"] or any(flag in rest for flag in meta["owner_only_flags"])
    return False


def owner_runs(command_line: str | None) -> bool:
    """True when the owner, not an agent, runs this suggested command: owner-only or interactive."""
    if is_owner_only(command_line):
        return True
    words = (command_line or "").split()
    name = next((w for w in words if w in COMMANDS), None)
    return name is not None and "interactive" in COMMANDS[name]["tags"]


def render_text(document: dict) -> str:
    """A short human listing: one line per command, or the flags of one command."""
    if "command" in document:
        entry = document["command"]
        lines = [f"{entry['name']}  [{', '.join(entry['tags'])}]", f"  {entry['help'] or ''}".rstrip()]
        if entry["owner_only_flags"]:
            lines.append(f"  owner-only flags: {', '.join(entry['owner_only_flags'])}")
        if entry.get("note"):
            lines.append(f"  note: {entry['note']}")
        lines.append(f"  JSON output: {entry['json'] or 'none'}")
        for flag in entry["flags"]:
            bits = [flag["type"]] + (["required"] if flag["required"] else []) + (
                ["repeatable"] if flag["repeatable"] else [])
            if flag["choices"]:
                bits.append("choices: " + ",".join(str(c) for c in flag["choices"]))
            if flag["default"] not in (None, [], False):
                bits.append(f"default: {flag['default']}")
            lines.append(f"  {flag['name']} ({'; '.join(bits)})" + (f"  {flag['help']}" if flag.get("help") else ""))
        return "\n".join(lines)
    width = max(len(entry["name"]) for entry in document["commands"]) + 2
    lines = [f"{entry['name'].ljust(width)}{','.join(entry['tags'])}"
             + (f" (owner-only with {' '.join(entry['owner_only_flags'])})" if entry["owner_only_flags"] else "")
             for entry in document["commands"]]
    lines += ["", f"{OWNER_ONLY_NOTE} 'reachweaver describe COMMAND' shows one command's flags; add --json for "
              "the machine-readable catalog."]
    return "\n".join(lines)
