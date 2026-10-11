# Claim grounding (lexical, offline)

`reachweaver/claims.py` checks each campaign variant against the evidence the campaign itself declares. It runs inside `campaign-check`, `campaign-export` and `campaign-stage`, and on its own as `claims-check`. It makes no network calls, needs no account and publishes nothing.

> **This is lexical grounding, not truth verification.** The checker compares the words and numbers in a draft with the `claim` and `excerpt` text a human typed into the campaign file. It never opens the source URL. It cannot tell whether an excerpt is genuine, current or quoted in context, and it does not understand paraphrase, negation, units or sarcasm. When a report passes, every checked token was found in cited evidence text. It does not mean the post is true. A human still has to open each source and confirm that it supports the specific claim.

## Command

```bash
reachweaver claims-check examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json
reachweaver claims-check CAMPAIGN --brand BRAND --json                         # structured report
reachweaver claims-check CAMPAIGN --brand BRAND --duplicate-threshold 0.5      # stricter duplicate check
```

To see the same findings next to exact text, platform lengths, capability status and a confidence level for each channel, run `campaign-preview` ([campaign preview](CAMPAIGN-PREVIEW.md)).

The command exits with `0` when the report has no `error` findings and `2` when it does.

## What is checked

The text used for matching is `claim` plus the optional `excerpt` of the evidence IDs that the variant **cites** in `evidence_ids`. Matching ignores digits inside URLs. The checked body is the variant's `text`. For a `thread` it is all `parts`, and for a `carousel_outline` it is the optional caption plus all `slides`, joined with a blank line. Offsets index into that joined body.

| # | Check | Code | Severity |
|---|---|---|---|
| 1 | Every number, percentage, version, date, time, ordinal or multiplier (`3x`, `3×`, `3 times`, `3-fold`) must appear in cited evidence. If the value only appears in evidence the variant does not cite, the message names that evidence. | `unsupported_number` | error |
| 2 | Testimonial or quote language (`said`, `according to`, a quotation of three or more words), partnership or endorsement language (`partnered with`, `endorsed by`, `trusted by`, `used by`), customer or usage counts (`1,000 users`, `thousands of developers`) and superlatives (`best`, `fastest`, `the first`, `#1`, `the only`). Each is accepted when the phrase or the quoted words appear in cited evidence. A customer count is accepted when one cited evidence item contains both the quantity and the noun. | `needs_human_review` (with `category`) | review |
| 2b | Feature or capability claims: `adds`, `added`, `introduces`/`introducing`, `supports`, `support for`, `integrates with`/`into`, `integration with`, `works with`, `ships with`, `comes with`, `compatible with`, `includes`, `can now`, and `now`/`finally` + `has`, `offers`, `supports`, `integrates with`, `with`, `lets you` and similar. Each content word of the claimed feature (up to 8 words after the trigger) must appear in cited evidence. The message lists the missing words and, when they appear only in uncited evidence, names that evidence. See [feature claims](#feature-claims). | `needs_human_review` (`category: "feature"`, `missing_terms`) | review |
| 3 | Release language (`released`, `launched`, `now available`, `is live`, `shipped`, ...) needs a cited evidence item with `"kind": "release"` or a releases URL such as `/releases/tag/v1.2.0`. | `unsupported_release` | error |
| 4 | Two variants in the same campaign whose word 3-gram Jaccard similarity is at or above the threshold (default `0.6`). | `near_duplicate` | warning |
| 4b | Only with `--history-db` (`campaign-check`, `campaign-preview`): a variant whose word 3-gram Jaccard with a local post of the **same account** from the last `--history-days` days is at or above `0.6`. See [posting history](#posting-history). | `history_near_duplicate` | warning |
| 4b | Only with `--history-db`: the account already has at least `--history-max-posts` local posts in the last `--history-window-days` days. A ReachWeaver brand-hygiene heuristic, not a platform rule. | `history_frequency` | warning |
| 5 | A phrase from the brand's `banned_phrases` list appears in the text (case-insensitive, whole words). | `banned_phrase` | error |
| 5 | The brand's `required_disclosure` text is missing from the variant. | `missing_disclosure` | error |
| 5 | The variant has more hashtags than the brand's `max_hashtags` cap. | `too_many_hashtags` | error |
| 5 | A declared `cta_url` (on the campaign or the variant) or a variant `cta_phrase` does not appear in the text. URL matching ignores query strings such as UTM parameters. | `missing_cta` | warning |
| 6 | The text @-mentions a handle or domain on the local opt-out list (`--optout FILE`, `$REACHWEAVER_OPTOUT` or `config/optout.json`). Thread parts and carousel slides are checked too, and the check applies whatever the brand or per-account rules say. E-mail addresses and URL paths are not mentions. See [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list). | `optout_mention` | error |

Brand rules can be narrowed per account with the optional brand `accounts` object. The merge is additive: banned phrases are the union, every distinct disclosure is required, and the lowest hashtag cap wins. Findings from an account override carry `scope: "account:<id>"`. See [campaign preview](CAMPAIGN-PREVIEW.md#per-account-brand-rules).

In `validate_campaign`, `error` findings become blocking errors, so the variant cannot be staged or exported. `review` and `warning` findings become warnings, which leaves the editorial decision with a person.

## Posting history

Check 4 compares variants only with each other. `campaign-check` and `campaign-preview` can also compare them with what each account has already posted or queued, read from a local ReachWeaver database:

```bash
reachweaver campaign-check --file F --brand B --history-db state/reachweaver.sqlite
reachweaver campaign-preview --file F --brand B --history-db state/reachweaver.sqlite --history-days 60 --json
```

| Option | Default | Effect |
|---|---|---|
| `--history-db PATH` | off | An **existing** database, opened read-only (`mode=ro` and `PRAGMA query_only`). A missing path, a symlink, a non-SQLite file or a database without a `posts` table is an error (exit 2), and the file is never created or modified. The global `--db` is not used by these commands, so history checks only run when you name the file. |
| `--history-days N` | 30 (1..365) | Look-back for `history_near_duplicate`. |
| `--history-window-days W` | 7 (1..365) | Rolling window for `history_frequency`. |
| `--history-max-posts K` | 7 (1..1000) | `history_frequency` warns when the account already has at least K posts in the window. |

The tuning options without `--history-db` are an error rather than silently ignored.

How it works (`reachweaver/history.py`, `storage.connect_readonly`, `storage.posting_history`):

- Only posts with the variant's `account_id` are compared, in the statuses `draft`, `approved`, `scheduled`, `sending`, `published` and `needs_review`. `cancelled` posts are ignored.
- A post's date is its latest `published` audit event, else its `scheduled_at`, else its `created_at`, in UTC. Posts scheduled in the future are included, because they will go out on the same account.
- Posts staged from this same campaign (`posts.campaign` equal to the campaign's `campaign_id`) are skipped and counted as `excluded_same_campaign`, because they are this campaign and check 4 already covers them. A re-run campaign that has been staged under the same `campaign_id` is therefore not compared with its own earlier posts.
- Similarity is the same `claims.jaccard` as check 4 (word 3-gram Jaccard, case, NFKC and URLs folded) with the same `0.6` threshold (`history.HISTORY_DUPLICATE_THRESHOLD`). A score of 1.0 is reported as identical wording.
- At most 5000 of the newest matching rows are read; `history.truncated` says when older ones were left out.

What a finding contains: `history_near_duplicate` names the best-matching post's `matched_post_id`, `matched_status`, `matched_at`, `matched_platform` and `similarity`, plus up to five `also_matched_post_ids`. It never contains the earlier post's text. `history_frequency` appears once per account, on its first variant, with `existing_posts`, `campaign_variants` (how many this campaign adds), `window_days`, `max_posts` and `heuristic: true`.

**The frequency default is a heuristic, not a platform fact.** Seven posts in seven days (about one a day per account) is a ReachWeaver brand-hygiene default chosen so an owner notices a crowded account. No platform publishes it as a limit, quota or ranking signal. Tune it to your own cadence.

Both findings are **warnings**. They never block staging, never change a preview's confidence level and never touch approval, scheduling or the database. The report gains a `history` summary (settings, accounts, `posts_considered`, `excluded_same_campaign`, `truncated`) with no post text. Without `--history-db`, reports are unchanged and have no `history` key.

Unicode: the shared 3-gram tokenizer composes text to NFC before its NFKC step and drops invisible format characters (zero-width space and joiners, word joiner, BOM, soft hyphen, bidi controls), so `café` typed with a combining accent, fullwidth letters, or a word split by a zero-width space match their plain forms. The exception is a zero-width space (U+200B) next to a Thai, Lao, Khmer or Myanmar letter: those scripts are written without spaces between words and use it as the word break, so there it becomes a space instead of merging the line into one token. Dropping a zero-width non-joiner joins the two parts of a Persian word, which is harmless for similarity. This also applies to check 4.

## Feature claims

Check 2b flags capability claims whose key words are absent from the evidence the variant cites, so an invented integration or feature ("now supports TikTok scheduling" when the source never mentions TikTok) reaches a human. It is a **review** finding: `validate_campaign` reports it as a warning, `campaign-preview` lowers the variant's confidence to `medium`, and staging and export are not blocked.

How a claim is read:

1. A trigger phrase starts the claim (see check 2b). An `adds`/`supports` that is part of a hyphenated word (`value-added`) is not a trigger.
2. The claimed feature is the text after the trigger up to the first sentence end (`.`, `;`, `:`, `!`, `?`, a line break, bracket, quote mark or dash), the start of a new clause (`that`, `which`, `because`, `but`, `when`, `not`, `and it`, ...) or the next trigger, and at most 8 words. "Supports X and adds Y" is two claims.
3. Content words are kept. Stopwords, generic marketing words (`new`, `native`, `full`, `better`, `easy`, `seamless`, `support`, `feature`, `tool`, ...) and bare numbers or versions are dropped. Numbers stay check 1's job. A claim with no content word left ("works with you") is ignored.
4. Each content word must appear in the `claim` or `excerpt` text of at least one cited evidence item. Matching ignores case and applies NFKC, and folds simple plurals and possessives on both sides (`queues`/`queue`, `caches`/`cache`, `policies`/`policy`, `Mastodon's`/`Mastodon`). There is no stemming of verbs and no synonym list, so `scheduling` is not grounded by `schedule`.

Skipped on purpose, to keep false positives low: claims negated just before the trigger (`does not support`, `doesn't integrate with`, `no longer supports`, `never`), claims inside a question (`Does it now support TikTok?`) and URLs.

A signal brief (`signal-brief`) puts the source signal's summary into the first evidence item's `excerpt`, so feature words from the signal ground a variant that cites that evidence. That summary is untrusted scan text; replace it with a verbatim excerpt you checked before you rely on a clean report.

## Numbers, locales and Unicode

- Before matching, text goes through NFKC normalization, and Unicode decimal digits are mapped to ASCII. Fullwidth `１２％`, Arabic-Indic `١٢` and no-break spaces before `%` all match their ASCII forms. Superscripts and vulgar fractions stay as they are, so the checker never invents digits from them.
- Thousands separators `,` `.` `'` and a space followed by three digits are recognized. **An ambiguous separator matches every plausible reading.** For example, `1.000` matches both `1000` and `1`. This is lenient on purpose, so one reading can hide a mismatch under another. Review figures that use mixed locales.
- The kind of value must match. `40%` is not grounded by a plain `40`. A multiplier is not grounded by a percentage, so `3x` does not match `200%`.
- Numbers written as words (`three`), unit conversions and arithmetic, such as a percentage derived from two counts, are **not** recognized. Put the exact figure in the excerpt.

## Contract additions (optional, backwards compatible)

Campaign files without these fields work as before.

```json
{
  "cta_url": "https://github.com/MSKazemi/reachweaver",
  "evidence": [{"id": "release-020", "kind": "release",
                "excerpt": "Verbatim text copied from the source page, at most 5000 characters.", "...": "..."}],
  "variants": [{"cta_phrase": "review the design", "...": "..."}]
}
```

`kind` must be one of `release`, `repository`, `documentation`, `paper`, `benchmark`, `announcement`, `testimonial`, `partnership`, `metric` or `other`. Brand files can also set `banned_phrases` (a list of nonempty strings), `required_disclosure` (a nonempty string of at most 280 characters), `max_hashtags` (an integer from 0 to 30) and `accounts` (per-account overrides of those three fields, keyed by an approved account ID). Variants can also set `rationale`, and use `format: "thread"` with `parts[]` or `format: "carousel_outline"` with `slides[]`. Both formats are previewed but never staged. See [campaign preview](CAMPAIGN-PREVIEW.md).

## Report shape (`--json`)

```text
report, method="lexical", campaign_id, valid, duplicate_threshold,
summary{error, review, warning, grounded}, limitations,
variants[]{index, variant_id, platform, cited_evidence[],
  findings[]{code, severity, message, span, start, end, matched_evidence[], category?, missing_terms?, other_variant_id?, similarity?, scope?, hashtags?},
  grounded[]{type, span, start, end, matched_evidence[]}}
```

`start` and `end` are Python string offsets into the original variant text. `grounded` lists each token or phrase that was found, together with the evidence IDs that contain it. A grounded feature claim has `type: "feature"`; `landing-check` does not look for these spans on landing pages, because the span is the draft's own wording ("now supports X").

## Known limits

- An excerpt is a declaration made by a person. If the excerpt is fabricated, a fabricated claim will pass.
- The phrase lists are English only and cover common patterns, not every way of saying something. Negated or hedged wording ("not the fastest") is still flagged for review.
- Near-duplicate detection measures surface wording only. Two variants can share a structure or angle and still score below the threshold.
- Feature detection (check 2b) only sees the trigger phrases listed above. A capability stated another way ("posting to TikTok is one click away", "lets you schedule Reels") passes. It checks words, not meaning: "supports Mastodon" is grounded by an excerpt that says "does not support Mastodon". Comparing the claim with the source is still a human task.
