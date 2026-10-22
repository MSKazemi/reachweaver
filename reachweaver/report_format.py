"""Render the local ``report`` result as JSON, Markdown or CSV (issue #15). Pure, offline.

Every format shows the same evidence: exact values only, with per-metric coverage
(exact posts / published posts) and counts by status. A metric with no exact value prints
``null`` (JSON) or ``unknown`` (Markdown, CSV), never 0. Platform estimates are listed
separately and labelled; they are never added to an exact value.

CSV cells that a spreadsheet could read as a formula (leading ``=``, ``+``, ``-``, ``@``, tab,
carriage return, line feed, or the full-width ``＝ ＋ － ＠``) are prefixed with ``'``, following
OWASP "CSV Injection" (https://community.owasp.org/attacks/CSV_Injection, accessed 2026-10-09;
the csv module also quotes separators and quotes, which that page says must be handled). Coverage is
written as two integer columns, not ``n/N``, because spreadsheets turn ``1/3`` into a date.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .storage import METRIC_FIELDS, METRIC_STATUS_LABELS

REPORT_FORMATS = ("json", "markdown", "csv", "text")
UNKNOWN = "unknown"
CSV_COLUMNS = ("platform", "metric", "published", "posts_with_snapshot", "value", "exact_posts", "total_posts",
               *(f"{label}_posts" for label in METRIC_STATUS_LABELS if label != "exact"),
               "estimated_sum", "windows_with", "windows_without", "earliest_window_start",
               "latest_window_end", "generated_at", "posted_by_hand")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def csv_cell(value: Any) -> str:
    """One CSV cell as text; None -> ``unknown``; formula-looking text is neutralised."""
    text = UNKNOWN if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


_MD_PUNCTUATION = re.compile(r"([\\`*_\[\]()|])")


def markdown_cell(value: Any) -> str:
    """Markdown table-safe text: no control characters, raw HTML, pipes, links, emphasis or code spans.

    Backslash escapes for ASCII punctuation are CommonMark 0.31.2 section 2.4 and GFM table
    cells escape ``|`` the same way (https://spec.commonmark.org/0.31.2/#backslash-escapes,
    https://github.github.com/gfm/#tables-extension-, accessed 2026-10-09). Defence in depth:
    only allowlisted values reach these cells today.
    """
    text = _CONTROL.sub(" ", str(value))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _MD_PUNCTUATION.sub(r"\\\1", text)


_md = markdown_cell


def _summary(item: dict, metric: str) -> dict:
    """The per-metric block; rebuilt from the legacy keys if ``metrics`` is absent."""
    summary = item.get("metrics", {}).get(metric)
    if summary is not None:
        return summary
    covered, total = item["coverage_by_metric"][metric], item["published"]
    return {"value": item[metric] if covered else None, "exact": covered, "total": total,
            "coverage": f"{covered}/{total}", "status_counts": {"exact": covered, "unknown": total - covered},
            "estimated": None}


def _value_cell(summary: dict) -> str:
    if not summary["exact"] or summary["value"] is None:
        return UNKNOWN
    suffix = "" if summary["exact"] == summary["total"] else f" ({summary['coverage']})"
    return f"{summary['value']}{suffix}"


def _window_cell(item: dict) -> str:
    """Markdown-ready window cell: the stored values are escaped, the fixed wording is not."""
    window = item["window_coverage"]
    if not window["with_window"]:
        return f"not supplied ({_md(window['without_window'])} snapshots)"
    return (f"{_md(str(window['earliest_window_start'])[:10])} to {_md(str(window['latest_window_end'])[:10])} "
            f"({_md(window['with_window'])}/{_md(item['metric_coverage'])} snapshots)")


def render_json(result: dict) -> str:
    return json.dumps(result, indent=2) + "\n"


def render_markdown(result: dict, *, level: int = 1, title: str = "ReachWeaver report") -> str:
    """Markdown tables: exact values, incomplete coverage by status, separate estimates."""
    if type(level) is not int or not 1 <= level <= 5:
        raise ValueError("level must be 1..5")
    h1, h2 = "#" * level, "#" * (level + 1)
    platforms: dict = result["platforms"]
    statuses = [label for label in METRIC_STATUS_LABELS if label != "exact"]
    out = [f"{h1} {title}", "",
           f"Generated {_md(result.get('generated_at', UNKNOWN))} from the local database only. "
           f"{_md(result.get('note', ''))}".rstrip(), "",
           *([f"Window: posts sent since {_md(result['window']['since'])}, with their latest snapshot observed "
              f"since then (until {_md(result['window']['until'])}).", ""] if result.get("window") else []),
           f"{h2} Exact values (latest snapshot per published post)", "",
           "`unknown` = no post has an exact value. `(n/N)` = only n of N published posts have one.", ""]
    if not platforms:
        out += ["No published posts in this window." if result.get("window") else "No published posts.", ""]
        return "\n".join(out + _replies_markdown(result, h2))
    out += ["| Platform | Published | With snapshot | " + " | ".join(METRIC_FIELDS) + " | Observation window |",
            "|---|---|---|" + "---|" * len(METRIC_FIELDS) + "---|"]
    for platform, item in platforms.items():
        cells = [_value_cell(_summary(item, key)) for key in METRIC_FIELDS]
        by_hand = int(item.get("posted_by_hand") or 0)  # posts recorded with mark-shared --url
        out.append(f"| {_md(platform)} | {item['published']}{f' ({by_hand} posted by hand)' if by_hand else ''} | "
                   f"{item['metric_coverage']} | "
                   + " | ".join(cells) + f" | {_window_cell(item)} |")
    out += ["", f"{h2} Coverage and status (metrics without full exact coverage)", ""]
    rows = []
    for platform, item in platforms.items():
        for key in METRIC_FIELDS:
            summary = _summary(item, key)
            if summary["exact"] < summary["total"]:
                counts = summary["status_counts"]
                rows.append(f"| {_md(platform)} | {key} | {summary['coverage']} | "
                            + " | ".join(str(counts.get(label, 0)) for label in statuses) + " |")
    if rows:
        out += ["| Platform | Metric | Exact | " + " | ".join(statuses) + " |",
                "|---|---|---|" + "---|" * len(statuses), *rows]
    else:
        out.append("Every metric has an exact value for every published post.")
    if any(_summary(item, key)["status_counts"].get("suppressed", 0)
           for item in platforms.values() for key in METRIC_FIELDS):
        out += ["", "`suppressed` = withheld by the platform (for example below a privacy threshold). "
                "The withheld number is not known here and is never shown as 0."]
    out += ["", f"{h2} Platform estimates (not included in the exact values above)", ""]
    estimates = []
    for platform, item in platforms.items():
        for key in METRIC_FIELDS:
            estimate = _summary(item, key)["estimated"]
            if estimate:
                estimates.append(f"| {_md(platform)} | {key} | {estimate['posts']} | {estimate['sum']} |")
    if estimates:
        out += ["| Platform | Metric | Posts | Estimated sum |", "|---|---|---|---|", *estimates]
    else:
        out.append("None recorded.")
    return "\n".join(out + [""] + _replies_markdown(result, h2)).rstrip("\n") + "\n"


def _replies_markdown(result: dict, h2: str) -> list[str]:
    """Replies (issue #17) are counted apart from the post totals; only when the result has them."""
    replies = result.get("replies")
    if not replies:
        return []
    out = [f"{h2} Replies (counted separately, not in the totals above)", "",
           "| Platform | Published replies | By status |", "|---|---|---|"]
    for platform, item in replies.items():
        statuses = ", ".join(f"{_md(status)}={int(count)}" for status, count in sorted(item["by_status"].items()))
        out.append(f"| {_md(platform)} | {int(item['published'])} | {statuses} |")
    return out + [""]


def render_csv(result: dict) -> str:
    """One row per platform and metric; every cell passes through ``csv_cell``."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    generated = result.get("generated_at")
    for platform, item in result["platforms"].items():
        window = item["window_coverage"]
        for key in METRIC_FIELDS:
            summary = _summary(item, key)
            counts = summary["status_counts"]
            estimate = summary["estimated"]
            row = [platform, key, item["published"], item["metric_coverage"],
                   summary["value"] if summary["exact"] else None, summary["exact"], summary["total"],
                   *(counts.get(label, 0) for label in METRIC_STATUS_LABELS if label != "exact"),
                   estimate["sum"] if estimate else "", window["with_window"], window["without_window"],
                   window["earliest_window_start"] or "", window["latest_window_end"] or "", generated,
                   int(item.get("posted_by_hand") or 0)]
            writer.writerow([csv_cell(cell) for cell in row])
    return buffer.getvalue()


TOP_POSTS = 5
TEXT_COLUMNS = ("likes", "replies", "reposts", "views")
# One line wherever there are no results yet (empty report, next, start): the one-step way to record a post made
# by hand together with its numbers. The command only writes a local record; nothing is posted.
POSTED_HINT = "Posted something yourself? Record it from its link: reachweaver posted LINK --likes N"


def _count(value: Any) -> str:
    return "-" if value is None else str(value)


def summary_line(result: dict, posts: list[dict] | None = None, *, head: str = "All your recorded posts") -> str:
    """The one-line answer that opens the terminal report: ``HEAD: N posts · L likes · R replies · best: ...``.

    ``result`` is a ``report`` envelope (``platforms`` from ``storage.report``) and ``posts`` is
    ``results.post_results`` for the same window. ``posted`` prints it at a terminal as "This week so far" from
    the same functions ``report --week`` uses, so the two can never disagree. Exact values only."""
    from . import results as res
    platforms: dict = result["platforms"]
    published = sum(int(item["published"]) for item in platforms.values())
    if not published:
        return f"{head}: no published posts recorded."
    totals = []
    for key in ("likes", "replies", "reposts", "views"):
        exact = [item[key] for item in platforms.values() if item.get(key) is not None]
        if exact:
            totals.append(f"{sum(exact)} {key}")
    ranked = res.ranked(posts or [])
    summary = f"{head}: {published} post{'s' if published != 1 else ''}"
    if totals:
        summary += " · " + " · ".join(totals)
    if ranked:
        best = ranked[0]
        where = f" on {res.platform_name(best['platform'])}" if best.get("titled", True) else ""  # label names it
        summary += (f" · best: {res.quoted_title(best)}{where} "
                    f"({res.numbers_text(best['numbers'], ('likes', 'replies', 'reposts'))})")
    return term_clean(summary)


def render_text(result: dict, posts: list[dict] | None = None, *, window_label: str | None = None,
                early: str | None = None, due: list[dict] | None = None,
                handles: dict[str, str] | None = None) -> str:
    """The short terminal summary (``report`` at a terminal, or ``--format text``).

    ``posts`` is ``results.post_results`` for the same window; ``early`` the plain note from
    ``results.early_note_text``; ``due`` is ``results.results_due``. Exact values only: ``-`` means no post
    has an exact value for that count (unknown, never 0). Stored text is cleaned of control characters."""
    from . import results as res
    posts = posts or []
    platforms: dict = result["platforms"]
    head = window_label or "All your recorded posts"
    published = sum(int(item["published"]) for item in platforms.values())
    if not published:
        lines = [summary_line(result, posts, head=head), POSTED_HINT]
        return "\n".join(lines) + "\n"
    ranked = res.ranked(posts)
    lines = [summary_line(result, posts, head=head), ""]
    width = max(8, *(len(res.platform_name(p)) for p in platforms))
    lines.append("Network".ljust(width) + "  Posts  With numbers  " + "  ".join(c.capitalize().rjust(7) for c in TEXT_COLUMNS))
    for platform, item in platforms.items():
        hand = int(item.get("posted_by_hand") or 0)
        lines.append(res.platform_name(platform).ljust(width) + f"  {item['published']:>5}  {item['metric_coverage']:>12}  "
                     + "  ".join(_count(item.get(c)).rjust(7) for c in TEXT_COLUMNS)
                     + (f"   ({hand} posted by hand)" if hand else ""))
    if ranked:
        lines += ["", "Top posts by interactions (likes + replies + reposts recorded):"]
        for number, row in enumerate(ranked[:TOP_POSTS], 1):
            when = (row["posted_at"] or "")[:10]
            # No post id in this summary (UX 2026-10-21); 'reachweaver list' shows them.
            lines.append(f"  {number}. {row['interactions']:>5}  {res.quoted_title(row)}  "
                         f"{res.platform_name(row['platform'])}, {res.account_label(row['account_id'], handles)}, {when}")
    missing = [row for row in posts if row["observed_at"] is None]
    if missing:
        lines += ["", f"{len(missing)} post(s) have no numbers yet, for example:",
                  f"  {res.metrics_command(missing[0]['post_id'])}"]
    if due:
        lines += ["", f"Ready for their 7-day numbers ({len(due)}):"]
        lines += [f"  {res.quoted_title(item)} ({res.platform_name(item['platform'])}, posted "
                  f"{item['days_since_posting']} days ago): {item['command']}" for item in due[:TOP_POSTS]]
    if early:
        lines += ["", early]
    replies = result.get("replies") or {}
    if replies:
        lines += ["", "Replies are counted apart: " + ", ".join(
            f"{res.platform_name(p)} {int(item['published'])}" for p, item in replies.items())]
    lines += ["", "Exact counts only; '-' = not recorded (unknown, not zero). Counts are each post's latest numbers, "
                  "not unique people.",
              "Other views: --format markdown | csv | json; 'reachweaver list' shows the post ids for "
              "'reachweaver show POST_ID'; 'reachweaver insights' for what works."]
    return term_clean("\n".join(lines)) + "\n"


def term_clean(text: str) -> str:
    from .term import clean
    return clean(text)


def render_report(result: dict, fmt: str = "json") -> str:
    if fmt == "text":
        return render_text(result)
    if fmt == "json":
        return render_json(result)
    if fmt == "markdown":
        return render_markdown(result)
    if fmt == "csv":
        return render_csv(result)
    raise ValueError(f"Unknown report format {fmt!r}; use one of {', '.join(REPORT_FORMATS)}")
