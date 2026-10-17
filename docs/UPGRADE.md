# Upgrade ReachWeaver — copy-and-paste commands

The **Claude Code plugin** and the **pipx Python CLI** are separate installations. Updating one never updates the other. Choose the section matching your setup.

## Recommended: both plugin and pipx

Run in your **normal terminal**, outside the Claude Code conversation:

```bash
claude plugin marketplace update reachweaver
claude plugin update reachweaver@reachweaver
pipx upgrade reachweaver
```

Then inside Claude Code, enter `/reload-plugins` or restart Claude Code. Verify from your normal terminal:

```bash
claude plugin list
pipx list
reachweaver version
```

The marketplace command refreshes its listing; it does not by itself upgrade the installed plugin. Updating is not publishing: none of these commands connect social accounts or send posts.

### Update just the plugin

In your normal terminal:

```bash
claude plugin marketplace update reachweaver
claude plugin update reachweaver@reachweaver
```

In Claude Code: `/reload-plugins` or restart.

### Update just the CLI installed via pipx

```bash
pipx upgrade reachweaver
reachweaver version
```

First install (not upgrade): `pipx install reachweaver`. If the command is missing after installation, run `pipx ensurepath` and open a new terminal. Do **not** confuse `pipx upgrade reachweaver` with upgrading the pipx utility itself.

## Standalone skill, without Claude plugin

If installed in your home directory using `scripts/install_skill.py`:

```bash
python3 "$HOME/.claude/skills/reachweaver/scripts/run.py" update --check
# Review the release first; run the next line only if you approve:
python3 "$HOME/.claude/skills/reachweaver/scripts/run.py" update --apply --yes
```

Restart Claude Code. The skill updater verifies the release checksum and keeps a previous copy.

For a **project-local skill**, run in the desired project directory:

```bash
curl -fsSLO https://raw.githubusercontent.com/MSKazemi/reachweaver/main/scripts/install_skill.py
# Inspect the downloaded script before executing:
python3 install_skill.py --project
```

The standalone skill update command cannot update a Claude plugin, a pip/pipx installation or a source checkout. An in-place skill update also refuses to modify files inside a Git checkout.

## Other installation types

| Installation | Upgrade |
|---|---|
| Source cloned with Git | `git pull --ff-only` inside the clone; developers should run `python3 scripts/sync_skill.py` to refresh skill/plugin mirrors |
| Installed with pip (not pipx) | `python3 -m pip install --upgrade reachweaver` in the environment used for installation |
| Installed with pipx from a specific wheel or Git source | Explicitly install a newer wheel or ref; do not assume a normal PyPI upgrade changes that pinned source |
| ZIP copied manually | Obtain a newer trusted release ZIP, verify checksum, and replace the skill manually |

**Never run `reachweaver update --apply --yes` to update a plugin or pipx package.** It only replaces a standalone skill installed outside a Git checkout.

See [installation](INSTALL.md), [recommended setup](RECOMMENDED-SETUP.md), and [troubleshooting](TROUBLESHOOTING.md).
