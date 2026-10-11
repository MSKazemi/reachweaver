# Source ingestion and opportunity ranking (`sources-scan`)

`reachweaver sources-scan` reads an **opt-in list** of sources: GitHub releases, tags, merged pull requests and labelled issues, RSS 2.0 or Atom feeds, website changelog pages, Keep a Changelog `CHANGELOG.md` files, and Crossref DOI records. It turns them into ranked `SourceSignal` records, so you can see which real work might be worth sharing. The output is **research only**. It never creates a draft, approves anything, schedules anything or publishes anything. A signal is a prompt for a human to check the evidence, not consent to post. To start a campaign from a signal, use [`signal-brief`](#from-signal-to-brief-signal-brief). (Issue #9: first offline slice, the issues/changelog/pagination/robots.txt/Crossref slice, and the signal-to-brief slice.)

## Quick start

```bash
# Offline (default): reads only the local fixture files named in the config.
python -m reachweaver.cli sources-scan --config examples/sources.reachweaver.json --top 5
python -m reachweaver.cli sources-scan --config examples/sources.reachweaver.json --json

# Novelty memory: record the candidates shown into a local state file, so a later scan marks them previously_seen.
python -m reachweaver.cli sources-scan --config my-sources.json --state state/sources-seen.json --mark-seen

# Explicit network access: GET only. Public GitHub repos (paginated), https feeds and changelogs
# (robots.txt checked first), and the Crossref API for DOI sources.
# An optional token raises the GitHub rate limit. It is read from the environment and never printed.
# export REACHWEAVER_GITHUB_TOKEN=...   # set this privately; never put it in the config
python -m reachweaver.cli sources-scan --config my-sources.json --online --top 10
```

Exit codes: `0` means a clean scan. `1` means some sources failed: the output still includes the other sources, and `errors` lists sanitized codes. `2` means the config, state or arguments are invalid.

## Config: `sources.reachweaver.json`

```json
{
  "schema_version": 1,
  "keywords": ["speech", "kubernetes", "hpc"],
  "half_life_days": 14,
  "include_prereleases": false,
  "weights": {"freshness": 0.35, "novelty": 0.25, "audience_relevance": 0.25, "confidence": 0.15},
  "overrides": {"include_urls": [], "exclude_urls": []},
  "sources": [
    {"id": "yazses-releases", "kind": "github_releases", "repo": "MSKazemi/YazSes", "sensitivity": "public"},
    {"id": "kubeintellect-pulls", "kind": "github_pulls", "repo": "MSKazemi/KubeIntellect", "sensitivity": "public", "max_pages": 2},
    {"id": "kubeintellect-notes", "kind": "github_issues", "repo": "MSKazemi/KubeIntellect", "sensitivity": "public",
     "labels": ["release-note", "announcement"]},
    {"id": "novafabric-tags", "kind": "github_tags", "repo": "MSKazemi/NovaFabric", "sensitivity": "public"},
    {"id": "blog", "kind": "feed", "url": "https://blog.example.org/feed.xml", "sensitivity": "public", "fixture": "feeds/blog.xml"},
    {"id": "changelog", "kind": "changelog", "format": "markdown", "sensitivity": "public",
     "url": "https://raw.githubusercontent.com/example-org/example-tool/main/CHANGELOG.md",
     "evidence_url": "https://github.com/example-org/example-tool/blob/main/CHANGELOG.md", "project": "example-tool"},
    {"id": "site-news", "kind": "changelog", "format": "html", "sensitivity": "public", "url": "https://www.example.org/changelog"},
    {"id": "paper", "kind": "doi", "doi": "10.5555/example", "sensitivity": "public", "fixture": "crossref/paper.json"}
  ]
}
```

Optional top-level `crossref_mailto` (for example `"crossref_mailto": "you@example.org"`) adds Crossref's polite-pool `mailto` query parameter to `--online` DOI requests. There is no default address: if you do not set it, no email is sent to Crossref.

- **Opt-in only.** Only the sources you list are read. Each source must declare `sensitivity` (`public` or `private`) explicitly. ReachWeaver never infers sensitivity from what a GitHub token can access. A `private` source is never read, fetched or emitted unless it is also a [private repository source](#private-repositories-opt-in-owner-approved-2026-10-09) with all three opt-ins. `"enabled": false` turns a source off and keeps it in the list.
- `kind` is one of:
  - `github_releases`, `github_tags`, `github_pulls` or `github_issues`. Each needs `repo: owner/name` and accepts `max_pages` (integer 1..10, default 3; 30 items per page). `github_issues` also needs `labels`: 1..20 label names you choose, such as `release-note` or `announcement`.
  - `feed`, which needs a public `https` `url`.
  - `changelog`, which needs a `format`. Use `markdown` for a Keep a Changelog style `CHANGELOG.md`. Its `url`, if set, must be `https://raw.githubusercontent.com/...`, or you can point `fixture` at a local file. Use `html` for a website changelog or news page. It needs a public `https` `url`, which is also the base for entry links. `evidence_url` (a human-facing page such as the GitHub `blob` view) replaces `url` as the link base. Every changelog source needs `url` or `evidence_url`, so that each entry links public evidence. `project` (optional) prefixes markdown entry titles; it defaults to the source `id`.
  - `doi`, which needs a `doi` in the form `10.NNNN/suffix` (no whitespace or control characters). Offline, a local Crossref JSON `fixture` is read. With `--online`, the Crossref API is queried.
- `fixture` is a local file path, resolved relative to the config file. Offline scans use it. With `--online`, every source that has a `url`, `repo` or `doi` is fetched instead. A `changelog` source without a `url` keeps reading its local file. A source with no fixture is skipped offline, with the reason `offline_no_fixture_use_online`. The example config lists the YazSes, KubeIntellect and NovaFabric repositories **only as example entries**. Offline, they are skipped, and tests never fetch them.
- `overrides.include_urls` bypasses the noise filter for one exact evidence URL. `overrides.exclude_urls` drops one. Even an included signal is only a candidate for human review.
- Keys that look like secrets (`token`, `password`, `secret` and similar) are rejected. Credentials never belong in the config.

## What each reader produces

| Kind | Source | Evidence URL | Notes |
|---|---|---|---|
| `github_release` | `/repos/{repo}/releases` JSON | `https://github.com/{repo}/releases/tag/{tag}`, rebuilt from structured fields (an `html_url` in the data is ignored) | Drafts are never emitted. Prereleases are skipped unless `include_prereleases: true`. |
| `github_tag` | `/repos/{repo}/tags` JSON | Same tag URL | The API gives no date, so freshness is 0 and confidence is low. If the same URL also appears as a release, the release wins. |
| `github_pull` | `/repos/{repo}/pulls?state=closed` JSON | `https://github.com/{repo}/pull/{n}` | Only merged PRs. Skips bot authors (`[bot]`, dependabot, renovate), noise labels (`dependencies`, `chore`, `ci`, `docs`…) and trivial titles (`chore:`, `ci:`, `docs:`, `bump X from`…). |
| `github_issue` | `/repos/{repo}/issues?state=closed` JSON | `https://github.com/{repo}/issues/{n}`, rebuilt from structured fields | Only `state: closed` with `state_reason: completed` (`not_closed_as_completed` otherwise, including legacy issues with a null reason). The issue needs at least one configured label, matched case-insensitively (`label_not_selected` otherwise). Items with a `pull_request` key are skipped as `pull_request_not_issue`. The date is `closed_at`. |
| `feed_item` | RSS 2.0 `<item>` / Atom `<entry>` | The item's `https` link | Items without a public https link are skipped. RSS 1.0/RDF is not supported. |
| `changelog_entry` (markdown) | Each `## [version] - YYYY-MM-DD` heading in a Keep a Changelog file | The version's link reference (`[1.2.0]: https://…compare/…`) or inline heading link, else `{evidence_url or url}#{github-style-anchor}` | Plain markdown line rules only. Headings inside fenced code are ignored. `[Unreleased]` and `[YANKED]` sections are skipped. `### Added`-style subsections and bullets become the summary. A heading without an ISO date is flagged `changelog_entry_date_unparseable`. |
| `changelog_entry` (html) | `<article>` elements, or `<h2>` sections when the page has no articles | A same-host `<a href>` inside the heading, else `{url}#{id}` or `{url}#{heading-slug}` | Parsed with `html.parser` only. Text inside `script`, `style`, `template`, `noscript`, `svg`, `iframe` and `object` is never read. Off-host heading links are ignored, never followed. The date comes from `<time datetime>`, else an ISO `YYYY-MM-DD` in the heading or in the start of the body. |
| `paper` | Crossref work JSON (`message` object), local or from `https://api.crossref.org/works/{doi}` | `https://doi.org/{DOI}` | The DOI in the record must match the configured DOI. Date parts that are missing become an uncertainty. |

A changelog entry whose `url` is a `#anchor` gets the uncertainty `entry_url_is_page_anchor`. The anchor is only an identity. `evidence_urls` contains just fragment-free URLs, which `campaign-check` accepts as evidence.

Each `SourceSignal` contains: `id` (the sha256 of `kind` + `url`, stable across runs), `kind`, `source_id`, `title`, `url`, `published_at` (RFC 3339 UTC or `null`), `evidence_urls`, `summary`, `untrusted_text: true`, `verification: "unverified_source_text"`, `confidence`, `uncertainties`, `freshness`, `novelty`, `audience_relevance`, `matched_keywords`, `status`, `duplicate_of`, `previously_seen`, `rank_score` and `rank_breakdown`.

## Ranking (transparent, not a prediction)

- **freshness** = `0.5 ** (age_days / half_life_days)`. An unknown date scores 0. A future timestamp is clamped to now and flagged.
- **novelty** = 1.0 for a new signal, 0.2 for a `near_duplicate` (a title that nearly matches an earlier signal or a remembered title; titles that differ in any number, such as `v1.2.0` and `v1.2.1`, are kept distinct), and 0.0 for `previously_seen` (its ID is in the `--state` file). The same evidence URL from two sources is merged, and the higher-confidence record is kept.
- **audience_relevance** = the number of matched keywords divided by `min(len(keywords), 3)`, capped at 1. Keywords match as whole words or phrases in the title and summary. With no keywords configured, every signal gets a neutral 0.5.
- **confidence** starts from the kind (release 0.9, paper 0.9, PR 0.85, issue 0.8, changelog entry 0.75, feed 0.7, tag 0.6). It loses 0.2 for an unknown date, 0.2 for instruction-like text, and 0.1 for a missing title.
- **rank_score** = the weighted mean of these four values. `rank_breakdown` shows each value, its normalized weight and its contribution, so you can audit and re-weight the ranking.

These are editorial heuristics. They do not measure or predict reach, views or engagement.

## Security model

- **Untrusted text.** Every title, body and description is treated as data. HTML tags and entities are removed, control and format characters (including bidi overrides) are dropped, whitespace is collapsed, and text is truncated (titles to 200 characters, summaries to 500). Text that looks like instructions, such as "ignore previous instructions", "approve and publish now" or `REACHWEAVER_ALLOW_LIVE`, is **never followed**. It only adds `instruction_like_text_treated_as_data` and lowers confidence. This detection is a heuristic flag, not a security boundary. The real boundary is that scanning cannot draft, approve or publish.
- **Poisoned feeds.** Before any parsing, a feed is rejected if it contains `<!DOCTYPE` or `<!ENTITY` (this blocks XXE and billion-laughs attacks). Feeds in UTF-16/32, feeds with NUL bytes, and feeds that declare a non-ASCII-compatible encoding are also rejected, so these declarations cannot be hidden from the check. Feeds are capped at 2 MB, and JSON at 1 MB.
- **Network.** Network access happens only with `--online`. Requests are GET only and use the same no-redirect opener as `publishers.py`. They have a 25-second timeout and bounded reads, and errors are reported only as sanitized codes (`http_403`, `network_or_timeout`, `response_too_large`). No URL, header or response body appears in an error. Every request sends the User-Agent `ReachWeaver/0.3 (+https://github.com/MSKazemi/reachweaver)`.
- **GitHub pagination.** GitHub list endpoints fetch 30 items per page. The scan follows the RFC 8288 `Link: <…>; rel="next"` header for at most `max_pages` pages per source (default 3, max 10). Before any request, and so before the token can be sent, a next link must be `https://api.github.com` exactly: no userinfo, no port other than 443, no fragment, and a `/repos/…` or `/repositories/…` path. A link that fails this check, or that repeats a page already fetched, stops pagination with the reason `pagination_next_link_refused`. The pages already fetched are still used. Reaching the cap while more pages exist adds `pagination_max_pages_reached`. Only the `Link`, `Content-Type` and `X-OAuth-Scopes` headers are read, each bounded to 8192 characters. Unauthenticated GitHub API use has a low hourly rate limit, so stay within GitHub's API terms.
- **robots.txt.** Before any `--online` non-API web fetch (feeds and changelogs, including `raw.githubusercontent.com`), the scan fetches `https://{host}/robots.txt` once per host and caches it for the run. It is parsed with `urllib.robotparser` for the agent `ReachWeaver`. A disallowed URL is skipped with the reason `robots_disallowed` and is never fetched. A 401 or 403 means everything is disallowed. Any other 4xx means there is no robots.txt, so fetching is allowed (RFC 9309 §2.3.1.3). A 5xx, a redirect (never followed), a timeout or a file over 512 KB skips the source as `robots_unavailable`, because RFC 9309 §2.3.1.4 says to assume complete disallow. API hosts (`api.github.com`, `api.crossref.org`) are queried under their API terms, not crawled.
- **Crossref.** With `--online`, a `doi` source sends a GET to `https://api.crossref.org/works/{doi}`. The DOI is validated against `10.NNNN/suffix` and fully URL-quoted, including `/`, `?`, `#` and `..`. No credentials are sent. `mailto` is added only if you set `crossref_mailto`, and the address never appears in output.
- **Credentials.** `REACHWEAVER_GITHUB_TOKEN` is optional. It is sent only as a header to `https://api.github.com`, never to feed hosts. It is never printed, logged or written to state. A malformed value is rejected without being echoed. Output reports only `github_authenticated: true/false`.
- **Public unless all three private opt-ins.** Before fetching a repository's releases, tags or PRs online, the scan reads the repository metadata. It refuses the repository (`repository_not_confirmed_public`) unless the metadata says `private: false` and the visibility is public. A token that *can* read a private repository does not make the repository eligible. The only exception is a source with all three [private opt-ins](#private-repositories-opt-in-owner-approved-2026-10-09).
- **The `--sources` caveat, stated exactly (#9).** The private-link refusal on `draft` (reply drafts too), `media-attach`, `youtube-attach`, `check` and `approve` is **opt-in per invocation**, deliberately. These commands read only the outbox database, which stores no source configuration, so they cannot learn which repositories are private. Behaviour is therefore: no `--sources` means no check and no refusal (a draft linking a private repository is created); `--sources CONFIG` naming a config with no `visibility: private` repository refuses nothing; `--sources CONFIG` naming a config with one refuses any link into it; and a missing or malformed `--sources` file fails closed (the command exits non-zero and changes nothing) rather than being skipped. `campaign-check`, `campaign-preview --utm`, `signal-brief` and `utm-link` apply the rule from the evidence record or from `--sources` in the same way. `tests/test_private_sources_integration.py` pins all four cases. A mandatory check is not offered because it would need the config path stored in the database, which adds state to protected approval paths; pass `--sources` whenever a private source is configured.
- **Hostile feeds and changelogs.** `tests/fixtures/sources/feed-malformed-items.xml` (items with no link, a `javascript:` link, a link with credentials, a bad date and a missing title), `feed-injection.xml` (instruction-like text in titles and CDATA bodies, entity-encoded `<system>` tags, a `<script>` body, bidirectional and zero-width controls) and `changelog-injection.md` are scanned by `tests/test_sources.py` (`HostileFixtureTests`). Expected result: unusable items are skipped with `item_without_public_https_link`, a missing title or summary lowers confidence (`missing_title`, `no_summary_text`), instruction-like text stays inert data with `instruction_like_text_treated_as_data` and lower confidence, control characters and markup are removed, the scan creates no outbox, and an unparseable document fails only its own source with a fixed code (`malformed_feed_xml`, `unsupported_feed_encoding`, `unsupported_feed_format`).
- **State file.** `--mark-seen` writes the IDs, titles and URLs of the candidates shown to the `--state` JSON file (a private signal is recorded by ID only). The write is atomic, the file has owner-only `0600` permissions, and it keeps at most 5000 entries. Without `--mark-seen`, the scan writes no files.

## Private repositories (opt-in, owner-approved 2026-10-09)

> Owner-approved 2026-10-09 with every recommended decision. This reads private data. See SECURITY-REVIEW-PROPOSAL-9-private-sources.md.

A private GitHub repository is read **only when all three opt-ins hold**:

1. The source entry sets `"visibility": "private"`. This is allowed only for the `github_*` kinds.
2. The same entry sets `"sensitivity": "private"`. `visibility: private` with `sensitivity: public` is a config error.
3. You pass `sources-scan --allow-private`. This also applies to offline fixtures.

```json
{"id": "internal-releases", "kind": "github_releases", "repo": "my-org/internal-tool",
 "visibility": "private", "sensitivity": "private", "max_pages": 1}
```

```bash
reachweaver sources-scan --config my-sources.json --online --allow-private --json > private/scan.json
```

| Situation | Result |
|---|---|
| No `visibility: private`, and the API says `private: true` | `repository_not_confirmed_public` error. Only the metadata request is made, and nothing is emitted or stored. |
| `visibility: private` without `sensitivity: private` | Config error (exit 2). No request is made. |
| `sensitivity: private` without `visibility: private` | Skipped as `private_source_excluded`. Nothing is read. |
| Both fields set, no `--allow-private` | Skipped as `private_source_needs_allow_private`. Nothing is read. |
| All three, no `REACHWEAVER_GITHUB_TOKEN` | `private_source_requires_token` error. No request is made. |
| All three, but the API says the repository is public | `repository_not_confirmed_private` error. No list endpoint is read. |

**What a private signal carries, and what keeps it inside.**

- Every signal from such a source has `visibility: "private"`, `private: true` and `publishable: false`. The result adds `private_sources_allowed`, `counts.private` and a `private_note`. Diagnostics (`skipped`) for a private source never include item URLs. Text output marks the signal `PRIVATE` and leaves out its summary, which appears only in `--json`. `--mark-seen` stores its ID only.
- `signal-brief` on a private signal sets `source_visibility: "private"` and `publishable: false` on every evidence record. It scaffolds **no** `cta_url`: the variant text has no link, and the `cta_note` asks for a public destination. The sidecar starts with a `PRIVATE SOURCE` warning. A scan file whose `visibility` is anything other than exactly `public` is treated as private.
- `campaign-check`, `campaign-preview` and `campaign-stage` (`validate_campaign`) refuse private evidence until a person sets `"publishable": true` on that evidence record at review time, after confirming that the content may be made public. `publishable` must be a boolean.
- A URL inside a private source repository (`github.com/owner/repo/...`, `api.github.com/repos/owner/repo/...`, `raw.githubusercontent.com/owner/repo/...`) is **never a public link**, even when the evidence is publishable. `campaign-check` rejects it as a campaign or variant `cta_url` or a link in the post text. `campaign-preview --utm` refuses to tag it. `utm-link URL --sources CONFIG` refuses any destination in a `visibility: private` repository of that config. Staging stores no private evidence URL as the post's `source_url`. The same rule covers the surfaces added later (integration, 2026-10-09): with `--sources CONFIG`, `draft` (a `--reply-to` reply draft too) refuses such a link in the text or `--source-url`, `media-attach` in the alt text or the hosted image URL, `youtube-attach` in the title or a tag, and `check`/`approve` anywhere in what the post publishes (text, alt texts, video title and tags, hosted image URLs) or its source URL. Without `--sources` the private repositories are unknown to these commands, so pass it whenever a private source is configured.
- `data-export` and `campaign-export` replace the `excerpt` and `url` of private evidence records with `[REDACTED:private_source]`. `data-export` also redacts an older post `source_url` that points into such a repository, and reports `private_source_rows_redacted`.
- The private text is never logged: `sources-scan` and `signal-brief` write no log lines, and every error is a fixed code.

**Token: use a fine-grained PAT, read-only, selected repositories only.** GitHub recommends fine-grained tokens over classic ones, and notes that a classic token "can access every repository that you can access" ([Managing your personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), accessed 2026-10-09). Create one with **Repository access: Only select repositories**, set an expiration, and grant only these **read-only** permissions ([permissions required for fine-grained PATs](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens), accessed 2026-10-09):

| Reader | Endpoint | Permission (read) |
|---|---|---|
| all | `GET /repos/{owner}/{repo}` | Metadata |
| `github_releases` | `GET /repos/{owner}/{repo}/releases` | Contents |
| `github_tags` | `GET /repos/{owner}/{repo}/tags` | Metadata |
| `github_pulls` | `GET /repos/{owner}/{repo}/pulls` | Pull requests |
| `github_issues` | `GET /repos/{owner}/{repo}/issues` | Issues |

**Scope warnings.** Classic tokens: GitHub's `X-OAuth-Scopes` response header "lists the scopes your token has authorized", and the classic `repo` scope "Grants full access to public and private repositories including read and write access to code" ([Scopes for OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps), accessed 2026-10-09). That page lists no read-only private-repository scope. When the header is present, `token_scope` lists every non-`read:*` scope as `write_capable` and warns `token_has_write_scopes`. Fine-grained tokens: GitHub's first-party docs do not say that a fine-grained token's response includes `X-OAuth-Scopes`, and third-party reports say it does not, so ReachWeaver cannot see a fine-grained token's permissions. A missing header gives the warning `token_scope_unknown`. It never means read-only. The repository metadata's `permissions` object is documented only as booleans, with no statement on whether it reflects the token or your role ([Get a repository](https://docs.github.com/en/rest/repos/repos#get-a-repository), accessed 2026-10-09). When it reports push, maintain or admin, the scan adds the advisory note `repository_permissions_report_write_access`. The token value is never printed. Only scope names appear.

## From signal to brief (`signal-brief`)

`signal-brief` turns one signal from a saved scan into a **new campaign file** that follows the [campaign contract](../.claude/skills/reachweaver/references/campaign-contract.md), plus a Markdown sidecar for the person writing the posts. It is a deterministic scaffold. **No copy is generated**: no language model, paid API, network or database is used. A person writes the posts, or the `/reachweaver` skill does it in an interactive session.

```bash
reachweaver sources-scan --config my-sources.json --json > private/scan.json
reachweaver signal-brief --scan private/scan.json --signal-id 149c5579 --brand private/brand.json \
  --accounts config/accounts.json --out private/v1-2-0.campaign.json        # also writes private/v1-2-0.campaign.brief.md
```

| Option | Effect |
|---|---|
| `--scan F` | The saved `sources-scan --json` output. It is treated as untrusted input (see below). |
| `--signal-id ID` | The full 64-character signal ID, or a unique prefix of at least 8 hex characters. An unknown or ambiguous ID is an error. |
| `--brand B` | Brand JSON. Its `brand_id` becomes the campaign's, and only its `approved_accounts` get variants. |
| `--out F.json` | The **new** campaign file, written owner-only (`0600`). The sidecar `F.brief.md` is written next to it. If either file already exists, nothing is written. |
| `--accounts A` | Optional public accounts config (the global `--accounts` works too). Without it, `variants` is an empty list. |
| `--objective O` | Campaign objective (default `awareness`). |
| `--allow-duplicate` | Explicit human override to brief a `near_duplicate` or `previously_seen` signal. Without it they are refused. |
| `--json` | Machine-readable result: files, variants, skipped accounts and the validation report. |

What the campaign file contains:

- **`campaign_id`** is the title as a slug plus the brief date (`YYYYMMDD`). For a signal with instruction-like text, it is `signal-<first 8 ID characters>-<date>` instead, so flagged text never names the campaign.
- **`evidence`**: one record per signal evidence URL. Each URL must pass the same public-HTTPS check `campaign-check` uses, so `#anchor` URLs and private hosts are dropped. The signal's own URL gets the mapped kind: `github_release` becomes `release`, `paper` becomes `paper`, `changelog_entry` becomes `documentation`, `github_pull`, `github_issue` and `github_tag` become `repository`, and `feed_item` becomes `announcement`. Every other URL gets `repository` (github.com) or `other`. Only a GitHub release maps to `release`, so release language in a post about any other signal stays blocked by `claims-check` until a person checks the source. The first record's `excerpt` holds the signal's cleaned summary, with `excerpt_status: "untrusted_scan_summary_not_verbatim"`. Every record has `checked_by: "unverified"`. **`verification` and `checked_at` are left out on purpose**, because only a person who opened the source can declare them.
- **`variants`**: one placeholder variant per account that is in both the accounts config and the brand's `approved_accounts` and whose platform has a live or manual path in the [capability registry](PLATFORMS.md). Live means the text format is `api_direct` with a ReachWeaver live adapter. Manual means the text format is `manual_export`, or a [manual-export](MANUAL-EXPORT.md) package exists for the platform. Other accounts are listed as skipped, with the reason. Each variant cites every evidence ID, declares `cta_url` as the signal URL, and carries a `cta_note` with the exact `utm-link` command to run. Its `text` is a `{{write: ...}}` placeholder followed by the link, so `campaign-preview` can show each account's remaining length budget.
- **`audience`**, each evidence `claim`, and each variant `angle` and `cta` are `{{write: ...}}` placeholders too. `brief` records the signal ID, kind, scores, uncertainties and whether the text was flagged.

The sidecar lists the signal title and summary, the evidence links, the freshness, novelty, relevance, confidence and rank scores, the uncertainties, the platform limits for each account (with how much of the budget the link uses), the steps to finish, and the validation result. If the signal was flagged as instruction-like, the sidecar begins with a warning.

**Blocked until a person edits it.** The command runs `validate_campaign` on the scaffold and reports it as `BLOCKED`. That is expected. The post-text placeholder trips `validate_post`'s placeholder lint, and `validate_campaign` also rejects a `{{...}}` placeholder left in `audience`, an evidence `claim`, or a variant `angle`, `cta`, `cta_phrase` or `rationale`. Missing `verification` and `checked_at` are errors too. If a scaffold ever validated, the command would refuse to write it. Staging needs every placeholder written, every source verified, and `claims-check`, `campaign-preview` and `campaign-check` passing. Approval is still a separate human step.

**Untrusted input.** The scan file could have been edited by anyone, so `signal-brief` checks it again. It re-cleans the title and summary with the same `clean_text` rules, detects instruction-like text again instead of trusting the scan's flag, drops uncertainty values that are not plain codes, ignores scores outside 0..1, and rejects any evidence URL with characters that could break the Markdown. In the sidecar, source text appears only inside one fenced block labelled as data. The fence is always longer than any backtick run in the text. Source text is never copied into an instruction, a heading, the campaign ID or any variant field, and it cannot change what the command does.

## Limitations

- Private repositories are readable only through the GitHub API readers with all three opt-ins (owner-approved 2026-10-09). There is no private feed, changelog or DOI reader. Feed and page URLs must be ones you are permitted to read. robots.txt is honoured, but it is not a licence.
- The HTML reader understands only `<article>` and `<h2>` patterns. It does not run JavaScript, so a page that renders its changelog client-side yields `no_changelog_entries_found`. Pages are decoded as UTF-8, and invalid bytes are replaced.
- Markdown anchors follow GitHub's heading-slug rules on a best-effort basis. Prefer link references (`[1.2.0]: https://…`) when you want exact per-version evidence.
- GitHub issues are filtered on the client side, so a sparse label can need more pages. Raise `max_pages` (at most 10) instead of scanning without a cap.
- `signal-brief` scaffolds only one signal per campaign. It never merges signals and never writes post copy.
