# Facebook Pages manual export checklist

Status: `manual_export_not_published`

MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on Facebook Pages. This package is not evidence that a post exists. The account holder decides whether to post it, in the platform's own app.

## Exact text

```text
ReachWeaver release notes for our Page.
```

## Hashtags, mentions and links

- Hashtags (0): none
- Mentions (0): none
- Links: none
- Links in Page posts stay as typed; ReachWeaver adds none.

## Checklist

- [ ] Verify every claim against its cited source (campaign-check / claims-check); this package does not prove anything is true.
- [ ] Signed in to the intended Facebook Pages account: (not supplied)
- [ ] Target is a Facebook Page you manage (declared: page); never a personal profile or group
- [ ] Paste caption.txt into Facebook Pages exactly; do not let tools add text, links or watermarks.
- [ ] Confirm rights, consent and disclosure for all text and media.
- [ ] If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver records nothing from this package.

## Why this is manual

Registry status `requires-review`, adapter `none`, verified 2026-10-08. facebook text is api_gated; publish refused (fail closed)

## Sourced platform facts

- `target`: Facebook Page only (verified, 2026-10-08) https://developers.facebook.com/docs/pages-api/posts - Page posts use a Page access token from a person who can perform CREATE_CONTENT, MANAGE and MODERATE tasks on the Page.
- `api_permissions`: ['pages_manage_engagement', 'pages_manage_posts', 'pages_read_engagement', 'pages_read_user_engagement', 'publish_video (video only)'] (verified, 2026-10-08) https://developers.facebook.com/docs/pages-api/posts
- `personal_profile`: not supported (verified, 2026-10-08) https://developers.facebook.com/docs/graph-api/reference/user/feed - The User feed reference states that creating a post is not possible on this endpoint.
- `groups`: not supported (verified, 2026-10-08) https://developers.facebook.com/docs/graph-api/changelog/version19.0 - Groups API permissions, including publish_to_groups, were removed from all versions on 2024-04-22.
- `text_max_chars`: unknown (unknown, 2026-10-08) https://developers.facebook.com/docs/pages-api/posts - Not stated on the fetched page; ReachWeaver applies its conservative cap from policy.py instead.
- `media_constraints`: unknown (unknown, 2026-10-08) https://developers.facebook.com/docs/pages-api/posts - No Facebook table in reachweaver/media.py yet.
