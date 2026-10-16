# Release playbook: from a tagged release to a measured campaign

**Status (2026-10-08):** Every command below exists and is covered by offline tests. The live steps (12 and 13) have **not** been run against a real account in this repository; see [FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md). This playbook is the ReachWeaver arm of the [field pilot protocol](FIELD-PILOT-PROTOCOL.md) and part of [issue #27](https://github.com/MSKazemi/reachweaver/issues/55).

The playbook turns one public release of a project you own into a small campaign on networks you are authorized to use, then measures what happened. Each **HUMAN GATE** is a decision only the owner can make. No command, plan, calendar or Claude Code session can make it for them, and passing an earlier gate never approves a later one.

Paths such as `private/` are placeholders for a directory outside any public repository.

## Maintainer release checks

This section is about releasing **ReachWeaver itself** as a GitHub Release (`skill.zip`, the Python wheel and sdist, and their checksums), not about posting to any social account. The [release workflow](../.github/workflows/release.yml) starts only when a `vMAJOR.MINOR.PATCH` tag is pushed, and has two jobs that always run and one optional job:

- **`verify`** runs with read-only repository permission and a checkout that keeps no credentials (`persist-credentials: false`). It runs `scripts/release_ref_guard.py`, then the same hygiene checks as CI (tracked-file secret scan, stdlib lint, byte compilation, runtime-dependency guard, and Ruff and mypy at the versions and flags pinned in `ci.yml`), `python scripts/sync_skill.py --check`, the tag-equals-`__version__` check, and `python scripts/release_gate.py --tag "$GITHUB_REF_NAME"` **with** the unit tests. No step may skip tests or ignore a failure.
- **`publish`** runs only after `verify` passed, and only when both the actor who pushed the tag and whoever started the run are the repository owner. It is the only job with `contents: write`. It builds `skill.zip`, tests the archive (`python -m zipfile -t`), writes and checks `skill.zip.sha256`, runs the guard again on its own checkout, then `gh release create ... --verify-tag`, which refuses to create a tag that is not already on GitHub.
- **`pypi`** (optional, **off by default**) runs only after `publish`, only for the repository owner, only when the repository variable `PYPI_PUBLISH` is `true`, and only after the owner approves the `pypi` environment (restricted to `v*` tags). It checks out nothing and builds nothing: it downloads the wheel and sdist that `publish` attached to the release, verifies them against `python-dist.sha256`, and uploads exactly those files to PyPI by trusted publishing. Its only extra permission is `id-token: write` (the short-lived OpenID Connect token PyPI trusts); there is no stored PyPI token. See [the Python package guide](PACKAGING.md).

`scripts/release_ref_guard.py` reads GitHub's default `GITHUB_*` variables and the job's own checkout (fetched with `fetch-depth: 0`); it uses no token and no network. It fails closed unless the run is a tag push, the tag is exactly `vMAJOR.MINOR.PATCH`, the pushing and triggering actors are the repository owner (login and account id), and the checked-out commit, the tag, `GITHUB_SHA` and `origin/main` are one commit. `GITHUB_SHA` is the same in both jobs, so `publish` releases the commit `verify` checked. A tag pushed before `main` moved on, or re-run after it moved, is refused: cut a fresh version from the current reviewed `main` instead of working around the check. The guard sees `main` as fetched at the start of its job; a push to `main` in the seconds between that fetch and the release is not detected.

Sources, read 2026-10-15: [`actions/checkout`](https://github.com/actions/checkout/blob/main/README.md) (`fetch-depth: 0` fetches "all history for all branches and tags"; `persist-credentials: false` opts out of keeping the token in the git config), [GitHub's default variables](https://docs.github.com/en/actions/reference/workflows-and-actions/variables) (`GITHUB_REF_TYPE`, `GITHUB_ACTOR_ID`, `GITHUB_REPOSITORY_OWNER_ID`, and `GITHUB_TRIGGERING_ACTOR`, which differs from `GITHUB_ACTOR` on a re-run), [expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions) ("GitHub ignores case when comparing strings") and [`gh release create`](https://cli.github.com/manual/gh_release_create) (`--verify-tag`: "Abort in case the git tag doesn't already exist in the remote repository").

Before pushing the tag, on the reviewed release commit:

```bash
make check
make ruff
make typecheck
make release-gate TAG=vX.Y.Z
```

**What this does not do.** The workflow cannot turn on branch protection: required reviews, required status checks and blocking force pushes on `main` are repository settings the owner configures and verifies in GitHub, and nothing in this repository proves they are on. Where a plan does not offer branch rules, the remaining control is manual: the owner reviews each commit and waits for green CI on it before tagging. A green release run is technical evidence only. It is not legal, rights, licensing or privacy clearance, it does not review Git history or release assets for secrets, and it is not a receipt that anything was posted to a social platform; those stay separate owner checks.

## Before you start: what ReachWeaver itself is ready for

```bash
python3 scripts/release_gate.py --scorecard          # table: status, issue, evidence count, blocker kinds
python3 scripts/release_gate.py --scorecard --json   # the same, machine-readable
```

The scorecard ([release-readiness.json](release-readiness.json)) lists each platform and product area as `offline_mock_tested`, `manual_export_only`, `blocked`, `not_started` or `live_receipt_verified`. Today only Bluesky and Mastodon are `live_receipt_verified`, each by one approved text post sent through ReachWeaver on 2026-10-11 (first-party receipts in [receipts/](receipts/)); the Telegram and LinkedIn adapters and the Postiz backend are mock-tested only, the Meta, YouTube, TikTok, X, Pinterest, DEV and Hashnode paths are manual or draft exports, and Reddit and Hacker News are blocked as human-only. Use it to pick the networks for step 9 versus step 13. `make release-gate` fails when the scorecard names a missing file, uses an unknown status, omits a capability-registry platform, gives a row whose `id`, `capability_id` or `distribution_id` names a human-only distribution-registry entry (such as `hacker_news` or `reddit`) any status but `blocked` or any other `distribution_id`, or claims a live receipt without a checked-in first-party receipt file.

## Overview

| # | Step | Command | Writes | Network | Human gate |
|---|---|---|---|---|---|
| 1 | Find what is worth announcing | `sources-scan` | nothing (optional `--state` file) | only with `--online` (GET) | pick the release |
| 2 | Scaffold, then write the campaign file | `signal-brief`, then editor / Claude Code | new 0600 campaign JSON + `.brief.md` | no | evidence is true |
| 2b | Turn the same source into a video and image cut list (optional) | `media-storyboard --campaign` | new folder: storyboard, SRT skeleton, manifest | no | write every placeholder; real footage only |
| 3 | Check claims and structure | `claims-check`, `campaign-check` | nothing | no | resolve every finding |
| 4 | Check media (if any) | `media-check` | nothing | no | rights and alt text |
| 5 | Choose destinations and links | `seo-destinations`, `utm-link` | nothing | no | landing page |
| 6 | Preregister the test (optional) | `experiment-preregister` | new 0600 record | no | freeze the plan |
| 7 | Stage drafts | `campaign-stage` | `draft` rows | no | — |
| 8 | Review and approve each post | `show`, `check`, `approve` | `approved` | no | **exact text + account** |
| 9 | Manual-only networks | `manual-export`, `campaign-export` | new folder | no | owner posts by hand |
| 10 | Propose a calendar | `calendar-plan` | nothing | no | review slots |
| 11 | Queue locally | `calendar-plan --apply` or `schedule` | `scheduled` | no | **authorize local queueing** |
| 12 | Dry run | `publish --post-id` | nothing | no | read the preview |
| 13 | Supervised live send | `REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 publish --live --post-id` | `sending` → `published`/`needs_review` | yes | **authorize this one send** |
| 14 | Reconcile | `queue-health`, `reconcile` | `published`/`draft` | no | check the native post |
| 15 | Import metrics | `import-metrics` | snapshot rows | no | data is first-party |
| 16 | Attribute traffic | `attribution-report` | nothing | no | read gaps |
| 17 | Read the experiment | `experiment-verify`, `experiment-evaluate` | nothing | no | decide next test |

`pause` stops all live publishing at any point. Use it first and investigate second.

## 1. Find what is worth announcing

```bash
reachweaver sources-scan --config sources.reachweaver.json --top 5          # local fixtures only
reachweaver sources-scan --config sources.reachweaver.json --online --top 5  # explicit GET of public GitHub/feeds
```

The scan ranks releases, tags, merged PRs, feeds and DOI records with evidence URLs and a score breakdown ([SOURCES.md](SOURCES.md)). Its output is untrusted data and a lead, not a draft.

**HUMAN GATE:** choose one release that is public, real and worth a reader's time. Open the release page yourself.

## 2. Scaffold, then write the campaign file

```bash
reachweaver sources-scan --config sources.reachweaver.json --json > private/scan.json
reachweaver signal-brief --scan private/scan.json --signal-id SIGNAL_ID --brand private/brand.json \
  --accounts config/accounts.json --out private/release.campaign.json   # + private/release.campaign.brief.md
```

`signal-brief` turns the chosen signal into a new campaign file ([SOURCES.md](SOURCES.md#from-signal-to-brief-signal-brief)). It fills in the evidence URLs, an untrusted `excerpt`, one placeholder variant per approved account with a live or manual path, and each variant's `utm-link` command. The sidecar `.brief.md` shows the scores, uncertainties, any injection warning and each account's length limit. It writes no copy. Every `{{write: ...}}` placeholder, plus the missing `verification` and `checked_at`, keeps the file **BLOCKED** until you finish it. You can also start by hand from `examples/campaign.reachweaver.json` and `examples/brand.reachweaver.json` ([SCHEMAS.md](SCHEMAS.md), `.claude/skills/reachweaver/references/campaign-contract.md`).

Either way, each claim needs an `evidence` entry with the release URL, the exact claim, an optional `excerpt` copied verbatim from the source (replace the scaffold's untrusted scan summary) and `kind: "release"`. Write a distinct variant per network; do not paste the same text everywhere. Claude Code can draft variants with `/reachweaver`, but it only proposes text.

**HUMAN GATE:** every evidence entry is something you checked in the primary source, today.

### 2b. One source, every format: variants plus a media cut list (public issue #8)

The scaffold from step 2 already holds **one placeholder variant per approved account** (each platform's own text budget is recorded), so one source becomes per-platform variants you write. The same campaign file also feeds the media tool:

```bash
reachweaver media-storyboard --campaign private/release.campaign.json --out private/storyboard \
  --duration 30 --video-targets tiktok youtube_shorts --image-targets instagram linkedin
```

It writes a **new** folder with `storyboard.json` and `storyboard.md` (a timed scene list: hook, beats tied to your evidence entries, call to action, with the text box and the platform UI-overlay safe areas), a `subtitles.en.srt` skeleton, per-target image specs and a `media-manifest.json`. No copy, alt text, subtitle text, image or video is generated: every creative field stays a `{{write: ...}}` placeholder, the status is `SCAFFOLD_BLOCKED_UNTIL_HUMAN_EDIT`, and `media-check` on the manifest stays blocked until real files exist, rights are confirmed and every placeholder is written. Record or capture real footage yourself, edit `storyboard.json`, then re-check with `media-storyboard --check private/storyboard/storyboard.json` ([MEDIA-ASSETS.md](MEDIA-ASSETS.md)). The whole chain (`sources-scan` on the offline fixtures, `signal-brief`, `media-storyboard`, `media-check`) runs offline and is pinned by `tests/test_repurposing_chain.py`.

## 3. Check claims and structure

```bash
reachweaver claims-check private/release.campaign.json --brand private/brand.json --json
reachweaver campaign-check --file private/release.campaign.json --brand private/brand.json --json
```

`claims-check` blocks numbers, versions, dates and release language that the cited evidence does not lexically contain, and flags testimonials, superlatives and near-duplicate variants for review ([CLAIM-GROUNDING.md](CLAIM-GROUNDING.md)). `campaign-check` checks the schema, the brand-to-account allowlist and media formats. Both exit 2 on errors.

**HUMAN GATE:** fix every error and decide every review finding. Lexical grounding is not truth verification: a passing check means the words match your evidence, not that the evidence is right.

## 4. Check media (only if the campaign has images or video)

```bash
reachweaver media-check private/media/manifest.json --json
```

It reads real file headers, sourced platform limits, alt text, captions and rights fields ([MEDIA-ASSETS.md](MEDIA-ASSETS.md)). ReachWeaver's live adapters are text-only, so media posts go through step 9.

**HUMAN GATE:** you hold the rights to every asset and the alt text describes it accurately.

## 5. Choose destinations and build tagged links

```bash
reachweaver seo-destinations --briefs private/seo-briefs.json --type release --project my-project \
  --platform bluesky --campaign release-v1-2
reachweaver utm-link 'https://github.com/OWNER/REPO/releases/tag/v1.2.0' \
  --platform bluesky --campaign release-v1-2 --content developer-bluesky \
  --allow-origin https://github.com
```

`seo-destinations` returns the canonical landing page for the campaign type and flags competing campaigns ([SEO-INTEGRATION.md](SEO-INTEGRATION.md)). `utm-link` builds the canonical, tracker-free link. Use the campaign `variant_id` as `--content` (posts do not exist yet), with one link per variant and network; `attribution-report` credits a variant within the same campaign ([ATTRIBUTION.md](ATTRIBUTION.md)). Put the link in the variant text, then rerun step 3.

**HUMAN GATE:** if the status is `review_conflicts`, resolve every entry in `destination_conflicts` (exit 2 until then), and confirm the landing page is the one you want readers to reach.

## 6. Preregister the test (optional, before any post)

```bash
reachweaver experiment-preregister private/release-hook.plan.json --out private/release-hook.prereg.json
```

Use this only when the campaign tests one change (hook, framing, format, CTA or timing) with 2–3 arms ([GROWTH-EXPERIMENTS.md](GROWTH-EXPERIMENTS.md)). The record is not approval to publish.

**HUMAN GATE:** commit or externally timestamp the record before the first post, so later edits are detectable.

## 7. Stage drafts

```bash
reachweaver campaign-stage --file private/release.campaign.json --brand private/brand.json
```

It needs your ignored `config/accounts.json`. It creates `draft` rows only, idempotently, and refuses a changed manifest with the same IDs. Only `text` variants can be staged.

## 8. Review and approve each post

```bash
reachweaver list --status draft
reachweaver show POST_ID      # exact text, account, source and an offline platform_preview
reachweaver check POST_ID     # platform limits for this account
reachweaver approve POST_ID   # one exact post
```

**HUMAN GATE (the main one):** read the full text in `show`, confirm the destination account and handle, and approve each post individually. An approved post cannot be edited; to change it, `unapprove` or `cancel` and stage a new version.

## 9. Networks without a ReachWeaver adapter

```bash
reachweaver manual-export --platform threads --variant private/threads-variant.json --out private/threads-pack
reachweaver campaign-export --file private/release.campaign.json --brand private/brand.json --out private/manual-pack
```

`manual-export` prepares a creator-ready package and checklist for Threads, Instagram, Facebook Pages, YouTube and TikTok ([MANUAL-EXPORT.md](MANUAL-EXPORT.md)); `campaign-export` writes a review pack for any variant. Hacker News is human-authored only, and Reddit is subject to each community's rules ([DISTRIBUTION-GOVERNANCE.md](DISTRIBUTION-GOVERNANCE.md)).

**HUMAN GATE:** you post by hand in the official app and then record the real URL. The package never claims a post exists.

## 10. Propose a calendar

```bash
reachweaver calendar-plan --account my-bluesky --timezone Europe/Rome --start 2026-10-15 --days 14
reachweaver calendar-plan --account my-mastodon --timezone Europe/Rome --start 2026-10-15 --days 14 \
  --slot 'Tue@10:00' --slot 'Thu@16:00'
```

The proposal uses low-confidence priors or your own slots, in audience-local time and UTC ([CALENDAR-PLANNER.md](CALENDAR-PLANNER.md)). It writes nothing.

**HUMAN GATE:** review each date, time zone and account.

## 11. Queue locally

```bash
reachweaver calendar-plan --account my-bluesky --timezone Europe/Rome --start 2026-10-15 --days 14 --apply
# or, for one post:
reachweaver schedule POST_ID --at '2026-10-15T09:00:00+02:00'
```

`--apply` records local due times for already-approved Bluesky, Mastodon, Telegram or LinkedIn-member posts, all or nothing. It does not publish and does not create a remote schedule.

**HUMAN GATE:** authorize local queueing for exactly these approved posts.

## 12. Dry run

```bash
reachweaver publish --post-id POST_ID
reachweaver queue-health
```

The dry run sends nothing. It shows whether the post is approved and due, the live budget and whether the emergency pause is on.

**HUMAN GATE:** read the preview. If anything is off, `unapprove` or `cancel`.

## 13. Supervised live send

```bash
reachweaver connect-check --account my-bluesky --online   # identity only; posts nothing
REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 reachweaver publish --live --post-id POST_ID
```

The send needs `--live`, `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod` (production host only), an approved and due post, no pause, credentials in the owner's environment, the capability registry and an identity preflight, and it stays inside the rolling per-account budget ([ARCHITECTURE.md](ARCHITECTURE.md)). Exit 0 is a verified receipt, 2 is blocked before sending, 3 is an uncertain outcome. Credentials are entered by the owner only, never through Claude or chat ([FIRST-LIVE-PILOT.md](FIRST-LIVE-PILOT.md)). For an unattended worker, read [WORKER-OPERATIONS.md](WORKER-OPERATIONS.md) first; do not use one for a first release campaign.

**HUMAN GATE:** authorize this one send to this one verified account, with the owner present.

## 14. Reconcile

```bash
reachweaver queue-health
reachweaver reconcile POST_ID --published --remote-id ID --remote-url https://...
reachweaver reconcile POST_ID --not-published
```

A post in `sending` or `needs_review` is never retried automatically, and neither is a Postiz acceptance ([POSTIZ.md](POSTIZ.md)).

**HUMAN GATE:** open the native account, find (or fail to find) the exact post, then reconcile with what you saw. Never resend before checking.

## 15. Import native metrics

```bash
reachweaver import-metrics --csv private/native-metrics.csv
reachweaver report
```

The CSV has `post_id`, `observed_at` (with a time zone), `source` and any of `views`, `impressions`, `likes`, `replies`, `reposts`, `saves`, `clicks`, `signups`. Blank cells stay unknown. Take snapshots at fixed post ages, for example +24 h and +7 d ([TIMING-EVALUATION.md](TIMING-EVALUATION.md)).

**HUMAN GATE:** the numbers come from the platform's own export or counter, and the `source` label says which.

## 16. Attribute traffic

```bash
reachweaver attribution-report --analytics private/analytics.csv \
  --github-traffic private/traffic-views.json --github-traffic private/referrers.json --json
```

It keeps exact UTM matches, domain-level referrers and unattributed rows apart and lists coverage gaps. GitHub keeps only 14 days of traffic, so save the traffic JSON at least weekly.

**HUMAN GATE:** read `coverage_gaps` before reading any total. An attributed session shows a tagged link was used, not that the post caused the visit.

## 17. Read the experiment and decide the next one

```bash
reachweaver experiment-verify private/release-hook.plan.json --hash sha256:<plan_hash>
reachweaver experiment-evaluate --file private/release-hook.experiment.json
```

`experiment-verify` exits 1 if the plan changed after registration. The readout is matched-age and non-causal; `growth.classify_result` maps it to `insufficient_data`, `inconclusive` or `directional` ([GROWTH-EXPERIMENTS.md](GROWTH-EXPERIMENTS.md)). In a field pilot, also add the campaign's rows to the operations log and run `pilot-report` ([FIELD-PILOT-PROTOCOL.md](FIELD-PILOT-PROTOCOL.md)).

**HUMAN GATE:** keep negative results, write down what you will change, and preregister the next test before posting again.

## What this playbook never does

It never publishes without per-post approval and the live opt-in, never retries an uncertain send, never invents a metric, never treats an HTTP 200 or a scheduler acknowledgement as a published post, and never uses a plan, a calendar or a Claude Code instruction as consent.
