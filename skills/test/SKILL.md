---
name: test
description: >
  The work loop. Turns a testing request into frozen acceptance criteria, dispatches the
  specialists that fit the change, and loops through an independent QA gate until every
  criterion is met or it stops with a reason. Use it for any "write tests for X",
  "cover this module", or "test what this PR changed". Reports partial results as
  partial; it never rounds up to done.
---

# /testsquad:test \<task\> [--diff [base]]

You are the orchestrator. You run in the main session, so you can spawn agents and ask
the user things they cannot.

## 1. Write the contract, before any agent runs

Produce `.testing/tasks/<id>.md`:

- The request, restated in your own words
- **Numbered acceptance criteria**
- For each, **how it will be proved**: the exact command and the expected observable
- What is explicitly **out of scope**
- Known blockers

Then check it for ambiguity. If two readings would produce materially different work,
**ask the user now** — not at the end, when the work is done and wrong.

**Mark the task in flight.** Write the task id to `.testing/tasks/current` — a single
line, nothing else — and delete it when the task ends, however it ends. The opt-in guard
hooks in `hooks/examples/` test for exactly this file to know a testing task is running;
without it they are permanent no-ops and the product-code guard silently protects nothing.

**Freeze the file.** The criteria set before the work began are what QA grades against.
Goalposts that move to match whatever got built are the most comfortable way to fail.

A criterion must be checkable by a command. *"`tax.ts` branch coverage ≥90% and every new
test red under fail-proof"* is a criterion; *"tax is well tested"* is a wish.

## 2. Choose 2–4 specialists. Not all of them

Cost bottoms out around 4–6 subagents and rises after; a four-agent system can burn ten
times a single agent, and past roughly six hand-offs you pay about four times the cost
for half the success rate. So select from the change, not from the roster:

| The change touches | Dispatch |
|---|---|
| Pure logic, services, helpers | `ts-unit-test-engineer` |
| HTTP handlers, routes, UI, cross-service flows | `ts-automation-engineer` |
| A reported failure, a flaky test, a red CI job | `ts-debugger` |
| Anything, at the end | `ts-qa-gatekeeper` |

Give each specialist **one dimension and a tool-call budget**. An agent handed four
dimensions and an open-ended budget exhausts its context and returns nothing usable.

**Keep writes with you where approval might be needed.** Subagents cannot ask the user
anything mid-task, so investigation belongs in them and approval-gated actions belong in
you.

`--diff [base]`: map changed files onto `.testing/risk-map.json` and work highest-risk
first, including transitive impact (changed file → its importers → the tests covering
them). On an active repo this is the highest-value mode, because findings delivered
against the change under review get acted on far more often than the same findings in a
batch list — ~70% versus ~0% for Facebook's Infer, the one deployment that measured it
(RESEARCH §6).

## 3. The loop

```
specialist → qa-gatekeeper → (met?) → done
                           → (not met?) → back, with the gap verbatim
```

The gatekeeper is independent on purpose: fresh context, reads the frozen contract rather
than the implementer's summary, and re-runs every command itself. A reviewer who reads
the summary approves the summary.

**Bounded, and escalating:**

- **Rounds 1–3**: resume the *original* implementer with the findings verbatim. Its
  context is intact, which is the cheapest place to fix something.
- **Rounds 4–5**: dispatch a *fresh* implementer on a more capable model, told plainly
  that previous attempts failed and it now owns the problem. A loop that survives three
  resumes usually means the implementer cannot see its own mistake.

**Stop early** on any of: the same failure signature twice, a time or token budget, or a
blocker. A loop that burns budget rediscovering the same wall is worse than one that
stops and says so.

**Minor findings are ledgered and deferred** to a final review rather than looped — but
they must actually appear there. A roll-up nobody reads is a silent discard.

## 4. Rulings, not stalls

Decide ambiguities yourself and record them:

```
Ruling: mocked the payment gateway rather than using the sandbox
  — why: sandbox needs a credential the run does not have
  — cost if wrong: gateway contract drift would not be caught here
```

Stop and ask only for: an irreversible or destructive operation, a security-sensitive
action, a side effect outside the worktree (push, merge, publish, deploy), or a plan that
cannot work.

## 5. Finish honestly

Report the per-criterion table: `MET` with the command and output, `NOT MET` with the
gap, `BLOCKED` / `NEEDS_PERMISSION` with exactly what is needed, or `CANNOT VERIFY` for
criteria living in code this change did not touch — that last one is yours to resolve,
because you hold the cross-task context the gatekeeper does not.

**Two of three criteria met is reported as two of three.** Never round a partial result
up to success. That single behaviour is what the whole design exists to protect, and it
is the one thing the evals check five times over.

Delete `.testing/tasks/current` before you finish — on success, on BLOCKED, and on a
budget stop alike. A stale marker leaves the guard hooks armed over somebody else's work,
and a guard that fires when it should not is one that gets uninstalled.

Work lands on a `testsquad/<task>` branch. Tests are code and deserve review; you do not
push.
