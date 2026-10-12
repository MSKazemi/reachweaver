# Instagram one-post supervised pilot (throwaway account)

A runbook for the first live Instagram action ReachWeaver should ever take: **one** JPEG post with caption and alt text, to a **throwaway Professional Instagram account you own**, sent by **you** from a terminal an AI agent does not drive. It follows the structure of [BLUESKY-PILOT.md](BLUESKY-PILOT.md) and condenses [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md) and [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md). Account preparation is in [INSTAGRAM-SETUP.md](INSTAGRAM-SETUP.md); adapter behavior is in [INSTAGRAM.md](INSTAGRAM.md).

**Status (UNVERIFIED, MOCK-TESTED only):** no Meta app, Instagram account or live post exists, and no receipt exists in this repository. The adapter is exercised only against mocked responses. The capability registry keeps Instagram at `api_gated` with adapter `none`, and `instagram` is not in `policy.SUPPORTED_LIVE`, so **steps 4 and 8 below cannot run today**: `connect-check --online` and `publish --live` refuse Instagram before any credential read or request. This is deliberate. Enabling it is a separate, reviewed change that needs the owner's security review and go (the Instagram security review proposal, kept private); this runbook does not do it and an agent must not. Do not report the pilot as complete until you hold a first-party receipt (the post opened in the native Instagram app).

Nothing here changes or bypasses the human approval, the dry-run default, the emergency pause, credential isolation or ambiguous-send reconciliation.

## Legend

| Tag | Meaning |
| --- | --- |
| **SAFE-OFFLINE** | No network, no credential read. An agent may run these (they cannot post). |
| **OWNER-ONLY** | Needs a credential, an online request, an approval or a live send. Run it yourself, in your own terminal. Never paste a secret into Claude, a chat, an issue or a screenshot. |
| **BLOCKED** | Refused by the code until the reviewed enablement change exists. |

Commands use `python3 -m reachweaver` from the repository root. Exit codes are as in [BLUESKY-PILOT.md](BLUESKY-PILOT.md#exit-codes): `0` done, `2` blocked before sending, `3` outcome uncertain (never retry), `130` interrupted.

## Step 0. Throwaway account and Meta app (OWNER-ONLY)

1. Create or convert an Instagram account used for nothing else and switch it to Professional (Business or Creator).
2. Follow [INSTAGRAM-SETUP.md](INSTAGRAM-SETUP.md): Meta developer app with the Instagram API using Instagram Login, your account added as a tester or app role, a dashboard token valid about 60 days.
3. Read the **current** Meta platform terms and the content-publishing guide yourself and note the date. Whether app review or business verification is required for your account is not verified here; if Meta requires it, the pilot waits.
4. Put the JPEG on an HTTPS host you control (plain path, no query string, no redirect, at most 8,000,000 bytes, aspect ratio 4:5 to 1.91:1).

## Step 1. Offline readiness (SAFE-OFFLINE)

```bash
python3 -m unittest discover -s tests -q
cp config/accounts.example.json config/accounts.json   # ignored by git
```

Keep only the throwaway account, public values only:

```json
{
  "accounts": [
    {
      "id": "my-instagram",
      "platform": "instagram",
      "env_prefix": "REACHWEAVER_IG",
      "handle": "your_username",
      "ig_user_id": "17841400000000000",
      "authorization_expires_at": "2026-12-01T00:00:00+00:00"
    }
  ]
}
```

```bash
python3 -m reachweaver init
python3 -m reachweaver doctor
python3 -m reachweaver capabilities --platform instagram
```

`doctor` is the read-only preflight. It lists the missing credential variable **names** (`REACHWEAVER_IG_ACCESS_TOKEN`), a `SETUP:` line naming any of `handle`, `ig_user_id` or `authorization_expires_at` still absent from the config, the token-expiry state, and the `INFO` line saying the adapter is blocked by the registry. It never prints a value and makes no request. Expected today: the credential shows as missing until you export it, and `capabilities` shows no live Instagram adapter.

## Step 2. Provide the token in your own terminal (OWNER-ONLY)

```bash
read -r -s -p 'Instagram token (input hidden): ' REACHWEAVER_IG_ACCESS_TOKEN; printf '\n'
export REACHWEAVER_IG_ACCESS_TOKEN
python3 -m reachweaver credentials-audit --history ~/.bash_history   # exit 0 expected
```

If `credentials-audit` exits `1`, revoke and regenerate the token before continuing.

## Step 3. Optional safeguards (OWNER-ONLY)

Same as [BLUESKY-PILOT.md step 3](BLUESKY-PILOT.md#step-3-optional-safeguards-strongly-recommended-owner-only): `approval-mode --confirmation tty_hash_prefix`, and the keyed approval only if you can keep the key from the agent.

## Step 4. Online identity check (OWNER-ONLY, BLOCKED today)

```bash
python3 -m reachweaver connect-check --account my-instagram --online
```

Today this is refused before any request. After the reviewed enablement it would read `GET /me`, require `user_id` to equal `ig_user_id`, `username` to equal `handle` and `account_type` to be Business or Media_Creator. It never posts. Any mismatch blocks publishing.

## Step 5. Draft one post and attach the image (SAFE-OFFLINE except the one image GET)

Use one true, low-stakes sentence and an image you own. Never use a claim you cannot source.

```bash
python3 -m reachweaver draft --account my-instagram --text 'Testing ReachWeaver, an approval-first publisher, on my own throwaway account.'
python3 -m reachweaver media-attach POST_ID --url https://img.example.org/pilot.jpg --alt 'Describe the image in one sentence' --online
python3 -m reachweaver show POST_ID
python3 -m reachweaver check POST_ID
```

`media-attach --online` makes one credential-free GET of your image URL and records its SHA-256, size and dimensions. Do not change or remove the image until the post is published.

## Step 6. Approve this exact post (OWNER-ONLY)

```bash
python3 -m reachweaver approve POST_ID
```

The approval binds the caption, alt text, image URL, MIME type and image SHA-256. Changed your mind: `unapprove POST_ID` or `cancel POST_ID`.

## Step 7. Dry run (SAFE-OFFLINE)

```bash
python3 -m reachweaver publish --post-id POST_ID
```

No `--live`, so no network. Confirm one post, the right account, `DRY RUN: 1 due approved posts; no requests sent` and a `Live gate:` line. Have the stop command ready in a second terminal: `python3 -m reachweaver pause`.

## Step 8. The single live send (OWNER-ONLY, BLOCKED today)

Only after the reviewed enablement exists **and** you explicitly authorize sending this exact post to this verified account:

```bash
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 python3 -m reachweaver publish --live --post-id POST_ID
```

Exit `0` prints `PUBLISHED POST_ID: URL`; open it in the native Instagram app. Exit `2` means nothing was sent. Exit `3` (`needs_review`) means the outcome is unknown: **never rerun `publish --live`**, go to the unknown-outcome steps below. Stages and their failure hints are listed in [INSTAGRAM.md](INSTAGRAM.md#what-the-adapter-would-send). Do not start an unattended scheduler for the pilot.

## Step 9. Record the receipt (OWNER-ONLY)

Keep a private record (not a public issue; nothing containing a token): date and time with timezone, account id and observed native profile, the exact reviewed caption and image SHA-256 shown by `approve`, the dry-run result and live exit code, the media id and permalink, delivery status from `list` and `show POST_ID`, any error or `needs_review` outcome, that no other queued post was sent, and the date you checked Meta's current terms and limits. Only a first-party receipt checked into the private records and named in [release-readiness.json](release-readiness.json) supports a `live_receipt_verified` claim. One test post establishes nothing about reach or growth.

## Emergency stop (OWNER-ONLY, any time)

```bash
python3 -m reachweaver pause
python3 -m reachweaver resume    # only when you know the cause
```

`pause` blocks every new claim; it cannot recall a request already sent. Leaked token: pause, revoke the token in the Meta dashboard first, replace it everywhere, re-approve queued posts ([SECURITY-CHECKLIST.md section C](SECURITY-CHECKLIST.md#c-when-something-goes-wrong)).

## Unknown outcome (exit 3)

1. Open the throwaway account in the native Instagram app and look for the post.
2. `python3 -m reachweaver dead-letter` lists `needs_review` posts (read-only).
3. Record exactly one of:

```bash
python3 -m reachweaver reconcile POST_ID --published --remote-id 'MEDIA_ID' --remote-url 'https://www.instagram.com/p/SHORTCODE/'
python3 -m reachweaver reconcile POST_ID --not-published
```

`reconcile` records your attestation, not proof. A container that Meta created but never published expires after 24 hours; it is not a public post.

## Teardown (OWNER-ONLY)

1. Delete the pilot post in the app if you do not want it public; revoke the token in the Meta dashboard; remove the test role if the app is retired.
2. `unset REACHWEAVER_IG_ACCESS_TOKEN REACHWEAVER_APPROVAL_SECRET REACHWEAVER_ENV REACHWEAVER_ALLOW_LIVE`, then close the terminal.
3. Run `python3 -m reachweaver credentials-audit --history ~/.bash_history` (must exit `0`) and `python3 -m reachweaver doctor` (credential shows missing again).
4. Local retention: `data-purge --help`, `privacy-status` and [PRIVACY-LIFECYCLE.md](PRIVACY-LIFECYCLE.md).

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and the failure hints in [INSTAGRAM.md](INSTAGRAM.md).
