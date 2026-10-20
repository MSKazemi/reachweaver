# Optional Postiz publishing backend (issue #30)

**Status:** offline, mock-tested slice. **No Postiz instance, API key or social account has been connected or live-tested by this repository.** Nothing here is a receipt, a connection claim or permission to publish.

Postiz is a scheduler that holds an owner's platform OAuth connections ("channels", called *integrations* in its API). ReachWeaver treats it as a **backend**, not a platform: the account's `platform` is still `bluesky`, `linkedin` and so on, and ReachWeaver remains the only approval authority. See ADR-0001.

## Verified Postiz facts (checked 2026-10-08)

| Fact | Value | Source |
| --- | --- | --- |
| Cloud API base | `https://api.postiz.com/public/v1` | [OpenAPI `servers`](https://docs.postiz.com/public-api/integrations/list), [API overview](https://docs.postiz.com/public-api/introduction) |
| Self-hosted API base | `https://{your-domain}/api/public/v1` | same OpenAPI `servers` entry |
| Auth | `Authorization: <api-key>` (raw key, no `Bearer` prefix); OAuth tokens start with `pos_` and are sent the same way | [API overview](https://docs.postiz.com/public-api/introduction) |
| List channels | `GET /integrations` returns `[{id, name, identifier, picture, disabled, profile, customer}]` | [List integrations](https://docs.postiz.com/public-api/integrations/list); field mapping confirmed in the public controller, `apps/backend/src/public-api/routes/v1/public.integrations.controller.ts` in [postiz-app](https://github.com/gitroomhq/postiz-app) (read, not copied) |
| Create | `POST /posts` with `type` (`now`/`schedule`/`draft`), `date`, `shortLink`, `tags`, `posts[{integration:{id}, value:[{content, image}], settings:{__type}}]`; response `[{postId, integration}]` | [Create post](https://docs.postiz.com/public-api/posts/create) |
| `type` semantics | `now` publishes immediately (date ignored), `schedule` at `date`, `draft` stores without scheduling | [Create post](https://docs.postiz.com/public-api/posts/create) |
| Read posts | `GET /posts?startDate&endDate[&customer]` returns `{posts:[{id, content, publishDate, releaseURL, state: QUEUE/PUBLISHED/ERROR/DRAFT, error, integration}]}` | [List posts](https://docs.postiz.com/public-api/posts/list) |
| Delete | `DELETE /posts/{id}` deletes every post in the same group | [Delete post](https://docs.postiz.com/public-api/posts/delete) |
| Text-only providers | `bluesky`, `mastodon`, `telegram`, `threads` need only `{"__type": ...}`; `linkedin`/`linkedin-page` and `facebook` take optional settings | provider pages: [Bluesky](https://docs.postiz.com/public-api/providers/bluesky), [LinkedIn](https://docs.postiz.com/public-api/providers/linkedin), [Telegram](https://docs.postiz.com/public-api/providers/telegram), [Facebook](https://docs.postiz.com/public-api/providers/facebook), [Threads](https://docs.postiz.com/public-api/providers/threads) |
| Rate limits | **Inconsistent in the docs.** The overview says 90 requests/hour (100 on cloud), applied only to the create-post endpoint, one global value per instance (`API_LIMIT` on self-host). The OpenAPI description embedded in each endpoint page says 30 requests/hour. ReachWeaver relies on neither; its own budget (default 2 attempts/account/24 h) is far lower | [API overview](https://docs.postiz.com/public-api/introduction) |
| Errors | 400, 401, 403, 404, 413, 429, 5xx documented; "5xx: retry with exponential backoff" | [API overview](https://docs.postiz.com/public-api/introduction) |
| MCP | Exists: `https://mcp.postiz.com/mcp` (Bearer key) or `/mcp/{api-key}` in the URL; 13 tools including `schedulePostTool` | [MCP introduction](https://docs.postiz.com/mcp/introduction), [MCP setup](https://docs.postiz.com/mcp/setup) |
| License | `gitroomhq/postiz-app` is **AGPL-3.0** (GitHub license API and `LICENSE` file); latest release v2.25.0, 2026-10-02 when first checked. Re-checked 2026-10-15: still AGPL-3.0, latest release v2.25.1 (2026-10-11); `gitroomhq/postiz-agent` still reports `NOASSERTION`. See [Licensing and credential review](#licensing-and-credential-review-2026-10-15) | [postiz-app](https://github.com/gitroomhq/postiz-app), [LICENSE](https://github.com/gitroomhq/postiz-app/blob/main/LICENSE) |

**Unknown (not verified; nothing depends on it):** an idempotency key for `POST /posts` (none is documented); whether `id` in `GET /posts` always equals the `postId` returned by create (the lookup reports `found: false` rather than guessing); whether Postiz delivers the `content` byte-identically on every provider; per-integration OAuth scopes or a "refresh needed" flag (not exposed by `GET /integrations`); cloud pricing and plan quotas (the overview says plans tier on channel and posts-per-month quotas only); whether a separate commercial/enterprise edition changes the public API (the repository contains an `enterprise.controller.ts`, but its scope was not examined). Self-hosted deployments that use a different reverse-proxy path than `/api` are not covered.

**MCP is deliberately not used.** The documented URL form embeds the API key in the URL, and an MCP tool hands the agent raw publishing authority, which ADR-0001 rejects. The adapter uses the REST API from the local CLI only; the key never enters a Claude prompt.

**Licensing.** ReachWeaver (Apache-2.0) talks to Postiz over its documented HTTP API and copies no Postiz source code. Running or modifying a Postiz server is the owner's separate AGPL-3.0 decision. This is not legal advice.

## Configuration

Add an entry to your ignored `config/accounts.json`. **No key in this file**:

```json
{
  "id": "my-linkedin-via-postiz",
  "platform": "linkedin",
  "env_prefix": "REACHWEAVER_LI_POSTIZ",
  "handle": "the-integration-profile",
  "backend": "postiz",
  "postiz_base_url": "https://api.postiz.com",
  "postiz_integration_id": "cm4ean69r0003w8w1cdomox9n"
}
```

- `backend` must be exactly `"postiz"`. Accounts without `backend` behave exactly as before. There is no default backend.
- `postiz_base_url` must be a public HTTPS origin (`policy.require_https_public_server`): no path, port, credentials, IP address, `localhost`, `.local` or single-label LAN host. A LAN-only self-hosted instance is **not supported** in this slice.
- `postiz_api_prefix` is optional: `/public/v1` for `api.postiz.com`, otherwise `/api/public/v1` (the documented self-hosted path). Only those two values are accepted. Unknown `postiz_*` keys are rejected.
- `postiz_integration_id` and `handle` must match `GET /integrations` `id` and `profile`. Run `postiz-check` to read them.
- The key is read only from `{env_prefix}_POSTIZ_API_KEY` (printable ASCII, no whitespace) at request time, through `accounts.credential`: an environment variable, or the file `$CREDENTIALS_DIRECTORY/{env_prefix}_POSTIZ_API_KEY` under systemd `LoadCredential=` ([onboarding](ACCOUNT-ONBOARDING.md#unattended-host-systemd-loadcredential)). `doctor` lists the missing variable name, never a value.
- Every account needs its own `env_prefix`, Postiz accounts included: `load_accounts` refuses identical or overlapping prefixes (issue #5). Two ReachWeaver accounts that use the same Postiz API key therefore store it once per prefix; `credentials-audit` reports that as `credential_value_reused`.
- `postiz_workspace` is optional (owner-approved 2026-10-09, proposal). It is a lowercase slug that you choose (`a-z`, `0-9`, `-`, 1–63 characters, for example `"acme"`), and it is accepted only together with `"backend": "postiz"`. Give the same value to every account whose API key belongs to the same Postiz organization. A 429 on any of them then pauses all of them (see "429" below). It is a label, never the key, a URL or a variable name.

Platforms accepted for the backend, and why the rest are refused (`postiz.PROVIDERS`/`EXCLUDED`, also checked against the capability registry at send time):

| ReachWeaver platform | Postiz `identifier` | Decision |
| --- | --- | --- |
| bluesky, mastodon, telegram, threads | same name | allowed |
| linkedin | `linkedin` or `linkedin-page` | allowed; the observed identifier is sent as `__type` |
| facebook | `facebook` | allowed (Page post defaults) |
| reddit | — | **refused**: human-only, moderator-only scheduling |
| x | — | **refused** until the #24 cost ceiling and spend ledger exist |
| instagram, pinterest | — | refused: text-only format `unavailable` |
| youtube, tiktok | — | refused: text format `unknown` (video-only) |
| devto, hashnode | — | refused: manual-export platforms; Postiz also needs title/tags settings |

The send-time gate is stricter than "text is not `unavailable`": it requires registry text `api_direct` or `api_gated` **and** platform status `supported` or `requires-review`. `unknown`, `manual_export`, `blocked` and `manual-export` fail closed. `api_gated` is accepted only because the gating (app review, OAuth consent) is held by the owner's Postiz connection, which the identity preflight confirms exists; it is not proof of scope.

### LinkedIn: `linkedin` versus `linkedin-page` (native and Postiz differ)

- **Native ReachWeaver adapter** (`platform: linkedin`, no Postiz): personal member profile only. Organization (company) Pages are **refused** ([LINKEDIN.md](LINKEDIN.md)): the adapter sends only a member author and never `urn:li:organization:*`.
- **Via Postiz**: `PROVIDERS["linkedin"]` is `("linkedin", "linkedin-page")`, so an integration whose Postiz `identifier` is `linkedin-page` **is accepted** and posts as a company Page, using Postiz's own LinkedIn connection and scopes. The native Page refusal does **not** apply on this path. The identity preflight checks only that the integration id exists, is enabled, has an allowed identifier and a `profile` equal to the configured `handle`.
- The approval gate, dry run, emergency stop, caps and ambiguous-send handling still apply to a Page post. What differs is the author: ReachWeaver cannot verify the Page role or `w_organization_social` scope, because the owner's Postiz connection holds them.
- **Decision pending (not changed here):** refusing `linkedin-page` would be a small validation change in `postiz.PROVIDERS`, but it contradicts `tests/test_postiz.py` (which sends through a `linkedin-page` integration) and the Postiz column of `docs/PLATFORM-MATRIX.md`, so it needs an owner decision and a test change, not a quiet edit. Until then, if you want the native "personal profile only" guarantee, do not connect a LinkedIn Page integration to an account that uses `platform: linkedin`.
- Postiz listing both identifiers was verified 2026-10-08 (see above). The Page-posting behaviour is read from this repository's code and tests, not from a live Postiz instance.

## Flow

```text
approve (exact text) -> [schedule] -> publish --live + REACHWEAVER_ALLOW_LIVE=1 + REACHWEAVER_ENV=prod
  (checked before any account is loaded or key is read, issue #5)
  -> pause check -> opt-out gate (@-mention of a listed handle/domain = BLOCKED, post stays approved)
  -> text lint -> backend capability gate -> key present/well-formed
  -> GET /integrations: configured id exists, provider maps to platform,
     profile == handle, not disabled          (read-only; failure = BLOCKED, post stays approved)
  -> claim_sending (BEGIN IMMEDIATE, rolling budget, pause re-checked)
  -> POST /posts {type: now, one integration, exact approved text, shortLink false}
  -> 200 [{postId, integration == configured}] -> needs_review, remote_id = postiz:<postId>,
     last_error = postiz_accepted_platform_receipt_unverified
  -> any error after the claim -> needs_review (postiz:<sanitized code>); never retried
```

- **Acceptance is not a receipt.** `type: now` only queues a job. The platform may still reject it, Postiz may transform the text, and the native permalink is not in the create response. The post is therefore `needs_review`, not `published`, and `report` does not count it. A human opens the native post, compares the exact text, and runs `reconcile POST_ID --published --remote-id NATIVE_ID --remote-url NATIVE_URL`, or `--not-published` (then delete the Postiz job in Postiz if it exists).
- **Never Postiz `draft`/`schedule`.** ReachWeaver's outbox owns timing; it sends `now` when the approved post is due. A future-dated Postiz job would keep running after `reachweaver pause`. Postiz drafts or approvals are never treated as ReachWeaver approval.
- **Errors** are sanitized codes only, for example `postiz_http_401_unauthorized`, `postiz_http_403_forbidden`, `postiz_http_429_rate_limited`, `postiz_http_500`, `postiz_network_or_timeout_uncertain`, `postiz_unexpected_create_response_uncertain`. Response bodies (which can echo content) and the key never reach output, `last_error` or `event_log`. Redirects are refused, so the `Authorization` header cannot follow one.
- **429 (issue #11).** A 429 records a per-account cooldown: a standard `Retry-After` header if Postiz sends one (not documented), otherwise 15 minutes, clamped to 60 s–24 h. The account's other posts are `SKIPPED` until it ends. A post-claim `POST /posts` 429 stays `needs_review` and is classified `ambiguous`; it is never retried, whatever the Postiz guidance on 5xx says. The read-only `GET /integrations` preflight may be retried twice with jitter before the claim. Without `postiz_workspace` the cooldown is per ReachWeaver account. With it, the 429 is recorded for the whole workspace: `scope=postiz-workspace:<slug>`. Every other account that declares the same workspace is then `SKIPPED ... account_provider_cooldown_active ... scope=postiz-workspace:<slug> recorded_by=<account>` in the dry run, before its identity preflight, and again inside `claim_sending`. The reason: Postiz counts create-post requests per **organization**. The docs' 90/h (100 cloud) is one configured value per instance, but the throttle key in the source is the organization id plus `_posts` (`ThrottlerBehindProxyGuard.getTracker`, postiz-app commit `4c5d42a`, read 2026-10-09). An API key or `pos_` OAuth token resolves to exactly one organization. ReachWeaver does not group per instance, because organizations on one instance have separate counters.
- Library callers of `publishers.publish()` get `postiz.BackendAccepted`, a `PublishError` subclass, on acceptance, so the generic "PublishError means `needs_review`" handling stays safe.

## Commands

```bash
reachweaver doctor                                   # offline; shows backend=postiz and the env name
reachweaver postiz-check --account ID --online       # read-only GET /integrations: id/identifier/profile/disabled + match result
reachweaver postiz-check --account ID --online --post-id POST_ID
#   read-only GET /posts around send_started_at: backend state and releaseURL (HTTPS only).
#   Backend-reported, not first-party evidence; it never reconciles.
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID   # owner-supervised, production host only
```

`connect-check` refuses backend accounts and points to `postiz-check`.

## What the API key can and cannot prove (scope detection)

A passing `postiz-check --online` proves only: the key is accepted, the configured integration id is listed for that key's organization, its provider identifier maps to the account's platform, its `profile` equals the configured `handle`, and it is not flagged `disabled`. The result is `identity: verified`, `write_permission: unverified`.

It does **not** prove: that the stored platform OAuth grant still has the write scope, that the platform token is unexpired or unrevoked, that the owner holds a Page/organization role, that `profile` is unique, or that an accepted post will be delivered. `GET /integrations` is documented to return no scope field; whether any Postiz version exposes one is **unverified**. The `disabled` parser reads only a literal `true` as disabled; a missing or non-boolean value reads as enabled (pinned by test, not changed here). ReachWeaver therefore never infers scope and keeps `write_permission` at `unverified`. Full table, design decision and the live-verification checklist (all unchecked): [POSTIZ-SCOPE-DETECTION.md](POSTIZ-SCOPE-DETECTION.md).

## Manual fallback when Postiz is unavailable

The fallback is to inspect the post offline and publish it yourself. Nothing is retried in the background.

- **Before the claim** (Postiz unreachable, 401/403/5xx on `GET /integrations`, a disabled or mismatched integration): the run prints `BLOCKED` and the post stays `approved`. No `POST /posts` was sent.
- `reachweaver show POST_ID` is offline and reads no key. It prints the exact approved text and the approval hash. For a backend account, its `platform_preview` is the Postiz request this account would send: `path: postiz`, `request` (`POST` URL, `type: now`, `shortLink: false`, configured `integration_id`), `handle`, `network: none`, plus the `receipt` and `manual_fallback` rules. It no longer shows the native Bluesky record key or service for a Postiz-backed Bluesky account, because that account never sends one.
- To post by hand, copy the exact text into the platform's own app. Then run `reachweaver cancel POST_ID` so that a later `publish --live` cannot also send it through Postiz. Keep the native URL in your private notes. A cancelled post is not counted as published; ReachWeaver claims no delivery it did not observe.
- For Threads and Facebook Pages, `manual-export` builds a full creator package ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)). For a campaign, use `campaign-export`.
- **After the claim** (any `needs_review` post): do not cancel and do not resend. First check the Postiz calendar and the native account, then `reconcile` (see Flow).

## Owner smoke test (staging or production)

**Status: NOT RUN.** On 2026-10-15, no Postiz instance, API key, integration or social account had been connected to ReachWeaver. No smoke test has been performed, and there is no receipt in [receipts/](receipts/). Nothing in this repository claims that a post sent through Postiz exists. Only the owner can run this procedure. An agent must not run it, schedule it, or treat a mocked test as a substitute for it.

**Scope.** One harmless text post to one account the owner controls, sent through one Postiz integration. Prefer Bluesky: text-only, and with a public read API for the receipt. You may use a Postiz Cloud workspace or your own self-hosted instance, and a dedicated test account or your real one. Record which ones you used.

1. **Offline readiness (safe).** `python3 -m unittest discover -s tests -q` passes, and `reachweaver doctor` shows `backend=postiz` and the variable name `{env_prefix}_POSTIZ_API_KEY`, never a value.
2. **Owner, in Postiz.** Connect the channel in Postiz yourself, accepting the platform's OAuth consent there. Then create an API key. Store it outside Claude, in the environment of your own terminal or as a systemd `LoadCredential=` file (see [Configuration](#configuration)), and use a separate `env_prefix` for each ReachWeaver account.
3. **Identity (read-only).** `reachweaver postiz-check --account ID --online` must show `identity: verified`, the expected `provider` and `write_permission: unverified`. Write down the integration `id`, `identifier`, `profile` and `disabled` fields actually returned (the [scope checklist](POSTIZ-SCOPE-DETECTION.md#live-verification-checklist-owner-supervised-none-done)).
4. **One post.** `draft`, then `show POST_ID` (check the `platform_preview` target), `check POST_ID`, `approve POST_ID` after reading the exact text, and `publish --post-id POST_ID` (a dry run, no network).
5. **Send (owner present, once).** `REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID`. Expected result: exit 3 and `REVIEW ... accepted by Postiz backend (postiz:<id>)`. Keep `reachweaver pause` ready, and run it if anything looks wrong.
6. **Observe.** `reachweaver postiz-check --account ID --online --post-id POST_ID` shows the backend `state` and `releaseURL`. This is backend-reported only. Next, open the native post, compare the text byte for byte, and read its native id from the platform (for Bluesky, the public AppView `app.bsky.feed.getPosts`).
7. **Reconcile.** `reachweaver reconcile POST_ID --published --remote-id NATIVE_ID --remote-url NATIVE_URL`, or `--not-published` if no native post exists. In that case, also delete the Postiz job in Postiz.
8. **Record evidence privately first.** Record the date and time with time zone, the instance (cloud, or self-hosted with its version), the integration identifier, the backend job id, the native id and URL, the observed text equality, and the exit codes. Publish a first-party receipt in `docs/receipts/postiz-<date>.json`, modelled on the Bluesky receipt, and update this status line only after the owner decides to. The test `PostizIssue56DocTests` keeps this section marked NOT RUN until a `postiz*` receipt exists.
9. **Teardown (optional).** Revoke the Postiz API key, or remove `backend` from the account entry. Accounts without `backend` use the native adapter, as before.

Pass criteria: one `POST /posts` and one native post, with byte-equal text, reconciled by a human. Failure handling: a `needs_review` post is never resent. Investigate it with the steps above.

## Licensing and credential review (2026-10-15)

This is a review of facts, not legal advice. Each item lists its source and the date it was checked.

| Item | Finding | Source (retrieved 2026-10-15) |
| --- | --- | --- |
| ReachWeaver licence | Apache-2.0 (`LICENSE`, `NOTICE`) | this repository |
| Postiz server licence | GNU AGPL v3. The GitHub license API reports `AGPL-3.0` for `LICENSE` (blob `24fd2279`), and the file opens with "GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007". The latest release is v2.25.1, 2026-10-11 | [LICENSE](https://github.com/gitroomhq/postiz-app/blob/main/LICENSE), `https://api.github.com/repos/gitroomhq/postiz-app/license`, `https://api.github.com/repos/gitroomhq/postiz-app/releases/latest` |
| Postiz agent | `gitroomhq/postiz-agent` has license `NOASSERTION` (unknown). ReachWeaver does not use it | `https://api.github.com/repos/gitroomhq/postiz-agent/license` |
| Copying | `reachweaver/postiz.py` is an independent client for the documented HTTP API. No Postiz source code, schema or asset is copied. One controller and one throttler guard were read only to confirm field names and the rate-limit key (see the table above). Interoperating over a network API does not make ReachWeaver a derivative of the AGPL server; this is the project's reading and has **not** been reviewed by counsel | [API overview](https://docs.postiz.com/public-api/introduction) (re-read 2026-10-15: raw `Authorization` key, 90/h (100 cloud) on create-post only, "5xx: retry with exponential backoff") |
| Running Postiz | Self-hosting or modifying a Postiz server is the owner's own AGPL-3.0 decision; its network-use clause applies to that server, not to ReachWeaver | AGPL-3.0 section 13 in the LICENSE above |
| Postiz Cloud terms | The terms page exists and showed "Last updated: October 15, 2026" when fetched. That date matches the retrieval day, so it may be generated when the page loads (**unverified**). The operators are Gitroom Limited and Gitroom LLC; Gitroom LLC holds the platform developer registrations. The terms forbid reselling or white-labelling the Service without a written agreement, and scraping or reverse engineering except as an open-source licence allows. A clause-by-clause review has **not** been done. ReachWeaver accepts no terms on the owner's behalf. Using Postiz Cloud is the owner's own contract | [Terms](https://postiz.com/terms), [Privacy](https://postiz.com/privacy) |
| Credential compartments | One key for each ReachWeaver account, held only in `{env_prefix}_POSTIZ_API_KEY` (environment or `$CREDENTIALS_DIRECTORY`), read at request time, never in `accounts.json`, never in a Claude prompt (MCP is not used, because its URL form embeds the key), and never in output, `last_error` or `event_log`. `load_accounts` refuses overlapping prefixes, `credentials-audit` reports reuse of the same value, and an account never borrows another account's key. ReachWeaver never sees platform OAuth tokens: the Postiz connection holds them | this repository: `tests/test_postiz.py` (`test_each_backend_account_sends_only_its_own_key`, `test_another_accounts_key_never_substitutes_for_a_missing_one`, `test_missing_key_and_malformed_key_never_echoed`, `test_post_claim_failures_need_review_and_hide_key`), `tests/test_hardening.py::test_postiz_backend_suffix_counted` |
| Permission | A Postiz API key is never permission to publish. Each post still needs its own approval, bound to the exact text and destination account (`approval_payload_sha256`, re-checked inside `claim_sending`), plus `--live` and both environment opt-ins | `tests/test_approval_binding.py` (`test_postiz_run_live_send_blocks_tampered_post` and the related tests) |

## Backend contract

`publishers.PublishingBackend` (a `typing.Protocol`) declares what every publishing path provides, and `publishers.backend_for(account)` names the path an account entry selects (no `backend` key: native; `"postiz"`: this backend; anything else: refused). It is declarative only: `publishers.publish` and `postiz.run_live_send` dispatch and gate exactly as before.

| Contract step | Native path (`publishers.NATIVE_PATH`) | Postiz (`postiz.POSTIZ_BACKEND`) |
| --- | --- | --- |
| `preview(account, content)`: offline, no credential, refuses text `validate_post` rejects | target account, platform and exact text | the same plus the fixed create shape (`POST /posts`, `type: now`, `shortLink: false`, configured integration id) |
| `create(...)`: one content-create request, only after a durable claim (`sendgate`) | returns the platform's own receipt | raises `BackendAccepted` (a `PublishError`): never a receipt |
| `lookup_receipt(...)`: read-only, never reconciles | `None` (create already returned the receipt) | `GET /posts` state and backend-reported `releaseURL` |
| Reconciliation hand-off (`acceptance_status`) | `published` | `needs_review` until a human `reconcile` with the native id/URL |

`tests/test_postiz_contract.py` runs the same conformance checks against both paths (native Bluesky mocked at `publishers.request_json`, Postiz replaying the synthetic fixtures in `tests/fixtures/providers/postiz/`).

Tests: `tests/test_postiz.py` (mocked `publishers._send_http`; payload shape, identity mismatch before claim, 401/403/429/5xx, timeouts, unexpected responses, key redaction, pause, dry run, live opt-in, unapproved posts, no auto-retry, read-only lookup). `PostizFixtureTests` in the same file replays synthetic fixtures for a create response carrying two jobs (a possible duplicate: `needs_review`, one `POST /posts`, nothing retried), two runs racing for one approved post (one claim, one create), a 403 on create shaped like a grant without write scope (`needs_review`, body and key never shown, not retried) and a disabled integration (`BLOCKED` before the claim). The fixture bodies are illustrative; none was recorded from Postiz. `PostizIssue56Tests` adds the remaining issue #56 cases. A `pause` during the read-only preflight is refused at the claim, so `POST /posts` is never sent. Backend states `QUEUE`, `ERROR`, an unknown state and a missing job are reported read-only, never reconciled, and the error text is not shown. A lookup refuses a post that has no `postiz:` job id. `reconcile` stores the observed native id and permalink. Each account sends only its own key. `show` previews the Postiz request offline. A Postiz outage leaves the post approved for the manual fallback. `PostizIssue56DocTests` pins the NOT RUN smoke-test status and the dated licensing review.

## Issue #56 acceptance evidence (offline, 2026-10-15)

| Acceptance test | Evidence | State |
| --- | --- | --- |
| Adapter interface plus provider-selection config, opt-in, disabled by default | `publishers.PublishingBackend`, `publishers.backend_for` (no `backend` key: native; unknown: refused); `tests/test_postiz_contract.py::ContractShapeTests`, `tests/test_postiz.py::PostizTests::test_native_accounts_unchanged`, `test_config_refusals` | met offline |
| Mock tests: account mismatch, 401/403/429/5xx, ambiguous write, duplicate job, paused queue, missing scope | `test_identity_mismatches`, `test_integration_mismatch_refused_before_claim`, `test_preflight_http_errors_block_before_claim`, `test_post_claim_failures_need_review_and_hide_key`, `PostizFixtureTests` (two jobs, race, 403 missing scope, disabled), `test_pause_respected_cli_and_direct`, `PostizIssue56Tests::test_pause_during_the_preflight_stops_the_send_at_the_claim`, `tests/test_rate_limits.py::test_postiz_post_claim_429_cooldown_and_preflight_429` | met offline |
| Observed remote id and permalink; an unknown outcome goes to review, with no blind retry | `test_acceptance_goes_to_needs_review_not_published` (`postiz:<id>`), `test_receipt_lookup_is_backend_reported_and_never_reconciles` (`releaseURL`), `PostizIssue56Tests::test_reconcile_stores_the_observed_native_id_and_permalink`, `test_backend_job_states_are_reported_and_never_reconcile` | met offline. The native id and URL come from the owner's observation, by design |
| Real-account smoke test documented | [Owner smoke test](#owner-smoke-test-staging-or-production) | procedure ready, **NOT RUN** (owner-only) |
| Credential compartments and licensing review, dated and sourced | [Licensing and credential review](#licensing-and-credential-review-2026-10-15) | recorded 2026-10-15. A counsel review of the Cloud terms is still open |
| README, platform matrix, skill publishing reference, offline tests | README "Optional Postiz backend", [PLATFORM-MATRIX.md](PLATFORM-MATRIX.md) backend row, `.claude/skills/reachweaver/references/publishing.md`, the tests above | met |
| Other publishing flows unchanged | `test_native_accounts_unchanged`, `PostizIssue56Tests::test_native_show_preview_is_unchanged`, the full offline suite | met offline |
