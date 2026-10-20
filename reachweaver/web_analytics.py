"""Offline parsers for owner-exported Google Search Console and GA4 CSV files.

The owner downloads the files by hand from the Search Console Performance report and
from a GA4 report or exploration, then passes the local paths to ``attribution-report``.
This module only PARSES those local files: no network, no Google API, no OAuth, no
credentials, no database and no writes. The join to ReachWeaver posts lives in
``attribution.attribution_report``.

Parsing is strict: unknown, duplicate or missing columns are refused with a message
naming the allowed columns; a file larger than ``MAX_EXPORT_BYTES`` or with more than
``MAX_EXPORT_ROWS`` data rows is refused; numbers accept ``1234``, ``1,234``,
``1.234`` (grouping), ``12.3`` / ``12,3`` (decimal) and ``12.3%``. A blank cell is
unknown (``None``), never zero. Error messages name a line and a column, never a cell
value, so an export's content is not echoed.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path

from .policy import PLATFORMS, ValidationError
from .privacy import redact

# GA4 "Download CSV" includes up to 100,000 rows (support.google.com/analytics/answer/9317657,
# checked 2026-10-20). Search Console exports are truncated to 1,000 rows per table
# (support.google.com/webmasters/answer/12919797), so the same caps cover both.
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_EXPORT_ROWS = 100_000
MAX_EXPORT_FILES = 12
_MAX_COUNT = 10**12

_SPACES = "   "
_GROUPED = re.compile(r"\d{1,3}(?:([,.'’   ])\d{3})(?:\1\d{3})*")
_GROUPED_WITH = {sep: re.compile(r"\d{1,3}(?:" + re.escape(sep) + r"\d{3})*") for sep in ",.'’"}
_DAY_COMPACT = re.compile(r"\d{8}")


def _norm(header: str) -> str:
    return " ".join((header or "").strip().lower().split())


def _shown(header: str) -> str:
    """A header name that is safe to print in an error (redacted, at most 60 characters)."""
    text = redact(" ".join((header or "").split()), emails=True)
    return text if len(text) <= 60 else text[:57] + "..."


def parse_count(value, label: str, line: int) -> int | None:
    """A nonnegative whole number; ``1234``, ``1,234``, ``1.234``, ``1 234`` or blank (None)."""
    text = (value or "").strip()
    if not text:
        return None
    if text.isascii() and text.isdecimal():
        number = int(text)
    elif _GROUPED.fullmatch(text):
        number = int("".join(ch for ch in text if ch.isdigit()))
    else:
        raise ValidationError(f"Line {line}: {label} must be a nonnegative whole number "
                              "(1234 or 1,234) or blank")
    if number > _MAX_COUNT:
        raise ValidationError(f"Line {line}: {label} is out of range")
    return number


def parse_decimal(value, label: str, line: int, *, percent: bool = False) -> float | None:
    """A nonnegative decimal; ``12.3``, ``12,3``, ``1,234.5``, ``1.234,5`` or blank (None).

    With both separators present the last one is the decimal mark. With a single ``,`` or
    ``.`` it is the decimal mark (``1,234`` reads as 1.234 here: decimal columns are rates
    and positions, not counts). With ``percent=True`` a trailing ``%`` divides by 100;
    without ``%`` the value is taken as already a fraction.
    """
    text = "".join(ch for ch in (value or "").strip() if ch not in _SPACES)
    if not text:
        return None
    is_percent = text.endswith("%")
    if is_percent:
        if not percent:
            raise ValidationError(f"Line {line}: {label} must be a number without %")
        text = text[:-1]
    if not text or not re.fullmatch(r"[0-9.,'’]+", text):
        raise ValidationError(f"Line {line}: {label} must be a nonnegative number or blank")
    if "," in text and "." in text:
        mark = "," if text.rfind(",") > text.rfind(".") else "."
    elif text.count(",") == 1:
        mark = ","
    elif text.count(".") == 1:
        mark = "."
    else:
        mark = None
    whole, frac = text.rsplit(mark, 1) if mark else (text, "")
    groupings = (("." if mark == "," else ","), "'", "’") if mark else tuple(_GROUPED_WITH)
    ok_whole = whole.isdecimal() or any(_GROUPED_WITH[sep].fullmatch(whole) for sep in groupings)
    if not whole or not ok_whole or (mark and not frac.isdecimal()):
        raise ValidationError(f"Line {line}: {label} must be a nonnegative number or blank")
    number = float("".join(ch for ch in whole if ch.isdigit()) + ("." + frac if frac else ""))
    if number > _MAX_COUNT:
        raise ValidationError(f"Line {line}: {label} is out of range")
    return number / 100 if is_percent else number


def parse_day(value, label: str, line: int) -> str | None:
    """``YYYY-MM-DD`` or ``YYYYMMDD`` (GA4's documented ``date`` format) to ISO; blank is None."""
    text = (value or "").strip()
    if not text:
        return None
    if _DAY_COMPACT.fullmatch(text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        if len(text) != 10:
            raise ValueError
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValidationError(f"Line {line}: {label} must be YYYY-MM-DD or YYYYMMDD") from None


def _read_export(path, label: str) -> str:
    file = Path(path)
    try:
        if file.is_symlink() or not file.is_file():
            raise ValidationError(f"{label} must be an existing regular file (no symlinks)")
        size = file.stat().st_size
        if size < 1:
            raise ValidationError(f"{label} is empty")
        if size > MAX_EXPORT_BYTES:
            raise ValidationError(f"{label} is larger than {MAX_EXPORT_BYTES // (1024 * 1024)} MiB")
        return file.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError(f"{label} is not UTF-8 text; re-download it as CSV") from None
    except OSError:
        raise ValidationError(f"{label} cannot be read") from None


def _check_header(header: list[str], label: str) -> None:
    if len(header) == 1 and ";" in header[0]:
        raise ValidationError(f"{label} looks semicolon-separated; download it again as comma-separated CSV")
    if any(not cell.strip() for cell in header):
        raise ValidationError(f"{label} has an empty column name")


def _rows(lines: list[str], first_line: int, label: str, width: int):
    """Yield ``(line_number, cells)`` for a CSV block; strict quoting, fixed width, row cap."""
    reader = csv.reader(io.StringIO("".join(lines), newline=""), strict=True)
    count = 0
    try:
        for cells in reader:
            line = first_line + reader.line_num - 1
            if not cells or all(not cell.strip() for cell in cells):
                continue
            count += 1
            if count > MAX_EXPORT_ROWS:
                raise ValidationError(f"{label} has more than {MAX_EXPORT_ROWS} data rows")
            if len(cells) != width:
                raise ValidationError(f"{label} line {line} has {len(cells)} cells; the header has {width}")
            yield line, [cell.strip() for cell in cells]
    except csv.Error:
        raise ValidationError(f"{label} has invalid CSV quoting") from None


# ---------------------------------------------------------------------------
# GA4: Reports > Share this report > Download File > Download CSV, or an exploration export
# ---------------------------------------------------------------------------

# UI labels from support.google.com/analytics/answer/9143382 and API names from
# developers.google.com/analytics/devguides/reporting/data/v1/api-schema (checked 2026-10-20).
GA4_DIMENSIONS = {
    "session source / medium": "source_medium", "sessionsourcemedium": "source_medium",
    "session source": "source", "sessionsource": "source",
    "session medium": "medium", "sessionmedium": "medium",
    "session campaign": "campaign", "sessioncampaignname": "campaign",
    "session manual ad content": "content", "sessionmanualadcontent": "content",
    "session manual term": "term", "sessionmanualterm": "term",
    "landing page": "landing", "landingpage": "landing",
    "landing page + query string": "landing", "landingpageplusquerystring": "landing",
    "date": "date",
}
# "Conversions" is GA4's label before key events were renamed; kept as a legacy alias.
GA4_METRICS = {
    "sessions": "sessions",
    "engaged sessions": "engaged_sessions", "engagedsessions": "engaged_sessions",
    "key events": "key_events", "keyevents": "key_events", "conversions": "key_events",
}
# Other documented metrics of the default Traffic acquisition table: accepted, never read.
GA4_IGNORED = frozenset({
    "engagement rate", "engagementrate", "average engagement time per session",
    "events per session", "eventspersession", "event count", "eventcount",
    "session key event rate", "sessionkeyeventrate", "total revenue", "totalrevenue",
    "total users", "totalusers", "active users", "activeusers", "new users", "newusers",
    "bounce rate", "bouncerate", "views per session", "screenpageviewspersession",
    "average session duration", "averagesessionduration", "userengagementduration",
})
# "(not set)" is GA4's documented placeholder; the others are its traffic-source defaults.
GA4_PLACEHOLDERS = frozenset({
    "(not set)", "(none)", "(direct)", "(organic)", "(referral)", "(cross-network)",
    "(data not available)", "(other)", "(not provided)",
})
_TOTAL_LABELS = frozenset({"grand total", "total", "totals"})
_META_DATE = re.compile(r"^#\s*(start|end)\s+date\s*:\s*(.*?)\s*$", re.I)


def _ga4_value(text: str) -> str:
    return "" if text.strip().lower() in GA4_PLACEHOLDERS else text.strip()


def _ga4_columns(header: list[str], label: str) -> list[tuple[str, str]]:
    """``[(kind, canonical)]`` per header cell; kind is dimension, metric or ignored."""
    _check_header(header, label)
    columns: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for cell in header:
        key = _norm(cell)
        if key in GA4_DIMENSIONS:
            kind, name = "dimension", GA4_DIMENSIONS[key]
        elif key in GA4_METRICS:
            kind, name = "metric", GA4_METRICS[key]
        elif key in GA4_IGNORED:
            kind, name = "ignored", key
        elif key.startswith("first user"):
            raise ValidationError(f"{label}: column {_shown(cell)!r} is user-scoped (User acquisition). Export the "
                                  "session-scoped Traffic acquisition report (Session source / medium) instead")
        else:
            raise ValidationError(
                f"{label}: column {_shown(cell)!r} is not supported. Dimensions: Session source / medium, "
                "Session source, Session medium, Session campaign, Session manual ad content, Session manual term, "
                "Landing page, Date. Metrics: Sessions, Engaged sessions, Key events (Conversions). Other "
                "Traffic acquisition metrics such as Engagement rate are accepted and ignored")
        if name in seen:
            raise ValidationError(f"{label}: columns {_shown(seen[name])!r} and {_shown(cell)!r} both give {name}")
        seen[name] = cell
        columns.append((kind, name))
    names = {name for _, name in columns}
    if "sessions" not in names:
        raise ValidationError(f"{label} needs a Sessions column")
    if "source_medium" in names and names & {"source", "medium"}:
        raise ValidationError(f"{label}: use either Session source / medium or Session source and Session medium")
    if "source_medium" not in names and not {"source", "medium"} <= names:
        raise ValidationError(f"{label} needs Session source / medium, or both Session source and Session medium")
    return columns


def _ga4_tagging(source: str, medium: str, campaign: str, content: str) -> dict:
    """UTM fields for a tagged row; otherwise the session source becomes the referrer.

    A row is tagged when it carries a campaign or ad content, or when its source is a
    ReachWeaver platform id with medium ``social`` (what ``utm-link`` writes). An untagged
    row such as ``t.co / referral`` keeps only its source, which the join maps to a
    social platform at domain level or leaves unattributed (``google / organic``).
    """
    if campaign or content or (source.lower() in PLATFORMS and medium.lower() == "social"):
        return {"utm_source": source, "utm_medium": medium, "utm_campaign": campaign, "utm_content": content,
                "referrer": ""}
    return {"utm_source": "", "utm_medium": "", "utm_campaign": "", "utm_content": "", "referrer": source}


def load_ga4_csv(path) -> dict:
    """Parse one GA4 CSV download (local file only).

    Leading ``#`` comment lines are skipped; ``# Start date: YYYYMMDD`` and
    ``# End date: YYYYMMDD`` lines in them, when present, give the export's date range.
    Only the first table is read: it ends at the first blank or ``#`` line after the
    header, and later sections (for example chart data) are counted, not parsed. A row
    whose dimension cells are all blank or ``Grand total``/``Total`` is the export's
    totals row: it is returned separately and never joined.
    """
    label = "GA4 CSV"
    text = _read_export(path, label)
    lines = text.splitlines(keepends=True)
    meta: dict[str, str] = {}
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        match = _META_DATE.match(lines[index].strip())
        if match:
            meta[match.group(1).lower()] = match.group(2)
        index += 1
    if index >= len(lines):
        raise ValidationError(f"{label} has no table header after its # comment lines")
    header_line = index + 1
    end = index + 1
    while end < len(lines) and lines[end].strip() and not lines[end].lstrip().startswith("#"):
        end += 1
    trailing = sum(1 for item in lines[end:] if item.strip() and not item.lstrip().startswith("#"))
    header = next(csv.reader([lines[index]]))
    columns = _ga4_columns(header, label)
    try:
        start, finish = parse_day(meta.get("start"), "date", 0), parse_day(meta.get("end"), "date", 0)
    except ValidationError:
        raise ValidationError(f"{label}: # Start date and # End date must be YYYYMMDD or YYYY-MM-DD") from None
    if start and finish and finish < start:
        raise ValidationError(f"{label}: # End date is before # Start date")
    rows: list[dict] = []
    totals = None
    dims = [name for kind, name in columns if kind == "dimension"]
    for line, cells in _rows(lines[index + 1:end], header_line + 1, label, len(header)):
        values = {name: cells[i] for i, (kind, name) in enumerate(columns) if kind != "ignored"}
        metrics = {name: parse_count(values[name], name, line)
                   for kind, name in columns if kind == "metric"}
        if all(not values[d] or values[d].lower() in _TOTAL_LABELS for d in dims):
            if totals is not None:
                raise ValidationError(f"{label} line {line}: a second totals row")
            totals = metrics
            continue
        if "source_medium" in values:
            source, _, medium = values["source_medium"].partition(" / ")
        else:
            source, medium = values["source"], values["medium"]
        day = parse_day(values.get("date"), "Date", line)
        if day and ((start and day < start) or (finish and day > finish)):
            raise ValidationError(f"{label} line {line}: Date is outside the export's # Start/End date range")
        rows.append({
            "line": line, "date": day, "landing": values.get("landing", ""),
            **_ga4_tagging(_ga4_value(source), _ga4_value(medium), _ga4_value(values.get("campaign", "")),
                           _ga4_value(values.get("content", ""))),
            "sessions": metrics["sessions"], "engaged_sessions": metrics.get("engaged_sessions"),
            "key_events": metrics.get("key_events"),
        })
    if start and finish:
        date_range, range_source = (start, finish), "export_header"
    elif any(r["date"] for r in rows):
        days = sorted(r["date"] for r in rows if r["date"])
        date_range, range_source = (days[0], days[-1]), "date_column"
    else:
        date_range, range_source = None, None
    if "date" in dims and any(r["date"] is None for r in rows):
        raise ValidationError(f"{label}: every row needs a Date when the Date column is present")
    return {
        "kind": "ga4", "rows": rows, "date_range": date_range, "date_range_source": range_source,
        "dimensions": dims, "metrics": [name for kind, name in columns if kind == "metric"],
        "ignored_columns": [name for kind, name in columns if kind == "ignored"],
        "totals_row": totals, "trailing_lines_ignored": trailing,
    }


def combine_ga4(exports: list[dict]) -> list[dict]:
    """Refuse a set of GA4 exports that would double-count sessions (overlapping periods)."""
    if len(exports) > MAX_EXPORT_FILES:
        raise ValidationError(f"Pass at most {MAX_EXPORT_FILES} --ga4-csv files")
    if len(exports) > 1:
        if any(item["date_range"] is None for item in exports):
            raise ValidationError("Several --ga4-csv files need a known date range each (# Start/End date lines "
                                  "or a Date column), so overlapping periods cannot be double-counted")
        spans = sorted(item["date_range"] for item in exports)
        for (_, last), (first, _) in zip(spans, spans[1:]):
            if first <= last:
                raise ValidationError("--ga4-csv files cover overlapping dates; pass non-overlapping periods "
                                      "or one file")
    return exports


# ---------------------------------------------------------------------------
# Search Console: Performance report > Export > Download CSV (one file per table)
# ---------------------------------------------------------------------------

GSC_TABLES = {
    "top pages": "pages", "pages": "pages", "page": "pages",
    "top queries": "queries", "queries": "queries", "query": "queries",
    "date": "dates", "dates": "dates",
    "filter": "filters",
}
GSC_OTHER_TABLES = frozenset({"country", "countries", "device", "devices", "search appearance"})
GSC_METRICS = {"clicks": "clicks", "impressions": "impressions", "ctr": "ctr",
               "position": "position", "average position": "position"}


def _gsc_metric_columns(header: list[str], label: str) -> list[str]:
    names, seen = [], set()
    for cell in header[1:]:
        key = _norm(cell)
        if key not in GSC_METRICS:
            raise ValidationError(f"{label}: column {_shown(cell)!r} is not supported; a Pages, Queries or Dates "
                                  "table has Clicks, Impressions, CTR and Position")
        name = GSC_METRICS[key]
        if name in seen:
            raise ValidationError(f"{label}: two columns give {name}")
        seen.add(name)
        names.append(name)
    if not {"clicks", "impressions"} <= seen:
        raise ValidationError(f"{label} needs Clicks and Impressions columns")
    return names


def load_gsc_csv(path) -> dict:
    """Parse one Search Console Performance export table (local file only).

    The table is recognized by its first column: ``Top pages``/``Page`` (pages),
    ``Top queries``/``Query`` (queries), ``Date`` (dates) or ``Filter`` (the export's
    filter list). Countries, Devices and Search appearance tables are refused. Google
    writes values shown as ``~`` or ``-`` as zeros in exports, so a ``Position`` of 0
    is read as unknown (``None``); a zero click or impression count cannot be told
    apart and is kept as exported.
    """
    label = "Search Console CSV"
    text = _read_export(path, label)
    lines = text.splitlines(keepends=True)
    index = next((i for i, item in enumerate(lines) if item.strip()), None)
    if index is None:
        raise ValidationError(f"{label} has no header")
    header = next(csv.reader([lines[index]]))
    _check_header(header, label)
    first = _norm(header[0])
    if first in GSC_OTHER_TABLES:
        raise ValidationError(f"{label}: the {_shown(header[0])} table is not used; pass the Pages, Queries, "
                              "Dates or Filters table")
    kind = GSC_TABLES.get(first)
    if kind is None:
        raise ValidationError(f"{label}: first column {_shown(header[0])!r} is not a Search Console Performance "
                              "table (Top pages, Top queries, Date or Filter)")
    rows: list[dict] = []
    if kind == "filters":
        if [_norm(cell) for cell in header] != ["filter", "value"]:
            raise ValidationError(f"{label}: a Filters table has exactly the columns Filter, Value")
        for _, cells in _rows(lines[index + 1:], index + 2, label, 2):
            rows.append({"filter": redact(cells[0], emails=True)[:100], "value": redact(cells[1], emails=True)[:200]})
        return {"kind": kind, "rows": rows}
    metrics = _gsc_metric_columns(header, label)
    seen_keys: set[str] = set()
    for line, cells in _rows(lines[index + 1:], index + 2, label, len(header)):
        key = cells[0]
        if not key:
            raise ValidationError(f"{label} line {line}: the first column is blank")
        if kind == "dates":
            key = parse_day(key, "Date", line) or ""
        if key in seen_keys:
            raise ValidationError(f"{label} line {line}: a repeated {kind[:-1]} row would be double-counted")
        seen_keys.add(key)
        row: dict = {"key": key, "line": line, "clicks": None, "impressions": None, "ctr": None, "position": None}
        for name, cell in zip(metrics, cells[1:]):
            if name in ("clicks", "impressions"):
                row[name] = parse_count(cell, name.capitalize(), line)
            elif name == "ctr":
                row[name] = parse_decimal(cell, "CTR", line, percent=True)
                if row[name] is not None and row[name] > 1:
                    raise ValidationError(f"Line {line}: CTR must be at most 100%")
            else:
                position = parse_decimal(cell, "Position", line)
                row[name] = position if position else None  # 0 = "~"/"-" in the report, i.e. unknown
        rows.append(row)
    return {"kind": kind, "rows": rows, "metrics": metrics}


def combine_gsc(tables: list[dict]) -> dict:
    """``{kind: table}``; one table of each kind (two Pages tables would double-count)."""
    if len(tables) > MAX_EXPORT_FILES:
        raise ValidationError(f"Pass at most {MAX_EXPORT_FILES} --gsc-csv files")
    combined: dict[str, dict] = {}
    for table in tables:
        if table["kind"] in combined:
            raise ValidationError(f"Two --gsc-csv files are {table['kind']} tables; pass one of each from a "
                                  "single export")
        combined[table["kind"]] = table
    return combined
