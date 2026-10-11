"""Verified-identity binding at approve time (wave 9 item 2, owner-approved 2026-10-10).

``approve --verify-identity --online`` runs the same read-only ``connections.check_connection`` the
live sender uses, then stores ``approved_identity``: a SHA-256 fingerprint over the account id, the
platform and the normalized identity fields of the local account config that the check just
compared with the remote account (handle, server, bot or channel names, person/user/page/channel
ids). The fingerprint is bound into the approval hash under tag ``approval/id-1`` (see
``storage.approval_payload``). Before any request, the live sender recomputes the fingerprint
from the current config and ``storage`` refuses a mismatch with a fixed reason code.

Stored: one 64-hex digest. Never stored: a token, a remote response, the handle itself.
What it proves: WHICH account the approver saw verified. What it does not prove: WHO approved
(the CLI cannot tell a human from an agent; see docs/THREAT-MODEL.md T8). Everything here is pure
and offline except ``verify_identity_online``, which only the approve command calls.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from urllib.parse import urlparse

from .policy import ValidationError

IDENTITY_TAG = "reachweaver.storage.approval/id-1"
FINGERPRINT_LABEL = "reachweaver.identity/1"
IDENTITY_MODE_SETTING = "approval_identity_mode"
IDENTITY_MODE_VALUE = "required"

# Config keys that name the remote identity, per platform (the ones connections.py compares).
IDENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "bluesky": ("handle", "service"),
    "mastodon": ("handle", "server"),
    "telegram": ("bot_username", "chat_id", "channel_username"),
    "linkedin": ("person_id",),
    "instagram": ("ig_user_id", "handle"),
    "threads": ("threads_user_id", "handle"),
    "facebook": ("page_id", "page_name"),
    "tiktok": ("open_id", "display_name"),
    "youtube": ("channel_id",),
}
_REQUIRED = {"bluesky": ("handle",), "mastodon": ("handle", "server"), "telegram": ("bot_username", "chat_id"),
             "linkedin": ("person_id",), "instagram": ("ig_user_id",), "threads": ("threads_user_id",),
             "facebook": ("page_id",), "tiktok": ("open_id",), "youtube": ("channel_id",)}

IDENTITY_UNAVAILABLE = "approval_identity_unavailable"
IDENTITY_MISMATCH = "approval_identity_mismatch_reapproval_required"
IDENTITY_MISSING = "approval_identity_missing_reapproval_required"
IDENTITY_REQUIRED = "approval_identity_required"
IDENTITY_CODES = (IDENTITY_UNAVAILABLE, IDENTITY_MISMATCH, IDENTITY_MISSING, IDENTITY_REQUIRED)
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def _normalize(key: str, value: object) -> str:
    text = str(value).strip()
    if key == "server":
        text = urlparse(text).hostname or text
    return text.casefold() if key == "chat_id" else text.lstrip("@").casefold()


def identity_fingerprint(account: Mapping) -> str:
    """SHA-256 over account id, platform and the normalized identity fields. Pure, offline.

    Raises ``ValidationError(approval_identity_unavailable)`` (a fixed code, no field values) when
    the account has no identity fields to bind, e.g. a Postiz-backed account or an unknown platform.
    """
    platform, account_id = account.get("platform"), account.get("id")
    if account.get("backend") is not None or platform not in IDENTITY_KEYS or not isinstance(account_id, str):
        raise ValidationError(IDENTITY_UNAVAILABLE)
    fields = {}
    for key in IDENTITY_KEYS[platform]:
        value = account.get(key)
        if value is None or not str(value).strip():
            if key in _REQUIRED[platform]:
                raise ValidationError(IDENTITY_UNAVAILABLE)
            continue
        fields[key] = _normalize(key, value)
    payload = {"binding": FINGERPRINT_LABEL, "account_id": account_id, "platform": platform, "fields": fields}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def current_fingerprint(account: Mapping | None) -> str | None:
    """The fingerprint of ``account`` now, or None when it cannot be computed (callers fail closed)."""
    if account is None:
        return None
    try:
        return identity_fingerprint(account)
    except ValidationError:
        return None


def is_fingerprint(value: object) -> bool:
    return isinstance(value, str) and bool(_FINGERPRINT.fullmatch(value))


def verify_identity_online(account: Mapping) -> str:
    """Run the read-only identity check once, then return the fingerprint it vouches for.

    Same call class as ``connect-check --online``. A failure raises and nothing is returned, so
    the caller writes nothing. The remote response is discarded by ``check_connection``.
    """
    from .connections import check_connection
    check_connection(dict(account))
    return identity_fingerprint(account)


def refusal_text(reason: str, post_id: str) -> str:
    """Operator text for an identity reason code; fixed strings, no handle, id or remote value."""
    steps = (f"re-approve after review: reachweaver unapprove {post_id}; reachweaver show {post_id}; "
             f"reachweaver approve {post_id} --verify-identity --online")
    if reason == IDENTITY_MISMATCH:
        return (f"{reason}: the account identity configured for post {post_id} is not the one verified when it "
                f"was approved; {steps}")
    if reason == IDENTITY_MISSING:
        return (f"{reason}: identity binding is required (approval-mode --require-identity) and post {post_id} "
                f"was approved without a verified identity; {steps}")
    if reason == IDENTITY_UNAVAILABLE:
        return (f"{reason}: this account has no identity fields that can be bound (a Postiz-backed account, or "
                "an incomplete account entry)")
    return (f"{reason}: identity binding is required (approval-mode --require-identity); approve with "
            "--verify-identity --online. Nothing approved")
