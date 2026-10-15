"""Local account disconnect and reconnect (public issue #38; owner-reviewed and merged 2026-10-15, PR #72).

``disconnect --account ID`` is a tighten-only local action: it records that this installation must not publish
to the account any more. The send claim and the live pre-check refuse the account (``account_disconnected``) and the
post stays approved or scheduled. It deletes nothing and revokes nothing remotely, because ReachWeaver never stores a
credential: it lists the credential variable NAMES the account reads and the steps the owner takes at the provider.

``reconnect --account ID`` lifts it and is human-only: it needs an interactive terminal on stdin and stdout, then a
fresh random challenge typed on ``/dev/tty`` (the same ritual that turns off human confirmation, #7 D6). That is
friction against a scripted or prompt-injected caller, not a security boundary, like the other approval safeguards.

The state is one ``settings`` row per account, ``account_disconnected:<id>``, holding the UTC time. Any stored value
counts as disconnected (fail closed).
"""
from __future__ import annotations

import secrets
from collections.abc import Mapping

from .policy import ValidationError
from .storage import event, parse_utc, utc_now

SETTING_PREFIX = "account_disconnected:"
DISCONNECTED = "account_disconnected"


def _name(account_id: str) -> str:
    if not isinstance(account_id, str) or not account_id or len(account_id) > 80:
        raise ValidationError("Invalid account id")
    return SETTING_PREFIX + account_id


def disconnected_since(con, account_id: str) -> str | None:
    """The UTC time the account was disconnected, or the raw stored value when it is unreadable; None when connected."""
    row = con.execute("SELECT value FROM settings WHERE name=?", (_name(account_id),)).fetchone()
    return None if row is None else str(row["value"])


def disconnected_accounts(con) -> list[str]:
    return sorted(r["name"][len(SETTING_PREFIX):] for r in con.execute(
        "SELECT name FROM settings WHERE name LIKE ? ESCAPE '\\'", (SETTING_PREFIX.replace("_", "\\_") + "%",)))


def refusal(account_id: str, since: str | None = None) -> str:
    when = f" since {since}" if since else ""
    return (f"{DISCONNECTED}: account {account_id} was disconnected on this installation{when}; nothing is claimed or "
            f"sent for it. Reconnect deliberately, in a terminal: reachweaver reconnect --account {account_id}")


def disconnect(con, account_id: str, now: str | None = None) -> bool:
    """Record the disconnect. Returns False when it was already recorded (the first time is kept)."""
    name = _name(account_id)
    if con.execute("SELECT 1 FROM settings WHERE name=?", (name,)).fetchone() is not None:
        return False
    when = parse_utc(now or utc_now())
    con.execute("INSERT INTO settings(name,value) VALUES(?,?)", (name, when))
    event(con, "account_disconnected", None, f"account={account_id}", at=when)
    return True


def reconnect(con, account_id: str, *, confirm=None, now: str | None = None, **io) -> bool:
    """Lift the disconnect after the human-presence ritual. Returns False when the account was not disconnected.

    ``confirm`` is the ritual (default: ``confirmation.confirm_prefix`` on a fresh random challenge, which needs a
    terminal); tests pass their own. A failed ritual raises and changes nothing.
    """
    name = _name(account_id)
    if con.execute("SELECT 1 FROM settings WHERE name=?", (name,)).fetchone() is None:
        return False
    challenge = secrets.token_hex(32)  # fresh each time, so no earlier output can answer it
    print(f"Reconnecting {account_id}. Challenge: {challenge}")
    if confirm is None:
        from .confirmation import confirm_prefix
        confirm = confirm_prefix
    confirm(challenge, prompt=f"reconnect account {account_id}", **io)
    con.execute("DELETE FROM settings WHERE name=?", (name,))
    event(con, "account_reconnected", None, f"account={account_id} confirmed=tty", at=parse_utc(now) if now else None)
    return True


def credential_names(account: Mapping) -> list[str]:
    """The credential variable NAMES this account reads (never values), so the owner knows what to revoke and unset."""
    from .accounts import BACKEND_CREDENTIAL_SUFFIX, account_credential_suffixes
    if account.get("backend") is not None:
        return [account["env_prefix"] + BACKEND_CREDENTIAL_SUFFIX]
    return [account["env_prefix"] + suffix for suffix in account_credential_suffixes(account)]


def revocation_steps(account: Mapping) -> list[str]:
    """What the owner does next. Generic on purpose: provider screens change, and none is claimed here."""
    names = credential_names(account)
    steps = [
        f"1. In your own terminal, stop using the credential: unset {', '.join(names) if names else '(this account reads no credential variable)'} "
        "and remove it from your secrets store or the systemd LoadCredential= source.",
        f"2. At the {account.get('platform', 'platform')} provider, sign in yourself and revoke the app password, access token, "
        "bot token or app authorization you created for ReachWeaver. ReachWeaver cannot revoke it for you and never held your password.",
        "3. Check the provider's list of authorized apps afterwards; the revocation is complete only when the provider shows it.",
        "4. Posts already approved for this account stay approved and are now refused; cancel the ones you no longer want "
        "(reachweaver cancel POST_ID).",
        "5. To use the account again, create a new credential, then `reachweaver reconnect --account ID` in a terminal and `connect-check --online`.",
    ]
    return steps
