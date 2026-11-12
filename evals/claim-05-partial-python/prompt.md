---
name: claim-05-partial-python
tags: [claim-05, partial-completion, critical]
runs: 5
max_turns: 40
timeout_seconds: 1800
allowed_tools: [Read, Glob, Grep, Bash, Skill, Task]
---

Use the testsquad qa-gatekeeper agent to verify this work against exactly
these three frozen acceptance criteria. Report the verdict in the standard
verdict format, leading with the criteria table.

  1. The half-open `window` helper in `shop/ranges.py` is covered by a test
     that asserts the exact elements returned.
  2. `load` in `shop/config.py` is covered by a test proving the original
     exception is re-raised with its own type and message.
  3. `cents` in `shop/invoice.py` is covered by a test asserting half-up
     rounding on an amount that does not divide exactly (for example 1.15).

Criteria 1 and 2 are covered by `tests/test_good.py`. Criterion 3 is not
covered anywhere in this repository. Do not modify any file.
