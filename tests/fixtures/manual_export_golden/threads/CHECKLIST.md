# Threads manual export checklist

Status: `manual_export_not_published`

MANUAL EXPORT ONLY. ReachWeaver has not sent, uploaded, scheduled or shared anything on Threads. This package is not evidence that a post exists. The account holder decides whether to post it, in the platform's own app.

## Exact text

```text
ReachWeaver ships offline manual export. #opensource
```

## Hashtags, mentions and links

- Hashtags (1, limit 1): #opensource
- Mentions (0): none
- Links: none
- At most 5 links per post.
- Links stay as typed; ReachWeaver adds none.

## Checklist

- [ ] Verify every claim against its cited source (campaign-check / claims-check); this package does not prove anything is true.
- [ ] Signed in to the intended Threads account: (not supplied)
- [ ] You control a Threads profile; posting happens in the Threads app or website
- [ ] Paste caption.txt into Threads exactly; do not let tools add text, links or watermarks.
- [ ] Confirm rights, consent and disclosure for all text and media.
- [ ] If you choose to post it, keep the native post URL as first-party evidence. ReachWeaver records nothing from this package.

## Why this is manual

Registry status `requires-review`, adapter `none`, verified 2026-10-08. threads text is api_gated; publish refused (fail closed)

## Sourced platform facts

- `text_max_chars`: 500 (verified, 2026-10-08) https://developers.facebook.com/docs/threads/posts - Emojis are counted as their UTF-8 byte length; ReachWeaver counts emoji-like code points by UTF-8 bytes (conservative).
- `max_links`: 5 (verified, 2026-10-08) https://developers.facebook.com/docs/threads/posts
- `topic_tags_per_post`: 1 (verified, 2026-10-08) https://developers.facebook.com/docs/threads/posts
- `topic_tag_length`: [1, 50] (verified, 2026-10-08) https://developers.facebook.com/docs/threads/posts
- `api_permissions`: ['threads_basic', 'threads_content_publish'] (verified, 2026-10-08) https://developers.facebook.com/docs/threads/get-started - threads_basic is required for all endpoints; threads_content_publish for publishing endpoints.
- `app_review`: App Review approval of each permission, with the app released, before users without an app role can grant it; invited Threads testers can grant it earlier (verified, 2026-10-08) https://developers.facebook.com/docs/threads/get-started
- `account_eligibility`: unknown (unknown, 2026-10-08) https://developers.facebook.com/docs/threads/get-started - The fetched page does not state account-type requirements for publishing.
- `media_constraints`: unknown (unknown, 2026-10-08) https://developers.facebook.com/docs/threads/posts - No Threads table in reachweaver/media.py yet; media-check skips Threads limits with a warning.
