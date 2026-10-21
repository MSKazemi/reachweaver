# Platform risk: how much of your reach depends on one platform

If most of the people who see your work see it on one platform, a change there (new API prices, new terms, a new recommendation algorithm) touches most of your reach at once. Spreading over several platforms, and keeping part of your audience on an address you control, lowers that exposure. This is often called **multi-homing**.

`reachweaver platform-risk` shows where your recorded reach sits today, how concentrated it is, and which sourced risk flags apply to the platforms you depend on most. It only reads: the database is opened read-only (never created or migrated), and nothing is drafted, approved, scheduled, sent or fetched.

## Quick start

```bash
# Every published post, views at 7 days after posting
python3 -m reachweaver platform-risk

# Last 90 days, one account, machine-readable
python3 -m reachweaver platform-risk --since 90d --account my-linkedin --json

# Add an owned channel ReachWeaver does not post to (your own number, same window and counter)
python3 -m reachweaver platform-risk --owned-reach newsletter=1200 --owned-posts newsletter=4

# Your TikTok audience is in the US; point the canonical-home hint at your site
python3 -m reachweaver platform-risk --market tiktok@US --site https://example.org
```

With no database yet it says so and exits 0.

## What it measures

| Part | How it is computed | How sure it is |
|---|---|---|
| Reach per post | `--metric` (default `views`, or `impressions`) at a matched post age: the exact snapshot nearest `--age-hours` (default 168) within `--tolerance-hours` (default 48), the same nearest-age rule as `insights` and `baseline-eval`. Windowed snapshots are skipped. Without one, the latest exact snapshot of that post is used and counted as `latest_not_age_matched`. A post's snapshots are never summed. | Only what you recorded (`metrics-fetch`, `import-metrics`, `metrics`). |
| Basis | **Measured reach** when every platform in the scope has at least one measured post. Otherwise the scope **falls back to post counts** (published posts plus manual shares recorded with `mark-shared`/`share`), and the output says `FALLBACK` and names the platforms without a measurement. | A post count is not reach. Views are counted differently on every platform, so even a measured share compares unlike counters. |
| Concentration | Top-platform share, the Herfindahl-Hirschman index (HHI: the sum of each platform's squared share, near 0 when spread thin, 1 when everything is on one platform; also shown as points, 0-10000) and effective platforms (1 / HHI). | Arithmetic on the basis above. |
| Label | `concentrated` (under 1.5 effective platforms), `few` (under 2.5), `spread` (2.5 or more). | **Editorial thresholds** chosen for this tool, not an industry or legal standard. |
| Owned share | The share held by owned channels: `site`, `blog`, `newsletter`, `email`, anything you name with `--owned ID`, and every `--owned-reach` / `--owned-posts` entry. | An editorial hypothesis: an address or list you control keeps working if a network changes. Owned figures you type are your own numbers. |
| Scopes | `overall`, one row per account (`share_of_overall`), and one scope per persona when accounts carry the optional `persona` field. | |

`--owned-reach` and `--owned-posts` apply to the overall view only (not with `--account`).

## Risk flags (sourced, checked 2026-10-21)

Each flag quotes an official page and gives the hypothesis for your reach. Terms and prices change: re-read the source before acting.

| Platform | Flag | Quote | Source |
|---|---|---|---|
| X | Pay-per-use API | "All prices are per resource fetched (reads) or per request (writes/actions)." and "Post: Create (with URL) $0.200 per request" | [X API pricing](https://docs.x.com/x-api/getting-started/pricing) |
| LinkedIn | Automated posting not allowed by the API terms | 3.1 General Restrictions lists, among things you must not do: "Use the Content or the APIs to automate posting on the LinkedIn Services." | [LinkedIn API Terms of Use](https://www.linkedin.com/legal/l/api-terms-of-use) |
| Reddit | Commercial API use needs a separate agreement | "If you are interested in using the Data APIs for commercial purposes, research in excess of rate limits, or for any use that is not expressly permitted under the Data API Terms, then you will need to enter into a separate agreement with Reddit." | [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms) |
| TikTok | Separate US entity and recommendation algorithm (since 2026-01-23) | "The Joint Venture will retrain, test, and update the content recommendation algorithm on U.S. user data." The same announcement: "Interoperability enables the Joint Venture to provide U.S. users with a global TikTok experience" | [TikTok USDS Joint Venture announcement, 2026-01-23](https://newsroom.tiktok.com/announcement-from-the-new-tiktok-usds-joint-venture-llc?lang=en) |

A platform with no flag shows `no_flag_recorded`: nothing was checked for it here, which is not the same as no risk.

The flags live in `reachweaver/platform_risk_registry.json`. `platform_risk.validate_registry` requires an https URL, the exact quote and a checked date for every flag, and labels for the editorial parts (owned channels, thresholds); `tests/test_platform_risk.py` checks it.

## TikTok: US and global advice (`--market`)

`calendar-plan`, `distribution-plan` and `platform-risk` accept `--market US`, `--market global` or a platform-specific form such as `--market tiktok@US` (repeatable). When TikTok is in the plan, the output gains a `market_notices` entry:

- `applies: true` (you said the audience is in the US): "TikTok in the US: since 2026-01-23 a separate US joint venture holds the US recommendation algorithm, which it announced it will retrain, test and update on US user data. Timing and algorithm advice here comes from general (not US-specific) sources; re-verify it with your own US audience data."
- `applies: false` (another market): the same fact, noting that the advice is not US-specific.
- `applies: null` (no market given): the same fact, plus how to pass `--market tiktok@US`.

The notice only flags and cites. It does not change any slot, ranking or other advice, and it claims no specific difference between US and global distribution: none is documented on the cited page.

## Reading the output

Every advice line is a hypothesis with a way to check it, for example:

- "tiktok holds 54% of your recorded reach (few: about 2.49 effective platforms). A rule, price or algorithm change there would touch that share."
- "An owned channel holds 14% of your recorded reach." or, when none does, a suggestion to publish the next piece first on your canonical home (`social-web-plan` decides which one; pass `--site` or `--setup` to use it here).
- One line per flagged platform holding at least 20% of the basis, with the source to re-read.

## What it is not

- **Not a forecast.** It says where recorded reach was, not where it will be or what would happen if a platform changed.
- **Not complete.** Reach you did not record through ReachWeaver (posts made elsewhere, unrecorded metrics) is invisible.
- **Not a legal or terms review.** A flag quotes a page; whether your use fits a platform's terms is your call.
