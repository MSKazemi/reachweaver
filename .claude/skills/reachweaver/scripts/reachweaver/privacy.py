"""Offline privacy lifecycle: redaction, structured logs, local export and retention.

Nothing here contacts a network or a social platform. Purging is planned first
(dry run) and applied only on explicit request, under hard rules that preserve
everything needed to prevent duplicate sends and to reconcile uncertain sends.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .policy import ValidationError
from .private_sources import (REDACTED_PRIVATE, is_private_evidence, redact_private_evidence, repo_of_url,
                              url_in_private_repo)
from .storage import event, has_reply_column, parse_utc, utc_now

EXPORT_SCHEMA_VERSION = 1
PURGED = "[purged]"
# Minimized reply target (issue #17): still marks the row as a reply for reply_report, names nobody.
PURGED_REPLY_TARGET = '{"purged":true}'
REDACTED = "[REDACTED]"
SEND_CLAIM_PROTECT_HOURS = 48
# Issue #11: a provider cooldown lasts at most 24 h; its event is kept like a send claim.
COOLDOWN_EVENT = "provider_cooldown"
# #11: createSession counter events feed a 24 h budget; kept like a send claim.
SESSION_EVENT = "bluesky_create_session"
ACTIVE_STATUSES = ("draft", "approved", "scheduled", "sending", "needs_review")
POLICY_KEYS = ("metric_snapshots_days", "event_log_days", "cancelled_drafts_days",
               "published_content_days", "campaign_evidence_days")
POLICY_ENV = "REACHWEAVER_RETENTION_POLICY"
DEFAULT_POLICY_PATH = Path("config/retention.json")
SECRET_ENV_NAME = re.compile(
    r"^REACHWEAVER_[A-Z0-9_]*(?:_TOKEN|_PASSWORD|_APP_PASSWORD|_BOT_TOKEN|_SECRET|_API_KEY)$")
ACTION_KEYS = ("metric_snapshots_delete", "event_log_delete", "cancelled_posts_delete",
               "cancelled_posts_minimize", "published_content_redact", "campaign_evidence_minimize")
TABLES = ("posts", "event_log", "campaign_variants", "metric_snapshots", "settings")

_SECRET_KEY = (r"[A-Za-z0-9_.-]*(?:token|password|passwd|secret|api[_-]?key|access[_-]?key|"
               r"private[_-]?key|session|cookie|credential)[A-Za-z0-9_.-]*")
_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Credentials embedded in a URL authority: scheme://user:secret@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@"), r"\1:" + REDACTED + "@"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"), "[REDACTED:jwt]"),
    # Telegram bot token, including inside https://api.telegram.org/bot<token>/method
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])"), "[REDACTED:telegram_bot_token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github_token]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED:github_token]"),
    # Bluesky app passwords are four groups of four lowercase letters/digits.
    (re.compile(r"(?<![A-Za-z0-9-])[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}(?![A-Za-z0-9-])"),
     "[REDACTED:app_password]"),
    (re.compile(r"(?i)\b(" + _SECRET_KEY + r")(\s*[=:]\s*)(?!\[REDACTED)(\"[^\"]*\"|'[^']*'|[^\s&,;\"']+)"),
     r"\1\2" + REDACTED),
)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def _env_secrets(env=None) -> list[tuple[str, str]]:
    env = os.environ if env is None else env
    pairs = [(name, value) for name, value in env.items()
             if SECRET_ENV_NAME.fullmatch(name) and isinstance(value, str) and len(value) >= 4]
    # Values read from $CREDENTIALS_DIRECTORY (systemd LoadCredential=) are stripped the same way.
    from .accounts import loaded_file_credentials
    pairs += [(name, value) for name, value in loaded_file_credentials() if SECRET_ENV_NAME.fullmatch(name)]
    return sorted(pairs, key=lambda item: len(item[1]), reverse=True)


def redact(text, *, emails: bool = False, env=None) -> str:
    """Replace secret-looking substrings; values of secret-named env vars are removed verbatim."""
    if not isinstance(text, str):
        text = str(text)
    for name, value in _env_secrets(env):
        text = text.replace(value, f"[REDACTED:env:{name}]")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if emails:
        text = _EMAIL.sub("[REDACTED:email]", text)
    return text


def redact_value(value, *, emails: bool = False, env=None):
    """Recursively redact strings inside JSON-like data; other scalars pass through."""
    if isinstance(value, str):
        return redact(value, emails=emails, env=env)
    if isinstance(value, dict):
        return {str(k): redact_value(v, emails=emails, env=env) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v, emails=emails, env=env) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value), emails=emails, env=env)


def structured_log(event_name: str, *, _stream=None, _now: str | None = None,
                   _emails: bool = True, **fields) -> str:
    """Emit one redacted JSON line (UTC timestamp) to stderr or a given stream; return it."""
    record = {**fields, "event": event_name, "ts": parse_utc(_now) if _now else utc_now()}
    line = json.dumps(redact_value(record, emails=_emails), sort_keys=True, ensure_ascii=False)
    stream = sys.stderr if _stream is None else _stream
    stream.write(line + "\n")
    return line


def _rows(con, query: str) -> list[dict]:
    return [dict(row) for row in con.execute(query)]


def export_data(con, *, now: str | None = None, emails: bool = False) -> dict:
    """JSON-serializable copy of the owner's local DB with secret-looking text redacted."""
    tables = {
        "posts": _rows(con, "SELECT * FROM posts ORDER BY created_at,id"),
        "events": _rows(con, "SELECT * FROM event_log ORDER BY id"),
        "campaign_variants": _rows(con, "SELECT * FROM campaign_variants ORDER BY recorded_at,campaign_id,variant_id"),
        "metric_snapshots": _rows(con, "SELECT * FROM metric_snapshots ORDER BY id"),
        "settings": _rows(con, "SELECT * FROM settings ORDER BY name"),
    }
    private_rows = _redact_private_sources(tables)
    return {
        "format": "reachweaver-local-export",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": parse_utc(now) if now else utc_now(),
        "redaction": "secret-looking substrings and secret env values replaced" + ("; emails replaced" if emails else "")
                     + "; private-source evidence excerpts and URLs replaced",
        "private_source_rows_redacted": private_rows,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": redact_value(tables, emails=emails),
        "note": "LOCAL DATA ONLY. Contains private drafts and analytics; store and share it privately.",
    }


def _redact_private_sources(tables: dict) -> int:
    """Issue #9: private-source evidence text/URLs never reach an export. Returns rows changed."""
    repos: set[str] = set()
    changed = 0
    for row in tables["campaign_variants"]:
        try:
            items = json.loads(row.get("evidence_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        private = [i for i in items if is_private_evidence(i)] if isinstance(items, list) else []
        if private:
            repos.update(r for r in (repo_of_url(i.get("url")) for i in private) if r)
            row["evidence_json"] = json.dumps(redact_private_evidence(items), ensure_ascii=False)
            changed += 1
    for row in tables["posts"]:
        if repos and url_in_private_repo(row.get("source_url"), repos):
            row["source_url"] = REDACTED_PRIVATE
            changed += 1
    return changed


def write_private_file(path: Path, text: str) -> None:
    """Create a NEW owner-only (0600) file; refuse to overwrite or follow symlinks."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
    finally:
        if fd != -1:
            os.close(fd)


def validate_policy(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("Retention policy must be a JSON object")
    data = raw.get("retention_days", raw)
    if not isinstance(data, dict):
        raise ValidationError("retention_days must be a JSON object")
    unknown = set(data) - set(POLICY_KEYS) - {"version", "retention_days"}
    if unknown:
        raise ValidationError(f"Unknown retention policy keys: {', '.join(sorted(unknown))}")
    policy = {}
    for key in POLICY_KEYS:
        value = data.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= 36500):
            raise ValidationError(f"{key} must be an integer number of days 1..36500 or null (keep)")
        policy[key] = value
    return policy


def policy_source(path: str | Path | None = None) -> Path | None:
    if path:
        return Path(path)
    if os.environ.get(POLICY_ENV):
        return Path(os.environ[POLICY_ENV])
    return DEFAULT_POLICY_PATH if DEFAULT_POLICY_PATH.is_file() else None


def load_policy(path: str | Path | None = None) -> tuple[dict, Path]:
    source = policy_source(path)
    if source is None:
        raise ValidationError(f"No retention policy configured. Pass --policy FILE, set {POLICY_ENV} "
                              f"or create {DEFAULT_POLICY_PATH} (see config/retention.example.json)")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Retention policy is unreadable or not JSON: {source}") from exc
    return validate_policy(raw), source


def _dt(value) -> datetime | None:
    try:
        return datetime.fromisoformat(parse_utc(value))
    except (ValidationError, TypeError, AttributeError):
        return None


def _older(value, cutoff: datetime | None) -> bool:
    when = _dt(value)
    return cutoff is not None and when is not None and when < cutoff


def plan_purge(con, policy: dict, now: str | None = None) -> dict:
    """Read-only retention plan; selects rows by policy and excludes protected rows."""
    policy = validate_policy(policy)
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    cutoff = {key: (current - timedelta(days=days) if days else None) for key, days in policy.items()}
    claim_guard = current - timedelta(hours=SEND_CLAIM_PROTECT_HOURS)
    statuses = {row["id"]: row["status"] for row in con.execute("SELECT id,status FROM posts")}
    variant_posts = {row["post_id"] for row in con.execute("SELECT post_id FROM campaign_variants")}
    claimed_posts = {row["post_id"] for row in con.execute(
        "SELECT DISTINCT post_id FROM event_log WHERE action='send_claimed' AND post_id IS NOT NULL")}
    last_event = {row["post_id"]: row["at"] for row in con.execute(
        "SELECT post_id, MAX(at) AS at FROM event_log WHERE post_id IS NOT NULL GROUP BY post_id")}
    actions: dict[str, list] = {key: [] for key in ACTION_KEYS}
    protected = {"recent_send_claimed_events": 0, "recent_provider_cooldown_events": 0,
                 "recent_create_session_events": 0,
                 "events_of_sending_or_needs_review_posts": 0, "active_posts_never_touched": 0}

    for row in con.execute("SELECT id,observed_at FROM metric_snapshots ORDER BY id"):
        if _older(row["observed_at"], cutoff["metric_snapshots_days"]):
            actions["metric_snapshots_delete"].append(row["id"])

    for row in con.execute("SELECT id,status,created_at,content FROM posts ORDER BY created_at,id"):
        if row["status"] in ACTIVE_STATUSES:
            protected["active_posts_never_touched"] += 1
        elif row["status"] == "cancelled" and cutoff["cancelled_drafts_days"] is not None:
            # Age from the latest audit event (cancellation) when it is newer than creation.
            reference = max(filter(None, (_dt(row["created_at"]), _dt(last_event.get(row["id"])))), default=None)
            if reference is None or reference >= cutoff["cancelled_drafts_days"]:
                continue
            if row["id"] in variant_posts or row["id"] in claimed_posts:
                # Campaign fingerprints reference the row; past claims count toward send budgets.
                if row["content"] != PURGED:
                    actions["cancelled_posts_minimize"].append(row["id"])
            else:
                actions["cancelled_posts_delete"].append(row["id"])
    for row in con.execute("""SELECT id,content,created_at,approved_at,send_started_at FROM posts
                              WHERE status='published' ORDER BY created_at,id"""):
        reference = row["send_started_at"] or row["approved_at"] or row["created_at"]
        if row["content"] != PURGED and _older(reference, cutoff["published_content_days"]):
            actions["published_content_redact"].append(row["id"])

    deleted_posts = set(actions["cancelled_posts_delete"])
    for row in con.execute("SELECT id,at,post_id,action FROM event_log ORDER BY id"):
        if row["action"] == "send_claimed" and not _older(row["at"], claim_guard):
            protected["recent_send_claimed_events"] += 1
            continue
        if row["action"] == COOLDOWN_EVENT and not _older(row["at"], claim_guard):
            protected["recent_provider_cooldown_events"] += 1
            continue
        if row["action"] == SESSION_EVENT and not _older(row["at"], claim_guard):
            protected["recent_create_session_events"] += 1
            continue
        if row["post_id"] in deleted_posts:
            actions["event_log_delete"].append(row["id"])
            continue
        if statuses.get(row["post_id"]) in ("sending", "needs_review"):
            protected["events_of_sending_or_needs_review_posts"] += 1
            continue
        if _older(row["at"], cutoff["event_log_days"]):
            actions["event_log_delete"].append(row["id"])

    for row in con.execute("""SELECT v.campaign_id,v.variant_id,v.evidence_json,v.recorded_at,p.status
                              FROM campaign_variants v JOIN posts p ON p.id=v.post_id
                              ORDER BY v.recorded_at,v.campaign_id,v.variant_id"""):
        if row["status"] in ("published", "cancelled") and _older(row["recorded_at"], cutoff["campaign_evidence_days"]) \
                and row["evidence_json"] != _minimized_evidence(row["evidence_json"]):
            actions["campaign_evidence_minimize"].append([row["campaign_id"], row["variant_id"]])

    return {"dry_run": True, "planned_at": now, "policy": policy, "actions": actions,
            "counts": {key: len(value) for key, value in actions.items()}, "protected": protected,
            "note": ("LOCAL STATE ONLY. Posts in draft/approved/scheduled/sending/needs_review, campaign_variants rows, "
                     "published remote ids and send_claimed/provider_cooldown/bluesky_create_session events newer than 48h "
                     "are never deleted.")}


def _minimized_evidence(evidence_json: str) -> str:
    try:
        items = json.loads(evidence_json)
    except (TypeError, json.JSONDecodeError):
        return "[]"
    if not isinstance(items, list):
        return "[]"
    return json.dumps([{"id": item.get("id"), "minimized": True} for item in items if isinstance(item, dict)])


def apply_purge(con, plan: dict, *, now: str | None = None) -> dict:
    """Apply a plan atomically. Every statement re-checks the hard safety rules in SQL."""
    if not isinstance(plan, dict) or not isinstance(plan.get("actions"), dict):
        raise ValidationError("Invalid purge plan")
    actions = plan["actions"]
    real_now = datetime.fromisoformat(parse_utc(now or utc_now()))
    planned = _dt(plan.get("planned_at")) or real_now
    # The more protective (earlier) of the two clocks bounds the send-claim guard.
    guard = (min(real_now, planned) - timedelta(hours=SEND_CLAIM_PROTECT_HOURS)).isoformat(timespec="seconds")
    active = ",".join(f"'{status}'" for status in ACTIVE_STATUSES)
    counts = {key: 0 for key in ACTION_KEYS}
    con.execute("PRAGMA secure_delete=ON")  # Overwrite freed pages with zeros where SQLite supports it.
    # Issue #23: a minimized/redacted post also drops its video record (it holds a local file path).
    clear = ",video_json=NULL" if "video_json" in {r[1] for r in con.execute("PRAGMA table_info(posts)")} else ""
    con.execute("BEGIN IMMEDIATE")
    try:
        for snapshot_id in actions.get("metric_snapshots_delete", []):
            counts["metric_snapshots_delete"] += con.execute(
                "DELETE FROM metric_snapshots WHERE id=?", (snapshot_id,)).rowcount
        for post_id in actions.get("cancelled_posts_delete", []):
            safe = con.execute("""SELECT 1 FROM posts p WHERE p.id=? AND p.status='cancelled'
                AND NOT EXISTS(SELECT 1 FROM campaign_variants v WHERE v.post_id=p.id)
                AND NOT EXISTS(SELECT 1 FROM event_log e WHERE e.post_id=p.id AND e.action='send_claimed')""",
                (post_id,)).fetchone()
            if safe:
                counts["metric_snapshots_delete"] += con.execute(
                    "DELETE FROM metric_snapshots WHERE post_id=?", (post_id,)).rowcount
                counts["event_log_delete"] += con.execute(
                    "DELETE FROM event_log WHERE post_id=? AND action<>'send_claimed'", (post_id,)).rowcount
                counts["cancelled_posts_delete"] += con.execute(
                    "DELETE FROM posts WHERE id=? AND status='cancelled'", (post_id,)).rowcount
        # A reply's target names a third party (parent author, parent URL): minimized with the text.
        target_sql = (",reply_target=CASE WHEN reply_target IS NULL THEN NULL ELSE ? END"
                      if has_reply_column(con) else "")
        target_args = (PURGED_REPLY_TARGET,) if target_sql else ()
        for post_id in actions.get("cancelled_posts_minimize", []):
            counts["cancelled_posts_minimize"] += con.execute(
                f"UPDATE posts SET content=?,media_json=NULL,last_error=NULL{clear}{target_sql} WHERE id=? AND status='cancelled'",
                (PURGED, *target_args, post_id)).rowcount
        for post_id in actions.get("published_content_redact", []):
            # Keep id/account/platform/remote_id/remote_url/status/timestamps for duplicate protection.
            counts["published_content_redact"] += con.execute(
                f"UPDATE posts SET content=?,media_json=NULL,last_error=NULL{clear}{target_sql} WHERE id=? AND status='published'",
                (PURGED, *target_args, post_id)).rowcount
        for event_id in actions.get("event_log_delete", []):
            counts["event_log_delete"] += con.execute("""DELETE FROM event_log WHERE id=?
                AND NOT (action IN ('send_claimed','provider_cooldown','bluesky_create_session') AND at>?)
                AND (post_id IS NULL OR post_id NOT IN (SELECT id FROM posts WHERE status IN ('sending','needs_review')))""",
                (event_id, guard)).rowcount
        for campaign_id, variant_id in actions.get("campaign_evidence_minimize", []):
            row = con.execute(f"""SELECT v.evidence_json FROM campaign_variants v JOIN posts p ON p.id=v.post_id
                WHERE v.campaign_id=? AND v.variant_id=? AND p.status NOT IN ({active})""",
                (campaign_id, variant_id)).fetchone()
            if row:
                counts["campaign_evidence_minimize"] += con.execute(
                    "UPDATE campaign_variants SET evidence_json=? WHERE campaign_id=? AND variant_id=?",
                    (_minimized_evidence(row["evidence_json"]), campaign_id, variant_id)).rowcount
        event(con, "privacy_purge", None, json.dumps(counts, sort_keys=True, separators=(",", ":")))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"dry_run": False, "applied_at": real_now.isoformat(timespec="seconds"), "counts": counts,
            "note": "Local rows only. Backups, exports and remote platform copies are not affected."}


def _mode(path: Path) -> str | None:
    try:
        return f"{stat.S_IMODE(path.stat().st_mode):04o}"
    except OSError:
        return None


def privacy_status(con, *, policy_path: str | Path | None = None, env=None) -> dict:
    """Offline report: DB permissions/size, row counts, oldest records, retention configuration."""
    database = next((Path(row["file"]) for row in con.execute("PRAGMA database_list")
                     if row["name"] == "main" and row["file"]), None)
    file_mode = _mode(database) if database else None
    dir_mode = _mode(database.parent) if database else None
    oldest = {
        "posts_created_at": con.execute("SELECT MIN(created_at) FROM posts").fetchone()[0],
        "event_log_at": con.execute("SELECT MIN(at) FROM event_log").fetchone()[0],
        "metric_snapshots_observed_at": con.execute("SELECT MIN(observed_at) FROM metric_snapshots").fetchone()[0],
        "campaign_variants_recorded_at": con.execute("SELECT MIN(recorded_at) FROM campaign_variants").fetchone()[0],
    }
    source = policy_source(policy_path)
    retention: dict[str, Any] = {"configured": False, "source": str(source) if source else None, "policy": None, "error": None}
    if source is not None:
        try:
            retention["policy"], _ = load_policy(source)
            retention["configured"] = True
        except ValidationError as exc:
            retention["error"] = str(exc)
    return {
        "database": {
            "path": str(database) if database else None,
            "file_mode": file_mode, "directory_mode": dir_mode,
            "file_private": file_mode is not None and int(file_mode, 8) & 0o077 == 0,
            "directory_private": dir_mode is not None and int(dir_mode, 8) & 0o077 == 0,
            "size_bytes": database.stat().st_size if database and database.exists() else None,
        },
        "row_counts": {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES},
        "purged_post_content": con.execute("SELECT COUNT(*) FROM posts WHERE content=?", (PURGED,)).fetchone()[0],
        "oldest_records": oldest,
        "retention_policy": retention,
        "secret_env_names_present": sorted(name for name, _ in _env_secrets(env)),
        "note": "OFFLINE. Values of secret environment variables are never printed; only their names.",
    }
