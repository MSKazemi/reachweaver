"""The shared image attachment model (issues #13 and #20; owner-approved 2026-10-09, disabled until a live receipt).

One record shape for every image a draft carries, stored as a JSON list in ``posts.media_json``
and bound by ``storage.approval_payload`` under ``reachweaver.storage.approval/media-1``. Each
record names its ``source``:

* ``"local"`` (#13, Mastodon): a local file plus required alt text and an optional focal point,
  validated offline from the file's real header bytes (``media`` parsers, stdlib only). The
  sender re-reads the file once, refuses it unless those exact bytes still hash to the approved
  value, and uploads only those bytes.
* ``"url"`` (#20, Instagram): an owner-hosted HTTPS URL that the platform fetches itself
  (``remote_media`` validates the URL and downloads it read-only to record the hash).

The approval hash covers, per attachment and in order, ``sha256, mime, alt, focus`` plus
``url`` for a hosted image (the URL is what is sent), and, when the owner recorded it (#48), the
``rights`` record (license, attribution, provenance, alt translations) under ``approval/media-2``. The local path is not bound (it is never
sent; the bytes are). Which source a platform accepts is fixed in ``MEDIA_PLATFORMS``; the
platform-specific limits (count, size, format, alt length) stay with each platform. Nothing in
this module touches the network.

Sources (accessed 2026-10-09): POST /api/v2/media ``description`` and ``focus`` fields,
https://docs.joinmastodon.org/methods/media/ ; focal points range -1.0..1.0 with (0,0) the
centre, https://docs.joinmastodon.org/api/guidelines/#focal-points ; limits in
``policy.MASTODON_MEDIA_DEFAULTS``.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any

from .media import (ALT_PLACEHOLDERS, BCP47, CREATORS, GPS_EXIF_WARNING, UNKNOWN, _check_rights, _info, exif_has_gps,
                    _parse_jpeg, _parse_png, _parse_webp)
from .policy import (BIDI_CONTROLS, INVISIBLE_FORMAT_CHARS, MASTODON_IMAGE_MIME, PLACEHOLDER, ValidationError,
                     mastodon_media_limits)

# The one attachment source each platform accepts (#13 local files, #20 owner-hosted URLs).
MEDIA_PLATFORMS = {"mastodon": "local", "instagram": "url"}
SOURCES = ("local", "url")
_COMMON_FIELDS = ("source", "sha256", "mime", "bytes", "width", "height", "alt", "focus")
SOURCE_FIELDS = {"local": ("path", *_COMMON_FIELDS), "url": ("url", *_COMMON_FIELDS)}
FIELDS = SOURCE_FIELDS["local"]  # the local-file record (#13)
BOUND_FIELDS = ("sha256", "mime", "alt", "focus")  # what the approval hash covers, per attachment (+ url)
UNSUPPORTED_PLATFORM = ("image attachments are only supported for Mastodon drafts (local --file) and "
                        "Instagram drafts (owner-hosted --url), not {platform}")
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COORD = re.compile(r"^-?[0-9](?:\.[0-9]{1,6})?$")
# Hard ceiling for any read, independent of owner configuration (the default size cap).
MAX_READ_BYTES = 16_000_000
MEDIA_CHANGED = "approval_media_changed_reapproval_required"
# Optional per-image rights record (#48): license, attribution, provenance and editable alt-text translations.
# Absent for every image attached before it existed; when present it is stored with the record, shown in review
# and bound by the approval hash under ``approval/media-2`` (records without it keep ``approval/media-1``).
RIGHTS_KEYS = frozenset({"rights_confirmed", "license", "attribution", "provenance", "alt_translations"})
MAX_TRANSLATIONS = 12
TRANSLATION_LIMIT = 1500


def parse_focus(value: str | None) -> str | None:
    """``"x,y"`` with each coordinate in -1.0..1.0 (at most 6 decimals); canonical text or None."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValidationError("focus must be text like 0.25,-0.5")
    parts = value.split(",")
    if len(parts) != 2 or not all(_COORD.fullmatch(part.strip()) for part in parts):
        raise ValidationError("focus must be two numbers x,y such as 0.25,-0.5 (each -1.0..1.0)")
    coords = [float(part) for part in parts]
    if not all(math.isfinite(c) and -1.0 <= c <= 1.0 for c in coords):
        raise ValidationError("focus coordinates must each be within -1.0..1.0")
    return ",".join(_coord_text(c) for c in coords)


def _coord_text(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def validate_alt(alt: object, limit: int) -> str:
    """Required, human-written alt text; returned with surrounding whitespace removed."""
    if not isinstance(alt, str) or not alt.strip():
        raise ValidationError("alt text is required for every attachment (describe what the image shows)")
    text = alt.strip()
    if len(text) > limit:  # code points; Ruby String#length, which Mastodon's validation uses
        raise ValidationError(f"alt text has {len(text)} characters; the conservative Mastodon limit is {limit}")
    if text.casefold() in ALT_PLACEHOLDERS or PLACEHOLDER.search(text):
        raise ValidationError("alt text is a placeholder; describe what the image shows")
    if any((ord(ch) < 32 and ch not in "\n\t") or ord(ch) == 127 for ch in text):
        raise ValidationError("alt text includes control characters")
    if any(ch in BIDI_CONTROLS or ch in INVISIBLE_FORMAT_CHARS for ch in text):
        raise ValidationError("alt text includes bidirectional-control or invisible format characters")
    return text


def _sniff(data: bytes) -> tuple[str, Any, Any]:
    """(MIME, width, height) from the bytes themselves; width/height may be ``unknown``."""
    info = _info()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        info["container"] = "png"
        _parse_png(data[:65536], info)
    elif data.startswith(b"\xff\xd8\xff"):
        info["container"] = "jpeg"
        _parse_jpeg(io.BytesIO(data), info)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        info["container"] = "webp"
        _parse_webp(data[:65536], info)
    mime = MASTODON_IMAGE_MIME.get(info["container"])
    if mime is None:
        raise ValidationError("attachment must be a PNG, JPEG or WebP image (checked from the file header, "
                              "not the extension); GIF and video are not supported")
    return mime, info["width"], info["height"]


def _read_regular(path: Path, max_bytes: int) -> bytes:
    """Read a regular file without following a final symlink; at most ``max_bytes``."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValidationError("attachment file is missing, unreadable or a symbolic link") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValidationError("attachment must be a regular file")
        if info.st_size == 0:
            raise ValidationError("attachment file is empty")
        if info.st_size > max_bytes:
            raise ValidationError(f"attachment has {info.st_size} bytes; the conservative limit is {max_bytes}")
        with os.fdopen(os.dup(fd), "rb") as handle:
            data = handle.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ValidationError(f"attachment grew past {max_bytes} bytes while being read")
    return data


def build_attachment(path: str | Path, alt: object, focus: str | None = None, *,
                     account: dict | None = None) -> dict[str, Any]:
    """Validate one local image and return its stored record (see ``FIELDS``)."""
    limits = mastodon_media_limits(account)
    resolved = Path(path).expanduser()
    if resolved.is_symlink():
        raise ValidationError("attachment cannot be a symbolic link")
    resolved = resolved.resolve()
    data = _read_regular(resolved, min(limits["image_size_limit"], MAX_READ_BYTES))
    mime, width, height = _sniff(data)
    if width == UNKNOWN or height == UNKNOWN:
        raise ValidationError("attachment dimensions could not be read from the image header; re-export it")
    return {"source": "local", "path": str(resolved), "sha256": hashlib.sha256(data).hexdigest(), "mime": mime,
            "bytes": len(data), "width": width, "height": height,
            "alt": validate_alt(alt, limits["media_description_limit"]), "focus": parse_focus(focus)}


def gps_warning(path: str | Path, account: dict | None = None) -> str | None:
    """Warning text when the local image carries EXIF GPS data, else None. Advisory only: it never
    changes the record, the approval hash or the bytes that are sent, and never blocks."""
    try:
        data = _read_regular(Path(path).expanduser().resolve(), min(mastodon_media_limits(account)["image_size_limit"],
                                                                  MAX_READ_BYTES))
    except ValidationError:
        return None
    return GPS_EXIF_WARNING if exif_has_gps(data) else None


def check_media_list(media: list[dict], platform: str, account: dict | None = None) -> None:
    """Platform, source and count rules plus each record's alt text against the effective limits.

    Mastodon takes local files (#13 limits, owner pins only lower); Instagram takes one
    owner-hosted URL (#20 limits, ``remote_media.check_media_list``). Anything else is refused.
    """
    if not media:
        return
    source = MEDIA_PLATFORMS.get(platform)
    if source is None:
        raise ValidationError(UNSUPPORTED_PLATFORM.format(platform=platform))
    if any(not isinstance(item, dict) or item.get("source") != source for item in media):
        kind = "local image files (--file)" if source == "local" else "owner-hosted image URLs (--url)"
        raise ValidationError(f"{platform} attachments must be {kind}")
    if source == "url":
        from .remote_media import check_media_list as check_url_media
        check_url_media(media, platform)
        return
    limits = mastodon_media_limits(account)
    if len(media) > limits["max_media_attachments"]:
        raise ValidationError(f"{len(media)} attachments; the conservative Mastodon limit is "
                              f"{limits['max_media_attachments']} images per post")
    for item in media:
        validate_alt(item["alt"], limits["media_description_limit"])
        if item["bytes"] > limits["image_size_limit"]:
            raise ValidationError(f"attachment has {item['bytes']} bytes; the conservative limit is "
                                  f"{limits['image_size_limit']}")


def build_rights(*, license_id: object, created_by: object, rights_confirmed: object, attribution: object = None,
                 tool: object = None, source_url: object = None,
                 alt_translations: dict | None = None) -> tuple[dict, list[str]]:
    """Validate the owner's statement about one image's rights; returns ``(record, warnings)``.

    Reuses the media-manifest rules (``media._check_rights``): the owner confirms rights, consent and likeness;
    the license is an SPDX id, ``owner`` or ``licensed``; provenance names who made it (``owner``, ``tool`` with
    the tool's name, or ``third_party`` with its real license, attribution text and, ideally, a source URL);
    ``CC-BY*`` needs attribution. ``alt_translations`` maps a BCP-47 tag to the owner's own alt text in that
    language: stored, reviewed and bound, but not sent (a platform takes one description). Nothing is fetched.
    """
    record: dict[str, Any] = {"rights_confirmed": rights_confirmed, "license": license_id,
                              "provenance": {"created_by": created_by}}
    if tool is not None:
        record["provenance"]["tool"] = tool
    if source_url is not None:
        record["provenance"]["source_url"] = source_url
    if attribution is not None:
        record["attribution"] = attribution
    errors: list[str] = []
    warnings: list[str] = []
    _check_rights("attachment", record, errors, warnings)
    translations = {} if alt_translations is None else alt_translations
    if not isinstance(translations, dict) or len(translations) > MAX_TRANSLATIONS:
        errors.append(f"alt_translations must map at most {MAX_TRANSLATIONS} language tags to text")
        translations = {}
    clean: dict[str, str] = {}
    for locale, text in translations.items():
        if not isinstance(locale, str) or not BCP47.fullmatch(locale):
            errors.append(f"alt translation language {locale!r} must be a BCP-47 tag such as it, fa or en-GB")
            continue
        try:
            clean[locale] = validate_alt(text, TRANSLATION_LIMIT)
        except ValidationError as exc:
            errors.append(f"alt translation ({locale}): {exc}")
    if errors:
        raise ValidationError("; ".join(errors))
    if clean:
        record["alt_translations"] = dict(sorted(clean.items()))
    return record, warnings


def _rights_ok(rights: object) -> bool:
    """A stored rights record must pass the same checks as a fresh one (strict keys; warnings are fine)."""
    if not isinstance(rights, dict) or set(rights) - RIGHTS_KEYS:
        return False
    provenance = rights.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) - {"created_by", "tool", "source_url"}:
        return False
    errors: list[str] = []
    _check_rights("attachment", rights, errors, [])
    translations = rights.get("alt_translations", {})
    if not isinstance(translations, dict) or len(translations) > MAX_TRANSLATIONS:
        return False
    for locale, text in translations.items():
        try:
            if not isinstance(locale, str) or not BCP47.fullmatch(locale) or validate_alt(text, TRANSLATION_LIMIT) != text:
                return False
        except ValidationError:
            return False
    return not errors and provenance.get("created_by") in CREATORS


def has_rights(media: list[dict]) -> bool:
    return any(item.get("rights") for item in media)


def encode_media(media: list[dict]) -> str | None:
    """Canonical JSON for ``posts.media_json``; None for no attachments."""
    if not media:
        return None
    return json.dumps([{**{key: item[key] for key in SOURCE_FIELDS[item["source"]]},
                        **({"rights": item["rights"]} if item.get("rights") else {})} for item in media],
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def decode_media(text: object) -> list[dict]:
    """Strictly parse a stored ``media_json`` value; NULL/empty -> []. Malformed raises."""
    if text in (None, ""):
        return []
    try:
        data = json.loads(str(text))
    except ValueError:
        raise ValidationError("media_json is not valid JSON") from None
    if not isinstance(data, list) or not data:
        raise ValidationError("media_json must be a non-empty JSON list")
    out = []
    for item in data:
        source = item.get("source") if isinstance(item, dict) else None
        if source not in SOURCES or set(item) - {"rights"} != set(SOURCE_FIELDS[source]):
            raise ValidationError("media_json entries must hold exactly the attachment fields")
        ok = (isinstance(item["sha256"], str) and _SHA256.fullmatch(item["sha256"])
              and item["mime"] in EXTENSIONS
              and all(type(item[k]) is int and item[k] > 0 for k in ("bytes", "width", "height"))
              and isinstance(item["alt"], str) and item["alt"].strip()
              and (item["focus"] is None or (isinstance(item["focus"], str)
                                             and parse_focus(item["focus"]) == item["focus"])))
        if source == "local":
            ok = ok and isinstance(item["path"], str) and item["path"].startswith("/")
        else:  # #20: a canonical owner-hosted JPEG URL; Instagram has no focal point
            from .remote_media import url_record_ok
            ok = ok and url_record_ok(item)
        if "rights" in item and not _rights_ok(item["rights"]):
            ok = False
        if not ok:
            raise ValidationError("media_json entry has an invalid field")
        out.append(dict(item))
    return out


def bound_view(media: list[dict]) -> list[dict]:
    """The per-attachment fields the approval hash covers, in order: ``sha256, mime, alt, focus``,
    plus ``url`` for a hosted image (#20, the URL is what is sent). The local path is not bound."""
    return [{**{key: item[key] for key in BOUND_FIELDS}, **({"url": item["url"]} if item["source"] == "url" else {}),
             **({"rights": item["rights"]} if item.get("rights") else {})}
            for item in media]


def read_approved_bytes(item: dict) -> bytes:
    """The file's bytes, only if they still hash to the recorded (approved) SHA-256.

    Read exactly once by the sender; the returned bytes are what is uploaded, so a file
    swapped after this read cannot change what is sent. Any difference raises
    ``approval_media_changed_reapproval_required`` (nothing about the content is echoed).
    Local files only: a hosted image (#20) is checked by ``remote_media.fetch_approved_bytes``.
    """
    if item.get("source") != "local":
        raise ValidationError(MEDIA_CHANGED)
    try:
        data = _read_regular(Path(item["path"]), MAX_READ_BYTES)
        changed = hashlib.sha256(data).hexdigest() != item["sha256"] or _sniff(data)[0] != item["mime"]
    except ValidationError:
        changed = True
    if changed:
        raise ValidationError(MEDIA_CHANGED)
    return data


def media_problem(media: list[dict]) -> str | None:
    """None when every local attachment file still matches its record; else ``MEDIA_CHANGED``.

    Offline: a hosted image (#20) cannot be checked without a download, so it is skipped here;
    the live path re-downloads it before the claim and again before the container request.
    """
    for item in media:
        if item.get("source") != "local":
            continue
        try:
            read_approved_bytes(item)
        except ValidationError:
            return MEDIA_CHANGED
    return None


def describe(media: list[dict]) -> list[dict]:
    """Review view for ``show``: everything recorded plus whether a local file still matches.

    A hosted image (#20) is not downloaded here; the view says how to verify it yourself.
    """
    view: list[dict] = []
    for index, item in enumerate(media, 1):
        if item.get("source") == "url":
            view.append({"position": index, **{key: item[key] for key in SOURCE_FIELDS["url"]},
                         **({"rights": item["rights"]} if item.get("rights") else {}),
                         "verify_yourself": "download the URL and compare its SHA-256 with sha256 (e.g. sha256sum)"})
            continue
        try:
            read_approved_bytes(item)
            matches = True
        except ValidationError:
            matches = False
        view.append({"position": index, **{key: item[key] for key in FIELDS},
                     **({"rights": item["rights"]} if item.get("rights") else {}), "file_matches_record": matches})
    return view
