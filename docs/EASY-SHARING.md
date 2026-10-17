# Share a post in a few simple steps

This is the easiest way to prepare social posts with ReachWeaver. You can use it **without an account connection, password, payment or API key**. You do not need to understand JSON.

**Important:** this feature prepares private folders with captions and checklists. It does **not** sign in, upload, schedule or publish anything. You finish each post in the social media app yourself.

## Even easier: open the browser composer

From the ReachWeaver folder:

```bash
python3 -m reachweaver share --web --open
```

Use its editor, tap the social-network choices, preview the exact captions and save a new private folder. Then open the native platforms and publish yourself. It never connects to a social account, uploads files or publishes. See [the browser workflow guide](BROWSER-SHARE-COMPOSER.md).

## Option A: answer a few questions

From the ReachWeaver project folder, enter:

~~~bash
python3 -m reachweaver share --guide
~~~

ReachWeaver asks which networks you want (for example, instagram,threads), asks for your caption, reviews the requirements and asks whether to save the package. It creates a private folder named something like state/share-YYYYMMDD-HHMMSS.

Open the **OPEN-FIRST.md** file in that folder. It says what to paste into each app, which options to choose yourself, and what to double-check before posting. Every platform has its own caption/post and CHECKLIST.md.

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

## Photos and video

Say which photo or video you plan to attach:

~~~bash
python3 -m reachweaver share --platform instagram,tiktok --text "Watch this short demonstration" --attach demo.mp4 --out state/my-video-share
~~~

The video is **only listed as a reference**; it is not copied, validated or uploaded. In Instagram and TikTok, **you** choose the actual file, check the video/photo specifications, rights, likeness consent, alt text/subtitles and disclosures, and publish in the native app.

Instagram defaults to an image post without a video attachment, or a video post when an attached video's extension is supplied. TikTok and YouTube use video formats. The filename extension alone is *not proof* that media is valid: use media-check with a rights-confirmed media manifest if you want technical validation.

## A YouTube video

YouTube needs a title and a description:

~~~bash
python3 -m reachweaver share --platform youtube --title "See ReachWeaver in action" --text "Here is what the demonstration covers." --out state/my-youtube-video
~~~

You get title.txt and description.txt. You must upload the video yourself and decide visibility, made-for-kids status and synthetic-content disclosures in YouTube Studio.

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
| Connect an account for an actual API send | See the security checklist and account onboarding; Share intentionally never sends |

If you need automated sending, that is a **different, approval-first** process and only works for specifically supported, owner-authorized accounts. One Share package is not consent to publish automatically.

## What Share does not do

Share does not generate media, change or optimize the text differently per platform, guarantee better visibility, detect the target account, validate native post outcomes, collect analytics or publish by itself. It does apply existing per-platform preflight checks where available and refuses to save any packages if one chosen network has a blocking error.

**Privacy:** all generated text is local. Files are stored under a new owner-only folder (0700 on POSIX, text files 0600). No new dependency, hosted service or additional charge is required.
