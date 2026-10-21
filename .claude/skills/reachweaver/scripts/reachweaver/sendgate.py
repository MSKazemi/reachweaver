"""Send gate for direct Python callers of ``publishers.publish`` / ``publish_video`` (wave 9 item 3).

``storage.claim_sending`` is the only production code that calls ``register_claim``, and only
after its atomic claim committed: the emergency pause, the approval hash/MAC binding, the
provider cooldown and the rolling send caps have all passed under its lock. ``publish()`` and
``publish_video()`` call ``consume`` before any capability, credential or network step, so a
direct call without a preceding claim of exactly this post is refused with a fixed code.

Honest limit (THREAT-MODEL T8/T9): Python offers no true privacy. A caller in the same process can
reach this module's registry and forge an entry; the gate stops accidental, copy-pasted and naive
bypass, not a determined same-process attacker. There is no environment-variable or flag escape
hatch: offline tests mint entries through ``register_claim`` itself, the function the claim uses.

Refusal messages are fixed codes; they never echo content, account metadata or credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import dataclass

from .policy import ValidationError, require_live_gate

# Same bounds the CLI enforces on a live run (`_run_publish`): 1..10 attempts/24h, >= 15 min gap.
MIN_GAP_MINUTES = 15
MAX_ATTEMPTS_PER_24H = 10
TICKET_TTL_SECONDS = 900  # a claim is sent immediately; a stale one is not honoured

TICKET_REQUIRED = "send_ticket_required"
TICKET_MISMATCH = "send_ticket_mismatch"
TICKET_CONSUMED = "send_ticket_consumed"
TICKET_EXPIRED = "send_ticket_expired"
CAPS_REQUIRED = "send_caps_required"
LIVE_GATE_CLOSED = "send_live_gate_closed"
_CODES = {
    TICKET_REQUIRED: "use the claim path (storage.claim_sending) before publishing; nothing was sent",
    TICKET_MISMATCH: "the claim does not match this account, content, media or bound send options; nothing was sent",
    TICKET_CONSUMED: "this claim was already used; nothing was sent",
    TICKET_EXPIRED: "the claim is too old; nothing was sent",
    CAPS_REQUIRED: "the claim carried no live send caps; nothing was sent",
    LIVE_GATE_CLOSED: "REACHWEAVER_ALLOW_LIVE=1 and REACHWEAVER_ENV=prod are required; nothing was sent",
}


def _refusal(code: str) -> ValidationError:
    return ValidationError(f"{code}: {_CODES[code]}")


@dataclass
class _Entry:
    account_id: str
    platform: str
    content_sha256: str
    media_digest: str
    max_per_24h: int | None
    min_gap_minutes: int
    issued: float
    send_options: str | None = None  # sheet items 5/9: the fingerprint bound at approval, or None
    used: bool = False


_LOCK = threading.Lock()
_ENTRIES: dict[str, _Entry] = {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def media_digest(items) -> str:
    """Digest of the ordered attachment hashes; ``None`` and ``[]`` are the same (no media)."""
    hashes = [i.get("sha256") if isinstance(i, dict) else None for i in (items or [])]
    return _sha(json.dumps(hashes, sort_keys=True, separators=(",", ":")))


def register_claim(post_id: str, *, account_id: str, platform: str, content: str, media,
                   max_per_24h: int | None, min_gap_minutes: int, send_options: str | None = None) -> None:
    """Record one committed claim (called by ``claim_sending`` only, after COMMIT).

    ``send_options``: the row's bound send-options fingerprint (``approved_send_options``), or None."""
    with _LOCK:
        _ENTRIES[post_id] = _Entry(account_id, platform, _sha(content), media_digest(media),
                                   max_per_24h, min_gap_minutes, time.monotonic(), send_options or None)


def reset() -> None:
    """Forget every claim (tests)."""
    with _LOCK:
        _ENTRIES.clear()


def _same_send_options(bound: str | None, account: dict) -> bool:
    from .send_options import fingerprint
    current = fingerprint(account)
    if bound is None or current is None:
        return bound is None and current is None
    return hmac.compare_digest(bound, current)


def consume(post_id: str, account: dict, content: str, media=None, *, env=None) -> None:
    """Fail closed unless this exact post was claimed in this process under live caps; mark it used.

    Checked first in ``publish`` / ``publish_video``, before any registry, credential or network
    step. The live opt-in is the same ``policy.require_live_gate`` the CLI runs. A mismatch does not
    consume the claim, so the legitimate sender can still use it.

    Sheet items 5/9: the account's current send-options fingerprint (``send_options.fingerprint``: Mastodon
    visibility, language, spoiler_text; Postiz server and integration) must equal the one bound to the
    claimed row (both None for an account without send options), so the request is built from exactly the
    values that were approved.
    """
    with _LOCK:
        entry = _ENTRIES.get(post_id) if isinstance(post_id, str) else None
        if entry is None:
            raise _refusal(TICKET_REQUIRED)
        if entry.used:
            raise _refusal(TICKET_CONSUMED)
        if time.monotonic() - entry.issued > TICKET_TTL_SECONDS:
            raise _refusal(TICKET_EXPIRED)
        if (not isinstance(account, dict) or entry.account_id != account.get("id")
                or entry.platform != account.get("platform") or not isinstance(content, str)
                or entry.content_sha256 != _sha(content) or entry.media_digest != media_digest(media)
                or not _same_send_options(entry.send_options, account)):
            raise _refusal(TICKET_MISMATCH)
        if (type(entry.max_per_24h) is not int or not 1 <= entry.max_per_24h <= MAX_ATTEMPTS_PER_24H
                or entry.min_gap_minutes < MIN_GAP_MINUTES):
            raise _refusal(CAPS_REQUIRED)
        try:
            require_live_gate(env)
        except ValidationError:
            raise _refusal(LIVE_GATE_CLOSED) from None
        entry.used = True
