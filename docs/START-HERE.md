# Start here: ReachWeaver in plain words

This guide is for you if you are not a developer, or you are new to this tool. It explains what ReachWeaver does, the few ideas you need, and how one post goes from idea to published. No account or password is needed to read it or to try the first steps.

Looking for the exact commands in order? Use the [quickstart](QUICKSTART.md). Meeting an unfamiliar word? See the [glossary](GLOSSARY.md). Something went wrong? See [troubleshooting](TROUBLESHOOTING.md).

## The idea in one minute

You made something real: a software release, a paper, a talk, a demo. You want the right people to see it. Writing a good post for every network, keeping track of what you sent, and not making a costly mistake is a lot of careful work.

ReachWeaver is a careful helper for that work. It has two halves:

- **Claude Code** (an AI assistant that runs in your terminal or editor) does the thinking and writing: researching your audience and drafting posts.
- **ReachWeaver's commands** do the careful bookkeeping: checking drafts, recording your approval, previewing the send, remembering what happened, and refusing anything unsafe.

The most important rule: **nothing is sent until you approve that exact post.** The AI can suggest and draft. Only you can approve.

## Five ideas you need

| Idea | In plain words |
|---|---|
| **Draft** | A saved post that has not been approved or sent. You can edit or delete it freely. |
| **Approval** | Your explicit "yes" to one exact post. ReachWeaver ties the approval to the exact text. If anyone changes even one character, the approval stops counting and you must approve again. |
| **Dry run** | A rehearsal. ReachWeaver shows what it *would* do and contacts nobody. This is what happens by default. |
| **Live send** | A real post on a real account. It needs several deliberate switches on top of your approval, so it cannot happen by accident. |
| **Manual export** | For networks ReachWeaver cannot send to, it prepares the text and a checklist, and you copy it into the app yourself. |

## The journey of one post

Here is one post, step by step. The commands are real and work offline. The names `my-bluesky` and `POST_ID` come from the example files; ReachWeaver prints your real post ID when you create a draft.

**1. Draft.** Write the text yourself or ask Claude to. Save it as a draft for one account:

```bash
reachweaver draft --account my-bluesky --file examples/example-post.txt --campaign first-campaign
```

ReachWeaver prints `Created draft POST_ID for my-bluesky; not published`. The draft is stored in a private database on your computer.

**2. Review.** See exactly what would be sent:

```bash
reachweaver show POST_ID
reachweaver check POST_ID
```

`show` prints the exact text, the account it is for, its status and a preview of how the network will display it. `check` points out problems such as placeholder text, a post that is too long, or a mention of someone who asked not to be tagged. Fix the text and draft again if needed.

**3. Approve.** When you are happy with it:

```bash
reachweaver approve POST_ID --approver YOUR_NAME
```

This records your decision and locks it to the exact text. It still sends nothing.

**4. Rehearse.** Run the send as a dry run:

```bash
reachweaver publish --post-id POST_ID
```

You should see `DRY RUN: 1 due approved posts; no requests sent`. The post stays approved, waiting. If you only wanted to practise, you can stop here.

**5. Send, or hand over.**

- If the account is on a platform ReachWeaver can send to, and you have finished the [security checklist](SECURITY-CHECKLIST.md) and [account onboarding](ACCOUNT-ONBOARDING.md), a real send is a separate, deliberate command that you run yourself. It is described in the [first live pilot](FIRST-LIVE-PILOT.md). Start with one post on a throwaway account.
- If the platform is not supported for sending, use `manual-export` to get a ready-to-paste package and post it yourself. See [manual export](MANUAL-EXPORT.md).

**6. Record and learn.** After a post is live, you can import the numbers from the platform's own analytics and get an honest report. Numbers you do not have are shown as *unknown*, never as zero. See [attribution](ATTRIBUTION.md) and the [weekly report example](WEEKLY-REPORT-EXAMPLE.md).

## Three ways to use it

| You are... | Use | Best for |
|---|---|---|
| Comfortable chatting with an AI assistant | **Claude Code with the `/reachweaver` skill** (see the [README](../README.md#get-started)) | Research, campaigns, drafts. Claude runs the checks for you and always stops for your approval. |
| Comfortable with a terminal | **The `reachweaver` commands directly** | Full control, scripting, a repeatable routine. |
| Not ready to connect any account | **Offline mode plus manual export** | Preparing posts and plans, then pasting them in by hand. This needs no credentials at all. |

You can mix them. For example, let Claude draft, then review and approve in the terminal.

## What is safe to try right now

Everything in the [quickstart](QUICKSTART.md) is safe. It uses example accounts, a throwaway database and no network. You cannot post by accident, because the live switches are off.

## What you need before a real send

All of these, never fewer:

1. An account that **you** own or are allowed to post for.
2. Credentials for it, created with the narrowest permissions and kept **only in your own environment**. Never type them into Claude, a chat, an issue, or a file in the repository. [Account onboarding](ACCOUNT-ONBOARDING.md) shows how for each platform.
3. A post you approved with `approve`.
4. The three live switches, set on purpose: `--live`, `REACHWEAVER_ALLOW_LIVE=1` and `REACHWEAVER_ENV=prod`.
5. Your read-through of the [security checklist](SECURITY-CHECKLIST.md).

Today the real-send adapters are for Bluesky, Mastodon, Telegram and LinkedIn personal profiles, text only. Only Bluesky and Mastodon have been proven with real posts. See the [platform matrix](PLATFORM-MATRIX.md) for the dated status of everything.

## Questions people ask

**Will it post by itself?** Not by default. Every send needs your approval of that exact post and the live switches. Advanced users can set up a scheduled sweep that only sends posts you already approved; that is described in [worker operations](WORKER-OPERATIONS.md) and is a deliberate setup, not something that happens on its own.

**Does it need my password?** No. It never asks for or stores your password. For platforms that allow it, you create a limited-permission token (for Bluesky an *App Password*, never your main password) and keep it in your own environment.

**Where is my data?** On your computer, in the `state/` folder, in a database only your user can read. ReachWeaver has no server of its own and uploads nothing there. The only things that touch the internet are a send to the platform you chose (`--live`), commands you start with an explicit `--online` flag, and a once-a-day check for a newer ReachWeaver release when it runs as a Claude Code plugin (you can turn that off). The [README](../README.md#security-and-reality-checks) lists exactly what each one contacts. See also the [privacy policy](PRIVACY.md).

**Can I take something back?** Before it is sent, yes: `unapprove POST_ID` withdraws approval and `cancel POST_ID` removes it from the queue. After it is sent, ReachWeaver cannot delete a post for you; delete it in the platform's own app.

**What if I am unsure whether a post went out?** ReachWeaver will not guess and will not resend. The post is marked `needs_review` and that account is held until you look at the real account and tell ReachWeaver what happened with `reconcile`. This is on purpose, so you never double-post by accident.

**What if something looks wrong right now?** Run `reachweaver pause`. It stops all publishing immediately. `reachweaver resume` turns it back on once you have checked.

**Will it make my post go viral?** No tool can promise that, and ReachWeaver does not. It helps you write platform-appropriate posts, avoid mistakes, and measure what actually happened.

**Is it allowed to post on Reddit or Hacker News for me?** No. Those communities expect posts written and submitted by a person, so ReachWeaver writes nothing for them. It can still help you understand their rules.

## Good habits

- Start with one post, on a test account, and check it on the real platform.
- Read the `show` output every time before you approve. The approval is only as good as your reading.
- Keep credentials out of anything you share, including screenshots and bug reports.
- Put claims you cannot source (numbers, "first", "fastest") through `claims-check`, or leave them out.
- When in doubt, use manual export. You lose nothing but a copy and paste.

## Where to go next

- [Quickstart](QUICKSTART.md): the safe ten-minute walk-through.
- [All documentation by goal](README.md).
- [All features in detail](FEATURES.md).
