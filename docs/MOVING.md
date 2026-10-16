# One folder for everything that is yours: your workspace

Everything that belongs to you lives in **one folder**, your workspace. To use ReachWeaver on another computer, install the program there, copy this one folder, and run one command.

```text
my-workspace/
  .reachweaver-workspace      # marks the folder; commands run inside it use it automatically
  README-WORKSPACE.txt        # the same instructions, kept next to your data
  config/
    accounts.json             # your accounts: handles and servers only, never a secret
    optout.json  caps.json  retention.json   # optional
  state/                      # private (0700)
    reachweaver.sqlite        # every draft, approval, post, link, metric and audit event
    approval-ledger.jsonl     # the approval replay ledger; travels WITH the database
    archive/                  # made by `archive`: text, metadata and copies of posted files
  media/                      # keep the images and videos you attach here
  logs/                       # worker logs: publish --log-file logs/worker.jsonl
  secrets.env.example         # variable NAMES only, so you know what to set on a new computer
```

## Set it up once

```bash
reachweaver workspace --init ~/reachweaver-work     # creates the layout; never overwrites or moves anything
cd ~/reachweaver-work                               # from here on, every command uses this folder
```

Already have a folder with `config/` and `state/`? Run `reachweaver workspace --init .` inside it: it only adds the marker and the missing sub-folders and leaves your files alone. You can run commands from any sub-folder, or from anywhere with `--home ~/reachweaver-work`. An explicit `--db` or `--accounts`, or a `REACHWEAVER_DB` / `REACHWEAVER_ACCOUNTS` setting, still wins over the workspace. A folder without the marker behaves exactly as before.

Keep the images and videos you attach in `media/`. If some live elsewhere (your Pictures folder), pull them in once:

```bash
reachweaver workspace --gather            # dry run: what would be copied
reachweaver workspace --gather --apply    # copies into media/ (hash-verified) and points the records at the copies
```

## Move to another computer

1. **On the old computer:** `reachweaver workspace` should end with `Ready to copy and use on another computer: yes`. Finish any running ReachWeaver command first.
2. **Copy the whole folder** (a plain copy, `rsync` or a zip). **Do not copy `secrets.env` or any `.env*` file** with it; the report warns if one is there.
3. **On the new computer:** install ReachWeaver (`/plugin install reachweaver@reachweaver`), then:

```bash
cd my-workspace
reachweaver workspace --repair --apply   # re-finds your attached files in media/ and fixes the recorded paths
reachweaver connect                      # which credential names still need to be set
reachweaver connect --guide              # walk through connecting the accounts
reachweaver list                         # your drafts and posts are all there
```

`--repair` is needed because attachments are recorded by absolute path. It finds each missing file in `media/` by size and SHA-256 (so a renamed file is still found), writes the path only for a verified match, and checks that the approval hash did not change, so **approved posts stay approved**. Anything it cannot find is listed and left alone. A dry run is the default; `--apply` writes.

## What is not in the folder, on purpose

- **Credentials.** Tokens, app passwords and the approval key are never stored by ReachWeaver. They live in your environment, a password manager or systemd credentials, so the new computer needs them set again. `secrets.env.example` lists the names; if you keep your own `secrets.env` in the folder, keep it `chmod 600` and treat it like a password.
- **The program.** Install it on each computer; the two are updated separately.
- **The update cache** in `~/.cache/reachweaver` is rebuilt by itself.

## Checking the folder

```bash
reachweaver workspace
```

Read-only. It shows where everything is, whether the database is intact and not in use (a `-journal` or `-wal` file means a run is in progress), whether the approval ledger still matches it, which credential names are missing here, which attachments of unsent posts are missing, changed or outside the folder, and whether a secrets file is sitting in it. It exits 1 when something would break the copy.

## If you moved files by hand instead

`reachweaver media-relink --from /old/media/folder --to /new/media/folder [--apply]` rewrites the folder prefix of recorded attachment paths (dry run by default). It has the same safety rules: only unsent posts, only files whose SHA-256 still matches, approval hash unchanged.

## Honest limits

- Copying a folder is not a backup strategy: keep your own backups of the workspace.
- The approval ledger protects against replaying an old approval. A copy that loses `state/approval-ledger.jsonl` leaves approvals that cannot be re-checked, and sends for them are refused.
- Two computers using copies of the same workspace at the same time will diverge. Use one at a time.
- Files of posts that were already sent do not matter for the move: the record keeps their hashes, and `journal` says whether the file is still there. `archive --copy-files` keeps copies.

See also: [account onboarding](ACCOUNT-ONBOARDING.md) (credentials), [journal and archive](JOURNAL.md), [privacy lifecycle](PRIVACY-LIFECYCLE.md) and [start here](START-HERE.md).
