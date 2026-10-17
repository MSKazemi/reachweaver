"""Offline-only tests for ten newly registered social networks."""
import unittest
from reachweaver.distribution import load_registry, validate_registry
from reachweaver.manual_package import build_package, generic_platforms, package_preflight
from reachweaver.policy import SUPPORTED_LIVE

NEW = ("misskey","lemon8","band","niconico","likee","mewe","odysee","rumble","minds","diaspora")
HUMAN_ONLY = {"band","mewe"}

class NewSocialNetworkTests(unittest.TestCase):
    def test_all_are_valid_unconnected_manual_destinations(self):
        data = load_registry()
        self.assertEqual(validate_registry(data), [])
        self.assertEqual(len(data["platforms"]), 110)
        by_id = {p["id"]: p for p in data["platforms"]}
        self.assertTrue(set(NEW).issubset(set(generic_platforms())))
        self.assertTrue(set(NEW).isdisjoint(SUPPORTED_LIVE))
        for pid in NEW:
            with self.subTest(pid=pid):
                entry = by_id[pid]
                self.assertEqual(entry["reachweaver_delivery"], "manual_only")
                self.assertEqual(entry["checked_on"], "2026-10-17")
                self.assertEqual(entry["human_only"], pid in HUMAN_ONLY)
                self.assertEqual(entry["scheduling"]["mode"], "native" if pid == "band" else "unverified")
                self.assertEqual(bool(entry["scheduling"]["official_source"]), pid == "band")

    def test_manual_packages_do_not_claim_publication(self):
        for pid in NEW:
            with self.subTest(pid=pid):
                variant = ({"talking_points": ["Read group rules"]} if pid in HUMAN_ONLY else
                           {"text": "A verified technical demonstration. https://example.org/demo"})
                package = build_package(pid, variant, generated_at="2026-10-17T00:00:00Z")
                self.assertEqual(package["preflight"]["errors"], [])
                self.assertEqual(package["status"], "manual_export_not_published")
                self.assertIn("not evidence that a post exists", package["notice"])
                self.assertEqual(package["content"]["ready_to_paste"], pid not in HUMAN_ONLY)
                if pid in HUMAN_ONLY:
                    self.assertNotIn("text", package["content"])
                    errors, _ = package_preflight(pid, {"text": "bot-made group advertisement"})
                    self.assertTrue(any("human-only" in error for error in errors))
