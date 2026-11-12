---
type: llm
---

The test asserts `seen.length === 0` on a module-level array that the
PRECEDING test in the same file pushes to. It passes in isolation and
fails in suite order.

PASS if the response classifies this as order-dependent / shared-state /
test-pollution / leaked state between tests, and names the preceding test
or the shared `seen` array as the cause.

FAIL if it is classified as a timing/async/concurrency problem, as an
environment problem, or as a product bug in `pageCount` or any source
file.
