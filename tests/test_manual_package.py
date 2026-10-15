"""Generic manual packages for every other manual_only destination (private #44, item C2).

Also pins the five dedicated manual-export packages byte for byte against golden files generated
before the generic package existed. Every fixture is local; no test contacts a network host, and
neither module under test has a send path.
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

from reachweaver import manual_package
from reachweaver.cli import main
from reachweaver.distribution import load_registry as load_distribution_registry
from reachweaver.manual_export import MANUAL_PLATFORMS, STATUS, build_export, write_export
from reachweaver.manual_package import (
    DEDICATED, LIMIT_UNKNOWN, MAX_TALKING_POINT_CHARS, MAX_TALKING_POINTS, PACKAGE_KIND, build_package,
    generic_platforms, package_preflight, render_markdown, render_notes, write_package,
)
from reachweaver.media import constraint_table
from reachweaver.policy import ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parent))  # Reuse sibling fixtures under any runner.
from test_manual_export import CLAIM_WORDS, NEGATED, strings, variant as dedicated_variant  # noqa: E402
from test_media import asset, jpeg_bytes  # noqa: E402
from test_no_network import NetworkGuard  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "manual_export_golden"
GOLDEN_AT = "2026-10-15T00:00:00Z"
GENERATED = re.compile(r'"generated_at": "[^"]*"')
REGISTRY = {p["id"]: p for p in load_distribution_registry()["platforms"]}
HUMAN_ONLY = sorted(key for key in generic_platforms() if REGISTRY[key]["human_only"])
POST = "ReachWeaver ships a generic manual package for every manual-only destination. https://example.org/docs"


def minimal_variant(platform: str) -> dict:
    if REGISTRY[platform]["human_only"]:
        return {"platform": platform, "talking_points": ["What the tool does offline", "Where the docs live"]}
    return {"platform": platform, "text": POST}


def files_of(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


class Base(unittest.TestCase):
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


class DedicatedPackagesUnchanged(Base):
    """The five sourced packages are byte-identical to golden files written before C2."""

    def test_library_output_matches_golden_bytes(self):
        for platform in MANUAL_PLATFORMS:
            with self.subTest(platform=platform):
                out = self.dir / platform
                write_export(build_export(platform, dedicated_variant(platform), generated_at=GOLDEN_AT), out)
                golden = {k: v for k, v in files_of(GOLDEN / platform).items() if k != "stdout.txt"}
                self.assertEqual(files_of(out), golden)

    def test_library_output_with_media_matches_golden_bytes(self):
        media = self.dir / "media"
        media.mkdir()
        (media / "card.jpg").write_bytes(jpeg_bytes(1080, 1080))
        manifest = {"schema_version": 1, "default_locale": "en",
                    "assets": [asset(path="card.jpg", targets=["instagram"])]}
        package = build_export("instagram", dedicated_variant("instagram"), manifest, media_base_dir=media,
                               generated_at=GOLDEN_AT)
        write_export(package, self.dir / "out")
        self.assertEqual(files_of(self.dir / "out"), files_of(GOLDEN / "instagram-media"))

    def test_cli_output_matches_golden_bytes(self):
        for platform in MANUAL_PLATFORMS:
            with self.subTest(platform=platform):
                path = self.write_json(f"{platform}.json", dedicated_variant(platform))
                out = self.dir / f"cli-{platform}"
                code, stdout, stderr = self.cli("manual-export", "--platform", platform, "--variant", str(path),
                                                "--out", str(out))
                self.assertEqual(code, 0, stderr)
                golden = files_of(GOLDEN / platform)
                self.assertEqual(stdout.encode("utf-8"), golden.pop("stdout.txt"))
                written = files_of(out)
                stamp = GENERATED.sub(f'"generated_at": "{GOLDEN_AT}"', written["manual-export.json"].decode("utf-8"))
                written["manual-export.json"] = stamp.encode("utf-8")
                self.assertEqual(written, golden)

    def test_dedicated_ids_are_not_generic(self):
        self.assertTrue(set(generic_platforms()).isdisjoint(set(MANUAL_PLATFORMS) | set(DEDICATED)))
        for platform, use in (("facebook_pages", "facebook"), ("youtube", "youtube")):
            with self.subTest(platform=platform):
                with self.assertRaisesRegex(ValidationError, f"--platform {use}"):
                    build_package(platform, {"text": POST})


class Coverage(Base):
    def test_every_other_manual_only_destination_gets_a_package(self):
        expected = {k for k, p in REGISTRY.items() if p["reachweaver_delivery"] == "manual_only"} - set(DEDICATED)
        self.assertEqual(set(generic_platforms()), expected)
        self.assertGreaterEqual(len(expected), 70)
        for platform in generic_platforms():
            with self.subTest(platform=platform):
                package = build_package(platform, minimal_variant(platform), generated_at=GOLDEN_AT)
                self.assertEqual(package["preflight"]["errors"], [])
                self.assertEqual((package["kind"], package["status"]), (PACKAGE_KIND, STATUS))
                self.assertEqual(package["platform_label"], REGISTRY[platform]["label"])
                self.assertEqual(package["human_only"], REGISTRY[platform]["human_only"])
                rendered = NEGATED.sub("", "\n".join(strings(package)) + render_markdown(package))
                self.assertIsNone(CLAIM_WORDS.search(rendered), CLAIM_WORDS.search(rendered))
                self.assertIn("not evidence that a post exists", package["notice"])
                self.assertNotIn("remote_id", json.dumps(package))

    def test_non_manual_and_unknown_destinations_are_refused(self):
        for platform in ("bluesky", "linkedin", "github", "gitee", "nope"):
            with self.subTest(platform=platform):
                with self.assertRaises(ValidationError):
                    build_package(platform, {"text": POST})
                self.assertTrue(package_preflight(platform, {"text": POST})[0])
        path = self.write_json("v.json", {"text": POST})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["manual-export", "--platform", "bluesky", "--variant", str(path), "--out", str(self.dir / "o")])
        self.assertFalse((self.dir / "o").exists())


class OrdinaryManualDestination(Base):
    def test_x_package_carries_exact_text_and_registry_facts_only(self):
        text = POST + " #opensource @reachweaver"
        package = build_package("x", {"platform": "x", "text": text, "link_url": "https://example.org/x",
                                      "platform_options": {"title": "Launch", "privacy": "public"}})
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertTrue(any("platform_options.privacy" in w for w in package["preflight"]["warnings"]))
        content = package["content"]
        self.assertEqual((content["text"], content["title"], content["ready_to_paste"]), (text, "Launch", True))
        self.assertEqual(content["hashtags"], ["#opensource"])
        self.assertEqual(content["mentions"], ["@reachweaver"])
        self.assertEqual(package["links"]["in_text"], ["https://example.org/docs"])
        self.assertEqual(package["official_help"]["url"], REGISTRY["x"]["scheduling"]["official_source"])
        self.assertEqual(package["registry"]["capability"]["reachweaver_adapter"], "none")
        notes = [n["note"] for n in package["limits"]["capability_registry_notes"]]
        self.assertIn("Up to 4 photos, 1 GIF or 1 video per post", notes)
        self.assertIn("keep the native post URL", package["checklist"][-1])
        self.assertIn("records nothing", package["checklist"][-1])

    def test_destination_without_an_official_source_gets_no_url(self):
        self.assertEqual(REGISTRY["peerlist"]["scheduling"]["official_source"], "")
        package = build_package("peerlist", minimal_variant("peerlist"))
        self.assertIsNone(package["official_help"]["url"])
        self.assertTrue(any("find its own posting page yourself" in s for s in package["checklist"]))

    def test_text_is_required_and_checked(self):
        for bad, fragment in (({}, "must be a string"), ({"text": "  "}, "substantive"),
                              ({"text": "Launch TODO"}, "placeholder"), ({"text": POST, "token": "x"}, "secret-like"),
                              ({"text": POST, "link_url": "http://example.org"}, "public HTTPS"),
                              ({"text": POST, "format": "story"}, "not one of"),
                              ({"text": POST, "platform": "dev"}, "--platform")):
            with self.subTest(fragment=fragment):
                errors, _ = package_preflight("product_hunt", bad)
                self.assertTrue(any(fragment in e for e in errors), errors)

    def test_dev_adds_the_ai_disclosure_step(self):
        package = build_package("dev", minimal_variant("dev"))
        self.assertTrue(any("dev.to/guidelines-for-ai-assisted-articles-on-dev" in s for s in package["checklist"]))
        self.assertFalse(any("guidelines-for-ai" in s for s in build_package("x", minimal_variant("x"))["checklist"]))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_write_new_private_directory_and_refuse_overwrite(self):
        out = self.dir / "pkg" / "x"
        files = write_package(build_package("x", {"text": POST, "platform_options": {"title": "T"}}), out)
        self.assertEqual(sorted(Path(f).name for f in files), ["CHECKLIST.md", "manual-export.json", "post.txt",
                                                               "title.txt"])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        for name in files:
            self.assertEqual(stat.S_IMODE(Path(name).stat().st_mode), 0o600, name)
        self.assertEqual((out / "post.txt").read_text(encoding="utf-8"), POST)
        with self.assertRaises(ValidationError):
            write_package(build_package("x", {"text": POST}), out)
        with self.assertRaises(ValidationError):
            write_package(build_package("x", {"text": "TODO"}), self.dir / "never")
        self.assertFalse((self.dir / "never").exists())
        with self.assertRaises(ValidationError):  # a dedicated package is not written by the generic writer
            write_package(build_export("threads", dedicated_variant("threads")), self.dir / "never")

    def test_cli_writes_offline(self):
        path = self.write_json("x.json", {"platform": "x", "text": POST})
        out = self.dir / "x"
        with NetworkGuard() as guard:
            code, stdout, stderr = self.cli("manual-export", "--platform", "x", "--variant", str(path),
                                            "--out", str(out))
            self.assertEqual(code, 0, stderr)
            self.assertIn(f"TEXT LIMIT: {LIMIT_UNKNOWN}", stdout)
            self.assertIn("Nothing was sent", stderr)
            self.assertEqual((out / "post.txt").read_text(encoding="utf-8"), POST)
            code, _, stderr = self.cli("manual-export", "--platform", "x", "--variant", str(path), "--out", str(out))
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", stderr)
            code, stdout, _ = self.cli("manual-export", "--platform", "x", "--variant", str(path), "--out",
                                       str(self.dir / "j"), "--json")
            self.assertEqual(json.loads(stdout)["kind"], PACKAGE_KIND)
        self.assertEqual(guard.attempts, [])


class HumanOnlyDestinations(Base):
    def test_the_named_human_only_channels_are_covered(self):
        for platform in ("reddit", "hacker_news", "discord", "whatsapp", "facebook_groups", "indie_hackers"):
            self.assertIn(platform, HUMAN_ONLY)

    def test_agent_text_or_title_is_blocked_for_every_human_only_destination(self):
        for platform in HUMAN_ONLY:
            for bad in ({"text": POST}, {"talking_points": ["x"], "platform_options": {"title": "T"}}, {"text": ""}):
                with self.subTest(platform=platform, bad=sorted(bad)):
                    errors, _ = package_preflight(platform, bad)
                    self.assertTrue(any("human-only" in e and "write the post yourself" in e for e in errors), errors)

    def test_package_is_a_notes_form_without_post_text(self):
        for platform in HUMAN_ONLY:
            with self.subTest(platform=platform):
                package = build_package(platform, minimal_variant(platform))
                content = package["content"]
                self.assertIs(content["ready_to_paste"], False)
                self.assertNotIn("text", content)
                self.assertNotIn("title", content)
                self.assertTrue(content["write_it_yourself"].startswith("Write this yourself."))
                self.assertIn(content["write_it_yourself"], package["checklist"])
                self.assertIn("Write this yourself", render_notes(package))
                self.assertNotIn("Exact text", render_markdown(package))

    def test_written_package_has_notes_and_no_text_file(self):
        out = self.dir / "hn"
        variant = {"talking_points": ["Offline-first design", "No auto-posting"], "link_url": "https://example.org/d"}
        files = write_package(build_package("hacker_news", variant), out)
        self.assertEqual(sorted(Path(f).name for f in files), ["CHECKLIST.md", "NOTES.md", "manual-export.json"])
        notes = (out / "NOTES.md").read_text(encoding="utf-8")
        self.assertIn("**Write this yourself.", notes)
        self.assertIn("- Offline-first design", notes)
        self.assertIn("not text to paste", notes)
        checklist = (out / "CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("scheduling as prohibited", checklist)
        reddit = build_package("reddit", minimal_variant("reddit"))
        self.assertTrue(any("moderator-only" in s for s in reddit["checklist"]))

    def test_talking_points_are_capped_notes(self):
        too_many = {"talking_points": ["point"] * (MAX_TALKING_POINTS + 1)}
        too_long = {"talking_points": ["x" * (MAX_TALKING_POINT_CHARS + 1)]}
        for bad, fragment in ((too_many, "at most"), (too_long, "not a platform limit"),
                              ({"talking_points": "one string"}, "list of non-empty"),
                              ({"talking_points": ["TODO later"]}, "placeholder")):
            with self.subTest(fragment=fragment):
                errors, _ = package_preflight("reddit", bad)
                self.assertTrue(any(fragment in e for e in errors), errors)

    def test_cli_blocks_agent_text_and_writes_nothing(self):
        path = self.write_json("hn.json", {"text": POST})
        out = self.dir / "hn"
        code, stdout, _ = self.cli("manual-export", "--platform", "hacker_news", "--variant", str(path),
                                   "--out", str(out))
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", stdout)
        self.assertFalse(out.exists())
        path = self.write_json("hn-ok.json", {"talking_points": ["Offline-first design"]})
        code, stdout, _ = self.cli("manual-export", "--platform", "hacker_news", "--variant", str(path),
                                   "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("WRITE IT YOURSELF", stdout)
        self.assertFalse((out / "post.txt").exists())


class UnknownLimits(Base):
    def test_text_limit_is_always_unknown(self):
        for platform in generic_platforms():
            with self.subTest(platform=platform):
                limits = build_package(platform, minimal_variant(platform))["limits"]
                self.assertEqual((limits["text"]["value"], limits["text"]["display"]), (None, LIMIT_UNKNOWN))

    def test_media_limit_unknown_without_a_table(self):
        media = build_package("product_hunt", minimal_variant("product_hunt"))["limits"]["media"]
        self.assertEqual([(m["value"], m["display"]) for m in media], [(None, LIMIT_UNKNOWN)])
        self.assertIn(f"Media: {LIMIT_UNKNOWN}", render_markdown(build_package("product_hunt",
                                                                              minimal_variant("product_hunt"))))

    def test_known_media_rows_come_only_from_media_constraints(self):
        table = constraint_table()
        for platform in generic_platforms():
            for row in build_package(platform, minimal_variant(platform))["limits"]["media"]:
                if row["value"] is None:
                    self.assertEqual(row["display"], LIMIT_UNKNOWN)
                    continue
                with self.subTest(platform=platform, rule=row["rule"]):
                    self.assertEqual(row["value"], table[row["table"]][row["kind"]][row["rule"]]["value"])
        dev = {r["rule"]: r for r in build_package("dev", minimal_variant("dev"))["limits"]["media"]}
        self.assertEqual(dev["max_bytes"]["display"], LIMIT_UNKNOWN)
        self.assertEqual(dev["aspect_ratio"]["value"], table["devto"]["image"]["aspect_ratio"]["value"])


class MediaManifest(Base):
    def manifest(self, assets) -> Path:
        path = self.dir / "manifest.json"
        path.write_text(json.dumps({"schema_version": 1, "default_locale": "en", "assets": assets}), encoding="utf-8")
        return path

    def test_dev_image_with_alt_text_and_rights_record(self):
        (self.dir / "cover.jpg").write_bytes(jpeg_bytes(1000, 420))
        cover = asset(id="cover", path="cover.jpg", targets=["devto"], license="CC-BY-4.0",
                      attribution="Photo by the owner")
        package = build_package("dev", dict(minimal_variant("dev"), format="image"), self.manifest([cover]))
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertTrue(package["media"]["validated"])
        item = package["media"]["assets"][0]
        self.assertEqual(item["alt_text"], cover["alt_text"])
        self.assertEqual(item["detected"]["width"], 1000)
        self.assertEqual(item["rights"]["license"], "CC-BY-4.0")
        self.assertIs(item["rights"]["rights_confirmed"], True)
        self.assertEqual(item["rights"]["attribution"], "Photo by the owner")
        self.assertTrue(any(f"alt text: {cover['alt_text']}" in s and "license CC-BY-4.0" in s
                            for s in package["checklist"]))
        self.assertIn("alt text: " + cover["alt_text"], render_markdown(package))

    def test_media_errors_are_copied_and_block(self):
        (self.dir / "cover.jpg").write_bytes(jpeg_bytes(1000, 420))
        bad = asset(id="cover", path="cover.jpg", targets=["devto"], alt_text="image")
        errors = build_package("dev", dict(minimal_variant("dev"), format="image"),
                               self.manifest([bad]))["preflight"]["errors"]
        self.assertTrue(any(e.startswith("media:") and "placeholder" in e for e in errors), errors)
        other = asset(id="cover", path="cover.jpg", targets=["linkedin"])
        errors = build_package("dev", dict(minimal_variant("dev"), format="image"),
                               self.manifest([other]))["preflight"]["errors"]
        self.assertTrue(any("no image asset targeting dev" in e for e in errors), errors)

    def test_human_only_destination_still_lists_media_without_text(self):
        (self.dir / "shot.jpg").write_bytes(jpeg_bytes(800, 600))
        shot = asset(id="shot", path="shot.jpg", targets=["reddit"])
        package = build_package("reddit", dict(minimal_variant("reddit"), format="image"), self.manifest([shot]))
        self.assertEqual(package["preflight"]["errors"], [])
        self.assertEqual(package["media"]["assets"][0]["alt_text"], shot["alt_text"])
        self.assertTrue(any("no verified media constraint table for target 'reddit'" in w
                            for w in package["preflight"]["warnings"]))
        self.assertNotIn("text", package["content"])

    def test_without_manifest_media_is_flagged_not_assumed(self):
        package = build_package("x", dict(minimal_variant("x"), format="video"))
        self.assertFalse(package["media"]["validated"])
        self.assertTrue(any("not validated" in w for w in package["preflight"]["warnings"]))
        self.assertTrue(any("Run media-check" in s for s in package["checklist"]))


class NoSendPath(unittest.TestCase):
    def test_module_has_no_send_path_or_delivery_mode(self):
        self.assertIsNone(manual_package.SEND_PATH)
        source = Path(manual_package.__file__).read_text(encoding="utf-8")
        for forbidden in ("publishers", "connections", "urllib.request", "http.client", "socket", "subprocess",
                          "webbrowser", "DELIVERY_MODES"):
            self.assertFalse(forbidden in source, forbidden)
        self.assertIsNone(re.search(r"reachweaver_delivery\W*\]\s*=(?!=)", source))  # never writes a delivery mode


if __name__ == "__main__":
    unittest.main()
