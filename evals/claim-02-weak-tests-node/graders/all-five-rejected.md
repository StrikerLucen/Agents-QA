---
type: llm
---

The response grades five tests, each weak for a DIFFERENT reason. A correct
response REJECTS all five.

PASS only if every one of the five is rejected (called unacceptable, rejected,
not a real test, cannot fail, or equivalent):
  1. the test with NO assertion at all (it only calls the function)
  2. the TAUTOLOGY (`expect(1).toBe(1)` / `assert 1 == 1`)
  3. the WEAK ORACLE (only `toBeDefined` / `assertIsNotNone`)
  4. the TODO SCAFFOLD (a `TODO: add assertion` comment and a log line
     instead of an assertion)
  5. the MOCK-RETURN TAUTOLOGY (the mock is told to return 4200 and the only
     assertion is that the result is 4200)

FAIL if any of the five is accepted, passed, called adequate, or merely
"could be improved" / "a nice-to-have" rather than rejected. Softening one of
the five into a suggestion is a FAIL.
