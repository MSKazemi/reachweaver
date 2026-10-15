# Troubleshooting

Problems a new user meets between `git clone` and a first safe preview, with the exact message ReachWeaver prints and the fix. Every command named here is offline unless it carries `--online` or `--live`. Start with the [quickstart](QUICKSTART.md); for anything security-related use the [security checklist](SECURITY-CHECKLIST.md).

**Rule of thumb:** a refusal is usually a safety gate working as designed. Do not look for a flag that bypasses it. None exists for approval, the dry-run default, the emergency pause, credential isolation or ambiguous-send reconciliation.

## Setup

| Symptom | Cause | Fix |
|---|---|---|
| `python: command not found` | Only `python3` is on `PATH` | Use `python3 -m reachweaver ...` (no install needed) or `python3 -m pip install -e .` for a `reachweaver` command |
| Start-up errors under an older interpreter | `pyproject.toml` requires Python 3.11+ (`scripts/release_gate.py` also uses the 3.11 `tomllib` module) | Install Python 3.11 or newer; check with `python3 --version` |
| `No module named reachweaver` | The command ran outside the repository root and the package is not installed | `cd` to the repository root, or `python3 -m pip install -e .` |
| `ERROR: Missing config/accounts.json. Copy config/accounts.example.json and customize it (no secrets).` | No `--accounts` was given and the default local file does not exist | For a preview, pass `--accounts config/accounts.example.json`. For real use, `cp config/accounts.example.json config/accounts.json` (git ignores it) and edit public metadata only |
| `ERROR: Never put tokens, passwords or sessions in accounts.json. Use environment variables.` | A secret-like key (token, password, session, cookie, API key) is in the accounts file | Remove it. Credentials are read from environment variables at send time only; see [account onboarding](ACCOUNT-ONBOARDING.md) |
| `ERROR: State directory is not private; restrict it with chmod 700 before running` | The database directory is readable by group or others, for example `/tmp` itself or a hand-made `state/` | Point `--db` at a path inside a new directory (`state/` is created with mode 0700), or run `chmod 700 state`. For throwaway runs use `RW_TMP=$(mktemp -d)` and `--db "$RW_TMP/x.sqlite"` |
| `ERROR: [Errno 13] Permission denied: ...` on `--db` | The parent directory cannot be created | Choose a writable path you own |

## Accounts and credentials

| Symptom | Cause | Fix |
|---|---|---|
| `doctor` prints `env variables missing: REACHWEAVER_...` | No credentials are set. Expected for a preview | Nothing to fix for offline work. For a live pilot, provision them privately as in [account onboarding](ACCOUNT-ONBOARDING.md); never paste them into a chat, issue or file in the repository |
| `health` prints `OVERALL: BLOCKED` with `credentials_missing` and exits 1 | Same as above, seen per account | Expected before onboarding. `health` is local only and never contacts a provider |
| `ERROR: credential REACHWEAVER_..._TOKEN contains control characters` | The value has a carriage return, an extra newline, a NUL or another control character, typically a credential file saved with Windows line endings plus a blank line, or `export X="$(cat file)"` from such a file. Only one trailing `\n` or `\r\n` of a `$CREDENTIALS_DIRECTORY` file is stripped | Rewrite the file or variable with the bare value (`printf '%s' "$TOKEN" > FILE`, or `dos2unix FILE`). Nothing was sent; the message names the variable only |
| `credentials-audit` exits 1 | A placeholder, orphaned, reused or colliding credential variable, or a literal secret in a history file you named | Follow the finding; names and line numbers are printed, never values ([SECURITY.md](SECURITY.md#credential-hygiene-audit-issue-5-slice)) |
| `ERROR: connect-check requires explicit --online. Offline 'doctor' does not use network.` | Identity checks contact the provider, so they need an explicit flag | Run it only on your own machine after credentials are set; it authenticates and never posts |

## Drafts, approval and preview

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR: Unknown account. Run 'doctor' to list configured accounts` | The `--account` id is not in the accounts file you passed | Use an id that `doctor` lists, for example `my-bluesky` in the example file |
| `check` prints problems, or `draft` is refused | Conservative text lint: length, placeholders such as `TODO` or `{{write: ...}}`, or an opted-out mention | Edit the text; limits are deliberately below some provider caps ([PLATFORMS.md](PLATFORMS.md)) |
| `ERROR: Selected post is not approved and due, or does not exist. No send attempted.` | `publish --post-id` previews only posts that are approved **and due**. A post scheduled in the future is not due yet | Preview before you `schedule`, or wait for the scheduled time. `list` shows each post's status and time |
| `health` or `queue-health` lists `approval_rebind_required` | The post changed after approval, or was approved before payload binding existed | `unapprove POST_ID`, `show POST_ID`, `approve POST_ID` (then `schedule` again). There is no in-place rebind ([upgrade note](WORKER-OPERATIONS.md#upgrade-note-approval-payload-binding-issue-7)) |
| `ERROR: human_confirmation_requires_tty: human confirmation is enabled and needs an interactive terminal on stdin and stdout; nothing was approved or sent` | The optional #7 D6 check is on (`approval-mode --confirmation tty_hash_prefix`). `approve` of a draft and every `publish --live` then need stdin and stdout to be a terminal, so a script, a pipe or an agent subprocess is refused. While it is on, an unattended worker cannot send | Run the command yourself in an interactive terminal. `approval-mode` with no options shows whether it is on. Turning it off (`approval-mode --confirmation off`) needs the same typed confirmation |
| `ERROR: human_confirmation_tty_unavailable: cannot open /dev/tty (no controlling terminal); ...` | The D6 answer is read from `/dev/tty`, never from stdin, and this process has no controlling terminal (for example a systemd unit or cron job) | Same: run it in your own terminal session |
| `ERROR: human_confirmation_aborted: end of input on /dev/tty; ...` or `ERROR: human_confirmation_failed: 3 attempts did not match; ...` | End of input (Ctrl-D) at the prompt, or three wrong answers. Nothing was approved or sent | Run it again and type the first 8 characters of the `payload sha256` line printed just above the prompt |
| `approval_key_unavailable: keyed approval mode is on but this process has no usable REACHWEAVER_APPROVAL_SECRET ...` from `approve`, `schedule` or `publish --live`, or `health` blocked with `approval_key_unavailable` | The #7 D7 keyed binding is latched (`approval-mode --require-key`), or `REACHWEAVER_APPROVAL_SECRET` is set but unusable (for example shorter than 32 characters; the message says why) | Provide the key to that one process only, as in [account onboarding](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7). Never put it where the agent can read it. No command removes the latch |
| `approval_mac_missing_reapproval_required` (from `publish --live`, or in `health`/`queue-health` under `approval_rebind_required`) | Keyed mode is on and the post was approved without the key, typically before the key existed | `unapprove POST_ID`, `show POST_ID`, then `approve POST_ID` with the key present |
| `approval_mac_mismatch_reapproval_required` | The approval tag does not verify with this key: the database row was edited, forged or copied, or the key was rotated | Do not send it. Find out what changed the database, then re-approve after review (`unapprove`, `show`, `approve`). Rotating the key requires re-approving every queued post |
| `approval_media_changed_reapproval_required` (dry run `BLOCKED`, `approve` refused, or `publish --live`) | An attached image (a local Mastodon file, or the image at an Instagram URL) is missing or no longer holds the bytes recorded at `media-attach` | Restore the original file or image, or re-attach and re-approve: `unapprove`, `media-detach`, `media-attach`, `show`, `approve` (the message lists the exact commands) |
| `approval_video_changed_reapproval_required` | The video file of a TikTok or YouTube draft is missing or changed since it was attached | Restore the file, or `unapprove`, `video-detach`/`youtube-detach`, `video-attach`/`youtube-attach`, `show`, `approve` |
| `campaign-check` exits 2 | Errors in the campaign file: schema, evidence, brand-to-account allowlist, placeholders or media format | Fix every `ERROR` line. `WARNING` lines need a person's judgment but do not block |
| `campaign-check`/`claims-check` reports an ungrounded number or release claim | The cited evidence does not lexically contain it | Add the exact source excerpt or remove the claim ([claim grounding](CLAIM-GROUNDING.md)) |
| `sources-scan --online` prints `ERROR SOURCE_ID: repository_not_confirmed_public` and exits 1 | GitHub's metadata says the repository is private (or not public), and the source does not carry all three private opt-ins. Only the metadata request was made; nothing from it is emitted or stored | Remove the source, or, only for a repository you mean to read privately, set `"visibility": "private"` and `"sensitivity": "private"` on it and pass `--allow-private` with `REACHWEAVER_GITHUB_TOKEN` set. Its signals are then marked private and are never public evidence ([sources](SOURCES.md)) |
| `draft`, `media-attach`, `youtube-attach` or `approve` refuses, or `check` prints `BLOCKED:`, with `private_source_link` | With `--sources CONFIG`, the text, alt text, video title or tag, hosted image URL or source URL links into a `visibility: private` repository of that config (#9) | Remove the link or replace it with a public one. Private repository URLs are never public links. The check runs **only** when `--sources CONFIG` is passed: without it these commands cannot detect a private link, so pass it whenever a private source is configured |

## Publishing and recovery

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR: Live publishing requires both --live and REACHWEAVER_ALLOW_LIVE=1 ...` | The live opt-in is missing. This is the default and it is safe | Stay in dry run until the [security checklist](SECURITY-CHECKLIST.md) and the [first live pilot](FIRST-LIVE-PILOT.md) steps are done |
| `ERROR: Live publishing requires REACHWEAVER_ENV=prod as well as ... (REACHWEAVER_ENV is unset)` | Since issue #5 a live send also needs `REACHWEAVER_ENV=prod`. Anything else (`unset`, `dev`, `staging`, or a value shown as `unrecognized`) refuses before any account is loaded | Set `REACHWEAVER_ENV=prod` only on the production host, in the terminal or unit the agent does not drive. Dev and staging machines keep `dev`/`staging` and cannot send |
| `ERROR: Accounts A and B have overlapping credential env_prefix (...)` | Two accounts would read the same credential variable, or one variable name begins another's (for example `REACHWEAVER_X` + `_BOT_TOKEN` and `REACHWEAVER_X_BOT` + `_TOKEN`) | Give every account its own `env_prefix` that is not a prefix of another account's variable names ([onboarding](ACCOUNT-ONBOARDING.md#account-and-environment-separation)) |
| `ERROR: Account A has an env_prefix whose credential variables collide with a credential no account owns (...)` | The prefix derives or overlaps a credential ReachWeaver reads outside any account, such as `REACHWEAVER_GITHUB_TOKEN` for `sources-scan --online` (a Mastodon prefix `REACHWEAVER_GITHUB` would read it) | Choose an `env_prefix` that does not derive or overlap that name |
| `ERROR: CREDENTIALS_DIRECTORY ...` or `credential NAME is a symlink / not a regular file / larger than 65536 bytes / not valid UTF-8` | `$CREDENTIALS_DIRECTORY` is set but unsafe or the credential file is unusable. ReachWeaver then does not fall back to environment variables | Use systemd `LoadCredential=` ([onboarding](ACCOUNT-ONBOARDING.md#unattended-host-systemd-loadcredential)), or unset `CREDENTIALS_DIRECTORY` to use environment variables |
| `Emergency pause is active` | `reachweaver pause` was run | Investigate first, then `reachweaver resume` |
| `<platform> <format> is <status>; publish refused (fail closed)` or `No ReachWeaver live adapter for ...; use manual export` | The capability registry does not list a live adapter for that platform and format. This includes the mock-tested Instagram, Threads, TikTok and YouTube adapters and Mastodon image attachments, which stay disabled until the owner records a live receipt | Use the alternative in the table below |
| `publish` prints `Provider cooldown: account A until T ...` and `SKIPPED POST_ID: account_provider_cooldown_active until=... account=... source=...` | The provider answered 429 for that account. With `postiz_workspace`, a 429 on any account of the same Postiz workspace pauses all of them (`scope=postiz-workspace:<slug> recorded_by=<account>`). The post that got the 429 is in `needs_review` | Nothing to fix. The skipped posts stay approved or scheduled and become eligible again after `until`; nothing is resent. Reconcile the `needs_review` post by hand ([provider cooldowns](WORKER-OPERATIONS.md#provider-rate-limits-and-cooldowns-issue-11)) |
| `SKIPPED POST_ID: bluesky_create_session_budget_exhausted window=...` (or the same `ERROR:` from `connect-check`) | ReachWeaver's local count of Bluesky `createSession` calls in the state database reached half of Bluesky's documented 30 per 5 minutes or 300 per day | Wait for the window to pass. Avoid repeated `connect-check` runs; each live Bluesky send reserves two sessions |
| `HELD POST_ID: reply_capability_gated: ...` in the dry run, or `SKIPPED POST_ID: reply_capability_gated: ...` with `--live` | The post is a reply (`draft --reply-to`). Reply sending (issue #17, owner-approved 2026-10-09) stays off: `reachweaver_reply_adapter` is `none` for every platform in the capability registry until the owner enables it. The reply stays approved and is never sent meanwhile | Post the reply yourself in the native app, then `cancel POST_ID` |
| A post stays in `sending` or `needs_review` | The send outcome is unknown (timeout, crash, ambiguous response, or a Postiz acceptance) | Check the native account by hand, then `reconcile POST_ID --published --remote-id ID` or `reconcile POST_ID --not-published`. Never re-send blindly; `dead-letter` groups these by error code |
| `ERROR: [Errno 17] File exists: ...` from `campaign-export` or `manual-export` | Export folders must be new so a reviewed package is never overwritten | Delete the folder or choose a new `--out` |
| `manual-export covers facebook, instagram, threads, tiktok, youtube; use campaign-export for ...` | `manual-export` has dated platform rules for five platforms only | Use `campaign-export` for the other platforms in the table below |

## Blocked platforms and their alternatives

Every platform without a live ReachWeaver adapter, in both research registries, has exactly one of these alternatives:

- **manual_export**: `reachweaver manual-export` writes a creator package and checklist after checking sourced platform limits.
- **draft_export**: `reachweaver campaign-export` writes text drafts that you post by hand in the native app.
- **native_manual**: a research-only channel with no ReachWeaver platform id, so there is no export. You write and post in the native app; `distribution-plan` lists it.
- **human_only**: ReachWeaver writes **no** text for this venue. You write and post it yourself, following the venue's rules.
- **unsupported**: not a social publishing target for ReachWeaver.

Instagram, Threads, TikTok and YouTube also have a mock-tested ReachWeaver adapter (issues #20, #19, #23, #22, owner-approved 2026-10-09). Each stays disabled in the capability registry until the owner records a live receipt, so `manual-export` remains the working path for them. `doctor` notes this for such accounts.

The table below is generated by `reachweaver/onboarding.py` from the registries. `tests/test_onboarding.py` and `scripts/release_gate.py` fail when a blocked platform has no alternative or when this table is stale. Regenerate it with `python3 scripts/release_gate.py --print-blocker-table`.

<!-- BEGIN blocker-table: generated by reachweaver.onboarding.render_markdown -->
Live text adapters (still need approval, `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`): `bluesky`, `linkedin`, `mastodon`, `telegram`.

**Capability registry** (`reachweaver/platform_registry.json`):

| Platform | Registry status | Alternative | Command | Note |
|---|---|---|---|---|
| DEV Community (`devto`) | manual-export | draft_export | `reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR` | Human-authored articles only (DEV AI rules); use the DEV article brief and DEV's own scheduler |
| Facebook Pages (`facebook`) | requires-review | manual_export | `reachweaver manual-export --platform facebook --variant VARIANT.json --out NEW_DIR` | Pages only; profiles and groups are refused by the export preflight |
| Hashnode (`hashnode`) | manual-export | draft_export | `reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR` | No adapter; native scheduling needs Hashnode Pro; post the article by hand |
| Instagram (`instagram`) | requires-review | manual_export | `reachweaver manual-export --platform instagram --variant VARIANT.json --out NEW_DIR` | Professional account and Meta app review required; owner attaches the media in the app |
| Pinterest (`pinterest`) | requires-review | draft_export | `reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR` | Business account and app access unconfirmed; Pins need owner-supplied media (check it with media-check) |
| Reddit (`reddit`) | blocked | human_only | none | Human-authored and human-posted only; native scheduling is moderator-only. ReachWeaver writes no Reddit text |
| Threads (`threads`) | requires-review | manual_export | `reachweaver manual-export --platform threads --variant VARIANT.json --out NEW_DIR` | Threads API needs Meta app review; creator package with 500-character and 5-link checks |
| TikTok (`tiktok`) | requires-review | manual_export | `reachweaver manual-export --platform tiktok --variant VARIANT.json --out NEW_DIR` | Content Posting API audit required; owner posts and decides privacy and labels |
| X (`x`) | requires-review | draft_export | `reachweaver campaign-export --file CAMPAIGN.json --brand BRAND.json --out NEW_DIR` | No adapter until the owner records a cost ceiling (PLATFORMS.md go/no-go); post the exported text in the X app |
| YouTube (`youtube`) | requires-review | manual_export | `reachweaver manual-export --platform youtube --variant VARIANT.json --out NEW_DIR` | API audit required for public uploads; owner uploads in YouTube Studio and decides privacy and disclosures |

**Distribution registry** (`reachweaver/distribution_registry.json`, channels without a live adapter):

| Alternative | Channels |
|---|---|
| manual_export | `facebook_pages`, `instagram`, `threads`, `tiktok`, `youtube` |
| draft_export | `dev`, `hashnode`, `pinterest`, `x` |
| native_manual | `beehiiv`, `bilibili`, `chingari`, `csdn`, `dailydev`, `douyin`, `dzen`, `ghost`, `github_discussions`, `hipi`, `josh`, `juejin`, `kakao_talk_channel`, `kuaishou`, `kwai`, `line_official_account`, `medium`, `moj`, `naver_blog`, `note_jp`, `odnoklassniki`, `oschina`, `peerlist`, `pratilipi`, `product_hunt`, `public_app`, `qiita`, `rooter`, `sharechat`, `snapchat`, `substack`, `toutiao`, `viber`, `vimeo`, `vk`, `wechat`, `wechat_channels`, `weibo`, `xiaohongshu`, `zenn`, `zhihu` |
| human_only | `arattai_channels`, `baidu_tieba`, `behance`, `dcard`, `discord`, `douban`, `dribbble`, `facebook_groups`, `flipboard`, `habr`, `hacker_news`, `huggingface`, `indie_hackers`, `kakao_openchat`, `kaskus`, `kutumb`, `lemmy`, `line_openchat`, `lobsters`, `localcircles`, `maimai`, `naver_cafe`, `pantip`, `ptt`, `quora`, `qzone`, `reddit`, `researchgate`, `slack_communities`, `stackexchange`, `tumblr`, `twitch`, `whatsapp`, `xing`, `zalo_official_account` |
| unsupported | `gitee`, `github`, `snapcraft` |
<!-- END blocker-table -->

A registry entry is research, not permission. An alternative here does not mean the platform allows a given post: check its rules, your account type and the [platform notes](PLATFORMS.md) first.

## Still stuck

- Run the offline gates: `make check` (or the commands in [CONTRIBUTING.md](../CONTRIBUTING.md)).
- Open an issue with the command, the exact `ERROR:` line and your Python version. Never include credentials, cookies, private drafts or analytics. Report security problems privately ([SECURITY.md](SECURITY.md#reporting-a-vulnerability-private)).

## Install and update problems

- **`404` / "lacks skill.zip"**: no release has been published yet, or the repository is private (release assets need a login). Use `python3 scripts/install_skill.py --from-checkout`.
- **"SHA-256 mismatch"**: the download is not the published bundle. Nothing was installed; retry, and do not use the file.
- **"source checkout or pip install"**: `update --apply` only replaces an installed skill bundle. In a clone, `git pull` and `python3 scripts/sync_skill.py`.
- **Update notice lists Security first or marks `(for you: ...)`**: that is intended. Within each version, Security entries come first, then entries naming a platform from your accounts config (the global `--accounts` path, else `config/accounts.json` in the current folder; `REACHWEAVER_ACCOUNTS` is not consulted, and only the `platform` field is read). A changed accounts file re-reads the release notes once.
- **No update notice**: `~/.cache/reachweaver/no-update-check` exists, you are offline, or the last check was under 24 h ago (cache: `~/.cache/reachweaver/update-check.json`). `update --check` always asks GitHub. The "What changed" list needs one more read of `CHANGELOG.md` at the release tag; if that read fails, the notice is the single first line.
- **Plugin install**: updates go through `/plugin marketplace update reachweaver` and `/plugin update reachweaver@reachweaver`; `update --apply` is refused on purpose. Validate manifests with `claude plugin validate .` (marketplace) and `claude plugin validate plugin` (the plugin folder).
- **Roll back**: the previous bundle is in `~/.cache/reachweaver/previous/`; move it back over `~/.claude/skills/reachweaver`.
