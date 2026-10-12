# ReachWeaver — Claude Code plugin

ReachWeaver turns your real software, research and knowledge into planned, platform-native social posts and measured results, with a human approving every send. The plugin adds one skill, `/reachweaver:reachweaver`, plus a bundled offline command-line tool. Drafting, brand and campaign validation, calendars, manual-export packages, health checks and analytics imports run locally with no network access. Live publishing is off by default and is run by the account owner, never by Claude on its own.

Status: v0.2 experimental. No personal social account is connected by default, and nothing here promises viral reach.

> **Legal notice.** Experimental user-operated software provided "AS IS" under Apache-2.0 Sections 7–8, with **no warranty** except as required by applicable law or agreed in writing, and liability limits **only as allowed by applicable law**. Operators review and authorize their own accounts, content and publishing activity; no safeguard guarantees compliance. This project is not affiliated with Anthropic or the listed social networks. See the [responsibility and liability notice](DISCLAIMER.md), [privacy policy](https://github.com/MSKazemi/reachweaver/blob/main/docs/PRIVACY.md) and [security policy](https://github.com/MSKazemi/reachweaver/blob/main/SECURITY.md).

## Install

```
claude plugin marketplace add MSKazemi/reachweaver
claude plugin install reachweaver@reachweaver
```

Then ask Claude about social media planning, or run `/reachweaver:reachweaver`. Requires Python 3.11 or newer on your machine.

## What this plugin runs, reads and sends

- **On session start** one hook runs `update --notice`: a single HTTPS GET to github.com at most every 24 h to look for a newer release. It does not intentionally upload drafts or credentials, but GitHub receives network metadata such as IP address, time and plugin-version User-Agent. It is silent offline, never updates by itself, and is disabled by `touch ~/.cache/reachweaver/no-update-check`.
- **Everything else is local and offline by default.** Planning, drafting, validation, `publish` without `--live` and `manual-export` never open a network connection. Local state stays in a `state/` folder in your project and in your own ignored account config.
- **Credentials** are read from user-set environment variables or a configured systemd credentials directory for explicitly authorized online operations; the data may be sent to the configured platform API or backend. Regression tests check that credentials are not printed in exports, reports, health output, dead letters or logs, but cannot guarantee the absence of all defects. Live sending additionally needs `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod`, an approved due post and an explicit `--live`.
- **Optional online checks** (identity preflight, `landing-check --online`) run only when you invoke them explicitly.

## Privacy

By design the author collects no data from this plugin and runs no server for it. Full policy: [Privacy](https://github.com/MSKazemi/reachweaver/blob/main/docs/PRIVACY.md).

## More

Source, full documentation, tests and the security model: https://github.com/MSKazemi/reachweaver (Apache-2.0).
