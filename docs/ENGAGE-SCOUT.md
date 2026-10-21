# Engagement scout (`engage-scout`, `engage-log`)

`reachweaver engage-scout` finds conversations where you can add something useful early, and ranks them into a short daily list. Each item gets a suggested **angle**: one or two bullets on what to bring, never a finished reply. You read the post, write the reply in your own words and post it yourself from the app. `reachweaver engage-log` records what you did, and its weekly readout lines your replies up with your follower, star and visit counts.

```
sources -> normalise -> filter -> score -> cap -> shortlist -> you reply -> engage-log -> weekly readout
```

## What it is not

The scout **never** posts, replies, likes, follows, reposts, votes or sends a DM, and it writes no reply text. There is no "post this for me" mode and none will be added. Automated or hidden promotion breaks platform rules (Bluesky's developer guidelines count "automated or bulk interactions, including any that would cause a notification to a user like a message, follow, like or reply" as spam; LinkedIn's User Agreement forbids using "bots or other unauthorized automated methods to ... create, comment on, like, share, or re-share posts"; GitHub's Acceptable Use Policies bar using its servers "to relay any form of unsolicited advertising or solicitation"), consumer-protection rules on undisclosed advertising, and ReachWeaver's own rule never to automate unauthorized engagement.

- **Human-only venues get no angle at all.** Reddit, Hacker News, DEV comments, Stack Overflow, Lobsters and every platform the [distribution registry](DISTRIBUTION-GOVERNANCE.md) marks `human_only` are listed under "read it yourself", with the venue's rule and nothing else.
- **No scraping and no login reuse.** X, LinkedIn, Threads, Reddit, YouTube and Stack Overflow conversations come only from links you paste into a file yourself.
- **No database.** The scout never opens the ReachWeaver database, so it never creates one.
- **No reach prediction.** The score orders conversations for you to read. It does not predict views, likes or followers.

## Quickstart

```bash
cp examples/engage-scout.example.json config/engage-scout.json     # edit projects, topics and your handles
reachweaver engage-scout --config config/engage-scout.json \
  --input examples/engage-candidates.example.json --now 2026-10-21T12:00:00Z
reachweaver engage-scout --config config/engage-scout.json --online --record state/engage-scout.json
reachweaver engage-log --state state/engage-scout.json --mark x-e5940be26464 --replied --with-link
reachweaver engage-log --state state/engage-scout.json --readout --outcomes my-outcomes.json
```

Without `--online` nothing touches the network; the output lists the GET requests an online run would send. `--input FILE` (repeatable) adds conversations you collected. `--platform P` narrows the run, `--limit N` shortens the list, `--optout FILE` names the opt-out list (default `$REACHWEAVER_OPTOUT` or `config/optout.json`), `--now` fixes the clock for a reproducible run and `--json` prints the machine-readable report. A bad config or input file exits with code 2 and writes nothing.

## Sources

| Platform | How candidates arrive | Scout treatment | Venue rule shown |
|---|---|---|---|
| Bluesky | `--online`: `app.bsky.feed.searchPosts` on `api.bsky.app`, plus one `getProfiles` call per 25 authors for follower counts; or your file | shortlist with angle | Reply yourself; automated replies count as spam |
| Mastodon | `--online`: `GET /api/v1/timelines/tag/:hashtag` on your `mastodon_server`; or your file | shortlist with angle | Reply yourself; follow the server's rules |
| GitHub issues | `--online`: `GET /search/issues` (open issues, newest first); or your file (for example saved `gh api` output, reshaped) | shortlist with angle | Comment only if you can help; no unsolicited advertising |
| X, LinkedIn, Threads, YouTube | your file only | shortlist with angle | Reply yourself in the app; no automated replies |
| Reddit, Hacker News, DEV, Stack Overflow, Lobsters | your file only | read it yourself: no angle, no link | Human-only; the venue's rule (for HN: "Don't post generated text or AI-edited text") |

Every online request is an HTTPS GET to an allow-listed host (`api.bsky.app`, `api.github.com`, your configured Mastodon server) with no credential, no cookie and no redirect followed, a 15 s timeout, a 2 MB body cap and the JSON nesting limit. Queries are bounded: at most 5 Bluesky searches, 5 Mastodon hashtags and 3 GitHub searches per run, each with at most `per_source_limit` results (default 25, maximum 40). A rate limit (HTTP 429, or GitHub's 403 with `x-ratelimit-remaining: 0`) stops that source at once with no retry. A Mastodon server that needs a token for hashtag timelines answers 401; the scout reports it and never sends a token.

The scout also respects the authors' own signals: Bluesky accounts with the `!no-unauthenticated` label (they asked apps not to show them to logged-out viewers), Mastodon accounts with `discoverable: false` or `noindex: true`, bot accounts, boosts and non-public posts are left out.

### Sources verified (2026-10-21)

Each claim below was read from the official page with `curl` on 2026-10-21, and each endpoint was called once without authentication.

- **Bluesky.** The [API hosts guide](https://docs.bsky.app/docs/api-directory): public endpoints "can be made directly against the Bluesky API, preferably via the https://public.api.bsky.app hostname, which includes additional caching." The [rate-limits page](https://docs.bsky.app/docs/rate-limits): the direct hosts `api.bsky.app` and `public.api.bsky.app` "do not support authentication" and "have generous rate-limits" (no number is given). The [searchPosts lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/packages/api/lexicons/app/bsky/feed/searchPosts.json) warns that the endpoint "may require authentication (eg, not public) for some service providers and implementations"; `limit` is 1-100 (default 25). **Observed:** `public.api.bsky.app/xrpc/app.bsky.feed.searchPosts` returned HTTP 403 from its CDN, while the same query on `api.bsky.app` returned HTTP 200 without auth, so the scout uses `api.bsky.app`. `getProfiles` takes at most 25 `actors` and returned `followersCount` without auth. The [developer guidelines](https://docs.bsky.app/docs/developer-guidelines) define spam as including "Generating automated or bulk interactions"; the scout makes none.
- **Mastodon.** The [timelines API](https://docs.joinmastodon.org/methods/timelines/#tag): `GET /api/v1/timelines/tag/:hashtag` is "OAuth: Public. Requires app token + read:statuses if the instance has disabled public preview", and returns 401 "when the server is configured to disable unauthenticated access to hashtag timelines"; `limit` "Defaults to 20 statuses. Max 40 statuses." The [rate limits](https://docs.joinmastodon.org/api/rate-limits/): "All endpoints and methods can be called 300 times within 5 minutes." Servers on 4.5 or newer publish `configuration.timelines_access.hashtag_feeds` in `/api/v2/instance` ([Instance entity](https://docs.joinmastodon.org/entities/Instance/)). **Observed:** `mastodon.social` reported `hashtag_feeds` `public` for local and remote posts (while its live feeds were `disabled`) and answered the hashtag timeline with HTTP 200 without auth (`x-ratelimit-limit: 300`). Other servers may differ.
- **GitHub.** The [search API](https://docs.github.com/en/rest/search/search): "For unauthenticated requests, the rate limit allows you to make up to 10 requests per minute", and search returns "up to 1,000 results for each search". The [Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies): "Scraping does not refer to the collection of information through our API", and "You may not use information from the Service (whether scraped, collected through our API, or obtained otherwise) for spamming purposes". **Observed:** `/search/issues` answered HTTP 200 without auth with `x-ratelimit-limit: 10`, `x-ratelimit-resource: search`.
- **Venue rules quoted in the output.** [Hacker News guidelines](https://news.ycombinator.com/newsguidelines.html): "Don't post generated text or AI-edited text. HN is for conversation between humans." and "Please don't use HN primarily for promotion." [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement): "Use bots or other unauthorized automated methods to access the Services, add or download contacts, send or redirect messages, create, comment on, like, share, or re-share posts, or otherwise drive inauthentic engagement" is listed among the things you agree not to do. Stack Overflow and Lobsters rules are the ones already recorded in the [distribution registry](COMMUNITY-AND-CREATOR-PLATFORMS-2026-10-15.md) (read 2026-10-15).

All three online sources work without a token today and their documentation permits reading public data through the API, so all three ship behind `--online`. If one starts requiring auth, the scout reports the HTTP status and you can keep using owner files for that platform.

## Config (`reachweaver.engage-scout-config`)

Validated strictly: unknown keys are refused, and any error exits 2 with nothing written. Template: [examples/engage-scout.example.json](../examples/engage-scout.example.json).

| Key | Default | Meaning |
|---|---|---|
| `projects` | required, 1-10 | `id`, `name`, `link` (your repo or page, public HTTPS), optional `docs`, and `topics`, `keywords`, `provides` (phrases). `provides` names what the project actually does; a link is suggested only when a post asks for exactly that |
| `hashtags` | `[]` | up to 5; Mastodon hashtag timelines, and terms for relevance |
| `search_terms` | first 3 keywords | up to 5; Bluesky and GitHub queries |
| `platforms` | all | which platforms the scout considers |
| `mastodon_server` | none | HTTPS origin of the Mastodon server to read hashtags from |
| `owner_handles` | `{}` | your own handles per platform; your own posts are dropped |
| `quiet_accounts` | `[]` | handles you do not want suggested |
| `daily_cap` | 7 | shortlist items per day (items already recorded today count) |
| `link_cap` | `{"links": 1, "per_replies": 10}` | at most 1 link in 10 of your recorded replies |
| `same_account_days` | 7 | the same account is suggested at most once in this many days |
| `audience_band` | 1,000-50,000 followers | the band where `audience_fit` peaks |
| `min_relevance` | 0.2 | items below it are dropped |
| `max_age_hours` | 24, GitHub 168 | older posts are dropped |
| `per_source_limit` | 25 (1-40) | results per online query |

These defaults answer the owner's open questions of 2026-10-21 (topics, platforms, daily cap) and can all be changed in the file.

## Candidates you collect (`reachweaver.engage-candidates`)

For platforms without an online source, paste the links you found into a file. Example: [examples/engage-candidates.example.json](../examples/engage-candidates.example.json) (synthetic X, LinkedIn, Threads, YouTube, Stack Overflow and Reddit items).

```json
{"schema_version": 1, "kind": "reachweaver.engage-candidates", "items": [
  {"platform": "x", "url": "https://x.com/example_maker/status/1980000000000000001",
   "author_handle": "example_maker", "created_at": "2026-10-21T09:30:00Z",
   "text": "Is there a tool for cross-posting ...?", "author_followers": 4200, "reply_count": 3}
]}
```

Required: `platform` (`bluesky`, `mastodon`, `github`, `x`, `linkedin`, `threads`, `reddit`, `youtube`, `stackoverflow`, `hackernews`, `devto`, `lobsters`), `url` (public HTTPS), `author_handle`, `created_at` (with timezone), and `text` or `title`. Optional: `author_followers`, `reply_count`, `labels` (GitHub labels), and the flags `locked`, `deleted`, `reply_restricted`, `owner_replied`, `author_is_bot`, `author_not_discoverable`. Unknown keys are refused. A missing number is unknown, never zero. The scout derives its own id from the URL (`x-e5940be26464`), so the same link is never listed twice.

## Filters (before scoring)

Each dropped item is counted by reason under `dropped`; an opted-out handle is never printed.

`duplicate`, `platform_not_selected`, `own_post`, `opt_out` (the [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list)), `quiet_account`, `bot_account`, `author_not_discoverable`, `already_engaged` (you marked it, or `owner_replied`), `already_shown` (recorded by an earlier `--record` run), `locked_deleted_or_reply_restricted` (including a Bluesky threadgate), `timestamp_in_future`, `too_old`, `spam_or_harassment`, `asks_not_to_be_contacted` (the post says "stop contacting me" or similar), `low_relevance`, then after scoring `same_account_within_days`, `same_account_this_run` and `daily_cap`.

## Score

`score = relevance × freshness × audience_fit × contribution × (1 − risk)`, each factor in 0..1, every factor and its inputs shown in the output (`factors`, `factor_detail`). All weights below are **ReachWeaver heuristics, not platform facts**.

- **relevance**: your config terms (topics, keywords, provides, hashtags) found as whole phrases after Unicode folding, as in `discovery-plan`. A match in the title or first line weighs 1.0, elsewhere 0.6; a term inside a longer matched term counts once; `relevance = 1 − e^(−sum)` (one first-line match 0.63, one body match 0.45).
- **freshness**: `0.5 ^ (age / half-life)`. Half-lives in hours: X 4; Bluesky, Threads 6; Mastodon, Hacker News 8; Reddit, Lobsters 12; LinkedIn 24; YouTube, Stack Overflow, DEV 48; GitHub 72.
- **audience_fit**: the geometric mean of a follower fit (1.0 inside the band, minus 0.4 per factor of ten outside it, at least 0.1; 0.5 when unknown) and a reply fit `1 / (1 + replies/10)` (0.6 when unknown), so niche accounts and posts with few replies so far rank higher.
- **contribution**: 0.15 plus 0.3 for a question, 0.3 for a request for help or advice, 0.25 for an error or problem report, 0.3 for a `help wanted`/`question`/`good first issue` label, 0.15 for a claim you can add a data point to; at most 1.
- **risk**: venue strictness (Bluesky 0.05; Mastodon, X, Threads, LinkedIn, YouTube, GitHub 0.1; human-only venues are never scored), plus 0.3 when instruction-like text was withheld or hidden control characters were removed, plus 0.1 per reply you recorded to the same account in 30 days (at most 0.3), plus 0.1 when the link cap is reached; at most 0.9.

## Caps and links

- At most `daily_cap` items a day, and the same account at most once a run and once per `same_account_days`.
- A link to your project is suggested only when the post asks for a tool or approach, the ask matches one of the project's `provides` phrases, the project has a `link`, and your last `per_replies − 1` recorded replies hold fewer than `links` links. At most that many link suggestions appear in one run.
- Every link suggestion carries the disclosure line **"Disclosure: I built this (it is my project)."** Use it whenever you post the link.

## Output

Text, or `--json` (`reachweaver.engage-scout.v1`): per item `rank`, `id`, `platform`, `url`, `author`, `age_hours`, `score`, the five `factors`, `factor_detail`, `why`, `angle` (one or two bullets), `link` (`allowed`, `reason`, `project_link`, `disclosure`), `venue_rule` and a cleaned `excerpt`. `read_it_yourself` lists human-only items with `angle: null` and `link: null`. Also `caps`, `dropped`, `dropped_items`, `sources` (status, requests, results and the doc URLs checked per source, for online runs) or `planned_requests` (offline), `reach_prediction: null` and the notes.

## Untrusted text

Post text is data, never instructions. It is cleaned before use: control and bidirectional characters removed, secrets, e-mail addresses and phone numbers redacted (`engagement.redact_private_data`), and every line that reads like an instruction to an assistant ("ignore previous instructions", "Claude, ...", a fake system prompt) replaced with `[withheld: instruction-like text; read the post yourself]`. The angle is built only from your config terms and fixed templates, so post text never reaches it. Spam, harassment and "do not contact me" posts are dropped.

## Recording what you did (`engage-log`)

`engage-scout --record STATE` writes the shortlist's ids to a private state file (mode 0600, atomic replace, symlinks refused), so the same items are not shown again; `--state FILE` reads it without writing. After you reply yourself:

```bash
reachweaver engage-log --state state/engage-scout.json --mark ID --replied [--with-link] [--at ISO]
reachweaver engage-log --state state/engage-scout.json --mark ID --skipped
```

The state file holds only ids, platforms, timestamps, a `link` flag and a 16-character hash of each author's handle (used for the same-account cap). It holds no handle, URL or post text.

## Weekly readout (correlational)

```bash
reachweaver engage-log --state state/engage-scout.json --readout [--outcomes FILE] [--weeks 4] [--json]
```

Per week: items shown, replies (and how many carried a link), skips, per-platform replies and, from an optional outcomes file you keep yourself ([example](../examples/engage-outcomes.example.json), `reachweaver.engage-outcomes`), the change in followers and stars and the latest profile-visit figure reported in that week. Values you did not record are `null`, never 0. The readout is **correlational only**: it does not show that a reply caused a follower or a star, because other posts, releases and platform changes happen in the same weeks. For link-level evidence use UTM links and [`attribution-report`](ATTRIBUTION.md); for per-post milestones use [`followup-suggest`](FOLLOWUPS.md).

## Privacy

- Data read: public posts returned by the three searches, or the file you supply. Nothing is read from an account, inbox or private timeline, and no credential exists in this path.
- Data stored: only the state file described above, and only with `--record` or `--mark`. Delete it any time; nothing else depends on it.
- Network destinations: `api.bsky.app`, `api.github.com` and the one Mastodon server you configure, only with `--online`. Your IP address and the User-Agent `ReachWeaver-engage-scout (read-only)` are visible to them.
- The printed report contains the public posts' handles, URLs and cleaned excerpts. Keep it on your machine like any other working note.

## Limits

- Follower counts are unknown for GitHub (the search result has none) and for owner-file items without `author_followers`; unknown counts score 0.5, never 0.
- The scout cannot see whether you already replied on a platform; mark it with `engage-log` or set `owner_replied` in your file.
- Bluesky search syntax and ranking are not specified by the lexicon; results can differ from the app's search.
- One run of the online sources makes at most 15 requests (5 + 2 + 5 + 3 including follower lookups).
