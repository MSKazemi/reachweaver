"""Threads text publisher (issue #19). Owner-approved 2026-10-09; disabled until a live receipt.

The capability registry keeps Threads at ``reachweaver_adapter: "none"`` and ``text:
"api_gated"``, and ``policy.SUPPORTED_LIVE`` excludes it, so ``publishers.publish``,
``connections.check_connection`` and the CLI refuse Threads before any credential read or
request. This module only becomes reachable after the owner's Meta App Review, a supervised
first live receipt and a reviewed registry change (docs/SECURITY-REVIEW-PROPOSAL-19-threads.md).

Scope: one text-only post (``media_type=TEXT``) on the authenticated user's own profile. No
media, replies, quotes, polls, topic tags, link attachments, reply controls, country allowlists
or Instagram cross-share are ever sent.

Facts read 2026-10-09 from the official Threads API docs (developers.facebook.com):

* Two-step publish: ``POST /{threads-user-id}/threads`` (``media_type=TEXT``, ``text``) returns a
  media container ``id``; ``POST /{threads-user-id}/threads_publish`` (``creation_id``) returns
  the media ``id``. Both examples send form fields, ``access_token`` among them; base URL
  ``https://graph.threads.net/v1.0/``. "It is recommended to wait on average 30 seconds before
  publishing a Threads media container"; no exception for TEXT is stated. No idempotency key
  is documented. https://developers.facebook.com/docs/threads/posts
  https://developers.facebook.com/documentation/threads/reference/publishing.md
* Limits: "Text posts are limited to 500 characters"; emojis count as their UTF-8 bytes; "The
  number of links is restricted to 5 or less" (more fail at container creation from
  2025-12-22); without ``link_attachment`` "the first link made in a text-only post via the API
  is configured as the link attachment". https://developers.facebook.com/docs/threads/posts
* Container status ``GET /{container-id}?fields=status,error_message``: EXPIRED (not published
  within 24 hours), ERROR, FINISHED (ready), IN_PROGRESS, PUBLISHED; poll "once per minute, for
  no more than 5 minutes". https://developers.facebook.com/docs/threads/troubleshooting
* Quota ``GET /{threads-user-id}/threads_publishing_limit`` (``quota_usage``, ``config.
  quota_total``, ``config.quota_duration``); "Threads profiles are limited to 250 API-published
  posts within a 24-hour moving period"; the endpoint "requires the threads_basic and
  threads_content_publish permissions".
  https://developers.facebook.com/documentation/threads/overview.md
* Profile ``GET /me?fields=id,username`` ("You may only fetch the profile of the app-scoped
  user"). https://developers.facebook.com/documentation/threads/threads-profiles.md
* Media ``GET /{threads-media-id}?fields=id,permalink,username,owner,text``; the example
  permalink is ``https://www.threads.net/@user/post/abcdefg`` (another example uses
  threads.com). https://developers.facebook.com/documentation/threads/retrieve-and-discover-posts/retrieve-posts.md
* Long-lived user tokens "are valid for 60 days"; permission grants by public profiles last 90
  days. https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens.md
  https://developers.facebook.com/documentation/threads/get-started.md

Not verified (fail closed or documented, never relied on): the HTTP status Meta uses for an
expired token, a missing permission or throttling (Graph API error codes 190, 10/200-299 and
4/17 are documented without HTTP statuses, so codes are mapped by HTTP status only and error
bodies are never read); the shape of real Threads ids (examples are numeric, so only digit
ids are accepted); an Authorization-header alternative to ``access_token`` (none documented).

Credentials come only from ``{env_prefix}_ACCESS_TOKEN`` via ``accounts.credential``. The
token travels in the form body (POST) or the query string (GET, as documented); no error raised
here contains it, the URL, the request headers or the remote response body.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode, urlsplit

from . import publishers
from .accounts import credential
from .policy import ValidationError

API_BASE = "https://graph.threads.net/v1.0"
CREDENTIAL_SUFFIX = "_ACCESS_TOKEN"
TEXT_MAX_CHARS = 500   # official; emoji counted by UTF-8 bytes (manual_export.threads_length)
MAX_LINKS = 5          # official
PUBLISHED_POSTS_PER_24H = 250  # official overview; the live quota endpoint is authoritative
CONTAINER_WAIT_SECONDS = 30    # official "wait on average 30 seconds" before publishing
STATUS_POLL_SECONDS = 60       # official "once per minute"
STATUS_MAX_CHECKS = 5          # official "for no more than 5 minutes"
TOKEN_WARN_DAYS = 7
ID_PATTERN = re.compile(r"^[0-9]{1,40}$")  # examples are numeric; anything else is refused
# Threads handles follow Instagram usernames; this shape is a fail-closed assumption (unverified).
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._]{1,30}$")
PERMALINK_HOSTS = frozenset({"www.threads.net", "threads.net", "www.threads.com", "threads.com"})
_PERMALINK_PATH = re.compile(r"^/@?([A-Za-z0-9._]{1,30})/post/([A-Za-z0-9_-]{1,64})/?$")
_ALLOWED_KEYS = frozenset({"id", "platform", "env_prefix", "handle", "threads_user_id",
                           "authorization_expires_at"})

# Indirections so tests never sleep and can pin the clock.
_sleep: Callable[[float], None] = time.sleep


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- config

def normalize_handle(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Threads handle is required (the 'username' from GET /me)")
    text = value.strip().lstrip("@")
    if not USERNAME_PATTERN.fullmatch(text):
        raise ValidationError("Threads handle must be 1-30 letters, digits, '.' or '_'")
    return text


def normalize_user_id(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not ID_PATTERN.fullmatch(text):
        raise ValidationError("threads_user_id is required: the numeric 'id' from GET /me")
    return text


def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``.

    An account without ``threads_user_id`` is manual-only metadata (calendar, manual-export)
    and loads as before. Once ``threads_user_id`` is set the account is adapter-shaped: only
    documented, non-secret keys are allowed and handle and id are normalized. ``preflight``
    re-applies the strict rules before any request either way.
    """
    try:
        if "authorization_expires_at" in raw:
            from .linkedin import parse_expiry
            parse_expiry(raw["authorization_expires_at"])
        if "threads_user_id" not in raw:
            return
        extra = sorted(set(raw) - _ALLOWED_KEYS)
        if extra:
            raise ValidationError(f"Threads account has unsupported fields {', '.join(extra)}; "
                                  "only text posts on your own profile are supported")
        raw["handle"] = normalize_handle(raw.get("handle"))
        raw["threads_user_id"] = normalize_user_id(raw.get("threads_user_id"))
    except ValidationError as exc:
        raise ValidationError(f"{exc} (account {account_id})") from None


def token_status(account: dict, now: datetime | None = None) -> tuple[str, str]:
    """('ok'|'warning'|'expired'|'unknown', message) from optional, non-secret metadata."""
    from .linkedin import parse_expiry
    raw = account.get("authorization_expires_at")
    if raw is None:
        return "unknown", ("authorization_expires_at not recorded; Threads long-lived tokens last 60 days, "
                           "record the expiry when you exchange or refresh the token")
    try:
        expires = parse_expiry(raw)
    except ValidationError as exc:
        return "expired", f"threads_token_expiry_invalid: {exc}"
    current = now or _utc_now()
    if expires <= current:
        return "expired", ("threads_token_expired: refresh or re-authorize the Threads token, export it and "
                           "update authorization_expires_at")
    remaining = expires - current
    if remaining <= timedelta(days=TOKEN_WARN_DAYS):
        return "warning", f"threads_token_expiring: about {max(remaining.days, 0)} day(s) left; refresh soon"
    return "ok", f"threads token valid for about {remaining.days} more day(s)"


def doctor_notes(account: dict, *, now: datetime | None = None) -> list[str]:
    """Offline notes for `doctor`; never reads the token value."""
    notes = ["INFO: Threads adapter is owner-approved but blocked by the capability registry until a live receipt "
             "(docs/SECURITY-REVIEW-PROPOSAL-19-threads.md); use manual-export meanwhile"]
    level, message = token_status(account, now)
    if level != "ok":
        notes.append(f"{'ERROR' if level == 'expired' else level.upper()}: {message}")
    return notes


def content_problems(text: str) -> list[str]:
    """Official Threads text rules (500 chars with emoji as UTF-8 bytes, at most 5 links)."""
    from .manual_export import extract_links, threads_length
    problems = []
    count = threads_length(text)
    if count > TEXT_MAX_CHARS:
        problems.append(f"Threads text counts as {count} characters (emoji by UTF-8 bytes); limit is {TEXT_MAX_CHARS}")
    links = extract_links(text)
    if len(links) > MAX_LINKS:
        problems.append(f"Threads text has {len(links)} links; at most {MAX_LINKS} are allowed")
    return problems


def preflight(account: dict, *, now: datetime | None = None) -> tuple[str, str]:
    """Offline, fail-closed checks before any Threads request. Returns (user id, handle)."""
    if account.get("platform") != "threads" or account.get("backend") is not None:
        raise ValidationError("threads_preflight_wrong_platform")
    extra = sorted(set(account) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError("threads_unsupported_fields: " + ", ".join(extra))
    user_id = normalize_user_id(account.get("threads_user_id"))
    handle = normalize_handle(account.get("handle"))
    level, message = token_status(account, now)
    if level == "expired":
        raise ValidationError(message)
    return user_id, handle


# --------------------------------------------------------------------------- network

def _token(account: dict) -> str:
    name = account["env_prefix"] + CREDENTIAL_SUFFIX
    token = (credential(name) or "").strip()
    if not token:
        raise ValidationError("missing_credentials: " + name)
    return token


_HINTS = {
    ("me", "http_400"): ("threads_http_400: request refused; the token may be invalid or expired (Meta does "
                         "not document the HTTP status); refresh or re-authorize"),
    ("me", "http_401"): "threads_http_401: token invalid, revoked or expired; re-authorize Threads",
    ("me", "http_403"): "threads_http_403: token lacks threads_basic; identity cannot be checked",
    ("quota", "http_400"): ("threads_http_400: quota read refused; confirm the token carries threads_basic and "
                            "threads_content_publish"),
    ("quota", "http_403"): "threads_http_403: token lacks threads_content_publish; nothing was sent",
    ("create", "http_400"): ("threads_create_http_400_uncertain: container refused (text, link count, token or "
                             "permission); verify the profile before reconciling"),
    ("create", "http_429"): "threads_create_http_429_uncertain: rate limited; do not retry automatically",
    ("publish", "http_429"): "threads_publish_http_429_uncertain: rate limited; do not retry automatically",
}
_POST_WRITE_STAGES = frozenset({"create", "status", "publish"})


def _actionable(stage: str, code: str) -> str:
    """Map a sanitized helper error to an actionable one; never adds remote text."""
    hint = _HINTS.get((stage, code))
    if hint:
        return hint
    if stage in _POST_WRITE_STAGES:  # after mark_write_started(): always ambiguous, never retried
        return f"threads_{stage}_{code}" + ("" if code.endswith("_uncertain") else "_uncertain")
    return f"threads_{code}"


def _get(path: str, fields: str, token: str, stage: str) -> dict:
    url = f"{API_BASE}/{path}?" + urlencode({"fields": fields, "access_token": token})
    try:
        return publishers.request_get_json(url)
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None


def _post(path: str, form: dict, stage: str) -> dict:
    try:
        return publishers.request_json(f"{API_BASE}/{path}", form, form=True)
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None


def _quota(user_id: str, token: str) -> dict:
    """Read-only publishing quota; raise before the claim when it is unreadable or exhausted."""
    data = _get(f"{user_id}/threads_publishing_limit", "quota_usage,config", token, "quota").get("data")
    entry: dict = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    usage = entry.get("quota_usage")
    raw_config = entry.get("config")
    config: dict = raw_config if isinstance(raw_config, dict) else {}
    total, duration = config.get("quota_total"), config.get("quota_duration")
    if not (type(usage) is int and usage >= 0 and type(total) is int and total > 0
            and type(duration) is int and duration > 0):
        raise publishers.PublishError("threads_quota_unreadable")
    if usage >= total:
        raise ValidationError(f"threads_publishing_quota_exhausted: {usage}/{total} posts in the provider's "
                              f"{duration // 3600} h window; nothing was sent")
    return {"usage": usage, "total": total, "duration": duration}


def verify_identity(account: dict, *, now: datetime | None = None) -> dict:
    """GET /me (id and username must equal the config), then the read-only quota preflight.

    Raises before the durable claim on any mismatch, unreadable quota or exhausted quota.
    A successful quota read implies threads_content_publish (the endpoint requires it).
    """
    user_id, handle = preflight(account, now=now)
    token = _token(account)
    me = _get("me", "id,username", token, "me")
    if not isinstance(me.get("id"), str) or not isinstance(me.get("username"), str):
        raise publishers.PublishError("threads_me_missing_identity")
    if me["id"] != user_id or me["username"].casefold() != handle.casefold():
        raise ValidationError("identity_mismatch: Threads token belongs to a different profile than configured")
    return _quota(user_id, token)


def _media_id(result: dict, code: str) -> str:
    value = result.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not ID_PATTERN.fullmatch(str(value)):
        raise publishers.PublishError(code)
    return str(value)


PAUSED_CODE = "threads_container_paused_not_public_uncertain"


def _stop_if_paused(paused: Callable[[], bool] | None) -> None:
    """Owner decision D19-2: the emergency pause is re-read during the container wait. A pause
    stops before ``threads_publish``: the container is not public, but the write marker is set,
    so the post goes to needs_review and is never resent or resumed."""
    if paused is not None and paused():
        raise publishers.PublishError(PAUSED_CODE)


def _wait_until_finished(container_id: str, token: str, paused: Callable[[], bool] | None = None) -> None:
    """Documented wait, then status polls once per minute for at most 5 checks. ``paused`` is
    checked before the first wait and after every wait (D19-2)."""
    _stop_if_paused(paused)
    _sleep(CONTAINER_WAIT_SECONDS)
    for check in range(STATUS_MAX_CHECKS):
        _stop_if_paused(paused)
        status = _get(container_id, "status,error_message", token, "status").get("status")
        if status == "FINISHED":
            return
        if status == "PUBLISHED":  # someone (or something) already published this container
            raise publishers.PublishError("threads_container_already_published_uncertain")
        if status in ("ERROR", "EXPIRED"):
            raise publishers.PublishError(f"threads_container_{status.lower()}_not_public_uncertain")
        if status != "IN_PROGRESS":
            raise publishers.PublishError("threads_container_status_unrecognized_uncertain")
        if check + 1 < STATUS_MAX_CHECKS:
            _sleep(STATUS_POLL_SECONDS)
    raise publishers.PublishError("threads_container_not_ready_not_public_uncertain")


def permalink_owner(url: object) -> str | None:
    """The username in a documented-shape HTTPS Threads permalink, else None."""
    if not isinstance(url, str) or len(url) > 300:
        return None
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in PERMALINK_HOSTS or parts.port is not None
            or parts.username or parts.password or parts.query or parts.fragment):
        return None
    match = _PERMALINK_PATH.fullmatch(parts.path)
    return match.group(1) if match else None


def _receipt(media_id: str, container_id: str, content: str, user_id: str, handle: str,
             token: str) -> publishers.Receipt:
    """The media id is the receipt. A permalink read failure keeps it (remote_url None);
    a permalink, username or owner that names another profile is ambiguous (needs_review)."""
    try:
        media = _get(media_id, "id,permalink,username,owner,text", token, "permalink")
    except publishers.PublishError:
        return publishers.Receipt(media_id, None, container_id=container_id, permalink="unavailable")
    if media.get("id") not in (None, media_id):
        raise publishers.PublishError("threads_receipt_id_mismatch_uncertain")
    owner = media.get("owner")
    if isinstance(owner, dict) and owner.get("id") is not None and str(owner.get("id")) != user_id:
        raise publishers.PublishError("threads_receipt_account_mismatch_uncertain")
    username = media.get("username")
    if isinstance(username, str) and username.casefold() != handle.casefold():
        raise publishers.PublishError("threads_receipt_account_mismatch_uncertain")
    link_owner = permalink_owner(media.get("permalink"))
    if link_owner is not None and link_owner.casefold() != handle.casefold():
        raise publishers.PublishError("threads_receipt_account_mismatch_uncertain")
    text = media.get("text")
    text_match = "unknown" if not isinstance(text, str) else ("true" if text == content else "false")
    if link_owner is None:
        return publishers.Receipt(media_id, None, container_id=container_id, permalink="unrecognized",
                                  text_match=text_match)
    return publishers.Receipt(media_id, media["permalink"], container_id=container_id, text_match=text_match)


def publish_text_post(account: dict, content: str, post_id: str, *, now: datetime | None = None,
                      paused: Callable[[], bool] | None = None) -> publishers.Receipt:
    """Create and publish one TEXT container. ``post_id`` is local only (no idempotency key).

    C1/C3 boundary: ``mark_write_started()`` is set before the container POST, so every later
    failure is ambiguous and the post goes to needs_review, although an unpublished container
    is not public (see the proposal for why the conservative choice was made). ``paused``
    (D19-2) is re-read during the container wait and once more right before ``threads_publish``.
    """
    user_id, handle = preflight(account, now=now)
    problems = content_problems(content)
    if problems:
        raise ValidationError("; ".join(problems))
    token = _token(account)
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    container_id = _media_id(_post(f"{user_id}/threads",
                                   {"media_type": "TEXT", "text": content, "access_token": token}, "create"),
                             "threads_create_missing_container_id_uncertain")
    _wait_until_finished(container_id, token, paused)
    _stop_if_paused(paused)  # the last chance before the post becomes public
    media_id = _media_id(_post(f"{user_id}/threads_publish",
                               {"creation_id": container_id, "access_token": token}, "publish"),
                         "threads_publish_missing_media_id_uncertain")
    return _receipt(media_id, container_id, content, user_id, handle, token)
