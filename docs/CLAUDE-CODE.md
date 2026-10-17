# Using ReachWeaver with Claude Code and your subscription

ReachWeaver is built to run **inside Claude Code**, on the subscription you already have. This page explains how that works, what Claude does and what you do, and how to keep your usage low.

## How it works

- **Claude does the thinking and writing.** When you ask for a campaign, Claude researches, drafts and checks inside your normal Claude Code session.
- **ReachWeaver does the careful bookkeeping.** It is a Python program that uses only Python's standard library. It makes **no AI model calls**, needs **no API key**, and has no account or server of its own. It validates drafts, records approvals, previews sends, checks your history for repeats and keeps a private database on your computer.
- **So there is nothing extra to pay for.** ReachWeaver adds no model charges. Your Claude Code usage is whatever your own plan includes. The repository has a test that fails if code is added that calls a paid model API.

## Install it (once)

In Claude Code:

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

Restart Claude Code if it asks you to. You need **Python 3.11 or newer**. Which route suits you (plugin, Python package, standalone installer, ZIP, clone) is in [how to install](INSTALL.md). To connect accounts and approve posts from your own terminal, also run `pipx install reachweaver`: the plugin does not put a `reachweaver` command on your terminal's path.

Update later with `/plugin marketplace update reachweaver`, then `/plugin update reachweaver@reachweaver`. Claude tells you when a newer release exists (one check to github.com at most every 24 hours, silent when offline) and never updates without asking.

## Your first session

Just describe what you want, in plain words. For example:

```text
/reachweaver
Run the setup check and tell me what is missing. Don't connect or post anything.
```

```text
/reachweaver
Take my latest GitHub release and plan a one-week campaign for LinkedIn and Bluesky.
Draft one post per channel, check them, and show me the exact text. Wait for me to approve.
```

```text
/reachweaver
I posted this video on YouTube by hand yesterday. Record it, then check whether the
draft I just wrote repeats it.
```

## Who does what

| Claude (in your session) | You (in your own terminal) |
|---|---|
| Researches the audience and channels | Creates accounts and credentials on the platforms' own pages |
| Drafts posts and campaign files | Sets credentials in your own environment, never in chat |
| Runs the offline checks: `doctor`, `check`, `repeat-check`, previews, dry runs | Runs `reachweaver connect --guide` to connect accounts one by one |
| Prepares manual-export packages for platforms ReachWeaver cannot send to | Reads the exact text with `show`, then runs `approve` |
| Reads your imported numbers and suggests the next experiment | Runs the live send, only where an adapter exists and you chose to |

The skill tells Claude never to run `approve` or `publish --live`, and this repository's own Claude Code settings block both commands outright. A plugin install does not carry those settings, so for a hard guarantee turn on the terminal check once: `reachweaver approval-mode --confirmation tty_hash_prefix`. After that, `approve` and `publish --live` only work in a real terminal where you type the first characters of the post's hash, which an AI session cannot do. Approval is yours, for each post.

## Keeping your usage low

- **The skill loads on demand.** `SKILL.md` is a short router (about 12 KB, down from about 37 KB). Claude loads only the one or two reference files your request needs.
- **Commands cost nothing to run.** The offline commands run on your computer. Only the text they print goes back into the conversation, so ask for `--json` summaries instead of long listings.
- **Work in stages.** A big request (several platforms for several weeks) is cheaper as steps: strategy, then one channel's drafts, then the rest.
- **Be specific.** One goal, one project and one or two channels give better drafts than a general request.

## If something does not work

- **`/reachweaver` is not found.** Restart Claude Code after installing. Check that the plugin is listed under `/plugin`.
- **Python errors.** Check `python3 --version` is 3.11 or newer.
- **Claude wants a password or token.** It should not. Stop, and set credentials yourself as described in [account onboarding](ACCOUNT-ONBOARDING.md).
- **Anything else.** [Troubleshooting](TROUBLESHOOTING.md), or the [plain-language guide](START-HERE.md).

## What it will not do for you

It will not post without your approval, promise views or followers, write for Hacker News or Reddit, or drive a browser to log in for you. See [what it will never do](../README.md#what-it-will-never-do).
