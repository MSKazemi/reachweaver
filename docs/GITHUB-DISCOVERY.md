# ReachWeaver: GitHub discovery, positioning and search strategy

**Reviewed:** 2026-10-11. **Decision:** keep the brand **ReachWeaver** and add a precise descriptive category. This is a positioning/repository-discovery checklist, not an observed search-rank improvement or trademark clearance.

## Identity and search-intent map

| Element | Recommended wording | Why |
| --- | --- | --- |
| Brand | **ReachWeaver** | Distinctive product name; preserve GitHub paths, package metadata and existing documentation |
| Category | **Claude Code social media skill** | Directly describes the user/product intersection |
| Functional descriptor | **Approval-first social media planning, scheduling and analytics** | Exposes the use case without promising universal live publication |
| GitHub About description | **Claude Code skill for evidence-backed social media planning, approval-first scheduling, publishing workflows, and analytics.** | Clear, short, searchable and compatible with actual local/draft capabilities |
| Homepage URL | **Unset** | No dedicated public ReachWeaver landing page/domain has been verified in the repository |
| Search-intent phrases | `Claude Code social media skill`, `approval-first social publishing`, `social media scheduling CLI`, `social media content planning for developers`, `open-source social media workflow` | Use naturally where directly relevant rather than repeating all terms on every page |
| Core promise | Turn verified project evidence into platform-specific content, human-review posts and measure real performance | Differentiates the tool from spam bots and generic AI copy generators |

Do **not** describe offline test coverage as proof of real connected social accounts, official third-party API approval, public ranking success, contributor growth or guaranteed algorithmic reach. Some adapters remain mock-tested and capability-registry-disabled. Check [the current platform matrix](PLATFORM-MATRIX.md), [release-readiness scorecard](release-readiness.json), and [quickstart](QUICKSTART.md) before making public feature claims.

## Concrete GitHub About metadata

The source of truth is [`.github/repository-about.json`](../.github/repository-about.json). Use its topic list and description instead of keeping multiple hard-coded variations in documents or scripts.

**At the time of review:** GitHub reports `MSKazemi/reachweaver` as a **private** repository, with **zero configured topics** and **no homepage URL**. Updating a README inside a private repository does not make it discoverable to the public. GitHub supports topics for public and private repositories, but private projects are only visible to authorized viewers. **Topic names themselves are public**, even when assigned to a private repo. [GitHub topic guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics).

### Owner action (no credentials in ChatGPT or Git)

To review the proposed About content offline:

```bash
python3 scripts/repository_about.py
```

To apply the **description and topics only**, after inspecting the manifest on your own trusted computer and authenticating there with GitHub CLI:

```bash
gh auth status
python3 scripts/repository_about.py --apply --confirm MSKazemi/reachweaver
```

This script **never updates visibility, private/public flags, features, collaborators, branch protections or billing**. It first verifies the authenticated repository identity; then it updates changed About fields and merges proposed topics with existing topics, refusing a combined list over GitHub's limit of 20. The homepage stays untouched while its manifest field is `null`. It doesn't need or print any credential itself, but `gh` must already have the required **Administration: write** repository permission. [GitHub REST documentation](https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28).

The connected GitHub integration used for editing source code does **not** offer a repository-About/topics mutation method. Therefore simply merging this file **does not change the live GitHub About metadata**. This owner action is separate and must be verified in GitHub after applying it.

An equivalent manual option: open the repository's **About** settings (gear icon), enter the manifest's description and topics, and save. Do not add an unverified homepage or a domain that might later belong to somebody else.

## README and documentation funnel

| Reader arrives searching for… | First useful destination | Action to complete |
| --- | --- | --- |
| `Claude Code social media skill` | [README first screen](../README.md) | Follow [quickstart](QUICKSTART.md) with the safe offline CLI |
| `social media scheduling for developers` | [Calendar planner](CALENDAR-PLANNER.md) | Preview account/timezone proposals; never assume a live queue |
| `social-media automation GitHub` | [Platform matrix](PLATFORM-MATRIX.md) | See current adapters and unsupported/manual handoffs |
| `developer audience growth` | [Open-source growth playbook](OPEN-SOURCE-DEVELOPER-GROWTH-PLAYBOOK.md) | Choose a relevant channel and an original tutorial/demo |
| `safe autonomous marketing agent` | [Security review](SECURITY-CHECKLIST.md) and [architecture](ARCHITECTURE.md) | Understand per-item approvals, credentials and kill switch |
| `contribute to open-source social tools` | [CONTRIBUTING.md](../CONTRIBUTING.md) | Run offline tests and choose a bounded issue |

Keep a **single canonical descriptive brand** across the README, About metadata, PyPI package description when published, official landing page, release titles and social profiles. Do not create duplicate thin doorway pages for each keyword. Content quality, user intent, accurate titles, helpful documentation and legitimate references matter more than keyword density.

## Public launch: only after security and rights review

Read the mandatory public-release checklist. Review not only tracked current files but **every branch, tag, commit history and artifact that would become visible**, code ownership and third-party reuse rights. The existing CI secret scanner checks tracked files in a checkout, not a complete historical security/license audit. Rotating exposed credentials is mandatory before any history-remediation decision.

The owner must separately authorize any transition from **private** to **public**. Neither this document, the metadata helper nor CI changes the repo's visibility.

## Post-publication visibility, attribution and experiments

1. Add topics and the concise description, then choose a **real, verified** landing URL or leave homepage blank.
2. Release an accurately scoped tag only after `make release-gate`, security checks and a real public preview; link release notes to the quickstart, feature matrix and limitations.
3. Publish one original technical case study on DEV Community or another audience-fit channel, link to an informative public README/tutorial, and disclose affiliations. Do not mass repost, evade Reddit moderation or automate Hacker News.
4. Track qualified source visits, documentation completions, issues and real contributions. GitHub stars and social impressions are supporting signals, not proof of adoption.
5. After 2–4 weeks, reassess measured discoverability rather than renaming based on intuition. Avoid citing Google/Bing/AI answer inclusion unless independently observed.

**Measurement baseline:** While the repository remains private, the primary public GitHub discovery KPI is **not measurable** from this repo; record visibility state as `private` rather than `0 organic impressions`. Once public, use GitHub Insights and native referrer evidence when available (with coverage notes), not fabricated traffic totals.

## Brand/legal qualifications

An unoccupied GitHub repo name or a low exact-match search volume does **not** prove available domain names, social handles or trademark rights. Trademark/database clearance (EUIPO, WIPO, USPTO as relevant) should precede commercial brand investment. No alternative name or domain purchase is authorized by this document.

## Maintenance

- Review after a significant capability change so the description never promises features that remain manual/mock-tested.
- Update [`.github/repository-about.json`](../.github/repository-about.json) and its contract tests together.
- Keep all changes in a reviewed PR. To change public visibility, require a separate owner decision after PUBLIC-RELEASE-CHECKLIST.
