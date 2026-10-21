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
- `report --since WHEN` limits the report to a window: `24h`, `7d`, `30d` (any number of hours or days up to 3650 days), a date such as `2026-10-01` (midnight UTC) or an ISO 8601 time with a timezone. It keeps only posts whose send started in the window (`send_started_at`; a post without one is left out) and, for each, the latest snapshot observed in the window, so a post with only older snapshots counts as unknown, never 0. Replies are filtered the same way. JSON gains a `window` object (`since`, `until`, `basis`), Markdown a "Window:" line; CSV columns are unchanged. A value it cannot read is refused before the database is opened. `--since` adds no write; `report` still opens the database the way it always has (like other read-write commands, it creates a missing one).
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

`coverage_gaps` lists published posts with no tagged rows (this means unknown, not zero), published posts whose links lack `utm_content`, posts published after the last analytics date, rows with unknown sessions or conversions, and missing GitHub inputs. When no `--analytics` file is given (because you pass only the exports of section 3a), the report says `analytics_csv_not_supplied` instead of listing every post as unmatched.

## 3a. Google Search Console and GA4 exports (offline import)

`attribution-report` also reads two kinds of files that you download yourself from Google. ReachWeaver never connects to Google: there is no Google API call, no OAuth, no credential and no network request. It parses the local files and reads the local database, nothing else. You need at least one of `--analytics`, `--ga4-csv` or `--gsc-csv`.

```bash
reachweaver attribution-report --ga4-csv private/ga4-october.csv \
  --gsc-csv private/gsc/Pages.csv --gsc-csv private/gsc/Queries.csv \
  --gsc-csv private/gsc/Dates.csv --gsc-csv private/gsc/Filters.csv --json
```

Both sections are labelled `owner_supplied_*` with the export's `date_range` (`null` when the file does not say). They are numbers you exported, not numbers ReachWeaver checked. Neither section claims reach, and neither says that a post caused a visit.

### GA4 (`--ga4-csv`, section `ga4`)

**How to export.** In GA4 open **Reports > Acquisition > Traffic acquisition**, change the table's dimension to **Session source / medium**, add **Session campaign** and **Session manual ad content** as secondary dimensions (or build an exploration with the same dimensions), then **Share this report > Download File > Download CSV**. GA4 documents the CSV download and its limit of 100,000 rows. Repeat `--ga4-csv` for separate periods. Files whose dates overlap are refused, because their sessions would be counted twice.

**Columns.** Each header is matched case-insensitively, by its GA4 UI label or its Data API name:

| Meaning | GA4 UI label | API name | Required |
|---|---|---|---|
| source and medium | `Session source / medium` | `sessionSourceMedium` | yes, or the next two |
| source | `Session source` | `sessionSource` | with `Session medium` |
| medium | `Session medium` | `sessionMedium` | with `Session source` |
| `utm_campaign` | `Session campaign` | `sessionCampaignName` | no, but needed for campaign matches |
| `utm_content` (post id) | `Session manual ad content` | `sessionManualAdContent` | no, but needed to credit a post |
| `utm_term` | `Session manual term` | `sessionManualTerm` | no (read, not joined) |
| landing page | `Landing page`, `Landing page + query string` | `landingPage`, `landingPagePlusQueryString` | no (cleaned like `landing_pages` above) |
| date | `Date` (`YYYYMMDD`) | `date` | no |
| sessions | `Sessions` | `sessions` | **yes** |
| engaged sessions | `Engaged sessions` | `engagedSessions` | no |
| key events | `Key events` (legacy `Conversions`) | `keyEvents` | no |

The other metrics of the default Traffic acquisition table (`Engagement rate`, `Average engagement time per session`, `Events per session`, `Event count`, `Session key event rate`, `Total revenue`, user counts, `Bounce rate`, `Views per session`, `Average session duration`) are accepted and ignored. Any other column is refused with the list above. A `First user ...` column is refused with a hint: those are user-scoped (User acquisition), not session-scoped.

**File layout.** Lines that start with `#` before the header are skipped. A `# Start date: YYYYMMDD` and `# End date: YYYYMMDD` pair among them sets the export's date range; otherwise the range comes from a `Date` column, and otherwise it is unknown (gap `ga4_date_range_unknown`). Only the first table is read. It ends at the first blank or `#` line, and anything after it (for example chart data) is counted in `trailing_lines_ignored`. A row whose dimension cells are all blank or `Grand total`/`Total` is the export's totals row: it is shown as `export_totals_row` and never joined. A second totals row is refused.

**Join.** GA4 values `(not set)`, `(none)`, `(direct)`, `(organic)`, `(referral)` and similar placeholders count as empty. A row is *tagged* when it has a campaign or ad content, or when its source is a ReachWeaver platform id with medium `social` (what `utm-link` writes). Tagged rows are joined exactly like the analytics CSV rows of section 3: `attributed.posts`, `attributed.campaigns` and `unattributed` by reason. An untagged row keeps only its session source as a referrer domain: `t.co / referral` becomes `referrer_only` for `x` at domain level, and `google / organic` is unattributed (`no_utm`). Every bucket sums `sessions`, `engaged_sessions` and `key_events`, each with a `*_known_rows` count; a blank cell is unknown, never 0. GA4 sessions are kept apart from `--analytics` sessions and are never added to them.

**Gaps.** `published_posts_without_matching_ga4_rows` (unknown, not zero), `published_after_ga4_window`, `ga4_date_range_unknown`, `ga4_manual_ad_content_column_missing` (only campaign-level matches are possible), `ga4_campaign_column_missing`, `ga4_<metric>_column_missing` and `ga4_<metric>_unknown_rows`.

`experiment-readout --attribution` and `baseline-eval --attribution` read the top-level `attributed.posts` from `--analytics` by default. Add `--attribution-source ga4` to read the `ga4` section instead: each post's GA4 sessions count for `docs_visits`, its key events for `repo_stars_utm`, and `ga4.date_range` is the window. The two sources are never mixed or added up, and the readout records which one it used as `attribution_source`.

### Search Console (`--gsc-csv`, section `search_console`)

**How to export.** In Search Console open **Performance > Search results**, set the date range and search type, click **Export** and choose **Download CSV**. The download holds one CSV per table. Pass the ones you want with `--gsc-csv`, at most one of each: `Pages.csv`, `Queries.csv`, `Dates.csv` and `Filters.csv`. The table is recognized by its first column (`Top pages`/`Page`, `Top queries`/`Query`, `Date`, `Filter`). The other columns must be `Clicks` and `Impressions` (required) and optionally `CTR` and `Position`/`Average position`. A Filters table must have exactly the columns `Filter` and `Value`. Countries, Devices and Search appearance tables are refused because the report does not use them.

**What the report shows.**

- `pages_linked_from_posts`: each exported page that a published post links to, matched by host and path (scheme, query and trailing slash ignored), with its clicks, impressions, CTR, position and `linked_from_post_ids`. The level is `page_search_context_not_post`. These are Google Search clicks on that page, not clicks from the post, and they are never credited to the post.
- `dates_total`: the sums of the Dates table (property level), with known-day counts.
- `top_pages` and `top_queries`: the 25 rows with the most clicks. Page URLs are cleaned like `landing_pages` above. Query text goes through the same e-mail and token redaction.
- `date_range` from the Dates table, `date_filter_label` and `search_type` from the Filters table.
- Gaps: `post_destinations_not_in_gsc_pages` (a linked page on a host the export covers but not in its rows: unknown, not zero), `gsc_pages_table_not_supplied`, `gsc_queries_table_not_supplied` and `gsc_date_range_unknown`.

**Limits stated by Google.** Exports are cut to 1,000 rows of "representative examples", so a page or query missing from the file is unknown, not zero. Values shown as `~` or `-` in the report are written as zeros in the download, so a 0 can mean "not available". ReachWeaver reads a `Position` of 0 as `null`, but it cannot tell a real 0 click count from a missing one. The Pages table counts impressions by page and the chart by property, so page rows are never added up into a site total.

### Parsing rules (both formats)

- One file is at most 16 MiB and 100,000 data rows, a regular file (no symlinks) in UTF-8 (a byte-order mark is fine). At most 12 files per flag.
- Strict CSV: comma separated (a semicolon-separated file gets a hint to download it again), consistent quoting, and every row as wide as the header. Duplicate columns, duplicate pages, queries or dates, a blank first cell and a CTR above 100% are refused.
- Whole numbers accept `1234`, `1,234`, `1.234`, `1 234` and `1'234` (groups of three). Decimals accept `12.3`, `12,3`, `1,234.5` and `1.234,5`: with both marks the last one is the decimal mark, and a single `,` or `.` is the decimal mark (so `1,234` in a CTR or Position column reads as 1.234). `CTR` accepts `3.5%` (stored as the fraction 0.035) or a fraction without `%`. Dates accept `YYYY-MM-DD` and `YYYYMMDD`.
- An error names the file kind, the line and the column, never a cell value.
- Everything is computed in memory: no file is written and the database is only read.

### Sources (checked 2026-10-20)

- Search Console metrics (Clicks, Impressions, CTR, Average position) and dimensions (Queries, Pages, Countries, Devices, Search appearance, Dates): [Performance report (Search results)](https://support.google.com/webmasters/answer/7576553?hl=en).
- Export formats (Google Sheets, Excel, CSV), the 1,000-row cut and `~`/`-` written as zeros: [Export data directly from a Search Console report](https://support.google.com/webmasters/answer/12919797?hl=en).
- Aggregation by page versus by property: [What are impressions, position, and clicks?](https://support.google.com/webmasters/answer/7042828?hl=en).
- GA4 CSV download and its 100,000-row limit: [Share & export reports](https://support.google.com/analytics/answer/9317657?hl=en).
- GA4 UI labels (`Session source / medium`, `Session campaign`, `Session manual ad content` populated by `utm_content`, `Engaged sessions`, `Key events`, `(not set)`): [Dimensions and metrics](https://support.google.com/analytics/answer/9143382?hl=en).
- GA4 API names (`sessionSourceMedium`, `sessionCampaignName`, `sessionManualAdContent`, `engagedSessions`, `keyEvents`, `landingPagePlusQueryString`, `date` as `YYYYMMDD`): [Data API schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema).

**Not verified on an official page:** the exact header text inside a Search Console CSV (`Top pages`, `Top queries`, `Position`) and its date format; the `#` comment lines at the top of a GA4 CSV with their `Start date`/`End date` lines; the GA4 totals row; and the GA4 placeholders other than `(not set)`. The parser accepts these spellings without depending on them. If a real export uses another header, the error lists the accepted columns so you can rename it.

## Limitations

- Analytics tools define sessions and conversions differently, so do not combine exports from different tools. Ad blockers, consent banners and link shorteners all reduce UTM coverage.
- An attributed session shows that a tagged link was used. It does not show that the post caused the visit or the conversion.
- Search and social referral numbers can be compared only when they come from the same analytics export. Social shares do not guarantee search ranking.
