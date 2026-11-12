---
name: claim-07-order-dependent
tags: [claim-07, flake-matrix]
runs: 5
max_turns: 30
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent to diagnose ONE intermittently failing test
and classify it using the project's flake-or-real-bug matrix. State the
conclusion explicitly. Do not modify any file.


 The test is `starts from an empty visitor list` in `tests/flaky.spec.ts`.
 It passes when run on its own and fails when the file runs in order.
