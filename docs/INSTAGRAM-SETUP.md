# Instagram setup: from zero to a ready account (owner steps)

Instagram posting in ReachWeaver is **disabled** until the owner records a supervised live receipt ([INSTAGRAM.md](INSTAGRAM.md)). This page lists what only you can prepare. Until then, use `manual-export --platform instagram` ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)). Meta's dashboards change; the steps below name what is needed, not exact button labels. Check the official [get started guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started) for the current screens.

Never paste a token into chat, a draft, a file in the repository or a shell history line you will share.

## Checklist

1. **Professional account.** The Instagram account must be Business or Creator. A personal account is refused by the identity check (`account_type`).
2. **Meta developer app** of the **Business** type with the Instagram API using Instagram Login, and the two permissions under [Scopes](#scopes-permissions-to-grant). Add your Instagram account to the app (App Dashboard) so a dashboard token works; Standard Access is enough for your own account ([Eligibility](#eligibility-app-review-and-business-verification-read-2026-10-15)).
3. **Token.** Generate an Instagram user access token in the dashboard. It is valid about 60 days. Put it in `REACHWEAVER_IG_ACCESS_TOKEN` in your own terminal or a private credentials file, never in the repository.
4. **`ig_user_id`.** The `user_id` returned by `GET /me` for that token. Put it, your `handle` and `authorization_expires_at` in the ignored `config/accounts.json` (example in [INSTAGRAM.md](INSTAGRAM.md#account-configuration-ignored-configaccountsjson-no-secrets)).
5. **Image host.** A public HTTPS URL on a plain path to a JPEG (at most 8,000,000 bytes, aspect ratio 4:5 to 1.91:1, no query string, no redirect). ReachWeaver hosts nothing.
6. **Check offline.** `reachweaver doctor` shows the account, the missing variable names (never values) and the expiry warning.

## Scopes (permissions) to grant

ReachWeaver's adapter uses the **Instagram API with Instagram Login** (Business Login for Instagram, host `graph.instagram.com`). Grant exactly these two permissions to your app, and no others:

| Permission | Why | Source (read 2026-10-15) |
|---|---|---|
| `instagram_business_basic` | `GET /me` identity check (`user_id`, `username`, `account_type`) | [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing), [content_publishing_limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit) |
| `instagram_business_content_publish` | create the container, `media_publish`, read the quota | same |

The permissions reference lists `instagram_business_basic` as the only dependency of `instagram_business_content_publish`. The older scope names `business_basic` and `business_content_publish` were replaced: Meta says "the old scope values will be deprecated on January 27, 2025" ([Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login)). Do not request comment, messaging or insights permissions; ReachWeaver does not use them.

## Instagram Login versus Facebook Login (dated comparison, 2026-10-15)

Meta offers two setups for the same publishing endpoints. ReachWeaver uses Instagram Login. Every row was read on 2026-10-15 from the official pages linked below and checked with `curl` and `grep`.

| | Instagram API with **Instagram Login** (ReachWeaver) | Instagram API with **Facebook Login** |
|---|---|---|
| Who logs in | "Your app serves Instagram professional accounts with a presence on Instagram only"; users sign in with Instagram credentials | The professional account must be connected to a Facebook Page; users sign in with Facebook credentials |
| Facebook Page | Not needed: "This API setup does not require a Facebook Page to be linked to the Instagram professional account." | Required. The user must be able to perform `MANAGE` or `CREATE_CONTENT` on the Page. A Page that requires Page Publishing Authorization blocks publishing until it is completed |
| Token | Instagram User access token | Facebook User or Page access token |
| Host | `graph.instagram.com` | `graph.facebook.com` |
| Publishing permissions | `instagram_business_basic`, `instagram_business_content_publish` | `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` (plus `ads_management`/`ads_read` if the Page role came through Business Manager). The permissions reference also lists `pages_show_list` as a dependency of `instagram_content_publish` |
| Token lifetime | Dashboard token: "Access tokens from the App Dashboard are long-lived and are valid for 60 days." Login-flow token: 1 hour, exchangeable for 60 days. A long-lived token can be refreshed for another 60 days once it is "at least 24 hours old" and still valid; "Tokens that have not been refreshed in 60 days will expire and can no longer be refreshed." | Short-lived 1 hour, long-lived 60 days (overview, same text for both login types) |
| Not available | "This API setup cannot access ads or tagging." Hashtag search, product tagging and partnership ads are Facebook Login only | — |

**Why ReachWeaver uses Instagram Login:** it needs no Facebook Page, so there is no Page role, Page Publishing Authorization or Page token to manage; it needs two permissions instead of three or more; and for an owner-only app Meta says "you do not need to implement a login flow", because the dashboard issues a 60-day token. ReachWeaver itself never runs an OAuth flow.

Sources: [overview](https://developers.facebook.com/docs/instagram-platform/overview), [Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login), [get started](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started), [Business Login for Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login), [Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login), [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing), [IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media), [permissions reference](https://developers.facebook.com/docs/permissions).

## Eligibility: App Review and Business Verification (read 2026-10-15)

- **Account type.** Only professional accounts: `account_type` "Can be `Business` or `Media_Creator`." The Facebook Login page states that setup "cannot access Instagram consumer accounts (i.e., non-Business or non-Creator Instagram accounts)", and that content publishing "is available to all Instagram Professional accounts, except Stories, which are only available to business accounts". ReachWeaver posts single feed images, not Stories.
- **Your own account needs no App Review.** "Standard Access is the default access level for all apps", and "If your app only serves your Instagram professional account or an account you manage, Standard Access is all your app needs." The Business Login page says the same: "Standard Access if your app serves Instagram professional accounts you own or manage and have added to your app in the App Dashboard".
- **Other people's accounts need Advanced Access.** Advanced Access is needed to serve accounts you do not own or manage, and "This access level requires App Review and Business Verification." Also: "Your app must complete Meta App Review to be granted Advanced Access" and "You must complete Business Verification if your app requires Advanced Access". ReachWeaver is built for the owner's own account, so the pilot does not need either.
- **App type.** The get started page warns that an app that is not a **Business** type app has to be replaced by a new app created as Business type, and that App Review is required if that new app needs Advanced Access.
- **Still caveated by Meta.** "Because of the limited scope of Standard Access, some features might not work properly until your app has been granted Advanced Access." Whether this affects single-image publishing is **not verified**; only a supervised live run would show it.

**Still not known:** how long App Review or Business Verification takes, and whether a personal (non-business) developer can complete Business Verification. Neither is needed for the owner-only pilot. Record anything you find in the issue so this page can be corrected.

## Then

After the checklist, the first live post follows the supervised-pilot pattern in [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md): a throwaway or low-stakes post, you approve the exact caption and image, and the result is recorded as a receipt. Passing this checklist is not consent to publish.
