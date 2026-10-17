# Which platforms can post automatically for free

ReachWeaver pays for no API and adds no model charges: Claude does the writing inside your own Claude Code plan ([how that works](CLAUDE-CODE.md)). This page answers, for each platform, one question: **can an approved post go out automatically through a free official API, or do you post it yourself?**

"Automatically" still means you approve each exact post first (`approve`) and start the send (`publish --live`). ReachWeaver never posts without that.

The facts below come from the official pages recorded in [PLATFORMS.md](PLATFORMS.md) and [the platform matrix](PLATFORM-MATRIX.md), read between 2026-10-08 and 2026-10-15. Platforms change prices and rules without notice: check the linked page again before you connect an account.

## The short answer

| | Platforms |
|---|---|
| **Free API, ReachWeaver can send today** (text posts) | Bluesky, Mastodon, Telegram channel, LinkedIn personal profile |
| **Free API, ReachWeaver adapter built but switched off** until your first supervised test post | Threads, Instagram (professional account), Facebook Page, YouTube, TikTok |
| **API costs money**, so ReachWeaver does not use it | X, Hashnode |
| **No ReachWeaver send path**: you post by hand from a ready package | Instagram personal account, Facebook profile or group, Pinterest (unless approved), DEV, Medium and the other writing and community sites |
| **Human only**: no tool writes or posts for you | Reddit, Hacker News, Lobsters and other community channels |

Paying nothing for APIs is therefore possible: every platform ReachWeaver can send to uses a free API. The two paid APIs (X and Hashnode) and the platforms without an API get a reviewed manual package instead (`manual-export`), which you post in the native app or site, often with its own free scheduler.

## Platform by platform

| Platform | Free official posting API? | ReachWeaver today | What you set up once | If there is no free API |
|---|---|---|---|---|
| Bluesky | Yes, free (App Password) | Sends text; one real test post on 2026-10-11 ([receipt](receipts/bluesky-2026-10-11.json)) | An App Password ([account onboarding](ACCOUNT-ONBOARDING.md)) | — |
| Mastodon | Yes, free (your server's API) | Sends text; one real test post on 2026-10-11 ([receipt](receipts/mastodon-2026-10-11.json)) | A token with `write:statuses` ([Mastodon](MASTODON.md)) | — |
| Telegram channel | Yes, free (Bot API) | Sends text; tested with simulated servers only | A bot that is admin of your channel | — |
| LinkedIn personal profile | Yes, free (Share on LinkedIn is self-serve) | Sends text; tested with simulated servers only | Your own LinkedIn developer app ([LinkedIn](LINKEDIN.md)) | — |
| LinkedIn company page | A separate organization permission (`w_organization_social`); not checked for cost | Refused: ReachWeaver posts as a person only | — | `manual-export`, or LinkedIn's own composer |
| Threads | Yes, free; your own account works as a tester of your own app | Adapter built, switched off | A Meta app with the Threads use case ([Threads](THREADS.md)) | `manual-export` until switched on |
| Instagram | Yes, free, for a **professional** (Business or Creator) account; Standard Access covers your own account | Single-image adapter built, switched off | A Meta app with Instagram Login ([setup](INSTAGRAM-SETUP.md)) | Personal accounts: `manual-export` |
| Facebook Page | Yes, free (Graph API); App Review only for people without a role on your app | Text and link adapter built, switched off | A Meta app and a Page token ([Facebook](FACEBOOK.md)) | Profiles and groups: no API, `manual-export` |
| YouTube | Yes, free (Data API; `videos.insert` has its own default of 100 calls a day) | Upload adapter built, switched off | A Google Cloud project with the upload scope; uploads from an unverified project may be forced private ([YouTube](YOUTUBE.md)) | `manual-export`, YouTube Studio scheduler |
| TikTok | Free, but needs TikTok's approval and an app audit; unaudited apps can only post privately | Creator-inbox adapter built, switched off | A TikTok developer app ([TikTok](TIKTOK.md)) | `manual-export` |
| X | **No: pay-per-use**, about $0.015 per post and $0.20 per post with a link (official pricing page, re-check before use) | No adapter | — | `campaign-export` text, then X's own scheduler (eligible accounts) |
| Pinterest | No fee documented, but needs app approval; apps registered from 2026-09-14 are refused for non-business accounts | No adapter | — | Post by hand with your own image |
| Hashnode | **No: needs the paid Pro plan** for API publishing | No adapter | — | `campaign-export` Markdown, native editor |
| DEV Community | Has an API, but ReachWeaver keeps DEV human-authored | No adapter | — | `campaign-export` Markdown, native editor and scheduler |
| Reddit | Human only | Never posts, comments, votes or messages | — | You write and post it yourself |
| Hacker News | Human only | Never drafts or posts | — | You write and post it yourself |

"Switched off" means the code exists and is tested with simulated servers, but the platform registry keeps it unreachable. Switching one on is your decision: you run one supervised test post on an account you control, keep the receipt, and then tell Claude to enable that platform in a reviewed change ([first live pilot](FIRST-LIVE-PILOT.md)). It costs nothing.

## Why Claude in Chrome does not post for you

It would cost nothing, but on the platforms that need it most it would break the platform's own rules, which puts your account at risk. On 2026-10-16 you recorded the decision **not planned** ([browser-assist decision](BROWSER-ASSIST-DECISION.md)). The readings:

| Platform | What its terms say | Result |
|---|---|---|
| LinkedIn | User Agreement (effective 2025-11-03): members may not "Use bots or other unauthorized automated methods to access the Services" or to "create, comment on, like, share, or re-share posts" | Prohibited, and not needed: the free API covers personal posts |
| TikTok | Terms (updated 2025-12-01): users may not "use automated scripts to collect information from or otherwise interact with the Services" | Prohibited |
| X | Terms of Service (effective 2026-10-09, read 2026-10-17): you may not access the Services "by any means (automated or otherwise) other than through our currently available, published interfaces", and may not "try to work around any technical limitations we impose". The automation rules page returned HTTP 403 | Not cleared: no dated reading permits it |
| Instagram, Threads, Facebook | The official terms page returned no text | Unverified, treated as prohibited; not needed for professional accounts and Pages, which have the free API |
| Reddit, Hacker News | Human-only communities | Never |

A human pressing the final Post button does not fix this for LinkedIn and TikTok, because their clauses cover creating the post by an automated method. If a platform's official terms ever permit assisted composing in your own browser, the decision page describes the small, gated design that would be built; until then the answer for platforms without a free API is the manual package.

Claude can still help a lot there without touching the platform: it writes the post, checks it, prepares the files, alt text and checklist (`manual-export`, or `share` for one caption across several networks, and `share --web` for the same in your browser), and after you post it by hand, records it (`mark-shared`) so the repeat check and your posting journal stay complete.

## What it costs you

| Item | Cost |
|---|---|
| ReachWeaver itself | Free (Apache-2.0) |
| Model calls | None by ReachWeaver; Claude's work counts against your own Claude Code plan |
| Bluesky, Mastodon, Telegram, LinkedIn personal, Threads, Instagram professional, Facebook Page, YouTube API access | Free, as recorded above; developer apps and approvals take your time, not money |
| X API, Hashnode Pro | Paid by the platform; ReachWeaver does not use them |
| Postiz backend | Optional and off unless you opt an account in. Postiz Cloud is a separate hosted service whose pricing ReachWeaver does not record; self-hosting Postiz is your own decision. The free route needs neither |

Related: [recommended setup](RECOMMENDED-SETUP.md), [manual export](MANUAL-EXPORT.md), [easy sharing](EASY-SHARING.md), [browser composer](BROWSER-SHARE-COMPOSER.md), [platform matrix](PLATFORM-MATRIX.md), [account onboarding](ACCOUNT-ONBOARDING.md).
