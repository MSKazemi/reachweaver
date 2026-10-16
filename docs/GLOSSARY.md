# Glossary: the words ReachWeaver uses

Short, plain explanations, in alphabetical order. For the bigger picture read the [start here guide](START-HERE.md).

**Account** — One social profile or channel you control, listed in your local `config/accounts.json` by an ID such as `my-bluesky`. The file holds public details only (handle, server), never secrets.

**Adapter** — The piece of ReachWeaver that knows how to talk to one platform's official interface to send a post. Some adapters are *live* (can really send, once you switch them on) and some are only *mock-tested*.

**App Password** — A separate, limited password that some platforms (Bluesky) let you create for a tool. Use it instead of your main password, and revoke it any time.

**Approval** — Your explicit decision that one exact post may be sent. It is tied to the exact text, account and attachments. If any of them changes, the approval stops counting and you approve again. See `approve`, `unapprove`.

**Approver** — The name you give when approving (`--approver YOUR_NAME`). It is recorded so you can see later who said yes.

**Campaign** — A set of related posts for different networks about one thing you made, described in a campaign file with its sources.

**Capability registry** — A dated list ReachWeaver keeps of what each platform allows and which sending paths are switched on. `reachweaver capabilities` prints it. If it says a path is off, ReachWeaver refuses to send there.

**Claim** — A factual statement in a post, such as a number or "first to". ReachWeaver can check that each claim is supported by a source you provided (`claims-check`). It checks the wording against your source; it cannot prove the source is true.

**Credential** — A secret that proves who you are to a platform: a token, an app password, an access key. Credentials live only in your environment, never in the repository, a chat or an issue.

**Archive** — A private folder made by `reachweaver archive`: one sub-folder per post with the exact text, the metadata and, if you ask, verified copies of the attached files. See [journal](JOURNAL.md).

**Dead letter** — A post whose send ended in an unknown or failed state and now needs a person to look at it. `dead-letter` lists them.

**Distribution plan** — An offline research suggestion of which channels to consider for a region and a goal. It is a hypothesis to test, not a forecast and not permission to post.

**Draft** — A saved post that has not been approved. Safe to edit or delete.

**Dry run** — A rehearsal that shows what would happen and contacts nobody. It is the default for `publish`.

**Environment variable** — A named setting your computer passes to programs, such as `REACHWEAVER_ALLOW_LIVE=1`. ReachWeaver reads credentials from these, only at the moment of sending.

**Evidence / source** — The document, release page or data a claim comes from. Campaign files declare their sources so claims can be checked.

**Human-only** — A venue where ReachWeaver writes nothing, because the community expects a person (Hacker News, Reddit and several others). You write and post.

**Journal** — The read-only list of what you posted, when, on which account, with the approver, the link and the files, made by `reachweaver journal` from the local database.

**Live** — Real, as opposed to rehearsal. A live send needs approval plus `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`.

**Live receipt** — Proof from the platform that a real ReachWeaver send produced a real post (a dated record in [receipts](receipts/)). Today only Bluesky and Mastodon have one.

**Manual export** — A ready-to-paste package (text plus a checklist) for platforms ReachWeaver cannot send to. You post it yourself. See [manual export](MANUAL-EXPORT.md).

**Mock-tested** — Tested against a simulated server instead of the real platform. It shows the code behaves as designed, not that the real platform accepts it.

**Native scheduling** — A platform's own "post later" feature, used inside the platform's app. It is separate from anything ReachWeaver does.

**`needs_review`** — A post whose send result is unknown (for example, a timeout). ReachWeaver will not resend it. You check the real account, then run `reconcile`.

**OAuth** — A sign-in method where you grant a tool limited access through the platform's own consent screen, without sharing your password.

**Opt-out list** — A file of handles or domains that asked not to be mentioned. Checks refuse drafts that mention them.

**Pause** — The emergency switch (`reachweaver pause`) that stops all publishing until `reachweaver resume`.

**Payload hash** — A fingerprint of the exact post. Approval is tied to it, which is how ReachWeaver notices if the text changed after you approved.

**Plugin / skill** — How ReachWeaver is added to Claude Code. A *skill* is a folder of instructions and tools Claude can use; the *plugin* is the packaged form installed with `/plugin install`.

**Preview** — A rendering of how the platform will show your post (length, links, mentions), produced offline by `show`.

**Queue / outbox** — The local list of posts and their statuses: `draft`, `approved`, `scheduled`, `sending`, `published`, `needs_review`, `cancelled`. `reachweaver list` shows it.

**Reconcile** — Telling ReachWeaver, after you looked at the real account, whether an uncertain post was published or not (`reconcile POST_ID --published ...` or `--not-published`).

**Repeat check / repeat guard** — The comparison of a draft with what a platform has already received from you: the same video or image file, identical wording, or the same link. Identical files and wording by the same account block approval unless you pass `--allow-repeat`. See [repeat guard](REPEAT-GUARD.md).

**Registry** — A versioned data file used by the offline tools: the capability registry, the distribution registry (channels and markets) and the audit registry (profile checklists).

**Shared-material ledger** — The local list of things you posted by hand, kept so the repeat check knows about them. You add to it with `mark-shared`.

**SQLite / state** — The small private database file in `state/` where your drafts, approvals and history live. Only your user can read it.

**Status** — Where a post is in its life: see *Queue*.

**UTM link** — A web link with tracking labels added so you can later see which post brought a visitor. `utm-link` builds them without third-party trackers.

**Workspace** — The folder you run ReachWeaver from: your `config/` (account metadata, no secrets) and `state/` (the private database, the approval ledger, the archive). It is separate from the program and holds no credentials, so you can copy it to another computer. See [moving to another computer](MOVING.md).

**Unknown** — What a report shows when a number is missing. ReachWeaver never turns missing data into zero.
