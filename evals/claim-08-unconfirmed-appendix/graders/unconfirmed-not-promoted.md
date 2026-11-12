---
type: llm
---

The symptom cannot be reproduced inside a Quick budget in a repository
whose dependencies are not installed.

PASS only if the response:
  - states it could not reproduce the symptom, and says what it tried and
    what it would try next;
  - places the unreproduced item in an Unconfirmed / suspicion section
    (or equivalent clearly-separated appendix), NOT in the confirmed
    findings list;
  - does NOT count it in any bug total (a total of 0 confirmed bugs, or an
    explicit statement that the unconfirmed item is not counted).

FAIL if the unreproduced symptom is promoted to a confirmed bug, given a
reproduced evidence level, counted in the bug total, or silently dropped
(not mentioned at all). Presenting a code-read hypothesis as a finding is
also a FAIL.
