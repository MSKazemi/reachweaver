"""Instagram single-image publisher (issue #20). Owner-approved 2026-10-09; disabled until a live receipt, so unreachable today.

The capability registry keeps Instagram at ``reachweaver_adapter: "none"`` with image
``api_gated`` and no ``live_media_formats``, and ``policy.SUPPORTED_LIVE`` excludes it, so
``publishers.publish``, ``connections.check_connection`` and the CLI refuse Instagram before
any credential read or request. This module only becomes reachable after the owner's Meta
App Review, a supervised first live receipt and a reviewed registry change
(docs/SECURITY-REVIEW-PROPOSAL-20-instagram.md).

Scope: one JPEG image with caption and alt text on the authenticated professional account's
own feed, through the Instagram API with Instagram Login (``graph.instagram.com``). No
carousel, video, reel, story, user tags, location, collaborators, product tags or trial
reels are ever sent. The image is an owner-hosted HTTPS URL (``remote_media``); ReachWeaver
uploads nothing.

Facts read 2026-10-09 from the official docs (developers.facebook.com):

* Content publishing guide, https://developers.facebook.com/docs/instagram-platform/content-publishing :
  host ``graph.instagram.com`` for Instagram Login; ``POST /<IG_ID>/media`` with ``image_url``
  creates a container; ``POST /<IG_ID>/media_publish`` with ``creation_id`` publishes it and
  returns the media id; the examples send JSON with ``Authorization: Bearer <ACCESS_TOKEN>``;
  ``GET /<IG_CONTAINER_ID>?fields=status_code`` returns EXPIRED, ERROR, FINISHED, IN_PROGRESS
  or PUBLISHED, queried "once per minute, for no more than 5 minutes"; "Instagram accounts
  are limited to 100 API-published posts within a 24-hour moving period" (the carousel section
  of the same page says 50, and the endpoint reference says ``quota_total`` is "currently
  50": the documented number is inconsistent, so the live ``content_publishing_limit`` is
  authoritative); ``alt_text`` exists for image posts; "JPEG is the only image format
  supported"; permissions ``instagram_business_basic`` and ``instagram_business_content_publish``.
* IG User Media reference, https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media :
  caption "Maximum 2200 characters, 30 hashtags, and 20 @ tags"; ``alt_text`` "up to 1000
  character"; containers expire after 24 hours; at most 400 containers per rolling 24 hours.
  (This reference is written for Facebook Login; the same limits are assumed, unverified, for
  Instagram Login.)
* Content publishing limit, https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit :
  ``GET /<IG_USER_ID>/content_publishing_limit``, fields ``quota_usage`` and ``config``
  (``quota_total``, ``quota_duration`` 86400), response ``{"data": [{...}]}``.
* Get started (Instagram Login), https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started :
  ``GET https://graph.instagram.com/v25.0/me?fields=user_id,username`` with ``access_token``
  as a query parameter; ``user_id`` is "The Instagram professional acount ID"; ``account_type``
  "Can be Business or Media_Creator"; dashboard tokens "are valid for 60 days".
* IG Media reference, https://developers.facebook.com/docs/instagram-platform/reference/instagram-media :
  ``GET /<IG_MEDIA_ID>?fields=...&access_token=...``; ``permalink`` "Permanent URL to the
  media"; ``username``; ``media_type`` IMAGE/VIDEO/CAROUSEL_ALBUM. No permalink format is
  documented.

Not verified (fail closed or documented, never relied on): the HTTP statuses Meta uses for an
expired token, a missing permission or throttling (error bodies are never read); whether
``Authorization: Bearer`` works for GET on graph.instagram.com (GETs use the documented query
parameter); the permalink URL shape; whether Meta refuses MPO/JPS JPEGs ReachWeaver accepts.

Credentials come only from ``{env_prefix}_ACCESS_TOKEN`` via ``accounts.credential``. No error
raised here contains the token, a URL, request headers or the remote response body.
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

API_VERSION = "v25.0"  # the version the official examples use (accessed 2026-10-09)
API_BASE = f"https://graph.instagram.com/{API_VERSION}"
CREDENTIAL_SUFFIX = "_ACCESS_TOKEN"
CAPTION_MAX_CHARS = 2200   # official
CAPTION_MAX_HASHTAGS = 30  # official
CAPTION_MAX_MENTIONS = 20  # official ("@ tags")
DOCUMENTED_POSTS_PER_24H = 50  # lower of the two documented numbers (50 vs 100); the live quota is authoritative
STATUS_POLL_SECONDS = 60   # official "once per minute"
STATUS_MAX_CHECKS = 5      # official "for no more than 5 minutes"
TOKEN_WARN_DAYS = 7
PROFESSIONAL_ACCOUNT_TYPES = frozenset({"business", "media_creator"})  # documented values, compared casefolded
ID_PATTERN = re.compile(r"^[0-9]{1,40}$")  # documented examples are numeric; anything else is refused
# Instagram usernames: a fail-closed shape assumption (not a documented API contract).
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._]{1,30}$")
PERMALINK_HOSTS = frozenset({"www.instagram.com", "instagram.com"})
_PERMALINK_PATH = re.compile(r"^/(?:p|reel)/[A-Za-z0-9_-]{1,64}/?$")  # observed shape, undocumented
_HASHTAG = re.compile(r"#[^\s#]+")
_MENTION = re.compile(r"@[A-Za-z0-9._]+")
_ALLOWED_KEYS = frozenset({"id", "platform", "env_prefix", "handle", "ig_user_id", "authorization_expires_at"})

# Indirections so tests never sleep and can pin the clock.
_sleep: Callable[[float], None] = time.sleep


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- config

def normalize_handle(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Instagram handle is required (the 'username' from GET /me)")
    text = value.strip().lstrip("@")
    if not USERNAME_PATTERN.fullmatch(text):
        raise ValidationError("Instagram handle must be 1-30 letters, digits, '.' or '_'")
    return text


def normalize_user_id(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not ID_PATTERN.fullmatch(text):
        raise ValidationError("ig_user_id is required: the numeric 'user_id' from GET /me (as a string)")
    return text


def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``.

    An account without ``ig_user_id`` is manual-only metadata (calendar, manual-export) and
    loads as before. Once ``ig_user_id`` is set the account is adapter-shaped: only
    documented, non-secret keys are allowed and handle and id are normalized. ``preflight``
    re-applies the strict rules before any request either way.
    """
    try:
        if "authorization_expires_at" in raw:
            from .linkedin import parse_expiry
            parse_expiry(raw["authorization_expires_at"])
        if "ig_user_id" not in raw:
            return
        extra = sorted(set(raw) - _ALLOWED_KEYS)
        if extra:
            raise ValidationError(f"Instagram account has unsupported fields {', '.join(extra)}; "
                                  "only single-image posts on your own professional account are supported")
        raw["handle"] = normalize_handle(raw.get("handle"))
        raw["ig_user_id"] = normalize_user_id(raw.get("ig_user_id"))
    except ValidationError as exc:
        raise ValidationError(f"{exc} (account {account_id})") from None


def token_status(account: dict, now: datetime | None = None) -> tuple[str, str]:
    """('ok'|'warning'|'expired'|'unknown', message) from optional, non-secret metadata."""
    from .linkedin import parse_expiry
    raw = account.get("authorization_expires_at")
    if raw is None:
        return "unknown", ("authorization_expires_at not recorded; Instagram long-lived tokens last 60 days, "
                           "record the expiry when you generate or refresh the token")
    try:
        expires = parse_expiry(raw)
    except ValidationError as exc:
        return "expired", f"instagram_token_expiry_invalid: {exc}"
    current = now or _utc_now()
    if expires <= current:
        return "expired", ("instagram_token_expired: refresh or regenerate the Instagram token, export it and "
                           "update authorization_expires_at")
    remaining = expires - current
    if remaining <= timedelta(days=TOKEN_WARN_DAYS):
        return "warning", f"instagram_token_expiring: about {max(remaining.days, 0)} day(s) left; refresh soon"
    return "ok", f"instagram token valid for about {remaining.days} more day(s)"


def doctor_notes(account: dict, *, now: datetime | None = None) -> list[str]:
    """Offline notes for `doctor`; never reads the token value."""
    notes = ["INFO: Instagram image adapter is owner-approved but blocked by the capability registry until a live receipt "
             "(docs/SECURITY-REVIEW-PROPOSAL-20-instagram.md); use manual-export meanwhile"]
    if "ig_user_id" in account:
        level, message = token_status(account, now)
        if level != "ok":
            notes.append(f"{'ERROR' if level == 'expired' else level.upper()}: {message}")
    return notes


def content_problems(caption: str) -> list[str]:
    """Official caption rules: 2200 characters, 30 hashtags, 20 @ tags (counted conservatively)."""
    problems = []
    if len(caption) > CAPTION_MAX_CHARS:
        problems.append(f"Instagram caption has {len(caption)} characters; limit is {CAPTION_MAX_CHARS}")
    hashtags = len(_HASHTAG.findall(caption))
    if hashtags > CAPTION_MAX_HASHTAGS:
        problems.append(f"Instagram caption has {hashtags} hashtags; at most {CAPTION_MAX_HASHTAGS} are allowed")
    mentions = len(_MENTION.findall(caption))
    if mentions > CAPTION_MAX_MENTIONS:
        problems.append(f"Instagram caption has {mentions} @ tags (emails count too); at most "
                        f"{CAPTION_MAX_MENTIONS} are allowed")
    return problems


def preflight(account: dict, *, now: datetime | None = None) -> tuple[str, str]:
    """Offline, fail-closed checks before any Instagram request. Returns (ig user id, handle)."""
    if account.get("platform") != "instagram" or account.get("backend") is not None:
        raise ValidationError("instagram_preflight_wrong_platform")
    extra = sorted(set(account) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError("instagram_unsupported_fields: " + ", ".join(extra))
    user_id = normalize_user_id(account.get("ig_user_id"))
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
    ("me", "http_400"): ("instagram_http_400: request refused; the token may be invalid or expired (Meta does "
                         "not document the HTTP status); refresh or regenerate it"),
    ("me", "http_401"): "instagram_http_401: token invalid, revoked or expired; regenerate the Instagram token",
    ("me", "http_403"): "instagram_http_403: token lacks instagram_business_basic; identity cannot be checked",
    ("quota", "http_400"): ("instagram_http_400: quota read refused; confirm the token carries "
                            "instagram_business_basic and instagram_business_content_publish"),
    ("quota", "http_403"): "instagram_http_403: token lacks instagram_business_content_publish; nothing was sent",
    ("create", "http_400"): ("instagram_create_http_400_uncertain: container refused (image URL, JPEG, aspect "
                             "ratio, caption, token or permission); verify the account before reconciling"),
    ("create", "http_429"): "instagram_create_http_429_uncertain: rate limited; do not retry automatically",
    ("publish", "http_429"): "instagram_publish_http_429_uncertain: rate limited; do not retry automatically",
}
_POST_WRITE_STAGES = frozenset({"create", "status", "publish"})


def _actionable(stage: str, code: str) -> str:
    """Map a sanitized helper error to an actionable one; never adds remote text."""
    hint = _HINTS.get((stage, code))
    if hint:
        return hint
    if stage in _POST_WRITE_STAGES:  # after mark_write_started(): always ambiguous, never retried
        return f"instagram_{stage}_{code}" + ("" if code.endswith("_uncertain") else "_uncertain")
    return f"instagram_{code}"


def _get(path: str, fields: str, token: str, stage: str) -> dict:
    # GET uses the documented access_token query parameter (Bearer for GET is not documented).
    url = f"{API_BASE}/{path}?" + urlencode({"fields": fields, "access_token": token})
    try:
        return publishers.request_get_json(url)
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None


def _post(path: str, body: dict, token: str, stage: str) -> dict:
    # POST follows the documented examples: JSON body plus Authorization: Bearer.
    try:
        return publishers.request_json(f"{API_BASE}/{path}", body, headers={"Authorization": f"Bearer {token}"})
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None


def _quota(user_id: str, token: str) -> dict:
    """Read-only publishing quota; raise before the claim when it is unreadable or exhausted."""
    data = _get(f"{user_id}/content_publishing_limit", "quota_usage,config", token, "quota").get("data")
    entry: dict = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    usage = entry.get("quota_usage")
    raw_config = entry.get("config")
    config: dict = raw_config if isinstance(raw_config, dict) else {}
    total, duration = config.get("quota_total"), config.get("quota_duration")
    if not (type(usage) is int and usage >= 0 and type(total) is int and total > 0
            and type(duration) is int and duration > 0):
        raise publishers.PublishError("instagram_quota_unreadable")
    if usage >= total:
        raise ValidationError(f"instagram_publishing_quota_exhausted: {usage}/{total} posts in the provider's "
                              f"{duration // 3600} h window; nothing was sent")
    return {"usage": usage, "total": total, "duration": duration}


def _id_text(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not ID_PATTERN.fullmatch(str(value)):
        return None
    return str(value)


def verify_identity(account: dict, *, now: datetime | None = None) -> dict:
    """GET /me (user_id, username and a professional account_type must match), then the quota.

    Raises before the durable claim on any mismatch, unreadable quota or exhausted quota.
    A successful quota read is taken as evidence of the publish permission (the endpoint
    lists instagram_business_content_publish among its permissions).
    """
    user_id, handle = preflight(account, now=now)
    token = _token(account)
    me = _get("me", "user_id,username,account_type", token, "me")
    actual_id, username, account_type = _id_text(me.get("user_id")), me.get("username"), me.get("account_type")
    if actual_id is None or not isinstance(username, str):
        raise publishers.PublishError("instagram_me_missing_identity")
    if actual_id != user_id or username.casefold() != handle.casefold():
        raise ValidationError("identity_mismatch: Instagram token belongs to a different account than configured")
    if not isinstance(account_type, str) or account_type.casefold() not in PROFESSIONAL_ACCOUNT_TYPES:
        raise ValidationError("instagram_not_professional_account: only Business or Creator accounts can publish "
                              "through the API")
    return _quota(user_id, token)


def _media_id(result: dict, code: str) -> str:
    value = _id_text(result.get("id"))
    if value is None:
        raise publishers.PublishError(code)
    return value


def _wait_until_finished(container_id: str, token: str, paused: Callable[[], bool] | None) -> None:
    """Status checks once per minute, at most 5. A pause stops before the media is published."""
    for check in range(STATUS_MAX_CHECKS):
        status = _get(container_id, "status_code", token, "status").get("status_code")
        if status == "FINISHED":
            return
        if status == "PUBLISHED":  # someone (or something) already published this container
            raise publishers.PublishError("instagram_container_already_published_uncertain")
        if status in ("ERROR", "EXPIRED"):
            raise publishers.PublishError(f"instagram_container_{status.lower()}_not_public_uncertain")
        if status != "IN_PROGRESS":
            raise publishers.PublishError("instagram_container_status_unrecognized_uncertain")
        if check + 1 < STATUS_MAX_CHECKS:
            if paused is not None and paused():
                raise publishers.PublishError("instagram_paused_before_publish_not_public_uncertain")
            _sleep(STATUS_POLL_SECONDS)
    raise publishers.PublishError("instagram_container_not_ready_not_public_uncertain")


def permalink_ok(url: object) -> bool:
    """True for an HTTPS instagram.com post permalink of the observed (undocumented) shape."""
    if not isinstance(url, str) or len(url) > 300:
        return False
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in PERMALINK_HOSTS and parts.port is None
            and not (parts.username or parts.password or parts.query or parts.fragment)
            and bool(_PERMALINK_PATH.fullmatch(parts.path)))


def _receipt(media_id: str, container_id: str, handle: str, token: str, sha256: str) -> publishers.Receipt:
    """The media id is the receipt. A permalink read failure keeps it (remote_url None); a
    username, id or media type that contradicts the send is ambiguous (needs_review)."""
    extra = {"container_id": container_id, "image_digest": sha256[:16]}  # keys must match [a-z_]
    try:
        media = _get(media_id, "id,permalink,username,media_type", token, "permalink")
    except publishers.PublishError:
        return publishers.Receipt(media_id, None, permalink="unavailable", **extra)
    if media.get("id") is not None and _id_text(media.get("id")) != media_id:
        raise publishers.PublishError("instagram_receipt_id_mismatch_uncertain")
    username = media.get("username")
    if isinstance(username, str) and username.casefold() != handle.casefold():
        raise publishers.PublishError("instagram_receipt_account_mismatch_uncertain")
    if media.get("media_type") not in (None, "IMAGE"):
        raise publishers.PublishError("instagram_receipt_media_type_mismatch_uncertain")
    if not permalink_ok(media.get("permalink")):
        return publishers.Receipt(media_id, None, permalink="unrecognized", **extra)
    return publishers.Receipt(media_id, media["permalink"], **extra)


def publish_image_post(account: dict, content: str, post_id: str, media: list[dict], *,
                       paused: Callable[[], bool] | None = None, now: datetime | None = None,
                       fetch: Callable[[str], bytes] | None = None) -> publishers.Receipt:
    """Create and publish one IMAGE container. ``post_id`` is local only (no idempotency key).

    Order: offline preflight, caption and attachment rules, token read, then the hosted image
    is downloaded again and must still hash to the approved SHA-256 (a mismatch raises before
    any write). C1/C3 boundary: ``mark_write_started()`` is set before the container POST, so
    every later failure is ambiguous and the post goes to needs_review.
    """
    from .remote_media import check_media_list, download_image, fetch_approved_bytes
    user_id, handle = preflight(account, now=now)
    problems = content_problems(content)
    if problems:
        raise ValidationError("; ".join(problems))
    if not media or len(media) != 1:
        raise ValidationError("instagram_image_required: exactly one approved image attachment is needed")
    check_media_list(media, "instagram")
    item = media[0]
    token = _token(account)
    fetch_approved_bytes(item, fetch=fetch or download_image)  # TOCTOU narrowing: bytes Meta will fetch
    if paused is not None and paused():
        raise ValidationError("Global emergency pause is active. Run 'resume' deliberately.")
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    container_id = _media_id(_post(f"{user_id}/media", {"image_url": item["url"], "caption": content,
                                                         "alt_text": item["alt"]}, token, "create"),
                             "instagram_create_missing_container_id_uncertain")
    _wait_until_finished(container_id, token, paused)
    if paused is not None and paused():
        raise publishers.PublishError("instagram_paused_before_publish_not_public_uncertain")
    media_id = _media_id(_post(f"{user_id}/media_publish", {"creation_id": container_id}, token, "publish"),
                         "instagram_publish_missing_media_id_uncertain")
    return _receipt(media_id, container_id, handle, token, item["sha256"])
