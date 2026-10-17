# The recommended setup: Claude does the work, you keep the approvals

This is the one route we suggest for most people. It gives you every command, lets Claude run all of them that are safe for Claude to run, and keeps the few decisions that must be yours in your own terminal. For the other routes and when to use them, see [how to install: pick one route](INSTALL.md).

You need **Python 3.11 or newer** and [Claude Code](https://code.claude.com/docs/en/overview). Nothing here connects an account or publishes anything.

## Why this combination

| Piece | What it gives you | Why you want it |
|---|---|---|
| **Claude Code plugin** | `/reachweaver` in Claude Code, with the whole command set built in | Claude researches, drafts, checks, previews and runs the offline commands for you. Claude Code updates it. |
| **Python package** (`pipx install reachweaver`) | the `reachweaver` command in **your** terminal | The steps that must be yours (connect an account, approve a post, send it live) are run by you, and Claude is told never to run them. |
| **One workspace folder** | your accounts file, database, approvals, media and logs in one place | You can back it up or move it to another computer. It holds no passwords or tokens. |

`pipx` rather than `pip`: it installs the command in its own isolated environment and puts it on your PATH, and it works on modern Linux systems that refuse a plain system-wide `pip install`. If you prefer `uv`, `uv tool install reachweaver` is equivalent.

## Step 1. Install, once per computer

In your terminal:

```bash
pipx install reachweaver
pipx ensurepath          # then open a new terminal if `reachweaver` is not found
```

Then add the plugin, either inside Claude Code:

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

or from your terminal, which suits scripts and setting up a second computer:

```bash
claude plugin marketplace add MSKazemi/reachweaver
claude plugin install reachweaver@reachweaver
```

You do not need an existing marketplace: the first line registers this repository as one. Check with `/plugin marketplace list`. If `/plugin` is not available to you, use the [standalone skill installer](INSTALL.md) instead of the plugin; the rest of this page is unchanged.

## Step 2. Make your workspace, once per project

```bash
mkdir my_social_media
cd my_social_media
reachweaver workspace --init .
[ -f config/accounts.json ] || curl -fsSL https://raw.githubusercontent.com/MSKazemi/reachweaver/main/config/accounts.example.json -o config/accounts.json
reachweaver approval-mode --confirmation tty_hash_prefix
```

The first command creates `config/`, `state/`, `media/`, `logs/`, a `.gitignore` that keeps secrets out of git, and `secrets.env.example` (credential names only). The `curl` line is a safety net and only runs when `config/accounts.json` is missing: it fetches the example accounts file (public handles, no secrets; edit it later to list your own accounts). Releases after 0.5.2 carry that file inside the package, so `workspace --init` creates it and the line does nothing; 0.5.2 from PyPI does not, and `doctor` then stops with "Missing .../config/accounts.json". The last command makes `approve` and `publish --live` work only in a real terminal where you type the first characters of the post's hash, which an AI session cannot do. That setting is stored in this workspace's database, so repeat it for each new workspace.

Claude can do the first steps for you if you prefer; run the last one yourself.

## Step 3. Start Claude in that folder and ask

```bash
claude
```

```text
/reachweaver
Run the setup check and tell me what is missing. Don't connect or post anything.
```

From here, describe the job in plain words:

- "Audit my LinkedIn profile and tell me what to improve first."
- "Take my latest GitHub release and plan a two-week campaign for LinkedIn and Bluesky. Draft one post per channel, check them, and show me the exact text."
- "I posted this video by hand yesterday. Record it, then check whether this new draft repeats it."

Claude runs the checks, previews and dry runs itself and reports what they printed.

## Optional: choose where to create your next account

Use `reachweaver account-advisor --region global --goal awareness --format short_video --weekly-hours 5` to shortlist channels, and `reachweaver account-advisor --setup instagram` to get the official starting page, the steps you do yourself and a **coaching** prompt for a normal Claude chat, which works only from what you describe and never opens or operates the site. You register and verify the account yourself, following the platform's own help pages; neither the CLI nor Claude handles your credentials. This is separate from `reachweaver connect --guide`. See [account advisor](ACCOUNT-ADVISOR.md).

## Step 4. The only things you do yourself

Run these in your own terminal, inside the workspace folder:

| When | Command | Why it is yours |
|---|---|---|
| You are ready to use a real account | `reachweaver connect --guide` | Walks you through the official sign-in and a limited credential, one account at a time. It needs a person at a terminal, and the credential stays in your environment, never in chat. |
| Claude shows you a draft you accept | `reachweaver show POST_ID`, then `reachweaver approve ...` | Approval is for one exact text and destination. A plan or a chat is never approval. |
| You want a real send | `reachweaver publish --post-id POST_ID --live` | Needs `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod` that only you set. Start with one text post on a throwaway account ([first live pilot](FIRST-LIVE-PILOT.md)). |

Claude tells you the exact command each time. Claude will not run `approve`, `publish --live`, `connect --guide` or `reconnect`, and will not loosen the approval settings. That is a deliberate safety rule, not a missing feature.

## Keeping it up to date

| What | How |
|---|---|
| Plugin | Claude tells you when a release is newer (one check to github.com at most every 24 hours, silent offline). Then `/plugin marketplace update reachweaver`, `/plugin update reachweaver@reachweaver`, `/reload-plugins`. |
| Package | `pipx upgrade reachweaver` |

They are separate copies of the same version, so update both. Your workspace folder is separate from the program, so updating never touches your data.

## What was and was not tested

The package install from PyPI, the workspace layout, `doctor` and `approval-mode --confirmation tty_hash_prefix` were run on 2026-10-17 in a scratch folder. The `claude plugin ...` terminal commands are listed by `claude plugin --help` (Claude Code 2.1.302) and the marketplace file passes `claude plugin validate`, but the install step itself was not run end to end here. Nothing in this page has sent or published anything.

## Next

[Start here](START-HERE.md) explains the ideas in plain words. [Using ReachWeaver with Claude Code](CLAUDE-CODE.md) covers who does what and how to keep usage low. [Troubleshooting](TROUBLESHOOTING.md) lists exact error messages.
