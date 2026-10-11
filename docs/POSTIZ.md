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
| License | `gitroomhq/postiz-app` is **AGPL-3.0** (GitHub license API and `LICENSE` file); latest release v2.25.0, 2026-10-02. `gitroomhq/postiz-agent` reports `NOASSERTION` | [postiz-app](https://github.com/gitroomhq/postiz-app), [LICENSE](https://github.com/gitroomhq/postiz-app/blob/main/LICENSE) |

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

## Before any real use

1. Owner connects the channel in Postiz, creates an API key, and exports it outside Claude.
2. `postiz-check --online` shows `identity: verified` for the configured integration.
3. One supervised post via [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md), then reconcile against the native post. Record the date, instance (cloud or self-hosted version) and evidence privately. **No such smoke test has been run.**

Tests: `tests/test_postiz.py` (mocked `publishers._send_http`; payload shape, identity mismatch before claim, 401/403/429/5xx, timeouts, unexpected responses, key redaction, pause, dry run, live opt-in, unapproved posts, no auto-retry, read-only lookup).
