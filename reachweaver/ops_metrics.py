"""Read-only operational metrics for monitoring a ReachWeaver host (issue #26).

``collect`` derives counts from local SQLite state only: the audit ``event_log`` (send
claims, adapter receipts, uncertain sends by C1/C2/C3 class, reconciliations, approval
binding failures, recorded provider cooldowns) and the current post/queue state (status
counts, ``needs_review`` by class, active cooldowns, posts needing re-approval, posts the
opt-out list would block, stale ``sending`` rows, due-but-unsent age, the emergency pause).

Labels are restricted to allow-listed values: the owner's account ids (``accounts.ACCOUNT_ID``
shape), a known platform, a post status and fixed reason/class/stage codes. Post text,
third-party handles, URLs, provider error text and credential values never become a label
or a value; anything outside the allow-lists is reported as ``invalid``/``other``/``unknown``.

``render_prometheus`` writes the Prometheus text exposition format 0.0.4 (HELP/TYPE lines,
escaped label values, sorted deterministic output) for node_exporter's textfile collector:
https://prometheus.io/docs/instrumenting/exposition_formats/ and
https://github.com/prometheus/node_exporter#textfile-collector (both accessed 2026-10-09).
``write_textfile`` replaces the target atomically (temp file in the same directory, then
``os.replace``), so the collector never reads a half-written file. It refuses the state
database, symbolic links, and any existing file whose name does not end in ``.prom``/``.json``.

Nothing here contacts a provider, retries, reconciles or writes to the database.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .accounts import ACCOUNT_ID
from .health import CLASS_GUIDANCE, sanitize_error_code
from .optout import publish_block_reason
from .policy import PLATFORMS, ValidationError
from .storage import (
    BINDING_MISMATCH, BINDING_MISSING, BINDING_UNAVAILABLE, COOLDOWN_EVENT, COOLDOWN_STAGES,
    event_activity, is_paused, needs_review_details, parse_utc, post_status_counts, queue_health,
    queued_posts, reply_status_counts, utc_now,
)

REPORT = "reachweaver_ops_metrics"
SCHEMA_VERSION = 1
PREFIX = "reachweaver_"
STATUSES = ("draft", "approved", "scheduled", "sending", "published", "needs_review", "cancelled")
BINDING_REASONS = (BINDING_MISSING, BINDING_MISMATCH, BINDING_UNAVAILABLE)
SEND_CLASS_LABELS = tuple(CLASS_GUIDANCE)  # certain_not_sent, likely_not_created, ambiguous, unknown
MAX_SINCE_DAYS = 3650
_EARLIEST_NOW = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LATEST_NOW = datetime(9998, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
REPLACEABLE_SUFFIXES = (".prom", ".json")  # an existing --out file is replaced only with one of these
NOTE = ("LOCAL STATE ONLY: counts derived from the local database; no provider was contacted and "
        "nothing was changed. Event counters drop after data-purge removes events (a counter reset).")
_RELATIVE = re.compile(r"^([1-9][0-9]{0,5})([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
_METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# (key, action(s), help) for audit-event counters. Counted per account_id/platform plus the
# extra label named in _EXTRA_LABEL, if any.
EVENT_COUNTERS = (
    ("send_claims", ("send_claimed",), "Send claims (a live send attempt started) from the audit log."),
    ("published", ("published",), "Adapter-reported publish receipts from the audit log."),
    ("send_failures", ("needs_review",),
     "Uncertain or failed sends moved to needs_review, by C1/C2/C3 send class."),
    ("reconciled", ("reconciled_published", "reconciled_not_published"),
     "Human reconciliations of uncertain sends, by outcome."),
    ("approval_binding_failures", ("approval_binding_failed",),
     "Claims refused because the approval payload binding was missing or changed (issue #7), by reason."),
    ("provider_cooldowns_recorded", (COOLDOWN_EVENT,),
     "Provider 429 cooldowns recorded (issue #11), by stage."),
)


def _account(value: object) -> str:
    return value if isinstance(value, str) and ACCOUNT_ID.fullmatch(value) else "invalid"


def _platform(value: object) -> str:
    if value is None:
        return "unknown"
    return value if isinstance(value, str) and value in PLATFORMS else "other"


def _status(value: object) -> str:
    return value if isinstance(value, str) and value in STATUSES else "other"


def _binding_reason(value: object) -> str:
    return value if isinstance(value, str) and value in BINDING_REASONS else "other"


def _tokens(detail: object) -> dict[str, str]:
    return dict(token.split("=", 1) for token in str(detail or "").split() if "=" in token)


def _send_class(detail: object) -> str:
    """The trailing ``class=`` token written by ``uncertain_send`` (same rule as health)."""
    tokens = str(detail or "").split()
    last = tokens[-1] if tokens else ""
    value = last[len("class="):] if last.startswith("class=") else ""
    return value if value in CLASS_GUIDANCE else "unknown"


def _credential_cause(last_error: object) -> str | None:
    """``permission`` (HTTP 401/403) or ``version_sunset`` (HTTP 426) from a sanitized error code, else None."""
    code = sanitize_error_code(last_error)
    if "http_426" in code:
        return "version_sunset"
    if "http_401" in code or "http_403" in code:
        return "permission"
    return None


def _first_token_value(detail: object, key: str) -> str | None:
    tokens = str(detail or "").split()
    if tokens and tokens[0].startswith(key + "="):
        return tokens[0][len(key) + 1:]
    return None


def parse_since(value: str, now: str) -> str:
    """``--since``: an ISO 8601 time with a timezone, or a relative ``30m``/``24h``/``7d`` before ``now``.

    The relative amount is bounded by ``MAX_SINCE_DAYS`` before any date arithmetic, and a
    window that would start before the earliest representable date is a ValidationError.
    """
    current = datetime.fromisoformat(parse_utc(now))
    match = _RELATIVE.fullmatch(value.strip()) if isinstance(value, str) else None
    if match:
        delta = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
        if delta > timedelta(days=MAX_SINCE_DAYS):
            raise ValidationError(f"--since must be within {MAX_SINCE_DAYS} days")
        try:
            since = current - delta
        except OverflowError:
            raise ValidationError("--since starts before the earliest supported date") from None
    else:
        since = datetime.fromisoformat(parse_utc(value))
    if since > current:
        raise ValidationError("--since must not be after the observation time")
    if current - since > timedelta(days=MAX_SINCE_DAYS):
        raise ValidationError(f"--since must be within {MAX_SINCE_DAYS} days")
    return since.isoformat(timespec="seconds")


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(parse_utc(value))
    except (ValueError, TypeError, ValidationError):
        return None


class _Families:
    """Metric families keyed by name; samples summed per identical label set."""

    def __init__(self) -> None:
        self.families: dict[str, dict] = {}

    def declare(self, name: str, kind: str, help_text: str) -> None:
        if not _METRIC_NAME.fullmatch(PREFIX + name):
            raise ValueError(f"invalid metric name {name}")
        self.families[PREFIX + name] = {"name": PREFIX + name, "type": kind, "help": help_text, "samples": {}}

    def add(self, name: str, value: int, **labels: str) -> None:
        for label in labels:
            if not _LABEL_NAME.fullmatch(label):
                raise ValueError(f"invalid label name {label}")
        key = tuple(sorted(labels.items()))
        samples = self.families[PREFIX + name]["samples"]
        samples[key] = samples.get(key, 0) + int(value)

    def export(self) -> list[dict]:
        out = []
        for name in sorted(self.families):
            family = self.families[name]
            out.append({"name": name, "type": family["type"], "help": family["help"],
                        "samples": [{"labels": dict(key), "value": value}
                                    for key, value in sorted(family["samples"].items())]})
        return out


def collect(con, *, now: str | None = None, since: str | None = None, stale_minutes: int = 30,
            optout: list[dict] | None = None, optout_configured: bool = False) -> dict:
    """All operational metrics at ``now`` from local state. ``since`` limits the event counters.

    ``optout`` is a validated entry list (``optout.load_optout``); queued posts that mention a
    listed handle are counted, never named. SELECT statements only.
    """
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    if not _EARLIEST_NOW <= current <= _LATEST_NOW:  # keeps every window/age computation in range
        raise ValidationError("--now must be between 1970-01-01 and 9998-12-31 (UTC)")
    since = parse_since(since, now) if since is not None else None
    windowed = since is not None
    queue = queue_health(con, now=now, stale_minutes=stale_minutes)
    status_rows = post_status_counts(con)
    account_platforms: dict[str, set[str]] = {}
    for row in status_rows:
        account_platforms.setdefault(row["account_id"], set()).add(row["platform"])

    def platform_of(account_id: str) -> str | None:
        """The account's platform from its posts (raw; None when absent or ambiguous)."""
        found = account_platforms.get(account_id, set())
        return next(iter(found)) if len(found) == 1 else None

    m = _Families()
    m.declare("ops_metrics_generated_timestamp_seconds", "gauge",
              "Unix time at which these metrics were computed; alert when it stops advancing.")
    m.add("ops_metrics_generated_timestamp_seconds", int(current.timestamp()))
    if since is not None:
        m.declare("ops_metrics_window_start_timestamp_seconds", "gauge",
                  "Unix time of --since: the *_in_window event counts cover [since, generated].")
        m.add("ops_metrics_window_start_timestamp_seconds", int(datetime.fromisoformat(since).timestamp()))
    m.declare("emergency_paused", "gauge", "1 while the global emergency pause is active, else 0.")
    m.add("emergency_paused", 1 if is_paused(con) else 0)

    m.declare("posts", "gauge", "Local posts by account, platform and status.")
    for row in status_rows:
        m.add("posts", row["count"], account_id=_account(row["account_id"]),
              platform=_platform(row["platform"]), status=_status(row["status"]))
    m.declare("reply_posts", "gauge", "Of those posts, replies (issue #17) by account, platform and status; "
                                      "counted separately, also included in posts.")
    for row in reply_status_counts(con):
        m.add("reply_posts", row["count"], account_id=_account(row["account_id"]),
              platform=_platform(row["platform"]), status=_status(row["status"]))

    # Audit-event counters: all-time counters, or gauges over the --since window.
    actions = tuple(action for _, group, _ in EVENT_COUNTERS for action in group)
    events = event_activity(con, actions, since=since, until=now)
    names: dict[str, tuple[str, str]] = {}
    for key, group, help_text in EVENT_COUNTERS:
        name = f"{key}_in_window" if windowed else f"{key}_total"
        kind = "gauge" if windowed else "counter"
        m.declare(name, kind, help_text + (" Window: --since to generated time." if windowed else " All time."))
        for action in group:
            names[action] = (key, name)
    for row in events:
        key, name = names[row["action"]]
        account, platform = row["account_id"], row["platform"]
        extra: dict[str, str] = {}
        if key == "send_failures":
            extra["send_class"] = _send_class(row["detail"])
        elif key == "reconciled":
            extra["outcome"] = "published" if row["action"] == "reconciled_published" else "not_published"
        elif key == "approval_binding_failures":
            extra["reason"] = _binding_reason(_first_token_value(row["detail"], "reason"))
        elif key == "provider_cooldowns_recorded":
            tokens = _tokens(row["detail"])
            account = tokens.get("account")  # the cooldown's account, also for post-less events
            stage = tokens.get("stage")
            extra["stage"] = stage if stage in COOLDOWN_STAGES else "unknown"
            if platform is None and isinstance(account, str):
                platform = platform_of(account)
        m.add(name, 1, account_id=_account(account) if account is not None else "unknown",
              platform=_platform(platform), **extra)

    # Current state gauges.
    m.declare("needs_review_posts", "gauge",
              "Posts in needs_review (dead letter: uncertain sends awaiting human reconcile), by send class.")
    review_details = needs_review_details(con)
    for post in queue["ambiguous_needs_review"]:
        m.add("needs_review_posts", 1, account_id=_account(post["account_id"]),
              platform=_platform(post["platform"]), send_class=_send_class(review_details.get(post["post_id"])))
    m.declare("needs_review_credential_posts", "gauge",
              "needs_review posts whose stored error code is HTTP 401/403 (cause permission) or 426 "
              "(cause version_sunset); see the revoked-permission and API-sunset runbook (issue #26).")
    for row in con.execute("SELECT account_id, platform, last_error FROM posts WHERE status='needs_review'"):
        cause = _credential_cause(row["last_error"])
        if cause:
            m.add("needs_review_credential_posts", 1, account_id=_account(row["account_id"]),
                  platform=_platform(row["platform"]), cause=cause)
    m.declare("dead_letter_posts", "gauge", "Total needs_review posts (the dead-letter count); never retried.")
    m.add("dead_letter_posts", len(queue["ambiguous_needs_review"]))

    m.declare("stale_sending_posts", "gauge",
              "Posts stuck in sending longer than --stale-minutes (or with a missing/invalid start time).")
    for post in queue["stale_sending"]:
        m.add("stale_sending_posts", 1, account_id=_account(post["account_id"]), platform=_platform(post["platform"]))
    m.declare("overdue_scheduled_posts", "gauge", "Scheduled posts whose due time has passed but are not sent.")
    for post in queue["overdue_scheduled"]:
        m.add("overdue_scheduled_posts", 1, account_id=_account(post["account_id"]),
              platform=_platform(post["platform"]))
    m.declare("approval_rebind_required_posts", "gauge",
              "Approved/scheduled posts no claim accepts until a human re-approves them (issue #7), by reason.")
    for post in queue["approval_rebind_required"]:
        m.add("approval_rebind_required_posts", 1, account_id=_account(post["account_id"]),
              platform=_platform(post["platform"]), reason=_binding_reason(post["reason"]))
    m.declare("send_attempts_rolling_24h", "gauge", "Send claims in the rolling 24 hours, per account.")
    for account_id, count in sorted(queue["send_attempts_rolling_24h_by_account"].items()):
        m.add("send_attempts_rolling_24h", count, account_id=_account(account_id),
              platform=_platform(platform_of(account_id)))

    m.declare("provider_cooldown_active", "gauge",
              "1 per account while a provider 429 cooldown is active (issue #11).")
    m.declare("provider_cooldown_remaining_seconds", "gauge",
              "Seconds until the account's active provider cooldown ends.")
    for cooldown in queue["provider_cooldowns"]:
        labels = {"account_id": _account(cooldown["account_id"]),
                  "platform": _platform(platform_of(cooldown["account_id"]))}
        m.add("provider_cooldown_active", 1, **labels)
        remaining = int((datetime.fromisoformat(cooldown["until"]) - current).total_seconds())
        m.add("provider_cooldown_remaining_seconds", remaining, **labels)

    # Due-but-unsent: approved without a schedule (due since approval) or scheduled at/before now.
    queued = queued_posts(con)
    oldest = 0
    m.declare("due_unsent_posts", "gauge", "Approved or scheduled posts that are due now but not yet claimed.")
    for queued_post in queued:
        if queued_post["status"] == "approved" and not queued_post["scheduled_at"]:
            due_since = queued_post["approved_at"] or queued_post["created_at"]
        elif queued_post["status"] == "scheduled":
            due_since = queued_post["scheduled_at"]
        else:
            continue
        due_at = _parse_time(due_since)
        if due_at is None:
            if queued_post["status"] == "scheduled":
                continue  # missing/invalid due time: counted by overdue_scheduled_posts, not aged here
            age = 0
        elif due_at > current:
            continue  # scheduled for later
        else:
            age = int((current - due_at).total_seconds())
        m.add("due_unsent_posts", 1, account_id=_account(queued_post["account_id"]),
              platform=_platform(queued_post["platform"]))
        oldest = max(oldest, age)
    m.declare("oldest_due_unsent_age_seconds", "gauge",
              "Age in seconds of the oldest due-but-unsent approved/scheduled post (0 when none).")
    m.add("oldest_due_unsent_age_seconds", oldest)

    m.declare("optout_list_entries", "gauge", "Entries in the loaded opt-out list (0 when none is configured).")
    m.add("optout_list_entries", len(optout or []))
    m.declare("optout_blocked_posts", "gauge",
              "Approved/scheduled posts that mention an opt-out listed handle; publish withholds them.")
    if optout:
        for queued_post in queued:
            if publish_block_reason(queued_post["content"], optout, queued_post["platform"]):
                m.add("optout_blocked_posts", 1, account_id=_account(queued_post["account_id"]),
                      platform=_platform(queued_post["platform"]))

    return {"report": REPORT, "schema_version": SCHEMA_VERSION, "observed_at": now,
            "window": {"since": since, "until": now} if windowed else None,
            "stale_sending_minutes": stale_minutes,
            "optout_list": {"configured": bool(optout_configured), "entries": len(optout or [])},
            "metrics": m.export(), "note": NOTE}



def _escape_label(value: str) -> str:
    """Exposition-format label value escaping: backslash, double quote and line feed."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(value: str) -> str:
    """Exposition-format HELP escaping: backslash and line feed."""
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def render_prometheus(report: dict) -> str:
    """Prometheus text format 0.0.4: one HELP and TYPE line per family, samples sorted."""
    lines = []
    for family in report["metrics"]:
        name = family["name"]
        lines.append(f"# HELP {name} {_escape_help(family['help'])}")
        lines.append(f"# TYPE {name} {family['type']}")
        for sample in family["samples"]:
            labels = ",".join(f'{key}="{_escape_label(str(value))}"'
                              for key, value in sorted(sample["labels"].items()))
            lines.append(f"{name}{{{labels}}} {int(sample['value'])}" if labels else f"{name} {int(sample['value'])}")
    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _check_out_target(path: Path, database: Path | None) -> None:
    """Refuse an ``--out`` target that could destroy data (review F1, issue #26).

    The state database (same path, same resolved path or the same inode via a hard link) and
    any symbolic link are refused. An existing file is replaced only when its name ends in
    ``.prom`` or ``.json``; otherwise the target must not exist yet.
    """
    if path.is_symlink():
        raise ValidationError(f"--out must not be a symbolic link: {path}")
    if path.is_dir():
        raise ValidationError(f"--out must be a file path, not a directory: {path}")
    if database is not None:
        same = path.resolve() == database.resolve()
        if not same and path.exists() and database.exists():
            same = os.path.samefile(path, database)
        if same:
            raise ValidationError(f"--out must not be the state database: {path}")
    if path.exists() and not path.name.endswith(REPLACEABLE_SUFFIXES):
        raise ValidationError(f"--out {path} already exists and its name does not end in .prom or .json; "
                              "ops-metrics replaces only such files")


def write_textfile(path: Path, text: str, *, database: Path | None = None) -> None:
    """Atomically replace ``path`` with ``text`` (mode 0644): temp file in the same directory,
    fsync, then ``os.replace``. The content holds counts and allow-listed labels only.

    ``database`` is the state database path, which is never overwritten; see
    ``_check_out_target`` for the other refusals. Nothing is written when one applies.
    """
    path = Path(path)
    _check_out_target(path, Path(database) if database is not None else None)
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise ValidationError(f"--out directory does not exist: {parent}")
    # node_exporter's textfile collector reads only *.prom, so the ".tmp" name is never collected.
    fd, temp = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        if fd != -1:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
