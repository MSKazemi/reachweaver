# Milestone follow-up suggestions (`followup-suggest`)

Some schedulers can act automatically when a post passes a milestone: like it, comment under it, DM the people who engaged, or repost it. ReachWeaver does none of that. `reachweaver followup-suggest` does something narrower. When one of your published posts reaches a threshold **you** set, it suggests a follow-up for you to review. Each suggestion is a short brief plus the normal next commands (`draft`, then `show`, then your own `approve`; or `idea add`).

> **Suggestion for review; nothing is drafted, approved or sent.** The command opens the local database read-only, makes no network request and has no path to like, reply, message, repost, follow, draft or publish.

## Quickstart

```bash
cp examples/followup-rules.example.json config/followup-rules.json   # edit thresholds to taste
reachweaver import-metrics native.csv                                  # or metrics-fetch for public counters
reachweaver followup-suggest --rules config/followup-rules.json
reachweaver followup-suggest --rules config/followup-rules.json --json --since 2026-10-01T00:00:00Z
# Remember what was already suggested, so the same post/rule pair is not suggested again:
reachweaver followup-suggest --rules config/followup-rules.json --state state/followups-state.json --record
```

Flags: `--rules FILE` (optional, see below), `--account ID` (only that account's posts), `--since DATE` (only posts published at or after an ISO 8601 time with a timezone), `--state FILE` and `--record` (dedupe, see below), `--optout FILE` (default `$REACHWEAVER_OPTOUT` or `config/optout.json` if present), `--now ISO` (fixed time for snapshot ages) and `--json`. It uses the global `--db` and `--accounts`.

## Without `--rules`: thresholds from your own results

`reachweaver followup-suggest` with no `--rules` decides what "took off" means for each account from that account's own results. The threshold is `ceil(2 x median)` interactions (likes + replies + reposts, all exact in one snapshot), using the median at the matched age of about 7 days (168 h +/- 24 h, [insights method](INSIGHTS.md#where-your-results-are-used)). A post is compared only against snapshots from the same source, taken no later than 192 h after posting. The output starts with "Thresholds derived from your own results" and shows each account as `derived from your N posts: 2 x median M = T`. Each brief repeats that basis, and the JSON carries `derived_thresholds`. The derived kind is `recap_thread`. The rule id is `took-off-ACCOUNT`, so `--state`/`--record` dedupe works as usual.

When an account has fewer than 10 posts with a result, has a median of 0, or is on a human-only platform, it gets no rule and the reason is shown. If no account qualifies, the command exits 0 with a friendly message: pass `--rules FILE`, or record results first. In that case `--json` gives `"status": "too_little_data"`. `--rules FILE` replaces the derived rules completely.

## Rules file

```json
{
  "schema_version": 1,
  "kind": "reachweaver.followup-rules",
  "rules": [
    {"id": "replies-10-day1", "metric": "replies", "threshold": 10, "within_hours": 24,
     "platforms": ["bluesky", "mastodon"], "kind": "thank_and_link",
     "link": "https://example.org/docs/getting-started"},
    {"id": "likes-100-adapt", "metric": "likes", "threshold": 100, "kind": "cross_post",
     "target_platforms": ["linkedin", "threads"]}
  ]
}
```

A bare list of rules is accepted too. The full example is [`examples/followup-rules.example.json`](../examples/followup-rules.example.json).

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique lowercase id (letters, digits, `-`), used in the output and the dedupe state |
| `metric` | yes | One of `views`, `impressions`, `likes`, `replies`, `reposts`, `saves`, `clicks`, `signups` |
| `threshold` | yes | Integer 1 or more; the rule matches when the exact value is at least this |
| `kind` | yes | `thank_and_link`, `recap_thread`, `cross_post`, `pin_or_feature` or `write_up` (below) |
| `within_hours` | no | Only count an observation made within this many hours of publishing (1 to 8784) |
| `platforms` | no | Only posts on these platforms |
| `accounts` | no | Only posts of these account ids |
| `link` | no | A public `https` URL: the resource a `thank_and_link` follow-up points to |
| `target_platforms` | no | `cross_post` only: limit the adaptation targets to these platforms |
| `note` | no | One line (up to 280 characters) added to the brief, for example "point to the guide, not the pricing page" |

Unknown keys, duplicate ids, unknown metrics or platforms, a non-integer threshold, a non-public link, or a Reddit or Hacker News cross-post target are refused with exit code 2 and a message naming the rule. The rule set never half-loads.

## What each kind suggests

| Kind | Suggestion | Next commands |
|---|---|---|
| `thank_and_link` | A short new post from the same account that thanks readers and points to one resource (the rule's `link`, or one you name). No request to like, share or follow. | `RW show POST`, `RW draft --account ID --file followup.txt [--source-url LINK]`, then you review and approve |
| `recap_thread` | A recap of what the post covered and what readers asked, linking back to the original | `RW show POST`, `RW draft --account ID --file recap.txt --source-url ORIGINAL` |
| `cross_post` | An adaptation (not a verbatim copy) for each other configured account on a different platform | `RW draft --account OTHER --file adapted-PLATFORM.txt`, then `RW repeat-check --post-id NEW_POST_ID` |
| `pin_or_feature` | A manual note: pin or feature the post yourself in the official app. ReachWeaver has no pin action. | `RW show POST` |
| `write_up` | An idea for a longer article; captured in the ideas inbox, not drafted | `RW idea add '...' --source ORIGINAL --tag followup` |

A brief is guidance for whoever writes the draft (you, or Claude in Claude Code). It is not post text. Every resulting draft still goes through `show`, `check` and **your own** `approve`, exactly like any other post.

## What it reads, and which values count

- Only posts with status `published` and no reply target (your own top-level posts), newest first, up to 5000. The publish time is the latest `published` event, else the send start, else the scheduled or creation time, the same rule `journal` uses.
- The metric snapshots of those posts, from `import-metrics` or `metrics-fetch`. For each rule it uses the **latest exact** value of the metric (within `within_hours` of publishing, when set) and reports its value, source, observation time, age in hours and, when known, the snapshot window.
- **Estimates are never compared.** A value stored as `estimated:N`, `suppressed`, `delayed` or `not_available` is not an exact count. It is never used, like in every other ReachWeaver reader, and the skip names the status.
- Crossing a threshold is an observation. It does not show that the post caused anything, and it says nothing about future reach.

## Hard rules

- No automated engagement of any kind: no auto-like, auto-comment, auto-reply, auto-DM, auto-repost or auto-follow. There is no code path for them.
- **Reddit and Hacker News** (and every community the distribution registry marks human-only) get **no** follow-up suggestion, and are never a cross-post target. Those communities are human-only or moderator-controlled; write there yourself under their rules.
- **Opt-out list respected.** People the original post mentions may be suggested for credit, but a handle on your [opt-out list](PRIVACY-LIFECYCLE.md#opt-out-list) is never named; the suggestion only says how many were withheld.
- Nothing is drafted, approved, scheduled or sent. The output is text for a person to act on.

## Privacy and files

- The database is opened with SQLite `mode=ro` plus `query_only`. A missing database fails with exit 2 and is never created; an older schema is never migrated (run `reachweaver init` once to upgrade it).
- Post text is read only to find @-mentions and is never printed.
- `--state FILE` is a private JSON file of `(post_id, rule_id)` pairs and a timestamp, nothing else. It is read when present (a symlink or a file readable by others is refused) and written only with `--record`, atomically, with mode 0600. Without `--record`, the command writes nothing at all.
- No network, no model call, no credential.

## In Claude Code

After metrics are imported (`import-metrics`, `metrics-fetch`), Claude can run `followup-suggest --rules FILE --json` and offer the suggestions. It drafts a follow-up only when you choose one, through the normal `draft` command, and never approves, publishes or engages for you. See the skill's `references/engagement.md`.
