---
type: llm
---

The test renders `new Date(0).toLocaleDateString("en-US")` and asserts
"1/1/1970". The result depends on the host TIMEZONE (and ICU data), so it
passes in US timezones and fails on a UTC-negative or differently
configured CI runner.

PASS if the response attributes the failure to the ENVIRONMENT --
timezone, locale, ICU/Intl data, or CI host configuration -- rather than
to the product code, and says the fix is to pin the timezone/locale.

FAIL if it is classified as order-dependent, as a timing/concurrency
problem, or as a product bug in any source file.
