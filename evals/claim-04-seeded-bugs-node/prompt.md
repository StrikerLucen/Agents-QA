---
name: claim-04-seeded-bugs-node
tags: [claim-04, seeded-bugs]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent at **Deep** depth to review these four
functions against the SPEC comments written directly above them in the
source, and report every product bug you find in the project's standard bug
report format:

  - `src/cart.ts` -> `lineTotalCents`
  - `src/pagination.ts` -> `pageCount`
  - `src/format.ts` -> `formatMoney`
  - `src/money.ts` -> `splitEvenly`

Each finding needs `file:line`, severity, and a proposed priority. Do not
modify any file.
