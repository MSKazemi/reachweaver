"""Clone-to-safe-preview onboarding, blocked-platform alternatives and the release gate (issue #27).

1. The repository is copied to a temporary directory (git-tracked plus untracked, non-ignored
   files, as a fresh clone would see them; ignored local config, state and secrets are left
   behind). Every ``python3 -m reachweaver`` line in docs/QUICKSTART.md then runs, in order,
   against that copy with an empty environment, a throwaway HOME and every socket entry point
   patched to refuse. The flow must end in a dry-run preview with no send.
2. Every platform without a live adapter, in both research registries, must have a manual
   alternative or an explicit human-only/unsupported marker, and each export alternative is
   executed offline in the copy.
3. scripts/release_gate.py checks versions, CHANGELOG, docs, the skill bundle and the blocker
   table, and detects a failing test suite.

No test contacts a network host, reads a real credential or publishes.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from reachweaver import onboarding
from reachweaver.capabilities import load_registry as load_capabilities
from reachweaver.distribution import load_registry as load_distribution
from reachweaver.manual_export import MANUAL_PLATFORMS

ROOT = Path(__file__).resolve().parents[1]
CLI_PREFIX = "python3 -m reachweaver"

# Installs a socket guard in a fresh interpreter, imports reachweaver from the copied clone only,
# then runs the CLI. Any connection or DNS attempt is recorded in a marker file and refused.
BOOTSTRAP = textwrap.dedent("""
    import os, socket, sys
    marker = os.environ["RW_NETWORK_MARKER"]
    def refuse(name):
        def inner(*args, **kwargs):
            with open(marker, "a", encoding="utf-8") as fh:
                fh.write(name + "\\n")
            raise RuntimeError("network access attempted via " + name)
        return inner
    socket.socket.connect = refuse("socket.connect")
    socket.socket.connect_ex = refuse("socket.connect_ex")
    socket.create_connection = refuse("socket.create_connection")
    socket.getaddrinfo = refuse("socket.getaddrinfo")
    socket.gethostbyname = refuse("socket.gethostbyname")
    clone = os.environ["RW_CLONE"]
    sys.path.insert(0, clone)
    import reachweaver
    from reachweaver.cli import main
    if not os.path.realpath(reachweaver.__file__).startswith(os.path.realpath(clone) + os.sep):
        sys.exit("reachweaver imported from outside the clone: " + reachweaver.__file__)
    sys.argv = ["reachweaver", *sys.argv[1:]]
    sys.exit(main(sys.argv[1:]))
""")


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


def repository_files(root: Path) -> list[str]:
    """Files a fresh clone of the current tree would contain (tracked or new, never ignored)."""
    if shutil.which("git") and (root / ".git").exists():
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                               "--exclude-standard"], capture_output=True, env=_git_env(), check=False)
        if proc.returncode == 0:
            names = [n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n]
            return [n for n in names if (root / n).is_file() and not (root / n).is_symlink()]
    skip_dirs = {".git", ".claude", "state", "data", "secrets", "__pycache__", "build", "dist", ".venv"}
    skip_files = {"config/accounts.json", "config/brands.json", "config/optout.json", "config/caps.json", ".env"}
    names = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in skip_dirs for part in rel.parts[:-1]) or not path.is_file():
            continue
        if rel.as_posix() in skip_files or path.suffix in {".sqlite", ".db", ".pyc"}:
            continue
        names.append(rel.as_posix())
    return names


def copy_repository(root: Path, destination: Path) -> Path:
    for name in repository_files(root):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    return destination


def quickstart_commands(text: str) -> list[list[str]]:
    """Every ``python3 -m reachweaver`` line inside ```bash fences, in document order."""
    commands, in_bash = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_bash = stripped == "```bash" and not in_bash
            continue
        if in_bash and stripped.startswith(CLI_PREFIX):
            commands.append(shlex.split(stripped)[3:])
    return commands


def subcommand(argv: list[str]) -> str:
    """The CLI subcommand of one argv, skipping the global --db/--accounts options."""
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg in ("--db", "--accounts"):
            skip = True
        elif not arg.startswith("-"):
            return arg
    return argv[0] if argv else ""


def offline_env(home: Path, clone: Path, marker: Path) -> dict:
    """A minimal environment: no REACHWEAVER_* variables, no tokens, a throwaway HOME."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "RW_CLONE": str(clone),
        "RW_NETWORK_MARKER": str(marker),
    }


def load_release_gate():
    spec = importlib.util.spec_from_file_location("release_gate", ROOT / "scripts" / "release_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FreshCloneOnboarding(unittest.TestCase):
    """One copied clone shared by the onboarding tests in this class (copying is the slow part)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.clone = copy_repository(ROOT, base / "reachweaver")
        cls.home = base / "home"
        cls.home.mkdir()
        cls.marker = base / "network-attempts.log"
        cls.bootstrap = base / "bootstrap.py"
        cls.bootstrap.write_text(BOOTSTRAP, encoding="utf-8")
        cls.env = offline_env(cls.home, cls.clone, cls.marker)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def cli(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", str(self.bootstrap), *argv], cwd=self.clone,
                              env=self.env, capture_output=True, text=True, timeout=120, check=False)

    def assertNoNetwork(self):
        attempts = self.marker.read_text(encoding="utf-8") if self.marker.exists() else ""
        self.assertEqual(attempts, "", "network access attempted")

    def test_clone_contains_no_local_secrets_or_state(self):
        self.assertTrue((self.clone / "docs" / "QUICKSTART.md").is_file())
        for ignored in ("config/accounts.json", ".env", "state"):
            self.assertFalse((self.clone / ignored).exists(), ignored)
        self.assertFalse(any(k.startswith("REACHWEAVER_") for k in self.env))

    def test_quickstart_reaches_a_safe_preview_without_credentials(self):
        commands = quickstart_commands((self.clone / "docs" / "QUICKSTART.md").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(commands), 12, "quickstart lost its runnable steps")
        subcommands = [subcommand(argv) for argv in commands]
        for required in ("doctor", "init", "draft", "show", "approve", "publish", "campaign-export",
                         "manual-export"):
            self.assertIn(required, subcommands)
        self.assertLess(subcommands.index("approve"), subcommands.index("publish"))
        self.assertNotIn("--live", [a for argv in commands for a in argv], "quickstart must never go live")

        post_id = None
        outputs: dict[str, str] = {}
        for argv in commands:
            if "POST_ID" in argv:
                self.assertIsNotNone(post_id, "a step uses POST_ID before draft created one")
                argv = [post_id if a == "POST_ID" else a for a in argv]
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.returncode, 0, f"{argv}\n{result.stdout}\n{result.stderr}")
                name = subcommand(argv)
                outputs[name] = result.stdout
                if name == "draft":
                    match = re.search(r"Created draft (\S+) for my-bluesky; not published", result.stdout)
                    self.assertIsNotNone(match, result.stdout)
                    post_id = match.group(1) if match else None
        self.assertIsNotNone(post_id)
        self.assertIn("Live opt-in: disabled", outputs["doctor"])
        self.assertIn("DRY RUN: 1 due approved posts; no requests sent", outputs["publish"])
        self.assertIn(f"{post_id} | my-bluesky | bluesky | approved", outputs["list"])

        # The live path fails closed without the explicit environment opt-in.
        db = ["--db", "state/quickstart.sqlite", "--accounts", "config/accounts.example.json"]
        live = self.cli(*db, "publish", "--live", "--post-id", str(post_id))
        self.assertEqual(live.returncode, 2)
        self.assertIn("REACHWEAVER_ALLOW_LIVE", live.stderr)

        state = self.clone / "state"
        with sqlite3.connect(state / "quickstart.sqlite") as con:
            statuses = [row[0] for row in con.execute("SELECT status FROM posts")]
        self.assertEqual(statuses, ["approved"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((state / "quickstart.sqlite").stat().st_mode), 0o600)
        export = json.loads((state / "quickstart-threads" / "manual-export.json").read_text(encoding="utf-8"))
        self.assertEqual(export["status"], "manual_export_not_published")
        self.assertTrue((state / "quickstart-drafts" / "MANUAL-REVIEW.txt").is_file())
        self.assertEqual(list(self.home.iterdir()), [], "onboarding wrote into HOME")
        self.assertNoNetwork()

    def test_every_export_alternative_runs_offline(self):
        audit = onboarding.blocker_audit()
        rows = [r for r in audit["rows"] if r["registry"] == "capability"]
        work = Path(tempfile.mkdtemp(dir=self.clone / "examples"))
        self.addCleanup(shutil.rmtree, work, True)

        manual = sorted(r["id"] for r in rows if r["kind"] == "manual_export")
        self.assertEqual(manual, sorted(MANUAL_PLATFORMS))
        variants = {
            "threads": {"platform": "threads", "text": "Release notes for ReachWeaver. #opensource"},
            "facebook": {"platform": "facebook", "text": "Release notes for our Page.",
                         "platform_options": {"target_type": "page"}},
            "instagram": {"platform": "instagram", "format": "image", "text": "Release card. #opensource"},
            "tiktok": {"platform": "tiktok", "format": "video", "text": "How the checks work #devtools"},
            "youtube": {"platform": "youtube", "format": "video", "text": "A short tour.",
                        "platform_options": {"title": "ReachWeaver in 60 seconds"}},
        }
        for platform in manual:
            with self.subTest(platform=platform):
                variant = work / f"{platform}.json"
                variant.write_text(json.dumps(variants[platform]), encoding="utf-8")
                out = work / f"{platform}-package"
                result = self.cli("manual-export", "--platform", platform, "--variant", str(variant),
                                  "--out", str(out))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                package = json.loads((out / "manual-export.json").read_text(encoding="utf-8"))
                self.assertEqual(package["status"], "manual_export_not_published")

        drafts = sorted(r["id"] for r in rows if r["kind"] == "draft_export")
        self.assertTrue(drafts)
        campaign = {
            "schema_version": 1, "campaign_id": "blocked-platform-drafts", "brand_id": "onboarding",
            "audience": "Developers checking the manual fallback", "objective": "qualified_visits",
            "evidence": [{"id": "source-repo", "url": "https://github.com/MSKazemi/reachweaver",
                          "claim": "ReachWeaver exports drafts for platforms without an adapter.",
                          "verification": "primary_source_checked",
                          "checked_at": "2026-10-08T12:00:00+00:00"}],
            "variants": [{"id": f"draft-{p}", "account_id": f"owner-{p}", "platform": p, "format": "text",
                          "angle": f"Manual {p} handoff", "evidence_ids": ["source-repo"],
                          "text": f"A reviewed {p} draft from ReachWeaver, posted by hand: "
                                  f"https://github.com/MSKazemi/reachweaver",
                          "cta": "Read the source"} for p in drafts],
        }
        brand = {"schema_version": 1, "brand_id": "onboarding", "display_name": "Onboarding",
                 "voice": "Plain and technical.", "approved_accounts": [f"owner-{p}" for p in drafts],
                 "prohibited_claims": ["Guaranteed virality"]}
        (work / "campaign.json").write_text(json.dumps(campaign), encoding="utf-8")
        (work / "brand.json").write_text(json.dumps(brand), encoding="utf-8")
        result = self.cli("campaign-export", "--file", str(work / "campaign.json"), "--brand",
                          str(work / "brand.json"), "--out", str(work / "drafts"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for platform in drafts:
            self.assertTrue((work / "drafts" / "drafts" / f"{platform}-draft-{platform}.txt").is_file(), platform)
        self.assertNoNetwork()


class BlockedPlatformAlternatives(unittest.TestCase):
    def setUp(self):
        self.caps = load_capabilities()
        self.dist = load_distribution()
        self.audit = onboarding.blocker_audit(self.caps, self.dist)

    def test_registries_have_no_uncovered_blocker(self):
        self.assertEqual(self.audit["errors"], [])
        self.assertIsNone(onboarding.SEND_PATH)

    def test_every_blocked_capability_platform_has_one_alternative(self):
        by_id = {r["id"]: r for r in self.audit["rows"] if r["registry"] == "capability"}
        for entry in self.caps["platforms"]:
            live = entry["status"] == "supported" and entry["reachweaver_adapter"] == "live_text"
            with self.subTest(platform=entry["id"]):
                if live:
                    self.assertNotIn(entry["id"], by_id)
                    continue
                row = by_id[entry["id"]]
                self.assertIn(row["kind"], ("manual_export", "draft_export", "human_only"))
                self.assertTrue(row["note"].strip())
                if row["kind"] in ("manual_export", "draft_export"):
                    self.assertTrue(row["command"].startswith("reachweaver "))
                else:
                    self.assertIsNone(row["command"])

    def test_every_distribution_channel_without_adapter_is_covered(self):
        covered = {r["id"]: r for r in self.audit["rows"] if r["registry"] == "distribution"}
        for entry in self.dist["platforms"]:
            with self.subTest(channel=entry["id"]):
                if entry["reachweaver_delivery"] == "mock_tested_text":
                    self.assertNotIn(entry["id"], covered)
                    continue
                kind = covered[entry["id"]]["kind"]
                self.assertIn(kind, onboarding.KINDS)
                if entry["human_only"]:
                    self.assertEqual(kind, "human_only")
                if entry["reachweaver_delivery"] == "not_applicable":
                    self.assertEqual(kind, "unsupported")

    def test_human_only_venues_never_get_generated_drafts(self):
        rows = {(r["registry"], r["id"]): r for r in self.audit["rows"]}
        for key in (("capability", "reddit"), ("distribution", "reddit"), ("distribution", "hacker_news")):
            with self.subTest(venue=key):
                self.assertEqual(rows[key]["kind"], "human_only")
                self.assertIsNone(rows[key]["command"])

    def test_new_blocked_platform_without_alternative_fails(self):
        caps = copy.deepcopy(self.caps)
        newcomer = copy.deepcopy(next(p for p in caps["platforms"] if p["id"] == "x"))
        newcomer.update(id="newnet", label="NewNet")
        caps["platforms"].append(newcomer)
        errors = onboarding.blocker_audit(caps, self.dist)["errors"]
        self.assertTrue(any(e.startswith("newnet:") and "no manual alternative" in e for e in errors), errors)

    def test_misclassified_alternatives_fail(self):
        cases = {
            "reddit": (("draft_export", "would generate Reddit text"), "human-only"),
            "x": (("manual_export", "not covered"), "manual-export does not cover"),
            "threads": (("draft_export", "weaker path"), "use kind manual_export"),
            "pinterest": (("teleport", "bogus"), "unknown alternative kind"),
        }
        for platform, (replacement, fragment) in cases.items():
            with self.subTest(platform=platform):
                alts = dict(onboarding.CAPABILITY_ALTERNATIVES, **{platform: replacement})
                errors = onboarding.blocker_audit(self.caps, self.dist, alts)["errors"]
                self.assertTrue(any(fragment in e for e in errors), errors)
        alts = dict(onboarding.CAPABILITY_ALTERNATIVES, bluesky=("draft_export", "stale"))
        errors = onboarding.blocker_audit(self.caps, self.dist, alts)["errors"]
        self.assertTrue(any("bluesky" in e and "stale" in e for e in errors), errors)

    def test_hacker_news_marker_cannot_be_dropped(self):
        dist = copy.deepcopy(self.dist)
        hn = next(p for p in dist["platforms"] if p["id"] == "hacker_news")
        hn["human_only"] = False
        errors = onboarding.blocker_audit(self.caps, dist)["errors"]
        self.assertTrue(any(e.startswith("hacker_news:") for e in errors), errors)

    def test_troubleshooting_table_matches_registries(self):
        gate = load_release_gate()
        text = (ROOT / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
        self.assertEqual(gate.extract_table(text), onboarding.render_markdown(self.audit),
                         "regenerate with: python3 scripts/release_gate.py --print-blocker-table")


class ReleaseGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load_release_gate()

    def make_copy(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return copy_repository(ROOT, Path(tmp.name) / "repo")

    def test_repository_passes_every_offline_check(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_gate.py"), "--skip-tests", "--json"],
                                cwd=ROOT, capture_output=True, text=True, timeout=300, check=False,
                                env={k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")})
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)  # incomplete, never 0
        self.assertEqual(report["overall"], "incomplete")
        self.assertEqual(report["failed"], [])
        statuses = {c["check"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses, {"version": "pass", "changelog": "pass", "docs": "pass", "bundle": "pass",
                                    "blockers": "pass", "readiness": "pass", "tests": "skipped"})

    def test_version_mismatch_and_wrong_tag_fail(self):
        repo = self.make_copy()
        self.assertEqual(self.gate.check_version(repo)["status"], "pass")
        version = self.gate.project_version(repo)
        bad = self.gate.check_version(repo, tag="v0.0.0")
        self.assertTrue(any("does not match" in e for e in bad["errors"]))
        self.assertEqual(self.gate.check_version(repo, tag=f"v{version}")["status"], "pass")
        init = repo / "reachweaver" / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8").replace(version, "9.9.9"), encoding="utf-8")
        mismatch = self.gate.check_version(repo)
        self.assertEqual(mismatch["status"], "fail")
        self.assertTrue(any("disagree" in e for e in mismatch["errors"]))

    def test_changelog_needs_a_section_and_a_date_to_tag(self):
        repo = self.make_copy()
        version = self.gate.project_version(repo)
        self.assertEqual(self.gate.check_changelog(repo, version)["status"], "pass")
        self.assertEqual(self.gate.check_changelog(repo, "99.0.0")["status"], "fail")
        changelog = repo / "CHANGELOG.md"
        text = changelog.read_text(encoding="utf-8")
        heading = next(line for line in text.splitlines() if line.startswith(f"## [{version}]"))
        if not re.search(r"\] - \d{4}-\d{2}-\d{2}", heading):
            self.assertEqual(self.gate.check_changelog(repo, version, tagging=True)["status"], "fail")
        changelog.write_text(text.replace(heading, f"## [{version}] - 2026-10-08"), encoding="utf-8")
        self.assertEqual(self.gate.check_changelog(repo, version, tagging=True)["status"], "pass")
        changelog.write_text(f"# Changelog\n\n## [{version}] - 2026-10-08\n\n## [0.0.1] - 2026-01-01\n- x\n",
                             encoding="utf-8")
        self.assertTrue(any("empty" in e for e in self.gate.check_changelog(repo, version)["errors"]))
        changelog.unlink()
        self.assertEqual(self.gate.check_changelog(repo, version)["status"], "fail")

    def test_docs_check_catches_missing_docs_links_and_commands(self):
        repo = self.make_copy()
        self.assertEqual(self.gate.check_docs(repo)["status"], "pass", self.gate.check_docs(repo)["errors"])
        (repo / "docs" / "SECURITY-CHECKLIST.md").unlink()
        (repo / "docs" / "QUICKSTART.md").write_text("# Quickstart\n\nSee [gone](NOPE.md).\n", encoding="utf-8")
        cli = repo / "reachweaver" / "cli.py"
        cli.write_text(cli.read_text(encoding="utf-8") + '\n# sub.add_parser("undocumented-xyz")\n',
                       encoding="utf-8")
        errors = self.gate.check_docs(repo)["errors"]
        self.assertTrue(any("SECURITY-CHECKLIST.md" in e for e in errors), errors)
        self.assertTrue(any("broken link NOPE.md" in e for e in errors), errors)
        self.assertTrue(any("undocumented-xyz" in e for e in errors), errors)

    def test_bundle_drift_fails(self):
        repo = self.make_copy()
        self.assertEqual(self.gate.check_bundle(repo)["status"], "pass")
        bundled = repo / self.gate.BUNDLE / "onboarding.py"
        bundled.write_text(bundled.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        self.assertEqual(self.gate.check_bundle(repo)["status"], "fail")

    def test_failing_test_suite_fails_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = Path(tmp) / "tests"
            suite.mkdir()
            (suite / "test_fail.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_no(self):\n        self.fail('boom')\n",
                encoding="utf-8")
            failed = self.gate.run_tests(Path(tmp))
            self.assertEqual(failed["status"], "fail")
            self.assertEqual(failed["details"]["ran"], 1)
            (suite / "test_fail.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n",
                encoding="utf-8")
            passed = self.gate.run_tests(Path(tmp))
            self.assertEqual(passed["status"], "pass", passed)
            (suite / "test_fail.py").unlink()
            empty = self.gate.run_tests(Path(tmp))
            self.assertEqual(empty["status"], "fail")

    def test_exit_codes(self):
        self.assertEqual(self.gate.exit_code({"overall": "pass"}), 0)
        self.assertEqual(self.gate.exit_code({"overall": "incomplete"}), 3)
        self.assertEqual(self.gate.exit_code({"overall": "fail"}), 1)


class ReleaseReadinessScorecard(unittest.TestCase):
    """docs/release-readiness.json: schema, evidence paths, registry coverage and no live claims."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_release_gate()
        cls.data = json.loads((ROOT / cls.gate.READINESS).read_text(encoding="utf-8"))
        cls.capabilities, cls.distribution, cls.alternatives = cls.gate.load_registries(ROOT)

    def errors(self, data: dict, root: Path = ROOT) -> list[str]:
        return self.gate.validate_readiness(data, root, capabilities=self.capabilities,
                                            distribution=self.distribution, alternatives=self.alternatives)

    def mutated(self, area_id: str, **changes) -> dict:
        data = copy.deepcopy(self.data)
        area = next(a for a in data["areas"] if a["id"] == area_id)
        for key, value in changes.items():
            if value is None and key != "issue":
                area.pop(key, None)
            else:
                area[key] = value
        return data

    def run_cli(self, *args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "release_gate.py"), "--root", str(root),
                               *args], capture_output=True, text=True, timeout=120, check=False,
                              env={k: v for k, v in os.environ.items() if not k.startswith("REACHWEAVER_")})

    def test_checked_in_scorecard_is_valid(self):
        self.assertEqual(self.errors(self.data), [])
        self.assertEqual(self.gate.check_readiness(ROOT)["status"], "pass")

    def test_no_area_claims_a_live_receipt(self):
        # No owner account has been connected and no live send has run, so nothing may claim one.
        statuses = {a["id"]: a["status"] for a in self.data["areas"]}
        self.assertNotIn("live_receipt_verified", statuses.values(), statuses)
        self.assertFalse([a["id"] for a in self.data["areas"] if "receipt" in a])
        report = self.gate.scorecard(ROOT)
        self.assertEqual(report["live_receipts"], 0)
        self.assertEqual(report["summary"]["live_receipt_verified"], 0)

    def test_every_status_comes_from_the_allowed_set_and_issue_list(self):
        for area in self.data["areas"]:
            self.assertIn(area["status"], self.gate.READINESS_STATUSES, area["id"])
        for name in ("bluesky", "mastodon", "telegram", "linkedin", "postiz", "threads", "instagram", "facebook",
                     "youtube", "tiktok", "x", "reddit", "hacker_news", *self.gate.REQUIRED_AREAS):
            self.assertIn(name, {a["id"] for a in self.data["areas"]})

    def test_statuses_follow_the_registries(self):
        by_id = {a["id"]: a for a in self.data["areas"]}
        for cap_id, entry in self.capabilities.items():
            row = next(a for a in self.data["areas"] if a.get("capability_id") == cap_id)
            if entry["reachweaver_adapter"] == "live_text":
                self.assertEqual(row["status"], "offline_mock_tested", cap_id)
                self.assertTrue(any(e.startswith("tests/") for e in row["evidence"]), cap_id)
            else:
                self.assertIn(row["status"], ("manual_export_only", "blocked"), cap_id)
        self.assertEqual(by_id["reddit"]["status"], "blocked")
        self.assertEqual(by_id["hacker_news"]["status"], "blocked")
        for manual in MANUAL_PLATFORMS:
            self.assertEqual(by_id[manual]["status"], "manual_export_only", manual)

    def test_unknown_status_fails(self):
        errors = self.errors(self.mutated("bluesky", status="almost_live"))
        self.assertTrue(any("unknown status 'almost_live'" in e for e in errors), errors)

    def test_missing_or_unsafe_evidence_fails(self):
        cases = {
            "docs/NO-SUCH-FILE.md": "does not exist",
            "tests/test_linkedin.py::NoSuchTests": "defines no class or function NoSuchTests",
            "../outside.md": "without '..'",
            "/etc/passwd": "without '..'",
            "docs/PLATFORMS.md::Heading": "only allowed for .py files",
        }
        for item, message in cases.items():
            with self.subTest(item=item):
                errors = self.errors(self.mutated("linkedin", evidence=["reachweaver/linkedin.py", item]))
                self.assertTrue(any(message in e for e in errors), errors)
        errors = self.errors(self.mutated("linkedin", evidence=[]))
        self.assertTrue(any("evidence must be a non-empty list" in e for e in errors), errors)

    def test_capability_platform_missing_from_scorecard_fails(self):
        data = copy.deepcopy(self.data)
        data["areas"] = [a for a in data["areas"] if a["id"] != "pinterest"]
        errors = self.errors(data)
        self.assertIn("capability-registry platform 'pinterest' is missing from the scorecard", errors)
        data["areas"] = [a for a in data["areas"] if a["id"] != "scheduler"]
        self.assertIn("required area 'scheduler' is missing from the scorecard", self.errors(data))

    def test_new_registry_platform_without_row_fails(self):
        capabilities = dict(self.capabilities, mastodon_fork={"id": "mastodon_fork", "status": "blocked",
                                                              "reachweaver_adapter": "none"})
        errors = self.gate.validate_readiness(self.data, ROOT, capabilities=capabilities,
                                              distribution=self.distribution, alternatives=self.alternatives)
        self.assertIn("capability-registry platform 'mastodon_fork' is missing from the scorecard", errors)

    def test_status_inconsistent_with_registry_fails(self):
        cases = {
            ("threads", "offline_mock_tested"): "has no live adapter",
            ("bluesky", "manual_export_only"): "manual_export_only needs",
            ("x", "blocked"): "neither blocks it nor marks it human-only",
            ("hacker_news", "manual_export_only"): "human-only in the distribution registry",
            ("reddit", "manual_export_only"): "manual_export_only needs",
            ("bluesky", "not_started"): "lists a live adapter",
        }
        for (area_id, status), message in cases.items():
            with self.subTest(area=area_id, status=status):
                errors = self.errors(self.mutated(area_id, status=status))
                self.assertTrue(any(message in e for e in errors), errors)
        errors = self.errors(self.mutated("telegram", capability_id="telegrm"))
        self.assertTrue(any("'telegrm' is not in reachweaver/platform_registry.json" in e for e in errors), errors)

    def test_human_only_rows_cannot_drop_their_distribution_id(self):
        # Review finding (2026-10-09): the human-only => blocked rule ran only when distribution_id was
        # present, so deleting it from the hacker_news row let the row claim any status.
        for area_id in ("hacker_news", "reddit"):
            with self.subTest(area=area_id, change="drop distribution_id"):
                errors = self.errors(self.mutated(area_id, distribution_id=None))
                self.assertTrue(any(f"must carry distribution_id {area_id!r}" in e for e in errors), errors)
            with self.subTest(area=area_id, change="drop distribution_id and unblock"):
                errors = self.errors(self.mutated(area_id, distribution_id=None, kind="area",
                                                  status="offline_mock_tested"))
                self.assertTrue(any("human-only in the distribution registry" in e for e in errors), errors)
            with self.subTest(area=area_id, change="point distribution_id elsewhere"):
                errors = self.errors(self.mutated(area_id, distribution_id="bluesky"))
                self.assertTrue(any(f"must carry distribution_id {area_id!r}" in e for e in errors), errors)
        # A row whose capability_id names a human-only distribution entry is held to the same rule.
        data = self.mutated("reddit", id="reddit_community", distribution_id=None)
        errors = self.errors(data)
        self.assertTrue(any("must carry distribution_id 'reddit'" in e for e in errors), errors)

    def test_schema_violations_fail(self):
        data = copy.deepcopy(self.data)
        data["areas"].append(copy.deepcopy(data["areas"][0]))
        self.assertIn("duplicate area id 'bluesky'", self.errors(data))
        errors = self.errors(self.mutated("media", owner_notes="x"))
        self.assertTrue(any("unknown field 'owner_notes'" in e for e in errors), errors)
        errors = self.errors(self.mutated("media", issue=True))
        self.assertTrue(any("issue must be a positive integer or null" in e for e in errors), errors)
        errors = self.errors(self.mutated("media", blockers=[]))
        self.assertTrue(any("needs at least one blocker" in e for e in errors), errors)
        errors = self.errors(self.mutated("media", blockers=[{"kind": "someday", "detail": "x"}]))
        self.assertTrue(any("each blocker is" in e for e in errors), errors)
        errors = self.errors(self.mutated("media", receipt="docs/PLATFORMS.md"))
        self.assertTrue(any("receipt is only allowed with status live_receipt_verified" in e for e in errors), errors)
        self.assertEqual(self.errors({"schema_version": 2}), ["schema_version must be 1"])

    def test_live_receipt_requires_a_first_party_receipt_file(self):
        errors = self.errors(self.mutated("bluesky", status="live_receipt_verified", blockers=[]))
        self.assertTrue(any("needs a first-party receipt file" in e for e in errors), errors)
        errors = self.errors(self.mutated("bluesky", status="live_receipt_verified",
                                          receipt="docs/receipts/bluesky.json"))
        self.assertTrue(any("receipt docs/receipts/bluesky.json does not exist" in e for e in errors), errors)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for area in self.data["areas"]:
                for item in area["evidence"]:
                    target = root / item.split("::", 1)[0]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = ROOT / item.split("::", 1)[0]
                    if not target.exists() and source.exists():  # private-only docs are absent in the public tree
                        shutil.copy2(source, target)
            receipt = root / "docs" / "receipts" / "bluesky.json"
            receipt.parent.mkdir(parents=True)
            good = {"area": "bluesky", "remote_url": "https://bsky.app/profile/example.invalid/post/abc",
                    "observed_at": "2026-10-09T10:00:00+00:00", "source": "native app", "first_party": True}
            receipt.write_text(json.dumps(good), encoding="utf-8")
            claim = self.mutated("bluesky", status="live_receipt_verified", receipt="docs/receipts/bluesky.json")
            self.assertEqual(self.errors(claim, root), [])
            for field, value, message in (("first_party", False, "first_party: true"),
                                          ("remote_url", "http://x.example/p", "https URL"),
                                          ("observed_at", "2026-10-09T10:00:00", "time zone"),
                                          ("area", "mastodon", "not 'bluesky'")):
                with self.subTest(field=field):
                    receipt.write_text(json.dumps(dict(good, **{field: value})), encoding="utf-8")
                    errors = self.errors(claim, root)
                    self.assertTrue(any(message in e for e in errors), errors)
            receipt.write_text("not json", encoding="utf-8")
            self.assertTrue(any("not valid JSON" in e for e in self.errors(claim, root)))

    def test_cli_scorecard_table_and_json(self):
        table = self.run_cli("--scorecard")
        self.assertEqual(table.returncode, 0, table.stdout + table.stderr)
        self.assertIn("RELEASE READINESS PASS", table.stdout)
        self.assertIn("Live receipts verified: 0. No area claims a live receipt.", table.stdout)
        for area in self.data["areas"]:
            self.assertIn(area["label"], table.stdout)
        machine = self.run_cli("--scorecard", "--json")
        self.assertEqual(machine.returncode, 0, machine.stderr)
        report = json.loads(machine.stdout)
        self.assertEqual(report["overall"], "pass")
        self.assertEqual(report["errors"], [])
        self.assertEqual([a["id"] for a in report["areas"]], [a["id"] for a in self.data["areas"]])
        refused = self.run_cli("--scorecard", "--tag", "v0.2.0")
        self.assertEqual(refused.returncode, 2)

    def test_broken_scorecard_fails_the_cli_and_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = copy_repository(ROOT, Path(tmp) / "repo")
            path = repo / self.gate.READINESS
            path.write_text(json.dumps(self.mutated("sources", evidence=["reachweaver/gone.py"])), encoding="utf-8")
            result = self.run_cli("--scorecard", root=repo)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("RELEASE READINESS FAIL", result.stdout)
            self.assertIn("reachweaver/gone.py does not exist", result.stdout)
            check = self.gate.check_readiness(repo)
            self.assertEqual(check["status"], "fail")
            path.write_text("{", encoding="utf-8")
            self.assertTrue(any("not valid JSON" in e for e in self.gate.check_readiness(repo)["errors"]))
            path.unlink()
            self.assertIn("docs/release-readiness.json is missing", self.gate.check_readiness(repo)["errors"])


if __name__ == "__main__":
    unittest.main()
