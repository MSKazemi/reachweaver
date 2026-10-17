# Ten additional social networks (2026-10-17)

ReachWeaver adds **ten previously missing distribution destinations** from a 100-channel baseline, expanding the catalog to **110**. All ten use the existing generic offline manual-export workflow. **No new automatic publishing, account connection, login, API client, external network call, or supported live adapter is introduced.**

| Platform | Focus | Scheduling evidence | ReachWeaver |
| --- | --- | --- | --- |
| Misskey (`misskey`) | Japanese federated conversations | Unverified | Manual-only |
| Lemon8 (`lemon8`) | Visual discovery | Unverified | Manual-only |
| BAND (`band`) | Group communities | [Native UI scheduling](https://help.band.us/help/1044) only | Human-only notes |
| Niconico (`niconico`) | Japanese video | Unverified | Manual-only |
| Likee (`likee`) | Short video | Unverified | Manual-only |
| MeWe (`mewe`) | Group communities | Unverified | Human-only notes |
| Odysee (`odysee`) | Independent video | Unverified | Manual-only |
| Rumble (`rumble`) | Video | Unverified | Manual-only |
| Minds (`minds`) | Social discussion | Unverified | Manual-only |
| diaspora* (`diaspora`) | Federated social | Unverified | Manual-only |

Official/service starting points are recorded individually as `research_source` in `reachweaver/distribution_registry.json`. A platform website is a **research pointer only**, not proof of developer access or marketing permission. A future live implementation requires first-party terms/scope review, owner approval, supervised evidence, and security review. Native BAND scheduling is not a ReachWeaver scheduler.

## Manual-export example

Create `state/misskey.json` with an account-owner-reviewed post (text is a synthetic example):

```json
{"platform": "misskey", "text": "Original tutorial: https://example.org/demo"}
```

Then run locally:

```bash
reachweaver manual-export --platform misskey --variant state/misskey.json --out state/misskey-export
```

This generates review files, **not** an online post. For BAND/MeWe, instead supply `talking_points` without `text`: the existing generic package enforces human-only wording. The community member must author their own message and check group rules. No unsolicited bulk outreach, scraping, browser-auth bypass, or unapproved posting.

Markets (global, Japan, South Korea, India and Brazil) have a few new lower-priority research hypotheses; planner results do not verify audience size, optimal timing, ranking algorithms or future posting.

**Provenance:** original AI-assisted short research metadata and synthetic offline test cases; no copied platform code, tokens, media or personal data. Keep the private development-compliance record outside the public mirror. Review official platform terms before any operational use.
