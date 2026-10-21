"""Offline contracts for the open social web registry and the read-only social-web-plan command."""
from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reachweaver import social_web
from reachweaver.cli import main
from reachweaver.distribution import load_registry as load_distribution
from reachweaver.policy import ValidationError
from reachweaver.social_web import (
    bridge_duplicate_risk, build_plan, load_registry, load_setup, render_text, validate_registry,
)

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/reachweaver"
DIST_IDS = {p["id"] for p in load_distribution()["platforms"]}
PLAN_KEYS = {"schema", "status", "registry_reviewed_on", "accounts_file", "presences", "by_protocol", "by_network",
             "canonical_home", "duplicate_reach_warnings", "identity_checklist", "gaps", "evidence_note",
             "network_requests", "account_access", "queue_touched", "posts_created", "publishing_approval_granted"}
PRESENCE_KEYS = {"id", "platform", "label", "source", "handle", "protocol", "federation", "federation_note",
                 "bridged_via", "fediverse_sharing", "reaches", "sources"}


def acct(platform, handle=None):
    return {"platform": platform, "env_prefix": "REACHWEAVER_X", **({"handle": handle} if handle else {})}


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_registry()

    def test_bundled_registry_is_valid_and_keyed_by_distribution_ids(self):
        self.assertEqual(validate_registry(self.data, DIST_IDS), [])
        ids = {p["id"] for p in self.data["platforms"]}
        self.assertLessEqual(ids, DIST_IDS)
        for expected in ("mastodon", "bluesky", "threads", "ghost", "flipboard", "pixelfed", "lemmy", "misskey",
                         "peertube", "nostr", "farcaster", "matrix", "diaspora", "minds"):
            self.assertIn(expected, ids)

    def test_protocol_facts_match_the_sources(self):
        by_id = {p["id"]: p for p in self.data["platforms"]}
        self.assertEqual(by_id["bluesky"]["protocol"], "atproto")
        self.assertEqual(by_id["threads"]["federation"], "opt_in")
        self.assertEqual(by_id["flipboard"]["federation"], "rollout")
        self.assertEqual(by_id["minds"]["federation"], "unverified")
        for key in ("mastodon", "pixelfed", "lemmy", "misskey", "peertube", "ghost"):
            self.assertEqual(by_id[key]["protocol"], "activitypub", key)

    def test_every_source_is_https_quoted_and_not_later_than_the_snapshot(self):
        def walk(node):
            if isinstance(node, dict):
                if "quote" in node:
                    yield node
                for value in node.values():
                    yield from walk(value)
            elif isinstance(node, list):
                for value in node:
                    yield from walk(value)
        sources = list(walk(self.data))
        self.assertGreaterEqual(len(sources), 30)
        for source in sources:
            self.assertTrue(source["url"].startswith("https://"), source)
            self.assertLessEqual(source["checked_on"], self.data["reviewed_on"])

    def test_bridgy_fed_is_opt_in_and_mirrors_rather_than_cross_posts(self):
        bridge = next(b for b in self.data["bridges"] if b["id"] == "bridgy_fed")
        self.assertIs(bridge["opt_in"], True)
        self.assertEqual(bridge["mode"], "mirror_existing_account")
        self.assertEqual(set(bridge["connects"]), {"web", "fediverse", "atmosphere"})
        quotes = " ".join(s["quote"] for s in bridge["sources"])
        self.assertIn("Bridgy Fed is opt in.", quotes)
        self.assertIn("doesn't cross-post", quotes)

    def test_validator_rejects_unsafe_or_unsourced_changes(self):
        cases = {
            "threads federation": lambda d: next(p for p in d["platforms"] if p["id"] == "threads").update(
                federation="native"),
            "bridge not opt-in": lambda d: d["bridges"][0].update(opt_in=False),
            "unknown platform": lambda d: d["platforms"].append({**d["platforms"][0], "id": "not-a-network"}),
            "duplicate platform": lambda d: d["platforms"].append(deepcopy(d["platforms"][0])),
            "unknown protocol": lambda d: d["platforms"][0].update(protocol="carrier_pigeon"),
            "missing sources": lambda d: d["platforms"][0].update(sources=[]),
            "http source": lambda d: d["platforms"][0]["sources"][0].update(url="http://example.org/"),
            "missing quote": lambda d: d["platforms"][0]["sources"][0].update(quote=""),
            "future check": lambda d: d["platforms"][0]["sources"][0].update(checked_on="2099-01-01"),
            "unknown identity check": lambda d: d["platforms"][0].update(identity_checks=["nope"]),
            "home policy": lambda d: d["canonical_home_policy"].update(status="proven"),
            "decision type": lambda d: d.update(decision_type="forecast"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                data = deepcopy(self.data)
                mutate(data)
                self.assertTrue(validate_registry(data, DIST_IDS), name)
        self.assertEqual(validate_registry([]), ["schema_version must be 1"])

    def test_registry_ships_in_the_package_and_the_skill_bundle(self):
        self.assertIn('"social_web_registry.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn('"social_web_registry.json"', (ROOT / "scripts/sync_skill.py").read_text(encoding="utf-8"))
        source = (ROOT / "reachweaver/social_web_registry.json").read_bytes()
        self.assertEqual((SKILL / "scripts/reachweaver/social_web_registry.json").read_bytes(), source)
        self.assertEqual((ROOT / "plugin/skills/reachweaver/scripts/reachweaver/social_web_registry.json").read_bytes(),
                         source)
        self.assertEqual((SKILL / "assets/social-web-setup.example.json").read_bytes(),
                         (ROOT / "examples/social-web-setup.example.json").read_bytes())

    def test_module_has_no_send_queue_network_or_credential_dependency(self):
        import ast
        tree = ast.parse((ROOT / "reachweaver/social_web.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0] or "." + (node.module or ""))
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
        for forbidden in ("storage", "publishers", "sendgate", "repeat_guard", "accounts", "credential", "socket",
                          "urllib.request", "http", "postiz", "netconnect"):
            self.assertNotIn(forbidden, imported, forbidden)


class PlanTests(unittest.TestCase):
    def test_bridged_bluesky_and_native_mastodon_warns_about_duplicate_reach(self):
        plan = build_plan({"my-bluesky": acct("bluesky", "me.bsky.social"), "my-mastodon": acct("mastodon")},
                          bridged=["my-bluesky"])
        warnings = plan["duplicate_reach_warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["severity"], "warning")
        self.assertEqual(warnings[0]["network"], "fediverse")
        self.assertEqual(warnings[0]["accounts"], ["my-bluesky", "my-mastodon"])
        self.assertIn("Bridgy Fed", warnings[0]["message"])
        self.assertIn("twice", warnings[0]["message"])
        self.assertTrue(warnings[0]["evidence"].startswith("hypothesis"))
        self.assertEqual(warnings[0]["source"], "https://fed.brid.gy/docs")
        self.assertEqual(sorted(plan["by_network"]["fediverse"]), ["my-bluesky", "my-mastodon"])

    def test_threads_reaches_the_fediverse_only_with_sharing_on(self):
        accounts = {"my-threads": acct("threads"), "my-mastodon": acct("mastodon")}
        off = build_plan(accounts)
        self.assertEqual(off["duplicate_reach_warnings"], [])
        self.assertIn("fediverse_sharing_off:my-threads", [g["id"] for g in off["gaps"]])
        on = build_plan(accounts, fediverse_sharing=["my-threads"])
        self.assertEqual([w["severity"] for w in on["duplicate_reach_warnings"]], ["warning"])
        self.assertIn("fediverse sharing on", on["duplicate_reach_warnings"][0]["message"])

    def test_two_native_accounts_on_one_network_is_info_only(self):
        plan = build_plan({"my-mastodon": acct("mastodon")}, presence=["pixelfed"])
        self.assertEqual([w["severity"] for w in plan["duplicate_reach_warnings"]], ["info"])

    def test_helper_is_pure_and_callable_from_other_planning_code(self):
        presences = [{"id": "a", "platform": "bluesky", "bridgy_fed": True}, {"id": "b", "platform": "mastodon"}]
        before = deepcopy(presences)
        result = bridge_duplicate_risk(presences)
        self.assertEqual(presences, before)
        self.assertEqual(result[0]["accounts"], ["a", "b"])
        self.assertEqual(bridge_duplicate_risk([{"id": "a", "platform": "bluesky"}]), [])

    def test_canonical_home_prefers_an_owned_activitypub_site_and_is_a_hypothesis(self):
        plan = build_plan({"my-mastodon": acct("mastodon")}, site_url="https://example.org", site_kind="ghost")
        home = plan["canonical_home"]
        self.assertEqual(home["status"], "editorial_hypothesis")
        self.assertEqual(home["recommendation"], "owned_activitypub_site")
        self.assertEqual(build_plan({}, site_url="https://example.org")["canonical_home"]["recommendation"], "owned_site")
        self.assertEqual(build_plan({}, presence=["substack"])["canonical_home"]["recommendation"], "newsletter")
        self.assertEqual(build_plan({"m": acct("mastodon")})["canonical_home"]["recommendation"],
                         "open_protocol_account")

    def test_bridged_site_reaches_both_networks(self):
        plan = build_plan({"my-mastodon": acct("mastodon")}, site_url="https://example.org",
                          site_kind="bridgy_fed_web")
        site = next(p for p in plan["presences"] if p["id"] == "site")
        self.assertEqual({h["network"] for h in site["reaches"]}, {"web", "fediverse", "atmosphere"})
        self.assertTrue(any(w["severity"] == "warning" and w["message"].startswith("Your site")
                            for w in plan["duplicate_reach_warnings"]))

    def test_identity_checklist(self):
        plan = build_plan({"b1": acct("bluesky", "me.bsky.social"), "b2": acct("bluesky", "example.org"),
                           "m": acct("mastodon")}, site_url="https://example.org",
                          setup={"identity": {"mastodon_link_verification": True}})
        status = {(i["id"], tuple(i["applies_to"])): i["status"] for i in plan["identity_checklist"]}
        self.assertEqual(status[("bluesky_domain_handle", ("b1",))], "todo")
        self.assertEqual(status[("bluesky_domain_handle", ("b2",))], "looks_done_not_verified")
        self.assertEqual(status[("mastodon_link_verification", ("m",))], "reported_done")
        self.assertEqual(status[("fediverse_creator", ("m",))], "todo")
        for item in plan["identity_checklist"]:
            self.assertTrue(item["source"].startswith("https://"))

    def test_gaps_when_nothing_open_is_recorded(self):
        plan = build_plan({"li": acct("linkedin")}, accounts_file="loaded")
        gaps = [g["id"] for g in plan["gaps"]]
        self.assertIn("no_open_protocol", gaps)
        self.assertIn("no_owned_home", gaps)
        self.assertIn("no_protocol_recorded", gaps)
        row = plan["presences"][0]
        self.assertEqual(row["protocol"], "none_recorded")
        self.assertIn("not a verified absence", row["federation_note"])
        self.assertIn("no_atmosphere", [g["id"] for g in build_plan({"m": acct("mastodon")})["gaps"]])
        self.assertIn("no_fediverse", [g["id"] for g in build_plan({"b": acct("bluesky", "x.bsky.social")})["gaps"]])

    def test_account_platform_ids_are_mapped_to_registry_ids(self):
        plan = build_plan({"fb": acct("facebook"), "d": acct("devto")})
        self.assertEqual([p["platform"] for p in plan["presences"]], ["facebook_pages", "dev"])

    def test_refusals(self):
        with self.assertRaises(ValidationError):
            build_plan({"t": acct("telegram")}, bridged=["t"])  # Bridgy Fed does not cover Telegram
        with self.assertRaises(ValidationError):
            build_plan({}, bridged=["nobody"])
        with self.assertRaises(ValidationError):
            build_plan({}, presence=["not-a-network"])
        with self.assertRaises(ValidationError):
            build_plan({}, site_url="http://example.org")
        with self.assertRaises(ValidationError):
            build_plan({}, site_url="https://user:pw@example.org")
        with self.assertRaises(ValidationError):
            build_plan({"m": acct("mastodon")}, fediverse_sharing=["m"])
        with self.assertRaises(ValidationError):
            build_plan({}, site_kind="ghost")

    def test_plan_schema_is_stable_and_states_it_did_nothing(self):
        plan = build_plan({"b": acct("bluesky", "x.bsky.social")}, site_url="https://example.org")
        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual(plan["schema"], "reachweaver.social-web-plan.v1")
        for row in plan["presences"]:
            self.assertEqual(set(row), PRESENCE_KEYS)
        self.assertIs(plan["network_requests"], False)
        self.assertIs(plan["queue_touched"], False)
        self.assertIs(plan["publishing_approval_granted"], False)
        self.assertEqual(plan["posts_created"], 0)
        self.assertIn("No network", render_text(plan))


class SetupFileTests(unittest.TestCase):
    def write(self, folder, data):
        path = Path(folder) / "setup.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_example_setup_loads(self):
        setup = load_setup(ROOT / "examples/social-web-setup.example.json")
        self.assertEqual(setup["site"]["kind"], "ghost")
        self.assertEqual(setup["bridged"], ["my-bluesky"])

    def test_bad_setup_files_are_refused(self):
        bad = [
            {"schema": "other"},
            {"schema": social_web.SETUP_SCHEMA, "tokens": "x"},
            {"schema": social_web.SETUP_SCHEMA, "bridges": [{"bridge": "other", "account": "a"}]},
            {"schema": social_web.SETUP_SCHEMA, "identity": {"fediverse_creator": "yes"}},
            {"schema": social_web.SETUP_SCHEMA, "presence": ["pixelfed"]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            for data in bad:
                with self.subTest(data=data), self.assertRaises(ValidationError):
                    load_setup(self.write(folder, data))


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_json_with_example_accounts_and_setup(self):
        code, out, err = self.run_cli("--accounts", str(ROOT / "config/accounts.example.json"), "social-web-plan",
                                      "--setup", str(ROOT / "examples/social-web-setup.example.json"), "--json")
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(set(plan), PLAN_KEYS)
        self.assertEqual(plan["accounts_file"], "loaded")
        self.assertEqual(plan["canonical_home"]["recommendation"], "owned_activitypub_site")
        self.assertTrue(any(w["severity"] == "warning" for w in plan["duplicate_reach_warnings"]))

    def test_missing_accounts_file_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, err = self.run_cli("social-web-plan", "--accounts", str(Path(folder) / "none.json"), "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["accounts_file"], "missing")

    def test_bad_flag_value_exits_2(self):
        code, _, err = self.run_cli("social-web-plan", "--accounts", str(ROOT / "config/accounts.example.json"),
                                    "--bridged", "my-telegram-channel")
        self.assertEqual(code, 2)
        self.assertIn("Bridgy Fed", err)

    def test_subprocess_creates_no_state_and_opens_no_network(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_no_network import NetworkGuard
        with NetworkGuard() as guard:
            code, out, _ = self.run_cli("--accounts", str(ROOT / "config/accounts.example.json"), "social-web-plan",
                                        "--bridged", "my-bluesky", "--site", "https://example.org")
        self.assertEqual(code, 0)
        self.assertEqual(guard.attempts, [])
        self.assertIn("Bridgy Fed", out)
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "should-not-exist.sqlite"
            result = subprocess.run([sys.executable, "-m", "reachweaver.cli", "--db", str(db), "social-web-plan",
                                     "--presence", "mastodon", "--json"], cwd=root,
                                    env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True,
                                    check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(db.exists())
            self.assertEqual(os.listdir(root), [])


if __name__ == "__main__":
    unittest.main()
