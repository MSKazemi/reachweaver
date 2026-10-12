"""Offline mock media-upload adapter (issue #18).

This is a *simulation*: it never opens a socket, never reads a credential, never writes the
database and never uploads anything. It runs the existing manifest validator
(:func:`reachweaver.media.validate_media_manifest`) for ONE target platform and converts each
finding into a stable, machine-readable error code with a concrete next action, the way a real
upload adapter would reject a bad asset before sending it.

It does not change any publishing, approval or send-gate behaviour and it is not consent to
publish. ``accepted: true`` only means the sourced offline constraints passed. Provenance and
license are validated in memory only; persisting them is a deferred protected item.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .media import CONSTRAINTS, read_manifest, validate_media_manifest
from .policy import ValidationError

# code -> actionable fix. Every code the classifier can emit must have an entry here.
ACTIONS: dict[str, str] = {
    "UNKNOWN_PLATFORM": "Use a platform id that has a verified constraint table (see known_platforms).",
    "MANIFEST_INVALID": "Fix the manifest structure (schema_version 1, assets list, locales) and re-run.",
    "NO_ASSETS_FOR_PLATFORM": "Add the platform id to an asset's targets, or choose another platform.",
    "ASSET_PATH_INVALID": "Use a relative path to an existing file inside the manifest directory.",
    "FILE_HEADER_UNRECOGNIZED": "Re-export the file as PNG, JPEG, GIF, WebP, MP4, MOV, SRT or WebVTT.",
    "KIND_MISMATCH": "Correct the declared kind or the file extension so they match the real file header.",
    "CONTAINER_NOT_ALLOWED": "Re-export the file in a container the platform accepts.",
    "FILE_TOO_LARGE": "Compress or re-encode the file below the platform byte limit.",
    "FILE_TOO_SMALL": "Use a larger or higher-quality file; the platform sets a minimum size.",
    "DURATION_OUT_OF_RANGE": "Trim or extend the video to the platform's duration range.",
    "ASPECT_RATIO_UNSUPPORTED": "Crop or pad the asset to an aspect ratio the platform accepts.",
    "RESOLUTION_OUT_OF_RANGE": "Resize the asset to the platform's resolution range.",
    "CODEC_NOT_ALLOWED": "Re-encode the video or audio with a codec the platform accepts.",
    "FRAME_RATE_OUT_OF_RANGE": "Re-encode at a frame rate inside the platform range.",
    "AUDIO_SPEC_UNSUPPORTED": "Re-encode audio within the platform's sample-rate and channel limits.",
    "ALT_TEXT_MISSING": "Write alt text that describes what the image shows.",
    "ALT_TEXT_PLACEHOLDER": "Replace the placeholder or file-name alt text with a real description.",
    "ALT_TEXT_TOO_LONG": "Shorten the alt text to the platform's character limit.",
    "ALT_TEXT_NOT_SUPPORTED": "Move the description into the caption; this format takes no alt text.",
    "CAPTIONS_MISSING": "Add an SRT or WebVTT sidecar file to the asset's subtitles.",
    "SUBTITLE_FORMAT_UNSUPPORTED": "Provide the subtitle format the platform accepts.",
    "SUBTITLE_TOO_LARGE": "Split or trim the subtitle file below the platform limit.",
    "RIGHTS_NOT_CONFIRMED": "Owner must confirm rights, consent and likeness permission (rights_confirmed true).",
    "LICENSE_INVALID": "Set license to an SPDX id, 'owner' or 'licensed'.",
    "PROVENANCE_INVALID": "Record provenance.created_by (and tool or source where required).",
    "ATTRIBUTION_REQUIRED": "Add the attribution text the license or third-party origin requires.",
    "PLACEHOLDER_UNRESOLVED": "Replace the {{write: ...}} or TODO placeholder with real text.",
    "MEDIA_INVALID": "Read the message, fix the asset or manifest and re-run.",
}

# Ordered (pattern, code); first match wins. Applied to the validator message after its asset label.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(p, re.I), c) for p, c in (
    (r"unresolved placeholder", "PLACEHOLDER_UNRESOLVED"),
    (r"must be a relative file path|must be relative to the manifest|escapes the manifest|not found:", "ASSET_PATH_INVALID"),
    (r"unrecognized file header", "FILE_HEADER_UNRECOGNIZED"),
    (r"declared kind", "KIND_MISMATCH"),
    (r"alt text is required", "ALT_TEXT_MISSING"),
    (r"alt text .*placeholder|alt text must not be the file name", "ALT_TEXT_PLACEHOLDER"),
    (r"alt text \(.*code points", "ALT_TEXT_TOO_LONG"),
    (r"alt text is not accepted", "ALT_TEXT_NOT_SUPPORTED"),
    (r"videos require captions", "CAPTIONS_MISSING"),
    (r"needs a .* subtitle file", "SUBTITLE_FORMAT_UNSUPPORTED"),
    (r"subtitle .* bytes; limit", "SUBTITLE_TOO_LARGE"),
    (r"rights_confirmed", "RIGHTS_NOT_CONFIRMED"),
    (r"requires attribution|needs attribution", "ATTRIBUTION_REQUIRED"),
    (r"license", "LICENSE_INVALID"),
    (r"provenance", "PROVENANCE_INVALID"),
    (r"not in (?:allowed|recommended)", "CONTAINER_NOT_ALLOWED"),
    (r"file is \d+ bytes; limit", "FILE_TOO_LARGE"),
    (r"file is \d+ bytes; minimum", "FILE_TOO_SMALL"),
    (r"duration .*(exceeds|below)", "DURATION_OUT_OF_RANGE"),
    (r"aspect ratio", "ASPECT_RATIO_UNSUPPORTED"),
    (r"width \d+px|dimension (below|above)|pixels exceeds", "RESOLUTION_OUT_OF_RANGE"),
    (r"codec", "CODEC_NOT_ALLOWED"),
    (r"frame rate", "FRAME_RATE_OUT_OF_RANGE"),
    (r"audio (sample rate|has)", "AUDIO_SPEC_UNSUPPORTED"),
))
_LABEL = re.compile(r"^assets\[\d+\](?: \(([^)]*)\))?")


def classify(message: str) -> dict[str, Any]:
    """Map one validator message to ``{code, asset_id, message, action}``."""
    head = _LABEL.match(message)
    if not head:
        return {"code": "MANIFEST_INVALID", "asset_id": None, "message": message, "action": ACTIONS["MANIFEST_INVALID"]}
    body = message[head.end():]
    code = "MEDIA_INVALID"
    for pattern, candidate in _RULES:
        if pattern.search(body):
            code = candidate
            break
    return {"code": code, "asset_id": head.group(1) or None, "message": message, "action": ACTIONS[code]}


def _failure(platform: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"platform": platform, "simulated": True, "uploaded": False, "accepted": False,
            "errors": [{"code": code, "asset_id": None, "message": message, "action": ACTIONS[code], **extra}],
            "warnings": [], "asset_ids": [], "note": NOTE}


NOTE = ("Mock adapter: offline checks against sourced constraints only. Nothing was uploaded, no network or "
        "credential was used, and this is not consent to publish.")


class MockMediaUploadAdapter:
    """Simulated upload preflight for one platform. Never uploads, never touches the network."""

    simulated = True

    def __init__(self, platform: str) -> None:
        self.platform = platform

    def preflight(self, manifest: dict, base_dir: str | Path) -> dict:
        """Validate ``manifest`` for ``self.platform`` and return a structured result."""
        if self.platform not in CONSTRAINTS:
            return _failure(self.platform, "UNKNOWN_PLATFORM",
                            f"no verified constraint table for platform {self.platform!r}",
                            known_platforms=sorted(CONSTRAINTS))
        scoped = manifest
        asset_ids: list[Any] = []
        assets = manifest.get("assets") if isinstance(manifest, dict) else None
        if isinstance(assets, list):
            kept: list[Any] = []
            for asset in assets:
                if not isinstance(asset, dict) or asset.get("kind") == "subtitle":
                    kept.append(asset)
                elif isinstance(asset.get("targets"), list) and self.platform in asset["targets"]:
                    clone = copy.deepcopy(asset)
                    clone["targets"] = [self.platform]  # other platforms' findings are not this adapter's
                    kept.append(clone)
                    asset_ids.append(clone.get("id"))
            if not asset_ids:
                return _failure(self.platform, "NO_ASSETS_FOR_PLATFORM",
                                f"no asset lists {self.platform!r} in targets")
            scoped = dict(manifest, assets=kept)
        report = validate_media_manifest(scoped, base_dir)
        errors = [classify(m) for m in report["errors"]]
        return {"platform": self.platform, "simulated": True, "uploaded": False, "accepted": not errors,
                "errors": errors, "warnings": [classify(m) for m in report["warnings"]],
                "asset_ids": asset_ids, "note": NOTE}

    def preflight_file(self, manifest_path: str | Path) -> dict:
        path = Path(manifest_path)
        try:
            manifest = read_manifest(path)
        except ValidationError as exc:
            return _failure(self.platform, "MANIFEST_INVALID", str(exc))
        return self.preflight(manifest, path.parent)
