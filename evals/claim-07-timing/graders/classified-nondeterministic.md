---
type: llm
---

The test draws `Math.floor(Math.random() * 100)` and asserts it is < 95,
so it fails about 5% of the time regardless of order or environment. This
is the non-deterministic / timing-and-entropy cell of the matrix: the TEST
is at fault, not the product.

PASS if the response classifies this as a genuinely flaky test caused by
unseeded randomness / non-determinism in the test itself, and does NOT
attribute it to the product code.

FAIL if it is classified as order-dependent, as an environment/CI problem,
or as a real product bug.
