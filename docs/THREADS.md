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
- `authorization_expires_at`: optional, non-secret. Long-lived Threads tokens are valid for 60 days. `doctor` and `health` (and so `ops-summary` and the dashboard) warn 7 days ahead, and an expired value is refused before any request.
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

## Media, alt text and other container options (not sent; checked 2026-10-15)

The adapter sends `media_type=TEXT` only. Media posts on Threads go through [`manual-export`](MANUAL-EXPORT.md) (`--format image|video|carousel` with a media manifest that requires alt text). These facts were fetched with `curl` on 2026-10-15 and matched with `grep`; they describe the official API, not anything ReachWeaver does:

- `media_type` accepts `TEXT`, `IMAGE`, `VIDEO` and `CAROUSEL`. `alt_text` is "The accessibility text label or description for an image or video in a Threads post", with "The maximum length of `alt_text` is 1,000 characters." ([publishing reference](https://developers.facebook.com/documentation/threads/reference/publishing.md))
- `image_url` and `video_url` must be fetchable by Meta: "We will cURL your image using the URL provided so it must be on a public server." A carousel needs "at least 2 and no more than 20 total images, videos, or a mix of the two", and "Carousel posts count as a single post against a profile's rate limit." ([posts](https://developers.facebook.com/documentation/threads/posts.md))
- Audience and reply options that the adapter never sends: `reply_control`, `allowlisted_country_codes` (the post "will not be shown to Threads profiles in countries outside of this list"), `link_attachment`, `topic_tag`, `quote_post_id`, `poll_attachment`, `crossreshare_to_ig`, `location_id` and `auto_publish_text` (which would skip the separate publish step, so the container check and the D19-2 pause point would be lost). ([publishing reference](https://developers.facebook.com/documentation/threads/reference/publishing.md))

A media adapter would need its own owner decision, its own registry switch and an owner-hosted public URL, like the Instagram image adapter (issue #20). None exists for Threads.

## Issue #49 acceptance evidence (offline, mocked)

Each test below runs with mocked HTTP on a temporary copy of the registry; nothing here is a live receipt. Run: `python3 -m unittest discover -s tests -p "test_threads.py" -v`.

| Criterion | Evidence |
|---|---|
| App setup, required permissions, supported account types and review requirements, with a verification date | "Meta app setup and review requirements" below (checked 2026-10-09, re-checked 2026-10-15) |
| Mock contract: successful post | `tests/test_threads.py::test_happy_path_requests_and_receipt`, `tests/test_threads.py::test_end_to_end_receipt_stored` |
| Mock contract: rejected permission | `tests/test_threads.py::test_preflight_http_errors_are_pre_write_and_actionable` (403 on `/me` names `threads_basic`, 403 on the quota read names `threads_content_publish`), `tests/test_threads.py::test_contract_rejected_permission_blocks_before_claim_and_keeps_post_approved`, and a 403 after the write in `tests/test_threads.py::test_post_write_http_errors_are_uncertain` |
| Mock contract: rate limit | `tests/test_threads.py::test_preflight_429_records_cooldown_and_keeps_post_approved`, `tests/test_threads.py::test_exhausted_or_unreadable_quota_refused_before_any_write`, 429 after the write in `tests/test_threads.py::test_post_write_failure_goes_to_needs_review_and_is_never_resent` |
| Mock contract: expired token | `tests/test_threads.py::test_contract_expired_token_blocks_before_claim_and_keeps_post_approved` (recorded expiry refused before any request; a 401 from Meta blocks before the claim), `tests/test_threads.py::test_offline_refusals_before_network` |
| Approved preview and matching remote receipt reconciled without duplicates | `tests/test_threads.py::test_approved_preview_matches_the_receipt_and_is_published_exactly_once` (dry-run preview, the same text sent, `text_match=true`, no request on re-runs), `tests/test_threads.py::test_needs_review_is_closed_by_reconcile_and_never_resent`, `tests/test_threads.py::test_reconciled_receipt_cannot_be_applied_twice` |
| Live publishing disabled without an authorized connection and explicit approval | `tests/test_threads.py::UnreachableTests` (bundled registry), `tests/test_threads.py::test_unapproved_draft_never_sent`, `tests/test_threads.py::test_without_live_gate_no_request`, `tests/test_threads.py::test_wrong_account_refused_before_any_write`, missing token in `tests/test_threads.py::test_offline_refusals_before_network` |

Still owner-only: a Meta app with the Threads use case, the owner's profile accepted as a Threads Tester, a long-lived token, a supervised first live post with its receipt, and the reviewed registry and `policy.SUPPORTED_LIVE` change.

## Meta app setup and review requirements (owner checklist)

**Verification status.** Every claim below comes from the Meta pages listed under "Sources (read 2026-10-09)". This documentation pass on **2026-10-10** had no network access, so the pages were **not re-fetched on 2026-10-10**. Treat each item as "last verified 2026-10-09 against the cited page", not as confirmed today. Steps 2, 4 and 5 were re-checked on **2026-10-15** against the live pages (see "Re-checked 2026-10-15" below). Items marked **[unverified]** are not established by any page this repository has read. Meta changes these flows without notice; check the live page before you rely on a step. Nothing here is evidence that a Meta app, tester or review exists for this project.

1. **Create the app** in the Meta App Dashboard with the **Threads use case**. Required permissions for this adapter: `threads_basic` and `threads_content_publish`. Set the OAuth redirect URI, the deauthorize URL and the data-deletion callback URL. (Create an app page, 2026-10-09.)
2. **Development mode, no review.** Add your own Threads profile as a **Threads Tester** (App roles) and accept the invitation in the Threads app (Account settings, Website permissions). Users with an app role can use the permissions without App Review. This is the intended route for a single owner posting to their own profile. (Get started page, 2026-10-09.)
3. **Authorize and exchange.** Authorize at `https://threads.com/oauth/authorize` with `scope=threads_basic,threads_content_publish`. The code is valid 1 hour and single use. A short-lived token lasts 1 hour; exchange it for a long-lived token (60 days, refreshable after 24 hours). Record `authorization_expires_at`. (Access token pages, 2026-10-09.)
4. **When Meta App Review is needed.** Only for people **without** an app role (any public user). Both permissions need review, then the app must be switched to **Live** ("Publish"), which asks for an app icon, a privacy policy URL, a data-deletion URL and, if Meta requests it, business verification. (Get started and Create an app pages, 2026-10-09.) Not needed for the owner-only route in step 2.
5. **Account types (re-checked 2026-10-15, see below).** No official Threads page read on 2026-10-15 limits publishing to a business, creator or professional account, unlike Instagram. The only account distinction the pages draw is **public versus private profile**, and it affects how long a permission grant lasts, not whether the profile can publish.
6. **[unverified]** The review turnaround time, the screencast or use-case wording Meta requires, whether a personal (non-business) Meta developer account can complete business verification, and whether every Threads profile can publish in practice (no page restricts it, but no receipt proves it either). The repository has no source for any of these. Do not promise a date to anyone.

### Re-checked 2026-10-15 (account types and app setup)

Fetched with `curl` on 2026-10-15; every quote below was matched with `grep` against the fetched page. The `developers.facebook.com/docs/threads/...` links now redirect to `developers.facebook.com/documentation/threads/...`.

- **Who can grant the permissions.** "Threads testers can grant your app these permissions at any time. In order for app users without a role on your app to be able to grant your app these permissions, each permission must first be approved through the App Review process, and your app must be published." ([get started](https://developers.facebook.com/docs/threads/get-started)) Unchanged from 2026-10-09: the owner-as-tester route needs no App Review.
- **Public versus private profiles.** "Permission grants made by app users with public profiles are valid for 90 days." Refreshing the long-lived token extends a public profile's grant by another 90 days, but if the profile is private "the permission grant cannot be extended and the app user must grant the expired permission to your app again." ([get started](https://developers.facebook.com/docs/threads/get-started)) So a **private** Threads profile has to re-authorize after the grant expires; the page does not say that it cannot publish.
- **No business or creator requirement found.** The [permissions reference](https://developers.facebook.com/docs/permissions) (served at `developers.facebook.com/documentation/development/permissions`) describes `threads_content_publish` as allowing "a business app to create and publish content on behalf of a Threads profile", with the allowed usage "enable a business app user to create and publish content to their Threads profile". "Business app" describes the Meta **app**, and the dependency is `threads_basic` only. The [overview](https://developers.facebook.com/docs/threads/overview) and [posts](https://developers.facebook.com/docs/threads/posts) pages state no account-type condition either.
- **App setup.** The [Threads use case page](https://developers.facebook.com/documentation/development/create-an-app/threads-use-case) lists `threads_basic` as required ("Cannot be removed") and `threads_content_publish` as optional. "Testing is only required if you are submitting your app for App Review"; App Review steps are listed under "For Tech Providers". Publishing the app needs an app icon, a privacy policy URL, a data-deletion URL and "Contact information for a Data Protection Officer, if you are doing business in the European Union". Business verification appears only as a "See also" link; the permissions reference says Business Verification is required for apps requesting Advanced Access. Get started also notes that a new app has two app IDs and secrets, and that you should "use the Threads app ID and its corresponding app secret".
- **Limits.** "Threads profiles are limited to 250 API-published posts within a 24-hour moving period" (also 1,000 replies, 100 deletions and 500 location searches per 24 hours), "Text posts are limited to 500 characters", and app calls are budgeted at `4800 * Number of Impressions` per 24 hours. The API "can be accessed by either `graph.threads.com` or `graph.threads.net`" ([overview](https://developers.facebook.com/docs/threads/overview)). These match what the adapter already enforces.

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

Not verified: whether every Threads profile can publish in practice (no official page restricts publishing by account type, re-checked 2026-10-15), the HTTP statuses for Meta error codes, the exact shape of Threads ids, and an `Authorization: Bearer` alternative to the documented `access_token` parameter.
