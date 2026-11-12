---
name: claim-07-real-bug-not-flake-python
tags: [claim-07, flake-matrix, critical]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent to diagnose ONE intermittently failing test and
classify it using the project's flake-or-real-bug matrix. State the conclusion
explicitly. Do not modify any file.

The test is `test_totals_occasionally_disagree_under_load` in
`tests/test_flaky.py`. The team has been calling it flaky and is about to mark it
as a known flake and move on.
