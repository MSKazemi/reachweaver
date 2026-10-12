# Evidence-ranked next actions — `reachweaver suggest` (Issue #6)

**Status:** offline, deterministic, read-only. This is a prioritization helper, **not a prediction of social views**, a hidden platform ranking algorithm or permission to publish. All actions require human review.

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

## Safe quickstart

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

## Ranking rules

Rank using an explicit **policy and evidence hierarchy**, not pseudo-scientific precision:

| Priority | Meaning | Example |
|---|---|---|
| P0 | Privacy or unresolved remote publication uncertainty | User opt-out, `needs_review`, stale `sending`, active emergency pause |
| P1 | Confirmed operational/blocking concern | Reapproval or overdue schedule, owner-reported missing core profile field, SEO canonical duplication, human-response-needed technical question |
| P2 | Actionable optimization or missing measurement evidence | Missing native metrics, owner-flagged weak hook/CTA, noncritical field improvement, one ongoing experiment awaiting more data |
| P3 | Investigation/test hypothesis after safety and known fixes | Check SEO live indexing independently, preregister an experiment, build first snapshot baseline |

The JSON response exposes the source, evidence counts, owner/source confidence classification, missing coverage and explicit human-review requirement. Every `next_step` is static and does **not** quote untrusted comment text, display social handles, tokens or private SEO URLs. Records are sorted deterministically within priority; **this order is a workflow convenience, not a measure of growth impact.**

Unknown profile fields (not supplied) are counted as unobserved, not missing. An owner-reported `false` is a known fix; fewer than 60% checked means no profile score. Small format cohorts do not yield false "low engagement" claims, and source-labeled impressions, views and reach are never pooled across platforms.

Safety decisions override virality: if the last publish attempt may have succeeded, the operator must inspect the native account before manually reconciling; this tool never retries. Community sites such as Reddit and Hacker News require human-written/policy-compliant participation.

## Validation and limitations

- Synthetic tests verify P0 safety outranks P2 content changes, deterministic ordering, no untrusted excerpts, opt-out precedence, SEO URL withholding and no network/SQLite creation in offline mode.
- GitHub Actions runs a full Python compatibility matrix and mirrors the code into the standalone Claude Code skill. This is **automated behavior testing**, not evidence of actual social reach growth.
- No direct private analytics account connection is opened by this feature. The separate `metrics-fetch` and provider-specific import flows remain the appropriate explicitly authorized sources; avoid sharing OAuth tokens or private datasets with the public repository.
- Future work: comprehensive first-party profile read-only connectors with provider-approved scopes, user-controlled analytics import, explicit multi-week prospective trials and a human dashboard for verified downstream website/GitHub outcomes.

See [platform-by-platform audit guide](PROFILE-AND-POST-AUDITS.md), [worker safety](WORKER-OPERATIONS.md), [SEO integration](SEO-INTEGRATION.md) and [evidence/experiment methodology](GROWTH-EXPERIMENTS.md).
