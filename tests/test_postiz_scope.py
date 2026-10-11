"""Postiz scope-detection boundary (issue #30): pure, offline pins of what the
identity preflight can and cannot prove. No network; ``postiz._call`` is patched."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from reachweaver import postiz
from reachweaver.policy import ValidationError
from reachweaver.publishers import PublishError

ACCOUNT = {"id": "a1", "platform": "bluesky", "handle": "@Me.Bsky.Social", "postiz_integration_id": "i1"}


def row(**kw):
    base = {"id": "i1", "identifier": "bluesky", "profile": "me.bsky.social", "disabled": False}
    base.update(kw)
    return base


class PostizScopeBoundary(unittest.TestCase):
    def test_match_never_claims_write_permission(self):
        verified = postiz.match_integration(ACCOUNT, [row()])
        self.assertEqual(verified.identity, "verified")
        self.assertEqual(verified.write_permission, "unverified")

    def test_handle_comparison_ignores_case_and_leading_at(self):
        postiz.match_integration(ACCOUNT, [row(profile="@ME.bsky.social")])

    def test_empty_profile_or_handle_is_a_mismatch(self):
        with self.assertRaisesRegex(ValidationError, "identity_mismatch"):
            postiz.match_integration(ACCOUNT, [row(profile=None)])
        with self.assertRaisesRegex(ValidationError, "identity_mismatch"):
            postiz.match_integration({**ACCOUNT, "handle": ""}, [row(profile="")])

    def test_list_reduces_to_routing_fields_and_drops_malformed_rows(self):
        raw = [{"id": "i1", "identifier": "bluesky", "profile": "p", "disabled": False,
                "picture": "https://x/p.png", "customer": {"id": "c"}, "extra": "SHOULD-NOT-SURVIVE"},
               "junk", {"id": 5}, {"name": "no id"}]
        with patch.object(postiz, "_call", return_value=raw):
            rows = postiz.list_integrations(ACCOUNT)
        self.assertEqual(rows, [{"id": "i1", "identifier": "bluesky", "profile": "p", "disabled": False}])

    def test_non_list_response_is_refused(self):
        with patch.object(postiz, "_call", return_value={"integrations": []}):
            with self.assertRaisesRegex(PublishError, "postiz_integrations_unexpected_response"):
                postiz.list_integrations(ACCOUNT)

    def test_only_literal_true_means_disabled(self):
        """Pins current behaviour: a missing or non-boolean ``disabled`` is read as enabled.
        Whether Postiz can ever send such a value is unverified (docs/POSTIZ-SCOPE-DETECTION.md)."""
        for value in ("true", 1, None):
            with patch.object(postiz, "_call", return_value=[{"id": "i1", "disabled": value}]):
                self.assertFalse(postiz.list_integrations(ACCOUNT)[0]["disabled"], value)
        with patch.object(postiz, "_call", return_value=[{"id": "i1"}]):
            self.assertFalse(postiz.list_integrations(ACCOUNT)[0]["disabled"])

    def test_duplicate_ids_first_row_wins(self):
        """Pins current behaviour; duplicate ids are not expected from Postiz (unverified)."""
        rows = [row(), row(profile="other")]
        self.assertEqual(postiz.match_integration(ACCOUNT, rows).integration_id, "i1")


if __name__ == "__main__":
    unittest.main()
