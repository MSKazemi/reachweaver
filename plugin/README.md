# ReachWeaver — Claude Code plugin

ReachWeaver turns your real software, research and knowledge into planned, platform-native social posts and measured results, with a human approving every send. The plugin adds one skill, `/reachweaver:reachweaver`, plus a bundled offline command-line tool. Drafting, brand and campaign validation, calendars, manual-export packages, health checks and analytics imports run locally with no network access. Live publishing is off by default and is run by the account owner, never by Claude on its own.

Status: v0.2 experimental. No personal social account is connected by default, and nothing here promises viral reach.

> **Independent-use and liability notice.** Experimental user-operated software provided **"AS IS"** with **no warranty**, except as required by law or agreed in writing, under Apache-2.0 Sections 7–9. The original authors do not manage other people's accounts or authorize their independent content, misuse, modifications or deployments. Operators must check their own permissions, data, posts and compliance; no disclaimer eliminates non-waivable liability. ReachWeaver is independent of Anthropic and the social platforms. Read the [responsible-use notice](DISCLAIMER.md), [privacy policy](https://github.com/MSKazemi/reachweaver/blob/main/docs/PRIVACY.md) and [security policy](https://github.com/MSKazemi/reachweaver/blob/main/SECURITY.md).

## Install

```
claude plugin marketplace add MSKazemi/reachweaver
claude plugin install reachweaver@reachweaver
```

Then ask Claude about social media planning, or run `/reachweaver:reachweaver`. Requires Python 3.11 or newer on your machine.

## What this plugin runs, reads and sends

- **On session start** one hook runs `update --notice`: a cached HTTPS check to api.github.com at most every 24 h to look for a newer release. It does not intentionally send private draft content or credentials, but GitHub receives connection metadata such as the device's IP address and version User-Agent. It is silent offline, never installs updates by itself, and is disabled by `touch ~/.cache/reachweaver/no-update-check`.
- **Everything else is local and offline by default.** Planning, drafting, validation, `publish` without `--live` and `manual-export` never open a network connection. Local state stays in a `state/` folder in your project and in your own ignored account config.
- **Credentials** come from operator-provided environment variables or a configured systemd credential directory, as applicable, for authorized online actions. They may be sent to the selected platform API or configured backend. Regression tests reduce the risk of disclosure in logs and exports but do not guarantee there are no defects. Live sending additionally needs `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod`, an approved due post and an explicit `--live`.
- **Optional online checks** (identity preflight, `landing-check --online`) run only when you invoke them explicitly.

## Privacy

The maintainer does not currently operate a hosted posting service or intentionally collect local draft databases or account credentials through this plugin. The plugin can contact GitHub for updates and contact third-party APIs when the operator invokes online features; locally stored posts and approval metadata are retained until managed by the operator. Full policy: [Privacy](https://github.com/MSKazemi/reachweaver/blob/main/docs/PRIVACY.md).

## More

Source, full documentation, tests and the security model: https://github.com/MSKazemi/reachweaver (Apache-2.0).
