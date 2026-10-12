# ReachWeaver privacy policy

**Effective 2026-10-12.** This page describes what the ReachWeaver software and its Claude Code plugin do with data. It is a description of software behaviour, not legal advice, and it is versioned with the code in this repository: the version at the tag you installed is the policy that applies to that release.

## Summary

- ReachWeaver is open-source software (Apache-2.0) that runs **on your own computer**, inside your Claude Code session and your project folder.
- **By design, the author does not operate a server for it, does not collect data from it, and the software contains no analytics or telemetry.** It is not designed to send anything you write, draft, approve, measure or configure to the author. You can verify this in the open source code.
- Data leaves your machine only in the few explicit cases listed under "Network use" below, and only to the third party named there.

## No warranty

This policy describes the intended behaviour of experimental software that is provided "as is" under the Apache License 2.0, without warranty and without liability. See the [disclaimer and terms of use](../DISCLAIMER.md). You are responsible for how you configure and use it, for your credentials and your computer's security, and for compliance with the laws and platform terms that apply to you.

## Who is responsible

The software is published by Mohsen Seyedkazemi Ardebili (GitHub: [MSKazemi](https://github.com/MSKazemi)). You operate it. When you use it with your own social accounts, you are the person who decides what is stored, drafted and sent, and you are responsible for complying with the terms of each platform and with the laws that apply to you and to people you mention. Questions about this policy: open an issue at <https://github.com/MSKazemi/reachweaver/issues>.

## What the software stores, and where

All of the following stays on your computer. None of it is uploaded by ReachWeaver unless you run an explicit send command (see "Network use").

| Data | Where | Notes |
| --- | --- | --- |
| Drafts, approved and published post text, account ids, platform, campaign names, timestamps, approval records (including an approver label: your OS login name or a name you typed) | A local SQLite database, `state/reachweaver.sqlite` in your project, file mode 0600 | Post text may mention or quote people |
| Brand cards, campaign files, account metadata (no secrets), opt-out list | Files in your project, ignored by version control by default | You create and edit them |
| Metric snapshots you import from a platform's own export | The same database | Aggregate counts per post |
| Update-check cache and an optional opt-out marker | `~/.cache/reachweaver/` | Holds the latest-release answer from GitHub |

Credentials (tokens, app passwords) are not designed to be stored by ReachWeaver. They are read from environment variables that you set yourself, when you run a live send, and automated tests check that they stay out of exports, reports, health output, dead letters and logs. Tests reduce risk but cannot prove the absence of every bug: protect your credentials yourself.

The software does not store comments, replies, follower lists or other people's profiles in its database. The comment-triage command reads an export file that you provide and writes draft files only. The [data lifecycle page](PRIVACY-LIFECYCLE.md) lists every table and its personal-data risk.

## Network use

By default the software works offline. It makes a network request only in these cases:

1. **Update check (automatic, once per session at most every 24 hours).** The plugin's session hook asks `github.com` for the latest release of this repository. GitHub receives the request, including your IP address and a `reachweaver/<version>` User-Agent, under [GitHub's privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement). ReachWeaver sends nothing else. It never updates itself. Disable it with `touch ~/.cache/reachweaver/no-update-check`.
2. **A live send or identity check that you run.** Publishing requires `REACHWEAVER_ALLOW_LIVE=1`, `REACHWEAVER_ENV=prod`, an approved due post and an explicit `--live`. It sends the approved post text, and for identity checks the credential you configured, to that platform's own API (for example Bluesky, Mastodon, Telegram or LinkedIn) or to a Postiz backend you configured. That platform then handles the data under its own terms and privacy policy.
3. **Fetching sources you point it at** (for example your own GitHub releases, a feed, or a landing page with `landing-check --online`). These requests go to the addresses you supply.

Claude Code itself sends your conversation to Anthropic under Anthropic's own terms and privacy policy. ReachWeaver does not change that, and the text of your prompts and files that Claude reads is handled by Claude, not by ReachWeaver.

## Retention and deletion

ReachWeaver keeps local data until you delete it. You control retention:

- `reachweaver privacy-status` shows what is stored.
- `reachweaver data-export --out FILE` exports it.
- `reachweaver data-purge --policy config/retention.json` shows a dry run, and `--apply` deletes or minimizes rows according to your retention policy.
- Deleting your project's `state/` folder and `~/.cache/reachweaver/` removes everything the software stored.

Because the author holds none of this data, the author cannot access, correct or delete it for you; you hold it locally.

## Other people's data

If you draft content that names people, you are responsible for having a lawful basis and for respecting their requests. The software includes an opt-out list that blocks drafting, approval and sending of posts that mention listed people, accounts or domains. It does not scrape, buy engagement, or contact people on its own.

## Children

ReachWeaver is a tool for adults managing their own professional social accounts and is not directed at children under 18.

## Changes

Changes to this policy are made in this repository and recorded in its git history and `CHANGELOG.md`.
