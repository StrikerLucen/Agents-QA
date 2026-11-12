---
name: mutate
description: >
  Run mutation testing on a path, on demand, to turn "this line was executed" into "this
  line is actually asserted". Applies Google's affordability rules so a changelist yields
  single-digit mutants rather than hundreds. Use it on critical-tier code before trusting
  its coverage, or when a test looks tautological. It is never automatic, never gates on
  a mutation score, and never runs project-wide.
---

# /testsquad:mutate [path]

Coverage tells you a line ran. Mutation tells you whether anything would have noticed if
that line were wrong. It is the strongest signal available for test quality — Meta's
mutation-guided ACH reached a **12–15%** mutant kill rate against **2.0–2.4%** for the
coverage-filtered generator it replaced — and it is also the easiest thing in this
toolkit to make unaffordable.

**On demand only.** Never triggered by `/testsquad:diagnose`, never on every push,
never on a schedule. Mutation runs when someone asks for it on a path they care about.

## The affordability rules — this is the whole skill

Google ran 16,935,148 mutants across 776,740 changelists in 10 languages and published
what made it survivable. The headline number: a naive median of **820 mutants per
changelist** became **7** after suppression and one-per-line, of which a median of **2**
were actually surfaced. Developer-reported usefulness went **15% → 89%** over six years.

**1. One mutant per line.** Chosen at random among applicable operators. In **over 90%**
of lines with multiple applicable mutants, all of them share the same fate — so
generating more is almost pure waste.

**2. Arid-node suppression.** Skip AST nodes whose mutation is almost never informative:

| Suppressed | Because |
|---|---|
| Logging and metrics calls | Killing a log line teaches nothing |
| Debug and assert statements | Not product behaviour |
| Trivial getters | No logic to break |
| Pure delegation | The behaviour lives one level down |
| `toString` / `__repr__` / `Display` | Presentation |
| String constants in error messages | Changing a message is not a defect |

This step is the difference between 820 and 7, and it **matters more than the choice of
tool**. Google needed 100+ hand-written rules to reach 89% usefulness, grown from
developer feedback on 66,798 mutants — which is why the affordance below exists.

**3. Hard cap: 7 mutants per file.**

**4. Budget: 10 minutes or 200 mutants, whichever comes first.** When it expires, report
what you have, say what was not covered, and pass. An unbounded mutation run is how
people learn to never run one again.

## Operators: five, weighted

Exotic operator sets buy **≤13%** over random selection, so there is no reason to carry
more than these:

| Operator | Note |
|---|---|
| **SBR** statement block removal | Roughly **68%** of generated mutants |
| **ROR** relational operator replacement | **84.1% productive** — weight toward it |
| **AOR** arithmetic operator replacement | |
| **LCR** logical connector replacement | |
| **UOI** unary operator insertion | **74.5% productive** — weight away from it |

## Scoping to the diff

Per-tool, verified flags — these differ more than the docs suggest:

| Language | Tool | Diff-scoped invocation |
|---|---|---|
| PHP | Infection | `--git-diff-lines --git-diff-base=origin/main` — line-level, the strictest |
| Rust | cargo-mutants | `--in-diff /tmp/pr.diff` |
| C# | Stryker.NET | `--since:origin/main --with-baseline` |
| JS/TS | StrykerJS | **`--incremental`, not `--since`** — `--since` is Stryker.NET only and the docs conflate the two. Reuses ~94% of prior results |
| Java | PIT | `historyInputLocation` — experimental, can report a false "still killed". Reliable git scoping needs commercial arcmutate |
| **Go** | — | **Effectively dead.** `go-mutesting` last commit 2021, `gremlins` semi-dormant since 2026-03-30. Say so rather than recommending something unmaintained |

## Never gate on a mutation score

A diff-scoped run produces a handful of mutants. On a 7-mutant diff **one survivor moves
the score by 14 points**, so a percentage threshold there measures diff size, not test
quality. Nobody can act on it and everybody will learn to route around it.

Gate **per test and binary** instead: for each new or changed test, does it kill the
mutant on the line it claims to cover — yes or no? That is the question the QA gate
already asks under a different name, it is stable at small N, and it names the test that
needs work instead of a number that needs explaining.

The ratchet argument is what makes this converge without a full run: if every merged
change clears its own changed lines, the project reaches a high covered-MSI **without a
project-wide sweep ever happening**. State the blind spots out loud, because they are
real: a change in file A can destroy coverage of file B unnoticed; a test-only change
runs no mutants at all, so deleting assertions passes the gate; and dependency, config
or fixture changes can flip mutant status silently. Keep an occasional full run off the
critical path rather than pretending the ratchet is an audit.

## The "this mutant is useless" affordance

Every surfaced mutant carries a one-click **not useful** control writing to
`.testing/feedback.json` with the node type, the file and the operator.

This is not a comment box. Those reports are the input to the arid-node rules in
`.testing/conventions.md`: a node type reported useless twice becomes a suppression
proposal, which goes through `/testsquad:learn` and needs the user's approval before it
is applied. This exact loop is what took Google from 15% to 89% usefulness, and it is
the only mechanism here that improves with use.

## Output

Per mutant: the file, the line, the operator, the mutated code, **survived or killed**,
and — for survivors — the test that should have caught it and the assertion it is
missing. A survivor is a specific, fixable gap; a score is not.

Bound by `${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`. Nothing executes before the
environment safety check passes, no mutation tool is installed without permission
recorded in `.testing/preferences.json`, and work lands on a branch that is never
pushed.
