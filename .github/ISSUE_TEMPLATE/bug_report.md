---
name: Bug report
about: Reproducible defect in the ReachWeaver CLI, skill or docs (not a security vulnerability)
title: "[bug] "
labels: bug
---

<!--
SECURITY ISSUES (credential exposure, approval/dry-run bypass, unintended live send):
do NOT file them here. Use the private reporting route in docs/SECURITY.md.
Never paste tokens, app passwords, cookies, session files, private analytics or private draft text.
-->

## What happened

## What you expected

## Steps to reproduce (offline if at all possible)

```bash
RW_TMP=$(mktemp -d)   # private (0700) directory; the state DB refuses shared directories
python3 -m reachweaver --db "$RW_TMP/bug.sqlite" init
# ...
```

## Output

```text
paste sanitized output here (redact handles/IDs you consider private)
```

## Environment

- ReachWeaver version / commit:
- Python version (`python3 --version`):
- OS:
- Installed as: repository CLI / bundled Claude Code skill

## Did a live platform request happen?

- [ ] No — offline command or dry run only.
- [ ] Yes — platform: ___ ; post state after the run (`reachweaver list`): ___ . If a send is in `sending`/`needs_review`, check the platform and reconcile manually; do not retry.
