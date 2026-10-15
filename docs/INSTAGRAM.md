# Instagram single-image posts (issue #20, owner-approved, disabled)

**Status:** owner-approved 2026-10-09; disabled until a live receipt. The mock-tested adapter exists, but it is **unreachable**. The capability registry keeps Instagram `image` at `api_gated` with `reachweaver_adapter: "none"`, and `instagram` is not in `policy.SUPPORTED_LIVE`. `publish --live` and `connect-check --online` therefore refuse Instagram before any credential read or request. No Meta app, Instagram account or live post exists. Until the owner completes the steps in the security review proposal, use `manual-export --platform instagram` ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)). Owner preparation steps: [INSTAGRAM-SETUP.md](INSTAGRAM-SETUP.md).

## What the adapter would send

One JPEG image with a caption and alt text, to the authenticated **professional** (Business or Creator) account's own feed, through the Instagram API with Instagram Login (`graph.instagram.com`, version `v25.0` as in the official examples). It never sends carousels, video, reels, stories, user tags, locations, collaborators or product tags.

| Step | Request (official) | Notes |
|---|---|---|
| Identity | `GET /me?fields=user_id,username,account_type` | `user_id` must equal `ig_user_id`, `username` must equal `handle`, `account_type` must be Business or Media_Creator. Before the claim. |
| Quota | `GET /{ig_user_id}/content_publishing_limit?fields=quota_usage,config` | Read-only, before the claim. Refused when `quota_usage >= quota_total`. |
| Image check | `GET <your image URL>` | Must still hash to the approved SHA-256. Before the write marker. |
| Container | `POST /{ig_user_id}/media` `{image_url, caption, alt_text}` | JSON with `Authorization: Bearer`. The write marker is set just before this. |
| Status | `GET /{container}?fields=status_code` | Once a minute, at most 5 checks (official recommendation). |
| Publish | `POST /{ig_user_id}/media_publish` `{creation_id}` | Returns the media id (the receipt). |
| Receipt | `GET /{media}?fields=id,permalink,username,media_type` | A mismatching username or media type goes to `needs_review`. |

## Image hosting: you host the JPEG

Instagram does not accept an upload here: "We will cURL your image using the passed in URL so it must be on a public server". ReachWeaver hosts nothing. Put the JPEG on an HTTPS host you control (your site, a static bucket with public read) at a **plain path**:

- `https://`, a public DNS name (no IP address, `localhost` or `.local`), port 443;
- no user name or password, **no query string** (signed or tokenized URLs would store a secret in the database and in Meta's fetch) and no fragment;
- printable ASCII only (percent-encode anything else); no redirects (give the final URL);
- JPEG only (MPO/JPS are not supported by Instagram and are not detected by ReachWeaver), at most 8,000,000 bytes, aspect ratio from 4:5 to 1.91:1. Widths outside 320 to 1440 are scaled by Instagram, not refused.

```bash
reachweaver draft --account my-instagram --text "Release notes are out. Link in bio."
reachweaver media-attach POST_ID --url https://img.example.org/2026/release.jpg \
  --alt "Bar chart: monthly downloads rose from 10 to 40" --online
reachweaver show POST_ID      # url, sha256, size, dimensions; nothing uploaded
curl -s https://img.example.org/2026/release.jpg | sha256sum   # optional: compare with sha256
```

`media-attach` makes one read-only GET with no credentials. It refuses addresses that resolve to private networks, and records the SHA-256, size and dimensions. Alt text is required (at most 1,000 characters) and is checked against the opt-out list. Approval then binds the caption, the image SHA-256, the MIME type, the alt text and the URL. **Do not change or remove the image until the post is published.** A changed image blocks the send (`approval_media_changed_reapproval_required`) until you restore the original bytes or re-attach and re-approve (`unapprove`, `media-detach`, `media-attach`, `show`, approve). `media-detach POST_ID` works on drafts only.

## Account configuration (ignored `config/accounts.json`, no secrets)

```json
{"id": "my-instagram", "platform": "instagram", "env_prefix": "REACHWEAVER_IG",
 "handle": "your_username", "ig_user_id": "17841400000000000",
 "authorization_expires_at": "2026-12-01T00:00:00+00:00"}
```

`ig_user_id` is the `user_id` from `GET /me` (a string of digits). Without it the account stays manual-only metadata, as before. The token is read only from `REACHWEAVER_IG_ACCESS_TOKEN` (environment or `$CREDENTIALS_DIRECTORY`). Dashboard tokens are valid for 60 days; record `authorization_expires_at` so `doctor` warns 7 days ahead and refuses an expired token offline.

## Documented limits (read 2026-10-09)

Caption: 2,200 characters, 30 hashtags and 20 @ tags (ReachWeaver counts emails as @ tags, which only tightens the check). Containers expire after 24 hours, and at most 400 containers may be created per rolling 24 hours. The content-publishing guide says "100 API-published posts within a 24-hour moving period", while its carousel section and the endpoint reference (`quota_total` "currently 50") say 50. The live quota is authoritative, and ReachWeaver's own budget (at most 10 per 24 h, tighten-only caps) applies first.

Sources: [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing), [IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media), [content_publishing_limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit), [get started with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started), [IG Media](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media).

**Not verified:** the HTTP statuses Meta uses for an expired token, a missing permission or throttling; whether `Authorization: Bearer` works for GET (GETs use the documented `access_token` query parameter); the permalink format; whether the IG User Media limits (that reference still lists only Facebook Login requirements, re-checked 2026-10-15) apply unchanged to Instagram Login; whether Standard Access limits single-image publishing in practice.

**Re-checked 2026-10-12** against the [content publishing guide](https://developers.facebook.com/docs/instagram-platform/content-publishing) (one page, read-only): it still states none of the error statuses for an expired token, missing permission or throttling (it only names the rate-limited endpoint, `media_publish`), shows `Authorization: Bearer` only in POST examples, documents no permalink format, and does not say when app review or Advanced Access is required for accounts outside app roles. It does say "Extended JPEG formats such as MPO and JPS are not supported" (the outcome of submitting one is not described), and it still gives both 100 and 50 posts per 24 hours without reconciling them. Nothing above changes: those items stay unverified and fail closed, and the live `content_publishing_limit` stays authoritative. The IG User Media and Instagram Login pages were not re-read in this pass.

**Re-checked 2026-10-15** with `curl`, every quote matched with `grep` against the fetched page (the `docs/instagram-platform/...` links now redirect to `documentation/instagram-platform/...`). Scopes, the Instagram Login versus Facebook Login comparison and the eligibility findings are in [INSTAGRAM-SETUP.md](INSTAGRAM-SETUP.md#instagram-login-versus-facebook-login-dated-comparison-2026-10-15).

- [IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media): unchanged image rules, "Format: JPEG", "File size: 8 MB maximum.", "Aspect ratio: Must be within a 4:5 to 1.91:1 range", widths 320 to 1440 scaled; caption "Maximum 2200 characters, 30 hashtags, and 20 @ tags."; `alt_text` "Alternative text, up to 1000 character, for an image." (image posts only); "Containers expire after 24 hours"; "An Instagram account can only create 400 containers within a rolling 24 hour period". Its Requirements table still lists only the Facebook Login permissions (`instagram_basic`, `instagram_content_publish`, `pages_read_engagement`) and Page tasks, so the Instagram Login question above stays open. ReachWeaver applies these limits to Instagram Login anyway, which can only make it stricter.
- [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing): its Requirements table names, for Instagram Login, the Instagram User access token, host `graph.instagram.com`, Business Login for Instagram, and the permissions `instagram_business_basic` and `instagram_business_content_publish`, with Standard or Advanced Access. It still says both "Instagram accounts are limited to 100 API-published posts within a 24-hour moving period." and, in the carousel section, "Accounts are limited to 50 published posts within a 24-hour period." The [content_publishing_limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit) reference still gives `quota_total` "(currently `50`)". The live quota stays authoritative.
- [Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login) and [get started](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started): no Facebook Page needed; `account_type` "Can be `Business` or `Media_Creator`."; dashboard tokens are valid for 60 days, login-flow tokens for 1 hour. The identity check and the 60-day `authorization_expires_at` guidance above match.
- [Overview](https://developers.facebook.com/docs/instagram-platform/overview): Standard Access is enough for your own professional account; Advanced Access, for accounts you do not own or manage, "requires App Review and Business Verification". App calls are budgeted at "Calls within 24 hours = 4800 * Number of Impressions" per app and user pair.

Owner pilot runbook: [INSTAGRAM-PILOT.md](INSTAGRAM-PILOT.md).
