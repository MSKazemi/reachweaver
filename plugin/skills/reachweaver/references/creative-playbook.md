# Creative craft: get genuine attention by earning it

Open this for writing, repurposing, scripts, calendars, hooks, translations and content audits. It is a creative process, not a list of unverifiable algorithm hacks.

## A brand-aware creative process

1. Read the right brand and audience card first. If there is a real prior sample, derive voice characteristics from it; don't impersonate a person without permission.
2. Extract one **load-bearing proof** (demo, release, source paper, benchmark, reproducible failure, validated takeaway). Mark observation vs interpretation vs projection.
3. Generate 3–5 concept candidates using distinct, honest frames: **pain/solution**, **counterintuitive proof**, **field lesson**, **useful tutorial**, **decision trade-off**, **human story**.
4. Rank candidates qualitatively by audience relevance, novelty of verifiable proof, accessibility, production cost and relevance to objective. Do **not** claim an algorithmic reach score.
5. Write 3 **truthful hooks** for the leading concept: specific result, practical tension, or helpful question. The next sentence must actually repay the promise; the first line should work before truncation. Choose based on clarity and evidence, not clickbait.
6. For each channel write a native post. Change angle, pacing, evidence presentation and CTA as needed; don't just cut the same text to a character limit.
7. Proofread in user-selected language and voice. For EN/IT/FA, adapt cultural context and terminology; preserve technical nouns, correct units, dates, and cited quantities. Never invent translations of citations, product features or legal disclosures.
8. Separate publish-ready text from **media briefs**. Deliver image prompt/description, alt text, media rights source and required dimensions only after current spec verification. A storyboard is not a rendered video.

## Voice check before the owner sees a draft

Offline and advisory; it never blocks approval or publishing (full checkout: `docs/VOICE-CHECK.md`).

1. Once, with the owner's consent: `reachweaver voice-profile --from OWN_POSTS --out NEW_FILE` (a .json, .csv or `---`-separated text file of posts the owner wrote) or `reachweaver voice-profile --from-db [--account ID] [--platform P] --out NEW_FILE` (published posts, database read-only). It needs 10+ posts (else `insufficient_sample`, exit 1), writes a NEW 0600 file and stores only statistics and short word lists. Never profile a third person's posts to imitate them.
2. For every draft, before showing it: `reachweaver voice-check --profile FILE --text "..." --platform P --json` (or `--file`, `--post-id`; without `--profile` only the tell list runs). `creative-preflight --voice-profile FILE` adds the same findings per candidate.
3. Rewrite each `warn` line yourself in the conversation: replace stock phrases (`tell.*`) with the concrete point, and move features outside the owner's range (`voice.*`) back toward their median. Read `profile_hints` for the owner's usual openings and words. Re-run until no `warn` remains or the owner prefers the flagged wording, then tell the owner what you changed. A tell shown as `info` because the owner uses it often may stay.
4. The tell list is a heuristic style list, **not an AI detector**: never tell the owner a draft "passes" or "fails" AI detection. For human-only destinations (Reddit, Hacker News) report findings only; do not rewrite or draft that text.

## Format-specific angles (verify live policies/limits)

| Channel | Native value to produce | Safe CTA | Caveat |
|---|---|---|---|
| LinkedIn | Engineering case study, lesson with proof, structured trade-off; concise opening | Ask for an informed viewpoint or a demo visit | Personal posting scope, carousel/PDF behavior and API permissions need current verification |
| Bluesky | Direct observation, short links, valuable peer conversation | Specific technical question or source link | Default text-only adapter; know rich-text facets and current grapheme rules |
| Mastodon | Context and authenticity, accessible links, community-appropriate explanation | Discussion or useful documentation | Server instance rules and cultures vary |
| Telegram | High-context release note, digest, valuable links | Documentation/release source | Bots must have authorized destination permissions |
| X | Thread with a tangible lesson, independent first post, proof per claim | Link in relevant context | API access may cost money and be restricted; manual only here |
| Threads | Human observation with coherent short narrative | Thoughtful question | Need approved provider before publishing |
| Instagram | Visual story, carousel outline, reel voice/script, captions and alt text | Appropriate profile/link/action | Professional account/media API constraints; text-only direct publishing unsupported |
| TikTok/Reels/Shorts | Hook in first seconds, visual proof, 3–5 beats, shot list, subtitles | Contextual next step | Copyright, video production and public upload app audits must be verified |
| YouTube | Title, thumbnail brief, description, source links, chapters when applicable | Visit full tutorial | Video upload/metadata API requires separate permission |
| Reddit | Explain or answer within individual community rules; no drive-by advertising | No promotional CTA by default | Human-led community participation; do not automate votes or spam |
| Pinterest | Descriptive keyword/title/vertical graphic brief linked to source | Helpful canonical link | Confirm board ownership, image rights, current media requirements |

## Short-form script contract

Return: target audience → one-sentence payoff → first-frame visual → spoken hook → 3–5 beats (each with visual evidence and on-screen text) → exact source list → silent-friendly captions → thumbnail/title brief → rights/consent → one natural CTA. Provide an honest production checklist and asset manifest. Never claim a video or thumbnail was generated unless actual output exists.

## Calendar and sustainable cadence

Prefer a two-week campaign with measured capacity over daily generic volume. Use 3–5 recurring thematic pillars, a clear source link, one deliberate audience per post, and an estimated effort. Recycle **ideas and evidence**, not identical text. Rotate educational, demonstration, build-in-public and community-help angles. Reserve time for real replies, not automated praise.

## When content underperforms

Separate **lack of distribution** from **low content retention**, from **weak conversion**. Check actual impressions/views (with definition), saves/shares, clicks, audience feedback, landing-page load and signups, all from source-specific data. Compare matched post ages; inspect losers as carefully as winners. A viral outlier does not establish a recipe.
