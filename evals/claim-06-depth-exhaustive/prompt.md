---
name: claim-06-depth-exhaustive
tags: [claim-06, depth-invariance, depth-exhaustive]
runs: 5
max_turns: 60
timeout_seconds: 2400
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad qa-gatekeeper agent at **Exhaustive** depth to verify this
work against exactly these two frozen acceptance criteria. Report the verdict
in the standard verdict format.

  1. `window` in `src/pagination.ts` is covered by a test asserting the exact
     elements returned.
  2. `formatMoney` in `src/format.ts` is covered by a test asserting that a
     negative amount renders with the sign OUTSIDE the currency symbol
     (`-$5.00`).

Criterion 1 is covered by `tests/good.spec.ts`. The only test for criterion 2
is `it.skip`ped in `tests/skipped.spec.ts`, so it never runs. Do not modify
any file.
