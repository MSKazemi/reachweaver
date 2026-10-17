# Editorial planning: audience jobs, content pillars, real creator capacity

Read this when the owner asks for repeatable weekly post ideas, an editorial series, content pillars, an audience-aware plan, or a way to post useful content without overworking.

**No command does this.** There is no `editorial` subcommand; no ReachWeaver command reads or validates the worksheet. Claude does the allocation in conversation and hands each chosen idea to the existing commands below. Never tell the owner a planner command exists.

**Existing inputs first.** Load the owner's brand card and known source evidence from the ReachWeaver workspace; do not re-interview for facts already recorded. Establish the audience's concrete job and pain, **one goal** (a campaign `objective`: `awareness`, `qualified_visits`, `contributors`, `discussion` or `signups`) and each idea's source status. An inferred persona, an audience quote nobody observed or an unverified growth claim is labeled a hypothesis.

**Worksheet.** `assets/editorial-plan.example.json` (full checkout: `examples/editorial-plan.example.json`) is a **synthetic** seed. Copy it into the owner's private workspace folder; keep real audience data out of Git, issues and external services, and never put tokens or confidential material in it.

**Allocate by hand.** Per week and channel, fill `weekly_slots` with the highest owner-set `priority` ideas for that channel until `weekly_budget_minutes` is spent. Keep `reactive_slots` empty for timely news. Report unfilled slots and leftover ideas; never drop them silently. `week_start` is the Monday of the editorial week, not a publish time. When time is short, plan fewer posts. The output is a list of **draft briefs**, not post copy, not a schedule and not approval. `owner_checked_public_source` is the owner's assertion: still check primary facts before writing; `needs_source_check` is checked first.

**Brief to post.** For each chosen idea: read `references/creative-playbook.md`, `references/platform-playbooks.md` and `references/campaign-contract.md`; write one original, platform-native draft with matching proof and one truthful CTA; run `campaign-check`, `claims-check`, `campaign-preview` and `repeat-check` (and `discovery-plan` for labels); use `share` or `manual-export` where there is no adapter. Exact per-post owner approval follows `references/security.md` and `references/publishing.md`. A brief or plan never authorizes `approve`, `schedule` or `publish --live`.

**Measure.** First-party outcomes at matched post age (`post-audit`, or `experiment-preregister` then `experiment-readout`); see `references/measurement.md`. Never infer ranking algorithms or promise reach; unrecorded metrics are unknown, not zero.

**Never plan** Hacker News (human-only) or Reddit (moderator-permitted only) slots; DEV Community allows no AI-generated comments or promotional articles (`references/dev-community.md`). Check current official platform policy before any external action. Full guide (full checkout): `docs/EDITORIAL-SYSTEM.md`.

Ideas informed by the MIT-licensed `social-media-skills/skills` collection; no code, prompts or text copied.
