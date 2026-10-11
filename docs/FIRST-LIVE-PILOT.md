# First supervised live pilot: local account authorization and one-post delivery

**Status (2026-10-08):** The account-identity checks are implemented and verified with offline mocks. There is **no verified live account**, no real post receipt and no real growth measurement yet. Do not report a completed pilot until the owner runs the commands on their own device and records an actual remote post URL. The steps below are not a substitute for official API scopes, provider approval or terms of service.

## Safety model

- The **owner** signs in to the provider's official interface and handles MFA/permission prompts. Never paste app passwords, OAuth tokens, OTPs or browser cookies into Claude Code, ChatGPT, GitHub, screenshots or a PR.
- Local `config/accounts.json` stores public identifiers only; it is ignored by Git. Credentials must be injected via a trusted local environment or secrets manager, **not** committed `.env` files. Avoid shell commands with literal secrets because shell history and process listings can disclose them.
- `doctor`, `show` and dry-run `publish` are offline. `connect-check --online` performs explicit remote identity/authentication queries but **does not post content**. Bluesky uses its create-session endpoint; this is an authentication operation, not a social post.
- `connect-check` returning `identity: verified` proves that the configured account identity was observed, **not** that all future posting scopes, rate limits or media requirements are approved. For Bluesky and Mastodon, `write_permission: unverified` is intentional. Telegram channel administrators are checked using `getChatMember`.
- A first live post is always one **exact post ID** with an observed owner approval, global pause checked, `--live`, the explicit `REACHWEAVER_ALLOW_LIVE=1` toggle, `REACHWEAVER_ENV=prod` (set only on the machine that publishes) and the identity preflight; other queued posts must remain untouched. Do not run an unattended scheduler for the first pilot.

## Pilot 0: offline readiness (no secrets)

From a trusted Linux/macOS shell in the checked-out private repository:

```bash
python -m unittest discover -s tests -q
cp config/accounts.example.json config/accounts.json
# Edit only IDs/handles/server/chat identifiers in the ignored file.
python -m reachweaver.cli doctor
python -m reachweaver.cli init
```

If an earlier state directory was created with broad permissions, correct it **locally** with `chmod 700 state` and `chmod 600 state/reachweaver.sqlite` (only after validating ownership/contents); the CLI now refuses a permissive POSIX directory or SQLite file. The default `state/` is ignored by Git.

## Pilot 1: Bluesky (recommended first)

A condensed, step-by-step runbook with OWNER-ONLY and SAFE-OFFLINE tags, exit codes, emergency stop, teardown and receipt recording is in [BLUESKY-PILOT.md](BLUESKY-PILOT.md).

1. On the official Bluesky site/app, create a dedicated app password for an account you control (not your main login password). Verify the app-password feature and policy through the provider. In `config/accounts.json`, set `handle` to the exact account handle and `env_prefix` to `REACHWEAVER_BSKY`.
2. Add `REACHWEAVER_BSKY_IDENTIFIER` and `REACHWEAVER_BSKY_APP_PASSWORD` to your own **trusted local process environment**. To avoid shell history, use silent terminal input, e.g.:

   ```bash
   export REACHWEAVER_BSKY_IDENTIFIER='your-handle.bsky.social'
   read -r -s -p 'Bluesky app password (input hidden): ' REACHWEAVER_BSKY_APP_PASSWORD
   printf '\n'
   export REACHWEAVER_BSKY_APP_PASSWORD
   ```

   Do not run the password entry through an AI-operated terminal session or terminal logs. Shell environment storage remains process-accessible; prefer a keyring/secret manager for persistent deployment.
3. With the owner present, run `python -m reachweaver.cli connect-check --account my-bluesky --online`. It should report the expected account ID, platform, `identity: verified`, and `write_permission: unverified` (because an identity check is not a write test). Any mismatch blocks publishing; fix the local account mapping before proceeding.
4. Prepare **one real, harmless and verified** text post (never the bundled placeholder/sample) and record its ID:

   ```bash
   python -m reachweaver.cli draft --account my-bluesky --text 'A genuine project update backed by my public release notes.'
   python -m reachweaver.cli show POST_ID     # displays exact full content and destination
   python -m reachweaver.cli check POST_ID
   python -m reachweaver.cli approve POST_ID  # only after the owner reviews the exact text
   python -m reachweaver.cli publish --post-id POST_ID   # DRY RUN; no network
   ```

   Recommended for the pilot, both optional (#7, owner-approved 2026-10-09, proposal). Turn them on in your own terminal **before** `approve`:

   - **D6 human confirmation:** `python -m reachweaver.cli approval-mode --confirmation tty_hash_prefix`. `approve` of a draft and `publish --live` then print the exact payload and its `payload sha256`, need a real terminal on stdin and stdout, and accept only the first 8 characters of that hash typed on `/dev/tty`. A script or agent subprocess is refused. This is friction, not a security boundary.
   - **D7 owner approval key:** provide `REACHWEAVER_APPROVAL_SECRET` (at least 32 characters) in your own terminal, only to the commands that need it, and run `python -m reachweaver.cli approval-mode --require-key` once. From then on `approve`, `schedule` and `publish --live` refuse without it (`approval_key_unavailable`). Every approval is then HMAC-signed, and an edited or forged database row is refused before any claim. It is a boundary only if the agent cannot read the key; no command removes the latch. Steps: [owner approval key](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7).

   `python -m reachweaver.cli approval-mode` with no options shows both settings.

5. **Only if the owner explicitly authorizes sending this exact post to the verified account**, perform the single supervised send:

   ```bash
   REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 python -m reachweaver.cli publish --live --post-id POST_ID
   ```

6. Confirm the actual post in the intended native account and archive the returned URL/ID in your private pilot notes. For an explicitly selected post the command returns exit code `0` on a confirmed send, `2` when blocked before sending, or `3` when the remote outcome requires review. If the CLI marks `needs_review`, **do not blindly retry**: inspect the native account first, then use `reconcile` with evidence of the real outcome. Rotate/revoke the app password after experiments as appropriate.

## Mastodon, Telegram and LinkedIn follow-up

- **Mastodon:** In ignored account config specify `server: https://<trusted-instance>` and `handle: your-local-username` or `your-local-username@instance`. Provision a user-authorized token through the instance's supported process, using `read:accounts` (or supported `profile`) for identity checks and `write:statuses` for publishing; check current scope support. `connect-check --account my-mastodon --online` verifies the **local account username** via `GET /api/v1/accounts/verify_credentials`; this cannot demonstrate that the token also has `write:statuses`.
- **Telegram channels:** In ignored metadata specify the exact `chat_id`, `channel_username` where public, and **expected `bot_username`**. Create/authorize the bot and add it to the exact channel with permission to post. `connect-check --account my-telegram-channel --online` reads the bot identity, target channel and posting permissions. Private chats/groups are intentionally unsupported by this first automated connector.
- **LinkedIn personal profile (member):** a text-only Posts API adapter exists and is mock-tested only (issue #14). In ignored metadata set `person_id` and the pinned `linkedin_version` (optionally `authorization_expires_at`); `connect-check --account my-linkedin --online` compares `GET /v2/userinfo` `sub` with `person_id` and reports `write_permission: unverified`. Optionally, add `"scope_check": "introspect"` and export the app's `REACHWEAVER_LINKEDIN_CLIENT_ID` and `REACHWEAVER_LINKEDIN_CLIENT_SECRET`: read-only token introspection must then show an active member token with `w_member_social` before `connect-check` reports `verified` and before every send is claimed. It stores the app client secret on the publishing host ([LINKEDIN.md, section 3a](LINKEDIN.md#3a-optional-verify-w_member_social-before-every-send)). Organization pages are refused. Follow [LINKEDIN.md](LINKEDIN.md) for the developer app, scopes and token steps; the same one-post supervised pilot applies.
- **Optional Postiz backend:** mock-tested only, no instance connected (issue #30). A Postiz acceptance is recorded as `needs_review` until you check the native post and run `reconcile`; see [POSTIZ.md](POSTIZ.md).
- **Instagram, Threads, TikTok, YouTube and Facebook Pages:** no live posting from ReachWeaver yet. The Instagram, Threads, TikTok and YouTube adapters are mock-tested only and stay disabled in the capability registry until the owner records a live receipt. Use `manual-export --platform PLATFORM --variant VARIANT.json --out NEW_DIR` (creator package and checklist) and post in the native app ([manual export](MANUAL-EXPORT.md)).
- **LinkedIn organization pages, X and others:** No direct automated posting authorization yet. Use `campaign-export` and post manually until official app consent and media/API workflows are independently implemented and tested. Never use stored login cookies or browser automation to evade restrictions.

## Evidence checklist before reporting success

Keep a *private* pilot record (not a public Issue): date/time with timezone; target account ID and observed native profile; original evidence/source link; exact reviewed text hash; approver; dry-run result; remote post URL/ID and native timestamp; delivery status; errors/unknown outcomes; permissions and service policy date; native analytics snapshot at predeclared age; whether other queued posts remained untouched. Avoid screenshots containing tokens. No reach improvement is established from a single test post.

## Official references (recheck before activation)

- Bluesky: https://docs.bsky.app/docs/tutorials/creating-a-post and session lexicon https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/server/createSession.json
- Mastodon: https://docs.joinmastodon.org/methods/accounts/ and https://docs.joinmastodon.org/api/oauth-scopes/
- Telegram Bot API: https://core.telegram.org/bots/api#getme and https://core.telegram.org/bots/api#getchatmember
