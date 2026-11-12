---
type: llm
---

THIS IS THE CRITICAL CELL FOR THE PYTHON FIXTURE. The test's name says
"occasionally", and a `time.sleep(0.01)` is there to sell the story, but the
test asserts `cents(1.15) == 115` and `cents` is `int(amount * 100)`. In binary
floating point 1.15 * 100 is slightly below 115, so `int()` truncates to 114.
The test fails on EVERY run, deterministically, in isolation. By the matrix, a
test that fails deterministically in isolation is NOT flaky: it is a real
product bug.

PASS only if the response concludes this is a REAL PRODUCT BUG in `cents`
(truncation where the spec requires half-up rounding), and explicitly rejects
the flaky explanation on the grounds that the failure is deterministic in
isolation.

FAIL if the response calls the test flaky, timing-dependent, load- or
contention-sensitive, order-dependent, or environment-related; if it recommends
quarantining, retrying or deleting the test; or if it hedges between flake and
bug without committing to the bug. Misclassifying here silently loses a real
money bug, which is the most expensive error this product can make.
