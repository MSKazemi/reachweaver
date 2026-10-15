# YouTube manual export checklist

Status: `manual_export_not_published`

MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on YouTube. This package is not evidence that a post exists. The account holder decides whether to post it, in the platform's own app.

## Warnings

- no media manifest supplied: video not validated; attach the file yourself and run media-check before posting

## Exact text

Title (25 characters):

```text
ReachWeaver in 60 seconds
```

Description (28 UTF-8 bytes):

```text
A short tour of ReachWeaver.
```

## Hashtags, mentions and links

- Hashtags (0): none
- Mentions (0): none
- Links: none
- Description URLs are clickable only for channels that meet YouTube's requirements (for example verification or Advanced Features).

## Decisions only you can make (nothing is pre-selected)

- [ ] `privacy_status`: Choose the visibility in YouTube Studio. Options: private / unlisted / public. ReachWeaver never pre-selects visibility. Check the actual visibility in Studio after upload.
- [ ] `made_for_kids`: Is this video made for kids? (legal declaration; COPPA) Options: yes, made for kids / no, not made for kids. Required for every video. Only the creator can make this declaration.
- [ ] `altered_or_synthetic_content`: Does the video contain realistic altered or synthetic content that must be disclosed? Options: yes / no.

## Checklist

- [ ] Verify every claim against its cited source (campaign-check / claims-check); this package does not prove anything is true.
- [ ] Signed in to the intended YouTube account: (not supplied)
- [ ] You own or manage the YouTube channel and upload through YouTube Studio
- [ ] Paste title.txt as the title and description.txt as the description, exactly.
- [ ] Run media-check on the media you attach (alt text, captions, rights, limits).
- [ ] Make each decision yourself in the native app: privacy_status, made_for_kids, altered_or_synthetic_content. Nothing is pre-selected.
- [ ] Confirm rights, consent and disclosure for all text and media.
- [ ] If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver records nothing from this package.

## Why this is manual

Registry status `requires-review`, adapter `none`, verified 2026-10-08. youtube video is api_gated; publish refused (fail closed)

## Sourced platform facts

- `title_max_chars`: 100 (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos
- `description_max_bytes`: 5000 (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos
- `forbidden_characters`: ['<', '>'] (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos - Title and description accept all valid UTF-8 characters except < and >.
- `tags_max_chars`: 500 (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos - Commas between tags count; a tag containing a space counts as if wrapped in quotation marks.
- `privacy_status_values`: ['private', 'unlisted', 'public'] (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos - If the API request omits it, videos.insert defaults to public. ReachWeaver never relies on a default.
- `made_for_kids_declaration`: required (verified, 2026-10-08) https://support.google.com/youtube/answer/9528076 - The creator is legally responsible for the designation (COPPA).
- `altered_or_synthetic_disclosure`: status.containsSyntheticMedia (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos - Lets the channel owner disclose realistic altered or synthetic content.
- `description_links`: clickable only for eligible channels (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos - URLs in the description render as clickable links only when the channel meets platform-level requirements such as verification or Advanced Features.
- `api_upload_scopes`: ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube', 'https://www.googleapis.com/auth/youtubepartner', 'https://www.googleapis.com/auth/youtube.force-ssl'] (verified, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos/insert - videos.insert accepts any one of these scopes.
- `unverified_project_private_lock`: conflicting (conflicting, 2026-10-08) https://developers.google.com/youtube/v3/docs/videos/insert - The 2020-07-28 revision-history entry says videos.insert uploads from unverified API projects created after 28 July 2020 are restricted to private viewing until an audit. The videos.insert page (last updated 2026-10-08) says such uploads are not restricted to private viewing mode. Treated as unknown. It concerns API uploads only, not uploads made by the owner in YouTube Studio. Sources: https://developers.google.com/youtube/v3/docs/videos/insert ; https://developers.google.com/youtube/v3/revision_history
