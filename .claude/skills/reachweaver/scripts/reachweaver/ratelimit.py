"""Provider rate limits: Retry-After parsing, send-failure classification, per-account cooldowns
and bounded pre-claim preflight backoff (issue #11).

Owner-approved 2026-10-08 as proposed in docs/SECURITY-REVIEW-PROPOSAL-7-11.md part 2 (D11-D16).
What this module never does (D16, a reaffirmation, RFC 9110 section 9.2.2): retry, resend or
release a claimed post. A post that reached ``claim_sending`` still ends in ``published`` with a
receipt or in ``needs_review`` via ``storage.uncertain_send``, exactly as before. Everything here
only changes *when an unclaimed post may next be tried*:

* ``classify_send_failure`` labels a post-claim failure ``certain_not_sent`` (C1, the create
  request was never handed to the network), ``likely_not_created`` (C2, a documented or
  source-verified provider rejection ahead of the create handler) or ``ambiguous`` (C3,
  everything else). The label feeds the cooldown audit event and ``dead-letter`` guidance only
  (D11); it never changes a post's state.
* ``parse_retry_after`` and friends turn a bounded header or Telegram error body into an
  integer delay (D15). Raw header and body text never leave this module.
* ``record_rate_limit`` writes a ``provider_cooldown`` event whose deadline is clamped to
  [floor, 24 h] (D12). ``claim_sending`` and both live senders refuse other unclaimed posts of
  that account until the deadline passes.
* ``preflight_with_backoff`` retries a read-only identity check (GET only, never Bluesky
  ``createSession``) at most twice with full jitter, base 2 s, cap 30 s (D13). It runs before
  the claim, so it can never cause a duplicate post.

Owner-approved 2026-10-09 (docs/SECURITY-REVIEW-PROPOSAL-11-shared-cooldown.md). Both
additions are tighten-only: they can make a post wait longer, never shorter.

* ``cooldown_scopes``: an owner-declared ``postiz_workspace`` groups Postiz-backed accounts
  whose API keys belong to one Postiz organization. A 429 on one of them records the group
  scope, and every gate (dry run, pre-claim, ``claim_sending`` under its lock) consults the
  account AND its group. Postiz keys its create-post throttle by organization id
  (``ThrottlerBehindProxyGuard.getTracker`` returns ``org.id + '_posts'``, postiz-app
  ``libraries/nestjs-libraries/src/throttler/throttler.provider.ts`` at commit 4c5d42a, read
  2026-10-09, not copied); an API key or ``pos_`` OAuth token resolves to exactly one
  organization (``apps/backend/src/services/auth/public.auth.middleware.ts``). The scope never
  contains or derives from a credential.
* ``reserve_bluesky_sessions``: a local ``createSession`` counter per Bluesky account that
  refuses, before any request, when a run would exceed half of the documented limit
  (30 per 5 minutes and 300 per day per account, Bluesky "Rate Limits",
  https://bsky.network/docs/rate-limits, accessed 2026-10-09; ``platform_registry.json``
  bluesky ``rate_limits.facts``).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

from .policy import ValidationError

# D12 bounds. The floor can only be raised (caps.json v2 cooldown_floor_seconds); the ceiling
# is fixed so a hostile or buggy header cannot silence an account for longer than a day.
FLOOR_SECONDS = 60
CEILING_SECONDS = 24 * 3600
DEFAULT_SECONDS = 15 * 60  # no usable header and no documented reset (D12 "others")
# D15 bounds on what is read from an error response.
MAX_HEADER_CHARS = 64
TELEGRAM_BODY_LIMIT = 4096
# D13 bounds for the read-only pre-claim identity preflight.
PREFLIGHT_MAX_RETRIES = 2
PREFLIGHT_BASE_SECONDS = 2.0
PREFLIGHT_CAP_SECONDS = 30.0
RETRYABLE_PREFLIGHT_STATUS = frozenset({429, 500, 502, 503, 504})
# Bluesky's identity check is POST createSession, which spends its own 30/5 min and 300/day
# budget; it is never retried automatically (D13).
NO_PREFLIGHT_RETRY_PLATFORMS = frozenset({"bluesky"})

SOURCES = ("retry_after", "x_ratelimit_reset", "telegram_retry_after", "daily_reset", "default")
CERTAIN_NOT_SENT = "certain_not_sent"
LIKELY_NOT_CREATED = "likely_not_created"
AMBIGUOUS = "ambiguous"
SEND_CLASSES = (CERTAIN_NOT_SENT, LIKELY_NOT_CREATED, AMBIGUOUS)
# Direct adapters whose create-call 429 is answered before the create handler runs (C2). Sources
# in the proposal table 2.2: Bluesky PDS createRecord rateLimit, Mastodon RateLimiter rollback
# and Rack::Attack, Telegram flood control. LinkedIn and Postiz 429s stay ambiguous (C3).
LIKELY_NOT_CREATED_429 = frozenset({"bluesky", "mastodon", "telegram"})

_HTTP_STATUS = re.compile(r"(?:^|_)http_([1-5][0-9]{2})(?:_|$)")
T = TypeVar("T")


@dataclass(frozen=True)
class RetryHint:
    """A provider-supplied delay: integer seconds (unclamped, >= 0) and a source label only."""
    delay_seconds: int
    source: str


def _utc(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValidationError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _bounded_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and len(text) <= MAX_HEADER_CHARS else None


def _seconds_until(when: datetime, now: datetime) -> int | None:
    delta = (when - now).total_seconds()
    return int(math.ceil(delta)) if delta > 0 else None


def parse_retry_after(value: object, now: datetime | None = None) -> int | None:
    """RFC 9110 section 10.2.3 ``Retry-After``: delay-seconds or an HTTP-date; None if unusable.

    delay-seconds is one or more ASCII digits (``0`` is valid and later raised to the floor).
    An HTTP-date must parse to a timezone-aware time in the future. Negative numbers, other
    text, a past date and values longer than 64 characters are ignored. Large values are
    returned as-is and clamped (with ``health`` attention) by ``cooldown_deadline``.
    """
    text = _bounded_text(value)
    if text is None:
        return None
    if text.isascii() and text.isdigit():
        return int(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None or when.tzinfo is None or when.utcoffset() is None:
        return None  # RFC 9110 HTTP-date is always GMT; "-0000" (unknown zone) is refused
    return _seconds_until(when, _utc(now))


def parse_x_ratelimit_reset(value: object, now: datetime | None = None) -> int | None:
    """Mastodon ``X-RateLimit-Reset``: an ISO 8601 timestamp with a zone, in the future."""
    text = _bounded_text(value)
    if text is None:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None or when.utcoffset() is None:
        return None
    return _seconds_until(when, _utc(now))


def parse_telegram_retry_after(body: object) -> int | None:
    """Telegram ``ResponseParameters.retry_after`` from at most 4 KB of an error body.

    Only ``parameters.retry_after`` (a non-negative integer) is extracted; a larger, non-JSON
    or differently shaped body is ignored as a whole, and nothing else from it is kept.
    """
    if not isinstance(body, (bytes, bytearray)) or len(body) > TELEGRAM_BODY_LIMIT:
        return None
    try:
        data = json.loads(bytes(body).decode("utf-8"))
    except (UnicodeError, ValueError):
        return None
    params = data.get("parameters") if isinstance(data, dict) else None
    value = params.get("retry_after") if isinstance(params, dict) else None
    return value if type(value) is int and value >= 0 else None


def _header(headers: object, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    try:
        value = getter(name)
    except Exception:  # a hostile mapping must never break error handling
        return None
    return value if isinstance(value, str) else None


def hint_from_http_error(error: object, *, telegram_body: bool = False,
                         now: datetime | None = None) -> RetryHint | None:
    """Delay hint from a 429/503 ``HTTPError``: headers first, then (Telegram only) the body.

    Read only to compute a delay (D15). Bluesky ``ratelimit-reset`` is not used: its unit has
    not been verified against a live sample, so the default applies (proposal 2.3).
    """
    try:
        status = getattr(error, "code", None)
        if status not in (429, 503):
            return None
        current = _utc(now)
        headers = getattr(error, "headers", None)
        seconds = parse_retry_after(_header(headers, "Retry-After"), current)
        if seconds is not None:
            return RetryHint(seconds, "retry_after")
        if telegram_body:
            reader = getattr(error, "read", None)
            body = reader(TELEGRAM_BODY_LIMIT + 1) if callable(reader) else None
            seconds = parse_telegram_retry_after(body)
            if seconds is not None:
                return RetryHint(seconds, "telegram_retry_after")
        seconds = parse_x_ratelimit_reset(_header(headers, "X-RateLimit-Reset"), current)
        if seconds is not None:
            return RetryHint(seconds, "x_ratelimit_reset")
    except Exception:  # never let a malformed error response replace the sanitized error code
        return None
    return None


def http_status(exc: BaseException) -> int | None:
    """HTTP status of a sanitized error: the ``status`` attribute, else ``http_NNN`` in its code."""
    status = getattr(exc, "status", None)
    if type(status) is int:
        return status
    match = _HTTP_STATUS.search(str(exc))
    return int(match.group(1)) if match else None


def is_rate_limited(exc: BaseException) -> bool:
    return http_status(exc) == 429


def classify_send_failure(platform: str, backend: str | None, exc: BaseException, *,
                          write_started: bool) -> str:
    """C1/C2/C3 label of a post-claim failure (proposal 2.2, D11). Guidance only, never state.

    ``write_started`` comes from local control flow: the adapter marks it immediately before
    handing the create request to the network (``publishers.mark_write_started``).
    """
    if not write_started:
        return CERTAIN_NOT_SENT
    if backend is None and platform in LIKELY_NOT_CREATED_429 and is_rate_limited(exc):
        return LIKELY_NOT_CREATED
    return AMBIGUOUS


def next_utc_midnight(now: datetime) -> datetime:
    current = _utc(now)
    return datetime(current.year, current.month, current.day, tzinfo=timezone.utc) + timedelta(days=1)


def default_delay(platform: str, backend: str | None, now: datetime) -> tuple[int, str]:
    """Delay when the provider sent no usable hint: LinkedIn's documented daily reset at
    00:00 UTC for a direct LinkedIn call, otherwise 15 minutes (D12)."""
    if backend is None and platform == "linkedin":
        current = _utc(now)
        return int(math.ceil((next_utc_midnight(current) - current).total_seconds())), "daily_reset"
    return DEFAULT_SECONDS, "default"


def cooldown_deadline(hint: RetryHint | None, *, platform: str, backend: str | None,
                      now: datetime | None = None, floor_seconds: int = FLOOR_SECONDS) -> dict:
    """{"until", "source", "seconds", "ceiling_clamped"}: the hint (or default) clamped to
    [floor, 24 h]. ``ceiling_clamped`` is True when the provider asked for more than 24 h."""
    if type(floor_seconds) is not int or not FLOOR_SECONDS <= floor_seconds <= CEILING_SECONDS:
        raise ValidationError(f"cooldown floor must be {FLOOR_SECONDS}..{CEILING_SECONDS} seconds")
    current = _utc(now)
    if hint is not None and hint.source in SOURCES:
        seconds, source = hint.delay_seconds, hint.source
    else:
        seconds, source = default_delay(platform, backend, current)
    clamped = seconds > CEILING_SECONDS
    seconds = max(floor_seconds, min(CEILING_SECONDS, seconds))
    until = (current + timedelta(seconds=seconds)).replace(microsecond=0)
    return {"until": until.isoformat(timespec="seconds"), "source": source,
            "seconds": seconds, "ceiling_clamped": clamped}


def record_rate_limit(con, account: Mapping[str, Any], exc: BaseException, *, stage: str,
                      post_id: str | None = None, send_class: str | None = None,
                      floor_seconds: int = FLOOR_SECONDS, now: datetime | None = None) -> dict:
    """Write one ``provider_cooldown`` event for a 429 on ``account``; returns the cooldown.

    ``stage`` is ``preflight`` (pre-claim, ``post_id`` None) or ``send`` (after the claim; the
    claimed post is already in ``needs_review`` and stays there). Only the computed deadline,
    its source label and fixed codes are stored, never header or body text.
    """
    from .storage import record_provider_cooldown
    current = _utc(now)
    hint = getattr(exc, "retry_hint", None)
    deadline = cooldown_deadline(hint if isinstance(hint, RetryHint) else None,
                                 platform=str(account.get("platform", "")), backend=account.get("backend"),
                                 now=current, floor_seconds=floor_seconds)
    # Shared scope (#11): written so the account's whole Postiz workspace waits. An
    # unusable value never breaks this error path; the account itself is always covered.
    scopes = cooldown_scopes(account, strict=False)
    return record_provider_cooldown(con, str(account["id"]), deadline["until"], deadline["source"],
                                    stage=stage, post_id=post_id, send_class=send_class,
                                    ceiling_clamped=deadline["ceiling_clamped"],
                                    scope=scopes[0] if scopes else None,
                                    now=current.isoformat(timespec="seconds"))


# --------------------------------------------------------------------------- shared scopes (#11)
# Owner-declared Postiz workspace label: a lowercase slug, never a key, URL or credential name.
POSTIZ_WORKSPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
POSTIZ_WORKSPACE_SCOPE = "postiz-workspace:"


def cooldown_scopes(account: Mapping[str, Any], *, strict: bool = True) -> tuple[str, ...]:
    """Shared cooldown scopes of ``account`` beyond the account itself (#11).

    ``()`` without ``postiz_workspace`` (per-account cooldowns, as before). With it, the
    account must be Postiz-backed and the value a slug, else ``strict`` raises (gates fail
    closed: the post is skipped) and non-strict returns ``()`` (the 429 recording path).
    """
    if "postiz_workspace" not in account:
        return ()
    workspace = account.get("postiz_workspace")
    if account.get("backend") != "postiz" or not isinstance(workspace, str) \
            or not POSTIZ_WORKSPACE.fullmatch(workspace):
        if strict:
            raise ValidationError("postiz_workspace_invalid: a lowercase slug on a Postiz-backed account only")
        return ()
    return (POSTIZ_WORKSPACE_SCOPE + workspace,)


# --------------------------------------------------------------------------- Bluesky createSession budget
SESSION_ACTION = "com.atproto.server.createSession"
# Documented per-account limits (window_seconds, limit): Bluesky "Rate Limits",
# https://bsky.network/docs/rate-limits, accessed 2026-10-09, mirrored in platform_registry.json.
# Used when the registry cannot be read; the registry can only lower them.
SESSION_DOCUMENTED = ((300, 30), (86400, 300))
# Local budget = documented limit * 1/2 (integer floor): leaves headroom for other tools that use
# the same Bluesky account and for limits of non-Bluesky PDS hosts, which are not published.
SESSION_FRACTION = (1, 2)
SESSIONS_PER_PUBLISH = 2  # identity preflight + the session inside publishers.publish_bluesky
SESSION_STAGES = ("connect_check", "preflight", "send_reserved")


def session_windows(registry: Mapping[str, Any] | None = None) -> list[dict]:
    """[{"window_seconds", "documented_limit", "local_limit"}] for Bluesky ``createSession``.

    Tighten-only: each built-in window takes the lower of the built-in and any registry fact,
    and an extra registry window for the action is added. A missing or unreadable registry
    keeps the built-in values.
    """
    documented = dict(SESSION_DOCUMENTED)
    try:
        if registry is None:
            from .capabilities import load_registry
            registry = load_registry()
        entry = next(e for e in registry["platforms"] if e["id"] == "bluesky")
        facts = entry["rate_limits"].get("facts", [])
    except Exception:  # the built-in, documented values still apply
        facts = []
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("action") != SESSION_ACTION or fact.get("scope") != "account":
            continue
        window, limit = fact.get("window_seconds"), fact.get("limit")
        if type(window) is int and type(limit) is int and window > 0 and limit >= 0:
            documented[window] = min(documented.get(window, limit), limit)
    num, den = SESSION_FRACTION
    return [{"window_seconds": window, "documented_limit": limit, "local_limit": limit * num // den}
            for window, limit in sorted(documented.items())]


def session_scope(account: Mapping[str, Any]) -> str:
    """Counter key of one Bluesky account: a digest of service origin + handle, never stored raw.

    Two ReachWeaver entries for the same handle on the same service share one budget, as the
    provider counts per account.
    """
    from .accounts import bluesky_service
    handle = str(account.get("handle", "")).strip().lstrip("@").casefold()
    if not handle:
        raise ValidationError("bluesky_handle_missing: cannot meter createSession")
    digest = hashlib.sha256(f"{bluesky_service(dict(account))}\n{handle}".encode("utf-8")).hexdigest()
    return "bsky-account:" + digest[:16]


def reserve_bluesky_sessions(con, account: Mapping[str, Any], *, needed: int, stage: str,
                             now: datetime | None = None, registry: Mapping[str, Any] | None = None) -> dict:
    """Pre-network gate: reserve ``needed`` createSession calls or raise the budget refusal.

    Called before any Bluesky ``createSession`` request (``connect-check --online`` needs 1, a
    live publish needs ``SESSIONS_PER_PUBLISH``). Every reserved call is recorded before the
    request, whatever its outcome, so failed and refused sessions also count.
    """
    from .storage import reserve_create_sessions
    current = _utc(now).isoformat(timespec="seconds")
    windows = [(w["window_seconds"], w["local_limit"]) for w in session_windows(registry)]
    return reserve_create_sessions(con, str(account["id"]), session_scope(account), windows=windows,
                                   needed=needed, stage=stage, now=current)


def bluesky_session_usage(con, account: Mapping[str, Any], *, now: datetime | None = None,
                          registry: Mapping[str, Any] | None = None) -> list[dict]:
    """Read-only: [{"window_seconds", "documented_limit", "local_limit", "used"}] for the account."""
    from .storage import create_session_usage
    windows = session_windows(registry)
    usage = create_session_usage(con, str(account["id"]), session_scope(account),
                                 [(w["window_seconds"], w["local_limit"]) for w in windows],
                                 now=_utc(now).isoformat(timespec="seconds"))
    return [{**w, "used": u["used"]} for w, u in zip(windows, usage)]


def recorded_session_usage(con, account_id: str, *, now: datetime | None = None,
                           registry: Mapping[str, Any] | None = None) -> list[dict] | None:
    """Dry-run preview without account metadata (#5): usage under the account's last recorded
    counter key, or None when the account was never metered (then nothing would be refused)."""
    from .storage import create_session_usage, recorded_session_scope
    scope = recorded_session_scope(con, account_id)
    if scope is None:
        return None
    windows = session_windows(registry)
    usage = create_session_usage(con, account_id, scope,
                                 [(w["window_seconds"], w["local_limit"]) for w in windows],
                                 now=_utc(now).isoformat(timespec="seconds"))
    return [{**w, "used": u["used"]} for w, u in zip(windows, usage)]


def session_budget_problem(usage: list[dict], needed: int) -> str | None:
    """The refusal ``reserve_bluesky_sessions`` would raise for ``needed`` more calls, or None."""
    from .storage import SESSION_REFUSAL
    for window in usage:
        if window["used"] + needed > window["local_limit"]:
            return (f"{SESSION_REFUSAL} window={window['window_seconds']}s used={window['used']} "
                    f"needed={needed} local_limit={window['local_limit']}")
    return None


def retryable_preflight(exc: BaseException) -> bool:
    """Transient read failures only: 429, 5xx gateway/server errors, network or timeout."""
    return http_status(exc) in RETRYABLE_PREFLIGHT_STATUS or "network_or_timeout" in str(exc)


def preflight_retries(platform: str, configured: int = PREFLIGHT_MAX_RETRIES) -> int:
    """Retries allowed for this platform's identity preflight: never above 2, 0 for Bluesky."""
    if platform in NO_PREFLIGHT_RETRY_PLATFORMS:
        return 0
    return max(0, min(PREFLIGHT_MAX_RETRIES, configured))


# Indirection so tests never sleep and can seed the jitter.
_sleep: Callable[[float], None] = time.sleep
_rng = random.Random()


def backoff_delay(attempt: int, rng: random.Random | None = None) -> float:
    """AWS "full jitter": uniform(0, min(cap, base * 2**attempt)), attempt counted from 0."""
    return (rng or _rng).uniform(0.0, min(PREFLIGHT_CAP_SECONDS, PREFLIGHT_BASE_SECONDS * (2 ** attempt)))


def _notify_retry(on_retry: Callable[[int, float, str], None], retry: int, delay: float,
                  exc: BaseException) -> None:
    """Call a retry observer with a sanitized code; swallow every failure (log only, decision item 3 D3)."""
    try:
        from .worker_log import error_code  # lazy: the observer is optional and only the CLI loop passes one
        on_retry(retry, delay, error_code(exc))
    except Exception:  # noqa: BLE001 - an observer can never change the retry, delay, pause check or claim
        pass


def preflight_with_backoff(check: Callable[[], T], *, max_retries: int, paused: Callable[[], bool],
                           retry_on: type[BaseException] | tuple[type[BaseException], ...],
                           rng: random.Random | None = None,
                           sleep: Callable[[float], None] | None = None,
                           on_retry: Callable[[int, float, str], None] | None = None) -> T:
    """Run a read-only, pre-claim identity check with at most ``max_retries`` jittered retries.

    Never wrap a content write in this. A retry happens only for ``retry_on`` errors that
    ``retryable_preflight`` accepts. A server-supplied delay above the 30 s cap stops at once
    (the caller records a cooldown and prints BLOCKED). The emergency pause is re-checked
    before every retry, and an active pause ends the check with a ValidationError.

    ``on_retry(retry, delay_seconds, code)`` (issue #26 retry metrics, security decision item 3,
    owner-approved 2026-10-21): an observer called just before each sleep with the retry number
    (1, 2), the chosen delay and the sanitized error code (``worker_log.error_code``, never provider
    text). It cannot change the outcome: anything it raises is swallowed, and ``None`` (the default)
    leaves the behaviour byte-identical.
    """
    if type(max_retries) is not int or not 0 <= max_retries <= PREFLIGHT_MAX_RETRIES:
        raise ValidationError(f"preflight retries must be 0..{PREFLIGHT_MAX_RETRIES}")
    attempt = 0
    while True:
        try:
            return check()
        except retry_on as exc:
            if attempt >= max_retries or not retryable_preflight(exc):
                raise
            hint = getattr(exc, "retry_hint", None)
            server_delay = hint.delay_seconds if isinstance(hint, RetryHint) else None
            if server_delay is not None and server_delay > PREFLIGHT_CAP_SECONDS:
                raise
            if paused():
                raise ValidationError("Global emergency pause is active; preflight retry abandoned") from None
            delay = backoff_delay(attempt, rng)
            if server_delay is not None:
                delay = max(delay, float(server_delay))
            if on_retry is not None:
                _notify_retry(on_retry, attempt + 1, delay, exc)
            (sleep or _sleep)(delay)
            if paused():
                raise ValidationError("Global emergency pause is active; preflight retry abandoned") from None
            attempt += 1
