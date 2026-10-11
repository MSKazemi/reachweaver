"""TikTok video delivery through the Content Posting API (issue #23). Owner-approved 2026-10-09;
disabled until a live receipt, so unreachable today.

Two modes, both gated by the capability registry (``capabilities.require_video_mode``) and by
``policy.SUPPORTED_LIVE_VIDEO``, which is empty, so ``run_live_send`` and ``deliver`` refuse
before any credential read or request:

* ``creator_inbox`` (first mode, ``video.upload``): the video goes to the creator's TikTok inbox
  as a draft. Nothing becomes public; the creator finishes and posts it in the TikTok app. The
  local post is therefore NEVER marked ``published``: it moves to ``needs_review`` with reason
  ``tiktok_creator_inbox_delivered_not_public`` (the Postiz "accepted, not a receipt" pattern),
  and the owner reconciles it with the native video id once the creator has posted it.
* ``direct_post`` (``video.publish``): behind a SECOND registry switch that stays off. The
  privacy level is bound at approval, must be one of the creator's live
  ``privacy_level_options`` and has no default (never PUBLIC by default).

Facts read 2026-10-09 from the official TikTok developer docs (developers.tiktok.com/doc/...):

* Inbox init ``POST https://open.tiktokapis.com/v2/post/publish/inbox/video/init/``, scope
  ``video.upload``, JSON ``source_info`` {``source: FILE_UPLOAD``, ``video_size``,
  ``chunk_size``, ``total_chunk_count``}; returns ``data.publish_id`` (up to 64 characters) and
  ``data.upload_url`` ("valid for one hour after issuance"; example host
  open-upload.tiktokapis.com). Users "must click on inbox notifications to continue the editing
  flow in TikTok"; "at most 5 pending shares within any 24-hour period"; 6 requests per minute
  per user access token. content-posting-api-reference-upload-video,
  content-posting-api-get-started-upload-content
* Direct Post init ``POST /v2/post/publish/video/init/``, scope ``video.publish``,
  ``post_info`` {``title`` (max "2200 in UTF-16 runes"), ``privacy_level`` (required, one of
  PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, SELF_ONLY, must match
  creator_info), ``disable_duet``, ``disable_stitch``, ``disable_comment``,
  ``brand_content_toggle``, ``brand_organic_toggle``, ``is_aigc``}; error
  ``unaudited_client_can_only_post_to_private_accounts``; "All content posted by unaudited
  clients will be restricted to private viewing mode." content-posting-api-reference-direct-post,
  content-posting-api-get-started
* Creator info ``POST /v2/post/publish/creator_info/query/`` (``video.publish``, 20 per minute):
  ``creator_username``, ``creator_nickname``, ``privacy_level_options``, ``comment_disabled``,
  ``duet_disabled``, ``stitch_disabled``, ``max_video_post_duration_sec``.
  content-posting-api-reference-query-creator-info
* Chunks: PUT ``upload_url`` with ``Content-Type``, ``Content-Length`` and ``Content-Range:
  bytes FIRST-LAST/TOTAL``; 206 = chunk accepted, more to come; 201 = all parts uploaded; chunks
  5 MB..64 MB, the final one up to 128 MB; ``total_chunk_count`` = video_size // chunk_size;
  under 5 MB the whole file is one chunk; over 64 MB it must be chunked; 1..1000 chunks, sent
  sequentially. Video: MP4 (recommended), WebM, MOV; H.264/H.265/VP8/VP9; 23..60 FPS; 360..4096
  px each side; at most 10 minutes; at most 4 GB. content-posting-api-media-transfer-guide
* Status ``POST /v2/post/publish/status/fetch/`` (30 per minute): PROCESSING_UPLOAD,
  PROCESSING_DOWNLOAD, SEND_TO_USER_INBOX, PUBLISH_COMPLETE, FAILED (+ ``fail_reason``);
  ``publicaly_available_post_id`` (sic) is "returned only if the post is published for public
  viewership". content-posting-api-reference-get-video-status
* Identity ``GET /v2/user/info/?fields=open_id,display_name`` (``user.info.basic``).
  tiktok-api-v2-get-user-info. Access tokens are valid 24 hours.
  oauth-user-access-token-management
* Content Sharing Guidelines: "Users must manually select the privacy status from a dropdown and
  there should be no default value"; interaction settings: "none should be checked by default";
  "API Clients must only start sending content materials to TikTok after the user has expressly
  consent to the upload." content-sharing-guidelines

Not verified (fail closed or documented, never relied on): the Content-Type values TikTok
accepts for a chunk (only ``video/mp4`` appears in the examples, so ReachWeaver sends MP4 only);
the exact meaning of "MB" (5,242,880..64,000,000 bytes is safe under both readings); the shape
of ``open_id``; whether an inbox init alone counts toward the pending-share cap; whether
``upload_url`` can use another host than ``*.tiktokapis.com`` (any other host is refused); the
public video URL format (no ``remote_url`` is recorded). HTTP error bodies are never read, so
4xx errors are mapped by HTTP status only.

The token comes only from ``{env_prefix}_TIKTOK_ACCESS_TOKEN`` via ``accounts.credential`` and is
sent only to open.tiktokapis.com (never to ``upload_url``). No error raised here contains the
token, a URL (the upload URL carries an upload token), headers or response bodies.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from . import publishers
from .accounts import credential
from .capabilities import require_video_mode
from .policy import ValidationError, validate_post

API_BASE = "https://open.tiktokapis.com"
USER_INFO_PATH = "/v2/user/info/"
CREATOR_INFO_PATH = "/v2/post/publish/creator_info/query/"
INBOX_INIT_PATH = "/v2/post/publish/inbox/video/init/"
DIRECT_INIT_PATH = "/v2/post/publish/video/init/"
STATUS_PATH = "/v2/post/publish/status/fetch/"
CREDENTIAL_SUFFIX = "_TIKTOK_ACCESS_TOKEN"
MODES = ("creator_inbox", "direct_post")
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
COMMERCIAL_CHOICES = ("none", "own_brand", "branded_content")
VIDEO_MIME = "video/mp4"
MAX_VIDEO_BYTES = 256 * 1024 * 1024  # ReachWeaver cap: the bytes sent are held in memory (TikTok: 4 GB)
SINGLE_CHUNK_MAX = 64_000_000        # official "over 64 MB must be chunked", read as decimal MB
CHUNK_SIZE = 10 * 1024 * 1024        # inside 5..64 MB under both decimal and binary readings
MAX_CHUNKS = 1000
MIN_DIMENSION, MAX_DIMENSION = 360, 4096
MIN_FPS, MAX_FPS = 23, 60
MAX_DURATION_S = 600
VIDEO_CODECS = frozenset({"h264", "hevc", "vp8", "vp9"})
STATUS_POLL_SECONDS = 10  # ReachWeaver choice; the documented limit is 30 status requests per minute
STATUS_MAX_CHECKS = 30    # ReachWeaver choice: about 5 minutes, then needs_review
INBOX_REASON = "tiktok_creator_inbox_delivered_not_public"
REMOTE_PREFIX = "tiktok_inbox:"
OPEN_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")  # shape not documented; fail-closed assumption
PUBLISH_ID = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")  # "up to 64 characters", e.g. v_inbox_file~v2.123
PUBLIC_POST_ID = re.compile(r"^[0-9]{1,20}$")       # int64
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
UPLOAD_HOST_SUFFIX = ".tiktokapis.com"
_FAIL_REASON = re.compile(r"^[a-z0-9_]{1,40}$")
_ALLOWED_KEYS = frozenset({"id", "platform", "env_prefix", "handle", "open_id", "display_name"})
# ``platform_options`` of the shared video record (``video.py``, phase 2 design, 2026-10-09); all
# are bound by the approval. In creator_inbox mode every option except ``mode`` is null (the
# creator chooses them in the TikTok app).
OPTION_KEYS = ("mode", "privacy_level", "commercial", "disable_comment", "disable_duet", "disable_stitch",
               "is_aigc")
_SETTINGS_KEYS = ("privacy_level", "disable_comment", "disable_duet", "disable_stitch",
                  "brand_content_toggle", "brand_organic_toggle", "is_aigc")  # the Direct Post post_info fields
# Documented error.code values; anything else is reported as "api_error" (never the message).
KNOWN_ERRORS = frozenset({
    "invalid_param", "spam_risk_too_many_pending_share", "spam_risk_too_many_posts",
    "spam_risk_user_banned_from_posting", "reached_active_user_cap",
    "unaudited_client_can_only_post_to_private_accounts", "url_ownership_unverified",
    "privacy_level_option_mismatch", "access_token_invalid", "scope_not_authorized", "rate_limit_exceeded",
    "invalid_publish_id", "token_not_authorized_for_specified_publish_id", "internal_error",
})

# Indirection so tests never sleep.
_sleep: Callable[[float], None] = time.sleep


class CreatorInboxDelivered(publishers.PublishError):
    """The video reached the creator's inbox. NOT a public post: callers must use needs_review.

    Subclassing PublishError means a generic caller that maps PublishError to
    ``uncertain_send`` is automatically safe (needs_review, never resent).
    """

    def __init__(self, publish_id: str, final_status: str):
        super().__init__(INBOX_REASON)
        self.publish_id = publish_id
        self.final_status = final_status


class VideoChanged(ValidationError):
    """The file on disk no longer holds the bytes the approval bound."""


@dataclass(frozen=True)
class VerifiedTikTok:
    account_id: str
    mode: str
    identity: str = "verified"
    write_permission: str = "unverified"  # no read-only probe exists for video.upload


# --------------------------------------------------------------------------- config

def _display_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(
            ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValidationError("TikTok display_name is required: the exact 'display_name' from GET /v2/user/info/")
    return value


def _open_id(value: object) -> str:
    if not isinstance(value, str) or not OPEN_ID.fullmatch(value):
        raise ValidationError("TikTok open_id is required: the 'open_id' from GET /v2/user/info/")
    return value


def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``.

    Without ``open_id`` the account is manual-only metadata (calendar, manual-export) and loads
    as before. With it the account is adapter-shaped: only documented, non-secret keys.
    """
    if "open_id" not in raw:
        return
    try:
        extra = sorted(set(raw) - _ALLOWED_KEYS)
        if extra:
            raise ValidationError(f"TikTok account has unsupported fields {', '.join(extra)}")
        _open_id(raw.get("open_id"))
        _display_name(raw.get("display_name"))
    except ValidationError as exc:
        raise ValidationError(f"{exc} (account {account_id})") from None


def preflight(account: dict) -> tuple[str, str]:
    """Offline, fail-closed checks before any TikTok request. Returns (open_id, display_name)."""
    if account.get("platform") != "tiktok" or account.get("backend") is not None:
        raise ValidationError("tiktok_preflight_wrong_platform")
    extra = sorted(set(account) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError("tiktok_unsupported_fields: " + ", ".join(extra))
    return _open_id(account.get("open_id")), _display_name(account.get("display_name"))


def doctor_notes(account: dict) -> list[str]:
    """Offline notes for `doctor`; never reads the token value."""
    notes = ["INFO: TikTok video adapter is owner-approved but blocked by the capability registry until a live receipt "
             "(docs/SECURITY-REVIEW-PROPOSAL-23-tiktok.md); use manual-export meanwhile"]
    notes.append("INFO: TikTok access tokens are valid for 24 hours; ReachWeaver has no refresh flow, so "
                 "export a fresh token before a supervised run")
    return notes


# --------------------------------------------------------------------------- video

def video_problems(info: dict, *, max_duration: int | None = None) -> list[str]:
    """Fail-closed TikTok checks on ``media.probe_*`` output: an unknown value is a problem."""
    problems = []
    if info.get("container") != "mp4":
        problems.append(f"container {info.get('container')}; ReachWeaver sends MP4 only")
    size = info.get("bytes")
    if type(size) is not int or not 1 <= size <= MAX_VIDEO_BYTES:
        problems.append(f"size {size} bytes; ReachWeaver's limit is {MAX_VIDEO_BYTES} (TikTok: 4 GB)")
    duration = info.get("duration_seconds")
    limit = MAX_DURATION_S if max_duration is None else min(MAX_DURATION_S, max_duration)
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        problems.append("duration unknown (header not parseable)")
    elif not 0 < duration <= limit:
        problems.append(f"duration {duration}s outside 0..{limit}s")
    width, height = info.get("width"), info.get("height")
    if type(width) is not int or type(height) is not int:
        problems.append("dimensions unknown (header not parseable)")
    elif not (MIN_DIMENSION <= width <= MAX_DIMENSION and MIN_DIMENSION <= height <= MAX_DIMENSION):
        problems.append(f"{width}x{height} outside {MIN_DIMENSION}..{MAX_DIMENSION} px per side")
    if info.get("video_codec") not in VIDEO_CODECS:
        problems.append(f"video codec {info.get('video_codec')} not in {', '.join(sorted(VIDEO_CODECS))}")
    fps = info.get("frame_rate_fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool):
        problems.append("frame rate unknown (header not parseable)")
    elif not MIN_FPS <= fps <= MAX_FPS:
        problems.append(f"frame rate {fps} outside {MIN_FPS}..{MAX_FPS} fps")
    return problems


def _read_video(path: Path) -> bytes:
    """Read a regular, non-symlink file of at most MAX_VIDEO_BYTES; the path is never echoed."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValidationError("video_file_unreadable: missing, unreadable or a symlink") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValidationError("video_file_not_regular")
        if info.st_size > MAX_VIDEO_BYTES:
            raise ValidationError(f"video_file_too_large: over {MAX_VIDEO_BYTES} bytes")
        data = handle.read(MAX_VIDEO_BYTES + 1)
    if len(data) > MAX_VIDEO_BYTES:
        raise ValidationError(f"video_file_too_large: over {MAX_VIDEO_BYTES} bytes")
    return data


def build_video_record(path: Path, *, mode: str = "creator_inbox", privacy_level: str | None = None,
                       allow_comment: bool = False, allow_duet: bool = False, allow_stitch: bool = False,
                       commercial: str | None = None, aigc: bool = False) -> dict:
    """Offline: probe a local MP4 and return the shared video record an approval will bind (nothing uploaded)."""
    from .media import probe_bytes
    from .video import make_record
    if mode not in MODES:
        raise ValidationError(f"--mode must be one of {', '.join(MODES)}")
    if Path(path).is_symlink():
        raise ValidationError("video_file_unreadable: missing, unreadable or a symlink")
    resolved = Path(path).resolve(strict=False)
    data = _read_video(resolved)
    info = probe_bytes(data)
    problems = video_problems(info)
    if problems:
        raise ValidationError("Video refused, nothing changed: " + "; ".join(problems))
    options: dict[str, Any] = {key: None for key in OPTION_KEYS}
    options["mode"] = mode
    direct_only = privacy_level is not None or commercial is not None or allow_comment or allow_duet \
        or allow_stitch or aigc
    if mode == "creator_inbox":
        if direct_only:
            raise ValidationError("--privacy-level, --commercial, --allow-* and --aigc apply to direct_post only; "
                                  "in creator_inbox mode the creator chooses them in the TikTok app")
    else:
        if privacy_level not in PRIVACY_LEVELS:
            raise ValidationError("direct_post needs an explicit --privacy-level (no default): "
                                  + ", ".join(PRIVACY_LEVELS))
        if commercial not in COMMERCIAL_CHOICES:
            raise ValidationError("direct_post needs an explicit --commercial: " + ", ".join(COMMERCIAL_CHOICES))
        if commercial == "branded_content" and privacy_level == "SELF_ONLY":
            raise ValidationError("branded content cannot be private (SELF_ONLY) per the Content Sharing Guidelines")
        options.update(privacy_level=privacy_level, commercial=commercial, disable_comment=not allow_comment,
                       disable_duet=not allow_duet, disable_stitch=not allow_stitch, is_aigc=bool(aigc))
    return make_record("tiktok", str(resolved), hashlib.sha256(data).hexdigest(), len(data), VIDEO_MIME, info,
                       options)


def record_mode(record: dict) -> str:
    """``creator_inbox`` or ``direct_post`` (the registry switch that must be live)."""
    return record["platform_options"]["mode"]


def post_settings(record: dict) -> dict | None:
    """The Direct Post ``post_info`` settings derived from the bound options; None for creator_inbox."""
    options = record["platform_options"]
    if options["mode"] != "direct_post":
        return None
    return {"privacy_level": options["privacy_level"], "disable_comment": options["disable_comment"],
            "disable_duet": options["disable_duet"], "disable_stitch": options["disable_stitch"],
            "brand_content_toggle": options["commercial"] == "branded_content",
            "brand_organic_toggle": options["commercial"] == "own_brand", "is_aigc": options["is_aigc"]}


def check_record(record: object) -> dict:
    """Strict check of a stored TikTok video record (shared core + TikTok options); a malformed
    record is never sent."""
    from .video import check_core
    check_core(record)
    assert isinstance(record, dict)
    options = record["platform_options"]
    if record["platform"] != "tiktok" or record["mime"] != VIDEO_MIME \
            or not 1 <= record["bytes"] <= MAX_VIDEO_BYTES or set(options) != set(OPTION_KEYS) \
            or options["mode"] not in MODES:
        raise ValidationError("video_record_malformed: run video-detach, then video-attach again")
    rest = [options[key] for key in OPTION_KEYS[1:]]
    if options["mode"] == "creator_inbox":
        if any(value is not None for value in rest):
            raise ValidationError("video_record_malformed: creator_inbox carries no post settings")
    elif not (options["privacy_level"] in PRIVACY_LEVELS and options["commercial"] in COMMERCIAL_CHOICES
              and all(type(options[k]) is bool for k in ("disable_comment", "disable_duet", "disable_stitch",
                                                          "is_aigc"))
              and not (options["commercial"] == "branded_content" and options["privacy_level"] == "SELF_ONLY")):
        raise ValidationError("video_record_malformed: direct_post settings invalid")
    return record


def file_issue(record: dict) -> str | None:
    """Offline (``video.file_issue``): None when the file still holds the approved, usable bytes."""
    try:
        load_bound_video(record)
    except ValidationError as exc:
        return str(exc)
    return None


def published_strings(record: dict) -> list[str]:
    """Strings besides the caption that a send publishes: none (TikTok options are not text)."""
    return []


def load_bound_video(record: dict) -> bytes:
    """Re-read the file and return its bytes only if they still hash to the approved value."""
    from .media import probe_bytes
    from .storage import BINDING_VIDEO_CHANGED
    check_record(record)
    try:
        data = _read_video(Path(record["path"]))
    except ValidationError as exc:
        raise VideoChanged(f"{BINDING_VIDEO_CHANGED}: {exc}") from None
    if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise VideoChanged(f"{BINDING_VIDEO_CHANGED}: the video file changed after approval")
    problems = video_problems(probe_bytes(data))
    if problems:
        raise ValidationError("tiktok_video_refused: " + "; ".join(problems))
    return data


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per the media transfer guide."""
    if type(size) is not int or not 1 <= size <= MAX_VIDEO_BYTES:
        raise ValidationError("tiktok_video_size_invalid")
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    count = size // CHUNK_SIZE
    if not 1 <= count <= MAX_CHUNKS:
        raise ValidationError("tiktok_chunk_count_invalid")
    return CHUNK_SIZE, count


def chunk_ranges(size: int, chunk_size: int, count: int) -> list[tuple[int, int]]:
    """Inclusive byte ranges; the final chunk carries the remainder (below 2 x chunk_size)."""
    ranges = [(i * chunk_size, (i + 1) * chunk_size - 1) for i in range(count - 1)]
    ranges.append(((count - 1) * chunk_size, size - 1))
    return ranges


# --------------------------------------------------------------------------- network

def _token(account: dict) -> str:
    name = account["env_prefix"] + CREDENTIAL_SUFFIX
    token = (credential(name) or "").strip()
    if not token:
        raise ValidationError("missing_credentials: " + name)
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}


_HINTS = {
    ("user_info", "http_401"): "tiktok_user_info_http_401: token invalid or expired (valid 24 hours); refresh it",
    ("creator_info", "http_401"): "tiktok_creator_info_http_401: token invalid, expired or lacks video.publish",
    ("init", "http_401"): "tiktok_init_http_401: token invalid, expired or lacks the upload/publish scope",
    ("init", "http_403"): ("tiktok_init_http_403: refused (pending-share or post cap, banned, unaudited client "
                           "or privacy mismatch); nothing was uploaded"),
}
_POST_WRITE_STAGES = frozenset({"upload", "status"})


def _actionable(stage: str, code: str) -> str:
    """A sanitized stage code; never adds remote text. Post-write stages are always uncertain."""
    hint = _HINTS.get((stage, code))
    if hint:
        return hint
    text = f"tiktok_{stage}_{code}"
    if stage in _POST_WRITE_STAGES and not text.endswith("_uncertain"):
        text += "_uncertain"
    return text


def _api_data(body: dict, stage: str) -> dict:
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code != "ok":
        raise publishers.PublishError(_actionable(stage, code if code in KNOWN_ERRORS else "api_error"))
    data = body.get("data")
    if not isinstance(data, dict):
        raise publishers.PublishError(_actionable(stage, "missing_data"))
    return data


def _post_json(path: str, payload: dict, token: str, stage: str) -> dict:
    try:
        body = publishers.request_json(API_BASE + path, payload, headers=_headers(token))
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable(stage, str(exc))) from None
    return _api_data(body, stage)


def verify_identity(account: dict) -> dict:
    """GET /v2/user/info/: open_id and display_name must equal the config exactly."""
    open_id, display_name = preflight(account)
    token = _token(account)
    url = API_BASE + USER_INFO_PATH + "?" + urlencode({"fields": "open_id,display_name"})
    try:
        body = publishers.request_get_json(url, {"Authorization": f"Bearer {token}"})
    except publishers.PublishError as exc:
        raise exc.with_code(_actionable("user_info", str(exc))) from None
    user = _api_data(body, "user_info").get("user")
    if not isinstance(user, dict) or not isinstance(user.get("open_id"), str) \
            or not isinstance(user.get("display_name"), str):
        raise publishers.PublishError("tiktok_user_info_missing_identity")
    if user["open_id"] != open_id or user["display_name"] != display_name:
        raise ValidationError("identity_mismatch: TikTok token belongs to a different creator than configured")
    return {"open_id": open_id, "display_name": display_name}


def check_creator_info(account: dict, record: dict) -> dict:
    """Direct Post only, read-only: creator, live privacy options, interaction locks, max duration."""
    _, display_name = preflight(account)
    settings = post_settings(record)
    assert settings is not None  # Direct Post only
    data = _post_json(CREATOR_INFO_PATH, {}, _token(account), "creator_info")
    if data.get("creator_nickname") != display_name:
        raise ValidationError("identity_mismatch: creator_info nickname differs from the configured display_name")
    handle = account.get("handle")
    username = data.get("creator_username")
    if handle and (not isinstance(username, str) or username.casefold() != str(handle).lstrip("@").casefold()):
        raise ValidationError("identity_mismatch: creator_info username differs from the configured handle")
    options = data.get("privacy_level_options")
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise publishers.PublishError("tiktok_creator_info_missing_privacy_options")
    if settings["privacy_level"] not in options:
        raise ValidationError(f"tiktok_privacy_level_not_offered: {settings['privacy_level']} is not among the "
                              f"creator's options ({', '.join(o for o in options if o in PRIVACY_LEVELS)}); "
                              "re-attach with an offered level and re-approve; nothing was sent")
    for key in ("comment", "duet", "stitch"):
        if settings[f"disable_{key}"] is False and data.get(f"{key}_disabled") is True:
            raise ValidationError(f"tiktok_{key}_disabled_by_creator: re-attach with {key} off; nothing was sent")
    max_duration = data.get("max_video_post_duration_sec")
    if type(max_duration) is not int or max_duration <= 0:
        raise publishers.PublishError("tiktok_creator_info_missing_max_duration")
    if record["duration_seconds"] > max_duration:
        raise ValidationError(f"tiktok_video_too_long_for_creator: limit {max_duration}s; nothing was sent")
    return {"privacy_level_options": [o for o in options if o in PRIVACY_LEVELS], "max_duration": max_duration}


def check_connection(account: dict, record: dict | None = None) -> VerifiedTikTok:
    """Identity preflight before the claim (``connect-check`` and ``run_live_send``)."""
    mode = record_mode(record) if record else "creator_inbox"
    require_video_mode("tiktok", mode)
    verify_identity(account)
    if mode == "direct_post" and record is not None:
        check_creator_info(account, record)
    return VerifiedTikTok(account["id"], mode)


def _upload_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise publishers.PublishError("tiktok_init_missing_upload_url")
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host.endswith(UPLOAD_HOST_SUFFIX) or parts.port is not None \
            or parts.username or parts.password:
        raise publishers.PublishError("tiktok_init_upload_url_refused")
    return value


def _upload(url: str, data: bytes, chunk_size: int, count: int) -> None:
    ranges = chunk_ranges(len(data), chunk_size, count)
    view = memoryview(data)
    for index, (first, last) in enumerate(ranges):
        chunk = bytes(view[first:last + 1])
        headers = {"Content-Type": VIDEO_MIME, "Content-Length": str(len(chunk)),
                   "Content-Range": f"bytes {first}-{last}/{len(data)}"}
        try:
            status = publishers.request_put_bytes(url, chunk, headers)
        except publishers.PublishError as exc:
            raise exc.with_code(_actionable("upload", str(exc))) from None
        expected = 201 if index == len(ranges) - 1 else 206
        if status != expected:
            raise publishers.PublishError(f"tiktok_upload_unexpected_status_{int(status)}_uncertain")


def _await_outcome(mode: str, publish_id: str, token: str, privacy_level: str | None) -> publishers.Receipt:
    complete_without_id = False
    for _ in range(STATUS_MAX_CHECKS):
        _sleep(STATUS_POLL_SECONDS)
        data = _post_json(STATUS_PATH, {"publish_id": publish_id}, token, "status")
        status = data.get("status")
        if status == "FAILED":
            reason = data.get("fail_reason")
            reason = reason if isinstance(reason, str) and _FAIL_REASON.fullmatch(reason) else "unknown"
            raise publishers.PublishError(f"tiktok_status_failed_{reason}_not_posted")
        if mode == "creator_inbox":
            if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                raise CreatorInboxDelivered(publish_id, status)
        elif status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id")
            first = ids[0] if isinstance(ids, list) and ids else None
            if first is not None and not isinstance(first, bool) and PUBLIC_POST_ID.fullmatch(str(first)):
                return publishers.Receipt(str(first), None, publish_id=publish_id, delivery="direct_post",
                                          privacy_level=privacy_level or "unknown")
            complete_without_id = True  # private, or still in moderation: keep polling
            continue
        elif status == "SEND_TO_USER_INBOX":
            raise publishers.PublishError("tiktok_status_unexpected_inbox_uncertain")
        if status not in ("PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD"):
            raise publishers.PublishError("tiktok_status_unrecognized_uncertain")
    if complete_without_id:
        raise publishers.PublishError("tiktok_direct_post_complete_no_public_id_uncertain")
    raise publishers.PublishError("tiktok_status_unconfirmed_uncertain")


def deliver(account: dict, content: str, record: dict, data: bytes, *,
            verified: VerifiedTikTok | None) -> publishers.Receipt:
    """Init, chunked upload and status polling for one bound video.

    Returns a Receipt only for a Direct Post that TikTok reports public (with a post id). A
    creator-inbox delivery raises ``CreatorInboxDelivered``. ``mark_write_started()`` comes after
    the init (which creates a publish_id and an upload URL but no content) and before the first
    chunk PUT, so every later failure is ambiguous and goes to needs_review, never retried.
    """
    check_record(record)
    mode = record_mode(record)
    require_video_mode("tiktok", mode)  # defense in depth for library callers
    if not (isinstance(verified, VerifiedTikTok) and verified.account_id == account.get("id")
            and verified.mode == mode and verified.identity == "verified"):
        raise ValidationError("tiktok_identity_not_verified: run check_connection immediately before deliver")
    preflight(account)
    if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
        from .storage import BINDING_VIDEO_CHANGED
        raise ValidationError(f"{BINDING_VIDEO_CHANGED}: bytes differ from the approved video")
    problems = validate_post("tiktok", content, account)
    if problems:
        raise ValidationError("; ".join(problems))
    token = _token(account)
    chunk_size, count = chunk_plan(len(data))
    source = {"source": "FILE_UPLOAD", "video_size": len(data), "chunk_size": chunk_size,
              "total_chunk_count": count}
    settings = post_settings(record)
    body: dict[str, Any]
    if mode == "creator_inbox":
        path, body = INBOX_INIT_PATH, {"source_info": source}  # caption is written by the creator in-app
    else:
        assert settings is not None
        path = DIRECT_INIT_PATH
        body = {"post_info": {"title": content, **settings}, "source_info": source}
    init = _post_json(path, body, token, "init")
    publish_id = init.get("publish_id")
    if not isinstance(publish_id, str) or not PUBLISH_ID.fullmatch(publish_id):
        raise publishers.PublishError("tiktok_init_missing_publish_id")
    url = _upload_url(init.get("upload_url"))
    publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
    _upload(url, data, chunk_size, count)
    return _await_outcome(mode, publish_id, token, settings["privacy_level"] if settings else None)


# --------------------------------------------------------------------------- live send

def record_inbox_delivery(con, post_id: str, publish_id: str, final_status: str) -> None:
    """sending -> needs_review with the publish_id. Never ``published``: nothing public exists yet."""
    from .storage import event
    con.execute("UPDATE posts SET status='needs_review',remote_id=?,last_error=? WHERE id=? AND status='sending'",
                (REMOTE_PREFIX + publish_id, INBOX_REASON, post_id))
    event(con, "needs_review", post_id,
          f"reason={INBOX_REASON} delivery=creator_inbox publish_id={publish_id} final_status={final_status}")


def run_live_send(con, post, account: dict, *, max_per_24h: int, min_gap_minutes: int, optout: list,
                  wlog, cooldown_floor_seconds: int, preflight_max_retries: int) -> int:
    """One guarded TikTok send for the CLI loop. Returns 0, 2 (blocked/skipped) or 3 (review).

    The CLI has already passed ``--live``, ``require_live_gate()`` and the emergency pause.
    Order here (the same as the native and Postiz loops): registry gate, opt-out, approval
    binding (keyed when #7 D7 is on), human confirmation (#7 D6, opt-in), provider cooldown
    (with any #11 shared scope), video record, file re-hash, credentials, caption checks,
    identity preflight, claim (pause, binding and budgets under lock), write marker, then
    needs_review for anything after the claim.
    """
    from .confirmation import confirm_live_send
    from .optout import publish_block_reason
    from .ratelimit import (AMBIGUOUS, classify_send_failure, cooldown_scopes, is_rate_limited, preflight_retries,
                            preflight_with_backoff, record_rate_limit)
    from .storage import (BINDING_VIDEO_CHANGED, active_provider_cooldown, approval_payload_sha256,
                          binding_refusal, check_approval_binding, claim_sending, cooldown_refusal, finish_send,
                          is_paused, post_video, published_text, record_binding_failure, uncertain_send)
    from .worker_log import claim_refusal_reason, error_code
    pid = post["id"]
    try:
        record = post_video(post)
    except ValidationError:
        record = None
    mode = record_mode(record) if record is not None else "creator_inbox"
    try:  # FIRST gate: the registry (and SUPPORTED_LIVE_VIDEO) must allow this mode. No I/O before it.
        require_video_mode("tiktok", mode)
    except ValidationError as exc:
        print(f"SKIPPED {pid}: {exc}")
        wlog.skipped(post, "capability_refused")
        return 2
    reason = publish_block_reason(published_text(post), optout, "tiktok")
    if reason:
        print(f"BLOCKED {pid}: {reason}; not claimed, post stays approved, nothing uploaded")
        wlog.skipped(post, "optout_blocked")
        return 2
    problem = check_approval_binding(con, pid)
    if problem:
        print(f"BLOCKED {pid}: {binding_refusal(problem, pid)}; nothing uploaded")
        wlog.skipped(post, "rebind_required")
        return 2
    # Human confirmation (#7 D6, opt-in): pre-claim, pre-network; a failure raises and stops the run.
    confirm_live_send(con, post)
    scopes = cooldown_scopes(account)  # () for a direct adapter; validated at load (#11)
    cooldown = active_provider_cooldown(con, account["id"], scopes=scopes)
    if cooldown:
        print(f"SKIPPED {pid}: {cooldown_refusal(cooldown)}; not claimed, no request sent")
        wlog.skipped(post, "cooldown")
        return 2
    try:
        if record is None:
            raise ValidationError("tiktok posts need an attached video (video-attach), then re-approval")
        check_record(record)
        problems = validate_post("tiktok", post["content"], account)
        if problems:
            raise ValidationError("; ".join(problems))
        data = load_bound_video(record)
    except VideoChanged as exc:
        record_binding_failure(con, pid, BINDING_VIDEO_CHANGED)
        print(f"BLOCKED {pid}: {exc}; {binding_refusal(BINDING_VIDEO_CHANGED, pid)}; nothing uploaded")
        wlog.skipped(post, "rebind_required")
        return 2
    except ValidationError as exc:
        print(f"SKIPPED {pid}: failed preflight check ({exc})")
        wlog.skipped(post, "content_preflight_failed")
        return 2
    name = account["env_prefix"] + CREDENTIAL_SUFFIX
    if not credential(name):
        print(f"SKIPPED {pid}: configure environment variables: {name}")
        wlog.skipped(post, "credentials_missing")
        return 2
    try:  # read-only: GET user info (+ creator_info query for Direct Post); bounded retries (#11)
        verified = preflight_with_backoff(lambda: check_connection(account, record),
                                          max_retries=preflight_retries("tiktok", preflight_max_retries),
                                          paused=lambda: is_paused(con), retry_on=publishers.PublishError)
    except (ValidationError, publishers.PublishError) as exc:
        note = ""
        if is_rate_limited(exc):
            recorded = record_rate_limit(con, account, exc, stage="preflight", floor_seconds=cooldown_floor_seconds)
            note = f"; {cooldown_refusal(recorded)} recorded"
        print(f"BLOCKED {pid}: account identity preflight failed ({exc}){note}; nothing uploaded")
        wlog.skipped(post, "identity_preflight_failed", rate_limited=bool(note))
        return 2
    try:
        claimed = claim_sending(con, pid, max_per_24h=max_per_24h, min_gap_minutes=min_gap_minutes,
                                expected_sha256=approval_payload_sha256(post),  # binds caption AND video record
                                cooldown_scopes=scopes)
    except ValidationError as exc:
        print(f"BLOCKED {pid}: {exc}; nothing uploaded")
        wlog.skipped(post, claim_refusal_reason(exc))
        return 2
    if not claimed:
        print(f"SKIPPED {pid}: no longer eligible")
        wlog.skipped(post, "not_eligible")
        return 2
    wlog.claimed(post)
    publishers.reset_write_marker()
    try:
        receipt = deliver(account, post["content"], record, data, verified=verified)
        remote_id, remote_url = receipt
    except CreatorInboxDelivered as delivered:
        record_inbox_delivery(con, pid, delivered.publish_id, delivered.final_status)
        print(f"INBOX {pid}: video delivered to the creator's TikTok inbox (publish_id {delivered.publish_id}, "
              f"{delivered.final_status}); NOT public. Finish and post it in the TikTok app, then reconcile with "
              "the native video id/URL")
        wlog.needs_review(post, send_class="unknown", code=INBOX_REASON)  # delivered; the creator decides
    except (publishers.PublishError, ValidationError) as exc:
        send_class = classify_send_failure("tiktok", None, exc, write_started=publishers.write_started())
        uncertain_send(con, pid, str(exc), send_class=send_class)
        note = ""
        if is_rate_limited(exc):
            cooldown = record_rate_limit(con, account, exc, stage="send", post_id=pid, send_class=send_class,
                                         floor_seconds=cooldown_floor_seconds)
            note = f"; {cooldown_refusal(cooldown)} recorded, this post is never resent automatically"
        print(f"REVIEW {pid}: {exc} [{send_class}]{note}; check the TikTok inbox and profile before reconciliation")
        wlog.needs_review(post, send_class=send_class, code=error_code(exc), rate_limited=bool(note))
    except Exception:
        uncertain_send(con, pid, "tiktok_unexpected_error_uncertain", send_class=AMBIGUOUS)
        print(f"REVIEW {pid}: unexpected error; inspect manually; not retried")
        wlog.needs_review(post, send_class=AMBIGUOUS, code="tiktok_unexpected_error_uncertain")
    else:
        finish_send(con, pid, remote_id, remote_url, getattr(receipt, "extra", None))
        print(f"PUBLISHED {pid}: TikTok post {remote_id} (public id returned by the API)")
        wlog.published(post, remote_id, remote_url)
        return 0
    return 3
