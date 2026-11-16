<!--
TEMPLATE. Copy the numbers out of evals/results/scorecard.json; do not type them
from memory. Every <...> must be replaced. An unreplaced placeholder means the
scorecard is not published.

Rules for this file:
  1. Date every run. An undated rate is not evidence.
  2. Pin the model. Two scorecards on different models are not comparable.
  3. Publish failures. A scorecard that only ever shows green is marketing.
  4. Never publish while the pre-gate is red or any claim reads NO DATA.
  5. Keep the history table. The trend is the real signal; one run is an anecdote.
-->

# testsquad scorecard

What this plugin claims, and what it measured. Every number below comes from
`evals/`, which is the project's own acceptance criteria. Method: each case runs
**5×**; a run counts only if every grader in it passed; the rate is passing runs
over total runs pooled across the cases under that claim.

| | |
|---|---|
| **Run date** | `<YYYY-MM-DD>` |
| **Plugin version** | `<0.1.0>` |
| **Model under test** | `<pinned model id>` |
| **Judge model** | `<judge model id>` |
| **Claude Code** | `<claudeVersion>` |
| **Runs per case** | 5 |
| **Total agent runs** | `<n>` |
| **Cost** | `<$n.nn>` |
| **Command** | `claude plugin eval . --scaffold --allow-tools Bash Write Edit --ablation none --runs 5 --model <model> --json evals/results/aggregate-result.json` |

**Pre-gate — deterministic core (claim 12):** `<PASS / FAIL>` — `<detail>`
**Fixture manifest reconciled:** `<yes / no>` (`harness/check_manifest.py`)

## Results

| # | Claim | Required | Measured | Runs | Verdict |
|---|---|---|---|---|---|
| 1 | Zero false alarms on decoy code | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 2 | Weak tests rejected | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 3 | Good tests accepted | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 4 | Seeded bugs found, standard format | ≥80% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 5 | Partial completion never rounded up | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 6 | Depth never weakens the gate | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 7 | Flake/bug discrimination, all four cells | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 8 | Unreproducible findings stay unconfirmed | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 9 | Safety | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 10 | Health gate | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |
| 11 | Idempotence | 100% | `<n.n%>` | `<n>` | `<PASS/FAIL>` |

**Overall: `<PASS / FAIL>` — `<n>` of 11 claims met.**

Depth invariance (claim 6): `<the scorer's note>`

### The two headline numbers

> **`<n.n%>`** of seeded weak tests rejected (claimed: 100% at 10 runs)
> **`<n.n%>`** of seeded bugs found with `file:line` (claimed: ≥80%)
>
> Measured `<YYYY-MM-DD>` on `<model>`, `<n>` runs. Method and cases in `evals/`.

If either number is below its claim, every page that publishes that number is
corrected in the same commit as this file. The numbers move together or not at
all.

As of 2026-11-16 that is an empty list, and deliberately so: the `README.md`
added that day publishes **no** behavioural rate. It says instead that every
behavioural claim here is design intent, not evidence, until this file has
numbers in it. So there is no README rate to correct yet — not an oversight,
and not something to fix by adding one. The rule above is written for the commit
that fills this template in: if that commit also puts a rate on another page, the
rate and this file move together, and if it cannot, the rate does not go on that
page at all.

## Flake matrix (claim 7), per cell

| Cell | Expected conclusion | Rate |
|---|---|---|
| Fails in suite, passes in isolation | order-dependent / shared state | `<n.n%>` |
| Fails non-deterministically, in isolation too | timing / non-determinism | `<n.n%>` |
| **Fails deterministically in isolation** | **real bug, not a flake** | `<n.n%>` |
| Passes locally, fails only on CI | environment | `<n.n%>` |

The third row is the one to read first: misclassifying it loses a real bug
silently, and nothing downstream ever reveals the mistake.

## Failures this run

<!-- Paste the scorer's "WHAT FAILED" block verbatim. If empty, write "None."
     Do not summarise it: the failing grader names are what makes the number
     actionable. -->

```
<paste>
```

**Action taken:** `<fix shipped / claim corrected / accepted with reason>`

## History

Oldest last. Keep every row, including bad ones.

| Date | Version | Model | C1 | C2 | C4 | C5 | C9 | Overall |
|---|---|---|---|---|---|---|---|---|
| `<YYYY-MM-DD>` | `<0.1.0>` | `<model>` | `<n%>` | `<n%>` | `<n%>` | `<n%>` | `<n%>` | `<PASS/FAIL>` |

## Caveats

- 5 runs catch a 50%-rate defect almost always, a 5% defect about a quarter of
  the time, and a 0.1% defect essentially never. **A 100% row means no failure
  in 5 runs, not an absence of failure.** Stated here because the gatekeeper is
  required to state the same limit about its own stability checks.
- Rates are specific to the model and the fixtures. They do not transfer to
  another model, and they are a lower bound on a real codebase, which is larger
  and noisier than a seeded fixture.
- Claims 6 and 11 are checked indirectly (identical graders across paired cases;
  on-disk file state after a re-run) rather than by diffing two verdicts
  directly. The pairing is recorded in `evals/harness/claims.json`, which maps
  each claim to its cases; the graders themselves are in each case's `graders/`.
  *(This pointed at `evals/README.md` until 2026-11-16. No such file exists or
  ever has — `ls evals/` lists the 27 case directories, `harness/` and this
  file — so the one caveat whose method needed looking up sent you nowhere.)*
