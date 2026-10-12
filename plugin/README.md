# ReachWeaver — Claude Code plugin

ReachWeaver turns your real software, research and knowledge into planned, platform-native social posts and measured results, with a human approving every send. The plugin adds one skill, `/reachweaver:reachweaver`, plus a bundled offline command-line tool. Drafting, brand and campaign validation, calendars, manual-export packages, health checks and analytics imports run locally with no network access. Live publishing is off by default and is run by the account owner, never by Claude on its own.

Status: v0.2 experimental. No personal social account is connected by default, and nothing here promises viral reach.

> **Disclaimer.** Experimental software, provided "as is" with no warranty and no liability (Apache-2.0). Independent project, not affiliated with or endorsed by Anthropic or any social platform. You alone are responsible for your accounts, content, credentials and legal compliance. Read [DISCLAIMER.md](DISCLAIMER.md) before use.

## Install

```
claude plugin marketplace add MSKazemi/reachweaver
claude plugin install reachweaver@reachweaver
```

Then ask Claude about social media planning, or run `/reachweaver:reachweaver`. Requires Python 3.11 or newer on your machine.

## What this plugin runs, reads and sends

- **On session start** one hook runs `update --notice`: a single HTTPS GET to github.com at most every 24 h to look for a newer release. It sends no data from your machine, is silent offline, never updates by itself, and is disabled by `touch ~/.cache/reachweaver/no-update-check`.
- **Everything else is local and offline by default.** Planning, drafting, validation, `publish` without `--live` and `manual-export` never open a network connection. Local state stays in a `state/` folder in your project and in your own ignored account config.
- **Credentials** are read only from `REACHWEAVER_*` environment variables you set yourself, only at send time, and are used only against that platform's own API host (for example Bluesky, Mastodon, Telegram, LinkedIn). Regression tests assert they never appear in exports, reports, health output, dead letters or logs. Live sending additionally needs `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod`, an approved due post and an explicit `--live`.
- **Optional online checks** (identity preflight, `landing-check --online`) run only when you invoke them explicitly.

## Privacy

By design the author collects no data from this plugin and runs no server for it. Full policy: [Privacy](https://github.com/MSKazemi/reachweaver/blob/main/docs/PRIVACY.md).

## More

Source, full documentation, tests and the security model: https://github.com/MSKazemi/reachweaver (Apache-2.0).
