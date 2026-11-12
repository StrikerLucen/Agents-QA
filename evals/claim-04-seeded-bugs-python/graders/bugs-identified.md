---
type: llm
---

Four bugs are seeded in the functions under review. A correct response
identifies them (wording may differ; the substance must match):

  1. `cents` uses `int(amount * 100)`, which TRUNCATES. The SPEC requires
     half-up rounding; `cents(1.15)` yields 114 instead of 115, so money
     is lost per line.
  2. `summary_line` always renders "items" and never pluralises for a
     count of 1; the SPEC requires "1 item".
  3. `chunk` iterates `range(0, len(items) - size, size)` and so DROPS the
     final short chunk (and, for short inputs, every element). The SPEC
     requires every element to appear in exactly one chunk.
  4. `contact_email` returns `profile.email` when `profile` is None,
     raising AttributeError. The SPEC requires '' when there is nothing
     to show, and a profile-less user is an ordinary state.

PASS if at least 3 of these 4 are reported as bugs.
FAIL if fewer than 3 are reported.
