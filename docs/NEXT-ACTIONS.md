# Evidence-ranked next actions — `reachweaver suggest` (Issue #6)

**Status:** offline, deterministic, read-only. This is a prioritization helper, **not a prediction of social views**, a hidden platform ranking algorithm or permission to publish. All actions require human review.

## "What should I post next?" — `reachweaver next`

For a creator the everyday question is simpler than a ranked review, so `reachweaver next` answers it with **at most five concrete steps, each with one command to type**:

1. **Safety first** (only when present): a send that ended in an unknown state (`dead-letter`), a stalled send (`queue-health`), an approval that no longer matches its post (`review`), a missed schedule, or the emergency stop.
2. **Drafts waiting** -> `reachweaver review` (your own approval loop; nothing is sent by looking).
3. **Posts due for their 7-day numbers**: a post 6 to 30 days old with no numbers from day 6 on -> the exact `reachweaver metrics POST_ID --likes N --replies N --reposts N` line (and how many more are due).
4. **Each open idea by name** (oldest first) -> `reachweaver share --web --open --idea IDEA_ID`: the browser composer opens with that idea as your caption (the ideas inbox is only read; if the composer already holds a post, nothing is replaced and the page asks first). The command carries the idea's id, never its text, so nothing the idea says reaches your shell. The note names your "usual networks": the ones you posted on most in the last 90 days (posts sent, posts recorded with `mark-shared`), at most three, that `share` accepts and that are not human-only. The composer only writes copy-ready packages; nothing is posted. Under `reachweaver studio`, the dashboard's Ideas page has the same step as a link (**Write a post from this idea**).
5. **Days since you last posted** on each network you use (`Instagram 8 days ago, Bluesky yesterday`), with the composer when one network has been quiet for 7 days or more.
6. **The strongest "what works for you" line** from `insights`, when your own results support one (an observation to test, not a cause).

Ideas fill the slots the other steps leave, and at least one idea is shown when you have any. With nothing recorded yet it points at the browser composer and `idea add`.

```bash
reachweaver next
reachweaver next --json     # schema reachweaver.next.v1
```

`--json` keys (stable within `reachweaver.next.v1`; keys may be added, never renamed): `schema`, `generated_at`, `actions[]` (`rank`, `kind` = `safety` | `drafts` | `results_due` | `idea` | `rhythm` | `what_works` | `first_post` | `save_idea`, `text`, `command` or null, `note` or null, `evidence`), `counts` (`open_ideas`, `posts_recorded`, `drafts`, `results_due`), `usual_networks`, `warnings`, `read_only` (always true), `note`.

`next` reads the ideas inbox and an **existing** database (`mode=ro` plus query-only; a missing one is never created, an older one is never migrated). It drafts, approves, schedules and sends nothing, and it is not a prediction of reach: the order is the fixed rule above. Because it prints your idea text and the first words of your posts, `mcp-serve` does not offer it as a tool (`mcp_server.WITHHELD_COMMANDS`); `suggest --json` stays counts-only.

Bare `reachweaver suggest` prints this same view, followed by a pointer to the full ranked review below (`suggest --include-db`). `suggest --include-db` now also counts open ideas (one P3 `open_ideas` item pointing at `next`; counts only, never the text).

## Purpose

A maintainer has multiple sources of truth: a manually inspected LinkedIn profile, owner-exported TikTok/YouTube post metrics, a local ReachWeaver outbox, DEV.to technical questions, SEO destination briefs, and classified content experiments. ReachWeaver should combine them into **one ranked review queue** without pretending missing analytics mean poor performance or ranking profiles by likes.

The command combines existing validated capabilities rather than introducing an additional AI scoring model.

| Source | CLI input | Evidence used | Not inferred |
|---|---|---|---|
| Platform-specific profile snapshot | `--snapshot state/owned-profiles.json` | Owner-reported missing profile fields, limited first-hand coverage, unsupported claim/length warnings | Whether the account really has the feature; how the algorithm weights it |
| Owner's post observations | Same `--snapshot` | Missing comparable data, explicitly flagged originality/hook/accessibility/CTA and count of affected posts | Causal effects or guaranteed improvement; popularity of one format on another platform |
| Existing local SQLite queue | `--include-db` | Persistent uncertain sends, stalled attempts, overdue schedules, binding reapproval, emergency pause | Remote publish success or permission to retry |
| Existing SQLite post metrics | `--include-db` | Per-platform published-post count and native snapshot/denominator coverage | A per-account attribution: `report` groups by platform; missing views are **unknown** |
| Engagement export | `--engagement-export state/my-engagement.json` | Counts of rule-classified bug reports, genuine technical questions, collaboration opportunities, opt-out requests | True intent, permission to contact; reporter identities/comments are **not** included in output |
| SEO briefs | `--seo-briefs state/seo-briefs.json` | Known duplicate canonical pages and conflicting scheduled page/keyword briefs | Google indexing position or traffic uplift |
| Experiment results | `--experiments state/experiment-results.json` | Existing `experiment-next` rule and sample history | Proof of statistical significance or a reason to exploit a winning variant automatically |

## Owner-approved CSV analytics as optional evidence

If you have a lawful owner export, normalized to `examples/profile-post-metrics.example.csv`, add `--metrics-csv state/my-export.csv --owner-authorized` alongside `--snapshot state/owned-profiles.json`. This adds only nonduplicated, account-matched observations **in memory**. It never calls an API, stores a CSV elsewhere, or treats provenance as cryptographically verified. See [CSV schema and limitations](PROFILE-METRICS-CSV.md). Without explicit ownership acknowledgement, the analytics file is refused.

## Safe quickstart

With **no input flag**, `suggest` prints the same plain view as `reachweaver next` (above). `suggest --json` without an input flag keeps its `reachweaver.suggestions.v1` schema: it looks at what is already on your computer, an existing local database opened query-only exactly as with `--include-db` (a missing one is skipped and never created), plus the number of open ideas in your ideas inbox (counts only, never the idea text); with nothing to rank it adds a `next_steps` list (`share --web --open`, `idea add`, `start`). Explicit input flags work exactly as before.

```bash
reachweaver suggest
```

Try the checked-in **synthetic example** first:

```bash
reachweaver suggest --snapshot examples/profile-post-audit.example.json --limit 10
reachweaver suggest --snapshot examples/profile-post-audit.example.json --json
```

Combine **real owner-authorized** exports kept privately under `state/` (gitignored):

```bash
reachweaver suggest \
  --snapshot state/owned-profiles.json \
  --engagement-export state/engagement-export.json \
  --seo-briefs state/seo-briefs.json \
  --experiments state/experiment-results.json \
  --limit 15 --json
```

Explicitly include a **pre-existing** local SQLite database using query-only mode:

```bash
reachweaver --db state/reachweaver.sqlite suggest \
  --include-db --snapshot state/owned-profiles.json --limit 15 --json
```

The `--include-db` option is **never implied**: without it the command neither opens nor creates the outbox. With it, an absent/symlink/non-SQLite DB is refused. The command uses `storage.connect_readonly` (SQLite `mode=ro`, `PRAGMA query_only`) and never performs migrations. No account tokens or online APIs are used in `suggest`. The exact owner-provided input files are also never changed.

Use [`profile-audit`](PROFILE-AND-POST-AUDITS.md), [`post-audit`](PROFILE-AND-POST-AUDITS.md), `engagement-triage`, `seo-destinations`, `experiment-next`, `queue-health`, and `report` independently to investigate details. Native metrics already retrieved by separate owner-authorized tools (for example public Bluesky/Mastodon post counters through `metrics-fetch --online`) may be reflected in the local database, but **running `suggest` never invokes a collector**.

## Weekly digest (`weekly-digest`, issue #45 W7)

`weekly-digest` writes the same ranked actions as `suggest`, and with `--include-db` the local `report` totals, into one Markdown file you read and share (or not) yourself.

```bash
reachweaver weekly-digest --snapshot state/owned-profiles.json --out state/digest-2026-10-13.md
reachweaver --db state/reachweaver.sqlite weekly-digest --include-db --snapshot state/owned-profiles.json --limit 15 --out state/digest.md
```

- It accepts the same input flags as `suggest` and loads them through the same code, so the two cannot disagree about what a file means. `--out` is required.
- The file is created new with mode `0600`. An existing file or a symlink at `--out` is refused and left untouched, and the parent directory must already exist.
- There is no send, e-mail, post or schedule path and no network access. Scheduling it (cron, a calendar reminder) and delivering it are your decisions.
- Free text is Markdown-escaped; fixed identifiers (priority, kind, signal, source) are written as code spans after dropping unexpected characters. Post text and comments are not copied in.
- The digest repeats the evidence labels and limits of its inputs and adds no ranking or forecast. Missing metrics stay `unknown`, never 0.
- The file can hold account ids and local metrics, so keep it under `state/` (gitignored).

## Ranking rules

Rank using an explicit **policy and evidence hierarchy**, not pseudo-scientific precision:

| Priority | Meaning | Example |
|---|---|---|
| P0 | Privacy or unresolved remote publication uncertainty | User opt-out, `needs_review`, stale `sending`, active emergency pause |
| P1 | Confirmed operational/blocking concern | Reapproval or overdue schedule, owner-reported missing core profile field, SEO canonical duplication, human-response-needed technical question |
| P2 | Actionable optimization or missing measurement evidence | Missing native metrics, owner-flagged weak hook/CTA, noncritical field improvement, one ongoing experiment awaiting more data |
| P3 | Investigation/test hypothesis after safety and known fixes | Check SEO live indexing independently, preregister an experiment, build first snapshot baseline, a "What works for you" observation, re-verify aging research |

**What works for you.** When `suggest` reads a database (bare `suggest` or `--include-db`), it adds up to three P3 `what_works_for_you` items per account from [`insights`](INSIGHTS.md#where-your-results-are-used). Each one says "try more of" or "try less of" a feature, gives the n and the medians of the group and the rest, and is marked `observational_only`. The next step is always to test it with a preregistered experiment, never to treat it as a cause. **Research freshness.** When a bundled registry fact is aging or stale, `suggest` adds one P3 `research_freshness` item with the `freshness.warning_line` text and points to `reachweaver registry-age`. Nothing is added when everything is fresh.

The JSON response exposes the source, evidence counts, owner/source confidence classification, missing coverage and explicit human-review requirement. Every `next_step` is static and does **not** quote untrusted comment text, display social handles, tokens or private SEO URLs. Records are sorted deterministically within priority; **this order is a workflow convenience, not a measure of growth impact.**

Unknown profile fields (not supplied) are counted as unobserved, not missing. An owner-reported `false` is a known fix; fewer than 60% checked means no profile score. Small format cohorts do not yield false "low engagement" claims, and source-labeled impressions, views and reach are never pooled across platforms.

Safety decisions override virality: if the last publish attempt may have succeeded, the operator must inspect the native account before manually reconciling; this tool never retries. Community sites such as Reddit and Hacker News require human-written/policy-compliant participation.

## Validation and limitations

- Synthetic tests verify P0 safety outranks P2 content changes, deterministic ordering, no untrusted excerpts, opt-out precedence, SEO URL withholding and no network/SQLite creation in offline mode.
- GitHub Actions runs a full Python compatibility matrix and mirrors the code into the standalone Claude Code skill. This is **automated behavior testing**, not evidence of actual social reach growth.
- No direct private analytics account connection is opened by this feature. The separate `metrics-fetch` and provider-specific import flows remain the appropriate explicitly authorized sources; avoid sharing OAuth tokens or private datasets with the public repository.
- Future work: comprehensive first-party profile read-only connectors with provider-approved scopes, user-controlled analytics import, explicit multi-week prospective trials and a human dashboard for verified downstream website/GitHub outcomes.

See [platform-by-platform audit guide](PROFILE-AND-POST-AUDITS.md), [worker safety](WORKER-OPERATIONS.md), [SEO integration](SEO-INTEGRATION.md) and [evidence/experiment methodology](GROWTH-EXPERIMENTS.md).
