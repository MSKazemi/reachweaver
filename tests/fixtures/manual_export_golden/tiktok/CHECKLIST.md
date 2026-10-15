# TikTok manual export checklist

Status: `manual_export_not_published`

MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on TikTok. This package is not evidence that a post exists. The account holder decides whether to post it, in the platform's own app.

## Warnings

- no media manifest supplied: video not validated; attach the file yourself and run media-check before posting

## Exact text

```text
How ReachWeaver checks captions #devtools
```

## Hashtags, mentions and links

- Hashtags (1): #devtools
- Mentions (0): none
- Links: none
- Do not rely on caption URLs being tappable (unverified). Use your profile link.

## Decisions only you can make (nothing is pre-selected)

- [ ] `privacy_level`: Who can view this video? Pick it yourself in TikTok; there is no default. Options: PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / FOLLOWER_OF_CREATOR / SELF_ONLY. Only the options your account offers apply. ReachWeaver never pre-selects public. Content Posting API audit status: unknown. Unaudited API clients are limited to private viewing; ReachWeaver has no TikTok API client and this package is for the TikTok app.
- [ ] `interactions`: Allow comments, Duets and Stitches? Options: allow / do not allow. TikTok's guidelines for API clients start these unchecked.
- [ ] `commercial_content`: Is this commercial content? Options: no / your brand / branded content (paid partnership). Branded content cannot be private on TikTok.
- [ ] `ai_generated_label`: Does the video contain AI-generated content that needs a label? Options: yes / no.

## Checklist

- [ ] Verify every claim against its cited source (campaign-check / claims-check); this package does not prove anything is true.
- [ ] Signed in to the intended TikTok account: (not supplied)
- [ ] You are the creator of the TikTok account and post in the TikTok app; the caption stays editable and no watermark is added
- [ ] Paste caption.txt into TikTok exactly; do not let tools add text, links or watermarks.
- [ ] Run media-check on the media you attach (alt text, captions, rights, limits).
- [ ] Make each decision yourself in the native app: privacy_level, interactions, commercial_content, ai_generated_label. Nothing is pre-selected.
- [ ] Confirm rights, consent and disclosure for all text and media.
- [ ] If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver records nothing from this package.

## Why this is manual

Registry status `requires-review`, adapter `none`, verified 2026-10-09. tiktok video is api_gated; publish refused (fail closed)

## Sourced platform facts

- `caption_max_utf16`: 2200 (verified, 2026-10-08) https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- `privacy_level_options`: ['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'FOLLOWER_OF_CREATOR', 'SELF_ONLY'] (verified, 2026-10-08) https://developers.tiktok.com/doc/content-posting-api-reference-direct-post - An API value must match one of the creator's privacy_level_options from creator_info.
- `privacy_no_default`: True (verified, 2026-10-08) https://developers.tiktok.com/doc/content-sharing-guidelines
- `interaction_defaults_off`: True (verified, 2026-10-08) https://developers.tiktok.com/doc/content-sharing-guidelines
- `unaudited_client_visibility`: private viewing mode only (verified, 2026-10-08) https://developers.tiktok.com/doc/content-posting-api-get-started - An audit is required to lift the restriction. Applies to Content Posting API clients, not to the TikTok app itself.
- `api_permissions`: ['video.publish'] (verified, 2026-10-08) https://developers.tiktok.com/doc/content-posting-api-get-started - Direct Post requires approval for the video.publish scope and the user's authorization. Draft-upload scope names were not on the fetched page.
- `caption_editable_no_watermark`: True (verified, 2026-10-08) https://developers.tiktok.com/doc/content-sharing-guidelines - Preset caption text and hashtags must stay editable; API clients must not add promotional watermarks or logos.
- `caption_links_clickable`: unknown (unknown, 2026-10-08) https://developers.tiktok.com/doc/content-posting-api-reference-direct-post - No official statement found. Do not rely on a caption URL being tappable.
