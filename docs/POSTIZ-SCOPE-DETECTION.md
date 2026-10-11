# Postiz scope detection: design note (issue #30)

**Status:** offline design note. **Nothing here was tested against a Postiz instance, an API key or a social account.** Every claim is tagged **[repo]** (true of this repository's code, checkable by reading it), **[doc]** (taken from the Postiz documentation as recorded in [POSTIZ.md](POSTIZ.md), checked 2026-10-08), or **[UNVERIFIED]** (a hypothesis or an absence of evidence; verify live before relying on it). This note changes no behaviour, and it is not consent to publish.

## Question

When a ReachWeaver account uses the Postiz backend, what can a Postiz API key (or `pos_` OAuth token) prove about the owner's right to post on the underlying platform, and what can it not prove?

## What the preflight proves

`postiz-check --online` and the pre-claim identity preflight run one read-only `GET /integrations` and then the pure `match_integration` check. **[repo]**

| Proved by a passing preflight | Basis |
| --- | --- |
| The API key is accepted by the configured instance | HTTP 2xx on the list call [repo]; 401/403 handling is mock-tested only |
| The key resolves to an organization that lists the configured `postiz_integration_id` | row lookup [repo]; that a key maps to exactly one organization is [doc]/source reading recorded in POSTIZ.md, **[UNVERIFIED] live** |
| That integration's `identifier` is in the allowed provider set for the account's platform | `PROVIDERS` map [repo]; the identifier strings are [doc], **[UNVERIFIED] live** for each provider |
| The integration's `profile` equals the configured `handle` (case-insensitive, leading `@` ignored) | [repo] |
| `disabled` is not literally `true` | [repo] |

The result is `identity: verified` and `write_permission: unverified`. The second field is hard-coded and never upgraded on this path. **[repo]** (`VerifiedIntegration`, pinned by `tests/test_postiz_scope.py`.)

## What it cannot prove

| Not provable from `GET /integrations` | Why | What to verify live |
| --- | --- | --- |
| That Postiz's stored OAuth grant still includes the write scope (for example LinkedIn `w_member_social` or `w_organization_social`, Facebook page publishing permissions) | The documented response fields are `id, name, identifier, picture, disabled, profile, customer`; none is a scope list. **[doc]** Whether other fields appear on some versions is **[UNVERIFIED]**. | Compare the raw response for a real connected channel; look for any scope, `refreshNeeded` or `inBetweenSteps` style field. Do not assume one exists. |
| That the platform token has not expired or been revoked | `disabled` is a Postiz-side flag. Whether it flips on token expiry or revocation is **[UNVERIFIED]**. | Revoke the app on the platform side in a throwaway channel, then re-list and observe `disabled`. |
| That the owner holds a Page/organization role (LinkedIn `linkedin-page`) | Role lives with the platform and Postiz; ReachWeaver sees only `identifier` and `profile`. **[repo]** (see POSTIZ.md, LinkedIn section) | Confirm the Page role in the platform UI before the first post. |
| That `profile` is unique or immutable | It is a display string. Two integrations may share one; a renamed handle breaks the match (fails closed). Uniqueness is **[UNVERIFIED]**. | List a workspace with two channels on the same platform and compare `profile` values. |
| That `disabled` is always a JSON boolean | The parser treats only literal `true` as disabled, so a missing, string or numeric value reads as enabled. **[repo]** (pinned in the test). Postiz is documented to send a boolean **[doc]**, but that is not a guarantee for every version. | Inspect `disabled` on cloud and on a self-hosted build. |
| That integration ids are unique within a response | First matching row wins. **[repo]** Duplicates are not expected **[UNVERIFIED]**. | Check on a workspace with many channels. |
| That an accepted `POST /posts` will be delivered | A 2xx means Postiz accepted the job; delivery happens later in Postiz's workers. Hence acceptance is `needs_review`, never `published`. **[repo]** | One supervised post, then compare against the native platform post ([FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md)). |
| That a key cannot reach more than one organization or more channels than intended | An API key is documented as org-scoped **[doc]**; whether finer per-channel keys exist is **[UNVERIFIED]**. | Create a key, list integrations, confirm only intended channels appear. |

Consequences, all already enforced: the owner's Postiz connection is the sole holder of platform authority; ReachWeaver never reads or stores the platform token; `api_gated` registry status is accepted for Postiz only because the owner holds the gating, "it is not proof of scope" (POSTIZ.md). **[repo]**

## Design decision

1. Keep `write_permission` fixed at `unverified` for the Postiz backend. Do not infer scope from `identifier`, `disabled` or the absence of an error.
2. Do not add scope heuristics. If a live check finds a documented scope or health field, propose a separate, security-reviewed change that treats it as an extra refusal only (it may block a send; it may never relax one), consistent with the approval, identity-binding and send-gate rules.
3. Treat the first-party native post as the only evidence of success. Backend-reported state (`postiz-lookup`) is never a reconciliation.
4. Prefer fail-closed readings. The one known soft spot is the `disabled` parser (non-boolean reads as enabled). Tightening it is an identity-preflight behaviour change, so it is **not** done here; it is listed for the owner. A strict reading (refuse unless `disabled is False`) would be a candidate once live data shows the real type.

## Live verification checklist (owner, supervised, none done)

Run privately, record date, instance version and evidence outside Claude. Mark each outcome observed or not observed; do not copy results into public docs as guarantees.

- [ ] Raw `GET /integrations` for each target provider: list every field actually present. Is there any scope or refresh indicator?
- [ ] Type and behaviour of `disabled` for a healthy channel, a manually disabled channel and a channel whose platform grant was revoked.
- [ ] `profile` format per provider (handle, display name, page name, URL) against the configured `handle`.
- [ ] Whether `identifier` for each provider matches `PROVIDERS` (`linkedin` versus `linkedin-page` especially).
- [ ] Behaviour of `POST /posts` when the platform grant lacks the write scope: synchronous error, accepted-then-failed, or silent. Determines whether any pre-send signal exists.
- [ ] Idempotency, 429 headers and `GET /posts` id equality (already listed in POSTIZ.md as unknown).

## Tests

`tests/test_postiz_scope.py` (7 offline tests, `postiz._call` patched) pins the boundary above: no write-scope claim, handle normalisation, empty profile or handle mismatch, field reduction with malformed rows dropped, non-list refusal, literal-`true`-only disabled reading, first-duplicate-wins. These document current behaviour; they do not assert that Postiz behaves that way.
