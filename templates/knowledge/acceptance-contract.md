# Acceptance contract — {{PROJECT_NAME}}

Agreed {{DATE}} by {{APPROVER}}. The baseline every `/testsquad:verify` run checks.

**Every line here must be checkable by a command.**
Good: `billing/tax.ts branch ≥90%, every new test red under fail-proof`.
Bad: `tax is well tested` — nobody can run that, so nobody can fail it.

## 1. Impact — the scarce list

Only features where failure **costs money, loses data, or breaks the law**.
If everything is critical, nothing is. Aim for 3–5 rows; more than ~7 means
the list has stopped meaning anything.

| Area | Impact | Why (one line, in business terms) |
|---|---|---|
| `src/billing/tax.ts` | critical | Wrong VAT is invoiced to customers and filed with HMRC |
| {{AREA}} | {{critical/standard/low}} | {{CONSEQUENCE}} |

## 2. Quality gates per tier

| Gate | Critical | Standard | Low | Checked by |
|---|---|---|---|---|
| Branch coverage | ≥90% | ≥70% | none | `{{COVERAGE_CMD}}` |
| Covered-MSI on the diff | ≥80% | ≥60% | none | `{{MUTATION_DIFF_CMD}}` |
| New test fails on the unfixed code | required | required | required | fail-proof: revert the fix, test must go red |
| {{GATE}} | {{T}} | {{T}} | {{T}} | `{{CMD}}` |

## 3. Test types

| Type | Critical | Standard | Low |
|---|---|---|---|
| Unit | mandatory | mandatory | assess only |
| Integration / contract | mandatory | assess only | assess only |
| E2E | {{mandatory/assess}} | assess only | no |
| Property-based | assess only | assess only | no |

*Assess only* means the advisor reports its absence; it never blocks.

## 4. Suite budget

| Limit | Value | Checked by |
|---|---|---|
| Max suite duration | {{N}} min | `{{TEST_CMD}}` wall clock |
| Flake budget | ≤{{N}}% of runs, 0 in critical areas | `/testsquad:health` |
| Skip allowance | ≤{{N}} skipped, each with an open issue id | `{{SKIP_COUNT_CMD}}` |
| `.only` / `fdescribe` | 0 | `python3 scripts/flake_smells.py test/ --format text` |

## 5. Changing a threshold

Lowering any number here is recorded in `decisions.md` with the date, the old
and new value, the reason and who decided. A threshold that can be edited
silently is not a standard — it is a preference, and it erodes by Friday.
