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
from pathlib import Path

from .accounts import missing_credentials
from .caps import load_caps
from .ops_summary import build_summary
from .policy import SUPPORTED_LIVE, ValidationError
from .post_view import build_post_view
from .privacy import redact, redact_value
from .show_view import approval_sentence, length_of
from .storage import connect_readonly, get_post, list_posts, schema_gaps

REQUIRED_TABLES = ("posts", "event_log", "settings")
LIST_LIMIT = 500
EVENT_LIMIT = 200
EXCERPT = 160
# Rows a human has to look at first, in this order.
ATTENTION_ORDER = {"needs_review": 0, "sending": 1, "approved": 2, "scheduled": 3, "draft": 4,
                   "published": 5, "cancelled": 6}


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
