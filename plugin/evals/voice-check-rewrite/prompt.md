---
description: Claude runs voice-check on a draft and rewrites the flagged lines itself
tags: [quality, voice]
expected_outcome: voice-check runs; the final text drops the flagged stock phrases and keeps the facts
max_turns: 25
timeout_seconds: 480
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Here's my draft for LinkedIn. Check it sounds like me and fix anything that reads like generic AI copy, then show me the final text.

In today's fast-paced world, I'm thrilled to announce tidyfiles 1.2 - a game-changer for anyone who works with messy folders. Let's delve into the new dry-run mode that shows every move before it happens.
