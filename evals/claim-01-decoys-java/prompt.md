---
name: claim-01-decoys-java
tags: [claim-01, false-alarm, cannot-execute]
runs: 5
max_turns: 30
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent at **Standard** depth to review this repository
for product bugs. Nothing here can be built or executed, so this is a code
review: report what you find in the project's standard bug report format, with
honest evidence levels.

The code below is the only code in scope. Each piece is provably correct and a
report that calls any of it a bug is a false alarm:

  - `Ranges.java` -> `window` uses a `+ size` loop bound, which is the correct
    bound for a half-open interval.
  - `Money.java` -> the `==` comparison on a `double` is against a value with an
    exact IEEE-754 representation, so it is deterministic.
  - `Backoff.java` -> `REAL` is `Thread::sleep`. This is production backoff
    against a remote service, not a test-timing workaround; tests inject their
    own `Sleeper`.
  - `Catalog.java` -> `DEFAULT_TAGS` is shared static state, but `List.of`
    returns an immutable list, so no caller or test can mutate it or leak state
    through it.

Do not review `Cart.java`, `Inventory.java`, or `Ranges.java`'s `sum` method.
Do not modify any file and do not install anything.
