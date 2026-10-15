# Mastodon publishing (issue #43)

> **Status:** mock-tested text adapter with one live receipt: a text post sent through ReachWeaver on 2026-10-11, recorded in [mastodon-2026-10-11.json](receipts/mastodon-2026-10-11.json). Everything below was checked against `reachweaver/publishers.py`, `connections.py`, `policy.py`, `accounts.py` and the offline tests; the receipt proves one real send, not the failure and rate-limit behaviour, which stays mock-tested. The two server pages it relies on are [`POST /api/v1/statuses`](https://docs.joinmastodon.org/methods/statuses/) and [media upload](https://docs.joinmastodon.org/methods/media/).

Nothing here changes the human approval, the dry-run default, the emergency pause, credential isolation or ambiguous-send reconciliation. Live sending still needs an approved post, `publish --live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`.

## Account configuration (no secrets)

```json
{"id": "my-mastodon", "platform": "mastodon", "env_prefix": "REACHWEAVER_MASTODON",
 "server": "https://mastodon.social", "handle": "example", "visibility": "public"}
```

| Field | Rule |
|---|---|
| `server` | Required. A public `https` host. Every limit and every request is for this server only. |
| `handle` | The local username (`example`), or `example@your-server` when the part after `@` is exactly the configured server. A handle on another server is refused with `mastodon_expected_handle_must_be_local_to_configured_server` by `connect-check --online` and by the live identity preflight, before any request (the account file itself still loads). |
| `visibility` | Optional: `public` (default), `unlisted` or `private`. `direct` (a direct message) is forbidden. |
| `language` | Optional lowercase ISO 639-1 code such as `en`. |
| `spoiler_text` | Optional, 1 to 200 printable characters. |
| `max_characters` | Optional integer. It can only lower the conservative 450 limit, never raise it: an instance that allows 500 still gets 450 unless you pin less. |
| `max_media_attachments`, `image_size_limit`, `media_description_limit` | Optional. Can only lower the defaults (4 images, 16,000,000 bytes, 1500 characters). |

The credential is `<env_prefix>_TOKEN`, an instance user token with `write:statuses`. Set it in your own terminal, never in a chat or a file in the repository (see [account onboarding](ACCOUNT-ONBOARDING.md)).

## Server capability probe

`connect-check --account my-mastodon --online` makes two read-only requests:

1. `GET <server>/api/v1/accounts/verify_credentials` with your token. The token's username must equal `handle`, otherwise `identity_mismatch`. It proves who the token belongs to; it does **not** prove the `write:statuses` scope.
2. `GET <server>/api/v2/instance` with **no** token (Mastodon 4.0 or later). It reports the instance's own `max_characters`, `max_media_attachments`, `characters_reserved_per_url`, image and video limits and the number of accepted MIME types, next to your `effective_text_limit`.

The command exits 2 when your pinned `max_characters` exceeds the instance value, or when the effective limit does (for example an instance that allows 300 while you have not pinned anything: the default of 450 would be too long). Nothing is saved; you edit the local account file yourself.

## Formats and limits

- **Text.** Counted in code points, with each URL counted as at least 23 characters, against the effective limit above. A URL counted as 23 can never make a post look shorter than it is.
- **Visibility, language, content warning.** From the account configuration, as in the table.
- **Images.** PNG, JPEG or WebP, at most 4 per post, each at most 16,000,000 bytes, with alt text (at most 1500 characters) that you write. GIF and video are not supported. The adapter polls an unprocessed upload a bounded number of times. Image sending is **off**: the capability registry lists no live image format, so `publish --live` refuses an image post before anything is claimed. The offline tools still work: `media-check`, `media-attach` and the approval binding of the file's SHA-256.
- **Replies.** Text only, on your own server only (`reply_server_mismatch` otherwise).

## What the approver sees and what is bound

`show POST_ID` is the offline preview and makes no request. For Mastodon its `platform_preview` lists the exact `server`, `handle`, `visibility`, `language`, `spoiler_text`, `counted_length`, `text_limit` and the `idempotency_key`.

`approve` binds the account, platform, exact text, schedule, source URL and campaign, plus the SHA-256 of any attached image. **It does not bind `visibility`, `language` or `spoiler_text`:** those are read from the account file when the post is sent. If you edit `visibility` after approving, the post is sent with the new value. Run `show` again after any account-file change and before `publish --live`. Binding these fields into the approval would change the approval hash, which needs maintainer security review; it is recorded as an open question, not changed here.

## How a send ends

A send is `POST <server>/api/v1/statuses` (form-encoded) with `Idempotency-Key` set to the local post id. Mastodon remembers an idempotency key for about an hour, so beyond that window the duplicate guard is ReachWeaver's own rule: an uncertain send goes to `needs_review` and is **never retried automatically**.

| Situation | Result |
|---|---|
| Checks before the post is claimed: a missing `<env_prefix>_TOKEN` (`SKIPPED ... configure environment variables`), text over the limit (`SKIPPED ... failed preflight check`), a failed identity preflight (`BLOCKED`; a `429` there is retried at most twice with jitter, then a cooldown is recorded) | Exit 2; nothing is claimed and the post stays approved. A `direct` visibility is refused when the account file is loaded; a handle on another server is refused at the identity preflight (`BLOCKED`) before any request. |
| An attached image file that changed since approval | Caught before the claim (`BLOCKED`, post stays approved). The adapter checks the bytes again just before the write marker; a change there is `needs_review` with class `certain_not_sent`, which is guidance only. |
| `401`, `403`, `4xx` other than 429, `5xx`, a timeout or a network error after the request was handed over | `needs_review`, class `ambiguous`, no automatic retry. |
| `429` | `needs_review`, class `likely_not_created`, and a per-account cooldown computed from `X-RateLimit-Reset` (default 15 minutes) so the next send waits. |
| A server reply without an `id`, without an `https` link, naming another account, or showing another visibility than requested | `needs_review` with a `mastodon_..._uncertain` code. |
| Success | `PUBLISHED POST_ID: URL`. The id and the link come from the server's reply. |

Errors carry only a fixed code such as `http_401`: never the URL, the headers, the token or the response body. After `needs_review`, look at the account in the Mastodon web UI, then `reconcile` (see [troubleshooting](TROUBLESHOOTING.md)).

## Local testing steps (no network, no token)

```bash
python3 -m unittest discover -s tests -p 'test_bluesky_mastodon.py' -v
python3 -m unittest discover -s tests -p 'test_mastodon_*.py' -v
python3 -m unittest discover -s tests -p 'test_rate_limits.py' -v
python3 -m unittest discover -s tests -p 'test_fault_injection.py' -v
```

The provider fixtures are synthetic and live in `tests/fixtures/providers/mastodon/` (a `429` with `X-RateLimit-Reset`, an unprocessed `202` upload, a partial `206` poll, a ready `200` poll, two upload `429`s, and `401`/`403` replies to `verify_credentials` and to the status create). `tests/test_mastodon_auth_fixtures.py` replays the `401`/`403` fixtures: at the identity preflight they block the post before the claim with one request and no retry; on the status create they end in `needs_review` and are never resent. It also checks that a handle on another server is refused before any request. `tests/test_rate_limits.py::test_ambiguous_codes_never_retried` sends `401`, `403`, `426`, `500`, `502`, `503`, a timeout, a malformed reply and an unexpected exception through the real CLI and requires one create request, `needs_review` and no resend. For a real first send use the owner-only runbook pattern in the [Bluesky pilot](BLUESKY-PILOT.md): a throwaway account you own, one harmless post, your own terminal.
