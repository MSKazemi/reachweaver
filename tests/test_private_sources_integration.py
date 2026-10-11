"""Private-source guards (#9) on the surfaces added by #13, #17, #20 and #22 (owner-approved 2026-10-09).

``test_private_sources`` covers campaigns, UTM links and exports. A reply draft, an image's alt text,
a hosted image URL and a YouTube title or tag are also published text, so with ``--sources CONFIG``
``draft`` (reply drafts too), ``media-attach``, ``youtube-attach``, ``check`` and ``approve`` refuse a
link into a ``visibility: private`` repository of that config, exactly like a campaign link. Offline:
no request is made, and refusals change nothing.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_mastodon_media import png
from test_media import mp4_bytes
from test_private_sources import REPO, config

from reachweaver.cli import main
from reachweaver.storage import add_post, connect, get_post, set_post_media

PRIVATE_LINK = f"https://github.com/{REPO}/issues/7"
PUBLIC_LINK = "https://github.com/example-org/public-tool"
REFUSED = "private_source_link"


class PrivateLinksOnNewSurfaces(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.db = str(self.tmp / "outbox.sqlite")
        self.accounts = self.tmp / "accounts.json"
        self.accounts.write_text(json.dumps({"accounts": [
            {"id": "my-bsky", "platform": "bluesky", "env_prefix": "REACHWEAVER_PSI_BSKY",
             "handle": "owner.bsky.social"},
            {"id": "my-masto", "platform": "mastodon", "env_prefix": "REACHWEAVER_PSI_MASTO",
             "server": "https://social.example.org", "handle": "owner"},
            {"id": "yt-me", "platform": "youtube", "env_prefix": "REACHWEAVER_PSI_YT", "handle": "MyChannel",
             "channel_id": "UCabcdefghijklmnopqrstuv"},
        ]}), encoding="utf-8")
        self.sources = self.tmp / "sources.json"
        self.sources.write_text(json.dumps(config()), encoding="utf-8")

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.dict(os.environ, {}):
            try:
                code = main(["--db", self.db, "--accounts", str(self.accounts), *argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def count_posts(self):
        with connect(self.db) as db:
            return db.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    def test_reply_draft_with_a_private_link_is_refused_before_any_verification_request(self):
        with patch("reachweaver.publishers.request_get_json") as network:
            code, _, err = self.cli("draft", "--account", "my-bsky", "--text", f"Answer: {PRIVATE_LINK}",
                                    "--reply-to", "bluesky:at://did:plc:alice456/app.bsky.feed.post/3kparent22222",
                                    "--online", "--sources", str(self.sources))
        self.assertEqual(code, 2)
        self.assertIn(REFUSED, err)
        network.assert_not_called()
        self.assertEqual(self.count_posts(), 0)

    def test_plain_draft_text_and_source_url_are_checked(self):
        for argv in (("--text", f"See {PRIVATE_LINK}"), ("--text", "Plain text", "--source-url", PRIVATE_LINK)):
            with self.subTest(argv=argv):
                code, _, err = self.cli("draft", "--account", "my-masto", *argv, "--sources", str(self.sources))
                self.assertEqual(code, 2)
                self.assertIn(REFUSED, err)
        self.assertEqual(self.count_posts(), 0)
        code, out, err = self.cli("draft", "--account", "my-masto", "--text", f"See {PUBLIC_LINK}",
                                  "--sources", str(self.sources))
        self.assertEqual(code, 0, out + err)

    def test_alt_text_with_a_private_link_is_refused(self):
        with connect(self.db) as db:
            pid = add_post(db, "my-masto", "mastodon", "A chart")
        image = self.tmp / "chart.png"
        image.write_bytes(png())
        code, _, err = self.cli("media-attach", pid, "--file", str(image), "--alt", f"Chart from {PRIVATE_LINK}",
                                "--sources", str(self.sources))
        self.assertEqual(code, 2)
        self.assertIn(REFUSED, err)
        with connect(self.db) as db:
            self.assertIsNone(get_post(db, pid)["media_json"])

    def test_youtube_title_or_tag_with_a_private_link_is_refused(self):
        with connect(self.db) as db:
            pid = add_post(db, "yt-me", "youtube", "Walkthrough")
        clip = self.tmp / "clip.mp4"
        clip.write_bytes(mp4_bytes(1080, 1920, 30, mdat_size=600_000))
        base = ["youtube-attach", pid, "--file", str(clip), "--made-for-kids", "no", "--category-id", "28",
                "--sources", str(self.sources)]
        for extra in (["--title", f"Notes {PRIVATE_LINK}"], ["--title", "Walkthrough", "--tag", PRIVATE_LINK]):
            with self.subTest(extra=extra):
                code, _, err = self.cli(*base, *extra)
                self.assertEqual(code, 2)
                self.assertIn(REFUSED, err)
        with connect(self.db) as db:
            self.assertIsNone(get_post(db, pid)["video_json"])

    def test_check_and_approve_refuse_a_private_link_in_any_published_text(self):
        image = self.tmp / "chart.png"
        image.write_bytes(png())
        from reachweaver.attachments import build_attachment
        with connect(self.db) as db:
            pid = add_post(db, "my-masto", "mastodon", "A chart")
            set_post_media(db, pid, [build_attachment(image, f"Chart from {PRIVATE_LINK}")])  # attached without --sources
        code, out, _ = self.cli("check", pid, "--sources", str(self.sources))
        self.assertEqual(code, 1)
        self.assertIn(REFUSED, out)
        code, _, err = self.cli("approve", pid, "--sources", str(self.sources))
        self.assertEqual(code, 2)
        self.assertIn(REFUSED, err)
        with connect(self.db) as db:
            self.assertEqual(get_post(db, pid)["status"], "draft")
        code, out, err = self.cli("check", pid)  # without --sources the private repositories are unknown
        self.assertEqual(code, 0, out + err)

    def test_documented_caveat_without_sources_nothing_is_detected(self):
        """The #9 caveat, pinned: refusal exists only when --sources names the config."""
        code, out, err = self.cli("draft", "--account", "my-masto", "--text", f"See {PRIVATE_LINK}")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(REFUSED, out + err)
        self.assertEqual(self.count_posts(), 1)
        code, out, err = self.cli("draft", "--account", "my-masto", "--text", "Plain", "--source-url", PRIVATE_LINK)
        self.assertEqual(code, 0, out + err)

    def test_sources_config_without_private_repositories_refuses_nothing(self):
        public_only = self.tmp / "public-only.json"
        public_only.write_text(json.dumps({"schema_version": 1, "sources": [
            {"id": "pub", "kind": "github_releases", "repo": "example-org/public-tool", "sensitivity": "public"}]}),
            encoding="utf-8")
        code, out, err = self.cli("draft", "--account", "my-masto", "--text", f"See {PRIVATE_LINK}",
                                  "--sources", str(public_only))
        self.assertEqual(code, 0, out + err)

    def test_unusable_sources_config_fails_closed(self):
        for name, body in (("malformed", "{not json"), ("missing", None)):
            with self.subTest(name=name):
                path = self.tmp / f"{name}.json"
                if body is not None:
                    path.write_text(body, encoding="utf-8")
                code, out, err = self.cli("draft", "--account", "my-masto", "--text", f"See {PUBLIC_LINK}",
                                          "--sources", str(path))
                self.assertNotEqual(code, 0, out + err)
                self.assertEqual(self.count_posts(), 0)


if __name__ == "__main__":
    unittest.main()
