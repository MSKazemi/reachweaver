# Browser Share Composer — a simpler way to prepare social posts

**Status:** experimental, local-only manual publishing preparation. This feature does not post, upload to a network, schedule, create accounts, fetch analytics, or call a social platform. Its only database write is "Mark as posted", which records a post you already published by hand.

The browser composer is for people who want to share work on Instagram, TikTok, Threads, YouTube and other platforms **without editing JSON or using lots of terminal commands**. It uses the existing ReachWeaver manual-export safety checks, including the checks on each individually edited platform caption. No additional model API, subscription, cloud hosting or password is required.

## Start in one command

From the project folder, run:

~~~bash
python3 -m reachweaver share --web --open
~~~

Or, if you have the ReachWeaver command installed:

~~~bash
reachweaver share --web --open
~~~

Your browser should open an address starting with http://127.0.0.1. If it doesn't, copy the **complete** private local URL printed in your terminal into the browser on the **same computer**. Leave the terminal open while using the composer. Press Ctrl+C in the terminal to stop the server.

**Do not share that session URL.** It contains a random authorization token in the URL fragment. ReachWeaver does not store credentials, cookies or posting tokens for this feature.

## Four easy steps

1. **Write:** enter one caption/description. For YouTube, add a video title. For human-only communities, add a short private note and write the real message yourself.
2. **Choose:** search or tap networks, up to ten at once. Every network gets a copy-only package; Bluesky, LinkedIn, Mastodon and Telegram are offered as copy-only too (see below), and nothing is sent from this page.
3. **Preview and tailor:** see the exact text and warnings, and edit each network's caption separately (for example a short Threads version and a longer Instagram version). After editing, click **Preview my posts** again to revalidate every network before saving. Changing the main caption discards these per-network edits, so finish the main caption first. The Copy button copies the current editor text; nothing posts automatically.
4. **Save:** click **Save as new package**. ReachWeaver writes a new private folder below state/shares (in your workspace, or else the folder you started the command from) and shows its full path. Open OPEN-FIRST.md, review each platform's CHECKLIST.md, and paste the relevant caption into the native app yourself.
5. **Mark as posted** (optional): after you published a network's post yourself, paste its link in the **Saved packages** list (see below) so ReachWeaver can measure it.

There is **no Publish button**, because this UI is a manual handoff, not a new API integration. "Mark as posted" only records what you already did by hand.

## Your draft is kept, and you can undo

As you type, the composer keeps your draft on this computer (state/composer/draft.json, readable only by you) and the bar above the editor says **Saved**. Close the tab or restart the composer and the draft comes back. The draft holds only the editor fields: networks, caption, per-network edits, YouTube title, community note, the attached files and the package you are editing; never the session link or any password. **Undo** reverts your last change (a burst of typing counts as one change). **Clear form** empties the editor and deletes the draft and its attached files; saved packages are not touched.

## Saved packages: reopen, duplicate, update

The **Saved packages** section lists every package in state/shares, newest first, with each network marked **Exported** (its files are in the package) or **Posted by hand**.

- **Open in editor** loads the package back: networks, caption, per-network edits, title, note and attached files. If the editor already holds something, the first click asks you to click again.
- **Duplicate as new** copies the package into a new one (rechecked against every rule; no network is marked posted in the copy).
- After opening a package, preview it, then choose **Save as new package** (always a fresh folder, nothing overwritten) or **Update this package**. Update asks you to confirm, replaces that package's files in one step, and is refused if the package changed since you opened it or if any of its networks is already marked as posted (save as new instead).

## Photos and videos

Choose **Photos or videos** under the caption to attach up to ten files (.jpg, .jpeg, .png, .webp, .mp4, .mov or .webm, at most 256 MiB each). The browser sends the file only to the local ReachWeaver server on this computer, which keeps it in state/composer/media and copies it into the package's media folder when you save. Nothing is uploaded to a social network: you still attach the file yourself in Instagram, TikTok, YouTube Studio and so on.

Each preview card then lists **Photo and video checks**: the same offline header checks as share --attach (container, size, dimensions, duration and the like against each network's sourced limits, and what could not be checked). These checks do not cover copyright, faces/likeness consent, disclosures, captions or alt text; the existing media-check validates owner-supplied media manifests for those.

## Mark as posted (measure what you posted by hand)

Under a saved package, choose **Mark as posted** next to a network, paste the link of the post you published (and, if it was not just now, when you posted it), then **Record as posted**. The composer records it exactly like `mark-shared --url`: a "posted by hand" record (status published_manual) in your history database, with the package's caption and media fingerprints so the repeat guard knows them. It shows the new post id, and the network turns **Posted by hand**. Record its results later with `reachweaver metrics POST_ID ...` or import-metrics.

It is refused, with nothing recorded, when the link is not one http(s) link, when that link is already recorded for the network, when the time is in the future, for human-only venues (record your own post with `mark-shared` in the terminal), and for networks ReachWeaver cannot measure yet. The record can never be approved, scheduled or sent. It uses the history database of `--db`, `REACHWEAVER_DB` or the workspace, like other commands (`reachweaver --db PATH share --web`).

## LinkedIn, Bluesky, Mastodon and Telegram

These networks are in the list too, marked "Copy and post yourself · or connect it to send from ReachWeaver". The composer prepares copy-ready text for them exactly as for the others and never sends. Each preview card says: *Copy and post it yourself; to send it from ReachWeaver instead, connect the account and use draft → review.* A caption that is too long for a network is shortened for that network only (at a sentence end when possible, else at a word; a closing link is kept), and its card says what was removed; edit it there if you prefer your own cut.

## Human-only groups

Some destinations (e.g. Reddit and Hacker News) are treated as human-only. They receive private NOTES.md files, **not** ready-made AI-authored promotional text. Human participants must check community rules and create their own posts. No bulk messages, automatic comments, login workarounds or unsolicited engagement.

## How is the browser interface kept private?

- It binds only to 127.0.0.1 (loopback). It is **not a public website**. Do not set up proxy forwarding to the composer.
- This session is meant for the **same computer** that runs ReachWeaver. Do not expose it with SSH tunnels, reverse proxies, remote-access tools or router port forwarding.
- A new cryptographically random session token is required for both reads of the manual platform catalog and POST actions. Every request must carry the exact local Host header (a DNS-rebinding defence); POST actions also require the exact local Origin. Cross-origin preflights and other methods are refused, and no CORS header is sent. A request body is limited to 96 KiB and must arrive within 10 seconds.
- No cookies, external JavaScript, remote fonts, images, trackers or cloud backend. The local page uses a restrictive Content Security Policy and inserts content as text, not executable HTML.
- No caption is transmitted to social platforms. Text is sent from your browser to the **local ReachWeaver server on the same computer** for validation and file generation.
- Preview never writes files. Saving always rechecks rules, creates a fresh 0700 owner-only directory (on POSIX), writes 0600 files and stages the entire handoff before a final rename. An update replaces a package only after your confirmation and only if it is unchanged since you opened it.
- The browser cannot specify an arbitrary destination path or load a local file by URL. Package names and attached-file ids must match a strict pattern and are resolved and confirmed to sit inside the private state/shares or state/composer folder; symbolic links there are refused, never followed. An attached file is at most 256 MiB, must have a photo or video extension and must arrive within 120 seconds.
- The history database is opened only by **Mark as posted**, which adds a terminal "posted by hand" record and nothing else.
- A malicious program already running as your operating-system user may still access your local files or browser session. Keep the computer secure and never share your session link.

The default command line share --guide remains available for anyone who prefers using a terminal. The existing read-only operations dashboard, reachweaver ui, remains **unchanged and read-only**.

## Testing and release

Run the offline test suite and packaging checks before release:

~~~bash
python3 -m unittest tests.test_share tests.test_share_browser tests.test_share_browser_composer -v
python3 -m unittest discover -s tests -v
python3 scripts/sync_skill.py --check
~~~

A passing test is not a real social-platform receipt. Browser previews and saved manual packages should never be labelled "published" in the app or in marketing.
