# Offline follower-polling design (issue #15 slice)

Status: DESIGN ONLY. Nothing here polls a network, stores a follower count or adds a CLI command. It records how follower counts would fit the existing metric pipeline so a later, separately reviewed slice can implement it. It is not consent to publish and does not touch approval, dry-run, emergency-stop or credential rules.

## 1. Why followers are not a post metric

`metric_snapshots` is keyed by `post_id`. A follower count belongs to an **account**, so forcing it into a post snapshot would invent a post that does not exist and would let `report` sum it with per-post values. Follower history therefore needs its own table and never feeds the exact per-post totals in `report`.

## 2. Proposed shape (not implemented)

| Item | Proposal |
|---|---|
| Table | `account_snapshots(account_id, observed_at, source, followers, following, metric_status)` with `UNIQUE(account_id, observed_at, source)`, upsert like `store_metrics` |
| Values | same grammar as `parse_metric_value`: a nonnegative integer, `unknown`, `suppressed`, `not_available`, `delayed`, `estimated:N`. Missing is never 0 |
| Sources | only public, first-party account counters (for example the public profile counters of a Bluesky or Mastodon account). A provider whose follower field is not documented stays `not_available` |
| Cadence | explicit `--online` only, sequential, capped per run, one request per account, a provider 429 stops the run with no retry (same rules as `metrics-fetch`). No daemon, no scheduler, no background loop |
| Identity | the response must name the configured account (DID or local handle on the configured server) or the row is rejected, as `metrics-fetch` already does for posts |
| Dry run | `--dry-run` prints the planned GET requests and touches neither network nor database |
| Credentials | none for public counters. Any token fallback needs its own review; this design does not add one |

## 3. Definitions differ per provider

The offline `mockstats` provider in `reachweaver/metrics_fetch.py` shows the rule the follower slice must follow: map a native field to a snapshot column **only when the documented definition is the same**. `unique_viewers` (distinct people) is not `impressions`, `reactions_all` is not `likes`, and `plays_3s` is `views` only because the provider defines a view that way. For followers the same caution applies: "followers" may be an approximate or rounded value, may include or exclude pending requests, and may be hidden by the account owner. Record the definition in the registry entry for the provider and leave the column unknown when it is not documented.

## 4. Privacy thresholds

Providers withhold small audience numbers. A count below the provider's minimum is stored as `suppressed` and the small number is **not** stored, so it cannot be summed as exact or re-exposed. `normalize_mockstats` implements this for per-post counts with fixtures in `tests/fixtures/providers/mockstats/threshold_cases.json`. A hidden follower count (profile setting) maps to `not_available`, a not-yet-computed one to `delayed`. Reports print `unknown` or the status label, never 0, and show a legend line when any value is `suppressed`.

## 5. Test plan for the later slice

1. Mock `reachweaver.publishers._send_http` (as `tests/test_metrics_fetch.py` does) and assert only GET requests leave the process.
2. Fixtures per provider case: normal, hidden, below threshold, pending, malformed (negative, bool, string, float), wrong account, HTTP 429 (run stops, no retry).
3. Report tests: follower history never enters the per-post totals; zero coverage prints `unknown`.
4. Run `python3 scripts/sync_skill.py`, then the offline test suite. Never run live polling from tests or CI.

## 6. Open questions for the owner

- Which providers document a public follower field? Each needs a cited source before a mapping exists.
- Retention: follower history is account-level personal data. It should follow [PRIVACY-LIFECYCLE.md](PRIVACY-LIFECYCLE.md) before any table is added.
