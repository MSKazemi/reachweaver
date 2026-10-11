#!/usr/bin/env bash
# Run manually on your trusted computer AFTER signing into GitHub CLI.
# This script intentionally cannot run from ChatGPT's restricted GitHub connector.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo 'GitHub CLI (gh) is required: https://cli.github.com/' >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo 'Please run: gh auth login (choose your GitHub account and complete browser auth)' >&2
  exit 1
fi
if [[ -d .git ]]; then
  echo 'Existing git repository detected; refusing to overwrite history.' >&2
  exit 1
fi
if gh repo view MSKazemi/reachweaver >/dev/null 2>&1; then
  echo 'MSKazemi/reachweaver already exists. Clone it and add these files manually.' >&2
  exit 1
fi
if [[ -f config/accounts.json || -f .env ]]; then
  echo 'Local account/secret files found; please move them outside this directory before publishing.' >&2
  exit 1
fi

git init -b main
git add .
git diff --cached --name-only | grep -E '(^\.env$|^config/accounts\.json$|^state/|^data/|\.sqlite$|\.db$)' && {
  echo 'Refusing to commit possible private state' >&2
  exit 1
} || true

git commit -m 'feat: initial ReachWeaver skill and approval-first publishing engine'
# Default private. The user may make it public after real credential and licensing review.
gh repo create MSKazemi/reachweaver --private --source . --remote origin --push
printf '\nCreated: https://github.com/MSKazemi/reachweaver\n'
