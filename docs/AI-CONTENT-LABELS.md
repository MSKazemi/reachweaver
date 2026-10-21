# AI content labels: which label to apply, per platform

**Status: `ai-label-check` released in 0.14.0 (advisory); the approval precondition for posts with media is unreleased.** Platforms and lawmakers now ask people to say when what they post was made with AI. The rules differ by platform, change often, and mostly concern **realistic** images, video and audio rather than text. `ai-label-check` reads one draft and tells you, for each platform you target, whether the platform's own rule asks for a label, what to tick in the platform's own composer, and which page says so.

The command itself never blocks, approves, schedules or sends anything. Since the owner-approved change of 2026-10-21, **`approve` of a post with images or a video asks for your answer** when the decision for the post's platform is `required` or `unknown`; see [When approval asks for an answer](#when-approval-asks-for-an-answer). Text-only posts are never blocked.

> **Not legal advice. Rules change: re-verify.** Every rule below was read on **2026-10-21** from the page linked next to it. Before you rely on an entry, open that page again. The EU AI Act wording comes from a **secondary** site (artificialintelligenceact.eu), not from the Official Journal. Whether a law applies to you is a legal question for you or a qualified lawyer.

## Run it

```bash
# A campaign or draft file: authorship is read from the file, or set it yourself
reachweaver ai-label-check --file examples/campaign.reachweaver.json --authorship ai_generated

# Several platforms, with the media manifest that says which asset is realistic AI-made media
reachweaver ai-label-check --file draft.json --platform youtube,instagram,tiktok --media media-manifest.json

# A stored post (database opened read-only): uses what you recorded with ai-declare; --authorship overrides it once
reachweaver ai-label-check --post-id POST_ID --authorship ai_assisted --platform linkedin

# Also check the EU AI Act Article 50 duty (you publish from, or for an audience in, the EU)
reachweaver ai-label-check --file draft.json --eu --public-interest yes --editorial-review no --json
```

Inputs:

- `--file` takes a draft (`authorship`, `platform` or `platforms`), a variant (`platform`) or a campaign (`variants`, each with a `platform` and optionally its own `authorship`). `--post-id` reads one stored post instead.
- `--authorship human|ai_assisted|ai_generated` is the draft schema's `authorship` field ([SCHEMAS.md](SCHEMAS.md)). For a stored post, the authorship you recorded with `ai-declare` is used; without either, it is checked as `unknown`.
- `--media` reads a media manifest ([MEDIA-ASSETS.md](MEDIA-ASSETS.md)). Two fields decide the answer: `provenance.created_by` (`owner`, `tool`, `third_party`) and the optional `realistic_synthetic` flag: `true` when the image or video was made or meaningfully altered with AI **and looks real**. An asset with `targets` counts only for those platforms. Without `--media`, a stored post's images and video are read from the database together with the `realistic_synthetic` answers you recorded with `ai-declare`; media with no answer and no `created_by owner` comes back `unknown`.
- `--eu`, `--public-interest yes|no` and `--editorial-review yes|no` add the EU AI Act Article 50 check below.

Each target gets one of four answers: **required** (the cited rule asks you to disclose or label it), **recommended** (the rule labels or asks about it but does not clearly require it here), **not required** (the cited rules do not ask for a label in this case), or **unknown** (a fact is missing, or the platform's rule was not verified). The output also names the native control to tick, the API field where ReachWeaver already knows one (it sends nothing here), whether the platform reads C2PA Content Credentials, and the consequence the platform states. `--json` prints the same as JSON. The command exits 0 after a check and 2 on an input error.

## Platform rules (checked 2026-10-21)

| Platform | When a label is required | What to tick | Reads C2PA | Stated consequence | Source |
|---|---|---|---|---|---|
| YouTube | Realistic content made or meaningfully altered with AI (a real person saying or doing something they did not, altered footage of a real event or place, a realistic scene that did not happen, AI music as the main focus). AI help with a script, title or thumbnail needs no disclosure. | YouTube Studio upload > Attributes > "AI use": Yes. API: `status.containsSyntheticMedia` | Yes | Manual label, removal, or suspension from the YouTube Partner Program for creators who consistently do not disclose | [help](https://support.google.com/youtube/answer/14328491?hl=en), [API](https://developers.google.com/youtube/v3/docs/videos) |
| Instagram, Facebook, Threads (Meta) | Organic posts with photorealistic video or realistic-sounding audio that was digitally created or altered. Realistic images: recommended, Meta labels them "AI info" when it detects C2PA or IPTC indicators. | Meta's AI disclosure / label tool in the composer (exact setting name not verified) | Yes (C2PA and IPTC) | Meta "may apply penalties" | [Feb 2024](https://about.fb.com/news/2024/02/labeling-ai-generated-images-on-facebook-instagram-and-threads/), [Apr 2024](https://about.fb.com/news/2024/04/metas-approach-to-labeling-ai-generated-content-and-manipulated-media/) |
| TikTok | AI-generated or significantly edited content that shows realistic-looking scenes or people (Community Guidelines effective 2026-09-24). | The creator AI-generated content label, or a sticker or caption (exact in-app name not verified). API: `post_info.is_aigc` | Yes | Unlabeled content may be removed, restricted or labelled by TikTok | [guidelines](https://www.tiktok.com/community-guidelines/en/integrity-authenticity), [label tool](https://newsroom.tiktok.com/en-us/new-labels-for-disclosing-ai-generated-content), [C2PA](https://newsroom.tiktok.com/en-us/partnering-with-our-industry-to-advance-ai-transparency-and-literacy), [API](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) |
| LinkedIn | Synthetic or manipulated media that depicts a person saying or doing something they did not must clearly disclose that it is fake or altered. Other realistic AI-made media: recommended. | Say it in the post; where offered (select countries), self-attest on upload that the media was created using AI | Yes | Not stated on the cited pages | [policy](https://www.linkedin.com/legal/professional-community-policies), [Content Credentials](https://www.linkedin.com/help/linkedin/answer/a6282984) |
| X | **Unknown.** The policy page returned a browser challenge to a plain request on 2026-10-21, so nothing about X is asserted. | Check X's policy yourself | Unknown | Unknown | [manipulated media policy](https://help.x.com/en/rules-and-policies/manipulated-media) (not read) |
| Chinese platforms (Weibo, Xiaohongshu, Douyin, Bilibili, WeChat and the others in [CHINA.md](CHINA.md)) | Anyone who publishes AI-generated or synthesized content must declare it and use the platform's labelling function (CAC measures, Art. 10, in force since 2025-09-01). | The composer's own AI declaration (for example Xiaohongshu 笔记含AI合成内容) | n/a | See [CHINA.md](CHINA.md) | `reachweaver/china.py` (source cited there) |

For every other platform the answer is **unknown**: no rule is recorded, so check the platform's own pages.

AI-written **text** alone needs no platform label on any verified platform above: the cited rules are about realistic images, video and audio, and YouTube lists AI help with a script or title as production assistance that needs no disclosure. China's rule and the EU rule below are different.

## EU AI Act, Article 50 (secondary source, not legal advice)

The AI Act "shall apply from 2 August 2026" ([Art. 113](https://artificialintelligenceact.eu/article/113/)). [Article 50(4)](https://artificialintelligenceact.eu/article/50/) puts two transparency duties on **deployers** of an AI system:

- **Deep fakes.** A deployer of a system that generates or manipulates image, audio or video content constituting a deep fake "shall disclose that the content has been artificially generated or manipulated". A deep fake is content that "resembles existing persons, objects, places, entities or events and would falsely appear to a person to be authentic or truthful" (Art. 3(60)). For an evidently artistic, creative, satirical or fictional work, the duty is limited to disclosing that such content exists, in a way that does not hamper the work.
- **Text on matters of public interest.** A deployer of a system "that generates or manipulates text which is published with the purpose of informing the public on matters of public interest shall disclose that the text has been artificially generated or manipulated". This does not apply "where the AI-generated content has undergone a process of human review or editorial control and where a natural or legal person holds editorial responsibility for the publication of the content."

The disclosure must be given "in a clear and distinguishable manner at the latest at the time of the first interaction or exposure" (Art. 50(5)). "Deployer" excludes use "in the course of a personal non-professional activity" (Art. 3(4)). Penalties are not summarised here.

With `--eu`, `ai-label-check` reports: realistic synthetic media is **required**; AI-generated text on a matter of public interest is **required** unless you answer `--editorial-review yes` (then **not required**, the exemption); `--public-interest no` makes the text duty **not required**; a missing answer, or `ai_assisted` text without review, is **unknown**. The tool cannot decide whether you are a deployer, whether a post is "public interest" or whether your review counts as editorial control.

## When approval asks for an answer

Owner-approved on 2026-10-21 after a security review (decisions D1-D4 and D6; D5, filling TikTok `is_aigc` or YouTube `containsSyntheticMedia` from your answer, is **not** part of it).

**Which posts.** Only posts that carry **images or a video**. `approve` computes the same platform decision `ai-label-check --post-id` prints, for the post's own platform, from what is stored on the post: your `authorship`, your `realistic_synthetic` answer per attachment and the image's recorded `provenance.created_by`. The EU Art. 50 duty is left out (D4: advisory only). When the decision is **required** or **unknown** (D2), approval is refused with `ai_label_unanswered` until you answer. A text-only post is never blocked: `check` and `show` print the advice and nothing else changes.

**How you answer.** Two ways, both recorded by you and bound into the approval:

```bash
# 1. Record what is true about the post; this may settle it (for example a non-realistic image on Instagram)
reachweaver ai-declare POST_ID --realistic-synthetic no            # every attachment; --attachment N for one
reachweaver ai-declare POST_ID --authorship ai_assisted            # who wrote the text (human|ai_assisted|ai_generated)
reachweaver check POST_ID                                           # prints "AI label (PLATFORM): DECISION; ..."

# 2. Answer the label question itself when you approve (owner, in your own terminal)
reachweaver approve POST_ID --ai-label applied      # you label it yourself: platform composer or the post text
reachweaver approve POST_ID --ai-label not_needed   # you decided no label is needed
```

`applied` records that **you** label the post. ReachWeaver sets no platform label field in this change: for a manual post, tick the control named under "Tick:"; for a disabled video adapter, the TikTok record's own `--aigc` option is separate and unchanged.

Some decisions can only be settled with `--ai-label`: realistic video on Instagram, Facebook, Threads, YouTube or TikTok is **required**, and a platform with no recorded rule (Mastodon, for example, or X, whose page was not read) is always **unknown**.

**What is bound.** For a post with media, the approval hash binds `authorship`, `realistic_synthetic_json` and `approved_ai_label` under the add-on tag `reachweaver.storage.approval/media-3` (key `ai_label_binding`, next to the media-1, media-2 or video-1 tag) whenever one of them is set. Changing any of them after approval refuses the send with `approval_hash_mismatch_reapproval_required`. Declarations can only change on a draft. The `approve` audit event records `ai_label_decision=DECISION ai_label=ANSWER|none`. The registry file is **not** bound: updating it never silently invalidates an approval.

**At send time.** `publish` (dry run and `--live`), `schedule`, `health` and the claim re-run the same decision on the bound values and the bundled registry (no fetch, no network). A media post that needs an answer and has none bound is refused with `ai_label_unanswered` and stays approved or scheduled. This happens to a media post approved **before** the upgrade (no grandfathering), and to one whose decision became required or unknown because the bundled rules changed in an update. Re-approve it: `reachweaver unapprove POST_ID`, `reachweaver show POST_ID`, then approve with `--ai-label`.

**Registry age.** `health` adds `ai_disclosure_registry_aging` (attention, never blocks) when the oldest checked date in `ai_disclosure_registry.json` is more than 90 days old, and `ai_disclosure_registry_invalid` when the file cannot be loaded; in that case every media post needs an explicit `--ai-label`.

**Limits.** Every input is your declaration: ReachWeaver cannot see how a file was made. A bound answer proves that you answered, not that the answer or the label was right; the platform may still label or act on the post itself.

## What it is not

- **Not a label setter.** The approval precondition above makes you answer for posts with media; it does not set any label on the platform, and `ai-label-check` itself still blocks nothing.
- **Not an AI detector.** ReachWeaver cannot see how a file or text was made. It uses what you record: `authorship`, `provenance.created_by` and `realistic_synthetic`.
- **Not a guarantee.** A platform may label your post itself (from C2PA metadata or its own classifiers) whatever you tick.

## Keeping it current

The rules live in `reachweaver/ai_disclosure_registry.json`: each entry cites a URL, the exact quote read there and the date. `tests/test_ai_labels.py` checks the file's structure (every decision cites a page that was actually read, EU sources are marked secondary, an unverified platform can only answer unknown). It cannot check that a page still says the same thing: re-read each source, update the quote and `checked_on`, then run `python3 scripts/sync_skill.py` and the tests.
