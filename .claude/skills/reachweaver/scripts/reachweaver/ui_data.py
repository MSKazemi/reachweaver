"""Read-only data layer for the local dashboard (``reachweaver ui``).

Every function takes an already-open **read-only** connection (``storage.connect_readonly``:
``mode=ro`` plus ``query_only``) and returns plain JSON-able dicts. Nothing here approves,
schedules, pauses, publishes, writes, reads a credential value or touches the network; the only
credential information is the *names* of environment variables that are missing. Every document
is passed through ``privacy.redact_value`` as a last layer. Post text is untrusted: consumers
must escape it (the bundled page does).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from . import ledger, workspace
from .accounts import missing_credentials
from .caps import load_caps
from .journal import NOTE as JOURNAL_NOTE
from .journal import build_entries
from .storage import shared_entries
from .ops_summary import build_summary
from .policy import SUPPORTED_LIVE, ValidationError
from .post_view import build_post_view
from .privacy import redact, redact_value
from .show_view import approval_sentence, length_of
from .suggest import build_suggestions
from .storage import (METRIC_FIELDS, approval_expiry_problem, approval_max_age_hours, connect_readonly, get_post,
                      latest_snapshot, list_posts, metric_states, parse_utc, report, schema_gaps, snapshots_for,
                      utc_now)

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
                   "published": 5, "cancelled": 6}
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
    """The redacted ops-summary document (readiness, per-account state, queue, dead letters)."""
    try:
        (caps, caps_path), caps_error = load_caps(), None
    except ValidationError as exc:
        (caps, caps_path), caps_error = (None, None), str(exc)
    return build_summary(con, accounts, accounts_error=accounts_error, caps=caps,
                         caps_path=str(caps_path) if caps_path else None, caps_error=caps_error,
                         optout=optout, optout_configured=optout_configured)


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
    return steps


def _events(con, post_id: str) -> list[dict]:
    rows = con.execute("SELECT at, action, detail FROM event_log WHERE post_id=? ORDER BY id DESC LIMIT ?",
                       (post_id, EVENT_LIMIT)).fetchall()
    return [{"at": r["at"], "action": r["action"], "detail": r["detail"]} for r in rows]


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
    detail["results"] = post_results(con, post_id, published)  # recorded snapshots only; [] when none
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


def results_view(con, *, account: str | None = None, platform: str | None = None) -> dict:
    """Recorded results: per-platform totals of exact values (``storage.report``) and each published post's latest
    snapshot, newest post first. Nothing is fetched; a post without a snapshot is listed as not recorded."""
    entries = build_entries(con, statuses=("published",), account=account, platform=platform, by_hand=False,
                            limit=RESULTS_LIMIT + 1)
    shown = entries[:RESULTS_LIMIT]
    posts = []
    for entry in shown:
        item = redact_value({k: entry.get(k) for k in ("id", "when", "platform", "account", "campaign", "remote_url")})
        text = entry.get("text") or ""
        item["excerpt"] = _excerpt(text)
        item["looks_like_secret"] = redact(text) != text
        item["latest"] = _snapshot_view(latest_snapshot(con, entry["id"]), entry.get("when"))
        posts.append(item)
    platforms = []
    for name, summary in report(con).items():
        if platform is not None and name != platform:
            continue
        platforms.append({"platform": name, "published": summary["published"],
                          "with_snapshot": summary["metric_coverage"],
                          "metrics": {k: {"value": summary["metrics"][k]["value"],
                                          "coverage": summary["metrics"][k]["coverage"],
                                          "estimated": summary["metrics"][k]["estimated"]} for k in METRIC_FIELDS}})
    recorded = sum(1 for p in posts if p["latest"] is not None)
    return {"metrics": list(METRIC_FIELDS), "platforms": platforms, "posts": posts, "shown": len(posts),
            "with_snapshot": recorded, "truncated": len(entries) > RESULTS_LIMIT,
            "platform_totals_scope": "all published posts of each platform (not filtered by account)",
            "note": RESULTS_NOTE}


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
    posts, campaigns = [], set()
    for row in sorted(list_posts(con), key=lambda r: r["created_at"], reverse=True):
        fields = (row["content"], row["id"], row["account_id"], row["platform"], row["campaign"], row["remote_url"])
        if any(_contains(f, needle) for f in fields):
            posts.append(row)
        if _contains(row["campaign"], needle):
            campaigns.add(row["campaign"])
    hits = []
    for row in posts[:SEARCH_LIMIT]:
        item = redact_value({"id": row["id"], "status": row["status"], "platform": row["platform"],
                             "account_id": row["account_id"], "campaign": row["campaign"] or None,
                             "created_at": row["created_at"]})
        item["excerpt"] = _excerpt(row["content"])
        item["looks_like_secret"] = redact(row["content"]) != row["content"]
        hits.append(item)
    shared = []
    for entry in shared_entries(con):
        if any(_contains(entry.get(k), needle) for k in ("content", "note", "url", "platform", "account_id")):
            item = redact_value({"id": entry["id"], "platform": entry["platform"], "account_id": entry["account_id"],
                                 "when": entry["posted_at"] or entry["recorded_at"], "url": entry["url"] or None,
                                 "note": entry["note"] or None})
            item["excerpt"] = _excerpt(entry["content"] or "")
            shared.append(item)
    return {"query": query, "posts": hits, "posts_total": len(posts), "campaigns": sorted(redact_value(sorted(campaigns))),
            "shared": shared[:SEARCH_LIMIT], "shared_total": len(shared)}
