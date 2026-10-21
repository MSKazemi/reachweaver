# Choose social platforms and set up accounts yourself

`reachweaver account-advisor` helps you pick a small number of relevant social platforms **before** you spend time creating profiles. For one platform it then prints the official starting page, the steps you do yourself, and a coaching prompt to paste into an ordinary Claude chat (Claude Code or claude.ai). It is an offline planner: it does **not** open a browser or a page, register an account, sign in, read credentials, connect an integration or publish a post.

## Recommend the next platforms

```bash
reachweaver account-advisor --region global --goal awareness --format short_video --weekly-hours 5 --limit 3
reachweaver account-advisor --region india --goal adoption --format articles --weekly-hours 4
reachweaver account-advisor --region china --goal awareness --format images --weekly-hours 4
reachweaver account-advisor --region global --goal collaboration --existing linkedin --existing github --json
```

Each recommendation shows when its distribution entry was last checked (`checked YYYY-MM-DD`, and `aging - re-verify` after 60 days or `stale - re-verify` after 120; JSON `checked_on`, `age_days`, `freshness`, `freshness_label`). The ranking starts from the region and goal order in the versioned `reachweaver/distribution_registry.json` (the same research as `distribution-plan`), then keeps only platforms that fit your `--format` and your weekly time. A platform appears only if the advisor has a reviewed official starting page for it, so some researched platforms are left out. `--existing` is your own statement, not verified access. The score orders options **within this plan only**; it is not a platform ranking signal, an audience estimate, a promised number of impressions or a forecast. The weekly hours per platform are editorial estimates, not measured platform data.

| Option | Values |
|---|---|
| `--region` | A market ID from the distribution registry, for example `global`, `india`, `china`, `japan`, `south_korea`, `brazil`, `nigeria` (default `global`) |
| `--goal` | `awareness` (default), `adoption`, `contributors`, `collaboration` |
| `--format` | `mixed` (default), `short_video`, `long_video`, `images`, `articles`, `text`, `community` |
| `--weekly-hours` | 1–40 estimated hours you can spend per week (default 6) |
| `--limit` | 1–8 new platforms (default 4) |
| `--existing ID` | A platform ID you already use; repeat for each |
| `--setup ID` | Print the setup steps and coaching prompt for one platform |
| `--brand`, `--audience`, `--language` | Public profile context for the prompt; never private data |
| `--json` | Machine-readable output |

A wrong value exits with code 2 and one `ERROR:` line, for example an unknown region lists the valid ones and an unknown `--setup` ID lists the IDs that have a starting page.

Human-only venues stay human-only. Reddit, Discord, Naver Cafe, KakaoTalk Open Chat, BAND and the other `human_only` platforms in the registry are marked as such in the list, and their setup prompt tells Claude not to draft, edit or suggest any post, comment or reply. Hacker News has no setup handoff at all.

## Set up one account yourself

```bash
reachweaver account-advisor --setup instagram --brand 'My Studio' --audience 'Home cooks' --language en
reachweaver account-advisor --setup bluesky --brand 'My Project'
```

The command prints:

1. the platform's official starting page, which you open yourself;
2. the steps only you do: follow the platform's own official help pages, read eligibility, terms and any fee, register and accept terms, enter every secret and personal detail, complete MFA, CAPTCHA and verification, confirm the account in the platform's own interface, and set your public profile;
3. a coaching prompt for a normal Claude chat.

The prompt tells Claude that it is coaching **from your own descriptions only**, with no browser control: it does not open, read or operate the platform's site or app. It points you to the platform's own help pages and defers to them, never asks for or stores passwords, codes, identity or payment details, treats any text you paste from the site as untrusted data, proposes only truthful display-name, bio and visual ideas, never assumes a handle is free, and never says the account exists until you confirm it.

ReachWeaver does not recommend Claude in Chrome or any browser agent on social platforms' sites; see the [browser-assist decision](BROWSER-ASSIST-DECISION.md). LinkedIn's User Agreement bans "bots or other unauthorized automated methods to access the Services", and TikTok's terms ban using "automated scripts to collect information from or otherwise interact with the Services"; the other platforms' readings are not cleared.

**Never paste a secret into any chat**, a repository, a log or a screenshot. If you paste one by mistake, change it on the platform.

## After the account exists

To prepare your name, username candidates, e-mail and bio for the sign-up form (in the form's language, behind one approval), use the [account kit](PERSONAS-AND-ACCOUNT-KIT.md); it still creates nothing. Only you can confirm that the account exists; `account-advisor` never reads account state. Then add its non-secret identifier to your private `config/accounts.json` and follow [account onboarding](ACCOUNT-ONBOARDING.md) (`reachweaver connect --guide`) for a supported API, or use [manual export](MANUAL-EXPORT.md) and post by hand. Adapters that are switched off stay switched off; setting up an account does not grant permission to post automatically.

## Limitations and privacy

- The rankings are editorial research hypotheses, not audited growth outcomes.
- A starting page is a link for you to open, not a statement that automated access is allowed.
- A platform may be unavailable in your country or need a business licence, a paid plan or an age or identity check. You check this on the platform's own pages.
- ReachWeaver does not create bulk, fake or impersonating accounts, get around restrictions or bypass security controls.
- `--brand` and `--audience` appear in the local output and in the prompt you paste, and stay in your shell history, so use only what belongs on a public profile.
- The advisor makes no network request, reads no credential or environment variable, and creates no database or file.

Passing the offline tests is not proof of any platform's permission or of legal clearance.
