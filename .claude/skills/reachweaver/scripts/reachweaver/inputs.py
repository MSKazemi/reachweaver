"""Reading the owner's local inputs: JSON files (with or without a BOM) and comma-separated option lists.

Editors on Windows often save JSON with a leading byte order mark, which ``json.loads`` rejects with an
unhelpful "Unexpected UTF-8 BOM" message; a file such as ``[[[[...]]]]`` nested tens of thousands deep
makes the stdlib decoder raise ``RecursionError``, which is not a ``ValueError`` and used to escape as a
traceback. The JSON helpers raise ``json.JSONDecodeError`` for malformed JSON exactly as before, so callers
keep their own "not JSON" messages, and ``ValidationError`` (exit 2 in the CLI) for nesting that is too deep.

Local files only: no network, no credentials, no state.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .policy import ValidationError

BOM = "﻿"
# Deepest [ / { nesting accepted in an owner's JSON file. Real files nest a handful of levels; the explicit
# limit makes "nested too deeply" independent of the interpreter (Python 3.14 bounds its C decoder by the
# thread stack, so the depth at which RecursionError appears differs between machines).
MAX_JSON_DEPTH = 512


def nesting_depth(text: str) -> int:
    """Deepest ``[``/``{`` nesting outside JSON strings; a linear scan that never recurses."""
    depth = deepest = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > deepest:
                deepest = depth
        elif char in "]}":
            depth -= 1
    return deepest


def loads_text(text: str, what: str = "JSON input", **kwargs: Any) -> Any:
    """``json.loads`` that ignores one leading BOM and refuses JSON nested deeper than ``MAX_JSON_DEPTH``.

    The depth check runs before parsing; a ``RecursionError`` from the decoder itself is still turned into
    the same ``ValidationError``.
    """
    if text.startswith(BOM):
        text = text[1:]
    if nesting_depth(text) > MAX_JSON_DEPTH:
        raise ValidationError(f"{what} is nested too deeply to read; nothing was changed")
    try:
        return json.loads(text, **kwargs)
    except RecursionError:
        raise ValidationError(f"{what} is nested too deeply to read; nothing was changed") from None


def read_json_file(path: str | Path, what: str | None = None, **kwargs: Any) -> Any:
    """Parse a local JSON file (UTF-8, with or without BOM). The message names the file, never its content."""
    source = Path(path)
    return loads_text(source.read_text(encoding="utf-8-sig"), what or f"JSON file {source.name}", **kwargs)


def split_list_args(groups: Iterable[str]) -> list[str]:
    """Repeatable options that also take commas: ``--platform a,b --platform c`` gives ``[a, b, c]``.

    Items are stripped and empty ones dropped; order is kept and nothing is validated here.
    """
    return [item.strip() for group in groups for item in group.split(",") if item.strip()]
