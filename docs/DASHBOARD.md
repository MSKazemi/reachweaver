# Local dashboard and friendlier terminal output

> Status: **experimental**. Read-only. Verified in desktop Chrome (light and dark, keyboard shortcuts, the narrow layout at 375, 400 and 414 px in a frame, printing a post page to PDF) and by offline tests; the Work with Claude, Ideas and Overview pages of 2026-10-21 were checked in headless Chromium (Playwright) at 1280 and 375 px, light and dark, against a synthetic database, with no console or CSP error; it has not been tried on a real phone or in other browsers.

ReachWeaver is a command-line tool. `reachweaver ui` adds an optional **read-only dashboard** that shows what is in your local database in a browser. It exists to make review easier: see what needs a human, read the exact text of a post next to its approval state, and find the command to run next. It does not replace the terminal for any action.

## What it can and cannot do

| It can | It cannot (by design) |
| --- | --- |
| Show the queue, newest first, with posts that need a human at the top | Approve, unapprove, cancel or schedule a post |
| Show what is scheduled, day by day, and what was already posted | Move a post to another time |
| Show one post's exact text, offline platform preview, approval binding, media/video/reply state and event timeline | Publish, send, retry or reconcile anything |
| Show readiness, the emergency-stop state, per-account budgets and dead letters | Pause or resume the emergency stop |
| List configured accounts and the **names** of missing credential variables | Show, accept or store a credential value |
| Browse the offline channel research (`distribution-plan`) | Contact a platform or any other server |
| Print the terminal command you could run next, with a Copy button | Run that command for you |
| Build a prompt for your own Claude Code session and copy it | Call a model, start Claude Code or send anything to Anthropic |
| Show what changed recently (drafts, approvals, sends, ideas) | Record, change or delete an event or an idea |

Approving, scheduling, pausing and publishing remain deliberate commands you type yourself. Nothing in the dashboard changes the approval gate, the dry-run default, the emergency stop, credential isolation or ambiguous-send reconciliation.

## Start it

```bash
reachweaver init                 # once, if you have no database yet
reachweaver ui                   # prints an address with a per-run token; open it in your browser
reachweaver ui --open            # also opens your default browser
reachweaver ui --port 8765       # fixed port instead of a free one
reachweaver --db state/other.sqlite --accounts config/accounts.json ui
```

The address looks like `http://127.0.0.1:PORT/RANDOM-TOKEN/`. Press Ctrl-C in the terminal to stop the dashboard. `ui` refuses to start if the database does not exist; it never creates one.

Keep the address private. It is the only thing that lets a browser read your queue, so do not paste it into a chat, an issue or a screenshot. The token stays valid until you stop the dashboard. `--open` hands the address to your browser on its command line, where another user of a shared computer could see it with `ps`; on such a machine paste the address yourself instead.

## Layout

A sidebar groups the pages into **Work** (Overview, Queue, Schedule, Ideas, Work with Claude), **Record** (Posted, Results, Campaigns) and **Setup** (Accounts, Channels). Counts next to Overview, Queue and Schedule say what needs you: red when a send needs review or an approval has expired, amber otherwise. They come from the same read-only check as the Overview's **Needs you** list (`api/attention`) and are refreshed on every page. The header holds the search box, a refresh button and a settings menu (theme, auto-refresh, alerts, keyboard shortcuts). Lists of posts are compact rows: status, platform and account, the first two lines of text, and the relevant time; the whole row opens the post.

**Jump to (Ctrl K or Cmd K):** type part of a page name, a post's text, id, account, platform or campaign, then press Enter. It only moves you to that page or post; the last entry searches everything for what you typed.

## Pages

- **Overview**: a banner for the emergency stop or overall readiness (`ok`, `attention`, `blocked`); **Needs you**, the posts that need a human, each listed once under its most urgent reason (a send to reconcile, an expired approval, past its time, an approval that runs out before the due time, a draft waiting for approval) with a link to it; a **What to do next** card (the same guidance as `reachweaver start`, with copyable commands); a bar of all posts by status whose legend links into the queue (the "scheduled" entry opens the Schedule); **Coming up** (the next five scheduled posts) and **Recently posted** (the last five entries of the journal); the reasons behind the status in plain language; then two checks that load after the page:
  - **Files and approvals**: the approval ledger's state in a sentence (`ok`, `absent`, `broken` ...), with a link to each affected post and what to do, the database integrity check, and the same folder check as `reachweaver workspace`: a missing or changed attachment of an unsent post, a database or ledger that is readable by other users, a run in progress. Informational notes are folded away. Because the check hashes the files of unsent posts, the dashboard reuses its result for up to a minute.
  - **Suggestions**: up to five offline rules from `reachweaver suggest --include-db`, such as published posts with no first-party metric snapshot yet. They are rules over your local database, not a reach prediction, and each needs a human. Safety items are left out here because the status reasons already list them. The card is hidden when there is nothing to suggest.

  Under **Needs you** sit **Ask Claude** (a compact composer and two ready-made prompts, "What needs me today?" and "Plan next week's posts") and **Your turn in the terminal** (the two most urgent owner-only command cards; all of them are on the Claude page). After **Coming up** and **Recently posted**, **Activity** lists the six newest local events. The page ends with per-account state and dead letters. The browser tab title shows how many sends need review, e.g. `(1) ReachWeaver Dashboard`.
- **Work with Claude**: see [Work with Claude](#work-with-claude) below.
- **Ideas**: your ideas inbox (`reachweaver idea`, the private `ideas.json` next to the database), newest first, filtered by state (open, promoted, dropped) and searchable. Each idea shows its text, id, tags and only the **host** of its source link (never the path or query). An open idea has **Promote with Claude**, which copies a prompt that asks Claude to propose a campaign id and, after you agree, run `idea promote` and fill the scaffold with verified claims. The dashboard never creates, changes or drops an idea; a damaged inbox is shown as an error instead of breaking the page.
- **Queue**: status chips (with their counts) and an account menu filter the list, and a search box narrows the posts shown. The list is grouped under a heading per status; each row shows when the post is due, or when it was created if it has no time. Posts in `needs_review` come first because a send ended in an unknown state and a human must check the native platform. Empty states say what to do next.
- **Schedule**: approved posts by due time, shown in your local time (the UTC time on hover). Posts past their time come first, then sends in progress, then the next 14 days grouped by day ("Today", "Tomorrow", then the date), then approved posts with no time set. When you have set an approval time limit (`approval-mode --max-age-hours`), a post whose approval has expired, or will expire before its due time, is flagged. Only a worker you run sends anything, and `reachweaver pause` stops every send. **Month** switches to a calendar of five weeks from this Monday, with each post at its local time (orange: past its time and not sent; red: approval expired; at most three per day, then "+N more"). Each entry shows its time, a platform dot and the start of its text; click a day number (or "+N more") to list that day's posts under the calendar. On a narrow screen it becomes a list of the days that have posts.
- **Search**: the box in the header (or `/` on a page without its own search) searches every post in any status, campaign names and posts recorded as shared by hand, by text, id, account, platform, campaign, note or link, case-insensitively. It runs on your own database only; nothing is sent anywhere.
- **Posted**: the record from `reachweaver journal`, newest first: what ReachWeaver sent (with the approver and a link to the remote post), sends that ended in an unknown state ("possibly posted: check the platform"), and what you recorded as shared by hand with `mark-shared`. A post recorded with `mark-shared --url` is labelled "posted by hand" and opens its post page. Search and an account menu narrow the list; it shows the newest 100. Files are listed by kind and count only; the dashboard does not open them.
- **Post**: a status card that says in plain language what the state means and offers the next command to run; when the post needs you (a draft to approve, an approved post to send, a send to reconcile, a send that did not finish, an expired approval), an **owner-only card** under it with the exact commands, who runs each ("You run this" or "Claude can run this") and one line on why it is yours; a **Work with Claude** card in the side panel with three ready-made prompts ("Voice-check and tighten this draft", "Draft a follow-up for this post", "Check claims in this post") and a link to ask something else about this post; the exact text with a **length meter** against the platform's own limit (Bluesky, Mastodon, LinkedIn) and a Copy text button; a **Preview**, an offline look-alike of the post with the account's handle and links, hashtags and mentions tinted (not the platform's own rendering: link cards, line breaks and truncation may differ); the approval binding, offline platform preview and attachments in collapsible sections; a side panel with more commands, the account, platform, campaign and times; previous/next post links; recorded results with a small line of one metric over hours after posting when at least two exact values were recorded (nothing is drawn from one value or interpolated beyond the points); and the event timeline as a vertical list with relative times ("2 hours ago", exact time on hover).

The exact text is shown **as stored**, not redacted, because you are reviewing what would be sent. A warning appears when it looks like it contains a password, token or key. Errors, events and previews are redacted.
- **Results**: what you recorded from each platform's own analytics (`metrics`, `import-metrics`, `metrics-fetch`). Per platform: the total of exact values and how many published posts have one (`3/5`). Only metrics with a recorded value somewhere get a column; the others are named in one line ("Nothing recorded yet for: ...") and stay unknown, not zero. Each post's latest record has a bar of its first metric against the largest exact value shown. Per post, newest first: the latest record with its time, how long after posting it was taken and its source. A value you did not record is "unknown", never 0. A platform estimate is labelled "(platform estimate)" and never added to exact counts, and a value the platform withheld says so (for example "not available"). The post page lists every record of that post. Posts you published by hand and recorded with `mark-shared --url` are included and labelled "posted by hand" (per platform: "N posted by hand"). Nothing is fetched: the page only shows what is already in your database. Under **What works for you** it shows up to three lines per account from `reachweaver insights` (your own posts' results about 7 days after posting, compared by time, length, link, format, hashtags and opening hook): observations to test, never causes; with too little data it says so ([INSIGHTS.md](INSIGHTS.md)).
- **Campaigns**: a card per campaign name used on a post, most recent first, with a status bar, its status counts, platforms, accounts and staged variants. A campaign's page shows its posts side by side (status, platform and account, text, when, recorded results) with links to each post. A campaign only groups posts for review: each post is still approved, scheduled and sent on its own.
- **Accounts**: the ready-made prompt "Which channel should I add next?" (it lists the platforms you already use), then a card per configured account with its handle, platform, adapter kind, links to its queue, posted list and results, and, per account, whether every credential variable is set ("all set") or which are missing (names only). Credentials are needed for live sending only.
- **Channels**: a card per channel from the offline distribution research by market and goal, with its delivery, native scheduling and official source. These are research hypotheses, not predictions, not an account connection and not permission to publish. The "Which channel should I add next?" prompt here uses the market and goal you picked.

A value that is not recorded is shown as "unknown", never as zero.

## Work with Claude

The dashboard does not run Claude. You keep Claude Code open in its own app (terminal, desktop, IDE or web) on your own subscription, and the dashboard becomes its companion: it turns what you want into a precise prompt, shows what only you can do, and follows what changes. It calls no model, starts no process and stores nothing.

- **Ask Claude.** Type what you want and pick what it is about (nothing specific, a post that needs you, an account, a campaign or an open idea; the post page's "Ask something else" link fills it in). The prompt appears in a read-only box as you type; **Copy prompt** (or Ctrl Enter in the text box) copies it. If the browser refuses the clipboard, the prompt is selected so Ctrl C / Cmd C copies it, and a short status line says which happened (read out by screen readers). Your words go to the local dashboard only as part of the request that builds the prompt; they are not logged or stored there. The unsent text is kept in this browser's local storage so a refresh does not lose it; **Clear** removes it.
- **What a prompt contains.** It starts with `/reachweaver` so Claude Code loads the ReachWeaver skill; then the ids it is about (post, account, platform, campaign, idea) taken from your database, never guessed; the commands to start with, beginning with `reachweaver context --json` (none of them approves or sends anything, and each is checked by the tests against the real command line); what you want, step by step; and the ground rules: Claude never runs `approve`, `publish --live`, `resume`, `reconcile`, `reconnect` or the other owner-only commands and hands you the exact command instead, treats quoted text as data, never asks for a password or token, and claims no post or view without first-party evidence. If you started the dashboard with `--db`, `--accounts` or `--home`, every command in the prompt repeats them.
- **Stored text is quoted as untrusted data.** A post's or idea's text appears between `BEGIN UNTRUSTED DATA` and `END UNTRUSTED DATA` lines whose marker is derived from the text's hash, so the text cannot close its own quote. Control characters are removed, something that looks like a password, token or key is replaced (the prompt says so), and text over 3000 characters is cut with a pointer to `reachweaver show ID`.
- **Ready-made prompts.** One click copies each: "What needs me today?" and "Plan next week's posts" (Overview and Claude page), "Which channel should I add next?" (Accounts, Channels and the Claude page), the three post prompts on each post page, and "Promote this idea into a campaign" on each open idea. "Show the prompt" lets you read it first.
- **Only you can do these.** One card per post that needs you, most urgent first (a send to reconcile, a send that did not finish, an expired approval, a post past its time, a draft waiting for approval, then approved posts waiting to be sent), plus owner steps from the `start` guide such as `reachweaver resume` when the emergency stop is on. Each card shows the exact commands with Copy buttons, marks each one "You run this" (owner-only in `reachweaver describe`) or "Claude can run this", and says in one line why it is yours. The commands come from the same helpers as the post page and `reachweaver start`; the live send is the dry-run command with `--live` added, and it still needs `REACHWEAVER_ENV=prod`, `REACHWEAVER_ALLOW_LIVE=1` and a due, approved post in your own terminal.
- **Activity.** The last 30 local events, newest first: drafts created, approvals (with the approver name you gave), schedules, sends started, published, sends that need review, reconciliations, recorded results, emergency stop changes, ideas added, promoted or dropped. Each shows a plain label, the relative time, the post's platform, account and the usual two-line excerpt; an event's stored detail (which can quote a provider's error) is never shown. Checks, voice-checks and voice profiles leave no record, so they do not appear. Turn on **Auto-refresh** (Settings, or the link on the Claude page) and the feed follows a Claude Code session every 20 seconds while you are not typing.
## Keyboard shortcuts

Press `?` for the list. `Ctrl K` (or `Cmd K`) opens **Jump to**. `g` then `o`/`q`/`s`/`p`/`r`/`m`/`a`/`c`/`i`/`w` jumps to Overview, Queue, Schedule, Posted, Results, Campaigns, Accounts, Channels, Ideas or Work with Claude; `Ctrl Enter` in the Claude composer copies the prompt; `j`/`k` move through the queue or the posted list and `Enter` opens the selected post; `[` and `]` go to the previous or next post; `/` searches the queue or the posted list; `r` refreshes; `t` cycles the theme; `Esc` leaves a field. Shortcuts are ignored while you type in a field.

## Narrow screens

Under 860 pixels wide the sidebar becomes a drawer (the menu button, or **More** in the bottom bar), and a bottom bar holds Overview, Queue, Schedule and Posted with their counts. The header is one row: menu, search and settings. Under 640 pixels, post rows put the status and time on the first line and the text under it, and tables become stacked labelled cards. Every page was checked at 390 px wide in headless Chrome with no sideways scrolling; it has not been tried on a real phone.

## Printing a post

Print a post page, or save it as PDF from the browser's print dialog, to keep a copy of what you reviewed. The printout leaves out the navigation and buttons, and opens the collapsed sections, so it includes the approval details (payload hash, approver, channel) and the offline preview next to the exact text.

## Theme, refresh and alerts

The settings menu in the header has the **Theme** (auto follows your system, light or dark) and an **Auto-refresh** checkbox (off by default; reloads the current page every 20 seconds while the tab is visible and you are not typing in a field). **Alerts** (off by default) asks your browser for permission to show notifications, then checks once a minute while the tab is open and notifies you when more sends need review or a new readiness block appears. A notification carries counts only, never post text, account names or links, so nothing private shows on a lock screen. The three choices are remembered in your browser's local storage, which holds nothing else apart from the unsent text of the Claude composer; if storage is blocked, the dashboard still works and simply forgets them.

## How it protects itself

The server is a small module built on the Python standard library, with no new dependency.

- **Loopback only.** It binds to `127.0.0.1`. There is no `--host` option, and the server refuses any other host.
- **Unguessable address.** Every request must carry the random token in the URL path; anything else gets a bare 403. The token is not logged and the page sends no referrer.
- **No DNS-rebinding or cross-site reads.** The `Host` header must be the loopback name and port; cross-origin and cross-site fetch requests are refused.
- **One request per connection.** The server answers HTTP/1.0 and drops a client that sends nothing for 10 seconds, so idle connections cannot pile up. A malformed request gets a plain 400, never a traceback.
- **Read only, twice.** Only `GET`/`HEAD` are accepted (anything else is 405), and the database is opened with SQLite `mode=ro` and `query_only`, so even a bug cannot write. The ideas inbox is only read, and a missing one is never created.
- **No model, no process.** The Claude page builds a text prompt from local ids (`reachweaver/ui_prompts.py`); it never starts Claude Code, calls a model or contacts Anthropic. What you type reaches the server only as a query parameter of that one request (printable text up to 4000 characters), is not logged or stored, and comes back inside the prompt, which the page puts in a read-only text box as a value, never as HTML.
- **Untrusted text stays text.** Post text, errors and URLs are inserted into the page as text, never as HTML, and a strict Content-Security-Policy forbids inline scripts and styles, remote resources and framing.
- **Redacted.** Everything passes through the same redaction as `ops-summary`. Like `doctor` and `health`, it checks whether a credential variable is set, but a value is never displayed, returned or logged.
- **No network from the dashboard.** The only socket is the local listener.

Tests in `tests/test_ui_data.py`, `tests/test_ui_server.py` and `tests/test_ui_companion.py` check each of these offline, including that every command a prompt or an owner card suggests parses with the real command line, that no prompt's start command is owner-only, that no credential or environment value reaches a prompt, that the database file is byte-identical after every endpoint is called and that the dashboard code references no send, approval or credential function.

If another program on your computer runs as you, it can read the same files the dashboard reads; the dashboard does not add a login. It is meant for your own, single-user machine.

## Also in this release: easier terminal output

- `reachweaver show POST_ID` at a terminal prints a review sheet: the exact text in a box, the length meter, the approval state in a sentence, attachments and the commands you could run next. Piped or with `--plain` it prints the same JSON as before.
- A mistyped command gets a short suggestion (`unknown command 'aprove'. Did you mean: approve, ...`) instead of a list of every command.
- `reachweaver completion bash` (or `zsh`) prints a Tab-completion script for commands, the actions of a command that has its own (`idea add|list|drop|promote`), options and fixed choices (`idea list --status <Tab>`). Load it with `eval "$(reachweaver completion bash)"` or save it where your shell loads completions. It reads no database and runs nothing; regenerate it after an upgrade.
- `reachweaver start` looks at your workspace, accounts file, database and queue, ideas inbox and metric snapshots and prints the exact next command. The easiest path, the browser composer (`share --web --open`), comes first; outside a workspace it suggests `reachweaver workspace --init .` (and copying `config/accounts.example.json` only when that file is in the folder); accounts that still carry the example placeholders are named ("N account(s) still use example placeholders — edit config/accounts.json") instead of being counted as configured; waiting drafts point to your guided `reachweaver review`; open ideas point to `idea list --status open`, and recorded snapshots to `report --since 30d --format markdown`. It runs nothing and never creates the database. `--json` gives the same guidance for scripts. Typing `reachweaver` alone prints the same guidance and points to `reachweaver --help` and `reachweaver --help all`, instead of a usage error.
- `reachweaver --help` (or `reachweaver help`) lists the 16 everyday commands, one plain line each. `reachweaver --help all` (or `reachweaver help all`) lists every command grouped by task, with the global options. `reachweaver COMMAND --help` is unchanged, and `reachweaver help COMMAND` prints the same.
- In a terminal, `list` prints an aligned table with the status coloured (the id column is never shortened, so you can copy it), and `check` colours `OK`/`BLOCKED`. Control characters in stored text are replaced before they reach the terminal. When output is piped or redirected, the format is exactly what it always was, so scripts keep working. Pass `--plain` before the command (`reachweaver --plain list`) to turn colour and tables off in a terminal too.
- At a terminal, a few common errors add one "Next:" line (missing accounts file, unknown post id, missing database). Scripts see only the `ERROR:` line.
- `reachweaver` reads `REACHWEAVER_DB` and `REACHWEAVER_ACCOUNTS`, so you can set them once instead of repeating `--db` and `--accounts`.

The `NO_COLOR` convention is not read yet; use `--plain`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Database ... does not exist` | Run `reachweaver init` first; the dashboard never creates a database. |
| `predates this version's schema` | Run `reachweaver init` once to upgrade it, then retry. |
| Browser shows `{"error": "forbidden"}` | The token in the address is missing or from an earlier run; copy the new address from the terminal. |
| The accounts page says the accounts file is missing | Copy `config/accounts.example.json` to `config/accounts.json`, or pass `--accounts FILE`. |
| The page is empty or stale | Press **Refresh** (or `r`); the dashboard reads the database on every request. |
| `Database ... is not a ReachWeaver database` or `Cannot read` | The file is not a ReachWeaver database; `ui` checks this before printing an address. |

See also: [Quickstart](QUICKSTART.md), [Security checklist](SECURITY-CHECKLIST.md), [Troubleshooting](TROUBLESHOOTING.md).
