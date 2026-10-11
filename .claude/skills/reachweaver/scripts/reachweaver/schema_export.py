"""Read-only export of the legacy SQLite outbox as canonical schema 1.2 documents (issue #4).

Every emitted document passes ``schemas.validate``. When storage does not record a
field the contract requires, the exporter writes an explicit unknown value only for
*descriptive* fields whose domain has one (``locale: "und"``, the BCP 47 "undetermined"
tag; free-text ``checked_by``/``display_name: "unknown"``). It never invents an
identifier or join key, an enum member, a consent record, a reviewer or an approval
hash: such documents are skipped and listed under ``unmapped`` with a reason.

Approvals are never exported. Storage records approval as an ``approved_at`` timestamp
only, and minting an ``approved_payload_sha256`` here would fabricate a human approval
of a payload that nobody reviewed in canonical form. See docs/ADR-0003-CANONICAL-CONTRACTS.md.

The connection is put in ``PRAGMA query_only`` mode for the duration of the export:
no table, row, setting or event is written. Nothing here sends, schedules or reconciles.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .campaigns import SLUG
from .policy import ValidationError
from .privacy import redact
from .schemas import (
    APPROVAL_GATED, SCHEMA_VERSION, STORAGE_STATUS_MAP, canonical_json, payload_sha256, validate,
)
from .storage import METRIC_FIELDS, decode_metric_status

# Document kinds the exporter reports on, in output order. Draft and Approval are
# listed so `--type Draft|Approval` explains why none are produced.
EXPORT_TYPES = ("Account", "Evidence", "PlatformVariant", "PublishJob", "PublishReceipt",
                "AnalyticsSnapshot", "Draft", "Approval")
UNKNOWN_LOCALE = "und"   # BCP 47 / ISO 639-2 "Undetermined" (RFC 5646, section 4.1)
UNKNOWN_TEXT = "unknown"
# Canonical states whose PublishJob must name an approval (schemas._job_rules).
NEEDS_APPROVAL_ID = APPROVAL_GATED | {"published", "failed", "unknown"}
ACCOUNT_PUBLIC_FIELDS = ("brand_id", "handle", "server", "audience_ids", "locale", "consent")
RECEIPT_EVENTS = {"published": "api_response", "reconciled_published": "owner_verified_native_account"}

DRAFT_REASON = ("authorship (human/ai_assisted/ai_generated) and the content brief are not recorded "
                f"in legacy storage; schema {SCHEMA_VERSION} has no unknown authorship value, so a Draft would be "
                "fabricated. The post text is exported as the PlatformVariant instead")
# Owner decision D8 (#7): the storage approval hash binds the legacy posts row, not a canonical
# PlatformVariant, so it is never minted into an Approval document.
APPROVAL_REASON = ("legacy approval binds a storage-row hash (reachweaver.storage.approval/1) and an "
                   "unauthenticated approver label, or nothing at all for rows approved before issue #7; "
                   f"it is not the canonical approved_payload_sha256 that schema {SCHEMA_VERSION} requires, "
                   "and computing that at export time would fabricate a human approval of a payload nobody "
                   "reviewed in canonical form. Re-review and approve the canonical payload instead")
NO_BRAND_REASON = ("brand_id is not recorded: the post was drafted directly, not staged with "
                   "'campaign-stage' (which records brand_id in campaign_variants)")
SECRET_REASON = ("contains secret-looking text, so it is not exported; use 'data-export' for a "
                 "redacted copy of the local database")
NOTE = (f"READ-ONLY export of local state as canonical schema {SCHEMA_VERSION} documents. Not an approval, "
        "not a platform receipt and not consent to publish; nothing was sent or written.")


def _utc(value: Any) -> Any:
    """Normalize an offset timestamp to RFC 3339 UTC ``...Z``; leave anything else as-is
    so that schema validation reports it instead of the exporter guessing a time."""
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return value
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _doc(kind: str, **fields: Any) -> dict[str, Any]:
    """Envelope plus every field whose value is known (None and "" are left out)."""
    doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "type": kind}
    for key, value in fields.items():
        if value is not None and value != "":
            doc[key] = value
    return doc


def _rows(con: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    cursor = con.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in cursor.execute(query)]
    finally:
        cursor.close()


def _has_secret(doc: Mapping[str, Any]) -> bool:
    line = canonical_json(doc).decode("utf-8")
    return redact(line) != line


class _Collector:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.unmapped: list[dict[str, Any]] = []

    def skip(self, kind: str, doc_id: str | None, source: str, reason: str) -> None:
        self.unmapped.append({"type": kind, "id": doc_id, "source": source, "reason": redact(reason)})

    def offer(self, kind: str, doc: dict[str, Any], source: str) -> bool:
        doc_id = doc.get("id") if isinstance(doc.get("id"), str) else None
        errors = validate(kind, doc)
        if errors:
            self.skip(kind, doc_id, source, f"does not satisfy schema {SCHEMA_VERSION}: " + "; ".join(errors))
            return False
        if _has_secret(doc):
            self.skip(kind, doc_id, source, SECRET_REASON)
            return False
        self.documents.append(doc)
        return True


def _post_ids(post_id: str) -> dict[str, str]:
    return {name: f"legacy:{post_id}:{name}" for name in ("draft", "variant", "approval", "job", "receipt")}


def _export_post(out: _Collector, post: dict[str, Any], staged: dict[str, Any] | None,
                 events: list[dict[str, Any]]) -> None:
    post_id, status = post["id"], post["status"]
    state = STORAGE_STATUS_MAP.get(status)
    ids, source = _post_ids(str(post_id)), f"posts:{post_id}"
    out.skip("Draft", ids["draft"], source, DRAFT_REASON)
    if state in NEEDS_APPROVAL_ID or post.get("approved_at"):
        out.skip("Approval", ids["approval"], source, APPROVAL_REASON)
    if state is None:
        for kind in ("PlatformVariant", "PublishJob"):
            out.skip(kind, ids["variant" if kind == "PlatformVariant" else "job"], source,
                     f"storage status {status!r} has no canonical state mapping")
    elif staged is None:
        out.skip("PlatformVariant", ids["variant"], source, NO_BRAND_REASON)
        out.skip("PublishJob", ids["job"], source, NO_BRAND_REASON)
    else:
        variant = _doc("PlatformVariant", id=ids["variant"], draft_id=ids["draft"],
                       brand_id=staged["brand_id"], campaign_id=staged["campaign_id"],
                       platform=post["platform"], account_id=post["account_id"], locale=UNKNOWN_LOCALE,
                       format="text", text=post["content"], created_at=_utc(post["created_at"]))
        if not out.offer("PlatformVariant", variant, source):
            out.skip("PublishJob", ids["job"], source, "its payload PlatformVariant is unmapped")
        else:
            job = _doc("PublishJob", id=ids["job"], variant_id=ids["variant"],
                       approval_id=ids["approval"] if state in NEEDS_APPROVAL_ID else None,
                       brand_id=staged["brand_id"], campaign_id=staged["campaign_id"],
                       account_id=post["account_id"], platform=post["platform"], state=state,
                       # The adapters' real dedupe key is the local post id (e.g. Mastodon Idempotency-Key).
                       idempotency_key=post_id, payload=variant, payload_sha256=payload_sha256(variant),
                       scheduled_at=_utc(post.get("scheduled_at")),
                       attempts=sum(1 for e in events if e["action"] == "send_claimed"),
                       remote_post_id=post.get("remote_id") if state == "published" else None,
                       remote_url=post.get("remote_url") if state == "published" else None,
                       error_code=post.get("last_error") if state == "unknown" else None,
                       created_at=_utc(post["created_at"]),
                       updated_at=_utc(events[-1]["at"] if events else post["created_at"]))
            out.offer("PublishJob", job, source)
    if status == "published":
        receipt_event = next((e for e in reversed(events) if e["action"] in RECEIPT_EVENTS), None)
        if receipt_event is None:
            out.skip("PublishReceipt", ids["receipt"], source,
                     "no surviving 'published' or 'reconciled_published' event, so receipt evidence "
                     "and observed_at are unknown (a published receipt cannot have evidence 'none')")
        else:
            out.offer("PublishReceipt", _doc(
                "PublishReceipt", id=ids["receipt"], job_id=ids["job"], account_id=post["account_id"],
                platform=post["platform"], idempotency_key=post_id, outcome="published",
                remote_post_id=post.get("remote_id"), remote_url=post.get("remote_url"),
                evidence=RECEIPT_EVENTS[receipt_event["action"]], observed_at=_utc(receipt_event["at"])), source)


def _export_snapshots(out: _Collector, con: sqlite3.Connection, posts: Mapping[str, dict[str, Any]],
                      staged: Mapping[str, dict[str, Any]]) -> None:
    for snap in _rows(con, "SELECT * FROM metric_snapshots ORDER BY id"):
        snap_id, source = f"legacy:snapshot:{snap['id']}", f"metric_snapshots:{snap['id']}"
        post = posts.get(snap["post_id"])
        if post is None or post["status"] != "published" or not post.get("remote_id"):
            status = post["status"] if post else "missing"
            out.skip("AnalyticsSnapshot", snap_id, source,
                     f"post {snap['post_id']} has no recorded remote post id (status {status})")
            continue
        campaign = staged[post["id"]]["campaign_id"] if post["id"] in staged else post.get("campaign")
        # Schema 1.2 per-metric status (#15): emitted only when storage recorded one. A malformed,
        # hand-edited column is reported, never read as unknown or zero.
        try:
            statuses = decode_metric_status(snap.get("metric_status"))
        except ValidationError as exc:
            out.skip("AnalyticsSnapshot", snap_id, source, str(exc))
            continue
        # Schema 1.1 window, emitted only when storage recorded one (NULL = unknown window).
        # .get tolerates a caller connection that skipped storage.migrate(); a half window
        # left by a hand-edited row fails validation and is reported as unmapped.
        out.offer("AnalyticsSnapshot", _doc(
            "AnalyticsSnapshot", id=snap_id, job_id=_post_ids(str(post["id"]))["job"],
            account_id=post["account_id"], platform=post["platform"], remote_post_id=post["remote_id"],
            campaign_id=campaign if isinstance(campaign, str) and SLUG.fullmatch(campaign) else None,
            observed_at=_utc(snap["observed_at"]), source=snap["source"],
            metrics={key: snap[key] for key in METRIC_FIELDS},
            window_start=_utc(snap.get("window_start")), window_end=_utc(snap.get("window_end")),
            metric_status=statuses or None), source)


def _export_evidence(out: _Collector, staged_rows: Iterable[dict[str, Any]]) -> None:
    seen: dict[str, bytes] = {}
    for row in staged_rows:
        source = f"campaign_variants:{row['campaign_id']}/{row['variant_id']}"
        try:
            items = json.loads(row["evidence_json"])
        except (TypeError, json.JSONDecodeError):
            out.skip("Evidence", None, source, "evidence_json is not valid JSON")
            continue
        if not isinstance(items, list):
            out.skip("Evidence", None, source, "evidence_json is not a list")
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                out.skip("Evidence", None, source, "evidence record is not an object with an id")
                continue
            doc = _doc("Evidence", id=f"{row['campaign_id']}:{item['id']}", claim=item.get("claim"),
                       url=item.get("url"), doi=item.get("doi"), excerpt=item.get("excerpt"),
                       verification=item.get("verification"),
                       checked_by=item.get("checked_by") or UNKNOWN_TEXT,
                       checked_at=_utc(item.get("checked_at")))
            fingerprint = canonical_json(doc)
            if doc["id"] in seen:
                if seen[doc["id"]] != fingerprint:
                    out.skip("Evidence", doc["id"], source,
                             "conflicting content for an evidence id already exported from this campaign")
                continue
            seen[doc["id"]] = fingerprint
            out.offer("Evidence", doc, source)


def _export_accounts(out: _Collector, accounts: Mapping[str, Mapping[str, Any]] | None,
                     accounts_error: str | None) -> None:
    if accounts is None:
        out.skip("Account", None, "accounts", accounts_error or "accounts config was not loaded")
        return
    for account_id in sorted(accounts):
        raw, source = accounts[account_id], f"accounts:{account_id}"
        missing = [name for name in ("brand_id", "consent") if name not in raw]
        if missing:
            out.skip("Account", account_id, source,
                     f"accounts.json does not record {' or '.join(missing)}; add the public brand_id and an "
                     "account_owner consent record (obtained, recorded_at) to export this Account")
            continue
        doc = _doc("Account", id=account_id, platform=raw.get("platform"), env_prefix=raw.get("env_prefix"),
                   display_name=raw.get("display_name") or UNKNOWN_TEXT,
                   chat_id=str(raw["chat_id"]) if raw.get("chat_id") not in (None, "") else None,
                   **{name: raw.get(name) for name in ACCOUNT_PUBLIC_FIELDS})
        out.offer("Account", doc, source)


def export_documents(con: sqlite3.Connection, accounts: Mapping[str, Mapping[str, Any]] | None = None, *,
                     accounts_error: str | None = None, types: Iterable[str] | None = None) -> dict[str, Any]:
    """Map local rows to validated canonical documents without writing to the database.

    Returns ``{"documents": [...], "unmapped": [...], "counts": {...}}``. ``accounts`` is the
    public ``load_accounts`` mapping (no secrets); ``None`` lists Accounts as unmapped.
    """
    wanted = set(EXPORT_TYPES if types is None else types)
    unknown = wanted - set(EXPORT_TYPES)
    if unknown:
        raise ValidationError(f"Unknown export type(s) {', '.join(sorted(unknown))}; "
                              f"expected one of {', '.join(EXPORT_TYPES)}")
    previous = con.execute("PRAGMA query_only").fetchone()[0]
    con.execute("PRAGMA query_only=ON")
    try:
        out = _Collector()
        _export_accounts(out, accounts, accounts_error)
        staged_rows = _rows(con, "SELECT * FROM campaign_variants ORDER BY campaign_id,variant_id")
        _export_evidence(out, staged_rows)
        staged = {row["post_id"]: row for row in staged_rows}
        events: dict[str, list[dict[str, Any]]] = {}
        for item in _rows(con, "SELECT post_id,action,at FROM event_log WHERE post_id IS NOT NULL ORDER BY id"):
            events.setdefault(item["post_id"], []).append(item)
        posts = {row["id"]: row for row in _rows(con, "SELECT * FROM posts ORDER BY created_at,id")}
        for post in posts.values():
            _export_post(out, post, staged.get(post["id"]), events.get(post["id"], []))
        _export_snapshots(out, con, posts, staged)
    finally:
        con.execute(f"PRAGMA query_only={'ON' if previous else 'OFF'}")
    order = {kind: index for index, kind in enumerate(EXPORT_TYPES)}
    documents = sorted((d for d in out.documents if d["type"] in wanted),
                       key=lambda d: (order[d["type"]], d["id"]))
    unmapped = [u for u in out.unmapped if u["type"] in wanted]
    counts = {kind: sum(1 for d in documents if d["type"] == kind) for kind in EXPORT_TYPES if kind in wanted}
    return {"schema_version": SCHEMA_VERSION, "documents": documents, "counts": counts,
            "unmapped": unmapped, "note": NOTE}


def render_jsonl(documents: Iterable[Mapping[str, Any]]) -> str:
    """One canonical-JSON document per line (sorted keys, UTF-8, no whitespace)."""
    return "".join(canonical_json(doc).decode("utf-8") + "\n" for doc in documents)
