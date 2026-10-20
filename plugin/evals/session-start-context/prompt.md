---
description: Session start runs the one read-only context call before anything else
tags: [session, smoke]
expected_outcome: Claude runs context --json, summarises it and changes nothing
max_turns: 25
timeout_seconds: 480
allowed_tools: [Read, Glob, Grep, Skill, Bash, Write, Edit]
---

I'm back to work on my project's social posts. Before we write anything, what is the state of my ReachWeaver setup and is anything waiting for me?
