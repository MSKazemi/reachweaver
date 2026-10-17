# Creative and platform-native format guidance

This file encodes durable *craft*, not volatile hard limits. Verify official platform constraints for exact text, file, ratio, duration, accessibility, music and rights before publication.

| Platform | Useful first format | Creator focus | Publishing status |
|---|---|---|---|
| LinkedIn | Problem -> real engineering proof -> lesson; document/image if owned | Technical credibility, collaborators, jobs | live text supported (member profile; organization pages refused) |
| Bluesky | Precise discovery, interesting demo result and direct conversation | Researchers/devs | live text supported |
| Mastodon | Accessible technical note, context and self-contained value | OSS communities | live text supported; image adapter exists, disabled until a live receipt |
| Telegram | Helpful project updates, highlights, source link | Opt-in subscribers | live text supported |
| X | Tight insight or source-backed thread | Technical discussion | draft/manual-only |
| Threads | Conversational take + useful details | Discovery/dialogue | draft/manual-only; text adapter exists, disabled until a live receipt |
| YouTube | Demo/tutorial/short with accurate title and thumbnail | Long-lived search and demonstration | storyboard/manual-only; video upload adapter exists, disabled until a live receipt |
| TikTok | Immediate visible result, short problem/solution | Visual discovery | storyboard/manual-only; video adapter (creator inbox, direct post) exists, disabled until a live receipt |
| Instagram | Strong visual/carousel/Reel with accessibility captions | Visual reach and credibility | creative/manual-only; single-image adapter exists, disabled until a live receipt |
| Facebook | Useful group-specific answer and community context | Relevant communities | creative/manual-only; mock-tested Facebook Page adapter, disabled until a live receipt |
| Reddit | Helpful answer that obeys each subreddit's self-promotion rules | Peer trust | manual-only, no automated posting |
| Pinterest | Clear evergreen problem-solution visual | Visual search | creative/manual-only |

## Creative briefs

Text-native: audience, first-line hook, real problem, original proof/source, 1 memorable insight, one optional CTA, disclosure, accessibility text where applicable.

Video: first 2 seconds of visible outcome, 3-5 explanatory beats, screen/demo shots grounded in actual product, captions, title/thumbnail hypothesis, copyright/consent check, clear CTA. Produce shot list and voiceover script if no render tooling; never claim a real video exists without creating it.

Carousel: slide 1 promise grounded in evidence, slides 2-5 demonstration/logic with one point each, final slide actionable takeaway. Draft visual briefs only if graphics tooling unavailable.

Do not use artificial hooks such as "You won't believe" for work that does not justify them. No unsupported statistics, made-up testimonials, copyright infringement, mass cross-post spam or AI-generated fake evidence.

## Validating real media files

When actual image/video/subtitle files exist, write a media manifest (schema in `docs/MEDIA-ASSETS.md` in the full repository) and run `media-check MANIFEST --json` (repository CLI or `scripts/run.py`). It reads real headers offline (dimensions, duration, MP4/MOV codecs, average frame rate, audio sample rate and channels, fast start), applies only **sourced, dated** platform limits (unknown limits are skipped with a warning), requires image alt text and video SRT/WebVTT captions, and blocks unconfirmed rights or unattributed third-party media. Translated subtitle tracks are compared cue by cue with the source-locale track: a different cue count, a missing or extra cue or timing drift is an error, and a dropped preserved term, a changed number, or a dropped or added URL is a warning for a person to judge (an added URL may be an injected phishing link). Locale tags match case-insensitively. Never "fix" a translation by inventing text. Report its `unknown` values as unknown. It does not upload, render or generate media, and a passing report is not publishing approval. Media generation tools are optional, user-chosen and never require a paid model.

## Storyboard scaffold

Before any media exists, `media-storyboard --campaign CAMPAIGN.json --out NEW_DIR` writes a vertical-video storyboard (`storyboard.json` and `storyboard.md`), an SRT subtitle skeleton with valid timing, a `media-manifest.json` with an image draft per `--image-targets` platform (default LinkedIn) and the vertical video, and image/video specs that carry the sourced limits. Scene copy, subtitle text, captions and alt text are `{{write: ...}}` placeholders and `rights_confirmed` is `false`, so `media-check` blocks until a person writes them; never fill alt text or subtitles with invented descriptions of media you have not seen. `media-storyboard --check storyboard.json` flags text (error) or focal (warning) boxes that a centered crop or a UI overlay would hide. The built-in TikTok, Instagram Reels and YouTube Shorts safe zones come from first-party **paid-ad** creative specs (accessed 2026-10-09), not organic-post documentation; say so, tell the user to preview in the native app, and use `--safe-zones FILE` only with numbers whose source you have checked. Generation stays opt-in: local tools cost nothing through ReachWeaver, and a paid provider bills the user directly. To repurpose one existing master video, `media-cutlist PLAN.json` gives a per-platform cut list (segments that fit each sourced duration limit, crop window, subtitle format) and a suggested `ffmpeg` command for the user to run; it opens no file, so the master's duration and size come from the user.
