"""Offline YouTube analytics-sampling report (issue #22 follow-up). Read-only: no network, no credential, no write.

Reads the metric snapshots already stored for *published YouTube posts* and says, per video and
per metric, how much of each number is known. It never fetches a view count and never invents
one. A video's identity and receipt fields come from the same place the upload receipt does:
``posts.remote_id`` (the video id, the receipt), ``posts.remote_url`` and the ``published``
audit event (``channel_id``, ``privacy_status``, ``upload_status``, written by
``storage.finish_send`` from ``youtube._receipt``). A post settled by ``reconcile --published``
has no such event fields, and they are reported as ``null`` rather than guessed.

Labels (``storage.metric_states``; the integer column stays authoritative):

* ``exact``         a stored integer count.
* ``sampled``       a platform estimate (stored status ``estimated``). Its value is shown apart,
                    never summed with exact counts.
* ``suppressed`` / ``delayed`` / ``not_available``  the stored provider status, no value.
* ``unknown``       no snapshot, or the snapshot holds nothing for that metric. Never zero.

An exact count whose snapshot has no observation window may be lifetime-cumulative or cover a
provider-defined period; it is labelled ``window: unknown`` and counted separately.
"""
from __future__ import annotations

import re
import sqlite3

from .policy import ValidationError
from .storage import METRIC_FIELDS, get_post, latest_snapshot, list_posts, metric_states, snapshots_for

LABELS = ("exact", "sampled", "suppressed", "delayed", "not_available", "unknown")
SAMPLED_NOTE = "platform estimates: shown apart, never summed with exact counts"
NOTE = ("Read from stored snapshots only; nothing is fetched. Unknown is not zero. Totals sum exact "
        "values from each video's latest snapshot, with coverage; snapshots with different or "
        "unknown windows are not comparable.")
_RECEIPT_KEYS = ("channel_id", "privacy_status", "upload_status")
_KEY_VALUE = re.compile(r"([a-z_]{1,32})=([A-Za-z0-9._:~-]{1,256})")


def receipt_fields(con: sqlite3.Connection, post_id: str) -> dict:
    """Receipt fields from the latest ``published`` audit event; each is None when absent."""
    row = con.execute("SELECT detail FROM event_log WHERE post_id=? AND action='published' "
                      "ORDER BY id DESC LIMIT 1", (post_id,)).fetchone()
    found = dict(_KEY_VALUE.findall(row["detail"] or "")) if row else {}
    return {key: found.get(key) for key in _RECEIPT_KEYS}


def _label(state: str) -> str:
    return "sampled" if state == "estimated" else state


def _window(snapshot) -> dict:
    keys = snapshot.keys()
    start = snapshot["window_start"] if "window_start" in keys else None
    end = snapshot["window_end"] if "window_end" in keys else None
    known = bool(start and end)
    return {"known": known, "start": start if known else None, "end": end if known else None}


def video_entry(con: sqlite3.Connection, post) -> dict:
    snapshot = latest_snapshot(con, post["id"])
    metrics: dict[str, dict] = {}
    for key, (state, value) in metric_states(snapshot).items():
        entry: dict = {"label": _label(state)}
        if state in ("exact", "estimated"):
            entry["value"] = value
        metrics[key] = entry
    video = {"post_id": post["id"], "account_id": post["account_id"],
             "video_id": post["remote_id"], "watch_url": post["remote_url"],
             **receipt_fields(con, post["id"]),
             "snapshots": len(snapshots_for(con, post["id"])),
             "latest": None, "metrics": metrics}
    if snapshot is not None:
        video["latest"] = {"observed_at": snapshot["observed_at"], "source": snapshot["source"],
                           "window": _window(snapshot)}
    return video


def sampling_report(con: sqlite3.Connection, *, account_id: str | None = None,
                    post_id: str | None = None) -> dict:
    """Per-video sampling labels plus coverage-aware totals for published YouTube posts."""
    if post_id is not None:
        post = get_post(con, post_id)
        if post["platform"] != "youtube":
            raise ValidationError("youtube-analytics only applies to YouTube posts")
        if account_id is not None and post["account_id"] != account_id:
            raise ValidationError("youtube-analytics: post does not belong to that account")
        posts = [post] if post["status"] == "published" else []
    else:
        posts = list_posts(con, status="published", account_id=account_id, platform="youtube")
    videos = [video_entry(con, post) for post in posts]
    totals: dict[str, dict] = {}
    for key in METRIC_FIELDS:
        labels = {label: 0 for label in LABELS}
        exact_sum = sampled_sum = sampled_n = exact_no_window = 0
        for video in videos:
            metric = video["metrics"][key]
            labels[metric["label"]] += 1
            if metric["label"] == "exact":
                exact_sum += metric["value"]
                if not video["latest"]["window"]["known"]:
                    exact_no_window += 1
            elif metric["label"] == "sampled":
                sampled_sum += metric["value"]
                sampled_n += 1
        exact_n = labels["exact"]
        totals[key] = {"value": exact_sum if exact_n else None, "exact": exact_n, "total": len(videos),
                       "coverage": f"{exact_n}/{len(videos)}", "exact_without_window": exact_no_window,
                       "labels": labels,
                       "sampled": ({"videos": sampled_n, "sum": sampled_sum, "note": SAMPLED_NOTE}
                                   if sampled_n else None)}
    return {"platform": "youtube", "videos": videos, "totals": totals, "note": NOTE}
