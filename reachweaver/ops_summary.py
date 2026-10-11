"""One redacted, read-only operations report (issue #26): health + queue + metrics + adapters.

``build_summary`` composes what already exists offline: ``health.build_health`` (adapter
capability per account, credential variable NAMES, budgets, cooldowns, pause, dead letters,
reasons) and ``ops_metrics.collect`` (claims, publishes, failures by send class, stale
sending, due age). Nothing is recomputed here and nothing is written or contacted.

The output is an allow-list, not a filter: account ids that match the id shape, known
platforms, fixed reason codes, counts and ISO times. Free text from ``build_health`` reason
details (which can carry post ids, paths or provider wording) is dropped, and the final
document is passed through ``privacy.redact_value`` as a second layer. Post text, handles,
URLs, provider error text and credential values never appear.
"""
from __future__ import annotations

import json
import re

from .accounts import ACCOUNT_ID
from .health import build_health
from .ops_metrics import collect
from .policy import PLATFORMS
from .privacy import redact_value

REPORT = "reachweaver_ops_summary"
SCHEMA_VERSION = 1
NOTE = ("LOCAL STATE ONLY: no provider was contacted, nothing was retried or changed, and credential "
        "values were never read into this report. Counts are not proof that a remote post exists or that "
        "a token still works. An alert is a prompt for the owner, never a reason to resend.")
_QUEUE_KEYS = ("draft", "approved", "scheduled", "sending", "needs_review", "published", "cancelled")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")


def _account(value: object) -> str:
    return value if isinstance(value, str) and ACCOUNT_ID.fullmatch(value) else "invalid"


def _platform(value: object) -> str:
    return value if isinstance(value, str) and value in PLATFORMS else "other"


def _code(value: object) -> str:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else "other"


def _env_name(value: object) -> str:
    return value if isinstance(value, str) and _ENV_NAME.fullmatch(value) else "other"


def _totals(metrics: dict) -> dict[str, int]:
    """Sum every sample of each metric family (name without the ``reachweaver_`` prefix)."""
    return {family["name"].removeprefix("reachweaver_"): sum(int(s["value"]) for s in family["samples"])
            for family in metrics["metrics"] if family["samples"]}


def build_summary(con, accounts: dict[str, dict] | None, *, accounts_error: str | None = None,
                  now: str | None = None, since: str | None = None, stale_minutes: int = 30,
                  caps: dict | None = None, caps_path: str | None = None, caps_error: str | None = None,
                  optout: list[dict] | None = None, optout_configured: bool = False,
                  env=None) -> dict:
    health = build_health(con, accounts, accounts_error=accounts_error, now=now, stale_minutes=stale_minutes,
                          caps=caps, caps_path=caps_path, caps_error=caps_error, env=env)
    metrics = collect(con, now=health["observed_at"], since=since, stale_minutes=stale_minutes,
                      optout=optout, optout_configured=optout_configured)
    glob = health["global"]
    rows = []
    for row in health["accounts"]:
        budget, cooldown = row["budget"], row["provider_cooldown"]
        rows.append({
            "account_id": _account(row["account_id"]), "platform": _platform(row["platform"]),
            "adapter": row["adapter"] if row["adapter"] in ("live_text", "backend", "manual") else "other",
            "registry_status": _code(str(row["registry"]["status"]).replace("-", "_")),
            "registry_stale": bool(row["registry"]["stale"]),
            "credentials_missing": sorted(_env_name(n) for n in row["credentials"]["missing"]),
            "budget": {"attempts_rolling_24h": int(budget["attempts_rolling_24h"]),
                       "max_per_24h": int(budget["max_per_24h"]), "remaining": int(budget["remaining"]),
                       "min_gap_minutes": int(budget["min_gap_minutes"])},
            "provider_cooldown_until": cooldown["until"] if cooldown else None,
            "queue": {k: int(row["queue"].get(k, 0)) for k in _QUEUE_KEYS},
            "needs_review": int(row["needs_review"]), "stale_sending": int(row["stale_sending"]),
            "overdue_scheduled": int(row["overdue_scheduled"]),
            "approval_rebind_required": int(row["approval_rebind_required"]),
            "status": row["status"] if row["status"] in ("ok", "attention", "blocked") else "other",
        })
    reasons = [{"level": r["level"], "code": _code(r["code"]),
                "account_id": _account(r["account_id"]) if r.get("account_id") else None}
               for r in health["reasons"]]
    letters = health["dead_letter"]
    classes: dict[str, int] = {}
    for group in letters["groups"]:
        for post in group["posts"]:
            name = _code(post["send_class"])
            classes[name] = classes.get(name, 0) + 1
    summary = {
        "report": REPORT, "schema_version": SCHEMA_VERSION, "observed_at": health["observed_at"],
        "window": metrics["window"], "overall": health["overall"],
        "global": {"emergency_paused": bool(glob["emergency_paused"]),
                   "live_opt_in_env": bool(glob["live_opt_in_env"]),
                   "runtime_environment": _code(glob["runtime_environment"]),
                   "stale_sending_minutes": stale_minutes,
                   "registry_reviewed_on": glob["registry"]["reviewed_on"],
                   "stale_platforms": sorted(_platform(p) for p in glob["registry"]["stale_platforms"]),
                   "status_counts": {_code(k): int(v) for k, v in glob["status_counts"].items()},
                   "optout_list": metrics["optout_list"]},
        "reasons": reasons, "accounts": rows,
        "dead_letter": {"total": int(letters["total"]),
                        "by_code": {_code(g["code"]): int(g["count"]) for g in letters["groups"]},
                        "by_send_class": dict(sorted(classes.items()))},
        "metric_totals": dict(sorted(_totals(metrics).items())),
        "note": NOTE,
    }
    return redact_value(summary)


def render_json(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True) + "\n"


def render_text(summary: dict) -> str:
    g = summary["global"]
    lines = [f"OPS SUMMARY: {summary['overall'].upper()} (observed {summary['observed_at']})",
             f"Emergency pause: {'ON' if g['emergency_paused'] else 'off'}; live opt-in env: "
             f"{'enabled' if g['live_opt_in_env'] else 'disabled'}; environment {g['runtime_environment']}; "
             f"registry reviewed {g['registry_reviewed_on']}"]
    for row in summary["accounts"]:
        b = row["budget"]
        missing = ", ".join(row["credentials_missing"]) or "none"
        lines.append(f"{row['account_id']}: {row['platform']}; adapter={row['adapter']}; status={row['status']}; "
                     f"attempts {b['attempts_rolling_24h']}/{b['max_per_24h']} per 24h; "
                     f"needs_review={row['needs_review']} stale_sending={row['stale_sending']} "
                     f"overdue={row['overdue_scheduled']}; env missing: {missing}")
    for r in summary["reasons"]:
        where = f"{r['account_id']}: " if r["account_id"] else ""
        lines.append(f"  {r['level'].upper()} {where}{r['code']}")
    dl = summary["dead_letter"]
    if dl["total"]:
        lines.append(f"Dead letter: {dl['total']} needs_review post(s); run 'reachweaver dead-letter' for steps")
    totals = summary["metric_totals"]
    lines.append("Totals: " + ", ".join(f"{k}={v}" for k, v in totals.items()))
    lines.append(summary["note"])
    return "\n".join(lines)


def exit_code(summary: dict) -> int:
    """Nonzero only when blocked, as for ``health``."""
    return 1 if summary["overall"] == "blocked" else 0
