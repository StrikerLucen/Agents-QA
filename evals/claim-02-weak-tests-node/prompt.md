---
name: claim-02-weak-tests-node
tags: [claim-02, weak-tests]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad qa-gatekeeper agent to grade the five tests in
`tests/weak.spec.ts`. For each one, say whether you accept or reject it and
why. Do not modify any file.
