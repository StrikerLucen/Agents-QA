# Known issues — accepted and deferred

Things already triaged and agreed not to act on *yet*. Agents read this at
session start and will not re-report anything listed here.

Entries are bullets, one line each, because the SessionStart hook surfaces the
first few verbatim. Keep the lead of each line readable on its own.

- KI-01 `orders.e2e.spec.ts` flakes ~4% on CI — accepted 2026-11-12, shared staging DB, environment fix scheduled Q1 — review 2027-01-15 — {{WHO}}
- {{ID}} {{symptom in one line}} — accepted {{DATE}}, {{why not now}} — review {{DATE}} — {{WHO}}

## Rules

Every entry carries a reason and a **review date**. An entry past its review date
is resurfaced on the next `/testsquad:diagnose` run and must be re-decided:
re-defer with a new date, fix it, or close it as won't-fix with the reason.

That review date is the whole point of this file. Without it, "known issue" is
simply where bugs go to die quietly, and the list grows into something nobody has
read since the quarter it started.

An issue is never accepted by an agent. A human accepts it; the agent records it.
No entry gets "indefinitely" instead of a date. Link the bug report or tracker id
so the detail lives somewhere other than this one line. Closing an entry means
saying which — **fixed**, with the commit, or **won't-fix**, with the reason.
Deleting the row is not closing it.
