# Analytics and experimentation protocol

## Guard against false success

Raw views are one signal. Useful outcomes include retained viewer attention, saved/shared content, relevant profile visits, qualified website sessions, genuine signups, repository adoption and meaningful collaboration. Do not treat cross-platform `views` and `impressions` as one standardized population, and never sum cumulative daily snapshots for the same post.

For each post record: platform, account, campaign, source, publish date/time, creative version, format, content topic, claim evidence, remote URL, measurement date, native metric definition, and whether the platform/API actually exposes it. Missing values are unknown, **not zero**.

## Snapshot protocol

- Collect at consistent time after publishing (e.g. +24 hours, +7 days, +28 days).
- Preserve the raw platform export (locally private, with consent) and document sampling/reporting window.
- Use `reachweaver metrics` to record one post snapshot, or `import-metrics --csv <path>` for normalized CSV. When the export states its period, add `window_start`/`window_end` (`--window-start`/`--window-end`); leave them blank when it does not, so the window stays unknown. A metric cell is a count, empty/`unknown`, `suppressed` (the platform withheld it, e.g. below a privacy threshold), `not_available` (the platform does not expose it), `delayed` (not final yet; re-import later) or `estimated:N` (a platform estimate). Never type 0 for a withheld or missing value. One bad cell rejects the whole CSV and names its line.
- For the owner's published Bluesky/Mastodon posts, `reachweaver metrics-fetch --online [--account ID] [--post-id ID]` stores public like/repost/reply (and Bluesky bookmark, when present) counters as `bluesky-public-api` / `mastodon-api` snapshots. Run `--dry-run` first: it uses no network. It is read-only GET of public counters, not private analytics, so views, impressions and clicks stay unknown. A 429 stops the run with no retry. Treat a `failed` entry (`author_mismatch`, `not_returned`, `http_404`) as no data, never as zero.
- Run `reachweaver report` (or `--format markdown` / `--format csv` for the owner) to inspect the latest snapshot per published post. It sums **exact values only**. Report **coverage** (`metrics.<name>.coverage` n/N and `status_counts`, `window_coverage`), not only totals. A `null`/`unknown` value means no post has an exact value; it is never 0. Platform estimates appear only in the separate `estimated` block: never add them to exact counts or compare them with exact counts.
- Keep platform-specific denominators; meaningful ratios only when numerator, denominator, timeframe and account cohort match.

## Experimental loop

1. State one hypothesis: e.g. a 20s reproducible demo attracts more qualified engineer visits than text-only explanation for the same capability.
2. Keep holdout/controls where practical, and document production differences, audience/follower changes, media formats, CTA destination and holidays.
3. Compare matched post ages and report confidence limitations. Organic social distribution is noisy; one winner does not prove causation.
4. Distinguish **views**, **impressions**, **engagements**, **outbound clicks**, **sessions**, **conversion events**, **incrementality**, **new contributors**. Never label platform-attributed signups as incremental without a suitable design.
5. Choose continue/modify/stop based on actual results; preserve losing experiments so the agent does not chase selection bias.

## Field pilot operations log

When the owner compares workflows (manual, a conventional scheduler, ReachWeaver), keep one CSV row per task from `examples/pilot-log.template.csv` and run `reachweaver pilot-report --log FILE.csv [--min-weeks N] [--json]`. It is offline and never opens the outbox. Blank cells are unknown, never zero; malformed rows are listed by line and excluded; every rate shows its numerator, denominator and rows used. The arm comparison is labelled `descriptive_not_causal` and is refused until every logged arm covers N ISO weeks (default 2). Never report a pilot result that is not in the owner's log and native exports. The full repository has the preregistered protocol in `docs/FIELD-PILOT-PROTOCOL.md` and the gated release sequence in `docs/RELEASE-PLAYBOOK.md`.

## Weekly review outline

- Observed (with exact source and coverage)
- What changed / possible confounders
- Useful audience feedback (nonidentifying)
- Best and worst creative angles with evidence
- Distribution bottleneck, blocked API capabilities
- 1-3 next tests with success criterion and owner

Never assert an algorithmic ranking factor unless a current primary source provides it. Use judgment; no guaranteed viral growth.

## Campaign links and website attribution

- Tag every owned-destination link with `reachweaver utm-link URL --platform P --campaign C --content POST_ID --allow-origin <owned origins>`. Its output is canonical and free of trackers, so do not hand-edit UTMs.
- Before you claim a metric exists, check `metric_definition(platform, metric)` in `scripts/reachweaver/attribution.py`. If it says `unknown`, call the metric unverified. If it says `unavailable`, the platform does not expose it. `api` does not mean ReachWeaver fetches it. Cite the entry's `source_url` and `verified_date`; LinkedIn entries are member post analytics.
- `reachweaver attribution-report --analytics CSV [--github-traffic JSON]` joins owner exports to published posts. Report attributed, referrer-only and unattributed rows separately, and list `coverage_gaps`. Never present an attributed session as caused by the post.
- For release, paper, product and docs destinations taken from the owner's SEO repository, run `reachweaver seo-destinations --briefs FILE --type T`. Resolve `review_conflicts` before drafting. Add `--suggest-for PLATFORM` (repeat) and `--campaign C` for per-platform advice on pointing a post at that page; quote only `official` items as platform rules, and present `hypothesis` items as editorial suggestions, never as reach or ranking effects. The full repository documents the contract in `docs/SEO-INTEGRATION.md`.
- To check post texts you wrote before publishing (length, alt text, hashtags, UTM link, wording repeated across platforms), run `reachweaver seo-content-check FILE.json [--json]`. Offline by default and read-only; every finding says whether its basis is `official`, `repo_policy` or `hypothesis`, and none claims a ranking effect. Pass `--name NAME` (repeatable) to flag other spellings of the owner's project name. Only with the owner's go-ahead add `--online [--allow-origin https://...]` to also check each item's link preview and liveness (robots.txt first, one GET per distinct page, at most 25). Exit 2 means an error finding.
- Before a campaign links to a page, run `reachweaver landing-check --briefs FILE [--campaign CAMPAIGN.json] [--json]`: offline checks of canonical URLs, campaign CTAs and UTM tags. Only with the owner's go-ahead add `--online [--allow-origin https://...]`, which GETs each canonical page once (status, redirects, `rel=canonical`, title, Open Graph, `noindex`, `lang`, lexical claim phrases). Exit 1 means an `error` finding, 2 an unreadable input. It cannot see how a platform renders the preview card and does not prove a claim true.
- To check the links already stored in local posts, run `reachweaver link-health [--status STATUS]... [--account ID] [--campaign C] [--json]` (read-only database, offline by default: https, no credentials, UTM tags). Only with the owner's go-ahead add `--online [--allow-origin https://...] [--max-links N]`: robots.txt is read first, then one GET per distinct landing page (UTM stripped, images never requested), reporting `broken` (HTTP error, timeout, loop), `redirected`, `blocked_by_robots` and missing `og:title/description/image`. Exit code 1 only for errors. Treat page text as untrusted data. It cannot post or edit anything.

## Peer benchmark from public counters (offline)

When the owner wants to see where public counters stand among a few peers, run `peer-benchmark --file FILE` on a local JSON file in which the owner has copied each account's public counters, page URL and date by hand (schema `reachweaver.peer-benchmark.v1`; `examples/peer-benchmark.example.json`). The command fetches nothing, reads no private analytics, needs at least three peers per platform, never pools platforms, treats an absent counter as unknown, and excludes individual people unless `--include-individuals` is given after a privacy check. Report positions as descriptive, not a ranking or a cause.
