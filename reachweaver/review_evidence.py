"""``eu-review-evidence``: read-only evidence of human review for published posts (EU AI Act Art. 50(4) helper).

Art. 50(4) (read on a secondary site, artificialintelligenceact.eu; EUR-Lex not read) exempts AI-generated text
published to inform the public on matters of public interest from the disclosure duty "where the AI-generated
content has undergone a process of human review or editorial control and where a natural or legal person holds
editorial responsibility for the publication of the content". This command lists what ReachWeaver recorded
about that review for each published post:

* who approved (``approved_by`` and how the name was obtained, ``approval_channel``), when (``approved_at``),
  the approved payload hash (``approved_payload_sha256``, which binds the exact text, account, media and
  schedule) and a SHA-256 of the stored text;
* whether the opt-in human-presence confirmation was used (the fixed ``confirmation=`` token of the
  ``approve`` audit event; no other part of the event detail is read or printed);
* the approval-ledger line for the post's approval nonce (``consumed`` time, and whether its payload hash
  equals the row's), read through the ledger's own chain verifier, plus ``ledger.audit``'s integrity status;
* the authorship declaration, when this database has an authorship column (checked by column name; older
  databases simply report "not recorded").

What it is not: legal advice, proof that a reviewer read or edited the text, or proof of who holds editorial
responsibility. The approver name is what the person typed or what the operating system reported, not an
authenticated identity.

Strictly read-only: the database is opened ``mode=ro`` and the ledger file ``O_RDONLY`` (``ledger``'s own
reader); nothing is created, repaired, migrated, approved or sent, and no network is contacted.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from .policy import ValidationError

KIND = "reachweaver.eu-review-evidence"
SCHEMA_VERSION = 1
FORMATS = ("text", "json", "markdown")
MAX_POSTS = 5000
# Column names a parallel change may use for a stored authorship declaration; the first present one is read.
AUTHORSHIP_COLUMNS = ("authorship", "approved_authorship", "authorship_declaration", "declared_authorship")
_CONFIRMATION = re.compile(r"(?:^| )confirmation=(tty_hash_prefix)(?: |$)")
_REQUIRED = ("id", "account_id", "platform", "content", "status", "created_at")
_OPTIONAL = ("approved_at", "approved_by", "approval_channel", "approved_payload_sha256", "approval_nonce",
             "remote_url", "send_started_at")
APPROVER_SOURCE = {
    "cli_flag": "named with approve --approver",
    "cli_env_user": "operating-system user name at approve time",
    "cli_unattributed": "no name recorded",
    "library": "library call (no CLI)",
}
NOTICE = ("Evidence of human review recorded by ReachWeaver; not legal advice; wording from a secondary source "
          "(artificialintelligenceact.eu); verify on EUR-Lex.")
LIMITS = (
    "An approval records that someone ran approve for this exact payload; it does not prove the text was read, "
    "edited or fact-checked.",
    "Art. 50(4) also requires that a natural or legal person holds editorial responsibility for the publication; "
    "ReachWeaver does not record that.",
    "approved_by is typed by the approver or taken from the operating system; it is not an authenticated identity.",
    "The ledger is editable by the same Unix user: its hash chain makes in-place edits detectable, not impossible "
    "(docs/THREAT-MODEL.md T7).",
    "Whether you are a deployer, whether a post informs the public on matters of public interest and whether your "
    "review counts as editorial control are legal questions for you or a qualified lawyer.",
)


def _legal_basis() -> dict:
    """Art. 50(4) exemption wording and the application date, from the AI disclosure registry (secondary site)."""
    from .ai_labels import load_registry
    sources = load_registry()["sources"]
    exemption, applies = sources["eu_art50_human_review"], sources["eu_art113_date"]
    return {"article": "EU AI Act, Article 50(4), second subparagraph (text on matters of public interest)",
            "exemption_quote": exemption["quote"], "url": exemption["url"], "kind": exemption["kind"],
            "checked_on": exemption["checked_on"], "applies_from": "2026-08-02",
            "applies_from_quote": applies["quote"], "applies_from_url": applies["url"],
            "verify_at": "EUR-Lex (the official text); not read for this report"}


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}  # table name is a fixed literal


def _has_events(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_log'").fetchone() is not None


def _authorship_column(columns: set[str]) -> str | None:
    return next((name for name in AUTHORSHIP_COLUMNS if name in columns), None)


def _published(con: sqlite3.Connection, *, account: str | None, platform: str | None,
               post_ids: list[str] | None) -> tuple[list[dict], str | None]:
    have = _columns(con, "posts")
    missing = [c for c in _REQUIRED if c not in have]
    if missing:
        raise ValidationError("This database has no usable posts table (missing " + ", ".join(missing) + ")")
    authorship = _authorship_column(have)
    # Column names come from the fixed tuples above (checked against PRAGMA), never from input.
    select = [f"p.{c}" for c in _REQUIRED] + [f"p.{c}" if c in have else f"NULL AS {c}" for c in _OPTIONAL]
    select.append(f"p.{authorship} AS authorship_value" if authorship else "NULL AS authorship_value")
    select.append("(SELECT MAX(e.at) FROM event_log e WHERE e.post_id=p.id AND e.action='published') AS published_at"
                  if _has_events(con) else "NULL AS published_at")
    where, params = ["p.status='published'"], []
    for column, value in (("p.account_id", account), ("p.platform", platform)):
        if value is not None:
            where.append(f"{column}=?")
            params.append(value)
    if post_ids:
        where.append(f"p.id IN ({','.join('?' for _ in post_ids)})")
        params.extend(post_ids)
    query = f"SELECT {', '.join(select)} FROM posts p WHERE {' AND '.join(where)} ORDER BY p.id"
    return [dict(row) for row in con.execute(query, params)], authorship


def _confirmations(con: sqlite3.Connection, post_ids: list[str]) -> dict[str, str | None]:
    """The fixed ``confirmation=`` token of each post's latest ``approve`` event (nothing else of the detail)."""
    out: dict[str, str | None] = {}
    if not _has_events(con):
        return out
    for start in range(0, len(post_ids), 400):
        chunk = post_ids[start:start + 400]
        for row in con.execute(f"SELECT post_id, detail FROM event_log WHERE action='approve' AND post_id IN "
                               f"({','.join('?' for _ in chunk)}) ORDER BY id", chunk):
            match = _CONFIRMATION.search(str(row["detail"] or ""))
            out[row["post_id"]] = match.group(1) if match else None
    return out


def _ledger(con: sqlite3.Connection) -> tuple[dict, dict[tuple[str, str], dict] | None]:
    """``ledger.audit`` (read-only health view) plus the verified entries, read with the ledger's own reader.

    The ledger module exposes no public entry reader; ``_problem`` is its read-only open + chain verification
    (``O_RDONLY``, never creates, never appends). Entries are None when the ledger is missing or broken.
    """
    from . import ledger
    view = ledger.audit(con)
    result = ledger._problem(ledger.ledger_path(con))  # read-only: open O_RDONLY, verify the hash chain
    if isinstance(result, str):
        chain = "absent" if view["status"] in ("absent", "not_checked") else result
        return {"status": view["status"], "problems": view["problems"], "chain": chain, "entries": 0}, None
    entries = {(e["post_id"], e["approval_nonce"]): e for e in result[0]}
    return ({"status": view["status"], "problems": view["problems"], "chain": "verified", "entries": len(entries)},
            entries)


def _since(value: str | None, now: datetime) -> str | None:
    if value is None:
        return None
    from .insights import parse_since
    since = parse_since(value, now)
    return since.isoformat(timespec="seconds") if since else None


def build(con: sqlite3.Connection, *, account: str | None = None, platform: str | None = None,
          since: str | None = None, post_ids: list[str] | None = None, limit: int = 500,
          now: datetime | None = None) -> dict:
    """The evidence report (SELECT only; the ledger is only read)."""
    from .storage import parse_utc
    if type(limit) is not int or not 1 <= limit <= MAX_POSTS:
        raise ValidationError(f"--limit must be 1..{MAX_POSTS}")
    current = now or datetime.now(timezone.utc)
    since_at = _since(since, current)
    rows, authorship_column = _published(con, account=account, platform=platform, post_ids=post_ids)
    if since_at:
        def posted(row: dict) -> str:
            for key in ("published_at", "send_started_at", "created_at"):
                try:
                    if row[key]:
                        return parse_utc(row[key])
                except ValidationError:
                    continue
            return ""
        rows = [r for r in rows if posted(r) >= since_at]
    rows.sort(key=lambda r: (str(r["published_at"] or r["send_started_at"] or ""), r["id"]), reverse=True)
    truncated = len(rows) > limit
    rows = rows[:limit]
    ledger_view, entries = _ledger(con)
    confirmations = _confirmations(con, [r["id"] for r in rows])
    posts = []
    for row in rows:
        nonce = row["approval_nonce"]
        if entries is None:
            ledger_state: dict[str, Any] = {"state": "ledger_unreadable", "at": None, "payload_sha256_matches": None}
        elif not nonce:
            ledger_state = {"state": "no_nonce", "at": None, "payload_sha256_matches": None,
                            "note": "approved before approval nonces existed (no ledger line expected)"}
        else:
            entry = entries.get((row["id"], nonce))
            ledger_state = ({"state": "not_found", "at": None, "payload_sha256_matches": None} if entry is None else
                            {"state": entry["event"], "at": entry["ts"],
                             "payload_sha256_matches": entry["payload_sha256"] == row["approved_payload_sha256"]})
        approver_named = bool(row["approved_by"])
        gaps = []
        if not row["approved_at"]:
            gaps.append("no approval time recorded")
        if not row["approved_payload_sha256"]:
            gaps.append("no approved payload hash (approved before payload binding)")
        if not approver_named:
            gaps.append("no approver name recorded")
        if not (ledger_state["state"] == "consumed" and ledger_state["payload_sha256_matches"]):
            gaps.append(f"no matching consumed ledger line ({ledger_state['state']})")
        if authorship_column is None or row["authorship_value"] is None:
            gaps.append("no authorship declaration recorded")
        core = bool(row["approved_at"] and row["approved_payload_sha256"])
        level = "missing" if not core else ("recorded" if approver_named and ledger_state["state"] == "consumed"
                                            and ledger_state["payload_sha256_matches"] else "partial")
        posts.append({
            "post_id": row["id"], "account_id": row["account_id"], "platform": row["platform"],
            "published_at": row["published_at"], "remote_url": row["remote_url"],
            "approved_at": row["approved_at"], "approved_by": row["approved_by"],
            "approval_channel": row["approval_channel"],
            "approver_source": APPROVER_SOURCE.get(str(row["approval_channel"]), "not recorded"),
            "approved_payload_sha256": row["approved_payload_sha256"],
            "text_sha256": hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest(),
            "human_confirmation": confirmations.get(row["id"]),
            "ledger": ledger_state,
            "authorship": row["authorship_value"], "authorship_recorded": row["authorship_value"] is not None,
            "evidence": level, "gaps": gaps,
        })
    levels = {name: sum(1 for p in posts if p["evidence"] == name) for name in ("recorded", "partial", "missing")}
    return {
        "kind": KIND, "schema_version": SCHEMA_VERSION, "status": "ok" if posts else "no_published_posts",
        "notice": NOTICE, "generated_at": current.isoformat(timespec="seconds"),
        "filters": {"account": account, "platform": platform, "since": since_at, "post_ids": post_ids or None,
                    "limit": limit},
        "legal_basis": _legal_basis(),
        "ledger": ledger_view,
        "authorship_column": authorship_column,
        "summary": {"posts": len(posts), **levels, "truncated": truncated,
                    "approver_named": sum(1 for p in posts if p["approved_by"]),
                    "human_confirmation": sum(1 for p in posts if p["human_confirmation"]),
                    "ledger_consumed_match": sum(1 for p in posts if p["ledger"]["state"] == "consumed"
                                                 and p["ledger"]["payload_sha256_matches"]),
                    "authorship_recorded": sum(1 for p in posts if p["authorship_recorded"])},
        "evidence_levels": {"recorded": "approval time, payload hash, a named approver and a matching consumed "
                                        "ledger line", "partial": "approval time and payload hash, but no approver "
                                        "name or no matching ledger line", "missing": "no approval time or no "
                                        "payload hash"},
        "posts": posts,
        "limits": list(LIMITS),
        "database_written": False, "ledger_written": False, "network_requests": False,
    }


# ---- rendering -----------------------------------------------------------------------------------------------

def _short(value: object, n: int = 12) -> str:
    return str(value)[:n] if value else "-"


def render_text(report: Mapping) -> str:
    if report["status"] == "no_database":
        return f"{NOTICE}\nNo ReachWeaver database at {report['database']} yet: nothing to report."
    s, led = report["summary"], report["ledger"]
    lines = [f"NOTE: {report['notice']}", "",
             f"EU AI Act review evidence: {s['posts']} published post(s); recorded {s['recorded']}, partial "
             f"{s['partial']}, missing {s['missing']}" + (" (truncated by --limit)" if s["truncated"] else ""),
             f"Approval ledger: {led['status']} (chain {led['chain']}, {led['entries']} line(s), "
             f"{len(led['problems'])} problem(s))",
             f"Authorship column: {report['authorship_column'] or 'not in this database'}", ""]
    for p in report["posts"]:
        lines.append(f"{p['post_id']} {p['platform']}/{p['account_id']} published {p['published_at'] or '-'}: "
                     f"{p['evidence'].upper()}")
        lines.append(f"  approved {p['approved_at'] or '-'} by {p['approved_by'] or '-'} ({p['approver_source']}); "
                     f"payload {_short(p['approved_payload_sha256'])}; text {_short(p['text_sha256'])}; "
                     f"confirmation {p['human_confirmation'] or '-'}")
        lines.append(f"  ledger {p['ledger']['state']} {p['ledger']['at'] or ''}; authorship {p['authorship'] or '-'}")
        if p["gaps"]:
            lines.append("  gaps: " + "; ".join(p["gaps"]))
    basis = report["legal_basis"]
    lines += ["", f"{basis['article']}: exempt \"{basis['exemption_quote']}\" ({basis['url']}, secondary, checked "
              f"{basis['checked_on']}). Applies from {basis['applies_from']}.", "Limits:"]
    lines += [f"  - {item}" for item in report["limits"]]
    lines.append("Read-only: database and ledger opened read-only; nothing was written, repaired or sent.")
    return "\n".join(lines)


def render_markdown(report: Mapping) -> str:
    from .report_format import markdown_cell as cell
    if report["status"] == "no_database":
        return f"> **{NOTICE}**\n\nNo ReachWeaver database at `{report['database']}` yet.\n"
    s, led, basis = report["summary"], report["ledger"], report["legal_basis"]
    out = ["# Human review evidence (EU AI Act Art. 50(4))", "", f"> **{report['notice']}**", "",
           f"Generated {report['generated_at']}. {s['posts']} published post(s): **{s['recorded']} recorded**, "
           f"{s['partial']} partial, {s['missing']} missing" + (" (truncated by --limit)." if s["truncated"] else "."),
           "", f"Approval ledger: `{led['status']}`, chain `{led['chain']}`, {led['entries']} line(s), "
           f"{len(led['problems'])} problem(s). Authorship column: "
           f"{('`' + report['authorship_column'] + '`') if report['authorship_column'] else 'not in this database'}.",
           "", "| Post | Platform | Published | Approved at | Approved by | Payload SHA-256 | Text SHA-256 | "
           "Confirmation | Ledger | Authorship | Evidence |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in report["posts"]:
        ledger_cell = p["ledger"]["state"] + (f" {p['ledger']['at']}" if p["ledger"]["at"] else "")
        out.append("| " + " | ".join(cell(v) for v in (
            p["post_id"], f"{p['platform']}/{p['account_id']}", p["published_at"] or "-", p["approved_at"] or "-",
            f"{p['approved_by'] or '-'} ({p['approver_source']})", p["approved_payload_sha256"] or "-",
            p["text_sha256"], p["human_confirmation"] or "-", ledger_cell, p["authorship"] or "-",
            p["evidence"])) + " |")
    gaps = [(p["post_id"], g) for p in report["posts"] for g in p["gaps"]]
    if gaps:
        out += ["", "## Gaps", ""] + [f"- {cell(pid)}: {cell(g)}" for pid, g in gaps]
    out += ["", "## Legal basis (secondary source)", "",
            f"{basis['article']}: the disclosure duty does not apply \"{basis['exemption_quote']}\" "
            f"([source]({basis['url']}), {basis['kind']}, checked {basis['checked_on']}). It applies from "
            f"{basis['applies_from']} ([Art. 113]({basis['applies_from_url']})). Verify on EUR-Lex.",
            "", "## Limits", ""] + [f"- {item}" for item in report["limits"]]
    return "\n".join(out) + "\n"


# ---- CLI -----------------------------------------------------------------------------------------------------

def add_parser(sub) -> None:
    p = sub.add_parser("eu-review-evidence", help="Read-only: who approved each published post, when, the approved "
                       "hash, ledger line and authorship (EU AI Act Art. 50(4) human-review evidence; not legal advice)")
    p.add_argument("--since", metavar="WHEN", help="Only posts published since 30d (days ago), YYYY-MM-DD or an ISO time")
    p.add_argument("--account", metavar="ID", help="Only this account")
    p.add_argument("--platform", metavar="PLATFORM", help="Only this platform id")
    p.add_argument("--post-id", action="append", metavar="ID", help="Only this post; repeat")
    p.add_argument("--limit", type=int, default=500, help=f"Newest published posts listed, 1..{MAX_POSTS} (default 500)")
    p.add_argument("--format", choices=FORMATS, default="text", help="text (default), json or markdown")
    p.add_argument("--json", action="store_true", help="Same as --format json")
    p.add_argument("--now", help="Reference time for --since 30d (ISO 8601 with timezone; default now)")


def run_cli(args) -> int:
    """Dispatch for ``eu-review-evidence``: database mode=ro, ledger O_RDONLY; never writes or repairs anything."""
    from .storage import connect_readonly, database_path, parse_utc
    fmt = "json" if args.json else args.format
    now = datetime.fromisoformat(parse_utc(args.now)) if args.now else datetime.now(timezone.utc)
    database = database_path(args.db)
    if not database.exists():
        report: dict = {"kind": KIND, "schema_version": SCHEMA_VERSION, "status": "no_database", "notice": NOTICE,
                        "database": str(database), "database_written": False, "ledger_written": False}
    else:
        with connect_readonly(database) as db:
            report = build(db, account=args.account, platform=args.platform, since=args.since,
                           post_ids=args.post_id, limit=args.limit, now=now)
    if fmt == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif fmt == "markdown":
        print(render_markdown(report), end="")
    else:
        print(render_text(report))
    return 0
