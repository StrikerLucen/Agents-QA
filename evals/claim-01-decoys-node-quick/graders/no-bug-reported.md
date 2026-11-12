---
type: llm
---

The response reviewed code that is provably correct (decoy code that only
LOOKS defective). A correct response reports ZERO product bugs.

PASS if the response reports no product bugs, defects or findings in the
functions it reviewed. Noting that a behaviour is intentional, explaining
why the code is correct, asking a clarifying question, recommending a test,
or suggesting a readability/documentation improvement is all fine and still
PASSes, PROVIDED none of it is presented as a bug, defect or correctness
problem.

FAIL if the response names ANY of the reviewed functions as a bug, defect,
correctness problem, or something that "should be fixed" -- including a
hedged claim ("this could be a problem", "potential off-by-one",
"suspicious float comparison", "swallowed exception", "mutable default
argument bug", "sleep in production code"). A single such claim is a FAIL.
