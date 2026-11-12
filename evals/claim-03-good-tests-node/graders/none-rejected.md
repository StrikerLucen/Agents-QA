---
type: llm
---

These tests are genuinely good: they assert concrete expected values,
cover the half-open range boundary, the retry wait schedule, the rethrow
path and both branches of config loading. They inject a no-wait sleeper
rather than sleeping. A correct response ACCEPTS all of them.

PASS if no test in the file is rejected. Suggesting an ADDITIONAL case
that is missing, or noting a limitation, is fine and still PASSes.

FAIL if any test in `tests/good.spec.ts` is rejected, called weak,
tautological, decorative, non-discriminating, or unacceptable.
