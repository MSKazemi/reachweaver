# Repeat guard: do not post the same material twice

Before a post can be approved, ReachWeaver checks whether this platform has already received the same material from you. It looks for the same **video file**, the same **image file**, the same **wording**, and the same **link**. The check is local and read-only, and it never sends anything. The blocking part runs again when the post is about to be sent (see [when a send is refused](#when-a-send-is-refused)).

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

Your options, in order of preference: choose different material; or, if sharing again is intended (an evergreen reminder, a re-upload after a fix), approve with `--allow-repeat`. The override is deliberate: it is bound into that approval (`approved_allow_repeat`, tag `approval/repeat-1`, so `unapprove` withdraws it and editing it in the database is a hash mismatch), and an audit event (`repeat_override`) lists what matched once the approval exists. Only you can pass it: approval stays a human action, and the Claude Code skill is not allowed to run `approve`.

## When a send is refused

The same check runs again when a post is about to be sent: in `publish --live` before the identity preflight, and inside the claim itself, under the same database lock that marks a post as sending. A post is refused when its video, an image or its exact wording matches material the same account has already published, is sending, has in `needs_review`, or recorded with `mark-shared`. So two copies approved before either was sent can no longer both go out: the first is sent, the second is refused.

```text
SKIPPED POST_ID: repeat_of_shared_material: post POST_ID is an exact repeat: the same wording is already shared
on this platform by this account (post 1a2b3c, published, 2026-10-21); it stays approved/scheduled and is
re-refused on every run until a human acts: ...; not claimed, no request sent
```

Nothing is written for the refused post (no claim, no budget, no `last_error`), and the worker log reason is `repeat_of_shared_material`. It is refused again on every run until you act: `cancel` it, or, if both copies should go out, `unapprove`, `show` and `approve --allow-repeat`. Only an approval made with `--allow-repeat` passes; the `repeat_override` event is never read as permission. `approve` prints a note when an exact copy is also queued on the same account, and the dry run (`publish` without `--live`) shows the refusal. Another account with the same material is not a repeat.

## Honest limits

- **Exact files only.** A video that was re-encoded, trimmed or re-exported has different bytes and is not recognised. There is no look-alike (perceptual) matching.
- **Only what ReachWeaver knows.** It does not read the platform. Posts made elsewhere count only if you recorded them with `mark-shared`.
- **Wording is a heuristic.** The 60% threshold is an editorial choice, not a platform spam rule. A platform may treat repeats differently; this is your own guard against posting the same thing twice.
- **Exact repeats only at send time.** The send-time check refuses identical files and identical wording on the same account; *similar* wording and the same link stay warnings at `check`, `show` and `approve`.
- **It does not judge quality or permission.** Passing the check does not mean a post is allowed on a platform.

See also: [start here](START-HERE.md), [manual export](MANUAL-EXPORT.md) (use `mark-shared` after you post a package by hand) and [troubleshooting](TROUBLESHOOTING.md).
