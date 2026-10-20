"""Campaign brief -> vertical-video storyboard scaffold, plus crop and safe-area checks (issue #18 slice).

``build_storyboard`` turns ONE campaign file (the campaign contract, or a ``signal-brief``
scaffold) into a deterministic, offline media scaffold:

- a vertical-video storyboard (JSON and Markdown) with timed scenes, one hook, one beat
  per cited evidence record (up to three) and one CTA scene;
- an SRT subtitle skeleton with valid, monotonic timing (and a WebVTT copy when a target
  only accepts WebVTT);
- a media manifest in the ``media-check`` format with an image draft per image target and
  the vertical video, whose alt text, captions and rights are unresolved placeholders;
- an image spec per platform and the vertical-video spec, carrying the sourced rows of
  ``media.CONSTRAINTS``;
- a crop and UI-overlay safe-area check of every text and focal box.

Nothing here writes copy, alt text or subtitles, generates or edits media, or calls any
model, provider or network. Every creative field is a ``{{write: ...}}`` placeholder that
``media-check`` and ``validate_post`` block, and ``rights_confirmed`` starts ``false``.

Built-in safe zones are ``sourced`` from first-party PAID-AD creative specs (accessed
2026-10-09): TikTok's In-Feed ads safe-zone template, Meta's Instagram Reels ad specs and
Google Ads' vertical-video safe-zone image. None of the three platforms publishes an organic
overlay figure in those sources, so every built-in zone carries ``placement: "ads"`` and
the check warns that organic overlays may differ. An owner can supply other numbers with
``--safe-zones FILE``; entries without a public source and date stay ``unverified`` and are
surfaced as warnings.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .briefs import _fence
from .campaigns import SLUG, _sensitive_keys, public_url, read_json
from .media import BCP47, CONSTRAINTS, validate_media_manifest, validate_subtitle_text
from .inputs import loads_text
from .policy import PLACEHOLDER, ValidationError
from .privacy import write_private_file
from .report_format import markdown_cell as _cell

SEND_PATH = None  # Asserted in tests: a storyboard can never stage, approve, schedule or send.
STATUS = "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT"
NOTE = ("SCAFFOLD ONLY. No copy, alt text, subtitle text, image or video was generated, and nothing was "
        "staged, approved, scheduled or published. Every {{write: ...}} placeholder and rights_confirmed=false "
        "blocks media-check until a person resolves it.")
MIN_DURATION_S = 10
MAX_DURATION_S = 180
DEFAULT_DURATION_S = 30
HOOK_MS = 3000
CTA_MS = 4000
MAX_BEATS = 3
FRAME_WIDTH = 1080
MAX_STORYBOARD_BYTES = 5_000_000
EPS = 1e-9
DEFAULT_VIDEO_TARGETS = ("youtube_shorts", "tiktok", "instagram")
DEFAULT_IMAGE_TARGETS = ("linkedin",)
FITS = ("cover", "contain")
BOX_KINDS = ("text", "focal")
VERIFIED_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXPECTED_SCAFFOLD_ERRORS = ("not found:", "rights_confirmed must be true", "unresolved placeholder")
MANIFEST_FILE = "media-manifest.json"
STORYBOARD_JSON = "storyboard.json"
STORYBOARD_MD = "storyboard.md"
IMAGE_EXTENSION = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "gif": ".gif"}
IMAGE_PREFERENCE = ("png", "jpeg", "webp", "gif")

_UNVERIFIED_NOTE = ("UNVERIFIED design-template convention, not checked against official platform documentation "
                    "by ReachWeaver. Overlays differ by app version, device, language and organic versus ad "
                    "placement. Preview in the native app, or pass --safe-zones FILE with sourced numbers.")
SAFE_ZONES_ACCESSED = "2026-10-09"
_TIKTOK_IN_FEED = "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads"
_TIKTOK_IN_FEED_TEMPLATE = ("https://lf-tt4b.tiktokcdn.com/obj/i18nblog/tt4b_cms/en-US/"
                            "5jqet0ab9qci-10e7f5Vig4uhAscNP8XPB0.zip")
_META_REELS_ADS = "https://www.facebook.com/business/ads-guide/update/video/instagram-reels"
_GOOGLE_VERTICAL_ADS = "https://support.google.com/google-ads/answer/9128498"


def _zone(top: float, bottom: float, left: float, right: float) -> dict[str, Any]:
    """An unverified zone: used for owner entries without a public source and date."""
    return {"viewport_aspect": "9:16", "top": top, "bottom": bottom, "left": left, "right": right,
            "status": "unverified", "source": None, "verified_date": None, "note": _UNVERIFIED_NOTE}


def _fraction(stated: float, whole: float) -> float:
    """``stated / whole`` rounded UP to 6 decimals, so the zone is never smaller than the source."""
    return math.ceil(round(stated / whole, 9) * 1_000_000) / 1_000_000


def _sourced_zone(*, frame: tuple[int, int] | None, stated: dict[str, Any], right: float,
                  source: str, document: str, evidence: str, note: str,
                  source_file: str | None = None) -> dict[str, Any]:
    """A zone whose numbers a first-party page or file states; fractions derive from ``stated``."""
    if frame is None:  # stated as percentages of the asset
        top, bottom, left = (stated[key] / 100 for key in ("top", "bottom", "left"))
    else:
        width, height = frame
        top, bottom, left = (_fraction(stated["top"], height), _fraction(stated["bottom"], height),
                             _fraction(stated["left"], width))
    zone: dict[str, Any] = {
        "viewport_aspect": "9:16", "top": top, "bottom": bottom, "left": left, "right": right,
        "status": "sourced", "source": source, "verified_date": SAFE_ZONES_ACCESSED, "placement": "ads",
        "source_document": document, "stated": stated, "evidence": evidence, "note": note}
    if source_file:
        zone["source_file"] = source_file
    return zone


# Fractions of the full-screen 9:16 player covered by UI (username, caption, buttons, progress
# bar), from first-party PAID-AD creative specs accessed 2026-10-09. Organic overlays are not
# stated by these sources and may differ; check_safe_area warns about that for every target.
SAFE_ZONES: dict[str, dict[str, Any]] = {
    "tiktok": _sourced_zone(
        frame=(720, 1280),
        stated={"unit": "px", "frame": "720x1280", "top": 160, "bottom": 440, "left": 80, "right": 80,
                "right_action_column": {"width": 120, "height_from_bottom": 720}},
        right=_fraction(80 + 120, 720),
        source=_TIKTOK_IN_FEED, source_file=_TIKTOK_IN_FEED_TEMPLATE,
        document="TikTok Ads Manager help: TikTok Auction In-Feed Ads (page 'Last updated: June 2026'), "
                 "downloadable 'In-Feed-Standard Version LTR' safe-zone template",
        evidence="The help page states the safe zone depends on dimension, ad caption length and add-ons, and "
                 "links the template; it states no numbers in its text. The template image (Feed.png, "
                 "2880x5120 drawn on a labelled 720x1280 base) labels 160 px top, 440 px bottom, 80 px each "
                 "side and a 120 px action column above the right margin over the bottom 720 px.",
        note="Paid In-Feed ad spec, not organic. The right margin applies the 80+120 px action column to the "
             "full height, because a zone here is a rectangle; that is stricter than the template above the "
             "column. Longer ad captions, anchors and Arabic (RTL) placements use other templates."),
    "instagram": _sourced_zone(
        frame=None, stated={"unit": "percent", "top": 14, "bottom": 35, "left": 6, "right": 6},
        right=0.06, source=_META_REELS_ADS,
        document="Meta Ads Guide: Awareness Video Ad Specs on Instagram Reels",
        evidence="'Consider leaving at least 14% of the top, 35% of the bottom, and 6% on each side of your "
                 "asset free from text, logos, ...'",
        note="Paid Reels ad spec ('at least'), not organic. Meta's ad guide also recommends 1440x2560."),
    "youtube_shorts": _sourced_zone(
        frame=(1080, 1920),
        stated={"unit": "px", "frame": "1080x1920", "top": 288, "bottom": 672, "left": 48, "right": 192},
        right=_fraction(192, 1080),
        source=_GOOGLE_VERTICAL_ADS,
        document="Google Ads Help: Use square and vertical video to engage mobile customers, section "
                 "'Safe zones for vertical video ads on YouTube'",
        evidence="The page's SVG image 'YouTube | Vertical Video Ads Safe Zone' (alt text: 'Resolution "
                 "1080x1920'; 'Dimensions in pixels') labels 288 top, 672 bottom, 48 left and 192 right; the "
                 "page text states no numbers.",
        note="Paid vertical video ads across in-stream, in-feed and Shorts inventory, not organic Shorts."),
}

# Suggested image canvases. "within_sourced_limits" means the size satisfies every sourced
# row in media.CONSTRAINTS; "unverified_convention" means the cited docs set no aspect ratio.
IMAGE_CANVAS: dict[str, dict[str, Any]] = {
    "linkedin": {"width": 1200, "height": 627, "basis": "unverified_convention",
                 "note": "The cited LinkedIn Images API states no aspect ratio; it accepts fewer than "
                         "36,152,320 pixels."},
    "instagram": {"width": 1080, "height": 1350, "basis": "within_sourced_limits",
                  "note": "4:5 is inside the sourced 4:5..1.91:1 range and 1080 px is inside 320..1440 px."},
    "bluesky": {"width": 1200, "height": 675, "basis": "unverified_convention",
                "note": "The lexicon states no aspect ratio; the sourced limit is 2,000,000 bytes."},
    "mastodon": {"width": 1200, "height": 675, "basis": "unverified_convention",
                 "note": "No aspect ratio documented; size limits are instance-configurable."},
    "tiktok": {"width": 1080, "height": 1920, "basis": "unverified_convention",
               "note": "The media transfer guide's 'up to 1080p' is ambiguous; no aspect ratio is stated."},
}

GENERATION: dict[str, Any] = {
    "enabled": False,
    "policy": ("ReachWeaver generates no image, video, audio, subtitle or text and calls no generation API. "
               "Producing media is an opt-in step that you run yourself with a tool you choose."),
    "options": [
        {"route": "manual_or_local",
         "examples": ["a screen recording of the real product", "an image editor",
                      "ffmpeg for local trimming, burned-in captions and faststart re-muxing",
                      "a local speech-to-text model for a first subtitle draft"],
         "cost": ("No ReachWeaver or provider charge. The costs are your own hardware, electricity and time. "
                  "Local model output still needs a human to check every word.")},
        {"route": "paid_provider",
         "examples": ["a hosted image, video, voice or transcription API that you sign up for yourself"],
         "cost": ("Billed by that provider to your own account at its current published price. ReachWeaver has "
                  "no integration, holds no key and pays nothing. Prices are not recorded here because they "
                  "change; read the provider's pricing page and terms (output ownership, likeness and voice "
                  "rules) before use.")},
    ],
    "provenance": ("Record any tool in the media manifest: provenance.created_by = 'tool' and provenance.tool = "
                   "name and version. media-check then asks for disclosure and likeness review."),
}


def _placeholder(text: str) -> str:
    return "{{write: " + text.replace("{", "(").replace("}", ")") + "}}"


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc)


# --------------------------------------------------------------------- geometry

def parse_aspect(text: object) -> tuple[int, int]:
    """``"9:16"`` -> ``(9, 16)``; positive integers up to 10000 on each side."""
    match = re.fullmatch(r"\s*(\d{1,5})\s*:\s*(\d{1,5})\s*", text) if isinstance(text, str) else None
    if not match or not 0 < int(match.group(1)) <= 10000 or not 0 < int(match.group(2)) <= 10000:
        raise ValidationError("--aspect must be W:H with positive integers, for example 9:16, 4:5 or 1:1")
    return int(match.group(1)), int(match.group(2))


def frame_size(aspect: tuple[int, int]) -> tuple[int, int]:
    """Pixel frame for an aspect ratio at a 1080 px width (height rounded to an even number)."""
    width, height = aspect
    return FRAME_WIDTH, max(2, 2 * round(FRAME_WIDTH * height / width / 2))


def _rect(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    return {"x0": round(x0, 6), "y0": round(y0, 6), "x1": round(x1, 6), "y1": round(y1, 6)}


def viewport_to_frame(frame_aspect: float, view_aspect: float, fit: str,
                      rect: tuple[float, float, float, float]) -> dict[str, float]:
    """Map a viewport-normalized rect into frame-normalized coordinates, clipped to the frame.

    The viewport is ``view_aspect`` wide and 1 high; the frame is ``frame_aspect`` wide and 1
    high before scaling. ``cover`` scales the frame to fill the viewport (centered crop);
    ``contain`` scales it to fit inside (letterbox). Aspects are width / height.
    """
    if fit not in FITS:
        raise ValidationError("fit must be cover or contain")
    ratio = view_aspect / frame_aspect
    scale = max(ratio, 1.0) if fit == "cover" else min(ratio, 1.0)
    shown_w, shown_h = frame_aspect * scale, scale
    off_x, off_y = (view_aspect - shown_w) / 2, (1 - shown_h) / 2
    u0, v0, u1, v1 = rect
    x0 = (u0 * view_aspect - off_x) / shown_w
    x1 = (u1 * view_aspect - off_x) / shown_w
    y0, y1 = (v0 - off_y) / shown_h, (v1 - off_y) / shown_h
    return _rect(max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1))


def target_areas(frame_aspect: float, zone: dict[str, Any], fit: str) -> tuple[dict[str, float], dict[str, float]]:
    """``(visible_rect, safe_rect)`` of the frame for one target's viewport and overlay zone."""
    view_w, view_h = parse_aspect(zone["viewport_aspect"])
    view_aspect = view_w / view_h
    visible = viewport_to_frame(frame_aspect, view_aspect, fit, (0.0, 0.0, 1.0, 1.0))
    safe = viewport_to_frame(frame_aspect, view_aspect, fit,
                             (zone["left"], zone["top"], 1 - zone["right"], 1 - zone["bottom"]))
    return visible, safe


def _outside(box: dict[str, float], rect: dict[str, float]) -> dict[str, float]:
    over = {"left": rect["x0"] - box["x"], "top": rect["y0"] - box["y"],
            "right": box["x"] + box["w"] - rect["x1"], "bottom": box["y"] + box["h"] - rect["y1"]}
    return {edge: round(value, 6) for edge, value in over.items() if value > EPS}


def _validate_box(box: object, label: str) -> dict[str, Any]:
    if not isinstance(box, dict):
        raise ValidationError(f"{label} must be an object with id, kind, x, y, w, h")
    kind = box.get("kind")
    if kind not in BOX_KINDS:
        raise ValidationError(f"{label}.kind must be text or focal")
    values: dict[str, Any] = {"id": str(box.get("id") or label), "kind": kind}
    for key in ("x", "y", "w", "h"):
        value = box.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError(f"{label}.{key} must be a number (a fraction of the frame, 0..1)")
        values[key] = float(value)
    if values["w"] <= 0 or values["h"] <= 0:
        raise ValidationError(f"{label}: w and h must be greater than 0")
    if (values["x"] < -EPS or values["y"] < -EPS or values["x"] + values["w"] > 1 + EPS
            or values["y"] + values["h"] > 1 + EPS):
        raise ValidationError(f"{label}: box must lie inside the frame (x, y, w, h are fractions of 0..1)")
    return values


def check_safe_area(boxes: list[dict[str, Any]], aspect: tuple[int, int], targets: list[str],
                    zones: dict[str, dict[str, Any]], fit: str = "cover") -> dict[str, Any]:
    """Flag text or focal boxes that are cropped away or covered by a target's UI overlay.

    Boxes use frame-normalized coordinates (x, y, w, h). A text box outside a safe area is an
    error; a focal box is a warning. Targets without zone data are skipped with a warning, and
    every unverified or ads-only zone adds a warning so its provenance is never silent.
    """
    width, height = frame_size(aspect)
    frame_aspect = aspect[0] / aspect[1]
    errors: list[str] = []
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    per_target: dict[str, Any] = {}
    combined: list[float] | None = None
    checked = [_validate_box(box, f"box[{n}]") for n, box in enumerate(boxes)]
    for target in targets:
        zone = zones.get(target)
        if zone is None:
            warnings.append(f"{target}: no UI-overlay safe-zone data; crop and overlay checks skipped. "
                            "Preview the upload in the native app")
            per_target[target] = {"status": "no_zone_data"}
            continue
        if zone.get("status") == "unverified":
            warnings.append(f"{target}: UI-overlay safe zone is UNVERIFIED (no first-party source and date); "
                            "treat this check as a rough guide and preview the upload in the native app")
        elif zone.get("placement") == "ads":
            warnings.append(f"{target}: UI-overlay safe zone comes from a paid-ad creative spec "
                            f"[source: {zone.get('source')}, accessed {zone.get('verified_date')}]; organic "
                            "overlays are not stated there and may differ. Preview the upload in the native app")
        visible, safe = target_areas(frame_aspect, zone, fit)
        per_target[target] = {"visible_rect": visible, "safe_rect": safe, "zone": dict(zone), "fit": fit}
        rect = [safe["x0"], safe["y0"], safe["x1"], safe["y1"]]
        combined = rect if combined is None else [max(combined[0], rect[0]), max(combined[1], rect[1]),
                                                  min(combined[2], rect[2]), min(combined[3], rect[3])]
        for box in checked:
            cropped = _outside(box, visible)
            covered = _outside(box, safe)
            if not covered:
                continue
            reason = "cropped" if cropped else "under_ui_overlay"
            edges = cropped or covered
            pixels = {edge: round(value * (width if edge in ("left", "right") else height))
                      for edge, value in edges.items()}
            severity = "error" if box["kind"] == "text" else "warning"
            finding = {"target": target, "box": box["id"], "kind": box["kind"], "reason": reason,
                       "severity": severity, "outside_by": edges, "outside_by_px": pixels,
                       "zone_status": zone["status"]}
            findings.append(finding)
            what = "cropped away by a centered fill" if reason == "cropped" else "under the app's UI overlay"
            sides = ", ".join(f"{edge} {pixels[edge]}px" for edge in edges)
            label = "unverified safe zone" if zone["status"] == "unverified" else f"zone source {zone['source']}"
            (errors if severity == "error" else warnings).append(
                f"{target}: {box['kind']} box {box['id']!r} is {what} ({sides} outside; {label})")
    combined_rect = None
    if combined is not None:
        combined_rect = _rect(*combined) if combined[0] < combined[2] and combined[1] < combined[3] else None
        if combined_rect is None:
            errors.append("the selected targets leave no common safe area at this aspect ratio")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "findings": findings,
            "frame": {"aspect": f"{aspect[0]}:{aspect[1]}", "width": width, "height": height},
            "fit": fit, "targets": per_target, "combined_safe_rect": combined_rect}


def load_safe_zones(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """Built-in unverified zones, overridden per target by an owner's sourced JSON file."""
    zones = {name: dict(zone) for name, zone in SAFE_ZONES.items()}
    if path is None:
        return zones
    data = read_json(path)
    issues = _sensitive_keys(data, "safe_zones")
    if issues:
        raise ValidationError("; ".join(issues))
    for target, raw in data.items():
        label = f"safe zones {target!r}"
        if target not in CONSTRAINTS or "video" not in CONSTRAINTS[target]:
            raise ValidationError(f"{label}: unknown video target; known: "
                                  f"{', '.join(sorted(t for t in CONSTRAINTS if 'video' in CONSTRAINTS[t]))}")
        if not isinstance(raw, dict):
            raise ValidationError(f"{label} must be an object with top, bottom, left, right")
        zone: dict[str, Any] = {"viewport_aspect": raw.get("viewport_aspect", "9:16")}
        parse_aspect(zone["viewport_aspect"])
        for key in ("top", "bottom", "left", "right"):
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 0.5:
                raise ValidationError(f"{label}.{key} must be a fraction of the viewport in 0..0.5")
            zone[key] = float(value)
        source, verified = raw.get("source"), raw.get("verified_date")
        sourced = public_url(source) and isinstance(verified, str) and bool(VERIFIED_DATE.fullmatch(verified))
        zone.update({"status": "owner_sourced" if sourced else "unverified",
                     "source": source if sourced else None, "verified_date": verified if sourced else None,
                     "note": ("Owner-supplied numbers with a public source URL and verification date."
                              if sourced else _UNVERIFIED_NOTE)})
        zones[target] = zone
    return zones


# --------------------------------------------------------------------- scaffold

def _timestamp(ms: int, sep: str) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def _campaign_inputs(campaign: object) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    if not isinstance(campaign, dict):
        raise ValidationError("Campaign file must be a JSON object")
    issues = _sensitive_keys(campaign, "campaign")
    if issues:
        raise ValidationError("; ".join(issues))
    campaign_id = campaign.get("campaign_id")
    if not isinstance(campaign_id, str) or not SLUG.fullmatch(campaign_id):
        raise ValidationError("campaign_id must be a lowercase slug (campaign contract or signal-brief output)")
    raw_evidence = campaign.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ValidationError("campaign evidence must be a list")
    evidence = []
    for item in raw_evidence:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            evidence.append({"id": item["id"], "kind": str(item.get("kind") or ""),
                             "url": item["url"] if public_url(item.get("url")) else "",
                             "claim": item["claim"] if isinstance(item.get("claim"), str) else ""})
    context = {key: campaign.get(key) for key in ("audience", "objective", "brand_id")
               if isinstance(campaign.get(key), str)}
    return campaign_id, evidence, context


def _scenes(duration_ms: int, evidence: list[dict[str, str]]) -> list[dict[str, Any]]:
    beats = max(1, min(MAX_BEATS, len(evidence)))
    middle = duration_ms - HOOK_MS - CTA_MS
    spans = [("hook", 0, HOOK_MS)]
    start = HOOK_MS
    for n in range(beats):
        length = middle // beats + (middle % beats if n == beats - 1 else 0)
        spans.append(("beat", start, start + length))
        start += length
    spans.append(("cta", start, duration_ms))
    scenes = []
    for n, (role, begin, end) in enumerate(spans, 1):
        sid = f"scene-{n}"
        cited = [evidence[n - 2]["id"]] if role == "beat" and len(evidence) >= n - 1 and evidence else []
        if role == "hook":
            guide = "the visible outcome in the first 2 seconds, shown with the real product"
        elif role == "cta":
            guide = "the one action a viewer should take; match the campaign CTA"
        else:
            guide = (f"one point grounded in evidence {cited[0]!r}" if cited else
                     "one point; cite evidence in the campaign before staging")
        scenes.append({
            "id": sid, "role": role, "start_ms": begin, "end_ms": end,
            "start": _timestamp(begin, ","), "end": _timestamp(end, ","),
            "evidence_ids": cited,
            "on_screen_text": _placeholder(f"short on-screen text for {sid} ({role}): {guide}"),
            "voiceover": _placeholder(f"voiceover for {sid}; the subtitle cue must match it word for word"),
            "visual": _placeholder(f"shot for {sid}: real footage or screen capture only; no fabricated UI, "
                                   "results or people"),
        })
    return scenes


def _default_boxes(scene_id: str, safe: dict[str, float] | None) -> list[dict[str, Any]]:
    """Text and focal boxes placed inside the combined safe rect (4-decimal, rounded inward)."""
    x0, y0, x1, y1 = (safe["x0"], safe["y0"], safe["x1"], safe["y1"]) if safe else (0.05, 0.05, 0.95, 0.95)
    def up(value: float) -> float:
        return math.ceil(value * 10_000) / 10_000

    def down(value: float) -> float:
        return math.floor(value * 10_000) / 10_000

    width, height = x1 - x0, y1 - y0
    tx, ty = up(x0 + 0.1 * width), up(y0 + 0.05 * height)
    text = {"id": f"{scene_id}-text", "kind": "text", "x": tx, "y": ty,
            "w": down(x0 + 0.9 * width - tx), "h": down(y0 + 0.25 * height - ty)}
    fx, fy = up(x0 + 0.2 * width), up(y0 + 0.35 * height)
    focal = {"id": f"{scene_id}-focal", "kind": "focal", "x": fx, "y": fy,
             "w": down(x0 + 0.8 * width - fx), "h": down(y0 + 0.9 * height - fy)}
    return [text, focal]


def render_subtitles(scenes: list[dict[str, Any]], fmt: str) -> str:
    """Subtitle skeleton with one cue per scene: valid timing, placeholder text."""
    sep = "," if fmt == "srt" else "."
    blocks = [] if fmt == "srt" else ["WEBVTT"]
    for n, scene in enumerate(scenes, 1):
        timing = f"{_timestamp(scene['start_ms'], sep)} --> {_timestamp(scene['end_ms'], sep)}"
        text = _placeholder(f"subtitle for {scene['id']} ({scene['role']}); match the voiceover word for word")
        blocks.append(f"{n}\n{timing}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _image_container(target: str) -> str:
    allowed = CONSTRAINTS[target]["image"]["allowed_containers"]["value"] or []
    return next((c for c in IMAGE_PREFERENCE if c in allowed), "png")


def _alt_limit(target: str) -> str:
    row = CONSTRAINTS[target]["image"].get("alt_text_max_chars")
    if row and row["value"] is not None:
        return f"; {target} limit {row['value']} characters ({row['note']})"
    return f"; {target} documents no alt-text limit in the cited source"


def _targets(values: object, kind: str, option: str) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValidationError(f"{option} needs at least one target")
    known = sorted(t for t in CONSTRAINTS if kind in CONSTRAINTS[t])
    out: list[str] = []
    for value in values:
        if value not in known:
            raise ValidationError(f"{option}: unknown {kind} target {value!r}; known: {', '.join(known)}")
        if value not in out:
            out.append(value)
    return out


def build_storyboard(campaign: dict, *, duration_s: int = DEFAULT_DURATION_S, aspect: str = "9:16",
                     video_targets: list[str] | tuple[str, ...] = DEFAULT_VIDEO_TARGETS,
                     image_targets: list[str] | tuple[str, ...] = DEFAULT_IMAGE_TARGETS,
                     locale: str = "en", zones: dict[str, dict[str, Any]] | None = None, fit: str = "cover",
                     now: datetime | None = None, campaign_file: str = "CAMPAIGN.json",
                     out_dir: str | Path = ".") -> dict[str, Any]:
    """Return ``{storyboard, manifest, subtitles, markdown, validation}``; writes nothing."""
    if isinstance(duration_s, bool) or not isinstance(duration_s, int) or not MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
        raise ValidationError(f"--duration must be {MIN_DURATION_S}..{MAX_DURATION_S} seconds")
    if not isinstance(locale, str) or not BCP47.fullmatch(locale):
        raise ValidationError("--locale must be a BCP-47 tag such as en, en-US, it or fa-IR")
    if fit not in FITS:
        raise ValidationError("--fit must be cover or contain")
    ratio = parse_aspect(aspect)
    videos = _targets(list(video_targets), "video", "--video-targets")
    images = _targets(list(image_targets), "image", "--image-targets")
    zones = zones if zones is not None else load_safe_zones(None)
    campaign_id, evidence, context = _campaign_inputs(campaign)
    canonical = json.dumps(campaign, sort_keys=True, ensure_ascii=False).encode("utf-8")
    moment = _now(now)
    scenes = _scenes(duration_s * 1000, evidence)
    layout = check_safe_area([], ratio, videos, zones, fit)
    if layout["errors"]:
        raise ValidationError("; ".join(layout["errors"]))
    for scene in scenes:
        scene["boxes"] = _default_boxes(scene["id"], layout["combined_safe_rect"])
    safe_area = check_safe_area([b for s in scenes for b in s["boxes"]], ratio, videos, zones, fit)
    if not safe_area["valid"]:  # the scaffold's own default layout must pass its own check
        raise ValidationError("Internal check failed: default boxes fall outside the safe area")

    subtitle_formats = ["srt"]
    for target in videos:
        row = CONSTRAINTS[target]["video"].get("subtitle_formats", {})
        allowed = row.get("value") if isinstance(row, dict) else None
        if isinstance(allowed, list) and "srt" not in allowed and "vtt" in allowed and "vtt" not in subtitle_formats:
            subtitle_formats.append("vtt")
    subtitles = {f"subtitles.{locale}.{fmt}": render_subtitles(scenes, fmt) for fmt in subtitle_formats}
    subtitle_reports = {}
    for name, text in subtitles.items():
        errors, warnings, summary = validate_subtitle_text(text, name.rsplit(".", 1)[1])
        if errors or warnings:
            raise ValidationError(f"Internal check failed: subtitle skeleton {name} is malformed: {errors + warnings}")
        subtitle_reports[name] = {"errors": errors, "warnings": warnings, **summary,
                                  "placeholders": len(PLACEHOLDER.findall(text))}

    rights = {"license": "owner", "attribution": "",
              "provenance": {"created_by": "owner", "tool": "", "source_url": ""}, "rights_confirmed": False}
    assets: list[dict[str, Any]] = []
    image_specs: dict[str, Any] = {}
    for target in images:
        container = _image_container(target)
        canvas = IMAGE_CANVAS[target]
        path = f"image-{target}{IMAGE_EXTENSION[container]}"
        image_specs[target] = {"asset_id": f"image-{target}", "path": path, "container": container,
                               "suggested_canvas": {**canvas, "aspect": round(canvas["width"] / canvas["height"], 4)},
                               "constraints": json.loads(json.dumps(CONSTRAINTS[target]["image"]))}
        assets.append({"id": f"image-{target}", "path": path, "kind": "image", "targets": [target],
                       "locale": locale,
                       "alt_text": _placeholder(f"alt text for the {target} image: describe what it shows and any "
                                                "text in it, in your own words" + _alt_limit(target)),
                       "caption": _placeholder(f"{target} post caption grounded in the campaign evidence"),
                       **json.loads(json.dumps(rights))})
    width, height = frame_size(ratio)
    video_spec = {"asset_id": "vertical-video", "path": "vertical-video.mp4", "container": "mp4",
                  "frame": {"width": width, "height": height, "aspect": f"{ratio[0]}:{ratio[1]}"},
                  "duration_s": duration_s,
                  "constraints": {t: json.loads(json.dumps(CONSTRAINTS[t]["video"])) for t in videos}}
    assets.append({"id": "vertical-video", "path": "vertical-video.mp4", "kind": "video", "targets": videos,
                   "locale": locale,
                   "subtitles": [{"path": name, "locale": locale} for name in subtitles],
                   "caption": _placeholder("video post caption grounded in the campaign evidence"),
                   **json.loads(json.dumps(rights))})
    manifest = {"schema_version": 1, "default_locale": locale, "target_locales": [locale], "preserve_terms": [],
                "generated_by": "reachweaver media-storyboard", "status": STATUS, "note": NOTE,
                "assets": assets}
    report = validate_media_manifest(manifest, Path(out_dir))
    unexpected = [e for e in report["errors"] if not any(p in e for p in EXPECTED_SCAFFOLD_ERRORS)]
    if report["valid"] or unexpected:
        raise ValidationError("Internal check failed: the media manifest scaffold "
                              + ("validated" if report["valid"] else f"has structural errors: {unexpected}"))
    storyboard = {
        "schema_version": 1, "type": "VerticalVideoStoryboard", "status": STATUS, "note": NOTE,
        "generated_by": "reachweaver media-storyboard", "generated_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "campaign": {"campaign_id": campaign_id, "file": campaign_file,
                     "sha256": hashlib.sha256(canonical).hexdigest(), **context},
        "locale": locale, "frame": safe_area["frame"], "fit_assumption": fit, "duration_ms": duration_s * 1000,
        "video_targets": videos, "image_targets": images,
        "evidence": evidence, "scenes": scenes,
        "safe_zones": {t: zones[t] for t in videos if t in zones},
        "safe_area": safe_area, "video_spec": video_spec, "image_specs": image_specs,
        "files": {"manifest": MANIFEST_FILE, "subtitles": list(subtitles), "markdown": STORYBOARD_MD},
        "generation": GENERATION,
    }
    validation = {"expected": "BLOCKED", "manifest_errors": report["errors"],
                  "manifest_warnings": report["warnings"], "subtitles": subtitle_reports,
                  "safe_area": {"errors": safe_area["errors"], "warnings": safe_area["warnings"]}}
    result: dict[str, Any] = {"storyboard": storyboard, "manifest": manifest, "subtitles": subtitles,
                              "validation": validation}
    result["markdown"] = render_markdown(result)
    return result


# --------------------------------------------------------------------- re-check

def check_storyboard(path: str | Path, zones: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Re-check an edited storyboard: safe areas, subtitle files and unresolved placeholders.

    Zones come from the built-in table or ``--safe-zones``, never from the edited file, so
    editing zone numbers inside storyboard.json cannot make a layout pass.
    """
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(MAX_STORYBOARD_BYTES + 1)
    if len(raw) > MAX_STORYBOARD_BYTES:
        raise ValidationError(f"Storyboard is larger than {MAX_STORYBOARD_BYTES} bytes")
    try:
        data = loads_text(raw.decode("utf-8-sig"), "Storyboard")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Storyboard must be UTF-8 JSON written by media-storyboard") from exc
    if not isinstance(data, dict) or data.get("type") != "VerticalVideoStoryboard" or not isinstance(data.get("scenes"), list):
        raise ValidationError("Not a media-storyboard storyboard.json (type VerticalVideoStoryboard, scenes[])")
    zones = zones if zones is not None else load_safe_zones(None)
    frame: dict[str, Any] = data["frame"] if isinstance(data.get("frame"), dict) else {}
    ratio = parse_aspect(frame.get("aspect"))
    fit = data.get("fit_assumption", "cover")
    videos = _targets(data.get("video_targets"), "video", "video_targets")
    errors: list[str] = []
    boxes: list[Any] = []
    for n, scene in enumerate(data["scenes"]):
        if not isinstance(scene, dict):
            errors.append(f"scenes[{n}] must be an object")
            continue
        for key in ("on_screen_text", "voiceover", "visual"):
            if isinstance(scene.get(key), str) and PLACEHOLDER.search(scene[key]):
                errors.append(f"scenes[{n}].{key} contains an unresolved placeholder")
        if not isinstance(scene.get("boxes", []), list):
            raise ValidationError(f"scenes[{n}].boxes must be a list of boxes")
        boxes.extend(scene.get("boxes", []))
    safe_area = check_safe_area(boxes, ratio, videos, zones, fit)
    errors.extend(safe_area["errors"])
    subtitles: dict[str, Any] = {}
    files: dict[str, Any] = data["files"] if isinstance(data.get("files"), dict) else {}
    names = files.get("subtitles", [])
    if not isinstance(names, list):
        raise ValidationError("files.subtitles must be a list of subtitle file names")
    for name in names:
        sub = (path.parent / str(name)).resolve()
        if path.parent.resolve() not in sub.parents or sub.suffix not in (".srt", ".vtt") or not sub.is_file():
            errors.append(f"subtitle {name}: missing, outside the storyboard folder, or not .srt/.vtt")
            continue
        text = sub.read_text(encoding="utf-8")
        serr, swarn, summary = validate_subtitle_text(text, sub.suffix[1:])
        if PLACEHOLDER.search(text):
            serr.append("contains an unresolved placeholder")
        subtitles[str(name)] = {"errors": serr, "warnings": swarn, **summary}
        errors.extend(f"subtitle {name}: {item}" for item in serr)
    return {"valid": not errors, "errors": errors, "warnings": safe_area["warnings"],
            "safe_area": safe_area, "subtitles": subtitles,
            "next": f"Then run: reachweaver media-check {path.parent / MANIFEST_FILE}"}


# --------------------------------------------------------------------- output

_CODE_SAFE = re.compile(r"[A-Za-z0-9._:/@+-]{1,200}")


def _code(value: object) -> str:
    """A Markdown code span for plain identifiers; anything else is an escaped table cell."""
    text = str(value)
    return f"`{text}`" if _CODE_SAFE.fullmatch(text) else _cell(text)


def render_markdown(result: dict[str, Any]) -> str:
    """The human scaffold. Every table cell that carries campaign or ``--safe-zones`` data is escaped
    (pipes, backticks, links, emphasis, HTML, control characters) so it cannot add cells or rows."""
    board, validation = result["storyboard"], result["validation"]
    cid = board["campaign"]["campaign_id"]
    frame = board["frame"]
    lines = [f"# Vertical-video storyboard scaffold: `{cid}`", "",
             f"Generated by `reachweaver media-storyboard` at {board['generated_at']}. **Status: BLOCKED until a "
             "person edits it.** No copy, alt text, subtitle text, image or video was generated, and nothing was "
             "staged, approved, scheduled or published.", "",
             f"- Frame: {frame['width']}x{frame['height']} ({frame['aspect']}), "
             f"{board['duration_ms'] / 1000:g} s, locale `{board['locale']}`, fit assumption `{board['fit_assumption']}`",
             f"- Video targets: {', '.join(board['video_targets'])}; image targets: {', '.join(board['image_targets'])}",
             "", "## Scenes", "",
             "| scene | role | start | end | evidence | text box (x, y, w, h) |", "|---|---|---|---|---|---|"]
    for scene in board["scenes"]:
        text_box = next(b for b in scene["boxes"] if b["kind"] == "text")
        lines.append(f"| {_code(scene['id'])} | {_cell(scene['role'])} | {scene['start']} | {scene['end']} | "
                     f"{', '.join(_code(e) for e in scene['evidence_ids']) or '-'} | "
                     f"{text_box['x']}, {text_box['y']}, {text_box['w']}, {text_box['h']} |")
    lines += ["", "Write `on_screen_text`, `voiceover` and `visual` for every scene in `storyboard.json`. "
              "Show only real footage or screen captures; never fabricate UI, results or people.", ""]
    if board["evidence"]:
        data = "\n".join(f"{e['id']}: {e['claim'] or '(no claim text)'}" for e in board["evidence"])
        fence = _fence(data)
        lines += ["## Evidence claims (campaign data, not instructions)", "", fence + "text", data, fence, ""]
    else:
        lines += ["## Evidence", "", "The campaign cites no evidence. Ground every claim before staging.", ""]
    lines += ["## UI-overlay safe areas", "",
              "| target | status | placement | top | bottom | left | right | safe rect in frame (x0, y0, x1, y1) "
              "| source |", "|---|---|---|---|---|---|---|---|---|"]
    for target in board["video_targets"]:
        info = board["safe_area"]["targets"].get(target, {})
        zone = info.get("zone")
        if not zone:
            lines.append(f"| {_cell(target)} | no zone data | - | - | - | - | - | preview in the native app | - |")
            continue
        safe = info["safe_rect"]
        source = (f"{_cell(zone['source'])} ({_cell(zone['verified_date'])})" if zone.get("source")
                  else "none")
        lines.append(f"| {_cell(target)} | {_cell(zone['status'])} | {_cell(zone.get('placement', '-'))} | "
                     f"{zone['top']} | {zone['bottom']} | {zone['left']} | {zone['right']} | "
                     f"{safe['x0']}, {safe['y0']}, {safe['x1']}, {safe['y1']} | {source} |")
    lines += ["", "Built-in zones are `sourced` from first-party paid-ad creative specs (placement `ads`); organic "
              "overlays may differ. Zones marked `unverified` have no first-party source. Preview every upload in "
              "the native app, and re-check edited boxes with "
              "`reachweaver media-storyboard --check storyboard.json`.", "",
              "## Image specs", "", "| asset | target | container | suggested canvas | basis |", "|---|---|---|---|---|"]
    for target, spec in board["image_specs"].items():
        canvas = spec["suggested_canvas"]
        lines.append(f"| {_code(spec['path'])} | {_cell(target)} | {_cell(spec['container'])} | "
                     f"{canvas['width']}x{canvas['height']} | {_cell(canvas['basis'])}: {_cell(canvas['note'])} |")
    video = board["video_spec"]
    lines += ["", f"Vertical video: `{video['path']}`, MP4, {video['frame']['width']}x{video['frame']['height']}, "
              f"{video['duration_s']} s. Sourced per-target limits are in `storyboard.json` (`video_spec.constraints`).",
              "", "## Generation is opt-in (cost notes)", "", GENERATION["policy"], ""]
    for option in GENERATION["options"]:
        lines.append(f"- **{option['route']}** ({'; '.join(option['examples'])}): {option['cost']}")
    lines += ["", GENERATION["provenance"], "", "## Before media-check can pass", "",
              "1. Produce the media files named in `media-manifest.json` yourself, in this folder.",
              "2. Write every alt text in your own words: describe what the image shows and any text in it. "
              "Alt text is never generated.",
              "3. Replace every `{{write: ...}}` in the manifest, `storyboard.json` and the subtitle files. "
              "Keep the cue timing or re-time it to the real video.",
              "4. Confirm rights, consent and likeness permission, then set `rights_confirmed` to `true`. "
              "Record any generation tool in `provenance`.",
              "5. Run `reachweaver media-storyboard --check storyboard.json`, then "
              f"`reachweaver media-check {MANIFEST_FILE}`.",
              "6. A passing check is not consent to publish. Approval stays a separate human step.", "",
              "## Validation at creation (expected: BLOCKED)", ""]
    lines += [f"- ERROR: {e}" for e in validation["manifest_errors"]]
    lines += [f"- WARNING: {w}" for w in validation["manifest_warnings"]]
    return "\n".join(lines) + "\n"


def write_storyboard(result: dict[str, Any], destination: str | Path) -> list[str]:
    """Write the scaffold to a NEW owner-only directory (0700) of NEW 0600 files."""
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ValidationError("Output directory already exists; refusing to overwrite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(destination, 0o700)
    if os.name == "posix":
        os.chmod(destination, 0o700)
    files = {STORYBOARD_JSON: json.dumps(result["storyboard"], indent=2, ensure_ascii=False) + "\n",
             STORYBOARD_MD: result["markdown"],
             MANIFEST_FILE: json.dumps(result["manifest"], indent=2, ensure_ascii=False) + "\n",
             **result["subtitles"]}
    written = []
    for name, text in files.items():
        target = destination / name
        write_private_file(target, text)
        written.append(str(target))
    return written


def run_command(args: Any) -> int:
    """CLI entry for ``media-storyboard``; offline, never opens the database."""
    zones = load_safe_zones(args.safe_zones)
    if args.check is not None:
        if args.campaign is not None or args.out is not None:
            raise ValidationError("--check cannot be combined with --campaign or --out")
        checked = check_storyboard(args.check, zones)
        if args.json:
            print(json.dumps(checked, indent=2, ensure_ascii=False))
        else:
            print("STORYBOARD OK" if checked["valid"] else "STORYBOARD BLOCKED")
            for item in checked["errors"]:
                print("  ERROR:", item)
            for item in checked["warnings"]:
                print("  WARNING:", item)
            print(checked["next"])
        return 0 if checked["valid"] else 2
    if args.campaign is None or args.out is None:
        raise ValidationError("media-storyboard needs --campaign and --out (or --check STORYBOARD.json)")
    result = build_storyboard(read_json(args.campaign), duration_s=args.duration, aspect=args.aspect,
                              video_targets=args.video_targets, image_targets=args.image_targets,
                              locale=args.locale, zones=zones, fit=args.fit, campaign_file=str(args.campaign),
                              out_dir=args.out)
    written = write_storyboard(result, args.out)
    summary = {"status": STATUS, "files": written, "campaign_id": result["storyboard"]["campaign"]["campaign_id"],
               "scenes": len(result["storyboard"]["scenes"]), "validation": result["validation"]}
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"{STATUS}: storyboard for {summary['campaign_id']} with {summary['scenes']} scenes")
        for path in written:
            print(f"  wrote {path}")
        for item in result["validation"]["safe_area"]["warnings"]:
            print("  WARNING:", item)
        print(f"  media-check: BLOCKED as expected ({len(result['validation']['manifest_errors'])} errors) until "
              "media files exist, every placeholder is written and rights are confirmed")
        print("Built-in safe zones come from paid-ad creative specs; organic overlays may differ. "
              "Preview in the native app.")
        print("No media or copy was generated; nothing was staged, approved, scheduled or published.")
    return 0
