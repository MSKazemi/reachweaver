"""Claude Code PreToolUse guard: refuse agent shell commands that run an owner-only ReachWeaver action.

The plugin registers this file as a ``PreToolUse`` command hook on the ``Bash``, ``PowerShell``
and ``Monitor`` tools (``plugin/hooks/hooks.json``), so the agent rule travels with a plugin
install, which does not carry the repository's own ``.claude/settings.json`` deny rules.
Claude Code passes the tool call as JSON on stdin (``tool_name``, ``tool_input.command``); exit
code 2 blocks a ``PreToolUse`` tool call and Claude reads the stderr text as the reason
(https://code.claude.com/docs/en/hooks, sections "Exit code 2" and "PreToolUse decision
control", checked 2026-10-20). Exit 0 leaves the call to the normal permission flow.

Owner-only actions: ``approve``, ``account-kit --approve-plan``, ``publish --live``, ``resume``,
``reconcile``, ``reconnect``, ``approval-mode --confirmation off``, and setting
``REACHWEAVER_ALLOW_LIVE``, ``REACHWEAVER_ENV=prod`` or any use of ``REACHWEAVER_APPROVAL_SECRET``.
An invocation is any of ``reachweaver``, ``run.py``, ``python -m reachweaver[.cli|.__main__]``,
``reachweaver/__main__.py`` or ``reachweaver/cli.py``, after any wrapper (``env``, ``sudo``,
``uvx``, ``xargs``...), in any part of a chained command, inside ``bash -c``/``eval`` (nested a few
levels) and inside ``$(...)``, backticks and process substitution (best effort). Option
abbreviations that older argparse parsers accepted (``--liv``, ``--conf off``) count as the full
option. When the command cannot be tokenized, runs Python from ``-c`` or stdin, or nests too
deeply, a plain mention of ReachWeaver together with an owner-only word is refused (fail closed).

Trade-off (tested): a command whose *words* (not a quoted string) read ``reachweaver approve``,
for example ``echo reachweaver approve``, is refused too. Draft text inside one quoted
``--text`` argument is a single word and is not refused unless it holds an unescaped ``$(...)``
or backtick command that itself runs an owner-only action, which the shell would execute.

This is defence in depth for a cooperative agent, not a security boundary: a script written to
a file and then run, an encoded PowerShell command or another language's subprocess call is
not seen. Human confirmation (``approval-mode --confirmation tty_hash_prefix``) and keyed
approval stay the controls that a process without the owner's terminal or key cannot satisfy.

Standard library only and no package-relative imports: the hook runs this file directly as
``python3 -I <path>`` (isolated mode, so this directory does not shadow the standard library).
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Iterator, TextIO

ASK_OWNER = "Owner-only: ask the owner to run this in their own terminal."

OWNER_ONLY_COMMANDS = frozenset({"approve", "resume", "reconcile", "reconnect"})
# Top-level options that take a value (reachweaver/cli.py make_parser); the value is skipped
# when looking for the subcommand.
GLOBAL_VALUE_OPTIONS = ("--db", "--accounts", "--profile", "--home")
# Per-command options whose next word is a plain value, never an owner-only switch.
VALUE_OPTIONS = {
    "publish": ("--limit", "--post-id", "--max-per-24h", "--min-gap-minutes", "--max-lateness-minutes",
                "--optout", "--log-file"),
    "approval-mode": ("--max-age-hours",),
    "account-kit": ("--platform", "--persona", "--out"),
}
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "fish", "busybox", "su",
                    "pwsh", "pwsh.exe", "powershell", "powershell.exe", "cmd", "cmd.exe"})
MAX_DEPTH = 4
_SEPARATORS = set(";&|()<>\n")
_PYTHON = re.compile(r"^(?:python(?:\d+(?:\.\d+)?)?w?|pypy\d*|py)(?:\.exe)?$")
_PYTHON_VALUE_FLAGS = frozenset({"-W", "-X", "-Q"})
_MENTION = re.compile(r"reachweaver|run\.py", re.I)
_OWNER_WORD = re.compile(r"\b(?:approve|approve-plan|resume|reconcile|reconnect|approval-mode)\b"
                         r"|--l(?:i(?:ve?)?)?\b", re.I)
_SECRET = re.compile(r"REACHWEAVER_APPROVAL_SECRET", re.I)
_ALLOW_LIVE_SET = re.compile(
    r"REACHWEAVER_ALLOW_LIVE['\"\]]*\s*\+?=(?!=)"
    r"|\b(?:export|declare|typeset|local|readonly|setenv|set|read|printf\s+-v)\b[^\n;&|]*\bREACHWEAVER_ALLOW_LIVE\b"
    r"|\$env:REACHWEAVER_ALLOW_LIVE"
    r"|(?:environ\s*\[|environ\.(?:update|setdefault)|putenv|SetEnvironmentVariable)[^\n]*REACHWEAVER_ALLOW_LIVE",
    re.I)
_ENV_VALUE = re.compile(r"REACHWEAVER_ENV['\"\]]*\s*\+?=(?!=)\s*['\"]?([^\s'\";&|)]*)"
                        r"|\b(?:setenv|set)\s+REACHWEAVER_ENV\s+['\"]?([^\s'\";&|)]*)", re.I)


def _dynamic(word: str) -> bool:
    """A word whose value the shell computes at run time ($VAR, $(...), backticks)."""
    return "$" in word or "`" in word


def _base(word: str) -> str:
    return word.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()


def _is_invoker(word: str, mentioned: bool) -> bool:
    """True when this word starts a ReachWeaver run: the command, run.py, -m module or a script path."""
    module = re.match(r"^-[A-Za-z]*m(\S+)$", word)  # -mreachweaver, -Imreachweaver.cli
    if module:
        word = module.group(1)
    low = word.replace("\\", "/").lower()
    if _base(word) in {"reachweaver", "reachweaver.exe", "run.py", "reachweaver.cli", "reachweaver.__main__"}:
        return True
    if low.endswith(("reachweaver/__main__.py", "reachweaver/cli.py")):
        return True
    # `RW=...run.py; $RW approve 1`: a computed command name, only when ReachWeaver is named.
    return mentioned and word.startswith("$")


def _option_name(word: str) -> str:
    return word.split("=", 1)[0]


def _is_prefix_option(word: str, full: str) -> bool:
    """`word` (or its name before `=`) is `full` or an abbreviation of it argparse could accept."""
    name = _option_name(word)
    return name.startswith("--") and len(name) >= 3 and full.startswith(name)


def _verdict(args: list[str]) -> str | None:
    """Reason to refuse the ReachWeaver arguments that follow an invoker, or None."""
    i = 0
    sub = None
    while i < len(args):
        word = args[i]
        if word == "--":
            i += 1
            if i < len(args):
                sub = args[i]
            break
        if word.startswith("-") and len(word) > 1 and not _dynamic(word):
            name = _option_name(word)
            takes_value = "=" not in word and any(_is_prefix_option(name, o) for o in GLOBAL_VALUE_OPTIONS)
            i += 2 if takes_value else 1
            continue
        sub = word
        break
    if sub is None:
        return None
    rest = args[i + 1:]
    if _dynamic(sub):
        return "a ReachWeaver command computed at run time"
    command = sub.lower()
    if command in OWNER_ONLY_COMMANDS:
        return f"`{command}`"
    if command not in VALUE_OPTIONS:
        return None
    values = VALUE_OPTIONS[command]
    previous = ""
    for word in rest:
        if previous in values and "=" not in previous:
            previous = ""
            continue
        previous = word
        if _dynamic(word):
            return f"`{command}` with an option computed at run time"
        if command == "publish" and _is_prefix_option(word, "--live"):
            return "`publish --live`"
        if command == "approval-mode" and (word.lower() == "off" or word.lower().endswith("=off")):
            return "`approval-mode --confirmation off`"
        if command == "account-kit" and _is_prefix_option(word, "--approve-plan"):
            return "`account-kit --approve-plan`"
    return None


def _substitutions(command: str) -> Iterator[str]:
    """Bodies of $(...), <(...), >(...) and backticks outside single quotes (best effort)."""
    i, n, quote = 0, len(command), ""
    while i < n:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = ""
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and quote != '"':
            quote = "'"
        elif ch == '"':
            quote = "" if quote == '"' else '"'
        elif ch in "$<>" and command.startswith("(", i + 1):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += {"(": 1, ")": -1}.get(command[j], 0)
                j += 1
            yield command[i + 2:j - 1]
            i = j
            continue
        elif ch == "`":
            j = i + 1
            while j < n and command[j] != "`":
                j += 2 if command[j] == "\\" else 1
            yield command[i + 1:j]
            i = j + 1
            continue
        i += 1


def _segments(command: str) -> list[list[str]]:
    """Simple commands split at ; & | ( ) < > and newlines. Raises ValueError when unbalanced."""
    lex = shlex.shlex(command, posix=True, punctuation_chars="".join(sorted(_SEPARATORS)))
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""  # bash comments start only at a word boundary; never drop the rest of a line
    segments: list[list[str]] = [[]]
    for token in lex:
        if token and set(token) <= _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _python_runs_inline_code(words: list[str], start: int) -> bool:
    """A python interpreter at words[start] that runs -c code or reads its program from stdin."""
    i = start + 1
    while i < len(words):
        word = words[i]
        if word == "-" or re.match(r"^-[A-Za-z]*c", word):
            return True
        if word in _PYTHON_VALUE_FLAGS:
            i += 2
            continue
        if word.startswith("-") and not word.startswith("--"):
            if re.match(r"^-[A-Za-z]*m", word):
                return False
            i += 1
            continue
        if word.startswith("--"):
            i += 1
            continue
        return False  # a script path
    return True  # no script: the program comes from stdin (pipe or heredoc)


def check_command(command: str, depth: int = 0) -> str | None:
    """Reason to refuse this shell command, or None when it runs no owner-only ReachWeaver action."""
    if _SECRET.search(command):
        return "REACHWEAVER_APPROVAL_SECRET"
    if _ALLOW_LIVE_SET.search(command):
        return "setting REACHWEAVER_ALLOW_LIVE"
    for match in _ENV_VALUE.finditer(command):
        value = (match.group(1) if match.group(1) is not None else match.group(2) or "").lower()
        if "prod" in value or _dynamic(value):
            return "setting REACHWEAVER_ENV=prod"
    mentioned = bool(_MENTION.search(command))
    fallback = mentioned and bool(_OWNER_WORD.search(command))
    if depth > MAX_DEPTH:
        return "a deeply nested command that names an owner-only action" if fallback else None
    try:
        segments = _segments(command)
    except ValueError:
        return "a command that could not be read and names an owner-only action" if fallback else None
    nested: list[str] = list(_substitutions(command))
    for words in segments:
        for index, word in enumerate(words):
            base = _base(word)
            if _is_invoker(word, mentioned):
                reason = _verdict(words[index + 1:])
                if reason:
                    return reason
            if base in SHELLS:
                for j in range(index + 1, len(words)):
                    flag = words[j].lower()
                    if flag in ("-c", "-command", "/c") or re.match(r"^-[a-z]*c[a-z]*$", flag):
                        nested.append(" ".join(words[j + 1:]))
                        break
            elif base in ("eval", "ssh"):
                nested.append(" ".join(words[index + 1:]))
            elif _PYTHON.match(base) and fallback and _python_runs_inline_code(words, index):
                return "Python code that names an owner-only ReachWeaver action"
    for inner in nested:
        reason = check_command(inner, depth + 1)
        if reason:
            return reason
    return None


def check_tool_call(payload: object) -> str | None:
    """Reason to refuse a PreToolUse payload, or None. Only a string `tool_input.command` is read."""
    if not isinstance(payload, dict):
        raise ValueError("hook input is not a JSON object")
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return check_command(command) if isinstance(command, str) else None


def message(reason: str) -> str:
    return f"ReachWeaver agent guard refused {reason}. {ASK_OWNER}"


def main(stdin: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Hook entry point: exit 2 with the reason on stderr to block, 0 to pass."""
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    raw = ""
    try:
        raw = stdin.read()
        reason = check_tool_call(json.loads(raw))
    except Exception:  # fail closed only for input that names ReachWeaver
        reason = "a request the guard could not read" if _MENTION.search(raw) else None
    if reason:
        print(message(reason), file=stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
