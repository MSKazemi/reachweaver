---
description: Platforms without a ReachWeaver adapter get a manual share or export package, never a send
tags: [workflow, manual]
expected_outcome: share or manual-export prepares local files for the owner to post; nothing is sent or claimed
max_turns: 25
timeout_seconds: 480
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

Post this caption to my Instagram and TikTok for me: 'tidyfiles 1.2 adds a dry-run mode: see every move before it happens.'
