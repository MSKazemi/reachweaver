"""Terminal presentation helpers: colour and aligned tables, only on a real terminal.

When output is piped, redirected or the person passed ``--plain``, every helper returns the exact plain text the commands have always printed, so scripts and
tests that parse it are unaffected. ``--json`` output never goes through here.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata

# This module reads no environment variable on purpose: tests/test_hardening.py pins every
# environment read in reachweaver/ to an allow-list, and widening it (e.g. for NO_COLOR) is a
# credential-isolation rule change that needs the owner's security review. ``--plain`` covers it.
_plain = False
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # keeps \t and \n


def clean(text: str) -> str:
    """Replace control characters (ESC sequences, NUL, C1) so stored text cannot steer the terminal."""
    return _CONTROL.sub("\ufffd", text)


MIN_COLUMNS = 20


def terminal_columns(stream=None, default: int = 100) -> int:
    """Width of the terminal on ``stream`` (or stdout), from the OS: no environment variable is read."""
    try:
        columns = os.get_terminal_size((stream or sys.stdout).fileno()).columns
    except (OSError, ValueError, AttributeError):
        return default
    return columns if columns >= MIN_COLUMNS else default  # a pty that reports 0 columns means "unknown"


def set_plain(value: bool) -> None:
    """Turn colour and tables off for this process (the global ``--plain`` option)."""
    global _plain
    _plain = bool(value)

STYLES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "grey": "90", "bold": "1"}
# Post status -> colour. needs_review is the one a human must act on.
STATUS_COLOUR = {"draft": "grey", "approved": "blue", "scheduled": "blue", "sending": "yellow",
                 "needs_review": "red", "published": "green", "cancelled": "grey"}


def enabled(stream) -> bool:
    """Colour and tables only for an interactive terminal, and not with ``--plain``."""
    if _plain:
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def interactive(stream) -> bool:
    """True for a person at a terminal (``--plain`` does not hide error hints)."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def style(text: str, name: str, stream) -> str:
    code = STYLES.get(name)
    if code is None or not enabled(stream):
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else (0 if unicodedata.combining(ch) else 1)
               for ch in text)


def _clip(text: str, limit: int) -> str:
    if _width(text) <= limit:
        return text
    out, used = [], 0
    for ch in text:
        step = _width(ch)
        if used + step > limit - 1:
            break
        out.append(ch)
        used += step
    return "".join(out) + "…"


def table(headers: list[str], rows: list[list[str]], stream, *, columns: int | None = None, paint=None,
          fixed: tuple[int, ...] = ()) -> str:
    """Aligned table fitted to the terminal width (the widest column shrinks first). Cells are plain
    text; control characters are replaced. Columns listed in ``fixed`` (e.g. an id the reader must copy)
    are never shrunk or clipped. ``paint(column, text) -> style name | None`` colours a body cell after fitting, so alignment
    never depends on escape codes. The header is bold on a terminal."""
    columns = columns or terminal_columns(stream)
    cells = [[" ".join(clean(str(c)).split()) for c in row] for row in rows]
    widths = [max([_width(h)] + [_width(row[i]) for row in cells]) for i, h in enumerate(headers)]
    gaps = 2 * (len(headers) - 1)
    shrinkable = [i for i in range(len(widths)) if i not in fixed]
    while sum(widths) + gaps > columns and shrinkable and max(widths[i] for i in shrinkable) > 12:
        widest = max(shrinkable, key=lambda i: widths[i])
        widths[widest] -= 1
    def line(values, body=True):
        parts = []
        for i, value in enumerate(values):
            clipped = value if i in fixed else _clip(value, widths[i])
            name = paint(i, value) if (paint and body) else None
            shown = style(clipped, name, stream) if name else clipped
            parts.append(shown + " " * (widths[i] - _width(clipped)))
        return "  ".join(parts).rstrip()
    out = [style(line(headers, body=False), "bold", stream)]
    out += [line(row) for row in cells]
    return "\n".join(out)
