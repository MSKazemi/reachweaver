"""media-storyboard: offline storyboard scaffold, subtitle skeleton, manifest, crop and safe-area checks.

Golden geometry values are worked by hand in the comments. No media is generated, no
network is used and every fixture lives in a temporary directory.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reachweaver import storyboard  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.media import media_check, validate_subtitle_text  # noqa: E402
from reachweaver.policy import PLACEHOLDER, ValidationError  # noqa: E402
from reachweaver.storyboard import (  # noqa: E402
    SAFE_ZONES, build_storyboard, check_safe_area, check_storyboard, frame_size, load_safe_zones,
    parse_aspect, viewport_to_frame, write_storyboard,
)
from test_media import av_mp4, png_bytes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "examples" / "campaign.reachweaver.json"
# Geometry goldens use a fixed, hand-picked UNVERIFIED zone so they do not move when the sourced
# built-in numbers are re-checked; the sourced table has its own tests below.
TIKTOK = {"tiktok": storyboard._zone(top=0.08, bottom=0.24, left=0.06, right=0.13)}


def campaign(**overrides) -> dict:
    data = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
    data.update(overrides)
    return data


def box(kind: str, x: float, y: float, w: float, h: float, name: str = "b") -> dict:
    return {"id": name, "kind": kind, "x": x, "y": y, "w": w, "h": h}


class GeometryGoldenTests(unittest.TestCase):
    def test_aspect_parsing_and_frame_size(self):
        self.assertEqual(parse_aspect("9:16"), (9, 16))
        self.assertEqual(frame_size((9, 16)), (1080, 1920))
        self.assertEqual(frame_size((4, 5)), (1080, 1350))
        self.assertEqual(frame_size((1, 1)), (1080, 1080))
        for bad in ("16x9", "0:1", "9:0", "-9:16", None, "100000:1"):
            with self.assertRaises(ValidationError):
                parse_aspect(bad)

    def test_cover_crop_of_square_and_landscape_frames_in_a_vertical_player(self):
        # 1:1 in a 9:16 player, centered fill: visible width = 0.5625, centered -> 0.21875..0.78125.
        visible = viewport_to_frame(1.0, 9 / 16, "cover", (0, 0, 1, 1))
        self.assertEqual(visible, {"x0": 0.21875, "y0": 0.0, "x1": 0.78125, "y1": 1.0})
        # 16:9 in a 9:16 player: visible width = 0.5625 / 1.7778 = 0.316406; x0 = 0.341797.
        visible = viewport_to_frame(16 / 9, 9 / 16, "cover", (0, 0, 1, 1))
        self.assertAlmostEqual(visible["x1"] - visible["x0"], 0.316406, places=5)
        self.assertAlmostEqual(visible["x0"], 0.341797, places=5)
        # 9:16 in 9:16 is the identity mapping.
        self.assertEqual(viewport_to_frame(9 / 16, 9 / 16, "cover", (0.06, 0.14, 0.94, 0.65)),
                         {"x0": 0.06, "y0": 0.14, "x1": 0.94, "y1": 0.65})

    def test_contain_letterbox_maps_overlay_zones_into_the_frame(self):
        # 1:1 letterboxed in 9:16: frame is 0.5625 high, top offset 0.21875. Instagram's
        # bottom zone ends at viewport 0.65 -> frame (0.65 - 0.21875) / 0.5625 = 0.766667.
        safe = viewport_to_frame(1.0, 9 / 16, "contain", (0.06, 0.14, 0.94, 0.65))
        self.assertAlmostEqual(safe["y1"], 0.766667, places=5)
        self.assertEqual(safe["y0"], 0.0)  # the top zone (0.14) lies in the letterbox bar
        self.assertAlmostEqual(safe["x0"], 0.06, places=6)
        with self.assertRaises(ValidationError):
            viewport_to_frame(1.0, 9 / 16, "stretch", (0, 0, 1, 1))

    def test_text_under_tiktok_bottom_overlay_is_an_error_with_pixels(self):
        result = check_safe_area([box("text", 0.1, 0.8, 0.5, 0.1, "caption")], (9, 16), ["tiktok"], TIKTOK)
        self.assertFalse(result["valid"])
        finding = result["findings"][0]
        # Safe bottom edge = 1 - 0.24 = 0.76; box bottom = 0.9 -> 0.14 x 1920 = 269 px.
        self.assertEqual(finding["reason"], "under_ui_overlay")
        self.assertEqual(finding["outside_by"], {"bottom": 0.14})
        self.assertEqual(finding["outside_by_px"], {"bottom": 269})
        self.assertEqual(finding["zone_status"], "unverified")
        self.assertIn("unverified safe zone", result["errors"][0])
        # An unverified zone is surfaced as a warning even when the layout is otherwise judged.
        self.assertTrue(any("safe zone is UNVERIFIED" in w for w in result["warnings"]), result["warnings"])

    def test_focal_box_under_right_rail_is_a_warning_and_centered_boxes_pass(self):
        result = check_safe_area([box("focal", 0.7, 0.4, 0.25, 0.2, "face"),
                                  box("text", 0.2, 0.3, 0.5, 0.1, "title")], (9, 16), ["tiktok"], TIKTOK)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(len(result["findings"]), 1)
        # Safe right edge = 1 - 0.13 = 0.87; box right = 0.95 -> 0.08 x 1080 = 86 px.
        self.assertEqual(result["findings"][0]["outside_by_px"], {"right": 86})
        self.assertTrue(any("focal box 'face'" in w for w in result["warnings"]), result["warnings"])

    def test_square_frame_text_near_the_edge_is_cropped_away(self):
        # Visible x0 = 0.21875; box x = 0.05 -> 0.16875 x 1080 = 182 px cropped.
        result = check_safe_area([box("text", 0.05, 0.4, 0.3, 0.1)], (1, 1), ["instagram"], SAFE_ZONES)
        finding = result["findings"][0]
        self.assertEqual(finding["reason"], "cropped")
        self.assertEqual(finding["outside_by_px"], {"left": 182})
        contain = check_safe_area([box("text", 0.05, 0.4, 0.3, 0.1)], (1, 1), ["instagram"], SAFE_ZONES,
                                  fit="contain")
        self.assertEqual(contain["findings"][0]["reason"], "under_ui_overlay")  # only the 6% side zone

    def test_combined_safe_rect_is_the_intersection_and_unknown_targets_skip(self):
        result = check_safe_area([], (9, 16), ["youtube_shorts", "tiktok", "instagram", "linkedin"], SAFE_ZONES)
        # x0 = TikTok left 80/720 rounded up = 0.111112; y0 = YouTube top 288/1920 = 0.15;
        # x1 = 1 - TikTok right (80 + 120)/720 = 1 - 0.277778; y1 = 1 - 0.35 (Instagram and YouTube).
        self.assertEqual(result["combined_safe_rect"], {"x0": 0.111112, "y0": 0.15, "x1": 0.722222, "y1": 0.65})
        self.assertEqual(result["targets"]["linkedin"], {"status": "no_zone_data"})
        self.assertTrue(any("linkedin: no UI-overlay safe-zone data" in w for w in result["warnings"]))

    def test_invalid_boxes_are_rejected(self):
        for bad in (box("logo", 0.1, 0.1, 0.1, 0.1), box("text", 0.9, 0.1, 0.2, 0.1),
                    box("text", 0.1, 0.1, 0, 0.1), {"kind": "text", "x": "0.1", "y": 0, "w": 0.1, "h": 0.1},
                    box("text", float("nan"), 0.1, 0.1, 0.1), "box"):
            with self.assertRaises(ValidationError):
                check_safe_area([bad], (9, 16), ["tiktok"], TIKTOK)


class SafeZoneSourceTests(unittest.TestCase):
    def test_every_sourced_built_in_zone_has_url_access_date_and_placement(self):
        self.assertEqual(set(SAFE_ZONES), {"tiktok", "instagram", "youtube_shorts"})
        for target, zone in SAFE_ZONES.items():
            with self.subTest(target=target):
                self.assertIn(zone["status"], ("sourced", "unverified"))
                if zone["status"] != "sourced":
                    self.assertIsNone(zone["source"])
                    self.assertIn("no first-party source", zone["note"])
                    continue
                self.assertTrue(zone["source"].startswith("https://"), zone["source"])
                self.assertEqual(zone["verified_date"], "2026-10-09")
                self.assertEqual(zone["placement"], "ads")  # every built-in source is a paid-ad spec
                self.assertIn("not organic", zone["note"])
                self.assertTrue(zone["source_document"] and zone["evidence"])
                for key in ("top", "bottom", "left", "right"):
                    self.assertTrue(0 <= zone[key] < 0.5, (key, zone[key]))

    def test_sourced_fractions_are_never_smaller_than_the_stated_numbers(self):
        # Hand-worked from each source: TikTok template 720x1280 (160/440/80 + 120 px action column),
        # Meta 14/35/6 percent, Google 1080x1920 image (288/672/48/192 px).
        expected = {"tiktok": (0.125, 0.34375, 0.111112, 0.277778),
                    "instagram": (0.14, 0.35, 0.06, 0.06),
                    "youtube_shorts": (0.15, 0.35, 0.044445, 0.177778)}
        for target, values in expected.items():
            zone = SAFE_ZONES[target]
            self.assertEqual((zone["top"], zone["bottom"], zone["left"], zone["right"]), values)
        tiktok = SAFE_ZONES["tiktok"]["stated"]
        self.assertGreaterEqual(SAFE_ZONES["tiktok"]["right"],
                                (tiktok["right"] + tiktok["right_action_column"]["width"]) / 720)
        self.assertGreaterEqual(SAFE_ZONES["tiktok"]["left"], tiktok["left"] / 720)
        # docs/MEDIA-ASSETS.md worked example: safe bottom edge = 1 - 0.34375 = 0.65625; a text box
        # ending at y = 0.9 is 0.24375 x 1920 = 468 px under TikTok's sourced bottom overlay.
        finding = check_safe_area([box("text", 0.2, 0.8, 0.4, 0.1)], (9, 16), ["tiktok"], SAFE_ZONES)["findings"][0]
        self.assertEqual((finding["outside_by"], finding["outside_by_px"], finding["zone_status"]),
                         ({"bottom": 0.24375}, {"bottom": 468}, "sourced"))
        shorts = SAFE_ZONES["youtube_shorts"]["stated"]
        self.assertGreaterEqual(SAFE_ZONES["youtube_shorts"]["left"], shorts["left"] / 1080)
        self.assertGreaterEqual(SAFE_ZONES["youtube_shorts"]["right"], shorts["right"] / 1080)

    def test_ads_only_and_unverified_zones_are_surfaced_as_warnings(self):
        result = check_safe_area([], (9, 16), ["tiktok", "instagram", "youtube_shorts"], SAFE_ZONES)
        for target in ("tiktok", "instagram", "youtube_shorts"):
            self.assertTrue(any(w.startswith(f"{target}: UI-overlay safe zone comes from a paid-ad creative spec")
                                and SAFE_ZONES[target]["source"] in w and "accessed 2026-10-09" in w
                                for w in result["warnings"]), result["warnings"])
        self.assertTrue(result["valid"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zones.json"
            path.write_text(json.dumps({"tiktok": {"top": 0.1, "bottom": 0.3, "left": 0.05, "right": 0.15}}))
            zones = load_safe_zones(path)
        result = check_safe_area([], (9, 16), ["tiktok"], zones)
        self.assertTrue(any("tiktok: UI-overlay safe zone is UNVERIFIED" in w for w in result["warnings"]))
        markdown = build_storyboard(campaign(), zones=zones, out_dir=Path(tmp) / "sb")["markdown"]
        self.assertIn("| tiktok | unverified |", markdown)
        self.assertIn("| instagram | sourced | ads |", markdown)

    def test_owner_override_needs_a_public_source_and_date_to_count_as_sourced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zones.json"
            path.write_text(json.dumps({
                "tiktok": {"top": 0.1, "bottom": 0.3, "left": 0.05, "right": 0.15,
                           "source": "https://example.org/safe-zones", "verified_date": "2026-10-08"},
                "instagram": {"top": 0.1, "bottom": 0.3, "left": 0.05, "right": 0.05}}))
            zones = load_safe_zones(path)
            self.assertEqual(zones["tiktok"]["status"], "owner_sourced")
            self.assertEqual(zones["tiktok"]["bottom"], 0.3)
            self.assertEqual(zones["instagram"]["status"], "unverified")
            self.assertEqual(zones["youtube_shorts"], SAFE_ZONES["youtube_shorts"])
            # Review finding (2026-10-09): the owner's source URL went into the Markdown table raw, so a
            # pipe, backtick or newline (urlparse ignores tab/CR/LF) broke or added table cells.
            path.write_text(json.dumps({"tiktok": {
                "top": 0.1, "bottom": 0.3, "left": 0.05, "right": 0.15, "verified_date": "2026-10-08",
                "source": "https://example.org/zones|evil`code`[x](https://evil.example)\n| injected | row |"}}))
            zones = load_safe_zones(path)
            self.assertEqual(zones["tiktok"]["status"], "owner_sourced")
            markdown = build_storyboard(campaign(), zones=zones, out_dir=Path(tmp) / "sb")["markdown"]
            rows = [line for line in markdown.splitlines() if line.startswith("| tiktok |")]
            self.assertEqual(len(rows), 1, rows)
            self.assertNotIn("\n| injected | row |", markdown)
            # 10 columns -> 11 unescaped pipes; the escaped ones stay inside the source cell.
            self.assertEqual(rows[0].replace("\\|", "").count("|"), 10, rows[0])
            self.assertIn("https://example.org/zones\\|evil\\`code\\`\\[x\\]\\(https://evil.example\\) "
                          "\\| injected \\| row \\| (2026-10-08)", rows[0])
            for bad in ({"tiktok": {"top": 0.6, "bottom": 0.1, "left": 0, "right": 0}},
                        {"myspace": {"top": 0.1, "bottom": 0.1, "left": 0, "right": 0}},
                        {"tiktok": {"top": 0.1, "bottom": 0.1, "left": 0, "right": 0, "api_token": "x"}}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValidationError):
                    load_safe_zones(path)


class StoryboardScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def build(self, data: dict | None = None, **kwargs) -> dict:
        return build_storyboard(data or campaign(), out_dir=self.dir / "sb", **kwargs)

    def test_scenes_are_contiguous_grounded_and_placeholder_only(self):
        evidence = [{"id": f"e{n}", "url": "https://github.com/MSKazemi/reachweaver", "claim": f"claim {n}"}
                    for n in range(1, 5)]
        result = self.build(campaign(evidence=evidence), duration_s=40)
        scenes = result["storyboard"]["scenes"]
        self.assertEqual([s["role"] for s in scenes], ["hook", "beat", "beat", "beat", "cta"])
        self.assertEqual([s["evidence_ids"] for s in scenes], [[], ["e1"], ["e2"], ["e3"], []])
        self.assertEqual(scenes[0]["start_ms"], 0)
        self.assertEqual(scenes[-1]["end_ms"], 40_000)
        for left, right in zip(scenes, scenes[1:]):
            self.assertEqual(left["end_ms"], right["start_ms"])
        for scene in scenes:
            for key in ("on_screen_text", "voiceover", "visual"):
                self.assertTrue(PLACEHOLDER.search(scene[key]), (scene["id"], key))
        self.assertIsNone(storyboard.SEND_PATH)
        self.assertEqual(result["storyboard"]["status"], "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT")

    def test_subtitle_skeleton_has_valid_timing_and_matches_the_scenes(self):
        result = self.build()
        srt = result["subtitles"]["subtitles.en.srt"]
        errors, warnings, summary = validate_subtitle_text(srt, "srt")
        self.assertEqual((errors, warnings), ([], []))
        self.assertEqual(summary["cues"], len(result["storyboard"]["scenes"]))
        self.assertEqual(summary["last_end_seconds"], 30.0)
        self.assertEqual(len(PLACEHOLDER.findall(srt)), summary["cues"])
        self.assertNotIn("subtitles.en.vtt", result["subtitles"])  # no default target needs WebVTT

    def test_bluesky_target_adds_a_valid_webvtt_skeleton(self):
        result = self.build(video_targets=["bluesky", "linkedin"])
        vtt = result["subtitles"]["subtitles.en.vtt"]
        self.assertTrue(vtt.startswith("WEBVTT\n"))
        self.assertEqual(validate_subtitle_text(vtt, "vtt")[:2], ([], []))
        # No zone data for these targets: both are skipped with a visible warning.
        self.assertEqual(len(result["validation"]["safe_area"]["warnings"]), 2)

    def test_manifest_is_blocked_only_by_human_work_and_carries_alt_text_placeholders(self):
        result = self.build(image_targets=["linkedin", "instagram"])
        manifest = result["manifest"]
        images = [a for a in manifest["assets"] if a["kind"] == "image"]
        self.assertEqual([a["path"] for a in images], ["image-linkedin.png", "image-instagram.jpg"])
        for item in manifest["assets"]:
            self.assertIs(item["rights_confirmed"], False)
            if item["kind"] == "video":
                self.assertNotIn("alt_text", item)
            else:
                self.assertTrue(PLACEHOLDER.search(item["alt_text"]))
        self.assertIn("limit 4086", images[0]["alt_text"])
        errors = result["validation"]["manifest_errors"]
        self.assertTrue(errors)
        for error in errors:
            self.assertTrue(any(p in error for p in storyboard.EXPECTED_SCAFFOLD_ERRORS), error)

    def test_image_and_video_specs_carry_sourced_rows(self):
        board = self.build(image_targets=["linkedin", "instagram"])["storyboard"]
        spec = board["image_specs"]["instagram"]
        self.assertEqual(spec["suggested_canvas"]["basis"], "within_sourced_limits")
        self.assertEqual(spec["constraints"]["aspect_ratio"]["value"], [0.8, 1.91])
        self.assertTrue(spec["constraints"]["aspect_ratio"]["source"].startswith("https://"))
        self.assertEqual(board["image_specs"]["linkedin"]["suggested_canvas"]["basis"], "unverified_convention")
        self.assertEqual(board["video_spec"]["frame"], {"width": 1080, "height": 1920, "aspect": "9:16"})
        self.assertEqual(set(board["video_spec"]["constraints"]), {"youtube_shorts", "tiktok", "instagram"})

    def test_generation_is_opt_in_with_local_and_paid_cost_notes(self):
        board = self.build()["storyboard"]
        self.assertIs(board["generation"]["enabled"], False)
        routes = {o["route"]: o["cost"] for o in board["generation"]["options"]}
        self.assertIn("No ReachWeaver or provider charge", routes["manual_or_local"])
        self.assertIn("pays nothing", routes["paid_provider"])
        self.assertIn("Generation is opt-in", self.build(locale="it")["markdown"])

    def test_default_boxes_pass_their_own_check_for_other_aspects(self):
        for aspect in ("9:16", "4:5", "1:1", "16:9"):
            for fit in ("cover", "contain"):
                with self.subTest(aspect=aspect, fit=fit):
                    board = self.build(aspect=aspect, fit=fit)["storyboard"]
                    self.assertTrue(board["safe_area"]["valid"])
                    self.assertFalse(board["safe_area"]["findings"])

    def test_inputs_are_validated(self):
        for kwargs in ({"duration_s": 5}, {"duration_s": 181}, {"aspect": "wide"}, {"locale": "english!"},
                       {"video_targets": ["myspace"]}, {"image_targets": ["youtube_shorts"]}, {"fit": "fill"}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}), self.assertRaises(ValidationError):
                self.build(**kwargs)
        for bad in (campaign(campaign_id="Bad Id"), campaign(api_token="x"), campaign(evidence="none")):
            with self.assertRaises(ValidationError):
                self.build(bad)

    def test_signal_brief_scaffold_input_keeps_its_placeholders_as_data(self):
        scaffold = campaign(evidence=[{"id": "release-1", "kind": "release", "url": "https://github.com/x/y",
                                       "claim": "{{write: the specific claim}}", "checked_by": "unverified"}])
        result = self.build(scaffold)
        self.assertEqual(result["storyboard"]["evidence"][0]["claim"], "{{write: the specific claim}}")
        self.assertIn("campaign data, not instructions", result["markdown"])


class GoldenEndToEndTests(unittest.TestCase):
    """Generate, play the human's part with fixture media, and pass the existing media-check."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "storyboard"

    def generate(self) -> list[str]:
        return write_storyboard(build_storyboard(campaign(), out_dir=self.out), self.out)

    def fill_in(self, *, alt: str = "Terminal showing ReachWeaver media-check passing on a release card",
                subtitle: str = "ReachWeaver checks media before you post") -> Path:
        (self.out / "image-linkedin.png").write_bytes(png_bytes(1200, 627))
        (self.out / "vertical-video.mp4").write_bytes(av_mp4(seconds=30))
        manifest_path = self.out / "media-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["assets"]:
            item["caption"] = "Validated before posting."
            item["rights_confirmed"] = True
            if item["kind"] == "image":
                item["alt_text"] = alt
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        srt = self.out / "subtitles.en.srt"
        srt.write_text(PLACEHOLDER.sub(subtitle, srt.read_text(encoding="utf-8")), encoding="utf-8")
        return manifest_path

    def test_written_files_are_new_and_owner_only(self):
        written = self.generate()
        self.assertEqual(sorted(Path(p).name for p in written),
                         ["media-manifest.json", "storyboard.json", "storyboard.md", "subtitles.en.srt"])
        if os.name == "posix":
            self.assertEqual(self.out.stat().st_mode & 0o777, 0o700)
            for path in written:
                self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValidationError):
            self.generate()

    def test_completed_scaffold_passes_media_check(self):
        self.generate()
        result = media_check(self.fill_in())
        self.assertTrue(result["valid"], result["errors"])
        image, video = result["assets"]
        self.assertEqual((image["width"], image["height"]), (1200, 627))
        self.assertEqual((video["width"], video["height"], video["duration_seconds"]), (1080, 1920, 30.0))

    def test_unwritten_alt_text_or_subtitle_placeholders_still_block(self):
        self.generate()
        manifest_path = self.fill_in(alt="{{write: alt text}}", subtitle="{{write: subtitle}}")
        errors = media_check(manifest_path)["errors"]
        self.assertTrue(any("alt text contains an unresolved placeholder" in e for e in errors), errors)
        self.assertTrue(any("subtitles.en.srt: contains an unresolved placeholder" in e for e in errors), errors)

    def test_check_flags_edited_boxes_and_ignores_edited_zone_numbers(self):
        self.generate()
        path = self.out / "storyboard.json"
        board = json.loads(path.read_text(encoding="utf-8"))
        for scene in board["scenes"]:
            for key in ("on_screen_text", "voiceover", "visual"):
                scene[key] = f"Written {key} for {scene['id']}"
        path.write_text(json.dumps(board), encoding="utf-8")
        self.fill_in()
        self.assertTrue(check_storyboard(path)["valid"], check_storyboard(path)["errors"])
        board["scenes"][0]["boxes"][0].update({"y": 0.8, "h": 0.1})  # text under every bottom overlay
        board["safe_zones"]["instagram"]["bottom"] = 0.0  # editing the file's zones must not help
        path.write_text(json.dumps(board), encoding="utf-8")
        checked = check_storyboard(path)
        self.assertFalse(checked["valid"])
        self.assertEqual({f["target"] for f in checked["safe_area"]["findings"]},
                         {"youtube_shorts", "tiktok", "instagram"})


class StoryboardCliTests(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(socket, "socket", side_effect=AssertionError("network used")):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_cli_generates_checks_and_never_opens_the_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, out = Path(tmp) / "never.sqlite", Path(tmp) / "sb"
            code, stdout, _ = self.run_cli("--db", str(db), "media-storyboard", "--campaign", str(CAMPAIGN),
                                           "--out", str(out), "--json")
            self.assertEqual(code, 0)
            summary = json.loads(stdout)
            self.assertEqual(summary["status"], "SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT")
            self.assertEqual(len(summary["files"]), 4)
            self.assertFalse(db.exists())
            code, stdout, _ = self.run_cli("media-storyboard", "--check", str(out / "storyboard.json"))
            self.assertEqual(code, 2)
            self.assertIn("STORYBOARD BLOCKED", stdout)
            code, _, err = self.run_cli("media-storyboard", "--campaign", str(CAMPAIGN), "--out", str(out))
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", err)
            code, _, err = self.run_cli("media-storyboard", "--check", str(out / "storyboard.json"),
                                        "--out", str(out))
            self.assertEqual(code, 2)
            code, _, err = self.run_cli("media-storyboard", "--campaign", str(CAMPAIGN))
            self.assertEqual(code, 2)
            self.assertIn("needs --campaign and --out", err)
            code, stdout, _ = self.run_cli("media-storyboard", "--campaign", str(CAMPAIGN), "--out",
                                           str(Path(tmp) / "text"), "--video-targets", "tiktok", "bluesky")
            self.assertEqual(code, 0)
            self.assertIn("BLOCKED as expected", stdout)
            self.assertTrue((Path(tmp) / "text" / "subtitles.en.vtt").is_file())

    def test_module_has_no_network_generation_or_publish_code(self):
        source = Path(storyboard.__file__).read_text(encoding="utf-8")
        for forbidden in ("urllib", "http.client", "import socket", "requests", "subprocess", "publishers",
                          "storage", "connect("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
