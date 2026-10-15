# Campaign links, metric definitions and conversion attribution

Issue #15 slice. Everything here runs **offline**: ReachWeaver reads files you exported yourself and the local SQLite database. It never fetches analytics, opens landing pages, calls the GitHub API or publishes anything. The one exception is the explicit, read-only `metrics-fetch --online` command in [section 2b](#2b-read-only-public-counter-polling-metrics-fetch), which reads public Bluesky and Mastodon post counters. Attribution here means joining tags and referrers. It is **not** causation or incrementality.

## 1. Canonical UTM links

```bash
reachweaver utm-link 'https://github.com/MSKazemi/reachweaver?fbclid=abc#readme' \
  --platform bluesky --campaign 'Release v0.3' --content 3f2a9c1b7d4e6f80 \
  --allow-origin https://github.com https://mskazemi.github.io
# https://github.com/MSKazemi/reachweaver?utm_source=bluesky&utm_medium=social&utm_campaign=release-v0-3&utm_content=3f2a9c1b7d4e6f80
```

How the link is built (`reachweaver/attribution.py: build_utm_link`):

- The destination must be public `https`. URLs with credentials, `localhost`, `.local` or IP-address hosts are rejected.
- The host is lowercased, port 443 is dropped and the `#fragment` is removed.
- Existing `utm_*` parameters and known click trackers (`fbclid`, `gclid`, `msclkid`, `igshid`, `mc_eid`, ...) are removed. Other query parameters are kept and sorted.
- The tool adds `utm_source=<platform>`, `utm_medium=social`, `utm_campaign=<slug>`, an optional `utm_content=<post or variant id>` and an optional `utm_term`. Values become lowercase `a-z0-9-` slugs.
- The result is deterministic and idempotent: rebuilding an existing output with the same arguments returns the same string.
- `--allow-origin` rejects any destination whose exact `scheme://host[:port]` is not listed.

Use the post ID, or the campaign `variant_id`, as `--content`. That is the key `attribution-report` uses to credit a post. `validate_utm_link(url)` lists missing, non-canonical or tracker-polluted links.

## 2. Versioned metric definitions

`metric_definition(platform, metric)` returns `version`, `native_field`, `definition`, `denominator`, `availability` (`api` | `native_export` | `unavailable` | `unknown`), `reporting_delay`, `source_url`, `additional_sources` and `verified_date`. It covers these canonical metrics: `views`, `impressions`, `reach`, `engaged_views`, `watch_time`, `replies`, `shares`, `saves`, `clicks`, `follower_change` and `profile_actions`.

Rules:

- A metric is recorded only after its `source_url` was fetched and checked. Table version `2026-10-08.2` was checked on `verified_date` 2026-10-08, and `tests/test_attribution.py` requires an `https` source and a `verified_date` on every recorded entry. Anything else returns `unknown` with no field, definition, denominator or `verified_date`. It is never guessed. For an `unknown` entry, `source_url` only says where to look. It is not evidence.
- `unavailable` means the cited source shows that the platform does not expose the metric. Examples: Bluesky `postView` and the Mastodon `Status` entity have no view or impression counts, and Instagram `impressions` is deprecated for media created after July 2, 2024.
- An `api` entry does **not** mean ReachWeaver can fetch it. The only fetcher is `metrics-fetch` ([section 2b](#2b-read-only-public-counter-polling-metrics-fetch)), and it reads only the public Bluesky and Mastodon counters. Many fields also need owner authorization: LinkedIn `r_member_postAnalytics`, organization `rw_organization_admin`, X `non_public_metrics` (owner user context, posts from the last 30 days only), Meta insights and the Pinterest `pins:read` scope.
- LinkedIn entries describe **member** posts (`memberCreatorPostAnalytics`), which is what the LinkedIn member adapter publishes. Where an organization page has an equivalent `organizationalEntityShareStatistics` field, the definition names it and `additional_sources` cites that page. Member `LINK_CLICKS` and organization `clickCount` ("Number of clicks") are not comparable.
- Never add up `views` across platforms. YouTube `views` "represents different numbers in different types of reports", Threads and Instagram mark `views` as in development, and Telegram `views` counts channel post views.
- Denominators are ReachWeaver's suggested rate bases, not provider statements.
- Provider definitions change. Re-check `source_url` before you rely on an entry. When you edit the table, bump the version and update `METRIC_DEFINITIONS_VERIFIED`.

What the 2026-10-08 verification changed (old to new):

| Platform / metric | Before (`2026-10-08.1`, unverified) | After (`2026-10-08.2`, checked) |
|---|---|---|
| LinkedIn (all) | organization `organizationalEntityShareStatistics` fields | member `memberCreatorPostAnalytics` types (`IMPRESSION`, `MEMBERS_REACHED`, `COMMENT`, `RESHARE`, `LINK_CLICKS`); organization fields named in the definition |
| LinkedIn saves, follower_change, profile_actions | unknown | `POST_SAVE`, `FOLLOWER_GAINED_FROM_CONTENT` (gains, not net), `PROFILE_VIEW_FROM_CONTENT` (API 202604+) |
| YouTube views | "number of times the video was viewed" | quotes the docs: different numbers in different report types |
| YouTube engaged_views | "views that exceed an initial-seconds threshold" | "viewed past the first frame, or the user clicks/taps to play" |
| YouTube impressions | source: Analytics API metrics page | source: YouTube Help (Studio Reach/Content tab; >1 s and ≥50% visible). The API metrics page lists no thumbnail-impression metric |
| X impressions | "times the post was viewed" | "times the post appeared on a user's screen; not unique"; fields written as `public_metrics.*` / `non_public_metrics.*` |
| Instagram impressions | unknown | unavailable (deprecated for media created after 2024-07-02) |
| Telegram views | native_export, source `core.telegram.org/api/stats` | api (MTProto `message.views`, "View count for channel posts"); the Bot API has no views field. Added shares = `forwards` |
| Bluesky | source: docs.bsky.app (now redirects) | source: `app.bsky.feed.defs` lexicon; added saves = `bookmarkCount` |
| Mastodon replies/shares | "known to the queried instance" | the docs' own wording, without the unverified instance claim |
| TikTok watch_time | native_export (TikTok Studio) | unknown (official help page not reachable) |
| Pinterest | `IMPRESSION`, `SAVE`, `OUTBOUND_CLICK` with invented definitions | enum names checked in the official OpenAPI spec (which has no definition text); added views, watch_time, replies, profile_actions |
| Facebook views, reach | unknown | `post_media_view`, `post_total_media_view_unique` (`post_impressions*` deprecated from Graph API v25) |
| Reddit replies | `num_comments` | unknown (the API page documents no submission fields) |
| DEV views, replies | `page_views_count`, `comments_count` | unknown (the Forem v1 reference shows neither field on articles) |

## 2a. Observation windows on metric snapshots

A snapshot may state the period its counts cover. `import-metrics` CSVs accept optional `window_start` and `window_end` columns, and `metrics` accepts `--window-start` and `--window-end`. Both take ISO 8601/RFC 3339 timestamps with a timezone and are stored in UTC. Give both or neither, `window_start` must not be after `window_end`, and `window_end` must not be after `observed_at`. A blank window means **unknown**: the counts may be lifetime cumulative counters or a provider-defined period. Use a window when the export states one, for example a LinkedIn `dateRange` or a YouTube `startDate..endDate` report.

The columns are nullable and are added to existing databases by `storage.migrate()` inside `connect()` (`ALTER TABLE metric_snapshots ADD COLUMN`, only when missing, under a write lock). Older rows keep a NULL window. `report` keeps every existing key and adds `window_coverage` per platform: `with_window`, `without_window`, `earliest_window_start` and `latest_window_end` across the latest snapshots. See the [SYNTHETIC weekly report example](WEEKLY-REPORT-EXAMPLE.md) for how the two reports combine into decisions.

## 2a-bis. Metric value status, privacy thresholds and report formats (issue #15)

Every metric cell in an `import-metrics` CSV, and every `metrics --views/--likes/...` value, takes one of these values. Tokens are case-insensitive:

| Value | Meaning | Stored as |
|---|---|---|
| `120` (digits only) | exact count from a first-party export | integer column |
| empty or `unknown` | no value observed | NULL, no status |
| `suppressed` | the platform withheld the value, for example because it is below a privacy threshold | NULL + status |
| `not_available` | the platform does not expose this metric (see `metric_definition` availability `unavailable`) | NULL + status |
| `delayed` | the value is not final yet; re-import the snapshot when it is | NULL + status |
| `estimated:N` | a platform estimate of N, not an exact count | NULL + status with value N |

Anything else fails, including `-1`, `1,234`, `12.5` and `n/a`. The value is never coerced to 0. `import-metrics` is all-or-nothing: one bad cell names its CSV line, and no row of that file is imported. Re-importing the same `post_id`, `observed_at` and `source` replaces the whole snapshot, statuses included. An observation where every value is a status (for example, all `suppressed`) is a valid snapshot. A row that is all `unknown` is still refused, because it records nothing.

Statuses live in the nullable JSON column `metric_snapshots.metric_status`. `storage.migrate()` adds it to older databases the same way as the window columns: `ADD COLUMN` only when missing, under a write lock, idempotently. Old rows keep NULL, so their missing values stay `unknown`. Exact counts stay in the integer columns. Every reader that uses those columns (`timing-evaluate`, `baseline-eval`, `experiment-evaluate`, `experiment-readout`) therefore sees exact values only. An estimate can never enter a rate or a sum as if it were exact.

`report` aggregates **exact values only**, from the latest snapshot per published post:

- `views`, `likes` and the other metric keys are the sum of exact values. When no post has an exact value they are `null`, never 0 (before #15 they were 0). `coverage_by_metric` still counts posts with an exact value.
- `metrics.<name>` gives `value`, `exact`/`total` (published posts), `coverage` (`"n/N"`), `status_counts` (`exact`, `unknown`, `suppressed`, `not_available`, `delayed`, `estimated`; a published post without any snapshot counts as `unknown`) and `estimated`. `estimated` is `null` or `{"posts", "sum", "note"}`: platform estimates, listed separately and never added to `value`.
- `report --format json` (the default) keeps the same envelope (`generated_at`, `platforms`, `note`). `--format markdown` prints three tables: exact values with `(n/N)` coverage, statuses for every metric without full exact coverage, and estimates. `--format csv` prints one row per platform and metric. Coverage is written as two integer columns (`exact_posts`, `total_posts`), because spreadsheets turn `1/3` into a date. Any cell starting with `=`, `+`, `-`, `@`, a tab, CR, LF or their full-width forms gets a leading `'`, following the [OWASP CSV Injection](https://community.owasp.org/attacks/CSV_Injection) guidance (accessed 2026-10-09).

Canonical exports carry the statuses as the optional `AnalyticsSnapshot.metric_status` field of schema 1.2 (see [SCHEMAS.md](SCHEMAS.md)).

## 2b. Read-only public counter polling (`metrics-fetch`)

```bash
reachweaver metrics-fetch --dry-run                       # planned GET requests; no network, no DB write
reachweaver metrics-fetch --online [--account ID] [--post-id ID] [--max-posts 50]
```

This command reads **public** counters. It is not private analytics. It only considers local posts in `published` status that have a `remote_id` and belong to a `bluesky` or `mastodon` account in `config/accounts.json`. Backend (Postiz) accounts are skipped, because their `remote_id` is not a platform ID. It stores one snapshot per post through `store_metrics`. The snapshot has `observed_at` set to the UTC response time (whole seconds) and no window. The counters are lifetime totals read at that moment.

| Platform | Request | Auth | Identity check | `source` |
|---|---|---|---|---|
| Bluesky | `GET https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris=...` (an XRPC query), up to 25 AT-URIs per request | none. The direct AppView endpoints "do not support authentication" | `author.did` must equal the DID in the post's AT-URI | `bluesky-public-api` |
| Mastodon | `GET {server}/api/v1/statuses/:id` on the configured server | none first. Only on 401 (authorized-fetch mode) or 404 ("does not exist or is private"), and only if `{env_prefix}_TOKEN` is set, it retries **once** with that bearer token. A token with `read:statuses` is enough for this call | `id` must match. `account.username` must equal the configured handle, and `acct` must be local to the configured server | `mastodon-api` |

Cross-reference to the [metric definitions table](#2-versioned-metric-definitions) (`metric_definition(platform, metric)`):

| Native field (docs) | Snapshot column | Canonical metric in the table | Availability in the table |
|---|---|---|---|
| Bluesky `likeCount` | `likes` | none (the table has no likes metric) | not applicable |
| Bluesky `repostCount` | `reposts` | `shares` | `api` |
| Bluesky `replyCount` | `replies` | `replies` | `api` |
| Bluesky `bookmarkCount`, stored only when present | `saves` | `saves` | `api` |
| Bluesky `quoteCount` | not stored (no column) | not applicable | not applicable |
| Mastodon `favourites_count` | `likes` | none | not applicable |
| Mastodon `reblogs_count` | `reposts` | `shares` | `api` |
| Mastodon `replies_count` | `replies` | `replies` | `api` |
| no field | `views`, `impressions`, `clicks` stay NULL | `views`, `impressions` | `unavailable` |

A missing field, a non-integer value or a negative value is stored as NULL (unknown), never as 0. If a response has none of the documented counters, no snapshot is written.

Safety rules:

- **GET only.** Requests go through `publishers.request_get_json`: 25 s timeout, no redirects (so a bearer token is never forwarded), a 1 MB response cap, and sanitized `http_NNN` errors that never echo the URL or the headers. No like, repost, follow or post endpoint is used.
- **Polite pacing.** Requests run one at a time. A run fetches at most `--max-posts` posts (default 50, maximum 200, newest first). The rest are listed as `deferred_max_posts_N`. A provider **429 stops the run immediately**. There is no retry. Snapshots already stored are kept, and the posts that were not reached are listed under `not_attempted`. Bluesky documents no numeric limit for the AppView ("generous rate-limits"). Mastodon's default is 300 requests per 5 minutes per IP and per account.
- **Idempotent.** A re-run within the same UTC second upserts the same `(post_id, observed_at, source)` row (`UNIQUE` constraint).
- **Output.** The JSON summary lists, for each post, `fields_fetched` (with values), `fields_unknown`, `auth` (`none` or `bearer_token_retry`) and the source URLs. It also lists `failed` posts with a sanitized reason (`author_mismatch`, `not_returned`, `http_404`, ...) and `skipped` posts with a reason. The token value never appears in the summary. `--dry-run` prints only the variable name. Exit code 1 means the run stopped on a 429 or at least one post failed.
- The emergency pause, approval, dry-run publishing and reconciliation rules are unchanged. This command cannot change a post's status.

Sources checked on 2026-10-08:

- [getPosts lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/getPosts.json): a `query` with `uris` (at-uri array, maxLength 25) that returns `posts` of `#postView`.
- [feed defs lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/defs.json): `postView` includes `author` (`profileViewBasic`), `replyCount`, `repostCount`, `likeCount`, `quoteCount` and `bookmarkCount`, all integers and all optional.
- Bluesky docs source, [API directory](https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/api-directory.mdx) and [rate limits](https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/rate-limits.md). They say public endpoints need no authentication and to use `public.api.bsky.app`, which "includes additional caching". That GitHub repository is archived. Its rendered pages now redirect to `bsky.network`, which could not be read without JavaScript.
- [Mastodon statuses methods](https://docs.joinmastodon.org/methods/statuses/#get): "Public for public statuses, user token + `read:statuses` for private statuses". Since 2.7.0, "public statuses no longer require token". 401 means "Instance is in authorized-fetch mode" and 404 means "Status does not exist or is private".
- [Mastodon Status entity](https://docs.joinmastodon.org/entities/Status/) (`favourites_count`, `reblogs_count`, `replies_count`; no view field) and the [Account entity](https://docs.joinmastodon.org/entities/Account/) (`username`, `acct`).
- [Mastodon rate limits](https://docs.joinmastodon.org/api/rate-limits/).

### Third mocked provider and follower polling (design)

`metrics_fetch.normalize_mockstats` is an offline, invented third provider used to test differing metric definitions and privacy thresholds (below-threshold counts become `suppressed`; see `tests/fixtures/providers/mockstats/`). It has no fetcher and no CLI command. Account-level follower polling is only a design: [FOLLOWER-POLLING-DESIGN.md](FOLLOWER-POLLING-DESIGN.md).

## 3. Conversion attribution report

```bash
reachweaver attribution-report --analytics private/ga-export.csv \
  --github-traffic private/traffic-views.json --github-traffic private/referrers.json --json
```

**Analytics CSV.** This is an aggregated export from your own web analytics tool. Required columns: `date` (YYYY-MM-DD), `landing_url` or `page`, `utm_source`, `utm_medium`, `utm_campaign`, `utm_content` and `sessions`. Optional columns: `conversions` and `referrer`. A blank `sessions` or `conversions` value means **unknown**. It is left out of sums and counted under `*_known_rows`, never turned into 0. Do not export per-user rows or personal data.

**GitHub traffic JSON.** Save the raw responses of `GET /repos/{owner}/{repo}/traffic/views` and `GET /repos/{owner}/{repo}/traffic/popular/referrers` (docs: https://docs.github.com/en/rest/metrics/traffic) yourself, then pass each file with `--github-traffic`. ReachWeaver parses the local files only. The API needs push access to the repository and covers only the last 14 days. Its data is repository-level and cannot be credited to a single post.

**Output classes** (the database is only read, never written):

| Class | Rule |
|---|---|
| `attributed.posts` | `utm_content` matches a **published** post ID, or a `campaign_variants` variant within the same `utm_campaign`. `utm_source` must equal the post platform, `utm_medium=social`, and `utm_campaign` must equal the post campaign slug |
| `attributed.campaigns` | `utm_campaign` and `utm_source` match published posts but no post ID is present. This is campaign-level only |
| `referrer_only` | No UTM, but the referrer domain is a social platform (`t.co`, `lnkd.in`, `bsky.app`, `news.ycombinator.com`, ...). Domain-level only, never credited to a post |
| `unattributed` | Grouped by reason: `no_utm`, `unknown_utm`, `utm_conflict:<field>` or `utm_matches_unpublished_post` |

**Landing pages in the output.** Each `attributed.posts`, `attributed.campaigns` and `referrer_only` bucket lists its `landing_pages`. They are cleaned before they are printed, because an analytics export can carry magic-link tokens, password-reset codes or subscriber e-mail addresses in a landing URL. Only `scheme://host[:port]/path` (or a bare `page` path) and the five canonical `utm_*` parameters are kept. User info, every other query parameter (`?token=`, `?email=`, `?coupon=`, `fbclid`, ...) and the fragment are dropped. A secret env value, a token shape or an e-mail address left in the path is replaced with a `[REDACTED...]` marker. So `https://example.org/welcome?token=...&email=...` is reported as `https://example.org/welcome`. Matching rows to posts uses the UTM columns, not the landing URL, so attribution results are unchanged.

`coverage_gaps` lists published posts with no tagged rows (this means unknown, not zero), published posts whose links lack `utm_content`, posts published after the last analytics date, rows with unknown sessions or conversions, and missing GitHub inputs.

## Limitations

- Analytics tools define sessions and conversions differently, so do not combine exports from different tools. Ad blockers, consent banners and link shorteners all reduce UTM coverage.
- An attributed session shows that a tagged link was used. It does not show that the post caused the visit or the conversion.
- Search and social referral numbers can be compared only when they come from the same analytics export. Social shares do not guarantee search ranking.
