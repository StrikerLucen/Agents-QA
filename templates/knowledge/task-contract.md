# Task {{TASK_ID}} — {{SHORT_TITLE}}

Opened {{DATE}} · **Frozen once the first agent starts.**

The criteria below are what QA grades against. They are set *before* any work
begins and are not edited afterwards. Goalposts that move to match whatever got
built are the most comfortable way to fail. If a criterion turns out to be wrong,
close this task and open a new one — do not rewrite history.

## The request, restated

{{ONE PARAGRAPH IN YOUR OWN WORDS. If two readings would produce materially
different work, ask the user now — not at the end, when the work is done and wrong.}}

## Acceptance criteria

Each is checkable by a command. *"`tax.ts` branch ≥90% and every new test red
under fail-proof"* is a criterion; *"tax is well tested"* is a wish.

| # | Criterion | How it will be proved | Expected observable |
|---|---|---|---|
| 1 | `src/billing/tax.ts` branch coverage ≥90% | `npx jest src/billing/tax.ts --coverage` | `Branches: ≥90%` |
| 2 | Each new test fails on the unfixed code | `git stash pop fix && npx jest <file>` | every new test red |
| 3 | {{CRITERION}} | `{{COMMAND}}` | {{OBSERVABLE}} |

## Out of scope

Named explicitly, so "you didn't do X" has an answer.

- {{THING}} — {{why not now}}

## Known blockers

- {{BLOCKER}} — needs {{WHAT, from WHOM}}

---

## Verdict — filled in by QA, not by the implementer

| # | Verdict | Evidence (command + real output) |
|---|---|---|
| 1 | MET | `npx jest src/billing/tax.ts --coverage` → `Branches: 58/61 (95.1%)` |
| 2 | NOT MET | 2 of 5 new tests stayed green on the unfixed code |
| 3 | {{VERDICT}} | {{OUTPUT}} |

- **MET** — the command ran and the observable matched.
- **NOT MET** — it ran and did not match. Excellent work answering a different
  question is NOT MET.
- **BLOCKED** — something outside the task stopped it; name it.
- **NEEDS_PERMISSION** — name the exact command that was not allowed to run.
- **CANNOT VERIFY** — the check could not run at all. Never rendered as a pass.

**Overall:** {{n}} of {{m}} met. Partial is reported as partial.
