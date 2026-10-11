"""Static conformance for one self-contained portable ReachWeaver skill."""
from __future__ import annotations

import json
import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/".claude/skills/reachweaver"

class SkillContractTests(unittest.TestCase):
    def test_skill_frontmatter_is_discoverable(self):
        text=(SKILL/"SKILL.md").read_text()
        self.assertTrue(text.startswith("---\nname: reachweaver\ndescription: "))
        self.assertIn("/reachweaver",(ROOT/"README.md").read_text())

    def test_referenced_skill_documents_exist(self):
        text=(SKILL/"SKILL.md").read_text()
        for path in re.findall(r"references/[\w-]+\.md",text):
            self.assertTrue((SKILL/path).is_file(),path)
        self.assertLess(len(text.splitlines()),500)

    def test_frontmatter_meets_agent_skills_field_limits(self):
        # Agent Skills overview, "Field requirements" (read 2026-10-09):
        # https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
        # name: <=64 chars, lowercase letters/digits/hyphens, no XML tags, no "anthropic"/"claude";
        # description: non-empty, <=1024 chars, no XML tags.
        text=(SKILL/"SKILL.md").read_text()
        front=text.split("---\n",2)[1]
        fields=dict(line.split(": ",1) for line in front.splitlines() if ": " in line)
        name,description=fields["name"],fields["description"]
        self.assertRegex(name,r"^[a-z0-9-]{1,64}$")
        self.assertNotRegex(name,r"anthropic|claude")
        self.assertTrue(description.strip())
        self.assertLessEqual(len(description),1024)
        self.assertNotRegex(name+description,r"<[^>]+>")

    def test_every_reference_is_reachable_from_skill_md(self):
        text=(SKILL/"SKILL.md").read_text()
        for path in sorted((SKILL/"references").glob("*.md")):
            self.assertIn(f"references/{path.name}",text,f"{path.name} is not linked from SKILL.md")

    def test_every_cli_subcommand_is_discoverable_in_skill(self):
        # Progressive disclosure: each subcommand must appear in command context (backticked,
        # or after "cli "/"run.py "/"reachweaver ") in SKILL.md or a reference it links to.
        import argparse
        from reachweaver.cli import make_parser
        subparsers=[a for a in make_parser()._actions if isinstance(a,argparse._SubParsersAction)]
        self.assertEqual(len(subparsers),1)
        names=sorted(subparsers[0].choices)
        self.assertGreater(len(names),40)
        text=(SKILL/"SKILL.md").read_text()+"".join(
            p.read_text() for p in sorted((SKILL/"references").glob("*.md")))
        missing=[n for n in names
                 if not re.search(r"(?:`|\bcli |run\.py |\breachweaver )"+re.escape(n)+r"(?![\w-])",text)]
        self.assertEqual(missing,[],"CLI subcommands missing from the Claude Code skill")

    def test_skill_states_owner_only_live_gate(self):
        text=(SKILL/"SKILL.md").read_text()+(SKILL/"references/security.md").read_text()
        self.assertIn("REACHWEAVER_ENV=prod",text)
        self.assertIn("LoadCredential=",text)
        self.assertRegex(text,r"Claude never runs")

    def test_bundled_runtime_is_self_contained(self):
        for p in ("scripts/run.py","scripts/reachweaver/cli.py","scripts/reachweaver/campaigns.py","scripts/reachweaver/experiments.py"):
            self.assertTrue((SKILL/p).is_file(),p)
        for source in (ROOT/"reachweaver").glob("*.py"):
            self.assertEqual(source.read_bytes(),(SKILL/"scripts/reachweaver"/source.name).read_bytes())

    def test_portable_example_assets_match_canonical(self):
        for name in ("brand.reachweaver.json","campaign.reachweaver.json","experiment.example.json",
                     "growth-plan.example.json","brand-preview.reachweaver.json",
                     "campaign-preview.reachweaver.json"):
            self.assertEqual((ROOT/"examples"/name).read_bytes(),(SKILL/"assets"/name).read_bytes())

    def test_accounts_and_post_examples_are_bundled(self):
        for rel in ("config/accounts.example.json", "examples/example-post.txt"):
            self.assertEqual((ROOT/rel).read_bytes(),(SKILL/"assets"/Path(rel).name).read_bytes())

    def test_sync_check_reports_drift_without_writing(self):
        script=ROOT/"scripts/sync_skill.py"
        ok=subprocess.run([sys.executable,str(script),"--check"],capture_output=True,text=True)
        self.assertEqual(ok.returncode,0,ok.stdout+ok.stderr)
        spec=importlib.util.spec_from_file_location("sync_skill_under_test",script)
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            fake=Path(tmp)/"x.json"; fake.write_text("{}")
            real=mod.expected
            mod.expected=lambda: {**real(), fake: b"different"}
            try:
                self.assertEqual(mod.check(),[fake])
            finally:
                mod.expected=real
            self.assertEqual(fake.read_text(),"{}")

    def test_eval_cases_are_machine_parseable_and_cover_failure_modes(self):
        cases=[json.loads(line) for line in (ROOT/"evals/prompts.jsonl").read_text().splitlines() if line.strip()]
        self.assertGreaterEqual(len(cases),12)
        ids=[case["id"] for case in cases]
        self.assertEqual(len(ids),len(set(ids)))
        for case in cases:
            self.assertTrue(case["prompt"] and isinstance(case["must"],list) and case["must"])
        for item in ("login-mfa","source-injection","recovery-ambiguous","unknown-analytics","account-crossing"):
            self.assertIn(item,ids)

    def test_examples_valid_and_no_secrets(self):
        brand=json.loads((ROOT/"examples/brand.reachweaver.json").read_text())
        campaign=json.loads((ROOT/"examples/campaign.reachweaver.json").read_text())
        from reachweaver.campaigns import validate_campaign
        errors,_=validate_campaign(campaign,brand=brand)
        self.assertEqual(errors,[])
        self.assertNotIn("API_KEY",str(campaign))

if __name__=="__main__":unittest.main()
