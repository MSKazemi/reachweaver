# Manual export for Threads, Instagram, Facebook Pages, YouTube and TikTok

**Issues #19-#23, offline slice. Verified 2026-10-08.**

These five platforms need Meta app review, Google API compliance audits or a TikTok Content Posting API audit before an app can post for a user. The owner has not completed any of them, so ReachWeaver has **no live adapter** for any of the five. `manual-export` is the supported fallback. It turns one variant (and an optional media manifest) into a creator-ready package. The account holder then posts it **themselves** in the platform's own app or website.

The package never sends, uploads, schedules or shares anything. Its `status` is always `manual_export_not_published`, and its wording never claims that a post exists. The module has no send path (`SEND_PATH = None`) and imports no network or publisher code.

## Command

```bash
reachweaver manual-export --platform youtube --variant variant.json \
  [--media media/manifest.json] [--account-meta account.json] --out exports/youtube-tour [--json]
```

- `--platform`: `threads`, `instagram`, `facebook`, `youtube` or `tiktok` for the sourced packages below, or any other `manual_only` id in the distribution registry for a [generic package](#generic-package-for-every-other-manual-only-destination).
- `--out` must be a **new** path. An existing file or directory, even an empty one, is refused. The directory is created with mode 0700, and each file with mode 0600 (`O_EXCL`, `O_NOFOLLOW`).
- If preflight finds a blocking error, the command prints `BLOCKED`, writes nothing and exits 2.
- Output files: `manual-export.json` (the full package), `CHECKLIST.md`, and the exact text. For YouTube that is `title.txt` and `description.txt`; for the others it is `caption.txt`. Text files contain the exact text, with no added newline.

From Python: `platform_preflight(platform, variant, account_meta=None) -> (errors, warnings)`, `build_export(platform, variant, media_manifest=None, account_meta=None) -> dict`, `render_markdown(package)` and `write_export(package, out_dir)`.

## Generic package for every other manual-only destination

**Private #44, item C2. Offline only.** Every other destination that [`distribution_registry.json`](../reachweaver/distribution_registry.json) marks `reachweaver_delivery: manual_only` gets a generic package from the same command, for example `--platform dev`, `x`, `product_hunt`, `reddit` or `hacker_news`. The five platforms above keep their own sourced packages, byte for byte (`tests/test_manual_package.py::test_cli_output_matches_golden_bytes`). `facebook_pages` maps to `--platform facebook`. A destination that is not `manual_only` (Bluesky, Mastodon, Telegram, LinkedIn, GitHub, Snapcraft, Gitee) is refused. The code is `reachweaver/manual_package.py`: `package_preflight(platform, variant, account_meta=None)`, `build_package(...)`, `render_markdown`, `render_notes` and `write_package(package, out_dir)`. It adds no delivery mode, no browser automation, no network access and no registry change.

The generic package knows only what the registries already record:

- **Label and help page.** The label comes from the distribution registry. The official page is the registry's `scheduling.official_source`, with its `checked_on` date, and only when the registry has one. That page may describe scheduling rather than the composer. Otherwise the package says that the registry has no official page.
- **Limits.** No registry field records a text limit for these destinations, so the text limit is always `limit unknown — check the platform`. Media rows come only from the `media.py` `CONSTRAINTS` table for the destination (DEV uses the `devto` table). A row without a value, and a destination without a table, also print `limit unknown — check the platform` (`tests/test_manual_package.py::test_known_media_rows_come_only_from_media_constraints`). The capability registry's `compliance_constraints` for the destination are listed as registry notes with their source and date, for example X's "Up to 4 photos, 1 GIF or 1 video per post". ReachWeaver measures the text (characters, UTF-16 units, UTF-8 bytes) but checks it against no limit.
- **Media.** With `--media`, the manifest is validated through `media.validate_media_manifest`, exactly as above. The package lists each asset whose `targets` include the destination id (DEV also accepts `devto`): path, alt text, caption files, detected size and the rights record from the manifest (`rights_confirmed`, `license`, `attribution`, `provenance`).
- **Checklist.** Verify claims; sign in to the intended account; open the platform yourself; paste `post.txt` (and `title.txt` if the variant has `platform_options.title`) exactly; the text-limit line; attach each asset with its alt text and rights; confirm rights, consent and disclosure; and keep the native post URL as first-party evidence. ReachWeaver has no command that records a hand-made post (`reconcile` covers only queued sends), so the package records nothing. DEV packages add DEV's rules: disclose AI assistance in the post, and no AI-generated comments ([DEV AI guidelines](https://dev.to/guidelines-for-ai-assisted-articles-on-dev/), checked 2026-10-15). Hacker News adds "do not schedule or automate" (registry scheduling `prohibited`); Reddit adds "post by hand" (`moderator_only`).
- **Output.** `manual-export.json` (kind `reachweaver.manual-package.v1`, status `manual_export_not_published`), `CHECKLIST.md`, and `post.txt` (plus `title.txt`). The same new-directory, 0700/0600 and no-overwrite rules apply, and a blocked package writes nothing.

### Human-only destinations: write it yourself

Every destination the registry marks `human_only` (Reddit, Hacker News, Discord, WhatsApp communities, Facebook Groups, Indie Hackers, Lobsters, Stack Exchange and the rest) gets **no agent-written post text**. This follows the existing rules: Hacker News text is human-written only, ReachWeaver writes no Reddit text, and engagement drafts skip DEV and HN.

- A variant `text` or `platform_options.title` for such a destination is a blocking error (`tests/test_manual_package.py::test_agent_text_or_title_is_blocked_for_every_human_only_destination`).
- The variant may carry `talking_points`: at most 10 short notes of at most 200 characters each. That cap is ReachWeaver's own, not a platform limit; it keeps notes from carrying a ready-made post.
- The package has `content.ready_to_paste: false`, no `text` field and no `post.txt`. It writes `NOTES.md` instead: a private notes form that starts with **"Write this yourself."** and has blanks for what you want to say, the community rules you checked, links, and the post URL after you post (`tests/test_manual_package.py::test_written_package_has_notes_and_no_text_file`).

```bash
reachweaver manual-export --platform hacker_news --variant notes.json --out exports/hn-notes
# notes.json: {"talking_points": ["Offline-first design", "No auto-posting"], "link_url": "https://example.org/docs"}
```

## Variant file

```json
{
  "platform": "youtube",
  "format": "video",
  "text": "Description or caption, exactly as it should appear.",
  "link_url": "https://example.org/docs",
  "platform_options": {"title": "ReachWeaver in 60 seconds", "tags": ["oss"], "shorts": false}
}
```

- `text` is the caption. For YouTube it is the description.
- `format`: Threads `text|link|image|video|carousel` (default `text`); Facebook `text|link|image|video` (default `text`); Instagram `image|video|carousel` (required); TikTok and YouTube `video` (required).
- `platform_options`: YouTube `title` (required), `tags`, `shorts`. Facebook `target_type` (`page`).
- Fields from the canonical `PlatformVariant` (`id`, `account_id`, `campaign_id`, `locale`, ...) are accepted and ignored. Unknown fields produce a warning. Secret-like keys (`token`, `password`, `session`, ...) block the export.
- **Choices the account holder must make are never taken from the variant.** `privacy_level`, `privacy_status`, `made_for_kids`, `contains_synthetic_media`, `is_aigc`, `disable_comment` and the like are ignored with a warning, and the package leaves them unselected.

Account metadata (`--account-meta`) is **public** information only: `account_id`, `handle`, `display_name`, `account_type` (`page`, `profile`, `group`, `business`, `creator`, `personal`), and `tiktok_api_audit` (`unaudited|audited|unknown`, default `unknown`). Secret-like keys block the export.

## Preflight rules

Errors block the export. Warnings need human judgment.

| Platform | Blocking errors | Warnings |
|---|---|---|
| Threads | More than 500 characters, with emoji-like code points counted by their UTF-8 bytes; more than 5 links | More than one hashtag (only one topic tag per post); a tag longer than 50 characters |
| Instagram | Caption over 2,200 characters; more than 30 hashtags; more than 20 @ tags; text-only format | A URL in the caption (do not rely on it being tappable) |
| Facebook | Target not declared, or declared as a profile, personal account, user, timeline or group; text over the conservative 4,500 cap (no official limit was verified) | — |
| YouTube | Missing title; title over 100 characters; description over 5,000 **UTF-8 bytes**; `<` or `>` in title or description; tags over 500 characters (commas count, and spaced tags count with quotes); non-video format | Empty description; description links (clickable only for eligible channels) |
| TikTok | Caption over 2,200 UTF-16 units; non-video format | A URL in the caption |
| All | Placeholder/TODO text, control characters, a non-public `link_url`, secret-like keys, platform mismatch | Text over ReachWeaver's conservative `policy.py` cap (the official limit is what applies to a manual post) |

## Media

Media requirements are **not duplicated** here. With `--media`, the manifest is validated by [`reachweaver/media.py`](MEDIA-ASSETS.md) (`validate_media_manifest`): real headers, the sourced `CONSTRAINTS` table, alt text, SRT/WebVTT captions, rights, provenance and locales. Its errors and warnings are copied into the package, prefixed `media:`. The package then lists each asset that targets the platform (`threads`, `facebook`, `instagram`, `tiktok`, or `youtube`/`youtube_shorts`), with its path, alt text, caption files and detected dimensions and duration.

- `image`, `video` and `carousel` formats need a matching asset that targets the platform. A carousel needs at least two.
- `media.py` has constraint tables for `instagram`, `tiktok` and `youtube_shorts`. For Threads, Facebook and long-form YouTube it warns that no verified table exists.
- Without `--media`, a media format only produces a warning that nothing was validated, plus a checklist step to run `media-check`.

## Decisions only the account holder makes

Each one is listed in `human_decisions` with `selected: null`, `required: true` and its source.

- **TikTok:** `privacy_level` (options `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY`; only those your account offers apply). Also `interactions` (comments, Duet, Stitch), `commercial_content` and `ai_generated_label`. Public is never pre-selected. The note records the Content Posting API audit status from account metadata (default `unknown`). The audit applies only to API clients; ReachWeaver has none for TikTok.
- **YouTube:** `privacy_status` (`private`, `unlisted`, `public`), `made_for_kids` (a legal declaration) and `altered_or_synthetic_content`.
- Threads, Instagram and Facebook have no extra platform-assigned decisions in this slice. The checklist still covers account, rights, consent and disclosure.

## Verified facts (2026-10-08)

Each fact is stored in `manual_export.FACTS` with its value, status, source, date and a short quote. `unknown` means the official page did not establish the fact. `conflicting` means official pages disagree; ReachWeaver never relies on such a fact.

| Platform | Fact | Status | Official source |
|---|---|---|---|
| Threads | Text posts limited to 500 characters; emoji counted by UTF-8 bytes | verified | [Threads posts](https://developers.facebook.com/docs/threads/posts) |
| Threads | At most 5 links; one topic tag per post; tag 1-50 characters | verified | [Threads posts](https://developers.facebook.com/docs/threads/posts) |
| Threads | Permissions `threads_basic`, `threads_content_publish`; App Review before users without an app role can grant them | verified | [Threads get started](https://developers.facebook.com/docs/threads/get-started) |
| Threads | Account-type requirements | unknown | Not stated on the fetched page; re-checked 2026-10-15, no official page limits publishing to a business or creator profile (see [THREADS.md](THREADS.md#re-checked-2026-10-15-account-types-and-app-setup)), but no receipt shows that every profile can publish |
| Instagram | Caption: 2,200 characters, 30 hashtags, 20 @ tags | verified | [IG User media](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media) |
| Instagram | API publishing needs an Instagram **professional** account; Business vs Creator split not stated | verified / unknown | [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing) |
| Instagram | Instagram Login: `instagram_business_basic`, `instagram_business_content_publish`. Facebook Login: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` | verified | [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing) |
| Instagram | Caption links tappable | unknown | No official statement found; the [Help Center](https://help.instagram.com/362497417173378) documents a profile website link |
| Facebook | Page posts: Page access token; tasks `CREATE_CONTENT`, `MANAGE`, `MODERATE`; `pages_manage_posts`, `pages_read_engagement`, `pages_manage_engagement`, `pages_read_user_engagement`, `publish_video` (video) | verified | [Pages API posts](https://developers.facebook.com/docs/pages-api/posts) |
| Facebook | Creating a post on a user (profile) feed is not possible on that endpoint | verified | [User feed reference](https://developers.facebook.com/docs/graph-api/reference/user/feed) |
| Facebook | Groups API permissions (incl. `publish_to_groups`) removed 2024-04-22 | verified | [Graph API v19.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version19.0) |
| Facebook | Page post text limit | unknown | Not stated; ReachWeaver's conservative cap applies |
| YouTube | Title at most 100 characters; description at most 5,000 bytes; no `<` or `>`; tags at most 500 characters | verified | [Videos resource](https://developers.google.com/youtube/v3/docs/videos) |
| YouTube | `privacyStatus`: `private`, `unlisted`, `public`; the API defaults to public when omitted | verified | [Videos resource](https://developers.google.com/youtube/v3/docs/videos) |
| YouTube | Every creator must declare whether videos are made for kids (COPPA) | verified | [YouTube Help 9528076](https://support.google.com/youtube/answer/9528076) |
| YouTube | Upload scopes: `youtube.upload`, `youtube`, `youtubepartner` or `youtube.force-ssl` | verified | [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) |
| YouTube | Uploads from unverified API projects created after 2020-07-28 locked to private | **conflicting** | The [revision history](https://developers.google.com/youtube/v3/revision_history) entry of 2020-07-28 says yes, until an audit; the [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) page (last updated 2026-10-08) says such uploads "are not restricted to private viewing mode". It concerns API uploads only, not Studio uploads |
| TikTok | Caption at most 2,200 UTF-16 units; `privacy_level` must match the creator's `privacy_level_options` | verified | [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) |
| TikTok | "All content posted by unaudited clients will be restricted to private viewing mode"; an audit lifts it; `video.publish` scope needs approval | verified | [Content Posting API get started](https://developers.tiktok.com/doc/content-posting-api-get-started) |
| TikTok | No default privacy; interaction toggles unchecked; commercial-content toggle off; caption editable; no promotional watermarks | verified | [Content sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) |
| TikTok | Draft-upload scope name; caption links tappable | unknown | Not on the fetched pages |

## Acceptance status (offline slice)

This slice delivers the manual fallback and the preflight that every issue names. It does **not** deliver the live adapters, mock API contract tests, receipt reconciliation or analytics, all of which need the owner's app review or audit first.

| Issue | Met by this slice | Still open |
|---|---|---|
| #19 Threads | Permissions and review requirements documented with date; manual-export fallback; live publishing stays disabled (registry `api_gated`, adapter unreachable). **Met since (mocked):** successful post (`tests/test_threads.py::test_happy_path_requests_and_receipt`), rejected permission (`tests/test_threads.py::test_contract_rejected_permission_blocks_before_claim_and_keeps_post_approved`), rate limit (`tests/test_threads.py::test_preflight_429_records_cooldown_and_keeps_post_approved`), expired token (`tests/test_threads.py::test_contract_expired_token_blocks_before_claim_and_keeps_post_approved`) and preview/receipt reconciliation without duplicates (`tests/test_threads.py::test_approved_preview_matches_the_receipt_and_is_published_exactly_once`, `tests/test_threads.py::test_reconciled_receipt_cannot_be_applied_twice`); see [THREADS.md](THREADS.md#issue-49-acceptance-evidence-offline-mocked) | Meta app with the Threads use case, the owner as Threads Tester, a long-lived token, a supervised first live receipt and the reviewed registry change |
| #20 Instagram | Professional-account prerequisite and API scopes documented; structured fallback for any account; fixtures cover bad media and missing alt text | Unsupported-consumer-account, missing-permission and asynchronous-processing API fixtures; Login-flow choice; account selection and receipts |
| #21 Facebook Pages | Personal profiles and groups rejected at preflight; Page permissions and Groups removal documented with sources and date | Reporting granted scopes from a real connection; mocked API contract tests; receipts and analytics |
| #22 YouTube | Documented manual YouTube Studio fallback (pinned by `tests/test_youtube_visibility.py::StudioFallbackDocTests`); the package never claims a video is public; privacy and made-for-kids left to the creator. **Met since (mocked, synthetic fixtures):** chunk upload (`tests/test_youtube.py::test_resumable_upload_in_chunks_then_receipt`), partial failure (`tests/test_youtube.py::test_partial_failures_after_the_first_byte_stop_and_never_resume`), audit gating (`tests/test_youtube.py::test_bundled_registry_keeps_youtube_gated`, `tests/test_youtube.py::test_unaudited_project_private_receipt_for_a_public_request_is_not_published`), duplicate avoidance (`tests/test_youtube.py::DuplicateAvoidanceTests`) and a mocked receipt feeding the analytics sampling report (`tests/test_youtube_visibility.py::test_an_adapter_receipt_flows_into_the_sampling_report_consistently`) | Google project verification and audit; a supervised private upload receipt from a real channel; channel-authorized YouTube Analytics API retrieval |
| #23 TikTok | Creator-assisted flow with traceable `manual_export_not_published` status; exact privacy choice left to the creator, public never pre-selected; audit requirements documented without bypasses. **Met since (mocked):** unaudited client (`tests/test_tiktok.py::test_unaudited_creator_offers_only_private_so_other_levels_are_blocked`), unapproved scope (`tests/test_tiktok_epic.py::test_init_401_names_the_missing_scope_and_nothing_is_uploaded`, `tests/test_tiktok_epic.py::test_an_api_scope_not_authorized_body_blocks_init_without_an_upload`), invalid creator options (`tests/test_tiktok.py::test_invalid_creator_options_block_before_the_claim`) and 429 (`tests/test_tiktok_epic.py::test_init_429_after_the_claim_is_certain_not_sent_with_a_cooldown_and_is_never_resent`, `tests/test_tiktok_epic.py::test_creator_info_429_records_a_cooldown_and_keeps_the_post_approved`) | App registration, Content Posting API scopes and audit; a supervised live inbox receipt |

**Update (2026-10-09):** later slices added a mock-tested adapter with mocked API contract tests for each of #19 to #23 (owner-approved 2026-10-09; see [THREADS.md](THREADS.md), [INSTAGRAM.md](INSTAGRAM.md), [FACEBOOK.md](FACEBOOK.md), [YOUTUBE.md](YOUTUBE.md) and [TIKTOK.md](TIKTOK.md)). Every one stays disabled in the capability registry until a supervised live receipt, so manual export is still the publishing path; app review, real receipts and analytics remain open.

Any future adapter needs its own security review and must keep human approval, dry run, the emergency stop, credential isolation and ambiguous-send reconciliation unchanged.
