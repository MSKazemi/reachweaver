# Creative preflight: an offline editorial checklist for draft posts

**Status: experimental, human review only, not yet in a released package.** This command does not know any network's ranking weights. It does not predict views, check whether sources are true, measure retention or claim any uplift.

## Run it

```bash
reachweaver creative-preflight --file examples/creative-preflight.example.json
reachweaver creative-preflight --file examples/creative-preflight.example.json --json
```

`python3 -m reachweaver creative-preflight ...` does the same from a source checkout. With the standalone Claude Code skill, run `python3 <skill-dir>/scripts/run.py creative-preflight --file <skill-dir>/assets/creative-preflight.example.json`, like every other command.

The input is one local JSON file that you control. Claude Code can help you prepare the drafts, but check every source, permission, claim and privacy question yourself before you share content with an AI provider or publish it.

Exit codes: `0` when the brief was read and checked; `2` with one `ERROR:` line when the file is missing, larger than 256 KB, a symlink, not UTF-8, not valid JSON or does not match the schema. The error names the field at fault.

## The brief

`schema_version` is `reachweaver.creative-preflight.v1`. Required fields:

- `topic`: up to 160 characters;
- `audience`: a `description` and 1-10 `keywords`;
- `goal`: `awareness`, `discussion` or `qualified_visits`;
- `candidates`: 1-6 drafts, each with a unique `id`, a `platform` from the profile-audit registry, a `format` and the `text`.

Optional fields:

- `facts`: up to 10 entries, each with an `id`, a `claim` and an `https://` `source_url`; a draft names the facts it relies on in `evidence_refs`;
- per draft: `title`, `cta`, `alt_text` and a true/false `captions`.

No URL is visited. Unknown fields, unknown platforms, undefined fact IDs and credential-like strings are refused.

## What the report means

Seven disclosed weights (20/20/20/15/10/10/5) measure **how complete the editorial checklist is**. They do not measure algorithm compatibility or the chance of reach. The checks are:

1. a declared audience term appears in the draft;
2. the opening line is 5-26 words and makes no hype promise;
3. the draft names owner-supplied facts;
4. the format is one the registry lists for that platform;
5. the topic appears in the draft;
6. captions (video) or alt text (images) are declared;
7. the declared call to action appears in the draft.

A check that cannot be judged is `unknown`, not a failure, and `assessed_weight` shows how much was judged. For example, an opening in a script written without spaces (Chinese, Japanese, Thai) is `unknown`, because word counts mean nothing there.

A full score never proves that the claims are true, that the creative is original or that a platform will promote the post. The lexical checks can miss multilingual or semantic relevance. Compare individual findings, not scores between networks.

The command reuses `profile_audit_registry.json` and the `seo-content-check` rules for repeated or heavy hashtags, text-length limits and near-identical wording across drafts. The three brainstorming questions are prompts for your own writing, not ready-to-post text.

## Voice findings (optional)

`--voice-profile FILE` adds advisory `voice_findings` per draft from a profile made by `voice-profile`: features outside the usual range of your own posts and stock phrases from a heuristic style list (not an AI detector). The text report lists the `warn` ones; the checklist points and the exit code do not change. See [voice check](VOICE-CHECK.md).

## Human-only destinations

Reddit and Hacker News are human-only, and DEV is human-authored only. A draft for one of them is still checked, with a `human_only` note. When a brief contains one, the report has **no** brainstorming prompts at all: write that text yourself under the community's rules.

## Where it fits

Run it before `campaign-preview`, `claims-check` and `repeat-check`. Publishing is still controlled by exact-item human approval, the live gates, caps and the other safety rules. Platforms without an adapter stay manual-only. The command opens no database and makes no network request. It calls no model, connects no account and writes no file.
