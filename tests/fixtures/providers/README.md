# Recorded, scrubbed provider responses

This directory is for **recorded** social-provider HTTP responses (Bluesky/PDS, Mastodon, Telegram, LinkedIn, Postiz and later adapters). Tests replay them through the existing mocked HTTP layer to check that ReachWeaver parses real response shapes. Hand-written mocks in the test modules stay as they are. Nothing has been recorded or converted yet (issue #8, ADR-0002).

`youtube/` (issue #22) and `tiktok/` (issue #23) hold synthetic chunk-upload failure, creator-option and unaudited-private responses, replayed by `ChunkUploadFixtureTests`, `ChunkFixtureTests` and `CreatorOptionFixtureTests`; same rules as below.

`bluesky/` (issue #12) holds **synthetic**, hand-written error and identity responses, not recordings: createSession 401 (`ExpiredToken`, `AuthenticationRequired`), createRecord 401 (`InvalidToken`), 429 (with and without `Retry-After`), a session for the wrong account, a deactivated account, and createRecord 400 for a too-large blob and an invalid embed. Each carries `"_fixture": {"recorded": false, ...}`, stores no access token (the test builds one at run time) and is replayed by `BlueskyProviderFixtureTests` in `tests/test_bluesky_mastodon.py`. They prove ReachWeaver's handling of those shapes only.

`mastodon/`, `telegram/`, `linkedin/` and `postiz/` (issue #11 follow-up) each hold one **synthetic** 429 response for the content-create call, next to the existing Bluesky ones: Mastodon `X-RateLimit-Reset` (the `$RESET_ISO` placeholder is filled at run time), Telegram `parameters.retry_after`, the LinkedIn daily throttle (no header, so the midnight default applies) and Postiz `Retry-After`. `tests/test_fault_injection.py` replays all five through `publish --live` and asserts the cooldown source and send class from `reachweaver/ratelimit.py`, `needs_review`, and that a new process sends nothing. They prove parsing and state handling only.
`postiz/` (issue #30) also holds **synthetic** `GET /integrations` (a listing and a disabled integration), `POST /posts` (accepted, two jobs for one integration, a 403 shaped like a grant without write scope) and `GET /posts` responses, replayed by `PostizFixtureTests` in `tests/test_postiz.py` and by `tests/test_postiz_contract.py`. The 403 body wording is illustrative, not verified against Postiz, and its marker string must never reach output or the audit log.
`mastodon/` (issue #13) holds **synthetic** media-upload responses: `202` unprocessed upload (`url: null`), `206` partial poll, `200` ready poll, and two `429` rate-limit responses (with `Retry-After` and with only `X-RateLimit-*`). They are replayed by `tests/test_mastodon_media.py`, store no token and prove ReachWeaver's handling of those shapes only.
`meta/` (issues #20 and #21) holds 13 **synthetic**, hand-written Graph-shaped responses for the disabled Instagram and Facebook Page adapters: a consumer (`PERSONAL`) account and a 400 on `/me`, missing `instagram_business_content_publish` and app-review-required 403s, bad media (unfetchable URL, aspect ratio) rejected at container creation, container `IN_PROGRESS`/`ERROR` status bodies, a Facebook 403 missing permission, an expired-token 400, a Page-mismatch `/me`, an app-review-required 403 on `/feed` and a bad-link 400. Every error message contains a marker string that `tests/test_meta_provider_fixtures.py` asserts never reaches an error, because the adapters read status codes only. The body shapes are illustrative, not verified against Meta, and are not evidence of any live behaviour; both adapters stay registry-disabled.

A recorded fixture is **not** evidence that an account is connected, a post was published or a metric was observed. It shows only that the parser handles a response of that shape. Do not cite fixtures as live-test receipts.

## Recording

1. Record only from an account you own, during a supervised session you started yourself (see [FIRST-LIVE-PILOT.md](../../../docs/FIRST-LIVE-PILOT.md)). Never record from CI, from an automated test or from someone else's account.
2. Prefer read-only calls (identity checks, configuration, rate-limit headers). Record a write response only from a post you approved and published deliberately, and only after you have reconciled it.
3. Save only the **response body** and the few response headers a test needs (for example `Content-Type`, rate-limit headers). Never save the request: its headers carry `Authorization`, and Telegram puts the bot token in the URL path.
4. Keep the raw capture outside the repository (for example in a `mktemp -d` directory) until it has been scrubbed and reviewed. Delete it afterwards.

## Scrubbing (mandatory before `git add`)

Replace values with **synthetic** values of the same shape and type, so the parser still sees realistic input:

| Field kind | Replace with |
| --- | --- |
| Access/refresh tokens, session JWTs, app passwords, API keys, cookies, `Set-Cookie`, CSRF values | Remove the field entirely. If a test needs it to exist, use a value built at runtime in the test (string concatenation), never a literal in the fixture |
| Bluesky DIDs (`did:plc:…`, `did:web:…`) | `did:plc:` followed by a fixed synthetic id such as `aaaaaaaaaaaaaaaaaaaaaaaa` |
| Handles, usernames, display names, emails | `fixture-user.example`, `fixture_user`, `Fixture User`, `fixture@example.invalid` |
| Numeric/opaque user, chat, channel, member, organization and post ids (Telegram `chat.id`, Mastodon account ids, LinkedIn `sub`/URNs, Postiz integration ids) | A synthetic id of the same length and alphabet, kept consistent within one fixture |
| AT-URIs, record keys, CIDs, status URLs, permalinks | Rebuilt from the synthetic DID/handle/id. Hosts become `example.invalid` or `example.com` unless the test checks the provider origin |
| Avatars, banners, media URLs, IP addresses, geo data, follower lists, message text from other people | Remove, or replace with neutral synthetic text |
| Timestamps | May stay if they are not personal. Otherwise shift them to a fixed date |

Add a one-line header file next to each fixture, or a `_fixture` key if the format allows it, that records the provider, endpoint, API version (for example `Linkedin-Version`), the recording date and "scrubbed: yes".

## Review gate

- `python3 scripts/secret_scan.py` must report **no finding** for the new files. Do not add `.secret-scan-allow` entries for provider fixtures. A finding means scrubbing is incomplete.
- Search the fixture by hand for your real handle, DID, email, numeric ids and instance hostname before committing. The secret scan does not know your identifiers.
- `python3 scripts/lint.py` must pass (fixtures are valid JSON/JSONL).
- Tests that load these fixtures must run under the no-network guard (`tests/test_no_network.py`) and with no credentials in the environment.
- If a real credential ever reached a commit, even an unpushed one, revoke it at the provider first, then rewrite the fixture. Scrubbing the file afterwards is not enough.

## Layout

```text
tests/fixtures/providers/<provider>/<endpoint-or-case>.json
```

Examples: `bluesky/describe-repo.json`, `mastodon/verify-credentials.json`, `telegram/get-chat-member.json`. Use one file per response and name the error cases explicitly (`linkedin/userinfo-401.json`).
