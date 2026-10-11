"""The shared video record (issues #22 and #23; owner-approved 2026-10-09, disabled until a live receipt).

A post carries at most one video, stored as one JSON object in ``posts.video_json`` and bound
by ``storage.approval_payload`` under ``reachweaver.storage.approval/video-1``. A post carries
images (``media_json``) or a video, never both. The record has one core shared by every video
platform plus a fully bound ``platform_options`` object:

* core: ``kind``, ``version``, ``platform``, ``path``, ``sha256``, ``bytes``, ``mime`` and the
  values parsed from the file headers (``duration_seconds``, ``width``, ``height``,
  ``video_codec``, ``frame_rate_fps``; ``"unknown"`` when a header does not say);
* ``platform_options``: TikTok ``{mode, privacy_level, commercial, disable_comment,
  disable_duet, disable_stitch, is_aigc}`` (``tiktok.py``), YouTube ``{title, tags,
  category_id, privacy_status, self_declared_made_for_kids}`` (``youtube.py``).

The approval hash covers every field except ``path`` (a local location that is never sent; the
bytes are, through ``sha256`` and ``bytes``). ``platform`` is bound and must equal the post's
platform, so a YouTube record can never be attached to a TikTok post or the reverse. Each
platform module validates its own options, limits and file checks; this module only holds the
codec and the dispatch. Nothing here touches the network.
"""
from __future__ import annotations

import json
import os
import re
from types import ModuleType
from typing import Any

from .policy import ValidationError

KIND = "reachweaver_video"
VERSION = 1
CORE_KEYS = ("kind", "version", "platform", "path", "sha256", "bytes", "mime", "duration_seconds", "width",
             "height", "video_codec", "frame_rate_fps", "platform_options")
UNBOUND_KEYS = ("path",)  # never sent, so never bound (#13/#22 decision); the bytes are bound via sha256
PARSED_KEYS = ("duration_seconds", "width", "height", "video_codec", "frame_rate_fps")
UNKNOWN = "unknown"
MALFORMED = "video_record_malformed: run video-detach, then video-attach again"
BOTH_KINDS = ("media_and_video_refused: a post carries images or one video, never both; detach one "
              "(media-detach or video-detach) on the draft first")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIME = re.compile(r"^video/[a-z0-9.+-]{1,40}$")


def _platform_module(platform: object) -> ModuleType:
    """The module that owns a platform's ``platform_options`` and file checks."""
    if platform == "tiktok":
        from . import tiktok
        return tiktok
    if platform == "youtube":
        from . import youtube
        return youtube
    raise ValidationError(f"video records are only supported for TikTok and YouTube drafts, not {platform}")


VIDEO_PLATFORMS = ("tiktok", "youtube")


def make_record(platform: str, path: str, sha256: str, size: int, mime: str, info: dict,
                options: dict) -> dict[str, Any]:
    """Assemble a record from a platform builder's checked values (see ``CORE_KEYS``)."""
    record = {"kind": KIND, "version": VERSION, "platform": platform, "path": path, "sha256": sha256,
              "bytes": size, "mime": mime,
              **{key: UNKNOWN if info.get(key) is None else info[key] for key in PARSED_KEYS},
              "platform_options": options}
    return check_record(record)


def _parsed_ok(key: str, value: object) -> bool:
    if value == UNKNOWN:
        return True
    if key == "video_codec":
        return isinstance(value, str) and 0 < len(value) <= 40
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def check_core(record: object) -> dict:
    """Strict shape check of the shared core; raises ``MALFORMED``. Platform rules are separate."""
    if not isinstance(record, dict) or set(record) != set(CORE_KEYS) or record.get("kind") != KIND \
            or record.get("version") != VERSION or record.get("platform") not in VIDEO_PLATFORMS:
        raise ValidationError(MALFORMED)
    ok = (isinstance(record["path"], str) and os.path.isabs(record["path"])
          and isinstance(record["sha256"], str) and _SHA256.fullmatch(record["sha256"])
          and type(record["bytes"]) is int and record["bytes"] > 0
          and isinstance(record["mime"], str) and _MIME.fullmatch(record["mime"])
          and all(_parsed_ok(key, record[key]) for key in PARSED_KEYS)
          and isinstance(record["platform_options"], dict))
    if not ok:
        raise ValidationError(MALFORMED)
    return record


def check_record(record: object) -> dict:
    """Core shape plus the owning platform's rules (options, MIME, size). Returns the record."""
    check_core(record)
    assert isinstance(record, dict)
    _platform_module(record["platform"]).check_record(record)
    return record


def encode_video(record: dict) -> str:
    """Canonical JSON for ``posts.video_json`` (same canonical form as ``storage._canonical_json``)."""
    check_record(record)
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def decode_video(text: object) -> dict | None:
    """Strictly parse a stored ``video_json``; NULL/empty -> None. Malformed raises ``MALFORMED``."""
    if text in (None, ""):
        return None
    try:
        record = json.loads(str(text))
    except ValueError:
        raise ValidationError(MALFORMED) from None
    return check_record(record)


def bound_view(record: dict) -> dict:
    """Every field the approval hash covers: the whole record except ``path``."""
    return {key: value for key, value in record.items() if key not in UNBOUND_KEYS}


def file_issue(record: dict) -> str | None:
    """None when the file still holds the recorded, usable bytes; else why not (no content echoed). Offline."""
    return _platform_module(record["platform"]).file_issue(record)


def file_problem(record: dict) -> str | None:
    """None when the file still holds the recorded bytes; else ``approval_video_changed_...``. Offline."""
    from .storage import BINDING_VIDEO_CHANGED
    return BINDING_VIDEO_CHANGED if file_issue(record) else None


def published_strings(record: dict) -> list[str]:
    """Human-readable strings the send publishes besides the post text (opt-out checks use them)."""
    return list(_platform_module(record["platform"]).published_strings(record))


def mode(record: dict) -> str:
    """The delivery mode the record asks for (the registry switch that must be live)."""
    return str(_platform_module(record["platform"]).record_mode(record))
