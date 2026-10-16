# Local dashboard and friendlier terminal output

> Status: **experimental**. Read-only. Verified in desktop Chrome (light and dark, keyboard shortcuts, a 400 px-wide frame for the narrow layout) and by offline tests; it has not been tried on a real phone or in other browsers.

ReachWeaver is a command-line tool. `reachweaver ui` adds an optional **read-only dashboard** that shows what is in your local database in a browser. It exists to make review easier: see what needs a human, read the exact text of a post next to its approval state, and find the command to run next. It does not replace the terminal for any action.

## What it can and cannot do

| It can | It cannot (by design) |
| --- | --- |
| Show the queue, newest first, with posts that need a human at the top | Approve, unapprove, cancel or schedule a post |
| Show one post's exact text, offline platform preview, approval binding, media/video/reply state and event timeline | Publish, send, retry or reconcile anything |
| Show readiness, the emergency-stop state, per-account budgets and dead letters | Pause or resume the emergency stop |
| List configured accounts and the **names** of missing credential variables | Show, accept or store a credential value |
| Browse the offline channel research (`distribution-plan`) | Contact a platform or any other server |
| Print the terminal command you could run next, with a Copy button | Run that command for you |

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

## Pages

- **Overview**: a banner for the emergency stop or overall readiness (`ok`, `attention`, `blocked`), a **What to do next** card (the same guidance as `reachweaver start`, with copyable commands), counts per status that link into the queue, the reasons behind the status in plain language, per-account state and dead letters. The browser tab title shows how many sends need review, e.g. `(1) ReachWeaver Dashboard`.
- **Queue**: status chips and an account menu filter the list, and a search box narrows the posts shown. Posts in `needs_review` come first because a send ended in an unknown state and a human must check the native platform. Empty states say what to do next.
- **Post**: a status card that says in plain language what the state means and offers the next command to run; the exact text with a **length meter** against the platform's own limit (Bluesky, Mastodon, LinkedIn) and a Copy text button; the approval binding, offline platform preview and attachments in collapsible sections; previous/next post links; and the event timeline with relative times ("2 hours ago", exact time on hover).

The exact text is shown **as stored**, not redacted, because you are reviewing what would be sent. A warning appears when it looks like it contains a password, token or key. Errors, events and previews are redacted.
- **Accounts**: configured accounts, adapter kind and, per account, whether every credential variable is set ("all set") or which are missing (names only). Credentials are needed for live sending only.
- **Channels**: the offline distribution research by market and goal. These are research hypotheses, not predictions, not an account connection and not permission to publish.

A value that is not recorded is shown as "unknown", never as zero.

## Keyboard shortcuts

Press `?` for the list. `g` then `o`/`q`/`a`/`c` jumps to Overview, Queue, Accounts or Channels; `j`/`k` move through the queue and `Enter` opens the selected post; `[` and `]` go to the previous or next post; `/` searches the queue; `r` refreshes; `t` cycles the theme; `Esc` leaves a field. Shortcuts are ignored while you type in a field.

## Narrow screens

Under 640 pixels wide, tables become stacked labelled cards, the filter chips and navigation wrap, and the header scrolls away instead of staying fixed. I checked this in a 400 px-wide frame in desktop Chrome; I have not tried a real phone.

## Theme and refresh

The header has a **Theme** button (auto follows your system, then light, then dark) and an **Auto-refresh** checkbox (off by default; reloads the current page every 20 seconds while the tab is visible and you are not typing in a field). Both choices are remembered in your browser's local storage, which holds nothing else; if storage is blocked, the dashboard still works and simply forgets them.

## How it protects itself

The server is a small module built on the Python standard library, with no new dependency.

- **Loopback only.** It binds to `127.0.0.1`. There is no `--host` option, and the server refuses any other host.
- **Unguessable address.** Every request must carry the random token in the URL path; anything else gets a bare 403. The token is not logged and the page sends no referrer.
- **No DNS-rebinding or cross-site reads.** The `Host` header must be the loopback name and port; cross-origin and cross-site fetch requests are refused.
- **One request per connection.** The server answers HTTP/1.0 and drops a client that sends nothing for 10 seconds, so idle connections cannot pile up. A malformed request gets a plain 400, never a traceback.
- **Read only, twice.** Only `GET`/`HEAD` are accepted (anything else is 405), and the database is opened with SQLite `mode=ro` and `query_only`, so even a bug cannot write.
- **Untrusted text stays text.** Post text, errors and URLs are inserted into the page as text, never as HTML, and a strict Content-Security-Policy forbids inline scripts and styles, remote resources and framing.
- **Redacted.** Everything passes through the same redaction as `ops-summary`. Like `doctor` and `health`, it checks whether a credential variable is set, but a value is never displayed, returned or logged.
- **No network from the dashboard.** The only socket is the local listener.

Tests in `tests/test_ui_data.py` and `tests/test_ui_server.py` check each of these offline, including that the database file is byte-identical after every endpoint is called and that the dashboard code references no send, approval or credential function.

If another program on your computer runs as you, it can read the same files the dashboard reads; the dashboard does not add a login. It is meant for your own, single-user machine.

## Also in this release: easier terminal output

- `reachweaver show POST_ID` at a terminal prints a review sheet: the exact text in a box, the length meter, the approval state in a sentence, attachments and the commands you could run next. Piped or with `--plain` it prints the same JSON as before.
- A mistyped command gets a short suggestion (`unknown command 'aprove'. Did you mean: approve, ...`) instead of a list of every command.
- `reachweaver completion bash` (or `zsh`) prints a Tab-completion script for commands, options and fixed choices. Load it with `eval "$(reachweaver completion bash)"` or save it where your shell loads completions. It reads no database and runs nothing; regenerate it after an upgrade.
- `reachweaver start` looks at your accounts file, database and queue and prints the exact next command. It runs nothing and never creates the database. `--json` gives the same guidance for scripts.
- `reachweaver --help` now lists commands grouped by task. `reachweaver COMMAND --help` is unchanged.
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
