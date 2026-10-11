<!--
One PR per independently testable improvement. Link the issue it closes.
Never paste credentials, cookies, session files, private analytics or exact private draft copy here.
-->

## Summary

Closes #

What user problem does this solve, and what verified behaviour changes?

## Test evidence (required)

Paste the exact commands you ran and the tail of their output (the summary line at minimum).

```text
$ python3 -m unittest discover -s tests -v
...
Ran N tests in X.XXXs
OK

$ python3 scripts/secret_scan.py
secret-scan: OK; ...

$ python3 scripts/lint.py
lint: OK; ...
```

- [ ] All offline tests pass locally (`python3 -m unittest discover -s tests -v`, or `make check`).
- [ ] New or changed behaviour has tests; HTTP is mocked and no test opens a real network connection (`tests/test_no_network.py` still passes).
- [ ] **No live publishing in tests, CI or examples**: no real tokens, no `REACHWEAVER_ALLOW_LIVE=1` or `REACHWEAVER_ENV=prod` outside mocked tests, no `--live` against a real account.
- [ ] `python3 scripts/sync_skill.py` was run after any `reachweaver/` change and the bundled skill copy is byte-identical.
- [ ] `python3 scripts/secret_scan.py` is clean. Any new `.secret-scan-allow` entry is a synthetic fixture and has a reason.

## Documentation (required)

- [ ] README / `docs/` / CLI `--help` / skill `SKILL.md` updated for any user-visible change, or: _not user-visible because …_
- [ ] Capabilities are labelled VERIFIED/LIVE-TESTED, MOCK-TESTED or NOT IMPLEMENTED; nothing claims a post, account connection or metric without first-party evidence.

## Security evaluation (required)

Answer each line; "untouched" is a valid answer.

- Human approval gate (`approve` before any send): untouched / changed — reviewed by:
- Dry-run default and the `--live` + `REACHWEAVER_ALLOW_LIVE=1` + `REACHWEAVER_ENV=prod` live gate: untouched / changed — reviewed by:
- Emergency stop (`pause`/`resume`): untouched / changed — reviewed by:
- Credential isolation (secrets only from env or `$CREDENTIALS_DIRECTORY` via `accounts.credential`, non-overlapping `env_prefix`, no tokens in config/logs/output): untouched / changed — reviewed by:
- Ambiguous-send reconciliation (`needs_review`, no automatic retry): untouched / changed — reviewed by:
- New network destinations, redirects, file writes or subprocesses: none / listed:
- HN human-only and Reddit moderator-only rules preserved: yes / not applicable

## Platform registry (where relevant)

- [ ] Not applicable — no platform, channel, scheduling or market data changed.
- [ ] `reachweaver/distribution_registry.json` and `docs/PLATFORM-MATRIX.md` updated, with official documentation URLs and verification dates; `tests/test_distribution.py` passes.
- [ ] Native platform scheduling, mock-tested ReachWeaver adapters and real owner account receipts are kept distinct.

## Rollback plan

Required for anything that could publish, schedule or access accounts. How is this reverted safely?
