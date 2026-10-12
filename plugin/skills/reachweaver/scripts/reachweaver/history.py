"""Repetition checks of campaign variants against the local posting history (issue #10, slice S6).

``campaign-check`` and ``campaign-preview`` compare variants only with each other (claims.py
check 4). With ``--history-db PATH`` they also compare each variant with the posts already in
that local ReachWeaver database for the SAME ``account_id``, and count how many posts each
account already has in a rolling window. Both results are **warnings** only: they never block
staging, never touch approval state and never write the database, which is opened read-only
through :func:`storage.connect_readonly`.

Similarity is the existing ``claims.jaccard`` (word 3-gram Jaccard), not a second algorithm.
Findings name the matched post id, status and date; they never carry the prior post's text.
The frequency check is a ReachWeaver brand-hygiene heuristic. It is not a platform rule,
quota or ranking fact, and no platform limit is implied by its defaults.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .claims import DEFAULT_DUPLICATE_THRESHOLD, _finding, jaccard, variant_body
from .storage import connect_readonly, parse_utc, posting_history, utc_now

# Statuses that represent a post the account has made, will make, or may have made.
# 'cancelled' rows are ignored (there is no 'rejected' status in the schema).
HISTORY_STATUSES = ("draft", "approved", "scheduled", "sending", "published", "needs_review")
DEFAULT_HISTORY_DAYS = 30
HISTORY_DAYS_RANGE = (1, 365)
HISTORY_DUPLICATE_THRESHOLD = DEFAULT_DUPLICATE_THRESHOLD
"""Word 3-gram Jaccard at or above which a variant repeats a prior post of the same account.

Deliberately the same value (0.6) as the in-campaign ``near_duplicate`` check, so "too
similar" means one thing across a campaign and across its history. 1.0 means the same word
sequence (case, Unicode form and URLs ignored); 0.6 still flags a respin that swapped a few
words. It is an editorial threshold chosen by ReachWeaver, not a platform spam rule.
"""
DEFAULT_FREQUENCY_WINDOW_DAYS = 7
DEFAULT_FREQUENCY_MAX_POSTS = 7
"""Warn when an account already has at least this many local posts in the rolling window.

ReachWeaver brand-hygiene heuristic (about one post a day per account over a week), chosen so
an owner notices a crowded account before adding more. No platform publishes this number as
a limit, quota or ranking signal; tune it with ``--history-max-posts``/``--history-window-days``.
"""
FREQUENCY_WINDOW_RANGE = (1, 365)
FREQUENCY_MAX_RANGE = (1, 1000)
HISTORY_MAX_ROWS = 5000  # newest rows read per run; older ones are reported as truncated
MAX_ALSO_MATCHED = 5  # further matching post ids listed on one finding (ids only)
HEURISTIC_NOTE = ("history_frequency is a ReachWeaver brand-hygiene heuristic, not a platform rule or limit. "
                  "Both history checks are warnings: they never block staging or change approval.")


def _bounded(name: str, value: object, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
        raise ValueError(f"{name} must be an integer in {bounds[0]}..{bounds[1]}")
    return value


def load_history(path, campaign: dict, *, now: str | None = None, days: int = DEFAULT_HISTORY_DAYS,
                 window_days: int = DEFAULT_FREQUENCY_WINDOW_DAYS,
                 max_posts: int = DEFAULT_FREQUENCY_MAX_POSTS) -> dict:
    """Read the posts of the campaign's accounts from an existing database, read-only.

    Posts staged from this same campaign (``posts.campaign`` equal to its ``campaign_id``) are
    excluded and counted: they are this campaign, already covered by the in-campaign check.
    The returned dict holds post text for comparison; pass it to :func:`history_findings` and
    never print it. :func:`history_summary` is the emit-safe view.
    """
    days = _bounded("history_days", days, HISTORY_DAYS_RANGE)
    window_days = _bounded("history_window_days", window_days, FREQUENCY_WINDOW_RANGE)
    max_posts = _bounded("history_max_posts", max_posts, FREQUENCY_MAX_RANGE)
    campaign = campaign if isinstance(campaign, dict) else {}
    now_utc = parse_utc(now) if now else utc_now()
    since = (datetime.fromisoformat(now_utc) - timedelta(days=max(days, window_days))).isoformat(timespec="seconds")
    variants = campaign.get("variants")
    raw: list = variants if isinstance(variants, list) else []
    account_ids = sorted({v["account_id"] for v in raw[:30] if isinstance(v, dict)
                          and isinstance(v.get("account_id"), str) and v["account_id"]})
    with connect_readonly(path) as con:
        rows = posting_history(con, account_ids, HISTORY_STATUSES, since=since, limit=HISTORY_MAX_ROWS + 1)
    truncated = len(rows) > HISTORY_MAX_ROWS
    rows = rows[:HISTORY_MAX_ROWS]
    campaign_id = campaign.get("campaign_id")
    same = isinstance(campaign_id, str) and bool(campaign_id)
    posts = [r for r in rows if not (same and r["campaign"] == campaign_id)]
    return {"now": now_utc, "days": days, "window_days": window_days, "max_posts": max_posts,
            "accounts": account_ids, "excluded_same_campaign": len(rows) - len(posts), "truncated": truncated,
            "posts": posts}


def history_summary(history: dict) -> dict:
    """What a report may show about the history read: counts and settings, never post text."""
    return {"source": "local_database_read_only", "now": history["now"], "days": history["days"],
            "duplicate_threshold": HISTORY_DUPLICATE_THRESHOLD, "window_days": history["window_days"],
            "max_posts": history["max_posts"], "accounts": list(history["accounts"]),
            "posts_considered": len(history["posts"]),
            "excluded_same_campaign": history["excluded_same_campaign"],
            "truncated": history["truncated"], "statuses": list(HISTORY_STATUSES), "note": HEURISTIC_NOTE}


def history_findings(variants: list[tuple[int, dict]], history: dict) -> dict[int, list[dict]]:
    """``history_near_duplicate`` and ``history_frequency`` warnings keyed by variant index."""
    now = datetime.fromisoformat(history["now"])
    dup_since = (now - timedelta(days=history["days"])).isoformat(timespec="seconds")
    freq_since = (now - timedelta(days=history["window_days"])).isoformat(timespec="seconds")
    out: dict[int, list[dict]] = {}
    by_account: dict[str, list[dict]] = {}
    for row in history["posts"]:
        by_account.setdefault(row["account_id"], []).append(row)
    first_variant: dict[str, int] = {}
    per_account: dict[str, int] = {}
    for index, variant in variants:
        account_id = variant.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            continue
        first_variant.setdefault(account_id, index)
        per_account[account_id] = per_account.get(account_id, 0) + 1
        body = variant_body(variant)
        if not body.strip():
            continue
        matches = []
        for row in by_account.get(account_id, []):
            if row["activity_at"] < dup_since:
                continue
            score = jaccard(body, row["content"])
            if score >= HISTORY_DUPLICATE_THRESHOLD:
                matches.append((score, row))
        if not matches:
            continue
        matches.sort(key=lambda m: -m[0])  # stable: rows are newest first, so ties keep the newest
        score, best = matches[0]
        what = "identical wording" if score == 1.0 else f"word 3-gram Jaccard {score:.2f}"
        out.setdefault(index, []).append(_finding(
            "history_near_duplicate", "warning",
            f"{what} with {best['status']} post {best['id']} on account {account_id!r} dated "
            f"{best['activity_at'][:10]} (threshold {HISTORY_DUPLICATE_THRESHOLD:.2f}, last {history['days']} days "
            "of local history); rewrite the angle instead of repeating it",
            matched_post_id=best["id"], matched_status=best["status"], matched_at=best["activity_at"],
            matched_platform=best["platform"], similarity=round(score, 3), account_id=account_id,
            also_matched_post_ids=[m[1]["id"] for m in matches[1:1 + MAX_ALSO_MATCHED]],
            history_days=history["days"]))
    for account_id, index in first_variant.items():
        existing = sum(1 for row in by_account.get(account_id, []) if row["activity_at"] >= freq_since)
        if existing < history["max_posts"]:
            continue
        adds = per_account[account_id]
        out.setdefault(index, []).append(_finding(
            "history_frequency", "warning",
            f"account {account_id!r} already has {existing} local post(s) dated within the last "
            f"{history['window_days']} days or scheduled later; this campaign adds {adds} more. "
            f"ReachWeaver brand-hygiene heuristic (warn at {history['max_posts']} per {history['window_days']} days), "
            "not a platform rule or limit: space the posts out or drop a variant",
            account_id=account_id, existing_posts=existing, campaign_variants=adds,
            window_days=history["window_days"], max_posts=history["max_posts"], heuristic=True))
    return out
