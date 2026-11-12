---
name: plan
description: >
  Rebuild the ranked roadmap from current evidence — what to do next, in what order, to
  reach the acceptance contract's targets. Ranks by leverage, not by severity or by
  coverage percentage, and gives every item a done-when line a command can check. Use it
  after diagnosing, after a contract change, or whenever the roadmap has gone stale. It
  does not do the work and it does not invent targets the contract never agreed to.
---

# /testsquad:plan

A roadmap is a claim about order. Everything here exists to make that order defensible
and the finish line checkable.

## What you read first

- `.testing/acceptance-contract.md` — the targets. You plan toward these, not toward a
  number you think is good. If no contract exists, stop and say so: without agreed
  targets a roadmap is a list of opinions. Offer `/testsquad:start` step 11.
- `.testing/health-baseline.json` — the verdict, and its commit.
- `.testing/coverage-state.md` and `.testing/risk-map.json` — where the gaps are.
- `.testing/known-issues.md`, `.testing/decisions.md`, `.testing/preferences.json` —
  what has already been decided, declined, or deferred. Re-proposing something the user
  declined last month is how a roadmap stops being read.

## Item #0 — a RED suite

If the health verdict is RED, **fixing the suite is item #0 and nothing else is ranked
above it.** No amount of new tests improves a repository whose suite does not pass: the
new tests inherit the same broken foundation and the signal stays zero. Say which of the
two problems it is, because they need different people:

| Verdict detail | Item #0 is |
|---|---|
| cannot execute | Repair the harness — missing dep, bad config, absent env var |
| executes and fails | Fix the failing tests, or the code they caught |

An AMBER verdict is not item #0, but every caveat it names is carried into the items it
affects, so nobody plans against a number that was measured under a cloud.

## Ranking: leverage, which is risk reduced ÷ effort

Not severity. Not coverage percentage. Not the order findings happened to be discovered
in. For each candidate item, state both halves of the ratio and let the reader check the
arithmetic:

```
Enable strict null checks on src/billing/
  risk reduced: 6 of the last 14 bug-fix commits in billing are null-handling
  effort:       ~2h, 31 existing errors to triage, no new dependency
```

**Static analysis and type checking usually rank first**, and you should expect that
rather than be surprised by it. They are free at run time, they never flake, they apply
to code nobody is currently editing, and they cost one configuration change rather than
one test per behaviour. A repository with a type checker switched half-off is the
cheapest board position in testing, and it stays cheap: Facebook's Infer is the standard
caution here — the analysis was never the hard part; delivery was. Findings on the diff
under review were fixed **~70%** of the time, the same findings as a batch list **~0%**.

After that, rank by the contract's tiers. Critical-tier code with no tests outranks
standard-tier code with weak tests, regardless of how much the percentage would move.
Never rank by how many tests an item would add.

## Every item carries four things

| Field | Rule |
|---|---|
| **What** | One sentence, a symptom or a capability, no jargon |
| **Who** | The agent that does it: `ts-unit-test-engineer`, `ts-automation-engineer`, `ts-debugger`, or the user where it is a decision |
| **Expected change** | The measurable difference, stated before the work, including "none measurable — this prevents a class of defect" where that is the honest answer |
| **Done when** | **A command and its expected observable.** Not a feeling |

A done-when line is the whole discipline:

```
Done when: npx jest src/billing --coverage shows branch coverage ≥90% for
           proration.ts AND every new test fails when its assertion is inverted.
# currently: 44%
```

*"Billing is well tested"* is not a done-when line. It is a wish, and it will be
declared met by whoever is tired.

## The first-install backlog rule

On a first run almost everything is new, and the resulting wall of legacy findings is
the single fastest route to a tool being ignored permanently. So:

- Pre-existing findings go to the **backlog**, counted, labelled *pre-existing*, and
  **non-blocking**. They never fail a PR.
- The backlog is worked down **deliberately, in risk order, through this roadmap** — not
  by ambushing whoever happens to touch the file next. Nobody should be made responsible
  for a defect they did not introduce; doing that once is how the gate gets disabled.
- Say the shape of it out loud in the report: *"7 items planned from the highest-risk
  code; 143 pre-existing findings are in the backlog, not shown."* A reader who thinks a
  short list is the whole list draws the wrong conclusion from it.

## Sizing and honesty

Cap the active roadmap at **10 items**, per the report style contract. An eleventh goes
to the backlog with the "N of M shown, ranked by …" line attached. Google SRE's budget
discipline is the model: ≤2 incidents requiring intervention per 12-hour shift, and the
instruction "you'll be tempted to add a lot of stuff; resist."

Where an item depends on a decision only the user can make — a new dependency, a
threshold, access to a service — mark it `NEEDS DECISION` with the exact question, and
rank it where it belongs rather than hiding it at the bottom.

## Output

Rewrite `.testing/roadmap.md` in full (atomic write: temp file, then rename), stamped
with the date, the commit, and the health verdict every figure was measured under. Note
in `.testing/decisions.md` anything that changed order for a reason the next reader will
not be able to reconstruct.

Then report in the house style: the verdict line, item #0, and the first thing to do
today — plus the reminder that each item is an ordinary `/testsquad:test` or
`/testsquad:debug` invocation, so nothing here requires running the whole plan at once.
