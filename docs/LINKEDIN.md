# LinkedIn personal-profile publishing (issue #14)

ReachWeaver can publish **one approved, text-only post to your own LinkedIn profile** through the official Posts API. Organization (company) pages are **not supported**.

> **Status (2026-10-08):** the adapter is tested with mocked HTTP only. No LinkedIn account has been connected and no live post has been sent from this repository. Treat your first send as a supervised pilot ([FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md)).

## What you need

| Item | Where it comes from |
|---|---|
| A LinkedIn developer app that you own | [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) |
| **Share on LinkedIn** product, which grants `w_member_social` | App, Products tab. It is an open, self-serve permission ([getting access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)) |
| **Sign In with LinkedIn using OpenID Connect** product, which grants `openid` and `profile` | App, Products tab. ReachWeaver uses it only to confirm whose token it holds |
| A member access token with `openid profile w_member_social` | The OAuth authorization code flow, or the portal's token generator ([authorization code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)) |
| Your member ID (`person_id`) | The `sub` value from `GET https://api.linkedin.com/v2/userinfo` ([OpenID Connect](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/sign-in-with-linkedin-v2)) |
| A pinned API version (`linkedin_version`, `YYYYMM`) | The "Latest Version" table on [Marketing API versioning](https://learn.microsoft.com/en-us/linkedin/marketing/versioning). It was `202609` on 2026-10-08 |

Organization posting needs `w_organization_social` and a page role (ADMINISTRATOR, DIRECT_SPONSORED_CONTENT_POSTER or CONTENT_ADMIN) according to the [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api) page. ReachWeaver refuses any `urn:li:organization` author and any organization, company, page or author field in the account config. Being able to post as a member never implies permission to post for a page.

## 1. Get a token privately

Do the OAuth consent yourself in your browser. Request only `openid profile w_member_social`. Never paste the token into Claude, an issue or a chat. LinkedIn currently issues access tokens with a **60-day** lifespan, and requesting a different scope set invalidates earlier tokens ([authorization code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)). Write down the expiry time shown at consent (`expires_in` seconds from issue).

Export the token in your local shell or secret manager, under the account's `env_prefix`:

```bash
export REACHWEAVER_LINKEDIN_ACCESS_TOKEN='...'   # set privately; never commit
```

To find your member ID, call the userinfo endpoint from your own terminal and copy `sub`:

```bash
curl -s -H "Authorization: Bearer $REACHWEAVER_LINKEDIN_ACCESS_TOKEN" https://api.linkedin.com/v2/userinfo
```

The OpenID discovery document lists the `pairwise` subject type, so treat `sub` as specific to your app. Read it with the same app whose token ReachWeaver uses.

## 2. Configure the account (no secrets in this file)

Add to your ignored `config/accounts.json`:

```json
{
  "id": "my-linkedin",
  "platform": "linkedin",
  "env_prefix": "REACHWEAVER_LINKEDIN",
  "person_id": "examplePersonId",
  "linkedin_version": "202609",
  "authorization_expires_at": "2026-12-07T09:00:00+00:00"
}
```

- `person_id`: the bare `sub`; `urn:li:person:{id}` is also accepted and stored bare. Letters, digits, `_` and `-` only.
- `linkedin_version`: required, `YYYYMM`.
- `scope_check`: optional; `"introspect"` turns on the pre-send write-scope check in section 3a. Any other value is refused.
- `authorization_expires_at`: optional, non-secret ISO-8601 timestamp with a timezone offset (use the expiry recorded at consent). It is accepted only on LinkedIn accounts, and any value that is not a timestamp is rejected. Do not name it `token_expires_at`: the loader rejects every key containing "token", "secret" or "password" so credentials never land in this file.

## 3. Check offline, then online

```bash
reachweaver doctor
# my-linkedin: linkedin; supported (text); env variables missing: none
#   my-linkedin: WARNING: linkedin_version_ageing: 202512 is 10 months old; ...   (only when relevant)
#   my-linkedin: WARNING: linkedin_token_expiring: about 3 day(s) left; ...      (only when relevant)

reachweaver connect-check --account my-linkedin --online
```

`doctor` makes no request. `connect-check --online` makes one `GET /v2/userinfo` and requires `sub` to equal `person_id` exactly. It proves **identity only**. It cannot prove that the token carries `w_member_social`, so the result reports `write_permission: unverified`, and `doctor` prints `INFO: linkedin_write_scope_unverified` to explain the residual risk: a token without the scope fails with HTTP 403 *after* the send is claimed, and the post goes to `needs_review`. To close that gap, opt in to the scope check below.

## 3a. Optional: verify `w_member_social` before every send

> **Opt-in, owner-approved 2026-10-09.** It stores your LinkedIn **app client secret** on the publishing host, which is a credential-isolation decision. See SECURITY-REVIEW-PROPOSAL-14-linkedin-scope.md. It is off unless you opt in per account.

Add `"scope_check": "introspect"` to the LinkedIn account in `accounts.json`. Then export two more values from your app's **Auth** tab next to the access token:

```bash
export REACHWEAVER_LINKEDIN_CLIENT_ID='...'       # set privately; never commit
export REACHWEAVER_LINKEDIN_CLIENT_SECRET='...'   # set privately; never commit
```

With the opt-in, `connect-check --online` and `publish --live` also call LinkedIn's read-only [token introspection](https://learn.microsoft.com/en-us/linkedin/shared/authentication/token-introspection) endpoint (accessed 2026-10-09) after the `userinfo` check and **before the send is claimed**:

```http
POST https://www.linkedin.com/oauth/v2/introspectToken
Content-Type: application/x-www-form-urlencoded

client_id=<app client id>&client_secret=<app client secret>&token=<member access token>
```

The secret goes only in the form body, never in a URL ([authorization code flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow) warns against query strings). The send is refused (`BLOCKED ... account identity preflight failed`, post stays `approved`, nothing is sent) unless **all** of these hold:

| Check | Refusal code |
|---|---|
| `active` is `true` and `status` (if present) is `active` | `linkedin_token_inactive`, `linkedin_token_revoked`, `linkedin_token_expired` |
| `client_id` (if present) equals your `_CLIENT_ID` | `linkedin_token_client_mismatch` |
| `auth_type` (if present) is `3L`, a member token | `linkedin_token_not_member_token` |
| `scope` contains exactly `w_member_social` | `linkedin_scope_missing_w_member_social` |
| `expires_at` is present and more than 1 hour away | `linkedin_introspection_missing_expiry`, `linkedin_token_expired`, `linkedin_token_expires_within_margin` |

HTTP errors also refuse before the claim: 400 (`linkedin_introspect_http_400`, LinkedIn says the client id or token is invalid), 401 (`linkedin_introspect_http_401`, invalid client secret), 5xx and timeouts (at most two jittered retries, as for `userinfo`). A 429 records the same account cooldown as a `userinfo` 429.

When the check passes, `connect-check` reports `write_permission: verified`. If `authorization_expires_at` is missing or more than one day off the expiry LinkedIn reports, ReachWeaver prints a note with LinkedIn's expiry (`linkedin_expiry_unrecorded` / `linkedin_expiry_mismatch`). It never edits `accounts.json`; update it yourself. Only the fixed codes, a yes/no and the expiry time are ever printed. The token, the client secret, the scope list and the raw response are never printed, logged or stored.

**Call budget.** LinkedIn documents no rate limit for `introspectToken`. All LinkedIn API calls are rate limited per app and per member per UTC day, and standard limits are not published ([rate limiting](https://learn.microsoft.com/en-us/linkedin/shared/api-guide/concepts/rate-limits), accessed 2026-10-09). Whether introspection counts toward the member quota is **not documented**. ReachWeaver therefore makes at most one introspection per account per run, keeps the result in memory only for 15 minutes, and stops after 3 requests per account per run even if they fail.

**Without the opt-in nothing changes:** no client credentials are read, no introspection request is made, and `credentials-audit` reports an exported `_CLIENT_SECRET` for that account as `orphan_credential`.

## 4. Publish one approved post

```bash
reachweaver draft --account my-linkedin --file post.txt
reachweaver show POST_ID          # review the exact text
reachweaver approve POST_ID
reachweaver publish --post-id POST_ID                                   # dry run
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID   # one supervised send, production host only
```

Before the send is claimed, ReachWeaver refuses (status `BLOCKED`, post stays `approved`):

- a `linkedin_version` that is 12 or more months old, malformed or in the future;
- a `authorization_expires_at` in the past;
- a `sub` that does not match `person_id`, or a userinfo 401/403;
- an organization author or field;
- with `"scope_check": "introspect"` only: a token that introspection does not show as active, issued to your app, a member token, carrying `w_member_social` and valid for more than an hour, or any introspection error (section 3a).

Then it sends exactly one request:

```http
POST https://api.linkedin.com/rest/posts
Authorization: Bearer <token>
Linkedin-Version: 202609
X-Restli-Protocol-Version: 2.0.0
Content-Type: application/json

{"author": "urn:li:person:examplePersonId", "commentary": "<escaped text>", "visibility": "PUBLIC",
 "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
 "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": false}
```

On HTTP 201 the `x-restli-id` header holds the post URN (`urn:li:share:…` or `urn:li:ugcPost:…`). ReachWeaver stores it as `remote_id`, plus `https://www.linkedin.com/feed/update/{urn}/` as `remote_url`. Any other outcome, including a missing header, a 403 for a missing `w_member_social` (the message names `w_member_social` and the Share on LinkedIn product and ends with `verify the profile before reconciling`), a 426 for an inactive version, a 429 or a timeout, becomes `needs_review`. It is never retried, because the Posts API documents no idempotency key. Open your profile, then run `reconcile`. A 429 (on `userinfo` or on the post) also records an account cooldown until the next 00:00 UTC, LinkedIn's documented daily reset, unless a `Retry-After` header says otherwise; the account's other posts wait until then. A post-claim LinkedIn 429 is classified `ambiguous`, because LinkedIn does not say whether a rate-limited create was applied ([worker operations](WORKER-OPERATIONS.md#provider-rate-limits-and-cooldowns-issue-11)).

## Text formatting

`commentary` uses LinkedIn's [`little` text format](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format). Reserved characters must be backslash-escaped even when they are not part of an element. ReachWeaver escapes `| { } @ [ ] ( ) < > # \ * _ ~` so the approved text appears literally. It never creates `@[Name](urn)` mentions. A `#` at the start of a word, followed by a letter or digit, is left as a hashtag. The conservative local length cap is 2,800 code points. LinkedIn returns `FIELD_LENGTH_TOO_LONG` above its own limit, which ReachWeaver has not independently verified. Because escaping adds a backslash per reserved character, `validate_post` also refuses a draft whose ESCAPED commentary exceeds 3,000 characters, before any approval or send.

## API version lifecycle

LinkedIn ships a version every month and supports each one for **a minimum of one year**. Calls without a version, or with an inactive one, are refused (400 `VERSION_MISSING`, 426 `NONEXISTENT_VERSION`) ([versioning](https://learn.microsoft.com/en-us/linkedin/marketing/versioning), [error responses](https://learn.microsoft.com/en-us/linkedin/marketing/error-responses)). On 2026-10-08 the docs announced that version 202510 sunsets on 2026-10-15.

ReachWeaver's local policy:

| Version age (calendar months) | `doctor` | Live send |
|---|---|---|
| 0–9 | silent | allowed |
| 10–11 | `WARNING: linkedin_version_ageing` | allowed |
| 12+ | `ERROR: linkedin_version_unsupported` | refused before the claim |
| in the future, not `YYYYMM`, or before 202206 | `ERROR: linkedin_version_invalid` | refused before the claim |

This matches the registry constraint in `platform_registry.json` (each version is supported for a minimum of one year; live sends are refused from month 12). Age is counted in calendar months from the version's `YYYYMM` to the current UTC month, so `202510` is 12 months old throughout October 2026 and is refused even before LinkedIn's announced 2026-10-15 sunset. Tests: `tests/test_linkedin.py` (`test_version_status_month_boundaries_match_documented_policy`).

Upgrade procedure: read the LinkedIn changelog for the Posts API, update `linkedin_version`, run `doctor` and `connect-check --online`, then supervise one post.

## Limits and what is not implemented

- The Share on LinkedIn page documents 150 requests per member and 100,000 per application per UTC day. ReachWeaver's own budgets (two attempts per rolling 24 hours, 60-minute gap) are stricter.
- Not implemented: image, video, document or article posts; polls; edits and deletes; comments, likes or any engagement; analytics (`r_member_social` is restricted to approved users); token refresh. The pre-send `w_member_social` check exists only as an opt-in (section 3a).
- The `/feed/update/{urn}/` link format is documented for `ugcPost` URNs and assumed for `share` URNs. The URN is the authoritative receipt.
