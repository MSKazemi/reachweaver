"""One offline readiness view per configured account, plus a read-only dead-letter report (issue #26).

``build_health`` combines what already exists offline: the capability registry
(adapter status and staleness), credential variable NAMES (never values),
LinkedIn version/authorization lifecycle, Postiz backend configuration, the
local queue, rolling send attempts against the effective budget, uncertain
sends, approved/scheduled posts whose approval binding needs a human re-approval
(issue #7), active provider cooldowns after a 429 (issue #11), the emergency pause and
the live opt-in flag. It returns an overall ``ok | attention | blocked`` with
machine-readable reasons.

Nothing here contacts a provider, mutates the database, retries a send or
prints a credential value (only the presence of each variable is checked).
``needs_review`` posts are grouped by a sanitized error code with the human
reconcile steps, plus the C1/C2/C3 send classification recorded with each failure
(issue #11, D11), which changes only the guidance; this module has no retry path.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

from .caps import CLI_DEFAULTS, effective_caps, effective_rate_limits, providers_for
from .capabilities import DEFAULT_MAX_AGE_DAYS, load_registry, require_capability, stale_entries
from .accounts import CredentialError, credential, credential_source
from .credential_hygiene import expected_variables
from .policy import ValidationError, live_opt_in, runtime_environment
from .privacy import redact
from .storage import approval_key_mode, is_paused, parse_utc, queue_health, utc_now

REPORT = "reachweaver_health"
LEVELS = ("ok", "attention", "blocked")
QUEUED_STATUSES = ("approved", "scheduled")
NOTE = ("LOCAL STATE ONLY: no provider was contacted, nothing was retried or changed, and credential "
        "values were never shown. A green report is not a remote receipt or proof of permission.")
_CODE_PART = re.compile(r"^[a-z][a-z0-9_]{0,79}$")

RECONCILE_STEPS = (
    "Do NOT resend or re-approve this post until it is reconciled; the remote write may have succeeded.",
    "Sign in to the native account (or the Postiz calendar for backend accounts) and search for the exact text.",
    "If it is live: reachweaver reconcile POST_ID --published --remote-id NATIVE_ID --remote-url NATIVE_URL",
    "If it is absent: reachweaver reconcile POST_ID --not-published (returns it to draft; the attempt still counts)",
)
_ACTIONS = (
    ("postiz_accepted_platform_receipt_unverified",
     "Postiz accepted the job; confirm the native post exists, then reconcile with the NATIVE id/URL."),
    ("tiktok_creator_inbox_delivered_not_public",
     "TikTok draft is in the creator's inbox, NOT public (issue #23). The creator finishes and posts it in "
     "the TikTok app; then reconcile with the native video id/URL, or --not-published if it was discarded."),
    ("http_401", "Credential rejected (revoked or expired). Reconcile first, then follow the revoked-permission "
                 "runbook: re-authorize, update the env var, credentials-audit, connect-check --online."),
    ("http_403", "Permission denied (scope or role revoked). Reconcile first, then re-check app permissions "
                 "and scopes at the provider before re-approving anything."),
    ("http_426", "API version no longer active (LinkedIn version sunset). Reconcile first, then pin a current "
                 "linkedin_version and re-test with connect-check --online."),
    ("http_429", "Rate limited. A provider cooldown now delays the account's other posts; this post is "
                 "never resent. Whether it was created depends on the provider (see send_class): verify "
                 "before reconciling, and consider stricter caps in config/caps.json."),
    ("identity_mismatch", "Wrong account or token. Pause, reconcile, then fix accounts.json/env before resuming."),
)
_DEFAULT_ACTION = "Outcome unknown. Inspect the native account for the exact text, then reconcile."
# Issue #11 (D11): what the C1/C2/C3 label means for the human check. Guidance only; nothing
# here moves a post out of needs_review, and the reconcile steps are the same for every class.
CLASS_GUIDANCE = {
    "certain_not_sent": ("certain not sent: the failure came before the create request was handed to the "
                         "network. A quick look at the native account is enough before "
                         "'reconcile --not-published'."),
    "likely_not_created": ("likely not created: the provider rejected the create with a rate limit that it "
                           "documents (or its server source shows) is applied before the create runs. This is "
                           "not an API guarantee: check the native account, then reconcile."),
    "ambiguous": ("ambiguous: the create may have been applied (timeout, 5xx, unexpected response, or a "
                  "provider that documents nothing). Search the native account for the exact text."),
    "unknown": "not classified (recorded before issue #11 or by another tool): treat as ambiguous.",
}
REAPPROVAL_STEPS = (
    "Nothing was sent: the post stays approved/scheduled, but no claim accepts it until a human re-approves it.",
    "reachweaver unapprove POST_ID   (back to draft; clears the old binding)",
    "reachweaver show POST_ID        (read the exact text, account and links that will be bound)",
    "reachweaver approve POST_ID     (binds a fresh payload hash), then schedule again if it was scheduled",
)


def _worst(levels) -> str:
    return max(levels, key=LEVELS.index, default="ok")


def _reason(level: str, code: str, detail: str = "", account_id: str | None = None) -> dict:
    item = {"level": level, "code": code}
    if account_id:
        item["account_id"] = account_id
    if detail:
        item["detail"] = detail
    return item


def sanitize_error_code(text: object) -> str:
    """Stable, secret-free code from a stored ``last_error``: leading ``a_b[:c_d]`` identifiers only."""
    if not isinstance(text, str) or not text.strip():
        return "unknown"
    head = redact(text).split(";")[0].strip()
    parts = []
    for part in head.split(":")[:2]:
        part = part.strip()
        if not _CODE_PART.fullmatch(part):
            break
        parts.append(part)
    return ":".join(parts) or "unclassified"


def suggested_action(code: str) -> str:
    for marker, action in _ACTIONS:
        if marker in code:
            return action
    return _DEFAULT_ACTION


def _send_class(detail: object) -> str:
    """The ``class=`` token appended by ``uncertain_send`` (last token only), else ``unknown``."""
    tokens = str(detail or "").split()
    last = tokens[-1] if tokens else ""
    value = last[len("class="):] if last.startswith("class=") else ""
    return value if value in CLASS_GUIDANCE else "unknown"


def _age_minutes(since: str | None, current: datetime) -> int | None:
    if not since:
        return None
    try:
        return max(0, int((current - datetime.fromisoformat(parse_utc(since))).total_seconds() // 60))
    except (ValueError, TypeError, ValidationError):
        return None


def dead_letter(con, now: str | None = None) -> dict:
    """Read-only grouping of ``needs_review`` posts by sanitized error code. Never retries."""
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    rows = con.execute("""SELECT p.id, p.account_id, p.platform, p.last_error, p.send_started_at, p.created_at,
                                 (SELECT MAX(e.at) FROM event_log e
                                  WHERE e.post_id=p.id AND e.action='needs_review') AS review_at,
                                 (SELECT e.detail FROM event_log e WHERE e.post_id=p.id
                                  AND e.action='needs_review' ORDER BY e.id DESC LIMIT 1) AS review_detail
                          FROM posts p WHERE p.status='needs_review' ORDER BY p.account_id, p.id""").fetchall()
    groups: dict[str, dict] = {}
    for row in rows:
        code = sanitize_error_code(row["last_error"])
        since = row["review_at"] or row["send_started_at"] or row["created_at"]
        age = _age_minutes(since, current)
        group = groups.setdefault(code, {"code": code, "count": 0, "oldest_age_minutes": None,
                                         "suggested_action": suggested_action(code),
                                         "send_classes": {},
                                         "reconcile_steps": list(RECONCILE_STEPS), "posts": []})
        send_class = _send_class(row["review_detail"])
        group["count"] += 1
        group["send_classes"][send_class] = group["send_classes"].get(send_class, 0) + 1
        if age is not None and (group["oldest_age_minutes"] is None or age > group["oldest_age_minutes"]):
            group["oldest_age_minutes"] = age
        group["posts"].append({"post_id": row["id"], "account_id": row["account_id"],
                               "platform": row["platform"], "since": since, "age_minutes": age,
                               "send_class": send_class, "class_guidance": CLASS_GUIDANCE[send_class]})
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], g["code"]))
    return {"report": "dead_letter", "observed_at": now, "total": len(rows), "groups": ordered,
            "retry": "never automatic; reconcile each post after checking the native account",
            "send_class_meaning": dict(CLASS_GUIDANCE),
            "note": "Read-only. Error codes are sanitized identifiers; raw provider text is never shown."}


def _adapter(account: dict, registry: dict) -> tuple[str, str | None]:
    """('live_text'|'backend'|'manual', refusal detail or None) from the registry, offline."""
    platform = account["platform"]
    if account.get("backend") is not None:
        from .postiz import require_backend_capability
        try:
            require_backend_capability(platform, registry=registry)
        except ValidationError as exc:
            return "manual", str(exc)
        return "backend", None
    try:
        require_capability(platform, "text", "publish", registry=registry)
    except ValidationError as exc:
        return "manual", str(exc)
    return "live_text", None


def _last_attempt(con, account_id: str, start: str, now: str) -> str | None:
    row = con.execute("""SELECT MAX(e.at) AS at FROM event_log e JOIN posts p ON p.id=e.post_id
                         WHERE p.account_id=? AND e.action='send_claimed' AND e.at>? AND e.at<=?""",
                      (account_id, start, now)).fetchone()
    return row["at"] if row else None


def account_health(con, account: dict, *, registry: dict, stale: list[str], queue: dict,
                   counts: dict, caps: dict | None, cli_caps: tuple[int, int], now: str, env) -> dict:
    current = datetime.fromisoformat(now)
    account_id, platform = account["id"], account["platform"]
    reasons: list[dict] = []
    entry = next(e for e in registry["platforms"] if e["id"] == platform)
    adapter, refusal = _adapter(account, registry)
    queued = sum(counts.get(s, 0) for s in QUEUED_STATUSES)

    expected = expected_variables(account)  # a Postiz-backed account reads only its backend key
    present: list[str] = []
    missing: list[str] = []
    unreadable: list[str] = []
    for name in expected:  # environment or $CREDENTIALS_DIRECTORY; presence only, never the value
        try:
            (present if credential(name, env) else missing).append(name)
        except CredentialError as exc:
            missing.append(name)
            unreadable.append(str(exc))

    result = {
        "account_id": account_id, "platform": platform, "adapter": adapter,
        "registry": {"status": entry["status"], "reachweaver_adapter": entry["reachweaver_adapter"],
                     "verified_date": entry["verified_date"], "stale": platform in stale},
        "credentials": {"present": present, "missing": missing, "source": credential_source(env),
                        "note": "variable names only; values are never shown"},
        "postiz": {"configured": account.get("backend") == "postiz"},
    }
    for problem in dict.fromkeys(unreadable):
        reasons.append(_reason("blocked", "credential_unreadable", problem))
    if account.get("backend") == "postiz":
        result["postiz"].update({"base_url": account.get("postiz_base_url"),
                                 "integration_id_set": bool(account.get("postiz_integration_id"))})
    if refusal:
        result["adapter_refusal"] = refusal
    if platform in stale:
        reasons.append(_reason("attention", "registry_entry_stale",
                               f"{platform} verified {entry['verified_date']}; re-verify official sources"))
    if adapter == "manual" and queued:
        reasons.append(_reason("attention", "manual_platform_has_queued_posts",
                               f"{queued} approved/scheduled post(s); publish --live will skip them"))
    if adapter != "manual" and missing:
        level = "blocked" if queued else "attention"
        reasons.append(_reason(level, "credentials_missing", ", ".join(missing)))

    if platform == "linkedin":
        from .linkedin import token_status, version_status
        v_level, v_message = version_status(account.get("linkedin_version"), current.date())
        t_level, t_message = token_status(account, current)
        result["linkedin"] = {"version": {"value": account.get("linkedin_version"), "status": v_level,
                                          "message": v_message},
                              "authorization": {"expires_at": account.get("authorization_expires_at"),
                                                "status": t_level, "message": t_message}}
        if v_level == "error":
            reasons.append(_reason("blocked", "linkedin_version_unsupported", v_message))
        elif v_level == "warning":
            reasons.append(_reason("attention", "linkedin_version_ageing", v_message))
        if t_level == "expired":
            reasons.append(_reason("blocked", "linkedin_authorization_expired", t_message))
        elif t_level in ("warning", "unknown"):
            reasons.append(_reason("attention", f"linkedin_authorization_{t_level}", t_message))
    if platform in ("instagram", "threads") and account.get("backend") is None:
        # The send preflight already refuses an expired token; this says so before a send is due. While the
        # registry keeps the adapter manual nothing can be sent with the token, so expiry is only attention
        # then, and an unrecorded expiry is not reported at all (the account may hold no token).
        from . import instagram, threads
        t_level, t_message = (instagram if platform == "instagram" else threads).token_status(account, current)
        result[platform] = {"authorization": {"expires_at": account.get("authorization_expires_at"),
                                              "status": t_level, "message": t_message}}
        if t_level == "expired":
            reasons.append(_reason("blocked" if adapter != "manual" else "attention",
                                   f"{platform}_authorization_expired", t_message))
        elif t_level == "warning" or (t_level == "unknown" and adapter != "manual"):
            reasons.append(_reason("attention", f"{platform}_authorization_{t_level}", t_message))
    if platform == "mastodon" and account.get("backend") is None:
        result["mastodon"] = {"server": account.get("server"),
                              "max_characters_pinned": account.get("max_characters") is not None}

    providers = providers_for(account)
    max_per_24h, min_gap = effective_caps(caps, account_id, *cli_caps, providers=providers)
    attempts = queue["send_attempts_rolling_24h_by_account"].get(account_id, 0)
    start = (current - timedelta(hours=24)).isoformat(timespec="seconds")
    last = _last_attempt(con, account_id, start, now)
    gap_ok_at = None
    if last:
        gap_ok_at = (datetime.fromisoformat(parse_utc(last)) + timedelta(minutes=min_gap)).isoformat(timespec="seconds")
    result["budget"] = {"attempts_rolling_24h": attempts, "max_per_24h": max_per_24h,
                        "remaining": max(0, max_per_24h - attempts), "min_gap_minutes": min_gap,
                        "last_attempt_at": last,
                        "next_attempt_allowed_at": gap_ok_at if gap_ok_at and gap_ok_at > now else None,
                        "source": "strictest of CLI defaults and caps file" if caps else "CLI defaults"}
    if attempts >= max_per_24h and queued:
        reasons.append(_reason("attention", "rolling_budget_exhausted",
                               f"{attempts}/{max_per_24h} attempts in 24h; queued posts wait"))
    # Issue #11: tighten-only caps.json v2 provider values and any active 429 cooldown.
    rate = effective_rate_limits(caps, providers)
    # #11: also a cooldown recorded by another account of the same Postiz workspace.
    from .ratelimit import bluesky_session_usage, cooldown_scopes
    from .storage import active_provider_cooldown
    cooldown = active_provider_cooldown(con, account_id, now, scopes=cooldown_scopes(account, strict=False))
    result["rate_limits"] = {"providers": list(providers), **rate,
                             "preflight_retries_note": ("Bluesky createSession is never retried"
                                                        if platform == "bluesky" and account.get("backend") is None
                                                        else "read-only GET preflight only; writes are never retried")}
    if account.get("postiz_workspace"):
        result["rate_limits"]["postiz_workspace"] = account["postiz_workspace"]
    if platform == "bluesky" and account.get("backend") is None:
        try:  # read-only local counter; ids and counts only
            result["rate_limits"]["create_session_budget"] = bluesky_session_usage(
                con, account, now=current, registry=registry)
        except ValidationError:
            result["rate_limits"]["create_session_budget"] = None
    result["provider_cooldown"] = cooldown
    if cooldown:
        reasons.append(_reason("attention", "provider_cooldown_active",
                               f"until {cooldown['until']} (source {cooldown['source']}); unclaimed posts wait, "
                               "nothing is resent"))
        if cooldown["ceiling_clamped"]:
            reasons.append(_reason("attention", "provider_cooldown_ceiling_clamped",
                                   "the provider asked for a delay above 24 h; clamped to 24 h. Check the account "
                                   "at the provider"))

    result["queue"] = {status: counts.get(status, 0) for status in
                       ("draft", "approved", "scheduled", "sending", "needs_review", "published", "cancelled")}
    review = [p for p in queue["ambiguous_needs_review"] if p["account_id"] == account_id]
    stale_sending = [p for p in queue["stale_sending"] if p["account_id"] == account_id]
    overdue = [p for p in queue["overdue_scheduled"] if p["account_id"] == account_id]
    rebind = [p for p in queue["approval_rebind_required"] if p["account_id"] == account_id]
    result["needs_review"] = len(review)
    result["approval_rebind_required"] = len(rebind)
    result["stale_sending"] = len(stale_sending)
    result["overdue_scheduled"] = len(overdue)
    if review:
        reasons.append(_reason("attention", "needs_review", f"{len(review)} uncertain send(s); reconcile manually"))
    if stale_sending:
        reasons.append(_reason("attention", "stale_sending",
                               f"{len(stale_sending)} post(s) stuck in sending; check the native account, then reconcile"))
    if overdue:
        reasons.append(_reason("attention", "overdue_scheduled", f"{len(overdue)} scheduled post(s) past due"))
    if rebind:
        reasons.append(_reason("attention", "approval_rebind_required",
                               f"{len(rebind)} approved/scheduled post(s) refused until re-approved: "
                               f"{', '.join(p['post_id'] for p in rebind)}"))
    for item in reasons:
        item["account_id"] = account_id
    result["status"] = _worst(r["level"] for r in reasons)
    result["reasons"] = reasons
    return result


def build_health(con, accounts: dict[str, dict] | None, *, accounts_error: str | None = None,
                 now: str | None = None, stale_minutes: int = 30, caps: dict | None = None,
                 caps_path: str | None = None, caps_error: str | None = None,
                 cli_caps: tuple[int, int] | None = None,
                 env=None, registry: dict | None = None,
                 max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Overall ``ok | attention | blocked`` readiness from local state only."""
    env = os.environ if env is None else env
    now = parse_utc(now or utc_now())
    current = datetime.fromisoformat(now)
    cli_caps = cli_caps or (CLI_DEFAULTS["max_per_24h"], CLI_DEFAULTS["min_gap_minutes"])
    reasons: list[dict] = []
    try:
        registry = registry if registry is not None else load_registry()
        stale = stale_entries(registry, max_age_days=max_age_days, today=current.date())
    except ValidationError as exc:
        registry, stale = None, []
        reasons.append(_reason("blocked", "capability_registry_invalid", str(exc)))
    queue = queue_health(con, now=now, stale_minutes=stale_minutes, key_mode=approval_key_mode(con, env))
    paused = is_paused(con)
    opt_in = live_opt_in(env)
    environment = runtime_environment(env)  # allowlisted value, "unset" or "unrecognized"
    if paused:
        reasons.append(_reason("blocked", "emergency_pause_active", "run 'reachweaver resume' deliberately"))
    keyed = queue["approval_keyed_binding"]  # #7 D7: keyed mode on, key not usable here
    if keyed["required"] and keyed["key_available"] is False:
        reasons.append(_reason("blocked", "approval_key_unavailable",
                               (keyed["key_error"] or "keyed approval mode is on and REACHWEAVER_APPROVAL_SECRET "
                                "is not provided") + "; every claim is refused until it is"))
    ledger_view = queue["approval_ledger"]  # wave 9 item 1: replay ledger audit (post ids and fixed codes)
    for code in sorted({p["reason"] for p in ledger_view["problems"]}):
        ids = ", ".join(str(p["post_id"]) for p in ledger_view["problems"] if p["reason"] == code and p["post_id"])
        reasons.append(_reason("attention", code, ids or "approval ledger missing, unreadable or its hash chain broken"))
    if caps_error:
        reasons.append(_reason("blocked", "caps_file_invalid",
                               f"{caps_error}; publish --live refuses to run until it is fixed"))
    if accounts_error:
        reasons.append(_reason("blocked", "accounts_config_unloadable", accounts_error))
    elif not accounts:
        reasons.append(_reason("attention", "no_accounts_configured"))

    per_status: dict[str, dict] = {}
    for row in con.execute("SELECT account_id, status, COUNT(*) AS n FROM posts GROUP BY account_id, status"):
        per_status.setdefault(row["account_id"], {})[row["status"]] = row["n"]
    rows = []
    if registry is not None:
        for account in (accounts or {}).values():
            rows.append(account_health(con, account, registry=registry, stale=stale, queue=queue,
                                       counts=per_status.get(account["id"], {}), caps=caps,
                                       cli_caps=cli_caps, now=now, env=env))
    configured = set(accounts or {})
    orphans = sorted(a for a, c in per_status.items()
                     if a not in configured and any(c.get(s) for s in ("approved", "scheduled", "sending", "needs_review")))
    if orphans and not accounts_error:
        reasons.append(_reason("attention", "queued_posts_for_unconfigured_accounts", ", ".join(orphans)))
    covered = {row["account_id"] for row in rows}
    unlisted = [p["post_id"] for p in queue["approval_rebind_required"] if p["account_id"] not in covered]
    if unlisted:  # per-account rows cover the rest; never let a refused post go unreported
        reasons.append(_reason("attention", "approval_rebind_required",
                               f"{len(unlisted)} post(s) outside configured accounts: {', '.join(unlisted)}"))
    stray = [c["account_id"] for c in queue["provider_cooldowns"] if c["account_id"] not in covered]
    if stray:
        reasons.append(_reason("attention", "provider_cooldown_active",
                               f"cooldown for account(s) outside configured accounts: {', '.join(stray)}"))
    letters = dead_letter(con, now=now)
    all_reasons = reasons + [r for row in rows for r in row["reasons"]]
    overall = _worst(r["level"] for r in all_reasons)
    return {
        "report": REPORT, "observed_at": now, "overall": overall, "reasons": all_reasons,
        "global": {"emergency_paused": paused, "live_opt_in_env": opt_in,
                   "runtime_environment": environment, "credential_source": credential_source(env),
                   "registry": {"reviewed_on": registry["reviewed_on"] if registry else None,
                                "stale_platforms": stale, "max_age_days": max_age_days},
                   "caps_file": caps_path, "stale_sending_minutes": stale_minutes,
                   "approval_keyed_binding": keyed, "approval_ledger": ledger_view,
                   "status_counts": queue["status_counts"]},
        "accounts": rows, "unconfigured_accounts_with_active_posts": orphans,
        "approval_rebind_required": {"total": len(queue["approval_rebind_required"]),
                                     "posts": queue["approval_rebind_required"],
                                     "steps": list(REAPPROVAL_STEPS)},
        "provider_cooldowns": queue["provider_cooldowns"],
        "dead_letter": letters, "note": NOTE,
    }


def render_health(report: dict) -> str:
    """Compact human summary; ``--json`` carries every field."""
    g = report["global"]
    lines = [f"OVERALL: {report['overall'].upper()} (observed {report['observed_at']})",
             f"Emergency pause: {'ON' if g['emergency_paused'] else 'off'}; live opt-in env: "
             f"{'enabled' if g['live_opt_in_env'] else 'disabled'}; REACHWEAVER_ENV {g['runtime_environment']} "
             f"(live requires prod); credential source {g['credential_source']}; registry reviewed {g['registry']['reviewed_on']}; "
             f"caps file: {g['caps_file'] or 'none'}"]
    for row in report["accounts"]:
        b = row["budget"]
        lines.append(f"{row['account_id']}: {row['platform']}; adapter={row['adapter']}; status={row['status']}; "
                     f"attempts {b['attempts_rolling_24h']}/{b['max_per_24h']} per 24h, gap {b['min_gap_minutes']} min; "
                     f"needs_review={row['needs_review']} stale_sending={row['stale_sending']}; "
                     f"env missing: {', '.join(row['credentials']['missing']) or 'none'}")
    for reason in report["reasons"]:
        where = f"{reason['account_id']}: " if reason.get("account_id") else ""
        detail = f" ({reason['detail']})" if reason.get("detail") else ""
        lines.append(f"  {reason['level'].upper()} {where}{reason['code']}{detail}")
    rebind = report["approval_rebind_required"]
    if rebind["total"]:
        lines.append(f"Re-approval required: {rebind['total']} post(s) refused until re-approved "
                     "(unapprove, show, approve):")
        lines += [f"  - {p['post_id']} {p['platform']} {p['account_id']} {p['status']}: {p['reason']}"
                  for p in rebind["posts"]]
    for cooldown in report.get("provider_cooldowns", []):
        lines.append(f"Provider cooldown: {cooldown['account_id']} until {cooldown['until']} "
                     f"(source {cooldown['source']}{', clamped to 24 h' if cooldown['ceiling_clamped'] else ''}); "
                     "unclaimed posts wait, nothing is resent")
    letters = report["dead_letter"]
    if letters["total"]:
        lines.append(f"Dead letter: {letters['total']} needs_review post(s); run 'reachweaver dead-letter' for steps")
    lines.append(report["note"])
    return "\n".join(lines)


def render_dead_letter(report: dict) -> str:
    lines = [f"DEAD LETTER: {report['total']} needs_review post(s) (observed {report['observed_at']})"]
    for group in report["groups"]:
        lines.append(f"[{group['code']}] {group['count']} post(s), oldest {group['oldest_age_minutes']} min")
        lines.append(f"  Suggested: {group['suggested_action']}")
        for post in group["posts"]:
            lines.append(f"  - {post['post_id']} {post['platform']} {post['account_id']} "
                         f"since {post['since']} ({post['age_minutes']} min) [{post['send_class']}]")
    if report["groups"]:
        classes = sorted({post["send_class"] for group in report["groups"] for post in group["posts"]})
        lines.append("Send classification (guidance only; every post still needs reconcile):")
        lines += [f"  {name}: {CLASS_GUIDANCE[name]}" for name in classes]
        lines.append("Reconcile steps for every post:")
        lines += [f"  {i}. {step}" for i, step in enumerate(RECONCILE_STEPS, start=1)]
    lines.append(report["note"])
    return "\n".join(lines)


def exit_code(report: dict) -> int:
    """Nonzero only when blocked; attention is informational for monitors."""
    return 1 if report["overall"] == "blocked" else 0
