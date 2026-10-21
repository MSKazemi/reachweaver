# Posting journal and archive: what was posted, when, and with which files

ReachWeaver already keeps everything it does in a private database on your computer (`state/`). Two commands turn that record into something you can read, search, back up and hand to someone else.

| You want | Command | What it does |
|---|---|---|
| A list of what went out | `reachweaver journal` | Read-only. Text, time, platform, account, who approved, the link the platform returned, the files, and an audit timeline |
| A folder you can browse | `reachweaver archive` | One sub-folder per post with the exact text and metadata, an index, and (with `--copy-files`) copies of the attached files |

Neither command sends, schedules or approves anything, and neither contacts any platform.

## What the journal shows

For every post that went out, or may have gone out (`published`, `sending`, `needs_review`):

- **When** (UTC): the time of the send, or the nearest recorded time
- **Where**: platform and account
- **Status** and the **link** the platform returned (`remote_url`, `remote_id`)
- **The exact text** and its length
- **Who approved** it, when, how, and the first 12 characters of the hash the approval is tied to
- **Files**: each image and the video, with path, SHA-256, size, type, and whether the file is still on disk (`--verify-files` also checks it still matches the recorded hash)
- **Timeline**: the audit events with their times (for example `draft_created`, `approve`, `send_claimed`, `published`)

Material you posted by hand and recorded with `mark-shared` appears too, marked `shared_by_hand`. A post recorded with `mark-shared --url` is listed once, as a measurable post with status `published_manual` and source `posted_by_hand` ([measure posts you shared by hand](MANUAL-EXPORT.md#measure-posts-you-shared-by-hand)). Use `--no-by-hand` to leave both out.

```bash
reachweaver journal                                  # a table of what went out, newest first
reachweaver journal --platform youtube --since 2026-10-01
reachweaver journal --account my-bluesky --format markdown --out journal.md
reachweaver journal --format csv --out journal.csv   # opens in a spreadsheet
reachweaver journal --post-id POST_ID --format json  # one post, everything, including the timeline
reachweaver journal --all-statuses                   # drafts, approved and scheduled posts as well
reachweaver journal --verify-files                   # do the recorded files still match?
```

Formats: `table` (default), `markdown`, `csv`, `json`. `--out FILE` writes a new owner-only file and never overwrites. `--since` and `--until` take a date (`2026-10-01`, UTC) or a full ISO time with a timezone; `--limit` keeps the newest N (default 100, at most 5000).

## The archive: a folder of what you posted

```bash
reachweaver archive --copy-files                 # into state/archive/, next to the database
reachweaver archive --out ~/social-archive --copy-files --since 2026-10-01
```

```text
social-archive/
  .reachweaver-archive                           # marks the folder as an archive
  index.md  index.csv                            # the overview, rebuilt each run
  2026-10-16_bluesky_my-bluesky_7eae2ea5.../
    post.txt                                     # the exact text, byte for byte
    meta.json                                    # account, times, approver, link, timeline, files
    files/
      01-image-chart.png                         # a copy, only with --copy-files
```

- **Private.** Folders are created `0700` and files `0600`. The archive holds your post text and files; do not publish it, and keep it out of git.
- **Only ever added to.** Running `archive` again adds posts that are not there yet. An existing post folder is never overwritten, and the folder must carry the `.reachweaver-archive` marker, so a mistyped `--out` cannot spill into another directory.
- **Copies are verified.** With `--copy-files`, a file is copied only if its bytes still match the SHA-256 recorded when it was attached. A file that changed or went missing is *not* copied and `meta.json` says `changed` or `missing`, so a wrong file is never presented as the one that was posted. Images that were attached by URL are recorded as `url_only`; hand-posted entries keep a hash only (`hash_only`) because ReachWeaver never saw the file.

## Keeping the files themselves

The database stores each file's **path and hash**, not the file. If you move or delete a video after posting, the journal still shows its hash and says it is no longer available. To keep copies, run `archive --copy-files` after you post (or on a schedule you set up). A reasonable habit: archive right after each posting session.

## Honest limits

- It is a **local record**, not a platform receipt. The link is what the platform returned at send time; confirm anything important on the native account.
- Anything posted outside ReachWeaver appears only if you recorded it with `mark-shared`.
- The audit timeline shows fixed action codes and times. The free-text detail of audit events can contain provider text, so it is never shown.
- Nothing is archived automatically at send time; changing the send path needs a separate security review. Run `archive` yourself.

See also: [repeat guard](REPEAT-GUARD.md) (the same history stops you posting the same thing twice), [privacy lifecycle](PRIVACY-LIFECYCLE.md) (`data-export`, `data-purge`) and [start here](START-HERE.md).
