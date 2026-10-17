# Browser Share Composer — a simpler way to prepare social posts

**Status:** experimental, local-only manual publishing preparation. This feature does not post, upload, schedule, create accounts, fetch analytics, or call a social platform.

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
2. **Choose:** search or tap networks, up to ten at once. Only platforms with an existing offline manual-export route appear. Platforms with a separate approved live-posting path are intentionally not shown here.
3. **Preview and tailor:** see the exact text and warnings, and edit each network's caption separately (for example a short Threads version and a longer Instagram version). After editing, click **Preview my posts** again to revalidate every network before saving. The Copy button copies the current editor text; nothing posts automatically.
4. **Save:** click Save posting files. ReachWeaver writes a new private folder below state/shares. Open OPEN-FIRST.md, review each platform's CHECKLIST.md, and paste the relevant caption into the native app yourself.

There is **no Publish button**, because this UI is a manual handoff, not a new API integration.

## What happens with photos and videos?

The browser composer prepares captions and checklists, **not** media files. Attach your photos/videos yourself in Instagram, TikTok, YouTube Studio, etc. Check copyright, faces/likeness consent, disclosures, subtitle/alt text and each network's current format requirements.

The CLI option share --attach PATH can list local media references for manual attachment, and the existing media-check validates owner-supplied media manifests. The browser does not upload files or inspect images, so an image-format warning does **not** mean the file has been checked.

## Human-only groups

Some destinations (e.g. Reddit and Hacker News) are treated as human-only. They receive private NOTES.md files, **not** ready-made AI-authored promotional text. Human participants must check community rules and create their own posts. No bulk messages, automatic comments, login workarounds or unsolicited engagement.

## How is the browser interface kept private?

- It binds only to 127.0.0.1 (loopback). It is **not a public website**. Do not set up proxy forwarding to the composer.
- This session is meant for the **same computer** that runs ReachWeaver. Do not expose it with SSH tunnels, reverse proxies, remote-access tools or router port forwarding.
- A new cryptographically random session token is required for both reads of the manual platform catalog and POST actions. Mutating requests also require the correct local Origin and Host headers; cross-origin preflights are refused.
- No cookies, external JavaScript, remote fonts, images, trackers or cloud backend. The local page uses a restrictive Content Security Policy and inserts content as text, not executable HTML.
- No caption is transmitted to social platforms. Text is sent from your browser to the **local ReachWeaver server on the same computer** for validation and file generation.
- Preview never writes files. Saving always rechecks rules, creates a fresh 0700 owner-only directory (on POSIX), writes 0600 files and stages the entire handoff before a final rename.
- The browser cannot specify an arbitrary destination path or load a local file by URL. This reduces accidental overwrites and path traversal.
- A malicious program already running as your operating-system user may still access your local files or browser session. Keep the computer secure and never share your session link.

The default command line share --guide remains available for anyone who prefers using a terminal. The existing read-only operations dashboard, reachweaver ui, remains **unchanged and read-only**.

## Testing and release

Run the offline test suite and packaging checks before release:

~~~bash
python3 -m unittest tests.test_share tests.test_share_browser -v
python3 -m unittest discover -s tests -v
python3 scripts/sync_skill.py --check
~~~

A passing test is not a real social-platform receipt. Browser previews and saved manual packages should never be labelled "published" in the app or in marketing.
