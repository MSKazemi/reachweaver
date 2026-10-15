"""Sourced media constraints for the GitHub social preview and the DEV cover image.

Fixtures are generated in temporary directories; nothing touches the network.
"""
from __future__ import annotations

import unittest

from reachweaver.media import CONSTRAINTS
from tests.test_media import MediaFixture, asset, gif_bytes, jpeg_bytes, png_bytes, webp_bytes

GITHUB_DOCS = "docs.github.com/en/repositories/managing-your-repositorys-settings-and-features"


class ConstraintTableTests(unittest.TestCase):
    def test_new_targets_are_sourced_and_dated(self):
        for target, expected in (("github", "2026-10-14"), ("devto", "2026-10-15")):
            for name, row in CONSTRAINTS[target]["image"].items():
                self.assertTrue(row["source"].startswith("https://"), (target, name))
                self.assertEqual(row["verified_date"], expected, (target, name))

    def test_older_rows_keep_their_original_date(self):
        for target in ("bluesky", "mastodon", "instagram", "youtube_shorts", "tiktok", "linkedin"):
            for rows in CONSTRAINTS[target].values():
                for name, row in rows.items():
                    self.assertEqual(row["verified_date"], "2026-10-08", (target, name))

    def test_unstated_dev_limits_stay_unknown(self):
        rows = CONSTRAINTS["devto"]["image"]
        self.assertIsNone(rows["allowed_containers"]["value"])
        self.assertIsNone(rows["max_bytes"]["value"])
        self.assertEqual(rows["aspect_ratio"]["severity"], "warning")


class GithubSocialPreviewTests(MediaFixture):
    def run_github(self, name: str, data: bytes) -> dict:
        self.write(name, data)
        return self.check(asset(path=name, targets=["github"]))

    def test_recommended_size_png_is_clean(self):
        report = self.run_github("card.png", png_bytes(1280, 640))
        self.assertTrue(report["valid"], report["errors"])
        self.assertLacks(report["warnings"], "github")

    def test_below_documented_minimum_is_an_error(self):
        report = self.run_github("small.png", png_bytes(600, 300))
        self.assertFalse(report["valid"])
        self.assertHas(report["errors"], "width 600px below 640px")
        self.assertHas(report["errors"], "height 300px below 320px")
        self.assertHas(report["errors"], GITHUB_DOCS)

    def test_exact_minimum_passes_size_rules(self):
        report = self.run_github("min.png", png_bytes(640, 320))
        self.assertTrue(report["valid"], report["errors"])

    def test_jpeg_and_gif_are_allowed(self):
        self.assertTrue(self.run_github("a.jpg", jpeg_bytes(1280, 640))["valid"])
        self.assertTrue(self.run_github("b.gif", gif_bytes(1280, 640))["valid"])

    def test_webp_is_rejected(self):
        report = self.run_github("card.webp", webp_bytes(1280, 640))
        self.assertFalse(report["valid"])
        self.assertHas(report["errors"], "not in allowed gif, jpeg, png")

    def test_one_megabyte_or_more_is_rejected(self):
        report = self.run_github("big.png", png_bytes(1280, 640) + b"\x00" * 1_000_000)
        self.assertFalse(report["valid"])
        self.assertHas(report["errors"], "limit is 1000000")

    def test_non_two_to_one_ratio_warns_but_stays_valid(self):
        report = self.run_github("linkedin.png", png_bytes(1200, 627))
        self.assertTrue(report["valid"], report["errors"])
        self.assertHas(report["warnings"], "github image: aspect ratio")


class DevCoverTests(MediaFixture):
    def run_dev(self, name: str, data: bytes) -> dict:
        self.write(name, data)
        return self.check(asset(path=name, targets=["devto"]))

    def test_recommended_cover_has_no_aspect_warning(self):
        report = self.run_dev("cover.png", png_bytes(1000, 420))
        self.assertTrue(report["valid"], report["errors"])
        self.assertLacks(report["warnings"], "devto image: aspect ratio")

    def test_square_cover_warns_without_blocking(self):
        report = self.run_dev("square.png", png_bytes(1000, 1000))
        self.assertTrue(report["valid"], report["errors"])
        self.assertHas(report["warnings"], "devto image: aspect ratio")

    def test_unstated_limits_are_reported_as_skipped(self):
        report = self.run_dev("cover.png", png_bytes(1000, 420))
        skipped = report["assets"][0]["targets"]["devto"]["skipped"]
        self.assertIn("max_bytes", skipped)
        self.assertIn("allowed_containers", skipped)


class ReportDatesTests(MediaFixture):
    def test_report_lists_every_verification_date(self):
        self.write("card.png", png_bytes(1280, 640))
        report = self.check(asset(path="card.png", targets=["github"]))
        self.assertEqual(report["constraints_verified_date"], "2026-10-08")
        self.assertEqual(report["constraints_verified_dates"], ["2026-10-08", "2026-10-14", "2026-10-15"])


if __name__ == "__main__":
    unittest.main()
