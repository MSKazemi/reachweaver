# TikTok (issue #23): creator-inbox upload first, Direct Post disabled

**Status: owner-approved 2026-10-09; disabled until a live receipt; mock-tested, unreachable.** `reachweaver/tiktok.py` implements the official Content Posting API flow, but the capability registry keeps both switches off (`reachweaver_video_inbox`, `reachweaver_video_direct_post`), and `policy.SUPPORTED_LIVE_VIDEO` is empty. `publish --live` and `connect-check --online` therefore refuse TikTok before any credential read or request. No TikTok app, account or upload exists. Until the owner's review (proposal), use `manual-export --platform tiktok` ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)).

## Two modes

| Mode | Scope | What TikTok does | What ReachWeaver records |
|---|---|---|---|
| `creator_inbox` (default, first) | `video.upload` | Puts the video in the creator's inbox as a draft. The creator must tap the inbox notification, then edit, caption and post it in the TikTok app. | `needs_review` with reason `tiktok_creator_inbox_delivered_not_public`, `remote_id = tiktok_inbox:<publish_id>`. **Never `published`.** |
| `direct_post` (second switch, off) | `video.publish` | Publishes with the bound caption and privacy level. Unaudited clients: "All content posted by unaudited clients will be restricted to private viewing mode." | `published` only when the status response returns a public post id; otherwise `needs_review`. |

Inbox upload comes first because it creates nothing public. The creator keeps the final decision in the native app, and it needs no audit to be useful. Direct Post from an unaudited client can only produce a private post.

**Reconciling an inbox upload.** After the creator posts the draft in the app, open the video and run `reconcile POST_ID --published --remote-id VIDEO_ID --remote-url URL`. If the draft was discarded, run `reconcile POST_ID --not-published`. `dead-letter` lists these posts with that guidance. Reports and metrics never count a post that has not been reconciled.

## Setup, review and audit (owner steps; ReachWeaver offers no bypass)

Official page read 2026-10-15: [Content Posting API: get started](https://developers.tiktok.com/doc/content-posting-api-get-started), which shows "Last updated August 4, 2026". Everything below is the owner's to do in TikTok's own portal and app. ReachWeaver never creates an app, requests a scope, logs in for you or handles a login challenge.

| Step | Who | What the official page says |
|---|---|---|
| 1. Register an app and add the product | Owner | Register an app, then "Add the Content Posting API product to your app". |
| 2. Pick the mode | Owner | **Direct Post:** enable the Direct Post configuration and get `video.publish` approved: "Your app must be approved for the video.publish scope." **Creator inbox:** uses `video.upload`, which is documented in the upload guide listed under Sources, not on the page above. |
| 3. Authorize the app | The TikTok user (the external user) | "The target TikTok user must have authorized your app for the video.publish scope." The user does this in their own TikTok session, including any login or verification TikTok asks for. (Not from this page: token lifetimes are in the token-management guide under Sources, and ReachWeaver's error hints assume a 24-hour access token. The owner supplies a fresh token through an environment variable.) |
| 4. Audit before public posts | Owner | "All content posted by unaudited clients will be restricted to private viewing mode." To lift that, "your API client must undergo an audit to verify compliance with our Terms of Service." |
| 5. Creator options before each post | ReachWeaver, read-only | The app must query the creator's current options before a Direct Post. ReachWeaver does this in the preflight and refuses a privacy level the creator is not offered. |

Until the audit passes, a Direct Post can only be private (`SELF_ONLY`), and ReachWeaver records it as `needs_review`, never as published, unless TikTok returns a public post id. The creator-inbox mode creates nothing public at all.

**No bypass.** ReachWeaver will not work around the audit, the private-viewing restriction, a missing scope or a TikTok challenge: no scraping, no unofficial endpoints, no browser or UI automation and no shared logins. TikTok's Terms of Service (last updated 1 December 2025, section 5) bar users from "use automated scripts to collect information from or otherwise interact with the Services". If the app is not approved or audited, the supported path is the creator-inbox mode or the manual package from `manual-export`, and the creator finishes the post in the TikTok app.

**Not verified:** how long an audit takes, what it requires beyond Terms-of-Service compliance, whether there is a cap on the number of users or posts for an unaudited client, and how the `video.upload` scope is approved. The page above does not say. Do not assume them.

## Commands (offline)

```bash
reachweaver draft --account my-tiktok --text "How the checks work #devtools"
reachweaver video-attach POST_ID --file media/clip.mp4                 # creator_inbox (default)
reachweaver video-attach POST_ID --file media/clip.mp4 --mode direct_post \
    --privacy-level SELF_ONLY --commercial none [--allow-comment] [--allow-duet] [--allow-stitch] [--aigc]
reachweaver show POST_ID      # record, file re-hash, both registry switches, what is sent
reachweaver approve POST_ID   # binds caption + the shared video record (SHA-256, options; not the path) under approval/video-1
reachweaver video-detach POST_ID
```

- The video must be a regular, non-symlink MP4 file of at most 256 MiB (a ReachWeaver cap; TikTok allows 4 GB). It must be H.264, H.265, VP8 or VP9, 23-60 fps, 360-4096 px per side and at most 600 s. Any value the header parser cannot read refuses the file.
- In `creator_inbox` mode, only the video is sent. The caption is your suggestion; the creator writes the real one in the app. Privacy, interaction and commercial flags are refused in this mode, because the creator sets them in the app.
- `direct_post` needs an explicit `--privacy-level`, with no default and never PUBLIC by default. The level must appear in the creator's live `privacy_level_options` at send time. It also needs `--commercial none|own_brand|branded_content`, and branded content cannot be `SELF_ONLY`. Comments, duets and stitches are **off** unless you turn them on (Content Sharing Guidelines).
- If the file changes after approval, `check`, `approve` and `publish --live` refuse it with `approval_video_changed_reapproval_required`. Re-approve with `unapprove`, `video-attach`, `show`, then `approve`.

## Account config (public metadata only)

```json
{"id": "my-tiktok", "platform": "tiktok", "env_prefix": "REACHWEAVER_TT",
 "handle": "yourname", "open_id": "<open_id from GET /v2/user/info/>", "display_name": "<exact display_name>"}
```

Without `open_id`, the account is manual-only metadata, as before. With it, only these keys are allowed. The token is read only from `{env_prefix}_TIKTOK_ACCESS_TOKEN`, which with the example prefix is `REACHWEAVER_TT_TIKTOK_ACCESS_TOKEN`. TikTok access tokens are valid for 24 hours, and ReachWeaver has no refresh flow.

## Live flow (when enabled after review)

```text
run_live_send
  require_video_mode(tiktok, mode)        registry switch + SUPPORTED_LIVE_VIDEO; nothing before it
  opt-out, approval binding, cooldown     pre-claim, no network
  re-read file, SHA-256 == record         else approval_binding_failed, BLOCKED
  GET /v2/user/info/                      open_id + display_name must match
  [direct] POST creator_info/query/       nickname/username, privacy option, locks, max duration
  claim_sending(expected_sha256)          pause, binding, budgets under lock
  POST .../inbox/video/init/ or .../video/init/    publish_id + upload_url (no content yet)
  mark_write_started()
  PUT upload_url, Content-Range per chunk 206 ... 201 (no Authorization header)
  POST /v2/post/publish/status/fetch/ every 10 s, at most 30 checks
     inbox: SEND_TO_USER_INBOX -> needs_review (delivered, not public)
     direct: PUBLISH_COMPLETE + publicaly_available_post_id -> published
     FAILED / timeout / any error -> needs_review, never retried
```

Chunking follows the media transfer guide. A file of at most 64,000,000 bytes is sent as one chunk; larger files use 10 MiB chunks, with the remainder in the final chunk; `total_chunk_count = size // chunk_size`.

**Before TikTok is enabled** (owner decision 2026-10-21, not built yet): the status polling must re-read the emergency pause between checks (today it does not), and the owner decides together how an inbox delivery counts in `queue-health` and in the account hold (today it is `needs_review`, so it is counted as ambiguous and holds the account until reconciled). See [preconditions](FIRST-LIVE-PILOT.md#preconditions-before-enabling-tiktok-youtube-or-a-meta-adapter).

## Offline fixtures

`tests/fixtures/providers/tiktok/` holds twelve **synthetic, hand-written** responses (not recordings), replayed by `ChunkFixtureTests` and `CreatorOptionFixtureTests` in `tests/test_tiktok.py` against a temporary flipped registry:

- **Chunk partial failures:** HTTP 500 or 416 on chunk 2, a 200 instead of 206 on chunk 1, and a 206 instead of 201 on the final chunk. Each goes to `needs_review` as `ambiguous` (`tiktok_upload_http_500_uncertain`, `..._http_416_uncertain`, `tiktok_upload_unexpected_status_200_uncertain`, `..._206_uncertain`); no chunk is retried and no status poll runs.
- **Invalid `creator_info`:** empty, missing, non-list or non-string `privacy_level_options`, and a missing, string or zero `max_video_post_duration_sec`. Direct Post is refused before the claim (`tiktok_privacy_level_not_offered`, `tiktok_creator_info_missing_privacy_options`, `tiktok_creator_info_missing_max_duration`); no init and no upload.
- **Unaudited, private-only creator:** `privacy_level_options` of just `SELF_ONLY`. Any other bound level is refused before the claim; `SELF_ONLY` is sent as `SELF_ONLY` and, with no public post id, ends in `needs_review`, never `published`.

They show ReachWeaver's handling of these shapes only; they are not evidence of a TikTok app, an audit or an upload.

## Sources (official, accessed 2026-10-09)

- Upload (inbox) reference and get started: https://developers.tiktok.com/doc/content-posting-api-reference-upload-video, https://developers.tiktok.com/doc/content-posting-api-get-started-upload-content
- Direct Post reference and get started (audit, private viewing): https://developers.tiktok.com/doc/content-posting-api-reference-direct-post, https://developers.tiktok.com/docs/en/content-posting-api-get-started
- Post status: https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status
- Creator info: https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info
- Media transfer guide (chunks, video limits): https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide
- Content Sharing Guidelines: https://developers.tiktok.com/doc/content-sharing-guidelines
- User info: https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info
- Token lifetimes: https://developers.tiktok.com/doc/oauth-user-access-token-management

**Not verified:** the accepted chunk Content-Types beyond `video/mp4`, the exact byte value of "MB", the shape of `open_id`, upload hosts other than `*.tiktokapis.com`, whether an inbox init alone counts toward the 5-pending-share cap, and the public video URL format. Each one fails closed or is documented as unverified.
