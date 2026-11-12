---
type: llm
---

Four bugs are seeded in the functions under review. A correct response
identifies them (wording may differ; the substance must match):

  1. `lineTotalCents` computes tax on the DISCOUNTED base. The SPEC says
     tax is computed on the UNDISCOUNTED line total, so taxed discounted
     lines are under-charged.
  2. `pageCount` uses `Math.floor` where the SPEC requires rounding up:
     10 items at 3 per page returns 3, but must be 4, so the last page of
     items is unreachable.
  3. `formatMoney` puts the minus sign INSIDE the currency symbol
     (`$-5.00`); the SPEC requires `-$5.00`.
  4. `splitEvenly` drops the remainder: the returned parts do not sum back
     to the total, so cents go missing.

PASS if at least 3 of these 4 are reported as bugs.
FAIL if fewer than 3 are reported.
