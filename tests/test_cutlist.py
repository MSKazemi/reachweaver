"""`media-cutlist`: one master video -> per-platform cut list. Offline: no media file is opened or edited."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path

from reachweaver import cutlist
from reachweaver.cli import main
from reachweaver.media import CONSTRAINTS
from reachweaver.policy import ValidationError
from test_ui_data import referenced_names

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "cut-list.example.json"
PLAN = {
    "schema_version": cutlist.SCHEMA,
    "source": {"file": "media/talk one.mp4", "duration_s": 300, "width": 3840, "height": 2160, "focus_x": 0.9},
    "segments": [
        {"id": "hook", "start_s": 0, "end_s": 10, "priority": 1},
        {"id": "long", "start_s": 20, "end_s": 200, "priority": 2},
        {"id": "outro", "start_s": 250, "end_s": 260, "priority": 1},
    ],
    "targets": [{"platform": "youtube_shorts"}, {"platform": "linkedin"}, {"platform": "tiktok", "max_s": 30}],
}


def build(plan=None):
    return cutlist.build_cutlist(cutlist.validate_plan(copy.deepcopy(plan or PLAN)))


def target(result, platform):
    return next(t for t in result["targets"] if t["platform"] == platform)


class SelectionTests(unittest.TestCase):
    def test_segments_fit_the_sourced_limit_by_priority_and_stay_in_time_order(self):
        result = build()
        shorts = target(result, "youtube_shorts")
        self.assertEqual(shorts["duration"]["max"]["value"], CONSTRAINTS["youtube_shorts"]["video"]["max_duration_s"]["value"])
        self.assertEqual(shorts["duration"]["max"]["basis"], "official")
        self.assertTrue(shorts["duration"]["max"]["source"].startswith("https://"))
        self.assertEqual([k["id"] for k in shorts["keep"]], ["hook", "outro"])  # 'long' (180 s) would exceed 180 with them
        self.assertEqual(shorts["leave_out"], ["long"])
        self.assertLessEqual(shorts["duration"]["cut_s"], 180)
        linkedin = target(result, "linkedin")
        self.assertEqual([k["id"] for k in linkedin["keep"]], ["hook", "long", "outro"])

    def test_owner_max_only_shortens(self):
        tiktok = target(build(), "tiktok")
        self.assertEqual(tiktok["duration"]["cut_s"], 20)
        plan = copy.deepcopy(PLAN)
        plan["targets"] = [{"platform": "youtube_shorts", "max_s": 999}]
        shorts = target(build(plan), "youtube_shorts")
        self.assertLessEqual(shorts["duration"]["cut_s"], 180)
        self.assertTrue(any("above" in w for w in shorts["warnings"]))

    def test_too_short_and_nothing_fits_are_warned(self):
        plan = copy.deepcopy(PLAN)
        plan["segments"] = [{"id": "blip", "start_s": 0, "end_s": 1}]
        plan["targets"] = [{"platform": "linkedin"}]
        result = build(plan)
        self.assertTrue(any("minimum" in w for w in target(result, "linkedin")["warnings"]))
        plan["targets"] = [{"platform": "tiktok", "max_s": 1}]
        plan["segments"] = [{"id": "long", "start_s": 0, "end_s": 5}]
        tiktok = target(build(plan), "tiktok")
        self.assertEqual(tiktok["keep"], [])
        self.assertIsNone(tiktok["ffmpeg"])
        self.assertTrue(any("no segment fits" in w for w in tiktok["warnings"]))

    def test_no_segments_means_the_whole_video(self):
        plan = copy.deepcopy(PLAN)
        plan["segments"] = []
        self.assertEqual([k["id"] for k in target(build(plan), "linkedin")["keep"]], ["whole"])

    def test_unestablished_limit_is_unverified(self):
        plan = copy.deepcopy(PLAN)
        plan["targets"] = [{"platform": "mastodon"}]
        mastodon = target(build(plan), "mastodon")
        self.assertEqual(mastodon["duration"]["max"]["basis"], "unverified")
        self.assertTrue(any("not established" in w for w in mastodon["warnings"]))


class CropTests(unittest.TestCase):
    def test_crop_follows_focus_and_stays_inside_the_frame(self):
        box = cutlist.crop_box(3840, 2160, (9, 16), 0.9, 0.5)
        self.assertEqual((box["width"], box["height"]), (1214, 2160))
        self.assertLessEqual(box["x"] + box["width"], 3840)
        self.assertGreater(box["x"], 2000)  # pulled toward the right-hand focus
        tall = cutlist.crop_box(1080, 1920, (16, 9), 0.5, 0.0)
        self.assertEqual((tall["y"], tall["width"]), (0, 1080))
        self.assertIsNone(cutlist.crop_box(1920, 1080, (16, 9), 0.5, 0.5))
        for value in box.values():
            self.assertIsInstance(value, int)
        self.assertEqual(box["width"] % 2, 0)

    def test_scaled_down_never_up(self):
        shorts = target(build(), "youtube_shorts")
        self.assertEqual(shorts["scale_to"], [1080, 1920])  # 4K master: scaled down
        plan = copy.deepcopy(PLAN)
        plan["source"].update(width=1920, height=1080)
        small = target(build(plan), "youtube_shorts")
        self.assertIsNone(small["scale_to"])
        self.assertTrue(any("never upscales" in w for w in small["warnings"]))

    def test_aspect_defaults_and_allowed_range(self):
        result = build()
        self.assertEqual(target(result, "youtube_shorts")["aspect"]["basis"], "repo_policy")
        self.assertEqual(target(result, "tiktok")["aspect"]["basis"], "hypothesis")
        self.assertEqual(target(result, "linkedin")["aspect"], {**target(result, "linkedin")["aspect"],
                                                                 "value": "16:9", "basis": "source"})
        plan = copy.deepcopy(PLAN)
        plan["targets"] = [{"platform": "youtube_shorts", "aspect": "16:9"}]
        self.assertTrue(any("outside" in w for w in target(build(plan), "youtube_shorts")["warnings"]))


class CommandTextTests(unittest.TestCase):
    def test_ffmpeg_command_is_quoted_and_uses_only_documented_filters(self):
        shorts = target(build(), "youtube_shorts")
        argv = shlex.split(shorts["ffmpeg"])
        self.assertEqual(argv[:3], ["ffmpeg", "-i", "media/talk one.mp4"])  # a path with a space stays one argument
        graph = argv[argv.index("-filter_complex") + 1]
        filters = {part.split("=")[0].split("]")[-1] for chain in graph.split(";") for part in chain.split(",")}
        self.assertLessEqual(filters - {""}, {"trim", "atrim", "setpts", "asetpts", "concat", "crop", "scale"})
        self.assertIn("concat=n=2:v=1:a=1", graph)
        self.assertEqual(argv[-1], "media/talk one.youtube_shorts.mp4")
        self.assertIn("+faststart", argv)

    def test_no_audio_drops_the_audio_chain(self):
        plan = copy.deepcopy(PLAN)
        plan["source"]["has_audio"] = False
        command = target(build(plan), "linkedin")["ffmpeg"]
        self.assertNotIn("atrim", command)
        self.assertIn("a=0", command)
        self.assertNotIn("[ac]", command)


class ValidationTests(unittest.TestCase):
    def bad(self, mutate):
        plan = copy.deepcopy(PLAN)
        mutate(plan)
        with self.assertRaises(ValidationError):
            cutlist.validate_plan(plan)

    def test_invalid_plans_are_refused(self):
        self.bad(lambda p: p.update(schema_version="v0"))
        self.bad(lambda p: p["segments"].append({"id": "overlap", "start_s": 5, "end_s": 15}))
        self.bad(lambda p: p["segments"].append({"id": "hook", "start_s": 270, "end_s": 280}))
        self.bad(lambda p: p["segments"].append({"id": "late", "start_s": 290, "end_s": 400}))
        self.bad(lambda p: p["targets"].append({"platform": "youtube"}))
        self.bad(lambda p: p["targets"].append({"platform": "linkedin"}))
        self.bad(lambda p: p["source"].update(duration_s=float("nan")))
        self.bad(lambda p: p["source"].update(file="a\nb.mp4"))
        self.bad(lambda p: p["source"].update(extra=1))
        self.bad(lambda p: p["segments"][0].update(priority=True))

    def test_module_opens_no_media_and_names_no_network_or_send_path(self):
        names = referenced_names(cutlist.__file__)
        self.assertEqual(names & {"socket", "urlopen", "urllib", "requests", "subprocess", "run", "Popen", "system",
                                  "connect", "publish", "credential"}, set())


class CommandTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_example_runs_and_never_creates_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state" / "never.sqlite"
            code, out, err = self.run_cli("--db", str(db), "media-cutlist", str(EXAMPLE), "--json")
            self.assertEqual(code, 0, err)
            self.assertFalse(db.exists())
        result = json.loads(out)
        self.assertEqual(len(result["targets"]), 5)
        code, text, _ = self.run_cli("media-cutlist", str(EXAMPLE))
        self.assertEqual(code, 0)
        self.assertIn("nothing was opened, cut, encoded or uploaded", text)

    def test_bad_file_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(self.run_cli("media-cutlist", str(bad))[0], 2)


if __name__ == "__main__":
    unittest.main()
