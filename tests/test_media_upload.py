"""Mock media-upload adapter: error codes, platform scoping, and no network. Fixtures are generated in temp dirs."""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reachweaver import media_upload
from reachweaver.media import CONSTRAINTS
from reachweaver.media_upload import ACTIONS, MockMediaUploadAdapter, classify
from test_media import GOOD_VTT, asset, mp4_bytes, png_bytes


class MockUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def run_pre(self, platform, *assets, files=None):
        for name, data in (files or {}).items():
            (self.dir / name).write_bytes(data) if isinstance(data, bytes) else (self.dir / name).write_text(data)
        manifest = {"schema_version": 1, "default_locale": "en", "assets": list(assets)}
        return MockMediaUploadAdapter(platform).preflight(manifest, self.dir)

    def codes(self, result, key="errors"):
        return [e["code"] for e in result[key]]

    def test_valid_image_accepted_and_flagged_simulated(self):
        res = self.run_pre("linkedin", asset(), files={"card.png": png_bytes(1200, 627)})
        self.assertTrue(res["accepted"], res["errors"])
        self.assertTrue(res["simulated"])
        self.assertFalse(res["uploaded"])
        self.assertEqual(res["asset_ids"], ["release-card"])
        self.assertIn("not consent to publish", res["note"])

    def test_unknown_platform(self):
        res = MockMediaUploadAdapter("nope").preflight({"assets": []}, self.dir)
        self.assertEqual(self.codes(res), ["UNKNOWN_PLATFORM"])
        self.assertIn("linkedin", res["errors"][0]["known_platforms"])
        self.assertFalse(res["accepted"])

    def test_no_asset_targets_platform(self):
        res = self.run_pre("bluesky", asset(targets=["linkedin"]), files={"card.png": png_bytes(10, 10)})
        self.assertEqual(self.codes(res), ["NO_ASSETS_FOR_PLATFORM"])

    def test_file_too_large_for_bluesky(self):
        big = png_bytes(10, 10) + b"\x00" * 2_100_000  # trailing bytes after IEND; header still parses
        res = self.run_pre("bluesky", asset(targets=["bluesky"]), files={"card.png": big})
        self.assertIn("FILE_TOO_LARGE", self.codes(res))
        err = next(e for e in res["errors"] if e["code"] == "FILE_TOO_LARGE")
        self.assertEqual(err["asset_id"], "release-card")
        self.assertTrue(err["action"])

    def test_alt_and_rights_codes(self):
        res = self.run_pre("linkedin", asset(alt_text="", rights_confirmed=False, license=""),
                           files={"card.png": png_bytes(1200, 627)})
        self.assertTrue({"ALT_TEXT_MISSING", "RIGHTS_NOT_CONFIRMED", "LICENSE_INVALID"} <= set(self.codes(res)))
        res = self.run_pre("linkedin", asset(alt_text="image"), files={"card.png": png_bytes(1200, 627)})
        self.assertIn("ALT_TEXT_PLACEHOLDER", self.codes(res))
        res = self.run_pre("linkedin", asset(caption="{{write: caption}}"), files={"card.png": png_bytes(1200, 627)})
        self.assertIn("PLACEHOLDER_UNRESOLVED", self.codes(res))

    def test_missing_file_and_path_escape(self):
        res = self.run_pre("linkedin", asset(path="missing.png"))
        self.assertIn("ASSET_PATH_INVALID", self.codes(res))
        res = self.run_pre("linkedin", asset(path="../x.png"))
        self.assertIn("ASSET_PATH_INVALID", self.codes(res))

    def test_video_without_captions_and_duration(self):
        vid = asset(id="v", path="v.mp4", kind="video", targets=["youtube_shorts"], alt_text="")
        res = self.run_pre("youtube_shorts", vid, files={"v.mp4": mp4_bytes(1080, 1920, 400, mdat_size=80_000)})
        codes = self.codes(res)
        self.assertIn("CAPTIONS_MISSING", codes)
        self.assertIn("DURATION_OUT_OF_RANGE", codes)

    def test_video_with_captions_has_no_caption_error(self):
        vid = asset(id="v", path="v.mp4", kind="video", targets=["youtube_shorts"], alt_text="",
                    subtitles=[{"path": "v.en.vtt", "locale": "en"}])
        res = self.run_pre("youtube_shorts", vid, files={"v.mp4": mp4_bytes(1080, 1920, 30, mdat_size=80_000),
                                                         "v.en.vtt": GOOD_VTT})
        self.assertNotIn("CAPTIONS_MISSING", self.codes(res))

    def test_other_platforms_findings_are_excluded(self):
        data = png_bytes(10, 10) + b"\x00" * 2_100_000
        both = asset(targets=["bluesky", "linkedin"])
        res = self.run_pre("linkedin", both, files={"card.png": data})
        self.assertNotIn("FILE_TOO_LARGE", self.codes(res))
        self.assertTrue(all("bluesky" not in e["message"] for e in res["errors"]))

    def test_manifest_level_error(self):
        res = MockMediaUploadAdapter("linkedin").preflight({"schema_version": 2, "assets": [asset()]}, self.dir)
        self.assertIn("MANIFEST_INVALID", self.codes(res))

    def test_classify_fallback_and_actions_complete(self):
        self.assertEqual(classify("assets[0] (a): something odd")["code"], "MEDIA_INVALID")
        self.assertEqual(classify("schema_version must be 1")["code"], "MANIFEST_INVALID")
        for rule in media_upload._RULES:
            self.assertIn(rule[1], ACTIONS)
        for action in ACTIONS.values():
            self.assertTrue(action.strip())

    def test_preflight_file_bad_json(self):
        path = self.dir / "m.json"
        path.write_text("{nope")
        res = MockMediaUploadAdapter("linkedin").preflight_file(path)
        self.assertEqual(self.codes(res), ["MANIFEST_INVALID"])

    def test_preflight_file_ok_and_no_network(self):
        (self.dir / "card.png").write_bytes(png_bytes(1200, 627))
        path = self.dir / "m.json"
        path.write_text(json.dumps({"schema_version": 1, "assets": [asset()]}))
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                mock.patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            res = MockMediaUploadAdapter("linkedin").preflight_file(path)
        self.assertTrue(res["accepted"], res["errors"])

    def test_every_known_platform_runs(self):
        (self.dir / "card.png").write_bytes(png_bytes(1200, 627))
        for platform in CONSTRAINTS:
            res = self.run_pre(platform, asset(targets=[platform]))
            self.assertIn("accepted", res)
            for e in res["errors"]:
                self.assertIn(e["code"], ACTIONS)


if __name__ == "__main__":
    unittest.main()
