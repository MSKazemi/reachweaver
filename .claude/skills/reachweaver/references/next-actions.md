# Decide what to improve next: ranked evidence, not social algorithm guesses

Load this reference for requests like "What is my next step to grow?", "Combine profile and post audits", "Which social account needs attention?", "Why am I receiving few impressions?", "Do a weekly optimization review", and "Unify my SEO/social/engagement findings".

**Use the existing `suggest` command rather than manually inventing platform weights.** It is a deterministic *review triage* over inputs already verified by the owner, not an AI-trained system, a forecast or authorization to post.

## Input decision tree

- **User has a profile snapshot or native post metrics:** run `reachweaver suggest --snapshot state/owned-profiles.json --json`, then read [profile-post-audit.md](profile-post-audit.md) for the exact network. Without owner data, do **not** claim any actual account was audited.
- **An owner already exported comments:** add `--engagement-export state/engagement-export.json`. Only counts/label codes are emitted; do not quote private messages or use AI classification as proof of intent.
- **SEO already produced destination briefs:** add `--seo-briefs state/seo-briefs.json`. It tests canonical/keyword conflicts only, not indexing status or Google rank.
- **There is a preregistered experiment results list:** add `--experiments state/experiment-results.json`. The result is a hypothesis for another controlled test, not a proof of optimal content or timing.
- **There is an existing ReachWeaver SQLite outbox with prior user-authorized native metrics:** opt in to local query-only evaluation with `reachweaver --db state/reachweaver.sqlite suggest --include-db --json`. By default `suggest` does **not** open the database; a missing file fails. No network APIs or provider credentials are needed.
- **Nothing was supplied:** explain that you cannot infer the user's profile reach or create fake impressions; ask for a public URL to discuss public profile fields or for the owner's sanitized native export. Offer the example under `assets/profile-post-audit.example.json` as a demonstration only.

## Interpret the ranked output

The exact output keys are `schema_version=reachweaver.suggestions.v1`, `source_coverage`, `actions` and `limitations`. Each action has `rank`, `priority` (P0 safety/privacy, P1 blockers, P2 known improvement/data collection, P3 test hypothesis), `evidence`, `confidence`, `platform/account_id`, and `next_step`.

**P0 always outranks perceived growth potential.** An ambiguous remote send cannot be retried without checking the native platform. Explicit opt-out requests are a human privacy review priority, never permission to message. A missing field has to be reported by the owner as **false** before recommending it as a fix; unknown fields reduce coverage, they do not reduce rank. SEO export conflicts remain recommendations for manual investigation.

Follow up after a human fixes one or two verified issues. Compare source-native observations at consistent post ages and, for causal claims, preregister a controlled test. Do not promise more views/likes or impersonate a real account.

## Claude Code example

```bash
python3 ~/.claude/skills/reachweaver/scripts/run.py suggest \
  --snapshot state/owned-profiles.json \
  --seo-briefs state/seo-briefs.json \
  --limit 10 --json
```

This outputs a **plan**, not drafts. It never changes a profile, approves or schedules a post, writes to the SQLite DB, fetches analytics, follows users, sends DMs, replies, or manipulates votes. The full repository explains provenance and limitations at `docs/NEXT-ACTIONS.md`.
