---
name: claim-10-cannot-execute-vs-fails
tags: [claim-10, health-gate, cannot-execute]
runs: 5
max_turns: 25
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Take a suite-health baseline for this repository and produce the first two
roadmap items you would propose. This is a Maven project with no wrapper, no
`target/` and no local repository: it is meant to be inspected, not built.
Do not modify any file and do not install anything.
