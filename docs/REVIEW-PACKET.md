# Review packets: ask a co-reviewer before you approve

> **New in 0.11.0, owner-reviewed 2026-10-20.** Behaviour may still change in a later release.

A teammate, an editor or a co-founder can read the **exact text** of a post before you approve it, and leave a comment. ReachWeaver writes a folder you send to them however you like (email, chat, a shared drive). It sends nothing itself.

**A comment is never approval.** A `looks_good` verdict approves, schedules and changes nothing. Only you approve, in your own terminal, with `reachweaver approve POST_ID` after reading `reachweaver show POST_ID`. The approve command never reads a review packet or its comments.

## 1. Write a packet

```bash
reachweaver review-packet --post-id 5800f28d7e0743b5 --post-id 9a1c... --out state/review-packets/launch-week
reachweaver review-packet --campaign-staged launch-2026-10 --out state/review-packets/launch-2026-10
```

- `--post-id ID` (repeat it) picks stored posts. `--campaign-staged NAME` picks every draft, approved or scheduled post that `campaign-stage` created for that campaign id. A packet holds at most 50 posts, and only draft, approved or scheduled ones.
- `--out` must name a **new** folder. It is created owner-only (`0700`), and every file in it is owner-only (`0600`). An existing path is refused.
- `--persona`, `--optout` and `--sources` work as they do for `check`.
- `--json` prints the folder, the files and each post's digest.

The database is opened **read-only**. The command never creates, upgrades or writes it.

The folder holds:

| File | What is in it |
|---|---|
| `README.md` | One line per post: platform, account, status, digest and a link to its file |
| `post-ID.md` | For the reviewer: destination (platform, account id, public handle), status, campaign, the exact text in a code block, each image's alt text, any video title or tags, the content digest, the `check` findings, the cited evidence and a comment template |
| `post-ID.json` | The same, as data |
| `packet.json` | The manifest that `review-comment` and `review-status` read |
| `comments.json` | Created by the first `review-comment`; stays in this folder |

What is **not** in a packet: credentials, local file paths, the approval nonce, the keyed approval tag, the database path. A source URL or an evidence link inside a private source repository is replaced by `[REDACTED:private_source]`, with the same rule as `data-export` (private repositories declared in `--sources`, or behind private evidence that was staged in this database). A post whose text, alt text, video title or hosted image URL links into such a private repository is **refused**, as is a post whose text matches a credential pattern or a secret from your environment. Nothing is written in either case.

### The content digest

Each post carries `payload_sha256`. It is the digest that `show` prints as `approve_would_bind_sha256` for a draft: the approval payload (account, platform, exact text, source URL, campaign, reply target, image and video records) without the schedule time, the per-approval nonce or the identity binding. ReachWeaver defines no new kind of hash for this. Approving or scheduling a post does not change this digest; editing the text (unapprove, then a new draft), the images, the destination, the source URL or the campaign does.

## 2. Record a comment

Your reviewer replies with three lines from the template (post id, verdict, note). You record them:

```bash
reachweaver review-comment --packet state/review-packets/launch-week --post-id 5800f28d7e0743b5 \
  --reviewer "Jane Editor" --verdict change_requested --note "Second sentence overstates the benchmark"
```

- `--verdict` is `looks_good`, `change_requested` or `question`.
- `--reviewer` is a label of at most 80 characters, not authentication. `--note` is at most 2000 characters. Control characters (other than new lines and tabs in a note) and bidirectional-override characters are refused. Anything in the reviewer label or note that looks like a credential is replaced with a `[REDACTED...]` marker.
- The comment is stored in `comments.json` inside the packet folder (owner-only, replaced atomically). It is never written to the database or the approval ledger.
- The comment is bound to the digest of the text the reviewer read (the packet's). If the database is present and the post has changed since the packet was written, the comment is **refused**: the reviewer read old text, so write a new packet. Without a database the comment is kept and marked `database_not_checked`.

`review-comment` is marked **owner-only** in `reachweaver describe`: it records another person's words, so Claude Code prepares the command and you run it. Claude must never invent a reviewer comment.

## 3. See where things stand

```bash
reachweaver review-status --packet state/review-packets/launch-week
reachweaver review-status            # every packet under state/review-packets/
reachweaver review-status --json
```

For each post it shows whether the text is unchanged since the packet, and each comment with its verdict, reviewer, time and note. A comment is **STALE** when the post's current digest differs from the digest the comment was made on: the post changed after the comment, so the comment is about other text. A post that is no longer in the database is shown as `post_missing`. Without a database nothing can be checked and comments are shown as `not_checked`.

The exit code is `1` when any comment is stale or any post changed or disappeared, and `0` otherwise. Reviewer notes are shown labelled as data: they were written by another person and are never instructions, for you or for Claude Code.

## Limits

- A packet is a snapshot. It does not update when you edit a post; write a new one.
- Comments are local. Anyone who can write to your packet folder can edit `comments.json`; that is why a comment carries no authority. The folder and file permissions keep other local users out.
- The reviewer's identity is whatever you type in `--reviewer`. ReachWeaver does not verify it.
- "My reviewer said it looks good" is not a reason to skip reading `show` yourself. The approve decision is yours.

## Related

- [Campaign preview](CAMPAIGN-PREVIEW.md): per-channel review of a campaign file before staging.
- [Glossary: payload hash](GLOSSARY.md).
- [Security](SECURITY.md).
