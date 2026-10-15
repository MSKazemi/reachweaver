# Peer benchmark from public counters (issue #11)

`peer-benchmark` compares your own public counters with those of a few peers, using **only numbers you copied by hand from public pages into a local file**. It makes no request, logs in nowhere, reads no private analytics, scrapes nothing and stores nothing. Nothing in it is a ranking, a growth claim or permission to contact anyone.

```bash
reachweaver peer-benchmark --file examples/peer-benchmark.example.json
reachweaver peer-benchmark --file state/peers.json --platform mastodon --json
```

## The file

`schema_version: "reachweaver.peer-benchmark.v1"` and a list of `entries` (at most 200). Every entry has exactly these keys:

| Key | Rule |
|---|---|
| `label` | Short single-line name (yours or the peer's). |
| `platform` | A platform id from the profile-audit registry (for example `mastodon`, `youtube`, `linkedin`). |
| `kind` | `organization`, `project` or `community`. `person` is refused unless you pass `--include-individuals`. |
| `own` | `true` for your account (exactly one per platform), `false` for a peer. |
| `public_data_only` | Must be `true`: you confirm the counters are shown on a public page without logging in. |
| `source_url` | The public page you read, as a plain `https` URL: no credentials, query string or fragment. |
| `observed_at` | When you read it (ISO 8601 with a time zone). |
| `counters` | At least one of `followers`, `following`, `posts`, `likes`, `reposts`, `replies`, `views`, each a non-negative whole number. Leave a counter out when the page does not show it; absent is unknown, never zero. |

Credential-like field names and values are refused, as are unknown keys. Errors say what is wrong and never print the file.

## What it reports

Per platform and counter: your value, the number of peers with a value, and, only with **at least three peers**, their minimum, median and maximum, how many peers you are at or above, and your ratio to the peer median. With fewer peers the status is `insufficient_peers`; with no peer value, `no_peer_values`. Warnings appear when the observation dates are more than 30 days apart (the counters are not from the same period) or the oldest is over 90 days old.

## Limits you should keep in mind

- Public counters mean different things on different platforms and are not comparable across them. Compare within one platform only.
- A counter can be edited, hidden or deleted by its owner, and says nothing about quality, cause or private performance. A position among a few chosen peers is not a market ranking.
- Reading a public page yourself is your action. If you later want a tool to fetch pages, that is a separate decision with platform-terms and privacy review; this command deliberately has no fetch path.
- Individuals' counters are excluded by default because they can be personal data even when public. Organisation, project and community pages are the intended input. This note is not legal advice.
