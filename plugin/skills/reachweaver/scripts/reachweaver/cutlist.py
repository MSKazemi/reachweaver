"""One master video -> a per-platform cut list (repurposing, issue #18 follow-up; `media-cutlist`).

The owner describes a master video (duration, frame size, focus point) and marks segments with a priority.
For each target platform ``build_cutlist`` says which segments to keep so the cut fits the platform's sourced
duration limits, how to crop to the target aspect around the focus point, which subtitle format and size limit
apply, and a suggested ``ffmpeg`` command. It is a plan for a human:

* no file is opened, probed, cut, encoded, uploaded or sent; the master's numbers are the owner's;
* limits come from ``media.CONSTRAINTS`` (each with its official source and checked date); an owner
  ``max_s`` can only shorten a cut; a limit the source does not establish is reported as ``unverified``;
* the ``ffmpeg`` command uses only documented filters (``trim``/``atrim``, ``setpts``, ``concat``, ``crop``,
  ``scale``; https://ffmpeg.org/ffmpeg-filters.html, accessed 2026-10-17) and is not executed by ReachWeaver
  or its tests: check it with your ffmpeg version and review the result before attaching it to a draft.
"""
from __future__ import annotations

import json
import math
import re
import shlex
from pathlib import Path, PurePath

from .media import CONSTRAINTS
from .policy import ValidationError
from .storyboard import parse_aspect
from .inputs import read_json_file

SCHEMA = "reachweaver.cut-list.v1"
MAX_BYTES = 256 * 1024
MAX_SEGMENTS = 100
MAX_TARGETS = 12
SHORT_SIDE = 1080  # ReachWeaver's output size on the short side (1080x1920, 1920x1080, 1080x1080); a choice, not a rule
VIDEO_PLATFORMS = tuple(sorted(p for p, kinds in CONSTRAINTS.items() if "video" in kinds))
# The aspect used when the owner names none. Shorts must be square or vertical (official range); 9:16 within
# it is ReachWeaver's choice. TikTok's sources do not establish an aspect, so 9:16 there is a hypothesis.
DEFAULT_ASPECT = {"youtube_shorts": ("9:16", "repo_policy"), "tiktok": ("9:16", "hypothesis")}
FFMPEG_DOCS = "https://ffmpeg.org/ffmpeg-filters.html"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SOURCE_KEYS = frozenset({"file", "duration_s", "width", "height", "focus_x", "focus_y", "has_audio"})
SEGMENT_KEYS = frozenset({"id", "start_s", "end_s", "label", "priority"})
TARGET_KEYS = frozenset({"platform", "aspect", "max_s"})
NOTE = ("A plan for you to cut yourself: nothing was opened, cut, encoded or uploaded. Limits are the sourced "
        "values in media.CONSTRAINTS; the ffmpeg command is a suggestion that ReachWeaver does not run. Check the "
        "result with media-check before attaching it to a draft.")


def _number(value: object, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValidationError(f"{label} must be a number from {low:g} to {high:g}")
    return float(value)


def _keys(obj: object, allowed: frozenset, label: str) -> dict:
    if not isinstance(obj, dict):
        raise ValidationError(f"{label} must be an object")
    extra = set(obj) - allowed
    if extra:
        raise ValidationError(f"{label}: unknown field(s) {', '.join(sorted(extra))}")
    return obj


def load_plan(path: str | Path) -> dict:
    """Read and validate a cut-list file; returns normalised ``source``, ``segments`` and ``targets``."""
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValidationError(f"{target} is not a regular file")
    if target.stat().st_size > MAX_BYTES:
        raise ValidationError(f"{target} is larger than {MAX_BYTES // 1024} KiB")
    try:
        data = read_json_file(target)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(f"{target} is not valid JSON: {exc}") from None
    return validate_plan(data)


def validate_plan(data: object) -> dict:
    plan = _keys(data, frozenset({"schema_version", "source", "segments", "targets"}), "cut list")
    if plan.get("schema_version") != SCHEMA:
        raise ValidationError(f"schema_version must be {SCHEMA!r}")
    raw = _keys(plan.get("source"), SOURCE_KEYS, "source")
    file = raw.get("file")
    if not isinstance(file, str) or not file.strip() or len(file) > 500 or "\n" in file or "\x00" in file:
        raise ValidationError("source.file must be a path (it is only written into the suggested command)")
    if PurePath(file).name in ("", ".", "..") or not PurePath(file).stem.strip("."):
        raise ValidationError("source.file must name a video file, for example media/talk.mp4")
    source = {"file": file, "duration_s": _number(raw.get("duration_s"), "source.duration_s", 0.1, 86_400),
              "width": int(_number(raw.get("width"), "source.width", 16, 16_384)),
              "height": int(_number(raw.get("height"), "source.height", 16, 16_384)),
              "focus_x": _number(raw.get("focus_x", 0.5), "source.focus_x", 0, 1),
              "focus_y": _number(raw.get("focus_y", 0.5), "source.focus_y", 0, 1),
              "has_audio": raw.get("has_audio", True)}
    if not isinstance(source["has_audio"], bool):
        raise ValidationError("source.has_audio must be true or false")

    raw_segments = plan.get("segments", [])
    if not isinstance(raw_segments, list) or len(raw_segments) > MAX_SEGMENTS:
        raise ValidationError(f"segments must be a list of at most {MAX_SEGMENTS}")
    segments: list[dict] = []
    seen: set[str] = set()
    for n, item in enumerate(raw_segments, start=1):
        item = _keys(item, SEGMENT_KEYS, f"segment {n}")
        seg_id = item.get("id")
        if not isinstance(seg_id, str) or not _ID.fullmatch(seg_id) or seg_id in seen:
            raise ValidationError(f"segment {n}: id must be a unique lowercase slug (a-z, 0-9, -)")
        seen.add(seg_id)
        start = _number(item.get("start_s"), f"segment {seg_id} start_s", 0, source["duration_s"])
        end = _number(item.get("end_s"), f"segment {seg_id} end_s", 0, source["duration_s"])
        if end <= start:
            raise ValidationError(f"segment {seg_id}: end_s must be after start_s")
        label = item.get("label", "")
        if not isinstance(label, str) or len(label) > 120:
            raise ValidationError(f"segment {seg_id}: label must be text up to 120 characters")
        priority = item.get("priority", 3)
        if type(priority) is not int or not 1 <= priority <= 5:
            raise ValidationError(f"segment {seg_id}: priority must be an integer 1 (keep first) to 5")
        segments.append({"id": seg_id, "start_s": start, "end_s": end, "label": label, "priority": priority})
    ordered = sorted(segments, key=lambda s: s["start_s"])
    for before, after in zip(ordered, ordered[1:]):
        if after["start_s"] < before["end_s"]:
            raise ValidationError(f"segments {before['id']} and {after['id']} overlap; split them so no moment repeats")
    if not segments:
        segments = [{"id": "whole", "start_s": 0.0, "end_s": source["duration_s"], "label": "the whole video",
                     "priority": 1}]

    raw_targets = plan.get("targets")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_TARGETS:
        raise ValidationError(f"targets must list 1 to {MAX_TARGETS} platforms")
    targets, names = [], set()
    for n, item in enumerate(raw_targets, start=1):
        item = _keys(item, TARGET_KEYS, f"target {n}")
        platform = item.get("platform")
        if platform not in VIDEO_PLATFORMS:
            raise ValidationError(f"target {n}: platform must be one of {', '.join(VIDEO_PLATFORMS)}")
        if platform in names:
            raise ValidationError(f"target {n}: {platform} is listed twice")
        names.add(platform)
        aspect = parse_aspect(item["aspect"]) if "aspect" in item else None
        max_s = _number(item["max_s"], f"target {platform} max_s", 1, 86_400) if "max_s" in item else None
        targets.append({"platform": platform, "aspect": aspect, "max_s": max_s})
    return {"source": source, "segments": segments, "targets": targets}


def _row(platform: str, key: str) -> dict | None:
    return CONSTRAINTS[platform]["video"].get(key)


def _limit(platform: str, key: str) -> dict:
    row = _row(platform, key)
    if row is None or row["value"] is None:
        return {"value": None, "basis": "unverified", "source": row["source"] if row else None,
                "checked": row["verified_date"] if row else None}
    return {"value": row["value"], "basis": "official", "source": row["source"], "checked": row["verified_date"]}


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def crop_box(width: int, height: int, aspect: tuple[int, int], focus_x: float, focus_y: float) -> dict | None:
    """The largest ``aspect`` window inside the frame, centred on the focus point; None when no crop is needed."""
    want = aspect[0] / aspect[1]
    have = width / height
    if abs(want - have) < 0.01:
        return None
    if have > want:  # source is wider: keep the full height
        w, h = _even(height * want), _even(height)
        x, y = min(max(0, round(focus_x * width - w / 2)), width - w), 0
    else:  # source is taller: keep the full width
        w, h = _even(width), _even(width / want)
        x, y = 0, min(max(0, round(focus_y * height - h / 2)), height - h)
    return {"x": int(x), "y": int(y), "width": w, "height": h}


def _select(segments: list[dict], max_s: float | None) -> tuple[list[dict], list[dict]]:
    """Segments by priority (then time) while they fit, returned in time order; and the ones left out."""
    kept, total = [], 0.0
    for seg in sorted(segments, key=lambda s: (s["priority"], s["start_s"])):
        length = seg["end_s"] - seg["start_s"]
        if max_s is None or total + length <= max_s + 1e-9:
            kept.append(seg)
            total += length
    dropped = [s for s in segments if s not in kept]
    return sorted(kept, key=lambda s: s["start_s"]), sorted(dropped, key=lambda s: s["start_s"])


def _seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def ffmpeg_command(source: dict, kept: list[dict], crop: dict | None, frame: tuple[int, int] | None,
                   output: str) -> str:
    """A suggested command: trim the kept segments, join them, crop and scale. Not executed here."""
    audio = source["has_audio"]
    parts, joins = [], []
    for n, seg in enumerate(kept):
        start, end = _seconds(seg["start_s"]), _seconds(seg["end_s"])
        parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{n}]")
        joins.append(f"[v{n}]")
        if audio:
            parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{n}]")
            joins.append(f"[a{n}]")
    parts.append("".join(joins) + f"concat=n={len(kept)}:v=1:a={1 if audio else 0}[vc]" + ("[ac]" if audio else ""))
    video = "[vc]"
    post = []
    if crop:
        post.append(f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
    if frame:
        post.append(f"scale={frame[0]}:{frame[1]}")
    if post:
        parts.append("[vc]" + ",".join(post) + "[vout]")
        video = "[vout]"
    command = ["ffmpeg", "-i", source["file"], "-filter_complex", ";".join(parts), "-map", video]
    if audio:
        command += ["-map", "[ac]", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", output]
    return shlex.join(command)


def output_size(width: int, height: int, platform: str) -> tuple[tuple[int, int], list[str]]:
    """The output frame: scaled down (never up) so the short side is at most ``SHORT_SIDE`` and the sourced
    maximum width/dimension is kept, with even sides (H.264 4:2:0 needs them); plus warnings."""
    warnings: list[str] = []
    scale = min(1.0, SHORT_SIDE / min(width, height))
    max_width, max_dimension = _limit(platform, "max_width")["value"], _limit(platform, "max_dimension")["value"]
    if max_width:
        scale = min(scale, max_width / width)
    if max_dimension:
        scale = min(scale, max_dimension / max(width, height))
    size = (_even(width * scale), _even(height * scale))
    min_dimension = _limit(platform, "min_dimension")["value"]
    if min_dimension and min(size) < min_dimension:
        warnings.append(f"{size[0]}x{size[1]} is below {platform}'s minimum of {min_dimension} px per side")
    return size, warnings


def _output_name(file: str, platform: str) -> str:
    path = PurePath(file)
    name = str(path.with_name(f"{path.stem}.{platform}.mp4"))
    return name if "/" in name and not name.startswith("-") else "./" + name  # never read as an ffmpeg option


def target_plan(source: dict, segments: list[dict], target: dict) -> dict:
    platform = target["platform"]
    warnings: list[str] = []
    max_limit, min_limit = _limit(platform, "max_duration_s"), _limit(platform, "min_duration_s")
    aspect_limit = _limit(platform, "aspect_ratio")
    cap = max_limit["value"]
    if target["max_s"] is not None:
        if cap is not None and target["max_s"] > cap:
            warnings.append(f"max_s {target['max_s']:g} s is above {platform}'s limit of {cap:g} s; the limit is used")
        else:
            cap = target["max_s"]
    if max_limit["value"] is None:
        warnings.append(f"{platform}'s maximum duration is not established by the cited source; check it yourself")
    kept, dropped = _select(segments, cap)
    total = sum(s["end_s"] - s["start_s"] for s in kept)
    if not kept:
        warnings.append("no segment fits the duration limit; mark a shorter segment")
    if min_limit["value"] is not None and kept and total < min_limit["value"]:
        warnings.append(f"the cut is {total:g} s, shorter than {platform}'s minimum of {min_limit['value']:g} s")

    if target["aspect"] is not None:
        aspect, aspect_basis = target["aspect"], "owner"
    elif platform in DEFAULT_ASPECT:
        aspect, aspect_basis = parse_aspect(DEFAULT_ASPECT[platform][0]), DEFAULT_ASPECT[platform][1]
    else:
        divisor = math.gcd(source["width"], source["height"])
        aspect, aspect_basis = (source["width"] // divisor, source["height"] // divisor), "source"
    ratio = aspect[0] / aspect[1]
    allowed = aspect_limit["value"]
    if allowed is not None and not allowed[0] <= ratio <= allowed[1]:
        warnings.append(f"aspect {aspect[0]}:{aspect[1]} is outside {platform}'s allowed range "
                        f"{allowed[0]:g} to {allowed[1]:g} (width / height)")
    crop = crop_box(source["width"], source["height"], aspect, source["focus_x"], source["focus_y"])
    picture = (crop["width"], crop["height"]) if crop else (source["width"], source["height"])
    size, size_warnings = output_size(*picture, platform)
    warnings += size_warnings
    if crop and min(size) < SHORT_SIDE:
        warnings.append(f"the cut stays {size[0]}x{size[1]}, below {SHORT_SIDE} px on its short side "
                        "(ReachWeaver never upscales). Use a higher-resolution master for a sharper cut")
    frame = size if size != picture else None
    output = _output_name(source["file"], platform)
    return {
        "platform": platform,
        "duration": {"cut_s": round(total, 3), "max": max_limit, "min": min_limit, "owner_max_s": target["max_s"]},
        "keep": [{"id": s["id"], "start_s": s["start_s"], "end_s": s["end_s"], "label": s["label"]} for s in kept],
        "leave_out": [s["id"] for s in dropped],
        "aspect": {"value": f"{aspect[0]}:{aspect[1]}", "basis": aspect_basis, "allowed": aspect_limit},
        "crop": crop, "scale_to": list(frame) if frame else None,
        "subtitles": _limit(platform, "subtitle_formats"), "max_bytes": _limit(platform, "max_bytes"),
        "output": output,
        "ffmpeg": ffmpeg_command(source, kept, crop, frame, output) if kept else None,
        "warnings": warnings,
    }


def build_cutlist(plan: dict) -> dict:
    """The cut list for every target of a validated plan (``validate_plan``/``load_plan``)."""
    return {"schema_version": SCHEMA, "source": plan["source"],
            "targets": [target_plan(plan["source"], plan["segments"], t) for t in plan["targets"]],
            "ffmpeg_docs": FFMPEG_DOCS, "note": NOTE}


def render_text(result: dict) -> str:
    src = result["source"]
    lines = [f"Cut list for {src['file']} ({_seconds(src['duration_s'])} s, {src['width']}x{src['height']})", ""]
    for t in result["targets"]:
        cap = t["duration"]["max"]["value"]
        lines.append(f"{t['platform']}: {t['duration']['cut_s']:g} s" + (f" of at most {cap:g} s" if cap else "")
                     + f", aspect {t['aspect']['value']} ({t['aspect']['basis']})")
        lines.append("  keep: " + (", ".join(f"{k['id']} {_seconds(k['start_s'])}-{_seconds(k['end_s'])} s"
                                             for k in t["keep"]) or "nothing fits"))
        if t["leave_out"]:
            lines.append("  leave out: " + ", ".join(t["leave_out"]))
        if t["crop"]:
            c = t["crop"]
            lines.append(f"  crop {c['width']}x{c['height']} at x={c['x']}, y={c['y']}"
                         + (f", then scale to {t['scale_to'][0]}x{t['scale_to'][1]}" if t["scale_to"] else ""))
        subs = t["subtitles"]["value"]
        lines.append("  subtitles: " + (", ".join(subs) if subs else "format not established by the source"))
        for warning in t["warnings"]:
            lines.append(f"  WARNING: {warning}")
        if t["ffmpeg"]:
            lines.append(f"  suggested: {t['ffmpeg']}")
        lines.append("")
    lines.append(result["note"])
    return "\n".join(lines)
