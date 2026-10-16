"""Shell completion for ``reachweaver`` (bash, and zsh through bashcompinit), generated from the parser.

``reachweaver completion bash`` prints a script; load it with ``eval "$(reachweaver completion bash)"``
or save it where your shell loads completions. It completes subcommands, each subcommand's options and
the fixed choices of an option (for example ``list --status``). It never opens the database, reads an
account or runs any reachweaver command while you press Tab: the words come from the parser at the time
the script was generated, so regenerate it after an upgrade.
"""
from __future__ import annotations

import argparse
import shlex

SHELLS = ("bash", "zsh")


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def _options(parser: argparse.ArgumentParser) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """(every option string, {option: fixed choices}, options that take a free-form value) of one parser."""
    flags: list[str] = []
    choices: dict[str, list[str]] = {}
    free: list[str] = []
    for action in parser._actions:
        for option in action.option_strings:
            if option.startswith("--"):
                flags.append(option)
                if action.choices:
                    choices[option] = [str(c) for c in action.choices]
                elif action.nargs != 0 and not isinstance(action, argparse._HelpAction):
                    free.append(option)
    return sorted(set(flags)), choices, sorted(set(free))


def render(shell: str, parser: argparse.ArgumentParser) -> str:
    if shell not in SHELLS:
        raise ValueError(f"unsupported shell {shell!r}; choose one of {', '.join(SHELLS)}")
    subs = _subparsers(parser)
    global_flags, _, _ = _options(parser)
    value_flags = [a.option_strings[0] for a in parser._actions
                   if a.option_strings and a.nargs != 0 and a.option_strings[0].startswith("--") and not isinstance(
                       a, argparse._HelpAction)]
    lines = []
    if shell == "zsh":
        lines += ["autoload -U +X compinit && compinit 2>/dev/null", "autoload -U +X bashcompinit && bashcompinit", ""]
    lines += ["_reachweaver() {", "  local cur prev cmd i word", "  COMPREPLY=()",
              '  cur="${COMP_WORDS[COMP_CWORD]}"', '  prev="${COMP_WORDS[COMP_CWORD-1]}"', '  cmd=""',
              "  i=1", '  while [ "$i" -lt "$COMP_CWORD" ]; do', '    word="${COMP_WORDS[$i]}"', '    case "$word" in']
    lines += [f"      {'|'.join(shlex.quote(f) for f in value_flags)}) i=$((i+2)); continue ;;"] if value_flags else []
    lines += ["      -*) ;;", '      *) cmd="$word"; break ;;', "    esac", "    i=$((i+1))", "  done",
              '  if [ -z "$cmd" ]; then',
              *([f"    case \"$prev\" in {'|'.join(shlex.quote(f) for f in value_flags)}) return 0 ;; esac"] if value_flags else []),
              f"    COMPREPLY=($(compgen -W {shlex.quote(' '.join(sorted(subs)) + ' ' + ' '.join(global_flags))} -- \"$cur\"))",
              "    return 0", "  fi", '  case "$cmd:$prev" in']
    for name in sorted(subs):
        _, choices, free = _options(subs[name])
        for option, values in sorted(choices.items()):
            lines.append(f"    {shlex.quote(name + ':' + option)}) COMPREPLY=($(compgen -W "
                         f"{shlex.quote(' '.join(values))} -- \"$cur\")); return 0 ;;")
        if free:  # after an option that takes a value, offer nothing (the shell falls back to file names)
            lines.append("    " + "|".join(shlex.quote(f"{name}:{option}") for option in free) + ") return 0 ;;")
    lines += ["  esac", '  case "$cmd" in']
    for name in sorted(subs):
        flags, _, _ = _options(subs[name])
        lines.append(f"    {shlex.quote(name)}) COMPREPLY=($(compgen -W {shlex.quote(' '.join(flags))} -- \"$cur\")) ;;")
    lines += ["  esac", "  return 0", "}", "complete -o default -F _reachweaver reachweaver", ""]
    return "\n".join(lines)
