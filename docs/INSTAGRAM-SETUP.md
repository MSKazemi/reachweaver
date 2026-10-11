# Instagram setup: from zero to a ready account (owner steps)

Instagram posting in ReachWeaver is **disabled** until the owner records a supervised live receipt ([INSTAGRAM.md](INSTAGRAM.md)). This page lists what only you can prepare. Until then, use `manual-export --platform instagram` ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)). Meta's dashboards change; the steps below name what is needed, not exact button labels. Check the official [get started guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started) for the current screens.

Never paste a token into chat, a draft, a file in the repository or a shell history line you will share.

## Checklist

1. **Professional account.** The Instagram account must be Business or Creator. A personal account is refused by the identity check (`account_type`).
2. **Meta developer app** with the Instagram API using Instagram Login. Add your Instagram account as a tester or app role so a dashboard token works.
3. **Token.** Generate an Instagram user access token in the dashboard. It is valid about 60 days. Put it in `REACHWEAVER_IG_ACCESS_TOKEN` in your own terminal or a private credentials file, never in the repository.
4. **`ig_user_id`.** The `user_id` returned by `GET /me` for that token. Put it, your `handle` and `authorization_expires_at` in the ignored `config/accounts.json` (example in [INSTAGRAM.md](INSTAGRAM.md#account-configuration-ignored-configaccountsjson-no-secrets)).
5. **Image host.** A public HTTPS URL on a plain path to a JPEG (at most 8,000,000 bytes, aspect ratio 4:5 to 1.91:1, no query string, no redirect). ReachWeaver hosts nothing.
6. **Check offline.** `reachweaver doctor` shows the account, the missing variable names (never values) and the expiry warning.

## What is not known yet

Whether Meta requires app review or business verification for your account type is not verified here. If it does, the pilot waits for it. Record what you find in the issue so this page can be corrected.

## Then

After the checklist, the first live post follows the supervised-pilot pattern in [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md): a throwaway or low-stakes post, you approve the exact caption and image, and the result is recorded as a receipt. Passing this checklist is not consent to publish.
