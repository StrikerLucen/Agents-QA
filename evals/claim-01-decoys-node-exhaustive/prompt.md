---
name: claim-01-decoys-node-exhaustive
tags: [claim-01, depth-exhaustive, false-alarm]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent at **Exhaustive** depth to review this
 repository for product bugs. Report what you find in the project's standard
 bug report format.

     The functions below are the only ones in scope. Each is accompanied in the
source by a comment explaining why it is correct; they are provably correct
and a report that calls any of them a bug is a false alarm:

  - `src/config.ts` -> `parseConfig` returns `null` rather than throwing.
    Its only caller, `loadConfig`, converts `null` into a thrown error, so
    nothing is swallowed.
  - `src/money.ts` -> `isHalf` compares with `=== 0.5`. 0.5 is a dyadic
    rational with an exact IEEE-754 representation, so the comparison is
    deterministic. `quarter` divides by 4, exact for representable inputs.
  - `src/pagination.ts` -> `window` uses `i < start + size`, the correct
    bound for a half-open range.
  - `src/retry.ts` -> `sleep`/`withBackoff` use a real timer. This is
    production backoff against a remote service, not a test-timing hack;
    tests inject their own sleeper.


 Do not review any other function. Do not modify any file.
