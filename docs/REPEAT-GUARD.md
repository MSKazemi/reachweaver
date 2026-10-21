# Repeat guard: do not post the same material twice

Before a post can be approved, ReachWeaver checks whether this platform has already received the same material from you. It looks for the same **video file**, the same **image file**, the same **wording**, and the same **link**. The check is local and read-only, and it never sends anything.

## What counts as "the same"

| Material | How it is compared | Result |
|---|---|---|
| Video | SHA-256 of the exact file bytes | Same bytes on the same platform and account: **blocks approval** |
| Image | SHA-256 of each image file | Same bytes: **blocks approval** |
| Wording | Word 3-gram overlap, ignoring case, accents and links | Identical wording: **blocks approval**. 60% or more overlap: a **warning** |
| Link | Lower-case host, tracking parameters and `#fragment` removed | A **reminder** only; reusing a link is normal |

The same video on a *different platform* is never a repeat, because cross-posting is the point. The same material from a *different account* on the same platform is a warning, not a block (a company page and a personal profile often share a video on purpose).

## Where the history comes from

1. **Posts in ReachWeaver's database.** Published, in flight (`sending`), unresolved (`needs_review`) and still-queued posts (`draft`, `approved`, `scheduled`) all count. Cancelled posts do not. Published, `sending` and `needs_review` posts count as *already shared*, because the platform has the post or may have it. Queued posts are *also queued*, which is a warning.
2. **Things you posted by hand.** ReachWeaver cannot see what you paste into a platform's own app or site. Record it once and later drafts are checked against it:

```bash
reachweaver mark-shared --platform youtube --account my-channel \
  --video launch.mp4 --text "Version 3 is out" --url https://youtu.be/EXAMPLE --note "launch video"
```

`mark-shared` stores the text, the file hashes and the canonical links in the local database. It does not copy your video or image files, contacts nobody and posts nothing. Without `--account` the record counts for every account on that platform.

## The commands

```bash
reachweaver check POST_ID                 # also reports repeats; a blocking repeat shows as BLOCKED
reachweaver show POST_ID                  # the JSON includes "repeat_check"
reachweaver repeat-check --post-id POST_ID
reachweaver repeat-check --platform youtube --account my-channel --video launch.mp4   # before you even draft
reachweaver mark-shared --platform tiktok --video clip.mp4
```

`repeat-check` is read-only: it opens the database read-only, creates nothing, and exits 1 only when something would block approval. Add `--json` for a machine-readable result and `--days N` to change how far back *similar* wording is looked for (default 365). Identical files and identical wording are always found.

## When approval is refused

```text
Refusing to approve POST_ID: the same video file is already shared on this platform by this account
(post 1a2b3c, published, 2026-09-30). Check `repeat-check --post-id` for the full list; pass --allow-repeat
only if sharing it again is intended. Nothing changed
```

Your options, in order of preference: choose different material; or, if sharing again is intended (an evergreen reminder, a re-upload after a fix), approve with `--allow-repeat`. The override is deliberate and leaves an audit event (`repeat_override`) listing what matched. The event is written only when the approval itself succeeds, in the same transaction, and carries the same `approval_sha256=` prefix as that `approve` event; a refused approve leaves no override behind. Only you can pass it: approval stays a human action, and the Claude Code skill is not allowed to run `approve`.

## Honest limits

- **Exact files only.** A video that was re-encoded, trimmed or re-exported has different bytes and is not recognised. There is no look-alike (perceptual) matching.
- **Only what ReachWeaver knows.** It does not read the platform. Posts made elsewhere count only if you recorded them with `mark-shared`.
- **Wording is a heuristic.** The 60% threshold is an editorial choice, not a platform spam rule. A platform may treat repeats differently; this is your own guard against posting the same thing twice.
- **Approval time, not send time.** The check runs at `check`, `show` and `approve`. Two drafts that were both approved before either was sent are not compared again at send time. Approve one, send it, then approve the next; a send-time recheck is a separate change that needs a security review.
- **It does not judge quality or permission.** Passing the check does not mean a post is allowed on a platform.

See also: [start here](START-HERE.md), [manual export](MANUAL-EXPORT.md) (use `mark-shared` after you post a package by hand) and [troubleshooting](TROUBLESHOOTING.md).
