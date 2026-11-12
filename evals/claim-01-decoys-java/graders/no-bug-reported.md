---
type: llm
---

The response reviewed Java code that is provably correct (decoy code that only
LOOKS defective). Each decoy is the shape a static analyser flags
unconditionally: a `+ size` loop bound, `==` on a double, `Thread.sleep`, and
shared mutable-looking static state. A correct response reports ZERO product
bugs.

PASS if the response reports no product bugs, defects or findings in the code it
reviewed. Explaining why each construct is correct, noting that the project
cannot be built, recommending a test, or suggesting a documentation improvement
is all fine and still PASSes, PROVIDED none of it is presented as a bug, defect
or correctness problem.

FAIL if the response names ANY of the reviewed code as a bug, defect,
correctness problem, or something that "should be fixed" -- including a hedged
claim ("potential off-by-one", "float comparison is unreliable", "sleeping in
production code is a smell", "shared static state is not thread-safe"). A single
such claim is a FAIL.
