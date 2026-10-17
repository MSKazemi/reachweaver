# How to install ReachWeaver: pick one route

You need **Python 3.11 or newer**. ReachWeaver has no other dependencies. Every route below installs the same program; they differ in *where it lives* and *who keeps it up to date*. Nothing here connects an account or publishes anything.

## The short answer

**Updating an existing installation?** See [Upgrade ReachWeaver: exact commands](UPGRADE.md). For the recommended Claude Code plugin + pipx setup, run `claude plugin marketplace update reachweaver`, `claude plugin update reachweaver@reachweaver`, and `pipx upgrade reachweaver` in your normal terminal, then `/reload-plugins` in Claude Code.


Want the full step-by-step version of the best route? See the [recommended setup](RECOMMENDED-SETUP.md).

**Use Claude Code? Do this, and you are done:**

```text
/plugin marketplace add MSKazemi/reachweaver
/plugin install reachweaver@reachweaver
```

Then type `/reachweaver` and describe what you want. Claude Code downloads the plugin, keeps it in its own folder and updates it for you.

**Plan to connect real accounts and publish?** Also run this once in your own terminal, because the plugin lives inside Claude Code and does not put a `reachweaver` command on your terminal's path:

```bash
pipx install reachweaver
```

You need that command for the steps that must be yours (`reachweaver connect --guide`, `approve`, `publish --live`). Without it, you can still plan, draft, check and dry-run everything through Claude.

## Which route should I use?

| If you... | Use | Install | Updates |
|---|---|---|---|
| use **Claude Code** and want the least work | **A. Claude Code plugin** | the two `/plugin` lines above | Claude Code tells you; `/plugin marketplace update reachweaver`, then `/plugin update reachweaver@reachweaver` |
| use Claude Code **and will publish for real** | **A + B together** | plugin, then `pipx install reachweaver` | plugin as above; `pipx upgrade reachweaver` |
| want only the **command line**: a server, a script, a cron job, no Claude | **B. Python package** | `pipx install reachweaver` (or `python3 -m pip install reachweaver`) | `pipx upgrade reachweaver` |
| use Claude Code but **cannot use plugins** (blocked by policy, or no GitHub access from the plugin system) | **C. Standalone skill installer** | `curl -fsSLO https://raw.githubusercontent.com/MSKazemi/reachweaver/main/scripts/install_skill.py`, then `python3 install_skill.py` | Claude asks at session start; `run.py update --check`, then `update --apply --yes` |
| want the skill **only inside one project**, not for all your projects | **C** with `--project` | `python3 install_skill.py --project` | same as C |
| want to **read the files before anything is installed**, or are on a machine with no direct GitHub access | **D. Skill ZIP** | download `skill.zip` and `skill.zip.sha256` from the [latest release](https://github.com/MSKazemi/reachweaver/releases/latest), check the hash, unpack into `~/.claude/skills/reachweaver` | install a newer ZIP by hand |
| just want to **try it for ten minutes, offline, with no account** | **E. git clone** | `git clone https://github.com/MSKazemi/reachweaver.git` then follow the [quickstart](QUICKSTART.md) | `git pull` |
| want to **read the source, run the tests, or contribute** | **E. git clone** | clone, then `python3 -m pip install -e .` | `git pull` |
| want a **pinned file you can checksum** (the exact wheel of one release) | **B** from the release page | download the wheel and `python-dist.sha256` from the [releases](https://github.com/MSKazemi/reachweaver/releases), check with `sha256sum --check`, then `pipx install ./the-wheel-file.whl` ([details](PACKAGING.md)) | install a newer wheel by hand |

If two rows fit you, pick the one higher in the table. Most people need only **A**.

## What each route gives you

| | A. Plugin | B. Python package | C. Skill installer | D. Skill ZIP | E. git clone |
|---|---|---|---|---|---|
| `/reachweaver` in Claude Code | yes | no | yes | yes | yes, when you start Claude Code inside the clone |
| `reachweaver` command in your terminal | no | **yes** | no | no | after `pip install -e .` |
| Updates itself or tells you | yes | no (you run `pipx upgrade`) | tells you, applies only after you agree | no | no (`git pull`) |
| Needs a clone of the repository | no | no | no | no | yes |
| Needs network access to install | GitHub | PyPI (or the GitHub release) | GitHub | GitHub, once | GitHub |
| Includes tests and the full docs | no | no | no | no | yes |

## Questions people ask

**Do I need both the plugin and the package?** No. The plugin is enough to plan, draft, check and dry-run. Add the package only when you want the `reachweaver` command in your own terminal, which you need to connect accounts and to approve and send real posts. The skill tells Claude never to run `approve` or `publish --live`; those are yours.

**Can the two coexist?** Yes. They are separate copies of the same version. Update both when a new release comes out. Your data is separate from the program in both cases: it lives in your workspace folder ([your workspace folder](MOVING.md)), not inside the plugin.

**Where does my data go?** On your computer, in `config/` and `state/` (or your workspace folder). No route uploads it anywhere. The plugin and the skill installer make one small request to github.com at most every 24 hours to check for a newer release; it is silent when offline. Turn it off with `touch ~/.cache/reachweaver/no-update-check`. See the [privacy policy](PRIVACY.md).

**Is anything here paid?** No. ReachWeaver makes no model calls and needs no API key; Claude does the writing inside your own Claude Code plan ([how that works](CLAUDE-CODE.md)).

**`/reachweaver` is not found.** Restart Claude Code after installing, and check the plugin is listed under `/plugin`. More in [troubleshooting](TROUBLESHOOTING.md).

**`reachweaver: command not found` after `pipx install`.** Run `pipx ensurepath`, then open a new terminal.

**Does `reachweaver update --apply` update my plugin or pip install?** No, on purpose. It only replaces a skill bundle installed by route C. Update a plugin through `/plugin` and a pip install through `pipx upgrade reachweaver`.

## Next step

Whatever route you picked: [Start here](START-HERE.md), then the [quickstart](QUICKSTART.md), then [using ReachWeaver with Claude Code](CLAUDE-CODE.md). Details of the Python package and how it is checked: [The Python package](PACKAGING.md).
