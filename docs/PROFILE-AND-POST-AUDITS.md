# Platform-by-platform profile and post audits

**Reviewed:** 2026-10-11 · **Scope:** [Issue #3](https://github.com/MSKazemi/reachweaver/issues/3) and developer growth. These are **offline, user-supplied audit workflows**. They do not log in, inspect private accounts, scrape social networks, change profiles, generate fake engagement or publish anything.

## One workflow per network, two independent scorecards

1. **Profile audit** — completeness/credibility/identity/discovery of a particular account. It uses the specific profile fields documented or provisionally hypothesized for that network. The output includes observations, unknowns, a prioritized fix list, topical advice and the evidence source. A score is reported only when **at least 60%** of the applicable fields were actually assessed. It is a **heuristic percentage of observed present fields**, **not a platform algorithm, quality grade or predicted reach**.
2. **Post audit** — evaluate the manually reviewed hook, proof, originality, CTA, accessibility and community requirements of each post, plus platform-native performance numbers if the operator supplied snapshots. Rates are compared **only** for the same account/platform/format/source and compatible age after publication. Fewer than **three comparable posts** per format means the median is **unknown**.
3. **Plan human changes**, never automatic optimization of accounts. Write a relevant bio/headline suggestion, draft a better first-frame/hook, schedule a manual experiment, or visit native analytics. Different platforms may require different account roles, eligibility and moderation policies.
4. **Measure**, with observations at +24 hours, +7 days and optionally +28 days using each platform's definitions, and track meaningful external outcomes when available. Views, impressions, reach, Shorts engaged views, interactions and GitHub traffic are different metrics, not additive or interchangeable.
5. **Re-audit**, preserve before/after evidence, and report confidence, not a viral-growth guarantee. A post's popularity is not proof that one copywriting rule caused it.

## Platform-specific profile and content strategies

The machine-readable source of truth is `reachweaver/profile_audit_registry.json`. Profile fields below are **recommendation checks**, not a claim that every account tier has every feature. Where an official link is unavailable, the whole row is an **editorial hypothesis needing live provider verification**.

| Platform | Profile details to audit | Initial content experiment | Native measurement and caution |
| --- | --- | --- | --- |
| **LinkedIn** | Authentic headshot, clear role/keywords in headline, credible About, work sample, website, relevant banner | Original design decision/diagram with real trade-offs; short technical video or evidence-backed document | Reach/impressions, conversation quality, link clicks; distinguish *personal* from *Page* account and eligible Featured features |
| **DEV Community (DEV.to)** | About, avatar, relevant tags, technical articles, portfolio link | Detailed original reproducible walkthrough and helpful code snippets | Article views, quality comments and downstream repo visits; editorial standards and transparent AI assistance |
| **YouTube** | Banner, channel About/handle, trailer, sections and useful playlists | Real software demo; searchable accurate title/thumbnail and compelling first 30 seconds; separately trial Shorts | Traffic sources, CTR and retention for eligible videos; **publish time isn't known to affect long-term video performance** |
| **X** | Bio (official 160-character max), header, one destination link, pinned proof | Standalone engineering lesson, screenshot, useful follow-up thread | Impressions/reposts/clicks; no magical hashtag/engagement weights; require original discussion |
| **Bluesky** | Honest bio and avatar, website/domain handle when authorized, context-rich pinned post, relevant starter packs | Reproducible observation that belongs in a specific developer custom feed | Reposts and genuine discussion where observable; discovery depends on multiple user-selected feeds |
| **Mastodon** | Instance-aware bio (default 500 characters), link verification/profile metadata, accessible header/avatar | Useful technical explanation, relevant hashtags and alt text; ask knowledgeable community members | Boosts, favorites and replies; instance metrics differ; do **not** invent global impressions |
| **Threads** | Clear identity/bio, relevant topic signals, useful pinned context where eligible | Short interesting idea and authentic question/reply | Views/replies measured by native insights when available; avoid engagement bait |
| **Reddit** | Community-specific history and rules, authentic profile, disclosure when referencing owned projects | Answer a real subreddit problem thoroughly; owner writes/submits manually | Meaningful comments/upvotes and approved referrals; **moderation/rules outrank reach advice**, no bulk publishing |
| **Hacker News** | Profile About/link and genuine history; know submission guidelines | **Human-written, human-posted only** original technical story, when appropriate | Constructive discussion only; no AI-generated or AI-edited HN submission text |
| **TikTok** | Clear creator identity, original samples, profile CTA as account eligibility permits | Visual before/after demo, clear captions and retention-first structure | Watch behavior, shares, qualified profile actions; no single magic posting hour |
| **Instagram** | Bio, avatar, relevant links, Highlights, original pinned examples and category if eligible | Original Reels and multi-step teaching carousels with readable overlays | Native reach, shares, saves, retention; check Professional Dashboard's updated guidance |
| **Facebook Pages** | Business contact/website, accurate description, CTA, cover and avatar | Original demonstration or case-study Reel; real community discussion | Native reach, shares, qualified actions; Meta explicitly deprioritizes trivial duplicate uploads |
| **Telegram** | Accurate channel title/photo/description, consented invite, pinned guide | Concise opt-in updates that members genuinely forward | Views and forwarding; channel reach is subscriber-led, NOT an Instagram-style For You algorithm |
| **Discord** | Clearly moderated welcome/resources and member permissions | Human community help and release walkthrough | Genuine questions and member retention; do not auto-send promotional messages |
| **Product Hunt** | Real maker identity, demo, truthful description, relevant product assets | Substantial product launch, accessible video, meaningful maker comment | Genuine feedback/conversion; **do not solicit votes** |
| **Pinterest** | Searchable boards and profile, connected owned site, original visual identity | Evergreen diagrams, how-to graphics and properly explained destination pages | Impressions, saves, outbound actions; claim owned website when eligible |
| **Hashnode** | Complete author page, useful portfolio, readable articles, canonical links | Original technical tutorial; compare search discoverability to DEV | Reader feedback and qualified site visits; scheduling/account availability varies by plan |
| **Indie Hackers** | Genuine founder/project details and relevant history | Specific build decisions/lessons from real experiments | Relevant feedback and collaboration inquiries; human-only participation |
| **Peerlist** | Project portfolio, clear expertise and links | Original verified project-update case study | Genuine builder connections and referrals; scheduling not verified |
| **Bilibili** | Chinese-language channel positioning and original video samples | Localized technical demo with subtitles and provenance | View/retention metrics only as owner export; account eligibility and official ranking details **not verified** |
| **Zhihu** | Chinese bio, credible professional identity and answer portfolio | Evidence-rich technical answer to a real question | Meaningful comments and follow-up; platform ranking specifics **not verified** |
| **OSCHINA** | Engineer profile, article portfolio and source-code links | Practical Chinese-language implementation walkthrough | Developer feedback/conversions; official ranking weights **not verified** |
| **GitHub** | Pinned repos, concise README, correct About/topics, quickstart, contributor guide | Useful actual release, architecture guide, reproducible example | Qualified traffic and accepted contributions; GitHub is not a generic social impression feed |
| **Snapchat** | Clear creator identity and proven mobile-native content | Original short vertical demo, tested with a defined audience | Native views/shares when exported; suitability for technical B2B is **hypothesis** |
| **WhatsApp Channels** | Owner-controlled opt-in identity and accurate channel description | Permissioned high-value subscriber announcements | Forwarding/retention only where available; **no unsolicited DMs or mass invitations** |

Primary documentation and evidence dates are saved per platform in [the registry](../reachweaver/profile_audit_registry.json). High-confidence **capability facts**, not undocumented secret weights, include:

- [LinkedIn profile intro](https://www.linkedin.com/help/linkedin/answer/a547248) and [Page best practices](https://business.linkedin.com/advertise/linkedin-pages/best-practices).
- [YouTube channel customization](https://support.google.com/youtube/answer/3027950?hl=en), [retention analysis](https://support.google.com/youtube/answer/9314415) and [publish-time FAQ](https://support.google.com/youtube/answer/141805?hl=en).
- [TikTok content recommendation](https://support.tiktok.com/en/using-tiktok/exploring-videos/how-tiktok-recommends-content); [Instagram Professional Dashboard](https://about.fb.com/news/2024/10/best-practices-education-hub-creators-instagram/); [Facebook original content](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/).
- [X profile fields and 160-character bio](https://help.x.com/en/managing-your-account/how-to-customize-your-profile); [Bluesky custom feeds](https://bsky.social/about/blog/7-27-2023-custom-feeds); [Mastodon default bio/profile metadata](https://docs.joinmastodon.org/user/profile/).
- [DEV editor, analytics and article scheduling](https://dev.to/help/writing-editing-scheduling); [Pinterest claimed website](https://help.pinterest.com/en/business/article/claim-your-website); [Product Hunt launch guide](https://help.producthunt.com/en/articles/17350772-launch-guide), including its prohibition on soliciting upvotes.
- [Reddit policies](https://redditinc.com/policies/reddit-rules) and [Hacker News submission guidelines](https://news.ycombinator.com/newsguidelines.html). Neither permits the generic bulk automation playbook.

## Optional owner-authorized native CSV observations

To avoid hand-editing large JSON post lists, supply a **normalized, local, owner-authorized** post metrics CSV. Both `post-audit` and `suggest` can merge it in memory (never a network call) when passed `--metrics-csv PATH --owner-authorized`. New rows must match the platform/account already present in the profile JSON and may not duplicate an existing post. A blank numeric cell means *unknown*. More details, example and the exact header contract: [PROFILE-METRICS-CSV.md](PROFILE-METRICS-CSV.md). This is not a verified private API connection or a social algorithm model.

## Input file and safe commands

Start with the credential-free **illustrative** snapshot: [`examples/profile-post-audit.example.json`](../examples/profile-post-audit.example.json). Replace sample IDs and fields with information from **your own, permitted account**, without credentials, emails, cookies or private audience data. Keep real exports in `state/` (gitignored), NOT in GitHub.

```bash
# These only read an existing local JSON file. No state DB or network call.
reachweaver profile-audit --file examples/profile-post-audit.example.json
reachweaver profile-audit --file state/my-social-snapshot.json --platform linkedin --json
reachweaver post-audit --file state/my-social-snapshot.json --age-hours 168 --tolerance-hours 24 --min-posts 3 --json
```

The snapshot uses `schema_version: "reachweaver.profile-post-audit.v1"`, a list of `profiles`, optional `brand_name`/`preferred_handle`, and optional `posts`. Each profile has `platform`, `account_id`, `handle`, optional `profile_kind`, `fields` (platform rules), and optional `keywords`. All field values may be **null**, meaning not observed; explicit `false` means the owner actually checked and found the feature missing. Profile completion is **not** calculated from missing fields. For organizations, handle/name deviations from the declared brand are **advisory**, not score penalties.

An optional post observation has `platform`, `account_id`, `post_id`, `format`, `source` (the exact owner's native export label), `published_at`, `observed_at` (offset-aware ISO 8601), `metrics` and `review_flags` (explicit booleans for hook, proof, originality, CTA, captions, alt text and policy review). The CLI never guesses these qualitative flags from a model. It never contacts an account to verify the fields.

The post report returns a per-post fix list and a **descriptive action/exposure rate**, when that particular platform/source exports both comparable native metrics. Rates are grouped only for the same **platform + account + format + source**, at matching ages. The command does **not** predict expected views and does not rank accounts using synthetic weights. Some platforms do not export both metrics, resulting in `insufficient_data` rather than a fake zero.

## Audit-based improvement procedure

For each platform, produce one human-reviewed action sheet:

| Field | Example |
| --- | --- |
| Evidence | Owner-provided bio and native impressions; official URL supporting actual profile fields |
| Profile weaknesses | Missing featured technical proof; no clear action for visitors; unverified account eligibility |
| One first improvement | Replace vague professional headline with honest expertise + concrete project outcome |
| Content hypothesis | One original screencast vs one structured technical document explaining the same verified feature |
| Exposure metric | Native platform impressions or views — use the **same exact definition** within an experiment |
| Action metric | Qualified clicks or shares, depending on documented availability |
| Publishing | Native manual handoff unless a provider adapter, credentials, scopes and exact approval have been verified |
| Retest | +24 h / +7 d / +28 d; matched topic/format/age; no success claim without source |

For actual post text, use `references/creative-playbook.md` and `references/platform-playbooks.md` with **fresh** account context. Do not manufacture testimonials, inflate follower counts, generate fake results, automate engagement or evade moderation. An audit should prioritize *what to test next*, not promise a particular number of likes.

## Known gaps

- **No automatic profile fetch or account login.** Public URLs/screenshots may help a human audit manually, but this command never reads a live profile or configures accounts.
- **No long-term causal attribution.** Observational creative flags and median rates cannot prove recommendation ranking or generate statistically sound uplift without experimental controls.
- **No exact bio-length guarantee for most networks.** Only documented X and Mastodon default limits are annotated; Mastodon servers can differ and Unicode/provider counters vary.
- **No automated post creation or scheduling.** The profile/post audit commands do not call `draft`, `approve`, `schedule`, `publish` or change any account.
- **No deep visual or language quality scoring.** A person's judgement is needed for screenshots, creative hooks, tone, community suitability and multilingual nuance.
- **No universal social exposure denominator.** Native impressions, views, reach and follower snapshots are not comparable between platforms or interchangeable.
- **No credentials or raw private data in output.** The loader rejects credential-like fields/values and oversized input; outputs only audit findings and aggregate metrics.

Future slices can add explicit read-only OAuth/native profile connectors with consent, versioned platform-specific media tests, a human-in-the-loop creative review UI, private account native outcome exports and a preregistered controlled test protocol.
