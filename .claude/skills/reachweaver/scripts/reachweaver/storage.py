"""Transactional durable outbox; automatic ambiguous retries are intentionally forbidden."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from dataclasses import dataclass

from .policy import ValidationError, validate_post


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Date must be ISO 8601 with a timezone, e.g. 2026-10-15T09:00:00+02:00") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValidationError("Date must include an explicit timezone")
    try:
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except OverflowError:  # e.g. 0001-01-01T00:00:00+01:00 is before datetime.min in UTC
        raise ValidationError("Date is out of range") from None


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  content TEXT NOT NULL,
  campaign TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK(status IN ('draft','approved','scheduled','sending','published','needs_review','cancelled')),
  created_at TEXT NOT NULL,
  approved_at TEXT,
  scheduled_at TEXT,
  remote_id TEXT,
  remote_url TEXT,
  last_error TEXT,
  approved_identity TEXT,  -- wave 9 item 2; placed here so the legacy-DDL guard test stays untouched
  approval_nonce TEXT,  -- wave 9 item 1 (same placement rule): per-approval random id, see reachweaver/ledger.py
  send_started_at TEXT,
  approved_payload_sha256 TEXT,
  approved_by TEXT,
  approval_channel TEXT,
  approved_payload_mac TEXT,
  media_json TEXT,
  video_json TEXT,
  reply_target TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_due ON posts(status,scheduled_at);
CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  post_id TEXT,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_claims ON event_log(action,at,post_id);
CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaign_variants (
  campaign_id TEXT NOT NULL, brand_id TEXT NOT NULL, variant_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL, post_id TEXT NOT NULL UNIQUE REFERENCES posts(id),
  evidence_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
  PRIMARY KEY(campaign_id,variant_id)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT NOT NULL REFERENCES posts(id),
  observed_at TEXT NOT NULL,
  source TEXT NOT NULL,
  views INTEGER, impressions INTEGER, likes INTEGER, replies INTEGER,
  reposts INTEGER, saves INTEGER, clicks INTEGER, signups INTEGER,
  window_start TEXT, window_end TEXT,
  metric_status TEXT,
  UNIQUE(post_id, observed_at, source)
);
CREATE TABLE IF NOT EXISTS cooldown_scope_members (
  account_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  declared_at TEXT NOT NULL,
  PRIMARY KEY(account_id, scope)
);
"""
METRIC_FIELDS = ("views", "impressions", "likes", "replies", "reposts", "saves", "clicks", "signups")
# Optional observation window of a snapshot (UTC ISO 8601). NULL means the window is unknown:
# the counts may be lifetime cumulative or cover a provider-defined period.
WINDOW_FIELDS = ("window_start", "window_end")
# Issue #7 approval payload binding (owner-approved 2026-10-08, docs/SECURITY-REVIEW-PROPOSAL-7-11.md).
# NULL hash on an approved/scheduled row = approved before the binding existed; such a row
# cannot be claimed or scheduled until a human re-approves it (unapprove, show, approve).
# There is no grandfathering and no bypass switch.
APPROVAL_BINDING_FIELDS = ("approved_payload_sha256", "approved_by", "approval_channel")
APPROVAL_BINDING_TAG = "reachweaver.storage.approval/1"
APPROVAL_CHANNELS = ("cli_flag", "cli_env_user", "cli_unattributed", "library")
APPROVER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,119}$")
BINDING_MISSING = "approval_hash_missing_reapproval_required"
BINDING_MISMATCH = "approval_hash_mismatch_reapproval_required"
BINDING_UNAVAILABLE = "approval_binding_unavailable"
# Issue #7 D7 keyed binding (owner-approved 2026-10-09: docs/SECURITY-REVIEW-PROPOSAL-7-d6-d7.md).
# HMAC-SHA256 (RFC 2104) over the post id and the plain payload hash, with an owner key read only
# through accounts.credential (environment or systemd LoadCredential). Keyed mode is required when
# this process holds the key, when the DB latch says the owner enabled it, or when a row carries a
# MAC; a forged or edited row cannot carry a valid MAC without the key. The latch only tightens.
APPROVAL_KEY_CREDENTIAL = "REACHWEAVER_APPROVAL_SECRET"  # *_SECRET: privacy.redact and credentials-audit treat it as a secret
APPROVAL_KEY_MIN_CHARS = 32  # RFC 2104 section 3: keys shorter than L (32 bytes for SHA-256) are strongly discouraged
APPROVAL_MAC_FIELDS = ("approved_payload_mac",)
APPROVAL_MAC_TAG = "reachweaver.storage.approval-mac/1"
APPROVAL_MAC_PREFIX = "hmac-sha256/1:"
KEYED_MODE_SETTING = "approval_binding_mode"
KEYED_MODE_VALUE = "keyed"
MAC_MISSING = "approval_mac_missing_reapproval_required"
MAC_MISMATCH = "approval_mac_mismatch_reapproval_required"
APPROVAL_KEY_UNAVAILABLE = "approval_key_unavailable"
# Process configuration problems, not post problems: refused, never recorded on the post.
_CONFIG_REASONS = (BINDING_UNAVAILABLE, APPROVAL_KEY_UNAVAILABLE, "approval_ledger_unavailable")
# Media binding (owner-approved 2026-10-09; #13, #20, #22, #23; disabled until a live receipt). One
# tag per payload kind, never two meanings for one tag or column:
# * images (#13 local files, #20 hosted URLs): ``media_json``, a list; tag ``approval/media-1``
#   adds, per attachment and in order, sha256, mime, alt and focus (plus url for a hosted image);
# * video (#22 YouTube, #23 TikTok): ``video_json``, exactly one shared record (``video.py``); tag
#   ``approval/video-1`` adds the whole record except its local path.
# A post carries images or a video, never both. A text-only post keeps the approval/1 payload
# byte for byte, so every existing approval hash stays valid. The image tag was ``approval/2`` on
# the #13/#20 draft branches (and #22 used ``approval/2`` for video); both were renamed at
# integration (2026-10-09) because nothing media-related had been released or approved.
MEDIA_FIELDS = ("media_json",)
APPROVAL_BINDING_TAG_MEDIA = "reachweaver.storage.approval/media-1"
BINDING_MEDIA_CHANGED = "approval_media_changed_reapproval_required"
VIDEO_FIELDS = ("video_json",)
APPROVAL_BINDING_TAG_VIDEO = "reachweaver.storage.approval/video-1"
BINDING_VIDEO_CHANGED = "approval_video_changed_reapproval_required"
# #17 replies (owner-approved 2026-10-09; disabled until a live receipt) are text-only: the reply
# draft never supported attachments, so a reply row with images or a video is refused at attach
# time, at approve time and by the live path, and its payload is marked never approvable.
REPLY_MEDIA_REFUSED = "reply_media_unsupported: a reply carries text only, never images or a video"
# Issue #11 provider cooldowns (owner-approved 2026-10-08, proposal part 2, D12). A 429 writes one
# durable `provider_cooldown` event; until its deadline, no other unclaimed post of that account
# is claimed. A claimed post is never released or resent because of it (D16).
COOLDOWN_EVENT = "provider_cooldown"
COOLDOWN_REFUSAL = "account_provider_cooldown_active"
COOLDOWN_STAGES = ("preflight", "send")
SEND_CLASSES = ("certain_not_sent", "likely_not_created", "ambiguous")
_COOLDOWN_TOKEN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_COOLDOWN_SOURCES = ("retry_after", "x_ratelimit_reset", "telegram_retry_after", "daily_reset", "default")
_COOLDOWN_MAX_HOURS = 24
# #11 shared scopes (docs/SECURITY-REVIEW-PROPOSAL-11-shared-cooldown.md): an optional
# ``scope=`` token on a cooldown event names an owner-declared group (``postiz-workspace:<slug>``).
# A row without it (every row written before this change) is per-account, as before. The
# ``cooldown_scope_members`` table (added by ``CREATE TABLE IF NOT EXISTS`` when ``connect``
# opens a database, so an old database gains it idempotently) records which accounts declared
# which scope at the last live run; every cooldown check unions it with the caller's scopes.
# Scopes only ever add waiting.
_COOLDOWN_SCOPE = re.compile(r"^[a-z][a-z-]{0,31}:[a-z0-9][a-z0-9-]{0,62}$")
# #11 Bluesky createSession counter: one event per reserved call, one per refusal.
SESSION_EVENT = "bluesky_create_session"
SESSION_REFUSED_EVENT = "bluesky_create_session_refused"
SESSION_REFUSAL = "bluesky_create_session_budget_exhausted"
SESSION_STAGES = ("connect_check", "preflight", "send_reserved")
_SESSION_SCOPE = re.compile(r"^bsky-account:[0-9a-f]{16}$")
# Per-metric value status (#15). An exact count lives in its INTEGER column. Every other state
# leaves that column NULL and, unless it is plain unknown, is recorded in the nullable JSON
# ``metric_status`` column ({"views": {"status": "suppressed"}, "likes": {"status": "estimated",
# "value": 120}}). Readers that only use the integer columns therefore see exact values only:
# a suppressed, unavailable, delayed or estimated value can never be summed as if it were exact.
# NULL column = no statuses (every NULL metric is unknown), which is also what an old row means.
METRIC_STATUSES = ("suppressed", "not_available", "delayed", "estimated")
METRIC_STATUS_LABELS = ("exact", "unknown", *METRIC_STATUSES)
STATUS_FIELDS = ("metric_status",)
METRIC_VALUE_HELP = ("a nonnegative integer, 'unknown' (or empty), 'suppressed' (withheld by the platform, "
                     "e.g. below a privacy threshold), 'not_available' (the platform does not expose it), "
                     "'delayed' (not final yet) or 'estimated:N' (a platform estimate, never summed as exact)")
_EXACT = re.compile(r"^[0-9]{1,18}$")
_ESTIMATED = re.compile(r"^estimated:([0-9]{1,18})$")
# (table, columns) pairs that migrate() may add; names are fixed here, never from input.
# Issue #17 (owner-approved 2026-10-09; disabled until a live receipt): a reply row carries its verified target as canonical
# JSON (replies.canonical_target). NULL = an ordinary post. The approval hash covers it.
REPLY_TARGET_FIELDS = ("reply_target",)
# Wave 9 item 2 (owner-approved 2026-10-10; docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md section 2):
# opt-in verified-identity fingerprint stored at approve time (reachweaver/identity.py). NULL = no
# identity binding, and the approval payload is then byte-identical to before.
IDENTITY_FIELDS = ("approved_identity",)
# Wave 9 item 1 (owner-approved 2026-10-10; docs/SECURITY-REVIEW-PROPOSAL-WAVE9.md section 1): a random
# 128-bit id drawn at every approve, bound into the hash under ``approval/2`` and consumed once in
# the append-only ledger (reachweaver/ledger.py). NULL = approved before the change: not grandfathered.
NONCE_FIELDS = ("approval_nonce",)
_MIGRATIONS = (("metric_snapshots", WINDOW_FIELDS), ("posts", APPROVAL_BINDING_FIELDS),
               ("metric_snapshots", STATUS_FIELDS), ("posts", APPROVAL_MAC_FIELDS), ("posts", MEDIA_FIELDS),
               ("posts", VIDEO_FIELDS), ("posts", REPLY_TARGET_FIELDS), ("posts", IDENTITY_FIELDS),
               ("posts", NONCE_FIELDS))


def migrate(con: sqlite3.Connection) -> list[str]:
    """Add columns that newer schemas define but an older database lacks.

    Only nullable ``ADD COLUMN`` steps, applied when the column is missing, so existing
    rows and older readers are untouched. Returns the columns that were added.
    """
    def missing():
        out = []
        for table, names in _MIGRATIONS:
            have = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            out.extend((table, name) for name in names if name not in have)
        return out

    if not missing():
        return []
    # Re-check under a write lock so two processes opening the same old DB cannot race.
    con.execute("BEGIN IMMEDIATE")
    try:
        added = missing()
        for table, name in added:  # names come from the fixed tuples above, never from input
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return [name for _, name in added]


def schema_gaps(con: sqlite3.Connection, tables: tuple[str, ...] | None = None) -> list[str]:
    """Tables (``name``) and columns (``table.column``) that ``SCHEMA`` defines but ``con`` lacks.

    SELECT/PRAGMA only, so it works on a :func:`connect_readonly` connection; read-only
    commands use it to fail clearly on an older database instead of migrating it.
    ``tables`` limits the check to the tables a command reads.
    """
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(SCHEMA)
        wanted = [row[0] for row in reference.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        expected = {table: [row[1] for row in reference.execute(f"PRAGMA table_info({table})")]
                    for table in wanted if tables is None or table in tables}
    finally:
        reference.close()
    gaps: list[str] = []
    for table, columns in expected.items():  # names come from SCHEMA, never from input
        have = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if not have:
            gaps.append(table)
        else:
            gaps.extend(f"{table}.{column}" for column in columns if column not in have)
    return gaps


def database_path(path: str | Path | None = None) -> Path:
    """``--db``, else ``$REACHWEAVER_DB``, else ``state/reachweaver.sqlite``."""
    return Path(path or os.environ.get("REACHWEAVER_DB", "state/reachweaver.sqlite"))


@contextmanager
def connect(path: str | None = None):
    database = database_path(path)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        # The DB contains private unpublished drafts, identities and engagement data.
        # Fail closed rather than silently modifying permissions of an existing user path.
        if database.is_symlink():
            raise ValidationError("State database cannot be a symbolic link")
        if stat.S_IMODE(database.parent.stat().st_mode) & 0o077:
            raise ValidationError("State directory is not private; restrict it with chmod 700 before running")
        if not database.exists():
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(database, flags, 0o600)
            except FileExistsError:
                pass  # Another trusted process may have created it under this private dir.
            else:
                os.close(fd)
        if database.is_symlink() or stat.S_IMODE(database.stat().st_mode) & 0o077:
            raise ValidationError("State database is not private; restrict it with chmod 600 before running")
    con = sqlite3.connect(str(database), timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.executescript(SCHEMA)
        migrate(con)
        yield con
    finally:
        con.close()


@contextmanager
def connect_readonly(path: str | Path):
    """Open an EXISTING ReachWeaver database strictly read-only (issue #10 history checks).

    Unlike :func:`connect` this never creates the file or its directory, never runs the schema
    script or migrations, and opens SQLite with ``mode=ro`` plus ``PRAGMA query_only``, so the
    database bytes cannot change. A missing path, a symlink, a non-SQLite file or a database
    without a ``posts`` table fails with a ValidationError naming the problem.
    """
    database = Path(path)
    if database.is_symlink():
        raise ValidationError("State database cannot be a symbolic link")
    if not database.is_file():
        raise ValidationError(f"Database {database} does not exist; read-only checks never create one")
    try:
        con = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=15,
                              isolation_level=None)
    except sqlite3.Error as exc:
        raise ValidationError(f"Cannot open {database} read-only: {exc}") from exc
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "posts" not in tables:
            raise ValidationError(f"{database} is not a ReachWeaver database (no posts table)")
        yield con
    except sqlite3.Error as exc:
        raise ValidationError(f"Cannot read {database}: {exc}") from exc
    finally:
        con.close()


def event(con: sqlite3.Connection, action: str, post_id: str | None = None,
          detail: str = "", *, at: str | None = None) -> None:
    con.execute("INSERT INTO event_log(at,post_id,action,detail) VALUES(?,?,?,?)",
                (parse_utc(at) if at else utc_now(), post_id, action, detail))


@contextmanager
def _atomic(con):
    """Run a write sequence under one ``BEGIN IMMEDIATE`` (send-state atomicity review, P2/P3/P5).

    Already inside a caller's transaction (e.g. the calendar apply), join it and let the caller
    commit or roll back. Otherwise take the write lock, then COMMIT on success or ROLLBACK on any
    error, so a refused or failed change leaves nothing half-written.
    """
    if con.in_transaction:
        yield
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        con.execute("ROLLBACK")
        raise
    try:
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:  # a failed COMMIT must not leave the write lock held
            con.execute("ROLLBACK")
        raise


def add_post(con, account_id, platform, content, campaign="", source_url="", *, account=None,
             reply_target: str | None = None) -> str:
    """Insert one draft. ``reply_target`` (issue #17) is the canonical JSON that
    ``replies.verify_reply_target`` produced; it is stored verbatim and bound by approval."""
    issues = validate_post(platform, content, account)
    if issues:
        raise ValidationError("; ".join(issues))
    if reply_target is not None and (not isinstance(reply_target, str) or not reply_target.startswith("{")):
        raise ValidationError("reply_target must be canonical JSON from replies.canonical_target")
    post_id = uuid4().hex[:16]
    if reply_target is None:
        con.execute("INSERT INTO posts (id,account_id,platform,content,campaign,source_url,created_at) VALUES(?,?,?,?,?,?,?)",
                    (post_id, account_id, platform, content, campaign, source_url, utc_now()))
    else:
        con.execute("INSERT INTO posts (id,account_id,platform,content,campaign,source_url,created_at,reply_target) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (post_id, account_id, platform, content, campaign, source_url, utc_now(), reply_target))
    event(con, "draft_created", post_id, f"account={account_id} platform={platform}"
          + (" kind=reply" if reply_target is not None else ""))
    return post_id


def reply_target_text(row) -> str | None:
    """The stored reply target JSON of a posts row, or None (ordinary post / pre-#17 table)."""
    if row is None or "reply_target" not in row.keys():
        return None
    value = row["reply_target"]
    return value if value not in (None, "") else None


def get_post(con, post_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        raise ValidationError(f"Unknown post id: {post_id}")
    return row


def _canonical_json(obj) -> bytes:
    # Byte-identical to schemas.canonical_json (asserted in tests); schemas imports this
    # module, so importing it here would be circular.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def post_media(row) -> list[dict]:
    """The row's image attachments in send order; [] when none (or no ``media_json`` column).

    A malformed stored value raises ValidationError: callers that send must fail closed.
    """
    if row is None or "media_json" not in row.keys() or row["media_json"] in (None, ""):
        return []
    from .attachments import decode_media
    return decode_media(row["media_json"])


def published_text(row) -> str:
    """Every human-readable string a send publishes: the post text, each alt text (#13, #20) and
    any text a video record sends besides the post text (#22 title and tags).

    Opt-out checks use this, so a listed handle in an image description or video title is caught too.
    """
    try:
        alts = [item["alt"] for item in post_media(row)]
    except ValidationError:
        alts = [str(row["media_json"])]  # malformed: check the raw text rather than skip it
    try:
        video = post_video(row)
        extra = video_published_strings(video) if video is not None else []
    except ValidationError:
        extra = [str(row["video_json"])]  # malformed: check the raw text rather than skip it
    return "\n".join([row["content"], *alts, *extra])


def video_published_strings(record: dict) -> list[str]:
    from .video import published_strings
    return published_strings(record)


def approval_payload(row, *, scheduled_at: str | None = None, use_row_schedule: bool = True,
                     identity: str | None = None, nonce: str | None = None) -> dict:
    """The approval payload (see ``_approval_payload_base``) plus, only when an identity is bound,
    ``identity_binding`` (tag ``approval/id-1``) and ``identity`` (the fingerprint from
    ``reachweaver.identity``), and, only when an approval nonce is present, ``nonce_binding`` (tag
    ``approval/2``) and ``approval_nonce`` (wave 9 item 1, ``reachweaver.ledger``). ``identity`` and
    ``nonce`` override the row's columns (used by ``transition`` while approving); with neither the
    payload is byte-identical to before. The keys are distinct and the JSON is canonical (sorted), so
    the combined tags have one fixed byte order."""
    payload = _approval_payload_base(row, scheduled_at=scheduled_at, use_row_schedule=use_row_schedule)
    bound = identity or (row["approved_identity"] if "approved_identity" in row.keys() else None)
    if bound:
        from .identity import IDENTITY_TAG
        payload["identity_binding"] = IDENTITY_TAG
        payload["identity"] = bound
    bound_nonce = nonce or (row["approval_nonce"] if "approval_nonce" in row.keys() else None)
    if bound_nonce:
        from .ledger import NONCE_TAG
        payload["nonce_binding"] = NONCE_TAG
        payload["approval_nonce"] = bound_nonce
    return payload


def _approval_payload_base(row, *, scheduled_at: str | None = None, use_row_schedule: bool = True) -> dict:
    """The exact fields an approval binds: what is sent, where, when, and its provenance.

    With image attachments (#13 local files, #20 owner-hosted URLs) the payload is tagged
    ``approval/media-1``: the text fields plus ``media``, a list of ``{sha256, mime, alt, focus}``
    (plus ``url`` for a hosted image, because the URL is what is sent) in send order. With a
    video (#22, #23) it is tagged ``approval/video-1``: the text fields plus ``video``, the shared
    record without its local path (``video.bound_view``). A malformed ``media_json`` or
    ``video_json`` binds a digest of the raw value instead, which can never equal a hash
    ``approve`` stored (``approve`` refuses malformed records and posts with both), so such a row
    fails closed.

    A reply (#17) also binds ``reply_target``: the stored JSON string byte for byte (Bluesky
    root/parent URI+CID, Mastodon server + in_reply_to_id + visibility). The key is present only
    for replies, so the hash of every ordinary post is unchanged, and adding, removing or editing
    a target after approval is a mismatch. A reply is text-only: ``approve`` and the attach
    commands refuse a reply row with images or a video (``REPLY_MEDIA_REFUSED``).
    """
    payload = {
        "binding": APPROVAL_BINDING_TAG,
        "account_id": row["account_id"], "platform": row["platform"], "content": row["content"],
        "scheduled_at": (row["scheduled_at"] if use_row_schedule else scheduled_at) or None,
        "source_url": row["source_url"], "campaign": row["campaign"],
    }
    target = reply_target_text(row)
    if target is not None:
        payload["reply_target"] = target
    raw = row["media_json"] if "media_json" in row.keys() else None
    raw_video = row["video_json"] if "video_json" in row.keys() else None
    if raw in (None, "") and raw_video in (None, ""):
        return payload  # text-only (or a text-only reply): identical to the pre-#13 payload
    if target is not None:
        payload["reply_media_invalid"] = True  # never approvable: a reply is text-only (#17)
    if raw not in (None, ""):
        from .attachments import bound_view
        payload["binding"] = APPROVAL_BINDING_TAG_MEDIA
        try:
            payload["media"] = bound_view(post_media(row))
        except ValidationError:
            payload["media_invalid_sha256"] = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()
    if raw_video not in (None, ""):
        from .video import bound_view as bound_video
        if raw not in (None, ""):
            payload["media_and_video_invalid"] = True  # never approvable: images or a video, not both
        payload["binding"] = APPROVAL_BINDING_TAG_VIDEO
        try:
            payload["video"] = bound_video(post_video(row) or {})
        except ValidationError:
            payload["video_invalid_sha256"] = hashlib.sha256(str(raw_video).encode("utf-8")).hexdigest()
    return payload


def approval_payload_sha256(row, **kw) -> str:
    return hashlib.sha256(_canonical_json(approval_payload(row, **kw))).hexdigest()


def _has_binding_columns(row) -> bool:
    return all(name in row.keys() for name in APPROVAL_BINDING_FIELDS)


@dataclass(frozen=True)
class ApprovalKeyMode:
    """Keyed-binding state for one process (#7 D7). Never prints the key.

    ``key``: the owner key bytes, or None. ``key_error``: why a provided key is unusable (names the
    variable only). ``latched``: the DB says keyed mode is on.
    """
    key: bytes | None = None
    key_error: str | None = None
    latched: bool = False
    identity_required: bool = False  # wave 9 item 2: the DB latch says approvals must carry a verified identity
    max_age_hours: int | None = None  # #39: the DB says an approval older than this many hours is expired (None: off)

    @property
    def required(self) -> bool:
        return self.latched or self.key is not None or self.key_error is not None

    def __repr__(self) -> str:
        return (f"ApprovalKeyMode(key={'set' if self.key is not None else None}, "
                f"key_error={self.key_error!r}, latched={self.latched})")


UNKEYED = ApprovalKeyMode()


def load_approval_key(env=None) -> tuple[bytes | None, str | None]:
    """(key, None); (None, None) when not provided; (None, error) when provided but unusable.

    Read only through ``accounts.credential`` (environment, or $CREDENTIALS_DIRECTORY for systemd
    LoadCredential=). Errors name the variable, never the value.
    """
    from .accounts import credential
    try:
        value = credential(APPROVAL_KEY_CREDENTIAL, env)
    except ValidationError as exc:
        return None, str(exc)
    if value is None or value == "":
        return None, None
    if len(value) < APPROVAL_KEY_MIN_CHARS:
        return None, (f"{APPROVAL_KEY_CREDENTIAL} must be at least {APPROVAL_KEY_MIN_CHARS} characters "
                      "(generate one with: python3 -c 'import secrets; print(secrets.token_hex(32))')")
    return value.encode("utf-8"), None


APPROVAL_MAX_AGE_SETTING = "approval_max_age_hours"
APPROVAL_EXPIRED = "approval_expired_reapproval_required"
APPROVAL_EXPIRES_BEFORE_DUE = "approval_would_expire_before_due_time"
MAX_AGE_LIMITS = (1, 8760)
_EXPIRED_ALWAYS = 0  # a stored value nobody can parse fails closed: every approval counts as expired


def approval_max_age_hours(con) -> int | None:
    """#39: the approval time-to-live in hours the owner set with ``approval-mode --max-age-hours``, else None (off).

    An unreadable stored value fails closed (returns 0, so every approval is expired) rather than switching
    the safeguard off.
    """
    row = con.execute("SELECT value FROM settings WHERE name=?", (APPROVAL_MAX_AGE_SETTING,)).fetchone()
    if row is None:
        return None
    try:
        hours = int(str(row["value"]))
    except ValueError:
        return _EXPIRED_ALWAYS
    return hours if MAX_AGE_LIMITS[0] <= hours <= MAX_AGE_LIMITS[1] else _EXPIRED_ALWAYS


def set_approval_max_age_hours(con, hours: int) -> int:
    """Set or lower the approval time-to-live. Tighten-only: a value at or above the current one is refused.

    Raising or clearing it is an owner procedure (edit the settings row by hand), like the keyed and identity
    latches, so a prompt-injected agent cannot widen the window with a CLI call. Returns the stored value.
    """
    if type(hours) is not int or not MAX_AGE_LIMITS[0] <= hours <= MAX_AGE_LIMITS[1]:
        raise ValidationError(f"--max-age-hours must be a whole number from {MAX_AGE_LIMITS[0]} to {MAX_AGE_LIMITS[1]}")
    current = approval_max_age_hours(con)
    if current is not None and hours >= current:
        raise ValidationError(f"approval max age is already {current} hours; only a lower value is accepted "
                              "(tighten-only; loosening is an owner procedure)")
    con.execute("INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (APPROVAL_MAX_AGE_SETTING, str(hours)))
    return hours


def approval_expiry_problem(row, max_age_hours: int | None, now: str | None = None) -> str | None:
    """None, or ``approval_expired_reapproval_required`` when the approval is older than the owner's limit.

    Pure. The age runs from ``approved_at`` (a schedule keeps it) to ``now``. A row with no readable
    ``approved_at`` is expired when a limit is set.
    """
    if max_age_hours is None:
        return None
    if max_age_hours <= 0:
        return APPROVAL_EXPIRED  # the unreadable-setting sentinel: fail closed
    stamp = row["approved_at"] if "approved_at" in row.keys() else None
    try:
        approved = datetime.fromisoformat(parse_utc(stamp)) if stamp else None
        current = datetime.fromisoformat(parse_utc(now or utc_now()))
    except (ValueError, TypeError, ValidationError):
        return APPROVAL_EXPIRED
    if approved is None or current - approved > timedelta(hours=max_age_hours):
        return APPROVAL_EXPIRED
    return None


def keyed_mode_latched(con) -> bool:
    row = con.execute("SELECT value FROM settings WHERE name=?", (KEYED_MODE_SETTING,)).fetchone()
    return row is not None  # any value counts: an unexpected one fails closed (keyed)


def latch_keyed_mode(con) -> None:
    """Record that keyed mode is on, so a later process without the key fails closed.

    Tighten-only: no ReachWeaver command removes it (owner procedure in the D6/D7 proposal).
    """
    con.execute("INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO NOTHING",
                (KEYED_MODE_SETTING, KEYED_MODE_VALUE))


def identity_mode_required(con) -> bool:
    """Wave 9 item 2: the owner latched ``approval-mode --require-identity`` (any stored value counts)."""
    from .identity import IDENTITY_MODE_SETTING
    return con.execute("SELECT 1 FROM settings WHERE name=?", (IDENTITY_MODE_SETTING,)).fetchone() is not None


def latch_identity_mode(con) -> None:
    """Make a verified identity mandatory for every approval. Tighten-only: no command removes it."""
    from .identity import IDENTITY_MODE_SETTING, IDENTITY_MODE_VALUE
    con.execute("INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO NOTHING",
                (IDENTITY_MODE_SETTING, IDENTITY_MODE_VALUE))


def approval_identity_problem(row, fingerprint: str | None) -> str | None:
    """None, or the reason code when the account identity now differs from the one verified at approval.

    Pure. Call after ``approval_binding_problem`` (the payload hash already covers a tampered
    ``approved_identity``). ``fingerprint``: ``identity.current_fingerprint(account)``; None means it
    could not be computed (no account, or an unbindable one), which fails closed. A row without a
    stored identity is not this function's concern (``approval_binding_problem`` handles the
    required mode).
    """
    from .identity import IDENTITY_MISMATCH
    stored = row["approved_identity"] if "approved_identity" in row.keys() else None
    if not stored:
        return None
    if fingerprint is None or not hmac.compare_digest(str(stored), str(fingerprint)):
        return IDENTITY_MISMATCH
    return None


def approval_key_mode(con, env=None) -> ApprovalKeyMode:
    key, error = load_approval_key(env)
    return ApprovalKeyMode(key=key, key_error=error, latched=keyed_mode_latched(con),
                           identity_required=identity_mode_required(con), max_age_hours=approval_max_age_hours(con))


def approval_mac(key: bytes, post_id: str, payload_sha256: str) -> str:
    """HMAC-SHA256 tag binding one post id to one approved payload hash (#7 D7, RFC 2104).

    The post id is inside the MAC, so a valid tag cannot be copied onto another row with the
    same text. Domain-separated and versioned by ``APPROVAL_MAC_TAG``/``APPROVAL_MAC_PREFIX``.
    """
    message = _canonical_json({"binding": APPROVAL_MAC_TAG, "post_id": post_id, "payload_sha256": payload_sha256})
    return APPROVAL_MAC_PREFIX + hmac.new(key, message, hashlib.sha256).hexdigest()


def approval_binding_problem(row, mode: ApprovalKeyMode | None = None, *, now: str | None = None) -> str | None:
    """None when the stored approval hash matches the row as it is now and has not expired; otherwise a reason code.

    The expiry (#39) is checked last, so tampering keeps its own code: ``mode.max_age_hours`` set and the
    approval older than that is ``approval_expired_reapproval_required``. ``now`` (UTC) makes it testable.

    Pure (no I/O). ``approval_binding_unavailable``: the row comes from a table without the
    binding columns; ``..._missing_...``: approved before the binding existed (or never
    bound); ``..._mismatch_...``: a bound field changed after approval.

    Keyed binding (#7 D7): when ``mode.required`` (or the row carries a MAC) the row must
    also carry a valid ``approved_payload_mac``. ``approval_key_unavailable``: this process has
    no usable key; ``approval_mac_missing_reapproval_required``: an unkeyed approval;
    ``approval_mac_mismatch_reapproval_required``: a forged, copied or other-key tag.
    ``mode=None`` means unkeyed; every enforcement path passes the real mode.
    """
    if not _has_binding_columns(row):
        return BINDING_UNAVAILABLE
    stored = row["approved_payload_sha256"]
    if not stored:
        return BINDING_MISSING
    if not hmac.compare_digest(str(stored), approval_payload_sha256(row)):
        return BINDING_MISMATCH
    mode = UNKEYED if mode is None else mode
    problem = _binding_problem_core(row, mode, stored)
    return problem or approval_expiry_problem(row, mode.max_age_hours, now)


def _binding_problem_core(row, mode: ApprovalKeyMode, stored) -> str | None:
    has_mac_column = all(name in row.keys() for name in APPROVAL_MAC_FIELDS)
    mac = row["approved_payload_mac"] if has_mac_column else None
    if mode.identity_required and not (row["approved_identity"] if "approved_identity" in row.keys() else None):
        from .identity import IDENTITY_MISSING
        return IDENTITY_MISSING  # wave 9 item 2: --require-identity is on and this approval predates it
    if not (mode.required or mac):
        return _nonce_problem(row)
    if mode.key is None:
        return APPROVAL_KEY_UNAVAILABLE  # a MAC this process cannot verify, or keyed mode without the key
    if not has_mac_column:
        return BINDING_UNAVAILABLE
    if not mac:
        return MAC_MISSING
    expected = approval_mac(mode.key, row["id"], str(stored))
    if not hmac.compare_digest(str(mac).encode("utf-8"), expected.encode("utf-8")):
        return MAC_MISMATCH
    return _nonce_problem(row)


def _nonce_problem(row) -> str | None:
    """Wave 9 item 1, checked last so the older codes (hash, identity, key, MAC) keep their meaning:
    a row without the nonce column is unavailable; a row approved before the nonce existed (or with
    an edited one) must be re-approved once, never grandfathered."""
    from .ledger import NONCE_MISSING, is_nonce
    if "approval_nonce" not in row.keys():
        return BINDING_UNAVAILABLE
    return None if is_nonce(row["approval_nonce"]) else NONCE_MISSING


def binding_refusal(reason: str, post_id: str) -> str:
    """Operator-facing text for a binding reason code: what happened and the exact re-approval steps."""
    steps = (f"re-approve after review: reachweaver unapprove {post_id}; reachweaver show {post_id}; "
             f"reachweaver approve {post_id}")
    from .identity import IDENTITY_CODES, refusal_text
    if reason in IDENTITY_CODES:
        return refusal_text(reason, post_id)
    from .ledger import LEDGER_CODES
    from .ledger import refusal_text as ledger_refusal_text
    if reason in LEDGER_CODES:
        return ledger_refusal_text(reason, post_id)
    if reason == APPROVAL_EXPIRED:
        return (f"{reason}: post {post_id} was approved longer ago than the owner's approval time-to-live "
                f"(reachweaver approval-mode shows it); {steps}")
    if reason == BINDING_UNAVAILABLE:
        return (f"{reason}: database lacks approval hash columns; open it with storage.connect() "
                "so it is migrated")
    if reason == BINDING_MISSING:
        return f"{reason}: post {post_id} was approved before approval binding existed; {steps}"
    if reason == APPROVAL_KEY_UNAVAILABLE:
        return (f"{reason}: keyed approval mode is on but this process has no usable {APPROVAL_KEY_CREDENTIAL} "
                "(environment or LoadCredential); provide it to this process")
    if reason == MAC_MISSING:
        return f"{reason}: post {post_id} was approved without the owner approval key; {steps}"
    if reason == MAC_MISMATCH:
        return f"{reason}: post {post_id} has no valid approval tag for this key; {steps}"
    if reason == BINDING_MEDIA_CHANGED:
        return (f"{reason}: an attachment of post {post_id} (a local file, or the image at its hosted URL) is missing "
                f"or no longer holds the recorded bytes; restore the original file or image, or re-attach and "
                f"re-approve: reachweaver unapprove {post_id}; reachweaver media-detach {post_id}; reachweaver "
                f"media-attach {post_id} (--file PATH | --url URL --online) --alt TEXT; reachweaver show {post_id}; "
                f"reachweaver approve {post_id}")
    if reason == BINDING_VIDEO_CHANGED:
        return (f"{reason}: the video file of post {post_id} is missing or no longer holds the approved "
                f"bytes; restore that file, or re-attach and re-approve: reachweaver unapprove {post_id}; "
                f"reachweaver video-detach {post_id} (TikTok) or youtube-detach {post_id} (YouTube); "
                f"reachweaver video-attach / youtube-attach {post_id} --file PATH ...; "
                f"reachweaver show {post_id}; reachweaver approve {post_id}")
    return f"{reason}: post {post_id} is not exactly what was approved; {steps}"


def _record_binding_failure(con, row, reason: str, now: str) -> None:
    """Inside the caller's transaction: last_error plus one audit event per reason (deduplicated
    on last_error, so a worker timer cannot flood the log). Hash prefixes only, never content."""
    if reason in _CONFIG_REASONS or row["last_error"] == reason:
        return
    stored = row["approved_payload_sha256"]
    con.execute("UPDATE posts SET last_error=? WHERE id=?", (reason, row["id"]))
    from .ledger import REPLAY, REPLAY_EVENT
    event(con, REPLAY_EVENT if reason == REPLAY else "approval_binding_failed", row["id"],
          f"reason={reason} stored={stored[:12] if stored else 'none'} "
          f"current={approval_payload_sha256(row)[:12]}", at=now)


def record_binding_failure(con, post_id: str, reason: str, now: str | None = None) -> None:
    """Record a binding failure found outside ``claim_sending`` (a changed attachment file, hosted
    image or video file).

    Same effect as a refused claim: ``last_error`` plus one deduplicated audit event; the
    status is never changed and no budget is used.
    """
    now = parse_utc(now or utc_now())
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if row is not None and _has_binding_columns(row):
            _record_binding_failure(con, row, reason, now)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def set_post_media(con, post_id: str, media: list[dict], *, account: dict | None = None) -> list[dict]:
    """Replace a DRAFT's image attachments (#13, #20). Approved/scheduled posts must be unapproved first.

    Validates platform, source, count and alt text against the account's effective limits. The
    audit event carries the count and SHA-256 prefixes only, never alt text, paths or URLs.
    """
    from .attachments import check_media_list, encode_media
    row = get_post(con, post_id)
    if row["status"] != "draft":
        raise ValidationError(f"Attachments can only change on a draft (post {post_id} is {row['status']}); "
                              f"run 'reachweaver unapprove {post_id}' first")
    if "media_json" not in row.keys():
        raise ValidationError("database lacks the media_json column; open it with storage.connect() so it is migrated")
    if media and "video_json" in row.keys() and row["video_json"]:
        from .video import BOTH_KINDS
        raise ValidationError(BOTH_KINDS)
    if media and reply_target_text(row) is not None:
        raise ValidationError(REPLY_MEDIA_REFUSED)
    check_media_list(media, row["platform"], account)
    con.execute("UPDATE posts SET media_json=? WHERE id=? AND status='draft'", (encode_media(media), post_id))
    event(con, "media_set", post_id, f"count={len(media)}" + "".join(
        f" sha256_{i}={item['sha256'][:12]}" for i, item in enumerate(media, 1)))
    return media


def post_video(row) -> dict | None:
    """The row's video record (#22, #23; ``video.py``), or None (none, or no ``video_json`` column).

    A malformed stored value raises ValidationError: callers that send must fail closed.
    """
    if row is None or "video_json" not in row.keys() or row["video_json"] in (None, ""):
        return None
    from .video import decode_video
    return decode_video(row["video_json"])


def set_post_video(con, post_id: str, record: dict | None) -> dict | None:
    """Attach (or, with None, detach) the one video of a DRAFT; approval then binds it.

    The record's ``platform`` must equal the post's (a YouTube spec never lands on a TikTok post
    or the reverse), and a post with images cannot also carry a video. The audit event carries
    the SHA-256 prefix, size and mode only, never the path, title or tags.
    """
    row = get_post(con, post_id)
    if row["status"] != "draft":
        raise ValidationError(f"The video can only change on a draft (post {post_id} is {row['status']}); "
                              "unapprove it first (the approval binds the video)")
    if "video_json" not in row.keys():
        raise ValidationError("database lacks the video_json column; open it with storage.connect()")
    text = None
    if record is not None:
        from .video import BOTH_KINDS, VIDEO_PLATFORMS, encode_video, mode
        if row["platform"] not in VIDEO_PLATFORMS:
            raise ValidationError(f"video records are only supported for TikTok and YouTube drafts, not {row['platform']}")
        if not isinstance(record, dict) or record.get("platform") != row["platform"]:
            platform = record.get("platform") if isinstance(record, dict) else None
            raise ValidationError(f"video record is for {platform}, not this {row['platform']} post; "
                                  "attach a video built for the post's platform")
        if "media_json" in row.keys() and row["media_json"]:
            raise ValidationError(BOTH_KINDS)
        if reply_target_text(row) is not None:
            raise ValidationError(REPLY_MEDIA_REFUSED)
        text = encode_video(record)
    con.execute("UPDATE posts SET video_json=? WHERE id=? AND status='draft'", (text, post_id))
    if record is None:
        event(con, "video_detached", post_id, "")
    else:
        event(con, "video_attached", post_id,
              f"sha256={record['sha256'][:12]} bytes={record['bytes']} mode={mode(record)}")
    return record


def _ledger_problem(con, row) -> str | None:
    """Wave 9 item 1, read-only: ``approval_replay_reapproval_required`` when this approval's nonce is
    already consumed, ``approval_ledger_unavailable`` when the ledger cannot be trusted, else None.
    A broken chain is reported as unavailable here (the claim is refused either way); ``health``
    tells the two apart."""
    from . import ledger
    return ledger.check(con, row["id"], row["approval_nonce"])


def check_approval_binding(con, post_id: str, now: str | None = None, *,
                           identity_fingerprint: str | None = None) -> str | None:
    """Pre-claim, pre-network gate for one approved/scheduled post.

    Returns None when the binding holds (or the post is not approved/scheduled, which
    ``claim_sending`` handles); otherwise records the failure exactly as ``claim_sending``
    does and returns the reason code. Never changes ``status``. ``claim_sending`` re-checks
    under its own lock regardless; this gate only avoids a pointless identity preflight.
    ``identity_fingerprint`` (wave 9 item 2): ``identity.current_fingerprint(account)``, compared
    offline with the identity stored at approval, so a re-pointed account entry is refused before
    any request. A row approved with an identity and a caller that passes none fails closed.
    """
    now = parse_utc(now or utc_now())
    key, key_error = load_approval_key()
    con.execute("BEGIN IMMEDIATE")
    try:
        mode = ApprovalKeyMode(key=key, key_error=key_error, latched=keyed_mode_latched(con),
                               identity_required=identity_mode_required(con),
                               max_age_hours=approval_max_age_hours(con))
        if key is not None:
            latch_keyed_mode(con)  # D7: once a verifier holds the key, later keyless runs fail closed
        row = con.execute("SELECT * FROM posts WHERE id=? AND status IN ('approved','scheduled')",
                          (post_id,)).fetchone()
        problem = approval_binding_problem(row, mode, now=now) if row is not None else None
        if problem is None and row is not None:
            problem = approval_identity_problem(row, identity_fingerprint)
        if problem is None and row is not None:
            problem = _ledger_problem(con, row)  # wave 9 item 1: replay / ledger, before any request
        if problem:
            _record_binding_failure(con, row, problem, now)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return problem


def _approval_attribution(approver: str | None, channel: str | None) -> tuple[str | None, str]:
    if channel is None:
        channel = "library"
    if channel not in APPROVAL_CHANNELS:
        raise ValidationError("Unknown approval channel")
    if approver is not None and not APPROVER.fullmatch(approver):
        raise ValidationError("--approver must be 1-120 characters of letters, digits and . _ @ + -")
    return approver, channel


def _guarded_update(con, post_id: str, expected: str, command: str, sql: str, params: tuple) -> None:
    """Run a status-guarded UPDATE; 0 rows means the post moved since it was read, so refuse."""
    if con.execute(sql, params).rowcount != 1:
        raise ValidationError(f"Cannot {command} post {post_id}: it is no longer in state {expected}; nothing changed")


def transition(con, post_id, command: str, scheduled_at: str | None = None, *,
               approver: str | None = None, approval_channel: str | None = None,
               expected_sha256: str | None = None, confirmation: str | None = None,
               identity: str | None = None, nonce: str | None = None) -> str:
    """Local state change (#7): approve binds a payload hash; schedule verifies it and
    rebinds the new time (an unbound or changed post is refused, never laundered);
    unapprove/cancel clear it. See docs/SECURITY-REVIEW-PROPOSAL-7-11.md.

    Keyed mode (#7 D7): approve/schedule also write an HMAC tag and need the owner key;
    schedule verifies the old tag first. ``expected_sha256`` (approve only): the hash a human
    confirmed; approval is refused if the row no longer hashes to it. ``confirmation`` (approve
    only): audit label of the human-presence check that ran (D6); recorded, never hashed.
    ``identity`` (approve only; wave 9 item 2): the fingerprint ``identity.verify_identity_online``
    returned; stored in ``approved_identity`` and bound into the hash under ``approval/id-1``. With
    ``approval-mode --require-identity`` latched, approve without it is refused. Schedule keeps it;
    unapprove/cancel clear it. ``nonce`` (approve only; wave 9 item 1): the approval nonce, drawn
    fresh by default (``ledger.new_nonce``); ``expected_sha256`` is the content digest WITHOUT it.
    Every approve stores it in ``approval_nonce`` and binds it (``approval/2``); schedule keeps it;
    unapprove/cancel clear it. Approving needs a usable approval ledger (``ledger.ensure``)."""
    with _atomic(con):  # P3: the row is re-read and every UPDATE is status-guarded under one lock
        return _transition_locked(con, post_id, command, scheduled_at, approver=approver,
                                  approval_channel=approval_channel, expected_sha256=expected_sha256,
                                  confirmation=confirmation, identity=identity, nonce=nonce)


def _transition_locked(con, post_id, command, scheduled_at, *, approver, approval_channel,
                       expected_sha256, confirmation, identity, nonce) -> str:
    allowed = {
        "approve": {"draft": "approved"},
        "unapprove": {"approved": "draft", "scheduled": "draft"},
        "cancel": {"draft": "cancelled", "approved": "cancelled", "scheduled": "cancelled"},
        "schedule": {"approved": "scheduled", "scheduled": "scheduled"},
    }
    if command not in allowed:
        raise ValidationError("Unsupported transition")
    row = get_post(con, post_id)
    target = allowed[command].get(row["status"])
    if not target:
        raise ValidationError(f"Cannot {command} post in state {row['status']}")
    if command == "schedule" and not scheduled_at:
        raise ValidationError("Scheduling needs --at with a timezone")
    if command != "approve" and (approver is not None or approval_channel is not None
                                 or expected_sha256 is not None or confirmation is not None
                                 or identity is not None or nonce is not None):
        raise ValidationError("Approver attribution only applies to approve")
    values = (target, utc_now() if command == "approve" else row["approved_at"] if target == "scheduled" else None,
              scheduled_at if command == "schedule" else None)
    detail = f"status={target}" + (f" at={scheduled_at}" if scheduled_at else "")
    if not _has_binding_columns(row):
        # A caller-built table without the binding columns (never opened via connect()).
        # Granting or extending an approval there would be unbound, so fail closed; revoking
        # one (unapprove/cancel) is always safe and keeps working.
        if command in ("approve", "schedule"):
            raise ValidationError(binding_refusal(BINDING_UNAVAILABLE, post_id) + f". Nothing changed by {command}")
        _guarded_update(con, post_id, row["status"], command,
                        "UPDATE posts SET status=?, approved_at=?, scheduled_at=?, last_error=NULL WHERE id=? AND status=?",
                        (*values, post_id, row["status"]))
        event(con, command, post_id, detail)
        return target
    has_mac_column = all(name in row.keys() for name in APPROVAL_MAC_FIELDS)
    granting = command in ("approve", "schedule")
    mode = approval_key_mode(con) if granting else UNKEYED  # revoking never needs the key
    if granting and mode.required:
        # D7: granting or extending a keyed approval needs a usable key and the MAC column.
        if mode.key is None:
            raise ValidationError(binding_refusal(APPROVAL_KEY_UNAVAILABLE, post_id)
                                  + (f" ({mode.key_error})" if mode.key_error else "")
                                  + f". Nothing changed by {command}")
        if not has_mac_column:
            raise ValidationError(binding_refusal(BINDING_UNAVAILABLE, post_id) + f". Nothing changed by {command}")
    # Decide the binding BEFORE writing anything, so a refused schedule changes nothing.
    new_hash, by, channel = None, None, None
    if command == "approve":
        from .identity import IDENTITY_REQUIRED, is_fingerprint, refusal_text
        if identity is not None and not is_fingerprint(identity):
            raise ValidationError(refusal_text("approval_identity_unavailable", post_id) + ". Nothing approved")
        if mode.identity_required and identity is None:
            raise ValidationError(refusal_text(IDENTITY_REQUIRED, post_id))
        from . import ledger
        if "approval_nonce" not in row.keys():
            raise ValidationError(binding_refusal(BINDING_UNAVAILABLE, post_id) + ". Nothing approved")
        if nonce is None:
            nonce = ledger.new_nonce()
        elif not ledger.is_nonce(nonce):
            raise ValidationError(ledger.refusal_text(ledger.NONCE_MISSING, post_id) + ". Nothing approved")
        unusable = ledger.ensure(con)
        if unusable:
            raise ValidationError(ledger.refusal_text(unusable, post_id) + ". Nothing approved")
        by, channel = _approval_attribution(approver, approval_channel)
        # #13/#20: attachments must be well-formed (post_media raises) and every local file must
        # still hold the recorded bytes (those shown for review); otherwise the approval would bind
        # other bytes. A hosted image (#20) is re-downloaded by the live path, never here (offline).
        media = post_media(row)
        if reply_target_text(row) is not None and (media or ("video_json" in row.keys() and row["video_json"])):
            raise ValidationError(f"{REPLY_MEDIA_REFUSED}. Nothing approved")
        if media:
            from .attachments import media_problem
            if media_problem(media):
                raise ValidationError(binding_refusal(BINDING_MEDIA_CHANGED, post_id) + ". Nothing approved")
        # #22/#23: the one video record must be well-formed (post_video raises) and its file must
        # still hold the recorded bytes; images and a video together are never approved.
        video = post_video(row)
        if video is not None:
            if media:
                from .video import BOTH_KINDS
                raise ValidationError(f"{BOTH_KINDS}. Nothing approved")
            from .video import file_problem
            if file_problem(video):
                raise ValidationError(binding_refusal(BINDING_VIDEO_CHANGED, post_id) + ". Nothing approved")
        new_hash = approval_payload_sha256(row, scheduled_at=None, use_row_schedule=False, identity=identity,
                                           nonce=nonce)
        # What a human confirms (D6) is the content digest; the random per-approval nonce is not
        # human-reviewable, so ``expected_sha256`` is compared without it.
        shown = approval_payload_sha256(row, scheduled_at=None, use_row_schedule=False, identity=identity)
        if expected_sha256 is not None and not hmac.compare_digest(str(expected_sha256), shown):
            raise ValidationError(f"{BINDING_MISMATCH}: post {post_id} changed after it was shown for "
                                  "confirmation; nothing approved. Review it again with show")
    elif command == "schedule":
        by, channel = row["approved_by"], row["approval_channel"]
        # Verify before rebinding, otherwise a schedule would launder tampered content. An
        # unbound legacy approval is refused too: scheduling it would only queue a post the
        # claim must refuse, so the operator re-approves first (owner decision D2).
        problem = approval_binding_problem(row, mode)
        if problem:
            raise ValidationError(binding_refusal(problem, post_id) + ". Nothing scheduled")
        if mode.max_age_hours is not None and scheduled_at:
            lapse = datetime.fromisoformat(parse_utc(row["approved_at"])) + timedelta(hours=mode.max_age_hours)
            if datetime.fromisoformat(parse_utc(scheduled_at)) > lapse:
                raise ValidationError(f"{APPROVAL_EXPIRES_BEFORE_DUE}: post {post_id} was approved {row['approved_at']} "
                                      f"and approvals last {mode.max_age_hours} hours (until {lapse.isoformat(timespec='seconds')}); "
                                      "choose an earlier time or re-approve closer to the due time. Nothing scheduled")
        new_hash = approval_payload_sha256(row, scheduled_at=scheduled_at, use_row_schedule=False)
    new_mac = approval_mac(mode.key, post_id, new_hash) if new_hash and mode.key is not None else None
    # unapprove/cancel: new_hash/by/channel/mac stay None, clearing the binding.
    _guarded_update(con, post_id, row["status"], command,
                    """UPDATE posts SET status=?, approved_at=?, scheduled_at=?, last_error=NULL,
                   approved_payload_sha256=?, approved_by=?, approval_channel=? WHERE id=? AND status=?""",
                    (*values, new_hash, by, channel, post_id, row["status"]))
    if has_mac_column:
        con.execute("UPDATE posts SET approved_payload_mac=? WHERE id=? AND status=?", (new_mac, post_id, target))
    if "approved_identity" in row.keys() and command != "schedule":
        con.execute("UPDATE posts SET approved_identity=? WHERE id=? AND status=?",
                    (identity if command == "approve" else None, post_id, target))
    if "approval_nonce" in row.keys() and command != "schedule":
        con.execute("UPDATE posts SET approval_nonce=? WHERE id=? AND status=?",
                    (nonce if command == "approve" else None, post_id, target))
    if new_mac:
        latch_keyed_mode(con)
    if new_hash:
        detail += f" approval_sha256={new_hash[:12]}"
        if new_mac:
            detail += " keyed=hmac-sha256/1"
        if command == "approve":
            detail += f" approver={by or 'unattributed'} channel={channel}"
            if identity:
                detail += " identity=bound"
            if confirmation:
                detail += f" confirmation={confirmation}"
    event(con, command, post_id, detail)
    return target


def pending(con, limit=10, now: str | None = None, *,
            post_id: str | None = None, scheduled_only: bool = False,
            scheduled_not_before: str | None = None):
    """Query due work with optional safe scheduled-only worker mode.

    scheduled_not_before prevents stale missed slots from starving fresh work.
    """
    now = parse_utc(now or utc_now())
    if scheduled_not_before is not None and not scheduled_only:
        raise ValidationError("scheduled_not_before requires scheduled_only mode")
    if scheduled_only:
        query = """SELECT * FROM posts
                   WHERE status='scheduled' AND scheduled_at<=?"""
        params: list[str | int] = [now]
        if scheduled_not_before is not None:
            query += " AND scheduled_at>=?"
            params.append(parse_utc(scheduled_not_before))
    else:
        query = """SELECT * FROM posts WHERE
           ((status='approved' AND scheduled_at IS NULL)
            OR (status='scheduled' AND scheduled_at<=?))"""
        params = [now]
    if post_id is not None:
        query += " AND id=?"
        params.append(post_id)
    query += " ORDER BY COALESCE(scheduled_at,created_at),id LIMIT ?"
    params.append(limit)
    return con.execute(query, params).fetchall()


def _recent_send_claims(con, account_id: str, now: str) -> list[str]:
    """Attempt history across post statuses and restarts (including uncertain sends).

    Use persistent claim EVENTS rather than only current post status: reconciling an
    uncertain post to 'draft' must not erase a previous attempt from rate budgets.
    """
    current = datetime.fromisoformat(parse_utc(now))
    start = (current - timedelta(hours=24)).isoformat(timespec="seconds")
    rows = con.execute("""SELECT e.at FROM event_log e
        JOIN posts p ON p.id=e.post_id
        WHERE p.account_id=? AND e.action='send_claimed'
          AND e.at>? AND e.at<=? ORDER BY e.at DESC""",
        (account_id, start, current.isoformat(timespec="seconds"))).fetchall()
    return [row["at"] for row in rows]


def _valid_scopes(scopes) -> tuple[str, ...]:
    if isinstance(scopes, str) or not all(isinstance(s, str) and _COOLDOWN_SCOPE.fullmatch(s) for s in scopes):
        raise ValidationError("provider cooldown scopes must be scope codes such as postiz-workspace:<slug>")
    return tuple(scopes)


def record_provider_cooldown(con, account_id: str, until: str, source: str, *, stage: str,
                             post_id: str | None = None, send_class: str | None = None,
                             ceiling_clamped: bool = False, reason: str = "http_429",
                             scope: str | None = None, now: str | None = None) -> dict:
    """Append one ``provider_cooldown`` audit event (issue #11). Never touches ``posts``.

    Every field is a fixed code, an account id or a UTC time: no provider text is stored.
    Cooldowns only extend: the active deadline is the latest one recorded for the account.
    ``scope`` (#11): the account's shared group; every account in it then waits too.
    """
    for name, value in (("account_id", account_id), ("source", source), ("reason", reason)):
        if not isinstance(value, str) or not _COOLDOWN_TOKEN.fullmatch(value):
            raise ValidationError(f"provider cooldown {name} must be a short code")
    if scope is not None:
        _valid_scopes((scope,))
    if source not in _COOLDOWN_SOURCES:
        raise ValidationError("Unknown provider cooldown source")
    if stage not in COOLDOWN_STAGES:
        raise ValidationError("Unknown provider cooldown stage")
    if send_class is not None and send_class not in SEND_CLASSES:
        raise ValidationError("Unknown send classification")
    at = parse_utc(now or utc_now())
    until = parse_utc(until)
    detail = f"account={account_id} until={until} source={source} reason={reason} stage={stage}"
    if send_class:
        detail += f" class={send_class}"
    if ceiling_clamped:
        detail += " clamped=ceiling"
    if scope is not None:
        detail += f" scope={scope}"
    event(con, COOLDOWN_EVENT, post_id, detail, at=at)
    return {"account_id": account_id, "until": until, "source": source, "reason": reason, "stage": stage,
            "send_class": send_class, "ceiling_clamped": bool(ceiling_clamped), "recorded_at": at,
            "post_id": post_id, "scope": scope}


def _parse_cooldown(row) -> dict | None:
    fields = dict(token.split("=", 1) for token in str(row["detail"]).split() if "=" in token)
    account, until = fields.get("account"), fields.get("until")
    if not account or not until:
        return None
    try:
        until = parse_utc(until)
    except ValidationError:
        return None  # a malformed row can never extend a cooldown
    scope = fields.get("scope")
    if scope is not None and not _COOLDOWN_SCOPE.fullmatch(scope):
        scope = None  # a malformed scope never widens a cooldown; the account itself still waits
    return {"account_id": account, "until": until, "source": fields.get("source", "unknown"),
            "reason": fields.get("reason", "unknown"), "stage": fields.get("stage", "unknown"),
            "send_class": fields.get("class"), "ceiling_clamped": fields.get("clamped") == "ceiling",
            "recorded_at": row["at"], "post_id": row["post_id"], "scope": scope}


def _active_cooldown_items(con, now: str) -> list[dict]:
    current = datetime.fromisoformat(now)
    since = (current - timedelta(hours=_COOLDOWN_MAX_HOURS + 1)).isoformat(timespec="seconds")
    items = []
    for row in con.execute("SELECT at,post_id,detail FROM event_log WHERE action=? AND at>=? ORDER BY id",
                           (COOLDOWN_EVENT, since)).fetchall():
        item = _parse_cooldown(row)
        if item is not None and datetime.fromisoformat(item["until"]) > current:
            items.append(item)
    return items


def provider_cooldowns(con, now: str | None = None, *, account_id: str | None = None) -> list[dict]:
    """Active cooldowns (deadline after ``now``), one per recording account: the latest deadline wins.

    Read-only. A recorded deadline is at most 24 hours after its event, so only events from the
    last 24 hours (plus a margin) are read. ``scope`` is the latest deadline's group, if any;
    use :func:`active_provider_cooldown` to ask whether a given account must wait.
    """
    now = parse_utc(now or utc_now())
    active: dict[str, dict] = {}
    for item in _active_cooldown_items(con, now):
        if account_id is not None and item["account_id"] != account_id:
            continue
        best = active.get(item["account_id"])
        if best is None or item["until"] > best["until"]:
            if best is not None and best["ceiling_clamped"]:
                item["ceiling_clamped"] = True
            active[item["account_id"]] = item
        elif item["ceiling_clamped"]:
            best["ceiling_clamped"] = True
    return [active[key] for key in sorted(active)]


def sync_cooldown_scopes(con, declared: dict[str, tuple[str, ...]], now: str | None = None) -> None:
    """Record the shared cooldown scopes the loaded accounts declare (#11).

    ``declared`` maps every loaded account id to its ``ratelimit.cooldown_scopes``. Rows of
    those accounts are replaced in one transaction; rows of accounts not loaded are kept.
    Ids and scope codes only.
    """
    for account_id, scopes in declared.items():
        if not isinstance(account_id, str) or not _COOLDOWN_TOKEN.fullmatch(account_id):
            raise ValidationError("cooldown scope member must be an account id")
        _valid_scopes(scopes)
    at = parse_utc(now or utc_now())
    con.execute("BEGIN IMMEDIATE")
    try:
        for account_id, scopes in sorted(declared.items()):
            if scopes:  # fixed placeholders only; values are bound
                con.execute("DELETE FROM cooldown_scope_members WHERE account_id=? AND scope NOT IN "
                            f"({','.join('?' * len(scopes))})", (account_id, *scopes))
            else:
                con.execute("DELETE FROM cooldown_scope_members WHERE account_id=?", (account_id,))
            for scope in scopes:
                con.execute("INSERT OR IGNORE INTO cooldown_scope_members(account_id,scope,declared_at) "
                            "VALUES(?,?,?)", (account_id, scope, at))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def recorded_cooldown_scopes(con, account_id: str) -> tuple[str, ...]:
    """Scopes ``account_id`` declared at the last live run; () if none or on an older database."""
    try:
        rows = con.execute("SELECT scope FROM cooldown_scope_members WHERE account_id=? ORDER BY scope",
                           (account_id,)).fetchall()
    except sqlite3.OperationalError:  # a read-only connection to a database without the table
        return ()
    return tuple(row["scope"] for row in rows if _COOLDOWN_SCOPE.fullmatch(str(row["scope"])))


def active_provider_cooldown(con, account_id: str, now: str | None = None, *,
                             scopes: tuple[str, ...] = ()) -> dict | None:
    """The cooldown ``account_id`` must honour now, or None. Read-only.

    Considers cooldowns recorded for the account itself (any scope, and legacy rows without
    one) AND cooldowns recorded by any account for one of its scopes (#11): the caller's
    ``scopes`` united with :func:`recorded_cooldown_scopes`, so a stale or omitted argument
    can only add waiting. The latest deadline wins. A hit through a scope names the queried
    account in ``account_id`` and the account whose 429 caused it in ``recorded_by``.
    """
    wanted = set(_valid_scopes(scopes)) | set(recorded_cooldown_scopes(con, account_id))
    now = parse_utc(now or utc_now())
    best = None
    clamped = False
    for item in _active_cooldown_items(con, now):
        if item["account_id"] != account_id and item["scope"] not in wanted:
            continue
        clamped = clamped or item["ceiling_clamped"]
        if best is None or item["until"] > best["until"]:
            best = item
    if best is None:
        return None
    return {**best, "account_id": account_id, "recorded_by": best["account_id"], "ceiling_clamped": clamped}


def cooldown_refusal(cooldown: dict) -> str:
    text = (f"{COOLDOWN_REFUSAL} until={cooldown['until']} account={cooldown['account_id']} "
            f"source={cooldown['source']}")
    if cooldown.get("scope"):
        text += f" scope={cooldown['scope']}"
    recorded_by = cooldown.get("recorded_by")
    if recorded_by and recorded_by != cooldown["account_id"]:
        text += f" recorded_by={recorded_by}"
    return text


def _session_events(con, scope: str, since: str) -> list[str]:
    out = []
    for row in con.execute("SELECT at,detail FROM event_log WHERE action=? AND at>? ORDER BY id",
                           (SESSION_EVENT, since)).fetchall():
        fields = dict(token.split("=", 1) for token in str(row["detail"]).split() if "=" in token)
        if fields.get("scope") == scope:
            out.append(row["at"])
    return out


def _session_usage(con, scope: str, windows: list[tuple[int, int]], now: str) -> list[dict]:
    current = datetime.fromisoformat(now)
    longest = max(window for window, _ in windows)
    times = _session_events(con, scope, (current - timedelta(seconds=longest)).isoformat(timespec="seconds"))
    usage = []
    for window, limit in windows:
        start = (current - timedelta(seconds=window)).isoformat(timespec="seconds")
        usage.append({"window_seconds": window, "local_limit": limit,
                      "used": sum(1 for at in times if start < at <= now)})
    return usage


def _check_session_args(account_id: str, scope: str, windows) -> list[tuple[int, int]]:
    if not isinstance(account_id, str) or not _COOLDOWN_TOKEN.fullmatch(account_id):
        raise ValidationError("createSession counter account_id must be a short code")
    if not isinstance(scope, str) or not _SESSION_SCOPE.fullmatch(scope):
        raise ValidationError("createSession counter scope must be bsky-account:<16 hex>")
    pairs = list(windows)
    checked = [(w, n) for w, n in pairs if type(w) is int and type(n) is int and w > 0 and n >= 0]
    if not checked or len(checked) != len(pairs):
        raise ValidationError("createSession windows must be (seconds > 0, limit >= 0) pairs")
    return checked


def recorded_session_scope(con, account_id: str) -> str | None:
    """The createSession counter key last recorded for ``account_id`` (dry-run preview), or None."""
    for row in con.execute("SELECT detail FROM event_log WHERE action=? AND instr(detail,?)=1 "
                           "ORDER BY id DESC LIMIT 1", (SESSION_EVENT, f"account={account_id} ")).fetchall():
        fields = dict(token.split("=", 1) for token in str(row["detail"]).split() if "=" in token)
        scope = fields.get("scope")
        if fields.get("account") == account_id and scope and _SESSION_SCOPE.fullmatch(scope):
            return scope
    return None


def create_session_usage(con, account_id: str, scope: str, windows, now: str | None = None) -> list[dict]:
    """Read-only: reserved createSession calls per window for one Bluesky account scope."""
    checked = _check_session_args(account_id, scope, windows)
    return _session_usage(con, scope, checked, parse_utc(now or utc_now()))


def reserve_create_sessions(con, account_id: str, scope: str, *, windows, needed: int, stage: str,
                            now: str | None = None) -> dict:
    """Atomically check and record ``needed`` Bluesky createSession calls (#11).

    Under ``BEGIN IMMEDIATE``: when any window's ``used + needed`` would exceed its local
    limit, one ``bluesky_create_session_refused`` event is committed and ``ValidationError``
    (``bluesky_create_session_budget_exhausted ...``) is raised before any request. Otherwise
    ``needed`` ``bluesky_create_session`` events are committed: the first carries ``stage``,
    the rest ``send_reserved``. Ids, codes and times only.
    """
    checked = _check_session_args(account_id, scope, windows)
    if stage not in SESSION_STAGES:
        raise ValidationError("Unknown createSession stage")
    if type(needed) is not int or not 1 <= needed <= 4:
        raise ValidationError("createSession reservation must be 1..4 calls")
    now = parse_utc(now or utc_now())
    refused = None
    con.execute("BEGIN IMMEDIATE")
    try:
        usage = _session_usage(con, scope, checked, now)
        over = [u for u in usage if u["used"] + needed > u["local_limit"]]
        if over:
            first = over[0]
            refused = (f"{SESSION_REFUSAL} account={account_id} window={first['window_seconds']}s "
                       f"used={first['used']} needed={needed} local_limit={first['local_limit']}")
            event(con, SESSION_REFUSED_EVENT, None, f"account={account_id} scope={scope} stage={stage} "
                  f"window={first['window_seconds']} used={first['used']} needed={needed} "
                  f"limit={first['local_limit']}", at=now)
        else:
            for index in range(needed):
                event(con, SESSION_EVENT, None, f"account={account_id} scope={scope} "
                      f"stage={stage if index == 0 else 'send_reserved'}", at=now)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if refused:
        raise ValidationError(refused + "; no createSession request sent")
    return {"account_id": account_id, "scope": scope, "reserved": needed, "stage": stage,
            "usage": [{**u, "used": u["used"] + needed} for u in usage]}


def _register_send_claim(row, max_per_24h, min_gap_minutes) -> None:
    """Tell the send gate (``sendgate``) this post is claimed, so ``publishers.publish`` accepts it once."""
    from . import sendgate
    try:
        media = post_media(row)
    except ValidationError:
        media = [{"sha256": "unreadable"}]  # never matches what a sender passes: fails closed at the gate
    sendgate.register_claim(row["id"], account_id=row["account_id"], platform=row["platform"],
                            content=row["content"], media=media, max_per_24h=max_per_24h,
                            min_gap_minutes=min_gap_minutes)


def claim_sending(con, post_id: str, now: str | None = None, *,
                  max_per_24h: int | None = None, min_gap_minutes: int = 0,
                  expected_sha256: str | None = None, cooldown_scopes: tuple[str, ...] = (),
                  identity_fingerprint: str | None = None) -> bool:
    """Atomic claim + optional per-account rolling safety budget.

    The CLI live sender supplies safe defaults; legacy direct callers can omit the
    caps for backwards compatibility. The budget is based on ALL durable attempt
    events, even ambiguous sends and later reconciliations.

    Provider cooldown (#11): while ``active_provider_cooldown`` finds a cooldown for the post's
    account, or (#11) for one of the caller's ``cooldown_scopes`` (the sending account's
    ``ratelimit.cooldown_scopes``), the claim raises ``account_provider_cooldown_active`` and the
    post stays approved/scheduled.

    Approval binding (#7): an eligible post whose approval hash is missing or no longer
    matches the row stays approved/scheduled, gets one ``approval_binding_failed`` event and
    raises ValidationError; nothing is claimed and no budget is consumed. A sender passes
    ``expected_sha256`` = ``approval_payload_sha256`` of the row it is about to send, so a
    copy read before the lock cannot differ from the bound row that is claimed.

    Identity binding (wave 9 item 2): a row approved with ``--verify-identity`` also needs the
    sender's ``identity_fingerprint`` (``identity.current_fingerprint(account)``) to equal the stored
    one; a mismatch, or no fingerprint, is ``approval_identity_mismatch_reapproval_required`` and the
    post stays approved. With ``approval-mode --require-identity`` latched, a row without a stored
    identity is ``approval_identity_missing_reapproval_required``.
    """
    if max_per_24h is not None and (type(max_per_24h) is not int or not 1 <= max_per_24h <= 10):
        raise ValidationError("max_per_24h must be 1..10")
    if type(min_gap_minutes) is not int or not 0 <= min_gap_minutes <= 1440:
        raise ValidationError("min_gap_minutes must be 0..1440")
    scopes = _valid_scopes(cooldown_scopes)
    now = parse_utc(now or utc_now())
    now_dt = datetime.fromisoformat(now)
    binding_failure = None
    key, key_error = load_approval_key()  # #7 D7: read before the lock; never logged
    con.execute("BEGIN IMMEDIATE")
    try:
        setting = con.execute("SELECT value FROM settings WHERE name='paused'").fetchone()
        if setting is not None and setting["value"] == "1":
            raise ValidationError("Global emergency pause is active. Run 'resume' deliberately.")
        row = con.execute("""SELECT * FROM posts WHERE id=?
              AND ((status='approved' AND scheduled_at IS NULL)
                   OR (status='scheduled' AND scheduled_at<=?))""",
                          (post_id, now)).fetchone()
        if row is None:
            con.execute("COMMIT")
            return False
        # #7: the row must still be exactly what a human approved. Checked under the same
        # lock as the status transition, before budgets, before any network use.
        mode = ApprovalKeyMode(key=key, key_error=key_error, latched=keyed_mode_latched(con),
                               identity_required=identity_mode_required(con),
                               max_age_hours=approval_max_age_hours(con))
        binding_failure = approval_binding_problem(row, mode, now=now)
        if binding_failure in _CONFIG_REASONS:
            raise ValidationError(binding_refusal(binding_failure, post_id) + ". No social post sent")
        if binding_failure is None and expected_sha256 is not None and not hmac.compare_digest(
                str(expected_sha256), str(row["approved_payload_sha256"])):
            binding_failure = BINDING_MISMATCH  # the caller's copy is not the bound payload
        if binding_failure is None:
            binding_failure = approval_identity_problem(row, identity_fingerprint)
        if binding_failure is None:
            binding_failure = _ledger_problem(con, row)  # wave 9 item 1: before cooldowns, budgets, any request
            if binding_failure in _CONFIG_REASONS:
                raise ValidationError(binding_refusal(binding_failure, post_id) + ". No social post sent")
        if binding_failure:
            # Post stays approved/scheduled; no send_claimed, so no budget is used.
            _record_binding_failure(con, row, binding_failure, now)
            con.execute("COMMIT")
        else:
            # #11: a provider cooldown on this account (after a 429) refuses the claim. Checked
            # after the pause and the approval hash, before the budgets; nothing is written.
            cooldown = active_provider_cooldown(con, row["account_id"], now, scopes=scopes)
            if cooldown:
                raise ValidationError(cooldown_refusal(cooldown) + "; post stays approved/scheduled")
            if max_per_24h is not None or min_gap_minutes:
                attempts = _recent_send_claims(con, row["account_id"], now)
                if max_per_24h is not None and len(attempts) >= max_per_24h:
                    raise ValidationError("account_rolling_24h_send_attempt_cap_reached")
                if min_gap_minutes and attempts:
                    last = datetime.fromisoformat(parse_utc(attempts[0]))
                    if now_dt - last < timedelta(minutes=min_gap_minutes):
                        raise ValidationError("account_minimum_send_attempt_gap_not_elapsed")
            # Wave 9 item 1: consume the approval in the append-only ledger BEFORE the status flip, under
            # this same write lock. A crash between the two leaves a consumed nonce on an approved row:
            # it fails closed (health lists it; recovery is a human unapprove + approve).
            from . import ledger
            consumed = ledger.consume(con, post_id, row["approval_nonce"], row["approved_payload_sha256"], now)
            if consumed:
                raise ValidationError(binding_refusal(consumed, post_id) + ". No social post sent")
            updated = con.execute("""UPDATE posts SET status='sending',send_started_at=?,last_error=NULL
                WHERE id=? AND ((status='approved' AND scheduled_at IS NULL)
                          OR (status='scheduled' AND scheduled_at<=?))""",
                          (now, post_id, now)).rowcount
            if updated:
                event(con, "send_claimed", post_id, "API request about to start", at=now)
            con.execute("COMMIT")
            if updated == 1:
                _register_send_claim(row, max_per_24h, min_gap_minutes)
            return updated == 1
    except Exception:
        con.execute("ROLLBACK")
        raise
    # Only reached after a committed binding failure (the audit event must survive).
    raise ValidationError(binding_refusal(binding_failure, post_id) + ". No social post sent")


def offline_key_mode(con) -> ApprovalKeyMode:
    """Keyed-mode state WITHOUT reading the key (dry runs, show, ops-metrics read no credential)."""
    return ApprovalKeyMode(latched=keyed_mode_latched(con), identity_required=identity_mode_required(con),
                           max_age_hours=approval_max_age_hours(con))


def approval_rebind_required(con, mode: ApprovalKeyMode | None = None) -> list[dict]:
    """Approved/scheduled posts that no claim will accept until a human re-approves them.

    Read-only (#7, owner decision D10): reason codes and ids only, never content. ``mode``: the
    keyed-binding state (``approval_key_mode``) when the caller may read the key; None reads no
    credential, so keyed tags are not verified. ``approval_key_unavailable`` is a process problem,
    reported by ``queue_health``'s ``approval_keyed_binding`` block, not per post.
    """
    out = []
    mode = offline_key_mode(con) if mode is None else mode
    for row in con.execute("""SELECT * FROM posts WHERE status IN ('approved','scheduled')
                              ORDER BY account_id,id""").fetchall():
        problem = approval_binding_problem(row, mode)
        if problem and problem != APPROVAL_KEY_UNAVAILABLE:
            out.append({"post_id": row["id"], "account_id": row["account_id"], "platform": row["platform"],
                        "status": row["status"], "reason": problem})
    return out


def queue_health(con, now: str | None = None, *, stale_minutes: int = 30,
                 key_mode: ApprovalKeyMode | None = None) -> dict:
    """Local-only queue diagnostic; no remote calls, retries or state mutations.

    ``key_mode`` (#7 D7): pass ``approval_key_mode(con)`` to verify keyed tags; None reads
    no credential and reports ``key_available`` as None (not checked).
    """
    if type(stale_minutes) is not int or not 5 <= stale_minutes <= 10080:
        raise ValidationError("stale_minutes must be 5..10080")
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    counts = {r["status"]: r["count"] for r in con.execute(
        "SELECT status,COUNT(*) AS count FROM posts GROUP BY status")}
    overdue, ambiguous, stale = [], [], []
    relevant = con.execute("""SELECT id,account_id,platform,status,scheduled_at,send_started_at
                              FROM posts WHERE status IN ('scheduled','sending','needs_review')
                              ORDER BY account_id,id""").fetchall()
    for row in relevant:
        post = {"post_id": row["id"], "account_id": row["account_id"],
                "platform": row["platform"], "status": row["status"]}
        if row["status"] == "needs_review":
            ambiguous.append(post)
        elif row["status"] == "scheduled":
            if row["scheduled_at"]:
                try:
                    if datetime.fromisoformat(parse_utc(row["scheduled_at"])) <= current:
                        overdue.append(post)
                except (ValueError, TypeError, ValidationError):
                    overdue.append({**post, "reason": "invalid_due_time"})
            else:
                overdue.append({**post, "reason": "missing_due_time"})
        elif row["status"] == "sending":
            if not row["send_started_at"]:
                stale.append({**post, "reason": "missing_send_started_at"})
                continue
            try:
                started = datetime.fromisoformat(parse_utc(row["send_started_at"]))
                if current - started >= timedelta(minutes=stale_minutes):
                    stale.append(post)
            except (ValueError, TypeError, ValidationError):
                stale.append({**post, "reason": "invalid_send_started_at"})
    start = (current - timedelta(hours=24)).isoformat(timespec="seconds")
    attempts = {row["account_id"]: row["count"] for row in con.execute(
        """SELECT p.account_id, COUNT(*) AS count FROM event_log e
        JOIN posts p ON p.id=e.post_id WHERE e.action='send_claimed'
        AND e.at>? AND e.at<=? GROUP BY p.account_id""",
        (start, now))}
    checked = key_mode is not None
    key_mode = offline_key_mode(con) if key_mode is None else key_mode
    rebind = approval_rebind_required(con, key_mode)
    cooldowns = provider_cooldowns(con, now)
    from . import ledger
    ledger_view = ledger.audit(con)  # wave 9 item 1: read-only, post ids and fixed codes only
    key_missing = checked and key_mode.required and key_mode.key is None
    keyed = {"required": key_mode.required, "latched": key_mode.latched,
             "key_available": (key_mode.key is not None) if checked else None,
             "key_error": key_mode.key_error}  # variable names only, never a value
    return {"observed_at": now, "paused": is_paused(con),
            "status_counts": counts, "send_attempts_rolling_24h_by_account": attempts,
            "overdue_scheduled": overdue, "ambiguous_needs_review": ambiguous,
            "stale_sending": stale, "approval_rebind_required": rebind,
            "provider_cooldowns": cooldowns, "approval_keyed_binding": keyed,
            "approval_ledger": ledger_view,
            "attention_required": bool(overdue or ambiguous or stale or rebind or cooldowns
                                       or key_missing or ledger_view["problems"]),
            "note": "LOCAL STATE ONLY: no API status check performed. Never auto-retry sending/needs_review posts."}


# Read-only helpers for ops_metrics (issue #26). SELECT only; nothing here writes or retries.
def post_status_counts(con) -> list[dict]:
    """Post counts per (account_id, platform, status). Ids and codes only, never content."""
    return [dict(row) for row in con.execute(
        """SELECT account_id, platform, status, COUNT(*) AS count FROM posts
           GROUP BY account_id, platform, status ORDER BY account_id, platform, status""")]


def queued_posts(con) -> list[sqlite3.Row]:
    """Approved and scheduled rows (full rows: the caller may check the text, never emit it)."""
    return con.execute("""SELECT * FROM posts WHERE status IN ('approved','scheduled')
                          ORDER BY account_id, id""").fetchall()


def posting_history(con, account_ids: list[str], statuses: tuple[str, ...], *, since: str,
                    limit: int) -> list[dict]:
    """Posts of ``account_ids`` in ``statuses`` whose ``activity_at`` is at or after ``since`` (SELECT only).

    ``activity_at`` is the latest ``published`` audit event, else ``scheduled_at``, else
    ``created_at`` (all stored as UTC ISO 8601, so string comparison orders them). Future
    scheduled posts are included. Newest first, at most ``limit`` rows. Rows carry the post
    text for the caller's similarity check; callers must never emit it.
    """
    if not account_ids or not statuses:
        return []
    activity = ("COALESCE((SELECT MAX(e.at) FROM event_log e WHERE e.post_id=p.id AND e.action='published'),"
                " p.scheduled_at, p.created_at)")
    query = (f"""SELECT * FROM (SELECT p.id, p.account_id, p.platform, p.status, p.campaign, p.content,
                                       {activity} AS activity_at FROM posts p
                                WHERE p.account_id IN ({','.join('?' for _ in account_ids)})
                                  AND p.status IN ({','.join('?' for _ in statuses)}))
                 WHERE activity_at>=? ORDER BY activity_at DESC, id LIMIT ?""")
    params = [*account_ids, *statuses, parse_utc(since), int(limit)]
    return [dict(row) for row in con.execute(query, params)]


def event_activity(con, actions: tuple[str, ...], *, since: str | None = None,
                   until: str | None = None) -> list[dict]:
    """Audit events whose action is in ``actions``, with ``since <= at <= until`` when given.

    Each row carries the post's ``account_id``/``platform`` (None when the event has no post
    or its post was purged). ``detail`` is returned as stored: it can hold provider text
    (``needs_review`` reasons), so a caller may parse fixed tokens from it but must never emit it.
    """
    if not actions:
        return []
    query = (f"""SELECT e.action, e.at, e.detail, p.account_id, p.platform FROM event_log e
                 LEFT JOIN posts p ON p.id=e.post_id
                 WHERE e.action IN ({','.join('?' for _ in actions)})""")
    params: list[str] = list(actions)
    if since is not None:
        query += " AND e.at>=?"
        params.append(parse_utc(since))
    if until is not None:
        query += " AND e.at<=?"
        params.append(parse_utc(until))
    return [dict(row) for row in con.execute(query + " ORDER BY e.id", params)]


def needs_review_details(con) -> dict[str, str]:
    """``post_id -> detail`` of the latest ``needs_review`` event of each post now in needs_review.

    The detail may hold provider text; callers parse the trailing ``class=`` token only.
    """
    latest: dict[str, str] = {}
    for row in con.execute("""SELECT e.post_id, e.detail FROM event_log e JOIN posts p ON p.id=e.post_id
                              WHERE e.action='needs_review' AND p.status='needs_review' ORDER BY e.id"""):
        latest[row["post_id"]] = row["detail"]
    return latest

STATE_CONFLICT = "state_conflict"
# P2: a post claimed less than this long ago may still have a send in flight; reconcile refuses it.
RECONCILE_MIN_SENDING_MINUTES = 15


def _state_conflict(con, post_id: str, intended: str, detail: str) -> bool:
    """Record that a send-outcome write found the post no longer ``sending``; never claims success."""
    row = con.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()
    current = row["status"] if row is not None else "missing"
    event(con, STATE_CONFLICT, post_id, f"intended={intended} current_status={current} {detail}")
    return False


def finish_send(con, post_id: str, remote_id: str, remote_url: str | None, receipt: dict | None = None) -> bool:
    """``receipt``: adapter-sanitized extra fields (e.g. Bluesky cid) kept in the audit event.

    Returns True when the post moved ``sending`` -> ``published``. Returns False (P1) when it was
    no longer ``sending``: nothing but a ``state_conflict`` event (carrying the remote id) is written,
    and the caller must tell a human to review, never report PUBLISHED.
    """
    extra = "".join(f" {k}={v}" for k, v in sorted((receipt or {}).items())
                    if re.fullmatch(r"[a-z_]{1,32}", k) and re.fullmatch(r"[A-Za-z0-9._:~-]{1,256}", str(v)))
    with _atomic(con):
        cur = con.execute("UPDATE posts SET status='published',remote_id=?,remote_url=?,last_error=NULL WHERE id=? AND status='sending'",
                          (remote_id, remote_url, post_id))
        if cur.rowcount != 1:
            return _state_conflict(con, post_id, "published", f"remote_id={remote_id}{extra}")
        event(con, "published", post_id, f"remote_id={remote_id}{extra}")
    return True


def uncertain_send(con, post_id: str, error_code: str, *, send_class: str | None = None) -> bool:
    """Never automatically retry: request could have succeeded remotely.

    ``send_class`` (issue #11, D11) is the C1/C2/C3 label from ``ratelimit.classify_send_failure``.
    It is appended to the audit event for ``dead-letter`` guidance and never changes state.
    Returns False (P1) when the post was no longer ``sending``: a ``state_conflict`` event carries
    the error code instead of a ``needs_review`` event.
    """
    if send_class is not None and send_class not in SEND_CLASSES:
        raise ValidationError("Unknown send classification")
    tail = f" class={send_class}" if send_class else ""
    with _atomic(con):
        cur = con.execute("UPDATE posts SET status='needs_review',last_error=? WHERE id=? AND status='sending'",
                          (error_code, post_id))
        if cur.rowcount != 1:
            return _state_conflict(con, post_id, "needs_review", f"error_code={error_code}{tail}")
        event(con, "needs_review", post_id, f"reason={error_code}{tail}")
    return True


def reconcile(con, post_id, published: bool, remote_id="", remote_url="", *, force: bool = False,
              now: str | None = None):
    """Human-confirm the outcome of an uncertain send.

    P2: runs under ``BEGIN IMMEDIATE`` with a status-guarded UPDATE. A ``sending`` post whose
    ``send_started_at`` is newer than ``RECONCILE_MIN_SENDING_MINUTES`` (or missing/unreadable) may
    still have a request in flight, so it is refused unless ``force``. ``needs_review`` posts are
    never subject to the age check.
    """
    if published and not remote_id:
        raise ValidationError("Reconciliation as published requires remote id")
    with _atomic(con):
        row = get_post(con, post_id)
        if row["status"] not in ("sending", "needs_review"):
            raise ValidationError("Reconciliation only accepts sending/needs_review posts")
        forced = False
        if row["status"] == "sending":
            try:
                age = (datetime.fromisoformat(parse_utc(now or utc_now()))
                       - datetime.fromisoformat(parse_utc(row["send_started_at"])))
                fresh = age < timedelta(minutes=RECONCILE_MIN_SENDING_MINUTES)
            except (ValidationError, ValueError, TypeError, AttributeError, OverflowError):
                fresh = True  # fail closed: an unreadable claim time counts as fresh
            if fresh:
                if not force:
                    raise ValidationError(
                        f"Post {post_id} was claimed for sending less than {RECONCILE_MIN_SENDING_MINUTES} "
                        "minutes ago (or its claim time is unreadable); a send may still be in flight. "
                        "Wait, or re-run with --force after checking the native account. Nothing changed")
                forced = True
        _guarded_update(con, post_id, row["status"], "reconcile",
                        "UPDATE posts SET status=?,remote_id=?,remote_url=?,last_error=NULL,scheduled_at=NULL,approved_at=? WHERE id=? AND status=?",
                        ("published" if published else "draft", remote_id if published else None,
                         remote_url if published else None, row["approved_at"] if published else None,
                         post_id, row["status"]))
        event(con, "reconciled_published" if published else "reconciled_not_published", post_id,
              "Human manually verified remote status" + (" forced=1" if forced else ""))


def set_paused(con, paused: bool):
    with _atomic(con):  # P5: the flag and its audit event commit together
        con.execute("INSERT INTO settings(name,value) VALUES('paused',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    ("1" if paused else "0",))
        event(con, "emergency_paused" if paused else "resumed")


def is_paused(con) -> bool:
    row = con.execute("SELECT value FROM settings WHERE name='paused'").fetchone()
    return row is not None and row["value"] == "1"


def observation_window(observed_at, window_start=None, window_end=None) -> tuple[str | None, str | None]:
    """Validate an optional snapshot window; both bounds or neither, start <= end <= observed_at."""
    if window_start in (None, "") and window_end in (None, ""):
        return None, None
    if window_start in (None, "") or window_end in (None, ""):
        raise ValidationError("Give both window_start and window_end, or neither (unknown window)")
    start, end = parse_utc(window_start), parse_utc(window_end)
    if datetime.fromisoformat(start) > datetime.fromisoformat(end):
        raise ValidationError("window_start must not be after window_end")
    if datetime.fromisoformat(end) > datetime.fromisoformat(parse_utc(observed_at)):
        raise ValidationError("window_end must not be after observed_at")
    return start, end


def parse_metric_value(raw) -> tuple[int | None, dict | None]:
    """One metric value as ``(exact, status)``; never coerces a non-numeric token to 0.

    ``int`` or decimal digits -> exact; ``None``, ``""`` or ``unknown`` -> unknown (both None);
    ``suppressed``/``not_available``/``delayed`` -> ``{"status": token}``; ``estimated:N`` ->
    ``{"status": "estimated", "value": N}``. Tokens are case-insensitive; anything else raises.
    """
    if raw is None:
        return None, None
    if type(raw) is int:
        if raw < 0:
            raise ValidationError(f"must be {METRIC_VALUE_HELP}")
        return raw, None
    if not isinstance(raw, str):
        raise ValidationError(f"must be {METRIC_VALUE_HELP}")
    text = raw.strip().lower()
    if text in ("", "unknown"):
        return None, None
    if _EXACT.fullmatch(text):
        return int(text), None
    if text in METRIC_STATUSES and text != "estimated":
        return None, {"status": text}
    estimated = _ESTIMATED.fullmatch(text)
    if estimated:
        return None, {"status": "estimated", "value": int(estimated.group(1))}
    raise ValidationError(f"value {raw[:40]!r} is not valid; use {METRIC_VALUE_HELP}")


def decode_metric_status(text) -> dict[str, dict]:
    """Parse a stored ``metric_status`` column; NULL/empty -> ``{}``. Malformed raises."""
    if text in (None, ""):
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValidationError("metric_status is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("metric_status must be a JSON object")
    for key, entry in data.items():
        if key not in METRIC_FIELDS or not isinstance(entry, dict) or entry.get("status") not in METRIC_STATUSES:
            raise ValidationError(f"metric_status.{str(key)[:40]}: unknown metric or status")
        value = entry.get("value")
        wants_value = entry["status"] == "estimated"
        if set(entry) != ({"status", "value"} if wants_value else {"status"}) or (
                wants_value and (type(value) is not int or value < 0)):
            raise ValidationError(f"metric_status.{key}: only estimated carries a nonnegative integer value")
    return data


def metric_states(snapshot) -> dict[str, tuple[str, int | None]]:
    """``{metric: (label, value)}`` for one snapshot row (or None); labels: METRIC_STATUS_LABELS.

    The integer column is authoritative for exact values. A row without the ``metric_status``
    column (a caller connection that skipped migrate()) or with a malformed one degrades to
    unknown for every non-exact metric: never to zero, never to a guessed status.
    """
    statuses: dict[str, dict] = {}
    if snapshot is not None and "metric_status" in snapshot.keys():
        try:
            statuses = decode_metric_status(snapshot["metric_status"])
        except ValidationError:
            statuses = {}
    states: dict[str, tuple[str, int | None]] = {}
    for key in METRIC_FIELDS:
        exact = snapshot[key] if snapshot is not None else None
        if exact is not None:
            states[key] = ("exact", exact)
        elif key in statuses:
            states[key] = (statuses[key]["status"], statuses[key].get("value"))
        else:
            states[key] = ("unknown", None)
    return states


def store_metrics(con, post_id, observed_at, source, *, window_start=None, window_end=None, **values) -> None:
    """Upsert one snapshot. ``window_start``/``window_end`` are optional (None = unknown window).

    Each metric value is anything ``parse_metric_value`` accepts. Exact counts go to their
    integer column; other statuses go to ``metric_status``. A re-import of the same
    (post, observed_at, source) replaces the whole snapshot, statuses included.
    """
    get_post(con, post_id)
    if not source or len(source) > 100:
        raise ValidationError("Metric source label required (e.g. 'native-export')")
    exact: dict[str, int | None] = {}
    statuses: dict[str, dict] = {}
    for key, value in values.items():
        if key not in METRIC_FIELDS:
            raise ValidationError(f"Unknown metric {str(key)[:40]}; use {', '.join(METRIC_FIELDS)}")
        try:
            exact[key], status = parse_metric_value(value)
        except ValidationError as exc:
            raise ValidationError(f"Metric {key}: {exc}") from None
        if status is not None:
            statuses[key] = status
    if not any(value is not None for value in exact.values()) and not statuses:
        raise ValidationError("At least one metric required (a count or a status such as suppressed); "
                              "missing is different from zero")
    window = observation_window(observed_at, window_start, window_end)
    status_json = json.dumps(statuses, sort_keys=True, separators=(",", ":")) if statuses else None
    fields = METRIC_FIELDS + WINDOW_FIELDS + STATUS_FIELDS
    cols = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    con.execute(f"""INSERT INTO metric_snapshots(post_id,observed_at,source,{cols})
      VALUES(?,?,?,{placeholders})
      ON CONFLICT(post_id,observed_at,source) DO UPDATE SET
      {','.join(f'{field}=excluded.{field}' for field in fields)}""",
      (post_id, observed_at, source, *(exact.get(f) for f in METRIC_FIELDS), *window, status_json))
    detail = f"source={source} observed_at={observed_at}"
    if window[0]:
        detail += f" window={window[0]}..{window[1]}"
    if statuses:
        detail += " statuses=" + ",".join(f"{k}:{statuses[k]['status']}" for k in sorted(statuses))
    event(con, "metrics_recorded",post_id,detail)


# --- Read helpers (read-only: no writes, no network) -----------------------------------------

def list_posts(con, *, status: str | None = None, account_id: str | None = None,
               platform: str | None = None, campaign: str | None = None) -> list[sqlite3.Row]:
    """Posts matching every given filter, ordered by ``send_started_at, id`` (NULL first)."""
    query, params, clauses = "SELECT * FROM posts", [], []
    for column, value in (("status", status), ("account_id", account_id), ("platform", platform),
                          ("campaign", campaign)):
        if value is not None:  # column names are fixed above, never from input
            clauses.append(f"{column}=?")
            params.append(value)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    return con.execute(query + " ORDER BY send_started_at,id", params).fetchall()


def published_posts(con, *, account_id: str | None = None, platform: str | None = None,
                    campaign: str | None = None, include_replies: bool = True) -> list[sqlite3.Row]:
    """Published posts: ``list_posts`` with ``status='published'``; ``include_replies=False``
    leaves out reply rows (issue #17), which ``report`` counts separately (``reply_report``)."""
    rows = list_posts(con, status="published", account_id=account_id, platform=platform, campaign=campaign)
    return rows if include_replies else [row for row in rows if reply_target_text(row) is None]


def has_reply_column(con) -> bool:
    return any(row[1] == "reply_target" for row in con.execute("PRAGMA table_info(posts)"))


def reply_status_counts(con) -> list[dict]:
    """Reply posts (issue #17) per (account_id, platform, status); ids and codes only.
    A table without the column (never migrated) holds no replies."""
    if not has_reply_column(con):
        return []
    return [dict(row) for row in con.execute(
        """SELECT account_id, platform, status, COUNT(*) AS count FROM posts
           WHERE reply_target IS NOT NULL AND reply_target<>''
           GROUP BY account_id, platform, status ORDER BY account_id, platform, status""")]


def reply_report(con) -> dict[str, dict]:
    """Per-platform reply counts by status, kept apart from ``report``'s post totals."""
    out: dict[str, dict] = {}
    for row in reply_status_counts(con):
        item = out.setdefault(row["platform"], {"published": 0, "by_status": {}})
        item["by_status"][row["status"]] = item["by_status"].get(row["status"], 0) + row["count"]
        if row["status"] == "published":
            item["published"] += row["count"]
    return dict(sorted(out.items()))


def snapshots_for(con, post_id: str, *, source: str | None = None) -> list[sqlite3.Row]:
    """Every stored snapshot of one post (optionally one exact source label), in storage order."""
    if source is None:
        return con.execute("SELECT * FROM metric_snapshots WHERE post_id=? ORDER BY id", (post_id,)).fetchall()
    return con.execute("SELECT * FROM metric_snapshots WHERE post_id=? AND source=? ORDER BY id",
                       (post_id, source)).fetchall()


def latest_snapshot(con, post_id: str) -> sqlite3.Row | None:
    """The most recently observed snapshot of one post (any source), or None."""
    return con.execute("""SELECT * FROM metric_snapshots WHERE post_id=?
                          ORDER BY observed_at DESC,id DESC LIMIT 1""", (post_id,)).fetchone()


ESTIMATE_NOTE = "platform estimates: excluded from value, never summed with exact counts"


def _metric_summary() -> dict:
    return {"value": None, "exact": 0, "total": 0, "coverage": "0/0",
            "status_counts": {label: 0 for label in METRIC_STATUS_LABELS}, "estimated": None}


def report(con) -> dict[str, dict]:
    """Per-platform latest-snapshot totals and coverage of published posts; exact values only.
    Replies (issue #17) are left out here and counted by ``reply_report``.

    The original keys are kept. Each metric key is the sum of EXACT latest values, and is
    ``None`` (unknown) when no post has an exact value, never a fabricated 0.
    ``coverage_by_metric`` counts posts with an exact value; ``window_coverage`` says how many
    latest snapshots carry an observation window and the span they cover. ``metrics`` (#15)
    adds per metric: ``value``, ``exact``/``total`` (published posts) and ``coverage``,
    ``status_counts`` (a post without any snapshot counts as unknown) and ``estimated``: the
    separately labelled count and sum of platform estimates, or None.
    """
    groups: dict[str, dict] = {}
    for post in published_posts(con, include_replies=False):  # replies: see reply_report()
        item = groups.setdefault(post["platform"], {
            "published": 0, "metric_coverage": 0, **{k: 0 for k in METRIC_FIELDS},
            "coverage_by_metric": {k: 0 for k in METRIC_FIELDS},
            "window_coverage": {"with_window": 0, "without_window": 0,
                                "earliest_window_start": None, "latest_window_end": None},
            "metrics": {k: _metric_summary() for k in METRIC_FIELDS}})
        item["published"] += 1
        snapshot = latest_snapshot(con, post["id"])
        for k, (label, value) in metric_states(snapshot).items():
            summary = item["metrics"][k]
            summary["status_counts"][label] += 1
            if label == "exact":
                item[k] += value
                item["coverage_by_metric"][k] += 1
            elif label == "estimated":
                estimate = summary["estimated"] or {"posts": 0, "sum": 0, "note": ESTIMATE_NOTE}
                estimate["posts"] += 1
                estimate["sum"] += value
                summary["estimated"] = estimate
        if snapshot:
            item["metric_coverage"] += 1
            windows = item["window_coverage"]
            columns = snapshot.keys()  # tolerate a caller-supplied connection that skipped migrate()
            start = snapshot["window_start"] if "window_start" in columns else None
            end = snapshot["window_end"] if "window_end" in columns else None
            if start and end:
                windows["with_window"] += 1
                if windows["earliest_window_start"] is None or start < windows["earliest_window_start"]:
                    windows["earliest_window_start"] = start
                if windows["latest_window_end"] is None or end > windows["latest_window_end"]:
                    windows["latest_window_end"] = end
            else:
                windows["without_window"] += 1
    for item in groups.values():
        total = item["published"]
        for k in METRIC_FIELDS:
            covered = item["coverage_by_metric"][k]
            if covered == 0:
                item[k] = None  # no exact value anywhere: unknown, never a fabricated 0
            item["metrics"][k].update(value=item[k], exact=covered, total=total, coverage=f"{covered}/{total}")
    return dict(sorted(groups.items()))
