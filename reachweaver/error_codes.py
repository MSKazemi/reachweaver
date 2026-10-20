"""Stable, machine-readable error codes for the CLI's ``--json`` mode.

``main()`` keeps printing ``ERROR: message`` on stderr exactly as before. When the command that failed was
invoked with ``--json``, it also prints ``{"error": {"code": ..., "message": ...}}`` on stdout, so an agent
can branch on ``code`` instead of parsing English. Codes come from the exception class, refined for the
common ``ValidationError`` messages. The list in ``CODES`` is a public contract: add codes, never rename.
"""
from __future__ import annotations

import csv
import json
import re

from .policy import ValidationError
from .publishers import PublishError

CODES = (
    "validation_error",  # bad input or a failed check (the default for ValidationError)
    "not_found",         # unknown post/account/campaign/command, or a missing file/database
    "already_exists",    # refuses to overwrite an existing file or folder
    "refused",           # a safety gate refused the action; nothing changed
    "paused",            # the emergency pause is on
    "live_gate",         # live publishing is not enabled in this shell
    "database",          # the local database is unusable (schema, symlink, not a database)
    "permission",        # a file or folder is not private, or the OS denied access
    "io_error",          # another operating-system error
    "invalid_json",      # an input file is not valid JSON
    "invalid_csv",       # an input file is not valid CSV
    "invalid_value",     # another bad value
    "invalid_input",     # an input document has an unexpected shape
    "publish_error",     # an adapter error before or during a send
    "error",             # anything else
)

_VALIDATION = (
    ("not_found", re.compile(r"^(Unknown (post|account|campaign|command)\b|Missing )|does not exist")),
    ("already_exists", re.compile(r"already exists")),
    ("paused", re.compile(r"emergency (pause|stop)", re.I)),
    ("live_gate", re.compile(r"^Live publishing requires")),
    ("permission", re.compile(r"not private|permission denied", re.I)),
    ("database", re.compile(r"predates this version's schema|State database|not a ReachWeaver database|"
                            r"not a database", re.I)),
    ("refused", re.compile(r"\brefus(ed|ing|es)\b", re.I)),
)


def error_code(exc: BaseException) -> str:
    """The stable code for an exception that ``main()`` reports."""
    if isinstance(exc, ValidationError):
        message = str(exc)
        for code, pattern in _VALIDATION:
            if pattern.search(message):
                return code
        return "validation_error"
    if isinstance(exc, PublishError):
        return "publish_error"
    if isinstance(exc, PermissionError):
        return "permission"
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return "not_found"
    if isinstance(exc, FileExistsError):
        return "already_exists"
    if isinstance(exc, OSError):
        return "io_error"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, csv.Error):
        return "invalid_csv"
    if isinstance(exc, ValueError):
        return "invalid_value"
    if isinstance(exc, (TypeError, KeyError, AttributeError)):
        return "invalid_input"
    return "error"


def wants_json(args) -> bool:
    """True when the parsed command line asked for ``--json`` output (never for ``--format json``)."""
    return getattr(args, "json", None) is True


def json_error(code: str, message: str) -> str:
    """The one-line stdout document; ``message`` must already be redacted."""
    return json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
