# Security checklist

A short, actionable list for three moments: before your first live send, before each release, and when something goes wrong. It summarizes controls described in [SECURITY.md](SECURITY.md) and THREAT-MODEL.md; read those for the reasoning and the residual risks. Ticking a box is your own check, not a guarantee.

Nothing in this list changes or bypasses the human approval, the dry-run default, the emergency pause, credential isolation or ambiguous-send reconciliation. Changes to those rules need a separate security review.

## A. Before your first live send (account owner)

Offline preparation:

- [ ] You completed the [quickstart](QUICKSTART.md) dry run and know what `show`, `approve`, `publish --post-id` and `pause` do.
- [ ] `config/accounts.json` holds public metadata only: ids, handles, servers, `env_prefix`. ReachWeaver refuses secret-like keys there, and git ignores the file.
- [ ] Each account in it is one **you** control and are allowed to post to. You never configure a client's, employer's or friend's account without their written authorization.
- [ ] You use least-privilege credentials: a Bluesky App Password (never the main password), a Mastodon token limited to `read:accounts` and `write:statuses`, a Telegram bot that is admin of that one channel, a LinkedIn member token for your own profile.
- [ ] Credentials live only in your environment or a local secret manager ([account onboarding](ACCOUNT-ONBOARDING.md)). They were never typed into Claude, a chat, an issue, a screenshot, a committed file or a shell command that lands in history.
- [ ] `reachweaver credentials-audit --history ~/.bash_history` (or your shell's history file) exits 0. It prints variable names and line numbers only.
- [ ] `reachweaver doctor` and `reachweaver health` show the account, adapter and credential **names** you expect.
- [ ] The state directory is private (`state/` mode 0700, database 0600) and on a persistent disk you back up. CI runners and shared machines are not suitable.
- [ ] `reachweaver capabilities --platform P` shows a live text adapter for the platform you plan to use. Every other platform is manual: see [troubleshooting](TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives).

Online, on your own machine:

- [ ] `reachweaver connect-check --account ID --online` confirms the exact remote identity (handle, `sub`, bot and channel). It authenticates and never posts.

The send itself:

- [ ] You ran `show POST_ID` and read the exact text, account and link, and every claim is backed by a source you opened today.
- [ ] You approved **this** post with `approve POST_ID`. A plan, a campaign file, a calendar or a Claude Code session is never approval.
- [ ] Optional (#7 D6/D7, proposal): on an owner-attended host, `reachweaver approval-mode --confirmation tty_hash_prefix`, so `approve` and `publish --live` need a terminal and the typed hash prefix. On a worker host, set up the [owner approval key](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7) where the agent cannot read it, and run `approval-mode --require-key`. Both add friction; neither proves a human is present.
- [ ] Wave 9 item 1: the worker runs as its own system user, so `approval-ledger.jsonl` (next to the database, mode 0600) is not writable from the agent's account, and the ledger is backed up together with the database. `reachweaver health` shows no `approval_ledger_*` or `approval_nonce_missing_reapproval_required` reason, and a post you did not expect never prints `approval_replay_reapproval_required`.
- [ ] `publish --post-id POST_ID` (dry run) shows exactly one post.
- [ ] Only then, on the production host: `REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID`, with `reachweaver pause` ready. `REACHWEAVER_ENV=prod` is set nowhere else (not on dev or staging machines, not in a shell an AI agent drives).
- [ ] Afterwards you opened the native post. If the post is in `sending` or `needs_review`, you checked the account by hand and ran `reconcile`. You never re-send blindly.

Follow [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md) for the full supervised sequence and the evidence to keep privately.

## Public/private visibility decision — separate from a tagged release

Before turning ReachWeaver from a **private** repository into a **public** repository, follow PUBLIC-RELEASE-CHECKLIST.md. It requires review of all Git history/branches/tags, rights to redistribute source and media, accidental personal data and historical credentials, and an independent **owner decision**. Current-file CI secret scans and a passing release gate do **not** prove that repository history is safe. The About-metadata helper does not change visibility.

## B. Before each release (maintainer)

- [ ] Approval replay ledger (wave 9 item 1): `tests/test_approval_ledger.py` passes, `claim_sending` still calls `ledger.consume` before it flips the row to `sending`, `check_approval_binding` still repeats the read-only ledger check, and no code path writes `approval_nonce` other than `transition`.
- [ ] Send gate (wave 9 item 3): `tests/test_send_gate.py` passes, `publishers.publish` and `publish_video` still start with `sendgate.consume`, no module other than `storage.py` calls `sendgate.register_claim` and no env var or flag disables the gate. A script that imports `reachweaver.publishers` and calls `publish()` directly must fail with a `send_*` code; this stops accidents, not a determined process running as the same OS user.

- [ ] `make release-gate` exits 0: versions agree, `CHANGELOG.md` has the version section, required docs exist, README names every CLI subcommand, relative links resolve, the skill bundle is in sync, every blocked platform has an alternative, the [release-readiness scorecard](release-readiness.json) is valid, and the offline unit tests pass. For the tag itself: `make release-gate TAG=vX.Y.Z` (also requires a dated CHANGELOG section). The tag-triggered release workflow (`.github/workflows/release.yml`) runs the same gate with the tag before it builds or publishes the bundle and refuses to publish if it fails (`tests/test_release_workflow.py` pins this).
- [ ] `make check`, `make ruff` and `make typecheck` exit 0, the same hygiene jobs CI runs (secret scan, stdlib lint, runtime-dependency guard).
- [ ] No test, script or CI step publishes, uses a real credential or contacts a platform. `tests/test_no_network.py` and `tests/test_onboarding.py` block sockets.
- [ ] New fake tokens in fixtures are built at runtime, or have a narrow, reasoned `.secret-scan-allow` entry.
- [ ] `pyproject.toml` still has `dependencies = []`. A first runtime dependency needs `pip-audit`, a lock file and an ADR-0002 amendment in the same change.
- [ ] The capability registry was re-verified within 120 days (`reachweaver capabilities` warns), and no live adapter was added without official docs, mock tests and a security review.
- [ ] `python3 scripts/release_gate.py --scorecard` shows each platform and area at its true status. Promote an area to `live_receipt_verified` only by checking in the owner's first-party receipt file and naming it in `receipt`; the gate rejects the status without one.
- [ ] The release notes claim no live account connection, delivered post, view count or growth result without first-party evidence.

## C. When something goes wrong

1. **Stop new sends:** `reachweaver pause`. This blocks every new claim.
2. **Leaked approval key (`REACHWEAVER_APPROVAL_SECRET`, #7 D7):** `pause`, replace the key everywhere, then re-approve every queued post. Rows signed with the old key are refused (`approval_mac_mismatch_reapproval_required`). Treat any post approved while the key was exposed as unreviewed.
3. **Leaked credential:** revoke it at the provider first ([revocation per provider](ACCOUNT-ONBOARDING.md#revoking-tokens-at-each-provider)), then remove it from the environment, history and any file, and rotate.
4. **Wrong or unexpected post:** delete it in the native app if needed, then `reconcile` the local record to match what really happened.
5. **Unknown outcome:** `reachweaver dead-letter` and `reachweaver queue-health` list `needs_review` and stale `sending` posts. Check each one by hand before `reconcile`.
6. **Vulnerability in ReachWeaver:** report it privately ([SECURITY.md](SECURITY.md#reporting-a-vulnerability-private)), never in a public issue.
7. Resume only after you know the cause: `reachweaver resume`.
