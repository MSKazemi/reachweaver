# Account onboarding: connect, check status, disconnect

This guide covers the account lifecycle with the commands that exist today: `doctor`, `credentials-audit`, `connect-check --online`, `pause` and the dry-run `publish`. ReachWeaver reads credentials from **one of two sources**, through one accessor (`reachweaver/accounts.py:credential`):

- **Environment variables** (the default), named `<env_prefix><SUFFIX>`.
- **systemd credentials** (issue #5): when `$CREDENTIALS_DIRECTORY` is set, as systemd does for a unit with `LoadCredential=`, each credential is read from the file `$CREDENTIALS_DIRECTORY/<VARIABLE_NAME>` and environment variables are **not** consulted at all. See [Unattended host: systemd `LoadCredential=`](#unattended-host-systemd-loadcredential).

It has no login wizard, no OAuth redirect flow and no keychain integration. The shell examples below show how an owner can feed those variables from a protected file or a password manager. ReachWeaver itself does not call any of these tools.

**Status (2026-10-15):** Bluesky and Mastodon each have one live text-post receipt ([docs/receipts/](receipts/)); no other platform has been connected or tested from this repository. The steps have been checked against the code and against the provider pages cited below. The `LoadCredential=` path is covered by offline tests (`tests/test_hardening.py`). On 2026-10-16 it was also run once under a real systemd 255 user unit (`systemd-run --user -p LoadCredential=NAME:FILE`) with a synthetic value. ReachWeaver reported `credentials_directory` as the source and read the value from `$CREDENTIALS_DIRECTORY`. It ignored the environment variable of the same name, even though that was set. It has not run under a system (root-managed) worker unit.

Security context: threat model, [security policy](SECURITY.md), [first live pilot](FIRST-LIVE-PILOT.md).

## Ground rules

- **You, the owner, sign in** on the provider's official site or app and complete MFA or CAPTCHA yourself. Never paste an app password, token, OTP or cookie into Claude Code, another AI assistant, an issue, a PR or a screenshot.
- **Keep credentials out of the agent's shell.** Export them only in a separate terminal, or in a process supervisor, that the AI agent does not drive. An agent that can run `env` can read anything exported in its own shell. Run live commands in that separate terminal yourself. This repository's `.claude/settings.json` also denies Claude Code any Bash command that runs `approve` or `publish --live`, or that contains `REACHWEAVER_ALLOW_LIVE=1` or `REACHWEAVER_ENV=prod`, or that turns the #7 D6 confirmation off or names `REACHWEAVER_APPROVAL_SECRET`. That is a harness rule for Claude Code in this checkout only, not a ReachWeaver control.
- `config/accounts.json` holds public identifiers only (handle, server, chat id). `load_accounts` rejects token, password, secret, cookie and session keys. The file is in `.gitignore`.
- Never type `export REACHWEAVER_..._TOKEN=literal`: it lands in shell history. `credentials-audit --history FILE` finds such lines (it prints line numbers only).
- Use one credential per account and one `env_prefix` per account, brand and environment. `load_accounts` **refuses** a config in which two accounts share an `env_prefix` or in which one account's credential variable name equals or begins another's, or equals or overlaps a credential no account owns (`REACHWEAVER_GITHUB_TOKEN`) ([details](#account-and-environment-separation)).
- `publish --live` sends only when **all three** are present: the `--live` flag, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`. Set `REACHWEAVER_ENV=prod` only on the production host.

## 1. Connect

### 1a. Describe the account (no secrets)

```bash
cp config/accounts.example.json config/accounts.json
# Edit ids, handles, server, chat_id, channel_username, bot_username and env_prefix only.
reachweaver doctor            # offline: lists each account and the env variable NAMES it is missing
```

`doctor` prints, for each account, the platform, whether a text adapter exists, and the missing variable names. Its last line points to `credentials-audit`.

| Platform | Variables read (`<P>` = `env_prefix`) | Credential to create |
| --- | --- | --- |
| Bluesky | `<P>_IDENTIFIER`, `<P>_APP_PASSWORD` | A dedicated app password, not your login password |
| Mastodon | `<P>_TOKEN` | A user token with `read:accounts` (identity check) and `write:statuses` (posting), plus `write:media` for image attachments |
| Telegram | `<P>_BOT_TOKEN` | A bot token from @BotFather; the bot must be a channel admin allowed to post |
| LinkedIn | `<P>_ACCESS_TOKEN`; `<P>_CLIENT_ID` and `<P>_CLIENT_SECRET` only with `"scope_check": "introspect"` | A member token with `openid profile w_member_social` from your own developer app ([LinkedIn guide](LINKEDIN.md)) |
| Instagram (adapter disabled) | `<P>_ACCESS_TOKEN` | A token for your own professional account ([Instagram guide](INSTAGRAM.md)) |
| Threads (adapter disabled) | `<P>_ACCESS_TOKEN` | A Threads user token with `threads_basic` and `threads_content_publish` ([Threads guide](THREADS.md)) |
| Facebook Page (adapter disabled) | `<P>_PAGE_ACCESS_TOKEN` | A long-lived **Page** access token (not a User token) for the one Page you configure, from your own Meta app; `doctor` names the eligible Page (`page_name`, `page_id`) and the permissions the token needs (`pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, from the official [Page feed reference](https://developers.facebook.com/docs/graph-api/reference/page/feed), checked 2026-10-15), next to your optional owner-declared list, but never checks them ([Facebook guide](FACEBOOK.md)) |
| TikTok (adapter disabled) | `<P>_TIKTOK_ACCESS_TOKEN` | A user access token, valid 24 hours; ReachWeaver has no refresh flow ([TikTok guide](TIKTOK.md)) |
| YouTube (adapter disabled) | `<P>_ACCESS_TOKEN`, read only when `channel_id` is set | An OAuth access token with `youtube.upload` from your own Google OAuth client; no refresh flow ([YouTube guide](YOUTUBE.md)) |
| Any platform with `"backend": "postiz"` | `<P>_POSTIZ_API_KEY` only | A Postiz API key ([Postiz guide](POSTIZ.md)) |

These suffixes come from `CREDENTIAL_SUFFIXES`, `OPT_IN_CREDENTIAL_SUFFIXES` and `BACKEND_CREDENTIAL_SUFFIX` in `reachweaver/accounts.py`. The disabled adapters are mock-tested only and refused by the capability registry until the owner records a live receipt; their variable names are reserved now so the `env_prefix` overlap rule and `credentials-audit` cover them. Two credentials belong to no account: `REACHWEAVER_GITHUB_TOKEN` (optional, `sources-scan --online`) and `REACHWEAVER_APPROVAL_SECRET` (optional, [owner approval key](#optional-the-owner-approval-key-7-d7)). No account's `env_prefix` may derive or overlap either.

Platform fields in `accounts.json` (public values only; `load_accounts` validates them):

| Field | Platform | Meaning |
| --- | --- | --- |
| `max_media_attachments`, `image_size_limit`, `media_description_limit` | Mastodon | Optional. Can only lower the defaults (4 images, 16,000,000 bytes, 1500 characters of alt text) |
| `person_id`, `linkedin_version` | LinkedIn | Required: the `sub` from `/v2/userinfo` and the pinned `YYYYMM` API version |
| `scope_check` | LinkedIn (native, not Postiz) | Optional. Only `"introspect"`: verify `w_member_social` before every send ([section 1d](#1d-verify-identity-explicit-online-never-posts)) |
| `authorization_expires_at` | LinkedIn, Instagram, Threads | Optional ISO-8601 time with offset; `doctor` warns before it and an expired value is refused |
| `ig_user_id` | Instagram | Your `user_id`. Without it the account is manual-only metadata; with it only `id`, `platform`, `env_prefix`, `handle`, `ig_user_id` and `authorization_expires_at` are allowed |
| `threads_user_id` | Threads | Your numeric Threads user id. Same rule: only `id`, `platform`, `env_prefix`, `handle`, `threads_user_id` and `authorization_expires_at` once it is set |
| `page_id`, `page_name` | Facebook Page | `page_id` is the numeric `id` from `GET /me` called with the Page token; `page_name` is the Page `name` (compared case-insensitively). Without `page_id` the account is manual-export metadata; with it both are required and only `id`, `platform`, `env_prefix`, `page_id`, `page_name` and the optional `declared_permissions` are allowed |
| `declared_permissions` | Facebook Page | Optional, 1 to 16 permission names you granted. Owner-declared, not verified: `doctor` shows them against the three the Page feed reference requires and they never allow, block or shape a send. Group permissions are refused (removed 2024-04-22, [v19.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version19.0), checked 2026-10-15) |
| `open_id`, `display_name` | TikTok | From `GET /v2/user/info/`. Without `open_id` the account is manual-only metadata; with it only `id`, `platform`, `env_prefix`, `handle`, `open_id` and `display_name` are allowed |
| `channel_id` | YouTube | Your own channel id (`UC` + 22 characters). Without it the account is manual-export metadata and needs no token; with it only `id`, `platform`, `env_prefix`, `handle` and `channel_id` are allowed |
| `backend`, `postiz_base_url`, `postiz_integration_id`, `postiz_api_prefix` | Postiz-backed accounts | See the [Postiz guide](POSTIZ.md) |
| `postiz_workspace` | Postiz-backed accounts | Optional shared-cooldown label ([below](#account-and-environment-separation)) |

### 1b. Provision the credential without exposing it

Pick **one** pattern. Every example keeps the value out of shell history and out of any file the repository tracks.

**Interactive, one session (hidden input).** Run this in a terminal the agent does not drive:

```bash
read -r -s -p 'Mastodon token (hidden): ' REACHWEAVER_MASTODON_TOKEN; printf '\n'
export REACHWEAVER_MASTODON_TOKEN
```

**`pass` (password-store).** `pass insert` reads the secret from standard input with echo off and asks for it twice. `pass show` prints it. ([pass(1)](https://git.zx2c4.com/password-store/about/))

```bash
pass insert reachweaver/my-mastodon/token
REACHWEAVER_MASTODON_TOKEN="$(pass show reachweaver/my-mastodon/token)" \
  reachweaver connect-check --account my-mastodon --online
```

Only the `$(...)` expression reaches history, not the value. The variable is set for that one command only.

**1Password CLI (`op run`).** Keep a file of secret *references*, not secrets, then let `op run` resolve them for one command. ([1Password: load secrets into the environment](https://www.1password.dev/cli/secrets-environment-variables/))

```bash
# reachweaver.op.env  (contains references only; still keep it out of git)
REACHWEAVER_MASTODON_TOKEN="op://<vault>/reachweaver-mastodon/credential"
```

```bash
op run --env-file=./reachweaver.op.env -- reachweaver connect-check --account my-mastodon --online
```

**GNOME Keyring / Secret Service (`secret-tool`).** `secret-tool store` prompts for the secret on a terminal; `lookup` prints it. ([secret-tool(1)](https://manpages.debian.org/testing/libsecret-tools/secret-tool.1.en.html))

```bash
secret-tool store --label='ReachWeaver my-mastodon' service reachweaver account my-mastodon
REACHWEAVER_MASTODON_TOKEN="$(secret-tool lookup service reachweaver account my-mastodon)" \
  reachweaver connect-check --account my-mastodon --online
```

#### Unattended host: systemd `LoadCredential=`

Only for a host that has already passed the [first live pilot](FIRST-LIVE-PILOT.md) and the [worker runbook](WORKER-OPERATIONS.md). This is the recommended pattern for an unattended worker (owner-approved for issue #5 on 2026-10-09). systemd's manual warns that environment variables are **not** a good channel for secrets: they are inherited by every child process, and unit environment settings are visible to unprivileged clients over D-Bus. It recommends `LoadCredential=` instead ([systemd.exec(5)](https://man7.org/linux/man-pages/man5/systemd.exec.5.html), ENVIRONMENT and CREDENTIALS sections).

`LoadCredential=ID:PATH` makes systemd read `PATH` when the unit starts and expose its contents as the file `$CREDENTIALS_DIRECTORY/ID`, readable by the unit's user only, and it sets `$CREDENTIALS_DIRECTORY` for the service ([systemd.exec(5)](https://man7.org/linux/man-pages/man5/systemd.exec.5.html), CREDENTIALS). ReachWeaver uses the **variable name as the credential ID**, so `REACHWEAVER_MASTODON_TOKEN` is read from `$CREDENTIALS_DIRECTORY/REACHWEAVER_MASTODON_TOKEN`.

1. Store each secret as its own root-owned file. Run as root on that host, with a root editor, never with `echo` or a heredoc that lands in history:

   ```bash
   install -d -m 0700 -o root -g root /etc/reachweaver/credentials
   install -m 0600 -o root -g root /dev/null /etc/reachweaver/credentials/REACHWEAVER_MASTODON_TOKEN
   sudoedit /etc/reachweaver/credentials/REACHWEAVER_MASTODON_TOKEN   # the token only; one trailing newline is fine
   ```

2. Add a drop-in such as `systemctl edit reachweaver-publish.service`. One `LoadCredential=` line per variable that any configured account reads (`doctor` lists the names):

   ```ini
   [Service]
   LoadCredential=REACHWEAVER_MASTODON_TOKEN:/etc/reachweaver/credentials/REACHWEAVER_MASTODON_TOKEN
   LoadCredential=REACHWEAVER_BSKY_IDENTIFIER:/etc/reachweaver/credentials/REACHWEAVER_BSKY_IDENTIFIER
   LoadCredential=REACHWEAVER_BSKY_APP_PASSWORD:/etc/reachweaver/credentials/REACHWEAVER_BSKY_APP_PASSWORD
   # Not secrets. Production host only; never set REACHWEAVER_ENV=prod on a dev or staging machine.
   Environment=REACHWEAVER_ENV=prod
   Environment=REACHWEAVER_ALLOW_LIVE=1
   LimitCORE=0
   UMask=0077
   ```

3. `systemctl daemon-reload`, then run the unit once with `publish` **without** `--live` and check the `Live gate:` line of the dry run: it reports `credential source credentials_directory`. `doctor` and `health` report the same, and `credentials-audit` audits the files instead of the environment.

What ReachWeaver checks when `$CREDENTIALS_DIRECTORY` is set (`reachweaver/accounts.py:credential`):

- The path must be absolute, and the directory must not be group- or world-writable.
- Each file is opened with `O_NOFOLLOW` (a symlink is refused) and `O_NONBLOCK` (a FIFO cannot hang the run), must be a regular file of at most 64 KiB and valid UTF-8. Exactly one trailing line ending (`\n`, or `\r\n` from a file saved on Windows) is stripped; nothing else is changed.
- A value that still contains a control character (CR, LF, NUL, tab, any other C0 control or DEL) is refused with `credential NAME contains control characters`, from a file **and** from an environment variable. Such a value would break the HTTP header or URL it is placed in, and the HTTP library's own error would quote it. Rewrite the file or variable with the bare value (for example `printf '%s' "$TOKEN" > FILE`).
- A missing file means the credential is missing (`missing_credentials: NAME`, the post is `SKIPPED`). **Environment variables are never consulted while `$CREDENTIALS_DIRECTORY` is set**, so every credential the accounts need, including `REACHWEAVER_GITHUB_TOKEN` for `sources-scan --online`, must be loaded with `LoadCredential=`. `credentials-audit` reports an exported variable that is shadowed this way as `env_credential_shadowed`.
- Errors name the variable only, never the value or a path. Values read from files are redacted from logs and output exactly like secret environment variables (`privacy.redact`).

`LimitCORE=0` stops a crash from writing a core file. For manual runs, `ulimit -c 0` in the live terminal does the same. `LoadCredentialEncrypted=` (credentials encrypted with `systemd-creds`) delivers the decrypted value through the same directory, so ReachWeaver reads it the same way; this repository has not tested it.

#### Optional: the owner approval key (#7 D7)

**Owner-approved 2026-10-09 (proposal); off until you provide the key.** `REACHWEAVER_APPROVAL_SECRET` is an HMAC key that signs every approval. With it, a database row edited or forged by anything that does not hold the key is refused before any claim. It is read through the same accessor as every credential (environment, or `$CREDENTIALS_DIRECTORY/REACHWEAVER_APPROVAL_SECRET` under `LoadCredential=`), must be at least 32 characters (RFC 2104 §3) and is never printed or stored.

1. Generate it in a terminal the agent does not drive, straight into a root-owned file: `install -m 0600 -o root -g root /dev/null /etc/reachweaver/credentials/REACHWEAVER_APPROVAL_SECRET`, then `python3 -c 'import secrets; print(secrets.token_hex(32))' | sudo tee /etc/reachweaver/credentials/REACHWEAVER_APPROVAL_SECRET >/dev/null`.
2. Worker: add `LoadCredential=REACHWEAVER_APPROVAL_SECRET:/etc/reachweaver/credentials/REACHWEAVER_APPROVAL_SECRET` to the drop-in above.
3. Owner `approve`: provide the same value only to that one command, for example `read -rs REACHWEAVER_APPROVAL_SECRET && export REACHWEAVER_APPROVAL_SECRET` in your own terminal, then `unset` it afterwards. Never put it in a shell profile, a file the agent can read, or a chat.
4. Run `reachweaver approval-mode --require-key` once with the key present. From then on, any process without a usable key refuses to approve, schedule or claim (`approval_key_unavailable`), and `health` reports `blocked`.
5. Approvals made before the key existed are refused with `approval_mac_missing_reapproval_required` and listed by `health`. Re-approve each one: `unapprove`, `show`, `approve`.

The key is a real boundary only if the agent cannot read it. If it sits in your user's environment or a file your user can read, an agent running as you can sign approvals too. Rotating the key requires re-approving every queued post. To leave keyed mode: remove the key everywhere, then run `DELETE FROM settings WHERE name='approval_binding_mode'` by hand. No command removes the latch.

**Fallback: `EnvironmentFile` with mode 0600.** On a host without `LoadCredential=` support, a root-owned 0600 `EnvironmentFile=/etc/reachweaver/credentials.env` (newline-separated `KEY=value` lines) at least keeps the value out of the unit file itself, which is often world-readable. It is still an environment channel with the weaknesses above. Never put a secret in an `Environment=` line.

### 1c. Audit hygiene offline

```bash
reachweaver credentials-audit                         # names only; exit 1 on findings
reachweaver credentials-audit --history ~/.bash_history --history ~/.zsh_history
reachweaver credentials-audit --json
```

It reads every credential through the same accessor as the adapters, so with `$CREDENTIALS_DIRECTORY` set it audits the credential files instead of the environment. It reports `credential_source` (`environment` or `credentials_directory`) and `REACHWEAVER_ENV` (an allowlisted value, `unset` or `unrecognized`). It never prints a value, a hash of one or a credentials path, never opens the state database and never uses the network. Missing credentials are reported as status, not findings, because offline dry runs need none. Findings:

| Code | Meaning | Fix |
| --- | --- | --- |
| `placeholder_value` | An account variable is empty, whitespace or a template such as `changeme`, `xxx`, `<token>` | Set the real value or unset it |
| `orphan_credential` | A `REACHWEAVER_*` credential variable (or credential file) is set but no configured account reads it | Unset or delete it, and revoke it if it is no longer needed |
| `credential_value_reused` | Two variables hold the same secret | Create a separate credential per account |
| `shared_credential_variable` | Two accounts read the same variable name (same or overlapping `env_prefix`) | Give each account its own `env_prefix`. `load_accounts` refuses such a config, so this fires only for raw account lists |
| `reserved_credential_variable` | An account's `env_prefix` derives or overlaps a credential no account owns, such as `REACHWEAVER_GITHUB_TOKEN` | Choose another `env_prefix`. `load_accounts` refuses such a config, so this fires only for raw account lists |
| `overlapping_env_prefix` | One account's credential variable name begins another's | As above; `load_accounts` refuses it |
| `unsafe_credential_file` | A file in `$CREDENTIALS_DIRECTORY` is a symlink, not a regular file, larger than 64 KiB or not UTF-8, or a credential (file or environment variable) contains a control character such as a stray `\r` | Recreate it with `LoadCredential=`, or re-export the bare value; it is treated as missing |
| `unsafe_credentials_directory` | `$CREDENTIALS_DIRECTORY` is relative, unreadable or group-/world-writable | Use the directory systemd provides; nothing is read from an unsafe one |
| `env_credential_shadowed` | A credential is also exported as an environment variable while `$CREDENTIALS_DIRECTORY` is set (it is ignored) | Unset it so the secret lives in one place |
| `shell_history_literal` | History lines (numbers shown) assign a literal secret | Revoke and rotate that credential, then delete those lines |
| `history_file_not_private` | The history file is readable by group or others | `chmod 600` it |

`REACHWEAVER_GITHUB_TOKEN` (used only by `sources-scan --online`) is listed as a non-account credential, not as an orphan.

### 1d. Verify identity (explicit, online, never posts)

With the owner present, in the credential terminal:

```bash
reachweaver connect-check --account my-mastodon --online
```

It prints the account id, platform, `identity` and `write_permission`, and nothing else. For Bluesky it first reserves one `createSession` call in the local counter of the state database (proposal), so `--db` applies. Repeated checks stop with `bluesky_create_session_budget_exhausted`, before any request, at half of Bluesky's documented 30 per 5 minutes or 300 per day. Bluesky authenticates a session and compares the handle. Mastodon reads `verify_credentials` and compares the local username. Telegram checks the bot username, the channel and the bot's posting right. A mismatch exits non-zero with `identity_mismatch`. `write_permission: unverified` on Bluesky and Mastodon is expected: an identity check cannot prove posting scope. LinkedIn also reports `unverified` unless the account opts in with `"scope_check": "introspect"` and the owner exports the app's `<prefix>_CLIENT_ID` and `<prefix>_CLIENT_SECRET`. Then LinkedIn's read-only token introspection must show an active member token with `w_member_social`, and the result is `verified`. This opt-in was owner-approved 2026-10-09 after a security review, because it stores the app client secret on the publishing host ([LinkedIn guide, section 3a](LINKEDIN.md#3a-optional-verify-w_member_social-before-every-send), proposal).

### 1e. Optional: bind the verified identity to each approval (wave 9 item 2)

**Owner-approved 2026-10-10 (proposal); off by default.** `connect-check` proves the credentials match the account entry now. To also prove, at send time, that the entry has not been re-pointed since you approved, approve with:

```bash
reachweaver approve POST_ID --verify-identity --online
```

This runs the same read-only identity request as `connect-check` once (a Bluesky run reserves one `createSession` call, so the same budget applies). On success it stores a SHA-256 fingerprint of the account id, platform and identity fields (Bluesky handle and service, Mastodon handle and server, Telegram bot, chat and channel, LinkedIn `person_id`, and so on). It stores no token and no remote response, and the fingerprint is bound into the approval hash under `approval/id-1`. If the check fails, nothing is approved and the post stays a draft. At `publish --live` the fingerprint is recomputed from the current accounts file; a difference blocks the post before any request with `approval_identity_mismatch_reapproval_required`, and you re-approve it after review.

To make this mandatory, run `reachweaver approval-mode --require-identity` (tighten-only; see the [upgrade note](WORKER-OPERATIONS.md#upgrade-note-verified-identity-binding-wave-9-item-2) for what that does to existing approvals). To leave the mode, run `DELETE FROM settings WHERE name='approval_identity_mode'` by hand; no command removes the latch. This binds **which account** was verified, not **who approved**: `--approver` is still attribution only, and Postiz-backed accounts cannot be bound.

## 2. Status

```bash
reachweaver doctor                # missing variable names; live opt-in on/off; REACHWEAVER_ENV; credential source
reachweaver credentials-audit     # hygiene findings, names only
reachweaver queue-health          # sending / needs_review / overdue items; offline
reachweaver privacy-status        # DB permissions and secret env var NAMES present
reachweaver connect-check --account ID --online   # only when you want a fresh remote identity check
```

`publish --live` repeats the identity check before every claim. If a token has expired or been revoked, the provider answers with an error such as `http_401`. The CLI then prints `BLOCKED ... no post sent`, exits 2 for `--post-id`, and leaves the post `approved` with no send claim recorded. If a credential is missing, the post is `SKIPPED` before any network use. `tests/test_credential_hygiene.py` covers both cases with sockets blocked.

## 3. Disconnect

1. `reachweaver pause` (emergency stop: no new claims).
2. `reachweaver queue-health`. Reconcile every `sending` or `needs_review` post against the native account before going further (`reconcile POST_ID --published|--not-published`).
3. `reachweaver disconnect --account ID` (draft, #38) records that this installation must not publish to the account any more: every later claim is refused (`account_disconnected`, the post stays approved), `publish` skips it before any credential check or request, and `connect-check` shows `local_state: disconnected`. It prints the credential variable **names** the account reads (never values) and the steps below. It revokes nothing by itself and deletes nothing; later `reconnect --account ID` lifts it, and only from an interactive terminal after you type a fresh challenge.
3b. **Revoke the credential at the provider** (next section). Unsetting a variable locally does not revoke anything.
4. Remove the value from its store: `unset` it in the terminal, `pass rm`, delete the 1Password item or the `secret-tool` entry, or delete the file under `/etc/reachweaver/credentials/` and its `LoadCredential=` line (or the line in `/etc/reachweaver/credentials.env`).
5. Remove the account from `config/accounts.json`. Run `reachweaver credentials-audit` and confirm there are no orphans.
6. Export or purge local data as your retention policy requires ([privacy lifecycle](PRIVACY-LIFECYCLE.md)). `reachweaver resume` only when other accounts should continue.

## Revoking tokens at each provider

Provider interfaces change. Check the official page before relying on a path.

- **Bluesky app passwords.** Open [bsky.app/settings/app-passwords](https://bsky.app/settings/app-passwords) while signed in and delete the app password by name. Menu labels have moved between app versions; the direct URL is the stable entry point. The protocol method behind it is `com.atproto.server.revokeAppPassword` ("Revoke an App Password by name", [lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/server/revokeAppPassword.json)). Deleting one app password does not affect your main login or other app passwords.
- **Mastodon.**
  - A token you created under **Preferences → Development**: open `https://<your-instance>/settings/applications`, then delete the application or use *Regenerate access token*. Both routes exist in Mastodon's source ([config/routes/settings.rb](https://github.com/mastodon/mastodon/blob/main/config/routes/settings.rb)).
  - A token granted to a third-party app through OAuth: open `https://<your-instance>/oauth/authorized_applications` and revoke it (Doorkeeper route in [config/routes.rb](https://github.com/mastodon/mastodon/blob/main/config/routes.rb)).
  - Programmatically, `POST /oauth/revoke` with `client_id`, `client_secret` and `token` invalidates a token ([Mastodon OAuth methods](https://docs.joinmastodon.org/methods/oauth/)).
- **Telegram bot tokens.** In a chat with [@BotFather](https://t.me/BotFather), revoke the token for your bot. Telegram documents that a token "can also be revoked at any time via @BotFather" ([bot tutorial](https://core.telegram.org/bots/tutorial)) and that a compromised token is replaced with a new one through BotFather's `/token` command ([bot features](https://core.telegram.org/bots/features)). Telegram's docs do not name the revoke command. BotFather's own command list (type `/` in the chat) is authoritative; it is commonly `/revoke`. The old token stops working, so update the stored value afterwards.
- **GitHub (`REACHWEAVER_GITHUB_TOKEN`).** Delete the token under GitHub **Settings → Developer settings → Personal access tokens**.
- **LinkedIn.** Remove your developer app from the services you have permitted in your LinkedIn account's privacy settings. The member token otherwise stays valid until its 60-day expiry. If you opted in to `scope_check`, also reset the app's client secret in the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps). See the [LinkedIn guide](LINKEDIN.md).
- **Meta (Instagram, Threads, Facebook Page).** Remove your Meta app from the apps and websites connected to that Instagram or Threads account, in the account's settings. The long-lived Instagram and Threads tokens otherwise stay valid for up to 60 days. For a Facebook Page, remove your Meta app from the apps and integrations connected to the Facebook account that generated the Page token, in that account's settings (menu names change). This matters more than for the other two: Meta documents that long-lived Page tokens "do not have an expiration date" and only "expire or are invalidated under certain conditions" ([long-lived tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/), re-read 2026-10-15), so an unrevoked Page token does not time out. See the [Instagram](INSTAGRAM.md), [Threads](THREADS.md) and [Facebook](FACEBOOK.md) guides.
- **Google (YouTube).** Remove your OAuth client's access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) (Google Account, third-party connections). See the [YouTube guide](YOUTUBE.md).
- **TikTok.** Remove your app from the apps connected to your TikTok account in the TikTok app's settings. The access token expires after 24 hours, but the refresh token issued at consent lasts 365 days. See the [TikTok guide](TIKTOK.md).

If a credential leaked, revoke it **first**, then run `reachweaver pause`, then clean up locally.

## Account and environment separation

- **One `env_prefix` per account, per brand, per environment, enforced (issue #5).** For example `REACHWEAVER_ACME_BSKY` and `REACHWEAVER_ACME_STAGING_BSKY`, never a shared prefix. `load_accounts` fails closed, naming the two account ids, when two accounts have the same `env_prefix`, or when any credential variable one account could read (`<prefix>` + `_IDENTIFIER`, `_APP_PASSWORD`, `_TOKEN`, `_BOT_TOKEN`, `_ACCESS_TOKEN`, `_CLIENT_ID`, `_CLIENT_SECRET` or `_POSTIZ_API_KEY`, whatever its platform, and whether or not the account uses LinkedIn's opt-in scope check) equals or is a string prefix of one the other could read. Example: a Mastodon prefix `REACHWEAVER_X_BOT` reads `REACHWEAVER_X_BOT_TOKEN`, which is the Telegram token for prefix `REACHWEAVER_X`, so that pair is refused. Every suffix counts, so switching an account's platform or backend can never make two loaded accounts collide. This also applies to Postiz: two Postiz-backed accounts that use the same Postiz API key still need two prefixes, so store that key once per account prefix (`credentials-audit` then reports `credential_value_reused`, which is expected in that case).
- **Postiz accounts that share one Postiz organization (owner-approved 2026-10-09).** Add the same `"postiz_workspace": "<slug>"` (lowercase `a-z`, `0-9`, `-`, 1–63 characters; only with `"backend": "postiz"`) to each of them. A 429 on one then pauses all of them, because Postiz counts create-post requests per organization ([POSTIZ.md](POSTIZ.md)). The value is a label you choose, never the key. Leave it out and cooldowns stay per account. A wrong or missing label never causes a post. At worst you get today's behaviour, more Postiz 429s.
- **One accounts file and one state database per environment.** Use the existing `--accounts` and `--db` flags (or `REACHWEAVER_ACCOUNTS` and `REACHWEAVER_DB`). For example `config/accounts.staging.json` with `state/staging.sqlite`, and a separate production file and database on the trusted host only.
- **One credential store entry per account.** Never copy a token between brands; `credentials-audit` reports reuse as `credential_value_reused`.
- **`REACHWEAVER_ENV` (enforced since issue #5, owner-approved 2026-10-09).** One of `dev`, `staging` or `prod`. `publish --live` refuses unless it is exactly `prod`, in addition to `--live` and `REACHWEAVER_ALLOW_LIVE=1`, for native adapters and the Postiz backend alike. The check runs before any account is loaded, any credential is read or any request is made (`reachweaver/policy.py:require_live_gate`). **Set `REACHWEAVER_ENV=prod` only on the production host**, in its unit or its owner terminal, never in a shell the agent drives and never on a dev or staging machine. `doctor`, `health`, `privacy-status`, `credentials-audit` and the `publish` dry run show the value; any value other than `dev`, `staging` or `prod` is shown as `unrecognized` and never echoed.
