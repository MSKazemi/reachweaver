"""One folder that holds everything that is yours: the marker, the layout, gather, and repair after a copy (offline).

The scenario these tests exist for: a user runs ReachWeaver inside one folder, copies that folder to another place
(another computer), and everything still works after one command. Nothing here sends, contacts a platform or reads a
credential.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mastodon_media import png  # noqa: E402

from reachweaver import attachments, home  # noqa: E402
from reachweaver.cli import main  # noqa: E402
from reachweaver.storage import (add_post, approval_binding_problem, approval_payload_sha256, connect,  # noqa: E402
                                 get_post, set_post_media, transition)

ROOT = Path(__file__).resolve().parents[1]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = Path(tmp.name)
        self.ws = self.top / "my-workspace"
        self.outside = self.top / "Pictures"
        self.outside.mkdir()
        self.image = self.outside / "chart.png"
        self.image.write_bytes(png())

    def cli(self, cwd, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, env or {}, clear=True), contextlib.chdir(cwd), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = main(["--plain", *argv])
        return code, out.getvalue(), err.getvalue()

    def init(self):
        code, out, err = self.cli(self.top, "workspace", "--init", str(self.ws))
        self.assertEqual(code, 0, err)
        return out

    def post_with_image(self, approved=True):
        with contextlib.chdir(self.ws), connect("state/reachweaver.sqlite") as db:
            pid = add_post(db, "my-mastodon", "mastodon", "A post that carries a chart")
            set_post_media(db, pid, [attachments.build_attachment(self.image, "A described chart")])
            if approved:
                transition(db, pid, "approve", approver="tester", approval_channel="cli_flag", ai_label="not_needed")
        return pid


class TheMarkerAndTheLayout(Base):
    def test_init_creates_the_one_folder_layout_without_a_secret(self):
        self.init()
        for rel in ("config/accounts.json", "state", "media", "logs", ".reachweaver-workspace", "secrets.env.example",
                    ".gitignore", "README-WORKSPACE.txt"):
            self.assertTrue((self.ws / rel).exists(), rel)
        for rel in ("state", "media", "logs"):
            self.assertEqual(stat.S_IMODE((self.ws / rel).stat().st_mode), 0o700, rel)
        template = (self.ws / "secrets.env.example").read_text(encoding="utf-8")
        self.assertIn("REACHWEAVER_BSKY_APP_PASSWORD=\n", template)  # names only, no values
        self.assertTrue(all(line.startswith("#") or line.endswith("=") for line in template.splitlines()))
        self.assertIn("secrets.env", (self.ws / ".gitignore").read_text(encoding="utf-8"))
        self.assertFalse((self.ws / "secrets.env").exists())

    def test_init_never_overwrites_or_moves_what_is_already_there(self):
        self.ws.mkdir()
        (self.ws / "config").mkdir()
        (self.ws / "config/accounts.json").write_text('{"accounts": []}', encoding="utf-8")
        (self.ws / "notes.txt").write_text("mine", encoding="utf-8")
        out = self.init()
        self.assertIn("kept    config/accounts.json", out)
        self.assertEqual((self.ws / "config/accounts.json").read_text(encoding="utf-8"), '{"accounts": []}')
        self.assertEqual((self.ws / "notes.txt").read_text(encoding="utf-8"), "mine")
        self.init()  # idempotent
        self.assertEqual(sorted(p.name for p in (self.ws / "state").iterdir()), [])

    def test_commands_use_the_workspace_from_anywhere_inside_it_and_with_home(self):
        self.init()
        sub = self.ws / "logs"
        self.assertEqual(self.cli(sub, "init")[0], 0)
        code, out, err = self.cli(sub, "draft", "--account", "my-bluesky", "--text", "A draft kept inside the workspace folder")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.ws / "state/reachweaver.sqlite").is_file())
        self.assertFalse((sub / "state").exists())
        code, listed, _ = self.cli(self.top, "--home", str(self.ws), "list")
        self.assertEqual(code, 0)
        self.assertIn("my-bluesky", listed)

    def test_home_must_point_at_a_marked_workspace(self):
        code, _, err = self.cli(self.top, "--home", str(self.top), "list")
        self.assertEqual(code, 2)
        self.assertIn("not a ReachWeaver workspace", err)

    def test_explicit_flags_and_settings_beat_the_workspace(self):
        self.init()
        other = self.top / "elsewhere.sqlite"
        self.assertEqual(self.cli(self.ws, "--db", str(other), "init")[0], 0)
        self.assertTrue(other.is_file())
        self.assertFalse((self.ws / "state/reachweaver.sqlite").exists())
        via_env = self.top / "env.sqlite"
        self.assertEqual(self.cli(self.ws, "init", env={"REACHWEAVER_DB": str(via_env)})[0], 0)
        self.assertTrue(via_env.is_file())

    def test_without_a_marker_nothing_changes_and_the_choice_does_not_leak_between_runs(self):
        self.init()
        bare = self.top / "plain-folder"
        bare.mkdir()
        self.cli(self.ws, "init")  # activates the workspace in this process...
        self.assertEqual(self.cli(bare, "init")[0], 0)  # ...but a later run elsewhere starts clean
        self.assertTrue((bare / "state/reachweaver.sqlite").is_file())
        self.assertIsNone(home.find_root(bare))

    def test_home_module_resolves_paths_only(self):
        text = (ROOT / "reachweaver/home.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("os.environ", "getenv", "urllib", "socket", "subprocess", "sqlite3", "open("):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, code)


class GatherAndRepair(Base):
    def test_gather_pulls_outside_files_into_media_and_keeps_the_approval_valid(self):
        self.init()
        pid = self.post_with_image()
        with contextlib.chdir(self.ws), connect("state/reachweaver.sqlite") as db:
            hash_before = approval_payload_sha256(get_post(db, pid))
        code, out, _ = self.cli(self.ws, "workspace", "--gather")
        self.assertEqual(code, 0, out)
        self.assertIn("Dry run", out)
        self.assertEqual(list((self.ws / "media").iterdir()), [])
        code, out, _ = self.cli(self.ws, "workspace", "--gather", "--apply")
        self.assertEqual(code, 0, out)
        copied = list((self.ws / "media").iterdir())
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].read_bytes(), self.image.read_bytes())
        self.assertEqual(stat.S_IMODE(copied[0].stat().st_mode), 0o600)
        with contextlib.chdir(self.ws), connect("state/reachweaver.sqlite") as db:
            row = get_post(db, pid)
            self.assertEqual(approval_payload_sha256(row), hash_before)
            self.assertIsNone(approval_binding_problem(row))
            self.assertEqual(row["status"], "approved")
            self.assertIn(str(copied[0]), row["media_json"])
        self.assertIn("Nothing to do", self.cli(self.ws, "workspace", "--gather", "--apply")[1])  # idempotent

    def test_gather_never_copies_a_file_that_changed(self):
        self.init()
        self.post_with_image()
        self.image.write_bytes(png(note=b"edited after attaching"))
        code, out, _ = self.cli(self.ws, "workspace", "--gather", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("changed", out)
        self.assertEqual(list((self.ws / "media").iterdir()), [])

    def test_the_whole_story_copy_the_folder_then_repair(self):
        self.init()
        pid = self.post_with_image()
        self.assertEqual(self.cli(self.ws, "workspace", "--gather", "--apply")[0], 0)
        self.assertEqual(self.cli(self.ws, "workspace")[0], 0)
        new_computer = self.top / "elsewhere" / "my-workspace"
        new_computer.parent.mkdir()
        shutil.copytree(self.ws, new_computer)
        shutil.rmtree(self.ws)  # the old computer is gone; the recorded absolute path no longer exists
        shutil.rmtree(self.outside)
        code, out, _ = self.cli(new_computer, "workspace")
        self.assertEqual(code, 1)
        self.assertIn("attachment_missing", out)
        code, out, _ = self.cli(new_computer, "workspace", "--repair")
        self.assertEqual(code, 0, out)
        self.assertIn("Dry run", out)
        code, out, _ = self.cli(new_computer, "workspace", "--repair", "--apply")
        self.assertEqual(code, 0, out)
        code, out, _ = self.cli(new_computer, "workspace", "--json")
        report = json.loads(out)
        self.assertEqual((code, report["ready_to_copy"]), (0, True))
        self.assertEqual(report["workspace"], str(new_computer.resolve()))
        with contextlib.chdir(new_computer), connect("state/reachweaver.sqlite") as db:
            row = get_post(db, pid)
            self.assertEqual(row["status"], "approved")
            self.assertIsNone(approval_binding_problem(row))
            self.assertIn(str(new_computer.resolve() / "media"), row["media_json"])

    def test_repair_finds_the_file_by_content_not_by_name_and_reports_what_it_cannot_find(self):
        self.init()
        self.post_with_image()
        self.assertEqual(self.cli(self.ws, "workspace", "--gather", "--apply")[0], 0)
        (copied,) = (self.ws / "media").iterdir()
        renamed = copied.with_name("renamed-by-user.png")
        copied.rename(renamed)
        self.image.unlink()
        code, out, _ = self.cli(self.ws, "workspace", "--repair", "--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("renamed-by-user.png", out)
        renamed.unlink()  # now it is really gone
        with contextlib.chdir(self.ws), connect("state/reachweaver.sqlite") as db:
            db.execute("UPDATE posts SET media_json=replace(media_json, ?, ?)", ("renamed-by-user.png", "gone.png"))
        code, out, _ = self.cli(self.ws, "workspace", "--repair", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("not found in media/", out)

    def test_gather_and_repair_need_a_workspace_and_refuse_to_mix(self):
        bare = self.top / "bare"
        bare.mkdir()
        code, _, err = self.cli(bare, "workspace", "--gather")
        self.assertEqual(code, 2)
        self.assertIn("workspace --init", err)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.cli(bare, "workspace", "--gather", "--repair")


class TheReportKnowsTheLayout(Base):
    def test_warns_about_secrets_and_outside_files_but_does_not_block(self):
        self.init()
        self.post_with_image()
        (self.ws / ".env.reachweaver").write_text("X=1\n", encoding="utf-8")
        code, out, _ = self.cli(self.ws, "workspace", "--json")
        report = json.loads(out)
        codes = {p["code"]: p["level"] for p in report["problems"]}
        self.assertEqual(codes["secrets_in_folder"], "warning")
        self.assertEqual(codes["attachment_outside_workspace"], "warning")
        self.assertEqual((code, report["ready_to_copy"]), (0, True))
        self.assertNotIn("X=1", out)  # file names only, never contents

    def test_a_folder_without_the_marker_is_told_how_to_get_one(self):
        with contextlib.chdir(self.top), connect("state/reachweaver.sqlite") as db:
            add_post(db, "my-bluesky", "bluesky", "A draft in an unmarked folder")
        shutil.copy(ROOT / "config/accounts.example.json", self.top / "accounts.json")
        code, out, _ = self.cli(self.top, "--accounts", "accounts.json", "workspace", "--json")
        self.assertIn("not_a_marked_workspace", [p["code"] for p in json.loads(out)["problems"]])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
