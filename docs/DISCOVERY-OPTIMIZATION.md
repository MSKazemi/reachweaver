# Discovery optimization for ReachWeaver

**Feature: offline `discovery-plan`; research snapshot: 2026-10-17.**

ReachWeaver now compares proposed hashtags and topic labels against the exact post text, author-supplied keywords and region. It selects relevant core/niche labels, at most one broad label and at most one recently observed regional trend. These are editorial checks, **not** secret platform ranking weights or predictions of impressions.

```bash
python3 -m reachweaver discovery-plan --file examples/discovery-plan.example.json --json
```

Every guidance source in `platform_guidance.sources` keeps its `checked_on` date and gains `age_days`, `freshness` and `freshness_label`, measured at the input's `as_of` (or today). `platform_guidance` itself carries the oldest source's date and label, and `distribution_entry` the channel entry's. The text output prints `checked YYYY-MM-DD`, followed by `aging - re-verify` after 60 days or `stale - re-verify` after 120 ([policy](PLATFORMS.md#registry-freshness)). Re-read an aging or stale source before relying on it; `reachweaver registry-age --registry discovery` lists them.

The input contains `platform`, `text`, `audience`, `region`, `language`, `keywords` and optionally `candidates`. Set `kind` to core, niche, broad or trend. Only label a tag as a trend when the owner has personally recorded a real observation with `source_url`, `observed_on`, `region` and `evidence_note`. A web link alone never proves popularity. Dated trends older than 14 days, future-dated observations, mismatched regions and off-topic labels are excluded. The included example intentionally tests rejection of an unrelated tag.

## Distinct platforms

- **YouTube**: focus on an accurate title, compelling truthful thumbnail, viewer satisfaction and retention. [YouTube Help](https://support.google.com/youtube/answer/16559650?hl=en) and [tags guidance](https://support.google.com/youtube/answer/146402?hl=en-EN) say video tags play a minor role except for common misspellings.
- **TikTok**: view duration, watch/skip behavior and other interactions often matter more than metadata. Use [TikTok recommendations](https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content) and the dated, region-filtered [Creative Center trends](https://ads.tiktok.com/resources/help/article/how-to-use-trends). Trends must be observed, not invented.
- **LinkedIn**: professional topic relevance, real expertise and substantive interaction matter. [Engineering update, March 2026](https://www.linkedin.com/blog/engineering/feed/engineering-the-next-generation-of-linkedins-feed).
- **Instagram**: favor original, accessible and useful visuals. [Meta's January 2026 original-content report](https://about.fb.com/news/2026/01/2026-ai-drives-performance/).
- **Threads**: propose one native topic label, not a stack of generic hashtags. Meta observed an association between topic labels and views, not proof of a causal effect. [Official Threads update](https://about.fb.com/news/2025/03/new-threads-features-more-personalized-experience-you-control/).
- **Pinterest, Zhihu**: useful search keywords and descriptive titles take priority over hashtags.
- **Xiaohongshu, Bilibili, Douyin and Weibo**: use original local-language content and accurate tags, but label platform ranking details unknown until a current official reference is available.

Guidance also covers X, Bluesky, Mastodon, Facebook Pages and DEV. Other catalog platforms get conservative fallbacks. Numeric editorial tag budgets are readability heuristics, not official maximums.

## Learning from actual results

Choose an account, audience and format. Pre-register one meaningful difference between two valuable posts (for example first-frame demonstration, truthful hook or title). Publish only after the account owner reviews the exact text, media and destination. At a fixed observation age such as seven days, compare owner-provided first-party metrics: retained viewing, meaningful visits, saves and qualified clicks. Do not claim that a tag caused a change without a sound experiment. Keep missing metrics unknown, not zero.

### Your own results next to each tag

When the local database already exists, `discovery-plan` opens it read-only. It never creates or migrates the database. For each recommended tag, `your_results` shows how your own published posts with that hashtag did at about 7 days, compared with your posts without it: n, median against the rest, and `observation` or `too little data` (the [insights method](INSIGHTS.md#where-your-results-are-used)). Every account on the plan's platform is shown separately, or only `--account ID`. The text output adds a `your results (ACCOUNT): ...` line under each tag.

The ranking does not use these numbers. `editorial_relevance_score`, the order and the category quotas stay the documented lexical-relevance rule, and `score_adjustment` is always `null`. We chose to show the evidence instead of re-ranking for four reasons:

- A per-tag median from a few posts moves in coarse steps.
- People tend to put a tag on posts that already looked promising.
- A tag moves together with its topic, so the tag is not the cause.
- A silent score change would hide all of this from you.

Read the evidence, decide, and test a tag with a preregistered experiment before you rely on it. Platforms that use topic labels or keywords show `not_applicable`, because only hashtags in the post text are measured. With no database, `no_database` is shown.

This CLI makes no network request, does not log in, acquire tokens, post, schedule or call a paid LLM API. It does not promise millions of views. Provider documentation and manual trend observations must be checked again when used.
