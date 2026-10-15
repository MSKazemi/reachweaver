# YouTube video and Shorts uploads (issue #22, owner-approved, disabled)

**Status:** owner-approved 2026-10-09; disabled until a live receipt (proposal, every recommended decision). The adapter is mock-tested only. **It cannot upload anything today**: the bundled capability registry keeps YouTube's `reachweaver_video_upload` switch `off` (with video `api_gated`), and `policy.SUPPORTED_LIVE_VIDEO` is empty, so `publish --live` prints `SKIPPED ... requires manual publishing` before any credential is read. No owner channel has been connected and no video has been uploaded. Until the owner enables it, upload in YouTube Studio from a [`manual-export`](MANUAL-EXPORT.md) package.

## What the draft does

1. `youtube-attach POST_ID --file CLIP.mp4 --title T --made-for-kids yes|no --category-id N [--privacy private|unlisted|public] [--tag T]...` on a **YouTube draft** (offline). The post text is the video **description**. The file must be an MP4 (checked from its header by `media.py`; QuickTime `.mov` is refused), a regular file and not a symlink. ReachWeaver records its absolute path, SHA-256 and size. `--made-for-kids` has no default: a person must answer it. `--privacy` defaults to `private`. The output includes an advisory Shorts hint.
2. `show POST_ID` prints the video fields and re-hashes the file (`file_status`). `approve` refuses when the file no longer matches, and otherwise binds the video approval payload (binding tag `reachweaver.storage.approval/video-1`): SHA-256, size, MIME, title, tags, privacy, made-for-kids and category, plus the `approval/1` text fields (the description is `content`). Changing any of these afterwards blocks the send until re-approval. Restoring the original bytes unblocks it.
3. `publish --live`, once enabled, re-hashes the file before the claim, checks the token's channel, claims the post, re-hashes again, and uploads with the resumable protocol. A file changed during the upload is detected before the final chunk, so the upload never completes with other bytes.
4. `youtube-detach POST_ID` removes the video from a draft.

## Account configuration (no secrets)

```json
{"id": "my-youtube", "platform": "youtube", "env_prefix": "REACHWEAVER_MY_YT", "handle": "MyChannel",
 "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx"}
```

- `channel_id` is your own channel id (`UC` + 22 characters, the observed shape; Google does not document it). Without `channel_id` the account is manual-export metadata and needs no token.
- The OAuth access token comes only from `{env_prefix}_ACCESS_TOKEN` (environment or `$CREDENTIALS_DIRECTORY`). Obtain it with **your own** Google OAuth client and the `https://www.googleapis.com/auth/youtube.upload` scope. There is **no refresh flow**: the token must be valid when the send runs. Never put a client secret or refresh token in `accounts.json` (it is refused).

## Official facts used (read 2026-10-09)

| Fact | Value | Source |
|---|---|---|
| Upload endpoint | `POST https://www.googleapis.com/upload/youtube/v3/videos`, `uploadType=resumable`, `part` | [resumable protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol) |
| Session start | `X-Upload-Content-Length`, `X-Upload-Content-Type` (`video/*` or `application/octet-stream`), JSON metadata; `200 OK` + `Location` session URI | same |
| Chunks | multiple of 256 KB (last may be smaller), `Content-Range: bytes F-L/T`, `308 Resume Incomplete` + `Range`, final `201 Created` | same |
| Interruption | resume via empty `PUT` with `Content-Range: bytes */T`; 500/502/503/504 resumable; 404 = session expired; session URI has a "finite lifetime" | same |
| File limits | 256GB; `video/*`, `application/octet-stream` | [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) |
| Title / description / tags | 100 characters / 5000 bytes, both without `<` `>` / 500 characters total (commas count; a tag with a space counts as quoted) | [video resource](https://developers.google.com/youtube/v3/docs/videos) |
| Privacy | `private`, `public`, `unlisted`; **defaults to public if omitted** | same |
| Made for kids | writable `status.selfDeclaredMadeForKids`; "Regardless of your location, we require you to tell us whether or not your videos are made for kids" | same; [YouTube Help](https://support.google.com/youtube/answer/9528076) |
| Quota | `videos.insert` has its own bucket: default 100 calls/day at 1 unit each (since 2026-06-01); `channels.list` 1 unit from the shared 10,000/day; reset at midnight Pacific Time | [quota costs](https://developers.google.com/youtube/v3/determine_quota_cost), [revision history](https://developers.google.com/youtube/v3/revision_history) |
| Errors | `quotaExceeded`, `rateLimitExceeded`, `dailyLimitExceeded`, `userRateLimitExceeded` (403); `authError`, `unauthorized` (401) | [core errors](https://developers.google.com/youtube/v3/docs/core_errors) |
| Identity | `GET /youtube/v3/channels?part=id&mine=true` (1 unit) | [channels.list](https://developers.google.com/youtube/v3/docs/channels/list) |
| Shorts | up to 3 minutes, square or vertical (uploads after 2024-10-15); classified automatically, **no API flag** | [YouTube Help](https://support.google.com/youtube/answer/15424877) |

**Conflict, unresolved.** The revision history (2020-07-28 entry) says uploads through `videos.insert` from unverified API projects created after 2020-07-28 "will be restricted to private viewing mode" until the project passes an audit; no later entry there lifts it. The current `videos.insert` page says "Videos uploaded from unverified API projects are not restricted to private viewing mode." ReachWeaver assumes the restriction may apply: it checks the returned `privacyStatus`, and a `public` request that comes back `private` goes to `needs_review` as `youtube_receipt_privacy_mismatch_uncertain`. Earlier quota figures (about 1600 units, then about 100 units per upload on 2025-12-04) are superseded by the 2026-06-01 bucket model above.

**Not verified:** the channel and video id shapes, the public watch URL `https://www.youtube.com/watch?v=ID` (stored as a convenience; the video id is the receipt), and whether the session-initiation POST alone can create a video (the protocol returns `201 Created` only on the final chunk, so ReachWeaver treats initiation as creating nothing).

## What the publish output says about visibility

A finished upload is not a public video. After `PUBLISHED <id>: <watch url>`, `publish` prints a second line for every YouTube post, taken from the privacy you approved:

- `private` or `unlisted`: `NOTE <id>: YouTube privacy_status=private: the upload succeeded, but the video is NOT public; change its visibility yourself in YouTube Studio when you want it public`.
- `public`: `NOTE <id>: YouTube privacy_status=public, as approved`. The receipt must also say `public`; otherwise the post goes to `needs_review` and is never reported as published.
- `public` with a receipt `uploadStatus` of `uploaded` (YouTube has the file but has not finished processing; `processed` means done, per the [Video resource](https://developers.google.com/youtube/v3/docs/videos), checked 2026-10-15): the same line continues `; upload_status=uploaded: YouTube has the file but processing is not confirmed yet, so the video may not play for viewers; check it in YouTube Studio, or read status.uploadStatus later with videos.list part=status ('processed' means done). ReachWeaver does not re-check it`. ReachWeaver makes no later status read itself; [`videos.list`](https://developers.google.com/youtube/v3/docs/videos/list) with `part=status` (checked 2026-10-15) is the Data API call that returns it.

The line only adds information: it changes no state and cannot fail the send (a malformed stored record prints no note). `youtube-analytics` repeats the same `privacy_status` from the receipt. Tests: `tests/test_youtube_visibility.py`, which also feeds an adapter receipt through the sampling report and checks that every metric stays `unknown`, never zero, until you import real numbers.

## Analytics sampling report

`reachweaver youtube-analytics [--account ID] [--post-id ID]` is read-only and offline. It opens the database with `storage.connect_readonly` (never created, never migrated; an older schema exits 2 and names the gap), reads the metric snapshots you already imported (`metrics` or `import-metrics`, from a native Studio export) and prints JSON. It fetches nothing from YouTube and has no network path, so a missing number stays missing.

Per published YouTube video it repeats the upload receipt (`video_id` = `posts.remote_id`, `watch_url`, and `channel_id`, `privacy_status`, `upload_status` from the `published` audit event; `null` for a post settled by `reconcile --published`, never guessed), then labels each metric of the **latest** snapshot:

| Label | Meaning |
|---|---|
| `exact` | a stored integer count (carries `value`) |
| `sampled` | a platform estimate (stored status `estimated`; carries `value`, shown apart) |
| `suppressed`, `delayed`, `not_available` | the stored provider status, no value |
| `unknown` | no snapshot, or the snapshot has nothing for that metric. **Never zero** |

`latest.window.known` is false for a snapshot without an observation window: its counts may be lifetime-cumulative or cover a provider-defined period. `totals` sums only `exact` values, with `coverage` (`2/3`), `exact_without_window`, label counts, and the `sampled` sum kept apart and never added to `value`. `value` is `null` when no video has an exact count. Totals across snapshots with different or unknown windows are not comparable. Not verified here: which YouTube Studio or Analytics figures are themselves sampled by Google; the report labels what was stored, not how it was measured. Tests: `tests/test_youtube_analytics.py`.

## Failure handling

| When | Result |
|---|---|
| File changed/missing before the claim | `BLOCKED approval_video_changed_reapproval_required`, post stays approved, one audit event |
| Wrong channel, zero or several channels | `BLOCKED ... identity`, no claim |
| Session initiation fails (e.g. `youtube_init_http_403_quotaExceeded`), or its `Location` leaves the upload endpoint | `needs_review`, class `certain_not_sent` (no byte was sent); reconcile `--not-published` and re-approve later |
| Any failure after the first byte: 5xx, network drop, 308 with an unexpected `Range`, pause between chunks, file changed during the upload, receipt with another channel, privacy, made-for-kids or upload status | `needs_review`, class `ambiguous`, code ends in `_uncertain`; **never resumed or resent** |
| 429 | as above, plus the existing provider cooldown |

The session URI is a bearer capability: it is never printed, logged, stored or put in an error. A `needs_review` upload therefore cannot be resumed from ReachWeaver; check YouTube Studio and run `reconcile` (`--published --remote-id VIDEO_ID` if the video exists, else `--not-published`). An abandoned session creates no video.

## Manual fallback: upload in YouTube Studio

Use this whenever the adapter is off (today), an upload sits in `needs_review`, or the API project is unverified. ReachWeaver never uploads in this path; **you** upload in your own browser session.

1. Build the package: `reachweaver manual-export --platform youtube --variant variant.json --out exports/yt-NAME` ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)). Use `title.txt` and `description.txt` verbatim (the description is capped at 5000 bytes, the title at 100 characters, and neither may contain `<` or `>`).
2. In YouTube Studio, choose to upload a video and select your MP4. Paste the title and description from the package.
3. **Answer the audience ("made for kids") question yourself.** The package never pre-selects it, and nothing in ReachWeaver answers it for you.
4. Set the visibility yourself. The safe first choice is `Private`; switch to `Unlisted` or `Public` only after you have checked the processed video. The API-project private-viewing restriction (see the unresolved conflict under Official facts) concerns API uploads; whether anything similar affects a Studio upload is not verified here, so check the visibility Studio shows after processing.
5. Add tags and a category if the package lists them, then finish the upload in Studio. Studio menu labels change over time and were not verified for this document; follow what the page shows.
6. Record the result: the video id is the part after `v=` in the video's watch URL. If the post exists in the ReachWeaver outbox, run `reachweaver reconcile` with `--published --remote-id VIDEO_ID` once the video is visible in Studio, or `--not-published` if you abandoned it. Never claim a view or reach number that Studio did not show you.

A failed or interrupted API upload leaves no resumable session in ReachWeaver (see the table above), so a Studio upload is the recovery path; first check Studio for a half-processed copy to avoid a duplicate.

## Enabling (owner only, after review)

Do not enable from an agent session. The owner (1) completes the decisions in the proposal, (2) has an OAuth client whose project passed Google's verification and the YouTube API audit if public uploads are wanted, (3) sets `"reachweaver_video_upload": "live"`, `video: "api_direct"` and sourced `rate_limits.facts` on the YouTube registry entry and adds `"youtube"` to `policy.SUPPORTED_LIVE_VIDEO`, (4) runs one supervised `private` upload with `publish --live --post-id ID` and records the native receipt per [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md).

## Offline fixtures

`tests/fixtures/providers/youtube/` holds six **synthetic, hand-written** responses (not recordings): a 403 `forbidden` and a 404 on a middle chunk, a `308` without a `Range`, a 500 and a `308` on the final chunk, and a receipt that returns `private` for a public request. `ChunkUploadFixtureTests` (`tests/test_youtube.py`) replays them against the temporary flipped registry and pins that each goes to `needs_review` as `ambiguous` with a code ending in `_uncertain`, sends nothing further, is not resumed by a second run, and never puts the token or session URI in an error. They show how ReachWeaver handles those shapes only; they are not evidence of a connected channel or an upload.
