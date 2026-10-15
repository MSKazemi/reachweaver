# Instagram manual export checklist

Status: `manual_export_not_published`

MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on Instagram. This package is not evidence that a post exists. The account holder decides whether to post it, in the platform's own app.

## Exact text

```text
Release card for ReachWeaver. #opensource #python
```

## Hashtags, mentions and links

- Hashtags (2, limit 30): #opensource, #python
- Mentions (0): none
- Links: none
- Do not rely on caption URLs being tappable (unverified). Put the destination on your profile.

## Checklist

- [ ] Verify every claim against its cited source (campaign-check / claims-check); this package does not prove anything is true.
- [ ] Signed in to the intended Instagram account: (not supplied)
- [ ] Any Instagram account you control can post in the app. A future API adapter would need an Instagram professional account; that is not required for this package
- [ ] Paste caption.txt into Instagram exactly; do not let tools add text, links or watermarks.
- [ ] Attach image card.jpg; enter alt text: Terminal showing ReachWeaver media-check passing on a release card
- [ ] Confirm rights, consent and disclosure for all text and media.
- [ ] If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver records nothing from this package.

## Why this is manual

Registry status `requires-review`, adapter `none`, verified 2026-10-08. instagram image is api_gated; publish refused (fail closed)

## Sourced platform facts

- `caption_max_chars`: 2200 (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- `max_hashtags`: 30 (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- `max_mentions`: 20 (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- `media_required`: True (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/content-publishing - Container creation takes image_url or video_url; text-only posts are not part of the content publishing API.
- `api_account_eligibility`: Instagram professional account (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/content-publishing - Applies to API publishing. The fetched pages do not split Business vs Creator.
- `api_permissions`: {'instagram_login': ['instagram_business_basic', 'instagram_business_content_publish'], 'facebook_login': ['instagram_basic', 'instagram_content_publish', 'pages_read_engagement']} (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/content-publishing
- `alt_text`: up to 1000 characters; single images and carousel images only; not Reels or stories (verified, 2026-10-08) https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media - Enforced by reachweaver/media.py CONSTRAINTS['instagram'].
- `caption_links_clickable`: unknown (unknown, 2026-10-08) https://help.instagram.com/362497417173378 - No official statement found. Do not rely on a caption URL being tappable; the Help Center documents a profile website link.
