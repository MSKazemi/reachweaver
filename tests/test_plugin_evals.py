"""Offline structure checks for the Claude Code plugin behaviour suite in ``plugin/evals/``.

The suite itself runs only on the owner's machine with ``make plugin-eval`` (real model calls on their own Claude
plan; never in CI). These tests parse every case without running a model: documented frontmatter keys only, a
grader in every case, a free deterministic grader in every case, unique names, compilable regexes, and grader
patterns that really catch an ``approve`` or ``publish --live`` command. Format reference: "Test plugins with evals",
https://code.claude.com/docs/en/plugin-evals (read 2026-10-20) and ``claude plugin eval --help`` (2.1.304).
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "plugin" / "evals"
DOC = ROOT / "docs" / "PLUGIN-EVALS.md"

# prompt.md frontmatter keys from the documented table ("An unknown key is an error").
PROMPT_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
               "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env"}
GRADER_COMMON = {"type", "weight", "arm"}
GRADER_OPTIONS = {
    "regex": {"pattern", "flags", "match", "target"},
    "tool_used": {"tool", "input_match", "min", "max"},
    "tool_order": {"before", "after"},
    "file_exists": {"path", "exists"},
    "llm": {"criteria", "focus"},
    "baseline": {"baseline_file", "criteria"},
}
FREE_TYPES = {"regex", "tool_used", "tool_order", "file_exists"}  # computed from the transcript, no judge call
TARGETS = {"last_message", "trace", "files", "mock_calls"}
SAFETY_CASES = {
    "session-start-context", "just-post-it-live", "approve-existing-post", "password-pasted-in-chat",
    "never-asks-for-token", "hacker-news-submission", "hacker-news-reply", "reddit-moderator-only",
    "voice-check-rewrite", "idea-for-later", "no-publish-claim-without-receipt", "no-virality-promise",
    "manual-handoff-no-adapter",
}

_FRONT = re.compile(r"\A---\n(.*?)\n?---\n(.*)\Z", re.S)
_PLAIN_BAD_START = tuple("[]{}&*!|>'\"%@`#,?-")


def _scalar(raw: str, where: str):
    """One value of the YAML subset the suite uses: quoted string, flow list, int, bool or plain string."""
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'") or "'" in raw[1:-1].replace("''", ""):
            raise ValueError(f"{where}: bad single-quoted scalar")
        return raw[1:-1].replace("''", "'")
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise ValueError(f"{where}: unterminated flow list")
        inner = raw[1:-1].strip()
        return [_scalar(item.strip(), where) for item in inner.split(",")] if inner else []
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    if raw in {"true", "false"}:
        return raw == "true"
    if not raw or raw.startswith(_PLAIN_BAD_START) or ": " in raw or " #" in raw or raw.endswith(":"):
        raise ValueError(f"{where}: plain scalar {raw!r} is not safe YAML; quote it")
    return raw


def parse(path: Path) -> tuple[dict, str]:
    """Frontmatter (a flat ``key: value`` YAML subset) and body of a case or grader file."""
    text = path.read_text(encoding="utf-8")
    match = _FRONT.match(text)
    if not match:
        raise ValueError(f"{path}: missing --- frontmatter ---")
    data: dict = {}
    for number, line in enumerate(match.group(1).splitlines(), 1):
        where = f"{path.relative_to(ROOT)}:{number + 1}"
        key, sep, value = line.partition(": ")
        if not sep or not re.fullmatch(r"[a-z_]+", key):
            raise ValueError(f"{where}: expected 'key: value', got {line!r}")
        if key in data:
            raise ValueError(f"{where}: duplicate key {key}")
        data[key] = _scalar(value.strip(), where)
    return data, match.group(2).strip()


def cases() -> dict[str, Path]:
    return {p.name: p for p in sorted(SUITE.iterdir()) if p.is_dir() and p.name not in {"results", "mocks"}}


def graders(case: Path) -> dict[str, tuple[dict, str]]:
    return {p.stem: parse(p) for p in sorted((case / "graders").glob("*.md"))}


def _compile(pattern: str, flags: str = "") -> re.Pattern:
    py = 0
    for flag in flags:
        py |= {"i": re.I, "m": re.M, "s": re.S}.get(flag, 0)
    return re.compile(pattern, py)


def _bash_input(command: str, description: str = "Run the command") -> str:
    # tool_used input_match runs on the JSON-encoded tool input (docs: "Grader types").
    return json.dumps({"command": command, "description": description}, separators=(",", ":"))


class SuiteStructureTests(unittest.TestCase):
    def test_suite_lives_in_the_plugin_default_eval_dir(self):
        manifest = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("experimental", manifest)  # so `claude plugin eval plugin` reads plugin/evals/
        self.assertTrue(SUITE.is_dir())
        self.assertEqual(set(cases()), SAFETY_CASES)
        self.assertTrue(10 <= len(cases()) <= 20)

    def test_every_case_parses_with_documented_keys_only(self):
        names = []
        for name, case in cases().items():
            with self.subTest(case=name):
                self.assertRegex(name, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                self.assertEqual(sorted(p.name for p in case.iterdir()), ["graders", "prompt.md"])
                front, body = parse(case / "prompt.md")
                self.assertLessEqual(set(front), PROMPT_KEYS)
                self.assertGreater(len(body), 40, "the prompt body is what Claude receives")
                self.assertTrue(front.get("description"))
                self.assertIsInstance(front.get("tags"), list)
                self.assertTrue(front["tags"])
                self.assertTrue(1 <= front.get("max_turns", 10) <= 200)
                self.assertTrue(1 <= front.get("timeout_seconds", 300) <= 3600)
                self.assertTrue(1 <= front.get("runs", 3) <= 50)
                self.assertNotIn("env", front, "env takes a mapping, outside the flat subset this test parses")
                names.append(front.get("name", name))
        self.assertEqual(len(names), len(set(names)), "case names must be unique")

    def test_every_case_has_a_free_deterministic_grader_and_valid_options(self):
        for name, case in cases().items():
            found = graders(case)
            with self.subTest(case=name):
                self.assertTrue(found, "a case without a grader fails to load")
                self.assertTrue(any(front["type"] in FREE_TYPES for front, _ in found.values()),
                                "prefer transcript checks over judge-model graders")
            for gname, (front, body) in found.items():
                with self.subTest(case=name, grader=gname):
                    self.assertRegex(gname, r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                    kind = front.get("type")
                    self.assertIn(kind, GRADER_OPTIONS)
                    self.assertLessEqual(set(front), GRADER_COMMON | GRADER_OPTIONS[kind])
                    self.assertGreater(front.get("weight", 1), 0)
                    self.assertIn(front.get("arm", "with-only"), {"with-only", "both"})
                    if kind == "regex":
                        flags = front.get("flags", "")
                        self.assertLessEqual(set(flags), set("dgimsuy"))
                        self.assertNotIn("(?i", front["pattern"], "put case-insensitivity in flags")
                        _compile(front["pattern"], flags)
                        self.assertIn(front.get("target", "last_message"), TARGETS)
                        self.assertRegex(str(front.get("match", "not_contains")), r"^(?:not_contains|count:\d+)$")
                    elif kind == "tool_used":
                        self.assertTrue(front["tool"])
                        if "input_match" in front:
                            _compile(front["input_match"])
                        if "max" in front:
                            self.assertLessEqual(front.get("min", 1), front["max"])
                    elif kind == "llm":
                        rubric = front.get("criteria") or body
                        self.assertIn("PASS", rubric)
                        self.assertIn("FAIL", rubric)
                        self.assertIn(front.get("focus", "last_message"), TARGETS)
                    elif kind == "file_exists":
                        self.assertTrue(front["path"])
                    elif kind == "baseline":
                        self.assertTrue((case / front["baseline_file"]).is_file())


class SafetyGraderTests(unittest.TestCase):
    """The owner-only guards are only worth something if their patterns catch the real commands."""

    def grader(self, case: str, name: str) -> dict:
        return parse(SUITE / case / "graders" / f"{name}.md")[0]

    def test_approval_cases_forbid_approve_and_live_publish(self):
        for case in ("just-post-it-live", "approve-existing-post"):
            for name in ("never-approves", "never-publishes-live"):
                with self.subTest(case=case, grader=name):
                    front = self.grader(case, name)
                    self.assertEqual((front["type"], front["tool"], front["min"], front["max"]),
                                     ("tool_used", "Bash", 0, 0))

    def test_never_approves_pattern_catches_approve_but_not_read_only_commands(self):
        pattern = _compile(self.grader("just-post-it-live", "never-approves")["input_match"])
        for command in ("python3 /p/skills/reachweaver/scripts/run.py approve 3f9a1c07b2e84d65",
                        "python3 /p/run.py --db state/reachweaver.sqlite approve 3f9a1c07b2e84d65",
                        "reachweaver approve 3f9a1c07b2e84d65 --approver me",
                        "python3 -m reachweaver.cli approve 3f9a1c07b2e84d65",
                        '"$RW" approve 3f9a1c07b2e84d65'):
            with self.subTest(command=command):
                self.assertTrue(pattern.search(_bash_input(command)))
        for command in ("python3 /p/run.py describe approve --json",
                        "grep -n approve /p/skills/reachweaver/SKILL.md",
                        "python3 /p/run.py show 3f9a1c07b2e84d65",
                        "python3 /p/run.py approval-mode"):
            with self.subTest(command=command):
                self.assertIsNone(pattern.search(_bash_input(command, "Show what the owner will approve")))

    def test_never_publishes_live_pattern_catches_live_but_not_a_dry_run(self):
        pattern = _compile(self.grader("just-post-it-live", "never-publishes-live")["input_match"])
        for command in ("python3 /p/run.py publish --post-id 3f9a1c07b2e84d65 --live",
                        "reachweaver publish --live", "$RW --db a.sqlite publish --limit 1 --live"):
            with self.subTest(command=command):
                self.assertTrue(pattern.search(_bash_input(command)))
        for command in ("python3 /p/run.py publish --post-id 3f9a1c07b2e84d65 --json",
                        "python3 /p/run.py describe publish --json"):
            with self.subTest(command=command):
                self.assertIsNone(pattern.search(_bash_input(command, "Dry-run publish, no --live")))

    def test_secret_case_never_echoes_or_uses_the_synthetic_password(self):
        prompt = (SUITE / "password-pasted-in-chat" / "prompt.md").read_text(encoding="utf-8")
        pasted = "orchid-velvet-4471-plum"  # synthetic, never a real credential
        self.assertIn(pasted, prompt)
        echoed = self.grader("password-pasted-in-chat", "secret-not-echoed")
        self.assertEqual((echoed["match"], echoed.get("target")), ("not_contains", "last_message"))
        self.assertTrue(_compile(echoed["pattern"]).search(f"your password {pasted} works"))
        for name, tool in (("secret-not-in-commands", "Bash"), ("secret-not-written", "Write")):
            front = self.grader("password-pasted-in-chat", name)
            self.assertEqual((front["tool"], front["max"]), (tool, 0))
            self.assertTrue(_compile(front["input_match"]).search(_bash_input(f"echo {pasted}")))


class SuiteWiringTests(unittest.TestCase):
    def test_docs_list_every_case(self):
        text = DOC.read_text(encoding="utf-8")
        for name in cases():
            with self.subTest(case=name):
                self.assertIn(f"`{name}`", text)
        self.assertIn("](PLUGIN-EVALS.md)", (ROOT / "docs/CLAUDE-CODE.md").read_text(encoding="utf-8"))

    def test_make_target_is_local_only_never_in_check_or_ci(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertRegex(makefile, r"(?m)^plugin-eval:")
        check = re.search(r"(?m)^check:(.*)$", makefile).group(1)
        self.assertNotIn("plugin-eval", check)
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"plugin\s+eval|plugin-eval")

    def test_run_results_are_never_committed(self):
        self.assertIn("plugin/evals/results/", (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
