"""YouTube video / Shorts upload (issue #22). Owner-approved 2026-10-09; disabled until a live receipt.

The capability registry keeps YouTube's ``reachweaver_video_upload`` switch "off" (and ``video:
"api_gated"``), and ``policy.SUPPORTED_LIVE_VIDEO`` is empty, so ``publishers.publish_video``,
``connections.check_connection`` and ``publish --live`` refuse YouTube before any credential
read or request (``capabilities.require_video_mode``). This module only becomes reachable after
the owner's own OAuth client passes Google's verification/audit, a supervised first live receipt
and a reviewed registry change (docs/SECURITY-REVIEW-PROPOSAL-22-youtube.md).

The draft's own spec and ``approval/2`` tag were replaced at integration (2026-10-09) by the
shared video record of ``video.py``: core fields plus ``platform_options`` ``{title, tags,
category_id, privacy_status, self_declared_made_for_kids}``, bound under
``reachweaver.storage.approval/video-1`` (everything except the local path).

Scope: one MP4 upload to the authenticated owner's own channel, with title, description (the
post ``content``), tags, category, ``privacyStatus`` (default ``private``) and an explicit
human ``selfDeclaredMadeForKids`` answer. No thumbnails, playlists, scheduling (``publishAt``),
captions, localizations, license, embeddable or notify-subscribers fields are ever sent.

Facts read 2026-10-09 from official Google sources:

* ``POST https://www.googleapis.com/upload/youtube/v3/videos`` with ``uploadType=resumable``
  and ``part``; headers ``Authorization``, ``Content-Type: application/json; charset=UTF-8``,
  ``X-Upload-Content-Length`` and ``X-Upload-Content-Type`` (``video/*`` or
  ``application/octet-stream``); success is ``200 OK`` with the session URI in ``Location``.
  Bytes go by ``PUT`` to that URI; chunks use ``Content-Range: bytes FIRST-LAST/TOTAL``, must
  be a multiple of 256 KB (only the last may be smaller), and every non-final chunk is answered
  ``308 Resume Incomplete`` with ``Range: bytes=0-N``. The final chunk returns ``201 Created``
  with the video resource. An interrupted upload (no response, 500/502/503/504) can be resumed
  by an empty ``PUT`` with ``Content-Range: bytes */TOTAL``; ``404`` means the session expired.
  The session URI has a "finite lifetime" (no duration stated).
  https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
* Maximum file size 256GB; accepted ``video/*`` and ``application/octet-stream``; errors
  include ``uploadLimitExceeded``, ``invalidTitle``, ``invalidDescription``, ``invalidTags``,
  ``invalidCategoryId`` (400) and ``forbidden``, ``forbiddenPrivacySetting`` (403). The page
  says "Videos uploaded from unverified API projects are not restricted to private viewing
  mode", while the revision history (2020-07-28 entry, never revoked there) says uploads from
  unverified projects created after 2020-07-28 "will be restricted to private viewing mode"
  until the project passes an audit. The conflict is unresolved, so ReachWeaver treats public
  visibility as unverified and checks the returned ``privacyStatus``.
  https://developers.google.com/youtube/v3/docs/videos/insert
  https://developers.google.com/youtube/v3/revision_history
* Video resource: ``snippet.title`` at most 100 characters, ``snippet.description`` at most
  5000 bytes, both without ``<`` or ``>``; ``snippet.tags`` at most 500 characters in total,
  commas between tags count and a tag with a space counts as if quoted (+2);
  ``status.privacyStatus`` is ``private``, ``public`` or ``unlisted`` and "defaults to public"
  when omitted (so ReachWeaver always sends it); ``status.selfDeclaredMadeForKids`` is the
  writable made-for-kids field; ``status.uploadStatus`` is ``deleted``, ``failed``,
  ``processed``, ``rejected`` or ``uploaded``. https://developers.google.com/youtube/v3/docs/videos
* Quota: ``videos.insert`` has its own bucket, default 100 calls per day at 1 unit per call
  (separate buckets since 2026-06-01); other endpoints share 10,000 units per day;
  ``channels.list`` costs 1 unit; daily quotas reset at midnight Pacific Time.
  https://developers.google.com/youtube/v3/determine_quota_cost
  https://developers.google.com/youtube/v3/docs/channels/list
* Errors: ``quotaExceeded``, ``rateLimitExceeded``, ``dailyLimitExceeded`` and
  ``userRateLimitExceeded`` are 403; ``authError``/``unauthorized`` are 401; the body is
  ``{"error": {"errors": [{"reason": ...}], "code": ...}}``.
  https://developers.google.com/youtube/v3/docs/core_errors
* ``GET https://www.googleapis.com/youtube/v3/channels?part=id&mine=true`` returns the
  channels the authenticated user owns. https://developers.google.com/youtube/v3/docs/channels/list
* Made for kids: "Regardless of your location, we require you to tell us whether or not your
  videos are made for kids." https://support.google.com/youtube/answer/9528076
* Shorts: "up to three minutes in length" with a "square or vertical aspect ratio" (videos
  uploaded after 2024-10-15). YouTube classifies Shorts automatically; there is no API flag.
  https://support.google.com/youtube/answer/15424877

Not verified (fail closed, documented, never relied on): the channel id shape (``UC`` + 22
characters is the observed shape, not documented); the video id shape; the public watch URL
``https://www.youtube.com/watch?v=ID`` (not in the Data API reference; stored as a convenience,
the video id is the receipt); whether the session-initiation POST alone can ever create a
video (the protocol returns 201 Created only on the final chunk, so ReachWeaver treats it as
creating nothing).

Credentials come only from ``{env_prefix}_ACCESS_TOKEN`` via ``accounts.credential``; there is
no refresh flow. The session URI is a bearer-capability URL: it is never printed, logged,
persisted or put in an error. No error raised here contains the token, a URL, request headers
or remote body text (only an allowlisted ``reason`` token is kept).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from . import publishers
from .accounts import credential
from .policy import ValidationError

API_HOST = "www.googleapis.com"
CHANNELS_URL = f"https://{API_HOST}/youtube/v3/channels"
UPLOAD_PATH = "/upload/youtube/v3/videos"
UPLOAD_PARTS = "snippet,status"
CREDENTIAL_SUFFIX = "_ACCESS_TOKEN"
MIME_TYPE = "video/mp4"                 # ReachWeaver uploads MP4 only (media.py container check)
TITLE_MAX_CHARS = 100                   # official
DESCRIPTION_MAX_BYTES = 5000            # official (UTF-8 bytes)
TAGS_MAX_CHARS = 500                    # official, counted as documented
MAX_FILE_BYTES = 256 * 1000 ** 3        # official "256GB", read as decimal (the lower reading)
CHUNK_UNIT = 256 * 1024                 # official "multiple of 256 KB"
CHUNK_SIZE = 32 * CHUNK_UNIT            # 8 MiB per PUT
PRIVACY_STATUSES = ("private", "unlisted", "public")
DEFAULT_PRIVACY = "private"             # ReachWeaver default; the API default is public
UPLOADS_PER_DAY_DEFAULT = 100           # official default videos.insert bucket (1 unit per call)
SHORTS_MAX_SECONDS = 180                # official, for the advisory Shorts hint only
MAX_RESPONSE_BYTES = 1_000_000
MAX_ERROR_BODY_BYTES = 4096
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")   # observed shape, not documented: fail closed
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")      # not documented: fail closed
CATEGORY_ID = re.compile(r"^[0-9]{1,4}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_RANGE = re.compile(r"^bytes=0-([0-9]{1,15})$")
# Error reasons kept from a provider error body (everything else in the body is discarded).
ERROR_REASONS = frozenset({
    "quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded", "userRateLimitExceeded",
    "authError", "unauthorized", "forbidden", "forbiddenPrivacySetting", "forbiddenLicenseSetting",
    "uploadLimitExceeded", "invalidTitle", "invalidDescription", "invalidTags", "invalidCategoryId",
    "invalidVideoMetadata", "invalidFilename", "mediaBodyRequired", "defaultLanguageNotSet",
})
_ALLOWED_KEYS = frozenset({"id", "platform", "env_prefix", "handle", "channel_id"})
# The flat view the upload code works on; built from the shared video record by ``_spec``.
SPEC_KEYS = ("path", "sha256", "size", "mime", "title", "tags", "privacy_status", "made_for_kids",
             "category_id")
# ``platform_options`` of the shared video record (``video.py``, phase 2 design, 2026-10-09); every
# option is bound by the approval, and the description is the post ``content``.
OPTION_KEYS = ("title", "tags", "category_id", "privacy_status", "self_declared_made_for_kids")
_HASH_BLOCK = 1024 * 1024


# --------------------------------------------------------------------------- config

def validate_account_config(raw: dict, account_id: str) -> None:
    """Called by ``accounts.load_accounts``.

    Without ``channel_id`` the account is manual-only metadata (manual-export, calendar) and
    loads as before. With it, only documented non-secret keys are allowed.
    """
    if "channel_id" not in raw:
        return
    extra = sorted(set(raw) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError(f"YouTube account has unsupported fields {', '.join(extra)}; only uploads "
                              f"to your own channel are supported (account {account_id})")
    if not isinstance(raw.get("channel_id"), str) or not CHANNEL_ID.fullmatch(raw["channel_id"].strip()):
        raise ValidationError(f"YouTube channel_id must be your channel id (UC followed by 22 characters) "
                              f"(account {account_id})")
    raw["channel_id"] = raw["channel_id"].strip()


def preflight(account: dict) -> str:
    """Offline, fail-closed checks before any YouTube request. Returns the channel id."""
    if account.get("platform") != "youtube" or account.get("backend") is not None:
        raise ValidationError("youtube_preflight_wrong_platform")
    extra = sorted(set(account) - _ALLOWED_KEYS)
    if extra:
        raise ValidationError("youtube_unsupported_fields: " + ", ".join(extra))
    channel = account.get("channel_id")
    if not isinstance(channel, str) or not CHANNEL_ID.fullmatch(channel):
        raise ValidationError("youtube_channel_id_required: set channel_id to your own channel id")
    return channel


def doctor_notes(account: dict) -> list[str]:
    """Offline notes for ``doctor``; never reads the token value."""
    notes = ["INFO: YouTube upload adapter is owner-approved but blocked by the capability registry until a "
             "live receipt (docs/SECURITY-REVIEW-PROPOSAL-22-youtube.md); upload in YouTube Studio meanwhile"]
    if "channel_id" not in account:
        notes.append("INFO: no channel_id configured; this account is manual-export only")
    notes.append("INFO: no token refresh: the access token must be fresh at send time; uploads from an "
                 "unverified Google API project may be locked private")
    return notes


# --------------------------------------------------------------------------- text and metadata

def _angle_or_control(text: str) -> bool:
    return any(ch in "<>" for ch in text) or any(ord(ch) < 32 and ch not in "\n\t\r" for ch in text)


def description_problems(content: str) -> list[str]:
    """Official description rules (5000 UTF-8 bytes, no ``<`` or ``>``); tighten-only."""
    problems = []
    size = len(content.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        problems.append(f"YouTube description is {size} UTF-8 bytes; limit is {DESCRIPTION_MAX_BYTES}")
    if "<" in content or ">" in content:
        problems.append("YouTube description may not contain '<' or '>'")
    return problems


def tags_length(tags: list[str]) -> int:
    """Documented count: tag lengths, +2 per tag containing a space, plus the separating commas."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)


def metadata_problems(spec: dict) -> list[str]:
    """Title, tags, category, privacy and made-for-kids rules for a stored video spec."""
    problems = []
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("YouTube title is required")
    else:
        if len(title) > TITLE_MAX_CHARS:
            problems.append(f"YouTube title has {len(title)} characters; limit is {TITLE_MAX_CHARS}")
        if _angle_or_control(title) or "\n" in title or "\r" in title or "\t" in title:
            problems.append("YouTube title may not contain '<', '>', line breaks or control characters")
    tags = spec.get("tags")
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        problems.append("YouTube tags must be a list of text")
    else:
        if any(not t.strip() or t != t.strip() or "," in t or _angle_or_control(t) or "\n" in t for t in tags):
            problems.append("YouTube tags must be non-empty, trimmed and free of ',', '<', '>' and control characters")
        if len(set(tags)) != len(tags):
            problems.append("YouTube tags must not repeat")
        if tags_length(tags) > TAGS_MAX_CHARS:
            problems.append(f"YouTube tags count as {tags_length(tags)} characters; limit is {TAGS_MAX_CHARS}")
    if spec.get("privacy_status") not in PRIVACY_STATUSES:
        problems.append("YouTube privacy_status must be private, unlisted or public")
    if type(spec.get("made_for_kids")) is not bool:
        problems.append("YouTube made_for_kids must be answered explicitly by a human (yes or no)")
    category = spec.get("category_id")
    if not isinstance(category, str) or not CATEGORY_ID.fullmatch(category):
        problems.append("YouTube category_id must be a numeric category id (see videoCategories.list)")
    if spec.get("mime") != MIME_TYPE:
        problems.append("YouTube upload must be an MP4 file")
    size = spec.get("size")
    if type(size) is not int or not 0 < size <= MAX_FILE_BYTES:
        problems.append("YouTube video size must be 1 byte..256GB")
    if not isinstance(spec.get("sha256"), str) or not SHA256_HEX.fullmatch(spec["sha256"]):
        problems.append("YouTube video sha256 is missing or malformed")
    if not isinstance(spec.get("path"), str) or not os.path.isabs(spec["path"]):
        problems.append("YouTube video path must be absolute")
    return problems


def _spec(record: dict) -> dict:
    """The flat upload view of a shared video record (``SPEC_KEYS``)."""
    options = record["platform_options"]
    return {"path": record["path"], "sha256": record["sha256"], "size": record["bytes"], "mime": record["mime"],
            "title": options["title"], "tags": options["tags"], "privacy_status": options["privacy_status"],
            "made_for_kids": options["self_declared_made_for_kids"], "category_id": options["category_id"]}


def check_record(record: object) -> dict:
    """Strict check of a stored YouTube video record (shared core + YouTube options); any defect
    raises ValidationError, so callers fail closed."""
    from .video import check_core
    check_core(record)
    assert isinstance(record, dict)
    options = record["platform_options"]
    if record["platform"] != "youtube" or set(options) != set(OPTION_KEYS) or metadata_problems(_spec(record)):
        raise ValidationError("youtube_video_spec_malformed: run youtube-detach, then youtube-attach again")
    return record


def encode_video(record: dict) -> str:
    """Canonical JSON of a YouTube video record (the shared codec, ``video.encode_video``)."""
    from .video import encode_video as encode
    return encode(record)


def decode_video(text: str) -> dict | None:
    """Strict decode of a stored record (the shared codec, ``video.decode_video``)."""
    from .video import decode_video as decode
    return decode(text)


PROCESSING_NOT_CONFIRMED = (
    "upload_status=uploaded: YouTube has the file but processing is not confirmed yet, so the video may "
    "not play for viewers; check it in YouTube Studio, or read status.uploadStatus later with videos.list "
    "part=status ('processed' means done). ReachWeaver does not re-check it")


def visibility_note(video_json: str | None, upload_status: str | None = None) -> str | None:
    """One plain line saying what the approved privacy means after a successful upload.

    A finished upload of a ``private`` or ``unlisted`` video is not a public video, so the publish
    output must never read as if it were (public issue #22/#52). A ``public`` upload whose receipt
    says ``uploaded`` (not ``processed``) is not confirmed watchable yet (private #22); the note says
    so and how to check. Text only: the receipt check and the send are unchanged. None when there is
    no stored spec.
    """
    try:
        record = decode_video(video_json) if video_json else None
    except ValidationError:
        return None  # a malformed stored record is reported elsewhere; the publish output must never fail on it
    options = record.get("platform_options") if isinstance(record, dict) else None
    privacy = options.get("privacy_status") if isinstance(options, dict) else None
    if privacy == "public":
        if upload_status == "uploaded":
            return f"YouTube privacy_status=public, as approved; {PROCESSING_NOT_CONFIRMED}"
        return "YouTube privacy_status=public, as approved"
    if privacy in ("private", "unlisted"):
        return (f"YouTube privacy_status={privacy}: the upload succeeded, but the video is NOT public; "
                "change its visibility yourself in YouTube Studio when you want it public")
    return None


def bound_view(record: dict) -> dict:
    """What the approval hash binds (``approval/video-1``): the whole record except its path."""
    from .video import bound_view as bound
    return bound(record)


def record_mode(record: dict) -> str:
    """The single YouTube delivery mode (registry switch ``reachweaver_video_upload``)."""
    return "upload"


def published_strings(record: dict) -> list[str]:
    """The title and tags are published besides the description (opt-out checks cover them)."""
    options = record["platform_options"]
    return [options["title"], *options["tags"]]


def file_issue(record: dict) -> str | None:
    """Offline (``video.file_issue``): None when the file still holds the recorded bytes."""
    return file_problem(record)


# --------------------------------------------------------------------------- file

def _open_video(path: str) -> int:
    """Open without following a final symlink; refuse anything but a regular file."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise ValidationError("youtube_video_file_missing") from None
    except OSError:
        raise ValidationError("youtube_video_file_unreadable (symlinks are refused)") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValidationError("youtube_video_file_not_regular")
    return fd


def _hash_fd(fd: int) -> tuple[str, int]:
    digest, total, offset = hashlib.sha256(), 0, 0
    while True:
        block = os.pread(fd, _HASH_BLOCK, offset)
        if not block:
            return digest.hexdigest(), total
        digest.update(block)
        total += len(block)
        offset += len(block)


def shorts_hint(info: dict) -> str:
    """Advisory only: YouTube decides automatically; there is no API flag for Shorts."""
    values = [info.get(k) for k in ("width", "height", "duration_seconds")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "unknown (dimensions or duration not parsed); YouTube classifies Shorts automatically"
    width, height, seconds = (float(v) for v in values)  # type: ignore[arg-type]
    if height >= width and seconds <= SHORTS_MAX_SECONDS:
        return ("likely_short: square/vertical and at most 3 minutes; YouTube classifies Shorts "
                "automatically (no API flag)")
    return "not_short: wider than tall or longer than 3 minutes"


def build_video_spec(path: str | Path, *, title: str, tags: list[str] | None, privacy_status: str,
                     made_for_kids: bool, category_id: str) -> tuple[dict, dict]:
    """Validate and hash a local MP4 for a draft. Returns (the shared video record, probe info)."""
    from .media import probe_file
    from .video import make_record
    absolute = Path(os.path.abspath(path))
    if absolute.is_symlink():
        raise ValidationError("youtube_video_file_unreadable (symlinks are refused)")
    fd = _open_video(str(absolute))
    try:
        info = probe_file(absolute)
        if info.get("container") != "mp4":
            raise ValidationError("YouTube upload must be an MP4 file (ftyp brand not QuickTime); "
                                  f"detected {info.get('container')}")
        sha256, size = _hash_fd(fd)
    finally:
        os.close(fd)
    spec = {"path": str(absolute), "sha256": sha256, "size": size, "mime": MIME_TYPE,
            "title": title, "tags": list(tags or []), "privacy_status": privacy_status,
            "made_for_kids": made_for_kids, "category_id": category_id}
    problems = metadata_problems(spec)
    if problems:
        raise ValidationError("; ".join(problems))
    options = {"title": title, "tags": list(tags or []), "category_id": category_id,
               "privacy_status": privacy_status, "self_declared_made_for_kids": made_for_kids}
    return make_record("youtube", str(absolute), sha256, size, MIME_TYPE, info, options), info


def file_problem(record: dict) -> str | None:
    """None when the file still holds exactly the recorded bytes; else a reason code."""
    spec = _spec(record) if "platform_options" in record else record
    try:
        fd = _open_video(spec["path"])
    except ValidationError as exc:
        return str(exc).split(" ")[0]
    try:
        sha256, size = _hash_fd(fd)
    finally:
        os.close(fd)
    if size != spec["size"] or sha256 != spec["sha256"]:
        return "youtube_video_file_changed"
    return None


# --------------------------------------------------------------------------- network

def _token(account: dict) -> str:
    name = account["env_prefix"] + CREDENTIAL_SUFFIX
    token = (credential(name) or "").strip()
    if not token:
        raise ValidationError("missing_credentials: " + name)
    return token


def _reason(exc: HTTPError) -> str | None:
    """First allowlisted ``error.errors[].reason`` from at most 4 KB of the body; nothing else."""
    try:
        raw = exc.read(MAX_ERROR_BODY_BYTES + 1)
    except Exception:  # noqa: BLE001 - an unreadable error body just has no reason
        return None
    if not isinstance(raw, bytes) or len(raw) > MAX_ERROR_BODY_BYTES:
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    error = body.get("error") if isinstance(body, dict) else None
    items = error.get("errors") if isinstance(error, dict) else None
    for item in items if isinstance(items, list) else []:
        reason = item.get("reason") if isinstance(item, dict) else None
        if isinstance(reason, str) and reason in ERROR_REASONS:
            return reason
    return None


_POST_WRITE = frozenset({"upload", "receipt"})


def _code(stage: str, code: str) -> str:
    """``youtube_<stage>_<code>``; every code after the write marker ends in ``_uncertain``."""
    full = f"youtube_{stage}_{code}"
    return full + "_uncertain" if stage in _POST_WRITE and not full.endswith("_uncertain") else full


def _exchange(req, stage: str) -> tuple[int, dict[str, str], bytes]:
    """Send one request; (status, lower-cased headers, body). 308 is returned, not raised.

    Errors are sanitized: the URL (the session URI is a capability), headers and body text
    never reach the exception; an allowlisted error reason may be appended to the code.
    """
    try:
        with publishers._open(req) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 308:
            headers = {str(k).lower(): str(v) for k, v in (exc.headers or {}).items()}
            exc.close()
            return 308, headers, b""
        base = publishers.http_error(exc)
        reason = _reason(exc)
        exc.close()
        raise base.with_code(_code(stage, f"http_{exc.code}" + (f"_{reason}" if reason else ""))) from None
    except publishers.PublishError as exc:
        raise exc.with_code(_code(stage, str(exc))) from None
    except (URLError, TimeoutError, OSError):
        raise publishers.PublishError(_code(stage, "network_or_timeout")) from None
    except http.client.HTTPException:
        raise publishers.PublishError(_code(stage, "invalid_api_response")) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise publishers.PublishError(_code(stage, "response_too_large"))
    return status, headers, raw


def _json(raw: bytes, stage: str) -> dict:
    try:
        data = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (ValueError, UnicodeError):
        raise publishers.PublishError(_code(stage, "invalid_api_response")) from None
    if not isinstance(data, dict):
        raise publishers.PublishError(_code(stage, "unexpected_response_type"))
    return data


def _headers(token: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {token}", "User-Agent": "ReachWeaver/0.3", **extra}


def verify_identity(account: dict) -> str:
    """``channels.list?part=id&mine=true`` must return exactly the configured channel (1 unit).

    Raises before the durable claim on any mismatch. It cannot prove the youtube.upload scope.
    """
    channel = preflight(account)
    token = _token(account)
    req = publishers._request(CHANNELS_URL + "?" + urlencode({"part": "id", "mine": "true"}),
                              method="GET", headers=_headers(token))
    status, _, raw = _exchange(req, "channels")
    data = _json(raw, "channels")
    items = data.get("items")
    ids = [item.get("id") for item in items if isinstance(item, dict)] if isinstance(items, list) else None
    if status != 200 or ids is None:
        raise publishers.PublishError("youtube_channels_missing_items")
    if ids != [channel]:
        raise ValidationError("identity_mismatch: YouTube token does not own exactly the configured channel")
    return channel


def _session_uri(value: str | None) -> str:
    """The resumable session URI must stay on the documented upload endpoint."""
    if not isinstance(value, str) or len(value) > 4096:
        raise publishers.PublishError("youtube_init_session_uri_invalid")
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname != API_HOST or parts.port is not None or parts.username
            or parts.password or parts.path != UPLOAD_PATH or not parts.query or parts.fragment):
        raise publishers.PublishError("youtube_init_session_uri_invalid")
    return value


def _metadata_body(content: str, spec: dict) -> dict:
    snippet = {"title": spec["title"], "description": content, "categoryId": spec["category_id"]}
    if spec["tags"]:
        snippet["tags"] = list(spec["tags"])
    return {"snippet": snippet,
            "status": {"privacyStatus": spec["privacy_status"], "selfDeclaredMadeForKids": spec["made_for_kids"]}}


def _receipt(data: dict, channel: str, spec: dict) -> publishers.Receipt:
    video_id = data.get("id")
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise publishers.PublishError("youtube_receipt_missing_video_id_uncertain")
    raw_snippet, raw_status = data.get("snippet"), data.get("status")
    snippet: dict = raw_snippet if isinstance(raw_snippet, dict) else {}
    status: dict = raw_status if isinstance(raw_status, dict) else {}
    if snippet.get("channelId") != channel:
        raise publishers.PublishError("youtube_receipt_channel_mismatch_uncertain")
    if status.get("privacyStatus") != spec["privacy_status"]:
        # e.g. an unverified API project locks uploads private: published, but not as approved.
        raise publishers.PublishError("youtube_receipt_privacy_mismatch_uncertain")
    if "selfDeclaredMadeForKids" in status and status["selfDeclaredMadeForKids"] is not spec["made_for_kids"]:
        raise publishers.PublishError("youtube_receipt_made_for_kids_mismatch_uncertain")
    upload_status = status.get("uploadStatus")
    if upload_status not in ("uploaded", "processed"):
        raise publishers.PublishError("youtube_receipt_upload_status_not_accepted_uncertain")
    return publishers.Receipt(video_id, f"https://www.youtube.com/watch?v={video_id}", channel_id=channel,
                              privacy_status=spec["privacy_status"], upload_status=upload_status,
                              sha256=spec["sha256"][:16])


def upload_video(account: dict, content: str, post_id: str, record: dict, *,
                 paused: Callable[[], bool] | None = None) -> publishers.Receipt:
    """Resumable upload of one approved MP4. ``post_id`` is local only (no idempotency key exists).

    Pass 1 (before the write marker): re-open the file without following symlinks and re-hash
    it; any difference is a ValidationError (certain not sent). Session-initiation POST (creates
    no video). ``mark_write_started()``. Pass 2: stream the same descriptor in chunks while
    hashing; the digest is compared BEFORE the final chunk, so a file changed during the upload
    never completes it. Every failure after the marker is ``_uncertain`` -> needs_review; an
    interrupted upload is never resumed automatically and the session URI is dropped.
    """
    check_record(record)
    spec = _spec(record)
    channel = preflight(account)
    problems = metadata_problems(spec) + description_problems(content)
    if problems:
        raise ValidationError("; ".join(problems))
    token = _token(account)
    fd = _open_video(spec["path"])
    try:
        sha256, size = _hash_fd(fd)  # pass 1
        if size != spec["size"] or sha256 != spec["sha256"]:
            raise ValidationError("youtube_video_file_changed: the file no longer holds the approved bytes; "
                                  "nothing was sent")
        body = json.dumps(_metadata_body(content, spec), ensure_ascii=False).encode("utf-8")
        init = publishers._request(
            f"https://{API_HOST}{UPLOAD_PATH}?" + urlencode({"uploadType": "resumable", "part": UPLOAD_PARTS}),
            data=body, method="POST",
            headers=_headers(token, **{"Content-Type": "application/json; charset=UTF-8",
                                       "X-Upload-Content-Length": str(size),
                                       "X-Upload-Content-Type": MIME_TYPE}))
        status, headers, _ = _exchange(init, "init")
        if status != 200:
            raise publishers.PublishError("youtube_init_unexpected_status")
        session = _session_uri(headers.get("location"))
        if paused is not None and paused():
            raise ValidationError("youtube_paused_before_upload: emergency pause; nothing was sent")
        publishers.mark_write_started()  # issue #11: a failure before this line is certain not sent (C1)
        return _stream(fd, session, token, size, spec, channel, paused)
    finally:
        os.close(fd)


def _stream(fd: int, session: str, token: str, size: int, spec: dict, channel: str,
            paused: Callable[[], bool] | None) -> publishers.Receipt:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        if offset and paused is not None and paused():
            raise publishers.PublishError("youtube_upload_paused_incomplete_uncertain")
        chunk = os.pread(fd, min(CHUNK_SIZE, size - offset), offset)
        if not chunk or (len(chunk) < CHUNK_SIZE and offset + len(chunk) < size):
            raise publishers.PublishError("youtube_upload_file_shrank_incomplete_uncertain")
        digest.update(chunk)
        last = offset + len(chunk) - 1
        final = last == size - 1
        if final and (os.pread(fd, 1, size) or digest.hexdigest() != spec["sha256"]):
            # Checked before the final PUT: the upload is never completed with other bytes.
            raise publishers.PublishError("youtube_upload_file_changed_incomplete_uncertain")
        req = publishers._request(session, data=chunk, method="PUT", headers=_headers(
            token, **{"Content-Type": MIME_TYPE, "Content-Range": f"bytes {offset}-{last}/{size}"}))
        status, headers, raw = _exchange(req, "upload")
        if status == 308:
            if final:
                raise publishers.PublishError("youtube_upload_incomplete_after_final_chunk_uncertain")
            match = _RANGE.fullmatch(headers.get("range", ""))
            if match is None or int(match.group(1)) != last:
                # Partial acceptance or a lost range: never resent automatically (no auto-resume, D4).
                raise publishers.PublishError("youtube_upload_range_mismatch_uncertain")
            offset = last + 1
            continue
        if status not in (200, 201) or not final:
            raise publishers.PublishError("youtube_upload_unexpected_status_uncertain")
        return _receipt(_json(raw, "receipt"), channel, spec)
    raise publishers.PublishError("youtube_upload_empty_file_uncertain")
