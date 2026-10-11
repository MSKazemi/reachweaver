# Threads text publishing (issue #19, owner-approved, disabled)

> **Status:** owner-approved 2026-10-09; disabled until a live receipt. `reachweaver/threads.py` is a mock-tested adapter that **cannot be reached**. The capability registry keeps Threads at `reachweaver_adapter: "none"` and text `api_gated`, and `policy.SUPPORTED_LIVE` does not include it, so `publish --live`, `connect-check --online` and the library `publish()` refuse Threads before any credential is read or any request is made. It has been tested with mocked HTTP only. Once enabled it is capped at 2 sends per 24 h (`caps.BUILTIN_PROVIDER_CAPS`, decision D19-4), and an emergency pause set during the container wait stops it before `threads_publish` (D19-2). No Threads account has been connected, no Meta app has been reviewed and no live post has been sent from this repository. Until the owner completes the steps in SECURITY-REVIEW-PROPOSAL-19-threads.md, publish on Threads with [`manual-export`](MANUAL-EXPORT.md).

## What the adapter would do (once enabled)

One approved, text-only post on **your own** Threads profile, through the official two-step publishing flow. It never sends media, replies, quotes, polls, topic tags, link attachments, reply controls, country allowlists or Instagram cross-shares.

| Step | Request (base `https://graph.threads.net/v1.0`) | When | Failure means |
|---|---|---|---|
| 1. Identity | `GET /me?fields=id,username` | before the claim | `BLOCKED`, post stays approved |
| 2. Quota | `GET /{threads_user_id}/threads_publishing_limit?fields=quota_usage,config` | before the claim | `BLOCKED` if unreadable or `quota_usage >= quota_total` |
| 3. Container | `POST /{threads_user_id}/threads` form `media_type=TEXT`, `text`, `access_token` | after the claim; `mark_write_started()` just before | `needs_review` (ambiguous) |
| 4. Wait | 30 s, then `GET /{container_id}?fields=status,error_message` once a minute, at most 5 times | | not `FINISHED` gives `needs_review` |
| 5. Publish | `POST /{threads_user_id}/threads_publish` form `creation_id`, `access_token` | | `needs_review` (ambiguous) |
| 6. Receipt | `GET /{media_id}?fields=id,permalink,username,owner,text` | | another profile named: `needs_review`; read failure: published with `remote_url` empty |

The media `id` from step 5 is the receipt (`remote_id`). The permalink is stored as `remote_url` only when it is an HTTPS `threads.net`/`threads.com` link of the form `/@{handle}/post/{code}` that names your handle. The audit event records `container_id` and `text_match` (`true`, `false` or `unknown`) to help reconciliation.

## Account configuration (no secrets)

```json
{
  "id": "my-threads",
  "platform": "threads",
  "env_prefix": "REACHWEAVER_THREADS",
  "handle": "yourhandle",
  "threads_user_id": "1234567",
  "authorization_expires_at": "2026-12-07T09:00:00+00:00"
}
```

- `handle`: the `username` from `GET /me` (a leading `@` is stripped). Compared case-insensitively.
- `threads_user_id`: the `id` from `GET /me`, compared exactly. Only digits are accepted, because the documented examples are numeric; this shape is an assumption.
- `authorization_expires_at`: optional, non-secret. Long-lived Threads tokens are valid for 60 days. `doctor` warns 7 days ahead, and an expired value is refused before any request.
- Any other key on an account that has `threads_user_id` is refused. An account without `threads_user_id` is manual-only metadata and loads as before, but the adapter preflight refuses it.
- The token is read only from `{env_prefix}_ACCESS_TOKEN` via `accounts.credential` (environment or systemd `$CREDENTIALS_DIRECTORY`). `_ACCESS_TOKEN` is in `accounts.CREDENTIAL_SUFFIXES`, so the env_prefix overlap rule and `credentials-audit` cover Threads accounts.

## Text rules

`validate_post` and the adapter apply the official limits, which only tighten the old local cap:

- 500 characters, with emoji counted as their UTF-8 byte length (`manual_export.threads_length`, conservative). The existing local cap of 450 code points still applies.
- At most 5 links; since 2025-12-22 Threads fails container creation above that.
- Threads makes the first link in a text-only API post its link attachment (preview card) when `link_attachment` is not sent. ReachWeaver never sends `link_attachment`.
- Only one topic tag is allowed per post. ReachWeaver never sends `topic_tag`, so hashtags stay plain text.

## Error handling

Errors are fixed codes. They never contain the token, the URL, request headers or the response body. Meta does not document which HTTP status it uses for an expired token, a missing permission or throttling (Graph API error codes 190, 10/200-299 and 4/17 are documented without statuses), so ReachWeaver maps by HTTP status only and does not read error bodies.

- Before the claim: `threads_http_400` (token may be invalid or expired), `threads_http_401`, `threads_http_403` (missing `threads_basic` or `threads_content_publish`), `threads_http_429` (records an account cooldown), `identity_mismatch`, `threads_quota_unreadable` and `threads_publishing_quota_exhausted`. The post stays approved.
- After `mark_write_started()`: every failure ends in `_uncertain` (for example `threads_create_http_429_uncertain`, `threads_container_error_not_public_uncertain`, `threads_publish_http_500_uncertain`). The post goes to `needs_review` with send class `ambiguous` and is **never** retried. No idempotency key is documented. Before reconciling, open your profile in the Threads app or website and check whether the post exists. An unpublished container is not public and expires after 24 hours.

## Meta app setup and review requirements (owner checklist)

**Verification status.** Every claim below comes from the Meta pages listed under "Sources (read 2026-10-09)". This documentation pass on **2026-10-10** had no network access, so the pages were **not re-fetched on 2026-10-10**. Treat each item as "last verified 2026-10-09 against the cited page", not as confirmed today. Items marked **[unverified]** are not established by any page this repository has read. Meta changes these flows without notice; check the live page before you rely on a step. Nothing here is evidence that a Meta app, tester or review exists for this project.

1. **Create the app** in the Meta App Dashboard with the **Threads use case**. Required permissions for this adapter: `threads_basic` and `threads_content_publish`. Set the OAuth redirect URI, the deauthorize URL and the data-deletion callback URL. (Create an app page, 2026-10-09.)
2. **Development mode, no review.** Add your own Threads profile as a **Threads Tester** (App roles) and accept the invitation in the Threads app (Account settings, Website permissions). Users with an app role can use the permissions without App Review. This is the intended route for a single owner posting to their own profile. (Get started page, 2026-10-09.)
3. **Authorize and exchange.** Authorize at `https://threads.com/oauth/authorize` with `scope=threads_basic,threads_content_publish`. The code is valid 1 hour and single use. A short-lived token lasts 1 hour; exchange it for a long-lived token (60 days, refreshable after 24 hours). Record `authorization_expires_at`. (Access token pages, 2026-10-09.)
4. **When Meta App Review is needed.** Only for people **without** an app role (any public user). Both permissions need review, then the app must be switched to **Live** ("Publish"), which asks for an app icon, a privacy policy URL, a data-deletion URL and, if Meta requests it, business verification. (Get started and Create an app pages, 2026-10-09.) Not needed for the owner-only route in step 2.
5. **[unverified]** The review turnaround time, the screencast or use-case wording Meta requires, whether a personal (non-business) Meta developer account can complete business verification, and whether every Threads account type can publish. The repository has no source for any of these. Do not promise a date to anyone.

Enabling the adapter is a separate owner decision: see SECURITY-REVIEW-PROPOSAL-19-threads.md. Until then use [`manual-export`](MANUAL-EXPORT.md).

## Sources (read 2026-10-09)

- Posts (two-step flow, 30 s wait, 500 characters, 5 links, link attachment, topic tags): <https://developers.facebook.com/docs/threads/posts>
- Publishing reference (all container parameters, including `auto_publish_text`, which is not used): <https://developers.facebook.com/documentation/threads/reference/publishing.md>
- Overview (250 API-published posts per 24-hour moving period, 1,000 replies, quota endpoint permissions, base URLs): <https://developers.facebook.com/documentation/threads/overview.md>
- Troubleshooting (container status values, polling once a minute for at most 5 minutes, quota response shape): <https://developers.facebook.com/docs/threads/troubleshooting>
- Profiles (`GET /me?fields=id,username`): <https://developers.facebook.com/documentation/threads/threads-profiles.md>
- Retrieve posts (`permalink`, `owner`, `username`): <https://developers.facebook.com/documentation/threads/retrieve-and-discover-posts/retrieve-posts.md>
- Get started (App Review, testers, permission grants of 90 days for public profiles): <https://developers.facebook.com/documentation/threads/get-started.md>
- Create an app (permissions, testers, App Review, publishing the app): <https://developers.facebook.com/documentation/threads/get-started/create-an-app.md>
- Access tokens and long-lived tokens (1 hour short-lived, 60 days long-lived, refresh after 24 hours): <https://developers.facebook.com/documentation/threads/get-started/get-access-tokens-and-permissions.md>, <https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens.md>
- Graph API error codes (not Threads-specific): <https://developers.facebook.com/docs/graph-api/guides/error-handling>

Not verified: account-type eligibility for publishing, the HTTP statuses for Meta error codes, the exact shape of Threads ids, and an `Authorization: Bearer` alternative to the documented `access_token` parameter.
