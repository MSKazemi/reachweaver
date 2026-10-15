# Platform-specific social profile and post-performance audits

**Trigger:** audit or improve a particular social profile; why my posts receive few views/likes/impressions; optimize profile bio, headline, avatar, banner, pinned content, website link, video retention, Shorts, carousels, or DEV articles; compare publication quality by social platform.

Do not give one generic "algorithm score" to all networks. In a complete ReachWeaver checkout, read [`docs/PROFILE-AND-POST-AUDITS.md`](../../../../docs/PROFILE-AND-POST-AUDITS.md); in a standalone copied skill, use this reference and its bundled registry at `scripts/reachweaver/profile_audit_registry.json`.

## Exact steps

1. **Identify the network and owned account.** If the person supplies a public profile URL, review only genuinely visible fields; private analytics require an owner-provided export or an explicitly authorized read-only connection. If there is no account information, explain the method and use an **illustrative** example only. No login bypass, scraping, cookies, stored tokens or assumptions about account eligibility.
2. **Load that network's profile specification** from `scripts/reachweaver/profile_audit_registry.json`. Interpret each check as an *editorial recommendation*, not a platform ranking weight. Only call an exact bio limit officially verified when the registry says so. Mastodon default limits vary by server, LinkedIn personal vs Page differs, and many eligible features are optional or plan-gated.
3. **Inspect identity and conversion:** audience/mission in bio, credible name/visuals, source-backed expertise, platform-specific featured/pinned evidence, relevant clean HTTPS links, human-realistic CTA, appropriate topics and community guidelines. For multi-profile brands, flag name/handle inconsistencies **for review**; individual accounts do not have to adopt a company name. Unknown fields mean *not observed* and reduce coverage, never zero performance.
4. **Run the deterministic offline profile check** on a user-created JSON snapshot (or a profile `.csv`, see `docs/PROFILE-AND-POST-AUDITS.md`). The output also carries an advisory cross-platform name/handle/link/bio consistency block. Do not pass a GitHub URL instead of a local snapshot; this command has **no network capability**.
   
   ```bash
   reachweaver profile-audit --file state/owned-profiles.json --platform linkedin --json
   # A copied global skill: python ~/.claude/skills/reachweaver/scripts/run.py profile-audit --file state/owned-profiles.json --json
   ```

   An observed completeness heuristic is reported only with >=60% of the platform's requested fields actually checked. It is **not** a 0–100 score from LinkedIn, Instagram, Google or another social algorithm. Read `fixes` and `alerts`, not just the score.
5. **Audit the post separately.** Classify original format, verified story, meaningful first-line or first-frame proof, proper licensing, accessibility and relevant action. For exported native metrics use per-post ISO timestamps and explicitly source-labeled views/reach/impressions and interactions:

   ```bash
   reachweaver post-audit --file state/owned-profiles.json --platform linkedin --age-hours 168 --tolerance-hours 24 --min-posts 3 --json
   ```

   The readout is descriptive only; a format cohort must have at least 3 comparable-age records within the *same platform, account, format, metric definition and exact source*. Missing snapshots mean **unknown**. Do not use unavailable impressions as zero or rank different platforms by raw rates.
6. **Recommend 1–3 concrete fixes per network**, and write one or two original content hypotheses per relevant audience. Review edits with the owner; update profile or publish **only** through a separate explicit authorized workflow. The audit itself must never call a publishing adapter, scheduler, likes, follows, comments or unsolicited DMs.
7. **Recheck with native account insights** after +24 h, +7 d and possibly +28 d. Preserve observation timestamps, title/thumbnail, content format, topic, paid/organic distinction, metric definitions and account changes. A higher result is correlation, not causal proof.

## The networks are different

| Platform | Profile focus | Native post experiment; never a guarantee |
| --- | --- | --- |
| LinkedIn | Headline, actual technical achievements, About, Featured, professional CTA | Evidence-backed technical decision, short demo, authored document; qualified discussions/clicks |
| YouTube | Channel trailer/sections, banner, searchable About | Accurate searchable title/thumbnail and retention of real demo; distinguish Shorts vs long-form |
| Instagram | Bio, accessible visuals, highlights, authentic original Reels | First-frame result and useful swipeable carousel; native reach, saves, shares |
| TikTok | Clear creator niche, original demos, honest profile CTA | Original immediate payoff and relevant caption, inspect watch-through and skips |
| X | 160-character bio, header, website, pinned proof | Concise real data/source and meaningful discussion; view/click reporting |
| Bluesky | Domain handle where eligible, targeted custom-feed participation | Useful open-source snippet, authentic feed conversation; custom feeds vary |
| Mastodon | Server-defined bio, profile metadata, rel=me verification, directory opt-in | Hashtags/alt text and relevant fediverse technical help; no global impressions promise |
| DEV.to / Hashnode | Accurate author expertise, profile links, original tutorials | Reproducible code tutorial, relevant tags/search, canonical care |
| Reddit / HN | Community history, actual rules; HN human text only | Genuinely useful human-authored community involvement; NEVER vote or submission bots |
| Product Hunt | Honest maker identity, useful gallery, functioning demo | Original launch narrative, available Q&A; never solicit votes |
| Pinterest | Claimed owned domain, topical boards | Original diagram with natural search language and useful landing page |
| Facebook / Threads | Useful social identity and non-promotional conversation | Original demonstrations; Meta original-content rules and native account insights |
| Telegram / Discord / WhatsApp | Consent, rules and onboarding rather than follower counts | Real opt-in community information; never mass-send unsolicited material |
| Indie Hackers / Peerlist | Credible founder/project history and proof | Substantive build story and measured lessons, not automated self-promotion |
| Bilibili / Zhihu / OSCHINA | Locale-appropriate technical identity and publications | Locally relevant Chinese technical demonstration/Q&A; current ranking specifics **unverified** |

## Optional first-party analytics overlay (no platform connection)

When the operator has an authorized native post-analytics export in the **documented normalized CSV format**, permit `post-audit --file OWNER_PROFILE.json --metrics-csv OWNER_METRICS.csv --owner-authorized --json`. Without both the CSV path and the explicit `--owner-authorized` acknowledgement, do not process it. This local loader joins to **predeclared account IDs only**, refuses duplicate posts, counts blank metrics as unknown (never zero), checks offset-aware timestamps and returns source-label caveats. It does **not** authenticate, fetch insights, create drafts, persist data or publish. The owner's claim of provenance is not independent verification. Use the synthetic `assets/profile-post-metrics.example.csv` as a template and `docs/PROFILE-METRICS-CSV.md` in the full repository for field definitions.

## Format of the deliverable

Return a per-platform assessment with the **evidence snapshot**, coverage, known profile strengths/fixes, unknowns, policy eligibility, category-specific native content suggestion, observational post metrics and denominator/age, confidence, a prioritized plan and the next measurement checkpoints. Do not invent private analytics, follower counts, native reach, impressions, algorithmic weights or personal brand achievements. When no account snapshot was provided, state explicitly that the user has **not yet received an audit of their real accounts**.

Hacker News is exceptional: the assistant may provide policy references and factual background but **cannot author or edit submission text**. Reddit/community publication needs human participation, moderator rules and consent. No social-media manipulation or engagement-bait gimmicks.
