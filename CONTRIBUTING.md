# Contributing

Open an issue describing the user problem, observed evidence, and proposed verified behavior before broad changes. One PR per independently testable improvement. Include tests, docs, and a rollback plan for anything that might publish, schedule or access accounts.

## Fresh clone: lint, tests and an offline example

Requires Python 3.11+ and git. The runtime has no third-party dependencies, so there is nothing to install; no command below needs a network connection, an account or a credential.

```bash
git clone https://github.com/MSKazemi/reachweaver.git
cd reachweaver

# Offline unit tests (HTTP is mocked; tests/test_no_network.py fails on any socket attempt)
python3 -m unittest discover -s tests -v

# Mirror reachweaver/ into the bundled Claude Code skill (run after every source change)
python3 scripts/sync_skill.py

# Secret scan of tracked files; prints path:line:pattern-id, never the value
python3 scripts/secret_scan.py

# Stdlib lint: syntax of every tracked .py, JSON/JSONL validity; then byte-compile
python3 scripts/lint.py
python3 -m compileall -q reachweaver scripts tests

# The runtime must stay stdlib-only; fails if pyproject.toml gains a runtime dependency
python3 scripts/check_runtime_deps.py

# Offline example run against a throwaway database (never contacts a platform).
# The database directory must be private (mode 0700), so use mktemp -d rather than /tmp itself.
RW_TMP=$(mktemp -d)
python3 -m reachweaver --db "$RW_TMP/example.sqlite" init
python3 -m reachweaver --db "$RW_TMP/example.sqlite" --accounts config/accounts.example.json doctor
python3 -m reachweaver campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json
python3 -m reachweaver distribution-plan --region india --goal adoption --limit 3
```

With `make` available, `make check` runs sync, secret scan, lint, the runtime-dependency guard (`make deps`) and tests in one go (`make help` lists targets; `make example` runs the offline example in a temporary directory). Three more targets download an exactly pinned tool through `pipx` and install nothing into the project:

| Target | What it runs | In CI |
| --- | --- | --- |
| `make ruff` | ruff hard-error rules (`E9,F63,F7,F82`) | Yes, blocking |
| `make typecheck` | mypy on `reachweaver/`, configured in `pyproject.toml` `[tool.mypy]` | Yes, blocking |
| `make format-check` | `ruff format --check` | No. Advisory only, never fails |

Before tagging a release, run `make release-gate TAG=vX.Y.Z` (offline; see [the release gate](docs/ARCHITECTURE.md#onboarding-audit-and-versioned-release-gate-issue-27-slice)). It fails unless the versions agree, `CHANGELOG.md` has a dated section for the version, the required documents exist, README names every CLI subcommand, relative links resolve, the skill bundle is in sync, every blocked platform has a manual alternative or explicit marker, and the unit tests pass. A new platform in either registry needs an entry in `reachweaver/onboarding.py`, and the table in `docs/TROUBLESHOOTING.md` must be regenerated with `python3 scripts/release_gate.py --print-blocker-table`. The maintainer checklist is in [docs/SECURITY-CHECKLIST.md](docs/SECURITY-CHECKLIST.md#b-before-each-release-maintainer).

Type checking is gradual: every `reachweaver` module is checked, but only annotated function bodies (`check_untyped_defs = false`). Do not add a `[[tool.mypy.overrides]]` `ignore_errors` entry to silence a new error; fix the annotation instead. **Do not reformat code** with `ruff format` in a feature PR: whole-repository formatting is deferred to one dedicated change (see ADR-0002, decision 9). Tool versions are pinned exactly in the `Makefile` and `.github/workflows/ci.yml`. Bump both together, on purpose.

**Runtime dependencies.** The runtime is stdlib-only and `pyproject.toml` must keep `dependencies = []`. A PR that adds a runtime dependency must, in the same change, add a pinned `pip-audit` CI step and a hash-pinned lock file, update `scripts/check_runtime_deps.py`, and amend ADR-0002. Recorded provider responses used as test fixtures follow [tests/fixtures/providers/README.md](tests/fixtures/providers/README.md) (scrubbed, synthetic ids, secret scan clean).

Tests must run with mocked HTTP and NO real social credentials, and must never perform live publishing.

## Secret scanning and the allowlist

`scripts/secret_scan.py` checks every git-tracked file for private key blocks, AWS keys, GitHub/Slack/Telegram tokens, JWTs, Bluesky app-password-shaped strings (outside `*example*` files) and literal `password`/`token`/`secret` assignments. It runs in CI and exits 1 on any finding. If a finding is a deliberately fake test fixture, add a narrow entry to `.secret-scan-allow`:

```text
<path-glob> <pattern-id>  # mandatory reason
```

Prefer building fake tokens at runtime (string concatenation) so no allowlist entry is needed. Never allowlist a value that has ever been a real credential — revoke it at the provider and rewrite the fixture. `python3 scripts/secret_scan.py --list-patterns` shows the pattern ids.

## Pull requests and issues

The pull request template asks for test evidence (command and output), documentation updates, a security evaluation of the approval, dry-run, emergency-stop, credential-isolation and ambiguous-send rules, and platform-registry updates where relevant. Use the issue templates for bugs, features and platform adapters; adapters need official documentation URLs, a verification date, the account type and the platform's approval requirements. Report vulnerabilities privately as described in [docs/SECURITY.md](docs/SECURITY.md#reporting-a-vulnerability-private), never in a public issue. Public PRs must not contain secrets, unlicensed third-party skill prose, browser cookies, session files, private analytics or copyrighted media.

Use original code. The upstream research directory is an **attribution/citation catalog, not a vendoring directory**. Postiz is AGPL-3.0 and must not be copied into this Apache-2.0 repository without independent license and provenance review. Preserve author credits for any separately permitted reused work.

Write issues and documentation in clear English. Identify API capabilities as VERIFIED/LIVE-TESTED, MOCK-TESTED or NOT IMPLEMENTED; never infer success from an attractive README.
