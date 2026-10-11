"""A secret pasted into a comment never reaches excerpts, review sheets or drafts."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.engagement import redact_private_data, triage_files

# Built at runtime so the repository secret scan sees no literal token shapes.
GHP = "ghp" + "_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
APP_PASSWORD = "-".join(["abcd", "efgh", "ijkl", "mnop"])
ENV_VALUE = "opaque" + "EnvSecret" + "4242"


def comment(i: str, text: str) -> dict:
    return {"id": i, "platform": "bluesky", "account_id": "my-bluesky", "author_handle": "someone",
            "author_is_owner": False, "text": text, "url": f"https://bsky.app/profile/x.example/post/{i}",
            "created_at": "2026-10-01T09:00:00Z", "in_reply_to": None, "thread_root_url": None}


class EngagementSecretRedactionTests(unittest.TestCase):
    def test_redact_private_data_strips_token_shapes(self):
        for secret in (GHP, APP_PASSWORD):
            with self.subTest(secret=secret[:4]):
                text, changed = redact_private_data(f"Bug: the CLI crashed after I set {secret} as my key")
                self.assertNotIn(secret, text)
                self.assertTrue(changed)
        text, _ = redact_private_data("Bug when token=" + "s3cr3tValue99" + " is set")
        self.assertNotIn("s3cr3tValue99", text)

    def test_tokens_never_reach_report_sheet_or_issue_drafts(self):
        items = [comment("1", f"Bug report: crash after login, my token is {GHP} please fix"),
                 comment("2", f"Feature request: support app password {APP_PASSWORD} rotation"),
                 comment("3", f"How do I configure this? I exported {ENV_VALUE} already")]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"REACHWEAVER_MAIN_TOKEN": ENV_VALUE}):
            src = Path(tmp) / "export.json"
            src.write_text(json.dumps({"schema_version": "reachweaver.engagement-export.v1", "items": items}),
                           encoding="utf-8")
            out_json, out_md, issues = Path(tmp) / "t.json", Path(tmp) / "t.md", Path(tmp) / "issues"
            report, _ = triage_files(src, None, out_json, issues)
            triage_files(src, None, out_md)
            blobs = [json.dumps(report), out_json.read_text(encoding="utf-8"), out_md.read_text(encoding="utf-8")]
            blobs += [p.read_text(encoding="utf-8") for p in issues.glob("*.md")]
            self.assertTrue(list(issues.glob("*.md")), "expected at least one issue draft")
        for blob in blobs:
            for secret in (GHP, APP_PASSWORD, ENV_VALUE):
                self.assertNotIn(secret, blob)


if __name__ == "__main__":
    unittest.main()
