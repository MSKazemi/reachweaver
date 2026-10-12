# ReachWeaver privacy notice

**Effective:** 12 October 2026  
**Software:** ReachWeaver v0.2 experimental Claude Code plugin  
**Maintainer contact:** mohsen.seyedkazemi@gmail.com  
**Code:** https://github.com/MSKazemi/reachweaver

This notice describes ReachWeaver's own behavior. It does not replace the privacy notices of Claude/Anthropic, GitHub, your chosen social networks, or any separately configured publishing service. ReachWeaver is a locally executed, open-source tool; it does not currently offer a maintainer-hosted social-media service. Use is optional.

## Where information is processed

Local planning, drafting, review, metrics import, and dry-run publishing generally run on the operator's machine. A local SQLite database (normally `state/reachweaver.sqlite`) records post text (including potentially identifiable names or quotations), account and platform identifiers, campaign and source information, approval labels, timestamps, send state, destination URLs/IDs, limited event history, campaign evidence and aggregate metric snapshots. Separate local files can contain opt-out identifiers, brand/audience profiles, imports, media paths and manual exports. Some inputs may contain third-party personal data: do not import or publish such data without an appropriate basis and necessary permissions. A typed approver name or operating-system login can itself identify a person.

ReachWeaver does **not** upload the local database to the maintainer and does not currently operate a central service that collects users' draft posts or social-network credentials. The local operator controls the device, its accounts, backups and exports. This is a description of the maintained code, not a statement about what Claude, operating-system backups, external integrations or modified copies may collect.

## Network access and recipients

- **Startup update notice:** the installed Claude Code plugin runs a session-start check, contacting GitHub's release API by HTTPS at most once per 24 hours (unless disabled). GitHub and intermediaries can process connection metadata such as IP address, request time and user agent. It does not intentionally send draft content, local database records or credentials. Disable by creating `~/.cache/reachweaver/no-update-check`.
- **Owner-triggered update commands:** explicit version checks or downloads contact GitHub. Updates are not silently installed.
- **Live publication:** only when the operator configures credentials, reviews/approves content and executes the required live-send opt-ins, the plugin can send approved content and authentication material to the selected social platform's API. Social networks may store or display the post and process account/network metadata under their own terms.
- **Optional explicit online operations:** account identity checks, configured publishing backends (including Postiz when enabled), external media handling and online URL/landing checks can contact third parties. Their own policies apply.
- **Claude itself:** prompts, files and tool outputs supplied to an AI assistant may be handled under the user's Anthropic agreement and privacy settings. ReachWeaver cannot control such processing.
- **Maintenance and support:** if you voluntarily email a bug report, the maintainer receives the information you send. Never include secrets or third-party personal data in public issues.

The software does not intentionally sell personal data or run advertising trackers. Do not interpret this as a promise that third-party networks collect no information.

## Retention, access, deletion

ReachWeaver **does retain data locally**. It does not automatically erase all records when a session ends. Configurable retention is supported, but is not enabled just by installing the plugin. Copy `config/retention.example.json` to `config/retention.json`, review the policy, and explicitly invoke `reachweaver data-purge --apply` after checking the dry-run plan. Without a policy or purge, data may remain indefinitely. Certain active/unresolved-send records, minimal historical identifiers and campaign fingerprints are preserved to prevent duplicate publishing; published text may be replaced with a redaction marker while identifiers remain. Consult [the detailed lifecycle documentation](docs/PRIVACY-LIFECYCLE.md). External social posts, cloud/OS backups, files exported by the operator and AI-provider records are **not** erased by local purge.

The local operator can inspect its data with `reachweaver privacy-status`, export it using `reachweaver data-export` and review potential purges with `reachweaver data-purge`. Protect exports carefully. To stop external platform processing, use the platform's own controls and delete remote posts separately where appropriate.

## Roles, responsibility and rights

For local installation and use, the operator generally decides which content and account data to process and why. Depending on the factual arrangement and applicable law, that operator may be a controller and must establish an appropriate legal basis, disclosures, security measures and mechanisms for individual rights. The maintainer does not automatically become the controller of every independent user's locally stored content. The maintainer may separately be responsible for personal data received through direct support correspondence.

For a privacy question or a request concerning data actually held by the maintainer, email **mohsen.seyedkazemi@gmail.com** with the subject `ReachWeaver privacy`. Do not send credentials. If the data exists only on your own device or a third-party service, address the request to that operator or service. Rights under applicable data-protection law, including access, correction, erasure, objection and complaint to a supervisory authority, remain unaffected.

## Security, children and changes

ReachWeaver is not directed to children under 18. Operators are responsible for protecting devices, local storage, credentials, access rights, and backups. Security issues can be reported privately as described in [the security policy](docs/SECURITY.md). No security or privacy system can eliminate all risk.

This notice will be updated if functionality or data practices materially change. Its effective date should be revised for substantive changes.
