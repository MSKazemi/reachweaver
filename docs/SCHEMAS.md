# Canonical schemas (schema_version 1.2)

`reachweaver/schemas.py` defines versioned, machine-validated contracts for content, accounts, publish jobs and analytics, plus a **pure** publish state machine. It uses only the standard library: a small declarative validator covers field type, required/optional, enum, regex, numeric/length ranges, nested objects and lists. No `jsonschema` dependency, no network, no SQLite writes.

The contracts are the **target shape** for issue #4. The existing SQLite outbox (`storage.py`) is unchanged. It still enforces approval, the emergency stop, account budgets and ambiguous-send handling for every live send. The schemas do not authorize or perform any publishing.

## Validate a document

```bash
reachweaver schema-validate --type PublishJob examples/schemas/text-post-job.json
reachweaver schema-validate --type PlatformVariant examples/schemas/image-variant.json
reachweaver schema-validate --type PlatformVariant examples/schemas/video-variant.json
reachweaver schema-validate --type Approval examples/schemas/text-post-approval.json
reachweaver schema-validate --type AnalyticsSnapshot examples/schemas/analytics-snapshot-window.json
reachweaver schema-validate --type AnalyticsSnapshot examples/schemas/analytics-snapshot-status.json
```

Prints `OK: TYPE VERSION` (the document's own `schema_version`: `1.0`, `1.1` or `1.2`) and exits 0, or prints `INVALID` with one `ERROR:` line per problem and exits 1. Unreadable JSON exits 2. Everything runs offline.

From Python: `validate(kind, doc) -> list[str]` (an empty list means valid) and `require_valid(kind, doc)`, which raises `ValidationError`.

## Types

Every top-level document carries a supported `"schema_version"` (`"1.0"`, `"1.1"` or `"1.2"`) and `"type": "<TypeName>"`. Embedded documents (a `PlatformVariant` inside `PublishJob.payload`, a `MediaAsset` inside `PlatformVariant.media`) may leave out both fields. If they include them, the type must match and the version must be supported and no newer than the container's. Unknown fields are rejected everywhere, and so is a field newer than the document's declared version (a `1.0` snapshot cannot carry `window_start`).

| Type | Purpose | Key fields |
|---|---|---|
| `SourceSignal` | An owned achievement worth telling (release, paper, demo) | `brand_id`, `kind`, `url` and/or `doi`, `observed_at` |
| `Evidence` | One claim tied to a traceable source | `claim`, `url`/`doi`, `verification` (`primary_source_checked`/`owner_attested`), `checked_by`, `checked_at` |
| `ContentBrief` | What to say, to whom, why | `brand_id`, `campaign_id`, `audience_id`, `objective`, `locale`, `evidence_ids`, optional `utm` |
| `Draft` | Platform-neutral copy | `brief_id`, `body`, `evidence_ids`, `authorship` (`human`/`ai_assisted`/`ai_generated`) |
| `PlatformVariant` | One platform/account rendering, i.e. the **approved payload** | `platform`, `account_id`, `format`, `text`, `media[]`, `link_url`, `utm`, `platform_options` |
| `MediaAsset` | Image/video/gif/document with integrity and rights | `mime_type`, `sha256`, `bytes`, `alt_text` (image/gif), `duration_seconds` + `has_captions` (video), `license`, `people_depicted`, `consent[]` |
| `Account` | **Public** account metadata only | `id`, `platform`, `brand_id`, `env_prefix`, `audience_ids`, owner `consent[]` |
| `Approval` | Human authorization bound to an exact payload | `variant_id`, `account_id`, `platform`, `approved_payload_sha256`, `decision`, `reviewer_kind: "human"`, `approved_at`, `expires_at` |
| `PublishJob` | Outbox unit and lifecycle state | `state`, `idempotency_key`, `payload`, `payload_sha256`, `approval_id`, `scheduled_at`, `attempts`, `remote_post_id`, `error_code`, `cost[]` |
| `PublishReceipt` | First-party outcome of one attempt | `job_id`, `idempotency_key`, `outcome` (`published`/`failed`/`unknown`), `remote_post_id`, `error_code`, `evidence` |
| `AnalyticsSnapshot` | One native metrics observation | `remote_post_id`, `observed_at`, `source`, `metrics` (missing = `null`/absent, never zero); 1.1 adds optional `window_start`/`window_end`; 1.2 adds optional `metric_status` |
| `Experiment` | A pre-declared comparison | `hypothesis`, `primary_metric`, `denominator`, `guardrail_metric`, `variant_ids`, `age_hours`, `min_pairs`, `declared_at` |

Shared helper objects: `UTM` (`source`, `medium`, `campaign`, optional `term`/`content`), `Cost` (`amount` ≥ 0, ISO-4217-style `currency`, `kind`), `Consent` (`subject`, `obtained`, `recorded_at`, optional `reference`), `Metrics` (the eight `storage.METRIC_FIELDS`, each a non-negative integer or `null`), and, from 1.2, `MetricStatuses` (the same eight keys, each an optional `MetricStatus`: `status` in `suppressed`, `not_available`, `delayed`, `estimated`, plus a non-negative integer `value` only for `estimated`).

### Format rules

- **Timestamps**: RFC 3339 in UTC only (`2026-10-15T07:00:00Z` or `+00:00`). Local offsets are rejected so that comparisons and hashes are unambiguous.
- **Locale**: a BCP-47 subset, `language[-Script][-REGION|-UN M.49]`, e.g. `en-US`, `pt-BR`, `zh-Hans-CN`.
- **URLs**: public `https` only. Credentials, IP hosts, localhost, fragments and secret-looking query keys (`token`, `code`, `sig`, …) are rejected, reusing `campaigns.public_url`. DOIs match `10.NNNN/...`.
- **IDs**: brand, campaign, audience and source IDs are lowercase slugs. Account IDs use the `accounts.py` rule. Idempotency keys are 16–128 characters of `[A-Za-z0-9._:-]`. Error codes are `snake_case`. Remote post IDs may contain URIs (e.g. `at://…`) but no whitespace or control characters.
- **Secrets**: any key anywhere in any document that matches `accounts.SECRET_FIELD` (`token`, `password`, `secret`, `cookie`, `session`, `api_key`, `private_key`) is rejected. Credentials stay in environment variables named by `Account.env_prefix`. Token values are never part of a document, so they cannot be logged from one.
- **Text**: `PlatformVariant.text` runs through `policy.validate_post`, which applies the conservative per-platform limit, placeholder detection and control-character checks. It also refuses Unicode bidi embeddings, overrides and isolates (U+202A-U+202E, U+2066-U+2069; Trojan Source) and invisible format characters such as U+2060 and U+FEFF. ZWNJ, ZWJ, ZWSP and the LRM/RLM/ALM marks that multilingual and emoji text needs still pass. A Bluesky job with 290 characters fails even though the same text would be fine on LinkedIn.
- **Media and format**: `text`, `article` and `link` carry no media (`link` needs `link_url`). `image` needs image/gif assets. `video` needs exactly one video. `carousel` needs ≥ 2 image/video assets. Images need `alt_text`. Videos need `duration_seconds`, `has_captions`, and a `transcript_uri` if not captioned. `people_depicted: true` needs a `Consent` with `subject: people_depicted, obtained: true`. A remote media URI must be public https; otherwise use a relative path.
- **Accounts**: need an `account_owner` consent with `obtained: true`. Mastodon `server` goes through `require_https_public_server`. Bluesky needs `handle`; Telegram needs `chat_id`.
- **Hacker News** is not a `PLATFORMS` value, so it can never be a `PlatformVariant`. That keeps it human-only.
- **Snapshot window (1.1)**: `window_start` and `window_end` give the period the counts cover. They are RFC 3339 UTC, both or neither, and `window_start ≤ window_end ≤ observed_at` (equal bounds are allowed). Leaving both out means the window is **unknown**: the counts may be lifetime cumulative or cover a provider-defined period. Never fill in a guessed window. The rule mirrors `storage.observation_window`, which `metrics --window-start/--window-end` and `import-metrics` apply before a row is stored. Sample: [`examples/schemas/analytics-snapshot-window.json`](../examples/schemas/analytics-snapshot-window.json) (illustrative values, not a real account's metrics).
- **Metric status (1.2, issue #15)**: `metric_status` says why a metric has no exact count. `{"views": {"status": "suppressed"}, "impressions": {"status": "estimated", "value": 1200}}`. `suppressed` means the platform withheld the value (for example below a privacy threshold), `not_available` means the platform does not expose it, `delayed` means it is not final yet, and `estimated` carries the platform's estimate in `value`. A metric is either an exact count in `metrics` or a status here, never both. A metric in neither is **unknown**. An estimate is never an exact metric, so it can never be summed with one. A snapshot needs at least one exact metric or one status. The rule mirrors the `metric_snapshots.metric_status` column, which `metrics` and `import-metrics` fill from value tokens (`storage.parse_metric_value`) ([ATTRIBUTION.md](ATTRIBUTION.md#2a-bis-metric-value-status-privacy-thresholds-and-report-formats-issue-15)). A `1.0` or `1.1` document cannot carry the field (`added in schema 1.2`). Sample: [`examples/schemas/analytics-snapshot-status.json`](../examples/schemas/analytics-snapshot-status.json) (illustrative values, not a real account's metrics).

## Immutable approved payload hash

`payload_sha256(variant)` is the SHA-256 of `canonical_json(variant)`: UTF-8 JSON with sorted keys, `(",", ":")` separators, `ensure_ascii=False`, and NaN/Infinity rejected. Key order does not matter, but every byte of content does. The payload itself contains `platform`, `account_id`, `text`, media hashes, links, UTM values and `platform_options`, so the hash binds all of them.

- `PublishJob.payload_sha256` must equal the hash of `PublishJob.payload`. This is a self-consistency check.
- `Approval.approved_payload_sha256` is set by the human reviewer at approval time. Before a job can enter `approved`, `scheduled` or `publishing`, it must equal the hash of the current payload. Editing the payload and recomputing the job's own hash still fails: the change needs a new review and a new approval.
- Avoid non-integer floats in payloads you intend to hash on other runtimes. Python's float formatting is deterministic, but other JSON encoders can differ.

## Publish state machine

```text
draft -> reviewed -> approved -> scheduled -> publishing -> published
  ^         |           |  ^         |             |-----> failed  -> draft (re-review + re-approve)
  |         |           |  +---------+             +-----> unknown -> published | failed (receipt only)
  +---------+-----------+------------+
draft/reviewed/approved/scheduled -> cancelled            published, cancelled: terminal
```

`transition_job(job, target, *, approval=None, receipt=None, account=None, prior_receipts=(), paused=False, now=None)` validates the job and returns a **new** job dict. It never mutates its input, sends, or persists anything. It rejects:

- **Unapproved publication**: `draft`/`reviewed` cannot jump to `publishing`. Entering `approved`, `scheduled` or `publishing` requires a valid `Approval` with `decision: approved`, `reviewer_kind: human`, a matching `approval_id`/`variant_id`, and an unexpired `expires_at`.
- **Wrong account**: `Approval.account_id` differs from the job's, or an `Account` document whose `id`/`platform` differs from the job's.
- **Cross-platform mismatch**: `Approval.platform` differs from the job's, or the job's `platform`/`account_id` differs from its payload's (caught by schema validation).
- **Hash change**: the payload hash differs from `approved_payload_sha256`.
- **Replay after success**: any `published -> publishing|published` step. Entering `publishing` is also refused when `prior_receipts` holds a `published` or `unknown` receipt with the same `idempotency_key`, so a copied job cannot re-send.
- **Blind retry**: `unknown` cannot return to `publishing`. Only a `PublishReceipt` (API response or owner-verified native account check) can move it to `published` or `failed`. `failed` returns to `draft` and drops `approval_id`.
- **Emergency stop and due time**: `paused=True` blocks `publishing`, and a scheduled job is blocked before `scheduled_at`.
- **Receipts**: `published`, `failed` and `unknown` need a receipt with a matching `job_id`/`account_id`/`platform`/`idempotency_key` and outcome. A published receipt needs a `remote_post_id` and first-party `evidence` other than `none`.

## Mapping to the current SQLite outbox (documentation only)

`STORAGE_STATUS_MAP` records how `storage.posts.status` values correspond to canonical states. Storage behaviour is unchanged.

| storage status | canonical state | note |
|---|---|---|
| `draft` | `draft` | `check` is a lint, not a recorded review, so storage has no `reviewed` |
| `approved` | `approved` | storage approval is the `approved_at` timestamp, not a hash-bound `Approval` |
| `scheduled` | `scheduled` | |
| `sending` | `publishing` | durable claim under `BEGIN IMMEDIATE` |
| `published` | `published` | `remote_id` ≈ `remote_post_id` |
| `needs_review` | `unknown` | `last_error` ≈ `error_code`; never auto-retried |
| `cancelled` | `cancelled` | |
| — | `failed` | `reconcile --not-published` goes straight from `needs_review` to `draft`, collapsing `unknown -> failed -> draft` |

## Migration rules

1. `schema_version` is a `"MAJOR.MINOR"` string. **The version is global, not per type**: one `SCHEMA_VERSION` (currently `"1.2"`) covers the whole contract set, and `MIGRATIONS` is keyed by version alone. A bump that touches one type moves every type to the new number; types that did not change are identical in both versions. `SUPPORTED_VERSIONS` lists what this build reads, currently `("1.0", "1.1", "1.2")`.
2. **Minor** bumps (1.0 → 1.1) may only add optional fields or new enum values. Older documents stay valid after a version-only step. Each added field records the version that introduced it (`F(since="1.1")`).
3. **Major** bumps rename, remove, retype or tighten fields. They need an explicit step in `MIGRATIONS = {from_version: (to_version, pure_fn)}`, a fixture, and a test.
4. `migrate(doc)` returns a deep copy upgraded to `SCHEMA_VERSION`. The current version is the identity path, and `1.0 → 1.1` and `1.1 → 1.2` are each the identity plus a top-level `schema_version` bump. Embedded documents keep their declared version, so an approved payload is not rewritten (see rule 6). An unknown or cyclic version raises `ValidationError("Unsupported schema_version …")` instead of guessing.
5. Validation accepts any version in `SUPPORTED_VERSIONS` and checks each document against the fields its declared version defines. A field newer than that version is rejected with `added in schema 1.1`. An unsupported version, such as a future major, is rejected with a hint to run `migrate()` first. Because rule 2 allows only optional additions, a supported older minor document needs no migration to stay valid.
6. **Approvals do not survive migration.** If a migration changes an approved `PlatformVariant` in any way, its hash no longer matches `approved_payload_sha256` and `transition_job` refuses it. A human must re-review and re-approve. Never rewrite an approval hash in a migration. The `1.0 → 1.1` step changes only the top-level `schema_version`, so a migrated `PublishJob` keeps its byte-identical `1.0` payload, and the existing approval still binds it.
7. Migrations are pure and offline. They never contact a platform or change SQLite rows.

| Version | Change | Migration from previous |
|---|---|---|
| `1.0` | Initial contracts for the 12 types | — |
| `1.1` | `AnalyticsSnapshot` gains optional `window_start`/`window_end` (minor) | identity + `schema_version` bump |
| `1.2` | `AnalyticsSnapshot` gains optional `metric_status` (minor, issue #15) | identity + `schema_version` bump |

## Replaceable connectors

Everything upstream of a connector (signals, evidence, briefs, drafts, approvals, jobs, receipts, snapshots, experiments) is vendor-neutral. Platform names are plain enum values from `policy.PLATFORMS`. Provider-specific knobs live **only** in `PlatformVariant.platform_options`, a free-form JSON object capped at 20 kB and checked for secret-like keys. Examples: Bluesky `langs`, LinkedIn `visibility`, YouTube `privacy_status`/`made_for_kids`. Unknown top-level fields are rejected, so a vendor field cannot leak into the shared contract by accident.

A connector reads a validated `PlatformVariant`, interprets its own `platform_options`, and returns a vendor-neutral `PublishReceipt` (`remote_post_id`, `remote_url`, `error_code`, `cost`). Swapping a connector, such as a direct API versus a native scheduler handoff, changes neither the upstream types nor the approval hash rules. Sample payloads live in [`examples/schemas/`](../examples/schemas/).

## Exporting the legacy outbox (`schema-export`)

`reachweaver schema-export [--out FILE] [--type T]` maps local SQLite rows to canonical documents at the current version, 1.2 (`reachweaver/schema_export.py`). It is **read-only**: the connection runs in `PRAGMA query_only` mode during the export, so no row, setting or event is written. It never sends, schedules, approves or reconciles anything. The output is one canonical-JSON document per line, written to stdout or to a **new** owner-only (0600) file. An existing path or symlink is refused, never overwritten. A summary goes to stderr, or to stdout when `--out` is set: `counts` per type, `unmapped` (each entry has `type`, `id`, `source` row and `reason`) and a note. `--type` limits both lists to one of `Account`, `Evidence`, `PlatformVariant`, `PublishJob`, `PublishReceipt`, `AnalyticsSnapshot`, `Draft` or `Approval`.

```bash
reachweaver schema-export --out state/canonical-export.jsonl   # new 0600 file; summary on stdout
reachweaver schema-export --type Approval                       # no documents; unmapped reasons on stderr
```

Every emitted document passes `validate`. When storage lacks a required field, the exporter fills in an explicit unknown value only for a descriptive field whose domain has one: `locale: "und"` (the BCP 47 "undetermined" tag), and `checked_by` / `display_name: "unknown"`. It never invents an identifier or join key (`brand_id`, `campaign_id`), an enum member, a consent record, a reviewer or a hash. Such documents go to `unmapped` instead.

| Legacy source | Exported as | Rule |
|---|---|---|
| `posts` row staged by `campaign-stage` | `PlatformVariant` + `PublishJob` (ids `legacy:<post>:variant`/`:job`) | `format: text`; `state` from `STORAGE_STATUS_MAP`; `idempotency_key` = post id (the adapters' real dedupe key); `attempts` = surviving `send_claimed` events; `updated_at` = last event; `error_code` = `last_error` for `unknown` |
| `posts` row created with `draft` | unmapped | no `brand_id` recorded |
| any `posts` row | `Draft` unmapped | authorship and brief are not recorded; no supported version (1.0, 1.1, 1.2) has an unknown authorship value |
| rows needing an approval | `Approval` unmapped | see below |
| `published` row + `published` event | `PublishReceipt`, `evidence: api_response` | `observed_at` = event time |
| `published` row + `reconciled_published` event | `PublishReceipt`, `evidence: owner_verified_native_account` | no surviving event: unmapped |
| `metric_snapshots` of a published post with `remote_id` | `AnalyticsSnapshot` | absent metrics are `null`; `source` must be a slug; `window_start`/`window_end` are emitted (normalized to UTC `Z`) only when the row has them, a NULL window is left out as unknown, and a row with only one bound is unmapped; `metric_status` (1.2) is emitted only when the row records statuses, and a malformed status column makes the row unmapped |
| `campaign_variants.evidence_json` items | `Evidence` (`<campaign>:<evidence id>`) | offsets normalized to UTC; conflicting duplicates unmapped |
| `accounts.json` entry with `brand_id` and `consent` | `Account` | public fields only: `id`, `platform`, `env_prefix`, `handle`, `server`, `chat_id`, `display_name`, `audience_ids`, `locale`, `consent` |

**What links a snapshot to its post (pinned by `tests/test_analytics_linkage.py`).** Each exported `AnalyticsSnapshot` carries the `account_id`, `platform` and `remote_post_id` of the published post its row belongs to, the snapshot's own `source` and `observed_at`, the observation window when stored, and `job_id`, which names the `PublishJob` (`idempotency_key` = the post id) and so the `PublishReceipt` with the same `remote_post_id`. `campaign_id` is the staged campaign, or the post's `campaign` label when that is a valid slug; otherwise it is left out, never rewritten. A snapshot whose post has no recorded remote post id is reported as unmapped, never exported, and a metric nobody imported is `null`, never `0`.

**Approvals are deliberately not exported, and no version bump relaxes that.** The 1.1 and 1.2 bumps only added optional snapshot fields (the window, then the per-metric status); `Approval` is unchanged. Legacy approval is only the `approved_at` timestamp written by `approve`. There is no reviewer identity and no `approved_payload_sha256`. Hashing the exported payload would fabricate a human approval of a canonical document that nobody reviewed. A 1.1 bump that made the hash optional would loosen a required field, which migration rule 2 does not allow, and it would weaken the hash gate for every future approval. Exported jobs in gated states carry `approval_id: "legacy:<post>:approval"`, and that id is listed in `unmapped` with the reason. No `Approval` document exists for it, so `transition_job` refuses to advance an exported job. A canonical approval needs a fresh human review of the canonical payload.

A document whose text looks secret (the `privacy.redact` patterns, or the value of any secret-named `REACHWEAVER_*` environment variable) is unmapped rather than redacted, because redacting would change the payload. Use `data-export` for a redacted copy of the whole database. The decision record is ADR-0003.
