"""Voice profile and voice check (offline, advisory, synthetic posts only).

Covers profile statistics from a file and from the database (opened read-only), the minimum sample, refusing to
overwrite, the private file mode, that no post text reaches the profile, the heuristic tell list (case-insensitive,
whole words, inflections listed explicitly, no hit inside a longer word), the JSON shape, the exit codes and the
optional creative-preflight integration.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver import voice
from reachweaver.cli import main, make_parser
from reachweaver.creative_preflight import SCHEMA as PREFLIGHT_SCHEMA, analyze
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect

ROOT = Path(__file__).resolve().parents[1]
UNIQUE = "zebracorn-marker-sentence-77"


def synthetic_posts(count: int = 12) -> list[dict]:
    """Short, lowercase, link-at-the-end posts with one trailing hashtag; one post carries a unique marker."""
    posts = []
    for i in range(count):
        text = (f"shipped a small fix to the scheduler today. tests are green and the docs are updated.\n"
                f"retry logic now waits {i + 2} seconds #python https://example.org/r/{i}")
        if i == 0:
            text = text.replace("today", f"today {UNIQUE}")
        posts.append({"platform": "bluesky" if i % 2 else "mastodon", "text": text})
    return posts


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def posts_file(self, posts: list[dict], name: str = "posts.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps({"posts": posts}), encoding="utf-8")
        return path


class FeatureTests(unittest.TestCase):
    def test_features_are_plain_counts(self) -> None:
        f = voice.features("I think we did it \u2014 finally! Did it work?\n- one\n- two "
                           "#tag @friend https://x.org \U0001F680")
        self.assertEqual(f["em_dashes"], 1)
        self.assertEqual(f["exclamations"], 1)
        self.assertEqual(f["questions"], 1)
        self.assertEqual(f["list_lines"], 2)
        self.assertEqual(f["line_breaks"], 2)
        self.assertEqual((f["hashtags"], f["mentions"], f["links"], f["emoji"]), (1, 1, 1, 1))
        self.assertGreater(f["first_person_singular"], 0)
        self.assertGreater(f["first_person_plural"], 0)
        self.assertFalse(f["starts_lowercase"])
        self.assertEqual(f["question_opener"], 0)

    def test_question_opener_and_hashtag_position(self) -> None:
        f = voice.features("Ever wondered why builds fail? Here is the reason.\n#ci #builds")
        self.assertEqual(f["question_opener"], 1)
        self.assertTrue(f["hashtags_at_end"])
        self.assertFalse(voice.features("A #ci note that keeps going after the tag")["hashtags_at_end"])


class ProfileTests(TmpCase):
    def test_profile_from_file_has_statistics_and_no_post_text(self) -> None:
        profile = voice.build_profile([(p["text"], p["platform"]) for p in synthetic_posts()])
        self.assertEqual(profile["status"], "ok")
        self.assertEqual(profile["schema_version"], voice.PROFILE_SCHEMA)
        self.assertEqual(profile["post_count"], 12)
        self.assertEqual(set(profile["features"]), set(voice.FEATURE_KEYS))
        for stats in profile["features"].values():
            self.assertLessEqual(stats["p10"], stats["p25"])
            self.assertLessEqual(stats["p25"], stats["median"])
            self.assertLessEqual(stats["median"], stats["p75"])
            self.assertLessEqual(stats["p75"], stats["p90"])
        self.assertEqual(sorted(profile["by_platform"]), ["bluesky", "mastodon"])
        self.assertEqual(profile["by_platform"]["bluesky"]["post_count"], 6)
        self.assertEqual(profile["habits"]["starts_lowercase"], 1.0)
        self.assertEqual(profile["habits"]["link_at_end"], 1.0)
        self.assertIn("scheduler", [w["text"] for w in profile["distinctive_words"]])
        self.assertNotIn("the", [w["text"] for w in profile["distinctive_words"]])  # baseline word
        self.assertEqual(len(profile["sample_sha256"]), 64)
        dumped = json.dumps(profile)
        self.assertNotIn(UNIQUE, dumped)  # a word used by one post never reaches the profile
        self.assertNotIn("example.org", dumped)
        self.assertNotIn("shipped a small fix to the scheduler", dumped)

    def test_platform_needs_five_posts_for_its_own_length_statistics(self) -> None:
        posts = [(p["text"], "bluesky") for p in synthetic_posts(10)] + [("a short mastodon note here", "mastodon")] * 4
        profile = voice.build_profile(posts)
        self.assertIn("bluesky", profile["by_platform"])
        self.assertNotIn("mastodon", profile["by_platform"])
        self.assertEqual(profile["platforms"]["mastodon"], 4)

    def test_platform_filter_keeps_unlabelled_posts_and_drops_other_platforms(self) -> None:
        posts = [(p["text"], None) for p in synthetic_posts(10)] + [("other network text", "threads")]
        profile = voice.build_profile(posts, platform="Bluesky")
        self.assertEqual(profile["post_count"], 10)
        self.assertEqual(profile["skipped"]["other_platform"], 1)
        self.assertEqual(profile["platforms"], {"bluesky": 10})

    def test_insufficient_sample_writes_nothing_and_exits_1(self) -> None:
        source = self.posts_file(synthetic_posts(9))
        out = self.root / "profile.json"
        code, stdout, _ = run_cli(["voice-profile", "--from", str(source), "--out", str(out)])
        self.assertEqual(code, 1)
        self.assertIn("insufficient_sample", stdout)
        self.assertFalse(out.exists())
        code, stdout, _ = run_cli(["voice-profile", "--from", str(source), "--out", str(out), "--json"])
        report = json.loads(stdout)
        self.assertEqual((code, report["status"], report["post_count"], report["written_to"]),
                         (1, "insufficient_sample", 9, None))
        self.assertNotIn("features", report)

    def test_writes_a_new_private_file_and_refuses_to_overwrite(self) -> None:
        source = self.posts_file(synthetic_posts())
        out = self.root / "profile.json"
        code, stdout, _ = run_cli(["voice-profile", "--from", str(source), "--out", str(out)])
        self.assertEqual(code, 0, stdout)
        self.assertIn("mode 600", stdout)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        before = out.read_bytes()
        code, _, err = run_cli(["voice-profile", "--from", str(source), "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(out.read_bytes(), before)
        code, _, err = run_cli(["voice-profile", "--from", str(source), "--out", str(self.root / "no" / "p.json")])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)

    def test_text_and_csv_inputs(self) -> None:
        texts = [p["text"] for p in synthetic_posts()]
        txt = self.root / "posts.txt"
        txt.write_text("\n---\n".join(texts), encoding="utf-8")
        self.assertEqual(voice.build_profile(voice.load_posts_file(txt))["post_count"], 12)
        table = self.root / "posts.csv"
        with table.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Platform", "Content"])
            writer.writerows(["linkedin", t] for t in texts)
        profile = voice.build_profile(voice.load_posts_file(table))
        self.assertEqual(profile["platforms"], {"linkedin": 12})
        bad = self.root / "bad.csv"
        bad.write_text("a,b\n1,2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "text"):
            voice.load_posts_file(bad)

    def test_bad_inputs_are_validation_errors(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text('[{"text": 3}]', encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "string"):
            voice.load_posts_file(bad)
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "invalid JSON"):
            voice.load_posts_file(bad)
        link = self.root / "link.json"
        link.symlink_to(bad)
        with self.assertRaisesRegex(ValidationError, "symlink"):
            voice.load_posts_file(link)
        code, _, err = run_cli(["voice-profile", "--from", str(bad), "--account", "a", "--out", str(self.root / "x")])
        self.assertEqual(code, 2)
        self.assertIn("--from-db", err)


class DatabaseTests(TmpCase):
    def test_from_db_reads_published_posts_read_only(self) -> None:
        db_path = self.root / "state" / "outbox.sqlite"
        with connect(str(db_path)) as db:
            for i, post in enumerate(synthetic_posts(11)):
                pid = add_post(db, "bsky-main", "bluesky", post["text"])
                if i < 10:
                    db.execute("UPDATE posts SET status='published' WHERE id=?", (pid,))
            draft_id = pid  # the 11th stays a draft and is not part of the profile
        digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
        with connect(str(db_path)) as watcher:
            version = watcher.execute("PRAGMA data_version").fetchone()[0]
            changes = watcher.total_changes
            out = self.root / "profile.json"
            code, stdout, err = run_cli(["--db", str(db_path), "voice-profile", "--from-db", "--account", "bsky-main",
                                         "--out", str(out), "--json"])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(stdout)["post_count"], 10)
            self.assertEqual(json.loads(stdout)["source"], "database")
            code, stdout, err = run_cli(["--db", str(db_path), "voice-check", "--profile", str(out),
                                         "--post-id", draft_id, "--json"])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(stdout)["platform"], "bluesky")  # taken from the stored post
            self.assertEqual(watcher.execute("PRAGMA data_version").fetchone()[0], version)
            self.assertEqual(watcher.total_changes, changes)
        self.assertEqual(hashlib.sha256(db_path.read_bytes()).hexdigest(), digest)
        code, _, err = run_cli(["--db", str(db_path), "voice-check", "--post-id", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown post", err)

    def test_missing_database_is_never_created(self) -> None:
        db_path = self.root / "state" / "never.sqlite"
        code, _, err = run_cli(["--db", str(db_path), "voice-profile", "--from-db", "--out", str(self.root / "p.json")])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)
        self.assertFalse(db_path.exists())
        code, _, _ = run_cli(["--db", str(db_path), "voice-check", "--text", "plain words"])
        self.assertEqual(code, 0)
        self.assertFalse(db_path.exists())


class TellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tells = voice.load_tells()

    def ids(self, text: str) -> set[str]:
        return {hit["id"] for hit in voice.find_tells(text, self.tells)}

    def test_tell_list_is_labelled_a_heuristic_not_a_detector(self) -> None:
        data = json.loads(voice.TELLS_FILE.read_text(encoding="utf-8"))
        self.assertIn("not an AI detector", data["label"])
        self.assertIn("No accuracy is claimed", data["label"])
        self.assertGreaterEqual(len(data["tells"]), 20)
        for entry in data["tells"]:
            self.assertTrue(entry["reason"].strip(), entry["id"])
            self.assertIn(entry["severity"], ("info", "warn"))
        self.assertNotRegex(json.dumps(data).lower(), r"\d+\s*% accura|accuracy of")

    def test_phrases_are_case_insensitive_whole_words(self) -> None:
        self.assertIn("delve", self.ids("Let me DELVE into it"))
        self.assertIn("delve", self.ids("delving deeper"))  # listed inflection
        self.assertNotIn("delve", self.ids("Delvecchio wrote the patch"))  # longer word, no hit
        self.assertNotIn("delve", self.ids("undelve is not a word"))
        self.assertIn("game_changer", self.ids("a real Game Changer"))  # hyphen matches a space
        self.assertIn("thrilled_to_announce", self.ids("I’m thrilled   to announce"))  # spacing, curly apostrophe
        self.assertIn("fast_paced_world", self.ids("In today’s fast-paced world"))
        self.assertNotIn("buzzwords", self.ids("leverages are mechanical"))

    def test_patterns_and_structure(self) -> None:
        self.assertIn("not_just_x_its_y", self.ids("It's not just a library, it's a movement."))
        self.assertIn("not_just_x_its_y", self.ids("This is not only fast — it is safe."))
        self.assertIn("em_dash_heavy", self.ids("one — two — three"))
        self.assertNotIn("em_dash_heavy", self.ids("one — two"))
        self.assertIn("rocket_emoji", self.ids("launch \U0001F680\U0001F680"))
        self.assertIn("triplet_lists", self.ids("fast, simple, and secure. cheap, small or quick."))
        self.assertIn("question_opener", self.ids("Ever wondered why? Here is why."))
        self.assertIn("hashtag_stack", self.ids("#a #b #c #d #e"))
        self.assertEqual(self.ids("We fixed the retry bug and wrote down why."), set())

    def test_broken_tell_list_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tells.json"
            path.write_text(json.dumps({"schema_version": voice.TELLS_SCHEMA, "label": "x", "tells": [
                {"id": "a", "kind": "structural", "feature": "nope", "threshold": 1, "severity": "warn",
                 "reason": "r"}]}))
            with self.assertRaisesRegex(ValidationError, "unknown kind or feature"):
                voice.load_tells(path)


class CheckTests(TmpCase):
    def profile(self) -> dict:
        return voice.build_profile([(p["text"], p["platform"]) for p in synthetic_posts()])

    def test_without_profile_only_tells_and_generic_checks(self) -> None:
        report = voice.check_text("In today's fast-paced world, let's dive in! \U0001F680\U0001F680")
        self.assertFalse(report["profile_used"])
        self.assertIsNone(report["comparison_basis"])
        self.assertEqual({f["kind"] for f in report["findings"]}, {"ai_tell"})
        self.assertGreaterEqual(report["counts"]["warn"], 3)
        self.assertFalse(report["blocks"])

    def test_with_profile_flags_features_outside_the_owners_range(self) -> None:
        draft = ("We are THRILLED to announce our new release!!! It changes everything for every team. "
                 "#launch #news #release #oss #python #tools")
        report = voice.check_text(draft, self.profile(), "bluesky")
        codes = {f["code"]: f for f in report["findings"]}
        self.assertEqual(report["comparison_basis"], "platform")
        self.assertIn("voice.exclamations", codes)
        self.assertEqual(codes["voice.hashtags"]["basis"], "platform")
        self.assertEqual(codes["voice.hashtags"]["usual_range"], [1.0, 1.0])
        self.assertIn("your bluesky posts", codes["voice.hashtags"]["message"])
        self.assertIn("habit.starts_lowercase", codes)
        self.assertIn("tell.thrilled_to_announce", codes)
        self.assertEqual(codes["tell.thrilled_to_announce"]["severity"], "warn")
        in_voice = voice.check_text(synthetic_posts()[3]["text"], self.profile(), "bluesky")
        self.assertEqual([f for f in in_voice["findings"] if f["severity"] == "warn"], [])

    def test_a_tell_the_owner_uses_often_is_only_info(self) -> None:
        posts = [(f"leveraging the cache keeps builds fast, entry {i} https://example.org/{i}", "bluesky")
                 for i in range(12)]
        profile = voice.build_profile(posts)
        self.assertEqual(profile["tell_rates"]["buzzwords"], 1.0)
        report = voice.check_text("It is not just leveraging a cache, it is design.", profile, "bluesky")
        finding = next(f for f in report["findings"] if f["code"] == "tell.buzzwords")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("may simply be your style", finding["message"])

    def test_json_shape_and_exit_code(self) -> None:
        out = self.root / "p.json"
        out.write_text(json.dumps(self.profile()), encoding="utf-8")
        draft = self.root / "draft.txt"
        draft.write_text("Let's dive in — really — now.", encoding="utf-8")
        code, stdout, err = run_cli(["voice-check", "--profile", str(out), "--file", str(draft), "--json"])
        self.assertEqual(code, 0, err)
        report = json.loads(stdout)
        self.assertEqual(report["schema_version"], voice.CHECK_SCHEMA)
        self.assertEqual(set(report) >= {"findings", "counts", "draft", "limitations", "tell_list", "profile_hints",
                                         "blocks", "network_access", "model_calls", "database_writes"}, True)
        self.assertEqual(set(report["draft"]), set(voice.FEATURE_KEYS))
        for item in report["findings"]:
            self.assertIn(item["severity"], ("info", "warn"))
            self.assertTrue({"code", "kind", "severity", "message"} <= set(item))
        self.assertGreater(report["counts"]["warn"], 0)  # findings still exit 0: advisory only
        code, stdout, _ = run_cli(["voice-check", "--text", "Let's dive in."])
        self.assertEqual(code, 0)
        self.assertIn("not an AI detector", stdout)

    def test_human_only_destination_gets_a_note(self) -> None:
        report = voice.check_text("A plain note I wrote myself.", None, "reddit")
        self.assertIn("must not rewrite", report["human_only_note"])

    def test_profile_validation(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"schema_version": voice.PROFILE_SCHEMA, "status": "insufficient_sample"}))
        with self.assertRaisesRegex(ValidationError, "status ok"):
            voice.load_profile(bad)
        broken = self.profile()
        broken["features"]["emoji"]["p90"] = "many"
        bad.write_text(json.dumps(broken))
        with self.assertRaisesRegex(ValidationError, "features.emoji"):
            voice.load_profile(bad)
        with self.assertRaisesRegex(ValidationError, "empty"):
            voice.check_text("   ")

    def test_no_network_during_profile_and_check(self) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("network used")), \
                patch.object(socket, "create_connection", side_effect=AssertionError("network used")):
            profile = self.profile()
            voice.check_text("Let's dive in.", profile, "bluesky")


class CliTests(TmpCase):
    def test_registered_and_grouped(self) -> None:
        parser = make_parser()
        for argv in (["voice-profile", "--from", "f", "--out", "o"], ["voice-check", "--text", "t"]):
            self.assertEqual(parser.parse_args(argv).command, argv[0])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["voice-check", "--text", "t", "--file", "f"])  # one draft source only
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["voice-profile", "--from", "f", "--from-db", "--out", "o"])
        from reachweaver.cli_help import GROUPS
        grouped = {name for _, names in GROUPS for name in names}
        self.assertTrue({"voice-profile", "voice-check"} <= grouped)

    def test_creative_preflight_takes_a_voice_profile(self) -> None:
        profile = voice.build_profile([(p["text"], p["platform"]) for p in synthetic_posts()])
        brief = {"schema_version": PREFLIGHT_SCHEMA, "topic": "scheduler retries",
                 "audience": {"description": "developers", "keywords": ["scheduler"]}, "goal": "awareness",
                 "candidates": [{"id": "a", "platform": "bluesky", "format": "text",
                                 "text": "In today's fast-paced world the scheduler is a game-changer!"}]}
        plain = analyze(brief)
        self.assertNotIn("voice_findings", plain["candidates"][0])
        self.assertNotIn("voice_note", plain)
        with_voice = analyze(brief, profile)
        codes = {f["code"] for f in with_voice["candidates"][0]["voice_findings"]}
        self.assertIn("tell.game_changer", codes)
        self.assertIn("voice_note", with_voice)
        self.assertFalse(with_voice["database_access"])
        brief_path, profile_path = self.root / "brief.json", self.root / "profile.json"
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        code, stdout, err = run_cli(["creative-preflight", "--file", str(brief_path), "--voice-profile",
                                     str(profile_path)])
        self.assertEqual(code, 0, err)
        self.assertIn("voice warn: tell.game_changer", stdout)

    def test_bundled_tell_list_and_package_data(self) -> None:
        self.assertEqual((ROOT / ".claude/skills/reachweaver/scripts/reachweaver/voice_tells.json").read_bytes(),
                         voice.TELLS_FILE.read_bytes())
        self.assertIn('"voice_tells.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_never_imports_a_send_path(self) -> None:
        source = (ROOT / "reachweaver/voice.py").read_text(encoding="utf-8")
        for forbidden in ("publishers", "urllib", "http.client", "transition(", "approve"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
