# Weekly report example (SYNTHETIC DATA)

> **SYNTHETIC.** Every account, post ID, count, session and conversion on this page is made up for the test fixture in `tests/fixtures/weekly_report/`. None of it is a real post, receipt or metric.

This page shows how to turn one week of ReachWeaver output into decisions, and where the evidence stops. The scenario: a `rw-v0-3-release` campaign posted once each on Bluesky, Mastodon, a LinkedIn member profile and a Telegram channel during the week of 2026-09-28 to 2026-10-04 (UTC).

## How it was produced

Sections A to E below are generated, not hand-written. `tests/test_weekly_report_example.py` loads the fixture posts into a throwaway database and runs:

```bash
reachweaver import-metrics --csv tests/fixtures/weekly_report/snapshots.csv
reachweaver report                     # JSON, used for the assertions
reachweaver report --format markdown   # section A, verbatim below its title and timestamp
reachweaver attribution-report --analytics tests/fixtures/weekly_report/analytics.csv \
  --github-traffic tests/fixtures/weekly_report/github-views.json \
  --github-traffic tests/fixtures/weekly_report/github-referrers.json --json
```

It then renders the output and fails if the block below differs (the timestamp is fixed so the block is reproducible). Section A is exactly what `report --format markdown` prints: wherever no post has an exact value it prints `unknown`, and `report` JSON gives `null` there, never 0. Its status table shows why a value is missing. The Bluesky and Mastodon `views` and `impressions` cells are imported as `not_available`, because section B lists them as `unavailable` for those platforms. Empty cells stay plain `unknown`. The snapshot CSV uses the optional `window_start`/`window_end` columns for the LinkedIn row only, because that export covers a stated date range. The other rows are cumulative public counters with no window.

<!-- BEGIN GENERATED: tests/test_weekly_report_example.py -->
### A. Latest native snapshot per published post (`reachweaver report --format markdown`)

Generated 2026-10-05T12:00:00+00:00 from the local database only. Coverage varies. Counts are latest-per-post snapshots, not unique people or comparable cross-platform views.

#### Exact values (latest snapshot per published post)

`unknown` = no post has an exact value. `(n/N)` = only n of N published posts have one.

| Platform | Published | With snapshot | views | impressions | likes | replies | reposts | saves | clicks | signups | Observation window |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bluesky | 1 | 1 | unknown | unknown | 31 | 4 | 9 | unknown | unknown | unknown | not supplied (1 snapshots) |
| linkedin | 1 | 1 | unknown | 1840 | 22 | 5 | 3 | unknown | 37 | unknown | 2026-09-30 to 2026-10-05 (1/1 snapshots) |
| mastodon | 1 | 1 | unknown | unknown | 14 | 2 | 6 | unknown | unknown | unknown | not supplied (1 snapshots) |
| telegram | 1 | 1 | 410 | unknown | unknown | unknown | unknown | unknown | unknown | unknown | not supplied (1 snapshots) |

#### Coverage and status (metrics without full exact coverage)

| Platform | Metric | Exact | unknown | suppressed | not_available | delayed | estimated |
|---|---|---|---|---|---|---|---|
| bluesky | views | 0/1 | 0 | 0 | 1 | 0 | 0 |
| bluesky | impressions | 0/1 | 0 | 0 | 1 | 0 | 0 |
| bluesky | saves | 0/1 | 1 | 0 | 0 | 0 | 0 |
| bluesky | clicks | 0/1 | 1 | 0 | 0 | 0 | 0 |
| bluesky | signups | 0/1 | 1 | 0 | 0 | 0 | 0 |
| linkedin | views | 0/1 | 1 | 0 | 0 | 0 | 0 |
| linkedin | saves | 0/1 | 1 | 0 | 0 | 0 | 0 |
| linkedin | signups | 0/1 | 1 | 0 | 0 | 0 | 0 |
| mastodon | views | 0/1 | 0 | 0 | 1 | 0 | 0 |
| mastodon | impressions | 0/1 | 0 | 0 | 1 | 0 | 0 |
| mastodon | saves | 0/1 | 1 | 0 | 0 | 0 | 0 |
| mastodon | clicks | 0/1 | 1 | 0 | 0 | 0 | 0 |
| mastodon | signups | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | impressions | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | likes | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | replies | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | reposts | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | saves | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | clicks | 0/1 | 1 | 0 | 0 | 0 | 0 |
| telegram | signups | 0/1 | 1 | 0 | 0 | 0 | 0 |

#### Platform estimates (not included in the exact values above)

None recorded.

### B. What each column means on each platform (`metric_definition`, table 2026-10-08.2)

| Platform | views | impressions | replies | reposts | saves | clicks |
|---|---|---|---|---|---|---|
| bluesky | none (unavailable) | none (unavailable) | `replyCount` (api) | `repostCount` (api) | `bookmarkCount` (api) | none (unknown) |
| linkedin | none (unknown) | `IMPRESSION` (api) | `COMMENT` (api) | `RESHARE` (api) | `POST_SAVE` (api) | `LINK_CLICKS` (api) |
| mastodon | none (unavailable) | none (unavailable) | `replies_count` (api) | `reblogs_count` (api) | none (unknown) | none (unknown) |
| telegram | `views` (api) | none (unknown) | none (unknown) | `forwards` (api) | none (unknown) | none (unknown) |

### C. Website sessions joined to posts (`reachweaver attribution-report`)

Analytics rows: 8, dates 2026-09-29 to 2026-10-03; GitHub traffic files: 2.

| Class | Key | Rows | Sessions (known rows) | Conversions (known rows) |
|---|---|---|---|---|
| attributed post | bluesky `5e0b000000000001` | 2 | 69 (2) | 3 (1) |
| attributed post | mastodon `5e0b000000000002` | 1 | 19 (1) | 1 (1) |
| attributed post | linkedin `5e0b000000000003` | 1 | 52 (1) | unknown (0) |
| campaign only, no post id | `rw-v0-3-release` | 1 | 6 (1) | 0 (1) |
| referrer only, domain level | telegram | 1 | 11 (1) | 0 (1) |
| unattributed | no_utm=1, unknown_utm=1 | 2 | 149 (2) | 5 (2) |

### D. GitHub traffic (repository level, never credited to a post)

- Views: 214 (61 unique) over 7 days, 2026-09-28 to 2026-10-04; level `repository_total_not_post`.
- Social referrer `bsky.app` (bluesky): 14 views, 9 unique.
- Other referrer `github.com`: 30 views, 12 unique.
- Other referrer `Google`: 22 views, 15 unique.

### E. Coverage gaps (unknown, not zero)

- `published_posts_without_matching_utm_rows`: post_ids=5e0b000000000004
- `published_posts_with_untagged_links`: post_ids=5e0b000000000004
- `conversions_unknown_rows`: rows=2
<!-- END GENERATED -->

## Decisions this evidence supports

Each decision names the row it rests on. They are operational choices for next week, not findings about platforms in general. The prose is pinned by `tests/test_weekly_report_prose.py`: every figure quoted below must appear in the generated block above or in the synthetic fixtures, and the derived ones (69, 52 and 19 attributed sessions, 149 unattributed, 140 from a search referrer, 11 from Telegram, 37 LinkedIn link clicks) must equal what `report` and `attribution-report` compute, so a changed fixture cannot leave these decisions stale.

1. **Tag the Telegram link before the next post.** Section E lists `5e0b000000000004` under both `published_posts_with_untagged_links` and `published_posts_without_matching_utm_rows`. The 11 sessions in section C reach Telegram only at domain level (`t.me` referrer), so they cannot be credited to that post. Next action: build the link with `reachweaver utm-link --platform telegram --campaign rw-v0-3-release --content <post id>`.
2. **Fix conversion tracking before comparing conversions across platforms.** The LinkedIn post has 52 attributed sessions but conversions `unknown (0)`, and `conversions_unknown_rows` is 2. A conversion rate for LinkedIn would be invented, so no conversion ranking is made this week. Next action: check that the analytics goal fired on the docs landing page for those rows.
3. **Keep the Bluesky and LinkedIn slots in next week's plan.** Within this one analytics export, tagged links on those two posts produced the most attributed sessions (69 and 52, against 19 for Mastodon). That is enough to justify repeating them. It is not enough to drop Mastodon: there is one post per platform, so this is a single observation with no matched comparison.
4. **Ask LinkedIn exports for a stated date range.** The LinkedIn snapshot is the only one with an observation window (section A). Lifetime counters read once, as on Bluesky and Mastodon, cannot be lined up with a weekly analytics export. Next action: export LinkedIn post statistics with a `dateRange`, and record Bluesky and Mastodon counters on the same day each week so week-over-week differences are comparable.
5. **Use one canonical landing page per campaign.** The campaign-only row (6 sessions, `rw-v0-3-release`, no `utm_content`) comes from a link without a post ID. Every campaign link should carry the post or variant ID so the row can be credited.

## Decisions this evidence cannot support

- **Which platform reached the most people.** Bluesky and Mastodon expose no view, impression or reach count (`unavailable` in section B). LinkedIn reports `IMPRESSION` and Telegram reports channel post `views`, and those are different measurements. ReachWeaver never sums or ranks them, and neither should this report.
- **That a post caused a visit, a star or a conversion.** Attribution is a join on UTM tags and referrers. Organic feeds are not randomized, and a release week has its own pull. The 149 unattributed sessions (140 of them from a search referrer) show how much traffic sits outside the tagged links.
- **Click-through rate comparisons.** LinkedIn `LINK_CLICKS` (37) and its 52 attributed sessions come from different systems with different definitions. They are expected to disagree, and neither is "correct".
- **Any GitHub effect of a specific post.** GitHub traffic (section D) is repository-level and covers only the last 14 days. The `bsky.app` referrer row is domain-level.
- **Best posting time, format or copy.** One post per platform gives no comparison. Use `experiment-preregister` and `timing-evaluate` with matched post ages instead.
- **Follower growth.** No follower snapshots were imported, so follower change is unknown, not flat.

## Known limitations of this example

- All values are synthetic and chosen to exercise each report class. They say nothing about real platform behaviour.
- `report` shows the latest snapshot per post. It cannot show a trend unless snapshots are imported on several dates.
- Section B reflects metric-definition table `2026-10-08.2`. Each non-unknown entry carries a `source_url` and `verified_date`. Provider definitions change, so re-check them before relying on an entry (see [ATTRIBUTION.md](ATTRIBUTION.md)).
- No privacy thresholds appear here. Some providers withhold small counts (for example, Instagram story metrics under 5). Import a withheld value as `suppressed`: it is counted under `suppressed` in the status table and never summed or shown as 0. A platform estimate goes in as `estimated:N` and is listed in its own table, outside the exact values.
