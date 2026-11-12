---
name: claim-04-seeded-bugs-python
tags: [claim-04, seeded-bugs]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent at **Deep** depth to review these four
functions against the SPEC docstrings written directly above them in the
source, and report every product bug you find in the project's standard bug
report format:

  - `shop/invoice.py` -> `cents`
  - `shop/invoice.py` -> `summary_line`
  - `shop/ranges.py` -> `chunk`
  - `shop/accounts.py` -> `contact_email`

Each finding needs `file:line`, severity, and a proposed priority. Do not
modify any file.
