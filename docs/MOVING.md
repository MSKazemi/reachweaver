# Moving to another computer: your workspace

Your own data is separate from the ReachWeaver program. It lives in the folder you run ReachWeaver from, your **workspace**, and you can copy that folder to another computer.

```text
my-workspace/
  config/
    accounts.json        # your accounts: handles and servers only, never a secret
    optout.json          # optional: handles and domains you never mention
    caps.json            # optional: stricter send limits
    retention.json       # optional: how long to keep local data
  state/                 # private (0700)
    reachweaver.sqlite   # every draft, approval, post, link, metric and audit event
    approval-ledger.jsonl# the approval replay ledger; must travel WITH the database
    archive/             # made by `archive`: text, metadata and copies of posted files
```

## What is not in the folder, on purpose

- **Credentials.** Tokens, app passwords and the approval key live in your environment, a password manager or systemd credentials, never in a file ReachWeaver owns. The folder carries no secret, so the new computer needs them set again. `reachweaver connect` lists the variable names that are still missing.
- **The program.** Install ReachWeaver on the new computer (`/plugin install reachweaver@reachweaver`), then bring only the workspace. The two are updated separately.
- **The update cache** in `~/.cache/reachweaver` is rebuilt by itself.
- **A worker log** you pointed `publish --log-file` at, and any attachment files that were kept outside the workspace.

> If you keep a secrets file such as `.env.reachweaver` in the same folder, **do not copy it with the workspace**. Copy `config/` and `state/` only, and move the secrets through your password manager or another secure channel.

## Check before you copy

```bash
reachweaver workspace
```

It is read-only. It shows where everything is, whether the database is intact, whether the approval ledger still matches it, which credential names are missing on this computer, and which attached files are missing or changed. It ends with `Ready to copy and use on another computer: yes` or lists what to fix first. It exits 1 when something blocks.

Things it will tell you to fix before copying:

- **The database is in use** (a `-journal` or `-wal` file exists). Let the running command finish, then copy.
- **The approval ledger is missing or does not match.** Always copy `state/approval-ledger.jsonl` together with the database.

## Copy

Copy `config/` and `state/` (and your attachment files, see below) to the same relative layout on the new computer. A plain folder copy, `rsync` or a zip all work. Do it while no ReachWeaver command is running.

## Use it on the new computer

```bash
cd my-workspace
reachweaver workspace        # same report: what is missing here
reachweaver connect          # which credential names still need to be set
reachweaver connect --guide  # walk through connecting the accounts
reachweaver list             # your drafts and posts are all there
```

## Attached images and videos

The database records each attachment by **absolute path and SHA-256**. For a post that has already gone out, a moved file does not matter: the record keeps the hash and `journal` says the file is no longer available. For a post that is **not sent yet** (draft, approved, scheduled), the file has to be found at its recorded path, or a send is refused until it is.

Two ways to fix that:

1. Put the files at the same absolute path on the new computer.
2. Point the records at the new folder:

```bash
reachweaver media-relink --from /old/media/folder --to /new/media/folder          # dry run
reachweaver media-relink --from /old/media/folder --to /new/media/folder --apply
```

`media-relink` only rewrites posts that are not sent and not in flight. It writes a path only after reading the file at the new location and finding it matches the recorded SHA-256, and it checks inside one transaction that the approval hash is unchanged, so **a relinked post stays approved**. Anything that does not match is listed and left alone. It does not copy files; move them first.

## Honest limits

- Copying a folder is not a backup strategy: keep your own backups of `state/` and `config/`.
- The approval ledger protects against replaying an old approval. It is a file next to the database; a copy that loses it leaves approvals that cannot be re-checked, and sends for them are refused.
- Two computers using the same copy at the same time will diverge. Use one at a time.
- `media-relink` handles absolute paths under one folder prefix. Files that moved individually need to be put back or re-attached.

See also: [account onboarding](ACCOUNT-ONBOARDING.md) (credentials), [journal and archive](JOURNAL.md), [privacy lifecycle](PRIVACY-LIFECYCLE.md) and [start here](START-HERE.md).
