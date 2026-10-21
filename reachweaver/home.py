"""The workspace folder: one directory that holds everything that is yours, so it can be copied as a whole.

A workspace is marked by a small file, ``.reachweaver-workspace``. When one is found in the folder you run ReachWeaver
from (or any parent of it), or named with ``--home DIR``, the default places move into it::

    config/accounts.json  config/optout.json  config/caps.json  config/retention.json
    state/reachweaver.sqlite  state/approval-ledger.jsonl  state/archive/
    media/  logs/

An explicit flag (``--db``, ``--accounts`` ...) or the matching ``REACHWEAVER_*`` setting still wins over the
workspace, and without a marker nothing changes: the defaults stay relative to the folder you run from.

It also remembers the global path options typed for this run (``--home``, ``--db``, ``--accounts``, ``--profile``)
so that a printed next step (``command``) repeats them: following it then reaches the same files instead of the
defaults (UX re-measure 2026-10-21: ``--accounts myaccounts.json draft`` suggested a plain ``account-add``, which
wrote config/accounts.json, and the retry failed again).

This module only resolves paths. It reads no environment, creates nothing and opens no database.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from .policy import ValidationError

MARKER = ".reachweaver-workspace"
SEARCH_LEVELS = 8

_root: Path | None = None
_typed: tuple[tuple[str, str], ...] = ()


def set_root(path: Path | None) -> None:
    global _root
    _root = path


def root() -> Path | None:
    return _root


def remember_flags(*, home_dir: str | Path | None = None, db: str | Path | None = None,
                   accounts: str | Path | None = None, profile: str | Path | None = None) -> None:
    """The global path options typed for this run; no argument forgets them (``cli.main`` does, when it ends)."""
    global _typed
    _typed = tuple((flag, str(value)) for flag, value in (("--home", home_dir), ("--db", db),
                                                           ("--accounts", accounts), ("--profile", profile))
                   if value is not None and str(value) != "")


def typed_accounts() -> str | None:
    """The ``--accounts`` value typed for this run, or None."""
    return next((value for flag, value in _typed if flag == "--accounts"), None)


def command(rest: str, *, accounts: str | Path | None = None) -> str:
    """``reachweaver [the global options typed for this run] REST``, ready to copy.

    ``accounts`` names an accounts file that differs from the run's default (a command's own ``--accounts``);
    it replaces a typed ``--accounts``. Values are shell-quoted; without typed options this is ``reachweaver REST``.
    """
    flags = [(flag, value) for flag, value in _typed if not (accounts is not None and flag == "--accounts")]
    if accounts is not None:
        flags.append(("--accounts", str(accounts)))
    words = ["reachweaver", *(f"{flag} {shlex.quote(value)}" for flag, value in flags)]
    return " ".join(words + ([rest] if rest else []))


def default_path(relative: str | Path) -> Path:
    """``relative`` inside the active workspace, else unchanged (relative to the working folder)."""
    path = Path(relative)
    return _root / path if _root is not None and not path.is_absolute() else path


def find_root(start: Path) -> Path | None:
    """The nearest folder at or above ``start`` that carries the workspace marker."""
    here = start.resolve()
    for folder in (here, *here.parents)[:SEARCH_LEVELS]:
        if (folder / MARKER).is_file():
            return folder
    return None


def activate(explicit: Path | None, cwd: Path) -> Path | None:
    """Choose the workspace for this run: ``--home DIR`` (which must carry the marker), else the nearest marker."""
    if explicit is not None:
        folder = explicit.expanduser().resolve()
        if not (folder / MARKER).is_file():
            raise ValidationError(f"{folder} is not a ReachWeaver workspace (no {MARKER} file). "
                                  "Create one with `reachweaver workspace --init DIR`")
        set_root(folder)
    else:
        set_root(find_root(cwd))
    return _root
