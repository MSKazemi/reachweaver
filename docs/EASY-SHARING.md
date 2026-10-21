# Share a post in a few simple steps

This is the easiest way to prepare social posts with ReachWeaver. You can use it **without an account connection, password, payment or API key**. You do not need to understand JSON.

**Important:** this feature prepares private folders with captions and checklists. It does **not** sign in, upload, schedule or publish anything. You finish each post in the social media app yourself.

## Even easier: open the browser composer

From the ReachWeaver folder:

```bash
python3 -m reachweaver share --web --open
```

Use its editor, tap the social-network choices, attach a photo or video if you like, preview the exact captions and save a new private folder. Then open the native platforms and publish yourself. Your draft is kept as you type, saved packages can be reopened, updated (after a confirmation) or duplicated, and once you have posted by hand you can paste the post's link under **Mark as posted** so ReachWeaver can measure it. It never connects to a social account, uploads files to a network or publishes. See [the browser workflow guide](BROWSER-SHARE-COMPOSER.md).

## Option A: answer a few questions

From the ReachWeaver project folder, enter:

~~~bash
python3 -m reachweaver share --guide
~~~

ReachWeaver asks which networks you want (for example, instagram,threads; an empty or mistyped answer is asked again, with a "Did you mean" and the list), asks for your caption, offers to check a photo or video (type its path, or press Enter to skip; nothing is uploaded), reviews the requirements and asks `Save these files? [Y/n]`. Enter saves, because saving only writes private files on your computer. It creates a private folder named something like state/share-YYYYMMDD-HHMMSS.

The caption may take several lines: type each line at the `>` prompt and press Enter on an empty line to finish (or Ctrl-D). An empty line ends the caption, so for blank lines between paragraphs save the text in a file and use `--file`.

Before it asks to save, the terminal shows the final text for each network, so you can copy it from there: a header per network with its length against that network's limit (for example `Instagram (photo post): 72/2200 characters`, `Bluesky: 72/300 graphemes`), then the text. A long text shows its first three lines (about 280 characters) and `… full text in instagram/caption.txt`. When your caption fits every network you chose, every copy is the same text, and the terminal says so once: `Same text for all 3: it fits every network, so nothing was changed`. ReachWeaver does not invent differences: a copy changes only when a sourced rule requires it (see the next section).

Open the **OPEN-FIRST.md** file in that folder. It says what to paste into each app, which options to choose yourself, and what to double-check before posting, with one short **Tip** per network (restating a fact ReachWeaver has a source for; a suggestion, not a promise of reach). Every platform has its own caption/post and CHECKLIST.md, written in plain words: "Not posted yet", "ReachWeaver cannot post to Instagram; you post it yourself", "Instagram needs a photo or video: attach it in the app". The machine-readable `manual-export.json` keeps its status codes.

### When a caption is too long, or a phrase does not fit

Each network's copy is fitted to that network's rule. It is cut at the end of the last sentence that fits (when that keeps most of the text), otherwise at a word with `…`. A closing link, alone or on a short last line such as `Read more: https://...`, is kept and the text before it is shortened instead. The `SHORTENED` line names everything removed: the removed passage (its start and its end, so a closing call to action is visible), `LINK REMOVED:` for any link that could not stay, and cut hashtags. `--no-shorten` keeps the text as written and blocks instead.

"link in bio" is flagged where a link in the post itself works: Bluesky ([rich text links](https://docs.bsky.app/docs/about-bluesky-content/post-richtext)), Telegram ([clickable URL entities](https://core.telegram.org/api/entities)) and Mastodon ([links in posts](https://docs.joinmastodon.org/user/posting/)), all checked 2026-10-21; for LinkedIn the note says that clickability is unverified. The text is never changed for you.

If you installed the command-line program, you may type reachweaver instead of python3 -m reachweaver.

## Option B: one command, no questions

~~~bash
python3 -m reachweaver share --platform instagram,threads --text "A simple look at our new feature #opensource" --out state/my-first-share
~~~

This saves two private folders and one simple OPEN-FIRST.md guide. It is an offline package, **not** an Instagram or Threads post.

For captions containing multiple lines, save a text file and run:

~~~bash
python3 -m reachweaver share --platform instagram,tiktok --file my-caption.txt --out state/video-share
~~~

Choose a *new* output folder every time. Existing folders are never overwritten.

### When one network is blocked

If one network cannot be prepared and the others can, Share writes the others and lists the skipped one with its fix, for example:

~~~text
SKIPPED 1 network(s); the other 2 are prepared below:
 - YouTube skipped: add --title "…" and run again with --platform youtube
~~~

Exit codes, for scripts: `0` every chosen network was prepared; `1` some were skipped (each listed with its fix; the others were prepared, unless you used `--preview` or answered no to saving); `2` blocked, nothing written. A problem that belongs to no single network (no caption, `--note` without a human-only community, `--title` without YouTube) still blocks everything, and so does every network being blocked. Add `--strict` for all or nothing: any blocked network then writes nothing and exits 2.

A file named with `--attach` that does not exist says where it looked: `Attachment not found: nothere.mp4 (looked in /your/folder). Check the name or leave it out.` (exit 2). A folder or a symbolic link given instead of a file gets its own plain message.

## Bluesky, Mastodon, Telegram and LinkedIn

ReachWeaver can also send to these four networks through their APIs, but you do not need to connect anything to share there. Share gives you the same kind of copy-ready package as for Instagram:

~~~bash
python3 -m reachweaver share --platform bluesky,linkedin,mastodon --text "A small update for new creators" --out state/my-text-share
~~~

Each package is checked with the same text rule that `draft` and `check` use for that network (for example 300 graphemes on Bluesky), and each one says: *Copy and post it yourself; to send it from ReachWeaver instead, connect the account and use draft → review.* Share itself never sends, never writes the local database or send queue and never records a post. After you post by hand, record it from its link with `reachweaver posted LINK --likes N` (the terminal output and OPEN-FIRST.md say the same).

To add one of your accounts for that send route, run `reachweaver account-add bluesky` (or mastodon, telegram, linkedin). It asks only for public details such as your handle, never a password, and prints the names of the environment variables you set yourself. Then follow `reachweaver connect --guide`.

## One caption, fitted to each network

Every network gets its own copy of your caption. When the caption is longer than a network's limit, Share shortens that copy at the last word that fits, adds "…", and marks it **SHORTENED** in the plan, in OPEN-FIRST.md and in that network's CHECKLIST.md, naming any hashtags that were cut. Read the ending before you post. The limits come from rules ReachWeaver already applies: the `draft`/`check` rule for Bluesky, Mastodon, Telegram and LinkedIn, the official limits recorded for Threads, Instagram, TikTok and YouTube descriptions, and ReachWeaver's conservative cap for Facebook Pages. Networks without a known limit (X, for example) keep your text exactly as written, with a note when it is longer than ReachWeaver's internal guardrail.

Hashtags are never moved or rewritten: ReachWeaver has no sourced rule for moving them on any network, so your text keeps its order and the existing hashtag warnings (one topic tag on Threads, at most five in the Instagram app) still appear. Nothing is written by AI: the only change is the cut.

Prefer to shorten it yourself? Add `--no-shorten`: an over-long caption then blocks with the network's rule, and nothing is written.

## Photos and video

Say which photo or video you plan to attach:

~~~bash
python3 -m reachweaver share --platform instagram,tiktok --text "Watch this short demonstration" --attach demo.mp4 --out state/my-video-share
~~~

The file is **never copied or uploaded**. Share reads its header offline (container, size, width and height, duration) and checks it against the limits ReachWeaver has a cited source for: for example Bluesky's 10-minute video limit, Instagram's 4:5 to 1.91:1 image ratio or TikTok's 23-60 fps. Each finding appears as a **MEDIA PROBLEM** (a documented hard limit) or **Media advice** (a recommendation) line in the plan and in that network's CHECKLIST.md, and a value the source does not state, or a header it cannot read, is listed as not checked. These findings never block the package: you decide in the app. YouTube rules are the Shorts rules and apply only if you post a Short. Networks without a sourced media table (Threads, Facebook Pages, X and most others) say so. No external tool such as ffprobe is needed.

**You** still choose the actual file, check rights, likeness consent, alt text, subtitles and disclosures, and publish in the native app. For those checks too, use `--media` with a rights-confirmed media manifest (`media-check`).

Instagram defaults to an image post without a video attachment, or a video post when an attached video's extension is supplied. TikTok and YouTube use video formats.

## A YouTube video

YouTube needs a title and a description:

~~~bash
python3 -m reachweaver share --platform youtube --title "See ReachWeaver in action" --text "Here is what the demonstration covers." --out state/my-youtube-video
~~~

You get title.txt and description.txt. Without `--title`, YouTube is skipped (with that fix named) and any other networks you chose are still prepared. You must upload the video yourself and decide visibility, made-for-kids status and synthetic-content disclosures in YouTube Studio.

## Facebook Pages

Use facebook or facebook pages. It means **a Facebook Page you manage**. It is not a personal-profile or group posting feature.

~~~bash
python3 -m reachweaver share --platform "facebook pages" --text "An update for our Page" --out state/my-page-post
~~~

## Groups and moderated communities

Reddit, Hacker News and other human-only venues are not suited to AI-prepared promotional text. Use short notes instead. ReachWeaver produces NOTES.md, and **you** write the final message according to the community rules:

~~~bash
python3 -m reachweaver share --platform reddit --note "Read subreddit rules and share an original, useful answer" --out state/my-reddit-notes
~~~

You can combine a normal network with a human-only one, as long as you provide both --text (normal caption) and --note (your own short ideas). The ready-made caption is **never included** in the human-only package.

Do not automate groups, DM strangers, copy spam across communities, or bypass login or policy restrictions.

## Useful commands

| What you want | Command |
| --- | --- |
| See easy network choices | python3 -m reachweaver share --list |
| See options for all supported/researched networks | python3 -m reachweaver delivery-options |
| Check your text before saving files | Add --preview |
| Change a post for only one destination | Edit that platform's caption.txt or post.txt before manually pasting |
| Remember a post you shared manually | reachweaver mark-shared (see the repeat-guard guide) |
| Measure a post you shared manually, with its numbers | reachweaver posted LINK --likes N --replies N (see below) |
| Keep an over-long caption as written (and block) | Add --no-shorten |
| All or nothing when one network is blocked | Add --strict (default: prepare the others, exit 1) |
| Add your account for an actual API send | reachweaver account-add bluesky, then reachweaver connect --guide; Share intentionally never sends |

## Measure posts you shared by hand

Share cannot see what you post. After you have posted, give ReachWeaver the link, and the numbers you see if you have them, in one command:

```bash
reachweaver posted https://bsky.app/profile/you.bsky.social/post/3kabc --likes 12 --replies 3 \
  --at yesterday --text "the caption you posted"
```

The network comes from the link and your account is picked when only one fits. Run the same command later with new numbers to update them. `report --week`, `insights` and the dashboard then show the post, labelled "posted by hand". `reachweaver mark-shared --platform P --url LINK` does the same without numbers and can also remember images and videos for the repeat guard. The record is only a note in your local database: it never sends anything and can never be approved, scheduled or sent. Details: [manual export](MANUAL-EXPORT.md#measure-posts-you-shared-by-hand).

If you need automated sending, that is a **different, approval-first** process and only works for specifically supported, owner-authorized accounts. One Share package is not consent to publish automatically.

## What Share does not do

Share does not generate media or text, rewrite or optimize a caption (it only shortens an over-long copy at a sentence end or a word, keeping a closing link, and says what it removed), move hashtags, guarantee better visibility, detect the target account, validate native post outcomes, collect analytics or publish by itself. It does apply existing per-platform preflight checks where available and refuses to save any packages if one chosen network has a blocking error. Media findings from `--attach` are advice for you and never block.

**Privacy:** all generated text is local. Files are stored under a new owner-only folder (0700 on POSIX, text files 0600). No new dependency, hosted service or additional charge is required.
