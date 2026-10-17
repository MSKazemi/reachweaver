"""Synthetic, offline regression coverage for the first creative growth slice."""
from __future__ import annotations
import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reachweaver.cli import main, make_parser
from reachweaver.creative_preflight import SCHEMA, analyze, check_file, render_text
from reachweaver.policy import ValidationError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/creative-preflight.example.json"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def case(platform="linkedin", fmt="text"):
    return {
        "schema_version": SCHEMA,
        "topic": "Kubernetes incident diagnosis",
        "audience": {"description": "Developers investigating incidents",
                     "keywords": ["Kubernetes", "incident"]},
        "goal": "qualified_visits",
        "facts": [{"id": "f1", "claim": "A synthetic troubleshooting example exists",
                   "source_url": "https://example.org/public-example"}],
        "candidates": [{"id": "a", "platform": platform, "format": fmt,
                        "text": ("Kubernetes incident diagnosis begins with a useful question.\n"
                                 "Learn to investigate a synthetic incident. Read the guide."),
                        "evidence_refs": ["f1"], "cta": "Read the guide."}],
    }


class PreflightTest(unittest.TestCase):
    def test_no_send_or_network(self):
        r = analyze(case())
        for field in ("publishes", "schedules", "network_access", "database_access", "model_calls"):
            self.assertFalse(r[field])
        self.assertEqual(r["status"], "read_only_no_publish")
        item = r["candidates"][0]
        self.assertEqual(item["editorial_points"], 100)
        self.assertEqual(sum(x["weight"] for x in item["checks"]), 100)
        self.assertIn("not a ranking formula", r["limitations"])

    def test_links_are_asserted_not_verified(self):
        checks = analyze(case())["candidates"][0]["checks"]
        source = next(x for x in checks if x["code"] == "source_linkage")
        self.assertEqual(source["basis"], "owner_reported_not_verified")

    def test_missing_facts_report_unknown(self):
        d = case()
        d["candidates"][0]["evidence_refs"] = []
        item = analyze(d)["candidates"][0]
        self.assertEqual(item["editorial_points"], 80)
        self.assertEqual(item["assessed_weight"], 80)

    def test_formats_and_accessibility(self):
        d = case("instagram", "image")
        row = analyze(d)["candidates"][0]
        self.assertEqual(next(x["status"] for x in row["checks"] if x["code"] == "accessibility"), "improve")
        d["candidates"][0]["format"] = "discussion"
        self.assertEqual(next(x["status"] for x in analyze(d)["candidates"][0]["checks"]
                              if x["code"] == "platform_format"), "improve")
        d = case("tiktok", "short_video")
        d["candidates"][0]["captions"] = True
        self.assertEqual(next(x["status"] for x in analyze(d)["candidates"][0]["checks"]
                              if x["code"] == "accessibility"), "met")

    def test_repeated_hashtags(self):
        d = case()
        d["candidates"][0]["text"] += " #Kubernetes #Kubernetes"
        self.assertTrue(any(x["code"] == "hashtag_repeated"
                            for x in analyze(d)["candidates"][0]["additional_findings"]))

    def test_repeated_wording_across_channels(self):
        d = case()
        d["candidates"].append(dict(d["candidates"][0], id="b", platform="bluesky"))
        self.assertTrue(any(x["code"] == "cross_platform_duplicate"
                            for x in analyze(d)["candidates"][0]["additional_findings"]))

    def test_human_written_surface_has_no_hook_generation(self):
        self.assertEqual(analyze(case("devto", "tutorial"))["hook_brainstorm_prompts"], [])

    def test_bad_structure(self):
        d = case()
        d["candidates"][0]["evidence_refs"] = ["unknown"]
        with self.assertRaises(ValidationError):
            analyze(d)
        d = case()
        d["candidates"].append(dict(d["candidates"][0]))
        with self.assertRaises(ValidationError):
            analyze(d)
        d = case()
        d["oops"] = "unknown_field"
        with self.assertRaises(ValidationError):
            analyze(d)

    def test_credentials_refused_safely(self):
        d = case()
        d["candidates"][0]["text"] += " ghp_abcdefghijk12345"
        with self.assertRaises(ValidationError) as err:
            analyze(d)
        self.assertNotIn("ghp_abcdef", str(err.exception))

    def test_invalid_url_port(self):
        d = case()
        d["facts"][0]["source_url"] = "https://example.org:invalid/thing"
        with self.assertRaises(ValidationError):
            analyze(d)

    def test_file_input_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "case.json"
            text = json.dumps(case())
            path.write_text(text, encoding="utf-8")
            self.assertEqual(check_file(path)["candidate_count"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            link = Path(temp) / "symlink.json"
            link.symlink_to(path)
            with self.assertRaises(ValidationError):
                check_file(link)
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(ValidationError):
                check_file(path)

    def test_human_facing_output_does_not_echo_source_content(self):
        output = render_text(analyze(case()))
        self.assertIn("offline editorial review", output)
        self.assertNotIn("A synthetic troubleshooting example exists", output)


    def test_reddit_and_hacker_news_get_no_generated_prompts(self):
        for platform, fmt in (("reddit", "discussion"), ("hackernews", "human_only")):
            with self.subTest(platform=platform):
                report = analyze(case(platform, fmt))
                self.assertEqual(report["hook_brainstorm_prompts"], [])
                self.assertTrue(report["candidates"][0]["human_only"])
                self.assertIn("Human-only destination", render_text(report))
        self.assertFalse(analyze(case())["candidates"][0]["human_only"])
        self.assertEqual(len(analyze(case())["hook_brainstorm_prompts"]), 3)

    def test_unspaced_script_opening_is_unknown_not_too_short(self):
        d = case("zhihu", "answer")
        d["candidates"][0]["text"] = "Kubernetes 事故诊断从一个有用的问题开始，阅读指南。\nRead the guide."
        opening = next(x for x in analyze(d)["candidates"][0]["checks"] if x["code"] == "opening_clarity")
        self.assertEqual(opening["status"], "unknown")

    def test_values_are_normalized_before_checks(self):
        d = case()
        d["candidates"][0]["format"] = " text "
        row = analyze(d)["candidates"][0]
        self.assertEqual(row["format"], "text")
        self.assertEqual(next(x["status"] for x in row["checks"] if x["code"] == "platform_format"), "met")

    def test_errors_name_the_field(self):
        d = case()
        del d["candidates"][0]["text"]
        with self.assertRaisesRegex(ValidationError, "missing required field.*text"):
            analyze(d)
        d = case()
        d["candidates"][0]["cta"] = 5
        with self.assertRaisesRegex(ValidationError, "cta must be a string"):
            analyze(d)
        d = case()
        d["candidates"][0]["platform"] = "myspace"
        with self.assertRaisesRegex(ValidationError, "platform must be one of"):
            analyze(d)

    def test_unreadable_files_are_validation_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "brief.json"
            for data, message in ((b'{"a": "caf\xe9"}', "not UTF-8"), (b"[" * 100_000 + b"]" * 100_000, "too deep"),
                                  (b" " * 300_000 + b"{}", "larger than")):
                path.write_bytes(data)
                with self.assertRaisesRegex(ValidationError, message):
                    check_file(path)
            with self.assertRaisesRegex(ValidationError, "regular file"):
                check_file(Path(temp))


class PreflightCliTest(unittest.TestCase):
    """creative-preflight is an ordinary subcommand, so the installed console script reaches it."""

    def test_registered_on_the_real_parser(self):
        action = next(a for a in make_parser()._actions if a.dest == "command")
        self.assertIn("creative-preflight", action.choices)

    def test_runs_through_cli_main_offline_without_a_database(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "never.sqlite"
            with patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
                    patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
                code, out, _ = run_cli(["--db", str(db), "creative-preflight", "--file", str(EXAMPLE), "--json"])
                self.assertEqual(code, 0)
                report = json.loads(out)
                self.assertEqual(report["schema_version"], SCHEMA)
                self.assertFalse(report["publishes"])
                code, out, _ = run_cli(["--db", str(db), "creative-preflight", "--file", str(EXAMPLE)])
                self.assertEqual(code, 0)
                self.assertIn("offline editorial review", out)
            self.assertFalse(db.exists())

    def test_bad_input_exits_2_with_an_error_line(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text("{bad", encoding="utf-8")
            code, out, err = run_cli(["creative-preflight", "--file", str(path)])
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith("ERROR: creative-preflight: invalid JSON"), err)
            self.assertNotIn("Traceback", err)

    def test_module_entry_point_has_no_command_hack(self):
        for main_py in (ROOT / "reachweaver/__main__.py",
                        ROOT / ".claude/skills/reachweaver/scripts/reachweaver/__main__.py"):
            self.assertEqual(main_py.read_text(encoding="utf-8"), "from .cli import main\n\nraise SystemExit(main())\n")

    def test_bundled_skill_run_py_reaches_the_command(self):
        with tempfile.TemporaryDirectory() as temp:
            done = subprocess.run([sys.executable, str(ROOT / ".claude/skills/reachweaver/scripts/run.py"),
                                   "creative-preflight", "--file", str(EXAMPLE), "--json"],
                                  cwd=temp, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["status"], "read_only_no_publish")
            self.assertFalse((ROOT / ".claude/skills/reachweaver/scripts/creative_preflight.py").exists())


if __name__ == "__main__":
    unittest.main()
