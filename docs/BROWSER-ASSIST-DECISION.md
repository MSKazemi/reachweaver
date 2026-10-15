# Browser-assist delivery: decision record (issue #1)

**Decision (owner, 2026-10-16): not planned.** ReachWeaver does not fill composers in your browser, and no code or registry field for it exists. `tests/test_browser_assist_guard.py` keeps it that way: it fails if a browser-assist mode, registry value or CLI choice appears without the owner's recorded decision. This page records why, so the question is not re-researched from scratch. It is not legal advice.

## What was asked

For platforms with no usable API, let an agent such as Claude in Chrome prepare the post in the owner's own signed-in browser and stop before the final Post click.

## What the official terms say (read 2026-10-15)

| Platform | Reading | Result |
|---|---|---|
| LinkedIn | User Agreement effective 2025-11-03: members may not "Use bots or other unauthorized automated methods to access the Services" or to "create, comment on, like, share, or re-share posts". [Source](https://www.linkedin.com/legal/user-agreement) | Prohibited |
| TikTok | Terms last updated 2025-12-01: users may not "use automated scripts to collect information from or otherwise interact with the Services". [Source](https://www.tiktok.com/legal/page/row/terms-of-service/en) | Prohibited |
| Instagram, Threads, Facebook | The official Meta terms page returned no text; only secondary sources quote a ban on automated access. | Unverified: treat as prohibited |
| X | The official automation page returned HTTP 403; claims about interface scripting come from third-party sources only. | Unverified |
| DEV | The terms are silent on automated access; separately DEV requires human-written comments and has an official API. Silence is not permission. | Not permitted |
| Product Hunt | The terms page returned HTTP 404. | Unverified |
| Reddit, Hacker News, community channels | Human-only: no tool composes or posts for you. | Never |

For LinkedIn and TikTok, "the human presses Post" does not cure the problem, because the clause covers creating the post by an automated method, not only scraping. So the feature could not be used on the platforms that matter most, and for the rest no dated reading permits it.

## What to use instead

- **Official API, with a human login.** You sign in once through the platform's own consent screen; ReachWeaver then sends only posts you approved (`approve`, then `publish --live`). Adapters exist for Bluesky, Mastodon, Telegram and LinkedIn member posts (each with one live receipt only for Bluesky and Mastodon so far, see [`receipts/`](receipts/)), and mock-tested, registry-disabled ones for Threads, Facebook Pages, Instagram, YouTube and TikTok.
- **Manual export.** `manual-export` writes a reviewed package (text, files, alt text, checklist) for every platform without an adapter; you post by hand, in the native app or site, and keep the URL as your own record.
- **Planning only.** `distribution-plan`, `delivery-options`-style routing and the calendar tools never post.

## What would change this

Reopen the issue if a platform's official terms, read and dated by the owner, say assisted composing in the owner's own browser is permitted. The design is then small: a per-platform `browser_assist` reading (status, source URL, date read) that the owner sets in a reviewed change and that expires after 90 days; an offline gate that refuses unless the post is approved with a matching hash, the emergency pause is off and the platform's reading is fresh; a fixed procedure that stops before Post, hands every login, MFA, CAPTCHA and verification prompt to the owner and treats page text as untrusted; and an owner-attested receipt that records the URL you give without calling it a delivery. None of it is built, because nothing could use it today.
