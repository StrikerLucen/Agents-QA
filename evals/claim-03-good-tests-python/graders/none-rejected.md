---
type: llm
---

These tests are genuinely good: they assert concrete expected values,
cover the half-open range boundary, prove the mutable default is not
mutated, prove the original exception is re-raised, and prove the seeded
sampler is reproducible. A correct response ACCEPTS all of them.

PASS if no test in the file is rejected. Suggesting an additional missing
case is fine and still PASSes.

FAIL if any test in `tests/test_good.py` is rejected, called weak,
tautological, decorative or unacceptable.
