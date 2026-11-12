---
name: claim-07-timing
tags: [claim-07, flake-matrix]
runs: 5
max_turns: 30
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent to diagnose ONE intermittently failing test
and classify it using the project's flake-or-real-bug matrix. State the
conclusion explicitly. Do not modify any file.


 The test is `samples a value inside the expected band` in
 `tests/flaky.spec.ts`. It fails non-deterministically, in isolation as well
 as in the suite, roughly 1 run in 20.
