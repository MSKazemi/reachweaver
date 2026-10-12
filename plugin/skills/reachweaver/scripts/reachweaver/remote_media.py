"""Owner-hosted image URL attachments for Instagram drafts (issue #20, owner-approved 2026-10-09; disabled until a live receipt).

Instagram's content-publishing API takes no upload: "We will cURL your image using the passed
in URL so it must be on a public server", and the "media must be hosted on a publicly
accessible server at the time of the attempt"
(https://developers.facebook.com/docs/instagram-platform/content-publishing, accessed
2026-10-09). ReachWeaver hosts nothing, so the owner supplies an HTTPS URL on a host they
control. ReachWeaver downloads it read-only, validates the bytes and records their SHA-256,
which the approval hash binds (``storage.approval_payload``, ``approval/media-1``). Immediately before the
container POST the sender downloads the URL again and refuses unless the bytes still hash to
the approved value. Residual risk: Meta fetches the URL itself afterwards, so a host that
serves different bytes to Meta (or changes them in between) is not detectable here (see
docs/SECURITY-REVIEW-PROPOSAL-20-instagram.md).

The record is the shared image attachment model of ``attachments`` (owner decision D20-7,
2026-10-09): ``source: "url"``, the same ``media_json`` column, the same
``reachweaver.storage.approval/media-1`` tag and the same bound fields ``sha256, mime, alt,
focus``, plus ``url`` because the URL is what is sent. ``focus`` is always null here (Instagram
has no focal-point field). The codec (``encode_media``, ``decode_media``, ``bound_view``) is
the shared one, re-exported here.

Image rules, from the IG User Media reference
(https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media,
accessed 2026-10-09): "Format: JPEG"; "File size: 8 MB maximum." (counted here as 8,000,000
bytes, the lower reading); aspect ratio "Must be within a 4:5 to 1.91:1 range"; widths outside
320..1440 are scaled by Meta, not refused; ``alt_text`` "up to 1000 character". The content
publishing page adds "JPEG is the only image format supported. Extended JPEG formats such as
MPO and JPS are not supported." MPO/JPS are NOT detected here (unverified residual risk; Meta
then refuses the container). Non-ASCII URLs fail at Meta, so only printable ASCII is accepted.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import io
import ipaddress
import re
import socket
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .attachments import MEDIA_CHANGED, SOURCE_FIELDS
from .attachments import bound_view as bound_view, decode_media as decode_media, encode_media as encode_media  # shared codec
from .media import ALT_PLACEHOLDERS, UNKNOWN, _info, _parse_jpeg
from .policy import (BIDI_CONTROLS, INVISIBLE_FORMAT_CHARS, PLACEHOLDER, ValidationError,
                     require_https_public_server)

MEDIA_PLATFORMS = frozenset({"instagram"})  # platforms that take owner-hosted URL images
MAX_ATTACHMENTS = 1                 # single image first (issue #20); carousels are a later issue
MAX_IMAGE_BYTES = 8_000_000         # official "8 MB maximum"; decimal reading is the stricter one
ASPECT_MIN = (4, 5)                 # official 4:5 (width:height)
ASPECT_MAX = (191, 100)             # official 1.91:1
ALT_TEXT_MAX = 1000                 # official alt_text limit (characters)
MAX_URL_LENGTH = 2048               # ReachWeaver cap, not a documented Meta limit
FIELDS = SOURCE_FIELDS["url"]  # the shared record with source "url"
BOUND_FIELDS = ("sha256", "mime", "alt", "focus", "url")  # what the approval hash covers, per attachment
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_URL_CHARS = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no spaces or controls

def _system_resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


# Indirection so tests resolve without DNS; the no-network guard refuses real lookups.
_resolve: Callable[[str], list[str]] = _system_resolve


class ImageFetchError(ValidationError):
    """Read-only image download refused or failed before any social request (fixed code text)."""


def validate_image_url(value: object) -> str:
    """An owner-hosted HTTPS image URL: public DNS host, port 443, no userinfo, query or fragment.

    A query string is refused outright: signed or tokenized URLs would put a secret in the
    local database, the approval record and Meta's fetch logs. Returns the canonical URL.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH or not _URL_CHARS.fullmatch(value):
        raise ValidationError(f"image URL must be 1..{MAX_URL_LENGTH} printable ASCII characters "
                              "(percent-encode anything else)")
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValidationError("image URL must use https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValidationError("image URL must not contain credentials")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValidationError("image URL must not have a query string or fragment (signed or tokenized "
                              "URLs would store a secret); host the image at a plain path")
    try:
        port = parts.port
    except ValueError:
        raise ValidationError("image URL has an invalid port") from None
    if port not in (None, 443):
        raise ValidationError("image URL must use the standard HTTPS port 443")
    origin = require_https_public_server(f"https://{parts.hostname or ''}")  # no localhost/.local/IP literal
    if not parts.path or parts.path == "/" or ".." in parts.path.split("/"):
        raise ValidationError("image URL needs a file path (no '..' segments)")
    return origin + parts.path


def _require_public_addresses(host: str) -> None:
    """Defense in depth against fetching private addresses (the URL host is not an IP literal)."""
    try:
        addresses = _resolve(host)
    except (OSError, UnicodeError):
        raise ImageFetchError("image_url_unresolvable: the image host did not resolve") from None
    if not addresses:
        raise ImageFetchError("image_url_unresolvable: the image host did not resolve")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            raise ImageFetchError("image_url_unresolvable: unexpected address form") from None
        if not ip.is_global:
            raise ImageFetchError("image_url_not_public: the image host resolves to a non-public address; "
                                  "Meta can only fetch publicly reachable images")


def download_image(url: str, *, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Read-only GET of an owner-hosted image: no credentials, no cookies, no redirects, bounded.

    Errors are fixed codes (``ImageFetchError``) that never echo the URL or response text.
    """
    from . import publishers  # shares the no-redirect opener tests mock (publishers._send_http)
    canonical = validate_image_url(url)
    _require_public_addresses(urlsplit(canonical).hostname or "")
    req = publishers._request(canonical, method="GET",
                              headers={"User-Agent": "ReachWeaver/0.3", "Accept": "image/jpeg"})
    try:
        with publishers._open(req) as response:
            length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
            if length is not None and str(length).isascii() and str(length).isdecimal() and int(length) > max_bytes:
                raise ImageFetchError(f"image_too_large: more than {max_bytes} bytes")
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ImageFetchError(f"image_url_redirect_refused: http_{exc.code}; give the final URL "
                                  "(Meta fetches it directly)") from None
        raise ImageFetchError(f"image_url_http_{exc.code}") from None
    except publishers.PublishError:
        raise ImageFetchError("image_url_invalid_request") from None
    except (URLError, TimeoutError, OSError, http.client.HTTPException):
        raise ImageFetchError("image_url_unreachable") from None
    if len(data) > max_bytes:
        raise ImageFetchError(f"image_too_large: more than {max_bytes} bytes")
    if not data:
        raise ImageFetchError("image_empty")
    return data


def inspect_image(data: bytes) -> tuple[str, int, int]:
    """(MIME, width, height) from the bytes; JPEG with readable dimensions and an allowed aspect ratio."""
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValidationError("image must be a JPEG (checked from the bytes, not the URL or Content-Type); "
                              "Instagram supports JPEG only")
    info = _info("jpeg")
    _parse_jpeg(io.BytesIO(data), info)
    width, height = info["width"], info["height"]
    if width == UNKNOWN or height == UNKNOWN or not isinstance(width, int) or not isinstance(height, int):
        raise ValidationError("JPEG dimensions could not be read from the image header; re-export it")
    if width * ASPECT_MIN[1] < height * ASPECT_MIN[0] or width * ASPECT_MAX[1] > height * ASPECT_MAX[0]:
        raise ValidationError(f"image is {width}x{height}; Instagram requires an aspect ratio from 4:5 to 1.91:1")
    return "image/jpeg", width, height


def validate_alt(alt: object, limit: int = ALT_TEXT_MAX) -> str:
    """Required, human-written alt text (sent as ``alt_text``); surrounding whitespace removed."""
    if not isinstance(alt, str) or not alt.strip():
        raise ValidationError("alt text is required (describe what the image shows); it is sent as alt_text")
    text = alt.strip()
    if len(text) > limit:
        raise ValidationError(f"alt text has {len(text)} characters; the Instagram limit is {limit}")
    if text.casefold() in ALT_PLACEHOLDERS or PLACEHOLDER.search(text):
        raise ValidationError("alt text is a placeholder; describe what the image shows")
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in text):
        raise ValidationError("alt text includes control characters")
    if any(ch in BIDI_CONTROLS or ch in INVISIBLE_FORMAT_CHARS for ch in text):
        raise ValidationError("alt text includes bidirectional-control or invisible format characters")
    return text


def build_remote_attachment(url: object, alt: object, *,
                            fetch: Callable[[str], bytes] = download_image) -> dict[str, Any]:
    """Validate the URL and alt text, download the image read-only and return its stored record."""
    canonical = validate_image_url(url)
    text = validate_alt(alt)
    data = fetch(canonical)
    mime, width, height = inspect_image(data)
    return {"source": "url", "url": canonical, "sha256": hashlib.sha256(data).hexdigest(), "mime": mime,
            "bytes": len(data), "width": width, "height": height, "alt": text, "focus": None}


def check_media_list(media: list[dict], platform: str) -> None:
    """Platform and count rules plus each record's alt text (offline)."""
    if not media:
        return
    if platform not in MEDIA_PLATFORMS:
        raise ValidationError(f"image URL attachments are only supported for Instagram drafts, not {platform}")
    if any(not isinstance(item, dict) or item.get("source") != "url" for item in media):
        raise ValidationError(f"{platform} attachments must be owner-hosted image URLs (--url)")
    if len(media) > MAX_ATTACHMENTS:
        raise ValidationError(f"{len(media)} images; the Instagram adapter publishes exactly one image per post")
    for item in media:
        validate_alt(item["alt"])
        if item["bytes"] > MAX_IMAGE_BYTES:
            raise ValidationError(f"image has {item['bytes']} bytes; the limit is {MAX_IMAGE_BYTES}")


def url_record_ok(item: dict) -> bool:
    """The #20 rules for a stored ``source: "url"`` record: a canonical owner-hosted URL, a JPEG
    and no focal point (Instagram has none). Used by ``attachments.decode_media``."""
    try:
        url_ok = validate_image_url(item["url"]) == item["url"]
    except ValidationError:
        url_ok = False
    return url_ok and item["mime"] == "image/jpeg" and item["focus"] is None


def fetch_approved_bytes(item: dict, *, fetch: Callable[[str], bytes] = download_image) -> bytes:
    """Download the image again; return the bytes only if they still match the approved record.

    A download failure raises its ``ImageFetchError`` (nothing about the image changed as far
    as ReachWeaver knows); different bytes, type or dimensions raise ``MEDIA_CHANGED``.
    """
    if item.get("source") != "url":
        raise ValidationError(MEDIA_CHANGED)
    data = fetch(item["url"])
    try:
        mime, width, height = inspect_image(data)
    except ValidationError:
        raise ValidationError(MEDIA_CHANGED) from None
    if (not hmac.compare_digest(hashlib.sha256(data).hexdigest(), item["sha256"])
            or (mime, width, height, len(data)) != (item["mime"], item["width"], item["height"], item["bytes"])):
        raise ValidationError(MEDIA_CHANGED)
    return data


def describe(media: list[dict]) -> list[dict]:
    """Offline review view for ``show``: the record only (no download)."""
    from .attachments import describe as describe_shared
    return describe_shared(media)
