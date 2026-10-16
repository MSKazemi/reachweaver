"""Readable ``doctor`` and ``health`` output for a person at a terminal.

Piped output of both commands is unchanged (scripts parse it, and ``health --json`` carries every field).
At a terminal they get a short verdict, a coloured aligned table and a "Next:" line. This module only
formats data the commands already computed; it reads no environment, database or network.
"""
from __future__ import annotations

from . import term
from .accounts import missing_credentials
from .policy import SUPPORTED_LIVE

LEVEL_COLOUR = {"ok": "green", "attention": "yellow", "blocked": "red"}
# The only reason codes that stop live sending without affecting drafting, review or a dry run.
CREDENTIAL_ONLY = {"credentials_missing"}


def account_status(account: dict) -> str:
    if account.get("backend") is not None:
        return f"backend={account['backend']} (opt-in; acceptance needs human reconcile)"
    return "supported (text)" if account["platform"] in SUPPORTED_LIVE else "planned/manual only"


def platform_notes(account: dict) -> list[str]:
    """Adapter-specific doctor notes (API version, token expiry, "disabled until a live receipt", ...)."""
    platform, has_backend = account["platform"], account.get("backend") is not None
    if platform == "linkedin":
        from .linkedin import doctor_notes as linkedin_notes
        return list(linkedin_notes(account))
    if platform == "instagram" and not has_backend:
        from .instagram import doctor_notes as instagram_notes
        return list(instagram_notes(account))
    if platform == "tiktok" and "open_id" in account:
        from .tiktok import doctor_notes as tiktok_notes
        return list(tiktok_notes(account))
    if platform == "youtube" and not has_backend:
        from .youtube import doctor_notes as youtube_notes
        return list(youtube_notes(account))
    if platform == "threads" and not has_backend:
        from .threads import doctor_notes as threads_notes
        return list(threads_notes(account))
    if platform == "facebook" and not has_backend:
        from .facebook import doctor_notes as facebook_notes
        return list(facebook_notes(account))
    return []


def doctor_findings(accounts: dict) -> list[dict]:
    """One entry per account: its status, the NAMES of missing credential variables and adapter notes."""
    return [{"name": name, "platform": account["platform"], "status": account_status(account),
             "missing": list(missing_credentials(account)), "notes": platform_notes(account)}
            for name, account in accounts.items()]


def doctor_plain_lines(findings: list[dict]) -> list[str]:
    """The exact per-account lines ``doctor`` has always printed (piped output)."""
    lines: list[str] = []
    for f in findings:
        lines.append(f"{f['name']}: {f['platform']}; {f['status']}; "
                     f"env variables missing: {', '.join(f['missing']) or 'none'}")
        lines += [f"  {f['name']}: {note}" for note in f["notes"]]
    return lines


def render_doctor(findings: list[dict], settings: list[str], stream) -> str:
    """Terminal view of ``doctor``: a verdict, one table row per account, then adapter notes and settings."""
    ready = sum(1 for f in findings if not f["missing"])
    out = []
    if findings:
        verdict = (term.style(f"{ready} of {len(findings)} account(s) have every credential set", "green", stream)
                   if ready == len(findings) else
                   term.style(f"{len(findings) - ready} of {len(findings)} account(s) are missing credentials", "yellow", stream))
        out += [verdict, ""]
        rows = [[f["name"], f["platform"], f["status"],
                 "all set" if not f["missing"] else f"{len(f['missing'])} missing"] for f in findings]
        out.append(term.table(["ACCOUNT", "PLATFORM", "ADAPTER", "CREDENTIALS"], rows, stream, fixed=(0,),
                              paint=lambda col, text: ("green" if text == "all set" else "yellow") if col == 3 else None))
        for f in findings:
            if f["missing"]:
                out.append(f"  {f['name']} is missing: {', '.join(f['missing'])}")
        for f in findings:
            out += [f"  {f['name']}: {term.clean(note)}" for note in f["notes"]]
    else:
        out.append("No accounts are configured.")
    out += ["", *settings, ""]
    if any(f["missing"] for f in findings):
        out.append("Credentials are needed only for live sending; drafting, review and dry runs work without them.")
    out.append("Next: reachweaver health  (one readiness verdict, local state only)")
    return "\n".join(out)


def _reason_line(reason: dict, stream) -> str:
    where = f"{reason['account_id']}: " if reason.get("account_id") else ""
    detail = f" ({reason['detail']})" if reason.get("detail") else ""
    level = reason["level"]
    return f"  {term.style(level.upper(), LEVEL_COLOUR.get(level, 'bold'), stream)} {where}{reason['code']}{detail}"


def render_health_sheet(report: dict, stream) -> str:
    """Terminal view of ``health``: verdict first, a per-account table, then only what needs attention."""
    g = report["global"]
    overall = report["overall"]
    out = [f"Readiness: {term.style(overall.upper(), LEVEL_COLOUR.get(overall, 'bold'), stream)}"
           f"   (observed {report['observed_at']})",
           f"Emergency pause {'ON' if g['emergency_paused'] else 'off'} · live opt-in "
           f"{'enabled' if g['live_opt_in_env'] else 'disabled'} · environment {g['runtime_environment']} "
           f"(live sends need prod) · credentials from {g['credential_source']}", ""]
    if report["accounts"]:
        rows = []
        for row in report["accounts"]:
            b = row["budget"]
            missing = row["credentials"]["missing"]
            rows.append([row["account_id"], row["platform"], row["status"],
                         f"{b['attempts_rolling_24h']}/{b['max_per_24h']}", str(row["needs_review"]),
                         f"{len(missing)} missing" if missing else "all set"])
        out.append(term.table(["ACCOUNT", "PLATFORM", "STATUS", "SENDS/24H", "NEEDS REVIEW", "CREDENTIALS"], rows, stream,
                              fixed=(0,), paint=lambda col, text: LEVEL_COLOUR.get(text) if col == 2 else
                              (("green" if text == "all set" else "yellow") if col == 5 else None)))
        out.append("")
    reasons = report["reasons"]
    if reasons:
        out.append("What needs attention")
        out += [_reason_line(r, stream) for r in reasons]
        out.append("")
    rebind = report["approval_rebind_required"]
    if rebind["total"]:
        out.append(f"Re-approval required for {rebind['total']} post(s): unapprove, show, then approve again.")
        out += [f"  - {p['post_id']} {p['platform']} {p['account_id']}: {term.clean(str(p['reason']))}" for p in rebind["posts"]]
        out.append("")
    for cooldown in report.get("provider_cooldowns", []):
        out.append(f"Provider cooldown: {cooldown['account_id']} until {cooldown['until']}; unclaimed posts wait, nothing is resent")
    out.append(next_step(report))
    out.append(report["note"])
    return "\n".join(out)


def next_step(report: dict) -> str:
    """One sentence saying what to do, never a command that sends or approves."""
    if report["dead_letter"]["total"]:
        return (f"Next: reachweaver dead-letter  ({report['dead_letter']['total']} send(s) ended in an unknown state; "
                "check the platform first, never resend blindly)")
    codes = {r["code"] for r in report["reasons"]}
    if report["global"]["emergency_paused"]:
        return "Next: the emergency stop is ON. Resume deliberately with 'reachweaver resume' when you are ready."
    if codes and codes <= CREDENTIAL_ONLY:
        return ("Next: only credentials are missing. Drafting, review and dry runs still work; "
                "set them (names via 'reachweaver doctor') only when you want live sends.")
    if report["overall"] == "ok":
        return "Next: nothing needs attention. 'reachweaver start' shows what to do with your queue."
    return "Next: work through the list above, then run 'reachweaver health' again."
