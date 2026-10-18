# Personas, usernames and the account kit

Two problems this solves:

1. **The wrong account.** You run more than one identity on a network, for example an Instagram account for work and one for family. You write a post for work, but the account you aimed it at is the family one. ReachWeaver now says so and names the account to switch to.
2. **A network you are not on yet.** You want an account on a network (perhaps one with a language or sign-up form you do not know). ReachWeaver prepares everything it can from a private profile file, in the platform's language, so you spend a few minutes pasting and verifying instead of working out the form. You approve the whole plan once.

Everything here is offline. It opens no site, fills no form and sends nothing. **You still create each account yourself**, and you complete every CAPTCHA, code, two-factor step and identity check. See [what it does not do](#what-it-does-not-do) for why.

## 1. Your profile (`config/profile.json`)

A private file on your computer. It holds who you are, your personas, and one username per network and persona. It is in `.gitignore` and is written with mode 600 (only you can read it).

```bash
reachweaver profile --init --persona work --persona family
reachweaver profile --set person.given_name=Alex --set person.family_name=Example \
                    --set person.email=alex@example.org --set person.phone=+390000000000 \
                    --set person.country=IT --set person.languages=en,zh-Hans
reachweaver profile --set personas.work.display_name="Alex Example" \
                    --set personas.work.display_name.zh-Hans=亚历克斯 \
                    --set personas.work.bio.en="I build open-source tools." \
                    --set personas.work.username_stems=alexexample \
                    --set preferences.default_persona=work
reachweaver profile --handle instagram:work=alex.work --handle instagram:family=alex.fam
reachweaver profile --show      # e-mail, phone and birth date are partly hidden
reachweaver profile --check     # validates the file, its permissions and its agreement with accounts.json
```

`config/profile.example.json` is a synthetic example of the whole shape. In Claude Code you can simply ask: Claude asks you the missing questions in the chat and saves your answers with `profile --set`.

| Section | Fields |
|---|---|
| `person` | `given_name`, `family_name`, `native_name`, `email`, `phone` (international form), `birth_date`, `country`, `timezone`, `languages` |
| `personas.ID` | `display_name` and `bio` (a text, or one per language), `email`, `username_stems`, `website`, `avatar` |
| `handles` | one entry per network and persona: `platform`, `persona`, `username`, `status` (`planned`, `claimed` or `unavailable`) |
| `preferences` | `default_persona` |

A handle's status is **your statement**. ReachWeaver never checks whether an account exists or a username is free.

**What the file refuses.** The file is a closed schema, so nothing else creeps in. It refuses any key that looks like a password, token, cookie, one-time or recovery code, passport or national id, card or bank number, or tax code. Keep those in your password manager. Error messages never repeat a value.

## 2. Say which persona a post is for

```bash
reachweaver draft --account ig-family --persona work --text "New release is out"
# ERROR: Draft refused, nothing created: Wrong account for persona 'work': account ig-family (@alex.fam) is your
# 'family' persona on instagram. Switch to account ig-work (@alex.work). Nothing was changed.
```

`--persona` also works on `check` and `approve`, and with several `--account` values (nothing is created if any one is wrong). If you set `preferences.default_persona`, it applies when you pass no flag; pass `--persona family` to override it for one post.

How an account's persona is known:

1. its `"persona": "work"` field in `config/accounts.json` (public metadata, like `handle`), or
2. its username matched against the usernames in your profile.

If neither tells, the guard **refuses** and tells you which line to add. You asked for a check, so "unknown" is not a pass. The guard only ever adds a refusal. It does not change the approval hash, the dry-run gate, the emergency stop or credential handling.

Not sure which account to use?

```bash
reachweaver account-route --platform instagram --persona work
# work on instagram: ok. Use account ig-work (@alex.work).
reachweaver account-route --platform weibo --persona work
# work on weibo: missing. No work account or username on weibo yet. Run: reachweaver account-kit --platform weibo --persona work
```

Exit code 0 means a username is known; 1 means it is missing, or the profile and `accounts.json` disagree.

For posts you make by hand in an app (`share`, `manual-export`), the risk is the app being signed in to the other account. Run `account-route` first and switch the app to the username it prints.

## 3. Create a missing account: the account kit

```bash
reachweaver account-kit --persona work --platform instagram --platform weibo
```

This prints one plan for all the networks you name and writes nothing. For each network it shows the official page (you open it yourself), the language of its form, and the details it may ask for. E-mail, phone and birth date appear partly hidden. If something required is missing you get a list of questions, and `--questions` prints just that list:

```bash
reachweaver account-kit --persona work --platform weibo --questions
```

Answer them with `profile --set` (in Claude Code, Claude asks you in the chat). When the plan has no open questions it ends with a **plan hash**. Your single approval, for every network in the plan, is that hash:

```bash
reachweaver account-kit --persona work --platform instagram --platform weibo \
    --out signup-kit --approve-plan 0123456789ab
```

That writes a **new** private folder (0700, files 0600) with one `SIGNUP.md` and `fields.json` per network. `SIGNUP.md` has:

- the values to paste (display name; username candidates, or the nickname for platforms that use one; e-mail; phone and birth date only where the platform may ask; bio and website),
- the form labels in the platform's language, so you can read the form,
- the steps that stay with you,
- the command that records the account afterwards: `reachweaver profile --handle weibo:work=THE_NAME`.

Delete the folder when you are done: it contains personal data.

The `--approve-plan` flag contains `approve`, so in Claude Code the project's deny rule for `approve` applies to it: **you** run that one command in your terminal (or with `!` in the prompt), not Claude. That is deliberate: the approval is yours, as it is for posts.

### Languages

The interface language comes from a small reviewed registry (`reachweaver/signup_registry.json`):

| Platforms | Form language | Name used |
|---|---|---|
| Weibo, Xiaohongshu, Douyin, Bilibili, WeChat, WeChat Channels, Zhihu, Juejin, CSDN, OSCHINA, Gitee | Simplified Chinese | your `display_name` for `zh-Hans`, else English |
| Qiita, Zenn, note, Niconico | Japanese | your `ja` display name, else English |
| Naver Blog and Cafe, Kakao OpenChat, BAND | Korean | your `ko` display name, else English |
| everything else | English | username candidates |

If your bio exists only in English, the plan lists an optional question for the local-language bio. In Claude Code you can ask Claude to draft one from your English text; you review it and save it with `profile --set personas.work.bio.zh-Hans=...`. The form-label word lists are ReachWeaver's own, not taken from the platforms: have a fluent reader confirm a label you rely on.

### Username candidates

Up to six, best first: names you already claimed for this persona elsewhere (so your identity stays consistent; domain-style handles such as `name.example.org` are skipped), your `username_stems`, then names built from your first and last name. Names you marked `unavailable` for that network are left out. Availability is never checked: the form decides.

### What is prepared, and why only that

`signup_registry.json` lists, per network, the details a sign-up form may ask for. These are editorial expectations (`status: expected`), used only to keep personal data out of a packet that does not need it: a phone number is prepared only for networks where it is expected, a birth date likewise. They are not platform rules and not a statement that sign-up is open to you. Read the platform's own pages. Chinese platforms commonly ask for a mainland-style mobile number and some check real identity before you can post; ReachWeaver has not verified the current rule of any platform. If a form asks for a phone number or document you do not have, stop. Do not borrow another person's, buy a number or an account, or use a bypass.

## What it does not do

- **It does not create the account, fill the form or drive a browser.** The owner's decision (2026-10-16, [browser-assist decision](BROWSER-ASSIST-DECISION.md)) is that no tool operates platform sites, and LinkedIn's and TikTok's terms ban automated methods. Automated sign-up would also have to handle passwords, CAPTCHAs, SMS codes and identity checks, which are exactly the controls that must stay with you, and many platforms prohibit automated account creation. The kit therefore does everything up to those steps and stops.
- **It never sees a password.** Create it yourself in your password manager.
- **It does not check whether a username is free** and does not verify that an account exists.
- **It does not create bulk, fake or impersonating accounts.** One persona is one real identity of yours.
- **It grants no permission to post.** A new account is not connected. Connecting and approving posts stay as described in [account onboarding](ACCOUNT-ONBOARDING.md).

If a platform's own terms later allow assisted form filling, that is a separate owner and legal decision with a dated reading of the terms; see "What would change this" in the browser-assist decision.

## Privacy

`config/profile.json` is a new local file with personal data: name, e-mail, phone, birth date, country, timezone, languages, bios and usernames. Purpose: preparing the sign-up details and the wrong-account check. It stays on your computer: no command here makes a network request, and nothing is added to the SQLite database, `data-export` or any log. Account-kit folders hold the full values; they are yours to delete (`rm -r signup-kit`). Deleting the profile deletes everything this feature stores. The file is ignored by git; do not commit it or paste it into an issue. Claude Code, if you use it, sees what you ask it to read or type in the chat; keep passwords and codes out of the chat. See also the [privacy notice](PRIVACY.md).

## Commands

```text
reachweaver profile [--init [--persona ID]...] [--show] [--check] [--set PATH=VALUE]... [--handle PLATFORM:PERSONA=NAME[:STATUS]]...
reachweaver account-route --platform P [--persona ID] [--json]
reachweaver account-kit --platform P... [--persona ID] [--questions] [--out NEW_DIR --approve-plan SHA256] [--json]
reachweaver draft|check|approve ... [--persona ID]
reachweaver --profile FILE ...        # default: config/profile.json (inside the workspace folder, if you use one)
```

Status: mock-tested and offline (`tests/test_profile.py`, `test_persona_guard.py`, `test_account_kit.py`, `test_persona_cli.py`). No platform sign-up has been performed with it.
