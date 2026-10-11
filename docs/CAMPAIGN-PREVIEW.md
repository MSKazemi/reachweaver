# Campaign preview (offline, per channel)

`reachweaver campaign-preview` shows how each variant of a campaign looks on its channel before anyone stages or approves it. `reachweaver/preview.py` is deterministic: the same campaign, brand, accounts and registry always give the same preview. It makes no network calls, needs no credentials or database (an optional `--history-db` is only read), writes nothing, and never modifies the campaign file. **No copy is generated.** Every string in the preview comes from the campaign file, and no language model or paid API is involved.

```bash
reachweaver campaign-preview --file examples/campaign-preview.reachweaver.json --brand examples/brand-preview.reachweaver.json
reachweaver campaign-preview --file F --brand B --accounts config/accounts.json --utm --markdown > review.md
reachweaver campaign-preview --file F --brand B --json
```

| Option | Effect |
|---|---|
| `--file F` | Campaign JSON. It is read only. |
| `--brand B` | Brand JSON, including the optional per-account rules described below. |
| `--accounts A` | Optional public accounts config. It is loaded only when you pass it (the global `--accounts` works too). It enables account/platform checks and a Mastodon account's lower `max_characters`. |
| `--utm` | Shows every link with `utm_source=<platform>`, `utm_medium=social`, `utm_campaign=<campaign_id>` and `utm_content=<variant id>` next to the original, plus the length after tagging. |
| `--optout F` | Opt-out list (default `$REACHWEAVER_OPTOUT`, else `config/optout.json` if present). An @-mention of a listed handle or domain in the text, any thread part or any carousel slide is an `optout_mention` error. See [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list). |
| `--history-db DB` | Optional existing local database, opened read-only. Adds `history_near_duplicate` and `history_frequency` warnings against the same account's posts, and a `history` summary. Tune with `--history-days`, `--history-window-days` and `--history-max-posts`. See [posting history](CLAIM-GROUNDING.md#posting-history). |
| `--json` / `--markdown` | Machine-readable report, or an editable Markdown review sheet. The default is compact text. |

The exit code is `2` when any variant has `low` confidence or the campaign has campaign-level errors, and `0` otherwise. A `0` exit means the variant is ready for review. It does not mean the variant is authorized to publish.

## What each variant shows

- **Exact text.** `text` is the full body as typed. `pieces[]` splits it into a text post, the parts of a thread, or a carousel caption and slides.
- **Platform-accurate length and remaining budget** (`pieces[].length`):
  - Bluesky counts approximate grapheme clusters (never under-counted) against 300 and UTF-8 bytes against 3000. Both budgets are shown.
  - Mastodon counts code points with each URL counted as at least 23. The limit is the account's `max_characters` when `--accounts` sets a lower value, and the conservative cap of 450 otherwise. `limit_source` says which one applied.
  - Every other platform counts Unicode code points against ReachWeaver's conservative cap (`policy.CONSERVATIVE_TEXT_LIMITS`). That cap is not a provider spec.
- **Facets, hashtags, mentions and links** (`pieces[].features`). Bluesky adds link and tag facets at UTF-8 byte offsets. Mentions stay plain text because ReachWeaver does no handle-to-DID resolution.
- **Capability status** from `platform_registry.json`: `api_direct`, `gated` (`api_gated`), `manual` (`manual_export`), `unavailable` or `unknown`, plus whether a ReachWeaver live text adapter exists. This is dated research. It is not consent and not account authorization.
- **Evidence links.** `evidence[]` lists the cited evidence records. `grounded[]` lists each figure or phrase that claims.py matched, with the `evidence_urls` it is anchored on.
- **Grounding findings** from claims.py (see [claim grounding](CLAIM-GROUNDING.md)), plus the preview's own thread and carousel checks. For a thread or carousel, `piece` names the part or slide a finding falls in.
- **Brand rules** that apply to the variant's account (`brand_rules`): the merged banned phrases, required disclosures, hashtag cap and hashtag count.
- **Stageability.** `stageable` is true only for a `text` variant with no errors. Thread and carousel variants are creative briefs and are never staged. Staging still needs `campaign-stage` and a private accounts file.

## Confidence

`confidence.level` is computed from rules. No model is involved:

| Level | Rule |
|---|---|
| `high` | No blocking errors and no human-review items, so every figure is grounded in cited evidence. |
| `medium` | Human-review items are present (testimonials, endorsements, customer counts, superlatives, feature claims missing from cited evidence, a thread whose first part does not stand alone). |
| `low` | Any blocking error: an unsupported figure or release claim, a banned phrase, a missing disclosure, too many hashtags, over the length limit, a structural error, or a campaign-level error. |

Warnings (`near_duplicate`, `history_near_duplicate`, `history_frequency`, `missing_cta`, `carousel_unavailable`, a long first line) are listed in the rationale but do not change the level. `confidence.rationale` explains the level line by line. The rationale is editable in two ways:

- An optional variant field `rationale` (a nonempty string, at most 1000 characters) records your own editorial reasoning. It is shown verbatim as `editor_rationale` and never changes the computed level.
- `--markdown` writes the rationale as bullets under **Rationale (editable)** for you to revise in the review sheet. The sheet is not read back.

Confidence inherits every limit of lexical grounding. `high` means the checked tokens have anchors in the evidence you typed. It does not mean the post is true.

## Threads and carousel outlines (optional, backwards compatible)

```json
{"id": "guardrails-thread", "platform": "bluesky", "format": "thread",
 "parts": ["Hook that stands alone, with the link: https://github.com/MSKazemi/reachweaver", "Second post", "Third post"],
 "account_id": "my-bluesky", "angle": "...", "cta": "...", "evidence_ids": ["source-repo"]}
{"id": "gates-carousel", "platform": "linkedin", "format": "carousel_outline",
 "text": "Optional caption", "slides": ["Slide one", "Slide two"],
 "account_id": "my-linkedin", "angle": "...", "cta": "...", "evidence_ids": ["source-repo"]}
```

- **`thread`** needs 2 to 25 string `parts` and no `text`. Each part is measured and validated against the platform limit on its own, and labelled `1/N` for review. ReachWeaver inserts no numbering. If you type numbering such as `2/4` into a part, it is grounded like any other figure. The **first part must stand alone**: its first line, after a leading 🧵, `1/` or `thread:` marker, needs at least 4 words (the hook), and the part must contain a link or the variant's `cta_phrase`. If it does not, the preview reports a `thread_first_part_not_standalone` review item. Grounding runs over all parts.
- **`carousel_outline`** needs 1 to 20 slides, each a nonempty string of at most 2000 characters, plus an optional `text` caption that is measured against the platform limit. The preview compares the outline with the registry's `carousel` status. It warns `carousel_unavailable` when the status is `unavailable` (for example, LinkedIn organic carousels) and `carousel_unverified` when it is `unknown`. The registry holds no per-platform slide limits, so none are invented.
- Both formats still produce the unchanged error `runtime only stages text posts; other formats are creative briefs` in `campaign-check`, `campaign-stage` and `campaign-export`. Only text variants are staged, as before.

## Per-account brand rules

An optional brand `accounts` object adds rules for individual approved accounts:

```json
{"banned_phrases": ["game-changer"],
 "accounts": {"my-linkedin": {"banned_phrases": ["thought leader"], "max_hashtags": 3, "required_disclosure": "Disclosure: I maintain ReachWeaver"}}}
```

The merge is **additive and never weakens a global rule**. Banned phrases are the union of both lists. Every distinct required disclosure must appear. The hashtag cap is the lowest one set, so a global brand `max_hashtags` (an integer from 0 to 30) also works. Findings that come from an account override carry `scope: "account:<id>"`. When `accounts` is absent, claims checks behave exactly as before. Validation rejects an override whose key is not in `approved_accounts`, any unknown field, and any invalid value. Hashtags are counted outside links with Bluesky tag syntax, and an overlong tag still counts.

## Known limits

- The hook and stand-alone checks are word counts and the presence of a link or phrase. They do not judge whether a hook is good.
- Capability statuses are only as current as the registry's `verified_date`. Run `reachweaver capabilities` to check staleness.
- UTM tagging only covers `https` links. Any other link is reported with the error and left unchanged.
- Lengths for platforms other than Bluesky and Mastodon use conservative code-point caps, not each provider's exact counting rules.
