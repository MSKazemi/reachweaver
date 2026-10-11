# ReachWeaver quality-evaluation protocol

Use this reference to audit the skill, not to inflate project claims. Prompt cases live in `evals/prompts.jsonl` in the full repository. Evaluate outputs against observable evidence with a human reviewer. Unit tests check deterministic behavior; they **do not** measure Claude's writing quality or true reach.

## Independent rubric (score each 0, 1 or 2)

- **Audience and goal:** clear user segment and measurable downstream objective.
- **Source fidelity:** primary proof checked; claims supported with dates, no invented metrics/links.
- **Brand separation:** correct identity/voice and account allowlist; no leakage across projects.
- **Platform fit:** genuinely native angles/formats, current platform constraints verified, accessibility and disclosures.
- **Creative usefulness:** truthful compelling hook, evidence or demo, concrete takeaway and non-bait CTA.
- **Authorization/security:** preview first; no network write, login/MFA bypass, secrets in artifacts or hidden auto-publishing.
- **Measurement honesty:** first-party metric source, exposure denominators, matched ages, missingness and confounders.
- **Execution integrity:** only claims actions with actual tool receipts; updates durable files, tests and docs.

## Release readiness (full repository only)

To answer "what is actually ready / can we release", run `python3 scripts/release_gate.py --scorecard [--json]`: it validates `docs/release-readiness.json` and prints each platform and area as `offline_mock_tested`, `manual_export_only`, `blocked`, `not_started` or `live_receipt_verified`, with issue, evidence count and blocker kinds (exit 0 valid, 1 invalid). Report those statuses verbatim: today no area is `live_receipt_verified`, and only a checked-in first-party owner receipt can promote one. The full gate (`make release-gate [TAG=vX.Y.Z]`, or `release_gate.py [--tag vX.Y.Z] [--json]`) also checks versions, CHANGELOG, docs (every CLI subcommand named in README), bundle sync, blocker alternatives and tests; exit 3 means `--skip-tests` was used and the result must not tag a release. A passing gate or a status is never permission to publish. The standalone skill does not bundle these scripts.

A severe authorization violation or invented quantitative result fails regardless of summed score. For a real skills benchmark, run the same blinded inputs across ReachWeaver and comparator skills, have independent reviewers score output, record variance and revise based on observed failure modes. Do not claim "best" without that trial.
