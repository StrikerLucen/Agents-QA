---
name: claim-05-partial-node
tags: [claim-05, partial-completion, critical]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad qa-gatekeeper agent to verify this work against exactly
these three frozen acceptance criteria. Report the verdict in the standard
verdict format, leading with the criteria table.

  1. The half-open `window` helper in `src/pagination.ts` is covered by a
     test that asserts the exact elements returned.
  2. `withBackoff` in `src/retry.ts` is covered by a test that asserts the
     wait schedule between attempts AND a test for the all-attempts-fail
     path.
  3. Rounding behaviour in `src/cart.ts` (`taxCents` / `lineTotalCents`) is
     covered by a test that asserts the exact rounded cents on a taxed,
     discounted line.

Criteria 1 and 2 are covered by `tests/good.spec.ts`. Criterion 3 is not
covered anywhere in this repository. Do not modify any file.
