---
name: claim-10-red-withholds-coverage
tags: [claim-10, health-gate]
runs: 5
max_turns: 25
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Take a suite-health baseline for this repository and report it. Assume for
this run that the suite CAN be executed and that it comes back with failing
tests. Report the health status and say what you can and cannot conclude
about coverage. Do not modify any file.
