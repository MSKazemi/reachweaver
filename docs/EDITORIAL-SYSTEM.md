# Editorial planning: audiences, content pillars and real production time

**What this is:** a planning method and an editable worksheet, [`examples/editorial-plan.example.json`](../examples/editorial-plan.example.json). It helps you decide *which idea is worth making, for which reader, in which format, within the time you really have*, before any post is written.

**What this is not:** a command. No ReachWeaver command reads or validates the worksheet today; it is plain JSON that you and Claude Code read as context. It writes no post text, picks no posting time, connects no account and predicts no reach. Each existing command it hands over to is listed below.

## Why a separate step

ReachWeaver already turns one source into a campaign scaffold (`signal-brief`), checks campaigns and claims (`campaign-check`, `claims-check`), proposes posting slots (`calendar-plan`), suggests discovery labels (`discovery-plan`) and measures results (`post-audit`, `experiment-readout`). What those commands assume is that you already know *what to make this week*. The worksheet records that decision so it is repeatable and honest about effort.

## The worksheet

Copy the example into your private workspace folder and replace the synthetic values with your own **non-secret** observations. Keep real audience research out of Git, issues and shared prompts. The file has these parts (schema label `reachweaver.editorial-plan.v1`, informational only):

- **Brand:** name plus concrete `voice_do` and `voice_avoid` rules. Start from your existing brand card (for example `examples/brand.reachweaver.json`) rather than re-describing it.
- **One goal**, using the same values as a campaign `objective`: `awareness`, `qualified_visits`, `contributors`, `discussion` or `signups`. Decide up front which first-party metric will show it.
- **1 to 3 audiences**, each with a concrete `job`, a `pain` and an `evidence_basis`: `owner_observation`, `public_research` or `hypothesis`. A persona or audience quote you have not observed stays a `hypothesis`.
- **1 to 5 content pillars**, each tied to one audience.
- **Channels**, each with `weekly_slots`, `reactive_slots` (space you keep free for timely news; leave it empty if nothing relevant happens) and the formats you can actually produce there.
- **Ideas**, each with an angle, a **reader payoff**, an effort estimate in `minutes`, a `priority` you set, an optional CTA and an `evidence_status`: `owner_checked_public_source` (you opened the public source and record its URL and date), `needs_source_check` or `editorial_opinion`. An owner check is your own statement, not independent verification.
- **Capacity:** `week_start` (the Monday of the editorial week, not a publish time), `weeks` and `weekly_budget_minutes`.

**Allocation rule, done by hand or with Claude Code:** fill each channel's weekly slots with the highest-priority ideas for that channel until the weekly minutes run out. Write down what did not fit (unfilled slots, ideas left over, reserved reactive slots) instead of dropping it silently. If time is short, plan fewer posts; do not plan more posts than you can write well.

## From brief to post

1. **Review each brief** for accuracy, audience, language, voice and permission to use the source. Facts marked `needs_source_check` are checked before any copy is written.
2. **Write original, platform-native drafts**, one at a time, with the skill's creative and platform playbooks and your brand card. A script or storyboard is not a finished video; real media needs assets you have the rights to, plus alt text (`media-check`, `media-storyboard`).
3. **Check before review:** put the drafts in a campaign file and run `campaign-check --file CAMPAIGN --brand BRAND`, `claims-check CAMPAIGN --brand BRAND` and `campaign-preview --file CAMPAIGN --brand BRAND`, then `repeat-check` against what you already posted. `discovery-plan` can check proposed hashtags and topic labels against the post text.
4. **Choose the route:** for a network ReachWeaver cannot send to, use `share` or `manual-export` and post by hand. For a supported text adapter, the normal draft, `show`, `approve` path applies.
5. **Approval is separate and per post.** No worksheet, brief, calendar or conversation is consent to publish. The owner approves the exact text, media and account; dry run stays the default, `reachweaver pause` stops everything, and an uncertain send is reconciled by a person, never resent.
6. **Measure with first-party numbers** at comparable post ages: `post-audit` with your own exported metrics, or `experiment-preregister` before and `experiment-readout` after a planned test ([growth experiments](GROWTH-EXPERIMENTS.md)). Change one thing at a time and keep negative results. A metric you did not record is unknown, not zero.

## Limits and rules that still apply

- Offline only: no scraping, no account connection, no network request, no paid model or API call.
- No hashtag stuffing, engagement bait, mass outreach, unsolicited replies or "best hour for everyone" claims.
- Do not plan slots for **Hacker News** (human-only: a person writes and submits) or **Reddit** (only where moderators allow it; `share` gives talking points, not text). On **DEV Community**, no AI-generated comments or promotional articles ([DEV authority playbook](DEV-AUTHORITY-PLAYBOOK-2026-10-08.md)).
- A source URL starting with `https://` does not prove the source is public, accurate or yours to use.
- A format label such as `reel_script` is an editorial outline, not a claim that ReachWeaver can upload that format to the platform ([platform matrix](PLATFORM-MATRIX.md)).

## Provenance

The worksheet and this method were written for ReachWeaver. The planning ideas (a durable brand profile, audience jobs and pains, a calendar sized to real capacity, goal-first reporting) were informed by the public, MIT-licensed [social-media-skills/skills](https://github.com/social-media-skills/skills) collection, already listed in [upstream research](UPSTREAM-RESEARCH.md). No code, prompts or text were copied from it.
