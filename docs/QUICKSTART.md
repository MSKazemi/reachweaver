# Quickstart: from clone to a safe preview, with no credentials

> **New to this?** Read [Start here](START-HERE.md) first for the ideas in plain words, and keep the [glossary](GLOSSARY.md) open for unfamiliar terms. You can also skip ahead: each step below says what you should see, so you always know whether it worked.

This walk-through takes about ten minutes. You need **Python 3.11 or newer** and git. You do **not** need an account, an API key, a token, `pip install` or a network connection after the clone. Nothing below can publish: every command is offline, and the one-post preview is a dry run.

`tests/test_onboarding.py` runs every `python3 -m reachweaver` line in this file, in order, against a fresh copy of the repository with an empty environment and every socket blocked. If a step here stops working, that test fails.

> On some systems `python` is not on `PATH`. Use `python3` as shown. See [troubleshooting](TROUBLESHOOTING.md) if a step fails.

## 1. Clone

```text
git clone https://github.com/MSKazemi/reachweaver.git
cd reachweaver
```

The runtime has no third-party dependencies, so there is nothing to install. All commands below run from the repository root. Local state goes to `state/`, which git ignores.

## 2. Look around: accounts, platforms and channels

```bash
python3 -m reachweaver --help
python3 -m reachweaver --help all
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json doctor
python3 -m reachweaver capabilities
python3 -m reachweaver distribution-plan --region global --goal contributors --limit 5
```

- `--help` prints the 16 everyday commands with one plain line each. `--help all` lists every command grouped by task, and `--help COMMAND` (or `COMMAND --help`) shows one command's options.
- `doctor` reads `config/accounts.example.json`, which holds public metadata only (handles, servers, environment-variable **names**). It lists the variables that would hold credentials as missing and ends with `Live opt-in: disabled`. That is the expected state.
- `capabilities` prints the dated capability registry. Only `bluesky`, `mastodon`, `telegram` and `linkedin` have live text adapters. Instagram, Threads, TikTok and YouTube have mock-tested adapters that stay disabled in the registry until the owner records a live receipt, so for now every other platform is manual. See [blocked platforms and their alternatives](TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives).
- `distribution-plan` ranks channels for a region and goal from a research registry. It is an editorial hypothesis, not a reach forecast, and it never schedules anything.

## 3. Check and preview a campaign file

```bash
python3 -m reachweaver campaign-check --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json
python3 -m reachweaver campaign-preview --file examples/campaign-preview.reachweaver.json --brand examples/brand-preview.reachweaver.json
```

`campaign-check` prints `VALID` plus editorial warnings. `campaign-preview` shows the exact text per channel, the length budget, links and hashtags, the registry capability and a rule-based confidence level. Neither command stages, approves or sends anything. A warning means "a person should look at this", not "blocked". Errors exit 2.

## 4. Draft, review, approve and preview one post

```bash
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json init
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json draft --account my-bluesky --file examples/example-post.txt --campaign quickstart
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json show POST_ID
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json check POST_ID
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json approve POST_ID --approver quickstart-reader
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json publish --post-id POST_ID
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json list
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json queue-health
python3 -m reachweaver --db state/quickstart.sqlite --accounts config/accounts.example.json approval-mode
```

- `init` creates `state/` with mode 0700 and the database with mode 0600.
- `draft` prints `Created draft POST_ID for my-bluesky; not published`. Use that ID in the next commands.
- `show` prints the exact text, the destination account, an offline Bluesky preview (grapheme count, facets, record key) and the hash that `approve` would bind.
- `approve` binds a SHA-256 of the exact payload. Any later change to the post blocks a send until it is approved again.
- `publish --post-id` is the **safe preview**. It prints `DRY RUN: 1 due approved posts; no requests sent` and the post stays `approved`. Without `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod` no send path runs at all.
- `approval-mode` shows two optional approval safeguards, both `off` here. Before a live pilot you can tighten them in your own terminal: `approval-mode --confirmation tty_hash_prefix` makes `approve` and `publish --live` require an interactive terminal and the first 8 characters of the payload hash typed by you, and `approval-mode --require-key` (with `REACHWEAVER_APPROVAL_SECRET` provided) signs every approval with your own key. `approval-mode --max-age-hours N` (off by default) makes an approval older than N hours expire, so the post must be re-approved before it can be sent; it can only be lowered later, never raised by a command. See the [first live pilot](FIRST-LIVE-PILOT.md) and [account onboarding](ACCOUNT-ONBOARDING.md#optional-the-owner-approval-key-7-d7).

Preview before you `schedule`: `publish --post-id` only selects posts that are approved **and due**, so a post scheduled for later is refused until its time comes.

## 5. Manual alternatives for platforms without an adapter

**Easier than creating JSON:** use `python3 -m reachweaver share --guide` from a real terminal, or `python3 -m reachweaver share --platform instagram,threads --text "Your caption" --out state/my-post`. This prepares handoff files with no account, database, or external request, for LinkedIn, Bluesky, Mastodon and Telegram too (copy-only). Over-long captions are shortened per network and marked SHORTENED, and `--attach` photos or videos are header-checked offline. See [Easy sharing](EASY-SHARING.md). When you set up your own accounts, `reachweaver account-add PLATFORM` writes a correct `config/accounts.json` entry without ever asking for a secret (`account-remove ID` takes one out again, keeping a `.bak`). Leave out `--accounts` before you have added one and the error says exactly that: `no accounts yet. Add one: reachweaver account-add bluesky ...`. A mistyped option, command or network gets a "Did you mean ...?", and a missing input file names the folder it looked in.

```bash
python3 -m reachweaver campaign-export --file examples/campaign.reachweaver.json --brand examples/brand.reachweaver.json --out state/quickstart-drafts
python3 -m reachweaver manual-export --platform threads --variant examples/manual-variant.threads.json --out state/quickstart-threads
```

- `campaign-export` writes text drafts plus `MANUAL-REVIEW.txt` to a new folder. You post them by hand.
- `manual-export` writes a creator package and checklist for Threads, Instagram, Facebook Pages, YouTube or TikTok, after checking sourced platform limits. Its status is always `manual_export_not_published`.
- Both refuse to write into an existing folder. Delete the folder or pick a new name to run them again.

Reddit and Hacker News are **human-only**: ReachWeaver writes no text for them. The full per-platform list is in [troubleshooting](TROUBLESHOOTING.md#blocked-platforms-and-their-alternatives).

## 6. What you did, and what you did not do

You created a local database, a draft, an approval bound to that exact text, a dry-run preview and two manual packages. You did **not** contact any social platform, store or read any credential, or publish anything. `health` would report the example accounts as `blocked` or `attention` for missing credentials, which is correct for this state.

Clean up when you are done:

```text
rm -rf state/quickstart.sqlite state/quickstart-drafts state/quickstart-threads
```

## Next steps

- Posting by hand already? Record a post with `reachweaver mark-shared --platform instagram --text '...' --url LINK --at yesterday`, add its numbers with `reachweaver metrics POST_ID --likes 40`, then `reachweaver report --week` and `reachweaver next` (what to post next: your ideas by name).
- Write your own campaign: [schemas](SCHEMAS.md), [campaign preview](CAMPAIGN-PREVIEW.md) and the Claude Code skill (`/reachweaver`, see the [README](../README.md#option-1-with-claude-code-recommended)).
- Before any live send, go through the [security checklist](SECURITY-CHECKLIST.md), then [account onboarding](ACCOUNT-ONBOARDING.md) and the [first live pilot](FIRST-LIVE-PILOT.md) (Bluesky: the [one-post pilot runbook](BLUESKY-PILOT.md)). A live send needs your own account, credentials entered privately on your machine, an approved post, `--live`, `REACHWEAVER_ALLOW_LIVE=1` **and** `REACHWEAVER_ENV=prod` (set only on the machine you publish from):

```text
# Only after the security checklist, for one approved post, on your own production machine:
# REACHWEAVER_ENV=prod REACHWEAVER_ALLOW_LIVE=1 python3 -m reachweaver publish --live --post-id POST_ID
```

- Skill-only install? The repository's `examples/` and `config/` files are in the skill's `assets/` folder (`config/accounts.example.json` is `assets/accounts.example.json`, `examples/example-post.txt` is `assets/example-post.txt`); start with `python3 <installed-skill>/scripts/run.py doctor`, and copy `assets/accounts.example.json` to `config/accounts.json` in your project.
- Copied the skill into another project? Run the same commands through `python3 <installed-skill>/scripts/run.py` from that project's directory.
