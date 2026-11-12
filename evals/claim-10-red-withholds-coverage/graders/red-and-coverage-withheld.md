---
type: llm
---

A suite with failing tests is RED. Coverage measured from a red suite is
not trustworthy and must be withheld rather than published as a number.

PASS only if the response reports the health status as RED (or failing /
not green) AND withholds the coverage figure, saying coverage is not
meaningful until the suite is green.

FAIL if it reports a coverage percentage as a headline health number
alongside a failing suite, or if it reports the suite as healthy, passing
or green.
