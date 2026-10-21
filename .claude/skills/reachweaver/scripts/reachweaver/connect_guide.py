"""Guided account connection: a status table and a one-account-at-a-time walkthrough with the owner present.

What this module does and does not do, deliberately:

* It never asks for, reads, prints or stores a password, token or any other secret. Credentials come into the
  process only through the existing accessor (``accounts.missing_credentials`` and the adapters' own
  ``check_connection``), exactly as for ``connect-check``. The walkthrough prints variable NAMES and the official
  sign-in page; the owner signs in, solves any verification or CAPTCHA, and creates the credential there.
* It never drives a browser, logs in, or posts. The only network call is the same read-only identity check that
  ``connect-check --online`` makes, and only after the owner answers ``y`` for that one account.
* It changes no approval, dry-run, pause or reconciliation rule. Publishing still needs ``approve`` per post.

The functions take their input/output/check callables as arguments so the whole flow is testable offline.
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping

from .accounts import account_credential_suffixes, missing_credentials
from .capabilities import require_capability
from .policy import ValidationError

# Platform -> (label, how to create the credential). Sources: docs/ACCOUNT-ONBOARDING.md (variables and
# scopes) and the provider pages it cites. Page addresses that depend on the account use {server}.
GUIDES: dict[str, dict] = {
    "bluesky": {
        "label": "Bluesky",
        "page": "https://bsky.app/settings/app-passwords",
        "steps": ("Sign in to Bluesky yourself and complete any verification it asks for.",
                  "Create an App Password with a clear name such as 'reachweaver'. Never use your main password.",
                  "Keep the generated password; Bluesky shows it once."),
        "doc": "docs/ACCOUNT-ONBOARDING.md",
    },
    "mastodon": {
        "label": "Mastodon",
        "page": "{server}/settings/applications",
        "steps": ("Sign in to your Mastodon server yourself and complete any verification it asks for.",
                  "Create a new application with the scopes read:accounts and write:statuses "
                  "(add write:media only if you will attach images).",
                  "Copy the application's access token."),
        "doc": "docs/MASTODON.md",
    },
    "telegram": {
        "label": "Telegram",
        "page": "https://t.me/BotFather",
        "steps": ("In Telegram, talk to @BotFather and create a bot (or reuse your own).",
                  "Add the bot as an administrator of your channel with permission to post messages.",
                  "Copy the bot token BotFather gives you."),
        "doc": "docs/ACCOUNT-ONBOARDING.md",
    },
    "linkedin": {
        "label": "LinkedIn (personal profile)",
        "page": "https://www.linkedin.com/developers/apps",
        "steps": ("Sign in to LinkedIn yourself and complete any verification it asks for.",
                  "In your own LinkedIn developer app, request the 'Share on LinkedIn' product.",
                  "Authorize with the scopes openid, profile and w_member_social, and copy the member access token "
                  "(it expires after about 60 days)."),
        "doc": "docs/LINKEDIN.md",
    },
}

# Account states, in the order the summary table lists them.
READY = "ready_to_check"
NEEDS = "needs_credentials"
MANUAL = "manual_only"
DISCONNECTED = "disconnected"
VERIFIED = "verified"
FAILED = "check_failed"
SKIPPED = "skipped"

LABELS = {
    READY: "ready to check",
    NEEDS: "credentials missing",
    MANUAL: "manual only",
    DISCONNECTED: "disconnected here",
    VERIFIED: "connected and verified",
    FAILED: "check failed",
    SKIPPED: "skipped",
}

FAILURE_HINTS = (
    ("http_401", "The platform rejected the credential: it is expired, revoked or wrong."),
    ("http_403", "The platform refused the request: the credential may lack a needed permission."),
    ("identity_mismatch", "The credential belongs to a different account than the one in your accounts file."),
    ("missing_credentials", "A credential variable is not set in this terminal."),
    ("bluesky_create_session_budget_exhausted", "Too many Bluesky sign-ins recently; wait and try again later."),
)


def adapter_kind(account: Mapping, registry: dict) -> str:
    """'live_text', 'backend' or 'manual' from the offline capability registry (same rule as ``health``)."""
    platform = account["platform"]
    if account.get("backend") is not None:
        from .postiz import require_backend_capability
        try:
            require_backend_capability(platform, registry=registry)
        except ValidationError:
            return "manual"
        return "backend"
    try:
        require_capability(platform, "text", "publish", registry=registry)
    except ValidationError:
        return "manual"
    return "live_text"


def account_rows(accounts: Mapping[str, dict], registry: dict, *, disconnected: Iterable[str] = ()) -> list[dict]:
    """One offline status row per account. Names only; never a value."""
    gone = set(disconnected)
    rows = []
    for account_id, account in accounts.items():
        kind = adapter_kind(account, registry)
        missing = missing_credentials(account) if kind != "manual" else []
        if kind == "manual":
            state, next_step = MANUAL, (f"No connection needed: ReachWeaver cannot send to this platform yet. "
                                        f"Use `manual-export --platform {account['platform']}`.")
        elif account_id in gone:
            state, next_step = DISCONNECTED, f"Run `reconnect --account {account_id}` in your own terminal to lift it."
        elif missing:
            state, next_step = NEEDS, f"Run `connect --guide --account {account_id}` for the steps."
        else:
            state, next_step = READY, f"Run `connect --guide --account {account_id}` to confirm the sign-in."
        rows.append({"account": account_id, "platform": account["platform"], "state": state,
                     "adapter": kind, "missing": missing, "next": next_step})
    return rows


def render_rows(rows: list[dict]) -> str:
    if not rows:
        return ("No accounts yet. Add one: reachweaver account-add bluesky (public details only), "
                "or set up a folder: reachweaver workspace --init .")
    width = max(len(r["account"]) for r in rows)
    lines = []
    for r in rows:
        label = f"{LABELS[r['state']]}: {', '.join(r['missing'])}" if r["missing"] and r["state"] == NEEDS else LABELS[r["state"]]
        lines.append(f"  {r['account']:<{width}}  {r['platform']:<10}  {label}")
    return "\n".join(lines)


def guide_for(account: Mapping) -> dict:
    guide = dict(GUIDES.get(account["platform"], {}))
    if not guide:
        return {"label": account["platform"], "page": "", "steps": (), "doc": "docs/ACCOUNT-ONBOARDING.md"}
    server = str(account.get("server") or "https://YOUR-SERVER").rstrip("/")
    guide["page"] = guide["page"].format(server=server)
    return guide


def variable_names(account: Mapping) -> list[str]:
    return [account["env_prefix"] + suffix for suffix in account_credential_suffixes(account)]


def explain_failure(code: str) -> str:
    for prefix, text in FAILURE_HINTS:
        if code.startswith(prefix):
            return text
    return "See docs/TROUBLESHOOTING.md for this code."


def run_guided(accounts: Mapping[str, dict], registry: dict, *, only: str | None, ask: Callable[[str], str],
               say: Callable[[str], None], check: Callable[[dict], str | None],
               disconnected: Iterable[str] = ()) -> list[dict]:
    """Walk through the accounts one at a time. ``check`` returns None on success or a short error code.

    ``ask`` returns the owner's typed answer. The owner can stop at any time with ``q``.
    """
    rows = account_rows(accounts, registry, disconnected=disconnected)
    if only is not None:
        rows = [r for r in rows if r["account"] == only]
    results: list[dict] = []
    total = len(rows)
    for number, row in enumerate(rows, start=1):
        account = accounts[row["account"]]
        guide = guide_for(account)
        say("")
        say(f"[{number}/{total}] {row['account']} ({guide['label']})")
        result = dict(row)
        if row["state"] == MANUAL:
            say(f"  {row['next']}")
        elif row["state"] == DISCONNECTED:
            say(f"  {row['next']}")
        elif row["state"] == NEEDS:
            say("  Credentials are not set in this terminal yet. To connect this account:")
            for index, step in enumerate(guide["steps"], start=1):
                say(f"   {index}. {step}")
            if guide["page"]:
                say(f"  Official page: {guide['page']}")
            say("  Then, in a terminal the AI does not control, set these variables WITHOUT typing the value into "
                "history, and run this guide again for this account:")
            for name in row["missing"]:
                say(f"      read -r -s -p '{name}: ' {name}; export {name}; printf '\\n'")
            say(f"      reachweaver connect --guide --account {row['account']}")
            say(f"  More detail: {guide['doc']}")
            say("  Never paste a password, token or code into Claude, a chat or an issue.")
        else:  # READY
            say("  The credential variables are set. Nothing is posted by this check; it only asks the platform "
                "who the credential belongs to.")
            answer = ask(f"  Contact {guide['label']} now to confirm the sign-in? [y = yes, n = skip, q = stop] ").strip().lower()
            if answer == "q":
                result["state"] = SKIPPED
                results.append(result)
                say("  Stopped. The remaining accounts were not checked.")
                break
            if answer == "y":
                code = check(account)
                if code is None:
                    result["state"] = VERIFIED
                    say(f"  Verified: {row['account']} is connected to the account named in your accounts file.")
                else:
                    result["state"] = FAILED
                    result["error"] = code
                    say(f"  Check failed ({code}). {explain_failure(code)}")
            else:
                result["state"] = SKIPPED
                say("  Skipped.")
        results.append(result)
    return results


def render_summary(results: list[dict]) -> str:
    lines = ["", "Summary", render_rows(results), ""]
    verified = [r["account"] for r in results if r["state"] == VERIFIED]
    if verified:
        lines.append("Next: write a draft for a verified account, review it, then approve it yourself:")
        lines.append(f"  reachweaver draft --account {verified[0]} --file examples/example-post.txt")
        lines.append("  reachweaver show POST_ID     # read the exact text")
        lines.append("  reachweaver approve POST_ID --approver YOUR_NAME")
        lines.append("Every post still needs your approval. Connecting an account does not turn that off.")
    else:
        lines.append("Next: finish the steps above for one account, then run `reachweaver connect --guide` again.")
    return "\n".join(lines)
