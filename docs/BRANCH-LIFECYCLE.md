# Branch lifecycle and non-destructive consolidation

Use one `main` branch and normally at most three **active** topic branches. Keep short-lived
PRs focused. Completed work belongs on `main`, not in indefinitely retained feature heads.

## Before any merge

1. Record the exact source/head and current target SHA. Review the diff, ownership,
   private/public boundary, dependencies, release rights and impact on existing code.
2. Run the current GitHub Actions hygiene and offline Python 3.11/3.12/3.13/3.14 matrix.
   A cancelled job, job without an allocated runner, or success for an old commit is
   **not** a passing gate. Rebase/reconcile conflicting changes without force-pushing
   over someone else's work. Require new checks on the resolved head.
3. Confirm no secrets, personal data, internal policy, unauthorized media or private
   `docs/internal/` documents will enter the **public** repository. Keep legal/IP and
   other owner-only decision records in `reachweaver-private`.
4. For security, publishing, licensing or other high-risk changes, obtain the separate
   owner/qualified-expert review they require. Green tests do not waive these gates.
5. Merge only through the PR, preferably squash for a coherent independent change.
   Verify the target SHA and its post-merge checks; no release is implied.

## Automatic cleanup of merged PR branches

The [guarded cleanup workflow](../.github/workflows/prune-merged-branches.yml)
removes stale branches after same-repository PRs are actually merged into public `main`.
Its historical cleanup considers only the pinned merged PRs #57-#59 and #62; it
**never** deletes the unmerged/closed directory branch associated with PR #60.

The code validates: the PR is closed and merged into this repository's `main`;
its merge commit remains an ancestor of current `main`; its head is in this
repository; the branch SHA matches the immutable merged-event SHA (or the
pinned historical PR SHA), is unprotected, unchanged since the PR head
and has no open PR using it. It then deletes with Git's SHA-checked
`--force-with-lease`, refusing deletion if the remote ref changed after inspection.
All other branches, including diverged/closed-but-not-merged heads, are retained.
The workflow uses SHA-pinned checkout, no persisted checkout credentials and a
short-lived GitHub Actions token with write permission scoped to that cleanup job.
It neither publishes a release nor calls social network APIs.

The script is dry-run by default; authorized maintainers can inspect eligible
candidates with `GITHUB_REPOSITORY=MSKazemi/reachweaver GITHUB_TOKEN=... python
scripts/prune_merged_branches.py --historical` (never paste a real token in a
chat, an issue, a command history or a public log). Only the `--apply` option
performs deletion. The workflow invokes it explicitly. If repository Actions
permissions deny ref deletion, cleanup fails visibly and the branch is retained.

The deletion of a branch *reference* does not undo a merged commit; the merge and
its PR remain in GitHub history. This is not an assurance of archival permanence:
keep independent backups for legally significant or unmerged work and do not
prune branches when provenance, rights or active PR references are uncertain.
For the private repository, no automatic deletion is configured while private CI
runner provisioning is blocked. Review that repository separately.
