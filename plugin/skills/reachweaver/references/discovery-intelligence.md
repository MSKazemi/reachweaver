# Evidence-backed discovery and hashtag guidance

Read this for higher views, platform algorithms, relevant hashtags, trending topics, or post optimization. Also consult `platform-algorithms.md` for currently described recommendation signals and `measurement.md` for real results.

**Use Claude Code as the research/writing assistant.** ReachWeaver's standard-library runtime has no paid AI model API and cannot read live platform trends automatically.

1. Define the authorized brand, real audience, concrete goal, and a source-backed piece of content. Create a different, valuable opening and appropriate format for each destination.
2. Run `reachweaver discovery-plan --file examples/discovery-plan.example.json --json` (in the installed standalone skill, `scripts/run.py discovery-plan --file assets/discovery-plan.example.json --json`). Replace synthetic content with an owner-reviewed draft.
3. Select literal keywords that describe the content. The engine generates core/niche tag candidates from them; broad tags may be suggested when directly topical. A trending candidate requires an actual owner-observed date, region and source URL; observations older than 14 days are rejected. Supplying a URL is not proof of popularity.
4. For TikTok, a person can research [Creative Center trends](https://ads.tiktok.com/resources/help/article/how-to-use-trends) using the target region and recent time window. Record only what was actually observed, not an invented trend, post count or rank. Elsewhere use current official creator help and permitted first-party native insights.
5. Don't add unrelated `#viral`, celebrities, sports events or entertainment trends to technical content. Do not repeat identical posts across networks. Human-only venues are excluded from generated tag plans.
6. **Match the surface:** YouTube emphasizes title/thumbnail and viewer satisfaction, not video tag stuffing; Threads uses a native topic label; TikTok needs engaging original video and truthful captions; LinkedIn needs professional relevance; Chinese networks need accurate native-language review. Unknown algorithms remain marked unknown.
7. Run `seo-content-check` on candidate captions. Before any real send, the human reviews evidence, rights, identity and platform-specific terms. ReachWeaver must not run approval or live-posting actions.
8. Evaluate with owner-supplied native performance snapshots at matched ages; changing both hashtags and hook does not isolate a hashtag effect. An editorial relevance score is not a platform ranking score or a forecast.

Full source ledger and tested tool: `docs/DISCOVERY-OPTIMIZATION.md` and `scripts/reachweaver/discovery_registry.json`. No scraping, engagement bait, paid APIs, credential access, automated comments or claims of millions of guaranteed views.
