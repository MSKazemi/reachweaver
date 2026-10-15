# Media assets: offline validation and storyboard scaffolds (issue #18 slices)

`reachweaver media-check` validates a **media manifest** before a human publishes images, videos or subtitle files by hand. It reads real file headers with the Python standard library and reports blocking errors and advisory warnings as text or JSON.

> **ReachWeaver does not upload, render, transcode or publish media.** No enabled adapter sends images or video to any platform: the enabled live adapters are text-only, and the mock-tested Mastodon image (#13), Instagram image (#20), YouTube (#22) and TikTok (#23) adapters (owner-approved 2026-10-09) stay disabled in the capability registry until a live receipt. `media-check` makes no network requests and never calls a generation tool or model. Image, video, caption or translation generation tools are **optional and user-configured**: you choose them, run them yourself and record them in `provenance`. No paid model or provider is required. Local tools (for example an image editor, `ffmpeg` or a local speech-to-text model) cost nothing to use with ReachWeaver. Any paid provider you choose bills you directly; ReachWeaver neither integrates with nor pays for one.

## Quickstart

```bash
reachweaver media-check media/manifest.json          # human-readable VALID / BLOCKED
reachweaver media-check media/manifest.json --json   # machine-readable report
```

The exit code is `0` when there are no errors and `2` when there are errors. Warnings never block, but each one needs a human decision.

## Manifest format (schema_version 1)

Asset paths are **relative to the manifest file** and must stay inside its directory. Absolute paths and `..` escapes are rejected.

```json
{
  "schema_version": 1,
  "default_locale": "en",
  "target_locales": ["en", "it", "fa"],
  "preserve_terms": ["ReachWeaver", "Kubernetes"],
  "assets": [
    {
      "id": "release-card",
      "path": "release-card.png",
      "kind": "image",
      "targets": ["linkedin", "bluesky"],
      "locale": "en",
      "alt_text": "Terminal output of ReachWeaver media-check reporting zero errors for a release card",
      "alt_text_localized": {"it": "Output del terminale: ReachWeaver media-check senza errori"},
      "caption": "v0.3 validates media before you post it.",
      "content_warning": null,
      "attribution": "",
      "license": "owner",
      "provenance": {"created_by": "owner", "tool": "", "source_url": ""},
      "rights_confirmed": true
    },
    {
      "id": "demo-short",
      "path": "demo.mp4",
      "kind": "video",
      "targets": ["youtube_shorts", "bluesky", "linkedin"],
      "subtitles": [
        {"path": "demo.en.vtt", "locale": "en"},
        {"path": "demo.en.srt", "locale": "en"},
        {"path": "demo.it.vtt", "locale": "it"}
      ],
      "license": "owner",
      "provenance": {"created_by": "tool", "tool": "ffmpeg 7 (local screen capture edit)", "source_url": ""},
      "rights_confirmed": true
    }
  ]
}
```

| Field | Rule |
|---|---|
| `kind` | `image`, `video` or `subtitle`. Must match the detected file header. |
| `targets` | Platform ids from the constraint table below. An unknown id produces a warning and is not checked. |
| `alt_text` | Required for images. It must be non-empty, must not be the file name or a placeholder (`image`, `image of`, `photo`, `TODO`, ...), and must fit each target's verified limit. A leading "image of ..." produces a warning. |
| `subtitles` | Videos require at least one SRT or WebVTT sidecar. `"burned_in_captions": true` downgrades a missing sidecar to a warning, because burned-in captions cannot be verified offline. |
| `content_warning` | Optional. If present, it must be a non-empty string. |
| `license` | An SPDX id (for example `CC-BY-4.0`), `owner` or `licensed`. `CC-BY*` requires `attribution`. |
| `provenance.created_by` | `owner`, `tool` or `third_party`. `tool` requires `provenance.tool` and produces a disclosure/likeness review warning. `third_party` requires a real license (not `owner`) and attribution. |
| `rights_confirmed` | Must be JSON `true`. Anything else blocks the asset. |
| `locale` / `alt_text_localized` | BCP-47-style tags. English is the default and the first locale. A declared `target_locales` entry without alt text (images) or subtitles (videos) produces a warning. A translation that drops a `preserve_terms` entry present in the source text produces a warning. |
| `subtitles[].locale` / `subtitles[].alignment` | A track whose `locale` differs from the asset's is a **translated track** and is checked cue by cue against the asset's source-locale track (below). `alignment` is `source` (default: the cues follow the source spotting) or `independent` (the owner re-timed the track on purpose; structural findings become warnings). |
| `subtitle_timing_tolerance_ms` | Optional, top level. Integer 0-5000, default 500: how far a translated cue's start or end may sit from its source cue. |

Secret-like keys (`token`, `password`, `cookie`, ...) are rejected anywhere in the manifest.

### Translated subtitle tracks

For each asset, the first well-formed subtitle track in the asset's own locale is the **source track**. Locale tags are compared case-insensitively, as BCP-47 requires ([RFC 5646 section 2.1.1](https://www.rfc-editor.org/rfc/rfc5646#section-2.1.1), accessed 2026-10-09), so `en-us` is the source track of an `en-US` asset and `IT` in `target_locales` matches an `it` track. Every well-formed track in another locale is compared with it. Tracks with errors, including an unresolved placeholder, are reported as usual and take no part. If there are translated tracks but no usable source track, a warning says they were not compared.

| Finding | Severity | Rule |
|---|---|---|
| Cue count differs | error | `has 2 cues; the source track has 3`. |
| Missing or extra cue | error | With equal counts, cues pair by position. Otherwise they pair by timing, and each unpaired source cue (`missing a cue for source cue 2 (00:00:02.000 --> 00:00:04.500)`) or translated cue (`extra cue 4 ... has no source cue within 0.5s`) is listed. |
| Timing drift | error | A paired cue whose start or end is more than the tolerance away from its source cue, with both timings and the drift. |
| Dropped preserved term | warning | A `preserve_terms` entry in the source cue that the paired cue lacks (case-sensitive, after removing `<i>`, `<c.x>`, inline timestamps and `{\an8}`-style tags). Terms absent from the source cue are not required. |
| Changed number | warning | Digit runs compared as a multiset, so `1,500`, `1.500` and Persian `۱٬۵۰۰` all match and leading zeros are ignored (`007` = `7`), while `3` -> `4` reports `missing 3; added 4`. Runs of any length are compared as text, never through `int()`, and runs longer than 20 digits are shortened in the finding. A number written in digits on one side and spelled out ("tre") on the other is reported for a person to judge. |
| Dropped or changed URL | warning | Every `http(s)://` or `www.` URL in the source cue must appear unchanged in the paired cue (trailing punctuation ignored). |
| Added URL | warning | `adds URL ... that the source cue does not contain`: a URL in the translated cue that its source cue lacks (or has fewer times). A link injected during translation is a phishing risk, so a changed URL is reported both as dropped and as added. |

Structural findings are errors because a track out of step with the speech misleads every viewer, and they are objective. `alignment: "independent"` downgrades them to warnings for that one track. Content findings are warnings, matching the alt-text preserved-term rule: a translation may adapt wording, so a person decides. At most 20 findings per severity are listed per track; the rest are counted in one more finding of the same severity. The JSON report adds `subtitle_translations` per asset: path, locale, source path, `cues`, `source_cues`, `paired`, `missing`, `extra`, `mistimed`, tolerance, alignment and the error and warning counts. Translation quality itself is not assessed.

## What is read from files

- **PNG** (IHDR), **JPEG** (SOF markers; EXIF APP1 detected), **GIF** (logical screen), **WebP** (`VP8`, `VP8L`, `VP8X`).
- **MP4/MOV** (ISO BMFF): container brand (`qt  ` means MOV), duration from `moov/mvhd`, display width and height from the video track's `tkhd` (with 90/270-degree rotation matrices applied), and top-level box order (`moov` before `mdat`, reported as `fast_start`).
- **MP4/MOV codecs, frame rate and audio** (see below): the `stsd` sample-entry fourcc of the first video and first audio track, the average frame rate from `mdhd` timescale and `stts` sample deltas, and the audio sample rate and channel count from the audio sample entry.
- File size and **extension-versus-header mismatch**. A mismatch is an error, except MP4 versus MOV, which is a warning because both are ISO BMFF.
- **SRT/WebVTT**: UTF-8 encoding, `WEBVTT` header, numeric SRT indices, well-formed timestamps, `end > start`, monotonic start times, non-empty cue text, overlap warnings, and cues running past the video's duration.

When a header cannot be parsed, the value is reported as `"unknown"` and the dependent checks are skipped with a warning. ReachWeaver never guesses a value. EXIF metadata triggers a warning because it can reveal location or device data and platforms may strip it.

### Codec, frame-rate and audio parsing

For `kind: video` the report adds `video_codec`, `video_fourcc`, `frame_rate_fps`, `variable_frame_rate`, `frame_rate_range_fps`, `audio_tracks`, `audio_codec`, `audio_fourcc`, `audio_sample_rate_hz`, `audio_channels` and `fast_start`. Every value is read from the file or reported as `"unknown"`.

| Sample entry fourcc | Reported codec |
|---|---|
| `avc1`, `avc3` | `h264` |
| `hvc1`, `hev1` | `hevc` |
| `av01` / `vp09` / `vp08` | `av1` / `vp9` / `vp8` |
| `mp4a` | `aac` only when the `esds` objectTypeIndication is an AAC type (0x40, 0x66-0x68); `mp3` for 0x69/0x6B; otherwise `unknown` |
| `Opus` / `ac-3` / `ec-3` | `opus` / `ac3` / `eac3` |
| anything else (for example encrypted `encv`) | `unknown`, with the fourcc still reported |

- **Frame rate** is `samples x timescale / total ticks` over the video track's `stts`, rounded to 3 decimals. That is an average.
- **Variable frame rate** is `true` only when `stts` holds more than one distinct sample delta. A single shorter final sample is ignored, because encoders commonly write one. `frame_rate_range_fps` gives the slowest and fastest per-sample rate. If the deltas cannot be read, it stays `unknown` and no VFR warning is raised.
- **Audio** sample rate and channel count come from the ISO/QuickTime v0 sample entry or the QuickTime v2 extended fields. The ISO `AudioSampleEntryV1` rate sentinel and its `srat` box are not parsed, so they report `unknown`.
- **`audio_tracks: 0`** is reported only when the whole `moov` was walked without a parse problem. Then the audio rules pass, because there is no audio to check.

**Guards.** The parser reads at most `MAX_MOOV_BYTES` (64 MiB) of `moov` and walks at most 10,000 top-level boxes, 200,000 boxes inside `moov`, 16 levels of container nesting and 1,000,000 `stts` entries. It stops at the first box whose declared size is smaller than its header or runs past its parent, and that covers absurd 64-bit sizes. Each guard that fires adds a `header parsing stopped early (...)` warning, and the affected values stay `unknown`. No exception escapes, and recursion never exceeds the depth guard.

For vertical video, the report adds a **safe-area note** only, because `media-check` reads no layout. To check where text and focal points sit, use the storyboard's box check (below). Preview each upload in the native app either way.

## Platform constraint table (verified 2026-10-08; `github` 2026-10-14, `devto` 2026-10-15)

Every row carries its source URL and its own verification date in `reachweaver/media.py` (`CONSTRAINTS`); the report lists every date in `constraints_verified_dates`. Where the cited documentation does not establish a value, the row is `unknown`: it is skipped with a warning, never invented. Limits change, so re-check before publishing.

| Target | Kind | Verified constraints | Unknown (skipped) | Source |
|---|---|---|---|---|
| `bluesky` | image | any detected image; ≤ 2,000,000 bytes; alt ≤ 2000 (app constant, counted in code points) | aspect, min resolution | [images lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json), [app constants](https://github.com/bluesky-social/social-app/blob/main/src/lib/constants.ts) |
| `bluesky` | video | MP4; ≤ 300,000,000 bytes; ≤ 600 s; captions must be WebVTT ≤ 20,000 bytes | aspect, min resolution, video/audio codec, frame rate | [video lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/video.json) |
| `mastodon` | image / video | documented defaults only, as **warnings**: images ≤ 16 MB, video MP4/MOV ≤ 99 MB; instance-configurable | alt limit, duration, aspect, caption files, video codec, frame rate (instance-reported `video_frame_rate_limit`; [Instance entity](https://docs.joinmastodon.org/entities/Instance/)) | [posting docs](https://docs.joinmastodon.org/user/posting/), [media API](https://docs.joinmastodon.org/methods/media/) |
| `instagram` | image | JPEG; ≤ 8 MB; aspect 4:5 to 1.91:1; width 320–1440 (warnings: rescaled); alt ≤ 1000 | none | [IG User Media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) |
| `instagram` | video (Reels) | MP4/MOV; `moov` before `mdat`; ≤ 300 MB; 3–900 s; aspect 0.01–10; width ≤ 1920; alt text not supported; video H.264 or HEVC; 23–60 fps; audio AAC, ≤ 48 kHz, 1–2 channels | caption files | same |
| `youtube_shorts` | video | ≤ 180 s; square or vertical (width/height ≤ 1). **Warnings only** (recommended encoding): H.264 video; AAC or Opus audio; 48 kHz; `moov` at the front | container, size, resolution, caption files, frame rate (any recorded rate is acceptable), channel layout | [YouTube Help](https://support.google.com/youtube/answer/15424877), [recommended upload encoding settings](https://support.google.com/youtube/answer/1722171) |
| `tiktok` | image | JPEG/WebP; ≤ 20 MB | aspect, resolution ("up to 1080p" is ambiguous), alt | [media transfer guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) |
| `tiktok` | video | MP4/MOV (WebM not detected); ≤ 4 GB; ≤ 600 s; each side 360–4096 px; video H.264, H.265, VP8 or VP9; 23–60 fps | aspect, caption files, audio codec | same |
| `linkedin` | image | JPEG/GIF/PNG; < 36,152,320 pixels; alt ≤ 4086 | file size, aspect | [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api) |
| `linkedin` | video | MP4; 75 KB–500 MB; 3–1800 s; captions SRT (one file, English only) | aspect, video/audio codec, frame rate | [Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api) |
| `github` | image (repository social preview) | PNG/JPEG/GIF; < 1 MB (decimal, conservative); width ≥ 640 and height ≥ 320. **Warning only:** about 2:1, because the docs recommend 1280 × 640 for best display | alt text (no field described) | [GitHub Docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview) |
| `devto` | image (post cover) | **Warning only:** aspect near 1000 × 420 (about 2.38:1), the guide's "best size", not a limit | file types and file size (not stated) | [DEV editor guide](https://dev.to/p/editor_guide) |

Where a unit is ambiguous (KB versus KiB, decimal versus binary GB), the stricter interpretation is used and noted in the row.

**Codec and frame-rate rules (verified 2026-10-08).** A frame rate outside a documented range is an **error** (Instagram Reels and TikTok, both 23–60 fps). The range is checked against the **average** frame rate. When variable frame rate is detected, each target with a documented range also gets a **warning** that gives the per-sample span. Neither platform's page addresses VFR, so VFR never blocks. YouTube's encoding page publishes *recommended* settings, so its codec, sample-rate and fast-start rows are warnings and never block. That page also says other frame rates are acceptable, so YouTube has no frame-rate range. LinkedIn's Videos API on Microsoft Learn states only MP4, 75 KB–500 MB and 3 s–30 min. LinkedIn's separate [Video Ads specifications](https://www.linkedin.com/help/lms/answer/a424737/video-ads-advertising-specifications?lang=en) list H.264/VP8, AAC/MPEG4 and under 30 fps, but they cover ads, not organic posts, so they are not enforced. Bluesky's lexicon and app constants name container MIME types only. Rows these sources do not establish stay `unknown` and are skipped with a warning.

## Storyboard scaffold: `media-storyboard`

`media-storyboard` turns **one campaign brief** into the files a person needs to make a vertical video and its images. The brief is the campaign file: the [campaign contract](../examples/campaign.reachweaver.json) or a `signal-brief` scaffold ([SOURCES.md](SOURCES.md)). It writes no copy, alt text or subtitle text, generates and edits no media, and calls no model, provider or network.

```bash
reachweaver media-storyboard --campaign campaign.json --out media/storyboard          # defaults below
reachweaver media-storyboard --campaign campaign.json --out media/sb2 --duration 45 \
  --video-targets youtube_shorts tiktok instagram bluesky --image-targets linkedin instagram
reachweaver media-storyboard --check media/storyboard/storyboard.json                 # after editing
reachweaver media-check media/storyboard/media-manifest.json                          # after producing media
```

Defaults: 30 s (10–180), `--aspect 9:16` (1080x1920 frame), `--video-targets youtube_shorts tiktok instagram`, `--image-targets linkedin`, `--locale en`, `--fit cover`. `--out` must be a NEW folder. It is created 0700 and every file is a new 0600 file; an existing folder is refused. The command exits 0 when the scaffold is written, and the scaffold itself is expected to be BLOCKED.

| File | Contents |
|---|---|
| `storyboard.json` | `VerticalVideoStoryboard`: campaign id and sha256, frame, scenes, evidence, safe zones, safe-area result, `video_spec`, `image_specs`, `generation` |
| `storyboard.md` | The same for a human: scene table, evidence claims in a fenced data block, safe areas, image specs, cost notes, checklist |
| `subtitles.<locale>.srt` | One cue per scene with valid, contiguous timing and placeholder text. A WebVTT copy is added when a target only accepts WebVTT (Bluesky) |
| `media-manifest.json` | Schema-1 manifest in the format above: `image-<target>` per image target and `vertical-video.mp4` with the subtitle sidecars |

**Scenes.** One `hook` (0–3 s: the visible outcome first), one `beat` per cited evidence record (at most three, each citing its `evidence_ids`) and one `cta` (the last 4 s). Each scene has `on_screen_text`, `voiceover` and `visual` placeholders, plus a `text` box and a `focal` box placed inside the combined safe area of the video targets. Boxes are fractions of the frame: `x`, `y`, `w`, `h` in 0..1.

**Blocking by design.** Alt text, captions, subtitle cues and scene copy are `{{write: ...}}` placeholders, and `rights_confirmed` is `false`. `media-check` therefore blocks the manifest until the media files exist, a person has written every placeholder in their own words and confirmed rights. Each image's alt-text placeholder quotes the target's sourced alt-text limit, or says that the cited source documents none. Alt text is never generated, because only someone who has seen the final image can describe it. `media-check` now also rejects a subtitle file that still contains a placeholder. The builder refuses to write a manifest that validates as generated, or one that fails for any reason other than missing files, placeholders or unconfirmed rights. Tests fill in a scaffold with fixture PNG and MP4 files and confirm that the existing `media-check` then passes it.

**Specs.** `image_specs.<target>` names the file, the container (LinkedIn PNG, Instagram and TikTok JPEG, Bluesky and Mastodon PNG) and a suggested canvas, and copies that target's sourced rows from the constraint table. Each canvas is labelled `within_sourced_limits` (Instagram 1080x1350, 4:5) or `unverified_convention` (LinkedIn 1200x627, Bluesky and Mastodon 1200x675, TikTok 1080x1920), because those platforms' cited documents state no aspect ratio. `video_spec` gives the frame, the duration and each video target's sourced rows.

### Crop and UI-overlay safe areas

`check_safe_area` takes the frame's aspect ratio, each target's overlay zone and a fit assumption:

- **Overlay zone.** The fractions of a full-screen 9:16 player covered by app UI (top bar, caption and username, action rail, progress bar), given as `top`, `bottom`, `left` and `right`.
- **Fit.** `cover` scales the frame to fill the player and crops the overflow evenly from both sides, which is the conservative default. `contain` letterboxes it.

The zone is mapped into frame coordinates. Any box outside the visible area is reported as `cropped`, and any box inside the visible area but under the UI as `under_ui_overlay`, with the overshoot per edge in frame fractions and pixels. **A text box outside a safe area is an error, and a focal box is a warning.** Targets with no zone data (Bluesky, Mastodon, LinkedIn) are skipped with a warning. `combined_safe_rect` is the intersection across all targets, so a single layout fits every selected platform.

Worked examples, which the golden tests also check:

- A 1:1 frame filled into a 9:16 player shows only x = 0.21875..0.78125. A text box at x = 0.05 is reported as `cropped` by 182 px.
- A 16:9 frame shows 31.6% of its width.
- In a 9:16 frame on TikTok, a text box ending at y = 0.9 sits 0.24375 (468 px) under the sourced bottom overlay (safe bottom edge 1 - 0.34375).

**Built-in zones are sourced from first-party PAID-AD specs (accessed 2026-10-09).** Each carries `status: "sourced"`, the `source` URL, `verified_date: "2026-10-09"`, `placement: "ads"`, the `source_document`, the numbers as `stated` by the source, an `evidence` quote or description and a `note`. None of these sources states an organic-post overlay, so every check also adds a warning that organic overlays may differ. Overlays vary by app version, device, language and caption length, so preview each upload in the native app. Fractions derived from pixel values are rounded up to 6 decimals, so a zone is never smaller than its source.

| Target | top | bottom | left | right | Source (paid ads) | What the source states |
|---|---|---|---|---|---|---|
| `tiktok` | 0.125 | 0.34375 | 0.111112 | 0.277778 | [TikTok Ads Manager: TikTok Auction In-Feed Ads](https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads) ("Last updated: June 2026"), its "In-Feed-Standard Version LTR" safe-zone template | The page text states no numbers; it says the safe zone depends on dimension, ad caption length and add-ons, and links the template. The template image is labelled on a 720x1280 base: 160 px top, 440 px bottom, 80 px each side, plus a 120 px action column inside the right margin over the bottom 720 px. A zone here is a rectangle, so `right` applies 80 + 120 px to the full height (stricter than the template above the column). Longer captions, anchors and Arabic (RTL) placements have their own templates. |
| `instagram` (Reels) | 0.14 | 0.35 | 0.06 | 0.06 | [Meta Ads Guide: Awareness Video Ad Specs on Instagram Reels](https://www.facebook.com/business/ads-guide/update/video/instagram-reels) | "Consider leaving at least 14% of the top, 35% of the bottom, and 6% on each side of your asset free from text, logos, ..." |
| `youtube_shorts` | 0.15 | 0.35 | 0.044445 | 0.177778 | [Google Ads Help: Use square and vertical video to engage mobile customers](https://support.google.com/google-ads/answer/9128498), section "Safe zones for vertical video ads on YouTube" | The page text states no numbers. Its "YouTube \| Vertical Video Ads Safe Zone" image (1080x1920, "Dimensions in pixels") labels 288 top, 672 bottom, 48 left and 192 right. It covers vertical video ads across in-stream, in-feed and Shorts inventory. |

To use other numbers, for example a template for longer TikTok captions or a measured organic overlay, pass `--safe-zones FILE`:

```json
{"tiktok": {"top": 0.125, "bottom": 0.40, "left": 0.111112, "right": 0.277778,
            "source": "https://<official page you opened>", "verified_date": "2026-10-09"}}
```

Each value must be a fraction from 0 up to, but not including, 0.5. Only video targets from the constraint table are accepted. An entry is labelled `owner_sourced` only when it has a public https `source` and a `YYYY-MM-DD` `verified_date`; otherwise it stays `unverified`, and every check that uses it adds a `safe zone is UNVERIFIED` warning. `--check` takes zones from the built-in table or `--safe-zones` and never from the edited `storyboard.json`, so changing zone numbers inside the file cannot make a layout pass. `--check` also reports any scene field that still contains a placeholder, and validates the subtitle timing and placeholders. It exits 2 on any error.

### Generation is opt-in, and what it costs

`generation.enabled` is always `false`. ReachWeaver does not generate or transform media, and has no generation integration to switch on.

- **Manual or local tools** cost nothing through ReachWeaver or any provider; the only costs are your hardware, electricity and time. Examples are a screen recording of the real product, an image editor, `ffmpeg` (trimming, burned-in captions, `-movflags +faststart`) and a local speech-to-text model for a first subtitle draft. Local model output still needs a human to check every word.
- **A paid provider** that you sign up for bills your own account at its current published price. ReachWeaver holds no key, has no integration and pays nothing. No prices are recorded here because they change, so read the provider's pricing page and terms before use, including who owns the output and its likeness and voice rules.

Whichever tool you use, record it in the manifest: `provenance.created_by: "tool"` and `provenance.tool: "<name and version>"`. `media-check` then asks for disclosure and likeness review.

## Mock upload adapter: `reachweaver/media_upload.py` (issue #18)

`MockMediaUploadAdapter(platform).preflight(manifest, base_dir)` (or `.preflight_file(path)`) simulates what an upload adapter would reject, for **one** platform, with no network, no credential, no database access and no upload. It reuses the `media-check` validator, keeps only assets whose `targets` include the platform (subtitle sidecars always travel with them), drops other platforms' findings, and turns each message into `{code, asset_id, message, action}`. The result carries `simulated: true`, `uploaded: false` and `accepted` (true only when there are no errors). `accepted` is not consent to publish; approval, dry-run and the send gate are untouched. It is a library API with no CLI subcommand.

| Code | Meaning |
|---|---|
| `UNKNOWN_PLATFORM`, `NO_ASSETS_FOR_PLATFORM`, `MANIFEST_INVALID` | Platform id has no verified table; no asset targets it; manifest-level problem |
| `ASSET_PATH_INVALID`, `FILE_HEADER_UNRECOGNIZED`, `KIND_MISMATCH` | Missing, absolute or escaping path; unreadable header; declared kind differs from header |
| `CONTAINER_NOT_ALLOWED`, `FILE_TOO_LARGE`, `FILE_TOO_SMALL`, `DURATION_OUT_OF_RANGE`, `ASPECT_RATIO_UNSUPPORTED`, `RESOLUTION_OUT_OF_RANGE` | Sourced platform limits |
| `CODEC_NOT_ALLOWED`, `FRAME_RATE_OUT_OF_RANGE`, `AUDIO_SPEC_UNSUPPORTED` | Container-metadata codec, frame-rate and audio rules |
| `ALT_TEXT_MISSING`, `ALT_TEXT_PLACEHOLDER`, `ALT_TEXT_TOO_LONG`, `ALT_TEXT_NOT_SUPPORTED` | Accessibility |
| `CAPTIONS_MISSING`, `SUBTITLE_FORMAT_UNSUPPORTED`, `SUBTITLE_TOO_LARGE` | Captions |
| `RIGHTS_NOT_CONFIRMED`, `LICENSE_INVALID`, `PROVENANCE_INVALID`, `ATTRIBUTION_REQUIRED`, `PLACEHOLDER_UNRESOLVED` | Rights fields, checked in memory only |
| `MEDIA_INVALID` | Any other validator error (fallback; read `message`) |

Codes are derived from validator message text, so a message the classifier does not recognise degrades to `MEDIA_INVALID` rather than being dropped. Subtitle-track structural errors (cue syntax, translation drift) currently fall into `MEDIA_INVALID`. Provenance and license are **not persisted** anywhere; storing them in the database is a deferred, security-reviewed item.

## Limits of this slice

- Codec, frame-rate and audio checks read container metadata only. ReachWeaver never decodes a stream, so it does not check codec profile or level, bitrate, GOP structure (Instagram asks for closed GOP), progressive versus interlaced scan, chroma subsampling, edit lists, HDR or colour profile. Only the first video and first audio track are checked, and fragmented MP4 (`moof`) timing is not read. No crop-preview rendering, no thumbnail checks and no OCR of burned-in text. `media-storyboard` checks the boxes you declare, not the pixels: it cannot see where text actually lands in a rendered file. Its built-in safe zones come from paid-ad specs; organic overlays may differ.
- No automatic fact-check of captions, and no detection of likenesses, copyrighted footage or sensitive content in pixels. `rights_confirmed` and `provenance` are owner declarations that a human must stand behind.
- Translation quality is not assessed. Checks flag missing locales, dropped preserved terms, and, for translated subtitle tracks, cue count and timing drift (errors) and dropped terms, changed numbers, and dropped or added URLs per cue (warnings).
- Tests build tiny PNG, JPEG, GIF, WebP and MP4 headers in temporary directories, so no binaries are committed. The MP4 fixtures include `stsd`/`mdhd`/`stts` and audio sample entries (H.264 and HEVC at 30 fps with AAC, malformed sizes, 5,000-level nesting, `moov` after `mdat`). The WebP and MP4 fixtures are structurally valid headers, not decodable media.
