"""``ai-label-check`` and ``ai_disclosure_registry.json``: sourced AI-content label advice (offline, advisory).

Every fixture is synthetic. Nothing here contacts a platform, opens a network socket or writes the database.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from reachweaver import ai_labels
from reachweaver.catalog import COMMANDS
from reachweaver.cli import main, make_parser
from reachweaver.media import validate_media_manifest
from reachweaver.policy import ValidationError
from reachweaver.storage import add_post, connect

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ai_labels.load_registry()
TODAY = date(2026, 10, 21)
EXPECTED_PLATFORMS = {"youtube", "instagram", "facebook_pages", "threads", "tiktok", "linkedin", "x"}


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def media(kind: str = "video", realistic: bool | None = None, created_by: str | None = "tool",
          targets: list[str] | None = None, asset_id: str = "clip") -> dict:
    return {"id": asset_id, "kind": kind, "realistic_synthetic": realistic, "created_by": created_by,
            "targets": targets or []}


def one(platform: str, authorship: str | None, items: list[dict] | None = None, **kwargs) -> dict:
    report = ai_labels.assess([{"id": "d1", "platform": platform, "authorship": authorship}], items or [],
                              registry=REGISTRY, today=kwargs.pop("today", TODAY), **kwargs)
    return report["targets"][0]


class RegistryTests(unittest.TestCase):
    def test_shipped_registry_is_valid_and_complete(self) -> None:
        data = json.loads(ai_labels.REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(ai_labels.validate_registry(data), [])
        self.assertEqual(set(data["platforms"]), EXPECTED_PLATFORMS)
        self.assertEqual(data["checked_on"], "2026-10-21")
        for sid, src in data["sources"].items():
            self.assertEqual(src["checked_on"], "2026-10-21", sid)

    def test_platform_ids_match_the_distribution_registry(self) -> None:
        dist = json.loads((ROOT / "reachweaver/distribution_registry.json").read_text(encoding="utf-8"))
        ids = {p["id"] for p in dist["platforms"]}
        self.assertLessEqual(set(REGISTRY["platforms"]), ids)

    def test_x_is_unverified_and_asserts_nothing(self) -> None:
        x = REGISTRY["platforms"]["x"]
        self.assertEqual(x["verification"], "unverified")
        self.assertEqual({*x["realistic_media"].values(), x["non_realistic_media"], x["ai_text"]}, {"unknown"})
        self.assertIsNone(x["native_control"])
        self.assertEqual(REGISTRY["sources"]["x_policy_unfetched"]["quote"], "")

    def test_eu_entry_is_secondary_and_not_legal_advice(self) -> None:
        eu = REGISTRY["jurisdictions"]["eu_ai_act_art50"]
        self.assertEqual(eu["applies_from"], "2026-08-02")
        self.assertIn("not legal advice", eu["disclaimer"])
        for sid in (s for k, v in eu.items() if k.endswith("_source") for s in v):
            self.assertEqual(REGISTRY["sources"][sid]["kind"], "secondary", sid)
            self.assertTrue(REGISTRY["sources"][sid]["url"].startswith("https://artificialintelligenceact.eu/"))
        self.assertIn("2 August 2026", REGISTRY["sources"]["eu_art113_date"]["quote"])

    def test_api_fields_are_the_ones_the_repo_already_uses(self) -> None:
        self.assertEqual(REGISTRY["platforms"]["youtube"]["api_field"], "status.containsSyntheticMedia")
        self.assertEqual(REGISTRY["platforms"]["tiktok"]["api_field"], "post_info.is_aigc")
        self.assertIn("containsSyntheticMedia", (ROOT / "reachweaver/manual_export.py").read_text(encoding="utf-8"))
        self.assertIn("is_aigc", (ROOT / "reachweaver/tiktok.py").read_text(encoding="utf-8"))

    def test_validator_rejects_broken_entries(self) -> None:
        def broken(mutate) -> list[str]:
            data = copy.deepcopy(REGISTRY)
            mutate(data)
            return ai_labels.validate_registry(data)

        self.assertTrue(broken(lambda d: d["sources"]["youtube_help_disclosure"].update(quote="")))
        self.assertTrue(broken(lambda d: d["sources"]["youtube_help_disclosure"].update(url="http://x")))
        self.assertTrue(broken(lambda d: d["sources"]["youtube_help_disclosure"].update(checked_on="21.10.2026")))
        self.assertTrue(broken(lambda d: d["platforms"]["youtube"].update(realistic_media_source=["nope"])))
        self.assertTrue(broken(lambda d: d["platforms"]["x"].update(ai_text="not_required")))
        self.assertTrue(broken(lambda d: d["platforms"]["youtube"].update(ai_text="maybe")))
        self.assertTrue(broken(lambda d: d["platforms"]["youtube"].update(realistic_media={"image": "required"})))
        self.assertTrue(broken(lambda d: d["platforms"]["youtube"].update(
            realistic_media_source=["x_policy_unfetched"])))  # a decision needs a source that was read
        self.assertTrue(broken(lambda d: d["platforms"]["threads"].update(aliases=["facebook"])))  # alias clash
        self.assertTrue(broken(lambda d: d["sources"]["eu_art50_text"].update(kind="official")))
        self.assertTrue(broken(lambda d: d["jurisdictions"]["eu_ai_act_art50"].update(disclaimer="fine")))
        self.assertTrue(broken(lambda d: d.update(label="no disclaimer")))
        self.assertTrue(broken(lambda d: d["jurisdictions"]["china"].update(module="elsewhere.py")))
        self.assertTrue(broken(lambda d: d["sources"]["x_policy_unfetched"].update(quote="made up")))
        self.assertEqual(ai_labels.validate_registry([]), ["registry must be a JSON object"])

    def test_load_registry_refuses_an_invalid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
            with self.assertRaises(ValidationError):
                ai_labels.load_registry(path)


class AssessTests(unittest.TestCase):
    def test_realistic_video_needs_a_label_on_youtube_meta_tiktok(self) -> None:
        for platform in ("youtube", "instagram", "facebook", "threads", "tiktok"):
            target = one(platform, "human", [media("video", True)])
            self.assertEqual(target["decision"], "required", platform)
        self.assertEqual(one("youtube", "human", [media("video", True)])["what_to_tick"],
                         'YouTube Studio upload > Attributes > "AI use": Yes')

    def test_meta_realistic_image_is_recommended_linkedin_recommended_with_condition(self) -> None:
        self.assertEqual(one("instagram", "human", [media("image", True)])["decision"], "recommended")
        target = one("linkedin", "human", [media("video", True)])
        self.assertEqual(target["decision"], "recommended")
        self.assertIn("saying or doing something they did not", target["reasons"][0]["why"])

    def test_non_realistic_owner_and_unflagged_media(self) -> None:
        self.assertEqual(one("youtube", "human", [media("video", False)])["decision"], "not_required")
        self.assertEqual(one("tiktok", "human", [media("video", None, "owner")])["decision"], "not_required")
        for creator in ("tool", "third_party", None):
            target = one("tiktok", "human", [media("video", None, creator)])
            self.assertEqual(target["decision"], "unknown", creator)
            self.assertIn("realistic_synthetic", target["reasons"][0]["why"])

    def test_ai_text_alone_needs_no_platform_label(self) -> None:
        for platform in ("youtube", "instagram", "tiktok", "linkedin"):
            target = one(platform, "ai_generated")
            self.assertEqual(target["decision"], "not_required", platform)
            self.assertTrue(target["what_to_tick"].startswith("Nothing"))
        self.assertIn("production assistance", one("youtube", "ai_generated")["reasons"][0]["why"])

    def test_unknown_and_unverified_platforms_answer_unknown(self) -> None:
        self.assertEqual(one("x", "human")["decision"], "unknown")
        self.assertEqual(one("twitter", "human")["registry_key"], "x")
        target = one("bluesky", "human")
        self.assertEqual((target["decision"], target["family"]), ("unknown", "none"))

    def test_china_points_at_the_cac_rule(self) -> None:
        from reachweaver.china import LEGAL
        target = one("weibo", "ai_generated")
        self.assertEqual((target["family"], target["decision"]), ("china", "required"))
        self.assertEqual(target["reasons"][0]["sources"][0]["url"], LEGAL["ai_label"]["source"])
        self.assertEqual(one("xiaohongshu", "ai_assisted")["decision"], "unknown")
        self.assertIn("笔记含AI合成内容", one("xiaohongshu", "ai_generated")["what_to_tick"])
        self.assertEqual(one("weibo", "human")["decision"], "not_required")
        self.assertEqual(one("douyin", "human", [media("video", None, "tool")])["decision"], "required")

    def test_media_targets_limit_where_an_asset_counts(self) -> None:
        clip = media("video", True, targets=["youtube_shorts"])
        self.assertEqual(one("youtube", "human", [clip])["decision"], "required")
        self.assertEqual(one("tiktok", "human", [clip])["decision"], "not_required")
        self.assertEqual(one("facebook", "human", [media("video", True, targets=["facebook"])])["decision"], "required")

    def test_eu_text_rule_and_human_review_exemption(self) -> None:
        def eu(authorship, public_interest=None, review=None, **kw):
            return one("linkedin", authorship, eu=True, public_interest=public_interest, editorial_review=review,
                       **kw)
        self.assertEqual(eu("ai_generated")["eu_decision"], "unknown")
        self.assertEqual(eu("ai_generated", "no")["decision"], "not_required")
        self.assertEqual(eu("ai_generated", "yes")["decision"], "unknown")
        self.assertEqual(eu("ai_generated", "yes", "yes")["decision"], "not_required")
        required = eu("ai_generated", "yes", "no")
        self.assertEqual(required["decision"], "required")
        self.assertIn("EU AI Act Art. 50", required["what_to_tick"])
        self.assertEqual(eu("ai_assisted", "yes", "no")["decision"], "unknown")
        self.assertEqual(eu("human", "yes", "no")["decision"], "not_required")
        sources = [s for r in required["reasons"] for s in r["sources"] if r["rule"] == "eu_ai_act_art50"]
        self.assertTrue(sources and all(s["kind"] == "secondary" for s in sources))
        exempt = eu("ai_generated", "yes", "yes")
        self.assertIn("human review or editorial control", exempt["reasons"][-1]["sources"][0]["quote"])

    def test_eu_deep_fake_and_dates(self) -> None:
        target = one("youtube", "human", [media("video", True)], eu=True)
        self.assertEqual(target["eu_decision"], "required")
        self.assertIn("artistic", target["reasons"][-1]["why"])
        early = one("youtube", "human", [media("video", True)], eu=True, today=date(2026, 7, 1))
        self.assertIn("not yet in force", early["reasons"][-1]["why"])
        self.assertNotIn("not yet in force", target["reasons"][-1]["why"])

    def test_eu_is_off_unless_asked(self) -> None:
        report = ai_labels.assess([{"id": "d", "platform": "youtube", "authorship": "ai_generated"}], [],
                                  registry=REGISTRY, today=TODAY)
        self.assertFalse(report["eu_ai_act_art50"]["evaluated"])
        self.assertIn("--eu", report["eu_ai_act_art50"]["hint"])
        self.assertIsNone(report["targets"][0]["eu_decision"])
        self.assertTrue(report["advisory"])
        self.assertIn("not legal advice", report["note"])

    def test_documents_and_manifests(self) -> None:
        items = ai_labels.items_from_document({"authorship": "ai_assisted", "variants": [
            {"id": "a", "platform": "youtube"}, {"id": "b", "platform": "tiktok", "authorship": "human"}]})
        self.assertEqual([(i["id"], i["platforms"], i["authorship"]) for i in items],
                         [("a", ["youtube"], "ai_assisted"), ("b", ["tiktok"], "human")])
        self.assertEqual(ai_labels.items_from_document({"id": "d", "platforms": ["x", "threads"]})[0]["platforms"],
                         ["x", "threads"])
        with self.assertRaises(ValidationError):
            ai_labels.items_from_document({"authorship": "robot"})
        assets = ai_labels.media_from_manifest({"assets": [
            {"id": "v", "kind": "video", "realistic_synthetic": True, "provenance": {"created_by": "tool"}},
            {"id": "s", "kind": "subtitle"}]})
        self.assertEqual([(a["id"], a["realistic_synthetic"], a["created_by"]) for a in assets], [("v", True, "tool")])
        with self.assertRaises(ValidationError):
            ai_labels.media_from_manifest({"assets": [{"kind": "image", "realistic_synthetic": "yes"}]})

    def test_stored_media_never_claims_realism(self) -> None:
        class Row(dict):
            def keys(self):  # sqlite3.Row-like
                return list(super().keys())

        stored = [{"rights": {"provenance": {"created_by": "tool"}}}, {}]
        with patch("reachweaver.attachments.decode_media", return_value=stored):
            items = ai_labels.media_from_post(Row(media_json="[...]", video_json=None))
        self.assertEqual([(i["kind"], i["created_by"], i["realistic_synthetic"]) for i in items],
                         [("image", "tool", None), ("image", None, None)])

    def test_module_cannot_send(self) -> None:
        self.assertIsNone(ai_labels.SEND_PATH)
        source = (ROOT / "reachweaver/ai_labels.py").read_text(encoding="utf-8")
        imports = set(re.findall(r"from \.(\w+) import", source))
        self.assertLessEqual(imports, {"policy", "china", "attachments", "video", "inputs", "storage"})
        self.assertNotIn("connect(", source.replace("connect_readonly(", ""))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name: str, data: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_registered_grouped_and_read_only(self) -> None:
        args = make_parser().parse_args(["ai-label-check", "--file", "f.json", "--platform", "youtube", "--eu"])
        self.assertEqual(args.command, "ai-label-check")
        from reachweaver.cli_help import GROUPS
        self.assertIn("ai-label-check", {n for _, names in GROUPS for n in names})
        self.assertEqual(COMMANDS["ai-label-check"]["tags"], ("read_only",))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            make_parser().parse_args(["ai-label-check", "--file", "f", "--post-id", "p"])

    def test_campaign_file_json_and_text(self) -> None:
        code, out, err = run_cli(["ai-label-check", "--file", str(ROOT / "examples/campaign.reachweaver.json"),
                                  "--authorship", "ai_generated", "--json"])
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual([t["platform"] for t in report["targets"]], ["linkedin", "bluesky"])
        self.assertEqual([t["decision"] for t in report["targets"]], ["not_required", "unknown"])
        code, out, _ = run_cli(["ai-label-check", "--file", str(ROOT / "examples/campaign.reachweaver.json"),
                                "--platform", "youtube"])
        self.assertEqual(code, 0)
        self.assertIn("not legal advice", out)
        self.assertIn("--eu", out)

    def test_media_manifest_and_eu_flags(self) -> None:
        draft = self.write("draft.json", {"id": "short", "platform": "tiktok", "authorship": "human"})
        manifest = self.write("m.json", {"schema_version": 1, "assets": [
            {"id": "v", "kind": "video", "realistic_synthetic": True, "provenance": {"created_by": "tool"}}]})
        code, out, err = run_cli(["ai-label-check", "--file", str(draft), "--media", str(manifest), "--eu", "--json"])
        self.assertEqual(code, 0, err)
        target = json.loads(out)["targets"][0]
        self.assertEqual((target["decision"], target["platform_decision"], target["eu_decision"]),
                         ("required", "required", "required"))
        self.assertEqual(target["api_field"], "post_info.is_aigc")

    def test_input_errors_exit_2(self) -> None:
        no_platform = self.write("np.json", {"id": "d", "authorship": "human"})
        bad_author = self.write("ba.json", {"platform": "youtube", "authorship": "robot"})
        bad_flag = self.write("bf.json", {"assets": [{"kind": "video", "realistic_synthetic": 1}]})
        good = self.write("g.json", {"platform": "youtube"})
        for argv in (["--file", str(no_platform)], ["--file", str(bad_author)],
                     ["--file", str(self.root / "missing.json")], ["--file", str(good), "--media", str(bad_flag)]):
            code, _, err = run_cli(["ai-label-check", *argv])
            self.assertEqual(code, 2, argv)
            self.assertIn("ERROR", err)

    def test_post_id_reads_the_database_without_changing_it(self) -> None:
        db_path = self.root / "state" / "outbox.sqlite"
        with connect(str(db_path)) as db:
            post_id = add_post(db, "bsky-main", "bluesky", "A synthetic draft for the label check.")
        digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
        code, out, err = run_cli(["--db", str(db_path), "ai-label-check", "--post-id", post_id, "--platform",
                                  "youtube,threads", "--json"])
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual([t["platform"] for t in report["targets"]], ["youtube", "threads"])
        self.assertEqual({t["authorship"] for t in report["targets"]}, {"unknown"})
        self.assertIn("--authorship", report["authorship_note"])
        code, out, _ = run_cli(["--db", str(db_path), "ai-label-check", "--post-id", post_id, "--json"])
        self.assertEqual(json.loads(out)["targets"][0]["platform"], "bluesky")
        code, _, _ = run_cli(["--db", str(db_path), "ai-label-check", "--post-id", "nope"])
        self.assertEqual(code, 2)
        self.assertEqual(hashlib.sha256(db_path.read_bytes()).hexdigest(), digest)
        missing = self.root / "none" / "outbox.sqlite"
        code, _, _ = run_cli(["--db", str(missing), "ai-label-check", "--post-id", "p"])
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())


class MediaManifestFlagTests(unittest.TestCase):
    def base(self, **extra) -> dict:
        asset = {"id": "a", "kind": "image", "path": "missing.png", "targets": ["instagram"], "alt_text": "A chart",
                 "license": "owner", "rights_confirmed": True,
                 "provenance": {"created_by": "owner", "tool": "", "source_url": ""}, **extra}
        return {"schema_version": 1, "assets": [asset]}

    def test_flag_is_optional_typed_and_warns_when_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = validate_media_manifest(self.base(), tmp)
            flagged = validate_media_manifest(self.base(realistic_synthetic=True), tmp)
            off = validate_media_manifest(self.base(realistic_synthetic=False), tmp)
            bad = validate_media_manifest(self.base(realistic_synthetic="yes"), tmp)
        self.assertEqual(off["errors"], plain["errors"])
        self.assertEqual(off["warnings"], plain["warnings"])
        self.assertEqual(flagged["errors"], plain["errors"])
        self.assertTrue(any("ai-label-check" in w for w in flagged["warnings"]))
        self.assertTrue(any("realistic_synthetic must be true or false" in e for e in bad["errors"]))


class ShippingAndDocsTests(unittest.TestCase):
    def test_registry_ships_in_package_and_bundle(self) -> None:
        self.assertIn('"ai_disclosure_registry.json"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        bundled = ROOT / ".claude/skills/reachweaver/scripts/reachweaver/ai_disclosure_registry.json"
        self.assertEqual(bundled.read_bytes(), ai_labels.REGISTRY_FILE.read_bytes())

    def test_user_guide_cites_every_source_and_platform(self) -> None:
        guide = (ROOT / "docs/AI-CONTENT-LABELS.md").read_text(encoding="utf-8")
        for src in REGISTRY["sources"].values():
            self.assertIn(src["url"], guide)
        for entry in REGISTRY["platforms"].values():
            self.assertIn(entry["name"], guide)
        for phrase in ("not legal advice", "re-verify", "2026-10-21", "secondary"):
            self.assertIn(phrase, guide)


if __name__ == "__main__":
    unittest.main()
