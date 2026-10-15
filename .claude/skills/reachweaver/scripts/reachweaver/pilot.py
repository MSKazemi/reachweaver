"""Offline, descriptive readout of an owner-maintained field-pilot operations log (issue #27 slice).

The owner keeps one CSV row per unit of pilot work (see ``docs/FIELD-PILOT-PROTOCOL.md`` and
``examples/pilot-log.template.csv``). ``pilot_report`` reads that file and returns per-arm
totals, rates with their numerators/denominators and per-field coverage.

Rules:

* A blank cell is **unknown**, never zero. Unknown values are left out of sums and counted
  separately; a rate uses only rows where its numerator AND denominator are both known.
* A row that cannot be parsed is reported with its line number and reasons and is left out
  of every total. It is never repaired or guessed.
* An arm comparison is **descriptive**, never causal. It is refused unless at least two arms
  are logged and every logged arm covers ``min_weeks`` distinct ISO weeks (default 2).

Nothing here opens the SQLite outbox, contacts a network, drafts, approves, schedules or
publishes. The module deliberately imports no storage, publisher or connection code.
"""
from __future__ import annotations

import csv
import math
import re
from datetime import date
from pathlib import Path

from .policy import ValidationError

KIND = "reachweaver.pilot-report"
SCHEMA_VERSION = 1
ARMS = ("manual", "scheduler", "reachweaver")
BASELINE_ARM = "manual"
COLUMNS = (
    "date", "arm", "task", "minutes_spent", "posts_published", "duplicates",
    "unrecoverable_errors", "interventions", "factual_errors_found", "clicks",
    "impressions", "downstream_conversions",
)
# minutes_spent may be fractional (timesheet); every other measure is a count.
MEASURES = COLUMNS[3:]
DECIMAL_FIELDS = frozenset({"minutes_spent"})
DEFAULT_MIN_WEEKS = 2
MAX_MIN_WEEKS = 52
MAX_TASK_LENGTH = 80
# name -> (numerator, denominator). Each rate is computed over rows where both are known.
RATES = {
    "minutes_per_post": ("minutes_spent", "posts_published"),
    "duplicate_rate": ("duplicates", "posts_published"),
    "unrecoverable_error_rate": ("unrecoverable_errors", "posts_published"),
    "interventions_per_post": ("interventions", "posts_published"),
    "factual_errors_per_post": ("factual_errors_found", "posts_published"),
    "click_through_rate": ("clicks", "impressions"),
    "conversions_per_click": ("downstream_conversions", "clicks"),
}
COMPARISON_LABEL = "descriptive_not_causal"
PROTOCOL_SECTION = "docs/FIELD-PILOT-PROTOCOL.md#4-measures-and-how-each-is-recorded"
# What each logged column means, from section 4 of the protocol. Included in every report so a number is never
# read without its definition. A blank cell is unknown for every measure; write 0 only when the value is known to be 0.
MEASURE_DEFINITIONS = {
    "minutes_spent": {"unit": "minutes", "source": "operator timer",
                      "definition": "Wall-clock operator minutes on the task, from a timer started and stopped for it, "
                                    "including research, drafting, review, approval, scheduling, publishing, checking the "
                                    "native post, reconciliation and metric export."},
    "posts_published": {"unit": "posts", "source": "native post URL",
                        "definition": "Posts confirmed in the native app under the pilot's success rule. A 'published' "
                                      "status or an API response alone is not a post."},
    "duplicates": {"unit": "posts", "source": "native account history",
                   "definition": "Published posts that repeat an earlier pilot post on the same account (the same item "
                                 "twice through a retry, or near-identical text and link). Never more than posts_published."},
    "unrecoverable_errors": {"unit": "outcomes", "source": "queue-health or the scheduler's failure list",
                             "definition": "Outcomes the workflow could not resolve by itself: a needs_review, a scheduler "
                                           "post marked failed or unknown, a manual post that had to be deleted or reposted."},
    "interventions": {"unit": "actions", "source": "operator log",
                      "definition": "Unplanned human actions such as re-authenticating, fixing a config, editing a failed "
                                    "post, a manual reconcile or a support lookup. Planned approval gates are not interventions."},
    "factual_errors_found": {"unit": "errors", "source": "post-publication audit sheet",
                             "definition": "Factual errors found in published posts by the audit that is applied to every arm."},
    "clicks": {"unit": "sessions", "source": "attribution-report (attributed.posts[].sessions)",
               "definition": "UTM-attributed landing sessions for the row's posts, one definition for every arm. Not "
                             "platform 'link clicks'."},
    "impressions": {"unit": "impressions", "source": "native export, only where documented",
                    "definition": "Native impressions only where the platform exports them with a documented definition. "
                                  "Left blank where it does not (Bluesky and Mastodon expose none): unknown, not zero."},
    "downstream_conversions": {"unit": "conversions", "source": "analytics tool, one event fixed before the pilot",
                               "definition": "Conversions on UTM-attributed sessions for the single conversion event "
                                             "defined before the pilot."},
}
RATE_NOTE = "numerator / denominator, summed over the rows where both are known; an empty denominator gives unknown"
_DECIMAL = re.compile(r"^\d+(?:\.\d+)?$")
_INTEGER = re.compile(r"^\d+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
LIMITATIONS = (
    "Descriptive only: arms run in different weeks, on different posts and audiences. "
    "No difference here shows that a workflow caused an outcome.",
    "Blank cells are unknown and are excluded, never zero-filled. Low coverage makes a rate "
    "unrepresentative even when it is computed.",
    "Values are owner-reported. The report cannot verify timesheets, native exports or "
    "click sources; keep the raw evidence the protocol lists.",
    "An API response or a 'published' status is not an outcome. Clicks, impressions and "
    "conversions must come from native exports or attribution-report.",
)


def _parse_measure(field: str, raw: str):
    """Return (value, error). A blank cell is (None, None): unknown, not zero."""
    text = raw.strip()
    if text == "":
        return None, None
    if field in DECIMAL_FIELDS:
        if not _DECIMAL.match(text):
            return None, f"{field} must be a nonnegative number or blank"
        value = float(text)
        if not math.isfinite(value):
            return None, f"{field} must be finite"
        return value, None
    if not _INTEGER.match(text):
        return None, f"{field} must be a nonnegative integer or blank"
    return int(text), None


def _parse_row(raw: dict) -> tuple[dict | None, list[str], list[str]]:
    errors, warnings = [], []
    day = None
    try:
        day = date.fromisoformat((raw.get("date") or "").strip())
    except ValueError:
        errors.append("date must be YYYY-MM-DD")
    arm = (raw.get("arm") or "").strip().lower()
    if arm not in ARMS:
        errors.append(f"arm must be one of {', '.join(ARMS)}")
    task = (raw.get("task") or "").strip()
    if not task:
        errors.append("task must not be blank")
    elif len(task) > MAX_TASK_LENGTH or _CONTROL.search(task):
        errors.append(f"task must be at most {MAX_TASK_LENGTH} printable characters")
    values = {}
    for field in MEASURES:
        value, error = _parse_measure(field, raw.get(field) or "")
        if error:
            errors.append(error)
        values[field] = value
    published, duplicates = values["posts_published"], values["duplicates"]
    if published is not None and duplicates is not None and duplicates > published:
        errors.append("duplicates cannot exceed posts_published (a duplicate is a published post)")
    clicks, impressions = values["clicks"], values["impressions"]
    if clicks is not None and impressions is not None and clicks > impressions:
        warnings.append("clicks exceed impressions; check that both come from the same native source")
    if errors:
        return None, errors, warnings
    return {"date": day, "arm": arm, "task": task, **values}, errors, warnings


def load_pilot_log(path) -> dict:
    """Parse the CSV log. Raises ``ValidationError`` only for an unusable header."""
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        fields = [(name or "").strip().lower() for name in reader.fieldnames or []]
        missing = [c for c in COLUMNS if c not in fields]
        if missing:
            raise ValidationError("Pilot log CSV needs columns " + ", ".join(COLUMNS)
                                  + "; missing: " + ", ".join(missing))
        if len(set(fields)) != len(fields):
            raise ValidationError("Pilot log CSV has duplicate column names")
        extra = sorted(set(fields) - set(COLUMNS))
        rows, malformed, warnings = [], [], []
        if extra:
            warnings.append({"line": 1, "warning": "ignored columns: " + ", ".join(extra)})
        for line, raw in enumerate(reader, start=2):
            if None in raw:  # more cells than header columns
                malformed.append({"line": line, "errors": ["row has more cells than the header"]})
                continue
            if any(value is None for value in raw.values()) and any((v or "").strip() for v in raw.values()):
                # Truncated row: missing cells are not blanks the owner chose, so do not guess.
                malformed.append({"line": line, "errors": ["row has fewer cells than the header"]})
                continue
            row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
            if not any(value.strip() for value in row.values()):
                continue  # fully blank line
            parsed, errors, row_warnings = _parse_row(row)
            warnings.extend({"line": line, "warning": w} for w in row_warnings)
            if errors:
                malformed.append({"line": line, "errors": errors})
            else:
                rows.append({**(parsed or {}), "line": line})
    return {"rows": rows, "malformed": malformed, "warnings": warnings}


def _total(rows, field):
    known = [r[field] for r in rows if r[field] is not None]
    total = sum(known) if known else None
    if isinstance(total, float):
        total = round(total, 4)
    return {"sum": total, "known_rows": len(known), "unknown_rows": len(rows) - len(known),
            "coverage": round(len(known) / len(rows), 4) if rows else None}


def _rate(rows, numerator, denominator):
    usable = [r for r in rows if r[numerator] is not None and r[denominator] is not None]
    num = sum(r[numerator] for r in usable) if usable else None
    den = sum(r[denominator] for r in usable) if usable else None
    result = {"numerator_field": numerator, "denominator_field": denominator,
              "numerator": round(num, 4) if isinstance(num, float) else num, "denominator": den,
              "rows_used": len(usable), "rows_excluded_unknown": len(rows) - len(usable),
              "value": None}
    if not usable:
        result["reason"] = "no_rows_with_both_values_known"
    elif den == 0:
        result["reason"] = "zero_denominator"
    else:
        result["value"] = round(num / den, 6)
    return result


def _arm_summary(rows):
    weeks = sorted({"%04d-W%02d" % r["date"].isocalendar()[:2] for r in rows})
    by_task = {}
    for row in rows:
        item = by_task.setdefault(row["task"], {"rows": 0, "minutes_known_rows": 0, "minutes": 0})
        item["rows"] += 1
        if row["minutes_spent"] is not None:
            item["minutes_known_rows"] += 1
            item["minutes"] += row["minutes_spent"]
    for item in by_task.values():
        item["minutes"] = round(item["minutes"], 4) if item["minutes_known_rows"] else None
    return {
        "rows": len(rows), "weeks": len(weeks), "iso_weeks": weeks,
        "first_date": min(r["date"] for r in rows).isoformat(),
        "last_date": max(r["date"] for r in rows).isoformat(),
        "totals": {field: _total(rows, field) for field in MEASURES},
        "rates": {name: _rate(rows, *pair) for name, pair in RATES.items()},
        "minutes_by_task": dict(sorted(by_task.items())),
    }


def _comparison(arms: dict, min_weeks: int) -> dict:
    reasons = []
    if len(arms) < 2:
        reasons.append("fewer_than_two_arms_logged")
    for arm, summary in arms.items():
        if summary["weeks"] < min_weeks:
            reasons.append(f"arm_{arm}_has_{summary['weeks']}_of_{min_weeks}_required_weeks")
    if reasons:
        return {"status": "refused", "label": COMPARISON_LABEL, "min_weeks": min_weeks,
                "reasons": reasons,
                "note": "No comparison is shown until every logged arm has enough weeks of data."}
    rates = {name: {arm: {k: arms[arm]["rates"][name][k]
                          for k in ("value", "numerator", "denominator", "rows_used")}
                    for arm in arms} for name in RATES}
    baseline = BASELINE_ARM if BASELINE_ARM in arms else None
    differences = {}
    if baseline:
        def exact(arm, name):  # unrounded ratio, so differences are not built from rounded values
            rate = arms[arm]["rates"][name]
            return None if rate["value"] is None else rate["numerator"] / rate["denominator"]
        for name in RATES:
            base = exact(baseline, name)
            differences[name] = {
                arm: (round(exact(arm, name) - base, 6)
                      if base is not None and exact(arm, name) is not None else None)
                for arm in arms if arm != baseline}
    return {"status": "descriptive", "label": COMPARISON_LABEL, "min_weeks": min_weeks,
            "arms": sorted(arms, key=ARMS.index), "rates": rates, "baseline_arm": baseline,
            "difference_vs_baseline": differences,
            "note": "Descriptive differences between non-randomized periods; not causal, not a "
                    "significance test and not a forecast. None means unknown."}


def pilot_report(log: dict, *, min_weeks: int = DEFAULT_MIN_WEEKS) -> dict:
    """Summarize a parsed log (``load_pilot_log`` output) per arm."""
    if type(min_weeks) is not int or not 1 <= min_weeks <= MAX_MIN_WEEKS:
        raise ValidationError(f"min_weeks must be an integer 1..{MAX_MIN_WEEKS}")
    grouped: dict[str, list[dict]] = {}
    for row in log["rows"]:
        grouped.setdefault(row["arm"], []).append(row)
    arms = {arm: _arm_summary(grouped[arm]) for arm in ARMS if arm in grouped}
    not_logged = [arm for arm in ARMS if arm not in grouped]
    warnings = list(log["warnings"])
    if not_logged:
        warnings.append({"line": None, "warning": "arms with no valid rows: " + ", ".join(not_logged)
                         + "; record a dropped arm as a protocol deviation"})
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION,
        "rows_valid": len(log["rows"]), "rows_malformed": len(log["malformed"]),
        "malformed_rows": log["malformed"], "warnings": warnings,
        "arms": arms, "arms_not_logged": not_logged,
        "comparison": _comparison(arms, min_weeks),
        "limitations": list(LIMITATIONS),
        "metric_definitions": {
            "protocol": PROTOCOL_SECTION,
            "measures": {name: dict(MEASURE_DEFINITIONS[name]) for name in MEASURES},
            "rates": {name: {"numerator": num, "denominator": den, "meaning": RATE_NOTE}
                      for name, (num, den) in RATES.items()},
            "blank_cell": "unknown; written as 0 only when the value is known to be zero",
        },
        "note": "Offline readout of an owner-maintained log. Blank = unknown, never zero. "
                "Nothing was published, fetched or written.",
    }


def _fmt(value):
    return "unknown" if value is None else str(value)


def render_report(report: dict) -> str:
    lines = [f"Pilot log: {report['rows_valid']} valid rows, {report['rows_malformed']} malformed rows"]
    for item in report["malformed_rows"]:
        lines.append(f"  MALFORMED line {item['line']}: " + "; ".join(item["errors"]))
    for item in report["warnings"]:
        where = f"line {item['line']}: " if item["line"] else ""
        lines.append(f"  WARNING {where}{item['warning']}")
    for arm, summary in report["arms"].items():
        lines.append(f"{arm}: {summary['rows']} rows, {summary['weeks']} ISO weeks "
                     f"({summary['first_date']}..{summary['last_date']})")
        for field, total in summary["totals"].items():
            lines.append(f"  {field}: {_fmt(total['sum'])} (known {total['known_rows']}/"
                         f"{total['known_rows'] + total['unknown_rows']} rows)")
        for name, rate in summary["rates"].items():
            lines.append(f"  {name}: {_fmt(rate['value'])} = {_fmt(rate['numerator'])}/"
                         f"{_fmt(rate['denominator'])} over {rate['rows_used']} rows"
                         + (f" [{rate['reason']}]" if rate.get("reason") else ""))
    comparison = report["comparison"]
    if comparison["status"] == "refused":
        lines.append("COMPARISON REFUSED: " + ", ".join(comparison["reasons"]))
    else:
        lines.append(f"COMPARISON ({comparison['label']}), baseline={_fmt(comparison['baseline_arm'])}:")
        for name, deltas in comparison["difference_vs_baseline"].items():
            lines.append(f"  {name} minus baseline: "
                         + ", ".join(f"{arm}={_fmt(v)}" for arm, v in deltas.items()))
    lines.append("Definitions of every measure and rate: see metric_definitions in --json, or "
                 + report["metric_definitions"]["protocol"] + ".")
    lines.append("Descriptive only; not causal. Blank = unknown, never zero. Nothing was published.")
    return "\n".join(lines)
