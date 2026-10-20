"""Offline aging and SLA view over an engagement triage (issue #45 W5).

Input is the report ``engagement.triage_engagement`` already produces from an owner-exported
file. This module only re-orders and annotates it: which items still need a human, how old
they are and which are past an owner-adjustable target. It has NO network access, reads no
credential and has no send, reply, follow, like or DM path. Reply drafts stay drafts that a
human reviews and posts manually.

The SLA hours are owner-adjustable defaults, not platform data and not a claim about what
audiences expect. Reach and sentiment are optional owner-supplied export fields: they only order items
inside the same label, and an absent value is unknown, never zero or neutral.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .engagement import LABELS, PRIORITY, triage_engagement
from .optout import load_optout
from .policy import ValidationError
from .storage import parse_utc
from .inputs import read_json_file

QUEUE_SCHEMA = "reachweaver.engagement-queue.v1"
# Hours until an open item is overdue. Owner-adjustable defaults (hypothesis, not sourced).
DEFAULT_SLA_HOURS = {
    "harassment": 12, "suspicious": 24, "bug_report": 24, "technical_question": 24,
    "actionable_feedback": 72, "feature_request": 72, "collaboration_opportunity": 72, "praise": 168,
}
NEEDS_REPLY = frozenset({"draft_reply_requires_approval"})
NEEDS_HUMAN = frozenset({"human_review_required_no_draft", "human_authored_reply_only"})
COVERED = frozenset({"duplicate_thread_draft_exists"})  # a draft already exists for this thread
DO_NOT_CONTACT = frozenset({"optout_listed_no_contact", "opt_out_no_contact"})
NO_REPLY_NEEDED = frozenset({"no_reply_spam", "no_reply_suggested", "skip_owner_comment", "owner_already_replied"})
MAX_SLA_HOURS = 24 * 365
BUCKETS = (("under_1d", 24), ("1d_to_3d", 72), ("3d_to_7d", 168), ("over_7d", None))


def parse_sla(pairs: list[str] | None) -> dict[str, int]:
    """``LABEL=HOURS`` overrides on top of the defaults; unknown labels and bad hours are refused."""
    sla = dict(DEFAULT_SLA_HOURS)
    for pair in pairs or []:
        label, sep, hours = pair.partition("=")
        if not sep or label not in LABELS or not hours.isdigit() or not 1 <= int(hours) <= MAX_SLA_HOURS:
            raise ValidationError(f"--sla expects LABEL=HOURS with LABEL in {', '.join(LABELS)} and "
                                  f"HOURS 1..{MAX_SLA_HOURS}; got {pair!r}")
        sla[label] = int(hours)
    return sla


def _bucket(age_hours: float) -> str:
    for name, limit in BUCKETS:
        if limit is None or age_hours < limit:
            return name
    return BUCKETS[-1][0]  # unreachable; keeps the type checker satisfied


def _state(action: str) -> str:
    if action in NEEDS_REPLY:
        return "needs_reply_draft_review"
    if action in NEEDS_HUMAN:
        return "needs_human"
    if action in COVERED:
        return "covered_by_thread_draft"
    if action in DO_NOT_CONTACT:
        return "do_not_contact"
    return "no_reply_needed"


def build_queue(report: dict, now: str | None = None, sla_hours: dict[str, int] | None = None) -> dict:
    """Pure: annotate a triage report with age, SLA and a review order. ``now`` is ISO 8601 with a timezone."""
    current = datetime.fromisoformat(parse_utc(now) if now else datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sla = sla_hours or dict(DEFAULT_SLA_HOURS)
    queue, closed = [], {"covered_by_thread_draft": 0, "do_not_contact": 0, "no_reply_needed": 0}
    for item in report["items"]:
        state = _state(item["action"])
        if state in closed:
            closed[state] += 1
            continue
        age = max(0.0, (current - datetime.fromisoformat(parse_utc(item["created_at"]))).total_seconds() / 3600)
        target = sla.get(item["label"])
        queue.append({
            "id": item["id"], "platform": item["platform"], "account_id": item["account_id"],
            "author_handle": item["author_handle"], "label": item["label"], "state": state,
            "action": item["action"], "source_url": item["source_url"], "excerpt": item["excerpt"],
            "reach": item.get("reach"), "sentiment": item.get("sentiment"),
            "created_at": item["created_at"], "age_hours": round(age, 1), "age_bucket": _bucket(age),
            "sla_hours": target, "overdue": target is not None and age > target,
            "hours_over_sla": round(age - target, 1) if target is not None and age > target else 0.0,
        })
    queue.sort(key=lambda q: (not q["overdue"], PRIORITY[q["label"]], q["sentiment"] != "negative",
                              -q["reach"] if q["reach"] is not None else 1, -q["age_hours"], q["id"]))
    for rank, entry in enumerate(queue, start=1):
        entry["rank"] = rank
    return {
        "schema_version": QUEUE_SCHEMA, "status": "read_only_no_send", "as_of": current.isoformat(timespec="seconds"),
        "sla_hours": {label: sla[label] for label in LABELS if label in sla},
        "summary": {
            "open": len(queue), "overdue": sum(1 for q in queue if q["overdue"]),
            "by_bucket": {name: sum(1 for q in queue if q["age_bucket"] == name) for name, _ in BUCKETS},
            "by_state": {s: sum(1 for q in queue if q["state"] == s) for s in ("needs_reply_draft_review", "needs_human")},
            "closed": closed,
        },
        "queue": queue,
        "policy": {
            "network_access": False, "send_path": None, "auto_reply": False,
            "note": ("Ordered overdue first, then label priority, then (within that label) owner-supplied negative "
                     "sentiment, then higher owner-supplied reach, then oldest. Reach and sentiment come only from the "
                     "export; an absent value is unknown, never zero or neutral, and sorts after known values. SLA hours "
                     "are owner-adjustable defaults, not platform data. Replies are drafts that a human reviews and "
                     "posts manually."),
        },
    }


def queue_files(input_path: Path, *, docs_map: Path | None = None, optout_path: Path | None = None,
                now: str | None = None, sla_pairs: list[str] | None = None) -> dict:
    """Read local JSON files and return the queue; writes nothing."""
    def read(path: Path) -> object:
        try:
            return read_json_file(Path(path))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON in {path}: {exc.msg}") from exc
    report = triage_engagement(read(input_path), read(docs_map) if docs_map else None, load_optout(optout_path))
    return build_queue(report, now, parse_sla(sla_pairs))


def render_text(queue: dict) -> str:
    summary = queue["summary"]
    lines = [f"engagement queue as of {queue['as_of']}: {summary['open']} open, {summary['overdue']} overdue; "
             f"closed {summary['closed']}"]
    for q in queue["queue"]:
        flag = "OVERDUE" if q["overdue"] else "ok"
        extra = "".join(f", {name} {q[name]}" for name in ("sentiment", "reach") if q.get(name) is not None)
        lines.append(f"{q['rank']:>3}. [{flag}] {q['label']} on {q['platform']} by {q['author_handle']}, "
                     f"{q['age_hours']}h old (target {q['sla_hours']}h){extra}: {q['state']}  {q['source_url']}")
    return "\n".join(lines)
