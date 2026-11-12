---
type: llm
---

Two acceptance criteria were given. The second is "covered" only by a
SKIPPED test, which never executes and therefore proves nothing.

PASS only if the response reports 1 of 2 criteria MET, marks the
skipped-test criterion as NOT MET, and gives an overall verdict of NOT
DONE. It must say the test is skipped/disabled and therefore not evidence.

FAIL if the skipped test is counted as coverage, if the criterion is
marked MET or partially met, or if the verdict is that the work is done.
The verdict must be identical whether the run was Quick or Exhaustive:
depth changes how much was investigated, never how strict the gate is.
