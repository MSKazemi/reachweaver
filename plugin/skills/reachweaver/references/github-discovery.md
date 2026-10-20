# GitHub-native discovery and public-release boundary

Use this reference when the user asks to **grow a GitHub repository's search visibility, compare a project/skill name, improve GitHub SEO, add topics, or make a private repo discoverable**. In a full ReachWeaver checkout, also use `docs/GITHUB-DISCOVERY.md` and `docs/PUBLIC-RELEASE-CHECKLIST.md`; a standalone copied skill might not include those external docs.

## Audit before suggesting tactics

1. Confirm the exact repository and current **visibility** from connected GitHub or an authorized inspection; don't infer public exposure from private access. An unauthenticated visitor cannot browse a private repo's README or documentation.
2. Read the real first screen, GitHub **About** (description/topics/homepage), README setup instructions, license, contribution paths, current supported capabilities, and release/security policy. A big README or unsupported claims can harm comprehension; prioritize discoverable intent and trustworthy proof.
3. Keep the established brand name if it is sufficiently distinctive and safe. Do not claim a trademark/domain/handle is free from a GitHub-name search alone. Verify brand searches and trademark rights separately when commercial investment is contemplated.
4. Recommend a **short descriptive subtitle**, then a maximum of 20 focused GitHub topics. Use actual search intents and human-readable content, not keyword stuffing or fake AI-search citations.
5. For a **private repository**, separate offline documentation/metadata preparation from the **independent owner decision to publish**. Never change repository visibility, expose history or turn on a site/Pages without deliberate owner authorization and historical secret/rights review. GitHub topics are public even on private repos; do not add confidential names.
6. Never claim SEO performance, crawl status, GitHub traffic, ChatGPT recommendation placement, conversion or stars without first-party measurement or a verified public observation. Propose a post-public baseline and track relevant clicks, contributor arrivals, usable issues and accepted PRs.

## ReachWeaver's own brand

- Product name stays **ReachWeaver**; describe it as a **Claude Code social media skill** for evidence-backed content planning, approval-first scheduling, publishing workflows and analytics.
- Its versioned GitHub About proposal is `.github/repository-about.json`; CLI `python3 scripts/repository_about.py` previews offline and **does not edit GitHub**. The exact owner-only `--apply --confirm MSKazemi/reachweaver` mode needs an authenticated local `gh` CLI with administration permission. It never changes visibility and never collects credentials in chat.
- Unverified homepage must remain blank. Source-code merges do not automatically set repository topics/About metadata. The owner must apply them through GitHub About or the explicit local helper.
- The repository documents guarded adapters and local planning; **real social account connections/receipts are not established solely by tests**. Do not promise automatic posting everywhere.

For other owned repositories, carry over the audit methodology and customize the wording; do not copy ReachWeaver's social-platform topics to a Kubernetes or accessibility repository.
