# Engagement triage (offline, draft-only)

`reachweaver engagement-triage` helps you answer the comments, replies and mentions that matter without spam, impersonation or unauthorized outreach. It reads a JSON file **you exported** from official platform APIs or native tools, labels each item with transparent keyword rules, orders a review queue and writes reply and GitHub-issue **drafts** to new local files.

Triage itself has **no send path** (a gated, owner-approved reply path that reuses the normal post lifecycle is described under [approved replies](#approved-replies-issue-17-owner-approved-2026-10-09-sending-blocked-by-the-registry-until-a-live-receipt)). It does not read platforms, post replies, follow, like, DM, vote or file issues, and it makes no network requests. To build the export from your own published Bluesky and Mastodon posts, use the separate read-only [`engagement-fetch`](#fetching-replies-engagement-fetch). Every reply draft has `status: "draft_requires_approval"`. You review it, edit it and post it yourself from the official app while signed in as the account owner, or you drop it.

## Quickstart

```bash
reachweaver engagement-triage tests/fixtures/engagement/export.json \
  --docs-map tests/fixtures/engagement/docs-map.json \
  --out triage.md --issues-dir issue-drafts
# --out triage.json writes the machine-readable report instead; --json prints it.
```

The command refuses to overwrite an existing output or issue file, so a draft you already reviewed is never replaced silently.

## Input contract (`reachweaver.engagement-export.v1`)

```json
{
  "schema_version": "reachweaver.engagement-export.v1",
  "items": [
    {
      "id": "c-123",
      "platform": "bluesky",
      "account_id": "my-bluesky",
      "author_handle": "someone.example",
      "author_is_owner": false,
      "text": "How do I configure the docs map?",
      "url": "https://bsky.app/profile/someone.example/post/abc",
      "created_at": "2026-10-01T09:00:00Z",
      "in_reply_to": null,
      "thread_root_url": "https://bsky.app/profile/owner.example/post/root"
    }
  ]
}
```

- `id`, `platform`, `account_id`, `author_handle`, `text`, `url` and `created_at` are required strings. `author_is_owner` is a required boolean. `created_at` needs an explicit timezone.
- `platform` is one of the ReachWeaver platforms plus `github` and `hackernews`.
- `url` and `thread_root_url` must be public HTTPS URLs without credentials. A plain anchor such as `#issuecomment-1` is allowed.
- `in_reply_to` is a parent item `id` or a parent HTTPS URL, or `null`.
- Ids must be unique. A run holds at most 5000 items. Secret-like field names (`token`, `password`, `session`, `cookie`, `api_key`) are rejected.

## Fetching replies (`engagement-fetch`)

`reachweaver engagement-fetch` builds the export for you from the **public** replies under posts that ReachWeaver itself recorded as `published` with a platform remote id (Bluesky AT-URI, Mastodon status id). It is read-only: it never replies, likes, follows or sends, uses no token, and writes no database row.

```bash
reachweaver engagement-fetch                      # plan only: lists the GET requests, no network, no file
reachweaver engagement-fetch --online --out replies.json [--account ID] [--post-id ID] \
  [--max-posts 50] [--depth 6] [--max-replies 100]
reachweaver engagement-triage replies.json --out triage.md
```

Without `--online` the command only prints the planned requests. `--online` requires `--out`, a **new** owner-only (0600) file that is never overwritten; the check happens before any request. When no reply is found, no file is written (the triage contract needs at least one item).

The local database is opened strictly read-only (SQLite `mode=ro`). A missing `--db` path fails with exit 2 and is not created; a database from an older ReachWeaver version is read as it is and never migrated (only the original `posts` columns are used).

| Platform | Request (unauthenticated GET, no redirects) | Documented limits |
|---|---|---|
| Bluesky | `https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?uri=AT-URI&depth=N&parentHeight=0` | `depth` default 6, 0..1000 in the lexicon; ReachWeaver caps it at 20. `thread` is a `threadViewPost`, `notFoundPost` or `blockedPost`. |
| Mastodon | `GET {server}/api/v1/statuses/:id` (owner check), then `GET {server}/api/v1/statuses/:id/context` | Unauthenticated context is "limited to 40 ancestors and 60 descendants with a maximum depth of 20". 404 means private or deleted. `content` is HTML and is reduced to text. |

Sources, checked 2026-10-09: the [getPostThread lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getPostThread.json), [feed defs](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/defs.json), [post record](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/post.json), [Bluesky API directory](https://raw.githubusercontent.com/bluesky-social/bsky-docs/main/docs/advanced-guides/api-directory.mdx), [Mastodon statuses context](https://docs.joinmastodon.org/methods/statuses/#context) and the [Mastodon Status entity](https://docs.joinmastodon.org/entities/Status/).

How replies map into `reachweaver.engagement-export.v1`:

- Your own published post is the thread root and is never an item. It becomes `thread_root_url` and the `in_reply_to` of top-level replies. Nested replies point `in_reply_to` at their parent item id.
- Item ids are `bluesky:<at-uri>` and `mastodon:<server>:<status id>` (a SHA-256 form when longer than 200 characters). The same reply reached twice in one run is kept once (`duplicates_dropped`).
- Your own replies are kept with `author_is_owner: true` (Bluesky: author DID equals the post's DID; Mastodon: the configured local handle), so triage can mark the replied-to item `owner_already_replied`. Mastodon handles are written as `user@server`. A Bluesky reply with an empty or missing `handle` is written with the author's DID instead; a reply with neither is skipped and listed under `notes` as `reply_without_author_identity_skipped`, so one malformed reply cannot block the whole export.
- Reply text is redacted with the same `redact_private_data` as triage excerpts and then capped at 4000 characters: secrets pasted into a reply (tokens, app passwords, `token=` values, `REACHWEAVER_*` secret values), e-mail addresses and phone-number-like runs never reach the file. Redaction runs on the first 8000 characters before the cap, so an address or token that crosses the cap is replaced whole; a fragment that either cut splits is dropped. Control and bidirectional characters are kept so triage can still flag them as `suspicious`.
- Only `public`/`unlisted` Mastodon statuses connected to your post through the returned replies are exported; replies deeper than `--depth` are left out.
- The written document is validated by the same loader `engagement-triage` uses before the file is created.

Failure handling: a 404, a Bluesky `notFound`/`blocked` thread or XRPC 400, a root post whose author is not you, malformed JSON or a refused redirect fails that post only and is listed under `failed`. A 429 stops the run at once without retrying; replies already collected are still written, the remaining posts are listed under `not_attempted`, and the provider's `Retry-After` delay is shown as `retry_after_seconds` (read-only; no cooldown state is stored). A Mastodon server in authorized-fetch mode answers 401, and `engagement-fetch` never retries with a token. A non-HTTPS, local or IP-address Mastodon server is refused before any request. The exit code is 1 when any post failed or the run stopped.

Neither endpoint is paginated: each post costs one Bluesky request or two Mastodon requests, at most `--max-posts` posts (50 by default, 200 maximum) per run. Replies beyond the Mastodon unauthenticated cap of 60 are not visible; the run notes `possibly_truncated` in that case.

## Docs map (`reachweaver.docs-map.v1`)

```json
{"schema_version": "reachweaver.docs-map.v1",
 "topics": [{"topic": "Install", "keywords": ["install", "pipx"], "url": "https://example.org/docs/install"}]}
```

Topics are matched in order on whole-word keywords in the sanitized comment. Keywords and comment go through the same [fold](#normalization), so matching ignores case and accents, and Persian keywords match either letter variant. The first match supplies the docs link for a draft. You supply and check these URLs; ReachWeaver never fetches them. If a technical question matches no topic, its draft contains an `[OWNER: add a verified answer or docs link before posting.]` marker, so you can't post it without editing it first.

The docs map can also carry your own reply templates per language. See [reply templates per language](#reply-templates-per-language).

### Claims check on reply drafts (`evidence`)

Every reply draft, built-in or from one of your templates, is run through the same lexical claims check as campaign posts (`claims-check`, [CLAIM-GROUNDING.md](CLAIM-GROUNDING.md)). The facts a reply may state are the entries of an optional `evidence` list in the docs map. They have the campaign-evidence shape:

```json
{"schema_version": "reachweaver.docs-map.v1",
 "topics": [{"topic": "Install", "keywords": ["install"], "url": "https://example.org/docs/install"}],
 "evidence": [{"id": "rel", "kind": "release", "url": "https://github.com/OWNER/REPO/releases/tag/v1.0.0",
               "claim": "v1.0.0 was released and supports 4 text adapters."}]}
```

Each draft gets `claims_check` with `status` `clean`, `review` or `error`, plus findings, and the report gets a `claims_check` summary. A figure, release wording, quote, customer count, superlative or feature claim that no listed evidence supports is flagged: unsupported figures and release language are `error`, the rest `review`. The built-in templates state no facts, so they are always `clean`. The check is lexical: it never opens a source and a clean result is not proof. It does not block, edit or send anything; every draft stays `draft_requires_approval` for you to review. Warnings that do not fit a reply (a missing call to action, similarity between fixed templates) are not shown. Limits: at most 50 evidence entries, an https `url` without credentials, `kind` one of `release`, `repository`, `documentation`, `announcement`, `paper`, `benchmark`, `other`.

## Labels, priority and actions

| Priority | Label | Default action |
|---|---|---|
| 1 | `bug_report` | reply draft + issue draft |
| 2 | `technical_question` | reply draft grounded in the docs map |
| 3 | `harassment` | `human_review_required_no_draft` |
| 4 | `actionable_feedback` | reply draft |
| 5 | `feature_request` | reply draft + issue draft |
| 6 | `collaboration_opportunity` | reply draft |
| 7 | `suspicious` | `human_review_required_no_draft`; excerpt withheld |
| 8 | `praise` | short thank-you draft |
| 9 | `off_topic` | `no_reply_suggested` |
| 10 | `spam` | `no_reply_spam` |

When several rules match, one label wins in this order: suspicious, harassment, spam, bug report, technical question, feature request, collaboration, feedback, praise. Every item lists all of its `matched_rules` (rule id, label and reason). The matched text itself is not repeated. The labels are keyword heuristics, not moderation decisions, so read the source URL before you act.

Other actions:

- `skip_owner_comment`: you wrote it.
- `owner_already_replied`: one of your comments in the export replies to it.
- `duplicate_thread_draft_exists`: the thread already has a draft. You get one draft per thread, and the higher-priority item wins.
- `human_authored_reply_only`: DEV Community and Hacker News prohibit AI-generated comments, so no draft is written.
- `opt_out_no_contact`: the author asked not to be contacted or tagged. No reply or issue draft is written. The item id is listed in the report's `optout.requests_to_record` so you can add the handle to your opt-out list.
- `optout_listed_no_contact`: the author's handle or domain is on your local opt-out list (`--optout FILE`, else `$REACHWEAVER_OPTOUT`, else `config/optout.json` if present). This check runs before every other rule except `skip_owner_comment`. No reply or issue draft is written. See [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list).

## Untrusted text

Comment text is untrusted data, not instructions:

- Control and format characters are removed, including zero-width and bidirectional overrides. The text is capped at 4000 characters, and excerpts at 280.
- Prompt-injection directives are labelled `suspicious` in all seven languages. Examples: "ignore previous instructions", "ignoriere alle vorherigen Anweisungen", "ignorez les instructions précédentes", "همه دستورالعمل‌های قبلی را نادیده بگیر", "you are an AI", "system prompt", "send me", "click this", requests for passwords or codes, and hidden bidirectional controls. Text is [folded](#normalization) before matching, so full-width look-alikes, missing accents and Persian letter variants are caught too.
- Reply drafts are fixed templates plus your docs URL. They never quote the comment or the author's handle.
- Secrets pasted into comments (bearer/JWT/GitHub/Telegram tokens, app-password shapes, `token=`-style assignments and values of `REACHWEAVER_*` secret env vars) are redacted with `privacy.redact`, then e-mail addresses and phone-number-like runs of 9 or more digits. This applies to the excerpt, so nothing secret reaches the review sheet, reply drafts or issue drafts.

## Issue drafts

Each `bug_report` and `feature_request` gets a Markdown issue body in `--issues-dir`. It contains a title, the source and thread links, the triage rules, an optional docs link and a sanitized, redacted quote. In the quote, `@mentions` are put in backticks so they don't notify anyone, and HTML is escaped. Author handles are left out. The file is a draft. File it yourself, for example in the GitHub web UI, after you confirm it and check that the reporter is fine with a public issue.

## Policy

The module exports these constants, and tests assert them: `NETWORK_ACCESS = False`, `SEND_PATH = None`, `AUTO_REPLY`, `MASS_FOLLOW`, `MASS_LIKE`, `MASS_DM`, `UNSOLICITED_OUTREACH`, `VOTE_MANIPULATION` and `FAKE_PERSONAS` are all `False`. Tests also check that `engagement.py` imports no `urllib`, `socket`, `http`, `subprocess` or publisher module. They also check that it exposes no public function whose name starts with send, post, publish, reply, follow, like, DM, message or submit.

## Multilingual rules

Rule packs exist for English (`en`), German (`de`), French (`fr`), Spanish (`es`), Italian (`it`), Portuguese (`pt`) and Persian (`fa`). Each pack covers every label: bug report, technical question, feature request, collaboration, actionable feedback, praise, spam, harassment and the `suspicious` prompt-injection rules (ignore instructions, AI identity, system prompt or role play, "send me", "click here", credential requests). Each pack also has opt-out phrases ("don't contact me" and equivalents). English rule ids are unchanged. Other rule ids are `<lang>_<rule>`, for example `de_injection_ignore_instructions` or `fa_bug_symptom`, so `matched_rules` shows which pack fired.

**Source of the rules.** The project wrote all packs by hand for this slice, from common phrasings of each intent. They are not taken from a published lexicon or corpus. No native speaker has reviewed them, and they are tested only on synthetic comments (`tests/fixtures/engagement/multilingual.json`, 20 per language: two per label plus two opt-outs). Treat them as a starting point. Native-speaker review of a pack is welcome, and the labels stay heuristics, not moderation decisions.

**Which packs run.**

- Safety rules (`suspicious`, `harassment`, `spam`) and opt-out phrases run from **every** pack on every comment, whatever the language guess. A German comment with a French injection is still `suspicious`. A missed injection or opt-out costs more than a false alarm that you then review.
- Content rules (question, bug, feature, collaboration, feedback, praise) run from English plus the guessed language or languages. When the guess has confidence below 0.5, every pack runs (`all_packs_fallback: true`).

### Normalization

Before matching, text and pattern source both go through the same fold. The fold is used only for matching. Excerpts are never rewritten with it.

1. Typographic apostrophes (`’ ‘ ʼ ´`) become `'`, so `s’il`, `doesn’t` and `qu’on` match.
2. NFKC, which folds full-width letters and Arabic presentation forms (`ﺧﻄﺎ` → `خطا`).
3. Unicode casefold (`ß` → `ss`, `İ` → `i`).
4. Combining marks are removed: Latin accents (`précédentes` → `precedentes`, `contraseña` → `contrasena`) and Arabic harakat such as tanwin (`لطفاً` → `لطفا`) and the hamza on `أ/ؤ/ئ`.
5. Arabic-script variants are unified: Arabic `ي`/`ى` → Persian `ی`, Arabic `ك` → `ک`, `ة/ۀ/ہ/ھ` → `ه`. Tatweel and ZWNJ/ZWJ are removed, and Persian and Arabic-Indic digits become ASCII.

Persian patterns allow a space, a ZWNJ or nothing where writers vary (`می‌کند`, `می کند`, `میکند` all match).

### Language guess

Every item carries `language_guess`, a transparent heuristic:

- `script_letters` counts Latin, Arabic-script and other letters.
- `scores` counts stopword hits per language. The stopword lists are short lists of function words that belong to one language only. Words shared across these languages (`de`, `la`, `que`, `no`, `in`, `a`, `e`) are left out. Arabic-script text containing Persian-only letters (`پ چ ژ گ ک ی`) adds 2 to Persian.
- `confidence = min(0.95, share × evidence)`. `share` is the top language's fraction of all hits, and `evidence = min(1, hits / 4)`. The value never reaches 1, and the report never states a language as certain (`heuristic: true`).
- `candidates` lists the top language plus any language with at least 2 hits and at least half the top score. `mixed: true` when there is more than one.
- `packs_applied` and `all_packs_fallback` show which content packs ran.
- `language: "und"` means no evidence: emoji only, digits only, or Arabic script without Persian evidence. Every pack runs then.

Short comments ("Danke!", "Merci") stay below 0.5, so every pack runs and they are still labelled. Closely related languages (Spanish, Portuguese, Italian) are confused most on short text.

### Reply templates per language

Reply drafts use the built-in **English** templates unless you add your own templates to the docs map:

```json
{"schema_version": "reachweaver.docs-map.v1",
 "topics": [{"topic": "Engagement", "keywords": ["docs map"], "url": "https://example.org/docs/engagement"}],
 "templates": {
   "de": {"technical_question": "Gute Frage! Das steht in der Dokumentation: {docs}",
          "praise": "Vielen Dank, das freut uns sehr!"},
   "fa": {"technical_question": "سؤال خوبی است. توضیحش در مستندات آمده: {docs}"}}}
```

- Keys are supported language codes and draftable labels (`bug_report`, `technical_question`, `feature_request`, `actionable_feedback`, `collaboration_opportunity`, `praise`). Unknown languages or labels are rejected.
- `{docs}` is the only placeholder. It is replaced by the matched docs URL, or removed if nothing matched. Any other `{`/`}` is rejected, and the text is never passed to `str.format`. A technical question without a docs match still gets the `[OWNER: ...]` marker.
- A template is 1-1000 characters. Control and bidirectional format characters are rejected, except newlines and the ZWNJ that Persian needs.
- Your `templates.<lang>` is used only when the guess is that language, has confidence of at least 0.5, and is not mixed. Otherwise the draft is English: your `templates.en` if you set it, else the built-in text. A draft that does not fit the platform limit drops the URL first, then falls back to English.

Every draft states `language_guess`, `template_language`, `machine_translated: false`, `native_speaker_review_recommended: true` and a `language_review_note`. The note names the guessed language and asks for a native-speaker human review before posting. **ReachWeaver never machine-translates** a comment or a reply. If an English draft answers a comment that looks like German, the note says so and suggests writing the reply yourself or adding `templates.de`. The Markdown review sheet and issue drafts show the guess too.

### English (`en`)

- **Approach:** the original English rule set, unchanged. It is the only pack with the language-agnostic rules (hidden bidirectional controls, three or more links). English content rules run on every comment, because technical English ("crash", "API", "bug") is common in comments written in other languages.
- **False-positive caveats:** "send me …" and "click here" are always `suspicious`, even when harmless. "How do I enter my password?" matches the credentials rule (verb plus secret noun). "Thanks" plus a defect word becomes `bug_report`, because bug outranks praise.

### German (`de`)

- **Approach:** imperative and formal forms (`ignoriere`/`ignorieren Sie`, `vergiss`/`vergessen Sie`) with qualifiers (`vorherigen`, `alle`, `bisherigen`) and nouns (`Anweisungen`, `Instruktionen`, `Regeln`) in either word order. Questions use `wie kann/funktioniert…`, `gibt es eine Möglichkeit`. Opt-outs are `kontaktieren Sie mich nicht`, `hör auf, mich zu taggen`, `lass mich in Ruhe`, `abmelden`. Umlauts fold to the base letter (`für` → `fur`, `Schlüssel` → `schlussel`) and `ß` to `ss`.
- **False-positive caveats:** `kann ich`/`kann man` counts as a question only with a `?`, because German word order puts the verb first after an adverb ("jetzt kann ich es nutzen"). `abmelden` also means "log out", so a logout question can be read as an opt-out. That over-blocks, which is the safe direction. `Sie sind …` means both "you are" (formal) and "they are". `schick mir …` is `suspicious` like English "send me". When every pack runs, `toll` can also match the English word "toll".
- **Known gaps:** spellings with `ue/oe/ae` instead of umlauts (`fuer`) and Swiss/Austrian variants are only partly covered.

### French (`fr`)

- **Approach:** `ignorez/oubliez/ne tenez pas compte` + `précédentes/toutes/vos` + `instructions/consignes/règles`, in either order. Also `tu es/vous êtes une IA`, `envoie-moi`, `cliquez ici`, `mot de passe`/`clé API`. Questions use `comment configurer/puis-je`, `est-ce que/qu'`, `y a-t-il`. Opt-outs are `ne me contactez plus`, `ne plus me contacter`, `arrêtez de me mentionner`, `laissez-moi tranquille`, `désinscrire`. Elisions (`qu'on`, `d'avoir`) match after apostrophe normalization.
- **False-positive caveats:** `envoie-moi`/`m'envoyer` are always `suspicious`. A past-tense remark ("j'ai oublié les instructions précédentes") can match the directive rule. `comment` counts only in combinations, because the English word "comment" is common.
- **Known gaps:** casual insults about things ("c'est nul") are deliberately not harassment. Only person-directed forms (`tu es nul`) are.

### Spanish (`es`)

- **Approach:** `ignora/olvida/haz caso omiso` + `anteriores/todas` + `instrucciones/reglas`. Also `eres una IA`, `envíame/mándame`, `haz clic aquí`, `contraseña`. Questions use `cómo puedo`, `hay alguna forma`, `se puede…?`. Opt-outs are `no me contactes (más)`, `dejad de etiquetarme`, `déjame en paz`, `darme de baja`. Voseo (`sos`) and Spain/Latin-American forms are both included where known.
- **False-positive caveats:** stripping accents merges `cómo`/`como`, `sí`/`si` and `año`/`ano`. The patterns therefore never use bare `como` and need a verb after it. `es posible` counts only with a following verb, because "es posible que" is a statement. `dar de baja` can mean deactivating something else (over-blocks, safe direction).
- **Known gaps:** regional slang insults are only partly covered.

### Italian (`it`)

- **Approach:** `ignora/dimentica` + `precedenti/tutte` + `istruzioni/regole`. Also `sei un'IA`, `mandami/inviami`, `clicca qui`, `password/chiave API`. Questions use `come posso`, `c'è un modo`, `si può…?`. Opt-outs are `non contattarmi (più)`, `smettila di taggarmi`, `lasciami in pace`, `disiscrivere`.
- **False-positive caveats:** `fallito` means both "failed" and "loser". It counts as harassment only in `sei un fallito`. `mandami` is `suspicious` even when harmless. `si può` needs a `?`.
- **Known gaps:** `dove …?` (where) questions match no question rule. The same is true of English "where".

### Portuguese (`pt`)

- **Approach:** `ignore/esqueça/desconsidere` + `anteriores/todas` + `instruções/regras`. Also `você é uma IA`, `me envie/envie-me`, `clique aqui`, `senha`. Questions use `como posso`, `tem algum jeito`, `dá para…`. Opt-outs are `não me contate (mais)`, `parem de me marcar`, `me deixe em paz`, `descadastrar`. Brazilian (`você`, `contatar`) and European (`tu`, `contactar`) forms are both included.
- **False-positive caveats:** `informe/digite a senha` is `suspicious` even in a harmless question, as in English. `erro de digitação` (typo) is excluded from the bug rule, but other set phrases with `erro` are not.
- **Known gaps:** Portuguese shares many function words with Spanish and French (`mais` is French "but"), so short Portuguese comments are often `und` or low confidence. Every pack then runs, so they are still labelled.

### Persian (`fa`)

- **Approach:** Persian puts the verb last, so the directive rule accepts noun + qualifier + verb in either order (`دستورالعمل‌های قبلی را نادیده بگیر`). Also `تو یک هوش مصنوعی هستی`, `برایم بفرست`, `روی این لینک کلیک کن`, `رمز عبور`/`کد تایید`. Questions use `چطور`, `چگونه`, `آیا راهی`, or a technical term with `؟`. Opt-outs are `با من تماس نگیرید`, `دیگر پیام ندهید`, `منو تگ نکنید`, `مزاحم نشوید`, `لغو اشتراک`. Formal and colloquial verb endings (`نمی‌کند`/`نمی‌کنه`) are both covered for the common verbs. Words are prefix-bounded, so attached suffixes (`ها`, `ی`, `ت`) still match.
- **False-positive caveats:** standalone insults (`احمق`, `کثافت`) are harassment even when they describe a thing. `همکاری` counts as collaboration only in proposal forms (`همکاری کنیم`, `پیشنهاد همکاری`), because "ممنون از همکاری" (thanks for your cooperation) is common. Urdu and other Arabic-script languages that use `پ چ گ ک ی` are guessed as Persian. Arabic text is `und` unless it contains a function word that Persian shares (`و`, `من`). That gives a low-confidence Persian guess, and every pack still runs. LRM/RLM marks, which some editors insert into right-to-left text, count as hidden bidirectional controls and make the item `suspicious`. A human then reviews it, the safe failure. The sanitizer removes ZWNJ, so excerpts show joined forms (`میکند`).
- **Known gaps:** Dari and Tajik variants, Finglish (Persian in Latin letters) and heavy colloquial spelling are not covered.

## Aging and SLA queue (`engagement-queue`, issue #45 W5, offline)

```bash
reachweaver engagement-queue export.json --now 2026-10-12T12:00:00Z          # text list
reachweaver engagement-queue export.json --json --sla bug_report=12 --sla praise=48
```

It runs the same triage as `engagement-triage`, then lists only the items that still need a human, with their age and whether they are past a target. Items that need no reply (spam, your own comments, threads you already answered, no-reply labels), items covered by a draft already made for the same thread and opted-out authors are counted under `closed` and not queued. Order: overdue first, then label priority, then oldest. `--now` defaults to the current UTC time; a timestamp in the future counts as age 0.

The overdue targets are **owner-adjustable defaults, not platform data** (harassment 12 h, suspicious, bug_report and technical_question 24 h, actionable_feedback, feature_request and collaboration_opportunity 72 h, praise 168 h). Override one with `--sla LABEL=HOURS` (1 to 8760). Reach and sentiment are not part of the export contract, so the queue does not use them. It makes no network request, opens no database, and has no send, reply, follow, like or DM path: replies remain drafts that you review and post manually.

## Approved replies (issue #17, owner-approved 2026-10-09; sending blocked by the registry until a live receipt)

> **Owner-approved 2026-10-09 with every recommended decision; disabled until a live receipt.** See SECURITY-REVIEW-PROPOSAL-17-reply-send.md. The bundled capability registry sets `reachweaver_reply_adapter: "none"` for Bluesky and Mastodon. Until the owner changes that after review, every reply is held before the claim (`reply_capability_gated`) and you post replies yourself in the official app.

Triage still never sends. A reply that ReachWeaver may send later is an **ordinary post row with a reply target**. It goes through the same `approve` (payload hash), `publish --live`, claim and receipt or `needs_review` lifecycle as any post. Writing, approving and sending are three separate human steps:

```bash
reachweaver draft --account my-bluesky --file reply.txt --reply-to 'bluesky:at://did:plc:…/app.bsky.feed.post/…'
#   plan only: prints the unauthenticated GETs that would verify the parent; creates nothing
reachweaver draft --account my-bluesky --file reply.txt --reply-to 'bluesky:at://…' --online
reachweaver show POST_ID      # "reply": parent URL, author, root post, triage label, reply_ref / visibility
reachweaver approve POST_ID   # binds text AND target; prints "Bound reply target: …"
reachweaver publish --post-id POST_ID          # dry run: REPLY / HELD lines, nothing sent
```

`--reply-to` takes an item id exactly as `engagement-fetch` writes it: `bluesky:<at-uri>` (or a bare AT-URI), or `mastodon:<your server host>:<status id>`. Mastodon status ids are local to the server that issued them, so the host must be your account's own server. You write the reply text yourself (`--text` or `--file`). ReachWeaver never generates and sends in one step.

With `--online`, the parent is verified using read-only requests with no token. Bluesky uses `getPostThread?uri=…&depth=0&parentHeight=20` on the public AppView. Mastodon uses `GET /api/v1/statuses/:id` and `/context` on your server. Draft refusals (exit 2, nothing created):

| Code | Why | Overridable |
|---|---|---|
| `reply_target_outside_own_threads` | The thread root is not a post that ReachWeaver published for this account, or the parent is a top-level post. No cold replies into strangers' threads. | no |
| `reply_root_cid_mismatch`, `reply_thread_unverifiable`, `reply_root_has_threadgate` | Bluesky: the root CID differs from the publish receipt, the ancestors do not reach the root within 20 levels, or the root has a threadgate. Mastodon: `in_reply_to_id` is not among the ancestors. | no |
| `reply_parent_is_owner_post`, `reply_parent_visibility_not_public` | The parent is your own post, or a Mastodon parent is not `public`/`unlisted`. | no |
| `reply_target_flagged_<label>` | Re-triage of the parent text (all 7 language packs) labels it `suspicious`, `harassment` or `spam`, or a Bluesky parent carries a moderation label (`moderation_labelled`). | `--override-flagged <label>`, exact label only; stored in the target and audited as `reply_override_flagged` |
| `reply_parent_asked_not_to_be_contacted`, `reply_parent_author_optout_listed` | The parent asks not to be contacted, or its author is on your opt-out list. | **never** |

The target stores the parent and root post references, the parent author, the parent URL, the verification time, the triage label and any override. It never stores the parent's text. For Bluesky the references are the root and parent URI and CID (`app.bsky.feed.post#replyRef`). For Mastodon they are your server, `in_reply_to_id`, the parent's visibility and the reply visibility. The reply visibility is the stricter of the parent's and your account's `visibility`, and it is never wider than either. This follows the official Mastodon web client's rule; the API docs do not specify one. `direct` is never sent. ReachWeaver adds no @-mention.

The approval hash covers the stored target byte for byte. A reply is text-only: `media-attach`, `approve`, `check`, the dry run and `publish` refuse a reply row with images or a video (`reply_media_unsupported`). With the #7 D7 keyed binding on, a rewritten target also fails the keyed tag, and the #7 D6 confirmation prints where the reply lands before the typed hash prefix. Changing, adding or removing it after approval refuses the claim with `approval_hash_mismatch_reapproval_required`, the same rule as for edited text. `approve`, `check` and every `publish` re-check the opt-out list against the parent author. They also check that the target still matches the account: no Postiz backend, same Mastodon server, and visibility not wider than the account's current setting. A blocked reply stays `approved`. Replies count in the **same** per-account rolling send budget as posts. After the write starts, every failure goes to `needs_review` and is never resent. A Mastodon receipt whose `in_reply_to_id` differs from the target counts as uncertain. `report` lists replies under a separate `replies` key, and `ops-metrics` exports `reachweaver_reply_posts`. Both keep them out of the post totals. `data-purge` minimizes the target along with the text.

## Unified inbox demo (issue #17 slice, offline)

An export item may carry an optional `kind`, one of `reply`, `mention`, `quote_post`, `comment`, `direct_message`. Anything else is refused; a missing `kind` is reported as `unspecified`. The triage report then adds an `inbox` block (counts per category and per source platform, and the action taken per category), each item carries its `kind`, and the Markdown sheet shows a category table. `kind` changes no label, rule, draft, opt-out or moderation decision: it is a grouping label only, and a claimed category never loosens a safety rule.

The demo is `tests/fixtures/engagement/unified-inbox.json`: synthetic items from five mocked sources (Bluesky, Mastodon, GitHub, DEV, Hacker News) covering all five categories, with injection, harassment, spam, an opt-out request, an owner item and an author on the opt-out list (`unified-inbox-optout.json`):

```bash
reachweaver engagement-triage tests/fixtures/engagement/unified-inbox.json \
  --docs-map tests/fixtures/engagement/docs-map.json --optout tests/fixtures/engagement/unified-inbox-optout.json \
  --out unified-inbox-review.md
```

Nothing is fetched or sent. `tests/test_engagement_inbox.py` pins the category counts, moderation holds, opt-out handling and determinism. The categories are a schema for exports you assemble yourself; ReachWeaver still reads nothing live except public replies via `engagement-fetch`.

## Not implemented yet (issue #17)

- Reading mentions, quote posts, or comments on platforms other than Bluesky and Mastodon, and a unified live inbox. `engagement-fetch` reads only public replies under your own published Bluesky/Mastodon posts; everything else is a manual export. There is no background or scheduled fetching.
- An enabled reply send. The gated path above is owner-approved (2026-10-09) but has no live receipt, and only for replies inside your own threads. Re-reading the parent right before the send is a proposed follow-up.
- ML classification, machine translation (deliberately excluded) and packs beyond the seven listed. The packs are keyword heuristics that no native speaker has reviewed yet.
