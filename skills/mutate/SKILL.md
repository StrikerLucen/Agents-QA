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

## 1. Plan the mutants with the script — do not re-derive the policy

Google ran 16,935,148 mutants across 776,740 changelists in 10 languages and published
what made it survivable. The headline number: a naive median of **820 mutants per
changelist** became **7** after suppression and one-per-line, of which a median of **2**
were actually surfaced. Developer-reported usefulness went **15% → 89%** over six years.

Those rules are implemented, seeded and tested in
`${CLAUDE_PLUGIN_ROOT}/scripts/arid_nodes.py`. **Run it.** Do not re-derive which lines
are worth mutating by reading the source yourself: a suppression policy applied from
prose is applied differently every run, and the point of this step is that two runs over
the same input select the same mutants.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/arid_nodes.py" <path> \
  --seed 0 --max-per-file 7 --format json --out .testing/mutation-plan.json
```

Add `--diff <file>` to scope the plan to a unified diff's changed lines, and
`--feedback .testing/feedback.json` to honour past "not useful" reports. `--out` writes
atomically; `--format text` gives one line per mutant and a count per suppression rule,
for reading rather than parsing.

What it decides, so you do not: **one mutant per line** (in **over 90%** of lines with
several applicable mutants all share the same fate, so generating more is near-pure
waste); **arid-node suppression** — nodes whose mutation teaches nobody anything, such
as logging, trivial accessors and presentation, while money arithmetic, boundary
comparisons, authorization connectors and loop conditions stay mutable however they are
named; the **per-file cap** (`--max-per-file`, default 7); and the weighted operator
draw. Suppression is the difference between 820 and 7, and it **matters more than the
choice of tool**.

Exit 0 means a plan was produced — possibly a partial one, see below. **Any non-zero
exit means no plan exists**: report that the plan could not be produced, quote stderr,
and stop. Never fall back to judging arid-ness by eye, and never mutate a line that is
not in the plan.

## 2. Read the plan — and read what it could not analyse

`.testing/mutation-plan.json` is `testsquad.aridnodes/1`:

- `files[].selected[]` — **mutate exactly these**: `line`, `operator`, `snippet`.
- `files[].suppressed[]` — `rule` and `why`: the sentence to quote when someone asks why
  a line was not mutated.
- `settings` and `summary` — seed, cap, whether it was diff-scoped, and counts per rule
  and per operator.

**Before reporting anything, collect the "could not analyse" signals.** A file nobody
opened and a file with nothing worth mutating produce a similarly small result; the
script states the difference, and the difference is lost unless you carry it through.

- Every `warning:` line on **stderr** names something it could not fully analyse. With
  `--out` the report goes to the file and stdout is empty, while the warnings still go to
  stderr — read both streams.
- In the JSON, **every top-level list besides `files` is a not-analysed record set.**
  Today: `skipped_files` (over the byte ceiling — never opened), `truncated_lines` (a
  line past the char ceiling, so "no mutant here" covers its head only), `unparsed_files`
  (read and planned, but by the weaker regex scanner, so function spans and parameter
  names were not used). Also `settings.feedback.ignored`, for feedback entries it could
  not apply. Treat the class, not the list: if a later version adds another such list, or
  another `summary` counter named for what was *not* analysed, report it the same way
  without waiting for this skill to name it.
- Any such list that is non-empty, or any such counter that is non-zero, goes in the
  report with its `file` and its `reason` — including when plenty of mutants were
  selected. It is not a finding and not a failure; it is the scope of the run.

So a plan with no selected mutants reports *"N files planned, M not analysed — listed
below"*. Never *"nothing to mutate"*.

**Budget: 10 minutes or 200 mutants, whichever comes first.** When it expires, report
what you have, say what was not covered, and pass. An unbounded mutation run is how
people learn to never run one again.

## Operators: five, weighted

The script draws one of these per line. **Measured:** these five are the ones with
published per-operator share, survivability and productivity (Petrović et al., TSE 2021 —
RESEARCH §20), so they are the five we can weight honestly. **A choice, not a finding:**
stopping at five. No paper says five is optimal; the mutation-reduction literature bounds
*removing* operators and explicitly exempts *adding* them (RESEARCH §20). What is measured
is that suppression pays more at this effort budget — 820 mutants down to 7 (RESEARCH §15)
— so a bigger catalogue is a cost question we have not spent on, not a settled one.

| Operator | Note |
|---|---|
| **SBR** statement block removal | Roughly **68%** of generated mutants |
| **ROR** relational operator replacement | **84.1% productive** — weight toward it |
| **AOR** arithmetic operator replacement | |
| **LCR** logical connector replacement | |
| **UOI** unary operator insertion | **74.5% productive** — weight away from it |

## Scoping to the diff

`--diff` scopes the plan. The mutation tool itself still has to be told, and the
per-tool flags differ more than the docs suggest:

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

Every surfaced mutant carries a one-click **not useful** control appending an entry to
`.testing/feedback.json` — the verdict, and the node type, operator or function pattern
it applies to, in the shape `--feedback` reads.

This is not a comment box. It is an input to the next run: pass `--feedback` and those
nodes are suppressed, with the output recording which suppressions came from feedback
rather than from a built-in rule. A node type reported useless twice becomes a
suppression proposal, which goes through `/testsquad:learn` and needs the user's approval
before it lands in `.testing/conventions.md`. Developer feedback growing the suppression rules is what
took Google from 15% to 89% usefulness (RESEARCH §15), and it is the only mechanism here
that improves with use.

## Output

Per mutant: the file, the line, the operator, the mutated code, **survived or killed**,
and — for survivors — the test that should have caught it and the assertion it is
missing. A survivor is a specific, fixable gap; a score is not. Alongside it, the scope
of the run: the seed, whether it was diff-scoped, and everything from §2 the script said
it could not analyse.

Bound by `${CLAUDE_PLUGIN_ROOT}/templates/report/RULES.md`. Nothing executes before the
environment safety check passes, no mutation tool is installed without permission
recorded in `.testing/preferences.json`, and work lands on a branch that is never
pushed.
