# Consent-aware, offline native post analytics CSV overlay

**Scope:** [Profile audit issue #3](https://github.com/MSKazemi/reachweaver/issues/3), 2026-10-12. This module is an **owner-authorized local file reader**, not an OAuth/API connector, scraper, proof of account ownership, or a real-time analytics collector.

## Why this exists

Real creator insights are often exported as CSV, while ReachWeaver's existing `profile-audit`, `post-audit` and `suggest` use a carefully validated owner profile JSON. The new `--metrics-csv` option reads a **normalized CSV** and joins it *in memory* to those profile records, for more evidence-based content recommendations. There are no provider calls, network requests, saved database changes or automatically generated posts.

Use with permission and only on accounts you own or are authorized to analyze. The mandatory `--owner-authorized` flag is an **explicit operator acknowledgement**, **not technical proof** of the export's true origin.

## Try the included synthetic data

```bash
reachweaver post-audit \
  --file examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv \
  --owner-authorized --age-hours 168 --json

reachweaver suggest \
  --snapshot examples/profile-post-audit.example.json \
  --metrics-csv examples/profile-post-metrics.example.csv \
  --owner-authorized --limit 15 --json
```

For real owner data, store the JSON and CSV under the gitignored `state/` directory. Do **not** commit private social exports, audience details, handles, contact data or credentials. Native exporters have varying column names and metric definitions: **manually map them to the documented normalized CSV format** before importing. ReachWeaver intentionally never guesses which provider metric a column represents.

## CSV contract

Use a UTF-8 (optionally BOM) CSV with this **required header subset**:

```text
platform,account_id,post_id,format,source,published_at,observed_at
```

Other supported columns are the explicitly named `reachweaver.profile_audit.METRICS`:
`views`, `impressions`, `reach`, `engaged_views`, `likes`, `comments`, `shares`, `reposts`, `replies`, `saves`, `clicks`, `upvotes`, `profile_visits`, `follows`, `watch_time_seconds`, `average_view_duration_seconds`. Optional manually reviewed creative flags: `hook_clear`, `proof_visible`, `original_content`, `captioned`, `alt_text`, `relevant_cta`, `community_rules_followed`, `human_written`.

Example:

```csv
platform,account_id,post_id,format,source,published_at,observed_at,clicks,impressions,hook_clear
linkedin,my-linkedin,post-001,text,owner-native-export,2026-09-01T10:00:00+02:00,2026-09-08T10:00:00+02:00,6,100,true
```

- `platform` + `account_id` **must already exist** in the owner profile snapshot; CSV cannot silently introduce a new account.
- `post_id` must be **unique** within platform/account, including any posts already present in the JSON. A second cumulative snapshot of the same post is **rejected**, not counted as another post.
- `format` and `source` are short owner-declared tokens (such as `reel`, `text`, `owner-native-export`), never independently verified by ReachWeaver.
- Both timestamps require explicit UTC or `±HH:MM` offsets; observation may not precede publication. They normalize to UTC in memory.
- Numeric counters are **non-negative integer counts**; only durations may have up to six decimal places. A blank cell is **unknown**, distinct from explicitly observed `0`.
- Creative flags are exactly `true`, `false` or blank/unknown. No AI reviews an image or video automatically from a CSV flag.
- CSV has no arbitrary comments/text or credential columns. Unknown headers, duplicate names, token-like values, malformed rows, duplicates, paths that are symlinks and exports over 2MB fail closed. Maximum combined snapshot size: 100 posts.
- Native insights differ. Instagram reach, YouTube views and LinkedIn impressions are **not comparable**, even when the CSV has identical column names. Observe the same account, source, format and age before interpreting descriptive rates.

## Readout and privacy

- `post-audit` includes `normalized_csv` provenance: count of rows added, per-platform counts, and the disclaimer that the owner acknowledgement is not independently verified.
- `suggest` adds the same `normalized_csv` detail under `source_coverage` and marks its source as `owner_authorized_local_csv_readonly`. The recommendations stay static human-review instructions; raw post text or private URLs are never printed.
- **Neither command writes a JSON file, database, remote post or new CSV**. They never attempt to authenticate or access social APIs. This is independent of `metrics-fetch --online`, which is a separately opted-in, provider-specific way to fetch a limited set of **public** counters.
- Use a private local directory and native permissions. Your exporting provider's retention/consent terms still apply. Deleting the input files is the owner's responsibility.

For direct account insights, the next phase requires separate provider-specific read scopes, app approval as relevant, explicit account selection, audit logs and supervised sandbox tests. No such private-account OAuth grant is implied by this CSV ingestion.

See the broader [profile/post audit](PROFILE-AND-POST-AUDITS.md), [ranked suggestions](NEXT-ACTIONS.md), and [privacy lifecycle](PRIVACY-LIFECYCLE.md).
