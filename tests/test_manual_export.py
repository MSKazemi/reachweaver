"""Manual-export / creator-draft packages for Threads, Instagram, Facebook Pages, YouTube, TikTok.

Issues #19-#23 (offline slice). Every fixture is generated in a temporary directory. No test
contacts a network host, and the module under test has no send path.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from reachweaver import manual_export
from reachweaver.cli import main
from reachweaver.manual_export import (
    FACTS, MANUAL_PLATFORMS, STATUS, build_export, platform_preflight, render_markdown,
    threads_length, utf16_length, write_export,
)
from reachweaver.policy import ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parent))  # Reuse sibling fixtures under any runner.
from test_media import GOOD_VTT, asset, jpeg_bytes, mp4_bytes  # noqa: E402
from test_no_network import NetworkGuard  # noqa: E402

# "published" may appear only inside the negated status token / phrase.
CLAIM_WORDS = re.compile(r"\b(?:published|successfully posted|is live|went live|now live)\b", re.I)
NEGATED = re.compile(r"manual_export_not_published|not published", re.I)


def variant(platform: str, **overrides) -> dict:
    base = {
        "threads": {"platform": "threads", "text": "ReachWeaver ships offline manual export. #opensource"},
        "facebook": {"platform": "facebook", "text": "ReachWeaver release notes for our Page.",
                     "platform_options": {"target_type": "page"}},
        "instagram": {"platform": "instagram", "format": "image",
                      "text": "Release card for ReachWeaver. #opensource #python"},
        "tiktok": {"platform": "tiktok", "format": "video", "text": "How ReachWeaver checks captions #devtools"},
        "youtube": {"platform": "youtube", "format": "video", "text": "A short tour of ReachWeaver.",
                    "platform_options": {"title": "ReachWeaver in 60 seconds"}},
    }[platform]
    base = json.loads(json.dumps(base))
    base.update(overrides)
    return base


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key)
            yield from strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from strings(value)


class PreflightRules(unittest.TestCase):
    def assertBlocked(self, platform, v, fragment, meta=None):
        errors, _ = platform_preflight(platform, v, meta)
        self.assertTrue(any(fragment in e for e in errors), errors)

    def test_baseline_variants_pass_for_every_platform(self):
        for platform in MANUAL_PLATFORMS:
            with self.subTest(platform=platform):
                errors, _ = platform_preflight(platform, variant(platform))
                self.assertEqual(errors, [])

    def test_unsupported_platform_and_mismatch(self):
        errors, _ = platform_preflight("bluesky", {"text": "x"})
        self.assertIn("campaign-export", errors[0])
        self.assertBlocked("threads", dict(variant("threads"), platform="instagram"), "--platform")
        with self.assertRaises(ValidationError):
            build_export("x", {"text": "hi"})

    def test_threads_length_links_and_topic_tags(self):
        self.assertBlocked("threads", variant("threads", text="a" * 501), "limit is 500")
        self.assertEqual(platform_preflight("threads", variant("threads", text="a" * 500))[0], [])
        # Emoji count by UTF-8 bytes: 125 x 4-byte emoji = 500, one more ASCII char = 501.
        self.assertEqual(threads_length("\U0001F600" * 125), 500)
        self.assertBlocked("threads", variant("threads", text="\U0001F600" * 125 + "a"), "limit is 500")
        links = " ".join(f"https://example.org/{i}" for i in range(6))
        self.assertBlocked("threads", variant("threads", text=links), "at most 5")
        _, warnings = platform_preflight("threads", variant("threads", text="Two tags #one #two"))
        self.assertTrue(any("one topic tag" in w for w in warnings))

    def test_instagram_caption_hashtags_mentions_and_media(self):
        self.assertBlocked("instagram", variant("instagram", text="a" * 2201), "limit is 2200")
        tags = " ".join(f"#t{i}" for i in range(31))
        self.assertBlocked("instagram", variant("instagram", text=tags), "31 hashtags; limit is 30")
        self.assertEqual(platform_preflight("instagram", variant("instagram", text=tags[:-4]))[0], [])
        mentions = " ".join(f"@user{i}" for i in range(21))
        self.assertBlocked("instagram", variant("instagram", text=mentions), "limit is 20")
        self.assertBlocked("instagram", variant("instagram", format="text"), "image or video")
        missing = variant("instagram")
        del missing["format"]
        self.assertBlocked("instagram", missing, "format is required")
        _, warnings = platform_preflight("instagram", variant("instagram", text="See https://example.org/x"))
        self.assertTrue(any("tappable" in w for w in warnings))

    def test_youtube_title_description_and_tags(self):
        no_title = variant("youtube", platform_options={})
        self.assertBlocked("youtube", no_title, "platform_options.title")
        self.assertBlocked("youtube", variant("youtube", platform_options={"title": "t" * 101}), "limit is 100")
        self.assertBlocked("youtube", variant("youtube", platform_options={"title": "a <b>"}), "< or >")
        # 5000-byte limit is bytes, not characters: 2501 two-byte letters exceed it.
        self.assertBlocked("youtube", variant("youtube", text="é" * 2501), "5000 bytes")
        self.assertEqual(platform_preflight("youtube", variant("youtube", text="é" * 2500))[0], [])
        tags = {"title": "ok", "tags": ["x" * 250, "y" * 250]}
        self.assertBlocked("youtube", variant("youtube", platform_options=tags), "limit is 500")
        self.assertBlocked("youtube", variant("youtube", format="image"), "needs a video")

    def test_tiktok_requires_video_and_utf16_caption_limit(self):
        self.assertBlocked("tiktok", variant("tiktok", format="image"), "needs a video")
        self.assertEqual(utf16_length("\U0001F600"), 2)
        self.assertBlocked("tiktok", variant("tiktok", text="\U0001F600" * 1101), "limit is 2200")
        self.assertEqual(platform_preflight("tiktok", variant("tiktok", text="\U0001F600" * 1100))[0], [])

    def test_facebook_profile_and_group_targets_rejected(self):
        for target in ("profile", "personal", "group", "user"):
            with self.subTest(target=target):
                self.assertBlocked("facebook", variant("facebook", platform_options={"target_type": target}),
                                   "rejected")
        undeclared = variant("facebook", platform_options={})
        self.assertBlocked("facebook", undeclared, "must be declared as a Page")
        self.assertBlocked("facebook", undeclared, "rejected", meta={"account_type": "personal"})
        self.assertEqual(platform_preflight("facebook", undeclared, {"account_type": "page"})[0], [])
        # An explicit Page variant cannot hide a profile account.
        self.assertBlocked("facebook", variant("facebook"), "conflict", meta={"account_type": "profile"})
        self.assertBlocked("facebook", variant("facebook", platform_options={"target_type": "event"}),
                           "not recognized")

    def test_secrets_placeholders_and_bad_links_block(self):
        self.assertBlocked("threads", variant("threads", access_token="x"), "secret-like")
        self.assertBlocked("threads", variant("threads", text="Launch TODO"), "placeholder")
        self.assertBlocked("threads", variant("threads", link_url="http://example.org"), "public HTTPS")
        self.assertBlocked("tiktok", variant("tiktok"), "secret-like", meta={"session": "abc"})


class PackageContract(unittest.TestCase):
    def test_status_is_always_manual_and_no_publication_wording(self):
        for platform in MANUAL_PLATFORMS:
            for v in (variant(platform), variant(platform, text="Launch TODO")):
                with self.subTest(platform=platform, blocked="TODO" in v["text"]):
                    package = build_export(platform, v)
                    self.assertEqual(package["status"], STATUS)
                    self.assertFalse(package["api_capability"]["api_publish_allowed"])
                    self.assertNotIn("remote_id", json.dumps(package))
                    rendered = NEGATED.sub("", "\n".join(strings(package)) + render_markdown(package))
                    self.assertIsNone(CLAIM_WORDS.search(rendered), CLAIM_WORDS.search(rendered))
                    self.assertIn("not evidence that a post exists", package["notice"])

    def test_tiktok_never_preselects_public(self):
        v = variant("tiktok", platform_options={"privacy_level": "PUBLIC_TO_EVERYONE", "disable_comment": False})
        package = build_export("tiktok", v, account_meta={"tiktok_api_audit": "unaudited"})
        decisions = {d["id"]: d for d in package["human_decisions"]}
        privacy = decisions["privacy_level"]
        self.assertIsNone(privacy["selected"])
        self.assertTrue(privacy["required"])
        self.assertEqual(privacy["options"], ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                                              "FOLLOWER_OF_CREATOR", "SELF_ONLY"])
        self.assertIn("unaudited", privacy["note"])
        self.assertIn("never pre-selects public", privacy["note"])
        self.assertTrue(all(d["selected"] is None for d in package["human_decisions"]))
        self.assertTrue(any("privacy_level is ignored" in w for w in package["preflight"]["warnings"]))
        self.assertEqual(package["account"]["tiktok_api_audit"], "unaudited")
        self.assertNotIn("PUBLIC_TO_EVERYONE", json.dumps(package["content"]))
        self.assertEqual(build_export("tiktok", variant("tiktok"))["account"]["tiktok_api_audit"], "unknown")

    def test_youtube_made_for_kids_and_privacy_require_the_human(self):
        v = variant("youtube", platform_options={"title": "Tour", "made_for_kids": False,
                                                 "privacy_status": "public"})
        package = build_export("youtube", v)
        decisions = {d["id"]: d for d in package["human_decisions"]}
        for key in ("made_for_kids", "privacy_status", "altered_or_synthetic_content"):
            self.assertIsNone(decisions[key]["selected"], key)
            self.assertTrue(decisions[key]["required"], key)
        self.assertEqual(decisions["made_for_kids"]["source"], "https://support.google.com/youtube/answer/9528076")
        self.assertEqual(package["content"]["title"], "Tour")
        self.assertEqual(package["content"]["description"], v["text"])
        self.assertIn("`made_for_kids`", render_markdown(package))
        self.assertEqual(FACTS["youtube"]["unverified_project_private_lock"]["status"], "conflicting")

    def test_exact_text_hashtags_and_links(self):
        text = "Docs at https://example.org/a#frag #OpenSource #python @reachweaver x@y.com"
        package = build_export("instagram", variant("instagram", text=text))
        self.assertEqual(package["content"]["text"], text)
        self.assertEqual(package["hashtags"]["items"], ["#OpenSource", "#python"])
        self.assertEqual(package["hashtags"]["limit"], 30)
        self.assertEqual(package["mentions"]["items"], ["@reachweaver"])
        self.assertEqual(package["links"]["in_text"], ["https://example.org/a#frag"])
        self.assertTrue(any("tappable" in n for n in package["links"]["notes"]))

    def test_facts_are_sourced_dated_and_honest(self):
        for platform, facts in FACTS.items():
            for key, fact in facts.items():
                with self.subTest(platform=platform, fact=key):
                    self.assertTrue(fact["source"].startswith("https://"))
                    self.assertRegex(fact["verified_date"], r"^\d{4}-\d{2}-\d{2}$")
                    self.assertIn(fact["status"], ("verified", "unknown", "conflicting"))
                    if fact["status"] != "verified":
                        self.assertIsNone(fact["value"])

    def test_module_has_no_send_path(self):
        self.assertIsNone(manual_export.SEND_PATH)
        source = Path(manual_export.__file__).read_text(encoding="utf-8")
        for forbidden in ("publishers", "connections", "urllib.request", "http.client", "socket"):
            self.assertNotIn(forbidden, source)


class MediaIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def manifest(self, assets) -> Path:
        path = self.dir / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "default_locale": "en", "assets": assets}),
                        encoding="utf-8")
        return path

    def test_instagram_image_alt_text_validated_via_media(self):
        (self.dir / "card.jpg").write_bytes(jpeg_bytes(1080, 1080))
        path = self.manifest([asset(path="card.jpg", targets=["instagram"])])
        package = build_export("instagram", variant("instagram"), path)
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertTrue(package["media"]["validated"])
        self.assertEqual(package["media"]["assets"][0]["alt_text"], asset()["alt_text"])
        self.assertEqual(package["media"]["assets"][0]["detected"]["width"], 1080)
        bad = self.manifest([asset(path="card.jpg", targets=["instagram"], alt_text="image")])
        errors = build_export("instagram", variant("instagram"), bad)["preflight"]["errors"]
        self.assertTrue(any(e.startswith("media:") and "placeholder" in e for e in errors), errors)

    def test_video_platforms_need_a_matching_video_asset_with_captions(self):
        (self.dir / "clip.mp4").write_bytes(mp4_bytes(1080, 1920, 20.0))
        (self.dir / "clip.vtt").write_text(GOOD_VTT, encoding="utf-8")
        video = asset(id="clip", path="clip.mp4", kind="video", targets=["tiktok"], alt_text=None,
                      subtitles=[{"path": "clip.vtt", "locale": "en"}])
        package = build_export("tiktok", variant("tiktok"), self.manifest([video]))
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertEqual(package["media"]["assets"][0]["subtitles"], ["clip.vtt"])
        # The same manifest has no asset targeting YouTube: blocked.
        errors = build_export("youtube", variant("youtube"), self.manifest([video]))["preflight"]["errors"]
        self.assertTrue(any("no video asset targeting youtube" in e for e in errors), errors)
        uncaptioned = dict(video, subtitles=[])
        errors = build_export("tiktok", variant("tiktok"), self.manifest([uncaptioned]))["preflight"]["errors"]
        self.assertTrue(any("captions/subtitles" in e for e in errors), errors)

    def test_without_manifest_media_is_flagged_not_assumed(self):
        package = build_export("tiktok", variant("tiktok"))
        self.assertFalse(package["media"]["validated"])
        self.assertTrue(any("not validated" in w for w in package["preflight"]["warnings"]))


class WriteAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_json(self, name, obj) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_new_directory_owner_only_files_and_overwrite_refusal(self):
        out = self.dir / "export" / "youtube"
        files = write_export(build_export("youtube", variant("youtube")), out)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        self.assertEqual(sorted(Path(f).name for f in files),
                         ["CHECKLIST.md", "description.txt", "manual-export.json", "title.txt"])
        for name in files:
            self.assertEqual(stat.S_IMODE(Path(name).stat().st_mode), 0o600, name)
        self.assertEqual((out / "title.txt").read_text(encoding="utf-8"), "ReachWeaver in 60 seconds")
        before = (out / "manual-export.json").read_bytes()
        with self.assertRaises(ValidationError):
            write_export(build_export("youtube", variant("youtube")), out)
        self.assertEqual((out / "manual-export.json").read_bytes(), before)
        empty = self.dir / "empty"
        empty.mkdir()
        with self.assertRaises(ValidationError):
            write_export(build_export("threads", variant("threads")), empty)
        self.assertEqual(list(empty.iterdir()), [])

    def test_blocked_package_is_never_written(self):
        package = build_export("facebook", variant("facebook", platform_options={"target_type": "profile"}))
        with self.assertRaises(ValidationError):
            write_export(package, self.dir / "never")
        self.assertFalse((self.dir / "never").exists())

    def test_cli_writes_package_offline_and_refuses_overwrite(self):
        v = self.write_json("tiktok.json", variant("tiktok"))
        out = self.dir / "pkg"
        with NetworkGuard() as guard:
            code, stdout, stderr = self.cli("manual-export", "--platform", "tiktok", "--variant", str(v),
                                            "--out", str(out), "--json")
            self.assertEqual(code, 0, stderr)
            package = json.loads(stdout)
            self.assertEqual(package["status"], STATUS)
            self.assertIn("Nothing was sent", stderr)
            self.assertEqual((out / "caption.txt").read_text(encoding="utf-8"), variant("tiktok")["text"])
            code, _, stderr = self.cli("manual-export", "--platform", "tiktok", "--variant", str(v),
                                       "--out", str(out))
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", stderr)
        self.assertEqual(guard.attempts, [])

    def test_cli_blocks_facebook_profile_and_writes_nothing(self):
        v = self.write_json("fb.json", variant("facebook", platform_options={}))
        meta = self.write_json("meta.json", {"account_type": "profile", "handle": "me"})
        out = self.dir / "fb"
        code, stdout, _ = self.cli("manual-export", "--platform", "facebook", "--variant", str(v),
                                   "--account-meta", str(meta), "--out", str(out))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", stdout)
        self.assertIn("rejected", stdout)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
