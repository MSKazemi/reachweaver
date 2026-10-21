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
reachweaver approval-mode --max-age-hours 168
```

The first command creates `config/`, `state/`, `media/`, `logs/`, a `.gitignore` that keeps secrets out of git, and `secrets.env.example` (credential names only). The `curl` line is a safety net and only runs when `config/accounts.json` is missing: it fetches the example accounts file (public handles, no secrets; edit it later to list your own accounts). Releases after 0.5.2 carry that file inside the package, so `workspace --init` creates it and the line does nothing; 0.5.2 from PyPI does not, and `doctor` then stops with "Missing .../config/accounts.json". The last command makes `approve` and `publish --live` work only in a terminal where you type the first characters of the post's hash, so an AI session running commands through pipes is refused. It is strong friction, not a cryptographic boundary (a program can open a pseudo-terminal); the owner approval key in [account onboarding](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7) is the stronger control. The next command makes an approval expire: a post approved more than 168 hours (7 days) ago is not sent until you approve it again, so an approval you forgot about cannot go out weeks later. Pick a number at least as long as the longest gap you plan between approving and the scheduled time; `schedule` refuses a due time after the approval expires. The limit can only be lowered from the command line, and turning the confirmation off needs the same typed check, so an assistant cannot quietly loosen either. Both are stored in this workspace's database, so repeat them for each new workspace.

Claude can do the first steps for you if you prefer; run the two `approval-mode` lines yourself.

### Recommended approval settings for a computer you sit at

These are the settings recommended after the 2026-10-21 security review, for the laptop or desktop where you approve posts yourself:

| Setting | Command | Status |
|---|---|---|
| Type the hash prefix for every approval and live send | `reachweaver approval-mode --confirmation tty_hash_prefix` | Recommended: on |
| Approvals expire after N hours | `reachweaver approval-mode --max-age-hours N` (for example 168; at least your longest scheduling lead) | Recommended: on |
| Owner approval key (`REACHWEAVER_APPROVAL_SECRET`) | see [account onboarding](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7) | Deferred: set it up when you run a separate worker host that sends without you |

Check what is on with `reachweaver approval-mode`. The confirmation is friction against a process that runs commands through pipes, not proof that a person is present; the approval key, which an assistant cannot read, is the stronger control once a separate worker host exists.

### Approve several drafts one by one

When a campaign leaves several drafts, run this in your own terminal instead of copying each post id:

```bash
reachweaver review --campaign CAMPAIGN_ID --approver YOUR_NAME
```

It takes the drafts oldest first. For each one it prints the same review sheet as `show` and the result of `check`, then asks on your terminal: `a` approves that one post (through the normal `approve` command, with all its checks; with the confirmation above on, you also type the hash prefix), `s` skips it, `c` cancels it after you type `CANCEL`, `q` stops. An empty or unknown answer never approves anything. It refuses to start without a terminal, so Claude cannot run it, and it never approves a repeat of something already shared (use `approve POST_ID --allow-repeat` yourself when that is intended). Reply drafts are not included; approve them one at a time. At the end it lists the ids it approved, skipped, cancelled and refused, and the next command (a dry run with `publish --post-id`, or `schedule`). `--account ID` limits it to one account; `--optout`, `--sources`, `--persona` and `--verify-identity --online` are passed to each check and approval.

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
| Claude shows you a draft you accept | `reachweaver show POST_ID`, then `reachweaver approve ...`; for several drafts, `reachweaver review` | Approval is for one exact text and destination. A plan or a chat is never approval. |
| You want a real send | `reachweaver publish --post-id POST_ID --live` | Needs `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod` that only you set. Start with one text post on a throwaway account ([first live pilot](FIRST-LIVE-PILOT.md)). |

Claude tells you the exact command each time. Claude will not run `approve`, `review`, `publish --live`, `connect --guide` or `reconnect`, and will not loosen the approval settings. That is a deliberate safety rule, not a missing feature.

## Keeping it up to date

| What | How |
|---|---|
| Plugin | Claude tells you when a release is newer (one check to github.com at most every 24 hours, silent offline). Then `/plugin marketplace update reachweaver`, `/plugin update reachweaver@reachweaver`, `/reload-plugins`. |
| Package | `pipx upgrade reachweaver` |

They are separate copies of the same version, so update both. Your workspace folder is separate from the program, so updating never touches your data.

## What was and was not tested

The package install from PyPI, the workspace layout, `doctor` and `approval-mode --confirmation tty_hash_prefix` were run on 2026-10-17 in a scratch folder. The `claude plugin ...` terminal commands are listed by `claude plugin --help` (Claude Code 2.1.302) and the marketplace file passes `claude plugin validate`, but the install step itself was not run end to end here. `approval-mode --max-age-hours` and `review` are covered by the offline test suite (`tests/test_review_loop.py` drives `review` with a simulated terminal); neither was run against a real account. Nothing in this page has sent or published anything.

## Next

[Start here](START-HERE.md) explains the ideas in plain words. [Using ReachWeaver with Claude Code](CLAUDE-CODE.md) covers who does what and how to keep usage low. [Troubleshooting](TROUBLESHOOTING.md) lists exact error messages.
