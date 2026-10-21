# AI answer engines and search visibility (`ai-visibility`)

People now find pages through AI answers as well as search results: ChatGPT search, Perplexity, Google's AI Overviews and AI Mode, and Microsoft Copilot. Social posts on TikTok, Instagram, X and YouTube also show up in Google Search. This guide covers what you control on **your own pages**: which crawlers your robots.txt lets in, and which page signals the search operators document. It also covers how you can measure the result.

**Nothing here guarantees that a page is crawled, indexed, cited or ranked.** Google says so plainly: *"Just because a page meets all requirements, best practices, and complies with the policies, doesn't mean that Google will crawl, index, or serve its content."* ([AI features and your website](https://developers.google.com/search/docs/appearance/ai-features), checked 2026-10-21).

## Run it

```bash
# Offline (default): a saved copy of your page and your robots.txt. No network.
reachweaver ai-visibility --url https://docs.example.org/guide --html saved/guide.html --robots saved/robots.txt
# Offline, taking the page address from one of your SEO briefs
reachweaver ai-visibility --briefs seo-briefs.json --page-id guide --robots saved/robots.txt
# Online (explicit opt-in): only the canonical_url pages of your own SEO briefs
reachweaver ai-visibility --briefs seo-briefs.json --online [--page-id guide] [--allow-origin https://www.example.net] [--json]
```

- **Your pages only.** `--online` fetches only the `canonical_url` of each active brief in your [SEO briefs](SEO-INTEGRATION.md) file (or the ones you name with `--page-id`). It never takes an arbitrary URL. `--url`, `--html` and `--robots` are offline only.
- **What `--online` sends.** One GET for robots.txt per host, one GET per page, and one GET for `/llms.txt` per origin. These are the same guarded requests that `landing-check` makes: robots.txt is honoured for ReachWeaver's own user agent, a redirect off the brief's domain is not followed unless you allow it with `--allow-origin`, bodies are size-capped, and IPv4 is tried first.
- **Exit codes.** `0` when there is no error (warnings and info do not fail), `1` when a page could not be checked, and `2` for bad input. The command never opens the database and never drafts, approves, schedules or publishes anything.

## Crawler controls: what each one means

The report lists, for every crawler below, whether your robots.txt **allows** or **blocks** the page, and whether the file **names** the crawler or only reaches it through the `*` group (`not_mentioned_allowed` / `not_mentioned_blocked`). The matcher follows [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html): a group's user-agent must match the crawler's name (case does not matter), groups for the same crawler are merged, `*` is the fallback, the longest matching rule wins, and `allow` wins a tie. A missing robots.txt (HTTP 4xx) allows everything. If robots.txt is unreachable (server or network error), RFC 9309 tells crawlers to assume everything is disallowed.

| Crawler | Operator | Role | What blocking it means, in the operator's words (checked 2026-10-21) |
|---|---|---|---|
| `Googlebot` | Google | search | "robots.txt directives for Googlebot is the control for site owners to manage access to how their sites are crawled for Search", AI features included ([source](https://developers.google.com/search/docs/appearance/ai-features)) |
| `Google-Extended` | Google | training and grounding | Controls Gemini training and grounding. "Google-Extended does not impact a site's inclusion in Google Search nor is it used as a ranking signal in Google Search." It is a robots.txt token only and sends no requests of its own ([source](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers)) |
| `OAI-SearchBot` | OpenAI | search | "Sites that are opted out of OAI-SearchBot will not be shown in ChatGPT search answers, though can still appear as navigational links." ([source](https://platform.openai.com/docs/bots)) |
| `GPTBot` | OpenAI | training | "Disallowing GPTBot indicates a site’s content should not be used in training generative AI foundation models." OpenAI: "Each setting is independent of the others" ([source](https://platform.openai.com/docs/bots)) |
| `ChatGPT-User` | OpenAI | user fetch | "Because these actions are initiated by a user, robots.txt rules may not apply." and "ChatGPT-User is not used to determine whether content may appear in Search." ([source](https://platform.openai.com/docs/bots)) |
| `PerplexityBot` | Perplexity | search | "PerplexityBot is designed to surface and link websites in search results on Perplexity. It is not used to crawl content for AI foundation models." ([source](https://docs.perplexity.ai/guides/bots)) |
| `Perplexity-User` | Perplexity | user fetch | "Since a user requested the fetch, this fetcher generally ignores robots.txt rules." ([source](https://docs.perplexity.ai/guides/bots)) |
| `Bingbot` | Microsoft | search | "Bing respects all content owner preferences expressed through robots.txt and other supported control mechanisms." ([source](https://blogs.bing.com/webmaster/2026/2/Introducing-AI-Performance-in-Bing-Webmaster-Tools-Public-Preview/)). Bing's help pages need JavaScript and could not be read with `curl` on 2026-10-21, so the report does not state the effect on each Bing surface |

How the report grades a block:

- **Search crawler blocked** (`search_crawler_blocked`, warning): the page is likely to be missing from that engine's answers. It is your choice, but check that it is deliberate.
- **Training crawler blocked** (`training_crawler_blocked`, info): a legitimate choice. The operators document it as separate from search (`GPTBot` versus `OAI-SearchBot`; `Google-Extended` versus `Googlebot`), so it is not a search fault.
- **User-fetch agent blocked** (`user_fetch_blocked`, info): the operators say robots.txt may not apply to these requests, so a block may have no effect.

The full registry, with every quote, URL and checked date, is [`reachweaver/ai_search_registry.json`](../reachweaver/ai_search_registry.json). It is validated by `reachweaver/ai_visibility.py` and `tests/test_ai_visibility.py`.

## Page signals

| Signal | Finding | Evidence |
|---|---|---|
| `<title>` and meta description present | `title_missing`, `meta_description_missing` (warning) | Basic page metadata |
| `<link rel=canonical>` present, single, and the same as the page address | `canonical_link_missing`, `canonical_link_multiple`, `canonical_mismatch`, `canonical_link_invalid` (warning) | Same rule as `landing-check` |
| `noindex` (meta `robots`, `googlebot` or `bingbot`, or the `X-Robots-Tag` header when online) | `noindex` (warning) | Google: to be a supporting link in AI Overviews or AI Mode, "a page must be indexed and eligible to be shown in Google Search with a snippet" ([source](https://developers.google.com/search/docs/appearance/ai-features)) |
| `nosnippet` | `nosnippet` (warning) | Google: it "will also prevent the content from being used as a direct input for AI Overviews and AI Mode" ([source](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag)) |
| `max-snippet` | `max_snippet` (info) | Google: it "will also limit how much of the content may be used as a direct input for AI Overviews and AI Mode" (same source) |
| `noarchive` / `nocache` for Bing | `bing_noarchive_nocache` (info) | Bing's 2023 statement about Bing Chat: "Content tagged NOARCHIVE will not be included in Bing Chat answers, not be linked to in the answers." ([source](https://blogs.bing.com/webmaster/september-2023/Announcing-new-options-for-webmasters-to-control-usage-of-their-content-in-Bing-Chat)). It is a 2023 statement, so check current Bing documentation |
| JSON-LD structured data and its `@type` values | `structured_data_missing` (info), `json_ld_invalid` (warning) | Google "recommends using JSON-LD for structured data" ([source](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)). Google also says you do not need special markup to appear in its AI features ([source](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)), so a page without it is reported as information |
| `<meta name="fediverse:creator" content="@you@server">` | `fediverse_creator_missing` (info), `fediverse_creator_invalid` (warning) | Mastodon shows the account as the author when the page is shared. "The handle can be any fediverse account, not just Mastodon." ([source](https://blog.joinmastodon.org/2024/07/highlighting-journalism-on-mastodon/)). This is attribution, not a search control |
| `/llms.txt` | always information, never a finding | **Optional; no major engine has confirmed it reads it.** It is a [community proposal](https://llmstxt.org/). Google: "For Google Search, you can ignore tactics like "chunking" content, creating unnecessary AI text files (like llms.txt)" ([source](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)). The report shows `present`, `absent` or `not_checked` and never treats it as a ranking factor |

The report also counts elements marked `data-nosnippet`. It does not judge them.

## Evidence versus hypothesis

- **Evidence (operator documentation):** every row in the two tables above, quoted from the operator's own page on 2026-10-21.
- **What the report computes:** what your robots.txt and HTML *ask* for. It cannot see whether an operator obeys, how often it crawls, or whether your page is cited.
- **Hypothesis (not supported by these sources):** that any tag, file or markup makes an AI engine cite you more often. ReachWeaver does not claim this, and nothing here guarantees citation.

## Measure the result

Measure with the operators' own reports, not with guesses:

- **Google Search Console, Generative AI performance report.** Google: "To measure how your content is performing in generative AI features on Google Search and Discover, use the Generative AI performance report in Search Console." ([source](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide)).
- **Search Console platform properties.** Search data "is also available for your TikTok, Instagram, X, or YouTube content" ([source](https://developers.google.com/search/docs/monitor-debug/analyze-social-video-content)). Google says the feature is "rolling out gradually, so it might not be available to everyone yet" ([source](https://support.google.com/webmasters/answer/17148418?hl=en)).
- **Bing Webmaster Tools, AI Performance (public preview).** Bing: "For the first time, you can understand how often your content is cited in generative answers" ([source](https://blogs.bing.com/webmaster/2026/2/Introducing-AI-Performance-in-Bing-Webmaster-Tools-Public-Preview/)).

## Your Google Search profile

`profile-audit` has a `google_search` entry with two checks, each with its official source:

- **Claim your Google Search profile** (`search_profile_claimed`). "A Search profile brings together your content from across the web and social platforms into a single destination on Google." ([source](https://developers.google.com/search/docs/appearance/search-profiles)). Google's help, checked 2026-10-21, lists Search profiles as available in Canada, India and the United States, for people aged 18 or over with at least 10,000 subscribers or followers on YouTube, Instagram, X or TikTok ([source](https://support.google.com/websearch/answer/16904498?hl=en)). If you do not qualify, record that; it is not a fault.
- **Check Search Console platform properties** for your TikTok, Instagram, X and YouTube accounts (`search_console_platform_properties`), as described above.

Record both as `true`, `false` or blank (unknown) in your profile snapshot. See [Profile and post audits](PROFILE-AND-POST-AUDITS.md).
