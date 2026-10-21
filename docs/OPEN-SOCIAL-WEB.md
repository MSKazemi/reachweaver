# The open social web: protocols, bridges and reach without double posting

Some networks are not single websites. Mastodon, Threads (when you turn it on), Ghost 6, WordPress with a plugin, Pixelfed, Lemmy, Misskey, PeerTube and Flipboard can talk to each other over **ActivityPub**. Together they are called the **fediverse**. Bluesky runs on a different open protocol, the **AT Protocol** (its network is called the **Atmosphere**). Nostr and Farcaster are two more open protocols. A **bridge** such as Bridgy Fed can connect some of these networks.

This matters for planning because one account can be followed from several networks. If you also keep a second account on one of those networks and post the same text on both, some people see it twice.

`reachweaver social-web-plan` maps your accounts onto these protocols and points out where that happens. It only reads: it never contacts a network, reads a credential, opens the database, touches the send queue, or creates, approves or publishes anything.

## Quick start

```bash
# Your accounts from config/accounts.json (public metadata only), nothing else
python3 -m reachweaver social-web-plan

# Add what accounts.json cannot hold: your site, other networks, bridges you turned on
python3 -m reachweaver social-web-plan --site https://example.org --site-kind ghost \
    --presence pixelfed --bridged my-bluesky

# Threads with fediverse sharing on, machine-readable
python3 -m reachweaver social-web-plan --fediverse-sharing my-threads --json

# Or describe everything once in a file (see examples/social-web-setup.example.json)
python3 -m reachweaver social-web-plan --setup social-web-setup.json
```

A missing `config/accounts.json` is reported as `"accounts_file": "missing"`, not an error, so you can try the command before you set up any account.

## What the plan shows

| Section | What it means | How sure it is |
|---|---|---|
| Presences | Each account, the protocol it uses, and which networks it reaches (`native`, `opt_in_sharing`, `bridgy_fed`, `rollout_unverified`) | Protocol facts are sourced (below). Your bridges, sharing settings and extra presences are **your own statement**; nothing is checked online |
| Canonical home | Where to publish the full piece first: an owned site with ActivityPub, then any owned site, then a newsletter, then an open-protocol account | **Editorial hypothesis**, not a measured result |
| Duplicate reach | Where two of your presences reach the same network, for example a Bluesky account bridged into the fediverse plus a native Mastodon account | The mechanism is sourced. "Some people see it twice" is a **hypothesis**: it only happens to people who follow both, and no audience data is read |
| Identity checklist | `fediverse:creator` tag, Mastodon verified links (`rel="me"`), a domain as your Bluesky handle, bridged handles | Steps quote the official docs. `reported_done` means you said so in the setup file; `looks_done_not_verified` means your Bluesky handle is not a `bsky.social` name, nothing more |
| Gaps | No open protocol, no owned home, fediverse but not Bluesky (or the reverse), Threads sharing not recorded as on, networks with no protocol recorded | Suggestions are options, not instructions |

A network that is not in the registry is shown as `none_recorded`. That means "no open protocol is recorded here", **not** "verified to have none".

## Bridges and opt-in

- **Bridgy Fed** connects the web, the fediverse and the Atmosphere (Bluesky). It is **opt in**: you turn it on yourself (on Bluesky by logging in at fed.brid.gy and clicking Enable, or by following `@ap.brid.gy`; on the fediverse by following `@bsky.brid.gy@bsky.brid.gy`) and turn it off by blocking the bot. It **mirrors one existing account** under a new bridged handle (a Bluesky account appears in the fediverse as `@[handle]@bsky.brid.gy`). It does not copy posts between two accounts you already have, which is exactly why a bridged account plus a native one can double up. Its docs say Farcaster and Nostr are being considered, not supported.
- **Threads fediverse sharing** is an account setting you turn on. Meta's June 2024 update called it a beta, for people 18 and over with public profiles; the current status was not re-checked.
- **Ghost 6** builds ActivityPub into the publication, and the **WordPress ActivityPub plugin** makes the blog itself a federated profile. Both are your choice to switch on.

ReachWeaver never turns a bridge or a sharing setting on or off, and the plan is never permission to post.

## Using the warnings

When the plan warns about duplicate reach, choose one:

1. Post once natively and let the bridge or sharing carry it.
2. Write a different version for each account.
3. Turn the bridge or sharing off for one of them.

`social_web.bridge_duplicate_risk(presences)` returns the same warnings as data, so other planning code can show them. It is advice only: it is **not** connected to `approve`, `publish` or the repeat guard, and it cannot block or change a post.

## The setup file

`--setup FILE` takes a JSON file with `"schema": "reachweaver.social-web-setup.v1"` and these optional fields:

| Field | Example | Meaning |
|---|---|---|
| `site` | `{"url": "https://example.org", "kind": "ghost"}` | Your owned site; `kind` is `ghost`, `wordpress_activitypub`, `bridgy_fed_web` or `plain` |
| `presence` | `[{"platform": "pixelfed", "id": "my-pixelfed", "handle": "@me@pixelfed.example"}]` | Networks `accounts.json` cannot hold, by distribution registry id |
| `bridges` | `[{"bridge": "bridgy_fed", "account": "my-bluesky"}]` | Accounts you bridged |
| `fediverse_sharing` | `["my-threads"]` | Threads accounts with sharing on |
| `identity` | `{"fediverse_creator": true, "mastodon_link_verification": false}` | Steps you say you have done |

Unknown fields are refused. Never put a password or token in it; the file holds public facts only. Command-line flags add to what the file says.

## Sources (checked 2026-10-21)

Every fact in `reachweaver/social_web_registry.json` carries the URL and the exact sentence it rests on. The main ones:

| Fact | Source |
|---|---|
| ActivityPub is "A decentralized social networking protocol based upon the ActivityStreams 2.0 data format and JSON-LD." | [Mastodon docs](https://docs.joinmastodon.org/spec/activitypub/) |
| Lemmy "uses a standardized, open protocol to implement federation which is called ActivityPub"; the fediverse includes Mastodon and PeerTube | [Lemmy docs](https://join-lemmy.org/docs/introduction.html) |
| Bluesky developer platform is "Backed by AT Protocol." | [docs.bsky.app](https://docs.bsky.app/) |
| "Threads users who turn on sharing to the fediverse can see and like replies from people on other servers, like Mastodon, directly in Threads." | [Meta newsroom](https://about.fb.com/news/2023/07/introducing-threads-new-app-text-sharing/) |
| Ghost 6: "Distribution is now built-in." | [Ghost changelog](https://ghost.org/changelog/6/) |
| "With the ActivityPub plugin installed, your WordPress blog itself functions as a federated profile" | [WordPress plugin directory](https://wordpress.org/plugins/activitypub/) |
| Flipboard began opening to the fediverse, "all built on a revolutionary open protocol called ActivityPub." | [Flipboard](https://about.flipboard.com/inside-flipboard/flipboard-begins-to-federate/) |
| Misskey "became decentralized through the adoption of ActivityPub in 2018." | [Misskey Hub](https://misskey-hub.net/en/docs/about-misskey/) |
| PeerTube: "It's based on ActivityPub to connect with tools like Mastodon for example." | [joinpeertube.org](https://joinpeertube.org/) |
| "Bridgy Fed is opt in."; "Bridgy Fed doesn't cross-post (ie copy posts) between separate accounts." | [Bridgy Fed docs](https://fed.brid.gy/docs) |
| `fediverse:creator`: "The handle can be any fediverse account, not just Mastodon." | [Mastodon blog](https://blog.joinmastodon.org/2024/07/highlighting-journalism-on-mastodon/) |
| Mastodon link verification with `rel=me` | [Mastodon docs: profile](https://docs.joinmastodon.org/user/profile/) |
| "On Bluesky, you can set your website as your username. This is one form of verification on Bluesky." | [Bluesky blog](https://bsky.social/about/blog/4-28-2023-domain-handle-tutorial) |
| Nostr, Farcaster, Matrix, diaspora* protocol statements | [nostr-protocol/nostr](https://github.com/nostr-protocol/nostr), [farcasterxyz/protocol](https://github.com/farcasterxyz/protocol), [matrix.org](https://matrix.org/), [diaspora* federation](https://diaspora.github.io/diaspora_federation/) |

What is **not** verified: whether a given Flipboard account is federated today, whether an ordinary Minds account federates (Minds' home page claims ActivityPub interoperability for its Networks product), whether a diaspora* pod also speaks ActivityPub, the current status of the Threads beta, and anything about your own accounts. Those entries say so.

## Keeping the registry honest

`reachweaver/social_web_registry.json` is validated by `social_web.validate_registry` and `tests/test_social_web.py`: platform ids must exist in `distribution_registry.json`, every entry needs an HTTPS source with its quote and a `checked_on` date no later than `reviewed_on`, bridges must stay `opt_in`, Threads federation must stay `opt_in`, and the canonical-home policy must stay labelled a hypothesis. To change a fact, fetch the official page, copy the sentence exactly, update the date, then run `python3 scripts/sync_skill.py` and the tests.
