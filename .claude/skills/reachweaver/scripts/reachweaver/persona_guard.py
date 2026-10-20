"""Wrong-account guard: which of your personas does an account belong to, and which one should this post use?

You may run several identities on one network (for example an Instagram account for work and one for family). Say
which persona a post is for (``--persona work``, or ``preferences.default_persona`` in the profile) and the guard
refuses an account that belongs to another persona, then names the account or username to switch to.

A persona is read from the account's ``"persona"`` field in ``accounts.json``. An account without one is matched by
its username against the usernames in ``config/profile.json``. If neither tells which persona it is, the guard
refuses and says how to fix that (fail closed: you asked for a check, so an unknown answer is not a pass).

It only ever adds a refusal; it never approves, changes a post or opens a network connection
(``SEND_PATH = None``). It does not touch the approval hash, the dry-run gate or credential handling.
"""
from __future__ import annotations

from collections.abc import Mapping

from .accounts import PERSONA_ID
from .policy import ValidationError
from .profile import canonical_platform, normalize_username

SEND_PATH = None  # Asserted in tests: this module can never send, approve or schedule.
USERNAME_KEYS = ("handle", "channel_username", "page_name", "display_name")


def account_username(account: Mapping) -> str | None:
    """The public name of an account, the way its config spells it (first of handle, channel, page, display name)."""
    for key in USERNAME_KEYS:
        value = account.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("@")
    return None


def _profile_handles(profile: Mapping | None, platform: str) -> list[dict]:
    if not profile:
        return []
    return [h for h in profile.get("handles", []) if canonical_platform(h.get("platform")) == platform]


def account_persona(account: Mapping, profile: Mapping | None) -> str | None:
    """The persona an account belongs to: its ``persona`` field, else the persona whose profile username it uses."""
    if isinstance(account.get("persona"), str):
        return account["persona"]
    name, platform = account_username(account), canonical_platform(account.get("platform"))
    if name is None or platform is None:
        return None
    for handle in _profile_handles(profile, platform):
        if handle.get("status") != "unavailable" and normalize_username(handle["username"]) == normalize_username(name):
            return handle["persona"]
    return None


def _check_persona_id(persona: str) -> None:
    if not isinstance(persona, str) or not PERSONA_ID.fullmatch(persona):
        raise ValidationError(f"Invalid persona {persona!r}: use a lowercase letter, then lowercase letters, "
                              "digits or '-' (2-31 characters), for example work or family")


def route(platform: str, persona: str, accounts: Mapping[str, Mapping], profile: Mapping | None) -> dict:
    """Which account and username to use for ``persona`` on ``platform``. Status: ok, conflict or missing."""
    canonical = canonical_platform(platform)
    if canonical is None:
        raise ValidationError("Unknown platform. `reachweaver account-advisor` lists the platforms")
    _check_persona_id(persona)
    matches = [{"id": str(a.get("id")), "username": account_username(a), "persona": persona}
               for a in accounts.values()
               if canonical_platform(a.get("platform")) == canonical and account_persona(a, profile) == persona]
    handle = next((dict(h, username=h["username"].strip().lstrip("@"))
                   for h in _profile_handles(profile, canonical) if h.get("persona") == persona), None)
    usable = handle if handle and handle.get("status") == "claimed" else None
    kit = f"reachweaver account-kit --platform {canonical} --persona {persona}"
    # An account with no username in accounts.json (LinkedIn often has none) matched through its explicit
    # "persona" field: its username is unknown, which is not evidence of a different one.
    named = [m for m in matches if m["username"]]
    if usable and named and not any(normalize_username(m["username"] or "") == normalize_username(usable["username"])
                                    for m in named):
        status = "conflict"
        advice = (f"Your profile says the {persona} username on {canonical} is @{usable['username']}, but "
                  f"accounts.json connects @{named[0]['username']} as {named[0]['id']}. One of them is wrong: "
                  "fix the profile (`reachweaver profile --handle ...`) or the account entry.")
    elif matches:
        status = "ok"
        first = matches[0]
        advice = f"Use account {first['id']}" + (f" (@{first['username']})" if first["username"] else "") + "."
        if usable and not named:
            advice += (f" accounts.json gives it no username, so it was matched by its \"persona\" field and not "
                       f"compared with @{usable['username']} in your profile.")
    elif usable:
        status = "ok"
        advice = (f"Use @{usable['username']} on {canonical} ({persona}). It is not connected to ReachWeaver: add it "
                  f'to accounts.json with "persona": "{persona}" to publish or draft with it.')
    else:
        status = "missing"
        if handle:
            advice = (f"Your {persona} username on {canonical} is only '{handle['status']}' (@{handle['username']}). "
                      f"Create the account yourself, then record it with `reachweaver profile --handle "
                      f"{canonical}:{persona}=NAME`; `{kit}` prepares the sign-up details.")
        else:
            advice = f"No {persona} account or username on {canonical} yet. Run: {kit}"
    return {"platform": canonical, "persona": persona, "status": status, "accounts": matches,
            "profile_handle": ({"username": handle["username"], "status": handle["status"]} if handle else None),
            "advice": advice}


def check_account(account: Mapping, persona: str, accounts: Mapping[str, Mapping],
                  profile: Mapping | None) -> str | None:
    """None when ``account`` belongs to ``persona``; otherwise one refusal sentence that says what to do instead."""
    _check_persona_id(persona)
    known = sorted(profile.get("personas", {})) if profile else []
    if profile and persona not in known:
        return (f"'{persona}' is not a persona in your profile ({', '.join(known) or 'none defined'}). "
                "Add it with `reachweaver profile --set personas.{0}.display_name=...`".format(persona))
    resolved = account_persona(account, profile)
    name = account_username(account)
    label = f"account {account.get('id')}" + (f" (@{name})" if name else "")
    if resolved == persona:
        return None
    if resolved is None:
        return (f"Wrong-account check: cannot tell which persona {label} belongs to, so it is not safe to use it for "
                f"'{persona}'. Add \"persona\": \"{persona}\" to its entry in accounts.json if it is your {persona} "
                "account, or record its username with `reachweaver profile --handle`.")
    platform = canonical_platform(account.get("platform")) or str(account.get("platform"))
    advice = route(platform, persona, accounts, profile)
    if advice["accounts"]:
        first = advice["accounts"][0]
        fix = f"Switch to account {first['id']}" + (f" (@{first['username']})" if first["username"] else "") + "."
    elif advice["profile_handle"] and advice["profile_handle"]["status"] == "claimed":
        fix = (f"Your {persona} username on {platform} is @{advice['profile_handle']['username']}, but it is not "
               f"connected as an account: add it to accounts.json with \"persona\": \"{persona}\".")
    else:
        fix = (f"You have no {persona} account on {platform} yet. Run: "
               f"reachweaver account-kit --platform {platform} --persona {persona}")
    return (f"Wrong account for persona '{persona}': {label} is your '{resolved}' persona on {platform}. {fix} "
            "Nothing was changed.")
