# ReachWeaver privacy notice

**Updated 14 October 2026.** This notice documents the behavior of the open-source ReachWeaver v0.2 software and bundled Claude Code plugin, not the separate practices of Claude/Anthropic, GitHub, social networks, your device or independently configured services.

## 1. Local-first software; who operates it

ReachWeaver runs on the operator's own computer and does not currently provide a maintainer-hosted social publishing service. The project does not intentionally collect telemetry or transmit local draft content, databases or social account credentials to its maintainer. The software may, however, communicate with GitHub and third-party services as described below. Connection metadata can be observed by those providers even when content is not uploaded.

For an independently operated installation, the local operator normally decides which accounts, posts, sources and personal data to process. The maintainer does not direct each operator's independent processing decisions merely because it distributes the code. Whether any person or organization is a GDPR controller or processor depends on **actual purposes, means and operations**, not merely the wording of this notice. A maintainer may separately receive data voluntarily supplied in private support or security correspondence and must handle that correspondence under applicable law. The maintainer cannot inspect or erase data that remains exclusively on an independent operator's device. Downstream forks, hosted adaptations and separately configured third-party integrations may use different data flows that their operators must disclose accurately.

## 2. Information stored locally

Depending on the commands and data supplied by the operator, ReachWeaver can process or retain:

| Category | Typical location and contents |
| --- | --- |
| Posts and approval records | Local SQLite database, normally `state/reachweaver.sqlite`: draft, scheduled and published text; account and platform ids; campaign/source references; approval labels (an OS login or entered name); timestamps; hashes; state, errors and remote post identifiers/links |
| Replies and media | Operator-authored outgoing reply text and reply-target identifiers may appear in post records; attachment/video metadata and paths may be stored. This is different from keeping a scraped database of incoming third-party replies |
| Campaign and measurements | Local campaign evidence, brand/profile files, native metric imports, aggregate counters and limited event records |
| Imported third-party data | Operator-provided exports for optional analysis/engagement triage and resulting draft files; files may contain personal data about other people even if the raw input is not imported into SQLite |
| Contact preferences | Locally maintained opt-out handles/domains; those identifiers can be personal data |
| Software cache | Release-check results, timestamps and an opt-out marker under `~/.cache/reachweaver/` |
| Profile file (optional) | `config/profile.json`, created only if you run `profile --init`: your name, e-mail, phone, birth date, country, timezone, languages, persona bios and usernames, written by you for the wrong-account check and the account kit. Local only (mode 600, ignored by git); no command reads it over a network, and it is not in SQLite or `data-export`. `account-kit --out` copies the values a platform's sign-up form may ask for into a folder you name; delete both when done. It refuses passwords, tokens, one-time codes and identity-document numbers |
| Credentials | Normally read at the point of an authorized online operation from user-set environment variables or the configured systemd credentials directory; **not intentionally stored in SQLite** |

Some content can identify individuals. Operators should minimize private or third-party information, protect local files, avoid adding secrets to prompts and exports, and use data only where they have an appropriate legal basis.

## 3. Network activity, recipients and AI providers

Offline planning, validation and dry-run commands are designed to avoid network requests. The following behavior is important:

1. **Session-start release notice:** a Claude Code plugin hook checks the latest ReachWeaver release through GitHub's HTTPS API no more than once per 24 hours under its local cache policy, unless disabled using `touch ~/.cache/reachweaver/no-update-check`. It does not intentionally upload post text, SQLite state or credentials. GitHub can receive an IP address, request time and a `reachweaver/<version>` user agent. GitHub's separate [privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) applies.
2. **Owner-initiated live sends:** the operator must satisfy the live opt-ins, configure account credentials and review/approve each eligible post. Selected external platform APIs (for example Bluesky, Mastodon, Telegram or LinkedIn) receive the data needed for the send, including approved content, relevant media and authentication material. The operator may also configure a Postiz publishing backend. Once published, material can become publicly available and subject to each provider's retention and moderation rules.
3. **Explicit online commands:** identity checks, remote media checks, source/landing-page retrieval and manual update/download commands can send requests to configured providers or destinations.
4. **AI assistant:** prompts, code, files and tool results made available to Claude Code are handled under the user's agreement and privacy settings with Anthropic, **not** under ReachWeaver's control. The plugin does not control the AI provider's retention practices.
5. **Direct support:** if someone voluntarily emails or privately reports a problem to the maintainer, the maintainer may receive the name, email and details supplied. Avoid including secrets or unnecessary personal data.

The maintainer does not sell the locally stored data or run an advertising-tracking service through ReachWeaver. This does **not** assert that third-party platforms cannot log or otherwise process network metadata.

## 4. Retention, export and deletion

**Local data is retained; installation is not a zero-retention service.** The SQLite database and manually created files can persist indefinitely without user action. To inspect and control it:

```sh
reachweaver privacy-status
reachweaver data-export --out state/export.json
cp config/retention.example.json config/retention.json
reachweaver data-purge            # review what could be minimized
reachweaver data-purge --apply    # optional, explicitly authorized local changes
```

The configured policy applies only where the command permits. Active, scheduled and unresolved sends are preserved; published-content text may be minimized while minimal IDs and fingerprints remain for safety, reconciliation and duplicate-send protection. Some local files, backups and exports are outside the purge mechanism. Removing the project database or cache does **not** automatically remove backups, other user files, source-control history, data already provided to an AI assistant, or material stored/published by external platforms.

The [technical data lifecycle](PRIVACY-LIFECYCLE.md) explains precise records, safeguards, export and purge behavior. Operators must separately handle deletion and retention on external platforms and backups where applicable. The developer's software notice cannot bind independent providers or cause them to delete their own copies.

## 5. Third-party rights and contact

A person mentioned in an operator's posts may have privacy, objection, erasure and other rights under applicable law. The **operator of the relevant account** normally controls that local data and should provide appropriate notices, handle requests and use platform tools for remote content. The maintainer cannot fulfill a local-data erasure request for a database that the maintainer never received.

For general questions, use [GitHub issues](https://github.com/MSKazemi/reachweaver/issues) without posting confidential information. To contact the maintainer privately about information the maintainer **actually received**, use the existing private contact route in the [security policy](../SECURITY.md) with the subject `ReachWeaver privacy`. Never publicly post access tokens, personal identifiers or private drafts. Individuals may also have the right to complain to their relevant data-protection supervisory authority.

## 6. Children, security and changes

ReachWeaver is not directed to people under 18; it does not claim to perform reliable age verification. Operators should secure their devices and secrets and review the [security policy](../SECURITY.md). Security controls and tests reduce risks, but no security guarantee is made. This notice may be revised with code changes; consult the version shipped with your installed code and its git history.

See also the [Apache-2.0 license](../LICENSE) and [warranty/responsibility notice](../DISCLAIMER.md). These documents do not override mandatory law.
