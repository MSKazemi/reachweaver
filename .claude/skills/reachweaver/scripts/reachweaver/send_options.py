"""Send-option binding at approve time (sheet items 5 and 9, owner-approved 2026-10-21).

Some account settings shape the request a send makes without being part of the post row:

* a direct Mastodon account: ``visibility`` (default ``public``), ``language`` and ``spoiler_text``,
  which ``publishers.mastodon_status_payload`` copies into the status request;
* a Postiz-backed account (any platform): ``postiz_base_url`` and ``postiz_integration_id``, which
  choose the Postiz server and the one integration that the backend's create request targets.

``approve`` stores a SHA-256 fingerprint of these effective values in ``posts.approved_send_options``
and binds it into the approval hash under tag ``approval/options-1`` (see
``storage.approval_payload``). Before any request, the live sender recomputes the fingerprint from the
account it is about to send with; ``storage`` refuses a difference with a fixed reason code, and the
send gate (``sendgate.consume``) refuses a ``publish()`` call whose account does not match the claimed
row. So changing these settings in ``accounts.json`` after approval needs a re-approval, exactly like a
text edit, and the request uses the values the approver saw.

Accounts without such settings (Bluesky, Telegram, LinkedIn, the video platforms) have no fingerprint:
nothing is stored and the approval payload is byte-identical to before. A Mastodon or Postiz post
approved before this binding existed has no fingerprint either; it is not grandfathered (decision 5b):
the live sender refuses it with ``approval_send_options_missing_reapproval_required`` until a human
re-approves it once.

Pure and offline: no network, no credential, no database. ``postiz_api_prefix`` is not bound: it only
selects the documented API path on the bound server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping

SEND_OPTIONS_TAG = "reachweaver.storage.approval/options-1"
FINGERPRINT_LABEL = "reachweaver.send-options/1"
SEND_OPTIONS_MISSING = "approval_send_options_missing_reapproval_required"
SEND_OPTIONS_CHANGED = "approval_send_options_changed_reapproval_required"
SEND_OPTIONS_UNAVAILABLE = "approval_send_options_unavailable"
SEND_OPTIONS_CODES = (SEND_OPTIONS_MISSING, SEND_OPTIONS_CHANGED, SEND_OPTIONS_UNAVAILABLE)
MASTODON_FIELDS = ("visibility", "language", "spoiler_text")
POSTIZ_FIELDS = ("postiz_base_url", "postiz_integration_id")
# Platforms whose every account (direct or through a backend) carries send options: a row of one of these
# without a stored fingerprint cannot be sent, whatever the account file says.
ALWAYS_BOUND_PLATFORMS = frozenset({"mastodon"})
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def bound_fields(account: Mapping | None) -> dict | None:
    """The effective send-shaping values of ``account``, or None when it has none to bind.

    A backend account binds the backend name and its routing fields (the backend never reads the Mastodon
    fields); a direct Mastodon account binds its three status options, with ``visibility`` defaulting to
    ``public`` exactly as the request does.
    """
    if account is None:
        return None
    backend = account.get("backend")
    if backend is not None:
        fields: dict = {"backend": str(backend)}
        for key in POSTIZ_FIELDS:
            value = account.get(key)
            fields[key] = None if value is None else str(value)
        return fields
    if account.get("platform") == "mastodon":
        return {"visibility": account.get("visibility", "public"),
                "language": account.get("language"), "spoiler_text": account.get("spoiler_text")}
    return None


def fingerprint(account: Mapping | None) -> str | None:
    """SHA-256 over account id, platform and :func:`bound_fields`; None when there is nothing to bind."""
    fields = bound_fields(account)
    if fields is None or account is None:
        return None
    payload = {"binding": FINGERPRINT_LABEL, "account_id": account.get("id"), "platform": account.get("platform"),
               "fields": fields}
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def is_fingerprint(value: object) -> bool:
    return isinstance(value, str) and bool(_FINGERPRINT.fullmatch(value))


def problem(stored: object, current: str | None) -> str | None:
    """Compare the fingerprint stored at approval with the sender's current one. Pure.

    ``current`` None means the sender's account has nothing to bind (or the caller passed none). A stored
    value with no or another current value is a change; no stored value while the account has send options
    is a pre-binding approval (no grandfathering).
    """
    if not stored:
        return SEND_OPTIONS_MISSING if current is not None else None
    if current is None or not hmac.compare_digest(str(stored), str(current)):
        return SEND_OPTIONS_CHANGED
    return None


def row_problem(row) -> str | None:
    """Offline, account-free check for reports (dry run, ``show``, ``health``): an approved/scheduled row of a
    platform whose accounts always carry send options, approved without a fingerprint."""
    if row is None or row["platform"] not in ALWAYS_BOUND_PLATFORMS:
        return None
    stored = row["approved_send_options"] if "approved_send_options" in row.keys() else None
    return None if stored else SEND_OPTIONS_MISSING


def describe(account: Mapping | None) -> str | None:
    """One line naming the bound values (no credential exists among them), or None."""
    fields = bound_fields(account)
    if fields is None:
        return None
    return " ".join(f"{key}={'-' if value is None else value}" for key, value in fields.items())


def refusal_text(reason: str, post_id: str) -> str:
    """Operator text for a send-options reason code; fixed strings plus the post id."""
    steps = (f"re-approve after review: reachweaver unapprove {post_id}; reachweaver show {post_id}; "
             f"reachweaver approve {post_id}")
    if reason == SEND_OPTIONS_CHANGED:
        return (f"{reason}: the account's send options (Mastodon visibility, language, spoiler_text; Postiz "
                f"server and integration) are not the ones bound when post {post_id} was approved; restore them "
                f"or {steps}")
    if reason == SEND_OPTIONS_MISSING:
        return (f"{reason}: post {post_id} was approved before the account's send options were bound "
                f"(no grandfathering); {steps}")
    return f"{reason}: the send options fingerprint is malformed. Nothing approved"
