# Voice check: does a draft sound like you?

**Status: experimental, advisory only, not yet in a released package.** Two offline commands help drafts sound like the account owner rather than like generic AI-written text:

- `voice-profile` measures how **you** have written, from your own past posts, and saves the numbers in a private file.
- `voice-check` compares one draft with that profile and with a short list of stock phrases and patterns (the "tell list").

Neither command blocks anything. A finding is `info` or `warn`; approval and publishing work exactly as before.

## What it is not

- **Not an AI detector.** The tell list is a heuristic style list: phrases and patterns that readers often associate with machine-drafted posts. A match does not show that a model wrote the text, a clean result does not show that a person did, and no accuracy is claimed. Plenty of people write "excited to share" or use em dashes.
- **Not a reach predictor.** The profile describes how you have written. It says nothing about which posts did well, and a draft outside your usual range may still be the right post.
- **Not a style imitator for other people.** Build a profile only from posts you wrote or are allowed to use. Do not profile another person's posts to impersonate them.

## Build a profile

```bash
reachweaver voice-profile --from my-posts.json --out state/voice-profile.json
reachweaver voice-profile --from-db --account bsky-main --platform bluesky --out state/voice-bluesky.json
```

`--from FILE` reads posts you supply:

- `.json`: a list of strings, a list of objects with `text` (or `content`) and an optional `platform`, or `{"posts": [...]}`;
- `.csv`: a header row with a `text` (or `content`, `post`, `body`) column and an optional `platform` column, such as a cleaned-up platform export;
- any other file: UTF-8 text with one post per block, blocks separated by a line holding only `---`.

`--from-db` reads your posts with status `published` from the local database. The database is opened read-only and is never created. `--account` limits it to one account; `--platform` limits either source to one platform (posts in a file without a platform count as that platform).

At least **10 usable posts** are needed. With fewer, the command prints `insufficient_sample`, writes nothing and exits `1`. Each platform with at least 5 posts also gets its own length statistics.

The output file must not exist yet: an existing file is never overwritten (exit `2`). It is created with mode 600 (only you can read it).

### What the profile holds

Plain statistics, with the median, the middle half (25th-75th percentile) and the middle 80% (10th-90th percentile) of your posts:

- length in characters and words, overall and per platform;
- average sentence length;
- emoji, hashtags, @-mentions and links per post;
- question and exclamation marks, line breaks and list lines;
- em dashes, semicolons and ellipses;
- I/me/my and we/us/our per 100 words, and ALL-CAPS words per 100 words.

It also records habits as shares of your posts (starting in lowercase, opening with a question, hashtags at the end, ending with the link), your common first words, the hashtags you reuse, and up to 15 recurring words and 10 two-word phrases. A recurring word is one that at least 3 posts use and that is not in a small built-in list of common English words. For posts in other languages that list will also contain ordinary words.

**No post text, post ID or link is stored**: only numbers and those short word lists. A word or hashtag used in only one post never appears: openings and hashtags need 2 posts, recurring words and phrases 3. `sample_sha256` is a hash of the hashes of the posts used, so you can tell two profiles of the same posts apart from profiles of different posts, without storing the posts.

## Check a draft

```bash
reachweaver voice-check --profile state/voice-profile.json --text "Draft text" --platform bluesky
reachweaver voice-check --profile state/voice-profile.json --post-id POST_ID
reachweaver voice-check --file draft.txt            # no profile: only the tell list and generic checks
reachweaver creative-preflight --file brief.json --voice-profile state/voice-profile.json
```

The draft comes from `--text`, a UTF-8 `--file` or a stored post (`--post-id`, read-only; its platform is used unless you pass `--platform`). `--profile` is optional.

Findings:

- `voice.<feature>` (`warn`): the draft is outside the middle 80% of your posts for that feature. The message shows your range and median. With `--platform`, length, words, hashtags and emoji use that platform's numbers when the profile has them.
- `habit.<name>` (`info`): for example, the draft starts with a capital letter while almost all of your posts start in lowercase.
- `tell.<id>` (`warn` or `info`): a stock phrase or pattern from the tell list, with the matched words and a short reason. If at least 20% of your own posts contain the same tell, it is shown as `info` with a note that it may simply be your style.

Exit codes: `0` after every successful check, whatever the findings (they are advisory); `2` with one `ERROR:` line for unreadable input, an unknown post or an invalid profile. `--json` prints the full report (`reachweaver.voice-check.v1`), including the draft's own feature values and a few hints from the profile (common openings and recurring words).

With `creative-preflight --voice-profile`, each draft gets `voice_findings`, and the text report lists the `warn` ones. The checklist points are unchanged.

## The tell list

The list lives in `reachweaver/voice_tells.json`, written for ReachWeaver. Each entry has an id, a severity and a reason. It has three kinds of entry:

- **phrases** such as "delve", "in today's fast-paced world", "game-changer", "unlock the power", "let's dive in", "thrilled to announce";
- **patterns** such as "it's not just X, it's Y" and "no X, no Y, just Z";
- **structure**: two or more em dashes, repeated three-item lists, two or more rocket or sparkle emoji, three or more lines that start with an emoji, a question as the first sentence, five or more hashtags, three or more exclamation marks.

**Matching.** Phrases match case-insensitively and only as whole words: "delve" matches "Delve", but not "Delvecchio". Inflections are listed explicitly when they are tells too ("delves", "delving", "delved"), so a longer word never matches by accident. Straight and curly apostrophes are treated alike, a space matches any run of spaces or line breaks, and a hyphen also matches a space ("game changer"). Emoji are counted by Unicode block, which is approximate.

## Privacy

Everything runs on your computer. No network request, no model call, no account, no credential and no write to the database. The profile is a private file you can delete at any time. Keep it out of Git, issues and shared prompts like the rest of your workspace. If you paste findings into a chat, they contain only the draft's own words and your aggregated numbers.

## With Claude Code

Claude runs `voice-check` on each draft before showing it to you. It rewrites the flagged lines itself in the conversation (no model API is involved) and tells you what it changed and why. For human-only destinations (Reddit, Hacker News) it only reports the findings: you write and edit that text yourself.
