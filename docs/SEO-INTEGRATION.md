# SEO repository integration contract (v1)

Issue #25 slice. ReachWeaver coordinates social campaigns with the owner's SEO work in https://github.com/MSKazemi/seo, but it does **not** depend on that repository at runtime. Instead, it reads **one local JSON file**: either an export or a file from a local checkout. It never clones or fetches the SEO repository, never imports its code and never copies its secrets. A file that contains credential-like keys (`token`, `secret`, `password`, `api_key`, `cookie`, `credential`, `authorization`) or token-like values (`ghp_`, `github_pat_`, `sk-`, ...) is rejected.

## Contract

```json
{
  "schema_version": 1,
  "source": {"repository": "https://github.com/MSKazemi/seo", "exported_at": "2026-10-08T15:00:00+02:00"},
  "briefs": [
    {
      "page_id": "reachweaver-docs-calendar",
      "canonical_url": "https://github.com/MSKazemi/reachweaver/blob/main/docs/CALENDAR-PLANNER.md",
      "title": "ReachWeaver calendar planner quickstart",
      "primary_keyword": "approval-first social scheduling",
      "audience": "Developers evaluating ReachWeaver releases",
      "campaign_type": "release",
      "priority": 1,
      "project": "reachweaver",
      "status": "active"
    }
  ],
  "campaigns": [
    {"campaign_id": "reachweaver-v0-3-release", "page_id": "reachweaver-docs-calendar",
     "start": "2026-10-15", "end": "2026-10-29", "status": "planned"}
  ]
}
```

| Field | Rule |
|---|---|
| `page_id` | Required, unique |
| `canonical_url` | Required. Must already be canonical: `https`, lowercase host, no fragment, no `utm_*` or click-tracker parameters, query sorted |
| `title`, `primary_keyword`, `audience` | Required non-empty strings. Keywords are compared case- and whitespace-insensitively |
| `campaign_type` | `release`, `paper`, `product` or `docs` |
| `priority` | Integer from 1 (highest) to 5 |
| `project` | Optional repository or project name |
| `status` | Optional: `active` (default), `draft` or `retired` |
| `campaigns[]` | Optional. `campaign_id` must be unique, `page_id` must reference a brief, `start`/`end` use YYYY-MM-DD (inclusive), and `status` is `planned` (default), `active` or `ended` |

`source` is free-form provenance and is not interpreted. Additive optional fields are allowed. Any breaking change bumps `schema_version`.

## Using it

```bash
# Release announcement -> docs landing page
reachweaver seo-destinations --briefs examples/seo-briefs.example.json --type release --project reachweaver \
  --platform linkedin --campaign reachweaver-v0-3-release
# Peer-reviewed paper -> publication page
reachweaver seo-destinations --briefs examples/seo-briefs.example.json --type paper
```

`campaign_destinations(briefs, campaign_type, project)` takes the validated document from `load_seo_briefs(path)` and returns:

- `destination`: the `active` brief with the matching type (and project, if given), lowest `priority` number first and then by `page_id`. If none matches, `status` is `no_destination`.
- `alternatives`: the other candidate briefs.
- `destination_conflicts` / `all_conflicts`: competing messages. These are `duplicate_canonical_url_across_briefs`, plus `same_canonical_url` and `same_primary_keyword` for two non-`ended` campaigns whose windows overlap (the `overlap` field gives the shared dates). Any conflict involving the destination sets `status` to `review_conflicts`, and the CLI then exits with code 2.
- `utm_link` (when both `--platform` and `--campaign` are given): the canonical tagged link. See [ATTRIBUTION.md](ATTRIBUTION.md).

## Testing campaign tracking

1. Validate the export with `seo-destinations` and resolve any conflicts.
2. Build each post link with `utm-link --content POST_ID --allow-origin <your origins>`, and keep the same canonical URL in the post as on the landing page.
3. After the posts are published, run `attribution-report` on an analytics export to confirm that rows come back as `attributed.posts`. `tests/test_attribution.py` covers this whole path with fixtures.

## Checking landing pages (`landing-check`)

```bash
# Offline: canonical URLs in the briefs, campaign CTAs and UTM tags. Reads two local files.
reachweaver landing-check --briefs examples/seo-briefs.example.json --campaign examples/campaign.reachweaver.json
# Online: also GET each canonical page once (explicit opt-in)
reachweaver landing-check --briefs FILE --campaign CAMPAIGN.json --online [--allow-origin https://www.example.net] [--json]
```

Every page, and the campaign, gets a status of `ok`, `warning` or `error`. The command exits with code 1 when any finding is an error, and with code 2 when an input file cannot be read. It never drafts, approves, schedules or publishes anything, and it never opens the database.

**Offline checks (default, no network):**

| Check | Finding codes |
|---|---|
| Each brief `canonical_url` is https, with no `#fragment`, no `utm_*` or click-tracker parameter (`fbclid`, `gclid`, ...), and already in `normalize_destination` form (lowercase host, no default port, sorted query) | `canonical_url_not_https`, `canonical_url_fragment`, `canonical_url_tracking_params`, `canonical_url_not_normalized`, `canonical_url_invalid` (errors) |
| The file passes the full brief contract above | `briefs_contract_invalid` (error) |
| Two briefs share a canonical URL | `duplicate_canonical_url` (warning) |
| A campaign CTA is the `cta_url` of the campaign or a variant, or any UTM-tagged link in a variant body. Each tagged CTA must pass `validate_utm_link` ([ATTRIBUTION.md](ATTRIBUTION.md)) | `utm_invalid`, `cta_invalid` (errors) |
| The CTA destination, with its UTM tags stripped, must be a brief's `canonical_url` | `cta_no_brief` (error) |
| A declared `cta_url` without UTM tags cannot be credited by `attribution-report` | `cta_untagged` (warning) |
| Nothing in the campaign links to a brief's page | `campaign_no_landing_link` (warning) |
| Credential-like keys in the campaign file | `campaign_secret_like_field` (error) |

Untagged links in a variant body that are not declared CTAs (for example, a reference link) are not CTA errors. They still map that variant to a brief's page for the claim check below.

**Online checks (`--online` only).** Each `active` brief, and each brief a campaign links to, is fetched once. Draft and retired briefs that no campaign links to are skipped, and so is any brief whose canonical URL failed the offline checks. A run fetches at most 50 pages.

| Reported | Finding codes |
|---|---|
| HTTP status, final URL and the full redirect chain (`status`, `from`, `to`) | `http_error` (error, any 4xx/5xx), `canonical_url_redirects` (warning: link the final URL instead) |
| `<link rel=canonical>` (resolved against the final URL) matches the brief | `canonical_mismatch`, `canonical_link_multiple`, `canonical_link_invalid` (errors), `canonical_link_missing` (warning) |
| `<title>` and meta description | `title_missing` (error), `meta_description_missing` (warning) |
| Social preview: `og:title`, `og:description`, `og:image`, and `og:image` uses https | `og_title_missing`, `og_description_missing`, `og_image_missing` (warnings), `og_image_not_https` (error) |
| `noindex` (or `none`) in a `robots`/`googlebot` meta tag or the `X-Robots-Tag` header | `noindex` (warning) |
| The `<html lang>` value | `html_lang_missing` (warning) |
| The campaign's grounded claim phrases (the `grounded[]` spans from `claims-check`, such as numbers, versions and release words) appear in the page title or visible text | `claim_phrase_not_on_page` (warning) |
| The response is HTML | `not_html` (error) |

The claim check is a **lexical mismatch check, not truth verification**. A missing phrase means the post and its landing page use different wording, so a reader who clicks may not find what the post promised. A found phrase does not show that the claim is true. Script, style, template, noscript and svg content is never counted as page text.

**Network safety rules:**

- Only GET requests, and only to the canonical URLs in your own briefs, their robots.txt, and redirect targets that pass the rules below. The User-Agent is `ReachWeaver/0.3 (+https://github.com/MSKazemi/reachweaver)`. No credentials or cookies are sent.
- robots.txt is checked first for every host, using the same checker as `sources-scan` (`sources.RobotsCache`, agent `ReachWeaver`). A disallowed URL (`robots_disallowed`) or an unreadable robots.txt (`robots_unavailable`, RFC 9309 says to assume disallow) is not fetched and is reported as an error, because the page could not be checked.
- urllib never follows redirects. ReachWeaver inspects each 3xx: at most 5 hops (`too_many_redirects`), https only (`redirect_not_https`), no loops (`redirect_loop`), and the target must share the brief's registrable domain (`redirect_off_domain`) unless its origin is listed with `--allow-origin`. Python's standard library has no Public Suffix List, so `landing.registrable_domain` uses the last two labels plus an embedded subset of multi-label suffixes (`co.uk`, `github.io`, `pages.dev`, ...). Allow anything else explicitly.
- Bodies are capped at 2 MB (`body_too_large`, checked against `Content-Length` and the bytes read). Each request times out after 25 seconds. HTML is parsed with `html.parser` only.
- Every value read from a page is untrusted data. It is cleaned, truncated to 300 characters and reported. It is never followed as an instruction.

## Post-text checks (`seo-content-check`, issue #45 W4, offline)

```bash
reachweaver seo-content-check posts.json            # text list
reachweaver seo-content-check posts.json --json     # full report
```

Input is one local JSON file, `{"items": [{"id", "platform", "text", "alt_text"?, "media_count"?, "link"?, "keywords"?}]}` (up to 500 items; unknown keys are refused). It checks only what is in that file and opens no database or network connection:

| Finding | Basis | Meaning |
| --- | --- | --- |
| `text_too_long` (error) | `repo_policy` | Over the conservative per-platform limit in `policy.CONSERVATIVE_TEXT_LIMITS`. |
| `instagram_caption_rule` (error) | `official` | Instagram's documented caption, hashtag and @-tag limits (see INSTAGRAM.md). |
| `alt_text_missing` (warn) | `hypothesis` | Media is attached but alt text is empty. |
| `hashtag_repeated`, `hashtag_heavy` (warn) | `hypothesis` | The same hashtag twice, or over half the words are hashtags. |
| `link_attribution` (warn) | `repo_policy` | The declared link fails the canonical UTM rules used by `utm-link`. |
| `keywords_absent` (info) | `hypothesis` | None of your declared topic phrases appear; add one only if it fits naturally. |
| `cross_platform_duplicate` (info) | `hypothesis` | Same wording (URLs and hashtags ignored, 90% word overlap) on different platforms. |

`hypothesis` findings are editorial advice. None of them claims that a hashtag, keyword or format changes reach or search ranking, because no source supports such a claim. Exit code `2` means at least one `error` finding; warnings and info exit `0`. Link-preview (Open Graph) checks need the page, so they stay with `landing-check`.

## Not covered yet

- `landing-check` checks the page a post links to. It cannot see how each platform renders the preview card (platforms cache and crop previews), and it does not check that a claim is true. Those stay manual review steps.
- Search and social referral outcomes can be compared only from the same analytics export. ReachWeaver makes no promise of ranking gains from social shares.
- `examples/seo-briefs.example.json` is illustrative. The `example.org` paper page is a placeholder.
