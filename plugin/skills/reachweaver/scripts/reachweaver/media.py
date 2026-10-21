"""Offline media-manifest validation: headers, platform constraints, accessibility, rights, locales.

Nothing here uploads, renders, transcodes, generates or fetches media. Files are read
locally with the standard library only. Dimensions and durations come from real file
headers; when a header cannot be parsed the value is reported as ``unknown`` and the
dependent checks are skipped with a warning. Values are never guessed.

Platform constraints are a dated, sourced table. A constraint whose value the cited
official documentation does not establish is recorded as ``None`` (unknown) and its
check is skipped with a warning instead of inventing a limit.
"""
from __future__ import annotations

import html
import io
import json
import re
import struct
import unicodedata
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .campaigns import _sensitive_keys, public_url
from .policy import PLACEHOLDER, ValidationError
from .inputs import read_json_file

UNKNOWN = "unknown"
VERIFIED_DATE = "2026-10-08"
MAX_ASSETS = 200
MAX_MOOV_BYTES = 64 * 1024 * 1024  # Largest moov box read into memory; larger ones are not parsed.
MAX_TOP_LEVEL_BOXES = 10000
MAX_BOX_DEPTH = 16  # Container nesting guard inside moov (real files need about 6 levels).
MAX_BOXES = 200_000  # Total boxes walked inside one moov.
MAX_STTS_ENTRIES = 1_000_000
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
DEFAULT_SUBTITLE_TOLERANCE_MS = 500  # Same slack media-check already allows a last cue past the video end.
MAX_SUBTITLE_TOLERANCE_MS = 5000
MAX_TRACK_FINDINGS = 20  # Per severity and translated track; the rest are counted, never dropped silently.
SUBTITLE_ALIGNMENTS = frozenset({"source", "independent"})

KINDS = frozenset({"image", "video", "subtitle"})
IMAGE_CONTAINERS = frozenset({"png", "jpeg", "gif", "webp"})
VIDEO_CONTAINERS = frozenset({"mp4", "mov"})
SUBTITLE_FORMATS = frozenset({"srt", "vtt"})
EXTENSIONS = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp",
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".srt": "srt", ".vtt": "vtt",
}
CREATORS = frozenset({"owner", "tool", "third_party"})
LICENSE_KEYWORDS = frozenset({"owner", "licensed"})
# A small, well-known subset; other well-formed ids pass with a "verify" warning.
COMMON_SPDX = frozenset({
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-ND-4.0",
    "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0", "CC-BY-3.0", "CC-BY-SA-3.0", "MIT",
    "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-3.0-only", "GPL-3.0-or-later",
    "LGPL-3.0-only", "MPL-2.0", "Unlicense", "OFL-1.1", "AGPL-3.0-only",
})
SPDX_ID = re.compile(r"^(?:LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9][A-Za-z0-9.+-]{0,63})$")
# BCP-47-ish: language[-Script][-REGION][-variant]*; not a full RFC 5646 parser.
BCP47 = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?(?:-[A-Za-z0-9]{5,8})*$")
ALT_PLACEHOLDERS = frozenset({
    "image", "image of", "picture", "picture of", "photo", "photo of", "screenshot",
    "graphic", "alt", "alt text", "alttext", "todo", "tbd", "n/a", "none", "-",
})
REDUNDANT_ALT_PREFIX = re.compile(r"^(?:an?\s+)?(?:image|picture|photo|graphic)\s+of\b", re.I)

# ISO BMFF sample-entry fourcc -> codec id. Other fourccs are reported as-is with codec "unknown".
VIDEO_FOURCC = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc",
                b"av01": "av1", b"vp09": "vp9", b"vp08": "vp8"}
# mp4a is resolved through its esds objectTypeIndication, never assumed to be AAC.
AUDIO_FOURCC = {b"Opus": "opus", b"ac-3": "ac3", b"ec-3": "eac3"}
AAC_OBJECT_TYPES = frozenset({0x40, 0x66, 0x67, 0x68})  # MPEG-4 Audio and MPEG-2 AAC profiles.
MP3_OBJECT_TYPES = frozenset({0x69, 0x6B})
CONTAINER_BOXES = frozenset({b"moov", b"trak", b"mdia", b"minf", b"stbl"})
AV_REPORT_FIELDS = ("video_codec", "video_fourcc", "frame_rate_fps", "variable_frame_rate",
                    "frame_rate_range_fps", "audio_tracks", "audio_codec", "audio_fourcc",
                    "audio_sample_rate_hz", "audio_channels", "fast_start")

# ---------------------------------------------------------------------------
# Sourced constraint table. Each row: value (None = not established), source URL,
# verified_date, severity of a violation, and an explanatory note.
# ---------------------------------------------------------------------------
_BSKY_IMAGES = "https://github.com/bluesky-social/atproto/blob/main/packages/api/lexicons/app/bsky/embed/images.json"
_BSKY_VIDEO = "https://github.com/bluesky-social/atproto/blob/main/packages/api/lexicons/app/bsky/embed/video.json"
_BSKY_APP = "https://github.com/bluesky-social/social-app/blob/main/src/lib/constants.ts"
_MASTODON = "https://docs.joinmastodon.org/user/posting/"
_MASTODON_API = "https://docs.joinmastodon.org/methods/media/"
_INSTAGRAM = "https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media"
_SHORTS = "https://support.google.com/youtube/answer/15424877"
_TIKTOK = "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide"
_LI_IMAGES = "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api"
_LI_VIDEOS = "https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api"
_YT_ENCODING = "https://support.google.com/youtube/answer/1722171"
_MASTODON_INSTANCE = "https://docs.joinmastodon.org/entities/Instance/"
_GITHUB_SOCIAL = ("https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/"
                  "customizing-your-repository/customizing-your-repositorys-social-media-preview")
_DEV_EDITOR = "https://dev.to/p/editor_guide"
# Rows added after the 2026-10-08 baseline carry their own date; never restamp older rows.
_GITHUB_VERIFIED = "2026-10-14"
_DEV_VERIFIED = "2026-10-15"


def _row(value: Any, source: str, note: str = "", severity: str = "error",
         verified_date: str = VERIFIED_DATE) -> dict[str, Any]:
    return {"value": value, "source": source, "verified_date": verified_date,
            "severity": severity, "note": note}


def _unknown(source: str, note: str = "Not established by the cited official source; check skipped.",
             verified_date: str = VERIFIED_DATE) -> dict[str, Any]:
    return _row(None, source, note, severity="warning", verified_date=verified_date)


CONSTRAINTS: dict[str, dict[str, dict[str, dict]]] = {
    "bluesky": {
        "image": {
            "allowed_containers": _row(sorted(IMAGE_CONTAINERS), _BSKY_IMAGES, "Lexicon accepts image/*."),
            "max_bytes": _row(2_000_000, _BSKY_IMAGES, "Lexicon blob maxSize."),
            "aspect_ratio": _unknown(_BSKY_IMAGES),
            "min_resolution": _unknown(_BSKY_IMAGES),
            "alt_text_max_chars": _row(2000, _BSKY_APP, "Official app constant MAX_ALT_TEXT; the lexicon itself "
                                       "declares no limit. Counted conservatively in code points."),
        },
        "video": {
            "allowed_containers": _row(["mp4"], _BSKY_VIDEO, "Lexicon accepts video/mp4."),
            "max_bytes": _row(300_000_000, _BSKY_VIDEO, "Lexicon blob maxSize."),
            "max_duration_s": _row(600, _BSKY_APP, "Official app constant VIDEO_MAX_DURATION_MS."),
            "aspect_ratio": _unknown(_BSKY_VIDEO),
            "min_resolution": _unknown(_BSKY_VIDEO),
            "subtitle_formats": _row(["vtt"], _BSKY_VIDEO, "Caption blobs accept text/vtt."),
            "subtitle_max_bytes": _row(20_000, _BSKY_VIDEO, "Caption blob maxSize."),
            "video_codecs": _unknown(_BSKY_VIDEO, "Lexicon and app constants name container MIME types only; "
                                     "no video codec is stated. Check skipped."),
            "audio_codecs": _unknown(_BSKY_VIDEO, "No audio codec stated. Check skipped."),
            "frame_rate_fps": _unknown(_BSKY_VIDEO, "No frame-rate range stated. Check skipped."),
        },
    },
    "mastodon": {
        "image": {
            "allowed_containers": _row(sorted(IMAGE_CONTAINERS), _MASTODON,
                                       "PNG/JPG/WEBP/GIF documented (HEIF/AVIF too, not detected here). "
                                       "Instance-configurable.", "warning"),
            "max_bytes": _row(16_000_000, _MASTODON, "Documented default; confirm per instance via "
                              "GET /api/v2/instance configuration.media_attachments.", "warning"),
            "aspect_ratio": _unknown(_MASTODON),
            "min_resolution": _unknown(_MASTODON),
            "alt_text_max_chars": _unknown(_MASTODON_API, "Description limit is instance-reported; not stated "
                                           "in the cited docs. Check skipped."),
        },
        "video": {
            "allowed_containers": _row(["mov", "mp4"], _MASTODON, "MP4/M4V/MOV/WebM documented; "
                                       "instance-configurable.", "warning"),
            "max_bytes": _row(99_000_000, _MASTODON, "Documented default; confirm per instance.", "warning"),
            "max_duration_s": _unknown(_MASTODON),
            "aspect_ratio": _unknown(_MASTODON),
            "min_resolution": _unknown(_MASTODON),
            "subtitle_formats": _unknown(_MASTODON_API, "No caption-file upload documented; burn in or describe."),
            "video_codecs": _unknown(_MASTODON_INSTANCE, "supported_mime_types lists containers, not codecs. "
                                     "Check skipped."),
            "frame_rate_fps": _unknown(_MASTODON_INSTANCE, "Instance-reported configuration.media_attachments."
                                       "video_frame_rate_limit (docs example: 120); compare with "
                                       "connect-check --online output. Check skipped."),
        },
    },
    "instagram": {
        "image": {
            "allowed_containers": _row(["jpeg"], _INSTAGRAM, "Graph API image posts: JPEG only."),
            "max_bytes": _row(8 * 1024 * 1024, _INSTAGRAM, "8 MB maximum (binary MB assumed; verify)."),
            "aspect_ratio": _row([0.8, 1.91], _INSTAGRAM, "Must be within 4:5 to 1.91:1."),
            "min_width": _row(320, _INSTAGRAM, "Scaled up if smaller; quality loss likely.", "warning"),
            "max_width": _row(1440, _INSTAGRAM, "Scaled down if larger.", "warning"),
            "alt_text_max_chars": _row(1000, _INSTAGRAM, "alt_text up to 1000 characters (image posts only)."),
        },
        "video": {
            "allowed_containers": _row(["mov", "mp4"], _INSTAGRAM, "Reels: MOV or MP4."),
            "moov_before_mdat": _row(True, _INSTAGRAM, "Reels: moov atom at the front of the file."),
            "max_bytes": _row(300_000_000, _INSTAGRAM, "Reels: 300 MB maximum."),
            "min_duration_s": _row(3, _INSTAGRAM, "Reels: 3 seconds minimum."),
            "max_duration_s": _row(900, _INSTAGRAM, "Reels: 15 minutes maximum."),
            "aspect_ratio": _row([0.01, 10.0], _INSTAGRAM, "Required 0.01:1 to 10:1; 9:16 recommended."),
            "max_width": _row(1920, _INSTAGRAM, "Reels: maximum 1920 horizontal pixels."),
            "alt_text_supported": _row(False, _INSTAGRAM, "alt_text is not supported for Reels.", "warning"),
            "subtitle_formats": _unknown(_INSTAGRAM, "No caption-file field documented for Reels; burn in captions."),
            "video_codecs": _row(["h264", "hevc"], _INSTAGRAM, "Reels: HEVC or H264 (progressive scan, closed "
                                 "GOP and 4:2:0 are documented too but not detected here)."),
            "frame_rate_fps": _row([23, 60], _INSTAGRAM, "Reels: 23-60 FPS. Variable frame rate is not "
                                   "addressed; the range is checked against the average."),
            "audio_codecs": _row(["aac"], _INSTAGRAM, "Reels: AAC audio."),
            "max_audio_sample_rate_hz": _row(48_000, _INSTAGRAM, "Reels: 48 kHz sample rate maximum."),
            "max_audio_channels": _row(2, _INSTAGRAM, "Reels: 1 or 2 channels (mono or stereo)."),
        },
    },
    "youtube_shorts": {
        "video": {
            "max_duration_s": _row(180, _SHORTS, "Shorts up to three minutes."),
            "aspect_ratio": _row([0.0, 1.0], _SHORTS, "Square or vertical only; wider videos are not Shorts."),
            "allowed_containers": _unknown(_SHORTS),
            "max_bytes": _unknown(_SHORTS),
            "min_resolution": _unknown(_SHORTS),
            "subtitle_formats": _unknown(_SHORTS),
            # YouTube publishes recommended (not required) upload encoding settings, so these are warnings.
            "video_codecs": _row(["h264"], _YT_ENCODING, "Recommended upload encoding: H.264. Other codecs are "
                                 "not stated as rejected.", "warning"),
            "audio_codecs": _row(["aac", "opus"], _YT_ENCODING, "Recommended: AAC-LC, Opus or Eclipsa Audio "
                                 "(AAC profile and Eclipsa Audio are not detected here).", "warning"),
            "audio_sample_rate_hz": _row(48_000, _YT_ENCODING, "Recommended sample rate: 48 kHz.", "warning"),
            "max_audio_channels": _unknown(_YT_ENCODING, "Recommended Stereo or Stereo + 5.1 is a track layout, "
                                           "not a channel cap. Check skipped."),
            "moov_before_mdat": _row(True, _YT_ENCODING, "Recommended: moov atom at the front of the file "
                                     "(Fast Start).", "warning"),
            "frame_rate_fps": _unknown(_YT_ENCODING, "Upload at the recorded frame rate; 24, 25, 30, 48, 50 and "
                                       "60 fps are common and other rates are acceptable, so no range is "
                                       "enforced. Check skipped."),
        },
    },
    "tiktok": {
        "image": {
            "allowed_containers": _row(["jpeg", "webp"], _TIKTOK, "Photo posts: WebP and JPEG."),
            "max_bytes": _row(20 * 1000 * 1000, _TIKTOK, "20 MB per image (decimal MB, conservative)."),
            "aspect_ratio": _unknown(_TIKTOK),
            "min_resolution": _unknown(_TIKTOK, "Docs say 'up to 1080p' without defining orientation; check skipped."),
            "alt_text_max_chars": _unknown(_TIKTOK),
        },
        "video": {
            "allowed_containers": _row(["mov", "mp4"], _TIKTOK, "MP4 recommended; WebM/MOV supported "
                                       "(WebM not detected here)."),
            "max_bytes": _row(4_000_000_000, _TIKTOK, "4 GB maximum (decimal GB, conservative)."),
            "max_duration_s": _row(600, _TIKTOK, "Upload endpoint up to 10 minutes; users may trim to their "
                                   "account's actual maximum."),
            "min_dimension": _row(360, _TIKTOK, "Each dimension 360..4096 px."),
            "max_dimension": _row(4096, _TIKTOK, "Each dimension 360..4096 px."),
            "aspect_ratio": _unknown(_TIKTOK),
            "subtitle_formats": _unknown(_TIKTOK),
            "video_codecs": _row(["h264", "hevc", "vp8", "vp9"], _TIKTOK, "H.264 recommended; H.265, VP8 and VP9 "
                                 "supported."),
            "frame_rate_fps": _row([23, 60], _TIKTOK, "Minimum 23 FPS, maximum 60 FPS (checked against the "
                                   "average)."),
            "audio_codecs": _unknown(_TIKTOK, "No audio codec stated in the media transfer guide. Check skipped."),
        },
    },
    "github": {
        "image": {
            "allowed_containers": _row(["gif", "jpeg", "png"], _GITHUB_SOCIAL, "Social preview: PNG, JPG or GIF.",
                                       verified_date=_GITHUB_VERIFIED),
            "max_bytes": _row(1_000_000, _GITHUB_SOCIAL, "Docs say 'under 1 MB'; decimal MB used as the "
                              "conservative reading.", verified_date=_GITHUB_VERIFIED),
            "min_width": _row(640, _GITHUB_SOCIAL, "Docs: at least 640 by 320 pixels.",
                              verified_date=_GITHUB_VERIFIED),
            "min_height": _row(320, _GITHUB_SOCIAL, "Docs: at least 640 by 320 pixels.",
                               verified_date=_GITHUB_VERIFIED),
            "aspect_ratio": _row([1.99, 2.01], _GITHUB_SOCIAL, "Docs recommend 1280 by 640 pixels for best "
                                 "display (2:1); the ratio is derived from that size, not stated separately.",
                                 "warning", verified_date=_GITHUB_VERIFIED),
        },
    },
    "devto": {
        "image": {
            "allowed_containers": _unknown(_DEV_EDITOR, "Image file types are not stated. Check skipped.",
                                           verified_date=_DEV_VERIFIED),
            "max_bytes": _unknown(_DEV_EDITOR, "Maximum image file size is not stated. Check skipped.",
                                  verified_date=_DEV_VERIFIED),
            "aspect_ratio": _row([2.37, 2.4], _DEV_EDITOR, "The cover guide says 'The best size is 1000 x 420' "
                                 "(about 2.38:1). It is a recommendation, not a limit.", "warning",
                                 verified_date=_DEV_VERIFIED),
        },
    },
    "linkedin": {
        "image": {
            "allowed_containers": _row(["gif", "jpeg", "png"], _LI_IMAGES, "Images API: JPG, GIF, PNG."),
            "max_pixels": _row(36_152_319, _LI_IMAGES, "Fewer than 36,152,320 pixels."),
            "max_bytes": _unknown(_LI_IMAGES),
            "aspect_ratio": _unknown(_LI_IMAGES),
            "alt_text_max_chars": _row(4086, _LI_IMAGES, "altText maximum 4,086; under 120 recommended."),
        },
        "video": {
            "allowed_containers": _row(["mp4"], _LI_VIDEOS, "Videos API: MP4."),
            "min_bytes": _row(75 * 1024, _LI_VIDEOS, "Between 75 KB and 500 MB (conservative KiB floor)."),
            "max_bytes": _row(500_000_000, _LI_VIDEOS, "Between 75 KB and 500 MB (conservative decimal cap)."),
            "min_duration_s": _row(3, _LI_VIDEOS, "Three seconds to 30 minutes."),
            "max_duration_s": _row(1800, _LI_VIDEOS, "Three seconds to 30 minutes."),
            "aspect_ratio": _unknown(_LI_VIDEOS),
            "subtitle_formats": _row(["srt"], _LI_VIDEOS, "Caption upload example uses SRT; one caption "
                                     "file per video and English-only captions documented."),
            "video_codecs": _unknown(_LI_VIDEOS, "Videos API states MP4 only. LinkedIn's separate Video Ads "
                                     "help page covers ads, not organic posts, so it is not enforced. "
                                     "Check skipped."),
            "audio_codecs": _unknown(_LI_VIDEOS, "No audio codec stated by the Videos API. Check skipped."),
            "frame_rate_fps": _unknown(_LI_VIDEOS, "No frame-rate range stated by the Videos API. Check skipped."),
        },
    },
}


def constraint_table() -> dict:
    """Return a JSON-serializable copy of the sourced constraint table."""
    return json.loads(json.dumps(CONSTRAINTS))


# ---------------------------------------------------------------------------
# Header parsing (stdlib only, best effort; never guesses).
# ---------------------------------------------------------------------------

def _info(container: str = UNKNOWN) -> dict[str, Any]:
    return {"container": container, "width": UNKNOWN, "height": UNKNOWN,
            "duration_seconds": UNKNOWN, "rotation_degrees": UNKNOWN,
            "top_level_boxes": [], "has_exif": False, "parse_notes": []}


def _parse_png(head: bytes, info: dict) -> None:
    if len(head) >= 24 and head[12:16] == b"IHDR":
        width, height = struct.unpack(">II", head[16:24])
        if width and height:
            info["width"], info["height"] = width, height
    if b"eXIf" in head:
        info["has_exif"] = True


def _parse_gif(head: bytes, info: dict) -> None:
    if len(head) >= 10:
        width, height = struct.unpack("<HH", head[6:10])
        if width and height:
            info["width"], info["height"] = width, height


def _parse_webp(head: bytes, info: dict) -> None:
    pos = 12
    while pos + 8 <= len(head):
        ctype, size = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        data = head[pos + 8:pos + 8 + size]
        if ctype == b"VP8X" and len(data) >= 10:
            if data[0] & 0x08:
                info["has_exif"] = True
            info["width"] = 1 + int.from_bytes(data[4:7], "little")
            info["height"] = 1 + int.from_bytes(data[7:10], "little")
        elif ctype == b"VP8 " and len(data) >= 10 and data[3:6] == b"\x9d\x01\x2a" and info["width"] == UNKNOWN:
            info["width"] = struct.unpack("<H", data[6:8])[0] & 0x3FFF
            info["height"] = struct.unpack("<H", data[8:10])[0] & 0x3FFF
        elif ctype == b"VP8L" and len(data) >= 5 and data[0] == 0x2F and info["width"] == UNKNOWN:
            bits = int.from_bytes(data[1:5], "little")
            info["width"] = (bits & 0x3FFF) + 1
            info["height"] = ((bits >> 14) & 0x3FFF) + 1
        elif ctype == b"EXIF":
            info["has_exif"] = True
        pos += 8 + size + (size & 1)
    if info["width"] == 0 or info["height"] == 0:
        info["width"] = info["height"] = UNKNOWN


_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _parse_jpeg(handle, info: dict) -> None:
    handle.seek(2)
    for _ in range(10000):
        byte = handle.read(1)
        if not byte:
            return
        if byte != b"\xff":
            return  # Not at a marker: malformed; leave unknown.
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if not marker:
            return
        code = marker[0]
        if code == 0xD8 or code == 0x01 or 0xD0 <= code <= 0xD7:
            continue
        if code in (0xD9, 0xDA):
            return  # EOI or start of scan before any SOF: dimensions unknown.
        raw = handle.read(2)
        if len(raw) < 2:
            return
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            return
        payload = handle.read(length - 2)
        if len(payload) < length - 2:
            return
        if code == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            info["has_exif"] = True
        if code in _SOF and len(payload) >= 5:
            height, width = struct.unpack(">HH", payload[1:5])
            if width and height:
                info["width"], info["height"] = width, height
            return


GPS_EXIF_WARNING = ("embedded EXIF metadata contains GPS location data; ReachWeaver uploads the exact approved "
                    "bytes and does not strip it, so the location will be sent. Strip the metadata yourself "
                    "(and re-attach) if you do not want that")


def _tiff_has_gps(tiff: bytes) -> bool:
    """True when the TIFF/EXIF block's first IFD points at a GPS IFD holding at least one entry."""
    if tiff.startswith(b"Exif\x00\x00"):
        tiff = tiff[6:]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return False
    order = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(order + "H", tiff[2:4])[0] != 42:
        return False
    ifd0 = struct.unpack(order + "I", tiff[4:8])[0]
    if ifd0 + 2 > len(tiff):
        return False
    count = struct.unpack(order + "H", tiff[ifd0:ifd0 + 2])[0]
    for i in range(min(count, 1024)):
        at = ifd0 + 2 + 12 * i
        if at + 12 > len(tiff):
            return False
        tag, _kind, _n, value = struct.unpack(order + "HHII", tiff[at:at + 12])
        if tag == 0x8825:  # GPSInfo IFD pointer
            if value == 0 or value + 2 > len(tiff):
                return False
            return struct.unpack(order + "H", tiff[value:value + 2])[0] > 0
    return False


def exif_has_gps(data: bytes) -> bool:
    """Best-effort, stdlib-only: does this PNG/JPEG/WebP carry an EXIF GPS block? Malformed means False."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            pos = 8
            while pos + 8 <= len(data):
                size, ctype = struct.unpack(">I4s", data[pos:pos + 8])
                if ctype == b"eXIf" and _tiff_has_gps(data[pos + 8:pos + 8 + size]):
                    return True
                pos += 12 + size
        elif data.startswith(b"\xff\xd8\xff"):
            pos = 2
            while pos + 4 <= len(data) and data[pos] == 0xFF:
                code = data[pos + 1]
                if code in (0xD9, 0xDA):
                    break
                length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
                if length < 2:
                    break
                payload = data[pos + 4:pos + 2 + length]
                if code == 0xE1 and payload.startswith(b"Exif\x00\x00") and _tiff_has_gps(payload):
                    return True
                pos += 2 + length
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            pos = 12
            while pos + 8 <= len(data):
                ctype, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
                if ctype == b"EXIF" and _tiff_has_gps(data[pos + 8:pos + 8 + size]):
                    return True
                pos += 8 + size + (size & 1)
    except (struct.error, IndexError):
        return False
    return False


def _iter_boxes(data: bytes, start: int, end: int, notes: list[str] | None = None) -> Iterator[tuple[bytes, int, int]]:
    """Yield (type, payload_start, box_end) for each well-formed box in data[start:end].

    Iteration stops at the first box whose declared size is smaller than its header or runs past
    its parent; when ``notes`` is given, that malformed size is recorded there.
    """
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack(">I4s", data[pos:pos + 8])
        header = 8
        if size == 1:
            if pos + 16 > end:
                if notes is not None:
                    notes.append(f"truncated 64-bit box header at offset {pos}")
                return
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            if notes is not None:
                notes.append(f"malformed size {size} for box {_fourcc(btype)!r} at offset {pos}")
            return
        yield btype, pos + header, pos + size
        pos += size


def _fourcc(raw: bytes) -> str:
    text = raw.decode("latin-1")
    return text if text.isprintable() else raw.hex()


def _fixed(value: int) -> float:
    return (value - (1 << 32) if value & 0x80000000 else value) / 65536.0


class _Walk:
    """Shared budget for one moov walk: box count, depth guard and parse notes."""

    def __init__(self) -> None:
        self.boxes = 0
        self.notes: list[str] = []

    def note(self, message: str) -> None:
        if message not in self.notes and len(self.notes) < 20:
            self.notes.append(message)


def _index(data: bytes, start: int, end: int, depth: int, walk: _Walk,
           found: dict[tuple[bytes, ...], tuple[int, int]], prefix: tuple[bytes, ...]) -> None:
    """Record the first occurrence of each box path, descending only into known containers."""
    if depth > MAX_BOX_DEPTH:
        walk.note(f"box nesting deeper than {MAX_BOX_DEPTH} levels ignored")
        return
    for btype, bstart, bend in _iter_boxes(data, start, end, walk.notes):
        walk.boxes += 1
        if walk.boxes > MAX_BOXES:
            walk.note(f"more than {MAX_BOXES} boxes in moov; remaining boxes ignored")
            return
        path = prefix + (btype,)
        found.setdefault(path, (bstart, bend))
        if btype in CONTAINER_BOXES:
            _index(data, bstart, bend, depth + 1, walk, found, path)


def _mdhd_timescale(data: bytes, start: int, end: int) -> int | None:
    if end - start < 4:
        return None
    at = start + (4 + 16 if data[start] == 1 else 4 + 8)  # version/flags, creation, modification
    if at + 4 > end:
        return None
    return struct.unpack(">I", data[at:at + 4])[0] or None


def _first_sample_entry(data: bytes, start: int, end: int, walk: _Walk) -> tuple[bytes, int, int] | None:
    if end - start < 8 or struct.unpack(">I", data[start + 4:start + 8])[0] == 0:
        return None
    for entry in _iter_boxes(data, start + 8, end, walk.notes):
        return entry
    return None


def _frame_rate(data: bytes, start: int, end: int, timescale: int | None, walk: _Walk) -> dict[str, Any]:
    """Average fps from stts sample deltas; variable frame rate only when the deltas show it."""
    out: dict[str, Any] = {"fps": UNKNOWN, "vfr": UNKNOWN, "range": UNKNOWN}
    if timescale is None or end - start < 8:
        return out
    count = struct.unpack(">I", data[start + 4:start + 8])[0]
    if count > MAX_STTS_ENTRIES:
        walk.note(f"stts declares {count} entries (cap {MAX_STTS_ENTRIES}); frame rate not computed")
        return out
    if count == 0 or start + 8 + 8 * count > end:
        if count:
            walk.note(f"stts declares {count} entries but the box is too short; frame rate not computed")
        return out
    entries = list(struct.iter_unpack(">II", data[start + 8:start + 8 + 8 * count]))
    samples = sum(c for c, _ in entries)
    ticks = sum(c * d for c, d in entries)
    if not samples or not ticks:
        return out
    out["fps"] = round(samples * timescale / ticks, 3)
    # Encoders commonly give the final sample its own (shorter) delta; that alone is not VFR.
    body = entries[:-1] if len(entries) > 1 and entries[-1][0] == 1 else entries
    deltas = {d for c, d in body if c}
    if deltas and 0 not in deltas:
        out["vfr"] = len(deltas) > 1
        out["range"] = [round(timescale / max(deltas), 3), round(timescale / min(deltas), 3)]
    return out


def _read_descriptor(data: bytes, pos: int, end: int) -> tuple[int, int] | None:
    """MPEG-4 descriptor header: return (tag, payload_pos) or None when out of bounds."""
    if pos >= end:
        return None
    tag, pos = data[pos], pos + 1
    for _ in range(4):
        if pos >= end:
            return None
        byte, pos = data[pos], pos + 1
        if not byte & 0x80:
            return tag, pos
    return tag, pos


def _esds_object_type(data: bytes, start: int, end: int) -> int | None:
    """objectTypeIndication from esds (ES_Descriptor -> DecoderConfigDescriptor), else None."""
    head = _read_descriptor(data, start + 4, end)  # Skip the FullBox version/flags.
    if head is None or head[0] != 0x03 or head[1] + 3 > end:
        return None
    pos = head[1]
    flags = data[pos + 2]
    pos += 3
    if flags & 0x80:
        pos += 2
    if flags & 0x40:
        if pos >= end:
            return None
        pos += 1 + data[pos]
    if flags & 0x20:
        pos += 2
    config = _read_descriptor(data, pos, end)
    if config is None or config[0] != 0x04 or config[1] >= end:
        return None
    return data[config[1]]


def _audio_entry(data: bytes, fourcc: bytes, start: int, end: int, walk: _Walk) -> dict[str, Any]:
    """Codec, sample rate and channel count from an audio sample entry (ISO and QuickTime v0/v1/v2)."""
    out: dict[str, Any] = {"codec": AUDIO_FOURCC.get(fourcc, UNKNOWN), "rate": UNKNOWN, "channels": UNKNOWN}
    if end - start < 28:
        return out
    version = struct.unpack(">H", data[start + 8:start + 10])[0]
    channels = struct.unpack(">H", data[start + 16:start + 18])[0]
    rate: float = struct.unpack(">I", data[start + 24:start + 28])[0] >> 16
    children = [start + 28]
    if version == 1:
        children.append(start + 44)  # QuickTime v1 adds four 32-bit fields; ISO V1 does not.
        if rate == 1:
            rate = 0  # ISO AudioSampleEntryV1 sentinel; the real rate lives in 'srat' (not parsed).
    elif version == 2:
        if end - start < 64:
            return out
        rate = struct.unpack(">d", data[start + 32:start + 40])[0]
        channels = struct.unpack(">I", data[start + 40:start + 44])[0]
        children = [start + 64]
    elif version != 0:
        walk.note(f"audio sample entry version {version} not understood")
        return out
    if 0 < rate < 10_000_000:
        out["rate"] = int(rate) if float(rate).is_integer() else round(rate, 3)
    if 0 < channels < 256:
        out["channels"] = channels
    if fourcc == b"mp4a":
        for child_at in children:
            boxes = {b: (s, e) for b, s, e in _iter_boxes(data, child_at, end)}
            if b"esds" in boxes:
                oti = _esds_object_type(data, *boxes[b"esds"])
                if oti in AAC_OBJECT_TYPES:
                    out["codec"] = "aac"
                elif oti in MP3_OBJECT_TYPES:
                    out["codec"] = "mp3"
                break
    return out


def _parse_trak(moov: bytes, start: int, end: int, walk: _Walk) -> dict[str, Any]:
    found: dict[tuple[bytes, ...], tuple[int, int]] = {}
    _index(moov, start, end, 3, walk, found, ())  # moov(1) > trak(2) > children(3).
    track: dict[str, Any] = {"handler": None, "width": 0, "height": 0, "rotation": UNKNOWN,
                             "fourcc": UNKNOWN, "codec": UNKNOWN, "fps": UNKNOWN, "vfr": UNKNOWN,
                             "range": UNKNOWN, "rate": UNKNOWN, "channels": UNKNOWN}
    if (b"tkhd",) in found:
        tstart, tend = found[(b"tkhd",)]
        if tend > tstart:
            offset = tstart + (4 + 32 if moov[tstart] == 1 else 4 + 20)
            # reserved(8) layer(2) alternate_group(2) volume(2) reserved(2) = 16
            matrix_at = offset + 16
            if matrix_at + 36 + 8 <= tend:
                matrix = struct.unpack(">9I", moov[matrix_at:matrix_at + 36])
                width, height = struct.unpack(">II", moov[matrix_at + 36:matrix_at + 44])
                track["width"], track["height"] = width >> 16, height >> 16
                a, b, c, d = (_fixed(matrix[0]), _fixed(matrix[1]), _fixed(matrix[3]), _fixed(matrix[4]))
                if a > 0.5 and d > 0.5:
                    track["rotation"] = 0
                elif b > 0.5 and c < -0.5:
                    track["rotation"] = 90
                elif a < -0.5 and d < -0.5:
                    track["rotation"] = 180
                elif b < -0.5 and c > 0.5:
                    track["rotation"] = 270
    hdlr = found.get((b"mdia", b"hdlr"))
    if hdlr and hdlr[1] - hdlr[0] >= 12:
        track["handler"] = moov[hdlr[0] + 8:hdlr[0] + 12]
    mdhd = found.get((b"mdia", b"mdhd"))
    timescale = _mdhd_timescale(moov, *mdhd) if mdhd else None
    stsd = found.get((b"mdia", b"minf", b"stbl", b"stsd"))
    entry = _first_sample_entry(moov, *stsd, walk) if stsd else None
    if entry is not None:
        fourcc, estart, eend = entry
        track["fourcc"] = _fourcc(fourcc)
        if track["handler"] == b"soun":
            audio = _audio_entry(moov, fourcc, estart, eend, walk)
            track.update(codec=audio["codec"], rate=audio["rate"], channels=audio["channels"])
        else:
            track["codec"] = VIDEO_FOURCC.get(fourcc, UNKNOWN)
    stts = found.get((b"mdia", b"minf", b"stbl", b"stts"))
    if stts and track["handler"] != b"soun":
        track.update(_frame_rate(moov, *stts, timescale, walk))
    return track


def _parse_moov(moov: bytes, info: dict) -> None:
    walk = _Walk()
    video_tracks, audio_tracks = [], []
    for btype, start, end in _iter_boxes(moov, 0, len(moov), walk.notes):
        if btype == b"mvhd" and end - start >= 20:
            version = moov[start]
            if version == 1 and end - start >= 32:
                timescale, duration = struct.unpack(">IQ", moov[start + 20:start + 32])
                unknown_duration = duration == 0xFFFFFFFFFFFFFFFF
            else:
                timescale, duration = struct.unpack(">II", moov[start + 12:start + 20])
                unknown_duration = duration == 0xFFFFFFFF
            if timescale and not unknown_duration:
                info["duration_seconds"] = round(duration / timescale, 3)
        elif btype == b"trak":
            track = _parse_trak(moov, start, end, walk)
            if track["handler"] == b"soun":
                audio_tracks.append(track)
            elif track["handler"] == b"vide" or (track["handler"] is None and track["width"] and track["height"]):
                video_tracks.append(track)
    for track in video_tracks:
        if track["width"] and track["height"]:
            width, height = track["width"], track["height"]
            if track["rotation"] in (90, 270):
                width, height = height, width
            info["width"], info["height"], info["rotation_degrees"] = width, height, track["rotation"]
            break
    if video_tracks:
        video = next((t for t in video_tracks if t["width"] and t["height"]), video_tracks[0])
        info.update(video_codec=video["codec"], video_fourcc=video["fourcc"], frame_rate_fps=video["fps"],
                    variable_frame_rate=video["vfr"], frame_rate_range_fps=video["range"])
    if audio_tracks:
        audio = audio_tracks[0]
        info.update(audio_codec=audio["codec"], audio_fourcc=audio["fourcc"],
                    audio_sample_rate_hz=audio["rate"], audio_channels=audio["channels"])
    # "No audio track" is only a fact when the whole moov was walked without a parse problem.
    info["audio_tracks"] = len(audio_tracks) if audio_tracks or not walk.notes else UNKNOWN
    info["parse_notes"].extend(walk.notes)


def _av_defaults() -> dict[str, Any]:
    return {"video_codec": UNKNOWN, "video_fourcc": UNKNOWN, "frame_rate_fps": UNKNOWN,
            "variable_frame_rate": UNKNOWN, "frame_rate_range_fps": UNKNOWN, "audio_tracks": UNKNOWN,
            "audio_codec": UNKNOWN, "audio_fourcc": UNKNOWN, "audio_sample_rate_hz": UNKNOWN,
            "audio_channels": UNKNOWN, "fast_start": UNKNOWN}


def _parse_isobmff(handle: BinaryIO, file_size: int, info: dict) -> None:
    info.update(_av_defaults())
    boxes: list[str] = info["top_level_boxes"]
    pos = 0
    for _ in range(MAX_TOP_LEVEL_BOXES):
        if pos + 8 > file_size:
            break
        handle.seek(pos)
        header = handle.read(16)
        if len(header) < 8:
            break
        size, btype = struct.unpack(">I4s", header[:8])
        head_len = 8
        if size == 1:
            if len(header) < 16:
                break
            size, head_len = struct.unpack(">Q", header[8:16])[0], 16
        elif size == 0:
            size = file_size - pos
        boxes.append(btype.decode("latin-1"))
        if size < head_len or pos + size > file_size:
            info["parse_notes"].append(f"malformed size {size} for top-level box {_fourcc(btype)!r} at offset {pos}")
            break
        if btype == b"ftyp":
            handle.seek(pos + head_len)
            brand = handle.read(4)
            info["container"] = "mov" if brand == b"qt  " else "mp4"
        elif btype == b"moov":
            if size - head_len <= MAX_MOOV_BYTES:
                handle.seek(pos + head_len)
                _parse_moov(handle.read(size - head_len), info)
            else:
                info["parse_notes"].append(f"moov box is {size} bytes (cap {MAX_MOOV_BYTES}); not parsed")
        pos += size
    else:
        info["parse_notes"].append(f"more than {MAX_TOP_LEVEL_BOXES} top-level boxes; remaining boxes ignored")
    if "moov" in boxes and "mdat" in boxes:
        info["fast_start"] = boxes.index("moov") < boxes.index("mdat")
    elif "mdat" in boxes:
        info["fast_start"] = False  # Media data was reached before any moov box.


def probe_file(path: Path) -> dict:
    """Read real headers. Unparseable values stay ``unknown``."""
    with path.open("rb") as handle:
        head = handle.read(65536)
        return _probe(handle, head, path.stat().st_size)


def probe_bytes(data: bytes) -> dict:
    """``probe_file`` on bytes already held in memory (issue #23: the exact bytes to be uploaded)."""
    handle = io.BytesIO(data)
    return _probe(handle, handle.read(65536), len(data))


def _probe(handle: BinaryIO, head: bytes, file_size: int) -> dict:
    info = _info()
    info["bytes"] = file_size
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        info["container"] = "png"
        _parse_png(head, info)
    elif head.startswith(b"\xff\xd8\xff"):
        info["container"] = "jpeg"
        _parse_jpeg(handle, info)
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        info["container"] = "gif"
        _parse_gif(head, info)
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        info["container"] = "webp"
        _parse_webp(head, info)
    elif head[4:8] == b"ftyp":
        _parse_isobmff(handle, file_size, info)
    elif head[4:8] in (b"moov", b"mdat", b"wide", b"free", b"skip"):
        info["container"] = "mov"  # Legacy QuickTime without ftyp.
        _parse_isobmff(handle, file_size, info)
    else:
        text = head.lstrip(b"\xef\xbb\xbf")
        if re.match(rb"WEBVTT(?:[ \t\r\n]|$)", text):
            info["container"] = "vtt"
        elif re.search(rb"\d{1,3}:\d{2}:\d{2},\d{3}\s*-->", text):
            info["container"] = "srt"
    return info


# ---------------------------------------------------------------------------
# Subtitle validation (SRT and WebVTT).
# ---------------------------------------------------------------------------
_SRT_TIME = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$")
_VTT_TIME = re.compile(r"^(?:(\d{2,}):)?([0-5]\d):([0-5]\d)\.(\d{3})$")


MAX_TIMESTAMP_HOUR_DIGITS = 6  # 999999 h; longer hour fields are malformed, not int()/float() overflows.


def _seconds(match) -> float | None:
    hours = _decimal_digits(match.group(1) or "0")
    if len(hours) > MAX_TIMESTAMP_HOUR_DIGITS:
        return None
    return (int(hours) * 3600 + int(_decimal_digits(match.group(2))) * 60 + int(_decimal_digits(match.group(3)))
            + int(_decimal_digits(match.group(4))) / 1000)


def _timing(line: str, pattern) -> tuple[float, float] | None:
    if "-->" not in line:
        return None
    left, right = line.split("-->", 1)
    right_parts = right.strip().split()
    if not right_parts:
        return None
    start, end = pattern.match(left.strip()), pattern.match(right_parts[0])
    if not start or not end:
        return None
    start_s, end_s = _seconds(start), _seconds(end)
    if start_s is None or end_s is None:
        return None
    return start_s, end_s


def validate_subtitle_text(text: str, fmt: str) -> tuple[list[str], list[str], dict]:
    """Return (errors, warnings, summary) for SRT/WebVTT text."""
    errors, warnings, summary, _ = _scan_subtitles(text, fmt)
    return errors, warnings, summary


def parse_subtitle_cues(text: str, fmt: str) -> list[dict[str, Any]]:
    """Well-formed cues as ``{"number", "start", "end", "text"}`` (seconds); malformed blocks are left out."""
    return _scan_subtitles(text, fmt)[3]


def _scan_subtitles(text: str, fmt: str) -> tuple[list[str], list[str], dict, list[dict[str, Any]]]:
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b for b in re.split(r"\n[ \t]*\n", text.strip("\n")) if b.strip()]
    pattern = _SRT_TIME if fmt == "srt" else _VTT_TIME
    if fmt == "vtt":
        if not blocks or not re.match(r"WEBVTT(?:[ \t]|$)", blocks[0].split("\n", 1)[0]):
            errors.append("WebVTT must start with a 'WEBVTT' header line")
            return errors, warnings, {"cues": 0, "last_end_seconds": UNKNOWN}, parsed
        blocks = blocks[1:]
    cues, previous_start, previous_end, expected_index = 0, None, None, 1
    for number, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if fmt == "vtt" and re.match(r"(?:NOTE|STYLE|REGION)(?:[ \t]|$)", lines[0]):
            continue
        label = f"cue {number}"
        if fmt == "srt":
            # str.isdecimal(), not isdigit(): "²" or "①" pass isdigit() and then make int() raise.
            raw_index = lines[0].strip()
            if not raw_index.isdecimal():
                errors.append(f"{label}: SRT cue must start with a numeric index")
                continue
            index = _decimal_digits(raw_index)
            if index != str(expected_index):
                warnings.append(f"{label}: SRT index {_shown_number(index)} is not sequential "
                                f"(expected {expected_index})")
            # An absurdly long index cannot go through int() (4300-digit limit); count on from the last one.
            expected_index = int(index) + 1 if len(index) <= 18 else expected_index + 1
            lines = lines[1:]
        elif lines and "-->" not in lines[0]:
            lines = lines[1:]  # Optional WebVTT cue identifier.
        if not lines:
            errors.append(f"{label}: missing timing line")
            continue
        timing = _timing(lines[0], pattern)
        if timing is None:
            errors.append(f"{label}: malformed timestamp line {lines[0].strip()[:60]!r}")
            continue
        start, end = timing
        if end <= start:
            errors.append(f"{label}: end time must be after start time")
        if previous_start is not None and start < previous_start:
            errors.append(f"{label}: start time goes backwards (timestamps must be monotonic)")
        elif previous_end is not None and start < previous_end:
            warnings.append(f"{label}: overlaps the previous cue; confirm this is intentional")
        if not any(line.strip() for line in lines[1:]):
            errors.append(f"{label}: cue text is empty")
        previous_start, previous_end = start, max(end, previous_end or end)
        cues += 1
        parsed.append({"number": cues, "start": start, "end": end, "text": "\n".join(lines[1:]).strip()})
    if cues == 0:
        errors.append("subtitle file contains no cues")
    summary = {"cues": cues, "last_end_seconds": previous_end if previous_end is not None else UNKNOWN}
    return errors, warnings, summary, parsed


# ---------------------------------------------------------------------------
# Translated subtitle tracks: cue alignment and fact preservation.
# ---------------------------------------------------------------------------
_CUE_MARKUP = re.compile(r"<[^>]*>|\{\\[^}]*\}")  # WebVTT/SRT tags: <i>, <c.x>, <00:01.000>, {\an8}
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
_URL_TRAILING = ".,;:!?)]}…»”’"
_DIGITS = re.compile(r"\d+")  # Unicode decimal digits, so Persian or Arabic-Indic numerals compare equal.
MAX_SHOWN_DIGITS = 20  # Longer digit runs are shortened in findings so one cue cannot flood the report.


def _decimal_digits(run: str) -> str:
    """A run of Unicode decimal digits (category Nd) as ASCII without leading zeros, never via ``int()``.

    ``int()`` refuses strings longer than ``sys.get_int_max_str_digits()`` (4300 by default since
    Python 3.11.0; https://docs.python.org/3/library/stdtypes.html#int-max-str-digits, accessed
    2026-10-09), so an over-long run in an owner file would crash the check.
    """
    return "".join(str(unicodedata.decimal(char)) for char in run).lstrip("0") or "0"


def _shown_number(digits: str) -> str:
    if len(digits) <= MAX_SHOWN_DIGITS:
        return digits
    return f"{digits[:MAX_SHOWN_DIGITS]}… ({len(digits)} digits)"


def _cue_facts(text: str) -> tuple[str, Counter, Counter]:
    """Plain cue text, its URLs and its digit runs (as ASCII digit strings; separators ignored)."""
    clean = html.unescape(_CUE_MARKUP.sub(" ", text))
    urls = Counter(url.rstrip(_URL_TRAILING) for url in _URL.findall(clean))
    numbers = Counter(_decimal_digits(run) for run in _DIGITS.findall(_URL.sub(" ", clean)))
    return clean, urls, numbers


def _numeric_order(digits: str) -> tuple[int, str]:
    return len(digits), digits  # Leading zeros are stripped, so length then text is numeric order.


def _locale_key(locale: object) -> str:
    """BCP-47 tags are case-insensitive (RFC 5646 section 2.1.1), so ``en-US`` and ``en-us`` match."""
    return locale.casefold() if isinstance(locale, str) else ""



_URL_PARTS = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?:[^@/?#]*@)?(?P<host>[^/?#]*)"
                        r"(?P<path>[^?#]*)(?P<rest>.*)$", re.S)


def _shown_url(url: str) -> str:
    """A cue URL for a warning: scheme, host and path only; userinfo, query and fragment never shown."""
    match = _URL_PARTS.match(url)
    if not match:
        return "<unparseable URL>"
    hidden = "?…" if match["rest"] else ""
    return f"{match['scheme']}://{match['host']}{match['path']}{hidden}"[:200]


def _clock(seconds: float) -> str:
    minutes, millis = divmod(round(seconds * 1000), 60_000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{millis / 1000:06.3f}"


def _align_cues(source: list[dict], target: list[dict], tolerance: float
                ) -> tuple[list[tuple[dict, dict]], list[dict], list[dict], list[tuple[dict, dict]]]:
    """``(pairs, missing, extra, mistimed)``. Equal counts pair by position; otherwise by timing."""
    pairs: list[tuple[dict, dict]]
    if len(source) == len(target):
        pairs = list(zip(source, target))
        mistimed = [(s, t) for s, t in pairs
                    if abs(s["start"] - t["start"]) > tolerance or abs(s["end"] - t["end"]) > tolerance]
        return pairs, [], [], mistimed
    pairs = []
    missing: list[dict] = []
    extra: list[dict] = []
    i = j = 0
    while i < len(source) and j < len(target):
        s, t = source[i], target[j]
        if abs(s["start"] - t["start"]) <= tolerance and abs(s["end"] - t["end"]) <= tolerance:
            pairs.append((s, t))
            i, j = i + 1, j + 1
        elif t["start"] < s["start"]:
            extra.append(t)
            j += 1
        else:
            missing.append(s)
            i += 1
    return pairs, missing + source[i:], extra + target[j:], []


def compare_subtitle_tracks(source: list[dict], target: list[dict], preserve: list[str], *,
                            tolerance_s: float = DEFAULT_SUBTITLE_TOLERANCE_MS / 1000,
                            alignment: str = "source") -> tuple[list[str], list[str], dict[str, Any]]:
    """Check a translated track's cues against its source track's; returns ``(errors, warnings, summary)``.

    Structure (cue count, missing or extra cues, start or end beyond ``tolerance_s``) is an
    error, because a track out of step with the speech misleads every viewer; ``alignment:
    "independent"`` (the owner re-timed the track on purpose) downgrades it to warnings.
    Content (a ``preserve_terms`` entry, number or URL in the source cue that the paired cue
    drops, or a URL or number the paired cue adds) is a warning, matching the alt-text
    preserved-term rule: a translation may adapt wording, so a human judges it. An added URL is
    reported because a link injected during translation is a phishing vector. Numbers compare
    as digit runs, so ``1,500`` matches ``1.500``, ``007`` matches ``7`` and Persian or
    Arabic-Indic digits match ASCII. Translation quality is not assessed.
    """
    errors: list[str] = []
    warnings: list[str] = []
    structural = warnings if alignment == "independent" else errors
    pairs, missing, extra, mistimed = _align_cues(source, target, tolerance_s)
    if len(source) != len(target):
        structural.append(f"has {len(target)} cues; the source track has {len(source)}")
    for cue in missing:
        structural.append(f"missing a cue for source cue {cue['number']} "
                          f"({_clock(cue['start'])} --> {_clock(cue['end'])})")
    for cue in extra:
        structural.append(f"extra cue {cue['number']} ({_clock(cue['start'])} --> {_clock(cue['end'])}) has no "
                          f"source cue within {tolerance_s:g}s")
    for s, t in mistimed:
        drift = max(abs(s["start"] - t["start"]), abs(s["end"] - t["end"]))
        structural.append(f"cue {t['number']} ({_clock(t['start'])} --> {_clock(t['end'])}) is {drift:.3f}s off "
                          f"source cue {s['number']} ({_clock(s['start'])} --> {_clock(s['end'])}); "
                          f"tolerance {tolerance_s:g}s")
    for s, t in pairs:
        s_text, s_urls, s_numbers = _cue_facts(s["text"])
        t_text, t_urls, t_numbers = _cue_facts(t["text"])
        where = f"cue {t['number']} (source cue {s['number']})"
        for term in preserve:
            if term in s_text and term not in t_text:
                warnings.append(f"{where} drops preserved term {term!r}; keep names, technical terms and "
                                "factual claims identical")
        dropped_urls = sorted((s_urls - t_urls).elements())
        if dropped_urls:
            warnings.append(f"{where} drops or changes URL {', '.join(map(_shown_url, dropped_urls))}; "
                            "links must stay identical")
        added_urls = sorted((t_urls - s_urls).elements())
        if added_urls:
            warnings.append(f"{where} adds URL {', '.join(map(_shown_url, added_urls))} that the source cue does not contain; "
                            "links must stay identical")
        if s_numbers != t_numbers:
            lost = [_shown_number(n) for n in sorted((s_numbers - t_numbers).elements(), key=_numeric_order)]
            added = [_shown_number(n) for n in sorted((t_numbers - s_numbers).elements(), key=_numeric_order)]
            parts = ([f"missing {', '.join(lost)}"] if lost else []) + ([f"added {', '.join(added)}"] if added else [])
            warnings.append(f"{where} numbers differ from the source cue ({'; '.join(parts)}); "
                            "keep every number identical")
    summary = {"cues": len(target), "source_cues": len(source), "paired": len(pairs), "missing": len(missing),
               "extra": len(extra), "mistimed": len(mistimed), "tolerance_s": tolerance_s, "alignment": alignment}
    return errors, warnings, summary


def _capped(items: list[str], label: str) -> list[str]:
    shown = [f"{label}: {item}" for item in items[:MAX_TRACK_FINDINGS]]
    if len(items) > MAX_TRACK_FINDINGS:
        shown.append(f"{label}: {len(items) - MAX_TRACK_FINDINGS} more finding(s) of this severity not listed")
    return shown


def _check_translated_subtitles(label: str, subtitles: list[dict], source_locale: str, preserve: list[str],
                                tolerance_s: float, errors: list[str], warnings: list[str]) -> list[dict]:
    """Compare every parsed non-source-locale track with the first parsed source-locale track.

    Locales compare case-insensitively; with translated tracks but no usable source-locale
    track the comparison is skipped with a warning, never silently.
    """
    usable = [sub for sub in subtitles if sub.get("cues") is not None]
    source_key = _locale_key(source_locale)
    translated = [sub for sub in usable if _locale_key(sub["locale"]) != source_key]
    if not translated:
        return []
    source = next((sub for sub in usable if _locale_key(sub["locale"]) == source_key), None)
    if source is None:
        warnings.append(f"{label}: translated subtitle tracks not compared: no valid {source_locale} source-locale "
                        "track to check cue timing, preserved terms, numbers and URLs against")
        return []
    reports = []
    for sub in translated:
        serr, swarn, summary = compare_subtitle_tracks(source["cues"], sub["cues"], preserve,
                                                       tolerance_s=tolerance_s, alignment=sub["alignment"])
        slabel = f"{label} subtitle {sub['path']} ({sub['locale']} vs source {source['path']})"
        errors.extend(_capped(serr, slabel))
        warnings.extend(_capped(swarn, slabel))
        reports.append({"path": sub["path"], "locale": sub["locale"], "source_path": source["path"],
                        "errors": len(serr), "warnings": len(swarn), **summary})
    return reports


# ---------------------------------------------------------------------------
# Manifest validation.
# ---------------------------------------------------------------------------

def read_manifest(path: str | Path) -> dict:
    try:
        obj = read_json_file(Path(path))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid media manifest JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise ValidationError("Media manifest must be a JSON object")
    return obj


def _resolve(base_dir: Path, value: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a relative file path")
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        errors.append(f"{label} must be relative to the manifest directory")
        return None
    resolved = (base_dir / candidate).resolve()
    if base_dir.resolve() not in (resolved, *resolved.parents):
        errors.append(f"{label} escapes the manifest directory")
        return None
    if not resolved.is_file():
        errors.append(f"{label} not found: {value}")
        return None
    return resolved


def _check_alt(text: object, path: Path | None, label: str, errors: list[str], warnings: list[str]) -> str | None:
    if not isinstance(text, str) or not text.strip():
        errors.append(f"{label}: alt text is required for images (describe what the image shows)")
        return None
    clean = text.strip()
    folded = clean.casefold().rstrip(".:")
    if folded in ALT_PLACEHOLDERS:
        errors.append(f"{label}: alt text {clean!r} is a placeholder, not a description")
    elif REDUNDANT_ALT_PREFIX.match(clean):
        warnings.append(f"{label}: alt text starts with 'image of'; screen readers already announce images")
    if path is not None and folded in (path.name.casefold(), path.stem.casefold()):
        errors.append(f"{label}: alt text must not be the file name")
    if PLACEHOLDER.search(clean):
        errors.append(f"{label}: alt text contains an unresolved placeholder/TODO")
    return clean


def _aspect(info: dict):
    if isinstance(info.get("width"), int) and isinstance(info.get("height"), int) and info["height"]:
        return info["width"] / info["height"]
    return UNKNOWN


def _skip(warnings: list[str], label: str, target: str, kind: str, name: str, why: str) -> None:
    warnings.append(f"{label}: {target} {kind} constraint '{name}' skipped ({why})")


def _check_target(label: str, target: str, kind: str, info: dict, asset: dict, alts: dict,
                  subtitles: list[dict], errors: list[str], warnings: list[str]) -> dict[str, list[str]]:
    """Apply one platform's rows. Unknown rows or unknown file values skip with a warning."""
    rows = CONSTRAINTS[target].get(kind)
    summary: dict[str, list[str]] = {"checked": [], "skipped": []}
    if rows is None:
        warnings.append(f"{label}: no verified {kind} constraint table for {target}; manual review required")
        return summary
    for name, row in rows.items():
        value, severity = row["value"], row["severity"]
        sink = errors if severity == "error" else warnings
        if value is None:
            summary["skipped"].append(name)
            _skip(warnings, label, target, kind, name, "value not established in verified source " + row["source"])
            continue
        def fail(message: str) -> None:
            sink.append(f"{label}: {target} {kind}: {message} [source: {row['source']}, verified {row['verified_date']}]")
        width, height, ratio = info.get("width"), info.get("height"), _aspect(info)
        if name == "allowed_containers":
            if info["container"] not in value:
                fail(f"container {info['container']} not in allowed {', '.join(value)}")
        elif name == "max_bytes":
            if info["bytes"] > value:
                fail(f"file is {info['bytes']} bytes; limit is {value}")
        elif name == "min_bytes":
            if info["bytes"] < value:
                fail(f"file is {info['bytes']} bytes; minimum is {value}")
        elif name in ("max_duration_s", "min_duration_s"):
            duration = info.get("duration_seconds")
            if duration == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, "duration unknown: header not parseable")
                continue
            if name == "max_duration_s" and duration > value:
                fail(f"duration {duration}s exceeds {value}s")
            if name == "min_duration_s" and duration < value:
                fail(f"duration {duration}s is below {value}s")
        elif name == "aspect_ratio":
            if ratio == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, "dimensions unknown: header not parseable")
                continue
            low, high = value
            if not low <= ratio <= high + 1e-9:
                fail(f"aspect ratio {ratio:.4f} outside {low}..{high} (width/height)")
        elif name in ("min_width", "min_height", "max_width", "min_dimension", "max_dimension", "max_pixels"):
            if not isinstance(width, int) or not isinstance(height, int):
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, "dimensions unknown: header not parseable")
                continue
            if name == "min_width" and width < value:
                fail(f"width {width}px below {value}px")
            elif name == "min_height" and height < value:
                fail(f"height {height}px below {value}px")
            elif name == "max_width" and width > value:
                fail(f"width {width}px above {value}px")
            elif name == "min_dimension" and min(width, height) < value:
                fail(f"{width}x{height} has a dimension below {value}px")
            elif name == "max_dimension" and max(width, height) > value:
                fail(f"{width}x{height} has a dimension above {value}px")
            elif name == "max_pixels" and width * height > value:
                fail(f"{width * height} pixels exceeds {value}")
        elif name == "moov_before_mdat":
            if info.get("fast_start", UNKNOWN) == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, "moov/mdat order unknown: boxes not found")
                continue
            if info["fast_start"] is False:
                fail("moov box must precede mdat (re-mux with a 'faststart' option)")
        elif name in ("video_codecs", "audio_codecs"):
            stream = "video" if name == "video_codecs" else "audio"
            codec, fourcc = info.get(f"{stream}_codec", UNKNOWN), info.get(f"{stream}_fourcc", UNKNOWN)
            if stream == "audio" and info.get("audio_tracks") == 0:
                pass  # No audio track: nothing to check.
            elif codec == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name,
                      f"{stream} codec unknown: sample entry {fourcc} not recognized or not parseable")
                continue
            elif codec not in value:
                wording = "allowed" if severity == "error" else "recommended"
                fail(f"{stream} codec {codec} ({fourcc}) not in {wording} {', '.join(value)}")
        elif name == "frame_rate_fps":
            fps = info.get("frame_rate_fps", UNKNOWN)
            if fps == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, "frame rate unknown: mdhd/stts not parseable")
                continue
            low, high = value
            if not low <= fps <= high:
                fail(f"average frame rate {fps} fps outside {low}..{high} fps")
            if info.get("variable_frame_rate") is True:
                spread = info["frame_rate_range_fps"]
                warnings.append(f"{label}: {target} {kind}: variable frame rate detected (sample durations span "
                                f"{spread[0]}..{spread[1]} fps); the documented {low}..{high} fps range was "
                                f"checked against the {fps} fps average only [source: {row['source']}, "
                                f"verified {row['verified_date']}]")
        elif name in ("max_audio_sample_rate_hz", "audio_sample_rate_hz", "max_audio_channels"):
            field = "audio_channels" if name == "max_audio_channels" else "audio_sample_rate_hz"
            actual = info.get(field, UNKNOWN)
            if info.get("audio_tracks") == 0:
                pass  # No audio track: nothing to check.
            elif actual == UNKNOWN:
                summary["skipped"].append(name)
                _skip(warnings, label, target, kind, name, f"{field} unknown: audio sample entry not parseable")
                continue
            elif name == "max_audio_sample_rate_hz" and actual > value:
                fail(f"audio sample rate {actual} Hz exceeds {value} Hz")
            elif name == "audio_sample_rate_hz" and actual != value:
                fail(f"audio sample rate {actual} Hz; recommended {value} Hz")
            elif name == "max_audio_channels" and actual > value:
                fail(f"audio has {actual} channels; maximum is {value}")
        elif name == "alt_text_max_chars":
            for locale, text in alts.items():
                if len(text) > value:
                    fail(f"alt text ({locale}) has {len(text)} code points; limit is {value}")
        elif name == "alt_text_supported":
            if not value and asset.get("alt_text"):
                fail("alt text is not accepted for this format; carry the description in the caption")
        elif name == "subtitle_formats":
            if subtitles and not any(sub["format"] in value for sub in subtitles):
                fail(f"needs a {'/'.join(value)} subtitle file; got {', '.join(sorted({s['format'] for s in subtitles}))}")
        elif name == "subtitle_max_bytes":
            allowed = rows.get("subtitle_formats", {}).get("value") or []
            for sub in subtitles:
                if sub["format"] in allowed and sub["bytes"] > value:
                    fail(f"subtitle {sub['path']} is {sub['bytes']} bytes; limit is {value}")
        summary["checked"].append(name)
    return summary


def _check_rights(label: str, asset: dict, errors: list[str], warnings: list[str]) -> None:
    if asset.get("rights_confirmed") is not True:
        errors.append(f"{label}: rights_confirmed must be true (owner confirms rights, consent and likeness permission)")
    license_id = asset.get("license")
    if not isinstance(license_id, str) or not license_id.strip():
        errors.append(f"{label}: license is required (SPDX id, 'owner' or 'licensed')")
        license_id = ""
    elif license_id not in LICENSE_KEYWORDS:
        if not SPDX_ID.fullmatch(license_id):
            errors.append(f"{label}: license {license_id!r} is not an SPDX identifier, 'owner' or 'licensed'")
        elif license_id not in COMMON_SPDX:
            warnings.append(f"{label}: license {license_id!r} is not in the built-in common SPDX list; "
                            "verify at https://spdx.org/licenses/")
    provenance = asset.get("provenance")
    if not isinstance(provenance, dict):
        errors.append(f"{label}: provenance object is required (created_by, tool, source_url)")
        return
    creator = provenance.get("created_by")
    attribution = asset.get("attribution")
    has_attribution = isinstance(attribution, str) and bool(attribution.strip())
    source_url = provenance.get("source_url")
    if creator not in CREATORS:
        errors.append(f"{label}: provenance.created_by must be one of {', '.join(sorted(CREATORS))}")
    if creator == "tool":
        if not isinstance(provenance.get("tool"), str) or not provenance["tool"].strip():
            errors.append(f"{label}: provenance.tool must name the generation/editing tool")
        warnings.append(f"{label}: tool-generated or tool-edited media; disclose per platform policy and confirm "
                        "no unauthorized likeness, cloned voice/persona or copyrighted input")
    if creator == "third_party":
        if license_id in ("", "owner"):
            errors.append(f"{label}: third-party media needs its actual license (SPDX id or 'licensed'), not 'owner'")
        if not has_attribution:
            errors.append(f"{label}: third-party media requires attribution text")
        if not source_url:
            warnings.append(f"{label}: third-party media has no provenance.source_url; record where it came from")
    if source_url and not public_url(source_url):
        warnings.append(f"{label}: provenance.source_url should be a public HTTPS URL without credentials")
    if license_id.startswith("CC-BY") and not has_attribution:
        errors.append(f"{label}: {license_id} requires attribution text")


def _check_locales(label: str, asset: dict, default_locale: str, targets: list[str], alts: dict,
                   subtitles: list[dict], preserve: list[str], kind: str,
                   errors: list[str], warnings: list[str]) -> None:
    locale = asset.get("locale", default_locale)
    if not isinstance(locale, str) or not BCP47.fullmatch(locale):
        errors.append(f"{label}: locale {locale!r} must be a BCP-47 tag such as en, en-US, fa-IR or it")
        return
    base = next((text for loc, text in alts.items() if _locale_key(loc) == _locale_key(locale)), "")
    alt_keys = {_locale_key(loc) for loc in alts}
    for target in targets:
        if _locale_key(target) == _locale_key(locale):
            continue
        if kind == "image" and _locale_key(target) not in alt_keys:
            warnings.append(f"{label}: missing alt text translation for target locale {target}")
        if kind == "video" and subtitles and not any(_locale_key(sub.get("locale")) == _locale_key(target)
                                                     for sub in subtitles):
            warnings.append(f"{label}: missing subtitle file for target locale {target}")
    for other, text in alts.items():
        if _locale_key(other) == _locale_key(locale):
            continue
        for term in preserve:
            if term in base and term not in text:
                warnings.append(f"{label}: translation {other} drops preserved term {term!r}; keep names, "
                                "technical terms and factual claims identical")


def validate_media_manifest(manifest: dict, base_dir: str | Path) -> dict:
    """Validate a media manifest offline. Errors block; warnings need human judgment."""
    base_dir = Path(base_dir)
    errors, warnings = _sensitive_keys(manifest, "manifest"), []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    default_locale = manifest.get("default_locale", "en")
    if not isinstance(default_locale, str) or not BCP47.fullmatch(default_locale):
        errors.append("default_locale must be a BCP-47 tag (English 'en' recommended first)")
        default_locale = "en"
    elif default_locale.split("-")[0].lower() != "en":
        warnings.append("default_locale is not English; ReachWeaver media workflow is English-first")
    targets_locales = manifest.get("target_locales", [default_locale])
    if not isinstance(targets_locales, list) or not all(isinstance(t, str) and BCP47.fullmatch(t) for t in targets_locales):
        errors.append("target_locales must be a list of BCP-47 tags")
        targets_locales = [default_locale]
    preserve = manifest.get("preserve_terms", [])
    if not isinstance(preserve, list) or not all(isinstance(t, str) and t.strip() for t in preserve):
        errors.append("preserve_terms must be a list of non-empty strings")
        preserve = []
    tolerance_ms = manifest.get("subtitle_timing_tolerance_ms", DEFAULT_SUBTITLE_TOLERANCE_MS)
    if (isinstance(tolerance_ms, bool) or not isinstance(tolerance_ms, int)
            or not 0 <= tolerance_ms <= MAX_SUBTITLE_TOLERANCE_MS):
        errors.append(f"subtitle_timing_tolerance_ms must be an integer from 0 to {MAX_SUBTITLE_TOLERANCE_MS}")
        tolerance_ms = DEFAULT_SUBTITLE_TOLERANCE_MS
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("assets must be a non-empty list")
        assets = []
    if len(assets) > MAX_ASSETS:
        errors.append(f"At most {MAX_ASSETS} assets per manifest")
        assets = assets[:MAX_ASSETS]
    reports, ids = [], set()
    for idx, asset in enumerate(assets):
        label = f"assets[{idx}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} must be an object")
            continue
        asset_id = asset.get("id")
        if isinstance(asset_id, str) and asset_id:
            if asset_id in ids:
                errors.append(f"{label}: duplicate asset id {asset_id!r}")
            ids.add(asset_id)
            label = f"assets[{idx}] ({asset_id})"
        kind = asset.get("kind")
        if kind not in KINDS:
            errors.append(f"{label}: kind must be image, video or subtitle")
            continue
        path = _resolve(base_dir, asset.get("path"), f"{label}.path", errors)
        report = {"id": asset_id, "path": asset.get("path"), "kind": kind, "detected_container": UNKNOWN,
                  "bytes": UNKNOWN, "width": UNKNOWN, "height": UNKNOWN, "aspect_ratio": UNKNOWN,
                  "duration_seconds": UNKNOWN, "rotation_degrees": UNKNOWN, "targets": {}, "notes": [],
                  "subtitle_translations": []}
        reports.append(report)
        _check_rights(label, asset, errors, warnings)
        if "realistic_synthetic" in asset:  # optional, additive: read by ai-label-check (docs/AI-CONTENT-LABELS.md)
            if not isinstance(asset["realistic_synthetic"], bool):
                errors.append(f"{label}.realistic_synthetic must be true or false when present")
            elif asset["realistic_synthetic"]:
                warnings.append(f"{label}: marked realistic_synthetic; most platforms ask for an AI label on "
                                "realistic AI-made media. Run ai-label-check for each target platform")
        for key in ("caption", "content_warning", "attribution"):
            value = asset.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{label}.{key} must be a string")
            elif key == "content_warning" and value is not None and not value.strip():
                errors.append(f"{label}.content_warning must be non-empty when present")
            elif isinstance(value, str) and PLACEHOLDER.search(value):
                errors.append(f"{label}.{key} contains an unresolved placeholder/TODO")
        if path is None:
            continue
        info = probe_file(path)
        report.update({"detected_container": info["container"], "bytes": info["bytes"],
                       "width": info["width"], "height": info["height"],
                       "duration_seconds": info["duration_seconds"], "rotation_degrees": info["rotation_degrees"]})
        ratio = _aspect(info)
        report["aspect_ratio"] = round(ratio, 4) if ratio != UNKNOWN else UNKNOWN
        if kind == "video":
            report.update({key: info.get(key, UNKNOWN) for key in AV_REPORT_FIELDS})
        for note in info["parse_notes"]:
            warnings.append(f"{label}: header parsing stopped early ({note}); dependent values reported unknown")
        expected = EXTENSIONS.get(path.suffix.lower())
        if info["container"] == UNKNOWN:
            errors.append(f"{label}: unrecognized file header; cannot confirm a supported image/video/subtitle format")
        elif expected is None:
            warnings.append(f"{label}: unrecognized extension {path.suffix!r}; detected {info['container']}")
        elif expected != info["container"]:
            same_family = {expected, info["container"]} <= VIDEO_CONTAINERS
            (warnings if same_family else errors).append(
                f"{label}: extension {path.suffix} does not match detected {info['container']} header")
        family = {"image": IMAGE_CONTAINERS, "video": VIDEO_CONTAINERS, "subtitle": SUBTITLE_FORMATS}[kind]
        if info["container"] != UNKNOWN and info["container"] not in family:
            errors.append(f"{label}: declared kind {kind} but file header is {info['container']}")
        if kind in ("image", "video") and info["container"] in family:
            fields: tuple[str, ...] = ("width", "height")
            if kind == "video":
                fields += ("duration_seconds", "video_codec", "frame_rate_fps")
            for field in fields:
                if info[field] == UNKNOWN:
                    why = "header not parseable"
                    if field == "video_codec" and info.get("video_fourcc", UNKNOWN) != UNKNOWN:
                        why = f"sample entry {info['video_fourcc']} not recognized"
                    warnings.append(f"{label}: {field} unknown ({why}); dependent checks skipped")
        if info.get("has_exif"):
            warnings.append(f"{label}: embedded EXIF metadata present; it may reveal location/device data and "
                            "platforms may strip it. Remove sensitive metadata; never rely on it for credit")
        # Accessibility.
        alts: dict[str, str] = {}
        locale = asset.get("locale", default_locale) if isinstance(asset.get("locale", default_locale), str) else default_locale
        if kind == "image":
            clean = _check_alt(asset.get("alt_text"), path, label, errors, warnings)
            if clean:
                alts[locale] = clean
        elif isinstance(asset.get("alt_text"), str) and asset["alt_text"].strip():
            alts[locale] = asset["alt_text"].strip()
        localized = asset.get("alt_text_localized", {})
        if not isinstance(localized, dict):
            errors.append(f"{label}.alt_text_localized must be an object mapping locale to text")
            localized = {}
        for loc, text in localized.items():
            if not BCP47.fullmatch(str(loc)):
                errors.append(f"{label}.alt_text_localized: {loc!r} is not a BCP-47 tag")
                continue
            clean = _check_alt(text, path, f"{label} [{loc}]", errors, warnings)
            if clean:
                alts[loc] = clean
        subtitles: list[dict] = []
        if kind == "subtitle":
            subtitles.append({"path": asset.get("path"), "format": info["container"], "bytes": info["bytes"],
                              "locale": locale, "resolved": path, "alignment": "source"})
        raw_subs = asset.get("subtitles", [])
        if not isinstance(raw_subs, list):
            errors.append(f"{label}.subtitles must be a list of {{path, locale}} objects")
            raw_subs = []
        for sidx, sub in enumerate(raw_subs):
            slabel = f"{label}.subtitles[{sidx}]"
            if not isinstance(sub, dict):
                errors.append(f"{slabel} must be an object")
                continue
            spath = _resolve(base_dir, sub.get("path"), f"{slabel}.path", errors)
            sloc = sub.get("locale", locale)
            if not isinstance(sloc, str) or not BCP47.fullmatch(sloc):
                errors.append(f"{slabel}.locale must be a BCP-47 tag")
            alignment = sub.get("alignment", "source")
            if alignment not in SUBTITLE_ALIGNMENTS:
                errors.append(f"{slabel}.alignment must be 'source' (cues follow the source track) or "
                              "'independent' (re-timed on purpose)")
                alignment = "source"
            if spath is None:
                continue
            sfmt = EXTENSIONS.get(spath.suffix.lower())
            if sfmt not in SUBTITLE_FORMATS:
                errors.append(f"{slabel}: subtitle must be .srt or .vtt")
                continue
            subtitles.append({"path": sub.get("path"), "format": sfmt, "bytes": spath.stat().st_size,
                              "locale": sloc, "resolved": spath, "alignment": alignment})
        for sub in subtitles:
            if sub["format"] not in SUBTITLE_FORMATS:
                continue
            slabel = f"{label} subtitle {sub['path']}"
            if sub["bytes"] > MAX_SUBTITLE_BYTES:
                errors.append(f"{slabel}: larger than {MAX_SUBTITLE_BYTES} bytes")
                continue
            try:
                text = sub["resolved"].read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"{slabel}: subtitle files must be UTF-8 text")
                continue
            serr, swarn, ssum, cues = _scan_subtitles(text, sub["format"])
            if PLACEHOLDER.search(text):
                serr.append("contains an unresolved placeholder/TODO; write the real subtitle text")
            if not serr:
                sub["cues"] = cues  # Only well-formed, written tracks take part in translation checks.
            errors.extend(f"{slabel}: {item}" for item in serr)
            warnings.extend(f"{slabel}: {item}" for item in swarn)
            if (kind == "video" and isinstance(info["duration_seconds"], float)
                    and isinstance(ssum["last_end_seconds"], float) and ssum["last_end_seconds"] > info["duration_seconds"] + 0.5):
                warnings.append(f"{slabel}: last cue ends after the video ({ssum['last_end_seconds']}s > "
                                f"{info['duration_seconds']}s)")
        report["subtitle_translations"] = _check_translated_subtitles(
            label, subtitles, locale, preserve, tolerance_ms / 1000, errors, warnings)
        if kind == "video" and not subtitles:
            if asset.get("burned_in_captions") is True:
                warnings.append(f"{label}: only burned-in captions declared; cannot be verified offline and are "
                                "not user-toggleable. Prefer a sidecar SRT/WebVTT file")
            else:
                errors.append(f"{label}: videos require captions/subtitles (SRT or WebVTT sidecar file)")
        if kind == "video" and ratio != UNKNOWN and ratio < 1:
            report["notes"].append("Vertical video: keep text, faces and key UI away from edges where platform "
                                   "controls/captions overlay. ReachWeaver does not measure overlay zones; preview "
                                   "in each native app before publishing (report only).")
        # Platform targets.
        targets = asset.get("targets", [])
        if not isinstance(targets, list) or not targets or not all(isinstance(t, str) for t in targets):
            errors.append(f"{label}.targets must be a non-empty list of platform ids")
            targets = []
        for target in targets:
            if target not in CONSTRAINTS:
                warnings.append(f"{label}: no verified media constraint table for target {target!r}; "
                                f"known: {', '.join(sorted(CONSTRAINTS))}")
                continue
            if kind == "subtitle":
                continue
            report["targets"][target] = _check_target(label, target, kind, info, asset, alts, subtitles,
                                                      errors, warnings)
        _check_locales(label, asset, default_locale, targets_locales, alts, subtitles, preserve, kind,
                       errors, warnings)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "assets": reports,
        "constraints_verified_date": VERIFIED_DATE,
        "constraints_verified_dates": sorted({row["verified_date"] for rows in CONSTRAINTS.values()
                                              for kind_rows in rows.values() for row in kind_rows.values()}),
        "note": ("Offline validation only. ReachWeaver does not upload, render or publish media, and does not "
                 "call any generation tool or paid model. Re-check platform documentation before publishing."),
    }


def media_check(manifest_path: str | Path) -> dict:
    path = Path(manifest_path)
    return validate_media_manifest(read_manifest(path), path.parent)


def check_file_for_targets(path: str | Path, targets: list[str] | tuple[str, ...]) -> dict:
    """Header-only technical checks of one local photo or video against each target's sourced table.

    Used by ``share --attach`` when no media manifest is supplied. It applies the same rows as
    ``media-check`` (container, size, duration, aspect ratio, dimensions, codecs, frame rate, audio) but
    there is no alt text, caption file or rights record to check, so those stay the account holder's job.
    ``problems`` come from rows whose cited source states a hard limit and ``advice`` from recommendations;
    the caller decides how to show them. A target without a sourced table, an unreadable header or an
    unknown value is reported as not checked, never guessed. Reads the file locally; nothing is uploaded.
    """
    path = Path(path)
    info = probe_file(path)
    container = info["container"]
    kind = "video" if container in VIDEO_CONTAINERS else "image" if container in IMAGE_CONTAINERS else None
    ratio = _aspect(info)
    result: dict[str, Any] = {
        "path": str(path), "kind": kind,
        "detected": {"container": container, "bytes": info.get("bytes", UNKNOWN), "width": info["width"],
                     "height": info["height"], "aspect_ratio": round(ratio, 4) if ratio != UNKNOWN else UNKNOWN,
                     "duration_seconds": info["duration_seconds"]},
        "notes": [], "targets": {},
    }
    if kind is None:
        result["notes"].append("unrecognized file header: not a PNG, JPEG, GIF, WebP, MP4 or MOV this checker can "
                               "read (WebM is not detected), so no platform limit was checked")
        return result
    expected = EXTENSIONS.get(path.suffix.lower())
    if expected is not None and expected != container and not {expected, container} <= VIDEO_CONTAINERS:
        result["notes"].append(f"extension {path.suffix} does not match the detected {container} header")
    if info.get("has_exif"):
        result["notes"].append("embedded EXIF metadata present; it may reveal location or device data. Remove "
                               "sensitive metadata before you upload")
    for target in dict.fromkeys(targets):
        if target not in CONSTRAINTS or kind not in CONSTRAINTS[target]:
            result["targets"][target] = {"problems": [], "advice": [], "checked": [], "not_checked": [],
                                         "table": False}
            continue
        errors: list[str] = []
        warnings: list[str] = []
        summary = _check_target(path.name, target, kind, info, {}, {}, [], errors, warnings)
        skipped = set(summary["skipped"])
        advice = [w for w in warnings if not any(f"constraint '{name}' skipped" in w for name in skipped)]
        result["targets"][target] = {"problems": errors, "advice": advice, "checked": summary["checked"],
                                     "not_checked": sorted(skipped), "table": True}
    return result
