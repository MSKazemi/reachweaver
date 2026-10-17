# Creator Amplification — offline MVP

**Added:** 2026-10-17. **Status:** experimental, not yet in a released package. No scraping, network APIs, unsolicited invitations, publishing, or automatic claims of virality.

## Goal

Help someone prepare an original, evidence-backed post so that YouTubers, researchers, technical educators, journalists or niche creators can independently critique, reproduce, remix, explain or cite it. A creator pickup can introduce a new audience, but it never guarantees views, endorsements, feed boosts or causal growth. ReachWeaver never promises hidden-platform algorithm knowledge.

The deterministic Python runtime consumes manually curated local information, with no model API, subscription or social login. Claude Code can help the owner think through a creator's independent story angle, but never contacts someone.

## Quickstart

From a ReachWeaver checkout or install (`python3 -m reachweaver ...` works the same from a source checkout):

~~~bash
reachweaver creator-match --file examples/creator-candidates.example.json
reachweaver creator-match --file examples/creator-candidates.example.json --json

reachweaver creator-kit --file examples/creator-source.example.json --out ./creator-kit-review

reachweaver creator-coverage --file examples/creator-coverage.example.json
reachweaver creator-coverage --file examples/creator-coverage.example.json --json
~~~

Standalone Claude Code skill (from the owner's project workspace):

~~~bash
python3 ~/.claude/skills/reachweaver/scripts/run.py creator-match --file ~/.claude/skills/reachweaver/assets/creator-candidates.example.json
python3 ~/.claude/skills/reachweaver/scripts/run.py creator-kit --file ~/.claude/skills/reachweaver/assets/creator-source.example.json --out ./creator-kit-review
python3 ~/.claude/skills/reachweaver/scripts/run.py creator-coverage --file ~/.claude/skills/reachweaver/assets/creator-coverage.example.json
~~~

The example handles, posts and pickup URLs are synthetic placeholders, not discovered creators or first-party analytics.

Each command reads one regular local JSON file of at most 512 KB (no symlinks, UTF-8, no duplicate keys). A bad file exits `2` with one `ERROR: Creator amplification: ...` line that names the field at fault.

## Creator Match (ranked owner-curated shortlist)

Input schema: reachweaver.creator-candidates.v1. Maximum 200 manually supplied professional/public creators. Each entry identifies its platform, profile, topics, reviewed-on date, recent public URLs, audience benefit, a specific independent remix idea and owner-entered subjective ratings.

| Editorial dimension | Weight |
|---|---:|
| Topical overlap (case-insensitive topic labels) | 30% |
| Audience usefulness (owner-entered 0–4) | 20% |
| Independent remix potential (owner-entered 0–4) | 20% |
| Relevant conversation (optional owner rating with public evidence URL) | 15% |
| Known existing relationship (optional owner assertion) | 10% |
| Audience-size tier (optional owner assertion) | 5% |

Missing dimensions remain unknown, not zero. Numeric scores are withheld if less than 70% scoring coverage is known, no current public work URL exists, topics do not overlap, the review is older than 180 days, human review is incomplete, or the creator explicitly declined or opted out. An unrelated large account never outranks a relevant micro-creator merely because of its follower tier. Each entry reports its matched topics, weight contributions, scoring coverage, rationale and blockers.

**This is not an influencer response probability or a reach forecast, and a high score never grants permission to contact anybody.** It makes no online lookups or writes.

Every row carries `contact_rules`. Creators who opted out or declined get "Do not contact". Every other row says that ReachWeaver contacts nobody: if you reach out, you do it yourself, one person at a time, only through a channel the creator publishes for such requests, never making coverage a condition and disclosing any payment, gift, free access or other connection. Reddit rows add the human-only rule: follow the community's self-promotion rules and its moderators, and never send unsolicited private messages. Hacker News is not an accepted platform. The report also lists `disclosure_reminders`.

## Creator Kit (source-first and rights-first)

Input schema: reachweaver.creator-source.v1. The owner supplies a purportedly public original URL, a summary, 1–6 claims with evidence URLs and explicit limitations, topics, creator-facing questions, and a reuse statement.

Output: NEW private directory with source-kit.json and CREATOR-REVIEW.md. On POSIX, directory mode 0700 and individual file mode 0600, each file created new and never through a symlink. An existing output path is refused, never overwritten, and a failed write removes the partly written folder. Input symlinks and invalid/unexpected fields are refused. The kit and its review page carry a "Consent and disclosure" section: coverage is the creator's independent choice, and any connection or value given must be disclosed plainly, with the platform's paid-partnership label where one exists.

Media cannot be included without explicit operator attestation of ownership/reuse rights for **each** asset, with terms. Even that is not independently verified by the CLI. When no asset rights are supplied the package instructs creators to cite sources but not reuse clips or images. No content is automatically transmitted.

Before sharing a kit, a human must check that the source is genuinely public and that evidence, attribution, permission, platform reuse settings, sponsorship disclosures, and brand claims are accurate. This command cannot legally clear third-party content or verify a source URL.

## Creator Coverage (manual earned-pickup accounting)

Input schema: reachweaver.creator-coverage.v1, with original link and publication time plus up to 500 owner-entered derivative observations. For each: local creator slug, public URL, platform, type of independent work, time, independent-contribution flag, and unverified/owner_confirmed status.

Counts only owner-confirmed, independently contributed derivatives; keeps unverified or non-independent observations out of earned-pickup totals. Summarizes counts at 48h/7d (items and unique creator IDs), distinct creators overall, and type/platform mix. Duplicate derivative URLs or a derivative observed before publication are refused. It never combines incompatible native platform view definitions or claims one creator caused another creator's views.

**Owner-confirmed is only a user assertion, not a remote verification performed by ReachWeaver.** The application makes no network calls, fetches no comments and infers no viral chain.

## Privacy, responsible outreach and retention

**The creator list you enter is your data and your responsibility** (see [DISCLAIMER.md section 3](../DISCLAIMER.md#3-responsibility-for-content-and-third-party-rules)). Keep only public information, and delete the list and its reports when the campaign ends. ReachWeaver keeps it only on your computer and never sends it anywhere.

A public professional handle may still be **personal data**. This feature never ships a real-person dataset, private messages, emails, phone numbers, creator contact lists or raw third-party transcripts. Operators must have an appropriate lawful basis, minimize data, respect local marketing laws and each platform's rules, honor opt-outs, and delete local files when no longer needed. Local reports can include the operator-entered public handles: keep outputs private and never commit real creator information.

No automatic unsolicited DMs, mass mentions, scripted engagement rings, fake or coordinated likes, account scraping, captcha bypasses, credential harvesting, marketing automation or synthetic influencer endorsements. Candidate matching, kit preparation and coverage measurement are **offline-only**.

## Work with ReachWeaver's existing tools

1. Use verified, owned and publishable sources in an ordinary campaign.
2. Make a creator-friendly source kit; verify every claim and the rights.
3. Curate a few relevant public professional creators, review their work manually and rank them.
4. Decide whether a creator has a genuinely useful original angle. If there is an appropriate channel and legal basis for an invitation, the human writes and sends it independently of ReachWeaver.
5. Log genuinely observed independent derivative URLs and report at day 2 and day 7.
6. Track qualified first-party downstream actions with ReachWeaver's attribution and growth experiments, clearly labeling unknowns and confounders.

## Remaining gates / roadmap

This is an initial feature slice, **not** an API-powered creator search engine, CRM, ongoing mention monitor or automated amplification agent. Future features require independent owner approval and platform/privacy/rights review. A bounded next step is a local dashboard, clearer source-kit templates, and transparent retention/deletion tools.

Before any public mirror, merge or release: run the full Python unit test matrix, no-network and secret scans, skill/plugin mirror check, rights/provenance gate, privacy review, and private-to-public file boundary audit. A passing local unit test does not certify security, legality, or commercial readiness.

The examples use synthetic data only; no influencer list ships with ReachWeaver.
