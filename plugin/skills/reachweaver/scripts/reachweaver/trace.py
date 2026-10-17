"""Read-only trace of one post across the database audit log and worker logs (issue #26, traces).

``build_trace`` joins two local records of what happened to a post:

* the ``event_log`` rows of the post (draft, approval, claim, send outcome, reconciliation), read
  through a read-only connection; and
* the lines of ``publish --log-json`` worker logs (``worker_log``) that name the post, plus the
  ``run_start``/``run_end`` lines of the same runs, so each send attempt shows the run it belongs to,
  whether it was a dry run and how the run ended.

A worker log is treated as untrusted input: only JSON objects with the worker-log schema version and a
known event name are used, only allow-listed fields are kept, every value passes ``privacy.redact`` and
oversized files or lines are refused or skipped with a fixed code. Nothing here contacts a provider,
retries, reconciles or writes anything.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import IO, Iterable

from . import term
from .policy import ValidationError
from .privacy import redact, redact_value
from .storage import get_post, parse_utc
from .worker_log import EVENTS, SCHEMA_VERSION, safe_post_id

MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_EVENTS = 1000
RUN_EVENTS = ("run_start", "run_end")
# Worker-log fields shown in a trace. Everything else on a line is ignored.
KEEP = ("run_id", "seq", "dry_run", "event", "ts", "post_id", "account_id", "platform", "reason", "backend",
        "send_class", "code", "rate_limited", "remote_url", "live_requested", "outcome", "exit_code", "counts")
SOURCE_ORDER = {"database": 0, "worker_log": 1}
_RUN_ID = re.compile(r"^[0-9A-Za-z.:_-]{1,64}$")


def _db_events(con, post_id: str) -> list[dict]:
    rows = con.execute("SELECT at, action, detail FROM event_log WHERE post_id=? ORDER BY id LIMIT ?",
                       (post_id, MAX_EVENTS)).fetchall()
    return [{"at": row["at"], "source": "database", "event": row["action"], "detail": redact(row["detail"] or "")}
            for row in rows]


def _open(path: Path | str, stdin: IO[str] | None) -> tuple[IO[str], str]:
    if str(path) == "-":
        return (stdin if stdin is not None else io.StringIO("")), "stdin"
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValidationError(f"Worker log {target} is not a regular file")
    if target.stat().st_size > MAX_LOG_BYTES:
        raise ValidationError(f"Worker log {target} is larger than {MAX_LOG_BYTES // (1024 * 1024)} MiB; "
                              "pass a shorter extract (for example one day of journalctl output)")
    return target.open("r", encoding="utf-8", errors="replace"), target.name


def _clean(record: dict) -> dict | None:
    """The allow-listed fields of one worker-log record, or None when it is not one."""
    if record.get("schema_version") != SCHEMA_VERSION or record.get("event") not in EVENTS:
        return None
    run_id, seq = record.get("run_id"), record.get("seq")
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or type(seq) is not int or seq < 0:
        return None  # every real worker-log line has both; anything else is not one
    try:
        stamp = parse_utc(str(record.get("ts")))
    except ValidationError:
        return None
    kept = {k: record[k] for k in KEEP if k in record and k != "ts"}
    kept["at"] = stamp
    if kept.get("post_id") is not None:
        kept["post_id"] = safe_post_id(kept["post_id"])
    return redact_value(kept)


def read_worker_logs(paths: Iterable[Path | str], post_id: str, *,
                     stdin: IO[str] | None = None) -> tuple[list[dict], dict]:
    """Worker-log lines about ``post_id`` plus the start/end lines of their runs, and read statistics."""
    files: list[str] = []
    read = not_worker_log = too_long = 0
    about: list[dict] = []
    bounds: list[dict] = []
    for path in paths:
        stream, label = _open(path, stdin)
        files.append(label)
        try:
            for raw in stream:
                read += 1
                if len(raw.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
                    too_long += 1
                    continue
                start = raw.find("{")  # journalctl without -o cat prefixes a timestamp and unit name
                try:
                    record = json.loads(raw[start:]) if start >= 0 else None
                except ValueError:
                    record = None
                line = _clean(record) if isinstance(record, dict) else None
                if line is None:
                    not_worker_log += 1
                    continue
                if line["event"] in RUN_EVENTS:
                    bounds.append(line)
                elif line.get("post_id") == post_id:
                    about.append(line)
        finally:
            if stream is not stdin:
                stream.close()
    runs = {line.get("run_id") for line in about}
    kept = about + [line for line in bounds if line.get("run_id") in runs]
    for line in kept:
        line["source"] = "worker_log"
    return kept, {"files": files, "lines": read, "used": len(kept), "skipped_not_worker_log": not_worker_log,
                  "skipped_too_long": too_long}


def _runs(lines: list[dict]) -> list[dict]:
    runs: dict[str, dict] = {}
    for line in lines:
        run = runs.setdefault(str(line.get("run_id")), {"run_id": line.get("run_id"), "started_at": None,
                                                         "ended_at": None, "dry_run": line.get("dry_run"),
                                                         "live_requested": None, "this_post": [],
                                                         "outcome": None, "exit_code": None})
        if line["event"] == "run_start":
            run["started_at"], run["live_requested"] = line["at"], line.get("live_requested")
        elif line["event"] == "run_end":
            run["ended_at"], run["outcome"], run["exit_code"] = line["at"], line.get("outcome"), line.get("exit_code")
        else:
            run["this_post"].append(line["event"] + (f" ({line['reason']})" if line.get("reason") else ""))
    return sorted(runs.values(), key=lambda r: (r["started_at"] or r["ended_at"] or "", str(r["run_id"])))


def summary(post, db: list[dict]) -> str:
    claims = sum(1 for e in db if e["event"] == "send_claimed")
    status = post["status"]
    if status == "published":
        return f"Published after {claims} send attempt(s); the remote link is what the platform returned."
    if status == "needs_review":
        return (f"{claims} send attempt(s); the last one ended in an unknown state. Check the native platform, "
                "then record the outcome with reconcile. Never just resend.")
    if status == "sending":
        return f"{claims} send attempt(s); one is in progress or was interrupted. Treat it as uncertain."
    if claims:
        return f"{claims} send attempt(s) so far; the post is {status} now."
    return f"No send attempt yet; the post is {status}."


def build_trace(con, post_id: str, log_paths: Iterable[Path | str] = (), *, stdin: IO[str] | None = None) -> dict:
    """The timeline of one post, oldest first. Raises ValidationError for an unknown post id."""
    post = get_post(con, post_id)
    db = _db_events(con, post_id)
    lines, stats = read_worker_logs(log_paths, post_id, stdin=stdin) if log_paths else ([], None)
    timeline = sorted(db + lines, key=lambda e: (e["at"] or "", SOURCE_ORDER[e["source"]], e.get("seq") or 0))
    return {
        "report": "reachweaver_post_trace",
        "post": redact_value({"id": post["id"], "status": post["status"], "account_id": post["account_id"],
                              "platform": post["platform"], "approved_at": post["approved_at"],
                              "remote_url": post["remote_url"]}),
        "summary": summary(post, db),
        "send_attempts": sum(1 for e in db if e["event"] == "send_claimed"),
        "runs": _runs(lines),
        "timeline": timeline[:MAX_EVENTS],
        "truncated": len(timeline) > MAX_EVENTS,
        "worker_logs": stats,
        "note": ("Local records only: the database audit log and the worker logs you passed. Nothing was sent, "
                 "retried or contacted. A worker log is matched by post id; lines that are not worker-log JSON "
                 "are skipped."),
    }


def render_text(trace: dict) -> str:
    post = trace["post"]
    lines = [f"Post {post['id']} ({post['account_id']} on {post['platform']}): {post['status']}",
             trace["summary"]]
    if post.get("remote_url"):
        lines.append(f"Remote: {post['remote_url']}")
    if trace["runs"]:
        lines.append("")
        lines.append("Worker runs that handled this post:")
        for run in trace["runs"]:
            mode = "dry run" if run["dry_run"] else "live" if run["live_requested"] else "run"
            ended = f", ended {run['outcome']} (exit {run['exit_code']})" if run["outcome"] else ", no end line"
            lines.append(f"  {run['run_id']}  {run['started_at'] or 'start not in log'}  {mode}{ended}: "
                         + (", ".join(run["this_post"]) or "only start/end lines"))
    lines.append("")
    lines.append("Timeline (UTC):")
    for item in trace["timeline"]:
        where = "db " if item["source"] == "database" else "log"
        extra = item.get("detail") or ", ".join(f"{k}={item[k]}" for k in ("reason", "send_class", "code", "remote_url")
                                                if item.get(k) not in (None, ""))
        run = f" [{item['run_id']}#{item['seq']}]" if item["source"] == "worker_log" else ""
        lines.append(f"  {item['at']}  {where}  {item['event']}{run}" + (f"  {extra}" if extra else ""))
    if trace["truncated"]:
        lines.append(f"  ... first {MAX_EVENTS} entries only")
    stats = trace["worker_logs"]
    if stats is None:
        lines.append("")
        lines.append("No worker log given. Add --log FILE (from publish --log-file) or pipe journalctl output with --log -.")
    elif stats["skipped_not_worker_log"] or stats["skipped_too_long"]:
        lines.append("")
        lines.append(f"Worker logs: {stats['lines']} line(s) read, {stats['used']} used, "
                     f"{stats['skipped_not_worker_log']} not worker-log JSON, {stats['skipped_too_long']} too long.")
    lines.append(trace["note"])
    return "\n".join(term.clean(line) for line in lines)  # a planted log line cannot steer the terminal
