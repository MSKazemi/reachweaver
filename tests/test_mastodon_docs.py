"""docs/MASTODON.md stays true to the Mastodon adapter it describes (public issue #43). Offline: no network, no token."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from reachweaver.accounts import MAX_SPOILER_CHARS, load_accounts
from reachweaver.cli import make_parser
from reachweaver.policy import CONSERVATIVE_TEXT_LIMITS, MASTODON_MEDIA_DEFAULTS, MASTODON_VISIBILITIES
from reachweaver.richtext import MASTODON_URL_WEIGHT
from reachweaver.storage import add_post, approval_payload_sha256, connect, get_post

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs/MASTODON.md").read_text(encoding="utf-8")
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "reachweaver").glob("*.py"))
TESTS = ROOT / "tests"


def subcommands() -> set[str]:
    names: set[str] = set()
    for action in make_parser()._actions:
        names |= set(getattr(action, "choices", None) or ())
    return names


class MastodonDocTests(unittest.TestCase):
    def test_status_is_honest_about_the_single_live_receipt(self):
        head = DOC.split("## Account configuration", 1)[0]
        self.assertIn("one live receipt", head)
        self.assertIn("receipts/mastodon-2026-10-11.json", head)
        self.assertIn("failure and rate-limit behaviour, which stays mock-tested", head)
        self.assertTrue((ROOT / "docs/receipts/mastodon-2026-10-11.json").is_file())
        for claim in ("fully supported", "production-ready", "guaranteed"):
            self.assertNotIn(claim, DOC.casefold())

    def test_numbers_in_the_tables_are_the_numbers_in_the_code(self):
        self.assertEqual(CONSERVATIVE_TEXT_LIMITS["mastodon"], 450)
        self.assertIn("conservative 450 limit", DOC)
        defaults = MASTODON_MEDIA_DEFAULTS
        self.assertIn(f"{defaults['max_media_attachments']} images", DOC)
        self.assertIn(f"{defaults['image_size_limit']:,} bytes", DOC)
        self.assertIn(f"{defaults['media_description_limit']} characters", DOC)
        self.assertIn(f"1 to {MAX_SPOILER_CHARS} printable characters", DOC)
        self.assertIn(f"at least {MASTODON_URL_WEIGHT} characters", DOC)
        for visibility in MASTODON_VISIBILITIES:
            self.assertIn(f"`{visibility}`", DOC)
        self.assertIn("`direct` (a direct message) is forbidden", DOC)

    def test_every_command_named_is_a_real_subcommand(self):
        named = set(re.findall(r"`(?:python3 -m reachweaver )?([a-z]+(?:-[a-z]+)*)(?: [^`]*)?`", DOC)) & {
            "connect-check", "show", "approve", "publish", "reconcile", "media-check", "media-attach"}
        self.assertEqual(named, {"connect-check", "show", "approve", "publish", "reconcile", "media-check", "media-attach"})
        self.assertLessEqual(named, subcommands())

    def test_every_error_code_and_test_it_cites_exists(self):
        for code in ("mastodon_expected_handle_must_be_local_to_configured_server", "identity_mismatch",
                     "reply_server_mismatch", "account_provider_cooldown_active"):
            if code in DOC:
                self.assertIn(code, SOURCE, code)
        for pattern in re.findall(r"-p '(test_[a-z_*]+\.py)'", DOC):
            self.assertTrue(list(TESTS.glob(pattern)), pattern)
        for name in re.findall(r"tests/(test_[a-z_]+)\.py::(test_[a-z_]+)", DOC):
            text = (TESTS / f"{name[0]}.py").read_text(encoding="utf-8")
            self.assertIn(f"def {name[1]}(", text, name)
        for fixture in ("statuses-429-x-ratelimit-reset", "media-upload-202-unprocessed", "media-poll-206-partial",
                        "media-poll-200-ready", "media-upload-429-rate-limit", "media-upload-429-reset-only"):
            self.assertTrue((TESTS / f"fixtures/providers/mastodon/{fixture}.json").is_file(), fixture)

    def test_relative_links_resolve(self):
        for target in re.findall(r"\]\((?!https?:)([^)#]+)(?:#[^)]*)?\)", DOC):
            self.assertTrue((ROOT / "docs" / target).exists(), target)

    def test_documented_http_failures_match_the_ambiguous_rule(self):
        # The doc claims 401/403/other 4xx/5xx after the request is handed over are 'ambiguous' and 429 is 'likely_not_created'.
        from reachweaver.ratelimit import AMBIGUOUS, LIKELY_NOT_CREATED, classify_send_failure
        from reachweaver.publishers import PublishError
        for status in (401, 403, 404, 422, 426, 500, 502, 503):
            exc = PublishError(f"http_{status}", status=status)
            self.assertEqual(classify_send_failure("mastodon", None, exc, write_started=True), AMBIGUOUS, status)
        self.assertEqual(classify_send_failure("mastodon", None, PublishError("http_429", status=429), write_started=True),
                         LIKELY_NOT_CREATED)

    def test_approval_does_not_bind_visibility_language_or_spoiler_text(self):
        """The doc says so, plainly, as an open question. If the approval ever binds them, change the doc and this test."""
        base = {"id": "doc-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_DOC_MASTO",
                "server": "https://social.example.org", "handle": "alice"}
        hashes = set()
        with tempfile.TemporaryDirectory() as work:
            db = str(Path(work) / "s.sqlite")
            for options in ({}, {"visibility": "unlisted"}, {"visibility": "private", "language": "en", "spoiler_text": "cw"}):
                config = Path(work) / "a.json"
                config.write_text(json.dumps({"accounts": [{**base, **options}]}), encoding="utf-8")
                self.assertIn("doc-masto", load_accounts(config))
                with connect(db) as con:
                    pid = add_post(con, "doc-masto", "mastodon", "Hello https://example.com") if not hashes else \
                        con.execute("SELECT id FROM posts").fetchone()["id"]
                    hashes.add(approval_payload_sha256(get_post(con, pid)))
        self.assertEqual(len(hashes), 1, "the approval hash must not change with visibility, language or spoiler_text")
        self.assertIn("It does not bind `visibility`, `language` or `spoiler_text`", DOC)


if __name__ == "__main__":
    unittest.main()
