# Facebook Page publishing (issue #21, owner-approved, disabled)

> **Status:** owner-approved 2026-10-09; disabled until a live receipt. `reachweaver/facebook.py` is a mock-tested adapter that **cannot be reached**. Once enabled it is capped at 2 sends per 24 h, at least 60 minutes apart (`caps.BUILTIN_PROVIDER_CAPS`, decision D21-5). The capability registry keeps Facebook at `reachweaver_adapter: "none"` with text and link `api_gated`, and `policy.SUPPORTED_LIVE` does not include it. So `publish --live`, `connect-check --online` and the library `publish()` refuse Facebook before any credential is read or any request is made. It has been tested with mocked HTTP only. No Page has been connected, no Meta app has been reviewed and no live post has been sent from this repository. Until the owner completes the steps in SECURITY-REVIEW-PROPOSAL-21-facebook-page.md, publish on Facebook with [`manual-export --platform facebook`](MANUAL-EXPORT.md).

## What the adapter would do (once enabled)

It publishes one approved text post on **one configured Facebook Page**. Personal profiles and groups are never targets. The User feed cannot create posts, and the Groups API permissions were removed on 2024-04-22 ([PLATFORMS.md](PLATFORMS.md)). The adapter never sends media, `published=false`, `scheduled_publish_time`, targeting, place, tags, backdating or call-to-action fields.

Graph API version **v25.0** is pinned. It was released on 2026-02-18 and is available until **2028-07-29**. v26.0 has no published end date yet. `doctor` warns 90 days before the end date. From that date on, `connect-check` and `publish --live` refuse to run until the version is re-tested and re-pinned.

| Step | Request (base `https://graph.facebook.com/v25.0`) | When | Failure means |
|---|---|---|---|
| 1. Page-token check | `GET /me?fields=id,name` | before the claim | `id` must equal `page_id`. A User token returns the person's id, so it is refused here. Result: `BLOCKED`, and the post stays approved. |
| 2. Page check | `GET /{page_id}?fields=id,name` | before the claim | `id` and `name` must equal the config. Otherwise `BLOCKED`. |
| 3. Publish | `POST /{page_id}/feed`, form `message` (plus `link`, see below) | after the claim, with `mark_write_started()` just before | `needs_review` (ambiguous) |
| 4. Receipt | `GET /{post_id}?fields=id,permalink_url,from,message` | | If the response names another post or Page: `needs_review`. If the read fails: published, with `remote_url` empty. |

The token is sent **only** in the `Authorization: Bearer` header. It never goes in a URL or a form body. The `id` returned in step 3 is the receipt (`remote_id`), and it must have the form `{page_id}_{digits}`. Any other shape is treated as ambiguous. `permalink_url` is stored as `remote_url` only when it is an HTTPS `www.facebook.com` URL. The audit event records `text_match` (`true`, `false` or `unknown`).

**`link` comes from the approved text.** When the approved text contains an `https://` URL, the first one is sent as `link`, with trailing punctuation removed. That makes the attached preview match the URL the reviewer saw. Nothing comes from config or the network, so the #7 approval hash over `content` covers every byte sent. `http://` and `www.` links stay plain text. Meta documents that the post "will not succeed" if the app or viewer cannot see the link. Point at public pages only.

## Account configuration (no secrets)

```json
{
  "id": "my-page",
  "platform": "facebook",
  "env_prefix": "REACHWEAVER_FB_PAGE",
  "page_id": "102030405060708",
  "page_name": "My Project"
}
```

- `page_id`: the numeric `id` returned by `GET /me` when called with the Page token. It is compared exactly.
- `page_name`: the Page `name`. It is compared case-insensitively, with whitespace collapsed. If the Page is renamed, sends are blocked until you update this value.
- Any other key on an account that has `page_id` is refused, including `target_type`, `group_id`, `published`, `link` and `authorization_expires_at`. An account without `page_id` is treated as manual-export metadata and loads as before, but the adapter preflight refuses it.
- The token is read only from `{env_prefix}_PAGE_ACCESS_TOKEN`, through `accounts.credential` (the environment or systemd `$CREDENTIALS_DIRECTORY`). `_PAGE_ACCESS_TOKEN` is in `accounts.CREDENTIAL_SUFFIXES`, so the env_prefix overlap rule and `credentials-audit` cover Facebook accounts. Use a **long-lived Page token**. According to Meta these "do not have an expiration date", but they can be invalidated.

## Text rules

Meta's Pages pages state no message length limit. ReachWeaver keeps its own conservative cap of 4,500 code points (`policy.py`), and `validate_post` applies its usual control, bidi and invisible-character checks.

## Error handling

Errors are fixed codes. They never contain the token, the URL, request headers or the response body. Meta documents its error codes without HTTP statuses: 190 (expired token), 10 and 200-299 (permission), 4/17/32/613/80001 (throttling), 368 (policy block) and 506 (duplicate post). ReachWeaver therefore maps errors by HTTP status only and does not read error bodies.

- **Before the claim:** `facebook_http_400` (the token may be invalid, expired, not a Page token, or throttled), `facebook_http_401`, `facebook_http_403`, `facebook_http_429` (records an account cooldown) and `identity_mismatch`. The post stays approved.
- **After `mark_write_started()`:** every failure ends in `_uncertain`, for example `facebook_feed_http_400_uncertain`, `facebook_feed_network_or_timeout_uncertain`, `facebook_feed_post_id_unrecognized_uncertain` or `facebook_receipt_page_mismatch_uncertain`. The post goes to `needs_review` with send class `ambiguous` and is **never** retried. No idempotency key is documented. Before you reconcile, open the Page and check whether the post exists.
- A documented throttle that arrives as HTTP 400 does **not** record a cooldown. The send still stops at `needs_review` (proposal, decision D21-4).

## Sources (read 2026-10-09)

- Pages API, posts (endpoint, `message`, `link`, `published`, permissions, tasks, response, permalink): <https://developers.facebook.com/docs/pages-api/posts>
- Page feed reference (Page token, `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, link visibility): <https://developers.facebook.com/docs/graph-api/reference/page/feed>
- Pages API overview (App Review for all Page permissions, Advanced Access, `/me/accounts`): <https://developers.facebook.com/docs/pages-api/overview>
- Page node (`id`, `name`): <https://developers.facebook.com/docs/graph-api/reference/page>
- Post node (`permalink_url`, `from`): <https://developers.facebook.com/docs/graph-api/reference/post>
- Graph API overview (`/me` resolves to the Page of the token in use; host; versions): <https://developers.facebook.com/docs/graph-api/overview>
- Changelog (v25.0 available until 2028-07-29): <https://developers.facebook.com/docs/graph-api/changelog>
- Errors and rate limits: <https://developers.facebook.com/docs/graph-api/guides/error-handling>, <https://developers.facebook.com/docs/graph-api/overview/rate-limiting>
- Long-lived Page tokens: <https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/>
- Permissions (`pages_manage_posts` depends on `pages_read_engagement` and `pages_show_list`; Business Verification for Advanced Access): <https://developers.facebook.com/docs/permissions/reference/pages_manage_posts>
- Bearer header on `graph.facebook.com` (WhatsApp Cloud API token guide): <https://developers.facebook.com/documentation/business-messaging/whatsapp/access-tokens/>

**Not verified:** the HTTP status that goes with each Meta error code; the exact shape of post ids; whether the Pages endpoints accept the Bearer header (if they do not, step 1 fails before the claim); any limit on the number of Page posts.
