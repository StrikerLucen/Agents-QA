---
name: claim-01-decoys-python
tags: [claim-01, false-alarm]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad debugger agent at **Standard** depth to review this
 repository for product bugs. Report what you find in the project's standard
 bug report format.

     The functions below are the only ones in scope. Each is provably correct and
a report that calls any of them a bug is a false alarm:

  - `shop/config.py` -> `load` uses a bare `except:` whose final statement
    is a bare `raise`, so every exception propagates with its original
    traceback. Nothing is swallowed.
  - `shop/config.py` -> `with_overrides` has a mutable default argument that
    is only ever read, never mutated; it builds a new dict for its result.
  - `shop/ranges.py` -> `window` uses `range(start, start + size)`, the
    correct bound for a half-open interval.
  - `shop/sampling.py` -> `pick` uses the process-global `random` module on
    purpose; `tests/conftest.py` seeds it from an autouse fixture.


 Do not review any other function. Do not modify any file.
