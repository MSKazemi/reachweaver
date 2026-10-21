"""Read-only data layer for the local dashboard (``reachweaver ui``).

Every function takes an already-open **read-only** connection (``storage.connect_readonly``:
``mode=ro`` plus ``query_only``) and returns plain JSON-able dicts. Nothing here approves,
schedules, pauses, publishes, writes, reads a credential value or touches the network; the only
credential information is the *names* of environment variables that are missing. Every document
is passed through ``privacy.redact_value`` as a last layer. Post text is untrusted: consumers
must escape it (the bundled page does).
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any
from datetime import datetime, timedelta
from pathlib import Path

from . import ideas as ideas_inbox
from . import ledger, workspace
from .accounts import missing_credentials
from .catalog import OWNER_ONLY_NOTE, owner_runs
from .caps import load_caps
from .journal import NOTE as JOURNAL_NOTE
from .journal import build_entries
from .storage import shared_entries
from .ops_summary import build_summary
from .policy import LIVE_ENVIRONMENT, SUPPORTED_LIVE, ValidationError
from .post_view import build_post_view
from .privacy import redact, redact_value
from .show_view import approval_sentence, length_of
from .insights import top_insights
from .suggest import build_suggestions
from .storage import MANUAL_RECORDED_EVENT as MANUAL_RECORDED
from .storage import (MANUAL_STATUS, MEASURED_STATUSES, METRIC_FIELDS, RECONCILE_MIN_SENDING_MINUTES,
                      approval_expiry_problem, approval_max_age_hours,
                      connect_readonly, get_post, latest_snapshot, list_posts, metric_states, parse_utc, report,
                      report_since, schema_gaps, snapshots_for, utc_now)

REQUIRED_TABLES = ("posts", "event_log", "settings")
LIST_LIMIT = 500
EVENT_LIMIT = 200
EXCERPT = 160
JOURNAL_LIMIT = 100
SCHEDULE_DAYS = 14
SUGGESTION_LIMIT = 5
RESULTS_LIMIT = 100
CAMPAIGN_LIMIT = 200
SEARCH_LIMIT = 50
SEARCH_MAX_CHARS = 100
MAX_SCHEDULE_DAYS = 62
ATTENTION_LIMIT = 20
ATTENTION_HORIZON_DAYS = 36500
# Rows a human has to look at first, in this order.
ATTENTION_ORDER = {"needs_review": 0, "sending": 1, "approved": 2, "scheduled": 3, "draft": 4,
                   "published": 5, MANUAL_STATUS: 5, "cancelled": 6}
# Plain-language meaning of each approval-ledger state (``ledger.audit``) and problem code.
LEDGER_SENTENCE = {
    "ok": "The approval ledger matches the database.",
    "absent": "No approval ledger yet. That is normal until the first approval with this version.",
    "not_checked": "The approval ledger could not be checked for this database.",
    "broken": "The approval ledger does not match the database. Sends for the affected approvals are refused.",
    "unavailable": "The approval ledger is missing or unreadable, so approvals cannot be re-checked and sends are refused.",
}
LEDGER_PROBLEM = {
    ledger.REPLAY: "Its approval was already used once. Approve it again in your terminal before it can be sent.",
    ledger.WITHDRAWN: ("Its approval was withdrawn earlier, so this approved copy came back from an older database. "
                       "Approve it again in your terminal before it can be sent."),
    ledger.BROKEN: "The ledger has no record of the approval this post was sent under. Check the post on the platform.",
    ledger.UNAVAILABLE: "The ledger file cannot be read. Restore state/approval-ledger.jsonl next to the database.",
}


@contextmanager
def open_database(path: str | Path):
    """Open an existing ReachWeaver database read-only; refuse one that predates the schema.

    Never creates, migrates or writes (unlike ``storage.connect``)."""
    with connect_readonly(path) as con:
        gaps = schema_gaps(con, REQUIRED_TABLES)
        if gaps:
            raise ValidationError(f"{path} predates this version's schema (missing {', '.join(gaps)}); the "
                                  "dashboard is read-only and never migrates. Run 'reachweaver init' once to "
                                  "upgrade it, then retry")
        yield con


def load_accounts_view(loader) -> tuple[dict, str | None]:
    """(accounts, error): a missing or invalid accounts file degrades the page, it does not stop it."""
    try:
        return loader(), None
    except ValidationError as exc:
        return {}, str(exc)


def overview(con, accounts: dict, accounts_error: str | None, *, optout: list[dict] | None = None,
             optout_configured: bool = False) -> dict:
    """The redacted ops-summary document (readiness, per-account state, queue, dead letters) plus ``headline``:
    what the Overview's banner says (see ``headline``)."""
    try:
        (caps, caps_path), caps_error = load_caps(), None
    except ValidationError as exc:
        (caps, caps_path), caps_error = (None, None), str(exc)
    summary = build_summary(con, accounts, accounts_error=accounts_error, caps=caps,
                            caps_path=str(caps_path) if caps_path else None, caps_error=caps_error,
                            optout=optout, optout_configured=optout_configured)
    summary["headline"] = headline(summary)
    return summary


# Attention-level readiness reasons that are a real problem for anyone, including a creator who only posts by hand:
# something was sent or is queued to be sent and needs a human. Every "blocked" reason is a problem too. The other
# attention reasons (a missing credential of an account that sends nothing, aging research facts, an unrecorded
# token expiry ...) only matter for automatic sending; the Overview folds them into its setup details.
PROBLEM_CODES = frozenset({
    "emergency_pause_active", "needs_review", "stale_sending", "overdue_scheduled", "approval_rebind_required",
    "manual_platform_has_queued_posts", "queued_posts_for_unconfigured_accounts", "provider_cooldown_active",
    "rolling_budget_exhausted", ledger.REPLAY, ledger.WITHDRAWN, ledger.BROKEN, ledger.UNAVAILABLE,
})


def headline(summary: dict) -> dict:
    """What the Overview leads with: ``level`` is ``blocked`` (emergency stop on, or a blocked reason), ``attention``
    (a real problem such as an uncertain send), ``setup`` (no problem; automatic sending is not set up, which a
    creator who posts by hand does not need) or ``ok``. ``problems`` are the reasons the banner names; the emergency
    stop and uncertain sends are never left out. ``setup_notes`` counts the reasons folded into the details."""
    glob = summary["global"]
    problems = [r for r in summary["reasons"] if r["level"] == "blocked" or r["code"] in PROBLEM_CODES]
    if glob["emergency_paused"] and not any(r["code"] == "emergency_pause_active" for r in problems):
        problems.insert(0, {"level": "blocked", "code": "emergency_pause_active", "account_id": None})
    review = int(glob["status_counts"].get("needs_review", 0))
    if review and not any(r["code"] == "needs_review" for r in problems):  # e.g. a post of an unconfigured account
        problems.append({"level": "attention", "code": "needs_review", "account_id": None})
    live_ready = any(a["adapter"] != "manual" and not a["credentials_missing"] for a in summary["accounts"])
    automatic = bool(glob["live_opt_in_env"]) and glob["runtime_environment"] == LIVE_ENVIRONMENT and live_ready
    if glob["emergency_paused"] or any(r["level"] == "blocked" for r in problems):
        level = "blocked"
    elif problems:
        level = "attention"
    else:
        level = "ok" if automatic else "setup"
    return {"level": level, "problems": problems, "needs_review": review,
            "setup_notes": len(summary["reasons"]) - sum(1 for r in summary["reasons"] if r in problems),
            "automatic_sending": automatic}


NEXT_SECONDARY = 3
WRITE_LABEL = "Write a new post"


def next_view(guide: dict) -> dict:
    """The Overview's "Your next step" card from the ``start`` guide (``start.build_guide``, same order): one
    ``primary`` step and at most ``NEXT_SECONDARY`` others. When nothing is waiting, the primary step is writing a new
    post in the browser composer (``share --web``), which needs no account. Each step says whether only the owner may
    run its command (``catalog.owner_runs``). Commands are text; nothing is run."""
    from .start import SHARE_WEB  # start imports this module: a late import avoids the cycle
    steps = [dict(s, owner_only=bool(s.get("command") and owner_runs(s["command"])))
             for s in guide["steps"] if s.get("command") != "reachweaver ui"]
    waiting = guide.get("state") not in ("empty", "idle")
    write = {"label": WRITE_LABEL, "command": SHARE_WEB,
             "note": "Opens a page on this computer that turns one post into a ready-to-paste version for each "
                     "network. You post it yourself; nothing is sent.", "owner_only": False}
    if waiting and steps:
        primary, rest = steps[0], steps[1:]
        if not any(s.get("command") == SHARE_WEB for s in rest):
            # The easiest way to make something new stays one glance away, but never ahead of a stop or an uncertain send.
            rest.insert(len(rest) if guide.get("state") in ("needs_review", "paused") else 0, write)
    else:
        primary, rest = write, [s for s in steps if s.get("command") != SHARE_WEB]
    return dict(guide, primary=primary, secondary=rest[:NEXT_SECONDARY], more=max(0, len(rest) - NEXT_SECONDARY))


def _excerpt(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= EXCERPT else flat[:EXCERPT - 1] + "…"


def posts_list(con, *, status: str | None = None, account_id: str | None = None,
               campaign: str | None = None, platform: str | None = None) -> dict:
    """Posts needing a human first, then newest; excerpts only (full text is in the review sheet).

    The excerpt is the stored text, not redacted: a reviewer must see what would be sent. Every other
    field is redacted, and ``looks_like_secret`` flags a text the redactor would have changed."""
    rows = list_posts(con, status=status, account_id=account_id, platform=platform, campaign=campaign)
    rows = sorted(rows, key=lambda r: r["created_at"], reverse=True)  # newest first within a status
    rows = sorted(rows, key=lambda r: ATTENTION_ORDER.get(r["status"], 9))  # stable: attention order wins
    total = len(rows)
    shown = rows[:LIST_LIMIT]
    items = redact_value([{"id": r["id"], "account_id": r["account_id"], "platform": r["platform"],
                           "status": r["status"], "campaign": r["campaign"], "scheduled_at": r["scheduled_at"],
                           "created_at": r["created_at"], "approved_at": r["approved_at"],
                           "remote_url": r["remote_url"], "last_error": r["last_error"]} for r in shown])
    for item, row in zip(items, shown):
        item["excerpt"] = _excerpt(row["content"])
        item["looks_like_secret"] = redact(row["content"]) != row["content"]
    return {"total": total, "shown": len(items), "truncated": total > len(items), "posts": items}


def next_steps(post) -> list[dict]:
    """CLI commands a human could run next, as text only. The dashboard never runs them."""
    pid, status = post["id"], post["status"]
    steps: list[dict] = []
    if status == "draft":
        steps = [{"label": "Check the draft", "command": f"reachweaver check {pid}"},
                 {"label": "Approve this exact text (you, in a terminal)",
                  "command": f"reachweaver approve {pid} --approver YOUR_NAME"}]
    elif status in ("approved", "scheduled"):
        steps = [{"label": "Preview the send (dry run, no network)", "command": f"reachweaver publish --post-id {pid}"},
                 {"label": "Withdraw approval", "command": f"reachweaver unapprove {pid}"}]
    elif status == "needs_review":
        steps = [{"label": "Check the native platform, then record the outcome",
                  "command": f"reachweaver reconcile {pid} --published --remote-url URL",
                  "note": "If you find it never posted, use --not-published instead of --published and --remote-url."}]
    elif status == "sending":
        steps = [{"label": "Inspect health for a stale send", "command": "reachweaver health"}]
    elif status == MANUAL_STATUS:  # posted by hand: only results can be added; it can never be sent
        steps = [{"label": "Record its results from the platform's own analytics",
                  "command": f"reachweaver metrics {pid} --observed-at TIME --source native-export --views N --likes N"}]
    return steps


def _events(con, post_id: str) -> list[dict]:
    rows = con.execute("SELECT at, action, detail FROM event_log WHERE post_id=? ORDER BY id DESC LIMIT ?",
                       (post_id, EVENT_LIMIT)).fetchall()
    return [{"at": r["at"], "action": r["action"], "detail": r["detail"],
             **event_sentence(r["action"], r["detail"])} for r in rows]


DETAIL_PAIR = re.compile(r"([a-z_]{1,40})=(\S{1,200})")


def event_sentence(action: str, detail: str | None) -> dict:
    """A timeline entry in plain words: ``label`` (as on the activity feed), ``tone`` and ``sentence``, a short
    sentence built from a few known ``key=value`` fields of the stored detail (never the raw detail; the page keeps
    that behind a "Technical details" toggle). Unknown fields are left out, never guessed."""
    label, tone = ACTIVITY_LABEL.get(action, (action.replace("_", " ").capitalize(), ""))
    fields = dict(DETAIL_PAIR.findall(detail or ""))
    where = " on " + fields["platform"] if fields.get("platform") not in (None, "unknown") else ""
    account = fields.get("account") if fields.get("account") not in (None, "unknown") else None
    sentence = None
    if action == "draft_created":
        sentence = "Draft written" + (f" for {account}" if account else "") + where + "."
    elif action == MANUAL_RECORDED:
        day = (fields.get("posted_at") or "")[:10]
        sentence = ("You recorded that you posted it yourself" + where + (f" on {day}" if day else "")
                    + ". ReachWeaver did not send it.")
        label, tone = "Posted by you", "ok"
    elif action == "metrics_recorded":
        source = fields.get("source")
        sentence = "Results recorded" + (f" from {source.replace('-', ' ')}" if source else "") + "."
        if "estimated" in fields.get("statuses", ""):
            sentence += " Some values are the platform's estimates."
    elif action == "approve" and fields.get("approver"):
        sentence = f"Approved by {fields['approver']}."
    return redact_value({"label": label, "tone": tone, "sentence": sentence})


def post_detail(con, post_id: str, accounts: dict) -> dict:
    """The review sheet for one post: the ``show`` document plus its event timeline and next steps."""
    post = get_post(con, post_id)  # ValidationError("Unknown post id") for a missing id
    account = accounts.get(post["account_id"])
    if account is not None and account["platform"] != post["platform"]:
        account = None
    detail = build_post_view(con, post, account)
    detail["events"] = _events(con, post_id)
    detail["next_steps"] = next_steps(post)
    detail["last_error"] = post["last_error"]
    detail["remote_url"] = post["remote_url"]
    detail["length"] = length_of(detail)  # the platform's own count and limit, or None (never guessed)
    detail["approval_summary"] = approval_sentence(detail)
    published = next((e["at"] for e in reversed(detail["events"]) if e["action"] == "published"), None)
    if post["status"] == MANUAL_STATUS:  # the time the owner declared he posted it by hand
        published = post["send_started_at"]
    detail["posted_by_hand"] = post["status"] == MANUAL_STATUS
    detail["posted_at"] = published  # when it went out (sent, or declared posted by hand); None before that
    detail["results"] = post_results(con, post_id, published)  # recorded snapshots only; [] when none
    detail["owner_action"] = owner_action(post, _owner_kind(post, approval_max_age_hours(con), utc_now()))
    exact = post["content"]
    detail = redact_value(detail)  # errors, events and previews may quote a secret
    detail["content"] = exact  # the exact text a reviewer approves is never altered ...
    detail["content_looks_like_secret"] = redact(exact) != exact  # ... but a likely secret in it is flagged
    return detail


def accounts_view(accounts: dict, error: str | None) -> dict:
    """Configured accounts: platform, adapter kind and the NAMES of missing credential variables."""
    rows = []
    for name, account in sorted(accounts.items()):
        backend = account.get("backend")
        rows.append({"id": name, "platform": account["platform"], "handle": account.get("handle"),
                     "adapter": "backend" if backend is not None else
                     ("live_text" if account["platform"] in SUPPORTED_LIVE else "manual"),
                     "backend": backend, "credentials_missing": sorted(missing_credentials(account))})
    return redact_value({"error": error, "accounts": rows})


def _file_counts(files: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in files:
        counts[item.get("kind", "file")] = counts.get(item.get("kind", "file"), 0) + 1
    return counts


def journal_view(con, *, platform: str | None = None, account: str | None = None) -> dict:
    """What was posted, newest first (``journal``): ReachWeaver sends plus posts recorded as shared by hand.

    Files are not opened or hashed. The excerpt is the posted text as stored; every other field is redacted."""
    entries = build_entries(con, platform=platform, account=account, limit=JOURNAL_LIMIT + 1)
    shown = entries[:JOURNAL_LIMIT]
    keep = ("id", "source", "when", "platform", "account", "status", "campaign", "remote_url", "approved_by",
            "approved_at", "approval_sha256", "note")
    items = redact_value([{k: e.get(k) for k in keep} for e in shown])
    for item, entry in zip(items, shown):
        text = entry.get("text") or ""
        item["excerpt"] = _excerpt(text)
        item["looks_like_secret"] = redact(text) != text
        item["files"] = _file_counts(entry.get("files") or [])
    return {"entries": items, "shown": len(items), "truncated": len(entries) > JOURNAL_LIMIT, "note": JOURNAL_NOTE}


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(parse_utc(value))
    except ValidationError:
        return None


def _schedule_item(row, now: str, max_age: int | None) -> dict:
    item = redact_value({"id": row["id"], "status": row["status"], "account_id": row["account_id"],
                         "platform": row["platform"], "campaign": row["campaign"], "scheduled_at": row["scheduled_at"],
                         "approved_at": row["approved_at"], "last_error": row["last_error"]})
    item["excerpt"] = _excerpt(row["content"])
    item["looks_like_secret"] = redact(row["content"]) != row["content"]
    item.update(approval_expires_at=None, approval_expired=False, expires_before_due=False)
    if max_age is not None and row["status"] in ("approved", "scheduled"):
        item["approval_expired"] = approval_expiry_problem(row, max_age, now) is not None
        approved, due = _utc(row["approved_at"]), _utc(row["scheduled_at"])
        if max_age > 0 and approved is not None:
            expires = approved + timedelta(hours=max_age)
            item["approval_expires_at"] = expires.isoformat(timespec="seconds")
            item["expires_before_due"] = due is not None and expires < due
    return item


def _by_due(item: dict) -> tuple[str, str]:
    return item["scheduled_at"] or "", item["id"]


def schedule_view(con, *, now: str | None = None, days: int = SCHEDULE_DAYS) -> dict:
    """What is coming: approved, scheduled and sending posts by due time (UTC). Nothing is changed.

    ``overdue`` is past its time and not sent; ``upcoming`` is due within ``days``; ``later`` only counts what is
    further out; ``unscheduled`` is approved with no (readable) time, so a worker you run decides when."""
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    horizon = current + timedelta(days=days)
    max_age = approval_max_age_hours(con)
    overdue: list[dict] = []
    upcoming: list[dict] = []
    unscheduled: list[dict] = []
    sending: list[dict] = []
    later = 0
    for row in list_posts(con):
        if row["status"] not in ("approved", "scheduled", "sending"):
            continue
        item = _schedule_item(row, now, max_age)
        due = _utc(row["scheduled_at"])
        if row["status"] == "sending":
            sending.append(item)
        elif due is None:  # no time, or one that cannot be read: shown as such, never guessed
            unscheduled.append(item)
        else:
            item["scheduled_at"] = due.isoformat(timespec="seconds")
            if due < current:
                overdue.append(item)
            elif due < horizon:
                upcoming.append(item)
            else:
                later += 1
    overdue.sort(key=_by_due)
    upcoming.sort(key=_by_due)
    unscheduled.sort(key=lambda item: (item["approved_at"] or "", item["id"]))
    return {"now": now, "days": days, "approval_max_age_hours": max_age, "overdue": overdue, "upcoming": upcoming,
            "sending": sending, "unscheduled": unscheduled, "later": later}


# What a human has to look at, most urgent first; a post is listed once, under its most urgent kind.
ATTENTION_KINDS = ("needs_review", "sending", "approval_expired", "overdue", "expires_before_due", "draft")


def attention_view(con, *, now: str | None = None) -> dict:
    """Status counts for the navigation badges plus the posts that need a human, by kind. Nothing is changed.

    ``kinds`` counts every post of each kind; ``items`` lists at most ``ATTENTION_LIMIT`` of them, most urgent
    first. Drafts are listed oldest first: the longest-waiting draft is the one most likely forgotten."""
    plan = schedule_view(con, now=now, days=ATTENTION_HORIZON_DAYS)  # every scheduled post, however far out
    counts: dict[str, int] = {}
    waiting = []
    for row in list_posts(con):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] in ("needs_review", "draft"):
            waiting.append(row)
    found: dict[str, tuple[str, dict]] = {}
    for item in plan["sending"]:
        found[item["id"]] = ("sending", item)
    for group in ("overdue", "upcoming", "unscheduled"):
        for item in plan[group]:
            kind = ("approval_expired" if item["approval_expired"] else "overdue" if group == "overdue"
                    else "expires_before_due" if item["expires_before_due"] else None)
            if kind:
                found[item["id"]] = (kind, item)
    for row in sorted(waiting, key=lambda r: (r["created_at"] or "", r["id"])):
        item = redact_value({"id": row["id"], "status": row["status"], "account_id": row["account_id"],
                             "platform": row["platform"], "campaign": row["campaign"],
                             "scheduled_at": row["scheduled_at"], "approved_at": row["approved_at"],
                             "last_error": row["last_error"], "created_at": row["created_at"]})
        item["excerpt"] = _excerpt(row["content"])
        item["looks_like_secret"] = redact(row["content"]) != row["content"]
        found[row["id"]] = (row["status"], item)
    order = {kind: i for i, kind in enumerate(ATTENTION_KINDS)}
    ranked = sorted(found.values(), key=lambda pair: order[pair[0]])  # stable: each kind keeps its own order
    kinds = {kind: 0 for kind in ATTENTION_KINDS}
    for kind, _ in ranked:
        kinds[kind] += 1
    items = [dict(item, kind=kind) for kind, item in ranked[:ATTENTION_LIMIT]]
    return {"now": plan["now"], "counts": counts, "kinds": kinds, "total": len(ranked), "items": items,
            "truncated": len(ranked) > len(items)}


def safety_view(con, db_path: Path, accounts: dict | None, accounts_path: Path, base: Path,
                root: Path | None) -> dict:
    """Approval-ledger integrity plus the folder check of ``workspace`` (files of unsent posts, private
    permissions, a run in progress), in plain language. Hashes the files of unsent posts; changes nothing."""
    audit = ledger.audit(con)
    report = workspace.inspect(con, db_path, accounts, accounts_path, base, root)
    problems = [{"post_id": p["post_id"], "code": p["reason"],
                 "sentence": LEDGER_PROBLEM.get(p["reason"], "See docs/TROUBLESHOOTING.md for this code.")}
                for p in audit["problems"]]
    # The ledger has its own section above, and the Accounts page reports a missing accounts file.
    checks = [p for p in report["problems"]
              if p["code"] != "accounts_file_missing" and not p["code"].startswith("approval_ledger_")]
    # `workspace` prints permissions from its path table rather than as problems; the card says them plainly.
    for entry in report["paths"]:
        if entry.get("private") is False:
            mode = "700" if entry["name"] == "archive" else "600"
            checks.append({"code": "readable_by_other_users", "level": "warning",
                           "detail": f"the {entry['name']} ({entry['path']}) can be read by other users of this computer",
                           "advice": f"chmod {mode} {entry['path']}"})
    return redact_value({"ledger": {"status": audit["status"], "sentence": LEDGER_SENTENCE.get(audit["status"], ""),
                                    "problems": problems},
                         "workspace": report["workspace"], "integrity": report["database"].get("integrity"),
                         "checks": checks, "ready_to_copy": report["ready_to_copy"]})


def suggestions_view(con) -> dict:
    """Offline suggestions from the local database (``suggest --include-db``), each needing a human.

    Publishing-safety items are left out: the Overview already lists them as status reasons."""
    result = build_suggestions(con=con, limit=50)
    actions = [a for a in result["actions"] if a["kind"] != "publishing_safety"]
    keep = ("priority", "kind", "signal", "title", "next_step", "platform", "account_id", "confidence")
    return redact_value({"actions": [{**{k: a[k] for k in keep}, "evidence_source": a["evidence"]["source"]}
                                     for a in actions[:SUGGESTION_LIMIT]],
                         "more": max(0, len(actions) - SUGGESTION_LIMIT), "limitations": result["limitations"][:2]})


RESULTS_NOTE = ("Only what you recorded from each platform's own analytics (metrics, import-metrics, metrics-fetch). "
                "'unknown' means no exact value was recorded, never zero; platform estimates are shown apart and never "
                "added to exact counts. A number here is a local record, not proof of reach.")


def _snapshot_view(row, posted_at: str | None) -> dict | None:
    """One snapshot: observed time, source, window, age since posting, and every metric as (state, value)."""
    if row is None:
        return None
    states = metric_states(row)
    age = None
    if posted_at and row["observed_at"]:
        try:
            seconds = (datetime.fromisoformat(parse_utc(row["observed_at"])) -
                       datetime.fromisoformat(parse_utc(posted_at))).total_seconds()
            age = round(seconds / 3600, 1) if seconds >= 0 else None
        except ValidationError:
            age = None
    keys = row.keys()
    return redact_value({"observed_at": row["observed_at"], "source": row["source"], "age_hours": age,
                         "window_start": row["window_start"] if "window_start" in keys else None,
                         "window_end": row["window_end"] if "window_end" in keys else None,
                         "metrics": {k: {"state": states[k][0], "value": states[k][1]} for k in METRIC_FIELDS}})


RESULTS_WINDOWS = ("7d", "30d")  # the Results page's "This week" and "30 days"; anything else is every post
INTERACTIONS = ("likes", "replies", "reposts")
TOP_POSTS = 3


def results_view(con, *, account: str | None = None, platform: str | None = None, since: str | None = None,
                 now: str | None = None) -> dict:
    """Recorded results: per-platform totals of exact values (``storage.report``) and each published post's latest
    snapshot, newest post first. Nothing is fetched; a post without a snapshot is listed as not recorded.

    ``since`` (``7d`` or ``30d``, as ``report --since``) keeps posts that went out in that window, and their
    snapshots taken since its start. ``summary`` adds up the exact latest values of the posts shown (a metric no post
    has an exact value for is None, never 0) and names the top posts by recorded interactions."""
    if since is not None and since not in RESULTS_WINDOWS:
        raise ValidationError("since must be one of " + ", ".join(RESULTS_WINDOWS))
    start = report_since(since, now) if since else None
    entries = build_entries(con, statuses=MEASURED_STATUSES, account=account, platform=platform, by_hand=False,
                            since=start, limit=RESULTS_LIMIT + 1)
    shown = entries[:RESULTS_LIMIT]
    posts = []
    for entry in shown:
        item = redact_value({k: entry.get(k) for k in ("id", "when", "platform", "account", "campaign", "remote_url")})
        item["posted_by_hand"] = entry.get("status") == MANUAL_STATUS  # labelled "posted by hand" on the page
        text = entry.get("text") or ""
        item["excerpt"] = _excerpt(text)
        item["looks_like_secret"] = redact(text) != text
        item["latest"] = _snapshot_view(latest_snapshot(con, entry["id"]), entry.get("when"))
        if start and item["latest"] and (item["latest"]["observed_at"] or "") < start:
            item["latest"] = None  # recorded before the window began: not a result of this window
        values = [_exact(item["latest"], k) for k in INTERACTIONS]
        item["interactions"] = sum(v for v in values if v is not None) if any(v is not None for v in values) else None
        posts.append(item)
    totals: dict[str, int | None] = {}
    for key in METRIC_FIELDS:
        exact = [v for v in (_exact(p["latest"], key) for p in posts) if v is not None]
        totals[key] = sum(exact) if exact else None
    top = sorted((p for p in posts if p["interactions"]), key=lambda p: (-p["interactions"], p["when"] or ""))
    summary_doc = {"window": since or "all", "start": start, "posts": len(posts),
                   "with_record": sum(1 for p in posts if p["latest"] is not None), "totals": totals,
                   "top": [p["id"] for p in top[:TOP_POSTS]]}
    platforms = []
    for name, summary in report(con, since=start).items():
        if platform is not None and name != platform:
            continue
        platforms.append({"platform": name, "published": summary["published"],
                          "posted_by_hand": summary.get("posted_by_hand", 0),
                          "with_snapshot": summary["metric_coverage"],
                          "metrics": {k: {"value": summary["metrics"][k]["value"],
                                          "coverage": summary["metrics"][k]["coverage"],
                                          "estimated": summary["metrics"][k]["estimated"]} for k in METRIC_FIELDS}})
    recorded = sum(1 for p in posts if p["latest"] is not None)
    return {"metrics": list(METRIC_FIELDS), "platforms": platforms, "posts": posts, "shown": len(posts),
            "with_snapshot": recorded, "truncated": len(entries) > RESULTS_LIMIT, "summary": summary_doc,
            "platform_totals_scope": ("all published posts of each platform (not filtered by account)" if not start
                                      else "posts of each platform that went out in this window (not filtered by "
                                           "account)"),
            "note": RESULTS_NOTE, "insights": insights_panel(con, account=account, platform=platform)}


INSIGHTS_NOTE = ("Observations from your own past posts (interactions = likes + replies + reposts about 7 days after "
                 "posting), not causes. Each line is a hypothesis to test with experiment-preregister. Details and "
                 "every group: reachweaver insights.")


def insights_panel(con, *, account: str | None = None, platform: str | None = None) -> dict:
    """'What works for you': up to three lines per account from ``insights.top_insights`` (read-only)."""
    try:
        lines = top_insights(con, account_id=account, platform=platform)
    except ValidationError:
        lines = []
    keep = ("account_id", "platform", "direction", "feature", "value", "n", "median", "rest_n", "rest_median",
            "relative_difference", "text")
    return {"lines": [redact_value({k: line[k] for k in keep}) for line in lines], "note": INSIGHTS_NOTE}


def post_results(con, post_id: str, posted_at: str | None) -> list[dict]:
    """Every recorded snapshot of one post, oldest first, for the post page."""
    rows = sorted(snapshots_for(con, post_id), key=lambda r: (r["observed_at"] or "", r["id"]))
    return [view for view in (_snapshot_view(r, posted_at) for r in rows) if view is not None]


def _exact(view: dict | None, key: str) -> int | None:
    if view is None:
        return None
    metric = view["metrics"][key]
    return metric["value"] if metric["state"] == "exact" else None


def campaigns_view(con) -> dict:
    """Every campaign named on a post: status counts, platforms, accounts and last activity, newest first."""
    groups: dict[str, dict] = {}
    for row in list_posts(con):
        name = row["campaign"] or ""
        if not name:
            continue
        group = groups.setdefault(name, {"campaign": name, "posts": 0, "status_counts": {}, "platforms": set(),
                                         "accounts": set(), "first_created": row["created_at"],
                                         "last_activity": row["created_at"]})
        group["posts"] += 1
        group["status_counts"][row["status"]] = group["status_counts"].get(row["status"], 0) + 1
        group["platforms"].add(row["platform"])
        group["accounts"].add(row["account_id"])
        for stamp in (row["created_at"], row["approved_at"], row["scheduled_at"]):
            if stamp and stamp > (group["last_activity"] or ""):
                group["last_activity"] = stamp
        if row["created_at"] < (group["first_created"] or row["created_at"]):
            group["first_created"] = row["created_at"]
    variants = dict(con.execute("SELECT campaign_id, COUNT(*) FROM campaign_variants GROUP BY campaign_id").fetchall())
    items = []
    for group in groups.values():
        group["platforms"], group["accounts"] = sorted(group["platforms"]), sorted(group["accounts"])
        group["staged_variants"] = variants.get(group["campaign"], 0)
        items.append(group)
    items.sort(key=lambda g: (g["last_activity"] or "", g["campaign"]), reverse=True)
    unnamed = sum(1 for row in list_posts(con) if not (row["campaign"] or ""))
    return redact_value({"campaigns": items[:CAMPAIGN_LIMIT], "truncated": len(items) > CAMPAIGN_LIMIT,
                         "posts_without_campaign": unnamed})


def campaign_detail(con, campaign: str) -> dict:
    """One campaign: each post side by side (status, platform, account, excerpt, schedule, latest exact results)
    and the staged variant records. Raises ValidationError for an unknown campaign."""
    rows = list_posts(con, campaign=campaign)
    if not rows:
        raise ValidationError("Unknown campaign")
    staged = {r["post_id"]: {"variant_id": r["variant_id"], "brand_id": r["brand_id"]} for r in con.execute(
        "SELECT post_id, variant_id, brand_id FROM campaign_variants WHERE campaign_id=?", (campaign,)).fetchall()}
    posts = []
    for row in sorted(rows, key=lambda r: (ATTENTION_ORDER.get(r["status"], 9), r["platform"], r["account_id"])):
        latest = _snapshot_view(latest_snapshot(con, row["id"]), None)
        item = redact_value({"id": row["id"], "status": row["status"], "platform": row["platform"],
                             "account_id": row["account_id"], "scheduled_at": row["scheduled_at"],
                             "approved_at": row["approved_at"], "remote_url": row["remote_url"],
                             "last_error": row["last_error"], "variant": staged.get(row["id"])})
        item["excerpt"] = _excerpt(row["content"])
        item["looks_like_secret"] = redact(row["content"]) != row["content"]
        item["chars"] = len(row["content"])
        item["results"] = ({k: _exact(latest, k) for k in METRIC_FIELDS} | {"observed_at": latest["observed_at"]}
                           if latest else None)
        posts.append(item)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"campaign": redact(campaign), "posts": posts, "status_counts": counts,
            "note": "Each post is approved, scheduled and sent on its own; a campaign groups them for review only."}


def _contains(haystack: object, needle: str) -> bool:
    return isinstance(haystack, str) and needle in haystack.casefold()


def search(con, query: str) -> dict:
    """Posts (any status), campaign names and hand-shared records whose text, id, account, platform, campaign,
    note or link contains ``query`` (case-insensitive). Read-only; at most ``SEARCH_LIMIT`` of each kind."""
    needle = " ".join(query.split()).casefold()
    if not 1 <= len(needle) <= SEARCH_MAX_CHARS:
        raise ValidationError(f"search needs 1 to {SEARCH_MAX_CHARS} characters")
    rows = {row["id"]: row for row in list_posts(con)}
    entries = shared_entries(con)
    # A hand-shared record linked to its measurable published_manual post (mark-shared --url) is the same post:
    # a match on the record's note or link finds the post, and the record is not listed a second time.
    linked = {e["post_id"]: e for e in entries if e.get("post_id") in rows}
    posts, campaigns = [], set()
    for row in sorted(rows.values(), key=lambda r: r["created_at"], reverse=True):
        fields = (row["content"], row["id"], row["account_id"], row["platform"], row["campaign"], row["remote_url"])
        record = linked.get(row["id"]) or {}
        if any(_contains(f, needle) for f in fields) or any(_contains(record.get(k), needle) for k in ("note", "url")):
            posts.append(row)
        if _contains(row["campaign"], needle):
            campaigns.add(row["campaign"])
    hits = []
    for row in posts[:SEARCH_LIMIT]:
        went_out = row["status"] in MEASURED_STATUSES  # published or posted by hand: show when, not when recorded
        item = redact_value({"id": row["id"], "status": row["status"], "platform": row["platform"],
                             "account_id": row["account_id"], "campaign": row["campaign"] or None,
                             "created_at": row["created_at"],
                             "posted_at": row["send_started_at"] if went_out else None})
        item["excerpt"] = _excerpt(row["content"])
        item["looks_like_secret"] = redact(row["content"]) != row["content"]
        hits.append(item)
    shared = []
    for entry in entries:
        if entry.get("post_id") in rows:
            continue  # listed as its post above (when it matches)
        if any(_contains(entry.get(k), needle) for k in ("content", "note", "url", "platform", "account_id")):
            item = redact_value({"id": entry["id"], "platform": entry["platform"], "account_id": entry["account_id"],
                                 "when": entry["posted_at"] or entry["recorded_at"], "url": entry["url"] or None,
                                 "note": entry["note"] or None})
            item["excerpt"] = _excerpt(entry["content"] or "")
            shared.append(item)
    return {"query": query, "posts": hits, "posts_total": len(posts), "campaigns": sorted(redact_value(sorted(campaigns))),
            "shared": shared[:SEARCH_LIMIT], "shared_total": len(shared)}


# ---------------------------------------------------------------------------- "Work with Claude" companion
# Owner-only action cards, the activity feed and the ideas inbox. Read-only like everything above: commands are
# text for the owner's own terminal, derived from ``next_steps`` and ``start.build_guide`` and labelled with
# ``catalog.owner_runs``; nothing here runs, approves or sends anything.

OWNER_ACTION_LIMIT = 50
ACTIVITY_LIMIT = 30
IDEAS_LIMIT = 500
LIVE_FLAG = "--live"  # catalog.COMMANDS["publish"]["owner_only_flags"]: the one flag that makes the dry run a send
LIVE_GATE = "REACHWEAVER_ENV=prod and REACHWEAVER_ALLOW_LIVE=1"  # variable names only, as post_view's dry_run note
OWNER_WHY = {
    "needs_review": "Only you can look at the native platform and record what happened; a blind resend could post twice.",
    "sending": (f"A send that does not finish is uncertain. Recording the outcome is refused for "
                f"{RECONCILE_MIN_SENDING_MINUTES} minutes after the send started, in case the request is still in flight."),
    "approval_expired": ("Your approval is older than the time limit you set (approval-mode), so it no longer counts. "
                         "Only you can approve the exact text again."),
    "approved": (f"A live send posts publicly under your account. It also needs {LIVE_GATE} in your own terminal, "
                 "and it goes out only once the post is due."),
    "draft": "Approval binds your name to this exact text; nothing can be sent until you give it.",
}
OWNER_TITLE = {"needs_review": "Check the platform, then record the outcome", "sending": "A send did not finish",
               "approval_expired": "Approve it again", "approved": "Send it live, or let your worker do it",
               "expires_before_due": "The approval runs out before the due time", "draft": "Review and approve"}


def _command(label: str, command: str, note: str | None = None) -> dict:
    return {"label": label, "command": command, "note": note, "owner_only": owner_runs(command)}


def _owner_kind(row, max_age: int | None, now: str) -> str | None:
    """Which owner action a post needs, if any (the post page; the Claude page uses the attention kinds)."""
    status = row["status"]
    if status in ("needs_review", "sending", "draft"):
        return status
    if status in ("approved", "scheduled"):
        return "approval_expired" if approval_expiry_problem(row, max_age, now) is not None else "approved"
    return None


def owner_action(post, kind: str | None) -> dict | None:
    """The terminal commands one post needs from its owner, built from ``next_steps``; None when it needs nothing.

    Each command carries ``owner_only`` (``catalog.owner_runs``): the owner runs those; the others are safe to ask
    Claude for. ``kind`` is an attention kind (``ATTENTION_KINDS``) or ``approved``."""
    if kind is None:
        return None
    pid = post["id"]

    def steps(status: str) -> list[dict]:
        return next_steps({"id": pid, "status": status})
    if kind == "needs_review":
        commands = [_command(s["label"], s["command"], s.get("note")) for s in steps("needs_review")]
    elif kind == "sending":
        commands = [_command(s["label"], s["command"]) for s in steps("sending")]
        commands += [_command(f"Still sending after {RECONCILE_MIN_SENDING_MINUTES} minutes? " + s["label"],
                              s["command"], s.get("note")) for s in steps("needs_review")]
    elif kind == "approval_expired":
        withdraw = next(s for s in steps("approved") if " unapprove " in f" {s['command']} ")
        grant = next(s for s in steps("draft") if owner_runs(s["command"]))
        commands = [_command(withdraw["label"], withdraw["command"]),
                    _command("Read the exact text again", f"reachweaver show {pid}"),
                    _command(grant["label"], grant["command"])]
    elif kind in ("approved", "overdue", "expires_before_due"):
        dry = steps("approved")[0]
        commands = [_command(dry["label"], dry["command"]),
                    _command("Send it now (you, in a terminal)", f"{dry['command']} {LIVE_FLAG}",
                             "Or leave it to the worker you run; it sends due posts only.")]
        kind = "expires_before_due" if kind == "expires_before_due" else "approved"
    elif kind == "draft":
        commands = [_command(s["label"], s["command"]) for s in steps("draft")]
    else:
        return None
    why = OWNER_WHY.get(kind) or OWNER_WHY["approved"]
    if kind == "expires_before_due":
        why = "Approve it again closer to its time, or send it earlier. " + OWNER_WHY["approved"]
    return {"post_id": pid, "kind": kind, "title": OWNER_TITLE[kind], "why": why, "status": post["status"],
            "platform": post["platform"], "account_id": post["account_id"], "commands": commands}


def owner_actions_view(con, guide_steps: list[dict], *, now: str | None = None) -> dict:
    """Everything only the owner can do now: steps of ``start.build_guide`` that the owner runs (e.g. ``resume``),
    then each post that needs the owner, most urgent first (``attention_view`` kinds, then approved posts waiting
    to be sent). Commands are text; nothing is run."""
    general = [dict(_command(s["label"], s["command"], s.get("note")), why=OWNER_ONLY_NOTE)
               for s in guide_steps if s.get("command") and owner_runs(s["command"]) and s["command"] != "reachweaver ui"]
    rows = {row["id"]: row for row in list_posts(con)}
    queue = [(rows[entry["id"]], entry["kind"]) for entry in attention_view(con, now=now)["items"] if entry["id"] in rows]
    listed = {row["id"] for row, _ in queue}
    queue += [(r, "approved") for r in sorted(rows.values(), key=lambda r: (r["scheduled_at"] or "9999", r["id"]))
              if r["status"] in ("approved", "scheduled") and r["id"] not in listed]
    found: list[tuple[dict, Any]] = []
    for row, kind in queue:
        action = owner_action(row, kind)
        if action is not None:
            found.append((action, row))
    out = []
    for action, row in found[:OWNER_ACTION_LIMIT]:
        doc = redact_value(action)
        doc["excerpt"] = _excerpt(row["content"])
        doc["looks_like_secret"] = redact(row["content"]) != row["content"]
        doc["campaign"] = redact(row["campaign"]) if row["campaign"] else None
        out.append(doc)
    return {"general": redact_value(general), "posts": out, "total": len(found),
            "truncated": len(found) > len(out),
            "note": OWNER_ONLY_NOTE + " Commands marked 'Claude can run this' are safe to ask Claude for."}


# event_log action -> (label, tone). Anything else is shown by its action name, never by its detail.
ACTIVITY_LABEL = {
    "draft_created": ("Draft created", "info"), "approve": ("Approved", "ok"), "unapprove": ("Approval withdrawn", ""),
    "schedule": ("Scheduled", "info"), "cancel": ("Cancelled", ""), "send_claimed": ("Send started", "warn"),
    "published": ("Published", "ok"), "needs_review": ("Send needs your review", "bad"),
    "reconciled_published": ("Recorded as posted", "ok"), "reconciled_not_published": ("Recorded as not posted", ""),
    "emergency_paused": ("Emergency stop turned on", "bad"), "resumed": ("Emergency stop turned off", "ok"),
    "metrics_recorded": ("Results recorded", "info"), "material_shared_recorded": ("Shared by hand recorded", "info"),
    "manual_publish_recorded": ("Posted by hand recorded (measurable)", "info"),
    "posts_status_check_widened": ("Database upgraded for posts published by hand", ""),
    "media_set": ("Images attached", ""), "video_attached": ("Video attached", ""), "video_detached": ("Video detached", ""),
    "attachment_relinked": ("Attachment relinked", ""),
    "campaign_variant_staged": ("Campaign variant staged as a draft", "info"),
    "human_confirmation_passed": ("Terminal confirmation passed", "ok"),
    "human_confirmation_failed": ("Terminal confirmation failed", "bad"),
    "approval_binding_failed": ("Approval no longer matches: approve again", "bad"),
    "approval_replay_blocked": ("A reused approval was refused", "bad"),
    "approval_confirmation_enabled": ("Terminal confirmation turned on", "ok"),
    "approval_confirmation_disabled": ("Terminal confirmation turned off", "warn"),
    "provider_cooldown": ("The platform asked to wait", "warn"),
    "state_conflict": ("Send state conflict recorded", "warn"),
    "account_disconnected": ("Account disconnected", ""), "account_reconnected": ("Account reconnected", "ok"),
    "privacy_purge": ("Old local data purged", ""), "bluesky_create_session": ("Bluesky sign-in for a send", ""),
    "bluesky_create_session_refused": ("Bluesky sign-in refused", "warn"),
}
APPROVER = re.compile(r"(?:^| )approver=(\S{1,80})")
ACCOUNT_TOKEN = re.compile(r"(?:^| )account=([A-Za-z0-9_.:@-]{1,80})")
ACTIVITY_NOTE = ("Local events only: the audit log of your database and your ideas inbox, newest first. Checks, "
                 "voice-checks and voice profiles leave no record, so they do not appear here.")


def ideas_path_for(database: str | Path) -> Path:
    """``ideas.json`` next to the database, as ``ideas.inbox_path`` resolves it for an explicit database path."""
    return Path(database).parent / ideas_inbox.FILENAME


def _load_ideas(path: Path) -> tuple[list[dict], str | None]:
    try:
        return ideas_inbox.load(path)["ideas"], None
    except (ValidationError, OSError) as exc:
        return [], redact(str(exc))


def _sort_key(at: str | None, rank: int, number: int) -> tuple[str, int, int] | None:
    try:
        return (parse_utc(at), rank, number) if at else None
    except ValidationError:
        return None


def activity_view(con, ideas_path: Path, *, limit: int = ACTIVITY_LIMIT) -> dict:
    """The last ``limit`` local events, newest first: audit-log rows (with the post's platform, account and the
    usual excerpt) and ideas added, dropped or promoted. An event's detail is never returned; only the approver
    name of an approval and the account of an account event are read from it."""
    rows = con.execute("""SELECT e.id, e.at, e.post_id, e.action, e.detail, p.account_id, p.platform, p.content
                          FROM event_log e LEFT JOIN posts p ON p.id=e.post_id ORDER BY e.id DESC LIMIT ?""",
                       (limit,)).fetchall()
    items: list[tuple[tuple, dict]] = []
    for row in rows:
        label, tone = ACTIVITY_LABEL.get(row["action"], (row["action"].replace("_", " ").capitalize(), ""))
        detail = row["detail"] or ""
        by = APPROVER.search(detail) if row["action"] == "approve" else None
        account = row["account_id"]
        if account is None:
            token = ACCOUNT_TOKEN.search(detail)
            account = token.group(1) if token else None
        item = redact_value({"source": "event", "at": row["at"], "action": row["action"], "label": label, "tone": tone,
                             "post_id": row["post_id"], "account_id": account, "platform": row["platform"],
                             "by": by.group(1) if by else None, "idea_id": None})
        if row["content"] is not None:
            item["excerpt"] = _excerpt(row["content"])
            item["looks_like_secret"] = redact(row["content"]) != row["content"]
        items.append((_sort_key(row["at"], 1, row["id"]) or ("", 1, row["id"]), item))
    ideas, error = _load_ideas(ideas_path)
    for idea in ideas:
        stamps = [("Idea added", "info", idea.get("created_at"))]
        if idea["status"] == "promoted":
            campaign = (idea.get("promoted") or {}).get("campaign_id")
            stamps.append((f"Idea promoted to campaign {campaign}", "ok", idea.get("updated_at")))
        elif idea["status"] == "dropped":
            stamps.append(("Idea dropped", "", idea.get("updated_at")))
        for label, tone, at in stamps:
            key = _sort_key(at, 0, 0)
            if key is None:
                continue
            item = redact_value({"source": "idea", "at": key[0], "action": "idea", "label": label, "tone": tone,
                                 "post_id": None, "account_id": None, "platform": None, "by": None,
                                 "idea_id": idea["id"], "excerpt": _excerpt(idea["text"])})
            item["looks_like_secret"] = False
            items.append((key, item))
    items.sort(key=lambda pair: pair[0], reverse=True)
    shown = [item for _, item in items[:limit]]
    return {"items": shown, "shown": len(shown), "ideas_error": error, "note": ACTIVITY_NOTE}


SOURCE_HOST = re.compile(r"https://([A-Za-z0-9.-]{1,253})(?:[:/?#]|$)")  # ideas.clean_source allows public https only


def _host(url: str | None) -> str | None:
    """The host name of an idea's source URL; the path and query are never shown."""
    found = SOURCE_HOST.match(url or "")
    return found.group(1).lower() if found else None


def ideas_view(ideas_path: Path, *, status: str | None = None) -> dict:
    """The ideas inbox (``idea list``), newest first: text, tags, state and the HOST of the source only. Never
    creates the file; a damaged inbox is reported as an error instead of failing the page."""
    if status is not None and status not in ideas_inbox.STATUSES:
        raise ValidationError(f"status must be one of {', '.join(ideas_inbox.STATUSES)}")
    ideas, error = _load_ideas(ideas_path)
    counts = {name: 0 for name in ideas_inbox.STATUSES}
    for idea in ideas:
        counts[idea["status"]] += 1
    chosen = sorted((i for i in ideas if status is None or i["status"] == status),
                    key=lambda i: (i.get("created_at") or "", i["id"]), reverse=True)
    items = []
    for idea in chosen[:IDEAS_LIMIT]:
        promoted = idea.get("promoted") or {}
        items.append(redact_value({"id": idea["id"], "status": idea["status"], "text": idea["text"],
                                   "excerpt": _excerpt(idea["text"]), "tags": list(idea.get("tags") or []),
                                   "source_host": _host(idea.get("source_url")), "created_at": idea.get("created_at"),
                                   "updated_at": idea.get("updated_at"),
                                   "promoted_campaign": promoted.get("campaign_id")}))
    return {"ideas": items, "counts": counts, "total": len(chosen), "truncated": len(chosen) > len(items),
            "error": error, "exists": ideas_path.is_file()}


def find_idea(ideas_path: Path, idea_id: str) -> dict:
    """One idea as ``ideas_view`` shows it; ValidationError("Unknown idea") when it is not in the inbox."""
    for idea in ideas_view(ideas_path)["ideas"]:
        if idea["id"] == idea_id:
            return idea
    raise ValidationError("Unknown idea")


def prompt_context(con, accounts: dict, ideas_path: Path, *, page: str | None = None, post_id: str | None = None,
                   account_id: str | None = None, campaign: str | None = None, idea_id: str | None = None,
                   region: str | None = None, goal: str | None = None) -> dict:
    """The context for ``ui_prompts.build_prompt``: real ids from the local database, accounts file and ideas
    inbox, never guessed. The post's exact text is included only to be quoted as untrusted data."""
    context: dict = {"page": page, "accounts": [{"id": name, "platform": a["platform"]}
                                                for name, a in sorted(accounts.items())]}
    if post_id:
        row = get_post(con, post_id)  # ValidationError("Unknown post id")
        context["post"] = {"id": row["id"], "status": row["status"], "platform": row["platform"],
                           "account_id": row["account_id"], "campaign": row["campaign"] or None,
                           "content": row["content"]}
    if account_id:
        account = accounts.get(account_id)
        if account is None:
            raise ValidationError("Unknown account")
        context["account"] = {"id": account_id, "platform": account["platform"], "handle": account.get("handle")}
    if campaign:
        if not list_posts(con, campaign=campaign):
            raise ValidationError("Unknown campaign")
        context["campaign"] = campaign
    if idea_id:
        context["idea"] = find_idea(ideas_path, idea_id)
    if region:
        context["region"] = region
    if goal:
        context["goal"] = goal
    return context
