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

## How Claude starts a session: one call

Claude starts with one read-only command instead of five or six:

```text
reachweaver context --json
```

It prints about 2-3 KB of JSON: the version and install kind, the workspace, database and accounts file paths, each account's offline readiness (and persona, if you set one) with the *names* of missing credential variables, whether the emergency pause is on, whether a live send is possible in this shell (yes/no only, never an environment value), your approval safeguards, post counts, the posts waiting for you (approved but not sent, `needs_review`, still `sending`, needing re-approval), the next steps (`owner_runs: true` marks the ones that are yours) and `owner_only_commands`: `approve`, `review`, `publish --live`, `resume`, `reconcile`, `reconnect`, `connect --guide`, `share --guide`, `share --web`, `account-kit --approve-plan` and any setting change of `approval-mode`. Claude never runs those. `context` opens the database read-only and never creates it, makes no network request and changes nothing. Without `--json` it prints a short summary for you.

**Update notice.** With the plugin, its SessionStart hook already checks for a newer release once per session (`context` shows `"session_hook": true`), so Claude does not check again. With another install, Claude runs `update --notice` once, only if no notice appeared yet.

**Exact flags, never guessed.** `reachweaver describe` lists every command; `reachweaver describe COMMAND --json` gives one command's flags (required, choices, defaults, repeatable, type, help), which flags conflict, how to get JSON, and its tags: `owner_only` (Claude never runs it), `network` (may contact a site, usually only with `--online` or `--live`), `writes` (may change the database or write files; commands that open the database read-write count, because they create a missing one), `read_only` (changes nothing) and `interactive` (needs you at a terminal). The catalog is generated from the command line itself, so it cannot drift; the tags are kept in `reachweaver/catalog.py` and a test fails when a new command has none.

**Other MCP clients.** Claude Code needs no MCP server: it runs the command line directly. For Claude Desktop or another MCP client there is an experimental, read-only `reachweaver mcp-serve`; you register it in that client yourself. See [Local MCP server](MCP-SERVER.md).

**JSON results and error codes.** `draft`, `list`, `check`, `doctor`, `schedule`, `cancel`, `unapprove`, `import-metrics`, `campaign-stage`, `approval-mode` (display only) and the dry-run `publish` take `--json`, like most read-only commands. On a failure the `ERROR: ...` line still goes to stderr; with `--json`, stdout also gets `{"error": {"code": "...", "message": "..."}}`. The codes are stable: `validation_error`, `not_found`, `already_exists`, `refused`, `paused`, `live_gate`, `database`, `permission`, `io_error`, `invalid_json`, `invalid_csv`, `invalid_value`, `invalid_input`, `publish_error` and `error`. `publish --json` is refused together with `--live`, and `approval-mode --json` together with a setting change: those stay text you read in your own terminal.

## Who does what

| Claude (in your session) | You (in your own terminal) |
|---|---|
| Researches the audience and channels | Creates accounts and credentials on the platforms' own pages |
| Drafts posts and campaign files | Sets credentials in your own environment, never in chat |
| Runs the offline checks: `doctor`, `check`, `repeat-check`, previews, dry runs | Runs `reachweaver connect --guide` to connect accounts one by one |
| Prepares manual-export packages for platforms ReachWeaver cannot send to | Reads the exact text with `show`, then runs `approve` |
| Reads your imported numbers and suggests the next experiment | Runs the live send, only where an adapter exists and you chose to |
| Asks the missing profile questions, checks which account a persona should use, builds the sign-up plan | Runs the one `account-kit --approve-plan` command, creates each account and completes every code and identity check |

The skill tells Claude never to run `approve` or `publish --live`, and this repository's own Claude Code settings block both commands outright. A plugin install does not carry those settings, so turn on the terminal check once: `reachweaver approval-mode --confirmation tty_hash_prefix`. After that, `approve` and `publish --live` need a terminal on stdin and stdout and the first 8 characters of the post's hash typed at that terminal; a command run with pipes instead of a terminal, as AI tool sessions usually run them, is refused before anything changes. This is strong friction for an agent, not a cryptographic boundary: a program can open a pseudo-terminal (`script`, `expect`, Python `pty`) and answer the check from its own output. The stronger control is the owner approval key (`approval-mode --require-key`, see [the owner approval key](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7)), held where the agent cannot read it. Approval is yours, for each post.

## The dashboard and Claude Code side by side

Keep Claude Code in its own window (terminal, desktop, IDE or web) and the read-only dashboard in a browser tab next to it:

1. Start the dashboard in your workspace: `reachweaver ui`, then open the address it prints (keep it private).
2. On **Work with Claude** (or **Ask Claude** on the Overview), type what you want, or pick a ready-made prompt on a post, account, channel or idea, and press **Copy prompt**.
3. Paste it into Claude Code. The prompt starts with `/reachweaver`, carries the ids from your database and the read-only commands to start with (`reachweaver context --json` first), quotes any stored post or idea text as untrusted data, and repeats the rule that Claude never runs `approve`, `publish --live`, `resume`, `reconcile` or `reconnect` but hands you the command.
4. Turn on **Auto-refresh** in the dashboard's settings. As Claude drafts, checks and saves ideas, the **Activity** feed and the queue update every 20 seconds.
5. When something needs you, **Only you can do these** shows the exact command with a Copy button; run it in your own terminal.

The dashboard calls no model and starts no process: it only builds text from your local data. Your Claude Code usage is whatever you send from your own session. Details: [dashboard: Work with Claude](DASHBOARD.md#work-with-claude).

## Ideas inbox: "post about this later"

Tell Claude "post about this later" and it saves the idea instead of drafting it:

```text
reachweaver idea add "Explain why approvals are bound to a payload hash" --source https://example.org/notes --tag security
reachweaver idea list [--status open|promoted|dropped] [--tag T] [--json]
reachweaver idea drop IDEA_ID
reachweaver idea promote IDEA_ID --campaign approval-binding --out campaigns/approval-binding [--brand BRAND.json --accounts ACCOUNTS.json] [--objective O]
```

- **Where it lives.** A private file, `ideas.json`, next to your database (`state/ideas.json` in a workspace; folder `0700`, file `0600`). It is not part of the post queue, so adding an idea never creates the database and an idea can never be approved, scheduled or sent. `data-export` includes it, `privacy-status` and `workspace` report it, and `data-purge` removes dropped or promoted ideas only when your retention policy sets `closed_ideas_days` (an open idea is never removed; see [data lifecycle](PRIVACY-LIFECYCLE.md)).
- **Limits.** Text up to 2000 characters, at most 8 lowercase tags, a source must be a public `https` URL (no credentials, IP addresses or local hosts), and text that looks like a password or token is refused: the inbox is not a secret store. Nothing is fetched; the source is not opened.
- **Promote.** Writes a NEW folder with `campaign.reachweaver.json` (the campaign contract format of `examples/campaign.reachweaver.json`, with your idea as the `brief` and `{{write: ...}}` placeholders for the audience, claims and post text) and `brief.md`. With `--brand` and `--accounts` it adds one placeholder variant per approved account. `campaign-check` reports the scaffold BLOCKED until you write the text and verify each source, the same as a `signal-brief` scaffold. It never creates a draft post, approval or send, and it does not open the database.

## Fact-check subagent

The plugin ships a read-only subagent, `fact-check` (listed as `reachweaver:fact-check`). Claude hands it a draft and the campaign file before showing you the draft. It splits the draft into factual claims (numbers, dates, versions, releases, features, comparisons, quotes, partnerships, customer counts), reads the cited evidence and opens public `https` sources with WebFetch, and labels each claim:

| Label | Meaning |
|---|---|
| `verified` | A source it read says the same thing; the table shows the URL and a short quote |
| `contradicted` | A source says something incompatible; the table shows the conflicting quote |
| `unverified` | No source, the source is silent, the page could not be fetched, or only an unchecked excerpt supports it |

Its tools are limited to `Read, Grep, Glob, WebFetch`: no shell, no file edits, and it never runs `reachweaver`, stages, approves or publishes. WebFetch returns a processed view of a page, so confirm any quote you rely on in the page itself before you approve. It complements `claims-check`, which is offline lexical matching against the evidence text in your campaign file and never opens a source.

The agent file is `plugin/agents/fact-check.md`. Layout and fields follow the official docs, read 2026-10-20: plugin agents live in the plugin's `agents/` folder ([plugins reference](https://code.claude.com/docs/en/plugins-reference), "Standard layout"); "Only `name` and `description` are required", `tools` is "a comma-separated string such as `Read, Grep, Bash` or a YAML list", and "plugin subagents don't support the `hooks`, `mcpServers`, or `permissionMode` frontmatter fields" ([subagents](https://code.claude.com/docs/en/sub-agents)). `claude plugin validate plugin --strict` (Claude Code 2.1.304) passes with the agent in place. The agent ships with the plugin only; a repository checkout does not add it to `.claude/agents/`.

## Status line

`reachweaver statusline` prints one short, read-only line for Claude Code's status bar:

```text
RW 0.10.0 | drafts 3 | queued 1 | needs review 0 | ideas 2 | PAUSED
```

`drafts` are posts waiting for your approval, `queued` are approved or scheduled posts not sent yet, `needs review` are sends you must check on the platform (`reconcile`), `sending` appears only while a send is in flight, `ideas` counts open ideas, and `PAUSED` shows when the emergency stop is on. Without a database it prints `RW: no workspace` and creates nothing. It opens the database read-only with a 0.25 s busy timeout, prints counts only (never post text, account names, paths or credentials), uses no network and always exits 0.

Claude Code runs the status line command with session JSON on stdin and shows what it prints ([status line docs](https://code.claude.com/docs/en/statusline), read 2026-10-20). ReachWeaver never reads that stdin. Updates are debounced at 300 ms, and "If a new update triggers while your script is still running, Claude Code cancels the in-flight script", so the command is kept fast: `python3 -m reachweaver.statusline` skips loading the full command line and took about 0.07 s on a laptop; the `reachweaver statusline` launcher (and `python3 -m reachweaver statusline`) took about 0.25 s.

To turn it on, add this to your own `~/.claude/settings.json` (or a project's settings) yourself; ReachWeaver and Claude never change your settings:

```json
{
  "statusLine": {
    "type": "command",
    "command": "reachweaver statusline"
  }
}
```

- **Installed with pip into the `python3` on your path:** `"command": "python3 -m reachweaver.statusline"` is the fastest form.
- **A repository checkout:** `"command": "PYTHONPATH=/path/to/reachweaver python3 -m reachweaver.statusline"`.
- **Another folder than the one Claude Code runs in:** add `--home DIR` (a workspace) or `--db PATH`, for example `reachweaver statusline --home ~/reachweaver-work`.
- **Plugin only:** a plugin cannot set your status line (the plugin `settings.json` honours only `agent` and `subagentStatusLine`, per the [plugins reference](https://code.claude.com/docs/en/plugins-reference)), and the plugin's folder changes with every update. Install the command line with `pipx install reachweaver` and use the first snippet.

## Behaviour evals

The plugin ships 13 behaviour tests in `plugin/evals/`. They check that Claude never runs `approve` or `publish --live` itself, never asks for or repeats a token, writes no Hacker News or Reddit text, starts with `context --json`, runs `voice-check`, saves ideas with `idea add`, uses a manual handoff where there is no adapter, and claims no unreceipted post and no virality. They are run with Claude Code's `claude plugin eval`, mostly with free transcript checks. Each run is a real model call on **your** Claude plan, so they run locally with `make plugin-eval` and never in CI. `make plugin-eval-load` checks the files without starting a run. See [plugin behaviour evals](PLUGIN-EVALS.md).

**The plugin's agent guard.** The plugin also installs a small check that Claude Code runs before every shell command Claude starts (a `PreToolUse` hook in `plugin/hooks/hooks.json`, script `reachweaver/agent_guard.py`, Python standard library only, offline). It blocks the command and tells Claude "Owner-only: ask the owner to run this in their own terminal." when it would run `approve`, `review` (the guided approval loop; `review-packet` and `review-status` are not affected), `account-kit --approve-plan`, `publish --live`, `resume`, `reconcile`, `reconnect` or `approval-mode --confirmation off`, or would set `REACHWEAVER_ALLOW_LIVE` or `REACHWEAVER_ENV=prod` or touch `REACHWEAVER_APPROVAL_SECRET`. It recognises every way of starting ReachWeaver (`reachweaver`, `run.py`, `python3 -m reachweaver` and `python3 -m reachweaver.cli`), also inside chained commands, `bash -c` and `$(...)`. Read-only commands such as `show`, `check`, `list` and `doctor`, dry runs, and draft text inside one quoted `--text` argument pass. A command whose plain words read like an owner-only run, such as `echo reachweaver approve`, is blocked as well; that is deliberate. The guard is a second layer that keeps a cooperative assistant from running these commands by mistake. It is not a lock: it cannot see a script that is written to a file and run later, so keep the terminal check above turned on. ReachWeaver also refuses shortened option names, so `publish --liv` is an error rather than a live send.

## Keeping your usage low

- **The skill loads on demand.** `SKILL.md` is a short router (under 16 KB, down from about 37 KB). Claude loads only the one or two reference files your request needs.
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
