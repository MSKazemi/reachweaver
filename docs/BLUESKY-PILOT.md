# Bluesky one-post supervised pilot (throwaway account)

A copy-pasteable runbook for the first and only live action ReachWeaver should take: **one** harmless text post, to a **throwaway Bluesky account you own**, sent by **you** from a terminal an AI agent does not drive. It condenses [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md), [ACCOUNT-ONBOARDING.md](ACCOUNT-ONBOARDING.md) and [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md); read those for the reasoning.

**Status (2026-10-15):** one text post was sent through ReachWeaver to Bluesky on 2026-10-11 and is recorded as a first-party receipt ([bluesky-2026-10-11.json](receipts/bluesky-2026-10-11.json)). This runbook itself was checked against `reachweaver/cli.py` and the offline tests; it is not that account's record. Do not report a new pilot as complete until you hold a first-party receipt (the post URL you opened in the native app).

Nothing in this runbook changes or bypasses the human approval, the dry-run default, the emergency pause, credential isolation or ambiguous-send reconciliation.

## Legend

| Tag | Meaning |
| --- | --- |
| **SAFE-OFFLINE** | No network, no credential read. An agent may run these (they cannot post). |
| **OWNER-ONLY** | Needs a credential, an online request, an approval or a live send. Run it yourself, in your own terminal, with the agent not driving it. Never paste a secret into Claude, a chat, an issue or a screenshot. |

All commands use `python3 -m reachweaver` from the repository root. `POST_ID` is the 16-hex-character id printed by `draft`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | `publish --post-id`: dry-run preview printed, or the post was sent and confirmed (`PUBLISHED POST_ID: URL`). Check the printed line, not only the code. |
| `2` | Blocked before sending (a `BLOCKED`/`SKIPPED` line, a refused gate, or any `ERROR:` on stderr). Nothing was claimed or sent. |
| `3` | The remote outcome is uncertain: the post is `needs_review` (`REVIEW POST_ID: ...`). **Do not retry.** Go to [Unknown outcome](#unknown-outcome-exit-3). |
| `130` | Interrupted. If a live send was in progress, reconcile before trying again. |

`credentials-audit` is the one command that uses exit `1` (findings).

## Step 0. Throwaway account and app password (OWNER-ONLY)

1. Create a Bluesky account used for nothing else, with its own email. Do not use your main account for the first pilot.
2. In the official Bluesky app (in its settings, under app passwords) create a dedicated **app password**. Never use the login password. Copy it into your password manager; do not paste it anywhere else.
3. Note the exact handle (for example `rw-pilot.bsky.social`).

## Step 1. Offline readiness (SAFE-OFFLINE)

```bash
python3 -m unittest discover -s tests -q
cp config/accounts.example.json config/accounts.json   # ignored by git
```

Edit `config/accounts.json` so it contains **only** the Bluesky account you will use, with public values only:

```json
{
  "accounts": [
    {
      "id": "my-bluesky",
      "platform": "bluesky",
      "env_prefix": "REACHWEAVER_BSKY",
      "handle": "rw-pilot.bsky.social"
    }
  ]
}
```

Then:

```bash
python3 -m reachweaver init
python3 -m reachweaver doctor          # lists the variable NAMES still missing; no values, no network
python3 -m reachweaver capabilities --platform bluesky
python3 -m reachweaver health
```

Expected: `doctor` lists `REACHWEAVER_BSKY_IDENTIFIER` and `REACHWEAVER_BSKY_APP_PASSWORD` as missing, and `capabilities` shows a live text adapter for Bluesky. If the state directory was created with broad permissions, run `chmod 700 state; chmod 600 state/reachweaver.sqlite` after checking ownership.

## Step 2. Provide credentials in your own terminal (OWNER-ONLY)

In a terminal the agent does not drive (hidden input, nothing lands in shell history):

```bash
export REACHWEAVER_BSKY_IDENTIFIER='rw-pilot.bsky.social'
read -r -s -p 'Bluesky app password (input hidden): ' REACHWEAVER_BSKY_APP_PASSWORD; printf '\n'
export REACHWEAVER_BSKY_APP_PASSWORD
python3 -m reachweaver credentials-audit --history ~/.bash_history   # exit 0 expected; prints names and line numbers only
```

If `credentials-audit` exits `1`, fix the finding (revoke and recreate the app password if it ever appeared in history) before continuing.

## Step 3. Optional safeguards, strongly recommended (OWNER-ONLY)

Proposal and limits: SECURITY-REVIEW-PROPOSAL-7-d6-d7.md. Both are friction against an agent, not proof that a human is present.

```bash
python3 -m reachweaver approval-mode --confirmation tty_hash_prefix   # D6: approve and publish --live need a real terminal and the first 8 hex chars of the payload sha256
python3 -m reachweaver approval-mode                                  # shows both settings
```

D7 (keyed approval): set `REACHWEAVER_APPROVAL_SECRET` (at least 32 characters, generated and held where the agent cannot read it, see [owner approval key](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7)), then run `python3 -m reachweaver approval-mode --require-key` once. No command removes the latch. Skip D7 if you cannot keep the key from the agent.

## Step 4. Online identity check (OWNER-ONLY)

This authenticates (Bluesky `createSession`, counted against a local budget) and **never posts**:

```bash
python3 -m reachweaver connect-check --account my-bluesky --online
```

Expected JSON: `account_id: my-bluesky`, `platform: bluesky`, `identity: verified`, `write_permission: unverified` (an identity check is not a write test). Any mismatch (exit `2`) blocks publishing; fix the local mapping first.

## Step 5. Draft one harmless post (SAFE-OFFLINE)

Use one real, true, low-stakes sentence. Never use the bundled sample text, a claim you cannot source, or a link you have not opened.

```bash
python3 -m reachweaver draft --account my-bluesky --text 'Testing ReachWeaver, an approval-first publisher, on my own throwaway account.'
```

The command prints `Created draft POST_ID for my-bluesky; not published`. Record `POST_ID` and use it below.

```bash
python3 -m reachweaver show POST_ID      # exact text, account and destination; offline
python3 -m reachweaver check POST_ID     # validation (length, links, opt-out, claims); offline
python3 -m reachweaver list --status draft
```

Fix anything `check` reports by creating a new draft (and `cancel POST_ID` for the old one). A plan, a campaign file or a Claude Code session is never approval.

## Step 6. Approve this exact post (OWNER-ONLY)

Read the output of `show` again, then:

```bash
python3 -m reachweaver approve POST_ID
```

This binds an immutable payload hash (and, with D6 on, asks you to type the first 8 characters of the shown `payload sha256` on the terminal; with D7 on, it signs the approval). Changed your mind or spotted a typo:

```bash
python3 -m reachweaver unapprove POST_ID    # approved or scheduled -> draft (SAFE-OFFLINE, always allowed)
python3 -m reachweaver cancel POST_ID       # draft, approved or scheduled -> cancelled
```

## Step 7. Dry run of exactly one post (SAFE-OFFLINE)

```bash
python3 -m reachweaver publish --post-id POST_ID
```

No `--live`, so there is no network. Confirm the output names exactly one post, the right account, and a `DRY RUN: 1 due approved posts; no requests sent` line and a `Live gate:` line (it shows your `REACHWEAVER_ALLOW_LIVE` and `REACHWEAVER_ENV` state). Exit `0` expected; `2` means something blocks it (read the `BLOCKED` or `SKIPPED` reason).

Have the stop command ready in a second terminal before step 8: `python3 -m reachweaver pause`.

## Step 8. The single live send (OWNER-ONLY)

Only if you explicitly authorize sending this exact post to this verified account. `REACHWEAVER_ENV=prod` is set only on the machine you publish from, only for this one command, never in a shell an agent drives:

```bash
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 python3 -m reachweaver publish --live --post-id POST_ID
```

Result by exit code:

| Exit | Output line | What to do |
| --- | --- | --- |
| `0` | `PUBLISHED POST_ID: URL` | Open the URL in the native Bluesky app and continue to [Record the receipt](#step-9-record-the-receipt-owner-only). |
| `2` | `BLOCKED`, `SKIPPED` or `ERROR:` | Nothing was sent. Read the reason, fix it, re-run the dry run. |
| `3` | `REVIEW POST_ID: ...; verify remote account before reconciliation` | The outcome is unknown. Follow [Unknown outcome](#unknown-outcome-exit-3). **Never rerun `publish --live`.** |

`publish --post-id` limits the run to that one post. Other queued posts are untouched. Do not start an unattended scheduler for the pilot. The defaults of at most 2 live attempts per account in 24 hours and 60 minutes between attempts apply.

## Step 9. Record the receipt (OWNER-ONLY)

Open the account's profile in the Bluesky app and find the post yourself. Keep a private record (not a public issue; no screenshots containing tokens):

- date and time with timezone, target account id and observed native profile
- exact reviewed text and its `payload sha256` as shown by `approve`
- who approved, the dry-run result, and the exit code of the live command
- the remote post URL or record key and the native timestamp
- delivery status from `python3 -m reachweaver list` and `python3 -m reachweaver show POST_ID`
- any error, `needs_review` outcome or unknown result
- that no other queued post was sent, and the date you checked Bluesky's current terms and rate limits

Promote the Bluesky area to `live_receipt_verified` only by checking in the first-party receipt file and naming it in `receipt` in [release-readiness.json](release-readiness.json), as described in [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md#b-before-each-release-maintainer). A single test post establishes no reach improvement. It measures nothing about growth.

## Emergency stop (OWNER-ONLY, any time)

```bash
python3 -m reachweaver pause      # prints "Emergency pause ON. All live publishing is blocked"
```

This blocks every new claim, including a `publish --live` in progress that has not yet claimed its post. It cannot recall a request already sent. After pausing: inspect the native account, then `python3 -m reachweaver dead-letter` and `queue-health`. Lift it only when you know the cause:

```bash
python3 -m reachweaver resume     # prints "Emergency pause OFF"
```

Leaked approval key or credential: `pause`, revoke at the provider first, replace everywhere, re-approve queued posts. See [SECURITY-CHECKLIST.md section C](SECURITY-CHECKLIST.md#c-when-something-goes-wrong).

## Unknown outcome (exit 3)

The post is `needs_review` (or stuck in `sending`). ReachWeaver never resends it.

1. Open the throwaway account in the native Bluesky app and look for the post.
2. `python3 -m reachweaver dead-letter` lists `needs_review` posts with reconcile steps (read-only).
3. Record what you saw. Exactly one of:

```bash
# The post is on Bluesky: remote id is required
python3 -m reachweaver reconcile POST_ID --published --remote-id 'RECORD_KEY_OR_AT_URI' --remote-url 'https://bsky.app/profile/HANDLE/post/RECORD_KEY'
# The post is not on Bluesky: returns the post to draft; create and approve it again deliberately
python3 -m reachweaver reconcile POST_ID --not-published
```

`reconcile` only accepts posts in `sending` or `needs_review`, and it records your attestation, not proof. Check carefully. A post still in `sending` that was claimed less than 15 minutes ago is refused, because the send may still be in flight; wait, or add `--force` once you have checked the native account (draft, pending owner security review).

## Teardown (OWNER-ONLY)

1. In the Bluesky app, delete the pilot post if you do not want it public, and revoke the app password (in its settings).
2. In the pilot terminal: `unset REACHWEAVER_BSKY_APP_PASSWORD REACHWEAVER_BSKY_IDENTIFIER REACHWEAVER_APPROVAL_SECRET REACHWEAVER_ENV REACHWEAVER_ALLOW_LIVE`, then close the terminal. Remove the password-manager entry if the account is retired.
3. Optionally delete the throwaway account in the Bluesky app.
4. Clean local state (offline): `python3 -m reachweaver data-purge --help` and `privacy-status` describe retention commands; `config/accounts.json` and `state/` stay ignored by git. Review [PRIVACY-LIFECYCLE.md](PRIVACY-LIFECYCLE.md) before deleting anything you may need as evidence.
5. Run `python3 -m reachweaver credentials-audit --history ~/.bash_history` again; it must exit `0`.
6. Run `python3 -m reachweaver doctor`; it should show the account's credentials as missing again.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Common stops: `bluesky_create_session_budget_exhausted` (wait, the budget is half of Bluesky's documented limit), a rate-limit cooldown (waiting is the fix), `approval_mac_*_reapproval_required` (re-approve after a key change), and a post that is not due or not approved (`list`, then `approve`).
