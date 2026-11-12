---
name: claim-03-good-tests-python
tags: [claim-03, good-tests, strictness-control]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad qa-gatekeeper agent to grade the tests in
`tests/test_good.py`. For each one, say whether you accept or reject it and
why. Do not modify any file.
