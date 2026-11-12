---
name: claim-11-idempotent-start
tags: [claim-11, idempotence]
runs: 5
max_turns: 60
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task, Write, Edit]
---

This repository has already been onboarded with testsquad: `.testing/` is
populated and `CLAUDE.md` already carries the testsquad block.

Run `/testsquad:start` on it again.
