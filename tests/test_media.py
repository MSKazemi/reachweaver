"""Offline media manifest validation: real headers, sourced limits, accessibility, rights, locales.

All fixtures are generated in temporary directories; no binaries are committed and no
network access is possible from the validator.
"""
from __future__ import annotations

import contextlib
import io
import json
import socket
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from reachweaver import media
from reachweaver.cli import main
from reachweaver.media import (
    CONSTRAINTS, UNKNOWN, media_check, probe_file, validate_media_manifest, validate_subtitle_text,
)


# ---------------------------------------------------------------------------
# Tiny fixture generators.
# ---------------------------------------------------------------------------

def png_bytes(width: int, height: int, exif: bool = False) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if exif:
        body += chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + body + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg_bytes(width: int, height: int, exif: bool = False) -> bytes:
    def segment(marker: int, payload: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    out = b"\xff\xd8" + segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if exif:
        out += segment(0xE1, b"Exif\x00\x00" + b"MM\x00*" + b"\x00" * 20)
    out += segment(0xDB, b"\x00" + bytes(range(1, 65)))
    out += segment(0xC0, b"\x08" + struct.pack(">HH", height, width) + b"\x01\x01\x11\x00")
    out += segment(0xC4, b"\x00" + b"\x01" + b"\x00" * 15 + b"\x00")
    out += segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x00\x00"
    return out + b"\xff\xd9"


def gif_bytes(width: int, height: int) -> bytes:
    return (b"GIF89a" + struct.pack("<HH", width, height) + b"\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff"
            + b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def webp_bytes(width: int, height: int, variant: str = "VP8L") -> bytes:
    if variant == "VP8L":
        bits = (width - 1) | ((height - 1) << 14)
        data = b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 4
    elif variant == "VP8 ":
        data = b"\x30\x01\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 8
    else:  # VP8X canvas
        data = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = variant.encode() + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


IDENTITY = (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
ROTATE_90 = (0, 0x10000, 0, 0xFFFF0000, 0, 0, 0, 0, 0x40000000)


def mp4_bytes(width: int, height: int, seconds: float, *, brand: bytes = b"isom", moov_first: bool = True,
              matrix=IDENTITY, version: int = 0, truncate_moov: bool = False, mdat_size: int = 64) -> bytes:
    timescale = 1000
    duration = int(seconds * timescale)
    if version == 1:
        mvhd = b"\x01\x00\x00\x00" + struct.pack(">QQIQ", 0, 0, timescale, duration) + b"\x00" * 80
        tkhd = b"\x01\x00\x00\x03" + struct.pack(">QQIIQ", 0, 0, 1, 0, duration)
    else:
        mvhd = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration) + b"\x00" * 80
        tkhd = b"\x00\x00\x00\x03" + struct.pack(">IIIII", 0, 0, 1, 0, duration)
    tkhd += b"\x00" * 16 + struct.pack(">9I", *matrix) + struct.pack(">II", width << 16, height << 16)
    hdlr = b"\x00" * 4 + b"\x00" * 4 + b"vide" + b"\x00" * 12 + b"VideoHandler\x00"
    moov = box(b"moov", box(b"mvhd", mvhd) + box(b"trak", box(b"tkhd", tkhd) + box(b"mdia", box(b"hdlr", hdlr))))
    if truncate_moov:
        moov = struct.pack(">I", len(moov) + 5000) + moov[4:]
    ftyp = box(b"ftyp", brand + b"\x00\x00\x02\x00" + brand + b"mp41")
    mdat = box(b"mdat", b"\x00" * mdat_size)
    return ftyp + (moov + mdat if moov_first else mdat + moov)


def full_box(kind: bytes, payload: bytes, version: int = 0) -> bytes:
    return box(kind, bytes([version]) + b"\x00\x00\x00" + payload)


def video_entry(fourcc: bytes, width: int = 1080, height: int = 1920) -> bytes:
    """VisualSampleEntry header plus an empty codec-config child (not decodable, structurally valid)."""
    payload = (b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16 + struct.pack(">HH", width, height)
               + struct.pack(">II", 0x00480000, 0x00480000) + b"\x00" * 4 + struct.pack(">H", 1)
               + b"\x00" * 32 + struct.pack(">Hh", 0x18, -1))
    return box(fourcc, payload + box(b"avcC" if fourcc.startswith(b"avc") else b"hvcC", b"\x01"))


def esds(object_type: int) -> bytes:
    decoder_config = bytes([object_type, 0x15]) + b"\x00" * 11 + b"\x05\x02\x12\x10"
    es = b"\x00\x01\x00" + b"\x04" + bytes([len(decoder_config)]) + decoder_config + b"\x06\x01\x02"
    return full_box(b"esds", b"\x03" + bytes([len(es)]) + es)


def audio_entry(fourcc: bytes = b"mp4a", rate: int = 48000, channels: int = 2, object_type: int = 0x40,
                qt_version: int = 0) -> bytes:
    if qt_version == 2:  # QuickTime SoundDescription v2: real values in the extended fields.
        payload = (b"\x00" * 6 + struct.pack(">H", 1) + struct.pack(">HH", 2, 0) + b"\x00" * 4
                   + struct.pack(">HHHH", 3, 16, 0xFFFE, 0) + struct.pack(">I", 0x00010000)
                   + struct.pack(">IdI", 72, float(rate), channels) + struct.pack(">IIIII", 0x7F000000, 16, 0, 0, 0))
    else:
        payload = (b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 8
                   + struct.pack(">HHHH", channels, 16, 0, 0) + struct.pack(">I", rate << 16))
    return box(fourcc, payload + (esds(object_type) if fourcc == b"mp4a" else b""))


def trak(handler: bytes, entry: bytes, timescale: int, stts: list[tuple[int, int]],
         width: int = 0, height: int = 0) -> bytes:
    tkhd = (b"\x00\x00\x00\x03" + struct.pack(">IIIII", 0, 0, 1, 0, 0) + b"\x00" * 16
            + struct.pack(">9I", *IDENTITY) + struct.pack(">II", width << 16, height << 16))
    mdhd = struct.pack(">IIII", 0, 0, timescale, sum(c * d for c, d in stts)) + b"\x55\xc4\x00\x00"
    hdlr = b"\x00" * 4 + handler + b"\x00" * 12 + b"Handler\x00"
    stsd = full_box(b"stsd", struct.pack(">I", 1) + entry)
    stts_box = full_box(b"stts", struct.pack(">I", len(stts)) + b"".join(struct.pack(">II", c, d) for c, d in stts))
    stbl = box(b"stbl", stsd + stts_box)
    return box(b"trak", box(b"tkhd", tkhd) + box(b"mdia", full_box(b"mdhd", mdhd) + full_box(b"hdlr", hdlr)
                                                 + box(b"minf", stbl)))


def av_mp4(video: bytes | None = b"avc1", *, fps_ticks: list[tuple[int, int]] | None = None,
           timescale: int = 30000, audio: bytes | None = None, moov_first: bool = True,
           raw_video_entry: bytes | None = None, mdat_size: int = 64, seconds: int = 10) -> bytes:
    """MP4 with real stsd/mdhd/stts/sample entries. Default: H.264 at 30 fps plus 48 kHz stereo AAC."""
    stts = fps_ticks if fps_ticks is not None else [(30 * seconds, 1000)]
    traks = b""
    if video is not None or raw_video_entry is not None:
        entry = raw_video_entry if raw_video_entry is not None else video_entry(video or b"avc1")
        traks += trak(b"vide", entry, timescale, stts, 1080, 1920)
    audio = audio_entry() if audio is None else audio
    if audio:
        traks += trak(b"soun", audio, 48000, [(47 * seconds, 1024)])
    mvhd = b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, seconds * 1000) + b"\x00" * 80
    moov = box(b"moov", box(b"mvhd", mvhd) + traks)
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isommp41")
    mdat = box(b"mdat", b"\x00" * mdat_size)
    return ftyp + (moov + mdat if moov_first else mdat + moov)


GOOD_VTT = "WEBVTT\n\nNOTE generated in test\n\n1\n00:00.000 --> 00:02.000\nReachWeaver demo\n\n00:02.000 --> 00:04.500 line:90%\nسلام دنیا\n"
GOOD_SRT = "1\n00:00:00,000 --> 00:00:02,000\nReachWeaver demo\n\n2\n00:00:02,000 --> 00:00:04,000\nCiao mondo\n"


def asset(**overrides) -> dict:
    base = {
        "id": "release-card", "path": "card.png", "kind": "image", "targets": ["linkedin"],
        "locale": "en", "alt_text": "Terminal showing ReachWeaver media-check passing on a release card",
        "license": "owner", "attribution": "", "rights_confirmed": True,
        "provenance": {"created_by": "owner", "tool": "", "source_url": ""},
    }
    base.update(overrides)
    return base


class MediaFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, data: bytes | str) -> Path:
        path = self.dir / name
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
        return path

    def check(self, *assets, **top) -> dict:
        manifest = {"schema_version": 1, "default_locale": "en", "assets": list(assets)}
        manifest.update(top)
        return validate_media_manifest(manifest, self.dir)

    def assertHas(self, items, needle):
        self.assertTrue(any(needle in item for item in items), f"{needle!r} not in {items}")

    def assertLacks(self, items, needle):
        self.assertFalse(any(needle in item for item in items), f"{needle!r} unexpectedly in {items}")


class HeaderProbeTests(MediaFixture):
    def test_image_headers_report_real_dimensions(self):
        cases = {
            "a.png": (png_bytes(30, 20), "png"), "a.jpg": (jpeg_bytes(640, 480), "jpeg"),
            "a.gif": (gif_bytes(12, 7), "gif"), "l.webp": (webp_bytes(300, 200, "VP8L"), "webp"),
            "s.webp": (webp_bytes(320, 180, "VP8 "), "webp"), "x.webp": (webp_bytes(1080, 1920, "VP8X"), "webp"),
        }
        expected = {"a.png": (30, 20), "a.jpg": (640, 480), "a.gif": (12, 7), "l.webp": (300, 200),
                    "s.webp": (320, 180), "x.webp": (1080, 1920)}
        for name, (data, container) in cases.items():
            info = probe_file(self.write(name, data))
            self.assertEqual(info["container"], container, name)
            self.assertEqual((info["width"], info["height"]), expected[name], name)

    def test_mp4_duration_dimensions_versions_rotation_and_mov_brand(self):
        info = probe_file(self.write("v.mp4", mp4_bytes(1080, 1920, 12.5)))
        self.assertEqual((info["container"], info["width"], info["height"]), ("mp4", 1080, 1920))
        self.assertEqual(info["duration_seconds"], 12.5)
        self.assertEqual(info["rotation_degrees"], 0)
        info = probe_file(self.write("v1.mp4", mp4_bytes(640, 360, 3.0, version=1)))
        self.assertEqual((info["width"], info["height"], info["duration_seconds"]), (640, 360, 3.0))
        # Phone footage stored landscape with a 90-degree display matrix is vertical when shown.
        info = probe_file(self.write("r.mp4", mp4_bytes(1920, 1080, 5, matrix=ROTATE_90)))
        self.assertEqual((info["width"], info["height"], info["rotation_degrees"]), (1080, 1920, 90))
        self.assertEqual(probe_file(self.write("q.mov", mp4_bytes(640, 480, 4, brand=b"qt  ")))["container"], "mov")

    def test_unparseable_media_reports_unknown_and_never_guesses(self):
        info = probe_file(self.write("t.mp4", mp4_bytes(1080, 1920, 10, truncate_moov=True)))
        self.assertEqual(info["container"], "mp4")
        self.assertEqual((info["width"], info["height"], info["duration_seconds"]), (UNKNOWN, UNKNOWN, UNKNOWN))
        info = probe_file(self.write("bad.jpg", b"\xff\xd8\xff\xe0\x00"))
        self.assertEqual((info["container"], info["width"]), ("jpeg", UNKNOWN))
        self.assertEqual(probe_file(self.write("n.png", b"not an image"))["container"], UNKNOWN)


class CodecFrameRateProbeTests(MediaFixture):
    def test_h264_and_hevc_at_30fps_with_aac(self):
        for fourcc, codec in ((b"avc1", "h264"), (b"hvc1", "hevc")):
            info = probe_file(self.write(f"{codec}.mp4", av_mp4(fourcc)))
            self.assertEqual((info["video_codec"], info["video_fourcc"]), (codec, fourcc.decode()))
            self.assertEqual(info["frame_rate_fps"], 30.0)
            self.assertIs(info["variable_frame_rate"], False)
            self.assertEqual((info["audio_codec"], info["audio_fourcc"]), ("aac", "mp4a"))
            self.assertEqual((info["audio_sample_rate_hz"], info["audio_channels"], info["audio_tracks"]),
                             (48000, 2, 1))
            self.assertIs(info["fast_start"], True)
            self.assertEqual(info["parse_notes"], [])
            self.assertEqual((info["width"], info["height"], info["duration_seconds"]), (1080, 1920, 10.0))

    def test_fourcc_table_audio_entries_and_ntsc_rate(self):
        for fourcc, codec in ((b"avc3", "h264"), (b"hev1", "hevc"), (b"av01", "av1"), (b"vp09", "vp9")):
            self.assertEqual(probe_file(self.write("v.mp4", av_mp4(fourcc)))["video_codec"], codec, fourcc)
        for entry, codec in ((audio_entry(b"Opus"), "opus"), (audio_entry(b"ac-3", 48000, 6), "ac3"),
                             (audio_entry(object_type=0x6B), "mp3"), (audio_entry(object_type=0xE1), UNKNOWN)):
            self.assertEqual(probe_file(self.write("a.mp4", av_mp4(audio=entry)))["audio_codec"], codec)
        info = probe_file(self.write("c.mp4", av_mp4(audio=audio_entry(b"ac-3", 44100, 6))))
        self.assertEqual((info["audio_sample_rate_hz"], info["audio_channels"]), (44100, 6))
        info = probe_file(self.write("q.mov", av_mp4(audio=audio_entry(rate=96000, channels=2, qt_version=2))))
        self.assertEqual((info["audio_codec"], info["audio_sample_rate_hz"], info["audio_channels"]), ("aac", 96000, 2))
        # 29.97 fps (30000/1001); a shorter final frame alone is not variable frame rate.
        info = probe_file(self.write("n.mp4", av_mp4(fps_ticks=[(299, 1001), (1, 500)])))
        self.assertEqual(info["frame_rate_fps"], 30.02)
        self.assertIs(info["variable_frame_rate"], False)
        self.assertEqual(info["frame_rate_range_fps"], [29.97, 29.97])
        info = probe_file(self.write("vfr.mp4", av_mp4(fps_ticks=[(150, 1000), (75, 2000)])))
        self.assertIs(info["variable_frame_rate"], True)
        self.assertEqual((info["frame_rate_range_fps"], info["frame_rate_fps"]), ([15.0, 30.0], 22.5))

    def test_unrecognized_entry_and_silent_video_are_reported_not_guessed(self):
        info = probe_file(self.write("e.mp4", av_mp4(raw_video_entry=box(b"encv", b"\x00" * 78))))
        self.assertEqual((info["video_codec"], info["video_fourcc"]), (UNKNOWN, "encv"))
        self.assertEqual(info["frame_rate_fps"], 30.0)  # Timing tables are independent of the codec.
        info = probe_file(self.write("silent.mp4", av_mp4(audio=b"")))
        self.assertEqual((info["audio_tracks"], info["audio_codec"]), (0, UNKNOWN))
        # Legacy fixture without stsd/stts: codec and frame rate stay unknown.
        info = probe_file(self.write("old.mp4", mp4_bytes(1080, 1920, 5)))
        self.assertEqual((info["video_codec"], info["frame_rate_fps"]), (UNKNOWN, UNKNOWN))

    def test_malformed_and_huge_box_sizes_are_guarded(self):
        # A sample entry that claims to be larger than its stsd parent.
        bad_entry = struct.pack(">I", 0x7FFFFFF0) + video_entry(b"avc1")[4:]
        info = probe_file(self.write("m.mp4", av_mp4(raw_video_entry=bad_entry)))
        self.assertEqual(info["video_codec"], UNKNOWN)
        self.assertTrue(any("malformed size" in n for n in info["parse_notes"]), info["parse_notes"])
        self.assertEqual(info["audio_codec"], "aac")  # The well-formed audio track still parses.
        # An stts that declares far more entries than it holds.
        good = av_mp4()
        marker = b"stts\x00\x00\x00\x00\x00\x00\x00\x01"
        self.assertEqual(good.count(marker), 2)  # Video track first, then audio.
        info = probe_file(self.write("s.mp4", good.replace(marker, b"stts\x00\x00\x00\x00\xff\xff\xff\xff", 1)))
        self.assertEqual(info["frame_rate_fps"], UNKNOWN)
        self.assertTrue(any("stts declares" in n for n in info["parse_notes"]), info["parse_notes"])
        # A 64-bit top-level size far beyond the file, and a moov larger than the read cap.
        huge = box(b"ftyp", b"isom\x00\x00\x02\x00") + struct.pack(">I4sQ", 1, b"mdat", 1 << 62)
        info = probe_file(self.write("h.mp4", huge))
        self.assertEqual((info["container"], info["video_codec"]), ("mp4", UNKNOWN))
        self.assertTrue(any("top-level box 'mdat'" in n for n in info["parse_notes"]), info["parse_notes"])
        with mock.patch.object(media, "MAX_MOOV_BYTES", 64):
            info = probe_file(self.write("cap.mp4", good))
        self.assertEqual((info["video_codec"], info["width"]), (UNKNOWN, UNKNOWN))
        self.assertTrue(any("not parsed" in n for n in info["parse_notes"]), info["parse_notes"])

    def test_absurd_nesting_depth_stops_without_recursion_error(self):
        nested = video_entry(b"avc1")
        for _ in range(5000):  # Far beyond Python's default recursion limit if walked naively.
            nested = box(b"trak", nested)
        data = box(b"ftyp", b"isom\x00\x00\x02\x00") + box(b"moov", nested) + box(b"mdat", b"\x00" * 8)
        info = probe_file(self.write("deep.mp4", data))
        self.assertEqual((info["video_codec"], info["frame_rate_fps"]), (UNKNOWN, UNKNOWN))
        self.assertEqual(info["audio_tracks"], UNKNOWN)  # The walk was cut short: no "no audio" claim.
        self.assertTrue(any("nesting deeper than" in n for n in info["parse_notes"]), info["parse_notes"])
        self.assertIs(info["fast_start"], True)

    def test_moov_after_mdat_is_not_fast_start(self):
        info = probe_file(self.write("late.mp4", av_mp4(moov_first=False)))
        self.assertIs(info["fast_start"], False)
        self.assertEqual((info["video_codec"], info["frame_rate_fps"]), ("h264", 30.0))


class SubtitleTests(unittest.TestCase):
    def test_good_srt_and_vtt(self):
        for text, fmt in ((GOOD_SRT, "srt"), (GOOD_VTT, "vtt"), ("﻿" + GOOD_SRT.replace("\n", "\r\n"), "srt")):
            errors, warnings, summary = validate_subtitle_text(text, fmt)
            self.assertEqual(errors, [], fmt)
            self.assertEqual(summary["cues"], 2)

    def test_bad_subtitles_are_actionable(self):
        bad_srt = ("1\n00:00:05,000 --> 00:00:06,000\nlater\n\n2\n00:00:01,000 --> 00:00:02,000\nearlier\n\n"
                   "3\n00:00:07,000 --> 00:00:06,000\nbackwards\n\n4\n00:00:08.000 --> 00:00:09,000\nwrong sep\n\n"
                   "5\n00:00:10,000 --> 00:00:11,000\n   \n")
        errors, _, _ = validate_subtitle_text(bad_srt, "srt")
        joined = " | ".join(errors)
        for needle in ("monotonic", "end time must be after", "malformed timestamp", "cue text is empty"):
            self.assertIn(needle, joined)
        errors, _, _ = validate_subtitle_text("1\n00:00.000 --> 00:01.000\nno header\n", "vtt")
        self.assertIn("WEBVTT", errors[0])
        errors, _, _ = validate_subtitle_text("WEBVTT\n", "vtt")
        self.assertIn("no cues", errors[0])
        _, warnings, _ = validate_subtitle_text("1\n00:00:00,000 --> 00:00:03,000\na\n\n2\n00:00:02,000 --> 00:00:04,000\nb\n", "srt")
        self.assertTrue(any("overlaps" in w for w in warnings))

    def test_non_ascii_or_huge_srt_indexes_do_not_crash(self):
        # Review finding (2026-10-09): str.isdigit() accepts "²", which int() then rejects with ValueError.
        for index in ("²", "①", "1²"):
            with self.subTest(index=index):
                errors, _, summary = validate_subtitle_text(f"{index}\n00:00:01,000 --> 00:00:02,000\nhi\n", "srt")
                self.assertIn("cue 1: SRT cue must start with a numeric index", errors)
                self.assertEqual(summary["cues"], 0)
        # Arabic-Indic decimal digits are decimal digits; they count as the index 1.
        errors, warnings, summary = validate_subtitle_text("١\n00:00:01,000 --> 00:00:02,000\nhi\n", "srt")
        self.assertEqual((errors, warnings, summary["cues"]), ([], [], 1))
        huge = "9" * 5000
        errors, warnings, summary = validate_subtitle_text(f"{huge}\n00:00:01,000 --> 00:00:02,000\nhi\n\n"
                                                           "2\n00:00:02,000 --> 00:00:03,000\nho\n", "srt")
        self.assertEqual((errors, summary["cues"]), ([], 2))
        self.assertEqual(len(warnings), 1, warnings)  # counting carries on from the bad index, so cue 2 is fine
        self.assertLess(len(warnings[0]), 200)
        self.assertIn("SRT index 99999999999999999999… (5000 digits) is not sequential (expected 1)", warnings[0])
        # Over-long hour fields are malformed timestamps, not int()/float() overflow crashes.
        for hours in ("9" * 5000, "9" * 400, "1234567"):
            with self.subTest(hour_digits=len(hours)):
                errors, _, _ = validate_subtitle_text(f"1\n{hours}:00:01,000 --> 00:00:02,000\nhi\n", "srt")
                self.assertTrue(any("malformed timestamp" in e for e in errors), errors)
                errors, _, _ = validate_subtitle_text(f"WEBVTT\n\n{hours}:00:01.000 --> 00:02.000\nhi\n", "vtt")
                self.assertTrue(any("malformed timestamp" in e for e in errors), errors)
        self.assertEqual(validate_subtitle_text("1\n100:00:01,000 --> 100:00:02,000\nhi\n", "srt")[0], [])


class ManifestTests(MediaFixture):
    def test_valid_linkedin_image_and_vertical_video_storyboard_assets(self):
        self.write("card.png", png_bytes(1200, 627))
        self.write("demo.mp4", mp4_bytes(1080, 1920, 30, mdat_size=80_000))
        self.write("demo.en.vtt", GOOD_VTT)
        self.write("demo.en.srt", GOOD_SRT)
        video = asset(id="demo", path="demo.mp4", kind="video", targets=["youtube_shorts", "bluesky", "linkedin"],
                      alt_text="", subtitles=[{"path": "demo.en.vtt", "locale": "en"},
                                              {"path": "demo.en.srt", "locale": "en"}])
        result = self.check(asset(), video)
        self.assertTrue(result["valid"], result["errors"])
        card, demo = result["assets"]
        self.assertEqual((card["width"], card["height"], card["detected_container"]), (1200, 627, "png"))
        self.assertEqual((demo["duration_seconds"], demo["aspect_ratio"]), (30.0, 0.5625))
        self.assertTrue(any("Vertical video" in n for n in demo["notes"]))
        self.assertIn("allowed_containers", card["targets"]["linkedin"]["checked"])
        # Unknown constraint rows are skipped visibly, never invented.
        self.assertIn("aspect_ratio", card["targets"]["linkedin"]["skipped"])
        self.assertHas(result["warnings"], "linkedin image constraint 'aspect_ratio' skipped")
        self.assertIn("does not upload", result["note"])

    def test_every_constraint_row_is_sourced_and_dated(self):
        for platform, kinds in CONSTRAINTS.items():
            for kind, rows in kinds.items():
                for name, row in rows.items():
                    self.assertTrue(row["source"].startswith("https://"), (platform, kind, name))
                    self.assertRegex(row["verified_date"], r"^\d{4}-\d{2}-\d{2}$")
                    self.assertIn(row["severity"], ("error", "warning"))
                    if row["value"] is None:
                        self.assertEqual(row["severity"], "warning")

    def test_extension_magic_mismatch_and_kind_mismatch(self):
        self.write("card.png", jpeg_bytes(1200, 627))
        result = self.check(asset())
        self.assertHas(result["errors"], "extension .png does not match detected jpeg")
        self.write("clip.png", mp4_bytes(100, 100, 2))
        result = self.check(asset(path="clip.png"))
        self.assertHas(result["errors"], "declared kind image but file header is mp4")
        self.write("weird.png", b"plain text")
        self.assertHas(self.check(asset(path="weird.png"))["errors"], "unrecognized file header")
        self.write("brand.mp4", mp4_bytes(1080, 1920, 5, brand=b"qt  "))
        self.write("s.vtt", GOOD_VTT)
        result = self.check(asset(path="brand.mp4", kind="video", targets=["tiktok"],
                                  subtitles=[{"path": "s.vtt"}]))
        self.assertHas(result["warnings"], "extension .mp4 does not match detected mov")
        self.assertLacks(result["errors"], "does not match")

    def test_platform_limits_large_files_aspect_duration_resolution(self):
        path = self.write("big.png", png_bytes(1200, 627))
        with path.open("r+b") as handle:  # Sparse padding: large on paper, cheap on disk.
            handle.truncate(2_000_001)
        result = self.check(asset(path="big.png", targets=["bluesky", "instagram"]))
        self.assertHas(result["errors"], "bluesky image: file is 2000001 bytes; limit is 2000000")
        self.assertHas(result["errors"], "instagram image: container png not in allowed jpeg")
        self.write("wide.jpg", jpeg_bytes(3000, 1000))
        result = self.check(asset(path="wide.jpg", targets=["instagram"]))
        self.assertHas(result["errors"], "aspect ratio 3.0000 outside 0.8..1.91")
        self.assertHas(result["warnings"], "width 3000px above 1440px")
        self.write("land.mp4", mp4_bytes(1920, 1080, 200))
        self.write("s.vtt", GOOD_VTT)
        result = self.check(asset(path="land.mp4", kind="video", targets=["youtube_shorts"],
                                  subtitles=[{"path": "s.vtt"}]))
        self.assertHas(result["errors"], "duration 200.0s exceeds 180s")
        self.assertHas(result["errors"], "aspect ratio 1.7778 outside 0.0..1.0")
        self.write("tiny.mp4", mp4_bytes(200, 356, 2))
        result = self.check(asset(path="tiny.mp4", kind="video", targets=["tiktok", "linkedin", "instagram"],
                                  subtitles=[{"path": "s.vtt"}]))
        self.assertHas(result["errors"], "has a dimension below 360px")
        self.assertHas(result["errors"], "linkedin video: file is")  # below 75 KB floor
        self.assertHas(result["errors"], "duration 2.0s is below 3s")
        self.assertHas(result["errors"], "needs a srt subtitle file")

    def test_instagram_moov_after_mdat_and_unknown_duration_skips(self):
        self.write("late.mp4", mp4_bytes(1080, 1920, 10, moov_first=False))
        self.write("s.vtt", GOOD_VTT)
        result = self.check(asset(path="late.mp4", kind="video", targets=["instagram"], subtitles=[{"path": "s.vtt"}]))
        self.assertHas(result["errors"], "moov box must precede mdat")
        self.write("trunc.mp4", mp4_bytes(1080, 1920, 10, truncate_moov=True))
        result = self.check(asset(path="trunc.mp4", kind="video", targets=["bluesky"], subtitles=[{"path": "s.vtt"}]))
        self.assertHas(result["warnings"], "'max_duration_s' skipped (duration unknown")
        self.assertHas(result["warnings"], "duration_seconds unknown")
        self.assertEqual(result["assets"][0]["duration_seconds"], UNKNOWN)

    def test_accessibility_alt_text_rules_and_unicode(self):
        self.write("card.png", png_bytes(1200, 627))
        self.assertHas(self.check(asset(alt_text=""))["errors"], "alt text is required")
        self.assertHas(self.check(asset(alt_text="card.png"))["errors"], "must not be the file name")
        self.assertHas(self.check(asset(alt_text="Image of"))["errors"], "is a placeholder")
        self.assertHas(self.check(asset(alt_text="Image of a dashboard with green checks"))["warnings"],
                       "starts with 'image of'")
        self.assertHas(self.check(asset(alt_text="TODO describe"))["errors"], "placeholder")
        result = self.check(asset(alt_text="ب" * 2001, targets=["bluesky", "linkedin"]))
        self.assertHas(result["errors"], "alt text (en) has 2001 code points; limit is 2000")
        self.assertLacks(result["errors"], "linkedin image: alt text")
        persian = "نمودار ReachWeaver که بررسی رسانه را نشان می‌دهد"
        self.assertTrue(self.check(asset(alt_text=persian, locale="fa"), default_locale="en")["valid"])

    def test_videos_require_captions_and_validate_files(self):
        self.write("v.mp4", mp4_bytes(1080, 1920, 10))
        self.assertHas(self.check(asset(path="v.mp4", kind="video", targets=["tiktok"]))["errors"],
                       "videos require captions/subtitles")
        result = self.check(asset(path="v.mp4", kind="video", targets=["tiktok"], burned_in_captions=True))
        self.assertTrue(result["valid"])
        self.assertHas(result["warnings"], "only burned-in captions")
        self.write("bad.vtt", "1\n00:01.000 --> 00:00.500\n\n")
        result = self.check(asset(path="v.mp4", kind="video", targets=["bluesky"], subtitles=[{"path": "bad.vtt"}]))
        self.assertHas(result["errors"], "WEBVTT")
        self.write("long.vtt", "WEBVTT\n\n00:00.000 --> 00:30.000\ntoo long\n")
        result = self.check(asset(path="v.mp4", kind="video", targets=["bluesky"], subtitles=[{"path": "long.vtt"}]))
        self.assertHas(result["warnings"], "last cue ends after the video")
        self.write("latin1.srt", "1\n00:00:00,000 --> 00:00:01,000\ncaf\xe9\n".encode("latin-1"))
        result = self.check(asset(path="v.mp4", kind="video", targets=["linkedin"], subtitles=[{"path": "latin1.srt"}]))
        self.assertHas(result["errors"], "must be UTF-8")
        self.write("solo.srt", GOOD_SRT)
        self.assertTrue(self.check(asset(path="solo.srt", kind="subtitle", targets=["linkedin"], alt_text=""))["valid"])

    def test_rights_and_provenance_block_unsafe_media(self):
        self.write("card.png", png_bytes(1200, 627))
        self.assertHas(self.check(asset(rights_confirmed="yes"))["errors"], "rights_confirmed must be true")
        result = self.check(asset(license="owner", provenance={"created_by": "third_party"}))
        self.assertHas(result["errors"], "needs its actual license")
        self.assertHas(result["errors"], "requires attribution text")
        self.assertHas(result["warnings"], "no provenance.source_url")
        ok = self.check(asset(license="CC-BY-4.0", attribution="Photo: Jane Doe, CC BY 4.0",
                              provenance={"created_by": "third_party", "source_url": "https://example.org/photo"}))
        self.assertTrue(ok["valid"], ok["errors"])
        self.assertHas(self.check(asset(license="CC-BY-SA-4.0"))["errors"], "requires attribution text")
        result = self.check(asset(provenance={"created_by": "tool", "tool": ""}))
        self.assertHas(result["errors"], "provenance.tool must name")
        self.assertHas(result["warnings"], "tool-generated")
        self.assertHas(self.check(asset(license="not a license!"))["errors"], "not an SPDX identifier")
        self.assertHas(self.check(asset(license="Zlib"))["warnings"], "verify at https://spdx.org/licenses/")
        self.assertHas(self.check(asset(provenance={"created_by": "ai"}))["errors"], "created_by must be one of")

    def test_localization_warnings_and_preserved_terms(self):
        self.write("card.png", png_bytes(1200, 627))
        result = self.check(asset(alt_text_localized={"it": "Terminale che mostra il controllo superato"}),
                            target_locales=["en", "it", "fa"], preserve_terms=["ReachWeaver"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertHas(result["warnings"], "missing alt text translation for target locale fa")
        self.assertLacks(result["warnings"], "target locale it")
        self.assertHas(result["warnings"], "translation it drops preserved term 'ReachWeaver'")
        self.assertHas(self.check(asset(locale="english"))["errors"], "must be a BCP-47 tag")
        self.assertHas(self.check(asset(alt_text_localized={"xx_YY": "a"}))["errors"], "not a BCP-47 tag")
        self.assertHas(self.check(asset(), default_locale="it")["warnings"], "English-first")
        self.write("v.mp4", mp4_bytes(1080, 1920, 10))
        self.write("v.en.vtt", GOOD_VTT)
        result = self.check(asset(path="v.mp4", kind="video", targets=["tiktok"],
                                  subtitles=[{"path": "v.en.vtt", "locale": "en"}]), target_locales=["en", "fa"])
        self.assertHas(result["warnings"], "missing subtitle file for target locale fa")

    def test_metadata_crop_safety_paths_and_secrets(self):
        self.write("exif.jpg", jpeg_bytes(1080, 1080, exif=True))
        self.assertHas(self.check(asset(path="exif.jpg", targets=["instagram"]))["warnings"], "EXIF metadata")
        self.write("exif.png", png_bytes(10, 10, exif=True))
        self.assertHas(self.check(asset(path="exif.png"))["warnings"], "EXIF metadata")
        self.write("r.mp4", mp4_bytes(1920, 1080, 10, matrix=ROTATE_90))
        self.write("s.vtt", GOOD_VTT)
        result = self.check(asset(path="r.mp4", kind="video", targets=["youtube_shorts"], subtitles=[{"path": "s.vtt"}]))
        self.assertTrue(result["valid"], result["errors"])  # Rotated phone video is vertical on display.
        self.assertTrue(result["assets"][0]["notes"])
        self.assertHas(self.check(asset(path="../outside.png"))["errors"], "escapes the manifest directory")
        self.assertHas(self.check(asset(path="/etc/hostname"))["errors"], "must be relative")
        self.assertHas(self.check(asset(path="missing.png"))["errors"], "not found")
        self.write("café ☕.png", png_bytes(1200, 627))
        self.write("card.png", png_bytes(1200, 627))
        self.assertTrue(self.check(asset(path="café ☕.png"))["valid"])
        self.assertHas(self.check(asset(api_token="x"))["errors"], "secret-like fields are forbidden")
        self.assertHas(self.check(asset(targets=["myspace"]))["warnings"], "no verified media constraint table")
        self.assertHas(self.check(asset(kind="audio"))["errors"], "kind must be image, video or subtitle")
        self.assertHas(validate_media_manifest({"schema_version": 2, "assets": []}, self.dir)["errors"],
                       "schema_version must be 1")


class CodecFrameRateRuleTests(MediaFixture):
    def video(self, name: str, data: bytes, targets: list[str]) -> dict:
        self.write(name, data)
        self.write("s.vtt", GOOD_VTT)
        return self.check(asset(path=name, kind="video", targets=targets, subtitles=[{"path": "s.vtt"}]))

    def test_good_h264_and_hevc_pass_instagram_and_tiktok(self):
        for fourcc in (b"avc1", b"hvc1"):
            result = self.video("ok.mp4", av_mp4(fourcc), ["instagram", "tiktok"])
            self.assertTrue(result["valid"], result["errors"])
            report = result["assets"][0]
            self.assertEqual((report["frame_rate_fps"], report["audio_codec"], report["fast_start"]),
                             (30.0, "aac", True))
            for target in ("instagram", "tiktok"):
                self.assertIn("video_codecs", report["targets"][target]["checked"])
                self.assertIn("frame_rate_fps", report["targets"][target]["checked"])
            self.assertIn("audio_codecs", report["targets"]["tiktok"]["skipped"])  # Not documented.
            self.assertHas(result["warnings"], "tiktok video constraint 'audio_codecs' skipped")

    def test_codec_audio_and_frame_rate_violations_are_sourced_errors(self):
        result = self.video("vp9.mp4", av_mp4(b"vp09"), ["instagram", "tiktok"])
        self.assertHas(result["errors"], "instagram video: video codec vp9 (vp09) not in allowed h264, hevc")
        self.assertLacks(result["errors"], "tiktok video: video codec")
        self.assertHas(result["errors"], "[source: https://developers.facebook.com/")
        result = self.video("av1.mp4", av_mp4(b"av01"), ["tiktok"])
        self.assertHas(result["errors"], "tiktok video: video codec av1 (av01) not in allowed h264, hevc, vp8, vp9")
        slow = av_mp4(fps_ticks=[(150, 2000)])  # 15 fps.
        result = self.video("slow.mp4", slow, ["instagram", "tiktok"])
        self.assertHas(result["errors"], "instagram video: average frame rate 15.0 fps outside 23..60 fps")
        self.assertHas(result["errors"], "tiktok video: average frame rate 15.0 fps outside 23..60 fps")
        fast = av_mp4(fps_ticks=[(1200, 250)])  # 120 fps.
        self.assertHas(self.video("fast.mp4", fast, ["tiktok"])["errors"], "average frame rate 120.0 fps outside")
        result = self.video("hi.mp4", av_mp4(audio=audio_entry(rate=96000, channels=6, qt_version=2)), ["instagram"])
        self.assertHas(result["errors"], "audio sample rate 96000 Hz exceeds 48000 Hz")
        self.assertHas(result["errors"], "audio has 6 channels; maximum is 2")
        result = self.video("opus.mp4", av_mp4(audio=audio_entry(b"Opus")), ["instagram"])
        self.assertHas(result["errors"], "audio codec opus (Opus) not in allowed aac")

    def test_youtube_recommendations_warn_and_never_block(self):
        late_hevc = av_mp4(b"hvc1", moov_first=False, audio=audio_entry(b"ac-3", 44100))
        result = self.video("yt.mp4", late_hevc, ["youtube_shorts"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertHas(result["warnings"], "video codec hevc (hvc1) not in recommended h264")
        self.assertHas(result["warnings"], "audio codec ac3 (ac-3) not in recommended aac, opus")
        self.assertHas(result["warnings"], "audio sample rate 44100 Hz; recommended 48000 Hz")
        self.assertHas(result["warnings"], "youtube_shorts video: moov box must precede mdat")
        self.assertHas(result["warnings"], "youtube_shorts video constraint 'frame_rate_fps' skipped")
        self.assertHas(self.video("ig.mp4", av_mp4(moov_first=False), ["instagram"])["errors"],
                       "instagram video: moov box must precede mdat")

    def test_variable_frame_rate_warns_only_when_detected(self):
        vfr = av_mp4(fps_ticks=[(150, 1000), (100, 1500)])  # Sample durations span 20..30 fps, average 25.
        result = self.video("vfr.mp4", vfr, ["instagram", "bluesky"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertHas(result["warnings"], "instagram video: variable frame rate detected (sample durations span "
                                           "20.0..30.0 fps)")
        self.assertLacks(result["warnings"], "bluesky video: variable frame rate")  # No documented range.
        self.assertLacks(self.video("cfr.mp4", av_mp4(), ["instagram"])["warnings"], "variable frame rate")

    def test_unknown_values_and_undocumented_platforms_skip_with_warnings(self):
        result = self.video("legacy.mp4", mp4_bytes(1080, 1920, 10), ["instagram", "bluesky", "linkedin"])
        self.assertHas(result["warnings"], "'video_codecs' skipped (video codec unknown")
        self.assertHas(result["warnings"], "'frame_rate_fps' skipped (frame rate unknown")
        self.assertHas(result["warnings"], "video_codec unknown (header not parseable)")
        self.assertHas(result["warnings"], "bluesky video constraint 'video_codecs' skipped (value not established")
        self.assertHas(result["warnings"], "linkedin video constraint 'frame_rate_fps' skipped (value not established")
        self.assertLacks(result["errors"], "codec")
        result = self.video("enc.mp4", av_mp4(raw_video_entry=box(b"encv", b"\x00" * 78)), ["instagram"])
        self.assertHas(result["warnings"], "video_codec unknown (sample entry encv not recognized)")
        silent = self.video("silent.mp4", av_mp4(audio=b""), ["instagram"])
        self.assertTrue(silent["valid"], silent["errors"])
        self.assertIn("audio_codecs", silent["assets"][0]["targets"]["instagram"]["checked"])
        bad = struct.pack(">I", 0x7FFFFFF0) + video_entry(b"avc1")[4:]
        result = self.video("bad.mp4", av_mp4(raw_video_entry=bad), ["tiktok"])
        self.assertHas(result["warnings"], "header parsing stopped early (malformed size")

    def test_codec_rows_cover_requested_platforms(self):
        for target in ("instagram", "tiktok", "youtube_shorts", "linkedin", "bluesky"):
            rows = CONSTRAINTS[target]["video"]
            self.assertIn("video_codecs", rows, target)
            self.assertIn("frame_rate_fps", rows, target)
        self.assertEqual(CONSTRAINTS["instagram"]["video"]["frame_rate_fps"]["value"], [23, 60])
        self.assertEqual(CONSTRAINTS["tiktok"]["video"]["frame_rate_fps"]["value"], [23, 60])
        self.assertIsNone(CONSTRAINTS["linkedin"]["video"]["video_codecs"]["value"])
        self.assertEqual(CONSTRAINTS["youtube_shorts"]["video"]["video_codecs"]["severity"], "warning")


def srt(*cues: tuple[str, str, str]) -> str:
    return "\n\n".join(f"{n}\n{start} --> {end}\n{text}" for n, (start, end, text) in enumerate(cues, 1)) + "\n"


SOURCE_CUES = (("00:00:00,000", "00:00:02,000", "ReachWeaver checks 3 platforms"),
               ("00:00:02,000", "00:00:04,500", "Docs: https://github.com/MSKazemi/reachweaver."),
               ("00:00:04,500", "00:00:06,000", "Saves 1,500 minutes"))
IT_VTT = ("WEBVTT\n\nNOTE translator: kept the source spotting\n\nintro\n00:00.000 --> 00:02.300 line:90%\n"
          "<i>ReachWeaver</i> controlla 3 piattaforme\n\n00:02.000 --> 00:04.500\n"
          "Documentazione: https://github.com/MSKazemi/reachweaver\n\n00:04.500 --> 00:06.000\n"
          "Risparmia 1.500 minuti &amp; tempo\n")
FA_SRT = srt(("00:00:00,000", "00:00:02,000", "ReachWeaver ۳ پلتفرم را "
                                                 "بررسی می‌کند"),
             ("00:00:02,000", "00:00:04,500", "https://github.com/MSKazemi/reachweaver"),
             ("00:00:04,500", "00:00:06,000", "۱٬۵۰۰ دقیقه"))


class SubtitleTranslationTests(MediaFixture):
    """Translated SRT/WebVTT tracks are checked cue by cue against the source-locale track."""

    def track(self, translated: str, name: str = "demo.it.srt", locale: str = "it", **sub) -> dict:
        self.write("demo.en.srt", srt(*SOURCE_CUES))
        self.write(name, translated)
        subtitle = asset(id="subs", path="demo.en.srt", kind="subtitle", targets=["linkedin"], alt_text="",
                         subtitles=[{"path": name, "locale": locale, **sub}])
        return subtitle

    def check_track(self, translated: str, name: str = "demo.it.srt", locale: str = "it", sub=None, **top) -> dict:
        top.setdefault("preserve_terms", ["ReachWeaver"])
        return self.check(self.track(translated, name, locale, **(sub or {})), **top)

    def test_parse_cues_skips_notes_identifiers_and_keeps_text(self):
        cues = media.parse_subtitle_cues(IT_VTT, "vtt")
        self.assertEqual([(c["number"], c["start"], c["end"]) for c in cues],
                         [(1, 0.0, 2.3), (2, 2.0, 4.5), (3, 4.5, 6.0)])
        self.assertEqual(cues[0]["text"], "<i>ReachWeaver</i> controlla 3 piattaforme")
        self.assertEqual(media.parse_subtitle_cues("WEBVTT\n", "vtt"), [])

    def test_aligned_translations_with_markup_and_localized_digits_pass(self):
        self.write("demo.en.srt", srt(*SOURCE_CUES))
        self.write("demo.it.vtt", IT_VTT)
        self.write("demo.fa.srt", FA_SRT)
        subtitle = asset(id="subs", path="demo.en.srt", kind="subtitle", targets=["linkedin"], alt_text="",
                         subtitles=[{"path": "demo.it.vtt", "locale": "it"}, {"path": "demo.fa.srt", "locale": "fa"}])
        result = self.check(subtitle, preserve_terms=["ReachWeaver"], target_locales=["en", "it", "fa"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertLacks(result["warnings"], "source cue")  # 1,500 = 1.500 = Persian 1٬500; markup ignored
        reports = {r["locale"]: r for r in result["assets"][0]["subtitle_translations"]}
        self.assertEqual(set(reports), {"it", "fa"})
        self.assertEqual((reports["it"]["paired"], reports["it"]["mistimed"], reports["it"]["source_path"]),
                         (3, 0, "demo.en.srt"))  # cue 1 ends 0.3 s late: inside the 0.5 s default

    def test_dropped_term_number_and_url_are_warnings_per_cue(self):
        bad = srt(("00:00:00,000", "00:00:02,000", "Il tool controlla 4 piattaforme"),
                  ("00:00:02,000", "00:00:04,500", "Documentazione: https://example.org/altro"),
                  ("00:00:04,500", "00:00:06,000", "Risparmia molti minuti"))
        result = self.check_track(bad)
        self.assertTrue(result["valid"], result["errors"])  # content findings need human judgment
        prefix = "subtitle demo.it.srt (it vs source demo.en.srt): "
        self.assertHas(result["warnings"], prefix + "cue 1 (source cue 1) drops preserved term 'ReachWeaver'")
        self.assertHas(result["warnings"], "cue 1 (source cue 1) numbers differ from the source cue "
                                           "(missing 3; added 4)")
        self.assertHas(result["warnings"], "cue 2 (source cue 2) drops or changes URL "
                                           "https://github.com/MSKazemi/reachweaver")
        self.assertHas(result["warnings"], "cue 3 (source cue 3) numbers differ from the source cue "
                                           "(missing 1, 500)")
        # The changed link is both a dropped source URL and a URL the source cue never had.
        self.assertHas(result["warnings"], "cue 2 (source cue 2) adds URL https://example.org/altro")
        report = result["assets"][0]["subtitle_translations"][0]
        self.assertEqual((report["errors"], report["warnings"]), (0, 5))

    def test_a_url_added_by_the_translation_is_a_warning(self):
        # Review finding (2026-10-09): only dropped URLs were reported, so a translator (or a compromised
        # translation step) could inject a phishing link into a cue whose source has no link at all.
        source = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\nVisit our site\n", "srt")
        target = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\n"
                                           "Besuche https://evil.example/phish jetzt\n", "srt")
        errors, warnings, _ = media.compare_subtitle_tracks(source, target, [])
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["cue 1 (source cue 1) adds URL https://evil.example/phish that the source "
                                    "cue does not contain; links must stay identical"])
        # The same URL repeated more often than in the source is also an addition.
        twice = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\nhttps://a.example "
                                          "https://a.example\n", "srt")
        once = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\nhttps://a.example\n", "srt")
        self.assertHas(media.compare_subtitle_tracks(once, twice, [])[1], "adds URL https://a.example")
        self.assertEqual(media.compare_subtitle_tracks(once, once, [])[1], [])

    def test_url_warnings_never_echo_query_strings_or_userinfo(self):
        # Review follow-up (2026-10-09): a changed URL was printed whole, ?token= query string included.
        source = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\n"
                                           "https://a.example/p?token=SrcSecret123\n", "srt")
        target = media.parse_subtitle_cues("1\n00:00:01,000 --> 00:00:02,000\n"
                                           "https://user:pw@b.example/q?sig=TgtSecret456#frag\n", "srt")
        _, warnings, _ = media.compare_subtitle_tracks(source, target, [])
        text = "\n".join(warnings)
        for secret in ("SrcSecret123", "TgtSecret456", "user:pw", "frag"):
            self.assertNotIn(secret, text)
        self.assertIn("https://a.example/p?…", text)
        self.assertIn("https://b.example/q?…", text)

    def test_huge_digit_runs_and_leading_zeros_compare_without_int(self):
        # Review finding (2026-10-09): str(int(run)) raised ValueError past Python's 4300-digit limit.
        def cue(text: str) -> list[dict]:
            return media.parse_subtitle_cues(f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n", "srt")
        big = "9" * 5000
        self.assertEqual(media.compare_subtitle_tracks(cue(big), cue(big), [])[:2], ([], []))
        self.assertEqual(media.compare_subtitle_tracks(cue("007 agents"), cue("7 agenti"), [])[1], [])
        self.assertEqual(media.compare_subtitle_tracks(cue("1500 users"), cue("۱۵۰۰ کاربر"), [])[1], [])
        warnings = media.compare_subtitle_tracks(cue(big), cue("9" * 4999), [])[1]
        self.assertEqual(len(warnings), 1)
        self.assertIn("missing 99999999999999999999… (5000 digits)", warnings[0])
        self.assertIn("added 99999999999999999999… (4999 digits)", warnings[0])
        self.assertLess(len(warnings[0]), 300)
        self.assertIn("missing 10, 200; added 3", media.compare_subtitle_tracks(cue("200 and 10"), cue("3"), [])[1][0])

    def test_source_locale_matches_case_insensitively(self):
        # Review finding (2026-10-09): en-US vs en-us never matched, so the en-us track was treated as a
        # translation (or the source never found) and the real translation was not compared to it.
        self.write("demo.en.srt", srt(*SOURCE_CUES))
        self.write("demo.it.srt", srt(SOURCE_CUES[0], SOURCE_CUES[1],
                                      ("00:00:04,500", "00:00:06,000", "Risparmia 2 minuti")))
        self.write("v.mp4", mp4_bytes(1080, 1920, 10))
        video = asset(id="v", path="v.mp4", kind="video", targets=["tiktok"], alt_text="", locale="en-US",
                      subtitles=[{"path": "demo.en.srt", "locale": "en-us"}, {"path": "demo.it.srt", "locale": "it"}])
        result = self.check(video, target_locales=["en-US", "it"])
        reports = result["assets"][0]["subtitle_translations"]
        self.assertEqual([(r["locale"], r["source_path"]) for r in reports], [("it", "demo.en.srt")])
        self.assertHas(result["warnings"], "it vs source demo.en.srt): cue 3 (source cue 3) numbers differ")
        self.assertLacks(result["warnings"], "not compared")
        self.assertLacks(result["warnings"], "missing subtitle file for target locale")
        # Upper-case target locales match lower-case track locales too.
        result = self.check(video, target_locales=["en-us", "IT"])
        self.assertLacks(result["warnings"], "missing subtitle file for target locale")

    def test_terms_absent_from_the_source_cue_are_not_required(self):
        result = self.check_track(srt(*SOURCE_CUES), preserve_terms=["ReachWeaver", "Kubernetes"])
        self.assertTrue(result["valid"], result["errors"])
        self.assertLacks(result["warnings"], "drops preserved term")

    def test_missing_and_extra_cues_are_errors_with_their_timing(self):
        missing = srt(SOURCE_CUES[0], SOURCE_CUES[2])
        result = self.check_track(missing)
        self.assertFalse(result["valid"])
        self.assertHas(result["errors"], "demo.it.srt (it vs source demo.en.srt): has 2 cues; the source track has 3")
        self.assertHas(result["errors"], "missing a cue for source cue 2 (00:00:02.000 --> 00:00:04.500)")
        self.assertLacks(result["errors"], "missing a cue for source cue 3")  # re-paired by timing
        extra = srt(*SOURCE_CUES, ("00:00:06,500", "00:00:08,000", "Grazie"))
        result = self.check_track(extra)
        self.assertHas(result["errors"], "has 4 cues; the source track has 3")
        self.assertHas(result["errors"], "extra cue 4 (00:00:06.500 --> 00:00:08.000) has no source cue within 0.5s")
        report = result["assets"][0]["subtitle_translations"][0]
        self.assertEqual((report["paired"], report["missing"], report["extra"]), (3, 0, 1))

    def test_timing_drift_beyond_tolerance_is_an_error_and_tolerance_is_configurable(self):
        drifted = srt(SOURCE_CUES[0], ("00:00:02,800", "00:00:04,500", SOURCE_CUES[1][2]), SOURCE_CUES[2])
        result = self.check_track(drifted)
        self.assertHas(result["errors"], "cue 2 (00:00:02.800 --> 00:00:04.500) is 0.800s off source cue 2 "
                                         "(00:00:02.000 --> 00:00:04.500); tolerance 0.5s")
        self.assertTrue(self.check_track(drifted, subtitle_timing_tolerance_ms=1000)["valid"])
        for bad in (-1, 5001, 0.5, True, "500"):
            with self.subTest(tolerance=bad):
                self.assertHas(self.check_track(srt(*SOURCE_CUES), subtitle_timing_tolerance_ms=bad)["errors"],
                               "subtitle_timing_tolerance_ms must be an integer from 0 to 5000")

    def test_independent_alignment_downgrades_structure_to_warnings(self):
        retimed = srt(("00:00:00,000", "00:00:04,500", "ReachWeaver controlla 3 piattaforme: "
                                                       "https://github.com/MSKazemi/reachweaver"),
                      SOURCE_CUES[2])
        result = self.check_track(retimed, sub={"alignment": "independent"})
        self.assertTrue(result["valid"], result["errors"])
        self.assertHas(result["warnings"], "has 2 cues; the source track has 3")
        self.assertEqual(result["assets"][0]["subtitle_translations"][0]["alignment"], "independent")
        self.assertHas(self.check_track(srt(*SOURCE_CUES), sub={"alignment": "loose"})["errors"],
                       "alignment must be 'source'")

    def test_without_a_valid_source_track_translations_are_flagged_not_compared(self):
        self.write("demo.it.srt", srt(*SOURCE_CUES))
        self.write("v.mp4", mp4_bytes(1080, 1920, 10))
        video = asset(id="v", path="v.mp4", kind="video", targets=["tiktok"], alt_text="",
                      subtitles=[{"path": "demo.it.srt", "locale": "it"}])
        result = self.check(video)
        self.assertHas(result["warnings"], "translated subtitle tracks not compared: no valid en source-locale track")
        self.assertEqual(result["assets"][0]["subtitle_translations"], [])
        # A source track that still holds a placeholder is an error and is not used as a reference.
        subtitle = self.track(srt(("00:00:00,000", "00:00:02,000", "Ciao")))
        self.write("demo.en.srt", srt(("00:00:00,000", "00:00:02,000", "{{write: subtitle}}")))
        result = self.check(subtitle)
        self.assertHas(result["errors"], "contains an unresolved placeholder")
        self.assertHas(result["warnings"], "translated subtitle tracks not compared")
        self.assertLacks(result["errors"], "vs source")

    def test_findings_per_track_are_capped_with_a_counted_remainder(self):
        source = [{"number": n, "start": float(n), "end": n + 0.9, "text": f"{n}"} for n in range(1, 26)]
        shifted = [{**cue, "start": cue["start"] + 0.7, "end": cue["end"] + 0.7} for cue in source]
        errors, warnings, summary = media.compare_subtitle_tracks(source, shifted, [])
        self.assertEqual((len(errors), warnings, summary["mistimed"]), (25, [], 25))
        capped = media._capped(errors, "label")
        self.assertEqual(len(capped), media.MAX_TRACK_FINDINGS + 1)
        self.assertEqual(capped[-1], "label: 5 more finding(s) of this severity not listed")


class MediaCliTests(MediaFixture):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network used")):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_cli_json_exit_codes_and_no_network(self):
        self.write("card.png", png_bytes(1200, 627))
        manifest = self.write("media.json", json.dumps({"schema_version": 1, "assets": [asset()]}))
        code, out, _ = self.run_cli("media-check", str(manifest), "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["valid"])
        self.assertEqual(report["assets"][0]["width"], 1200)
        manifest.write_text(json.dumps({"schema_version": 1, "assets": [asset(rights_confirmed=False)]}))
        code, out, _ = self.run_cli("media-check", str(manifest))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", out)
        self.assertIn("does not upload", out)
        manifest.write_text("{not json")
        code, _, err = self.run_cli("media-check", str(manifest))
        self.assertEqual(code, 2)
        self.assertIn("Invalid media manifest JSON", err)

    def test_module_has_no_network_or_upload_code(self):
        source = Path(media.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib", "http.client", "import socket", "requests", "subprocess"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
